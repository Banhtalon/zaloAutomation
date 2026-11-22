"""HTTP endpoints for the dedicated 'Lớp Hôm Nay (Live)' page."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from api.dependencies import enqueue_job, get_container, require_operator, require_viewer
from api.errors import public_error
from core.container import AppContainer
from repositories.job_repository import JobStateError
from services.today_ops_service import (
    build_today_overview,
    parse_target_date_iso,
    parse_today_scope,
)
from services.zalo_preflight import assess_zalo_scan_preflight


router = APIRouter(tags=["today"])

_REFRESHABLE_PLAN_RED_CODES = frozenset(
    {
        "SCAN_PLAN_MISSING",
        "SCAN_PLAN_STALE",
        "SCAN_PLAN_INCOMPLETE",
        "SCAN_PLAN_EMPTY",
        "SCAN_TARGETS_DEFERRED",
    }
)


def _enforce_today_sync_preflight(
    preflight: dict,
    *,
    dry_run: bool,
    confirm_yellow: bool,
    refresh_discovery_if_stale: bool,
) -> None:
    """Enforce fail-closed preflight safety while allowing live refresh when opted in."""
    checks = preflight.get("checks") or []
    ignored_red_codes = (
        _REFRESHABLE_PLAN_RED_CODES
        if refresh_discovery_if_stale
        else frozenset()
    )
    blocker = next(
        (
            item
            for item in checks
            if isinstance(item, dict)
            and item.get("status") == "RED"
            and str(item.get("code") or "") not in ignored_red_codes
        ),
        None,
    )
    if blocker is not None:
        blocker_code = str(blocker.get("code") or "")
        if blocker_code.startswith("SCAN_PLAN_") or blocker_code == "SCAN_TARGETS_DEFERRED":
            raise public_error(409, blocker_code, str(blocker.get("message") or ""))
        raise public_error(
            409,
            "SCAN_PREFLIGHT_BLOCKED",
            str(
                blocker.get("message")
                or "Chưa thể chạy vì kiểm tra an toàn chưa đạt."
            ),
        )
    has_yellow = any(
        isinstance(item, dict) and item.get("status") == "YELLOW"
        for item in checks
    )
    if (preflight.get("status") == "YELLOW" or has_yellow) and not dry_run and not confirm_yellow:
        raise public_error(
            409,
            "SCAN_PREFLIGHT_CONFIRMATION_REQUIRED",
            "Có cảnh báo an toàn trước khi chạy production; hãy đọc và xác nhận rồi thử lại.",
        )


@router.get("/api/today/overview", dependencies=[Depends(require_viewer)])
async def get_today_overview(
    container: AppContainer = Depends(get_container),
    date: str | None = Query(default=None),
    scope: str | None = Query(default=None),
):
    raw_date = None if (date is None or hasattr(date, "default")) else str(date)
    raw_scope = None if (scope is None or hasattr(scope, "default")) else str(scope)
    try:
        resolved_date = parse_target_date_iso(raw_date)
    except ValueError as exc:
        raise public_error(
            400,
            "INVALID_TARGET_DATE",
            "Ngày tra cứu không hợp lệ (định dạng YYYY-MM-DD).",
        ) from exc
    try:
        resolved_scope = parse_today_scope(raw_scope)
    except ValueError as exc:
        raise public_error(
            400,
            "INVALID_TODAY_SCOPE",
            "Phạm vi Lớp Hôm Nay không hợp lệ (chỉ hỗ trợ 'date' hoặc 'current_week').",
        ) from exc
    if raw_scope is None:
        return await build_today_overview(container, target_date_iso=resolved_date)
    return await build_today_overview(
        container,
        target_date_iso=resolved_date,
        scope=resolved_scope,
    )


@router.post("/api/today/sync", dependencies=[Depends(require_operator)])
async def sync_today_sessions(
    container: AppContainer = Depends(get_container),
    target_date_iso: str | None = Body(default=None, embed=True),
    scope: str | None = Body(default=None, embed=True),
    skip_completed_bcnb: bool = Body(default=False, embed=True),
    check_only: bool = Body(default=False, embed=True),
    tick_lms: bool = Body(default=True, embed=True),
    refresh_discovery_if_stale: bool = Body(default=True, embed=True),
    selected_target_keys: list[str] | None = Body(default=None, embed=True),
    confirm_yellow: bool = Body(default=False, embed=True),
):
    raw_date = (
        None
        if (target_date_iso is None or hasattr(target_date_iso, "default"))
        else str(target_date_iso)
    )
    raw_scope = (
        None
        if (scope is None or hasattr(scope, "default"))
        else str(scope)
    )
    try:
        resolved_date = parse_target_date_iso(raw_date)
    except ValueError as exc:
        raise public_error(
            400,
            "INVALID_TARGET_DATE",
            "Ngày tra cứu không hợp lệ (định dạng YYYY-MM-DD).",
        ) from exc
    try:
        resolved_scope = parse_today_scope(raw_scope)
    except ValueError as exc:
        raise public_error(
            400,
            "INVALID_TODAY_SCOPE",
            "Phạm vi Lớp Hôm Nay không hợp lệ (chỉ hỗ trợ 'date' hoặc 'current_week').",
        ) from exc

    if not isinstance(skip_completed_bcnb, bool):
        skip_completed_bcnb = False
    if not isinstance(check_only, bool):
        check_only = False
    if not isinstance(tick_lms, bool):
        tick_lms = True
    if not isinstance(refresh_discovery_if_stale, bool):
        refresh_discovery_if_stale = True
    if not isinstance(confirm_yellow, bool):
        confirm_yellow = False

    if not isinstance(selected_target_keys, list):
        if selected_target_keys is not None and not hasattr(selected_target_keys, "default"):
            raise public_error(
                422,
                "SCAN_SELECTED_TARGETS_INVALID",
                "Danh sách target chọn lọc không hợp lệ.",
            )
        selected_target_keys = None

    normalized_selected_keys: list[str] | None = None
    if selected_target_keys is not None:
        normalized_selected_keys = []
        seen_keys: set[str] = set()
        for raw_key in selected_target_keys:
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise public_error(
                    422,
                    "SCAN_SELECTED_TARGETS_INVALID",
                    "Mã target chọn lọc không hợp lệ.",
                )
            clean_key = raw_key.strip()
            if clean_key not in seen_keys:
                seen_keys.add(clean_key)
                normalized_selected_keys.append(clean_key)
        if not normalized_selected_keys or len(normalized_selected_keys) > 200:
            raise public_error(
                422,
                "SCAN_SELECTED_TARGETS_INVALID",
                "Danh sách target chọn lọc phải có từ 1 đến 200 mục.",
            )

    dry_run = bool(container.settings.bcnb_dry_run)
    effective_tick_lms = False if check_only else tick_lms
    execution_mode = "dry-run" if (check_only or dry_run) else "production"

    if not check_only:
        preflight = await assess_zalo_scan_preflight(container)
        _enforce_today_sync_preflight(
            preflight,
            dry_run=dry_run,
            confirm_yellow=confirm_yellow,
            refresh_discovery_if_stale=refresh_discovery_if_stale,
        )

    payload = {
        "version": 1,
        "target_date_iso": resolved_date,
        "scope": resolved_scope,
        "skip_completed_bcnb": skip_completed_bcnb,
        "check_only": check_only,
        "tick_lms": effective_tick_lms,
        "refresh_discovery_if_stale": refresh_discovery_if_stale,
        "selected_target_keys": normalized_selected_keys,
        "dry_run": (check_only or dry_run),
        "execution_mode": execution_mode,
    }
    mutates_external = execution_mode == "production" and not check_only
    if resolved_scope == "current_week":
        start_msg = (
            "Đang kiểm tra Live LMS & Zalo tuần thực tế..."
            if check_only
            else "Đang quét Live & tick BCNB tuần thực tế..."
        )
    else:
        start_msg = (
            f"Đang kiểm tra Live LMS & Zalo ngày {resolved_date}..."
            if check_only
            else f"Đang quét Live & tick BCNB đúng tuần ngày {resolved_date}..."
        )
    try:
        job_id = await container.job_repo.create_job(
            "TODAY_REALTIME_SYNC",
            payload,
            retryable=(check_only or dry_run),
            recoverable=(check_only or dry_run),
            mutates_external=mutates_external,
            message=start_msg,
            reject_if_active=True,
        )
    except JobStateError as exc:
        raise public_error(
            409,
            "ACTIVE_JOB_PRESENT",
            "Một job khác vừa được bắt đầu; hãy chờ job đó kết thúc rồi thử lại.",
        ) from exc

    await enqueue_job(container, job_id)
    return {
        "message": start_msg,
        "job_id": job_id,
        "target_date_iso": resolved_date,
        "scope": resolved_scope,
        "skip_completed_bcnb": skip_completed_bcnb,
        "check_only": check_only,
        "tick_lms": effective_tick_lms,
        "dry_run": (check_only or dry_run),
        "execution_mode": execution_mode,
    }
