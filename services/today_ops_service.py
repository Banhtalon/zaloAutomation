"""Operational service for the dedicated 'Lớp Hôm Nay (Live)' page.

Aggregates scheduled sessions on a given business day across BOTH bounded
BCNB weeks (``required_week`` and ``latest_week``), performs live LMS + Zalo +
SPCK B12-14 verification, and synchronizes verified ``NX Zalo`` and ``NX LMS``
ticks back to the exact BCNB week and row without touching unended classes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import contextlib
from datetime import date, datetime
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit

from core.container import AppContainer
from core.time_utils import (
    business_day_start,
    now_business,
    now_utc,
    parse_timestamp,
    utc_iso,
)
from db.database import get_db_connection
from googleform_autosubmit.config import Config
from logger_utils import log_message
from repositories.audit_repository import AuditRepository
from repositories.evidence_repository import EvidenceRepository
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobCancelledError, JobRepository
from repositories.zalo_batch_repository import (
    ZaloBatchRepository,
    canonical_target_key,
)
from services.browser_job_guard import exclusive_browser_job
from services.external_gateways import ExternalGateways
from services.lms_quality_service import (
    fetch_lms_slots_for_targets,
    inspect_and_attach_quality_issues_async,
    is_lms_fetch_unavailable,
    lms_target_lessons_by_class,
)
import inspect
from services.report_status import (
    CATEGORY_DONE,
    CATEGORY_EXEMPT,
    CATEGORY_LATE,
    CATEGORY_MISSING,
    CATEGORY_REVIEW,
    ISSUE_CODE_LMS_MANUAL_CHECK,
    ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
    ISSUE_CODE_LMS_SPCK_MISSING,
    ISSUE_CODE_LMS_SPCK_NOTE,
    SEVERITY_FAIL,
    SEVERITY_NOTE,
    categorize_row,
    classify_course_track,
    is_lesson_14,
    is_lms_ready_for_bcnb_tick,
    issue_code,
    issue_severity,
    issue_text,
    normalize_class_code,
    normalize_lesson_number,
    resolve_lms_status,
    resolve_spck_status,
)
from services.runtime import WorkerRuntime
from services.zalo_bcnb_service import (
    collect_result_item_evidence_paths,
    update_bcnb_reports,
)
from services.zalo_discovery_service import (
    DiscoverySafetyError,
    _build_discovery_plan,
    _collect_bcnb_class_states,
    _collect_bcnb_week_plan,
    _collect_zalo_group_names,
    _login_bcnb_if_needed,
)
from services.zalo_evidence import apply_lesson_14_exemption, get_evidence_reference
from services.zalo_parallel_service import (
    LANDING_SETUP_CODES,
    MAX_SETUP_ATTEMPTS,
    SETUP_OK,
    TARGET_TIMEOUT,
    ScanPageState,
    TargetSearchError,
    TargetTimeoutError,
    ZaloSetupError,
    _await_with_target_timeout,
    _close_scan_page,
    _create_production_backup,
    _emit_setup_telemetry,
    _empty_result,
    _invoke_landing_provider,
    _new_scan_page,
    _scan_one_target,
    _setup_failure_result,
    _setup_scan_page,
    _target_search_failure_result,
    _target_timeout_seconds,
    attach_zalo_sync_result,
)
from services.zalo_target_eligibility import (
    parse_group_time_range,
    resolve_session_time_window,
)


PHASE_UPCOMING = "UPCOMING"
PHASE_ONGOING = "ONGOING"
PHASE_ENDED = "ENDED"
PHASE_TIME_UNKNOWN = "TIME_UNKNOWN"
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SPCK_APPLICABLE_LESSONS: frozenset[int] = frozenset({12, 13, 14})
_SCAN_ONLY_DECISION_DEFERRED = "SESSION_NOT_ENDED_DEFERRED"


def parse_target_date_iso(
    raw_date: str | None = None,
    *,
    default_date: date | None = None,
) -> str:
    """Validate an optional ``YYYY-MM-DD`` date string or return business today."""
    if raw_date is None or not str(raw_date).strip():
        resolved = default_date or now_business().date()
        return resolved.isoformat()
    clean = str(raw_date).strip()
    if not _ISO_DATE_RE.fullmatch(clean):
        raise ValueError("Ngày tra cứu không hợp lệ (định dạng YYYY-MM-DD).")
    try:
        parsed = date.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError("Ngày tra cứu không hợp lệ (ngày không tồn tại).") from exc
    return parsed.isoformat()


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _week_pair(raw: Any) -> tuple[int, int] | None:
    if not isinstance(raw, Mapping):
        return None
    week = _safe_int(raw.get("week"))
    year = _safe_int(raw.get("year"))
    if week is not None and year is not None and 1 <= week <= 53 and 2000 < year <= 2100:
        return week, year
    return None


def _entry_key_tuple(
    entry: Mapping[str, Any],
    *,
    fallback_week: int | None = None,
    fallback_year: int | None = None,
) -> tuple[int, int, str, str, str] | None:
    st = entry.get("scan_target")
    st_map = st if isinstance(st, Mapping) else {}
    ev = entry.get("evidence")
    ctx_raw = ev.get("date_context") if isinstance(ev, Mapping) else None
    ctx = ctx_raw if isinstance(ctx_raw, Mapping) else {}

    week = _safe_int(
        st_map.get("week")
        or ctx.get("target_week")
        or entry.get("week")
        or fallback_week
    )
    year = _safe_int(
        st_map.get("year")
        or ctx.get("target_year")
        or entry.get("year")
        or fallback_year
    )
    class_code = normalize_class_code(
        st_map.get("class_code")
        or ctx.get("target_class_code")
        or entry.get("class_code")
        or entry.get("ma_lop")
        or ""
    )
    raw_lesson = str(
        st_map.get("lesson")
        or ctx.get("target_lesson")
        or entry.get("lesson")
        or entry.get("buoi_so")
        or ""
    ).strip()
    lesson_num = normalize_lesson_number(raw_lesson)
    lesson = str(lesson_num) if lesson_num is not None else raw_lesson
    date_iso = str(
        st_map.get("class_date_iso")
        or ctx.get("target_class_date")
        or ctx.get("bcnb_class_date")
        or entry.get("class_date_iso")
        or entry.get("ngay_hoc_iso")
        or ""
    ).strip()
    if week is None or year is None or not class_code or not _ISO_DATE_RE.fullmatch(date_iso):
        return None
    return (week, year, class_code, lesson, date_iso)


def _format_evidence_web_url(raw_path: str) -> str:
    clean = str(raw_path or "").strip()
    if not clean:
        return ""
    if clean.startswith("/api/evidence/"):
        return clean
    match = re.match(r"^/?static/screenshots/job-(\d+)/([^/?#]+)$", clean)
    if match:
        return f"/api/evidence/{match.group(1)}/{match.group(2)}"
    if clean.startswith("/"):
        return clean
    return f"/{clean}"


def _parse_json_safe(raw: Any, fallback: Any = None) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _extract_bcnb_row_date_iso(row: Mapping[str, Any]) -> str:
    direct = str(row.get("ngay_hoc_iso") or row.get("class_date_iso") or "").strip()
    if _ISO_DATE_RE.fullmatch(direct):
        return direct
    raw_date = str(row.get("ngay_hoc") or row.get("class_date") or "").strip()
    match = re.search(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)", raw_date)
    if match:
        try:
            d_val = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            return d_val.isoformat()
        except ValueError:
            return ""
    return ""


def _collect_today_entries_from_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    target_date_iso: str,
    teacher_by_class_code: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Gather all sessions on ``target_date_iso`` across both bounded BCNB weeks."""
    teacher_map = teacher_by_class_code or {}
    if not isinstance(snapshot, Mapping):
        return [], {
            "snapshot_id": None,
            "observed_at": None,
            "is_stale": True,
            "latest_week": None,
            "required_week": None,
        }

    plan = snapshot.get("plan")
    plan_map = plan if isinstance(plan, Mapping) else {}
    week_plan = plan_map.get("bcnb_week_plan")
    week_plan_map = week_plan if isinstance(week_plan, Mapping) else {}
    latest_week = week_plan_map.get("latest_week")
    required_week = week_plan_map.get("required_week")
    latest_pair = _week_pair(latest_week)
    required_pair = _week_pair(required_week)

    seen_keys: set[tuple[int, int, str, str, str]] = set()
    entries: list[dict[str, Any]] = []

    def _enrich_person(class_code: str, existing_person: str) -> str:
        clean = str(existing_person or "").strip()
        if clean and clean != "Chưa xác định":
            return clean
        return str(teacher_map.get(normalize_class_code(class_code)) or "Chưa xác định").strip()

    for bucket_name, raw_items in (
        ("scan_targets", plan_map.get("scan_targets") or plan_map.get("targets") or []),
        ("deferred_targets", plan_map.get("deferred_targets") or []),
        ("exempt_targets", plan_map.get("exempt_targets") or []),
    ):
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            kt = _entry_key_tuple(item)
            if kt is None or kt[4] != target_date_iso:
                continue
            if kt in seen_keys:
                continue
            seen_keys.add(kt)
            copy_item = dict(item)
            raw_item_lesson = str(
                copy_item.get("lesson") or copy_item.get("buoi_so") or ""
            ).strip()
            copy_item["class_code"] = normalize_class_code(copy_item.get("class_code"))
            copy_item["lesson"] = raw_item_lesson or kt[3]
            copy_item["class_date_iso"] = target_date_iso
            copy_item["week"] = kt[0]
            copy_item["year"] = kt[1]
            copy_item["is_current_week"] = (kt[0], kt[1]) == latest_pair
            copy_item["person"] = _enrich_person(copy_item["class_code"], str(copy_item.get("person") or ""))
            copy_item.setdefault("missing_zalo_group", False)
            copy_item.setdefault("snapshot_bucket", bucket_name)
            entries.append(copy_item)

    # Also surface BCNB schedule rows on target_date_iso missing a unique Zalo group match.
    dropped_reasons_by_key: dict[tuple[int, int, str, str, str], tuple[str, list[str]]] = {}
    dropped_rows = plan_map.get("dropped_rows")
    if isinstance(dropped_rows, Mapping):
        for group_key in ("missing_zalo", "ambiguous_zalo", "transition_review"):
            group_items = dropped_rows.get(group_key)
            if not isinstance(group_items, list):
                continue
            for dropped in group_items:
                if not isinstance(dropped, Mapping):
                    continue
                kt = _entry_key_tuple(dropped)
                if kt is not None and kt[4] == target_date_iso:
                    dropped_reasons_by_key[kt] = (
                        str(dropped.get("reason") or group_key.upper()),
                        list(dropped.get("candidate_group_names") or []),
                    )

    for pair, data_key in ((required_pair, "required_data"), (latest_pair, "latest_data")):
        raw_rows = week_plan_map.get(data_key)
        if pair is None or not isinstance(raw_rows, list):
            continue
        for row in raw_rows:
            if not isinstance(row, Mapping):
                continue
            row_date_iso = _extract_bcnb_row_date_iso(row)
            if row_date_iso != target_date_iso:
                continue
            class_code = normalize_class_code(row.get("ma_lop") or row.get("class_code") or "")
            raw_lesson = str(row.get("buoi_so") or row.get("lesson") or "").strip()
            lesson_num = normalize_lesson_number(raw_lesson)
            lesson = str(lesson_num) if lesson_num is not None else raw_lesson
            kt = (pair[0], pair[1], class_code, lesson, row_date_iso)
            if not class_code or kt in seen_keys:
                continue
            seen_keys.add(kt)
            reason, candidates = dropped_reasons_by_key.get(kt, ("MISSING_ZALO_GROUP", []))
            synth: dict[str, Any] = {
                "group_name": "",
                "person": _enrich_person(class_code, str(row.get("giao_vien") or "")),
                "class_code": class_code,
                "lesson": raw_lesson or lesson,
                "class_date": str(row.get("ngay_hoc") or row_date_iso).strip(),
                "class_date_iso": row_date_iso,
                "zalo_deadline": str(row.get("zalo_deadline") or "").strip(),
                "week": pair[0],
                "year": pair[1],
                "week_source": "BCNB_SERVER",
                "is_current_week": pair == latest_pair,
                "bcnb_session_id": str(row.get("bcnb_session_id") or "").strip(),
                "khoi": str(row.get("khoi") or "").strip(),
                "lms_deadline": str(row.get("lms_deadline") or "").strip(),
                "evaluation_status": str(row.get("evaluation_status") or "").strip(),
                "missing_zalo_group": True,
                "match_warning": reason,
                "candidate_group_names": candidates,
                "snapshot_bucket": "unmatched_bcnb",
            }
            for flag_key in ("nx_zalo_checked", "nx_lms_checked"):
                if isinstance(row.get(flag_key), bool):
                    synth[flag_key] = row[flag_key]
            for note_key in ("qc_issue_note", "instructor_note"):
                if isinstance(row.get(note_key), str):
                    synth[note_key] = row[note_key]
            entries.append(synth)

    meta = {
        "snapshot_id": snapshot.get("snapshot_id"),
        "observed_at": snapshot.get("observed_at"),
        "latest_week": latest_week,
        "required_week": required_week,
    }
    return entries, meta


