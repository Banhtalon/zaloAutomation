"""Phase 7 invariant gate: time contract, migration chain and restore drill."""

from __future__ import annotations

import ast
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.backup import backup_database, restore_drill  # noqa: E402
from db.database import current_db_path, get_schema_migrations, init_db  # noqa: E402


def _assert_no_naive_now() -> None:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__", ".backups"} for part in path.parts):
            continue
        if "tests" in path.parts or path == ROOT / "core" / "time_utils.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "now" or node.args or node.keywords:
                continue
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "datetime":
                violations.append(f"{path}:{node.lineno}")
    if violations:
        raise AssertionError("Naive datetime.now() remains: " + ", ".join(violations))


def main() -> int:
    _assert_no_naive_now()
    init_db()
    migrations = get_schema_migrations()
    versions = [int(item["version"]) for item in migrations]
    if versions != [1, 2, 3, 4]:
        raise AssertionError(f"Migration chain khong dung: {versions}")
    if any(len(str(item["checksum"])) != 64 for item in migrations):
        raise AssertionError("Migration checksum khong phai SHA-256.")

    with sqlite3.connect(current_db_path()) as connection:
        indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(external_action_audit)")
        }
        if "idx_action_audit_outcome_created_at" not in indexes:
            raise AssertionError("Thieu index audit outcome/time.")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise AssertionError(
                f"DB integrity/FK khong dat: {integrity}, {foreign_keys}"
            )

    with tempfile.TemporaryDirectory(prefix="phase7-restore-") as directory:
        root = Path(directory)
        fresh = root / "fresh.sqlite3"
        backup = root / "backup.sqlite3"
        restored = root / "restored.sqlite3"
        init_db(str(fresh))
        artifact = backup_database(fresh, backup)
        drill = restore_drill(backup, restored)
        if artifact.sha256 != drill["sha256"] or drill["integrity"] != "ok":
            raise AssertionError("Restore drill artifact/hash khong dat.")

    print(
        "PHASE 7 QUALITY PASS: "
        f"migrations={versions}, backup_sha256={artifact.sha256}, "
        f"restore_integrity={drill['integrity']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
