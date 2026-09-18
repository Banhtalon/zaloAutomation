from contextlib import asynccontextmanager
import os
from pathlib import Path
import re
from time import monotonic
from urllib.parse import urlparse
import uuid

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from api.googleform_routes import router as googleform_router
from api.auth_routes import router as auth_router
from api.evidence_routes import router as evidence_router
from api.health_routes import router as health_router
from api.landing_probe_routes import router as landing_probe_router
from api.job_routes import router as job_router
from api.lms_routes import router as lms_router
from api.upload_routes import router as upload_router
from api.view_routes import router as view_router
from api.zalo_routes import router as zalo_router
from core.container import AppContainer
from core.settings import AppSettings
from db.database import database_context
from logger_utils import log_context, log_message, use_log_manager


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver", "testclient"}
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PUBLIC_API_PATHS = {
    "/api/health/live",
    "/api/health/ready",
    "/api/auth/login",
}
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_PROJECT_ROOT = Path(__file__).resolve().parent


def _host_and_port(value: str) -> tuple[str, int | None]:
    parsed = urlparse(f"//{value}")
    return (parsed.hostname or "").lower(), parsed.port


def _origin_matches_allowlist(origin: str, allowed_origins: tuple[str, ...]) -> bool:
    if not origin:
        return True
    return origin.rstrip("/") in {item.rstrip("/") for item in allowed_origins}


def create_app(
    settings: AppSettings | None = None,
    *,
    container: AppContainer | None = None,
) -> FastAPI:
    runtime = container or AppContainer(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    application = FastAPI(lifespan=lifespan)
    application.state.container = runtime

    @application.middleware("http")
    async def protect_local_access(request, call_next):
        request_host, request_port = _host_and_port(request.headers.get("host", ""))
        settings_snapshot = runtime.settings
        allowed_hosts = set(getattr(settings_snapshot, "auth_allowed_hosts", ()))
        if settings_snapshot.auth_mode == "local":
            allowed_hosts.update(_LOOPBACK_HOSTS)
        if request_host not in allowed_hosts:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": {
                        "code": "HOST_NOT_ALLOWED",
                        "message": "Host is not allowed.",
                    }
                },
            )
        if request.url.path.startswith("/static/screenshots"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > runtime.settings.max_http_body_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": {"code": "BODY_TOO_LARGE", "message": "Request body is too large."}},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": {"code": "INVALID_CONTENT_LENGTH", "message": "Invalid Content-Length header."}},
                )
        if request.method in _MUTATING_METHODS:
            origin = request.headers.get("origin")
            if settings_snapshot.auth_mode == "web":
                allowed_origins = tuple(settings_snapshot.auth_allowed_origins)
                origin_allowed = _origin_matches_allowlist(origin or "", allowed_origins)
                if origin and not allowed_origins:
                    parsed_origin = urlparse(origin)
                    origin_host = (parsed_origin.hostname or "").lower()
                    origin_allowed = (
                        origin_host == request_host
                        and (parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80))
                        == (request_port or (443 if parsed_origin.scheme == "https" else 80))
                    )
                if origin and not origin_allowed:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": {"code": "ORIGIN_NOT_ALLOWED", "message": "Origin is not allowed."}},
                    )
            elif origin:
                parsed_origin = urlparse(origin)
                origin_host = (parsed_origin.hostname or "").lower()
                origin_port = parsed_origin.port
                same_loopback_origin = (
                    origin_host in _LOOPBACK_HOSTS
                    and origin_host == request_host
                    and origin_port == request_port
                )
                if not same_loopback_origin:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": {"code": "CROSS_SITE_MUTATION", "message": "Cross-site mutation is not allowed."}},
                    )
        user, session = runtime.auth.authenticate_request(request)
        request.state.auth_user = user
        request.state.auth_session = session
        if settings_snapshot.auth_mode == "web":
            if request.url.path.startswith("/api/") and request.url.path not in _PUBLIC_API_PATHS:
                if user is None:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": {"code": "AUTH_REQUIRED", "message": "Authentication is required."}},
                    )
                if not runtime.auth.configured:
                    return JSONResponse(
                        status_code=503,
                        content={"detail": {"code": "AUTH_NOT_CONFIGURED", "message": "Web auth is not configured."}},
                    )
            if request.method in _MUTATING_METHODS and request.url.path != "/api/auth/login":
                if user is None:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": {"code": "AUTH_REQUIRED", "message": "Authentication is required."}},
                    )
                if user is None or not runtime.auth.csrf_matches(request, session):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": {"code": "CSRF_INVALID", "message": "Missing or invalid CSRF token."}},
                    )
        return await call_next(request)

    @application.middleware("http")
    async def observe_requests(request, call_next):
        manager = runtime.log_manager
        if manager is None:
            return JSONResponse(
                status_code=503,
                content={"detail": "Application is not ready."},
            )
        supplied_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_id
            if _REQUEST_ID_PATTERN.fullmatch(supplied_id)
            else uuid.uuid4().hex
        )
        started = monotonic()
        with database_context(runtime.settings.database_path), use_log_manager(
            manager
        ), log_context(request_id=request_id, component="http"):
            try:
                response = await call_next(request)
            except Exception as exc:
                log_message(
                    "HTTP request thất bại.",
                    level="ERROR",
                    event="http.failed",
                    method=request.method,
                    path=request.url.path,
                    duration_ms=round((monotonic() - started) * 1000, 2),
                    error=str(exc),
                )
                raise
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            if request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            polling_read = request.method == "GET" and (
                request.url.path in {"/api/logs", "/api/jobs/latest"}
                or re.fullmatch(r"/api/jobs/\d+", request.url.path) is not None
            )
            if not polling_read or response.status_code >= 400:
                log_message(
                    "HTTP request hoàn tất.",
                    event="http.completed",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=round((monotonic() - started) * 1000, 2),
                )
            return response

    static_root = _PROJECT_ROOT / "static"
    os.makedirs(static_root, exist_ok=True)
    os.makedirs(runtime.settings.evidence_root, exist_ok=True)
    application.mount("/static", StaticFiles(directory=str(static_root)), name="static")
    application.include_router(view_router)
    application.include_router(auth_router)
    application.include_router(evidence_router)
    application.include_router(zalo_router)
    application.include_router(googleform_router)
    application.include_router(lms_router)
    application.include_router(job_router)
    application.include_router(upload_router)
    application.include_router(health_router)
    application.include_router(landing_probe_router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
