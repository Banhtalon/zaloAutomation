"""Durable per-target state for a local parallel Zalo/BCNB batch."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from core.time_utils import now_utc, utc_iso
from db.database import get_db_connection


SCAN_STATUSES = {"PENDING", "RUNNING", "VERIFIED", "SKIPPED", "FAILED"}
WRITE_STATUSES = {
    "NOT_READY",
    "PENDING",
    "RUNNING",
    "DRY_RUN",
    "VERIFIED",
    "ALREADY_DONE",
    "FAILED_BEFORE_WRITE",
    "OUTCOME_UNKNOWN",
    "BLOCKED_BATCH_HALT",
}


def canonical_target_key(target: dict[str, Any]) -> str:
    class_code = str(target.get("class_code") or "").strip().upper()
    lesson = str(target.get("lesson") or "").strip()
    class_date_iso = str(target.get("class_date_iso") or "").strip()
    try:
        week_value = target.get("week")
        year_value = target.get("year")
        if week_value is None or year_value is None:
            raise ValueError
        week = int(week_value)
        year = int(year_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Target thiếu tuần/năm hợp lệ.") from exc
    if not class_code or not lesson or not class_date_iso:
        raise ValueError("Target thiếu class_code/lesson/class_date_iso.")
    if not 1 <= week <= 53 or not 2000 < year <= 2100:
        raise ValueError("Target có tuần/năm ngoài phạm vi.")
    return f"{year:04d}-W{week:02d}|{class_code}|{lesson}|{class_date_iso}"


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


class ZaloBatchRepository:
    _columns = """
        id, job_id, target_key, group_name, person, class_code, week, year,
        lesson, class_date, class_date_iso, source_json, scan_status, write_status,
        claim_phase, claim_owner, claim_expires_at, attempt_count,
        evidence_path, evidence_sha256, result_json, error_code, error_message,
        created_at, updated_at, started_at, finished_at
    """

    @staticmethod
    def _row(row) -> dict[str, Any]:
        keys = [
            "id", "job_id", "target_key", "group_name", "person",
            "class_code", "week", "year", "lesson", "class_date",
            "class_date_iso", "source", "scan_status", "write_status", "claim_phase",
            "claim_owner", "claim_expires_at", "attempt_count",
            "evidence_path", "evidence_sha256", "result", "error_code",
            "error_message", "created_at", "updated_at", "started_at",
            "finished_at",
        ]
        item = dict(zip(keys, row, strict=True))
        item["source"] = _parse_json(item["source"])
        item["result"] = _parse_json(item["result"])
        return item

    async def seed_targets(self, job_id: int, targets: list[dict[str, Any]]) -> int:
        if not targets:
            raise ValueError("Batch phải có ít nhất một target.")
        prepared: list[tuple[Any, ...]] = []
        seen: set[str] = set()
        stamp = utc_iso()
        for target in targets:
            key = canonical_target_key(target)
            if key in seen:
                raise ValueError(f"Target trùng trong batch: {key}")
            seen.add(key)
            group_name = str(target.get("group_name") or "").strip()
            if not group_name:
                raise ValueError(f"Target {key} thiếu group_name.")
            prepared.append(
                (
                    job_id,
                    key,
                    group_name,
                    str(target.get("person") or "Chưa xác định").strip(),
                    str(target.get("class_code") or "").strip().upper(),
                    int(target["week"]),
                    int(target["year"]),
                    str(target.get("lesson") or "").strip(),
                    str(target.get("class_date") or "").strip(),
                    str(target.get("class_date_iso") or "").strip(),
                    _json(target),
                    stamp,
                    stamp,
                )
            )
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.executemany(
                    """
                    INSERT INTO zalo_batch_targets(
                        job_id, target_key, group_name, person, class_code,
                        week, year, lesson, class_date, class_date_iso,
                        source_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    prepared,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return len(prepared)

    async def _claim(
        self,
        job_id: int,
        *,
        phase: str,
        owner: str,
        lease_seconds: int = 120,
        periods: set[tuple[int, int]] | None = None,
    ) -> dict[str, Any] | None:
        status_column = "scan_status" if phase == "scan" else "write_status"
        eligible_status = "PENDING"
        period_sql = ""
        params: list[Any] = [job_id, eligible_status]
        if periods:
            clauses = []
            for week, year in sorted(periods):
                clauses.append("(week = ? AND year = ?)")
                params.extend((week, year))
            period_sql = " AND (" + " OR ".join(clauses) + ")"
        expires = utc_iso(now_utc() + timedelta(seconds=max(1, lease_seconds)))
        stamp = utc_iso()
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                f"""
                SELECT id FROM zalo_batch_targets
                WHERE job_id = ? AND {status_column} = ?{period_sql}
                ORDER BY week, year, id LIMIT 1
                """,
                tuple(params),
            ) as cursor:
                selected = await cursor.fetchone()
            if selected is None:
                await db.rollback()
                return None
            target_id = int(selected[0])
            cursor = await db.execute(
                f"""
                UPDATE zalo_batch_targets
                SET {status_column} = 'RUNNING', claim_phase = ?, claim_owner = ?,
                    claim_expires_at = ?, attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE id = ? AND {status_column} = ?
                """,
                (phase, owner, expires, stamp, stamp, target_id, eligible_status),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return None
            async with db.execute(
                f"SELECT {self._columns} FROM zalo_batch_targets WHERE id = ?",
                (target_id,),
            ) as cursor:
                row = await cursor.fetchone()
            await db.commit()
        return self._row(row)

    async def claim_scan(self, job_id: int, owner: str) -> dict[str, Any] | None:
        return await self._claim(job_id, phase="scan", owner=owner)

    async def claim_write(
        self,
        job_id: int,
        owner: str,
        *,
        periods: set[tuple[int, int]] | None = None,
    ) -> dict[str, Any] | None:
        return await self._claim(
            job_id, phase="write", owner=owner, periods=periods
        )

    async def complete_scan(
        self,
        target_id: int,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        evidence_path: str | None = None,
        evidence_sha256: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        write_ready: bool = False,
    ) -> None:
        if status not in SCAN_STATUSES - {"PENDING", "RUNNING"}:
            raise ValueError(f"Scan status không hợp lệ: {status}")
        write_status = "PENDING" if write_ready else "NOT_READY"
        stamp = utc_iso()
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                UPDATE zalo_batch_targets
                SET scan_status = ?, write_status = ?, result_json = ?,
                    evidence_path = ?, evidence_sha256 = ?, error_code = ?,
                    error_message = ?, claim_phase = NULL, claim_owner = NULL,
                    claim_expires_at = NULL, updated_at = ?, finished_at = ?
                WHERE id = ? AND scan_status = 'RUNNING'
                """,
                (
                    status, write_status, _json(result), evidence_path,
                    evidence_sha256, error_code,
                    (error_message or "")[:500] or None, stamp, stamp, target_id,
                ),
            )
            await db.commit()
            if cursor.rowcount != 1:
                raise RuntimeError("Target scan không còn ở trạng thái RUNNING.")

    async def finalize_registered_scan(
        self,
        target_id: int,
        *,
        result: dict[str, Any],
        write_ready: bool,
        batch_halted: bool = False,
        evidence_path: str | None = None,
        evidence_sha256: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Persist post-registry evidence state before the write phase starts."""
        write_status = (
            "BLOCKED_BATCH_HALT"
            if batch_halted
            else ("PENDING" if write_ready else "FAILED_BEFORE_WRITE")
        )
        stamp = utc_iso()
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                UPDATE zalo_batch_targets
                SET result_json = ?, evidence_path = ?, evidence_sha256 = ?,
                    write_status = ?, error_code = ?, error_message = ?,
                    updated_at = ?, finished_at = ?
                WHERE id = ?
                  AND scan_status IN ('VERIFIED', 'SKIPPED', 'FAILED')
                  AND write_status IN ('NOT_READY', 'PENDING')
                """,
                (
                    _json(result), evidence_path, evidence_sha256, write_status,
                    error_code, (error_message or "")[:500] or None,
                    stamp, stamp, target_id,
                ),
            )
            await db.commit()
            if cursor.rowcount != 1:
                raise RuntimeError("Target scan không sẵn sàng để đăng ký evidence.")

    async def complete_write(
        self,
        target_id: int,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in WRITE_STATUSES - {"NOT_READY", "PENDING", "RUNNING"}:
            raise ValueError(f"Write status không hợp lệ: {status}")
        stamp = utc_iso()
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                UPDATE zalo_batch_targets
                SET write_status = ?, error_code = ?, error_message = ?,
                    claim_phase = NULL, claim_owner = NULL,
                    claim_expires_at = NULL, updated_at = ?, finished_at = ?
                WHERE id = ? AND write_status = 'RUNNING'
                """,
                (
                    status, error_code, (error_message or "")[:500] or None,
                    stamp, stamp, target_id,
                ),
            )
            await db.commit()
            if cursor.rowcount != 1:
                raise RuntimeError("Target write không còn ở trạng thái RUNNING.")

    async def resolve_unknown(self, target_id: int, *, status: str) -> None:
        """Resolve an interrupted production write using fresh BCNB readback."""
        if status not in {"VERIFIED", "PENDING", "OUTCOME_UNKNOWN"}:
            raise ValueError(f"Trạng thái reconciliation không hợp lệ: {status}")
        stamp = utc_iso()
        error_code = None if status != "OUTCOME_UNKNOWN" else "READBACK_UNAVAILABLE"
        error_message = (
            None
            if status != "OUTCOME_UNKNOWN"
            else "Không đọc được trạng thái BCNB; không được click lại."
        )
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                UPDATE zalo_batch_targets
                SET write_status = ?, error_code = ?, error_message = ?,
                    claim_phase = NULL, claim_owner = NULL,
                    claim_expires_at = NULL, updated_at = ?, finished_at = ?
                WHERE id = ? AND write_status = 'OUTCOME_UNKNOWN'
                """,
                (status, error_code, error_message, stamp, stamp, target_id),
            )
            await db.commit()
            if cursor.rowcount != 1:
                raise RuntimeError("Target không còn ở trạng thái OUTCOME_UNKNOWN.")

    async def halt_unclaimed(self, job_id: int, reason: str) -> dict[str, int]:
        """Stop new claims while leaving workers already in readback untouched."""
        stamp = utc_iso()
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            scan = await db.execute(
                """
                UPDATE zalo_batch_targets
                SET scan_status = 'FAILED', write_status = 'BLOCKED_BATCH_HALT',
                    error_code = 'BATCH_HALTED', error_message = ?,
                    updated_at = ?, finished_at = ?
                WHERE job_id = ? AND scan_status = 'PENDING'
                """,
                (reason[:500], stamp, stamp, job_id),
            )
            write = await db.execute(
                """
                UPDATE zalo_batch_targets
                SET write_status = 'BLOCKED_BATCH_HALT',
                    error_code = 'BATCH_HALTED', error_message = ?,
                    updated_at = ?, finished_at = ?
                WHERE job_id = ? AND write_status = 'PENDING'
                """,
                (reason[:500], stamp, stamp, job_id),
            )
            await db.commit()
        return {
            "scan_blocked": max(0, int(scan.rowcount)),
            "write_blocked": max(0, int(write.rowcount)),
        }

    async def terminalize_interrupted(
        self,
        job_id: int,
        *,
        production: bool,
        reason: str,
    ) -> None:
        """Leave no active target behind when the parent job terminates."""
        stamp = utc_iso()
        active_write_status = "OUTCOME_UNKNOWN" if production else "FAILED_BEFORE_WRITE"
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                UPDATE zalo_batch_targets
                SET scan_status = 'FAILED', write_status = 'BLOCKED_BATCH_HALT',
                    claim_phase = NULL, claim_owner = NULL,
                    claim_expires_at = NULL, error_code = 'PARENT_INTERRUPTED',
                    error_message = ?, updated_at = ?, finished_at = ?
                WHERE job_id = ? AND scan_status IN ('PENDING', 'RUNNING')
                """,
                (reason[:500], stamp, stamp, job_id),
            )
            await db.execute(
                f"""
                UPDATE zalo_batch_targets
                SET write_status = '{active_write_status}', claim_phase = NULL,
                    claim_owner = NULL, claim_expires_at = NULL,
                    error_code = 'PARENT_INTERRUPTED', error_message = ?,
                    updated_at = ?, finished_at = ?
                WHERE job_id = ? AND write_status = 'RUNNING'
                """,
                (reason[:500], stamp, stamp, job_id),
            )
            await db.execute(
                """
                UPDATE zalo_batch_targets
                SET write_status = 'BLOCKED_BATCH_HALT',
                    error_code = 'PARENT_INTERRUPTED', error_message = ?,
                    updated_at = ?, finished_at = ?
                WHERE job_id = ? AND write_status IN ('PENDING', 'NOT_READY')
                """,
                (reason[:500], stamp, stamp, job_id),
            )
            await db.commit()

    async def block_pending_writes(self, job_id: int, reason: str) -> int:
        stamp = utc_iso()
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                UPDATE zalo_batch_targets
                SET write_status = 'BLOCKED_BATCH_HALT', error_code = 'BATCH_HALTED',
                    error_message = ?, updated_at = ?, finished_at = ?
                WHERE job_id = ? AND write_status = 'PENDING'
                """,
                (reason[:500], stamp, stamp, job_id),
            )
            await db.commit()
            return max(0, int(cursor.rowcount))

    async def recover_interrupted(self, job_id: int) -> dict[str, int]:
        stamp = utc_iso()
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            scan = await db.execute(
                """
                UPDATE zalo_batch_targets
                SET scan_status = 'PENDING', claim_phase = NULL, claim_owner = NULL,
                    claim_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND scan_status = 'RUNNING'
                """,
                (stamp, job_id),
            )
            write = await db.execute(
                """
                UPDATE zalo_batch_targets
                SET write_status = 'OUTCOME_UNKNOWN', claim_phase = NULL,
                    claim_owner = NULL, claim_expires_at = NULL,
                    error_code = 'INTERRUPTED_DURING_WRITE',
                    error_message = 'Phải readback trước khi retry.',
                    updated_at = ?, finished_at = ?
                WHERE job_id = ? AND write_status = 'RUNNING'
                """,
                (stamp, stamp, job_id),
            )
            await db.commit()
        return {"scan_requeued": scan.rowcount, "write_unknown": write.rowcount}

    async def list_for_job(self, job_id: int) -> list[dict[str, Any]]:
        async with get_db_connection() as db:
            async with db.execute(
                f"SELECT {self._columns} FROM zalo_batch_targets WHERE job_id = ? ORDER BY id",
                (job_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row(row) for row in rows]

    async def summary(self, job_id: int) -> dict[str, Any]:
        items = await self.list_for_job(job_id)
        scan: dict[str, int] = {}
        write: dict[str, int] = {}
        for item in items:
            scan[item["scan_status"]] = scan.get(item["scan_status"], 0) + 1
            write[item["write_status"]] = write.get(item["write_status"], 0) + 1
        return {"total": len(items), "scan": scan, "write": write}
