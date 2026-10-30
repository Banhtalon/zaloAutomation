from datetime import datetime
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.googleform_routes import (
    FetchLmsStudentsRequest,
    MappingItem,
    OcrRequest,
    RestoreGroupRequest,
    StudentItem,
    SubmitRequest,
    api_fetch_googleform_lms_students,
    api_run_googleform_ocr,
    clear_history,
    confirm_ignored_group_ended,
    delete_mapping,
    delete_submission_screenshot,
    get_history,
    get_ignored_groups,
    get_mappings,
    get_submission_screenshot,
    restore_ignored_group,
    run_submit,
    save_mapping,
)
from api.job_routes import (
    cancel_job,
    get_job_audit,
    get_job_events,
    get_job_status,
    get_job_targets,
    get_latest_active_job,
    get_latest_job,
    retry_terminal_job,
)
from api.lms_routes import (
    LMSAnalyzeRequest,
    LMSConfirmRequest,
    api_lms_analyze,
    api_lms_confirm,
)
from api.zalo_routes import (
    auto_discover,
    get_bcnb_zalo_gaps,
    get_class_lifecycles,
    get_groups,
    get_zalo_scan_preflight,
    get_zalo_scan_plan,
    manual_approve_zalo_report,
    run_scan,
)
from core.time_utils import BUSINESS_TIMEZONE
from repositories.job_repository import JobNotFoundError, JobStateError
from repositories.lms_repository import ConfirmationError
from services.lms_plan import compute_plan_digest


class FakeSettings:
    bcnb_dry_run = True
    google_form_mode = "dry-run"
    lms_confirmation_ttl_minutes = 15
    zalo_plan_max_age_minutes = 9_999_999
    zalo_scan_concurrency = 2
    runtime_root = "runtime"


class FakeDispatcher:
    def __init__(self):
        self.scheduled = []
        self.retry_result = 77
        self.retry_error = None

    def schedule_job(self, job_id):
        self.scheduled.append(job_id)

    async def retry_job(self, _job_id):
        if self.retry_error:
            raise self.retry_error
        return self.retry_result


class FakeJobRepo:
    def __init__(self):
        self.created = []
        self.updates = []
        self.payloads = []
        self.job = {"id": 41, "status": "FAILED"}
        self.cancel_result = {"status": "RUNNING", "cancel_requested": True}
        self.cancel_error = None
        self.idempotent_result = None

    async def create_job(self, job_type, payload, **metadata):
        self.created.append((job_type, payload, metadata))
        return 41

    async def create_idempotent_job(self, job_type, payload, **metadata):
        job_id = await self.create_job(job_type, payload, **metadata)
        return self.idempotent_result or {
            "job_id": job_id,
            "created": True,
            "status": "PENDING",
        }

    async def update_payload(self, job_id, payload):
        self.payloads.append((job_id, payload))

    async def update_job(self, job_id, **values):
        self.updates.append((job_id, values))

    async def get_job(self, _job_id):
        return self.job

    async def get_latest_job(self, active_only=False):
        return {"id": 41, "active_only": active_only}

    async def request_cancel(self, _job_id):
        if self.cancel_error:
            raise self.cancel_error
        return self.cancel_result

    async def list_events(self, _job_id, limit):
        return [{"id": 1, "limit": limit}]


class FakeZaloBatchRepo:
    async def summary(self, _job_id):
        return {"total": 2, "scan": {"RUNNING": 1}, "write": {"VERIFIED": 1}}

    async def list_for_job(self, _job_id):
        return [{"id": 1, "scan_status": "RUNNING", "write_status": "NOT_READY"}]


class FakeGroupRepo:
    def __init__(self):
        self.groups = [{"name": "VT-JSB"}]
        self.ignored = ["Old group"]
        self.calls = []
        self.restore_result = True
        self.snapshot = {
            "snapshot_id": 9,
            "observed_at": "2026-08-14T01:00:00+00:00",
            "status": "OBSERVED_ONLY",
            "plan": {
                "plan_version": 4,
                "bcnb_zalo_coverage": {
                    "source_count": 3,
                    "matched_count": 1,
                    "missing_count": 1,
                    "ambiguous_count": 1,
                    "transition_review_count": 0,
                    "records": [
                        {
                            "class_code": "VT-JSB",
                            "status": "MATCHED",
                            "periods": [{"week": 32, "year": 2026}],
                        },
                        {
                            "class_code": "VT-C4K-G01",
                            "status": "MISSING_ZALO_GROUP",
                            "periods": [{"week": 32, "year": 2026}],
                        },
                        {
                            "class_code": "VT-C4K-G02",
                            "status": "AMBIGUOUS_ZALO_GROUP",
                            "periods": [{"week": 33, "year": 2026}],
                        },
                    ],
                },
                "bcnb_week_plan": {
                    "latest_week": {"week": 33, "year": 2026},
                    "required_week": {"week": 32, "year": 2026},
                    "selected_weeks": [
                        {"week": 32, "year": 2026},
                        {"week": 33, "year": 2026},
                    ],
                },
                "scan_targets": [
                    {
                        "person": "Teacher",
                        "class_code": "VT-JSB",
                        "lesson": "12",
                        "class_date": "07/08/2026",
                        "class_date_iso": "2026-08-07",
                        "group_name": "MindX - VT-JSB T6 (18H-20H)",
                        "week": 32,
                        "year": 2026,
                    }
                ],
            },
        }

    async def get_all_groups(self):
        return self.groups

    async def get_scan_groups(self):
        return self.groups

    async def get_discovery_snapshot(self, _snapshot_id):
        return self.snapshot

    async def get_latest_discovery_snapshot(self):
        return self.snapshot

    async def cleanup_old_ignored_groups(self, days):
        self.calls.append(("cleanup", days))

    async def archive_confirmed_ended_groups(self, days):
        self.calls.append(("archive", days))

    async def get_ignored_group_records(self):
        return [
            {
                "group_name": "Old group",
                "status": "PAUSED_OR_TRANSITIONING",
            }
        ]

    async def get_all_ignored_groups(self):
        return self.ignored

    async def remove_ignored_group(self, name):
        self.calls.append(("remove", name))

    async def restore_ignored_group(self, name):
        self.calls.append(("restore", name))
        return self.restore_result

    async def add_group(self, name, teacher):
        self.calls.append(("add", name, teacher))

    async def confirm_ignored_group_ended(self, name):
        self.calls.append(("confirm-ended", name))
        return name == "Old group"

    async def get_class_lifecycles(self, *, include_archived=False):
        return [{"cohort_key": "VT-C4K|G|02", "include_archived": include_archived}]


class FakeReportRepo:
    def __init__(self):
        self.mappings = [{"id": 1}]
        self.calls = []
        self.screenshot_path = "runtime/screenshots/googleform/test.png"

    async def get_form_mappings(self):
        return self.mappings

    async def add_form_mapping(self, question, field_type, value):
        self.calls.append(("add", question, field_type, value))

    async def delete_form_mapping(self, mapping_id):
        self.calls.append(("delete", mapping_id))

    async def get_form_submissions(self):
        return [{"student": "A"}]

    async def get_form_submission(self, submission_id):
        if submission_id == 1:
            return {"id": 1, "screenshot_path": self.screenshot_path}
        if submission_id == 2:
            return {"id": 2, "screenshot_path": "outside/secret.png"}
        if submission_id == 3:
            return {"id": 3, "screenshot_path": "runtime/screenshots/googleform/missing_non_existent.png"}
        return None

    async def delete_submission_screenshot(self, submission_id):
        self.calls.append(("delete_screenshot", submission_id))
        return submission_id == 1

    async def clear_form_submissions(self):
        self.calls.append(("clear_history",))
        return 3


class FakeUploadStore:
    async def claim(self, upload_ids, purpose, job_id):
        return [f"{purpose}:{item}:{job_id}" for item in upload_ids]


class FakeAuditRepo:
    async def list_for_job(self, _job_id, limit):
        return [{"id": 2, "limit": limit}]

    async def count_unresolved(self):
        return 0


