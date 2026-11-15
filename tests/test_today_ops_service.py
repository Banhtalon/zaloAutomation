from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.time_utils import BUSINESS_TIMEZONE
from db.database import database_context, init_db
from repositories.audit_repository import AuditRepository
from repositories.evidence_repository import EvidenceRepository
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobRepository
from repositories.report_repository import ReportRepository
from repositories.zalo_batch_repository import ZaloBatchRepository, canonical_target_key
from services.external_gateways import ExternalGateways
from services.runtime import WorkerRuntime
from services.today_ops_service import (
    _scan_ended_zalo_targets,
    build_today_overview,
    parse_target_date_iso,
    run_today_realtime_sync,
)
from services.zalo_bcnb_service import update_bcnb_reports
from services.zalo_parallel_service import (
    TARGET_TIMEOUT,
    ScanPageState,
    TargetSearchError,
    TargetTimeoutError,
    ZaloSetupError,
)


def _make_two_week_snapshot() -> dict:
    discovered = [
        {"TenNhom": "MindX - VT-PTA01 (08:00-10:00)", "MaLop": "VT-PTA01", "CohortKey": "VT-PTA01|K1"},
        {"TenNhom": "MindX - VT-PTB01 (10:00-12:00)", "MaLop": "VT-PTB01", "CohortKey": "VT-PTB01|K1"},
        {"TenNhom": "MindX - VT-ROBO01 (14:00-16:00)", "MaLop": "VT-ROBO01", "CohortKey": "VT-ROBO01|K1"},
        {"TenNhom": "MindX - VT-JS01 (18:00-20:00)", "MaLop": "VT-JS01", "CohortKey": "VT-JS01|K1"},
        {"TenNhom": "MindX - VT-CS02", "MaLop": "VT-CS02", "CohortKey": "VT-CS02|K1"},
    ]
    return {
        "version": 4,
        "reliable": True,
        "discovered_groups": discovered,
        "source_group_names": [d["TenNhom"] for d in discovered],
        "expected_count": len(discovered),
        "bcnb_week_plan": {
            "latest_week": {"week": 46, "year": 2026},
            "required_week": {"week": 45, "year": 2026},
            "selected_weeks": [
                {"week": 45, "year": 2026},
                {"week": 46, "year": 2026},
            ],
            "required_data": [
                {
                    "ma_lop": "VT-PTA01",
                    "buoi_so": "12",
                    "ngay_hoc": "14/11/2026",
                    "ngay_hoc_iso": "2026-11-14",
                    "giao_vien": "GV A",
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-45-pta",
                },
                {
                    "ma_lop": "VT-UNMATCHED-01",
                    "buoi_so": "5",
                    "ngay_hoc": "14/11/2026",
                    "ngay_hoc_iso": "2026-11-14",
                    "giao_vien": "GV Unmatched",
                    "khoi": "Robotics",
                    "bcnb_session_id": "sess-45-unmatched",
                },
            ],
            "latest_data": [
                {
                    "ma_lop": "VT-PTB01",
                    "buoi_so": "13",
                    "ngay_hoc": "14/11/2026",
                    "ngay_hoc_iso": "2026-11-14",
                    "giao_vien": "GV B",
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-46-ptb",
                },
                {
                    "ma_lop": "VT-ROBO01",
                    "buoi_so": "12",
                    "ngay_hoc": "14/11/2026",
                    "ngay_hoc_iso": "2026-11-14",
                    "giao_vien": "GV R",
                    "khoi": "Robotics",
                    "bcnb_session_id": "sess-46-robo",
                },
                {
                    "ma_lop": "VT-JS01",
                    "buoi_so": "08",
                    "ngay_hoc": "14/11/2026",
                    "ngay_hoc_iso": "2026-11-14",
                    "giao_vien": "GV Late",
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-46-js",
                },
                {
                    "ma_lop": "VT-CS02",
                    "buoi_so": "04",
                    "ngay_hoc": "14/11/2026",
                    "ngay_hoc_iso": "2026-11-14",
                    "giao_vien": "GV Night",
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-46-cs",
                },
            ],
        },
        "scan_targets": [
            {
                "group_name": "MindX - VT-PTA01 (08:00-10:00)",
                "person": "GV A",
                "class_code": "VT-PTA01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "week": 45,
                "year": 2026,
                "khoi": "Coding",
                "bcnb_session_id": "sess-45-pta",
            },
            {
                "group_name": "MindX - VT-PTB01 (10:00-12:00)",
                "person": "GV B",
                "class_code": "VT-PTB01",
                "lesson": "13",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "week": 46,
                "year": 2026,
                "khoi": "Coding",
                "bcnb_session_id": "sess-46-ptb",
            },
        ],
        "deferred_targets": [
            {
                "group_name": "MindX - VT-ROBO01 (14:00-16:00)",
                "person": "GV R",
                "class_code": "VT-ROBO01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "week": 46,
                "year": 2026,
                "khoi": "Robotics",
                "bcnb_session_id": "sess-46-robo",
            },
            {
                "group_name": "MindX - VT-JS01 (18:00-20:00)",
                "person": "GV Late",
                "class_code": "VT-JS01",
                "lesson": "08",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "week": 46,
                "year": 2026,
                "khoi": "Coding",
                "bcnb_session_id": "sess-46-js",
            },
            {
                "group_name": "MindX - VT-CS02",
                "person": "GV Night",
                "class_code": "VT-CS02",
                "lesson": "04",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "week": 46,
                "year": 2026,
                "khoi": "Coding",
                "bcnb_session_id": "sess-46-cs",
            },
        ],
        "exempt_targets": [],
        "dropped_rows": {
            "missing_zalo": [
                {
                    "class_code": "VT-UNMATCHED-01",
                    "lesson": "5",
                    "class_date": "14/11/2026",
                    "class_date_iso": "2026-11-14",
                    "week": 45,
                    "year": 2026,
                    "reason": "MISSING_ZALO_GROUP",
                    "candidate_group_names": ["MindX - VT-UNMATCHED"],
                }
            ],
            "counts": {"total": 1, "missing_zalo": 1},
        },
    }


def _make_fake_cell():
    checkbox = MagicMock()
    checkbox.count = AsyncMock(return_value=1)
    checkbox.is_checked = AsyncMock(return_value=False)
    cell = MagicMock()
    cell.locator.return_value.first = checkbox
    return cell


def _make_fake_bcnb_row(
    *,
    class_code: str,
    lesson: str,
    date_str: str,
    date_iso: str,
    session_id: str,
    zalo_deadline: str = "15/11/2026",
    lms_deadline: str = "15/11/2026",
    nx_zalo_checked: bool = False,
    nx_lms_checked: bool = False,
) -> dict:
    return {
        "cells": [_make_fake_cell() for _ in range(15)],
        "ma_lop": class_code,
        "buoi_so": lesson,
        "ngay_hoc": date_str,
        "ngay_hoc_iso": date_iso,
        "bcnb_session_id": session_id,
        "zalo_deadline": zalo_deadline,
        "lms_deadline": lms_deadline,
        "nx_zalo_checked": nx_zalo_checked,
        "nx_lms_checked": nx_lms_checked,
        "qc_issue_note": "",
        "instructor_note": "",
    }


class TodayOpsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "today.sqlite3")
        self.evidence_root = str(Path(self.tmpdir.name) / "screenshots")
        init_db(self.db_path)
        self.db_ctx = database_context(self.db_path)
        self.db_ctx.__enter__()
        self.job_repo = JobRepository()
        self.group_repo = GroupRepository()
        self.audit_repo = AuditRepository()
        self.evidence_repo = EvidenceRepository()
        self.batch_repo = ZaloBatchRepository()
        self.report_repo = ReportRepository()

    async def asyncTearDown(self) -> None:
        self.db_ctx.__exit__(None, None, None)
        self.tmpdir.cleanup()

    def test_parse_target_date_iso_validation(self) -> None:
        self.assertEqual(
            parse_target_date_iso("2026-11-14", default_date=datetime(2026, 11, 15).date()),
            "2026-11-14",
        )
        self.assertEqual(
            parse_target_date_iso(None, default_date=datetime(2026, 11, 15).date()),
            "2026-11-15",
        )
        with self.assertRaises(ValueError):
            parse_target_date_iso("14/11/2026")
        with self.assertRaises(ValueError):
            parse_target_date_iso("2026-02-31")

    async def test_update_bcnb_reports_tick_lms_3_stage_and_skips_unready(self) -> None:
        now_dt = datetime(2026, 11, 14, 18, 0, tzinfo=BUSINESS_TIMEZONE)
        today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        page = MagicMock()
        audit_repo = AsyncMock()
        job_repo = AsyncMock()

        ready_res = {
            "group": "MindX - VT-PTA01 (08:00-10:00)",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "delay_days": 0,
            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5},
            "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
            "lms_spck": {"status": "COMPLETE", "submitted_count": 5, "missing_comment_count": 0},
            "scan_target": {
                "week": 45,
                "year": 2026,
                "class_code": "VT-PTA01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "bcnb_session_id": "sess-pta-12",
            },
            "evidence": {
                "date_context": {
                    "target_week": 45,
                    "target_year": 2026,
                    "target_class_code": "VT-PTA01",
                    "target_lesson": "12",
                    "target_class_date": "2026-11-14",
                },
                "classifier_result": {"matched_ui_sent_at": "14/11/2026"},
                "images": {"proof": {"path": "/api/evidence/1/a.png", "sha256": "ABC"}},
            },
        }
        unready_res = {
            "group": "MindX - VT-JS01 (10:00-12:00)",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "delay_days": 0,
            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 4},
            "lms_comments": {"status": "MISSING", "submitted_count": 0},
            "lms_spck": {"status": "COMPLETE", "submitted_count": 4, "missing_comment_count": 0},
            "scan_target": {
                "week": 45,
                "year": 2026,
                "class_code": "VT-JS01",
                "lesson": "8",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "bcnb_session_id": "sess-js-8",
            },
            "evidence": {
                "date_context": {
                    "target_week": 45,
                    "target_year": 2026,
                    "target_class_code": "VT-JS01",
                    "target_lesson": "8",
                    "target_class_date": "2026-11-14",
                },
                "classifier_result": {"matched_ui_sent_at": "14/11/2026"},
                "images": {"proof": {"path": "/api/evidence/1/b.png", "sha256": "DEF"}},
            },
        }

        row_pta = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-pta-12",
        )
        row_js = _make_fake_bcnb_row(
            class_code="VT-JS01",
            lesson="8",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-js-8",
        )
        read_rows = AsyncMock(return_value=[row_pta, row_js])
        tick_mock = AsyncMock(return_value="VERIFIED")
        note_mock = AsyncMock(return_value="NO_CHANGE")

        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", read_rows),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", note_mock),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-PTA01": [{}], "VT-JS01": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=77,
                execution_mode="production",
                scan_results=[ready_res, unready_res],
                today=today,
                now=now_dt,
                scan_plan={
                    "latest_week": {"week": 46, "year": 2026},
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                },
                job_repo=job_repo,
                audit_repo=audit_repo,
                tick_lms=True,
                check_only=False,
            )

        self.assertEqual(summary["updated_count"], 2)
        self.assertEqual(summary["lms_updated_count"], 1)
        self.assertEqual(len(summary["lms_target_outcomes"]), 1)
        self.assertEqual(summary["lms_target_outcomes"][0]["class_code"], "VT-PTA01")
        self.assertEqual(summary["lms_target_outcomes"][0]["status"], "VERIFIED")
        self.assertTrue(ready_res.get("nx_zalo_checked"))
        self.assertTrue(ready_res.get("nx_lms_checked"))
        self.assertIs(unready_res.get("nx_lms_checked"), False)

        action_types = [call.kwargs["action_type"] for call in tick_mock.await_args_list]
        self.assertEqual(
            action_types,
            ["BCNB_REPORT_TICK", "BCNB_LMS_TICK", "BCNB_REPORT_TICK"],
        )
        lms_call_kwargs = tick_mock.await_args_list[1].kwargs
        self.assertEqual(lms_call_kwargs["column_kind"], "lms")
        self.assertEqual(lms_call_kwargs["session_id"], "sess-pta-12")
        self.assertEqual(lms_call_kwargs["class_date_iso"], "2026-11-14")
        self.assertFalse(lms_call_kwargs["require_evidence_registry"])

    async def test_update_bcnb_reports_check_only_reads_flags_with_zero_writes(self) -> None:
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        page = MagicMock()
        audit_repo = AsyncMock()
        job_repo = AsyncMock()

        item = {
            "group": "MindX - VT-PTA01 (08:00-10:00)",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "scan_target": {
                "week": 45,
                "year": 2026,
                "class_code": "VT-PTA01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "bcnb_session_id": "sess-pta-12",
            },
            "evidence": {
                "date_context": {
                    "target_week": 45,
                    "target_year": 2026,
                    "target_class_code": "VT-PTA01",
                    "target_lesson": "12",
                    "target_class_date": "2026-11-14",
                },
            },
        }
        row_pta = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-pta-12",
            nx_zalo_checked=True,
            nx_lms_checked=False,
        )
        tick_mock = AsyncMock()
        note_mock = AsyncMock()

        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(return_value=[row_pta])),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", note_mock),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-PTA01": [{}], "VT-JS01": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=78,
                execution_mode="dry-run",
                scan_results=[item],
                today=today,
                now=now_dt,
                scan_plan={
                    "latest_week": {"week": 46, "year": 2026},
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                },
                job_repo=job_repo,
                audit_repo=audit_repo,
                tick_lms=True,
                check_only=True,
            )

        self.assertEqual(summary["updated_count"], 0)
        self.assertEqual(summary["lms_updated_count"], 0)
        tick_mock.assert_not_awaited()
        note_mock.assert_not_awaited()
        self.assertIs(item["nx_zalo_checked"], True)
        self.assertIs(item["nx_lms_checked"], False)

    async def test_build_today_overview_two_weeks_tracks_phases_and_overlays(self) -> None:
        plan = _make_two_week_snapshot()
        await self.group_repo.apply_discovery_snapshot(plan)

        # Overlay from a check_only job (should still appear in Today overview!)
        check_job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"target_date_iso": "2026-11-14", "check_only": True, "execution_mode": "dry-run"},
        )
        await self.job_repo.update_job(check_job_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            check_job_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "selective_rescan": True,
                    "check_only": True,
                    "target_date_iso": "2026-11-14",
                    "today_items": [
                        {
                            "group": "MindX - VT-PTA01 (08:00-10:00)",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "nx_zalo_checked": True,
                            "nx_lms_checked": False,
                            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5},
                            "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
                            "lms_spck": {
                                "status": "MISSING",
                                "submitted_count": 3,
                                "attended_count": 5,
                                "missing_comment_count": 1,
                                "missing_image_count": 1,
                                "missing_code_count": 2,
                            },
                            "scan_target": {
                                "week": 45,
                                "year": 2026,
                                "class_code": "VT-PTA01",
                                "lesson": "12",
                                "class_date_iso": "2026-11-14",
                                "bcnb_session_id": "sess-45-pta",
                            },
                            "evidence": {
                                "images": {
                                    "proof": {"path": f"/api/evidence/{check_job_id}/pta.png", "sha256": "111"}
                                }
                            },
                        },
                        {
                            "group": "MindX - VT-PTB01 (10:00-12:00)",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 4},
                            "lms_comments": {"status": "COMPLETE", "submitted_count": 4},
                            "lms_spck": {"status": "EXEMPT", "submitted_count": 0, "missing_comment_count": 0},
                            "scan_target": {
                                "week": 46,
                                "year": 2026,
                                "class_code": "VT-PTB01",
                                "lesson": "13",
                                "class_date_iso": "2026-11-14",
                                "bcnb_session_id": "sess-46-ptb",
                            },
                        },
                        {
                            "group": "MindX - VT-ROBO01 (14:00-16:00)",
                            "evidence_state": "DEFERRED",
                            "lms_spck": {
                                "status": "COMPLETE",
                                "submitted_count": 4,
                                "attended_count": 4,
                                "missing_comment_count": 0,
                            },
                            "scan_target": {
                                "week": 46,
                                "year": 2026,
                                "class_code": "VT-ROBO01",
                                "lesson": "12",
                                "class_date_iso": "2026-11-14",
                                "bcnb_session_id": "sess-46-robo",
                            },
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        )

        # Fake production job must NOT leak into dry-run mode overview
        prod_job_id = await self.job_repo.create_job(
            "ZALO_SCAN",
            {"execution_mode": "production"},
            mutates_external=True,
        )
        await self.job_repo.update_job(prod_job_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            prod_job_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                [
                    {
                        "group": "MindX - VT-JS01 (18:00-20:00)",
                        "status": "✅ Đã báo cáo",
                        "evidence_state": "SUBMITTED_VERIFIED",
                        "scan_target": {
                            "week": 46,
                            "year": 2026,
                            "class_code": "VT-JS01",
                            "lesson": "08",
                            "class_date_iso": "2026-11-14",
                        },
                    }
                ],
                ensure_ascii=False,
            ),
        )

        fake_container = MagicMock()
        fake_container.settings.bcnb_dry_run = True
        fake_container.settings.zalo_plan_max_age_minutes = 30
        fake_container.group_repo = self.group_repo

        # 15:00 local: 08-10 ENDED, 10-12 ENDED, 14-16 ONGOING, 18-20 UPCOMING, CS02 TIME_UNKNOWN
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        overview = await build_today_overview(
            fake_container,
            target_date_iso="2026-11-14",
            now=now_dt,
        )

        self.assertEqual(overview["target_date_iso"], "2026-11-14")
        self.assertEqual(overview["execution_mode"], "dry-run")
        items = overview["items"]
        self.assertEqual(len(items), 6)

        by_code = {it["class_code"]: it for it in items}
        self.assertEqual(by_code["VT-PTA01"]["week"], 45)
        self.assertEqual(by_code["VT-PTA01"]["phase"], "ENDED")
        self.assertEqual(by_code["VT-PTA01"]["course_track"], "CODING")
        self.assertTrue(by_code["VT-PTA01"]["is_coding"])
        self.assertTrue(by_code["VT-PTA01"]["requires_spck"])
        self.assertEqual(by_code["VT-PTA01"]["screenshot_url"], f"/api/evidence/{check_job_id}/pta.png")
        self.assertTrue(by_code["VT-PTA01"]["lms_ready_for_tick"])
        self.assertIs(by_code["VT-PTA01"]["nx_lms_checked"], False)

        self.assertEqual(by_code["VT-PTB01"]["week"], 46)
        self.assertTrue(by_code["VT-PTB01"]["spck_exempt_course"])
        self.assertEqual(by_code["VT-PTB01"]["lms_spck"]["summary_text"], "SPCK không áp dụng (toàn bộ học viên nghỉ)")
        self.assertEqual(by_code["VT-ROBO01"]["phase"], "ONGOING")
        self.assertTrue(by_code["VT-ROBO01"]["is_robotics"])
        self.assertEqual(by_code["VT-JS01"]["phase"], "UPCOMING")
        self.assertEqual(by_code["VT-JS01"]["zalo_category"], "deferred")
        self.assertEqual(by_code["VT-CS02"]["phase"], "TIME_UNKNOWN")
        self.assertTrue(by_code["VT-UNMATCHED-01"]["missing_zalo_group"])
        self.assertEqual(by_code["VT-UNMATCHED-01"]["phase"], "TIME_UNKNOWN")

        kpis = overview["kpis"]
        self.assertEqual(kpis["total"]["count"], 6)
        self.assertEqual(kpis["total"]["ended"], 2)
        self.assertEqual(kpis["total"]["ongoing"], 1)
        self.assertEqual(kpis["total"]["upcoming"], 1)
        self.assertEqual(kpis["total"]["missing_zalo_group"], 1)
        self.assertEqual(kpis["spck_b12_14"]["total"], 3)
        self.assertEqual(kpis["spck_b12_14"]["coding_total"], 1)
        self.assertEqual(kpis["spck_b12_14"]["coding_incomplete"], 1)
        self.assertEqual(kpis["spck_b12_14"]["robotics_total"], 1)
        self.assertEqual(kpis["spck_b12_14"]["robotics_complete"], 1)
        self.assertEqual(kpis["spck_b12_14"]["exempt_course_total"], 1)
        self.assertEqual(kpis["bcnb_pending"]["need_nx_lms"], 1)

        # Chronological sort: timed entries come before TIME_UNKNOWN ('99:99')
        self.assertEqual(
            [it["class_code"] for it in items[:4]],
            ["VT-PTA01", "VT-PTB01", "VT-ROBO01", "VT-JS01"],
        )

    async def test_run_today_realtime_sync_defers_unended_and_registers_ended(self) -> None:
        plan = _make_two_week_snapshot()
        await self.group_repo.apply_discovery_snapshot(plan)
        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"target_date_iso": "2026-11-14", "check_only": False, "tick_lms": True},
        )

        class _FakeSession:
            landing_facts_provider = None

        @asynccontextmanager
        async def _persistent_session():
            yield _FakeSession()

        browser_gw = MagicMock()
        browser_gw.persistent_session = _persistent_session
        lms_gw = MagicMock()
        lms_gw.fetch_class_slots = AsyncMock(
            return_value={
                "VT-PTA01": [{"lessonNumber": 12, "startTime": "08:00", "endTime": "10:00", "comments": [], "students": []}],
                "VT-PTB01": [{"lessonNumber": 13, "startTime": "10:00", "endTime": "12:00", "comments": [], "students": []}],
                "VT-ROBO01": [{"lessonNumber": 12, "startTime": "14:00", "endTime": "16:00", "comments": [], "students": []}],
                "VT-JS01": [{"lessonNumber": 8, "startTime": "18:00", "endTime": "20:00", "comments": [], "students": []}],
            }
        )
        gateways = ExternalGateways(
            browser=browser_gw,
            lms_ocr=MagicMock(),
            student_ocr_factory=MagicMock,
            lms_quality=lms_gw,
        )
        runtime = WorkerRuntime(external=gateways, evidence_root=self.evidence_root)

        scanned_codes: list[str] = []

        async def _fake_scan_one(_page, *, job_id: int, target: dict, screenshots_dir: str, **_kwargs):
            scanned_codes.append(target["class_code"])
            shot_file = Path(screenshots_dir) / f"{target['class_code']}.png"
            shot_file.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            return {
                "group": target["group_name"],
                "person": target["person"],
                "status": "✅ Đã báo cáo",
                "evidence_state": "SUBMITTED_VERIFIED",
                "submitted_for_bcnb": True,
                "scan_target": dict(target),
                "evidence": {
                    "images": {
                        "proof": {
                            "path": f"/api/evidence/{job_id}/{target['class_code']}.png",
                            "stored_path": str(shot_file),
                            "sha256": "ABCD",
                        }
                    },
                    "date_context": {
                        "target_week": target["week"],
                        "target_year": target["year"],
                        "target_class_code": target["class_code"],
                        "target_lesson": target["lesson"],
                        "target_class_date": target["class_date_iso"],
                    },
                },
            }

        bcnb_calls: list[dict] = []

        async def _fake_update_bcnb(page, **kwargs):
            bcnb_calls.append(kwargs)
            return {
                "selected_weeks": kwargs["scan_plan"]["selected_weeks"],
                "updated_count": 2,
                "lms_updated_count": 1,
                "failed_targets": [],
                "lms_failed_targets": [],
                "target_outcomes": [],
                "lms_target_outcomes": [],
            }

        fake_page = MagicMock()
        fake_page.goto = AsyncMock()
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        with (
            patch("services.today_ops_service._new_scan_page", AsyncMock(return_value=fake_page)),
            patch("services.today_ops_service._close_scan_page", AsyncMock()),
            patch("services.today_ops_service._setup_scan_page", AsyncMock(return_value={"decision": "SYNC_VERIFIED", "verified": True})),
            patch("services.today_ops_service._scan_one_target", AsyncMock(side_effect=_fake_scan_one)),
            patch("services.today_ops_service._login_bcnb_if_needed", AsyncMock()),
            patch("services.today_ops_service.update_bcnb_reports", AsyncMock(side_effect=_fake_update_bcnb)),
            patch("services.today_ops_service.Config.BCNB_URL", "https://bcnb.example.invalid"),
        ):
            await run_today_realtime_sync(
                job_id,
                {"target_date_iso": "2026-11-14", "check_only": False, "tick_lms": True},
                "dry-run",
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                group_repo=self.group_repo,
                audit_repo=self.audit_repo,
                evidence_repo=self.evidence_repo,
                batch_repo=self.batch_repo,
                now_override=now_dt,
            )

        # Only ENDED sessions with Zalo group were scanned; ONGOING (ROBO01), UPCOMING (JS01), TIME_UNKNOWN (CS02), and UNMATCHED were NOT scanned in Zalo
        self.assertEqual(scanned_codes, ["VT-PTA01", "VT-PTB01"])
        self.assertEqual(len(bcnb_calls), 1)
        self.assertTrue(bcnb_calls[0]["tick_lms"])
        self.assertFalse(bcnb_calls[0]["check_only"])

        job = await self.job_repo.get_job(job_id)
        self.assertEqual(job["status"], "SUCCESS")
        results = json.loads(job["results"])
        self.assertTrue(results["selective_rescan"])
        self.assertEqual(len(results["items"]), 2)
        self.assertEqual({it["scan_target"]["class_code"] for it in results["items"]}, {"VT-PTA01", "VT-PTB01"})
        self.assertEqual(len(results["today_items"]), 6)

    async def test_weekly_summary_merges_today_sync_and_ignores_check_only(self) -> None:
        snap = await self.group_repo.apply_discovery_snapshot(_make_two_week_snapshot())
        snap_id = snap["snapshot_id"]

        # Full-week baseline ZALO_SCAN on Monday W46/2026
        mon_job_id = await self.job_repo.create_job(
            "ZALO_SCAN",
            {
                "execution_mode": "dry-run",
                "scan_plan": {
                    "snapshot_id": snap_id,
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                },
            },
        )
        await self.job_repo.update_job(mon_job_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            mon_job_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "snapshot_id": snap_id,
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                    "items": [
                        {
                            "group": "MindX - VT-MON01 (08:00-10:00)",
                            "person": "GV Mon",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "scan_target": {
                                "week": 46,
                                "year": 2026,
                                "class_code": "VT-MON01",
                                "lesson": "3",
                                "class_date": "10/11/2026",
                                "class_date_iso": "2026-11-10",
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

        # Mutating (non-check-only) TODAY_REALTIME_SYNC on Saturday W46/2026
        today_sync_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "dry-run", "check_only": False, "target_date_iso": "2026-11-14"},
        )
        await self.job_repo.update_job(today_sync_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            today_sync_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "selective_rescan": True,
                    "check_only": False,
                    "snapshot_id": snap_id,
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                    "items": [
                        {
                            "group": "MindX - VT-PTB01 (10:00-12:00)",
                            "person": "GV B",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "scan_target": {
                                "week": 46,
                                "year": 2026,
                                "class_code": "VT-PTB01",
                                "lesson": "13",
                                "class_date": "14/11/2026",
                                "class_date_iso": "2026-11-14",
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

        # Read-only check_only=True job (both active and completed) must NEVER appear or block Weekly Summary
        check_done_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "dry-run", "check_only": True, "target_date_iso": "2026-11-14"},
        )
        await self.job_repo.update_job(check_done_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            check_done_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "selective_rescan": True,
                    "check_only": True,
                    "snapshot_id": snap_id,
                    "items": [
                        {
                            "group": "MindX - SHOULD-NOT-APPEAR",
                            "status": "❌ Chưa báo cáo",
                            "evidence_state": "NOT_FOUND_VERIFIED",
                            "scan_target": {
                                "week": 46,
                                "year": 2026,
                                "class_code": "VT-SHOULD-NOT-APPEAR",
                                "lesson": "1",
                                "class_date_iso": "2026-11-14",
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
        check_active_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "dry-run", "check_only": True, "target_date_iso": "2026-11-14", "week": 46, "year": 2026},
        )
        await self.job_repo.update_job(check_active_id, status="RUNNING", progress=20)

        active_blocking = await self.report_repo.get_active_mutation_jobs_for_week(week=46, year=2026)
        self.assertEqual(active_blocking, [])

        # Non-check-only active sync SHOULD appear in get_active_mutation_jobs_for_week
        sync_active_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "dry-run", "check_only": False, "target_date_iso": "2026-11-14", "week": 46, "year": 2026},
        )
        await self.job_repo.update_job(sync_active_id, status="RUNNING", progress=30)
        active_with_sync = await self.report_repo.get_active_mutation_jobs_for_week(week=46, year=2026)
        self.assertEqual([j["job_id"] for j in active_with_sync], [sync_active_id])
        await self.job_repo.update_job(sync_active_id, status="CANCELLED", progress=100)

        summary = await self.report_repo.get_weekly_summary(
            week=46,
            year=2026,
            execution_mode="dry-run",
        )
        row_codes = {row["class_code"] for row in summary["rows"]}
        self.assertIn("VT-MON01", row_codes)
        self.assertIn("VT-PTB01", row_codes)
        self.assertNotIn("VT-SHOULD-NOT-APPEAR", row_codes)

    async def test_weekly_summary_preserves_compatible_baseline_across_refreshed_snapshot(self) -> None:
        base_rows = [
            {
                "ma_lop": f"VT-BASE0{idx}",
                "buoi_so": str(idx),
                "ngay_hoc": f"1{idx}/11/2026",
                "ngay_hoc_iso": f"2026-11-1{idx}",
                "giao_vien": f"GV {idx}",
                "khoi": "Coding",
                "bcnb_session_id": f"sess-b-{idx}",
            }
            for idx in range(1, 6)
        ]
        snap1_payload = {
            "version": 4,
            "reliable": True,
            "discovered_groups": [
                {"TenNhom": f"MindX - VT-BASE0{idx} (18:00-20:00)", "MaLop": f"VT-BASE0{idx}", "CohortKey": f"K{idx}"}
                for idx in range(1, 6)
            ],
            "source_group_names": [f"MindX - VT-BASE0{idx} (18:00-20:00)" for idx in range(1, 6)],
            "expected_count": 5,
            "bcnb_week_plan": {
                "latest_week": {"week": 46, "year": 2026},
                "required_week": {"week": 45, "year": 2026},
                "selected_weeks": [{"week": 46, "year": 2026}],
                "latest_data": base_rows,
                "required_data": [],
            },
            "scan_targets": [
                {
                    "class_code": r["ma_lop"],
                    "group_name": f"MindX - {r['ma_lop']} (18:00-20:00)",
                    "person": r["giao_vien"],
                    "week": 46,
                    "year": 2026,
                    "lesson": r["buoi_so"],
                    "class_date": r["ngay_hoc"],
                    "class_date_iso": r["ngay_hoc_iso"],
                    "bcnb_session_id": r["bcnb_session_id"],
                }
                for r in base_rows
            ],
            "deferred_targets": [],
            "exempt_targets": [],
        }
        snap1 = await self.group_repo.apply_discovery_snapshot(snap1_payload)
        snap1_id = snap1["snapshot_id"]

        mon_job_id = await self.job_repo.create_job(
            "ZALO_SCAN",
            {
                "execution_mode": "production",
                "scan_plan": {
                    "snapshot_id": snap1_id,
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 46, "year": 2026}],
                },
            },
            mutates_external=True,
        )
        await self.job_repo.update_job(mon_job_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            mon_job_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "snapshot_id": snap1_id,
                    "selected_weeks": [{"week": 46, "year": 2026}],
                    "items": [
                        {
                            "group": f"MindX - {r['ma_lop']} (18:00-20:00)",
                            "person": r["giao_vien"],
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "bcnb_session_id": r["bcnb_session_id"],
                            "scan_target": {
                                "week": 46,
                                "year": 2026,
                                "class_code": r["ma_lop"],
                                "lesson": r["buoi_so"],
                                "class_date": r["ngay_hoc"],
                                "class_date_iso": r["ngay_hoc_iso"],
                                "bcnb_session_id": r["bcnb_session_id"],
                            },
                        }
                        for r in base_rows
                    ],
                },
                ensure_ascii=False,
            ),
        )

        # Snapshot 2 refreshes W46 keeping BASE01..04 and removing BASE05
        snap2_payload = {
            **snap1_payload,
            "discovered_groups": snap1_payload["discovered_groups"][:4],
            "source_group_names": snap1_payload["source_group_names"][:4],
            "expected_count": 4,
            "bcnb_week_plan": {
                **snap1_payload["bcnb_week_plan"],
                "latest_data": base_rows[:4],
            },
            "scan_targets": snap1_payload["scan_targets"][:4],
        }
        snap2 = await self.group_repo.apply_discovery_snapshot(snap2_payload)
        snap2_id = snap2["snapshot_id"]

        today_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "production", "check_only": False, "target_date_iso": "2026-11-12"},
            mutates_external=True,
        )
        await self.job_repo.update_job(today_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            today_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "selective_rescan": True,
                    "check_only": False,
                    "snapshot_id": snap2_id,
                    "selected_weeks": [{"week": 46, "year": 2026}],
                    "items": [
                        {
                            "group": "MindX - VT-BASE02 (18:00-20:00)",
                            "person": "GV 2",
                            "status": "🟡 Đã báo cáo trễ",
                            "evidence_state": "SUBMITTED_LATE_VERIFIED",
                            "bcnb_session_id": "sess-b-2",
                            "scan_target": {
                                "week": 46,
                                "year": 2026,
                                "class_code": "VT-BASE02",
                                "lesson": "2",
                                "class_date": "12/11/2026",
                                "class_date_iso": "2026-11-12",
                                "bcnb_session_id": "sess-b-2",
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

        summary = await self.report_repo.get_weekly_summary(
            week=46,
            year=2026,
            execution_mode="production",
        )
        row_by_code = {row["class_code"]: row for row in summary["rows"]}
        self.assertEqual(set(row_by_code), {"VT-BASE01", "VT-BASE02", "VT-BASE03", "VT-BASE04"})
        self.assertEqual(row_by_code["VT-BASE02"]["zalo_status"], "🟡 Đã báo cáo trễ")
        self.assertIsNone(summary["snapshot_id"])

    async def test_missing_zalo_group_ended_row_displayed_but_excluded_from_bcnb_ticks(self) -> None:
        snap = _make_two_week_snapshot()
        await self.group_repo.apply_discovery_snapshot(snap)
        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {
                "execution_mode": "production",
                "check_only": False,
                "tick_lms": True,
                "refresh_discovery_if_stale": False,
                "target_date_iso": "2026-11-14",
            },
            mutates_external=True,
        )
        runtime = WorkerRuntime(evidence_root=self.evidence_root)
        gateways = ExternalGateways.production()

        row_pta = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-45-pta",
        )
        row_unmatched = _make_fake_bcnb_row(
            class_code="VT-UNMATCHED-01",
            lesson="5",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-45-unmatched",
        )
        tick_mock = AsyncMock(return_value="VERIFIED")

        async def _fake_prefetch(page, *, today_candidates, **kwargs):
            for c in today_candidates:
                code = str((c.get("scan_target") or {}).get("class_code") or "").upper()
                if code in {"VT-PTA01", "VT-UNMATCHED-01"}:
                    c["lms_start_time"] = "08:00"
                    c["lms_end_time"] = "10:00"
                    if isinstance(c.get("scan_target"), dict):
                        c["scan_target"]["lms_start_time"] = "08:00"
                        c["scan_target"]["lms_end_time"] = "10:00"
                if code == "VT-UNMATCHED-01":
                    c["lms_attendance"] = {"status": "VERIFIED", "unverified_count": 0, "attended_count": 4}
                    c["lms_comments"] = {"status": "COMPLETE", "submitted_count": 4}
                    c["lms_spck"] = {"status": "NOT_APPLICABLE", "submitted_count": 0, "missing_comment_count": 0}
                else:
                    c["lms_attendance"] = {"status": "VERIFIED", "unverified_count": 0, "attended_count": 4}
                    c["lms_comments"] = {"status": "COMPLETE", "submitted_count": 4}
                    c["lms_spck"] = {"status": "COMPLETE", "submitted_count": 4, "missing_comment_count": 0}
            return {"VT-PTA01": [{}], "VT-UNMATCHED-01": [{}]}

        async def _fake_scan_one(session, *, job_id, target, screenshots_dir, **kwargs):
            shot_file = Path(screenshots_dir) / f"{target['class_code']}.png"
            shot_bytes = b"\x89PNG\r\n\x1a\nfake"
            shot_file.write_bytes(shot_bytes)
            shot_sha = hashlib.sha256(shot_bytes).hexdigest()
            return {
                "group": target["group_name"],
                "person": target.get("person") or "GV A",
                "status": "✅ Đã báo cáo",
                "evidence_state": "SUBMITTED_VERIFIED",
                "submitted_for_bcnb": True,
                "delay_days": 0,
                "scan_target": dict(target),
                "evidence": {
                    "date_context": {
                        "target_week": target["week"],
                        "target_year": target["year"],
                        "target_class_code": target["class_code"],
                        "target_lesson": target["lesson"],
                        "target_class_date": target["class_date_iso"],
                    },
                    "classifier_result": {"matched_ui_sent_at": "14/11/2026"},
                    "images": {
                        "proof": {
                            "path": f"/api/evidence/{job_id}/{target['class_code']}.png",
                            "stored_path": str(shot_file),
                            "sha256": shot_sha,
                        }
                    },
                },
            }

        class _FakeSess:
            landing_facts_provider = None

        @asynccontextmanager
        async def _fake_session():
            yield _FakeSess()

        gateways.browser.persistent_session = _fake_session
        fake_page = MagicMock()
        fake_page.goto = AsyncMock()
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        with (
            patch("services.today_ops_service._create_production_backup", AsyncMock()),
            patch("services.today_ops_service._new_scan_page", AsyncMock(return_value=fake_page)),
            patch("services.today_ops_service._close_scan_page", AsyncMock()),
            patch("services.today_ops_service._setup_scan_page", AsyncMock(return_value={"decision": "SYNC_VERIFIED", "verified": True})),
            patch("services.today_ops_service._scan_one_target", AsyncMock(side_effect=_fake_scan_one)),
            patch("services.today_ops_service._login_bcnb_if_needed", AsyncMock()),
            patch("services.today_ops_service._prefetch_lms_for_today", AsyncMock(side_effect=_fake_prefetch)),
            patch("services.today_ops_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.zalo_bcnb_service.select_week_checked", AsyncMock()),
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(return_value=[row_pta, row_unmatched])),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", AsyncMock(return_value="NO_CHANGE")),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-PTA01": [{}], "VT-UNMATCHED-01": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.today_ops_service.Config.BCNB_URL", "https://bcnb.example.invalid"),
        ):
            await run_today_realtime_sync(
                job_id,
                {"target_date_iso": "2026-11-14", "check_only": False, "tick_lms": True},
                "production",
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                group_repo=self.group_repo,
                audit_repo=self.audit_repo,
                evidence_repo=self.evidence_repo,
                batch_repo=self.batch_repo,
                now_override=now_dt,
            )

        job = await self.job_repo.get_job(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "SUCCESS")

        ticked_classes = [(c.kwargs["class_code"], c.kwargs["action_type"]) for c in tick_mock.await_args_list]
        self.assertTrue(all(cls != "VT-UNMATCHED-01" for cls, _ in ticked_classes))
        self.assertIn(("VT-PTA01", "BCNB_LMS_TICK"), ticked_classes)

        parsed = json.loads(job["results"])
        self.assertNotIn("VT-UNMATCHED-01", {it["scan_target"]["class_code"] for it in parsed["items"]})
        self.assertIn("VT-UNMATCHED-01", {it["scan_target"]["class_code"] for it in parsed["today_items"]})

        fake_container = MagicMock()
        fake_container.settings.bcnb_dry_run = False
        fake_container.settings.zalo_plan_max_age_minutes = 30
        fake_container.group_repo = self.group_repo
        fake_container.job_repo = self.job_repo
        overview = await build_today_overview(
            fake_container,
            target_date_iso="2026-11-14",
            now=now_dt,
        )
        unmatched_item = next(it for it in overview["items"] if it["class_code"] == "VT-UNMATCHED-01")
        self.assertEqual(unmatched_item["phase"], "ENDED")
        self.assertTrue(unmatched_item["missing_zalo_group"])
        self.assertFalse(unmatched_item["eligible_for_sync"])

    async def test_update_bcnb_reports_rejects_stage_b_lms_tick_when_reload_loses_session_id(self) -> None:
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        page = MagicMock()
        audit_repo = AsyncMock()
        job_repo = AsyncMock()

        ready_res = {
            "group": "MindX - VT-PTA01 (08:00-10:00)",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "delay_days": 0,
            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5},
            "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
            "lms_spck": {"status": "COMPLETE", "submitted_count": 5, "missing_comment_count": 0},
            "scan_target": {
                "week": 45,
                "year": 2026,
                "class_code": "VT-PTA01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "bcnb_session_id": "sess-A",
            },
            "evidence": {
                "date_context": {
                    "target_week": 45,
                    "target_year": 2026,
                    "target_class_code": "VT-PTA01",
                    "target_lesson": "12",
                    "target_class_date": "2026-11-14",
                },
                "classifier_result": {"matched_ui_sent_at": "14/11/2026"},
                "images": {"proof": {"path": "/api/evidence/1/a.png", "sha256": "ABC"}},
            },
        }
        initial_row = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-A",
        )
        reloaded_missing_sid = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="",
        )
        w45_reads = 0

        async def _read_side_effect(*args, week=45, **kwargs):
            nonlocal w45_reads
            if int(week) != 45:
                return []
            w45_reads += 1
            return [initial_row] if w45_reads <= 2 else [reloaded_missing_sid]

        read_rows = AsyncMock(side_effect=_read_side_effect)
        tick_mock = AsyncMock(return_value="VERIFIED")

        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", read_rows),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", AsyncMock(return_value="NO_CHANGE")),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-PTA01": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=88,
                execution_mode="production",
                scan_results=[ready_res],
                today=today,
                now=now_dt,
                scan_plan={
                    "latest_week": {"week": 46, "year": 2026},
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                },
                job_repo=job_repo,
                audit_repo=audit_repo,
                tick_lms=True,
                check_only=False,
            )

        self.assertEqual([c.kwargs["action_type"] for c in tick_mock.await_args_list], ["BCNB_REPORT_TICK"])
        self.assertEqual(summary["lms_updated_count"], 0)
        self.assertEqual(len(summary["lms_failed_targets"]), 1)

    async def test_bcnb_summary_and_today_overview_sanitize_raw_exceptions(self) -> None:
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        page = MagicMock()
        audit_repo = AsyncMock()
        job_repo = AsyncMock()

        ready_res = {
            "group": "MindX - VT-PTA01 (08:00-10:00)",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "delay_days": 0,
            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5},
            "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
            "lms_spck": {"status": "COMPLETE", "submitted_count": 5, "missing_comment_count": 0},
            "scan_target": {
                "week": 45,
                "year": 2026,
                "class_code": "VT-PTA01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "bcnb_session_id": "sess-pta-12",
            },
            "evidence": {
                "date_context": {
                    "target_week": 45,
                    "target_year": 2026,
                    "target_class_code": "VT-PTA01",
                    "target_lesson": "12",
                    "target_class_date": "2026-11-14",
                },
                "classifier_result": {"matched_ui_sent_at": "14/11/2026"},
                "images": {"proof": {"path": "/api/evidence/1/a.png", "sha256": "ABC"}},
            },
        }
        row_pta = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-pta-12",
        )
        read_rows = AsyncMock(return_value=[row_pta])
        tick_mock = AsyncMock(side_effect=["VERIFIED", RuntimeError("SECRET_INTERNAL_RAW_ERR")])

        bcnb_logs: list[str] = []

        def _capture_bcnb_log(msg: str, *args, **kwargs):
            bcnb_logs.append(str(msg))

        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", read_rows),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", AsyncMock(return_value="NO_CHANGE")),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-PTA01": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.zalo_bcnb_service.log_message", side_effect=_capture_bcnb_log),
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=89,
                execution_mode="production",
                scan_results=[ready_res],
                today=today,
                now=now_dt,
                scan_plan={
                    "latest_week": {"week": 46, "year": 2026},
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                },
                job_repo=job_repo,
                audit_repo=audit_repo,
                tick_lms=True,
                check_only=False,
            )

        self.assertNotIn("SECRET_INTERNAL_RAW_ERR", json.dumps(summary, ensure_ascii=False))
        self.assertNotIn("SECRET_INTERNAL_RAW_ERR", "\n".join(bcnb_logs))
        self.assertEqual(summary["lms_failed_targets"][0]["error_code"], "BCNB_LMS_TICK_FAILED")

        await self.group_repo.apply_discovery_snapshot(_make_two_week_snapshot())
        persisted_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "production", "check_only": False},
            mutates_external=True,
        )
        await self.job_repo.update_job(persisted_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            persisted_id,
            status="PARTIAL_SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "target_date_iso": "2026-11-14",
                    "bcnb_summary": {
                        "lms_failed_targets": [
                            {"class_code": "VT-PTA01", "error": "REVIEW_SENTINEL_ONLY"}
                        ]
                    },
                    "items": [],
                }
            ),
        )
        fake_container = MagicMock()
        fake_container.settings.bcnb_dry_run = False
        fake_container.settings.zalo_plan_max_age_minutes = 30
        fake_container.group_repo = self.group_repo
        overview = await build_today_overview(
            fake_container,
            target_date_iso="2026-11-14",
            now=datetime(2026, 11, 14, 18, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertNotIn("REVIEW_SENTINEL_ONLY", json.dumps(overview, ensure_ascii=False))
        self.assertEqual(
            overview["latest_sync_job"]["bcnb_summary"]["lms_failed_targets"][0]["error_code"],
            "BCNB_LMS_TICK_FAILED",
        )

    async def test_lesson_14_with_valid_group_is_not_flagged_missing_and_ticks_bcnb(self) -> None:
        snap = _make_two_week_snapshot()
        snap["discovered_groups"] = [
            {"TenNhom": "MindX - VT-B14 (08:00-10:00)", "MaLop": "VT-B14", "CohortKey": "VT-B14|K1"}
        ]
        snap["source_group_names"] = ["MindX - VT-B14 (08:00-10:00)"]
        snap["expected_count"] = 1
        snap["bcnb_week_plan"]["required_data"] = [
            {
                "ma_lop": "VT-B14",
                "buoi_so": "14",
                "ngay_hoc": "14/11/2026",
                "ngay_hoc_iso": "2026-11-14",
                "giao_vien": "GV B14",
                "khoi": "Coding",
                "bcnb_session_id": "sess-45-b14",
            }
        ]
        snap["bcnb_week_plan"]["latest_data"] = []
        snap["scan_targets"] = []
        snap["exempt_targets"] = [
            {
                "group_name": "MindX - VT-B14 (08:00-10:00)",
                "person": "GV B14",
                "class_code": "VT-B14",
                "lesson": "14",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "week": 45,
                "year": 2026,
                "khoi": "Coding",
                "bcnb_session_id": "sess-45-b14",
                "missing_zalo_group": False,
            }
        ]
        snap["dropped_rows"] = {"missing_zalo": [], "counts": {"total": 0}}
        await self.group_repo.apply_discovery_snapshot(snap)

        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {
                "execution_mode": "production",
                "check_only": False,
                "tick_lms": True,
                "refresh_discovery_if_stale": False,
                "target_date_iso": "2026-11-14",
            },
            mutates_external=True,
        )
        runtime = WorkerRuntime(evidence_root=self.evidence_root)
        gateways = ExternalGateways.production()
        row_b14 = _make_fake_bcnb_row(
            class_code="VT-B14",
            lesson="14",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-45-b14",
        )
        tick_mock = AsyncMock(return_value="VERIFIED")

        async def _fake_prefetch(page, *, today_candidates, **kwargs):
            for c in today_candidates:
                c["lms_attendance"] = {"status": "VERIFIED", "unverified_count": 0, "attended_count": 4}
                c["lms_comments"] = {"status": "COMPLETE", "submitted_count": 4}
                c["lms_spck"] = {"status": "COMPLETE", "submitted_count": 4, "missing_comment_count": 0}
            return {"VT-B14": [{}]}

        class _FakeSess:
            landing_facts_provider = None

        @asynccontextmanager
        async def _fake_session():
            yield _FakeSess()

        gateways.browser.persistent_session = _fake_session
        fake_page = MagicMock()
        fake_page.goto = AsyncMock()
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        with (
            patch("services.today_ops_service._create_production_backup", AsyncMock()),
            patch("services.today_ops_service._new_scan_page", AsyncMock(return_value=fake_page)),
            patch("services.today_ops_service._close_scan_page", AsyncMock()),
            patch("services.today_ops_service._login_bcnb_if_needed", AsyncMock()),
            patch("services.today_ops_service._prefetch_lms_for_today", AsyncMock(side_effect=_fake_prefetch)),
            patch("services.today_ops_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.zalo_bcnb_service.select_week_checked", AsyncMock()),
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(return_value=[row_b14])),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", AsyncMock(return_value="NO_CHANGE")),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-B14": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.today_ops_service.Config.BCNB_URL", "https://bcnb.example.invalid"),
        ):
            await run_today_realtime_sync(
                job_id,
                {"target_date_iso": "2026-11-14", "check_only": False, "tick_lms": True},
                "production",
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                group_repo=self.group_repo,
                audit_repo=self.audit_repo,
                evidence_repo=self.evidence_repo,
                batch_repo=self.batch_repo,
                now_override=now_dt,
            )

        job = await self.job_repo.get_job(job_id)
        self.assertEqual(job["status"], "SUCCESS")
        parsed = json.loads(job["results"])
        self.assertEqual(len(parsed["items"]), 1)
        self.assertIs(parsed["items"][0].get("missing_zalo_group"), False)
        ticked = [(c.kwargs["class_code"], c.kwargs["action_type"]) for c in tick_mock.await_args_list]
        self.assertIn(("VT-B14", "BCNB_REPORT_TICK"), ticked)
        self.assertIn(("VT-B14", "BCNB_LMS_TICK"), ticked)

    async def test_build_today_overview_rejects_stale_or_missing_session_id_overlay(self) -> None:
        snap = _make_two_week_snapshot()
        snap["bcnb_week_plan"]["required_data"][0]["bcnb_session_id"] = "NEW-SESSION"
        snap["bcnb_week_plan"]["required_data"][0]["nx_zalo_checked"] = False
        snap["bcnb_week_plan"]["required_data"][0]["nx_lms_checked"] = False
        snap["scan_targets"][0]["bcnb_session_id"] = "NEW-SESSION"
        snap["scan_targets"][0]["nx_zalo_checked"] = False
        snap["scan_targets"][0]["nx_lms_checked"] = False
        # Both base row and overlay missing bcnb_session_id must fail closed
        snap["bcnb_week_plan"]["latest_data"][3]["bcnb_session_id"] = ""
        snap["deferred_targets"][2]["bcnb_session_id"] = ""
        await self.group_repo.apply_discovery_snapshot(snap)

        old_job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "dry-run", "check_only": True, "target_date_iso": "2026-11-14"},
        )
        await self.job_repo.update_job(old_job_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            old_job_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "check_only": True,
                    "target_date_iso": "2026-11-14",
                    "today_items": [
                        {
                            "group": "MindX - VT-PTA01 (08:00-10:00)",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "bcnb_session_id": "OLD-SESSION",
                            "scan_target": {
                                "week": 45,
                                "year": 2026,
                                "class_code": "VT-PTA01",
                                "lesson": "12",
                                "class_date_iso": "2026-11-14",
                                "bcnb_session_id": "OLD-SESSION",
                            },
                        },
                        {
                            "group": "MindX - VT-PTB01 (10:00-12:00)",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "scan_target": {
                                "week": 46,
                                "year": 2026,
                                "class_code": "VT-PTB01",
                                "lesson": "13",
                                "class_date_iso": "2026-11-14",
                            },
                        },
                        {
                            "group": "MindX - VT-CS02",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "bcnb_session_id": "",
                            "scan_target": {
                                "week": 46,
                                "year": 2026,
                                "class_code": "VT-CS02",
                                "lesson": "04",
                                "class_date_iso": "2026-11-14",
                                "bcnb_session_id": "",
                            },
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        )

        fake_container = MagicMock()
        fake_container.settings.bcnb_dry_run = True
        fake_container.settings.zalo_plan_max_age_minutes = 30
        fake_container.group_repo = self.group_repo
        overview = await build_today_overview(
            fake_container,
            target_date_iso="2026-11-14",
            now=datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        by_code = {it["class_code"]: it for it in overview["items"]}
        self.assertEqual(by_code["VT-PTA01"]["bcnb_session_id"], "NEW-SESSION")
        self.assertIs(by_code["VT-PTA01"]["nx_zalo_checked"], False)
        self.assertIs(by_code["VT-PTA01"]["nx_lms_checked"], False)
        self.assertEqual(by_code["VT-PTA01"]["zalo_category"], "not_scanned")

        self.assertEqual(by_code["VT-PTB01"]["bcnb_session_id"], "sess-46-ptb")
        self.assertEqual(by_code["VT-PTB01"]["zalo_category"], "not_scanned")
        self.assertIsNone(by_code["VT-PTB01"]["nx_lms_checked"])
        self.assertTrue(by_code["VT-PTB01"]["spck_exempt_course"])
        self.assertEqual(by_code["VT-PTB01"]["lms_spck"]["summary_text"], "⚪ Miễn SPCK (PTB/CSB)")

        self.assertEqual(by_code["VT-CS02"]["bcnb_session_id"], "")
        self.assertEqual(by_code["VT-CS02"]["zalo_category"], "deferred")
        self.assertNotEqual(by_code["VT-CS02"].get("evidence_state"), "SUBMITTED_VERIFIED")
        self.assertIsNot(by_code["VT-CS02"]["nx_zalo_checked"], True)
        self.assertIsNot(by_code["VT-CS02"]["nx_lms_checked"], True)

    async def test_run_today_realtime_sync_lesson_08_matches_and_ticks_bcnb(self) -> None:
        snap = _make_two_week_snapshot()
        snap["discovered_groups"] = [
            {"TenNhom": "MindX - VT-JS01 (08:00-10:00)", "MaLop": "VT-JS01", "CohortKey": "VT-JS01|K1"}
        ]
        snap["source_group_names"] = ["MindX - VT-JS01 (08:00-10:00)"]
        snap["expected_count"] = 1
        snap["bcnb_week_plan"]["required_data"] = []
        snap["bcnb_week_plan"]["latest_data"] = [
            {
                "ma_lop": "VT-JS01",
                "buoi_so": "08",
                "ngay_hoc": "14/11/2026",
                "ngay_hoc_iso": "2026-11-14",
                "giao_vien": "GV JS01",
                "khoi": "Coding",
                "bcnb_session_id": "sess-46-js08",
            }
        ]
        snap["scan_targets"] = [
            {
                "group_name": "MindX - VT-JS01 (08:00-10:00)",
                "person": "GV JS01",
                "class_code": "VT-JS01",
                "lesson": "08",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "week": 46,
                "year": 2026,
                "khoi": "Coding",
                "bcnb_session_id": "sess-46-js08",
            }
        ]
        snap["deferred_targets"] = []
        snap["exempt_targets"] = []
        snap["dropped_rows"] = {"missing_zalo": [], "counts": {"total": 0}}
        await self.group_repo.apply_discovery_snapshot(snap)

        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {
                "execution_mode": "production",
                "check_only": False,
                "tick_lms": True,
                "refresh_discovery_if_stale": False,
                "selected_target_keys": ["2026-W46|VT-JS01|8|2026-11-14"],
                "target_date_iso": "2026-11-14",
            },
            mutates_external=True,
        )
        runtime = WorkerRuntime(evidence_root=self.evidence_root)
        gateways = ExternalGateways.production()

        shot_bytes = b"\x89PNG\r\n\x1a\njs08fake"
        shot_sha = hashlib.sha256(shot_bytes).hexdigest()

        async def _fake_scan_one(session, *, job_id, target, screenshots_dir, **kwargs):
            shot_file = Path(screenshots_dir) / f"js08-{job_id}.png"
            shot_file.parent.mkdir(parents=True, exist_ok=True)
            shot_file.write_bytes(shot_bytes)
            return {
                "group": target["group_name"],
                "person": target["person"],
                "status": "✅ Đã báo cáo",
                "evidence_state": "SUBMITTED_VERIFIED",
                "submitted_for_bcnb": True,
                "delay_days": 0,
                "scan_target": dict(target),
                "evidence": {
                    "images": {
                        "proof": {
                            "path": f"/api/evidence/{job_id}/js08-{job_id}.png",
                            "stored_path": str(shot_file),
                            "sha256": shot_sha,
                        }
                    },
                    "date_context": {
                        "target_week": 46,
                        "target_year": 2026,
                        "target_class_code": "VT-JS01",
                        "target_lesson": "08",
                        "target_class_date": "2026-11-14",
                    },
                    "classifier_result": {"matched_ui_sent_at": "14/11/2026"},
                },
            }

        async def _fake_prefetch(page, *, today_candidates, **kwargs):
            for c in today_candidates:
                c["lms_attendance"] = {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5}
                c["lms_comments"] = {"status": "COMPLETE", "submitted_count": 5}
            return {"VT-JS01": [{}]}

        row_js08 = _make_fake_bcnb_row(
            class_code="VT-JS01",
            lesson="08",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-46-js08",
        )
        tick_mock = AsyncMock(return_value="VERIFIED")

        class _FakeSess:
            landing_facts_provider = None

        @asynccontextmanager
        async def _fake_session():
            yield _FakeSess()

        gateways.browser.persistent_session = _fake_session
        fake_page = MagicMock()
        fake_page.goto = AsyncMock()
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        with (
            patch("services.today_ops_service._create_production_backup", AsyncMock()),
            patch("services.today_ops_service._new_scan_page", AsyncMock(return_value=fake_page)),
            patch("services.today_ops_service._close_scan_page", AsyncMock()),
            patch("services.today_ops_service._setup_scan_page", AsyncMock(return_value={"decision": "SYNC_VERIFIED", "verified": True})),
            patch("services.today_ops_service._scan_one_target", AsyncMock(side_effect=_fake_scan_one)),
            patch("services.today_ops_service._login_bcnb_if_needed", AsyncMock()),
            patch("services.today_ops_service._prefetch_lms_for_today", AsyncMock(side_effect=_fake_prefetch)),
            patch("services.today_ops_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.zalo_bcnb_service.select_week_checked", AsyncMock()),
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(return_value=[row_js08])),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", AsyncMock(return_value="NO_CHANGE")),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-JS01": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.today_ops_service.Config.BCNB_URL", "https://bcnb.example.invalid"),
        ):
            await run_today_realtime_sync(
                job_id,
                {
                    "target_date_iso": "2026-11-14",
                    "check_only": False,
                    "tick_lms": True,
                    "selected_target_keys": ["2026-W46|VT-JS01|8|2026-11-14"],
                },
                "production",
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                group_repo=self.group_repo,
                audit_repo=self.audit_repo,
                evidence_repo=self.evidence_repo,
                batch_repo=self.batch_repo,
                now_override=now_dt,
            )

        job = await self.job_repo.get_job(job_id)
        self.assertEqual(job["status"], "SUCCESS")
        ticked = [(c.kwargs["class_code"], c.kwargs["action_type"]) for c in tick_mock.await_args_list]
        self.assertIn(("VT-JS01", "BCNB_REPORT_TICK"), ticked)
        self.assertIn(("VT-JS01", "BCNB_LMS_TICK"), ticked)

    async def test_weekly_summary_rejects_cross_snapshot_result_missing_session_id(self) -> None:
        snap1 = _make_two_week_snapshot()
        snap1["bcnb_week_plan"]["required_data"] = [
            {
                "ma_lop": "VT-PTA01",
                "buoi_so": "12",
                "ngay_hoc": "14/11/2026",
                "ngay_hoc_iso": "2026-11-14",
                "giao_vien": "GV A",
                "khoi": "Coding",
                "bcnb_session_id": "",
            }
        ]
        snap1["scan_targets"] = [
            {
                "group_name": "MindX - VT-PTA01 (08:00-10:00)",
                "person": "GV A",
                "class_code": "VT-PTA01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "week": 45,
                "year": 2026,
                "khoi": "Coding",
            }
        ]
        snap1_applied = await self.group_repo.apply_discovery_snapshot(snap1)
        old_scan_id = await self.job_repo.create_job(
            "ZALO_SCAN",
            {"execution_mode": "production", "snapshot_id": snap1_applied["snapshot_id"]},
            mutates_external=True,
        )
        await self.job_repo.update_job(old_scan_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            old_scan_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "zalo_scan",
                    "snapshot_id": snap1_applied["snapshot_id"],
                    "required_week": {"week": 45, "year": 2026},
                    "latest_week": {"week": 46, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                    "items": [
                        {
                            "group": "MindX - VT-PTA01 (08:00-10:00)",
                            "person": "GV A",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "submitted_for_bcnb": True,
                            "scan_target": {
                                "week": 45,
                                "year": 2026,
                                "class_code": "VT-PTA01",
                                "lesson": "12",
                                "class_date_iso": "2026-11-14",
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

        # Snap 2 introduces single unique row for VT-PTA01 with NEW-SESSION, plus VT-OTHER
        snap2 = _make_two_week_snapshot()
        snap2["bcnb_week_plan"]["required_data"] = [
            {
                "ma_lop": "VT-PTA01",
                "buoi_so": "12",
                "ngay_hoc": "14/11/2026",
                "ngay_hoc_iso": "2026-11-14",
                "giao_vien": "GV A",
                "khoi": "Coding",
                "bcnb_session_id": "NEW-SESSION",
                "nx_zalo_checked": False,
            },
            {
                "ma_lop": "VT-OTHER01",
                "buoi_so": "12",
                "ngay_hoc": "14/11/2026",
                "ngay_hoc_iso": "2026-11-14",
                "giao_vien": "GV O",
                "khoi": "Coding",
                "bcnb_session_id": "S-OTHER",
            },
        ]
        snap2["scan_targets"] = [
            {
                "group_name": "MindX - VT-PTA01 (08:00-10:00)",
                "person": "GV A",
                "class_code": "VT-PTA01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "week": 45,
                "year": 2026,
                "khoi": "Coding",
                "bcnb_session_id": "NEW-SESSION",
            }
        ]
        snap2_applied = await self.group_repo.apply_discovery_snapshot(snap2)

        sync_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "production", "check_only": False, "target_date_iso": "2026-11-14"},
            mutates_external=True,
        )
        await self.job_repo.update_job(sync_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            sync_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "selective_rescan": True,
                    "check_only": False,
                    "snapshot_id": snap2_applied["snapshot_id"],
                    "required_week": {"week": 45, "year": 2026},
                    "latest_week": {"week": 46, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                    "items": [
                        {
                            "group": "MindX - VT-OTHER01",
                            "person": "GV O",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "bcnb_session_id": "S-OTHER",
                            "scan_target": {
                                "week": 45,
                                "year": 2026,
                                "class_code": "VT-OTHER01",
                                "lesson": "12",
                                "class_date_iso": "2026-11-14",
                                "bcnb_session_id": "S-OTHER",
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

        summary = await ReportRepository().get_weekly_summary(week=45, year=2026)
        by_code = {r["class_code"]: r for r in summary["rows"]}
        self.assertEqual(by_code["VT-PTA01"]["bcnb_session_id"], "NEW-SESSION")
        self.assertNotEqual(by_code["VT-PTA01"].get("zalo_category"), "DONE")
        self.assertNotIn("✅", str(by_code["VT-PTA01"].get("zalo_status") or ""))

    async def test_run_today_realtime_sync_failure_redacts_exception_pii_from_logs_and_job(self) -> None:
        await self.group_repo.apply_discovery_snapshot(_make_two_week_snapshot())
        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"target_date_iso": "2026-11-14", "check_only": True},
        )
        runtime = WorkerRuntime(evidence_root=self.evidence_root)
        gateways = ExternalGateways(
            browser=MagicMock(),
            lms_ocr=MagicMock(),
            student_ocr_factory=MagicMock,
        )
        logged_messages: list[str] = []

        def _capture_log(msg: str, *args, **kwargs):
            logged_messages.append(str(msg))

        with (
            patch(
                "services.today_ops_service._collect_today_entries_from_snapshot",
                side_effect=RuntimeError("boom teacher.secret@mindx.example phone=0901234567"),
            ),
            patch("services.today_ops_service.log_message", side_effect=_capture_log),
        ):
            await run_today_realtime_sync(
                job_id,
                {"target_date_iso": "2026-11-14", "check_only": True},
                "dry-run",
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                group_repo=self.group_repo,
                audit_repo=self.audit_repo,
                evidence_repo=self.evidence_repo,
                batch_repo=self.batch_repo,
            )

        job = await self.job_repo.get_job(job_id)
        self.assertEqual(job["status"], "FAILED")
        self.assertIn("RuntimeError", job["message"])
        self.assertNotIn("teacher.secret@mindx.example", job["message"])
        self.assertNotIn("0901234567", job["message"])
        joined_logs = "\n".join(logged_messages)
        self.assertIn("RuntimeError", joined_logs)
        self.assertNotIn("teacher.secret@mindx.example", joined_logs)
        self.assertNotIn("0901234567", joined_logs)

    async def test_stage_a_report_tick_rejects_session_id_or_date_mismatch_on_reload(self) -> None:
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "production", "check_only": False, "target_date_iso": "2026-11-14"},
            mutates_external=True,
        )
        shot_bytes = b"\x89PNG\r\n\x1a\nstagea_proof"
        shot_sha = hashlib.sha256(shot_bytes).hexdigest().upper()
        shot_file = Path(self.evidence_root) / f"job-{job_id}" / "stagea.png"
        shot_file.parent.mkdir(parents=True, exist_ok=True)
        shot_file.write_bytes(shot_bytes)
        public_path = f"/api/evidence/{job_id}/stagea.png"
        await self.evidence_repo.register_artifact(
            job_id=job_id,
            public_path=public_path,
            stored_path=str(shot_file),
            capture_type="proof",
            expected_sha256=shot_sha,
        )

        ready_res = {
            "group": "MindX - VT-PTA01 (08:00-10:00)",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "delay_days": 0,
            "scan_target": {
                "week": 45,
                "year": 2026,
                "class_code": "VT-PTA01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "bcnb_session_id": "sess-A",
            },
            "evidence": {
                "date_context": {
                    "target_week": 45,
                    "target_year": 2026,
                    "target_class_code": "VT-PTA01",
                    "target_lesson": "12",
                    "target_class_date": "2026-11-14",
                },
                "classifier_result": {"matched_ui_sent_at": "14/11/2026"},
                "images": {"proof": {"path": public_path, "sha256": shot_sha}},
            },
        }
        initial_row = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-A",
        )

        class _FakeCell:
            def __init__(self, text: str = "", checkbox=None) -> None:
                self._text = text
                self._checkbox = checkbox

            async def inner_text(self) -> str:
                return self._text

            def locator(self, selector: str):
                cb = self._checkbox

                class _BoxLoc:
                    @property
                    def first(self):
                        return cb

                return _BoxLoc()

        class _FakeReloadCheckbox:
            async def count(self) -> int:
                return 1

            async def is_checked(self) -> bool:
                return True

        class _FakeRow:
            def __init__(self, cells: list[_FakeCell], sid: str, is_header: bool = False) -> None:
                self._cells = cells
                self._sid = sid
                self._is_header = is_header

            async def get_attribute(self, name: str) -> str:
                return self._sid if name == "data-session-id" else ""

            def locator(self, selector: str):
                row_ref = self

                class _CellLoc:
                    async def all(self):
                        if row_ref._is_header:
                            return row_ref._cells if selector == "th, td" else []
                        return [] if selector == "th, td" else row_ref._cells

                return _CellLoc()

        class _EmptyLoc:
            @property
            def first(self):
                return self

            async def count(self) -> int:
                return 0

            async def all(self):
                return []

        class _FakeReloadPage:
            async def reload(self, **kwargs) -> None:
                return None

            def locator(self, selector: str):
                if selector != "table tr":
                    return _EmptyLoc()
                header = _FakeRow(
                    [_FakeCell(h) for h in ("Mã lớp", "Buổi", "Ngày học", "NX Zalo", "NX LMS")],
                    sid="",
                    is_header=True,
                )
                # Same class_code & lesson, same date, checked=True, BUT wrong session_id after reload!
                reloaded_wrong_sid = _FakeRow(
                    [
                        _FakeCell("VT-PTA01"),
                        _FakeCell("12"),
                        _FakeCell("14/11/2026"),
                        _FakeCell("", checkbox=_FakeReloadCheckbox()),
                        _FakeCell(""),
                    ],
                    sid="sess-OTHER",
                )

                class _TableLoc:
                    @property
                    def first(self):
                        return header

                    async def all(self):
                        return [header, reloaded_wrong_sid]

                return _TableLoc()

        fake_page = _FakeReloadPage()
        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(return_value=[initial_row])),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(0, 1, 3))),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", AsyncMock(return_value="NO_CHANGE")),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-PTA01": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.bcnb_safety.select_week_checked", AsyncMock(return_value=(45, 2026))),
            patch("services.bcnb_safety.resolve_report_columns", AsyncMock(return_value=(0, 1, 3))),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
        ):
            summary = await update_bcnb_reports(
                fake_page,
                job_id=job_id,
                execution_mode="production",
                scan_results=[ready_res],
                today=today,
                now=now_dt,
                scan_plan={
                    "latest_week": {"week": 46, "year": 2026},
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                },
                job_repo=self.job_repo,
                audit_repo=self.audit_repo,
                tick_lms=False,
                check_only=False,
            )

        self.assertEqual(summary["updated_count"], 0)
        self.assertEqual(len(summary["failed_targets"]), 1)
        self.assertEqual(summary["failed_targets"][0]["error_code"], "BCNB_REPORT_OUTCOME_UNKNOWN")
        self.assertIsNot(ready_res.get("nx_zalo_checked"), True)
        audits = await self.audit_repo.list_for_job(job_id)
        self.assertEqual(audits[0]["outcome"], "UNKNOWN")

    async def test_real_bcnb_actions_redact_exception_pii_from_logs_and_audit(self) -> None:
        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"execution_mode": "production", "check_only": False, "target_date_iso": "2026-11-14"},
            mutates_external=True,
        )
        shot_bytes = b"\x89PNG\r\n\x1a\npii_proof"
        shot_sha = hashlib.sha256(shot_bytes).hexdigest().upper()
        shot_file = Path(self.evidence_root) / f"job-{job_id}" / "pii.png"
        shot_file.parent.mkdir(parents=True, exist_ok=True)
        shot_file.write_bytes(shot_bytes)
        public_path = f"/api/evidence/{job_id}/pii.png"
        await self.evidence_repo.register_artifact(
            job_id=job_id,
            public_path=public_path,
            stored_path=str(shot_file),
            capture_type="proof",
            expected_sha256=shot_sha,
        )

        ready_res = {
            "group": "MindX - VT-PTA01 (08:00-10:00)",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "delay_days": 0,
            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5},
            "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
            "lms_spck": {"status": "COMPLETE", "submitted_count": 5, "missing_comment_count": 0},
            "zalo_content_issues": ["[Zalo] Thiếu ảnh buổi học"],
            "scan_target": {
                "week": 45,
                "year": 2026,
                "class_code": "VT-PTA01",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "bcnb_session_id": "sess-pii-12",
            },
            "evidence": {
                "date_context": {
                    "target_week": 45,
                    "target_year": 2026,
                    "target_class_code": "VT-PTA01",
                    "target_lesson": "12",
                    "target_class_date": "2026-11-14",
                },
                "classifier_result": {"matched_ui_sent_at": "14/11/2026"},
                "images": {"proof": {"path": public_path, "sha256": shot_sha}},
            },
        }
        failing_row = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-pii-12",
        )
        # Configure Stage A NX Zalo checkbox click to fail with PII exception text before write completes
        failing_row["cells"][3].locator('input[type="checkbox"]').first.check = AsyncMock(
            side_effect=RuntimeError("PII teacher.zalo@mindx.example phone=0912345678")
        )
        # Stage B NX LMS checkbox fails with PII exception text too
        failing_row["cells"][5].locator('input[type="checkbox"]').first.check = AsyncMock(
            side_effect=RuntimeError("PII teacher.lms@mindx.example phone=0987654321")
        )

        # Also test notes_only=True so real execute_verified_checker_note_action runs and fails with PII
        note_row = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-pii-12",
        )
        note_page = MagicMock()
        note_page.locator = MagicMock(return_value=MagicMock())

        captured_logs: list[str] = []

        def _capture_any_log(msg: str, *args, **kwargs) -> None:
            captured_logs.append(str(msg))

        dummy_page = MagicMock()
        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(return_value=[failing_row])),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(0, 1, 3))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 5))),
            patch("services.bcnb_actions.verify_checkbox_persisted", AsyncMock(side_effect=RuntimeError("PII verify.secret@mindx.example 0900001111"))),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-PTA01": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
            patch("services.bcnb_actions.log_message", side_effect=_capture_any_log),
            patch("services.zalo_bcnb_service.log_message", side_effect=_capture_any_log),
        ):
            summary_ticks = await update_bcnb_reports(
                dummy_page,
                job_id=job_id,
                execution_mode="production",
                scan_results=[ready_res],
                today=today,
                now=now_dt,
                scan_plan={
                    "latest_week": {"week": 46, "year": 2026},
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                },
                job_repo=self.job_repo,
                audit_repo=self.audit_repo,
                tick_lms=True,
                check_only=False,
            )

        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(return_value=[note_row])),
            patch("services.bcnb_actions.read_checker_note_by_session", AsyncMock(return_value=(True, ""))),
            patch("services.bcnb_actions._post_checker_note_update", AsyncMock(side_effect=RuntimeError("PII teacher.note@mindx.example phone=0977778888"))),
            patch("services.bcnb_actions.verify_checker_note_persisted", AsyncMock(side_effect=RuntimeError("PII note.verify@mindx.example 0966665555"))),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-PTA01": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
            patch("services.bcnb_actions.log_message", side_effect=_capture_any_log),
            patch("services.zalo_bcnb_service.log_message", side_effect=_capture_any_log),
        ):
            summary_notes = await update_bcnb_reports(
                note_page,
                job_id=job_id,
                execution_mode="production",
                scan_results=[ready_res],
                today=today,
                now=now_dt,
                scan_plan={
                    "latest_week": {"week": 46, "year": 2026},
                    "required_week": {"week": 45, "year": 2026},
                    "selected_weeks": [{"week": 45, "year": 2026}, {"week": 46, "year": 2026}],
                },
                job_repo=self.job_repo,
                audit_repo=self.audit_repo,
                tick_lms=False,
                check_only=False,
                notes_only=True,
            )

        audits = await self.audit_repo.list_for_job(job_id)
        joined_all = "\n".join(
            [
                "\n".join(captured_logs),
                json.dumps(summary_ticks, ensure_ascii=False),
                json.dumps(summary_notes, ensure_ascii=False),
                json.dumps(audits, ensure_ascii=False),
            ]
        )
        for pii in (
            "teacher.zalo@mindx.example",
            "0912345678",
            "teacher.lms@mindx.example",
            "0987654321",
            "verify.secret@mindx.example",
            "0900001111",
            "teacher.note@mindx.example",
            "0977778888",
            "note.verify@mindx.example",
            "0966665555",
        ):
            self.assertNotIn(pii, joined_all)
        self.assertGreaterEqual(len(audits), 3)

    async def test_run_today_realtime_sync_navigates_zalo_before_landing_facts_provider(
        self,
    ) -> None:
        await self.group_repo.apply_discovery_snapshot(_make_two_week_snapshot())
        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"target_date_iso": "2026-11-14", "check_only": True, "tick_lms": False},
        )
        runtime = WorkerRuntime(evidence_root=self.evidence_root)
        gateways = ExternalGateways.production()

        class _FakeZaloPage:
            def __init__(self) -> None:
                self.url = "about:blank"
                self.closed = False
                self.goto_urls: list[str] = []

            async def add_init_script(self, _script: str) -> None:
                return None

            async def goto(self, url: str, **_kwargs) -> None:
                self.url = url
                self.goto_urls.append(url)

            async def wait_for_selector(self, _selector: str, **_kwargs) -> None:
                return None

            def is_closed(self) -> bool:
                return self.closed

            async def close(self) -> None:
                self.closed = True

        fake_page = _FakeZaloPage()
        observed_urls: list[str] = []

        class _FakeSess:
            async def landing_facts_provider(self, page):
                observed_urls.append(page.url)
                if not page.url.startswith("https://chat.zalo.me"):
                    raise TimeoutError("polled about:blank before goto")
                return {
                    "page_closed": False,
                    "blocking_modal": False,
                    "unsupported_browser": False,
                    "network_error": False,
                    "readiness_timeout": False,
                    "selector_detached": False,
                    "authenticated_shell_visible": True,
                    "search_control_visible": True,
                    "login_or_qr_visible": False,
                }

        @asynccontextmanager
        async def _fake_session():
            yield _FakeSess()

        gateways.browser.persistent_session = _fake_session
        scanned_codes: list[str] = []

        async def _fake_prefetch(_page, *, today_candidates, **_kwargs):
            for c in today_candidates:
                c["lms_attendance"] = {"status": "VERIFIED", "unverified_count": 0}
                c["lms_comments"] = {"status": "COMPLETE", "submitted_count": 3}
                c["lms_spck"] = {"status": "COMPLETE", "submitted_count": 3}
            return {}

        async def _fake_scan_one(_page, *, job_id: int, target: dict, screenshots_dir: str, **_kw):
            scanned_codes.append(target["class_code"])
            shot_file = Path(screenshots_dir) / f"{target['class_code']}.png"
            shot_file.parent.mkdir(parents=True, exist_ok=True)
            shot_file.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            return {
                "group": target["group_name"],
                "class_code": target["class_code"],
                "session_id": target.get("session_id"),
                "date_iso": target.get("date_iso"),
                "person": target.get("person") or "GV A",
                "decision": "VERIFIED",
                "evidence_state": "SUBMITTED_VERIFIED",
                "reason": "ok",
                "screenshot_path": str(shot_file),
                "screenshot_sha256": hashlib.sha256(b"\x89PNG\r\n\x1a\nfake").hexdigest(),
                "image_match_method": "EXACT",
            }

        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        with (
            patch("services.today_ops_service._new_scan_page", AsyncMock(return_value=fake_page)),
            patch("services.today_ops_service._close_scan_page", AsyncMock()),
            patch("services.today_ops_service._prefetch_lms_for_today", AsyncMock(side_effect=_fake_prefetch)),
            patch("services.today_ops_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.today_ops_service._scan_one_target", AsyncMock(side_effect=_fake_scan_one)),
            patch("services.today_ops_service._login_bcnb_if_needed", AsyncMock()),
            patch(
                "services.today_ops_service.update_bcnb_reports",
                AsyncMock(
                    return_value={
                        "mode": "check_only",
                        "updated_count": 0,
                        "lms_updated_count": 0,
                        "failed_targets": [],
                        "lms_failed_targets": [],
                    }
                ),
            ),
            patch(
                "services.zalo_parallel_service.ensure_zalo_history_synced",
                AsyncMock(return_value={"decision": "SYNC_VERIFIED", "verified": True}),
            ),
        ):
            await run_today_realtime_sync(
                job_id,
                {"target_date_iso": "2026-11-14", "check_only": True, "tick_lms": False},
                "dry-run",
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                group_repo=self.group_repo,
                audit_repo=self.audit_repo,
                evidence_repo=self.evidence_repo,
                batch_repo=self.batch_repo,
                now_override=now_dt,
            )

        job = await self.job_repo.get_job(job_id)
        self.assertEqual(job["status"], "SUCCESS")
        self.assertEqual(observed_urls, ["https://chat.zalo.me/"])
        self.assertGreaterEqual(len(fake_page.goto_urls), 1)
        self.assertEqual(fake_page.goto_urls[0], "https://chat.zalo.me/")
        self.assertEqual(scanned_codes, ["VT-PTA01", "VT-PTB01"])

    async def test_run_today_realtime_sync_isolates_zalo_setup_login_qr_and_preserves_lms_spck(
        self,
    ) -> None:
        await self.group_repo.apply_discovery_snapshot(_make_two_week_snapshot())
        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"target_date_iso": "2026-11-14", "check_only": True, "tick_lms": False},
        )
        runtime = WorkerRuntime(evidence_root=self.evidence_root)
        gateways = ExternalGateways.production()

        class _FakePage:
            def __init__(self) -> None:
                self.url = "about:blank"
                self.closed = False

            async def add_init_script(self, _script: str) -> None:
                return None

            async def goto(self, url: str, **_kwargs) -> None:
                self.url = url

            async def wait_for_selector(self, _selector: str, **_kwargs) -> None:
                return None

            async def close(self) -> None:
                self.closed = True

        provider_calls = 0

        class _QrSession:
            async def landing_facts_provider(self, _page):
                nonlocal provider_calls
                provider_calls += 1
                return {
                    "page_closed": False,
                    "blocking_modal": False,
                    "unsupported_browser": False,
                    "network_error": False,
                    "readiness_timeout": False,
                    "selector_detached": False,
                    "authenticated_shell_visible": False,
                    "search_control_visible": False,
                    "login_or_qr_visible": True,
                }

        @asynccontextmanager
        async def _fake_session():
            yield _QrSession()

        gateways.browser.persistent_session = _fake_session
        scan_mock = AsyncMock(side_effect=AssertionError("must not scan when setup returns LOGIN_OR_QR"))

        async def _fake_prefetch(_page, *, today_candidates, **_kwargs):
            for c in today_candidates:
                c["lms_attendance"] = {"status": "VERIFIED", "unverified_count": 0}
                c["lms_comments"] = {"status": "COMPLETE", "submitted_count": 4}
                c["lms_spck"] = {"status": "COMPLETE", "submitted_count": 4}
            return {}

        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        with (
            patch("services.today_ops_service._new_scan_page", AsyncMock(side_effect=lambda _s: _FakePage())),
            patch("services.today_ops_service._close_scan_page", AsyncMock()),
            patch("services.today_ops_service._prefetch_lms_for_today", AsyncMock(side_effect=_fake_prefetch)),
            patch("services.today_ops_service.inspect_and_attach_quality_issues_async", AsyncMock()),
            patch("services.today_ops_service._scan_one_target", scan_mock),
            patch("services.today_ops_service._login_bcnb_if_needed", AsyncMock()),
            patch(
                "services.today_ops_service.update_bcnb_reports",
                AsyncMock(
                    return_value={
                        "mode": "check_only",
                        "updated_count": 0,
                        "lms_updated_count": 0,
                        "failed_targets": [],
                        "lms_failed_targets": [],
                    }
                ),
            ),
        ):
            await run_today_realtime_sync(
                job_id,
                {"target_date_iso": "2026-11-14", "check_only": True, "tick_lms": False},
                "dry-run",
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                group_repo=self.group_repo,
                audit_repo=self.audit_repo,
                evidence_repo=self.evidence_repo,
                batch_repo=self.batch_repo,
                now_override=now_dt,
            )

        job = await self.job_repo.get_job(job_id)
        self.assertEqual(job["status"], "PARTIAL_SUCCESS")
        self.assertEqual(provider_calls, 1)
        self.assertEqual(scan_mock.await_count, 0)
        self.assertIn("Lưu ý Zalo:", job["message"])
        envelope = json.loads(job["results"])
        self.assertIn("đăng nhập/QR", envelope["zalo_setup_notice"])
        self.assertEqual(len(envelope["items"]), 2)
        for item in envelope["items"]:
            self.assertEqual(item["evidence_state"], "INSUFFICIENT_EVIDENCE")
            self.assertEqual(
                item["evidence"]["classifier_result"]["decision"],
                "INSUFFICIENT_EVIDENCE",
            )
            self.assertEqual(item["error_code"], "LOGIN_OR_QR")
        pta_item = next(
            x
            for x in envelope["today_items"]
            if (x.get("class_code") or (x.get("scan_target") or {}).get("class_code"))
            == "VT-PTA01"
        )
        self.assertEqual(pta_item["lms_spck"]["status"], "COMPLETE")
        fake_container = MagicMock()
        fake_container.settings.bcnb_dry_run = True
        fake_container.settings.zalo_plan_max_age_minutes = 30
        fake_container.group_repo = self.group_repo
        fake_container.job_repo = self.job_repo
        overview = await build_today_overview(
            fake_container,
            target_date_iso="2026-11-14",
            now=now_dt,
        )
        overview_pta = next(x for x in overview["items"] if x["class_code"] == "VT-PTA01")
        self.assertEqual(overview_pta["lms_spck"]["status"], "COMPLETE")

    async def test_scan_ended_zalo_targets_retries_transient_setup_and_target_search_error(
        self,
    ) -> None:
        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"target_date_iso": "2026-11-14", "check_only": False, "tick_lms": False},
            mutates_external=True,
        )
        runtime = WorkerRuntime(evidence_root=self.evidence_root)
        gateways = ExternalGateways.production()
        targets = [
            {
                "group_name": "MindX - VT-PTA01 (08:00-10:00)",
                "class_code": "VT-PTA01",
                "week": 45,
                "year": 2026,
                "lesson": "12",
                "class_date_iso": "2026-11-14",
                "session_id": "sess-1",
                "date_iso": "2026-11-14",
                "person": "GV A",
            },
            {
                "group_name": "MindX - VT-PTB01 (10:00-12:00)",
                "class_code": "VT-PTB01",
                "week": 46,
                "year": 2026,
                "lesson": "13",
                "class_date_iso": "2026-11-14",
                "session_id": "sess-2",
                "date_iso": "2026-11-14",
                "person": "GV B",
            },
        ]
        await self.batch_repo.seed_targets(job_id, targets)
        seeded_rows = await self.batch_repo.list_for_job(job_id)
        target_id_by_key = {
            canonical_target_key(dict(row.get("source") or row)): int(row["id"])
            for row in seeded_rows
        }

        opened_pages: list[dict] = []
        closed_pages: list[int] = []
        setup_attempts = 0
        target_attempts: list[tuple[str, int]] = []

        async def _fake_new_page(_sess):
            page = {"id": len(opened_pages) + 1}
            opened_pages.append(page)
            return page

        async def _fake_close_page(_sess, page):
            closed_pages.append(int(page["id"]))

        async def _fake_setup(page, **_kwargs):
            nonlocal setup_attempts
            setup_attempts += 1
            if setup_attempts == 1:
                raise ZaloSetupError(
                    "SELECTOR_READINESS_TIMEOUT",
                    page_state=ScanPageState.SELECTOR_WAIT,
                )
            return {"decision": "SYNC_VERIFIED", "verified": True, "page_id": page["id"]}

        async def _fake_scan_one(_page, *, target, screenshots_dir, attempt: int = 1, **_kw):
            code = target["class_code"]
            target_attempts.append((code, attempt))
            if code == "VT-PTA01" and attempt == 1:
                raise TargetSearchError("TARGET_SEARCH_FILL_ERROR", phase="fill", attempt=1)
            if code == "VT-PTB01":
                raise TargetTimeoutError("target scan hung")
            shot_file = Path(screenshots_dir) / f"{code}.png"
            shot_file.parent.mkdir(parents=True, exist_ok=True)
            shot_file.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            return {
                "group": target["group_name"],
                "class_code": code,
                "session_id": target["session_id"],
                "date_iso": target["date_iso"],
                "person": target["person"],
                "decision": "VERIFIED",
                "evidence_state": "SUBMITTED_VERIFIED",
                "reason": "ok",
                "screenshot_path": str(shot_file),
                "screenshot_sha256": hashlib.sha256(b"\x89PNG\r\n\x1a\nfake").hexdigest(),
                "image_match_method": "EXACT",
            }

        today_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        with (
            patch("services.today_ops_service._new_scan_page", AsyncMock(side_effect=_fake_new_page)),
            patch("services.today_ops_service._close_scan_page", AsyncMock(side_effect=_fake_close_page)),
            patch("services.today_ops_service._setup_scan_page", AsyncMock(side_effect=_fake_setup)),
            patch("services.today_ops_service._scan_one_target", AsyncMock(side_effect=_fake_scan_one)),
        ):
            results, failures, notice = await _scan_ended_zalo_targets(
                object(),
                job_id=job_id,
                targets=targets,
                screenshots_dir=str(Path(self.evidence_root) / f"job_{job_id}"),
                today=today_dt,
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                batch_repo=self.batch_repo,
                target_id_by_key=target_id_by_key,
                persist_batch_targets=True,
            )

        self.assertEqual(setup_attempts, 3)
        self.assertEqual([p["id"] for p in opened_pages], [1, 2, 3])
        self.assertEqual(closed_pages, [1, 2, 3])
        self.assertEqual(target_attempts, [("VT-PTA01", 1), ("VT-PTA01", 2), ("VT-PTB01", 1)])
        self.assertIsNone(notice)
        self.assertEqual(failures, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["decision"], "VERIFIED")
        self.assertEqual(results[1]["evidence_state"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(
            results[1]["evidence"]["classifier_result"]["decision"],
            "INSUFFICIENT_EVIDENCE",
        )
        self.assertEqual(results[1]["error_code"], TARGET_TIMEOUT)
        persisted = await self.batch_repo.list_for_job(job_id)
        by_code = {r["class_code"]: r for r in persisted}
        self.assertEqual(by_code["VT-PTA01"]["scan_status"], "VERIFIED")
        self.assertEqual(by_code["VT-PTB01"]["scan_status"], "FAILED")
        self.assertEqual(by_code["VT-PTB01"]["error_code"], TARGET_TIMEOUT)
