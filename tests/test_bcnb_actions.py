from datetime import datetime
import json
import subprocess
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.time_utils import BUSINESS_TIMEZONE
from services.bcnb_actions import (
    _UPDATE_CHECKER_NOTE_JS,
    BcnbBeforeWriteError,
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
        merged_1 = merge_checker_issue_note(
            "GV báo xin nộp muộn [đã duyệt]",
            [
                "[LMS] Chưa ghi nội dung bài học",
                "[Zalo] Báo cáo Zalo ghi sai ngày học (ghi 23/10/2026, lịch BCNB 24/10/2026)",
            ],
        )
        self.assertEqual(
            merged_1,
            "GV báo xin nộp muộn [đã duyệt] | [LMS] Chưa ghi nội dung bài học; [Zalo] Báo cáo Zalo ghi sai ngày học (ghi 23/10/2026, lịch BCNB 24/10/2026)",
        )
        # Idempotent re-merge
        merged_2 = merge_checker_issue_note(
            merged_1,
            [
                "[LMS] Chưa ghi nội dung bài học",
                "[Zalo] Báo cáo Zalo ghi sai ngày học (ghi 23/10/2026, lịch BCNB 24/10/2026)",
            ],
        )
        self.assertEqual(merged_2, merged_1)

        manual, auto_items = split_checker_issue_note(merged_2)
        self.assertEqual(manual, "GV báo xin nộp muộn [đã duyệt]")
        self.assertEqual(len(auto_items), 2)

        # Clearing resolved auto issues preserves manual note
        cleared = merge_checker_issue_note(merged_2, [])
        self.assertEqual(cleared, "GV báo xin nộp muộn [đã duyệt]")

        # Legacy [Auto QC: ...] note is also unwrapped cleanly when re-merged or cleared
        legacy_cleared = merge_checker_issue_note(
            "[Auto QC: [LMS] Nhận xét HS copy-paste trùng lặp (5/5 HS)]",
            [],
        )
        self.assertEqual(legacy_cleared, "")

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
                    "qc_issue_note": f"{manual_with_phrase} | [Auto QC: [LMS] Nhận xét HS copy-paste trùng lặp (3/3 HS); [LMS] Chưa ghi nội dung bài học]"
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
            split_checker_issue_note(merged_internal_delim),
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
        manual_out, auto_out = split_checker_issue_note(merged_long)
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
                    "slot_index": 0,
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



if __name__ == "__main__":
    unittest.main()
