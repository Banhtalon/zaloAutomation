import os
import tempfile
import unittest

import db.database as database
import services.job_dispatcher as dispatcher
from repositories.job_repository import JobRepository, JobStateError


class JobLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        database.init_db()
        self.jobs = JobRepository()

    async def asyncTearDown(self):
        database.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    async def test_cancel_wins_race_with_success_and_terminal_is_immutable(self):
        job_id = await self.jobs.create_job("READ", {"version": 2})
        self.assertTrue(await self.jobs.mark_running(job_id))
        await self.jobs.request_cancel(job_id)
        await self.jobs.update_job(job_id, status="SUCCESS", progress=100)
        job = await self.jobs.get_job(job_id)
        self.assertEqual(job["status"], "CANCELLED")
        self.assertFalse(await self.jobs.update_job(job_id, status="RUNNING"))
        self.assertEqual((await self.jobs.get_job(job_id))["status"], "CANCELLED")

    async def test_heartbeat_only_updates_running_job(self):
        job_id = await self.jobs.create_job("READ", {"version": 2})
        self.assertFalse(await self.jobs.heartbeat(job_id))
        await self.jobs.mark_running(job_id)
        self.assertTrue(await self.jobs.heartbeat(job_id))
        self.assertIsNotNone((await self.jobs.get_job(job_id))["heartbeat_at"])
        await self.jobs.update_job(job_id, status="SUCCESS", progress=100)
        self.assertFalse(await self.jobs.heartbeat(job_id))

    async def test_recovery_requeues_read_only_but_quarantines_external_write(self):
        safe_id = await self.jobs.create_job(
            "SAFE", {"version": 2}, recoverable=True, mutates_external=False
        )
        unsafe_id = await self.jobs.create_job(
            "UNSAFE", {"version": 2}, recoverable=False, mutates_external=True
        )
        await self.jobs.mark_running(safe_id)
        await self.jobs.mark_running(unsafe_id)

        queued = await self.jobs.prepare_recovery()
        self.assertIn(safe_id, queued)
        self.assertNotIn(unsafe_id, queued)
        self.assertEqual((await self.jobs.get_job(safe_id))["status"], "PENDING")
        unsafe = await self.jobs.get_job(unsafe_id)
        self.assertEqual(unsafe["status"], "FAILED")
        self.assertIn("External mutation", unsafe["recovery_note"])

    async def test_retry_creates_new_attempt_and_blocks_non_retryable_job(self):
        retryable_id = await self.jobs.create_job(
            "TEST_READ", {"version": 2}, retryable=True, recoverable=True
        )
        await self.jobs.update_job(retryable_id, status="FAILED", progress=100)
        original_schedule = dispatcher.schedule_job
        dispatcher.schedule_job = lambda _job_id: None
        try:
            new_id = await dispatcher.retry_job(retryable_id)
        finally:
            dispatcher.schedule_job = original_schedule
        retried = await self.jobs.get_job(new_id)
        self.assertEqual(retried["retry_of_job_id"], retryable_id)
        self.assertEqual(retried["attempt"], 2)
        self.assertEqual(retried["status"], "PENDING")

        blocked_id = await self.jobs.create_job("WRITE", {"version": 2})
        await self.jobs.update_job(blocked_id, status="FAILED", progress=100)
        with self.assertRaises(JobStateError):
            await dispatcher.retry_job(blocked_id)

    async def test_exclusive_job_admission_rejects_active_job_atomically(self):
        active_id = await self.jobs.create_job("ZALO_SCAN", {"version": 1})
        await self.jobs.mark_running(active_id)

        with self.assertRaises(JobStateError):
            await self.jobs.create_job(
                "ZALO_SCAN",
                {"version": 2},
                reject_if_active=True,
            )

        self.assertEqual((await self.jobs.get_latest_job())['id'], active_id)


if __name__ == "__main__":
    unittest.main()
