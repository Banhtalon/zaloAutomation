"""LMS & Zalo content quality inspection service (Wave 4 — Stage 4.2).

Evaluates:
1. LMS lesson summary (`slot.summary`) for substantive content vs empty/placeholder text.
2. Session type classification: Checkpoint sessions (Coding B5/B9, Robotics B4/B8, or Rubric)
   vs Regular sessions (all other sessions except exempted Lesson 14).
3. LMS per-student comments (`studentAttendance`) for attended students (`ATTENDED`, `LATE_ARRIVED`),
   enforcing 3-part structure on regular sessions and competency rubric on checkpoint sessions,
   without ever exposing student PII (names or raw comments) in issue reports.
4. Zalo report content issues: wrong date, wrong lesson number, or lesson title mismatch against LMS.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import asyncio
import contextlib
import html
import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from core.time_utils import BUSINESS_TIMEZONE
from services.bcnb_safety import fold_text, normalize_auto_issue_text
from services.report_status import (
    ISSUE_CODE_LMS_MANUAL_CHECK,
    ISSUE_CODE_ZALO_REPORT_NO_DATE,
    ISSUE_CODE_ZALO_REPORT_WRONG_DATE,
    SEVERITY_NOTE,
    SUBMITTED_EVIDENCE_STATES,
    VERIFIED_EVIDENCE_STATES,
    StructuredIssueStr,
    is_lesson_14,
    issue_code,
    issue_text,
    make_structured_issue,
    normalize_class_code,
    normalize_lesson_number,
)
from services.zalo_safety import parse_class_date

MAX_LMS_QUALITY_CLASSES_PER_BATCH = 60
MAX_LMS_SLOTS_PER_CLASS = 20
MAX_LMS_STUDENTS_PER_SLOT = 50
MIN_SUBSTANTIVE_SUMMARY_CHARS = 15
MIN_REGULAR_COMMENT_CHARS = 110
LMS_GRAPHQL_ENDPOINT = "https://lms-api.mindx.edu.vn/"
LMS_ALLOWED_HOSTNAME = "lms.mindx.edu.vn"
ALLOWED_LMS_TOKEN_KEYS = frozenset(
    {"id_token", "access_token", "lms_token", "auth_token", "token"}
)

ATTENDED_STATUSES = frozenset({"ATTENDED", "LATE_ARRIVED"})

CODING_TRACK_TOKENS = frozenset(
    {
        "C4K",
        "PTB",
        "PTI",
        "PTA",
        "JSB",
        "JSI",
        "JSA",
        "CSB",
        "CSI",
        "CSA",
        "GB",
        "GI",
        "GA",
        "SB",
        "SI",
        "SA",
        "WEB",
        "APP",
        "PY",
    }
)

SUMMARY_PLACEHOLDER_PATTERNS = (
    r"^chua\s+(?:co\s+)?noi\s+dung(?:\s+bai\s+hoc)?$",
    r"^khong\s+co(?:\s+noi\s+dung)?$",
    r"^trong$",
    r"^n/?a$",
    r"^(?:b|bai|buoi|lesson|session|slot)\s*(?:so\s*)?\d+(?:\s*[-–—:.,]\s*)?$",
    r"^(?:kiem\s+tra\s+)?checkpoint(?:\s*(?:so\s*)?\d+)?$",
    r"^kiem\s+tra(?:\s+dinh\s+ky|\s+cuoi\s+khoa|\s+giua\s+khoa)?(?:\s*\d+)?$",
    r"^bai\s+kiem\s+tra(?:\s*\d+)?$",
    r"^test(?:\s*\d+)?$",
    r"^on\s+tap(?:\s+va\s+kiem\s+tra)?(?:\s*\d+)?$",
)

SUMMARY_PREFIX_STRIP_RE = re.compile(
    r"^\s*(?:(?:b|bai|bài|buoi|buổi|lesson|session|slot)\s*(?:hoc|học)?\s*(?:so|số)?\s*\d+\s*[-–—:.]?\s*"
    r"|(?:(?:kiem|kiểm)\s+tra\s+)?checkpoint\s*(?:so|số)?\s*\d*\s*[-–—:.]?\s*)+",
    flags=re.IGNORECASE,
)

AUTO_COMMENT_HEADER_RE = re.compile(
    r"^\s*(?:[-–—•*]*\s*(?:đánh|danh)\s+gi[aá]\s+chung\s*[:\-–—]?\s*\n?\s*)?"
    r"(?:nh[aậ]n\s+x[eé]t\s+bu[oổ]i\s+h[oọ]c\s+s[oố]\s*\d+\s*:[^\n]*\n?)?",
    flags=re.IGNORECASE,
)

STRENGTH_SIGNALS = (
    "diem manh",
    "uu diem",
    "tien bo",
    "nam bai",
    "nam vung",
    "hieu bai",
    "tiep thu",
    "hoan thanh",
    "lam tot",
    "thao tac",
    "chu dong",
    "tich cuc",
    "nhanh nhen",
    "sang tao",
    "tu duy",
    "lap rap",
    "lap trinh",
    "van dung",
    "ap dung",
)

IMPROVEMENT_SIGNALS = (
    "diem can cai thien",
    "can cai thien",
    "diem yeu",
    "han che",
    "can khac phuc",
    "khac phuc",
    "tuy nhien",
    "nhung",
    "doi luc",
    "doi khi",
    "van con",
    "con cham",
    "con nham",
    "con lung tung",
    "con quen",
    "chua thuc su",
    "chua chu dong",
    "chua can than",
    "chua chac chan",
    "can chu y",
    "chu y hon",
    "can tap trung hon",
    "can ren",
    "can on tap",
    "can danh them",
    "can manh dan",
    "can co gang",
    "van de",
    "luu y",
)

ADVICE_SIGNALS = (
    "loi khuyen",
    "goi y",
    "giai phap",
    "de xuat",
    "khuyen khich",
    "nen ",
    "can ren luyen",
    "ren luyen them",
    "luyen tap them",
    "on tap them",
    "ve nha",
    "bai tap",
    "tim hieu them",
    "thu suc",
    "phat huy",
    "duy tri",
    "co mong",
    "thay mong",
    "co tin",
    "thay tin",
)

EXPLICIT_STRENGTH_HEADERS = (
    "diem manh",
    "uu diem",
)

EXPLICIT_IMPROVEMENT_HEADERS = (
    "diem can cai thien",
    "can cai thien",
    "diem yeu",
    "han che",
    "van de can cai thien",
)

EXPLICIT_ADVICE_HEADERS = (
    "loi khuyen",
    "goi y giai phap",
    "giai phap",
    "de xuat",
    "huong khac phuc",
)

TOPIC_STOPWORDS = frozenset(
    {
        "bai",
        "buoi",
        "hoc",
        "so",
        "phan",
        "chu",
        "de",
        "noi",
        "dung",
        "tim",
        "hieu",
        "thuc",
        "hanh",
        "on",
        "tap",
        "va",
        "voi",
        "trong",
        "cua",
        "cho",
        "cac",
        "mot",
        "nhung",
        "duoc",
        "ve",
        "lam",
        "quen",
        "gioi",
        "thieu",
        "co",
        "ban",
        "nang",
        "cao",
        "tiep",
        "theo",
        "tong",
        "ket",
        "kiem",
        "tra",
    }
)


def _strip_html(raw: str | None) -> str:
    """Convert HTML fragment into clean plain text while preserving line breaks."""
    if not raw:
        return ""
    text = html.unescape(str(raw))
    text = re.sub(r"(?i)<\s*(?:br|/p|/div|/li|/tr|/h[1-6])\b[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _parse_lesson_number(lesson: str | int | None) -> int | None:
    return normalize_lesson_number(lesson)


def _is_robotics_class(class_code: str, khoi: str = "") -> bool:
    folded_khoi = fold_text(khoi)
    if "rob" in folded_khoi:
        return True
    upper_code = str(class_code or "").strip().upper()
    return bool(re.search(r"(?:^|[-_])ROB(?:[-_]|\d|$)", upper_code))


def _is_coding_class(class_code: str, khoi: str = "") -> bool:
    if _is_robotics_class(class_code, khoi):
        return False
    folded_khoi = fold_text(khoi)
    if "coding" in folded_khoi or "lap trinh" in folded_khoi:
        return True
    upper_code = str(class_code or "").strip().upper()
    return any(token in upper_code for token in CODING_TRACK_TOKENS)


def _has_checkpoint_rubric_in_slot(slot_data: dict[str, Any] | None) -> bool:
    if not isinstance(slot_data, dict):
        return False
    records = slot_data.get("studentAttendance")
    if not isinstance(records, list):
        return False
    for entry in records[:MAX_LMS_STUDENTS_PER_SLOT]:
        if not isinstance(entry, dict):
            continue
        folded = fold_text(_strip_html(entry.get("comment")))
        if not folded:
            continue
        pillar_hits = sum(
            1 for p in ("kien thuc", "ky nang", "thai do") if p in folded
        )
        pillar_header_hits = sum(
            1
            for p in ("kien thuc", "ky nang", "thai do")
            if re.search(
                rf"(?:^|\n)\s*(?:[-•*]|\d+[.)])?\s*{p}\s*(?::|-|–|—|\n|$)",
                folded,
            )
            or re.search(rf"(?:^|\n|-|•|\*|\d+[.)])\s*{p}\s*:", folded)
        )
        if ("diem nang luc" in folded and pillar_hits >= 2) or pillar_header_hits == 3:
            return True
    return False


def is_checkpoint_session(
    class_code: str,
    khoi: str = "",
    lesson: str | int | None = "",
    slot_data: dict[str, Any] | None = None,
) -> bool:
    """Return True when a session is a Checkpoint session (Coding B5/B9, Robotics B4/B8, or Rubric)."""
    lesson_num = _parse_lesson_number(lesson)
    if is_lesson_14(lesson):
        return False
    if lesson_num is not None:
        if _is_robotics_class(class_code, khoi):
            if lesson_num in {4, 8}:
                return True
        elif _is_coding_class(class_code, khoi) or class_code or khoi:
            if lesson_num in {5, 9}:
                return True
    if _has_checkpoint_rubric_in_slot(slot_data):
        return True
    return False


def evaluate_lms_slot_summary(summary_raw: str | None) -> dict[str, Any]:
    """Check whether `slot.summary` contains substantive lesson content (Requirement #3)."""
    clean_text = _strip_html(summary_raw)
    single_line = " ".join(clean_text.split()).strip()
    folded_full = fold_text(single_line).strip(" -–—:.,;!?()[]{}\"'")
    if not folded_full:
        return {
            "has_valid_summary": False,
            "summary_clean": "",
            "substance_text": "",
            "issue": "[LMS] Chưa ghi nội dung bài học",
        }
    for pattern in SUMMARY_PLACEHOLDER_PATTERNS:
        if re.fullmatch(pattern, folded_full):
            return {
                "has_valid_summary": False,
                "summary_clean": single_line,
                "substance_text": "",
                "issue": "[LMS] Chưa ghi nội dung bài học",
            }
    substance = SUMMARY_PREFIX_STRIP_RE.sub("", single_line).strip(" -–—:.,;!?")
    folded_substance = SUMMARY_PREFIX_STRIP_RE.sub(
        "",
        fold_text(substance),
    ).strip(" -–—:.,;!?()[]{}\"'")
    for pattern in SUMMARY_PLACEHOLDER_PATTERNS:
        if re.fullmatch(pattern, folded_substance):
            folded_substance = ""
            substance = ""
            break
    word_count = len([w for w in folded_substance.split() if len(w) >= 2])
    if len(folded_substance) < MIN_SUBSTANTIVE_SUMMARY_CHARS or word_count < 3:
        return {
            "has_valid_summary": False,
            "summary_clean": single_line,
            "substance_text": substance,
            "issue": "[LMS] Chưa ghi nội dung bài học",
        }
    return {
        "has_valid_summary": True,
        "summary_clean": single_line,
        "substance_text": substance,
        "issue": "",
    }


def _strip_comment_header_and_student_name(
    raw_comment: str | None,
    *,
    student_full_name: str = "",
) -> str:
    """Strip auto-generated session header and student name to inspect pure comment body."""
    plain = _strip_html(raw_comment)
    if not plain:
        return ""
    without_header = AUTO_COMMENT_HEADER_RE.sub("", plain).strip()
    if student_full_name:
        clean_name = " ".join(str(student_full_name).split()).strip()
        if len(clean_name) >= 3:
            without_header = re.sub(
                re.escape(clean_name),
                "",
                without_header,
                flags=re.IGNORECASE,
            ).strip()
    return without_header


def _is_valid_checkpoint_student_comment(body_text: str) -> bool:
    folded = fold_text(body_text)
    if not folded or len(folded) < 60:
        return False
    pillar_hits = sum(
        1 for p in ("kien thuc", "ky nang", "thai do") if p in folded
    )
    return pillar_hits == 3


def _is_valid_regular_student_comment(body_text: str) -> bool:
    """Check whether a regular session student comment covers all 3 required parts:
    1. Điểm mạnh (Strengths)
    2. Điểm cần cải thiện (Areas for improvement)
    3. Lời khuyên / Giải pháp (Actionable advice)
    """
    folded = fold_text(body_text)
    if not folded:
        return False

    has_explicit_strength = any(h in folded for h in EXPLICIT_STRENGTH_HEADERS)
    has_explicit_improvement = any(h in folded for h in EXPLICIT_IMPROVEMENT_HEADERS)
    has_explicit_advice = any(h in folded for h in EXPLICIT_ADVICE_HEADERS)
    if (
        has_explicit_strength
        and has_explicit_improvement
        and has_explicit_advice
        and len(folded) >= 80
    ):
        return True

    if len(folded) < MIN_REGULAR_COMMENT_CHARS:
        return False

    # Count distinct sentences / bullet clauses
    segments = [
        seg.strip()
        for seg in re.split(r"(?:[\n•\-*]+|[.!?]+(?:\s+|$))", body_text)
        if len(seg.strip()) >= 12
    ]
    if len(segments) < 3:
        return False

    has_strength = any(sig in folded for sig in STRENGTH_SIGNALS)
    has_improvement = any(sig in folded for sig in IMPROVEMENT_SIGNALS)
    has_advice = any(sig in folded for sig in ADVICE_SIGNALS)
    return bool(has_strength and has_improvement and has_advice)


def _normalize_comment_for_dedup(body_text: str) -> str:
    folded = fold_text(body_text)
    folded = re.sub(r"[^a-z0-9\s]", " ", folded)
    return " ".join(folded.split())


def evaluate_lms_student_comments(
    student_attendance: list[dict[str, Any]] | None,
    *,
    is_checkpoint: bool,
    lesson: str | int | None = "",
) -> dict[str, Any]:
    """Evaluate per-student LMS comments for attended students without exposing PII (Requirement #4)."""
    if is_lesson_14(lesson):
        return {
            "is_exempt": True,
            "is_checkpoint": False,
            "attended_count": 0,
            "missing_count": 0,
            "shallow_count": 0,
            "failed_count": 0,
            "duplicate_count": 0,
            "issues": [],
        }

    if not isinstance(student_attendance, list) or len(student_attendance) == 0:
        return {
            "is_exempt": False,
            "is_checkpoint": is_checkpoint,
            "attended_count": 0,
            "missing_count": 0,
            "shallow_count": 0,
            "failed_count": 0,
            "duplicate_count": 0,
            "issues": [
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Chưa kiểm tra được danh sách học viên trên LMS, cần kiểm tra thủ công",
                )
            ],
        }

    raw_total_records = len(student_attendance)
    overflow_records = raw_total_records > MAX_LMS_STUDENTS_PER_SLOT
    attended_entries: list[dict[str, Any]] = []
    for raw_entry in student_attendance[:MAX_LMS_STUDENTS_PER_SLOT]:
        if not isinstance(raw_entry, dict):
            continue
        status = str(raw_entry.get("status") or "").strip().upper()
        if status in ATTENDED_STATUSES:
            attended_entries.append(raw_entry)

    attended_count = len(attended_entries)
    if attended_count == 0:
        no_attended_issues: list[Any] = [
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                "[LMS] Chưa có học viên điểm danh có mặt trên LMS, cần kiểm tra thủ công",
            )
        ]
        if overflow_records:
            no_attended_issues.append(
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    f"[LMS] Danh sách học viên vượt giới hạn kiểm tra ({raw_total_records} > {MAX_LMS_STUDENTS_PER_SLOT} HS), cần kiểm tra thủ công",
                )
            )
        return {
            "is_exempt": False,
            "is_checkpoint": is_checkpoint,
            "attended_count": 0,
            "missing_count": 0,
            "shallow_count": 0,
            "failed_count": 0,
            "duplicate_count": 0,
            "issues": no_attended_issues,
        }

    missing_count = 0
    shallow_count = 0
    normalized_bodies: list[str] = []
    slot_has_rubric = is_checkpoint and _has_checkpoint_rubric_in_slot(
        {"studentAttendance": attended_entries}
    )

    for entry in attended_entries:
        student_obj = entry.get("student")
        full_name = (
            str(student_obj.get("fullName") or "")
            if isinstance(student_obj, dict)
            else ""
        )
        body = _strip_comment_header_and_student_name(
            entry.get("comment"),
            student_full_name=full_name,
        )
        if not body.strip():
            missing_count += 1
            continue
        norm = _normalize_comment_for_dedup(body)
        if norm:
            normalized_bodies.append(norm)
        if is_checkpoint:
            is_valid = (
                _is_valid_checkpoint_student_comment(body)
                if slot_has_rubric
                else (
                    _is_valid_checkpoint_student_comment(body)
                    or _is_valid_regular_student_comment(body)
                )
            )
        else:
            is_valid = _is_valid_regular_student_comment(body)
        if not is_valid:
            shallow_count += 1

    failed_count = missing_count + shallow_count
    issues: list[Any] = []

    if is_checkpoint:
        if missing_count == attended_count:
            issues.append(
                f"[LMS] Chưa nhận xét HS buổi Checkpoint ({missing_count}/{attended_count} HS)"
            )
        elif failed_count > 0:
            issues.append("[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực")
    else:
        if missing_count == attended_count:
            issues.append(
                f"[LMS] Chưa nhận xét học viên ({missing_count}/{attended_count} HS)"
            )
        elif missing_count > 0 and shallow_count > 0:
            issues.append(
                f"[LMS] Thiếu nhận xét học viên ({missing_count}/{attended_count} HS)"
            )
            issues.append("[LMS] Nhận xét thiếu ý")
        elif missing_count > 0:
            issues.append(
                f"[LMS] Thiếu nhận xét học viên ({missing_count}/{attended_count} HS)"
            )
        elif shallow_count > 0:
            issues.append("[LMS] Nhận xét thiếu ý")

    # Duplicate comment count is tracked as metadata only; do not flag copy-paste as an issue
    # as long as each comment satisfies the required 3-part structure.
    duplicate_count = 0
    if not is_checkpoint and len(normalized_bodies) >= 2:
        counts_by_body: dict[str, int] = {}
        for b in normalized_bodies:
            counts_by_body[b] = counts_by_body.get(b, 0) + 1
        dup_total = sum(c for c in counts_by_body.values() if c >= 2)
        if dup_total >= 2:
            duplicate_count = dup_total

    if overflow_records:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Danh sách học viên vượt giới hạn kiểm tra ({raw_total_records} > {MAX_LMS_STUDENTS_PER_SLOT} HS), cần kiểm tra thủ công",
            )
        )

    return {
        "is_exempt": False,
        "is_checkpoint": is_checkpoint,
        "attended_count": attended_count,
        "missing_count": missing_count,
        "shallow_count": shallow_count,
        "failed_count": failed_count,
        "duplicate_count": duplicate_count,
        "issues": issues,
    }


