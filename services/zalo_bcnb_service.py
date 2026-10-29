"""Week-aware BCNB matching and audited report checkbox actions."""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import datetime
import json
import os
import re
from typing import Any

from core.time_utils import business_day_start, now_business, now_utc, utc_iso
from db.backup import backup_job
from db.database import get_db_connection
from googleform_autosubmit.config import Config
from logger_utils import log_message
from repositories.audit_repository import AuditRepository
from repositories.evidence_repository import EvidenceRepository
from repositories.job_repository import JobCancelledError, JobRepository
from repositories.zalo_batch_repository import canonical_target_key
from services.bcnb_actions import (
    BcnbOutcomeUnknownError,
    execute_verified_checkbox_action,
    execute_verified_checker_note_action,
)
from services.bcnb_safety import (
    clean_bcnb_note_text,
    extract_bcnb_extended_row_fields,
    merge_checker_issue_note,
    resolve_extended_bcnb_columns,
    resolve_report_columns,
    resolve_schedule_columns,
    select_week_checked,
    split_checker_issue_note,
)
from services.browser_job_guard import exclusive_browser_job
from services.external_gateways import ExternalGateways
from services.lms_quality_service import inspect_and_attach_quality_issues
from services.zalo_discovery_matching import find_bcnb_match
from services.zalo_evidence import (
    LESSON_14_EXEMPTION,
    apply_lesson_14_exemption,
    get_evidence_reference,
)
from services.zalo_safety import parse_class_date
from services.zalo_target_eligibility import (
    evaluate_scan_target,
    scan_target_identity,
)


