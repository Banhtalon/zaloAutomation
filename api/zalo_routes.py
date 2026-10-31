import re

from fastapi import APIRouter, Body, Depends, Query, Response

from api.dependencies import enqueue_job, get_container, require_operator, require_viewer
from api.errors import public_error
from api.errors import internal_error
from core.auth import AuthUser
from core.container import AppContainer
from core.time_utils import now_business
from core.time_utils import now_utc, parse_timestamp
from repositories.report_repository import (
    WeeklySummaryError,
    build_weekly_summary_csv,
    filter_failed_weekly_summary_rows,
)
from repositories.zalo_batch_repository import canonical_target_key
from repositories.job_repository import JobStateError, TERMINAL_STATUSES
from services.report_email_service import (
    ReportEmailError,
    send_failed_classes_report_email,
)
from services.report_status import validate_two_week_plan
from services.zalo_bcnb_service import (
    collect_result_item_evidence_paths,
    find_manual_approve_target_matches,
    is_result_item_already_submitted,
    parse_scan_job_results_envelope,
)
from services.zalo_preflight import (
    assess_zalo_scan_preflight,
    effective_scan_worker_count,
)


router = APIRouter()


def _snapshot_is_stale(snapshot: dict, max_age_minutes: int) -> bool:
    observed = parse_timestamp(snapshot.get("observed_at"))
    if observed is None:
        return True
    return (now_utc() - observed).total_seconds() > max(1, max_age_minutes) * 60


def _enforce_scan_preflight(
    preflight: dict,
    *,
    dry_run: bool,
    confirm_yellow: bool,
) -> None:
    """Turn the latest admission report into a fail-closed route decision."""
    status = str(preflight.get("status") or "RED")
    checks = preflight.get("checks") or []
    if status == "RED":
        blocker = next(
            (item for item in checks if item.get("status") == "RED"),
            None,
        )
        if blocker and (
            str(blocker.get("code", "")).startswith("SCAN_PLAN_")
            or blocker.get("code") == "SCAN_TARGETS_DEFERRED"
        ):
            raise public_error(409, blocker["code"], blocker["message"])
        raise public_error(
            409,
            "SCAN_PREFLIGHT_BLOCKED",
            (blocker or {}).get(
                "message",
                "Chưa thể chạy vì kiểm tra an toàn chưa đạt.",
            ),
        )
    if status == "YELLOW" and not dry_run and not confirm_yellow:
        raise public_error(
            409,
            "SCAN_PREFLIGHT_CONFIRMATION_REQUIRED",
            "Có cảnh báo an toàn trước khi chạy production; hãy đọc và xác nhận rồi thử lại.",
        )


@router.post("/auto-discover", dependencies=[Depends(require_operator)])
async def auto_discover(container: AppContainer = Depends(get_container)):
    job_id = await container.job_repo.create_job(
        "ZALO_DISCOVERY",
        {"version": 4},
        retryable=True,
        recoverable=True,
        mutates_external=False,
        message="Đang chờ dò tìm nhóm...",
    )
    await enqueue_job(container, job_id)
    return {"message": "Đang bắt đầu dò tìm nhóm.", "job_id": job_id}


