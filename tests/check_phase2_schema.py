import sqlite3

from db.database import DB_PATH, init_db


init_db()
connection = sqlite3.connect(DB_PATH)
try:
    columns = [row[1] for row in connection.execute("PRAGMA table_info(scan_jobs)")]
    expected_columns = {
        "heartbeat_at",
        "cancel_requested",
        "attempt",
        "retry_of_job_id",
        "retryable",
        "recoverable",
        "mutates_external",
        "request_payload",
    }
    missing = expected_columns - set(columns)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    expected_tables = {"job_events", "external_action_audit", "job_uploads"}
    audit_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(external_action_audit)")
    }
    missing_audit_columns = {"evidence_sha256"} - audit_columns
    if missing or missing_audit_columns or not expected_tables.issubset(tables):
        raise SystemExit(
            f"schema-invalid missing_columns={sorted(missing)} "
            f"missing_audit_columns={sorted(missing_audit_columns)} "
            f"missing_tables={sorted(expected_tables - tables)}"
        )
    active_jobs = connection.execute(
        "SELECT COUNT(*) FROM scan_jobs WHERE status IN ('PENDING', 'RUNNING')"
    ).fetchone()[0]
    active_summary = connection.execute(
        """
        SELECT job_type, status, COUNT(*) FROM scan_jobs
        WHERE status IN ('PENDING', 'RUNNING')
        GROUP BY job_type, status ORDER BY job_type, status
        """
    ).fetchall()
    print(
        f"phase2-schema-ok database={DB_PATH} "
        f"scan_jobs_columns={len(columns)} active_jobs={active_jobs}"
    )
    print(f"active_summary={active_summary}")
finally:
    connection.close()
