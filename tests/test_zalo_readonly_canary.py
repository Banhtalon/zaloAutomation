"""Hermetic contract tests for the reviewed ARMB19 read-only canary."""

from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from playwright.async_api import async_playwright

from services.external_gateways import (
    CleanupCode,
    CleanupResult,
    ExternalGateways,
)
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
    CANARY_INSUFFICIENT_EVIDENCE,
    CANARY_MAX_MESSAGES,
    CANARY_MAX_SCROLLS,
    CANARY_READY,
    CanaryBounds,
    ReadOnlyCanaryRunner,
    _classify_scan,
    canary_descriptor_dict,
)


class _ScriptedCanaryRunner(ReadOnlyCanaryRunner):
    async def _observe_once(self):
        return {
            "status": CANARY_INSUFFICIENT_EVIDENCE,
            "reason_code": "FIXTURE_RESULT",
            "readiness": None,
            "scan": None,
        }


class ReadOnlyCanaryContractTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _build_scan_result(
        *,
        report_found: bool,
        classifier_decision: str,
        verified_report_state: str = SUBMITTED_VERIFIED,
        report_image: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return build_scan_result(
            group="Synthetic group",
            person="Synthetic teacher",
            actual_group_name="Synthetic group",
            scanned_at=datetime(2026, 9, 5, 12, 0),
            messages_checked=2,
            classifier_result={
                "engine": "synthetic",
                "decision": classifier_decision,
                "report_found": report_found,
                "evaluated_messages": 2,
                "candidate_reports": 1 if report_found else 0,
            },
            required_window_start=datetime(2026, 8, 17),
            required_window_end=datetime(2026, 8, 27),
            oldest_message_date=datetime(2026, 8, 17),
            newest_message_date=datetime(2026, 8, 27),
            images={
                "report": report_image,
                "latest": {"path": "synthetic-latest.png"} if not report_found else None,
                "oldest": (
                    {"path": "synthetic-oldest.png", "ui_date_text": "17/08/2026"}
                    if not report_found
                    else None
                ),
                "oldest_message": None,
            },
            report_found=report_found,
            report_detail="Synthetic detail" if report_found else None,
            submitted_for_bcnb=report_found,
            verified_report_state=verified_report_state,
        )

    def test_descriptor_is_fixed_to_reviewed_target_and_window(self):
        descriptor = canary_descriptor_dict()

        self.assertEqual(descriptor["class_code"], "VT-ROB-ARMB19")
        self.assertEqual(
            descriptor["group_name"],
            "MindX - Robotics-VT-ROB-ARMB19 CN (18H-20H)",
        )
        self.assertEqual(descriptor["week"], 34)
        self.assertEqual(descriptor["year"], 2026)
        self.assertEqual(descriptor["lesson"], "1")
        self.assertEqual(descriptor["class_date_iso"], "2026-08-23")
        self.assertEqual(descriptor["message_window_start"], "2026-08-17")
        self.assertEqual(descriptor["message_window_end"], "2026-08-27")

    def test_bounds_are_enforced_before_message_materialization(self):
        self.assertEqual(CanaryBounds().max_scrolls, CANARY_MAX_SCROLLS)
        self.assertEqual(CanaryBounds().max_messages, CANARY_MAX_MESSAGES)
        with self.assertRaises(ValueError):
            CanaryBounds(max_scrolls=CANARY_MAX_SCROLLS + 1)
        with self.assertRaises(ValueError):
            CanaryBounds(max_messages=CANARY_MAX_MESSAGES + 1)

    def test_runner_has_no_external_write_capability_or_ready_injection(self):
        signature = inspect.signature(ReadOnlyCanaryRunner)
        self.assertNotIn("ready", signature.parameters)
        self.assertNotIn("authenticated", signature.parameters)
        capabilities = ReadOnlyCanaryRunner.capabilities()
        self.assertFalse(capabilities["write_allowed"])
        self.assertIn("BCNB", capabilities["denied_capabilities"])
        self.assertIn("ZALO_SEND", capabilities["denied_capabilities"])

    def test_sanitized_result_drops_raw_message_and_identity_fields(self):
        runner = ReadOnlyCanaryRunner(
            browser_gateway=ExternalGateways.production(profile_path="C:/temp/profile").browser,
            profile_lock_path="C:/temp/profile.lock",
            artifact_root="C:/temp/canary-artifacts",
        )
        sanitized = runner.sanitize_scan_result(
            {
                "actual_group_name": "private group header",
                "messages_checked": 4,
                "status_detail": "private message text",
                "evidence": {
                    "classifier_result": {
                        "decision": "SUBMITTED_ON_TIME",
                        "candidates": [{"content": "private report"}],
                    }
                },
            }
        )
        rendered = json.dumps(sanitized, ensure_ascii=False)
        self.assertNotIn("private group header", rendered)
        self.assertNotIn("private message text", rendered)
        self.assertNotIn("private report", rendered)
        self.assertEqual(sanitized["messages_checked"], 4)

    def test_real_scan_result_roundtrip_preserves_on_time_labels_and_consumer_decision(self):
        raw = self._build_scan_result(
            report_found=True,
            classifier_decision="SUBMITTED_ON_TIME",
            verified_report_state=SUBMITTED_VERIFIED,
            report_image={"path": "synthetic-report.png"},
        )
        runner = ReadOnlyCanaryRunner(
            browser_gateway=ExternalGateways.production(profile_path="C:/temp/profile").browser,
            profile_lock_path="C:/temp/profile.lock",
            artifact_root="C:/temp/canary-artifacts",
        )

        sanitized = runner.sanitize_scan_result(raw)
        roundtripped = json.loads(json.dumps(sanitized, ensure_ascii=False))

        self.assertEqual(roundtripped["status"], SUBMITTED_STATUS)
        self.assertEqual(roundtripped["evidence_state"], SUBMITTED_VERIFIED)
        self.assertEqual(roundtripped["classifier_decision"], "SUBMITTED_ON_TIME")
        self.assertEqual(
            _classify_scan(roundtripped),
            (CANARY_READY, "SUBMITTED_ON_TIME"),
        )

    def test_real_scan_result_roundtrip_preserves_late_labels_and_consumer_decision(self):
        raw = self._build_scan_result(
            report_found=True,
            classifier_decision="SUBMITTED_LATE",
            verified_report_state=SUBMITTED_LATE_VERIFIED,
            report_image={"path": "synthetic-report.png"},
        )
        runner = ReadOnlyCanaryRunner(
            browser_gateway=ExternalGateways.production(profile_path="C:/temp/profile").browser,
            profile_lock_path="C:/temp/profile.lock",
            artifact_root="C:/temp/canary-artifacts",
        )

        sanitized = runner.sanitize_scan_result(raw)
        roundtripped = json.loads(json.dumps(sanitized, ensure_ascii=False))

        self.assertEqual(roundtripped["status"], SUBMITTED_LATE_STATUS)
        self.assertEqual(roundtripped["evidence_state"], SUBMITTED_LATE_VERIFIED)
        self.assertEqual(roundtripped["classifier_decision"], "SUBMITTED_LATE")
        self.assertEqual(
            _classify_scan(roundtripped),
            (CANARY_READY, "SUBMITTED_LATE"),
        )

    def test_real_scan_result_roundtrip_preserves_insufficient_and_verified_absence_labels(self):
        runner = ReadOnlyCanaryRunner(
            browser_gateway=ExternalGateways.production(profile_path="C:/temp/profile").browser,
            profile_lock_path="C:/temp/profile.lock",
            artifact_root="C:/temp/canary-artifacts",
        )
        insufficient = self._build_scan_result(
            report_found=True,
            classifier_decision="SUBMITTED_ON_TIME",
            report_image=None,
        )
        absent = self._build_scan_result(
            report_found=False,
            classifier_decision="NOT_FOUND",
        )

        insufficient_roundtrip = json.loads(
            json.dumps(runner.sanitize_scan_result(insufficient), ensure_ascii=False)
        )
        absent_roundtrip = json.loads(
            json.dumps(runner.sanitize_scan_result(absent), ensure_ascii=False)
        )

        self.assertEqual(insufficient_roundtrip["status"], INSUFFICIENT_STATUS)
        self.assertEqual(insufficient_roundtrip["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertEqual(absent_roundtrip["status"], NOT_FOUND_STATUS)
        self.assertEqual(absent_roundtrip["evidence_state"], NOT_FOUND_VERIFIED)
        self.assertEqual(absent_roundtrip["classifier_decision"], "NOT_FOUND")

    def test_unknown_label_and_decision_values_fail_closed_without_leakage(self):
        marker = "SYNTHETIC_SECRET_MARKER"
        raw = self._build_scan_result(
            report_found=True,
            classifier_decision="SUBMITTED_ON_TIME",
            report_image={"path": "synthetic-report.png"},
        )
        raw["status"] = marker
        raw["evidence_state"] = marker
        raw["status_detail"] = marker
        raw["evidence"]["classifier_result"]["decision"] = marker

        runner = ReadOnlyCanaryRunner(
            browser_gateway=ExternalGateways.production(profile_path="C:/temp/profile").browser,
            profile_lock_path="C:/temp/profile.lock",
            artifact_root="C:/temp/canary-artifacts",
        )
        sanitized = runner.sanitize_scan_result(raw)
        roundtripped = json.loads(json.dumps(sanitized, ensure_ascii=False))
        rendered = json.dumps(roundtripped, ensure_ascii=False)

        self.assertEqual(roundtripped["status"], INSUFFICIENT_STATUS)
        self.assertEqual(roundtripped["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertEqual(roundtripped["classifier_decision"], "UNKNOWN")
        self.assertNotIn(marker, rendered)

    def test_non_mapping_scan_result_uses_status_and_evidence_state_fallbacks(self):
        runner = ReadOnlyCanaryRunner(
            browser_gateway=ExternalGateways.production(profile_path="C:/temp/profile").browser,
            profile_lock_path="C:/temp/profile.lock",
            artifact_root="C:/temp/canary-artifacts",
        )

        sanitized = runner.sanitize_scan_result(None)

        self.assertEqual(sanitized["status"], INSUFFICIENT_STATUS)
        self.assertEqual(sanitized["evidence_state"], INSUFFICIENT_EVIDENCE)

    def test_production_factory_uses_code_sealed_adapter(self):
        gateway = ExternalGateways.production(profile_path="C:/temp/profile")
        runner = ReadOnlyCanaryRunner.from_production(
            profile_lock_path="C:/temp/profile.lock",
            artifact_root="C:/temp/canary-artifacts",
            profile_path="C:/temp/profile",
        )
        self.assertIsInstance(runner, ReadOnlyCanaryRunner)
        provider = gateway.browser._landing_facts_provider_factory()
        self.assertEqual(type(provider).__name__, "ZaloAuthReadinessAdapter")
        self.assertEqual(runner.descriptor, CANARY_DESCRIPTOR)

    def test_result_exposes_artifact_name_not_absolute_path(self):
        runner = ReadOnlyCanaryRunner(
            browser_gateway=ExternalGateways.production(profile_path="C:/temp/profile").browser,
            profile_lock_path="C:/temp/profile.lock",
            artifact_root="C:/temp/canary-artifacts",
        )
        result = runner._result(
            status=CANARY_INSUFFICIENT_EVIDENCE,
            reason_code="FIXTURE_RESULT",
            observed_at="2026-09-05T00:00:00+00:00",
            started=0.0,
            artifact_path=Path("C:/temp/canary-artifacts/result.json"),
            lock_released=None,
        )
        self.assertEqual(result["artifact_name"], "result.json")
        self.assertNotIn("artifact_path", result)

    def test_artifact_root_must_not_overlap_profile_or_lock_destination(self):
        with tempfile.TemporaryDirectory(prefix="canary-root-") as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                ReadOnlyCanaryRunner(
                    browser_gateway=ExternalGateways.production(
                        profile_path=str(root / "profile")
                    ).browser,
                    profile_lock_path=root / "profile" / "profile.lock",
                    artifact_root=root / "profile" / "artifacts",
                )

            occupied = root / "occupied"
            occupied.mkdir()
            (occupied / "prior.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                ReadOnlyCanaryRunner(
                    browser_gateway=ExternalGateways.production(
                        profile_path=str(root / "profile-2")
                    ).browser,
                    profile_lock_path=root / "profile-2.lock",
                    artifact_root=occupied,
                )

    async def _run_scripted_cleanup_case(self, cleanup: CleanupResult):
        directory = tempfile.TemporaryDirectory(prefix="canary-cleanup-")
        root = Path(directory.name)
        lock_path = root / "profile.lock"
        gateway = SimpleNamespace(last_cleanup_result=cleanup)
        runner = _ScriptedCanaryRunner(
            browser_gateway=gateway,
            profile_lock_path=lock_path,
            artifact_root=root / "artifacts",
        )
        result = await runner.run()
        return directory, runner, lock_path, result

    async def test_incomplete_cleanup_retains_lock_and_blocks_reuse(self):
        cleanup = CleanupResult(
            code=CleanupCode.CLEANUP_INCOMPLETE,
            remaining_pages=1,
            invariant_ok=False,
            cleanup_incomplete=True,
        )
        directory, runner, lock_path, result = await self._run_scripted_cleanup_case(cleanup)
        try:
            self.assertEqual(result["status"], "CLEANUP_INCOMPLETE")
            self.assertFalse(result["cleanup"]["complete"])
            self.assertTrue(lock_path.exists())
            second = await runner.run()
            self.assertEqual(second["status"], "CLEANUP_INCOMPLETE")
            self.assertTrue(lock_path.exists())
        finally:
            directory.cleanup()

    async def test_confirmed_cleanup_releases_lock(self):
        cleanup = CleanupResult(code=CleanupCode.CLEANUP_OK)
        directory, _runner, lock_path, result = await self._run_scripted_cleanup_case(cleanup)
        try:
            self.assertEqual(result["status"], CANARY_INSUFFICIENT_EVIDENCE)
            self.assertTrue(result["cleanup"]["complete"])
            self.assertFalse(lock_path.exists())
        finally:
            directory.cleanup()

    def test_verified_absence_is_not_canary_ready(self):
        scan = {
            "classifier_decision": "NOT_FOUND_VERIFIED",
            "messages_checked": 2,
            "capture_count": 1,
            "validation_error_count": 0,
            "required_window_start": CANARY_DESCRIPTOR.message_window_start,
            "required_window_end": CANARY_DESCRIPTOR.message_window_end,
        }
        status, reason = _classify_scan(scan)
        self.assertNotEqual(status, CANARY_READY)
        self.assertEqual(status, CANARY_INSUFFICIENT_EVIDENCE)
        self.assertEqual(reason, "LESSON_REPORT_ABSENT")

    def test_found_lesson_report_with_valid_capture_is_canary_ready(self):
        scan = {
            "classifier_decision": "SUBMITTED_ON_TIME",
            "messages_checked": 2,
            "capture_count": 1,
            "validation_error_count": 0,
            "required_window_start": CANARY_DESCRIPTOR.message_window_start,
            "required_window_end": CANARY_DESCRIPTOR.message_window_end,
        }
        status, reason = _classify_scan(scan)
        self.assertEqual(status, CANARY_READY)
        self.assertEqual(reason, "SUBMITTED_ON_TIME")


class ReadOnlyCanarySyntheticBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_adapter_is_used_on_network_blocked_fixture(self):
        runner = ReadOnlyCanaryRunner(
            browser_gateway=ExternalGateways.production(profile_path="C:/temp/profile").browser,
            profile_lock_path="C:/temp/profile.lock",
            artifact_root="C:/temp/canary-artifacts",
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()

            async def fulfill(route):
                if route.request.url.startswith("https://chat.zalo.me/"):
                    await route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=(
                            "<html><body>"
                            "<nav style='display:block;width:10px;height:10px'></nav>"
                            "<input id='contact-search-input'>"
                            "</body></html>"
                        ),
                    )
                else:
                    await route.abort()

            await context.route("**/*", fulfill)
            try:
                page = await context.new_page()
                await page.goto("https://chat.zalo.me/")
                result = await runner.observe_readiness(page)
            finally:
                await context.close()
                await browser.close()

        self.assertEqual(result["reason_code"], "AUTHENTICATED_READY")
        self.assertEqual(result["selector_contract_version"], "zalo-auth-readiness-v1")


if __name__ == "__main__":
    unittest.main()