@router.post("/run-scan", dependencies=[Depends(require_operator)])
async def run_scan(
    container: AppContainer = Depends(get_container),
    snapshot_id: int | None = Body(default=None, embed=True),
    confirm_yellow: bool = Body(default=False, embed=True),
    selected_target_keys: list[str] | None = Body(default=None, embed=True),
):
    # Direct unit calls do not pass through FastAPI's dependency/body parser;
    # normalize its ``Body(...)`` sentinel values before using them.
    if not isinstance(snapshot_id, int) or isinstance(snapshot_id, bool):
        snapshot_id = None
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
        seen_selected: set[str] = set()
        for raw_key in selected_target_keys:
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise public_error(
                    422,
                    "SCAN_SELECTED_TARGETS_INVALID",
                    "Danh sách target chọn lọc có phần tử rỗng hoặc sai kiểu.",
                )
            clean_key = raw_key.strip()
            if clean_key in seen_selected:
                raise public_error(
                    422,
                    "SCAN_SELECTED_TARGETS_DUPLICATE",
                    "Danh sách target chọn lọc bị trùng lặp.",
                )
            seen_selected.add(clean_key)
            normalized_selected_keys.append(clean_key)
        if not normalized_selected_keys:
            raise public_error(
                422,
                "SCAN_SELECTED_TARGETS_EMPTY",
                "Danh sách target chọn lọc không được rỗng.",
            )
    dry_run = container.settings.bcnb_dry_run
    execution_mode = "dry-run" if dry_run else "production"
    preflight = await assess_zalo_scan_preflight(
        container,
        snapshot_id=snapshot_id,
    )
    _enforce_scan_preflight(
        preflight,
        dry_run=dry_run,
        confirm_yellow=confirm_yellow,
    )
    raw_pf_id = preflight.get("snapshot_id") or (preflight.get("plan") or {}).get(
        "snapshot_id"
    )
    pinned_snapshot_id = (
        snapshot_id
        if snapshot_id is not None
        else (
            raw_pf_id
            if isinstance(raw_pf_id, int) and not isinstance(raw_pf_id, bool)
            else None
        )
    )

    async def _ensure_snapshot_not_drifted(
        expected_id: int | None,
        observed_pf: dict | None = None,
    ) -> None:
        if expected_id is None:
            return
        pf_id = (
            observed_pf.get("snapshot_id")
            or (observed_pf.get("plan") or {}).get("snapshot_id")
            if isinstance(observed_pf, dict)
            else None
        )
        latest_snap = (
            await container.group_repo.get_latest_discovery_snapshot()
            if snapshot_id is None
            else None
        )
        if (
            (pf_id is not None and pf_id != expected_id)
            or (
                snapshot_id is None
                and (
                    latest_snap is None
                    or latest_snap.get("snapshot_id") != expected_id
                )
            )
        ):
            raise public_error(
                409,
                "SNAPSHOT_CHANGED",
                "Kế hoạch dò tìm đã thay đổi trong lúc chuẩn bị quét; hãy kiểm tra lại rồi bấm Quét.",
            )

    await _ensure_snapshot_not_drifted(pinned_snapshot_id)
    snapshot = (
        await container.group_repo.get_discovery_snapshot(pinned_snapshot_id)
        if pinned_snapshot_id is not None
        else await container.group_repo.get_latest_discovery_snapshot()
    )
    max_age_minutes = int(
        getattr(container.settings, "zalo_plan_max_age_minutes", 30)
    )
    validation = validate_two_week_plan(
        snapshot,
        max_age_minutes=max_age_minutes,
        now=now_utc(),
        business_now=now_business(),
    )
    if not validation["valid"]:
        err_code = str(validation["error_code"] or "SCAN_PLAN_INCOMPLETE")
        if err_code != "SCAN_TARGETS_DEFERRED":
            raise public_error(409, err_code, str(validation["message"]))
    if isinstance(snapshot, dict) and _snapshot_is_stale(snapshot, max_age_minutes):
        raise public_error(
            409,
            "SCAN_PLAN_STALE",
            "Kế hoạch BCNB đã quá hạn; hãy bấm Dò tìm lại trước khi quét.",
        )
    target_keys = list(validation["target_keys"])
    if normalized_selected_keys is not None:
        admitted_key_set = set(target_keys)
        if any(key not in admitted_key_set for key in normalized_selected_keys):
            raise public_error(
                409,
                "SCAN_SELECTED_TARGET_NOT_IN_PLAN",
                "Target chọn lọc không thuộc kế hoạch tuần BCNB hiện tại; hãy Dò tìm lại.",
            )
    selected_weeks = validation["selected_weeks"]
    scan_targets = list(validation["scan_targets"])
    deferred_targets = list(validation["deferred_targets"])
    exempt_targets = list(validation.get("exempt_targets") or [])
    if normalized_selected_keys is not None:
        selected_key_set = set(normalized_selected_keys)
        scan_targets = [
            target
            for target in scan_targets
            if canonical_target_key(target) in selected_key_set
        ]
        deferred_targets = [
            target
            for target in deferred_targets
            if canonical_target_key(target) in selected_key_set
        ]
        exempt_targets = [
            target
            for target in exempt_targets
            if canonical_target_key(target) in selected_key_set
        ]
    if not scan_targets and not exempt_targets:
        raise public_error(
            409,
            "SCAN_TARGETS_DEFERRED",
            "Chưa có lớp đủ giờ học để quét. Các lớp hôm nay sẽ tự khả dụng sau giờ kết thúc buổi học.",
        )
    assert isinstance(snapshot, dict)
    effective_snapshot_id = (
        pinned_snapshot_id
        if pinned_snapshot_id is not None
        else snapshot.get("snapshot_id")
    )
    latest_preflight = await assess_zalo_scan_preflight(
        container,
        snapshot_id=effective_snapshot_id,
    )
    await _ensure_snapshot_not_drifted(effective_snapshot_id, latest_preflight)
    _enforce_scan_preflight(
        latest_preflight,
        dry_run=dry_run,
        confirm_yellow=confirm_yellow,
    )
    scan_plan = {
        "snapshot_id": snapshot["snapshot_id"],
        "observed_at": snapshot.get("observed_at"),
        "expires_at": validation["expires_at"],
        "latest_week": validation["latest_week"],
        "required_week": validation["required_week"],
        "selected_weeks": selected_weeks,
        "targets": scan_targets,
        "deferred_targets": deferred_targets,
        "exempt_targets": exempt_targets,
        "dropped_rows": validation.get("dropped_rows"),
    }
    if normalized_selected_keys is not None:
        scan_plan["selective_rescan"] = True
        scan_plan["selected_target_keys"] = [
            canonical_target_key(target)
            for target in [*scan_targets, *exempt_targets]
        ]
    try:
        job_id = await container.job_repo.create_job(
            "ZALO_SCAN",
            {
                "phase": "ZALO_SCAN",
                "reason_code": "QUEUED",
                "attempted_target_count": 0,
                "version": 5,
                "dry_run": dry_run,
                "execution_mode": execution_mode,
                "scan_plan": scan_plan,
            },
            retryable=dry_run,
            recoverable=dry_run,
            mutates_external=not dry_run,
            message="Đang chờ quét Zalo/BCNB...",
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
        "message": "Bắt đầu quét trong nền...",
        "job_id": job_id,
        "dry_run": dry_run,
        "target_count": len(scan_targets),
        "exempt_target_count": len(exempt_targets),
        "worker_count": int(
            effective_scan_worker_count(container.settings, execution_mode)
        ),
        "deferred_target_count": len(deferred_targets),
        "selective_rescan": normalized_selected_keys is not None,
    }


