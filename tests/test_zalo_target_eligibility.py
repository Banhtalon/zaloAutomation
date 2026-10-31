from datetime import datetime
import unittest

from core.time_utils import BUSINESS_TIMEZONE
from services.zalo_target_eligibility import (
    DEFERRED_SAME_DAY_BEFORE_CLASS_END,
    DEFERRED_SAME_DAY_TIME_UNKNOWN,
    READY_CURRENT_DAY_AFTER_CLASS_END,
    READY_PREVIOUS_WEEK,
    evaluate_scan_target,
    parse_group_end_time,
    split_scan_targets_for_execution,
    split_scan_targets_with_deduped,
)


class ZaloTargetEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.latest_week = {"week": 33, "year": 2026}
        self.rob4i01_w32 = {
            "group_name": "MindX -LEGO-VT-KIND-ROB4I01 T6 18H-19H30",
            "class_code": "VT-KIND-ROB4I01",
            "lesson": "1",
            "class_date": "07/08/2026",
            "class_date_iso": "2026-08-07",
            "week": 32,
            "year": 2026,
        }
        self.rob4i01_w33 = {
            **self.rob4i01_w32,
            "lesson": "2",
            "class_date": "14/08/2026",
            "class_date_iso": "2026-08-14",
            "week": 33,
        }

    def test_parses_group_end_time_variants(self):
        self.assertEqual(parse_group_end_time("T6 18H-19H30").isoformat(), "19:30:00")
        self.assertEqual(parse_group_end_time("CN (14H-16H)").isoformat(), "16:00:00")
        self.assertEqual(parse_group_end_time("T7 18:00 - 20:00").isoformat(), "20:00:00")
        self.assertIsNone(parse_group_end_time("MindX - lớp chưa có giờ"))

    def test_same_day_target_is_deferred_before_class_end(self):
        eligibility = evaluate_scan_target(
            self.rob4i01_w33,
            latest_week=self.latest_week,
            now=datetime(2026, 8, 14, 16, 5, tzinfo=BUSINESS_TIMEZONE),
        )

        self.assertFalse(eligibility.eligible)
        self.assertEqual(eligibility.reason, DEFERRED_SAME_DAY_BEFORE_CLASS_END)
        self.assertEqual(eligibility.eligible_after, "2026-08-14T19:30+07:00")

    def test_same_day_target_becomes_ready_at_class_end(self):
        eligibility = evaluate_scan_target(
            self.rob4i01_w33,
            latest_week=self.latest_week,
            now=datetime(2026, 8, 14, 19, 30, tzinfo=BUSINESS_TIMEZONE),
        )

        self.assertTrue(eligibility.eligible)
        self.assertEqual(eligibility.reason, READY_CURRENT_DAY_AFTER_CLASS_END)

    def test_previous_week_target_remains_ready(self):
        eligibility = evaluate_scan_target(
            self.rob4i01_w32,
            latest_week=self.latest_week,
            now=datetime(2026, 8, 14, 16, 5, tzinfo=BUSINESS_TIMEZONE),
        )

        self.assertTrue(eligibility.eligible)
        self.assertEqual(eligibility.reason, READY_PREVIOUS_WEEK)

    def test_split_keeps_w32_and_defers_current_day_w33(self):
        ready, deferred = split_scan_targets_for_execution(
            [self.rob4i01_w32, self.rob4i01_w33],
            latest_week=self.latest_week,
            now=datetime(2026, 8, 14, 16, 5, tzinfo=BUSINESS_TIMEZONE),
        )

        self.assertEqual([(item["week"], item["lesson"]) for item in ready], [(32, "1")])
        self.assertEqual([(item["week"], item["lesson"]) for item in deferred], [(33, "2")])
        self.assertEqual(deferred[0]["deferred_reason"], DEFERRED_SAME_DAY_BEFORE_CLASS_END)

    def test_multiple_time_ranges_fail_closed_to_time_unknown(self):
        self.assertIsNone(parse_group_end_time("T7 9H-11H & CN 14H-16H"))
        self.assertIsNone(parse_group_end_time("MindX - VT-C4K-SI25 T3 (18:00-19:30) / T7 (14:00-15:30)"))

        multi_time_target = {
            **self.rob4i01_w33,
            "group_name": "MindX - VT-KIND-ROB4I01 T7 9H-11H & CN 14H-16H",
        }
        ready, deferred, deduped = split_scan_targets_with_deduped(
            [multi_time_target, dict(multi_time_target)],
            latest_week=self.latest_week,
            now=datetime(2026, 8, 14, 12, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(ready, [])
        self.assertEqual(len(deferred), 1)
        self.assertEqual(deferred[0]["deferred_reason"], DEFERRED_SAME_DAY_TIME_UNKNOWN)
        self.assertEqual(deferred[0]["deferred_reason_detail"], "cần xác nhận giờ học")
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["reason"], "DUPLICATE_TARGET")
