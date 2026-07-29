from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.dependencies import get_container
from api.errors import public_error
from core.container import AppContainer
from repositories.lms_repository import ConfirmationError
from repositories.upload_repository import UploadClaimError
from services.lms_plan import compute_plan_digest
from services.upload_store import UploadValidationError


router = APIRouter()


class LMSAnalyzeRequest(BaseModel):
    upload_id: str


class LMSLegacyUploadRequest(BaseModel):
    image_base64: str


class LMSConfirmRequest(BaseModel):
    analysis_job_id: int
    confirmation_token: str


async def _start_analysis(upload_id: str, container: AppContainer):
    dry_run = container.settings.bcnb_dry_run
    execution_mode = "dry-run" if dry_run else "production"
    job_id = await container.job_repo.create_job(
        "LMS_ANALYSIS",
        {
            "version": 3,
            "dry_run": dry_run,
            "execution_mode": execution_mode,
        },
        retryable=True,
        recoverable=True,
        mutates_external=False,
        message="Đang chuẩn bị ảnh phân tích LMS...",
    )
    try:
        upload_ids = await container.upload_store.claim(
            [upload_id],
            "lms_analysis",
            job_id,
        )
        await container.job_repo.update_payload(
            job_id,
            {
                "version": 3,
                "upload_ids": upload_ids,
                "dry_run": dry_run,
                "execution_mode": execution_mode,
            },
        )
    except UploadValidationError as exc:
        await container.job_repo.update_job(
            job_id,
            status="FAILED",
            progress=100,
            message="Không nhận được ảnh LMS hợp lệ.",
        )
        raise public_error(
            400,
            "UPLOAD_INVALID",
            "Dữ liệu ảnh LMS không hợp lệ.",
        ) from exc
    except UploadClaimError as exc:
        await container.job_repo.update_job(
            job_id,
            status="FAILED",
            progress=100,
            message="Ảnh LMS không còn sẵn sàng.",
        )
        raise public_error(
            409,
            "UPLOAD_CONFLICT",
            "Ảnh LMS không còn sẵn sàng.",
        ) from exc
    container.dispatcher.schedule_job(job_id)
    return {"status": "success", "job_id": job_id, "dry_run": dry_run}


@router.post("/api/lms/analyze")
async def api_lms_analyze(
    req: LMSAnalyzeRequest,
    container: AppContainer = Depends(get_container),
):
    return await _start_analysis(req.upload_id, container)


@router.post("/api/lms/upload-and-run", deprecated=True)
async def api_lms_legacy_analyze(
    req: LMSLegacyUploadRequest,
    container: AppContainer = Depends(get_container),
):
    """Compatibility alias; still analyzes only and never writes without confirmation."""
    try:
        upload = await container.upload_store.save(
            req.image_base64,
            "lms_analysis",
        )
    except UploadValidationError as exc:
        raise public_error(
            400,
            "UPLOAD_INVALID",
            "Dữ liệu tải lên không hợp lệ.",
        ) from exc
    return await _start_analysis(upload["upload_id"], container)


@router.post("/api/lms/confirm")
async def api_lms_confirm(
    req: LMSConfirmRequest,
    container: AppContainer = Depends(get_container),
):
    try:
        confirmation = await container.lms_repo.consume_confirmation(
            req.analysis_job_id,
            req.confirmation_token,
            max_age_minutes=container.settings.lms_confirmation_ttl_minutes,
        )
    except ConfirmationError as exc:
        raise public_error(
            409,
            "LMS_CONFIRMATION_INVALID",
            "Xác nhận LMS không hợp lệ hoặc đã hết hạn.",
        ) from exc

    plan = confirmation["plan"]
    expected_digest = confirmation["plan_digest"]
    if not plan.get("can_confirm") or compute_plan_digest(plan) != expected_digest:
        await container.job_repo.update_job(
            confirmation["job_id"],
            status="FAILED",
            message="Kế hoạch xác nhận không hợp lệ hoặc đã bị thay đổi.",
            progress=100,
        )
        raise public_error(
            409,
            "LMS_PLAN_INVALID",
            "Kế hoạch LMS không còn hợp lệ.",
        )

    container.dispatcher.schedule_job(confirmation["job_id"])
    return {
        "status": "success",
        "job_id": confirmation["job_id"],
        "dry_run": confirmation["dry_run"],
    }
