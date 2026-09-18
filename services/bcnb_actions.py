"""Audited BCNB checkbox writes with independent server read-back."""

import asyncio
from collections.abc import Callable
import re

from logger_utils import log_message
from repositories.evidence_repository import EvidenceRepository
from services.bcnb_safety import dismiss_current_week_notice, verify_checkbox_persisted


class BcnbBeforeWriteError(RuntimeError):
    """The action stopped before the external checkbox click."""


class BcnbOutcomeUnknownError(RuntimeError):
    """The click may have happened but hard readback was unavailable."""


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
    evidence_sha256: str | None = None,
    require_evidence_registry: bool = False,
    settle_seconds: float = 0.5,
    should_halt: Callable[[], bool] | None = None,
) -> str:
    """Write once, then mark VERIFIED only after hard reload and fresh lookup."""
    normalized_sha256 = str(evidence_sha256 or "").strip().upper() or None
    if evidence_path and not (
        normalized_sha256 and re.fullmatch(r"[A-F0-9]{64}", normalized_sha256)
    ):
        raise ValueError("Ảnh minh chứng BCNB phải có SHA-256 hợp lệ.")
    if normalized_sha256 and not evidence_path:
        raise ValueError("SHA-256 minh chứng BCNB phải đi kèm đường dẫn ảnh.")
    if evidence_path:
        evidence_repo = EvidenceRepository()
        artifact = await evidence_repo.get_by_public_path(evidence_path)
        if artifact is None:
            if require_evidence_registry:
                raise ValueError("Evidence chưa được đăng ký trong registry.")
        else:
            verified_artifact = await evidence_repo.verify(
                evidence_path,
                expected_sha256=normalized_sha256,
                job_id=job_id,
            )
            if not verified_artifact or verified_artifact.get("status") != "AVAILABLE":
                raise ValueError("Evidence không khả dụng hoặc SHA-256 không khớp.")

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
        evidence_sha256=normalized_sha256,
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

    if before_checked:
        outcome = "DRY_RUN" if execution_mode != "production" else "VERIFIED"
        await audit_repo.complete_action(
            audit["id"],
            outcome=outcome,
            after_state={"checked": True},
        )
        return outcome

    if execution_mode != "production":
        await audit_repo.complete_action(
            audit["id"], outcome="DRY_RUN", after_state={"checked": before_checked}
        )
        return "DRY_RUN"

    after_checked = None
    terminal_audit_recorded = False
    write_started = False
    try:
        await job_repo.ensure_not_cancelled(job_id)
        # BCNB shows an informational SweetAlert on the current week.  It is
        # a read-only notice, but it still sits above the table and causes
        # Playwright's checkbox check to report that the state did not change.
        # Close only this known notice before attempting the audited write.
        await dismiss_current_week_notice(page)
        await job_repo.ensure_not_cancelled(job_id)
        if should_halt is not None and should_halt():
            terminal_audit_recorded = True
            await audit_repo.complete_action(
                audit["id"],
                outcome="FAILED",
                after_state={"checked": False},
                error_message="Batch halted before checkbox click.",
            )
            raise BcnbBeforeWriteError("Batch đã halt trước checkbox click.")
        write_started = True
        await checkbox.check(force=True)
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
            raise BcnbOutcomeUnknownError(
                f"Không xác minh được trạng thái {column_kind} sau hard reload."
            ) from verification_error
        if not after_checked:
            raise RuntimeError("Checkbox không giữ trạng thái sau khi hard reload.")
        await audit_repo.complete_action(
            audit["id"], outcome="VERIFIED", after_state={"checked": True}
        )
        return "VERIFIED"
    except Exception as action_error:
        if write_started and after_checked is None and not terminal_audit_recorded:
            try:
                recovered_checked = await verify_checkbox_persisted(
                    page,
                    class_code=class_code,
                    column_kind=column_kind,
                    week=week,
                    year=year,
                    lesson=lesson,
                )
            except Exception as recovery_error:
                await audit_repo.complete_action(
                    audit["id"],
                    outcome="UNKNOWN",
                    after_state={"checked": None},
                    error_message=str(recovery_error)[:500],
                )
                terminal_audit_recorded = True
                raise BcnbOutcomeUnknownError(
                    f"Không xác minh được trạng thái {column_kind} sau lỗi click."
                ) from recovery_error
            if recovered_checked:
                await audit_repo.complete_action(
                    audit["id"], outcome="VERIFIED", after_state={"checked": True}
                )
                return "VERIFIED"
            after_checked = False
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
