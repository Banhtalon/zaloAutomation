import unittest

from fastapi import HTTPException

from api.googleform_routes import RestoreGroupRequest, restore_ignored_group
from api.job_routes import retry_terminal_job
from api.upload_routes import UploadRequest, create_upload
from api.zalo_routes import get_groups
from services.upload_store import UploadValidationError


class RaisingGroupRepository:
    async def get_all_groups(self):
        raise RuntimeError("database password=super-secret")

    async def restore_ignored_group(self, _name):
        raise RuntimeError("database password=super-secret")


class RaisingUploadStore:
    async def save(self, *_args):
        raise UploadValidationError("decoder path C:/secret/file.png")


class RaisingDispatcher:
    async def retry_job(self, _job_id):
        raise RuntimeError("sqlite path F:/secret/database.db")


class FakeContainer:
    def __init__(self):
        self.group_repo = RaisingGroupRepository()
        self.upload_store = RaisingUploadStore()
        self.dispatcher = RaisingDispatcher()


class ApiErrorTests(unittest.IsolatedAsyncioTestCase):
    def assert_sanitized(self, exc, *, status, code):
        self.assertEqual(exc.status_code, status)
        self.assertIsInstance(exc.detail, dict)
        self.assertEqual(exc.detail["code"], code)
        serialized = str(exc.detail).lower()
        self.assertNotIn("secret", serialized)
        self.assertNotIn("password", serialized)
        self.assertNotIn("database.db", serialized)

    async def test_group_read_and_restore_hide_internal_errors(self):
        container = FakeContainer()
        with self.assertRaises(HTTPException) as caught:
            await get_groups(container)
        self.assert_sanitized(caught.exception, status=500, code="INTERNAL_ERROR")

        with self.assertRaises(HTTPException) as caught:
            await restore_ignored_group(RestoreGroupRequest(group_name="Group"), container)
        self.assert_sanitized(caught.exception, status=500, code="INTERNAL_ERROR")

    async def test_upload_validation_has_stable_public_contract(self):
        with self.assertRaises(HTTPException) as caught:
            await create_upload(
                UploadRequest(image_data="bad", purpose="googleform_ocr"),
                FakeContainer(),
            )
        self.assert_sanitized(caught.exception, status=400, code="UPLOAD_INVALID")

    async def test_unexpected_retry_error_is_internal_not_conflict(self):
        with self.assertRaises(HTTPException) as caught:
            await retry_terminal_job(99, FakeContainer())
        self.assert_sanitized(caught.exception, status=500, code="INTERNAL_ERROR")


if __name__ == "__main__":
    unittest.main()
