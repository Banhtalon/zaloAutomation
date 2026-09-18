"""Phase 9A release audit: dependency lock, optional CVE scan and secrets.

The scanner is intentionally conservative: it reports file/line/pattern only
and never prints the matched value.  The CVE scan is opt-in because it needs a
network advisory source; the local quality gate omits that network step, while
the release checklist requires a fresh explicit ``--pip-audit`` run.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git",
    ".backups",
    "runtime",
    "venv",
    "zalo_session",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
    "static/screenshots",
    "googleform_autosubmit/uploads",
    "temp_lms",
}
TEXT_SUFFIXES = {
    ".bat",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b")),
    (
        "url_embedded_credentials",
        re.compile(r"https?://[^\s/@:]+:[^\s/@]+@", re.IGNORECASE),
    ),
)


def _excluded(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return any(part in EXCLUDED_PARTS for part in relative.parts)


def _iter_text_files():
    for path in ROOT.rglob("*"):
        if (
            path.is_file()
            and path.name != ".env"
            and path.suffix.lower() in TEXT_SUFFIXES
            and not _excluded(path)
        ):
            yield path


def dependency_lock_check() -> dict[str, Any]:
    expected: dict[str, str] = {}
    for line in (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, pinned = line.split("==", 1)
        expected[name.strip().lower().replace("_", "-")] = pinned.strip()

    missing: list[str] = []
    mismatched: list[dict[str, str]] = []
    for name, pinned in expected.items():
        try:
            installed = version(name)
        except PackageNotFoundError:
            missing.append(name)
            continue
        if installed != pinned:
            mismatched.append(
                {"name": name, "expected": pinned, "installed": installed}
            )
    return {
        "ok": not missing and not mismatched,
        "entries": len(expected),
        "missing": missing,
        "mismatched": mismatched,
    }


def secret_scan() -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for path in _iter_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for pattern_name, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        {
                            "path": path.relative_to(ROOT).as_posix(),
                            "line": line_number,
                            "pattern": pattern_name,
                        }
                    )
    return {"ok": not findings, "findings": findings}


def _run_pip_audit(extra_args: list[str]) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        *extra_args,
        "--strict",
        "--timeout",
        "60",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout"}
    return {
        "ok": completed.returncode == 0,
        "status": "clean" if completed.returncode == 0 else "vulnerabilities_or_error",
        "returncode": completed.returncode,
    }


def pip_audit() -> dict[str, Any]:
    """Audit both declared runtime requirements and the complete local env.

    The requirements-file scan catches drift in the deployable runtime set,
    while the environment scan catches vulnerable dev/audit tooling that is
    present in the locked release workspace but is not imported by the app.
    Keep subprocess output out of the artifact so advisory details cannot
    accidentally include URLs or environment-specific paths.
    """

    checks = {
        "requirements": _run_pip_audit(
            ["-r", str(ROOT / "requirements.txt")]
        ),
        "environment": _run_pip_audit([]),
    }
    ok = all(result["ok"] for result in checks.values())
    return {
        "ran": True,
        "ok": ok,
        "status": "clean" if ok else "vulnerabilities_or_error",
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pip-audit", action="store_true")
    parser.add_argument("--require-pip-audit", action="store_true")
    args = parser.parse_args()

    lock = dependency_lock_check()
    secrets = secret_scan()
    audit = (
        pip_audit()
        if args.pip_audit
        else {"ran": False, "ok": None, "status": "skipped"}
    )
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dependency_lock": lock,
        "secret_scan": secrets,
        "pip_audit": audit,
    }
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    failed = not lock["ok"] or not secrets["ok"]
    if args.require_pip_audit and not audit["ran"]:
        failed = True
    if audit["ran"] and not audit["ok"]:
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
