import json
from datetime import datetime, timezone
from core.time_utils import utc_iso
from typing import Any

from db.database import get_db_connection


def _now() -> str:
    return utc_iso()


def _parse_iso_utc(raw_value: Any) -> datetime | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
                if evidence_path:
                    from repositories.evidence_repository import EvidenceRepository

                    await EvidenceRepository().link_audit(
                        audit_id=int(existing[0]),
                        public_path=evidence_path,
                        evidence_sha256=evidence_sha256,
                    )
                return row, False

            effective_job_id = int(job_id) if isinstance(job_id, int) and not isinstance(job_id, bool) else 0
            if action_type == "REPORT_EMAIL_SMTP_SEND":
                if effective_job_id > 0:
                    async with db.execute(
                        "SELECT id FROM scan_jobs WHERE id = ? LIMIT 1",
                        (effective_job_id,),
                    ) as job_cur:
                        if await job_cur.fetchone() is None:
                            effective_job_id = 0
                if effective_job_id <= 0:
                    now_anchor = _now()
                    anchor_cur = await db.execute(
                        """
                        INSERT INTO scan_jobs (
                            job_type, status, started_at, finished_at, progress,
                            message, results, created_at, mutates_external
                        ) VALUES ('REPORT_EMAIL', 'SUCCESS', ?, ?, 100, ?, '{}', ?, 1)
                        """,
                        (
                            now_anchor,
                            now_anchor,
                            f"Audit anchor for weekly email {target_id}",
                            now_anchor,
                        ),
                    )
                    effective_job_id = int(anchor_cur.lastrowid or 0)

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
                    effective_job_id,
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
        if evidence_path:
            from repositories.evidence_repository import EvidenceRepository

            await EvidenceRepository().link_audit(
                audit_id=int(audit_id),
                public_path=evidence_path,
                evidence_sha256=evidence_sha256,
            )
            row = await self.get_by_id(int(audit_id)) or row
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
        if evidence_path:
            from repositories.evidence_repository import EvidenceRepository

            row = await self.get_by_id(audit_id)
            if row is not None:
                await EvidenceRepository().link_audit(
                    audit_id=audit_id,
                    public_path=evidence_path,
                    evidence_sha256=evidence_sha256,
                )

    async def reconcile_verified(self, audit_id: int) -> None:
        """Resolve an interrupted/unknown intent after fresh server readback."""
        async with get_db_connection() as db:
            await db.execute(
                """
                UPDATE external_action_audit
                SET outcome = 'VERIFIED', after_json = ?, error_message = NULL,
                    completed_at = ?
                WHERE id = ? AND outcome IN ('INTENT_RECORDED', 'UNKNOWN')
                """,
                (_dump({"checked": True, "reconciled": True}), _now(), audit_id),
            )
            await db.commit()

    async def get_by_id(self, audit_id: int) -> dict | None:
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, action_key, job_id, action_type, target_type,
                       target_id, mode, reason_json, before_json,
                       intended_json, after_json, outcome, evidence_path,
                       evidence_sha256, evidence_artifact_id, evidence_status,
                       evidence_size_bytes, evidence_mime_type, evidence_created_at,
                       evidence_capture_type, error_message, created_at, completed_at
                FROM external_action_audit WHERE id = ?
                """,
                (audit_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return self._row(row) if row else None

    async def get_by_action_key(self, action_key: str) -> dict | None:
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, action_key, job_id, action_type, target_type,
                       target_id, mode, reason_json, before_json,
                       intended_json, after_json, outcome, evidence_path,
                       evidence_sha256, evidence_artifact_id, evidence_status,
                       evidence_size_bytes, evidence_mime_type, evidence_created_at,
                       evidence_capture_type, error_message, created_at, completed_at
                FROM external_action_audit WHERE action_key = ?
                """,
                (action_key,),
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
                       evidence_sha256, evidence_artifact_id, evidence_status,
                       evidence_size_bytes, evidence_mime_type, evidence_created_at,
                       evidence_capture_type, error_message, created_at, completed_at
                FROM external_action_audit
                WHERE job_id = ? ORDER BY id DESC LIMIT ?
                """,
                (job_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row(row) for row in rows]

    async def list_checker_note_audits_for_class(
        self,
        class_code: str,
        *,
        limit: int = 200,
    ) -> list[dict]:
        clean_code = str(class_code or "").strip()
        if not clean_code:
            return []
        bounded_limit = max(1, min(500, int(limit)))
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, action_key, job_id, action_type, target_type,
                       target_id, mode, reason_json, before_json,
                       intended_json, after_json, outcome, evidence_path,
                       evidence_sha256, evidence_artifact_id, evidence_status,
                       evidence_size_bytes, evidence_mime_type, evidence_created_at,
                       evidence_capture_type, error_message, created_at, completed_at
                FROM external_action_audit
                WHERE action_type = 'BCNB_UPDATE_CHECKER_NOTE'
                  AND UPPER(REPLACE(TRIM(target_id), ' ', '')) = UPPER(REPLACE(TRIM(?), ' ', ''))
                ORDER BY id DESC LIMIT ?
                """,
                (clean_code, bounded_limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row(row) for row in rows]

    async def list_recent_smtp_send_audits(
        self,
        target_id: str,
        *,
        recipients_sha256: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        clean_target = str(target_id or "").strip()
        if not clean_target:
            return []
        bounded_limit = max(1, min(200, int(limit)))
        clean_rec_sha = str(recipients_sha256 or "").strip()
        async with get_db_connection() as db:
            if clean_rec_sha:
                async with db.execute(
                    """
                    SELECT id, action_key, job_id, action_type, target_type,
                           target_id, mode, reason_json, before_json,
                           intended_json, after_json, outcome, evidence_path,
                           evidence_sha256, evidence_artifact_id, evidence_status,
                           evidence_size_bytes, evidence_mime_type, evidence_created_at,
                           evidence_capture_type, error_message, created_at, completed_at
                    FROM external_action_audit
                    WHERE action_type = 'REPORT_EMAIL_SMTP_SEND'
                      AND target_id = ?
                      AND (
                          (json_valid(reason_json) AND json_extract(reason_json, '$.recipients_sha256') = ?)
                          OR (json_valid(after_json) AND json_extract(after_json, '$.recipients_sha256') = ?)
                          OR (json_valid(intended_json) AND json_extract(intended_json, '$.recipients_sha256') = ?)
                      )
                    ORDER BY COALESCE(completed_at, created_at) DESC, id DESC LIMIT ?
                    """,
                    (clean_target, clean_rec_sha, clean_rec_sha, clean_rec_sha, bounded_limit + 1),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with db.execute(
                    """
                    SELECT id, action_key, job_id, action_type, target_type,
                           target_id, mode, reason_json, before_json,
                           intended_json, after_json, outcome, evidence_path,
                           evidence_sha256, evidence_artifact_id, evidence_status,
                           evidence_size_bytes, evidence_mime_type, evidence_created_at,
                           evidence_capture_type, error_message, created_at, completed_at
                    FROM external_action_audit
                    WHERE action_type = 'REPORT_EMAIL_SMTP_SEND'
                      AND target_id = ?
                    ORDER BY COALESCE(completed_at, created_at) DESC, id DESC LIMIT ?
                    """,
                    (clean_target, bounded_limit + 1),
                ) as cursor:
                    rows = await cursor.fetchall()
        overflow = len(rows) > bounded_limit
        parsed_rows = [self._row(row) for row in rows[:bounded_limit]]
        if overflow and parsed_rows:
            parsed_rows[0]["_overflow"] = True
        return parsed_rows

    async def begin_smtp_send_intent(
        self,
        *,
        action_key: str,
        job_id: int | None,
        target_id: str,
        recipients_sha256: str,
        content_sha256: str,
        intended_state: Any = None,
        reason: Any = None,
        duplicate_window_seconds: int = 300,
        confirm_duplicate: bool = False,
    ) -> tuple[dict | None, dict | None]:
        """Atomically check recent SMTP sends under BEGIN IMMEDIATE and record INTENT_RECORDED."""
        clean_target = str(target_id or "").strip()
        clean_rec_sha = str(recipients_sha256 or "").strip()
        max_scan_rows = 200
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            if not confirm_duplicate and clean_target:
                async with db.execute(
                    """
                    SELECT id, action_key, job_id, action_type, target_type,
                           target_id, mode, reason_json, before_json,
                           intended_json, after_json, outcome, evidence_path,
                           evidence_sha256, evidence_artifact_id, evidence_status,
                           evidence_size_bytes, evidence_mime_type, evidence_created_at,
                           evidence_capture_type, error_message, created_at, completed_at
                    FROM external_action_audit
                    WHERE action_type = 'REPORT_EMAIL_SMTP_SEND'
                      AND target_id = ?
                      AND outcome IN ('VERIFIED', 'INTENT_RECORDED', 'FAILED_AFTER_WRITE', 'UNKNOWN')
                      AND (
                          (json_valid(reason_json) AND json_extract(reason_json, '$.recipients_sha256') = ?)
                          OR (json_valid(after_json) AND json_extract(after_json, '$.recipients_sha256') = ?)
                          OR (json_valid(intended_json) AND json_extract(intended_json, '$.recipients_sha256') = ?)
                      )
                    ORDER BY COALESCE(completed_at, created_at) DESC, id DESC LIMIT ?
                    """,
                    (clean_target, clean_rec_sha, clean_rec_sha, clean_rec_sha, max_scan_rows + 1),
                ) as cursor:
                    recent_rows = await cursor.fetchall()
                if len(recent_rows) > max_scan_rows:
                    await db.rollback()
                    top_parsed = self._row(recent_rows[0])
                    top_parsed["_overflow"] = True
                    top_parsed["_age_seconds"] = 0
                    return None, top_parsed
                now_utc = datetime.now(timezone.utc)
                for raw_row in recent_rows[:max_scan_rows]:
                    parsed = self._row(raw_row)
                    outcome = str(parsed.get("outcome") or "").strip().upper()
                    audit_dt = _parse_iso_utc(
                        parsed.get("completed_at") or parsed.get("created_at")
                    )
                    if audit_dt is None:
                        continue
                    age_sec = (now_utc - audit_dt).total_seconds()
                    if age_sec > float(duplicate_window_seconds):
                        continue
                    reason_obj = parsed.get("reason")
                    if not isinstance(reason_obj, dict):
                        reason_obj = {}
                    after_obj = parsed.get("after_state")
                    if not isinstance(after_obj, dict):
                        after_obj = {}
                    intended_obj = parsed.get("intended_state")
                    if not isinstance(intended_obj, dict):
                        intended_obj = {}
                    if outcome == "FAILED_AFTER_WRITE":
                        accepted = after_obj.get("accepted_recipients") or reason_obj.get(
                            "accepted_recipients"
                        )
                        if not accepted:
                            continue
                    row_rec_sha = str(
                        reason_obj.get("recipients_sha256")
                        or after_obj.get("recipients_sha256")
                        or intended_obj.get("recipients_sha256")
                        or ""
                    )
                    if row_rec_sha == clean_rec_sha:
                        await db.rollback()
                        parsed["_age_seconds"] = max(0, int(age_sec))
                        return None, parsed

            effective_job_id = (
                int(job_id)
                if isinstance(job_id, int) and not isinstance(job_id, bool) and job_id > 0
                else 0
            )
            if effective_job_id > 0:
                async with db.execute(
                    "SELECT id FROM scan_jobs WHERE id = ? LIMIT 1",
                    (effective_job_id,),
                ) as job_cur:
                    if await job_cur.fetchone() is None:
                        effective_job_id = 0
            if effective_job_id <= 0:
                now_anchor = _now()
                anchor_cur = await db.execute(
                    """
                    INSERT INTO scan_jobs (
                        job_type, status, started_at, finished_at, progress,
                        message, results, created_at, mutates_external
                    ) VALUES ('REPORT_EMAIL', 'SUCCESS', ?, ?, 100, ?, '{}', ?, 1)
                    """,
                    (
                        now_anchor,
                        now_anchor,
                        f"Audit anchor for weekly email {clean_target}",
                        now_anchor,
                    ),
                )
                effective_job_id = int(anchor_cur.lastrowid or 0)

            cursor = await db.execute(
                """
                INSERT INTO external_action_audit (
                    action_key, job_id, action_type, target_type, target_id,
                    mode, reason_json, before_json, intended_json,
                    outcome, evidence_path, evidence_sha256, created_at
                ) VALUES (?, ?, 'REPORT_EMAIL_SMTP_SEND', 'weekly_report_email', ?, 'production', ?, NULL, ?, 'INTENT_RECORDED', NULL, ?, ?)
                """,
                (
                    action_key,
                    effective_job_id,
                    clean_target,
                    _dump(reason),
                    _dump(intended_state),
                    content_sha256,
                    _now(),
                ),
            )
            audit_id = cursor.lastrowid
            await db.commit()
        row = await self.get_by_id(audit_id)
        if row is None:
            raise RuntimeError("Không đọc lại được audit SMTP vừa tạo.")
        return row, None

    async def count_unresolved(self) -> int:
        """Count external actions that still need read-back or review."""
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT COUNT(*)
                FROM external_action_audit
                WHERE outcome IN ('INTENT_RECORDED', 'UNKNOWN')
                """
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0

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
            "evidence_artifact_id": row[14],
            "evidence_status": row[15],
            "evidence_size_bytes": row[16],
            "evidence_mime_type": row[17],
            "evidence_created_at": row[18],
            "evidence_capture_type": row[19],
            "error_message": row[20],
            "created_at": row[21],
            "completed_at": row[22],
        }
