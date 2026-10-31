"""Single source of truth for report status, row categories, issue severity, and shared validators."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import re
from typing import Any
import unicodedata

from core.time_utils import now_business, now_utc, parse_timestamp, utc_iso
from services.zalo_evidence import (
    AI_SUGGESTED_REPORT_STATUS,
    INSUFFICIENT_STATUS,
    LESSON_14_STATUS,
    NOT_FOUND_STATUS,
    SUBMITTED_LATE_STATUS,
    SUBMITTED_STATUS,
)

INSUFFICIENT_EVIDENCE_STATUS = INSUFFICIENT_STATUS
LESSON_14_EXEMPT_STATUS = LESSON_14_STATUS
MANUAL_APPROVED_STATUS = "✅ Đã duyệt tay & Tích BCNB"

# Evidence state constants
SUBMITTED_VERIFIED = "SUBMITTED_VERIFIED"
SUBMITTED_LATE_VERIFIED = "SUBMITTED_LATE_VERIFIED"
SUBMITTED_ON_TIME_VERIFIED = "SUBMITTED_ON_TIME_VERIFIED"
NOT_FOUND_VERIFIED = "NOT_FOUND_VERIFIED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
LESSON_14_EXEMPTION = "LESSON_14_EXEMPTION"
MANUAL_APPROVED_VERIFIED = "MANUAL_APPROVED_VERIFIED"
BLOCKED_BATCH_HALT = "BLOCKED_BATCH_HALT"

# Evidence & lifecycle terminal sets
SUBMITTED_EVIDENCE_STATES = frozenset(
    {
        SUBMITTED_VERIFIED,
        SUBMITTED_LATE_VERIFIED,
        SUBMITTED_ON_TIME_VERIFIED,
    }
)
ALREADY_SUBMITTED_EVIDENCE_STATES = frozenset(
    {
        SUBMITTED_VERIFIED,
        SUBMITTED_LATE_VERIFIED,
        SUBMITTED_ON_TIME_VERIFIED,
        LESSON_14_EXEMPTION,
        MANUAL_APPROVED_VERIFIED,
    }
)
VERIFIED_EVIDENCE_STATES = frozenset(
    {
        SUBMITTED_VERIFIED,
        SUBMITTED_LATE_VERIFIED,
        SUBMITTED_ON_TIME_VERIFIED,
        NOT_FOUND_VERIFIED,
        LESSON_14_EXEMPTION,
        MANUAL_APPROVED_VERIFIED,
    }
)
TERMINAL_SCAN_STATUSES = frozenset({"SCANNED", "FAILED", "CANCELLED"})
TERMINAL_WRITE_STATUSES = frozenset(
    {
        "UPDATED",
        "SKIPPED",
        "DRY_RUN",
        "FAILED_BEFORE_WRITE",
        "OUTCOME_UNKNOWN",
        "BLOCKED_BATCH_HALT",
    }
)
TERMINAL_JOB_STATUSES = frozenset(
    {
        "COMPLETED",
        "COMPLETED_WITH_ERRORS",
        "FAILED",
        "CANCELLED",
    }
)
FINISHED_SCAN_JOB_STATUSES = frozenset(
    {
        "COMPLETED",
        "COMPLETED_WITH_ERRORS",
    }
)

# Date window constants
REPORT_DATE_LOOKBACK_DAYS = 10
EVIDENCE_WINDOW_DAYS = 10
BCNB_TICK_MAX_DELAY_DAYS = 6

# Row category taxonomy
CATEGORY_DONE = "DONE"
CATEGORY_LATE = "LATE"
CATEGORY_MISSING = "MISSING"
CATEGORY_REVIEW = "REVIEW"
CATEGORY_EXEMPT = "EXEMPT"
CATEGORY_NOT_SCANNED = "NOT_SCANNED"
ALL_ROW_CATEGORIES = frozenset(
    {
        CATEGORY_DONE,
        CATEGORY_LATE,
        CATEGORY_MISSING,
        CATEGORY_REVIEW,
        CATEGORY_EXEMPT,
        CATEGORY_NOT_SCANNED,
    }
)

# Structured issue severity and codes
SEVERITY_FAIL = "FAIL"
SEVERITY_NOTE = "NOTE"

ISSUE_CODE_LMS_MANUAL_CHECK = "LMS_MANUAL_CHECK"
ISSUE_CODE_ZALO_MISSING_OR_WRONG_DATE_SAME_DAY = (
    "ZALO_MISSING_OR_WRONG_DATE_SAME_DAY"
)
ISSUE_CODE_ZALO_REPORT_NO_DATE = "ZALO_REPORT_NO_DATE"
ISSUE_CODE_ZALO_REPORT_WRONG_DATE = "ZALO_REPORT_WRONG_DATE"
ISSUE_CODE_LMS_QUALITY_FAIL = "LMS_QUALITY_FAIL"
ISSUE_CODE_ZALO_CONTENT_FAIL = "ZALO_CONTENT_FAIL"
ISSUE_CODE_LEGACY_UNCLASSIFIED_FAIL = "LEGACY_UNCLASSIFIED_FAIL"

NOTE_ISSUE_CODES = frozenset(
    {
        ISSUE_CODE_LMS_MANUAL_CHECK,
        ISSUE_CODE_ZALO_MISSING_OR_WRONG_DATE_SAME_DAY,
        ISSUE_CODE_ZALO_REPORT_NO_DATE,
        ISSUE_CODE_ZALO_REPORT_WRONG_DATE,
    }
)

_IDENTITY_TOKEN_PATTERN = re.compile(r"[A-Z0-9]+")
_LESSON_NUMBER_RE = re.compile(
    r"^\s*(?:(?:bu[oổ]i|b[aà]i|session|slot|lesson|b)\s*(?:s[oố]\s*)?)?0*(\d{1,3})\s*$",
    flags=re.IGNORECASE,
)
_PREFIXED_ISSUE_RE = re.compile(
    r"^\s*\[(?P<sev>NOTE|FAIL)\s*:\s*(?P<code>[A-Z0-9_]+)\]\s*(?P<text>.*)$",
    flags=re.IGNORECASE,
)
_LEGACY_D4_MANUAL_CHECK_RE = re.compile(
    r"^\[LMS\].*cần\s+kiểm\s+tra\s+thủ\s+công\.?$",
    flags=re.IGNORECASE,
)
_D2_NO_DATE_RE = re.compile(
    r"^\[Zalo\]\s*Báo\s+cáo\s+không\s+ghi\s+ngày/buổi\.?$",
    flags=re.IGNORECASE,
)
_D2_WRONG_DATE_SAME_DAY_RE = re.compile(
    r"^\[Zalo\]\s*Báo\s+cáo\s+ghi\s+sai\s+ngày\s*\(\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*\)\.?$",
    flags=re.IGNORECASE,
)
_LEGACY_IGNORED_DUP_RE = re.compile(
    r"^\[LMS\]\s*(?:Nhận|Nhan)\s+x[eé]t\s+HS\s+copy-paste\s+tr[uù]ng\s+l[aặ]p\b",
    flags=re.IGNORECASE,
)
_DRY_RUN_BADGE_PREFIX_RE = re.compile(
    r"^\s*\[(?:DRY-RUN|DRY_RUN)\]\s*",
    flags=re.IGNORECASE,
)


def identity_tokens(value: str | None) -> tuple[str, ...]:
    """Split an identifier into uppercase alphanumeric tokens."""
    if not value:
        return ()
    normalized = unicodedata.normalize("NFKC", str(value)).upper()
    return tuple(
        token for token in _IDENTITY_TOKEN_PATTERN.findall(normalized) if token
    )


def normalize_class_code(value: Any) -> str:
    """Return the canonical hyphen-joined uppercase class code."""
    if value is None:
        return ""
    tokens = identity_tokens(str(value))
    return "-".join(tokens)


def normalize_lesson_number(value: Any) -> int | None:
    """Parse a lesson number from int or text ('14', '014', 'Buổi 14') without matching dates."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    match = _LESSON_NUMBER_RE.match(text)
    if not match:
        return None
    try:
        parsed = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def is_lesson_14(
    value: Any,
    st: Mapping[str, Any] | None = None,
) -> bool:
    """Return True when a scalar lesson value or row/result dict represents Lesson 14."""
    if isinstance(value, Mapping):
        raw_st = value.get("scan_target")
        st_map: Mapping[str, Any] = (
            st
            if isinstance(st, Mapping)
            else (raw_st if isinstance(raw_st, Mapping) else {})
        )
        raw_ev = value.get("evidence")
        ev_map: Mapping[str, Any] = raw_ev if isinstance(raw_ev, Mapping) else {}
        raw_ctx = ev_map.get("date_context")
        ctx_map: Mapping[str, Any] = raw_ctx if isinstance(raw_ctx, Mapping) else {}
        for candidate in (
            st_map.get("lesson"),
            ctx_map.get("target_lesson"),
            value.get("lesson"),
            value.get("buoi_so"),
        ):
            if candidate is not None and str(candidate).strip() not in {"", "—"}:
                return normalize_lesson_number(candidate) == 14
        ev_state = str(value.get("evidence_state") or "").strip().upper()
        if ev_state == LESSON_14_EXEMPTION:
            return True
        status_text = str(
            value.get("zalo_status") or value.get("status") or ""
        ).strip()
        if "Miễn" in status_text and "14" in status_text:
            return True
        return False
    return normalize_lesson_number(value) == 14


