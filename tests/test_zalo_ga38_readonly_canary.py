"""Hermetic contract tests for the fixed VT-C4K-GA38 canary extension."""

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
from services.zalo_evidence import (
    INSUFFICIENT_EVIDENCE,
    INSUFFICIENT_STATUS,
    NOT_FOUND_STATUS,
    NOT_FOUND_VERIFIED,
    SUBMITTED_LATE_STATUS,
    SUBMITTED_LATE_VERIFIED,
    SUBMITTED_STATUS,
    SUBMITTED_VERIFIED,
    build_scan_result,
)
from services.zalo_readonly_canary import (
    CANARY_DESCRIPTOR,
    CANARY_GROUP_IDENTITY_UNVERIFIED,
    CANARY_INSUFFICIENT_EVIDENCE,
    CANARY_MESSAGE_WINDOW_UNVERIFIED,
    CANARY_READY,
    CanaryBounds,
    ReadOnlyCanaryRunner,
)


GA38_TARGET = {
    "class_code": "VT-C4K-GA38",
    "group_name": "MindX- Lập trình Game VT-C4K-GA38 CN (18H-20H)",
    "person": "GA38_READ_ONLY_CANARY",
    "week": 35,
    "year": 2026,
    "lesson": "3",
    "class_date": "30/08/2026",
    "class_date_iso": "2026-08-30",
    "zalo_deadline": "2026-09-01T20:00:00+07:00",
}
ARMB_TARGET = {
    "class_code": "VT-ROB-ARMB19",
    "group_name": "MindX - Robotics-VT-ROB-ARMB19 CN (18H-20H)",
    "person": "ARMB19_READ_ONLY_CANARY",
    "week": 34,
    "year": 2026,
    "lesson": "1",
    "class_date": "23/08/2026",
    "class_date_iso": "2026-08-23",
    "zalo_deadline": "2026-08-27T23:59:59+07:00",
}


class _FakeLocator:
    async def count(self) -> int:
        return 1

    async def is_visible(self) -> bool:
        return True


class _FakePage:
    def __init__(self) -> None:
        self.url = ""
        self.closed = False
        self.goto_calls: list[tuple[str, int]] = []

    async def is_closed(self) -> bool:
        return self.closed

    async def goto(self, url: str, *, timeout: int):
        self.url = url
        self.goto_calls.append((url, timeout))
        return SimpleNamespace(status=200)

    def locator(self, selector: str) -> _FakeLocator:
        if selector not in {"body nav", "#contact-search-input"}:
            raise AssertionError(f"unexpected readiness selector: {selector}")
        return _FakeLocator()


class _FakeSessionContext:
    def __init__(self, page: _FakePage) -> None:
        self.context = SimpleNamespace(pages=[page])

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
        return False


class _FakeBrowserGateway:
    def __init__(self, profile_path: Path, page: _FakePage) -> None:
        self.profile_path = str(profile_path)
        self.page = page
        self.session_calls = 0
        self.last_cleanup_result = CleanupResult(code=CleanupCode.CLEANUP_OK)

    def persistent_session(self, **_kwargs):
        self.session_calls += 1
        return _FakeSessionContext(self.page)


