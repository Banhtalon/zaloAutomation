"""Persistent evidence metadata, verification and retention operations.

The database is the source of truth for an evidence lifecycle.  A screenshot
is only considered usable when the file exists and its SHA-256 matches the
stored digest.  Retention changes the lifecycle state; it does not silently
delete files.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any

from core.time_utils import UTC_TIMEZONE, now_utc, parse_timestamp, utc_iso
from db.database import get_db_connection
from logger_utils import log_message


AVAILABLE = "AVAILABLE"
EXPIRED = "EXPIRED"
MISSING = "MISSING"
QUARANTINED = "QUARANTINED"


def _now() -> str:
    return utc_iso()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return parse_timestamp(value)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _metadata(path: Path) -> tuple[str, int, str]:
    guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return sha256_file(path), path.stat().st_size, guessed


class EvidenceRepository:
    async def register_artifact(
        self,
        *,
        job_id: int | None,
        public_path: str,
        stored_path: str,
        capture_type: str,
        audit_id: int | None = None,
        expected_sha256: str | None = None,
        created_at: str | None = None,
        retention_days: int = 14,
    ) -> dict[str, Any]:
        """Upsert an artifact, deriving all file metadata from disk.

        Missing files never receive a fabricated hash.  If a supplied digest
        differs from the bytes on disk the artifact is quarantined.
        """
        public_path = str(public_path or "").strip()
        stored = Path(stored_path).expanduser().resolve()
        created = created_at or _now()
        created_dt = _parse_time(created) or now_utc()
        expires = created_dt + timedelta(days=max(1, retention_days))
        expires_text = utc_iso(expires)
        expected = str(expected_sha256 or "").strip().upper() or None
        status = MISSING
        digest: str | None = None
        size: int | None = None
        mime: str | None = None
        error: str | None = None
        if stored.is_file():
            try:
                actual, size, mime = _metadata(stored)
                digest = actual
                if expected and expected != actual:
                    status = QUARANTINED
                    error = "Stored SHA-256 does not match file bytes."
                else:
                    status = AVAILABLE
            except OSError as exc:
                status = MISSING
                error = str(exc)
        else:
            error = "Evidence file is missing."

        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO evidence_artifacts(
                    job_id, audit_id, public_path, stored_path, sha256,
                    size_bytes, mime_type, created_at, capture_type, status,
                    expires_at, last_verified_at, quarantined_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(public_path) DO UPDATE SET
                    job_id=COALESCE(excluded.job_id, evidence_artifacts.job_id),
                    audit_id=COALESCE(excluded.audit_id, evidence_artifacts.audit_id),
                    stored_path=excluded.stored_path,
                    sha256=excluded.sha256,
                    size_bytes=excluded.size_bytes,
                    mime_type=excluded.mime_type,
                    capture_type=excluded.capture_type,
                    status=excluded.status,
                    expires_at=excluded.expires_at,
                    last_verified_at=excluded.last_verified_at,
                    quarantined_at=excluded.quarantined_at,
                    error_message=excluded.error_message
                """,
                (
                    job_id,
                    audit_id,
                    public_path,
                    str(stored),
                    digest,
                    size,
                    mime,
                    created,
                    capture_type or "unknown",
                    status,
                    expires_text,
                    _now() if status == AVAILABLE else None,
                    _now() if status == QUARANTINED else None,
                    error,
                ),
            )
            await db.commit()
        artifact = await self.get_by_public_path(public_path)
        if artifact is None:
            raise RuntimeError("Khong doc lai duoc evidence artifact vua tao.")
        return artifact

    async def get_by_public_path(self, public_path: str) -> dict[str, Any] | None:
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, job_id, audit_id, public_path, stored_path, sha256,
                       size_bytes, mime_type, created_at, capture_type, status,
                       expires_at, last_verified_at, quarantined_at,
                       tombstoned_at, error_message
                FROM evidence_artifacts WHERE public_path = ?
                """,
                (public_path,),
            ) as cursor:
                row = await cursor.fetchone()
        return self._row(row) if row else None

    async def get_by_id(self, artifact_id: int) -> dict[str, Any] | None:
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, job_id, audit_id, public_path, stored_path, sha256,
                       size_bytes, mime_type, created_at, capture_type, status,
                       expires_at, last_verified_at, quarantined_at,
                       tombstoned_at, error_message
                FROM evidence_artifacts WHERE id = ?
                """,
                (artifact_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return self._row(row) if row else None

    async def verify(
        self,
        public_path: str,
        *,
        expected_sha256: str | None = None,
        job_id: int | None = None,
    ) -> dict[str, Any] | None:
        artifact = await self.get_by_public_path(public_path)
        if artifact is None:
            return None
        expected = str(expected_sha256 or artifact.get("sha256") or "").upper() or None
        status = str(artifact.get("status") or MISSING)
        error: str | None = None
        actual: str | None = None
        stored = Path(str(artifact["stored_path"])).resolve()
        if job_id is not None and artifact.get("job_id") not in {None, job_id}:
            status = MISSING
            error = "Evidence does not belong to this job."
        elif status in {EXPIRED, QUARANTINED}:
            error = f"Evidence lifecycle is {status}."
        elif not stored.is_file():
            status = MISSING
            error = "Evidence file is missing."
        else:
            try:
                actual, size, mime = _metadata(stored)
            except OSError as exc:
                status = MISSING
                error = str(exc)
            else:
                expires = _parse_time(artifact.get("expires_at"))
                if expires and now_utc() >= expires:
                    status = EXPIRED
                    error = "Evidence retention window has expired."
                elif expected and actual != expected:
                    status = QUARANTINED
                    error = "Evidence SHA-256 does not match."
                elif artifact.get("sha256") and actual != str(artifact["sha256"]).upper():
                    status = QUARANTINED
                    error = "Evidence SHA-256 does not match registry."
                else:
                    status = AVAILABLE
                    expected = actual

                async with get_db_connection() as db:
                    await db.execute(
                        """
                        UPDATE evidence_artifacts
                        SET sha256 = COALESCE(?, sha256), size_bytes = ?,
                            mime_type = ?, status = ?, last_verified_at = ?,
                            quarantined_at = CASE WHEN ? = 'QUARANTINED'
                                THEN COALESCE(quarantined_at, ?) ELSE quarantined_at END,
                            error_message = ?
                        WHERE id = ?
                        """,
                        (
                            actual if status == AVAILABLE else None,
                            size,
                            mime,
                            status,
                            _now() if status == AVAILABLE else None,
                            status,
                            _now(),
                            error,
                            artifact["id"],
                        ),
                    )
                    await db.commit()
        if status in {MISSING, EXPIRED, QUARANTINED} and error:
            async with get_db_connection() as db:
                await db.execute(
                    "UPDATE evidence_artifacts SET status = ?, error_message = ? WHERE id = ?",
                    (status, error, artifact["id"]),
                )
                await db.commit()
        refreshed = await self.get_by_id(int(artifact["id"]))
        return refreshed

    async def link_audit(
        self,
        *,
        audit_id: int,
        public_path: str | None,
        evidence_sha256: str | None,
    ) -> dict[str, Any] | None:
        if not public_path:
            return None
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT id, sha256, status, size_bytes, mime_type, created_at, capture_type "
                "FROM evidence_artifacts WHERE public_path = ?",
                (public_path,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            linked_status = row[2]
            supplied = str(evidence_sha256 or "").strip().upper() or None
            registered = str(row[1] or "").strip().upper() or None
            if supplied and registered and supplied != registered:
                linked_status = QUARANTINED
                await db.execute(
                    "UPDATE evidence_artifacts SET status = ?, quarantined_at = COALESCE(quarantined_at, ?), "
                    "error_message = ? WHERE id = ?",
                    (
                        QUARANTINED,
                        _now(),
                        "Audit SHA-256 does not match evidence registry.",
                        row[0],
                    ),
                )
            await db.execute(
                "UPDATE evidence_artifacts SET audit_id = ? WHERE id = ?",
                (audit_id, row[0]),
            )
            await db.execute(
                """
                UPDATE external_action_audit
                SET evidence_artifact_id = ?, evidence_status = ?,
                    evidence_size_bytes = ?, evidence_mime_type = ?,
                    evidence_created_at = ?, evidence_capture_type = ?
                WHERE id = ?
                """,
                (row[0], linked_status, row[3], row[4], row[5], row[6], audit_id),
            )
            await db.commit()
        return await self.get_by_id(int(row[0]))

    async def record_access(
        self,
        *,
        artifact_id: int | None,
        job_id: int,
        username: str | None,
        outcome: str,
        request_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO evidence_access_log(
                    evidence_artifact_id, job_id, username, accessed_at,
                    outcome, request_id, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, job_id, username, _now(), outcome, request_id, error_message),
            )
            await db.commit()

    async def expire_old(
        self,
        *,
        root: str,
        retention_days: int = 14,
        policy: str = "tombstone",
        delete_files: bool = False,
    ) -> int:
        """Expire evidence at the retention boundary and optionally delete bytes.

        The registry/audit rows remain in SQLite so historical decisions can
        still be read back.  When ``delete_files`` is enabled, only PNG files
        below the configured evidence root and ``job-*`` directories are
        removed, and only after their lifecycle has been marked ``EXPIRED``.
        The inclusive comparison makes an artifact exactly ``retention_days``
        old eligible on the next app/scan run.
        """
        cutoff = now_utc() - timedelta(days=max(1, int(retention_days)))
        lifecycle = QUARANTINED if policy == "quarantine" else EXPIRED
        root_path = Path(root).expanduser().resolve()
        expired_paths: list[str] = []
        changed = 0
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT id, created_at, stored_path, status "
                "FROM evidence_artifacts "
                "WHERE status IN ('AVAILABLE', 'EXPIRED') "
                "AND created_at IS NOT NULL"
            ) as cursor:
                rows = await cursor.fetchall()
            for artifact_id, created_at, stored_path, status in rows:
                created = _parse_time(created_at)
                if not created or created > cutoff:
                    continue
                if status == AVAILABLE:
                    await db.execute(
                        "UPDATE evidence_artifacts SET status = ?, "
                        "tombstoned_at = CASE WHEN ? = 'EXPIRED' "
                        "THEN COALESCE(tombstoned_at, ?) ELSE tombstoned_at END, "
                        "quarantined_at = CASE WHEN ? = 'QUARANTINED' "
                        "THEN COALESCE(quarantined_at, ?) ELSE quarantined_at END, "
                        "error_message = ? WHERE id = ?",
                        (
                            lifecycle,
                            lifecycle,
                            _now(),
                            lifecycle,
                            _now(),
                            "Evidence exceeded configured retention policy.",
                            artifact_id,
                        ),
                    )
                    changed += 1
                if lifecycle == EXPIRED and stored_path:
                    expired_paths.append(str(stored_path))
            await db.commit()

        deleted = 0
        if delete_files and lifecycle == EXPIRED:
            for stored_path in expired_paths:
                if self._delete_expired_file(root_path, stored_path):
                    deleted += 1

        # Keep a deterministic fallback for legacy files which have not yet
        # been backfilled.  Register their lifecycle before optional deletion.
        if root_path.is_dir():
            for path in root_path.rglob("*.png"):
                try:
                    modified = datetime.fromtimestamp(
                        path.stat().st_mtime, tz=UTC_TIMEZONE
                    )
                except OSError:
                    continue
                if modified <= cutoff:
                    changed += await self._tombstone_unregistered(
                        root_path,
                        path,
                        delete_file=delete_files,
                    )
            if delete_files:
                for directory in sorted(
                    (item for item in root_path.glob("job-*") if item.is_dir()),
                    key=lambda item: len(item.parts),
                    reverse=True,
                ):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
        if delete_files and deleted:
            log_message(
                f"Đã xóa {deleted} ảnh minh chứng hết hạn sau {max(1, int(retention_days))} ngày.",
                event="evidence.lifecycle.deleted",
                deleted_count=deleted,
                retention_days=max(1, int(retention_days)),
            )
        return changed

    @staticmethod
    def _delete_expired_file(root: Path, stored_path: str | Path) -> bool:
        """Delete one expired PNG only when it is inside the evidence root."""
        candidate = Path(stored_path).expanduser().resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            log_message(
                "Bỏ qua xóa evidence nằm ngoài EVIDENCE_ROOT.",
                level="WARNING",
                event="evidence.lifecycle.delete_rejected",
                stored_path=str(candidate),
            )
            return False
        if (
            candidate.suffix.lower() != ".png"
            or len(relative.parts) < 2
            or not relative.parts[0].startswith("job-")
        ):
            log_message(
                "Bỏ qua xóa đường dẫn evidence không hợp lệ.",
                level="WARNING",
                event="evidence.lifecycle.delete_rejected",
                stored_path=str(candidate),
            )
            return False
        if not candidate.exists():
            return False
        try:
            candidate.unlink()
            return True
        except OSError as exc:
            log_message(
                "Không xóa được ảnh evidence hết hạn.",
                level="WARNING",
                event="evidence.lifecycle.delete_failed",
                stored_path=str(candidate),
                error=str(exc),
            )
            return False

    async def _tombstone_unregistered(
        self,
        root: Path,
        path: Path,
        *,
        delete_file: bool = False,
    ) -> int:
        relative = path.relative_to(root)
        if len(relative.parts) < 2 or not relative.parts[0].startswith("job-"):
            return 0
        try:
            job_id = int(relative.parts[0][4:])
        except ValueError:
            return 0
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT 1 FROM scan_jobs WHERE id = ?", (job_id,)
            ) as cursor:
                has_job = await cursor.fetchone() is not None
        public = f"/api/evidence/{job_id}/{relative.name}"
        artifact = await self.get_by_public_path(public)
        if artifact is None:
            artifact = await self.register_artifact(
                job_id=job_id if has_job else None,
                public_path=public,
                stored_path=str(path),
                capture_type="legacy_backfill",
                retention_days=1,
            )
        changed = 0
        if artifact.get("status") == AVAILABLE:
            async with get_db_connection() as db:
                await db.execute(
                    "UPDATE evidence_artifacts SET status='EXPIRED', "
                    "tombstoned_at=COALESCE(tombstoned_at, ?), error_message=? WHERE id=?",
                    (_now(), "Evidence exceeded configured retention policy.", artifact["id"]),
                )
                await db.commit()
            changed = 1
        if delete_file and artifact.get("status") in {AVAILABLE, EXPIRED}:
            self._delete_expired_file(root, path)
        return changed

    async def backfill_existing_results(
        self,
        *,
        evidence_root: str,
        retention_days: int = 14,
    ) -> int:
        async with get_db_connection() as db:
            async with db.execute("SELECT id, results FROM scan_jobs WHERE results IS NOT NULL") as cursor:
                jobs = await cursor.fetchall()
        registered = 0
        for job_id, raw_results in jobs:
            try:
                results = json.loads(raw_results) if isinstance(raw_results, str) else raw_results
            except (TypeError, json.JSONDecodeError):
                continue
            for item in self._iter_images(results):
                public = str(item.get("path") or "")
                filename = self._filename_for_job(public, int(job_id))
                if not filename:
                    continue
                stored = Path(evidence_root).resolve() / f"job-{job_id}" / filename
                before = await self.get_by_public_path(public)
                await self.register_artifact(
                    job_id=int(job_id),
                    public_path=public,
                    stored_path=str(stored),
                    capture_type=str(item.get("capture_type") or "legacy_backfill"),
                    expected_sha256=str(item.get("sha256") or "") or None,
                    created_at=str(item.get("created_at") or "") or None,
                    retention_days=retention_days,
                )
                registered += 1 if before is None else 0
        return registered

    async def register_scan_results(
        self,
        *,
        job_id: int,
        results: list[dict[str, Any]],
        evidence_root: str,
        retention_days: int = 14,
    ) -> list[dict[str, Any]]:
        """Register every image and downgrade results with unusable evidence."""
        for result in results:
            evidence_value = result.setdefault("evidence", {})
            evidence = evidence_value if isinstance(evidence_value, dict) else {}
            images = evidence.get("images")
            if not isinstance(images, dict):
                continue
            statuses: list[str] = []
            for key, item in list(images.items()):
                if not isinstance(item, dict) or not item.get("path"):
                    continue
                filename = self._filename_for_job(str(item["path"]), job_id)
                if not filename:
                    item["status"] = MISSING
                    statuses.append(MISSING)
                    continue
                stored = Path(evidence_root).resolve() / f"job-{job_id}" / filename
                artifact = await self.register_artifact(
                    job_id=job_id,
                    public_path=str(item["path"]),
                    stored_path=str(stored),
                    capture_type=str(item.get("capture_type") or f"zalo_{key}"),
                    expected_sha256=str(item.get("sha256") or "") or None,
                    created_at=str(item.get("created_at") or "") or None,
                    retention_days=retention_days,
                )
                item.update(
                    {
                        "sha256": artifact.get("sha256"),
                        "size_bytes": artifact.get("size_bytes"),
                        "mime_type": artifact.get("mime_type"),
                        "created_at": artifact.get("created_at"),
                        "capture_type": artifact.get("capture_type"),
                        "evidence_status": artifact.get("status"),
                        "artifact_id": artifact.get("id"),
                    }
                )
                statuses.append(str(artifact.get("status")))
            evidence["evidence_statuses"] = statuses
            if any(status in {MISSING, EXPIRED, QUARANTINED} for status in statuses):
                self._downgrade_result(result, statuses)
        return results

    @staticmethod
    def _downgrade_result(result: dict[str, Any], statuses: list[str]) -> None:
        result["evidence_state"] = "INSUFFICIENT_EVIDENCE"
        result["submitted_for_bcnb"] = False
        result["status"] = "⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công"
        detail = "Evidence lifecycle không khả dụng: " + ", ".join(sorted(set(statuses)))
        old = str(result.get("status_detail") or "").strip()
        result["status_detail"] = f"{old}; {detail}" if old else detail
        evidence = result.setdefault("evidence", {})
        errors = evidence.setdefault("validation_errors", [])
        if detail not in errors:
            errors.append(detail)

    @staticmethod
    def _filename_for_job(public_path: str, job_id: int) -> str | None:
        prefix = f"/api/evidence/{job_id}/"
        legacy = f"/static/screenshots/job-{job_id}/"
        if public_path.startswith(prefix):
            filename = public_path[len(prefix):]
        elif public_path.startswith(legacy):
            filename = public_path[len(legacy):]
        else:
            return None
        if "/" in filename or "\\" in filename or not filename.lower().endswith(".png"):
            return None
        return filename

    @classmethod
    def _iter_images(cls, value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                yield value
            for item in value.values():
                yield from cls._iter_images(item)
        elif isinstance(value, list):
            for item in value:
                yield from cls._iter_images(item)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        return {
            "id": row[0],
            "job_id": row[1],
            "audit_id": row[2],
            "public_path": row[3],
            "stored_path": row[4],
            "sha256": str(row[5] or "").upper() or None,
            "size_bytes": row[6],
            "mime_type": row[7],
            "created_at": row[8],
            "capture_type": row[9],
            "status": row[10],
            "expires_at": row[11],
            "last_verified_at": row[12],
            "quarantined_at": row[13],
            "tombstoned_at": row[14],
            "error_message": row[15],
        }
