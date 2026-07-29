import asyncio
import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db.database as database
from googleform_autosubmit.config import Config
from repositories.job_repository import JobRepository
from repositories.upload_repository import UploadClaimError, UploadRepository
from services.upload_store import UploadStore, UploadValidationError


PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class UploadStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "test.db")
        database.init_db()
        self.store = UploadStore(
            os.path.join(self.temp_dir.name, "uploads"), UploadRepository()
        )
        self.jobs = JobRepository()

    async def asyncTearDown(self):
        database.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    async def test_each_job_claims_only_its_uploads(self):
        first = await self.store.save(PNG_1X1, "googleform_ocr")
        second = await self.store.save(PNG_1X1, "googleform_ocr")
        job_id = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})

        await self.store.claim([first["upload_id"]], "googleform_ocr", job_id)
        paths = await self.store.paths_for_job(job_id, "googleform_ocr")
        self.assertEqual(len(paths), 1)
        self.assertTrue(os.path.isfile(paths[0]))

        untouched = await self.store.repository.get_many([second["upload_id"]])
        self.assertEqual(untouched[0]["state"], "STAGED")
        self.assertIsNone(untouched[0]["job_id"])

    async def test_upload_cannot_be_claimed_twice_or_for_wrong_purpose(self):
        upload = await self.store.save(PNG_1X1, "googleform_ocr")
        first_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        second_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        await self.store.claim([upload["upload_id"]], "googleform_ocr", first_job)
        with self.assertRaises(UploadClaimError):
            await self.store.claim([upload["upload_id"]], "googleform_ocr", second_job)

        lms_upload = await self.store.save(PNG_1X1, "lms_analysis")
        with self.assertRaises(UploadClaimError):
            await self.store.claim([lms_upload["upload_id"]], "googleform_ocr", second_job)

    async def test_path_traversal_and_invalid_ids_are_rejected(self):
        with self.assertRaises(UploadValidationError):
            self.store._resolve("../outside.png")
        for value in ("../x", "C:\\temp\\x", "not-a-uuid", "a" * 31):
            with self.subTest(value=value):
                with self.assertRaises(UploadValidationError):
                    self.store.validate_upload_id(value)

    async def test_pixel_limit_is_enforced_before_storage(self):
        original = Config.MAX_IMAGE_PIXELS
        Config.MAX_IMAGE_PIXELS = 0
        try:
            with self.assertRaises(UploadValidationError):
                await self.store.save(PNG_1X1, "googleform_ocr")
        finally:
            Config.MAX_IMAGE_PIXELS = original

    async def test_clone_rolls_back_database_and_files_when_batch_insert_fails(self):
        uploads = [
            await self.store.save(PNG_1X1, "googleform_ocr")
            for _ in range(2)
        ]
        source_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        target_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        await self.store.claim(
            [item["upload_id"] for item in uploads],
            "googleform_ocr",
            source_job,
        )
        async with database.get_db_connection() as db:
            await db.execute(
                f"""
                CREATE TRIGGER fail_clone_batch
                BEFORE INSERT ON job_uploads
                WHEN NEW.job_id = {target_job}
                  AND (SELECT COUNT(*) FROM job_uploads WHERE job_id = {target_job}) >= 1
                BEGIN
                    SELECT RAISE(ABORT, 'forced clone failure');
                END
                """
            )
            await db.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            await self.store.clone_job_uploads(source_job, target_job)

        self.assertEqual(await self.store.repository.list_for_job(target_job), [])
        target_dir = Path(self.store.root, "jobs", str(target_job), "inputs")
        self.assertEqual(list(target_dir.glob("*")) if target_dir.exists() else [], [])

    async def test_clone_missing_source_does_not_leave_partial_target(self):
        uploads = [
            await self.store.save(PNG_1X1, "googleform_ocr")
            for _ in range(2)
        ]
        source_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        target_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        await self.store.claim(
            [item["upload_id"] for item in uploads],
            "googleform_ocr",
            source_job,
        )
        source_paths = await self.store.paths_for_job(source_job, "googleform_ocr")
        Path(source_paths[-1]).unlink()

        with self.assertRaises(UploadClaimError):
            await self.store.clone_job_uploads(source_job, target_job)

        self.assertEqual(await self.store.repository.list_for_job(target_job), [])
        target_dir = Path(self.store.root, "jobs", str(target_job), "inputs")
        self.assertEqual(list(target_dir.glob("*")) if target_dir.exists() else [], [])

    async def test_clone_success_preserves_metadata_and_content_hash(self):
        uploads = [
            await self.store.save(PNG_1X1, "googleform_ocr")
            for _ in range(2)
        ]
        source_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        target_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        await self.store.claim(
            [item["upload_id"] for item in uploads],
            "googleform_ocr",
            source_job,
        )

        cloned_ids = await self.store.clone_job_uploads(source_job, target_job)

        self.assertEqual(len(cloned_ids), 2)
        self.assertTrue(set(cloned_ids).isdisjoint(item["upload_id"] for item in uploads))
        target_records = await self.store.repository.list_for_job(target_job)
        self.assertEqual(len(target_records), 2)
        for record, path in zip(
            target_records,
            await self.store.paths_for_job(target_job, "googleform_ocr"),
        ):
            self.assertEqual(Path(path).stat().st_size, record["size_bytes"])
            self.assertEqual(
                hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                record["sha256"],
            )

    async def test_two_store_instances_allow_exactly_one_claim(self):
        upload = await self.store.save(PNG_1X1, "googleform_ocr")
        first_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        second_job = await self.jobs.create_job("GOOGLEFORM_OCR", {"version": 2})
        second_store = UploadStore(self.store.root, UploadRepository())

        results = await asyncio.gather(
            self.store.claim([upload["upload_id"]], "googleform_ocr", first_job),
            second_store.claim([upload["upload_id"]], "googleform_ocr", second_job),
            return_exceptions=True,
        )

        self.assertEqual(sum(isinstance(result, list) for result in results), 1)
        self.assertEqual(sum(isinstance(result, UploadClaimError) for result in results), 1)

    async def test_reconcile_removes_orphans_but_preserves_live_uploads(self):
        upload = await self.store.save(PNG_1X1, "googleform_ocr")
        live_record = (await self.store.repository.get_many([upload["upload_id"]]))[0]
        live_path = self.store._resolve(live_record["stored_path"])
        orphan = Path(self.store.root, "jobs", "999", "inputs", "orphan.png")
        pending = Path(self.store.root, "jobs", "999", "inputs", "x.png.pending-test")
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"orphan")
        pending.write_bytes(b"pending")

        result = await self.store.reconcile_orphans()

        self.assertTrue(live_path.is_file())
        self.assertFalse(orphan.exists())
        self.assertFalse(pending.exists())
        self.assertEqual(result["deleted_orphans"], 2)
        self.assertEqual(result["missing_live"], 0)


if __name__ == "__main__":
    unittest.main()
