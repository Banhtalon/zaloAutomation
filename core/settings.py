from dataclasses import dataclass, replace

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

    def __post_init__(self):
        if not self.database_path or not self.upload_root or not self.log_dir:
            raise ValueError("Database/upload/log path không được để trống.")
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
        }
        invalid = sorted(name for name, value in positive_fields.items() if value <= 0)
        if invalid:
            raise ValueError("Cấu hình phải lớn hơn 0: " + ", ".join(invalid))
        if self.google_form_mode not in {"dry-run", "production"}:
            raise ValueError("GOOGLE_FORM_MODE chỉ nhận dry-run hoặc production.")

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
            gemini_configured=bool(Config.GEMINI_API_KEY),
        )

    def with_overrides(self, **values) -> "AppSettings":
        return replace(self, **values)
