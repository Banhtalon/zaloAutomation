import json
from datetime import timedelta
import re
from typing import Any

from core.time_utils import now_utc, parse_timestamp, utc_iso
from db.database import get_db_connection
from googleform_autosubmit.config import Config


TERMINAL_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED", "CANCELLED"}


class JobStateError(ValueError):
    pass


class JobQueueFullError(JobStateError):
    """Raised when the bounded in-memory admission queue is full."""

    def __init__(self, message: str, *, limit: int, position: int | None = None):
        super().__init__(message)
        self.limit = limit
        self.position = position


class IdempotencyConflictError(JobStateError):
    """A key was already bound to a different intent."""


class IdempotencyKeyExpiredError(JobStateError):
    """A terminal/old key cannot be reused for a new intent."""


class JobNotFoundError(JobStateError):
    pass


class JobCancelledError(RuntimeError):
    pass


def utc_now() -> str:
    return utc_iso()


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_json(value: str | None, fallback=None):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class JobRepository:
    _columns = """
        id, job_type, status, created_at, started_at, finished_at,
        heartbeat_at, progress, message, results, cancel_requested,
        attempt, retry_of_job_id, retryable, recoverable,
        mutates_external, request_payload, recovery_note,
        lease_owner, lease_expires_at, deadline_at, queued_at, queue_position
    """

    async def create_job(
        self,
        job_type: str,
        request_payload: dict | None = None,
        *,
        retryable: bool = False,
        recoverable: bool = False,
        mutates_external: bool = False,
        retry_of_job_id: int | None = None,
        attempt: int = 1,
        message: str = "Khởi tạo job...",
    ) -> int:
        created_at = utc_now()
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                INSERT INTO scan_jobs (
                    job_type, status, created_at, progress, message,
                    cancel_requested, attempt, retry_of_job_id, retryable,
                    recoverable, mutates_external, request_payload, queued_at
                ) VALUES (?, 'PENDING', ?, 0, ?, 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_type,
                    created_at,
                    message,
                    max(1, attempt),
                    retry_of_job_id,
                    int(retryable),
                    int(recoverable),
                    int(mutates_external),
                    _json(request_payload),
                    created_at,
                ),
            )
            job_id = cursor.lastrowid
            await self._insert_event(
                db,
                job_id,
                "CREATED",
                None,
                "PENDING",
                message,
                {"attempt": max(1, attempt), "retry_of_job_id": retry_of_job_id},
            )
            await db.commit()
            return job_id

    async def create_idempotent_job(
        self,
        job_type: str,
        request_payload: dict | None,
        *,
        key_digest: str,
        operation: str,
        payload_hash: str,
        actor_key: str,
        target_hash: str,
        execution_mode: str,
        ttl_hours: int,
        retryable: bool = False,
        recoverable: bool = False,
        mutates_external: bool = False,
        message: str = "Khởi tạo job...",
    ) -> dict[str, Any]:
        """Claim a Google Form intent and create its job atomically.

        ``BEGIN IMMEDIATE`` serializes concurrent retries.  A replay returns
        the original job without inserting or enqueueing another job; a
        changed binding fails closed with a conflict.
        """

        created_at = utc_now()
        expires_at = utc_iso(now_utc() + timedelta(hours=max(1, int(ttl_hours))))
        binding = (operation, payload_hash, actor_key, target_hash, execution_mode)
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """
                SELECT operation, payload_hash, actor_key, target_hash,
                       execution_mode, job_id, status, expires_at
                FROM googleform_idempotency
                WHERE key_digest = ?
                """,
                (key_digest,),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing:
                existing_binding = tuple(str(value) for value in existing[:5])
                expiry = parse_timestamp(existing[7])
                if expiry is None or now_utc() >= expiry:
                    await db.rollback()
                    raise IdempotencyKeyExpiredError(
                        "Idempotency key đã hết hạn; hãy tạo intent mới."
                    )
                if existing_binding != binding:
                    await db.rollback()
                    raise IdempotencyConflictError(
                        "Idempotency key đã được gắn với payload/actor/target/mode khác."
                    )
                await db.rollback()
                return {
                    "job_id": int(existing[5]),
                    "created": False,
                    "status": str(existing[6]),
                }

            cursor = await db.execute(
                """
                INSERT INTO scan_jobs (
                    job_type, status, created_at, progress, message,
                    cancel_requested, attempt, retry_of_job_id, retryable,
                    recoverable, mutates_external, request_payload, queued_at
                ) VALUES (?, 'PENDING', ?, 0, ?, 0, 1, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    job_type,
                    created_at,
                    message,
                    int(retryable),
                    int(recoverable),
                    int(mutates_external),
                    _json(request_payload),
                    created_at,
                ),
            )
            job_id = int(cursor.lastrowid)
            await self._insert_event(
                db,
                job_id,
                "CREATED",
                None,
                "PENDING",
                message,
                {"attempt": 1, "idempotency_key_sha256": key_digest},
            )
            await db.execute(
                """
                INSERT INTO googleform_idempotency(
                    key_digest, operation, payload_hash, actor_key, target_hash,
                    execution_mode, job_id, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    key_digest,
                    operation,
                    payload_hash,
                    actor_key,
                    target_hash,
                    execution_mode,
                    job_id,
                    created_at,
                    expires_at,
                ),
            )
            await db.commit()
            return {"job_id": job_id, "created": True, "status": "PENDING"}

    async def _insert_event(
        self,
        db,
        job_id: int,
        event_type: str,
        old_status: str | None,
        new_status: str | None,
        message: str | None,
        details: dict | None = None,
    ) -> None:
        await db.execute(
            """
            INSERT INTO job_events (
                job_id, event_type, old_status, new_status,
                message, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                event_type,
                old_status,
                new_status,
                message,
                _json(details),
                utc_now(),
            ),
        )

    async def add_event(
        self,
        job_id: int,
        event_type: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        async with get_db_connection() as db:
            await self._insert_event(
                db,
                job_id,
                event_type,
                None,
                None,
                message,
                details,
            )
            await db.commit()

    async def update_job(
        self,
        job_id: int,
        status: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        results: str | None = None,
    ) -> bool:
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT status, cancel_requested FROM scan_jobs WHERE id = ?", (job_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                await db.rollback()
                raise JobNotFoundError("Không tìm thấy job.")

            old_status, cancel_requested = row
            new_status = status or old_status
            if old_status in TERMINAL_STATUSES and new_status != old_status:
                await db.rollback()
                return False
            if old_status in TERMINAL_STATUSES and status is None:
                await db.rollback()
                return False
            if old_status == "RUNNING" and new_status == "PENDING":
                await db.rollback()
                return False
            if old_status == "PENDING" and new_status in {"SUCCESS", "PARTIAL_SUCCESS"}:
                await db.rollback()
                return False
            if cancel_requested and new_status in {"SUCCESS", "FAILED"}:
                new_status = "CANCELLED"
                message = message or "Job đã dừng theo yêu cầu."

            updates = []
            params: list[Any] = []
            if status is not None:
                updates.append("status = ?")
                params.append(new_status)
                if new_status in TERMINAL_STATUSES:
                    updates.extend(
                        [
                            "finished_at = ?",
                            "heartbeat_at = NULL",
                            "lease_owner = NULL",
                            "lease_expires_at = NULL",
                            "deadline_at = NULL",
                            "queue_position = NULL",
                        ]
                    )
                    params.append(utc_now())
            if progress is not None:
                updates.append("progress = ?")
                params.append(max(0, min(100, int(progress))))
            if message is not None:
                updates.append("message = ?")
                params.append(message)
            if results is not None:
                updates.append("results = ?")
                params.append(results)
            if not updates:
                await db.rollback()
                return False

            params.append(job_id)
            await db.execute(
                f"UPDATE scan_jobs SET {', '.join(updates)} WHERE id = ?", tuple(params)
            )
            if status is not None and (new_status != old_status or message):
                await self._insert_event(
                    db,
                    job_id,
                    "STATUS_CHANGED" if new_status != old_status else "PROGRESS",
                    old_status,
                    new_status,
                    message,
                    {"progress": progress} if progress is not None else None,
                )
            elif message is not None:
                await self._insert_event(
                    db,
                    job_id,
                    "PROGRESS",
                    old_status,
                    old_status,
                    message,
                    {"progress": progress} if progress is not None else None,
                )
            if status is not None and new_status in TERMINAL_STATUSES:
                await db.execute(
                    """
                    UPDATE googleform_idempotency
                    SET status = ?, completed_at = COALESCE(completed_at, ?)
                    WHERE job_id = ?
                    """,
                    (new_status, utc_now(), job_id),
                )
            await db.commit()
            return True

    async def update_payload(self, job_id: int, request_payload: dict) -> None:
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE scan_jobs SET request_payload = ? WHERE id = ? AND status = 'PENDING'",
                (_json(request_payload), job_id),
            )
            await db.commit()

    async def mark_running(self, job_id: int) -> bool:
        now = utc_now()
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT status, cancel_requested FROM scan_jobs WHERE id = ?", (job_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if not row or row[0] != "PENDING" or row[1]:
                await db.rollback()
                return False
            cursor = await db.execute(
                """
                UPDATE scan_jobs
                SET status = 'RUNNING', started_at = COALESCE(started_at, ?),
                    heartbeat_at = ?, message = COALESCE(message, 'Đang chạy...')
                WHERE id = ? AND status = 'PENDING' AND cancel_requested = 0
                """,
                (now, now, job_id),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return False
            await self._insert_event(
                db, job_id, "STARTED", "PENDING", "RUNNING", "Worker đã nhận job."
            )
            await db.commit()
            return True

    async def claim_lease(
        self,
        job_id: int,
        owner_id: str,
        lease_seconds: int,
        *,
        deadline_seconds: int | None = None,
    ) -> bool:
        """Atomically move a pending job to RUNNING with an owner lease."""
        now = utc_now()
        current = now_utc()
        lease_expires = current + timedelta(seconds=max(1, int(lease_seconds)))
        deadline = current + timedelta(
            seconds=max(1, int(deadline_seconds or Config.JOB_TIMEOUT_SECONDS))
        )
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT status, cancel_requested FROM scan_jobs WHERE id = ?",
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if not row or row[0] != "PENDING" or row[1]:
                await db.rollback()
                return False
            cursor = await db.execute(
                """
                UPDATE scan_jobs
                SET status = 'RUNNING', started_at = COALESCE(started_at, ?),
                    heartbeat_at = ?, lease_owner = ?, lease_expires_at = ?,
                    deadline_at = ?, queue_position = NULL
                WHERE id = ? AND status = 'PENDING' AND cancel_requested = 0
                """,
                (
                    now,
                    now,
                    owner_id,
                    utc_iso(lease_expires),
                    utc_iso(deadline),
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return False
            await self._insert_event(
                db,
                job_id,
                "LEASE_CLAIMED",
                "PENDING",
                "RUNNING",
                "Worker đã nhận lease cho job.",
                {"owner_id": owner_id},
            )
            await db.commit()
            return True

    async def renew_lease(
        self,
        job_id: int,
        owner_id: str,
        lease_seconds: int,
    ) -> bool:
        expires = now_utc() + timedelta(
            seconds=max(1, int(lease_seconds))
        )
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                UPDATE scan_jobs
                SET heartbeat_at = ?, lease_expires_at = ?
                WHERE id = ? AND status = 'RUNNING' AND lease_owner = ?
                """,
                (
                    utc_now(),
                    utc_iso(expires),
                    job_id,
                    owner_id,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def set_queue_metadata(
        self,
        job_id: int,
        position: int,
        *,
        queued_at: str | None = None,
    ) -> bool:
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                UPDATE scan_jobs
                SET queue_position = ?, queued_at = COALESCE(queued_at, ?)
                WHERE id = ? AND status = 'PENDING'
                """,
                (max(0, int(position)), queued_at or utc_now(), job_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def heartbeat(self, job_id: int) -> bool:
        async with get_db_connection() as db:
            cursor = await db.execute(
                "UPDATE scan_jobs SET heartbeat_at = ? WHERE id = ? AND status = 'RUNNING'",
                (utc_now(), job_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def request_cancel(self, job_id: int) -> dict:
        now = utc_now()
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT status, cancel_requested FROM scan_jobs WHERE id = ?", (job_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                await db.rollback()
                raise JobNotFoundError("Không tìm thấy job.")
            status, already_requested = row
            if status in TERMINAL_STATUSES:
                await db.rollback()
                return {"status": status, "cancel_requested": bool(already_requested)}

            if status == "PENDING":
                await db.execute(
                    """
                    UPDATE scan_jobs SET cancel_requested = 1, status = 'CANCELLED',
                        finished_at = ?, progress = 100,
                        lease_owner = NULL, lease_expires_at = NULL,
                        deadline_at = NULL, queue_position = NULL,
                        message = 'Job đã được hủy trước khi bắt đầu.'
                    WHERE id = ?
                    """,
                    (now, job_id),
                )
                new_status = "CANCELLED"
            else:
                await db.execute(
                    """
                    UPDATE scan_jobs SET cancel_requested = 1,
                        message = 'Đã nhận yêu cầu hủy; sẽ dừng tại điểm an toàn kế tiếp.'
                    WHERE id = ?
                    """,
                    (job_id,),
                )
                new_status = status
            await self._insert_event(
                db,
                job_id,
                "CANCEL_REQUESTED",
                status,
                new_status,
                "Người vận hành yêu cầu hủy job.",
            )
            await db.commit()
            return {"status": new_status, "cancel_requested": True}

    async def is_cancel_requested(self, job_id: int) -> bool:
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT cancel_requested, status FROM scan_jobs WHERE id = ?", (job_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return bool(row and (row[0] or row[1] == "CANCELLED"))

    async def ensure_not_cancelled(self, job_id: int) -> None:
        if await self.is_cancel_requested(job_id):
            raise JobCancelledError("Job đã dừng theo yêu cầu của người vận hành.")

    async def get_job(self, job_id: int) -> dict | None:
        async with get_db_connection() as db:
            async with db.execute(
                f"SELECT {self._columns} FROM scan_jobs WHERE id = ?", (job_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return self._row_to_job(row, include_payload=False) if row else None

    async def get_job_record(self, job_id: int) -> dict | None:
        async with get_db_connection() as db:
            async with db.execute(
                f"SELECT {self._columns} FROM scan_jobs WHERE id = ?", (job_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return self._row_to_job(row, include_payload=True) if row else None

    async def get_latest_job(self, active_only: bool = False) -> dict | None:
        where = "WHERE status IN ('PENDING', 'RUNNING')" if active_only else ""
        async with get_db_connection() as db:
            async with db.execute(
                f"SELECT {self._columns} FROM scan_jobs {where} ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
        return self._row_to_job(row, include_payload=False) if row else None

    def _row_to_job(self, row, *, include_payload: bool) -> dict:
        heartbeat_age = None
        if row[6]:
            try:
                heartbeat = parse_timestamp(row[6])
                if heartbeat is None:
                    raise ValueError("invalid heartbeat timestamp")
                heartbeat_age = max(
                    0, int((now_utc() - heartbeat).total_seconds())
                )
            except (TypeError, ValueError):
                heartbeat_age = None
        deadline_expired = False
        if row[20]:
            try:
                deadline = parse_timestamp(row[20])
                deadline_expired = deadline is not None and now_utc() >= deadline
            except (TypeError, ValueError):
                deadline_expired = False
        job = {
            "id": row[0],
            "job_type": row[1],
            "status": row[2],
            "created_at": row[3],
            "started_at": row[4],
            "finished_at": row[5],
            "heartbeat_at": row[6],
            "progress": row[7] or 0,
            "message": row[8],
            "results": row[9],
            "cancel_requested": bool(row[10]),
            "attempt": row[11] or 1,
            "retry_of_job_id": row[12],
            "retryable": bool(row[13]),
            "recoverable": bool(row[14]),
            "mutates_external": bool(row[15]),
            "recovery_note": row[17],
            "lease_owner": row[18],
            "lease_expires_at": row[19],
            "deadline_at": row[20],
            "queued_at": row[21],
            "queue_position": row[22],
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat_stale": row[2] == "RUNNING" and (
                heartbeat_age is None or heartbeat_age > Config.HEALTH_STALE_SECONDS
            ),
            "deadline_expired": deadline_expired,
            "cancel_allowed": row[2] in {"PENDING", "RUNNING"} and not bool(row[10]),
        }
        payload = _parse_json(row[16], {})
        if row[1] == "ZALO_SCAN":
            result_payload = _parse_json(row[9], {})
            source = result_payload if isinstance(result_payload, dict) else payload
            phase = source.get("phase") if isinstance(source, dict) else None
            reason_code = (
                source.get("reason_code") if isinstance(source, dict) else None
            )
            attempted = (
                source.get("attempted_target_count")
                if isinstance(source, dict)
                else None
            )
            job["phase"] = phase if phase == "ZALO_SCAN" else "ZALO_SCAN"
            job["reason_code"] = (
                reason_code
                if isinstance(reason_code, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", reason_code)
                else "UNKNOWN_FAIL_CLOSED"
            )
            job["attempted_target_count"] = (
                max(0, int(attempted))
                if isinstance(attempted, int) and not isinstance(attempted, bool)
                else 0
            )
        if include_payload:
            job["request_payload"] = payload
        return job

    async def list_events(self, job_id: int, limit: int = 100) -> list[dict]:
        limit = max(1, min(200, limit))
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, event_type, old_status, new_status, message,
                       details_json, created_at
                FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT ?
                """,
                (job_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "event_type": row[1],
                "old_status": row[2],
                "new_status": row[3],
                "message": row[4],
                "details": _parse_json(row[5], {}),
                "created_at": row[6],
            }
            for row in rows
        ]

    async def prepare_recovery(self) -> list[int]:
        """Recover safe work and quarantine interrupted external mutations."""
        now = utc_now()
        to_schedule: list[int] = []
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """
                SELECT DISTINCT jobs.id
                FROM scan_jobs AS jobs
                JOIN zalo_batch_targets AS targets ON targets.job_id = jobs.id
                WHERE jobs.job_type = 'ZALO_SCAN'
                  AND jobs.status IN ('FAILED', 'PARTIAL_SUCCESS', 'CANCELLED')
                  AND targets.write_status = 'OUTCOME_UNKNOWN'
                """
            ) as cursor:
                reconciliation_jobs = [int(row[0]) for row in await cursor.fetchall()]
            for reconciliation_job_id in reconciliation_jobs:
                await db.execute(
                    """
                    UPDATE scan_jobs
                    SET status = 'PENDING', finished_at = NULL, progress = 0,
                        recovery_note = 'Unknown target requires automatic readback',
                        message = 'Đang chờ reconciliation BCNB an toàn.'
                    WHERE id = ?
                    """,
                    (reconciliation_job_id,),
                )
                await self._insert_event(
                    db,
                    reconciliation_job_id,
                    "RECOVERY_READBACK_QUEUED",
                    "FAILED",
                    "PENDING",
                    "Target OUTCOME_UNKNOWN được xếp lại chỉ để readback.",
                )
            async with db.execute(
                """
                SELECT id, job_type, status, recoverable, mutates_external,
                       request_payload
                FROM scan_jobs WHERE status IN ('PENDING', 'RUNNING')
                ORDER BY id
                """
            ) as cursor:
                rows = await cursor.fetchall()

            for job_id, job_type, status, recoverable, mutates_external, payload in rows:
                has_payload = bool(_parse_json(payload, None))
                durable_zalo_targets = False
                if status == "RUNNING" and job_type == "ZALO_SCAN" and has_payload:
                    async with db.execute(
                        "SELECT COUNT(*) FROM zalo_batch_targets WHERE job_id = ?",
                        (job_id,),
                    ) as target_cursor:
                        target_row = await target_cursor.fetchone()
                    durable_zalo_targets = bool(target_row and int(target_row[0]) > 0)
                if status == "PENDING" and has_payload:
                    to_schedule.append(job_id)
                    await self._insert_event(
                        db,
                        job_id,
                        "RECOVERY_QUEUED",
                        "PENDING",
                        "PENDING",
                        "Job chờ đã được xếp lại sau khi ứng dụng khởi động.",
                    )
                elif status == "RUNNING" and durable_zalo_targets:
                    await db.execute(
                        """
                        UPDATE scan_jobs SET status = 'PENDING', started_at = NULL,
                            heartbeat_at = NULL, lease_owner = NULL,
                            lease_expires_at = NULL, deadline_at = NULL,
                            queue_position = NULL, recovery_note = ?,
                            message = 'Khôi phục batch Zalo theo target; write dở sẽ readback trước retry.'
                        WHERE id = ? AND status = 'RUNNING'
                        """,
                        ("Target-aware Zalo reconciliation required", job_id),
                    )
                    to_schedule.append(job_id)
                    await self._insert_event(
                        db,
                        job_id,
                        "RECOVERY_RECONCILIATION_QUEUED",
                        "RUNNING",
                        "PENDING",
                        "Batch có target bền vững; worker phải readback target write dở.",
                    )
                elif status == "RUNNING" and recoverable and not mutates_external and has_payload:
                    await db.execute(
                        """
                        UPDATE scan_jobs SET status = 'PENDING', started_at = NULL,
                            heartbeat_at = NULL, lease_owner = NULL,
                            lease_expires_at = NULL, deadline_at = NULL,
                            queue_position = NULL, recovery_note = ?,
                            message = 'Khôi phục tác vụ đọc an toàn sau khi ứng dụng khởi động lại.'
                        WHERE id = ? AND status = 'RUNNING'
                        """,
                        ("Recovered from interrupted read-only run", job_id),
                    )
                    to_schedule.append(job_id)
                    await self._insert_event(
                        db,
                        job_id,
                        "RECOVERED",
                        "RUNNING",
                        "PENDING",
                        "Tác vụ đọc được phép chạy lại an toàn.",
                    )
                else:
                    note = (
                        "External mutation may be incomplete; review audit before retry."
                        if mutates_external
                        else "Job had no durable payload and could not be recovered."
                    )
                    await db.execute(
                        """
                        UPDATE scan_jobs SET status = 'FAILED', finished_at = ?,
                            heartbeat_at = NULL, lease_owner = NULL,
                            lease_expires_at = NULL, deadline_at = NULL,
                            queue_position = NULL, recovery_note = ?, progress = 100,
                            message = ?
                        WHERE id = ? AND status IN ('PENDING', 'RUNNING')
                        """,
                        (
                            now,
                            note,
                            "Job bị gián đoạn; đã dừng an toàn và cần kiểm tra nhật ký.",
                            job_id,
                        ),
                    )
                    await self._insert_event(
                        db,
                        job_id,
                        "RECOVERY_BLOCKED",
                        status,
                        "FAILED",
                        note,
                    )
            await db.commit()
        return to_schedule

    async def reconcile_shutdown(self, job_ids: list[int]) -> None:
        """Leave no RUNNING rows behind when a worker is shutting down."""
        if not job_ids:
            return
        now = utc_now()
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in job_ids)
            async with db.execute(
                f"""
                SELECT id, job_type, status, recoverable, mutates_external
                FROM scan_jobs
                WHERE id IN ({placeholders}) AND status IN ('PENDING', 'RUNNING')
                """,
                tuple(job_ids),
            ) as cursor:
                rows = await cursor.fetchall()
            for job_id, job_type, status, recoverable, mutates_external in rows:
                if status == "PENDING":
                    await db.execute(
                        "UPDATE scan_jobs SET queue_position = NULL WHERE id = ?",
                        (job_id,),
                    )
                    continue
                durable_zalo_targets = False
                if job_type == "ZALO_SCAN":
                    async with db.execute(
                        "SELECT COUNT(*) FROM zalo_batch_targets WHERE job_id = ?",
                        (job_id,),
                    ) as target_cursor:
                        target_row = await target_cursor.fetchone()
                    durable_zalo_targets = bool(target_row and int(target_row[0]) > 0)
                if (recoverable and not mutates_external) or durable_zalo_targets:
                    await db.execute(
                        """
                        UPDATE scan_jobs
                        SET status = 'PENDING', started_at = NULL,
                            heartbeat_at = NULL, lease_owner = NULL,
                            lease_expires_at = NULL, deadline_at = NULL,
                            queue_position = NULL, recovery_note = ?,
                            message = 'Worker đã đóng an toàn; job sẵn sàng chạy lại.'
                        WHERE id = ? AND status = 'RUNNING'
                        """,
                        (
                            "Target-aware reconciliation after graceful shutdown"
                            if durable_zalo_targets
                            else "Requeued after graceful worker shutdown",
                            job_id,
                        ),
                    )
                    await self._insert_event(
                        db,
                        job_id,
                        "SHUTDOWN_REQUEUED",
                        "RUNNING",
                        "PENDING",
                        "Job đọc được xếp lại sau khi worker dừng.",
                    )
                else:
                    await db.execute(
                        """
                        UPDATE scan_jobs
                        SET status = 'FAILED', finished_at = ?, progress = 100,
                            heartbeat_at = NULL, lease_owner = NULL,
                            lease_expires_at = NULL, deadline_at = NULL,
                            queue_position = NULL, recovery_note = ?,
                            message = 'Worker đóng an toàn; job thay đổi bên ngoài cần kiểm tra.'
                        WHERE id = ? AND status = 'RUNNING'
                        """,
                        (
                            now,
                            "External mutation interrupted during shutdown",
                            job_id,
                        ),
                    )
                    await self._insert_event(
                        db,
                        job_id,
                        "SHUTDOWN_QUARANTINED",
                        "RUNNING",
                        "FAILED",
                        "Job ghi bên ngoài bị cô lập khi worker dừng.",
                    )
            await db.commit()

    async def mark_interrupted_jobs_failed(self):
        """Backward-compatible alias for callers from Giai đoạn 1."""
        await self.prepare_recovery()
