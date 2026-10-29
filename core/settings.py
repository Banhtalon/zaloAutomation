from dataclasses import dataclass, replace
from pathlib import Path

from googleform_autosubmit.config import Config


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Immutable per-app snapshot of runtime and observability settings."""

    database_path: str
    upload_root: str
    upload_retention_hours: int
    max_http_body_bytes: int
    lms_confirmation_ttl_minutes: int
    job_heartbeat_seconds: int
    app_version: str
    log_level: str
    log_dir: str
    log_max_bytes: int
    log_backup_count: int
    log_memory_lines: int
    metrics_window_hours: int
    health_stale_seconds: int
    min_free_disk_bytes: int
    bcnb_dry_run: bool
    google_form_mode: str
    gemini_configured: bool
    googleform_idempotency_ttl_hours: int = 24
    auth_mode: str = "local"
    auth_session_ttl_seconds: int = 28800
    auth_cookie_name: str = "zalo_session"
    auth_secure_cookie: bool = False
    auth_allowed_hosts: tuple[str, ...] = (
        "127.0.0.1",
        "localhost",
        "::1",
        "testserver",
        "testclient",
    )
    auth_allowed_origins: tuple[str, ...] = ()
    auth_users: tuple[tuple[str, str, str], ...] = ()
    local_production_ack: str = ""
    evidence_root: str = "static/screenshots"
    runtime_root: str = "runtime"
    browser_profile_path: str = "zalo_session"
    lock_root: str = "runtime/locks"
    max_concurrent_jobs: int = 1
    max_queued_jobs: int = 8
    job_timeout_seconds: int = 30 * 60
    lease_timeout_seconds: int = 90
    shutdown_grace_seconds: int = 20
    queue_poll_seconds: float = 0.25
    evidence_retention_days: int = 7
    evidence_retention_policy: str = "tombstone"
    evidence_delete_expired: bool = True
    db_backup_root: str = ".backups/db"
    db_backup_retention_count: int = 7
    zalo_scan_concurrency: int = 2
    bcnb_write_concurrency: int = 2
    zalo_plan_max_age_minutes: int = 30
    batch_systemic_failure_limit: int = 3
    batch_abnormal_percent_limit: int = 20
    class_ended_archive_days: int = 7
    report_email_recipients: tuple[str, ...] = ("quangda@mindx.net.vn",)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True

    def __post_init__(self):
        if not self.database_path or not self.upload_root or not self.log_dir:
            raise ValueError("Database/upload/log path không được để trống.")
        for name in (
            "database_path",
            "upload_root",
            "log_dir",
            "evidence_root",
            "runtime_root",
            "browser_profile_path",
            "lock_root",
            "db_backup_root",
        ):
            value = getattr(self, name)
            if not value:
                raise ValueError(f"{name} khong duoc de trong.")
            object.__setattr__(self, name, str(Path(value).expanduser().resolve()))
        positive_fields = {
            "upload_retention_hours": self.upload_retention_hours,
            "max_http_body_bytes": self.max_http_body_bytes,
            "lms_confirmation_ttl_minutes": self.lms_confirmation_ttl_minutes,
            "job_heartbeat_seconds": self.job_heartbeat_seconds,
            "log_max_bytes": self.log_max_bytes,
            "log_backup_count": self.log_backup_count,
            "log_memory_lines": self.log_memory_lines,
            "metrics_window_hours": self.metrics_window_hours,
            "health_stale_seconds": self.health_stale_seconds,
            "min_free_disk_bytes": self.min_free_disk_bytes,
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "job_timeout_seconds": self.job_timeout_seconds,
            "lease_timeout_seconds": self.lease_timeout_seconds,
            "shutdown_grace_seconds": self.shutdown_grace_seconds,
            "googleform_idempotency_ttl_hours": self.googleform_idempotency_ttl_hours,
            "zalo_scan_concurrency": self.zalo_scan_concurrency,
            "bcnb_write_concurrency": self.bcnb_write_concurrency,
            "zalo_plan_max_age_minutes": self.zalo_plan_max_age_minutes,
            "batch_systemic_failure_limit": self.batch_systemic_failure_limit,
            "batch_abnormal_percent_limit": self.batch_abnormal_percent_limit,
            "class_ended_archive_days": self.class_ended_archive_days,
        }
        invalid = sorted(name for name, value in positive_fields.items() if value <= 0)
        if invalid:
            raise ValueError("Cấu hình phải lớn hơn 0: " + ", ".join(invalid))
        if self.google_form_mode not in {"dry-run", "production"}:
            raise ValueError("GOOGLE_FORM_MODE khong hop le.")
        if self.auth_mode not in {"local", "web"}:
            raise ValueError("AUTH_MODE chi nhan local hoac web.")
        if self.auth_session_ttl_seconds <= 0:
            raise ValueError("AUTH_SESSION_TTL_SECONDS phai lon hon 0.")
        if not self.auth_cookie_name or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for char in self.auth_cookie_name
        ):
            raise ValueError("AUTH_COOKIE_NAME khong hop le.")
        allowed_roles = {"Viewer", "Operator", "Admin"}
        for username, role, password_hash in self.auth_users:
            if not username or role not in allowed_roles or not password_hash:
                raise ValueError("AUTH_USERS_JSON co user/role/hash khong hop le.")
        if not self.evidence_root:
            raise ValueError("EVIDENCE_ROOT khong duoc de trong.")
        if self.max_queued_jobs < 0 or self.queue_poll_seconds <= 0:
            raise ValueError("MAX_QUEUED_JOBS/QUEUE_POLL_SECONDS khong hop le.")
        if self.evidence_retention_days <= 0:
            raise ValueError("EVIDENCE_RETENTION_DAYS phai lon hon 0.")
        if self.evidence_retention_policy not in {"tombstone", "quarantine"}:
            raise ValueError("EVIDENCE_RETENTION_POLICY chi nhan tombstone hoac quarantine.")
        if self.db_backup_retention_count <= 0:
            raise ValueError("DB_BACKUP_RETENTION_COUNT phai lon hon 0.")
        if self.max_concurrent_jobs != 1:
            raise ValueError("App local bắt buộc MAX_CONCURRENT_JOBS=1.")
        if self.zalo_scan_concurrency != 2:
            raise ValueError("App local bắt buộc ZALO_SCAN_CONCURRENCY=2.")
        if self.bcnb_write_concurrency not in {1, 2}:
            raise ValueError("BCNB_WRITE_CONCURRENCY chỉ được 2 hoặc fallback 1.")
        if not 1 <= self.batch_abnormal_percent_limit <= 100:
            raise ValueError("BATCH_ABNORMAL_PERCENT_LIMIT phải nằm trong 1..100.")

    @property
    def profile_lock_path(self) -> str:
        return str(Path(self.lock_root) / "zalo-profile.lock")

    @classmethod
    def from_config(cls) -> "AppSettings":
        from db.database import DB_PATH

        return cls(
            database_path=DB_PATH,
            upload_root=Config.UPLOAD_ROOT,
            upload_retention_hours=Config.UPLOAD_RETENTION_HOURS,
            max_http_body_bytes=Config.MAX_HTTP_BODY_BYTES,
            lms_confirmation_ttl_minutes=Config.LMS_CONFIRMATION_TTL_MINUTES,
            job_heartbeat_seconds=Config.JOB_HEARTBEAT_SECONDS,
            app_version=Config.APP_VERSION,
            log_level=Config.LOG_LEVEL,
            log_dir=Config.LOG_DIR,
            log_max_bytes=Config.LOG_MAX_BYTES,
            log_backup_count=Config.LOG_BACKUP_COUNT,
            log_memory_lines=Config.LOG_MEMORY_LINES,
            metrics_window_hours=Config.METRICS_WINDOW_HOURS,
            health_stale_seconds=Config.HEALTH_STALE_SECONDS,
            min_free_disk_bytes=Config.MIN_FREE_DISK_BYTES,
            bcnb_dry_run=Config.BCNB_DRY_RUN,
            google_form_mode=Config.MODE,
            googleform_idempotency_ttl_hours=Config.IDEMPOTENCY_TTL_HOURS,
            gemini_configured=bool(Config.GEMINI_API_KEY),
            auth_mode=Config.AUTH_MODE,
            auth_session_ttl_seconds=Config.AUTH_SESSION_TTL_SECONDS,
            auth_cookie_name=Config.AUTH_COOKIE_NAME,
            auth_secure_cookie=Config.AUTH_SECURE_COOKIE,
            auth_allowed_hosts=Config.AUTH_ALLOWED_HOSTS,
            auth_allowed_origins=Config.AUTH_ALLOWED_ORIGINS,
            auth_users=Config.AUTH_USERS,
            local_production_ack=Config.LOCAL_PRODUCTION_ACK,
            evidence_root=Config.EVIDENCE_ROOT,
            evidence_retention_days=Config.EVIDENCE_RETENTION_DAYS,
            evidence_retention_policy=Config.EVIDENCE_RETENTION_POLICY,
            evidence_delete_expired=Config.EVIDENCE_DELETE_EXPIRED,
            db_backup_root=Config.DB_BACKUP_ROOT,
            db_backup_retention_count=Config.DB_BACKUP_RETENTION_COUNT,
            runtime_root=Config.RUNTIME_ROOT,
            browser_profile_path=Config.BROWSER_PROFILE_PATH,
            lock_root=Config.LOCK_ROOT,
            max_concurrent_jobs=Config.MAX_CONCURRENT_JOBS,
            max_queued_jobs=Config.MAX_QUEUED_JOBS,
            job_timeout_seconds=Config.JOB_TIMEOUT_SECONDS,
            lease_timeout_seconds=Config.LEASE_TIMEOUT_SECONDS,
            shutdown_grace_seconds=Config.SHUTDOWN_GRACE_SECONDS,
            queue_poll_seconds=Config.QUEUE_POLL_SECONDS,
            zalo_scan_concurrency=Config.ZALO_SCAN_CONCURRENCY,
            bcnb_write_concurrency=Config.BCNB_WRITE_CONCURRENCY,
            zalo_plan_max_age_minutes=Config.ZALO_PLAN_MAX_AGE_MINUTES,
            batch_systemic_failure_limit=Config.BATCH_SYSTEMIC_FAILURE_LIMIT,
            batch_abnormal_percent_limit=Config.BATCH_ABNORMAL_PERCENT_LIMIT,
            class_ended_archive_days=Config.CLASS_ENDED_ARCHIVE_DAYS,
            report_email_recipients=Config.REPORT_EMAIL_RECIPIENTS,
            smtp_host=Config.SMTP_HOST,
            smtp_port=Config.SMTP_PORT,
            smtp_username=Config.SMTP_USERNAME,
            smtp_password=Config.SMTP_PASSWORD,
            smtp_from=Config.SMTP_FROM,
            smtp_use_tls=Config.SMTP_USE_TLS,
        )

    def with_overrides(self, **values) -> "AppSettings":
        return replace(self, **values)
