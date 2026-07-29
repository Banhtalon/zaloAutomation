import unittest

from services.zalo_discovery_matching import find_scan_result_matches


class ZaloScanMatchingTests(unittest.TestCase):
    def test_short_pti08_alias_resolves_to_unique_full_bcnb_code(self):
        result = {
            "group": "MindX - Lập trình App- PTI08 T7 (14H-16H)",
            "person": "Dương Anh Quang",
            "status": "❌ Chưa nộp (Hoặc quá 6 ngày)",
        }

        matches = find_scan_result_matches(
            "VT-C4K-PTI08",
            [result],
            [{"ma_lop": "VT-C4K-PTI08"}],
        )

        self.assertEqual(matches, [result])
        matches[0]["status"] = "✅ Miễn báo cáo (Buổi 14)"
        self.assertEqual(result["status"], "✅ Miễn báo cáo (Buổi 14)")

    def test_short_alias_fails_closed_when_two_bcnb_codes_share_it(self):
        result = {
            "group": "MindX - Lập trình App- PTI08 T7 (14H-16H)",
            "status": "❌ Chưa nộp (Hoặc quá 6 ngày)",
        }
        bcnb_data = [
            {"ma_lop": "VT-C4K-PTI08"},
            {"ma_lop": "HN-C4K-PTI08"},
        ]

        self.assertEqual(
            find_scan_result_matches("VT-C4K-PTI08", [result], bcnb_data),
            [],
        )
        self.assertEqual(
            find_scan_result_matches("HN-C4K-PTI08", [result], bcnb_data),
            [],
        )

    def test_full_code_match_is_preserved(self):
        result = {
            "group": "MindX - VT-C4K-PTI08 - Teacher",
            "status": "✅ Đã nộp (Đúng hạn)",
        }

        matches = find_scan_result_matches(
            "VT-C4K-PTI08",
            [result],
            [
                {"ma_lop": "VT-C4K-PTI08"},
                {"ma_lop": "HN-C4K-PTI08"},
            ],
        )

        self.assertEqual(matches, [result])


if __name__ == "__main__":
    unittest.main()