def evaluate_lms_slot_quality(
    slot_data: dict[str, Any] | None,
    *,
    class_code: str,
    khoi: str = "",
    lesson: str | int | None = "",
) -> dict[str, Any]:
    """Evaluate both lesson summary and student comments for one LMS slot."""
    if is_lesson_14(lesson):
        return {
            "is_exempt": True,
            "is_checkpoint": False,
            "has_valid_summary": True,
            "summary_clean": "",
            "attended_count": 0,
            "failed_comment_count": 0,
            "issues": [],
        }
    if not isinstance(slot_data, dict):
        return {
            "is_exempt": False,
            "is_checkpoint": is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": False,
            "summary_clean": "",
            "attended_count": 0,
            "failed_comment_count": 0,
            "issues": [
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Chưa kiểm tra được dữ liệu buổi học trên LMS, cần kiểm tra thủ công",
                )
            ],
        }

    if bool(slot_data.get("overflow_class_limit")):
        return {
            "is_exempt": False,
            "is_checkpoint": is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": False,
            "summary_clean": "",
            "attended_count": 0,
            "failed_comment_count": 0,
            "issues": [
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Vượt giới hạn số lớp kiểm tra LMS trong một đợt, cần kiểm tra thủ công",
                )
            ],
        }
    if bool(slot_data.get("duplicate_class")):
        return {
            "is_exempt": False,
            "is_checkpoint": is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": False,
            "summary_clean": "",
            "attended_count": 0,
            "failed_comment_count": 0,
            "issues": [
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Phát hiện trùng mã lớp trên LMS, cần kiểm tra thủ công",
                )
            ],
        }
    if bool(slot_data.get("missing_class")):
        return {
            "is_exempt": False,
            "is_checkpoint": is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": False,
            "summary_clean": "",
            "attended_count": 0,
            "failed_comment_count": 0,
            "issues": [
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Không tìm thấy dữ liệu lớp trên LMS, cần kiểm tra thủ công",
                )
            ],
        }
    if bool(slot_data.get("fetch_error")):
        return {
            "is_exempt": False,
            "is_checkpoint": is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": False,
            "summary_clean": "",
            "attended_count": 0,
            "failed_comment_count": 0,
            "issues": [
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Không tải được đầy đủ dữ liệu buổi học từ LMS, cần kiểm tra thủ công",
                )
            ],
        }
    checkpoint = is_checkpoint_session(
        class_code,
        khoi,
        lesson,
        slot_data=slot_data,
    )
    summary_eval = evaluate_lms_slot_summary(slot_data.get("summary"))
    comments_eval = evaluate_lms_student_comments(
        slot_data.get("studentAttendance"),
        is_checkpoint=checkpoint,
        lesson=lesson,
    )
    issues: list[Any] = []
    if summary_eval["issue"]:
        issues.append(summary_eval["issue"])
    issues.extend(comments_eval["issues"])
    if bool(slot_data.get("truncated")) and not any(
        "vượt giới hạn" in issue_text(item) for item in issues
    ):
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                "[LMS] Dữ liệu buổi học trên LMS vượt giới hạn kiểm tra, cần kiểm tra thủ công",
            )
        )
    return {
        "is_exempt": False,
        "is_checkpoint": checkpoint,
        "has_valid_summary": bool(summary_eval["has_valid_summary"]),
        "summary_clean": str(summary_eval["summary_clean"] or ""),
        "attended_count": int(comments_eval["attended_count"]),
        "failed_comment_count": int(comments_eval["failed_count"]),
        "issues": issues,
    }


