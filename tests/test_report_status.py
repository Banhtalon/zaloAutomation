import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.job_routes import (
    get_job_status,
    get_job_targets,
    get_latest_active_job,
    get_latest_job,
)
from api.zalo_routes import (
    get_weekly_summary,
    get_zalo_scan_plan,
    get_zalo_scan_preflight,
    run_scan,
    send_failed_csv_email_report,
)
from db import database
from repositories.zalo_batch_repository import canonical_target_key
from services.bcnb_actions import _UPDATE_CHECKER_NOTE_JS
from services.bcnb_safety import (
    BCNB_CHECKER_NOTE_HEADER_SUBSTRINGS,
    extract_proven_auto_issues_from_audits,
    match_bcnb_column_indexes,
    merge_checker_issue_note,
    split_checker_issue_note,
)
from services.lms_quality_service import (
    evaluate_lms_slot_quality,
    evaluate_lms_student_comments,
    evaluate_zalo_content_issues,
    fetch_and_evaluate_lms_quality,
)
from services.report_status import (
    CATEGORY_DONE,
    CATEGORY_EXEMPT,
    CATEGORY_LATE,
    CATEGORY_MISSING,
    CATEGORY_NOT_SCANNED,
    CATEGORY_REVIEW,
    ISSUE_CODE_LEGACY_UNCLASSIFIED_FAIL,
    ISSUE_CODE_LMS_MANUAL_CHECK,
    ISSUE_CODE_LMS_QUALITY_FAIL,
    ISSUE_CODE_ZALO_MISSING_OR_WRONG_DATE_SAME_DAY,
    ISSUE_CODE_ZALO_REPORT_NO_DATE,
    ISSUE_CODE_ZALO_REPORT_WRONG_DATE,
    LESSON_14_EXEMPTION,
    LESSON_14_STATUS,
    NOT_FOUND_STATUS,
    NOT_FOUND_VERIFIED,
    SEVERITY_FAIL,
    SEVERITY_NOTE,
    SUBMITTED_LATE_STATUS,
    SUBMITTED_LATE_VERIFIED,
    SUBMITTED_STATUS,
    SUBMITTED_VERIFIED,
    categorize_row,
    coerce_structured_issue,
    identity_tokens,
    is_failed,
    is_lesson_14,
    issue_code,
    issue_severity,
    issue_text,
    make_structured_issue,
    normalize_class_code,
    normalize_lesson_number,
    validate_two_week_plan,
)
from services.zalo_bcnb_service import (
    _collect_row_verifiable_auto_issues,
    is_result_item_already_submitted,
)
from services.zalo_evidence import apply_lesson_14_exemption
from services.zalo_target_matching import parse_bcnb_scan_target
from tests.test_api_routes_unit import FakeContainer