@router.post("/api/zalo-manual-approve", dependencies=[Depends(require_operator)])
async def manual_approve_zalo_report(
    container: AppContainer = Depends(get_container),
    user: AuthUser = Depends(require_operator),
    job_id: int | None = Body(default=None, embed=True),
    group_name: str | None = Body(default=None, embed=True),
    class_code: str | None = Body(default=None, embed=True),
    week: int | None = Body(default=None, embed=True),
    year: int | None = Body(default=None, embed=True),
    lesson: str | None = Body(default=None, embed=True),
    class_date_iso: str | None = Body(default=None, embed=True),
    evidence_path: str | None = Body(default=None, embed=True),
):
    """Queue a single-target audited BCNB report checkbox tick backed by job evidence."""
    if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
        raise public_error(
            422,
            "MANUAL_APPROVE_INVALID_INPUT",
            "Mã job gốc không hợp lệ.",
        )
    if not isinstance(group_name, str) or not group_name.strip():
        raise public_error(
            422,
            "MANUAL_APPROVE_INVALID_INPUT",
            "Tên nhóm Zalo không được để trống.",
        )
    if not isinstance(evidence_path, str) or not evidence_path.strip():
        raise public_error(
            422,
            "MANUAL_APPROVE_EVIDENCE_REQUIRED",
            "Bắt buộc có đường dẫn ảnh bằng chứng hợp lệ để duyệt tay.",
        )
    if (
        not isinstance(week, int)
        or isinstance(week, bool)
        or not isinstance(year, int)
        or isinstance(year, bool)
        or not (1 <= week <= 53)
        or not (2000 < year <= 2100)
    ):
        raise public_error(
            422,
            "MANUAL_APPROVE_INVALID_TARGET",
            "Tuần hoặc năm BCNB không hợp lệ.",
        )
    if not isinstance(lesson, str) or not lesson.strip():
        raise public_error(
            422,
            "MANUAL_APPROVE_INVALID_TARGET",
            "Buổi học BCNB không hợp lệ.",
        )
    if (
        not isinstance(class_date_iso, str)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", class_date_iso.strip())
    ):
        raise public_error(
            422,
            "MANUAL_APPROVE_INVALID_TARGET",
            "Ngày học ISO (YYYY-MM-DD) không hợp lệ.",
        )
    normalized_class_code: str | None = None
    if isinstance(class_code, str) and class_code.strip():
        normalized_class_code = class_code.strip().upper()
    elif class_code is not None and not hasattr(class_code, "default"):
        raise public_error(
            422,
            "MANUAL_APPROVE_INVALID_TARGET",
            "Mã lớp không hợp lệ.",
        )

    source_job = await container.job_repo.get_job(job_id)
    if source_job is None:
        raise public_error(
            404,
            "SOURCE_JOB_NOT_FOUND",
            "Không tìm thấy job quét Zalo gốc.",
        )
    if source_job.get("job_type") != "ZALO_SCAN":
        raise public_error(
            409,
            "MANUAL_APPROVE_INVALID_SOURCE_JOB",
            "Job gốc phải là job quét Zalo (ZALO_SCAN).",
        )
    if source_job.get("status") not in TERMINAL_STATUSES:
        raise public_error(
            409,
            "SOURCE_JOB_NOT_TERMINAL",
            "Job quét Zalo gốc chưa hoàn tất; hãy chờ job kết thúc trước khi duyệt tay.",
        )

    audit_repo = getattr(container, "audit_repo", None)
    if audit_repo is not None and hasattr(audit_repo, "count_unresolved"):
        if int(await audit_repo.count_unresolved()) > 0:
            raise public_error(
                409,
                "AUDIT_PENDING",
                "Còn thao tác bên ngoài chưa được đối soát; hãy kiểm tra audit trước khi duyệt tay.",
            )

    _, items = parse_scan_job_results_envelope(source_job.get("results"))
    matches = find_manual_approve_target_matches(
        items,
        group_name=group_name.strip(),
        week=week,
        year=year,
        lesson=lesson.strip(),
        class_date_iso=class_date_iso.strip(),
        class_code=normalized_class_code,
    )
    if not matches:
        raise public_error(
            409,
            "MANUAL_APPROVE_TARGET_NOT_IN_JOB",
            "Không tìm thấy buổi học tương ứng trong kết quả của job gốc.",
        )
    if len(matches) > 1:
        raise public_error(
            409,
            "MANUAL_APPROVE_AMBIGUOUS_TARGET",
            "Dòng kết quả trong job gốc bị trùng; dừng để tránh duyệt nhầm.",
        )
    _, matched_item, resolved_class_code = matches[0]
    try:
        target_key = canonical_target_key(
            {
                "week": week,
                "year": year,
                "class_code": resolved_class_code,
                "lesson": lesson.strip(),
                "class_date_iso": class_date_iso.strip(),
            }
        )
    except ValueError as exc:
        raise public_error(
            422,
            "MANUAL_APPROVE_INVALID_TARGET",
            "Thông tin định danh target BCNB không hợp lệ.",
        ) from exc

    if is_result_item_already_submitted(matched_item):
        raise public_error(
            409,
            "MANUAL_APPROVE_ALREADY_SUBMITTED",
            "Lớp này đã được tích hoặc miễn báo cáo trên BCNB.",
        )

    clean_evidence_path = evidence_path.strip()
    if clean_evidence_path not in collect_result_item_evidence_paths(matched_item):
        raise public_error(
            409,
            "MANUAL_APPROVE_EVIDENCE_NOT_IN_TARGET",
            "Ảnh bằng chứng không thuộc dòng kết quả của lớp được chọn.",
        )

    verified_artifact = await container.evidence_repo.verify(
        clean_evidence_path,
        job_id=job_id,
    )
    if (
        not verified_artifact
        or verified_artifact.get("status") != "AVAILABLE"
        or not verified_artifact.get("sha256")
    ):
        raise public_error(
            409,
            "MANUAL_APPROVE_EVIDENCE_UNAVAILABLE",
            "Ảnh bằng chứng không khả dụng hoặc không thuộc job gốc.",
        )
    evidence_sha256 = str(verified_artifact["sha256"]).strip().upper()
    dry_run = container.settings.bcnb_dry_run
    execution_mode = "dry-run" if dry_run else "production"
    approved_by = (
        str(getattr(user, "username", None) or "operator").strip() or "operator"
    )
    target_payload = {
        "group_name": group_name.strip(),
        "class_code": resolved_class_code,
        "week": week,
        "year": year,
        "lesson": lesson.strip(),
        "class_date_iso": class_date_iso.strip(),
        "target_key": target_key,
    }
    try:
        manual_job_id = await container.job_repo.create_job(
            "ZALO_MANUAL_APPROVE",
            {
                "version": 1,
                "source_job_id": job_id,
                "target": target_payload,
                "evidence_path": clean_evidence_path,
                "evidence_sha256": evidence_sha256,
                "approved_by": approved_by,
                "dry_run": dry_run,
                "execution_mode": execution_mode,
            },
            retryable=dry_run,
            recoverable=dry_run,
            mutates_external=not dry_run,
            message=f"Đang chờ duyệt & tích BCNB cho {resolved_class_code}...",
            reject_if_active=True,
        )
    except JobStateError as exc:
        raise public_error(
            409,
            "ACTIVE_JOB_PRESENT",
            "Một job khác vừa được bắt đầu; hãy chờ job đó kết thúc rồi thử lại.",
        ) from exc
    await enqueue_job(container, manual_job_id)
    return {
        "message": f"Đang duyệt & tích BCNB cho lớp {resolved_class_code}...",
        "job_id": manual_job_id,
        "source_job_id": job_id,
        "target_key": target_key,
        "dry_run": dry_run,
    }


