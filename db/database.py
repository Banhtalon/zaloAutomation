import os
import hashlib
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path

import aiosqlite

from core.time_utils import utc_iso


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_configured_db = Path(os.getenv("DATABASE_PATH", "database.db")).expanduser()
DB_PATH = str(
    _configured_db
    if _configured_db.is_absolute()
    else (_PROJECT_ROOT / _configured_db).resolve()
)
_database_path: ContextVar[str | None] = ContextVar("database_path", default=None)


class SchemaMigrationError(RuntimeError):
    """Raised when a recorded migration cannot be verified or applied."""


_MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "baseline_schema_v1",
        ("BASELINE_SCHEMA_V1",),
    ),
    (
        2,
        "phase7_integrity_indexes",
        (
            "CREATE INDEX IF NOT EXISTS idx_scan_jobs_status_created_at "
            "ON scan_jobs(status, created_at, id)",
            "CREATE INDEX IF NOT EXISTS idx_action_audit_outcome_created_at "
            "ON external_action_audit(outcome, created_at, id)",
            "CREATE INDEX IF NOT EXISTS idx_action_audit_target "
            "ON external_action_audit(target_type, target_id, action_type)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_artifacts_sha256 "
            "ON evidence_artifacts(sha256, status)",
            "CREATE INDEX IF NOT EXISTS idx_form_submissions_action_key "
            "ON form_submissions(action_key, id)",
        ),
    ),
    (
        3,
        "phase8_googleform_idempotency",
        (
            """
            CREATE TABLE IF NOT EXISTS googleform_idempotency(
                key_digest TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                actor_key TEXT NOT NULL,
                target_hash TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                job_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(job_id) REFERENCES scan_jobs(id)
            )
            """.strip(),
            "CREATE INDEX IF NOT EXISTS idx_googleform_idempotency_expiry "
            "ON googleform_idempotency(expires_at, status)",
            "CREATE INDEX IF NOT EXISTS idx_googleform_idempotency_binding "
            "ON googleform_idempotency(operation, payload_hash, actor_key, target_hash, execution_mode)",
        ),
    ),
    (
        4,
        "local_parallel_zalo_batch_targets",
        (
            """
            CREATE TABLE IF NOT EXISTS zalo_batch_targets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                target_key TEXT NOT NULL,
                group_name TEXT NOT NULL,
                person TEXT,
                class_code TEXT NOT NULL,
                week INTEGER NOT NULL,
                year INTEGER NOT NULL,
                lesson TEXT NOT NULL,
                class_date TEXT,
                class_date_iso TEXT NOT NULL,
                source_json TEXT NOT NULL,
                scan_status TEXT NOT NULL DEFAULT 'PENDING',
                write_status TEXT NOT NULL DEFAULT 'NOT_READY',
                claim_phase TEXT,
                claim_owner TEXT,
                claim_expires_at DATETIME,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                evidence_path TEXT,
                evidence_sha256 TEXT,
                result_json TEXT,
                error_code TEXT,
                error_message TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                started_at DATETIME,
                finished_at DATETIME,
                UNIQUE(job_id, target_key),
                FOREIGN KEY(job_id) REFERENCES scan_jobs(id)
            )
            """.strip(),
            "CREATE INDEX IF NOT EXISTS idx_zalo_batch_targets_scan "
            "ON zalo_batch_targets(job_id, scan_status, id)",
            "CREATE INDEX IF NOT EXISTS idx_zalo_batch_targets_write "
            "ON zalo_batch_targets(job_id, write_status, week, year, id)",
            "CREATE INDEX IF NOT EXISTS idx_zalo_batch_targets_claim "
            "ON zalo_batch_targets(claim_owner, claim_expires_at)",
        ),
    ),
)


