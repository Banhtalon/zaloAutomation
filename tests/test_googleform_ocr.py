import json
import os
import tempfile
import unittest

import services.googleform_service as service


class FakeJobRepository:
    def __init__(self):
        self.updates = []

    async def update_job(self, job_id, **values):
        self.updates.append((job_id, values))


class FakeOCRProcessor:
    result = []

    async def process_file_async(self, _path):
        return self.result


class GoogleFormOcrTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_ocr = service.OCRProcessor
        self.original_repo = service.job_repo
        service.OCRProcessor = FakeOCRProcessor
        service.job_repo = FakeJobRepository()

    async def asyncTearDown(self):
        service.OCRProcessor = self.original_ocr
        service.job_repo = self.original_repo
        self.temp_dir.cleanup()

    def _add_image(self, name="image.png"):
        path = os.path.join(self.temp_dir.name, name)
        with open(path, "wb") as file:
            file.write(b"fake image handled by fake OCR")
        return path

    async def test_success_persists_students_and_retains_input_for_retry(self):
        path = self._add_image()
        FakeOCRProcessor.result = [
            {"name": "  Nguyen   Van A ", "class_code": " vt-jsb14 "},
            {"name": "Nguyen Van A", "class_code": "VT-JSB14"},
        ]
        result = await service._run_googleform_ocr(10, [path])
        final = service.job_repo.updates[-1][1]
        payload = json.loads(final["results"])
        self.assertEqual(final["status"], "SUCCESS")
        self.assertEqual(
            payload["students"],
            [{"name": "Nguyen Van A", "class_code": "VT-JSB14"}],
        )
        self.assertEqual(result["students"], payload["students"])
        self.assertTrue(os.path.exists(path))

    async def test_empty_result_fails_closed_and_retains_input_for_review(self):
        path = self._add_image()
        FakeOCRProcessor.result = []
        result = await service._run_googleform_ocr(11, [path])
        final = service.job_repo.updates[-1][1]
        self.assertEqual(final["status"], "FAILED")
        self.assertIn("error", result)
        self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
