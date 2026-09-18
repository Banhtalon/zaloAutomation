from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.database import current_db_path, init_db  # noqa: E402


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


required_files = {
    "repositories/evidence_repository.py",
    "tests/test_evidence_lifecycle.py",
    "api/evidence_routes.py",
    "services/evidence_store.py",
}
missing = sorted(path for path in required_files if not (ROOT / path).exists())
if missing:
    raise SystemExit(f"missing-phase6-files={missing}")

database = read("db/database.py")
evidence_repo = read("repositories/evidence_repository.py")
evidence_api = read("api/evidence_routes.py")
evidence_store = read("services/evidence_store.py")
zalo = read("services/zalo_service.py")
audit = read("repositories/audit_repository.py")
quality_script = read("CHECK_QUALITY.ps1")

invariants = {
    "lifecycle-table": "CREATE TABLE IF NOT EXISTS evidence_artifacts" in database
    and "CREATE TABLE IF NOT EXISTS evidence_access_log" in database,
    "lifecycle-states": all(
        token in evidence_repo
        for token in ("AVAILABLE", "EXPIRED", "MISSING", "QUARANTINED")
    ),
    "metadata-required": all(
        token in evidence_repo
        for token in ("sha256", "size_bytes", "mime_type", "created_at", "capture_type")
    ),
    "hash-fail-closed": "status = QUARANTINED" in evidence_repo
    and "EVIDENCE_HASH_MISMATCH" in evidence_api
    and "verify(" in evidence_api,
    "private-access-audit": "require_viewer" in evidence_api
    and "record_access" in evidence_api,
    "no-silent-delete": "hard_delete: bool = False" in evidence_store
    and "os.remove(file_path)" in evidence_store
    and "await evidence_repo.expire_old" in zalo,
    "audit-linkage": "evidence_artifact_id" in audit
    and "link_audit" in audit,
    "phase6-gate-wired": '"tests\\check_phase6_quality.py"' in quality_script,
}

failed = sorted(name for name, passed in invariants.items() if not passed)
if failed:
    raise SystemExit(f"phase6-invariant-failed={failed}")

database_path = Path(current_db_path())
if not database_path.is_file():
    init_db(str(database_path))
with sqlite3.connect(database_path) as connection:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required_tables = {"evidence_artifacts", "evidence_access_log"}
    if not required_tables.issubset(tables):
        raise SystemExit(
            f"phase6-schema-missing-tables={sorted(required_tables - tables)}"
        )
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(evidence_artifacts)")
    }
    required_columns = {
        "public_path",
        "stored_path",
        "sha256",
        "size_bytes",
        "mime_type",
        "created_at",
        "capture_type",
        "status",
    }
    if not required_columns.issubset(columns):
        raise SystemExit(
            f"phase6-schema-missing-columns={sorted(required_columns - columns)}"
        )
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("phase6-schema-integrity-failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise SystemExit("phase6-schema-foreign-key-failed")

print("phase6-quality-gate-ok")
