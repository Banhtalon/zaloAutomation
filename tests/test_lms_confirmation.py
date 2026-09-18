import asyncio
import json
import os
import secrets
import tempfile
import unittest

import db.database as database
from db.database import get_db_connection, init_db
from repositories.job_repository import JobRepository
from repositories.lms_repository import ConfirmationError, LmsRepository
from services.lms_plan import build_lms_plan


class LmsConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        init_db()
        self.jobs = JobRepository()
        self.lms = LmsRepository()

    async def asyncTearDown(self):
        database.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    async def _create_confirmation(self, *, dry_run=True):
        analysis_job_id = await self.jobs.create_job("LMS_ANALYSIS")
        token = secrets.token_urlsafe(32)
        plan = build_lms_plan(
            ["VT-JSB14"],
            [
                {"class_code": "VT-JSB14", "checked": False},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            week=28,
            year=2026,
            dry_run=dry_run,
        )
        await self.lms.save_confirmation(
            analysis_job_id,
            token,
            plan["plan_digest"],
            plan,
        )
        return analysis_job_id, token, plan

    async def test_wrong_token_is_rejected_without_revoking_valid_token(self):
        analysis_job_id, token, _plan = await self._create_confirmation()
        with self.assertRaises(ConfirmationError) as caught:
            await self.lms.consume_confirmation(analysis_job_id, "wrong-token")
        self.assertEqual(caught.exception.code, "LMS_CONFIRMATION_TOKEN_INVALID")
        result = await self.lms.consume_confirmation(analysis_job_id, token)
        self.assertTrue(result["dry_run"])

    async def test_confirmation_is_one_use_and_creates_execution_job(self):
        analysis_job_id, token, _plan = await self._create_confirmation()
        result = await self.lms.consume_confirmation(
            analysis_job_id,
            token,
            dry_run=True,
        )
        self.assertGreater(result["job_id"], 0)
        self.assertEqual(result["plan"]["classes_to_tick"], ["VT-JSB15"])
        created_job = await self.jobs.get_job(result["job_id"])
        self.assertEqual(created_job["job_type"], "LMS_AUTOMATION")
        worker_job = await self.jobs.get_job_record(result["job_id"])
        self.assertEqual(worker_job["request_payload"]["execution_mode"], "dry-run")
        self.assertTrue(result["dry_run"])
        self.assertNotIn(
            "confirmation_token",
            json.dumps(worker_job["request_payload"], ensure_ascii=False),
        )
        with self.assertRaises(ConfirmationError) as caught:
            await self.lms.consume_confirmation(analysis_job_id, token, dry_run=True)
        self.assertEqual(caught.exception.code, "LMS_CONFIRMATION_REPLAY")

    async def test_expired_confirmation_is_rejected_and_revoked(self):
        analysis_job_id, token, _plan = await self._create_confirmation()
        async with get_db_connection() as db:
            await db.execute(
                """
                UPDATE lms_confirmations
                SET created_at = '2000-01-01T00:00:00+00:00',
                    expires_at = '2000-01-01T00:00:00+00:00'
                WHERE analysis_job_id = ?
                """,
                (analysis_job_id,),
            )
            await db.commit()
        with self.assertRaises(ConfirmationError) as caught:
            await self.lms.consume_confirmation(
                analysis_job_id,
                token,
                max_age_minutes=30,
            )
        self.assertEqual(caught.exception.code, "LMS_CONFIRMATION_EXPIRED")
        with self.assertRaises(ConfirmationError) as replay:
            await self.lms.consume_confirmation(analysis_job_id, token)
        self.assertEqual(replay.exception.code, "LMS_CONFIRMATION_REVOKED")

    async def test_production_preview_then_dry_run_is_rejected_and_revoked(self):
        analysis_job_id, token, _plan = await self._create_confirmation(dry_run=False)
        with self.assertRaises(ConfirmationError) as caught:
            await self.lms.consume_confirmation(
                analysis_job_id,
                token,
                dry_run=True,
            )
        self.assertEqual(caught.exception.code, "LMS_CONFIRMATION_MODE_MISMATCH")
        with self.assertRaises(ConfirmationError) as revoked:
            await self.lms.consume_confirmation(
                analysis_job_id,
                token,
                dry_run=False,
            )
        self.assertEqual(revoked.exception.code, "LMS_CONFIRMATION_REVOKED")

    async def test_dry_run_preview_then_production_is_rejected(self):
        analysis_job_id, token, _plan = await self._create_confirmation(dry_run=True)
        with self.assertRaises(ConfirmationError) as caught:
            await self.lms.consume_confirmation(
                analysis_job_id,
                token,
                dry_run=False,
            )
        self.assertEqual(caught.exception.code, "LMS_CONFIRMATION_MODE_MISMATCH")

    async def test_modified_payload_is_rejected_before_execution_job_creation(self):
        analysis_job_id, token, plan = await self._create_confirmation()
        tampered = dict(plan)
        tampered["week"] = 29
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE lms_confirmations SET plan_payload = ? WHERE analysis_job_id = ?",
                (json.dumps(tampered, ensure_ascii=False), analysis_job_id),
            )
            await db.commit()
        with self.assertRaises(ConfirmationError) as caught:
            await self.lms.consume_confirmation(analysis_job_id, token, dry_run=True)
        self.assertEqual(caught.exception.code, "LMS_PLAN_DIGEST_MISMATCH")
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM scan_jobs WHERE job_type = 'LMS_AUTOMATION'"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_wrong_target_is_rejected_before_execution_job_creation(self):
        analysis_job_id, token, plan = await self._create_confirmation()
        tampered = dict(plan)
        tampered["target"] = "OTHER_SYSTEM"
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE lms_confirmations SET plan_payload = ? WHERE analysis_job_id = ?",
                (json.dumps(tampered, ensure_ascii=False), analysis_job_id),
            )
            await db.commit()
        with self.assertRaises(ConfirmationError) as caught:
            await self.lms.consume_confirmation(analysis_job_id, token, dry_run=True)
        self.assertEqual(caught.exception.code, "LMS_PLAN_TARGET_INVALID")

    async def test_concurrent_consume_creates_exactly_one_execution_job(self):
        analysis_job_id, token, _plan = await self._create_confirmation()
        results = await asyncio.gather(
            self.lms.consume_confirmation(analysis_job_id, token, dry_run=True),
            self.lms.consume_confirmation(analysis_job_id, token, dry_run=True),
            return_exceptions=True,
        )
        successes = [item for item in results if not isinstance(item, Exception)]
        failures = [item for item in results if isinstance(item, ConfirmationError)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "LMS_CONFIRMATION_REPLAY")
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM scan_jobs WHERE job_type = 'LMS_AUTOMATION'"
            ) as cursor:
                self.assertEqual((await cursor.fetchone())[0], 1)


if __name__ == "__main__":
    unittest.main()
