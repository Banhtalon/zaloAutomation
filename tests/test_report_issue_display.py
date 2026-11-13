"""Display-only regression cases for repeated Teaching and bot issue messages."""

import copy
import csv
from html.parser import HTMLParser
import io
import unittest

from repositories.report_repository import (
    _resolve_row_issue_description,
    _split_display_issues,
    build_weekly_summary_csv,
)
from services.bcnb_safety import split_checker_issue_note
from services.report_email_service import (
    _prepare_email_row_fields,
    build_failed_classes_email_package,
)


COMMENT = "[LMS] Nhận xét cần xem lại (4/4 nhận xét - chưa rõ điểm cần cải thiện: 4/4)"
MISSING = "[LMS] Chưa nộp SPCK (4/4 HS)"
PRODUCT_NOTES = [
    "[LMS] Chưa ghi nội dung bài học",
    "[LMS] Nhận xét cần xem lại (3/4 nhận xét - chưa rõ điểm cần cải thiện: 3/4, chưa rõ lời khuyên/giải pháp: 3/4)",
    "[LMS] SPCK thiếu ảnh thumbnail/ảnh chụp (1/4 HS)",
    "[LMS] SPCK thiếu file đính kèm (4/4 HS)",
    "[LMS] Nhận xét SPCK sơ sài (4/4 HS)",
]


def report_row(code, issues):
    structured = [
        {"text": issue, "code": "LMS_QUALITY_FAIL", "severity": "NOTE"} for issue in issues
    ]
    return {
        "class_code": code,
        "teacher": "Giảng viên mẫu",
        "lesson": "12",
        "class_date": "08/11/2026",
        "zalo_status": "✅ Đã nộp – Có minh chứng",
        "lms_status": "✅ Đã nhận xét",
        "qc_issue_note": "; ".join(issues),
        "quality_issues": structured,
        "lms_quality_issues": structured,
    }


class IssueListParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.styles = []
        self.items = []
        self.active_item = None

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.active_item = []
            self.styles.append(dict(attrs).get("style", ""))

    def handle_data(self, data):
        if self.active_item is not None:
            self.active_item.append(data)

    def handle_endtag(self, tag):
        if tag == "li" and self.active_item is not None:
            self.items.append("".join(self.active_item))
            self.active_item = None


