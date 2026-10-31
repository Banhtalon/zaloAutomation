from datetime import datetime, timedelta, timezone
import unittest

from services.class_lifecycle import (
    LifecycleStatus,
    cohort_key_pair_from_key,
    eligible_for_archive,
    extract_progression_signature,
    parse_bcnb_class_code,
    status_after_bcnb_snapshot,
    verify_cohort_key_for_class_code,
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
        confirmed_6_days_ago = now - timedelta(days=6)
        confirmed_7_days_ago = now - timedelta(days=7)

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
                ended_at=confirmed_6_days_ago,
                now=now,
            )
        )
        self.assertTrue(
            eligible_for_archive(
                LifecycleStatus.ENDED_CONFIRMED,
                ended_at=confirmed_7_days_ago,
                now=now,
            )
        )

    def test_5_8_1_table_driven_real_class_codes_and_progression_rules(self):
        cases = [
            ("VT-C4K-PTB16", "VT-C4K", "PT", "B", "16", "VT-C4K|PT|16", None),
            ("VT-C4K-GI35", "VT-C4K", "G", "I", "35", "VT-C4K|G|35", None),
            ("VT-C4K-SA27", "VT-C4K", "S", "A", "27", "VT-C4K|S|27", None),
            ("VT-JSB17", "VT", "JS", "B", "17", "VT|JS|17", "VT-J|S|17"),
            ("VT-JSI12-HB", "VT", "JS", "I", "12", "VT|JS|12", "VT-J|S|12"),
            ("VT-CSA04-HB", "VT", "CS", "A", "04", "VT|CS|04", "VT-C|S|04"),
            ("VT-CSI02", "VT", "CS", "I", "02", "VT|CS|02", "VT-C|S|02"),
            ("VT-ROB-PREA18", "VT-ROB", "PRE", "A", "18", "VT-ROB|PRE|18", None),
            ("VT-ROB-ARMB20", "VT-ROB", "ARM", "B", "20", "VT-ROB|ARM|20", None),
            ("VT-ROB-SEMIB07", "VT-ROB", "SEMI", "B", "07", "VT-ROB|SEMI|07", None),
            ("VT-KIND-ROB4I02", "VT-KIND", "ROB4", "I", "02", "VT-KIND|ROB4|02", None),
        ]
        for (
            raw_code,
            expected_center,
            expected_track,
            expected_level,
            expected_number,
            expected_cohort_key,
            expected_legacy_key,
        ) in cases:
            with self.subTest(raw_code=raw_code):
                parsed = parse_bcnb_class_code(raw_code)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.center_code, expected_center)
                self.assertEqual(parsed.track, expected_track)
                self.assertEqual(parsed.level, expected_level)
                self.assertEqual(parsed.cohort_number, expected_number)
                self.assertEqual(parsed.cohort_key, expected_cohort_key)
                self.assertEqual(parsed.legacy_cohort_key, expected_legacy_key)
                self.assertTrue(
                    verify_cohort_key_for_class_code(expected_cohort_key, raw_code)
                )
                if expected_legacy_key:
                    self.assertTrue(
                        verify_cohort_key_for_class_code(expected_legacy_key, raw_code)
                    )
                    self.assertEqual(
                        cohort_key_pair_from_key(expected_legacy_key),
                        (expected_cohort_key, expected_legacy_key),
                    )
                    self.assertEqual(
                        cohort_key_pair_from_key(expected_cohort_key),
                        (expected_cohort_key, expected_legacy_key),
                    )

        # CSA03 must NOT parse as track S
        csa03 = parse_bcnb_class_code("VT-CSA03")
        self.assertIsNotNone(csa03)
        assert csa03 is not None
        self.assertEqual(csa03.track, "CS")
        self.assertNotEqual(csa03.track, "S")
        self.assertEqual(csa03.cohort_key, "VT|CS|03")
        self.assertEqual(
            extract_progression_signature("MindX - VT-CSA03"),
            ("CS", "A", "03"),
        )

        # JSB17 -> JSA17 shares the same cohort
        jsb17 = parse_bcnb_class_code("VT-JSB17")
        jsa17 = parse_bcnb_class_code("VT-JSA17")
        self.assertIsNotNone(jsb17)
        self.assertIsNotNone(jsa17)
        assert jsb17 is not None and jsa17 is not None
        self.assertEqual(jsb17.cohort_key, jsa17.cohort_key)
        self.assertEqual(jsb17.cohort_key, "VT|JS|17")

        # PREI16 -> ARMB16 cross-track transition does NOT share cohort
        prei16 = parse_bcnb_class_code("VT-ROB-PREI16")
        armb16 = parse_bcnb_class_code("VT-ROB-ARMB16")
        self.assertIsNotNone(prei16)
        self.assertIsNotNone(armb16)
        assert prei16 is not None and armb16 is not None
        self.assertNotEqual(prei16.cohort_key, armb16.cohort_key)


if __name__ == "__main__":
    unittest.main()
