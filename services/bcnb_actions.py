"""Audited BCNB checkbox and checker-note writes with independent server read-back."""

import asyncio
from collections.abc import Callable, Sequence
import inspect
import json
import re
from typing import Any

from logger_utils import log_message
from repositories.evidence_repository import EvidenceRepository
from services.bcnb_safety import (
    BCNB_CHECKER_NOTE_HEADER_SUBSTRINGS,
    BCNB_CLASS_HEADER_EXACT,
    BCNB_CLASS_HEADER_SUBSTRINGS,
    BCNB_LESSON_HEADER_SUBSTRINGS,
    _BCNB_NOTE_PLACEHOLDERS,
    dismiss_current_week_notice,
    merge_checker_issue_note,
    preserve_bcnb_note_text,
    read_checker_note_by_session,
    resolve_row_proven_auto_issues,
    split_checker_issue_note,
    verify_checkbox_persisted,
    verify_checker_note_persisted,
)
from services.report_status import issue_text


class BcnbBeforeWriteError(RuntimeError):
    """The action stopped before the external checkbox click."""


class BcnbOutcomeUnknownError(RuntimeError):
    """The click may have happened but hard readback was unavailable."""


class BcnbConflictAfterWriteError(BcnbOutcomeUnknownError):
    """The POST completed, but server readback differed from merged_note."""


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
    evidence_job_id: int | None = None,
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
                job_id=job_id if evidence_job_id is None else evidence_job_id,
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