def _job_exec_mode(raw_payload: Mapping[str, Any], mutates_external: Any) -> str:
    mode = str(raw_payload.get("execution_mode") or "").strip().lower()
    if mode in {"dry-run", "production"}:
        return mode
    return "production" if bool(mutates_external) else "dry-run"


_SAFE_BCNB_LIST_KEYS = (
    "failed_targets",
    "lms_failed_targets",
    "checker_note_failures",
    "target_outcomes",
    "lms_target_outcomes",
    "checker_note_outcomes",
)

_SAFE_BCNB_DEFAULT_ACTION = {
    "failed_targets": "BCNB_REPORT_TICK",
    "lms_failed_targets": "BCNB_LMS_TICK",
    "checker_note_failures": "BCNB_UPDATE_CHECKER_NOTE",
    "target_outcomes": "BCNB_REPORT_TICK",
    "lms_target_outcomes": "BCNB_LMS_TICK",
    "checker_note_outcomes": "BCNB_UPDATE_CHECKER_NOTE",
}


def _safe_bcnb_error_tuple(
    list_key: str,
    action_type: str,
    status_val: str,
    raw_code: str,
) -> tuple[str, str]:
    if status_val == "BLOCKED_BATCH_HALT" or "BLOCKED" in raw_code:
        return (
            "BCNB_BLOCKED_BATCH_HALT",
            "Batch đã halt trước thao tác BCNB.",
        )
    if status_val == "SKIPPED_TICK_FAILED":
        return (
            "BCNB_CHECKER_NOTE_SKIPPED",
            "Bỏ qua cập nhật Mô tả vấn đề vì bước tick trước đó thất bại.",
        )
    if status_val == "CONFLICT_AFTER_WRITE" or "CONFLICT" in raw_code:
        return (
            "BCNB_CONFLICT_AFTER_WRITE",
            "Phát hiện thay đổi ngoài luồng trên BCNB.",
        )
    if status_val == "OUTCOME_UNKNOWN" or "OUTCOME_UNKNOWN" in raw_code:
        if action_type == "BCNB_LMS_TICK" or list_key.startswith("lms_"):
            return (
                "BCNB_LMS_OUTCOME_UNKNOWN",
                "Không xác minh được trạng thái NX LMS sau khi ghi BCNB.",
            )
        if action_type == "BCNB_UPDATE_CHECKER_NOTE" or list_key.startswith("checker_note_"):
            return (
                "BCNB_CHECKER_NOTE_OUTCOME_UNKNOWN",
                "Không xác minh được trạng thái ô Mô tả vấn đề sau khi ghi BCNB.",
            )
        return (
            "BCNB_REPORT_OUTCOME_UNKNOWN",
            "Không xác minh được trạng thái NX Zalo sau khi ghi BCNB.",
        )
    if action_type == "BCNB_LMS_TICK" or list_key.startswith("lms_"):
        return (
            "BCNB_LMS_TICK_FAILED",
            "Cập nhật NX LMS trên BCNB thất bại.",
        )
    if action_type == "BCNB_UPDATE_CHECKER_NOTE" or list_key.startswith("checker_note_"):
        return (
            "BCNB_CHECKER_NOTE_FAILED",
            "Cập nhật ô Mô tả vấn đề trên BCNB thất bại.",
        )
    return (
        "BCNB_REPORT_TICK_FAILED",
        "Cập nhật NX Zalo trên BCNB thất bại.",
    )


def _sanitize_public_bcnb_summary(raw_summary: Any) -> dict[str, Any] | None:
    """Return a sanitized public BCNB summary with zero raw exception text."""
    if not isinstance(raw_summary, Mapping):
        return None
    sanitized: dict[str, Any] = {
        "updated_count": _safe_int(raw_summary.get("updated_count")),
        "lms_updated_count": _safe_int(raw_summary.get("lms_updated_count")),
        "checker_note_updated_count": _safe_int(raw_summary.get("checker_note_updated_count")),
        "checker_note_failure_count": _safe_int(raw_summary.get("checker_note_failure_count")),
        "checker_note_skipped_tick_failed_count": _safe_int(
            raw_summary.get("checker_note_skipped_tick_failed_count")
        ),
        "lms_fetch_unavailable": bool(raw_summary.get("lms_fetch_unavailable")),
    }
    if isinstance(raw_summary.get("selected_weeks"), list):
        sanitized["selected_weeks"] = [
            {"week": _safe_int(w.get("week")), "year": _safe_int(w.get("year"))}
            for w in raw_summary["selected_weeks"]
            if isinstance(w, Mapping)
        ]
    for list_key in _SAFE_BCNB_LIST_KEYS:
        raw_entries = raw_summary.get(list_key)
        if not isinstance(raw_entries, list):
            continue
        clean_entries: list[dict[str, Any]] = []
        default_action = _SAFE_BCNB_DEFAULT_ACTION[list_key]
        is_failure_list = list_key in {
            "failed_targets",
            "lms_failed_targets",
            "checker_note_failures",
        }
        for entry in raw_entries:
            if not isinstance(entry, Mapping):
                continue
            action_type = str(entry.get("action_type") or default_action).strip() or default_action
            status_val = str(entry.get("status") or "").strip().upper()
            raw_code = str(entry.get("error_code") or "").strip().upper()
            clean_item: dict[str, Any] = {}
            for safe_key in ("class_code", "lesson", "week", "year", "class_date_iso"):
                if safe_key in entry and entry.get(safe_key) is not None:
                    clean_item[safe_key] = entry.get(safe_key)
            if "action_type" in entry or action_type != default_action:
                clean_item["action_type"] = action_type
            if status_val:
                clean_item["status"] = status_val
            has_error = is_failure_list or bool(raw_code) or ("error" in entry and entry.get("error")) or status_val in {
                "FAILED",
                "FAILED_BEFORE_WRITE",
                "OUTCOME_UNKNOWN",
                "CONFLICT_AFTER_WRITE",
                "BLOCKED_BATCH_HALT",
                "SKIPPED_TICK_FAILED",
            }
            if has_error:
                safe_code, safe_msg = _safe_bcnb_error_tuple(
                    list_key,
                    action_type,
                    status_val,
                    raw_code,
                )
                clean_item["error_code"] = safe_code
                clean_item["error"] = safe_msg
            clean_entries.append(clean_item)
        sanitized[list_key] = clean_entries
    return sanitized


def _entry_session_id(item: Mapping[str, Any]) -> str:
    st = item.get("scan_target")
    st_map = st if isinstance(st, Mapping) else {}
    return str(
        item.get("bcnb_session_id") or st_map.get("bcnb_session_id") or ""
    ).strip()


