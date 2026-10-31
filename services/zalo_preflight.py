"""Read-only checks before admitting a Zalo/BCNB scan job.

The scan worker still performs its own final checks. This module only gives the
Owner a small, safe explanation before pressing the run button; it never opens
a browser, writes BCNB, or creates a job. Production backup probing may create
and remove one disposable local SQLite copy.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from core.time_utils import now_utc
from db.backup import backup_database
from services.observability import ready_status
from services.report_status import validate_two_week_plan


GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"


def effective_scan_worker_count(settings: Any, execution_mode: str) -> int:
    """Keep production on one Zalo page per profile.

    Dry-run keeps the configured parallelism.  Production uses one page until
    separate browser profiles have been proven safe against Zalo's single-tab
    session rule.
    """

    configured = max(1, int(getattr(settings, "zalo_scan_concurrency", 2)))
    return 1 if execution_mode == "production" else configured


def _check(code: str, status: str, message: str, action: str = "") -> dict[str, str]:
    return {
        "code": code,
        "status": status,
        "message": message,
        "action": action,
    }


def _status(checks: list[dict[str, str]]) -> str:
    if any(item["status"] == RED for item in checks):
        return RED
    if any(item["status"] == YELLOW for item in checks):
        return YELLOW
    return GREEN


def _profile_conflict(runtime: Any) -> tuple[str, str, str] | None:
    lock = getattr(runtime, "profile_lock", None)
    path = getattr(lock, "path", None)
    if path is None:
        return None
    path = Path(path)
    if not path.exists() or bool(getattr(lock, "held", False)):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = payload.get("pid")
    except (OSError, TypeError, ValueError):
        pid = None
    if isinstance(pid, int) and pid > 0 and os.name == "nt":
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is not None:
            handle = windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                windll.kernel32.CloseHandle(handle)
                return (
                    RED,
                    "ZALO_PROFILE_LOCKED",
                    "Profile Zalo đang được tiến trình khác sử dụng.",
                )
    return (
        YELLOW,
        "ZALO_PROFILE_LOCK_STALE",
        "Có lock cũ của profile Zalo; worker sẽ chỉ thu hồi nếu tiến trình cũ đã dừng.",
    )


async def _check_production_backup(settings: Any, runtime: Any) -> dict[str, str]:
    """Verify a disposable online backup can be created before production."""
    source_value = getattr(runtime, "database_path", None) or getattr(
        settings, "database_path", None
    )
    root_value = getattr(runtime, "db_backup_root", None) or getattr(
        settings, "db_backup_root", None
    )
    if not source_value or not root_value:
        return _check(
            "DATABASE_BACKUP",
            RED,
            "Chưa cấu hình được nơi tạo backup SQLite.",
            "Kiểm tra cấu hình database và thư mục backup.",
        )
    source = Path(str(source_value)).expanduser()
    root = Path(str(root_value)).expanduser()
    if not source.is_file():
        return _check(
            "DATABASE_BACKUP",
            RED,
            "Không tìm thấy database để backup trước production.",
            "Khởi động lại ứng dụng rồi kiểm tra database.",
        )
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".zalo-preflight-", dir=str(root)
        ) as probe_dir:
            artifact = await asyncio.to_thread(
                backup_database,
                source,
                Path(probe_dir) / "probe.sqlite3",
            )
        if str(artifact.integrity).lower() != "ok":
            raise RuntimeError("integrity check không đạt")
    except Exception:
        return _check(
            "DATABASE_BACKUP",
            RED,
            "Không tạo hoặc kiểm chứng được backup SQLite trước production.",
            "Kiểm tra quyền ghi, dung lượng ổ đĩa và integrity của database.",
        )
    return _check(
        "DATABASE_BACKUP",
        GREEN,
        "Có thể tạo và kiểm chứng backup SQLite trước production.",
    )


def _plan_snapshot(
    snapshot: dict[str, Any] | None, max_age_minutes: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    validation = validate_two_week_plan(
        snapshot,
        max_age_minutes=max_age_minutes,
        now=now_utc(),
    )
    dropped_rows = validation.get("dropped_rows") or {}
    if not validation["valid"]:
        red_check: dict[str, Any] = _check(
            str(validation["error_code"] or "SCAN_PLAN_INCOMPLETE"),
            RED,
            str(validation["message"]),
            str(validation["remediation"]),
        )
        if int((dropped_rows.get("counts") or {}).get("total") or 0) > 0:
            red_check["details"] = dropped_rows
        return None, red_check, dropped_rows
    return (
        {
            "snapshot_id": validation["snapshot_id"],
            "observed_at": validation["observed_at"],
            "latest_week": validation["latest_week"],
            "required_week": validation["required_week"],
            "selected_weeks": validation["selected_weeks"],
            "target_count": validation["target_count"],
            "deferred_target_count": validation["deferred_target_count"],
            "exempt_target_count": validation["exempt_target_count"],
            "candidate_target_count": validation["candidate_target_count"],
            "exempt_targets": validation["exempt_targets"],
            "dropped_rows": dropped_rows,
        },
        None,
        dropped_rows,
    )


async def assess_zalo_scan_preflight(
    container: Any,
    *,
    snapshot_id: int | None = None,
) -> dict[str, Any]:
    """Return a sanitized GREEN/YELLOW/RED scan admission report."""

    settings = getattr(container, "settings", None)
    dry_run = bool(getattr(settings, "bcnb_dry_run", True))
    execution_mode = "dry-run" if dry_run else "production"
    checks: list[dict[str, Any]] = []

    snapshot = (
        await container.group_repo.get_discovery_snapshot(snapshot_id)
        if snapshot_id is not None
        else await container.group_repo.get_latest_discovery_snapshot()
    )
    plan, plan_check, dropped_rows = _plan_snapshot(
        snapshot,
        int(getattr(settings, "zalo_plan_max_age_minutes", 30)),
    )
    checks.append(
        plan_check
        or _check(
            "SCAN_PLAN_READY",
            GREEN,
            "Kế hoạch hai tuần và target hợp lệ.",
        )
    )
    dropped_counts = dropped_rows.get("counts") or {}
    dropped_total = int(dropped_counts.get("total") or 0)
    if dropped_total > 0:
        dropped_check: dict[str, Any] = _check(
            "SCAN_PLAN_DROPPED_ROWS",
            YELLOW,
            (
                f"Có {dropped_total} dòng BCNB bị loại khỏi lượt quét tự động "
                "(chuyển khóa/thiếu nhóm/mơ hồ/ngày lỗi/rỗng/trùng)."
            ),
            "Kiểm tra danh sách dòng bị loại trước khi chạy.",
        )
        dropped_check["details"] = dropped_rows
        checks.append(dropped_check)

    job_repo = getattr(container, "job_repo", None)
    active_job = None
    if job_repo is not None and hasattr(job_repo, "get_latest_job"):
        active_job = await job_repo.get_latest_job(active_only=True)
    if isinstance(active_job, dict) and active_job.get("status") in {"PENDING", "RUNNING"}:
        checks.append(
            _check(
                "ACTIVE_JOB_PRESENT",
                RED,
                "Đang có một job khác hoạt động.",
                "Chờ job hiện tại kết thúc hoặc hủy an toàn.",
            )
        )
    else:
        checks.append(_check("NO_ACTIVE_JOB", GREEN, "Không có job khác đang chạy."))

    audit_repo = getattr(container, "audit_repo", None)
    if audit_repo is not None and hasattr(audit_repo, "count_unresolved"):
        try:
            unresolved_audits = int(await audit_repo.count_unresolved())
        except Exception:
            checks.append(
                _check(
                    "AUDIT_STATUS_UNAVAILABLE",
                    RED,
                    "Không đọc được trạng thái các thao tác bên ngoài chưa hoàn tất.",
                    "Mở Nhật ký và kiểm tra database/audit trước khi chạy lại.",
                )
            )
        else:
            if unresolved_audits:
                checks.append(
                    _check(
                        "AUDIT_PENDING",
                        RED,
                        f"Còn {unresolved_audits} thao tác bên ngoài chưa được đối soát.",
                        "Đọc lại audit và xử lý trạng thái UNKNOWN trước khi chạy.",
                    )
                )
            else:
                checks.append(_check("AUDIT_CLEAR", GREEN, "Không có audit đang treo."))

    readiness = getattr(container, "production_readiness", None)
    if readiness is not None:
        if not bool(getattr(readiness, "ready", False)):
            blockers = ", ".join(getattr(readiness, "blockers", ()))
            checks.append(
                _check(
                    "PRODUCTION_CONFIGURATION",
                    RED,
                    "Cấu hình production chưa đủ an toàn.",
                    blockers or "Mở phần cấu hình production để xử lý.",
                )
            )
        else:
            checks.append(_check("PRODUCTION_CONFIGURATION", GREEN, "Cấu hình production đã qua kiểm tra."))

    runtime = getattr(container, "worker_runtime", None)
    if runtime is not None and settings is not None:
        db_path = Path(str(getattr(settings, "database_path", ""))).expanduser()
        if not db_path.is_file():
            checks.append(_check("DATABASE_FILE", RED, "Không tìm thấy database đang dùng.", "Khởi động lại ứng dụng và kiểm tra database."))
        else:
            checks.append(_check("DATABASE_FILE", GREEN, "Database đang tồn tại."))
        conflict = _profile_conflict(runtime)
        if conflict is None:
            checks.append(_check("ZALO_PROFILE_LOCK", GREEN, "Không thấy profile Zalo bị tiến trình khác giữ."))
        else:
            checks.append(_check(conflict[1], conflict[0], conflict[2], "Đóng phiên Zalo/ứng dụng cũ rồi kiểm tra lại."))
        if not dry_run:
            checks.append(await _check_production_backup(settings, runtime))
            checks.append(
                _check(
                    "ZALO_SESSION_RUNTIME_CHECK",
                    YELLOW,
                    "Phiên đăng nhập và popup một-tab sẽ được kiểm tra khi worker mở Zalo.",
                    "Nếu Zalo hiện popup phiên khác, đóng tab ngoài worker rồi chạy lại.",
                )
            )
        configured_workers = max(1, int(getattr(settings, "zalo_scan_concurrency", 2)))
        if not dry_run and configured_workers > 1:
            checks.append(
                _check(
                    "PRODUCTION_SINGLE_ZALO_PAGE",
                    YELLOW,
                    "Production tự dùng một worker Zalo để tránh xung đột hai tab cùng profile.",
                    "Chỉ tăng worker sau khi đã cấu hình profile riêng và kiểm thử độc lập.",
                )
            )

        try:
            ready = await asyncio.to_thread(
                ready_status,
                settings=settings,
                dispatcher=getattr(container, "dispatcher", None),
                instance_id=str(getattr(container, "instance_id", "preflight")),
            )
        except Exception:
            ready = {"ready": False}
        if ready.get("ready"):
            checks.append(_check("APPLICATION_READY", GREEN, "Ứng dụng, database và thư mục cần thiết sẵn sàng."))
        else:
            checks.append(_check("APPLICATION_READY", RED, "Ứng dụng chưa sẵn sàng để chạy.", "Mở Nhật ký và xử lý readiness trước."))

    result_status = _status(checks)
    return {
        "status": result_status,
        "run_allowed": result_status != RED,
        "requires_confirmation": result_status == YELLOW,
        "mode": execution_mode,
        "checked_at": now_utc().isoformat(timespec="seconds"),
        "worker_count": effective_scan_worker_count(settings, execution_mode),
        "checks": checks,
        "plan": plan,
        "snapshot_id": plan.get("snapshot_id") if plan else None,
        "target_count": plan.get("target_count", 0) if plan else 0,
        "deferred_target_count": plan.get("deferred_target_count", 0) if plan else 0,
        "exempt_target_count": plan.get("exempt_target_count", 0) if plan else 0,
        "dropped_rows": (
            plan.get("dropped_rows")
            if plan
            else (dropped_rows if dropped_total > 0 else None)
        ),
    }