def extract_report_lesson_title(raw_content: str | None) -> str:
    """Extract an explicit lesson title/topic line from a Zalo report without capturing student PII."""
    if not raw_content:
        return ""
    for line in str(raw_content).splitlines():
        clean_line = " ".join(line.split()).strip(" -–—•*📌📝📚🎯✅🔹")
        if not clean_line:
            continue
        m = re.match(
            r"^(?:t[eê]n\s+b[aà]i(?:\s+h[oọ]c)?|ch[uủ]\s+[dđ][eề](?:\s+b[aà]i\s+h[oọ]c)?|"
            r"n[oộ]i\s+dung\s+b[aà]i\s+h[oọ]c|b[aà]i\s+h[oọ]c\s+h[oô]m\s+nay)\s*[:\-–—]\s*(.+)$",
            clean_line,
            flags=re.IGNORECASE,
        )
        if m:
            title = m.group(1).strip(" .;:!|-–—")
            if 3 <= len(title) <= 120:
                return title
    return ""


def _topic_keywords(text: str | None) -> set[str]:
    folded = fold_text(text or "")
    folded = SUMMARY_PREFIX_STRIP_RE.sub("", folded)
    tokens = re.findall(r"[a-z0-9]{3,}", folded)
    return {t for t in tokens if t not in TOPIC_STOPWORDS and not t.isdigit()}


def evaluate_zalo_content_issues(
    item: dict[str, Any],
    *,
    lms_summary_text: str = "",
) -> list[str]:
    """Inspect a Zalo scan result for wrong date, wrong lesson number, or wrong lesson title."""
    if not isinstance(item, dict):
        return []
    evidence_state = str(item.get("evidence_state") or "").strip().upper()
    if is_lesson_14(item) or evidence_state not in SUBMITTED_EVIDENCE_STATES:
        return []
    issues: list[str] = []
    seen: set[str] = set()

    def _add(msg: str, *, code: str = "", severity: str = "") -> None:
        cleaned = " ".join(str(msg or "").split()).strip(" ;")
        if not cleaned:
            return
        cleaned = cleaned.replace("; ", " — ")
        if not cleaned.startswith("["):
            cleaned = f"[Zalo] {cleaned}"
        cleaned = normalize_auto_issue_text(cleaned)
        norm = fold_text(re.sub(r"^\[[^\]]+\]\s*", "", cleaned))
        if norm and norm not in seen:
            seen.add(norm)
            issues.append(
                StructuredIssueStr(cleaned, code=code, severity=severity)
            )

    raw_st = item.get("scan_target")
    st: dict[str, Any] = raw_st if isinstance(raw_st, dict) else {}
    raw_ev = item.get("evidence")
    ev: dict[str, Any] = raw_ev if isinstance(raw_ev, dict) else {}
    raw_ctx = ev.get("date_context")
    ctx: dict[str, Any] = raw_ctx if isinstance(raw_ctx, dict) else {}
    raw_clf = ev.get("classifier_result")
    clf: dict[str, Any] = raw_clf if isinstance(raw_clf, dict) else {}
    raw_cands = clf.get("candidates")
    candidates: list[Any] = raw_cands if isinstance(raw_cands, list) else []
    matched_cand: dict[str, Any] = {}
    for cand in reversed(candidates):
        if not isinstance(cand, dict):
            continue
        raw_cand_tm = cand.get("target_match")
        if isinstance(raw_cand_tm, dict):
            if raw_cand_tm.get("matches") is True:
                matched_cand = cand
                break
            continue
        if str(cand.get("decision") or "").strip().upper().startswith("SUBMITTED"):
            matched_cand = cand
            break
    raw_tm = matched_cand.get("target_match")
    tm: dict[str, Any] = (
        raw_tm
        if isinstance(raw_tm, dict) and raw_tm.get("matches") is True
        else {}
    )

    target_match_decision = str(
        ctx.get("target_match_decision")
        or tm.get("decision")
        or ""
    ).strip().upper()
    target_match_note = str(
        ctx.get("target_match_note")
        or tm.get("note")
        or ""
    ).strip()
    if target_match_note:
        note_code = ""
        note_sev = ""
        if target_match_decision == "TARGET_SAME_DAY_NO_DATE":
            note_code = ISSUE_CODE_ZALO_REPORT_NO_DATE
            note_sev = SEVERITY_NOTE
        elif target_match_decision == "TARGET_SAME_DAY_WRONG_DATE":
            note_code = ISSUE_CODE_ZALO_REPORT_WRONG_DATE
            note_sev = SEVERITY_NOTE
        _add(target_match_note, code=note_code, severity=note_sev)

    raw_lesson = st.get("lesson") or ctx.get("target_lesson") or item.get("lesson")
    if is_lesson_14(raw_lesson):
        return []
    expected_lesson = _parse_lesson_number(raw_lesson)
    content_lesson = _parse_lesson_number(
        ctx.get("content_lesson")
        or matched_cand.get("content_lesson")
        or item.get("content_lesson")
    )
    folded_match_note = fold_text(target_match_note)
    already_mentions_wrong_lesson = (
        "sai buoi" in folded_match_note
        or "sai ngay/buoi" in folded_match_note
        or "ngay/buoi ghi trong noi dung khong khop" in folded_match_note
        or (
            content_lesson is not None
            and f"bai so {content_lesson}" in folded_match_note
        )
    )
    if (
        expected_lesson is not None
        and content_lesson is not None
        and content_lesson != expected_lesson
        and not already_mentions_wrong_lesson
    ):
        _add(
            f"Ghi sai số bài học trong nội dung (ghi Bài {content_lesson}, lịch là Buổi {expected_lesson})"
        )

    reported_title = str(
        ctx.get("reported_lesson_title")
        or matched_cand.get("reported_lesson_title")
        or item.get("reported_lesson_title")
        or extract_report_lesson_title(item.get("raw_content"))
        or ""
    ).strip()
    clean_lms_summary = str(
        lms_summary_text or item.get("lms_lesson_summary") or ""
    ).strip()

    if reported_title and clean_lms_summary:
        summary_check = evaluate_lms_slot_summary(clean_lms_summary)
        if summary_check["has_valid_summary"]:
            zalo_kw = _topic_keywords(reported_title)
            lms_kw = _topic_keywords(summary_check["substance_text"])
            folded_zalo = fold_text(reported_title)
            folded_lms = fold_text(summary_check["substance_text"])
            if (
                len(zalo_kw) >= 2
                and len(lms_kw) >= 2
                and not (zalo_kw & lms_kw)
                and folded_zalo not in folded_lms
                and folded_lms not in folded_zalo
            ):
                _add("Tên bài học trên Zalo không khớp với nội dung bài trên LMS")

    return issues