@router.get(
    "/api/zalo-scan-preflight",
    dependencies=[Depends(require_viewer)],
)
async def get_zalo_scan_preflight(
    container: AppContainer = Depends(get_container),
    snapshot_id: int | None = Query(default=None),
):
    """Return a sanitized, read-only admission report for the dashboard."""

    return await assess_zalo_scan_preflight(container, snapshot_id=snapshot_id)


@router.get("/api/zalo-scan-plan", dependencies=[Depends(require_viewer)])
async def get_zalo_scan_plan(container: AppContainer = Depends(get_container)):
    snapshot = await container.group_repo.get_latest_discovery_snapshot()
    if snapshot is None:
        return {"available": False, "plan": None}
    max_age_minutes = int(
        getattr(container.settings, "zalo_plan_max_age_minutes", 30)
    )
    validation = validate_two_week_plan(
        snapshot,
        max_age_minutes=max_age_minutes,
        now=now_utc(),
        business_now=now_business(),
    )
    available = bool(
        validation["valid"]
        and not _snapshot_is_stale(snapshot, max_age_minutes)
    )
    return {
        "available": available,
        "reason_code": validation["error_code"] if not available else None,
        "snapshot_id": validation["snapshot_id"],
        "status": validation["status"],
        "observed_at": validation["observed_at"],
        "latest_week": validation["latest_week"],
        "required_week": validation["required_week"],
        "selected_weeks": validation["selected_weeks"],
        "target_count": validation["target_count"],
        "deferred_target_count": validation["deferred_target_count"],
        "exempt_target_count": validation["exempt_target_count"],
        "candidate_target_count": validation["candidate_target_count"],
        "dropped_rows": validation["dropped_rows"],
    }


