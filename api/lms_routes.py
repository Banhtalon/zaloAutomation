from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.dependencies import enqueue_job, get_container, require_admin, require_operator
from api.errors import public_error
from core.container import AppContainer
from repositories.lms_repository import ConfirmationError
from repositories.upload_repository import UploadClaimError
from services.lms_plan import (
    LmsSafetyError,
    compute_plan_digest,
    compute_selection_digest,
    validate_selected_classes_to_tick,
)
from services.upload_store import UploadValidationError


router = APIRouter()


class LMSAnalyzeRequest(BaseModel):
    upload_id: str | None = None


class LMSLegacyUploadRequest(BaseModel):
    image_base64: str


class LMSConfirmRequest(BaseModel):
    analysis_job_id: int
    confirmation_token: str
    selected_classes_to_tick: list[str] | None = None


async def _start_analysis(upload_id: str | None, container: AppContainer):
    dry_run = container.settings.bcnb_dry_run
    execution_mode = "dry-run" if dry_run else "production"
    if not upload_id:
        job_id = await container.job_repo.create_job(
            "LMS_ANALYSIS",
            {
                "version": 4,
                "source": "lms_web",
                "dry_run": dry_run,
                "execution_mode": execution_mode,
            },
            retryable=True,
            recoverable=True,
            mutates_external=False,
            message="Đang chuẩn bị quét trang LMS và lập kế hoạch...",
        )
        await enqueue_job(container, job_id)
        return {"status": "success", "job_id": job_id, "dry_run": dry_run}

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
    await enqueue_job(container, job_id)
    return {"status": "success", "job_id": job_id, "dry_run": dry_run}


@router.post("/api/lms/analyze", dependencies=[Depends(require_operator)])
async def api_lms_analyze(
    req: LMSAnalyzeRequest | None = None,
    container: AppContainer = Depends(get_container),
):
    return await _start_analysis(req.upload_id if req else None, container)


@router.post("/api/lms/upload-and-run", deprecated=True, dependencies=[Depends(require_operator)])
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


@router.post("/api/lms/confirm", dependencies=[Depends(require_admin)])
@router.post("/run-lms", dependencies=[Depends(require_admin)], include_in_schema=False)
async def api_lms_confirm(
    req: LMSConfirmRequest,
    container: AppContainer = Depends(get_container),
):
    try:
        if req.selected_classes_to_tick is None:
            confirmation = await container.lms_repo.consume_confirmation(
                req.analysis_job_id,
                req.confirmation_token,
                # The mode is read from the current immutable app settings at the
                # last possible moment.  A preview made in production must never
                # be consumable after the operator switches to dry-run (or vice
                # versa).
                dry_run=container.settings.bcnb_dry_run,
                max_age_minutes=container.settings.lms_confirmation_ttl_minutes,
            )
        else:
            confirmation = await container.lms_repo.consume_confirmation(
                req.analysis_job_id,
                req.confirmation_token,
                dry_run=container.settings.bcnb_dry_run,
                max_age_minutes=container.settings.lms_confirmation_ttl_minutes,
                selected_classes_to_tick=req.selected_classes_to_tick,
            )
    except ConfirmationError as exc:
        code = getattr(exc, "code", "LMS_CONFIRMATION_INVALID")
        if code in {
            "LMS_SELECTED_CLASSES_EMPTY",
            "LMS_SELECTED_CLASSES_DUPLICATE",
            "LMS_SELECTED_CLASSES_INVALID",
        }:
            raise public_error(422, code, str(exc)) from exc
        if code == "LMS_SELECTED_CLASS_NOT_IN_PLAN":
            raise public_error(409, code, str(exc)) from exc
        raise public_error(
            409,
            code,
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

    if req.selected_classes_to_tick is not None and "selected_classes_to_tick" not in plan:
        try:
            selected_subset = validate_selected_classes_to_tick(
                req.selected_classes_to_tick,
                plan.get("classes_to_tick", []),
            )
            plan["selected_classes_to_tick"] = selected_subset
            plan["selection_digest"] = compute_selection_digest(
                expected_digest, selected_subset
            )
        except LmsSafetyError as exc:
            code = getattr(exc, "code", "LMS_SELECTED_CLASSES_INVALID")
            status_code = 409 if code == "LMS_SELECTED_CLASS_NOT_IN_PLAN" else 422
            raise public_error(status_code, code, str(exc)) from exc

    await enqueue_job(container, confirmation["job_id"])
    response = {
        "status": "success",
        "job_id": confirmation["job_id"],
        "dry_run": confirmation["dry_run"],
    }
    if "selected_classes_to_tick" in plan:
        response["selected_classes_to_tick"] = plan["selected_classes_to_tick"]
    return response
