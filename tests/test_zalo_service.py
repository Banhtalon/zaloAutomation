from datetime import datetime
import tempfile
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from core.time_utils import BUSINESS_TIMEZONE
from services.zalo_bcnb_service import (
    _apply_bcnb_deadline_state,
    _apply_lesson_14_result,
    _result_target_week,
    update_bcnb_reports,
)
import services.zalo_service as zalo_service


class ZaloServiceWeekAndDeadlineTests(unittest.TestCase):
    def test_result_period_requires_the_authoritative_bcnb_target(self):
        inferred_from_date = {
            "evidence": {"date_context": {"lesson_date": "2026-08-03"}}
        }
        explicit_bcnb_target = {
            "evidence": {
                "date_context": {
                    "lesson_date": "2026-08-03",
                    "target_week": 33,
                    "target_year": 2026,
                    "target_week_source": "BCNB_SERVER",
                }
            }
        }

        self.assertIsNone(_result_target_week(inferred_from_date))
        self.assertEqual(_result_target_week(explicit_bcnb_target), (33, 2026))

    def test_deadline_state_marks_late_report_submitted(self):
        result: dict[str, Any] = {
            "status": "✅ Đã nộp – Có minh chứng",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "evidence": {
                "date_context": {
                    "lesson_date": "2026-08-03",
                    "target_match_note": "Đã báo cáo nhưng ghi sai ngày; đã ghép BCNB.",
                },
                "classifier_result": {
                    "matched_ui_sent_at": "2026-08-06",
                    "candidates": [{"message_fingerprint": "abc"}],
                },
            },
        }
        row = {
            "ngay_hoc_iso": "2026-08-03",
            "zalo_deadline": "18:00\n05/08/2026",
        }

        self.assertTrue(
            _apply_bcnb_deadline_state(
                result,
                row,
                today=datetime(2026, 8, 6),
            )
        )
        self.assertEqual(result["evidence_state"], "SUBMITTED_LATE_VERIFIED")
        self.assertTrue(result["submitted_for_bcnb"])
        self.assertEqual(
            result["evidence"]["date_context"]["deadline_delay_days"],
            1,
        )
        self.assertEqual(
            result["evidence"]["classifier_result"]["decision"],
            "SUBMITTED_LATE",
        )
        self.assertIn("ghi sai ngày", result["status_detail"])

    def test_lesson_14_replaces_stale_renamed_group_warning(self):
        scan_results: list[dict[str, Any]] = [
            {
                "group": "MindX -LEGO-VT-KIND-ROB4A01 T6 18H-19H30",
                "status": "⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công",
                "evidence_state": "INSUFFICIENT_EVIDENCE",
                "evidence": {"validation_errors": ["Tên nhóm đã đổi"]},
            }
        ]
        row = {"ma_lop": "VT-KIND-ROB4A01"}

        exempted = _apply_lesson_14_result(scan_results, row)

        self.assertEqual(len(scan_results), 1)
        self.assertIs(scan_results[0], exempted)
        self.assertEqual(exempted["evidence_state"], "LESSON_14_EXEMPTION")
        self.assertEqual(exempted["status"], "✅ Miễn báo cáo (Buổi 14)")
        self.assertEqual(
            exempted["evidence"]["business_rule"]["source_evidence_state"],
            "INSUFFICIENT_EVIDENCE",
        )

    def test_lesson_14_uses_the_exact_week_when_group_has_two_scan_results(self):
        scan_results: list[dict[str, Any]] = [
            {
                "group": "MindX -LEGO-VT-KIND-ROB4A01 T6 18H-19H30",
                "status": "⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công",
                "evidence_state": "INSUFFICIENT_EVIDENCE",
                "evidence": {"date_context": {"target_week": 32, "target_year": 2026, "target_lesson": "14"}},
            },
            {
                "group": "MindX -LEGO-VT-KIND-ROB4A01 T6 18H-19H30",
                "status": "✅ Đã nộp – Có minh chứng",
                "evidence_state": "SUBMITTED_VERIFIED",
                "evidence": {"date_context": {"target_week": 33, "target_year": 2026, "target_lesson": "15"}},
            },
        ]
        row = {"ma_lop": "VT-KIND-ROB4A01", "buoi_so": "14", "week": 32, "year": 2026}

        exempted = _apply_lesson_14_result(scan_results, row)

        self.assertEqual(exempted["evidence"]["business_rule"]["source_evidence_state"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(scan_results[1]["evidence_state"], "SUBMITTED_VERIFIED")


class ZaloProductionPlanGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_worker_rejects_legacy_broad_job_without_snapshot(self):
        class Jobs:
            def __init__(self):
                self.updates = []

            async def update_job(self, job_id, **values):
                self.updates.append((job_id, values))

        jobs = Jobs()
        with tempfile.TemporaryDirectory() as evidence_root:
            with (
                patch.object(zalo_service, "job_repo", jobs),
                patch.object(zalo_service.evidence_repo, "expire_old", AsyncMock()),
            ):
                await zalo_service._run_zalo_automation(
                    123,
                    "production",
                    scan_plan=None,
                    evidence_root=evidence_root,
                )

        self.assertEqual(jobs.updates[-1][1]["status"], "FAILED")
        self.assertIn("snapshot hai tuần", jobs.updates[-1][1]["message"])

    async def test_worker_defers_same_day_target_without_opening_browser(self):
        class Jobs:
            def __init__(self):
                self.updates = []

            async def update_job(self, job_id, **values):
                self.updates.append((job_id, values))

            async def ensure_not_cancelled(self, _job_id):
                return None

        jobs = Jobs()
        scan_plan = {
            "latest_week": {"week": 33, "year": 2026},
            "targets": [
                {
                    "group_name": "MindX -LEGO-VT-KIND-ROB4I01 T6 18H-19H30",
                    "class_code": "VT-KIND-ROB4I01",
                    "lesson": "2",
                    "class_date": "14/08/2026",
                    "class_date_iso": "2026-08-14",
                    "week": 33,
                    "year": 2026,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as evidence_root:
            with (
                patch.object(zalo_service, "job_repo", jobs),
                patch.object(zalo_service.evidence_repo, "expire_old", AsyncMock()),
                patch.object(
                    zalo_service,
                    "now_business",
                    return_value=datetime(2026, 8, 14, 16, 5, tzinfo=BUSINESS_TIMEZONE),
                ),
            ):
                await zalo_service._run_zalo_automation(
                    124,
                    "production",
                    scan_plan=scan_plan,
                    evidence_root=evidence_root,
                )

        self.assertEqual(jobs.updates[-1][1]["status"], "SUCCESS")
        self.assertIn("đã hoãn 1 target", jobs.updates[-1][1]["message"])


class ZaloBcnbTimingGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_day_deferred_target_cannot_reach_bcnb_checkbox_action(self):
        class Jobs:
            async def ensure_not_cancelled(self, _job_id):
                return None

        row = {
            "cells": [],
            "week": 33,
            "year": 2026,
            "ma_lop": "VT-KIND-ROB4I01",
            "buoi_so": "2",
            "ngay_hoc_iso": "2026-08-14",
            "zalo_deadline": "19:30 16/08/2026",
        }

        async def read_rows(_page, *, week, **_kwargs):
            return [] if week == 32 else [row]

        action = AsyncMock()
        scan_plan = {
            "latest_week": {"week": 33, "year": 2026},
            "selected_weeks": [
                {"week": 32, "year": 2026},
                {"week": 33, "year": 2026},
            ],
            "targets": [
                {
                    "group_name": "MindX -LEGO-VT-KIND-ROB4I01 T6 18H-19H30",
                    "class_code": "VT-KIND-ROB4I01",
                    "lesson": "2",
                    "class_date": "14/08/2026",
                    "class_date_iso": "2026-08-14",
                    "week": 33,
                    "year": 2026,
                }
            ],
        }
        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", side_effect=read_rows),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(0, 1, 2))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", action),
        ):
            await update_bcnb_reports(
                object(),
                job_id=999,
                execution_mode="production",
                scan_results=[],
                today=datetime(2026, 8, 14, 0, 0, tzinfo=BUSINESS_TIMEZONE),
                now=datetime(2026, 8, 14, 16, 5, tzinfo=BUSINESS_TIMEZONE),
                scan_plan=scan_plan,
                job_repo=Jobs(),
                audit_repo=object(),
            )

        action.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
