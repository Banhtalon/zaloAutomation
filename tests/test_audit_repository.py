import os
import tempfile
import unittest

import db.database as database
from repositories.audit_repository import AuditRepository
from repositories.job_repository import JobRepository


class AuditRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        database.init_db()
        self.jobs = JobRepository()
        self.audit = AuditRepository()

    async def asyncTearDown(self):
        database.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    async def test_intent_is_committed_before_result_and_action_key_is_idempotent(self):
        job_id = await self.jobs.create_job("WRITE", {"version": 2})
        intent, created = await self.audit.record_intent(
            action_key="action-1",
            job_id=job_id,
            action_type="BCNB_TICK",
            target_type="CLASS",
            target_id="VT-JSB14",
            mode="dry-run",
            before_state={"checked": False},
            intended_state={"checked": True},
            evidence_path="/static/proof.png",
            evidence_sha256="A" * 64,
        )
        self.assertTrue(created)
        self.assertEqual(intent["outcome"], "INTENT_RECORDED")
        self.assertEqual(intent["evidence_sha256"], "A" * 64)

        duplicate, duplicate_created = await self.audit.record_intent(
            action_key="action-1",
            job_id=job_id,
            action_type="BCNB_TICK",
            target_type="CLASS",
            target_id="VT-JSB14",
            mode="dry-run",
        )
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["id"], intent["id"])

        await self.audit.complete_action(
            intent["id"], outcome="DRY_RUN", after_state={"checked": False}
        )
        actions = await self.audit.list_for_job(job_id)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["outcome"], "DRY_RUN")
        self.assertEqual(actions[0]["before_state"], {"checked": False})
        self.assertEqual(actions[0]["evidence_path"], "/static/proof.png")
        self.assertEqual(actions[0]["evidence_sha256"], "A" * 64)


if __name__ == "__main__":
    unittest.main()
