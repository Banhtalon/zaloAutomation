"""Synthetic session evidence: attendance determines who owes comments and SPCK."""

import unittest

from services.lms_quality_service import (
    evaluate_lms_slot_quality,
    evaluate_lms_spck_quality,
    evaluate_lms_student_comments,
    inspect_and_attach_quality_issues,
)
from services.report_status import (
    ISSUE_CODE_LMS_MANUAL_CHECK,
    ISSUE_CODE_LMS_SPCK_MISSING,
    SEVERITY_FAIL,
    SEVERITY_NOTE,
    issue_code,
    issue_severity,
    issue_text,
    make_structured_issue,
)
from services.bcnb_safety import merge_checker_issue_note
from services.zalo_bcnb_service import _collect_row_verifiable_auto_issues


GOOD_COMMENT = (
    "Con nắm vững kiến thức vòng lặp và hoàn thành bài thực hành, "
    "tuy nhiên đôi lúc con còn nhầm điều kiện dừng; "
    "về nhà con nên luyện tập thêm bài tập vòng lặp và kiểm tra lại kết quả."
)


def slot(rows, **extra):
    return {
        "summary": "Lập trình vòng lặp và kiểm tra điều kiện dừng",
        "studentAttendance": rows,
        **extra,
    }


class LmsAttendanceQualityTests(unittest.TestCase):
    def test_ptb_csb_exemptions_keep_comment_requirements_and_other_courses(self):
        rows = [{"studentKey": "stu_1", "status": "ATTENDED", "comment": ""}]
        for code in ("PTB", "CSB", "VT-C4K-PTB15", "VT-CSB01-HB", "onl_csb03", "1:1 PTB02"):
            for lesson in (12, 13, 14):
                with self.subTest(code=code, lesson=lesson):
                    for data in (
                        None,
                        slot(rows),
                        slot(rows, studentWorks=[], studentWorksFetchError=True),
                    ):
                        result = evaluate_lms_slot_quality(data, class_code=code, lesson=lesson)
                        self.assertEqual(result["lms_spck"]["status"], "NOT_REQUIRED")
                        self.assertFalse(
                            any("SPCK" in issue_text(item) for item in result["issues"])
                        )
                    result = evaluate_lms_slot_quality(slot(rows), class_code=code, lesson=lesson)
                    self.assertEqual(
                        result["lms_comments"]["status"], "EXEMPT" if lesson == 14 else "MISSING"
                    )
        for code in (
            "VT-PTA15",
            "VT-PTI01",
            "VT-CSA01",
            "VT-CSI01",
            "VT-GA38",
            "VT-XPTB01",
            "VT-CSBASIC01",
        ):
            for lesson in (12, 13, 14):
                with self.subTest(code=code, lesson=lesson):
                    result = evaluate_lms_spck_quality(
                        slot(rows, studentWorks=[]), class_code=code, lesson=lesson
                    )
                    self.assertEqual(result["status"], "MISSING")

    def test_attendees_only_and_absent_with_notice_exemption(self):
        rows = [
            {"studentKey": "stu_1", "status": "LATE_ARRIVED", "comment": GOOD_COMMENT},
            {"studentKey": "stu_2", "status": "ABSENT_WITH_NOTICE", "comment": ""},
            {"studentKey": "stu_3", "status": "ABSENT", "comment": ""},
        ]
        result = evaluate_lms_slot_quality(
            slot(
                rows,
                studentWorks=[
                    {
                        "studentKey": "stu_1",
                        "title": "Dự án lập trình",
                        "hasStudentInfoInTitle": True,
                    },
                    {"studentKey": "stu_2", "title": "Bài của HS vắng"},
                ],
            ),
            class_code="SYNTH-JSB01",
            lesson=12,
        )
        self.assertEqual(
            result["lms_attendance"],
            {
                "status": "VERIFIED",
                "attended_count": 1,
                "absent_count": 2,
                "unverified_count": 0,
                "exempt_count": 2,
                "total_count": 3,
            },
        )
        self.assertEqual(result["lms_comments"]["status"], "COMPLETE")
        self.assertEqual(result["lms_comments"]["submitted_count"], 1)
        self.assertEqual(result["lms_spck"]["submitted_count"], 1)
        self.assertEqual(result["lms_spck"]["status"], "COMPLETE")
        self.assertFalse(
            any(issue_code(item) == ISSUE_CODE_LMS_SPCK_MISSING for item in result["issues"])
        )

    def test_all_absent_is_explicit_exemption_even_when_no_works_fetched(self):
        rows = [
            {"studentKey": "stu_1", "status": "ABSENT_WITH_NOTICE"},
            {"studentKey": "stu_2", "status": "ABSENT"},
        ]
        for lesson in (3, 12, 13, 14):
            with self.subTest(lesson=lesson):
                result = evaluate_lms_slot_quality(
                    slot(rows), class_code="SYNTH-JSB01", lesson=lesson
                )
                self.assertEqual(result["lms_attendance"]["status"], "EXEMPT")
                self.assertEqual(result["lms_comments"]["status"], "EXEMPT")
                self.assertEqual(result["lms_comments"]["exempt_count"], 2)
                self.assertEqual(
                    result["lms_spck"]["status"], "EXEMPT" if lesson >= 12 else "NOT_REQUIRED"
                )
                self.assertEqual(result["issues"], [])

    def test_uncertain_attendance_never_claims_missing_or_complete(self):
        cases = [
            None,
            [],
            [{"studentKey": "stu_1", "status": "UNKNOWN"}],
            [{"studentKey": "stu_1"}],
            [None],
            [
                {"studentKey": "stu_1", "status": "ATTENDED"},
                {"studentKey": "stu_1", "status": "ABSENT"},
            ],
            [
                {"studentKey": "stu_1", "status": "ATTENDED"},
                {"studentKey": "stu_2", "status": "ABSENT_WITHOUT_NOTICE"},
            ],
        ]
        for rows in cases:
            for lesson in (12, 14):
                with self.subTest(rows=rows, lesson=lesson):
                    result = evaluate_lms_slot_quality(
                        slot(rows, studentWorks=[]), class_code="SYNTH-JSB01", lesson=lesson
                    )
                    self.assertEqual(result["lms_attendance"]["status"], "UNVERIFIED")
                    self.assertEqual(result["lms_spck"]["status"], "UNVERIFIED")
                    self.assertEqual(
                        result["lms_comments"]["status"], "EXEMPT" if lesson == 14 else "UNVERIFIED"
                    )
                    self.assertEqual(result["lms_spck"]["missing_count"], 0)
                    self.assertTrue(result["issues"])
                    self.assertTrue(
                        all(
                            issue_code(item) == ISSUE_CODE_LMS_MANUAL_CHECK
                            for item in result["issues"]
                        )
                    )

    def test_truncated_or_partial_attendance_is_manual_before_conclusions(self):
        rows = [{"studentKey": "stu_1", "status": "ATTENDED", "comment": ""}]
        for flag in (
            "truncated",
            "studentAttendanceTruncated",
            "studentAttendanceFetchError",
            "attendanceAmbiguous",
        ):
            result = evaluate_lms_slot_quality(
                slot(rows, studentWorks=[], **{flag: True}), class_code="SYNTH-JSB01", lesson=12
            )
            self.assertEqual(result["lms_comments"]["status"], "UNVERIFIED")
            self.assertEqual(result["lms_spck"]["status"], "UNVERIFIED")
            self.assertFalse(
                any(issue_severity(item) == SEVERITY_FAIL for item in result["issues"])
            )
        rows = [{"studentKey": f"stu_{i}", "status": "ATTENDED"} for i in range(51)]
        result = evaluate_lms_spck_quality(slot(rows, studentWorks=[]), lesson=12)
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertIn("vượt giới hạn", issue_text(result["issues"][0]))

    def test_spck_owner_and_source_must_be_verified_even_when_zero_works(self):
        for works in ([], None, [None]):
            result = evaluate_lms_spck_quality(
                slot([{"status": "ATTENDED"}], studentWorks=works), lesson=12
            )
            self.assertEqual(result["status"], "UNVERIFIED")
            self.assertEqual(result["missing_submission_count"], 0)
        rows = [{"studentKey": "stu_1", "status": "ATTENDED"}]
        for data in (
            slot(rows),
            slot(rows, studentWorks=None),
            slot(rows, studentWorks=[None]),
            slot(rows, studentWorks=[], studentWorksFetchError=True),
        ):
            result = evaluate_lms_spck_quality(data, lesson=12)
            self.assertEqual(result["status"], "UNVERIFIED")
            self.assertTrue(
                all(issue_code(item) == ISSUE_CODE_LMS_MANUAL_CHECK for item in result["issues"])
            )

    def test_verified_missing_and_lesson14_spck_still_required(self):
        rows = [{"studentKey": "stu_1", "status": "ATTENDED", "comment": ""}]
        for lesson in (12, 13, 14):
            result = evaluate_lms_slot_quality(
                slot(rows, studentWorks=[]), class_code="SYNTH-JSB01", lesson=lesson
            )
            self.assertEqual(result["lms_spck"]["status"], "MISSING")
            self.assertEqual(result["lms_spck"]["missing_count"], 1)
            self.assertEqual(
                result["lms_comments"]["status"], "EXEMPT" if lesson == 14 else "MISSING"
            )
            self.assertTrue(
                any(issue_code(item) == ISSUE_CODE_LMS_SPCK_MISSING for item in result["issues"])
            )

    def test_regular_comments_one_sentence_comma_semicolon_and_inline_header(self):
        for text in (
            GOOD_COMMENT,
            GOOD_COMMENT.replace(";", ","),
            "NHẬN XÉT BUỔI HỌC SỐ 2: " + GOOD_COMMENT,
        ):
            result = evaluate_lms_student_comments(
                [{"status": "ATTENDED", "comment": text}], is_checkpoint=False, lesson=2
            )
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["issues"], [])

    def test_short_complete_ideas_are_review_not_missing_and_reasons_quantified(self):
        short = "Điểm mạnh: tốt; Cần cải thiện: còn chậm."
        result = evaluate_lms_student_comments(
            [
                {"status": "ATTENDED", "comment": short},
                {"status": "ATTENDED", "comment": "Học tốt."},
                {"status": "ABSENT_WITH_NOTICE", "comment": ""},
            ],
            is_checkpoint=False,
            lesson=2,
        )
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["submitted_count"], 2)
        self.assertEqual(result["missing_count"], 0)
        self.assertEqual(result["review_reasons"]["nhận xét sơ sài"], 2)
        self.assertEqual(issue_severity(result["issues"][0]), SEVERITY_NOTE)
        self.assertEqual(issue_text(result["issues"][0]), "[LMS] Nhận xét sơ sài (2/2 HS)")
        self.assertNotIn(short, issue_text(result["issues"][0]))

    def test_current_unverified_does_not_reactivate_old_spck_failure(self):
        old = make_structured_issue(ISSUE_CODE_LMS_SPCK_MISSING, "[LMS] Chưa nộp SPCK (1/1 HS)")
        result = {
            "class_code": "SYNTH-JSB01",
            "lesson": "12",
            "date": "10/11/2026",
            "lms_quality_issues": [old],
            "quality_issues": [old],
            "scan_target": {
                "class_code": "SYNTH-JSB01",
                "lesson": "12",
                "class_date": "10/11/2026",
            },
        }
        inspect_and_attach_quality_issues(
            [result],
            lms_slots_by_class={
                "SYNTH-JSB01": [
                    slot(
                        [{"studentKey": "stu_1", "status": "UNKNOWN"}],
                        index=11,
                        date="2026-11-10",
                        studentWorks=[],
                    )
                ]
            },
        )
        self.assertEqual(result["lms_spck"]["status"], "UNVERIFIED")
        self.assertFalse(
            any(
                issue_code(item) == ISSUE_CODE_LMS_SPCK_MISSING for item in result["quality_issues"]
            )
        )
        self.assertEqual(result["lms_previous_quality_issues"], [old])
        for key in ("lms_attendance", "lms_comments", "lms_spck", "lms_previous_quality_issues"):
            self.assertEqual(result[key], result["scan_target"][key])
        old_text = issue_text(old)
        for history_present in (True, False):
            if not history_present:
                result.pop("lms_previous_quality_issues", None)
                result["scan_target"].pop("lms_previous_quality_issues", None)
            effective, should_sync = _collect_row_verifiable_auto_issues(
                result,
                existing_note="QC ghi tay | " + old_text,
                lms_slots_by_class={"SYNTH-JSB01": [{"studentAttendance": [{"status": "UNKNOWN"}]}]},
                proven_auto_issues=[old_text],
            )
            self.assertTrue(should_sync)
            self.assertFalse(any(issue_code(item) == ISSUE_CODE_LMS_SPCK_MISSING for item in effective))
            self.assertFalse(any(issue_code(item) == ISSUE_CODE_LMS_SPCK_MISSING for item in result["lms_quality_issues"]))
            self.assertEqual([issue_text(item) for item in result["lms_previous_quality_issues"]], [old_text])
            merged = merge_checker_issue_note("QC ghi tay | " + old_text, effective, proven_auto_issues=[old_text])
            self.assertIn("QC ghi tay", merged)
            self.assertNotIn(old_text, merged)
            self.assertIn("kiểm tra thủ công", merged)


if __name__ == "__main__":
    unittest.main()
