import unittest

from services.lms_plan import (
    LmsSafetyError,
    build_lms_plan,
    compute_plan_digest,
    compute_selection_digest,
    format_overdue_session_detail,
    merge_overdue_session_details,
    normalize_class_codes,
    normalize_lms_student_class_codes,
    normalize_overdue_details,
    validate_selected_classes_to_tick,
)


class LmsPlanSafetyTests(unittest.TestCase):
    def test_empty_ocr_is_rejected(self):
        with self.assertRaises(LmsSafetyError):
            normalize_class_codes([])
        self.assertEqual(normalize_class_codes([], allow_empty=True), [])

    def test_web_mode_allows_unmatched_and_empty_missing_classes(self):
        plan_with_unmatched = build_lms_plan(
            ["VT-JSB14", "VT-OTHER-99"],
            [
                {"class_code": "VT-JSB14", "checked": False},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            week=28,
            year=2026,
            allow_unmatched_missing=True,
            allow_empty_missing=True,
        )
        self.assertTrue(plan_with_unmatched["can_confirm"])
        self.assertEqual(plan_with_unmatched["unmatched_classes"], ["VT-OTHER-99"])
        self.assertEqual(plan_with_unmatched["classes_to_tick"], ["VT-JSB15"])
        self.assertEqual(plan_with_unmatched.get("source"), "lms_web")

        plan_with_empty = build_lms_plan(
            [],
            [{"class_code": "VT-JSB15", "checked": False}],
            week=28,
            year=2026,
            allow_unmatched_missing=True,
            allow_empty_missing=True,
        )
        self.assertTrue(plan_with_empty["can_confirm"])
        self.assertEqual(plan_with_empty["classes_to_tick"], ["VT-JSB15"])
        self.assertEqual(plan_with_empty.get("source"), "lms_web")

        plan_with_checked_missing = build_lms_plan(
            ["VT-ROB-PREA16"],
            [
                {"class_code": "VT-ROB-PREA16", "checked": True},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            week=43,
            year=2026,
            allow_unmatched_missing=True,
            allow_empty_missing=True,
            allow_checked_missing=True,
            overdue_details={"VT-ROB-PREA16": "9"},
        )
        self.assertTrue(plan_with_checked_missing["can_confirm"])
        self.assertEqual(
            plan_with_checked_missing["missing_but_checked_classes"],
            ["VT-ROB-PREA16"],
        )
        self.assertEqual(plan_with_checked_missing["blocking_issues"], [])
        self.assertEqual(plan_with_checked_missing["classes_to_tick"], ["VT-JSB15"])
        self.assertEqual(plan_with_checked_missing.get("source"), "lms_web")

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

    def test_overdue_details_formatting_and_digest_binding(self):
        self.assertEqual(format_overdue_session_detail(""), "")
        self.assertEqual(format_overdue_session_detail(None), "")
        self.assertEqual(format_overdue_session_detail(" , ; "), "")
        self.assertEqual(format_overdue_session_detail("3, 04; Buổi 5, 3"), "Buổi 3, Buổi 4, Buổi 5")
        self.assertEqual(format_overdue_session_detail("Session 2"), "Session 2")
        self.assertEqual(
            merge_overdue_session_details("Buổi 10", "1"),
            "Buổi 10, Buổi 1",
        )
        self.assertEqual(
            merge_overdue_session_details("Buổi 3, Buổi 4", "Buổi 3"),
            "Buổi 3, Buổi 4",
        )

        with self.assertRaises(LmsSafetyError):
            normalize_overdue_details(["not-a-dict"])

        normalized = normalize_overdue_details(
            {
                "vt-jsb14": "3, 4",
                "VT-JSB14": "3",
                "vt-c4k-sb24": "10",
                "VT-C4K-SB24": "1",
                " ": "1",
                "VT-IGNORED": "2",
                "VT-EMPTY": "   ",
            },
            allowed_codes={"VT-JSB14", "VT-C4K-SB24", "VT-EMPTY"},
        )
        self.assertEqual(
            normalized,
            {
                "VT-JSB14": "Buổi 3, Buổi 4",
                "VT-C4K-SB24": "Buổi 10, Buổi 1",
            },
        )

        plan_a = build_lms_plan(
            ["VT-JSB14"],
            [
                {"class_code": "VT-JSB14", "checked": False},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            week=28,
            year=2026,
            overdue_details={"VT-JSB14": "3"},
        )
        plan_b = build_lms_plan(
            ["VT-JSB14"],
            [
                {"class_code": "VT-JSB14", "checked": False},
                {"class_code": "VT-JSB15", "checked": False},
            ],
            week=28,
            year=2026,
            overdue_details={"VT-JSB14": "4"},
        )
        self.assertEqual(plan_a["overdue_details"], {"VT-JSB14": "Buổi 3"})
        self.assertNotEqual(plan_a["plan_digest"], plan_b["plan_digest"])
        self.assertNotEqual(
            compute_selection_digest(plan_a["plan_digest"], ["VT-JSB15"]),
            compute_selection_digest(plan_a["plan_digest"], ["VT-JSB14", "VT-JSB15"]),
        )

    def test_validate_selected_classes_to_tick_enforces_subset_fail_closed(self):
        allowed = ["VT-JSB15", "VT-C4K-SI24"]
        self.assertEqual(
            validate_selected_classes_to_tick(["vt-c4k-si24"], allowed),
            ["VT-C4K-SI24"],
        )
        for bad_input, expected_code in (
            ("not-a-list", "LMS_SELECTED_CLASSES_INVALID"),
            ([], "LMS_SELECTED_CLASSES_EMPTY"),
            (["bad code"], "LMS_SELECTED_CLASSES_INVALID"),
            (["VT-JSB15", "vt-jsb15"], "LMS_SELECTED_CLASSES_DUPLICATE"),
            (["VT-OTHER-99"], "LMS_SELECTED_CLASS_NOT_IN_PLAN"),
            ([f"VT-CLS{i:03d}" for i in range(205)], "LMS_SELECTED_CLASSES_INVALID"),
        ):
            with self.subTest(bad_input=bad_input):
                with self.assertRaises(LmsSafetyError) as caught:
                    validate_selected_classes_to_tick(bad_input, allowed)
                self.assertEqual(caught.exception.code, expected_code)

    def test_normalize_lms_student_class_codes_validates_inputs(self):
        self.assertEqual(
            normalize_lms_student_class_codes("vt-c4k-ga41, VT-C4K-GI35;\nVT-ROB-PA19"),
            ["VT-C4K-GA41", "VT-C4K-GI35", "VT-ROB-PA19"],
        )
        self.assertEqual(
            normalize_lms_student_class_codes(None, "vt-c4k-ga41"),
            ["VT-C4K-GA41"],
        )
        self.assertEqual(
            normalize_lms_student_class_codes(["vt-c4k-ga41", "VT-C4K-GI35"]),
            ["VT-C4K-GA41", "VT-C4K-GI35"],
        )
        for raw_codes, raw_single, expected_code in (
            (["VT-C4K-GA41"], "VT-C4K-GI35", "LMS_CLASS_CODES_INVALID"),
            (None, None, "LMS_CLASS_CODES_INVALID"),
            (123, None, "LMS_CLASS_CODES_INVALID"),
            ("", None, "LMS_CLASS_CODES_EMPTY"),
            ([], None, "LMS_CLASS_CODES_EMPTY"),
            (["VT-C4K-GA41, VT-C4K-GI35"], None, "LMS_CLASS_CODES_INVALID"),
            ([123], None, "LMS_CLASS_CODES_INVALID"),
            (["bad_code"], None, "LMS_CLASS_CODES_INVALID"),
            (["VT-C4K-GA41", "vt-c4k-ga41"], None, "LMS_CLASS_CODES_DUPLICATE"),
            ([f"VT-CLS{i:02d}" for i in range(21)], None, "LMS_CLASS_CODES_INVALID"),
        ):
            with self.subTest(raw_codes=raw_codes, raw_single=raw_single):
                with self.assertRaises(LmsSafetyError) as caught:
                    normalize_lms_student_class_codes(raw_codes, raw_single)
                self.assertEqual(caught.exception.code, expected_code)


if __name__ == "__main__":
    unittest.main()