class ReportIssueDisplayTests(unittest.TestCase):
    def test_tagged_manual_follow_up_survives_legacy_auto_filter(self):
        manual = "QC đã trao đổi | [LMS] Nhận xét HS copy-paste trùng lặp (3/3 HS); cần theo dõi buổi sau"
        row = report_row("SYNTH-CSA03", [MISSING])
        row["qc_issue_note"] = manual
        row["lms_quality_issues"].append("[LMS] Nhận xét HS copy-paste trùng lặp (3/3 HS)")
        original = copy.deepcopy(row)
        description = _resolve_row_issue_description(row, st={})
        self.assertIn("QC đã trao đổi", description)
        self.assertIn(
            "[LMS] Nhận xét HS copy-paste trùng lặp (3/3 HS); cần theo dõi buổi sau", description
        )
        self.assertEqual(description.count("copy-paste trùng lặp"), 1)
        self.assertEqual(row, original)
        self.assertIn(
            "cần theo dõi buổi sau", _prepare_email_row_fields(1, row)["issue_description"]
        )
        self.assertIn("cần theo dõi buổi sau", build_weekly_summary_csv([row]))

    def test_grouped_teaching_note_and_structured_issues_appear_once(self):
        for issues in ([COMMENT, MISSING], PRODUCT_NOTES):
            row = report_row("SYNTH-CSA03", issues)
            before = copy.deepcopy(row)
            description = _resolve_row_issue_description(row, st={})
            self.assertEqual(_split_display_issues(description), issues)
            self.assertEqual(row, before)
            self.assertEqual(_resolve_row_issue_description({}, st=row), description)
            self.assertEqual(_resolve_row_issue_description({}, st={}, fb=row), description)

    def test_cached_repeated_description_is_cleaned_for_email_and_csv(self):
        row = report_row("SYNTH-CSA03", [COMMENT, MISSING])
        row["issue_description"] = "; ".join([COMMENT, MISSING, COMMENT, MISSING])
        fields = _prepare_email_row_fields(1, row)
        self.assertEqual(fields["issue_description"], "; ".join([COMMENT, MISSING]))
        csv_rows = list(csv.reader(io.StringIO(build_weekly_summary_csv([row]))))
        self.assertEqual(csv_rows[1][7], "; ".join([COMMENT, MISSING]))

    def test_reason_semicolons_and_manual_notes_keep_their_text(self):
        reason = "[LMS] Nhận xét cần xem lại (2/4 nhận xét; chưa rõ điểm mạnh: 1/4; chưa rõ lời khuyên: 2/4)"
        manual = "QC đã trao đổi; cần kiểm tra thêm & đối chiếu <bản gốc>"
        row = report_row("SYNTH-CSA03", [reason, MISSING])
        row["qc_issue_note"] = manual + " | " + row["qc_issue_note"]
        description = _resolve_row_issue_description(row, st={})
        self.assertEqual(_split_display_issues(description), [manual, reason, MISSING])
        self.assertEqual(split_checker_issue_note(row["qc_issue_note"]), (row["qc_issue_note"], []))

    def test_distinct_student_counts_and_reasons_are_not_merged(self):
        issues = [
            MISSING,
            MISSING.replace("4/4", "3/4"),
            COMMENT,
            COMMENT.replace("cải thiện", "lời khuyên"),
        ]
        row = report_row("SYNTH-CSA03", issues)
        self.assertEqual(_split_display_issues(_resolve_row_issue_description(row, st={})), issues)

    def test_email_has_one_bullet_per_issue_and_selective_warning_or_red_emphasis(self):
        rows = [
            report_row("SYNTH-CSA03", [COMMENT, MISSING]),
            report_row("SYNTH-ROB-ARMI12", PRODUCT_NOTES),
        ]
        package = build_failed_classes_email_package(
            week=45,
            year=2026,
            failed_rows=rows,
            total_rows_count=2,
            selected_job_id=None,
            recipients=["owner@example.com"],
        )
        parsed = IssueListParser()
        parsed.feed(package["html_table"])
        self.assertEqual(parsed.items, [COMMENT, MISSING, *PRODUCT_NOTES])
        red_items = [item for item, style in zip(parsed.items, parsed.styles) if "#b91c1c" in style]
        warn_items = [item for item, style in zip(parsed.items, parsed.styles) if "#b45309" in style]
        self.assertEqual(red_items, [])
        self.assertEqual(warn_items, [MISSING])
        self.assertNotIn("background:#fef2f2", package["html_table"])

    def test_zalo_missing_is_red_error_while_spck_missing_and_comment_missing_are_warnings_in_email(
        self,
    ):
        spck_missing_row = {
            "class_code": "SYNTH-CSA03",
            "teacher": "Giảng viên A",
            "lesson": "12",
            "class_date": "08/11/2026",
            "zalo_status": "✅ Đã nộp – Có minh chứng",
            "lms_status": "✅ Đã nhận xét 4/4 HS đi học",
            "lms_quality_issues": [
                {
                    "code": "LMS_SPCK_MISSING",
                    "severity": "FAIL",
                    "text": "[LMS] Chưa nộp SPCK (4/4 HS)",
                }
            ],
        }
        spck_comment_missing_row = {
            "class_code": "SYNTH-GB42-HB",
            "teacher": "Giảng viên B",
            "lesson": "13",
            "class_date": "04/11/2026",
            "zalo_status": "✅ Đã nộp – Có minh chứng",
            "lms_status": "✅ Đã nhận xét 4/4 HS đi học · 1 HS nghỉ miễn",
            "lms_quality_issues": [
                {
                    "code": "LMS_SUMMARY_NOTE",
                    "severity": "NOTE",
                    "text": "[LMS] Chưa ghi nội dung bài học",
                },
                {
                    "code": "LMS_SPCK_COMMENT_MISSING",
                    "severity": "FAIL",
                    "text": "[LMS] SPCK chưa có nhận xét (4/4 HS)",
                },
            ],
        }
        zalo_missing_and_spck_row = {
            "class_code": "SYNTH-ROB-ARMI12",
            "teacher": "Giảng viên C",
            "lesson": "13",
            "class_date": "06/11/2026",
            "zalo_status": "❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng",
            "lms_status": "✅ Đã nhận xét 4/4 HS đi học",
            "evaluation_status": "⚠️ Đã hoàn tất nhận xét LMS, Zalo cần kiểm tra",
            "lms_quality_issues": [
                {
                    "code": "LMS_SUMMARY_NOTE",
                    "severity": "NOTE",
                    "text": "[LMS] Chưa ghi nội dung bài học",
                },
                {
                    "code": "LMS_SPCK_COMMENT_MISSING",
                    "severity": "FAIL",
                    "text": "[LMS] SPCK chưa có nhận xét (4/4 HS)",
                },
                "[Zalo] Chưa gửi báo cáo nhận xét trên nhóm Zalo",
            ],
        }
        f1 = _prepare_email_row_fields(1, spck_missing_row)
        f2 = _prepare_email_row_fields(2, spck_comment_missing_row)
        f3 = _prepare_email_row_fields(3, zalo_missing_and_spck_row)
        self.assertEqual(f1["evaluation_status"], "⚠️ Chưa nộp SPCK trên LMS")
        self.assertEqual(f2["evaluation_status"], "⚠️ SPCK chưa có nhận xét trên LMS")
        self.assertEqual(f3["zalo_status"], "❌ Chưa nhận xét")
        self.assertEqual(f3["evaluation_status"], "❌ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo")

        package = build_failed_classes_email_package(
            week=45,
            year=2026,
            failed_rows=[spck_missing_row, spck_comment_missing_row, zalo_missing_and_spck_row],
            total_rows_count=46,
            selected_job_id=501,
            recipients=["quangda@mindx.net.vn"],
        )
        html_tbl = package["html_table"]
        self.assertIn("color:#b91c1c;font-weight:700;\">❌ Chưa nhận xét</td>", html_tbl)
        self.assertIn(
            "color:#b91c1c;font-weight:700;\">❌ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo</td>",
            html_tbl,
        )
        self.assertIn("color:#b45309;font-weight:600;\">⚠️ Chưa nộp SPCK trên LMS</td>", html_tbl)
        self.assertIn(
            "color:#b45309;font-weight:600;\">⚠️ SPCK chưa có nhận xét trên LMS</td>",
            html_tbl,
        )
        parsed = IssueListParser()
        parsed.feed(html_tbl)
        red_items = [item for item, style in zip(parsed.items, parsed.styles) if "#b91c1c" in style]
        warn_items = [item for item, style in zip(parsed.items, parsed.styles) if "#b45309" in style]
        self.assertEqual(red_items, ["[Zalo] Chưa gửi báo cáo nhận xét trên nhóm Zalo"])
        self.assertEqual(
            warn_items,
            [
                "[LMS] Chưa nộp SPCK (4/4 HS)",
                "[LMS] SPCK chưa có nhận xét (4/4 HS)",
                "[LMS] SPCK chưa có nhận xét (4/4 HS)",
            ],
        )

    def test_html_escapes_user_text_and_handles_empty_issues(self):
        row = report_row("SYNTH-CSA03", [])
        row["issue_description"] = '<script>alert("QC")</script> & ghi chú'
        package = build_failed_classes_email_package(
            week=45,
            year=2026,
            failed_rows=[row, report_row("SYNTH-GA38", [])],
            total_rows_count=2,
            selected_job_id=None,
            recipients=["owner@example.com"],
        )
        self.assertNotIn("<script>", package["html_table"])
        self.assertIn("&lt;script&gt;", package["html_table"])
        self.assertIn("&amp; ghi chú", package["html_table"])
        parsed = IssueListParser()
        parsed.feed(package["html_table"])
        self.assertEqual(parsed.items, [row["issue_description"]])


if __name__ == "__main__":
    unittest.main()
