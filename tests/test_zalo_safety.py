import unittest
from datetime import datetime

from services.zalo_safety import (
    _strip_trailing_next_lesson_sections,
    class_code_matches_group,
    extract_report_content_lesson_number,
    extract_report_lesson_number,
    infer_previous_lesson_date,
    open_chat_matches,
    report_delay_days,
)


class ZaloClassMatchingTests(unittest.TestCase):
    def test_complete_class_code_matches_with_separator_variants(self):
        self.assertTrue(
            class_code_matches_group("VT-C4K-SI24", "MindX - VT C4K SI24 - Teacher")
        )

    def test_short_or_prefix_match_is_rejected(self):
        self.assertFalse(class_code_matches_group("VT-JSB14", "MindX - VT-JSB140"))
        self.assertFalse(class_code_matches_group("VT-JSB14", "MindX - JSB14"))
        self.assertFalse(class_code_matches_group("JSB14", "MindX - JSB14"))

    def test_open_chat_requires_complete_group_identity(self):
        self.assertTrue(
            open_chat_matches(
                "MindX - VT-JSB14 - Teacher",
                "MindX | VT JSB14 | Teacher",
            )
        )
        self.assertFalse(
            open_chat_matches("MindX - VT-JSB14", "MindX - VT-JSB140")
        )
        self.assertFalse(open_chat_matches("MindX - VT-JSB14", ""))

    def test_nearby_class_codes_never_match(self):
        self.assertFalse(
            class_code_matches_group("VT-C4K-GB4", "MindX - VT-C4K-GB40")
        )
        self.assertFalse(
            open_chat_matches("MindX - VT-C4K-GB40", "MindX - VT-C4K-GB41")
        )
        self.assertFalse(
            open_chat_matches("MindX - VT-C4K-PTI08", "MindX - VT-C4K-PTI09")
        )

    def test_invalid_nonempty_class_date_fails_closed(self):
        with self.assertRaises(ValueError):
            report_delay_days(
                "31/02/2026",
                datetime(2026, 2, 10, 9, 0),
                default_year=2026,
            )

    def test_missing_class_date_is_the_only_legacy_fallback(self):
        self.assertIsNone(
            report_delay_days(
                "",
                datetime(2026, 2, 10, 9, 0),
                default_year=2026,
            )
        )
        self.assertEqual(
            report_delay_days(
                "08/02/2026",
                datetime(2026, 2, 10, 9, 0),
                default_year=2026,
            ),
            2,
        )

    def test_infer_previous_lesson_date_requires_a_recent_content_date(self):
        previous = infer_previous_lesson_date(
            [datetime(2025, 7, 26), datetime(2026, 7, 26), datetime(2026, 8, 2)],
            next_class_date=datetime(2026, 8, 2),
            message_date=datetime(2026, 7, 29),
        )
        self.assertEqual(previous, datetime(2026, 7, 26))
        self.assertIsNone(
            infer_previous_lesson_date(
                [datetime(2026, 7, 15)],
                next_class_date=datetime(2026, 8, 2),
                message_date=datetime(2026, 7, 29),
            )
        )

    def test_content_lesson_marker_is_separate_from_buoi_number(self):
        text = "Update sau buổi học số 5 - 26/07/2026. Bài số 6: GML."
        self.assertEqual(extract_report_content_lesson_number(text), 6)
        self.assertIsNone(extract_report_content_lesson_number("Bài tập về nhà số 6"))

    def test_gb42_hb_b13_next_lesson_section_does_not_leak_lesson_14(self):
        from services.lms_quality_service import evaluate_zalo_content_issues

        gb42_b13_report = (
            "📣 TỔNG KẾT BUỔI HỌC SỐ 13 - LỚP VT-C4K-GB42-HB (04/11/2026)\n"
            "1. Nội dung buổi học:\n"
            "- Hoàn thiện lập trình cơ chế tính điểm và màn hình chiến thắng cho dự án cuối khóa.\n"
            "2. Tình hình lớp học:\n"
            "- Các bạn tập trung hoàn thành sản phẩm tốt.\n"
            "📌 Buổi học kế tiếp:\n"
            "- Thời gian: 18h00 - 20h00 Thứ Bảy ngày 11/11/2026\n"
            "- Nội dung: Buổi 14 - Thuyết trình sản phẩm cuối khóa (Bài số 14 / Lesson 14)"
        )
        stripped = _strip_trailing_next_lesson_sections(gb42_b13_report)
        self.assertNotIn("Buổi 14", stripped)
        self.assertNotIn("Bài số 14", stripped)
        self.assertEqual(extract_report_lesson_number(gb42_b13_report), 13)
        self.assertIsNone(extract_report_content_lesson_number(gb42_b13_report))

        # Even if header had no explicit Buổi 13, trailing Buổi học kế tiếp: Buổi 14 must not leak
        no_header_num_report = (
            "📣 BÁO CÁO SAU BUỔI HỌC LỚP VT-C4K-GB42-HB (04/11/2026)\n"
            "- Nội dung: Hoàn thiện dự án cuối khóa.\n"
            "👉 Lịch học tiếp theo:\n"
            "- Thứ 3 tuần sau các con học Buổi 14 (Bài số 14) bảo vệ sản phẩm."
        )
        self.assertIsNone(extract_report_lesson_number(no_header_num_report))
        self.assertIsNone(extract_report_content_lesson_number(no_header_num_report))

        scan_item = {
            "evidence_state": "SUBMITTED_VERIFIED",
            "raw_content": gb42_b13_report,
            "scan_target": {
                "class_code": "VT-C4K-GB42-HB",
                "lesson": "13",
                "class_date_iso": "2026-11-04",
            },
            "evidence": {
                "date_context": {
                    "target_lesson": "13",
                    "reported_lesson": extract_report_lesson_number(gb42_b13_report),
                    "content_lesson": extract_report_content_lesson_number(gb42_b13_report),
                }
            },
        }
        self.assertEqual(evaluate_zalo_content_issues(scan_item), [])

        # Numbered + emoji homework header and inline next-lesson header must also be truncated
        numbered_emoji_hw = (
            "📣 BÁO CÁO SAU BUỔI HỌC SỐ 13 - LỚP VT-C4K-GB42-HB\n"
            "1. Nội dung: Hoàn thiện dự án cuối khóa.\n"
            "3. 📌 Bài tập về nhà: Làm bài 14 trên LMS\n"
            "IV) 🔹 Buổi học kế tiếp: Buổi 14"
        )
        self.assertEqual(extract_report_lesson_number(numbered_emoji_hw), 13)
        self.assertIsNone(extract_report_content_lesson_number(numbered_emoji_hw))

        inline_hw = "Nội dung: Học bài số 13. Bài tập về nhà: Chuẩn bị bài số 14 và xem trước Buổi 14."
        self.assertEqual(extract_report_content_lesson_number(inline_hw), 13)
        self.assertIsNone(extract_report_lesson_number(inline_hw))

        # Mid-message Bài tập về nhà section must NOT truncate subsequent valid report sections
        mid_message_hw = (
            "📣 Báo cáo buổi học lớp VT-C4K-GB42-HB (04/11/2026)\n"
            "📌 Bài tập về nhà: Làm bài số 14 và chuẩn bị Buổi 14\n"
            "📘 Nội dung buổi học: Bài số 13 - Hoàn thiện dự án cuối khóa\n"
            "💬 Nhận xét chung: Các con tập trung hoàn thành sản phẩm tốt."
        )
        self.assertEqual(extract_report_content_lesson_number(mid_message_hw), 13)
        self.assertIsNone(extract_report_lesson_number(mid_message_hw))

        # Inline Bài tập về nhà: entering multi-line bullets must skip subsequent bullet lines
        inline_hw_multiline_bullets = (
            "Nội dung: Học bài 13. Bài tập về nhà:\n"
            "- Chuẩn bị Buổi 14, Bài số 14\n"
            "- Ôn lại kiến thức chuẩn bị thuyết trình Buổi 14"
        )
        self.assertIsNone(extract_report_lesson_number(inline_hw_multiline_bullets))
        self.assertIsNone(extract_report_content_lesson_number(inline_hw_multiline_bullets))

        # Inline Bài tập về nhà followed by inline resumed section on the same line must resume cleanly
        inline_hw_with_inline_resume = (
            "Báo cáo buổi 13. Bài tập về nhà: Làm bài số 14, chuẩn bị Buổi 14. "
            "Nhận xét chung: Lớp học tốt, hoàn thành Bài số 13."
        )
        self.assertEqual(extract_report_lesson_number(inline_hw_with_inline_resume), 13)
        self.assertEqual(
            extract_report_content_lesson_number(inline_hw_with_inline_resume), 13
        )


if __name__ == "__main__":
    unittest.main()
