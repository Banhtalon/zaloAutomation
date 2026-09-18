from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import db.database as database
from repositories.evidence_repository import (
    AVAILABLE,
    EXPIRED,
    MISSING,
    QUARANTINED,
    EvidenceRepository,
)
from repositories.audit_repository import AuditRepository
from repositories.job_repository import JobRepository


class EvidenceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "evidence.db")
        database.init_db()
        self.job_id = await JobRepository().create_job("TEST", {})
        self.root = Path(self.temp_dir.name) / "screenshots"
        (self.root / "job-1").mkdir(parents=True)
        self.repo = EvidenceRepository()

    async def asyncTearDown(self):
        database.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    async def test_register_verify_and_hash_mismatch_quarantines(self):
        path = self.root / "job-1" / "latest.png"
        path.write_bytes(b"proof")
        digest = hashlib.sha256(b"proof").hexdigest().upper()
        artifact = await self.repo.register_artifact(
            job_id=self.job_id,
            public_path="/api/evidence/1/latest.png",
            stored_path=str(path),
            capture_type="zalo_latest",
            expected_sha256=digest,
        )
        self.assertEqual(artifact["status"], AVAILABLE)
        self.assertEqual(artifact["size_bytes"], 5)
        self.assertEqual(artifact["mime_type"], "image/png")
        verified = await self.repo.verify(
            "/api/evidence/1/latest.png", expected_sha256=digest, job_id=1
        )
        self.assertEqual(verified["status"], AVAILABLE)
        path.write_bytes(b"tampered")
        mismatch = await self.repo.verify(
            "/api/evidence/1/latest.png", expected_sha256=digest, job_id=1
        )
        self.assertEqual(mismatch["status"], QUARANTINED)

    async def test_missing_and_expiry_fail_closed_without_fake_hash(self):
        missing = await self.repo.register_artifact(
            job_id=self.job_id,
            public_path="/api/evidence/1/missing.png",
            stored_path=str(self.root / "job-1" / "missing.png"),
            capture_type="zalo_oldest",
        )
        self.assertEqual(missing["status"], MISSING)
        self.assertIsNone(missing["sha256"])
        old = self.root / "job-1" / "old.png"
        old.write_bytes(b"old")
        created = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        expired = await self.repo.register_artifact(
            job_id=self.job_id,
            public_path="/api/evidence/1/old.png",
            stored_path=str(old),
            capture_type="legacy_backfill",
            created_at=created,
            retention_days=14,
        )
        checked = await self.repo.verify("/api/evidence/1/old.png", job_id=1)
        self.assertEqual(expired["status"], AVAILABLE)
        self.assertEqual(checked["status"], EXPIRED)
        missing_checked = await self.repo.verify(
            "/api/evidence/1/missing.png", job_id=self.job_id
        )
        self.assertEqual(missing_checked["status"], MISSING)

    async def test_verify_rejects_wrong_job_and_link_audit_quarantines_digest(self):
        path = self.root / "job-1" / "linked.png"
        path.write_bytes(b"linked")
        digest = hashlib.sha256(b"linked").hexdigest().upper()
        await self.repo.register_artifact(
            job_id=self.job_id,
            public_path="/api/evidence/1/linked.png",
            stored_path=str(path),
            capture_type="zalo_report",
            expected_sha256=digest,
        )
        wrong_job = await self.repo.verify(
            "/api/evidence/1/linked.png",
            expected_sha256=digest,
            job_id=self.job_id + 1,
        )
        self.assertEqual(wrong_job["status"], MISSING)

        audit = AuditRepository()
        intent, _ = await audit.record_intent(
            action_key="evidence-link",
            job_id=self.job_id,
            action_type="BCNB_TICK",
            target_type="CLASS",
            target_id="VT-JSB14",
            mode="dry-run",
        )
        linked = await self.repo.link_audit(
            audit_id=intent["id"],
            public_path="/api/evidence/1/linked.png",
            evidence_sha256="B" * 64,
        )
        self.assertEqual(linked["status"], QUARANTINED)
        self.assertIsNone(await self.repo.link_audit(
            audit_id=intent["id"], public_path="/api/evidence/1/unknown.png", evidence_sha256=None
        ))
        self.assertIsNone(await self.repo.link_audit(
            audit_id=intent["id"], public_path=None, evidence_sha256=None
        ))
        await self.repo.record_access(
            artifact_id=linked["id"],
            job_id=self.job_id,
            username="tester",
            outcome="DENIED",
            request_id="req-1",
            error_message="test",
        )

    async def test_retention_tombstone_keeps_bytes(self):
        path = self.root / "job-1" / "old.png"
        path.write_bytes(b"old")
        artifact = await self.repo.register_artifact(
            job_id=1,
            public_path="/api/evidence/1/old.png",
            stored_path=str(path),
            capture_type="zalo_oldest",
            retention_days=1,
            created_at=(datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),
        )
        self.assertEqual(artifact["status"], AVAILABLE)
        count = await self.repo.expire_old(root=str(self.root), retention_days=1)
        self.assertGreaterEqual(count, 1)
        current = await self.repo.get_by_public_path("/api/evidence/1/old.png")
        self.assertEqual(current["status"], EXPIRED)
        self.assertTrue(path.exists())

    async def test_delete_expired_file_is_restricted_to_png_under_job_directory(self):
        root = self.root.resolve()
        outside = Path(self.temp_dir.name) / "outside.png"
        outside.write_bytes(b"outside")
        self.assertFalse(self.repo._delete_expired_file(root, outside))
        bad_suffix = root / "job-1" / "not-image.txt"
        bad_suffix.write_text("x", encoding="utf-8")
        self.assertFalse(self.repo._delete_expired_file(root, bad_suffix))
        bad_folder = root / "misc" / "image.png"
        bad_folder.parent.mkdir(exist_ok=True)
        bad_folder.write_bytes(b"x")
        self.assertFalse(self.repo._delete_expired_file(root, bad_folder))

    async def test_retention_deletes_bytes_at_inclusive_seven_day_boundary(self):
        path = self.root / "job-1" / "boundary.png"
        path.write_bytes(b"boundary")
        # Keep the job directory non-empty so retention exercises the safe
        # rmdir failure path after deleting the expired PNG.
        (self.root / "job-1" / "keep.txt").write_text("keep", encoding="utf-8")
        created = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        artifact = await self.repo.register_artifact(
            job_id=1,
            public_path="/api/evidence/1/boundary.png",
            stored_path=str(path),
            capture_type="zalo_report",
            created_at=created,
            retention_days=7,
        )
        self.assertEqual(artifact["status"], AVAILABLE)

        count = await self.repo.expire_old(
            root=str(self.root),
            retention_days=7,
            delete_files=True,
        )

        self.assertGreaterEqual(count, 1)
        self.assertFalse(path.exists())
        current = await self.repo.get_by_public_path("/api/evidence/1/boundary.png")
        self.assertEqual(current["status"], EXPIRED)

    async def test_scan_result_is_downgraded_when_capture_is_missing(self):
        result = {
            "status": "✅ Đã nộp – Có minh chứng",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "evidence": {
                "images": {
                    "report": {
                        "path": "/api/evidence/1/not-captured.png",
                        "sha256": "A" * 64,
                    }
                },
                "validation_errors": [],
            },
        }
        await self.repo.register_scan_results(
            job_id=self.job_id,
            results=[result],
            evidence_root=str(self.root),
        )
        self.assertFalse(result["submitted_for_bcnb"])
        self.assertEqual(result["evidence_state"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("MISSING", result["evidence"]["evidence_statuses"])


if __name__ == "__main__":
    unittest.main()