BCNB_ZALO_GAP_STATUSES = {
    "MISSING_ZALO_GROUP",
    "AMBIGUOUS_ZALO_GROUP",
    "TRANSITION_REVIEW",
}


@router.get(
    "/api/discovery/bcnb-zalo-gaps",
    dependencies=[Depends(require_viewer)],
)
async def get_bcnb_zalo_gaps(
    week: int | None = None,
    year: int | None = None,
    status: str | None = None,
    container: AppContainer = Depends(get_container),
):
    """Read BCNB-to-Zalo gaps from the latest immutable discovery snapshot."""
    if week is not None and not 1 <= week <= 53:
        raise public_error(422, "INVALID_WEEK", "Tuần phải nằm trong khoảng 1-53.")
    if year is not None and not 2000 <= year <= 2100:
        raise public_error(422, "INVALID_YEAR", "Năm BCNB không hợp lệ.")
    normalized_status = status.strip().upper() if status else None
    if normalized_status and normalized_status not in BCNB_ZALO_GAP_STATUSES:
        raise public_error(
            422,
            "INVALID_GAP_STATUS",
            "Trạng thái đối chiếu BCNB/Zalo không hợp lệ.",
        )

    snapshot = await container.group_repo.get_latest_discovery_snapshot()
    if snapshot is None:
        return {
            "available": False,
            "reason": "NO_DISCOVERY_SNAPSHOT",
            "stale": True,
            "summary": {},
            "items": [],
            "filtered_count": 0,
        }
    plan = snapshot.get("plan") or {}
    try:
        plan_version = int(plan.get("plan_version", 0))
    except (TypeError, ValueError):
        plan_version = 0
    coverage = plan.get("bcnb_zalo_coverage")
    if plan_version < 4 or not isinstance(coverage, dict):
        return {
            "available": False,
            "reason": "DISCOVERY_PLAN_UPGRADE_REQUIRED",
            "snapshot_id": snapshot.get("snapshot_id"),
            "observed_at": snapshot.get("observed_at"),
            "stale": True,
            "summary": {},
            "items": [],
            "filtered_count": 0,
        }

    items = [
        item
        for item in coverage.get("records", [])
        if isinstance(item, dict) and item.get("status") in BCNB_ZALO_GAP_STATUSES
    ]
    if normalized_status:
        items = [item for item in items if item.get("status") == normalized_status]
    if week is not None or year is not None:
        items = [
            item
            for item in items
            if any(
                isinstance(period, dict)
                and (week is None or period.get("week") == week)
                and (year is None or period.get("year") == year)
                for period in item.get("periods", [])
            )
        ]
    items.sort(key=lambda item: (str(item.get("class_code", "")), item["status"]))
    return {
        "available": True,
        "reason": None,
        "snapshot_id": snapshot.get("snapshot_id"),
        "observed_at": snapshot.get("observed_at"),
        "stale": _snapshot_is_stale(
            snapshot,
            int(getattr(container.settings, "zalo_plan_max_age_minutes", 30)),
        ),
        "summary": {
            "source_count": int(coverage.get("source_count", 0)),
            "matched_count": int(coverage.get("matched_count", 0)),
            "missing_count": int(coverage.get("missing_count", 0)),
            "ambiguous_count": int(coverage.get("ambiguous_count", 0)),
            "transition_review_count": int(
                coverage.get("transition_review_count", 0)
            ),
        },
        "items": items,
        "filtered_count": len(items),
    }


