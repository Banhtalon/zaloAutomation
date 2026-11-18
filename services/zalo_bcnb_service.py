"""Week-aware BCNB matching and audited report checkbox actions."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
import contextlib
from datetime import datetime
import json
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
    BcnbConflictAfterWriteError,
    BcnbOutcomeUnknownError,
    execute_verified_checkbox_action,
    execute_verified_checker_note_action,
)
from services.bcnb_safety import (
    extract_bcnb_extended_row_fields,
    fold_text,
    merge_checker_issue_note,
    preserve_bcnb_note_text,
    resolve_extended_bcnb_columns,
    resolve_lms_columns,
    resolve_report_columns,
    resolve_row_proven_auto_issues,
    resolve_schedule_columns,
    select_week_checked,
    split_checker_issue_note,
)
from services.browser_job_guard import exclusive_browser_job
from services.external_gateways import ExternalGateways
from services.lms_quality_service import (
    fetch_and_evaluate_lms_quality,
    inspect_and_attach_quality_issues_async,
    is_lms_fetch_unavailable,
)
from services.report_status import (
    ALREADY_SUBMITTED_EVIDENCE_STATES,
    CATEGORY_DONE,
    CATEGORY_EXEMPT,
    CATEGORY_LATE,
    ISSUE_CODE_LEGACY_UNCLASSIFIED_FAIL,
    ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
    ISSUE_CODE_LMS_COMMENT_NOTE,
    ISSUE_CODE_LMS_MANUAL_CHECK,
    ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
    ISSUE_CODE_LMS_SPCK_MISSING,
    ISSUE_CODE_LMS_SPCK_NOTE,
    ISSUE_CODE_LMS_SUMMARY_NOTE,
    SUBMITTED_EVIDENCE_STATES,
    VERIFIED_EVIDENCE_STATES,
    categorize_row,
    coerce_structured_issue,
    is_lesson_14,
    is_lms_ready_for_bcnb_tick,
    issue_code,
    issue_text,
    normalize_class_code,
    normalize_lesson_number,
)
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


def _normalized_lesson_token(raw_lesson: Any) -> str:
    txt = str(raw_lesson or "").strip()
    if not txt:
        return ""
    parsed = normalize_lesson_number(txt)
    return str(parsed) if parsed is not None else txt


def _normalized_target_key_str(raw_key: str) -> str:
    clean = str(raw_key or "").strip()
    parts = clean.split("|")
    if len(parts) != 4:
        return clean
    period, code, lesson, date_iso = parts
    return "|".join(
        [
            period.strip(),
            normalize_class_code(code),
            _normalized_lesson_token(lesson),
            date_iso.strip(),
        ]
    )


def _normalized_target_key_from_mapping(target: dict[str, Any]) -> str:
    return _normalized_target_key_str(canonical_target_key(target))


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
    group_val = str(result.get("group") or "").strip()
    if group_val:
        if not find_bcnb_match(group_val, [row]):
            return False
    else:
        raw_st = result.get("scan_target")
        st_map = raw_st if isinstance(raw_st, dict) else {}
        ctx_map = (result.get("evidence") or {}).get("date_context") or {}
        res_code = normalize_class_code(
            st_map.get("class_code")
            or ctx_map.get("target_class_code")
            or result.get("class_code")
        )
        row_code = normalize_class_code(row.get("ma_lop"))
        if not res_code or not row_code or res_code != row_code:
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
    if target_lesson and _normalized_lesson_token(target_lesson) != _normalized_lesson_token(
        row.get("buoi_so")
    ):
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
    row_lesson = _normalized_lesson_token(row.get("buoi_so"))
    st_lesson = _normalized_lesson_token(st.get("lesson"))
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
    raw_lesson = row.get("buoi_so")
    if raw_lesson is not None and str(raw_lesson).strip() and not is_lesson_14(raw_lesson):
        raise ValueError(f"Dòng {class_code} không phải Buổi 14.")
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
        row_code = normalize_class_code(row.get("ma_lop"))
        matches = []
        for result in scan_results:
            group_str = str(result.get("group") or "").strip()
            if group_str:
                if find_bcnb_match(group_str, [row]):
                    matches.append(result)
            elif row_code:
                raw_st = result.get("scan_target")
                st_map: dict[str, Any] = (
                    raw_st if isinstance(raw_st, dict) else {}
                )
                raw_ev = result.get("evidence")
                ev_map: dict[str, Any] = (
                    raw_ev if isinstance(raw_ev, dict) else {}
                )
                raw_ctx = ev_map.get("date_context")
                ctx_map: dict[str, Any] = (
                    raw_ctx if isinstance(raw_ctx, dict) else {}
                )
                res_code = normalize_class_code(
                    st_map.get("class_code")
                    or ctx_map.get("target_class_code")
                    or result.get("class_code")
                )
                if res_code == row_code:
                    matches.append(result)
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
    lesson_str = str(raw_lesson or "14").strip() or "14"
    row_date_iso = str(row.get("ngay_hoc_iso") or "").strip()
    row_date_raw = str(row.get("ngay_hoc") or "").strip()
    if class_code and not str(exempted.get("class_code") or "").strip():
        exempted["class_code"] = class_code
    if lesson_str and not str(exempted.get("lesson") or "").strip():
        exempted["lesson"] = lesson_str
    if row_date_iso and not exempted.get("ngay_hoc_iso"):
        exempted["ngay_hoc_iso"] = row_date_iso
    if row_date_raw and not exempted.get("ngay_hoc"):
        exempted["ngay_hoc"] = row_date_raw
    if row_pair is not None:
        exempted.setdefault("week", row_pair[0])
        exempted.setdefault("year", row_pair[1])
    st_obj = exempted.get("scan_target")
    if not isinstance(st_obj, dict):
        st_obj = {}
        exempted["scan_target"] = st_obj
    if class_code and not str(st_obj.get("class_code") or "").strip():
        st_obj["class_code"] = class_code
    if lesson_str and not str(st_obj.get("lesson") or "").strip():
        st_obj["lesson"] = lesson_str
    if (row_date_raw or row_date_iso) and not str(st_obj.get("class_date") or "").strip():
        st_obj["class_date"] = row_date_raw or row_date_iso
    if row_date_iso and not str(st_obj.get("class_date_iso") or "").strip():
        st_obj["class_date_iso"] = row_date_iso
    if row_pair is not None:
        st_obj.setdefault("week", row_pair[0])
        st_obj.setdefault("year", row_pair[1])
    ev_obj = exempted.setdefault("evidence", {})
    if isinstance(ev_obj, dict):
        ctx_obj = ev_obj.setdefault("date_context", {})
        if isinstance(ctx_obj, dict):
            if class_code and not ctx_obj.get("target_class_code"):
                ctx_obj["target_class_code"] = class_code
            if lesson_str and not ctx_obj.get("target_lesson"):
                ctx_obj["target_lesson"] = lesson_str
            if row_date_iso and not ctx_obj.get("target_class_date"):
                ctx_obj["target_class_date"] = row_date_iso
            if row_pair is not None:
                ctx_obj.setdefault("target_week", row_pair[0])
                ctx_obj.setdefault("target_year", row_pair[1])
    if source is None:
        scan_results.append(exempted)
    else:
        if exempted is not source:
            source.clear()
            source.update(exempted)
        return source
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


_READ_BCNB_ROWS_FAST_JS = """
(extCols) => {
  const cols = extCols || {};
  const trs = Array.from(document.querySelectorAll("table tr"));
  const readCheckbox = (tds, idx) => {
    if (typeof idx !== "number" || idx < 0 || idx >= tds.length) return null;
    const cb = tds[idx].querySelector('input[type="checkbox"]');
    return cb ? Boolean(cb.checked) : null;
  };
  const readNote = (tds, idx, noteType) => {
    if (typeof idx !== "number" || idx < 0 || idx >= tds.length) {
      return { present: false, attrRaw: null, fieldVal: null, textRaw: null };
    }
    const td = tds[idx];
    const noteEl = td.querySelector('.note-cell[data-note-type="' + noteType + '"]');
    const attrRaw = noteEl ? noteEl.getAttribute("data-current-note") : null;
    const fieldEl = td.querySelector('textarea, input:not([type="checkbox"]):not([type="hidden"])');
    const fieldVal = fieldEl && typeof fieldEl.value === "string" ? fieldEl.value : null;
    const textRaw = typeof td.innerText === "string" ? td.innerText : String(td.textContent || "");
    return { present: true, attrRaw, fieldVal, textRaw };
  };
  const out = [];
  for (const tr of trs) {
    if (tr.querySelector("table") !== null) {
      return null;
    }
    const tds = Array.from(tr.querySelectorAll("td"));
    const texts = tds.map((td) =>
      typeof td.innerText === "string" ? td.innerText : String(td.textContent || "")
    );
    out.push({
      cellCount: tds.length,
      texts,
      sessionId: String(tr.getAttribute("data-session-id") || "").trim(),
      rowBlock: String(tr.getAttribute("data-block") || "").trim(),
      nxZaloChecked: readCheckbox(tds, cols.zalo_checkbox_index),
      nxLmsChecked: readCheckbox(tds, cols.lms_checkbox_index),
      checkerNote: readNote(tds, cols.checker_note_index, "checker"),
      instructorNote: readNote(tds, cols.instructor_note_index, "instructor"),
    });
  }
  return out;
}
"""

_ZALO_MANUAL_CHECK_MARKERS = ("cần kiểm tra thủ công", "kiểm tra tay")


def _resolve_evaluated_note_cell(note_info: Any) -> str | None:
    if not isinstance(note_info, Mapping) or not note_info.get("present"):
        return None
    attr_value: str | None = None
    raw_attr = note_info.get("attrRaw")
    if raw_attr is not None:
        attr_value = preserve_bcnb_note_text(str(raw_attr))
        if attr_value:
            return attr_value
    raw_field = note_info.get("fieldVal")
    if raw_field is not None:
        field_value = preserve_bcnb_note_text(str(raw_field))
        if field_value:
            return field_value
    raw_text = note_info.get("textRaw")
    if raw_text is not None:
        return preserve_bcnb_note_text(str(raw_text))
    return attr_value


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
    required_index = max(class_index, lesson_index, date_index, deadline_index)
    is_playwright_page = str(getattr(type(page), "__module__", "")).startswith(
        "playwright."
    ) and callable(getattr(page, "evaluate", None))
    if is_playwright_page:
        try:
            raw_rows = await page.evaluate(_READ_BCNB_ROWS_FAST_JS, ext_cols)
            if not isinstance(raw_rows, list) or len(raw_rows) != len(rows):
                raise RuntimeError("BCNB fast table read row count mismatch.")
            fast_snapshot: list[dict[str, Any]] = []
            cols_map = ext_cols if isinstance(ext_cols, Mapping) else {}
            block_idx = cols_map.get("block_index")
            lms_dl_idx = cols_map.get("lms_deadline_index")
            eval_idx = cols_map.get("evaluation_status_index")
            for row, raw_item in zip(rows, raw_rows, strict=True):
                await job_repo.ensure_not_cancelled(job_id)
                if not isinstance(raw_item, Mapping):
                    raise RuntimeError("BCNB fast table read row payload invalid.")
                texts = raw_item.get("texts")
                if not isinstance(texts, list):
                    raise RuntimeError("BCNB fast table read texts invalid.")
                cell_count = int(raw_item.get("cellCount", len(texts)))
                if cell_count != len(texts):
                    raise RuntimeError("BCNB fast table read cell count mismatch.")
                if cell_count <= required_index:
                    continue
                class_code = str(texts[class_index] or "").strip()
                class_date = str(texts[date_index] or "").strip()
                if not class_code or not class_date:
                    continue
                parsed_class_date = _parse_bcnb_date(class_date, today=today)
                iso_date = (
                    parsed_class_date.date().isoformat() if parsed_class_date else None
                )
                khoi = (
                    str(texts[block_idx] or "").strip()
                    if isinstance(block_idx, int) and 0 <= block_idx < cell_count
                    else ""
                )
                if not khoi:
                    khoi = str(raw_item.get("rowBlock") or "").strip()
                lms_deadline = (
                    str(texts[lms_dl_idx] or "").strip()
                    if isinstance(lms_dl_idx, int) and 0 <= lms_dl_idx < cell_count
                    else ""
                )
                evaluation_status = (
                    re.sub(r"\s+", " ", str(texts[eval_idx] or "")).strip()
                    if isinstance(eval_idx, int) and 0 <= eval_idx < cell_count
                    else ""
                )
                raw_zalo_chk = raw_item.get("nxZaloChecked")
                raw_lms_chk = raw_item.get("nxLmsChecked")
                full_ext: dict[str, Any] = {
                    "bcnb_session_id": str(raw_item.get("sessionId") or "").strip(),
                    "khoi": khoi,
                    "nx_zalo_checked": (
                        raw_zalo_chk if isinstance(raw_zalo_chk, bool) else None
                    ),
                    "nx_lms_checked": (
                        raw_lms_chk if isinstance(raw_lms_chk, bool) else None
                    ),
                    "lms_deadline": lms_deadline,
                    "qc_issue_note": _resolve_evaluated_note_cell(
                        raw_item.get("checkerNote")
                    ),
                    "instructor_note": _resolve_evaluated_note_cell(
                        raw_item.get("instructorNote")
                    ),
                    "evaluation_status": evaluation_status,
                }
                ext_fields = {
                    k: v
                    for k, v in full_ext.items()
                    if (
                        isinstance(v, bool)
                        or (
                            isinstance(v, str)
                            and (
                                bool(v.strip())
                                or k in {"qc_issue_note", "instructor_note"}
                            )
                        )
                    )
                }
                checker_note_unreadable = (
                    "qc_issue_note" not in ext_fields
                    or not isinstance(ext_fields.get("qc_issue_note"), str)
                )
                cells = [row.locator("td").nth(i) for i in range(cell_count)]
                fast_snapshot.append(
                    {
                        "row": row,
                        "cells": cells,
                        "week": week,
                        "year": year,
                        "ma_lop": class_code,
                        "buoi_so": str(texts[lesson_index] or "").strip(),
                        "ngay_hoc": class_date,
                        "ngay_hoc_iso": iso_date,
                        "zalo_deadline": str(texts[deadline_index] or "").strip(),
                        "checker_note_unreadable": checker_note_unreadable,
                        **ext_fields,
                    }
                )
            return fast_snapshot
        except JobCancelledError:
            raise
        except Exception:
            pass

    snapshot: list[dict[str, Any]] = []
    for row in rows:
        await job_repo.ensure_not_cancelled(job_id)
        cells = await row.locator("td").all()
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
    row_code = str(bcnb_row.get("ma_lop") or "").strip().upper()
    if row_code and not str(result.get("class_code") or "").strip():
        result["class_code"] = row_code
        if st_dict is not None and not str(st_dict.get("class_code") or "").strip():
            st_dict["class_code"] = row_code
    row_lesson = str(bcnb_row.get("buoi_so") or "").strip()
    if row_lesson and not str(result.get("lesson") or "").strip():
        result["lesson"] = row_lesson
        if st_dict is not None and not str(st_dict.get("lesson") or "").strip():
            st_dict["lesson"] = row_lesson
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
    row_iso = str(bcnb_row.get("ngay_hoc_iso") or "").strip()
    if row_iso and not result.get("ngay_hoc_iso"):
        result["ngay_hoc_iso"] = row_iso
        if st_dict is not None and not st_dict.get("class_date_iso"):
            st_dict["class_date_iso"] = row_iso
    for bool_key in ("nx_zalo_checked", "nx_lms_checked"):
        bool_val = bcnb_row.get(bool_key)
        if isinstance(bool_val, bool):
            result[bool_key] = bool(bool_val)
            if st_dict is not None:
                st_dict[bool_key] = bool(bool_val)


def _is_zalo_scan_indeterminate(
    result: Mapping[str, Any],
    raw_zalo: Sequence[Any],
) -> bool:
    """Return True when the Zalo scan could not reach a verified verdict."""
    if any(
        marker in issue_text(x).lower()
        for x in raw_zalo
        for marker in _ZALO_MANUAL_CHECK_MARKERS
    ):
        return True
    if str(result.get("error_code") or "").strip():
        return True
    if str(result.get("scan_status") or "").strip().upper() == "FAILED":
        return True
    evidence_state = str(result.get("evidence_state") or "").strip().upper()
    if evidence_state == "INSUFFICIENT_EVIDENCE":
        return True
    ev = result.get("evidence")
    clf = ev.get("classifier_result") if isinstance(ev, dict) else None
    decision = (
        str((clf.get("decision") if isinstance(clf, dict) else "") or "")
        .strip()
        .upper()
    )
    if decision in {
        "SCAN_FAILED",
        "NOT_EVALUATED",
        "ZALO_SYNC_INCOMPLETE",
        "GROUP_IDENTITY_MISMATCH",
        "MESSAGE_CAP_REACHED",
        "AI_SUGGESTED_REPORT_NEEDS_REVIEW",
        "TARGET_SESSION_AMBIGUOUS",
        "UI_SENT_AT_UNREADABLE",
    }:
        return True
    if evidence_state and evidence_state not in VERIFIED_EVIDENCE_STATES:
        return True
    return False


def _collect_row_verifiable_auto_issues(
    result: dict[str, Any],
    *,
    existing_note: str | None,
    lms_slots_by_class: dict[str, list[dict[str, Any]]] | None,
    proven_auto_issues: Sequence[Any] | None = None,
) -> tuple[list[Any], bool]:
    """Return ``(effective_auto_issues, should_sync_note)`` for one BCNB row."""
    _, existing_auto = split_checker_issue_note(
        existing_note,
        proven_auto_issues=proven_auto_issues,
    )
    raw_zalo = [
        x
        for x in (result.get("zalo_content_issues") or [])
        if issue_text(x)
    ]
    zalo_indeterminate = _is_zalo_scan_indeterminate(result, raw_zalo)
    if zalo_indeterminate:
        zalo_auto: list[Any] = [
            x for x in existing_auto if issue_text(x).upper().startswith("[ZALO]")
        ]
    else:
        zalo_auto = [
            x
            for x in raw_zalo
            if not any(
                marker in issue_text(x).lower()
                for marker in _ZALO_MANUAL_CHECK_MARKERS
            )
        ]

    raw_lms = [
        x
        for x in (result.get("lms_quality_issues") or [])
        if issue_text(x)
    ]
    lms_unavailable = is_lms_fetch_unavailable(lms_slots_by_class)
    all_slots_empty_stub = bool(
        isinstance(lms_slots_by_class, Mapping)
        and lms_slots_by_class
        and all(
            isinstance(v, Sequence)
            and not isinstance(v, (str, bytes))
            and len(v) == 0
            for k, v in lms_slots_by_class.items()
            if not str(k).startswith("__")
        )
    )
    has_lms_manual_check = any(
        issue_code(x) == ISSUE_CODE_LMS_MANUAL_CHECK for x in raw_lms
    )
    target = result.get("scan_target")
    target = target if isinstance(target, dict) else {}
    spck = result.get("lms_spck", target.get("lms_spck"))
    comments_meta = result.get("lms_comments", target.get("lms_comments"))
    spck_codes = {
        ISSUE_CODE_LMS_SPCK_MISSING,
        ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
        ISSUE_CODE_LMS_SPCK_NOTE,
    }

    def _is_spck_issue_item(item: Any) -> bool:
        return issue_code(item) in spck_codes or "spck" in issue_text(item).lower()

    existing_lms_auto: list[Any] = [
        x
        for x in existing_auto
        if issue_text(x).upper().startswith(("[LMS]", "[SPCK]"))
    ]
    comments_status = (
        str(comments_meta.get("status") or "").strip().upper()
        if isinstance(comments_meta, Mapping)
        else ""
    )
    comments_verified = comments_status in {
        "COMPLETE",
        "REVIEW",
        "MISSING",
        "MISSING_ATTENDANCE",
        "EXEMPT",
    }
    spck_status = (
        str(spck.get("status") or "").strip().upper()
        if isinstance(spck, Mapping)
        else ""
    )
    spck_reason = (
        str(spck.get("reason") or "").strip().lower()
        if isinstance(spck, Mapping)
        else ""
    )
    att_meta = result.get("lms_attendance", target.get("lms_attendance"))
    spck_att = spck.get("attendance") if isinstance(spck, Mapping) else None
    spck_attendance_unverified = spck_status == "UNVERIFIED" and (
        spck_reason == "attendance_unverified"
        or (
            isinstance(att_meta, Mapping)
            and str(att_meta.get("status") or "").strip().upper() == "UNVERIFIED"
        )
        or (
            isinstance(spck_att, Mapping)
            and str(spck_att.get("status") or "").strip().upper() == "UNVERIFIED"
        )
    )
    spck_superseded = (
        spck_status
        in {
            "NOT_REQUIRED",
            "COMPLETE",
            "PARTIAL",
            "MISSING",
            "MISSING_ATTENDANCE",
            "EXEMPT",
        }
        or spck_attendance_unverified
    )

    lesson_norm = normalize_lesson_number(
        target.get("lesson") or result.get("lesson")
    )
    comments_unverified = (
        comments_status == "UNVERIFIED"
        or (
            not comments_verified
            and any(not _is_spck_issue_item(x) for x in existing_lms_auto)
            and bool(spck_status)
        )
    )
    spck_unverified = (
        (spck_status == "UNVERIFIED" and not spck_superseded)
        or (
            not spck_superseded
            and any(_is_spck_issue_item(x) for x in existing_lms_auto)
            and (
                bool(comments_status)
                or lesson_norm in {"12", "13", "14"}
            )
        )
    )
    lms_indeterminate = (
        (lms_slots_by_class is None)
        or lms_unavailable
        or has_lms_manual_check
        or comments_unverified
        or spck_unverified
    )

    current_raw_texts = {issue_text(item) for item in raw_lms if issue_text(item)}
    raw_lms_codes = {issue_code(item) for item in raw_lms if issue_code(item)}
    raw_lms_has_summary_note = ISSUE_CODE_LMS_SUMMARY_NOTE in raw_lms_codes or any(
        "dien tom tat bai" in fold_text(issue_text(item)) for item in raw_lms
    )
    dedup_semantic_codes = {
        ISSUE_CODE_LMS_SUMMARY_NOTE,
        ISSUE_CODE_LMS_COMMENT_NOTE,
        ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
        ISSUE_CODE_LMS_SPCK_MISSING,
        ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
        ISSUE_CODE_LMS_SPCK_NOTE,
    }
    superseded_items: list[Any] = []
    if spck_superseded:
        superseded_items.extend(
            item for item in existing_lms_auto if _is_spck_issue_item(item)
        )
    if comments_verified:
        superseded_items.extend(
            item for item in existing_lms_auto if not _is_spck_issue_item(item)
        )
    if not (lms_slots_by_class is None or lms_unavailable):
        already_superseded_ids = {id(item) for item in superseded_items}
        for item in existing_lms_auto:
            if id(item) in already_superseded_ids:
                continue
            c_existing = issue_code(item)
            if (
                c_existing == ISSUE_CODE_LMS_SUMMARY_NOTE and raw_lms_has_summary_note
            ) or (c_existing in dedup_semantic_codes and c_existing in raw_lms_codes):
                superseded_items.append(item)
    superseded_texts: set[str] = set()
    if superseded_items:
        history = list(result.get("lms_previous_quality_issues") or [])
        history_texts = {issue_text(item) for item in history}
        for item in superseded_items:
            t_txt = issue_text(item)
            if t_txt and t_txt not in current_raw_texts:
                superseded_texts.add(t_txt)
            if t_txt and t_txt not in history_texts and t_txt not in current_raw_texts:
                history_texts.add(t_txt)
                history.append(item)
        if history:
            result["lms_previous_quality_issues"] = history
            if isinstance(result.get("scan_target"), dict):
                result["scan_target"]["lms_previous_quality_issues"] = list(history)
        superseded_set = {id(item) for item in superseded_items}
        existing_lms_auto = [
            item for item in existing_lms_auto if id(item) not in superseded_set
        ]
    if lms_slots_by_class is None or lms_unavailable:
        lms_auto = list(existing_lms_auto)
    elif has_lms_manual_check and all_slots_empty_stub:
        lms_auto = [
            x
            for x in existing_lms_auto
            if issue_code(x) != ISSUE_CODE_LMS_MANUAL_CHECK
        ]
    elif has_lms_manual_check or comments_unverified or spck_unverified:
        # Preserve prior verified [LMS] FAIL issues while also writing the
        # single-class/slot LMS_MANUAL_CHECK NOTE issue to Mô tả vấn đề (D4),
        # dropping any legacy duplicate already represented in raw_lms.
        lms_auto = [
            x
            for x in existing_lms_auto
            if issue_code(x) != ISSUE_CODE_LMS_MANUAL_CHECK
            and not (
                issue_code(x) == ISSUE_CODE_LMS_SUMMARY_NOTE and raw_lms_has_summary_note
            )
            and not (
                issue_code(x) in dedup_semantic_codes and issue_code(x) in raw_lms_codes
            )
        ]
        seen_lms_auto = {issue_text(x) for x in lms_auto if issue_text(x)}
        for item in raw_lms:
            t_item = issue_text(item)
            if t_item and t_item not in seen_lms_auto:
                seen_lms_auto.add(t_item)
                lms_auto.append(item)
    else:
        lms_auto = list(raw_lms)

    if superseded_texts:
        for list_field in ("lms_quality_issues", "quality_issues"):
            if isinstance(result.get(list_field), list):
                result[list_field] = [
                    x for x in result[list_field] if issue_text(x) not in superseded_texts
                ]
            if isinstance(result.get("scan_target"), dict) and isinstance(
                result["scan_target"].get(list_field), list
            ):
                result["scan_target"][list_field] = [
                    x
                    for x in result["scan_target"][list_field]
                    if issue_text(x) not in superseded_texts
                ]

    combined = [*zalo_auto, *lms_auto]
    if existing_auto and (zalo_indeterminate or lms_indeterminate):
        def _to_structured_or_raw(item: Any) -> Any:
            return (
                coerce_structured_issue(item)
                if issue_code(item) != ISSUE_CODE_LEGACY_UNCLASSIFIED_FAIL
                else item
            )

        if zalo_indeterminate and zalo_auto:
            cur_zi = list(result.get("zalo_content_issues") or [])
            seen_zi = {issue_text(item) for item in cur_zi if issue_text(item)}
            for restored_z in zalo_auto:
                rz_text = issue_text(restored_z)
                if rz_text and rz_text not in seen_zi:
                    seen_zi.add(rz_text)
                    cur_zi.append(_to_structured_or_raw(restored_z))
            result["zalo_content_issues"] = cur_zi
            if isinstance(result.get("scan_target"), dict):
                result["scan_target"]["zalo_content_issues"] = list(cur_zi)

        if lms_indeterminate and lms_auto:
            cur_li = list(result.get("lms_quality_issues") or [])
            seen_li = {issue_text(item) for item in cur_li if issue_text(item)}
            for restored_l in lms_auto:
                rl_text = issue_text(restored_l)
                if rl_text and rl_text not in seen_li:
                    seen_li.add(rl_text)
                    cur_li.append(_to_structured_or_raw(restored_l))
            result["lms_quality_issues"] = cur_li
            if isinstance(result.get("scan_target"), dict):
                result["scan_target"]["lms_quality_issues"] = list(cur_li)

        cur_qi = list(result.get("quality_issues") or [])
        seen_qi_texts = {issue_text(item) for item in cur_qi if issue_text(item)}
        for restored in combined:
            r_text = issue_text(restored)
            if r_text and r_text not in seen_qi_texts:
                seen_qi_texts.add(r_text)
                cur_qi.append(_to_structured_or_raw(restored))
        result["quality_issues"] = cur_qi
        if isinstance(result.get("scan_target"), dict):
            result["scan_target"]["quality_issues"] = list(cur_qi)
    before_clean = preserve_bcnb_note_text(existing_note)
    merged = merge_checker_issue_note(
        before_clean,
        combined,
        proven_auto_issues=proven_auto_issues,
    )
    if lms_unavailable:
        should_sync = bool(zalo_auto) or (
            bool(existing_auto) and merged != before_clean
        )
    else:
        should_sync = bool(combined) or (
            bool(existing_auto) and merged != before_clean
        )
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
    on_row_finalized: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    lms_quality_gateway: Any = None,
    lms_slots_by_class: dict[str, list[dict[str, Any]]] | None = None,
    notes_only: bool | None = False,
    lms_ai_reviewer: Any = None,
    reset_ai_batch_counter: bool = True,
    tick_lms: bool = False,
    check_only: bool = False,
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
        *(scan_plan.get("exempt_targets") or []),
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
        {
            _normalized_target_key_str(str(key))
            for key in selected_target_keys_raw
            if str(key).strip()
        }
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
        for source in (
            *(scan_plan.get("targets") or []),
            *(scan_plan.get("exempt_targets") or []),
        ):
            if isinstance(source, dict):
                try:
                    pair = (int(source["week"]), int(source["year"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if pair in active_pairs:
                    target_pairs.add(pair)
        for source in (scan_plan.get("deferred_targets") or []):
            if isinstance(source, dict):
                try:
                    deferred_key = _normalized_target_key_from_mapping(source)
                    pair = (int(source["week"]), int(source["year"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if deferred_key in selected_target_key_set and pair in active_pairs:
                    target_pairs.add(pair)
    failures: list[dict[str, Any]] = []
    lms_failures: list[dict[str, Any]] = []
    checker_note_failures: list[dict[str, Any]] = []
    target_outcomes: list[dict[str, Any]] = []
    lms_target_outcomes: list[dict[str, Any]] = []
    checker_note_outcomes: list[dict[str, Any]] = []

    async def emit_outcome(item: dict[str, Any]) -> None:
        if str(item.get("action_type") or "") == "BCNB_UPDATE_CHECKER_NOTE":
            return
        target_outcomes.append(item)
        if on_target_outcome is not None:
            await on_target_outcome(item)

    updated_count = 0
    lms_updated_count = 0
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
    resolved_lms_slots = await fetch_and_evaluate_lms_quality(
        page,
        scoped_results,
        lms_slots_by_class=lms_slots_by_class,
        lms_quality_gateway=lms_quality_gateway,
        ai_reviewer=lms_ai_reviewer,
        latest_week=latest_pair,
        now=current_time,
        reset_ai_batch_counter=reset_ai_batch_counter,
    )

    lms_times_by_identity: dict[tuple[int, int, str, str, str], tuple[str | None, str | None]] = {}
    for scoped_item in scoped_results:
        scoped_st = scoped_item.get("scan_target")
        scoped_source = scoped_st if isinstance(scoped_st, dict) else scoped_item
        scoped_ident = scan_target_identity(scoped_source) or scan_target_identity(scoped_item)
        if scoped_ident is not None:
            l_start = scoped_item.get("lms_start_time") or (
                scoped_st.get("lms_start_time") if isinstance(scoped_st, dict) else None
            )
            l_end = scoped_item.get("lms_end_time") or (
                scoped_st.get("lms_end_time") if isinstance(scoped_st, dict) else None
            )
            if l_start or l_end:
                lms_times_by_identity[scoped_ident] = (
                    str(l_start) if l_start else None,
                    str(l_end) if l_end else None,
                )

    def _sync_row_checked_flags(
        target_obj: dict[str, Any] | None,
        row_obj: Mapping[str, Any],
    ) -> None:
        if not isinstance(target_obj, dict):
            return
        for flag_key in ("nx_zalo_checked", "nx_lms_checked"):
            flag_val = row_obj.get(flag_key)
            if isinstance(flag_val, bool):
                target_obj[flag_key] = flag_val
                st_obj = target_obj.get("scan_target")
                if isinstance(st_obj, dict):
                    st_obj[flag_key] = flag_val

    def _is_missing_zalo_group_result(target_obj: Mapping[str, Any] | None) -> bool:
        if not isinstance(target_obj, Mapping):
            return False
        if target_obj.get("missing_zalo_group"):
            return True
        st_obj = target_obj.get("scan_target")
        return isinstance(st_obj, Mapping) and bool(st_obj.get("missing_zalo_group"))

    def _match_refreshed_row(
        refreshed_rows: list[dict[str, Any]],
        reference_row: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        ref_sid = str(reference_row.get("bcnb_session_id") or "").strip()
        ref_code = str(reference_row.get("ma_lop") or "").strip().upper()
        ref_lesson = str(reference_row.get("buoi_so") or "").strip()
        ref_date = reference_row.get("ngay_hoc_iso")
        candidates = [
            item
            for item in refreshed_rows
            if item["ma_lop"].strip().upper() == ref_code
            and item["buoi_so"].strip() == ref_lesson
            and item.get("ngay_hoc_iso") == ref_date
            and (
                not ref_sid
                or str(item.get("bcnb_session_id") or "").strip() == ref_sid
            )
        ]
        return candidates[0] if len(candidates) == 1 else None

    async def _ensure_row_lms_evaluated(
        target_result: dict[str, Any], row_obj: Mapping[str, Any]
    ) -> bool:
        nonlocal resolved_lms_slots
        row_code = str(row_obj.get("ma_lop") or "").strip().upper()
        row_date_iso = str(row_obj.get("ngay_hoc_iso") or "").strip()
        if row_date_iso and not target_result.get("ngay_hoc_iso"):
            target_result["ngay_hoc_iso"] = row_date_iso
        page_used_for_lms = False
        if (
            lms_slots_by_class is None
            and row_code
            and (
                not isinstance(resolved_lms_slots, dict)
                or row_code not in resolved_lms_slots
            )
            and not is_lms_fetch_unavailable(resolved_lms_slots)
        ):
            extra_lms_slots = await fetch_and_evaluate_lms_quality(
                page,
                [target_result],
                lms_slots_by_class=None,
                lms_quality_gateway=lms_quality_gateway,
                ai_reviewer=lms_ai_reviewer,
                latest_week=latest_pair,
                now=current_time,
                reset_ai_batch_counter=False,
            )
            if isinstance(extra_lms_slots, dict):
                page_used_for_lms = True
                if isinstance(resolved_lms_slots, dict):
                    resolved_lms_slots.update(extra_lms_slots)
                else:
                    resolved_lms_slots = dict(extra_lms_slots)
        await inspect_and_attach_quality_issues_async(
            [target_result],
            lms_slots_by_class=resolved_lms_slots,
            now=current_time,
            ai_reviewer=lms_ai_reviewer,
            latest_week=latest_pair,
            reset_ai_batch_counter=False,
        )
        return page_used_for_lms

    async def _maybe_update_row_checker_note(
        target_result: dict[str, Any] | None,
        bcnb_row: dict[str, Any],
        *,
        selected_week: int,
        selected_year: int,
    ) -> bool:
        nonlocal checker_note_updated_count
        if (
            target_result is None
            or _is_missing_zalo_group_result(target_result)
            or audit_repo is None
            or not hasattr(audit_repo, "record_intent")
        ):
            return False
        row_code = str(bcnb_row.get("ma_lop") or "").strip().upper()
        row_lesson = str(bcnb_row.get("buoi_so") or "").strip()
        row_date_iso = str(bcnb_row.get("ngay_hoc_iso") or "").strip()
        lms_used_page = await _ensure_row_lms_evaluated(target_result, bcnb_row)
        has_readable_note = (
            isinstance(bcnb_row.get("qc_issue_note"), str)
            and not bool(bcnb_row.get("checker_note_unreadable"))
        )
        existing_note = (
            str(bcnb_row["qc_issue_note"])
            if has_readable_note
            else str(target_result.get("qc_issue_note") or "")
        )
        row_sid = str(
            bcnb_row.get("bcnb_session_id")
            or _result_session_id(target_result)
            or ""
        ).strip()
        proven_auto_issues = await resolve_row_proven_auto_issues(
            audit_repo,
            class_code=row_code,
            lesson=row_lesson,
            week=selected_week,
            year=selected_year,
            session_id=row_sid or None,
            class_date_iso=row_date_iso or None,
        )
        effective_auto, should_sync = _collect_row_verifiable_auto_issues(
            target_result,
            existing_note=existing_note,
            lms_slots_by_class=resolved_lms_slots,
            proven_auto_issues=proven_auto_issues,
        )
        if not should_sync:
            return lms_used_page
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
            checker_note_failures.append(failure)
            unreadable_outcome = {
                **failure,
                "class_date_iso": row_date_iso or None,
                "status": "FAILED_BEFORE_WRITE",
            }
            checker_note_outcomes.append(unreadable_outcome)
            target_result["checker_note_outcome"] = "FAILED"
            log_message(
                f"Bỏ qua cập nhật Mô tả vấn đề BCNB {row_code}/{row_lesson} "
                f"W{selected_week}/{selected_year}: {err_msg}",
                level="WARNING",
                event="bcnb.checker_note_failed",
                class_code=row_code,
                lesson=row_lesson,
                week=selected_week,
                year=selected_year,
            )
            return lms_used_page
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
            return lms_used_page

        merged_note = merge_checker_issue_note(
            existing_note,
            effective_auto,
            proven_auto_issues=proven_auto_issues,
        )
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
                proven_auto_issues=proven_auto_issues,
                reason={
                    "week": selected_week,
                    "year": selected_year,
                    "lesson": row_lesson,
                    "class_date": row_date_iso or None,
                    "bcnb_session_id": row_sid or None,
                    "auto_issues": effective_auto,
                    "existing_note": preserve_bcnb_note_text(existing_note),
                    "merged_note": merged_note,
                },
            )
        except JobCancelledError:
            raise
        except Exception as note_error:
            is_conflict = isinstance(note_error, BcnbConflictAfterWriteError)
            unknown = isinstance(note_error, BcnbOutcomeUnknownError)
            note_status = (
                "CONFLICT_AFTER_WRITE"
                if execution_mode == "production" and is_conflict
                else (
                    "OUTCOME_UNKNOWN"
                    if execution_mode == "production" and unknown
                    else "FAILED_BEFORE_WRITE"
                )
            )
            note_err_code = (
                "BCNB_CHECKER_NOTE_CONFLICT"
                if is_conflict
                else (
                    "BCNB_CHECKER_NOTE_OUTCOME_UNKNOWN"
                    if unknown
                    else "BCNB_CHECKER_NOTE_FAILED"
                )
            )
            note_safe_error = (
                "Phát hiện thay đổi ngoài luồng trên ô Mô tả vấn đề BCNB."
                if is_conflict
                else (
                    "Không xác minh được trạng thái ô Mô tả vấn đề sau khi ghi BCNB."
                    if unknown
                    else "Cập nhật ô Mô tả vấn đề trên BCNB thất bại."
                )
            )
            failure = {
                "class_code": row_code,
                "lesson": row_lesson,
                "week": selected_week,
                "year": selected_year,
                "action_type": "BCNB_UPDATE_CHECKER_NOTE",
                "error_code": note_err_code,
                "error": note_safe_error,
            }
            checker_note_failures.append(failure)
            err_outcome = {
                **failure,
                "class_date_iso": row_date_iso or None,
                "status": note_status,
            }
            checker_note_outcomes.append(err_outcome)
            target_result["checker_note_outcome"] = (
                note_status
                if note_status in {"CONFLICT_AFTER_WRITE", "OUTCOME_UNKNOWN"}
                else "FAILED"
            )
            log_message(
                f"Bỏ qua lỗi cập nhật Mô tả vấn đề BCNB {row_code}/{row_lesson} "
                f"W{selected_week}/{selected_year}: {type(note_error).__name__}",
                level="ERROR",
                event="bcnb.checker_note_failed",
                class_code=row_code,
                lesson=row_lesson,
                week=selected_week,
                year=selected_year,
                error_type=type(note_error).__name__,
            )
            return execution_mode == "production" or lms_used_page

        if note_outcome == "NO_CHANGE":
            return lms_used_page
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
        return execution_mode == "production" or lms_used_page

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
        _, _, report_index = (
            (0, 0, 0)
            if (notes_only or check_only)
            else await resolve_report_columns(page)
        )
        latest_bcnb_rows = bcnb_rows
        dom_dirty = False
        is_current_week = (selected_week, selected_year) == latest_pair
        log_message(
            f"Đã kiểm chứng BCNB ở Tuần {selected_week} / {selected_year}: "
            f"{len(bcnb_rows)} dòng."
        )

        async def _relocate_fresh_row(reference_row: dict[str, Any]) -> dict[str, Any] | None:
            nonlocal latest_bcnb_rows, dom_dirty, report_index
            refreshed_rows = await _read_bcnb_schedule_rows(
                page,
                week=selected_week,
                year=selected_year,
                job_id=job_id,
                today=today,
                job_repo=job_repo,
            )
            if not notes_only and not check_only:
                _, _, report_index = await resolve_report_columns(page)
            latest_bcnb_rows = refreshed_rows
            dom_dirty = False
            return _match_refreshed_row(refreshed_rows, reference_row)

        for bcnb_row in bcnb_rows:
            await job_repo.ensure_not_cancelled(job_id)
            if selected_target_key_set is not None:
                try:
                    row_canonical_key = _normalized_target_key_from_mapping(
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
            is_ineligible_current_week = False
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
                        if not check_only:
                            log_message(
                                f"Bỏ qua {bcnb_row['ma_lop']}/{bcnb_row['buoi_so']}: "
                                "chưa có target Zalo đủ giờ học để đối chiếu.",
                                level="INFO",
                            )
                            continue
                        is_ineligible_current_week = True
                else:
                    effective_target = dict(scheduled_target)
                    if row_identity in lms_times_by_identity:
                        l_start, l_end = lms_times_by_identity[row_identity]
                        if l_start and not effective_target.get("lms_start_time"):
                            effective_target["lms_start_time"] = l_start
                        if l_end and not effective_target.get("lms_end_time"):
                            effective_target["lms_end_time"] = l_end
                    eligibility = evaluate_scan_target(
                        effective_target,
                        latest_week=latest_pair,
                        now=current_time,
                    )
                    if not eligibility.eligible:
                        if not check_only:
                            log_message(
                                f"Bỏ qua {bcnb_row['ma_lop']}/{bcnb_row['buoi_so']}: "
                                f"{eligibility.reason}; chưa quét và không tick BCNB.",
                                level="INFO",
                            )
                            continue
                        is_ineligible_current_week = True
            if execution_mode == "production" and not check_only:
                if dom_dirty:
                    refreshed = await _relocate_fresh_row(bcnb_row)
                elif latest_bcnb_rows is not bcnb_rows:
                    refreshed = _match_refreshed_row(latest_bcnb_rows, bcnb_row)
                else:
                    refreshed = bcnb_row
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
            row_sid = str(bcnb_row.get("bcnb_session_id") or "").strip()
            row_is_lesson_14 = is_lesson_14(lesson)
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
                    for candidate_row in latest_bcnb_rows
                    if _result_matches_bcnb_row_for_metadata(
                        meta_row_matches[0],
                        candidate_row,
                        (selected_week, selected_year),
                    )
                ]
                if len(matching_bcnb_rows) == 1:
                    unique_meta_result = meta_row_matches[0]
                    _attach_bcnb_row_metadata(unique_meta_result, bcnb_row)
                    _sync_row_checked_flags(unique_meta_result, bcnb_row)
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
                if result.get("evidence_state") in SUBMITTED_EVIDENCE_STATES
            ]
            if len(result_matches) > 1:
                raise RuntimeError(
                    f"Dòng {class_code}/{lesson} khớp nhiều báo cáo Zalo; "
                    "dừng để tránh tick nhầm."
                )
            matched_result = result_matches[0] if result_matches else None
            _sync_row_checked_flags(matched_result, bcnb_row)
            if check_only:
                if not is_ineligible_current_week:
                    if row_is_lesson_14:
                        matched_result = _apply_lesson_14_result(scan_results, bcnb_row)
                        _attach_bcnb_row_metadata(matched_result, bcnb_row)
                        _sync_row_checked_flags(matched_result, bcnb_row)
                    elif matched_result is not None:
                        _apply_bcnb_deadline_state(
                            matched_result,
                            bcnb_row,
                            today=today,
                        )
                        _sync_row_checked_flags(matched_result, bcnb_row)
                continue
            row_missing_zalo = (
                _is_missing_zalo_group_result(matched_result)
                or _is_missing_zalo_group_result(unique_meta_result)
            )
            if not check_only and row_missing_zalo:
                log_message(
                    f"Bỏ qua ghi BCNB cho {class_code}/{lesson}: chưa khớp nhóm Zalo.",
                    level="INFO",
                )
                continue
            if notes_only:
                note_target = matched_result or unique_meta_result
                if row_is_lesson_14 and note_target is None:
                    note_target = _apply_lesson_14_result(scan_results, bcnb_row)
                    _attach_bcnb_row_metadata(note_target, bcnb_row)
                if await _maybe_update_row_checker_note(
                    note_target,
                    bcnb_row,
                    selected_week=selected_week,
                    selected_year=selected_year,
                ):
                    if execution_mode == "production":
                        dom_dirty = True
                continue
            is_submitted = False

            if row_is_lesson_14:
                is_submitted = True
                matched_result = _apply_lesson_14_result(scan_results, bcnb_row)
                _attach_bcnb_row_metadata(matched_result, bcnb_row)
                _sync_row_checked_flags(matched_result, bcnb_row)
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

            need_row_refresh = False
            report_tick_failed = False
            lms_tick_failed = False
            halt_blocked = False
            row_stage_a_outcome: dict[str, Any] | None = None

            async def _record_stage_a_outcome(item: dict[str, Any]) -> None:
                nonlocal row_stage_a_outcome
                row_stage_a_outcome = item
                await emit_outcome(item)

            async def _finalize_row_if_needed() -> None:
                if row_stage_a_outcome is not None and on_row_finalized is not None:
                    await on_row_finalized(
                        {
                            **row_stage_a_outcome,
                            "target_result": matched_result,
                        }
                    )

            # Stage A: BCNB NX Zalo checkbox tick
            if is_submitted:
                checkbox = cells[report_index].locator('input[type="checkbox"]').first
                if await checkbox.count() == 0:
                    raise RuntimeError(f"BCNB không có checkbox báo cáo cho {class_code}/{lesson}.")
                is_checked = await checkbox.is_checked()
                if is_checked and not row_is_lesson_14:
                    if matched_result is not None:
                        matched_result["nx_zalo_checked"] = True
                        if isinstance(matched_result.get("scan_target"), dict):
                            matched_result["scan_target"]["nx_zalo_checked"] = True
                    await _record_stage_a_outcome(
                        {
                            "class_code": class_code,
                            "lesson": lesson,
                            "week": selected_week,
                            "year": selected_year,
                            "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                            "status": "ALREADY_DONE",
                        }
                    )
                elif should_halt is not None and should_halt():
                    halt_blocked = True
                    await _record_stage_a_outcome(
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
                else:
                    evidence = (matched_result or {}).get("evidence", {})
                    context = evidence.get("date_context", {}) or {}
                    classifier = evidence.get("classifier_result", {}) or {}
                    candidates = classifier.get("candidates") or []
                    candidate = candidates[-1] if candidates else {}
                    evidence_path, evidence_sha256 = get_evidence_reference(matched_result)
                    try:
                        if execution_mode == "production":
                            dom_dirty = True
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
                            class_date_iso=bcnb_row.get("ngay_hoc_iso") or None,
                            session_id=row_sid or None,
                            execution_mode=execution_mode,
                            reason={
                                "week": selected_week,
                                "year": selected_year,
                                "lesson": lesson,
                                "class_date": bcnb_row.get("ngay_hoc_iso"),
                                "bcnb_session_id": row_sid or None,
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
                                "lesson_14_exemption": row_is_lesson_14,
                                "evidence_decision": LESSON_14_EXEMPTION if row_is_lesson_14 else None,
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
                        report_tick_failed = True
                        unknown = isinstance(action_error, BcnbOutcomeUnknownError)
                        if execution_mode == "production":
                            dom_dirty = True
                            need_row_refresh = True
                        failure_code = (
                            "BCNB_REPORT_OUTCOME_UNKNOWN"
                            if unknown
                            else "BCNB_REPORT_TICK_FAILED"
                        )
                        failure_safe_error = (
                            "Không xác minh được trạng thái NX Zalo sau khi ghi BCNB."
                            if unknown
                            else "Cập nhật NX Zalo trên BCNB thất bại."
                        )
                        failure = {
                            "class_code": class_code,
                            "lesson": lesson,
                            "week": selected_week,
                            "year": selected_year,
                            "error_code": failure_code,
                            "error": failure_safe_error,
                        }
                        failures.append(failure)
                        await _record_stage_a_outcome(
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
                            f"W{selected_week}/{selected_year}; tiếp tục lớp kế tiếp: {type(action_error).__name__}",
                            level="ERROR",
                            event="bcnb.update_row_failed",
                            class_code=class_code,
                            lesson=lesson,
                            week=selected_week,
                            year=selected_year,
                            error_type=type(action_error).__name__,
                        )
                    else:
                        updated_count += 1
                        if outcome == "VERIFIED":
                            if execution_mode == "production":
                                dom_dirty = True
                                need_row_refresh = True
                            if matched_result is not None:
                                matched_result["nx_zalo_checked"] = True
                                if isinstance(matched_result.get("scan_target"), dict):
                                    matched_result["scan_target"]["nx_zalo_checked"] = True
                        await _record_stage_a_outcome(
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
                                + (" (Miễn báo cáo Buổi 14)" if row_is_lesson_14 else "")
                            )
                        else:
                            log_message(
                                f"Đã tick và kiểm chứng BCNB cho lớp: {class_code} "
                                f"W{selected_week}/{selected_year}"
                            )

            if halt_blocked:
                await _finalize_row_if_needed()
                continue

            # Stage B: BCNB NX LMS checkbox tick (positive verification required)
            lms_target = matched_result or unique_meta_result
            if tick_lms and lms_target is None and row_is_lesson_14 and not row_missing_zalo:
                lms_target = _apply_lesson_14_result(scan_results, bcnb_row)
                _attach_bcnb_row_metadata(lms_target, bcnb_row)
                _sync_row_checked_flags(lms_target, bcnb_row)

            if (
                tick_lms
                and lms_target is not None
                and not _is_missing_zalo_group_result(lms_target)
                and not is_lms_fetch_unavailable(resolved_lms_slots)
            ):
                if await _ensure_row_lms_evaluated(lms_target, bcnb_row):
                    if execution_mode == "production":
                        dom_dirty = True
                        need_row_refresh = True
                lms_ready, lms_ready_reason = is_lms_ready_for_bcnb_tick(lms_target)
                if lms_ready:
                    if need_row_refresh:
                        refreshed_after_report = await _relocate_fresh_row(bcnb_row)
                        need_row_refresh = False
                        if refreshed_after_report is None:
                            lms_tick_failed = True
                            lms_missing_err = (
                                f"Không tìm lại được dòng {class_code}/{lesson} sau reload để tick NX LMS."
                            )
                            lms_fail_entry = {
                                "class_code": class_code,
                                "lesson": lesson,
                                "week": selected_week,
                                "year": selected_year,
                                "action_type": "BCNB_LMS_TICK",
                                "error": lms_missing_err,
                            }
                            lms_failures.append(lms_fail_entry)
                            lms_target_outcomes.append(
                                {
                                    **lms_fail_entry,
                                    "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                                    "status": "FAILED_BEFORE_WRITE",
                                }
                            )
                        else:
                            bcnb_row = refreshed_after_report
                            cells = bcnb_row["cells"]
                    if not lms_tick_failed:
                        lms_action_invoked = False
                        try:
                            _, lms_index = await resolve_lms_columns(page)
                            lms_checkbox = cells[lms_index].locator('input[type="checkbox"]').first
                            if await lms_checkbox.count() == 0:
                                raise RuntimeError(
                                    f"BCNB không có checkbox NX LMS cho {class_code}/{lesson}."
                                )
                            lms_checked = await lms_checkbox.is_checked()
                            if lms_checked:
                                lms_target["nx_lms_checked"] = True
                                if isinstance(lms_target.get("scan_target"), dict):
                                    lms_target["scan_target"]["nx_lms_checked"] = True
                                lms_target_outcomes.append(
                                    {
                                        "class_code": class_code,
                                        "lesson": lesson,
                                        "week": selected_week,
                                        "year": selected_year,
                                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                                        "action_type": "BCNB_LMS_TICK",
                                        "status": "ALREADY_DONE",
                                    }
                                )
                            elif should_halt is not None and should_halt():
                                halt_blocked = True
                                lms_target_outcomes.append(
                                    {
                                        "class_code": class_code,
                                        "lesson": lesson,
                                        "week": selected_week,
                                        "year": selected_year,
                                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                                        "action_type": "BCNB_LMS_TICK",
                                        "status": "BLOCKED_BATCH_HALT",
                                        "error": "Batch đã halt trước click NX LMS.",
                                    }
                                )
                            else:
                                raw_lms_spck = lms_target.get("lms_spck")
                                lms_spck_obj: dict[str, Any] = (
                                    raw_lms_spck
                                    if isinstance(raw_lms_spck, dict)
                                    else {}
                                )
                                lms_action_invoked = True
                                if execution_mode == "production":
                                    dom_dirty = True
                                lms_outcome = await execute_verified_checkbox_action(
                                    page=page,
                                    checkbox=lms_checkbox,
                                    audit_repo=audit_repo,
                                    job_repo=job_repo,
                                    job_id=job_id,
                                    action_key=(
                                        f"bcnb-lms:{job_id}:{selected_year}:W{selected_week}:"
                                        f"{class_code}:{lesson}:{row_sid or bcnb_row.get('ngay_hoc_iso') or 'na'}"
                                    ),
                                    action_type="BCNB_LMS_TICK",
                                    class_code=class_code,
                                    column_kind="lms",
                                    week=selected_week,
                                    year=selected_year,
                                    lesson=lesson,
                                    class_date_iso=bcnb_row.get("ngay_hoc_iso") or None,
                                    session_id=row_sid or None,
                                    execution_mode=execution_mode,
                                    reason={
                                        "week": selected_week,
                                        "year": selected_year,
                                        "lesson": lesson,
                                        "class_date": bcnb_row.get("ngay_hoc_iso"),
                                        "bcnb_session_id": row_sid or None,
                                        "lms_ready_reason": lms_ready_reason,
                                        "lms_spck_status": lms_spck_obj.get("status"),
                                    },
                                    evidence_path=None,
                                    evidence_sha256=None,
                                    require_evidence_registry=False,
                                    settle_seconds=0.7,
                                    should_halt=should_halt,
                                )
                                lms_updated_count += 1
                                if lms_outcome == "VERIFIED":
                                    if execution_mode == "production":
                                        dom_dirty = True
                                        need_row_refresh = True
                                    lms_target["nx_lms_checked"] = True
                                    if isinstance(lms_target.get("scan_target"), dict):
                                        lms_target["scan_target"]["nx_lms_checked"] = True
                                lms_target_outcomes.append(
                                    {
                                        "class_code": class_code,
                                        "lesson": lesson,
                                        "week": selected_week,
                                        "year": selected_year,
                                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                                        "action_type": "BCNB_LMS_TICK",
                                        "status": lms_outcome,
                                    }
                                )
                                if lms_outcome == "DRY_RUN":
                                    log_message(
                                        f"[DRY-RUN] Sẽ Tick xanh NX LMS cho lớp: {class_code}/{lesson} "
                                        f"W{selected_week}/{selected_year}"
                                    )
                                else:
                                    log_message(
                                        f"Đã tick và kiểm chứng NX LMS cho lớp: {class_code}/{lesson} "
                                        f"W{selected_week}/{selected_year}"
                                    )
                        except JobCancelledError:
                            raise
                        except Exception as lms_error:
                            lms_tick_failed = True
                            lms_unknown = isinstance(lms_error, BcnbOutcomeUnknownError)
                            if execution_mode == "production" and lms_action_invoked:
                                dom_dirty = True
                                need_row_refresh = True
                            lms_status = (
                                "OUTCOME_UNKNOWN"
                                if execution_mode == "production" and lms_unknown
                                else "FAILED_BEFORE_WRITE"
                            )
                            lms_error_code = (
                                "BCNB_LMS_OUTCOME_UNKNOWN"
                                if lms_unknown
                                else "BCNB_LMS_TICK_FAILED"
                            )
                            lms_safe_error = (
                                "Không xác minh được trạng thái NX LMS sau khi ghi BCNB."
                                if lms_unknown
                                else "Cập nhật NX LMS trên BCNB thất bại."
                            )
                            lms_failure = {
                                "class_code": class_code,
                                "lesson": lesson,
                                "week": selected_week,
                                "year": selected_year,
                                "action_type": "BCNB_LMS_TICK",
                                "error_code": lms_error_code,
                                "error": lms_safe_error,
                            }
                            lms_failures.append(lms_failure)
                            lms_target_outcomes.append(
                                {
                                    **lms_failure,
                                    "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                                    "status": lms_status,
                                }
                            )
                            lms_target["bcnb_lms_update_state"] = "FAILED"
                            log_message(
                                f"Bỏ qua lỗi cập nhật NX LMS BCNB {class_code}/{lesson} "
                                f"W{selected_week}/{selected_year}; tiếp tục lớp kế tiếp: {type(lms_error).__name__}",
                                level="ERROR",
                                event="bcnb.lms_tick_failed",
                                class_code=class_code,
                                lesson=lesson,
                                week=selected_week,
                                year=selected_year,
                                error_type=type(lms_error).__name__,
                            )

            if halt_blocked:
                await _finalize_row_if_needed()
                continue

            # Stage C: Checker note update (skipped if any checkbox tick on this row failed)
            note_target = (
                matched_result
                or unique_meta_result
                or (lms_target if tick_lms else None)
            )
            if report_tick_failed or lms_tick_failed:
                if note_target is not None:
                    note_target["checker_note_outcome"] = "SKIPPED_TICK_FAILED"
                checker_note_outcomes.append(
                    {
                        "class_code": class_code,
                        "lesson": lesson,
                        "week": selected_week,
                        "year": selected_year,
                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                        "action_type": "BCNB_UPDATE_CHECKER_NOTE",
                        "status": "SKIPPED_TICK_FAILED",
                        "error": "Bỏ qua cập nhật Mô tả vấn đề vì bước tick báo cáo hoặc NX LMS thất bại.",
                    }
                )
                await _finalize_row_if_needed()
                continue

            if need_row_refresh:
                refreshed_for_note = await _relocate_fresh_row(bcnb_row)
                need_row_refresh = False
                if refreshed_for_note is None:
                    log_message(
                        f"Không tìm lại được dòng {class_code}/{lesson} sau tick để ghi Mô tả vấn đề.",
                        level="WARNING",
                    )
                    await _finalize_row_if_needed()
                    continue
                bcnb_row = refreshed_for_note

            if await _maybe_update_row_checker_note(
                note_target,
                bcnb_row,
                selected_week=selected_week,
                selected_year=selected_year,
            ):
                if execution_mode == "production":
                    dom_dirty = True
            await _finalize_row_if_needed()

    await inspect_and_attach_quality_issues_async(
        scoped_results,
        lms_slots_by_class=resolved_lms_slots,
        now=current_time,
        ai_reviewer=lms_ai_reviewer,
        latest_week=latest_pair,
        reset_ai_batch_counter=False,
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
        "lms_updated_count": lms_updated_count,
        "checker_note_updated_count": checker_note_updated_count,
        "checker_note_failures": checker_note_failures,
        "checker_note_failure_count": len(checker_note_failures),
        "checker_note_skipped_tick_failed_count": sum(
            1
            for item in checker_note_outcomes
            if item.get("status") == "SKIPPED_TICK_FAILED"
        ),
        "failed_targets": failures,
        "lms_failed_targets": lms_failures,
        "target_outcomes": target_outcomes,
        "lms_target_outcomes": lms_target_outcomes,
        "checker_note_outcomes": checker_note_outcomes,
        "lms_fetch_unavailable": bool(is_lms_fetch_unavailable(resolved_lms_slots)),
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
    if (
        str(item.get("evidence_state") or "").strip().upper()
        in ALREADY_SUBMITTED_EVIDENCE_STATES
    ):
        return True
    if categorize_row(item) in {CATEGORY_DONE, CATEGORY_LATE, CATEGORY_EXEMPT}:
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
    """Update the source ZALO_SCAN job results & batch target row in SQLite (production VERIFIED only)."""
    source_job = await job_repo.get_job(source_job_id)
    if not source_job:
        return None, [], None, None
    raw_source_results = source_job.get("results")
    if isinstance(raw_source_results, (dict, list)):
        raw_source_results = json.loads(
            json.dumps(raw_source_results, ensure_ascii=False)
        )
    envelope, items = parse_scan_job_results_envelope(raw_source_results)
    snapshot_id = envelope.get("snapshot_id") if isinstance(envelope, dict) else None
    required_week = envelope.get("required_week") if isinstance(envelope, dict) else None
    verified_production = (not dry_run) and outcome == "VERIFIED"
    if not verified_production:
        return None, items, snapshot_id, required_week

    matches = find_manual_approve_target_matches(
        items,
        group_name=group_name,
        week=week,
        year=year,
        lesson=lesson,
        class_date_iso=class_date_iso,
        class_code=class_code,
    )
    approved_status = "✅ Đã duyệt tay & Tích BCNB"
    approved_detail = f"Duyệt tay bởi {approved_by} · BCNB W{week}/{year} buổi {lesson}"
    updated_item: dict[str, Any] | None = None
    if len(matches) == 1:
        _, matched_item, _ = matches[0]
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
            "dry_run": False,
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

    try:
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
    except Exception as ledger_err:
        err_type = type(ledger_err).__name__
        log_message(
            f"Không thể cập nhật ledger zalo_batch_targets cho duyệt tay {target_key} ({err_type}).",
            level="WARNING",
            event="bcnb.manual_approve_ledger_update_failed",
            error_type=err_type,
            source_job_id=source_job_id,
            approval_job_id=approval_job_id,
            target_key=target_key,
        )
        add_event = getattr(job_repo, "add_event", None)
        if add_event is not None:
            with contextlib.suppress(Exception):
                await add_event(
                    approval_job_id,
                    "MANUAL_APPROVE_LEDGER_UPDATE_FAILED",
                    f"Lỗi cập nhật ledger duyệt tay ({err_type}).",
                    {
                        "error_type": err_type,
                        "source_job_id": source_job_id,
                        "target_key": target_key,
                    },
                )

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
        err_type = type(exc).__name__
        log_message(
            f"Lỗi duyệt tay BCNB: {err_type}",
            level="ERROR",
            event="bcnb.manual_approve_failed",
            error_type=err_type,
        )
        await selected_job_repo.update_job(
            job_id,
            status="FAILED",
            progress=100,
            message=f"Dừng an toàn: {err_type}.",
        )
