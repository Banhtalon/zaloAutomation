from datetime import datetime, timedelta, timezone
import unittest

from services.class_lifecycle import (
    LifecycleStatus,
    eligible_for_archive,
    extract_progression_signature,
    parse_bcnb_class_code,
    status_after_bcnb_snapshot,
)


class ClassLifecycleTests(unittest.TestCase):
    def test_pt_and_game_levels_share_a_stable_cohort_key(self):
        pt_codes = [
            parse_bcnb_class_code("VT-C4K-PTB06"),
            parse_bcnb_class_code("VT-C4K-PTA06"),
            parse_bcnb_class_code("VT-C4K-PTI06"),
        ]
        game_codes = [
            parse_bcnb_class_code("VT-C4K-GB02"),
            parse_bcnb_class_code("VT-C4K-GA02"),
            parse_bcnb_class_code("VT-C4K-GI02"),
        ]

        self.assertTrue(all(item is not None for item in pt_codes + game_codes))
        self.assertEqual({item.cohort_key for item in pt_codes if item}, {"VT-C4K|PT|06"})
        self.assertEqual({item.cohort_key for item in game_codes if item}, {"VT-C4K|G|02"})
        self.assertEqual([item.level for item in pt_codes if item], ["B", "A", "I"])

    def test_short_group_names_expose_progression_without_inventing_a_center(self):
        self.assertEqual(
            extract_progression_signature("MindX - Lập trình Game - GB02 CN"),
            ("G", "B", "02"),
        )
        self.assertEqual(
            extract_progression_signature("MindX - Lập trình App -PTI09 CN"),
            ("PT", "I", "09"),
        )

    def test_absence_never_becomes_ended_based_on_elapsed_weeks(self):
        for weeks in (1, 2, 3, 8):
            with self.subTest(weeks=weeks):
                self.assertEqual(
                    status_after_bcnb_snapshot(
                        LifecycleStatus.ACTIVE,
                        observed_on_bcnb=False,
                        days_absent=weeks * 7,
                    ),
                    LifecycleStatus.PAUSED_OR_TRANSITIONING,
                )

    def test_reappearance_on_bcnb_reactivates_every_non_active_state(self):
        for status in (
            LifecycleStatus.PAUSED_OR_TRANSITIONING,
            LifecycleStatus.ENDED_CONFIRMED,
            LifecycleStatus.ARCHIVED,
        ):
            with self.subTest(status=status):
                self.assertEqual(
                    status_after_bcnb_snapshot(status, observed_on_bcnb=True),
                    LifecycleStatus.ACTIVE,
                )

    def test_archive_clock_starts_at_confirmed_end_not_first_absence(self):
        now = datetime(2026, 7, 16, tzinfo=timezone.utc)
        old_date = now - timedelta(days=30)
        confirmed_13_days_ago = now - timedelta(days=13)
        confirmed_14_days_ago = now - timedelta(days=14)

        self.assertFalse(
            eligible_for_archive(
                LifecycleStatus.PAUSED_OR_TRANSITIONING,
                ended_at=old_date,
                now=now,
            )
        )
        self.assertFalse(
            eligible_for_archive(
                LifecycleStatus.ENDED_CONFIRMED,
                ended_at=confirmed_13_days_ago,
                now=now,
            )
        )
        self.assertTrue(
            eligible_for_archive(
                LifecycleStatus.ENDED_CONFIRMED,
                ended_at=confirmed_14_days_ago,
                now=now,
            )
        )


if __name__ == "__main__":
    unittest.main()