def _strip_issue_prefix(raw_text: str) -> tuple[str, str, str]:
    """Return ``(prefix_code, prefix_severity, clean_text)`` from a ``[NOTE:CODE]`` or ``[FAIL:CODE]`` string."""
    cleaned = " ".join(str(raw_text or "").split()).strip()
    match = _PREFIXED_ISSUE_RE.match(cleaned)
    if not match:
        return "", "", cleaned
    return (
        match.group("code").strip().upper(),
        match.group("sev").strip().upper(),
        " ".join(match.group("text").split()).strip(),
    )


class StructuredIssueStr(str):
    """A ``str`` subclass that behaves identically to plain text while retaining ``code`` and ``severity``."""

    code: str
    severity: str

    def __new__(
        cls,
        text: str,
        *,
        code: str = "",
        severity: str = "",
    ) -> StructuredIssueStr:
        _, _, clean_text = _strip_issue_prefix(text)
        obj = super().__new__(cls, clean_text)
        adapted = _adapt_legacy_issue(
            {"code": code, "severity": severity, "text": clean_text}
            if code
            else text
        )
        obj.code = adapted["code"]
        obj.severity = adapted["severity"]
        return obj

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "text": str(self),
        }


def make_structured_issue(
    code: str,
    text: str,
    *,
    severity: str | None = None,
) -> dict[str, str]:
    """Create a JSON-serializable structured issue dict `{code, severity, text}`."""
    pref_code, pref_sev, stripped_text = _strip_issue_prefix(str(text or ""))
    clean_code = (
        str(code or "").strip().upper()
        or pref_code
        or ISSUE_CODE_LEGACY_UNCLASSIFIED_FAIL
    )
    explicit_sev = str(severity or pref_sev or "").strip().upper()
    if explicit_sev in {SEVERITY_NOTE, SEVERITY_FAIL}:
        resolved_severity = explicit_sev
    else:
        resolved_severity = (
            SEVERITY_NOTE if clean_code in NOTE_ISSUE_CODES else SEVERITY_FAIL
        )
    return {
        "code": clean_code,
        "severity": resolved_severity,
        "text": stripped_text,
    }


