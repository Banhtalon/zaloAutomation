import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from time import monotonic
import uuid

from core.settings import AppSettings
from db.database import database_context, init_db
from logger_utils import LogManager, log_message, use_log_manager
from repositories.audit_repository import AuditRepository
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobRepository
from repositories.lms_repository import LmsRepository
from repositories.report_repository import ReportRepository
from repositories.upload_repository import UploadRepository
from services.job_dispatcher import JobDispatcher
from services.runtime import WorkerRuntime
from services.upload_store import UploadStore


class AppContainer:
    """Composition root and lifecycle owner for one FastAPI application."""

    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or AppSettings.from_config()
        self.job_repo = JobRepository()
        self.group_repo = GroupRepository()
        self.report_repo = ReportRepository()
        self.lms_repo = LmsRepository()
        self.audit_repo = AuditRepository()
        self.upload_repo = UploadRepository()
        self.worker_runtime = WorkerRuntime()
        self.upload_store = UploadStore(
            root=self.settings.upload_root,
            repository=self.upload_repo,
        )
        self.log_manager: LogManager | None = None
        self.dispatcher = JobDispatcher(
            job_repo=self.job_repo,
            upload_store=self.upload_store,
            worker_runtime=self.worker_runtime,
            database_path=self.settings.database_path,
            heartbeat_seconds=self.settings.job_heartbeat_seconds,
        )
        self.instance_id = uuid.uuid4().hex[:12]
        self.started_at: datetime | None = None
        self.started_monotonic: float | None = None
        self._started = False
        self._class_lifecycle_task: asyncio.Task | None = None

    def _new_log_manager(self) -> LogManager:
        return LogManager(
            log_dir=self.settings.log_dir,
            level=self.settings.log_level,
            max_bytes=self.settings.log_max_bytes,
            backup_count=self.settings.log_backup_count,
            memory_lines=self.settings.log_memory_lines,
        )

    async def start(self) -> None:
        if self._started:
            return
        self.instance_id = uuid.uuid4().hex[:12]
        self.started_at = datetime.now(timezone.utc)
        self.started_monotonic = monotonic()
        self.log_manager = self._new_log_manager()
        self.dispatcher.log_manager = self.log_manager
        with database_context(self.settings.database_path), use_log_manager(
            self.log_manager
        ):
            init_db(self.settings.database_path)
            archived = await self.group_repo.archive_confirmed_ended_groups(14)
            if archived:
                log_message(
                    f"Đã lưu trữ {archived} lớp được xác nhận kết thúc quá 14 ngày.",
                    event="class_lifecycle.archived",
                    archived_count=archived,
                )
            await self.upload_store.reconcile_orphans()
            await self.upload_store.cleanup_expired(
                self.settings.upload_retention_hours
            )
            await self.dispatcher.recover_jobs()
            log_message(
                "Ứng dụng đã sẵn sàng.",
                event="app.ready",
                version=self.settings.app_version,
                bcnb_mode=(
                    "dry-run" if self.settings.bcnb_dry_run else "production"
                ),
                google_form_mode=self.settings.google_form_mode,
            )
        self._started = True
        self._class_lifecycle_task = asyncio.create_task(
            self._class_lifecycle_maintenance_loop(),
            name=f"class-lifecycle-{self.instance_id}",
        )

    async def _class_lifecycle_maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(24 * 60 * 60)
            manager = self.log_manager
            if manager is None:
                return
            with database_context(self.settings.database_path), use_log_manager(manager):
                archived = await self.group_repo.archive_confirmed_ended_groups(14)
                if archived:
                    log_message(
                        f"Đã lưu trữ {archived} lớp được xác nhận kết thúc quá 14 ngày.",
                        event="class_lifecycle.archived",
                        archived_count=archived,
                    )

    async def stop(self) -> None:
        if self._class_lifecycle_task is not None:
            self._class_lifecycle_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._class_lifecycle_task
            self._class_lifecycle_task = None
        if not self._started:
            await self.dispatcher.shutdown_jobs()
            return
        manager = self.log_manager
        if manager is None:
            raise RuntimeError("Log manager không tồn tại khi dừng ứng dụng.")
        with database_context(self.settings.database_path), use_log_manager(manager):
            await self.dispatcher.shutdown_jobs()
            log_message("Ứng dụng đã dừng.", event="app.stopped")
        manager.close()
        self._started = False

    @property
    def started(self) -> bool:
        return self._started
