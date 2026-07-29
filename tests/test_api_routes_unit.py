import unittest

from fastapi import HTTPException

from api.googleform_routes import (
    MappingItem,
    OcrRequest,
    RestoreGroupRequest,
    StudentItem,
    SubmitRequest,
    api_run_googleform_ocr,
    confirm_ignored_group_ended,
    delete_mapping,
    get_history,
    get_ignored_groups,
    get_mappings,
    restore_ignored_group,
    run_submit,
    save_mapping,
)
from api.job_routes import (
    cancel_job,
    get_job_audit,
    get_job_events,
    get_job_status,
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
from api.zalo_routes import auto_discover, get_class_lifecycles, get_groups, run_scan
from repositories.job_repository import JobNotFoundError, JobStateError
from services.lms_plan import compute_plan_digest


class FakeSettings:
    bcnb_dry_run = True
    google_form_mode = "dry-run"
    lms_confirmation_ttl_minutes = 15


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

    async def create_job(self, job_type, payload, **metadata):
        self.created.append((job_type, payload, metadata))
        return 41

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


class FakeGroupRepo:
    def __init__(self):
        self.groups = [{"name": "VT-JSB"}]
        self.ignored = ["Old group"]
        self.calls = []

    async def get_all_groups(self):
        return self.groups

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

    async def get_form_mappings(self):
        return self.mappings

    async def add_form_mapping(self, question, field_type, value):
        self.calls.append(("add", question, field_type, value))

    async def delete_form_mapping(self, mapping_id):
        self.calls.append(("delete", mapping_id))

    async def get_form_submissions(self):
        return [{"student": "A"}]


class FakeUploadStore:
    async def claim(self, upload_ids, purpose, job_id):
        return [f"{purpose}:{item}:{job_id}" for item in upload_ids]


class FakeAuditRepo:
    async def list_for_job(self, _job_id, limit):
        return [{"id": 2, "limit": limit}]


class FakeLmsRepo:
    def __init__(self, confirmation):
        self.confirmation = confirmation

    async def consume_confirmation(self, *_args, **_kwargs):
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


if __name__ == "__main__":
    unittest.main()
