import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.time_utils import (
    BUSINESS_TIMEZONE,
    BUSINESS_TIMEZONE_NAME,
    business_day_start,
    now_business,
    parse_timestamp,
    previous_iso_week,
    previous_business_week,
    utc_iso,
)


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

    def test_previous_week_is_stable_at_sunday_to_monday_boundary(self):
        sunday = datetime(2026, 8, 2, 23, 59, tzinfo=BUSINESS_TIMEZONE)
        monday = datetime(2026, 8, 3, 0, 1, tzinfo=BUSINESS_TIMEZONE)
        self.assertEqual(previous_business_week(sunday), (30, 2026))
        self.assertEqual(previous_business_week(monday), (31, 2026))
        self.assertEqual(
            previous_business_week(sunday.astimezone(timezone.utc)),
            previous_business_week(sunday),
        )

    def test_previous_week_handles_year_end_and_leap_day(self):
        new_year = datetime(2024, 1, 1, 9, tzinfo=BUSINESS_TIMEZONE)
        leap_day = datetime(2024, 2, 29, 9, tzinfo=BUSINESS_TIMEZONE)
        self.assertEqual(previous_business_week(new_year), (52, 2023))
        self.assertEqual(previous_business_week(leap_day), (8, 2024))

    def test_previous_iso_week_uses_explicit_bcnb_period(self):
        self.assertEqual(previous_iso_week(33, 2026), (32, 2026))
        self.assertEqual(previous_iso_week(1, 2027), (53, 2026))

    def test_previous_iso_week_rejects_invalid_iso_pair(self):
        with self.assertRaises(ValueError):
            previous_iso_week(54, 2026)
        with self.assertRaises(ValueError):
            previous_iso_week(53, 2025)

    def test_legacy_naive_timestamp_is_explicitly_business_local(self):
        parsed = parse_timestamp("2026-07-30T00:00:00")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.isoformat(), "2026-07-29T17:00:00+00:00")

    def test_utc_iso_is_host_timezone_independent(self):
        value = datetime(2026, 7, 29, 20, 30, tzinfo=timezone.utc)
        self.assertEqual(utc_iso(value), "2026-07-29T20:30:00+00:00")
        self.assertEqual(getattr(ZoneInfo(BUSINESS_TIMEZONE_NAME), "key", None), "Asia/Ho_Chi_Minh")


if __name__ == "__main__":
    unittest.main()
