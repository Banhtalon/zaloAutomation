import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from datetime import datetime, timezone

from services.zalo_evidence import (
    INSUFFICIENT_EVIDENCE,
    NOT_FOUND_VERIFIED,
    SUBMITTED_LATE_VERIFIED,
    SUBMITTED_VERIFIED,
)
from services.zalo_scan_evidence import (
    _capture_zalo_evidence,
    _message_date_from_zalo_ui,
    _required_window_start,
    _zalo_sync_is_incomplete,
    build_empty_evidence_result,
    detect_history_gap,
    EvidenceBoundsExceeded,
    scan_open_zalo_chat,
)


class FakeDateHeaders:
    def __init__(self, value, *, scroll_error=None):
        self.value = value
        self.last = self
        self.scroll_into_view_if_needed = AsyncMock()
        if scroll_error is not None:
            self.scroll_into_view_if_needed.side_effect = scroll_error

    async def count(self):
        return 1

    async def inner_text(self):
        return self.value


class FakeMessage:
    def __init__(self, text, date_header, *, date_scroll_error=None):
        self.text = text
        self.date_header = date_header
        self.date_headers = FakeDateHeaders(
            date_header,
            scroll_error=date_scroll_error,
        )
        self.scroll_into_view_if_needed = AsyncMock()

    async def inner_text(self):
        return self.text

    def locator(self, _selector):
        return self.date_headers


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


class CountingListLocator(FakeListLocator):
    def __init__(self, values, *, reported_count=None, fail_on_all=False):
        super().__init__(values)
        self.reported_count = len(values) if reported_count is None else reported_count
        self.fail_on_all = fail_on_all
        self.count_calls = 0

    async def count(self):
        self.count_calls += 1
        return self.reported_count

    async def all(self):
        if self.fail_on_all:
            raise AssertionError("message materialization must be bounded first")
        return await super().all()


class CountingPage(FakePage):
    def __init__(self, messages, *, reported_count=None, fail_on_all=False):
        super().__init__(messages)
        self.message_locator = CountingListLocator(
            messages,
            reported_count=reported_count,
            fail_on_all=fail_on_all,
        )

    def locator(self, selector):
        if selector == '[data-component="bubble-message"]':
            return self.message_locator
        return super().locator(selector)


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
    def __init__(self, messages, *, error_message="screenshot unavailable"):
        super().__init__(messages)
        self.error_message = error_message

    async def screenshot(self, *, path):
        raise RuntimeError(self.error_message)


class HangingDateHeaders(FakeDateHeaders):
    def __init__(self, value):
        super().__init__(value)
        self.scroll_into_view_if_needed = AsyncMock(side_effect=self._hang)

    async def _hang(self, **_kwargs):
        await asyncio.Event().wait()


class HangingCaptureMessage(FakeMessage):
    def __init__(self, text, date_header):
        super().__init__(text, date_header)
        self.date_headers = HangingDateHeaders(date_header)


class HangingScreenshotPage(FakePage):
    async def screenshot(self, *, path):
        await asyncio.Event().wait()