@router.get("/api/groups", dependencies=[Depends(require_viewer)])
async def get_groups(container: AppContainer = Depends(get_container)):
    try:
        # The UI's "active" table must use the same eligibility boundary as
        # the Zalo worker. Historical/transitioning rows remain available via
        # the ignored/lifecycle views, but must not appear as active targets.
        return {"groups": await container.group_repo.get_scan_groups()}
    except Exception as exc:
        raise internal_error(exc, event="api.groups.read_failed") from exc


@router.get("/api/class-lifecycles", dependencies=[Depends(require_viewer)])
async def get_class_lifecycles(
    include_archived: bool = False,
    container: AppContainer = Depends(get_container),
):
    try:
        return {
            "classes": await container.group_repo.get_class_lifecycles(
                include_archived=include_archived
            )
        }
    except Exception as exc:
        raise internal_error(exc, event="api.class_lifecycles.read_failed") from exc


def _normalize_weekly_summary_params(
    week: int | None,
    year: int | None,
    job_id: int | None,
) -> tuple[int | None, int | None, int | None]:
    if hasattr(week, "default"):
        week = None
    elif week is not None and (
        isinstance(week, bool) or not isinstance(week, int) or not (1 <= week <= 53)
    ):
        raise public_error(
            422,
            "INVALID_PERIOD",
            "Tham số tuần (week) phải từ 1 đến 53.",
        )

    if hasattr(year, "default"):
        year = None
    elif year is not None and (
        isinstance(year, bool) or not isinstance(year, int) or not (2000 <= year <= 2100)
    ):
        raise public_error(
            422,
            "INVALID_PERIOD",
            "Tham số năm (year) phải từ 2000 đến 2100.",
        )

    if (week is None) != (year is None):
        raise public_error(
            422,
            "INVALID_PERIOD",
            "Hãy cung cấp đồng thời cả tuần (week) và năm (year).",
        )

    if hasattr(job_id, "default"):
        job_id = None
    elif job_id is not None and (
        isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0
    ):
        raise public_error(
            422,
            "INVALID_JOB_ID",
            "Mã job quét không hợp lệ.",
        )
    return week, year, job_id


