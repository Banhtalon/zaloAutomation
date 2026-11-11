"""Synthetic evidence for attendance-aware reporting; no live data access."""

import unittest

from api.job_routes import _annotate_item_status
from repositories.report_repository import (
    _resolve_row_evaluation_status,
    _resolve_row_issue_description,
    _sanitize_dashboard_item,
    build_weekly_summary_csv,
    filter_failed_weekly_summary_rows,
)
from services.bcnb_safety import merge_checker_issue_note, normalize_auto_issue_text
from services.report_email_service import _prepare_email_row_fields
from services.report_status import has_spck_missing_fail, is_failed, resolve_lms_status


def completed_row():
    return {
        "class_code": "VT-JSB99",
        "status": "✅ Đã báo cáo",
        "zalo_status": "✅ Đã báo cáo",
        "evidence_state": "SUBMITTED_VERIFIED",
        "nx_lms_checked": False,
        "lms_status": "❌ Chưa nộp LMS",
        "evaluation_status": "❌ Chưa nộp SPCK trên LMS",
        "lms_attendance": {"status": "VERIFIED", "attended_count": 7, "absent_count": 1, "exempt_count": 1, "unverified_count": 0, "total_count": 8},
        "lms_comments": {"status": "COMPLETE", "submitted_count": 7, "missing_count": 0, "review_count": 0, "exempt_count": 1},
        "lms_spck": {"status": "COMPLETE", "submitted_count": 7, "missing_count": 0, "attended_count": 7, "exempt_count": 1},
        "quality_issues": [],
        "lms_quality_issues": [],
        "scan_target": {"class_code": "VT-JSB99", "lesson": "12"},
    }


