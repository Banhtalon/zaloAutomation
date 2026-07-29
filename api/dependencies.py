from fastapi import Request

from core.container import AppContainer


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("Application container chưa được cấu hình.")
    return container
