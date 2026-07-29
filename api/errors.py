from fastapi import HTTPException

from logger_utils import log_message


def public_error(status_code: int, code: str, message: str) -> HTTPException:
    """Build the stable, non-sensitive error contract exposed by the API."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def internal_error(exc: Exception, *, event: str) -> HTTPException:
    """Log only safe diagnostics and hide implementation details from clients."""
    log_message(
        "Yêu cầu API thất bại do lỗi nội bộ.",
        level="ERROR",
        event=event,
        error_type=type(exc).__name__,
    )
    return public_error(
        500,
        "INTERNAL_ERROR",
        "Không thể xử lý yêu cầu lúc này.",
    )