class Ga38CanaryContractTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _factory():
        return getattr(ReadOnlyCanaryRunner, "from_ga38", None)

    def _new_runner(self, root: Path):
        factory = self._factory()
        self.assertTrue(callable(factory), "GA38 production factory is missing")
        if not callable(factory):
            return None, None, None
        page = _FakePage()
        gateway = _FakeBrowserGateway(root / "profile", page)
        runner = factory(
            profile_path=root / "profile",
            profile_lock_path=root / "profile.lock",
            artifact_root=root / "artifacts",
        )
        runner.browser_gateway = gateway
        return runner, gateway, page

    @staticmethod
    def _scan_result(
        target: dict[str, object],
        *,
        decision: str,
        report_found: bool,
        verified_state: str = SUBMITTED_VERIFIED,
        report_image: bool = True,
        window_start: datetime = datetime(2026, 8, 24),
        window_end: datetime = datetime(2026, 9, 5),
    ) -> dict[str, object]:
        result = build_scan_result(
            group=str(target["group_name"]),
            person=str(target["person"]),
            actual_group_name=str(target["group_name"]),
            scanned_at=datetime(2026, 9, 5, 12, 0),
            messages_checked=2,
            classifier_result={
                "engine": "synthetic",
                "decision": decision,
                "report_found": report_found,
                "evaluated_messages": 2,
                "candidate_reports": 1 if report_found else 0,
            },
            required_window_start=window_start,
            required_window_end=window_end,
            oldest_message_date=datetime(2026, 8, 24),
            newest_message_date=datetime(2026, 9, 5),
            images={
                "report": {"path": "synthetic-report.png"} if report_image else None,
                "latest": {"path": "synthetic-latest.png"} if not report_found else None,
                "oldest": (
                    {"path": "synthetic-oldest.png", "ui_date_text": "24/08/2026"}
                    if not report_found
                    else None
                ),
                "oldest_message": None,
            },
            report_found=report_found,
            report_detail="Synthetic report",
            submitted_for_bcnb=report_found,
            verified_report_state=verified_state,
        )
        result["scan_target"] = dict(target)
        return result

    async def _observe_with_scan(
        self,
        runner: ReadOnlyCanaryRunner,
        raw_scan: dict[str, object],
    ) -> dict[str, object]:
        async def fake_sync(*_args, **_kwargs):
            return {"verified": True, "decision": "SYNC_NOT_REQUIRED"}

        async def fake_scan(*_args, **_kwargs):
            return raw_scan

        with (
            patch(
                "services.zalo_readonly_canary.ensure_zalo_history_synced",
                new=fake_sync,
            ),
            patch(
                "services.zalo_readonly_canary._scan_one_target",
                new=fake_scan,
            ),
        ):
            return await runner._observe_once()

    def test_ga38_factory_seals_descriptor_namespace_and_formatted_date(self):
        factory = self._factory()
        self.assertTrue(callable(factory), "GA38 production factory is missing")
        if not callable(factory):
            return
        with tempfile.TemporaryDirectory(prefix="ga38-descriptor-") as directory:
            root = Path(directory)
            runner = factory(
                profile_path=root / "profile",
                profile_lock_path=root / "profile.lock",
                artifact_root=root / "artifacts",
            )
            descriptor = runner.descriptor
            self.assertEqual(descriptor.class_code, "VT-C4K-GA38")
            self.assertEqual(
                descriptor.group_name,
                "MindX- Lập trình Game VT-C4K-GA38 CN (18H-20H)",
            )
            self.assertEqual((descriptor.week, descriptor.year), (35, 2026))
            self.assertEqual(descriptor.lesson, "3")
            self.assertEqual(descriptor.class_date_iso, "2026-08-30")
            self.assertEqual(descriptor.message_window_start, "2026-08-24")
            self.assertEqual(descriptor.message_window_end, "2026-09-05")
            rendered = runner._result(
                status=CANARY_INSUFFICIENT_EVIDENCE,
                reason_code="FIXTURE_RESULT",
                observed_at="2026-09-05T00:00:00+00:00",
                started=0.0,
                artifact_path=root / "artifacts" / "result.json",
                lock_released=None,
            )
            self.assertEqual(rendered["phase"], "ZALO_VT_C4K_GA38_READ_ONLY_CANARY")
            self.assertEqual(
                rendered["schema_version"],
                "zalo-vt-c4k-ga38-read-only-canary-v1",
            )

    async def test_ga38_runner_routes_literal_target_through_real_orchestration(self):
        with tempfile.TemporaryDirectory(prefix="ga38-runner-") as directory:
            runner, gateway, page = self._new_runner(Path(directory))
            if runner is None:
                return
            observed: dict[str, object] = {}
            raw = self._scan_result(
                GA38_TARGET,
                decision="SUBMITTED_LATE",
                report_found=True,
                verified_state=SUBMITTED_LATE_VERIFIED,
            )

            async def fake_scan(*_args, **kwargs):
                observed.update(kwargs["target"])
                return raw

            async def fake_sync(*_args, **_kwargs):
                return {"verified": True, "decision": "SYNC_NOT_REQUIRED"}

            with (
                patch(
                    "services.zalo_readonly_canary.ensure_zalo_history_synced",
                    new=fake_sync,
                ),
                patch(
                    "services.zalo_readonly_canary._scan_one_target",
                    new=fake_scan,
                ),
            ):
                result = await runner.run()

            self.assertEqual(result["status"], CANARY_READY)
            self.assertEqual(result["reason_code"], "SUBMITTED_LATE")
            self.assertEqual(observed, GA38_TARGET)
            self.assertEqual(gateway.session_calls, 1)
            self.assertEqual(page.goto_calls, [("https://chat.zalo.me/", 30000)])
            self.assertTrue(result["cleanup"]["complete"])
            self.assertTrue(result["artifact_name"].startswith("zalo-vt-c4k-ga38-canary-"))

    def test_ga38_producer_and_sanitizer_distinguish_report_outcomes(self):
        factory = self._factory()
        self.assertTrue(callable(factory), "GA38 production factory is missing")
        if not callable(factory):
            return
        with tempfile.TemporaryDirectory(prefix="ga38-producer-") as directory:
            root = Path(directory)
            runner = factory(
                profile_path=root / "profile",
                profile_lock_path=root / "profile.lock",
                artifact_root=root / "artifacts",
            )
            cases = (
                (
                    "ontime",
                    self._scan_result(
                        GA38_TARGET,
                        decision="SUBMITTED_ON_TIME",
                        report_found=True,
                    ),
                    SUBMITTED_STATUS,
                    SUBMITTED_VERIFIED,
                    (CANARY_READY, "SUBMITTED_ON_TIME"),
                ),
                (
                    "late",
                    self._scan_result(
                        GA38_TARGET,
                        decision="SUBMITTED_LATE",
                        report_found=True,
                        verified_state=SUBMITTED_LATE_VERIFIED,
                    ),
                    SUBMITTED_LATE_STATUS,
                    SUBMITTED_LATE_VERIFIED,
                    (CANARY_READY, "SUBMITTED_LATE"),
                ),
                (
                    "absence",
                    self._scan_result(
                        GA38_TARGET,
                        decision="NOT_FOUND",
                        report_found=False,
                    ),
                    NOT_FOUND_STATUS,
                    NOT_FOUND_VERIFIED,
                    (CANARY_INSUFFICIENT_EVIDENCE, "LESSON_REPORT_ABSENT"),
                ),
                (
                    "insufficient",
                    self._scan_result(
                        GA38_TARGET,
                        decision="SUBMITTED_ON_TIME",
                        report_found=True,
                        report_image=False,
                    ),
                    INSUFFICIENT_STATUS,
                    INSUFFICIENT_EVIDENCE,
                    ("CAPTURE_INCOMPLETE", "CAPTURE_INCOMPLETE"),
                ),
            )
            for label, raw, expected_status, expected_state, expected_classification in cases:
                with self.subTest(label=label):
                    sanitized = runner.sanitize_scan_result(raw)
                    self.assertEqual(sanitized["status"], expected_status)
                    self.assertEqual(sanitized["evidence_state"], expected_state)
                    self.assertEqual(
                        runner._classify_scan(sanitized), expected_classification
                    )

    async def test_each_target_rejects_foreign_identity_and_wrong_window(self):
        with tempfile.TemporaryDirectory(prefix="ga38-cross-target-") as directory:
            root = Path(directory)
            ga38_runner, _gateway, _page = self._new_runner(root / "ga38")
            if ga38_runner is None:
                return
            armb_window_scan = self._scan_result(
                ARMB_TARGET,
                decision="SUBMITTED_ON_TIME",
                report_found=True,
                window_start=datetime(2026, 8, 17),
                window_end=datetime(2026, 8, 27),
            )
            ga38_outcome = await self._observe_with_scan(ga38_runner, armb_window_scan)
            self.assertEqual(ga38_outcome["status"], CANARY_GROUP_IDENTITY_UNVERIFIED)

            armb_root = root / "armb"
            armb_page = _FakePage()
            armb_gateway = _FakeBrowserGateway(armb_root / "profile", armb_page)
            armb_runner = ReadOnlyCanaryRunner(
                browser_gateway=armb_gateway,
                profile_lock_path=armb_root / "profile.lock",
                artifact_root=armb_root / "artifacts",
            )
            ga38_scan = self._scan_result(
                GA38_TARGET,
                decision="SUBMITTED_ON_TIME",
                report_found=True,
            )
            armb_outcome = await self._observe_with_scan(armb_runner, ga38_scan)
            self.assertEqual(armb_outcome["status"], CANARY_GROUP_IDENTITY_UNVERIFIED)

            ga38_window_mismatch = self._scan_result(
                GA38_TARGET,
                decision="SUBMITTED_ON_TIME",
                report_found=True,
                window_start=datetime(2026, 8, 17),
                window_end=datetime(2026, 8, 27),
            )
            window_outcome = await self._observe_with_scan(
                ga38_runner, ga38_window_mismatch
            )
            self.assertEqual(
                window_outcome["status"], CANARY_MESSAGE_WINDOW_UNVERIFIED
            )

    async def test_ga38_rejects_mismatched_group_lesson_and_date_before_ready(self):
        with tempfile.TemporaryDirectory(prefix="ga38-target-validation-") as directory:
            root = Path(directory)
            runner, _gateway, _page = self._new_runner(root)
            if runner is None:
                return
            for field, value in (
                ("group_name", ARMB_TARGET["group_name"]),
                ("lesson", ARMB_TARGET["lesson"]),
                ("class_date_iso", ARMB_TARGET["class_date_iso"]),
                ("class_date", ARMB_TARGET["class_date"]),
            ):
                with self.subTest(field=field):
                    target = dict(GA38_TARGET)
                    target[field] = value
                    outcome = await self._observe_with_scan(
                        runner,
                        self._scan_result(
                            target,
                            decision="SUBMITTED_ON_TIME",
                            report_found=True,
                        ),
                    )
                    self.assertNotEqual(outcome["status"], CANARY_READY)

    def test_ga38_preserves_bounds_denials_and_armb_default(self):
        self.assertEqual(CanaryBounds().max_runtime_seconds, 300.0)
        self.assertEqual(CanaryBounds().max_scrolls, 60)
        self.assertEqual(CanaryBounds().max_messages, 1000)
        with self.assertRaises(ValueError):
            CanaryBounds(max_scrolls=61)
        capabilities = ReadOnlyCanaryRunner.capabilities()
        self.assertFalse(capabilities["write_allowed"])
        self.assertIn("BCNB", capabilities["denied_capabilities"])
        self.assertIn("ZALO_SEND", capabilities["denied_capabilities"])
        self.assertEqual(
            ReadOnlyCanaryRunner(
                browser_gateway=SimpleNamespace(profile_path="C:/temp/profile"),
                profile_lock_path="C:/temp/profile.lock",
                artifact_root="C:/temp/canary-artifacts",
            ).descriptor.class_code,
            "VT-ROB-ARMB19",
        )

    async def test_tampered_ga38_descriptor_fails_before_browser_entry(self):
        factory = self._factory()
        self.assertTrue(callable(factory), "GA38 production factory is missing")
        if not callable(factory):
            return
        with tempfile.TemporaryDirectory(prefix="ga38-tamper-") as directory:
            root = Path(directory)
            runner = factory(
                profile_path=root / "profile",
                profile_lock_path=root / "profile.lock",
                artifact_root=root / "artifacts",
            )

            class _ExplodingGateway:
                def persistent_session(self, **_kwargs):
                    raise AssertionError("tampered descriptor reached browser")

            runner.descriptor = CANARY_DESCRIPTOR
            runner.browser_gateway = _ExplodingGateway()
            result = await runner.run()

            self.assertEqual(result["status"], "BROWSER_ERROR")
            self.assertEqual(result["reason_code"], "INVALID_CANARY_DESCRIPTOR")

    def test_ga38_cli_has_no_arbitrary_target_or_descriptor_flags(self):
        script = Path(__file__).parents[1] / "scripts" / "zalo_ga38_readonly_canary.py"
        self.assertTrue(script.exists(), "GA38 CLI is missing")
        if not script.exists():
            return
        completed = subprocess.run(
            [sys.executable, "-m", "scripts.zalo_ga38_readonly_canary", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=Path(__file__).parents[1],
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("--class-code", completed.stdout)
        self.assertNotIn("--group-name", completed.stdout)
        self.assertNotIn("--descriptor", completed.stdout)

    def test_ga38_cli_invalid_bootstrap_returns_sealed_safe_json(self):
        script = Path(__file__).parents[1] / "scripts" / "zalo_ga38_readonly_canary.py"
        self.assertTrue(script.exists(), "GA38 CLI is missing")
        if not script.exists():
            return
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.zalo_ga38_readonly_canary",
                "--profile-path",
                "relative-profile",
                "--profile-lock-path",
                "relative-lock",
                "--artifact-root",
                "relative-artifacts",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=Path(__file__).parents[1],
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
            },
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["phase"], "ZALO_VT_C4K_GA38_READ_ONLY_CANARY")
        self.assertEqual(payload["schema_version"], "zalo-vt-c4k-ga38-read-only-canary-v1")
        self.assertEqual(payload["status"], "BROWSER_ERROR")
        self.assertEqual(payload["reason_code"], "INVALID_CANARY_BOOTSTRAP")


if __name__ == "__main__":
    unittest.main()
