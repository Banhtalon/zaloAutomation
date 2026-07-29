import json
from datetime import datetime, timezone
from typing import Any

from db.database import get_db_connection
from googleform_autosubmit.config import Config


TERMINAL_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED", "CANCELLED"}


class JobStateError(ValueError):
    pass


class JobNotFoundError(JobStateError):
    pass


class JobCancelledError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        mutates_external, request_payload, recovery_note
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
                    recoverable, mutates_external, request_payload
                ) VALUES (?, 'PENDING', ?, 0, ?, 0, ?, ?, ?, ?, ?, ?)
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
                    updates.extend(["finished_at = ?", "heartbeat_at = NULL"])
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
                heartbeat = datetime.fromisoformat(row[6])
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                heartbeat_age = max(
                    0, int((datetime.now(timezone.utc) - heartbeat).total_seconds())
                )
            except (TypeError, ValueError):
                heartbeat_age = None
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
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat_stale": row[2] == "RUNNING" and (
                heartbeat_age is None or heartbeat_age > Config.HEALTH_STALE_SECONDS
            ),
            "cancel_allowed": row[2] in {"PENDING", "RUNNING"} and not bool(row[10]),
        }
        if include_payload:
            job["request_payload"] = _parse_json(row[16], {})
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
                SELECT id, status, recoverable, mutates_external, request_payload
                FROM scan_jobs WHERE status IN ('PENDING', 'RUNNING')
                ORDER BY id
                """
            ) as cursor:
                rows = await cursor.fetchall()

            for job_id, status, recoverable, mutates_external, payload in rows:
                has_payload = bool(_parse_json(payload, None))
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
                elif status == "RUNNING" and recoverable and not mutates_external and has_payload:
                    await db.execute(
                        """
                        UPDATE scan_jobs SET status = 'PENDING', started_at = NULL,
                            heartbeat_at = NULL, recovery_note = ?,
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
                            heartbeat_at = NULL, recovery_note = ?, progress = 100,
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

    async def mark_interrupted_jobs_failed(self):
        """Backward-compatible alias for callers from Giai đoạn 1."""
        await self.prepare_recovery()
