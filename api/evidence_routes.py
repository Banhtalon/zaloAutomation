from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from api.dependencies import get_container, require_viewer
from api.errors import public_error
from core.auth import AuthUser
from core.container import AppContainer


router = APIRouter()
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.png$")


def _paths_in(value) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"path", "screenshot_path", "evidence_path"} and isinstance(item, str):
                paths.add(item)
            paths.update(_paths_in(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(_paths_in(item))
    return paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


@router.get("/api/evidence/{job_id}/{filename}")
async def get_evidence(
    job_id: int,
    filename: str,
    request: Request,
    container: AppContainer = Depends(get_container),
    _user: AuthUser = Depends(require_viewer),
):
    if job_id <= 0 or not _SAFE_FILENAME.fullmatch(filename):
        raise public_error(404, "EVIDENCE_NOT_FOUND", "Evidence was not found.")
    job = await container.job_repo.get_job_record(job_id)
    if not job:
        raise public_error(404, "EVIDENCE_NOT_FOUND", "Evidence was not found.")
    raw_results = job.get("results")
    try:
        results = json.loads(raw_results) if isinstance(raw_results, str) else raw_results
    except json.JSONDecodeError:
        results = None
    expected_paths = _paths_in(results)
    audit_rows = await container.audit_repo.list_for_job(job_id, limit=500)
    expected_paths.update(_paths_in(audit_rows))
    endpoint_path = f"/api/evidence/{job_id}/{filename}"
    legacy_path = f"/static/screenshots/job-{job_id}/{filename}"
    if endpoint_path not in expected_paths and legacy_path not in expected_paths:
        raise public_error(404, "EVIDENCE_NOT_FOUND", "Evidence does not belong to this job.")

    root = Path(container.settings.evidence_root).resolve()
    job_root = (root / f"job-{job_id}").resolve()
    file_path = (job_root / filename).resolve()
    if job_root != file_path.parent:
        await container.evidence_repo.record_access(
            artifact_id=None,
            job_id=job_id,
            username=_user.username,
            outcome="PATH_REJECTED",
            request_id=request.headers.get("x-request-id"),
        )
        raise public_error(404, "EVIDENCE_NOT_FOUND", "Evidence file was not found.")

    stored_hashes = {
        str(item.get("sha256") or "").upper()
        for item in _iter_image_records(results)
        if item.get("path") in {endpoint_path, legacy_path}
    }
    stored_hashes.update(
        str(item.get("evidence_sha256") or "").upper()
        for item in audit_rows
        if isinstance(item, dict)
        and item.get("evidence_path") in {endpoint_path, legacy_path}
        and item.get("evidence_sha256")
    )
    if not stored_hashes:
        await container.evidence_repo.record_access(
            artifact_id=None,
            job_id=job_id,
            username=_user.username,
            outcome="HASH_MISSING",
            request_id=request.headers.get("x-request-id"),
        )
        raise public_error(410, "EVIDENCE_HASH_MISSING", "Evidence has no stored SHA-256.")
    expected_hash = next(iter(stored_hashes))
    public_path = endpoint_path if endpoint_path in expected_paths else legacy_path
    artifact = await container.evidence_repo.get_by_public_path(public_path)
    if artifact is None:
        # A legacy job result may predate the registry.  Register only the
        # bytes and digest that are actually present; never invent a hash.
        if not file_path.is_file():
            await container.evidence_repo.record_access(
                artifact_id=None,
                job_id=job_id,
                username=_user.username,
                outcome="MISSING",
                request_id=request.headers.get("x-request-id"),
            )
            raise public_error(404, "EVIDENCE_NOT_FOUND", "Evidence file was not found.")
        artifact = await container.evidence_repo.register_artifact(
            job_id=job_id,
            public_path=endpoint_path,
            stored_path=str(file_path),
            capture_type="legacy_backfill",
            expected_sha256=expected_hash,
            retention_days=container.settings.evidence_retention_days,
        )
        public_path = endpoint_path
    stored_path = Path(str(artifact.get("stored_path") or "")).resolve()
    if stored_path != file_path:
        await container.evidence_repo.record_access(
            artifact_id=artifact.get("id"),
            job_id=job_id,
            username=_user.username,
            outcome="PATH_REJECTED",
            request_id=request.headers.get("x-request-id"),
            error_message="Registry path is outside the job evidence directory.",
        )
        raise public_error(404, "EVIDENCE_NOT_FOUND", "Evidence file was not found.")
    verified = await container.evidence_repo.verify(
        public_path,
        expected_sha256=expected_hash,
        job_id=job_id,
    )
    if verified is None or verified.get("status") != "AVAILABLE":
        status = str((verified or artifact).get("status") or "MISSING")
        code = {
            "MISSING": "EVIDENCE_MISSING",
            "EXPIRED": "EVIDENCE_EXPIRED",
            "QUARANTINED": "EVIDENCE_HASH_MISMATCH",
        }.get(status, "EVIDENCE_NOT_FOUND")
        await container.evidence_repo.record_access(
            artifact_id=(verified or artifact).get("id"),
            job_id=job_id,
            username=_user.username,
            outcome=status,
            request_id=request.headers.get("x-request-id"),
            error_message=(verified or artifact).get("error_message"),
        )
        raise public_error(410, code, "Evidence is not currently available.")
    actual_hash = str(verified.get("sha256") or "").upper()
    if actual_hash not in stored_hashes:
        await container.evidence_repo.record_access(
            artifact_id=verified.get("id"),
            job_id=job_id,
            username=_user.username,
            outcome="HASH_MISMATCH",
            request_id=request.headers.get("x-request-id"),
        )
        raise public_error(410, "EVIDENCE_HASH_MISMATCH", "Evidence hash does not match.")
    await container.evidence_repo.record_access(
        artifact_id=verified.get("id"),
        job_id=job_id,
        username=_user.username,
        outcome="SERVED",
        request_id=request.headers.get("x-request-id"),
    )
    response = FileResponse(
        file_path,
        media_type="image/png",
        filename=filename,
        content_disposition_type="inline",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Evidence-SHA256"] = actual_hash
    return response


def _iter_image_records(value):
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            yield value
        for item in value.values():
            yield from _iter_image_records(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_image_records(item)
