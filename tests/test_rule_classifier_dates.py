import unittest
from datetime import datetime

from classifiers.rule_classifier import (
    check_is_report_and_extract_date,
    parse_zalo_date_header,
)


class RuleClassifierDateTests(unittest.TestCase):
    def test_unknown_or_invalid_ui_date_fails_closed(self):
        today = datetime(2026, 7, 15)
        self.assertIsNone(parse_zalo_date_header("Thứ tư", today))
        self.assertIsNone(parse_zalo_date_header("31/02/2026", today))

    def test_relative_dates_are_supported(self):
        today = datetime(2026, 7, 15)
        self.assertEqual(parse_zalo_date_header("Hôm nay", today), today)
        self.assertEqual(
            parse_zalo_date_header("Hôm qua", today),
            datetime(2026, 7, 14),
        )

    def test_yearless_date_at_new_year_uses_previous_year(self):
        self.assertEqual(
            parse_zalo_date_header("31/12", datetime(2026, 1, 2)),
            datetime(2025, 12, 31),
        )

    def test_report_without_a_date_keeps_date_unknown(self):
        is_report, report_date = check_is_report_and_extract_date(
            "Chào phụ huynh. Báo cáo buổi học: Nội dung và nhận xét của lớp.",
            "MindX - VT-JSB14",
            datetime(2026, 7, 15),
        )
        self.assertTrue(is_report)
        self.assertIsNone(report_date)


if __name__ == "__main__":
    unittest.main()
