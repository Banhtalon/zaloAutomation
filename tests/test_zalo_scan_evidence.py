import os
import tempfile
import unittest
from unittest.mock import AsyncMock

from datetime import datetime

from services.zalo_scan_evidence import scan_open_zalo_chat


class FakeDateHeaders:
    def __init__(self, value):
        self.value = value
        self.last = self

    async def count(self):
        return 1

    async def inner_text(self):
        return self.value


class FakeMessage:
    def __init__(self, text, date_header):
        self.text = text
        self.date_header = date_header
        self.scroll_into_view_if_needed = AsyncMock()

    async def inner_text(self):
        return self.text

    def locator(self, _selector):
        return FakeDateHeaders(self.date_header)


class FakeListLocator:
    def __init__(self, values):
        self.values = values

    async def all(self):
        return self.values


class FakeMouse:
    def __init__(self):
        self.move = AsyncMock()
        self.wheel = AsyncMock()


class FakePage:
    def __init__(self, messages):
        self.messages = messages
        self.mouse = FakeMouse()
        self.screenshots = []

    def locator(self, selector):
        if selector == '[data-component="bubble-message"]':
            return FakeListLocator(self.messages)
        if selector == 'text="Xem thêm"':
            return FakeListLocator([])
        raise AssertionError(f"Unexpected selector: {selector}")

    async def screenshot(self, *, path):
        self.screenshots.append(path)
        with open(path, "wb") as evidence:
            evidence.write(f"proof-{len(self.screenshots)}".encode())


class ZaloScanEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_negative_result_requires_two_hashed_images_and_full_window(self):
        messages = [
            FakeMessage("Tin nhắn thông thường " * 5, "18/07/2026"),
            FakeMessage("Trao đổi lớp học thông thường " * 4, "Hôm nay"),
        ]
        page = FakePage(messages)
        ensure_not_cancelled = AsyncMock()

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=123,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-PTI09",
                actual_group_name="MindX - VT-C4K-PTI09",
                person="Teacher",
                class_date_str="",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=ensure_not_cancelled,
            )
            images = result["evidence"]["images"]
            self.assertEqual(result["evidence_state"], "NOT_FOUND_VERIFIED")
            self.assertTrue(os.path.isfile(os.path.join(root, os.path.basename(images["latest"]["path"]))))
            self.assertEqual(len(images["latest"]["sha256"]), 64)
            self.assertEqual(len(images["oldest"]["sha256"]), 64)

        self.assertEqual(len(page.screenshots), 2)
        ensure_not_cancelled.assert_awaited()


if __name__ == "__main__":
    unittest.main()
