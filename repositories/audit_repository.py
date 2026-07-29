import json
from datetime import datetime, timezone
from typing import Any

from db.database import get_db_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value: str | None):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


class AuditRepository:
    async def record_intent(
        self,
        *,
        action_key: str,
        job_id: int,
        action_type: str,
        target_type: str,
        target_id: str,
        mode: str,
        before_state=None,
        intended_state=None,
        reason=None,
        evidence_path: str | None = None,
        evidence_sha256: str | None = None,
    ) -> tuple[dict, bool]:
        """Commit intent before an external write; return existing row on duplicate."""
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT id, outcome FROM external_action_audit WHERE action_key = ?",
                (action_key,),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing:
                await db.rollback()
                row = await self.get_by_id(existing[0])
                if row is None:
                    raise RuntimeError("Không đọc lại được audit action đã tồn tại.")
                return row, False

            cursor = await db.execute(
                """
                INSERT INTO external_action_audit (
                    action_key, job_id, action_type, target_type, target_id,
                    mode, reason_json, before_json, intended_json,
                    outcome, evidence_path, evidence_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'INTENT_RECORDED', ?, ?, ?)
                """,
                (
                    action_key,
                    job_id,
                    action_type,
                    target_type,
                    target_id,
                    mode,
                    _dump(reason),
                    _dump(before_state),
                    _dump(intended_state),
                    evidence_path,
                    evidence_sha256,
                    _now(),
                ),
            )
            audit_id = cursor.lastrowid
            await db.commit()
        row = await self.get_by_id(audit_id)
        if row is None:
            raise RuntimeError("Không đọc lại được audit action vừa tạo.")
        return row, True

    async def complete_action(
        self,
        audit_id: int,
        *,
        outcome: str,
        after_state=None,
        error_message: str | None = None,
        evidence_path: str | None = None,
        evidence_sha256: str | None = None,
    ) -> None:
        async with get_db_connection() as db:
            await db.execute(
                """
                UPDATE external_action_audit
                SET outcome = ?, after_json = ?, error_message = ?,
                    evidence_path = COALESCE(?, evidence_path),
                    evidence_sha256 = COALESCE(?, evidence_sha256),
                    completed_at = ?
                WHERE id = ? AND outcome = 'INTENT_RECORDED'
                """,
                (
                    outcome,
                    _dump(after_state),
                    error_message,
                    evidence_path,
                    evidence_sha256,
                    _now(),
                    audit_id,
                ),
            )
            await db.commit()

    async def get_by_id(self, audit_id: int) -> dict | None:
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, action_key, job_id, action_type, target_type,
                       target_id, mode, reason_json, before_json,
                       intended_json, after_json, outcome, evidence_path,
                       evidence_sha256, error_message, created_at, completed_at
                FROM external_action_audit WHERE id = ?
                """,
                (audit_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return self._row(row) if row else None

    async def list_for_job(self, job_id: int, limit: int = 100) -> list[dict]:
        limit = max(1, min(200, limit))
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, action_key, job_id, action_type, target_type,
                       target_id, mode, reason_json, before_json,
                       intended_json, after_json, outcome, evidence_path,
                       evidence_sha256, error_message, created_at, completed_at
                FROM external_action_audit
                WHERE job_id = ? ORDER BY id DESC LIMIT ?
                """,
                (job_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row) -> dict:
        return {
            "id": row[0],
            "action_key": row[1],
            "job_id": row[2],
            "action_type": row[3],
            "target_type": row[4],
            "target_id": row[5],
            "mode": row[6],
            "reason": _load(row[7]),
            "before_state": _load(row[8]),
            "intended_state": _load(row[9]),
            "after_state": _load(row[10]),
            "outcome": row[11],
            "evidence_path": row[12],
            "evidence_sha256": row[13],
            "error_message": row[14],
            "created_at": row[15],
            "completed_at": row[16],
        }
