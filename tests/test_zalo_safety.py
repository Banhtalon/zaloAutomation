import unittest
from datetime import datetime

from services.zalo_safety import (
    class_code_matches_group,
    extract_report_content_lesson_number,
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


if __name__ == "__main__":
    unittest.main()
