from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from api.dependencies import get_container, get_current_user
from api.errors import public_error
from core.auth import AuthUser
from core.container import AppContainer


router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)


@router.post("/api/auth/login")
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    container: AppContainer = Depends(get_container),
):
    if not container.auth.enabled:
        return {
            "authenticated": True,
            "user": {"username": "local-admin", "role": "Admin"},
            "csrf_token": None,
            "mode": "local",
        }
    client_key = request.client.host if request.client else "unknown"
    result = container.auth.login(req.username, req.password, client_key=client_key)
    if result is None:
        if not container.auth.configured:
            raise public_error(503, "AUTH_NOT_CONFIGURED", "Web auth is not configured.")
        raise public_error(401, "AUTH_INVALID", "Invalid username or password.")
    session_id, session = result
    response.set_cookie(
        container.auth.cookie_name,
        session_id,
        max_age=container.auth.ttl_seconds,
        httponly=True,
        secure=container.auth.secure_cookie,
        samesite="lax",
        path="/",
    )
    return {
        "authenticated": True,
        "user": {"username": session.user.username, "role": session.user.role},
        "csrf_token": session.csrf_token,
        "mode": "web",
    }


@router.get("/api/auth/me")
async def me(
    request: Request,
    container: AppContainer = Depends(get_container),
    user: AuthUser = Depends(get_current_user),
):
    session = getattr(request.state, "auth_session", None)
    return {
        "authenticated": True,
        "user": {"username": user.username, "role": user.role},
        "csrf_token": session.csrf_token if session else None,
        "mode": "web" if container.auth.enabled else "local",
    }


@router.post("/api/auth/logout")
async def logout(
    request: Request,
    response: Response,
    container: AppContainer = Depends(get_container),
    _user: AuthUser = Depends(get_current_user),
):
    session_id = request.cookies.get(container.auth.cookie_name)
    container.auth.logout(session_id)
    response.delete_cookie(container.auth.cookie_name, path="/")
    return {"authenticated": False}
