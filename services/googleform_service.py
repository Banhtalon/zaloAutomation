import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from typing import List, Dict

from googleform_autosubmit.config import Config
from googleform_autosubmit.form_submitter import SubmissionOutcomeUnknownError
from googleform_autosubmit.ocr_processor import OCRProcessor
from logger_utils import log_message
from repositories.audit_repository import AuditRepository
from repositories.job_repository import JobRepository
from repositories.report_repository import ReportRepository
from services.external_gateways import ExternalGateways, StudentOcrGateway
from services.googleform_idempotency import persisted_key_digest

report_repo = ReportRepository()
job_repo = JobRepository()
audit_repo = AuditRepository()

_ocr_lock = asyncio.Lock()
_form_submit_job_lock = asyncio.Lock()

async def _ensure_not_cancelled(job_id: int):
    checker = getattr(job_repo, "ensure_not_cancelled", None)
    if checker:
        await checker(job_id)


async def _is_cancel_requested(job_id: int) -> bool:
    checker = getattr(job_repo, "is_cancel_requested", None)
    return bool(await checker(job_id)) if checker else False


@asynccontextmanager
async def _cancellable_lock(lock: asyncio.Lock, job_id: int, waiting_message: str):
    if lock.locked():
        await job_repo.update_job(job_id, message=waiting_message)
    while True:
        await _ensure_not_cancelled(job_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=1)
            break
        except asyncio.TimeoutError:
            continue
    try:
        yield
    finally:
        lock.release()


async def background_submit_forms(
    job_id: int,
    students: List[Dict[str, str]],
    idempotency_key: str | None = None,
    execution_mode: str = "dry-run",
    *,
    runtime=None,
):
    async with _cancellable_lock(
        runtime.form_submit_lock if runtime else _form_submit_job_lock,
        job_id,
        "Đang chờ đợt nộp Google Form trước đó hoàn tất...",
    ):
        await _background_submit_forms(
            job_id,
            students,
            idempotency_key or f"job-{job_id}",
            execution_mode,
            runtime.external if runtime else None,
        )


async def _background_submit_forms(
    job_id: int,
    students: List[Dict[str, str]],
    idempotency_key: str,
    execution_mode: str,
    external: ExternalGateways | None = None,
):
    await job_repo.update_job(job_id, status="RUNNING", message="Đang bắt đầu nộp form...", progress=5)
    
    try:
        mapping_dict = await report_repo.get_form_mapping_dict()
        external = external or ExternalGateways.production()
        # The worker may be called directly by a legacy integration; normalize
        # again so no raw client token reaches audit or report persistence.
        batch_digest = persisted_key_digest(idempotency_key)
        sem = asyncio.Semaphore(Config.CONCURRENCY_LIMIT)
        log_message(f"Khởi chạy tiến trình nộp {len(students)} form...")

        async with external.browser.form_batch(
            mapping_dict,
            execution_mode,
            headless=Config.HEADLESS,
        ) as submitter:

            async def submit_student(idx, student):
                async with sem:
                    await asyncio.sleep(idx * 1.5)
                    student_name = str(student.get("name", "")).strip()
                    class_code = str(student.get("class_code", "")).strip()
                    audit_id = None
                    try:
                        await _ensure_not_cancelled(job_id)
                        if not student_name or not class_code:
                            raise ValueError("Thiếu tên học viên hoặc mã lớp.")
                        target_hash = hashlib.sha256(
                            f"{student_name.casefold()}\0{class_code.upper()}".encode("utf-8")
                        ).hexdigest()[:24]
                        audit, created = await audit_repo.record_intent(
                            action_key=f"googleform:{batch_digest}:{target_hash}",
                            job_id=job_id,
                            action_type="GOOGLE_FORM_SUBMIT",
                            target_type="STUDENT_CLASS",
                            target_id=f"{student_name} | {class_code.upper()}",
                            mode=execution_mode,
                            before_state=None,
                            intended_state={"submitted": True},
                            reason={"batch_key_sha256": batch_digest},
                        )
                        audit_id = audit["id"]
                        if not created:
                            if audit["outcome"] in {"VERIFIED", "DRY_RUN"}:
                                return {
                                    "name": student_name,
                                    "class_code": class_code,
                                    "success": True,
                                    "outcome": audit["outcome"],
                                    "duplicate_prevented": True,
                                }
                            return {
                                "name": student_name,
                                "class_code": class_code,
                                "success": False,
                                "outcome": audit.get("outcome") or "UNKNOWN",
                                "error": "Đã có lần nộp chưa xác định kết quả; cần kiểm tra trước khi gửi lại.",
                            }
                        await _ensure_not_cancelled(job_id)
                        log_message(
                            f"🚀 Nộp form cho học viên #{idx + 1} ({class_code})"
                        )
                        screenshot_path = await submitter.submit_form(student_name, class_code)
                        status_text = "Thành công (DryRun)" if execution_mode != "production" else "Thành công"
                        await audit_repo.complete_action(
                            audit_id,
                            outcome="DRY_RUN" if execution_mode != "production" else "VERIFIED",
                            after_state={"submitted": execution_mode == "production"},
                        )
                        await report_repo.add_form_submission(
                            student_name,
                            class_code,
                            status_text,
                            "",
                            job_id=job_id,
                            action_key=audit["action_key"],
                            screenshot_path=screenshot_path,
                        )
                        return {
                            "name": student_name,
                            "class_code": class_code,
                            "success": True,
                            "outcome": (
                                "DRY_RUN"
                                if execution_mode != "production"
                                else "VERIFIED"
                            ),
                        }
                    except Exception as exc:
                        error = str(exc)
                        if student_name and student_name.strip():
                            error = error.replace(
                                student_name, "[REDACTED_STUDENT_NAME]"
                            ).replace(
                                student_name.strip(), "[REDACTED_STUDENT_NAME]"
                            )
                        cancelled = await _is_cancel_requested(job_id)
                        failure_outcome = (
                            "CANCELLED_BEFORE_WRITE"
                            if cancelled
                            else (
                                "UNKNOWN"
                                if isinstance(exc, SubmissionOutcomeUnknownError)
                                else "FAILED"
                            )
                        )
                        if audit_id is not None:
                            await audit_repo.complete_action(
                                audit_id,
                                outcome=failure_outcome,
                                after_state=None,
                                error_message=error[:500],
                            )
                        log_message(
                            f"❌ Nộp thất bại cho học viên #{idx + 1} ({class_code}): {error}"
                        )
                        await report_repo.add_form_submission(
                            student_name,
                            class_code,
                            "Thất bại",
                            error,
                            job_id=job_id,
                            action_key=(audit["action_key"] if audit_id is not None else None),
                        )
                        return {
                            "name": student_name,
                            "class_code": class_code,
                            "success": False,
                            "outcome": failure_outcome,
                            "error": error,
                        }
                    finally:
                        await asyncio.sleep(Config.RATE_LIMIT)

            outcomes = await asyncio.gather(
                *(submit_student(idx, student) for idx, student in enumerate(students))
            )

        success_count = sum(1 for item in outcomes if item["success"])
        failed_count = len(outcomes) - success_count
        cancelled = await _is_cancel_requested(job_id)
        if cancelled and success_count:
            status = "PARTIAL_SUCCESS"
        elif cancelled:
            status = "CANCELLED"
        elif success_count and failed_count:
            status = "PARTIAL_SUCCESS"
        elif failed_count:
            status = "FAILED"
        else:
            status = "SUCCESS"
        await job_repo.update_job(
            job_id,
            status=status,
            message=f"Hoàn tất: {success_count} thành công, {failed_count} thất bại.",
            progress=100,
            results=json.dumps(
                {"kind": "googleform_submit", "outcomes": outcomes},
                ensure_ascii=False,
            ),
        )
        log_message("Tiến trình nộp Google Form đã hoàn thành!")
    except Exception as exc:
        log_message(f"❌ Tiến trình nộp Google Form bị lỗi: {exc}")
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message=f"Lỗi nộp Google Form: {str(exc)[:180]}",
            progress=100,
        )


