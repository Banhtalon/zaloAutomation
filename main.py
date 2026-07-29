from contextlib import asynccontextmanager
import os
import re
from time import monotonic
from urllib.parse import urlparse
import uuid

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from api.googleform_routes import router as googleform_router
from api.health_routes import router as health_router
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


def _host_and_port(value: str) -> tuple[str, int | None]:
    parsed = urlparse(f"//{value}")
    return (parsed.hostname or "").lower(), parsed.port


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
        if request_host not in _LOOPBACK_HOSTS:
            return JSONResponse(
                status_code=403,
                content={"detail": "Non-local host is not allowed."},
            )
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > runtime.settings.max_http_body_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body is too large."},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
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
                        content={"detail": "Cross-site mutation is not allowed."},
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
            if (
                request.url.path not in {"/api/logs", "/api/jobs/latest"}
                or response.status_code >= 400
            ):
                log_message(
                    "HTTP request hoàn tất.",
                    event="http.completed",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=round((monotonic() - started) * 1000, 2),
                )
            return response

    os.makedirs("static", exist_ok=True)
    os.makedirs("static/screenshots", exist_ok=True)
    application.mount("/static", StaticFiles(directory="static"), name="static")
    application.include_router(view_router)
    application.include_router(zalo_router)
    application.include_router(googleform_router)
    application.include_router(lms_router)
    application.include_router(job_router)
    application.include_router(upload_router)
    application.include_router(health_router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
