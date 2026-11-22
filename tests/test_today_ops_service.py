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
from db.database import database_context, get_db_connection, init_db
from repositories.audit_repository import AuditRepository
from repositories.evidence_repository import EvidenceRepository
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobCancelledError, JobRepository
from repositories.report_repository import ReportRepository
from repositories.zalo_batch_repository import ZaloBatchRepository, canonical_target_key
from services.external_gateways import ExternalGateways
from services.runtime import WorkerRuntime
from services.today_ops_service import (
    _collect_today_entries_from_snapshot,
    _merge_entry_with_overlay,
    _persist_batch_write_outcomes,
    _scan_ended_zalo_targets,
    build_today_overview,
    parse_target_date_iso,
    parse_today_scope,
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
        self.assertTrue(bcnb_calls[0]["defer_unexpired_checker_notes"])

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
            return [initial_row] if w45_reads == 1 else [reloaded_missing_sid]

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

    def test_today_normalize_and_kpis_for_missing_attendance(self) -> None:
        from services.today_ops_service import _compute_today_kpis, _normalize_today_item

        now_dt = datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        ended_missing_att = _normalize_today_item(
            {
                "week": 45,
                "year": 2026,
                "is_current_week": False,
                "class_code": "VT-KIND-ROB4B04",
                "lesson": "12",
                "class_date": "14/11/2026",
                "class_date_iso": "2026-11-14",
                "group_name": "MindX - VT-KIND-ROB4B04 (08:00-10:00)",
                "person": "GV Robotics",
                "khoi": "Robotics",
                "bcnb_session_id": "sess-rob-12",
                "status": "✅ Đã nộp báo cáo",
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
            },
            now_dt=now_dt,
        )
        self.assertEqual(ended_missing_att["phase"], "ENDED")
        self.assertEqual(ended_missing_att["lms_status_text"], "❌ Chưa điểm danh LMS")
        self.assertEqual(
            ended_missing_att["lms_spck"]["summary_text"], "SPCK khóa (chưa điểm danh)"
        )
        self.assertFalse(ended_missing_att["lms_ready_for_tick"])
        self.assertEqual(ended_missing_att["fail_count"], 1)
        self.assertEqual(
            [it["text"] for it in ended_missing_att["issues"]], ["[LMS] Chưa điểm danh"]
        )
        kpis = _compute_today_kpis([ended_missing_att])
        self.assertEqual(kpis["lms"]["missing_attendance_count"], 1)
        self.assertEqual(kpis["lms"]["ready_for_tick"], 0)

    async def test_defer_unexpired_checker_notes_prevents_early_bcnb_faults_while_allowing_ticks_and_clears(
        self,
    ) -> None:
        now_dt = datetime(2026, 11, 18, 20, 45, tzinfo=BUSINESS_TIMEZONE)
        today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        page = MagicMock()

        # Row 1: Unexpired deadline (20:00 20/11/2026) — Teacher NOT sent Zalo AND MISSING_ATTENDANCE LMS.
        # Checker note must be DEFERRED (no early fault written to BCNB).
        unexpired_fault_res = {
            "group": "MindX - VT-ARMA18 (18:00-20:00)",
            "status": "❌ Chưa báo cáo",
            "evidence_state": "NOT_FOUND_VERIFIED",
            "submitted_for_bcnb": False,
            "zalo_content_issues": [
                {"code": "ZALO_MISSING_FAIL", "severity": "FAIL", "text": "[Zalo] Chưa gửi báo cáo"}
            ],
            "lms_attendance": {"status": "MISSING_ATTENDANCE", "unverified_count": 0, "attended_count": 0},
            "lms_comments": {"status": "MISSING_ATTENDANCE", "submitted_count": 0},
            "lms_spck": {"status": "MISSING_ATTENDANCE", "submitted_count": 0},
            "lms_quality_issues": [
                {"code": "LMS_QUALITY_FAIL", "severity": "FAIL", "text": "[LMS] Chưa điểm danh"}
            ],
            "scan_target": {
                "week": 47,
                "year": 2026,
                "class_code": "VT-ARMA18",
                "lesson": "2",
                "class_date": "18/11/2026",
                "class_date_iso": "2026-11-18",
                "group_name": "MindX - VT-ARMA18 (18:00-20:00)",
                "lms_start_time": "18:00",
                "lms_end_time": "20:00",
                "bcnb_session_id": "sess-arma-2",
            },
            "evidence": {
                "date_context": {
                    "target_week": 47,
                    "target_year": 2026,
                    "target_class_code": "VT-ARMA18",
                    "target_lesson": "2",
                    "target_class_date": "2026-11-18",
                },
                "classifier_result": {},
                "images": {},
            },
        }
        # Row 2: Unexpired deadline, teacher completed Zalo + LMS AND previously had a BCNB note "[LMS] Chưa điểm danh".
        # Bot must BOTH tick NX Zalo + NX LMS AND clear the obsolete "[LMS] Chưa điểm danh" note on Teaching.
        cleared_res = {
            "group": "MindX - VT-JSB15 (18:00-20:00)",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "delay_days": 0,
            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5},
            "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
            "lms_spck": {"status": "NOT_REQUIRED"},
            "scan_target": {
                "week": 47,
                "year": 2026,
                "class_code": "VT-JSB15",
                "lesson": "5",
                "class_date": "18/11/2026",
                "class_date_iso": "2026-11-18",
                "group_name": "MindX - VT-JSB15 (18:00-20:00)",
                "lms_start_time": "18:00",
                "lms_end_time": "20:00",
                "bcnb_session_id": "sess-jsb-5",
            },
            "evidence": {
                "date_context": {
                    "target_week": 47,
                    "target_year": 2026,
                    "target_class_code": "VT-JSB15",
                    "target_lesson": "5",
                    "target_class_date": "2026-11-18",
                },
                "classifier_result": {"matched_ui_sent_at": "18/11/2026"},
                "images": {"proof": {"path": "/api/evidence/1/jsb.png", "sha256": "JSBSHA"}},
            },
        }
        # Row 3: Expired deadline ("20:00 13/11/2026\nhết hạn") in previous week -> MUST still write "[LMS] Chưa điểm danh" to BCNB!
        expired_fault_res = {
            "group": "MindX - VT-ROB4B (18:00-20:00)",
            "status": "❌ Chưa báo cáo",
            "evidence_state": "NOT_FOUND_VERIFIED",
            "submitted_for_bcnb": False,
            "zalo_content_issues": [
                {"code": "ZALO_MISSING_FAIL", "severity": "FAIL", "text": "[Zalo] Chưa gửi báo cáo"}
            ],
            "lms_attendance": {"status": "MISSING_ATTENDANCE", "unverified_count": 0, "attended_count": 0},
            "lms_comments": {"status": "MISSING_ATTENDANCE", "submitted_count": 0},
            "lms_spck": {"status": "MISSING_ATTENDANCE", "submitted_count": 0},
            "lms_quality_issues": [
                {"code": "LMS_QUALITY_FAIL", "severity": "FAIL", "text": "[LMS] Chưa điểm danh"}
            ],
            "scan_target": {
                "week": 46,
                "year": 2026,
                "class_code": "VT-ROB4B",
                "lesson": "12",
                "class_date": "11/11/2026",
                "class_date_iso": "2026-11-11",
                "bcnb_session_id": "sess-rob-12",
            },
            "evidence": {
                "date_context": {
                    "target_week": 46,
                    "target_year": 2026,
                    "target_class_code": "VT-ROB4B",
                    "target_lesson": "12",
                    "target_class_date": "2026-11-11",
                },
                "classifier_result": {},
                "images": {},
            },
        }

        row_unexpired = _make_fake_bcnb_row(
            class_code="VT-ARMA18",
            lesson="2",
            date_str="18/11/2026",
            date_iso="2026-11-18",
            session_id="sess-arma-2",
            zalo_deadline="20:00 20/11/2026\ncòn 1d 23h 15' 00s",
            lms_deadline="20:00 20/11/2026\ncòn 1d 23h 15' 00s",
        )
        row_cleared = _make_fake_bcnb_row(
            class_code="VT-JSB15",
            lesson="5",
            date_str="18/11/2026",
            date_iso="2026-11-18",
            session_id="sess-jsb-5",
            zalo_deadline="20:00 20/11/2026\ncòn 1d 23h 15' 00s",
            lms_deadline="20:00 20/11/2026\ncòn 1d 23h 15' 00s",
        )
        row_cleared["qc_issue_note"] = "[LMS] Chưa điểm danh"
        row_expired = _make_fake_bcnb_row(
            class_code="VT-ROB4B",
            lesson="12",
            date_str="11/11/2026",
            date_iso="2026-11-11",
            session_id="sess-rob-12",
            zalo_deadline="20:00 13/11/2026\nhết hạn\n13/11 09:23",
            lms_deadline="20:00 13/11/2026\nhết hạn\n16/11 00:31",
        )

        # Record real provenance audit for row_cleared so split_checker_issue_note recognizes "[LMS] Chưa điểm danh" as proven auto
        seed_job_id = await self.job_repo.create_job("ZALO_SCAN", {"execution_mode": "production"})
        audit_row, _ = await self.audit_repo.record_intent(
            action_key="seed-jsb-prev-note",
            job_id=seed_job_id,
            action_type="BCNB_UPDATE_CHECKER_NOTE",
            target_type="CLASS",
            target_id="VT-JSB15",
            mode="production",
            before_state={"qc_issue_note": ""},
            intended_state={"qc_issue_note": "[LMS] Chưa điểm danh"},
            reason={
                "week": 47,
                "year": 2026,
                "lesson": "5",
                "class_date": "2026-11-18",
                "bcnb_session_id": "sess-jsb-5",
                "auto_issues": ["[LMS] Chưa điểm danh"],
                "merged_note": "[LMS] Chưa điểm danh",
            },
        )
        await self.audit_repo.complete_action(
            audit_row["id"],
            outcome="VERIFIED",
            after_state={"qc_issue_note": "[LMS] Chưa điểm danh", "post_verified": True},
        )

        async def _read_by_week(_page, *, week, **_kwargs):
            if int(week) == 47:
                return [row_unexpired, row_cleared]
            return [row_expired]

        tick_mock = AsyncMock(return_value="VERIFIED")
        note_mock = AsyncMock(return_value="VERIFIED")

        scan_plan = {
            "latest_week": {"week": 47, "year": 2026},
            "required_week": {"week": 46, "year": 2026},
            "selected_weeks": [{"week": 46, "year": 2026}, {"week": 47, "year": 2026}],
            "targets": [
                unexpired_fault_res["scan_target"],
                cleared_res["scan_target"],
                expired_fault_res["scan_target"],
            ],
        }

        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(side_effect=_read_by_week)),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", note_mock),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-ARMA18": [{}], "VT-JSB15": [{}], "VT-ROB4B": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=901,
                execution_mode="production",
                scan_results=[unexpired_fault_res, cleared_res, expired_fault_res],
                today=today,
                now=now_dt,
                scan_plan=scan_plan,
                job_repo=self.job_repo,
                audit_repo=self.audit_repo,
                tick_lms=True,
                check_only=False,
                defer_unexpired_checker_notes=True,
            )

        self.assertEqual(summary["updated_count"], 1)
        self.assertEqual(summary["lms_updated_count"], 1)
        self.assertEqual(unexpired_fault_res.get("checker_note_outcome"), "DEFERRED_BEFORE_DEADLINE")
        # UI issues on the scan result object remain untouched for Lớp Hôm Nay live display & Copy nhắc GV
        self.assertEqual(len(unexpired_fault_res.get("zalo_content_issues") or []), 1)
        self.assertEqual(len(unexpired_fault_res.get("lms_quality_issues") or []), 1)

        note_calls = {
            (c.kwargs["class_code"], c.kwargs["week"]): list(c.kwargs["auto_issues"])
            for c in note_mock.await_args_list
        }
        self.assertNotIn(("VT-ARMA18", 47), note_calls)
        self.assertEqual(note_calls[("VT-JSB15", 47)], [])
        self.assertEqual(len(note_calls[("VT-ROB4B", 46)]), 2)

    def test_is_bcnb_deadline_expired_handles_bcnb_formats(self) -> None:
        from services.zalo_bcnb_service import _is_bcnb_deadline_expired

        ref = datetime(2026, 11, 18, 20, 30, tzinfo=BUSINESS_TIMEZONE)
        self.assertFalse(_is_bcnb_deadline_expired("20:00 20/11/2026\ncòn 1d 23h", now_dt=ref))
        self.assertTrue(_is_bcnb_deadline_expired("20:00 18/11/2026\nhết hạn\n18/11 21:00", now_dt=ref))
        self.assertFalse(_is_bcnb_deadline_expired("21:00 18/11/2026\nhết hạn", now_dt=ref))
        self.assertTrue(_is_bcnb_deadline_expired("13/11/2026 20:00", now_dt=ref))
        self.assertTrue(_is_bcnb_deadline_expired("2026-11-18 19:00", now_dt=ref))
        self.assertFalse(_is_bcnb_deadline_expired("18/11/2026", now_dt=ref))
        self.assertTrue(_is_bcnb_deadline_expired("17/11/2026", now_dt=ref))
        self.assertFalse(_is_bcnb_deadline_expired("", now_dt=ref))
        self.assertFalse(_is_bcnb_deadline_expired("—", now_dt=ref))
        self.assertTrue(_is_bcnb_deadline_expired("hết hạn", now_dt=ref))

    async def test_defer_all_new_checker_notes_defers_expired_faults_clears_fixed_and_preserves_manual(
        self,
    ) -> None:
        now_dt = datetime(2026, 11, 22, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        page = MagicMock()

        # Row A: Expired >48h ago (13/11), has new [Zalo], [LMS], and [SPCK] issues + manual note.
        # defer_all_new_checker_notes=True must NOT write new issues and must set DEFERRED_FOR_FULL_SCAN.
        overdue_new_fault_res = {
            "group": "MindX - VT-OVERDUE1 (18:00-20:00)",
            "status": "❌ Chưa báo cáo",
            "evidence_state": "NOT_FOUND_VERIFIED",
            "submitted_for_bcnb": False,
            "zalo_content_issues": [
                {"code": "ZALO_MISSING_FAIL", "severity": "FAIL", "text": "[Zalo] Chưa gửi báo cáo"}
            ],
            "lms_attendance": {"status": "MISSING_ATTENDANCE", "unverified_count": 0, "attended_count": 0},
            "lms_comments": {"status": "MISSING_ATTENDANCE", "submitted_count": 0},
            "lms_spck": {"status": "MISSING", "submitted_count": 0},
            "lms_quality_issues": [
                {"code": "LMS_QUALITY_FAIL", "severity": "FAIL", "text": "[LMS] Chưa điểm danh"},
                {"code": "LMS_SPCK_MISSING", "severity": "FAIL", "text": "[SPCK] Chưa nộp (0/5)"},
            ],
            "scan_target": {
                "week": 47,
                "year": 2026,
                "class_code": "VT-OVERDUE1",
                "lesson": "12",
                "class_date": "16/11/2026",
                "class_date_iso": "2026-11-16",
                "bcnb_session_id": "sess-od-1",
            },
            "evidence": {
                "date_context": {
                    "target_week": 47,
                    "target_year": 2026,
                    "target_class_code": "VT-OVERDUE1",
                    "target_lesson": "12",
                    "target_class_date": "2026-11-16",
                },
                "classifier_result": {},
                "images": {},
            },
        }
        # Row B: Expired >48h ago, teacher fixed LMS + submitted Zalo; obsolete proven auto note must clear while keeping manual note.
        fixed_cleared_res = {
            "group": "MindX - VT-FIXED1 (18:00-20:00)",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "submitted_for_bcnb": True,
            "delay_days": 0,
            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5},
            "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
            "lms_spck": {"status": "NOT_REQUIRED"},
            "scan_target": {
                "week": 47,
                "year": 2026,
                "class_code": "VT-FIXED1",
                "lesson": "5",
                "class_date": "16/11/2026",
                "class_date_iso": "2026-11-16",
                "bcnb_session_id": "sess-fx-1",
            },
            "evidence": {
                "date_context": {
                    "target_week": 47,
                    "target_year": 2026,
                    "target_class_code": "VT-FIXED1",
                    "target_lesson": "5",
                    "target_class_date": "2026-11-16",
                },
                "classifier_result": {"matched_ui_sent_at": "16/11/2026"},
                "images": {"proof": {"path": "/api/evidence/1/fx.png", "sha256": "FXSHA"}},
            },
        }
        # Row C: Expired >48h ago, teacher fixed old proven LMS issue, but has a NEW Zalo missing fault.
        # Must clear old LMS issue, keep manual note byte-for-byte, and NEVER write the new Zalo issue.
        mixed_clear_defer_res = {
            "group": "MindX - VT-MIXED1 (18:00-20:00)",
            "status": "❌ Chưa báo cáo",
            "evidence_state": "NOT_FOUND_VERIFIED",
            "submitted_for_bcnb": False,
            "zalo_content_issues": [
                {"code": "ZALO_MISSING_FAIL", "severity": "FAIL", "text": "[Zalo] Chưa gửi báo cáo"}
            ],
            "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 4},
            "lms_comments": {"status": "COMPLETE", "submitted_count": 4},
            "lms_spck": {"status": "NOT_REQUIRED"},
            "scan_target": {
                "week": 47,
                "year": 2026,
                "class_code": "VT-MIXED1",
                "lesson": "4",
                "class_date": "16/11/2026",
                "class_date_iso": "2026-11-16",
                "bcnb_session_id": "sess-mx-1",
            },
            "evidence": {
                "date_context": {
                    "target_week": 47,
                    "target_year": 2026,
                    "target_class_code": "VT-MIXED1",
                    "target_lesson": "4",
                    "target_class_date": "2026-11-16",
                },
                "classifier_result": {},
                "images": {},
            },
        }

        row_od = _make_fake_bcnb_row(
            class_code="VT-OVERDUE1",
            lesson="12",
            date_str="16/11/2026",
            date_iso="2026-11-16",
            session_id="sess-od-1",
            zalo_deadline="20:00 18/11/2026\nhết hạn",
            lms_deadline="20:00 18/11/2026\nhết hạn",
        )
        row_od["qc_issue_note"] = "QC: Học viên xin phép"
        row_fx = _make_fake_bcnb_row(
            class_code="VT-FIXED1",
            lesson="5",
            date_str="16/11/2026",
            date_iso="2026-11-16",
            session_id="sess-fx-1",
            zalo_deadline="20:00 18/11/2026\nhết hạn",
            lms_deadline="20:00 18/11/2026\nhết hạn",
        )
        row_fx["qc_issue_note"] = "QC: Ghi chú tay | [LMS] Chưa điểm danh"
        row_mx = _make_fake_bcnb_row(
            class_code="VT-MIXED1",
            lesson="4",
            date_str="16/11/2026",
            date_iso="2026-11-16",
            session_id="sess-mx-1",
            zalo_deadline="20:00 18/11/2026\nhết hạn",
            lms_deadline="20:00 18/11/2026\nhết hạn",
        )
        row_mx["qc_issue_note"] = "QC: Giữ nguyên | [LMS] Chưa điểm danh"

        seed_job_id = await self.job_repo.create_job("ZALO_SCAN", {"execution_mode": "production"})
        for code, lesson, sid, note in (
            ("VT-FIXED1", "5", "sess-fx-1", "QC: Ghi chú tay | [LMS] Chưa điểm danh"),
            ("VT-MIXED1", "4", "sess-mx-1", "QC: Giữ nguyên | [LMS] Chưa điểm danh"),
        ):
            arow, _ = await self.audit_repo.record_intent(
                action_key=f"seed-prev-{code}",
                job_id=seed_job_id,
                action_type="BCNB_UPDATE_CHECKER_NOTE",
                target_type="CLASS",
                target_id=code,
                mode="production",
                before_state={"qc_issue_note": ""},
                intended_state={"qc_issue_note": note},
                reason={
                    "week": 47,
                    "year": 2026,
                    "lesson": lesson,
                    "class_date": "2026-11-16",
                    "bcnb_session_id": sid,
                    "auto_issues": ["[LMS] Chưa điểm danh"],
                    "merged_note": note,
                },
            )
            await self.audit_repo.complete_action(
                arow["id"],
                outcome="VERIFIED",
                after_state={"qc_issue_note": note, "post_verified": True},
            )

        async def _read_by_week(_page, *, week, **_kwargs):
            return [row_od, row_fx, row_mx] if int(week) == 47 else []

        tick_mock = AsyncMock(return_value="VERIFIED")
        note_mock = AsyncMock(return_value="VERIFIED")
        scan_plan = {
            "latest_week": {"week": 47, "year": 2026},
            "required_week": {"week": 46, "year": 2026},
            "selected_weeks": [{"week": 46, "year": 2026}, {"week": 47, "year": 2026}],
            "targets": [
                overdue_new_fault_res["scan_target"],
                fixed_cleared_res["scan_target"],
                mixed_clear_defer_res["scan_target"],
            ],
        }
        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(side_effect=_read_by_week)),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", note_mock),
            patch(
                "services.zalo_bcnb_service.fetch_and_evaluate_lms_quality",
                AsyncMock(return_value={"VT-OVERDUE1": [{}], "VT-FIXED1": [{}], "VT-MIXED1": [{}]}),
            ),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
        ):
            summary = await update_bcnb_reports(
                page,
                job_id=955,
                execution_mode="production",
                scan_results=[overdue_new_fault_res, fixed_cleared_res, mixed_clear_defer_res],
                today=today,
                now=now_dt,
                scan_plan=scan_plan,
                job_repo=self.job_repo,
                audit_repo=self.audit_repo,
                tick_lms=True,
                check_only=False,
                defer_unexpired_checker_notes=True,
                defer_all_new_checker_notes=True,
            )

        self.assertEqual(summary["updated_count"], 1)
        self.assertEqual(summary["lms_updated_count"], 2)
        self.assertEqual(overdue_new_fault_res.get("checker_note_outcome"), "DEFERRED_FOR_FULL_SCAN")
        note_calls = {c.kwargs["class_code"]: c.kwargs for c in note_mock.await_args_list}
        self.assertNotIn("VT-OVERDUE1", note_calls)
        self.assertEqual(note_calls["VT-FIXED1"]["auto_issues"], [])
        self.assertEqual(note_calls["VT-FIXED1"]["reason"]["merged_note"], "QC: Ghi chú tay")
        self.assertEqual(note_calls["VT-MIXED1"]["auto_issues"], [])
        self.assertEqual(note_calls["VT-MIXED1"]["reason"]["merged_note"], "QC: Giữ nguyên")

        # Default (defer_all_new_checker_notes=False) STILL writes expired issues for full ZALO_SCAN
        note_mock_default = AsyncMock(return_value="VERIFIED")
        overdue_default_res = json.loads(json.dumps(overdue_new_fault_res))
        with (
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(side_effect=_read_by_week)),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", AsyncMock(return_value="VERIFIED")),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", note_mock_default),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-OVERDUE1": [{}]})),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
        ):
            await update_bcnb_reports(
                page,
                job_id=956,
                execution_mode="production",
                scan_results=[overdue_default_res],
                today=today,
                now=now_dt,
                scan_plan=scan_plan,
                job_repo=self.job_repo,
                audit_repo=self.audit_repo,
                tick_lms=False,
                check_only=False,
                defer_unexpired_checker_notes=True,
                defer_all_new_checker_notes=False,
            )
        default_calls = {c.kwargs["class_code"]: c.kwargs for c in note_mock_default.await_args_list}
        self.assertIn("VT-OVERDUE1", default_calls)
        self.assertEqual(len(default_calls["VT-OVERDUE1"]["auto_issues"]), 3)

    async def test_run_today_realtime_sync_persists_per_row_complete_write_before_mid_run_cancel(
        self,
    ) -> None:
        snap = _make_two_week_snapshot()
        # Both VT-PTA01 and VT-PTB01 are ended at 15:00 on 2026-11-14
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

        class _FakeSess:
            landing_facts_provider = None

        @asynccontextmanager
        async def _fake_session():
            yield _FakeSess()

        gateways.browser.persistent_session = _fake_session

        async def _fake_prefetch(_page, *, today_candidates, **_kwargs):
            for c in today_candidates:
                c["lms_attendance"] = {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5}
                c["lms_comments"] = {"status": "COMPLETE", "submitted_count": 5}
                c["lms_spck"] = {"status": "COMPLETE", "submitted_count": 5, "missing_comment_count": 0}
            return {"VT-PTA01": [{}], "VT-PTB01": [{}]}

        async def _fake_scan_one(_page, *, job_id: int, target: dict, screenshots_dir: str, **_kw):
            shot_file = Path(screenshots_dir) / f"{target['class_code']}.png"
            shot_bytes = b"\x89PNG\r\n\x1a\nfake"
            shot_file.write_bytes(shot_bytes)
            shot_sha = hashlib.sha256(shot_bytes).hexdigest()
            return {
                "group": target["group_name"],
                "person": target.get("person") or "GV",
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

        row_pta = _make_fake_bcnb_row(
            class_code="VT-PTA01",
            lesson="12",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-45-pta",
        )
        row_ptb = _make_fake_bcnb_row(
            class_code="VT-PTB01",
            lesson="13",
            date_str="14/11/2026",
            date_iso="2026-11-14",
            session_id="sess-46-ptb",
        )

        async def _read_by_week(_page, *, week, **_kwargs):
            return [row_pta] if int(week) == 45 else [row_ptb]

        async def _tick_then_cancel(**kwargs):
            if kwargs.get("class_code") == "VT-PTB01":
                raise JobCancelledError("Cancelled on second row")
            return "VERIFIED"

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
            patch("services.zalo_bcnb_service._read_bcnb_schedule_rows", AsyncMock(side_effect=_read_by_week)),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", AsyncMock(side_effect=_tick_then_cancel)),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", AsyncMock(return_value="NO_CHANGE")),
            patch("services.zalo_bcnb_service.fetch_and_evaluate_lms_quality", AsyncMock(return_value={"VT-PTA01": [{}], "VT-PTB01": [{}]})),
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
        self.assertEqual(job["status"], "CANCELLED")
        persisted = {r["class_code"]: r for r in await self.batch_repo.list_for_job(job_id)}
        self.assertEqual(persisted["VT-PTA01"]["write_status"], "VERIFIED")
        self.assertTrue((persisted["VT-PTA01"]["result"] or {}).get("nx_zalo_checked"))
        self.assertEqual(persisted["VT-PTB01"]["write_status"], "OUTCOME_UNKNOWN")

    async def test_parse_today_scope_and_build_today_overview_current_week_multiday(
        self,
    ) -> None:
        self.assertEqual(parse_today_scope(None), "date")
        self.assertEqual(parse_today_scope(""), "date")
        self.assertEqual(parse_today_scope("date"), "date")
        self.assertEqual(parse_today_scope("current_week"), "current_week")
        with self.assertRaises(ValueError):
            parse_today_scope("invalid_scope")

        snap = {
            "version": 4,
            "reliable": True,
            "discovered_groups": [
                {"TenNhom": "MindX - VT-MON01 (18:00-20:00)", "MaLop": "VT-MON01", "CohortKey": "K1"},
                {"TenNhom": "MindX - VT-WED02 (18:00-20:00)", "MaLop": "VT-WED02", "CohortKey": "K2"},
                {"TenNhom": "MindX - VT-SUN04 (18:00-20:00)", "MaLop": "VT-SUN04", "CohortKey": "K4"},
            ],
            "source_group_names": [
                "MindX - VT-MON01 (18:00-20:00)",
                "MindX - VT-WED02 (18:00-20:00)",
                "MindX - VT-SUN04 (18:00-20:00)",
            ],
            "expected_count": 3,
            "bcnb_week_plan": {
                "latest_week": {"week": 47, "year": 2026},
                "required_week": {"week": 46, "year": 2026},
                "selected_weeks": [{"week": 46, "year": 2026}, {"week": 47, "year": 2026}],
                "required_data": [
                    {
                        "ma_lop": "VT-OLD46",
                        "buoi_so": "9",
                        "ngay_hoc": "22/11/2026",
                        "ngay_hoc_iso": "2026-11-22",
                        "giao_vien": "GV Old",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-46-old",
                    }
                ],
                "latest_data": [
                    {
                        "ma_lop": "VT-MON01",
                        "buoi_so": "1",
                        "ngay_hoc": "16/11/2026",
                        "ngay_hoc_iso": "2026-11-16",
                        "giao_vien": "GV Mon",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-47-mon",
                        "nx_zalo_checked": True,
                        "nx_lms_checked": True,
                    },
                    {
                        "ma_lop": "VT-WED02",
                        "buoi_so": "2",
                        "ngay_hoc": "18/11/2026",
                        "ngay_hoc_iso": "2026-11-18",
                        "giao_vien": "GV Wed",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-47-wed",
                        "nx_zalo_checked": True,
                        "nx_lms_checked": False,
                    },
                    {
                        "ma_lop": "VT-SAT03",
                        "buoi_so": "3",
                        "ngay_hoc": "21/11/2026",
                        "ngay_hoc_iso": "2026-11-21",
                        "giao_vien": "GV Sat",
                        "khoi": "Robotics",
                        "bcnb_session_id": "sess-47-sat",
                        "nx_zalo_checked": False,
                        "nx_lms_checked": False,
                    },
                    {
                        "ma_lop": "VT-SUN04",
                        "buoi_so": "4",
                        "ngay_hoc": "22/11/2026",
                        "ngay_hoc_iso": "2026-11-22",
                        "giao_vien": "GV Sun",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-47-sun",
                        "nx_zalo_checked": False,
                        "nx_lms_checked": False,
                    },
                ],
            },
            "scan_targets": [
                {
                    "group_name": "MindX - VT-MON01 (18:00-20:00)",
                    "person": "GV Mon",
                    "class_code": "VT-MON01",
                    "lesson": "1",
                    "class_date": "16/11/2026",
                    "class_date_iso": "2026-11-16",
                    "week": 47,
                    "year": 2026,
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-47-mon",
                    "nx_zalo_checked": True,
                    "nx_lms_checked": True,
                },
                {
                    "group_name": "MindX - VT-WED02 (18:00-20:00)",
                    "person": "GV Wed",
                    "class_code": "VT-WED02",
                    "lesson": "2",
                    "class_date": "18/11/2026",
                    "class_date_iso": "2026-11-18",
                    "week": 47,
                    "year": 2026,
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-47-wed",
                    "nx_zalo_checked": True,
                    "nx_lms_checked": False,
                },
            ],
            "deferred_targets": [
                {
                    "group_name": "MindX - VT-SUN04 (18:00-20:00)",
                    "person": "GV Sun",
                    "class_code": "VT-SUN04",
                    "lesson": "4",
                    "class_date": "22/11/2026",
                    "class_date_iso": "2026-11-22",
                    "week": 47,
                    "year": 2026,
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-47-sun",
                    "nx_zalo_checked": False,
                    "nx_lms_checked": False,
                }
            ],
            "exempt_targets": [],
        }
        await self.group_repo.apply_discovery_snapshot(snap)

        week_job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {
                "scope": "current_week",
                "target_date_iso": "2026-11-22",
                "check_only": True,
                "execution_mode": "dry-run",
            },
        )
        await self.job_repo.update_job(week_job_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            week_job_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "scope": "current_week",
                    "scope_week": {"week": 47, "year": 2026},
                    "check_only": True,
                    "target_date_iso": "2026-11-22",
                    "today_items": [
                        {
                            "group": "MindX - VT-MON01 (18:00-20:00)",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "scan_target": {
                                "week": 47,
                                "year": 2026,
                                "class_code": "VT-MON01",
                                "lesson": "1",
                                "class_date_iso": "2026-11-16",
                                "bcnb_session_id": "sess-47-mon",
                            },
                            "evidence": {
                                "images": {
                                    "proof": {"path": f"/api/evidence/{week_job_id}/mon.png", "sha256": "MON"}
                                }
                            },
                        },
                        {
                            "group": "MindX - VT-WED02 (18:00-20:00)",
                            "skipped_already_completed": True,
                            "evidence_state": "NOT_SCANNED",
                            "scan_target": {
                                "week": 47,
                                "year": 2026,
                                "class_code": "VT-WED02",
                                "lesson": "2",
                                "class_date_iso": "2026-11-18",
                                "bcnb_session_id": "sess-47-wed",
                            },
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        )

        # A date-mode job should match date banner, while week_job_id matches current_week banner
        date_job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"scope": "date", "target_date_iso": "2026-11-22", "check_only": True, "execution_mode": "dry-run"},
        )
        await self.job_repo.update_job(date_job_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            date_job_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "scope": "date",
                    "check_only": True,
                    "target_date_iso": "2026-11-22",
                    "today_items": [],
                },
                ensure_ascii=False,
            ),
        )

        fake_container = MagicMock()
        fake_container.settings.bcnb_dry_run = True
        fake_container.settings.zalo_plan_max_age_minutes = 30
        fake_container.group_repo = self.group_repo

        now_sun = datetime(2026, 11, 22, 15, 0, tzinfo=BUSINESS_TIMEZONE)
        week_overview = await build_today_overview(
            fake_container,
            target_date_iso="2026-11-22",
            scope="current_week",
            now=now_sun,
        )
        self.assertEqual(week_overview["scope"], "current_week")
        self.assertEqual(week_overview["scope_week"], {"week": 47, "year": 2026})
        self.assertEqual(week_overview["scope_label"], "Tuần thực tế W47/2026")
        self.assertEqual(week_overview["latest_sync_job"]["job_id"], week_job_id)

        items = week_overview["items"]
        self.assertEqual(
            [it["class_code"] for it in items],
            ["VT-MON01", "VT-WED02", "VT-SAT03", "VT-SUN04"],
        )
        self.assertEqual(
            [it["class_date_iso"] for it in items],
            ["2026-11-16", "2026-11-18", "2026-11-21", "2026-11-22"],
        )
        self.assertTrue(items[0]["bcnb_completed_both"])
        self.assertEqual(items[0]["screenshot_url"], f"/api/evidence/{week_job_id}/mon.png")
        self.assertFalse(items[1]["bcnb_completed_both"])
        self.assertEqual(items[1]["zalo_category"], "not_scanned")  # skipped_already_completed item ignored
        self.assertTrue(items[2]["missing_zalo_group"])
        self.assertEqual(items[3]["phase"], "UPCOMING")
        self.assertEqual(week_overview["kpis"]["bcnb_pending"]["completed_both_count"], 1)

        date_overview = await build_today_overview(
            fake_container,
            target_date_iso="2026-11-22",
            scope="date",
            now=now_sun,
        )
        self.assertEqual(date_overview["scope"], "date")
        self.assertEqual(date_overview["scope_label"], "Ngày 2026-11-22")
        self.assertEqual(date_overview["latest_sync_job"]["job_id"], date_job_id)
        self.assertEqual({it["class_code"] for it in date_overview["items"]}, {"VT-OLD46", "VT-SUN04"})

    async def test_run_today_realtime_sync_current_week_skip_completed_and_selected_override(
        self,
    ) -> None:
        snap = {
            "version": 4,
            "reliable": True,
            "discovered_groups": [
                {"TenNhom": "MindX - VT-MON01 (18:00-20:00)", "MaLop": "VT-MON01", "CohortKey": "K1"},
                {"TenNhom": "MindX - VT-WED02 (18:00-20:00)", "MaLop": "VT-WED02", "CohortKey": "K2"},
                {"TenNhom": "MindX - VT-SUN04 (18:00-20:00)", "MaLop": "VT-SUN04", "CohortKey": "K4"},
            ],
            "source_group_names": [
                "MindX - VT-MON01 (18:00-20:00)",
                "MindX - VT-WED02 (18:00-20:00)",
                "MindX - VT-SUN04 (18:00-20:00)",
            ],
            "expected_count": 3,
            "bcnb_week_plan": {
                "latest_week": {"week": 47, "year": 2026},
                "required_week": {"week": 46, "year": 2026},
                "selected_weeks": [{"week": 46, "year": 2026}, {"week": 47, "year": 2026}],
                "required_data": [],
                "latest_data": [
                    {
                        "ma_lop": "VT-MON01",
                        "buoi_so": "1",
                        "ngay_hoc": "16/11/2026",
                        "ngay_hoc_iso": "2026-11-16",
                        "giao_vien": "GV Mon",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-47-mon",
                        "nx_zalo_checked": True,
                        "nx_lms_checked": True,
                    },
                    {
                        "ma_lop": "VT-WED02",
                        "buoi_so": "2",
                        "ngay_hoc": "18/11/2026",
                        "ngay_hoc_iso": "2026-11-18",
                        "giao_vien": "GV Wed",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-47-wed",
                        "nx_zalo_checked": False,
                        "nx_lms_checked": False,
                    },
                    {
                        "ma_lop": "VT-SUN04",
                        "buoi_so": "4",
                        "ngay_hoc": "22/11/2026",
                        "ngay_hoc_iso": "2026-11-22",
                        "giao_vien": "GV Sun",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-47-sun",
                        "nx_zalo_checked": False,
                        "nx_lms_checked": False,
                    },
                ],
            },
            "scan_targets": [
                {
                    "group_name": "MindX - VT-MON01 (18:00-20:00)",
                    "person": "GV Mon",
                    "class_code": "VT-MON01",
                    "lesson": "1",
                    "class_date": "16/11/2026",
                    "class_date_iso": "2026-11-16",
                    "week": 47,
                    "year": 2026,
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-47-mon",
                    "nx_zalo_checked": True,
                    "nx_lms_checked": True,
                },
                {
                    "group_name": "MindX - VT-WED02 (18:00-20:00)",
                    "person": "GV Wed",
                    "class_code": "VT-WED02",
                    "lesson": "2",
                    "class_date": "18/11/2026",
                    "class_date_iso": "2026-11-18",
                    "week": 47,
                    "year": 2026,
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-47-wed",
                    "nx_zalo_checked": False,
                    "nx_lms_checked": False,
                },
            ],
            "deferred_targets": [
                {
                    "group_name": "MindX - VT-SUN04 (18:00-20:00)",
                    "person": "GV Sun",
                    "class_code": "VT-SUN04",
                    "lesson": "4",
                    "class_date": "22/11/2026",
                    "class_date_iso": "2026-11-22",
                    "week": 47,
                    "year": 2026,
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-47-sun",
                    "nx_zalo_checked": False,
                    "nx_lms_checked": False,
                }
            ],
            "exempt_targets": [],
        }
        await self.group_repo.apply_discovery_snapshot(snap)

        # Previous scan stored screenshot + LMS evidence for VT-MON01
        prev_job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"scope": "current_week", "check_only": False, "execution_mode": "dry-run"},
        )
        await self.job_repo.update_job(prev_job_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            prev_job_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "scope": "current_week",
                    "scope_week": {"week": 47, "year": 2026},
                    "check_only": False,
                    "today_items": [
                        {
                            "group": "MindX - VT-MON01 (18:00-20:00)",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "nx_zalo_checked": True,
                            "nx_lms_checked": True,
                            "scan_target": {
                                "week": 47,
                                "year": 2026,
                                "class_code": "VT-MON01",
                                "lesson": "1",
                                "class_date_iso": "2026-11-16",
                                "bcnb_session_id": "sess-47-mon",
                            },
                            "evidence": {
                                "images": {
                                    "proof": {"path": f"/api/evidence/{prev_job_id}/mon_prev.png", "sha256": "PREV"}
                                }
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
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
                "VT-WED02": [{"lessonNumber": 2, "startTime": "18:00", "endTime": "20:00", "comments": [], "students": []}],
                "VT-SUN04": [{"lessonNumber": 4, "startTime": "18:00", "endTime": "20:00", "comments": [], "students": []}],
                "VT-MON01": [{"lessonNumber": 1, "startTime": "18:00", "endTime": "20:00", "comments": [], "students": []}],
            }
        )
        gateways = ExternalGateways(
            browser=browser_gw,
            lms_ocr=MagicMock(),
            student_ocr_factory=MagicMock,
            lms_quality=lms_gw,
        )
        runtime = WorkerRuntime(external=gateways, evidence_root=self.evidence_root)

        scanned_targets: list[tuple[str, str, str]] = []

        async def _fake_scan_one(_page, *, job_id: int, target: dict, screenshots_dir: str, today: datetime, **_kw):
            scanned_targets.append((target["class_code"], target["class_date_iso"], today.date().isoformat()))
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
                            "sha256": "NEWSHA",
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

        async def _fake_update_bcnb(page, **kwargs):
            return {
                "selected_weeks": kwargs["scan_plan"]["selected_weeks"],
                "updated_count": 1,
                "lms_updated_count": 0,
                "failed_targets": [],
                "lms_failed_targets": [],
                "target_outcomes": [],
                "lms_target_outcomes": [],
            }

        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {
                "scope": "current_week",
                "skip_completed_bcnb": True,
                "target_date_iso": "2026-11-22",
                "check_only": False,
                "tick_lms": True,
            },
        )
        fake_page = MagicMock()
        fake_page.goto = AsyncMock()
        now_sun = datetime(2026, 11, 22, 15, 0, tzinfo=BUSINESS_TIMEZONE)
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
                {
                    "scope": "current_week",
                    "skip_completed_bcnb": True,
                    "target_date_iso": "2026-11-22",
                    "check_only": False,
                    "tick_lms": True,
                    "refresh_discovery_if_stale": False,
                },
                "dry-run",
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                group_repo=self.group_repo,
                audit_repo=self.audit_repo,
                evidence_repo=self.evidence_repo,
                batch_repo=self.batch_repo,
                now_override=now_sun,
            )

        # VT-MON01 skipped, VT-WED02 scanned with its own Monday/Wed class_date_iso (today=Sun 22/11), VT-SUN04 deferred
        self.assertEqual(scanned_targets, [("VT-WED02", "2026-11-18", "2026-11-22")])
        job = await self.job_repo.get_job(job_id)
        self.assertEqual(job["status"], "SUCCESS")
        self.assertIn("Bỏ qua 1 lớp đã tick đủ BCNB", job["message"])
        env = json.loads(job["results"])
        self.assertEqual(env["scope"], "current_week")
        self.assertEqual(env["skipped_completed_count"], 1)
        self.assertEqual(env["skipped_completed_keys"], ["2026-W47|VT-MON01|1|2026-11-16"])
        self.assertEqual([it["scan_target"]["class_code"] for it in env["items"]], ["VT-WED02"])
        self.assertEqual(
            {it["scan_target"]["class_code"] for it in env["today_items"]},
            {"VT-WED02", "VT-SUN04"},
        )

        # Previous evidence for VT-MON01 is preserved in build_today_overview!
        fake_container = MagicMock()
        fake_container.settings.bcnb_dry_run = True
        fake_container.settings.zalo_plan_max_age_minutes = 30
        fake_container.group_repo = self.group_repo
        ov = await build_today_overview(
            fake_container, target_date_iso="2026-11-22", scope="current_week", now=now_sun
        )
        by_code = {it["class_code"]: it for it in ov["items"]}
        self.assertEqual(
            by_code["VT-MON01"]["screenshot_url"],
            f"/api/evidence/{prev_job_id}/mon_prev.png",
        )
        self.assertEqual(
            by_code["VT-WED02"]["screenshot_url"],
            f"/api/evidence/{job_id}/VT-WED02.png",
        )

        # Selected target keys override skip_completed_bcnb=True
        scanned_targets.clear()
        sel_job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {
                "scope": "current_week",
                "skip_completed_bcnb": True,
                "selected_target_keys": ["2026-W47|VT-MON01|1|2026-11-16"],
            },
        )
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
                sel_job_id,
                {
                    "scope": "current_week",
                    "skip_completed_bcnb": True,
                    "selected_target_keys": ["2026-W47|VT-MON01|1|2026-11-16"],
                    "check_only": False,
                    "tick_lms": True,
                    "refresh_discovery_if_stale": False,
                },
                "dry-run",
                runtime=runtime,
                external=gateways,
                job_repo=self.job_repo,
                group_repo=self.group_repo,
                audit_repo=self.audit_repo,
                evidence_repo=self.evidence_repo,
                batch_repo=self.batch_repo,
                now_override=now_sun,
            )
        self.assertEqual(scanned_targets, [("VT-MON01", "2026-11-16", "2026-11-22")])

    async def test_run_today_realtime_sync_all_completed_skips_cleanly_without_opening_browser(
        self,
    ) -> None:
        snap = {
            "version": 4,
            "reliable": True,
            "discovered_groups": [
                {"TenNhom": "MindX - VT-MON01 (18:00-20:00)", "MaLop": "VT-MON01", "CohortKey": "K1"},
                {"TenNhom": "MindX - VT-TUE02 (18:00-20:00)", "MaLop": "VT-TUE02", "CohortKey": "K2"},
            ],
            "source_group_names": [
                "MindX - VT-MON01 (18:00-20:00)",
                "MindX - VT-TUE02 (18:00-20:00)",
            ],
            "expected_count": 2,
            "bcnb_week_plan": {
                "latest_week": {"week": 47, "year": 2026},
                "required_week": {"week": 46, "year": 2026},
                "selected_weeks": [{"week": 46, "year": 2026}, {"week": 47, "year": 2026}],
                "required_data": [],
                "latest_data": [
                    {
                        "ma_lop": "VT-MON01",
                        "buoi_so": "1",
                        "ngay_hoc": "16/11/2026",
                        "ngay_hoc_iso": "2026-11-16",
                        "giao_vien": "GV Mon",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-47-mon",
                        "nx_zalo_checked": True,
                        "nx_lms_checked": True,
                    },
                    {
                        "ma_lop": "VT-TUE02",
                        "buoi_so": "2",
                        "ngay_hoc": "17/11/2026",
                        "ngay_hoc_iso": "2026-11-17",
                        "giao_vien": "GV Tue",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-47-tue",
                        "nx_zalo_checked": True,
                        "nx_lms_checked": True,
                    },
                ],
            },
            "scan_targets": [
                {
                    "group_name": "MindX - VT-MON01 (18:00-20:00)",
                    "person": "GV Mon",
                    "class_code": "VT-MON01",
                    "lesson": "1",
                    "class_date": "16/11/2026",
                    "class_date_iso": "2026-11-16",
                    "week": 47,
                    "year": 2026,
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-47-mon",
                    "nx_zalo_checked": True,
                    "nx_lms_checked": True,
                },
                {
                    "group_name": "MindX - VT-TUE02 (18:00-20:00)",
                    "person": "GV Tue",
                    "class_code": "VT-TUE02",
                    "lesson": "2",
                    "class_date": "17/11/2026",
                    "class_date_iso": "2026-11-17",
                    "week": 47,
                    "year": 2026,
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-47-tue",
                    "nx_zalo_checked": True,
                    "nx_lms_checked": True,
                },
            ],
            "deferred_targets": [],
            "exempt_targets": [],
        }
        await self.group_repo.apply_discovery_snapshot(snap)
        job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"scope": "current_week", "skip_completed_bcnb": True, "check_only": False},
        )
        browser_gw = MagicMock()
        browser_gw.persistent_session.side_effect = AssertionError("must not open browser")
        gateways = ExternalGateways(
            browser=browser_gw,
            lms_ocr=MagicMock(),
            student_ocr_factory=MagicMock,
            lms_quality=MagicMock(),
        )
        runtime = WorkerRuntime(external=gateways, evidence_root=self.evidence_root)
        await run_today_realtime_sync(
            job_id,
            {
                "scope": "current_week",
                "skip_completed_bcnb": True,
                "check_only": False,
                "refresh_discovery_if_stale": False,
            },
            "dry-run",
            runtime=runtime,
            external=gateways,
            job_repo=self.job_repo,
            group_repo=self.group_repo,
            audit_repo=self.audit_repo,
            evidence_repo=self.evidence_repo,
            batch_repo=self.batch_repo,
            now_override=datetime(2026, 11, 22, 15, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        job = await self.job_repo.get_job(job_id)
        self.assertEqual(job["status"], "SUCCESS")
        self.assertEqual(
            job["message"],
            "Tất cả 2 lớp đều đã tick đủ NX Zalo & NX LMS trên BCNB (bỏ qua quét lại).",
        )
        env = json.loads(job["results"])
        self.assertEqual(env["items"], [])
        self.assertEqual(env["today_items"], [])
        self.assertEqual(env["skipped_completed_count"], 2)
        self.assertEqual(len(env["skipped_completed_keys"]), 2)
        browser_gw.persistent_session.assert_not_called()

    async def test_build_today_overview_preserves_newer_snapshot_bcnb_flags_and_notes_over_older_overlay(
        self,
    ) -> None:
        now_sun = datetime(2026, 11, 22, 12, 0, tzinfo=BUSINESS_TIMEZONE)
        # Job #10 at 10:05 recorded nx_zalo_checked=True, nx_lms_checked=False, old notes + screenshot
        old_job_id = await self.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            {"scope": "current_week", "check_only": False, "execution_mode": "dry-run"},
        )
        await self.job_repo.update_job(old_job_id, status="RUNNING", progress=10)
        await self.job_repo.update_job(
            old_job_id,
            status="SUCCESS",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "scope": "current_week",
                    "scope_week": {"week": 47, "year": 2026},
                    "check_only": False,
                    "today_items": [
                        {
                            "group": "MindX - VT-MON01 (18:00-20:00)",
                            "status": "✅ Đã báo cáo",
                            "evidence_state": "SUBMITTED_VERIFIED",
                            "nx_zalo_checked": True,
                            "nx_lms_checked": False,
                            "qc_issue_note": "[LMS] Chưa NX (3/5)",
                            "instructor_note": "GV ghi chú cũ",
                            "lms_comments": {"status": "MISSING", "text": "Chưa NX 3/5"},
                            "scan_target": {
                                "week": 47,
                                "year": 2026,
                                "class_code": "VT-MON01",
                                "lesson": "1",
                                "class_date_iso": "2026-11-16",
                                "bcnb_session_id": "sess-47-mon",
                                "nx_zalo_checked": True,
                                "nx_lms_checked": False,
                                "qc_issue_note": "[LMS] Chưa NX (3/5)",
                                "instructor_note": "GV ghi chú cũ",
                            },
                            "evidence": {
                                "images": {
                                    "proof": {"path": f"/api/evidence/{old_job_id}/mon_old.png", "sha256": "OLD"}
                                }
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
        # Snapshot #2 at 11:00 observed nx_lms_checked=True and cleared qc_issue_note/instructor_note
        snap = {
            "version": 4,
            "reliable": True,
            "discovered_groups": [
                {"TenNhom": "MindX - VT-MON01 (18:00-20:00)", "MaLop": "VT-MON01", "CohortKey": "VT-MON01|K1"},
            ],
            "source_group_names": ["MindX - VT-MON01 (18:00-20:00)"],
            "expected_count": 1,
            "bcnb_week_plan": {
                "latest_week": {"week": 47, "year": 2026},
                "required_week": {"week": 46, "year": 2026},
                "selected_weeks": [{"week": 46, "year": 2026}, {"week": 47, "year": 2026}],
                "required_data": [],
                "latest_data": [
                    {
                        "ma_lop": "VT-MON01",
                        "buoi_so": "1",
                        "ngay_hoc": "16/11/2026",
                        "ngay_hoc_iso": "2026-11-16",
                        "giao_vien": "GV Mon",
                        "khoi": "Coding",
                        "bcnb_session_id": "sess-47-mon",
                        "nx_zalo_checked": True,
                        "nx_lms_checked": True,
                        "qc_issue_note": "",
                        "instructor_note": "",
                    }
                ],
            },
            "scan_targets": [
                {
                    "group_name": "MindX - VT-MON01 (18:00-20:00)",
                    "person": "GV Mon",
                    "class_code": "VT-MON01",
                    "lesson": "1",
                    "class_date": "16/11/2026",
                    "class_date_iso": "2026-11-16",
                    "week": 47,
                    "year": 2026,
                    "khoi": "Coding",
                    "bcnb_session_id": "sess-47-mon",
                    "nx_zalo_checked": True,
                    "nx_lms_checked": True,
                    "qc_issue_note": "",
                    "instructor_note": "",
                }
            ],
            "deferred_targets": [],
            "exempt_targets": [],
        }
        await self.group_repo.apply_discovery_snapshot(snap)
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE scan_jobs SET created_at = ?, started_at = ?, finished_at = ? WHERE id = ?",
                ("2026-11-22T03:05:00Z", "2026-11-22T03:05:00Z", "2026-11-22T03:05:00Z", old_job_id),
            )
            await db.execute(
                "UPDATE discovery_snapshots SET observed_at = ?",
                ("2026-11-22T04:00:00Z",),
            )
            await db.commit()

        fake_container = MagicMock()
        fake_container.settings.bcnb_dry_run = True
        fake_container.settings.zalo_plan_max_age_minutes = 60
        fake_container.group_repo = self.group_repo

        ov = await build_today_overview(
            fake_container,
            target_date_iso="2026-11-22",
            scope="current_week",
            now=now_sun,
        )
        self.assertEqual(len(ov["items"]), 1)
        item = ov["items"][0]
        self.assertIs(item["nx_zalo_checked"], True)
        self.assertIs(item["nx_lms_checked"], True)
        self.assertIs(item["bcnb_completed_both"], True)
        self.assertEqual(item["qc_issue_note"], "")
        self.assertEqual(item["instructor_note"], "")
        self.assertEqual(item["screenshot_url"], f"/api/evidence/{old_job_id}/mon_old.png")
        self.assertEqual(ov["kpis"]["bcnb_pending"]["completed_both_count"], 1)
        self.assertEqual(ov["kpis"]["bcnb_pending"]["total_pending_rows"], 0)

        # Direct unit check of _merge_entry_with_overlay on both merged & scan_target
        snap_dt = datetime(2026, 11, 22, 11, 0, tzinfo=BUSINESS_TIMEZONE)
        base_entry = {
            "week": 47,
            "year": 2026,
            "class_code": "VT-MON01",
            "lesson": "1",
            "class_date_iso": "2026-11-16",
            "bcnb_session_id": "sess-47-mon",
            "nx_zalo_checked": True,
            "nx_lms_checked": True,
            "qc_issue_note": "",
            "instructor_note": "",
        }
        older_overlay = {
            "_overlay_observed_at": "2026-11-22T10:05:00+07:00",
            "nx_zalo_checked": True,
            "nx_lms_checked": False,
            "qc_issue_note": "[LMS] Chưa NX (3/5)",
            "instructor_note": "Cũ",
            "screenshot_path": "/api/evidence/10/mon.png",
            "scan_target": {
                "week": 47,
                "year": 2026,
                "class_code": "VT-MON01",
                "lesson": "1",
                "class_date_iso": "2026-11-16",
                "bcnb_session_id": "sess-47-mon",
                "nx_zalo_checked": True,
                "nx_lms_checked": False,
                "qc_issue_note": "[LMS] Chưa NX (3/5)",
                "instructor_note": "Cũ",
            },
        }
        merged_older = _merge_entry_with_overlay(
            base_entry, older_overlay, snapshot_observed_at=snap_dt
        )
        self.assertIs(merged_older["nx_lms_checked"], True)
        self.assertIs(merged_older["scan_target"]["nx_lms_checked"], True)
        self.assertEqual(merged_older["qc_issue_note"], "")
        self.assertEqual(merged_older["scan_target"]["qc_issue_note"], "")
        self.assertEqual(merged_older["screenshot_path"], "/api/evidence/10/mon.png")

    async def test_defer_all_new_checker_notes_preserves_changed_count_and_composite_family_without_erasing(
        self,
    ) -> None:
        now_dt = datetime(2026, 11, 22, 18, 0, tzinfo=BUSINESS_TIMEZONE)
        today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        page = MagicMock()

        def _make_overdue_row(code: str, lesson: str, sid: str, note: str) -> dict:
            row = _make_fake_bcnb_row(
                class_code=code,
                lesson=lesson,
                date_str="16/11/2026",
                date_iso="2026-11-16",
                session_id=sid,
                zalo_deadline="20:00 17/11/2026\nhết hạn",
                lms_deadline="20:00 17/11/2026\nhết hạn",
            )
            row["qc_issue_note"] = note
            return row

        def _make_res(code: str, lesson: str, sid: str, live_issues: list[str]) -> dict:
            return {
                "group": f"MindX - {code} (18:00-20:00)",
                "status": "✅ Đã báo cáo",
                "evidence_state": "SUBMITTED_VERIFIED",
                "submitted_for_bcnb": True,
                "delay_days": 0,
                "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5},
                "lms_comments": {"status": "MISSING" if live_issues else "COMPLETE", "submitted_count": 5},
                "lms_spck": {"status": "COMPLETE", "submitted_count": 5, "missing_comment_count": 0},
                "lms_quality_issues": list(live_issues),
                "zalo_content_issues": [],
                "quality_issues": list(live_issues),
                "scan_target": {
                    "week": 47,
                    "year": 2026,
                    "class_code": code,
                    "lesson": lesson,
                    "class_date": "16/11/2026",
                    "class_date_iso": "2026-11-16",
                    "bcnb_session_id": sid,
                    "lms_attendance": {"status": "VERIFIED", "unverified_count": 0, "attended_count": 5},
                    "lms_comments": {"status": "MISSING" if live_issues else "COMPLETE", "submitted_count": 5},
                    "lms_spck": {"status": "COMPLETE", "submitted_count": 5, "missing_comment_count": 0},
                },
            }

        # 1) (3/5) -> (2/5) with manual note; 2) SPCK counts; 3) composite; 4) legacy summary; 5) fixed; 6) distinct issue
        res_lms_cnt = _make_res("VT-LMSCNT", "1", "sess-fam-1", ["[LMS] Chưa NX (2/5)"])
        res_spck_cnt = _make_res(
            "VT-SPCKCNT",
            "12",
            "sess-fam-2",
            ["[SPCK] Chưa NX (1/5)", "[SPCK] Chưa nộp (0/4)"],
        )
        res_comp = _make_res(
            "VT-LMSCOMP",
            "2",
            "sess-fam-3",
            ["[LMS] Điền tóm tắt bài, viết kỹ NX"],
        )
        res_legacy = _make_res("VT-LEGACY1", "3", "sess-fam-4", ["[LMS] Điền tóm tắt bài"])
        res_fixed = _make_res("VT-FIXED2", "4", "sess-fam-5", [])
        res_distinct = _make_res(
            "VT-DIST1",
            "5",
            "sess-fam-6",
            ["[LMS] Chưa điểm danh"],
        )

        specs = [
            ("VT-LMSCNT", "1", "sess-fam-1", ["[LMS] Chưa NX (3/5)"], "QC tay | [LMS] Chưa NX (3/5)"),
            (
                "VT-SPCKCNT",
                "12",
                "sess-fam-2",
                ["[SPCK] Chưa NX (3/5)", "[SPCK] Chưa nộp (0/5)"],
                "[SPCK] Chưa NX (3/5); [SPCK] Chưa nộp (0/5)",
            ),
            ("VT-LMSCOMP", "2", "sess-fam-3", ["[LMS] Điền tóm tắt bài"], "[LMS] Điền tóm tắt bài"),
            (
                "VT-LEGACY1",
                "3",
                "sess-fam-4",
                ["[LMS] Chưa ghi nội dung bài học"],
                "[LMS] Chưa ghi nội dung bài học",
            ),
            ("VT-FIXED2", "4", "sess-fam-5", ["[LMS] Chưa NX (3/5)"], "[LMS] Chưa NX (3/5)"),
            ("VT-DIST1", "5", "sess-fam-6", ["[LMS] Chưa NX (3/5)"], "[LMS] Chưa NX (3/5)"),
        ]
        seed_job_id = await self.job_repo.create_job("ZALO_SCAN", {"execution_mode": "production"})
        rows_w47 = []
        for code, lesson, sid, proven_list, existing_cell in specs:
            rows_w47.append(_make_overdue_row(code, lesson, sid, existing_cell))
            arow, _ = await self.audit_repo.record_intent(
                action_key=f"seed-fam-{code}",
                job_id=seed_job_id,
                action_type="BCNB_UPDATE_CHECKER_NOTE",
                target_type="CLASS",
                target_id=code,
                mode="production",
                before_state={"qc_issue_note": ""},
                intended_state={"qc_issue_note": existing_cell},
                reason={
                    "week": 47,
                    "year": 2026,
                    "lesson": lesson,
                    "class_date": "2026-11-16",
                    "bcnb_session_id": sid,
                    "auto_issues": proven_list,
                    "merged_note": existing_cell,
                },
            )
            await self.audit_repo.complete_action(
                arow["id"],
                outcome="VERIFIED",
                after_state={"qc_issue_note": existing_cell, "post_verified": True},
            )

        note_mock = AsyncMock(return_value="VERIFIED")
        tick_mock = AsyncMock(return_value="VERIFIED")
        all_results = [res_lms_cnt, res_spck_cnt, res_comp, res_legacy, res_fixed, res_distinct]
        scan_plan = {
            "latest_week": {"week": 47, "year": 2026},
            "required_week": {"week": 46, "year": 2026},
            "selected_weeks": [{"week": 46, "year": 2026}, {"week": 47, "year": 2026}],
            "targets": [r["scan_target"] for r in all_results],
        }
        lms_slots = {code: [{}] for code, *_ in specs}
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(side_effect=lambda _p, *, week, **_kw: rows_w47 if int(week) == 47 else []),
            ),
            patch("services.zalo_bcnb_service.resolve_report_columns", AsyncMock(return_value=(3, 4, 5))),
            patch("services.zalo_bcnb_service.resolve_lms_columns", AsyncMock(return_value=(0, 6))),
            patch("services.zalo_bcnb_service.execute_verified_checkbox_action", tick_mock),
            patch("services.zalo_bcnb_service.execute_verified_checker_note_action", note_mock),
            patch(
                "services.zalo_bcnb_service.fetch_and_evaluate_lms_quality",
                AsyncMock(return_value=lms_slots),
            ),
            patch("services.zalo_bcnb_service.inspect_and_attach_quality_issues_async", AsyncMock()),
        ):
            await update_bcnb_reports(
                page,
                job_id=958,
                execution_mode="production",
                scan_results=all_results,
                today=today,
                now=now_dt,
                scan_plan=scan_plan,
                job_repo=self.job_repo,
                audit_repo=self.audit_repo,
                tick_lms=False,
                check_only=False,
                defer_unexpired_checker_notes=True,
                defer_all_new_checker_notes=True,
            )

        note_calls = {c.kwargs["class_code"]: c.kwargs for c in note_mock.await_args_list}
        for preserved_code, preserved_res in (
            ("VT-LMSCNT", res_lms_cnt),
            ("VT-SPCKCNT", res_spck_cnt),
            ("VT-LMSCOMP", res_comp),
            ("VT-LEGACY1", res_legacy),
        ):
            self.assertNotIn(preserved_code, note_calls)
            self.assertEqual(preserved_res.get("checker_note_outcome"), "DEFERRED_FOR_FULL_SCAN")
        # Fixed (3/5) -> [] and fixed (3/5) -> distinct [LMS] Chưa điểm danh both clear obsolete (3/5)
        self.assertEqual(note_calls["VT-FIXED2"]["auto_issues"], [])
        self.assertEqual(note_calls["VT-FIXED2"]["reason"]["merged_note"], "")
        self.assertEqual(note_calls["VT-DIST1"]["auto_issues"], [])
        self.assertEqual(note_calls["VT-DIST1"]["reason"]["merged_note"], "")

    async def test_persist_batch_write_outcomes_never_passes_invalid_skipped_to_complete_write(
        self,
    ) -> None:
        job_id = await self.job_repo.create_job("TODAY_REALTIME_SYNC", {"scope": "current_week"})
        t1 = {
            "week": 47,
            "year": 2026,
            "class_code": "VT-NFV01",
            "lesson": "1",
            "class_date_iso": "2026-11-16",
            "group_name": "MindX - VT-NFV01",
        }
        t2 = {
            "week": 47,
            "year": 2026,
            "class_code": "VT-SKP02",
            "lesson": "2",
            "class_date_iso": "2026-11-17",
            "group_name": "MindX - VT-SKP02",
        }
        await self.batch_repo.seed_targets(job_id, [t1, t2])
        s1 = await self.batch_repo.claim_scan(job_id, "worker-1")
        s2 = await self.batch_repo.claim_scan(job_id, "worker-1")
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)
        await self.batch_repo.complete_scan(
            s1["id"],
            status="VERIFIED",
            result={"scan_target": t1, "evidence_state": "NOT_FOUND_VERIFIED"},
            write_ready=True,
        )
        await self.batch_repo.complete_scan(
            s2["id"],
            status="VERIFIED",
            result={"scan_target": t2, "evidence_state": "SUBMITTED_VERIFIED"},
            write_ready=True,
        )
        c1 = await self.batch_repo.claim_write(job_id, "worker-1")
        c2 = await self.batch_repo.claim_write(job_id, "worker-1")
        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)

        await _persist_batch_write_outcomes(
            job_id=job_id,
            execution_mode="dry-run",
            batch_repo=self.batch_repo,
            results=[
                {"scan_target": t1, "evidence_state": "NOT_FOUND_VERIFIED"},
                {"scan_target": t2, "evidence_state": "SUBMITTED_VERIFIED"},
            ],
            bcnb_summary={
                "target_outcomes": [
                    {**t2, "status": "SKIPPED", "error": "Skipped"},
                ]
            },
            claimed_rows=[c1, c2],
        )
        rows = await self.batch_repo.list_for_job(job_id)
        by_code = {r["class_code"]: r for r in rows}
        self.assertEqual(by_code["VT-NFV01"]["write_status"], "FAILED_BEFORE_WRITE")
        self.assertEqual(by_code["VT-NFV01"]["error_code"], "TARGET_NOT_UPDATED")
        self.assertEqual(by_code["VT-SKP02"]["write_status"], "FAILED_BEFORE_WRITE")

    def test_collect_today_entries_bcnb_row_key_uses_session_id_and_blocks_duplicate_key_leak(
        self,
    ) -> None:
        base_week_plan = {
            "latest_week": {"week": 47, "year": 2026},
            "required_week": {"week": 46, "year": 2026},
            "selected_weeks": [{"week": 46, "year": 2026}, {"week": 47, "year": 2026}],
            "required_data": [],
            "latest_data": [
                {
                    "ma_lop": "VT-DUP01",
                    "buoi_so": "1",
                    "ngay_hoc": "16/11/2026",
                    "ngay_hoc_iso": "2026-11-16",
                    "giao_vien": "GV Dup",
                    "bcnb_session_id": "sess-dup-a",
                    "nx_zalo_checked": True,
                    "nx_lms_checked": True,
                },
                {
                    "ma_lop": "VT-DUP01",
                    "buoi_so": "1",
                    "ngay_hoc": "16/11/2026",
                    "ngay_hoc_iso": "2026-11-16",
                    "giao_vien": "GV Dup",
                    "bcnb_session_id": "sess-dup-b",
                    "nx_zalo_checked": False,
                    "nx_lms_checked": False,
                },
            ],
        }
        snap_matching_b = {
            "plan": {
                **base_week_plan,
                "bcnb_week_plan": base_week_plan,
                "scan_targets": [
                    {
                        "group_name": "MindX - VT-DUP01 (18:00-20:00)",
                        "class_code": "VT-DUP01",
                        "lesson": "1",
                        "class_date_iso": "2026-11-16",
                        "week": 47,
                        "year": 2026,
                        "bcnb_session_id": "sess-dup-b",
                    }
                ],
            }
        }
        entries_b, _ = _collect_today_entries_from_snapshot(
            snap_matching_b, target_date_iso="2026-11-16", scope="current_week"
        )
        self.assertEqual(len(entries_b), 1)
        self.assertIs(entries_b[0]["nx_zalo_checked"], False)
        self.assertIs(entries_b[0]["nx_lms_checked"], False)

        # Duplicate kt with missing bcnb_session_id on target must NOT inherit True flags from sess-dup-a
        snap_no_sid = {
            "plan": {
                **base_week_plan,
                "bcnb_week_plan": base_week_plan,
                "scan_targets": [
                    {
                        "group_name": "MindX - VT-DUP01 (18:00-20:00)",
                        "class_code": "VT-DUP01",
                        "lesson": "1",
                        "class_date_iso": "2026-11-16",
                        "week": 47,
                        "year": 2026,
                        "bcnb_session_id": "",
                    }
                ],
            }
        }
        entries_no_sid, _ = _collect_today_entries_from_snapshot(
            snap_no_sid, target_date_iso="2026-11-16", scope="current_week"
        )
        self.assertEqual(len(entries_no_sid), 1)
        self.assertNotIn("nx_zalo_checked", entries_no_sid[0])
        self.assertNotIn("nx_lms_checked", entries_no_sid[0])

        # Unique kt without target bcnb_session_id STILL enriches when neither side conflicts
        snap_unique = {
            "plan": {
                "bcnb_week_plan": {
                    **base_week_plan,
                    "latest_data": [base_week_plan["latest_data"][0]],
                },
                "scan_targets": [
                    {
                        "group_name": "MindX - VT-DUP01 (18:00-20:00)",
                        "class_code": "VT-DUP01",
                        "lesson": "1",
                        "class_date_iso": "2026-11-16",
                        "week": 47,
                        "year": 2026,
                        "bcnb_session_id": "",
                    }
                ],
            }
        }
        entries_uniq, _ = _collect_today_entries_from_snapshot(
            snap_unique, target_date_iso="2026-11-16", scope="current_week"
        )
        self.assertIs(entries_uniq[0]["nx_zalo_checked"], True)
        self.assertIs(entries_uniq[0]["nx_lms_checked"], True)
