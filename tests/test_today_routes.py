from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.today_routes import get_today_overview, sync_today_sessions
from core.time_utils import now_business
from repositories.job_repository import JobStateError
from services.job_dispatcher import JobDispatcher
from services.runtime import WorkerRuntime


class _FakeSettings:
    bcnb_dry_run = True
    zalo_plan_max_age_minutes = 30
    execution_mode = "dry-run"


class _FakeJobRepo:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.next_id = 501
        self.conflict = False

    async def create_job(
        self,
        job_type: str,
        payload: dict,
        *,
        retryable: bool = False,
        recoverable: bool = False,
        mutates_external: bool = False,
        message: str = "",
        reject_if_active: bool = False,
    ) -> int:
        if self.conflict:
            raise JobStateError("Đang có một job khác hoạt động.")
        job_id = self.next_id
        self.next_id += 1
        self.created.append(
            {
                "id": job_id,
                "job_type": job_type,
                "payload": payload,
                "retryable": retryable,
                "recoverable": recoverable,
                "mutates_external": mutates_external,
                "message": message,
                "reject_if_active": reject_if_active,
            }
        )
        return job_id


class _FakeDispatcher:
    def __init__(self) -> None:
        self.scheduled: list[int] = []

    def schedule_job(self, job_id: int) -> None:
        self.scheduled.append(job_id)


def _make_container(*, dry_run: bool = True) -> MagicMock:
    container = MagicMock()
    container.settings = _FakeSettings()
    container.settings.bcnb_dry_run = dry_run
    container.job_repo = _FakeJobRepo()
    container.dispatcher = _FakeDispatcher()
    return container


class TodayRoutesUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_today_overview_validates_date_and_delegates(self) -> None:
        container = _make_container()
        with self.assertRaises(HTTPException) as bad_date:
            await get_today_overview(container=container, date="14/11/2026")
        self.assertEqual(bad_date.exception.status_code, 400)
        self.assertEqual(bad_date.exception.detail["code"], "INVALID_TARGET_DATE")

        mock_overview = {"target_date_iso": "2026-11-14", "items": [], "kpis": {}}
        with patch(
            "api.today_routes.build_today_overview",
            AsyncMock(return_value=mock_overview),
        ) as build_mock:
            resp = await get_today_overview(container=container, date="2026-11-14")
        self.assertEqual(resp, mock_overview)
        build_mock.assert_awaited_once_with(container, target_date_iso="2026-11-14")

    async def test_sync_today_check_only_bypasses_preflight_and_schedules_non_mutating_job(self) -> None:
        container = _make_container(dry_run=False)
        with patch(
            "api.today_routes.assess_zalo_scan_preflight",
            AsyncMock(),
        ) as preflight_mock:
            resp = await sync_today_sessions(
                container=container,
                target_date_iso="2026-11-14",
                check_only=True,
                tick_lms=True,
            )

        preflight_mock.assert_not_awaited()
        self.assertEqual(resp["job_id"], 501)
        self.assertTrue(resp["check_only"])
        self.assertFalse(resp["tick_lms"])
        self.assertEqual(resp["execution_mode"], "dry-run")
        self.assertEqual(container.dispatcher.scheduled, [501])
        created = container.job_repo.created[0]
        self.assertEqual(created["job_type"], "TODAY_REALTIME_SYNC")
        self.assertFalse(created["mutates_external"])
        self.assertTrue(created["reject_if_active"])

    async def test_sync_today_production_requires_yellow_confirmation_and_allows_refreshable_red(self) -> None:
        container = _make_container(dry_run=False)
        yellow_preflight = {
            "status": "YELLOW",
            "checks": [
                {"code": "SCAN_PLAN_DROPPED_ROWS", "status": "YELLOW", "message": "Có 1 dòng thiếu nhóm."}
            ],
        }
        with patch(
            "api.today_routes.assess_zalo_scan_preflight",
            AsyncMock(return_value=yellow_preflight),
        ):
            with self.assertRaises(HTTPException) as need_confirm:
                await sync_today_sessions(
                    container=container,
                    target_date_iso="2026-11-14",
                    check_only=False,
                    confirm_yellow=False,
                )
            self.assertEqual(need_confirm.exception.status_code, 409)
            self.assertEqual(
                need_confirm.exception.detail["code"],
                "SCAN_PREFLIGHT_CONFIRMATION_REQUIRED",
            )

            resp = await sync_today_sessions(
                container=container,
                target_date_iso="2026-11-14",
                check_only=False,
                confirm_yellow=True,
                tick_lms=True,
            )
            self.assertEqual(resp["execution_mode"], "production")
            self.assertTrue(container.job_repo.created[0]["mutates_external"])

        # Missing snapshot is RED, allowed when refresh_discovery_if_stale=True
        stale_red = {
            "status": "RED",
            "checks": [{"code": "SCAN_PLAN_STALE", "status": "RED", "message": "Quá hạn"}],
        }
        with patch(
            "api.today_routes.assess_zalo_scan_preflight",
            AsyncMock(return_value=stale_red),
        ):
            ok_resp = await sync_today_sessions(
                container=container,
                target_date_iso="2026-11-14",
                check_only=False,
                refresh_discovery_if_stale=True,
            )
            self.assertEqual(ok_resp["job_id"], 502)

            with self.assertRaises(HTTPException) as stale_err:
                await sync_today_sessions(
                    container=container,
                    target_date_iso="2026-11-14",
                    check_only=False,
                    refresh_discovery_if_stale=False,
                )
            self.assertEqual(stale_err.exception.status_code, 409)
            self.assertEqual(stale_err.exception.detail["code"], "SCAN_PLAN_STALE")

    async def test_sync_today_conflict_and_invalid_targets_and_default_date(self) -> None:
        container = _make_container(dry_run=True)
        with self.assertRaises(HTTPException) as bad_targets:
            await sync_today_sessions(
                container=container,
                selected_target_keys=[],
                check_only=True,
            )
        self.assertEqual(bad_targets.exception.status_code, 422)
        self.assertEqual(bad_targets.exception.detail["code"], "SCAN_SELECTED_TARGETS_INVALID")

        container.job_repo.conflict = True
        with self.assertRaises(HTTPException) as active_err:
            await sync_today_sessions(container=container, check_only=True)
        self.assertEqual(active_err.exception.status_code, 409)
        self.assertEqual(active_err.exception.detail["code"], "ACTIVE_JOB_PRESENT")

        container.job_repo.conflict = False
        default_resp = await sync_today_sessions(container=container, check_only=True)
        self.assertEqual(default_resp["target_date_iso"], now_business().date().isoformat())

    async def test_job_dispatcher_routes_today_realtime_sync(self) -> None:
        runtime = WorkerRuntime()
        dispatcher = JobDispatcher(
            job_repo=MagicMock(),
            worker_runtime=runtime,
        )
        mock_run = AsyncMock()
        job = {
            "id": 912,
            "job_type": "TODAY_REALTIME_SYNC",
            "mutates_external": False,
            "request_payload": {
                "target_date_iso": "2026-11-14",
                "check_only": True,
                "execution_mode": "dry-run",
            },
        }
        with patch("services.today_ops_service.run_today_realtime_sync", mock_run):
            await dispatcher._dispatch(job)
        mock_run.assert_awaited_once()
        args, kwargs = mock_run.await_args
        self.assertEqual(args[0], 912)
        self.assertEqual(args[1]["target_date_iso"], "2026-11-14")
        self.assertEqual(args[2], "dry-run")
        self.assertIs(kwargs["runtime"], runtime)

    async def test_sync_today_blocks_deferred_targets_red_when_refresh_disabled(self) -> None:
        container = _make_container(dry_run=False)
        deferred_red = {
            "status": "RED",
            "checks": [
                {
                    "code": "SCAN_TARGETS_DEFERRED",
                    "status": "RED",
                    "message": "Mọi lớp đều chưa kết thúc giờ học.",
                }
            ],
        }
        with patch("api.today_routes.assess_zalo_scan_preflight", AsyncMock(return_value=deferred_red)):
            with self.assertRaises(HTTPException) as err:
                await sync_today_sessions(
                    container=container,
                    target_date_iso="2026-11-14",
                    check_only=False,
                    refresh_discovery_if_stale=False,
                )
            self.assertEqual(err.exception.status_code, 409)
            self.assertEqual(err.exception.detail["code"], "SCAN_TARGETS_DEFERRED")

            ok_resp = await sync_today_sessions(
                container=container,
                target_date_iso="2026-11-14",
                check_only=False,
                refresh_discovery_if_stale=True,
            )
            self.assertEqual(ok_resp["job_id"], 501)
