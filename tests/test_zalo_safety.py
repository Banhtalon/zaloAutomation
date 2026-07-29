import unittest
from datetime import datetime

from services.zalo_safety import (
    class_code_matches_group,
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


if __name__ == "__main__":
    unittest.main()
