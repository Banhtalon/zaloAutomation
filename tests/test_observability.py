import os
from contextlib import closing
import tempfile
import unittest
import sqlite3

import db.database as database
from googleform_autosubmit.config import Config
from repositories.job_repository import JobRepository
from services.observability import collect_job_metrics, ready_status


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.DB_PATH
        self.original_upload_root = Config.UPLOAD_ROOT
        self.original_log_dir = Config.LOG_DIR
        database.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        Config.UPLOAD_ROOT = os.path.join(self.temp_dir.name, "uploads")
        Config.LOG_DIR = os.path.join(self.temp_dir.name, "logs")
        database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.original_db
        Config.UPLOAD_ROOT = self.original_upload_root
        Config.LOG_DIR = self.original_log_dir
        self.temp_dir.cleanup()

    async def test_metrics_are_aggregate_only(self):
        jobs = JobRepository()
        job_id = await jobs.create_job(
            "TEST_READ",
            {"private_name": "must-not-appear"},
            retryable=True,
            recoverable=True,
        )
        await jobs.update_job(job_id, status="FAILED", progress=100)
        metrics = collect_job_metrics(24)
        self.assertEqual(metrics["jobs"]["total"], 1)
        self.assertEqual(metrics["jobs"]["by_type"]["TEST_READ"]["FAILED"], 1)
        self.assertNotIn("must-not-appear", str(metrics))

    async def test_readiness_checks_local_dependencies_without_external_calls(self):
        status = ready_status()
        self.assertTrue(status["ready"])
        self.assertTrue(status["checks"]["database"]["ok"])
        if Config.BCNB_USERNAME:
            self.assertNotIn(Config.BCNB_USERNAME, str(status))
        if Config.BCNB_PASSWORD:
            self.assertNotIn(Config.BCNB_PASSWORD, str(status))

    async def test_metrics_indexes_exist(self):
        with closing(sqlite3.connect(database.DB_PATH)) as connection:
            indexes = {
                row[1]
                for table in ("scan_jobs", "external_action_audit", "job_uploads")
                for row in connection.execute(f"PRAGMA index_list({table})").fetchall()
            }
        self.assertIn("idx_scan_jobs_metrics_time", indexes)
        self.assertIn("idx_action_audit_created_at", indexes)
        self.assertIn("idx_job_uploads_created_at", indexes)


if __name__ == "__main__":
    unittest.main()
