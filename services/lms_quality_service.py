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
import base64
import contextlib
import html
import json
import math
import os
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from core.time_utils import BUSINESS_TIMEZONE
from logger_utils import log_message
from services.bcnb_safety import fold_text, normalize_auto_issue_text
from services.report_status import (
    ISSUE_CODE_LMS_MANUAL_CHECK,
    ISSUE_CODE_LMS_SPCK_MISSING,
    ISSUE_CODE_LMS_SPCK_NOTE,
    ISSUE_CODE_ZALO_REPORT_NO_DATE,
    ISSUE_CODE_ZALO_REPORT_WRONG_DATE,
    SEVERITY_FAIL,
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
MIN_SPCK_COMMENT_CHARS = 50
MIN_SPCK_COMMENT_WORDS = 12
SPCK_REQUIRED_LESSONS = frozenset({12, 13, 14})
SPCK_SLIDE_YOUTUBE_LESSONS = frozenset({14})
LMS_GRAPHQL_ENDPOINT = "https://lms-api.mindx.edu.vn/"
LMS_ALLOWED_HOSTNAME = "lms.mindx.edu.vn"
LMS_ALLOWED_LOGIN_HOSTNAMES = frozenset(
    {"lms.mindx.edu.vn", "id.mindx.edu.vn", "base.mindx.edu.vn"}
)
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


SPCK_TITLE_PLACEHOLDER_PATTERNS = (
    r"^spck(?:\s*(?:buoi|b|lesson|slot)?\s*\d+)?$",
    r"^san\s+pham(?:\s+cuoi\s+khoa)?(?:\s*(?:buoi|b|lesson|slot)?\s*\d+)?$",
    r"^cuoi\s+khoa(?:\s*(?:buoi|b|lesson|slot)?\s*\d+)?$",
    r"^project(?:\s+cuoi\s+khoa|\s+final)?(?:\s*\d+)?$",
    r"^final(?:\s+project)?(?:\s*\d+)?$",
    r"^bai\s+(?:nop|tap|thi|kiem\s+tra)(?:\s+cuoi\s+khoa)?(?:\s*\d+)?$",
    r"^nop\s+(?:bai|spck|san\s+pham)(?:\s*\d+)?$",
    r"^(?:b|bai|buoi|lesson|session|slot)\s*(?:so\s*)?\d+$",
    r"^test(?:\s*\d+)?$",
    r"^demo(?:\s*\d+)?$",
    r"^untitled$",
    r"^n/?a$",
)

SPCK_COMMENT_PLACEHOLDER_PATTERNS = (
    r"^(?:tot|rat\s+tot|kha|ok|okay|good|great|nice|well\s+done|excellent|pass|"
    r"dat|hoan\s+thanh|da\s+nop|da\s+cham|cham\s+bai|da\s+kiem\s+tra|n/?a|done|"
    r"yes|no|10|10/10|100|100%|a\+?)[\s.!?,;:]*$",
)

SPCK_COMMENT_DOMAIN_SIGNALS = (
    "san pham",
    "du an",
    "project",
    "spck",
    "y tuong",
    "chu de",
    "bo cuc",
    "giao dien",
    "thiet ke",
    "chuc nang",
    "tinh nang",
    "code",
    "lap trinh",
    "thuat toan",
    "logic",
    "ung dung",
    "website",
    "trang web",
    "web",
    "game",
    "tro choi",
    "nhan vat",
    "hinh anh",
    "am thanh",
    "hieu ung",
    "mau sac",
    "tuong tac",
    "thao tac",
    "dieu khien",
    "hoan thien",
    "hoan thanh",
    "thuyet trinh",
    "trinh bay",
    "slide",
    "video",
    "demo",
    "robot",
    "mo hinh",
    "lap rap",
    "cam bien",
    "dong co",
    "ket qua",
    "diem manh",
    "uu diem",
    "cai thien",
    "gop y",
    "loi khuyen",
    "phat trien",
    "bo sung",
    "sang tao",
    "chinh chu",
    "chi tiet",
    "kien thuc",
    "ky nang",
    "thai do",
    "van dung",
    "ap dung",
)

_YOUTUBE_URL_RE = re.compile(
    r"(?:https?://)?(?:[\w-]+\.)?(?:youtube\.com/(?:watch\?[^\s\"'<>]*v=|shorts/|embed/|live/|playlist\?[^\s\"'<>]*list=)|youtu\.be/[\w-]{4,})",
    flags=re.IGNORECASE,
)

_SLIDE_URL_HOST_PATTERNS = (
    "canva.com",
    "canva.link",
    "docs.google.com/presentation",
    "slideshare.net",
    "prezi.com",
    "pitch.com",
    "gamma.app",
    "1drv.ms/p/",
    "onedrive.live.com",
    "slides.com",
    "powerpoint",
)

_SLIDE_FILE_EXT_RE = re.compile(
    r"\.(?:pptx|ppt|pdf|key|odp)(?:$|[?#])",
    flags=re.IGNORECASE,
)

_SLIDE_NAME_KEYWORDS = (
    "slide",
    "thuyet trinh",
    "thuyet-trinh",
    "thuyet_trinh",
    "thuyettrinh",
    "presentation",
    "canva",
    "powerpoint",
    "bai trinh bay",
)


def _has_spck_student_info_in_title(
    work: Mapping[str, Any],
    *,
    student_full_name: str = "",
) -> bool:
    """Return True when StudentWork.title includes student identity/info (not empty or generic placeholder)."""
    raw_title = _strip_html(str(work.get("title") or "")).strip()
    if not raw_title or len(raw_title) < 3:
        return False
    folded_title = fold_text(raw_title).strip(" -–—:.,;!?()[]{}\"'")
    if not folded_title:
        return False
    for pattern in SPCK_TITLE_PLACEHOLDER_PATTERNS:
        if re.fullmatch(pattern, folded_title):
            return False
    explicit_flag = work.get("hasStudentInfoInTitle")
    if isinstance(explicit_flag, bool):
        return explicit_flag
    if "[hs]" in raw_title.lower():
        return True
    if student_full_name:
        folded_name = fold_text(student_full_name).strip()
        if len(folded_name) >= 3 and folded_name in folded_title:
            return True
        name_tokens = [t for t in folded_name.split() if t]
        if len(name_tokens) >= 2:
            tail_two = " ".join(name_tokens[-2:])
            if len(tail_two) >= 4 and tail_two in folded_title:
                return True
        return False
    parts = [
        p.strip()
        for p in re.split(r"\s*(?:[-–—|:]|_)\s*", raw_title)
        if p.strip()
    ]
    if len(parts) >= 2:
        non_generic_parts = 0
        for part in parts:
            fp = fold_text(part).strip(" -–—:.,;!?()[]{}\"'")
            if not fp:
                continue
            if any(re.fullmatch(pat, fp) for pat in SPCK_TITLE_PLACEHOLDER_PATTERNS):
                continue
            non_generic_parts += 1
        if non_generic_parts >= 2:
            return True
    return False


def _has_spck_thumbnail_and_images(work: Mapping[str, Any]) -> bool:
    """Return True when StudentWork has both a thumbnail URL and at least 1 screenshot in imageUrl."""
    thumb = str(work.get("thumbnail") or "").strip()
    if len(thumb) < 5:
        return False
    raw_images = work.get("imageUrl")
    if not isinstance(raw_images, Sequence) or isinstance(raw_images, (str, bytes)):
        return False
    valid_images = [
        str(u).strip()
        for u in raw_images
        if isinstance(u, str) and len(u.strip()) >= 5
    ]
    return len(valid_images) >= 1


def _has_spck_attachment_file(work: Mapping[str, Any]) -> bool:
    """Return True when StudentWork has at least 1 attached file URL in attachmentUrls."""
    raw_attachments = work.get("attachmentUrls")
    if not isinstance(raw_attachments, Sequence) or isinstance(
        raw_attachments, (str, bytes)
    ):
        return False
    valid_files = [
        str(u).strip()
        for u in raw_attachments
        if isinstance(u, str) and len(u.strip()) >= 5
    ]
    return len(valid_files) >= 1


def _is_substantive_spck_comment(
    raw_comment: str | None,
    *,
    student_full_name: str = "",
) -> bool:
    """Return True when a teacher's SPCK comment is substantive and product-specific (not 1-2 words or placeholder)."""
    body = _strip_comment_header_and_student_name(
        raw_comment,
        student_full_name=student_full_name,
    )
    if not body:
        return False
    body_no_hs = re.sub(r"\[hs\]", " ", body, flags=re.IGNORECASE).strip()
    folded = fold_text(body_no_hs).strip(" -–—:.,;!?()[]{}\"'")
    if not folded or len(folded) < MIN_SPCK_COMMENT_CHARS:
        return False
    for pattern in SPCK_COMMENT_PLACEHOLDER_PATTERNS:
        if re.fullmatch(pattern, folded):
            return False
    words = [w for w in re.findall(r"[a-z0-9]+", folded) if len(w) >= 2]
    if len(words) < MIN_SPCK_COMMENT_WORDS or len(set(words)) < 6:
        return False
    if _is_valid_regular_student_comment(body) or _is_valid_checkpoint_student_comment(
        body
    ):
        return True
    clauses = [
        seg.strip()
        for seg in re.split(r"(?:[\n•\-*]+|[.!?;:]+(?:\s+|$))", body_no_hs)
        if len(seg.strip()) >= 8
    ]
    if len(clauses) < 2:
        return False
    domain_hits = sum(1 for kw in SPCK_COMMENT_DOMAIN_SIGNALS if kw in folded)
    return domain_hits >= 2


def _has_spck_youtube_link(work: Mapping[str, Any]) -> bool:
    """Return True when StudentWork contains a valid YouTube link in videoUrls or relatedUrls."""
    candidates: list[str] = []
    for list_key in ("videoUrls", "attachmentUrls"):
        raw_list = work.get(list_key)
        if isinstance(raw_list, Sequence) and not isinstance(raw_list, (str, bytes)):
            for item in raw_list:
                if isinstance(item, str) and item.strip():
                    candidates.append(item.strip())
    raw_related = work.get("relatedUrls")
    if isinstance(raw_related, Sequence) and not isinstance(raw_related, (str, bytes)):
        for rel in raw_related:
            if isinstance(rel, Mapping):
                u = str(rel.get("url") or "").strip()
                if u:
                    candidates.append(u)
            elif isinstance(rel, str) and rel.strip():
                candidates.append(rel.strip())
    return any(bool(_YOUTUBE_URL_RE.search(url)) for url in candidates)


def _has_spck_slide_link_or_file(work: Mapping[str, Any]) -> bool:
    """Return True when StudentWork contains a presentation slide link (relatedUrls) or slide file (attachmentUrls)."""
    raw_related = work.get("relatedUrls")
    if isinstance(raw_related, Sequence) and not isinstance(raw_related, (str, bytes)):
        for rel in raw_related:
            if isinstance(rel, Mapping):
                rel_url = str(rel.get("url") or "").strip()
                rel_name = fold_text(str(rel.get("name") or "")).strip()
            elif isinstance(rel, str):
                rel_url = rel.strip()
                rel_name = ""
            else:
                continue
            if len(rel_url) < 5 or _YOUTUBE_URL_RE.search(rel_url):
                continue
            lower_url = rel_url.lower()
            folded_url = fold_text(rel_url)
            if any(host in lower_url for host in _SLIDE_URL_HOST_PATTERNS):
                return True
            if _SLIDE_FILE_EXT_RE.search(lower_url):
                return True
            if any(kw in rel_name or kw in folded_url for kw in _SLIDE_NAME_KEYWORDS):
                return True

    raw_attachments = work.get("attachmentUrls")
    if isinstance(raw_attachments, Sequence) and not isinstance(
        raw_attachments, (str, bytes)
    ):
        for att in raw_attachments:
            if not isinstance(att, str):
                continue
            att_url = att.strip()
            if len(att_url) < 5 or _YOUTUBE_URL_RE.search(att_url):
                continue
            lower_att = att_url.lower()
            folded_att = fold_text(att_url)
            if _SLIDE_FILE_EXT_RE.search(lower_att):
                return True
            if any(host in lower_att for host in _SLIDE_URL_HOST_PATTERNS):
                return True
            if any(kw in folded_att for kw in _SLIDE_NAME_KEYWORDS):
                return True
    return False


def _merge_student_works(works: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Combine multiple non-deleted StudentWork entries for the same student in a single slot."""
    if len(works) == 1:
        return dict(works[0])
    merged: dict[str, Any] = dict(works[0])
    best_title = ""
    has_info_flags: list[bool] = []
    best_thumb = ""
    images: list[str] = []
    attachments: list[str] = []
    videos: list[str] = []
    related: list[Any] = []
    best_comment = ""
    for w in works:
        flag = w.get("hasStudentInfoInTitle")
        if isinstance(flag, bool):
            has_info_flags.append(flag)
        t = str(w.get("title") or "").strip()
        if t and (
            not best_title
            or (flag is True and not merged.get("hasStudentInfoInTitle"))
            or len(t) > len(best_title)
        ):
            best_title = t
        th = str(w.get("thumbnail") or "").strip()
        if len(th) >= 5 and not best_thumb:
            best_thumb = th
        for u in w.get("imageUrl") or []:
            if isinstance(u, str) and u.strip() and u.strip() not in images:
                images.append(u.strip())
        for u in w.get("attachmentUrls") or []:
            if isinstance(u, str) and u.strip() and u.strip() not in attachments:
                attachments.append(u.strip())
        for u in w.get("videoUrls") or []:
            if isinstance(u, str) and u.strip() and u.strip() not in videos:
                videos.append(u.strip())
        for r in w.get("relatedUrls") or []:
            if r not in related:
                related.append(r)
        c = str(w.get("comment") or "").strip()
        if len(c) > len(best_comment):
            best_comment = c
    merged["title"] = best_title
    if has_info_flags:
        merged["hasStudentInfoInTitle"] = any(has_info_flags)
    merged["thumbnail"] = best_thumb
    merged["imageUrl"] = images
    merged["attachmentUrls"] = attachments
    merged["videoUrls"] = videos
    merged["relatedUrls"] = related
    merged["comment"] = best_comment
    return merged


def evaluate_lms_spck_quality(
    slot_data: Mapping[str, Any] | None,
    *,
    class_code: str = "",
    khoi: str = "",
    lesson: str | int | None = "",
) -> dict[str, Any]:
    """Evaluate final project (SPCK) submissions on LMS for Lessons 12, 13, and 14 (DEC-042)."""
    lesson_num = _parse_lesson_number(lesson)
    is_robotics = _is_robotics_class(class_code, khoi)
    empty_res: dict[str, Any] = {
        "is_spck_lesson": lesson_num in SPCK_REQUIRED_LESSONS,
        "is_robotics": is_robotics,
        "attended_count": 0,
        "submitted_count": 0,
        "missing_submission_count": 0,
        "missing_student_info_count": 0,
        "missing_image_count": 0,
        "missing_attachment_count": 0,
        "missing_comment_count": 0,
        "shallow_comment_count": 0,
        "missing_youtube_count": 0,
        "missing_slide_count": 0,
        "issues": [],
    }
    if lesson_num not in SPCK_REQUIRED_LESSONS or not isinstance(slot_data, Mapping):
        return empty_res

    if bool(slot_data.get("studentWorksFetchError")):
        return {
            **empty_res,
            "issues": [
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Chưa kiểm tra được dữ liệu SPCK trên LMS, cần kiểm tra thủ công",
                )
            ],
        }

    student_attendance = slot_data.get("studentAttendance")
    attended_entries: list[dict[str, Any]] = []
    if isinstance(student_attendance, list):
        for raw_entry in student_attendance[:MAX_LMS_STUDENTS_PER_SLOT]:
            if not isinstance(raw_entry, dict):
                continue
            status = str(raw_entry.get("status") or "").strip().upper()
            if status in ATTENDED_STATUSES:
                attended_entries.append(raw_entry)

    attended_count = len(attended_entries)
    if attended_count == 0:
        if is_lesson_14(lesson_num) and "studentWorks" in slot_data:
            return {
                **empty_res,
                "issues": [
                    make_structured_issue(
                        ISSUE_CODE_LMS_MANUAL_CHECK,
                        "[LMS] Chưa có học viên điểm danh có mặt ở Buổi 14 để kiểm tra SPCK, cần kiểm tra thủ công",
                    )
                ],
            }
        return empty_res

    raw_works = slot_data.get("studentWorks")
    if raw_works is None and "studentWorks" not in slot_data:
        return {
            **empty_res,
            "attended_count": attended_count,
        }
    if not isinstance(raw_works, Sequence) or isinstance(raw_works, (str, bytes)):
        return {
            **empty_res,
            "attended_count": attended_count,
            "issues": [
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Chưa kiểm tra được dữ liệu SPCK trên LMS, cần kiểm tra thủ công",
                )
            ],
        }

    active_works: list[dict[str, Any]] = []
    for w in raw_works:
        if not isinstance(w, Mapping):
            continue
        is_del = w.get("isDeleted")
        if is_del is True or str(is_del or "").strip().lower() == "true":
            continue
        active_works.append(dict(w))

    def _entry_student_key(entry: Mapping[str, Any]) -> str:
        k = str(entry.get("studentKey") or entry.get("studentId") or "").strip()
        if k:
            return k
        stu = entry.get("student")
        if isinstance(stu, Mapping):
            return str(stu.get("id") or stu.get("studentKey") or "").strip()
        return ""

    def _entry_student_name(entry: Mapping[str, Any]) -> str:
        stu = entry.get("student")
        if isinstance(stu, Mapping):
            return str(stu.get("fullName") or "").strip()
        return ""

    attended_by_key: dict[str, str] = {}
    attended_ordered_keys: list[str] = []
    unkeyed_attended_count = 0
    non_attended_keys: set[str] = set()
    attendance_slice = (
        student_attendance[:MAX_LMS_STUDENTS_PER_SLOT]
        if isinstance(student_attendance, list)
        else []
    )
    for entry in attendance_slice:
        if not isinstance(entry, Mapping):
            continue
        s_key = _entry_student_key(entry)
        status = str(entry.get("status") or "").strip().upper()
        if status in ATTENDED_STATUSES:
            if s_key:
                if s_key not in attended_by_key:
                    attended_by_key[s_key] = _entry_student_name(entry)
                    attended_ordered_keys.append(s_key)
            else:
                unkeyed_attended_count += 1
        elif s_key:
            non_attended_keys.add(s_key)

    works_by_key: dict[str, list[dict[str, Any]]] = {}
    unknown_owner_works: list[dict[str, Any]] = []
    for w in active_works:
        w_key = str(w.get("studentKey") or w.get("studentId") or "").strip()
        if w_key:
            if w_key in attended_by_key:
                works_by_key.setdefault(w_key, []).append(w)
            elif w_key in non_attended_keys:
                continue
            else:
                unknown_owner_works.append(w)
        else:
            unknown_owner_works.append(w)

    if unknown_owner_works or (unkeyed_attended_count > 0 and active_works):
        return {
            **empty_res,
            "attended_count": attended_count,
            "issues": [
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Dữ liệu SPCK trên LMS có bài nộp không xác định được học viên đi học, cần kiểm tra thủ công",
                )
            ],
        }

    inspected_works: list[tuple[dict[str, Any], str]] = []
    for s_key in attended_ordered_keys:
        if s_key in works_by_key:
            inspected_works.append(
                (
                    _merge_student_works(works_by_key[s_key]),
                    attended_by_key.get(s_key, ""),
                )
            )

    submitted_count = len(inspected_works)
    missing_submission_count = max(0, attended_count - submitted_count)
    issues: list[Any] = []

    if submitted_count == 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_MISSING,
                f"[LMS] Chưa nộp SPCK ({attended_count}/{attended_count} HS)",
                severity=SEVERITY_FAIL,
            )
        )
        return {
            **empty_res,
            "attended_count": attended_count,
            "submitted_count": 0,
            "missing_submission_count": attended_count,
            "issues": issues,
        }

    if missing_submission_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] Thiếu nộp SPCK ({missing_submission_count}/{attended_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )

    missing_student_info_count = 0
    missing_image_count = 0
    missing_attachment_count = 0
    missing_comment_count = 0
    shallow_comment_count = 0
    missing_youtube_count = 0
    missing_slide_count = 0

    for work_item, stu_name in inspected_works:
        if not _has_spck_student_info_in_title(work_item, student_full_name=stu_name):
            missing_student_info_count += 1
        if not _has_spck_thumbnail_and_images(work_item):
            missing_image_count += 1
        if not _has_spck_attachment_file(work_item):
            missing_attachment_count += 1
        raw_c = work_item.get("comment")
        clean_c = _strip_comment_header_and_student_name(
            raw_c if isinstance(raw_c, str) else "",
            student_full_name=stu_name,
        )
        if not clean_c.strip():
            missing_comment_count += 1
        elif not _is_substantive_spck_comment(
            raw_c if isinstance(raw_c, str) else "",
            student_full_name=stu_name,
        ):
            shallow_comment_count += 1
        if lesson_num in SPCK_SLIDE_YOUTUBE_LESSONS:
            if not _has_spck_youtube_link(work_item):
                missing_youtube_count += 1
            if not _has_spck_slide_link_or_file(work_item):
                missing_slide_count += 1

    if missing_student_info_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] SPCK thiếu thông tin học viên ({missing_student_info_count}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if missing_image_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] SPCK thiếu ảnh thumbnail/ảnh chụp ({missing_image_count}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if missing_attachment_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] SPCK thiếu file đính kèm ({missing_attachment_count}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if missing_comment_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] SPCK chưa có nhận xét ({missing_comment_count}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if shallow_comment_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] Nhận xét SPCK sơ sài ({shallow_comment_count}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if lesson_num in SPCK_SLIDE_YOUTUBE_LESSONS:
        if missing_youtube_count > 0:
            issues.append(
                make_structured_issue(
                    ISSUE_CODE_LMS_SPCK_NOTE,
                    f"[LMS] SPCK Buổi 14 thiếu link YouTube ({missing_youtube_count}/{submitted_count} HS)",
                    severity=SEVERITY_NOTE,
                )
            )
        if missing_slide_count > 0:
            issues.append(
                make_structured_issue(
                    ISSUE_CODE_LMS_SPCK_NOTE,
                    f"[LMS] SPCK Buổi 14 thiếu link/file Slide thuyết trình ({missing_slide_count}/{submitted_count} HS)",
                    severity=SEVERITY_NOTE,
                )
            )

    return {
        "is_spck_lesson": True,
        "is_robotics": is_robotics,
        "attended_count": attended_count,
        "submitted_count": submitted_count,
        "missing_submission_count": missing_submission_count,
        "missing_student_info_count": missing_student_info_count,
        "missing_image_count": missing_image_count,
        "missing_attachment_count": missing_attachment_count,
        "missing_comment_count": missing_comment_count,
        "shallow_comment_count": shallow_comment_count,
        "missing_youtube_count": missing_youtube_count,
        "missing_slide_count": missing_slide_count,
        "issues": issues,
    }


def evaluate_lms_slot_quality(
    slot_data: dict[str, Any] | None,
    *,
    class_code: str,
    khoi: str = "",
    lesson: str | int | None = "",
) -> dict[str, Any]:
    """Evaluate lesson summary, student comments, and SPCK (Lessons 12-14) for one LMS slot."""
    lesson_num = _parse_lesson_number(lesson)
    lesson_14_flag = is_lesson_14(lesson)
    if lesson_14_flag and isinstance(slot_data, dict) and not any(
        slot_data.get(flag)
        for flag in (
            "overflow_class_limit",
            "duplicate_class",
            "missing_class",
            "fetch_error",
            "studentWorksFetchError",
        )
    ) and "studentWorks" not in slot_data:
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
            "is_exempt": lesson_14_flag,
            "is_checkpoint": False if lesson_14_flag else is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": lesson_14_flag,
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
            "is_exempt": lesson_14_flag,
            "is_checkpoint": False if lesson_14_flag else is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": lesson_14_flag,
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
            "is_exempt": lesson_14_flag,
            "is_checkpoint": False if lesson_14_flag else is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": lesson_14_flag,
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
            "is_exempt": lesson_14_flag,
            "is_checkpoint": False if lesson_14_flag else is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": lesson_14_flag,
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
            "is_exempt": lesson_14_flag,
            "is_checkpoint": False if lesson_14_flag else is_checkpoint_session(class_code, khoi, lesson),
            "has_valid_summary": lesson_14_flag,
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
    if lesson_14_flag:
        spck_eval_14 = evaluate_lms_spck_quality(
            slot_data,
            class_code=class_code,
            khoi=khoi,
            lesson=14,
        )
        l14_issues = list(spck_eval_14.get("issues") or [])
        if bool(slot_data.get("truncated")) and not any(
            "vượt giới hạn" in issue_text(item) for item in l14_issues
        ):
            l14_issues.append(
                make_structured_issue(
                    ISSUE_CODE_LMS_MANUAL_CHECK,
                    "[LMS] Dữ liệu buổi học trên LMS vượt giới hạn kiểm tra, cần kiểm tra thủ công",
                )
            )
        return {
            "is_exempt": True,
            "is_checkpoint": False,
            "has_valid_summary": True,
            "summary_clean": "",
            "attended_count": int(spck_eval_14.get("attended_count") or 0),
            "failed_comment_count": 0,
            "spck_submitted_count": int(spck_eval_14.get("submitted_count") or 0),
            "issues": l14_issues,
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
    if lesson_num in SPCK_REQUIRED_LESSONS and (
        "studentWorks" in slot_data or "studentWorksFetchError" in slot_data
    ):
        spck_eval = evaluate_lms_spck_quality(
            slot_data,
            class_code=class_code,
            khoi=khoi,
            lesson=lesson_num,
        )
        issues.extend(spck_eval["issues"])
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


_NEXT_LESSON_SECTION_MARKERS = (
    "buoi hoc ke tiep",
    "buoi ke tiep",
    "buoi hoc tiep theo",
    "buoi tiep theo",
    "bai hoc ke tiep",
    "bai hoc tiep theo",
    "lich hoc buoi tiep theo",
    "lich hoc ke tiep",
)


def extract_report_lesson_title(raw_content: str | None) -> str:
    """Extract an explicit lesson title/topic line from a Zalo report without capturing student PII."""
    if not raw_content:
        return ""
    awaiting_next_line_title = False
    for line in str(raw_content).splitlines():
        clean_line = re.sub(
            r"^[^\w]+",
            "",
            " ".join(line.split()),
            flags=re.UNICODE,
        ).strip()
        if not clean_line:
            continue
        folded_line = fold_text(clean_line)
        if any(marker in folded_line for marker in _NEXT_LESSON_SECTION_MARKERS):
            break
        m = re.match(
            r"^(?:t[eê]n\s+b[aà]i(?:\s+h[oọ]c)?|ch[uủ]\s+[dđ][eề](?:\s+b[aà]i\s+h[oọ]c)?|"
            r"n[oộ]i\s+dung\s+(?:c[uủ]a\s+)?(?:b[aà]i|bu[oổ]i)\s+h[oọ]c|b[aà]i\s+h[oọ]c\s+h[oô]m\s+nay)"
            r"(?:\s*[:\-–—]\s*(.+)|\s*[:\-–—]?\s*)$",
            clean_line,
            flags=re.IGNORECASE,
        )
        if m:
            inline_title = (m.group(1) or "").strip(" .;:!|-–—")
            if 3 <= len(inline_title) <= 120:
                return inline_title
            awaiting_next_line_title = True
            continue
        if awaiting_next_line_title:
            awaiting_next_line_title = False
            m_numbered = re.match(
                r"^(?:b[aà]i(?:\s+h[oọ]c)?(?:\s+s[oố])?|bu[oổ]i(?:\s+h[oọ]c)?(?:\s+s[oố])?|lesson)\s*\d+\s*[:\-–—.]\s*(.+)$",
                clean_line,
                flags=re.IGNORECASE,
            )
            if m_numbered and len(m_numbered.group(1).strip(" .;:!|-–—")) >= 3:
                title = clean_line.strip(" .;:!|-–—")
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


def _is_usable_lms_token(
    raw_token: Any,
    *,
    min_ttl_seconds: int = 60,
    expiration_time: Any = None,
    now_epoch: float | None = None,
) -> bool:
    """Return True if raw_token is a non-empty token and not an expired (or about-to-expire) JWT."""
    if not isinstance(raw_token, str):
        return False
    clean = raw_token.strip().strip('"')
    if clean.lower().startswith("bearer "):
        clean = clean[7:].strip()
    if len(clean) <= 20:
        return False
    now_sec = float(now_epoch) if now_epoch is not None else time.time()
    if expiration_time is not None and not isinstance(expiration_time, bool):
        try:
            exp_num = float(expiration_time)
            if math.isfinite(exp_num) and exp_num > 0:
                exp_sec = exp_num / 1000.0 if exp_num > 1e11 else exp_num
                if exp_sec <= now_sec + min_ttl_seconds:
                    return False
        except (TypeError, ValueError):
            pass
    if clean.startswith("eyJ") and clean.count(".") == 2:
        parts = clean.split(".")
        payload_b64 = parts[1].strip()
        if not payload_b64:
            return False
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        try:
            raw_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
            payload_obj = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            return False
        if not isinstance(payload_obj, Mapping):
            return False
        raw_exp = payload_obj.get("exp")
        if raw_exp is not None and not isinstance(raw_exp, bool):
            try:
                exp_val = float(raw_exp)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(exp_val) or exp_val <= 0:
                return False
            exp_sec = exp_val / 1000.0 if exp_val > 1e11 else exp_val
            if exp_sec <= now_sec + min_ttl_seconds:
                return False
    return True


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
                        if k in ALLOWED_LMS_TOKEN_KEYS and _is_usable_lms_token(v):
                            pre_resolved_token = v
                            if k == "id_token":
                                break
                        elif v.startswith("eyJ") and len(v) > 40 and _is_usable_lms_token(v):
                            pre_resolved_token = v
                        elif raw_v.startswith("{"):
                            with contextlib.suppress(Exception):
                                parsed_ls = json.loads(raw_v)
                                if isinstance(parsed_ls, dict):
                                    sts = parsed_ls.get("stsTokenManager")
                                    exp_time = (
                                        sts.get("expirationTime")
                                        if isinstance(sts, dict)
                                        else parsed_ls.get("expirationTime")
                                    )
                                    acc = (
                                        sts.get("accessToken")
                                        if isinstance(sts, dict)
                                        else None
                                    )
                                    if isinstance(acc, str) and _is_usable_lms_token(
                                        acc, expiration_time=exp_time
                                    ):
                                        pre_resolved_token = acc
                                    elif isinstance(
                                        parsed_ls.get("idToken"), str
                                    ) and _is_usable_lms_token(
                                        parsed_ls["idToken"], expiration_time=exp_time
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
                        if not _is_usable_lms_token(val):
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
        const isTokenUsable = (rawToken, expTime) => {
            if (typeof rawToken !== 'string') return false;
            let clean = rawToken.trim().replace(/^"|"$/g, '');
            if (/^bearer\\s+/i.test(clean)) {
                clean = clean.replace(/^bearer\\s+/i, '').trim();
            }
            if (clean.length <= 20) return false;
            const nowSec = Date.now() / 1000;
            if (expTime != null && typeof expTime !== 'boolean') {
                const expNum = Number(expTime);
                if (Number.isFinite(expNum) && expNum > 0) {
                    const expSec = expNum > 1e11 ? expNum / 1000 : expNum;
                    if (expSec <= nowSec + 60) return false;
                }
            }
            if (clean.startsWith('eyJ') && clean.split('.').length === 3) {
                try {
                    const b64 = clean.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
                    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
                    const jsonStr = typeof atob === 'function'
                        ? atob(b64 + pad)
                        : Buffer.from(b64 + pad, 'base64').toString('utf8');
                    const payload = JSON.parse(jsonStr);
                    if (!payload || typeof payload !== 'object') return false;
                    if (payload.exp != null && typeof payload.exp !== 'boolean') {
                        const expVal = Number(payload.exp);
                        if (!Number.isFinite(expVal) || expVal <= 0) return false;
                        const expSec = expVal > 1e11 ? expVal / 1000 : expVal;
                        if (expSec <= nowSec + 60) return false;
                    }
                } catch (_) {
                    return false;
                }
            }
            return true;
        };
        const isAuthErrorPayload = (status, payload) => {
            if (status === 401 || status === 403) return true;
            const errs = payload && Array.isArray(payload.errors) ? payload.errors : [];
            for (const err of errs) {
                if (!err || typeof err !== 'object') continue;
                const msg = String(err.message || '').toLowerCase();
                const extCode = String((err.extensions && err.extensions.code) || '').toLowerCase();
                if (
                    extCode === 'unauthenticated' ||
                    extCode === 'unauthorized' ||
                    extCode === 'forbidden' ||
                    /unauthenticated|unauthorized|jwt\\s+expired|token\\s+expired|invalid\\s+token|not\\s+authenticated/i.test(msg)
                ) {
                    return true;
                }
            }
            return false;
        };
        const findToken = async () => {
            if (isTokenUsable(preResolvedToken)) {
                return preResolvedToken;
            }
            try {
                const currentHost = String((window.location && window.location.hostname) || '').toLowerCase();
                if (currentHost !== allowedHostname) {
                    return '';
                }
                const keySet = new Set(allowedTokenKeys || []);
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
                                            const sts = val && val.stsTokenManager;
                                            const access = sts && sts.accessToken;
                                            const expTime = sts && sts.expirationTime;
                                            if (typeof access === 'string' && isTokenUsable(access, expTime)) {
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
                if (typeof localStorage !== 'undefined' && localStorage) {
                    for (let i = 0; i < localStorage.length; i++) {
                        const rawKey = String(localStorage.key(i) || '');
                        const k = rawKey.trim().toLowerCase();
                        const rawVal = String(localStorage.getItem(rawKey) || '').trim();
                        const v = rawVal.replace(/^"|"$/g, '');
                        if (keySet.has(k) && isTokenUsable(v)) {
                            return v;
                        }
                        if (v.startsWith('eyJ') && v.length > 40 && isTokenUsable(v)) {
                            return v;
                        }
                        if (rawVal.startsWith('{')) {
                            try {
                                const parsed = JSON.parse(rawVal);
                                const stsObj = parsed && parsed.stsTokenManager;
                                const sts = stsObj && stsObj.accessToken;
                                const expTime = (stsObj && stsObj.expirationTime) || (parsed && parsed.expirationTime);
                                if (typeof sts === 'string' && isTokenUsable(sts, expTime)) {
                                    return sts;
                                }
                                if (parsed && typeof parsed.idToken === 'string' && isTokenUsable(parsed.idToken, expTime)) {
                                    return parsed.idToken;
                                }
                            } catch (_) {}
                        }
                    }
                }
            } catch (_) {}
            return '';
        };
        const out = {};
        const token = await findToken();
        if (!token) {
            out.__auth_error__ = true;
            out.__failure_reason__ = 'missing_or_expired_token';
            for (const code of classCodes) {
                out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
            }
            return out;
        }
        let authFailed = false;
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
                        id
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
                                    id
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
        const studentWorksQuery = `
            query findAllStudentWorks($classIds: [ID], $pageIndex: Int, $itemsPerPage: Int) {
                findAllStudentWorks(
                    payload: {
                        classId_in: $classIds,
                        pageIndex: $pageIndex,
                        itemsPerPage: $itemsPerPage
                    }
                ) {
                    data {
                        id
                        title
                        classId
                        classSessionId
                        classSessionNumber
                        studentId
                        thumbnail
                        imageUrl
                        attachmentUrls
                        videoUrls
                        relatedUrls {
                            name
                            url
                        }
                        comment
                        isDeleted
                    }
                }
            }
        `;
        const foldViChars = (str) => String(str || '')
            .normalize('NFC')
            .normalize('NFD')
            .replace(/[\\u0300-\\u036f]/g, '')
            .replace(/[đĐ]/g, 'd')
            .toLowerCase();
        const foldVi = (str) => foldViChars(str)
            .replace(/\\s+/g, ' ')
            .trim();
        const replaceCaseInsensitive = (sourceText, needle, replacement) => {
            const src = String(sourceText || '').normalize('NFC');
            const ndlTokens = foldVi(needle).split(' ').filter(Boolean);
            if (!src || ndlTokens.length === 0) return src;
            const foldedSrc = foldViChars(src);
            if (foldedSrc.length !== src.length) return src;
            const firstTok = ndlTokens[0];
            if (ndlTokens.length === 1 && firstTok.length < 3) return src;
            let result = '';
            let cursor = 0;
            while (cursor < src.length) {
                const hit = foldedSrc.indexOf(firstTok, cursor);
                if (hit === -1) {
                    result += src.slice(cursor);
                    break;
                }
                let pos = hit + firstTok.length;
                let matchedAll = true;
                for (let tIdx = 1; tIdx < ndlTokens.length; tIdx++) {
                    let wsEnd = pos;
                    while (wsEnd < foldedSrc.length && /\\s/.test(foldedSrc[wsEnd])) {
                        wsEnd += 1;
                    }
                    const nextTok = ndlTokens[tIdx];
                    if (wsEnd === pos || !foldedSrc.startsWith(nextTok, wsEnd)) {
                        matchedAll = false;
                        break;
                    }
                    pos = wsEnd + nextTok.length;
                }
                if (matchedAll) {
                    result += src.slice(cursor, hit) + replacement;
                    cursor = pos;
                } else {
                    result += src.slice(cursor, hit + 1);
                    cursor = hit + 1;
                }
            }
            return result;
        };
        const scrubStudentNames = (rawText, namesList) => {
            let text = String(rawText || '');
            if (!text) return '';
            for (const fullName of namesList) {
                if (!fullName || fullName.length < 3) continue;
                text = replaceCaseInsensitive(text, fullName, '[HS]');
                const tokens = fullName.split(/\\s+/).filter(Boolean);
                if (tokens.length >= 2) {
                    const tailTwo = tokens.slice(-2).join(' ');
                    if (tailTwo.length >= 4) {
                        text = replaceCaseInsensitive(text, tailTwo, '[HS]');
                    }
                }
            }
            return text;
        };
        const checkTitleHasStudentName = (rawTitle, ownerName, namesList) => {
            const foldedTitle = foldVi(rawTitle);
            if (!foldedTitle) return false;
            if (foldedTitle.includes('[hs]')) return true;
            const candidates = ownerName ? [ownerName, ...namesList] : namesList;
            for (const fullName of candidates) {
                const foldedName = foldVi(fullName);
                if (foldedName.length >= 3 && foldedTitle.includes(foldedName)) {
                    return true;
                }
                const tokens = foldedName.split(' ').filter(Boolean);
                if (tokens.length >= 2) {
                    const tailTwo = tokens.slice(-2).join(' ');
                    if (tailTwo.length >= 4 && foldedTitle.includes(tailTwo)) {
                        return true;
                    }
                }
            }
            return false;
        };
        const bearerHeader = token.startsWith('Bearer ') ? token : `Bearer ${token}`;
        const rawTokenHeader = token.replace(/^Bearer\\s+/i, '');
        const matchedClassMeta = {};
        const formatItemSlots = (item, codeKey) => {
            if (!item || !Array.isArray(item.slots) || item.slots.length === 0) {
                return Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
            }
            const studentIdToKey = new Map();
            const studentIdToName = new Map();
            const studentNamesSet = new Set();
            let studentCounter = 0;
            for (const s of item.slots.slice(0, maxSlots)) {
                const rawAtt = Array.isArray(s && s.studentAttendance) ? s.studentAttendance : [];
                for (const att of rawAtt.slice(0, maxStudents)) {
                    const stuObj = (att && att.student && typeof att.student === 'object') ? att.student : null;
                    const stuId = stuObj && stuObj.id != null ? String(stuObj.id).trim() : '';
                    const fullName = stuObj && typeof stuObj.fullName === 'string' ? stuObj.fullName.trim() : '';
                    if (fullName && fullName.length >= 3) {
                        studentNamesSet.add(fullName);
                    }
                    if (stuId && !studentIdToKey.has(stuId)) {
                        studentCounter += 1;
                        studentIdToKey.set(stuId, `stu_${studentCounter}`);
                    }
                    if (stuId && fullName && !studentIdToName.has(stuId)) {
                        studentIdToName.set(stuId, fullName);
                    }
                }
            }
            const classIdStr = (item && item.id != null) ? String(item.id).trim().slice(0, 64) : '';
            if (codeKey) {
                matchedClassMeta[codeKey] = {
                    classId: classIdStr,
                    studentIdToKey,
                    studentIdToName,
                    studentNames: Array.from(studentNamesSet)
                };
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
                            const stuObj = (att && att.student && typeof att.student === 'object') ? att.student : null;
                            const stuId = stuObj && stuObj.id != null ? String(stuObj.id).trim() : '';
                            const stuKey = stuId ? studentIdToKey.get(stuId) : '';
                            const fullName = String((stuObj && stuObj.fullName) || '').trim();
                            let cleanComment = (att && typeof att.comment === 'string') ? att.comment.slice(0, 15000) : '';
                            if (fullName && fullName.length >= 3 && cleanComment) {
                                cleanComment = cleanComment.split(fullName).join('');
                            }
                            return {
                                status: String((att && att.status) || ''),
                                comment: cleanComment,
                                ...(stuKey ? { studentKey: stuKey } : {})
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
                        if (isAuthErrorPayload(resp.status, null)) {
                            authFailed = true;
                        }
                        bulkComplete = false;
                        break;
                    }
                    const payload = await resp.json();
                    if (payload && Array.isArray(payload.errors) && payload.errors.length > 0) {
                        if (isAuthErrorPayload(resp.status, payload)) {
                            authFailed = true;
                        }
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
                        out[code] = formatItemSlots(matched[0], code);
                    }
                }
            }
        }

        // Tier 2: Per-code fetch for small batches (<= 3) or any code not returned in Tier 1
        const fetchOneCode = async (code) => {
            if (authFailed) {
                out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
                return;
            }
            for (let attempt = 0; attempt < 2; attempt++) {
                if (authFailed) {
                    out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
                    return;
                }
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
                        if (isAuthErrorPayload(resp.status, null)) {
                            authFailed = true;
                            out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
                            return;
                        }
                        if (attempt === 0) {
                            await new Promise((r) => setTimeout(r, 300));
                            continue;
                        }
                        out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
                        return;
                    }
                    const payload = await resp.json();
                    if (payload && Array.isArray(payload.errors) && payload.errors.length > 0) {
                        if (isAuthErrorPayload(resp.status, payload)) {
                            authFailed = true;
                            out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
                            return;
                        }
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
                    out[code] = formatItemSlots(matched[0], code);
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
            if (authFailed) {
                for (const code of remainingCodes) {
                    out[code] = Array.from({ length: maxSlots }, () => ({ fetch_error: true }));
                }
            } else {
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
        }

        // Tier 3: Fetch findAllStudentWorks for classes that have SPCK slots (index 11, 12, 13 -> Buổi 12, 13, 14)
        const spckCodes = [];
        const spckClassIds = [];
        const classIdToCodes = new Map();
        for (const code of classCodes) {
            const meta = matchedClassMeta[code];
            const slotsArr = Array.isArray(out[code]) ? out[code] : [];
            const hasSpckSlot = slotsArr.some((s) => s && (s.index === 11 || s.index === 12 || s.index === 13));
            if (!hasSpckSlot || !meta) continue;
            if (!meta.classId) {
                for (const s of slotsArr) {
                    if (s && (s.index === 11 || s.index === 12 || s.index === 13)) {
                        s.studentWorksFetchError = true;
                    }
                }
                continue;
            }
            spckCodes.push(code);
            if (!classIdToCodes.has(meta.classId)) {
                classIdToCodes.set(meta.classId, []);
                spckClassIds.push(meta.classId);
            }
            classIdToCodes.get(meta.classId).push(code);
        }

        if (spckClassIds.length > 0 && !authFailed) {
            const allWorks = [];
            let worksFetchOk = false;
            for (let pageIdx = 0; pageIdx < 5; pageIdx++) {
                const ctrl = new AbortController();
                const timer = setTimeout(() => ctrl.abort(), Math.max(timeoutMs || 12000, 15000));
                try {
                    const resp = await fetch(endpoint, {
                        method: 'POST',
                        headers: {
                            'content-type': 'application/json',
                            'authorization': bearerHeader,
                            'token': rawTokenHeader
                        },
                        body: JSON.stringify({
                            operationName: 'findAllStudentWorks',
                            variables: {
                                classIds: spckClassIds,
                                pageIndex: pageIdx,
                                itemsPerPage: 200
                            },
                            query: studentWorksQuery
                        }),
                        signal: ctrl.signal
                    });
                    if (!resp.ok) {
                        if (isAuthErrorPayload(resp.status, null)) {
                            authFailed = true;
                        }
                        worksFetchOk = false;
                        break;
                    }
                    const payload = await resp.json();
                    if (payload && Array.isArray(payload.errors) && payload.errors.length > 0) {
                        if (isAuthErrorPayload(resp.status, payload)) {
                            authFailed = true;
                        }
                        worksFetchOk = false;
                        break;
                    }
                    const pageWorks = (((payload || {}).data || {}).findAllStudentWorks || {}).data;
                    if (!Array.isArray(pageWorks)) {
                        worksFetchOk = false;
                        break;
                    }
                    allWorks.push(...pageWorks);
                    if (pageWorks.length < 200) {
                        worksFetchOk = true;
                        break;
                    }
                } catch (_) {
                    worksFetchOk = false;
                    break;
                } finally {
                    clearTimeout(timer);
                }
            }
            if (!worksFetchOk) {
                for (const code of spckCodes) {
                    const slotsArr = Array.isArray(out[code]) ? out[code] : [];
                    for (const s of slotsArr) {
                        if (s && (s.index === 11 || s.index === 12 || s.index === 13)) {
                            s.studentWorksFetchError = true;
                        }
                    }
                }
            } else {
                const worksByClassId = new Map();
                for (const w of allWorks) {
                    if (!w || typeof w !== 'object') continue;
                    const isDel = String(w.isDeleted || '').trim().toLowerCase();
                    if (w.isDeleted === true || isDel === 'true') continue;
                    const cid = w.classId != null ? String(w.classId).trim() : '';
                    if (!cid) continue;
                    if (!worksByClassId.has(cid)) {
                        worksByClassId.set(cid, []);
                    }
                    worksByClassId.get(cid).push(w);
                }
                for (const code of spckCodes) {
                    const meta = matchedClassMeta[code];
                    const slotsArr = Array.isArray(out[code]) ? out[code] : [];
                    const classWorks = (meta && worksByClassId.get(meta.classId)) || [];
                    for (const s of slotsArr) {
                        if (!s || (s.index !== 11 && s.index !== 12 && s.index !== 13)) continue;
                        const slotId = String(s._id || '').trim();
                        const slotNumber = Number(s.index) + 1;
                        const matchedWorks = classWorks.filter((w) => {
                            const wSessionId = w.classSessionId != null ? String(w.classSessionId).trim() : '';
                            if (slotId && wSessionId) {
                                return wSessionId === slotId;
                            }
                            if (!wSessionId && w.classSessionNumber != null) {
                                return Number(w.classSessionNumber) === slotNumber;
                            }
                            return false;
                        });
                        if (matchedWorks.length > maxStudents * 2) {
                            s.studentWorksFetchError = true;
                            continue;
                        }
                        s.studentWorks = matchedWorks.map((w) => {
                            const rawStuId = w.studentId != null ? String(w.studentId).trim() : '';
                            if (!rawStuId || !meta || !meta.studentIdToKey.has(rawStuId)) {
                                return { unknownOwner: true };
                            }
                            const stuKey = meta.studentIdToKey.get(rawStuId) || '';
                            const ownerName = meta.studentIdToName.get(rawStuId) || '';
                            const namesList = meta.studentNames || [];
                            const rawTitle = typeof w.title === 'string' ? w.title.trim().slice(0, 500) : '';
                            const hasStudentInfo = checkTitleHasStudentName(rawTitle, ownerName, namesList);
                            const cleanTitle = scrubStudentNames(rawTitle, namesList).slice(0, 500);
                            const rawWorkComment = typeof w.comment === 'string' ? w.comment.slice(0, 15000) : '';
                            const cleanWorkComment = scrubStudentNames(rawWorkComment, namesList).slice(0, 15000);
                            return {
                                ...(stuKey ? { studentKey: stuKey } : {}),
                                title: cleanTitle,
                                hasStudentInfoInTitle: Boolean(hasStudentInfo),
                                thumbnail: typeof w.thumbnail === 'string' ? w.thumbnail.trim().slice(0, 1000) : '',
                                imageUrl: Array.isArray(w.imageUrl)
                                    ? w.imageUrl.filter((u) => typeof u === 'string' && u.trim()).slice(0, 30).map((u) => u.trim().slice(0, 1000))
                                    : [],
                                attachmentUrls: Array.isArray(w.attachmentUrls)
                                    ? w.attachmentUrls.filter((u) => typeof u === 'string' && u.trim()).slice(0, 30).map((u) => u.trim().slice(0, 1000))
                                    : [],
                                videoUrls: Array.isArray(w.videoUrls)
                                    ? w.videoUrls.filter((u) => typeof u === 'string' && u.trim()).slice(0, 20).map((u) => u.trim().slice(0, 1000))
                                    : [],
                                relatedUrls: Array.isArray(w.relatedUrls)
                                    ? w.relatedUrls
                                        .filter((r) => r && typeof r === 'object' && (r.url || r.name))
                                        .slice(0, 20)
                                        .map((r) => ({
                                            name: typeof r.name === 'string' ? scrubStudentNames(r.name.trim(), namesList).slice(0, 300) : '',
                                            url: typeof r.url === 'string' ? r.url.trim().slice(0, 1000) : ''
                                        }))
                                    : [],
                                comment: cleanWorkComment
                            };
                        });
                    }
                }
            }
        }
        if (authFailed) {
            out.__auth_error__ = true;
            out.__failure_reason__ = 'graphql_auth_rejected';
        }
        return out;
    }
    """
    js_script = "\n".join(
        line.strip()
        for line in js_script.splitlines()
        if line.strip() and not line.strip().startswith("//")
    )
    clear_stale_auth_js = """
    async ({ allowedTokenKeys }) => {
        try {
            const keySet = new Set(allowedTokenKeys || []);
            if (typeof localStorage !== 'undefined' && localStorage) {
                const toRemove = [];
                for (let i = 0; i < localStorage.length; i++) {
                    const rawKey = String(localStorage.key(i) || '');
                    const k = rawKey.trim().toLowerCase();
                    const rawVal = String(localStorage.getItem(rawKey) || '').trim();
                    if (
                        keySet.has(k) ||
                        k.includes('firebase') ||
                        k.includes('token') ||
                        rawVal.startsWith('eyJ') ||
                        rawVal.includes('stsTokenManager')
                    ) {
                        toRemove.push(rawKey);
                    }
                }
                for (const rk of toRemove) {
                    localStorage.removeItem(rk);
                }
            }
            if (typeof indexedDB !== 'undefined' && indexedDB && typeof indexedDB.open === 'function') {
                await new Promise((resolve) => {
                    const timer = setTimeout(resolve, 1000);
                    try {
                        const req = indexedDB.open('firebaseLocalStorageDb');
                        req.onerror = () => { clearTimeout(timer); resolve(); };
                        req.onsuccess = () => {
                            try {
                                const db = req.result;
                                if (!db || !db.objectStoreNames || !db.objectStoreNames.contains('firebaseLocalStorage')) {
                                    if (db && typeof db.close === 'function') db.close();
                                    clearTimeout(timer);
                                    resolve();
                                    return;
                                }
                                const tx = db.transaction('firebaseLocalStorage', 'readwrite');
                                tx.objectStore('firebaseLocalStorage').clear();
                                tx.oncomplete = () => { db.close(); clearTimeout(timer); resolve(); };
                                tx.onerror = () => { db.close(); clearTimeout(timer); resolve(); };
                            } catch (_) {
                                clearTimeout(timer);
                                resolve();
                            }
                        };
                    } catch (_) {
                        clearTimeout(timer);
                        resolve();
                    }
                });
            }
        } catch (_) {}
    }
    """
    listener_page = None
    try:
        page_mod = str(getattr(type(page), "__module__", "") or "")
        current_page_url = str(getattr(page, "url", "") or "").strip()
        parsed_page_url = urlsplit(current_page_url)
        current_host = str(parsed_page_url.hostname or "").lower()

        async def _navigate_and_authenticate_lms(
            *, clear_stale_storage: bool = False
        ) -> str:
            nonlocal eval_page, temp_page, listener_page, pre_resolved_token
            try:
                from googleform_autosubmit.config import Config
                from services.lms_service import _login_lms_if_needed

                lms_url = str(getattr(Config, "LMS_URL", "") or "").strip()
                parsed_lms_url = urlsplit(lms_url)
                lms_scheme = str(parsed_lms_url.scheme or "").lower()
                lms_host = str(parsed_lms_url.hostname or "").lower()
                if lms_scheme != "https" or lms_host != LMS_ALLOWED_HOSTNAME:
                    return "skipped_invalid_lms_url"
                if eval_page is page and current_host != LMS_ALLOWED_HOSTNAME:
                    if parsed_page_url.scheme in {"", "about"} and callable(
                        getattr(page, "goto", None)
                    ):
                        eval_page = page
                    elif ctx_obj is not None and callable(
                        getattr(ctx_obj, "new_page", None)
                    ):
                        temp_page = await ctx_obj.new_page()
                        eval_page = temp_page
                if eval_page is None or not callable(getattr(eval_page, "goto", None)):
                    return "skipped_no_goto"
                if listener_page is None and callable(getattr(eval_page, "on", None)):
                    with contextlib.suppress(Exception):
                        eval_page.on("request", _on_lms_request)
                        listener_page = eval_page
                eval_parsed = urlsplit(str(getattr(eval_page, "url", "") or "").strip())
                eval_scheme = str(eval_parsed.scheme or "").lower()
                eval_host = str(eval_parsed.hostname or "").lower()
                if clear_stale_storage:
                    captured_auth.clear()
                    pre_resolved_token = ""
                    if eval_scheme != "https" or eval_host != LMS_ALLOWED_HOSTNAME:
                        await eval_page.goto(lms_url)
                        if callable(getattr(eval_page, "wait_for_load_state", None)):
                            with contextlib.suppress(Exception):
                                await eval_page.wait_for_load_state("networkidle")
                        eval_parsed = urlsplit(
                            str(getattr(eval_page, "url", "") or "").strip()
                        )
                        eval_scheme = str(eval_parsed.scheme or "").lower()
                        eval_host = str(eval_parsed.hostname or "").lower()
                    if (
                        eval_scheme == "https"
                        and eval_host == LMS_ALLOWED_HOSTNAME
                        and callable(getattr(eval_page, "evaluate", None))
                    ):
                        with contextlib.suppress(Exception):
                            await eval_page.evaluate(
                                clear_stale_auth_js,
                                {"allowedTokenKeys": sorted(ALLOWED_LMS_TOKEN_KEYS)},
                            )
                        captured_auth.clear()
                        await eval_page.goto(lms_url)
                        if callable(getattr(eval_page, "wait_for_load_state", None)):
                            with contextlib.suppress(Exception):
                                await eval_page.wait_for_load_state("networkidle")
                elif eval_scheme != "https" or eval_host != LMS_ALLOWED_HOSTNAME:
                    await eval_page.goto(lms_url)
                    if callable(getattr(eval_page, "wait_for_load_state", None)):
                        with contextlib.suppress(Exception):
                            await eval_page.wait_for_load_state("networkidle")
                post_nav = urlsplit(str(getattr(eval_page, "url", "") or "").strip())
                post_scheme = str(post_nav.scheme or "").lower()
                post_host = str(post_nav.hostname or "").lower()
                if post_scheme != "https" or post_host not in LMS_ALLOWED_LOGIN_HOSTNAMES:
                    return "untrusted_redirect"
                try:
                    await _login_lms_if_needed(eval_page)
                except asyncio.CancelledError:
                    raise
                except Exception as login_exc:
                    with contextlib.suppress(Exception):
                        log_message(
                            "Cảnh báo khi tự động đăng nhập LMS trong bước kiểm tra chất lượng.",
                            level="WARNING",
                            event="lms.quality_fetch.login_warning",
                            error_type=type(login_exc).__name__,
                        )
                after_login = urlsplit(str(getattr(eval_page, "url", "") or "").strip())
                after_scheme = str(after_login.scheme or "").lower()
                after_host = str(after_login.hostname or "").lower()
                if after_scheme != "https" or after_host not in LMS_ALLOWED_LOGIN_HOSTNAMES:
                    return "untrusted_redirect"
                if after_host != LMS_ALLOWED_HOSTNAME:
                    await eval_page.goto(lms_url)
                    if callable(getattr(eval_page, "wait_for_load_state", None)):
                        with contextlib.suppress(Exception):
                            await eval_page.wait_for_load_state("networkidle")
                    after_login = urlsplit(
                        str(getattr(eval_page, "url", "") or "").strip()
                    )
                    if (
                        str(after_login.scheme or "").lower() != "https"
                        or str(after_login.hostname or "").lower() != LMS_ALLOWED_HOSTNAME
                    ):
                        return "untrusted_redirect"
                if listener_page is not None:
                    for _ in range(20):
                        tok_candidate = captured_auth.get("token")
                        if _is_usable_lms_token(tok_candidate):
                            pre_resolved_token = str(tok_candidate)
                            break
                        await asyncio.sleep(0.25)
                return "ok"
            except asyncio.CancelledError:
                raise
            except Exception as nav_exc:
                with contextlib.suppress(Exception):
                    log_message(
                        "Cảnh báo khi điều hướng trang LMS để lấy dữ liệu buổi học.",
                        level="WARNING",
                        event="lms.quality_fetch.nav_warning",
                        error_type=type(nav_exc).__name__,
                    )
                return "nav_exception"

        if not pre_resolved_token and not page_mod.startswith("unittest.mock"):
            nav_status = await _navigate_and_authenticate_lms(
                clear_stale_storage=False
            )
            if nav_status == "untrusted_redirect":
                with contextlib.suppress(Exception):
                    log_message(
                        "Không tải được dữ liệu buổi học từ LMS do chuyển hướng ngoài tên miền cho phép.",
                        level="WARNING",
                        event="lms.quality_fetch.degraded",
                        reason="untrusted_redirect",
                        class_count=len(all_codes),
                    )
                return {
                    code: [
                        {"fetch_error": True}
                        for _ in range(MAX_LMS_SLOTS_PER_CLASS)
                    ]
                    for code in all_codes
                }

        eval_args = {
            "endpoint": LMS_GRAPHQL_ENDPOINT,
            "classCodes": normalized_codes,
            "centres": resolved_centres,
            "maxSlots": MAX_LMS_SLOTS_PER_CLASS,
            "maxStudents": MAX_LMS_STUDENTS_PER_SLOT,
            "timeoutMs": int(timeout_ms),
            "preResolvedToken": pre_resolved_token,
            "allowedHostname": LMS_ALLOWED_HOSTNAME,
            "allowedTokenKeys": sorted(ALLOWED_LMS_TOKEN_KEYS),
        }
        raw_res = await eval_page.evaluate(js_script, eval_args)
        if (
            isinstance(raw_res, dict)
            and bool(raw_res.get("__auth_error__"))
            and not page_mod.startswith("unittest.mock")
        ):
            retry_nav = await _navigate_and_authenticate_lms(
                clear_stale_storage=True
            )
            if retry_nav == "ok":
                eval_args["preResolvedToken"] = pre_resolved_token
                eval_args["centres"] = resolved_centres
                raw_res = await eval_page.evaluate(js_script, eval_args)
            elif retry_nav == "untrusted_redirect":
                return {
                    code: [
                        {"fetch_error": True}
                        for _ in range(MAX_LMS_SLOTS_PER_CLASS)
                    ]
                    for code in all_codes
                }

        if not isinstance(raw_res, dict):
            return {
                code: [{"fetch_error": True} for _ in range(MAX_LMS_SLOTS_PER_CLASS)]
                for code in all_codes
            }
        sanitized: dict[str, list[dict[str, Any]]] = {}
        for k, v in raw_res.items():
            if str(k).startswith("__"):
                continue
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
        if is_lms_fetch_unavailable(sanitized, normalized_codes):
            with contextlib.suppress(Exception):
                log_message(
                    "Không tải được dữ liệu buổi học từ LMS; chuyển sang cờ kiểm tra thủ công.",
                    level="WARNING",
                    event="lms.quality_fetch.degraded",
                    reason=str(raw_res.get("__failure_reason__") or "fetch_error"),
                    class_count=len(all_codes),
                )
        return sanitized
    except asyncio.CancelledError:
        raise
    except Exception as eval_exc:
        with contextlib.suppress(Exception):
            log_message(
                "Lỗi ngoại lệ khi tải dữ liệu buổi học từ LMS; chuyển sang cờ kiểm tra thủ công.",
                level="WARNING",
                event="lms.quality_fetch.degraded",
                reason="evaluate_exception",
                error_type=type(eval_exc).__name__,
                class_count=len(all_codes),
            )
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
            has_l14_lms_data = (
                (has_lms_map and bool(class_code) and class_code in slots_map)
                or bool(result.get("lms_quality_issues"))
                or bool(result.get("quality_issues"))
                or bool(st_dict and st_dict.get("lms_quality_issues"))
                or bool(st_dict and st_dict.get("quality_issues"))
            )
            if not has_l14_lms_data:
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
        elif has_lms_map:
            result.pop("lms_fetch_unavailable", None)
            if st_dict is not None:
                st_dict.pop("lms_fetch_unavailable", None)


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
