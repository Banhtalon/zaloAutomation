from dataclasses import dataclass, field
import asyncio
from pathlib import Path

from services.external_gateways import ExternalGateways
from services.profile_lock import InterProcessFileLock


@dataclass(slots=True)
class WorkerRuntime:
    """Mutable locks owned by one application container/lifespan."""

    external: ExternalGateways = field(default_factory=ExternalGateways.production)
    browser_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ocr_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    form_submit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    profile_lock_path: str = str(
        (Path("runtime") / "locks" / "zalo-profile.lock").resolve()
    )
    profile_lock_timeout_seconds: float = 45.0
    profile_lock_stale_after_seconds: float = 10 * 60
    evidence_root: str = str(Path("static") / "screenshots")
    database_path: str = "database.db"
    db_backup_root: str = ".backups/db"
    db_backup_retention_count: int = 7
    zalo_scan_concurrency: int = 2
    bcnb_write_concurrency: int = 2
    batch_systemic_failure_limit: int = 3
    batch_abnormal_percent_limit: int = 20
    _landing_probe_consumed: bool = field(default=False, init=False, repr=False)
    profile_lock: InterProcessFileLock = field(init=False)

    def __post_init__(self) -> None:
        self.profile_lock_path = str(Path(self.profile_lock_path).expanduser().resolve())
        self.evidence_root = str(Path(self.evidence_root).expanduser().resolve())
        self.database_path = str(Path(self.database_path).expanduser().resolve())
        self.db_backup_root = str(Path(self.db_backup_root).expanduser().resolve())
        self.profile_lock = InterProcessFileLock(
            self.profile_lock_path,
            timeout_seconds=self.profile_lock_timeout_seconds,
            stale_after_seconds=self.profile_lock_stale_after_seconds,
        )

    def reset_locks(self) -> None:
        if any(
            lock.locked()
            for lock in (self.browser_lock, self.ocr_lock, self.form_submit_lock)
        ):
            raise RuntimeError("Không thể reset runtime khi lock còn được sử dụng.")
        self.browser_lock = asyncio.Lock()
        self.ocr_lock = asyncio.Lock()
        self.form_submit_lock = asyncio.Lock()

    @property
    def landing_probe_consumed(self) -> bool:
        """Whether this runtime has consumed its one landing-probe attempt."""

        return self._landing_probe_consumed

    def consume_landing_probe_attempt(self) -> bool:
        """Consume the process-lifetime landing-probe latch exactly once."""

        if self._landing_probe_consumed:
            return False
        self._landing_probe_consumed = True
        return True