def _adapt_legacy_issue(issue: Any) -> dict[str, str]:
    """Single legacy compatibility adapter for structured dicts, ``[NOTE:CODE]`` strings, and uncoded issues."""
    if isinstance(issue, StructuredIssueStr) and issue.code:
        attr_code = str(issue.code).strip().upper()
        attr_sev = str(getattr(issue, "severity", "") or "").strip().upper()
        _, _, attr_text = _strip_issue_prefix(str(issue))
        return {
            "code": attr_code,
            "severity": (
                attr_sev
                if attr_sev in {SEVERITY_NOTE, SEVERITY_FAIL}
                else (
                    SEVERITY_NOTE
                    if attr_code in NOTE_ISSUE_CODES
                    else SEVERITY_FAIL
                )
            ),
            "text": attr_text,
        }

    if isinstance(issue, Mapping):
        raw_text_in = " ".join(
            str(issue.get("text") or issue.get("message") or "").split()
        ).strip()
        pref_code, pref_sev, stripped_text = _strip_issue_prefix(raw_text_in)
        raw_code = str(issue.get("code") or "").strip().upper() or pref_code
        raw_sev = str(issue.get("severity") or "").strip().upper() or pref_sev
        if raw_code:
            resolved_sev = (
                raw_sev
                if raw_sev in {SEVERITY_NOTE, SEVERITY_FAIL}
                else (
                    SEVERITY_NOTE
                    if raw_code in NOTE_ISSUE_CODES
                    else SEVERITY_FAIL
                )
            )
            return {
                "code": raw_code,
                "severity": resolved_sev,
                "text": stripped_text,
            }
        text = stripped_text
    else:
        raw_str = " ".join(str(issue or "").split()).strip()
        pref_code, pref_sev, stripped_text = _strip_issue_prefix(raw_str)
        if pref_code:
            resolved_sev = (
                pref_sev
                if pref_sev in {SEVERITY_NOTE, SEVERITY_FAIL}
                else (
                    SEVERITY_NOTE
                    if pref_code in NOTE_ISSUE_CODES
                    else SEVERITY_FAIL
                )
            )
            return {
                "code": pref_code,
                "severity": resolved_sev,
                "text": stripped_text,
            }
        text = stripped_text

    if not text:
        return {
            "code": "",
            "severity": SEVERITY_NOTE,
            "text": "",
        }
    if _LEGACY_D4_MANUAL_CHECK_RE.match(text):
        return {
            "code": ISSUE_CODE_LMS_MANUAL_CHECK,
            "severity": SEVERITY_NOTE,
            "text": text,
        }
    if _D2_NO_DATE_RE.match(text):
        return {
            "code": ISSUE_CODE_ZALO_REPORT_NO_DATE,
            "severity": SEVERITY_NOTE,
            "text": text,
        }
    if _D2_WRONG_DATE_SAME_DAY_RE.match(text):
        return {
            "code": ISSUE_CODE_ZALO_REPORT_WRONG_DATE,
            "severity": SEVERITY_NOTE,
            "text": text,
        }
    return {
        "code": ISSUE_CODE_LEGACY_UNCLASSIFIED_FAIL,
        "severity": SEVERITY_FAIL,
        "text": text,
    }


def coerce_structured_issue(issue: Any) -> dict[str, str]:
    """Normalize either a structured issue dict or a legacy string into `{code, severity, text}`."""
    return _adapt_legacy_issue(issue)


def issue_text(issue: Any) -> str:
    """Return plain display/Teaching text for a structured or legacy issue (never dict repr or prefix)."""
    if issue is None:
        return ""
    if isinstance(issue, Mapping):
        raw_val = str(issue.get("text") or issue.get("message") or "")
        return _strip_issue_prefix(raw_val)[2]
    return _strip_issue_prefix(str(issue))[2]


def issue_code(issue: Any) -> str:
    """Return the canonical issue code for a structured or legacy issue."""
    return _adapt_legacy_issue(issue)["code"]


