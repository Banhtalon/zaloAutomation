import os
import tempfile
import unittest
from unittest.mock import AsyncMock

from datetime import datetime

from services.zalo_evidence import (
    INSUFFICIENT_EVIDENCE,
    SUBMITTED_LATE_VERIFIED,
    SUBMITTED_VERIFIED,
)
from services.zalo_scan_evidence import (
    _capture_zalo_evidence,
    _message_date_from_zalo_ui,
    _required_window_start,
    _zalo_sync_is_incomplete,
    build_empty_evidence_result,
    scan_open_zalo_chat,
)


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


class FakeVisibleBanner:
    first = None

    def __init__(self):
        self.first = self

    async def count(self):
        return 1

    async def is_visible(self):
        return True


class FakeSyncIncompletePage(FakePage):
    def get_by_text(self, _text, *, exact):
        self.assert_exact = exact
        return FakeVisibleBanner()


class FakeNoDateMessage(FakeMessage):
    def locator(self, _selector):
        class EmptyDateHeaders:
            async def count(self):
                return 0

        return EmptyDateHeaders()


class FakeFailingScreenshotPage(FakePage):
    async def screenshot(self, *, path):
        raise RuntimeError("screenshot unavailable")


class FakeBrokenDateMessage(FakeMessage):
    def locator(self, _selector):
        raise RuntimeError("date selector changed")


class FakeHiddenBanner:
    first = None

    def __init__(self):
        self.first = self

    async def count(self):
        return 1

    async def is_visible(self):
        return False


class FakeHiddenBannerPage:
    def get_by_text(self, _text, *, exact):
        self.exact = exact
        return FakeHiddenBanner()


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

    async def test_gb40_uses_zalo_ui_date_not_stale_date_in_report_content(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi quý phụ huynh. Tổng kết sau buổi học số 6 - 26/07/2025. "
                "Mã lớp: VT-C4K-GB40. NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên.",
                "Hôm nay",
            ),
        ]
        page = FakePage(messages)

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=124,
                screenshots_dir=root,
                group_name="MindX - Lập trình Game VT-C4K-GB40 CN",
                actual_group_name="MindX - Lập trình Game VT-C4K-GB40 CN",
                person="Teacher",
                class_date_str="26/07/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )

        candidate = result["evidence"]["classifier_result"]["candidates"][0]
        self.assertEqual(result["evidence_state"], SUBMITTED_VERIFIED)
        self.assertTrue(result["submitted_for_bcnb"])
        self.assertEqual(candidate["ui_sent_at"], "2026-07-29")
        self.assertIn("2025-07-26", candidate["content_dates"])
        self.assertEqual(candidate["decision"], "SUBMITTED_LATE_ACCEPTED")
        self.assertIn("CONTENT_DATE_MISMATCH", result["evidence"]["warnings"][0])

    async def test_report_candidate_without_ui_date_fails_closed(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Thứ tư",
            ),
        ]
        page = FakePage(messages)

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=125,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="26/07/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )

        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertIn("không đọc được ngày gửi", result["status_detail"])

    async def test_incomplete_zalo_sync_banner_fails_closed_before_scan(self):
        page = FakeSyncIncompletePage([])
        result = await scan_open_zalo_chat(
            page,
            job_id=126,
            screenshots_dir=".",
            group_name="MindX - VT-C4K-GB40",
            actual_group_name="MindX - VT-C4K-GB40",
            person="Teacher",
            class_date_str="26/07/2026",
            today=datetime(2026, 7, 29),
            ensure_not_cancelled=AsyncMock(),
        )

        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertEqual(
            result["evidence"]["classifier_result"]["decision"],
            "ZALO_SYNC_INCOMPLETE",
        )
        self.assertEqual(page.screenshots, [])

    async def test_late_report_beyond_six_days_is_evidenced_but_not_submitted(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Hôm nay",
            ),
        ]
        page = FakePage(messages)

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=127,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="20/07/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )

        self.assertEqual(result["evidence_state"], SUBMITTED_LATE_VERIFIED)
        self.assertFalse(result["submitted_for_bcnb"])
        self.assertIn("Quá hạn 9 ngày", result["status_detail"])

    async def test_invalid_class_date_fails_closed_before_browser_interaction(self):
        page = FakePage([])
        result = await scan_open_zalo_chat(
            page,
            job_id=128,
            screenshots_dir=".",
            group_name="MindX - VT-C4K-GB40",
            actual_group_name="MindX - VT-C4K-GB40",
            person="Teacher",
            class_date_str="31/02/2026",
            today=datetime(2026, 7, 29),
            ensure_not_cancelled=AsyncMock(),
        )

        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertEqual(
            result["evidence"]["classifier_result"]["decision"],
            "INVALID_CLASS_DATE",
        )
        page.mouse.move.assert_not_awaited()

    async def test_failed_negative_screenshots_cannot_claim_not_found(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage("Tin nhắn mới thông thường " * 5, "Hôm nay"),
        ]
        page = FakeFailingScreenshotPage(messages)

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=129,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )

        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertIn("Không chụp được ảnh", result["status_detail"])

    async def test_ui_date_helper_and_window_helpers_fail_closed(self):
        today = datetime(2026, 7, 29)
        self.assertIsNone(await _message_date_from_zalo_ui(FakeNoDateMessage("", ""), today))
        self.assertIsNone(await _message_date_from_zalo_ui(FakeBrokenDateMessage("", ""), today))
        self.assertFalse(await _zalo_sync_is_incomplete(FakeHiddenBannerPage()))
        self.assertEqual(
            _required_window_start("10/07/2026", today),
            datetime(2026, 7, 8),
        )
        empty = build_empty_evidence_result(
            group="MindX - VT-C4K-GB40",
            person="Teacher",
            actual_group_name=None,
            scanned_at=today,
            today=today,
            reason="Không mở được chat.",
        )
        self.assertEqual(empty["evidence_state"], INSUFFICIENT_EVIDENCE)

    async def test_capture_none_and_no_class_date_report_paths(self):
        self.assertIsNone(
            await _capture_zalo_evidence(
                FakePage([]), None, job_id=130, screenshots_dir=".", label="none"
            )
        )
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "23/07/2026",
            ),
        ]
        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                FakePage(messages),
                job_id=131,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )
        self.assertEqual(result["evidence_state"], SUBMITTED_VERIFIED)
        self.assertEqual(
            result["evidence"]["classifier_result"]["decision"],
            "SUBMITTED_WITHOUT_CLASS_DATE",
        )

    async def test_on_time_and_before_class_candidates_are_distinguished(self):
        on_time_messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "26/07/2026",
            ),
        ]
        before_class_messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "23/07/2026",
            ),
        ]
        with tempfile.TemporaryDirectory() as root:
            on_time = await scan_open_zalo_chat(
                FakePage(on_time_messages),
                job_id=132,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="26/07/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )
            before_class = await scan_open_zalo_chat(
                FakePage(before_class_messages),
                job_id=133,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="26/07/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )
        self.assertEqual(on_time["evidence_state"], SUBMITTED_VERIFIED)
        self.assertFalse(before_class["submitted_for_bcnb"])
        self.assertEqual(
            before_class["evidence"]["classifier_result"]["candidates"][0]["decision"],
            "BEFORE_CLASS_DATE",
        )


if __name__ == "__main__":
    unittest.main()
