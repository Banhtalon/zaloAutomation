import asyncio
import tempfile
import unittest
from pathlib import Path

from db.database import database_context, init_db
from repositories.job_repository import JobRepository
from repositories.zalo_batch_repository import ZaloBatchRepository


def target(index: int) -> dict:
    return {
        "group_name": f"MindX - VT-JSB{index:02d}",
        "person": "Teacher",
        "class_code": f"VT-JSB{index:02d}",
        "lesson": "12",
        "class_date": "24/08/2026",
        "class_date_iso": "2026-08-24",
        "week": 35,
        "year": 2026,
    }


class ZaloBatchRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "batch.sqlite3")
        init_db(self.path)
        self.context = database_context(self.path)
        self.context.__enter__()
        self.jobs = JobRepository()
        self.targets = ZaloBatchRepository()

    async def asyncTearDown(self):
        self.context.__exit__(None, None, None)
        self.directory.cleanup()

    async def _job(self) -> int:
        return await self.jobs.create_job("ZALO_SCAN", {"execution_mode": "dry-run"})

    async def test_duplicate_target_rolls_back_batch(self):
        job_id = await self._job()
        with self.assertRaises(ValueError):
            await self.targets.seed_targets(job_id, [target(1), target(1)])
        self.assertEqual(await self.targets.list_for_job(job_id), [])

    async def test_two_workers_claim_each_target_exactly_once(self):
        job_id = await self._job()
        await self.targets.seed_targets(job_id, [target(i) for i in range(50)])

        async def consume(owner: str) -> list[int]:
            claimed = []
            while True:
                item = await self.targets.claim_scan(job_id, owner)
                if item is None:
                    return claimed
                claimed.append(item["id"])
                await self.targets.complete_scan(
                    item["id"], status="VERIFIED", result={"owner": owner}
                )

        left, right = await asyncio.gather(consume("worker-1"), consume("worker-2"))
        claimed = left + right
        self.assertEqual(len(claimed), 50)
        self.assertEqual(len(set(claimed)), 50)
        summary = await self.targets.summary(job_id)
        self.assertEqual(summary["scan"], {"VERIFIED": 50})

    async def test_stress_twenty_batches_fifty_targets(self):
        for round_index in range(20):
            job_id = await self._job()
            await self.targets.seed_targets(job_id, [target(i) for i in range(50)])

            async def consume(owner: str) -> list[int]:
                claimed = []
                while item := await self.targets.claim_scan(job_id, owner):
                    claimed.append(item["id"])
                    await self.targets.complete_scan(
                        item["id"],
                        status="VERIFIED" if item["id"] % 2 else "SKIPPED",
                        result={"round": round_index, "owner": owner},
                    )
                return claimed

            claimed = sum(
                await asyncio.gather(consume("worker-a"), consume("worker-b")),
                [],
            )
            self.assertEqual(len(claimed), 50)
            self.assertEqual(len(set(claimed)), 50)
            summary = await self.targets.summary(job_id)
            self.assertEqual(sum(summary["scan"].values()), 50)

    async def test_interrupted_write_is_unknown_not_retried(self):
        job_id = await self._job()
        await self.targets.seed_targets(job_id, [target(1)])
        scan = await self.targets.claim_scan(job_id, "scan-worker")
        await self.targets.complete_scan(
            scan["id"], status="VERIFIED", result={}, write_ready=True
        )
        write = await self.targets.claim_write(job_id, "write-worker")
        self.assertIsNotNone(write)
        recovered = await self.targets.recover_interrupted(job_id)
        self.assertEqual(recovered["write_unknown"], 1)
        self.assertIsNone(await self.targets.claim_write(job_id, "other-worker"))
        item = (await self.targets.list_for_job(job_id))[0]
        self.assertEqual(item["write_status"], "OUTCOME_UNKNOWN")
        await self.jobs.update_job(job_id, status="FAILED", progress=100)
        self.assertEqual(await self.jobs.prepare_recovery(), [job_id])
        self.assertEqual((await self.jobs.get_job(job_id))["status"], "PENDING")

    async def test_production_recovery_requeues_only_durable_target_batch(self):
        job_id = await self.jobs.create_job(
            "ZALO_SCAN",
            {"execution_mode": "production", "scan_plan": {"version": 1}},
            recoverable=False,
            mutates_external=True,
        )
        await self.targets.seed_targets(job_id, [target(2)])
        self.assertTrue(await self.jobs.claim_lease(job_id, "worker-a", 30))
        scan = await self.targets.claim_scan(job_id, "scan-worker")
        await self.targets.complete_scan(
            scan["id"], status="VERIFIED", result={}, write_ready=True
        )
        self.assertIsNotNone(
            await self.targets.claim_write(job_id, "write-worker")
        )

        self.assertEqual(await self.jobs.prepare_recovery(), [job_id])
        self.assertEqual((await self.jobs.get_job(job_id))["status"], "PENDING")
        recovered = await self.targets.recover_interrupted(job_id)
        self.assertEqual(recovered["write_unknown"], 1)
        item = (await self.targets.list_for_job(job_id))[0]
        self.assertEqual(item["write_status"], "OUTCOME_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
