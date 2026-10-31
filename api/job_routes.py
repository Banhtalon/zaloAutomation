import json
from typing import Any

from fastapi import APIRouter, Depends, Query

from api.dependencies import get_container, require_operator, require_viewer
from api.errors import internal_error, public_error
from core.container import AppContainer
from repositories.job_repository import JobNotFoundError, JobStateError
from services.report_status import (
    SUBMITTED_EVIDENCE_STATES,
    categorize_row,
    is_failed,
)
from services.zalo_preflight import effective_scan_worker_count


router = APIRouter()


def _annotate_item_status(item: dict[str, Any]) -> dict[str, Any]:
    item["category"] = categorize_row(item)
    item["is_failed"] = is_failed(item)
    return item


def _enrich_job_payload(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(job, dict):
        return job
    results = job.get("results")
    parsed_from_str = False
    if isinstance(results, str) and results.strip():
        try:
            results = json.loads(results)
            parsed_from_str = True
        except (TypeError, ValueError):
            parsed_from_str = False
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                _annotate_item_status(item)
    elif isinstance(results, dict):
        items = results.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    _annotate_item_status(item)
        updated_item = results.get("updated_item")
        if isinstance(updated_item, dict):
            _annotate_item_status(updated_item)
    if parsed_from_str:
        job["results"] = json.dumps(results, ensure_ascii=False)
    return job


def _zalo_target_report(targets: list[dict]) -> tuple[dict[str, int], list[dict]]:
    """Build a small, read-only Owner report without exposing raw result text."""
    counts = {
        "total": len(targets),
        "checked": 0,
        "submitted_verified": 0,
        "not_found_verified": 0,
        "insufficient_evidence": 0,
        "scan_failed": 0,
        "blocked_batch_halt": 0,
    }
    review_queue: list[dict] = []
    technical_errors = {
        "SCAN_FAILED",
        "TARGET_SEARCH_CLEAR_TIMEOUT",
        "TARGET_SEARCH_CLEAR_ERROR",
        "TARGET_SEARCH_FILL_TIMEOUT",
        "TARGET_SEARCH_FILL_ERROR",
        "TARGET_SEARCH_RESULT_TIMEOUT",
        "TARGET_SEARCH_RESULT_ERROR",
    }
    for target in targets:
        scan_status = str(target.get("scan_status") or "")
        write_status = str(target.get("write_status") or "")
        result_value = target.get("result")
        result = result_value if isinstance(result_value, dict) else {}
        evidence_state = str(result.get("evidence_state") or "")
        error_code = str(target.get("error_code") or result.get("error_code") or "")
        if scan_status not in {"PENDING", "RUNNING"}:
            counts["checked"] += 1
        if evidence_state in SUBMITTED_EVIDENCE_STATES:
            counts["submitted_verified"] += 1
        elif evidence_state == "NOT_FOUND_VERIFIED":
            counts["not_found_verified"] += 1
        elif (
            error_code in technical_errors
            or error_code.startswith("TARGET_SEARCH_")
            or error_code.startswith("SETUP_")
        ):
            counts["scan_failed"] += 1
        elif evidence_state == "INSUFFICIENT_EVIDENCE":
            counts["insufficient_evidence"] += 1
        if write_status == "BLOCKED_BATCH_HALT":
            counts["blocked_batch_halt"] += 1

        needs_review = (
            scan_status == "FAILED"
            or write_status in {"FAILED_BEFORE_WRITE", "OUTCOME_UNKNOWN", "BLOCKED_BATCH_HALT"}
            or evidence_state in {"INSUFFICIENT_EVIDENCE", "UNKNOWN"}
            or error_code in technical_errors
            or error_code.startswith("TARGET_SEARCH_")
            or error_code.startswith("SETUP_")
        )
        if not needs_review:
            continue
        evidence_paths = []
        evidence_value = result.get("evidence")
        evidence = evidence_value if isinstance(evidence_value, dict) else {}
        images_value = evidence.get("images")
        images = images_value if isinstance(images_value, dict) else {}
        for image in images.values():
            if isinstance(image, dict) and isinstance(image.get("path"), str) and image["path"]:
                evidence_paths.append(image["path"])
        if isinstance(target.get("evidence_path"), str) and target["evidence_path"]:
            evidence_paths.append(target["evidence_path"])
        review_queue.append(
            {
                "target_id": target.get("id"),
                "class_code": target.get("class_code"),
                "lesson": target.get("lesson"),
                "class_date": target.get("class_date"),
                "reason_code": error_code or evidence_state or write_status or "REVIEW_REQUIRED",
                "reason": (
                    "Lỗi kỹ thuật cần kiểm tra lại."
                    if (
                        error_code in technical_errors
                        or error_code.startswith("TARGET_SEARCH_")
                        or error_code.startswith("SETUP_")
                    )
                    else "Đã có báo cáo đúng buổi nhưng bước cập nhật BCNB cần kiểm tra."
                    if evidence_state in {"SUBMITTED_VERIFIED", "SUBMITTED_LATE_VERIFIED"}
                    else "Chưa tìm thấy báo cáo đúng buổi."
                    if evidence_state == "NOT_FOUND_VERIFIED"
                    else "Chưa đủ bằng chứng để kết luận."
                ),
                "action": "Mở evidence và Nhật ký; không tự ghi lại BCNB từ màn hình này.",
                "evidence_paths": list(dict.fromkeys(evidence_paths)),
            }
        )
    return counts, review_queue


@router.get("/api/jobs/active/latest", dependencies=[Depends(require_viewer)])
async def get_latest_active_job(container: AppContainer = Depends(get_container)):
    return _enrich_job_payload(
        await container.job_repo.get_latest_job(active_only=True)
    )


@router.get("/api/jobs/latest", dependencies=[Depends(require_viewer)])
async def get_latest_job(
    container: AppContainer = Depends(get_container),
    job_type: str | None = Query(default=None),
):
    clean_type = (
        None
        if hasattr(job_type, "default") or job_type is None
        else str(job_type).strip().upper() or None
    )
    job = (
        await container.job_repo.get_latest_job(job_type=clean_type)
        if clean_type
        else await container.job_repo.get_latest_job()
    )
    return _enrich_job_payload(job)


@router.get("/api/jobs/{job_id}", dependencies=[Depends(require_viewer)])
async def get_job_status(
    job_id: int,
    container: AppContainer = Depends(get_container),
):
    job = await container.job_repo.get_job(job_id)
    if not job:
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.")
    return _enrich_job_payload(job)


@router.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_operator)])
async def cancel_job(job_id: int, container: AppContainer = Depends(get_container)):
    try:
        result = await container.job_repo.request_cancel(job_id)
    except JobNotFoundError as exc:
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.") from exc
    except JobStateError as exc:
        raise public_error(
            409,
            "JOB_STATE_CONFLICT",
            "Trạng thái job không cho phép thao tác này.",
        ) from exc
    return {"job_id": job_id, **result}


