"""Hermetic contract checks for the fixed PTI10 read-only canary."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from services.external_gateways import CleanupCode, CleanupResult
from services.zalo_evidence import SUBMITTED_VERIFIED, build_scan_result
from services.zalo_readonly_canary import (
    CANARY_DESCRIPTOR,
    CANARY_READY,
    PTI10_DESCRIPTOR,
    CanaryBounds,
    ReadOnlyCanaryRunner,
)


PTI10_TARGET = {
    "class_code": "VT-C4K-PTI10",
    "group_name": "MindX - Lập trình App -PTI10 CN (8H-10H)",
    "person": "PTI10_READ_ONLY_CANARY",
    "week": 37,
    "year": 2026,
    "lesson": "13",
    "class_date": "13/09/2026",
    "class_date_iso": "2026-09-13",
    "zalo_deadline": "2026-09-16T23:59:59+07:00",
}


class _FakeLocator:
    async def count(self) -> int:
        return 1

    async def is_visible(self) -> bool:
        return True


class _FakePage:
    url = ""

    async def is_closed(self) -> bool:
        return False

    async def goto(self, url: str, *, timeout: int):
        self.url = url
        return SimpleNamespace(status=200)

    def locator(self, selector: str) -> _FakeLocator:
        if selector not in {"body nav", "#contact-search-input"}:
            raise AssertionError(f"unexpected readiness selector: {selector}")
        return _FakeLocator()


class _FakeSession:
    def __init__(self, page: _FakePage) -> None:
        self.context = SimpleNamespace(pages=[page])

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
        return False


class _FakeGateway:
    def __init__(self, profile_path: Path, page: _FakePage) -> None:
        self.profile_path = str(profile_path)
        self.page = page
        self.last_cleanup_result = CleanupResult(code=CleanupCode.CLEANUP_OK)

    def persistent_session(self, **_kwargs):
        return _FakeSession(self.page)


class Pti10CanaryContractTests(unittest.IsolatedAsyncioTestCase):
    def test_descriptor_is_fixed_to_owner_target(self):
        self.assertEqual(PTI10_DESCRIPTOR.as_dict(), {
            "class_code": "VT-C4K-PTI10",
            "group_name": "MindX - Lập trình App -PTI10 CN (8H-10H)",
            "week": 37,
            "year": 2026,
            "lesson": "13",
            "class_date_iso": "2026-09-13",
            "message_window_start": "2026-09-13",
            "message_window_end": "2026-09-16",
        })
        self.assertEqual(PTI10_DESCRIPTOR.zalo_deadline, PTI10_TARGET["zalo_deadline"])

    async def test_pti10_factory_uses_literal_target_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory(prefix="pti10-canary-") as directory:
            root = Path(directory)
            runner = ReadOnlyCanaryRunner.from_pti10(
                profile_path=root / "profile",
                profile_lock_path=root / "profile.lock",
                artifact_root=root / "artifacts",
            )
            page = _FakePage()
            runner.browser_gateway = _FakeGateway(root / "profile", page)
            raw = build_scan_result(
                group=PTI10_TARGET["group_name"],
                person=PTI10_TARGET["person"],
                actual_group_name=PTI10_TARGET["group_name"],
                scanned_at=datetime(2026, 9, 16, 12, 0),
                messages_checked=1,
                classifier_result={
                    "engine": "synthetic",
                    "decision": "SUBMITTED_ON_TIME",
                    "report_found": True,
                    "evaluated_messages": 1,
                    "candidate_reports": 1,
                },
                required_window_start=datetime(2026, 9, 13),
                required_window_end=datetime(2026, 9, 16, 23, 59, 59),
                oldest_message_date=datetime(2026, 9, 13),
                newest_message_date=datetime(2026, 9, 16),
                images={"report": {"path": "synthetic.png"}},
                report_found=True,
                report_detail="Synthetic report",
                submitted_for_bcnb=True,
                verified_report_state=SUBMITTED_VERIFIED,
            )
            raw["scan_target"] = dict(PTI10_TARGET)

            async def fake_sync(*_args, **_kwargs):
                return {"verified": True, "decision": "SYNC_NOT_REQUIRED"}

            async def fake_scan(*_args, **_kwargs):
                return raw

            with (
                patch("services.zalo_readonly_canary.ensure_zalo_history_synced", new=fake_sync),
                patch("services.zalo_readonly_canary._scan_one_target", new=fake_scan),
            ):
                result = await runner.run()

            self.assertEqual(result["status"], CANARY_READY)
            self.assertEqual(result["reason_code"], "SUBMITTED_ON_TIME")
            self.assertEqual(result["descriptor"], PTI10_DESCRIPTOR.as_dict())
            self.assertTrue(result["cleanup"]["complete"])

            runner.descriptor = CANARY_DESCRIPTOR
            result = await runner.run()
            self.assertEqual(result["reason_code"], "INVALID_CANARY_DESCRIPTOR")

    def test_cli_does_not_accept_arbitrary_target_or_relative_paths(self):
        command = [sys.executable, "-m", "scripts.zalo_pti10_readonly_canary"]
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
        root = Path(__file__).parents[1]
        help_result = subprocess.run(
            [*command, "--help"], capture_output=True, text=True, encoding="utf-8", cwd=root, env=environment
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertNotIn("--class-code", help_result.stdout)
        self.assertNotIn("--group-name", help_result.stdout)
        self.assertNotIn("--descriptor", help_result.stdout)

        invalid = subprocess.run(
            [*command, "--profile-path", "relative", "--profile-lock-path", "relative", "--artifact-root", "relative"],
            capture_output=True, text=True, encoding="utf-8", cwd=root, env=environment
        )
        self.assertEqual(invalid.returncode, 2)
        payload = json.loads(invalid.stdout)
        self.assertEqual(payload["phase"], PTI10_DESCRIPTOR.phase)
        self.assertEqual(payload["status"], "BROWSER_ERROR")
        self.assertEqual(payload["reason_code"], "INVALID_CANARY_BOOTSTRAP")

    def test_shared_bounds_and_write_denials_remain_unchanged(self):
        self.assertEqual(CanaryBounds().max_runtime_seconds, 300.0)
        self.assertFalse(ReadOnlyCanaryRunner.capabilities()["write_allowed"])
        self.assertIn("BCNB", ReadOnlyCanaryRunner.capabilities()["denied_capabilities"])


if __name__ == "__main__":
    unittest.main()
