from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest

import db.database as database
from repositories.job_repository import (
    IdempotencyConflictError,
    IdempotencyKeyExpiredError,
    JobRepository,
)
from services.googleform_idempotency import (
    actor_key,
    key_digest,
    payload_hash,
    persisted_key_digest,
    target_hash,
)


class GoogleFormIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "idempotency.db")
        database.init_db()
        self.jobs = JobRepository()
        self.students = [{"name": " Nguyễn  An ", "class_code": "vt-gb40"}]
        self.kwargs = {
            "key_digest": key_digest("client-key-123456789"),
            "operation": "GOOGLEFORM_SUBMIT",
            "payload_hash": payload_hash(self.students, "dry-run"),
            "actor_key": actor_key("local-admin", "Admin"),
            "target_hash": target_hash(self.students),
            "execution_mode": "dry-run",
            "ttl_hours": 24,
            "retryable": True,
            "recoverable": True,
        }

    async def asyncTearDown(self):
        database.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    async def _create(self, **overrides):
        values = {**self.kwargs, **overrides}
        return await self.jobs.create_idempotent_job(
            "GOOGLEFORM_SUBMIT",
            {"students": self.students, "idempotency_key": values["key_digest"]},
            **values,
        )

    async def test_replay_returns_original_job_without_creating_another(self):
        first = await self._create()
        replay = await self._create()
        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(first["job_id"], replay["job_id"])
        connection = sqlite3.connect(database.DB_PATH)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM scan_jobs").fetchone()[0], 1
            )
        finally:
            connection.close()

    async def test_worker_digest_is_stable_for_redacted_key(self):
        digest = key_digest("client-key-123456789")
        self.assertEqual(persisted_key_digest(digest), digest)
        self.assertNotEqual(persisted_key_digest("client-key-123456789"), "client-key-123456789")

    async def test_payload_actor_target_or_mode_change_conflicts(self):
        await self._create()
        for field, value in (
            ("payload_hash", "different-payload"),
            ("actor_key", "different-actor"),
            ("target_hash", "different-target"),
            ("execution_mode", "production"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(IdempotencyConflictError):
                    await self._create(**{field: value})

    async def test_expired_key_fails_closed(self):
        first = await self._create()
        connection = sqlite3.connect(database.DB_PATH)
        try:
            connection.execute(
                "UPDATE googleform_idempotency SET expires_at = '2000-01-01T00:00:00+00:00' "
                "WHERE key_digest = ?",
                (self.kwargs["key_digest"],),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(IdempotencyKeyExpiredError):
            await self._create()
        self.assertIsNotNone(await self.jobs.get_job(first["job_id"]))

    async def test_concurrent_retries_share_one_claim(self):
        results = await asyncio.gather(*[self._create() for _ in range(6)])
        self.assertEqual({result["job_id"] for result in results}, {results[0]["job_id"]})
        self.assertEqual(sum(bool(result["created"]) for result in results), 1)

    async def test_terminal_job_updates_idempotency_record(self):
        result = await self._create()
        await self.jobs.mark_running(result["job_id"])
        await self.jobs.update_job(result["job_id"], status="SUCCESS", progress=100)
        connection = sqlite3.connect(database.DB_PATH)
        try:
            row = connection.execute(
                "SELECT status, completed_at FROM googleform_idempotency WHERE key_digest = ?",
                (self.kwargs["key_digest"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "SUCCESS")
        self.assertTrue(row[1])


if __name__ == "__main__":
    unittest.main()