_UPDATE_CHECKER_NOTE_JS = (
    """
async (args) => {
  const CLASS_HEADER_SUBSTRINGS = """
    + json.dumps(list(BCNB_CLASS_HEADER_SUBSTRINGS))
    + """;
  const CLASS_HEADER_EXACT = """
    + json.dumps(list(BCNB_CLASS_HEADER_EXACT))
    + """;
  const LESSON_HEADER_SUBSTRINGS = """
    + json.dumps(list(BCNB_LESSON_HEADER_SUBSTRINGS))
    + """;
  const CHECKER_NOTE_HEADER_SUBSTRINGS = """
    + json.dumps(list(BCNB_CHECKER_NOTE_HEADER_SUBSTRINGS))
    + """;
  const NOTE_PLACEHOLDERS = """
    + json.dumps(sorted(_BCNB_NOTE_PLACEHOLDERS))
    + """;
  const normCode = (val) =>
    String(val || '')
      .trim()
      .toUpperCase()
      .replace(/[\\u2013\\u2014]/g, '-')
      .replace(/\\s+/g, '');
  const foldHeader = (val) =>
    String(val || '')
      .normalize('NFD')
      .replace(/[\\u0300-\\u036f]/g, '')
      .replace(/[\\u0111\\u0110]/g, 'd')
      .toLowerCase()
      .trim();
  const sessionId = String((args && args.sessionId) || '').trim();
  const targetCode = normCode(args && args.classCode);
  const targetLesson = String((args && args.lesson) || '').trim();
  const noteText = String((args && args.note) ?? '');
  if (!/^\\d+$/.test(sessionId)) {
    return { ok: false, stage: 'before_write', error: 'INVALID_SESSION_ID' };
  }
  const allTrs = Array.from(document.querySelectorAll('table tr'));
  const rows = allTrs.filter(
    (tr) => String(tr.getAttribute('data-session-id') || '').trim() === sessionId
  );
  if (rows.length !== 1) {
    return {
      ok: false,
      stage: 'before_write',
      error: rows.length === 0 ? 'SESSION_ROW_NOT_FOUND' : 'DUPLICATE_SESSION_ROW',
    };
  }
  const targetRow = rows[0];
  const rowCells =
    typeof targetRow.querySelectorAll === 'function'
      ? Array.from(targetRow.querySelectorAll('td'))
      : [];
  if (targetCode) {
    if (rowCells.length > 0) {
      const headerCells =
        allTrs.length > 0 && typeof allTrs[0].querySelectorAll === 'function'
          ? Array.from(allTrs[0].querySelectorAll('th, td'))
          : [];
      const classIndexes = headerCells
        .map((c, idx) => ({ idx, text: foldHeader(c.innerText || c.textContent) }))
        .filter(
          (x) =>
            CLASS_HEADER_SUBSTRINGS.some((sub) => x.text.includes(sub)) ||
            CLASS_HEADER_EXACT.includes(x.text)
        )
        .map((x) => x.idx);
      const lessonIndexes = headerCells
        .map((c, idx) => ({ idx, text: foldHeader(c.innerText || c.textContent) }))
        .filter((x) => LESSON_HEADER_SUBSTRINGS.some((sub) => x.text.includes(sub)))
        .map((x) => x.idx);
      if (classIndexes.length === 1 && classIndexes[0] < rowCells.length) {
        const observedCode = normCode(
          rowCells[classIndexes[0]].innerText || rowCells[classIndexes[0]].textContent
        );
        if (observedCode !== targetCode) {
          return { ok: false, stage: 'before_write', error: 'SESSION_ROW_CLASS_MISMATCH' };
        }
      } else {
        const anyExactCell = rowCells.some(
          (td) => normCode(td.innerText || td.textContent) === targetCode
        );
        if (!anyExactCell) {
          return { ok: false, stage: 'before_write', error: 'SESSION_ROW_CLASS_MISMATCH' };
        }
      }
      if (
        targetLesson &&
        lessonIndexes.length === 1 &&
        lessonIndexes[0] < rowCells.length
      ) {
        const observedLesson = String(
          rowCells[lessonIndexes[0]].innerText || rowCells[lessonIndexes[0]].textContent || ''
        ).trim();
        if (observedLesson !== targetLesson) {
          return { ok: false, stage: 'before_write', error: 'SESSION_ROW_LESSON_MISMATCH' };
        }
      }
    } else {
      const tokens = String(targetRow.innerText || '')
        .trim()
        .split(/\\s+/)
        .map(normCode)
        .filter(Boolean);
      if (!tokens.includes(targetCode)) {
        return { ok: false, stage: 'before_write', error: 'SESSION_ROW_CLASS_MISMATCH' };
      }
    }
  }
  const cleanDomNote = (rawVal) => {
    let txt = String(rawVal ?? '').trim();
    if (!txt) return '';
    if (txt.startsWith('"') && txt.endsWith('"')) {
      try {
        const parsed = JSON.parse(txt);
        if (typeof parsed === 'string') txt = parsed.trim();
      } catch (_e) {}
    }
    const folded = foldHeader(txt).replace(/\\s+/g, ' ');
    if (NOTE_PLACEHOLDERS.includes(folded)) {
      return '';
    }
    return txt;
  };
  const preserveDomNote = (rawVal) => {
    if (rawVal === null || rawVal === undefined) return '';
    let txt = String(rawVal);
    if (!txt) return '';
    let stripped = txt.trim();
    if (!stripped) return '';
    if (stripped.startsWith('"') && stripped.endsWith('"')) {
      try {
        const parsed = JSON.parse(stripped);
        if (typeof parsed === 'string') {
          txt = parsed;
          stripped = parsed.trim();
          if (!stripped) return '';
        }
      } catch (_e) {}
    }
    if (cleanDomNote(stripped) === '') return '';
    return txt;
  };
  if (args && typeof args.expectedExistingNote === 'string') {
    let observedCurrentNote = null;
    if (typeof targetRow.querySelector === 'function') {
      const noteEl = targetRow.querySelector('.note-cell[data-note-type="checker"]');
      if (noteEl && typeof noteEl.getAttribute === 'function') {
        const rawAttr = noteEl.getAttribute('data-current-note');
        if (rawAttr !== null && rawAttr !== undefined) {
          observedCurrentNote = preserveDomNote(rawAttr);
        } else {
          observedCurrentNote = preserveDomNote(noteEl.innerText || noteEl.textContent || '');
        }
      }
    }
    if (observedCurrentNote === null && rowCells.length > 0) {
      const headerCells =
        allTrs.length > 0 && typeof allTrs[0].querySelectorAll === 'function'
          ? Array.from(allTrs[0].querySelectorAll('th, td'))
          : [];
      const checkerIndexes = headerCells
        .map((c, idx) => ({ idx, text: foldHeader(c.innerText || c.textContent) }))
        .filter((x) =>
          CHECKER_NOTE_HEADER_SUBSTRINGS.some((sub) => x.text.includes(sub))
        )
        .map((x) => x.idx);
      if (checkerIndexes.length === 1 && checkerIndexes[0] < rowCells.length) {
        const cell = rowCells[checkerIndexes[0]];
        observedCurrentNote = preserveDomNote(cell.innerText || cell.textContent || '');
      }
    }"""
    + """
    if (
      observedCurrentNote !== null &&
      observedCurrentNote !== preserveDomNote(args.expectedExistingNote)
    ) {
      return {
        ok: false,
        stage: 'before_write',
        error: 'CONCURRENT_NOTE_EDIT',
        observed_note: observedCurrentNote,
      };
    }
  }
  let token = '';
  try {
    if (typeof csrfToken === 'string' && csrfToken.trim()) {
      token = csrfToken.trim();
    }
  } catch (_e) {}
  if (
    !token &&
    typeof window !== 'undefined' &&
    typeof window.csrfToken === 'string' &&
    window.csrfToken.trim()
  ) {
    token = window.csrfToken.trim();
  }
  if (!token) {
    const inputEl = document.querySelector('input[name="csrf_token"], meta[name="csrf-token"]');
    if (inputEl) {
      token = String(inputEl.value || inputEl.getAttribute('content') || '').trim();
    }
  }
  if (!token && document.scripts) {
    for (const s of Array.from(document.scripts)) {
      const m = String(s.textContent || '').match(/csrfToken\\s*=\\s*['"]([^'"]+)['"]/);
      if (m && m[1]) {
        token = m[1].trim();
        break;
      }
    }
  }
  if (!token) {
    return { ok: false, stage: 'before_write', error: 'MISSING_CSRF_TOKEN' };
  }
  const body = new URLSearchParams({
    action: 'update_note',
    csrf_token: token,
    session_id: sessionId,
    note_type: 'checker',
    note: noteText,
  });
  const endpoint =
    typeof window !== 'undefined' && window.location && window.location.href
      ? window.location.href
      : '/check_nhanxet.php';
  const resp = await fetch(endpoint, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
      'X-Requested-With': 'XMLHttpRequest',
    },
    body: body.toString(),
  });
  const rawText = await resp.text();
  let data = null;
  try {
    data = JSON.parse(rawText);
  } catch (_e) {}
  const success = Boolean(
    resp.ok &&
      data &&
      (data.success === true || data.status === 'success' || data.ok === true)
  );
  return {
    ok: success,
    stage: 'after_write',
    http_status: resp.status,
    error: success
      ? ''
      : String((data && (data.message || data.error)) || `HTTP_${resp.status}`).slice(0, 200),
  };
}
"""
)