def _parse_bcnb_date(value: str, *, today: datetime) -> datetime | None:
    """Extract a date from a BCNB cell such as ``18:00\n10/08/2026``."""
    iso_match = re.fullmatch(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*", value or "")
    if iso_match:
        try:
            return parse_class_date(
                f"{int(iso_match.group(3)):02d}/{int(iso_match.group(2)):02d}/{iso_match.group(1)}",
                default_year=today.year,
                timezone_info=today.tzinfo,
            )
        except (TypeError, ValueError):
            return None
    matches = re.findall(
        r"(?<!\d)(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?(?!\d)",
        value or "",
    )
    if not matches:
        return None
    day, month, year = matches[-1]
    year_value = year or str(today.year)
    if len(year_value) == 2:
        year_value = f"20{year_value}"
    try:
        return parse_class_date(
            f"{int(day):02d}/{int(month):02d}/{int(year_value):04d}",
            default_year=today.year,
            timezone_info=today.tzinfo,
        )
    except (TypeError, ValueError):
        return None


def _result_target_week(result: dict[str, Any]) -> tuple[int, int] | None:
    context = result.get("evidence", {}).get("date_context", {}) or {}
    try:
        week_value = context.get("target_week")
        year_value = context.get("target_year")
        if week_value is None or year_value is None:
            raise ValueError("missing target week")
        week = int(str(week_value))
        year = int(str(year_value))
        if 1 <= week <= 53 and 2000 < year <= 2100:
            return week, year
    except (TypeError, ValueError):
        return None
    return None


def _result_period_pair(result: dict[str, Any]) -> tuple[int, int] | None:
    pair = _result_target_week(result)
    if pair is not None:
        return pair
    st = result.get("scan_target")
    st_dict = st if isinstance(st, dict) else {}
    for w_cand, y_cand in (
        (st_dict.get("week"), st_dict.get("year")),
        (result.get("week"), result.get("year")),
    ):
        try:
            if w_cand is None or y_cand is None:
                continue
            week = int(str(w_cand))
            year = int(str(y_cand))
            if 1 <= week <= 53 and 2000 < year <= 2100:
                return week, year
        except (TypeError, ValueError):
            continue
    return None


def _result_lesson_date(result: dict[str, Any]) -> str | None:
    context = result.get("evidence", {}).get("date_context", {}) or {}
    value = str(
        context.get("matched_lesson_date")
        or context.get("target_class_date")
        or context.get("lesson_date")
        or ""
    ).strip()
    return value or None


def _result_session_id(result: dict[str, Any]) -> str:
    st = result.get("scan_target")
    st_dict = st if isinstance(st, dict) else {}
    return str(
        result.get("bcnb_session_id") or st_dict.get("bcnb_session_id") or ""
    ).strip()


def _result_matches_schedule_row(
    result: dict[str, Any],
    row: dict[str, Any],
    pair: tuple[int, int],
) -> bool:
    """Match a report to one week/date row; never match by class code alone."""
    res_sid = _result_session_id(result)
    row_sid = str(row.get("bcnb_session_id") or "").strip()
    if res_sid and row_sid and res_sid != row_sid:
        return False
    if not find_bcnb_match(result.get("group", ""), [row]):
        return False
    if _result_target_week(result) != pair:
        return False
    lesson_date = _result_lesson_date(result)
    if lesson_date and row.get("ngay_hoc_iso") and lesson_date != row["ngay_hoc_iso"]:
        return False
    target_lesson = str(
        (result.get("evidence", {}).get("date_context", {}) or {}).get(
            "target_lesson"
        )
        or ""
    ).strip()
    if target_lesson and target_lesson != str(row.get("buoi_so") or "").strip():
        return False
    return True


def _result_matches_bcnb_row_for_metadata(
    result: dict[str, Any],
    row: dict[str, Any],
    pair: tuple[int, int],
) -> bool:
    """Match a scan result to a BCNB row for read-only metadata sync using full target identity."""
    if _result_matches_schedule_row(result, row, pair):
        return True
    st = result.get("scan_target")
    if not isinstance(st, dict):
        return False
    res_sid = _result_session_id(result)
    row_sid = str(row.get("bcnb_session_id") or "").strip()
    if res_sid and row_sid and res_sid != row_sid:
        return False
    target_pair = _result_target_week(result)
    if target_pair is None:
        try:
            w_val = int(str(st.get("week")))
            y_val = int(str(st.get("year")))
            if 1 <= w_val <= 53 and 2000 < y_val <= 2100:
                target_pair = (w_val, y_val)
        except (TypeError, ValueError):
            target_pair = None
    if target_pair != pair:
        return False
    row_code = str(row.get("ma_lop") or "").strip().upper()
    st_code = str(st.get("class_code") or "").strip().upper()
    if not row_code:
        return False
    if st_code:
        if st_code != row_code:
            return False
    elif not find_bcnb_match(str(result.get("group") or ""), [row]):
        return False
    row_lesson = str(row.get("buoi_so") or "").strip()
    st_lesson = str(st.get("lesson") or "").strip()
    if not row_lesson or not st_lesson or st_lesson != row_lesson:
        return False
    row_date_iso = str(row.get("ngay_hoc_iso") or "").strip()
    st_date_raw = str(st.get("class_date_iso") or st.get("class_date") or "").strip()
    dm_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", st_date_raw)
    st_date_iso = (
        f"{int(dm_match.group(3)):04d}-{int(dm_match.group(2)):02d}-{int(dm_match.group(1)):02d}"
        if dm_match
        else st_date_raw
    )
    return bool(row_date_iso and st_date_iso and st_date_iso == row_date_iso)


def _apply_lesson_14_result(
    scan_results: list[dict[str, Any]],
    row: dict[str, Any],
) -> dict[str, Any]:
    """Replace a stale/renamed lesson-14 scan result with one exemption row.

    A class can finish lesson 14 under its old Zalo name and be renamed before
    the next course starts.  The old configured group may therefore produce a
    fail-closed identity warning even though BCNB already exempts that row.
    Preserve the audit trail, but expose one business exemption instead of
    leaving a duplicate manual-review result beside it.
    """
    class_code = str(row.get("ma_lop") or "").strip()
    try:
        row_pair = (int(row["week"]), int(row["year"]))
    except (KeyError, TypeError, ValueError):
        row_pair = None
    if row_pair is not None:
        matches = [
            result
            for result in scan_results
            if _result_matches_schedule_row(result, row, row_pair)
        ]
    else:
        matches = [
            result
            for result in scan_results
            if find_bcnb_match(str(result.get("group") or ""), [row])
        ]
    if len(matches) > 1:
        raise RuntimeError(
            f"Dòng {class_code}/14 khớp nhiều kết quả Zalo; "
            "dừng để tránh gắn nhầm miễn báo cáo."
        )
    source = matches[0] if matches else None
    exempted = apply_lesson_14_exemption(
        source,
        class_code=class_code,
        applied_at=now_utc(),
    )
    if source is None:
        scan_results.append(exempted)
    else:
        scan_results[scan_results.index(source)] = exempted
    return exempted


def _apply_bcnb_deadline_state(
    result: dict[str, Any],
    row: dict[str, Any],
    *,
    today: datetime,
) -> bool:
    """Reconcile timing against the exact BCNB deadline row."""
    evidence = result.setdefault("evidence", {})
    context = evidence.setdefault("date_context", {})
    context.update(
        {
            "bcnb_class_date": row.get("ngay_hoc_iso"),
            "bcnb_zalo_deadline": row.get("zalo_deadline"),
        }
    )
    classifier = evidence.setdefault("classifier_result", {})
    sent_text = classifier.get("matched_ui_sent_at")
    sent_date = _parse_bcnb_date(str(sent_text or ""), today=today)
    deadline_date = _parse_bcnb_date(str(row.get("zalo_deadline") or ""), today=today)
    if sent_date is None or deadline_date is None:
        context["deadline_verification"] = "UNREADABLE"
        return False

    delay = (sent_date.date() - deadline_date.date()).days
    context.update(
        {
            "report_sent_at": sent_date.date().isoformat(),
            "deadline_date": deadline_date.date().isoformat(),
            "deadline_delay_days": delay,
            "deadline_verification": "DATE_LEVEL",
        }
    )
    candidates = classifier.get("candidates") or []
    if candidates:
        candidates[-1].update(
            {
                "deadline_date": deadline_date.date().isoformat(),
                "deadline_delay_days": delay,
            }
        )
    result["submitted_for_bcnb"] = True
    result["evidence_state"] = (
        "SUBMITTED_LATE_VERIFIED" if delay > 0 else "SUBMITTED_VERIFIED"
    )
    result["status"] = (
        "🟡 Đã nộp trễ – Có minh chứng" if delay > 0 else "✅ Đã nộp – Có minh chứng"
    )
    status_detail = (
        f"Nộp {sent_date.strftime('%d/%m')} – Trễ {delay} ngày"
        if delay > 0
        else f"Nộp {sent_date.strftime('%d/%m')} đúng hạn"
    )
    target_match_note = str(context.get("target_match_note") or "").strip()
    result["status_detail"] = (
        f"{status_detail}; {target_match_note}" if target_match_note else status_detail
    )
    classifier["decision"] = "SUBMITTED_LATE" if delay > 0 else "SUBMITTED_ON_TIME"
    return True


async def _read_bcnb_schedule_rows(
    page,
    *,
    week: int,
    year: int,
    job_id: int,
    today: datetime,
    job_repo,
) -> list[dict[str, Any]]:
    """Select and read one BCNB week into an auditable in-memory snapshot."""
    await job_repo.ensure_not_cancelled(job_id)
    await select_week_checked(page, week, year)
    class_index, lesson_index, date_index, deadline_index = await resolve_schedule_columns(page)
    ext_cols = await resolve_extended_bcnb_columns(page)
    rows = await page.locator("table tr").all()
    snapshot: list[dict[str, Any]] = []
    for row in rows:
        await job_repo.ensure_not_cancelled(job_id)
        cells = await row.locator("td").all()
        required_index = max(class_index, lesson_index, date_index, deadline_index)
        if len(cells) <= required_index:
            continue
        class_code = (await cells[class_index].inner_text()).strip()
        class_date = (await cells[date_index].inner_text()).strip()
        if not class_code or not class_date:
            continue
        parsed_class_date = _parse_bcnb_date(class_date, today=today)
        iso_date = parsed_class_date.date().isoformat() if parsed_class_date else None
        ext_fields = await extract_bcnb_extended_row_fields(
            row, cells, ext_cols, include_empty=False
        )
        checker_note_unreadable = "qc_issue_note" not in ext_fields or not isinstance(
            ext_fields.get("qc_issue_note"), str
        )
        snapshot.append(
            {
                "row": row,
                "cells": cells,
                "week": week,
                "year": year,
                "ma_lop": class_code,
                "buoi_so": (await cells[lesson_index].inner_text()).strip(),
                "ngay_hoc": class_date,
                "ngay_hoc_iso": iso_date,
                "zalo_deadline": (await cells[deadline_index].inner_text()).strip(),
                "checker_note_unreadable": checker_note_unreadable,
                **ext_fields,
            }
        )
    return snapshot


def _attach_bcnb_row_metadata(
    result: dict[str, Any],
    bcnb_row: dict[str, Any],
) -> None:
    """Attach observed BCNB row metadata (LMS status, notes, session_id) to a scan result."""
    st = result.get("scan_target")
    st_dict = st if isinstance(st, dict) else None
    for str_key in (
        "bcnb_session_id",
        "khoi",
        "lms_deadline",
        "evaluation_status",
    ):
        val = str(bcnb_row.get(str_key) or "").strip()
        if val:
            result[str_key] = val
            if st_dict is not None:
                st_dict[str_key] = val
    for note_key in ("qc_issue_note", "instructor_note"):
        if note_key in bcnb_row and bcnb_row.get(note_key) is not None:
            note_val = str(bcnb_row.get(note_key) or "").strip()
            result[note_key] = note_val
            if st_dict is not None:
                st_dict[note_key] = note_val
    for bool_key in ("nx_zalo_checked", "nx_lms_checked"):
        bool_val = bcnb_row.get(bool_key)
        if isinstance(bool_val, bool):
            result[bool_key] = bool(bool_val)
            if st_dict is not None:
                st_dict[bool_key] = bool(bool_val)


def _collect_row_verifiable_auto_issues(
    result: dict[str, Any],
    *,
    existing_note: str | None,
    lms_slots_by_class: dict[str, list[dict[str, Any]]] | None,
) -> tuple[list[str], bool]:
    """Return ``(effective_auto_issues, should_sync_note)`` for one BCNB row."""
    _, existing_auto = split_checker_issue_note(existing_note)
    raw_zalo = [
        str(x).strip()
        for x in (result.get("zalo_content_issues") or [])
        if str(x).strip()
    ]
    zalo_auto = [x for x in raw_zalo if "cần kiểm tra thủ công" not in x]

    raw_lms = [
        str(x).strip()
        for x in (result.get("lms_quality_issues") or [])
        if str(x).strip()
    ]
    lms_indeterminate = (lms_slots_by_class is None) or any(
        "cần kiểm tra thủ công" in x for x in raw_lms
    )
    if lms_indeterminate:
        lms_auto = [x for x in existing_auto if x.upper().startswith("[LMS]")]
    else:
        lms_auto = [x for x in raw_lms if "cần kiểm tra thủ công" not in x]

    combined = [*zalo_auto, *lms_auto]
    before_clean = clean_bcnb_note_text(existing_note)
    merged = merge_checker_issue_note(before_clean, combined)
    should_sync = bool(combined) or (bool(existing_auto) and merged != before_clean)
    return combined, should_sync



async def update_bcnb_reports(
    page,
    *,
    job_id: int,
    execution_mode: str,
    scan_results: list[dict[str, Any]],
    today: datetime,
    now: datetime | None = None,
    scan_plan: dict | None = None,
    job_repo,
    audit_repo,
    period_filter: set[tuple[int, int]] | None = None,
    should_halt: Callable[[], bool] | None = None,
    on_target_outcome: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    lms_quality_gateway: Any = None,
    lms_slots_by_class: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Update both bounded weeks with exact-row matching and server read-back."""
    await job_repo.ensure_not_cancelled(job_id)
    if scan_plan:
        raw_weeks = scan_plan.get("selected_weeks") or []
        week_pairs = []
        for item in raw_weeks:
            if not isinstance(item, dict):
                continue
            try:
                pair = (int(item["week"]), int(item["year"]))
            except (KeyError, TypeError, ValueError):
                continue
            if pair not in week_pairs:
                week_pairs.append(pair)
        if len(week_pairs) != 2:
            raise RuntimeError("Kế hoạch scan BCNB phải có đúng hai tuần.")
        latest_block = scan_plan.get("latest_week") or {}
        try:
            latest_pair = (int(latest_block["week"]), int(latest_block["year"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Kế hoạch scan thiếu tuần BCNB mới nhất.") from exc
        active_pairs = [
            pair for pair in week_pairs if period_filter is None or pair in period_filter
        ]
        if period_filter is not None and not active_pairs:
            raise RuntimeError("Worker BCNB không được phân công tuần hợp lệ.")
    else:
        raise RuntimeError(
            "Không có kế hoạch tuần BCNB đã đọc từ máy chủ; từ chối suy tuần từ ngày hệ thống."
        )
    target_by_identity: dict[tuple[int, int, str, str, str], dict[str, Any]] = {}
    for source in (
        *(scan_plan.get("targets") or []),
        *(scan_plan.get("deferred_targets") or []),
    ):
        if not isinstance(source, dict):
            continue
        identity = scan_target_identity(source)
        if identity is not None:
            target_by_identity.setdefault(identity, source)
    current_time = now or today
    selected_target_keys_raw = (
        scan_plan.get("selected_target_keys") if scan_plan else None
    )
    selected_target_key_set: set[str] | None = (
        {str(key).strip() for key in selected_target_keys_raw if str(key).strip()}
        if isinstance(selected_target_keys_raw, list)
        else None
    )
    target_pairs: set[tuple[int, int]] = set()
    for result in scan_results:
        result_pair = _result_target_week(result)
        if result_pair is not None and result_pair in week_pairs:
            target_pairs.add(result_pair)
    # Buổi 14 is a business exemption and does not require a Zalo result,
    # except during a selective rescan where only explicitly selected targets may be touched.
    if selected_target_key_set is None:
        target_pairs.update(active_pairs)
    else:
        for source in scan_plan.get("targets") or []:
            if isinstance(source, dict):
                try:
                    pair = (int(source["week"]), int(source["year"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if pair in active_pairs:
                    target_pairs.add(pair)
    failures: list[dict[str, Any]] = []
    target_outcomes: list[dict[str, Any]] = []
    checker_note_outcomes: list[dict[str, Any]] = []

    async def emit_outcome(item: dict[str, Any]) -> None:
        target_outcomes.append(item)
        if on_target_outcome is not None:
            await on_target_outcome(item)

    updated_count = 0
    checker_note_updated_count = 0

    scoped_results = [
        r
        for r in scan_results
        if isinstance(r, dict)
        and (
            period_filter is None
            or _result_period_pair(r) in active_pairs
        )
    ]
    resolved_lms_slots: dict[str, list[dict[str, Any]]] | None = (
        dict(lms_slots_by_class) if isinstance(lms_slots_by_class, dict) else None
    )
    active_lms_gateway = lms_quality_gateway
    if (
        active_lms_gateway is None
        and resolved_lms_slots is None
        and os.getenv("ZALO_QUALITY_ISOLATED") != "1"
        and page is not None
        and hasattr(page, "evaluate")
    ):
        try:
            active_lms_gateway = getattr(
                ExternalGateways.production(), "lms_quality", None
            )
        except Exception:
            active_lms_gateway = None
    if resolved_lms_slots is None and active_lms_gateway is not None:
        candidate_codes = sorted(
            {
                str(
                    (r.get("scan_target") or {}).get("class_code")
                    if isinstance(r.get("scan_target"), dict)
                    else r.get("class_code") or ""
                )
                .strip()
                .upper()
                for r in scoped_results
            }
            - {""}
        )
        if candidate_codes and hasattr(active_lms_gateway, "fetch_class_slots"):
            try:
                fetched = await active_lms_gateway.fetch_class_slots(
                    page, candidate_codes
                )
                if isinstance(fetched, dict):
                    resolved_lms_slots = fetched
                else:
                    resolved_lms_slots = {
                        code: [{"fetch_error": True}] for code in candidate_codes
                    }
            except Exception:
                resolved_lms_slots = {
                    code: [{"fetch_error": True}] for code in candidate_codes
                }
        else:
            resolved_lms_slots = {}
    inspect_and_attach_quality_issues(
        scoped_results,
        lms_slots_by_class=resolved_lms_slots,
    )

    async def _maybe_update_row_checker_note(
        target_result: dict[str, Any] | None,
        bcnb_row: dict[str, Any],
        *,
        selected_week: int,
        selected_year: int,
    ) -> None:
        nonlocal checker_note_updated_count
        if (
            target_result is None
            or audit_repo is None
            or not hasattr(audit_repo, "record_intent")
        ):
            return
        row_lesson = str(bcnb_row.get("buoi_so") or "").strip()
        if row_lesson == "14":
            return
        inspect_and_attach_quality_issues(
            [target_result],
            lms_slots_by_class=resolved_lms_slots,
        )
        has_readable_note = (
            isinstance(bcnb_row.get("qc_issue_note"), str)
            and not bool(bcnb_row.get("checker_note_unreadable"))
        )
        existing_note = (
            str(bcnb_row["qc_issue_note"])
            if has_readable_note
            else str(target_result.get("qc_issue_note") or "")
        )
        effective_auto, should_sync = _collect_row_verifiable_auto_issues(
            target_result,
            existing_note=existing_note,
            lms_slots_by_class=resolved_lms_slots,
        )
        if not should_sync:
            return
        row_code = str(bcnb_row.get("ma_lop") or "").strip().upper()
        row_date_iso = str(bcnb_row.get("ngay_hoc_iso") or "").strip()
        row_sid = str(
            bcnb_row.get("bcnb_session_id")
            or _result_session_id(target_result)
            or ""
        ).strip()
        if not has_readable_note:
            err_msg = (
                "Không đọc được ô Mô tả vấn đề hiện tại trên BCNB; "
                "dừng để bảo toàn ghi chú tay của QC."
            )
            failure = {
                "class_code": row_code,
                "lesson": row_lesson,
                "week": selected_week,
                "year": selected_year,
                "action_type": "BCNB_UPDATE_CHECKER_NOTE",
                "error": err_msg,
            }
            failures.append(failure)
            unreadable_outcome = {
                **failure,
                "class_date_iso": row_date_iso or None,
                "status": "FAILED_BEFORE_WRITE",
            }
            checker_note_outcomes.append(unreadable_outcome)
            target_result["checker_note_outcome"] = "FAILED"
            if on_target_outcome is not None:
                await on_target_outcome(unreadable_outcome)
            return
        if should_halt is not None and should_halt():
            halt_outcome = {
                "class_code": row_code,
                "lesson": row_lesson,
                "week": selected_week,
                "year": selected_year,
                "class_date_iso": row_date_iso or None,
                "action_type": "BCNB_UPDATE_CHECKER_NOTE",
                "status": "BLOCKED_BATCH_HALT",
                "error": "Batch đã halt trước khi cập nhật Mô tả vấn đề.",
            }
            checker_note_outcomes.append(halt_outcome)
            target_result["checker_note_outcome"] = "BLOCKED_BATCH_HALT"
            return

        merged_note = merge_checker_issue_note(existing_note, effective_auto)
        note_action_key = (
            f"bcnb-checker-note:{job_id}:{selected_year}:W{selected_week}:"
            f"{row_code}:{row_lesson}:{row_sid or row_date_iso or 'na'}"
        )
        try:
            note_outcome = await execute_verified_checker_note_action(
                page=page,
                audit_repo=audit_repo,
                job_repo=job_repo,
                job_id=job_id,
                action_key=note_action_key,
                class_code=row_code,
                week=selected_week,
                year=selected_year,
                lesson=row_lesson,
                execution_mode=execution_mode,
                existing_note=existing_note,
                auto_issues=effective_auto,
                session_id=row_sid or None,
                class_date_iso=row_date_iso or None,
                should_halt=should_halt,
                reason={
                    "week": selected_week,
                    "year": selected_year,
                    "lesson": row_lesson,
                    "class_date": row_date_iso or None,
                    "bcnb_session_id": row_sid or None,
                    "auto_issues": effective_auto,
                    "existing_note": clean_bcnb_note_text(existing_note),
                    "merged_note": merged_note,
                },
            )
        except JobCancelledError:
            raise
        except Exception as note_error:
            unknown = isinstance(note_error, BcnbOutcomeUnknownError)
            failure = {
                "class_code": row_code,
                "lesson": row_lesson,
                "week": selected_week,
                "year": selected_year,
                "action_type": "BCNB_UPDATE_CHECKER_NOTE",
                "error": str(note_error)[:500],
            }
            failures.append(failure)
            err_outcome = {
                **failure,
                "class_date_iso": row_date_iso or None,
                "status": (
                    "OUTCOME_UNKNOWN"
                    if execution_mode == "production" and unknown
                    else "FAILED_BEFORE_WRITE"
                ),
            }
            checker_note_outcomes.append(err_outcome)
            target_result["checker_note_outcome"] = (
                "OUTCOME_UNKNOWN"
                if execution_mode == "production" and unknown
                else "FAILED"
            )
            if on_target_outcome is not None:
                await on_target_outcome(err_outcome)
            log_message(
                f"Bỏ qua lỗi cập nhật Mô tả vấn đề BCNB {row_code}/{row_lesson} "
                f"W{selected_week}/{selected_year}: {note_error}",
                level="ERROR",
                event="bcnb.checker_note_failed",
                class_code=row_code,
                lesson=row_lesson,
                week=selected_week,
                year=selected_year,
            )
            return

        if note_outcome == "NO_CHANGE":
            return
        checker_note_updated_count += 1
        target_result["checker_note_outcome"] = note_outcome
        if note_outcome == "VERIFIED":
            target_result["qc_issue_note"] = merged_note
            if isinstance(target_result.get("scan_target"), dict):
                target_result["scan_target"]["qc_issue_note"] = merged_note
        else:
            target_result["planned_qc_issue_note"] = merged_note
        checker_note_outcomes.append(
            {
                "class_code": row_code,
                "lesson": row_lesson,
                "week": selected_week,
                "year": selected_year,
                "class_date_iso": row_date_iso or None,
                "action_type": "BCNB_UPDATE_CHECKER_NOTE",
                "status": note_outcome,
            }
        )

    log_message(
        "Đối chiếu BCNB theo các tuần: "
        + ", ".join(f"W{week}/{year}" for week, year in active_pairs)
    )

    for selected_week, selected_year in active_pairs:
        await job_repo.ensure_not_cancelled(job_id)
        if (selected_week, selected_year) not in target_pairs:
            continue
        bcnb_rows = await _read_bcnb_schedule_rows(
            page,
            week=selected_week,
            year=selected_year,
            job_id=job_id,
            today=today,
            job_repo=job_repo,
        )
        _, _, report_index = await resolve_report_columns(page)
        is_current_week = (selected_week, selected_year) == latest_pair
        log_message(
            f"Đã kiểm chứng BCNB ở Tuần {selected_week} / {selected_year}: "
            f"{len(bcnb_rows)} dòng."
        )

        for bcnb_row in bcnb_rows:
            await job_repo.ensure_not_cancelled(job_id)
            if selected_target_key_set is not None:
                try:
                    row_canonical_key = canonical_target_key(
                        {
                            "week": selected_week,
                            "year": selected_year,
                            "class_code": bcnb_row.get("ma_lop"),
                            "lesson": bcnb_row.get("buoi_so"),
                            "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                        }
                    )
                except ValueError:
                    continue
                if row_canonical_key not in selected_target_key_set:
                    continue
            if is_current_week:
                row_identity = scan_target_identity(
                    {
                        "week": selected_week,
                        "year": selected_year,
                        "class_code": bcnb_row.get("ma_lop"),
                        "lesson": bcnb_row.get("buoi_so"),
                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                    }
                )
                scheduled_target = (
                    target_by_identity.get(row_identity) if row_identity is not None else None
                )
                if scheduled_target is None:
                    # A current-day or future row without an immutable target
                    # cannot be safely timed or matched to a Zalo result.
                    if (
                        not bcnb_row.get("ngay_hoc_iso")
                        or str(bcnb_row["ngay_hoc_iso"]) >= current_time.date().isoformat()
                    ):
                        log_message(
                            f"Bỏ qua {bcnb_row['ma_lop']}/{bcnb_row['buoi_so']}: "
                            "chưa có target Zalo đủ giờ học để đối chiếu.",
                            level="INFO",
                        )
                        continue
                else:
                    eligibility = evaluate_scan_target(
                        scheduled_target,
                        latest_week=latest_pair,
                        now=current_time,
                    )
                    if not eligibility.eligible:
                        log_message(
                            f"Bỏ qua {bcnb_row['ma_lop']}/{bcnb_row['buoi_so']}: "
                            f"{eligibility.reason}; chưa quét và không tick BCNB.",
                            level="INFO",
                        )
                        continue
            if execution_mode == "production":
                refreshed_rows = await _read_bcnb_schedule_rows(
                    page,
                    week=selected_week,
                    year=selected_year,
                    job_id=job_id,
                    today=today,
                    job_repo=job_repo,
                )
                _, _, report_index = await resolve_report_columns(page)
                row_sid = str(bcnb_row.get("bcnb_session_id") or "").strip()
                refreshed_candidates = [
                    item
                    for item in refreshed_rows
                    if item["ma_lop"].strip().upper()
                    == bcnb_row["ma_lop"].strip().upper()
                    and item["buoi_so"].strip() == bcnb_row["buoi_so"].strip()
                    and item.get("ngay_hoc_iso") == bcnb_row.get("ngay_hoc_iso")
                    and (
                        not row_sid
                        or not str(item.get("bcnb_session_id") or "").strip()
                        or str(item.get("bcnb_session_id") or "").strip() == row_sid
                    )
                ]
                refreshed = (
                    refreshed_candidates[0]
                    if len(refreshed_candidates) == 1
                    else None
                )
                if refreshed is None:
                    log_message(
                        f"Không tìm lại được dòng BCNB {bcnb_row['ma_lop']}/{bcnb_row['buoi_so']}; "
                        "bỏ qua để kiểm tra thủ công.",
                        level="WARNING",
                    )
                    continue
                bcnb_row = refreshed

            cells = bcnb_row["cells"]
            class_code = bcnb_row["ma_lop"].strip().upper()
            lesson = bcnb_row["buoi_so"].strip()
            is_lesson_14 = lesson == "14"
            unique_meta_result: dict[str, Any] | None = None
            meta_row_matches = [
                result
                for result in scan_results
                if _result_matches_bcnb_row_for_metadata(
                    result,
                    bcnb_row,
                    (selected_week, selected_year),
                )
            ]
            if len(meta_row_matches) == 1:
                matching_bcnb_rows = [
                    candidate_row
                    for candidate_row in bcnb_rows
                    if _result_matches_bcnb_row_for_metadata(
                        meta_row_matches[0],
                        candidate_row,
                        (selected_week, selected_year),
                    )
                ]
                if len(matching_bcnb_rows) == 1:
                    unique_meta_result = meta_row_matches[0]
                    _attach_bcnb_row_metadata(unique_meta_result, bcnb_row)
            all_row_matches = [
                result
                for result in scan_results
                if _result_matches_schedule_row(
                    result,
                    bcnb_row,
                    (selected_week, selected_year),
                )
            ]
            result_matches = [
                result
                for result in all_row_matches
                if result.get("evidence_state")
                in {"SUBMITTED_VERIFIED", "SUBMITTED_LATE_VERIFIED"}
            ]
            if len(result_matches) > 1:
                raise RuntimeError(
                    f"Dòng {class_code}/{lesson} khớp nhiều báo cáo Zalo; "
                    "dừng để tránh tick nhầm."
                )
            matched_result = result_matches[0] if result_matches else None
            is_submitted = False

            if is_lesson_14:
                is_submitted = True
                matched_result = _apply_lesson_14_result(scan_results, bcnb_row)
                _attach_bcnb_row_metadata(matched_result, bcnb_row)
                log_message(
                    f"Lớp {class_code}: Buổi 14 được MIỄN BÁO CÁO. "
                    "Đã ghi LESSON_14_EXEMPTION."
                )
            elif matched_result is not None:
                is_submitted = _apply_bcnb_deadline_state(
                    matched_result,
                    bcnb_row,
                    today=today,
                )
                if not is_submitted:
                    log_message(
                        f"Bỏ qua {class_code}/{lesson}: không đọc được deadline "
                        "Zalo của đúng dòng BCNB.",
                        level="WARNING",
                    )

            if not is_submitted:
                await _maybe_update_row_checker_note(
                    matched_result or unique_meta_result,
                    bcnb_row,
                    selected_week=selected_week,
                    selected_year=selected_year,
                )
                continue
            checkbox = cells[report_index].locator('input[type="checkbox"]').first
            if await checkbox.count() == 0:
                raise RuntimeError(f"BCNB không có checkbox báo cáo cho {class_code}/{lesson}.")
            is_checked = await checkbox.is_checked()
            if is_checked and not is_lesson_14:
                if matched_result is not None:
                    matched_result["nx_zalo_checked"] = True
                    if isinstance(matched_result.get("scan_target"), dict):
                        matched_result["scan_target"]["nx_zalo_checked"] = True
                await emit_outcome(
                    {
                        "class_code": class_code,
                        "lesson": lesson,
                        "week": selected_week,
                        "year": selected_year,
                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                        "status": "ALREADY_DONE",
                    }
                )
                await _maybe_update_row_checker_note(
                    matched_result or unique_meta_result,
                    bcnb_row,
                    selected_week=selected_week,
                    selected_year=selected_year,
                )
                continue

            if should_halt is not None and should_halt():
                await emit_outcome(
                    {
                        "class_code": class_code,
                        "lesson": lesson,
                        "week": selected_week,
                        "year": selected_year,
                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                        "status": "BLOCKED_BATCH_HALT",
                        "error": "Batch đã halt trước click; target không bị thay đổi.",
                    }
                )
                continue

            evidence = (matched_result or {}).get("evidence", {})
            context = evidence.get("date_context", {}) or {}
            classifier = evidence.get("classifier_result", {}) or {}
            candidates = classifier.get("candidates") or []
            candidate = candidates[-1] if candidates else {}
            evidence_path, evidence_sha256 = get_evidence_reference(matched_result)
            try:
                outcome = await execute_verified_checkbox_action(
                    page=page,
                    checkbox=checkbox,
                    audit_repo=audit_repo,
                    job_repo=job_repo,
                    job_id=job_id,
                    action_key=(
                        f"bcnb-report:{job_id}:{selected_year}:W{selected_week}:"
                        f"{class_code}:{lesson}"
                    ),
                    action_type="BCNB_REPORT_TICK",
                    class_code=class_code,
                    column_kind="report",
                    week=selected_week,
                    year=selected_year,
                    lesson=lesson,
                    execution_mode=execution_mode,
                    reason={
                        "week": selected_week,
                        "year": selected_year,
                        "lesson": lesson,
                        "class_date": bcnb_row.get("ngay_hoc_iso"),
                        "zalo_deadline": bcnb_row.get("zalo_deadline"),
                        "report_sent_at": context.get("report_sent_at")
                        or classifier.get("matched_ui_sent_at"),
                        "delay_days": context.get("deadline_delay_days")
                        if context.get("deadline_delay_days") is not None
                        else candidate.get("delay_days"),
                        "message_fingerprint": candidate.get("message_fingerprint"),
                        "target_week_source": context.get("target_week_source"),
                        "target_match_decision": context.get("target_match_decision"),
                        "target_match_note": context.get("target_match_note"),
                        "reported_lesson_date": context.get("reported_lesson_date"),
                        "reported_lesson": context.get("reported_lesson"),
                        "matched_lesson_date": context.get("matched_lesson_date"),
                        "matched_lesson": context.get("matched_lesson"),
                        "submitted": is_submitted,
                        "evidence_state": (
                            matched_result.get("evidence_state") if matched_result else None
                        ),
                        "lesson_14_exemption": is_lesson_14,
                        "evidence_decision": LESSON_14_EXEMPTION if is_lesson_14 else None,
                    },
                    evidence_path=evidence_path,
                    evidence_sha256=evidence_sha256,
                    require_evidence_registry=True,
                    settle_seconds=0.7,
                    should_halt=should_halt,
                )
            except JobCancelledError:
                raise
            except Exception as action_error:
                unknown = isinstance(action_error, BcnbOutcomeUnknownError)
                failure = {
                    "class_code": class_code,
                    "lesson": lesson,
                    "week": selected_week,
                    "year": selected_year,
                    "error": str(action_error)[:500],
                }
                failures.append(failure)
                await emit_outcome(
                    {
                        **failure,
                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                        "status": (
                            "OUTCOME_UNKNOWN"
                            if execution_mode == "production" and unknown
                            else "FAILED_BEFORE_WRITE"
                        ),
                    }
                )
                if matched_result is not None:
                    matched_result["status"] = (
                        "⚠️ BCNB chưa cập nhật – Cần kiểm tra thủ công"
                    )
                    matched_result["status_detail"] = (
                        f"BCNB W{selected_week}/{selected_year}, buổi {lesson}: "
                        "ghi thất bại; xem audit để xử lý."
                    )
                    matched_result["bcnb_update_state"] = "FAILED"
                log_message(
                    f"Bỏ qua lỗi cập nhật BCNB {class_code}/{lesson} "
                    f"W{selected_week}/{selected_year}; tiếp tục lớp kế tiếp: {action_error}",
                    level="ERROR",
                    event="bcnb.update_row_failed",
                    class_code=class_code,
                    lesson=lesson,
                    week=selected_week,
                    year=selected_year,
                )
                continue
            updated_count += 1
            if outcome == "VERIFIED" and matched_result is not None:
                matched_result["nx_zalo_checked"] = True
                if isinstance(matched_result.get("scan_target"), dict):
                    matched_result["scan_target"]["nx_zalo_checked"] = True
            await emit_outcome(
                {
                    "class_code": class_code,
                    "lesson": lesson,
                    "week": selected_week,
                    "year": selected_year,
                    "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                    "status": outcome,
                }
            )
            if outcome == "DRY_RUN":
                log_message(
                    f"[DRY-RUN] Sẽ Tick xanh cho lớp: {class_code} "
                    f"W{selected_week}/{selected_year}"
                    + (" (Miễn báo cáo Buổi 14)" if is_lesson_14 else "")
                )
            else:
                log_message(
                    f"Đã tick và kiểm chứng BCNB cho lớp: {class_code} "
                    f"W{selected_week}/{selected_year}"
                )
            if not is_lesson_14:
                await _maybe_update_row_checker_note(
                    matched_result or unique_meta_result,
                    bcnb_row,
                    selected_week=selected_week,
                    selected_year=selected_year,
                )

    inspect_and_attach_quality_issues(
        scoped_results,
        lms_slots_by_class=resolved_lms_slots,
    )

    if failures:
        log_message(
            f"Đối chiếu BCNB hoàn tất với {len(failures)} dòng lỗi; "
            "các lớp còn lại đã được xử lý.",
            level="WARNING",
            event="bcnb.update_partial",
            failed_count=len(failures),
        )
    else:
        log_message("ĐÃ ĐỐI CHIẾU TOÀN BỘ CÁC TUẦN BCNB THÀNH CÔNG!")
    return {
        "selected_weeks": [
            {"week": week, "year": year} for week, year in active_pairs
        ],
        "updated_count": updated_count,
        "checker_note_updated_count": checker_note_updated_count,
        "failed_targets": failures,
        "target_outcomes": target_outcomes,
        "checker_note_outcomes": checker_note_outcomes,
    }


def parse_scan_job_results_envelope(
    raw_results: Any,
) -> tuple[dict[str, Any] | list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Return ``(envelope, items)`` from a persisted ZALO_SCAN results payload."""
    parsed = raw_results
    if isinstance(raw_results, str):
        try:
            parsed = json.loads(raw_results)
        except (TypeError, ValueError):
            return None, []
    if isinstance(parsed, list):
        items = [item for item in parsed if isinstance(item, dict)]
        return items, items
    if isinstance(parsed, dict):
        raw_items = parsed.get("items")
        if isinstance(raw_items, list):
            items = [item for item in raw_items if isinstance(item, dict)]
            return parsed, items
    return None, []


def collect_result_item_evidence_paths(item: dict[str, Any]) -> set[str]:
    """Return all registered/public evidence paths referenced by one scan result item."""
    paths: set[str] = set()
    evidence = item.get("evidence")
    if isinstance(evidence, dict):
        images = evidence.get("images")
        if isinstance(images, dict):
            for image in images.values():
                if isinstance(image, dict):
                    path = str(image.get("path") or "").strip()
                    if path:
                        paths.add(path)
    screenshot_path = str(item.get("screenshot_path") or "").strip()
    if screenshot_path:
        paths.add(screenshot_path)
    return paths


def is_result_item_already_submitted(item: dict[str, Any]) -> bool:
    """Return True if a scan result row was already verified, exempted, or approved."""
    if bool(item.get("submitted_for_bcnb")):
        return True
    status = str(item.get("status") or "").strip()
    if status.startswith("✅") or status.startswith("🟡") or "Miễn" in status:
        return True
    if str(item.get("evidence_state") or "") in {
        "SUBMITTED_VERIFIED",
        "SUBMITTED_LATE_VERIFIED",
        "LESSON_14_EXEMPTION",
        "MANUAL_APPROVED_VERIFIED",
    }:
        return True
    return False


def find_manual_approve_target_matches(
    items: list[dict[str, Any]],
    *,
    group_name: str,
    week: int,
    year: int,
    lesson: str,
    class_date_iso: str,
    class_code: str | None = None,
) -> list[tuple[int, dict[str, Any], str]]:
    """Find matching ``(index, item, resolved_class_code)`` tuples in scan results."""
    clean_group = str(group_name or "").strip()
    clean_lesson = str(lesson or "").strip()
    clean_date = str(class_date_iso or "").strip()
    clean_code = str(class_code or "").strip().upper()
    matches: list[tuple[int, dict[str, Any], str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        scan_target = item.get("scan_target")
        st = scan_target if isinstance(scan_target, dict) else {}
        evidence = item.get("evidence")
        ev = evidence if isinstance(evidence, dict) else {}
        date_context = ev.get("date_context")
        ctx = date_context if isinstance(date_context, dict) else {}
        try:
            item_week = int(
                st.get("week") if st.get("week") is not None else ctx.get("target_week")  # type: ignore[arg-type]
            )
            item_year = int(
                st.get("year") if st.get("year") is not None else ctx.get("target_year")  # type: ignore[arg-type]
            )
        except (TypeError, ValueError):
            continue
        if item_week != week or item_year != year:
            continue
        item_lesson = str(
            st.get("lesson")
            if st.get("lesson") is not None
            else (ctx.get("target_lesson") or "")
        ).strip()
        if item_lesson != clean_lesson:
            continue
        item_date_iso = str(st.get("class_date_iso") or "").strip()
        if item_date_iso != clean_date:
            continue
        item_group = str(item.get("group") or "").strip()
        target_group = str(st.get("group_name") or "").strip()
        if clean_group and clean_group not in {item_group, target_group}:
            continue
        item_class_code = str(
            st.get("class_code") or ctx.get("target_class_code") or ""
        ).strip().upper()
        if not item_class_code:
            continue
        if clean_code and item_class_code != clean_code:
            continue
        matches.append((index, item, item_class_code))
    return matches


@contextlib.asynccontextmanager
async def _open_bcnb_page(external):
    browser = getattr(external, "browser", None)
    if browser is not None and hasattr(browser, "persistent_page"):
        async with browser.persistent_page() as page:
            yield page
        return
    if browser is not None and hasattr(browser, "persistent_session"):
        async with browser.persistent_session() as session:
            page = await session.new_page()
            try:
                yield page
            finally:
                with contextlib.suppress(Exception):
                    await session.close_page(page)
        return
    raise RuntimeError("Browser gateway không hỗ trợ mở trang BCNB.")


async def _persist_manual_approval_to_source_job(
    *,
    source_job_id: int,
    approval_job_id: int,
    target_key: str,
    group_name: str,
    class_code: str,
    week: int,
    year: int,
    lesson: str,
    class_date_iso: str,
    evidence_path: str,
    evidence_sha256: str,
    approved_by: str,
    outcome: str,
    dry_run: bool,
    job_repo,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], Any, Any]:
    """Update the source ZALO_SCAN job results & batch target row in SQLite."""
    source_job = await job_repo.get_job(source_job_id)
    if not source_job:
        return None, [], None, None
    envelope, items = parse_scan_job_results_envelope(source_job.get("results"))
    matches = find_manual_approve_target_matches(
        items,
        group_name=group_name,
        week=week,
        year=year,
        lesson=lesson,
        class_date_iso=class_date_iso,
        class_code=class_code,
    )
    verified_production = (not dry_run) and outcome == "VERIFIED"
    approved_status = "✅ Đã duyệt tay & Tích BCNB"
    approved_detail = (
        f"[DRY-RUN] Đã mô phỏng duyệt tay bởi {approved_by} · BCNB W{week}/{year} buổi {lesson} (chưa tích thật)"
        if not verified_production
        else f"Duyệt tay bởi {approved_by} · BCNB W{week}/{year} buổi {lesson}"
    )
    updated_item: dict[str, Any] | None = None
    if len(matches) == 1:
        _, matched_item, _ = matches[0]
        if verified_production:
            matched_item["status"] = approved_status
            matched_item["submitted_for_bcnb"] = True
            matched_item["manual_approved"] = True
            matched_item["evidence_state"] = "MANUAL_APPROVED_VERIFIED"
        matched_item["status_detail"] = approved_detail
        matched_item["bcnb_update_state"] = outcome
        matched_item["manual_approval"] = {
            "approved_by": approved_by,
            "approved_at": utc_iso(),
            "approval_job_id": approval_job_id,
            "source_job_id": source_job_id,
            "evidence_path": evidence_path,
            "evidence_sha256": evidence_sha256,
            "outcome": outcome,
            "dry_run": dry_run,
            "target_key": target_key,
        }
        updated_item = matched_item
        if envelope is not None:
            if isinstance(envelope, dict):
                envelope["items"] = items
            serialized = json.dumps(envelope, ensure_ascii=False)
            update_terminal = getattr(job_repo, "update_terminal_results", None)
            if update_terminal is not None:
                await update_terminal(source_job_id, serialized)
            else:
                await job_repo.update_job(
                    source_job_id,
                    status=source_job.get("status"),
                    results=serialized,
                )

    with contextlib.suppress(Exception):
        stamp = utc_iso()
        async with get_db_connection() as db:
            await db.execute(
                """
                UPDATE zalo_batch_targets
                SET write_status = ?, result_json = COALESCE(?, result_json),
                    error_code = NULL, error_message = NULL,
                    updated_at = ?, finished_at = ?
                WHERE job_id = ? AND target_key = ?
                """,
                (
                    outcome,
                    json.dumps(updated_item, ensure_ascii=False, sort_keys=True)
                    if updated_item is not None
                    else None,
                    stamp,
                    stamp,
                    source_job_id,
                    target_key,
                ),
            )
            await db.commit()

    snapshot_id = envelope.get("snapshot_id") if isinstance(envelope, dict) else None
    required_week = envelope.get("required_week") if isinstance(envelope, dict) else None
    return updated_item, items, snapshot_id, required_week


async def run_zalo_manual_approve(
    job_id: int,
    payload: dict[str, Any],
    execution_mode: str = "dry-run",
    *,
    runtime=None,
    job_repo=None,
    audit_repo=None,
    evidence_repo=None,
) -> None:
    """Execute one human-approved BCNB report checkbox tick with server read-back."""
    selected_job_repo = job_repo or JobRepository()
    selected_audit_repo = audit_repo or AuditRepository()
    selected_evidence_repo = evidence_repo or EvidenceRepository()
    external = (
        runtime.external
        if runtime and getattr(runtime, "external", None) is not None
        else ExternalGateways.production()
    )
    dry_run = execution_mode != "production"
    try:
        await selected_job_repo.ensure_not_cancelled(job_id)
        source_job_id = payload.get("source_job_id")
        if not isinstance(source_job_id, int) or isinstance(source_job_id, bool) or source_job_id <= 0:
            raise ValueError("Job duyệt tay thiếu source_job_id hợp lệ.")
        target_raw = payload.get("target")
        target = target_raw if isinstance(target_raw, dict) else {}
        group_name = str(target.get("group_name") or "").strip()
        class_code = str(target.get("class_code") or "").strip().upper()
        lesson = str(target.get("lesson") or "").strip()
        class_date_iso = str(target.get("class_date_iso") or "").strip()
        week = int(target.get("week"))  # type: ignore[arg-type]
        year = int(target.get("year"))  # type: ignore[arg-type]
        target_key = canonical_target_key(
            {
                "week": week,
                "year": year,
                "class_code": class_code,
                "lesson": lesson,
                "class_date_iso": class_date_iso,
            }
        )
        evidence_path = str(payload.get("evidence_path") or "").strip()
        evidence_sha256 = str(payload.get("evidence_sha256") or "").strip().upper()
        approved_by = str(payload.get("approved_by") or "operator").strip() or "operator"
        if not group_name or not evidence_path:
            raise ValueError("Job duyệt tay thiếu tên nhóm hoặc đường dẫn bằng chứng.")

        source_job = await selected_job_repo.get_job(source_job_id)
        if not source_job or source_job.get("job_type") != "ZALO_SCAN":
            raise ValueError("Không tìm thấy job quét Zalo gốc hợp lệ.")
        _, source_items = parse_scan_job_results_envelope(source_job.get("results"))
        source_matches = find_manual_approve_target_matches(
            source_items,
            group_name=group_name,
            week=week,
            year=year,
            lesson=lesson,
            class_date_iso=class_date_iso,
            class_code=class_code,
        )
        if len(source_matches) != 1:
            raise ValueError("Không xác định được duy nhất dòng kết quả trong job gốc.")
        _, source_item, _ = source_matches[0]
        if is_result_item_already_submitted(source_item):
            raise ValueError("Lớp này đã được tích hoặc miễn báo cáo trên BCNB.")
        if evidence_path not in collect_result_item_evidence_paths(source_item):
            raise ValueError("Ảnh bằng chứng không thuộc dòng kết quả của lớp được duyệt.")

        verified_artifact = await selected_evidence_repo.verify(
            evidence_path,
            expected_sha256=evidence_sha256 or None,
            job_id=source_job_id,
        )
        if (
            not verified_artifact
            or verified_artifact.get("status") != "AVAILABLE"
            or not verified_artifact.get("sha256")
        ):
            raise ValueError("Ảnh bằng chứng không còn khả dụng hoặc SHA-256 không khớp.")
        evidence_sha256 = str(verified_artifact["sha256"]).strip().upper()

        if (
            execution_mode == "production"
            and runtime is not None
            and getattr(runtime, "database_path", None)
            and getattr(runtime, "db_backup_root", None)
        ):
            artifact = await asyncio.to_thread(
                backup_job,
                runtime.database_path,
                runtime.db_backup_root,
                keep_count=getattr(runtime, "db_backup_retention_count", 10),
                label=f"zalo-manual-approve-{job_id}",
            )
            add_event = getattr(selected_job_repo, "add_event", None)
            if add_event is not None:
                await add_event(
                    job_id,
                    "DB_BACKUP_VERIFIED",
                    "Đã tạo và kiểm chứng SQLite online backup trước khi duyệt tay BCNB.",
                    {
                        "path": artifact["destination_path"],
                        "sha256": artifact["sha256"],
                        "integrity": artifact["integrity"],
                        "foreign_key_errors": len(artifact["foreign_key_errors"]),
                    },
                )

        async with exclusive_browser_job(
            job_id,
            selected_job_repo,
            lock=runtime.browser_lock if runtime else None,
            process_lock=getattr(runtime, "profile_lock", None) if runtime else None,
        ):
            await selected_job_repo.update_job(
                job_id,
                status="RUNNING",
                progress=25,
                message=f"Đang mở BCNB tuần W{week}/{year} để duyệt lớp {class_code}...",
            )
            today = business_day_start(now_business())
            from services.zalo_discovery_service import _login_bcnb_if_needed

            async with _open_bcnb_page(external) as page:
                await page.goto(Config.BCNB_URL)
                await _login_bcnb_if_needed(page)
                bcnb_rows = await _read_bcnb_schedule_rows(
                    page,
                    week=week,
                    year=year,
                    job_id=job_id,
                    today=today,
                    job_repo=selected_job_repo,
                )
                _, _, report_index = await resolve_report_columns(page)
                if group_name:
                    matched_group_row = find_bcnb_match(group_name, bcnb_rows)
                    if (
                        matched_group_row is None
                        or matched_group_row["ma_lop"].strip().upper() != class_code
                    ):
                        raise RuntimeError(
                            f"Tên nhóm '{group_name}' không khớp duy nhất với mã lớp {class_code} trên bảng BCNB W{week}/{year}; dừng an toàn."
                        )
                row_matches = [
                    row
                    for row in bcnb_rows
                    if row["ma_lop"].strip().upper() == class_code
                    and row["buoi_so"].strip() == lesson
                    and row.get("ngay_hoc_iso") == class_date_iso
                ]
                if not row_matches:
                    raise RuntimeError(
                        f"Không tìm thấy dòng BCNB khớp {class_code} buổi {lesson} ngày {class_date_iso} ở W{week}/{year}."
                    )
                if len(row_matches) > 1:
                    raise RuntimeError(
                        f"Tìm thấy nhiều dòng BCNB trùng cho {class_code} buổi {lesson} ngày {class_date_iso}; dừng an toàn."
                    )
                bcnb_row = row_matches[0]
                checkbox = bcnb_row["cells"][report_index].locator('input[type="checkbox"]').first
                if await checkbox.count() == 0:
                    raise RuntimeError(
                        f"BCNB không có checkbox báo cáo cho {class_code}/{lesson}."
                    )
                outcome = await execute_verified_checkbox_action(
                    page=page,
                    checkbox=checkbox,
                    audit_repo=selected_audit_repo,
                    job_repo=selected_job_repo,
                    job_id=job_id,
                    action_key=(
                        f"bcnb-manual-approve:{execution_mode}:{source_job_id}:"
                        f"{year}:W{week}:{class_code}:{lesson}:{class_date_iso}"
                    ),
                    action_type="BCNB_MANUAL_APPROVE_TICK",
                    class_code=class_code,
                    column_kind="report",
                    week=week,
                    year=year,
                    lesson=lesson,
                    execution_mode=execution_mode,
                    reason={
                        "manual_approve": True,
                        "approved_by": approved_by,
                        "source_job_id": source_job_id,
                        "group_name": group_name,
                        "class_code": class_code,
                        "week": week,
                        "year": year,
                        "lesson": lesson,
                        "class_date_iso": class_date_iso,
                        "target_key": target_key,
                        "zalo_deadline": bcnb_row.get("zalo_deadline"),
                    },
                    evidence_path=evidence_path,
                    evidence_sha256=evidence_sha256,
                    evidence_job_id=source_job_id,
                    require_evidence_registry=True,
                    settle_seconds=0.7,
                )
                if execution_mode == "production" and outcome != "VERIFIED":
                    raise RuntimeError(
                        f"Duyệt tay production yêu cầu kết quả VERIFIED nhưng nhận được {outcome}; dừng an toàn."
                    )

        updated_item, updated_items, snapshot_id, required_week = (
            await _persist_manual_approval_to_source_job(
                source_job_id=source_job_id,
                approval_job_id=job_id,
                target_key=target_key,
                group_name=group_name,
                class_code=class_code,
                week=week,
                year=year,
                lesson=lesson,
                class_date_iso=class_date_iso,
                evidence_path=evidence_path,
                evidence_sha256=evidence_sha256,
                approved_by=approved_by,
                outcome=outcome,
                dry_run=dry_run,
                job_repo=selected_job_repo,
            )
        )
        verified_production = (not dry_run) and outcome == "VERIFIED"
        approved_status = (
            "✅ Đã duyệt tay & Tích BCNB"
            if verified_production
            else str(
                (updated_item or source_item).get("status")
                or "⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công"
            )
        )
        approved_detail = (
            f"[DRY-RUN] Đã mô phỏng duyệt tay bởi {approved_by} · BCNB W{week}/{year} buổi {lesson} (chưa tích thật)"
            if not verified_production
            else f"Duyệt tay bởi {approved_by} · BCNB W{week}/{year} buổi {lesson}"
        )
        result_payload = {
            "kind": "zalo_manual_approve",
            "source_job_id": source_job_id,
            "snapshot_id": snapshot_id,
            "required_week": required_week,
            "target_key": target_key,
            "class_code": class_code,
            "group_name": group_name,
            "week": week,
            "year": year,
            "lesson": lesson,
            "class_date_iso": class_date_iso,
            "outcome": outcome,
            "dry_run": dry_run,
            "approved_by": approved_by,
            "evidence_path": evidence_path,
            "evidence_sha256": evidence_sha256,
            "status": approved_status,
            "status_detail": approved_detail,
            "updated_item": updated_item,
            "items": updated_items,
        }
        await selected_job_repo.update_job(
            job_id,
            status="SUCCESS",
            progress=100,
            message=(
                f"[DRY-RUN] Đã duyệt tay & mô phỏng tích BCNB cho {class_code} (W{week}/{year} buổi {lesson})."
                if dry_run
                else f"Đã duyệt tay & tích BCNB thành công cho {class_code} (W{week}/{year} buổi {lesson})."
            ),
            results=json.dumps(result_payload, ensure_ascii=False),
        )
        log_message(
            f"Đã duyệt tay BCNB ({outcome}) cho lớp {class_code} "
            f"W{week}/{year} buổi {lesson} bởi {approved_by}."
        )
    except JobCancelledError as exc:
        await selected_job_repo.update_job(
            job_id,
            status="CANCELLED",
            progress=100,
            message=str(exc),
        )
    except Exception as exc:
        log_message(f"Lỗi duyệt tay BCNB: {exc}", level="ERROR")
        await selected_job_repo.update_job(
            job_id,
            status="FAILED",
            progress=100,
            message=f"Dừng an toàn: {str(exc)[:180]}",
        )
