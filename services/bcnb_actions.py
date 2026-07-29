"""Audited BCNB checkbox writes with independent server read-back."""

import asyncio

from logger_utils import log_message
from services.bcnb_safety import verify_checkbox_persisted


async def execute_verified_checkbox_action(
    *,
    page,
    checkbox,
    audit_repo,
    job_repo,
    job_id: int,
    action_key: str,
    action_type: str,
    class_code: str,
    column_kind: str,
    week: int,
    year: int,
    execution_mode: str,
    reason: dict,
    lesson: str | None = None,
    evidence_path: str | None = None,
    settle_seconds: float = 0.5,
) -> str:
    """Write once, then mark VERIFIED only after hard reload and fresh lookup."""
    before_checked = await checkbox.is_checked()
    audit, created = await audit_repo.record_intent(
        action_key=action_key,
        job_id=job_id,
        action_type=action_type,
        target_type="CLASS",
        target_id=class_code,
        mode=execution_mode,
        before_state={"checked": before_checked},
        intended_state={"checked": True},
        reason=reason,
        evidence_path=evidence_path,
    )

    if not created:
        if audit["outcome"] in {"VERIFIED", "DRY_RUN"}:
            return audit["outcome"]
        if audit["outcome"] != "INTENT_RECORDED":
            raise RuntimeError(
                f"Audit BCNB của {class_code} ở trạng thái {audit['outcome']}; "
                "cần kiểm tra thủ công."
            )
        persisted = await verify_checkbox_persisted(
            page,
            class_code=class_code,
            column_kind=column_kind,
            week=week,
            year=year,
            lesson=lesson,
        )
        if persisted:
            await audit_repo.complete_action(
                audit["id"], outcome="VERIFIED", after_state={"checked": True}
            )
            return "VERIFIED"

    if execution_mode != "production":
        await audit_repo.complete_action(
            audit["id"], outcome="DRY_RUN", after_state={"checked": before_checked}
        )
        return "DRY_RUN"

    after_checked = None
    terminal_audit_recorded = False
    try:
        await job_repo.ensure_not_cancelled(job_id)
        await checkbox.check(force=True)
        confirm_button = page.locator(".swal2-confirm").first
        if await confirm_button.count() > 0 and await confirm_button.is_visible():
            await confirm_button.click()
        await asyncio.sleep(settle_seconds)
        try:
            after_checked = await verify_checkbox_persisted(
                page,
                class_code=class_code,
                column_kind=column_kind,
                week=week,
                year=year,
                lesson=lesson,
            )
        except Exception as verification_error:
            await audit_repo.complete_action(
                audit["id"],
                outcome="UNKNOWN",
                after_state={"checked": None},
                error_message=str(verification_error)[:500],
            )
            terminal_audit_recorded = True
            raise RuntimeError(
                f"Không xác minh được trạng thái {column_kind} sau hard reload."
            ) from verification_error
        if not after_checked:
            raise RuntimeError("Checkbox không giữ trạng thái sau khi hard reload.")
        await audit_repo.complete_action(
            audit["id"], outcome="VERIFIED", after_state={"checked": True}
        )
        return "VERIFIED"
    except Exception as action_error:
        if not terminal_audit_recorded:
            await audit_repo.complete_action(
                audit["id"],
                outcome="FAILED",
                after_state={"checked": after_checked},
                error_message=str(action_error)[:500],
            )
        log_message(
            f"Xác minh thao tác BCNB thất bại cho {class_code}: {action_error}",
            level="ERROR",
        )
        raise
