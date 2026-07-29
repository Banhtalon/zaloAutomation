import json
import secrets
from datetime import datetime, timedelta

from db.database import get_db_connection


class ConfirmationError(ValueError):
    pass


class LmsRepository:
    async def save_confirmation(
        self,
        analysis_job_id: int,
        confirmation_token: str,
        plan_digest: str,
        plan_payload: dict,
    ):
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload_json = json.dumps(plan_payload, ensure_ascii=False, sort_keys=True)
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO lms_confirmations (
                    analysis_job_id, confirmation_token, plan_digest,
                    plan_payload, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (analysis_job_id, confirmation_token, plan_digest, payload_json, created_at),
            )
            await db.commit()

    async def consume_confirmation(
        self,
        analysis_job_id: int,
        token: str,
        *,
        dry_run: bool | None = None,
        max_age_minutes: int = 30,
    ):
        """Atomically consume a plan and create exactly one execution job."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    """
                    SELECT confirmation_token, plan_digest, plan_payload,
                           consumed_at, execution_job_id, created_at
                    FROM lms_confirmations
                    WHERE analysis_job_id = ?
                    """,
                    (analysis_job_id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if not row:
                    raise ConfirmationError("Không tìm thấy kế hoạch LMS để xác nhận.")
                if not secrets.compare_digest(row[0], token):
                    raise ConfirmationError("Token xác nhận LMS không hợp lệ.")
                if row[3] is not None or row[4] is not None:
                    raise ConfirmationError("Kế hoạch LMS này đã được xác nhận trước đó.")
                try:
                    created_at = datetime.strptime(row[5], "%Y-%m-%d %H:%M:%S")
                except (TypeError, ValueError) as exc:
                    raise ConfirmationError("Thời điểm tạo kế hoạch LMS không hợp lệ.") from exc
                if datetime.now() - created_at > timedelta(minutes=max_age_minutes):
                    raise ConfirmationError("Kế hoạch LMS đã hết hạn; hãy phân tích lại ảnh.")

                plan_payload = json.loads(row[2])
                plan_payload.pop("confirmation_token", None)
                plan_dry_run = bool(plan_payload.get("dry_run", True))
                if dry_run is not None and bool(dry_run) != plan_dry_run:
                    raise ConfirmationError(
                        "Chế độ BCNB đã thay đổi sau khi lập kế hoạch; hãy phân tích lại ảnh."
                    )
                execution_mode = "dry-run" if plan_dry_run else "production"
                request_payload = json.dumps(
                    {
                        "version": 3,
                        "plan": plan_payload,
                        "expected_digest": row[1],
                        "execution_mode": execution_mode,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                cursor = await db.execute(
                    """
                    INSERT INTO scan_jobs (
                        job_type, status, created_at, progress, message,
                        retryable, recoverable, mutates_external,
                        cancel_requested, attempt, request_payload
                    ) VALUES ('LMS_AUTOMATION', 'PENDING', ?, 0, ?, 0, ?, ?, 0, 1, ?)
                    """,
                    (
                        now,
                        "Đang chờ quyền sử dụng trình duyệt...",
                        int(plan_dry_run),
                        int(not plan_dry_run),
                        request_payload,
                    ),
                )
                execution_job_id = cursor.lastrowid
                await db.execute(
                    """
                    INSERT INTO job_events (
                        job_id, event_type, old_status, new_status,
                        message, details_json, created_at
                    ) VALUES (?, 'CREATED', NULL, 'PENDING', ?, ?, ?)
                    """,
                    (
                        execution_job_id,
                        "Đã xác nhận kế hoạch LMS; chờ worker thực hiện.",
                        json.dumps(
                            {
                                "analysis_job_id": analysis_job_id,
                                "dry_run": plan_dry_run,
                                "execution_mode": execution_mode,
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                await db.execute(
                    """
                    UPDATE lms_confirmations
                    SET consumed_at = ?, execution_job_id = ?
                    WHERE analysis_job_id = ? AND consumed_at IS NULL
                    """,
                    (now, execution_job_id, analysis_job_id),
                )
                await db.commit()
                return {
                    "job_id": execution_job_id,
                    "plan_digest": row[1],
                    "plan": plan_payload,
                    "dry_run": plan_dry_run,
                }
            except Exception:
                await db.rollback()
                raise
