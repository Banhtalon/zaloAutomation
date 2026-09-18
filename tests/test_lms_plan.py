import unittest

from services.lms_plan import (
    LmsSafetyError,
    build_lms_plan,
    compute_plan_digest,
    normalize_class_codes,
)


class LmsPlanSafetyTests(unittest.TestCase):
    def test_empty_ocr_is_rejected(self):
        with self.assertRaises(LmsSafetyError):
            normalize_class_codes([])

    def test_codes_are_normalized_and_deduplicated(self):
        self.assertEqual(
            normalize_class_codes([" vt-jsb14 ", "VT-JSB14", "vt-c4k-si24"]),
            ["VT-JSB14", "VT-C4K-SI24"],
        )

    def test_invalid_shapes_and_codes_are_rejected(self):
        for value in (None, {}, [123], ["not a class"]):
            with self.subTest(value=value):
                with self.assertRaises(LmsSafetyError):
                    normalize_class_codes(value)

    def test_matching_is_exact_not_substring_based(self):
        plan = build_lms_plan(
            ["VT-JSB14"],
            [
                {"class_code": "VT-JSB14", "checked": False},
                {"class_code": "VT-JSB140", "checked": False},
            ],
            week=28,
            year=2026,
        )
        self.assertEqual(plan["missing_classes"], ["VT-JSB14"])
        self.assertEqual(plan["classes_to_tick"], ["VT-JSB140"])
        self.assertTrue(plan["can_confirm"])

    def test_unknown_missing_class_blocks_confirmation(self):
        plan = build_lms_plan(
            ["VT-JSB14"],
            [{"class_code": "VT-JSB140", "checked": False}],
            week=28,
            year=2026,
        )
        self.assertFalse(plan["can_confirm"])
        self.assertEqual(plan["unmatched_classes"], ["VT-JSB14"])

    def test_checked_missing_class_blocks_confirmation(self):
        plan = build_lms_plan(
            ["VT-JSB14"],
            [
                {"class_code": "VT-JSB14", "checked": True},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            week=28,
            year=2026,
        )
        self.assertFalse(plan["can_confirm"])
        self.assertEqual(plan["missing_but_checked_classes"], ["VT-JSB14"])

    def test_digest_changes_when_snapshot_changes(self):
        base = build_lms_plan(
            ["VT-JSB14"],
            [
                {"class_code": "VT-JSB14", "checked": False},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            week=28,
            year=2026,
        )
        changed = dict(base)
        changed["week"] = 29
        self.assertNotEqual(compute_plan_digest(base), compute_plan_digest(changed))

    def test_digest_binds_execution_mode(self):
        dry_run = build_lms_plan(
            ["VT-JSB14"],
            [
                {"class_code": "VT-JSB14", "checked": False},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            week=28,
            year=2026,
            dry_run=True,
        )
        production = dict(dry_run)
        production["dry_run"] = False
        self.assertNotEqual(
            compute_plan_digest(dry_run),
            compute_plan_digest(production),
        )

    def test_digest_binds_plan_version_target_and_action_set(self):
        plan = build_lms_plan(
            ["VT-JSB14"],
            [
                {"class_code": "VT-JSB14", "checked": False},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            week=28,
            year=2026,
        )
        for field, value in (
            ("plan_version", plan["plan_version"] + 1),
            ("target", "OTHER_SYSTEM"),
            ("action_set", ["OTHER_ACTION"]),
        ):
            changed = dict(plan)
            changed[field] = value
            with self.subTest(field=field):
                self.assertNotEqual(
                    compute_plan_digest(plan),
                    compute_plan_digest(changed),
                )


if __name__ == "__main__":
    unittest.main()
