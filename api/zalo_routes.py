from fastapi import APIRouter, Depends

from api.dependencies import get_container
from api.errors import internal_error
from core.container import AppContainer


router = APIRouter()


@router.post("/auto-discover")
async def auto_discover(container: AppContainer = Depends(get_container)):
    job_id = await container.job_repo.create_job(
        "ZALO_DISCOVERY",
        {"version": 2},
        retryable=True,
        recoverable=True,
        mutates_external=False,
        message="Đang chờ dò tìm nhóm...",
    )
    container.dispatcher.schedule_job(job_id)
    return {"message": "Đang bắt đầu dò tìm nhóm.", "job_id": job_id}


@router.post("/run-scan")
async def run_scan(container: AppContainer = Depends(get_container)):
    dry_run = container.settings.bcnb_dry_run
    execution_mode = "dry-run" if dry_run else "production"
    job_id = await container.job_repo.create_job(
        "ZALO_SCAN",
        {
            "version": 3,
            "dry_run": dry_run,
            "execution_mode": execution_mode,
        },
        retryable=dry_run,
        recoverable=dry_run,
        mutates_external=not dry_run,
        message="Đang chờ quét Zalo/BCNB...",
    )
    container.dispatcher.schedule_job(job_id)
    return {"message": "Bắt đầu quét trong nền...", "job_id": job_id, "dry_run": dry_run}


@router.get("/api/groups")
async def get_groups(container: AppContainer = Depends(get_container)):
    try:
        return {"groups": await container.group_repo.get_all_groups()}
    except Exception as exc:
        raise internal_error(exc, event="api.groups.read_failed") from exc


@router.get("/api/class-lifecycles")
async def get_class_lifecycles(
    include_archived: bool = False,
    container: AppContainer = Depends(get_container),
):
    try:
        return {
            "classes": await container.group_repo.get_class_lifecycles(
                include_archived=include_archived
            )
        }
    except Exception as exc:
        raise internal_error(exc, event="api.class_lifecycles.read_failed") from exc