def _migration_checksum(statements: tuple[str, ...]) -> str:
    canonical = "\n".join(statement.strip() for statement in statements)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_schema_migrations(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations(
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _apply_migrations(cursor: sqlite3.Cursor) -> None:
    """Apply forward-only, checksum-verified migrations in one transaction."""
    _ensure_schema_migrations(cursor)
    cursor.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    )
    applied = {int(row[0]): (str(row[1]), str(row[2])) for row in cursor.fetchall()}
    expected_versions = {version for version, _, _ in _MIGRATIONS}
    unexpected = sorted(set(applied) - expected_versions)
    if unexpected:
        raise SchemaMigrationError(
            f"Schema migration version khong duoc ho tro: {unexpected}"
        )

    for version, name, statements in _MIGRATIONS:
        checksum = _migration_checksum(statements)
        recorded = applied.get(version)
        if recorded is not None:
            if recorded != (name, checksum):
                raise SchemaMigrationError(
                    f"Checksum migration {version} khong khop; dung migration forward-only."
                )
            continue
        if version == 1:
            # The pre-Phase-7 schema is created by init_db above.  Recording a
            # baseline is intentionally non-destructive and lets old DBs join
            # the same migration chain as fresh DBs.
            pass
        else:
            for statement in statements:
                cursor.execute(statement)
        cursor.execute(
            """
            INSERT INTO schema_migrations(version, name, checksum, applied_at)
            VALUES (?, ?, ?, ?)
            """,
            (version, name, checksum, utc_iso()),
        )


def get_schema_migrations(path: str | None = None) -> list[dict[str, str | int]]:
    """Read migration metadata without changing the database."""
    conn = sqlite3.connect(path or current_db_path(), timeout=5)
    try:
        cursor = conn.execute(
            "SELECT version, name, checksum, applied_at "
            "FROM schema_migrations ORDER BY version"
        )
        return [
            {
                "version": int(row[0]),
                "name": str(row[1]),
                "checksum": str(row[2]),
                "applied_at": str(row[3]),
            }
            for row in cursor.fetchall()
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


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
    _ensure_schema_migrations(cursor)

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
        CREATE TABLE IF NOT EXISTS discovery_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at DATETIME NOT NULL,
            source_count INTEGER NOT NULL,
            recognized_count INTEGER NOT NULL,
            expected_count INTEGER NOT NULL,
            coverage REAL NOT NULL,
            delta_count INTEGER NOT NULL,
            fingerprint_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            trust_reason TEXT,
            plan_json TEXT NOT NULL,
            promoted_at DATETIME,
            previous_snapshot_id INTEGER,
            FOREIGN KEY (previous_snapshot_id) REFERENCES discovery_snapshots(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discovery_snapshots_status_time
        ON discovery_snapshots(status, observed_at, id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discovery_snapshots_fingerprint
        ON discovery_snapshots(fingerprint_sha256, id)
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
    _ensure_column(cursor, "form_submissions", "screenshot_path", "TEXT")

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
    _ensure_column(cursor, "scan_jobs", "lease_owner", "TEXT")
    _ensure_column(cursor, "scan_jobs", "lease_expires_at", "DATETIME")
    _ensure_column(cursor, "scan_jobs", "deadline_at", "DATETIME")
    _ensure_column(cursor, "scan_jobs", "queued_at", "DATETIME")
    _ensure_column(cursor, "scan_jobs", "queue_position", "INTEGER")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_jobs_queue "
        "ON scan_jobs(status, queue_position, id)"
    )
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
    # Confirmation contracts are persisted so a mode/config change can revoke
    # an old preview instead of allowing it to be consumed later.  These
    # additive columns keep existing databases upgradeable without destructive
    # migrations; legacy rows continue to use created_at + the configured TTL.
    _ensure_column(cursor, "lms_confirmations", "expires_at", "DATETIME")
    _ensure_column(cursor, "lms_confirmations", "invalidated_at", "DATETIME")
    _ensure_column(cursor, "lms_confirmations", "invalidated_reason", "TEXT")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lms_confirmations_expires_at "
        "ON lms_confirmations(expires_at)"
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
            evidence_sha256 TEXT,
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
    _ensure_column(
        cursor,
        "external_action_audit",
        "evidence_sha256",
        "TEXT",
    )
    _ensure_column(cursor, "external_action_audit", "evidence_artifact_id", "INTEGER")
    _ensure_column(cursor, "external_action_audit", "evidence_status", "TEXT")
    _ensure_column(cursor, "external_action_audit", "evidence_size_bytes", "INTEGER")
    _ensure_column(cursor, "external_action_audit", "evidence_mime_type", "TEXT")
    _ensure_column(cursor, "external_action_audit", "evidence_created_at", "DATETIME")
    _ensure_column(cursor, "external_action_audit", "evidence_capture_type", "TEXT")

    # Evidence is a first-class, append-only audit record.  Files are never
    # silently deleted by retention; their lifecycle is represented here.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_artifacts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            audit_id INTEGER,
            public_path TEXT NOT NULL UNIQUE,
            stored_path TEXT NOT NULL,
            sha256 TEXT,
            size_bytes INTEGER,
            mime_type TEXT,
            created_at DATETIME NOT NULL,
            capture_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'AVAILABLE',
            expires_at DATETIME,
            last_verified_at DATETIME,
            quarantined_at DATETIME,
            tombstoned_at DATETIME,
            error_message TEXT,
            FOREIGN KEY (job_id) REFERENCES scan_jobs(id),
            FOREIGN KEY (audit_id) REFERENCES external_action_audit(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_artifacts_job "
        "ON evidence_artifacts(job_id, status, id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_artifacts_status_expiry "
        "ON evidence_artifacts(status, expires_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_artifacts_audit "
        "ON evidence_artifacts(audit_id, id)"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_access_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_artifact_id INTEGER,
            job_id INTEGER,
            username TEXT,
            accessed_at DATETIME NOT NULL,
            outcome TEXT NOT NULL,
            request_id TEXT,
            error_message TEXT,
            FOREIGN KEY (evidence_artifact_id) REFERENCES evidence_artifacts(id),
            FOREIGN KEY (job_id) REFERENCES scan_jobs(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_access_log_artifact "
        "ON evidence_access_log(evidence_artifact_id, accessed_at)"
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
            ("Cơ sở trường MindX mà em đang học", "select", "Vũng Tàu - 205A Lê Hồng Phong"),
            ("Đánh giá mức độ hoàn thành bài tập của em?", "radio", "Hoàn thành tốt"),
            ("Ý kiến phản hồi khác (nếu có)", "text", "Không có ý kiến gì thêm."),
        ]
        cursor.executemany(
            "INSERT INTO form_mappings (question, field_type, value) VALUES (?, ?, ?)",
            default_mappings,
        )
    else:
        cursor.execute(
            "UPDATE form_mappings SET question = 'Cơ sở trường MindX mà em đang học', field_type = 'select', value = 'Vũng Tàu - 205A Lê Hồng Phong' WHERE question = 'Mã cơ sở của em là?'"
        )

    try:
        _apply_migrations(cursor)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
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
