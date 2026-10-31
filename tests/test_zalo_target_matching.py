from datetime import datetime
import unittest

from core.time_utils import BUSINESS_TIMEZONE
from services.zalo_target_matching import (
    TARGET_BEFORE_CLASS_DATE,
    TARGET_DATE_TYPO_CORRECTED,
    TARGET_EXACT,
    TARGET_SAME_DAY_NO_DATE,
    TARGET_SAME_DAY_WRONG_DATE,
    TARGET_SESSION_AMBIGUOUS,
    parse_bcnb_scan_target,
    resolve_report_target,
)


class ZaloTargetMatchingTests(unittest.TestCase):
    def _target(self):
        target = parse_bcnb_scan_target(
            {
                "week": 43,
                "year": 2026,
                "class_code": "VT-C4K-PTB16",
                "lesson": "5",
                "class_date": "25/10/2026",
                "zalo_deadline": "20:00 27/10/2026",
            },
            default_year=2026,
            timezone_info=BUSINESS_TIMEZONE,
        )
        assert target is not None
        return target

    def test_exact_date_and_lesson_matches_without_typo_note(self) -> None:
        target = self._target()
        match = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson=5,
            content_lesson=5,
        )
        self.assertTrue(match.matches)
        self.assertEqual(match.decision, TARGET_EXACT)
        self.assertFalse(match.corrected_teacher_date)
        self.assertIsNone(match.note)

    def test_sent_before_class_date_is_rejected_with_before_class_date_decision(self) -> None:
        target = self._target()
        match = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 24, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson=5,
            content_lesson=5,
        )
        self.assertFalse(match.matches)
        self.assertEqual(match.decision, TARGET_BEFORE_CLASS_DATE)
        self.assertIn("trước ngày học target", match.note or "")

    def test_exact_lesson_repairs_wrong_date_and_records_typo_note(self) -> None:
        target = self._target()
        match = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 26, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=datetime(2026, 10, 18, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson=5,
            content_lesson=None,
        )
        self.assertTrue(match.matches)
        self.assertEqual(match.decision, TARGET_DATE_TYPO_CORRECTED)
        self.assertTrue(match.corrected_teacher_date)
        self.assertIn("ghi sai ngày 18/10", match.note or "")

    def test_ambiguous_candidate_without_corroborating_lesson_does_not_match(self) -> None:
        target = self._target()
        match = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 26, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=datetime(2026, 10, 18, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson=4,
            content_lesson=None,
        )
        self.assertFalse(match.matches)
        self.assertEqual(match.decision, TARGET_SESSION_AMBIGUOUS)

    def test_5_8_4_same_day_missing_or_wrong_date_fallback_and_review_branches(self) -> None:
        target = self._target()

        # 1. Same day (sent_day == target_day), no date and no lesson -> matches with TARGET_SAME_DAY_NO_DATE
        m_no_date = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=None,
            claimed_lesson=None,
            content_lesson=None,
        )
        self.assertTrue(m_no_date.matches)
        self.assertEqual(m_no_date.decision, TARGET_SAME_DAY_NO_DATE)
        self.assertEqual(m_no_date.note, "[Zalo] Báo cáo không ghi ngày/buổi")

        # 2. Same day, wrong date (18/10) without conflicting lesson -> matches with TARGET_SAME_DAY_WRONG_DATE
        m_wrong_date = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=datetime(2026, 10, 18, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson=None,
            content_lesson=None,
        )
        self.assertTrue(m_wrong_date.matches)
        self.assertEqual(m_wrong_date.decision, TARGET_SAME_DAY_WRONG_DATE)
        self.assertTrue(m_wrong_date.corrected_teacher_date)
        self.assertEqual(m_wrong_date.note, "[Zalo] Báo cáo ghi sai ngày (18/10)")

        # 3. Same day, future wrong date (28/10) without conflicting lesson -> matches with TARGET_SAME_DAY_WRONG_DATE
        m_future_wrong = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=datetime(2026, 10, 28, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson=None,
            content_lesson=None,
        )
        self.assertTrue(m_future_wrong.matches)
        self.assertEqual(m_future_wrong.decision, TARGET_SAME_DAY_WRONG_DATE)
        self.assertEqual(m_future_wrong.note, "[Zalo] Báo cáo ghi sai ngày (28/10)")

        # 3b. Same day, wrong date (18/10) WITH matching lesson (Buổi 5) -> still TARGET_SAME_DAY_WRONG_DATE (NOTE, not FAIL)
        m_same_day_lesson_match_wrong_date = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=datetime(2026, 10, 18, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson=5,
            content_lesson=5,
        )
        self.assertTrue(m_same_day_lesson_match_wrong_date.matches)
        self.assertEqual(
            m_same_day_lesson_match_wrong_date.decision,
            TARGET_SAME_DAY_WRONG_DATE,
        )
        self.assertEqual(
            m_same_day_lesson_match_wrong_date.note,
            "[Zalo] Báo cáo ghi sai ngày (18/10)",
        )

        # 4. Unreadable ui_sent_at -> REVIEW (TARGET_SESSION_AMBIGUOUS)
        m_unreadable = resolve_report_target(
            target,
            ui_sent_at=None,
            claimed_lesson_date=None,
            claimed_lesson=None,
            content_lesson=None,
        )
        self.assertFalse(m_unreadable.matches)
        self.assertEqual(m_unreadable.decision, TARGET_SESSION_AMBIGUOUS)

        # 5. Sent next day without date/lesson -> REVIEW (TARGET_SESSION_AMBIGUOUS)
        m_next_day_no_date = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 26, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=None,
            claimed_lesson=None,
            content_lesson=None,
        )
        self.assertFalse(m_next_day_no_date.matches)
        self.assertEqual(m_next_day_no_date.decision, TARGET_SESSION_AMBIGUOUS)

        # 6. Same day but explicitly conflicting lesson number (Buổi 4 vs Buổi 5) -> REVIEW
        m_conflict_lesson = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=None,
            claimed_lesson=4,
            content_lesson=None,
        )
        self.assertFalse(m_conflict_lesson.matches)
        self.assertEqual(m_conflict_lesson.decision, TARGET_SESSION_AMBIGUOUS)

        # 7. Two BCNB targets on the same day for the same group (same_day_target_ambiguous=True) -> REVIEW
        m_ambig_same_day = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=None,
            claimed_lesson=None,
            content_lesson=None,
            same_day_target_ambiguous=True,
        )
        self.assertFalse(m_ambig_same_day.matches)
        self.assertEqual(m_ambig_same_day.decision, TARGET_SESSION_AMBIGUOUS)
        self.assertIn("nhiều buổi học BCNB trùng ngày", m_ambig_same_day.note or "")

        # 8. Regression D1: Sent after class date with matching date/lesson still matches
        m_late_valid = resolve_report_target(
            target,
            ui_sent_at=datetime(2026, 10, 28, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson_date=datetime(2026, 10, 25, tzinfo=BUSINESS_TIMEZONE),
            claimed_lesson=5,
            content_lesson=5,
        )
        self.assertTrue(m_late_valid.matches)
        self.assertEqual(m_late_valid.decision, TARGET_EXACT)


if __name__ == "__main__":
    unittest.main()
