import csv
import io
import json
import re
from typing import Any

from db.database import get_db_connection
from core.time_utils import utc_iso
from repositories.zalo_batch_repository import canonical_target_key
from services.bcnb_safety import split_checker_issue_note
from services.lms_quality_service import evaluate_zalo_content_issues


CSV_HEADERS: tuple[str, ...] = (
    "Mã lớp",
    "Giáo viên",
    "Buổi học",
    "Ngày học",
    "Trạng thái Zalo",
    "Trạng thái LMS",
    "Tình trạng đánh giá",
    "Mô tả vấn đề (TE/TF/QC)",
    "Thời gian kiểm chứng",
)


class WeeklySummaryError(ValueError):
    def __init__(self, message: str, *, code: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def sanitize_csv_cell(value: Any) -> str:
    """Sanitize a cell value for Excel CSV export and prevent formula injection."""
    if value is None:
        return ""
    raw = str(value)
    had_leading_tab_or_cr = raw.startswith(("\t", "\r"))
    cleaned = re.sub(r"[\r\n\t]+", " ", raw).strip()
    if not cleaned:
        return ""
    if had_leading_tab_or_cr or cleaned[0] in {"=", "+", "-", "@"}:
        return f"'{cleaned}"
    return cleaned


def build_weekly_summary_csv(rows: list[dict[str, Any]]) -> str:
    """Build a UTF-8 with BOM CSV string containing only class management columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_st = row.get("scan_target")
        st: dict[str, Any] = raw_st if isinstance(raw_st, dict) else {}
        zalo_status = str(row.get("zalo_status") or "").strip()
        lms_status = str(row.get("lms_status") or "").strip()
        eval_status = str(row.get("evaluation_status") or "").strip()
        if not eval_status and (zalo_status or lms_status):
            eval_status = _resolve_row_evaluation_status(
                zalo_status=zalo_status,
                lms_status=lms_status,
                nx_zalo_checked=row.get("nx_zalo_checked"),
                nx_lms_checked=row.get("nx_lms_checked"),
            )
        issue_desc = str(row.get("issue_description") or "").strip()
        if not issue_desc:
            issue_desc = _resolve_row_issue_description(row, st=st)
        writer.writerow(
            [
                sanitize_csv_cell(row.get("class_code")),
                sanitize_csv_cell(row.get("teacher")),
                sanitize_csv_cell(row.get("lesson")),
                sanitize_csv_cell(row.get("class_date")),
                sanitize_csv_cell(zalo_status),
                sanitize_csv_cell(lms_status),
                sanitize_csv_cell(eval_status),
                sanitize_csv_cell(issue_desc),
                sanitize_csv_cell(row.get("verified_at")),
            ]
        )
    return "\ufeff" + buffer.getvalue()


def is_failed_weekly_summary_row(row: Any) -> bool:
    """Return True when a weekly summary row has an unresolved Zalo, LMS, or quality issue."""
    if not isinstance(row, dict):
        return False
    zalo_status = str(row.get("zalo_status") or "").strip()
    lms_status = str(row.get("lms_status") or "").strip()
    eval_status = str(row.get("evaluation_status") or "").strip()
    raw_st = row.get("scan_target")
    st: dict[str, Any] = raw_st if isinstance(raw_st, dict) else {}
    issue_desc = str(
        row.get("issue_description")
        or _resolve_row_issue_description(row, st=st)
        or ""
    ).strip()
    zalo_ok_or_exempt = ("✅" in zalo_status) or ("Miễn" in zalo_status)
    lms_failed = lms_status.startswith(("❌", "⚠️"))
    eval_failed = eval_status.startswith(("❌", "⚠️"))
    return (not zalo_ok_or_exempt) or lms_failed or eval_failed or bool(issue_desc)


def filter_failed_weekly_summary_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter weekly summary rows to only classes with Zalo or LMS errors."""
    return [row for row in (rows or []) if is_failed_weekly_summary_row(row)]


def _parse_json(raw: Any, fallback: Any = None) -> Any:
    if raw is None:
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _valid_period_pair(week_val: Any, year_val: Any) -> tuple[int, int] | None:
    if isinstance(week_val, bool) or isinstance(year_val, bool):
        return None
    try:
        week = int(week_val)
        year = int(year_val)
    except (TypeError, ValueError):
        return None
    if 1 <= week <= 53 and 2000 <= year <= 2100:
        return week, year
    return None


def _extract_item_period(
    item: dict[str, Any],
    default_period: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    scan_target = item.get("scan_target")
    if isinstance(scan_target, dict):
        pair = _valid_period_pair(scan_target.get("week"), scan_target.get("year"))
        if pair is not None:
            return pair
    evidence = item.get("evidence")
    if isinstance(evidence, dict):
        ctx = evidence.get("date_context")
        if isinstance(ctx, dict):
            pair = _valid_period_pair(ctx.get("target_week"), ctx.get("target_year"))
            if pair is not None:
                return pair
    pair = _valid_period_pair(item.get("week"), item.get("year"))
    if pair is not None:
        return pair
    return default_period


def _extract_item_class_code(item: dict[str, Any]) -> str:
    scan_target = item.get("scan_target")
    if isinstance(scan_target, dict):
        code = str(scan_target.get("class_code") or "").strip().upper()
        if code:
            return code
    evidence = item.get("evidence")
    if isinstance(evidence, dict):
        ctx = evidence.get("date_context")
        if isinstance(ctx, dict):
            code = str(ctx.get("target_class_code") or "").strip().upper()
            if code:
                return code
    code = str(item.get("class_code") or "").strip().upper()
    if code:
        return code
    group_name = str(item.get("group") or "").strip()
    if group_name:
        cleaned = re.sub(r"^MindX\s*[-–—]\s*", "", group_name, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned).strip().upper()
        if cleaned:
            return cleaned
    return "KHONG_RO_LOP"


def _normalize_date_iso(raw_date: Any) -> str:
    text = str(raw_date or "").strip()
    if not text:
        return ""
    dm_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if dm_match:
        return (
            f"{int(dm_match.group(3)):04d}-{int(dm_match.group(2)):02d}-{int(dm_match.group(1)):02d}"
        )
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return text


def _extract_item_row_key(item: dict[str, Any], week: int, year: int) -> str:
    scan_target = item.get("scan_target")
    st = dict(scan_target) if isinstance(scan_target, dict) else {}
    st.setdefault("week", week)
    st.setdefault("year", year)
    st.setdefault("class_code", _extract_item_class_code(item))
    evidence = item.get("evidence")
    ctx = evidence.get("date_context") if isinstance(evidence, dict) else None
    if not st.get("lesson"):
        if isinstance(ctx, dict) and ctx.get("target_lesson"):
            st["lesson"] = str(ctx.get("target_lesson")).strip()
    if not st.get("class_date_iso"):
        candidate_date = (
            st.get("class_date")
            or (ctx.get("target_class_date") if isinstance(ctx, dict) else None)
            or item.get("class_date_iso")
            or item.get("class_date")
        )
        if candidate_date:
            st["class_date_iso"] = _normalize_date_iso(candidate_date)
    else:
        st["class_date_iso"] = _normalize_date_iso(st["class_date_iso"])
    try:
        return canonical_target_key(st)
    except ValueError:
        class_code = _extract_item_class_code(item)
        lesson = str(st.get("lesson") or item.get("lesson") or "?").strip()
        class_date = str(
            st.get("class_date_iso")
            or st.get("class_date")
            or item.get("class_date")
            or "?"
        ).strip()
        return f"{year:04d}-W{week:02d}|{class_code}|{lesson}|{class_date}"


def _is_verified_manual_approved_item(item: dict[str, Any]) -> bool:
    manual_meta = item.get("manual_approval")
    if isinstance(manual_meta, dict):
        if bool(manual_meta.get("dry_run")):
            return False
        if str(manual_meta.get("outcome") or "").upper() == "VERIFIED":
            return True
    if (
        bool(item.get("manual_approved"))
        and str(item.get("bcnb_update_state") or "").upper() == "VERIFIED"
        and not (isinstance(manual_meta, dict) and bool(manual_meta.get("dry_run")))
    ):
        return True
    return False


def _apply_verified_manual_approval(
    src_job_id: int,
    job_ts: str,
    item: dict[str, Any],
    approval_info: dict[str, Any],
    *,
    week: int,
    year: int,
) -> tuple[int, str, dict[str, Any]]:
    merged = dict(item)
    approved_item = approval_info.get("approved_item")
    if isinstance(approved_item, dict):
        if not merged.get("evidence") and approved_item.get("evidence"):
            merged["evidence"] = approved_item["evidence"]
        elif isinstance(merged.get("evidence"), dict) and isinstance(
            approved_item.get("evidence"), dict
        ):
            merged_imgs = merged["evidence"].get("images")
            app_imgs = approved_item["evidence"].get("images")
            if not merged_imgs and app_imgs:
                merged_ev = dict(merged["evidence"])
                merged_ev["images"] = app_imgs
                merged["evidence"] = merged_ev
    approved_by = (
        str(approval_info.get("approved_by") or "operator").strip() or "operator"
    )
    st_raw = merged.get("scan_target")
    st_dict = dict(st_raw) if isinstance(st_raw, dict) else {}
    approval_sid = str(approval_info.get("bcnb_session_id") or "").strip()
    if not approval_sid and isinstance(approved_item, dict):
        app_st = approved_item.get("scan_target")
        app_ma = approved_item.get("manual_approval")
        approval_sid = str(
            approved_item.get("bcnb_session_id")
            or (app_st.get("bcnb_session_id") if isinstance(app_st, dict) else "")
            or (app_ma.get("bcnb_session_id") if isinstance(app_ma, dict) else "")
            or ""
        ).strip()
    if approval_sid:
        merged["bcnb_session_id"] = approval_sid
        st_dict["bcnb_session_id"] = approval_sid
        merged["scan_target"] = st_dict
    lesson = str(
        approval_info.get("lesson") or st_dict.get("lesson") or "?"
    ).strip()
    merged["status"] = "✅ Đã duyệt tay & Tích BCNB"
    merged["status_detail"] = (
        f"Duyệt tay bởi {approved_by} · BCNB W{week}/{year} buổi {lesson}"
    )
    merged["submitted_for_bcnb"] = True
    merged["manual_approved"] = True
    merged["evidence_state"] = "MANUAL_APPROVED_VERIFIED"
    merged["bcnb_update_state"] = "VERIFIED"
    existing_ma = merged.get("manual_approval")
    ma_dict = dict(existing_ma) if isinstance(existing_ma, dict) else {}
    approved_at = str(
        approval_info.get("approved_at") or ma_dict.get("approved_at") or job_ts
    ).strip()
    resolved_src_job_id = (
        int(approval_info["source_job_id"])
        if isinstance(approval_info.get("source_job_id"), int)
        and not isinstance(approval_info.get("source_job_id"), bool)
        and int(approval_info["source_job_id"]) > 0
        else src_job_id
    )
    ma_dict.update(
        {
            "approved_by": approved_by,
            "approved_at": approved_at,
            "approval_job_id": (
                approval_info.get("approval_job_id")
                or ma_dict.get("approval_job_id")
            ),
            "source_job_id": resolved_src_job_id,
            "evidence_path": (
                approval_info.get("evidence_path")
                or ma_dict.get("evidence_path")
                or ""
            ),
            "evidence_sha256": (
                approval_info.get("evidence_sha256")
                or ma_dict.get("evidence_sha256")
                or ""
            ),
            "outcome": "VERIFIED",
            "dry_run": False,
            "target_key": approval_info.get("target_key"),
            **(
                {"bcnb_session_id": approval_sid}
                if approval_sid
                else {}
            ),
        }
    )
    merged["manual_approval"] = ma_dict
    merged["source_job_id"] = resolved_src_job_id
    return (resolved_src_job_id, approved_at or job_ts, merged)


def _is_halted_unscanned_item(item: dict[str, Any]) -> bool:
    if str(item.get("evidence_state") or "") == "BLOCKED_BATCH_HALT":
        return True
    if str(item.get("bcnb_update_state") or "") == "BLOCKED_BATCH_HALT":
        return True
    status = str(item.get("status") or "")
    return "BLOCKED_BATCH_HALT" in status


def _first_defined_str(*sources: dict[str, Any], key: str) -> str:
    """Return the first defined (non-None) string value for key, preserving explicit empty strings."""
    for src in sources:
        if isinstance(src, dict) and key in src and src.get(key) is not None:
            return str(src.get(key) or "").strip()
    return ""


def _resolve_row_evaluation_status(
    *,
    zalo_status: str,
    lms_status: str,
    nx_zalo_checked: bool | None = None,
    nx_lms_checked: bool | None = None,
    raw_evaluation_status: str = "",
) -> str:
    """Resolve the unified Teaching evaluation status for a weekly summary row."""
    clean_raw = str(raw_evaluation_status or "").strip()
    zalo_failed_or_warn = zalo_status.startswith(("❌", "⚠️"))
    lms_failed_or_warn = lms_status.startswith(("❌", "⚠️"))
    zalo_done = not zalo_failed_or_warn and (
        ("✅" in zalo_status)
        or ("Miễn" in zalo_status)
        or ("🟡" in zalo_status)
        or (zalo_status in {"", "Chưa xác định", "Chưa quét Zalo"} and bool(nx_zalo_checked))
    )
    lms_done = not lms_failed_or_warn and (
        lms_status.startswith("✅")
        or ("Dry-run LMS" in lms_status)
        or ("Chờ tích BCNB" in lms_status)
        or (lms_status in {"", "Chưa quét LMS"} and bool(nx_lms_checked))
    )
    if lms_status.startswith("⚠️") or zalo_status.startswith("⚠️"):
        if zalo_done and lms_status.startswith("⚠️"):
            return "⚠️ Đã nhận xét Zalo, LMS cần kiểm tra"
        if lms_done and zalo_status.startswith("⚠️"):
            return "⚠️ Đã hoàn tất nhận xét LMS, Zalo cần kiểm tra"
        return "⚠️ Cần kiểm tra nhận xét"
    if clean_raw.startswith(("⚠️", "❌")) or "cần kiểm tra" in clean_raw.lower():
        if zalo_done and lms_done:
            return clean_raw
    if zalo_done and lms_done:
        return "✅ Đã hoàn thành nhận xét"
    if lms_done and not zalo_done:
        if clean_raw.startswith("⚠️") or (
            clean_raw and "chưa nhận xét zalo" in clean_raw.lower()
        ):
            return clean_raw
        return "⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo"
    if zalo_done and not lms_done:
        if clean_raw.startswith("⚠️") or (
            clean_raw and "chưa nhận xét lms" in clean_raw.lower()
        ):
            return clean_raw
        return "⏳ Đã hoàn tất nhận xét Zalo, chưa nhận xét LMS"
    if (
        clean_raw
        and "hoàn thành" not in clean_raw.lower()
        and "hoàn tất" not in clean_raw.lower()
    ):
        return clean_raw
    return "❌ Chưa hoàn thành nhận xét"


def _resolve_row_issue_description(
    item: dict[str, Any],
    *,
    st: dict[str, Any],
    fb: dict[str, Any] | None = None,
) -> str:
    """Combine QC issue notes, LMS quality issues, and Zalo content warnings."""
    parts: list[str] = []
    seen_norm: set[str] = set()

    def _norm_issue_key(text: str) -> str:
        stripped = re.sub(r"^\[[^\]]+\]\s*", "", text.strip())
        stripped = stripped.replace(" — ", "; ").strip(" .;").lower()
        return " ".join(stripped.split())

    def _add_one(raw_segment: str) -> None:
        cleaned = " ".join(str(raw_segment or "").split()).strip(" ;")
        if not cleaned:
            return
        key = _norm_issue_key(cleaned)
        if key and key not in seen_norm:
            seen_norm.add(key)
            parts.append(cleaned)

    def _add(val: Any, *, split_semicolon: bool = False) -> None:
        if not val:
            return
        if isinstance(val, (list, tuple)):
            for entry in val:
                _add(entry, split_semicolon=False)
            return
        text = str(val).strip()
        if not text:
            return
        # Unwrap [Auto QC: ...] blocks using balanced-bracket parsing so embedded issues deduplicate cleanly
        if re.search(r"\[Auto\s+QC:\s*", text, flags=re.IGNORECASE):
            manual_note, auto_segments = split_checker_issue_note(text)
            if manual_note:
                _add_one(manual_note)
            for seg in auto_segments:
                _add_one(seg)
            return
        if split_semicolon:
            for segment in text.split(";"):
                _add_one(segment)
        else:
            _add_one(text)

    fb_dict = fb or {}
    qc_note = _first_defined_str(item, st, fb_dict, key="qc_issue_note")
    _add(qc_note, split_semicolon=False)
    _add(item.get("quality_issues") or st.get("quality_issues"))
    _add(
        item.get("lms_quality_issues")
        or st.get("lms_quality_issues")
        or fb_dict.get("lms_quality_issues")
    )
    _add(
        item.get("zalo_content_issues")
        or st.get("zalo_content_issues")
        or fb_dict.get("zalo_content_issues")
    )
    lms_summary = _first_defined_str(item, st, fb_dict, key="lms_lesson_summary")
    for zalo_issue in evaluate_zalo_content_issues(
        item,
        lms_summary_text=lms_summary,
    ):
        _add_one(zalo_issue)
    return "; ".join(parts)


def _sanitize_dashboard_item(
    item: dict[str, Any],
    *,
    week: int,
    year: int,
    source_job_id: int,
    lms_status: str,
    job_timestamp: str,
    snapshot_id: Any = None,
    fallback_bcnb_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scan_target = item.get("scan_target")
    st = dict(scan_target) if isinstance(scan_target, dict) else {}
    fb = fallback_bcnb_meta if isinstance(fallback_bcnb_meta, dict) else {}
    class_code = _extract_item_class_code(item)
    st.setdefault("week", week)
    st.setdefault("year", year)
    st.setdefault("class_code", class_code)
    evidence_raw = item.get("evidence")
    ev = dict(evidence_raw) if isinstance(evidence_raw, dict) else {}
    ctx_raw = ev.get("date_context")
    ctx = dict(ctx_raw) if isinstance(ctx_raw, dict) else {}
    if not st.get("lesson") and ctx.get("target_lesson") is not None:
        st["lesson"] = str(ctx.get("target_lesson")).strip()
    if not st.get("class_date") and ctx.get("target_class_date"):
        st["class_date"] = str(ctx.get("target_class_date")).strip()
    if not st.get("class_date_iso") and st.get("class_date"):
        raw_date = str(st["class_date"]).strip()
        dm_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw_date)
        if dm_match:
            st["class_date_iso"] = (
                f"{int(dm_match.group(3)):04d}-{int(dm_match.group(2)):02d}-{int(dm_match.group(1)):02d}"
            )
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            st["class_date_iso"] = raw_date

    for ext_str_key in (
        "bcnb_session_id",
        "khoi",
        "lms_deadline",
        "evaluation_status",
    ):
        val = _first_defined_str(item, st, fb, key=ext_str_key)
        if val:
            st[ext_str_key] = val

    for note_key in ("qc_issue_note", "instructor_note"):
        defined_note = _first_defined_str(item, st, fb, key=note_key)
        if any(note_key in src and src.get(note_key) is not None for src in (item, st, fb)):
            st[note_key] = defined_note

    nx_zalo_checked: bool | None = None
    nx_lms_checked: bool | None = None
    for candidate_src in (item, st, fb):
        if nx_zalo_checked is None and isinstance(
            candidate_src.get("nx_zalo_checked"), bool
        ):
            nx_zalo_checked = bool(candidate_src["nx_zalo_checked"])
        if nx_lms_checked is None and isinstance(
            candidate_src.get("nx_lms_checked"), bool
        ):
            nx_lms_checked = bool(candidate_src["nx_lms_checked"])
    if isinstance(nx_zalo_checked, bool):
        st["nx_zalo_checked"] = nx_zalo_checked
    if isinstance(nx_lms_checked, bool):
        st["nx_lms_checked"] = nx_lms_checked

    resolved_lms_status = lms_status
    if resolved_lms_status == "Chưa quét LMS":
        if nx_lms_checked is True:
            resolved_lms_status = "✅ Đã nộp LMS"
        elif nx_lms_checked is False:
            resolved_lms_status = "❌ Chưa nộp LMS"

    zalo_status_str = str(item.get("status") or "Chưa xác định")
    evaluation_status = _resolve_row_evaluation_status(
        zalo_status=zalo_status_str,
        lms_status=resolved_lms_status,
        nx_zalo_checked=nx_zalo_checked,
        nx_lms_checked=nx_lms_checked,
        raw_evaluation_status=str(st.get("evaluation_status") or ""),
    )
    issue_description = _resolve_row_issue_description(
        item,
        st=st,
        fb=fb,
    )
    instructor_note = _first_defined_str(item, st, fb, key="instructor_note")

    images_raw = ev.get("images")
    sanitized_images: dict[str, dict[str, Any]] = {}
    if isinstance(images_raw, dict):
        for key in ("report", "latest", "oldest", "oldest_message"):
            img = images_raw.get(key)
            if isinstance(img, dict) and img.get("path"):
                sanitized_images[key] = {
                    "path": str(img.get("path")),
                    "evidence_status": str(
                        img.get("evidence_status") or img.get("status") or "AVAILABLE"
                    ),
                    "sha256": img.get("sha256"),
                    "size_bytes": img.get("size_bytes"),
                    "capture_type": img.get("capture_type"),
                }

    sanitized_evidence = {
        "images": sanitized_images,
        "date_context": {
            "target_week": week,
            "target_year": year,
            "target_lesson": st.get("lesson"),
            "target_class_code": class_code,
            "target_class_date": st.get("class_date") or st.get("class_date_iso"),
            **(
                {"target_match_note": ctx.get("target_match_note")}
                if ctx.get("target_match_note")
                else {}
            ),
        },
        "messages_checked": int(ev.get("messages_checked") or 0),
        "oldest_message_date": ev.get("oldest_message_date"),
        "newest_message_date": ev.get("newest_message_date"),
        "required_window_start": ev.get("required_window_start"),
        "required_window_end": ev.get("required_window_end"),
        "evidence_statuses": list(ev.get("evidence_statuses") or []),
        "scanned_at": ev.get("scanned_at") or job_timestamp,
    }
    resolved_source_job_id = (
        int(item["source_job_id"])
        if isinstance(item.get("source_job_id"), int)
        and not isinstance(item.get("source_job_id"), bool)
        and int(item["source_job_id"]) > 0
        else source_job_id
    )
    resolved_snapshot_id = (
        item.get("_origin_snapshot_id")
        if "_origin_snapshot_id" in item
        else (snapshot_id if snapshot_id is not None else item.get("snapshot_id"))
    )
    return {
        "group": str(item.get("group") or st.get("group_name") or class_code),
        "person": str(item.get("person") or st.get("person") or "Chưa phân công"),
        "timestamp": str(
            ev.get("scanned_at") or item.get("timestamp") or job_timestamp or "—"
        ),
        "status": zalo_status_str,
        "status_detail": item.get("status_detail"),
        "lms_status": resolved_lms_status,
        "evaluation_status": evaluation_status,
        "issue_description": issue_description,
        "qc_issue_note": str(st.get("qc_issue_note") or ""),
        "instructor_note": instructor_note,
        "khoi": str(st.get("khoi") or ""),
        "bcnb_session_id": str(st.get("bcnb_session_id") or ""),
        "nx_zalo_checked": nx_zalo_checked,
        "nx_lms_checked": nx_lms_checked,
        "submitted_for_bcnb": bool(item.get("submitted_for_bcnb")),
        "manual_approved": bool(item.get("manual_approved")),
        "evidence_state": item.get("evidence_state"),
        "bcnb_update_state": item.get("bcnb_update_state"),
        "source_job_id": resolved_source_job_id,
        "snapshot_id": resolved_snapshot_id,
        "scan_target": st,
        "evidence": sanitized_evidence,
    }

class ReportRepository:
    async def get_form_mappings(self):
        async with get_db_connection() as db:
            async with db.execute("SELECT id, question, field_type, value FROM form_mappings ORDER BY id") as cursor:
                rows = await cursor.fetchall()
                return [{"id": row[0], "question": row[1], "field_type": row[2], "value": row[3]} for row in rows]

    async def get_form_mapping_dict(self):
        mappings = await self.get_form_mappings()
        mapping_dict = {}
        for m in mappings:
            mapping_dict[m['question']] = {"type": m['field_type'], "value": m['value']}
        return mapping_dict

    async def add_form_mapping(self, question, field_type, value):
        async with get_db_connection() as db:
            await db.execute("INSERT INTO form_mappings (question, field_type, value) VALUES (?, ?, ?)", (question, field_type, value))
            await db.commit()

    async def update_form_mapping(self, mapping_id, question, field_type, value):
        async with get_db_connection() as db:
            await db.execute("UPDATE form_mappings SET question=?, field_type=?, value=? WHERE id=?", (question, field_type, value, mapping_id))
            await db.commit()

    async def delete_form_mapping(self, mapping_id):
        async with get_db_connection() as db:
            await db.execute("DELETE FROM form_mappings WHERE id=?", (mapping_id,))
            await db.commit()

    async def get_form_submissions(self):
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT id, student_name, class_code, submitted_at, status, "
                "error_message, job_id, action_key, screenshot_path "
                "FROM form_submissions ORDER BY id DESC LIMIT 100"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "id": row[0],
                        "student_name": row[1],
                        "class_code": row[2],
                        "submitted_at": row[3],
                        "status": row[4],
                        "error_message": row[5],
                        "job_id": row[6],
                        "action_key": row[7],
                        "screenshot_path": row[8],
                    }
                    for row in rows
                ]

    async def get_form_submission(self, submission_id: int):
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT id, student_name, class_code, submitted_at, status, "
                "error_message, job_id, action_key, screenshot_path "
                "FROM form_submissions WHERE id = ?",
                (submission_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "student_name": row[1],
                    "class_code": row[2],
                    "submitted_at": row[3],
                    "status": row[4],
                    "error_message": row[5],
                    "job_id": row[6],
                    "action_key": row[7],
                    "screenshot_path": row[8],
                }

    async def add_form_submission(
        self,
        student_name,
        class_code,
        status,
        error_message="",
        *,
        job_id=None,
        action_key=None,
        screenshot_path=None,
    ):
        submitted_at = utc_iso()
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO form_submissions "
                "(student_name, class_code, submitted_at, status, error_message, "
                "job_id, action_key, screenshot_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    student_name,
                    class_code,
                    submitted_at,
                    status,
                    error_message,
                    job_id,
                    action_key,
                    screenshot_path,
                ),
            )
            await db.commit()

    async def delete_submission_screenshot(self, submission_id: int) -> bool:
        sub = await self.get_form_submission(submission_id)
        if not sub:
            return False
        screenshot_path = sub.get("screenshot_path")
        if screenshot_path:
            try:
                import os
                if os.path.exists(screenshot_path):
                    os.remove(screenshot_path)
            except Exception:
                pass
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE form_submissions SET screenshot_path = NULL WHERE id = ?",
                (submission_id,),
            )
            await db.commit()
        return True

    async def clear_form_submissions(self) -> int:
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT screenshot_path FROM form_submissions WHERE screenshot_path IS NOT NULL"
            ) as cursor:
                rows = await cursor.fetchall()
                import os
                for row in rows:
                    p = row[0]
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
            async with db.execute("DELETE FROM form_submissions") as cursor:
                deleted_count = cursor.rowcount
            await db.commit()
            return deleted_count
            
    async def save_report(self, group_name, class_code, lesson_number, lesson_date, confidence):
        created_at = utc_iso()
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO reports (group_name, class_code, lesson_number, lesson_date, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (group_name, class_code, lesson_number, lesson_date, confidence, created_at)
            )
            await db.commit()
            
    async def save_classification_log(self, message, rule_score, embedding_score, llm_result, final_result):
        created_at = utc_iso()
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO classification_logs (message, rule_score, embedding_score, llm_result, final_result, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message, rule_score, embedding_score, llm_result, final_result, created_at)
            )
            await db.commit()

    async def get_weekly_summary(
        self,
        *,
        week: int | None = None,
        year: int | None = None,
        job_id: int | None = None,
    ) -> dict[str, Any]:
        """Aggregate Zalo scan results and LMS status for a selected week from SQLite."""
        async with get_db_connection() as db:
            explicit_job_row = None
            if job_id is not None:
                async with db.execute(
                    """
                    SELECT id, job_type, status, created_at, started_at, finished_at,
                           results, request_payload, mutates_external
                    FROM scan_jobs
                    WHERE id = ?
                    """,
                    (int(job_id),),
                ) as cursor:
                    explicit_job_row = await cursor.fetchone()
                if explicit_job_row is None:
                    raise WeeklySummaryError(
                        "Không tìm thấy job quét đã chọn.",
                        code="JOB_NOT_FOUND",
                        status_code=404,
                    )
                if str(explicit_job_row[1] or "") != "ZALO_SCAN":
                    raise WeeklySummaryError(
                        "Job được chọn phải là job quét Zalo (ZALO_SCAN).",
                        code="INVALID_SCAN_JOB",
                        status_code=409,
                    )

            async with db.execute(
                """
                SELECT id, job_type, status, created_at, started_at, finished_at,
                       results, request_payload, mutates_external
                FROM scan_jobs
                WHERE job_type = 'ZALO_SCAN'
                ORDER BY id DESC
                """
            ) as cursor:
                zalo_job_rows = list(await cursor.fetchall())

            if explicit_job_row is not None and all(
                int(r[0]) != int(explicit_job_row[0]) for r in zalo_job_rows
            ):
                zalo_job_rows.append(explicit_job_row)

            async with db.execute(
                """
                SELECT DISTINCT week, year
                FROM zalo_batch_targets
                """
            ) as cursor:
                distinct_target_week_rows = list(await cursor.fetchall())

            if week is not None and year is not None and job_id is not None:
                async with db.execute(
                    """
                    SELECT job_id, group_name, person, class_code, week, year,
                           lesson, class_date, class_date_iso, scan_status,
                           write_status, result_json, updated_at, finished_at, created_at
                    FROM zalo_batch_targets
                    WHERE (week = ? AND year = ?) OR job_id = ?
                    ORDER BY job_id DESC, id ASC
                    """,
                    (int(week), int(year), int(job_id)),
                ) as cursor:
                    batch_target_rows = list(await cursor.fetchall())
            elif week is not None and year is not None:
                async with db.execute(
                    """
                    SELECT job_id, group_name, person, class_code, week, year,
                           lesson, class_date, class_date_iso, scan_status,
                           write_status, result_json, updated_at, finished_at, created_at
                    FROM zalo_batch_targets
                    WHERE week = ? AND year = ?
                    ORDER BY job_id DESC, id ASC
                    """,
                    (int(week), int(year)),
                ) as cursor:
                    batch_target_rows = list(await cursor.fetchall())
            else:
                async with db.execute(
                    """
                    SELECT job_id, group_name, person, class_code, week, year,
                           lesson, class_date, class_date_iso, scan_status,
                           write_status, result_json, updated_at, finished_at, created_at
                    FROM zalo_batch_targets
                    ORDER BY job_id DESC, id ASC
                    """
                ) as cursor:
                    batch_target_rows = list(await cursor.fetchall())

            async with db.execute(
                """
                SELECT analysis_job_id, plan_payload, created_at, consumed_at,
                       execution_job_id, invalidated_at
                FROM lms_confirmations
                WHERE invalidated_at IS NULL
                ORDER BY analysis_job_id DESC
                """
            ) as cursor:
                lms_conf_rows = list(await cursor.fetchall())

            async with db.execute(
                """
                SELECT id, job_type, status, created_at, started_at, finished_at,
                       results, request_payload, mutates_external
                FROM scan_jobs
                WHERE job_type IN ('LMS_ANALYSIS', 'LMS_AUTOMATION')
                ORDER BY id DESC
                """
            ) as cursor:
                lms_job_rows = list(await cursor.fetchall())

            async with db.execute(
                """
                SELECT job_id, target_id, mode, reason_json, outcome,
                       completed_at, created_at
                FROM external_action_audit
                WHERE action_type = 'BCNB_LMS_TICK'
                ORDER BY id DESC
                """
            ) as cursor:
                lms_audit_rows = list(await cursor.fetchall())

            async with db.execute(
                """
                SELECT job_id, target_id, mode, reason_json, outcome,
                       evidence_path, evidence_sha256, completed_at, created_at
                FROM external_action_audit
                WHERE action_type = 'BCNB_MANUAL_APPROVE_TICK'
                  AND mode = 'production'
                  AND outcome = 'VERIFIED'
                ORDER BY id DESC
                """
            ) as cursor:
                manual_approve_audit_rows = list(await cursor.fetchall())

            async with db.execute(
                """
                SELECT plan_json
                FROM discovery_snapshots
                WHERE status IN ('OBSERVED_ONLY', 'PROMOTED')
                ORDER BY id DESC
                LIMIT 1
                """
            ) as cursor:
                latest_snapshot_row = await cursor.fetchone()

            async with db.execute(
                "SELECT class_code, group_name, person_in_charge FROM groups"
            ) as cursor:
                group_rows = list(await cursor.fetchall())

        teacher_by_class_code: dict[str, str] = {}
        for g_code, g_name, g_person in group_rows:
            person_str = str(g_person or "").strip()
            if not person_str:
                continue
            clean_code = str(g_code or "").strip().upper()
            if clean_code and clean_code not in teacher_by_class_code:
                teacher_by_class_code[clean_code] = person_str
            if g_name:
                inferred = _extract_item_class_code({"group": str(g_name)})
                if inferred and inferred not in teacher_by_class_code:
                    teacher_by_class_code[inferred] = person_str

        batch_targets_by_job: dict[int, list[dict[str, Any]]] = {}
        for bt in batch_target_rows:
            bt_job_id = int(bt[0])
            bt_result = _parse_json(bt[11], None)
            bt_week, bt_year = int(bt[4]), int(bt[5])
            st_obj = {
                "group_name": str(bt[1] or ""),
                "person": str(bt[2] or "Chưa phân công"),
                "class_code": str(bt[3] or "").strip().upper(),
                "week": bt_week,
                "year": bt_year,
                "lesson": str(bt[6] or ""),
                "class_date": str(bt[7] or bt[8] or ""),
                "class_date_iso": str(bt[8] or ""),
            }
            if isinstance(bt_result, dict):
                merged_item = dict(bt_result)
                existing_st = merged_item.get("scan_target")
                if isinstance(existing_st, dict):
                    merged_st = {**st_obj, **existing_st}
                else:
                    merged_st = st_obj
                merged_item["scan_target"] = merged_st
                merged_item.setdefault("group", st_obj["group_name"])
                merged_item.setdefault("person", st_obj["person"])
                merged_item.setdefault("source_job_id", bt_job_id)
                merged_item.setdefault(
                    "timestamp", str(bt[13] or bt[12] or bt[14] or "")
                )
            else:
                scan_status = str(bt[9] or "PENDING")
                write_status = str(bt[10] or "NOT_READY")
                fallback_status = (
                    "⚠️ Bị dừng batch halt"
                    if write_status == "BLOCKED_BATCH_HALT"
                    else f"⚠️ Chưa hoàn tất ({scan_status})"
                )
                merged_item = {
                    "group": st_obj["group_name"],
                    "person": st_obj["person"],
                    "status": fallback_status,
                    "timestamp": str(bt[13] or bt[12] or bt[14] or ""),
                    "evidence_state": (
                        "BLOCKED_BATCH_HALT"
                        if write_status == "BLOCKED_BATCH_HALT"
                        else "UNKNOWN"
                    ),
                    "bcnb_update_state": write_status,
                    "source_job_id": bt_job_id,
                    "scan_target": st_obj,
                }
            batch_targets_by_job.setdefault(bt_job_id, []).append(merged_item)

        bcnb_meta_by_lesson_rows: dict[tuple[int, int, str, str], list[dict[str, Any]]] = {}
        bcnb_meta_by_code_rows: dict[tuple[int, int, str], list[dict[str, Any]]] = {}

        def _can_merge_bcnb_meta(
            existing: dict[str, Any],
            incoming: dict[str, Any],
            *,
            source_tag: str,
        ) -> bool:
            if existing.get("_lesson") != incoming.get("_lesson"):
                return False
            ex_date = str(existing.get("_class_date_iso") or "").strip()
            in_date = str(incoming.get("_class_date_iso") or "").strip()
            if not ex_date or ex_date != in_date:
                return False
            if source_tag and source_tag in existing.get("_sources", set()):
                return False
            ex_sid = str(existing.get("bcnb_session_id") or "").strip()
            in_sid = str(incoming.get("bcnb_session_id") or "").strip()
            if ex_sid != in_sid:
                return False
            for cmp_key in (
                "nx_zalo_checked",
                "nx_lms_checked",
                "qc_issue_note",
                "instructor_note",
                "evaluation_status",
                "khoi",
                "lms_deadline",
            ):
                if (
                    cmp_key in existing
                    and cmp_key in incoming
                    and existing[cmp_key] != incoming[cmp_key]
                ):
                    return False
            return True

        def _register_bcnb_row_meta(
            raw_entry: dict[str, Any],
            *,
            default_week: int | None = None,
            default_year: int | None = None,
            source_tag: str = "",
        ) -> None:
            if not isinstance(raw_entry, dict):
                return
            pair = _valid_period_pair(
                raw_entry.get("week", default_week),
                raw_entry.get("year", default_year),
            )
            if pair is None:
                return
            c_code = str(
                raw_entry.get("class_code") or raw_entry.get("ma_lop") or ""
            ).strip().upper()
            if not c_code:
                return
            lesson_str = str(
                raw_entry.get("lesson") or raw_entry.get("buoi_so") or ""
            ).strip()
            if not lesson_str:
                return
            raw_date = (
                raw_entry.get("class_date_iso")
                or raw_entry.get("ngay_hoc_iso")
                or raw_entry.get("class_date")
                or raw_entry.get("ngay_hoc")
                or ""
            )
            date_iso = _normalize_date_iso(raw_date)
            meta: dict[str, Any] = {
                "_lesson": lesson_str,
                "_class_date_iso": date_iso,
                "_sources": {source_tag} if source_tag else set(),
            }
            for k in (
                "bcnb_session_id",
                "khoi",
                "lms_deadline",
                "evaluation_status",
            ):
                v = str(raw_entry.get(k) or "").strip()
                if v:
                    meta[k] = v
            for nk in ("qc_issue_note", "instructor_note"):
                if nk in raw_entry and raw_entry.get(nk) is not None:
                    meta[nk] = str(raw_entry.get(nk) or "").strip()
            for bk in ("nx_zalo_checked", "nx_lms_checked"):
                bv = raw_entry.get(bk)
                if isinstance(bv, bool):
                    meta[bk] = bv
            lesson_list = bcnb_meta_by_lesson_rows.setdefault(
                (pair[0], pair[1], c_code, lesson_str), []
            )
            matched_lesson_entry = next(
                (
                    m
                    for m in lesson_list
                    if _can_merge_bcnb_meta(m, meta, source_tag=source_tag)
                ),
                None,
            )
            if matched_lesson_entry is None:
                lesson_copy = dict(meta)
                lesson_copy["_sources"] = set(meta["_sources"])
                lesson_list.append(lesson_copy)
            else:
                if source_tag:
                    matched_lesson_entry.setdefault("_sources", set()).add(source_tag)
                for mk, mv in meta.items():
                    if mk != "_sources":
                        matched_lesson_entry.setdefault(mk, mv)
            code_list = bcnb_meta_by_code_rows.setdefault(
                (pair[0], pair[1], c_code), []
            )
            matched_code_entry = next(
                (
                    m
                    for m in code_list
                    if _can_merge_bcnb_meta(m, meta, source_tag=source_tag)
                ),
                None,
            )
            if matched_code_entry is None:
                code_copy = dict(meta)
                code_copy["_sources"] = set(meta["_sources"])
                code_list.append(code_copy)
            else:
                if source_tag:
                    matched_code_entry.setdefault("_sources", set()).add(source_tag)
                for mk, mv in meta.items():
                    if mk != "_sources":
                        matched_code_entry.setdefault(mk, mv)

        def _resolve_bcnb_row_fallback(
            *,
            week_num: int,
            year_num: int,
            c_code: str,
            lesson_str: str,
            date_iso: str,
            session_id: str = "",
        ) -> dict[str, Any]:
            if not lesson_str:
                return {}
            candidates = bcnb_meta_by_lesson_rows.get(
                (week_num, year_num, c_code, lesson_str), []
            )
            if not candidates:
                return {}
            sid = str(session_id or "").strip()
            if sid:
                sid_matches = [
                    c
                    for c in candidates
                    if str(c.get("bcnb_session_id") or "").strip() == sid
                    and (
                        not date_iso
                        or not str(c.get("_class_date_iso") or "").strip()
                        or str(c.get("_class_date_iso") or "").strip() == date_iso
                    )
                ]
                if len(sid_matches) == 1:
                    return sid_matches[0]
                if len(sid_matches) > 1:
                    return {}
                if any(str(c.get("bcnb_session_id") or "").strip() for c in candidates):
                    return {}
            if date_iso:
                date_matches = [
                    c
                    for c in candidates
                    if str(c.get("_class_date_iso") or "").strip() == date_iso
                ]
                if len(date_matches) == 1:
                    has_undated_other = any(
                        not str(c.get("_class_date_iso") or "").strip()
                        for c in candidates
                    )
                    if not has_undated_other:
                        return date_matches[0]
                    return {}
                if len(date_matches) > 1:
                    return {}
            if len(candidates) != 1:
                return {}
            only = candidates[0]
            cand_date = str(only.get("_class_date_iso") or "").strip()
            if date_iso and cand_date and cand_date != date_iso:
                return {}
            return only

        available_weeks_set: set[tuple[int, int]] = set()
        for dt_week, dt_year in distinct_target_week_rows:
            dt_pair = _valid_period_pair(dt_week, dt_year)
            if dt_pair is not None:
                available_weeks_set.add(dt_pair)
        if latest_snapshot_row and latest_snapshot_row[0]:
            snap_plan = _parse_json(latest_snapshot_row[0], {})
            if isinstance(snap_plan, dict):
                for tgt_list_key in ("scan_targets", "deferred_targets"):
                    for tgt_entry in snap_plan.get(tgt_list_key) or []:
                        if isinstance(tgt_entry, dict):
                            _register_bcnb_row_meta(
                                tgt_entry,
                                source_tag=f"snap_{tgt_list_key}",
                            )
                bcnb_wp = snap_plan.get("bcnb_week_plan")
                if isinstance(bcnb_wp, dict):
                    req_w = bcnb_wp.get("required_week")
                    if isinstance(req_w, dict):
                        pair = _valid_period_pair(req_w.get("week"), req_w.get("year"))
                        if pair is not None:
                            available_weeks_set.add(pair)
                            for r_entry in bcnb_wp.get("required_data") or []:
                                if isinstance(r_entry, dict):
                                    _register_bcnb_row_meta(
                                        r_entry,
                                        default_week=pair[0],
                                        default_year=pair[1],
                                        source_tag="snap_required_data",
                                    )
                    lat_w = bcnb_wp.get("latest_week")
                    if isinstance(lat_w, dict):
                        l_pair = _valid_period_pair(lat_w.get("week"), lat_w.get("year"))
                        if l_pair is not None:
                            for r_entry in bcnb_wp.get("latest_data") or []:
                                if isinstance(r_entry, dict):
                                    _register_bcnb_row_meta(
                                        r_entry,
                                        default_week=l_pair[0],
                                        default_year=l_pair[1],
                                        source_tag="snap_latest_data",
                                    )

        parsed_zalo_jobs: list[dict[str, Any]] = []
        for row in zalo_job_rows:
            j_id = int(row[0])
            j_status = str(row[2] or "")
            j_created = str(row[3] or "") or None
            j_started = str(row[4] or "") or None
            j_finished = str(row[5] or "") or None
            j_timestamp = j_finished or j_started or j_created or ""
            raw_results = _parse_json(row[6], None)
            req_payload = _parse_json(row[7], {})
            mutates_ext = bool(row[8])

            envelope = raw_results if isinstance(raw_results, dict) else {}
            if isinstance(raw_results, list):
                raw_items = [it for it in raw_results if isinstance(it, dict)]
            elif isinstance(raw_results, dict) and isinstance(
                raw_results.get("items"), list
            ):
                raw_items = [
                    it for it in raw_results["items"] if isinstance(it, dict)
                ]
            else:
                raw_items = []

            if not raw_items and j_id in batch_targets_by_job:
                raw_items = batch_targets_by_job[j_id]

            scan_plan_raw = (
                req_payload.get("scan_plan") if isinstance(req_payload, dict) else None
            )
            scan_plan: dict[str, Any] = (
                scan_plan_raw if isinstance(scan_plan_raw, dict) else {}
            )
            for sp_list_key in ("targets", "deferred_targets"):
                for sp_tgt in scan_plan.get(sp_list_key) or []:
                    if isinstance(sp_tgt, dict):
                        _register_bcnb_row_meta(
                            sp_tgt,
                            source_tag=f"job_{j_id}_{sp_list_key}",
                        )
            selective_rescan = bool(
                envelope.get("selective_rescan")
                or scan_plan.get("selective_rescan")
            )
            snapshot_id = (
                envelope.get("snapshot_id")
                if envelope.get("snapshot_id") is not None
                else scan_plan.get("snapshot_id")
            )
            exec_mode = (
                str(req_payload.get("execution_mode"))
                if isinstance(req_payload, dict)
                and req_payload.get("execution_mode") in {"dry-run", "production"}
                else ("production" if mutates_ext else "dry-run")
            )

            declared_periods: set[tuple[int, int]] = set()
            req_week_pair: tuple[int, int] | None = None
            for req_candidate in (
                envelope.get("required_week"),
                scan_plan.get("required_week"),
            ):
                if isinstance(req_candidate, dict):
                    pair = _valid_period_pair(
                        req_candidate.get("week"), req_candidate.get("year")
                    )
                    if pair is not None:
                        declared_periods.add(pair)
                        if req_week_pair is None:
                            req_week_pair = pair
            for sel_list in (
                envelope.get("selected_weeks"),
                scan_plan.get("selected_weeks"),
            ):
                if isinstance(sel_list, list):
                    for sel_item in sel_list:
                        if isinstance(sel_item, dict):
                            pair = _valid_period_pair(
                                sel_item.get("week"), sel_item.get("year")
                            )
                            if pair is not None:
                                declared_periods.add(pair)

            items_by_period: dict[tuple[int, int], list[dict[str, Any]]] = {}
            for item in raw_items:
                period = _extract_item_period(item, default_period=req_week_pair)
                if period is None:
                    continue
                items_by_period.setdefault(period, []).append(item)
                declared_periods.add(period)

            for period in items_by_period:
                available_weeks_set.add(period)
            if req_week_pair is not None:
                available_weeks_set.add(req_week_pair)

            parsed_zalo_jobs.append(
                {
                    "job_id": j_id,
                    "status": j_status,
                    "execution_mode": exec_mode,
                    "selective_rescan": selective_rescan,
                    "snapshot_id": snapshot_id,
                    "created_at": j_created,
                    "started_at": j_started,
                    "finished_at": j_finished,
                    "timestamp": j_timestamp,
                    "required_week": req_week_pair,
                    "declared_periods": declared_periods,
                    "items_by_period": items_by_period,
                }
            )

        # Parse LMS plans and executions by (week, year)
        lms_previews_by_period: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for a_job_id, plan_raw, c_at, cons_at, exec_job_id, _ in lms_conf_rows:
            plan = _parse_json(plan_raw, None)
            if not isinstance(plan, dict):
                continue
            pair = _valid_period_pair(plan.get("week"), plan.get("year"))
            if pair is None:
                continue
            available_weeks_set.add(pair)
            lms_previews_by_period.setdefault(pair, []).append(
                {
                    "analysis_job_id": int(a_job_id),
                    "execution_job_id": int(exec_job_id) if exec_job_id else None,
                    "plan": plan,
                    "timestamp": str(cons_at or c_at or ""),
                }
            )

        lms_executions_by_period: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for row in lms_job_rows:
            l_id = int(row[0])
            l_type = str(row[1] or "")
            l_status = str(row[2] or "")
            l_created = str(row[3] or "")
            l_finished = str(row[5] or "")
            l_timestamp = l_finished or l_created
            l_res = _parse_json(row[6], None)
            l_req = _parse_json(row[7], {})
            if l_type == "LMS_ANALYSIS" and isinstance(l_res, dict):
                pair = _valid_period_pair(l_res.get("week"), l_res.get("year"))
                if pair is not None and l_status == "SUCCESS":
                    available_weeks_set.add(pair)
                    existing_ids = {
                        p["analysis_job_id"]
                        for p in lms_previews_by_period.get(pair, [])
                    }
                    if l_id not in existing_ids:
                        lms_previews_by_period.setdefault(pair, []).append(
                            {
                                "analysis_job_id": l_id,
                                "execution_job_id": None,
                                "plan": l_res,
                                "timestamp": l_timestamp,
                            }
                        )
            elif l_type == "LMS_AUTOMATION":
                source_obj = (
                    l_res
                    if isinstance(l_res, dict)
                    else (l_req if isinstance(l_req, dict) else {})
                )
                pair = _valid_period_pair(
                    source_obj.get("week"), source_obj.get("year")
                )
                if pair is not None and isinstance(l_res, dict):
                    available_weeks_set.add(pair)
                    lms_executions_by_period.setdefault(pair, []).append(
                        {
                            "execution_job_id": l_id,
                            "status": l_status,
                            "dry_run": bool(l_res.get("dry_run", True)),
                            "result": l_res,
                            "timestamp": l_timestamp,
                        }
                    )

        lms_audit_by_period: dict[tuple[int, int], dict[str, dict[str, str]]] = {}
        for a_job_id, target_id, mode, reason_raw, outcome, comp_at, c_at in lms_audit_rows:
            reason = _parse_json(reason_raw, {})
            if not isinstance(reason, dict):
                continue
            pair = _valid_period_pair(reason.get("week"), reason.get("year"))
            if pair is None:
                continue
            code = str(target_id or "").strip().upper()
            if not code:
                continue
            available_weeks_set.add(pair)
            period_map = lms_audit_by_period.setdefault(pair, {})
            if code not in period_map or (
                outcome == "VERIFIED"
                and mode == "production"
                and period_map[code].get("outcome") != "VERIFIED"
            ):
                period_map[code] = {
                    "outcome": str(outcome or ""),
                    "mode": str(mode or ""),
                    "timestamp": str(comp_at or c_at or ""),
                }

        manual_approvals_by_period: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}
        for (
            ma_job_id,
            ma_target_id,
            ma_mode,
            ma_reason_raw,
            ma_outcome,
            ma_ev_path,
            ma_ev_sha,
            ma_comp_at,
            ma_c_at,
        ) in manual_approve_audit_rows:
            if str(ma_mode or "") != "production" or str(ma_outcome or "") != "VERIFIED":
                continue
            reason = _parse_json(ma_reason_raw, {})
            if not isinstance(reason, dict):
                continue
            pair = _valid_period_pair(reason.get("week"), reason.get("year"))
            if pair is None:
                continue
            class_code = str(reason.get("class_code") or ma_target_id or "").strip().upper()
            lesson = str(reason.get("lesson") or "").strip()
            class_date_iso = _normalize_date_iso(
                reason.get("class_date_iso") or reason.get("class_date")
            )
            r_key = ""
            if class_code and lesson and class_date_iso:
                try:
                    r_key = canonical_target_key(
                        {
                            "week": pair[0],
                            "year": pair[1],
                            "class_code": class_code,
                            "lesson": lesson,
                            "class_date_iso": class_date_iso,
                        }
                    )
                except ValueError:
                    r_key = ""
            if not r_key:
                r_key = str(reason.get("target_key") or "").strip()
            if not r_key:
                continue
            available_weeks_set.add(pair)
            ma_sid = str(reason.get("bcnb_session_id") or "").strip()
            ma_key = f"{r_key}|sid:{ma_sid}" if ma_sid else r_key
            period_ma = manual_approvals_by_period.setdefault(pair, {})
            if ma_key not in period_ma:
                src_j = reason.get("source_job_id")
                period_ma[ma_key] = {
                    "target_key": r_key,
                    "bcnb_session_id": ma_sid,
                    "class_code": class_code,
                    "group_name": str(reason.get("group_name") or "").strip(),
                    "week": pair[0],
                    "year": pair[1],
                    "lesson": lesson,
                    "class_date_iso": class_date_iso,
                    "approved_by": str(reason.get("approved_by") or "operator").strip()
                    or "operator",
                    "approved_at": str(ma_comp_at or ma_c_at or ""),
                    "approval_job_id": int(ma_job_id) if ma_job_id else None,
                    "source_job_id": (
                        int(src_j)
                        if isinstance(src_j, int)
                        and not isinstance(src_j, bool)
                        and int(src_j) > 0
                        else None
                    ),
                    "evidence_path": str(
                        ma_ev_path or reason.get("evidence_path") or ""
                    ).strip(),
                    "evidence_sha256": str(
                        ma_ev_sha or reason.get("evidence_sha256") or ""
                    ).strip(),
                    "outcome": "VERIFIED",
                    "dry_run": False,
                    "approved_item": None,
                }

        sorted_weeks = sorted(
            available_weeks_set,
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )
        available_weeks = [
            {
                "week": w,
                "year": y,
                "period_key": f"{y:04d}-W{w:02d}",
                "label": f"W{w:02d}/{y}",
            }
            for w, y in sorted_weeks
        ]

        selected_period: tuple[int, int] | None = None
        if week is not None and year is not None:
            selected_period = (int(week), int(year))
        elif job_id is not None:
            target_job = next(
                (j for j in parsed_zalo_jobs if j["job_id"] == int(job_id)),
                None,
            )
            if target_job is not None:
                if target_job["required_week"] is not None:
                    selected_period = target_job["required_week"]
                elif len(target_job["items_by_period"]) == 1:
                    selected_period = next(iter(target_job["items_by_period"].keys()))
                elif len(target_job["declared_periods"]) == 1:
                    selected_period = next(iter(target_job["declared_periods"]))
            if selected_period is None:
                raise WeeklySummaryError(
                    "Không xác định được tuần của job đã chọn; hãy truyền thêm week và year.",
                    code="INVALID_PERIOD",
                    status_code=422,
                )
        elif sorted_weeks:
            selected_period = sorted_weeks[0]

        if selected_period is None:
            return {
                "week": None,
                "year": None,
                "period_key": None,
                "selected_job_id": None,
                "snapshot_id": None,
                "is_selective_only": False,
                "available_weeks": available_weeks,
                "jobs": [],
                "summary": {
                    "total": 0,
                    "zalo_completed": 0,
                    "zalo_exempt": 0,
                    "zalo_late": 0,
                    "zalo_pending": 0,
                    "zalo_unscanned": 0,
                    "lms_completed": 0,
                    "lms_dry_run": 0,
                    "lms_pending_tick": 0,
                    "lms_overdue": 0,
                    "lms_unscanned": 0,
                },
                "lms_summary": {
                    "has_lms_data": False,
                    "analysis_job_id": None,
                    "execution_job_id": None,
                    "missing_count": 0,
                    "ticked_count": 0,
                    "dry_run_count": 0,
                    "pending_tick_count": 0,
                    "already_checked_count": 0,
                },
                "rows": [],
                "items": [],
            }

        sel_week, sel_year = selected_period
        period_key = f"{sel_year:04d}-W{sel_week:02d}"

        # Filter historical ZALO_SCAN jobs relevant to (sel_week, sel_year)
        jobs_for_week: list[dict[str, Any]] = []
        matching_jobs_chronological: list[dict[str, Any]] = []
        for j in parsed_zalo_jobs:
            period_items = j["items_by_period"].get(selected_period, [])
            is_for_period = bool(period_items) or (
                selected_period in j["declared_periods"]
            )
            if not is_for_period:
                continue
            jobs_for_week.append(
                {
                    "job_id": j["job_id"],
                    "status": j["status"],
                    "execution_mode": j["execution_mode"],
                    "selective_rescan": j["selective_rescan"],
                    "snapshot_id": j["snapshot_id"],
                    "created_at": j["created_at"],
                    "finished_at": j["finished_at"],
                    "target_count": len(period_items),
                    "weeks": [
                        {"week": pw, "year": py}
                        for pw, py in sorted(
                            j["declared_periods"],
                            key=lambda p: (p[1], p[0]),
                            reverse=True,
                        )
                    ],
                }
            )
            if period_items:
                matching_jobs_chronological.append(j)

        jobs_for_week = jobs_for_week[:200]
        matching_jobs_chronological.sort(key=lambda j: j["job_id"])

        job_snapshot_by_id: dict[int, Any] = {
            int(j["job_id"]): j["snapshot_id"] for j in parsed_zalo_jobs
        }
        period_manual_approvals: dict[str, dict[str, Any]] = {}
        for k, v in manual_approvals_by_period.get(selected_period, {}).items():
            v_copy = dict(v)
            src_j_id = v_copy.get("source_job_id")
            v_copy["snapshot_id"] = (
                job_snapshot_by_id.get(int(src_j_id))
                if isinstance(src_j_id, int)
                and not isinstance(src_j_id, bool)
                and int(src_j_id) in job_snapshot_by_id
                else None
            )
            period_manual_approvals[k] = v_copy

        def _item_session_id(candidate_item: dict[str, Any]) -> str:
            st_cand = candidate_item.get("scan_target")
            st_cand_dict = st_cand if isinstance(st_cand, dict) else {}
            ma_cand = candidate_item.get("manual_approval")
            ma_cand_dict = ma_cand if isinstance(ma_cand, dict) else {}
            return _first_defined_str(
                ma_cand_dict, candidate_item, st_cand_dict, key="bcnb_session_id"
            )

        def _approval_session_id(app_info: dict[str, Any]) -> str:
            direct = str(app_info.get("bcnb_session_id") or "").strip()
            if direct:
                return direct
            app_item = app_info.get("approved_item")
            if isinstance(app_item, dict):
                return _item_session_id(app_item)
            return ""

        def _manual_approval_matches_item(
            app_info: dict[str, Any],
            candidate_item: dict[str, Any],
            *,
            same_row_key_items: list[dict[str, Any]],
        ) -> bool:
            app_sid = _approval_session_id(app_info)
            cand_sid = _item_session_id(candidate_item)
            if app_sid and cand_sid:
                if app_sid != cand_sid:
                    return False
                same_sid_count = sum(
                    1 for it in same_row_key_items if _item_session_id(it) == app_sid
                )
                return same_sid_count == 1
            if app_sid and not cand_sid:
                return False
            if not app_sid and cand_sid:
                return False
            return len(same_row_key_items) == 1

        for j in matching_jobs_chronological:
            job_period_items = j["items_by_period"].get(selected_period, [])
            for item in job_period_items:
                r_key = _extract_item_row_key(item, sel_week, sel_year)
                item_sid = _item_session_id(item)
                ma_lookup_key = f"{r_key}|sid:{item_sid}" if item_sid else r_key
                for cand_ma_key in (ma_lookup_key, r_key):
                    if (
                        cand_ma_key in period_manual_approvals
                        and period_manual_approvals[cand_ma_key].get("approved_item")
                        is None
                    ):
                        existing_app = period_manual_approvals[cand_ma_key]
                        ex_app_sid = str(
                            existing_app.get("bcnb_session_id") or ""
                        ).strip()
                        if ex_app_sid and item_sid and ex_app_sid != item_sid:
                            continue
                        if ex_app_sid and not item_sid:
                            continue
                        same_key_in_job = [
                            it
                            for it in job_period_items
                            if _extract_item_row_key(it, sel_week, sel_year) == r_key
                        ]
                        if not ex_app_sid and len(same_key_in_job) != 1:
                            continue
                        src_candidate = existing_app.get("source_job_id")
                        if src_candidate is None or int(src_candidate) == int(
                            j["job_id"]
                        ):
                            existing_app["approved_item"] = item
                            if item_sid and not ex_app_sid:
                                existing_app["bcnb_session_id"] = item_sid
                            if (
                                src_candidate is not None
                                and int(src_candidate) == int(j["job_id"])
                                and existing_app.get("snapshot_id") is None
                            ):
                                existing_app["snapshot_id"] = j["snapshot_id"]
                if not _is_verified_manual_approved_item(item):
                    continue
                ma_meta = (
                    item.get("manual_approval")
                    if isinstance(item.get("manual_approval"), dict)
                    else {}
                )
                st_raw = item.get("scan_target")
                st_dict = st_raw if isinstance(st_raw, dict) else {}
                src_j = (
                    ma_meta.get("source_job_id")
                    or item.get("source_job_id")
                    or j["job_id"]
                )
                resolved_src_j = (
                    int(src_j)
                    if isinstance(src_j, int)
                    and not isinstance(src_j, bool)
                    and int(src_j) > 0
                    else j["job_id"]
                )
                ma_store_key = f"{r_key}|sid:{item_sid}" if item_sid else r_key
                period_manual_approvals[ma_store_key] = {
                    "target_key": r_key,
                    "bcnb_session_id": item_sid,
                    "class_code": _extract_item_class_code(item),
                    "group_name": str(
                        item.get("group") or st_dict.get("group_name") or ""
                    ).strip(),
                    "week": sel_week,
                    "year": sel_year,
                    "lesson": str(st_dict.get("lesson") or "").strip(),
                    "class_date_iso": _normalize_date_iso(
                        st_dict.get("class_date_iso") or st_dict.get("class_date")
                    ),
                    "approved_by": str(ma_meta.get("approved_by") or "operator").strip()
                    or "operator",
                    "approved_at": str(
                        ma_meta.get("approved_at")
                        or item.get("timestamp")
                        or j["timestamp"]
                        or ""
                    ).strip(),
                    "approval_job_id": ma_meta.get("approval_job_id"),
                    "source_job_id": resolved_src_j,
                    "snapshot_id": job_snapshot_by_id.get(
                        resolved_src_j, j["snapshot_id"]
                    ),
                    "evidence_path": str(ma_meta.get("evidence_path") or "").strip(),
                    "evidence_sha256": str(ma_meta.get("evidence_sha256") or "").strip(),
                    "outcome": "VERIFIED",
                    "dry_run": False,
                    "approved_item": item,
                }

        selected_job_id: int | None = None
        selected_snapshot_id: Any = None
        is_selective_only: bool = False
        selected_raw_items: list[tuple[int, str, dict[str, Any]]] = []
        if job_id is not None:
            target_job = next(
                (j for j in parsed_zalo_jobs if j["job_id"] == int(job_id)),
                None,
            )
            assert target_job is not None
            period_items = target_job["items_by_period"].get(selected_period, [])
            if not period_items and selected_period not in target_job["declared_periods"]:
                raise WeeklySummaryError(
                    "Job được chọn không có dữ liệu của tuần đang tra cứu.",
                    code="JOB_WEEK_MISMATCH",
                    status_code=409,
                )
            selected_job_id = int(job_id)
            selected_snapshot_id = target_job["snapshot_id"]
            is_selective_only = bool(target_job["selective_rescan"])
            for item in period_items:
                r_key = _extract_item_row_key(item, sel_week, sel_year)
                same_r_key_items = [
                    it
                    for it in period_items
                    if _extract_item_row_key(it, sel_week, sel_year) == r_key
                ]
                item_with_snap = dict(item)
                item_with_snap["_origin_snapshot_id"] = target_job["snapshot_id"]
                entry = (target_job["job_id"], target_job["timestamp"], item_with_snap)
                matching_apps = [
                    app_info
                    for app_info in period_manual_approvals.values()
                    if str(app_info.get("target_key") or "") == r_key
                    and app_info.get("source_job_id") in {None, int(job_id)}
                    and _manual_approval_matches_item(
                        app_info,
                        item_with_snap,
                        same_row_key_items=same_r_key_items,
                    )
                ]
                if len(matching_apps) == 1:
                    app_info = matching_apps[0]
                    if "Miễn" not in str(item_with_snap.get("status") or "") and (
                        "✅" not in str(item_with_snap.get("status") or "")
                        or bool(item_with_snap.get("manual_approved"))
                    ):
                        entry = _apply_verified_manual_approval(
                            entry[0],
                            entry[1],
                            entry[2],
                            app_info,
                            week=sel_week,
                            year=sel_year,
                        )
                selected_raw_items.append(entry)
        elif matching_jobs_chronological:
            # Enforce same-snapshot boundary when merging selective rescans onto a baseline
            latest_job = matching_jobs_chronological[-1]
            active_snapshot_id = latest_job["snapshot_id"]
            if not latest_job["selective_rescan"] or active_snapshot_id is None:
                jobs_to_merge = [latest_job]
            else:
                contiguous_same_snapshot: list[dict[str, Any]] = []
                for j in reversed(matching_jobs_chronological):
                    if j["snapshot_id"] != active_snapshot_id:
                        break
                    contiguous_same_snapshot.append(j)
                    if not j["selective_rescan"] and j["status"] in {
                        "SUCCESS",
                        "PARTIAL_SUCCESS",
                    }:
                        break
                contiguous_same_snapshot.reverse()
                jobs_to_merge = contiguous_same_snapshot

            has_full_baseline = any(
                not j["selective_rescan"]
                and j["status"] in {"SUCCESS", "PARTIAL_SUCCESS"}
                for j in jobs_to_merge
            )
            selected_snapshot_id = active_snapshot_id
            is_selective_only = not has_full_baseline

            merged_by_key: dict[str, tuple[int, str, dict[str, Any]]] = {}
            ordered_keys: list[str] = []
            dup_counter = 0
            for j in jobs_to_merge:
                selected_job_id = j["job_id"]
                job_items = j["items_by_period"].get(selected_period, [])
                r_key_counts_in_job: dict[str, int] = {}
                for it in job_items:
                    rk = _extract_item_row_key(it, sel_week, sel_year)
                    r_key_counts_in_job[rk] = r_key_counts_in_job.get(rk, 0) + 1
                for item in job_items:
                    r_key = _extract_item_row_key(item, sel_week, sel_year)
                    item_sid = _item_session_id(item)
                    merge_key = f"{r_key}|sid:{item_sid}" if item_sid else r_key
                    if merge_key in merged_by_key:
                        prev_job_id, _, prev_item = merged_by_key[merge_key]
                        prev_sid = _item_session_id(prev_item)
                        if (
                            prev_job_id == j["job_id"]
                            or item_sid != prev_sid
                            or r_key_counts_in_job.get(r_key, 0) > 1
                        ):
                            dup_counter += 1
                            merge_key = f"{merge_key}#dup{dup_counter}"
                    elif r_key in merged_by_key:
                        prev_job_id, _, prev_item = merged_by_key[r_key]
                        prev_sid = _item_session_id(prev_item)
                        if (
                            prev_job_id != j["job_id"]
                            and item_sid == prev_sid
                            and r_key_counts_in_job.get(r_key, 0) == 1
                        ):
                            merge_key = r_key
                    if merge_key in merged_by_key:
                        _, _, prev_item = merged_by_key[merge_key]
                        if (
                            _is_halted_unscanned_item(item)
                            and not _is_halted_unscanned_item(prev_item)
                        ):
                            continue
                    else:
                        ordered_keys.append(merge_key)
                    item_with_snap = dict(item)
                    item_with_snap["_origin_snapshot_id"] = j["snapshot_id"]
                    merged_by_key[merge_key] = (
                        j["job_id"],
                        j["timestamp"],
                        item_with_snap,
                    )

            # Reconcile verified production manual approvals across all scans of the week
            for ma_key, app_info in period_manual_approvals.items():
                r_key = str(app_info.get("target_key") or ma_key)
                r_key_merge_keys = [
                    mk
                    for mk in ordered_keys
                    if _extract_item_row_key(merged_by_key[mk][2], sel_week, sel_year)
                    == r_key
                ]
                app_sid = _approval_session_id(app_info)
                if r_key_merge_keys:
                    same_r_key_items = [
                        merged_by_key[mk][2] for mk in r_key_merge_keys
                    ]
                    compatible_mks = [
                        mk
                        for mk in r_key_merge_keys
                        if _manual_approval_matches_item(
                            app_info,
                            merged_by_key[mk][2],
                            same_row_key_items=same_r_key_items,
                        )
                    ]
                    if len(compatible_mks) == 1:
                        target_mk = compatible_mks[0]
                        cur_job_id, cur_ts, cur_item = merged_by_key[target_mk]
                        if "Miễn" not in str(cur_item.get("status") or "") and (
                            "✅" not in str(cur_item.get("status") or "")
                            or bool(cur_item.get("manual_approved"))
                        ):
                            merged_by_key[target_mk] = _apply_verified_manual_approval(
                                cur_job_id,
                                cur_ts,
                                cur_item,
                                app_info,
                                week=sel_week,
                                year=sel_year,
                            )
                else:
                    base_item_raw = app_info.get("approved_item")
                    if isinstance(base_item_raw, dict):
                        base_item = dict(base_item_raw)
                        base_st_raw = base_item.get("scan_target")
                        st_copy = dict(base_st_raw) if isinstance(base_st_raw, dict) else {}
                        if app_sid:
                            base_item.setdefault("bcnb_session_id", app_sid)
                            st_copy.setdefault("bcnb_session_id", app_sid)
                        base_item["scan_target"] = st_copy
                    else:
                        c_code = app_info.get("class_code") or "KHONG_RO_LOP"
                        g_name = app_info.get("group_name") or c_code
                        person_name = teacher_by_class_code.get(
                            c_code, "Chưa phân công"
                        )
                        base_item = {
                            "group": g_name,
                            "person": person_name,
                            **(
                                {"bcnb_session_id": app_sid}
                                if app_sid
                                else {}
                            ),
                            "scan_target": {
                                "group_name": g_name,
                                "person": person_name,
                                "class_code": c_code,
                                "week": sel_week,
                                "year": sel_year,
                                "lesson": app_info.get("lesson") or "—",
                                "class_date": app_info.get("class_date_iso") or "—",
                                "class_date_iso": app_info.get("class_date_iso") or "",
                                **(
                                    {"bcnb_session_id": app_sid}
                                    if app_sid
                                    else {}
                                ),
                            },
                        }
                    app_snapshot_id = app_info.get("snapshot_id")
                    base_item["_origin_snapshot_id"] = app_snapshot_id
                    if app_snapshot_id is None or app_snapshot_id != active_snapshot_id:
                        selected_snapshot_id = None
                    fallback_job_id = int(
                        app_info.get("source_job_id")
                        or selected_job_id
                        or 0
                    )
                    synth_mk = ma_key if ma_key not in merged_by_key else f"{ma_key}#ma"
                    ordered_keys.append(synth_mk)
                    merged_by_key[synth_mk] = _apply_verified_manual_approval(
                        fallback_job_id,
                        str(app_info.get("approved_at") or ""),
                        base_item,
                        app_info,
                        week=sel_week,
                        year=sel_year,
                    )

            selected_raw_items = [merged_by_key[k] for k in ordered_keys]
        elif period_manual_approvals:
            for r_key, app_info in period_manual_approvals.items():
                app_sid = _approval_session_id(app_info)
                base_item_raw = app_info.get("approved_item")
                if isinstance(base_item_raw, dict):
                    base_item = dict(base_item_raw)
                    base_st_raw = base_item.get("scan_target")
                    st_copy = dict(base_st_raw) if isinstance(base_st_raw, dict) else {}
                    if app_sid:
                        base_item.setdefault("bcnb_session_id", app_sid)
                        st_copy.setdefault("bcnb_session_id", app_sid)
                    base_item["scan_target"] = st_copy
                    base_item["_origin_snapshot_id"] = app_info.get("snapshot_id")
                else:
                    c_code = app_info.get("class_code") or "KHONG_RO_LOP"
                    g_name = app_info.get("group_name") or c_code
                    person_name = teacher_by_class_code.get(c_code, "Chưa phân công")
                    base_item = {
                        "group": g_name,
                        "person": person_name,
                        "_origin_snapshot_id": app_info.get("snapshot_id"),
                        **(
                            {"bcnb_session_id": app_sid}
                            if app_sid
                            else {}
                        ),
                        "scan_target": {
                            "group_name": g_name,
                            "person": person_name,
                            "class_code": c_code,
                            "week": sel_week,
                            "year": sel_year,
                            "lesson": app_info.get("lesson") or "—",
                            "class_date": app_info.get("class_date_iso") or "—",
                            "class_date_iso": app_info.get("class_date_iso") or "",
                            **(
                                {"bcnb_session_id": app_sid}
                                if app_sid
                                else {}
                            ),
                        },
                    }
                fallback_job_id = int(app_info.get("source_job_id") or 0)
                if selected_job_id is None and fallback_job_id > 0:
                    selected_job_id = fallback_job_id
                selected_raw_items.append(
                    _apply_verified_manual_approval(
                        fallback_job_id,
                        str(app_info.get("approved_at") or ""),
                        base_item,
                        app_info,
                        week=sel_week,
                        year=sel_year,
                    )
                )

        # Resolve LMS status per class_code for (sel_week, sel_year)
        previews = sorted(
            lms_previews_by_period.get(selected_period, []),
            key=lambda x: x["analysis_job_id"],
            reverse=True,
        )
        executions = sorted(
            lms_executions_by_period.get(selected_period, []),
            key=lambda x: x["execution_job_id"],
            reverse=True,
        )
        audits = lms_audit_by_period.get(selected_period, {})

        latest_preview = previews[0] if previews else None
        latest_exec = executions[0] if executions else None

        lms_status_by_class: dict[str, str] = {}
        lms_verified_at_by_class: dict[str, str] = {}
        bcnb_lms_classes_in_week: list[str] = []

        if latest_preview is not None:
            p_plan = latest_preview["plan"]
            p_time = latest_preview["timestamp"]
            overdue_details = (
                p_plan.get("overdue_details")
                if isinstance(p_plan.get("overdue_details"), dict)
                else {}
            )
            unmatched_set = {
                str(c).strip().upper()
                for c in (p_plan.get("unmatched_classes") or [])
                if str(c).strip()
            }
            missing_checked_set = {
                str(c).strip().upper()
                for c in (p_plan.get("missing_but_checked_classes") or [])
                if str(c).strip()
            }
            for raw_code in p_plan.get("already_checked_classes") or []:
                code = str(raw_code).strip().upper()
                if not code:
                    continue
                if code not in bcnb_lms_classes_in_week:
                    bcnb_lms_classes_in_week.append(code)
                lms_status_by_class[code] = "✅ Đã tích trên BCNB"
                lms_verified_at_by_class[code] = p_time
            for raw_code in p_plan.get("classes_to_tick") or []:
                code = str(raw_code).strip().upper()
                if not code:
                    continue
                if code not in bcnb_lms_classes_in_week:
                    bcnb_lms_classes_in_week.append(code)
                lms_status_by_class[code] = "⏳ Đã nhận xét (Chờ tích BCNB)"
                lms_verified_at_by_class[code] = p_time
            for raw_code in p_plan.get("missing_classes") or []:
                code = str(raw_code).strip().upper()
                if not code:
                    continue
                if code not in unmatched_set and code not in bcnb_lms_classes_in_week:
                    bcnb_lms_classes_in_week.append(code)
                detail = str(overdue_details.get(code) or "").strip()
                if code in missing_checked_set:
                    lms_status_by_class[code] = (
                        f"⚠️ Chưa nhận xét ({detail}) - BCNB đã tích"
                        if detail
                        else "⚠️ Chưa nhận xét - BCNB đã tích"
                    )
                else:
                    lms_status_by_class[code] = (
                        f"❌ Chưa nhận xét ({detail})"
                        if detail
                        else "❌ Chưa nhận xét"
                    )
                lms_verified_at_by_class[code] = p_time

        if latest_exec is not None and (
            latest_preview is None
            or latest_exec["execution_job_id"] > latest_preview["analysis_job_id"]
        ):
            e_res = latest_exec["result"]
            e_time = latest_exec["timestamp"]
            e_dry_run = bool(latest_exec["dry_run"])
            for raw_code in e_res.get("verified_classes") or []:
                code = str(raw_code).strip().upper()
                if not code:
                    continue
                if code not in bcnb_lms_classes_in_week:
                    bcnb_lms_classes_in_week.append(code)
                lms_status_by_class[code] = (
                    "🧪 Đã nhận xét (Dry-run LMS)"
                    if e_dry_run
                    else "✅ Đã nhận xét & Tích BCNB"
                )
                lms_verified_at_by_class[code] = e_time
            for f_item in e_res.get("failed_classes") or []:
                code = (
                    str(f_item.get("class_code") or "").strip().upper()
                    if isinstance(f_item, dict)
                    else str(f_item or "").strip().upper()
                )
                if not code:
                    continue
                if code not in bcnb_lms_classes_in_week:
                    bcnb_lms_classes_in_week.append(code)
                lms_status_by_class[code] = "⚠️ Lỗi tích BCNB LMS"
                lms_verified_at_by_class[code] = e_time

        for code, audit_info in audits.items():
            if (
                audit_info.get("outcome") == "VERIFIED"
                and audit_info.get("mode") == "production"
            ):
                if code not in bcnb_lms_classes_in_week:
                    bcnb_lms_classes_in_week.append(code)
                if not lms_status_by_class.get(code, "").startswith(("❌", "⚠️")):
                    lms_status_by_class[code] = "✅ Đã nhận xét & Tích BCNB"
                    if audit_info.get("timestamp"):
                        lms_verified_at_by_class[code] = audit_info["timestamp"]

        rows: list[dict[str, Any]] = []
        sanitized_items: list[dict[str, Any]] = []
        seen_class_codes: set[str] = set()

        for src_job_id, job_ts, item in selected_raw_items:
            class_code = _extract_item_class_code(item)
            seen_class_codes.add(class_code)
            raw_st_obj = item.get("scan_target")
            raw_st: dict[str, Any] = (
                raw_st_obj if isinstance(raw_st_obj, dict) else {}
            )
            raw_ev = item.get("evidence")
            raw_ctx_obj = (
                raw_ev.get("date_context") if isinstance(raw_ev, dict) else None
            )
            raw_ctx: dict[str, Any] = (
                raw_ctx_obj if isinstance(raw_ctx_obj, dict) else {}
            )
            item_lesson = str(
                raw_st.get("lesson")
                or raw_ctx.get("target_lesson")
                or item.get("lesson")
                or ""
            ).strip()
            item_date_iso = _normalize_date_iso(
                raw_st.get("class_date_iso")
                or raw_st.get("class_date")
                or raw_ctx.get("target_class_date")
                or item.get("class_date_iso")
                or item.get("class_date")
                or ""
            )
            item_session_id = _first_defined_str(
                item, raw_st, key="bcnb_session_id"
            )
            fallback_meta = _resolve_bcnb_row_fallback(
                week_num=sel_week,
                year_num=sel_year,
                c_code=class_code,
                lesson_str=item_lesson,
                date_iso=item_date_iso,
                session_id=item_session_id,
            )
            lms_status = lms_status_by_class.get(class_code, "Chưa quét LMS")
            sanitized = _sanitize_dashboard_item(
                item,
                week=sel_week,
                year=sel_year,
                source_job_id=src_job_id,
                lms_status=lms_status,
                job_timestamp=job_ts,
                fallback_bcnb_meta=fallback_meta,
            )
            resolved_lms_status = str(sanitized.get("lms_status") or lms_status)
            sanitized_items.append(sanitized)
            st = sanitized["scan_target"]
            manual_app = item.get("manual_approval")
            verified_at = str(
                (
                    manual_app.get("approved_at")
                    if isinstance(manual_app, dict)
                    else None
                )
                or sanitized["evidence"].get("scanned_at")
                or job_ts
                or sanitized.get("timestamp")
                or "—"
            ).strip()
            rows.append(
                {
                    "class_code": class_code,
                    "teacher": str(sanitized.get("person") or "Chưa phân công").strip()
                    or "Chưa phân công",
                    "lesson": str(st.get("lesson") or "—").strip() or "—",
                    "class_date": str(
                        st.get("class_date") or st.get("class_date_iso") or "—"
                    ).strip()
                    or "—",
                    "zalo_status": str(sanitized.get("status") or "Chưa xác định").strip(),
                    "lms_status": resolved_lms_status,
                    "evaluation_status": str(sanitized.get("evaluation_status") or ""),
                    "issue_description": str(sanitized.get("issue_description") or ""),
                    "qc_issue_note": str(sanitized.get("qc_issue_note") or ""),
                    "instructor_note": str(sanitized.get("instructor_note") or ""),
                    "khoi": str(sanitized.get("khoi") or ""),
                    "bcnb_session_id": str(sanitized.get("bcnb_session_id") or ""),
                    "verified_at": verified_at,
                }
            )

        if job_id is None:
            for code in bcnb_lms_classes_in_week:
                if code in seen_class_codes:
                    continue
                seen_class_codes.add(code)
                lms_status = lms_status_by_class.get(code, "Chưa quét LMS")
                lms_ts = lms_verified_at_by_class.get(code) or "—"
                teacher = teacher_by_class_code.get(code, "Chưa phân công")
                code_candidates = bcnb_meta_by_code_rows.get(
                    (sel_week, sel_year, code), []
                )
                fb_meta = code_candidates[0] if len(code_candidates) == 1 else {}
                eval_status = _resolve_row_evaluation_status(
                    zalo_status="Chưa quét Zalo",
                    lms_status=lms_status,
                    nx_zalo_checked=fb_meta.get("nx_zalo_checked"),
                    nx_lms_checked=fb_meta.get("nx_lms_checked"),
                    raw_evaluation_status=str(fb_meta.get("evaluation_status") or ""),
                )
                rows.append(
                    {
                        "class_code": code,
                        "teacher": teacher,
                        "lesson": "—",
                        "class_date": "—",
                        "zalo_status": "Chưa quét Zalo",
                        "lms_status": lms_status,
                        "evaluation_status": eval_status,
                        "issue_description": _resolve_row_issue_description(
                            fb_meta, st=fb_meta, fb=fb_meta
                        ),
                        "qc_issue_note": str(fb_meta.get("qc_issue_note") or ""),
                        "instructor_note": str(fb_meta.get("instructor_note") or ""),
                        "khoi": str(fb_meta.get("khoi") or ""),
                        "bcnb_session_id": str(fb_meta.get("bcnb_session_id") or ""),
                        "verified_at": lms_ts,
                    }
                )

        zalo_exempt = sum(
            1
            for r in rows
            if "Miễn" in str(r.get("zalo_status") or "")
        )
        zalo_completed = sum(
            1
            for r in rows
            if "Miễn" not in str(r.get("zalo_status") or "")
            and "✅" in str(r.get("zalo_status") or "")
        )
        zalo_late = sum(
            1
            for r in rows
            if "nộp trễ" in str(r.get("zalo_status") or "").lower()
        )
        zalo_unscanned = sum(
            1 for r in rows if r.get("zalo_status") == "Chưa quét Zalo"
        )
        zalo_pending = max(
            0, len(rows) - zalo_completed - zalo_exempt - zalo_unscanned
        )

        lms_completed = sum(
            1
            for r in rows
            if str(r.get("lms_status") or "")
            in {
                "✅ Đã nhận xét & Tích BCNB",
                "✅ Đã tích trên BCNB",
                "✅ Đã nộp LMS",
            }
        )
        lms_dry_run = sum(
            1
            for r in rows
            if str(r.get("lms_status") or "") == "🧪 Đã nhận xét (Dry-run LMS)"
        )
        lms_pending_tick = sum(
            1
            for r in rows
            if str(r.get("lms_status") or "") == "⏳ Đã nhận xét (Chờ tích BCNB)"
        )
        lms_overdue = sum(
            1
            for r in rows
            if str(r.get("lms_status") or "").startswith(("❌", "⚠️"))
        )
        lms_unscanned = max(
            0,
            len(rows)
            - lms_completed
            - lms_dry_run
            - lms_pending_tick
            - lms_overdue,
        )

        return {
            "week": sel_week,
            "year": sel_year,
            "period_key": period_key,
            "selected_job_id": selected_job_id,
            "snapshot_id": selected_snapshot_id,
            "is_selective_only": is_selective_only,
            "available_weeks": available_weeks,
            "jobs": jobs_for_week,
            "summary": {
                "total": len(rows),
                "zalo_completed": zalo_completed,
                "zalo_exempt": zalo_exempt,
                "zalo_late": zalo_late,
                "zalo_pending": zalo_pending,
                "zalo_unscanned": zalo_unscanned,
                "lms_completed": lms_completed,
                "lms_dry_run": lms_dry_run,
                "lms_pending_tick": lms_pending_tick,
                "lms_overdue": lms_overdue,
                "lms_unscanned": lms_unscanned,
            },
            "lms_summary": {
                "has_lms_data": bool(
                    latest_preview is not None
                    or latest_exec is not None
                    or audits
                ),
                "analysis_job_id": (
                    latest_preview["analysis_job_id"]
                    if latest_preview is not None
                    else None
                ),
                "execution_job_id": (
                    latest_exec["execution_job_id"]
                    if latest_exec is not None
                    else None
                ),
                "missing_count": sum(
                    1
                    for s in lms_status_by_class.values()
                    if s.startswith(("❌", "⚠️"))
                ),
                "ticked_count": sum(
                    1
                    for s in lms_status_by_class.values()
                    if s == "✅ Đã nhận xét & Tích BCNB"
                ),
                "dry_run_count": sum(
                    1
                    for s in lms_status_by_class.values()
                    if s == "🧪 Đã nhận xét (Dry-run LMS)"
                ),
                "pending_tick_count": sum(
                    1
                    for s in lms_status_by_class.values()
                    if s == "⏳ Đã nhận xét (Chờ tích BCNB)"
                ),
                "already_checked_count": sum(
                    1
                    for s in lms_status_by_class.values()
                    if s == "✅ Đã tích trên BCNB"
                ),
            },
            "rows": rows,
            "items": sanitized_items,
        }

    is_failed_weekly_summary_row = staticmethod(is_failed_weekly_summary_row)
    filter_failed_weekly_summary_rows = staticmethod(
        filter_failed_weekly_summary_rows
    )
