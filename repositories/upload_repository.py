from core.time_utils import utc_iso

from db.database import get_db_connection


class UploadClaimError(ValueError):
    pass


def _now() -> str:
    return utc_iso()


class UploadRepository:
    async def create(
        self,
        *,
        upload_id: str,
        purpose: str,
        stored_path: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        state: str = "STAGED",
        job_id: int | None = None,
    ) -> None:
        now = _now()
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO job_uploads (
                    upload_id, purpose, stored_path, mime_type, size_bytes,
                    sha256, state, job_id, created_at, claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    upload_id,
                    purpose,
                    stored_path,
                    mime_type,
                    size_bytes,
                    sha256,
                    state,
                    job_id,
                    now,
                    now if state == "CLAIMED" else None,
                ),
            )
            await db.commit()

    async def create_many_claimed(self, records: list[dict]) -> None:
        """Insert a complete clone batch or leave no rows behind."""
        if not records:
            return
        now = _now()
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.executemany(
                    """
                    INSERT INTO job_uploads (
                        upload_id, purpose, stored_path, mime_type, size_bytes,
                        sha256, state, job_id, created_at, claimed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'CLAIMED', ?, ?, ?)
                    """,
                    [
                        (
                            record["upload_id"],
                            record["purpose"],
                            record["stored_path"],
                            record["mime_type"],
                            record["size_bytes"],
                            record["sha256"],
                            record["job_id"],
                            now,
                            now,
                        )
                        for record in records
                    ],
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def get_many(self, upload_ids: list[str]) -> list[dict]:
        if not upload_ids:
            return []
        placeholders = ",".join("?" for _ in upload_ids)
        async with get_db_connection() as db:
            async with db.execute(
                f"""
                SELECT upload_id, purpose, stored_path, mime_type, size_bytes,
                       sha256, state, job_id, created_at, claimed_at, deleted_at
                FROM job_uploads WHERE upload_id IN ({placeholders})
                """,
                tuple(upload_ids),
            ) as cursor:
                rows = await cursor.fetchall()
        by_id = {row[0]: self._row(row) for row in rows}
        return [by_id[upload_id] for upload_id in upload_ids if upload_id in by_id]

    async def claim(
        self,
        *,
        upload_ids: list[str],
        purpose: str,
        job_id: int,
        new_paths: dict[str, str],
    ) -> None:
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            for upload_id in upload_ids:
                async with db.execute(
                    """
                    SELECT purpose, state, job_id FROM job_uploads
                    WHERE upload_id = ?
                    """,
                    (upload_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if not row:
                    await db.rollback()
                    raise UploadClaimError(f"Upload {upload_id} không tồn tại.")
                if row[0] != purpose:
                    await db.rollback()
                    raise UploadClaimError("Upload không đúng mục đích sử dụng.")
                if row[1] != "STAGED" or row[2] is not None:
                    await db.rollback()
                    raise UploadClaimError("Upload đã thuộc về một job khác.")

            for upload_id in upload_ids:
                cursor = await db.execute(
                    """
                    UPDATE job_uploads
                    SET state = 'CLAIMED', job_id = ?, stored_path = ?, claimed_at = ?
                    WHERE upload_id = ? AND state = 'STAGED' AND job_id IS NULL
                    """,
                    (job_id, new_paths[upload_id], _now(), upload_id),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    raise UploadClaimError("Upload vừa được một job khác nhận.")
            await db.commit()

    async def list_for_job(self, job_id: int, purpose: str | None = None) -> list[dict]:
        params: list[int | str] = [job_id]
        purpose_clause = ""
        if purpose:
            purpose_clause = "AND purpose = ?"
            params.append(purpose)
        async with get_db_connection() as db:
            async with db.execute(
                f"""
                SELECT upload_id, purpose, stored_path, mime_type, size_bytes,
                       sha256, state, job_id, created_at, claimed_at, deleted_at
                FROM job_uploads
                WHERE job_id = ? AND state = 'CLAIMED' {purpose_clause}
                ORDER BY created_at, upload_id
                """,
                tuple(params),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row(row) for row in rows]

    async def list_expired(self, cutoff: str) -> list[dict]:
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT upload_id, purpose, stored_path, mime_type, size_bytes,
                       sha256, state, job_id, created_at, claimed_at, deleted_at
                FROM job_uploads
                WHERE deleted_at IS NULL AND created_at < ?
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row(row) for row in rows]

    async def list_live_paths(self) -> list[str]:
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT stored_path FROM job_uploads
                WHERE deleted_at IS NULL AND state IN ('STAGED', 'CLAIMED')
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def mark_deleted(self, upload_id: str) -> None:
        async with get_db_connection() as db:
            await db.execute(
                """
                UPDATE job_uploads SET state = 'DELETED', deleted_at = ?
                WHERE upload_id = ? AND deleted_at IS NULL
                """,
                (_now(), upload_id),
            )
            await db.commit()

    @staticmethod
    def _row(row) -> dict:
        return {
            "upload_id": row[0],
            "purpose": row[1],
            "stored_path": row[2],
            "mime_type": row[3],
            "size_bytes": row[4],
            "sha256": row[5],
            "state": row[6],
            "job_id": row[7],
            "created_at": row[8],
            "claimed_at": row[9],
            "deleted_at": row[10],
        }
