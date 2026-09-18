import asyncio
from contextlib import suppress
from datetime import datetime
from time import monotonic
import uuid

from core.settings import AppSettings
from core.auth import AuthManager
from core.production_readiness import assert_production_ready
from core.time_utils import now_utc
from db.database import database_context, init_db
from googleform_autosubmit.config import Config
from logger_utils import LogManager, log_message, use_log_manager
from repositories.audit_repository import AuditRepository
from repositories.evidence_repository import EvidenceRepository
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobRepository
from repositories.lms_repository import LmsRepository
from repositories.report_repository import ReportRepository
from repositories.upload_repository import UploadRepository
from repositories.zalo_batch_repository import ZaloBatchRepository
from services.job_dispatcher import JobDispatcher
from services.external_gateways import ExternalGateways
from services.runtime import WorkerRuntime
from services.upload_store import UploadStore


class AppContainer:
    """Composition root and lifecycle owner for one FastAPI application."""

    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or AppSettings.from_config()
        # Check before constructing the browser worker.  Dry-run remains
        # available locally, while any external mutation fails closed until
        # web auth, HTTPS allowlists and required credentials are configured.
        self.production_readiness = assert_production_ready(
            self.settings,
            form_url=Config.FORM_URL,
            bcnb_url=Config.BCNB_URL,
            bcnb_username=Config.BCNB_USERNAME,
            bcnb_password=Config.BCNB_PASSWORD,
            gemini_api_key=Config.GEMINI_API_KEY,
            local_production_ack=self.settings.local_production_ack,
        )
        self.auth = AuthManager(self.settings)
        self.job_repo = JobRepository()
        self.group_repo = GroupRepository()
        self.report_repo = ReportRepository()
        self.lms_repo = LmsRepository()
        self.audit_repo = AuditRepository()
        self.evidence_repo = EvidenceRepository()
        self.upload_repo = UploadRepository()
        self.zalo_batch_repo = ZaloBatchRepository()
        self.instance_id = uuid.uuid4().hex[:12]
        self.worker_runtime = WorkerRuntime(
            external=ExternalGateways.production(
                profile_path=self.settings.browser_profile_path,
            ),
            profile_lock_path=self.settings.profile_lock_path,
            profile_lock_timeout_seconds=self.settings.lease_timeout_seconds,
            evidence_root=self.settings.evidence_root,
            database_path=self.settings.database_path,
            db_backup_root=self.settings.db_backup_root,
            db_backup_retention_count=self.settings.db_backup_retention_count,
            zalo_scan_concurrency=self.settings.zalo_scan_concurrency,
            bcnb_write_concurrency=self.settings.bcnb_write_concurrency,
            batch_systemic_failure_limit=self.settings.batch_systemic_failure_limit,
            batch_abnormal_percent_limit=self.settings.batch_abnormal_percent_limit,
        )
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
            max_concurrent_jobs=self.settings.max_concurrent_jobs,
            max_queued_jobs=self.settings.max_queued_jobs,
            job_timeout_seconds=self.settings.job_timeout_seconds,
            lease_timeout_seconds=self.settings.lease_timeout_seconds,
            shutdown_grace_seconds=self.settings.shutdown_grace_seconds,
            queue_poll_seconds=self.settings.queue_poll_seconds,
            worker_id=self.instance_id,
        )
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
        self.dispatcher.worker_id = self.instance_id
        self.started_at = now_utc()
        self.started_monotonic = monotonic()
        self.log_manager = self._new_log_manager()
        self.dispatcher.log_manager = self.log_manager
        with database_context(self.settings.database_path), use_log_manager(
            self.log_manager
        ):
            init_db(self.settings.database_path)
            backfilled = await self.evidence_repo.backfill_existing_results(
                evidence_root=self.settings.evidence_root,
                retention_days=self.settings.evidence_retention_days,
            )
            expired = await self.evidence_repo.expire_old(
                root=self.settings.evidence_root,
                retention_days=self.settings.evidence_retention_days,
                policy=self.settings.evidence_retention_policy,
                delete_files=self.settings.evidence_delete_expired,
            )
            if backfilled or expired:
                log_message(
                    "Đã đồng bộ vòng đời minh chứng.",
                    event="evidence.lifecycle.reconciled",
                    backfilled=backfilled,
                    expired=expired,
                )
            archive_days = self.settings.class_ended_archive_days
            archived = await self.group_repo.archive_confirmed_ended_groups(
                archive_days
            )
            if archived:
                log_message(
                    f"Đã lưu trữ {archived} lớp được xác nhận kết thúc quá "
                    f"{archive_days} ngày.",
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
                archive_days = self.settings.class_ended_archive_days
                archived = await self.group_repo.archive_confirmed_ended_groups(
                    archive_days
                )
                if archived:
                    log_message(
                        f"Đã lưu trữ {archived} lớp được xác nhận kết thúc quá "
                        f"{archive_days} ngày.",
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