def issue_severity(issue: Any) -> str:
    """Return `'FAIL'` or `'NOTE'` based on structured issue code (with single legacy adapter)."""
    adapted = _adapt_legacy_issue(issue)
    if not adapted["text"]:
        return SEVERITY_NOTE
    if adapted["severity"] in {SEVERITY_NOTE, SEVERITY_FAIL}:
        return adapted["severity"]
    if adapted["code"] in NOTE_ISSUE_CODES:
        return SEVERITY_NOTE
    return SEVERITY_FAIL


def _strip_dry_run_prefix(status_text: str) -> str:
    return _DRY_RUN_BADGE_PREFIX_RE.sub("", str(status_text or "").strip()).strip()


def categorize_row(row: Any) -> str:
    """Classify a scan result or weekly summary row into one canonical category.

    Returns one of: ``DONE | LATE | MISSING | REVIEW | EXEMPT | NOT_SCANNED``.
    """
    if not isinstance(row, Mapping):
        return CATEGORY_REVIEW

    raw_st = row.get("scan_target")
    st: Mapping[str, Any] = raw_st if isinstance(raw_st, Mapping) else {}

    bcnb_state = str(row.get("bcnb_update_state") or "").strip().upper()
    write_status_val = str(row.get("write_status") or "").strip().upper()
    failed_write_states = {
        "FAILED",
        "FAILED_BEFORE_WRITE",
        "OUTCOME_UNKNOWN",
        "CONFLICT_AFTER_WRITE",
        "BLOCKED_BATCH_HALT",
    }
    scan_status = str(row.get("scan_status") or "").strip().upper()
    error_code = str(row.get("error_code") or "").strip()
    if (
        bcnb_state in failed_write_states
        or write_status_val in failed_write_states
        or scan_status in {"FAILED", "CANCELLED"}
        or bool(error_code)
    ):
        return CATEGORY_REVIEW

    if is_lesson_14(row):
        return CATEGORY_EXEMPT

    ev_state = str(row.get("evidence_state") or "").strip().upper()
    status_raw = _strip_dry_run_prefix(
        str(row.get("zalo_status") or row.get("status") or "")
    )
    status_lower = status_raw.lower()

    # Check if deadline verification downgraded a submitted result to ⚠️ review
    raw_ev = row.get("evidence")
    ev_map: Mapping[str, Any] = raw_ev if isinstance(raw_ev, Mapping) else {}
    raw_ctx = ev_map.get("date_context")
    ctx_map: Mapping[str, Any] = raw_ctx if isinstance(raw_ctx, Mapping) else {}
    deadline_ver = str(ctx_map.get("deadline_verification") or "").strip().upper()
    if deadline_ver == "UNREADABLE" or status_raw.startswith("⚠️"):
        return CATEGORY_REVIEW

    if ev_state == LESSON_14_EXEMPTION:
        return CATEGORY_EXEMPT
    if ev_state == SUBMITTED_LATE_VERIFIED and deadline_ver != "PENDING":
        return CATEGORY_LATE
    if (
        ev_state
        in {
            SUBMITTED_VERIFIED,
            SUBMITTED_ON_TIME_VERIFIED,
            SUBMITTED_LATE_VERIFIED,
            MANUAL_APPROVED_VERIFIED,
        }
        or bool(row.get("manual_approved"))
    ):
        return CATEGORY_DONE
    if ev_state == NOT_FOUND_VERIFIED:
        return CATEGORY_MISSING
    if ev_state in {INSUFFICIENT_EVIDENCE, BLOCKED_BATCH_HALT, "UNKNOWN"}:
        return CATEGORY_REVIEW

    if status_raw == "Chưa quét Zalo":
        return CATEGORY_NOT_SCANNED
    if "miễn" in status_lower:
        return CATEGORY_EXEMPT
    if status_raw.startswith("🟡") or "nộp trễ" in status_lower:
        return CATEGORY_LATE
    if status_raw.startswith("⏳"):
        return CATEGORY_DONE
    nx_zalo_checked = row.get("nx_zalo_checked")
    if not isinstance(nx_zalo_checked, bool):
        nx_zalo_checked = st.get("nx_zalo_checked")
    if status_raw.startswith("✅") or (
        status_raw in {"", "Chưa xác định"} and nx_zalo_checked is True
    ):
        return CATEGORY_DONE
    if (
        status_raw.startswith("❌")
        or status_lower.startswith("chưa nhận xét")
        or "chưa tìm thấy" in status_lower
    ):
        return CATEGORY_MISSING
    return CATEGORY_REVIEW


def _norm_issue_key(text: str) -> str:
    stripped = re.sub(r"^\[[^\]]+\]\s*", "", str(text or "").strip())
    stripped = stripped.replace(" — ", "; ").strip(" .;").lower()
    return " ".join(stripped.split())


def _is_ignored_legacy_duplicate_issue(text: str) -> bool:
    cleaned = " ".join(str(text or "").split()).strip(" ;")
    if not cleaned:
        return True
    if _LEGACY_IGNORED_DUP_RE.match(cleaned):
        return True
    if re.match(r"^\[LMS\]", cleaned, flags=re.IGNORECASE) and (
        "copy-paste trùng lặp" in cleaned.lower()
    ):
        return True
    return False


