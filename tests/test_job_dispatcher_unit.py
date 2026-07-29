import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from repositories.job_repository import (
    JobCancelledError,
    JobNotFoundError,
    JobStateError,
)
from services.job_dispatcher import JobDispatcher


class FakeUploadStore:
    def __init__(self):
        self.clone_result = []
        self.clone_error = None

    async def paths_for_job(self, _job_id, purpose):
        if purpose == "googleform_ocr":
            return ["one.png", "two.png"]
        return ["lms.png"]

    async def clone_job_uploads(self, _source_id, _new_id):
        if self.clone_error:
            raise self.clone_error
        return self.clone_result


class FakeRuntime:
    def __init__(self):
        self.reset_count = 0

    def reset_locks(self):
        self.reset_count += 1


class FakeAuditRepository:
    def __init__(self):
        self.records = []
        self.requested_jobs = []

    async def list_for_job(self, job_id, limit=100):
        self.requested_jobs.append((job_id, limit))
        return list(self.records)


class FakeJobRepository:
    def __init__(self):
        self.mark_running_result = True
        self.record = {
            "id": 7,
            "job_type": "ZALO_DISCOVERY",
            "request_payload": {},
        }
        self.current = {"status": "RUNNING"}
        self.updates = []
        self.created = []
        self.recovery_ids = []

    async def mark_running(self, _job_id):
        return self.mark_running_result

    async def get_job_record(self, _job_id):
        return self.record

    async def ensure_not_cancelled(self, _job_id):
        return None

    async def heartbeat(self, _job_id):
        return False

    async def get_job(self, _job_id):
        return self.current

    async def update_job(self, job_id, **values):
        self.updates.append((job_id, values))
        return True

    async def create_job(self, job_type, payload, **values):
        self.created.append((job_type, payload, values))
        return 88

    async def update_payload(self, job_id, payload):
        self.updated_payload = (job_id, payload)

    async def prepare_recovery(self):
        return list(self.recovery_ids)


def job(job_id, job_type, payload=None, **metadata):
    return {
        "id": job_id,
        "job_type": job_type,
        "request_payload": payload or {},
        "retryable": False,
        "recoverable": False,
        "mutates_external": False,
        **metadata,
    }


class JobDispatcherDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_every_supported_job_type(self):
        runtime = object()
        uploads = FakeUploadStore()
        dispatcher = JobDispatcher(
            job_repo=FakeJobRepository(),
            upload_store=uploads,
            worker_runtime=runtime,
        )

        with (
            patch(
                "services.zalo_discovery_service.run_auto_discovery",
                new_callable=AsyncMock,
            ) as discovery,
            patch(
                "services.zalo_service.run_zalo_automation",
                new_callable=AsyncMock,
            ) as scan,
            patch(
                "services.googleform_service.run_googleform_ocr",
                new_callable=AsyncMock,
            ) as ocr,
            patch(
                "services.googleform_service.background_submit_forms",
                new_callable=AsyncMock,
            ) as submit,
            patch(
                "services.lms_service.run_lms_analysis",
                new_callable=AsyncMock,
            ) as analysis,
            patch(
                "services.lms_service.run_lms_automation",
                new_callable=AsyncMock,
            ) as automation,
        ):
            await dispatcher._dispatch(job(1, "ZALO_DISCOVERY"))
            await dispatcher._dispatch(
                job(2, "ZALO_SCAN", {"execution_mode": "dry-run"})
            )
            await dispatcher._dispatch(job(3, "GOOGLEFORM_OCR"))
            await dispatcher._dispatch(
                job(
                    4,
                    "GOOGLEFORM_SUBMIT",
                    {
                        "students": [{"name": "A"}],
                        "idempotency_key": "batch-key",
                        "execution_mode": "dry-run",
                    },
                )
            )
            await dispatcher._dispatch(
                job(5, "LMS_ANALYSIS", {"execution_mode": "dry-run"})
            )
            await dispatcher._dispatch(
                job(
                    6,
                    "LMS_AUTOMATION",
                    {
                        "plan": {"rows": []},
                        "expected_digest": "digest",
                        "execution_mode": "dry-run",
                    },
                )
            )

        discovery.assert_awaited_once_with(1, runtime=runtime)
        scan.assert_awaited_once_with(2, "dry-run", runtime=runtime)
        ocr.assert_awaited_once_with(
            3,
            ["one.png", "two.png"],
            runtime=runtime,
        )
        submit.assert_awaited_once_with(
            4,
            [{"name": "A"}],
            "batch-key",
            "dry-run",
            runtime=runtime,
        )
        analysis.assert_awaited_once_with(
            5,
            "lms.png",
            "dry-run",
            runtime=runtime,
        )
        automation.assert_awaited_once_with(
            6,
            {"rows": []},
            "digest",
            "dry-run",
            runtime=runtime,
        )

    async def test_dispatch_rejects_missing_lms_image_and_unknown_job(self):
        uploads = FakeUploadStore()
        dispatcher = JobDispatcher(
            job_repo=FakeJobRepository(),
            upload_store=uploads,
            worker_runtime=object(),
        )
        uploads.paths_for_job = AsyncMock(return_value=[])
        with self.assertRaises(ValueError):
            await dispatcher._dispatch(job(1, "LMS_ANALYSIS"))
        with self.assertRaises(ValueError):
            await dispatcher._dispatch(job(2, "UNKNOWN"))


class JobDispatcherLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_dispatcher(self):
        self.repo = FakeJobRepository()
        self.uploads = FakeUploadStore()
        self.runtime = FakeRuntime()
        self.audits = FakeAuditRepository()
        return JobDispatcher(
            job_repo=self.repo,
            upload_store=self.uploads,
            worker_runtime=self.runtime,
            audit_repo=self.audits,
            heartbeat_seconds=2,
        )

    async def test_run_job_stops_when_claim_or_record_is_missing(self):
        dispatcher = self.make_dispatcher()
        self.repo.mark_running_result = False
        dispatcher._dispatch = AsyncMock()
        await dispatcher._run_job_in_context(7)
        dispatcher._dispatch.assert_not_awaited()

        self.repo.mark_running_result = True
        self.repo.record = None
        await dispatcher._run_job_in_context(7)
        dispatcher._dispatch.assert_not_awaited()

    async def test_run_job_marks_missing_terminal_state_as_failed(self):
        dispatcher = self.make_dispatcher()
        dispatcher._dispatch = AsyncMock()
        await dispatcher._run_job_in_context(7)
        dispatcher._dispatch.assert_awaited_once()
        self.assertEqual(self.repo.updates[-1][1]["status"], "FAILED")

    async def test_run_job_handles_cancel_and_hides_unexpected_details(self):
        dispatcher = self.make_dispatcher()
        dispatcher._dispatch = AsyncMock(
            side_effect=JobCancelledError("sensitive cancellation detail")
        )
        await dispatcher._run_job_in_context(7)
        cancelled = self.repo.updates[-1][1]
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertNotIn("sensitive", cancelled["message"])

        self.repo.updates.clear()
        dispatcher._dispatch = AsyncMock(
            side_effect=RuntimeError("password=super-secret")
        )
        await dispatcher._run_job_in_context(7)
        failed = self.repo.updates[-1][1]
        self.assertEqual(failed["status"], "FAILED")
        self.assertNotIn("secret", failed["message"])
        self.assertNotIn("password", failed["message"])

    async def test_googleform_retry_only_selects_unambiguously_failed_students(self):
        dispatcher = self.make_dispatcher()
        self.repo.record = {
            "id": 103,
            "job_type": "GOOGLEFORM_SUBMIT",
            "status": "PARTIAL_SUCCESS",
            "retryable": True,
            "recoverable": True,
            "mutates_external": False,
            "attempt": 1,
            "results": json.dumps(
                {
                    "kind": "googleform_submit",
                    "outcomes": [
                        {
                            "name": "Already Done",
                            "class_code": "VT-C4K-PTI10",
                            "success": True,
                            "outcome": "DRY_RUN",
                        },
                        {
                            "name": "Safe Failure",
                            "class_code": "VT-C4K-PTI10",
                            "success": False,
                        },
                        {
                            "name": "Unknown Result",
                            "class_code": "VT-C4K-PTI10",
                            "success": False,
                            "outcome": "UNKNOWN",
                        },
                        {
                            "name": "Cancelled",
                            "class_code": "VT-C4K-PTI10",
                            "success": False,
                            "outcome": "CANCELLED_BEFORE_WRITE",
                        },
                    ],
                }
            ),
            "request_payload": {
                "version": 3,
                "students": [
                    {"name": "Already Done", "class_code": "VT-C4K-PTI10"},
                    {"name": "Safe Failure", "class_code": "VT-C4K-PTI10"},
                    {"name": "Unknown Result", "class_code": "VT-C4K-PTI10"},
                    {"name": "Cancelled", "class_code": "VT-C4K-PTI10"},
                ],
                "idempotency_key": "original-batch-key-123456",
                "execution_mode": "dry-run",
            },
        }
        self.audits.records = [
            {
                "action_type": "GOOGLE_FORM_SUBMIT",
                "target_id": "Already Done | VT-C4K-PTI10",
                "outcome": "DRY_RUN",
            },
            {
                "action_type": "GOOGLE_FORM_SUBMIT",
                "target_id": "Safe Failure | VT-C4K-PTI10",
                "outcome": "FAILED",
            },
            {
                "action_type": "GOOGLE_FORM_SUBMIT",
                "target_id": "Unknown Result | VT-C4K-PTI10",
                "outcome": "UNKNOWN",
            },
            {
                "action_type": "GOOGLE_FORM_SUBMIT",
                "target_id": "Cancelled | VT-C4K-PTI10",
                "outcome": "CANCELLED_BEFORE_WRITE",
            },
        ]
        scheduled = []

        with patch(
            "services.job_dispatcher.secrets.token_urlsafe",
            return_value="fresh-retry-key-1234567890",
        ):
            new_id = await dispatcher.retry_job(103, scheduled.append)

        self.assertEqual(new_id, 88)
        self.assertEqual(scheduled, [88])
        self.assertEqual(self.audits.requested_jobs, [(103, 200)])
        retry_payload = self.repo.updated_payload[1]
        self.assertEqual(
            retry_payload["students"],
            [{"name": "Safe Failure", "class_code": "VT-C4K-PTI10"}],
        )
        self.assertEqual(
            retry_payload["idempotency_key"],
            "fresh-retry-key-1234567890",
        )
        self.assertNotEqual(
            retry_payload["idempotency_key"],
            self.repo.record["request_payload"]["idempotency_key"],
        )
        self.assertEqual(retry_payload["retry_source_job_id"], 103)
        self.assertEqual(retry_payload["retry_policy"], "failed-only-v1")

    async def test_googleform_retry_fails_closed_for_unsafe_or_ambiguous_outcomes(self):
        dispatcher = self.make_dispatcher()
        self.repo.record = {
            "id": 103,
            "job_type": "GOOGLEFORM_SUBMIT",
            "status": "PARTIAL_SUCCESS",
            "retryable": True,
            "recoverable": True,
            "mutates_external": False,
            "attempt": 1,
            "results": json.dumps(
                {
                    "kind": "googleform_submit",
                    "outcomes": [
                        {
                            "name": "Unknown Result",
                            "class_code": "VT-C4K-PTI10",
                            "success": False,
                            "outcome": "UNKNOWN",
                        },
                        {
                            "name": "Conflicting Audit",
                            "class_code": "VT-C4K-PTI10",
                            "success": False,
                            "outcome": "FAILED",
                        },
                    ],
                }
            ),
            "request_payload": {
                "students": [
                    {"name": "Unknown Result", "class_code": "VT-C4K-PTI10"},
                    {"name": "Conflicting Audit", "class_code": "VT-C4K-PTI10"},
                ],
                "idempotency_key": "original-batch-key-123456",
                "execution_mode": "dry-run",
            },
        }
        self.audits.records = [
            {
                "action_type": "GOOGLE_FORM_SUBMIT",
                "target_id": "Unknown Result | VT-C4K-PTI10",
                "outcome": "UNKNOWN",
            },
            {
                "action_type": "GOOGLE_FORM_SUBMIT",
                "target_id": "Conflicting Audit | VT-C4K-PTI10",
                "outcome": "FAILED",
            },
            {
                "action_type": "GOOGLE_FORM_SUBMIT",
                "target_id": "Conflicting Audit | VT-C4K-PTI10",
                "outcome": "UNKNOWN",
            },
        ]

        with self.assertRaisesRegex(JobStateError, "FAILED"):
            await dispatcher.retry_job(103)

        self.assertEqual(self.repo.created, [])
        self.assertFalse(hasattr(self.repo, "updated_payload"))

    async def test_googleform_retry_blocks_production_even_when_failed(self):
        dispatcher = self.make_dispatcher()
        self.repo.record = {
            "id": 103,
            "job_type": "GOOGLEFORM_SUBMIT",
            "status": "FAILED",
            "retryable": True,
            "recoverable": False,
            "mutates_external": True,
            "attempt": 1,
            "results": json.dumps(
                {
                    "kind": "googleform_submit",
                    "outcomes": [
                        {
                            "name": "Student",
                            "class_code": "VT-C4K-PTI10",
                            "success": False,
                            "outcome": "FAILED",
                        }
                    ],
                }
            ),
            "request_payload": {
                "students": [{"name": "Student", "class_code": "VT-C4K-PTI10"}],
                "idempotency_key": "original-batch-key-123456",
                "execution_mode": "production",
            },
        }
        self.audits.records = [
            {
                "action_type": "GOOGLE_FORM_SUBMIT",
                "target_id": "Student | VT-C4K-PTI10",
                "outcome": "FAILED",
            }
        ]

        with self.assertRaises(JobStateError):
            await dispatcher.retry_job(103)

        self.assertEqual(self.repo.created, [])

    async def test_googleform_all_failed_dry_run_can_retry_safe_failures(self):
        dispatcher = self.make_dispatcher()
        student = {"name": "Safe Failure", "class_code": "VT-C4K-PTI10"}
        self.repo.record = {
            "id": 104,
            "job_type": "GOOGLEFORM_SUBMIT",
            "status": "FAILED",
            "retryable": True,
            "recoverable": True,
            "mutates_external": False,
            "attempt": 1,
            "results": json.dumps(
                {
                    "kind": "googleform_submit",
                    "outcomes": [{**student, "success": False, "outcome": "FAILED"}],
                }
            ),
            "request_payload": {
                "students": [student],
                "idempotency_key": "original-batch-key-123456",
                "execution_mode": "dry-run",
            },
        }
        self.audits.records = [
            {
                "action_type": "GOOGLE_FORM_SUBMIT",
                "target_id": "Safe Failure | VT-C4K-PTI10",
                "outcome": "FAILED",
            }
        ]

        new_id = await dispatcher.retry_job(104, Mock())

        self.assertEqual(new_id, 88)
        self.assertEqual(self.repo.updated_payload[1]["students"], [student])

    async def test_run_job_propagates_task_cancellation(self):
        dispatcher = self.make_dispatcher()
        dispatcher._dispatch = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await dispatcher._run_job_in_context(7)
        self.assertEqual(self.repo.updates[-1][1]["status"], "CANCELLED")

        self.repo.updates.clear()
        dispatcher._shutting_down = True
        with self.assertRaises(asyncio.CancelledError):
            await dispatcher._run_job_in_context(7)
        self.assertEqual(self.repo.updates, [])

    async def test_retry_success_validation_and_safe_failure(self):
        dispatcher = self.make_dispatcher()
        self.repo.record = None
        with self.assertRaises(JobNotFoundError):
            await dispatcher.retry_job(1)

        self.repo.record = {
            "id": 1,
            "job_type": "GOOGLEFORM_OCR",
            "status": "RUNNING",
            "retryable": True,
            "recoverable": True,
            "mutates_external": False,
            "attempt": 1,
            "request_payload": {"upload_ids": ["old"]},
        }
        with self.assertRaises(JobStateError):
            await dispatcher.retry_job(1)

        self.repo.record["status"] = "FAILED"
        self.repo.record["retryable"] = False
        with self.assertRaises(JobStateError):
            await dispatcher.retry_job(1)

        self.repo.record["retryable"] = True
        self.uploads.clone_result = ["new-upload"]
        scheduled = []
        new_id = await dispatcher.retry_job(1, scheduled.append)
        self.assertEqual(new_id, 88)
        self.assertEqual(scheduled, [88])
        self.assertEqual(
            self.repo.updated_payload,
            (88, {"upload_ids": ["new-upload"]}),
        )

        self.uploads.clone_error = RuntimeError("path=C:/super-secret")
        with self.assertRaises(RuntimeError):
            await dispatcher.retry_job(1, scheduled.append)
        failed = self.repo.updates[-1][1]
        self.assertEqual(failed["status"], "FAILED")
        self.assertNotIn("secret", failed["message"])

    async def test_recovery_status_scheduling_and_shutdown(self):
        dispatcher = self.make_dispatcher()
        self.repo.recovery_ids = [10, 11]
        dispatcher.schedule_job = Mock()
        recovered = await dispatcher.recover_jobs()
        self.assertEqual(recovered, [10, 11])
        self.assertEqual(dispatcher.schedule_job.call_count, 2)
        self.assertEqual(self.runtime.reset_count, 1)

        dispatcher = self.make_dispatcher()
        first = asyncio.create_task(asyncio.sleep(30))
        done = asyncio.create_task(asyncio.sleep(0))
        await done
        dispatcher._tasks = {1: first, 2: done}
        self.assertEqual(dispatcher.status()["active_jobs"], 1)
        await dispatcher.shutdown_jobs()
        self.assertTrue(dispatcher.status()["shutting_down"])
        self.assertTrue(first.cancelled())


if __name__ == "__main__":
    unittest.main()
