from datetime import timedelta

from fastapi import APIRouter, Body, Depends

from api.dependencies import enqueue_job, get_container, require_operator, require_viewer
from api.errors import public_error
from api.errors import internal_error
from core.container import AppContainer
from core.time_utils import now_business
from core.time_utils import now_utc, parse_timestamp, utc_iso
from repositories.zalo_batch_repository import canonical_target_key
from services.zalo_target_eligibility import split_scan_targets_for_execution


router = APIRouter()


def _snapshot_is_stale(snapshot: dict, max_age_minutes: int) -> bool:
    observed = parse_timestamp(snapshot.get("observed_at"))
    if observed is None:
        return True
    return (now_utc() - observed).total_seconds() > max(1, max_age_minutes) * 60


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
):
    dry_run = container.settings.bcnb_dry_run
    execution_mode = "dry-run" if dry_run else "production"
    snapshot = (
        await container.group_repo.get_discovery_snapshot(snapshot_id)
        if snapshot_id is not None
        else await container.group_repo.get_latest_discovery_snapshot()
    )
    if snapshot is None:
        raise public_error(
            409,
            "SCAN_PLAN_REQUIRED",
            "Hãy bấm Dò tìm để tạo kế hoạch hai tuần trước khi quét.",
        )
    if snapshot.get("status") not in {"OBSERVED_ONLY", "PROMOTED"}:
        raise public_error(
            409,
            "SCAN_PLAN_NOT_TRUSTED",
            "Kế hoạch tuần BCNB chưa đủ tin cậy để quét.",
        )
    plan = snapshot.get("plan") or {}
    week_plan = plan.get("bcnb_week_plan") or {}
    selected_weeks = week_plan.get("selected_weeks") or []
    candidate_targets = [
        *(plan.get("scan_targets") or []),
        *(plan.get("deferred_targets") or []),
    ]
    normalized_periods = set()
    for item in selected_weeks:
        if not isinstance(item, dict):
            continue
        try:
            normalized_periods.add((int(item["week"]), int(item["year"])))
        except (KeyError, TypeError, ValueError):
            continue
    latest_period: tuple[int, int] | None
    required_period: tuple[int, int] | None
    try:
        latest_period = (
            int(week_plan["latest_week"]["week"]),
            int(week_plan["latest_week"]["year"]),
        )
        required_period = (
            int(week_plan["required_week"]["week"]),
            int(week_plan["required_week"]["year"]),
        )
    except (KeyError, TypeError, ValueError):
        latest_period = required_period = None
    if (
        len(selected_weeks) != 2
        or len(normalized_periods) != 2
        or latest_period is None
        or required_period is None
        or normalized_periods != {latest_period, required_period}
        or latest_period == required_period
        or not candidate_targets
    ):
        raise public_error(
            409,
            "SCAN_PLAN_INCOMPLETE",
            "Kế hoạch BCNB chưa có đủ hai tuần và target quét.",
        )
    try:
        target_keys = [canonical_target_key(item) for item in candidate_targets]
    except (AttributeError, TypeError, ValueError) as exc:
        raise public_error(
            409,
            "SCAN_PLAN_INVALID_TARGET",
            "Kế hoạch BCNB có target thiếu identity bất biến; hãy Dò tìm lại.",
        ) from exc
    if len(target_keys) != len(set(target_keys)):
        raise public_error(
            409,
            "SCAN_PLAN_DUPLICATE_TARGET",
            "Kế hoạch BCNB có target trùng; hãy Dò tìm lại.",
        )
    if _snapshot_is_stale(
        snapshot,
        int(getattr(container.settings, "zalo_plan_max_age_minutes", 30)),
    ):
        raise public_error(
            409,
            "SCAN_PLAN_STALE",
            "Kế hoạch BCNB đã quá hạn; hãy bấm Dò tìm lại trước khi quét.",
        )
    observed_at = parse_timestamp(snapshot.get("observed_at"))
    if observed_at is None:
        raise public_error(
            409,
            "SCAN_PLAN_STALE",
            "Kế hoạch BCNB thiếu thời điểm quan sát hợp lệ; hãy bấm Dò tìm lại.",
        )
    expires_at = observed_at + timedelta(
        minutes=int(getattr(container.settings, "zalo_plan_max_age_minutes", 30))
    )
    scan_targets, deferred_targets = split_scan_targets_for_execution(
        candidate_targets,
        latest_week=week_plan.get("latest_week"),
        now=now_business(),
    )
    if not scan_targets:
        raise public_error(
            409,
            "SCAN_TARGETS_DEFERRED",
            "Chưa có lớp đủ giờ học để quét. Các lớp hôm nay sẽ tự khả dụng sau giờ kết thúc buổi học.",
        )
    scan_plan = {
        "snapshot_id": snapshot["snapshot_id"],
        "observed_at": snapshot.get("observed_at"),
        "expires_at": utc_iso(expires_at),
        "latest_week": week_plan.get("latest_week"),
        "required_week": week_plan.get("required_week"),
        "selected_weeks": selected_weeks,
        "targets": scan_targets,
        "deferred_targets": deferred_targets,
    }
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
    )
    await enqueue_job(container, job_id)
    return {
        "message": "Bắt đầu quét trong nền...",
        "job_id": job_id,
        "dry_run": dry_run,
        "target_count": len(scan_targets),
        "worker_count": int(
            getattr(container.settings, "zalo_scan_concurrency", 2)
        ),
        "deferred_target_count": len(deferred_targets),
    }