class FakeLmsRepo:
    def __init__(self, confirmation=None, error=None):
        self.confirmation = confirmation
        self.error = error
        self.calls = []

    async def consume_confirmation(self, *_args, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.confirmation


class FakeEvidenceRepo:
    def __init__(self):
        self.verify_result = {
            "status": "AVAILABLE",
            "reason": "verified",
            "sha256": "a" * 64,
            "size_bytes": 1024,
        }
        self.calls = []

    async def verify(self, evidence_ref, *, job_id=None):
        self.calls.append((evidence_ref, job_id))
        return dict(self.verify_result)


class FakeContainer:
    def __init__(self):
        self.settings = FakeSettings()
        self.dispatcher = FakeDispatcher()
        self.job_repo = FakeJobRepo()
        self.group_repo = FakeGroupRepo()
        self.report_repo = FakeReportRepo()
        self.upload_store = FakeUploadStore()
        self.audit_repo = FakeAuditRepo()
        self.zalo_batch_repo = FakeZaloBatchRepo()
        self.evidence_repo = FakeEvidenceRepo()
        self.lms_repo = None


class ApiRoutesUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_zalo_and_group_routes_preserve_success_contracts(self):
        container = FakeContainer()
        discovery = await auto_discover(container)
        scan = await run_scan(container)
        groups = await get_groups(container)
        lifecycles = await get_class_lifecycles(False, container)

        self.assertEqual(discovery["job_id"], 41)
        self.assertEqual(scan["dry_run"], True)
        self.assertEqual(groups, {"groups": [{"name": "VT-JSB"}]})
        self.assertEqual(lifecycles["classes"][0]["cohort_key"], "VT-C4K|G|02")
        self.assertEqual(container.dispatcher.scheduled, [41, 41])
        self.assertEqual(
            [item[0] for item in container.job_repo.created],
            ["ZALO_DISCOVERY", "ZALO_SCAN"],
        )
        self.assertEqual(container.job_repo.created[0][1], {"version": 4})

    async def test_bcnb_zalo_gap_route_reads_latest_snapshot_and_filters(self):
        container = FakeContainer()

        payload = await get_bcnb_zalo_gaps(32, 2026, None, container)

        self.assertTrue(payload["available"])
        self.assertEqual(payload["filtered_count"], 1)
        self.assertEqual(payload["items"][0]["class_code"], "VT-C4K-G01")
        self.assertEqual(payload["summary"]["matched_count"], 1)
        self.assertEqual(container.group_repo.calls, [])

    async def test_bcnb_zalo_gap_route_requires_v4_snapshot_without_backfill(self):
        container = FakeContainer()
        container.group_repo.snapshot["plan"]["plan_version"] = 3

        payload = await get_bcnb_zalo_gaps(None, None, None, container)

        self.assertFalse(payload["available"])
        self.assertEqual(payload["reason"], "DISCOVERY_PLAN_UPGRADE_REQUIRED")
        self.assertEqual(payload["items"], [])
        self.assertEqual(container.group_repo.calls, [])

    async def test_bcnb_zalo_gap_route_rejects_unknown_status(self):
        container = FakeContainer()

        with self.assertRaises(HTTPException) as caught:
            await get_bcnb_zalo_gaps(None, None, "MATCHED", container)

        self.assertEqual(caught.exception.detail["code"], "INVALID_GAP_STATUS")

    async def test_bcnb_zalo_gap_route_validates_period_and_handles_no_snapshot(self):
        container = FakeContainer()
        for week, year, expected in (
            (0, None, "INVALID_WEEK"),
            (None, 1999, "INVALID_YEAR"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(HTTPException) as caught:
                    await get_bcnb_zalo_gaps(week, year, None, container)
                self.assertEqual(caught.exception.detail["code"], expected)

        container.group_repo.snapshot = None
        payload = await get_bcnb_zalo_gaps(None, None, None, container)
        self.assertFalse(payload["available"])
        self.assertEqual(payload["reason"], "NO_DISCOVERY_SNAPSHOT")

    async def test_bcnb_zalo_gap_route_filters_status_and_marks_stale(self):
        container = FakeContainer()
        container.settings.zalo_plan_max_age_minutes = 30
        container.group_repo.snapshot["observed_at"] = "2020-01-01T00:00:00+00:00"

        payload = await get_bcnb_zalo_gaps(
            None, None, "ambiguous_zalo_group", container
        )

        self.assertTrue(payload["stale"])
        self.assertEqual(payload["filtered_count"], 1)
        self.assertEqual(payload["items"][0]["status"], "AMBIGUOUS_ZALO_GROUP")

    async def test_bcnb_zalo_gap_route_fails_closed_on_malformed_plan_version(self):
        container = FakeContainer()
        container.group_repo.snapshot["plan"]["plan_version"] = "invalid"

        payload = await get_bcnb_zalo_gaps(None, None, None, container)

        self.assertFalse(payload["available"])
        self.assertEqual(payload["reason"], "DISCOVERY_PLAN_UPGRADE_REQUIRED")

    async def test_target_progress_route_returns_durable_summary(self):
        container = FakeContainer()
        payload = await get_job_targets(41, container)

        self.assertEqual(payload["job_id"], 41)
        self.assertEqual(payload["summary"]["total"], 2)
        self.assertEqual(payload["targets"][0]["scan_status"], "RUNNING")
        self.assertEqual(payload["report"]["checked"], 0)
        self.assertEqual(payload["review_queue"], [])

    async def test_target_progress_route_exposes_safe_review_queue(self):
        container = FakeContainer()
        container.zalo_batch_repo.summary = AsyncMock(
            return_value={"total": 1, "scan": {"FAILED": 1}, "write": {"BLOCKED_BATCH_HALT": 1}}
        )
        container.zalo_batch_repo.list_for_job = AsyncMock(
            return_value=[
                {
                    "id": 7,
                    "class_code": "VT-JSB16",
                    "lesson": "6",
                    "class_date": "20/09/2026",
                    "scan_status": "FAILED",
                    "write_status": "BLOCKED_BATCH_HALT",
                    "error_code": "TARGET_SEARCH_CLEAR_TIMEOUT",
                    "evidence_path": "/api/evidence/41/report.png",
                    "result": {
                        "evidence_state": "INSUFFICIENT_EVIDENCE",
                        "evidence": {
                            "images": {
                                "report": {"path": "/api/evidence/41/report.png"}
                            }
                        },
                    },
                }
            ]
        )

        payload = await get_job_targets(41, container)

        self.assertEqual(payload["report"]["scan_failed"], 1)
        self.assertEqual(payload["report"]["blocked_batch_halt"], 1)
        self.assertEqual(len(payload["review_queue"]), 1)
        self.assertEqual(payload["review_queue"][0]["class_code"], "VT-JSB16")
        self.assertEqual(payload["review_queue"][0]["reason_code"], "TARGET_SEARCH_CLEAR_TIMEOUT")
        self.assertEqual(payload["review_queue"][0]["evidence_paths"], ["/api/evidence/41/report.png"])

    async def test_target_review_distinguishes_report_evidence_from_bcnb_issues(self):
        cases = [
            ("SUBMITTED_VERIFIED", "TARGET_NOT_UNIQUE_OR_MISSING", "VERIFIED",
             "Đã có báo cáo đúng buổi nhưng bước cập nhật BCNB cần kiểm tra."),
            ("SUBMITTED_LATE_VERIFIED", "BCNB_WRITE_FAILED", "VERIFIED",
             "Đã có báo cáo đúng buổi nhưng bước cập nhật BCNB cần kiểm tra."),
            ("NOT_FOUND_VERIFIED", "EVIDENCE_NOT_READY", "VERIFIED",
             "Chưa tìm thấy báo cáo đúng buổi."),
            ("INSUFFICIENT_EVIDENCE", "EVIDENCE_NOT_READY", "VERIFIED",
             "Chưa đủ bằng chứng để kết luận."),
            ("UNKNOWN", "", "VERIFIED", "Chưa đủ bằng chứng để kết luận."),
            ("INSUFFICIENT_EVIDENCE", "TARGET_SEARCH_CLEAR_TIMEOUT", "FAILED",
             "Lỗi kỹ thuật cần kiểm tra lại."),
        ]
        for state, error_code, scan_status, expected_reason in cases:
            with self.subTest(state=state, error_code=error_code):
                container = FakeContainer()
                container.zalo_batch_repo.list_for_job = AsyncMock(return_value=[{
                    "id": 7,
                    "class_code": "SYNTHETIC-CLASS",
                    "lesson": "8",
                    "class_date": "01/10/2026",
                    "scan_status": scan_status,
                    "write_status": "FAILED_BEFORE_WRITE",
                    "error_code": error_code,
                    "evidence_path": "/api/evidence/41/report.png",
                    "result": {"evidence_state": state},
                }])

                payload = await get_job_targets(41, container)

                self.assertEqual(len(payload["review_queue"]), 1)
                item = payload["review_queue"][0]
                self.assertEqual(item["reason"], expected_reason)
                self.assertEqual(item["reason_code"], error_code or state)
                self.assertEqual(item["evidence_paths"], ["/api/evidence/41/report.png"])
                self.assertEqual(payload["report"]["submitted_verified"], int(
                    state in {"SUBMITTED_VERIFIED", "SUBMITTED_LATE_VERIFIED"}
                ))
                self.assertEqual(payload["report"]["not_found_verified"], int(
                    state == "NOT_FOUND_VERIFIED"
                ))

    async def test_zalo_preflight_reports_safe_read_only_summary(self):
        container = FakeContainer()
        report = await get_zalo_scan_preflight(container)

        self.assertEqual(report["status"], "GREEN")
        self.assertTrue(report["run_allowed"])
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(report["target_count"], 1)
        self.assertTrue(any(item["code"] == "SCAN_PLAN_READY" for item in report["checks"]))

    async def test_zalo_preflight_blocks_an_active_job(self):
        container = FakeContainer()

        async def active_job(*, active_only=False):
            return {"id": 99, "status": "RUNNING"} if active_only else None

        container.job_repo.get_latest_job = active_job
        report = await get_zalo_scan_preflight(container)

        self.assertEqual(report["status"], "RED")
        self.assertFalse(report["run_allowed"])
        self.assertIn("ACTIVE_JOB_PRESENT", {item["code"] for item in report["checks"]})

    async def test_zalo_preflight_blocks_unresolved_external_audit(self):
        container = FakeContainer()
        container.audit_repo.count_unresolved = AsyncMock(return_value=1)

        report = await get_zalo_scan_preflight(container)

        self.assertEqual(report["status"], "RED")
        self.assertFalse(report["run_allowed"])
        self.assertIn("AUDIT_PENDING", {item["code"] for item in report["checks"]})

    async def test_production_yellow_requires_explicit_confirmation(self):
        container = FakeContainer()
        container.settings.bcnb_dry_run = False
        yellow = {
            "status": "YELLOW",
            "checks": [{"code": "ZALO_SESSION_RUNTIME_CHECK", "status": "YELLOW", "message": "Cần xác nhận."}],
        }
        with patch(
            "api.zalo_routes.assess_zalo_scan_preflight",
            new=AsyncMock(return_value=yellow),
        ):
            with self.assertRaises(HTTPException) as caught:
                await run_scan(container)
            self.assertEqual(
                caught.exception.detail["code"],
                "SCAN_PREFLIGHT_CONFIRMATION_REQUIRED",
            )

            response = await run_scan(container, confirm_yellow=True)
            self.assertEqual(response["dry_run"], False)

    async def test_scan_requires_a_trusted_complete_two_week_snapshot(self):
        container = FakeContainer()
        container.group_repo.snapshot = None
        with patch(
            "api.zalo_routes.assess_zalo_scan_preflight",
            new=AsyncMock(return_value={"status": "GREEN", "checks": []}),
        ):
            with self.assertRaises(HTTPException) as required:
                await run_scan(container)
        self.assertEqual(required.exception.detail["code"], "SCAN_PLAN_REQUIRED")

        container.group_repo.snapshot = {
            "snapshot_id": 1,
            "status": "REJECTED",
            "plan": {},
        }
        with patch(
            "api.zalo_routes.assess_zalo_scan_preflight",
            new=AsyncMock(return_value={"status": "GREEN", "checks": []}),
        ):
            with self.assertRaises(HTTPException) as untrusted:
                await run_scan(container)
        self.assertEqual(untrusted.exception.detail["code"], "SCAN_PLAN_NOT_TRUSTED")

        container.group_repo.snapshot = {
            "snapshot_id": 1,
            "status": "OBSERVED_ONLY",
            "plan": {"bcnb_week_plan": {"selected_weeks": [{"week": 32, "year": 2026}]}},
        }
        with patch(
            "api.zalo_routes.assess_zalo_scan_preflight",
            new=AsyncMock(return_value={"status": "GREEN", "checks": []}),
        ):
            with self.assertRaises(HTTPException) as incomplete:
                await run_scan(container)
        self.assertEqual(incomplete.exception.detail["code"], "SCAN_PLAN_INCOMPLETE")

    async def test_run_scan_keeps_legacy_validation_branches_fail_closed(self):
        green = {"status": "GREEN", "checks": []}

        container = FakeContainer()
        with patch("api.zalo_routes.assess_zalo_scan_preflight", new=AsyncMock(return_value=green)):
            response = await run_scan(container, snapshot_id=9, confirm_yellow=False)
        self.assertEqual(response["job_id"], 41)

        container = FakeContainer()
        with patch(
            "api.zalo_routes.assess_zalo_scan_preflight",
            new=AsyncMock(return_value=green),
        ):
            container.group_repo.snapshot["plan"]["bcnb_week_plan"]["selected_weeks"] = [
                None,
                {"week": "not-a-week", "year": 2026},
            ]
            with self.assertRaises(HTTPException) as malformed_periods:
                await run_scan(container, snapshot_id=9, confirm_yellow=False)
            self.assertEqual(malformed_periods.exception.detail["code"], "SCAN_PLAN_INCOMPLETE")

            container = FakeContainer()
            container.group_repo.snapshot["plan"]["bcnb_week_plan"]["latest_week"] = {
                "week": "not-a-week",
                "year": 2026,
            }
            with self.assertRaises(HTTPException) as malformed_latest:
                await run_scan(container, snapshot_id=9)
            self.assertEqual(malformed_latest.exception.detail["code"], "SCAN_PLAN_INCOMPLETE")

            container = FakeContainer()
            container.group_repo.snapshot["plan"]["scan_targets"] = [None]
            with self.assertRaises(HTTPException) as invalid_target:
                await run_scan(container, snapshot_id=9)
            self.assertEqual(invalid_target.exception.detail["code"], "SCAN_PLAN_INVALID_TARGET")

            container = FakeContainer()
            original = container.group_repo.snapshot["plan"]["scan_targets"][0]
            container.group_repo.snapshot["plan"]["scan_targets"].append(dict(original))
            with self.assertRaises(HTTPException) as duplicate:
                await run_scan(container, snapshot_id=9)
            self.assertEqual(duplicate.exception.detail["code"], "SCAN_PLAN_DUPLICATE_TARGET")

            container = FakeContainer()
            container.group_repo.snapshot["observed_at"] = "2020-01-01T00:00:00+00:00"
            container.settings.zalo_plan_max_age_minutes = 30
            with self.assertRaises(HTTPException) as stale:
                await run_scan(container, snapshot_id=9)
            self.assertEqual(stale.exception.detail["code"], "SCAN_PLAN_STALE")

            container = FakeContainer()
            container.group_repo.snapshot["observed_at"] = None
            with patch("api.zalo_routes._snapshot_is_stale", return_value=False):
                with self.assertRaises(HTTPException) as missing_timestamp:
                    await run_scan(container, snapshot_id=9)
            self.assertEqual(missing_timestamp.exception.detail["code"], "SCAN_PLAN_STALE")

    async def test_run_scan_blocks_non_plan_preflight_red(self):
        container = FakeContainer()
        blocked = {
            "status": "RED",
            "checks": [
                {
                    "status": "RED",
                    "code": "ACTIVE_JOB_PRESENT",
                    "message": "Đang có job khác.",
                }
            ],
        }
        with patch(
            "api.zalo_routes.assess_zalo_scan_preflight",
            new=AsyncMock(return_value=blocked),
        ):
            with self.assertRaises(HTTPException) as caught:
                await run_scan(container)
        self.assertEqual(caught.exception.detail["code"], "SCAN_PREFLIGHT_BLOCKED")

    async def test_scan_rejects_snapshot_older_than_configured_limit(self):
        container = FakeContainer()
        container.settings.zalo_plan_max_age_minutes = 30
        container.group_repo.snapshot["observed_at"] = "2020-01-01T00:00:00+00:00"

        with patch(
            "api.zalo_routes.assess_zalo_scan_preflight",
            new=AsyncMock(return_value={"status": "GREEN", "checks": []}),
        ):
            with self.assertRaises(HTTPException) as stale:
                await run_scan(container)

        self.assertEqual(stale.exception.detail["code"], "SCAN_PLAN_STALE")
        self.assertEqual(container.job_repo.created, [])

    async def test_scan_rejects_duplicate_immutable_target(self):
        container = FakeContainer()
        original = container.group_repo.snapshot["plan"]["scan_targets"][0]
        container.group_repo.snapshot["plan"]["scan_targets"].append(dict(original))

        with self.assertRaises(HTTPException) as duplicate:
            await run_scan(container)

        self.assertEqual(
            duplicate.exception.detail["code"], "SCAN_PLAN_DUPLICATE_TARGET"
        )
        self.assertEqual(container.job_repo.created, [])

    async def test_run_scan_defers_current_day_target_before_class_end(self):
        container = FakeContainer()
        container.group_repo.snapshot["plan"]["scan_targets"].append(
            {
                "group_name": "MindX - VT-JSB T6 (18H-20H)",
                "person": "Teacher",
                "class_code": "VT-JSB",
                "lesson": "13",
                "class_date": "14/08/2026",
                "class_date_iso": "2026-08-14",
                "week": 33,
                "year": 2026,
            }
        )
        with patch(
            "api.zalo_routes.now_business",
            return_value=datetime(2026, 8, 14, 16, 5, tzinfo=BUSINESS_TIMEZONE),
        ):
            response = await run_scan(container)

        payload = container.job_repo.created[-1][1]
        runtime_plan = payload["scan_plan"]
        self.assertEqual(response["deferred_target_count"], 1)
        self.assertEqual([(item["week"], item["lesson"]) for item in runtime_plan["targets"]], [(32, "12")])
        self.assertEqual(
            [(item["week"], item["lesson"]) for item in runtime_plan["deferred_targets"]],
            [(33, "13")],
        )

    async def test_run_scan_waits_when_every_target_is_before_its_end_time(self):
        container = FakeContainer()
        container.group_repo.snapshot["plan"]["scan_targets"] = [
            {
                "group_name": "MindX - VT-JSB T6 (18H-20H)",
                "person": "Teacher",
                "class_code": "VT-JSB",
                "lesson": "13",
                "class_date": "14/08/2026",
                "class_date_iso": "2026-08-14",
                "week": 33,
                "year": 2026,
            }
        ]
        with patch(
            "api.zalo_routes.now_business",
            return_value=datetime(2026, 8, 14, 16, 5, tzinfo=BUSINESS_TIMEZONE),
        ):
            with self.assertRaises(HTTPException) as deferred:
                await run_scan(container)

        self.assertEqual(deferred.exception.detail["code"], "SCAN_TARGETS_DEFERRED")
        self.assertEqual(container.job_repo.created, [])

    async def test_scan_plan_rejects_malformed_period_items_in_both_routes(self):
        container = FakeContainer()
        container.group_repo.snapshot["plan"]["bcnb_week_plan"]["selected_weeks"] = [
            None,
            {"week": "not-a-week", "year": 2026},
        ]

        with self.assertRaises(HTTPException) as incomplete:
            await run_scan(container)
        self.assertEqual(incomplete.exception.detail["code"], "SCAN_PLAN_INCOMPLETE")

        preview = await get_zalo_scan_plan(container)
        self.assertFalse(preview["available"])

        container = FakeContainer()
        container.group_repo.snapshot["plan"]["bcnb_week_plan"]["latest_week"] = {
            "week": "not-a-week",
            "year": 2026,
        }
        preview = await get_zalo_scan_plan(container)
        self.assertFalse(preview["available"])

    async def test_scan_plan_read_route_exposes_snapshot_periods(self):
        container = FakeContainer()
        plan = await get_zalo_scan_plan(container)
        self.assertTrue(plan["available"])
        self.assertEqual(plan["latest_week"], {"week": 33, "year": 2026})
        self.assertEqual(plan["required_week"], {"week": 32, "year": 2026})

        container.group_repo.snapshot["observed_at"] = None
        self.assertFalse((await get_zalo_scan_plan(container))["available"])

        container.group_repo.snapshot = None
        self.assertEqual(
            await get_zalo_scan_plan(container),
            {"available": False, "plan": None},
        )

    async def test_group_routes_fail_closed_when_repository_read_fails(self):
        container = FakeContainer()

        async def fail_groups():
            raise RuntimeError("groups unavailable")

        async def fail_lifecycles(*, include_archived=False):
            raise RuntimeError("lifecycles unavailable")

        container.group_repo.get_scan_groups = fail_groups
        container.group_repo.get_class_lifecycles = fail_lifecycles
        with self.assertRaises(HTTPException):
            await get_groups(container)
        with self.assertRaises(HTTPException):
            await get_class_lifecycles(False, container)

    async def test_bcnb_production_mode_unlocks_zalo_and_lms_execution(self):
        container = FakeContainer()
        container.settings.bcnb_dry_run = False

        scan = await run_scan(container)
        analysis = await api_lms_analyze(
            LMSAnalyzeRequest(upload_id="upload-lms-production"),
            container,
        )

        self.assertFalse(scan["dry_run"])
        self.assertEqual(scan["worker_count"], 1)
        zalo_job = container.job_repo.created[0]
        self.assertEqual(zalo_job[1]["execution_mode"], "production")
        self.assertTrue(zalo_job[2]["mutates_external"])
        self.assertFalse(zalo_job[2]["retryable"])
        self.assertFalse(zalo_job[2]["recoverable"])

        self.assertFalse(analysis["dry_run"])
        lms_job = container.job_repo.created[1]
        self.assertEqual(lms_job[1]["execution_mode"], "production")
        self.assertEqual(
            container.job_repo.payloads[-1][1]["execution_mode"],
            "production",
        )

    async def test_googleform_routes_cover_successful_crud_ocr_and_submit(self):
        container = FakeContainer()
        self.assertEqual(
            await get_ignored_groups(container),
            {
                "groups": ["Old group"],
                "items": [
                    {
                        "group_name": "Old group",
                        "status": "PAUSED_OR_TRANSITIONING",
                    }
                ],
            },
        )
        self.assertNotIn(("archive", 14), container.group_repo.calls)
        confirmed = await confirm_ignored_group_ended(
            RestoreGroupRequest(group_name="Old group"),
            container,
        )
        self.assertIn("message", confirmed)
        restored = await restore_ignored_group(
            RestoreGroupRequest(group_name="Restored"),
            container,
        )
        self.assertIn("message", restored)
        container.group_repo.restore_result = False
        with self.assertRaises(HTTPException) as conflict:
            await restore_ignored_group(
                RestoreGroupRequest(group_name="Partial identity"),
                container,
            )
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(
            conflict.exception.detail["code"],
            "GROUP_RESTORE_CONFLICT",
        )

        self.assertEqual(
            await get_mappings(container),
            {"mappings": [{"id": 1}]},
        )
        await save_mapping(
            MappingItem(question="Question", field_type="text", value="Answer"),
            container,
        )
        await delete_mapping(1, container)
        self.assertEqual(await get_history(container), {"history": [{"student": "A"}]})
        cleared = await clear_history(container)
        self.assertEqual(cleared["deleted_count"], 3)
        self.assertIn(("clear_history",), container.report_repo.calls)

        del_ss = await delete_submission_screenshot(1, container)
        self.assertEqual(del_ss["message"], "Đã xóa ảnh chụp thành công")
        self.assertIn(("delete_screenshot", 1), container.report_repo.calls)

        # Screenshot endpoints coverage
        import tempfile
        from pathlib import Path
        ss_dir = Path(container.settings.runtime_root) / "screenshots" / "googleform"
        ss_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(ss_dir), suffix=".png", delete=False) as f:
            f.write(b"fake image content")
            real_file = f.name
        container.report_repo.screenshot_path = real_file

        resp = await get_submission_screenshot(1, container)
        self.assertEqual(resp.media_type, "image/png")
        if Path(real_file).exists():
            Path(real_file).unlink()

        with self.assertRaises(HTTPException) as forbidden:
            await get_submission_screenshot(2, container)
        self.assertEqual(forbidden.exception.detail["code"], "FORBIDDEN_PATH")

        with self.assertRaises(HTTPException) as missing_file:
            await get_submission_screenshot(3, container)
        self.assertEqual(missing_file.exception.detail["code"], "SCREENSHOT_FILE_NOT_FOUND")

        with self.assertRaises(HTTPException) as not_found:
            await get_submission_screenshot(999, container)
        self.assertEqual(not_found.exception.detail["code"], "SCREENSHOT_NOT_FOUND")

        with self.assertRaises(HTTPException) as sub_not_found:
            await delete_submission_screenshot(999, container)
        self.assertEqual(sub_not_found.exception.detail["code"], "SUBMISSION_NOT_FOUND")

        ocr = await api_run_googleform_ocr(
            OcrRequest(upload_ids=["upload-1"]),
            container,
        )
        self.assertEqual(ocr["job_id"], 41)
        self.assertEqual(
            container.job_repo.payloads[-1],
            (41, {"upload_ids": ["googleform_ocr:upload-1:41"]}),
        )

        lms_fetch = await api_fetch_googleform_lms_students(
            FetchLmsStudentsRequest(class_codes=["vt-c4k-ga41", "VT-C4K-GI35"]),
            container,
        )
        self.assertEqual(lms_fetch["job_id"], 41)
        self.assertEqual(
            lms_fetch["class_codes"],
            ["VT-C4K-GA41", "VT-C4K-GI35"],
        )
        self.assertEqual(
            container.job_repo.created[-1][0],
            "GOOGLEFORM_LMS_STUDENTS",
        )
        for bad_req, expected_code in (
            (FetchLmsStudentsRequest(class_codes=[]), "LMS_CLASS_CODES_EMPTY"),
            (
                FetchLmsStudentsRequest(
                    class_codes=["VT-C4K-GA41", "vt-c4k-ga41"]
                ),
                "LMS_CLASS_CODES_DUPLICATE",
            ),
            (
                FetchLmsStudentsRequest(class_codes=["bad_code"]),
                "LMS_CLASS_CODES_INVALID",
            ),
            (
                FetchLmsStudentsRequest(
                    class_codes=["VT-C4K-GA41"],
                    class_code="VT-C4K-GI35",
                ),
                "LMS_CLASS_CODES_INVALID",
            ),
        ):
            with self.assertRaises(HTTPException) as bad_lms_fetch:
                await api_fetch_googleform_lms_students(bad_req, container)
            self.assertEqual(bad_lms_fetch.exception.status_code, 422)
            self.assertEqual(
                bad_lms_fetch.exception.detail["code"],
                expected_code,
            )

        submitted = await run_submit(
            SubmitRequest(
                students=[StudentItem(name=" Student ", class_code=" vt-jsb ")],
                idempotency_key="1234567890abcdef",
            ),
            container,
        )
        self.assertEqual(submitted["idempotency_key"], "1234567890abcdef")
        submit_payload = container.job_repo.created[-1][1]
        self.assertEqual(
            submit_payload["students"],
            [{"name": "Student", "class_code": "VT-JSB"}],
        )
        self.assertEqual(len(submit_payload["idempotency_key_sha256"]), 64)
        self.assertNotEqual(submit_payload["idempotency_key"], "1234567890abcdef")

    async def test_production_submit_requires_client_idempotency_key(self):
        container = FakeContainer()
        container.settings.google_form_mode = "production"
        with self.assertRaises(HTTPException) as caught:
            await run_submit(
                SubmitRequest(
                    students=[StudentItem(name="Student", class_code="VT-JSB")]
                ),
                container,
            )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail["code"], "IDEMPOTENCY_KEY_REQUIRED")

    async def test_terminal_googleform_replay_is_truthful_and_not_requeued(self):
        container = FakeContainer()
        for status in ("FAILED", "CANCELLED"):
            container.job_repo.idempotent_result = {
                "job_id": 41,
                "created": False,
                "status": status,
            }
            scheduled_before = list(container.dispatcher.scheduled)
            with self.assertRaises(HTTPException) as caught:
                await run_submit(
                    SubmitRequest(
                        students=[StudentItem(name="Student", class_code="VT-JSB")],
                        idempotency_key=f"1234567890abcdef-{status.lower()}",
                    ),
                    container,
                )
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(
                caught.exception.detail["code"],
                f"IDEMPOTENCY_TERMINAL_{status}",
            )
            self.assertEqual(container.dispatcher.scheduled, scheduled_before)

    async def test_job_routes_cover_success_not_found_and_state_conflicts(self):
        container = FakeContainer()
        self.assertEqual(
            await get_latest_active_job(container),
            {"id": 41, "active_only": True},
        )
        self.assertEqual(
            await get_latest_job(container),
            {"id": 41, "active_only": False},
        )
        self.assertEqual((await get_job_status(41, container))["id"], 41)
        self.assertTrue((await cancel_job(41, container))["cancel_requested"])
        self.assertEqual((await retry_terminal_job(41, container))["job_id"], 77)
        self.assertEqual(
            await get_job_events(41, 5, container),
            {"events": [{"id": 1, "limit": 5}]},
        )
        self.assertEqual(
            await get_job_audit(41, 6, container),
            {"actions": [{"id": 2, "limit": 6}]},
        )

        container.job_repo.job = None
        for call in (
            lambda: get_job_status(99, container),
            lambda: get_job_events(99, 5, container),
            lambda: get_job_audit(99, 5, container),
        ):
            with self.assertRaises(HTTPException) as caught:
                await call()
            self.assertEqual(caught.exception.status_code, 404)

        container.job_repo.cancel_error = JobNotFoundError("private")
        with self.assertRaises(HTTPException) as caught:
            await cancel_job(99, container)
        self.assertEqual(caught.exception.detail["code"], "JOB_NOT_FOUND")

        container.job_repo.cancel_error = JobStateError("private")
        with self.assertRaises(HTTPException) as caught:
            await cancel_job(41, container)
        self.assertEqual(caught.exception.detail["code"], "JOB_STATE_CONFLICT")

        container.dispatcher.retry_error = JobNotFoundError("private")
        with self.assertRaises(HTTPException) as caught:
            await retry_terminal_job(99, container)
        self.assertEqual(caught.exception.detail["code"], "JOB_NOT_FOUND")

        container.dispatcher.retry_error = JobStateError("private")
        with self.assertRaises(HTTPException) as caught:
            await retry_terminal_job(41, container)
        self.assertEqual(caught.exception.detail["code"], "JOB_STATE_CONFLICT")

    async def test_lms_routes_cover_analysis_confirmation_and_plan_drift(self):
        container = FakeContainer()
        web_result = await api_lms_analyze(None, container)
        self.assertEqual(web_result["job_id"], 41)
        self.assertEqual(web_result["dry_run"], True)

        result = await api_lms_analyze(
            LMSAnalyzeRequest(upload_id="upload-lms"),
            container,
        )
        self.assertEqual(result["job_id"], 41)
        self.assertEqual(result["dry_run"], True)

        plan = {"can_confirm": True, "rows": [{"class_code": "VT-JSB"}]}
        confirmation = {
            "job_id": 42,
            "plan": plan,
            "plan_digest": compute_plan_digest(plan),
            "dry_run": True,
        }
        container.lms_repo = FakeLmsRepo(confirmation)
        confirmed = await api_lms_confirm(
            LMSConfirmRequest(
                analysis_job_id=41,
                confirmation_token="token",
            ),
            container,
        )
        self.assertEqual(confirmed["job_id"], 42)
        self.assertIn(42, container.dispatcher.scheduled)
        self.assertEqual(
            container.lms_repo.calls[-1]["dry_run"],
            container.settings.bcnb_dry_run,
        )

        confirmation["plan_digest"] = "changed"
        with self.assertRaises(HTTPException) as caught:
            await api_lms_confirm(
                LMSConfirmRequest(
                    analysis_job_id=41,
                    confirmation_token="token",
                ),
                container,
            )
        self.assertEqual(caught.exception.detail["code"], "LMS_PLAN_INVALID")
        self.assertEqual(container.job_repo.updates[-1][1]["status"], "FAILED")

    async def test_lms_confirmation_error_code_is_preserved(self):
        container = FakeContainer()
        container.lms_repo = FakeLmsRepo(
            error=ConfirmationError(
                "mode changed",
                code="LMS_CONFIRMATION_MODE_MISMATCH",
            )
        )
        with self.assertRaises(HTTPException) as caught:
            await api_lms_confirm(
                LMSConfirmRequest(
                    analysis_job_id=41,
                    confirmation_token="token",
                ),
                container,
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"],
            "LMS_CONFIRMATION_MODE_MISMATCH",
        )

        # Subset selection on api_lms_confirm
        subset_plan = {
            "can_confirm": True,
            "classes_to_tick": ["VT-JSB14", "VT-JSB15"],
        }
        subset_confirmation = {
            "job_id": 43,
            "plan": dict(subset_plan),
            "plan_digest": compute_plan_digest(subset_plan),
            "dry_run": True,
        }
        container.lms_repo = FakeLmsRepo(subset_confirmation)
        subset_res = await api_lms_confirm(
            LMSConfirmRequest(
                analysis_job_id=41,
                confirmation_token="token",
                selected_classes_to_tick=["vt-jsb15"],
            ),
            container,
        )
        self.assertEqual(subset_res["selected_classes_to_tick"], ["VT-JSB15"])

        # Reject outside plan (409) and empty selection (422)
        subset_confirmation["plan"] = dict(subset_plan)
        with self.assertRaises(HTTPException) as caught:
            await api_lms_confirm(
                LMSConfirmRequest(
                    analysis_job_id=41,
                    confirmation_token="token",
                    selected_classes_to_tick=["VT-OUTSIDE-99"],
                ),
                container,
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"],
            "LMS_SELECTED_CLASS_NOT_IN_PLAN",
        )

        subset_confirmation["plan"] = dict(subset_plan)
        with self.assertRaises(HTTPException) as caught:
            await api_lms_confirm(
                LMSConfirmRequest(
                    analysis_job_id=41,
                    confirmation_token="token",
                    selected_classes_to_tick=[],
                ),
                container,
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(
            caught.exception.detail["code"],
            "LMS_SELECTED_CLASSES_EMPTY",
        )

    async def test_run_scan_selective_rescan_filters_admitted_targets_and_rejects_invalid_keys(self):
        container = FakeContainer()
        container.group_repo.snapshot["plan"]["scan_targets"].append(
            {
                "person": "Teacher 2",
                "class_code": "VT-C4K-GI35",
                "lesson": "2",
                "class_date": "08/08/2026",
                "class_date_iso": "2026-08-08",
                "group_name": "MindX - VT-C4K-GI35 T7 (18H-20H)",
                "week": 32,
                "year": 2026,
            }
        )
        valid_key = "2026-W32|VT-C4K-GI35|2|2026-08-08"
        result = await run_scan(
            container,
            selected_target_keys=[valid_key],
        )
        self.assertEqual(result["target_count"], 1)
        self.assertTrue(result["selective_rescan"])
        created_payload = container.job_repo.created[-1][1]
        self.assertTrue(created_payload["scan_plan"]["selective_rescan"])
        self.assertEqual(
            created_payload["scan_plan"]["selected_target_keys"],
            [valid_key],
        )
        self.assertEqual(len(created_payload["scan_plan"]["targets"]), 1)
        self.assertEqual(
            created_payload["scan_plan"]["targets"][0]["class_code"],
            "VT-C4K-GI35",
        )

        # Reject unknown target key outside snapshot
        with self.assertRaises(HTTPException) as caught:
            await run_scan(
                container,
                selected_target_keys=["2026-W32|UNKNOWN|1|2026-08-08"],
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"],
            "SCAN_SELECTED_TARGET_NOT_IN_PLAN",
        )

        # Reject empty or duplicate target keys
        with self.assertRaises(HTTPException) as caught:
            await run_scan(container, selected_target_keys=[])
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(
            caught.exception.detail["code"],
            "SCAN_SELECTED_TARGETS_EMPTY",
        )

        with self.assertRaises(HTTPException) as caught:
            await run_scan(
                container,
                selected_target_keys=[valid_key, valid_key],
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(
            caught.exception.detail["code"],
            "SCAN_SELECTED_TARGETS_DUPLICATE",
        )

    async def test_manual_approve_zalo_report_validates_target_and_evidence_fail_closed(self):
        container = FakeContainer()
        container.job_repo.job = {
            "id": 900,
            "job_type": "ZALO_SCAN",
            "status": "PARTIAL_SUCCESS",
            "results": {
                "snapshot_id": 9,
                "items": [
                    {
                        "group": "MindX - VT-C4K-GI35 T7",
                        "person": "Teacher 2",
                        "status": "❌ Chưa tìm thấy trong khoảng đã kiểm chứng",
                        "evidence": {
                            "images": {
                                "latest": {
                                    "path": "/api/evidence/900/gi35.png",
                                    "evidence_status": "AVAILABLE",
                                }
                            }
                        },
                        "scan_target": {
                            "week": 32,
                            "year": 2026,
                            "class_code": "VT-C4K-GI35",
                            "lesson": "2",
                            "class_date": "08/08/2026",
                            "class_date_iso": "2026-08-08",
                            "group_name": "MindX - VT-C4K-GI35 T7",
                        },
                    }
                ],
            },
        }
        from types import SimpleNamespace
        fake_user = SimpleNamespace(username="operator-1")
        res = await manual_approve_zalo_report(
            container,
            user=fake_user,
            job_id=900,
            group_name="MindX - VT-C4K-GI35 T7",
            class_code="VT-C4K-GI35",
            week=32,
            year=2026,
            lesson="2",
            class_date_iso="2026-08-08",
            evidence_path="/api/evidence/900/gi35.png",
        )
        self.assertEqual(res["job_id"], 41)
        self.assertEqual(res["source_job_id"], 900)
        self.assertEqual(res["target_key"], "2026-W32|VT-C4K-GI35|2|2026-08-08")
        created_type, created_payload, _meta = container.job_repo.created[-1]
        self.assertEqual(created_type, "ZALO_MANUAL_APPROVE")
        self.assertEqual(created_payload["approved_by"], "operator-1")
        self.assertEqual(
            container.evidence_repo.calls[-1],
            ("/api/evidence/900/gi35.png", 900),
        )

        # Reject missing evidence path
        with self.assertRaises(HTTPException) as caught:
            await manual_approve_zalo_report(
                container,
                user=fake_user,
                job_id=900,
                group_name="MindX - VT-C4K-GI35 T7",
                class_code="VT-C4K-GI35",
                week=32,
                year=2026,
                lesson="2",
                class_date_iso="2026-08-08",
                evidence_path="   ",
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(
            caught.exception.detail["code"],
            "MANUAL_APPROVE_EVIDENCE_REQUIRED",
        )

        # Reject evidence path not belonging to target
        with self.assertRaises(HTTPException) as caught:
            await manual_approve_zalo_report(
                container,
                user=fake_user,
                job_id=900,
                group_name="MindX - VT-C4K-GI35 T7",
                class_code="VT-C4K-GI35",
                week=32,
                year=2026,
                lesson="2",
                class_date_iso="2026-08-08",
                evidence_path="/api/evidence/900/other.png",
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"],
            "MANUAL_APPROVE_EVIDENCE_NOT_IN_TARGET",
        )

        # Reject unavailable evidence in registry
        container.evidence_repo.verify_result = {
            "status": "MISSING",
            "reason": "File ảnh đã bị xóa",
        }
        with self.assertRaises(HTTPException) as caught:
            await manual_approve_zalo_report(
                container,
                user=fake_user,
                job_id=900,
                group_name="MindX - VT-C4K-GI35 T7",
                class_code="VT-C4K-GI35",
                week=32,
                year=2026,
                lesson="2",
                class_date_iso="2026-08-08",
                evidence_path="/api/evidence/900/gi35.png",
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["code"],
            "MANUAL_APPROVE_EVIDENCE_UNAVAILABLE",
        )

        # Cover all input validation & state guard branches
        for bad_kwargs, expected_code in (
            ({"job_id": 0}, "MANUAL_APPROVE_INVALID_INPUT"),
            ({"group_name": "  "}, "MANUAL_APPROVE_INVALID_INPUT"),
            ({"week": 99}, "MANUAL_APPROVE_INVALID_TARGET"),
            ({"lesson": ""}, "MANUAL_APPROVE_INVALID_TARGET"),
            ({"class_date_iso": "08-08-2026"}, "MANUAL_APPROVE_INVALID_TARGET"),
            ({"class_code": 123}, "MANUAL_APPROVE_INVALID_TARGET"),
        ):
            base_kwargs = {
                "job_id": 900,
                "group_name": "MindX - VT-C4K-GI35 T7",
                "class_code": "VT-C4K-GI35",
                "week": 32,
                "year": 2026,
                "lesson": "2",
                "class_date_iso": "2026-08-08",
                "evidence_path": "/api/evidence/900/gi35.png",
            }
            base_kwargs.update(bad_kwargs)
            with self.assertRaises(HTTPException) as caught:
                await manual_approve_zalo_report(container, user=fake_user, **base_kwargs)
            self.assertEqual(caught.exception.detail["code"], expected_code)

        # Target not in job
        with self.assertRaises(HTTPException) as caught:
            await manual_approve_zalo_report(
                container,
                user=fake_user,
                job_id=900,
                group_name="MindX - VT-C4K-GI35 T7",
                class_code="VT-C4K-GI35",
                week=32,
                year=2026,
                lesson="99",
                class_date_iso="2026-08-08",
                evidence_path="/api/evidence/900/gi35.png",
            )
        self.assertEqual(caught.exception.detail["code"], "MANUAL_APPROVE_TARGET_NOT_IN_JOB")

        # Already submitted target
        container.job_repo.job["results"]["items"][0]["status"] = "✅ Đã nộp – Có minh chứng"
        with self.assertRaises(HTTPException) as caught:
            await manual_approve_zalo_report(
                container,
                user=fake_user,
                job_id=900,
                group_name="MindX - VT-C4K-GI35 T7",
                class_code="VT-C4K-GI35",
                week=32,
                year=2026,
                lesson="2",
                class_date_iso="2026-08-08",
                evidence_path="/api/evidence/900/gi35.png",
            )
        self.assertEqual(caught.exception.detail["code"], "MANUAL_APPROVE_ALREADY_SUBMITTED")

        # Ambiguous duplicate target in job
        container.job_repo.job["results"]["items"][0]["status"] = "❌ Chưa báo cáo"
        container.job_repo.job["results"]["items"].append(
            dict(container.job_repo.job["results"]["items"][0])
        )
        with self.assertRaises(HTTPException) as caught:
            await manual_approve_zalo_report(
                container,
                user=fake_user,
                job_id=900,
                group_name="MindX - VT-C4K-GI35 T7",
                class_code="VT-C4K-GI35",
                week=32,
                year=2026,
                lesson="2",
                class_date_iso="2026-08-08",
                evidence_path="/api/evidence/900/gi35.png",
            )
        self.assertEqual(caught.exception.detail["code"], "MANUAL_APPROVE_AMBIGUOUS_TARGET")

        # Non-terminal, wrong job_type, missing source job
        container.job_repo.job["status"] = "RUNNING"
        with self.assertRaises(HTTPException) as caught:
            await manual_approve_zalo_report(
                container,
                user=fake_user,
                job_id=900,
                group_name="MindX - VT-C4K-GI35 T7",
                class_code="VT-C4K-GI35",
                week=32,
                year=2026,
                lesson="2",
                class_date_iso="2026-08-08",
                evidence_path="/api/evidence/900/gi35.png",
            )
        self.assertEqual(caught.exception.detail["code"], "SOURCE_JOB_NOT_TERMINAL")

        container.job_repo.job["job_type"] = "ZALO_DISCOVERY"
        with self.assertRaises(HTTPException) as caught:
            await manual_approve_zalo_report(
                container,
                user=fake_user,
                job_id=900,
                group_name="MindX - VT-C4K-GI35 T7",
                class_code="VT-C4K-GI35",
                week=32,
                year=2026,
                lesson="2",
                class_date_iso="2026-08-08",
                evidence_path="/api/evidence/900/gi35.png",
            )
        self.assertEqual(caught.exception.detail["code"], "MANUAL_APPROVE_INVALID_SOURCE_JOB")

        container.job_repo.job = None
        with self.assertRaises(HTTPException) as caught:
            await manual_approve_zalo_report(
                container,
                user=fake_user,
                job_id=900,
                group_name="MindX - VT-C4K-GI35 T7",
                class_code="VT-C4K-GI35",
                week=32,
                year=2026,
                lesson="2",
                class_date_iso="2026-08-08",
                evidence_path="/api/evidence/900/gi35.png",
            )
        self.assertEqual(caught.exception.detail["code"], "SOURCE_JOB_NOT_FOUND")

    def test_weekly_summary_csv_format_and_formula_injection_sanitization(self):
        from repositories.report_repository import (
            CSV_HEADERS,
            build_weekly_summary_csv,
            sanitize_csv_cell,
        )

        self.assertEqual(sanitize_csv_cell("=CMD|' /C calc'!A0"), "'=CMD|' /C calc'!A0")
        self.assertEqual(sanitize_csv_cell("+SUM(A1:A2)"), "'+SUM(A1:A2)")
        self.assertEqual(sanitize_csv_cell("-10+20"), "'-10+20")
        self.assertEqual(sanitize_csv_cell("@HYPERLINK()"), "'@HYPERLINK()")
        self.assertEqual(sanitize_csv_cell("\t=1+1"), "'=1+1")
        self.assertEqual(sanitize_csv_cell("Nguyễn\r\nVăn A"), "Nguyễn Văn A")

        csv_text = build_weekly_summary_csv(
            [
                {
                    "class_code": "VT-C4K-GI35",
                    "teacher": "=Nguyễn Văn A",
                    "lesson": "5",
                    "class_date": "18/10/2026",
                    "zalo_status": "✅ Đã nộp – Có minh chứng",
                    "lms_status": "✅ Đã nhận xét & Tích BCNB",
                    "verified_at": "2026-10-18T14:00:00Z",
                    "raw_chat_text": "TIN_NHAN_NHAY_CAM_KHONG_DUOC_XUAT",
                    "student_phone": "0909123456",
                }
            ]
        )
        self.assertTrue(csv_text.startswith("\ufeff"))
        lines = [line for line in csv_text.lstrip("\ufeff").split("\r\n") if line]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], ",".join(CSV_HEADERS))
        self.assertIn("VT-C4K-GI35,'=Nguyễn Văn A,5,18/10/2026", lines[1])
        self.assertNotIn("TIN_NHAN_NHAY_CAM_KHONG_DUOC_XUAT", csv_text)
        self.assertNotIn("0909123456", csv_text)

    async def test_weekly_summary_api_route_and_sqlite_aggregation(self):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        from api.zalo_routes import get_weekly_summary
        from db import database
        from repositories.report_repository import ReportRepository

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = str(Path(tmp_dir) / "weekly_summary_test.db")
            with patch.object(database, "DB_PATH", db_file):
                database.init_db()
                async with database.get_db_connection() as db:
                    await db.execute(
                        "INSERT INTO groups (group_name, person_in_charge, class_code) VALUES (?, ?, ?)",
                        ("MindX - VT-LMS-ONLY", "Lê Thị C", "VT-LMS-ONLY"),
                    )
                    # Job 1: Full ZALO_SCAN for W42/2026
                    full_results = {
                        "snapshot_id": 10,
                        "required_week": {"week": 42, "year": 2026},
                        "selected_weeks": [
                            {"week": 42, "year": 2026},
                            {"week": 43, "year": 2026},
                        ],
                        "items": [
                            {
                                "group": "VT-JSB16",
                                "person": "Trần Thị B",
                                "status": "✅ Đã nộp – Có minh chứng",
                                "timestamp": "2026-10-18T10:00:00Z",
                                "raw_message_secret": "NOI_DUNG_CHAT_RIENG_TU",
                                "scan_target": {
                                    "week": 42,
                                    "year": 2026,
                                    "class_code": "VT-JSB16",
                                    "lesson": "5",
                                    "class_date": "18/10/2026",
                                    "class_date_iso": "2026-10-18",
                                },
                                "evidence": {
                                    "scanned_at": "2026-10-18T10:00:00Z",
                                    "messages_checked": 14,
                                    "images": {
                                        "report": {
                                            "path": "/api/evidence/1/report.png",
                                            "evidence_status": "AVAILABLE",
                                        }
                                    },
                                },
                            },
                            {
                                "group": "VT-C4K-GI35",
                                "person": "Nguyễn Văn A",
                                "status": "❌ Chưa tìm thấy báo cáo",
                                "timestamp": "2026-10-18T10:02:00Z",
                                "scan_target": {
                                    "week": 42,
                                    "year": 2026,
                                    "class_code": "VT-C4K-GI35",
                                    "lesson": "2",
                                    "class_date": "17/10/2026",
                                    "class_date_iso": "2026-10-17",
                                },
                            },
                        ],
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (?, 'ZALO_SCAN', 'SUCCESS', '2026-10-18T09:55:00Z', '2026-10-18T10:05:00Z', ?, ?, 0)
                        """,
                        (
                            1,
                            json.dumps(full_results, ensure_ascii=False),
                            json.dumps(
                                {
                                    "execution_mode": "dry-run",
                                    "scan_plan": {
                                        "snapshot_id": 10,
                                        "required_week": {"week": 42, "year": 2026},
                                    },
                                }
                            ),
                        ),
                    )
                    # Job 2: Selective rescan ZALO_SCAN updating VT-C4K-GI35 in W42/2026
                    selective_results = {
                        "snapshot_id": 10,
                        "selective_rescan": True,
                        "required_week": {"week": 42, "year": 2026},
                        "items": [
                            {
                                "group": "VT-C4K-GI35",
                                "person": "Nguyễn Văn A",
                                "status": "✅ Đã nộp – Có minh chứng",
                                "timestamp": "2026-10-18T11:00:00Z",
                                "scan_target": {
                                    "week": 42,
                                    "year": 2026,
                                    "class_code": "VT-C4K-GI35",
                                    "lesson": "2",
                                    "class_date": "17/10/2026",
                                    "class_date_iso": "2026-10-17",
                                },
                                "evidence": {
                                    "scanned_at": "2026-10-18T11:00:00Z",
                                    "messages_checked": 18,
                                },
                            }
                        ],
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (?, 'ZALO_SCAN', 'SUCCESS', '2026-10-18T10:55:00Z', '2026-10-18T11:01:00Z', ?, ?, 0)
                        """,
                        (
                            2,
                            json.dumps(selective_results, ensure_ascii=False),
                            json.dumps(
                                {
                                    "execution_mode": "dry-run",
                                    "scan_plan": {
                                        "snapshot_id": 10,
                                        "selective_rescan": True,
                                        "required_week": {"week": 42, "year": 2026},
                                    },
                                }
                            ),
                        ),
                    )
                    # Job 3: LMS_ANALYSIS preview for W42/2026
                    lms_plan = {
                        "kind": "lms_preview",
                        "week": 42,
                        "year": 2026,
                        "classes_to_tick": ["VT-JSB16"],
                        "already_checked_classes": [],
                        "missing_classes": ["VT-C4K-GI35", "VT-LMS-ONLY"],
                        "missing_but_checked_classes": [],
                        "unmatched_classes": [],
                        "overdue_details": {
                            "VT-C4K-GI35": "Trễ 2 ngày",
                            "VT-LMS-ONLY": "Trễ 4 ngày",
                        },
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (3, 'LMS_ANALYSIS', 'SUCCESS', '2026-10-18T11:10:00Z', '2026-10-18T11:12:00Z', ?, '{}', 0)
                        """,
                        (json.dumps(lms_plan, ensure_ascii=False),),
                    )
                    # Job 4: LMS_AUTOMATION execution for W42/2026
                    lms_exec = {
                        "kind": "lms_execution",
                        "week": 42,
                        "year": 2026,
                        "dry_run": False,
                        "verified_classes": ["VT-JSB16"],
                        "failed_classes": [],
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (4, 'LMS_AUTOMATION', 'SUCCESS', '2026-10-18T11:15:00Z', '2026-10-18T11:16:00Z', ?, '{}', 1)
                        """,
                        (json.dumps(lms_exec, ensure_ascii=False),),
                    )
                    await db.commit()

                container = SimpleNamespace(report_repo=ReportRepository())

                # 1. Aggregated summary for W42/2026 merges Job 1 + Job 2 + LMS
                summary = await get_weekly_summary(
                    week=42,
                    year=2026,
                    container=container,
                )
                self.assertEqual(summary["week"], 42)
                self.assertEqual(summary["year"], 2026)
                self.assertEqual(summary["period_key"], "2026-W42")
                self.assertEqual(len(summary["jobs"]), 2)
                self.assertEqual(len(summary["rows"]), 3)
                rows_by_code = {r["class_code"]: r for r in summary["rows"]}
                self.assertEqual(
                    rows_by_code["VT-JSB16"]["lms_status"],
                    "✅ Đã nhận xét & Tích BCNB",
                )
                self.assertEqual(
                    rows_by_code["VT-C4K-GI35"]["zalo_status"],
                    "✅ Đã nộp – Có minh chứng",
                )
                self.assertEqual(
                    rows_by_code["VT-C4K-GI35"]["lms_status"],
                    "❌ Chưa nhận xét (Trễ 2 ngày)",
                )
                self.assertEqual(
                    rows_by_code["VT-LMS-ONLY"]["zalo_status"],
                    "Chưa quét Zalo",
                )
                self.assertEqual(
                    rows_by_code["VT-LMS-ONLY"]["teacher"],
                    "Lê Thị C",
                )
                # Ensure sensitive raw message field was stripped from sanitized items
                self.assertNotIn("NOI_DUNG_CHAT_RIENG_TU", json.dumps(summary))

                # 2. Querying explicit historical Job #1 shows unmerged pre-rescan status
                job1_summary = await get_weekly_summary(
                    week=42,
                    year=2026,
                    job_id=1,
                    container=container,
                )
                self.assertEqual(job1_summary["selected_job_id"], 1)
                job1_by_code = {r["class_code"]: r for r in job1_summary["rows"]}
                self.assertEqual(
                    job1_by_code["VT-C4K-GI35"]["zalo_status"],
                    "❌ Chưa tìm thấy báo cáo",
                )

                # 3. Exporting CSV via format='csv'
                csv_resp = await get_weekly_summary(
                    week=42,
                    year=2026,
                    format="csv",
                    container=container,
                )
                self.assertEqual(csv_resp.media_type, "text/csv; charset=utf-8")
                self.assertIn(
                    "Bao_Cao_Tuan_W42_2026.csv",
                    csv_resp.headers.get("Content-Disposition", ""),
                )
                csv_decoded = csv_resp.body.decode("utf-8")
                self.assertTrue(csv_decoded.startswith("\ufeff"))
                self.assertIn(
                    "Mã lớp,Giáo viên,Buổi học,Ngày học,Trạng thái Zalo,Trạng thái LMS,Tình trạng đánh giá,Mô tả vấn đề (TE/TF/QC),Thời gian kiểm chứng",
                    csv_decoded,
                )
                self.assertIn("VT-JSB16,Trần Thị B,5,18/10/2026", csv_decoded)

                # 4. Validation errors
                with self.assertRaises(HTTPException) as bad_period:
                    await get_weekly_summary(week=42, year=None, container=container)
                self.assertEqual(bad_period.exception.detail["code"], "INVALID_PERIOD")

                with self.assertRaises(HTTPException) as bad_fmt:
                    await get_weekly_summary(
                        week=42,
                        year=2026,
                        format="xlsx",
                        container=container,
                    )
                self.assertEqual(bad_fmt.exception.detail["code"], "INVALID_REPORT_FORMAT")

                with self.assertRaises(HTTPException) as missing_job:
                    await get_weekly_summary(
                        week=42,
                        year=2026,
                        job_id=999,
                        container=container,
                    )
                self.assertEqual(missing_job.exception.detail["code"], "JOB_NOT_FOUND")

                with self.assertRaises(HTTPException) as wrong_job_type:
                    await get_weekly_summary(
                        week=42,
                        year=2026,
                        job_id=3,
                        container=container,
                    )
                self.assertEqual(wrong_job_type.exception.detail["code"], "INVALID_SCAN_JOB")

                # 5. Cross-snapshot guard, late vs exempt Zalo classification, dry-run LMS separation, and >200 jobs history retention
                async with database.get_db_connection() as db:
                    # W41/2026 Job 10: Snapshot 10 full scan with [VT-SNAP10-A, VT-SNAP10-B]
                    snap10_results = {
                        "snapshot_id": 10,
                        "required_week": {"week": 41, "year": 2026},
                        "items": [
                            {
                                "group": "VT-SNAP10-A",
                                "person": "GV A",
                                "status": "✅ Đã nộp – Có minh chứng",
                                "scan_target": {
                                    "week": 41,
                                    "year": 2026,
                                    "class_code": "VT-SNAP10-A",
                                    "lesson": "1",
                                    "class_date": "10/10/2026",
                                    "class_date_iso": "2026-10-10",
                                },
                            },
                            {
                                "group": "VT-SNAP10-B",
                                "person": "GV B",
                                "status": "❌ Chưa tìm thấy báo cáo",
                                "scan_target": {
                                    "week": 41,
                                    "year": 2026,
                                    "class_code": "VT-SNAP10-B",
                                    "lesson": "2",
                                    "class_date": "11/10/2026",
                                    "class_date_iso": "2026-10-11",
                                },
                            },
                        ],
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (10, 'ZALO_SCAN', 'SUCCESS', '2026-10-11T10:00:00Z', '2026-10-11T10:05:00Z', ?, '{"execution_mode":"dry-run"}', 0)
                        """,
                        (json.dumps(snap10_results, ensure_ascii=False),),
                    )
                    # W41/2026 Job 11: Snapshot 20 selective rescan with [VT-SNAP20-LATE, VT-SNAP20-EXEMPT]
                    snap20_selective = {
                        "snapshot_id": 20,
                        "selective_rescan": True,
                        "required_week": {"week": 41, "year": 2026},
                        "items": [
                            {
                                "group": "VT-SNAP20-LATE",
                                "person": "GV Trễ",
                                "status": "🟡 Đã nộp trễ – Có minh chứng",
                                "scan_target": {
                                    "week": 41,
                                    "year": 2026,
                                    "class_code": "VT-SNAP20-LATE",
                                    "lesson": "3",
                                    "class_date": "11/10/2026",
                                    "class_date_iso": "2026-10-11",
                                },
                            },
                            {
                                "group": "VT-SNAP20-EXEMPT",
                                "person": "GV Miễn",
                                "status": "🟡 Miễn báo cáo (Buổi 14)",
                                "scan_target": {
                                    "week": 41,
                                    "year": 2026,
                                    "class_code": "VT-SNAP20-EXEMPT",
                                    "lesson": "14",
                                    "class_date": "11/10/2026",
                                    "class_date_iso": "2026-10-11",
                                },
                            },
                        ],
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (11, 'ZALO_SCAN', 'SUCCESS', '2026-10-11T11:00:00Z', '2026-10-11T11:05:00Z', ?, '{"execution_mode":"dry-run"}', 0)
                        """,
                        (json.dumps(snap20_selective, ensure_ascii=False),),
                    )
                    # W41/2026 Job 12: Dry-run LMS_AUTOMATION must count in dry_run_count, NOT ticked_count
                    w41_lms_dry = {
                        "kind": "lms_execution",
                        "week": 41,
                        "year": 2026,
                        "dry_run": True,
                        "verified_classes": ["VT-SNAP20-LATE"],
                        "failed_classes": [],
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (12, 'LMS_AUTOMATION', 'SUCCESS', '2026-10-11T11:10:00Z', '2026-10-11T11:12:00Z', ?, '{}', 0)
                        """,
                        (json.dumps(w41_lms_dry, ensure_ascii=False),),
                    )
                    # Insert 205 newer ZALO_SCAN jobs in W43/2026 to ensure W41 and W42 never disappear past 200 jobs
                    w43_payload = json.dumps(
                        {
                            "snapshot_id": 30,
                            "required_week": {"week": 43, "year": 2026},
                            "items": [],
                        }
                    )
                    await db.executemany(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (?, 'ZALO_SCAN', 'SUCCESS', '2026-10-25T10:00:00Z', '2026-10-25T10:01:00Z', ?, '{"execution_mode":"dry-run"}', 0)
                        """,
                        [(100 + idx, w43_payload) for idx in range(205)],
                    )
                    await db.commit()

                w41_summary = await get_weekly_summary(
                    week=41,
                    year=2026,
                    container=container,
                )
                w41_codes = [r["class_code"] for r in w41_summary["rows"]]
                # Must not mix Snapshot 10 rows into Snapshot 20 selective scan
                self.assertEqual(w41_codes, ["VT-SNAP20-LATE", "VT-SNAP20-EXEMPT"])
                # Late report must NOT be counted as exempt
                self.assertEqual(w41_summary["summary"]["zalo_exempt"], 1)
                self.assertEqual(w41_summary["summary"]["zalo_late"], 1)
                self.assertEqual(w41_summary["summary"]["zalo_pending"], 1)
                # Dry-run LMS must NOT be counted as production ticked_count
                self.assertEqual(w41_summary["lms_summary"]["ticked_count"], 0)
                self.assertEqual(w41_summary["lms_summary"]["dry_run_count"], 1)
                self.assertEqual(w41_summary["summary"]["lms_completed"], 0)
                self.assertEqual(w41_summary["summary"]["lms_dry_run"], 1)
                # Available weeks still include W41 and W42 even after >200 newer jobs
                avail_keys = {w["period_key"] for w in w41_summary["available_weeks"]}
                self.assertIn("2026-W41", avail_keys)
                self.assertIn("2026-W42", avail_keys)

                # 6. Manual approval reconciliation: scan twice in W40/2026 (Job 310, then Job 311),
                # then manually approve from Job 310 -> weekly summary must report "✅ Đã duyệt tay & Tích BCNB"
                async with database.get_db_connection() as db:
                    w40_scan1 = {
                        "snapshot_id": 40,
                        "required_week": {"week": 40, "year": 2026},
                        "items": [
                            {
                                "group": "VT-C4K-MA01",
                                "person": "GV Duyệt Tay",
                                "status": "❌ Chưa tìm thấy báo cáo",
                                "scan_target": {
                                    "week": 40,
                                    "year": 2026,
                                    "class_code": "VT-C4K-MA01",
                                    "lesson": "6",
                                    "class_date": "04/10/2026",
                                    "class_date_iso": "2026-10-04",
                                },
                                "evidence": {
                                    "scanned_at": "2026-10-04T10:00:00Z",
                                    "images": {
                                        "latest": {
                                            "path": "/api/evidence/310/latest.png",
                                            "evidence_status": "AVAILABLE",
                                        }
                                    },
                                },
                            }
                        ],
                    }
                    w40_scan2 = {
                        "snapshot_id": 40,
                        "required_week": {"week": 40, "year": 2026},
                        "items": [
                            {
                                "group": "VT-C4K-MA01",
                                "person": "GV Duyệt Tay",
                                "status": "❌ Chưa tìm thấy báo cáo",
                                "scan_target": {
                                    "week": 40,
                                    "year": 2026,
                                    "class_code": "VT-C4K-MA01",
                                    "lesson": "6",
                                    "class_date": "04/10/2026",
                                    "class_date_iso": "2026-10-04",
                                },
                            }
                        ],
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (310, 'ZALO_SCAN', 'SUCCESS', '2026-10-04T10:00:00Z', '2026-10-04T10:05:00Z', ?, '{"execution_mode":"production"}', 1)
                        """,
                        (json.dumps(w40_scan1, ensure_ascii=False),),
                    )
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (311, 'ZALO_SCAN', 'SUCCESS', '2026-10-04T11:00:00Z', '2026-10-04T11:05:00Z', ?, '{"execution_mode":"production"}', 1)
                        """,
                        (json.dumps(w40_scan2, ensure_ascii=False),),
                    )
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (id, job_type, status, created_at, finished_at, results, request_payload, mutates_external)
                        VALUES (312, 'ZALO_MANUAL_APPROVE', 'SUCCESS', '2026-10-04T12:00:00Z', '2026-10-04T12:00:05Z', '{}', '{"execution_mode":"production"}', 1)
                        """
                    )
                    await db.execute(
                        """
                        INSERT INTO external_action_audit (
                            job_id, action_key, action_type, target_type, target_id, mode,
                            reason_json, evidence_path, evidence_sha256,
                            outcome, created_at, completed_at
                        )
                        VALUES (
                            312,
                            'bcnb-manual-approve:production:310:2026:W40:VT-C4K-MA01:6:2026-10-04',
                            'BCNB_MANUAL_APPROVE_TICK',
                            'bcnb_checkbox',
                            'VT-C4K-MA01',
                            'production',
                            ?,
                            '/api/evidence/310/latest.png',
                            'ABC123SHA',
                            'VERIFIED',
                            '2026-10-04T12:00:00Z',
                            '2026-10-04T12:00:05Z'
                        )
                        """,
                        (
                            json.dumps(
                                {
                                    "manual_approve": True,
                                    "approved_by": "owner_admin",
                                    "source_job_id": 310,
                                    "group_name": "VT-C4K-MA01",
                                    "class_code": "VT-C4K-MA01",
                                    "week": 40,
                                    "year": 2026,
                                    "lesson": "6",
                                    "class_date_iso": "2026-10-04",
                                    "target_key": "2026-W40|VT-C4K-MA01|6|2026-10-04",
                                }
                            ),
                        ),
                    )
                    await db.commit()

                w40_summary = await get_weekly_summary(
                    week=40,
                    year=2026,
                    container=container,
                )
                self.assertEqual(w40_summary["snapshot_id"], 40)
                self.assertFalse(w40_summary["is_selective_only"])
                self.assertEqual(len(w40_summary["rows"]), 1)
                self.assertEqual(
                    w40_summary["rows"][0]["zalo_status"],
                    "✅ Đã duyệt tay & Tích BCNB",
                )
                self.assertEqual(
                    w40_summary["rows"][0]["verified_at"],
                    "2026-10-04T12:00:05Z",
                )
                self.assertEqual(w40_summary["summary"]["zalo_completed"], 1)
                self.assertEqual(w40_summary["summary"]["zalo_pending"], 0)
                self.assertTrue(w40_summary["items"][0]["manual_approved"])
                self.assertEqual(w40_summary["items"][0]["source_job_id"], 310)
                self.assertEqual(w40_summary["items"][0]["snapshot_id"], 40)

                # 7. Cross-snapshot manual approval row (not present in latest snapshot)
                # must preserve its own row snapshot_id and clear top-level snapshot_id
                async with database.get_db_connection() as db:
                    await db.execute(
                        """
                        INSERT INTO external_action_audit (
                            job_id, action_key, action_type, target_type, target_id, mode,
                            reason_json, evidence_path, evidence_sha256,
                            outcome, created_at, completed_at
                        )
                        VALUES (
                            312,
                            'bcnb-manual-approve:production:999:2026:W40:VT-C4K-EXTRA:2:2026-10-03',
                            'BCNB_MANUAL_APPROVE_TICK',
                            'bcnb_checkbox',
                            'VT-C4K-EXTRA',
                            'production',
                            ?,
                            '/api/evidence/310/extra.png',
                            'DEF456SHA',
                            'VERIFIED',
                            '2026-10-04T12:10:00Z',
                            '2026-10-04T12:10:05Z'
                        )
                        """,
                        (
                            json.dumps(
                                {
                                    "manual_approve": True,
                                    "approved_by": "owner_admin",
                                    "source_job_id": 999,
                                    "group_name": "VT-C4K-EXTRA",
                                    "class_code": "VT-C4K-EXTRA",
                                    "week": 40,
                                    "year": 2026,
                                    "lesson": "2",
                                    "class_date_iso": "2026-10-03",
                                    "target_key": "2026-W40|VT-C4K-EXTRA|2|2026-10-03",
                                }
                            ),
                        ),
                    )
                    await db.commit()

                w40_mixed_summary = await get_weekly_summary(
                    week=40,
                    year=2026,
                    container=container,
                )
                self.assertIsNone(w40_mixed_summary["snapshot_id"])
                self.assertEqual(len(w40_mixed_summary["items"]), 2)
                by_group = {
                    item["group"]: item for item in w40_mixed_summary["items"]
                }
                self.assertEqual(by_group["VT-C4K-MA01"]["snapshot_id"], 40)
                self.assertIsNone(by_group["VT-C4K-EXTRA"]["snapshot_id"])

                # 8. failed_only=True filters weekly summary JSON and CSV to only failed classes
                from api.zalo_routes import send_failed_csv_email_report

                w42_failed_json = await get_weekly_summary(
                    week=42,
                    year=2026,
                    failed_only=True,
                    container=container,
                )
                self.assertTrue(w42_failed_json["failed_only"])
                self.assertEqual(
                    [r["class_code"] for r in w42_failed_json["rows"]],
                    ["VT-C4K-GI35", "VT-LMS-ONLY"],
                )
                w42_failed_csv_resp = await get_weekly_summary(
                    week=42,
                    year=2026,
                    format="csv",
                    failed_only=True,
                    container=container,
                )
                self.assertIn(
                    "Bao_Cao_Lop_Loi_Tuan_W42_2026.csv",
                    w42_failed_csv_resp.headers.get("Content-Disposition", ""),
                )
                failed_csv_decoded = w42_failed_csv_resp.body.decode("utf-8")
                self.assertIn("VT-C4K-GI35", failed_csv_decoded)
                self.assertIn("VT-LMS-ONLY", failed_csv_decoded)
                self.assertNotIn("VT-JSB16", failed_csv_decoded)

                # 9. POST /api/reports/send-failed-csv-email defaults to Owner trial email (quangda@mindx.net.vn)
                container.settings = SimpleNamespace(
                    report_email_recipients=("quangda@mindx.net.vn",),
                    smtp_host="smtp.gmail.com",
                    smtp_port=587,
                    smtp_username="",
                    smtp_password="",
                    smtp_from="",
                    smtp_use_tls=True,
                )
                email_trial_resp = await send_failed_csv_email_report(
                    week=42,
                    year=2026,
                    container=container,
                )
                self.assertTrue(email_trial_resp["ok"])
                self.assertEqual(
                    email_trial_resp["delivery_status"], "SMTP_NOT_CONFIGURED"
                )
                self.assertEqual(
                    email_trial_resp["recipients"], ["quangda@mindx.net.vn"]
                )
                self.assertEqual(
                    email_trial_resp["available_managers"],
                    ["quangda@mindx.net.vn", "tuanpa@mindx.com.vn"],
                )
                self.assertEqual(email_trial_resp["failed_count"], 2)
                self.assertEqual(email_trial_resp["total_count"], 3)
                self.assertEqual(
                    email_trial_resp["filename"],
                    "Bao_Cao_Lop_Loi_Tuan_W42_2026.csv",
                )
                self.assertIn("quangda%40mindx.net.vn", email_trial_resp["gmail_compose_url"])

                # 10. Both managers (quangda@mindx.net.vn and tuanpa@mindx.com.vn) + SMTP delivery
                container.settings = SimpleNamespace(
                    report_email_recipients=(
                        "quangda@mindx.net.vn",
                        "tuanpa@mindx.com.vn",
                    ),
                    smtp_host="smtp.gmail.com",
                    smtp_port=587,
                    smtp_username="sender@mindx.net.vn",
                    smtp_password="app-password-placeholder",
                    smtp_from="sender@mindx.net.vn",
                    smtp_use_tls=True,
                )
                import ssl

                sent_messages = []
                observed_tls_contexts = []
                refused_to_return = {}

                class FakeSmtpClient:
                    def __init__(self, host, port, timeout=15.0, context=None):
                        self.host = host
                        self.port = port
                        if context is not None:
                            observed_tls_contexts.append(context)

                    def __enter__(self):
                        return self

                    def __exit__(self, exc_type, exc, tb):
                        return False

                    def ehlo(self):
                        pass

                    def starttls(self, context=None):
                        observed_tls_contexts.append(context)

                    def login(self, username, password):
                        pass

                    def send_message(self, msg):
                        sent_messages.append(msg)
                        return dict(refused_to_return)

                with (
                    patch("services.report_email_service.os.getenv", return_value="0"),
                    patch("services.report_email_service.smtplib.SMTP", FakeSmtpClient),
                ):
                    smtp_sent_resp = await send_failed_csv_email_report(
                        week=42,
                        year=2026,
                        recipients=["quangda@mindx.net.vn", "tuanpa@mindx.com.vn"],
                        container=container,
                    )
                self.assertEqual(smtp_sent_resp["delivery_status"], "SENT")
                self.assertEqual(
                    smtp_sent_resp["recipients"],
                    ["quangda@mindx.net.vn", "tuanpa@mindx.com.vn"],
                )
                self.assertEqual(
                    smtp_sent_resp["accepted_recipients"],
                    ["quangda@mindx.net.vn", "tuanpa@mindx.com.vn"],
                )
                self.assertEqual(smtp_sent_resp["refused_recipients"], [])
                self.assertIn("VT-C4K-GI35", smtp_sent_resp["csv_text"])
                self.assertNotIn("VT-JSB16", smtp_sent_resp["csv_text"])
                self.assertIn("<table", smtp_sent_resp["html_table"])
                self.assertIn("Mô tả vấn đề (TE/TF/QC)", smtp_sent_resp["html_table"])
                self.assertIn("Tình trạng đánh giá", smtp_sent_resp["html_table"])
                self.assertIn("┌", smtp_sent_resp["text_table"])
                self.assertIn("VT-C4K-GI35", smtp_sent_resp["text_table"])
                self.assertTrue(
                    ReportRepository.is_failed_weekly_summary_row(
                        {
                            "class_code": "VT-QUAL-ERR",
                            "zalo_status": "✅ Đã nộp – Có minh chứng",
                            "lms_status": "✅ Đã nộp LMS",
                            "evaluation_status": "⚠️ Đã nộp nhận xét nhưng còn lỗi chất lượng nội dung",
                            "issue_description": "[LMS] Chưa ghi nội dung bài học",
                        }
                    )
                )
                self.assertEqual(len(sent_messages), 1)
                self.assertEqual(
                    sent_messages[0]["To"],
                    "quangda@mindx.net.vn, tuanpa@mindx.com.vn",
                )
                self.assertIsInstance(observed_tls_contexts[-1], ssl.SSLContext)
                self.assertEqual(
                    observed_tls_contexts[-1].verify_mode, ssl.CERT_REQUIRED
                )
                self.assertTrue(observed_tls_contexts[-1].check_hostname)

                # Partial recipient rejection (550 for tuanpa@mindx.com.vn) must fail closed with 502 SMTP_RECIPIENT_REFUSED
                refused_to_return = {"tuanpa@mindx.com.vn": (550, b"User unknown")}
                with (
                    patch("services.report_email_service.os.getenv", return_value="0"),
                    patch("services.report_email_service.smtplib.SMTP", FakeSmtpClient),
                ):
                    with self.assertRaises(HTTPException) as partial_refused:
                        await send_failed_csv_email_report(
                            week=42,
                            year=2026,
                            recipients=[
                                "quangda@mindx.net.vn",
                                "tuanpa@mindx.com.vn",
                            ],
                            container=container,
                        )
                self.assertEqual(partial_refused.exception.status_code, 502)
                self.assertEqual(
                    partial_refused.exception.detail["code"],
                    "SMTP_RECIPIENT_REFUSED",
                )
                self.assertEqual(
                    partial_refused.exception.detail["accepted_recipients"],
                    ["quangda@mindx.net.vn"],
                )
                self.assertEqual(
                    partial_refused.exception.detail["refused_recipients"],
                    ["tuanpa@mindx.com.vn"],
                )
                self.assertIn(
                    "VT-C4K-GI35", partial_refused.exception.detail["csv_text"]
                )
                refused_to_return = {}

                # Port 465 SSL path and SMTP failure path
                container.settings.smtp_port = 465
                with (
                    patch("services.report_email_service.os.getenv", return_value="0"),
                    patch("services.report_email_service.smtplib.SMTP_SSL", FakeSmtpClient),
                ):
                    ssl_resp = await send_failed_csv_email_report(
                        week=42,
                        year=2026,
                        container=container,
                    )
                self.assertEqual(ssl_resp["delivery_status"], "SENT")
                self.assertIsInstance(observed_tls_contexts[-1], ssl.SSLContext)
                self.assertEqual(
                    observed_tls_contexts[-1].verify_mode, ssl.CERT_REQUIRED
                )
                self.assertTrue(observed_tls_contexts[-1].check_hostname)

                with (
                    patch("services.report_email_service.os.getenv", return_value="0"),
                    patch(
                        "services.report_email_service.smtplib.SMTP_SSL",
                        side_effect=RuntimeError("smtp connection refused"),
                    ),
                ):
                    with self.assertRaises(HTTPException) as smtp_err:
                        await send_failed_csv_email_report(
                            week=42,
                            year=2026,
                            container=container,
                        )
                self.assertEqual(smtp_err.exception.status_code, 502)
                self.assertEqual(smtp_err.exception.detail["code"], "SMTP_SEND_FAILED")

                # 11. Reject unauthorized external recipient, malformed email, no failed classes (W40), and empty week (W43)
                with self.assertRaises(HTTPException) as unauth_rec:
                    await send_failed_csv_email_report(
                        week=42,
                        year=2026,
                        recipients=["external@evil.example.com"],
                        container=container,
                    )
                self.assertEqual(
                    unauth_rec.exception.detail["code"],
                    "UNAUTHORIZED_EMAIL_RECIPIENT",
                )

                with self.assertRaises(HTTPException) as bad_rec:
                    await send_failed_csv_email_report(
                        week=42,
                        year=2026,
                        recipients=["not-an-email"],
                        container=container,
                    )
                self.assertEqual(
                    bad_rec.exception.detail["code"],
                    "INVALID_EMAIL_RECIPIENTS",
                )

                # W40 has only manually-approved passing classes -> NO_FAILED_CLASSES
                with self.assertRaises(HTTPException) as no_failed:
                    await send_failed_csv_email_report(
                        week=40,
                        year=2026,
                        container=container,
                    )
                self.assertEqual(no_failed.exception.status_code, 409)
                self.assertEqual(
                    no_failed.exception.detail["code"], "NO_FAILED_CLASSES"
                )

                # W43 has 0 rows -> EMPTY_WEEKLY_SUMMARY
                with self.assertRaises(HTTPException) as empty_week:
                    await send_failed_csv_email_report(
                        week=43,
                        year=2026,
                        container=container,
                    )
                self.assertEqual(empty_week.exception.status_code, 409)
                self.assertEqual(
                    empty_week.exception.detail["code"], "EMPTY_WEEKLY_SUMMARY"
                )

    async def test_weekly_summary_resolves_lms_status_and_evaluation_status_from_bcnb_meta(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from db import database
        from repositories.report_repository import ReportRepository

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = str(Path(tmp_dir) / "weekly_summary_bcnb_meta.db")
            with patch.object(database, "DB_PATH", db_file):
                database.init_db()
                async with database.get_db_connection() as db:
                    scan_results = {
                        "snapshot_id": 25,
                        "required_week": {"week": 43, "year": 2026},
                        "selected_weeks": [
                            {"week": 43, "year": 2026},
                            {"week": 44, "year": 2026},
                        ],
                        "items": [
                            {
                                "group": "MindX - VT-C4K-PTB16",
                                "person": "Đỗ Thị Thùy Trang",
                                "status": "❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng",
                                "timestamp": "2026-10-29T01:00:00Z",
                                "scan_target": {
                                    "week": 43,
                                    "year": 2026,
                                    "class_code": "VT-C4K-PTB16",
                                    "lesson": "1",
                                    "class_date": "24/10/2026",
                                    "class_date_iso": "2026-10-24",
                                    "bcnb_session_id": "6274",
                                    "khoi": "Coding",
                                    "nx_zalo_checked": False,
                                    "nx_lms_checked": True,
                                    "instructor_note": "Chưa nội dung bài học",
                                    "evaluation_status": "⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo",
                                },
                                "evidence": {
                                    "scanned_at": "2026-10-29T01:00:00Z",
                                    "date_context": {
                                        "target_week": 43,
                                        "target_year": 2026,
                                        "target_lesson": "1",
                                        "target_class_code": "VT-C4K-PTB16",
                                        "target_class_date": "24/10/2026",
                                    },
                                },
                            },
                            {
                                "group": "MindX - VT-C4K-PTB16",
                                "person": "Đỗ Thị Thùy Trang",
                                "status": "❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng",
                                "timestamp": "2026-10-29T01:02:00Z",
                                "scan_target": {
                                    "week": 43,
                                    "year": 2026,
                                    "class_code": "VT-C4K-PTB16",
                                    "lesson": "2",
                                    "class_date": "25/10/2026",
                                    "class_date_iso": "2026-10-25",
                                },
                                "evidence": {
                                    "scanned_at": "2026-10-29T01:02:00Z",
                                    "date_context": {
                                        "target_week": 43,
                                        "target_year": 2026,
                                        "target_lesson": "2",
                                        "target_class_code": "VT-C4K-PTB16",
                                        "target_class_date": "25/10/2026",
                                    },
                                },
                            },
                        ],
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (
                            id, job_type, status, created_at, started_at, finished_at,
                            results, request_payload, mutates_external
                        ) VALUES (?, 'ZALO_SCAN', 'PARTIAL_SUCCESS', ?, ?, ?, ?, '{}', 0)
                        """,
                        (
                            397,
                            "2026-10-29T00:55:00Z",
                            "2026-10-29T00:56:00Z",
                            "2026-10-29T01:05:00Z",
                            json.dumps(scan_results, ensure_ascii=False),
                        ),
                    )
                    await db.commit()

                summary = await ReportRepository().get_weekly_summary(week=43, year=2026)
                self.assertEqual(summary["summary"]["total"], 2)
                self.assertEqual(summary["summary"]["lms_completed"], 1)
                row = summary["rows"][0]
                self.assertEqual(row["class_code"], "VT-C4K-PTB16")
                self.assertEqual(row["lesson"], "1")
                self.assertEqual(row["lms_status"], "✅ Đã nộp LMS")
                self.assertEqual(
                    row["evaluation_status"],
                    "⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo",
                )
                self.assertEqual(row["instructor_note"], "Chưa nội dung bài học")
                self.assertEqual(row["khoi"], "Coding")
                self.assertEqual(row["bcnb_session_id"], "6274")
                row2 = summary["rows"][1]
                self.assertEqual(row2["lesson"], "2")
                self.assertEqual(row2["lms_status"], "Chưa quét LMS")
                self.assertEqual(row2["bcnb_session_id"], "")
                self.assertEqual(row2["instructor_note"], "")

                # Ambiguous same-lesson rows (date 24 has meta, date 25 has no meta):
                # date-missing scan item and LMS-only row must NOT inherit date 24's meta.
                async with database.get_db_connection() as db:
                    snap_plan = {
                        "bcnb_week_plan": {
                            "required_week": {"week": 43, "year": 2026},
                            "required_data": [
                                {
                                    "ma_lop": "VT-AMB01",
                                    "buoi_so": "1",
                                    "ngay_hoc": "24/10/2026",
                                    "ngay_hoc_iso": "2026-10-24",
                                    "qc_issue_note": "Ghi chú ngày 24",
                                    "nx_lms_checked": True,
                                },
                                {
                                    "ma_lop": "VT-AMB01",
                                    "buoi_so": "1",
                                    "ngay_hoc": "25/10/2026",
                                    "ngay_hoc_iso": "2026-10-25",
                                },
                                {
                                    "ma_lop": "VT-LMS-MULTI",
                                    "buoi_so": "1",
                                    "ngay_hoc": "24/10/2026",
                                    "ngay_hoc_iso": "2026-10-24",
                                    "qc_issue_note": "Ghi chú buổi 1",
                                },
                                {
                                    "ma_lop": "VT-LMS-MULTI",
                                    "buoi_so": "2",
                                    "ngay_hoc": "25/10/2026",
                                    "ngay_hoc_iso": "2026-10-25",
                                },
                                {
                                    "ma_lop": "VT-DUP-SID",
                                    "buoi_so": "1",
                                    "ngay_hoc": "24/10/2026",
                                    "ngay_hoc_iso": "2026-10-24",
                                    "bcnb_session_id": "session-A",
                                    "nx_lms_checked": True,
                                    "qc_issue_note": "QC FOR SESSION A",
                                },
                                {
                                    "ma_lop": "VT-DUP-SID",
                                    "buoi_so": "1",
                                    "ngay_hoc": "24/10/2026",
                                    "ngay_hoc_iso": "2026-10-24",
                                    "bcnb_session_id": "session-B",
                                },
                            ],
                        }
                    }
                    await db.execute(
                        """
                        INSERT INTO discovery_snapshots (
                            id, observed_at, fingerprint_sha256,
                            source_count, recognized_count, expected_count, delta_count,
                            coverage, status, plan_json
                        ) VALUES (25, '2026-10-29T00:50:00Z', 'abc', 2, 2, 2, 0, 1.0, 'PROMOTED', ?)
                        """,
                        (json.dumps(snap_plan, ensure_ascii=False),),
                    )
                    amb_scan_results = {
                        "snapshot_id": 25,
                        "required_week": {"week": 43, "year": 2026},
                        "selected_weeks": [{"week": 43, "year": 2026}],
                        "items": [
                            {
                                "group": "MindX - VT-AMB01",
                                "person": "GV Amb",
                                "status": "❌ Chưa tìm thấy báo cáo",
                                "scan_target": {
                                    "week": 43,
                                    "year": 2026,
                                    "class_code": "VT-AMB01",
                                    "lesson": "1",
                                },
                            },
                            {
                                "group": "MindX - VT-DUP-SID",
                                "person": "GV Dup",
                                "status": "❌ Chưa tìm thấy báo cáo",
                                "scan_target": {
                                    "week": 43,
                                    "year": 2026,
                                    "class_code": "VT-DUP-SID",
                                    "lesson": "1",
                                    "class_date_iso": "2026-10-24",
                                    "bcnb_session_id": "session-B",
                                },
                            },
                        ],
                    }
                    await db.execute(
                        "UPDATE scan_jobs SET results = ? WHERE id = 397",
                        (json.dumps(amb_scan_results, ensure_ascii=False),),
                    )
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (
                            id, job_type, status, created_at, finished_at, results, request_payload, mutates_external
                        ) VALUES (398, 'LMS_ANALYSIS', 'SUCCESS', '2026-10-29T01:10:00Z', '2026-10-29T01:11:00Z', ?, '{}', 0)
                        """,
                        (
                            json.dumps(
                                {
                                    "week": 43,
                                    "year": 2026,
                                    "already_checked_classes": ["VT-LMS-MULTI"],
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
                    await db.commit()

                amb_summary = await ReportRepository().get_weekly_summary(week=43, year=2026)
                by_code = {r["class_code"]: r for r in amb_summary["rows"]}
                self.assertEqual(by_code["VT-AMB01"]["qc_issue_note"], "")
                self.assertEqual(by_code["VT-AMB01"]["lms_status"], "Chưa quét LMS")
                self.assertEqual(by_code["VT-LMS-MULTI"]["qc_issue_note"], "")
                self.assertEqual(by_code["VT-DUP-SID"]["bcnb_session_id"], "session-B")
                self.assertEqual(by_code["VT-DUP-SID"]["qc_issue_note"], "")
                self.assertEqual(by_code["VT-DUP-SID"]["lms_status"], "Chưa quét LMS")

    async def test_bcnb_meta_isolation_warning_override_and_cleared_notes(self):
        from datetime import datetime
        from unittest.mock import AsyncMock, patch
        from repositories.report_repository import (
            _resolve_row_evaluation_status,
            _sanitize_dashboard_item,
        )
        from services.zalo_bcnb_service import (
            _attach_bcnb_row_metadata,
            _result_matches_schedule_row,
            update_bcnb_reports,
        )
        from services.zalo_parallel_service import _empty_result

        # 1. Contradictory LMS warning or raw BCNB warning must not be masked
        eval_warn = _resolve_row_evaluation_status(
            zalo_status="✅ Đã nộp – Có minh chứng",
            lms_status="⚠️ Chưa nhận xét - BCNB đã tích",
            nx_zalo_checked=True,
            nx_lms_checked=True,
        )
        self.assertEqual(eval_warn, "⚠️ Đã nhận xét Zalo, LMS cần kiểm tra")
        eval_raw_warn = _resolve_row_evaluation_status(
            zalo_status="✅ Đã nộp",
            lms_status="✅ Đã nộp LMS",
            raw_evaluation_status="⚠️ Cần kiểm tra nhận xét",
        )
        self.assertEqual(eval_raw_warn, "⚠️ Cần kiểm tra nhận xét")

        # 2. Early-stopped Zalo scan result updates BCNB metadata without weakening checkbox safety gate
        early_target = {
            "week": 43,
            "year": 2026,
            "class_code": "VT-TEST01",
            "group_name": "MindX - VT-TEST01",
            "lesson": "1",
            "class_date": "24/10/2026",
            "class_date_iso": "2026-10-24",
            "qc_issue_note": "stale QC note",
            "nx_lms_checked": False,
        }
        early_item = _empty_result(
            dict(early_target),
            today=datetime(2026, 10, 29),
            reason="synthetic sync failure",
            decision="ZALO_SYNC_INCOMPLETE",
        )
        bcnb_row = {
            "week": 43,
            "year": 2026,
            "ma_lop": "VT-TEST01",
            "buoi_so": "1",
            "ngay_hoc_iso": "2026-10-24",
            "qc_issue_note": "",
            "nx_lms_checked": True,
            "cells": [],
        }
        self.assertFalse(_result_matches_schedule_row(early_item, bcnb_row, (43, 2026)))
        plan = {
            "selected_weeks": [{"week": 43, "year": 2026}, {"week": 44, "year": 2026}],
            "latest_week": {"week": 44, "year": 2026},
            "targets": [early_target],
        }
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                new=AsyncMock(return_value=[bcnb_row]),
            ),
            patch(
                "services.zalo_bcnb_service.resolve_report_columns",
                new=AsyncMock(return_value=(0, 1, 2)),
            ),
            patch("services.zalo_bcnb_service.log_message"),
        ):
            await update_bcnb_reports(
                object(),
                job_id=1,
                execution_mode="dry-run",
                scan_results=[early_item],
                today=datetime(2026, 10, 29),
                scan_plan=plan,
                job_repo=type("Jobs", (), {"ensure_not_cancelled": AsyncMock()})(),
                audit_repo=object(),
                period_filter={(43, 2026)},
            )
        sanitized_early = _sanitize_dashboard_item(
            early_item,
            week=43,
            year=2026,
            source_job_id=1,
            lms_status="Chưa quét LMS",
            job_timestamp="2026-10-29",
        )
        self.assertEqual(sanitized_early["qc_issue_note"], "")
        self.assertIs(sanitized_early["nx_lms_checked"], True)
        self.assertEqual(sanitized_early["lms_status"], "✅ Đã nộp LMS")

        # 3. Cleared note on BCNB must overwrite stale scan_target / fallback note
        stale_item = {
            "group": "MindX - VT-C4K-PTB16",
            "person": "GV A",
            "status": "❌ Chưa tìm thấy báo cáo",
            "scan_target": {
                "week": 43,
                "year": 2026,
                "class_code": "VT-C4K-PTB16",
                "lesson": "1",
                "class_date": "24/10/2026",
                "qc_issue_note": "Ghi chú QC cũ",
                "instructor_note": "Giải trình cũ",
            },
        }
        _attach_bcnb_row_metadata(
            stale_item,
            {
                "qc_issue_note": "",
                "instructor_note": "",
                "nx_lms_checked": True,
            },
        )
        self.assertEqual(stale_item["qc_issue_note"], "")
        self.assertEqual(stale_item["instructor_note"], "")
        sanitized = _sanitize_dashboard_item(
            stale_item,
            week=43,
            year=2026,
            source_job_id=1,
            lms_status="Chưa quét LMS",
            job_timestamp="2026-10-29T01:00:00Z",
            fallback_bcnb_meta={
                "qc_issue_note": "Ghi chú snapshot cũ",
                "instructor_note": "Giải trình snapshot cũ",
            },
        )
        self.assertEqual(sanitized["qc_issue_note"], "")
        self.assertEqual(sanitized["issue_description"], "")
        self.assertEqual(sanitized["instructor_note"], "")

        # 4. Same-source duplicate rows and cross-session manual approvals stay isolated
        import json
        import os
        import tempfile
        from db import database
        from repositories.report_repository import ReportRepository

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "dup_session.sqlite3")
            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                async with database.get_db_connection() as db:
                    old_approved_b = {
                        "group": "MindX - VT-SYNTH01",
                        "person": "GV Synth",
                        "status": "✅ Đã duyệt tay & Tích BCNB",
                        "manual_approved": True,
                        "manual_approval": {
                            "outcome": "VERIFIED",
                            "dry_run": False,
                            "source_job_id": 1,
                            "approved_by": "Reviewer B",
                        },
                        "scan_target": {
                            "week": 43,
                            "year": 2026,
                            "class_code": "VT-SYNTH01",
                            "lesson": "1",
                            "class_date_iso": "2026-10-24",
                            "bcnb_session_id": "B",
                        },
                    }
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (
                            id, job_type, status, created_at, finished_at, results, request_payload, mutates_external
                        ) VALUES (1, 'ZALO_SCAN', 'SUCCESS', '2026-10-28', '2026-10-28', ?, '{}', 0)
                        """,
                        (
                            json.dumps(
                                {
                                    "snapshot_id": 24,
                                    "required_week": {"week": 43, "year": 2026},
                                    "items": [old_approved_b],
                                }
                            ),
                        ),
                    )
                    current_items = [
                        {
                            "group": "MindX - VT-SYNTH01",
                            "person": "GV Synth",
                            "status": "❌ Chưa tìm thấy",
                            "qc_issue_note": "FIRST QC ISSUE",
                            "nx_lms_checked": False,
                            "scan_target": {
                                "week": 43,
                                "year": 2026,
                                "class_code": "VT-SYNTH01",
                                "lesson": "1",
                                "class_date_iso": "2026-10-24",
                                "bcnb_session_id": "A",
                            },
                        },
                        {
                            "group": "MindX - VT-SYNTH01",
                            "person": "GV Synth",
                            "status": "❌ Chưa tìm thấy",
                            "qc_issue_note": "",
                            "nx_lms_checked": True,
                            "scan_target": {
                                "week": 43,
                                "year": 2026,
                                "class_code": "VT-SYNTH01",
                                "lesson": "1",
                                "class_date_iso": "2026-10-24",
                                "bcnb_session_id": "A",
                            },
                        },
                    ]
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (
                            id, job_type, status, created_at, finished_at, results, request_payload, mutates_external
                        ) VALUES (2, 'ZALO_SCAN', 'SUCCESS', '2026-10-29', '2026-10-29', ?, '{}', 0)
                        """,
                        (
                            json.dumps(
                                {
                                    "snapshot_id": 25,
                                    "required_week": {"week": 43, "year": 2026},
                                    "items": current_items,
                                }
                            ),
                        ),
                    )
                    await db.commit()
                dup_summary = await ReportRepository().get_weekly_summary(
                    week=43, year=2026
                )
                self.assertEqual(len(dup_summary["rows"]), 2)
                self.assertEqual(dup_summary["rows"][0]["qc_issue_note"], "FIRST QC ISSUE")
                self.assertEqual(dup_summary["rows"][0]["zalo_status"], "❌ Chưa tìm thấy")
                self.assertEqual(dup_summary["rows"][1]["qc_issue_note"], "")
                self.assertEqual(dup_summary["rows"][1]["zalo_status"], "❌ Chưa tìm thấy")

        with tempfile.TemporaryDirectory() as tmp_dir2:
            db_path2 = os.path.join(tmp_dir2, "synth_ma_session.sqlite3")
            with patch.object(database, "DB_PATH", db_path2):
                database.init_db()
                async with database.get_db_connection() as db:
                    await db.execute(
                        """
                        INSERT INTO discovery_snapshots (
                            id, observed_at, fingerprint_sha256,
                            source_count, recognized_count, expected_count, delta_count,
                            coverage, status, plan_json
                        ) VALUES (1, '2026-10-29T00:00:00Z', 'abc', 1, 1, 1, 0, 1.0, 'PROMOTED', ?)
                        """,
                        (
                            json.dumps(
                                {
                                    "scan_targets": [
                                        {
                                            "week": 43,
                                            "year": 2026,
                                            "class_code": "VT-SYNTH02",
                                            "lesson": "1",
                                            "class_date_iso": "2026-10-24",
                                            "bcnb_session_id": "A",
                                            "qc_issue_note": "SESSION A ISSUE",
                                            "nx_lms_checked": True,
                                        }
                                    ]
                                }
                            ),
                        ),
                    )
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (
                            id, job_type, status, created_at, finished_at, results, request_payload, mutates_external
                        ) VALUES (99, 'ZALO_MANUAL_APPROVE', 'SUCCESS', '2026-10-29', '2026-10-29', '{}', '{}', 1)
                        """
                    )
                    await db.execute(
                        """
                        INSERT INTO external_action_audit (
                            job_id, action_key, action_type, target_type, target_id, mode, reason_json, outcome, completed_at, created_at
                        ) VALUES (99, 'ma-b-1', 'BCNB_MANUAL_APPROVE_TICK', 'bcnb_row', 'VT-SYNTH02', 'production', ?, 'VERIFIED', '2026-10-29T01:00:00Z', '2026-10-29T01:00:00Z')
                        """,
                        (
                            json.dumps(
                                {
                                    "week": 43,
                                    "year": 2026,
                                    "class_code": "VT-SYNTH02",
                                    "lesson": "1",
                                    "class_date_iso": "2026-10-24",
                                    "bcnb_session_id": "B",
                                    "approved_by": "Approver B",
                                }
                            ),
                        ),
                    )
                    await db.commit()
                # Branch 2: no ZALO_SCAN jobs for the week -> elif period_manual_approvals
                no_job_summary = await ReportRepository().get_weekly_summary(
                    week=43, year=2026
                )
                self.assertEqual(len(no_job_summary["rows"]), 1)
                self.assertEqual(no_job_summary["rows"][0]["bcnb_session_id"], "B")
                self.assertEqual(no_job_summary["rows"][0]["qc_issue_note"], "")
                self.assertEqual(no_job_summary["rows"][0]["lms_status"], "Chưa quét LMS")

                # Branch 1: ZALO_SCAN job exists for the week (for another class), so VT-SYNTH02 hits the else/should_synthesize_missing_row branch
                async with database.get_db_connection() as db:
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (
                            id, job_type, status, created_at, finished_at, results, request_payload, mutates_external
                        ) VALUES (10, 'ZALO_SCAN', 'SUCCESS', '2026-10-29', '2026-10-29', ?, '{}', 0)
                        """,
                        (
                            json.dumps(
                                {
                                    "snapshot_id": 1,
                                    "required_week": {"week": 43, "year": 2026},
                                    "items": [
                                        {
                                            "group": "MindX - VT-OTHER01",
                                            "person": "GV Other",
                                            "status": "✅ Đã nộp bài",
                                            "scan_target": {
                                                "week": 43,
                                                "year": 2026,
                                                "class_code": "VT-OTHER01",
                                                "lesson": "1",
                                                "class_date_iso": "2026-10-24",
                                                "bcnb_session_id": "OTHER",
                                            },
                                        }
                                    ],
                                }
                            ),
                        ),
                    )
                    await db.commit()
                with_job_summary = await ReportRepository().get_weekly_summary(
                    week=43, year=2026
                )
                synth_rows = [
                    r for r in with_job_summary["rows"] if r["class_code"] == "VT-SYNTH02"
                ]
                self.assertEqual(len(synth_rows), 1)
                self.assertEqual(synth_rows[0]["bcnb_session_id"], "B")
                self.assertEqual(synth_rows[0]["qc_issue_note"], "")
                self.assertEqual(synth_rows[0]["lms_status"], "Chưa quét LMS")

    def test_lesson_14_exemption_normalizes_legacy_insufficient_evidence_row(self):
        from repositories.report_repository import (
            _sanitize_dashboard_item,
            build_weekly_summary_csv,
            is_failed_weekly_summary_row,
        )

        legacy_l14_item = {
            "group": "MindX - Lập trình Game VT-C4K-GA37-T6 (18H-20H)",
            "person": "Đỗ Thị Thùy Trang",
            "status": "⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công",
            "status_detail": (
                "Buổi 14 được miễn báo cáo Zalo.; Không xác minh được tên nhóm Zalo thực tế.; "
                "Không đọc được tin nhắn Zalo."
            ),
            "evidence_state": "LESSON_14_EXEMPTION",
            "submitted_for_bcnb": False,
            "nx_lms_checked": True,
            "evaluation_status": "⚠️ Đã hoàn tất nhận xét LMS, Zalo cần kiểm tra",
            "scan_target": {
                "week": 43,
                "year": 2026,
                "class_code": "VT-C4K-GA37",
                "lesson": "14",
                "class_date": "23/10/2026",
                "class_date_iso": "2026-10-23",
                "nx_lms_checked": True,
                "evaluation_status": "⚠️ Đã hoàn tất nhận xét LMS, Zalo cần kiểm tra",
            },
            "evidence": {
                "date_context": {
                    "target_week": 43,
                    "target_year": 2026,
                    "target_lesson": "14",
                    "target_class_code": "VT-C4K-GA37",
                }
            },
        }
        sanitized = _sanitize_dashboard_item(
            legacy_l14_item,
            week=43,
            year=2026,
            source_job_id=412,
            lms_status="✅ Đã nộp LMS",
            job_timestamp="2026-10-30 09:53:19 ICT",
        )
        self.assertEqual(sanitized["status"], "✅ Miễn báo cáo (Buổi 14)")
        self.assertEqual(
            sanitized["status_detail"],
            "Buổi 14 – miễn báo cáo theo quy tắc nghiệp vụ.",
        )
        self.assertEqual(sanitized["evaluation_status"], "✅ Đã hoàn thành nhận xét")
        self.assertTrue(sanitized["submitted_for_bcnb"])
        self.assertEqual(sanitized["evidence_state"], "LESSON_14_EXEMPTION")

        summary_row = {
            "class_code": "VT-C4K-GA37",
            "teacher": "Đỗ Thị Thùy Trang",
            "lesson": "14",
            "class_date": "23/10/2026",
            "zalo_status": "⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công",
            "lms_status": "✅ Đã nộp LMS",
            "evaluation_status": "⚠️ Đã hoàn tất nhận xét LMS, Zalo cần kiểm tra",
            "issue_description": "",
            "verified_at": "2026-10-30 09:53:19 ICT",
        }
        self.assertFalse(is_failed_weekly_summary_row(summary_row))
        self.assertFalse(
            is_failed_weekly_summary_row(
                {**summary_row, "evaluation_status": "❌ Chưa nhận xét Zalo"}
            )
        )
        self.assertFalse(
            is_failed_weekly_summary_row(
                {**summary_row, "evaluation_status": "⚠️ Chưa nhận xét Zalo"}
            )
        )
        csv_text = build_weekly_summary_csv([summary_row])
        self.assertIn("✅ Miễn báo cáo (Buổi 14)", csv_text)
        self.assertIn("✅ Đã hoàn thành nhận xét", csv_text)
        self.assertNotIn("Zalo cần kiểm tra", csv_text)


if __name__ == "__main__":
    unittest.main()
