"""Phase 9 local release-readiness invariant gate.

This gate is deliberately local and read-only.  It proves that the checkout
starts in dry-run mode and that the production preflight is fail-closed; it
does not contact Zalo/BCNB, submit a form or perform a canary mutation.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError, version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.production_readiness import production_readiness_report  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from db.database import current_db_path, get_schema_migrations, init_db  # noqa: E402
from googleform_autosubmit.config import Config  # noqa: E402


def main() -> int:
    if Config.MODE != "dry-run" or not Config.BCNB_DRY_RUN:
        raise AssertionError(
            "Local release gate yeu cau GOOGLE_FORM_MODE=dry-run va BCNB_DRY_RUN=true."
        )

    lock_entries: dict[str, str] = {}
    for line in (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, pinned = line.split("==", 1)
        lock_entries[name.strip().lower().replace("_", "-")] = pinned.strip()
    missing: list[str] = []
    mismatched: list[tuple[str, str, str]] = []
    for name, pinned in lock_entries.items():
        try:
            installed = version(name)
        except PackageNotFoundError:
            missing.append(name)
            continue
        if installed != pinned:
            mismatched.append((name, pinned, installed))
    if missing or mismatched:
        raise AssertionError(
            f"Dependency lock khong khop: missing={missing}, mismatched={mismatched}"
        )

    report = production_readiness_report(AppSettings.from_config())
    if report.required or not report.ready or report.blockers:
        raise AssertionError(f"Dry-run production preflight khong an toan: {report.as_dict()}")

    source = (ROOT / "core" / "production_readiness.py").read_text(encoding="utf-8")
    required_markers = (
        "AUTH_MODE_WEB_REQUIRED",
        "AUTH_SECURE_COOKIE_REQUIRED",
        "PUBLIC_HOST_ALLOWLIST_REQUIRED",
        "HTTPS_ORIGIN_ALLOWLIST_REQUIRED",
        "ProductionReadinessError",
    )
    missing = [marker for marker in required_markers if marker not in source]
    if missing:
        raise AssertionError(f"Thieu production preflight markers: {missing}")

    runbook = ROOT / "PROJECT_COMPLETION_PLAN.md"
    text = runbook.read_text(encoding="utf-8")
    for marker in ("HTTPS", "canary", "quay lại", "dry-run", "Owner"):
        if marker.lower() not in text.lower():
            raise AssertionError(f"Runbook thieu marker: {marker}")

    init_db()
    migrations = [int(item["version"]) for item in get_schema_migrations()]
    if migrations != [1, 2, 3, 4]:
        raise AssertionError(f"Migration chain khong dung: {migrations}")
    with sqlite3.connect(current_db_path()) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise AssertionError(f"DB integrity/FK khong dat: {integrity}, {foreign_keys}")

    print(
        "PHASE 9 LOCAL READINESS PASS: "
        "dry_run=on, production_preflight=fail_closed, migrations=[1,2,3,4], db_integrity=ok"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
