import json
import os
import secrets
import tempfile
import unittest

import db.database as database
from db.database import get_db_connection, init_db
from repositories.job_repository import JobRepository
from repositories.lms_repository import ConfirmationError, LmsRepository


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

    async def _create_confirmation(self):
        analysis_job_id = await self.jobs.create_job("LMS_ANALYSIS")
        token = secrets.token_urlsafe(32)
        plan = {
            "kind": "lms_preview",
            "can_confirm": True,
            "week": 28,
            "year": 2026,
            "missing_classes": ["VT-JSB14"],
            "observed_classes": [
                {"class_code": "VT-JSB14", "checked": False},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            "classes_to_tick": ["VT-JSB15"],
            "dry_run": True,
        }
        await self.lms.save_confirmation(analysis_job_id, token, "digest", plan)
        return analysis_job_id, token

    async def test_wrong_token_is_rejected(self):
        analysis_job_id, _token = await self._create_confirmation()
        with self.assertRaises(ConfirmationError):
            await self.lms.consume_confirmation(analysis_job_id, "wrong-token")

    async def test_confirmation_is_one_use_and_creates_execution_job(self):
        analysis_job_id, token = await self._create_confirmation()
        result = await self.lms.consume_confirmation(analysis_job_id, token)
        self.assertGreater(result["job_id"], 0)
        self.assertEqual(result["plan"]["classes_to_tick"], ["VT-JSB15"])
        created_job = await self.jobs.get_job(result["job_id"])
        self.assertEqual(created_job["job_type"], "LMS_AUTOMATION")
        worker_job = await self.jobs.get_job_record(result["job_id"])
        self.assertEqual(
            worker_job["request_payload"]["execution_mode"], "dry-run"
        )
        self.assertTrue(result["dry_run"])
        self.assertNotIn(
            "confirmation_token",
            json.dumps(worker_job["request_payload"], ensure_ascii=False),
        )
        with self.assertRaises(ConfirmationError):
            await self.lms.consume_confirmation(analysis_job_id, token)

    async def test_expired_confirmation_is_rejected(self):
        analysis_job_id, token = await self._create_confirmation()
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE lms_confirmations SET created_at = '2000-01-01 00:00:00' WHERE analysis_job_id = ?",
                (analysis_job_id,),
            )
            await db.commit()
        with self.assertRaises(ConfirmationError):
            await self.lms.consume_confirmation(
                analysis_job_id, token, max_age_minutes=30
            )

    async def test_config_mode_flip_requires_new_analysis(self):
        analysis_job_id, token = await self._create_confirmation()
        with self.assertRaises(ConfirmationError):
            await self.lms.consume_confirmation(
                analysis_job_id,
                token,
                dry_run=False,
            )


if __name__ == "__main__":
    unittest.main()
