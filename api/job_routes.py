from fastapi import APIRouter, Depends, Query

from api.dependencies import get_container, require_operator, require_viewer
from api.errors import internal_error, public_error
from core.container import AppContainer
from repositories.job_repository import JobNotFoundError, JobStateError


router = APIRouter()


@router.get("/api/jobs/active/latest", dependencies=[Depends(require_viewer)])
async def get_latest_active_job(container: AppContainer = Depends(get_container)):
    return await container.job_repo.get_latest_job(active_only=True)


@router.get("/api/jobs/latest", dependencies=[Depends(require_viewer)])
async def get_latest_job(container: AppContainer = Depends(get_container)):
    return await container.job_repo.get_latest_job()


@router.get("/api/jobs/{job_id}", dependencies=[Depends(require_viewer)])
async def get_job_status(
    job_id: int,
    container: AppContainer = Depends(get_container),
):
    job = await container.job_repo.get_job(job_id)
    if not job:
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.")
    return job


@router.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_operator)])
async def cancel_job(job_id: int, container: AppContainer = Depends(get_container)):
    try:
        result = await container.job_repo.request_cancel(job_id)
    except JobNotFoundError as exc:
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.") from exc
    except JobStateError as exc:
        raise public_error(
            409,
            "JOB_STATE_CONFLICT",
            "Trạng thái job không cho phép thao tác này.",
        ) from exc
    return {"job_id": job_id, **result}


@router.post("/api/jobs/{job_id}/retry", dependencies=[Depends(require_operator)])
async def retry_terminal_job(
    job_id: int,
    container: AppContainer = Depends(get_container),
):
    try:
        new_job_id = await container.dispatcher.retry_job(job_id)
    except JobNotFoundError as exc:
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.") from exc
    except JobStateError as exc:
        raise public_error(
            409,
            "JOB_STATE_CONFLICT",
            "Job hiện tại không thể chạy lại.",
        ) from exc
    except Exception as exc:
        raise internal_error(exc, event="api.jobs.retry_failed") from exc
    return {"job_id": new_job_id, "retry_of_job_id": job_id}


@router.get("/api/jobs/{job_id}/events", dependencies=[Depends(require_viewer)])
async def get_job_events(
    job_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    container: AppContainer = Depends(get_container),
):
    if not await container.job_repo.get_job(job_id):
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.")
    return {"events": await container.job_repo.list_events(job_id, limit)}


@router.get("/api/jobs/{job_id}/audit", dependencies=[Depends(require_viewer)])
async def get_job_audit(
    job_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    container: AppContainer = Depends(get_container),
):
    if not await container.job_repo.get_job(job_id):
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.")
    return {"actions": await container.audit_repo.list_for_job(job_id, limit)}


@router.get("/api/jobs/{job_id}/targets", dependencies=[Depends(require_viewer)])
async def get_job_targets(
    job_id: int,
    container: AppContainer = Depends(get_container),
):
    job = await container.job_repo.get_job(job_id)
    if not job:
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.")
    repository = container.zalo_batch_repo
    settings = getattr(container, "settings", None)
    return {
        "job_id": job_id,
        "worker_count": max(
            int(getattr(settings, "zalo_scan_concurrency", 2)),
            int(getattr(settings, "bcnb_write_concurrency", 2)),
        ),
        "summary": await repository.summary(job_id),
        "targets": await repository.list_for_job(job_id),
    }
