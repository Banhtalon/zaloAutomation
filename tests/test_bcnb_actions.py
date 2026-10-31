from datetime import datetime
import json
import subprocess
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.time_utils import BUSINESS_TIMEZONE
from services.bcnb_actions import (
    _UPDATE_CHECKER_NOTE_JS,
    BcnbBeforeWriteError,
    BcnbConflictAfterWriteError,
    BcnbOutcomeUnknownError,
    execute_verified_checkbox_action,
    execute_verified_checker_note_action,
    merge_checker_issue_note,
    split_checker_issue_note,
)


class BcnbActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        log_patcher = patch("services.bcnb_actions.log_message")
        self.mock_log_message = log_patcher.start()
        self.addCleanup(log_patcher.stop)
        bcnb_log_patcher = patch("services.zalo_bcnb_service.log_message")
        self.mock_bcnb_log_message = bcnb_log_patcher.start()
        self.addCleanup(bcnb_log_patcher.stop)

    def make_dependencies(self):
        checkbox = AsyncMock()
        checkbox.is_checked.return_value = False
        confirm = AsyncMock()
        confirm.count.return_value = 0
        page = MagicMock()
        page.locator.return_value.first = confirm
        audit_repo = AsyncMock()
        audit_repo.record_intent.return_value = (
            {"id": 7, "outcome": "INTENT_RECORDED"},
            True,
        )
        job_repo = AsyncMock()
        return page, checkbox, audit_repo, job_repo

    async def execute(self, persisted):
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        readback = AsyncMock()
        if isinstance(persisted, BaseException):
            readback.side_effect = persisted
        else:
            readback.return_value = persisted
        with (
            patch("services.bcnb_actions.verify_checkbox_persisted", readback),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            result = await execute_verified_checkbox_action(
                page=page,
                checkbox=checkbox,
                audit_repo=audit_repo,
                job_repo=job_repo,
                job_id=3,
                action_key="test-action",
                action_type="BCNB_REPORT_TICK",
                class_code="VT-JSB14",
                column_kind="report",
                week=27,
                year=2026,
                lesson="14",
                execution_mode="production",
                reason={"test": True},
            )
        return result, audit_repo

    async def test_verified_is_recorded_only_after_server_readback(self):
        result, audit_repo = await self.execute(True)
        self.assertEqual(result, "VERIFIED")
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs["outcome"], "VERIFIED"
        )

    async def test_unchecked_server_readback_is_failed(self):
        with self.assertRaises(RuntimeError):
            await self.execute(False)

    async def test_unreadable_server_state_is_unknown_not_verified(self):
        with self.assertRaises(RuntimeError):
            await self.execute(RuntimeError("reload failed"))
        # Exercise again to inspect the terminal audit call without hiding the exception.
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        with (
            patch(
                "services.bcnb_actions.verify_checkbox_persisted",
                AsyncMock(side_effect=RuntimeError("reload failed")),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(RuntimeError):
                await execute_verified_checkbox_action(
                    page=page,
                    checkbox=checkbox,
                    audit_repo=audit_repo,
                    job_repo=job_repo,
                    job_id=3,
                    action_key="test-action",
                    action_type="BCNB_REPORT_TICK",
                    class_code="VT-JSB14",
                    column_kind="report",
                    week=27,
                    year=2026,
                    lesson="14",
                    execution_mode="production",
                    reason={"test": True},
                )
        outcomes = [call.kwargs["outcome"] for call in audit_repo.complete_action.await_args_list]
        self.assertEqual(outcomes, ["UNKNOWN"])

    async def test_evidence_path_without_sha256_is_rejected_before_intent(self):
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        with self.assertRaises(ValueError):
            await execute_verified_checkbox_action(
                page=page,
                checkbox=checkbox,
                audit_repo=audit_repo,
                job_repo=job_repo,
                job_id=3,
                action_key="test-action",
                action_type="BCNB_REPORT_TICK",
                class_code="VT-JSB14",
                column_kind="report",
                week=27,
                year=2026,
                lesson="14",
                execution_mode="dry-run",
                reason={"test": True},
                evidence_path="/static/proof.png",
            )
        audit_repo.record_intent.assert_not_awaited()

    async def test_sha256_is_written_directly_to_bcnb_audit(self):
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        evidence_repo = AsyncMock()
        evidence_repo.get_by_public_path.return_value = None
        with patch("services.bcnb_actions.EvidenceRepository", return_value=evidence_repo):
            await execute_verified_checkbox_action(
                page=page,
                checkbox=checkbox,
                audit_repo=audit_repo,
                job_repo=job_repo,
                job_id=3,
                action_key="test-action",
                action_type="BCNB_REPORT_TICK",
                class_code="VT-JSB14",
                column_kind="report",
                week=27,
                year=2026,
                lesson="14",
                execution_mode="dry-run",
                reason={"test": True},
                evidence_path="/static/proof.png",
                evidence_sha256="B" * 64,
            )
        self.assertEqual(
            audit_repo.record_intent.await_args.kwargs["evidence_sha256"],
            "B" * 64,
        )

    async def test_dry_run_never_clicks_checkbox(self):
        page, checkbox, audit_repo, job_repo = self.make_dependencies()

        result = await execute_verified_checkbox_action(
            page=page,
            checkbox=checkbox,
            audit_repo=audit_repo,
            job_repo=job_repo,
            job_id=3,
            action_key="dry-run-action",
            action_type="BCNB_REPORT_TICK",
            class_code="VT-JSB14",
            column_kind="report",
            week=27,
            year=2026,
            lesson="14",
            execution_mode="dry-run",
            reason={"test": True},
        )

        self.assertEqual(result, "DRY_RUN")
        checkbox.check.assert_not_awaited()
        job_repo.ensure_not_cancelled.assert_not_awaited()
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs,
            {"outcome": "DRY_RUN", "after_state": {"checked": False}},
        )

    async def test_already_checked_production_state_is_audited_without_clicking(self):
        page, checkbox, audit_repo, job_repo = self.make_dependencies()
        checkbox.is_checked.return_value = True
        result = await execute_verified_checkbox_action(
            page=page,
            checkbox=checkbox,
            audit_repo=audit_repo,
            job_repo=job_repo,
            job_id=3,
            action_key="lesson-14-observation",
            action_type="BCNB_REPORT_TICK",
            class_code="VT-JSB14",
            column_kind="report",
            week=27,
            year=2026,
            lesson="14",
            execution_mode="production",
            reason={"lesson_14_exemption": True},
        )
        self.assertEqual(result, "VERIFIED")
        checkbox.check.assert_not_awaited()
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs,
            {"outcome": "VERIFIED", "after_state": {"checked": True}},
        )

    def test_merge_and_split_checker_issue_note_preserve_manual_and_nested_brackets(self):
        self.assertEqual(split_checker_issue_note("+ Ghi chú QC"), ("", []))
        sample_auto = [
            "[LMS] Chưa ghi nội dung bài học",
            "[Zalo] Báo cáo Zalo ghi sai ngày học (ghi 23/10/2026, lịch BCNB 24/10/2026)",
        ]
        merged_1 = merge_checker_issue_note(
            "GV báo xin nộp muộn [đã duyệt]",
            sample_auto,
        )
        self.assertEqual(
            merged_1,
            "GV báo xin nộp muộn [đã duyệt] | [LMS] Chưa ghi nội dung bài học; [Zalo] Báo cáo Zalo ghi sai ngày học (ghi 23/10/2026, lịch BCNB 24/10/2026)",
        )
        # Idempotent re-merge (even when proven_auto_issues is passed or deduplicated against existing)
        merged_2 = merge_checker_issue_note(
            merged_1,
            sample_auto,
            proven_auto_issues=sample_auto,
        )
        self.assertEqual(merged_2, merged_1)

        # Without provenance, direct [LMS]/[Zalo] segments are treated as manual and preserved
        manual_unproven, auto_unproven = split_checker_issue_note(merged_2)
        self.assertEqual(manual_unproven, merged_2)
        self.assertEqual(auto_unproven, [])
        self.assertEqual(merge_checker_issue_note(merged_2, []), merged_2)

        # With provenance (5.2.4), proven auto segments are split and can be cleared
        manual, auto_items = split_checker_issue_note(
            merged_2,
            proven_auto_issues=sample_auto,
        )
        self.assertEqual(manual, "GV báo xin nộp muộn [đã duyệt]")
        self.assertEqual(len(auto_items), 2)

        # Clearing resolved auto issues with provenance preserves manual note
        cleared = merge_checker_issue_note(
            merged_2,
            [],
            proven_auto_issues=sample_auto,
        )
        self.assertEqual(cleared, "GV báo xin nộp muộn [đã duyệt]")

        # Legacy [Auto QC: ...] note without provenance is preserved byte-for-byte; with provenance it is unwrapped
        legacy_unproven = merge_checker_issue_note(
            "[Auto QC: [LMS] Chưa ghi nội dung bài học]",
            [],
        )
        self.assertEqual(legacy_unproven, "[Auto QC: [LMS] Chưa ghi nội dung bài học]")
        legacy_cleared = merge_checker_issue_note(
            "[Auto QC: [LMS] Chưa ghi nội dung bài học]",
            [],
            proven_auto_issues=["[LMS] Chưa ghi nội dung bài học"],
        )
        self.assertEqual(legacy_cleared, "")
        legacy_mixed = merge_checker_issue_note(
            "[Auto QC: Câu không tag; [LMS] Chưa ghi nội dung bài học]",
            [],
            proven_auto_issues=["[LMS] Chưa ghi nội dung bài học"],
        )
        self.assertEqual(legacy_mixed, "[Auto QC: Câu không tag]")

        # Manual note containing "copy-paste trùng lặp" is never dropped by legacy auto-issue filter
        from repositories.report_repository import _resolve_row_issue_description

        manual_with_phrase = "QC: bỏ qua copy-paste trùng lặp; GV chưa nộp minh chứng"
        self.assertEqual(
            _resolve_row_issue_description({"qc_issue_note": manual_with_phrase}, st={}),
            manual_with_phrase,
        )
        self.assertEqual(
            _resolve_row_issue_description(
                {
                    "qc_issue_note": manual_with_phrase,
                    "lms_quality_issues": [
                        "[LMS] Nhận xét HS copy-paste trùng lặp (3/3 HS)",
                        "[LMS] Chưa ghi nội dung bài học",
                    ],
                },
                st={},
            ),
            f"{manual_with_phrase}; [LMS] Chưa ghi nội dung bài học",
        )
        self.assertEqual(
            _resolve_row_issue_description(
                {
                    "lms_quality_issues": [
                        "[LMS] Nhận xét HS sơ sài, thiếu đủ 3 ý (4/4 HS)",
                        "[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực (4/5 HS)",
                    ],
                    "zalo_content_issues": [
                        "[Zalo] Đã báo cáo nhưng ghi sai ngày/buổi trong mẫu — 'Bài số 6' khớp với BCNB W43/2026, ngày 25/10, buổi 6.",
                        "[Zalo] Đã báo cáo nhưng ghi sai ngày 22/10 — đã ghép theo BCNB W43/2026, ngày 21/10, buổi 13.",
                    ],
                },
                st={},
            ),
            "[LMS] Nhận xét thiếu ý; [LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực; "
            "[Zalo] Đã báo cáo nhưng ghi sai ngày/buổi ( Bài số 6 ); "
            "[Zalo] Đã báo cáo nhưng ghi sai ngày ( ghi ngày 22/10 nhưng lịch là ngày 21/10, buổi 13)",
        )

        # Auto issue containing internal ; or | is sanitized so re-splitting stays idempotent
        merged_internal_delim = merge_checker_issue_note(
            "GV xin phép",
            ["[Zalo] Báo cáo sai ngày; sai buổi | cần kiểm tra"],
        )
        self.assertEqual(
            split_checker_issue_note(
                merged_internal_delim,
                proven_auto_issues=["[Zalo] Báo cáo sai ngày; sai buổi | cần kiểm tra"],
            ),
            ("GV xin phép", ["[Zalo] Báo cáo sai ngày, sai buổi, cần kiểm tra"]),
        )

        # Length bound preserves manual note and leading [LMS] tag
        bounded = merge_checker_issue_note(
            "Ghi chú tay quan trọng",
            ["[LMS] " + ("Nội dung rất dài " * 20)],
            max_length=80,
        )
        self.assertLessEqual(len(bounded), 80)
        self.assertTrue(bounded.startswith("Ghi chú tay quan trọng | [LMS] "))
        self.assertTrue(bounded.endswith("..."))

        # Long manual note (480+ chars) is never truncated and [LMS] tag is preserved
        long_manual = "A" * 485
        merged_long = merge_checker_issue_note(
            long_manual,
            ["[LMS] Chưa ghi nội dung bài học"],
            max_length=500,
        )
        manual_out, auto_out = split_checker_issue_note(
            merged_long,
            proven_auto_issues=[merged_long.split(" | ", 1)[1]],
        )
        self.assertEqual(manual_out, long_manual)
        self.assertEqual(len(auto_out), 1)
        self.assertTrue(auto_out[0].startswith("[LMS]"))

    async def test_checker_note_dry_run_and_production_verified_and_failures(self):
        page, _, audit_repo, job_repo = self.make_dependencies()
        page.evaluate = AsyncMock(
            return_value={"ok": True, "stage": "after_write", "http_status": 200, "error": ""}
        )

        # 1. Dry-run never calls page.evaluate
        dry_outcome = await execute_verified_checker_note_action(
            page=page,
            audit_repo=audit_repo,
            job_repo=job_repo,
            job_id=10,
            action_key="note-dry-1",
            class_code="VT-C4K-PTB16",
            week=43,
            year=2026,
            lesson="1",
            execution_mode="dry-run",
            existing_note="GV báo bận",
            auto_issues=["[LMS] Chưa ghi nội dung bài học"],
            session_id="6274",
            class_date_iso="2026-10-24",
            reason={"test": True},
        )
        self.assertEqual(dry_outcome, "DRY_RUN")
        page.evaluate.assert_not_awaited()
        self.assertEqual(
            audit_repo.record_intent.await_args.kwargs["action_type"],
            "BCNB_UPDATE_CHECKER_NOTE",
        )
        self.assertEqual(
            audit_repo.record_intent.await_args.kwargs["intended_state"]["qc_issue_note"],
            "GV báo bận | [LMS] Chưa ghi nội dung bài học",
        )

        # 2. Production VERIFIED after readback
        page, _, audit_repo, job_repo = self.make_dependencies()
        page.evaluate = AsyncMock(
            return_value={"ok": True, "stage": "after_write", "http_status": 200, "error": ""}
        )
        expected_merged = "GV báo bận | [LMS] Chưa ghi nội dung bài học"
        with (
            patch(
                "services.bcnb_actions.verify_checker_note_persisted",
                AsyncMock(return_value=(True, expected_merged)),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            prod_outcome = await execute_verified_checker_note_action(
                page=page,
                audit_repo=audit_repo,
                job_repo=job_repo,
                job_id=11,
                action_key="note-prod-1",
                class_code="VT-C4K-PTB16",
                week=43,
                year=2026,
                lesson="1",
                execution_mode="production",
                existing_note="GV báo bận",
                auto_issues=["[LMS] Chưa ghi nội dung bài học"],
                session_id="6274",
                class_date_iso="2026-10-24",
                reason={"test": True},
            )
        self.assertEqual(prod_outcome, "VERIFIED")
        page.evaluate.assert_awaited_once()
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs["outcome"],
            "VERIFIED",
        )

        # 3. Production missing session_id fails closed before write
        page, _, audit_repo, job_repo = self.make_dependencies()
        page.evaluate = AsyncMock()
        with self.assertRaises(BcnbBeforeWriteError):
            await execute_verified_checker_note_action(
                page=page,
                audit_repo=audit_repo,
                job_repo=job_repo,
                job_id=12,
                action_key="note-prod-no-sid",
                class_code="VT-C4K-PTB16",
                week=43,
                year=2026,
                lesson="1",
                execution_mode="production",
                existing_note="",
                auto_issues=["[LMS] Chưa ghi nội dung bài học"],
                session_id="",
                reason={"test": True},
            )
        page.evaluate.assert_not_awaited()
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs["outcome"],
            "FAILED",
        )

        # 4. Production readback failure marks UNKNOWN
        page, _, audit_repo, job_repo = self.make_dependencies()
        page.evaluate = AsyncMock(
            return_value={"ok": True, "stage": "after_write", "http_status": 200, "error": ""}
        )
        with (
            patch(
                "services.bcnb_actions.verify_checker_note_persisted",
                AsyncMock(side_effect=RuntimeError("reload error")),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(BcnbOutcomeUnknownError):
                await execute_verified_checker_note_action(
                    page=page,
                    audit_repo=audit_repo,
                    job_repo=job_repo,
                    job_id=13,
                    action_key="note-prod-unknown",
                    class_code="VT-C4K-PTB16",
                    week=43,
                    year=2026,
                    lesson="1",
                    execution_mode="production",
                    existing_note="",
                    auto_issues=["[LMS] Chưa ghi nội dung bài học"],
                    session_id="6274",
                    reason={"test": True},
                )
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs["outcome"],
            "UNKNOWN",
        )

        # 5. Unreadable existing_note (None) fails closed before page.evaluate
        page, _, audit_repo, job_repo = self.make_dependencies()
        page.evaluate = AsyncMock()
        with self.assertRaises(BcnbBeforeWriteError):
            await execute_verified_checker_note_action(
                page=page,
                audit_repo=audit_repo,
                job_repo=job_repo,
                job_id=14,
                action_key="note-prod-unreadable-existing",
                class_code="VT-C4K-PTB16",
                week=43,
                year=2026,
                lesson="1",
                execution_mode="production",
                existing_note=None,
                auto_issues=["[LMS] Chưa ghi nội dung bài học"],
                session_id="6274",
                reason={"test": True},
            )
        page.evaluate.assert_not_awaited()
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs["outcome"],
            "FAILED",
        )

    def test_update_checker_note_js_validates_dom_and_csrf_before_fetch(self):
        harness = f"""
        const fn = {_UPDATE_CHECKER_NOTE_JS};
        (async () => {{
          let fetchCalls = 0;
          global.window = {{ csrfToken: 'tok-123', location: {{ href: 'https://teaching.mindx.edu.vn/check_nhanxet.php' }} }};
          global.document = {{
            scripts: [],
            querySelector: () => null,
            querySelectorAll: () => [
              {{
                getAttribute: (k) => (k === 'data-session-id' ? '6274' : null),
                innerText: '1 VT-C4K-PTB16 1 24/10/2026',
              }},
            ],
          }};
          global.fetch = async (url, opts) => {{
            fetchCalls += 1;
            return {{
              ok: true,
              status: 200,
              text: async () => JSON.stringify({{ success: true, body: opts.body }}),
            }};
          }};
          const okRes = await fn({{ sessionId: '6274', classCode: 'VT-C4K-PTB16', note: '[Auto QC: test]' }});
          const prefixCollisionRes = await fn({{ sessionId: '6274', classCode: 'VT-C4K-PTB1', note: '[Auto QC: test]' }});
          const mismatchRes = await fn({{ sessionId: '6274', classCode: 'VT-OTHER', note: '[Auto QC: test]' }});
          global.window.csrfToken = '';
          const noCsrfRes = await fn({{ sessionId: '6274', classCode: 'VT-C4K-PTB16', note: '[Auto QC: test]' }});
          console.log(JSON.stringify({{ okRes, prefixCollisionRes, mismatchRes, noCsrfRes, fetchCalls }}));
        }})();
        """
        proc = subprocess.run(
            ["node", "-e", harness],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        out = json.loads(proc.stdout.strip())
        self.assertTrue(out["okRes"]["ok"])
        self.assertEqual(out["okRes"]["stage"], "after_write")
        self.assertFalse(out["prefixCollisionRes"]["ok"])
        self.assertEqual(out["prefixCollisionRes"]["stage"], "before_write")
        self.assertEqual(out["prefixCollisionRes"]["error"], "SESSION_ROW_CLASS_MISMATCH")
        self.assertFalse(out["mismatchRes"]["ok"])
        self.assertEqual(out["mismatchRes"]["stage"], "before_write")
        self.assertEqual(out["mismatchRes"]["error"], "SESSION_ROW_CLASS_MISMATCH")
        self.assertFalse(out["noCsrfRes"]["ok"])
        self.assertEqual(out["noCsrfRes"]["stage"], "before_write")
        self.assertEqual(out["noCsrfRes"]["error"], "MISSING_CSRF_TOKEN")
        self.assertEqual(out["fetchCalls"], 1)

    async def test_update_bcnb_reports_writes_checker_note_for_unsubmitted_and_submitted_rows(self):
        from services.zalo_bcnb_service import update_bcnb_reports

        audit_repo = AsyncMock()
        audit_repo.record_intent.return_value = (
            {"id": 99, "outcome": "INTENT_RECORDED"},
            True,
        )
        job_repo = AsyncMock()
        checkbox = AsyncMock()
        checkbox.count.return_value = 1
        checkbox.is_checked.return_value = True

        fake_cell = MagicMock()
        fake_cell.locator.return_value.first = checkbox

        fake_rows = [
            {
                "row": MagicMock(),
                "cells": [fake_cell] * 12,
                "week": 43,
                "year": 2026,
                "ma_lop": "VT-C4K-PTB16",
                "buoi_so": "1",
                "ngay_hoc": "24/10/2026",
                "ngay_hoc_iso": "2026-10-24",
                "zalo_deadline": "20:00 26/10/2026",
                "bcnb_session_id": "6274",
                "khoi": "Coding",
                "nx_zalo_checked": False,
                "nx_lms_checked": True,
                "qc_issue_note": "GV báo bận",
                "instructor_note": "Chưa nội dung bài học",
                "evaluation_status": "⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo",
                "checker_note_unreadable": False,
            }
        ]
        scan_results = [
            {
                "group": "[MINDX] VT-C4K-PTB16",
                "person": "Đỗ Thị Thùy Trang",
                "status": "❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng",
                "evidence_state": "NOT_FOUND_VERIFIED",
                "scan_target": {
                    "class_code": "VT-C4K-PTB16",
                    "lesson": "1",
                    "class_date": "24/10/2026",
                    "class_date_iso": "2026-10-24",
                    "week": 43,
                    "year": 2026,
                },
                "evidence": {
                    "date_context": {
                        "target_week": 43,
                        "target_year": 2026,
                        "target_lesson": "1",
                        "target_class_date": "2026-10-24",
                    }
                },
            }
        ]
        lms_slots = {
            "VT-C4K-PTB16": [
                {
                    "index": 0,
                    "date": "2026-10-23T17:00:00.000Z",
                    "startTime": "2026-10-24T01:00:00.000Z",
                    "summary": "",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            "comment": "Con đi học đúng giờ, tập trung nghe giảng.",
                        }
                    ],
                }
            ]
        }
        scan_plan = {
            "selected_weeks": [{"week": 43, "year": 2026}, {"week": 42, "year": 2026}],
            "latest_week": {"week": 43, "year": 2026},
            "targets": [scan_results[0]["scan_target"]],
        }
        expected_note = (
            "GV báo bận | [LMS] Chưa ghi nội dung bài học; "
            "[LMS] Nhận xét thiếu ý"
        )
        page, _, _, _ = self.make_dependencies()
        page.evaluate = AsyncMock(
            return_value={"ok": True, "stage": "after_write", "http_status": 200, "error": ""}
        )
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(return_value=fake_rows),
            ),
            patch(
                "services.zalo_bcnb_service.resolve_report_columns",
                AsyncMock(return_value=(1, 2, 8)),
            ),
            patch(
                "services.bcnb_actions.verify_checker_note_persisted",
                AsyncMock(return_value=(True, expected_note)),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=501,
                execution_mode="production",
                scan_results=scan_results,
                today=datetime(2026, 10, 29, tzinfo=BUSINESS_TIMEZONE),
                scan_plan=scan_plan,
                job_repo=job_repo,
                audit_repo=audit_repo,
                period_filter={(43, 2026)},
                lms_slots_by_class=lms_slots,
            )

        self.assertEqual(summary["checker_note_updated_count"], 1)
        self.assertEqual(scan_results[0]["checker_note_outcome"], "VERIFIED")
        self.assertEqual(scan_results[0]["qc_issue_note"], expected_note)
        self.assertEqual(
            audit_repo.record_intent.await_args.kwargs["action_type"],
            "BCNB_UPDATE_CHECKER_NOTE",
        )

    async def test_5_2_1_unreadable_checker_note_with_ten_verified_rows_ticks_all_ten_without_halt(self):
        """5.2.1(a): Unreadable note cell on 10 verified rows must not trigger policy halt; all 10 rows are ticked."""
        from services.zalo_bcnb_service import update_bcnb_reports
        from services.zalo_parallel_service import BatchFailurePolicy

        policy = BatchFailurePolicy(total_targets=10)
        audit_repo = AsyncMock()
        audit_repo.record_intent.return_value = (
            {"id": 100, "outcome": "INTENT_RECORDED"},
            True,
        )
        job_repo = AsyncMock()
        checkbox = AsyncMock()
        checkbox.count.return_value = 1
        checkbox.is_checked.return_value = False
        fake_cell = MagicMock()
        fake_cell.locator.return_value.first = checkbox

        fake_rows = []
        scan_results = []
        targets = []
        for idx in range(1, 11):
            code = f"VT-C4K-PTB{idx:02d}"
            fake_rows.append(
                {
                    "row": MagicMock(),
                    "cells": [fake_cell] * 12,
                    "week": 43,
                    "year": 2026,
                    "ma_lop": code,
                    "buoi_so": "1",
                    "ngay_hoc": "24/10/2026",
                    "ngay_hoc_iso": "2026-10-24",
                    "zalo_deadline": "20:00 26/10/2026",
                    "bcnb_session_id": f"62{idx:02d}",
                    "khoi": "Coding",
                    "nx_zalo_checked": False,
                    "nx_lms_checked": True,
                    "qc_issue_note": None,
                    "checker_note_unreadable": True,
                }
            )
            st = {
                "class_code": code,
                "lesson": "1",
                "class_date": "24/10/2026",
                "class_date_iso": "2026-10-24",
                "week": 43,
                "year": 2026,
            }
            targets.append(st)
            scan_results.append(
                {
                    "group": f"[MINDX] {code}",
                    "person": "GV Test",
                    "status": "🟢 Đã nộp đúng hạn – Có minh chứng",
                    "evidence_state": "SUBMITTED_VERIFIED",
                    "zalo_content_issues": ["[Zalo] Báo cáo ghi sai buổi"],
                    "scan_target": st,
                    "evidence": {
                        "screenshot_path": f"static/screenshots/ev_{idx}.png",
                        "artifact_sha256": "a" * 64,
                        "classifier_result": {
                            "matched_ui_sent_at": "24/10/2026",
                        },
                        "date_context": {
                            "target_week": 43,
                            "target_year": 2026,
                            "target_lesson": "1",
                            "target_class_date": "2026-10-24",
                            "content_lesson": "2",
                        },
                    },
                }
            )

        async def record_row_outcome(outcome):
            if str(outcome.get("action_type") or "") == "BCNB_UPDATE_CHECKER_NOTE":
                return
            status = str(outcome.get("status") or "")
            abnormal = status in {"FAILED_BEFORE_WRITE", "OUTCOME_UNKNOWN"}
            await policy.record(abnormal=abnormal, systemic=False, reason=str(outcome.get("error") or ""))

        page, _, _, _ = self.make_dependencies()
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(return_value=fake_rows),
            ),
            patch(
                "services.zalo_bcnb_service.resolve_report_columns",
                AsyncMock(return_value=(1, 2, 8)),
            ),
            patch(
                "services.zalo_bcnb_service.execute_verified_checkbox_action",
                AsyncMock(return_value="VERIFIED"),
            ) as mock_tick,
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=502,
                execution_mode="production",
                scan_results=scan_results,
                today=datetime(2026, 10, 29, tzinfo=BUSINESS_TIMEZONE),
                scan_plan={
                    "selected_weeks": [{"week": 43, "year": 2026}, {"week": 42, "year": 2026}],
                    "latest_week": {"week": 43, "year": 2026},
                    "targets": targets,
                },
                job_repo=job_repo,
                audit_repo=audit_repo,
                period_filter={(43, 2026)},
                should_halt=policy.halted.is_set,
                on_target_outcome=record_row_outcome,
            )

        self.assertEqual(mock_tick.await_count, 10)
        self.assertEqual(summary["updated_count"], 10)
        self.assertFalse(policy.halted.is_set())
        self.assertEqual(policy.abnormal_targets, 0)
        self.assertEqual(summary["checker_note_failure_count"], 10)
        self.assertEqual(len(summary["checker_note_failures"]), 10)
        self.assertEqual(len(summary["failed_targets"]), 0)
        self.assertTrue(
            all(item["status"] == "VERIFIED" for item in summary["target_outcomes"])
        )

    async def test_5_2_2_notes_only_skips_checkbox_actions_and_column_resolution(self):
        """5.2.2: When notes_only=True, update_bcnb_reports never calls execute_verified_checkbox_action."""
        from services.zalo_bcnb_service import update_bcnb_reports

        audit_repo = AsyncMock()
        audit_repo.record_intent.return_value = (
            {"id": 101, "outcome": "INTENT_RECORDED"},
            True,
        )
        job_repo = AsyncMock()
        fake_rows = [
            {
                "row": MagicMock(),
                "cells": [],
                "week": 43,
                "year": 2026,
                "ma_lop": "VT-C4K-PTB16",
                "buoi_so": "1",
                "ngay_hoc": "24/10/2026",
                "ngay_hoc_iso": "2026-10-24",
                "bcnb_session_id": "6274",
                "qc_issue_note": "Ghi chú tay",
                "checker_note_unreadable": False,
            }
        ]
        st = {
            "class_code": "VT-C4K-PTB16",
            "lesson": "1",
            "class_date": "24/10/2026",
            "class_date_iso": "2026-10-24",
            "week": 43,
            "year": 2026,
        }
        scan_results = [
            {
                "group": "[MINDX] VT-C4K-PTB16",
                "person": "GV Test",
                "status": "🟢 Đã nộp đúng hạn – Có minh chứng",
                "evidence_state": "SUBMITTED_VERIFIED",
                "zalo_content_issues": ["[Zalo] Báo cáo ghi sai buổi"],
                "scan_target": st,
                "evidence": {
                    "screenshot_path": "static/screenshots/ev_1.png",
                    "artifact_sha256": "b" * 64,
                    "classifier_result": {
                        "matched_ui_sent_at": "24/10/2026",
                    },
                    "date_context": {
                        "target_week": 43,
                        "target_year": 2026,
                        "target_lesson": "1",
                        "target_class_date": "2026-10-24",
                        "content_lesson": "2",
                    },
                },
            }
        ]
        expected_note = "Ghi chú tay | [Zalo] Ghi sai số bài học trong nội dung (ghi Bài 2, lịch là Buổi 1)"
        page, _, _, _ = self.make_dependencies()
        page.evaluate = AsyncMock(
            return_value={"ok": True, "stage": "after_write", "http_status": 200, "error": ""}
        )
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(return_value=fake_rows),
            ),
            patch(
                "services.zalo_bcnb_service.resolve_report_columns",
                AsyncMock(side_effect=AssertionError("resolve_report_columns should not be called in notes_only mode")),
            ) as mock_resolve_cols,
            patch(
                "services.zalo_bcnb_service.execute_verified_checkbox_action",
                AsyncMock(),
            ) as mock_tick,
            patch(
                "services.bcnb_actions.verify_checker_note_persisted",
                AsyncMock(return_value=(True, expected_note)),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=503,
                execution_mode="production",
                scan_results=scan_results,
                today=datetime(2026, 10, 29, tzinfo=BUSINESS_TIMEZONE),
                scan_plan={
                    "selected_weeks": [{"week": 43, "year": 2026}, {"week": 42, "year": 2026}],
                    "latest_week": {"week": 43, "year": 2026},
                    "targets": [st],
                },
                job_repo=job_repo,
                audit_repo=audit_repo,
                period_filter={(43, 2026)},
                notes_only=True,
            )

        mock_resolve_cols.assert_not_awaited()
        mock_tick.assert_not_awaited()
        self.assertEqual(summary["updated_count"], 0)
        self.assertEqual(summary["checker_note_updated_count"], 1)
        self.assertEqual(scan_results[0]["checker_note_outcome"], "VERIFIED")

    async def test_5_2_3_optimistic_pre_read_blocks_post_and_post_readback_mismatch_flags_conflict(self):
        """5.2.3(b)&(c): Concurrent edit before POST blocks POST; mismatch after POST flags CONFLICT_AFTER_WRITE."""
        # Case (b1): read_checker_note_by_session detects concurrent QC edit before POST
        page, _, audit_repo, job_repo = self.make_dependencies()
        page.evaluate = AsyncMock()
        with patch(
            "services.bcnb_actions.read_checker_note_by_session",
            AsyncMock(return_value=(True, "QC vừa sửa tay tức thì")),
        ):
            with self.assertRaises(BcnbBeforeWriteError) as ctx:
                await execute_verified_checker_note_action(
                    page=page,
                    audit_repo=audit_repo,
                    job_repo=job_repo,
                    job_id=504,
                    action_key="note-pre-read-changed",
                    class_code="VT-C4K-PTB16",
                    week=43,
                    year=2026,
                    lesson="1",
                    execution_mode="production",
                    existing_note="Ghi chú cũ",
                    auto_issues=["[LMS] Chưa ghi nội dung bài học"],
                    session_id="6274",
                    reason={"test": True},
                )
        self.assertIn("QC vừa chỉnh sửa", str(ctx.exception))
        page.evaluate.assert_not_awaited()
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs["outcome"],
            "FAILED",
        )

        # Case (b2): _UPDATE_CHECKER_NOTE_JS detects concurrent DOM change before fetch()
        harness = f"""
        const fn = {_UPDATE_CHECKER_NOTE_JS};
        (async () => {{
          let fetchCalls = 0;
          global.window = {{ csrfToken: 'tok-123', location: {{ href: 'https://teaching.mindx.edu.vn/check_nhanxet.php' }} }};
          global.document = {{
            scripts: [],
            querySelector: () => null,
            querySelectorAll: () => [
              {{
                getAttribute: (k) => (k === 'data-session-id' ? '6274' : null),
                innerText: '1 VT-C4K-PTB16 1 24/10/2026',
                querySelector: () => ({{
                  getAttribute: (k) => (k === 'data-current-note' ? 'QC vừa sửa ngay trước fetch' : null),
                  innerText: 'QC vừa sửa ngay trước fetch',
                }}),
              }},
            ],
          }};
          global.fetch = async () => {{ fetchCalls += 1; return {{ ok: true, status: 200, text: async () => '{{}}' }}; }};
          const res = await fn({{
            sessionId: '6274',
            classCode: 'VT-C4K-PTB16',
            note: 'Ghi chú cũ | [LMS] Chưa ghi nội dung bài học',
            expectedExistingNote: 'Ghi chú cũ',
          }});
          console.log(JSON.stringify({{ res, fetchCalls }}));
        }})();
        """
        proc = subprocess.run(
            ["node", "-e", harness],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        out = json.loads(proc.stdout.strip())
        self.assertFalse(out["res"]["ok"])
        self.assertEqual(out["res"]["stage"], "before_write")
        self.assertEqual(out["res"]["error"], "CONCURRENT_NOTE_EDIT")
        self.assertEqual(out["fetchCalls"], 0)

        # Case (c): Note changed between POST and readback -> BcnbConflictAfterWriteError (CONFLICT_AFTER_WRITE)
        page, _, audit_repo, job_repo = self.make_dependencies()
        page.evaluate = AsyncMock(
            return_value={"ok": True, "stage": "after_write", "http_status": 200, "error": ""}
        )
        with (
            patch(
                "services.bcnb_actions.read_checker_note_by_session",
                AsyncMock(return_value=(True, "Ghi chú cũ")),
            ),
            patch(
                "services.bcnb_actions.verify_checker_note_persisted",
                AsyncMock(return_value=(False, "QC sửa đè giữa lúc reload")),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(BcnbConflictAfterWriteError):
                await execute_verified_checker_note_action(
                    page=page,
                    audit_repo=audit_repo,
                    job_repo=job_repo,
                    job_id=505,
                    action_key="note-conflict-after-write",
                    class_code="VT-C4K-PTB16",
                    week=43,
                    year=2026,
                    lesson="1",
                    execution_mode="production",
                    existing_note="Ghi chú cũ",
                    auto_issues=["[LMS] Chưa ghi nội dung bài học"],
                    session_id="6274",
                    reason={"test": True},
                )
        page.evaluate.assert_awaited_once()
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs["outcome"],
            "UNKNOWN",
        )
        self.assertEqual(
            audit_repo.complete_action.await_args.kwargs["after_state"]["conflict_status"],
            "CONFLICT_AFTER_WRITE",
        )

    def test_5_2_4_table_driven_provenance_auto_vs_manual_note(self):
        """5.2.4: Table-driven verification of all 5 provenance conditions and byte-for-byte manual preservation."""
        row_id = {
            "class_code": "VT-C4K-PTB16",
            "lesson": "1",
            "week": 43,
            "year": 2026,
            "session_id": "6274",
            "class_date_iso": "2026-10-24",
        }
        auto_str = "[LMS] Chưa ghi nội dung bài học"
        valid_audit = {
            "action_type": "BCNB_UPDATE_CHECKER_NOTE",
            "target_id": "VT-C4K-PTB16",
            "mode": "production",
            "outcome": "VERIFIED",
            "before_state": {"qc_issue_note": "Ghi chú tay"},
            "intended_state": {"qc_issue_note": f"Ghi chú tay | {auto_str}"},
            "after_state": {
                "qc_issue_note": f"Ghi chú tay | {auto_str}",
                "bcnb_session_id": "6274",
                "post_verified": True,
            },
            "reason": {
                "week": 43,
                "year": 2026,
                "lesson": "1",
                "class_date": "2026-10-24",
                "bcnb_session_id": "6274",
                "auto_issues": [auto_str],
                "merged_note": f"Ghi chú tay | {auto_str}",
            },
        }

        cases = [
            (
                "1_manual_identical_to_auto_without_audit_preserved",
                auto_str,
                [],
                [],
                auto_str,
            ),
            (
                "2_matching_production_verified_audit_replaced",
                f"Ghi chú tay | {auto_str}",
                [valid_audit],
                ["[Zalo] Báo cáo ghi sai buổi"],
                "Ghi chú tay | [Zalo] Báo cáo ghi sai buổi",
            ),
            (
                "3a_negative_dry_run_audit_preserved",
                auto_str,
                [{**valid_audit, "mode": "dry-run", "outcome": "DRY_RUN"}],
                [],
                auto_str,
            ),
            (
                "3b_negative_failed_audit_preserved",
                auto_str,
                [{**valid_audit, "outcome": "FAILED"}],
                [],
                auto_str,
            ),
            (
                "3c_negative_outcome_unknown_audit_preserved",
                auto_str,
                [{**valid_audit, "outcome": "UNKNOWN"}],
                [],
                auto_str,
            ),
            (
                "3d_negative_pending_intent_audit_preserved",
                auto_str,
                [{**valid_audit, "outcome": "INTENT_RECORDED"}],
                [],
                auto_str,
            ),
            (
                "3e_negative_other_row_lesson_audit_preserved",
                auto_str,
                [
                    {
                        **valid_audit,
                        "reason": {**valid_audit["reason"], "lesson": "2"},
                    }
                ],
                [],
                auto_str,
            ),
            (
                "3f_negative_other_row_session_id_audit_preserved",
                auto_str,
                [
                    {
                        **valid_audit,
                        "after_state": {
                            **valid_audit["after_state"],
                            "bcnb_session_id": "9999",
                        },
                        "reason": {
                            **valid_audit["reason"],
                            "bcnb_session_id": "9999",
                        },
                    }
                ],
                [],
                auto_str,
            ),
            (
                "4_manual_segment_carried_in_merged_and_after_state_but_not_in_reason_auto_issues_preserved",
                f"{auto_str} | [Zalo] Báo cáo ghi sai buổi",
                [
                    {
                        **valid_audit,
                        "before_state": {"qc_issue_note": auto_str},
                        "after_state": {
                            "qc_issue_note": f"{auto_str} | [Zalo] Báo cáo ghi sai buổi",
                            "bcnb_session_id": "6274",
                            "post_verified": True,
                        },
                        "reason": {
                            **valid_audit["reason"],
                            "auto_issues": ["[Zalo] Báo cáo ghi sai buổi"],
                            "merged_note": f"{auto_str} | [Zalo] Báo cáo ghi sai buổi",
                        },
                    }
                ],
                [],
                auto_str,
            ),
            (
                "5_no_change_verified_audit_without_post_not_used_as_provenance",
                auto_str,
                [
                    {
                        **valid_audit,
                        "before_state": {"qc_issue_note": auto_str},
                        "after_state": {
                            "qc_issue_note": auto_str,
                            "bcnb_session_id": "6274",
                            "post_verified": False,
                            "no_change": True,
                        },
                    }
                ],
                [],
                auto_str,
            ),
            (
                "6_multiline_and_pipe_manual_note_preserved_byte_for_byte",
                f"  Dòng 1: QC kiểm tra  2 spaces\nDòng 2: có | dấu gạch và ; chấm phẩy\n | {auto_str}",
                [valid_audit],
                [],
                "  Dòng 1: QC kiểm tra  2 spaces\nDòng 2: có | dấu gạch và ; chấm phẩy\n",
            ),
            (
                "7_untagged_manual_segment_matching_proven_body_preserved",
                f"Chưa ghi nội dung bài học | {auto_str}",
                [valid_audit],
                [],
                "Chưa ghi nội dung bài học",
            ),
            (
                "8_different_tag_manual_segment_matching_proven_body_preserved",
                f"[Zalo] Chưa ghi nội dung bài học | {auto_str}",
                [valid_audit],
                [],
                "[Zalo] Chưa ghi nội dung bài học",
            ),
            (
                "9_manual_segment_ending_with_ellipsis_sharing_prefix_preserved",
                f"[LMS] Chưa ghi nội... | {auto_str}",
                [valid_audit],
                [],
                "[LMS] Chưa ghi nội...",
            ),
            (
                "10_audit_without_post_verified_true_rejected",
                auto_str,
                [
                    {
                        **valid_audit,
                        "after_state": {
                            "qc_issue_note": f"Ghi chú tay | {auto_str}",
                            "bcnb_session_id": "6274",
                        },
                    }
                ],
                [],
                auto_str,
            ),
            (
                "11_leading_trailing_whitespace_and_newline_preserved_when_no_auto_issues",
                "  QC\n",
                [],
                [],
                "  QC\n",
            ),
            (
                "12_untagged_template_phrase_never_gains_zalo_tag_or_matches_proven",
                "Đã báo cáo nhưng ghi sai ngày/buổi (Bài số 6) | [Zalo] Đã báo cáo nhưng ghi sai ngày/buổi ( Bài số 6 )",
                [
                    {
                        **valid_audit,
                        "before_state": {
                            "qc_issue_note": "Đã báo cáo nhưng ghi sai ngày/buổi (Bài số 6)"
                        },
                        "after_state": {
                            "qc_issue_note": "Đã báo cáo nhưng ghi sai ngày/buổi (Bài số 6) | [Zalo] Đã báo cáo nhưng ghi sai ngày/buổi ( Bài số 6 )",
                            "bcnb_session_id": "6274",
                            "post_verified": True,
                        },
                        "reason": {
                            **valid_audit["reason"],
                            "auto_issues": [
                                "[Zalo] Đã báo cáo nhưng ghi sai ngày/buổi ( Bài số 6 )"
                            ],
                        },
                    }
                ],
                [],
                "Đã báo cáo nhưng ghi sai ngày/buổi (Bài số 6)",
            ),
            (
                "13_manual_segment_A_in_before_state_when_audit_posts_A_and_B_is_not_treated_as_proven_auto",
                f"{auto_str} | [Zalo] Báo cáo ghi sai buổi",
                [
                    {
                        **valid_audit,
                        "before_state": {"qc_issue_note": auto_str},
                        "after_state": {
                            "qc_issue_note": f"{auto_str} | [Zalo] Báo cáo ghi sai buổi",
                            "bcnb_session_id": "6274",
                            "post_verified": True,
                        },
                        "reason": {
                            **valid_audit["reason"],
                            "auto_issues": [auto_str, "[Zalo] Báo cáo ghi sai buổi"],
                            "merged_note": f"{auto_str} | [Zalo] Báo cáo ghi sai buổi",
                        },
                    }
                ],
                [],
                auto_str,
            ),
            (
                "14_audit_missing_before_state_rejected",
                auto_str,
                [
                    {
                        **valid_audit,
                        "before_state": None,
                    }
                ],
                [],
                auto_str,
            ),
            (
                "15_audit_missing_before_qc_issue_note_rejected",
                auto_str,
                [
                    {
                        **valid_audit,
                        "before_state": {"bcnb_session_id": "6274"},
                    }
                ],
                [],
                auto_str,
            ),
        ]

        for name, existing, audits, new_auto, expected in cases:
            with self.subTest(case=name):
                result = merge_checker_issue_note(
                    existing,
                    new_auto,
                    audit_records=audits,
                    row_identity=row_id,
                )
                self.assertEqual(result, expected)

    async def test_5_2_4_integration_direct_issue_verified_then_clean_rescan_removes_auto_preserves_manual_bytes(self):
        """5.2.4 integration: Direct [LMS]/[Zalo] issue written VERIFIED -> clean rescan -> auto removed, manual preserved byte-for-byte."""
        from services.zalo_bcnb_service import update_bcnb_reports

        stored_audits: list[dict] = []

        class InMemoryCheckerAuditRepo:
            async def record_intent(self, **kwargs):
                rec = {
                    "id": len(stored_audits) + 1,
                    "action_key": kwargs["action_key"],
                    "job_id": kwargs["job_id"],
                    "action_type": kwargs["action_type"],
                    "target_type": kwargs["target_type"],
                    "target_id": kwargs["target_id"],
                    "mode": kwargs["mode"],
                    "outcome": "INTENT_RECORDED",
                    "before_state": kwargs.get("before_state"),
                    "intended_state": kwargs.get("intended_state"),
                    "after_state": None,
                    "reason": kwargs.get("reason"),
                }
                stored_audits.append(rec)
                return rec, True

            async def complete_action(self, audit_id, *, outcome, after_state=None):
                for rec in stored_audits:
                    if rec["id"] == audit_id:
                        rec["outcome"] = outcome
                        rec["after_state"] = after_state

            async def list_checker_note_audits_for_class(self, class_code: str, *, limit: int = 200):
                return [
                    rec
                    for rec in reversed(stored_audits)
                    if rec["action_type"] == "BCNB_UPDATE_CHECKER_NOTE"
                    and rec["target_id"].upper() == class_code.strip().upper()
                ][:limit]

        audit_repo = InMemoryCheckerAuditRepo()
        job_repo = AsyncMock()
        manual_note = "  QC dòng 1:  giữ nguyên 2 dấu cách\nQC dòng 2: giữ | pipe ; chấm phẩy\n"
        direct_auto = "[Zalo] Ghi sai số bài học trong nội dung (ghi Bài 2, lịch là Buổi 1)"
        expected_after_run_1 = f"{manual_note} | {direct_auto}"

        row_state = {
            "row": MagicMock(),
            "cells": [],
            "week": 43,
            "year": 2026,
            "ma_lop": "VT-C4K-PTB16",
            "buoi_so": "1",
            "ngay_hoc": "24/10/2026",
            "ngay_hoc_iso": "2026-10-24",
            "bcnb_session_id": "6274",
            "qc_issue_note": manual_note,
            "checker_note_unreadable": False,
        }
        st = {
            "class_code": "VT-C4K-PTB16",
            "lesson": "1",
            "class_date": "24/10/2026",
            "class_date_iso": "2026-10-24",
            "week": 43,
            "year": 2026,
        }
        scan_plan = {
            "selected_weeks": [{"week": 43, "year": 2026}, {"week": 42, "year": 2026}],
            "latest_week": {"week": 43, "year": 2026},
            "targets": [st],
        }
        page, _, _, _ = self.make_dependencies()
        page.evaluate = AsyncMock(
            return_value={"ok": True, "stage": "after_write", "http_status": 200, "error": ""}
        )

        # Pass 1: Writes direct_auto with production VERIFIED
        scan_results_pass_1 = [
            {
                "group": "[MINDX] VT-C4K-PTB16",
                "person": "GV Test",
                "status": "🟢 Đã nộp đúng hạn – Có minh chứng",
                "evidence_state": "SUBMITTED_VERIFIED",
                "scan_target": dict(st),
                "evidence": {
                    "date_context": {
                        "target_week": 43,
                        "target_year": 2026,
                        "target_lesson": "1",
                        "target_class_date": "2026-10-24",
                        "content_lesson": "2",
                    }
                },
            }
        ]
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(return_value=[dict(row_state)]),
            ),
            patch(
                "services.bcnb_actions.read_checker_note_by_session",
                AsyncMock(return_value=(True, manual_note)),
            ),
            patch(
                "services.bcnb_actions.verify_checker_note_persisted",
                AsyncMock(return_value=(True, expected_after_run_1)),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            summary_1 = await update_bcnb_reports(
                page,
                job_id=601,
                execution_mode="production",
                scan_results=scan_results_pass_1,
                today=datetime(2026, 10, 29, tzinfo=BUSINESS_TIMEZONE),
                scan_plan=scan_plan,
                job_repo=job_repo,
                audit_repo=audit_repo,
                period_filter={(43, 2026)},
                lms_slots_by_class={"VT-C4K-PTB16": []},
                notes_only=True,
            )
        self.assertEqual(summary_1["checker_note_updated_count"], 1)
        self.assertEqual(scan_results_pass_1[0]["qc_issue_note"], expected_after_run_1)

        # Pass 2: Clean rescan (no issues) -> direct_auto removed, manual_note preserved byte-for-byte
        row_state_pass_2 = {**row_state, "qc_issue_note": expected_after_run_1}
        scan_results_pass_2 = [
            {
                "group": "[MINDX] VT-C4K-PTB16",
                "person": "GV Test",
                "status": "🟢 Đã nộp đúng hạn – Có minh chứng",
                "evidence_state": "SUBMITTED_VERIFIED",
                "zalo_content_issues": [],
                "lms_quality_issues": [],
                "quality_issues": [],
                "scan_target": {**st, "qc_issue_note": expected_after_run_1},
                "evidence": {
                    "date_context": {
                        "target_week": 43,
                        "target_year": 2026,
                        "target_lesson": "1",
                        "target_class_date": "2026-10-24",
                    }
                },
            }
        ]
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(return_value=[row_state_pass_2]),
            ),
            patch(
                "services.bcnb_actions.read_checker_note_by_session",
                AsyncMock(return_value=(True, expected_after_run_1)),
            ),
            patch(
                "services.bcnb_actions.verify_checker_note_persisted",
                AsyncMock(return_value=(True, manual_note)),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            summary_2 = await update_bcnb_reports(
                page,
                job_id=602,
                execution_mode="production",
                scan_results=scan_results_pass_2,
                today=datetime(2026, 10, 29, tzinfo=BUSINESS_TIMEZONE),
                scan_plan=scan_plan,
                job_repo=job_repo,
                audit_repo=audit_repo,
                period_filter={(43, 2026)},
                lms_slots_by_class={"VT-C4K-PTB16": []},
                notes_only=True,
            )
        self.assertEqual(summary_2["checker_note_updated_count"], 1)
        self.assertEqual(scan_results_pass_2[0]["qc_issue_note"], manual_note)

        # Pass 3: Resuming an interrupted INTENT_RECORDED (or no_change) never stamps post_verified=True
        interrupted_audit_repo = AsyncMock()
        interrupted_audit_repo.record_intent.return_value = (
            {"id": 777, "outcome": "INTENT_RECORDED"},
            False,
        )
        with patch(
            "services.bcnb_actions.verify_checker_note_persisted",
            AsyncMock(return_value=(True, f"Manual | {direct_auto}")),
        ):
            rec_outcome = await execute_verified_checker_note_action(
                page=page,
                audit_repo=interrupted_audit_repo,
                job_repo=job_repo,
                job_id=603,
                action_key="note-intent-reconcile",
                class_code="VT-C4K-PTB16",
                week=43,
                year=2026,
                lesson="1",
                execution_mode="production",
                existing_note="Manual",
                auto_issues=[direct_auto],
                session_id="6274",
                class_date_iso="2026-10-24",
                proven_auto_issues=[],
                reason={
                    "week": 43,
                    "year": 2026,
                    "lesson": "1",
                    "class_date": "2026-10-24",
                    "bcnb_session_id": "6274",
                    "auto_issues": [direct_auto],
                    "existing_note": "Manual",
                    "merged_note": f"Manual | {direct_auto}",
                },
            )
        self.assertEqual(rec_outcome, "VERIFIED")
        reconciled_after = interrupted_audit_repo.complete_action.await_args.kwargs["after_state"]
        self.assertIs(reconciled_after["post_verified"], False)
        self.assertTrue(reconciled_after.get("reconciled_from_intent"))

    async def test_5_2_5_checkbox_tick_failure_records_skipped_tick_failed_for_checker_note(self):
        """5.2.5: When checkbox tick fails, checker note is recorded as SKIPPED_TICK_FAILED instead of silent continue."""
        from services.zalo_bcnb_service import update_bcnb_reports

        audit_repo = AsyncMock()
        job_repo = AsyncMock()
        checkbox = AsyncMock()
        checkbox.count.return_value = 1
        checkbox.is_checked.return_value = False
        fake_cell = MagicMock()
        fake_cell.locator.return_value.first = checkbox

        fake_rows = [
            {
                "row": MagicMock(),
                "cells": [fake_cell] * 12,
                "week": 43,
                "year": 2026,
                "ma_lop": "VT-C4K-PTB16",
                "buoi_so": "1",
                "ngay_hoc": "24/10/2026",
                "ngay_hoc_iso": "2026-10-24",
                "zalo_deadline": "20:00 26/10/2026",
                "bcnb_session_id": "6274",
                "qc_issue_note": "",
                "checker_note_unreadable": False,
            }
        ]
        st = {
            "class_code": "VT-C4K-PTB16",
            "lesson": "1",
            "class_date": "24/10/2026",
            "class_date_iso": "2026-10-24",
            "week": 43,
            "year": 2026,
        }
        scan_results = [
            {
                "group": "[MINDX] VT-C4K-PTB16",
                "person": "GV Test",
                "status": "🟢 Đã nộp đúng hạn – Có minh chứng",
                "evidence_state": "SUBMITTED_VERIFIED",
                "zalo_content_issues": ["[Zalo] Báo cáo ghi sai buổi"],
                "scan_target": st,
                "evidence": {
                    "screenshot_path": "static/screenshots/ev_1.png",
                    "artifact_sha256": "c" * 64,
                    "classifier_result": {
                        "matched_ui_sent_at": "24/10/2026",
                    },
                    "date_context": {
                        "target_week": 43,
                        "target_year": 2026,
                        "target_lesson": "1",
                        "target_class_date": "2026-10-24",
                        "content_lesson": "2",
                    },
                },
            }
        ]
        page, _, _, _ = self.make_dependencies()
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(return_value=fake_rows),
            ),
            patch(
                "services.zalo_bcnb_service.resolve_report_columns",
                AsyncMock(return_value=(1, 2, 8)),
            ),
            patch(
                "services.zalo_bcnb_service.execute_verified_checkbox_action",
                AsyncMock(side_effect=BcnbBeforeWriteError("Checkbox disabled")),
            ),
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=506,
                execution_mode="production",
                scan_results=scan_results,
                today=datetime(2026, 10, 29, tzinfo=BUSINESS_TIMEZONE),
                scan_plan={
                    "selected_weeks": [{"week": 43, "year": 2026}, {"week": 42, "year": 2026}],
                    "latest_week": {"week": 43, "year": 2026},
                    "targets": [st],
                },
                job_repo=job_repo,
                audit_repo=audit_repo,
                period_filter={(43, 2026)},
            )

        self.assertEqual(scan_results[0]["checker_note_outcome"], "SKIPPED_TICK_FAILED")
        self.assertEqual(len(summary["checker_note_outcomes"]), 1)
        self.assertEqual(
            summary["checker_note_outcomes"][0]["status"],
            "SKIPPED_TICK_FAILED",
        )
        self.assertEqual(
            summary["checker_note_outcomes"][0]["action_type"],
            "BCNB_UPDATE_CHECKER_NOTE",
        )

    async def test_read_checker_note_by_session_and_verify_checker_note_persisted_dom_paths(self):
        """Direct DOM helper coverage for read_checker_note_by_session, verify_checker_note_persisted, and provenance edge cases."""
        from services.bcnb_safety import (
            extract_proven_auto_issues_from_audits,
            read_checker_note_by_session,
            verify_checker_note_persisted,
        )

        # 1. Edge cases of extract_proven_auto_issues_from_audits
        self.assertEqual(
            extract_proven_auto_issues_from_audits(
                [],
                class_code="VT-C4K-PTB16",
                lesson="1",
                week=43,
                year=2026,
            ),
            [],
        )
        self.assertEqual(
            extract_proven_auto_issues_from_audits(
                [{"action_type": "BCNB_UPDATE_CHECKER_NOTE"}],
                class_code="",
                lesson="1",
                week=43,
                year=2026,
            ),
            [],
        )
        self.assertEqual(
            extract_proven_auto_issues_from_audits(
                [{"action_type": "BCNB_UPDATE_CHECKER_NOTE"}],
                class_code="VT-C4K-PTB16",
                lesson="1",
                week=43,
                year=2026,
                session_id=None,
                class_date_iso=None,
            ),
            [],
        )

        # 2. read_checker_note_by_session with synthetic DOM rows
        def _make_cell(text: str, current_note: str | None = None):
            c = MagicMock()
            c.inner_text = AsyncMock(return_value=text)
            note_el = MagicMock()
            if current_note is not None:
                note_el.count = AsyncMock(return_value=1)
                note_el.get_attribute = AsyncMock(
                    side_effect=lambda k: current_note if k == "data-current-note" else None
                )
            else:
                note_el.count = AsyncMock(return_value=0)
            loc = MagicMock()
            loc.first = note_el
            c.locator.return_value = loc
            return c

        def _make_row(sid: str | None, cells: list):
            r = MagicMock()
            r.get_attribute = AsyncMock(
                side_effect=lambda k: sid if k == "data-session-id" else None
            )
            td_loc = MagicMock()
            td_loc.all = AsyncMock(return_value=cells)
            th_loc = MagicMock()
            th_loc.all = AsyncMock(return_value=cells)
            r.locator.side_effect = (
                lambda sel: td_loc if sel == "td" else th_loc
            )
            return r

        header_cells = [
            _make_cell("STT"),
            _make_cell("Mã lớp"),
            _make_cell("Buổi"),
            _make_cell("Ngày học"),
            _make_cell("Mô tả vấn đề (TE/TF/QC)"),
        ]
        header_row = _make_row(None, header_cells)
        data_row = _make_row(
            "6274",
            [
                _make_cell("1"),
                _make_cell("VT-C4K-PTB16"),
                _make_cell("1"),
                _make_cell("24/10/2026"),
                _make_cell("Ghi chú tay của QC", current_note="Ghi chú tay của QC"),
            ],
        )

        page = MagicMock()
        page.reload = AsyncMock()
        tr_loc = MagicMock()
        tr_loc.all = AsyncMock(return_value=[header_row, data_row])
        tr_loc.first = header_row
        page.locator.side_effect = lambda sel: tr_loc if sel == "table tr" else MagicMock()

        with (
            patch(
                "services.bcnb_safety.resolve_class_lesson_columns",
                AsyncMock(return_value=(1, 2)),
            ),
            patch(
                "services.bcnb_safety.resolve_extended_bcnb_columns",
                AsyncMock(return_value={"checker_note_index": 4}),
            ),
            patch(
                "services.bcnb_safety.select_week_checked",
                AsyncMock(return_value=(43, 2026)),
            ),
        ):
            inspected, note_val = await read_checker_note_by_session(
                page,
                session_id="6274",
                class_code="VT-C4K-PTB16",
                lesson="1",
            )
            self.assertEqual((inspected, note_val), (True, "Ghi chú tay của QC"))

            # Mismatched class_code returns (True, None)
            inspected_mm, note_mm = await read_checker_note_by_session(
                page,
                session_id="6274",
                class_code="VT-OTHER",
                lesson="1",
            )
            self.assertEqual((inspected_mm, note_mm), (True, None))

            # verify_checker_note_persisted returns (True, "Ghi chú tay của QC")
            persisted_ok, obs_val = await verify_checker_note_persisted(
                page,
                class_code="VT-C4K-PTB16",
                week=43,
                year=2026,
                expected_note="Ghi chú tay của QC",
                lesson="1",
                class_date_iso="2026-10-24",
                session_id="6274",
            )
            self.assertEqual((persisted_ok, obs_val), (True, "Ghi chú tay của QC"))

            # Whitespace mismatch ("  Ghi chú tay của QC\n" vs "Ghi chú tay của QC") returns False (conflict)
            persisted_ws_mismatch, obs_ws = await verify_checker_note_persisted(
                page,
                class_code="VT-C4K-PTB16",
                week=43,
                year=2026,
                expected_note="  Ghi chú tay của QC\n",
                lesson="1",
                class_date_iso="2026-10-24",
                session_id="6274",
            )
            self.assertEqual((persisted_ws_mismatch, obs_ws), (False, "Ghi chú tay của QC"))

            # Fallback inner_text path also preserves leading/trailing whitespace and newline
            ws_row = _make_row(
                "6274",
                [
                    _make_cell("1"),
                    _make_cell("VT-C4K-PTB16"),
                    _make_cell("1"),
                    _make_cell("24/10/2026"),
                    _make_cell("  QC fallback\n", current_note=None),
                ],
            )
            tr_loc.all = AsyncMock(return_value=[header_row, ws_row])
            inspected_ws, note_ws = await read_checker_note_by_session(
                page,
                session_id="6274",
                class_code="VT-C4K-PTB16",
                lesson="1",
            )
            self.assertEqual((inspected_ws, note_ws), (True, "  QC fallback\n"))
            persisted_ws_ok, obs_ws_ok = await verify_checker_note_persisted(
                page,
                class_code="VT-C4K-PTB16",
                week=43,
                year=2026,
                expected_note="  QC fallback\n",
                lesson="1",
                class_date_iso="2026-10-24",
                session_id="6274",
            )
            self.assertEqual((persisted_ws_ok, obs_ws_ok), (True, "  QC fallback\n"))



if __name__ == "__main__":
    unittest.main()
