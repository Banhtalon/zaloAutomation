"""Offline Phase 9A gate for security hygiene and critical-module coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.phase9a_audit import dependency_lock_check, secret_scan  # noqa: E402


CRITICAL_COVERAGE_TARGETS = {
    "core/production_readiness.py": 90.0,
    "core/auth.py": 85.0,
    "services/bcnb_safety.py": 75.0,
    "services/zalo_evidence.py": 90.0,
    "services/zalo_scan_evidence.py": 85.0,
    "services/googleform_idempotency.py": 95.0,
    "services/lms_plan.py": 90.0,
    "services/job_dispatcher.py": 80.0,
    "repositories/job_repository.py": 80.0,
    "repositories/evidence_repository.py": 70.0,
    "api/googleform_routes.py": 75.0,
    "api/zalo_routes.py": 90.0,
}


def _coverage_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AssertionError(
            f"Thieu coverage JSON: {path}. Chay coverage json truoc Phase 9A gate."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _check_critical_coverage(data: dict[str, Any]) -> list[dict[str, Any]]:
    files = {
        str(key).replace("\\", "/"): value
        for key, value in dict(data.get("files", {})).items()
    }
    failures: list[dict[str, Any]] = []
    for relative, target in CRITICAL_COVERAGE_TARGETS.items():
        file_data = files.get(relative)
        if file_data is None:
            # Coverage may emit absolute paths on Windows; resolve by suffix.
            candidates = [
                value
                for key, value in files.items()
                if key.endswith("/" + relative) or key == relative
            ]
            file_data = candidates[0] if candidates else None
        if file_data is None:
            failures.append(
                {"file": relative, "target": target, "actual": None, "reason": "missing"}
            )
            continue
        actual = float(file_data.get("summary", {}).get("percent_covered", 0.0))
        if actual < target:
            failures.append(
                {"file": relative, "target": target, "actual": round(actual, 2)}
            )
    return failures


def _check_audit_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AssertionError(f"Thieu pip-audit report: {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    audit = report.get("pip_audit", {})
    if not audit.get("ran") or not audit.get("ok"):
        raise AssertionError(f"pip-audit report khong sach: {audit}")
    return {
        "generated_at": report.get("generated_at"),
        "status": audit.get("status"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--coverage-json",
        type=Path,
        default=ROOT / "coverage-phase9a.json",
    )
    parser.add_argument("--require-audit-report", type=Path)
    args = parser.parse_args()

    lock = dependency_lock_check()
    secrets = secret_scan()
    if not lock["ok"]:
        raise AssertionError(f"Dependency lock khong khop: {lock}")
    if not secrets["ok"]:
        raise AssertionError(
            "Secret scan phat hien finding (chi xem path/line/pattern): "
            + json.dumps(secrets["findings"], ensure_ascii=False)
        )

    coverage = _coverage_report(args.coverage_json)
    coverage_failures = _check_critical_coverage(coverage)
    if coverage_failures:
        raise AssertionError(
            "Critical coverage duoi target: "
            + json.dumps(coverage_failures, ensure_ascii=False)
        )

    audit_summary = {"required": False}
    if args.require_audit_report:
        audit_summary = {"required": True, **_check_audit_report(args.require_audit_report)}
    print(
        "PHASE 9A QUALITY PASS: "
        f"lock_entries={lock['entries']}, secrets=0, "
        f"critical_modules={len(CRITICAL_COVERAGE_TARGETS)}, "
        f"pip_audit={audit_summary.get('status', 'not-required')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