def _overlay_matches_entry_identity(
    base_entry: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> bool:
    """Require non-empty matching bcnb_session_id; reject stale or sessionless overlays."""
    base_sid = _entry_session_id(base_entry)
    ov_sid = _entry_session_id(overlay)
    return bool(base_sid and ov_sid and base_sid == ov_sid)


async def _load_persisted_today_overlays(
    *,
    target_date_iso: str,
    current_execution_mode: str,
) -> tuple[
    dict[tuple[tuple[int, int, str, str, str], str], dict[str, Any]],
    dict[str, str],
    dict[str, Any] | None,
]:
    """Load newest persisted per-session overlays from SQLite (scan_jobs & zalo_batch_targets)."""
    overlay_by_key: dict[tuple[tuple[int, int, str, str, str], str], dict[str, Any]] = {}
    teacher_by_class_code: dict[str, str] = {}
    latest_sync_job: dict[str, Any] | None = None

    async with get_db_connection() as db:
        async with db.execute(
            """
            SELECT class_code, person_in_charge
            FROM groups
            WHERE class_code IS NOT NULL AND TRIM(class_code) != ''
              AND person_in_charge IS NOT NULL AND TRIM(person_in_charge) != ''
            LIMIT 2000
            """
        ) as cursor:
            for g_code, g_person in await cursor.fetchall():
                norm = normalize_class_code(g_code)
                if norm and norm not in teacher_by_class_code:
                    teacher_by_class_code[norm] = str(g_person).strip()

        async with db.execute(
            """
            SELECT id, job_type, status, created_at, started_at, finished_at,
                   results, request_payload, mutates_external
            FROM scan_jobs
            WHERE job_type IN ('ZALO_SCAN', 'TODAY_REALTIME_SYNC')
              AND status IN ('SUCCESS', 'PARTIAL_SUCCESS')
            ORDER BY id DESC
            LIMIT 40
            """
        ) as cursor:
            job_rows = list(await cursor.fetchall())

        matching_job_ids: list[int] = []
        job_meta_by_id: dict[int, dict[str, Any]] = {}
        for row in job_rows:
            j_id = int(row[0])
            j_type = str(row[1] or "")
            j_status = str(row[2] or "")
            j_obs_at = str(row[5] or row[4] or row[3] or "").strip() or None
            envelope = _parse_json_safe(row[6], None)
            req_payload = _parse_json_safe(row[7], {})
            req_map = req_payload if isinstance(req_payload, Mapping) else {}
            is_check_only = bool(
                (j_type == "TODAY_REALTIME_SYNC")
                and (
                    req_map.get("check_only")
                    or (isinstance(envelope, Mapping) and envelope.get("check_only"))
                )
            )
            j_mode = _job_exec_mode(req_map, row[8])
            if not is_check_only and j_mode != current_execution_mode:
                continue
            if (
                j_type == "TODAY_REALTIME_SYNC"
                and latest_sync_job is None
                and isinstance(envelope, Mapping)
                and str(envelope.get("target_date_iso") or "") == target_date_iso
            ):
                latest_sync_job = {
                    "job_id": j_id,
                    "status": j_status,
                    "check_only": is_check_only,
                    "observed_at": j_obs_at,
                    "execution_mode": j_mode,
                    "bcnb_summary": _sanitize_public_bcnb_summary(
                        envelope.get("bcnb_summary")
                    ),
                }
            matching_job_ids.append(j_id)
            job_meta_by_id[j_id] = {
                "job_id": j_id,
                "job_type": j_type,
                "observed_at": j_obs_at,
                "check_only": is_check_only,
                "execution_mode": j_mode,
            }

            raw_items: list[dict[str, Any]] = []
            if isinstance(envelope, list):
                raw_items = [it for it in envelope if isinstance(it, dict)]
            elif isinstance(envelope, Mapping):
                if isinstance(envelope.get("today_items"), list):
                    raw_items = [it for it in envelope["today_items"] if isinstance(it, dict)]
                elif isinstance(envelope.get("items"), list):
                    raw_items = [it for it in envelope["items"] if isinstance(it, dict)]

            for item in raw_items:
                kt = _entry_key_tuple(item)
                if kt is None or kt[4] != target_date_iso:
                    continue
                ov_sid = _entry_session_id(item)
                if not ov_sid:
                    continue
                sub_key = (kt, ov_sid)
                if sub_key not in overlay_by_key:
                    enriched = dict(item)
                    enriched["_overlay_job_id"] = j_id
                    enriched["_overlay_job_type"] = j_type
                    enriched["_overlay_observed_at"] = j_obs_at
                    enriched["_overlay_check_only"] = is_check_only
                    overlay_by_key[sub_key] = enriched

        if matching_job_ids:
            placeholders = ",".join("?" for _ in matching_job_ids)
            async with db.execute(
                f"""
                SELECT job_id, class_code, week, year, lesson, class_date_iso,
                       scan_status, write_status, evidence_path, result_json, updated_at
                FROM zalo_batch_targets
                WHERE job_id IN ({placeholders})
                  AND class_date_iso = ?
                ORDER BY job_id DESC, id DESC
                LIMIT 1000
                """,
                (*matching_job_ids, target_date_iso),
            ) as cursor:
                for bt_row in await cursor.fetchall():
                    bt_job_id = int(bt_row[0])
                    bt_code = normalize_class_code(bt_row[1])
                    bt_week = _safe_int(bt_row[2])
                    bt_year = _safe_int(bt_row[3])
                    bt_lesson_raw = str(bt_row[4] or "").strip()
                    bt_lesson_num = normalize_lesson_number(bt_lesson_raw)
                    bt_lesson = str(bt_lesson_num) if bt_lesson_num is not None else bt_lesson_raw
                    bt_date = str(bt_row[5] or "").strip()
                    if not bt_code or bt_week is None or bt_year is None or bt_date != target_date_iso:
                        continue
                    kt = (bt_week, bt_year, bt_code, bt_lesson, bt_date)
                    parsed_bt_res = _parse_json_safe(bt_row[9], {})
                    bt_sid = (
                        _entry_session_id(parsed_bt_res)
                        if isinstance(parsed_bt_res, dict)
                        else ""
                    )
                    if not bt_sid:
                        continue
                    sub_key = (kt, bt_sid)
                    existing = overlay_by_key.get(sub_key)
                    if existing is None or int(existing.get("_overlay_job_id") or 0) <= bt_job_id:
                        if isinstance(parsed_bt_res, dict) and existing is None:
                            j_meta = job_meta_by_id.get(bt_job_id, {})
                            item_bt = dict(parsed_bt_res)
                            item_bt["scan_status"] = bt_row[6]
                            item_bt["write_status"] = bt_row[7]
                            if bt_row[8] and not item_bt.get("screenshot_path"):
                                item_bt["screenshot_path"] = bt_row[8]
                            item_bt["_overlay_job_id"] = bt_job_id
                            item_bt["_overlay_job_type"] = j_meta.get("job_type", "ZALO_SCAN")
                            item_bt["_overlay_observed_at"] = j_meta.get("observed_at") or bt_row[10]
                            item_bt["_overlay_check_only"] = bool(j_meta.get("check_only", False))
                            overlay_by_key[sub_key] = item_bt
                        elif existing is not None:
                            existing.setdefault("scan_status", bt_row[6])
                            existing.setdefault("write_status", bt_row[7])
                            if bt_row[8] and not existing.get("screenshot_path"):
                                existing["screenshot_path"] = bt_row[8]

    return overlay_by_key, teacher_by_class_code, latest_sync_job


def _merge_entry_with_overlay(
    base_entry: Mapping[str, Any],
    overlay: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(base_entry)
    if not overlay or not _overlay_matches_entry_identity(base_entry, overlay):
        return merged
    merged_st = dict(base_entry)
    overlay_st = overlay.get("scan_target")
    if isinstance(overlay_st, Mapping):
        for k, v in overlay_st.items():
            if v is not None and v != "":
                merged_st[k] = v
    for k, v in overlay.items():
        if k == "scan_target":
            continue
        if v is not None:
            merged[k] = v
    # Preserve BCNB base row identity and group flags
    for keep_key in (
        "week",
        "year",
        "class_code",
        "lesson",
        "class_date",
        "class_date_iso",
        "bcnb_session_id",
        "zalo_deadline",
        "lms_deadline",
        "is_current_week",
        "missing_zalo_group",
        "match_warning",
    ):
        if base_entry.get(keep_key) not in (None, ""):
            merged[keep_key] = base_entry[keep_key]
    merged["scan_target"] = merged_st
    return merged


def _phase_label(phase: str) -> str:
    return {
        PHASE_UPCOMING: "⏳ Sắp diễn ra",
        PHASE_ONGOING: "🟢 Đang diễn ra",
        PHASE_ENDED: "🏁 Đã kết thúc",
        PHASE_TIME_UNKNOWN: "❓ Chưa rõ giờ",
    }.get(phase, "❓ Chưa rõ giờ")


def _normalize_row_issues(row: Mapping[str, Any]) -> list[dict[str, str]]:
    seen_texts: set[str] = set()
    normalized: list[dict[str, str]] = []
    st = row.get("scan_target")
    st_map = st if isinstance(st, Mapping) else {}
    for src in (row, st_map):
        for key in ("quality_issues", "lms_quality_issues", "zalo_content_issues"):
            raw_list = src.get(key)
            if not isinstance(raw_list, list):
                continue
            for item in raw_list:
                txt = issue_text(item)
                if not txt or txt in seen_texts:
                    continue
                seen_texts.add(txt)
                normalized.append(
                    {
                        "code": issue_code(item) or "UNCLASSIFIED_ISSUE",
                        "text": txt,
                        "severity": issue_severity(item) or SEVERITY_NOTE,
                    }
                )
    return normalized


def _resolve_row_time_window(
    row: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    date_iso = str(row.get("class_date_iso") or row.get("ngay_hoc_iso") or "").strip()
    target_day: date | None = None
    if _ISO_DATE_RE.fullmatch(date_iso):
        with contextlib.suppress(ValueError):
            target_day = date.fromisoformat(date_iso)
    st = row.get("scan_target")
    st_map = st if isinstance(st, Mapping) else {}
    group_name = row.get("group_name") or row.get("group") or st_map.get("group_name") or ""
    window = resolve_session_time_window(
        target_day,
        lms_start_time=row.get("lms_start_time") or st_map.get("lms_start_time"),
        lms_end_time=row.get("lms_end_time") or st_map.get("lms_end_time"),
        group_name=group_name,
        now=now,
    )
    group_range = parse_group_time_range(group_name)
    window["group_start_hm"] = group_range[0].strftime("%H:%M") if group_range else None
    window["group_end_hm"] = group_range[1].strftime("%H:%M") if group_range else None
    return window


def _normalize_today_item(
    merged_row: dict[str, Any],
    *,
    now_dt: datetime,
) -> dict[str, Any]:
    """Normalize one session row for the Lớp Hôm Nay timeline & KPIs."""
    class_code = normalize_class_code(merged_row.get("class_code") or merged_row.get("ma_lop") or "")
    lesson = str(merged_row.get("lesson") or merged_row.get("buoi_so") or "").strip()
    khoi = str(merged_row.get("khoi") or "").strip()
    week = int(merged_row.get("week") or 0)
    year = int(merged_row.get("year") or 0)
    date_iso = str(merged_row.get("class_date_iso") or "").strip()
    target_key = canonical_target_key(
        {
            "week": week,
            "year": year,
            "class_code": class_code,
            "lesson": lesson,
            "class_date_iso": date_iso,
        }
    )

    time_win = _resolve_row_time_window(merged_row, now=now_dt)
    phase = str(time_win.get("session_phase") or PHASE_TIME_UNKNOWN)
    is_ended = phase == PHASE_ENDED

    # If session has now ended, do not let a stale pre-end deferral linger as active status
    ev = merged_row.get("evidence")
    ev_map = ev if isinstance(ev, Mapping) else {}
    raw_classifier = ev_map.get("classifier_result")
    classifier = raw_classifier if isinstance(raw_classifier, Mapping) else {}
    decision = str(classifier.get("decision") or "").strip().upper()
    row_for_display = dict(merged_row)
    if is_ended and decision == _SCAN_ONLY_DECISION_DEFERRED:
        row_for_display["session_deferred"] = False
        row_for_display["evidence_state"] = "NOT_SCANNED"
        row_for_display.pop("status", None)
    elif not is_ended:
        row_for_display["session_deferred"] = True

    missing_zalo_group = bool(merged_row.get("missing_zalo_group"))
    has_scan_result = bool(
        row_for_display.get("evidence_state") not in (None, "", "DEFERRED", "NOT_SCANNED")
        or is_lesson_14(lesson)
    )
    if not is_ended:
        zalo_category = "deferred"
        zalo_status_label = "⏳ Chưa tới giờ kiểm tra"
    elif missing_zalo_group and not is_lesson_14(lesson):
        zalo_category = "missing_group"
        zalo_status_label = "⚠️ Thiếu nhóm Zalo"
    elif is_lesson_14(lesson) and not has_scan_result:
        zalo_category = CATEGORY_EXEMPT
        zalo_status_label = "⚪ Miễn B14"
    elif not has_scan_result:
        zalo_category = "not_scanned"
        zalo_status_label = "⏳ Chưa quét Zalo"
    else:
        zalo_category = categorize_row(row_for_display)
        zalo_status_label = str(row_for_display.get("status") or "").strip() or {
            CATEGORY_DONE: "✅ Đã nộp",
            CATEGORY_LATE: "🟡 Đã nộp trễ",
            CATEGORY_EXEMPT: "⚪ Miễn B14",
            CATEGORY_MISSING: "❌ Chưa nhận xét",
            CATEGORY_REVIEW: "⚠️ Cần kiểm tra",
        }.get(zalo_category, "⚠️ Cần kiểm tra")

    raw_paths = sorted(collect_result_item_evidence_paths(row_for_display))
    evidence_urls = [_format_evidence_web_url(p) for p in raw_paths if _format_evidence_web_url(p)]
    screenshot_url = evidence_urls[0] if evidence_urls else ""

    track_info = classify_course_track(class_code, khoi=khoi, lesson=lesson)
    issues = _normalize_row_issues(row_for_display)
    lms_att = row_for_display.get("lms_attendance")
    lms_com = row_for_display.get("lms_comments")
    lms_spck = row_for_display.get("lms_spck")
    lms_att_map = dict(lms_att) if isinstance(lms_att, Mapping) else {}
    lms_com_map = dict(lms_com) if isinstance(lms_com, Mapping) else {}
    lms_spck_map = dict(lms_spck) if isinstance(lms_spck, Mapping) else {}

    lms_evaluated = bool(lms_com_map or lms_att_map)
    lms_fetch_unavailable = bool(row_for_display.get("lms_fetch_unavailable")) or any(
        it["code"] == ISSUE_CODE_LMS_MANUAL_CHECK for it in issues
    )
    lms_status_text = resolve_lms_status(
        row_for_display,
        fallback=(
            "⏳ Đang chờ điểm danh / nhận xét LMS"
            if not is_ended
            else ("⚠️ Cần kiểm tra thủ công LMS" if lms_fetch_unavailable else "⏳ Chưa kiểm tra LMS")
        ),
    )
    spck_status_text = resolve_spck_status(row_for_display)
    if not spck_status_text and track_info["is_spck_session"] and track_info["spck_exempt_course"]:
        spck_status_text = "⚪ Miễn SPCK (PTB/CSB)"
    spck_detail_notes = [
        it["text"]
        for it in issues
        if (
            it["code"]
            in {
                ISSUE_CODE_LMS_SPCK_MISSING,
                ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
                ISSUE_CODE_LMS_SPCK_NOTE,
            }
            or "SPCK" in it["text"].upper()
        )
    ]

    lms_ready, lms_ready_reason = is_lms_ready_for_bcnb_tick(row_for_display)
    if not is_ended or not lms_evaluated or lms_fetch_unavailable:
        lms_ready = False

    start_hm = time_win.get("start_time_hm")
    end_hm = time_win.get("end_time_hm")
    if start_hm and end_hm:
        time_range_display = f"{start_hm} – {end_hm}"
    elif end_hm:
        time_range_display = f"Kết thúc {end_hm}"
    else:
        time_range_display = "Chưa rõ giờ"

    raw_nx_zalo = row_for_display.get("nx_zalo_checked")
    raw_nx_lms = row_for_display.get("nx_lms_checked")
    nx_zalo_checked = raw_nx_zalo if isinstance(raw_nx_zalo, bool) else None
    nx_lms_checked = raw_nx_lms if isinstance(raw_nx_lms, bool) else None

    fail_count = sum(1 for it in issues if it["severity"] == SEVERITY_FAIL)
    note_count = sum(1 for it in issues if it["severity"] != SEVERITY_FAIL)

    return {
        "target_key": target_key,
        "week": week,
        "year": year,
        "week_label": f"W{week:02d}/{year}",
        "is_current_week": bool(merged_row.get("is_current_week")),
        "class_code": class_code,
        "lesson": lesson,
        "lesson_num": track_info["lesson_num"],
        "class_date": str(merged_row.get("class_date") or date_iso).strip(),
        "class_date_iso": date_iso,
        "group_name": str(merged_row.get("group_name") or "").strip(),
        "person": str(merged_row.get("person") or "Chưa xác định").strip(),
        "khoi": khoi,
        "bcnb_session_id": str(merged_row.get("bcnb_session_id") or "").strip(),
        "zalo_deadline": str(merged_row.get("zalo_deadline") or "").strip(),
        "lms_deadline": str(merged_row.get("lms_deadline") or "").strip(),
        "missing_zalo_group": missing_zalo_group,
        "match_warning": str(merged_row.get("match_warning") or "").strip(),
        "phase": phase,
        "phase_label": _phase_label(phase),
        "eligible_for_sync": bool(is_ended and not missing_zalo_group),
        "start_hm": start_hm,
        "end_hm": end_hm,
        "time_range_display": time_range_display,
        "group_start_hm": time_win.get("group_start_hm"),
        "group_end_hm": time_win.get("group_end_hm"),
        "lms_start_time_hm": merged_row.get("lms_start_time_hm")
        or (time_win.get("start_time_hm") if time_win.get("time_source") in {"LMS_SLOT", "COMBINED"} else None),
        "lms_end_time_hm": merged_row.get("lms_end_time_hm")
        or (time_win.get("end_time_hm") if time_win.get("time_source") in {"LMS_SLOT", "COMBINED"} else None),
        "time_source": time_win.get("time_source"),
        "course_track": track_info["course_track"],
        "is_coding": bool(track_info["is_coding"]),
        "is_robotics": bool(track_info["is_robotics"]),
        "spck_exempt_course": bool(track_info["spck_exempt_course"]),
        "is_spck_session": bool(track_info["is_spck_session"]),
        "requires_spck": bool(track_info["requires_spck"]),
        "zalo_category": zalo_category,
        "zalo_status_label": zalo_status_label,
        "zalo_status_detail": str(row_for_display.get("status_detail") or "").strip(),
        "evidence_state": str(row_for_display.get("evidence_state") or "").strip(),
        "nx_zalo_checked": nx_zalo_checked,
        "screenshot_url": screenshot_url,
        "evidence_urls": evidence_urls,
        "lms_checked": lms_evaluated,
        "lms_fetch_unavailable": lms_fetch_unavailable,
        "lms_status_text": lms_status_text,
        "lms_attendance": lms_att_map,
        "lms_comments": lms_com_map,
        "lms_spck": {
            **lms_spck_map,
            "is_spck_session": bool(track_info["is_spck_session"]),
            "requires_spck": bool(track_info["requires_spck"]),
            "spck_exempt_course": bool(track_info["spck_exempt_course"]),
            "summary_text": spck_status_text,
            "detail_notes": spck_detail_notes,
        },
        "nx_lms_checked": nx_lms_checked,
        "lms_ready_for_tick": lms_ready,
        "lms_ready_reason": lms_ready_reason,
        "qc_issue_note": str(row_for_display.get("qc_issue_note") or "").strip(),
        "instructor_note": str(row_for_display.get("instructor_note") or "").strip(),
        "issues": issues,
        "fail_count": fail_count,
        "note_count": note_count,
        "source_job_id": merged_row.get("_overlay_job_id"),
        "source_job_type": merged_row.get("_overlay_job_type"),
        "source_observed_at": merged_row.get("_overlay_observed_at"),
        "check_only_observation": bool(merged_row.get("_overlay_check_only", False)),
    }


def _compute_today_kpis(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = {
        "count": len(items),
        "upcoming": sum(1 for it in items if it.get("phase") == PHASE_UPCOMING),
        "ongoing": sum(1 for it in items if it.get("phase") == PHASE_ONGOING),
        "ended": sum(1 for it in items if it.get("phase") == PHASE_ENDED),
        "time_unknown": sum(1 for it in items if it.get("phase") == PHASE_TIME_UNKNOWN),
        "missing_zalo_group": sum(1 for it in items if it.get("missing_zalo_group")),
    }
    zalo = {
        "submitted_or_exempt": sum(
            1 for it in items if it.get("zalo_category") in {CATEGORY_DONE, CATEGORY_LATE, CATEGORY_EXEMPT}
        ),
        "done": sum(1 for it in items if it.get("zalo_category") == CATEGORY_DONE),
        "late": sum(1 for it in items if it.get("zalo_category") == CATEGORY_LATE),
        "exempt": sum(1 for it in items if it.get("zalo_category") == CATEGORY_EXEMPT),
        "failed": sum(
            1 for it in items if it.get("zalo_category") in {CATEGORY_MISSING, CATEGORY_REVIEW}
        ),
        "missing": sum(1 for it in items if it.get("zalo_category") == CATEGORY_MISSING),
        "review": sum(1 for it in items if it.get("zalo_category") == CATEGORY_REVIEW),
        "deferred": sum(1 for it in items if it.get("zalo_category") == "deferred"),
        "not_scanned": sum(1 for it in items if it.get("zalo_category") == "not_scanned"),
        "nx_zalo_checked": sum(1 for it in items if it.get("nx_zalo_checked") is True),
    }
    lms = {
        "evaluated": sum(1 for it in items if it.get("lms_checked")),
        "ready_for_tick": sum(1 for it in items if it.get("lms_ready_for_tick")),
        "nx_lms_checked": sum(1 for it in items if it.get("nx_lms_checked") is True),
        "comment_issue_count": sum(
            1
            for it in items
            if str((it.get("lms_comments") or {}).get("status") or "").upper() in {"MISSING", "REVIEW"}
        ),
        "attendance_unverified": sum(
            1
            for it in items
            if str((it.get("lms_attendance") or {}).get("status") or "").upper() == "UNVERIFIED"
        ),
    }

    spck_rows = [it for it in items if it.get("is_spck_session")]
    coding_spck = [it for it in spck_rows if it.get("is_coding") and it.get("requires_spck")]
    robotics_spck = [it for it in spck_rows if it.get("is_robotics") and it.get("requires_spck")]

    def _is_spck_complete(row: Mapping[str, Any]) -> bool:
        spck = row.get("lms_spck")
        if not isinstance(spck, Mapping):
            return False
        st = str(spck.get("status") or "").upper()
        missing_comments = _safe_int(spck.get("missing_comment_count"))
        return st in {"COMPLETE", "EXEMPT"} and (missing_comments == 0 or st == "EXEMPT")

    spck_b12_14 = {
        "total": len(spck_rows),
        "coding_total": len(coding_spck),
        "coding_complete": sum(1 for r in coding_spck if _is_spck_complete(r)),
        "coding_incomplete": sum(1 for r in coding_spck if not _is_spck_complete(r)),
        "robotics_total": len(robotics_spck),
        "robotics_complete": sum(1 for r in robotics_spck if _is_spck_complete(r)),
        "robotics_incomplete": sum(1 for r in robotics_spck if not _is_spck_complete(r)),
        "exempt_course_total": sum(1 for r in spck_rows if r.get("spck_exempt_course")),
    }

    need_nx_zalo = sum(
        1
        for it in items
        if it.get("eligible_for_sync")
        and not it.get("missing_zalo_group")
        and it.get("zalo_category") in {CATEGORY_DONE, CATEGORY_LATE, CATEGORY_EXEMPT}
        and it.get("nx_zalo_checked") is not True
    )
    need_nx_lms = sum(
        1
        for it in items
        if it.get("eligible_for_sync")
        and it.get("lms_ready_for_tick")
        and it.get("nx_lms_checked") is not True
    )
    total_pending_rows = sum(
        1
        for it in items
        if it.get("eligible_for_sync")
        and (
            (
                not it.get("missing_zalo_group")
                and it.get("zalo_category") in {CATEGORY_DONE, CATEGORY_LATE, CATEGORY_EXEMPT}
                and it.get("nx_zalo_checked") is not True
            )
            or (it.get("lms_ready_for_tick") and it.get("nx_lms_checked") is not True)
        )
    )
    bcnb_pending = {
        "need_nx_zalo": need_nx_zalo,
        "need_nx_lms": need_nx_lms,
        "total_pending_rows": total_pending_rows,
    }
    return {
        "total": total,
        "zalo": zalo,
        "lms": lms,
        "spck_b12_14": spck_b12_14,
        "bcnb_pending": bcnb_pending,
    }


def _timeline_sort_key(item: Mapping[str, Any]) -> tuple[str, str, str, int]:
    start_hm = str(item.get("start_hm") or "99:99")
    end_hm = str(item.get("end_hm") or "99:99")
    code = str(item.get("class_code") or "")
    lesson_num = _safe_int(item.get("lesson_num"))
    return (start_hm, end_hm, code, lesson_num if lesson_num is not None else 9999)


async def build_today_overview(
    container: AppContainer,
    *,
    target_date_iso: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the real-time timeline overview for ``target_date_iso`` across both BCNB weeks."""
    current_now = now or now_business()
    resolved_date_iso = parse_target_date_iso(
        target_date_iso,
        default_date=current_now.date(),
    )
    today_iso = current_now.date().isoformat()
    execution_mode = "dry-run" if container.settings.bcnb_dry_run else "production"
    max_age_minutes = int(getattr(container.settings, "zalo_plan_max_age_minutes", 30))

    snapshot = await container.group_repo.get_latest_discovery_snapshot()
    overlays, teacher_by_code, latest_sync_job = await _load_persisted_today_overlays(
        target_date_iso=resolved_date_iso,
        current_execution_mode=execution_mode,
    )
    entries, snapshot_meta = _collect_today_entries_from_snapshot(
        snapshot,
        target_date_iso=resolved_date_iso,
        teacher_by_class_code=teacher_by_code,
    )

    observed_dt = parse_timestamp(snapshot_meta.get("observed_at"))
    snapshot_meta["is_stale"] = (
        True
        if observed_dt is None
        else (now_utc() - observed_dt).total_seconds() > max(1, max_age_minutes) * 60
    )

    normalized_items: list[dict[str, Any]] = []
    for entry in entries:
        kt = _entry_key_tuple(entry)
        base_sid = _entry_session_id(entry)
        overlay = overlays.get((kt, base_sid)) if (kt is not None and base_sid) else None
        merged = _merge_entry_with_overlay(entry, overlay)
        normalized_items.append(_normalize_today_item(merged, now_dt=current_now))

    normalized_items.sort(key=_timeline_sort_key)
    kpis = _compute_today_kpis(normalized_items)

    return {
        "target_date_iso": resolved_date_iso,
        "today_iso": today_iso,
        "is_today": resolved_date_iso == today_iso,
        "generated_at": utc_iso(now_utc()),
        "execution_mode": execution_mode,
        "snapshot": snapshot_meta,
        "latest_sync_job": latest_sync_job,
        "kpis": kpis,
        "items": normalized_items,
    }


def _snapshot_needs_refresh(
    snapshot: Mapping[str, Any] | None,
    *,
    max_age_minutes: int,
) -> bool:
    if not isinstance(snapshot, Mapping):
        return True
    plan = snapshot.get("plan")
    if not isinstance(plan, Mapping):
        return True
    week_plan = plan.get("bcnb_week_plan")
    if not isinstance(week_plan, Mapping):
        return True
    if not _week_pair(week_plan.get("latest_week")) or not _week_pair(week_plan.get("required_week")):
        return True
    observed = parse_timestamp(snapshot.get("observed_at"))
    if observed is None:
        return True
    return (now_utc() - observed).total_seconds() > max(1, max_age_minutes) * 60


async def _refresh_discovery_snapshot_in_session(
    session,
    *,
    job_id: int,
    job_repo: JobRepository,
    group_repo: GroupRepository,
    now_dt: datetime,
) -> dict[str, Any] | None:
    page = await _new_scan_page(session)
    try:
        bcnb_week_plan = await _collect_bcnb_week_plan(page, job_id)
        bcnb_data = bcnb_week_plan["latest_data"]
        try:
            bcnb_class_states = await _collect_bcnb_class_states(page, job_id)
        except DiscoverySafetyError:
            bcnb_class_states = None
        group_names = await _collect_zalo_group_names(page, job_id)
    finally:
        await _close_scan_page(session, page)

    ignored_groups = await group_repo.get_all_ignored_groups(include_archived=True)
    existing_groups = await group_repo.get_scan_groups()
    plan = _build_discovery_plan(
        group_names,
        bcnb_data,
        ignored_groups,
        existing_groups,
        bcnb_week_plan=bcnb_week_plan,
        bcnb_class_states=bcnb_class_states,
        now=now_dt,
    )
    if not plan.get("reliable"):
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message="Dừng an toàn: kết quả dò tìm BCNB/Zalo rỗng hoặc không khớp.",
            progress=100,
        )
        return None
    promotion = await group_repo.apply_discovery_snapshot(plan)
    if isinstance(promotion, dict) and promotion.get("status") == "REJECTED":
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message="Dừng an toàn: snapshot mới bị từ chối vì không đủ tin cậy.",
            progress=100,
        )
        return None
    return await group_repo.get_latest_discovery_snapshot()


async def _prefetch_lms_for_today(
    session,
    *,
    today_candidates: list[dict[str, Any]],
    external: ExternalGateways,
    latest_week: Any,
    now_dt: datetime,
) -> dict[str, list[dict[str, Any]]] | None:
    """Fetch live LMS + SPCK B12-14 across all today classes before time partitioning."""
    if not today_candidates:
        return {}
    pre_gw = getattr(external, "lms_quality", None)
    ai_reviewer = getattr(external, "lms_ai_reviewer", None) or getattr(
        external, "lms_comment_reviewer", None
    )
    resolved_slots: dict[str, list[dict[str, Any]]] | None = None
    if pre_gw is not None and hasattr(pre_gw, "fetch_class_slots"):
        pre_codes = sorted(
            {
                normalize_class_code(
                    (c.get("scan_target") or {}).get("class_code")
                    if isinstance(c.get("scan_target"), dict)
                    else c.get("class_code") or ""
                )
                for c in today_candidates
            }
            - {""}
        )
        requested_lessons = lms_target_lessons_by_class(today_candidates)
        if pre_codes:
            if type(pre_gw).__name__ != "PlaywrightLmsQualityGateway":
                try:
                    fetched = await fetch_lms_slots_for_targets(
                        pre_gw, None, pre_codes, requested_lessons
                    )
                    resolved_slots = (
                        fetched
                        if isinstance(fetched, dict)
                        else {c: [{"fetch_error": True}] for c in pre_codes}
                    )
                except Exception:
                    resolved_slots = {c: [{"fetch_error": True}] for c in pre_codes}
            elif (
                os.getenv("ZALO_QUALITY_ISOLATED") != "1"
                and urlsplit(str(getattr(Config, "LMS_URL", "") or "").strip()).scheme.lower()
                == "https"
                and (
                    urlsplit(str(getattr(Config, "LMS_URL", "") or "").strip()).hostname or ""
                ).lower()
                == "lms.mindx.edu.vn"
            ):
                lms_page = None
                try:
                    lms_page = await _new_scan_page(session)
                    fetched = await fetch_lms_slots_for_targets(
                        pre_gw, lms_page, pre_codes, requested_lessons
                    )
                    if isinstance(fetched, dict) and fetched:
                        resolved_slots = fetched
                except Exception:
                    resolved_slots = {c: [{"fetch_error": True}] for c in pre_codes}
                finally:
                    await _close_scan_page(session, lms_page)

    await inspect_and_attach_quality_issues_async(
        today_candidates,
        lms_slots_by_class=resolved_slots,
        now=now_dt,
        ai_reviewer=ai_reviewer,
        latest_week=latest_week,
        reset_ai_batch_counter=True,
    )
    if is_lms_fetch_unavailable(resolved_slots):
        for candidate in today_candidates:
            candidate["lms_fetch_unavailable"] = True
            if isinstance(candidate.get("scan_target"), dict):
                candidate["scan_target"]["lms_fetch_unavailable"] = True
    return resolved_slots


def _copy_lms_fields(dest: dict[str, Any], src: Mapping[str, Any]) -> None:
    for key in (
        "lms_start_time",
        "lms_end_time",
        "lms_start_time_hm",
        "lms_end_time_hm",
        "lms_attendance",
        "lms_comments",
        "lms_spck",
        "lms_lesson_summary",
        "lms_is_checkpoint",
        "lms_quality_issues",
        "lms_fetch_unavailable",
    ):
        if key in src and src.get(key) is not None:
            dest[key] = src[key]
            if isinstance(dest.get("scan_target"), dict):
                dest["scan_target"][key] = src[key]


def _filter_selected_targets(
    entries: list[dict[str, Any]],
    selected_keys: list[str] | None,
) -> list[dict[str, Any]]:
    if not selected_keys:
        return entries
    allowed = {
        k
        for raw_k in selected_keys
        for k in (
            (
                str(raw_k).strip(),
                _normalized_today_target_key_str(str(raw_k)),
            )
        )
        if k
    }
    if not allowed:
        return entries
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        try:
            key = canonical_target_key(entry)
        except ValueError:
            continue
        if key in allowed or _normalized_today_target_key_str(key) in allowed:
            filtered.append(entry)
    return filtered


def _normalized_today_target_key_str(raw_key: str) -> str:
    clean = str(raw_key or "").strip()
    parts = clean.split("|")
    if len(parts) != 4:
        return clean
    period, code, lesson, date_iso = parts
    lesson_num = normalize_lesson_number(lesson)
    norm_lesson = str(lesson_num) if lesson_num is not None else lesson.strip()
    return "|".join(
        [
            period.strip(),
            normalize_class_code(code),
            norm_lesson,
            date_iso.strip(),
        ]
    )


def _today_lookup_key(item: Mapping[str, Any]) -> str | None:
    kt = _entry_key_tuple(item)
    if kt is None:
        return None
    week, year, class_code, lesson, date_iso = kt
    return f"{year:04d}-W{week:02d}|{class_code}|{lesson}|{date_iso}"


def _make_today_placeholder_result(
    target: Mapping[str, Any],
    *,
    today: datetime,
    deferred: bool,
) -> dict[str, Any]:
    base = dict(target)
    is_missing_group = bool(target.get("missing_zalo_group"))
    group_name = "" if is_missing_group else str(target.get("group_name") or "").strip()
    date_ctx = {
        "target_week": target.get("week"),
        "target_year": target.get("year"),
        "target_week_source": "BCNB_SERVER",
        "target_class_code": target.get("class_code"),
        "target_lesson": target.get("lesson"),
        "target_class_date": target.get("class_date_iso") or target.get("class_date"),
    }
    if deferred:
        out: dict[str, Any] = {
            "group": group_name,
            "person": str(target.get("person") or "Chưa xác định").strip(),
            "class_code": str(target.get("class_code") or "").strip(),
            "lesson": str(target.get("lesson") or "").strip(),
            "ngay_hoc": str(target.get("class_date") or "").strip(),
            "ngay_hoc_iso": str(target.get("class_date_iso") or "").strip(),
            "week": target.get("week"),
            "year": target.get("year"),
            "status": "⏳ Chưa tới giờ kiểm tra",
            "status_detail": "Buổi học hôm nay chưa kết thúc hoặc chưa rõ giờ kết thúc.",
            "submitted_for_bcnb": False,
            "evidence_state": "DEFERRED",
            "session_deferred": True,
            "missing_zalo_group": is_missing_group,
            "scan_target": base,
            "evidence": {
                "validation_errors": [],
                "date_context": date_ctx,
                "classifier_result": {"decision": _SCAN_ONLY_DECISION_DEFERRED},
            },
        }
        if target.get("match_warning"):
            out["match_warning"] = target.get("match_warning")
        return out
    if is_missing_group:
        return {
            "group": "",
            "person": str(target.get("person") or "Chưa xác định").strip(),
            "class_code": str(target.get("class_code") or "").strip(),
            "lesson": str(target.get("lesson") or "").strip(),
            "ngay_hoc": str(target.get("class_date") or "").strip(),
            "ngay_hoc_iso": str(target.get("class_date_iso") or "").strip(),
            "week": target.get("week"),
            "year": target.get("year"),
            "status": "⚠️ Thiếu nhóm Zalo",
            "status_detail": f"Chưa ghép được nhóm Zalo ({target.get('match_warning') or 'MISSING_ZALO_GROUP'}).",
            "submitted_for_bcnb": False,
            "evidence_state": "MISSING_ZALO_GROUP",
            "missing_zalo_group": True,
            "match_warning": target.get("match_warning"),
            "scan_target": base,
            "evidence": {
                "validation_errors": [],
                "date_context": date_ctx,
            },
        }
    return {
        "group": group_name,
        "person": str(target.get("person") or "Chưa xác định").strip(),
        "class_code": str(target.get("class_code") or "").strip(),
        "lesson": str(target.get("lesson") or "").strip(),
        "ngay_hoc": str(target.get("class_date") or "").strip(),
        "ngay_hoc_iso": str(target.get("class_date_iso") or "").strip(),
        "week": target.get("week"),
        "year": target.get("year"),
        "status": "⏳ Chờ kiểm tra",
        "status_detail": "Đang chờ tổng hợp kết quả Zalo & LMS.",
        "submitted_for_bcnb": False,
        "evidence_state": "NOT_SCANNED",
        "missing_zalo_group": False,
        "scan_target": base,
        "evidence": {
            "validation_errors": [],
            "date_context": date_ctx,
        },
    }


def _zalo_setup_notice(error_code: str) -> str:
    """Build a safe Vietnamese status notice when Zalo Web scan setup halts."""
    if error_code in {
        "LOGIN_OR_QR",
        "AUTH_STATE_AMBIGUOUS",
        "AUTH_READINESS_AMBIGUOUS",
    }:
        return (
            "Phiên Zalo Web chưa sẵn sàng hoặc đang yêu cầu đăng nhập/QR; "
            "chưa kiểm tra báo cáo Zalo cho các lớp đã kết thúc."
        )
    return (
        f"Không thể khởi tạo trang Zalo Web ({error_code}); "
        "đã giữ kết quả LMS/SPCK/BCNB và đặt các lớp Zalo về trạng thái cần kiểm tra lại."
    )


async def _claim_batch_target(
    target: dict[str, Any],
    *,
    job_id: int,
    batch_repo: ZaloBatchRepository,
    target_id_by_key: Mapping[str, int],
    persist_batch_targets: bool,
) -> tuple[dict[str, Any], int | None]:
    """Claim one queued Zalo scan row when batch target persistence is enabled."""
    key = canonical_target_key(target)
    if not (persist_batch_targets and key in target_id_by_key):
        return target, None
    claimed = await batch_repo.claim_scan(job_id, "today-ops-1")
    if claimed is None:
        return target, None
    return dict(claimed.get("source") or target), int(claimed["id"])


async def _fail_batch_target(
    batch_repo: ZaloBatchRepository,
    claimed_target_id: int | None,
    *,
    result: dict[str, Any],
    error_code: str,
    error_message: str,
) -> None:
    """Persist one fail-closed Zalo target scan outcome."""
    if claimed_target_id is None:
        return
    await batch_repo.complete_scan(
        claimed_target_id,
        status="FAILED",
        result=result,
        error_code=error_code,
        error_message=error_message,
    )


async def _fail_remaining_targets_on_setup(
    remaining_targets: Sequence[dict[str, Any]],
    *,
    job_id: int,
    today: datetime,
    setup_error: ZaloSetupError,
    job_repo: JobRepository,
    batch_repo: ZaloBatchRepository,
    target_id_by_key: Mapping[str, int],
    persist_batch_targets: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Mark unclaimed remaining targets fail-closed when Zalo setup halts."""
    failed_results: list[dict[str, Any]] = []
    for raw_target in remaining_targets:
        await job_repo.ensure_not_cancelled(job_id)
        target, claimed_target_id = await _claim_batch_target(
            raw_target,
            job_id=job_id,
            batch_repo=batch_repo,
            target_id_by_key=target_id_by_key,
            persist_batch_targets=persist_batch_targets,
        )
        res = _setup_failure_result(target, today=today, setup_error=setup_error)
        await _fail_batch_target(
            batch_repo,
            claimed_target_id,
            result=res,
            error_code=setup_error.code,
            error_message=setup_error.safe_message,
        )
        failed_results.append(res)
    return failed_results, len(failed_results)


async def _scan_ended_zalo_targets(
    session,
    *,
    job_id: int,
    targets: list[dict[str, Any]],
    screenshots_dir: str,
    today: datetime,
    runtime: WorkerRuntime | None = None,
    external: ExternalGateways,
    job_repo: JobRepository,
    batch_repo: ZaloBatchRepository,
    target_id_by_key: Mapping[str, int],
    persist_batch_targets: bool,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Scan ended Zalo targets sequentially on one Zalo Web page with Retina 2x capture."""
    if not targets:
        return [], 0, None
    results: list[dict[str, Any]] = []
    scan_failures = 0
    setup_notice: str | None = None
    zalo_ai_classifier = getattr(external, "zalo_ai_report_classifier", None) or getattr(
        external, "zalo_ai_classifier", None
    )
    landing_provider = getattr(session, "landing_facts_provider", None)
    timeout_seconds = _target_timeout_seconds(runtime)
    closed_pages: set[int] = set()

    async def close_page_once(page: Any) -> None:
        if page is None or id(page) in closed_pages:
            return
        closed_pages.add(id(page))
        try:
            await _close_scan_page(session, page)
        except JobCancelledError:
            raise
        except Exception as exc:
            log_message(
                "Dong trang quet Zalo trong Lop Hom Nay khong thanh cong",
                job_id=job_id,
                error_type=type(exc).__name__,
            )

    def landing_provider_for(target_page: Any):
        if not callable(landing_provider):
            return landing_provider

        async def _provide() -> Any:
            # Delegate inside _setup_scan_page only after navigating to chat.zalo.me.
            return await _invoke_landing_provider(landing_provider, target_page)

        return _provide

    async def open_ready_page(*, max_attempts: int) -> tuple[Any, dict[str, Any]]:
        last_error: ZaloSetupError | None = None
        for attempt in range(1, max_attempts + 1):
            await job_repo.ensure_not_cancelled(job_id)
            candidate_page: Any = None
            setup_started = asyncio.get_running_loop().time()
            try:
                candidate_page = await _new_scan_page(session)
                sync_res = await _setup_scan_page(
                    candidate_page,
                    job_id=job_id,
                    job_repo=job_repo,
                    landing_facts_provider=landing_provider_for(candidate_page),
                )
                await job_repo.ensure_not_cancelled(job_id)
                _emit_setup_telemetry(
                    job_id=job_id,
                    worker_id="today-ops-1",
                    started_at=setup_started,
                    page_state=ScanPageState.READY,
                    selector_count=1,
                    selector_ready=True,
                    error_code=SETUP_OK,
                )
                return candidate_page, sync_res
            except (JobCancelledError, asyncio.CancelledError):
                await close_page_once(candidate_page)
                raise
            except ZaloSetupError as exc:
                last_error = exc
                _emit_setup_telemetry(
                    job_id=job_id,
                    worker_id="today-ops-1",
                    started_at=setup_started,
                    page_state=exc.page_state,
                    selector_count=exc.selector_count,
                    selector_ready=exc.selector_ready,
                    error_code=exc.code,
                )
                await close_page_once(candidate_page)
                if exc.code in LANDING_SETUP_CODES or attempt >= max_attempts:
                    raise
                log_message(
                    "Thu lai khoi tao trang quet Zalo sau loi setup",
                    job_id=job_id,
                    attempt=attempt,
                    error_code=exc.code,
                )
            except Exception as exc:
                await close_page_once(candidate_page)
                raise ZaloSetupError(
                    f"SETUP_{type(exc).__name__.upper()}",
                    page_state=ScanPageState.NAVIGATING,
                ) from exc
        raise last_error or ZaloSetupError(
            "SETUP_UNKNOWN",
            page_state=ScanPageState.NAVIGATING,
        )

    try:
        zalo_page, sync_result = await open_ready_page(max_attempts=MAX_SETUP_ATTEMPTS)
    except (JobCancelledError, asyncio.CancelledError):
        raise
    except ZaloSetupError as setup_exc:
        setup_notice = _zalo_setup_notice(setup_exc.code)
        log_message(
            "Dung quet Zalo trong Lop Hom Nay do setup khong san sang",
            job_id=job_id,
            error_code=setup_exc.code,
            target_count=len(targets),
        )
        failed_results, failed_count = await _fail_remaining_targets_on_setup(
            targets,
            job_id=job_id,
            today=today,
            setup_error=setup_exc,
            job_repo=job_repo,
            batch_repo=batch_repo,
            target_id_by_key=target_id_by_key,
            persist_batch_targets=persist_batch_targets,
        )
        return failed_results, failed_count, setup_notice

    try:
        total = len(targets)
        for idx, raw_target in enumerate(targets):
            await job_repo.ensure_not_cancelled(job_id)
            pct = 50 + int((idx * 24) / max(1, total))
            class_label = str(raw_target.get("class_code") or f"#{idx + 1}")
            await job_repo.update_job(
                job_id,
                message=f"Đang quét Zalo lớp {idx + 1}/{total}: {class_label}...",
                progress=min(74, max(50, pct)),
            )
            target, claimed_target_id = await _claim_batch_target(
                raw_target,
                job_id=job_id,
                batch_repo=batch_repo,
                target_id_by_key=target_id_by_key,
                persist_batch_targets=persist_batch_targets,
            )
            try:
                try:
                    res = await _await_with_target_timeout(
                        _scan_one_target(
                            zalo_page,
                            job_id=job_id,
                            target=target,
                            sync_result=sync_result,
                            screenshots_dir=screenshots_dir,
                            today=today,
                            job_repo=job_repo,
                            worker_id="today-ops-1",
                            ai_report_classifier=zalo_ai_classifier,
                        ),
                        timeout_seconds=timeout_seconds,
                    )
                except TargetSearchError as search_exc:
                    log_message(
                        "Thu lam moi trang quet Zalo sau loi tim kiem target",
                        job_id=job_id,
                        class_code=target.get("class_code"),
                        error_code=search_exc.code,
                    )
                    old_page = zalo_page
                    zalo_page = None
                    await close_page_once(old_page)
                    zalo_page, sync_result = await open_ready_page(max_attempts=1)
                    res = await _await_with_target_timeout(
                        _scan_one_target(
                            zalo_page,
                            job_id=job_id,
                            target=target,
                            sync_result=sync_result,
                            screenshots_dir=screenshots_dir,
                            today=today,
                            job_repo=job_repo,
                            worker_id="today-ops-1",
                            attempt=2,
                            ai_report_classifier=zalo_ai_classifier,
                        ),
                        timeout_seconds=timeout_seconds,
                    )
            except (JobCancelledError, asyncio.CancelledError):
                raise
            except ZaloSetupError as setup_exc:
                setup_notice = setup_notice or _zalo_setup_notice(setup_exc.code)
                res = _setup_failure_result(target, today=today, setup_error=setup_exc)
                scan_failures += 1
                await _fail_batch_target(
                    batch_repo,
                    claimed_target_id,
                    result=res,
                    error_code=setup_exc.code,
                    error_message=setup_exc.safe_message,
                )
                results.append(res)
                if zalo_page is None or setup_exc.code in LANDING_SETUP_CODES:
                    rest_results, rest_failures = await _fail_remaining_targets_on_setup(
                        targets[idx + 1 :],
                        job_id=job_id,
                        today=today,
                        setup_error=setup_exc,
                        job_repo=job_repo,
                        batch_repo=batch_repo,
                        target_id_by_key=target_id_by_key,
                        persist_batch_targets=persist_batch_targets,
                    )
                    results.extend(rest_results)
                    scan_failures += rest_failures
                    break
                continue
            except TargetSearchError as search_exc:
                scan_failures += 1
                res = attach_zalo_sync_result(
                    _target_search_failure_result(target, today=today, error=search_exc),
                    sync_result,
                )
                await _fail_batch_target(
                    batch_repo,
                    claimed_target_id,
                    result=res,
                    error_code=search_exc.code,
                    error_message=search_exc.safe_message,
                )
            except TargetTimeoutError:
                scan_failures += 1
                res = attach_zalo_sync_result(
                    _empty_result(
                        target,
                        today=today,
                        reason="Không đủ bằng chứng vì quét Zalo vượt quá thời gian cho phép.",
                        decision="INSUFFICIENT_EVIDENCE",
                    ),
                    sync_result,
                )
                res["error_code"] = TARGET_TIMEOUT
                await _fail_batch_target(
                    batch_repo,
                    claimed_target_id,
                    result=res,
                    error_code=TARGET_TIMEOUT,
                    error_message="Quét target Zalo vượt quá thời gian cho phép.",
                )
            except Exception as exc:
                scan_failures += 1
                res = attach_zalo_sync_result(
                    _empty_result(
                        target,
                        today=today,
                        reason=f"Lỗi quét Zalo: {type(exc).__name__}.",
                        decision="SCAN_FAILED",
                    ),
                    sync_result,
                )
                res["error_code"] = "SCAN_FAILED"
                await _fail_batch_target(
                    batch_repo,
                    claimed_target_id,
                    result=res,
                    error_code="SCAN_FAILED",
                    error_message=res["error_code"],
                )
            else:
                if claimed_target_id is not None:
                    ev_state = str(res.get("evidence_state") or "")
                    await batch_repo.complete_scan(
                        claimed_target_id,
                        status="VERIFIED",
                        result=res,
                        write_ready=ev_state in {"SUBMITTED_VERIFIED", "SUBMITTED_LATE_VERIFIED"},
                    )
            results.append(res)
    finally:
        await close_page_once(zalo_page)
    return results, scan_failures, setup_notice


async def _persist_batch_write_outcomes(
    *,
    job_id: int,
    execution_mode: str,
    batch_repo: ZaloBatchRepository,
    results: list[dict[str, Any]],
    bcnb_summary: Mapping[str, Any],
) -> None:
    """Update claimed write rows on ``zalo_batch_targets`` cleanly for Weekly Summary."""
    result_by_key: dict[str, dict[str, Any]] = {}
    for item in results:
        lk = _today_lookup_key(item)
        if lk:
            result_by_key[lk] = item
    outcome_by_key: dict[str, dict[str, Any]] = {}
    for outcome in bcnb_summary.get("target_outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        lk = _today_lookup_key(outcome)
        if lk:
            outcome_by_key[lk] = outcome
    complete_write_accepts_result = (
        "result" in inspect.signature(batch_repo.complete_write).parameters
        or any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in inspect.signature(batch_repo.complete_write).parameters.values()
        )
    )
    while True:
        claimed = await batch_repo.claim_write(job_id, "today-ops-bcnb-1")
        if claimed is None:
            break
        key = claimed["target_key"]
        norm_key = _normalized_today_target_key_str(key)
        outcome = outcome_by_key.get(norm_key) or outcome_by_key.get(key)
        res = result_by_key.get(norm_key) or result_by_key.get(key)
        extra_kwargs: dict[str, Any] = (
            {"result": res} if complete_write_accepts_result and res is not None else {}
        )
        if outcome is None:
            status = "SKIPPED" if str((res or {}).get("evidence_state") or "") == "NOT_FOUND_VERIFIED" else "FAILED_BEFORE_WRITE"
            await batch_repo.complete_write(
                claimed["id"],
                status=status,
                error_code=None if status == "SKIPPED" else "TARGET_NOT_UPDATED",
                error_message=None if status == "SKIPPED" else "Chưa ghi nhận kết quả BCNB.",
                **extra_kwargs,
            )
            continue
        status_str = str(outcome.get("status") or "FAILED_BEFORE_WRITE")
        if status_str not in {"VERIFIED", "ALREADY_DONE", "DRY_RUN", "SKIPPED", "FAILED_BEFORE_WRITE", "OUTCOME_UNKNOWN", "BLOCKED_BATCH_HALT"}:
            status_str = "FAILED_BEFORE_WRITE"
        await batch_repo.complete_write(
            claimed["id"],
            status=status_str,
            error_code="BCNB_WRITE_FAILED" if status_str in {"FAILED_BEFORE_WRITE", "OUTCOME_UNKNOWN"} else None,
            error_message=outcome.get("error"),
            **extra_kwargs,
        )


async def _run_bcnb_sync_on_session(
    session,
    *,
    job_id: int,
    effective_mode: str,
    check_only: bool,
    tick_lms: bool,
    ended_results: list[dict[str, Any]],
    all_today_candidates: list[dict[str, Any]],
    snapshot: Mapping[str, Any],
    snapshot_plan: Mapping[str, Any],
    resolved_lms_slots: dict[str, list[dict[str, Any]]] | None,
    ai_reviewer: Any,
    external: ExternalGateways,
    today: datetime,
    now_dt: datetime,
    job_repo: JobRepository,
    audit_repo: AuditRepository,
) -> dict[str, Any]:
    week_plan = snapshot_plan.get("bcnb_week_plan") or {}
    latest_week = week_plan.get("latest_week")
    required_week = week_plan.get("required_week")
    selected_weeks = week_plan.get("selected_weeks") or [required_week, latest_week]

    if check_only or not ended_results:
        scope_entries = all_today_candidates
        scan_results_for_bcnb = all_today_candidates
        effective_check_only = True
        effective_tick_lms = False
    else:
        scope_entries = ended_results
        scan_results_for_bcnb = ended_results
        effective_check_only = False
        effective_tick_lms = tick_lms

    scoped_keys = []
    for entry in scope_entries:
        with contextlib.suppress(ValueError):
            st_raw = entry.get("scan_target")
            st_src = dict(st_raw) if isinstance(st_raw, Mapping) else dict(entry)
            scoped_keys.append(canonical_target_key(st_src))
    scoped_keys = list(dict.fromkeys(scoped_keys))

    if not scoped_keys:
        return {
            "selected_weeks": selected_weeks,
            "updated_count": 0,
            "lms_updated_count": 0,
            "checker_note_updated_count": 0,
            "checker_note_failures": [],
            "checker_note_failure_count": 0,
            "failed_targets": [],
            "lms_failed_targets": [],
            "target_outcomes": [],
            "lms_target_outcomes": [],
            "checker_note_outcomes": [],
            "lms_fetch_unavailable": bool(is_lms_fetch_unavailable(resolved_lms_slots)),
        }

    scan_plan = {
        "snapshot_id": snapshot.get("snapshot_id"),
        "observed_at": snapshot.get("observed_at"),
        "latest_week": latest_week,
        "required_week": required_week,
        "selected_weeks": selected_weeks,
        "targets": list(snapshot_plan.get("scan_targets") or snapshot_plan.get("targets") or []),
        "deferred_targets": list(snapshot_plan.get("deferred_targets") or []),
        "exempt_targets": list(snapshot_plan.get("exempt_targets") or []),
        "selected_target_keys": scoped_keys,
    }

    bcnb_page = await _new_scan_page(session)
    try:
        if Config.BCNB_URL:
            await bcnb_page.goto(Config.BCNB_URL)
        await _login_bcnb_if_needed(bcnb_page)
        return await update_bcnb_reports(
            bcnb_page,
            job_id=job_id,
            execution_mode=effective_mode,
            scan_results=scan_results_for_bcnb,
            today=today,
            now=now_dt,
            scan_plan=scan_plan,
            job_repo=job_repo,
            audit_repo=audit_repo,
            lms_quality_gateway=getattr(external, "lms_quality", None),
            lms_slots_by_class=resolved_lms_slots,
            lms_ai_reviewer=ai_reviewer,
            reset_ai_batch_counter=False,
            tick_lms=effective_tick_lms,
            check_only=effective_check_only,
        )
    finally:
        await _close_scan_page(session, bcnb_page)


async def run_today_realtime_sync(
    job_id: int,
    payload: Mapping[str, Any] | None = None,
    execution_mode: str = "dry-run",
    *,
    runtime: WorkerRuntime | None = None,
    external: ExternalGateways | None = None,
    job_repo: JobRepository | None = None,
    group_repo: GroupRepository | None = None,
    audit_repo: AuditRepository | None = None,
    evidence_repo: EvidenceRepository | None = None,
    batch_repo: ZaloBatchRepository | None = None,
    now_override: datetime | None = None,
) -> None:
    """Execute one real-time Today check or sync+tick job under browser exclusivity."""
    j_repo = job_repo or JobRepository()
    g_repo = group_repo or GroupRepository()
    a_repo = audit_repo or AuditRepository()
    e_repo = evidence_repo or EvidenceRepository()
    b_repo = batch_repo or ZaloBatchRepository()

    req = payload or {}
    check_only = bool(req.get("check_only", False))
    tick_lms = False if check_only else bool(req.get("tick_lms", True))
    refresh_discovery_if_stale = bool(req.get("refresh_discovery_if_stale", True))
    selected_keys_raw = req.get("selected_target_keys")
    selected_target_keys = (
        [str(k).strip() for k in selected_keys_raw if str(k).strip()]
        if isinstance(selected_keys_raw, list)
        else None
    )
    effective_mode = "dry-run" if check_only else str(execution_mode or "dry-run").strip().lower()
    if effective_mode not in {"dry-run", "production"}:
        effective_mode = "dry-run"

    current_now = now_override or now_business()
    today = business_day_start(current_now)
    target_date_iso = parse_target_date_iso(
        req.get("target_date_iso"),
        default_date=current_now.date(),
    )
    evidence_root = (
        getattr(runtime, "evidence_root", None)
        or os.path.abspath(os.path.join("static", "screenshots"))
    )
    screenshots_dir = os.path.join(evidence_root, f"job-{job_id}")
    os.makedirs(screenshots_dir, exist_ok=True)
    effective_external = external or (
        runtime.external if runtime is not None else ExternalGateways.production()
    )
    effective_runtime = (
        runtime
        if runtime is not None
        else WorkerRuntime(external=effective_external, evidence_root=evidence_root)
    )

    try:
        async with exclusive_browser_job(
            job_id,
            j_repo,
            lock=effective_runtime.browser_lock,
            process_lock=getattr(effective_runtime, "profile_lock", None),
        ):
            await j_repo.update_job(
                job_id,
                status="RUNNING",
                message=(
                    f"Đang kiểm tra Live LMS & Zalo ngày {target_date_iso}..."
                    if check_only
                    else f"Đang quét Live & tick BCNB đúng tuần ngày {target_date_iso}..."
                ),
                progress=5,
            )

            if effective_mode == "production" and not check_only:
                try:
                    await _create_production_backup(job_id, effective_runtime, j_repo)
                except Exception as exc:
                    raise RuntimeError("Backup SQLite production không đạt; dừng an toàn.") from exc

            max_age_minutes = int(getattr(Config, "ZALO_PLAN_MAX_AGE_MINUTES", 30))
            snapshot = await g_repo.get_latest_discovery_snapshot()

            async with effective_external.browser.persistent_session() as session:
                if refresh_discovery_if_stale and _snapshot_needs_refresh(
                    snapshot, max_age_minutes=max_age_minutes
                ):
                    await j_repo.update_job(
                        job_id,
                        message="Đang làm mới snapshot 2 tuần từ BCNB & Zalo...",
                        progress=10,
                    )
                    refreshed_snap = await _refresh_discovery_snapshot_in_session(
                        session,
                        job_id=job_id,
                        job_repo=j_repo,
                        group_repo=g_repo,
                        now_dt=current_now,
                    )
                    if refreshed_snap is None:
                        return
                    snapshot = refreshed_snap

                if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("plan"), Mapping):
                    await j_repo.update_job(
                        job_id,
                        status="FAILED",
                        message="Chưa có kế hoạch Dò tìm 2 tuần từ BCNB; hãy chạy Dò tìm hoặc bật làm mới snapshot.",
                        progress=100,
                    )
                    return

                snapshot_plan = snapshot["plan"]
                week_plan = snapshot_plan.get("bcnb_week_plan") or {}
                latest_week = week_plan.get("latest_week")
                required_week = week_plan.get("required_week")
                selected_weeks = week_plan.get("selected_weeks") or [required_week, latest_week]

                all_entries, _ = _collect_today_entries_from_snapshot(
                    snapshot,
                    target_date_iso=target_date_iso,
                )
                all_entries = _filter_selected_targets(all_entries, selected_target_keys)
                if not all_entries:
                    empty_envelope = {
                        "kind": "today_realtime_sync",
                        "selective_rescan": True,
                        "check_only": check_only,
                        "tick_lms": tick_lms,
                        "target_date_iso": target_date_iso,
                        "snapshot_id": snapshot.get("snapshot_id"),
                        "latest_week": latest_week,
                        "required_week": required_week,
                        "selected_weeks": selected_weeks,
                        "items": [],
                        "today_items": [],
                        "bcnb_summary": {"updated_count": 0, "lms_updated_count": 0},
                    }
                    await j_repo.update_job(
                        job_id,
                        status="SUCCESS",
                        message=f"Không có lớp nào diễn ra trong ngày {target_date_iso}.",
                        progress=100,
                        results=json.dumps(empty_envelope, ensure_ascii=False),
                    )
                    return

                await j_repo.update_job(
                    job_id,
                    message=f"Đang kiểm tra lịch, nhận xét & SPCK B12–14 trên LMS ({len(all_entries)} lớp)...",
                    progress=25,
                )
                today_candidates = [
                    _make_today_placeholder_result(entry, today=today, deferred=False)
                    for entry in all_entries
                ]
                ai_reviewer = getattr(effective_external, "lms_ai_reviewer", None) or getattr(
                    effective_external, "lms_comment_reviewer", None
                )
                resolved_lms_slots = await _prefetch_lms_for_today(
                    session,
                    today_candidates=today_candidates,
                    external=effective_external,
                    latest_week=latest_week,
                    now_dt=now_override or now_business(),
                )

                # Partition using updated LMS start/end times
                eval_now = now_override or now_business()
                deferred_entries: list[dict[str, Any]] = []
                ended_scan_targets: list[dict[str, Any]] = []
                ended_exempt_targets: list[dict[str, Any]] = []
                ended_missing_entries: list[dict[str, Any]] = []
                lms_by_key: dict[str, Mapping[str, Any]] = {}

                for entry, candidate in zip(all_entries, today_candidates, strict=True):
                    _copy_lms_fields(entry, candidate)
                    if isinstance(candidate.get("scan_target"), dict):
                        _copy_lms_fields(entry, candidate["scan_target"])
                    lk = _today_lookup_key(entry)
                    if lk:
                        lms_by_key[lk] = candidate
                    time_win = _resolve_row_time_window(entry, now=eval_now)
                    if time_win["session_phase"] != PHASE_ENDED:
                        deferred_entries.append(entry)
                    elif entry.get("missing_zalo_group"):
                        ended_missing_entries.append(entry)
                    elif is_lesson_14(entry.get("lesson")):
                        ended_exempt_targets.append(entry)
                    else:
                        ended_scan_targets.append(entry)

                persist_batch = not check_only
                target_id_by_key: dict[str, int] = {}
                if persist_batch:
                    if ended_scan_targets:
                        await b_repo.seed_targets(job_id, ended_scan_targets)
                    if ended_exempt_targets and hasattr(b_repo, "seed_exempt_targets"):
                        await b_repo.seed_exempt_targets(
                            job_id,
                            ended_exempt_targets,
                            applied_at=utc_iso(current_now),
                        )
                    target_id_by_key = {
                        row["target_key"]: int(row["id"])
                        for row in await b_repo.list_for_job(job_id)
                    }

                await j_repo.update_job(
                    job_id,
                    message=f"Đang quét Zalo {len(ended_scan_targets)} lớp đã kết thúc...",
                    progress=50,
                )
                zalo_scan_results, scan_failures, zalo_setup_notice = (
                    await _scan_ended_zalo_targets(
                        session,
                        job_id=job_id,
                        targets=ended_scan_targets,
                        screenshots_dir=screenshots_dir,
                        today=today,
                        runtime=runtime,
                        external=effective_external,
                        job_repo=j_repo,
                        batch_repo=b_repo,
                        target_id_by_key=target_id_by_key,
                        persist_batch_targets=persist_batch,
                    )
                )

                ended_exempt_results = [
                    apply_lesson_14_exemption(
                        _make_today_placeholder_result(entry, today=today, deferred=False),
                        class_code=str(entry.get("class_code") or ""),
                        applied_at=eval_now,
                        lesson=str(entry.get("lesson") or "14"),
                    )
                    for entry in ended_exempt_targets
                ]
                ended_missing_results = [
                    _make_today_placeholder_result(entry, today=today, deferred=False)
                    for entry in ended_missing_entries
                ]
                deferred_results = [
                    _make_today_placeholder_result(entry, today=today, deferred=True)
                    for entry in deferred_entries
                ]

                ended_writable_results = [*zalo_scan_results, *ended_exempt_results]
                all_today_items = [
                    *ended_writable_results,
                    *ended_missing_results,
                    *deferred_results,
                ]
                for item in all_today_items:
                    lk = _today_lookup_key(item)
                    if lk and lk in lms_by_key:
                        _copy_lms_fields(item, lms_by_key[lk])

                await inspect_and_attach_quality_issues_async(
                    all_today_items,
                    lms_slots_by_class=resolved_lms_slots,
                    now=eval_now,
                    ai_reviewer=ai_reviewer,
                    latest_week=latest_week,
                    reset_ai_batch_counter=False,
                )

                if ended_writable_results:
                    await e_repo.register_scan_results(
                        job_id=job_id,
                        results=ended_writable_results,
                        evidence_root=evidence_root,
                        retention_days=getattr(Config, "EVIDENCE_RETENTION_DAYS", 7),
                    )
                    if persist_batch:
                        ended_exempt_lks = {
                            lk for x in ended_exempt_targets if (lk := _today_lookup_key(x))
                        }
                        for res in ended_writable_results:
                            with contextlib.suppress(ValueError):
                                res_st = res.get("scan_target")
                                res_src = dict(res_st) if isinstance(res_st, Mapping) else res
                                rk = canonical_target_key(res_src)
                                rl = _today_lookup_key(res_src)
                                tid = target_id_by_key.get(rk)
                                if tid is not None and rl not in ended_exempt_lks:
                                    ev_state = str(res.get("evidence_state") or "")
                                    ev_path, ev_sha = get_evidence_reference(res)
                                    err_code = str(res.get("error_code") or "").strip() or None
                                    err_msg = (
                                        str(res.get("reason") or err_code).strip()
                                        if err_code
                                        else None
                                    )
                                    await b_repo.finalize_registered_scan(
                                        tid,
                                        result=res,
                                        write_ready=ev_state
                                        in {"SUBMITTED_VERIFIED", "SUBMITTED_LATE_VERIFIED"},
                                        batch_halted=bool(res.get("setup_error_code")),
                                        evidence_path=ev_path,
                                        evidence_sha256=ev_sha,
                                        error_code=err_code,
                                        error_message=err_msg,
                                    )

                await j_repo.update_job(
                    job_id,
                    message=(
                        "Đang đọc trạng thái NX Zalo & NX LMS từ BCNB..."
                        if check_only
                        else "Đang đối chiếu & tick NX Zalo + NX LMS đúng tuần trên BCNB..."
                    ),
                    progress=75,
                )
                bcnb_summary = await _run_bcnb_sync_on_session(
                    session,
                    job_id=job_id,
                    effective_mode=effective_mode,
                    check_only=check_only,
                    tick_lms=tick_lms,
                    ended_results=ended_writable_results,
                    all_today_candidates=all_today_items,
                    snapshot=snapshot,
                    snapshot_plan=snapshot_plan,
                    resolved_lms_slots=resolved_lms_slots,
                    ai_reviewer=ai_reviewer,
                    external=effective_external,
                    today=today,
                    now_dt=eval_now,
                    job_repo=j_repo,
                    audit_repo=a_repo,
                )

                if persist_batch and ended_writable_results:
                    await _persist_batch_write_outcomes(
                        job_id=job_id,
                        execution_mode=effective_mode,
                        batch_repo=b_repo,
                        results=ended_writable_results,
                        bcnb_summary=bcnb_summary,
                    )

                public_bcnb_summary = (
                    _sanitize_public_bcnb_summary(bcnb_summary) or {}
                )
                failed_zalo_ticks = len(public_bcnb_summary.get("failed_targets") or [])
                failed_lms_ticks = len(public_bcnb_summary.get("lms_failed_targets") or [])
                total_failures = scan_failures + failed_zalo_ticks + failed_lms_ticks
                final_status = "PARTIAL_SUCCESS" if total_failures > 0 else "SUCCESS"
                mode_tag = (
                    "[CHỈ KIỂM TRA LIVE]"
                    if check_only
                    else ("[DRY-RUN]" if effective_mode == "dry-run" else "[PRODUCTION]")
                )
                summary_msg = (
                    f"{mode_tag} Ngày {target_date_iso}: {len(all_today_items)} lớp "
                    f"({len(ended_writable_results)} đủ nhóm/đã tan ca, "
                    f"{len(ended_missing_results)} thiếu nhóm Zalo, "
                    f"{len(deferred_results)} chờ giờ học); "
                    f"NX Zalo: {int(public_bcnb_summary.get('updated_count') or 0)}, "
                    f"NX LMS: {int(public_bcnb_summary.get('lms_updated_count') or 0)}."
                )
                if zalo_setup_notice:
                    summary_msg = f"{summary_msg} Lưu ý Zalo: {zalo_setup_notice}"
                envelope = {
                    "kind": "today_realtime_sync",
                    "selective_rescan": True,
                    "check_only": check_only,
                    "tick_lms": tick_lms,
                    "target_date_iso": target_date_iso,
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "latest_week": latest_week,
                    "required_week": required_week,
                    "selected_weeks": selected_weeks,
                    "items": ended_writable_results,
                    "today_items": all_today_items,
                    "bcnb_summary": public_bcnb_summary,
                }
                if zalo_setup_notice:
                    envelope["zalo_setup_notice"] = zalo_setup_notice
                await j_repo.update_job(
                    job_id,
                    status=final_status,
                    message=summary_msg,
                    progress=100,
                    results=json.dumps(envelope, ensure_ascii=False),
                )
    except JobCancelledError:
        await b_repo.terminalize_interrupted(
            job_id,
            production=(effective_mode == "production" and not check_only),
            reason="Đã dừng job Lớp Hôm Nay theo yêu cầu.",
        )
        await j_repo.update_job(
            job_id,
            status="CANCELLED",
            message="Đã hủy kiểm tra Lớp Hôm Nay theo yêu cầu.",
            progress=100,
            results=json.dumps(
                {
                    "kind": "today_realtime_sync",
                    "check_only": check_only,
                    "target_date_iso": target_date_iso,
                    "cancelled": True,
                },
                ensure_ascii=False,
            ),
        )
    except Exception as exc:
        log_message(
            f"Lỗi xử lý Lớp Hôm Nay (job #{job_id}): {type(exc).__name__}.",
            level="ERROR",
            event="today.sync_failed",
            job_id=job_id,
            error_type=type(exc).__name__,
        )
        await b_repo.terminalize_interrupted(
            job_id,
            production=(effective_mode == "production" and not check_only),
            reason=type(exc).__name__,
        )
        await j_repo.update_job(
            job_id,
            status="FAILED",
            message=f"Lỗi đồng bộ Lớp Hôm Nay: {type(exc).__name__}.",
            progress=100,
        )
