from contextlib import asynccontextmanager
import json
import unittest
from unittest.mock import AsyncMock, patch

from googleform_autosubmit.form_submitter import SubmissionOutcomeUnknownError
from services.external_gateways import ExternalGateways
import services.googleform_service as service


class FakeJobRepository:
    def __init__(self):
        self.updates = []

    async def update_job(self, job_id, **changes):
        self.updates.append({"job_id": job_id, **changes})

    async def ensure_not_cancelled(self, _job_id):
        return None

    async def is_cancel_requested(self, _job_id):
        return False


class FakeReportRepository:
    def __init__(self):
        self.submissions = []

    async def get_form_mapping_dict(self):
        return {"Question": {"type": "radio", "value": "Answer"}}

    async def add_form_submission(self, *args, **kwargs):
        self.submissions.append((args, kwargs))


class FakeAuditRepository:
    def __init__(self, *, existing=None):
        self.existing = existing
        self.completions = []

    async def record_intent(self, **kwargs):
        if self.existing:
            return self.existing, False
        return {"id": 7, "action_key": kwargs["action_key"], "outcome": "INTENDED"}, True

    async def complete_action(self, audit_id, **kwargs):
        self.completions.append((audit_id, kwargs))


class FakeFormBatch:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def submit_form(self, student_name, class_code):
        self.calls.append((student_name, class_code))
        if self.error:
            raise self.error


class FakeBrowserGateway:
    def __init__(self, batch):
        self.batch = batch
        self.enter_count = 0
        self.exit_count = 0

    @asynccontextmanager
    async def form_batch(self, *_args, **_kwargs):
        self.enter_count += 1
        try:
            yield self.batch
        finally:
            self.exit_count += 1


class GoogleFormSubmitServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_job_repo = service.job_repo
        self.original_report_repo = service.report_repo
        self.original_audit_repo = service.audit_repo
        self.job_repo = FakeJobRepository()
        self.report_repo = FakeReportRepository()
        self.audit_repo = FakeAuditRepository()
        service.job_repo = self.job_repo
        service.report_repo = self.report_repo
        service.audit_repo = self.audit_repo

    def tearDown(self):
        service.job_repo = self.original_job_repo
        service.report_repo = self.original_report_repo
        service.audit_repo = self.original_audit_repo

    def external(self, batch):
        browser = FakeBrowserGateway(batch)
        return (
            ExternalGateways(
                browser=browser,
                lms_ocr=object(),
                student_ocr_factory=lambda: object(),
            ),
            browser,
        )

    async def test_ambiguous_confirmation_is_audited_unknown(self):
        batch = FakeFormBatch(
            SubmissionOutcomeUnknownError("confirmation missing")
        )
        external, browser = self.external(batch)
        with patch.object(service.asyncio, "sleep", AsyncMock()):
            await service._background_submit_forms(
                1,
                [{"name": "Student", "class_code": "vt-code"}],
                "batch-1",
                "production",
                external,
            )

        self.assertEqual(browser.enter_count, 1)
        self.assertEqual(browser.exit_count, 1)
        self.assertEqual(self.audit_repo.completions[0][1]["outcome"], "UNKNOWN")
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
        outcomes = json.loads(self.job_repo.updates[-1]["results"])["outcomes"]
        self.assertEqual(outcomes[0]["outcome"], "UNKNOWN")
        self.assertEqual(self.report_repo.submissions[0][0][2], "Thất bại")

    async def test_verified_duplicate_is_not_submitted_again(self):
        self.audit_repo = FakeAuditRepository(
            existing={
                "id": 8,
                "action_key": "existing",
                "outcome": "VERIFIED",
            }
        )
        service.audit_repo = self.audit_repo
        batch = FakeFormBatch()
        external, _browser = self.external(batch)
        with patch.object(service.asyncio, "sleep", AsyncMock()):
            await service._background_submit_forms(
                2,
                [{"name": "Student", "class_code": "vt-code"}],
                "batch-2",
                "production",
                external,
            )

        self.assertEqual(batch.calls, [])
        self.assertEqual(self.job_repo.updates[-1]["status"], "SUCCESS")
        outcomes = json.loads(self.job_repo.updates[-1]["results"])["outcomes"]
        self.assertEqual(outcomes[0]["outcome"], "VERIFIED")
        self.assertEqual(self.audit_repo.completions, [])

    async def test_safe_failure_is_explicit_in_results_for_selective_retry(self):
        batch = FakeFormBatch(RuntimeError("radio was not checked"))
        external, _browser = self.external(batch)
        with patch.object(service.asyncio, "sleep", AsyncMock()):
            await service._background_submit_forms(
                3,
                [{"name": "Student", "class_code": "vt-code"}],
                "batch-3",
                "dry-run",
                external,
            )

        outcomes = json.loads(self.job_repo.updates[-1]["results"])["outcomes"]
        self.assertEqual(outcomes[0]["outcome"], "FAILED")
        self.assertFalse(outcomes[0]["success"])


if __name__ == "__main__":
    unittest.main()
