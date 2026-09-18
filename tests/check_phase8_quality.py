"""Phase 8 invariant gate: Google Form idempotency is fail-closed."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.database import current_db_path, get_schema_migrations, init_db  # noqa: E402


def main() -> int:
    init_db()
    migrations = get_schema_migrations()
    versions = [int(item["version"]) for item in migrations]
    if versions != [1, 2, 3, 4]:
        raise AssertionError(f"Migration chain Phase 8 không đúng: {versions}")

    with sqlite3.connect(current_db_path()) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "googleform_idempotency" not in tables:
            raise AssertionError("Thiếu bảng googleform_idempotency.")
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(googleform_idempotency)")
        }
        required = {
            "key_digest",
            "payload_hash",
            "actor_key",
            "target_hash",
            "execution_mode",
            "job_id",
            "expires_at",
        }
        if not required.issubset(columns):
            raise AssertionError(f"Thiếu cột idempotency: {sorted(required - columns)}")
        duplicate_keys = connection.execute(
            "SELECT key_digest FROM googleform_idempotency GROUP BY key_digest HAVING COUNT(*) > 1"
        ).fetchall()
        if duplicate_keys:
            raise AssertionError(f"Có idempotency key trùng: {duplicate_keys}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise AssertionError(f"DB integrity/FK không đạt: {integrity}, {foreign_keys}")

    route = (ROOT / "api" / "googleform_routes.py").read_text(encoding="utf-8")
    ui = (ROOT / "static" / "js" / "googleform.js").read_text(encoding="utf-8")
    repo = (ROOT / "repositories" / "job_repository.py").read_text(encoding="utf-8")
    for marker, source in (
        ("create_idempotent_job", repo),
        ("IDEMPOTENCY_KEY_REQUIRED", route),
        ("googleform.idempotency.v1", ui),
        ("idempotency_replay", route),
    ):
        if marker not in source:
            raise AssertionError(f"Thiếu marker Phase 8: {marker}")

    print(
        "PHASE 8 QUALITY PASS: "
        f"migrations={versions}, idempotency_table=ok, duplicate_keys=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
