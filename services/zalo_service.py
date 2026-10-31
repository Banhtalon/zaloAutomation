import json
import os
from typing import Any

from googleform_autosubmit.config import Config
from core.time_utils import business_day_start, now_business
from logger_utils import log_message
from repositories.audit_repository import AuditRepository
from repositories.evidence_repository import EvidenceRepository
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobCancelledError, JobRepository
from services.browser_job_guard import exclusive_browser_job
from services.external_gateways import ExternalGateways
from services.runtime import WorkerRuntime
from services.zalo_discovery_service import run_auto_discovery as run_auto_discovery


group_repo = GroupRepository()
job_repo = JobRepository()
audit_repo = AuditRepository()
evidence_repo = EvidenceRepository()


async def run_zalo_automation(
    job_id: int,
    execution_mode: str = "dry-run",
    *,
    scan_plan: dict | None = None,
    runtime=None,
):
    try:
        async with exclusive_browser_job(
            job_id,
            job_repo,
            lock=runtime.browser_lock if runtime else None,
            process_lock=getattr(runtime, "profile_lock", None) if runtime else None,
        ):
            await _run_zalo_automation(
                job_id,
                execution_mode,
                scan_plan=scan_plan,
                external=(runtime.external if runtime else ExternalGateways.production()),
                evidence_root=(getattr(runtime, "evidence_root", None) if runtime else None),
                runtime=runtime,
            )
    except JobCancelledError as exc:
        actions = await audit_repo.list_for_job(job_id, 200)
        verified = [item for item in actions if item["outcome"] == "VERIFIED"]
        cancelled_payload: dict[str, Any] = {
            "kind": "zalo_scan_cancelled",
            "verified_actions": len(verified),
            "cancelled": True,
        }
        if isinstance(scan_plan, dict):
            if scan_plan.get("snapshot_id") is not None:
                cancelled_payload["snapshot_id"] = scan_plan.get("snapshot_id")
            if isinstance(scan_plan.get("required_week"), dict):
                cancelled_payload["required_week"] = dict(scan_plan["required_week"])
            if scan_plan.get("selective_rescan"):
                cancelled_payload["selective_rescan"] = True
                cancelled_payload["selected_target_keys"] = list(
                    scan_plan.get("selected_target_keys") or []
                )
        await job_repo.update_job(
            job_id,
            status="PARTIAL_SUCCESS" if verified else "CANCELLED",
            progress=100,
            message=(
                f"Đã dừng theo yêu cầu sau {len(verified)} thay đổi đã kiểm chứng."
                if verified
                else str(exc)
            ),
            results=json.dumps(
                cancelled_payload,
                ensure_ascii=False,
            ),
        )


async def _run_zalo_automation(
    job_id: int,
    execution_mode: str,
    *,
    scan_plan: dict | None = None,
    external: ExternalGateways | None = None,
    evidence_root: str | None = None,
    runtime=None,
):
    evidence_root = evidence_root or os.path.abspath(
        os.path.join("static", "screenshots")
    )
    await evidence_repo.expire_old(
        root=evidence_root,
        retention_days=getattr(Config, "EVIDENCE_RETENTION_DAYS", 7),
        policy=getattr(Config, "EVIDENCE_RETENTION_POLICY", "tombstone"),
        delete_files=getattr(Config, "EVIDENCE_DELETE_EXPIRED", True),
    )
    job_screenshots_dir = os.path.join(evidence_root, f"job-{job_id}")
    os.makedirs(job_screenshots_dir, exist_ok=True)
    await job_repo.update_job(job_id, status="RUNNING", message="Đang quét báo cáo...", progress=5)
    log_message("Bắt đầu tiến trình quét báo cáo tự động...")

    if not scan_plan:
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message="Dừng an toàn: quét Zalo bắt buộc có snapshot hai tuần do BCNB cung cấp.",
            progress=100,
        )
        log_message(
            "Từ chối quét Zalo không có snapshot hai tuần do BCNB cung cấp.",
            level="ERROR",
            event="zalo.scan_plan_required",
        )
        return

    scan_started_at = now_business()
    try:
        from services.zalo_parallel_service import (
            prepare_frozen_scan_execution,
            run_and_finalize_parallel_zalo_batch,
        )

        prepared = await prepare_frozen_scan_execution(
            job_id=job_id,
            scan_plan=scan_plan,
            scan_started_at=scan_started_at,
            runtime=runtime,
            job_repo=job_repo,
        )
        if prepared is None:
            return
        scan_plan, _groups_list, reconciliation_only = prepared
    except JobCancelledError:
        raise
    except Exception as e:
        await job_repo.update_job(
            job_id, status="FAILED", message="Lỗi: Không thể lấy danh sách nhóm từ Database"
        )
        log_message(f"Lỗi: Không thể lấy danh sách nhóm ({e})")
        return

    today = business_day_start(scan_started_at)
    external = external or (
        runtime.external if runtime is not None else ExternalGateways.production()
    )
    effective_runtime = (
        runtime
        if runtime is not None
        else WorkerRuntime(external=external, evidence_root=evidence_root)
    )
    await run_and_finalize_parallel_zalo_batch(
        job_id=job_id,
        execution_mode=execution_mode,
        scan_plan=scan_plan,
        today=today,
        scan_started_at=scan_started_at,
        evidence_root=evidence_root,
        screenshots_dir=job_screenshots_dir,
        runtime=effective_runtime,
        job_repo=job_repo,
        audit_repo=audit_repo,
        evidence_repo=evidence_repo,
        reconciliation_only=reconciliation_only,
    )
