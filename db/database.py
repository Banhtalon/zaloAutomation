import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

import aiosqlite


DB_PATH = os.getenv("DATABASE_PATH", "database.db")
_database_path: ContextVar[str | None] = ContextVar("database_path", default=None)


def current_db_path() -> str:
    return _database_path.get() or DB_PATH


@contextmanager
def database_context(path: str):
    token = _database_path.set(path)
    try:
        yield
    finally:
        _database_path.reset(token)


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")


def _ensure_column(cursor: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(path: str | None = None):
    conn = sqlite3.connect(path or current_db_path(), timeout=5)
    _configure_connection(conn)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT UNIQUE,
            person_in_charge TEXT,
            class_date TEXT,
            class_code TEXT,
            cohort_key TEXT,
            lesson_number INTEGER
        )
        """
    )
    _ensure_column(cursor, "groups", "class_code", "TEXT")
    _ensure_column(cursor, "groups", "cohort_key", "TEXT")
    _ensure_column(cursor, "groups", "lesson_number", "INTEGER")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ignored_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT UNIQUE,
            ignored_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'PAUSED_OR_TRANSITIONING',
            class_code TEXT,
            cohort_key TEXT,
            ended_at DATETIME,
            archived_at DATETIME,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ensure_column(cursor, "ignored_groups", "ignored_at", "DATETIME")
    _ensure_column(
        cursor,
        "ignored_groups",
        "status",
        "TEXT NOT NULL DEFAULT 'PAUSED_OR_TRANSITIONING'",
    )
    _ensure_column(cursor, "ignored_groups", "class_code", "TEXT")
    _ensure_column(cursor, "ignored_groups", "cohort_key", "TEXT")
    _ensure_column(cursor, "ignored_groups", "ended_at", "DATETIME")
    _ensure_column(cursor, "ignored_groups", "archived_at", "DATETIME")
    _ensure_column(cursor, "ignored_groups", "updated_at", "DATETIME")
    cursor.execute(
        """
        UPDATE ignored_groups
        SET status = 'PAUSED_OR_TRANSITIONING'
        WHERE status IS NULL OR status = ''
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS class_cohorts (
            cohort_key TEXT PRIMARY KEY,
            center_code TEXT NOT NULL,
            track TEXT NOT NULL,
            cohort_number TEXT NOT NULL,
            current_class_code TEXT NOT NULL,
            current_group_name TEXT,
            current_lesson_number INTEGER,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            last_seen_bcnb_at DATETIME,
            missing_since DATETIME,
            ended_at DATETIME,
            archived_at DATETIME,
            updated_at DATETIME NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_class_cohorts_status
        ON class_cohorts(status, updated_at)
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS class_code_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cohort_key TEXT NOT NULL,
            class_code TEXT NOT NULL,
            group_name TEXT NOT NULL DEFAULT '',
            first_seen_at DATETIME NOT NULL,
            last_seen_at DATETIME NOT NULL,
            last_lesson_number INTEGER,
            UNIQUE(cohort_key, class_code, group_name),
            FOREIGN KEY (cohort_key) REFERENCES class_cohorts(cohort_key)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_class_code_history_cohort
        ON class_code_history(cohort_key, first_seen_at)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS form_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT UNIQUE,
            field_type TEXT,
            value TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS form_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT,
            class_code TEXT,
            submitted_at TEXT,
            status TEXT,
            error_message TEXT
        )
        """
    )
    _ensure_column(cursor, "form_submissions", "job_id", "INTEGER")
    _ensure_column(cursor, "form_submissions", "action_key", "TEXT")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_jobs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT,
            status TEXT,
            started_at DATETIME,
            finished_at DATETIME,
            progress INTEGER,
            message TEXT,
            results TEXT
        )
        """
    )
    _ensure_column(cursor, "scan_jobs", "created_at", "DATETIME")
    _ensure_column(cursor, "scan_jobs", "heartbeat_at", "DATETIME")
    _ensure_column(cursor, "scan_jobs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cursor, "scan_jobs", "attempt", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(cursor, "scan_jobs", "retry_of_job_id", "INTEGER")
    _ensure_column(cursor, "scan_jobs", "retryable", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cursor, "scan_jobs", "recoverable", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cursor, "scan_jobs", "mutates_external", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cursor, "scan_jobs", "request_payload", "TEXT")
    _ensure_column(cursor, "scan_jobs", "recovery_note", "TEXT")
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scan_jobs_metrics_time
        ON scan_jobs(COALESCE(created_at, started_at))
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT,
            class_code TEXT,
            lesson_number INTEGER,
            lesson_date DATE,
            confidence REAL,
            created_at DATETIME
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS classification_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            rule_score REAL,
            embedding_score REAL,
            llm_result TEXT,
            final_result TEXT,
            created_at DATETIME
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS lms_confirmations(
            analysis_job_id INTEGER PRIMARY KEY,
            confirmation_token TEXT UNIQUE NOT NULL,
            plan_digest TEXT NOT NULL,
            plan_payload TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            consumed_at DATETIME,
            execution_job_id INTEGER,
            FOREIGN KEY (analysis_job_id) REFERENCES scan_jobs(id),
            FOREIGN KEY (execution_job_id) REFERENCES scan_jobs(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS job_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT,
            message TEXT,
            details_json TEXT,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (job_id) REFERENCES scan_jobs(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON job_events(job_id, id)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS external_action_audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_key TEXT UNIQUE NOT NULL,
            job_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            reason_json TEXT,
            before_json TEXT,
            intended_json TEXT,
            after_json TEXT,
            outcome TEXT NOT NULL DEFAULT 'INTENT_RECORDED',
            evidence_path TEXT,
            error_message TEXT,
            created_at DATETIME NOT NULL,
            completed_at DATETIME,
            FOREIGN KEY (job_id) REFERENCES scan_jobs(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_audit_job_id ON external_action_audit(job_id, id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_audit_created_at ON external_action_audit(created_at)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS job_uploads(
            upload_id TEXT PRIMARY KEY,
            purpose TEXT NOT NULL,
            stored_path TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'STAGED',
            job_id INTEGER,
            created_at DATETIME NOT NULL,
            claimed_at DATETIME,
            deleted_at DATETIME,
            FOREIGN KEY (job_id) REFERENCES scan_jobs(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_uploads_job_id ON job_uploads(job_id, state)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_uploads_created_at ON job_uploads(created_at)"
    )

    cursor.execute("SELECT COUNT(*) FROM form_mappings")
    if cursor.fetchone()[0] == 0:
        default_mappings = [
            ("Đánh giá mức độ hoàn thành bài tập của em?", "radio", "Hoàn thành tốt"),
            ("Ý kiến phản hồi khác (nếu có)", "text", "Không có ý kiến gì thêm."),
        ]
        cursor.executemany(
            "INSERT INTO form_mappings (question, field_type, value) VALUES (?, ?, ?)",
            default_mappings,
        )

    conn.commit()
    conn.close()


@asynccontextmanager
async def get_db_connection(path: str | None = None):
    db = await aiosqlite.connect(path or current_db_path(), timeout=5)
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 5000")
    try:
        yield db
    finally:
        await db.close()
