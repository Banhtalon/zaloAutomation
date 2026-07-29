from dataclasses import FrozenInstanceError
from contextlib import closing
import os
import re
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from core.settings import AppSettings
from logger_utils import log_message, use_log_manager
from main import create_app


def isolated_settings(root: str, name: str) -> AppSettings:
    return AppSettings.from_config().with_overrides(
        database_path=os.path.join(root, f"{name}.db"),
        upload_root=os.path.join(root, name, "uploads"),
        log_dir=os.path.join(root, name, "logs"),
        min_free_disk_bytes=1,
        bcnb_dry_run=True,
        google_form_mode="dry-run",
    )


class AppFactoryTests(unittest.TestCase):
    def test_two_apps_isolate_database_logs_dispatcher_and_locks(self):
        with tempfile.TemporaryDirectory() as root:
            app_a = create_app(isolated_settings(root, "a"))
            app_b = create_app(isolated_settings(root, "b"))
            container_a = app_a.state.container
            container_b = app_b.state.container

            self.assertIsNot(container_a.dispatcher, container_b.dispatcher)
            self.assertIsNot(
                container_a.worker_runtime.browser_lock,
                container_b.worker_runtime.browser_lock,
            )
            self.assertIsNot(container_a.upload_store._lock, container_b.upload_store._lock)

            with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
                with closing(
                    sqlite3.connect(container_a.settings.database_path)
                ) as connection:
                    connection.execute(
                        """
                        INSERT INTO scan_jobs (
                            job_type, status, created_at, progress, message
                        ) VALUES ('ISOLATION_A', 'FAILED', datetime('now'), 100, 'a')
                        """
                    )
                    connection.commit()

                latest_a = client_a.get("/api/jobs/latest").json()
                latest_b = client_b.get("/api/jobs/latest").json()
                self.assertEqual(latest_a["job_type"], "ISOLATION_A")
                self.assertIsNone(latest_b)

                with use_log_manager(container_a.log_manager):
                    log_message("only-app-a-marker", event="test.app_isolation")
                logs_a = client_a.get("/api/logs").json()["records"]
                logs_b = client_b.get("/api/logs").json()["records"]
                self.assertIn("only-app-a-marker", str(logs_a))
                self.assertNotIn("only-app-a-marker", str(logs_b))

                live_a = client_a.get("/api/health/live").json()
                live_b = client_b.get("/api/health/live").json()
                self.assertNotEqual(live_a["instance_id"], live_b["instance_id"])

            self.assertTrue(container_a.dispatcher.status()["shutting_down"])
            self.assertTrue(container_b.dispatcher.status()["shutting_down"])
            with self.assertRaises(RuntimeError):
                container_a.dispatcher.schedule_job(999)

    def test_same_app_lifecycle_can_restart_three_times(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(isolated_settings(root, "repeat"))
            container = app.state.container
            instance_ids = []
            for _ in range(3):
                with TestClient(app) as client:
                    response = client.get("/api/health/live")
                    self.assertEqual(response.status_code, 200)
                    instance_ids.append(response.json()["instance_id"])
                    self.assertFalse(container.dispatcher.status()["shutting_down"])
                self.assertEqual(container.dispatcher.status()["active_jobs"], 0)
                self.assertTrue(container.dispatcher.status()["shutting_down"])
            self.assertEqual(len(set(instance_ids)), 3)

    def test_local_security_and_request_limits(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(isolated_settings(root, "security"))
            with TestClient(app) as client:
                page = client.get("/")
                self.assertEqual(page.headers["cache-control"], "no-store")
                self.assertIn(
                    f"/static/js/groups.js?v={app.state.container.instance_id}",
                    page.text,
                )
                self.assertEqual(
                    client.get("/api/logs", headers={"Host": "evil.example"}).status_code,
                    403,
                )
                accepted = client.post(
                    "/api/uploads",
                    headers={
                        "Host": "localhost:8000",
                        "Origin": "http://localhost:8000",
                    },
                    json={},
                )
                self.assertEqual(accepted.status_code, 422)
                rejected = client.post(
                    "/api/uploads",
                    headers={
                        "Host": "localhost:8000",
                        "Origin": "http://localhost:9000",
                    },
                    json={},
                )
                self.assertEqual(rejected.status_code, 403)
                oversized = client.post(
                    "/api/uploads",
                    headers={"Content-Length": str(20 * 1024 * 1024)},
                    content=b"{}",
                )
                self.assertEqual(oversized.status_code, 413)
                bad_id = "bad id" * 100
                response = client.get(
                    "/api/health/live",
                    headers={"X-Request-ID": bad_id},
                )
                returned_id = response.headers["X-Request-ID"]
                self.assertNotEqual(returned_id, bad_id)
                self.assertRegex(returned_id, re.compile(r"^[a-f0-9]{32}$"))


class SettingsTests(unittest.TestCase):
    def test_settings_are_immutable_and_validate_overrides(self):
        settings = AppSettings.from_config()
        with self.assertRaises(FrozenInstanceError):
            settings.database_path = "changed.db"
        with self.assertRaises(ValueError):
            settings.with_overrides(job_heartbeat_seconds=0)
        with self.assertRaises(ValueError):
            settings.with_overrides(google_form_mode="unsafe")
        self.assertNotIn("BCNB_PASSWORD", repr(settings))
        self.assertNotIn("GEMINI_API_KEY", repr(settings))


if __name__ == "__main__":
    unittest.main()
