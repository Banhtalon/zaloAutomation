import asyncio
import contextlib
import json
import secrets
from collections import defaultdict
from copy import deepcopy
from time import monotonic

from db.database import database_context
from googleform_autosubmit.config import Config
from logger_utils import (
    get_default_log_manager,
    log_context,
    log_message,
    use_log_manager,
)
from repositories.audit_repository import AuditRepository
from repositories.job_repository import (
    JobCancelledError,
    JobNotFoundError,
    JobRepository,
    JobStateError,
    TERMINAL_STATUSES,
)
from services.execution_mode import mode_from_job, normalize_execution_mode
from services.runtime import WorkerRuntime
from services.upload_store import UploadStore


class JobDispatcher:
    """Per-application owner of task registry, shutdown state and worker runtime."""

    def __init__(
        self,
        *,
        job_repo=None,
        upload_store=None,
        worker_runtime=None,
        audit_repo=None,
        database_path: str | None = None,
        heartbeat_seconds: int | None = None,
        log_manager=None,
    ):
        self.job_repo = job_repo or JobRepository()
        self.upload_store = upload_store or UploadStore()
        self.worker_runtime = worker_runtime or WorkerRuntime()
        self.audit_repo = audit_repo or AuditRepository()
        self.database_path = database_path
        self.heartbeat_seconds = max(
            2,
            int(heartbeat_seconds or Config.JOB_HEARTBEAT_SECONDS),
        )
        self.log_manager = log_manager
        self._tasks: dict[int, asyncio.Task] = {}
        self._shutting_down = False

    async def _heartbeat_loop(self, job_id: int) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            if not await self.job_repo.heartbeat(job_id):
                return

    async def _dispatch(self, job: dict) -> None:
        job_id = job["id"]
        payload = job.get("request_payload") or {}
        job_type = job["job_type"]

        if job_type == "ZALO_DISCOVERY":
            from services.zalo_discovery_service import run_auto_discovery

            await run_auto_discovery(job_id, runtime=self.worker_runtime)
        elif job_type == "ZALO_SCAN":
            from services.zalo_service import run_zalo_automation

            await run_zalo_automation(
                job_id,
                mode_from_job(job),
                runtime=self.worker_runtime,
            )
        elif job_type == "GOOGLEFORM_OCR":
            from services.googleform_service import run_googleform_ocr

            paths = await self.upload_store.paths_for_job(job_id, "googleform_ocr")
            await run_googleform_ocr(
                job_id,
                paths,
                runtime=self.worker_runtime,
            )
        elif job_type == "GOOGLEFORM_SUBMIT":
            from services.googleform_service import background_submit_forms

            await background_submit_forms(
                job_id,
                payload.get("students") or [],
                payload.get("idempotency_key") or f"job-{job_id}",
                mode_from_job(job),
                runtime=self.worker_runtime,
            )
        elif job_type == "LMS_ANALYSIS":
            from services.lms_service import run_lms_analysis

            paths = await self.upload_store.paths_for_job(job_id, "lms_analysis")
            if len(paths) != 1:
                raise ValueError("Job LMS phải có đúng một ảnh đầu vào.")
            await run_lms_analysis(
                job_id,
                paths[0],
                normalize_execution_mode(payload.get("execution_mode")),
                runtime=self.worker_runtime,
            )
        elif job_type == "LMS_AUTOMATION":
            from services.lms_service import run_lms_automation

            await run_lms_automation(
                job_id,
                payload.get("plan") or {},
                payload.get("expected_digest") or "",
                mode_from_job(job),
                runtime=self.worker_runtime,
            )
        else:
            raise ValueError(f"Không có worker cho loại job {job_type}.")

    async def _run_job(self, job_id: int) -> None:
        manager = self.log_manager or get_default_log_manager()
        database_scope = (
            database_context(self.database_path)
            if self.database_path
            else contextlib.nullcontext()
        )
        with database_scope, use_log_manager(manager):
            await self._run_job_in_context(job_id)

    async def _run_job_in_context(self, job_id: int) -> None:
        heartbeat_task = None
        started = monotonic()
        job = None
        try:
            if not await self.job_repo.mark_running(job_id):
                return
            job = await self.job_repo.get_job_record(job_id)
            if not job:
                return
            await self.job_repo.ensure_not_cancelled(job_id)
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id))
            with log_context(
                job_id=job_id,
                job_type=job["job_type"],
                component="job_dispatcher",
            ):
                log_message("Worker bắt đầu.", event="job.started")
                await self._dispatch(job)
            current = await self.job_repo.get_job(job_id)
            if current and current["status"] not in TERMINAL_STATUSES:
                await self.job_repo.ensure_not_cancelled(job_id)
                await self.job_repo.update_job(
                    job_id,
                    status="FAILED",
                    progress=100,
                    message="Worker kết thúc nhưng không tạo trạng thái cuối; job đã dừng an toàn.",
                )
        except JobCancelledError:
            await self.job_repo.update_job(
                job_id,
                status="CANCELLED",
                progress=100,
                message="Job đã dừng theo yêu cầu của người vận hành.",
            )
        except asyncio.CancelledError:
            if not self._shutting_down:
                with contextlib.suppress(Exception):
                    await self.job_repo.update_job(
                        job_id,
                        status="CANCELLED",
                        progress=100,
                        message="Job đã bị hủy.",
                    )
            raise
        except Exception as exc:
            log_message(
                "Worker dừng do lỗi nội bộ.",
                level="ERROR",
                event="job.internal_error",
                error_type=type(exc).__name__,
            )
            with contextlib.suppress(Exception):
                await self.job_repo.update_job(
                    job_id,
                    status="FAILED",
                    progress=100,
                    message="Worker dừng an toàn do lỗi nội bộ.",
                )
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
            with log_context(
                job_id=job_id,
                job_type=job.get("job_type") if job else None,
                component="job_dispatcher",
            ):
                log_message(
                    "Worker kết thúc.",
                    event="job.finished",
                    duration_ms=round((monotonic() - started) * 1000, 2),
                )

    def status(self) -> dict:
        return {
            "active_jobs": sum(
                1 for task in self._tasks.values() if not task.done()
            ),
            "shutting_down": self._shutting_down,
        }

    def schedule_job(self, job_id: int) -> asyncio.Task:
        if self._shutting_down:
            raise RuntimeError("Dispatcher đang dừng; không nhận job mới.")
        existing = self._tasks.get(job_id)
        if existing and not existing.done():
            return existing
        task = asyncio.create_task(self._run_job(job_id), name=f"job-{job_id}")
        self._tasks[job_id] = task
        def discard_completed(_task: asyncio.Task) -> None:
            self._tasks.pop(job_id, None)

        task.add_done_callback(discard_completed)
        return task

    async def retry_job(self, source_job_id: int, schedule_callback=None) -> int:
        source = await self.job_repo.get_job_record(source_job_id)
        if not source:
            raise JobNotFoundError("Không tìm thấy job.")
        if source["status"] not in TERMINAL_STATUSES:
            raise JobStateError("Chỉ retry job đã kết thúc.")
        if not source["retryable"]:
            raise JobStateError(
                "Job này không được retry để tránh ghi lặp dữ liệu bên ngoài."
            )

        payload = deepcopy(source.get("request_payload") or {})
        if source["job_type"] == "GOOGLEFORM_SUBMIT":
            payload = await self._prepare_googleform_retry_payload(source, payload)
        new_job_id = await self.job_repo.create_job(
            source["job_type"],
            None,
            retryable=source["retryable"],
            recoverable=source["recoverable"],
            mutates_external=source["mutates_external"],
            retry_of_job_id=source_job_id,
            attempt=(source.get("attempt") or 1) + 1,
            message=(
                f"Retry lần {(source.get('attempt') or 1) + 1} "
                f"từ job #{source_job_id}."
            ),
        )
        try:
            cloned_upload_ids = await self.upload_store.clone_job_uploads(
                source_job_id,
                new_job_id,
            )
            if cloned_upload_ids:
                payload["upload_ids"] = cloned_upload_ids
            await self.job_repo.update_payload(new_job_id, payload)
            (schedule_callback or self.schedule_job)(new_job_id)
            return new_job_id
        except Exception:
            await self.job_repo.update_job(
                new_job_id,
                status="FAILED",
                progress=100,
                message="Không thể chuẩn bị retry do lỗi nội bộ.",
            )
            raise

    async def _prepare_googleform_retry_payload(
        self,
        source: dict,
        payload: dict,
    ) -> dict:
        """Build a fail-closed retry containing only proven safe failures."""
        if source.get("status") not in {"FAILED", "PARTIAL_SUCCESS"}:
            raise JobStateError(
                "Google Form chỉ được retry chọn lọc từ job FAILED "
                "hoặc PARTIAL_SUCCESS."
            )
        if (
            payload.get("execution_mode") != "dry-run"
            or source.get("mutates_external")
        ):
            raise JobStateError(
                "Không retry Google Form production để tránh gửi trùng dữ liệu."
            )

        students = payload.get("students")
        if not isinstance(students, list) or not students:
            raise JobStateError("Job Google Form không có danh sách học viên hợp lệ.")

        result_targets: dict[str, list[tuple[object, object]]] = defaultdict(list)
        result_data = source.get("results")
        if isinstance(result_data, str):
            try:
                result_data = json.loads(result_data)
            except (TypeError, json.JSONDecodeError):
                result_data = None
        if isinstance(result_data, dict):
            outcomes = result_data.get("outcomes")
            if isinstance(outcomes, list):
                for outcome in outcomes:
                    if not isinstance(outcome, dict):
                        continue
                    target_id = self._googleform_target_id(outcome)
                    if target_id:
                        result_targets[target_id].append(
                            (outcome.get("success"), outcome.get("outcome"))
                        )

        safe_result_targets = {
            target_id
            for target_id, states in result_targets.items()
            if states
            and all(
                success is False and outcome in {None, "FAILED"}
                for success, outcome in states
            )
        }

        audit_targets: dict[str, set[str]] = defaultdict(set)
        for audit in await self.audit_repo.list_for_job(source["id"], limit=200):
            if audit.get("action_type") != "GOOGLE_FORM_SUBMIT":
                continue
            target_id = str(audit.get("target_id") or "").strip()
            outcome = str(audit.get("outcome") or "").strip()
            if target_id and outcome:
                audit_targets[target_id].add(outcome)
        safe_audit_targets = {
            target_id
            for target_id, outcomes in audit_targets.items()
            if outcomes == {"FAILED"}
        }

        retry_students = []
        selected_targets = set()
        for student in students:
            if not isinstance(student, dict):
                continue
            target_id = self._googleform_target_id(student)
            if (
                target_id
                and target_id in safe_result_targets
                and target_id in safe_audit_targets
                and target_id not in selected_targets
            ):
                selected_targets.add(target_id)
                retry_students.append(deepcopy(student))

        if not retry_students:
            raise JobStateError(
                "Không có học viên outcome FAILED an toàn để retry; "
                "UNKNOWN hoặc trạng thái hủy phải được kiểm tra thủ công."
            )

        old_key = str(payload.get("idempotency_key") or "")
        new_key = secrets.token_urlsafe(24)
        if new_key == old_key:
            new_key = f"{new_key}-retry"
        payload["students"] = retry_students
        payload["idempotency_key"] = new_key
        payload["retry_source_job_id"] = source["id"]
        payload["retry_policy"] = "failed-only-v1"
        return payload

    @staticmethod
    def _googleform_target_id(student: dict) -> str:
        name = str(student.get("name") or "").strip()
        class_code = str(student.get("class_code") or "").strip().upper()
        if not name or not class_code:
            return ""
        return f"{name} | {class_code}"

    async def recover_jobs(self) -> list[int]:
        self._shutting_down = False
        self.worker_runtime.reset_locks()
        job_ids = await self.job_repo.prepare_recovery()
        for job_id in job_ids:
            self.schedule_job(job_id)
        return job_ids

    async def shutdown_jobs(self) -> None:
        self._shutting_down = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


_legacy_dispatcher = None


def get_legacy_dispatcher() -> JobDispatcher:
    global _legacy_dispatcher
    if _legacy_dispatcher is None:
        _legacy_dispatcher = JobDispatcher()
    return _legacy_dispatcher


def dispatcher_status() -> dict:
    return get_legacy_dispatcher().status()


def schedule_job(job_id: int) -> asyncio.Task:
    return get_legacy_dispatcher().schedule_job(job_id)


async def retry_job(source_job_id: int) -> int:
    return await get_legacy_dispatcher().retry_job(
        source_job_id,
        schedule_callback=schedule_job,
    )


async def recover_jobs() -> list[int]:
    return await get_legacy_dispatcher().recover_jobs()


async def shutdown_jobs() -> None:
    await get_legacy_dispatcher().shutdown_jobs()
