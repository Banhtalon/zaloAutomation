"""Local health/readiness checks and aggregate-only operational metrics."""

from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
from statistics import median
from time import monotonic
import uuid

import db.database as database
from googleform_autosubmit.config import Config
_STARTED_AT = datetime.now(timezone.utc)
_STARTED_MONOTONIC = monotonic()
_INSTANCE_ID = uuid.uuid4().hex[:12]


def live_status(
    *,
    version: str | None = None,
    instance_id: str | None = None,
    started_at=None,
    started_monotonic: float | None = None,
) -> dict:
    started_at = started_at or _STARTED_AT
    started_monotonic = started_monotonic or _STARTED_MONOTONIC
    return {
        "status": "ok",
        "version": version or Config.APP_VERSION,
        "instance_id": instance_id or _INSTANCE_ID,
        "started_at": started_at.isoformat(timespec="seconds"),
        "uptime_seconds": round(monotonic() - started_monotonic, 3),
    }


def _directory_check(path_value: str, instance_id: str = _INSTANCE_ID) -> dict:
    path = Path(path_value).resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".health-{instance_id}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"ok": True, "path": str(path)}
    except OSError as exc:
        return {"ok": False, "path": str(path), "error": str(exc)[:160]}


def ready_status(
    *,
    settings=None,
    dispatcher=None,
    database_path: str | None = None,
    instance_id: str = _INSTANCE_ID,
) -> dict:
    db_path = database_path or (
        settings.database_path if settings else database.current_db_path()
    )
    upload_root = settings.upload_root if settings else Config.UPLOAD_ROOT
    log_dir = settings.log_dir if settings else Config.LOG_DIR
    min_free = (
        settings.min_free_disk_bytes if settings else Config.MIN_FREE_DISK_BYTES
    )
    checks: dict[str, dict[str, object]] = {}
    try:
        with closing(sqlite3.connect(db_path, timeout=2)) as connection:
            connection.execute("SELECT 1").fetchone()
        checks["database"] = {"ok": True}
    except (OSError, sqlite3.Error) as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:160]}

    checks["uploads"] = _directory_check(upload_root, instance_id)
    checks["logs"] = _directory_check(log_dir, instance_id)
    try:
        disk = shutil.disk_usage(Path(db_path).resolve().parent)
        checks["disk"] = {
            "ok": disk.free >= min_free,
            "free_bytes": disk.free,
            "minimum_bytes": min_free,
        }
    except OSError as exc:
        checks["disk"] = {"ok": False, "error": str(exc)[:160]}

    if dispatcher is None:
        from services.job_dispatcher import dispatcher_status

        dispatcher_state = dispatcher_status()
    else:
        dispatcher_state = dispatcher.status()
    checks["dispatcher"] = {
        "ok": not dispatcher_state["shutting_down"],
        **dispatcher_state,
    }
    ready = all(item.get("ok") for item in checks.values())
    return {
        "status": "ok" if ready else "not_ready",
        "ready": ready,
        "version": settings.app_version if settings else Config.APP_VERSION,
        "checks": checks,
        "capabilities": {
            "bcnb": (
                "dry-run"
                if (settings.bcnb_dry_run if settings else Config.BCNB_DRY_RUN)
                else "production"
            ),
            "google_form": (
                settings.google_form_mode if settings else Config.MODE
            ),
            "gemini_configured": (
                settings.gemini_configured
                if settings
                else bool(Config.GEMINI_API_KEY)
            ),
        },
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return round(ordered[index], 3)


def _parse_time(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except (TypeError, ValueError):
        return None


def _tree_size(path_value: str) -> int:
    path = Path(path_value)
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def collect_job_metrics(
    window_hours: int | None = None,
    *,
    settings=None,
    dispatcher=None,
    database_path: str | None = None,
    log_dir: str | None = None,
) -> dict:
    default_window = (
        settings.metrics_window_hours if settings else Config.METRICS_WINDOW_HOURS
    )
    window_hours = max(1, min(int(window_hours or default_window), 168))
    db_path = database_path or (
        settings.database_path if settings else database.current_db_path()
    )
    log_path = log_dir or (settings.log_dir if settings else Config.LOG_DIR)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat(
        timespec="seconds"
    )
    with closing(sqlite3.connect(db_path, timeout=3)) as connection:
        jobs = connection.execute(
            """
            SELECT job_type, status, COUNT(*)
            FROM scan_jobs
            WHERE COALESCE(created_at, started_at) >= ?
            GROUP BY job_type, status
            """,
            (cutoff,),
        ).fetchall()
        timing_rows = connection.execute(
            """
            SELECT started_at, finished_at, attempt, recovery_note
            FROM scan_jobs
            WHERE COALESCE(created_at, started_at) >= ?
            """,
            (cutoff,),
        ).fetchall()
        actions = connection.execute(
            """
            SELECT outcome, COUNT(*) FROM external_action_audit
            WHERE created_at >= ? GROUP BY outcome
            """,
            (cutoff,),
        ).fetchall()
        uploads = connection.execute(
            """
            SELECT state, COUNT(*), COALESCE(SUM(size_bytes), 0)
            FROM job_uploads WHERE created_at >= ? GROUP BY state
            """,
            (cutoff,),
        ).fetchall()

    durations = []
    for started_at, finished_at, _attempt, _note in timing_rows:
        started = _parse_time(started_at)
        finished = _parse_time(finished_at)
        if started and finished and finished >= started:
            durations.append((finished - started).total_seconds())

    by_type: dict[str, dict[str, int]] = {}
    for job_type, status, count in jobs:
        by_type.setdefault(job_type or "UNKNOWN", {})[status or "UNKNOWN"] = count
    database_size = sum(
        _tree_size(f"{db_path}{suffix}") for suffix in ("", "-wal", "-shm")
    )
    if dispatcher is None:
        from services.job_dispatcher import dispatcher_status

        dispatcher_state = dispatcher_status()
    else:
        dispatcher_state = dispatcher.status()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_hours": window_hours,
        "jobs": {
            "total": sum(count for _job_type, _status, count in jobs),
            "by_type": by_type,
            "retries": sum(1 for row in timing_rows if (row[2] or 1) > 1),
            "recovery_blocked": sum(1 for row in timing_rows if row[3]),
            "duration_seconds": {
                "median": round(median(durations), 3) if durations else None,
                "p95": _percentile(durations, 0.95),
            },
        },
        "external_actions": {outcome: count for outcome, count in actions},
        "uploads": {
            state: {"count": count, "bytes": size}
            for state, count, size in uploads
        },
        "storage": {
            "database_bytes": database_size,
            "log_bytes": _tree_size(log_path),
        },
        "dispatcher": dispatcher_state,
    }
