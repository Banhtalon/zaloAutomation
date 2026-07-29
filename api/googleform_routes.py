import secrets
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_container
from api.errors import internal_error, public_error
from core.container import AppContainer
from logger_utils import log_message
from repositories.upload_repository import UploadClaimError
from services.upload_store import UploadValidationError


router = APIRouter()


class Base64Image(BaseModel):
    image_data: str


class OcrRequest(BaseModel):
    upload_ids: List[str] = Field(min_length=1, max_length=20)


class RestoreGroupRequest(BaseModel):
    group_name: str


class MappingItem(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    field_type: Literal["text", "radio"]
    value: str = Field(min_length=1, max_length=2000)


class StudentItem(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    class_code: str = Field(min_length=1, max_length=100)


class SubmitRequest(BaseModel):
    students: List[StudentItem] = Field(min_length=1, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=100)


@router.get("/api/ignored-groups")
async def get_ignored_groups(container: AppContainer = Depends(get_container)):
    try:
        await container.group_repo.archive_confirmed_ended_groups(14)
        groups = await container.group_repo.get_all_ignored_groups()
        records = await container.group_repo.get_ignored_group_records()
        return {"groups": groups, "items": records}
    except Exception as exc:
        raise internal_error(exc, event="api.ignored_groups.read_failed") from exc


@router.post("/api/ignored-groups/confirm-ended")
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


@router.post("/api/ignored-groups/restore")
async def restore_ignored_group(
    req: RestoreGroupRequest,
    container: AppContainer = Depends(get_container),
):
    try:
        await container.group_repo.remove_ignored_group(req.group_name)
        await container.group_repo.add_group(req.group_name, "Chưa xác định")
        log_message(f"Đã phục hồi nhóm {req.group_name} từ danh sách loại bỏ qua API.")
        return {"message": "Phục hồi thành công"}
    except Exception as exc:
        raise internal_error(exc, event="api.ignored_groups.restore_failed") from exc


@router.post("/api/googleform/upload", deprecated=True)
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


@router.post("/api/googleform/run-ocr")
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
    container.dispatcher.schedule_job(job_id)
    return {"message": "Đang chạy AI OCR trong nền", "job_id": job_id}


@router.get("/api/googleform/mappings")
async def get_mappings(container: AppContainer = Depends(get_container)):
    return {"mappings": await container.report_repo.get_form_mappings()}


@router.post("/api/googleform/mappings")
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


@router.delete("/api/googleform/mappings/{mapping_id}")
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


@router.get("/api/googleform/history")
async def get_history(container: AppContainer = Depends(get_container)):
    return {"history": await container.report_repo.get_form_submissions()}


@router.post("/api/googleform/submit")
async def run_submit(
    req: SubmitRequest,
    container: AppContainer = Depends(get_container),
):
    students = [
        {"name": item.name.strip(), "class_code": item.class_code.strip().upper()}
        for item in req.students
    ]
    batch_key = req.idempotency_key or secrets.token_urlsafe(24)
    is_dry_run = container.settings.google_form_mode != "production"
    execution_mode = "dry-run" if is_dry_run else "production"
    payload = {
        "version": 3,
        "students": students,
        "idempotency_key": batch_key,
        "execution_mode": execution_mode,
    }
    job_id = await container.job_repo.create_job(
        "GOOGLEFORM_SUBMIT",
        payload,
        retryable=is_dry_run,
        recoverable=is_dry_run,
        mutates_external=not is_dry_run,
        message="Đang chờ nộp Google Form...",
    )
    container.dispatcher.schedule_job(job_id)
    return {
        "message": "Đã bắt đầu nộp form trong nền",
        "job_id": job_id,
        "idempotency_key": batch_key,
    }
