from pathlib import Path
from typing import Any, List, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.dependencies import (
    get_container,
    enqueue_job,
    require_admin,
    require_operator,
    require_viewer,
)
from api.errors import internal_error, public_error
from core.container import AppContainer
from core.auth import AuthUser
from logger_utils import log_message
from repositories.job_repository import (
    IdempotencyConflictError,
    IdempotencyKeyExpiredError,
)
from repositories.upload_repository import UploadClaimError
from services.googleform_idempotency import (
    GOOGLEFORM_OPERATION,
    actor_key,
    dry_run_key,
    key_digest,
    normalize_students,
    payload_hash,
    target_hash,
)
from services.lms_plan import (
    LmsSafetyError,
    normalize_lms_student_class_codes,
)
from services.upload_store import UploadValidationError


router = APIRouter()


class Base64Image(BaseModel):
    image_data: str


class OcrRequest(BaseModel):
    upload_ids: List[str] = Field(min_length=1, max_length=20)


class FetchLmsStudentsRequest(BaseModel):
    class_codes: Any = None
    class_code: Any = None


class RestoreGroupRequest(BaseModel):
    group_name: str


class MappingItem(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    field_type: Literal["text", "radio", "select", "dropdown"]
    value: str = Field(min_length=1, max_length=2000)


class StudentItem(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    class_code: str = Field(min_length=1, max_length=100)


class SubmitRequest(BaseModel):
    students: List[StudentItem] = Field(min_length=1, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=100)


@router.get("/api/ignored-groups", dependencies=[Depends(require_viewer)])
async def get_ignored_groups(container: AppContainer = Depends(get_container)):
    try:
        groups = await container.group_repo.get_all_ignored_groups()
        records = await container.group_repo.get_ignored_group_records()
        return {"groups": groups, "items": records}
    except Exception as exc:
        raise internal_error(exc, event="api.ignored_groups.read_failed") from exc


@router.post("/api/ignored-groups/confirm-ended", dependencies=[Depends(require_admin)])
async def confirm_ignored_group_ended(
    req: RestoreGroupRequest,
    container: AppContainer = Depends(get_container),
):
    try:
        changed = await container.group_repo.confirm_ignored_group_ended(
            req.group_name
        )
        if not changed:
            raise public_error(
                404,
                "GROUP_NOT_FOUND",
                "Không tìm thấy lớp cần xác nhận.",
            )
        log_message(f"Đã xác nhận lớp {req.group_name} kết thúc.")
        return {"message": "Đã xác nhận kết thúc"}
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_error(
            exc,
            event="api.ignored_groups.confirm_ended_failed",
        ) from exc


@router.post("/api/ignored-groups/restore", dependencies=[Depends(require_admin)])
async def restore_ignored_group(
    req: RestoreGroupRequest,
    container: AppContainer = Depends(get_container),
):
    try:
        restored = await container.group_repo.restore_ignored_group(req.group_name)
        if not restored:
            raise public_error(
                409,
                "GROUP_RESTORE_CONFLICT",
                "Không thể phục hồi: lớp không tồn tại hoặc identity hiện tại đã thay đổi.",
            )
        log_message(f"Đã phục hồi nhóm {req.group_name} từ danh sách loại bỏ qua API.")
        return {"message": "Phục hồi thành công"}
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_error(exc, event="api.ignored_groups.restore_failed") from exc


@router.post("/api/googleform/upload", deprecated=True, dependencies=[Depends(require_operator)])
async def upload_image(
    data: Base64Image,
    container: AppContainer = Depends(get_container),
):
    """Compatibility alias. New clients use POST /api/uploads."""
    try:
        result = await container.upload_store.save(
            data.image_data,
            "googleform_ocr",
        )
        return {**result, "message": "Tải ảnh thành công"}
    except UploadValidationError as exc:
        raise public_error(
            400,
            "UPLOAD_INVALID",
            "Dữ liệu tải lên không hợp lệ.",
        ) from exc


@router.post("/api/googleform/run-ocr", dependencies=[Depends(require_operator)])
async def api_run_googleform_ocr(
    req: OcrRequest,
    container: AppContainer = Depends(get_container),
):
    job_id = await container.job_repo.create_job(
        "GOOGLEFORM_OCR",
        None,
        retryable=True,
        recoverable=True,
        mutates_external=False,
        message="Đang chuẩn bị ảnh OCR...",
    )
    try:
        upload_ids = await container.upload_store.claim(
            req.upload_ids, "googleform_ocr", job_id
        )
        await container.job_repo.update_payload(job_id, {"upload_ids": upload_ids})
    except UploadValidationError as exc:
        await container.job_repo.update_job(
            job_id,
            status="FAILED",
            progress=100,
            message="Không nhận được ảnh OCR hợp lệ.",
        )
        raise public_error(
            400,
            "UPLOAD_INVALID",
            "Dữ liệu ảnh OCR không hợp lệ.",
        ) from exc
    except UploadClaimError as exc:
        await container.job_repo.update_job(
            job_id,
            status="FAILED",
            progress=100,
            message="Ảnh OCR không còn sẵn sàng.",
        )
        raise public_error(
            409,
            "UPLOAD_CONFLICT",
            "Ảnh OCR không còn sẵn sàng.",
        ) from exc
    await enqueue_job(container, job_id)
    return {"message": "Đang chạy AI OCR trong nền", "job_id": job_id}


@router.post(
    "/api/googleform/fetch-lms-students",
    dependencies=[Depends(require_operator)],
)
async def api_fetch_googleform_lms_students(
    req: FetchLmsStudentsRequest,
    container: AppContainer = Depends(get_container),
):
    try:
        normalized_codes = normalize_lms_student_class_codes(
            req.class_codes,
            req.class_code,
        )
    except LmsSafetyError as exc:
        raise public_error(
            422,
            getattr(exc, "code", None) or "LMS_CLASS_CODES_INVALID",
            str(exc),
        ) from exc

    job_id = await container.job_repo.create_job(
        "GOOGLEFORM_LMS_STUDENTS",
        {
            "version": 1,
            "class_codes": normalized_codes,
            "execution_mode": "dry-run",
        },
        retryable=True,
        recoverable=True,
        mutates_external=False,
        message=(
            f"Đang chuẩn bị lấy danh sách học viên từ LMS ({len(normalized_codes)} lớp)..."
        ),
    )
    await enqueue_job(container, job_id)
    return {
        "status": "success",
        "message": (
            f"Đang lấy danh sách học viên từ LMS cho {len(normalized_codes)} lớp..."
        ),
        "job_id": job_id,
        "class_codes": normalized_codes,
    }


@router.get("/api/googleform/mappings", dependencies=[Depends(require_viewer)])
async def get_mappings(container: AppContainer = Depends(get_container)):
    return {"mappings": await container.report_repo.get_form_mappings()}


@router.post("/api/googleform/mappings", dependencies=[Depends(require_admin)])
async def save_mapping(
    item: MappingItem,
    container: AppContainer = Depends(get_container),
):
    try:
        await container.report_repo.add_form_mapping(
            item.question,
            item.field_type,
            item.value,
        )
        log_message(f"Đã thêm mapping mới: {item.question}")
        return {"message": "Lưu thành công"}
    except Exception as exc:
        raise internal_error(exc, event="api.form_mapping.save_failed") from exc


@router.delete("/api/googleform/mappings/{mapping_id}", dependencies=[Depends(require_admin)])
async def delete_mapping(
    mapping_id: int,
    container: AppContainer = Depends(get_container),
):
    try:
        await container.report_repo.delete_form_mapping(mapping_id)
        log_message(f"Đã xóa mapping ID: {mapping_id}")
        return {"message": "Xóa thành công"}
    except Exception as exc:
        raise internal_error(exc, event="api.form_mapping.delete_failed") from exc


@router.get("/api/googleform/history", dependencies=[Depends(require_viewer)])
async def get_history(container: AppContainer = Depends(get_container)):
    return {"history": await container.report_repo.get_form_submissions()}


@router.delete("/api/googleform/history", dependencies=[Depends(require_operator)])
async def clear_history(container: AppContainer = Depends(get_container)):
    try:
        deleted_count = await container.report_repo.clear_form_submissions()
        log_message(f"Đã xóa toàn bộ lịch sử nộp form ({deleted_count} bản ghi).")
        return {"message": "Đã xóa toàn bộ lịch sử nộp form", "deleted_count": deleted_count}
    except Exception as exc:
        raise internal_error(exc, event="api.googleform_history.clear_failed") from exc


@router.get("/api/googleform/submissions/{submission_id}/screenshot", dependencies=[Depends(require_viewer)])
async def get_submission_screenshot(
    submission_id: int,
    container: AppContainer = Depends(get_container),
):
    sub = await container.report_repo.get_form_submission(submission_id)
    if not sub or not sub.get("screenshot_path"):
        raise public_error(404, "SCREENSHOT_NOT_FOUND", "Không tìm thấy ảnh chụp cho lần nộp này.")

    raw_path = Path(sub["screenshot_path"]).resolve()
    allowed_dir = (Path(container.settings.runtime_root) / "screenshots" / "googleform").resolve()
    try:
        raw_path.relative_to(allowed_dir)
    except ValueError:
        raise public_error(403, "FORBIDDEN_PATH", "Đường dẫn tệp không hợp lệ.")

    if not raw_path.is_file():
        raise public_error(404, "SCREENSHOT_FILE_NOT_FOUND", "Tệp ảnh chụp không còn tồn tại trên máy chủ.")

    return FileResponse(str(raw_path), media_type="image/png")


@router.delete("/api/googleform/submissions/{submission_id}/screenshot", dependencies=[Depends(require_operator)])
async def delete_submission_screenshot(
    submission_id: int,
    container: AppContainer = Depends(get_container),
):
    try:
        success = await container.report_repo.delete_submission_screenshot(submission_id)
        if not success:
            raise public_error(404, "SUBMISSION_NOT_FOUND", "Không tìm thấy bản ghi nộp form.")
        log_message(f"Đã xóa ảnh chụp cho lần nộp ID {submission_id}.")
        return {"message": "Đã xóa ảnh chụp thành công"}
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_error(exc, event="api.googleform_screenshot.delete_failed") from exc


@router.post("/api/googleform/submit")
async def run_submit(
    req: SubmitRequest,
    container: AppContainer = Depends(get_container),
    current_user: AuthUser = Depends(require_operator),
):
    students = normalize_students(
        {"name": item.name, "class_code": item.class_code} for item in req.students
    )
    is_dry_run = container.settings.google_form_mode != "production"
    execution_mode = "dry-run" if is_dry_run else "production"
    request_hash = payload_hash(students, execution_mode)
    raw_key = (req.idempotency_key or "").strip()
    if not raw_key:
        if not is_dry_run:
            raise public_error(
                400,
                "IDEMPOTENCY_KEY_REQUIRED",
                "Production yêu cầu idempotency_key do client tạo và giữ lại khi retry.",
            )
        raw_key = dry_run_key(students, execution_mode)
    persisted_key = key_digest(raw_key)
    username = getattr(current_user, "username", "local-admin")
    role = getattr(current_user, "role", "Admin")
    persisted_actor = actor_key(username, role)
    persisted_target = target_hash(students)
    payload = {
        "version": 4,
        "students": students,
        # Only the redacted digest crosses the job/worker boundary.
        "idempotency_key": persisted_key,
        "idempotency_key_sha256": persisted_key,
        "payload_hash": request_hash,
        "actor_key": persisted_actor,
        "target_hash": persisted_target,
        "execution_mode": execution_mode,
    }
    try:
        result = await container.job_repo.create_idempotent_job(
            "GOOGLEFORM_SUBMIT",
            payload,
            key_digest=persisted_key,
            operation=GOOGLEFORM_OPERATION,
            payload_hash=request_hash,
            actor_key=persisted_actor,
            target_hash=persisted_target,
            execution_mode=execution_mode,
            ttl_hours=int(
                getattr(
                    container.settings,
                    "googleform_idempotency_ttl_hours",
                    24,
                )
            ),
            retryable=is_dry_run,
            recoverable=is_dry_run,
            mutates_external=not is_dry_run,
            message="Đang chờ nộp Google Form...",
        )
    except IdempotencyConflictError as exc:
        raise public_error(
            409,
            "IDEMPOTENCY_CONFLICT",
            "Idempotency key đã được dùng cho payload, tài khoản, lớp hoặc chế độ khác.",
        ) from exc
    except IdempotencyKeyExpiredError as exc:
        raise public_error(
            409,
            "IDEMPOTENCY_KEY_EXPIRED",
            "Idempotency key đã hết hạn; hãy tạo intent mới.",
        ) from exc

    job_id = int(result["job_id"])
    job_status = str(result.get("status") or "PENDING").upper()
    if not result["created"] and job_status in {"FAILED", "CANCELLED"}:
        code = f"IDEMPOTENCY_TERMINAL_{job_status}"
        raise public_error(
            409,
            code,
            "Intent cũ đã kết thúc không thành công; hãy kiểm tra job trước khi tạo intent mới.",
        )
    if result["created"]:
        await enqueue_job(container, job_id)
    replay_messages = {
        "PENDING": "Intent đã tồn tại và đang chờ xử lý.",
        "RUNNING": "Intent đã tồn tại và đang được xử lý.",
        "SUCCESS": "Intent đã hoàn tất thành công trước đó.",
        "PARTIAL_SUCCESS": "Intent đã hoàn tất một phần; hãy xem kết quả job.",
    }
    return {
        "message": (
            "Đã bắt đầu nộp form trong nền"
            if result["created"]
            else replay_messages.get(job_status, "Intent đã tồn tại.")
        ),
        "job_id": job_id,
        # Echo the client token so a timeout can be retried verbatim.
        "idempotency_key": raw_key,
        "idempotency_replay": not bool(result["created"]),
        "job_status": job_status,
        "created": bool(result["created"]),
    }