async def fetch_lms_class_slots_via_page(
    page: Any,
    *,
    class_names: list[str],
    timeout_ms: int = 12000,
) -> dict[str, list[dict[str, Any]]]:
    """Query MindX LMS GraphQL for slot summaries and studentAttendance of the given classes.

    Strictly fail-safe and isolated: returns `{}` when `ZALO_QUALITY_ISOLATED == "1"`,
    when `page` has no authenticated LMS token, or when any network/GraphQL error occurs.
    """
    if os.getenv("ZALO_QUALITY_ISOLATED") == "1":
        return {}
    all_codes: list[str] = []
    for raw in class_names:
        code = str(raw or "").strip().upper()
        if code and code not in all_codes:
            all_codes.append(code)
    if not all_codes:
        return {}
    normalized_codes = all_codes[:MAX_LMS_QUALITY_CLASSES_PER_BATCH]
    overflow_codes = all_codes[MAX_LMS_QUALITY_CLASSES_PER_BATCH:]
    if page is None or not hasattr(page, "evaluate"):
        return {
            code: [{"fetch_error": True} for _ in range(MAX_LMS_SLOTS_PER_CLASS)]
            for code in all_codes
        }

    pre_resolved_token = ""
    ctx_obj = getattr(page, "context", None)
    if ctx_obj is not None and callable(getattr(ctx_obj, "storage_state", None)):
        try:
            state = await ctx_obj.storage_state()
            if isinstance(state, dict):
                for origin_entry in state.get("origins") or []:
                    if not isinstance(origin_entry, dict):
                        continue
                    origin_url = str(origin_entry.get("origin") or "").strip().lower()
                    parsed_origin = urlsplit(origin_url)
                    if parsed_origin.hostname != LMS_ALLOWED_HOSTNAME:
                        continue
                    for ls_item in origin_entry.get("localStorage") or []:
                        if not isinstance(ls_item, dict):
                            continue
                        k = str(ls_item.get("name") or "").strip().lower()
                        raw_v = str(ls_item.get("value") or "").strip()
                        v = raw_v.strip('"')
                        if k in ALLOWED_LMS_TOKEN_KEYS and len(v) > 20:
                            pre_resolved_token = v
                            if k == "id_token":
                                break
                        elif v.startswith("eyJ") and len(v) > 40:
                            pre_resolved_token = v
                        elif raw_v.startswith("{"):
                            with contextlib.suppress(Exception):
                                parsed_ls = json.loads(raw_v)
                                if isinstance(parsed_ls, dict):
                                    sts = parsed_ls.get("stsTokenManager")
                                    acc = (
                                        sts.get("accessToken")
                                        if isinstance(sts, dict)
                                        else None
                                    )
                                    if isinstance(acc, str) and len(acc) > 20:
                                        pre_resolved_token = acc
                                    elif (
                                        isinstance(parsed_ls.get("idToken"), str)
                                        and len(parsed_ls["idToken"]) > 20
                                    ):
                                        pre_resolved_token = parsed_ls["idToken"]
                    if pre_resolved_token:
                        break
        except Exception:
            pre_resolved_token = ""

    eval_page = page
    temp_page = None
    captured_auth: dict[str, str] = {}
    resolved_centres: list[str] = []

    with contextlib.suppress(Exception):
        from googleform_autosubmit.config import Config as _Cfg

        cfg_lms_url = str(getattr(_Cfg, "LMS_URL", "") or "").strip()
        if cfg_lms_url:
            raw_centres_qs = parse_qs(urlsplit(cfg_lms_url).query).get("centres") or []
            for raw_c in raw_centres_qs:
                parsed_c = json.loads(raw_c)
                if isinstance(parsed_c, list):
                    for cid in parsed_c:
                        s_cid = str(cid or "").strip()
                        if s_cid and s_cid not in resolved_centres:
                            resolved_centres.append(s_cid)

    def _on_lms_request(req: Any) -> None:
        with contextlib.suppress(Exception):
            req_url = str(getattr(req, "url", "") or "").strip()
            parsed_req = urlsplit(req_url)
            req_scheme = str(parsed_req.scheme or "").lower()
            req_host = str(parsed_req.hostname or "").lower()
            req_method = str(getattr(req, "method", "") or "").upper()
            if (
                req_scheme == "https"
                and req_host == "lms-api.mindx.edu.vn"
                and req_method == "POST"
            ):
                hdrs = getattr(req, "headers", None) or {}
                if isinstance(hdrs, dict):
                    for hk, hv in hdrs.items():
                        val = str(hv or "").strip()
                        if len(val) <= 20:
                            continue
                        if str(hk).lower() == "authorization":
                            captured_auth["token"] = val
                        elif str(hk).lower() == "token" and "token" not in captured_auth:
                            captured_auth["token"] = val
                post_data = str(getattr(req, "post_data", "") or "")
                if post_data.startswith("{"):
                    body_json = json.loads(post_data)
                    req_vars = (
                        body_json.get("variables")
                        if isinstance(body_json, dict)
                        else None
                    )
                    if isinstance(req_vars, dict) and isinstance(
                        req_vars.get("centres"), list
                    ):
                        for cid in req_vars["centres"]:
                            s_cid = str(cid or "").strip()
                            if s_cid and s_cid not in resolved_centres:
                                resolved_centres.append(s_cid)

    js_script = """
    async ({ endpoint, classCodes, centres, maxSlots, maxStudents, timeoutMs, preResolvedToken, allowedHostname, allowedTokenKeys }) => {
        const findToken = async () => {
            if (preResolvedToken && preResolvedToken.length > 20) {
                return preResolvedToken;
            }
            try {
                const currentHost = String((window.location && window.location.hostname) || '').toLowerCase();
                if (currentHost !== allowedHostname) {
                    return '';
                }
                const keySet = new Set(allowedTokenKeys || []);
                if (typeof localStorage !== 'undefined' && localStorage) {
                    for (let i = 0; i < localStorage.length; i++) {
                        const rawKey = String(localStorage.key(i) || '');
                        const k = rawKey.trim().toLowerCase();
                        const rawVal = String(localStorage.getItem(rawKey) || '').trim();
                        const v = rawVal.replace(/^"|"$/g, '');
                        if (keySet.has(k) && v.length > 20) {
                            return v;
                        }
                        if (v.startsWith('eyJ') && v.length > 40) {
                            return v;
                        }
                        if (rawVal.startsWith('{')) {
                            try {
                                const parsed = JSON.parse(rawVal);
                                const sts = parsed && parsed.stsTokenManager && parsed.stsTokenManager.accessToken;
                                if (typeof sts === 'string' && sts.length > 20) {
                                    return sts;
                                }
                                if (parsed && typeof parsed.idToken === 'string' && parsed.idToken.length > 20) {
                                    return parsed.idToken;
                                }
                            } catch (_) {}
                        }
                    }
                }
                if (typeof indexedDB !== 'undefined' && indexedDB && typeof indexedDB.open === 'function') {
                    const idbToken = await new Promise((resolve) => {
                        let settled = false;
                        const finish = (val) => {
                            if (!settled) {
                                settled = true;
                                resolve(val || '');
                            }
                        };
                        const timer = setTimeout(() => finish(''), 1500);
                        try {
                            const req = indexedDB.open('firebaseLocalStorageDb');
                            req.onerror = () => { clearTimeout(timer); finish(''); };
                            req.onsuccess = () => {
                                try {
                                    const db = req.result;
                                    if (!db || !db.objectStoreNames || !db.objectStoreNames.contains('firebaseLocalStorage')) {
                                        if (db && typeof db.close === 'function') db.close();
                                        clearTimeout(timer);
                                        finish('');
                                        return;
                                    }
                                    const tx = db.transaction('firebaseLocalStorage', 'readonly');
                                    const store = tx.objectStore('firebaseLocalStorage');
                                    const getAllReq = store.getAll();
                                    getAllReq.onerror = () => { db.close(); clearTimeout(timer); finish(''); };
                                    getAllReq.onsuccess = () => {
                                        let found = '';
                                        const rows = Array.isArray(getAllReq.result) ? getAllReq.result : [];
                                        for (const row of rows) {
                                            const val = row && row.value;
                                            const access = val && val.stsTokenManager && val.stsTokenManager.accessToken;
                                            if (typeof access === 'string' && access.length > 20) {
                                                found = access;
                                                break;
                                            }
                                        }
                                        db.close();
                                        clearTimeout(timer);
                                        finish(found);
                                    };
                                } catch (_) {
                                    clearTimeout(timer);
                                    finish('');
                                }
                            };
                        } catch (_) {
                            clearTimeout(timer);
                            finish('');
                        }
                    });
                    if (idbToken) {
                        return idbToken;
                    }
                }
            } catch (_) {}
            return '';
        };
        const out = {};
        const token = await findToken();
        if (!token) {
            for (const code of classCodes) {
                out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
            }
            return out;
        }
        const query = `
            query classesMissAttendOrComment($type: [String], $search: String, $centres: [String], $pageIndex: Int, $itemsPerPage: Int, $orderBy: String, $status: [String]) {
                classesMissAttendOrComment(
                    payload: {
                        type: $type,
                        filter_textSearch: $search,
                        centre_in: $centres,
                        pageIndex: $pageIndex,
                        itemsPerPage: $itemsPerPage,
                        orderBy: $orderBy,
                        status_in: $status
                    }
                ) {
                    data {
                        name
                        slots {
                            _id
                            index
                            date
                            startTime
                            endTime
                            summary
                            studentAttendance {
                                student {
                                    fullName
                                }
                                status
                                comment
                            }
                        }
                    }
                }
            }
        `;
        const bearerHeader = token.startsWith('Bearer ') ? token : `Bearer ${token}`;
        const rawTokenHeader = token.replace(/^Bearer\\s+/i, '');
        const formatItemSlots = (item) => {
            if (!item || !Array.isArray(item.slots) || item.slots.length === 0) {
                return Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
            }
            const slotsTruncated = item.slots.length > maxSlots;
            return item.slots.slice(0, maxSlots).map((s) => {
                const rawAtt = Array.isArray(s && s.studentAttendance) ? s.studentAttendance : null;
                const attTruncated = Array.isArray(rawAtt) && rawAtt.length > maxStudents;
                return {
                    _id: (s && s._id != null) ? String(s._id).slice(0, 64) : '',
                    index: (s && typeof s.index === 'number' && Number.isFinite(s.index)) ? s.index : null,
                    date: (s && typeof s.date === 'string') ? s.date.slice(0, 64) : '',
                    startTime: (s && typeof s.startTime === 'string') ? s.startTime.slice(0, 64) : '',
                    endTime: (s && typeof s.endTime === 'string') ? s.endTime.slice(0, 64) : '',
                    ...(s && typeof s.status === 'string' ? { status: s.status.slice(0, 64) } : {}),
                    ...(s && typeof s.cancelled === 'boolean' ? { cancelled: s.cancelled } : {}),
                    ...(s && typeof s.isCancelled === 'boolean' ? { isCancelled: s.isCancelled } : {}),
                    summary: (s && typeof s.summary === 'string') ? s.summary.slice(0, 6000) : '',
                    truncated: Boolean(slotsTruncated || attTruncated),
                    studentAttendance: Array.isArray(rawAtt)
                        ? rawAtt.slice(0, maxStudents).map((att) => {
                            const fullName = String((att && att.student && att.student.fullName) || '').trim();
                            let cleanComment = (att && typeof att.comment === 'string') ? att.comment.slice(0, 15000) : '';
                            if (fullName && fullName.length >= 3 && cleanComment) {
                                cleanComment = cleanComment.split(fullName).join('');
                            }
                            return {
                                status: String((att && att.status) || ''),
                                comment: cleanComment
                            };
                        })
                        : null
                };
            });
        };

        // Tier 1: Bulk fetch when querying > 3 classes (avoids N-query rate limits on LMS)
        if (classCodes.length > 3) {
            const bulkItems = [];
            let bulkComplete = false;
            const activeCentres = (Array.isArray(centres) && centres.length > 0) ? centres : undefined;
            for (let pageIdx = 0; pageIdx < 5; pageIdx++) {
                const bulkTimeout = Math.max(timeoutMs || 12000, 25000);
                const ctrl = new AbortController();
                const timer = setTimeout(() => ctrl.abort(), bulkTimeout);
                try {
                    const vars = {
                        type: [],
                        status: ['FINISHED', 'RUNNING'],
                        pageIndex: pageIdx,
                        itemsPerPage: 100,
                        orderBy: 'createdAt_desc'
                    };
                    if (activeCentres) {
                        vars.centres = activeCentres;
                    }
                    const resp = await fetch(endpoint, {
                        method: 'POST',
                        headers: {
                            'content-type': 'application/json',
                            'authorization': bearerHeader,
                            'token': rawTokenHeader
                        },
                        body: JSON.stringify({
                            operationName: 'classesMissAttendOrComment',
                            variables: vars,
                            query
                        }),
                        signal: ctrl.signal
                    });
                    if (!resp.ok) {
                        bulkComplete = false;
                        break;
                    }
                    const payload = await resp.json();
                    if (payload && Array.isArray(payload.errors) && payload.errors.length > 0) {
                        bulkComplete = false;
                        break;
                    }
                    const rawData = (((payload || {}).data || {}).classesMissAttendOrComment || {}).data;
                    if (!Array.isArray(rawData)) {
                        bulkComplete = false;
                        break;
                    }
                    bulkItems.push(...rawData);
                    if (rawData.length < 100) {
                        bulkComplete = true;
                        break;
                    }
                } catch (_) {
                    bulkComplete = false;
                    break;
                } finally {
                    clearTimeout(timer);
                }
            }
            if (bulkComplete && bulkItems.length > 0) {
                for (const code of classCodes) {
                    const matched = bulkItems.filter((item) => {
                        const itemName = String((item && item.name) || '').trim().toUpperCase();
                        return itemName === code;
                    });
                    if (matched.length > 1) {
                        out[code] = Array.from({ length: maxSlots }, () => ({
                            fetch_error: true,
                            duplicate_class: true
                        }));
                    } else if (matched.length === 1) {
                        out[code] = formatItemSlots(matched[0]);
                    }
                }
            }
        }

        // Tier 2: Per-code fetch for small batches (<= 3) or any code not returned in Tier 1
        const fetchOneCode = async (code) => {
            for (let attempt = 0; attempt < 2; attempt++) {
                const ctrl = new AbortController();
                const timer = setTimeout(() => ctrl.abort(), timeoutMs);
                try {
                    const resp = await fetch(endpoint, {
                        method: 'POST',
                        headers: {
                            'content-type': 'application/json',
                            'authorization': bearerHeader,
                            'token': rawTokenHeader
                        },
                        body: JSON.stringify({
                            operationName: 'classesMissAttendOrComment',
                            variables: {
                                type: [],
                                search: code,
                                status: ['FINISHED', 'RUNNING'],
                                pageIndex: 0,
                                itemsPerPage: 20,
                                orderBy: 'createdAt_desc'
                            },
                            query
                        }),
                        signal: ctrl.signal
                    });
                    if (!resp.ok) {
                        if (attempt === 0) {
                            await new Promise((r) => setTimeout(r, 300));
                            continue;
                        }
                        out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
                        return;
                    }
                    const payload = await resp.json();
                    if (payload && Array.isArray(payload.errors) && payload.errors.length > 0) {
                        if (attempt === 0) {
                            await new Promise((r) => setTimeout(r, 300));
                            continue;
                        }
                        out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
                        return;
                    }
                    const list = (((payload || {}).data || {}).classesMissAttendOrComment || {}).data || [];
                    const matched = list.filter((item) => {
                        const itemName = String((item && item.name) || '').trim().toUpperCase();
                        return itemName === code;
                    });
                    if (matched.length > 1) {
                        out[code] = Array.from({ length: maxSlots }, () => ({
                            fetch_error: true,
                            duplicate_class: true
                        }));
                        return;
                    }
                    if (matched.length === 0) {
                        out[code] = Array.from({ length: maxSlots }, () => ({
                            fetch_error: true,
                            missing_class: true
                        }));
                        return;
                    }
                    out[code] = formatItemSlots(matched[0]);
                    return;
                } catch (_) {
                    if (attempt === 0) {
                        await new Promise((r) => setTimeout(r, 300));
                        continue;
                    }
                    out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
                } finally {
                    clearTimeout(timer);
                }
            }
        };
        const remainingCodes = classCodes.filter((code) => !out[code]);
        if (remainingCodes.length > 0) {
            const concurrency = Math.min(3, remainingCodes.length || 1);
            let cursor = 0;
            const workers = Array.from({ length: concurrency }, async () => {
                while (cursor < remainingCodes.length) {
                    const idx = cursor++;
                    await fetchOneCode(remainingCodes[idx]);
                }
            });
            await Promise.all(workers);
        }
        return out;
    }
    """
    listener_page = None
    try:
        page_mod = str(getattr(type(page), "__module__", "") or "")
        current_page_url = str(getattr(page, "url", "") or "").strip()
        parsed_page_url = urlsplit(current_page_url)
        current_host = str(parsed_page_url.hostname or "").lower()
        if not pre_resolved_token and not page_mod.startswith("unittest.mock"):
            with contextlib.suppress(Exception):
                from googleform_autosubmit.config import Config
                from services.lms_service import _login_lms_if_needed

                lms_url = str(getattr(Config, "LMS_URL", "") or "").strip()
                parsed_lms_url = urlsplit(lms_url)
                lms_scheme = str(parsed_lms_url.scheme or "").lower()
                lms_host = str(parsed_lms_url.hostname or "").lower()
                if lms_scheme == "https" and lms_host == LMS_ALLOWED_HOSTNAME:
                    if current_host != LMS_ALLOWED_HOSTNAME:
                        if parsed_page_url.scheme in {"", "about"} and callable(
                            getattr(page, "goto", None)
                        ):
                            eval_page = page
                        elif ctx_obj is not None and callable(
                            getattr(ctx_obj, "new_page", None)
                        ):
                            temp_page = await ctx_obj.new_page()
                            eval_page = temp_page
                    if eval_page is not None and callable(getattr(eval_page, "goto", None)):
                        if callable(getattr(eval_page, "on", None)):
                            with contextlib.suppress(Exception):
                                eval_page.on("request", _on_lms_request)
                                listener_page = eval_page
                        eval_parsed = urlsplit(str(getattr(eval_page, "url", "") or "").strip())
                        eval_host = str(eval_parsed.hostname or "").lower()
                        if eval_host != LMS_ALLOWED_HOSTNAME:
                            await eval_page.goto(lms_url)
                            if callable(getattr(eval_page, "wait_for_load_state", None)):
                                with contextlib.suppress(Exception):
                                    await eval_page.wait_for_load_state("networkidle")
                            post_nav = urlsplit(str(getattr(eval_page, "url", "") or "").strip())
                            if (
                                str(post_nav.scheme or "").lower() == "https"
                                and str(post_nav.hostname or "").lower() == LMS_ALLOWED_HOSTNAME
                            ):
                                await _login_lms_if_needed(eval_page)
                                for _ in range(20):
                                    if captured_auth.get("token"):
                                        pre_resolved_token = captured_auth["token"]
                                        break
                                    await asyncio.sleep(0.25)
                            else:
                                return {
                                    code: [
                                        {"fetch_error": True}
                                        for _ in range(MAX_LMS_SLOTS_PER_CLASS)
                                    ]
                                    for code in all_codes
                                }

        raw_res = await eval_page.evaluate(
            js_script,
            {
                "endpoint": LMS_GRAPHQL_ENDPOINT,
                "classCodes": normalized_codes,
                "centres": resolved_centres,
                "maxSlots": MAX_LMS_SLOTS_PER_CLASS,
                "maxStudents": MAX_LMS_STUDENTS_PER_SLOT,
                "timeoutMs": int(timeout_ms),
                "preResolvedToken": pre_resolved_token,
                "allowedHostname": LMS_ALLOWED_HOSTNAME,
                "allowedTokenKeys": sorted(ALLOWED_LMS_TOKEN_KEYS),
            },
        )
        if not isinstance(raw_res, dict):
            return {
                code: [{"fetch_error": True} for _ in range(MAX_LMS_SLOTS_PER_CLASS)]
                for code in all_codes
            }
        sanitized: dict[str, list[dict[str, Any]]] = {}
        for k, v in raw_res.items():
            code = str(k or "").strip().upper()
            if code and isinstance(v, list):
                sanitized[code] = [
                    slot for slot in v[:MAX_LMS_SLOTS_PER_CLASS] if isinstance(slot, dict)
                ]
        for code in normalized_codes:
            if code not in sanitized or not sanitized[code]:
                sanitized[code] = [
                    {"fetch_error": True, "missing_class": True}
                    for _ in range(MAX_LMS_SLOTS_PER_CLASS)
                ]
        for code in overflow_codes:
            sanitized[code] = [
                {"fetch_error": True, "overflow_class_limit": True}
                for _ in range(MAX_LMS_SLOTS_PER_CLASS)
            ]
        return sanitized
    except Exception:
        return {
            code: [{"fetch_error": True} for _ in range(MAX_LMS_SLOTS_PER_CLASS)]
            for code in all_codes
        }
    finally:
        if listener_page is not None:
            for detach_name in ("remove_listener", "off"):
                detach_fn = getattr(listener_page, detach_name, None)
                if callable(detach_fn):
                    with contextlib.suppress(Exception):
                        detach_fn("request", _on_lms_request)
                    break
        if temp_page is not None and callable(getattr(temp_page, "close", None)):
            with contextlib.suppress(Exception):
                await temp_page.close()


