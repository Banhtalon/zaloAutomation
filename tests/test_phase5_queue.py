import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import db.database as database
from repositories.job_repository import JobRepository, JobQueueFullError
from services.job_dispatcher import JobDispatcher
from services.gateway_resilience import GatewayCircuitBreaker, GatewayCircuitOpen
from core.settings import AppSettings
from main import create_app
from fastapi.testclient import TestClient
from services.profile_lock import InterProcessFileLock, ProfileLockTimeout


class Phase5ProfileLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_owners_cannot_share_profile_lock(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "profile.lock"
            first = InterProcessFileLock(path, timeout_seconds=0.15, poll_seconds=0.02)
            second = InterProcessFileLock(path, timeout_seconds=0.15, poll_seconds=0.02)
            await first.acquire()
            with self.assertRaises(ProfileLockTimeout):
                await second.acquire()
            await first.release()
            await second.acquire()
            await second.release()
            self.assertFalse(path.exists())

    async def test_gateway_retry_is_bounded_and_opens_circuit(self):
        breaker = GatewayCircuitBreaker(
            max_retries=1,
            backoff_seconds=0,
            failure_threshold=2,
            cooldown_seconds=30,
        )
        operation = AsyncMock(side_effect=RuntimeError("gateway down"))
        with self.assertRaises(RuntimeError):
            await breaker.call(operation)
        self.assertEqual(operation.await_count, 2)
        with self.assertRaises(GatewayCircuitOpen):
            await breaker.call(operation)
        self.assertEqual(operation.await_count, 2)

    async def test_stale_owner_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "profile.lock"
            path.write_text('{"owner_id":"dead"}', encoding="utf-8")
            old = 1.0
            os.utime(path, (old, old))
            lock = InterProcessFileLock(
                path,
                timeout_seconds=0.5,
                stale_after_seconds=1,
                poll_seconds=0.02,
            )
            await lock.acquire()
            await lock.release()

    async def test_dead_owner_pid_is_immediately_reclaimed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "profile.lock"
            path.write_text(
                json.dumps({"owner_id": "dead", "pid": 999999, "acquired_at": time.time()}),
                encoding="utf-8",
            )
            lock = InterProcessFileLock(
                path,
                timeout_seconds=0.5,
                stale_after_seconds=600,
                poll_seconds=0.02,
            )
            await lock.acquire()
            await lock.release()
            self.assertFalse(path.exists())

    async def test_second_process_is_blocked_by_profile_lock(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "profile.lock")
            child_code = textwrap.dedent(
                """
                import asyncio, sys
                from services.profile_lock import InterProcessFileLock
                async def main():
                    lock = InterProcessFileLock(sys.argv[1], timeout_seconds=2)
                    await lock.acquire()
                    print('LOCKED', flush=True)
                    await asyncio.sleep(1)
                    await lock.release()
                asyncio.run(main())
                """
            )
            child = subprocess.Popen(
                [sys.executable, "-u", "-c", child_code, path],
                cwd=str(Path(__file__).resolve().parents[1]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                ready = await asyncio.to_thread(child.stdout.readline)
                self.assertEqual(ready.strip(), "LOCKED")
                contender = InterProcessFileLock(
                    path,
                    timeout_seconds=0.15,
                    poll_seconds=0.02,
                )
                with self.assertRaises(ProfileLockTimeout):
                    await contender.acquire()
            finally:
                child.wait(timeout=5)
                if child.stdout:
                    child.stdout.close()
                if child.stderr:
                    child.stderr.close()


class Phase5RepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "phase5.db")
        database.init_db()
        self.jobs = JobRepository()

    async def asyncTearDown(self):
        database.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    async def test_claim_renew_and_shutdown_reconcile_are_durable(self):
        safe_id = await self.jobs.create_job(
            "SAFE", {"version": 5}, recoverable=True, mutates_external=False
        )
        unsafe_id = await self.jobs.create_job(
            "WRITE", {"version": 5}, recoverable=False, mutates_external=True
        )
        self.assertTrue(
            await self.jobs.claim_lease(safe_id, "worker-a", 30, deadline_seconds=60)
        )
        safe = await self.jobs.get_job(safe_id)
        self.assertEqual(safe["status"], "RUNNING")
        self.assertEqual(safe["lease_owner"], "worker-a")
        self.assertTrue(await self.jobs.claim_lease(unsafe_id, "worker-a", 30))
        safe_claim_events = await self.jobs.list_events(safe_id)
        self.assertTrue(
            any("Worker đã nhận lease cho job." in (e.get("message") or "") for e in safe_claim_events)
        )

        await self.jobs.reconcile_shutdown([safe_id, unsafe_id])
        safe_reconciled = await self.jobs.get_job(safe_id)
        self.assertEqual(safe_reconciled["status"], "PENDING")
        self.assertEqual(safe_reconciled["message"], "Worker đã đóng an toàn; job sẵn sàng chạy lại.")
        safe_events = await self.jobs.list_events(safe_id)
        self.assertTrue(
            any("Job đọc được xếp lại sau khi worker dừng." in (e.get("message") or "") for e in safe_events)
        )

        unsafe_reconciled = await self.jobs.get_job(unsafe_id)
        self.assertEqual(unsafe_reconciled["status"], "FAILED")
        self.assertEqual(unsafe_reconciled["message"], "Worker đóng an toàn; job thay đổi bên ngoài cần kiểm tra.")
        unsafe_events = await self.jobs.list_events(unsafe_id)
        self.assertTrue(
            any("Job ghi bên ngoài bị cô lập khi worker dừng." in (e.get("message") or "") for e in unsafe_events)
        )


class Phase5DispatcherQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_admission_is_bounded_and_reports_capacity(self):
        dispatcher = JobDispatcher(
            job_repo=object(),
            upload_store=object(),
            worker_runtime=object(),
            max_concurrent_jobs=1,
            max_queued_jobs=1,
            job_timeout_seconds=5,
        )
        async def slow_job(_job_id):
            await asyncio.sleep(0.2)

        dispatcher._run_job = AsyncMock(side_effect=slow_job)
        first = dispatcher.schedule_job(1)
        second = dispatcher.schedule_job(2)
        with self.assertRaises(JobQueueFullError):
            dispatcher.schedule_job(3)
        self.assertTrue(dispatcher.status()["queue_saturated"])
        await asyncio.gather(first, second)
        self.assertEqual(dispatcher.status()["active_jobs"], 0)


class Phase5CwdTests(unittest.TestCase):
    def test_app_uses_absolute_configured_paths_from_another_cwd(self):
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as root:
            try:
                os.chdir(root)
                settings = AppSettings.from_config().with_overrides(
                    database_path=os.path.join(root, "app.db"),
                    upload_root=os.path.join(root, "uploads"),
                    log_dir=os.path.join(root, "logs"),
                    evidence_root=os.path.join(root, "evidence"),
                    browser_profile_path=os.path.join(root, "zalo-session"),
                    lock_root=os.path.join(root, "locks"),
                    min_free_disk_bytes=1,
                    bcnb_dry_run=True,
                    google_form_mode="dry-run",
                )
                app = create_app(settings)
                with TestClient(app) as client:
                    self.assertEqual(client.get("/").status_code, 200)
                self.assertTrue(os.path.isabs(app.state.container.settings.database_path))
                self.assertTrue(os.path.isabs(app.state.container.worker_runtime.profile_lock_path))
                self.assertTrue(os.path.isabs(app.state.container.worker_runtime.external.browser.profile_path))
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