def _row_issue_severities(row: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return ``(has_fail_issue, has_any_issue)`` for a row or scan result."""
    from services.bcnb_safety import split_checker_issue_note
    from services.lms_quality_service import normalize_auto_issue_text

    raw_st = row.get("scan_target")
    st: Mapping[str, Any] = raw_st if isinstance(raw_st, Mapping) else {}

    note_keys: set[str] = set()
    fail_keys: set[str] = set()

    def _record_structured_or_item(entry: Any) -> None:
        txt = issue_text(entry)
        if not txt or _is_ignored_legacy_duplicate_issue(txt):
            return
        if re.match(r"^\[(?:LMS|Zalo)\]\s*", txt, flags=re.IGNORECASE):
            txt = normalize_auto_issue_text(txt)
        key = _norm_issue_key(txt)
        if not key or key in fail_keys:
            return
        sev = issue_severity(entry)
        if sev == SEVERITY_NOTE:
            note_keys.add(key)
        else:
            note_keys.discard(key)
            fail_keys.add(key)

    for source in (row, st):
        for list_key in ("quality_issues", "lms_quality_issues", "zalo_content_issues"):
            items = source.get(list_key)
            if isinstance(items, (list, tuple)):
                for item in items:
                    _record_structured_or_item(item)

    if (
        not row.get("zalo_content_issues")
        and not st.get("zalo_content_issues")
        and isinstance(row.get("evidence"), Mapping)
    ):
        from services.lms_quality_service import evaluate_zalo_content_issues

        lms_summary = str(
            row.get("lms_lesson_summary") or st.get("lms_lesson_summary") or ""
        ).strip()
        for evaluated_issue in evaluate_zalo_content_issues(
            dict(row),
            lms_summary_text=lms_summary,
        ):
            _record_structured_or_item(evaluated_issue)

    def _inspect_text_field(raw_text: Any, *, split_semi: bool) -> None:
        text = " ".join(str(raw_text or "").split()).strip()
        if not text:
            return
        manual_note, auto_segments = split_checker_issue_note(text)
        segments: list[tuple[str, bool]] = []
        if auto_segments or re.search(r"\[Auto\s+QC:\s*", text, flags=re.IGNORECASE):
            if manual_note:
                segments.append((manual_note, False))
            for seg in auto_segments:
                segments.append((seg, True))
        else:
            for pipe_part in text.split("|"):
                p_clean = pipe_part.strip()
                if not p_clean:
                    continue
                if split_semi or re.search(
                    r"(?:^|;)\s*\[(?:LMS|Zalo|NOTE:|FAIL:)",
                    p_clean,
                    flags=re.IGNORECASE,
                ):
                    for sub_part in p_clean.split(";"):
                        if sub_part.strip():
                            segments.append((sub_part.strip(), False))
                else:
                    segments.append((p_clean, False))

        for seg, is_auto in segments:
            cleaned = " ".join(str(seg or "").split()).strip(" ;")
            if not cleaned or _is_ignored_legacy_duplicate_issue(cleaned):
                continue
            pref_code, _, stripped_seg = _strip_issue_prefix(cleaned)
            if is_auto or re.match(
                r"^\[(?:LMS|Zalo)\]\s*", stripped_seg, flags=re.IGNORECASE
            ):
                stripped_seg = normalize_auto_issue_text(stripped_seg)
            key = _norm_issue_key(stripped_seg)
            if not key or key in fail_keys:
                continue
            if isinstance(seg, StructuredIssueStr) and seg.code:
                resolved_sev = issue_severity(seg)
            elif pref_code:
                resolved_sev = issue_severity(cleaned)
            else:
                resolved_sev = issue_severity(stripped_seg)
            if resolved_sev == SEVERITY_NOTE:
                note_keys.add(key)
            else:
                note_keys.discard(key)
                fail_keys.add(key)

    qc_note = str(row.get("qc_issue_note") or st.get("qc_issue_note") or "").strip()
    if qc_note:
        _inspect_text_field(qc_note, split_semi=False)

    issue_desc = str(row.get("issue_description") or "").strip()
    if issue_desc:
        _inspect_text_field(issue_desc, split_semi=True)

    has_fail = len(fail_keys) > 0
    has_any = has_fail or len(note_keys) > 0
    return has_fail, has_any


def is_failed(row: Any) -> bool:
    """Return True when a row/result has a Zalo error, confirmed LMS non-submission, or FAIL QC issue.

    Per Owner Decisions D1 & D4:
    - ``LATE`` (🟡 nộp trễ), ``DONE``, ``EXEMPT``, and ``NOT_SCANNED`` are not Zalo errors.
    - ``NOTE`` issues (e.g. ``LMS_MANUAL_CHECK``, ``ZALO_MISSING_OR_WRONG_DATE_SAME_DAY``,
      ``ZALO_REPORT_NO_DATE``, ``ZALO_REPORT_WRONG_DATE``) do not mark a row as failed.
    - Confirmed LMS non-submission / non-evaluation (``lms_status`` or ``evaluation_status`` starting
      with ``❌``/``⚠️``) and ``FAIL`` issues still mark the row as failed.
    """
    if not isinstance(row, Mapping):
        return False

    category = categorize_row(row)
    zalo_failed = category in {
        CATEGORY_MISSING,
        CATEGORY_REVIEW,
    }

    raw_st = row.get("scan_target")
    st: Mapping[str, Any] = raw_st if isinstance(raw_st, Mapping) else {}
    lms_status = str(row.get("lms_status") or st.get("lms_status") or "").strip()
    nx_lms = row.get("nx_lms_checked")
    if not isinstance(nx_lms, bool):
        nx_lms = st.get("nx_lms_checked")
    if lms_status in {"", "Chưa quét LMS"} and nx_lms is False:
        lms_status = "❌ Chưa nộp LMS"
    eval_status = str(
        row.get("evaluation_status") or st.get("evaluation_status") or ""
    ).strip()
    eval_lower = eval_status.lower()

    if category == CATEGORY_EXEMPT and (
        "zalo cần kiểm tra" in eval_lower or "chưa nhận xét zalo" in eval_lower
    ):
        eval_status = ""
        eval_lower = ""
    elif category == CATEGORY_LATE and (
        "chưa nhận xét zalo" in eval_lower or "nộp trễ" in eval_lower
    ):
        eval_status = ""
        eval_lower = ""

    has_fail_issue, has_any_issue = _row_issue_severities(row)

    lms_failed = lms_status.startswith(("❌", "⚠️"))
    if has_any_issue and not has_fail_issue and "lỗi chất lượng nội dung" in eval_lower:
        eval_failed = False
    else:
        eval_failed = eval_status.startswith(("❌", "⚠️"))

    return zalo_failed or lms_failed or eval_failed or has_fail_issue


def validate_two_week_plan(
    snapshot: Mapping[str, Any] | None,
    *,
    max_age_minutes: int = 30,
    now: datetime | None = None,
    business_now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a two-week BCNB/Zalo discovery snapshot plan.

    Shared across ``zalo_preflight``, ``/run-scan``, and ``/api/zalo-scan-plan`` so
    all three agree on plan-level validity (missing weeks, invalid/duplicate targets,
    staleness, Lesson 14 exemptions, dropped rows, and deferred targets).
    """
    from repositories.zalo_batch_repository import canonical_target_key
    from services.zalo_discovery_plan import build_dropped_rows_summary
    from services.zalo_target_eligibility import (
        _parse_target_day,
        split_scan_targets_with_deduped,
    )

    empty_dropped = build_dropped_rows_summary()
    if not isinstance(snapshot, Mapping):
        return {
            "valid": False,
            "error_code": "SCAN_PLAN_REQUIRED",
            "message": "Hãy bấm Dò tìm để tạo kế hoạch hai tuần trước khi quét.",
            "remediation": "Bấm Dò tìm trước khi quét.",
            "snapshot_id": None,
            "status": None,
            "observed_at": None,
            "expires_at": None,
            "latest_week": None,
            "required_week": None,
            "selected_weeks": [],
            "scan_targets": [],
            "deferred_targets": [],
            "exempt_targets": [],
            "candidate_targets": [],
            "dropped_rows": empty_dropped,
            "target_keys": [],
            "target_count": 0,
            "deferred_target_count": 0,
            "exempt_target_count": 0,
            "candidate_target_count": 0,
        }

    raw_plan = snapshot.get("plan")
    plan: Mapping[str, Any] = raw_plan if isinstance(raw_plan, Mapping) else {}
    raw_week_plan = plan.get("bcnb_week_plan")
    week_plan: Mapping[str, Any] = (
        raw_week_plan if isinstance(raw_week_plan, Mapping) else {}
    )
    raw_selected_weeks = week_plan.get("selected_weeks")
    selected_weeks = (
        list(raw_selected_weeks) if isinstance(raw_selected_weeks, list) else []
    )
    raw_dropped_obj = plan.get("dropped_rows")
    raw_dropped: Mapping[str, Any] = (
        raw_dropped_obj if isinstance(raw_dropped_obj, Mapping) else {}
    )
    raw_dropped_inner = raw_dropped.get("groups")
    dropped_src: Mapping[str, Any] = (
        raw_dropped_inner
        if isinstance(raw_dropped_inner, Mapping)
        else raw_dropped
    )
    dropped_groups = {
        key: [
            dict(item)
            for item in (
                dropped_src.get(key)
                or (
                    dropped_src.get("deduped_targets")
                    if key == "deduped"
                    else None
                )
                or []
            )
            if isinstance(item, Mapping)
        ]
        for key in (
            "transition_review",
            "missing_zalo",
            "ambiguous_zalo",
            "deduped",
            "invalid_date",
            "skipped_empty",
        )
    }

    raw_candidates = [
        *(plan.get("scan_targets") or []),
        *(plan.get("deferred_targets") or []),
    ]
    raw_exempts = list(plan.get("exempt_targets") or [])

    def _has_invalid_lesson_date_only(item: Any) -> bool:
        if not isinstance(item, Mapping):
            return False
        class_code = normalize_class_code(item.get("class_code"))
        lesson = str(item.get("lesson") or "").strip()
        raw_w = item.get("week")
        raw_y = item.get("year")
        if raw_w is None or raw_y is None or isinstance(raw_w, bool) or isinstance(raw_y, bool):
            return False
        try:
            w = int(str(raw_w))
            y = int(str(raw_y))
        except (TypeError, ValueError):
            return False
        if not class_code or not lesson or not (1 <= w <= 53 and 2000 < y <= 2100):
            return False
        return (
            _parse_target_day(item.get("class_date_iso") or item.get("class_date"))
            is None
        )

    candidate_targets: list[Any] = []
    separated_invalid_date_count = 0
    for item in raw_candidates:
        if _has_invalid_lesson_date_only(item):
            sep = dict(item)
            sep.setdefault("reason", "INVALID_DATE")
            dropped_groups["invalid_date"].append(sep)
            separated_invalid_date_count += 1
        elif isinstance(item, Mapping) and is_lesson_14(item):
            raw_exempts.append(item)
        else:
            candidate_targets.append(item)

    exempt_targets: list[dict[str, Any]] = []
    seen_exempt_keys: set[str] = set()
    invalid_exempt_target = False
    for item in raw_exempts:
        if _has_invalid_lesson_date_only(item):
            sep = dict(item)
            sep.setdefault("reason", "INVALID_DATE")
            dropped_groups["invalid_date"].append(sep)
            separated_invalid_date_count += 1
            continue
        if not isinstance(item, Mapping):
            invalid_exempt_target = True
            break
        entry = dict(item)
        entry.setdefault("group_name", str(entry.get("group_name") or "").strip())
        entry.setdefault("target_kind", "EXEMPTION")
        parsed_day = _parse_target_day(
            entry.get("class_date_iso") or entry.get("class_date")
        )
        if parsed_day is not None and not entry.get("class_date_iso"):
            entry["class_date_iso"] = parsed_day.isoformat()
        try:
            ex_key = canonical_target_key(entry)
        except (AttributeError, TypeError, ValueError):
            invalid_exempt_target = True
            break
        if ex_key in seen_exempt_keys:
            dup = dict(entry)
            dup.setdefault("reason", "DUPLICATE_TARGET")
            dropped_groups["deduped"].append(dup)
            continue
        seen_exempt_keys.add(ex_key)
        exempt_targets.append(entry)

    periods: set[tuple[int, int]] = set()
    for item in selected_weeks:
        if isinstance(item, Mapping):
            try:
                periods.add((int(item["week"]), int(item["year"])))
            except (KeyError, TypeError, ValueError):
                continue

    latest_period: tuple[int, int] | None
    required_period: tuple[int, int] | None
    try:
        latest_period = (
            int(week_plan["latest_week"]["week"]),
            int(week_plan["latest_week"]["year"]),
        )
        required_period = (
            int(week_plan["required_week"]["week"]),
            int(week_plan["required_week"]["year"]),
        )
    except (KeyError, TypeError, ValueError):
        latest_period = required_period = None

    effective_business_now = business_now or now_business()
    has_duplicate_candidate = False
    try:
        candidate_keys = [canonical_target_key(item) for item in candidate_targets]
        has_duplicate_candidate = len(candidate_keys) != len(set(candidate_keys))
    except Exception:
        candidate_keys = None

    try:
        scan_targets, deferred_targets, _ = (
            split_scan_targets_with_deduped(
                candidate_targets,
                latest_week=week_plan.get("latest_week"),
                now=effective_business_now,
            )
        )
    except Exception:
        scan_targets, deferred_targets = [], []

    dropped_rows = build_dropped_rows_summary(**dropped_groups)
    observed_at_dt = parse_timestamp(snapshot.get("observed_at"))
    ttl_minutes = max(1, int(max_age_minutes))
    expires_at_dt = (
        observed_at_dt + timedelta(minutes=ttl_minutes)
        if observed_at_dt is not None
        else None
    )

    base_info: dict[str, Any] = {
        "snapshot_id": snapshot.get("snapshot_id"),
        "status": snapshot.get("status"),
        "observed_at": snapshot.get("observed_at"),
        "expires_at": utc_iso(expires_at_dt) if expires_at_dt is not None else None,
        "latest_week": week_plan.get("latest_week"),
        "required_week": week_plan.get("required_week"),
        "selected_weeks": selected_weeks,
        "scan_targets": scan_targets,
        "deferred_targets": deferred_targets,
        "exempt_targets": exempt_targets,
        "candidate_targets": candidate_targets,
        "dropped_rows": dropped_rows,
        "target_keys": [],
        "target_count": len(scan_targets),
        "deferred_target_count": len(deferred_targets),
        "exempt_target_count": len(exempt_targets),
        "candidate_target_count": len(candidate_targets),
    }

    if snapshot.get("status") not in {"OBSERVED_ONLY", "PROMOTED"}:
        return {
            **base_info,
            "valid": False,
            "error_code": "SCAN_PLAN_NOT_TRUSTED",
            "message": "Kế hoạch tuần BCNB chưa đủ tin cậy để quét.",
            "remediation": "Bấm Dò tìm lại.",
        }

    if (
        len(selected_weeks) != 2
        or len(periods) != 2
        or latest_period is None
        or required_period is None
        or periods != {latest_period, required_period}
        or latest_period == required_period
    ):
        return {
            **base_info,
            "valid": False,
            "error_code": "SCAN_PLAN_INCOMPLETE",
            "message": "Kế hoạch BCNB chưa có đủ hai tuần và target quét.",
            "remediation": "Bấm Dò tìm lại.",
        }

    if not candidate_targets and not exempt_targets:
        if separated_invalid_date_count > 0:
            return {
                **base_info,
                "valid": False,
                "error_code": "SCAN_PLAN_INVALID_TARGET",
                "message": "Mục tiêu quét thiếu ngày học cụ thể của buổi BCNB.",
                "remediation": "Kiểm tra ngày học trên BCNB rồi Dò tìm lại.",
            }
        return {
            **base_info,
            "valid": False,
            "error_code": "SCAN_PLAN_INCOMPLETE",
            "message": "Kế hoạch BCNB chưa có đủ hai tuần và target quét.",
            "remediation": "Bấm Dò tìm lại.",
        }

    if invalid_exempt_target or candidate_keys is None:
        return {
            **base_info,
            "valid": False,
            "error_code": "SCAN_PLAN_INVALID_TARGET",
            "message": "Kế hoạch có target thiếu mã định danh ổn định.",
            "remediation": "Kiểm tra lớp mơ hồ rồi Dò tìm lại.",
        }

    exempt_keys = [canonical_target_key(item) for item in exempt_targets]
    target_keys = [*candidate_keys, *exempt_keys]
    base_info["target_keys"] = target_keys
    if has_duplicate_candidate or len(target_keys) != len(set(target_keys)):
        return {
            **base_info,
            "valid": False,
            "error_code": "SCAN_PLAN_DUPLICATE_TARGET",
            "message": "Kế hoạch có target trùng.",
            "remediation": "Bấm Dò tìm lại trước khi chạy.",
        }

    effective_now = now or now_utc()
    if (
        observed_at_dt is None
        or (effective_now - observed_at_dt).total_seconds() > ttl_minutes * 60
    ):
        return {
            **base_info,
            "valid": False,
            "error_code": "SCAN_PLAN_STALE",
            "message": "Kế hoạch đã quá hạn hoặc thiếu thời điểm quan sát.",
            "remediation": "Bấm Dò tìm lại ngay trước khi quét.",
        }

    if not scan_targets and not exempt_targets:
        return {
            **base_info,
            "valid": False,
            "error_code": "SCAN_TARGETS_DEFERRED",
            "message": "Chưa có lớp đủ giờ học để quét.",
            "remediation": "Chờ sau giờ kết thúc buổi học rồi Dò tìm lại.",
        }

    return {
        **base_info,
        "valid": True,
        "error_code": None,
        "message": "Kế hoạch hai tuần và target hợp lệ.",
        "remediation": "",
    }


__all__ = [
    "AI_SUGGESTED_REPORT_STATUS",
    "ALL_ROW_CATEGORIES",
    "ALREADY_SUBMITTED_EVIDENCE_STATES",
    "BCNB_TICK_MAX_DELAY_DAYS",
    "BLOCKED_BATCH_HALT",
    "CATEGORY_DONE",
    "CATEGORY_EXEMPT",
    "CATEGORY_LATE",
    "CATEGORY_MISSING",
    "CATEGORY_NOT_SCANNED",
    "CATEGORY_REVIEW",
    "EVIDENCE_WINDOW_DAYS",
    "FINISHED_SCAN_JOB_STATUSES",
    "INSUFFICIENT_EVIDENCE",
    "INSUFFICIENT_EVIDENCE_STATUS",
    "ISSUE_CODE_LEGACY_UNCLASSIFIED_FAIL",
    "ISSUE_CODE_LMS_MANUAL_CHECK",
    "ISSUE_CODE_LMS_QUALITY_FAIL",
    "ISSUE_CODE_ZALO_CONTENT_FAIL",
    "ISSUE_CODE_ZALO_REPORT_NO_DATE",
    "ISSUE_CODE_ZALO_REPORT_WRONG_DATE",
    "LESSON_14_EXEMPTION",
    "LESSON_14_EXEMPT_STATUS",
    "MANUAL_APPROVED_STATUS",
    "MANUAL_APPROVED_VERIFIED",
    "NOTE_ISSUE_CODES",
    "NOT_FOUND_STATUS",
    "NOT_FOUND_VERIFIED",
    "REPORT_DATE_LOOKBACK_DAYS",
    "SEVERITY_FAIL",
    "SEVERITY_NOTE",
    "SUBMITTED_EVIDENCE_STATES",
    "SUBMITTED_LATE_STATUS",
    "SUBMITTED_LATE_VERIFIED",
    "SUBMITTED_ON_TIME_VERIFIED",
    "SUBMITTED_STATUS",
    "SUBMITTED_VERIFIED",
    "TERMINAL_JOB_STATUSES",
    "TERMINAL_SCAN_STATUSES",
    "TERMINAL_WRITE_STATUSES",
    "VERIFIED_EVIDENCE_STATES",
    "categorize_row",
    "coerce_structured_issue",
    "identity_tokens",
    "is_failed",
    "is_lesson_14",
    "issue_code",
    "issue_severity",
    "issue_text",
    "make_structured_issue",
    "normalize_class_code",
    "normalize_lesson_number",
    "validate_two_week_plan",
]
