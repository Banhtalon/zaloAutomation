import json
import secrets
from datetime import datetime, timedelta
from typing import NoReturn

from core.time_utils import now_utc, parse_timestamp, utc_iso
from db.database import get_db_connection
from googleform_autosubmit.config import Config
from services.lms_plan import (
    LMS_PLAN_ACTION_SET,
    LMS_PLAN_KIND,
    LMS_PLAN_TARGET,
    LMS_PLAN_VERSION,
    compute_plan_digest,
)


class ConfirmationError(ValueError):
    def __init__(self, message: str, *, code: str = "LMS_CONFIRMATION_INVALID"):
        super().__init__(message)
        self.code = code


def _utc_now() -> datetime:
    return now_utc()


def _format_datetime(value: datetime) -> str:
    return utc_iso(value)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return parse_timestamp(value)


class LmsRepository:
    async def save_confirmation(
        self,
        analysis_job_id: int,
        confirmation_token: str,
        plan_digest: str,
        plan_payload: dict,
        *,
        ttl_minutes: int | None = None,
    ):
        if not isinstance(plan_payload, dict):
            raise ConfirmationError(
                "Kế hoạch LMS không hợp lệ.", code="LMS_PLAN_INVALID"
            )
        payload = dict(plan_payload)
        payload.pop("confirmation_token", None)
        computed_digest = compute_plan_digest(payload)
        if not secrets.compare_digest(str(plan_digest), computed_digest):
            raise ConfirmationError(
                "Digest kế hoạch LMS không khớp.", code="LMS_PLAN_INVALID"
            )
        ttl = Config.LMS_CONFIRMATION_TTL_MINUTES if ttl_minutes is None else ttl_minutes
        if int(ttl) <= 0:
            raise ConfirmationError(
                "Thời hạn xác nhận LMS không hợp lệ.",
                code="LMS_CONFIRMATION_CONFIG_INVALID",
            )
        now_dt = _utc_now()
        created_at = _format_datetime(now_dt)
        expires_at = _format_datetime(now_dt + timedelta(minutes=int(ttl)))
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO lms_confirmations (
                    analysis_job_id, confirmation_token, plan_digest,
                    plan_payload, created_at, expires_at,
                    invalidated_at, invalidated_reason
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    analysis_job_id,
                    confirmation_token,
                    plan_digest,
                    payload_json,
                    created_at,
                    expires_at,
                ),
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
        """Atomically consume a valid plan and create exactly one execution job."""
        now_dt = _utc_now()
        now = _format_datetime(now_dt)

        async def reject_and_invalidate(db, *, code: str, message: str) -> NoReturn:
            await db.execute(
                """
                UPDATE lms_confirmations
                SET invalidated_at = COALESCE(invalidated_at, ?),
                    invalidated_reason = COALESCE(invalidated_reason, ?)
                WHERE analysis_job_id = ?
                """,
                (now, f"{code}: {message}", analysis_job_id),
            )
            await db.commit()
            raise ConfirmationError(message, code=code)

        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    """
                    SELECT confirmation_token, plan_digest, plan_payload,
                           consumed_at, execution_job_id, created_at,
                           expires_at, invalidated_at, invalidated_reason
                    FROM lms_confirmations
                    WHERE analysis_job_id = ?
                    """,
                    (analysis_job_id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if not row:
                    raise ConfirmationError(
                        "Không tìm thấy kế hoạch LMS để xác nhận.",
                        code="LMS_CONFIRMATION_NOT_FOUND",
                    )
                if not secrets.compare_digest(row[0], token):
                    raise ConfirmationError(
                        "Token xác nhận LMS không hợp lệ.",
                        code="LMS_CONFIRMATION_TOKEN_INVALID",
                    )
                if row[7] is not None:
                    raise ConfirmationError(
                        "Xác nhận LMS đã bị hủy; hãy phân tích lại ảnh.",
                        code="LMS_CONFIRMATION_REVOKED",
                    )
                if row[3] is not None or row[4] is not None:
                    raise ConfirmationError(
                        "Kế hoạch LMS này đã được xác nhận trước đó.",
                        code="LMS_CONFIRMATION_REPLAY",
                    )

                expires_at = _parse_datetime(row[6])
                if expires_at is None:
                    created_at = _parse_datetime(row[5])
                    if created_at is None:
                        await reject_and_invalidate(
                            db,
                            code="LMS_CONFIRMATION_INVALID",
                            message="Thời điểm tạo kế hoạch LMS không hợp lệ.",
                        )
                    if max_age_minutes <= 0 or now_dt - created_at > timedelta(
                        minutes=max_age_minutes
                    ):
                        await reject_and_invalidate(
                            db,
                            code="LMS_CONFIRMATION_EXPIRED",
                            message="Kế hoạch LMS đã hết hạn; hãy phân tích lại ảnh.",
                        )
                elif now_dt > expires_at:
                    await reject_and_invalidate(
                        db,
                        code="LMS_CONFIRMATION_EXPIRED",
                        message="Kế hoạch LMS đã hết hạn; hãy phân tích lại ảnh.",
                    )

                try:
                    plan_payload = json.loads(row[2])
                except (TypeError, json.JSONDecodeError):
                    await reject_and_invalidate(
                        db,
                        code="LMS_PLAN_INVALID",
                        message="Nội dung kế hoạch LMS không đọc được.",
                    )
                if not isinstance(plan_payload, dict):
                    await reject_and_invalidate(
                        db,
                        code="LMS_PLAN_INVALID",
                        message="Nội dung kế hoạch LMS không hợp lệ.",
                    )
                plan_payload.pop("confirmation_token", None)
                if plan_payload.get("plan_version") != LMS_PLAN_VERSION:
                    await reject_and_invalidate(
                        db,
                        code="LMS_PLAN_VERSION_MISMATCH",
                        message="Phiên bản kế hoạch LMS đã cũ; hãy phân tích lại ảnh.",
                    )
                if plan_payload.get("kind") != LMS_PLAN_KIND:
                    await reject_and_invalidate(
                        db,
                        code="LMS_PLAN_INVALID",
                        message="Loại kế hoạch LMS không hợp lệ.",
                    )
                if plan_payload.get("target") != LMS_PLAN_TARGET or plan_payload.get(
                    "action_set"
                ) != list(LMS_PLAN_ACTION_SET):
                    await reject_and_invalidate(
                        db,
                        code="LMS_PLAN_TARGET_INVALID",
                        message="Mục tiêu hoặc tập thao tác LMS không hợp lệ.",
                    )
                if not isinstance(plan_payload.get("week"), int) or not isinstance(
                    plan_payload.get("year"), int
                ):
                    await reject_and_invalidate(
                        db,
                        code="LMS_PLAN_TARGET_INVALID",
                        message="Tuần hoặc năm BCNB trong kế hoạch không hợp lệ.",
                    )
                try:
                    computed_digest = compute_plan_digest(plan_payload)
                except (TypeError, ValueError):
                    await reject_and_invalidate(
                        db,
                        code="LMS_PLAN_DIGEST_MISMATCH",
                        message="Không thể kiểm tra toàn vẹn kế hoạch LMS.",
                    )
                if not secrets.compare_digest(row[1], computed_digest):
                    await reject_and_invalidate(
                        db,
                        code="LMS_PLAN_DIGEST_MISMATCH",
                        message="Kế hoạch LMS đã bị thay đổi; hãy phân tích lại ảnh.",
                    )

                plan_dry_run = bool(plan_payload.get("dry_run", True))
                if dry_run is not None and bool(dry_run) != plan_dry_run:
                    await reject_and_invalidate(
                        db,
                        code="LMS_CONFIRMATION_MODE_MISMATCH",
                        message=(
                            "Chế độ BCNB đã thay đổi sau khi lập kế hoạch; "
                            "hãy phân tích lại ảnh."
                        ),
                    )
                if not plan_payload.get("can_confirm"):
                    await reject_and_invalidate(
                        db,
                        code="LMS_PLAN_INVALID",
                        message="Kế hoạch LMS không được phép xác nhận.",
                    )

                execution_mode = "dry-run" if plan_dry_run else "production"
                request_payload = json.dumps(
                    {
                        "version": LMS_PLAN_VERSION,
                        "plan": plan_payload,
                        "expected_digest": row[1],
                        "execution_mode": execution_mode,
                        "target": plan_payload["target"],
                        "week": plan_payload["week"],
                        "year": plan_payload["year"],
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
                                "target": plan_payload["target"],
                                "week": plan_payload["week"],
                                "year": plan_payload["year"],
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                update_cursor = await db.execute(
                    """
                    UPDATE lms_confirmations
                    SET consumed_at = ?, execution_job_id = ?
                    WHERE analysis_job_id = ?
                      AND consumed_at IS NULL
                      AND invalidated_at IS NULL
                    """,
                    (now, execution_job_id, analysis_job_id),
                )
                if update_cursor.rowcount != 1:
                    raise ConfirmationError(
                        "Kế hoạch LMS đã được xác nhận trước đó.",
                        code="LMS_CONFIRMATION_REPLAY",
                    )
                await db.commit()
                return {
                    "job_id": execution_job_id,
                    "plan_digest": row[1],
                    "plan": plan_payload,
                    "dry_run": plan_dry_run,
                    "execution_mode": execution_mode,
                }
            except Exception:
                await db.rollback()
                raise