@router.get("/api/reports/weekly-summary", dependencies=[Depends(require_viewer)])
async def get_weekly_summary(
    week: int | None = Query(default=None, ge=1, le=53),
    year: int | None = Query(default=None, ge=2000, le=2100),
    job_id: int | None = Query(default=None, ge=1),
    format: str | None = Query(default=None),
    failed_only: bool = Query(default=False),
    execution_mode: str | None = Query(default=None),
    include_dry_run: bool = Query(default=False),
    container: AppContainer = Depends(get_container),
):
    week, year, job_id = _normalize_weekly_summary_params(week, year, job_id)

    if hasattr(format, "default"):
        format = None
    fmt = str(format or "").strip().lower()
    if fmt not in {"", "json", "csv"}:
        raise public_error(
            422,
            "INVALID_REPORT_FORMAT",
            "Định dạng báo cáo không hợp lệ (chỉ hỗ trợ json hoặc csv).",
        )
    failed_only_flag = False if hasattr(failed_only, "default") else bool(failed_only)
    exec_mode_param = None if hasattr(execution_mode, "default") else execution_mode
    include_dry_run_flag = (
        False if hasattr(include_dry_run, "default") else bool(include_dry_run)
    )

    try:
        payload = await container.report_repo.get_weekly_summary(
            week=week,
            year=year,
            job_id=job_id,
            execution_mode=exec_mode_param,
            include_dry_run=include_dry_run_flag,
        )
    except WeeklySummaryError as exc:
        raise public_error(exc.status_code, exc.code, str(exc)) from exc
    except Exception as exc:
        raise internal_error(exc, event="api.reports.weekly_summary_failed") from exc

    rows_for_output = payload.get("rows") or []
    if failed_only_flag:
        rows_for_output = filter_failed_weekly_summary_rows(rows_for_output)

    if fmt == "csv":
        is_dry_run_csv = bool(
            payload.get("is_dry_run")
            or payload.get("execution_mode") == "dry-run"
            or payload.get("execution_mode_badge") == "DRY-RUN"
        )
        csv_text = build_weekly_summary_csv(
            rows_for_output,
            is_dry_run=is_dry_run_csv,
            execution_mode_badge=payload.get("execution_mode_badge"),
        )
        sel_w = payload.get("week")
        sel_y = payload.get("year")
        prefix = "Bao_Cao_Lop_Loi_Tuan" if failed_only_flag else "Bao_Cao_Tuan"
        mode_suffix = "_DRY_RUN" if is_dry_run_csv else ""
        filename = (
            f"{prefix}_W{int(sel_w):02d}_{int(sel_y)}{mode_suffix}.csv"
            if sel_w is not None and sel_y is not None
            else f"{prefix}{mode_suffix}.csv"
        )
        resp_headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Execution-Mode": "dry-run" if is_dry_run_csv else "production",
        }
        if is_dry_run_csv:
            resp_headers["X-Execution-Mode-Badge"] = "DRY-RUN"
        return Response(
            content=csv_text.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers=resp_headers,
        )
    if failed_only_flag:
        return {
            **payload,
            "rows": rows_for_output,
            "failed_only": True,
        }
    return payload


