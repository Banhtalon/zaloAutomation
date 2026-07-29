import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    APP_VERSION = os.getenv("APP_VERSION", "4.0.0")
    FORM_URL = os.getenv("FORM_URL", "")
    MODE = os.getenv("GOOGLE_FORM_MODE", "dry-run").lower()
    RATE_LIMIT = int(os.getenv("GOOGLE_FORM_RATE_LIMIT", 5))
    CONCURRENCY_LIMIT = int(os.getenv("GOOGLE_FORM_CONCURRENCY_LIMIT", 3))
    HEADLESS = os.getenv("GOOGLE_FORM_HEADLESS", "false").lower() == "true"
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    BCNB_USERNAME = os.getenv("BCNB_USERNAME", "")
    BCNB_PASSWORD = os.getenv("BCNB_PASSWORD", "")
    BCNB_URL = os.getenv("BCNB_URL", "https://teachingmindx.top/check_nhanxet.php")
    # Safety default: browser jobs may inspect BCNB, but never mutate it unless
    # the operator explicitly opts in through the local .env file.
    BCNB_DRY_RUN = os.getenv("BCNB_DRY_RUN", "true").lower() != "false"
    MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 8 * 1024 * 1024))
    MAX_HTTP_BODY_BYTES = int(
        os.getenv("MAX_HTTP_BODY_BYTES", 12 * 1024 * 1024)
    )
    MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", 24_000_000))
    UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", "runtime/uploads")
    UPLOAD_RETENTION_HOURS = int(os.getenv("UPLOAD_RETENTION_HOURS", 24))
    JOB_HEARTBEAT_SECONDS = max(2, int(os.getenv("JOB_HEARTBEAT_SECONDS", 5)))
    LMS_CONFIRMATION_TTL_MINUTES = max(
        1, int(os.getenv("LMS_CONFIRMATION_TTL_MINUTES", 30))
    )
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_DIR = os.getenv("LOG_DIR", "runtime/logs")
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
