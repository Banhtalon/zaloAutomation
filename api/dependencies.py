from fastapi import Depends, Request

from api.errors import public_error
from core.auth import AuthUser, role_allows
from core.container import AppContainer
from repositories.job_repository import JobQueueFullError


async def enqueue_job(container: AppContainer, job_id: int):
    """Admit a job into the bounded dispatcher or fail it explicitly."""
    try:
        task = container.dispatcher.schedule_job(job_id)
    except JobQueueFullError as exc:
        await container.job_repo.update_job(
            job_id,
            status="FAILED",
            progress=100,
            message="Hàng đợi worker đã đầy; job chưa được chạy.",
        )
        raise public_error(
            429,
            "JOB_QUEUE_FULL",
            "Hệ thống đang xử lý đủ số tác vụ; vui lòng thử lại sau.",
        ) from exc
    return task


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("Application container is not configured.")
    return container


def get_current_user(request: Request) -> AuthUser:
    user = getattr(request.state, "auth_user", None)
    if user is None:
        raise public_error(401, "AUTH_REQUIRED", "Authentication is required.")
    return user


def _require_role(required_role: str, user: AuthUser) -> AuthUser:
    if not role_allows(user, required_role):
        raise public_error(
            403,
            "FORBIDDEN",
            "The account is not allowed to perform this action.",
        )
    return user


def require_viewer(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    return _require_role("Viewer", user)


def require_operator(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    return _require_role("Operator", user)


def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    return _require_role("Admin", user)
