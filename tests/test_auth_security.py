from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from core.auth import hash_password
from core.settings import AppSettings
from db.database import database_context
from main import create_app


def _settings(root: str, users):
    return AppSettings.from_config().with_overrides(
        database_path=os.path.join(root, "security.db"),
        upload_root=os.path.join(root, "uploads"),
        log_dir=os.path.join(root, "logs"),
        evidence_root=os.path.join(root, "screenshots"),
        min_free_disk_bytes=1,
        bcnb_dry_run=True,
        google_form_mode="dry-run",
        auth_mode="web",
        auth_allowed_hosts=("testserver", "127.0.0.1", "localhost"),
        auth_allowed_origins=(),
        auth_users=tuple(
            (username, role, hash_password(password, iterations=100_000))
            for username, role, password in users
        ),
    )


class AuthSecurityTests(unittest.TestCase):
    def test_auth_roles_csrf_origin_and_private_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(
                _settings(
                    root,
                    (
                        ("viewer", "Viewer", "viewer-pass"),
                        ("operator", "Operator", "operator-pass"),
                        ("admin", "Admin", "admin-pass"),
                    ),
                )
            )
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/health/live").status_code, 200)
                self.assertEqual(client.get("/api/logs").status_code, 401)
                self.assertEqual(client.post("/auto-discover").status_code, 401)
                self.assertEqual(
                    client.post("/auto-discover", headers={"Origin": "https://evil.example"}).status_code,
                    403,
                )

                login = client.post(
                    "/api/auth/login",
                    json={"username": "viewer", "password": "viewer-pass"},
                )
                self.assertEqual(login.status_code, 200)
                csrf = login.json()["csrf_token"]
                self.assertEqual(client.get("/api/logs").status_code, 200)
                self.assertEqual(client.post("/auto-discover").status_code, 403)
                self.assertEqual(
                    client.post(
                        "/auto-discover",
                        headers={"X-CSRF-Token": csrf},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.post(
                        "/api/googleform/mappings",
                        headers={"X-CSRF-Token": csrf},
                        json={"question": "Q", "field_type": "text", "value": "A"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.post(
                        "/api/googleform/mappings",
                        headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf},
                        json={"question": "Q", "field_type": "text", "value": "A"},
                    ).status_code,
                    403,
                )
                operator_login = client.post(
                    "/api/auth/login",
                    json={"username": "operator", "password": "operator-pass"},
                )
                operator_csrf = operator_login.json()["csrf_token"]
                self.assertEqual(
                    client.post(
                        "/api/googleform/run-ocr",
                        headers={"X-CSRF-Token": operator_csrf},
                        json={"upload_ids": []},
                    ).status_code,
                    422,
                )
                self.assertEqual(
                    client.post(
                        "/api/googleform/mappings",
                        headers={"X-CSRF-Token": operator_csrf},
                        json={"question": "Q", "field_type": "text", "value": "A"},
                    ).status_code,
                    403,
                )
                admin_login = client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "admin-pass"},
                )
                self.assertEqual(admin_login.status_code, 200)
                self.assertEqual(
                    client.post(
                        "/api/googleform/mappings",
                        headers={"X-CSRF-Token": admin_login.json()["csrf_token"]},
                        json={"question": "Q", "field_type": "text", "value": "A"},
                    ).status_code,
                    200,
                )
                session_id = client.cookies.get(app.state.container.auth.cookie_name)
                app.state.container.auth._sessions[session_id].expires_at = (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                )
                self.assertEqual(client.get("/api/logs").status_code, 401)

                login = client.post(
                    "/api/auth/login",
                    json={"username": "viewer", "password": "viewer-pass"},
                )
                csrf = login.json()["csrf_token"]
                evidence_root = Path(root) / "screenshots" / "job-1"
                evidence_root.mkdir(parents=True)
                evidence_file = evidence_root / "proof.png"
                evidence_file.write_bytes(b"phase4-proof")
                digest = hashlib.sha256(evidence_file.read_bytes()).hexdigest().upper()
                with database_context(app.state.container.settings.database_path):
                    job_id = _run_async(app.state.container.job_repo.create_job("TEST", {}))
                # The repository is async; use the app's event loop through a tiny helper below.
                self.assertEqual(job_id, 1)
                result = [
                    {
                        "evidence": {
                            "images": {
                                "latest": {
                                    "path": "/api/evidence/1/proof.png",
                                    "sha256": digest,
                                }
                            }
                        }
                    }
                ]
                with database_context(app.state.container.settings.database_path):
                    _run_async(app.state.container.job_repo.update_job(1, results=json.dumps(result)))
                valid = client.get("/api/evidence/1/proof.png")
                self.assertEqual(valid.status_code, 200)
                self.assertEqual(valid.headers["x-evidence-sha256"], digest)
                self.assertEqual(client.get("/static/screenshots/job-1/proof.png").status_code, 404)
                self.assertEqual(client.get("/api/evidence/2/proof.png").status_code, 404)
                self.assertEqual(client.get("/api/evidence/1/%2e%2e%2fproof.png").status_code, 404)

    def test_web_mode_without_users_fails_closed_and_login_is_throttled(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(_settings(root, ()))
            with TestClient(app) as client:
                self.assertEqual(
                    client.post(
                        "/api/auth/login",
                        json={"username": "nobody", "password": "bad"},
                    ).status_code,
                    503,
                )
            settings = _settings(root, (("admin", "Admin", "admin-pass"),))
            app = create_app(settings)
            with TestClient(app) as client:
                for _ in range(5):
                    self.assertEqual(
                        client.post(
                            "/api/auth/login",
                            json={"username": "admin", "password": "wrong"},
                        ).status_code,
                        401,
                    )
                self.assertEqual(
                    client.post(
                        "/api/auth/login",
                        json={"username": "admin", "password": "admin-pass"},
                    ).status_code,
                    401,
                )


def _run_async(awaitable):
    import asyncio

    return asyncio.run(awaitable)


if __name__ == "__main__":
    unittest.main()