def _slot_timestamp_to_hcm_date_iso(raw_val: Any) -> str | None:
    """Convert an LMS slot date/startTime string to Asia/Ho_Chi_Minh YYYY-MM-DD."""
    if not isinstance(raw_val, str):
        return None
    text = raw_val.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    iso_text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        dt = datetime.fromisoformat(iso_text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(BUSINESS_TIMEZONE).date().isoformat()


def _extract_lms_slot_date_iso(slot: Mapping[str, Any]) -> str | None:
    """Return the slot's YYYY-MM-DD in Asia/Ho_Chi_Minh, or None if missing/conflicting."""
    parsed_dates: set[str] = set()
    has_raw_date = False
    for key in ("date", "startTime", "start_time", "class_date_iso", "slot_date"):
        raw = slot.get(key)
        if raw is not None and str(raw).strip():
            has_raw_date = True
            converted = _slot_timestamp_to_hcm_date_iso(raw)
            if converted is None:
                return None
            parsed_dates.add(converted)
    if not has_raw_date or len(parsed_dates) != 1:
        return None
    return next(iter(parsed_dates))


def _extract_lms_slot_lesson_number(slot: Mapping[str, Any]) -> tuple[bool, int | None]:
    """Return (has_lesson_field, 1-based lesson number) for a ClassSlot."""
    if "index" in slot and slot.get("index") is not None:
        raw_idx = slot.get("index")
        if isinstance(raw_idx, int) and not isinstance(raw_idx, bool) and raw_idx >= 0:
            return True, raw_idx + 1
        return True, None
    if "lesson" in slot and slot.get("lesson") is not None:
        return True, _parse_lesson_number(slot.get("lesson"))
    return False, None


def _is_lms_slot_cancelled(slot: Mapping[str, Any]) -> bool:
    """Return True only when a slot carries an explicit verified cancellation marker."""
    if (
        slot.get("cancelled") is True
        or slot.get("isCancelled") is True
        or slot.get("is_cancelled") is True
    ):
        return True
    status_val = str(slot.get("status") or "").strip().upper()
    return status_val in {"CANCELLED", "CANCELED", "CANCEL", "HUY", "ĐÃ HỦY", "DA HUY"}


def _resolve_target_class_date_iso(
    result: Mapping[str, Any],
    st_dict: Mapping[str, Any] | None,
    ctx: Mapping[str, Any],
) -> str | None:
    """Resolve the authoritative BCNB target date (YYYY-MM-DD) for LMS slot matching."""
    for candidate in (
        st_dict.get("class_date_iso") if st_dict else None,
        st_dict.get("ngay_hoc_iso") if st_dict else None,
        ctx.get("target_class_date"),
        ctx.get("bcnb_class_date_iso"),
        result.get("ngay_hoc_iso"),
        result.get("class_date_iso"),
    ):
        iso_val = _slot_timestamp_to_hcm_date_iso(candidate)
        if iso_val is not None:
            return iso_val
    raw_year = (
        (st_dict.get("year") if st_dict else None)
        or ctx.get("target_year")
        or result.get("year")
    )
    default_year: int | None = None
    with contextlib.suppress(TypeError, ValueError):
        parsed_year = int(str(raw_year).strip())
        if 2000 <= parsed_year <= 2100:
            default_year = parsed_year
    for raw_date in (
        st_dict.get("class_date") if st_dict else None,
        st_dict.get("ngay_hoc") if st_dict else None,
        ctx.get("bcnb_class_date"),
        result.get("ngay_hoc"),
        result.get("class_date"),
    ):
        text = str(raw_date or "").strip()
        if not text:
            continue
        iso_direct = _slot_timestamp_to_hcm_date_iso(text)
        if iso_direct is not None:
            return iso_direct
        if default_year is not None or re.search(r"\b\d{4}\b", text):
            parsed = parse_class_date(
                text,
                default_year=default_year or 2026,
                timezone_info=BUSINESS_TIMEZONE,
            )
            if parsed is not None:
                return parsed.date().isoformat()
    return None


def select_matching_lms_slot(
    class_slots: Sequence[Mapping[str, Any]],
    *,
    target_date_iso: str | None,
    lesson_num: int,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Select the unique LMS slot matching target_date_iso and lesson_num.

    Returns ``(matched_slot, None)`` when exactly 1 active slot matches, or
    ``(None, manual_check_issue)`` in all other cases (never falls back by position).
    """
    if lesson_num > MAX_LMS_SLOTS_PER_CLASS:
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Buổi {lesson_num} vượt giới hạn kiểm tra LMS ({MAX_LMS_SLOTS_PER_CLASS} buổi), cần kiểm tra thủ công",
            ),
        )
    if not target_date_iso:
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Thiếu ngày học của Buổi {lesson_num} để đối chiếu LMS, cần kiểm tra thủ công",
            ),
        )
    dict_slots = [dict(s) for s in class_slots if isinstance(s, Mapping)]
    if not dict_slots:
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                "[LMS] Chưa kiểm tra được dữ liệu buổi học trên LMS, cần kiểm tra thủ công",
            ),
        )
    active_slots = [s for s in dict_slots if not _is_lms_slot_cancelled(s)]
    if not active_slots:
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Slot ngày {target_date_iso} (Buổi {lesson_num}) trên LMS đã bị huỷ, cần kiểm tra thủ công",
            ),
        )
    matched_slots: list[dict[str, Any]] = []
    wrong_lesson_on_date: list[dict[str, Any]] = []
    missing_lesson_on_date: list[dict[str, Any]] = []
    has_any_valid_date = False
    cancelled_on_target_date = any(
        _is_lms_slot_cancelled(s) and _extract_lms_slot_date_iso(s) == target_date_iso
        for s in dict_slots
    )
    for slot in active_slots:
        slot_date_iso = _extract_lms_slot_date_iso(slot)
        if slot_date_iso is not None:
            has_any_valid_date = True
        if slot_date_iso != target_date_iso:
            continue
        has_lesson, slot_lesson = _extract_lms_slot_lesson_number(slot)
        if not has_lesson:
            missing_lesson_on_date.append(slot)
        elif slot_lesson == lesson_num:
            matched_slots.append(slot)
        else:
            wrong_lesson_on_date.append(slot)

    if len(matched_slots) == 1 and not missing_lesson_on_date:
        return matched_slots[0], None
    if len(matched_slots) > 1 or (matched_slots and missing_lesson_on_date):
        total_ambiguous = len(matched_slots) + len(missing_lesson_on_date)
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Phát hiện {total_ambiguous} slot trùng ngày {target_date_iso} cho Buổi {lesson_num} trên LMS, cần kiểm tra thủ công",
            ),
        )
    if missing_lesson_on_date:
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Thiếu thông tin số buổi của slot ngày {target_date_iso} (Buổi {lesson_num}) trên LMS, cần kiểm tra thủ công",
            ),
        )
    if wrong_lesson_on_date:
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Slot ngày {target_date_iso} trên LMS lệch số buổi so với Buổi {lesson_num}, cần kiểm tra thủ công",
            ),
        )
    if cancelled_on_target_date:
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Slot ngày {target_date_iso} (Buổi {lesson_num}) trên LMS đã bị huỷ, cần kiểm tra thủ công",
            ),
        )
    if any(bool(s.get("truncated")) for s in dict_slots):
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Buổi {lesson_num} vượt giới hạn kiểm tra LMS ({MAX_LMS_SLOTS_PER_CLASS} buổi), cần kiểm tra thủ công",
            ),
        )
    if not has_any_valid_date:
        return (
            None,
            make_structured_issue(
                ISSUE_CODE_LMS_MANUAL_CHECK,
                f"[LMS] Thiếu thông tin ngày học của Buổi {lesson_num} trên LMS, cần kiểm tra thủ công",
            ),
        )
    return (
        None,
        make_structured_issue(
            ISSUE_CODE_LMS_MANUAL_CHECK,
            f"[LMS] Không tìm thấy slot khớp ngày {target_date_iso} (Buổi {lesson_num}) trên LMS, cần kiểm tra thủ công",
        ),
    )


def inspect_and_attach_quality_issues(
    scan_results: list[dict[str, Any]],
    *,
    lms_slots_by_class: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    """Evaluate and attach LMS & Zalo quality issues onto each scan result in place."""
    has_lms_map = isinstance(lms_slots_by_class, dict)
    slots_map: dict[str, list[dict[str, Any]]] = (
        lms_slots_by_class if isinstance(lms_slots_by_class, dict) else {}
    )
    for result in scan_results:
        if not isinstance(result, dict):
            continue
        st = result.get("scan_target")
        st_dict: dict[str, Any] | None = st if isinstance(st, dict) else None
        ev = result.get("evidence")
        ev_dict: dict[str, Any] = ev if isinstance(ev, dict) else {}
        raw_ctx = ev_dict.get("date_context")
        ctx: dict[str, Any] = raw_ctx if isinstance(raw_ctx, dict) else {}
        class_code = normalize_class_code(
            (st_dict.get("class_code") if st_dict else "")
            or ctx.get("target_class_code")
            or result.get("class_code")
            or ""
        )
        lesson_str = str(
            (st_dict.get("lesson") if st_dict else "")
            or ctx.get("target_lesson")
            or result.get("lesson")
            or ""
        ).strip()
        khoi_str = str(
            result.get("khoi")
            or (st_dict.get("khoi") if st_dict else "")
            or ""
        ).strip()
        lesson_num = _parse_lesson_number(lesson_str)
        if is_lesson_14(result, st_dict):
            continue

        lms_summary_clean = str(result.get("lms_lesson_summary") or "").strip()
        lms_issues: list[Any] = []
        existing_lms_issues = result.get("lms_quality_issues")
        if isinstance(existing_lms_issues, list):
            lms_issues.extend(
                dict(x) if isinstance(x, Mapping) else str(x).strip()
                for x in existing_lms_issues
                if issue_text(x)
            )

        slot_evaluated = False
        if has_lms_map:
            slot_evaluated = True
            if not class_code or lesson_num is None:
                lms_issues = [
                    make_structured_issue(
                        ISSUE_CODE_LMS_MANUAL_CHECK,
                        "[LMS] Thiếu mã lớp hoặc số buổi để kiểm tra LMS, cần kiểm tra thủ công",
                    )
                ]
            elif class_code not in slots_map:
                lms_issues = [
                    make_structured_issue(
                        ISSUE_CODE_LMS_MANUAL_CHECK,
                        "[LMS] Chưa kiểm tra được dữ liệu LMS của lớp, cần kiểm tra thủ công",
                    )
                ]
            else:
                class_slots = slots_map[class_code]
                if not isinstance(class_slots, list) or len(class_slots) == 0:
                    lms_issues = [
                        make_structured_issue(
                            ISSUE_CODE_LMS_MANUAL_CHECK,
                            "[LMS] Chưa kiểm tra được dữ liệu buổi học trên LMS, cần kiểm tra thủ công",
                        )
                    ]
                else:
                    first_slot = class_slots[0] if isinstance(class_slots[0], dict) else {}
                    if any(
                        first_slot.get(flag)
                        for flag in (
                            "overflow_class_limit",
                            "duplicate_class",
                            "missing_class",
                            "fetch_error",
                        )
                    ):
                        slot_eval = evaluate_lms_slot_quality(
                            first_slot,
                            class_code=class_code,
                            khoi=khoi_str,
                            lesson=lesson_num,
                        )
                        lms_issues = list(slot_eval.get("issues") or [])
                    else:
                        target_date_iso = _resolve_target_class_date_iso(
                            result, st_dict, ctx
                        )
                        matched_slot, slot_issue = select_matching_lms_slot(
                            class_slots,
                            target_date_iso=target_date_iso,
                            lesson_num=lesson_num,
                        )
                        if matched_slot is not None:
                            slot_eval = evaluate_lms_slot_quality(
                                matched_slot,
                                class_code=class_code,
                                khoi=khoi_str,
                                lesson=lesson_num,
                            )
                            lms_summary_clean = str(
                                slot_eval.get("summary_clean") or ""
                            ).strip()
                            lms_issues = list(slot_eval.get("issues") or [])
                            result["lms_is_checkpoint"] = bool(
                                slot_eval.get("is_checkpoint")
                            )
                            if st_dict is not None:
                                st_dict["lms_is_checkpoint"] = bool(
                                    slot_eval.get("is_checkpoint")
                                )
                        else:
                            lms_issues = [slot_issue] if slot_issue else []

        if slot_evaluated or lms_summary_clean:
            result["lms_lesson_summary"] = lms_summary_clean
            if st_dict is not None:
                st_dict["lms_lesson_summary"] = lms_summary_clean

        prior_zalo_issues: list[Any] = []
        prior_lms_issues: list[Any] = []
        seen_prior_zalo: set[str] = set()
        seen_prior_lms: set[str] = set()
        for src_obj in (result, st_dict or {}):
            if not isinstance(src_obj, Mapping):
                continue
            for list_key in (
                "zalo_content_issues",
                "lms_quality_issues",
                "quality_issues",
            ):
                for item in src_obj.get(list_key) or []:
                    txt = issue_text(item)
                    if not txt:
                        continue
                    upper_txt = txt.upper()
                    if (
                        upper_txt.startswith("[ZALO]")
                        and "cần kiểm tra thủ công" not in txt
                        and txt not in seen_prior_zalo
                    ):
                        seen_prior_zalo.add(txt)
                        prior_zalo_issues.append(item)
                    elif (
                        upper_txt.startswith("[LMS]")
                        and issue_code(item) != ISSUE_CODE_LMS_MANUAL_CHECK
                        and txt not in seen_prior_lms
                    ):
                        seen_prior_lms.add(txt)
                        prior_lms_issues.append(item)

        lms_eval_indeterminate = (not slot_evaluated) or any(
            issue_code(x) == ISSUE_CODE_LMS_MANUAL_CHECK
            for x in lms_issues
        )
        if lms_eval_indeterminate and prior_lms_issues:
            seen_lms = {issue_text(x) for x in lms_issues if issue_text(x)}
            for prior_l in prior_lms_issues:
                pt = issue_text(prior_l)
                if pt and pt not in seen_lms:
                    seen_lms.add(pt)
                    lms_issues.append(prior_l)

        if slot_evaluated or lms_issues:
            result["lms_quality_issues"] = list(lms_issues)
            if st_dict is not None:
                st_dict["lms_quality_issues"] = list(lms_issues)

        zalo_issues = list(
            evaluate_zalo_content_issues(
                result,
                lms_summary_text=lms_summary_clean,
            )
        )
        ev_state = str(result.get("evidence_state") or "").strip().upper()
        ev = result.get("evidence")
        clf = ev.get("classifier_result") if isinstance(ev, Mapping) else None
        decision = (
            str((clf.get("decision") if isinstance(clf, Mapping) else "") or "")
            .strip()
            .upper()
        )
        zalo_eval_indeterminate = (
            bool(str(result.get("error_code") or "").strip())
            or str(result.get("scan_status") or "").strip().upper() == "FAILED"
            or (not ev_state or ev_state not in VERIFIED_EVIDENCE_STATES)
            or decision
            in {
                "SCAN_FAILED",
                "NOT_EVALUATED",
                "ZALO_SYNC_INCOMPLETE",
                "GROUP_IDENTITY_MISMATCH",
                "MESSAGE_CAP_REACHED",
                "AI_SUGGESTED_REPORT_NEEDS_REVIEW",
                "TARGET_SESSION_AMBIGUOUS",
                "UI_SENT_AT_UNREADABLE",
            }
            or any("cần kiểm tra thủ công" in issue_text(x) for x in zalo_issues)
        )
        if zalo_eval_indeterminate and prior_zalo_issues:
            seen_zalo = {issue_text(x) for x in zalo_issues if issue_text(x)}
            for prior_z in prior_zalo_issues:
                pt = issue_text(prior_z)
                if pt and pt not in seen_zalo:
                    seen_zalo.add(pt)
                    zalo_issues.append(prior_z)

        if zalo_issues or "zalo_content_issues" in result or (
            st_dict is not None and "zalo_content_issues" in st_dict
        ):
            result["zalo_content_issues"] = list(zalo_issues)
            if st_dict is not None:
                st_dict["zalo_content_issues"] = list(zalo_issues)

        combined_quality = [
            *list(zalo_issues),
            *list(result.get("lms_quality_issues") or []),
        ]
        if (
            combined_quality
            or "quality_issues" in result
            or (st_dict is not None and "quality_issues" in st_dict)
        ):
            result["quality_issues"] = combined_quality
            if st_dict is not None:
                st_dict["quality_issues"] = list(combined_quality)
        if has_lms_map and is_lms_fetch_unavailable(slots_map):
            result["lms_fetch_unavailable"] = True
            if st_dict is not None:
                st_dict["lms_fetch_unavailable"] = True


def is_lms_fetch_unavailable(
    lms_slots_by_class: Mapping[str, Any] | None,
    requested_classes: Sequence[str] | None = None,
) -> bool:
    """Return True when LMS fetch failed system-wide (every requested class had fetch_error or exception)."""
    if not isinstance(lms_slots_by_class, Mapping):
        return False
    if bool(lms_slots_by_class.get("__lms_fetch_unavailable__")):
        return True
    raw_keys = (
        requested_classes
        if requested_classes is not None
        else [k for k in lms_slots_by_class.keys() if not str(k).startswith("__")]
    )
    class_keys = [
        normalize_class_code(k)
        for k in raw_keys
        if normalize_class_code(k)
    ]
    if not class_keys:
        return False
    inspected = 0
    for code in class_keys:
        slots = lms_slots_by_class.get(code)
        if (
            not isinstance(slots, Sequence)
            or isinstance(slots, (str, bytes))
            or len(slots) == 0
        ):
            return False
        inspected += 1
        if not all(
            isinstance(s, Mapping) and bool(s.get("fetch_error")) for s in slots
        ):
            return False
    return inspected > 0


async def fetch_and_evaluate_lms_quality(
    page: Any,
    scan_results: list[dict[str, Any]],
    *,
    lms_slots_by_class: dict[str, list[dict[str, Any]]] | None = None,
    lms_quality_gateway: Any = None,
    allow_page_fallback: bool = False,
) -> dict[str, list[dict[str, Any]]] | None:
    """Shared helper to fetch LMS class slots (if needed) and attach quality issues onto scan_results."""
    resolved_lms_slots: dict[str, list[dict[str, Any]]] | None = (
        dict(lms_slots_by_class) if isinstance(lms_slots_by_class, dict) else None
    )
    active_gateway = lms_quality_gateway
    if (
        os.getenv("ZALO_QUALITY_ISOLATED") == "1"
        and resolved_lms_slots is None
        and type(active_gateway).__name__ == "PlaywrightLmsQualityGateway"
    ):
        active_gateway = None
    if (
        active_gateway is None
        and resolved_lms_slots is None
        and not allow_page_fallback
        and os.getenv("ZALO_QUALITY_ISOLATED") != "1"
        and page is not None
        and hasattr(page, "evaluate")
    ):
        try:
            from services.external_gateways import ExternalGateways

            active_gateway = getattr(
                ExternalGateways.production(), "lms_quality", None
            )
        except Exception:
            active_gateway = None

    if resolved_lms_slots is None:
        candidate_codes = sorted(
            {
                normalize_class_code(
                    (r.get("scan_target") or {}).get("class_code")
                    if isinstance(r.get("scan_target"), dict)
                    else r.get("class_code") or ""
                )
                for r in scan_results
                if isinstance(r, dict)
            }
            - {""}
        )
        if active_gateway is not None:
            if candidate_codes and hasattr(active_gateway, "fetch_class_slots"):
                try:
                    fetched = await active_gateway.fetch_class_slots(
                        page, candidate_codes
                    )
                    if isinstance(fetched, dict):
                        resolved_lms_slots = fetched
                    else:
                        resolved_lms_slots = {
                            code: [{"fetch_error": True}]
                            for code in candidate_codes
                        }
                except Exception:
                    resolved_lms_slots = {
                        code: [{"fetch_error": True}] for code in candidate_codes
                    }
            else:
                resolved_lms_slots = {}
        elif (
            allow_page_fallback
            and os.getenv("ZALO_QUALITY_ISOLATED") != "1"
            and candidate_codes
            and page is not None
            and hasattr(page, "evaluate")
        ):
            try:
                fetched_slots = await fetch_lms_class_slots_via_page(
                    page, class_names=candidate_codes
                )
                if isinstance(fetched_slots, dict) and fetched_slots:
                    resolved_lms_slots = fetched_slots
            except Exception:
                resolved_lms_slots = {
                    code: [{"fetch_error": True}] for code in candidate_codes
                }

    inspect_and_attach_quality_issues(
        scan_results,
        lms_slots_by_class=resolved_lms_slots,
    )
    if is_lms_fetch_unavailable(resolved_lms_slots):
        for r in scan_results:
            if isinstance(r, dict):
                r["lms_fetch_unavailable"] = True
                if isinstance(r.get("scan_target"), dict):
                    r["scan_target"]["lms_fetch_unavailable"] = True
    return resolved_lms_slots
