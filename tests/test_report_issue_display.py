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
        self.assertEqual(len(csv_rows[0]), 8)
        self.assertEqual(csv_rows[1][6], "; ".join([COMMENT, MISSING]))

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
        red_items = [item for item, style in zip(parsed.items, parsed.styles) if "#dc2626" in style]
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
            "evidence_state": "NOT_FOUND_VERIFIED",
            "scan_status": "VERIFIED",
            "write_status": "FAILED_BEFORE_WRITE",
            "bcnb_update_state": "FAILED_BEFORE_WRITE",
            "error_code": "EVIDENCE_NOT_READY",
            "category": "REVIEW",
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
        self.assertEqual(f3["is_zalo_missing"], "1")
        zalo_missing_csv = build_weekly_summary_csv([zalo_missing_and_spck_row])
        self.assertIn("❌ Chưa nhận xét", zalo_missing_csv)
        self.assertNotIn("Tình trạng đánh giá", zalo_missing_csv)
        l14_stale_review = {
            **zalo_missing_and_spck_row,
            "lesson": "14",
            "lms_quality_issues": [],
        }
        self.assertEqual(
            _prepare_email_row_fields(4, l14_stale_review)["evaluation_status"],
            "✅ Đã hoàn thành nhận xét",
        )
        l14_csv = build_weekly_summary_csv([l14_stale_review])
        self.assertIn("✅ Miễn báo cáo (Buổi 14)", l14_csv)
        self.assertNotIn("Tình trạng đánh giá", l14_csv)
        manual_stale_review = {
            **zalo_missing_and_spck_row,
            "manual_approved": True,
            "zalo_status": "✅ Đã duyệt tay & Tích BCNB",
            "lms_quality_issues": [],
        }
        self.assertEqual(
            _prepare_email_row_fields(5, manual_stale_review)["evaluation_status"],
            "✅ Đã hoàn thành nhận xét",
        )
        manual_csv = build_weekly_summary_csv([manual_stale_review])
        self.assertIn("✅ Đã duyệt tay & Tích BCNB", manual_csv)
        self.assertNotIn("Tình trạng đánh giá", manual_csv)

        package = build_failed_classes_email_package(
            week=45,
            year=2026,
            failed_rows=[spck_missing_row, spck_comment_missing_row, zalo_missing_and_spck_row],
            total_rows_count=46,
            selected_job_id=501,
            recipients=["quangda@mindx.net.vn"],
        )
        html_tbl = package["html_table"]
        self.assertNotIn("Tình trạng đánh giá", html_tbl)
        self.assertEqual(package["nx_failed_count"], 1)
        self.assertEqual(package["spck_only_count"], 2)
        self.assertIn("Lưu ý lớp chưa hoàn tất SPCK trên LMS", html_tbl)
        self.assertIn(
            'color:#dc2626;font-weight:700;"><span style="color:#dc2626;font-weight:700;">❌ Chưa nhận xét</span></td>',
            html_tbl,
        )
        parsed = IssueListParser()
        parsed.feed(html_tbl)
        red_items = [item for item, style in zip(parsed.items, parsed.styles) if "#dc2626" in style]
        warn_items = [item for item, style in zip(parsed.items, parsed.styles) if "#b45309" in style]
        self.assertEqual(red_items, ["[Zalo] Chưa gửi báo cáo nhận xét trên nhóm Zalo"])
        self.assertEqual(
            warn_items,
            [
                "[LMS] SPCK chưa có nhận xét (4/4 HS)",
                "[LMS] Chưa nộp SPCK (4/4 HS)",
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

    def test_muc_a_spck_lms_zalo_email_coloring_and_non_collision(self):
        muc_a_spck_missing = {
            "class_code": "SYNTH-CSI02",
            "teacher": "Giảng viên D",
            "lesson": "12",
            "class_date": "08/11/2026",
            "zalo_status": "✅ Đã nộp – Có minh chứng",
            "lms_status": "✅ Đã nhận xét 4/4 HS đi học",
            "lms_quality_issues": ["[SPCK] Chưa nộp (0/4)"],
        }
        muc_a_both_chua_nx = {
            "class_code": "SYNTH-JSB15",
            "teacher": "Giảng viên E",
            "lesson": "13",
            "class_date": "09/11/2026",
            "zalo_status": "✅ Đã nộp – Có minh chứng",
            "lms_status": "❌ Chưa nhận xét đủ HS",
            "qc_issue_note": "[LMS] Chưa NX (1/4); [SPCK] Chưa NX (1/4); [SPCK] Bổ sung tên SP, ảnh",
            "lms_quality_issues": [
                {"code": "LMS_QUALITY_FAIL", "severity": "FAIL", "text": "[LMS] Chưa NX (1/4)"},
                {
                    "code": "LMS_SPCK_COMMENT_MISSING",
                    "severity": "FAIL",
                    "text": "[SPCK] Chưa NX (1/4)",
                },
                {
                    "code": "LMS_SPCK_NOTE",
                    "severity": "NOTE",
                    "text": "[SPCK] Bổ sung tên SP, ảnh",
                },
            ],
        }
        muc_a_zalo_missing = {
            "class_code": "SYNTH-GA38",
            "teacher": "Giảng viên F",
            "lesson": "12",
            "class_date": "10/11/2026",
            "zalo_status": "❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng",
            "evidence_state": "NOT_FOUND_VERIFIED",
            "scan_status": "VERIFIED",
            "write_status": "FAILED_BEFORE_WRITE",
            "bcnb_update_state": "FAILED_BEFORE_WRITE",
            "error_code": "EVIDENCE_NOT_READY",
            "category": "REVIEW",
            "lms_status": "✅ Đã nhận xét 4/4 HS đi học",
            "lms_quality_issues": [
                {"code": "LMS_SUMMARY_NOTE", "severity": "NOTE", "text": "[LMS] Điền tóm tắt bài"},
                "[Zalo] Chưa gửi báo cáo nhận xét trên nhóm Zalo",
            ],
        }

        f_spck = _prepare_email_row_fields(1, muc_a_spck_missing)
        self.assertEqual(f_spck["evaluation_status"], "⚠️ Chưa nộp SPCK trên LMS")

        # Both [LMS] Chưa NX (1/4) and [SPCK] Chưa NX (1/4) survive deduplication without colliding!
        desc_both = _resolve_row_issue_description(muc_a_both_chua_nx, st={})
        self.assertEqual(
            _split_display_issues(desc_both),
            ["[LMS] Chưa NX (1/4)", "[SPCK] Chưa NX (1/4)", "[SPCK] Bổ sung tên SP, ảnh"],
        )

        pkg = build_failed_classes_email_package(
            week=46,
            year=2026,
            failed_rows=[muc_a_spck_missing, muc_a_both_chua_nx, muc_a_zalo_missing],
            total_rows_count=10,
            selected_job_id=601,
            recipients=["owner@example.com"],
        )
        parsed = IssueListParser()
        parsed.feed(pkg["html_table"])
        red_items = [item for item, style in zip(parsed.items, parsed.styles) if "#dc2626" in style]
        warn_items = [item for item, style in zip(parsed.items, parsed.styles) if "#b45309" in style]
        self.assertEqual(
            red_items,
            ["[LMS] Chưa NX (1/4)", "[Zalo] Chưa gửi báo cáo nhận xét trên nhóm Zalo"],
        )
        self.assertEqual(
            warn_items,
            ["[SPCK] Chưa NX (1/4)", "[SPCK] Chưa nộp (0/4)"],
        )
        self.assertIn("[LMS] Chưa NX (1/4)", parsed.items)
        self.assertIn("[SPCK] Bổ sung tên SP, ảnh", parsed.items)
        self.assertIn("[LMS] Chưa có nội dung bài", parsed.items)

    def test_legacy_and_muc_a_dedup_and_missing_attendance_email_csv_display(self):
        from services.report_email_service import _short_url_issue

        self.assertEqual(_short_url_issue("[LMS] Chưa điểm danh"), "Chưa ĐD LMS")

        # 1. Legacy + Mức A summary & manual-check deduplicate cleanly, preferring Mức A regardless of order
        row_rob_dup = {
            "class_code": "VT-KIND-ROB4B04",
            "teacher": "Giảng viên R",
            "lesson": "12",
            "class_date": "15/11/2026",
            "zalo_status": "✅ Đã nộp – Có minh chứng",
            "lms_status": "⏳ Chờ xác minh điểm danh/nhận xét LMS",
            "qc_issue_note": (
                "QC nhắc riêng | [LMS] Chưa ghi nội dung bài học; "
                "[LMS] Không tải được đầy đủ dữ liệu buổi học từ LMS, cần kiểm tra thủ công; "
                "[Zalo] Báo cáo không ghi ngày/buổi"
            ),
            "lms_quality_issues": [
                {"code": "LMS_SUMMARY_NOTE", "severity": "NOTE", "text": "[LMS] Điền tóm tắt bài"},
                {"code": "LMS_MANUAL_CHECK", "severity": "NOTE", "text": "[LMS] Kiểm tra tay trên LMS"},
            ],
            "zalo_content_issues": [
                {"code": "ZALO_REPORT_NO_DATE", "severity": "NOTE", "text": "[Zalo] Thiếu ngày/buổi"}
            ],
        }
        self.assertEqual(
            _split_display_issues(_resolve_row_issue_description(row_rob_dup, st={})),
            [
                "QC nhắc riêng",
                "[LMS] Chưa có nội dung bài",
                "[LMS] Kiểm tra tay trên LMS",
                "[Zalo] Thiếu ngày/buổi",
            ],
        )

        # Reverse order + compound Mức A summary note ([LMS] Điền tóm tắt bài, viết kỹ NX) -> [LMS] Chưa có nội dung bài, viết kỹ NX
        row_compound = {
            "qc_issue_note": "[LMS] Chưa ghi nội dung bài học",
            "lms_quality_issues": ["[LMS] Điền tóm tắt bài, viết kỹ NX", "[LMS] Chưa ghi nội dung bài học"],
        }
        self.assertEqual(
            _resolve_row_issue_description(row_compound, st={}),
            "[LMS] Chưa có nội dung bài, viết kỹ NX",
        )

        # 2. MISSING_ATTENDANCE row renders ❌ Chưa điểm danh LMS and [LMS] Chưa điểm danh in Email & CSV
        missing_att_row = {
            "class_code": "VT-KIND-ROB4B04",
            "teacher": "Giảng viên R",
            "lesson": "12",
            "class_date": "15/11/2026",
            "zalo_status": "✅ Đã nộp – Có minh chứng",
            "evidence_state": "SUBMITTED_VERIFIED",
            "lms_attendance": {
                "status": "MISSING_ATTENDANCE",
                "attended_count": 0,
                "absent_count": 0,
                "unverified_count": 0,
                "exempt_count": 0,
                "total_count": 0,
                "active_student_count": 6,
            },
            "lms_comments": {"status": "MISSING_ATTENDANCE", "submitted_count": 0},
            "lms_spck": {"status": "MISSING_ATTENDANCE", "submitted_count": 0},
            "lms_quality_issues": [
                {"code": "LMS_QUALITY_FAIL", "severity": "FAIL", "text": "[LMS] Chưa điểm danh"}
            ],
        }
        fields = _prepare_email_row_fields(1, missing_att_row)
        self.assertEqual(fields["lms_status"], "❌ Chưa điểm danh LMS")
        self.assertEqual(fields["issue_description"], "[LMS] Chưa điểm danh")
        csv_text = build_weekly_summary_csv([missing_att_row])
        self.assertIn("❌ Chưa điểm danh LMS", csv_text)
        self.assertIn("[LMS] Chưa điểm danh", csv_text)

    def test_dec_056_8col_email_csv_red_lms_and_spck_separate_table_and_dedup(self) -> None:
        import csv
        import io
        from services.report_email_service import EMAIL_TABLE_HEADERS
        from repositories.report_repository import CSV_HEADERS

        self.assertEqual(len(EMAIL_TABLE_HEADERS), 8)
        self.assertNotIn("Tình trạng đánh giá", EMAIL_TABLE_HEADERS)
        self.assertEqual(len(CSV_HEADERS), 8)
        self.assertNotIn("Tình trạng đánh giá", CSV_HEADERS)

        pta15_row = {
            "class_code": "VT-C4K-PTA15",
            "teacher": "Giảng viên PTA15",
            "lesson": "1",
            "class_date": "22/11/2026",
            "zalo_status": "✅ Đã nộp – Có minh chứng",
            "evidence_state": "SUBMITTED_VERIFIED",
            "lms_attendance": {"status": "VERIFIED", "attended_count": 6, "absent_count": 0},
            "lms_comments": {"status": "MISSING", "submitted_count": 0},
            "lms_quality_issues": [
                {"code": "LMS_QUALITY_FAIL", "severity": "FAIL", "text": "[LMS] Chưa NX (6/6)"},
                {"code": "LMS_SUMMARY_NOTE", "severity": "NOTE", "text": "[LMS] Điền tóm tắt bài"},
            ],
        }
        partial_lms_row = {
            "class_code": "VT-C4K-PTB02",
            "teacher": "Giảng viên PTB02",
            "lesson": "3",
            "class_date": "22/11/2026",
            "zalo_status": "✅ Đã nộp – Có minh chứng",
            "evidence_state": "SUBMITTED_VERIFIED",
            "lms_attendance": {"status": "VERIFIED", "attended_count": 6, "absent_count": 0},
            "lms_comments": {"status": "MISSING", "submitted_count": 5},
            "lms_quality_issues": [
                {"code": "LMS_QUALITY_FAIL", "severity": "FAIL", "text": "[LMS] Chưa NX (1/6)"},
            ],
        }
        semia06_row = {
            "class_code": "VT-ROB-SEMIA06",
            "teacher": "Giảng viên SEMIA06",
            "lesson": "12",
            "class_date": "22/11/2026",
            "zalo_status": "❌ Chưa nhận xét",
            "evidence_state": "NOT_FOUND_VERIFIED",
            "lms_attendance": {"status": "VERIFIED", "attended_count": 6, "absent_count": 0},
            "lms_comments": {"status": "COMPLETE", "submitted_count": 6},
            "lms_spck": {"status": "UNVERIFIED", "submitted_count": 0},
            "qc_issue_note": "[SPCK] Chưa nộp (0/6); [LMS] Điền tóm tắt bài; [SPCK] Kiểm tra tay trên LMS",
            "lms_quality_issues": [
                {"code": "LMS_SUMMARY_NOTE", "severity": "NOTE", "text": "[LMS] Điền tóm tắt bài"},
                {"code": "LMS_MANUAL_CHECK", "severity": "NOTE", "text": "[SPCK] Kiểm tra tay trên LMS"},
                {"code": "LMS_SPCK_MISSING", "severity": "FAIL", "text": "[SPCK] Chưa nộp (0/6)"},
            ],
        }
        prea16_spck_only = {
            "class_code": "VT-ROB-PREA16",
            "teacher": "Giảng viên PREA16",
            "lesson": "12",
            "class_date": "17/11/2026",
            "zalo_status": "✅ Đã nộp – Có minh chứng",
            "evidence_state": "SUBMITTED_VERIFIED",
            "lms_attendance": {"status": "VERIFIED", "attended_count": 5, "absent_count": 0},
            "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
            "lms_spck": {"status": "MISSING", "submitted_count": 0},
            "lms_quality_issues": [
                {"code": "LMS_SPCK_MISSING", "severity": "FAIL", "text": "[SPCK] Chưa nộp (0/5)"},
            ],
        }

        pkg = build_failed_classes_email_package(
            week=47,
            year=2026,
            failed_rows=[pta15_row, partial_lms_row, semia06_row, prea16_spck_only],
            total_rows_count=20,
            selected_job_id=580,
            recipients=["quangda@mindx.net.vn"],
        )
        self.assertEqual(pkg["nx_failed_count"], 3)
        self.assertEqual(pkg["spck_only_count"], 1)
        self.assertIn("3/20 buổi lỗi NX (3 lớp) · 1 buổi lưu ý SPCK", pkg["subject"])
        self.assertNotIn("Tình trạng đánh giá", pkg["html_body"])
        self.assertNotIn("Tình trạng đánh giá", pkg["text_table"])
        self.assertIn(
            '<span style="color:#dc2626;font-weight:700;">❌ Chưa nhận xét</span>',
            pkg["html_table"],
        )
        self.assertIn(
            '<span style="color:#dc2626;font-weight:700;">❌ Chưa nhận xét (thiếu 1/6 HS)</span>',
            pkg["html_table"],
        )
        self.assertIn("[LMS] Chưa có nội dung bài", pkg["html_table"])
        self.assertNotIn("[LMS] Điền tóm tắt bài", pkg["html_table"])
        self.assertNotIn("[SPCK] Kiểm tra tay trên LMS", pkg["html_table"])

        parsed_csv = list(csv.reader(io.StringIO(pkg["csv_text"].lstrip("\ufeff"))))
        self.assertEqual(parsed_csv[0], list(CSV_HEADERS))
        self.assertEqual(len(parsed_csv[0]), 8)
        self.assertEqual(len(parsed_csv), 5)

        # Zero NX errors + SPCK-only row still builds valid email
        pkg_spck_only = build_failed_classes_email_package(
            week=47,
            year=2026,
            failed_rows=[prea16_spck_only],
            total_rows_count=20,
            selected_job_id=581,
            recipients=["quangda@mindx.net.vn"],
        )
        self.assertEqual(pkg_spck_only["nx_failed_count"], 0)
        self.assertEqual(pkg_spck_only["spck_only_count"], 1)
        self.assertIn("0/20 buổi lỗi NX · 1 buổi lưu ý SPCK", pkg_spck_only["subject"])
        self.assertIn("Không có buổi học lỗi nhận xét Zalo / LMS", pkg_spck_only["html_table"])
        self.assertIn("VT-ROB-PREA16", pkg_spck_only["html_table"])

        # Missing attendance & legacy LMS comment-missing bullets are red (#dc2626) in NX LMS and issue list
        missing_att_pkg = build_failed_classes_email_package(
            week=47,
            year=2026,
            failed_rows=[
                {
                    "class_code": "VT-ROB-ATT01",
                    "teacher": "Giảng viên T",
                    "lesson": "12",
                    "class_date": "22/11/2026",
                    "zalo_status": "✅ Đã nộp – Có minh chứng",
                    "evidence_state": "SUBMITTED_VERIFIED",
                    "lms_attendance": {"status": "MISSING_ATTENDANCE", "attended_count": 0},
                    "lms_comments": {"status": "MISSING_ATTENDANCE", "submitted_count": 0},
                    "lms_quality_issues": [
                        {"code": "LMS_QUALITY_FAIL", "severity": "FAIL", "text": "[LMS] Chưa điểm danh"},
                    ],
                },
                {
                    "class_code": "VT-C4K-LEG01",
                    "teacher": "Giảng viên L",
                    "lesson": "4",
                    "class_date": "22/11/2026",
                    "zalo_status": "✅ Đã nộp – Có minh chứng",
                    "evidence_state": "SUBMITTED_VERIFIED",
                    "lms_status": "❌ Chưa nhận xét",
                    "issue_description": "[LMS] Thiếu nhận xét học viên (2/6 HS)",
                },
            ],
            total_rows_count=10,
            selected_job_id=582,
            recipients=["quangda@mindx.net.vn"],
        )
        self.assertIn(
            '<span style="color:#dc2626;font-weight:700;">❌ Chưa điểm danh LMS</span>',
            missing_att_pkg["html_table"],
        )
        parsed_att = IssueListParser()
        parsed_att.feed(missing_att_pkg["html_table"])
        red_att_items = [
            item for item, style in zip(parsed_att.items, parsed_att.styles) if "#dc2626" in style
        ]
        self.assertEqual(
            red_att_items,
            ["[LMS] Chưa điểm danh", "[LMS] Thiếu nhận xét học viên (2/6 HS)"],
        )


if __name__ == "__main__":
    unittest.main()