class AttendanceReportingTests(unittest.TestCase):
    def test_verified_attendees_override_old_unchecked_checkbox_on_all_exports(self):
        row = completed_row()
        status = "✅ Đã nhận xét 7/7 HS đi học · 1 HS nghỉ miễn"
        self.assertEqual(resolve_lms_status(row), status)
        self.assertFalse(is_failed(row))
        self.assertEqual(filter_failed_weekly_summary_rows([row]), [])
        self.assertEqual(_annotate_item_status(dict(row))["lms_status"], status)
        csv = build_weekly_summary_csv([row])
        self.assertIn(status, csv)
        self.assertIn("SPCK 7/7 HS đi học · 1 HS nghỉ miễn", csv)
        self.assertNotIn("❌ Chưa nộp SPCK", csv)
        email = _prepare_email_row_fields(1, row)
        self.assertEqual(email["lms_status"], status)
        self.assertIn("SPCK 7/7 HS đi học · 1 HS nghỉ miễn", email["evaluation_status"])

    def test_whole_class_absent_is_exempt_and_unknown_waits_without_teacher_error(self):
        for status in ("EXEMPT", "UNVERIFIED"):
            with self.subTest(status=status):
                row = completed_row()
                row["lms_attendance"]["status"] = status
                row["lms_comments"]["status"] = status
                row["lms_spck"]["status"] = status
                row["lms_quality_issues"] = [{"code": "LMS_SPCK_MISSING", "severity": "FAIL", "text": "[LMS] Chưa nộp SPCK (8/8 HS)"}]
                self.assertFalse(is_failed(row))
                self.assertFalse(has_spck_missing_fail(row))
                evaluation = _resolve_row_evaluation_status(zalo_status=row["status"], lms_status=row["lms_status"], row=row)
                self.assertIn("Không áp dụng" if status == "EXEMPT" else "Chờ xác minh", evaluation)
                row["evidence_state"] = "NOT_FOUND_VERIFIED"
                self.assertTrue(is_failed(row), "attendance exemption must not hide a missing Zalo report")

    def test_missing_real_comment_or_spck_stays_failed_but_quality_review_is_note(self):
        row = completed_row()
        row["lms_comments"]["status"] = "REVIEW"
        row["lms_comments"]["review_count"] = 2
        self.assertFalse(is_failed(row))
        self.assertIn("cần xem lại chất lượng", resolve_lms_status(row))
        row["lms_comments"]["status"] = "MISSING"
        row["lms_comments"]["submitted_count"] = 6
        row["lms_comments"]["missing_count"] = 1
        self.assertTrue(is_failed(row))
        row["lms_comments"]["status"] = "COMPLETE"
        row["lms_spck"]["status"] = "MISSING"
        self.assertTrue(is_failed(row))

    def test_manual_and_note_spck_text_cannot_create_teacher_error(self):
        row = {"status": "✅ Đã báo cáo", "lms_status": "✅ Đã nộp LMS", "qc_issue_note": "[LMS] Chưa nộp SPCK (8/8 HS)", "issue_description": "[LMS] Chưa nộp SPCK (8/8 HS)"}
        self.assertFalse(is_failed(row))
        row["lms_quality_issues"] = [{"code": "LMS_SPCK_MISSING", "severity": "NOTE", "text": row["qc_issue_note"]}]
        self.assertFalse(is_failed(row))
        row["lms_quality_issues"][0]["severity"] = "FAIL"
        self.assertTrue(is_failed(row), "legacy structured submission fault remains readable")

    def test_lesson_14_comment_exemption_keeps_spck_check_required(self):
        row = completed_row()
        row["scan_target"]["lesson"] = "14"
        row["lms_comments"]["status"] = "EXEMPT"
        self.assertEqual(resolve_lms_status(row), "✅ Miễn nhận xét LMS (Buổi 14)")
        row["lms_spck"]["status"] = "UNVERIFIED"
        evaluation = _resolve_row_evaluation_status(zalo_status=row["status"], lms_status=row["lms_status"], row=row)
        self.assertEqual(evaluation, "⏳ Chờ xác minh SPCK trên LMS")
        row["lms_spck"]["status"] = "MISSING"
        self.assertTrue(is_failed(row))
        evaluation = _resolve_row_evaluation_status(zalo_status=row["status"], lms_status=row["lms_status"], row=row)
        self.assertEqual(evaluation, "❌ Chưa nộp SPCK trên LMS")

    def test_unknown_spck_does_not_hide_a_verified_missing_comment(self):
        row = completed_row()
        row["lms_comments"].update(status="MISSING", missing_count=1, submitted_count=6)
        row["lms_spck"]["status"] = "UNVERIFIED"
        self.assertTrue(is_failed(row))
        evaluation = _resolve_row_evaluation_status(zalo_status=row["status"], lms_status=row["lms_status"], row=row)
        self.assertIn("chưa nhận xét LMS", evaluation)
        self.assertNotIn("Chờ xác minh SPCK", evaluation)

    def test_sanitizer_transports_current_counts_but_never_invents_fallback_counts(self):
        row = completed_row()
        clean = _sanitize_dashboard_item(row, week=46, year=2026, source_job_id=1, lms_status=row["lms_status"], job_timestamp="2026-11-11T01:00:00Z")
        self.assertEqual(clean["lms_comments"], row["lms_comments"])
        self.assertEqual(clean["scan_target"]["lms_attendance"], row["lms_attendance"])
        self.assertFalse(clean["is_failed"])
        legacy = _sanitize_dashboard_item({"status": "✅ Đã báo cáo"}, week=46, year=2026, source_job_id=2, lms_status="✅ Đã nộp LMS", job_timestamp="2026-11-11T02:00:00Z", fallback_bcnb_meta=row)
        self.assertNotIn("lms_comments", legacy)
        self.assertNotIn("lms_spck", legacy)
        self.assertNotIn("7/7", legacy["lms_status"])

    def test_counts_and_distinct_quality_reasons_survive_teaching_and_reports(self):
        old = "[LMS] Nhận xét thiếu ý (2/7 HS)"
        short = "[LMS] Nhận xét học viên quá ngắn/sơ sài (1/7 HS)"
        missing_ideas = "[LMS] Nhận xét thiếu ý (3/7 HS; thiếu ý thái độ)"
        checkpoint = "[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực (1/7 HS)"
        for issue in (old, short, missing_ideas, checkpoint):
            self.assertEqual(normalize_auto_issue_text(issue), issue)
        note = merge_checker_issue_note("QC ghi tay | " + old, [short, missing_ideas], proven_auto_issues=[old])
        self.assertIn("QC ghi tay", note)
        self.assertIn(short, note)
        self.assertIn("3/7 HS", note)
        self.assertIn("thiếu ý thái độ", note)
        self.assertNotIn(old, note)
        row = completed_row()
        row["lms_quality_issues"] = [short, missing_ideas]
        desc = _resolve_row_issue_description(row, st={})
        self.assertIn(short, desc)
        self.assertIn(missing_ideas, desc)
        self.assertFalse(is_failed(row))


if __name__ == "__main__":
    unittest.main()