async def run_googleform_ocr(
    job_id: int,
    image_paths: list[str] | None = None,
    *,
    runtime=None,
):
    async with _cancellable_lock(
        runtime.ocr_lock if runtime else _ocr_lock,
        job_id,
        "Đang chờ tác vụ OCR trước đó hoàn tất...",
    ):
        ocr = runtime.external.student_ocr_factory() if runtime else None
        await _run_googleform_ocr(job_id, image_paths, ocr=ocr)


async def _run_googleform_ocr(
    job_id: int,
    image_paths: list[str] | None = None,
    *,
    ocr: StudentOcrGateway | None = None,
):
    await job_repo.update_job(job_id, status="RUNNING", message="Đang chạy AI OCR...", progress=10)
    log_message("Bắt đầu trích xuất OCR bằng Gemini Vision...")
    
    ocr = ocr or OCRProcessor()
    await _ensure_not_cancelled(job_id)
    if image_paths is None:
        image_paths = []
    
    if not image_paths:
        await job_repo.update_job(
            job_id, status="FAILED", message="Lỗi: Không có ảnh.", progress=100
        )
        return {"error": "Vui lòng tải ảnh lên trước khi chạy OCR"}
        
    students_data = []
    tasks = []
    for path in image_paths:
        tasks.append(ocr.process_file_async(path))
    
    try:
        ocr_results = await asyncio.gather(*tasks)
        await _ensure_not_cancelled(job_id)
        seen = set()
        for data_from_img in ocr_results:
            for item in data_from_img:
                if not isinstance(item, dict):
                    continue
                name = " ".join(str(item.get("name", "")).split())
                class_code = "".join(str(item.get("class_code", "")).split()).upper()
                key = (name.casefold(), class_code)
                if name and class_code and key not in seen:
                    seen.add(key)
                    students_data.append({"name": name, "class_code": class_code})

        if not students_data:
            raise ValueError("AI không trích xuất được học viên hợp lệ nào.")
            
        log_message(f"Đã trích xuất thành công {len(students_data)} học viên.")
        payload = {
            "kind": "googleform_ocr",
            "students": students_data,
            "source_count": len(image_paths),
        }
        await job_repo.update_job(
            job_id,
            status="SUCCESS",
            message="OCR hoàn tất.",
            progress=100,
            results=json.dumps(payload, ensure_ascii=False),
        )
        
        return {"students": students_data}
    except Exception as e:
        await job_repo.update_job(
            job_id, status="FAILED", message=f"Lỗi OCR: {e}", progress=100
        )
        log_message(f"Lỗi OCR: {e}")
        return {"error": str(e)}