@router.post("/api/jobs/{job_id}/retry", dependencies=[Depends(require_operator)])
async def retry_terminal_job(
    job_id: int,
    container: AppContainer = Depends(get_container),
):
    try:
        new_job_id = await container.dispatcher.retry_job(job_id)
    except JobNotFoundError as exc:
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.") from exc
    except JobStateError as exc:
        raise public_error(
            409,
            "JOB_STATE_CONFLICT",
            "Job hiện tại không thể chạy lại.",
        ) from exc
    except Exception as exc:
        raise internal_error(exc, event="api.jobs.retry_failed") from exc
    return {"job_id": new_job_id, "retry_of_job_id": job_id}


@router.get("/api/jobs/{job_id}/events", dependencies=[Depends(require_viewer)])
async def get_job_events(
    job_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    container: AppContainer = Depends(get_container),
):
    if not await container.job_repo.get_job(job_id):
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.")
    return {"events": await container.job_repo.list_events(job_id, limit)}


@router.get("/api/jobs/{job_id}/audit", dependencies=[Depends(require_viewer)])
async def get_job_audit(
    job_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    container: AppContainer = Depends(get_container),
):
    if not await container.job_repo.get_job(job_id):
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.")
    return {"actions": await container.audit_repo.list_for_job(job_id, limit)}


@router.get("/api/jobs/{job_id}/targets", dependencies=[Depends(require_viewer)])
async def get_job_targets(
    job_id: int,
    container: AppContainer = Depends(get_container),
):
    job = await container.job_repo.get_job(job_id)
    if not job:
        raise public_error(404, "JOB_NOT_FOUND", "Không tìm thấy job.")
    repository = container.zalo_batch_repo
    settings = getattr(container, "settings", None)
    targets = await repository.list_for_job(job_id)
    for target in targets:
        if not isinstance(target, dict):
            continue
        res_val = target.get("result")
        if isinstance(res_val, dict):
            eff_write_status = str(
                target.get("write_status") or res_val.get("write_status") or ""
            ).strip()
            eff_bcnb_state = str(res_val.get("bcnb_update_state") or "").strip()
            if eff_write_status and (
                not eff_bcnb_state
                or eff_write_status.upper()
                in {
                    "FAILED",
                    "FAILED_BEFORE_WRITE",
                    "OUTCOME_UNKNOWN",
                    "CONFLICT_AFTER_WRITE",
                    "BLOCKED_BATCH_HALT",
                }
            ):
                eff_bcnb_state = eff_write_status
            combined_row = {
                **res_val,
                "bcnb_update_state": eff_bcnb_state or None,
                "write_status": eff_write_status or None,
                "scan_status": target.get("scan_status") or res_val.get("scan_status"),
                "error_code": target.get("error_code") or res_val.get("error_code"),
            }
            category = categorize_row(combined_row)
            failed = is_failed(combined_row)
            if eff_bcnb_state:
                res_val["bcnb_update_state"] = eff_bcnb_state
            res_val["category"] = category
            res_val["is_failed"] = failed
            target["category"] = category
            target["is_failed"] = failed
        else:
            target["category"] = categorize_row(target)
            target["is_failed"] = is_failed(target)
    report, review_queue = _zalo_target_report(targets)
    execution_mode = "production" if job.get("mutates_external") else "dry-run"
    get_record = getattr(container.job_repo, "get_job_record", None)
    if get_record is not None:
        record = await get_record(job_id)
        payload = record.get("request_payload") if isinstance(record, dict) else None
        if isinstance(payload, dict) and payload.get("execution_mode") in {"dry-run", "production"}:
            execution_mode = payload["execution_mode"]
    return {
        "job_id": job_id,
        "worker_count": effective_scan_worker_count(settings, execution_mode),
        "execution_mode": execution_mode,
        "summary": await repository.summary(job_id),
        "report": report,
        "review_queue": review_queue,
        "targets": targets,
    }