@router.get("/api/zalo-scan-plan", dependencies=[Depends(require_viewer)])
async def get_zalo_scan_plan(container: AppContainer = Depends(get_container)):
    snapshot = await container.group_repo.get_latest_discovery_snapshot()
    if snapshot is None:
        return {"available": False, "plan": None}
    plan = snapshot.get("plan") or {}
    week_plan = plan.get("bcnb_week_plan") or {}
    selected_weeks = week_plan.get("selected_weeks") or []
    candidate_targets = [
        *(plan.get("scan_targets") or []),
        *(plan.get("deferred_targets") or []),
    ]
    selected_periods = set()
    for item in selected_weeks:
        if isinstance(item, dict):
            try:
                selected_periods.add((int(item["week"]), int(item["year"])))
            except (KeyError, TypeError, ValueError):
                continue
    latest_period: tuple[int, int] | None
    required_period: tuple[int, int] | None
    try:
        latest_period = (
            int(week_plan["latest_week"]["week"]),
            int(week_plan["latest_week"]["year"]),
        )
        required_period = (
            int(week_plan["required_week"]["week"]),
            int(week_plan["required_week"]["year"]),
        )
    except (KeyError, TypeError, ValueError):
        latest_period = required_period = None
    scan_targets, deferred_targets = split_scan_targets_for_execution(
        candidate_targets,
        latest_week=week_plan.get("latest_week"),
        now=now_business(),
    )
    target_count = len(scan_targets)
    deferred_target_count = len(deferred_targets)
    available = (
        snapshot.get("status") in {"OBSERVED_ONLY", "PROMOTED"}
        and not _snapshot_is_stale(
            snapshot,
            int(getattr(container.settings, "zalo_plan_max_age_minutes", 30)),
        )
        and len(selected_weeks) == 2
        and latest_period is not None
        and required_period is not None
        and latest_period != required_period
        and selected_periods == {latest_period, required_period}
        and target_count > 0
    )
    return {
        "available": available,
        "snapshot_id": snapshot.get("snapshot_id"),
        "status": snapshot.get("status"),
        "observed_at": snapshot.get("observed_at"),
        "latest_week": week_plan.get("latest_week"),
        "required_week": week_plan.get("required_week"),
        "selected_weeks": selected_weeks,
        "target_count": target_count,
        "deferred_target_count": deferred_target_count,
        "candidate_target_count": len(candidate_targets),
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