async def _post_checker_note_update(
    page,
    *,
    session_id: str,
    note: str,
    class_code: str,
    lesson: str | None = None,
    expected_existing_note: str | None = None,
) -> dict[str, Any]:
    """Execute the Teaching check_nhanxet.php checker-note POST via the live page."""
    if page is None or not hasattr(page, "evaluate"):
        return {
            "ok": False,
            "stage": "before_write",
            "error": "PAGE_EVALUATE_UNAVAILABLE",
        }
    payload: dict[str, Any] = {
        "sessionId": str(session_id or "").strip(),
        "note": str(note or ""),
        "classCode": str(class_code or "").strip(),
        "lesson": str(lesson or "").strip(),
    }
    if expected_existing_note is not None:
        payload["expectedExistingNote"] = str(expected_existing_note)
    eval_call = page.evaluate(_UPDATE_CHECKER_NOTE_JS, payload)
    raw_result = await eval_call if inspect.isawaitable(eval_call) else eval_call
    if not isinstance(raw_result, dict):
        raise RuntimeError("Phản hồi cập nhật ghi chú BCNB không đúng định dạng dict.")
    return raw_result


async def execute_verified_checker_note_action(
    *,
    page,
    audit_repo,
    job_repo,
    job_id: int,
    action_key: str,
    class_code: str,
    week: int,
    year: int,
    lesson: str,
    execution_mode: str,
    existing_note: str | None,
    auto_issues: Sequence[str] | None,
    reason: dict[str, Any],
    session_id: str | None = None,
    class_date_iso: str | None = None,
    action_type: str = "BCNB_UPDATE_CHECKER_NOTE",
    settle_seconds: float = 0.3,
    should_halt: Callable[[], bool] | None = None,
    proven_auto_issues: Sequence[str] | None = None,
) -> str:
    """Audited update of ``Mô tả vấn đề (TE/TF/QC)`` with independent server read-back."""
    existing_note_readable = isinstance(existing_note, str)
    before_note = preserve_bcnb_note_text(existing_note) if existing_note_readable else ""
    clean_session_id = str(session_id or "").strip()
    resolved_proven = (
        list(proven_auto_issues)
        if proven_auto_issues is not None
        else await resolve_row_proven_auto_issues(
            audit_repo,
            class_code=class_code,
            lesson=lesson,
            week=week,
            year=year,
            session_id=clean_session_id or None,
            class_date_iso=class_date_iso,
        )
    )
    merged_note = merge_checker_issue_note(
        before_note,
        auto_issues,
        proven_auto_issues=resolved_proven,
    )
    normalized_issues = [
        issue_text(x)
        for x in (auto_issues or [])
        if issue_text(x)
    ]
    if existing_note_readable and not normalized_issues and before_note == merged_note:
        return "NO_CHANGE"
    if not existing_note_readable and not normalized_issues:
        return "NO_CHANGE"

    before_state = {
        "qc_issue_note": before_note if existing_note_readable else None,
        "bcnb_session_id": clean_session_id or None,
    }
    intended_state = {
        "qc_issue_note": merged_note,
        "bcnb_session_id": clean_session_id or None,
    }
    audit, created = await audit_repo.record_intent(
        action_key=action_key,
        job_id=job_id,
        action_type=action_type,
        target_type="CLASS",
        target_id=class_code,
        mode=execution_mode,
        before_state=before_state,
        intended_state=intended_state,
        reason=reason,
    )

    if not existing_note_readable:
        await audit_repo.complete_action(
            audit["id"],
            outcome="FAILED",
            after_state=before_state,
            error_message="Chưa đọc được ghi chú Mô tả vấn đề hiện tại trước khi ghi.",
        )
        raise BcnbBeforeWriteError(
            f"Chưa đọc được ghi chú Mô tả vấn đề hiện tại cho {class_code}/{lesson}."
        )

    if not created:
        if audit["outcome"] in {"VERIFIED", "DRY_RUN"}:
            return str(audit["outcome"])
        if audit["outcome"] != "INTENT_RECORDED":
            raise RuntimeError(
                f"Audit ghi chú BCNB của {class_code} ở trạng thái {audit['outcome']}; "
                "cần kiểm tra thủ công."
            )

    if before_note == merged_note:
        outcome = "DRY_RUN" if execution_mode != "production" else "VERIFIED"
        await audit_repo.complete_action(
            audit["id"],
            outcome=outcome,
            after_state={
                **before_state,
                "post_verified": False,
                "no_change": True,
            },
        )
        return outcome

    if not created:
        persisted, observed_existing = await verify_checker_note_persisted(
            page,
            class_code=class_code,
            week=week,
            year=year,
            expected_note=merged_note,
            lesson=lesson,
            class_date_iso=class_date_iso,
            session_id=clean_session_id or None,
        )
        if persisted:
            await audit_repo.complete_action(
                audit["id"],
                outcome="VERIFIED",
                after_state={
                    "qc_issue_note": observed_existing,
                    "bcnb_session_id": clean_session_id or None,
                    "post_verified": False,
                    "reconciled_from_intent": True,
                },
            )
            return "VERIFIED"

    if execution_mode != "production":
        await audit_repo.complete_action(
            audit["id"],
            outcome="DRY_RUN",
            after_state=before_state,
        )
        return "DRY_RUN"

    if not re.fullmatch(r"\d+", clean_session_id):
        await audit_repo.complete_action(
            audit["id"],
            outcome="FAILED",
            after_state=before_state,
            error_message="Thiếu session_id hợp lệ để cập nhật Mô tả vấn đề BCNB.",
        )
        raise BcnbBeforeWriteError(
            f"Thiếu session_id hợp lệ cho {class_code}/{lesson} trước khi ghi Mô tả vấn đề."
        )

    observed_after: str | None = None
    terminal_audit_recorded = False
    write_started = False
    try:
        await job_repo.ensure_not_cancelled(job_id)
        await dismiss_current_week_notice(page)
        await job_repo.ensure_not_cancelled(job_id)
        if should_halt is not None and should_halt():
            terminal_audit_recorded = True
            await audit_repo.complete_action(
                audit["id"],
                outcome="FAILED",
                after_state=before_state,
                error_message="Batch halted before checker note update.",
            )
            raise BcnbBeforeWriteError("Batch đã halt trước khi cập nhật ghi chú BCNB.")

        pre_inspected, pre_observed = await read_checker_note_by_session(
            page,
            session_id=clean_session_id,
            class_code=class_code,
            lesson=lesson,
        )
        if pre_inspected:
            if pre_observed is None:
                terminal_audit_recorded = True
                await audit_repo.complete_action(
                    audit["id"],
                    outcome="FAILED",
                    after_state=before_state,
                    error_message="Không đọc lại được ô Mô tả vấn đề theo session_id trước khi POST.",
                )
                raise BcnbBeforeWriteError(
                    f"Không đọc lại được ô Mô tả vấn đề của {class_code}/{lesson} trước khi POST."
                )
            if preserve_bcnb_note_text(pre_observed) != before_note:
                terminal_audit_recorded = True
                await audit_repo.complete_action(
                    audit["id"],
                    outcome="FAILED",
                    after_state={
                        "qc_issue_note": preserve_bcnb_note_text(pre_observed),
                        "bcnb_session_id": clean_session_id,
                    },
                    error_message="QC vừa chỉnh sửa ô Mô tả vấn đề trước khi ghi.",
                )
                raise BcnbBeforeWriteError(
                    f"QC vừa chỉnh sửa ô Mô tả vấn đề của {class_code}/{lesson} trước khi ghi."
                )

        write_started = True
        post_result = await _post_checker_note_update(
            page,
            session_id=clean_session_id,
            note=merged_note,
            class_code=class_code,
            lesson=lesson,
            expected_existing_note=before_note,
        )
        if post_result.get("stage") == "before_write":
            write_started = False
            err_code = str(post_result.get("error") or "PRECHECK_FAILED")
            observed_concurrent = post_result.get("observed_note")
            terminal_audit_recorded = True
            err_msg = (
                "QC vừa chỉnh sửa ô Mô tả vấn đề trước khi ghi (CONCURRENT_NOTE_EDIT)."
                if err_code == "CONCURRENT_NOTE_EDIT"
                else f"Dừng trước khi gửi update_note: {err_code}"[:500]
            )
            await audit_repo.complete_action(
                audit["id"],
                outcome="FAILED",
                after_state=(
                    {
                        "qc_issue_note": preserve_bcnb_note_text(observed_concurrent),
                        "bcnb_session_id": clean_session_id,
                    }
                    if isinstance(observed_concurrent, str)
                    else before_state
                ),
                error_message=err_msg,
            )
            raise BcnbBeforeWriteError(
                f"Dừng trước khi gửi update_note cho {class_code}: {err_code}"
            )

        await asyncio.sleep(settle_seconds)
        try:
            persisted, observed_after = await verify_checker_note_persisted(
                page,
                class_code=class_code,
                week=week,
                year=year,
                expected_note=merged_note,
                lesson=lesson,
                class_date_iso=class_date_iso,
                session_id=clean_session_id,
            )
        except Exception as verification_error:
            await audit_repo.complete_action(
                audit["id"],
                outcome="UNKNOWN",
                after_state={
                    "qc_issue_note": None,
                    "bcnb_session_id": clean_session_id,
                },
                error_message=str(verification_error)[:500],
            )
            terminal_audit_recorded = True
            raise BcnbOutcomeUnknownError(
                f"Không xác minh được ghi chú Mô tả vấn đề của {class_code} sau hard reload."
            ) from verification_error

        if not persisted:
            post_err = str(post_result.get("error") or "").strip()
            detail = f" ({post_err})" if post_err else ""
            conflict_msg = (
                f"CONFLICT_AFTER_WRITE: Ghi chú Mô tả vấn đề trên server khác giá trị kỳ vọng sau hard reload{detail}."
            )
            await audit_repo.complete_action(
                audit["id"],
                outcome="UNKNOWN",
                after_state={
                    "qc_issue_note": observed_after,
                    "bcnb_session_id": clean_session_id,
                    "conflict_status": "CONFLICT_AFTER_WRITE",
                },
                error_message=conflict_msg[:500],
            )
            terminal_audit_recorded = True
            raise BcnbConflictAfterWriteError(conflict_msg)

        await audit_repo.complete_action(
            audit["id"],
            outcome="VERIFIED",
            after_state={
                "qc_issue_note": observed_after,
                "bcnb_session_id": clean_session_id,
                "post_verified": True,
            },
        )
        return "VERIFIED"
    except Exception as action_error:
        if write_started and observed_after is None and not terminal_audit_recorded:
            try:
                recovered_ok, recovered_note = await verify_checker_note_persisted(
                    page,
                    class_code=class_code,
                    week=week,
                    year=year,
                    expected_note=merged_note,
                    lesson=lesson,
                    class_date_iso=class_date_iso,
                    session_id=clean_session_id,
                )
            except Exception as recovery_error:
                await audit_repo.complete_action(
                    audit["id"],
                    outcome="UNKNOWN",
                    after_state={
                        "qc_issue_note": None,
                        "bcnb_session_id": clean_session_id,
                    },
                    error_message=str(recovery_error)[:500],
                )
                terminal_audit_recorded = True
                raise BcnbOutcomeUnknownError(
                    f"Không xác minh được ghi chú Mô tả vấn đề của {class_code} sau lỗi POST."
                ) from recovery_error
            if recovered_ok:
                await audit_repo.complete_action(
                    audit["id"],
                    outcome="VERIFIED",
                    after_state={
                        "qc_issue_note": recovered_note,
                        "bcnb_session_id": clean_session_id,
                        "post_verified": True,
                    },
                )
                return "VERIFIED"
            observed_after = recovered_note
            conflict_msg = (
                f"CONFLICT_AFTER_WRITE: Ghi chú Mô tả vấn đề của {class_code} khác giá trị kỳ vọng sau lỗi POST."
            )
            await audit_repo.complete_action(
                audit["id"],
                outcome="UNKNOWN",
                after_state={
                    "qc_issue_note": observed_after,
                    "bcnb_session_id": clean_session_id,
                    "conflict_status": "CONFLICT_AFTER_WRITE",
                },
                error_message=conflict_msg[:500],
            )
            terminal_audit_recorded = True
            raise BcnbConflictAfterWriteError(conflict_msg) from action_error
        if not terminal_audit_recorded:
            await audit_repo.complete_action(
                audit["id"],
                outcome="FAILED",
                after_state={
                    "qc_issue_note": (
                        observed_after if write_started else before_note
                    ),
                    "bcnb_session_id": clean_session_id or None,
                },
                error_message=str(action_error)[:500],
            )
        log_message(
            f"Xác minh ghi chú Mô tả vấn đề BCNB thất bại cho {class_code}: {action_error}",
            level="ERROR",
        )
        raise


__all__ = [
    "BcnbBeforeWriteError",
    "BcnbConflictAfterWriteError",
    "BcnbOutcomeUnknownError",
    "execute_verified_checkbox_action",
    "execute_verified_checker_note_action",
    "merge_checker_issue_note",
    "split_checker_issue_note",
]
