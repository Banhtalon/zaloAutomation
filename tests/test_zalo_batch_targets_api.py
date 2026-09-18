import unittest

from fastapi import HTTPException

from api.job_routes import get_job_targets


class FakeJobRepository:
    def __init__(self, job=None):
        self.job = job

    async def get_job(self, _job_id):
        return self.job


class FakeBatchRepository:
    async def summary(self, job_id):
        return {
            "total": 2,
            "scan": {"VERIFIED": 1, "FAILED": 1},
            "write": {"DRY_RUN": 1, "BLOCKED_BATCH_HALT": 1},
            "job_id_seen": job_id,
        }

    async def list_for_job(self, job_id):
        return [
            {
                "id": 10,
                "job_id": job_id,
                "scan_status": "VERIFIED",
                "write_status": "DRY_RUN",
            },
            {
                "id": 11,
                "job_id": job_id,
                "scan_status": "FAILED",
                "write_status": "BLOCKED_BATCH_HALT",
            },
        ]


class FakeContainer:
    def __init__(self, job=None):
        self.job_repo = FakeJobRepository(job)
        self.zalo_batch_repo = FakeBatchRepository()


class ZaloBatchTargetsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_targets_endpoint_returns_summary_and_durable_rows(self):
        payload = await get_job_targets(
            42,
            FakeContainer({"id": 42, "job_type": "ZALO_SCAN"}),
        )

        self.assertEqual(payload["job_id"], 42)
        self.assertEqual(payload["summary"]["total"], 2)
        self.assertEqual(payload["summary"]["job_id_seen"], 42)
        self.assertEqual(
            [(item["id"], item["write_status"]) for item in payload["targets"]],
            [(10, "DRY_RUN"), (11, "BLOCKED_BATCH_HALT")],
        )

    async def test_targets_endpoint_returns_public_not_found_contract(self):
        with self.assertRaises(HTTPException) as caught:
            await get_job_targets(404, FakeContainer())

        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail["code"], "JOB_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