class MissingScreenshotFilePage(FakePage):
    async def screenshot(self, *, path):
        return None


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
    async def test_canary_message_bound_rejects_before_materialization(self):
        page = CountingPage(
            [],
            reported_count=1001,
            fail_on_all=True,
        )
        with self.assertRaises(EvidenceBoundsExceeded):
            await scan_open_zalo_chat(
                page,
                job_id=160,
                screenshots_dir=".",
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="29/07/2026",
                today=datetime(2026, 7, 29, tzinfo=timezone.utc),
                ensure_not_cancelled=AsyncMock(),
                capture_max_messages=1000,
            )
        self.assertEqual(page.message_locator.count_calls, 1)

    async def test_canary_bounds_and_window_are_validated(self):
        page = FakePage([])
        common = {
            "page": page,
            "job_id": 161,
            "screenshots_dir": ".",
            "group_name": "MindX - VT-C4K-GB40",
            "actual_group_name": "MindX - VT-C4K-GB40",
            "person": "Teacher",
            "class_date_str": "29/07/2026",
            "today": datetime(2026, 7, 29, tzinfo=timezone.utc),
            "ensure_not_cancelled": AsyncMock(),
        }
        with self.assertRaises(EvidenceBoundsExceeded):
            await scan_open_zalo_chat(**common, capture_max_scrolls=0)
        with self.assertRaises(EvidenceBoundsExceeded):
            await scan_open_zalo_chat(**common, capture_max_messages=1001)
        with self.assertRaises(ValueError):
            await scan_open_zalo_chat(
                **common,
                capture_window_start=datetime(2026, 7, 17),
            )
        with self.assertRaises(ValueError):
            await scan_open_zalo_chat(
                **common,
                capture_window_start=datetime(2026, 7, 18, tzinfo=timezone.utc),
                capture_window_end=datetime(2026, 7, 17, tzinfo=timezone.utc),
            )

    async def test_canary_window_uses_bounded_count_and_real_extraction(self):
        messages = [
            FakeMessage("Tin nhắn thông thường " * 5, "17/07/2026"),
            FakeMessage("Trao đổi lớp học thông thường " * 5, "Hôm nay"),
        ]
        page = CountingPage(messages)
        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=162,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="29/07/2026",
                today=datetime(2026, 7, 29, tzinfo=timezone.utc),
                ensure_not_cancelled=AsyncMock(),
                capture_max_scrolls=2,
                capture_max_messages=1000,
                capture_window_start=datetime(2026, 7, 17, tzinfo=timezone.utc),
                capture_window_end=datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
        self.assertEqual(result["evidence"]["required_window_start"], "2026-07-17")
        self.assertEqual(result["evidence"]["required_window_end"], "2026-07-27")
        self.assertGreaterEqual(page.message_locator.count_calls, 3)

    async def test_wrong_open_chat_header_fails_closed_before_classification(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Hôm nay",
            ),
        ]
        page = FakePage(messages)
        ensure_not_cancelled = AsyncMock()

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=122,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB41",
                person="Teacher",
                class_date_str="29/07/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=ensure_not_cancelled,
            )

        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertEqual(
            result["evidence"]["classifier_result"]["decision"],
            "GROUP_IDENTITY_MISMATCH",
        )
        self.assertIn("không khớp", result["status_detail"])
        self.assertEqual(page.screenshots, [])
        page.mouse.move.assert_not_awaited()
        ensure_not_cancelled.assert_not_awaited()

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
            self.assertEqual(images["oldest"]["ui_date_text"], "18/07/2026")
            self.assertEqual(len(images["oldest_message"]["sha256"]), 64)

        self.assertEqual(len(page.screenshots), 3)
        messages[0].date_headers.scroll_into_view_if_needed.assert_awaited()
        messages[0].scroll_into_view_if_needed.assert_awaited()
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

    async def test_gb40_next_class_date_uses_proven_previous_lesson_date(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "22/07/2026"),
            FakeMessage(
                "Kính gửi quý phụ huynh. Tổng kết sau buổi học số 6 - 26/07/2026. "
                "Mã lớp: VT-C4K-GB40. NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên. "
                "Nội dung buổi học kế tiếp: 02/08/2026.",
                "29/07/2026",
            ),
        ]
        page = FakePage(messages)

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=134,
                screenshots_dir=root,
                group_name="MindX - Lập trình Game VT-C4K-GB40 CN",
                actual_group_name="MindX - Lập trình Game VT-C4K-GB40 CN",
                person="Teacher",
                class_date_str="02/08/2026",
                next_class_date_str="02/08/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )

        candidate = result["evidence"]["classifier_result"]["candidates"][0]
        self.assertEqual(result["evidence_state"], SUBMITTED_VERIFIED)
        self.assertTrue(result["submitted_for_bcnb"])
        self.assertEqual(candidate["ui_sent_at"], "2026-07-29")
        self.assertEqual(candidate["previous_lesson_date"], "2026-07-26")
        self.assertEqual(candidate["date_reference"], "previous_lesson_date_from_content")
        self.assertEqual(
            result["evidence"]["date_context"]["previous_lesson_date"],
            "2026-07-26",
        )

    async def test_armb15_uses_authoritative_bcnb_week_not_the_date_iso_week(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "27/07/2026"),
            FakeMessage(
                "Kính gửi quý phụ huynh. Update sau buổi học số 11 - 03/08/2026. "
                "Mã lớp: VT-ROB-ARMB15. NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên.",
                "06/08/2026",
            ),
        ]
        page = FakePage(messages)

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=136,
                screenshots_dir=root,
                group_name="MindX - Robotics-VT-ROB-ARMB15 T2 (18H-20H)",
                actual_group_name="MindX - Robotics-VT-ROB-ARMB15 T2 (18H-20H)",
                person="Teacher",
                class_date_str="27/07/2026",
                scan_target={
                    "week": 33,
                    "year": 2026,
                    "class_code": "VT-ROB-ARMB15",
                    "lesson": "11",
                    "class_date": "03/08/2026",
                    "zalo_deadline": "20:00 05/08/2026",
                },
                today=datetime(2026, 8, 6),
                ensure_not_cancelled=AsyncMock(),
            )

        candidate = result["evidence"]["classifier_result"]["candidates"][0]
        context = result["evidence"]["date_context"]
        self.assertTrue(result["submitted_for_bcnb"])
        self.assertEqual(candidate["lesson_date"], "2026-08-03")
        self.assertEqual(candidate["ui_sent_at"], "2026-08-06")
        self.assertEqual(context["target_week"], 33)
        self.assertEqual(context["target_year"], 2026)
        self.assertEqual(context["target_week_source"], "BCNB_SERVER")
        self.assertEqual(context["lesson_date_source"], "bcnb_target_date")

    async def test_ga35_style_stale_template_header_is_corrected_only_with_bai_signal(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi quý phụ huynh. Update sau buổi học số 5 - 26/07/2026. "
                "Mã lớp: VT-C4K-GA35. NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên. "
                "Bài số 6: Lập trình di chuyển với GML. NHIỆM VỤ KHI VỀ NHÀ.",
                "06/08/2026",
            ),
        ]
        target = {
            "week": 32,
            "year": 2026,
            "class_code": "VT-C4K-GA35",
            "lesson": "6",
            "class_date": "02/08/2026",
            "zalo_deadline": "12:00 04/08/2026",
        }

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                FakePage(messages),
                job_id=139,
                screenshots_dir=root,
                group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                actual_group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                person="Teacher",
                class_date_str="02/08/2026",
                scan_target=target,
                today=datetime(2026, 8, 14),
                ensure_not_cancelled=AsyncMock(),
            )

        candidate = result["evidence"]["classifier_result"]["candidates"][-1]
        context = result["evidence"]["date_context"]
        self.assertEqual(result["evidence_state"], SUBMITTED_VERIFIED)
        self.assertTrue(result["submitted_for_bcnb"])
        self.assertEqual(candidate["decision"], "SUBMITTED_DATE_TYPO")
        self.assertEqual(candidate["target_match"]["decision"], "TARGET_DATE_TYPO_CORRECTED")
        self.assertEqual(context["target_week"], 32)
        self.assertEqual(context["lesson_date"], "2026-08-02")
        self.assertEqual(context["reported_lesson_date"], "2026-07-26")
        self.assertEqual(context["reported_lesson"], 5)
        self.assertIn("ghi sai ngày/buổi", result["status_detail"])

    async def test_old_report_is_not_reused_for_a_later_bcnb_target(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi quý phụ huynh. Update sau buổi học số 5 - 26/07/2026. "
                "Mã lớp: VT-C4K-GA35. NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên. "
                "Bài số 6: Lập trình di chuyển với GML. NHIỆM VỤ KHI VỀ NHÀ.",
                "06/08/2026",
            ),
        ]

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                FakePage(messages),
                job_id=140,
                screenshots_dir=root,
                group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                actual_group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                person="Teacher",
                class_date_str="09/08/2026",
                scan_target={
                    "week": 33,
                    "year": 2026,
                    "class_code": "VT-C4K-GA35",
                    "lesson": "7",
                    "class_date": "09/08/2026",
                    "zalo_deadline": "12:00 11/08/2026",
                },
                today=datetime(2026, 8, 14),
                ensure_not_cancelled=AsyncMock(),
            )

        candidate = result["evidence"]["classifier_result"]["candidates"][-1]
        self.assertEqual(result["evidence_state"], NOT_FOUND_VERIFIED)
        self.assertFalse(result["submitted_for_bcnb"])
        self.assertEqual(candidate["decision"], "TARGET_BEFORE_CLASS_DATE")
        self.assertIn("không dùng cho dòng này", candidate["target_match"]["note"])
        self.assertIsNone(result["evidence"]["date_context"]["target_match_note"])
        self.assertNotIn("trước ngày học target", result["status_detail"])

    async def test_unconfirmed_stale_header_remains_manual_review(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi quý phụ huynh. Update sau buổi học số 5 - 26/07/2026. "
                "Mã lớp: VT-C4K-GA35. NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên. "
                "NHIỆM VỤ KHI VỀ NHÀ.",
                "06/08/2026",
            ),
        ]

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                FakePage(messages),
                job_id=141,
                screenshots_dir=root,
                group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                actual_group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                person="Teacher",
                class_date_str="02/08/2026",
                scan_target={
                    "week": 32,
                    "year": 2026,
                    "class_code": "VT-C4K-GA35",
                    "lesson": "6",
                    "class_date": "02/08/2026",
                    "zalo_deadline": "12:00 04/08/2026",
                },
                today=datetime(2026, 8, 14),
                ensure_not_cancelled=AsyncMock(),
            )

        candidate = result["evidence"]["classifier_result"]["candidates"][-1]
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertFalse(result["submitted_for_bcnb"])
        self.assertEqual(candidate["decision"], "TARGET_SESSION_AMBIGUOUS")
        self.assertIn("chưa đủ căn cứ", result["status_detail"])
        self.assertNotIn("W32/2026, ngày 02/08, buổi 6", result["status_detail"])
        self.assertIn("W32/2026, ngày 02/08, buổi 6", candidate["target_match"]["note"])
        self.assertIsNone(result["evidence"]["date_context"]["target_match_note"])

    async def test_next_class_date_without_previous_date_evidence_is_manual_review(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "22/07/2026"),
            FakeMessage(
                "Kính gửi quý phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên.",
                "29/07/2026",
            ),
        ]
        page = FakePage(messages)

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=135,
                screenshots_dir=root,
                group_name="MindX - Lập trình Game VT-C4K-GB40 CN",
                actual_group_name="MindX - Lập trình Game VT-C4K-GB40 CN",
                person="Teacher",
                class_date_str="02/08/2026",
                next_class_date_str="02/08/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )

        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertFalse(result["submitted_for_bcnb"])
        self.assertIn("chưa xác định được buổi học tham chiếu", result["status_detail"])
        self.assertEqual(
            result["evidence"]["classifier_result"]["candidates"][0]["decision"],
            "BEFORE_CLASS_DATE",
        )

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

    async def test_late_report_beyond_six_days_is_evidenced_and_submitted(self):
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
        self.assertTrue(result["submitted_for_bcnb"])
        self.assertIn("Trễ 9 ngày", result["status_detail"])

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

    async def test_failed_report_screenshot_cannot_claim_submitted(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Hôm nay",
            ),
        ]
        page = FakeFailingScreenshotPage(messages)

        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=130,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="29/07/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )

        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertFalse(result["submitted_for_bcnb"])
        self.assertIn("Không chụp được ảnh tin nhắn báo cáo", result["status_detail"])

    async def test_report_found_screenshot_error_persists_typed_capture_diagnostic(self):
        secret_marker = "synthetic-screenshot-secret-marker"
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Hôm nay",
            ),
        ]
        page = FakeFailingScreenshotPage(messages, error_message=secret_marker)

        with tempfile.TemporaryDirectory() as root:
            with (
                patch("services.zalo_scan_evidence.asyncio.sleep", new=AsyncMock()),
                patch("services.zalo_scan_evidence.log_message") as capture_log,
            ):
                result = await scan_open_zalo_chat(
                    page,
                    job_id=150,
                    screenshots_dir=root,
                    group_name="MindX - VT-C4K-GB40",
                    actual_group_name="MindX - VT-C4K-GB40",
                    person="Teacher",
                    class_date_str="29/07/2026",
                    today=datetime(2026, 7, 29),
                    ensure_not_cancelled=AsyncMock(),
                    worker_id="scan-7",
                    attempt=2,
                )

        classifier = result["evidence"]["classifier_result"]
        self.assertTrue(classifier["report_found"])
        self.assertEqual(classifier["decision"], "SUBMITTED_ON_TIME")
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertFalse(result["submitted_for_bcnb"])
        self.assertIn("đã nhận diện report nhưng không tạo được evidence", result["status_detail"].casefold())
        diagnostics = result["evidence"].get("capture_diagnostics", [])
        screenshot_failures = [
            item for item in diagnostics if item.get("error_code") == "CAPTURE_SCREENSHOT_ERROR"
        ]
        self.assertEqual(len(screenshot_failures), 2)
        self.assertEqual(screenshot_failures[0]["label"], "report")
        self.assertEqual(screenshot_failures[0]["phase"], "CAPTURE_SCREENSHOT")
        self.assertIsInstance(screenshot_failures[0]["elapsed_ms"], int)
        self.assertNotIn(secret_marker, repr(result))
        self.assertTrue(all(secret_marker not in repr(call) for call in capture_log.call_args_list))
        capture_calls = [
            call
            for call in capture_log.call_args_list
            if call.kwargs.get("event") == "zalo.scan.capture_telemetry"
        ]
        self.assertEqual(len(capture_calls), 2)
        allowed_log_fields = {
            "event",
            "job_id",
            "worker_id",
            "phase",
            "attempt",
            "elapsed_ms",
            "page_state",
            "error_code",
        }
        self.assertEqual(set(capture_calls[0].kwargs), allowed_log_fields)
        self.assertEqual(capture_calls[0].kwargs["job_id"], 150)
        self.assertEqual(capture_calls[0].kwargs["worker_id"], "scan-7")
        self.assertEqual(capture_calls[0].kwargs["attempt"], 2)
        self.assertEqual(capture_calls[0].kwargs["phase"], "CAPTURE_SCREENSHOT")
        self.assertEqual(capture_calls[0].kwargs["page_state"], "READY")
        self.assertEqual(capture_calls[0].kwargs["error_code"], "CAPTURE_SCREENSHOT_ERROR")
        self.assertNotIn("label", capture_calls[0].kwargs)
        self.assertNotIn("report", repr(capture_calls[0]))

    async def test_detached_date_locator_during_scroll_is_typed_and_sanitized(self):
        secret_marker = "synthetic-detached-secret-marker"
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Hôm nay",
                date_scroll_error=RuntimeError(secret_marker),
            ),
        ]
        page = FakePage(messages)

        with tempfile.TemporaryDirectory() as root:
            with (
                patch("services.zalo_scan_evidence.asyncio.sleep", new=AsyncMock()),
                patch("services.zalo_scan_evidence.log_message") as capture_log,
            ):
                result = await scan_open_zalo_chat(
                    page,
                    job_id=151,
                    screenshots_dir=root,
                    group_name="MindX - VT-C4K-GB40",
                    actual_group_name="MindX - VT-C4K-GB40",
                    person="Teacher",
                    class_date_str="29/07/2026",
                    today=datetime(2026, 7, 29),
                    ensure_not_cancelled=AsyncMock(),
                )

        self.assertTrue(result["evidence"]["classifier_result"]["report_found"])
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        diagnostics = result["evidence"].get("capture_diagnostics", [])
        self.assertTrue(
            any(
                item.get("error_code") == "CAPTURE_SCROLL_ERROR"
                and item.get("phase") == "CAPTURE_SCROLL"
                for item in diagnostics
            )
        )
        self.assertNotIn(secret_marker, repr(result))
        self.assertTrue(all(secret_marker not in repr(call) for call in capture_log.call_args_list))

    async def test_capture_scroll_timeout_returns_in_finite_time_with_typed_code(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            HangingCaptureMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Hôm nay",
            ),
        ]
        page = FakePage(messages)

        with tempfile.TemporaryDirectory() as root:
            with (
                patch("services.zalo_scan_evidence.asyncio.sleep", new=AsyncMock()),
                patch("services.zalo_scan_evidence.log_message") as capture_log,
                patch("services.zalo_scan_evidence.CAPTURE_SCROLL_TIMEOUT_SECONDS", 0.05),
            ):
                try:
                    result = await asyncio.wait_for(
                        scan_open_zalo_chat(
                            page,
                            job_id=152,
                            screenshots_dir=root,
                            group_name="MindX - VT-C4K-GB40",
                            actual_group_name="MindX - VT-C4K-GB40",
                            person="Teacher",
                            class_date_str="29/07/2026",
                            today=datetime(2026, 7, 29),
                            ensure_not_cancelled=AsyncMock(),
                        ),
                        timeout=0.2,
                    )
                except asyncio.TimeoutError as exc:
                    self.fail(f"capture scroll did not return within finite timeout: {exc}")

        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        diagnostics = result["evidence"].get("capture_diagnostics", [])
        self.assertTrue(
            any(
                item.get("error_code") == "CAPTURE_SCROLL_TIMEOUT"
                and item.get("phase") == "CAPTURE_SCROLL"
                for item in diagnostics
            )
        )
        self.assertTrue(all("TimeoutError" not in repr(call) for call in capture_log.call_args_list))

    async def test_screenshot_timeout_is_distinct_from_screenshot_error(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Hôm nay",
            ),
        ]
        page = HangingScreenshotPage(messages)

        with tempfile.TemporaryDirectory() as root:
            with (
                patch("services.zalo_scan_evidence.asyncio.sleep", new=AsyncMock()),
                patch("services.zalo_scan_evidence.log_message"),
                patch("services.zalo_scan_evidence.CAPTURE_SCREENSHOT_TIMEOUT_SECONDS", 0.05),
            ):
                try:
                    result = await asyncio.wait_for(
                        scan_open_zalo_chat(
                            page,
                            job_id=153,
                            screenshots_dir=root,
                            group_name="MindX - VT-C4K-GB40",
                            actual_group_name="MindX - VT-C4K-GB40",
                            person="Teacher",
                            class_date_str="29/07/2026",
                            today=datetime(2026, 7, 29),
                            ensure_not_cancelled=AsyncMock(),
                        ),
                        timeout=0.2,
                    )
                except asyncio.TimeoutError as exc:
                    self.fail(f"capture screenshot did not return within finite timeout: {exc}")

        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        diagnostics = result["evidence"].get("capture_diagnostics", [])
        self.assertTrue(
            any(
                item.get("error_code") == "CAPTURE_SCREENSHOT_TIMEOUT"
                and item.get("phase") == "CAPTURE_SCREENSHOT"
                for item in diagnostics
            )
        )

    async def test_missing_screenshot_file_has_typed_validation_diagnostic(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Hôm nay",
            ),
        ]
        page = MissingScreenshotFilePage(messages)

        with tempfile.TemporaryDirectory() as root:
            with patch("services.zalo_scan_evidence.asyncio.sleep", new=AsyncMock()):
                result = await scan_open_zalo_chat(
                    page,
                    job_id=154,
                    screenshots_dir=root,
                    group_name="MindX - VT-C4K-GB40",
                    actual_group_name="MindX - VT-C4K-GB40",
                    person="Teacher",
                    class_date_str="29/07/2026",
                    today=datetime(2026, 7, 29),
                    ensure_not_cancelled=AsyncMock(),
                )

        self.assertTrue(result["evidence"]["classifier_result"]["report_found"])
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        diagnostics = result["evidence"].get("capture_diagnostics", [])
        self.assertTrue(
            any(
                item.get("error_code") == "CAPTURE_FILE_VALIDATION_ERROR"
                and item.get("phase") == "CAPTURE_SCREENSHOT"
                for item in diagnostics
            )
        )

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

    async def test_previous_lesson_date_context_is_used_when_content_has_no_date(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "29/07/2026",
            ),
        ]
        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                FakePage(messages),
                job_id=137,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="02/08/2026",
                previous_lesson_date_str="26/07/2026",
                next_class_date_str="02/08/2026",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )
        candidate = result["evidence"]["classifier_result"]["candidates"][0]
        self.assertEqual(candidate["date_reference"], "previous_lesson_date")
        self.assertEqual(
            result["evidence"]["date_context"]["lesson_date"],
            "2026-07-26",
        )

    async def test_report_without_class_date_over_eight_days_remains_submitted(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "10/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "15/07/2026",
            ),
        ]
        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                FakePage(messages),
                job_id=138,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="",
                today=datetime(2026, 7, 29),
                ensure_not_cancelled=AsyncMock(),
            )
        self.assertEqual(result["evidence_state"], SUBMITTED_LATE_VERIFIED)
        self.assertTrue(result["submitted_for_bcnb"])

    def test_detect_history_gap_unit(self):
        target = datetime(2026, 9, 11)
        # Gap of 13 days straddling target date (04/09 to 17/09)
        dates = [datetime(2026, 9, 4), datetime(2026, 9, 17)]
        gap = detect_history_gap(dates, target, min_gap_days=5)
        self.assertIsNotNone(gap)
        d_prev, d_curr, gap_days = gap
        self.assertEqual(d_prev.date(), datetime(2026, 9, 4).date())
        self.assertEqual(d_curr.date(), datetime(2026, 9, 17).date())
        self.assertEqual(gap_days, 13)

        # Message exists on target date -> No gap
        dates_with_target = [datetime(2026, 9, 4), datetime(2026, 9, 11), datetime(2026, 9, 17)]
        self.assertIsNone(detect_history_gap(dates_with_target, target, min_gap_days=5))

        # Small gap (< 5 days) -> No suspicious gap
        small_gap_dates = [datetime(2026, 9, 10), datetime(2026, 9, 12)]
        self.assertIsNone(detect_history_gap(small_gap_dates, target, min_gap_days=5))

        # Target date outside the gap range -> No gap
        past_target = datetime(2026, 9, 1)
        self.assertIsNone(detect_history_gap(dates, past_target, min_gap_days=5))

    async def test_history_gap_straddling_target_date_fails_closed_with_insufficient_evidence(self):
        # When messages jump across the class date without any message in between,
        # it indicates un-synced history and must NOT conclude NOT_FOUND_VERIFIED.
        messages = [
            FakeMessage("Tin nhắn cũ trước ngày học " * 4, "04/09/2026"),
            FakeMessage("Tin nhắn mới sau ngày học " * 4, "17/09/2026"),
        ]
        page = FakePage(messages)
        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                page,
                job_id=232,
                screenshots_dir=root,
                group_name="[VŨNG TÀU] - VT-KIND-ROB4A03 - [18H00-19H30 - T6] (OFF)",
                actual_group_name="[VŨNG TÀU] - VT-KIND-ROB4A03 - [18H00-19H30 - T6] (OFF)",
                person="Teacher",
                class_date_str="11/09/2026",
                scan_target={
                    "class_code": "VT-KIND-ROB4A03",
                    "class_date": "11/09/2026",
                    "class_date_iso": "2026-09-11",
                    "lesson": "4",
                    "week": 37,
                    "year": 2026,
                    "zalo_deadline": "12:00 13/09/2026",
                },
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
            )
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertFalse(result["submitted_for_bcnb"])
        validation_errors = result["evidence"]["validation_errors"]
        self.assertTrue(any("khoảng trống tin nhắn" in err for err in validation_errors))
        self.assertTrue(any("chưa đồng bộ" in err for err in validation_errors))

    async def test_ai_assist_not_called_when_disabled_or_rule_matched_or_out_of_window(self):
        informal_text = (
            "Dạ thầy gửi ba mẹ tình hình học hôm nay của các bạn lớp mình nhé ạ: "
            "Hôm nay cả lớp đã cùng nhau lắp ráp mô hình robot cảm biến khoảng cách "
            "và lập trình cho robot tự tránh vật cản trên sa bàn thành công."
        )
        opening_notice = (
            "Mindx Vũng Tàu Quản Lí Lớp Học\n"
            "📣 THÔNG BÁO KHAI GIẢNG LỚP LẬP TRÌNH VT-C4K-GB40 📣 @All\n"
            "Em chào quý phụ huynh và học viên của lớp. Lịch học Chủ nhật hàng tuần."
        )
        ai_gateway = AsyncMock(
            return_value={
                "is_likely_report": False,
                "confidence": 0.1,
                "reason": "Tin nhắn trao đổi thông thường",
            }
        )
        target = {
            "class_code": "VT-C4K-GB40",
            "class_date": "11/09/2026",
            "class_date_iso": "2026-09-11",
            "lesson": "4",
            "week": 37,
            "year": 2026,
            "zalo_deadline": "12:00 13/09/2026",
        }
        with tempfile.TemporaryDirectory() as root:
            # 1. Disabled flag -> 0 AI calls even with eligible in-window message
            await scan_open_zalo_chat(
                FakePage([FakeMessage(informal_text, "12/09/2026")]),
                job_id=240,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="11/09/2026",
                scan_target=target,
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
                ai_report_classifier=ai_gateway,
                ai_assist_enabled=False,
            )
            ai_gateway.evaluate_ambiguous_message.assert_not_awaited()

            # 2. Rule-based already matched -> 0 AI calls even when enabled
            valid_report = (
                "Kính gửi phụ huynh. Báo cáo buổi học ngày 11/09/2026 buổi 4. "
                "Mã lớp: VT-C4K-GB40. NỘI DUNG và NHẬN XÉT học viên."
            )
            rule_matched = await scan_open_zalo_chat(
                FakePage(
                    [
                        FakeMessage(informal_text, "12/09/2026"),
                        FakeMessage(valid_report, "12/09/2026"),
                    ]
                ),
                job_id=241,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="11/09/2026",
                scan_target=target,
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
                ai_report_classifier=ai_gateway,
                ai_assist_enabled=True,
            )
            self.assertTrue(rule_matched["submitted_for_bcnb"])
            ai_gateway.evaluate_ambiguous_message.assert_not_awaited()

            # 3. Missing lesson date (no scan_target and empty class_date_str) -> 0 AI calls
            await scan_open_zalo_chat(
                FakePage([FakeMessage(informal_text, "12/09/2026")]),
                job_id=2415,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="",
                scan_target=None,
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
                ai_report_classifier=ai_gateway,
                ai_assist_enabled=True,
            )
            ai_gateway.evaluate_ambiguous_message.assert_not_awaited()

            # 4. Out-of-window or penalized messages -> 0 AI calls, max 3 calls per target_key across two weekly targets in same job
            mixed_messages = [
                FakeMessage(informal_text, "02/09/2026"),  # before W36 class_date (04/09)
                FakeMessage(opening_notice, "05/09/2026"),  # penalized opening notice
                FakeMessage(informal_text + " (w36-1)", "05/09/2026"),
                FakeMessage(informal_text + " (w36-2)", "06/09/2026"),
                FakeMessage(informal_text + " (w37-1)", "11/09/2026"),
                FakeMessage(informal_text + " (w37-2)", "12/09/2026"),
                FakeMessage(informal_text + " (w37-3)", "13/09/2026"),
                FakeMessage(informal_text + " (w37-4)", "14/09/2026"),
            ]
            ai_gateway.evaluate_ambiguous_message = AsyncMock(
                return_value={
                    "is_likely_report": False,
                    "confidence": 0.15,
                    "reason": "Không phải báo cáo",
                }
            )
            w36_target = {
                "class_code": "VT-C4K-GB40",
                "class_date": "04/09/2026",
                "class_date_iso": "2026-09-04",
                "lesson": "3",
                "week": 36,
                "year": 2026,
                "zalo_deadline": "12:00 06/09/2026",
            }
            res_w36 = await scan_open_zalo_chat(
                FakePage(mixed_messages),
                job_id=242,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="04/09/2026",
                scan_target=w36_target,
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
                ai_report_classifier=ai_gateway,
                ai_assist_enabled=True,
            )
            res_w37 = await scan_open_zalo_chat(
                FakePage(mixed_messages),
                job_id=242,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="11/09/2026",
                scan_target=target,
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
                ai_report_classifier=ai_gateway,
                ai_assist_enabled=True,
            )
            self.assertEqual(
                res_w36["evidence"]["classifier_result"]["ai_assist_evaluated"], 2
            )
            self.assertFalse(
                res_w36["evidence"]["classifier_result"]["ai_assist_budget_exhausted"]
            )
            self.assertEqual(
                res_w37["evidence"]["classifier_result"]["ai_assist_evaluated"], 3
            )
            self.assertTrue(
                res_w37["evidence"]["classifier_result"]["ai_assist_budget_exhausted"]
            )
            self.assertEqual(ai_gateway.evaluate_ambiguous_message.await_count, 5)

    async def test_ai_assist_positive_suggestion_never_auto_ticks_bcnb_and_captures_report_image(self):
        informal_text = (
            "Dạ thầy gửi ba mẹ tình hình học hôm nay của các bạn lớp mình nhé ạ: "
            "Hôm nay cả lớp đã cùng nhau lắp ráp mô hình robot cảm biến khoảng cách "
            "và lập trình cho robot tự tránh vật cản trên sa bàn thành công."
        )
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "05/09/2026"),
            FakeMessage(informal_text, "12/09/2026"),
        ]
        ai_gateway = AsyncMock()
        ai_gateway.evaluate_ambiguous_message = AsyncMock(
            return_value={
                "is_likely_report": True,
                "confidence": 0.92,
                "reason": "Có tóm tắt kiến thức buổi học của giáo viên",
            }
        )
        target = {
            "class_code": "VT-C4K-GB40",
            "class_date": "11/09/2026",
            "class_date_iso": "2026-09-11",
            "lesson": "4",
            "week": 37,
            "year": 2026,
            "zalo_deadline": "12:00 13/09/2026",
        }
        with tempfile.TemporaryDirectory() as root:
            result = await scan_open_zalo_chat(
                FakePage(messages),
                job_id=243,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB40",
                actual_group_name="MindX - VT-C4K-GB40",
                person="Teacher",
                class_date_str="11/09/2026",
                scan_target=target,
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
                ai_report_classifier=ai_gateway,
                ai_assist_enabled=True,
            )
        ai_gateway.evaluate_ambiguous_message.assert_awaited_once()
        # Absolute safety boundary: NEVER auto-tick BCNB
        self.assertFalse(result["submitted_for_bcnb"])
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertEqual(
            result["status"],
            "⚠️ AI gợi ý có thể là báo cáo – Cần kiểm tra thủ công",
        )
        self.assertEqual(
            result["evidence"]["classifier_result"]["decision"],
            "AI_SUGGESTED_REPORT_NEEDS_REVIEW",
        )
        self.assertFalse(result["evidence"]["classifier_result"]["report_found"])
        report_image = result["evidence"]["images"]["report"]
        self.assertIsNotNone(report_image)
        self.assertEqual(result["screenshot_path"], report_image["path"])

    async def test_gemini_zalo_report_classifier_gateway_fails_closed(self):
        from services.external_gateways import GeminiZaloReportClassifierGateway

        informal_text = (
            "Dạ thầy gửi ba mẹ tình hình học hôm nay của các bạn lớp mình nhé ạ: "
            "Hôm nay cả lớp đã cùng nhau lắp ráp mô hình robot cảm biến khoảng cách "
            "và lập trình cho robot tự tránh vật cản trên sa bàn thành công."
        )
        # 1. Isolated quality mode without custom client_factory must refuse network
        default_gw = GeminiZaloReportClassifierGateway(api_key_provider=lambda: "key")
        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "1"}, clear=False):
            isolated_res = await default_gw.evaluate_ambiguous_message(informal_text)
        self.assertFalse(isolated_res["is_likely_report"])
        self.assertEqual(isolated_res["reason"], "ISOLATED_QUALITY_MODE_DISABLED")

        # 2. Valid JSON response via injected client_factory
        class _FakeModels:
            async def generate_content(self, **_kwargs):
                return type(
                    "Resp",
                    (),
                    {
                        "text": '{"is_likely_report": true, "confidence": 0.88, "reason": "Báo cáo tự do"}'
                    },
                )()

        fake_client = type(
            "Client", (), {"aio": type("Aio", (), {"models": _FakeModels()})()}
        )()
        gw = GeminiZaloReportClassifierGateway(
            api_key_provider=lambda: "fake-key",
            client_factory=lambda _k: fake_client,
        )
        ok_res = await gw.evaluate_ambiguous_message(
            informal_text,
            group_name="MindX - VT-C4K-GB40",
            lesson="4",
            class_date_iso="2026-09-11",
        )
        self.assertTrue(ok_res["is_likely_report"])
        self.assertAlmostEqual(ok_res["confidence"], 0.88)

        # 3. Runtime error fails closed
        class _ErrorModels:
            async def generate_content(self, **_kwargs):
                raise RuntimeError("network error")

        err_client = type(
            "Client", (), {"aio": type("Aio", (), {"models": _ErrorModels()})()}
        )()
        err_gw = GeminiZaloReportClassifierGateway(
            api_key_provider=lambda: "fake-key",
            client_factory=lambda _k: err_client,
        )
        err_res = await err_gw.evaluate_ambiguous_message(informal_text)
        self.assertFalse(err_res["is_likely_report"])
        self.assertEqual(err_res["reason"], "AI_CLASSIFIER_ERROR")

    async def test_report_screenshot_retries_once_and_succeeds_on_second_attempt(self):
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi phụ huynh. Báo cáo buổi học. Mã lớp: VT-C4K-GB40. "
                "NỘI DUNG và NHẬN XÉT học viên.",
                "Hôm nay",
            ),
        ]

        class FlakyOnceScreenshotPage(FakePage):
            def __init__(self, msgs):
                super().__init__(msgs)
                self.attempts = 0

            async def screenshot(self, *, path):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("transient screenshot glitch")
                await super().screenshot(path=path)

        page = FlakyOnceScreenshotPage(messages)
        with tempfile.TemporaryDirectory() as root:
            with patch("services.zalo_scan_evidence.asyncio.sleep", new=AsyncMock()):
                result = await scan_open_zalo_chat(
                    page,
                    job_id=250,
                    screenshots_dir=root,
                    group_name="MindX - VT-C4K-GB40",
                    actual_group_name="MindX - VT-C4K-GB40",
                    person="Teacher",
                    class_date_str="29/07/2026",
                    today=datetime(2026, 7, 29),
                    ensure_not_cancelled=AsyncMock(),
                )
        self.assertEqual(page.attempts, 2)
        self.assertEqual(result["evidence_state"], SUBMITTED_VERIFIED)
        self.assertTrue(result["submitted_for_bcnb"])
        self.assertIsNotNone(result["evidence"]["images"]["report"])
        diagnostics = result["evidence"].get("capture_diagnostics", [])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["error_code"], "CAPTURE_SCREENSHOT_ERROR")
        self.assertEqual(diagnostics[0]["attempt"], 1)

    async def test_history_gap_loop_checks_cancellation_and_message_cap_returns_insufficient(self):
        messages = [
            FakeMessage("Tin nhắn thông thường rất dài " * 4, "15/07/2026"),
            FakeMessage("Trao đổi bài tập về nhà bình thường " * 4, "29/07/2026"),
        ]
        # 1. Hitting capture_max_messages cap returns INSUFFICIENT_EVIDENCE with reason, never NOT_FOUND_VERIFIED
        with tempfile.TemporaryDirectory() as root:
            with patch("services.zalo_scan_evidence.asyncio.sleep", new=AsyncMock()):
                capped = await scan_open_zalo_chat(
                    FakePage(messages),
                    job_id=251,
                    screenshots_dir=root,
                    group_name="MindX - VT-C4K-GB40",
                    actual_group_name="MindX - VT-C4K-GB40",
                    person="Teacher",
                    class_date_str="22/07/2026",
                    today=datetime(2026, 7, 29),
                    ensure_not_cancelled=AsyncMock(),
                    capture_max_messages=2,
                )
        self.assertEqual(capped["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertNotEqual(capped["evidence_state"], NOT_FOUND_VERIFIED)
        self.assertEqual(
            capped["evidence"]["classifier_result"]["decision"],
            "MESSAGE_CAP_REACHED",
        )
        self.assertTrue(
            any(
                "capture_max_messages" in reason
                for reason in capped["evidence"]["validation_errors"]
            )
        )

        # 2. History gap loop periodically calls ensure_not_cancelled
        call_count = 0

        async def cancel_during_gap_check():
            nonlocal call_count
            call_count += 1
            # Scroll loop + reversed evaluation loop + history gap loop
            if call_count >= 5:
                raise RuntimeError("CANCELLED_IN_GAP_LOOP")

        with tempfile.TemporaryDirectory() as root:
            with patch("services.zalo_scan_evidence.asyncio.sleep", new=AsyncMock()):
                with self.assertRaisesRegex(RuntimeError, "CANCELLED_IN_GAP_LOOP"):
                    await scan_open_zalo_chat(
                        FakePage(messages),
                        job_id=252,
                        screenshots_dir=root,
                        group_name="MindX - VT-C4K-GB40",
                        actual_group_name="MindX - VT-C4K-GB40",
                        person="Teacher",
                        class_date_str="22/07/2026",
                        today=datetime(2026, 7, 29),
                        ensure_not_cancelled=cancel_during_gap_check,
                        capture_max_scrolls=1,
                    )

    async def test_5_3_2_and_5_3_5_rejected_candidate_note_and_content_date_mismatch_isolation(
        self,
    ):
        # Rejected candidate has CONTENT_DATE_MISMATCH (content date 26/07 vs sent 06/08)
        # and TARGET_BEFORE_CLASS_DATE note; neither may leak into date_context or warnings when NOT_FOUND.
        rejected_only_messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi quý phụ huynh. Update sau buổi học số 5 - 26/07/2026. "
                "Mã lớp: VT-C4K-GA35. NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên.",
                "06/08/2026",
            ),
        ]
        target = {
            "week": 33,
            "year": 2026,
            "class_code": "VT-C4K-GA35",
            "lesson": "7",
            "class_date": "09/08/2026",
            "zalo_deadline": "12:00 11/08/2026",
        }
        with tempfile.TemporaryDirectory() as root:
            not_found_res = await scan_open_zalo_chat(
                FakePage(rejected_only_messages),
                job_id=260,
                screenshots_dir=root,
                group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                actual_group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                person="Teacher",
                class_date_str="09/08/2026",
                scan_target=target,
                today=datetime(2026, 8, 14),
                ensure_not_cancelled=AsyncMock(),
            )

        self.assertEqual(not_found_res["evidence_state"], NOT_FOUND_VERIFIED)
        self.assertIsNone(
            not_found_res["evidence"]["date_context"]["target_match_note"]
        )
        self.assertNotIn("trước ngày học target", not_found_res["status_detail"])
        self.assertEqual(not_found_res["evidence"]["warnings"], [])
        rejected_cand = not_found_res["evidence"]["classifier_result"]["candidates"][0]
        self.assertNotIn("warning", rejected_cand)
        self.assertIn(
            "trước ngày học target",
            rejected_cand["target_match"]["note"],
        )

        # When a newer candidate is rejected (ambiguous/mismatch) and an older candidate matches
        # with a content-date mismatch, CONTENT_DATE_MISMATCH is attached only to the matched candidate
        # and deduplicated in warnings.
        matched_and_rejected_messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "18/07/2026"),
            FakeMessage(
                "Kính gửi quý phụ huynh. Update sau buổi học số 7 - 08/08/2026. "
                "Mã lớp: VT-C4K-GA35. NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên.",
                "10/08/2026",
            ),
            FakeMessage(
                "Kính gửi quý phụ huynh. Update sau buổi học số 8 - 10/08/2026. "
                "Mã lớp: VT-C4K-GA35. NỘI DUNG BUỔI HỌC và NHẬN XÉT học viên.",
                "12/08/2026",
            ),
        ]
        with tempfile.TemporaryDirectory() as root:
            matched_res = await scan_open_zalo_chat(
                FakePage(matched_and_rejected_messages),
                job_id=261,
                screenshots_dir=root,
                group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                actual_group_name="MindX - Lập trình Game VT-C4K-GA35 CN",
                person="Teacher",
                class_date_str="09/08/2026",
                scan_target=target,
                today=datetime(2026, 8, 14),
                ensure_not_cancelled=AsyncMock(),
            )
        self.assertEqual(matched_res["evidence_state"], SUBMITTED_VERIFIED)
        cands = matched_res["evidence"]["classifier_result"]["candidates"]
        self.assertEqual(len(cands), 2)
        self.assertNotIn("warning", cands[0])
        self.assertEqual(cands[1].get("warning"), "CONTENT_DATE_MISMATCH")
        warnings = matched_res["evidence"]["warnings"]
        self.assertEqual(
            warnings,
            ["CONTENT_DATE_MISMATCH: ngày trong nội dung khác ngày gửi trên Zalo."],
        )
        self.assertEqual(
            matched_res["evidence"]["date_context"]["deadline_verification"],
            "PENDING",
        )

    async def test_phase_5_6_ai_assist_error_and_pending_deadline_display(self):
        from repositories.report_repository import normalize_zalo_status_text
        from services.report_email_service import _prepare_email_row_fields

        informal_text = (
            "Dạ thầy gửi ba mẹ tình hình học hôm nay của các bạn lớp mình nhé ạ: "
            "Hôm nay cả lớp đã cùng nhau lắp ráp mô hình robot cảm biến khoảng cách "
            "và lập trình cho robot tự tránh vật cản trên sa bàn thành công."
        )
        messages = [
            FakeMessage("Tin nhắn cũ thông thường " * 5, "05/09/2026"),
            FakeMessage(informal_text, "12/09/2026"),
        ]
        ai_gateway = AsyncMock()
        ai_gateway.evaluate_ambiguous_message = AsyncMock(
            side_effect=RuntimeError("Gemini API timeout")
        )
        target = {
            "class_code": "VT-C4K-GB41",
            "class_date": "11/09/2026",
            "class_date_iso": "2026-09-11",
            "lesson": "4",
            "week": 37,
            "year": 2026,
            "zalo_deadline": "12:00 13/09/2026",
        }
        with tempfile.TemporaryDirectory() as root:
            err_res = await scan_open_zalo_chat(
                FakePage(messages),
                job_id=299,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB41",
                actual_group_name="MindX - VT-C4K-GB41",
                person="Teacher",
                class_date_str="11/09/2026",
                scan_target=target,
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
                ai_report_classifier=ai_gateway,
                ai_assist_enabled=True,
            )
        cls_res = err_res["evidence"]["classifier_result"]
        self.assertEqual(cls_res["ai_assist_error"], "AI_CLASSIFIER_ERROR")
        self.assertEqual(
            cls_res["candidates"][-1]["ai_assist"]["reason"],
            "AI_CLASSIFIER_ERROR",
        )
        from services.zalo_scan_evidence import (
            _resolve_ai_assist_budget_key,
            _try_claim_ai_assist_target_slot,
        )

        self.assertEqual(
            _resolve_ai_assist_budget_key(
                "MindX - VT-C4K-GB41",
                target=None,
                scan_target={"target_key": "2026-W37|VT-C4K-GB41|4|2026-09-11"},
            ),
            "2026-W37|VT-C4K-GB41|4|2026-09-11",
        )
        # Claim remaining slots and verify exhaustion on same target_key
        while await _try_claim_ai_assist_target_slot(
            299, "2026-W37|VT-C4K-GB41|4|2026-09-11"
        ):
            pass
        with tempfile.TemporaryDirectory() as root:
            exhausted_res = await scan_open_zalo_chat(
                FakePage(messages),
                job_id=299,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB41",
                actual_group_name="MindX - VT-C4K-GB41",
                person="Teacher",
                class_date_str="11/09/2026",
                scan_target=target,
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
                ai_report_classifier=ai_gateway,
                ai_assist_enabled=True,
            )
        self.assertTrue(
            exhausted_res["evidence"]["classifier_result"]["ai_assist_budget_exhausted"]
        )

        # Verify PENDING deadline_verification renders ⏳ in report_repository and email service
        pending_row = {
            "class_code": "VT-C4K-GB41",
            "teacher": "Teacher",
            "lesson": "4",
            "class_date": "11/09/2026",
            "zalo_status": "✅ Đã báo cáo – Minh chứng hợp lệ",
            "evidence": {"date_context": {"deadline_verification": "PENDING"}},
        }
        normalized = normalize_zalo_status_text(
            pending_row["zalo_status"], item=pending_row
        )
        self.assertTrue(normalized.startswith("⏳"))
        self.assertIn("chờ kiểm tra deadline BCNB", normalized)
        email_fields = _prepare_email_row_fields(1, pending_row)
        self.assertTrue(email_fields["zalo_status"].startswith("⏳"))

        pending_late_row = {
            **pending_row,
            "zalo_status": "🟡 Đã báo cáo (Nộp trễ)",
        }
        normalized_late = normalize_zalo_status_text(
            pending_late_row["zalo_status"], item=pending_late_row
        )
        self.assertTrue(normalized_late.startswith("⏳"))
        self.assertNotIn("🟡", normalized_late)
        self.assertIn("chờ kiểm tra deadline BCNB", normalized_late)
        late_email_fields = _prepare_email_row_fields(1, pending_late_row)
        self.assertTrue(late_email_fields["zalo_status"].startswith("⏳"))

    async def test_5_8_3_ai_assist_passes_verbatim_text_and_never_logs_raw_message(self):
        import json
        from unittest.mock import patch
        import services.zalo_scan_evidence as zse

        verbatim_report = (
            "Dạ cô gửi ba mẹ tình hình học tập của các con Nguyễn Văn An và Trần Thị Bình hôm nay ạ: "
            "Cả lớp đã lắp ráp hoàn chỉnh cảm biến siêu âm và lập trình xe tránh vật cản rất tốt."
        )
        ai_gateway = AsyncMock()
        ai_gateway.evaluate_ambiguous_message = AsyncMock(
            return_value={
                "is_likely_report": True,
                "confidence": 0.91,
                "reason": "Nội dung tổng kết buổi học",
            }
        )
        target = {
            "class_code": "VT-C4K-GB42",
            "class_date": "11/09/2026",
            "class_date_iso": "2026-09-11",
            "lesson": "4",
            "week": 37,
            "year": 2026,
            "zalo_deadline": "12:00 13/09/2026",
        }
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(zse, "log_message") as mock_log,
        ):
            res = await scan_open_zalo_chat(
                FakePage([FakeMessage(verbatim_report, "11/09/2026")]),
                job_id=583,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-GB42",
                actual_group_name="MindX - VT-C4K-GB42",
                person="Teacher",
                class_date_str="11/09/2026",
                scan_target=target,
                today=datetime(2026, 9, 17),
                ensure_not_cancelled=AsyncMock(),
                ai_report_classifier=ai_gateway,
                ai_assist_enabled=True,
            )
        ai_gateway.evaluate_ambiguous_message.assert_awaited_once()
        passed_text = ai_gateway.evaluate_ambiguous_message.await_args.args[0]
        # D5: Verbatim text is passed to Gemini without redaction
        self.assertEqual(passed_text, verbatim_report)
        self.assertIn("Nguyễn Văn An", passed_text)
        # Raw message text must NEVER appear in application logs or scan result JSON
        for call in mock_log.call_args_list:
            serialized_call = json.dumps(
                {"args": call.args, "kwargs": call.kwargs},
                ensure_ascii=False,
                default=str,
            )
            self.assertNotIn("Nguyễn Văn An", serialized_call)
            self.assertNotIn(verbatim_report, serialized_call)
        serialized_res = json.dumps(res, ensure_ascii=False, default=str)
        self.assertNotIn("Nguyễn Văn An", serialized_res)

    async def test_5_8_4_same_day_missing_or_wrong_date_e2e_and_d1_late_regression(self):
        from services.report_status import (
            CATEGORY_DONE,
            CATEGORY_LATE,
            CATEGORY_REVIEW,
            ISSUE_CODE_ZALO_REPORT_NO_DATE,
            ISSUE_CODE_ZALO_REPORT_WRONG_DATE,
            SEVERITY_NOTE,
            categorize_row,
            is_failed,
            issue_code,
            issue_severity,
            issue_text,
        )
        from services.zalo_bcnb_service import _apply_bcnb_deadline_state

        target = {
            "class_code": "VT-C4K-PTB16",
            "class_date": "25/10/2026",
            "class_date_iso": "2026-10-25",
            "lesson": "5",
            "week": 43,
            "year": 2026,
            "zalo_deadline": "20:00 27/10/2026",
        }
        with tempfile.TemporaryDirectory() as root:
            # 1. Same day report without date or lesson number -> SUBMITTED_VERIFIED + NOTE issue
            no_date_msg = (
                "Kính gửi quý phụ huynh lớp VT-C4K-PTB16. "
                "Thầy xin gửi báo cáo tổng kết buổi học hôm nay của lớp. "
                "NỘI DUNG BÀI HỌC: Làm quen cấu trúc danh sách List và vòng lặp trong Python. "
                "NHẬN XÉT TÌNH HÌNH LỚP: Các con đi học đầy đủ, chú ý nghe giảng và hoàn thành bài thực hành tốt."
            )
            res_no_date = await scan_open_zalo_chat(
                FakePage([FakeMessage(no_date_msg, "25/10/2026")]),
                job_id=5841,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-PTB16",
                actual_group_name="MindX - VT-C4K-PTB16",
                person="Teacher",
                class_date_str="25/10/2026",
                scan_target=target,
                today=datetime(2026, 10, 26),
                ensure_not_cancelled=AsyncMock(),
            )
            self.assertEqual(res_no_date["evidence_state"], SUBMITTED_VERIFIED)
            self.assertTrue(res_no_date["submitted_for_bcnb"])
            self.assertTrue(res_no_date.get("zalo_content_issues"))
            first_issue = res_no_date["zalo_content_issues"][0]
            self.assertEqual(issue_code(first_issue), ISSUE_CODE_ZALO_REPORT_NO_DATE)
            self.assertEqual(issue_severity(first_issue), SEVERITY_NOTE)
            self.assertEqual(issue_text(first_issue), "[Zalo] Báo cáo không ghi ngày/buổi")
            self.assertEqual(categorize_row(res_no_date), CATEGORY_DONE)
            self.assertFalse(is_failed(res_no_date))

            # 2. Same day report with wrong date (28/10) and no conflicting lesson -> SUBMITTED_VERIFIED + NOTE issue
            wrong_date_msg = (
                "Kính gửi quý phụ huynh lớp VT-C4K-PTB16. "
                "Thầy xin gửi báo cáo buổi học số 5 ngày 28/10/2026 của lớp. "
                "NỘI DUNG BÀI HỌC: Làm quen cấu trúc danh sách List và vòng lặp trong Python. "
                "NHẬN XÉT TÌNH HÌNH LỚP: Các con đi học đầy đủ, chú ý nghe giảng và hoàn thành bài thực hành tốt."
            )
            res_wrong_date = await scan_open_zalo_chat(
                FakePage([FakeMessage(wrong_date_msg, "25/10/2026")]),
                job_id=5842,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-PTB16",
                actual_group_name="MindX - VT-C4K-PTB16",
                person="Teacher",
                class_date_str="25/10/2026",
                scan_target=target,
                today=datetime(2026, 10, 26),
                ensure_not_cancelled=AsyncMock(),
            )
            self.assertEqual(res_wrong_date["evidence_state"], SUBMITTED_VERIFIED)
            self.assertTrue(res_wrong_date["submitted_for_bcnb"])
            self.assertTrue(res_wrong_date.get("zalo_content_issues"))
            wrong_issue = res_wrong_date["zalo_content_issues"][0]
            self.assertEqual(issue_code(wrong_issue), ISSUE_CODE_ZALO_REPORT_WRONG_DATE)
            self.assertEqual(issue_severity(wrong_issue), SEVERITY_NOTE)
            self.assertEqual(issue_text(wrong_issue), "[Zalo] Báo cáo ghi sai ngày (28/10)")
            self.assertEqual(categorize_row(res_wrong_date), CATEGORY_DONE)
            self.assertFalse(is_failed(res_wrong_date))

            # 3. Next day report without date or lesson -> stays INSUFFICIENT_EVIDENCE / REVIEW
            res_next_day_no_date = await scan_open_zalo_chat(
                FakePage([FakeMessage(no_date_msg, "26/10/2026")]),
                job_id=5843,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-PTB16",
                actual_group_name="MindX - VT-C4K-PTB16",
                person="Teacher",
                class_date_str="25/10/2026",
                scan_target=target,
                today=datetime(2026, 10, 26),
                ensure_not_cancelled=AsyncMock(),
            )
            self.assertEqual(res_next_day_no_date["evidence_state"], INSUFFICIENT_EVIDENCE)
            self.assertFalse(res_next_day_no_date["submitted_for_bcnb"])
            self.assertEqual(categorize_row(res_next_day_no_date), CATEGORY_REVIEW)

            # 4. Regression D1: Late report sent on 28/10 with matching date (25/10) and lesson (Buổi 5)
            late_valid_msg = (
                "Kính gửi quý phụ huynh lớp VT-C4K-PTB16. "
                "Thầy xin gửi báo cáo buổi học số 5 ngày 25/10/2026 của lớp. "
                "NỘI DUNG BÀI HỌC: Làm quen cấu trúc danh sách List và vòng lặp trong Python. "
                "NHẬN XÉT TÌNH HÌNH LỚP: Các con đi học đầy đủ, chú ý nghe giảng và hoàn thành bài thực hành tốt."
            )
            res_late = await scan_open_zalo_chat(
                FakePage([FakeMessage(late_valid_msg, "28/10/2026")]),
                job_id=5844,
                screenshots_dir=root,
                group_name="MindX - VT-C4K-PTB16",
                actual_group_name="MindX - VT-C4K-PTB16",
                person="Teacher",
                class_date_str="25/10/2026",
                scan_target=target,
                today=datetime(2026, 10, 29),
                ensure_not_cancelled=AsyncMock(),
            )
            _apply_bcnb_deadline_state(
                res_late,
                {"ngay_hoc_iso": "2026-10-25", "zalo_deadline": "27/10/2026"},
                today=datetime(2026, 10, 29),
            )
            self.assertEqual(res_late["evidence_state"], SUBMITTED_LATE_VERIFIED)
            self.assertTrue(res_late["submitted_for_bcnb"])
            self.assertTrue(res_late["status"].startswith("🟡"))
            self.assertEqual(categorize_row(res_late), CATEGORY_LATE)
            self.assertFalse(is_failed(res_late))


if __name__ == "__main__":
    unittest.main()
