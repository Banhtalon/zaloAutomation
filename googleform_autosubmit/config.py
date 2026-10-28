import json
import os
from pathlib import Path

# Configuration is intentionally sourced from the process environment only.
# Loading ``.env`` at import time made quality checks and subprocesses inherit
# production paths/secrets before an explicit run context could be installed.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _path_env(name: str, default: str) -> str:
    value = Path(os.getenv(name, default)).expanduser()
    return str(value if value.is_absolute() else (PROJECT_ROOT / value).resolve())


def _csv_env(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.getenv(name, default).split(",")
        if item.strip()
    )


def _auth_users_env() -> tuple[tuple[str, str, str], ...]:
    """Read only pre-hashed web users; never accept plaintext passwords."""
    raw = os.getenv("AUTH_USERS_JSON", "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    users: list[tuple[str, str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        role = str(item.get("role") or "").strip()
        password_hash = str(item.get("password_hash") or "").strip()
        if username and role and password_hash:
            users.append((username, role, password_hash))
    return tuple(users)

class Config:
    APP_VERSION = os.getenv("APP_VERSION", "4.0.0")
    FORM_URL = os.getenv("FORM_URL", "")
    MODE = os.getenv("GOOGLE_FORM_MODE", "dry-run").lower()
    RATE_LIMIT = int(os.getenv("GOOGLE_FORM_RATE_LIMIT", 5))
    CONCURRENCY_LIMIT = int(os.getenv("GOOGLE_FORM_CONCURRENCY_LIMIT", 3))
    IDEMPOTENCY_TTL_HOURS = max(
        1, int(os.getenv("GOOGLEFORM_IDEMPOTENCY_TTL_HOURS", 24))
    )
    HEADLESS = os.getenv("GOOGLE_FORM_HEADLESS", "false").lower() == "true"
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    # Secondary AI assist for ambiguous Zalo report messages. Disabled by
    # default; even when enabled it only flags messages for manual review and
    # never sets submitted_for_bcnb=True automatically.
    ZALO_AI_ASSIST_CLASSIFIER = (
        os.getenv("ZALO_AI_ASSIST_CLASSIFIER", "false").strip().lower() == "true"
    )
    BCNB_USERNAME = os.getenv("BCNB_USERNAME", "")
    BCNB_PASSWORD = os.getenv("BCNB_PASSWORD", "")
    BCNB_URL = os.getenv("BCNB_URL", "https://teachingmindx.top/check_nhanxet.php")
    LMS_USERNAME = os.getenv("LMS_USERNAME", "")
    LMS_PASSWORD = os.getenv("LMS_PASSWORD", "")
    LMS_URL = os.getenv(
        "LMS_URL",
        'https://lms.mindx.edu.vn/admin/class-sessions?centres=[%22644346f794300678908f79bc%22]&type=[%22MISS_ATTEND%22,%22DUE_COMMENT%22]&haveSlot=[]&status=[%22FINISHED%22,%22RUNNING%22]&pageIndex=0&itemsPerPage=20&orderBy=%22createdAt_desc%22',
    ).strip()
    # Safety default: browser jobs may inspect BCNB, but never mutate it unless
    # the operator explicitly opts in through the local .env file.
    BCNB_DRY_RUN = os.getenv("BCNB_DRY_RUN", "true").lower() != "false"
    MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 8 * 1024 * 1024))
    MAX_HTTP_BODY_BYTES = int(
        os.getenv("MAX_HTTP_BODY_BYTES", 12 * 1024 * 1024)
    )
    MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", 24_000_000))
    UPLOAD_ROOT = _path_env("UPLOAD_ROOT", "runtime/uploads")
    RUNTIME_ROOT = _path_env("RUNTIME_ROOT", "runtime")
    BROWSER_PROFILE_PATH = _path_env("BROWSER_PROFILE_PATH", "zalo_session")
    LOCK_ROOT = _path_env("LOCK_ROOT", "runtime/locks")
    EVIDENCE_ROOT = _path_env("EVIDENCE_ROOT", "static/screenshots")
    DB_BACKUP_ROOT = _path_env("DB_BACKUP_ROOT", ".backups/db")
    DB_BACKUP_RETENTION_COUNT = max(
        1, int(os.getenv("DB_BACKUP_RETENTION_COUNT", 7))
    )
    CLASS_ENDED_ARCHIVE_DAYS = max(
        1, int(os.getenv("CLASS_ENDED_ARCHIVE_DAYS", 7))
    )
    BCNB_CLASS_STATUS_URL = os.getenv(
        "BCNB_CLASS_STATUS_URL",
        "https://teachingmindx.top/hocsinh.php?datatable=students",
    ).strip()
    EVIDENCE_RETENTION_DAYS = max(
        1, int(os.getenv("EVIDENCE_RETENTION_DAYS", 7))
    )
    EVIDENCE_RETENTION_POLICY = os.getenv(
        "EVIDENCE_RETENTION_POLICY", "tombstone"
    ).strip().lower()
    # Keep the audit/evidence registry rows, but remove the expired image
    # bytes during the next application/scan run.  This is intentionally
    # separate from the lifecycle policy so audit readback remains intact.
    EVIDENCE_DELETE_EXPIRED = (
        os.getenv("EVIDENCE_DELETE_EXPIRED", "true").strip().lower() == "true"
    )
    UPLOAD_RETENTION_HOURS = int(os.getenv("UPLOAD_RETENTION_HOURS", 24))
    JOB_HEARTBEAT_SECONDS = max(2, int(os.getenv("JOB_HEARTBEAT_SECONDS", 5)))
    MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", 1)))
    ZALO_SCAN_CONCURRENCY = max(1, min(4, int(os.getenv("ZALO_SCAN_CONCURRENCY", 2))))
    BCNB_WRITE_CONCURRENCY = max(1, min(4, int(os.getenv("BCNB_WRITE_CONCURRENCY", 2))))
    ZALO_PLAN_MAX_AGE_MINUTES = max(
        1, int(os.getenv("ZALO_PLAN_MAX_AGE_MINUTES", 30))
    )
    BATCH_SYSTEMIC_FAILURE_LIMIT = max(
        1, int(os.getenv("BATCH_SYSTEMIC_FAILURE_LIMIT", 3))
    )
    BATCH_ABNORMAL_PERCENT_LIMIT = max(
        1, min(100, int(os.getenv("BATCH_ABNORMAL_PERCENT_LIMIT", 20)))
    )
    MAX_QUEUED_JOBS = max(0, int(os.getenv("MAX_QUEUED_JOBS", 8)))
    JOB_TIMEOUT_SECONDS = max(
        30, int(os.getenv("JOB_TIMEOUT_SECONDS", 30 * 60))
    )
    LEASE_TIMEOUT_SECONDS = max(
        JOB_HEARTBEAT_SECONDS * 3,
        int(os.getenv("LEASE_TIMEOUT_SECONDS", 90)),
    )
    SHUTDOWN_GRACE_SECONDS = max(
        1, int(os.getenv("SHUTDOWN_GRACE_SECONDS", 20))
    )
    QUEUE_POLL_SECONDS = max(
        0.05, float(os.getenv("QUEUE_POLL_SECONDS", 0.25))
    )
    GATEWAY_MAX_RETRIES = max(0, int(os.getenv("GATEWAY_MAX_RETRIES", 2)))
    GATEWAY_BACKOFF_SECONDS = max(
        0.0, float(os.getenv("GATEWAY_BACKOFF_SECONDS", 0.25))
    )
    GATEWAY_FAILURE_THRESHOLD = max(
        1, int(os.getenv("GATEWAY_FAILURE_THRESHOLD", 3))
    )
    GATEWAY_COOLDOWN_SECONDS = max(
        1.0, float(os.getenv("GATEWAY_COOLDOWN_SECONDS", 30))
    )
    LMS_CONFIRMATION_TTL_MINUTES = max(
        1, int(os.getenv("LMS_CONFIRMATION_TTL_MINUTES", 30))
    )
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_DIR = _path_env("LOG_DIR", "runtime/logs")
    LOG_MAX_BYTES = max(64 * 1024, int(os.getenv("LOG_MAX_BYTES", 5 * 1024 * 1024)))
    LOG_BACKUP_COUNT = max(1, int(os.getenv("LOG_BACKUP_COUNT", 5)))
    LOG_MEMORY_LINES = max(100, int(os.getenv("LOG_MEMORY_LINES", 500)))
    METRICS_WINDOW_HOURS = max(1, int(os.getenv("METRICS_WINDOW_HOURS", 24)))
    HEALTH_STALE_SECONDS = max(
        30,
        int(os.getenv("HEALTH_STALE_SECONDS", 3 * JOB_HEARTBEAT_SECONDS)),
    )
    MIN_FREE_DISK_BYTES = max(
        16 * 1024 * 1024,
        int(os.getenv("MIN_FREE_DISK_BYTES", 100 * 1024 * 1024)),
    )
    AUTH_MODE = os.getenv("AUTH_MODE", "local").strip().lower()
    AUTH_SESSION_TTL_SECONDS = max(
        300, int(os.getenv("AUTH_SESSION_TTL_SECONDS", 8 * 60 * 60))
    )
    AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "zalo_session").strip()
    AUTH_SECURE_COOKIE = os.getenv("AUTH_SECURE_COOKIE", "false").lower() == "true"
    AUTH_ALLOWED_HOSTS = _csv_env(
        "AUTH_ALLOWED_HOSTS", "127.0.0.1,localhost,::1,testserver,testclient"
    )
    AUTH_ALLOWED_ORIGINS = _csv_env("AUTH_ALLOWED_ORIGINS")
    AUTH_USERS = _auth_users_env()
    # High-friction opt-in for a personal, loopback-only production run.  The
    # value intentionally acknowledges that the current /run-scan endpoint is
    # a broad batch operation, not a single-target canary.
    LOCAL_PRODUCTION_ACK = os.getenv("LOCAL_PRODUCTION_ACK", "").strip()
