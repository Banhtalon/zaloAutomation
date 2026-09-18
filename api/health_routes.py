import asyncio

from fastapi import APIRouter, Depends, Query, Response

from api.dependencies import get_container, require_viewer
from core.container import AppContainer
from services.observability import collect_job_metrics, live_status, ready_status


router = APIRouter()


@router.get("/api/health/live")
async def health_live(container: AppContainer = Depends(get_container)):
    return live_status(
        version=container.settings.app_version,
        instance_id=container.instance_id,
        started_at=container.started_at,
        started_monotonic=container.started_monotonic,
    )


@router.get("/api/health/ready")
async def health_ready(
    response: Response,
    container: AppContainer = Depends(get_container),
):
    result = await asyncio.to_thread(
        ready_status,
        settings=container.settings,
        dispatcher=container.dispatcher,
        instance_id=container.instance_id,
    )
    if not result["ready"]:
        response.status_code = 503
    return result


@router.get("/api/metrics/jobs", dependencies=[Depends(require_viewer)])
async def job_metrics(
    window_hours: int | None = Query(default=None, ge=1, le=168),
    container: AppContainer = Depends(get_container),
):
    return await asyncio.to_thread(
        collect_job_metrics,
        window_hours,
        settings=container.settings,
        dispatcher=container.dispatcher,
    )
