"""Fail-closed execution-mode helpers for persisted background jobs."""

DRY_RUN = "dry-run"
PRODUCTION = "production"


class ExecutionModeError(ValueError):
    pass


def normalize_execution_mode(value, *, default: str = DRY_RUN) -> str:
    """Normalize a stored mode; unknown or missing values never enable writes."""
    if value is None:
        value = default
    if isinstance(value, bool):
        return DRY_RUN if value else PRODUCTION
    normalized = str(value).strip().lower().replace("_", "-")
    if normalized in {"dry-run", "dryrun", "preview"}:
        return DRY_RUN
    if normalized == PRODUCTION:
        return PRODUCTION
    raise ExecutionModeError(f"Chế độ thực thi không hợp lệ: {value!r}")


def mode_from_job(job: dict) -> str:
    """Read the immutable job mode and verify it matches recovery metadata."""
    payload = job.get("request_payload") or {}
    value = payload.get("execution_mode")
    if value is None and "dry_run" in payload:
        value = payload["dry_run"]
    if value is None and isinstance(payload.get("plan"), dict):
        value = payload["plan"].get("dry_run")

    # Legacy jobs without a mode are deliberately downgraded to dry-run.
    mode = normalize_execution_mode(value, default=DRY_RUN)
    mutates_external = bool(job.get("mutates_external"))
    retryable = bool(job.get("retryable"))
    recoverable = bool(job.get("recoverable"))

    if mode == PRODUCTION:
        if not mutates_external or retryable or recoverable:
            raise ExecutionModeError(
                "Job production không khớp metadata an toàn; từ chối thực thi."
            )
    elif mutates_external:
        raise ExecutionModeError(
            "Job dry-run lại được đánh dấu có ghi bên ngoài; từ chối thực thi."
        )
    return mode


def is_dry_run(mode: str) -> bool:
    return normalize_execution_mode(mode) == DRY_RUN