class ReportStatusSingleSourceTests(unittest.IsolatedAsyncioTestCase):
    def test_5_5_1_table_driven_categorize_row_issue_severity_and_is_failed(self) -> None:
        note_lms_issue = make_structured_issue(
            ISSUE_CODE_LMS_MANUAL_CHECK,
            "[LMS] Không tải được đầy đủ dữ liệu buổi học từ LMS, cần kiểm tra thủ công",
        )
        note_zalo_no_date = make_structured_issue(
            ISSUE_CODE_ZALO_REPORT_NO_DATE,
            "[Zalo] Báo cáo không ghi ngày/buổi",
        )
        note_zalo_wrong_date = make_structured_issue(
            ISSUE_CODE_ZALO_REPORT_WRONG_DATE,
            "[Zalo] Báo cáo ghi sai ngày (15/10)",
        )
        fail_lms_issue = make_structured_issue(
            ISSUE_CODE_LMS_QUALITY_FAIL,
            "[LMS] Chưa ghi nội dung bài học",
        )

        self.assertEqual(issue_severity(note_lms_issue), SEVERITY_NOTE)
        self.assertEqual(issue_severity(note_zalo_no_date), SEVERITY_NOTE)
        self.assertEqual(issue_severity(note_zalo_wrong_date), SEVERITY_NOTE)
        self.assertEqual(issue_severity(fail_lms_issue), SEVERITY_FAIL)
        # Legacy string compatibility adapter: D4 & D2 texts and [NOTE:CODE] prefixes -> NOTE, other strings -> FAIL
        self.assertEqual(
            issue_severity(
                "[LMS] Không tải được đầy đủ dữ liệu buổi học từ LMS, cần kiểm tra thủ công"
            ),
            SEVERITY_NOTE,
        )
        self.assertEqual(
            issue_severity("[Zalo] Báo cáo không ghi ngày/buổi"),
            SEVERITY_NOTE,
        )
        self.assertEqual(
            issue_severity("[Zalo] Báo cáo ghi sai ngày (15/10)"),
            SEVERITY_NOTE,
        )
        prefixed_d4 = "[NOTE:LMS_MANUAL_CHECK] [LMS] Chưa kiểm tra được dữ liệu buổi học trên LMS, cần kiểm tra thủ công"
        self.assertEqual(issue_code(prefixed_d4), ISSUE_CODE_LMS_MANUAL_CHECK)
        self.assertEqual(issue_severity(prefixed_d4), SEVERITY_NOTE)
        self.assertEqual(
            issue_text(prefixed_d4),
            "[LMS] Chưa kiểm tra được dữ liệu buổi học trên LMS, cần kiểm tra thủ công",
        )
        prefixed_d2 = "[NOTE:ZALO_MISSING_OR_WRONG_DATE_SAME_DAY] [Zalo] Báo cáo không ghi ngày/buổi"
        self.assertEqual(
            issue_code(prefixed_d2),
            ISSUE_CODE_ZALO_MISSING_OR_WRONG_DATE_SAME_DAY,
        )
        self.assertEqual(issue_severity(prefixed_d2), SEVERITY_NOTE)
        self.assertEqual(
            issue_text(prefixed_d2),
            "[Zalo] Báo cáo không ghi ngày/buổi",
        )
        self.assertEqual(
            issue_severity("[LMS] Chưa ghi nội dung bài học"),
            SEVERITY_FAIL,
        )
        self.assertEqual(
            coerce_structured_issue("[LMS] Chưa ghi nội dung bài học")["code"],
            ISSUE_CODE_LEGACY_UNCLASSIFIED_FAIL,
        )

        cases = [
            (
                "D1_late_verified_not_failed",
                {
                    "status": SUBMITTED_LATE_STATUS,
                    "evidence_state": SUBMITTED_LATE_VERIFIED,
                    "lms_status": "✅ Đã nộp LMS",
                },
                CATEGORY_LATE,
                False,
            ),
            (
                "done_with_only_note_issues_not_failed",
                {
                    "status": SUBMITTED_STATUS,
                    "evidence_state": SUBMITTED_VERIFIED,
                    "lms_status": "✅ Đã nộp LMS",
                    "lms_quality_issues": [note_lms_issue],
                    "zalo_content_issues": [note_zalo_no_date, note_zalo_wrong_date],
                    "issue_description": (
                        "[LMS] Không tải được đầy đủ dữ liệu buổi học từ LMS, cần kiểm tra thủ công; "
                        "[Zalo] Báo cáo không ghi ngày/buổi"
                    ),
                },
                CATEGORY_DONE,
                False,
            ),
            (
                "done_with_fail_issue_is_failed",
                {
                    "status": SUBMITTED_STATUS,
                    "evidence_state": SUBMITTED_VERIFIED,
                    "lms_status": "✅ Đã nộp LMS",
                    "lms_quality_issues": [fail_lms_issue],
                },
                CATEGORY_DONE,
                True,
            ),
            (
                "not_found_verified_is_missing_and_failed",
                {
                    "status": NOT_FOUND_STATUS,
                    "evidence_state": NOT_FOUND_VERIFIED,
                    "lms_status": "✅ Đã nộp LMS",
                },
                CATEGORY_MISSING,
                True,
            ),
            (
                "lesson_14_is_exempt_and_not_failed",
                {
                    "lesson": "14",
                    "status": LESSON_14_STATUS,
                    "evidence_state": LESSON_14_EXEMPTION,
                    "lms_status": "✅ Đã nộp LMS",
                },
                CATEGORY_EXEMPT,
                False,
            ),
            (
                "done_zalo_plus_confirmed_lms_unsubmitted_no_qc_issue_is_failed",
                {
                    "status": SUBMITTED_STATUS,
                    "evidence_state": SUBMITTED_VERIFIED,
                    "lms_status": "❌ Chưa nộp LMS",
                },
                CATEGORY_DONE,
                True,
            ),
            (
                "late_zalo_plus_confirmed_lms_unsubmitted_via_checkbox_is_failed",
                {
                    "status": SUBMITTED_LATE_STATUS,
                    "evidence_state": SUBMITTED_LATE_VERIFIED,
                    "lms_status": "Chưa quét LMS",
                    "nx_lms_checked": False,
                },
                CATEGORY_LATE,
                True,
            ),
            (
                "unscanned_zalo_with_lms_done_is_not_scanned_and_not_failed",
                {
                    "zalo_status": "Chưa quét Zalo",
                    "lms_status": "✅ Đã nộp LMS",
                },
                CATEGORY_NOT_SCANNED,
                False,
            ),
            (
                "unscanned_zalo_with_confirmed_lms_unsubmitted_is_failed",
                {
                    "zalo_status": "Chưa quét Zalo",
                    "lms_status": "❌ Chưa nộp LMS",
                },
                CATEGORY_NOT_SCANNED,
                True,
            ),
        ]

        for name, row, expected_category, expected_failed in cases:
            with self.subTest(name=name):
                self.assertEqual(categorize_row(row), expected_category)
                self.assertEqual(is_failed(row), expected_failed)

    def test_5_5_2_is_lesson_14_variants_and_exemption_guards(self) -> None:
        for valid_val in ("14", "Buổi 14", "014", 14, "  buổi 014 "):
            with self.subTest(valid_val=valid_val):
                self.assertEqual(normalize_lesson_number(valid_val), 14)
                self.assertTrue(is_lesson_14(valid_val))

        for invalid_val in ("1", "4", "140", "2026-10-14", "", None, False, True):
            with self.subTest(invalid_val=invalid_val):
                self.assertFalse(is_lesson_14(invalid_val))

        # Spoofed LESSON_14_EXEMPTION on lesson 2 must be rejected
        spoofed = {
            "lesson": "2",
            "evidence_state": LESSON_14_EXEMPTION,
        }
        self.assertFalse(is_lesson_14(spoofed))

        # apply_lesson_14_exemption rejects non-14 lesson
        with self.assertRaises(ValueError):
            apply_lesson_14_exemption(
                None,
                class_code="VT-C4K-PTB16",
                applied_at=datetime.now(timezone.utc),
                lesson="12",
            )

        # evaluate_lms_slot_quality, evaluate_lms_student_comments, and evaluate_zalo_content_issues use is_lesson_14
        for variant in ("14", "Buổi 14", "014", 14):
            eval_res = evaluate_lms_slot_quality(
                {"summary": "", "studentAttendance": []},
                class_code="VT-C4K-PTB16",
                khoi="Coding",
                lesson=variant,
            )
            self.assertTrue(eval_res["is_exempt"])
            self.assertEqual(eval_res["issues"], [])
            comments_res = evaluate_lms_student_comments(
                [],
                is_checkpoint=False,
                lesson=variant,
            )
            self.assertTrue(comments_res["is_exempt"])
            self.assertEqual(comments_res["issues"], [])
            zalo_issues = evaluate_zalo_content_issues(
                {
                    "evidence_state": SUBMITTED_VERIFIED,
                    "scan_target": {"lesson": variant},
                    "evidence": {
                        "date_context": {
                            "target_lesson": variant,
                            "content_lesson": "2",
                        }
                    },
                }
            )
            self.assertEqual(zalo_issues, [])

        # is_result_item_already_submitted uses evidence_state / categorize_row
        self.assertTrue(
            is_result_item_already_submitted(
                {"evidence_state": SUBMITTED_LATE_VERIFIED}
            )
        )
        self.assertTrue(
            is_result_item_already_submitted(
                {"scan_target": {"lesson": "Buổi 14"}}
            )
        )
        self.assertFalse(
            is_result_item_already_submitted(
                {"evidence_state": NOT_FOUND_VERIFIED, "status": NOT_FOUND_STATUS}
            )
        )

    async def test_5_5_1_and_5_5_3_and_5_5_4_db_summary_email_and_job_api_contracts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "test_report_status.db")
            with patch.object(database, "DB_PATH", db_path):
                database.init_db()

                note_issue = make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Không tải được đầy đủ dữ liệu buổi học từ LMS, cần kiểm tra thủ công",
                )
                d2_note_issue = make_structured_issue(
                    ISSUE_CODE_ZALO_REPORT_NO_DATE,
                    "[Zalo] Báo cáo không ghi ngày/buổi",
                )
                fail_issue = make_structured_issue(
                    ISSUE_CODE_LMS_QUALITY_FAIL,
                    "[LMS] Chưa ghi nội dung bài học",
                )

                scan_payload = {
                    "snapshot_id": 55,
                    "required_week": {"week": 42, "year": 2026},
                    "items": [
                        {
                            "group": "VT-C4K-NOTE01",
                            "person": "GV Note",
                            "status": SUBMITTED_STATUS,
                            "evidence_state": SUBMITTED_VERIFIED,
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "lms_quality_issues": [note_issue],
                            "zalo_content_issues": [d2_note_issue],
                            "quality_issues": [note_issue, d2_note_issue],
                            "scan_target": {
                                "week": 42,
                                "year": 2026,
                                "class_code": "VT-C4K-NOTE01",
                                "lesson": "3",
                                "class_date": "15/10/2026",
                                "class_date_iso": "2026-10-15",
                            },
                            "evidence": {"scanned_at": "2026-10-15T14:00:00Z"},
                        },
                        {
                            "group": "VT-C4K-LATE01",
                            "person": "GV Trễ",
                            "status": SUBMITTED_LATE_STATUS,
                            "evidence_state": SUBMITTED_LATE_VERIFIED,
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "scan_target": {
                                "week": 42,
                                "year": 2026,
                                "class_code": "VT-C4K-LATE01",
                                "lesson": "4",
                                "class_date": "16/10/2026",
                                "class_date_iso": "2026-10-16",
                            },
                            "evidence": {"scanned_at": "2026-10-17T09:00:00Z"},
                        },
                        {
                            "group": "VT-C4K-FAIL01",
                            "person": "GV Lỗi Nội Dung",
                            "status": SUBMITTED_STATUS,
                            "evidence_state": SUBMITTED_VERIFIED,
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "lms_quality_issues": [fail_issue],
                            "quality_issues": [fail_issue],
                            "scan_target": {
                                "week": 42,
                                "year": 2026,
                                "class_code": "VT-C4K-FAIL01",
                                "lesson": "5",
                                "class_date": "17/10/2026",
                                "class_date_iso": "2026-10-17",
                            },
                            "evidence": {"scanned_at": "2026-10-17T15:00:00Z"},
                        },
                        {
                            "group": "VT-C4K-EXEMPT14",
                            "person": "GV Miễn",
                            "status": LESSON_14_STATUS,
                            "evidence_state": LESSON_14_EXEMPTION,
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "scan_target": {
                                "week": 42,
                                "year": 2026,
                                "class_code": "VT-C4K-EXEMPT14",
                                "lesson": "Buổi 14",
                                "class_date": "18/10/2026",
                                "class_date_iso": "2026-10-18",
                            },
                            "evidence": {"scanned_at": "2026-10-18T15:00:00Z"},
                        },
                    ],
                }

                async with database.get_db_connection() as db:
                    await db.execute(
                        """
                        INSERT INTO scan_jobs (
                            id, job_type, status, created_at, finished_at,
                            results, request_payload, mutates_external
                        )
                        VALUES (
                            501, 'ZALO_SCAN', 'SUCCESS',
                            '2026-10-18T16:00:00Z', '2026-10-18T16:05:00Z',
                            ?, '{"execution_mode":"production"}', 1
                        )
                        """,
                        (json.dumps(scan_payload, ensure_ascii=False),),
                    )
                    await db.commit()

                container = FakeContainer()
                from repositories.report_repository import ReportRepository

                container.report_repo = ReportRepository()
                summary_resp = await get_weekly_summary(
                    week=42, year=2026, container=container
                )

                rows_by_code = {
                    r["class_code"]: r for r in summary_resp["rows"]
                }
                items_by_code = {
                    it["scan_target"]["class_code"]: it
                    for it in summary_resp["items"]
                }

                # 1. NOTE row: plain text in issue_description, structured issue preserved, is_failed=False
                note_row = rows_by_code["VT-C4K-NOTE01"]
                self.assertEqual(note_row["category"], CATEGORY_DONE)
                self.assertFalse(note_row["is_failed"])
                self.assertEqual(
                    note_row["issue_description"],
                    "[LMS] Không tải được đầy đủ dữ liệu buổi học từ LMS, cần kiểm tra thủ công; "
                    "[Zalo] Báo cáo không ghi ngày/buổi",
                )
                self.assertNotIn("{'code'", note_row["issue_description"])
                self.assertEqual(
                    note_row["lms_quality_issues"][0]["code"],
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                )
                self.assertEqual(
                    items_by_code["VT-C4K-NOTE01"]["category"], CATEGORY_DONE
                )
                self.assertFalse(items_by_code["VT-C4K-NOTE01"]["is_failed"])

                # 2. D1 LATE row: category=LATE, is_failed=False, counted in zalo_late and zalo_reported, NOT zalo_pending
                late_row = rows_by_code["VT-C4K-LATE01"]
                self.assertEqual(late_row["category"], CATEGORY_LATE)
                self.assertFalse(late_row["is_failed"])
                self.assertEqual(summary_resp["summary"]["zalo_completed"], 2)
                self.assertEqual(summary_resp["summary"]["zalo_reported"], 3)
                self.assertEqual(summary_resp["summary"]["zalo_late"], 1)
                self.assertEqual(summary_resp["summary"]["zalo_exempt"], 1)
                self.assertEqual(summary_resp["summary"]["zalo_pending"], 0)

                # 3. FAIL row: category=DONE, is_failed=True, only row included in failed email
                fail_row = rows_by_code["VT-C4K-FAIL01"]
                self.assertEqual(fail_row["category"], CATEGORY_DONE)
                self.assertTrue(fail_row["is_failed"])
                self.assertEqual(
                    fail_row["issue_description"],
                    "[LMS] Chưa ghi nội dung bài học",
                )

                email_resp = await send_failed_csv_email_report(
                    week=42,
                    year=2026,
                    container=container,
                )
                self.assertEqual(email_resp["failed_count"], 1)
                self.assertIn("VT-C4K-FAIL01", email_resp["text_body"])
                self.assertNotIn("VT-C4K-NOTE01", email_resp["text_body"])
                self.assertNotIn("VT-C4K-LATE01", email_resp["text_body"])
                self.assertIn(
                    "[LMS] Chưa ghi nội dung bài học", email_resp["text_body"]
                )

                # 4. Audit provenance & Teaching note merge preserve plain text and structured metadata
                merged_note = merge_checker_issue_note(
                    "Ghi chú tay QC",
                    [d2_note_issue, fail_issue],
                )
                self.assertEqual(
                    merged_note,
                    "Ghi chú tay QC | [Zalo] Báo cáo không ghi ngày/buổi; [LMS] Chưa ghi nội dung bài học",
                )
                proven = extract_proven_auto_issues_from_audits(
                    [
                        {
                            "action_type": "BCNB_UPDATE_CHECKER_NOTE",
                            "target_id": "VT-C4K-NOTE01",
                            "mode": "production",
                            "outcome": "VERIFIED",
                            "reason": {
                                "class_code": "VT-C4K-NOTE01",
                                "lesson": "3",
                                "week": 42,
                                "year": 2026,
                                "bcnb_session_id": "9901",
                                "auto_issues": [d2_note_issue, fail_issue],
                            },
                            "before_state": {
                                "bcnb_session_id": "9901",
                                "qc_issue_note": "Ghi chú tay QC",
                            },
                            "after_state": {
                                "bcnb_session_id": "9901",
                                "post_verified": True,
                                "qc_issue_note": merged_note,
                            },
                        }
                    ],
                    class_code="VT-C4K-NOTE01",
                    lesson="3",
                    week=42,
                    year=2026,
                    session_id="9901",
                )
                self.assertEqual(
                    proven,
                    [
                        "[Zalo] Báo cáo không ghi ngày/buổi",
                        "[LMS] Chưa ghi nội dung bài học",
                    ],
                )
                self.assertEqual(issue_code(proven[0]), ISSUE_CODE_ZALO_REPORT_NO_DATE)
                self.assertEqual(issue_severity(proven[0]), SEVERITY_NOTE)
                self.assertEqual(issue_code(proven[1]), ISSUE_CODE_LMS_QUALITY_FAIL)
                self.assertEqual(issue_severity(proven[1]), SEVERITY_FAIL)

                # Full round-trip: audit -> split_checker_issue_note -> _collect_row_verifiable_auto_issues
                # -> inspect_and_attach_quality_issues -> DB -> get_weekly_summary
                from services.lms_quality_service import (
                    inspect_and_attach_quality_issues,
                )

                manual_part, split_auto = split_checker_issue_note(
                    "[Zalo] Báo cáo không ghi ngày/buổi",
                    proven_auto_issues=[proven[0]],
                )
                self.assertEqual(manual_part, "")
                self.assertEqual(len(split_auto), 1)
                self.assertEqual(issue_code(split_auto[0]), ISSUE_CODE_ZALO_REPORT_NO_DATE)
                self.assertEqual(issue_severity(split_auto[0]), SEVERITY_NOTE)

                restored_result: dict[str, object] = {
                    "group": "VT-C4K-RESTORE01",
                    "person": "GV Restore",
                    "status": "✅ Đã tích trên BCNB",
                    "evidence_state": "ALREADY_SUBMITTED_BCNB",
                    "nx_zalo_checked": True,
                    "nx_lms_checked": True,
                    "qc_issue_note": "[Zalo] Báo cáo không ghi ngày/buổi",
                    "scan_target": {
                        "week": 42,
                        "year": 2026,
                        "class_code": "VT-C4K-RESTORE01",
                        "lesson": "3",
                        "class_date": "15/10/2026",
                        "class_date_iso": "2026-10-15",
                    },
                    "evidence": {"scanned_at": "2026-10-15T14:00:00Z"},
                }
                eff_auto, _ = _collect_row_verifiable_auto_issues(
                    restored_result,
                    existing_note="[Zalo] Báo cáo không ghi ngày/buổi",
                    lms_slots_by_class=None,
                    proven_auto_issues=[proven[0]],
                )
                self.assertEqual(len(eff_auto), 1)
                self.assertEqual(issue_code(eff_auto[0]), ISSUE_CODE_ZALO_REPORT_NO_DATE)
                self.assertEqual(issue_severity(eff_auto[0]), SEVERITY_NOTE)
                inspect_and_attach_quality_issues(
                    [restored_result],
                    lms_slots_by_class=None,
                )
                restored_qi = restored_result.get("quality_issues")
                self.assertTrue(isinstance(restored_qi, list) and len(restored_qi) == 1)
                assert isinstance(restored_qi, list)
                self.assertEqual(
                    restored_qi[0],
                    {
                        "code": ISSUE_CODE_ZALO_REPORT_NO_DATE,
                        "severity": SEVERITY_NOTE,
                        "text": "[Zalo] Báo cáo không ghi ngày/buổi",
                    },
                )

                # Explicit [FAIL:CODE] prefix on issue_description is authoritative and never downgraded to NOTE (even if qc_issue_note has uncoded NOTE text)
                self.assertEqual(
                    issue_severity("[FAIL:ZALO_CONTENT_FAIL] [Zalo] Báo cáo không ghi ngày/buổi"),
                    SEVERITY_FAIL,
                )
                self.assertTrue(
                    is_failed(
                        {
                            "category": CATEGORY_DONE,
                            "zalo_status": SUBMITTED_STATUS,
                            "evidence_state": SUBMITTED_VERIFIED,
                            "lms_status": "✅ Đã tích trên BCNB",
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "qc_issue_note": "[Zalo] Báo cáo không ghi ngày/buổi",
                            "issue_description": "[FAIL:ZALO_CONTENT_FAIL] [Zalo] Báo cáo không ghi ngày/buổi",
                        }
                    )
                )

                # Persist restored_result and ledger-failed target (VT-C4K-UNK01) into DB and verify weekly summary parity
                unk_item: dict[str, object] = {
                    "group": "VT-C4K-UNK01",
                    "person": "GV Unknown Write",
                    "status": SUBMITTED_STATUS,
                    "evidence_state": SUBMITTED_VERIFIED,
                    "bcnb_update_state": "VERIFIED",
                    "nx_zalo_checked": False,
                    "nx_lms_checked": True,
                    "scan_target": {
                        "week": 42,
                        "year": 2026,
                        "class_code": "VT-C4K-UNK01",
                        "lesson": "5",
                        "class_date": "17/10/2026",
                        "class_date_iso": "2026-10-17",
                    },
                    "evidence": {"scanned_at": "2026-10-17T16:00:00Z"},
                }
                roundtrip_payload = json.loads(json.dumps(scan_payload))
                roundtrip_payload["items"].append(restored_result)
                roundtrip_payload["items"].append(unk_item)
                async with database.get_db_connection() as db:
                    await db.execute(
                        "UPDATE scan_jobs SET results = ? WHERE id = 501",
                        (json.dumps(roundtrip_payload, ensure_ascii=False),),
                    )
                    await db.execute(
                        """
                        INSERT INTO zalo_batch_targets (
                            job_id, target_key, group_name, person, class_code,
                            week, year, lesson, class_date, class_date_iso,
                            source_json, scan_status, write_status, result_json,
                            created_at, updated_at
                        )
                        VALUES (
                            501, '2026-W42|VT-C4K-UNK01|5|2026-10-17',
                            'VT-C4K-UNK01', 'GV Unknown Write', 'VT-C4K-UNK01',
                            42, 2026, '5', '17/10/2026', '2026-10-17',
                            '{}', 'VERIFIED', 'OUTCOME_UNKNOWN', ?,
                            '2026-10-18T16:00:00Z', '2026-10-18T16:05:00Z'
                        )
                        """,
                        (json.dumps(unk_item, ensure_ascii=False),),
                    )
                    await db.commit()

                roundtrip_summary = await get_weekly_summary(
                    week=42, year=2026, container=container
                )
                rt_rows = {
                    r["class_code"]: r for r in roundtrip_summary["rows"]
                }
                self.assertEqual(rt_rows["VT-C4K-RESTORE01"]["category"], CATEGORY_DONE)
                self.assertFalse(rt_rows["VT-C4K-RESTORE01"]["is_failed"])
                self.assertEqual(
                    rt_rows["VT-C4K-RESTORE01"]["quality_issues"],
                    [
                        {
                            "code": ISSUE_CODE_ZALO_REPORT_NO_DATE,
                            "severity": SEVERITY_NOTE,
                            "text": "[Zalo] Báo cáo không ghi ngày/buổi",
                        }
                    ],
                )
                self.assertEqual(rt_rows["VT-C4K-UNK01"]["category"], CATEGORY_REVIEW)
                self.assertTrue(rt_rows["VT-C4K-UNK01"]["is_failed"])

                # 5. Job API routes attach category and is_failed (including write_status override on targets)
                container.job_repo.get_job = AsyncMock(
                    return_value={
                        "id": 501,
                        "job_type": "ZALO_SCAN",
                        "status": "SUCCESS",
                        "results": json.loads(json.dumps(scan_payload)),
                    }
                )
                container.job_repo.get_latest_job = AsyncMock(
                    return_value={
                        "id": 501,
                        "job_type": "ZALO_SCAN",
                        "status": "SUCCESS",
                        "results": json.loads(json.dumps(scan_payload)),
                    }
                )
                container.zalo_batch_repo.list_for_job = AsyncMock(
                    return_value=[
                        {
                            "id": 1,
                            "class_code": "VT-C4K-LATE01",
                            "lesson": "4",
                            "class_date": "16/10/2026",
                            "scan_status": "DONE",
                            "write_status": "VERIFIED",
                            "result": {
                                "status": SUBMITTED_LATE_STATUS,
                                "evidence_state": SUBMITTED_LATE_VERIFIED,
                            },
                        },
                        {
                            "id": 2,
                            "class_code": "VT-C4K-UNK01",
                            "lesson": "5",
                            "class_date": "17/10/2026",
                            "scan_status": "SCANNED",
                            "write_status": "OUTCOME_UNKNOWN",
                            "result": {
                                "status": SUBMITTED_STATUS,
                                "evidence_state": SUBMITTED_VERIFIED,
                                "bcnb_update_state": "VERIFIED",
                            },
                        },
                    ]
                )
                for route_coro in (
                    get_job_status(501, container),
                    get_latest_job(container),
                    get_latest_active_job(container),
                ):
                    job_data = await route_coro
                    items = job_data["results"]["items"]
                    self.assertEqual(items[0]["category"], CATEGORY_DONE)
                    self.assertFalse(items[0]["is_failed"])
                    self.assertEqual(items[1]["category"], CATEGORY_LATE)
                    self.assertFalse(items[1]["is_failed"])
                    self.assertEqual(items[2]["category"], CATEGORY_DONE)
                    self.assertTrue(items[2]["is_failed"])
                    self.assertEqual(items[3]["category"], CATEGORY_EXEMPT)
                    self.assertFalse(items[3]["is_failed"])

                targets_data = await get_job_targets(501, container)
                self.assertEqual(
                    targets_data["targets"][0]["category"], CATEGORY_LATE
                )
                self.assertFalse(targets_data["targets"][0]["is_failed"])
                self.assertEqual(
                    targets_data["targets"][1]["category"], CATEGORY_REVIEW
                )
                self.assertTrue(targets_data["targets"][1]["is_failed"])
                self.assertEqual(
                    targets_data["targets"][1]["result"]["category"],
                    CATEGORY_REVIEW,
                )
                self.assertTrue(
                    targets_data["targets"][1]["result"]["is_failed"]
                )
                self.assertEqual(
                    targets_data["targets"][0]["result"]["category"],
                    CATEGORY_LATE,
                )

    async def test_5_5_5_validate_two_week_plan_shared_across_preflight_scan_plan_and_run_scan(
        self,
    ) -> None:
        from core.time_utils import utc_iso

        base_container = FakeContainer()
        valid_snap = json.loads(json.dumps(base_container.group_repo.snapshot))
        valid_snap["observed_at"] = utc_iso()

        def _mutated_snap(mutator):
            s = json.loads(json.dumps(valid_snap))
            mutator(s)
            return s

        invalid_cases = [
            ("SCAN_PLAN_REQUIRED", None),
            (
                "SCAN_PLAN_NOT_TRUSTED",
                _mutated_snap(lambda s: s.update({"status": "REJECTED"})),
            ),
            (
                "SCAN_PLAN_INCOMPLETE",
                _mutated_snap(
                    lambda s: s["plan"]["bcnb_week_plan"].update(
                        {"selected_weeks": [{"week": 32, "year": 2026}]}
                    )
                ),
            ),
            (
                "SCAN_PLAN_INVALID_TARGET",
                _mutated_snap(
                    lambda s: s["plan"].update({"scan_targets": [{"week": 32}]})
                ),
            ),
            (
                "SCAN_PLAN_DUPLICATE_TARGET",
                _mutated_snap(
                    lambda s: s["plan"]["scan_targets"].append(
                        dict(s["plan"]["scan_targets"][0])
                    )
                ),
            ),
            (
                "SCAN_PLAN_STALE",
                _mutated_snap(
                    lambda s: s.update(
                        {"observed_at": "2020-01-01T00:00:00+00:00"}
                    )
                ),
            ),
            (
                "SCAN_TARGETS_DEFERRED",
                _mutated_snap(
                    lambda s: s["plan"].update(
                        {
                            "scan_targets": [],
                            "deferred_targets": [
                                {
                                    **s["plan"]["scan_targets"][0],
                                    "week": 33,
                                    "year": 2026,
                                    "class_date": "31/12/2099",
                                    "class_date_iso": "2099-12-31",
                                }
                            ],
                        }
                    )
                ),
            ),
        ]

        for expected_code, snap in invalid_cases:
            with self.subTest(expected_code=expected_code):
                container = FakeContainer()
                container.settings.zalo_plan_max_age_minutes = 30
                container.group_repo.snapshot = snap

                val = validate_two_week_plan(snap, max_age_minutes=30)
                self.assertFalse(val["valid"])
                self.assertEqual(val["error_code"], expected_code)

                plan_resp = await get_zalo_scan_plan(container)
                self.assertFalse(plan_resp["available"])
                if snap is not None:
                    self.assertEqual(plan_resp["reason_code"], expected_code)

                preflight_resp = await get_zalo_scan_preflight(container)
                self.assertEqual(preflight_resp["status"], "RED")
                self.assertFalse(preflight_resp["run_allowed"])
                self.assertTrue(
                    any(
                        c["code"] == expected_code and c["status"] == "RED"
                        for c in preflight_resp["checks"]
                    )
                )

                with self.assertRaises(HTTPException) as caught:
                    await run_scan(container)
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(
                    caught.exception.detail["code"], expected_code
                )

        # RED takes precedence over YELLOW in preflight
        red_and_yellow_container = FakeContainer()
        red_and_yellow_container.settings.bcnb_dry_run = False
        red_and_yellow_container.audit_repo.count_unresolved = AsyncMock(
            return_value=2
        )
        preflight_ry = await get_zalo_scan_preflight(red_and_yellow_container)
        self.assertEqual(preflight_ry["status"], "RED")
        self.assertFalse(preflight_ry["requires_confirmation"])

    def test_5_5_6_normalize_class_code_and_parse_bcnb_scan_target_prefers_iso(
        self,
    ) -> None:
        self.assertEqual(
            identity_tokens("  vt – c4k _ ptb16 "),
            ("VT", "C4K", "PTB16"),
        )
        self.assertEqual(
            normalize_class_code("  vt – c4k _ ptb16 "),
            "VT-C4K-PTB16",
        )
        self.assertEqual(
            canonical_target_key(
                {
                    "week": 42,
                    "year": 2026,
                    "class_code": " vt – c4k _ ptb16 ",
                    "lesson": "5",
                    "class_date_iso": "2026-10-17",
                }
            ),
            "2026-W42|VT-C4K-PTB16|5|2026-10-17",
        )

        # parse_bcnb_scan_target prefers class_date_iso over class_date
        tz = timezone.utc
        parsed = parse_bcnb_scan_target(
            {
                "week": 42,
                "year": 2026,
                "class_code": "vt_c4k_ptb16",
                "lesson": "5",
                "class_date": "01/01/2026",
                "class_date_iso": "2026-10-17",
                "zalo_deadline": "18/10/2026",
            },
            default_year=2026,
            timezone_info=tz,
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.class_code, "VT-C4K-PTB16")
        self.assertEqual(
            parsed.class_date.strftime("%Y-%m-%d"),
            "2026-10-17",
        )

    async def test_5_5_7_shared_bcnb_column_matcher_and_lms_fetch_helper(self) -> None:
        headers = [
            "STT",
            "Mã lớp",
            "Giảng viên",
            "Buổi thứ",
            "Thời gian học",
            "Deadline nhận xét Zalo",
            "NX Zalo",
            "Deadline nhận xét LMS",
            "NX LMS",
            "Tình trạng đánh giá",
            "Mô tả vấn đề (TE/TF/QC)",
        ]
        matched_cols = match_bcnb_column_indexes(headers)
        self.assertEqual(matched_cols["class_indexes"], [1])
        self.assertEqual(matched_cols["checker_note_indexes"], [10])
        # JS script in bcnb_actions uses the exact shared constants from bcnb_safety
        for sub in BCNB_CHECKER_NOTE_HEADER_SUBSTRINGS:
            self.assertIn(sub, _UPDATE_CHECKER_NOTE_JS)

        # Shared fetch_and_evaluate_lms_quality helper
        fake_gw = AsyncMock()
        fake_gw.fetch_class_slots = AsyncMock(
            return_value={
                "VT-C4K-PTB16": [
                    {
                        "index": 0,
                        "date": "2026-10-16T17:00:00.000Z",
                        "summary": "",
                        "studentAttendance": [
                            {
                                "status": "ATTENDED",
                                "comment": (
                                    "Điểm mạnh: Con hiểu bài.\n"
                                    "Điểm cần cải thiện: Chú ý cú pháp.\n"
                                    "Lời khuyên: Ôn bài ở nhà."
                                ),
                            }
                        ],
                    }
                ]
            }
        )
        results = [
            {
                "scan_target": {
                    "class_code": "vt-c4k-ptb16",
                    "lesson": "1",
                    "class_date_iso": "2026-10-17",
                    "khoi": "Coding",
                }
            }
        ]
        resolved = await fetch_and_evaluate_lms_quality(
            page=None,
            scan_results=results,
            lms_quality_gateway=fake_gw,
        )
        self.assertIn("VT-C4K-PTB16", resolved or {})
        self.assertEqual(
            results[0]["lms_quality_issues"],
            ["[LMS] Chưa ghi nội dung bài học"],
        )

        # _collect_row_verifiable_auto_issues preserves structured D2 and D4 NOTE issues,
        # and suppresses LMS_MANUAL_CHECK only when LMS is unavailable system-wide (5.8.5)
        d2_note = make_structured_issue(
            ISSUE_CODE_ZALO_REPORT_NO_DATE,
            "[Zalo] Báo cáo không ghi ngày/buổi",
        )
        d4_note = make_structured_issue(
            ISSUE_CODE_LMS_MANUAL_CHECK,
            "[LMS] Không tải được đầy đủ dữ liệu buổi học từ LMS, cần kiểm tra thủ công",
        )
        effective_auto, should_sync = _collect_row_verifiable_auto_issues(
            {
                "evidence_state": SUBMITTED_VERIFIED,
                "zalo_content_issues": [d2_note],
                "lms_quality_issues": [d4_note],
            },
            existing_note="",
            lms_slots_by_class={
                "VT-C4K-PTB16": [
                    {"index": 0, "date": "2026-10-17T17:00:00.000Z"}
                ]
            },
        )
        self.assertTrue(should_sync)
        self.assertEqual(effective_auto, [d2_note, d4_note])

        effective_outage, should_sync_outage = _collect_row_verifiable_auto_issues(
            {
                "evidence_state": SUBMITTED_VERIFIED,
                "zalo_content_issues": [d2_note],
                "lms_quality_issues": [d4_note],
            },
            existing_note="",
            lms_slots_by_class={"VT-C4K-PTB16": [{"fetch_error": True}]},
        )
        self.assertTrue(should_sync_outage)
        self.assertEqual(effective_outage, [d2_note])


if __name__ == "__main__":
    unittest.main()