@router.post(
    "/api/reports/send-failed-csv-email",
    dependencies=[Depends(require_operator)],
)
async def send_failed_csv_email_report(
    week: int | None = Body(default=None, embed=True),
    year: int | None = Body(default=None, embed=True),
    job_id: int | None = Body(default=None, embed=True),
    recipients: list[str] | None = Body(default=None, embed=True),
    confirm_duplicate: bool = Body(default=False, embed=True),
    execution_mode: str | None = Body(default=None, embed=True),
    include_dry_run: bool = Body(default=False, embed=True),
    container: AppContainer = Depends(get_container),
):
    week, year, job_id = _normalize_weekly_summary_params(week, year, job_id)
    if hasattr(recipients, "default"):
        recipients = None
    confirm_duplicate_flag = (
        False if hasattr(confirm_duplicate, "default") else bool(confirm_duplicate)
    )
    exec_mode_param = None if hasattr(execution_mode, "default") else execution_mode
    include_dry_run_flag = (
        False if hasattr(include_dry_run, "default") else bool(include_dry_run)
    )

    if week is not None and year is not None:
        try:
            active_jobs = await container.report_repo.get_active_mutation_jobs_for_week(
                int(week), int(year)
            )
        except Exception as exc:
            raise internal_error(
                exc, event="api.reports.send_failed_csv_email_failed"
            ) from exc
        if active_jobs:
            active_desc = ", ".join(
                f"#{j.get('id')} ({j.get('job_type')}:{j.get('status')})"
                for j in active_jobs
            )
            err = public_error(
                409,
                "SCAN_ACTIVE_FOR_WEEK",
                f"Tuần W{int(week):02d}/{int(year)} đang có tác vụ chạy hoặc chờ chạy ({active_desc}). "
                "Vui lòng đợi tác vụ hoàn tất trước khi gửi email báo cáo.",
            )
            if isinstance(err.detail, dict):
                err.detail["active_jobs"] = active_jobs
            raise err

    try:
        payload = await container.report_repo.get_weekly_summary(
            week=week,
            year=year,
            job_id=job_id,
            execution_mode=exec_mode_param,
            include_dry_run=include_dry_run_flag,
        )
    except WeeklySummaryError as exc:
        raise public_error(exc.status_code, exc.code, str(exc)) from exc
    except Exception as exc:
        raise internal_error(exc, event="api.reports.weekly_summary_failed") from exc

    all_rows = payload.get("rows") or []
    failed_rows = filter_failed_weekly_summary_rows(all_rows)
    sel_w = payload.get("week") or week
    sel_y = payload.get("year") or year
    if sel_w is None or sel_y is None:
        raise public_error(
            409,
            "EMPTY_WEEKLY_SUMMARY",
            "Chưa có dữ liệu báo cáo trong tuần đã chọn để xuất CSV và gửi mail.",
        )

    try:
        return await send_failed_classes_report_email(
            settings=getattr(container, "settings", None),
            week=int(sel_w),
            year=int(sel_y),
            all_rows=all_rows,
            failed_rows=failed_rows,
            selected_job_id=payload.get("selected_job_id"),
            requested_recipients=recipients,
            confirm_duplicate=confirm_duplicate_flag,
            execution_mode=str(payload.get("execution_mode") or "production"),
            is_dry_run=bool(payload.get("is_dry_run", False)),
            execution_mode_badge=payload.get("execution_mode_badge"),
            audit_repo=getattr(container, "audit_repo", None),
            report_repo=getattr(container, "report_repo", None),
        )
    except ReportEmailError as exc:
        err = public_error(exc.status_code, exc.code, str(exc))
        if getattr(exc, "extra", None) and isinstance(err.detail, dict):
            err.detail.update(exc.extra)
        raise err from exc
    except Exception as exc:
        raise internal_error(exc, event="api.reports.send_failed_csv_email_failed") from exc
