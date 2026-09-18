from datetime import datetime
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.googleform_routes import (
    MappingItem,
    OcrRequest,
    RestoreGroupRequest,
    StudentItem,
    SubmitRequest,
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
    get_zalo_scan_plan,
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

    async def test_scan_requires_a_trusted_complete_two_week_snapshot(self):
        container = FakeContainer()
        container.group_repo.snapshot = None
        with self.assertRaises(HTTPException) as required:
            await run_scan(container)
        self.assertEqual(required.exception.detail["code"], "SCAN_PLAN_REQUIRED")

        container.group_repo.snapshot = {
            "snapshot_id": 1,
            "status": "REJECTED",
            "plan": {},
        }
        with self.assertRaises(HTTPException) as untrusted:
            await run_scan(container)
        self.assertEqual(untrusted.exception.detail["code"], "SCAN_PLAN_NOT_TRUSTED")

        container.group_repo.snapshot = {
            "snapshot_id": 1,
            "status": "OBSERVED_ONLY",
            "plan": {"bcnb_week_plan": {"selected_weeks": [{"week": 32, "year": 2026}]}},
        }
        with self.assertRaises(HTTPException) as incomplete:
            await run_scan(container)
        self.assertEqual(incomplete.exception.detail["code"], "SCAN_PLAN_INCOMPLETE")

    async def test_scan_rejects_snapshot_older_than_configured_limit(self):
        container = FakeContainer()
        container.settings.zalo_plan_max_age_minutes = 30
        container.group_repo.snapshot["observed_at"] = "2020-01-01T00:00:00+00:00"

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

    async def test_scan_plan_read_route_exposes_snapshot_periods(self):
        container = FakeContainer()
        plan = await get_zalo_scan_plan(container)
        self.assertTrue(plan["available"])
        self.assertEqual(plan["latest_week"], {"week": 33, "year": 2026})
        self.assertEqual(plan["required_week"], {"week": 32, "year": 2026})

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


if __name__ == "__main__":
    unittest.main()
