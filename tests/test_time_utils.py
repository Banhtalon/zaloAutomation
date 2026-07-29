import unittest
from datetime import datetime, timezone

from core.time_utils import BUSINESS_TIMEZONE_NAME, business_day_start, now_business


class BusinessTimeTests(unittest.TestCase):
    def test_business_timezone_is_iana_ho_chi_minh(self):
        current = now_business()
        self.assertEqual(BUSINESS_TIMEZONE_NAME, "Asia/Ho_Chi_Minh")
        self.assertEqual(getattr(current.tzinfo, "key", None), BUSINESS_TIMEZONE_NAME)
        self.assertEqual(current.utcoffset().total_seconds(), 7 * 60 * 60)

    def test_business_day_start_converts_before_truncating(self):
        utc_value = datetime(2026, 7, 29, 20, 30, tzinfo=timezone.utc)
        local_start = business_day_start(utc_value)
        self.assertEqual(local_start.isoformat(), "2026-07-30T00:00:00+07:00")


if __name__ == "__main__":
    unittest.main()
