"""LMS & Zalo content quality inspection service (Wave 4 — Stage 4.2).

Evaluates:
1. LMS lesson summary (`slot.summary`) for substantive content vs empty/placeholder text.
2. Session type classification: Checkpoint sessions (Coding B5/B9, Robotics B4/B8, or Rubric)
   vs Regular sessions (all other sessions except exempted Lesson 14).
3. LMS per-student comments (`studentAttendance`) for attended students (`ATTENDED`, `LATE_ARRIVED`),
   checking 3-part content on regular sessions and competency rubric on checkpoint sessions,
   without ever exposing student PII (names or raw comments) in issue reports.
4. Zalo report content issues: wrong date, wrong lesson number, or lesson title mismatch against LMS.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import concurrent.futures
import copy
from datetime import date, datetime
import asyncio
import base64
import contextlib
import hashlib
import html
import inspect
import json
import math
import os
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from core.time_utils import BUSINESS_TIMEZONE, now_business
from logger_utils import log_message
from services.bcnb_safety import fold_text, normalize_auto_issue_text
from services.report_status import (
    ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
    ISSUE_CODE_LMS_COMMENT_NOTE,
    ISSUE_CODE_LMS_MANUAL_CHECK,
    ISSUE_CODE_LMS_QUALITY_FAIL,
    ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
    ISSUE_CODE_LMS_SPCK_MISSING,
    ISSUE_CODE_LMS_SPCK_NOTE,
    ISSUE_CODE_LMS_SUMMARY_NOTE,
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
from services.zalo_target_eligibility import (
    DEFERRED_INVALID_TARGET,
    READY_PREVIOUS_WEEK,
    evaluate_scan_target,
    parse_group_end_time,
)

MAX_LMS_QUALITY_CLASSES_PER_BATCH = 60
MAX_LMS_SLOTS_PER_CLASS = 20
MAX_LMS_STUDENTS_PER_SLOT = 50
MIN_SUBSTANTIVE_SUMMARY_CHARS = 15
MIN_SPCK_COMMENT_CHARS = 50
MIN_SPCK_COMMENT_WORDS = 12
SPCK_REQUIRED_LESSONS = frozenset({12, 13, 14})
SPCK_SLIDE_YOUTUBE_LESSONS = frozenset({14})


def _is_spck_exempt_course(class_code: str) -> bool:
    """PTB and CSB courses do not require SPCK in sessions 12–14."""
    return bool(
        re.search(r"(?:^|-)(?:PTB|CSB)(?:[0-9]+)?(?:-|$)", normalize_class_code(class_code))
    )


LMS_GRAPHQL_ENDPOINT = "https://lms-api.mindx.edu.vn/"
LMS_ALLOWED_HOSTNAME = "lms.mindx.edu.vn"
LMS_ALLOWED_LOGIN_HOSTNAMES = frozenset(
    {"lms.mindx.edu.vn", "id.mindx.edu.vn", "base.mindx.edu.vn"}
)
ALLOWED_LMS_TOKEN_KEYS = frozenset({"id_token", "access_token", "lms_token", "auth_token", "token"})

ATTENDED_STATUSES = frozenset({"ATTENDED", "LATE_ARRIVED"})
ABSENT_STATUSES = frozenset({"ABSENT", "ABSENT_WITH_NOTICE"})

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
    r"(?:nh[aậ]n\s+x[eé]t\s+bu[oổ]i\s+h[oọ]c\s+s[oố]\s*\d+\s*:\s*)?",
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
    "thao tac tot",
    "thao tac nhanh",
    "thao tac thanh thao",
    "thao tac chinh xac",
    "chu dong",
    "tich cuc",
    "nhanh nhen",
    "sang tao",
    "tu duy tot",
    "lap rap dung",
    "lap rap tot",
    "lap trinh duoc",
    "van dung tot",
    "ap dung duoc",
    "thuc hanh tot",
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
        pillar_hits = sum(1 for p in ("kien thuc", "ky nang", "thai do") if p in folded)
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
    summary_issue = make_structured_issue(
        ISSUE_CODE_LMS_SUMMARY_NOTE,
        "[LMS] Chưa ghi nội dung bài học",
        severity=SEVERITY_NOTE,
    )
    clean_text = _strip_html(summary_raw)
    single_line = " ".join(clean_text.split()).strip()
    folded_full = fold_text(single_line).strip(" -–—:.,;!?()[]{}\"'")
    if not folded_full:
        return {
            "has_valid_summary": False,
            "summary_clean": "",
            "substance_text": "",
            "issue": summary_issue,
        }
    for pattern in SUMMARY_PLACEHOLDER_PATTERNS:
        if re.fullmatch(pattern, folded_full):
            return {
                "has_valid_summary": False,
                "summary_clean": single_line,
                "substance_text": "",
                "issue": summary_issue,
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
            "issue": summary_issue,
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
    without_header = re.sub(
        r"^\s*(?:\[hs\]\s*)+(?=\([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\))",
        "",
        without_header,
        flags=re.IGNORECASE,
    ).strip()
    return re.sub(r"^\s*\([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\)\s*", "", without_header).strip()


_TRIVIAL_SINGLE_TOKEN_FILLERS = frozenset(
    {"ok", "okay", "good", "none", "na", "n/a", "x", "test", "."}
)

_ABSENCE_STATEMENT_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"(?:hom\s+nay|buoi\s+nay|buoi\s+hoc\s+hom\s+nay)\s+"
    r"(?:ban\s+|hv\s+|hoc\s+vien\s+|con\s+|em\s+|be\s+|gia\s+dinh\s+|phu\s+huynh\s+)*"
    r"(?:xin\s+phep\s+cho\s+(?:ban|con|em|be|hv)\s+|xin\s+phep\s+|bao\s+|xin\s+|co\s+|da\s+)*"
    r"(?:nghi|vang)(?:\s+hoc|\s+om|\s+mat|\s+phep|\s+co\s+phep|\s+khong\s+phep)?"
    r"|(?:ban|hv|hoc\s+vien|con|em|be|gia\s+dinh|phu\s+huynh)\s+"
    r"(?:xin\s+phep\s+cho\s+(?:ban|con|em|be|hv)\s+|xin\s+phep\s+|bao\s+cho\s+(?:ban|con|em|be)\s+|bao\s+|xin\s+|co\s+|da\s+|hom\s+nay\s+|buoi\s+nay\s+)*"
    r"(?:nghi|vang)(?:\s+hoc|\s+om|\s+mat|\s+phep|\s+co\s+phep|\s+khong\s+phep|\s+buoi\s+nay|\s+hom\s+nay)?"
    r"|(?:nghi\s+hoc|nghi\s+om|nghi\s+co\s+phep|nghi\s+khong\s+phep|xin\s+phep\s+nghi|xin\s+nghi|"
    r"vang\s+mat|vang\s+phep|vang\s+khong\s+phep|vang\s+hoc|hoc\s+vien\s+vang|ban\s+vang|bao\s+nghi|bao\s+vang)"
    r"|^(?:nghi|vang)$"
    r")(?![a-z0-9])",
    flags=re.IGNORECASE,
)

_NEGATED_ABSENCE_RE = re.compile(
    r"\b(?:khong|chua|chang|ko|k)\s+"
    r"(?:tung\s+|bao\s+gio\s+|he\s+|co\s+|bi\s+|phai\s+|hay\s+|ngay\s+nao\s+|buoi\s+nao\s+|xin\s+)*"
    r"(?:nghi(?:\s+hoc|\s+om|\s+buoi|\s+phep|\s+buoi\s+nao)?|vang(?:\s+mat|\s+hoc|\s+phep|\s+buoi\s+nao)?|bo\s+buoi)\b"
    r"|\bdi\s+hoc\s+day\s+du\b",
    flags=re.IGNORECASE,
)

_PAST_OR_FUTURE_ABSENCE_RE = re.compile(
    r"\b(?:nghi|vang|xin\s+nghi|xin\s+phep\s+nghi)\s+(?:hoc\s+|om\s+)?(?:buoi\s+truoc|tuan\s+truoc|hom\s+truoc|bai\s+truoc|buoi\s+vua\s+roi|buoi\s+sau|tuan\s+sau|buoi\s+toi|buoi\s+tiep\s+theo|buoi\s+ke\s+tiep)\b"
    r"|\b(?:buoi\s+truoc|tuan\s+truoc|hom\s+truoc|bai\s+truoc|buoi\s+vua\s+roi|buoi\s+sau|tuan\s+sau|buoi\s+toi)\s+(?:ban\s+|con\s+|em\s+|be\s+|hv\s+|hoc\s+vien\s+)?(?:da\s+|xin\s+|co\s+)*(?:nghi|vang)(?:\s+hoc|\s+om|\s+phep)?\b",
    flags=re.IGNORECASE,
)

_OTHER_SESSION_ANCHOR_RE = re.compile(
    r"\b(?:buoi\s+truoc|tuan\s+truoc|hom\s+truoc|bai\s+truoc|buoi\s+vua\s+roi|"
    r"buoi\s+sau|tuan\s+sau|buoi\s+toi|buoi\s+tiep\s+theo|buoi\s+ke\s+tiep)\b",
    flags=re.IGNORECASE,
)

_CURRENT_SESSION_ANCHOR_RE = re.compile(
    r"\b(?:hom\s+nay|buoi\s+nay|buoi\s+hoc\s+hom\s+nay|buoi\s+hoc\s+nay)\b",
    flags=re.IGNORECASE,
)

_IN_CLASS_PARTICIPATION_RE = re.compile(
    r"\b(?:tren\s+lop\s+(?:con\s+|ban\s+|em\s+)?(?:hoan\s+thanh|lam\s+xong|thuc\s+hanh|nam\s+vung|hieu\s+bai)|"
    r"trong\s+gio\s+hoc\s+(?:con\s+|ban\s+|em\s+)?(?:tap\s+trung|phat\s+bieu|hoan\s+thanh)|"
    r"di\s+hoc\s+dung\s+gio)\b",
    flags=re.IGNORECASE,
)

_VI_CAP_WORD = (
    r"[A-ZĐÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ]"
    r"[a-zđàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ]+"
)
_HONORIFIC_NAME_RE = re.compile(
    rf"\b(?P<hon>(?i:bạn|bé|em|con|hv|học\s+viên))\s+(?P<words>{_VI_CAP_WORD}(?:\s+{_VI_CAP_WORD}){{0,3}})\b"
)
_FULL_VI_NAME_RE = re.compile(
    rf"\b(?:Nguyễn|Trần|Lê|Phạm|Hoàng|Huỳnh|Phan|Vũ|Võ|Đặng|Bùi|Đỗ|Hồ|Ngô|Dương|Lý|Đinh|Đoàn|Trương|Lương|Mai|Tạ|Châu|Tô|Hà|Thái|Kiều|Cao|Lâm|Đào|Vương|Trịnh|Nghiêm|Tống|Quách|Từ|Phùng|Chu)"
    rf"(?:\s+{_VI_CAP_WORD}){{1,3}}\b"
)
_UNPREFIXED_MULTIWORD_VI_NAME_RE = re.compile(rf"\b{_VI_CAP_WORD}(?:\s+{_VI_CAP_WORD}){{2,4}}\b")
_VI_SURNAMES_FOLDED = frozenset(
    {
        "nguyen",
        "tran",
        "le",
        "pham",
        "hoang",
        "huynh",
        "phan",
        "vu",
        "vo",
        "dang",
        "bui",
        "do",
        "ho",
        "ngo",
        "duong",
        "ly",
        "dinh",
        "doan",
        "truong",
        "luong",
        "mai",
        "ta",
        "chau",
        "to",
        "ha",
        "thai",
        "kieu",
        "cao",
        "lam",
        "dao",
        "vuong",
        "trinh",
        "nghiem",
        "tong",
        "quach",
        "tu",
        "phung",
        "chu",
        "la",
        "au",
        "mac",
        "khuc",
        "luc",
        "nguy",
        "trieu",
        "ton",
        "luu",
        "tang",
        "dam",
        "bach",
        "khong",
        "han",
        "diep",
    }
)
_NON_NAME_STOPWORDS_FOLDED = frozenset(
    {
        "bai",
        "tap",
        "buoi",
        "hoc",
        "vong",
        "lap",
        "ham",
        "bien",
        "mang",
        "chuoi",
        "lop",
        "nhom",
        "cau",
        "hoi",
        "tro",
        "choi",
        "vat",
        "mo",
        "hinh",
        "mach",
        "cu",
        "xong",
        "rap",
        "robot",
        "robotics",
        "python",
        "scratch",
        "javascript",
        "html",
        "css",
        "roblox",
        "unity",
        "arduino",
        "microbit",
        "canva",
        "figma",
        "slide",
        "video",
        "game",
        "web",
        "app",
        "code",
        "coding",
        "mindx",
        "checkpoint",
        "muc",
        "chuong",
        "nhanh",
        "cham",
        "logic",
        "spck",
        "project",
        "lesson",
        "session",
        "slot",
        "node",
        "next",
        "react",
        "vue",
        "sql",
    }
)
_NON_NAME_BIGRAMS_FOLDED = frozenset(
    {
        ("du", "an"),
        ("cuoi", "khoa"),
        ("hoan", "thanh"),
        ("nam", "vung"),
        ("nam", "bai"),
        ("nam", "duoc"),
        ("hieu", "bai"),
        ("phat", "trien"),
        ("phat", "bieu"),
        ("san", "pham"),
        ("bai", "tap"),
        ("bai", "hoc"),
        ("buoi", "hoc"),
        ("vong", "lap"),
        ("dieu", "kien"),
        ("thuyet", "trinh"),
        ("lap", "trinh"),
        ("lap", "rap"),
        ("kien", "thuc"),
        ("ky", "nang"),
        ("thai", "do"),
        ("thai", "duong"),
        ("tu", "duy"),
        ("tich", "cuc"),
        ("chu", "dong"),
        ("thuat", "toan"),
        ("danh", "sach"),
        ("doi", "tuong"),
        ("nhan", "vat"),
        ("ung", "dung"),
        ("thiet", "ke"),
        ("giao", "dien"),
        ("chuc", "nang"),
        ("tinh", "nang"),
        ("thao", "tac"),
        ("diem", "manh"),
        ("cai", "thien"),
        ("bo", "sung"),
        ("sang", "tao"),
        ("chi", "tiet"),
    }
)
_NON_NAME_CLAUSE_SUBJECTS_FOLDED = frozenset(
    {
        "con",
        "em",
        "ban",
        "be",
        "hv",
        "hoc",
        "lop",
        "nhom",
        "bai",
        "buoi",
        "thay",
        "co",
        "gia",
        "phu",
        "me",
        "bo",
        "ba",
        "anh",
        "chi",
        "ca",
        "phan",
        "muc",
        "chuong",
        "san",
        "du",
        "kien",
        "ky",
    }
    | _NON_NAME_STOPWORDS_FOLDED
)
_CLAUSE_SUBJECT_NAME_RE = re.compile(
    rf"(?P<pre>(?:^|[.!?\n;,]+\s*)(?:(?i:riêng|còn|với|đối\s+với|như|về\s+phần)\s+)?)(?P<name>{_VI_CAP_WORD})(?=(?:,\s*(?i:con|em|bạn|bé|hv|học\s+viên)\b|(?:\s*,)?\s+(?i:nắm\s+vững|nắm\s+được|nắm\s+bài|hiểu\s+bài|hiểu\s+được|hoàn\s+thành|hoàn\s+thiện|làm\s+bài|làm\s+tốt|làm\s+xong|làm\s+rất|thực\s+hành|tiếp\s+thu|tập\s+trung|chú\s+ý|tham\s+gia|phát\s+biểu|lắp\s+ráp|lập\s+trình|thuyết\s+trình|trình\s+bày|vận\s+dụng|áp\s+dụng|biết\b|đã\b|cần\b|chưa\b|rất\b|có\s+thái\s+độ|có\s+ý\s+thức|đi\s+học|vào\s+lớp|ngồi\s+học|nghe\s+giảng|tương\s+tác|thao\s+tác)))"
)
_EMAIL_SCRUB_RE = re.compile(
    r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
    flags=re.IGNORECASE,
)
_URL_SCRUB_RE = re.compile(
    r"(?:https?://|//|www\.)[^\s\"'<>]+"
    r"|\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}(?::\d+)?/[^\s\"'<>]*"
    r"|\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com\.vn|edu\.vn|gov\.vn|net\.vn|org\.vn|ac\.vn|info\.vn|pro\.vn|health\.vn|name\.vn|biz\.vn|int\.vn|com|vn|net|org|edu|gov|io|app|dev|me|info|co|ai|xyz|online|site|tech|cloud|page|gg|ly|invalid|local|test)\b(?::\d+)?(?:/[^\s\"'<>]*)?",
    flags=re.IGNORECASE,
)
_PHONE_CANDIDATE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-]*)?(?:\(\+?\d{1,4}\)[\s.-]*)?\d(?:[\d\s().-]{5,18}\d)(?!\w)"
)

_CONCRETE_LEARNING_SIGNALS = (
    "kien thuc",
    "ky nang",
    "bai hoc",
    "bai tap",
    "thuc hanh",
    "lap trinh",
    "code",
    "thuat toan",
    "logic",
    "bien",
    "ham",
    "vong lap",
    "dieu kien",
    "danh sach",
    "list",
    "array",
    "object",
    "class",
    "function",
    "python",
    "scratch",
    "javascript",
    "html",
    "css",
    "web",
    "trang web",
    "game",
    "tro choi",
    "nhan vat",
    "du an",
    "san pham",
    "spck",
    "project",
    "robot",
    "mo hinh",
    "lap rap",
    "cam bien",
    "dong co",
    "mach",
    "arduino",
    "ung dung",
    "giao dien",
    "chuc nang",
    "tinh nang",
    "thao tac",
    "cu phap",
    "nam bai",
    "nam vung",
    "nam duoc",
    "hieu bai",
    "hieu duoc",
    "van dung",
    "ap dung",
    "hoan thanh",
    "hoan thien",
    "thiet ke",
    "trinh bay",
    "thuyet trinh",
    "slide",
)

_DEFAULT_AI_REVIEWER: Any = None


def _is_placeholder_or_dot_bypass(text: str | None) -> bool:
    """Return True when comment text is empty, punctuation/dot-only, or a single trivial filler token."""
    body = _strip_comment_header_and_student_name(text)
    if not body:
        return True
    no_hs = re.sub(r"\[hs\]", " ", body, flags=re.IGNORECASE).strip()
    if not no_hs:
        return True
    folded = fold_text(no_hs).strip()
    stripped_punct = folded.strip(" -–—:.,;!?()[]{}\"'/_*~`@#$%^&+=|\\<>")
    if not stripped_punct or stripped_punct in _TRIVIAL_SINGLE_TOKEN_FILLERS:
        return True
    alnum_tokens = re.findall(r"[a-z0-9]+", folded)
    if not alnum_tokens:
        return True
    if len(alnum_tokens) == 1 and (
        alnum_tokens[0] in _TRIVIAL_SINGLE_TOKEN_FILLERS or len(alnum_tokens[0]) == 1
    ):
        return True
    return False


_CLAUSE_SPLIT_FOLDED_RE = re.compile(
    r"[.!?\n;]+"
    r"|,\s*(?=(?:nhung|tuy\s+nhien|con|rieng|hom\s+nay|buoi\s+nay|buoi\s+hoc\s+hom\s+nay|buoi\s+hoc\s+nay|buoi\s+hom\s+nay|hien\s+tai|toi\s+nay|sang\s+nay|chieu\s+nay|gia\s+dinh|phu\s+huynh|me|bo|ba|ban|be|em|buoi\s+truoc|tuan\s+truoc|hom\s+truoc|lan\s+truoc|buoi\s+sau|tuan\s+sau)\b)"
    r"|\b(?:nhung|tuy\s+nhien)\s+(?=(?:hom\s+nay|buoi\s+nay|buoi\s+hoc|buoi\s+hom\s+nay|con|ban|em|be|buoi\s+truoc|tuan\s+truoc)\b)",
    flags=re.IGNORECASE,
)


def _is_absence_statement_in_comment(body: str | None) -> bool:
    """Return True when a comment explicitly states the student was absent in the current session."""
    clean = _strip_comment_header_and_student_name(body)
    if not clean:
        return False
    no_hs = re.sub(r"\[hs\]", " ", clean, flags=re.IGNORECASE).strip()
    if not no_hs:
        return False
    folded_all = fold_text(no_hs)
    clauses = _CLAUSE_SPLIT_FOLDED_RE.split(folded_all)
    has_current_absence = False
    has_current_in_class_participation = False
    for raw_clause in clauses:
        clause_folded = raw_clause.strip(" -–—:.,;!?()[]{}\"'")
        if not clause_folded:
            continue
        if _OTHER_SESSION_ANCHOR_RE.search(clause_folded) and not _CURRENT_SESSION_ANCHOR_RE.search(
            clause_folded
        ):
            continue
        if _IN_CLASS_PARTICIPATION_RE.search(clause_folded):
            has_current_in_class_participation = True
        without_negated = _NEGATED_ABSENCE_RE.sub(" ", clause_folded)
        without_other_sessions = _PAST_OR_FUTURE_ABSENCE_RE.sub(" ", without_negated).strip(
            " -–—:.,;!?()[]{}\"'"
        )
        if without_other_sessions and _ABSENCE_STATEMENT_RE.search(without_other_sessions):
            has_current_absence = True
    return has_current_absence and not has_current_in_class_participation


def _truncate_non_name_tokens(tokens: list[str]) -> list[str]:
    kept: list[str] = []
    folded_tokens = [fold_text(t).strip() for t in tokens]
    i = 0
    while i < len(tokens):
        ft = folded_tokens[i]
        if not ft or ft in _NON_NAME_STOPWORDS_FOLDED:
            break
        if i + 1 < len(tokens) and (ft, folded_tokens[i + 1]) in _NON_NAME_BIGRAMS_FOLDED:
            break
        kept.append(tokens[i])
        i += 1
    return kept


def _scrub_comment_for_ai(
    text: str | None,
    *,
    known_student_names: Sequence[str] | None = None,
) -> str:
    """Scrub URLs, emails, phone numbers, and Vietnamese proper names before sending to AI."""
    clean = _strip_comment_header_and_student_name(text)
    if not clean:
        return ""
    scrubbed = clean
    if known_student_names:
        given_tokens_set: set[str] = set()
        for raw_name in known_student_names:
            norm_name = " ".join(str(raw_name or "").split()).strip()
            if len(norm_name) >= 3:
                scrubbed = re.sub(
                    re.escape(norm_name),
                    "[HS]",
                    scrubbed,
                    flags=re.IGNORECASE,
                )
                tokens = norm_name.split()
                if len(tokens) >= 2:
                    tail_two = " ".join(tokens[-2:])
                    if len(tail_two) >= 4:
                        scrubbed = re.sub(
                            rf"\b{re.escape(tail_two)}\b",
                            "[HS]",
                            scrubbed,
                            flags=re.IGNORECASE,
                        )
                    given_tok = tokens[-1]
                    given_folded = fold_text(given_tok).strip()
                    if (
                        len(given_tok) >= 2
                        and given_folded
                        and given_folded not in _NON_NAME_STOPWORDS_FOLDED
                    ):
                        scrubbed = re.sub(
                            rf"\b((?:bạn|ban|bé|be|em|con|hv|học\s+viên|hoc\s+vien)\s+){re.escape(given_tok)}\b",
                            r"\g<1>[HS]",
                            scrubbed,
                            flags=re.IGNORECASE,
                        )
                        given_tokens_set.add(given_folded)
        if given_tokens_set:
            spans = [
                (m.start(), m.end(), m.group(0), fold_text(m.group(0)).strip())
                for m in re.finditer(r"[^\W\d_]+", scrubbed, flags=re.UNICODE)
            ]
            parts: list[str] = []
            cursor = 0
            for idx, (s_start, s_end, _raw_w, folded_w) in enumerate(spans):
                if folded_w not in given_tokens_set:
                    continue
                if (
                    s_start > 0
                    and s_end < len(scrubbed)
                    and scrubbed[s_start - 1] == "["
                    and scrubbed[s_end] == "]"
                ):
                    continue
                prev_space_only = idx > 0 and scrubbed[spans[idx - 1][1] : s_start].isspace()
                next_space_only = (
                    idx + 1 < len(spans) and scrubbed[s_end : spans[idx + 1][0]].isspace()
                )
                if (
                    prev_space_only and (spans[idx - 1][3], folded_w) in _NON_NAME_BIGRAMS_FOLDED
                ) or (
                    next_space_only and (folded_w, spans[idx + 1][3]) in _NON_NAME_BIGRAMS_FOLDED
                ):
                    continue
                parts.append(scrubbed[cursor:s_start])
                parts.append("[HS]")
                cursor = s_end
            if parts:
                parts.append(scrubbed[cursor:])
                scrubbed = "".join(parts)
    scrubbed = _EMAIL_SCRUB_RE.sub("[EMAIL]", scrubbed)

    def _replace_url_match(m: re.Match[str]) -> str:
        raw_url = m.group(0)
        stripped = raw_url.rstrip(".,;:!?)]}")
        trail = raw_url[len(stripped) :]
        return f"[URL]{trail}"

    scrubbed = _URL_SCRUB_RE.sub(_replace_url_match, scrubbed)

    def _replace_phone_match(m: re.Match[str]) -> str:
        candidate = m.group(0)
        digit_count = sum(1 for ch in candidate if ch.isdigit())
        if 7 <= digit_count <= 15:
            return "[PHONE]"
        return candidate

    scrubbed = _PHONE_CANDIDATE_RE.sub(_replace_phone_match, scrubbed)

    def _replace_honorific_name(m: re.Match[str]) -> str:
        hon = m.group("hon")
        words = m.group("words").split()
        kept_name = _truncate_non_name_tokens(words)
        if not kept_name:
            return m.group(0)
        tail = words[len(kept_name) :]
        if tail:
            return f"{hon} [HS] {' '.join(tail)}"
        return f"{hon} [HS]"

    def _replace_full_vi_name(m: re.Match[str]) -> str:
        words = m.group(0).split()
        kept_name = _truncate_non_name_tokens(words)
        if len(kept_name) < 2:
            return m.group(0)
        tail = words[len(kept_name) :]
        if tail:
            return f"[HS] {' '.join(tail)}"
        return "[HS]"

    def _replace_unprefixed_multiword_name(m: re.Match[str]) -> str:
        words = m.group(0).split()
        if not words or fold_text(words[0]).strip() not in _VI_SURNAMES_FOLDED:
            return m.group(0)
        kept_name = _truncate_non_name_tokens(words)
        if len(kept_name) < 2:
            return m.group(0)
        tail = words[len(kept_name) :]
        if tail:
            return f"[HS] {' '.join(tail)}"
        return "[HS]"

    def _replace_clause_subject_name(m: re.Match[str]) -> str:
        name_w = m.group("name")
        folded_w = fold_text(name_w).strip()
        if not folded_w or folded_w in _NON_NAME_CLAUSE_SUBJECTS_FOLDED:
            return m.group(0)
        return f"{m.group('pre')}[HS]"

    scrubbed = _HONORIFIC_NAME_RE.sub(_replace_honorific_name, scrubbed)
    scrubbed = _FULL_VI_NAME_RE.sub(_replace_full_vi_name, scrubbed)
    scrubbed = _UNPREFIXED_MULTIWORD_VI_NAME_RE.sub(_replace_unprefixed_multiword_name, scrubbed)
    scrubbed = _CLAUSE_SUBJECT_NAME_RE.sub(_replace_clause_subject_name, scrubbed)
    scrubbed = re.sub(r"(?:\[HS\]\s*){2,}", "[HS] ", scrubbed, flags=re.IGNORECASE)
    return " ".join(scrubbed.split())[:600]


def _normalize_ai_verdict(raw_entry: Any) -> str:
    if isinstance(raw_entry, str):
        return raw_entry.strip().upper()
    if isinstance(raw_entry, Mapping):
        return (
            str(
                raw_entry.get("classification")
                or raw_entry.get("label")
                or raw_entry.get("verdict")
                or ""
            )
            .strip()
            .upper()
        )
    return ""


def _sanitize_ai_comment_batch(
    items: list[dict[str, Any]],
    *,
    lesson_summary: str = "",
    comment_kind: str = "SESSION",
    known_student_names: Sequence[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    clean_summary = _scrub_comment_for_ai(
        _strip_html(lesson_summary),
        known_student_names=known_student_names,
    )[:240]
    sanitized_payload: list[dict[str, Any]] = []
    for entry in items:
        item_id = str(entry.get("id") or "").strip()
        scrubbed_text = _scrub_comment_for_ai(
            str(entry.get("text") or ""),
            known_student_names=known_student_names,
        )
        if not item_id or not scrubbed_text:
            continue
        sanitized_payload.append(
            {
                "id": item_id,
                "type": str(entry.get("type") or f"{comment_kind}_COMMENT"),
                "text": scrubbed_text,
            }
        )
    return clean_summary, sanitized_payload


def _resolve_reviewer_timeout(active_reviewer: Any) -> float:
    for attr in ("timeout_seconds", "_timeout_seconds"):
        val = getattr(active_reviewer, attr, None)
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            return max(0.1, float(val))
    with contextlib.suppress(Exception):
        from googleform_autosubmit.config import Config

        return max(0.1, float(getattr(Config, "LMS_AI_TIMEOUT_SECONDS", 8.0)))
    return 8.0


def _run_coro_sync(coro: Any, timeout_seconds: float) -> Any:
    try:
        asyncio.get_running_loop()
        loop_running = True
    except RuntimeError:
        loop_running = False
    if not loop_running:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout_seconds))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = executor.submit(lambda: asyncio.run(asyncio.wait_for(coro, timeout=timeout_seconds)))
        return fut.result(timeout=timeout_seconds + 0.25)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


class _BatchRecordingReviewer:
    """Internal collector used by async fetch_and_evaluate_lms_quality to gather gray-zone batches."""

    def __init__(self) -> None:
        self.batches: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []

    def record(self, items: list[dict[str, Any]], **kwargs: Any) -> dict[str, str]:
        self.batches.append((list(items), dict(kwargs)))
        return {}


class _PrecomputedBatchReviewer:
    """Internal proxy returning pre-awaited async AI verdicts in batch order."""

    def __init__(self, verdicts_by_batch: list[dict[str, str]]) -> None:
        self._verdicts = list(verdicts_by_batch)
        self._idx = 0

    def next_verdicts(self) -> dict[str, str]:
        if self._idx < len(self._verdicts):
            res = self._verdicts[self._idx]
            self._idx += 1
            return res
        return {}


async def _invoke_ai_comment_reviewer_async(
    active_reviewer: Any,
    items: list[dict[str, Any]],
    *,
    class_code: str = "",
    khoi: str = "",
    lesson: str | int | None = "",
    lesson_summary: str = "",
    comment_kind: str = "SESSION",
    known_student_names: Sequence[str] | None = None,
) -> dict[str, str]:
    if active_reviewer is None or not items:
        return {}
    clean_summary, sanitized_payload = _sanitize_ai_comment_batch(
        items,
        lesson_summary=lesson_summary,
        comment_kind=comment_kind,
        known_student_names=known_student_names,
    )
    if not sanitized_payload:
        return {}
    timeout_s = _resolve_reviewer_timeout(active_reviewer)
    raw_verdicts: Any = None
    with contextlib.suppress(Exception):
        if hasattr(active_reviewer, "review_short_comments"):
            fn = active_reviewer.review_short_comments
            if inspect.iscoroutinefunction(fn):
                try:
                    coro = fn(
                        sanitized_payload,
                        class_code=class_code,
                        khoi=khoi,
                        lesson=lesson,
                        lesson_summary=clean_summary,
                        comment_kind=comment_kind,
                        known_student_names=known_student_names,
                    )
                except TypeError:
                    try:
                        coro = fn(
                            sanitized_payload,
                            class_code=class_code,
                            khoi=khoi,
                            lesson=lesson,
                            lesson_summary=clean_summary,
                            comment_kind=comment_kind,
                        )
                    except TypeError:
                        try:
                            coro = fn(
                                sanitized_payload,
                                lesson_summary=clean_summary,
                            )
                        except TypeError:
                            coro = fn(sanitized_payload)
                raw_verdicts = await asyncio.wait_for(coro, timeout=timeout_s)
            else:

                def _call_short() -> Any:
                    try:
                        return fn(
                            sanitized_payload,
                            class_code=class_code,
                            khoi=khoi,
                            lesson=lesson,
                            lesson_summary=clean_summary,
                            comment_kind=comment_kind,
                        )
                    except TypeError:
                        try:
                            return fn(sanitized_payload, lesson_summary=clean_summary)
                        except TypeError:
                            return fn(sanitized_payload)

                res = await asyncio.wait_for(asyncio.to_thread(_call_short), timeout=timeout_s)
                raw_verdicts = (
                    await asyncio.wait_for(res, timeout=timeout_s)
                    if inspect.iscoroutine(res)
                    else res
                )
        elif hasattr(active_reviewer, "review_comments"):
            fn = active_reviewer.review_comments
            if inspect.iscoroutinefunction(fn):
                try:
                    coro = fn(sanitized_payload, lesson_summary=clean_summary)
                except TypeError:
                    coro = fn(sanitized_payload)
                raw_verdicts = await asyncio.wait_for(coro, timeout=timeout_s)
            else:

                def _call_rc() -> Any:
                    try:
                        return fn(sanitized_payload, lesson_summary=clean_summary)
                    except TypeError:
                        return fn(sanitized_payload)

                res = await asyncio.wait_for(asyncio.to_thread(_call_rc), timeout=timeout_s)
                raw_verdicts = (
                    await asyncio.wait_for(res, timeout=timeout_s)
                    if inspect.iscoroutine(res)
                    else res
                )
        elif hasattr(active_reviewer, "review_short_comments_sync"):
            fn_sync = active_reviewer.review_short_comments_sync
            raw_verdicts = await asyncio.wait_for(
                asyncio.to_thread(
                    fn_sync,
                    sanitized_payload,
                    class_code=class_code,
                    khoi=khoi,
                    lesson=lesson,
                    lesson_summary=clean_summary,
                    comment_kind=comment_kind,
                    known_student_names=known_student_names,
                ),
                timeout=timeout_s,
            )

    if not isinstance(raw_verdicts, Mapping):
        return {}
    return {
        str(k): _normalize_ai_verdict(v)
        for k, v in raw_verdicts.items()
        if _normalize_ai_verdict(v)
    }


def _invoke_ai_comment_reviewer(
    active_reviewer: Any,
    items: list[dict[str, Any]],
    *,
    class_code: str = "",
    khoi: str = "",
    lesson: str | int | None = "",
    lesson_summary: str = "",
    comment_kind: str = "SESSION",
    known_student_names: Sequence[str] | None = None,
) -> dict[str, str]:
    if active_reviewer is None or not items:
        return {}
    if isinstance(active_reviewer, _BatchRecordingReviewer):
        return active_reviewer.record(
            items,
            class_code=class_code,
            khoi=khoi,
            lesson=lesson,
            lesson_summary=lesson_summary,
            comment_kind=comment_kind,
            known_student_names=known_student_names,
        )
    if isinstance(active_reviewer, _PrecomputedBatchReviewer):
        return active_reviewer.next_verdicts()

    clean_summary, sanitized_payload = _sanitize_ai_comment_batch(
        items,
        lesson_summary=lesson_summary,
        comment_kind=comment_kind,
        known_student_names=known_student_names,
    )
    if not sanitized_payload:
        return {}

    timeout_s = _resolve_reviewer_timeout(active_reviewer)
    raw_verdicts: Any = None
    with contextlib.suppress(Exception):
        if hasattr(active_reviewer, "review_short_comments_sync"):
            raw_verdicts = active_reviewer.review_short_comments_sync(
                sanitized_payload,
                class_code=class_code,
                khoi=khoi,
                lesson=lesson,
                lesson_summary=clean_summary,
                comment_kind=comment_kind,
            )
        elif hasattr(active_reviewer, "review_short_comments"):
            fn = active_reviewer.review_short_comments
            try:
                raw_verdicts = fn(
                    sanitized_payload,
                    class_code=class_code,
                    khoi=khoi,
                    lesson=lesson,
                    lesson_summary=clean_summary,
                    comment_kind=comment_kind,
                )
            except TypeError:
                try:
                    raw_verdicts = fn(
                        sanitized_payload,
                        lesson_summary=clean_summary,
                    )
                except TypeError:
                    raw_verdicts = fn(sanitized_payload)
            if inspect.iscoroutine(raw_verdicts):
                raw_verdicts = _run_coro_sync(raw_verdicts, timeout_s)
        elif hasattr(active_reviewer, "review_comments"):
            fn = active_reviewer.review_comments
            try:
                raw_verdicts = fn(
                    sanitized_payload,
                    lesson_summary=clean_summary,
                )
            except TypeError:
                raw_verdicts = fn(sanitized_payload)
            if inspect.iscoroutine(raw_verdicts):
                raw_verdicts = _run_coro_sync(raw_verdicts, timeout_s)

    if not isinstance(raw_verdicts, Mapping):
        return {}
    return {
        str(k): _normalize_ai_verdict(v)
        for k, v in raw_verdicts.items()
        if _normalize_ai_verdict(v)
    }


def _count_meaningful_words(text: str | None) -> tuple[int, int]:
    """Return (meaningful_word_count, normalized_char_length) after stripping [HS] tokens."""
    no_hs = re.sub(r"\[hs\]", " ", str(text or ""), flags=re.IGNORECASE).strip()
    folded = fold_text(no_hs)
    norm_text = " ".join(folded.split()).strip(" -–—:.,;!?()[]{}\"'")
    words = re.findall(r"[a-z0-9]+", norm_text)
    return len(words), len(norm_text)


def _has_concrete_learning_signal(body_text: str) -> bool:
    folded = fold_text(re.sub(r"\[hs\]", " ", str(body_text or ""), flags=re.IGNORECASE))
    return any(
        re.search(r"\b" + re.escape(sig) + r"\b", folded) for sig in _CONCRETE_LEARNING_SIGNALS
    )


def _resolve_active_lms_ai_reviewer(ai_reviewer: Any = None) -> Any:
    """Return the active LmsAiCommentReviewerGateway or None when disabled/isolated."""
    global _DEFAULT_AI_REVIEWER
    if ai_reviewer is not None:
        return ai_reviewer
    if os.getenv("ZALO_QUALITY_ISOLATED") == "1":
        return None
    env_flag = os.getenv("LMS_AI_COMMENT_CHECK", "").strip().lower()
    if env_flag in {"0", "false", "no", "off"}:
        return None
    with contextlib.suppress(Exception):
        from googleform_autosubmit.config import Config

        if not getattr(Config, "LMS_AI_COMMENT_CHECK", True):
            return None
    if not os.getenv("GEMINI_API_KEY", "").strip():
        return None
    if _DEFAULT_AI_REVIEWER is None:
        with contextlib.suppress(Exception):
            from services.external_gateways import GeminiLmsCommentReviewerGateway

            _DEFAULT_AI_REVIEWER = GeminiLmsCommentReviewerGateway()
    return _DEFAULT_AI_REVIEWER


def _is_valid_checkpoint_student_comment(body_text: str) -> bool:
    folded = fold_text(body_text)
    if not folded or len(folded) < 60:
        return False
    pillar_hits = sum(1 for p in ("kien thuc", "ky nang", "thai do") if p in folded)
    return pillar_hits == 3


def _regular_comment_review_reasons(body_text: str) -> list[str]:
    """Deterministic Tier 1 / fallback check for regular session comments."""
    if _is_placeholder_or_dot_bypass(body_text) or _is_absence_statement_in_comment(body_text):
        return []
    word_count, char_len = _count_meaningful_words(body_text)
    if word_count >= 25 and char_len >= 80:
        return []
    if word_count < 12 or char_len < 45 or not _has_concrete_learning_signal(body_text):
        return ["nhận xét sơ sài"]
    return []


def _is_valid_regular_student_comment(body_text: str) -> bool:
    return not _regular_comment_review_reasons(body_text)


def _attendance_student_key(entry: Mapping[str, Any]) -> str:
    key = str(entry.get("studentKey") or entry.get("studentId") or "").strip()
    student = entry.get("student")
    return key or (
        str(student.get("id") or student.get("studentKey") or "").strip()
        if isinstance(student, Mapping)
        else ""
    )


def _evaluate_lms_attendance(
    student_attendance: Any, slot_data: Mapping[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Any]]:
    """One attendance decision for comments and SPCK; unknown evidence never means absence."""
    data = slot_data or {}
    rows = student_attendance if isinstance(student_attendance, list) else []
    partial = any(
        data.get(flag)
        for flag in (
            "truncated",
            "studentAttendanceTruncated",
            "studentAttendanceFetchError",
            "attendanceAmbiguous",
        )
    )
    overflow = len(rows) > MAX_LMS_STUDENTS_PER_SLOT
    attended: list[dict[str, Any]] = []
    absent = 0
    unknown = 0
    seen_keys: set[str] = set()
    for row in rows[:MAX_LMS_STUDENTS_PER_SLOT]:
        if not isinstance(row, dict):
            unknown += 1
            continue
        key = _attendance_student_key(row)
        if key and key in seen_keys:
            unknown += 1
            continue
        if key:
            seen_keys.add(key)
        status = str(row.get("status") or "").strip().upper()
        if status in ATTENDED_STATUSES:
            attended.append(row)
        elif status in ABSENT_STATUSES:
            absent += 1
        else:
            unknown += 1
    unknown += max(0, len(rows) - MAX_LMS_STUDENTS_PER_SLOT)
    unverified = not rows or unknown > 0 or partial
    status = "UNVERIFIED" if unverified else ("EXEMPT" if not attended else "VERIFIED")
    metadata = {
        "status": status,
        "attended_count": len(attended),
        "absent_count": absent,
        "unverified_count": unknown,
        "exempt_count": absent,
        "total_count": len(rows),
    }
    issues = []
    if unverified:
        if overflow:
            text = f"[LMS] Danh sách học viên vượt giới hạn kiểm tra ({len(rows)} > {MAX_LMS_STUDENTS_PER_SLOT} HS), cần kiểm tra thủ công"
        elif not rows:
            text = "[LMS] Chưa kiểm tra được danh sách học viên trên LMS, cần kiểm tra thủ công"
        else:
            text = "[LMS] Điểm danh chưa đầy đủ hoặc chưa xác định, cần kiểm tra thủ công"
        issues.append(make_structured_issue(ISSUE_CODE_LMS_MANUAL_CHECK, text))
    return metadata, attended, issues


def _normalize_comment_for_dedup(body_text: str) -> str:
    folded = fold_text(body_text)
    folded = re.sub(r"[^a-z0-9\s]", " ", folded)
    return " ".join(folded.split())


def evaluate_lms_student_comments(
    student_attendance: list[dict[str, Any]] | None,
    *,
    is_checkpoint: bool,
    lesson: str | int | None = "",
    slot_data: Mapping[str, Any] | None = None,
    ai_reviewer: Any = None,
    lesson_summary: str = "",
    class_code: str = "",
    khoi: str = "",
    verdict_cache: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Check only verified attendees; quality uncertainty is a quantified QC note."""
    attendance, attended_entries, attendance_issues = _evaluate_lms_attendance(
        student_attendance, slot_data
    )
    exempt = is_lesson_14(lesson) or attendance["status"] == "EXEMPT"
    base = {
        "is_exempt": exempt,
        "is_checkpoint": is_checkpoint and not exempt,
        "attended_count": attendance["attended_count"],
        "missing_count": 0,
        "shallow_count": 0,
        "attendance_conflict_count": 0,
        "failed_count": 0,
        "duplicate_count": 0,
        "submitted_count": 0,
        "review_reasons": {},
        "attendance": attendance,
        "status": "EXEMPT" if exempt else "UNVERIFIED",
        "exempt_count": attendance["total_count"]
        if is_lesson_14(lesson)
        else attendance["exempt_count"],
        "issues": [],
    }
    if exempt:
        return base
    if attendance["status"] == "UNVERIFIED":
        return {**base, "issues": attendance_issues}

    attended_count = len(attended_entries)
    missing_count = 0
    shallow_count = 0
    attendance_conflict_count = 0
    reasons_count: dict[str, int] = {}
    normalized_bodies: list[str] = []
    gray_zone_items: list[tuple[str, str, int, int]] = []
    slot_has_rubric = is_checkpoint and _has_checkpoint_rubric_in_slot(
        {"studentAttendance": attended_entries}
    )
    for idx, entry in enumerate(attended_entries):
        student_obj = entry.get("student")
        full_name = str(student_obj.get("fullName") or "") if isinstance(student_obj, dict) else ""
        body = _strip_comment_header_and_student_name(
            entry.get("comment"), student_full_name=full_name
        )
        if _is_placeholder_or_dot_bypass(body):
            missing_count += 1
            continue
        if _is_absence_statement_in_comment(body):
            attendance_conflict_count += 1
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
            if not is_valid:
                shallow_count += 1
                reasons_count["thiếu cấu trúc Checkpoint"] = (
                    reasons_count.get("thiếu cấu trúc Checkpoint", 0) + 1
                )
        else:
            word_count, char_len = _count_meaningful_words(body)
            if word_count >= 25 and char_len >= 80:
                continue
            gray_zone_items.append((f"stu_c_{idx}", body, word_count, char_len))

    if gray_zone_items and not is_checkpoint:
        active_reviewer = _resolve_active_lms_ai_reviewer(ai_reviewer)
        known_names = [
            str(entry.get("student", {}).get("fullName") or "").strip()
            for entry in (student_attendance or [])
            if isinstance(entry, Mapping)
            and isinstance(entry.get("student"), Mapping)
            and str(entry.get("student", {}).get("fullName") or "").strip()
        ]
        resolved_summary = str(
            lesson_summary
            or (slot_data.get("summary") if isinstance(slot_data, Mapping) else "")
            or ""
        ).strip()
        cache_key_by_id: dict[str, str] = {}
        pending_ai_items: list[dict[str, str]] = []
        for item_id, body_text, _, _ in gray_zone_items:
            scrubbed_body = _scrub_comment_for_ai(body_text, known_student_names=known_names)
            c_key = hashlib.sha256(f"SESSION:{scrubbed_body}".encode("utf-8")).hexdigest()
            cache_key_by_id[item_id] = c_key
            if verdict_cache is not None and c_key in verdict_cache:
                continue
            pending_ai_items.append(
                {
                    "id": item_id,
                    "type": "SESSION_COMMENT",
                    "text": body_text,
                }
            )
        ai_verdicts: dict[str, str] = {}
        if pending_ai_items and active_reviewer is not None:
            ai_verdicts = _invoke_ai_comment_reviewer(
                active_reviewer,
                pending_ai_items,
                class_code=class_code,
                khoi=khoi,
                lesson=lesson,
                lesson_summary=resolved_summary,
                comment_kind="SESSION",
                known_student_names=known_names,
            )
            if verdict_cache is not None and not isinstance(
                active_reviewer, _BatchRecordingReviewer
            ):
                for p_item in pending_ai_items:
                    p_id = p_item["id"]
                    verdict_cache[cache_key_by_id[p_id]] = ai_verdicts.get(p_id, "")
        for item_id, body_text, word_count, char_len in gray_zone_items:
            c_key = cache_key_by_id[item_id]
            verdict = ai_verdicts.get(item_id) or (
                verdict_cache.get(c_key, "") if verdict_cache is not None else ""
            )
            if verdict == "VALID_CONCISE":
                continue
            if verdict == "ATTENDANCE_CONFLICT":
                attendance_conflict_count += 1
                continue
            if verdict in {"SHALLOW_GENERIC", "PLACEHOLDER_BYPASS"}:
                shallow_count += 1
                reasons_count["nhận xét sơ sài"] = reasons_count.get("nhận xét sơ sài", 0) + 1
                continue
            # Deterministic fallback when AI is disabled or did not return a verdict
            if word_count < 12 or char_len < 45 or not _has_concrete_learning_signal(body_text):
                shallow_count += 1
                reasons_count["nhận xét sơ sài"] = reasons_count.get("nhận xét sơ sài", 0) + 1

    issues: list[Any] = []
    if missing_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_QUALITY_FAIL,
                f"[LMS] Chưa nhận xét học viên ({missing_count}/{attended_count} HS)",
                severity=SEVERITY_FAIL,
            )
        )
    if attendance_conflict_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
                f"[LMS] Lỗi điểm danh: tích Đi học/Đến muộn nhưng nhận xét ghi học viên nghỉ ({attendance_conflict_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if is_checkpoint and shallow_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_COMMENT_NOTE,
                "[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực",
                severity=SEVERITY_NOTE,
            )
        )
    elif shallow_count > 0 and not is_checkpoint:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_COMMENT_NOTE,
                f"[LMS] Nhận xét sơ sài ({shallow_count}/{attended_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    counts_by_body: dict[str, int] = {}
    if not is_checkpoint:
        for body in normalized_bodies:
            counts_by_body[body] = counts_by_body.get(body, 0) + 1
    return {
        **base,
        "status": "MISSING"
        if missing_count
        else ("REVIEW" if (shallow_count or attendance_conflict_count) else "COMPLETE"),
        "missing_count": missing_count,
        "submitted_count": attended_count - missing_count,
        "shallow_count": shallow_count,
        "attendance_conflict_count": attendance_conflict_count,
        "failed_count": missing_count + shallow_count,
        "review_reasons": reasons_count,
        "duplicate_count": sum(count for count in counts_by_body.values() if count >= 2),
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

SPCK_EMPTY_TITLE_PATTERNS = (
    r"^test(?:\s*\d+)?$",
    r"^demo(?:\s*\d+)?$",
    r"^untitled$",
    r"^n/?a$",
    r"^none$",
    r"^null$",
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


def _is_spck_title_empty_or_placeholder(work: Mapping[str, Any]) -> bool:
    """Return True when StudentWork.title is empty, dot/punctuation-only, or a trivial placeholder."""
    raw_title = _strip_html(str(work.get("title") or "")).strip()
    if not raw_title:
        return True
    no_hs = re.sub(r"\[hs\]", " ", raw_title, flags=re.IGNORECASE).strip()
    folded_full = fold_text(raw_title).strip(" -–—:.,;!?()[]{}\"'/_*")
    if not folded_full or len(re.findall(r"[a-z0-9]", folded_full)) < 2:
        return True
    folded_no_hs = fold_text(no_hs).strip(" -–—:.,;!?()[]{}\"'/_*")
    if folded_no_hs and any(re.fullmatch(pat, folded_no_hs) for pat in SPCK_EMPTY_TITLE_PATTERNS):
        return True
    if any(re.fullmatch(pat, folded_full) for pat in SPCK_EMPTY_TITLE_PATTERNS):
        return True
    return False


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
    parts = [p.strip() for p in re.split(r"\s*(?:[-–—|:]|_)\s*", raw_title) if p.strip()]
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


def _has_spck_thumbnail(work: Mapping[str, Any]) -> bool:
    thumb = str(work.get("thumbnail") or "").strip()
    return len(thumb) >= 5


def _has_spck_capture_images(work: Mapping[str, Any]) -> bool:
    raw_images = work.get("imageUrl")
    if not isinstance(raw_images, Sequence) or isinstance(raw_images, (str, bytes)):
        return False
    valid_images = [
        str(u).strip() for u in raw_images if isinstance(u, str) and len(u.strip()) >= 5
    ]
    return len(valid_images) >= 1


def _has_spck_thumbnail_and_images(work: Mapping[str, Any]) -> bool:
    """Return True when StudentWork has both a thumbnail URL and at least 1 screenshot in imageUrl."""
    return _has_spck_thumbnail(work) and _has_spck_capture_images(work)


def _has_spck_attachment_file(work: Mapping[str, Any]) -> bool:
    """Return True when StudentWork has at least 1 attached file URL in attachmentUrls."""
    raw_attachments = work.get("attachmentUrls")
    if not isinstance(raw_attachments, Sequence) or isinstance(raw_attachments, (str, bytes)):
        return False
    valid_files = [
        str(u).strip() for u in raw_attachments if isinstance(u, str) and len(u.strip()) >= 5
    ]
    return len(valid_files) >= 1


def _is_substantive_spck_comment(
    raw_comment: str | None,
    *,
    student_full_name: str = "",
) -> bool:
    """Deterministic Tier 1 / fallback check for SPCK product comments."""
    body = _strip_comment_header_and_student_name(
        raw_comment,
        student_full_name=student_full_name,
    )
    if _is_placeholder_or_dot_bypass(body):
        return False
    body_no_hs = re.sub(r"\[hs\]", " ", body, flags=re.IGNORECASE).strip()
    folded = fold_text(body_no_hs).strip(" -–—:.,;!?()[]{}\"'")
    if not folded:
        return False
    for pattern in SPCK_COMMENT_PLACEHOLDER_PATTERNS:
        if re.fullmatch(pattern, folded):
            return False
    word_count, _ = _count_meaningful_words(body_no_hs)
    if word_count >= 15:
        return True
    if word_count < 8:
        return False
    domain_hits = sum(1 for kw in SPCK_COMMENT_DOMAIN_SIGNALS if kw in folded)
    return domain_hits >= 1


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
    if isinstance(raw_attachments, Sequence) and not isinstance(raw_attachments, (str, bytes)):
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
    ai_reviewer: Any = None,
    verdict_cache: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate final project (SPCK) submissions on LMS for Lessons 12, 13, and 14 (DEC-042 & Phase 6.5)."""
    lesson_num = _parse_lesson_number(lesson)
    is_robotics = _is_robotics_class(class_code, khoi)
    spck_required = lesson_num in SPCK_REQUIRED_LESSONS and not _is_spck_exempt_course(class_code)
    empty_res: dict[str, Any] = {
        "is_spck_lesson": spck_required,
        "is_robotics": is_robotics,
        "status": "UNVERIFIED" if spck_required else "NOT_REQUIRED",
        "attended_count": 0,
        "exempt_count": 0,
        "submitted_count": 0,
        "missing_submission_count": 0,
        "no_title_count": 0,
        "missing_student_name_in_title_count": 0,
        "missing_student_info_count": 0,
        "missing_both_image_count": 0,
        "missing_capture_image_count": 0,
        "missing_thumb_image_count": 0,
        "missing_image_count": 0,
        "missing_attachment_count": 0,
        "missing_comment_count": 0,
        "shallow_comment_count": 0,
        "missing_youtube_count": 0,
        "missing_slide_count": 0,
        "issues": [],
    }
    if not spck_required or not isinstance(slot_data, Mapping):
        return empty_res

    student_attendance = slot_data.get("studentAttendance")
    attendance, attended_entries, attendance_issues = _evaluate_lms_attendance(
        student_attendance, slot_data
    )
    attended_count = attendance["attended_count"]
    empty_res.update(
        {
            "attended_count": attended_count,
            "exempt_count": attendance["exempt_count"],
            "attendance": attendance,
        }
    )
    if attendance["status"] == "EXEMPT":
        return {**empty_res, "status": "EXEMPT"}
    if attendance["status"] == "UNVERIFIED":
        return {**empty_res, "issues": attendance_issues}
    raw_works = slot_data.get("studentWorks")
    if (
        bool(slot_data.get("studentWorksFetchError"))
        or not isinstance(raw_works, Sequence)
        or isinstance(raw_works, (str, bytes))
    ):
        return {
            **empty_res,
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
            return {
                **empty_res,
                "issues": [
                    make_structured_issue(
                        ISSUE_CODE_LMS_MANUAL_CHECK,
                        "[LMS] Dữ liệu SPCK không đầy đủ, cần kiểm tra thủ công",
                    )
                ],
            }

        is_del = w.get("isDeleted")
        if is_del is True or str(is_del or "").strip().lower() == "true":
            continue
        active_works.append(dict(w))

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
        s_key = _attendance_student_key(entry)
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
            elif w_key in non_attended_keys or bool(w.get("inactiveInClass")):
                continue
            else:
                unknown_owner_works.append(w)
        else:
            unknown_owner_works.append(w)

    if unknown_owner_works or unkeyed_attended_count > 0:
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
            "status": "MISSING",
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

    no_title_count = 0
    missing_student_name_in_title = 0
    missing_both_img = 0
    missing_capture_img = 0
    missing_thumb_img = 0
    missing_attachment_count = 0
    missing_comment_count = 0
    shallow_comment_count = 0
    missing_youtube_count = 0
    missing_slide_count = 0
    spck_gray_zone_items: list[tuple[str, str, str, int]] = []

    for idx, (work_item, stu_name) in enumerate(inspected_works):
        if _is_spck_title_empty_or_placeholder(work_item):
            no_title_count += 1
        elif not _has_spck_student_info_in_title(work_item, student_full_name=stu_name):
            missing_student_name_in_title += 1

        has_thumb = _has_spck_thumbnail(work_item)
        has_capture = _has_spck_capture_images(work_item)
        if not has_thumb and not has_capture:
            missing_both_img += 1
        elif has_thumb and not has_capture:
            missing_capture_img += 1
        elif has_capture and not has_thumb:
            missing_thumb_img += 1

        if not _has_spck_attachment_file(work_item):
            missing_attachment_count += 1

        raw_c = work_item.get("comment")
        raw_c_str = raw_c if isinstance(raw_c, str) else ""
        clean_c = _strip_comment_header_and_student_name(
            raw_c_str,
            student_full_name=stu_name,
        )
        if _is_placeholder_or_dot_bypass(clean_c):
            missing_comment_count += 1
        else:
            word_count, _ = _count_meaningful_words(clean_c)
            if word_count < 15:
                spck_gray_zone_items.append((f"spck_c_{idx}", raw_c_str, stu_name, word_count))

        if lesson_num in SPCK_SLIDE_YOUTUBE_LESSONS:
            if not _has_spck_youtube_link(work_item):
                missing_youtube_count += 1
            if not is_robotics and not _has_spck_slide_link_or_file(work_item):
                missing_slide_count += 1

    if spck_gray_zone_items:
        active_reviewer = _resolve_active_lms_ai_reviewer(ai_reviewer)
        spck_known_names = [
            name
            for name in (
                *attended_by_key.values(),
                *(stu_name for _, stu_name in inspected_works),
            )
            if isinstance(name, str) and name.strip()
        ]
        spck_cache_key_by_id: dict[str, str] = {}
        pending_spck_items: list[dict[str, str]] = []
        for item_id, raw_c_str, _, _ in spck_gray_zone_items:
            scrubbed_spck = _scrub_comment_for_ai(raw_c_str, known_student_names=spck_known_names)
            c_key = hashlib.sha256(f"SPCK:{scrubbed_spck}".encode("utf-8")).hexdigest()
            spck_cache_key_by_id[item_id] = c_key
            if verdict_cache is not None and c_key in verdict_cache:
                continue
            pending_spck_items.append(
                {
                    "id": item_id,
                    "type": "SPCK_COMMENT",
                    "text": raw_c_str,
                }
            )
        ai_verdicts: dict[str, str] = {}
        if pending_spck_items and active_reviewer is not None:
            ai_verdicts = _invoke_ai_comment_reviewer(
                active_reviewer,
                pending_spck_items,
                class_code=class_code,
                khoi=khoi,
                lesson=lesson,
                lesson_summary=str(slot_data.get("summary") or ""),
                comment_kind="SPCK",
                known_student_names=spck_known_names,
            )
            if verdict_cache is not None and not isinstance(
                active_reviewer, _BatchRecordingReviewer
            ):
                for p_item in pending_spck_items:
                    p_id = p_item["id"]
                    verdict_cache[spck_cache_key_by_id[p_id]] = ai_verdicts.get(p_id, "")
        for item_id, raw_c_str, stu_name, _word_count in spck_gray_zone_items:
            c_key = spck_cache_key_by_id[item_id]
            verdict = ai_verdicts.get(item_id) or (
                verdict_cache.get(c_key, "") if verdict_cache is not None else ""
            )
            if verdict == "VALID_CONCISE":
                continue
            if verdict in {"SHALLOW_GENERIC", "PLACEHOLDER_BYPASS", "ATTENDANCE_CONFLICT"}:
                shallow_comment_count += 1
                continue
            if not _is_substantive_spck_comment(raw_c_str, student_full_name=stu_name):
                shallow_comment_count += 1

    missing_student_info_count = no_title_count + missing_student_name_in_title
    missing_image_count = missing_both_img + missing_capture_img + missing_thumb_img

    if no_title_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] SPCK chưa đặt tên sản phẩm ({no_title_count}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if missing_student_name_in_title > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] Tiêu đề SPCK chưa ghi kèm tên học viên ({missing_student_name_in_title}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if missing_both_img > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] SPCK chưa có ảnh sản phẩm ({missing_both_img}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if missing_capture_img > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] SPCK thiếu ảnh chụp bổ sung ({missing_capture_img}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if missing_thumb_img > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                f"[LMS] SPCK thiếu ảnh đại diện ({missing_thumb_img}/{submitted_count} HS)",
                severity=SEVERITY_NOTE,
            )
        )
    if missing_attachment_count > 0:
        attachment_msg = (
            f"[LMS] SPCK chưa có file code đính kèm ({missing_attachment_count}/{submitted_count} HS)"
            if is_robotics
            else f"[LMS] SPCK thiếu file đính kèm ({missing_attachment_count}/{submitted_count} HS)"
        )
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_NOTE,
                attachment_msg,
                severity=SEVERITY_NOTE,
            )
        )
    if missing_comment_count > 0:
        issues.append(
            make_structured_issue(
                ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
                f"[LMS] SPCK chưa có nhận xét ({missing_comment_count}/{submitted_count} HS)",
                severity=SEVERITY_FAIL,
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
                    f"[LMS] SPCK Buổi 14 thiếu link video YouTube ({missing_youtube_count}/{submitted_count} HS)",
                    severity=SEVERITY_NOTE,
                )
            )
        if not is_robotics and missing_slide_count > 0:
            issues.append(
                make_structured_issue(
                    ISSUE_CODE_LMS_SPCK_NOTE,
                    f"[LMS] SPCK Buổi 14 thiếu link/file Slide thuyết trình ({missing_slide_count}/{submitted_count} HS)",
                    severity=SEVERITY_NOTE,
                )
            )

    return {
        **empty_res,
        "status": "PARTIAL" if missing_submission_count else "COMPLETE",
        "is_spck_lesson": True,
        "is_robotics": is_robotics,
        "attended_count": attended_count,
        "submitted_count": submitted_count,
        "missing_submission_count": missing_submission_count,
        "no_title_count": no_title_count,
        "missing_student_name_in_title_count": missing_student_name_in_title,
        "missing_student_info_count": missing_student_info_count,
        "missing_both_image_count": missing_both_img,
        "missing_capture_image_count": missing_capture_img,
        "missing_thumb_image_count": missing_thumb_img,
        "missing_image_count": missing_image_count,
        "missing_attachment_count": missing_attachment_count,
        "missing_comment_count": missing_comment_count,
        "shallow_comment_count": shallow_comment_count,
        "missing_youtube_count": missing_youtube_count,
        "missing_slide_count": missing_slide_count,
        "issues": issues,
    }


def _lms_quality_metadata(
    attendance: Mapping[str, Any], comments: Mapping[str, Any], spck: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "lms_attendance": dict(attendance),
        "lms_comments": {
            "status": comments["status"],
            "submitted_count": comments.get("submitted_count", 0),
            "missing_count": comments.get("missing_count", 0),
            "review_count": comments.get("shallow_count", 0),
            "exempt_count": comments.get("exempt_count", 0),
        },
        "lms_spck": {
            "status": spck["status"],
            "submitted_count": spck.get("submitted_count", 0),
            "missing_count": spck.get("missing_submission_count", 0),
            "missing_comment_count": spck.get("missing_comment_count", 0),
            "exempt_count": spck.get("exempt_count", 0),
            "attended_count": attendance.get("attended_count", 0),
        },
    }


def evaluate_lms_slot_quality(
    slot_data: dict[str, Any] | None,
    *,
    class_code: str,
    khoi: str = "",
    lesson: str | int | None = "",
    ai_reviewer: Any = None,
    verdict_cache: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate one session with the same verified attendance for comments and SPCK."""
    lesson_14_flag = is_lesson_14(lesson)
    data = slot_data if isinstance(slot_data, dict) else {}
    checkpoint = (
        False if lesson_14_flag else is_checkpoint_session(class_code, khoi, lesson, slot_data=data)
    )
    unavailable_issue = ""
    if not isinstance(slot_data, dict):
        unavailable_issue = (
            "[LMS] Chưa kiểm tra được dữ liệu buổi học trên LMS, cần kiểm tra thủ công"
        )
    for flag, text in (
        ("overflow_class_limit", "Vượt giới hạn số lớp kiểm tra LMS trong một đợt"),
        ("duplicate_class", "Phát hiện trùng mã lớp trên LMS"),
        ("missing_class", "Không tìm thấy dữ liệu lớp trên LMS"),
        ("fetch_error", "Không tải được đầy đủ dữ liệu buổi học từ LMS"),
    ):
        if data.get(flag):
            unavailable_issue = f"[LMS] {text}, cần kiểm tra thủ công"
            break
    if unavailable_issue:
        attendance, _, _ = _evaluate_lms_attendance(None)
        return {
            **_lms_quality_metadata(
                attendance,
                {"status": "EXEMPT" if lesson_14_flag else "UNVERIFIED"},
                {
                    "status": "UNVERIFIED"
                    if _parse_lesson_number(lesson) in SPCK_REQUIRED_LESSONS
                    and not _is_spck_exempt_course(class_code)
                    else "NOT_REQUIRED"
                },
            ),
            "is_exempt": lesson_14_flag,
            "is_checkpoint": checkpoint,
            "has_valid_summary": lesson_14_flag,
            "summary_clean": "",
            "attended_count": 0,
            "failed_comment_count": 0,
            "issues": [make_structured_issue(ISSUE_CODE_LMS_MANUAL_CHECK, unavailable_issue)],
        }
    summary_eval = (
        {"has_valid_summary": True, "summary_clean": "", "issue": ""}
        if lesson_14_flag
        else evaluate_lms_slot_summary(data.get("summary"))
    )
    comments_eval = evaluate_lms_student_comments(
        data.get("studentAttendance"),
        is_checkpoint=checkpoint,
        lesson=lesson,
        slot_data=data,
        ai_reviewer=ai_reviewer,
        lesson_summary=str(summary_eval.get("summary_clean") or ""),
        class_code=class_code,
        khoi=khoi,
        verdict_cache=verdict_cache,
    )
    spck_eval = evaluate_lms_spck_quality(
        data,
        class_code=class_code,
        khoi=khoi,
        lesson=lesson,
        ai_reviewer=ai_reviewer,
        verdict_cache=verdict_cache,
    )
    issues = [summary_eval["issue"]] if summary_eval["issue"] else []
    for item in [*comments_eval["issues"], *spck_eval["issues"]]:
        if issue_text(item) not in {issue_text(existing) for existing in issues}:
            issues.append(item)
    return {
        **_lms_quality_metadata(comments_eval["attendance"], comments_eval, spck_eval),
        "is_exempt": lesson_14_flag,
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
            issues.append(StructuredIssueStr(cleaned, code=code, severity=severity))

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
        raw_tm if isinstance(raw_tm, dict) and raw_tm.get("matches") is True else {}
    )

    target_match_decision = (
        str(ctx.get("target_match_decision") or tm.get("decision") or "").strip().upper()
    )
    target_match_note = str(ctx.get("target_match_note") or tm.get("note") or "").strip()
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
        or (content_lesson is not None and f"bai so {content_lesson}" in folded_match_note)
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
    clean_lms_summary = str(lms_summary_text or item.get("lms_lesson_summary") or "").strip()

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
        except TypeError, ValueError:
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
            except TypeError, ValueError:
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
    target_lessons_by_class: dict[str, list[int]] | None = None,
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
                                    acc = sts.get("accessToken") if isinstance(sts, dict) else None
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
                    req_vars = body_json.get("variables") if isinstance(body_json, dict) else None
                    if isinstance(req_vars, dict) and isinstance(req_vars.get("centres"), list):
                        for cid in req_vars["centres"]:
                            s_cid = str(cid or "").strip()
                            if s_cid and s_cid not in resolved_centres:
                                resolved_centres.append(s_cid)

    js_script = """
    async ({ endpoint, classCodes, centres, maxSlots, maxStudents, timeoutMs, preResolvedToken, allowedHostname, allowedTokenKeys, targetLessons, spckExemptClasses }) => {
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
                        students {
                            activeInClass
                            student {
                                id
                                fullName
                            }
                        }
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
            query findAllStudentWorks($sessionId: String, $studentId: String) {
                findAllStudentWorks(
                    payload: {
                        classSessionId_equals: $sessionId
                        studentId_equals: $studentId
                    }
                ) {
                    data {
                        id
                        status
                        classId
                        classSessionId
                        classSessionNumber
                        studentId
                        isDeleted
                        latestData {
                            title
                            thumbnail
                            imageUrl
                            attachmentUrls
                            videoUrls
                            relatedUrls {
                                name
                                url
                            }
                            comment
                        }
                        publishedData {
                            title
                            thumbnail
                            imageUrl
                            attachmentUrls
                            videoUrls
                            relatedUrls {
                                name
                                url
                            }
                            comment
                        }
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
        const buildFoldedWithMap = (rawStr) => {
            const src = String(rawStr || '').normalize('NFC');
            let folded = '';
            const spans = [];
            for (let i = 0; i < src.length; ) {
                const cStart = i;
                const cp = src.codePointAt(i);
                i += (cp !== undefined && cp > 0xFFFF) ? 2 : 1;
                while (i < src.length && /[\\u0300-\\u036f]/.test(src[i])) i += 1;
                const chunk = foldViChars(src.slice(cStart, i));
                for (let k = 0; k < chunk.length; k++) {
                    folded += chunk[k];
                    spans.push([cStart, i]);
                }
            }
            return { src, folded, spans };
        };
        const URL_RESERVED_TOKENS = new Set([
            'https', 'http', 'youtube', 'youtu', 'canva', 'google', 'presentation',
            'slides', 'slide', 'drive', 'github', 'vercel', 'netlify', 'mindx',
            'static', 'resources', 'uploads', 'images', 'documents', 'watch',
            'design', 'share', 'media', 'index', 'project', 'admin', 'class'
        ]);
        const consumeUrlSepUnit = (f, p) => {
            const ch = f[p];
            if (/[\\s\\-_+.]/.test(ch)) return p + 1;
            if (ch === '%') {
                const mSep = f.slice(p, p + 15).match(/^%(?:25)*(?:20|2b|2d|2e|5f)/);
                if (mSep) return p + mSep[0].length;
            }
            return -1;
        };
        const replaceTokenGroup = (partStr, grp, replacement, urlMode) => {
            if (!partStr || !Array.isArray(grp) || grp.length === 0) return String(partStr || '');
            const grpChars = grp.join('').length;
            if (urlMode) {
                if (grp.length === 1 ? (grpChars < 5 || URL_RESERVED_TOKENS.has(grp[0])) : grpChars < 4) return partStr;
            } else if (grp.length === 1 && grpChars < 3) {
                return String(partStr);
            }
            const allowConcat = urlMode && (grp.length >= 3 || (grp.length >= 2 && grpChars >= 7));
            const strongMulti = grp.length >= 3 && grpChars >= 8;
            const { src, folded, spans } = buildFoldedWithMap(partStr);
            if (!src || !folded) return src;
            const firstTok = grp[0];
            let outStr = '';
            let fCur = 0;
            let sCur = 0;
            while (fCur < folded.length) {
                const hit = folded.indexOf(firstTok, fCur);
                if (hit === -1) break;
                const leftOk = hit === 0 || !/[a-z]/.test(folded[hit - 1]) || strongMulti
                    || /%(?:25)*[0-9a-f]{2}$/.test(folded.slice(Math.max(0, hit - 25), hit));
                if (!leftOk) { fCur = hit + 1; continue; }
                let pos = hit + firstTok.length;
                let ok = true;
                for (let t = 1; t < grp.length; t++) {
                    const nextTok = grp[t];
                    if (!urlMode) {
                        let ws = pos;
                        while (ws < folded.length) {
                            if (/[\\s\\-_+]/.test(folded[ws])) { ws += 1; continue; }
                            if (folded[ws] === '%') {
                                const mSep = folded.slice(ws, ws + 25).match(/^%(?:25)*(?:20|2b|2d|2e|5f)/);
                                if (mSep) { ws += mSep[0].length; continue; }
                            }
                            break;
                        }
                        if (ws === pos || !folded.startsWith(nextTok, ws)) { ok = false; break; }
                        pos = ws + nextTok.length;
                    } else {
                        let sepEnd = pos;
                        let sc = 0;
                        while (sepEnd < folded.length && sc < 16) {
                            const nx = consumeUrlSepUnit(folded, sepEnd);
                            if (nx === -1) break;
                            sepEnd = nx;
                            sc += 1;
                        }
                        if (sc > 0 && folded.startsWith(nextTok, sepEnd)) pos = sepEnd + nextTok.length;
                        else if (allowConcat && sc === 0 && folded.startsWith(nextTok, pos)) pos += nextTok.length;
                        else { ok = false; break; }
                    }
                }
                const rightOk = ok && (pos === folded.length || !/[a-z]/.test(folded[pos]) || strongMulti);
                if (rightOk && spans[hit][0] >= sCur) {
                    outStr += src.slice(sCur, spans[hit][0]) + replacement;
                    sCur = spans[pos - 1][1];
                    fCur = pos;
                    while (fCur < spans.length && spans[fCur][0] < sCur) fCur += 1;
                } else {
                    fCur = hit + 1;
                }
            }
            return outStr + src.slice(sCur);
        };
        const decodePunycode = (enc) => {
            const str = String(enc || '').toLowerCase();
            if (!str || str.length > 255 || !/^[a-z0-9-]+$/.test(str)) return null;
            const out = [];
            const delim = str.lastIndexOf('-');
            let idx = 0;
            if (delim > 0) {
                for (let j = 0; j < delim; j++) out.push(str.charCodeAt(j));
                idx = delim + 1;
            } else if (delim === 0) {
                idx = 1;
            }
            let n = 128;
            let i = 0;
            let bias = 72;
            while (idx < str.length) {
                const oldi = i;
                let w = 1;
                for (let k = 36; ; k += 36) {
                    if (idx >= str.length) return null;
                    const c = str.charCodeAt(idx++);
                    const digit = (c >= 48 && c <= 57) ? c - 22 : ((c >= 97 && c <= 122) ? c - 97 : 36);
                    if (digit >= 36 || digit > Math.floor((0x7fffffff - i) / w)) return null;
                    i += digit * w;
                    const t = k <= bias ? 1 : (k >= bias + 26 ? 26 : k - bias);
                    if (digit < t) break;
                    if (w > Math.floor(0x7fffffff / (36 - t))) return null;
                    w *= (36 - t);
                }
                const outLen = out.length + 1;
                let delta = oldi === 0 ? Math.floor((i - oldi) / 700) : Math.floor((i - oldi) / 2);
                delta += Math.floor(delta / outLen);
                let kBias = 0;
                while (delta > 455) { delta = Math.floor(delta / 35); kBias += 36; }
                bias = Math.floor(kBias + (36 * delta) / (delta + 38));
                if (Math.floor(i / outLen) > 0x7fffffff - n) return null;
                n += Math.floor(i / outLen);
                i %= outLen;
                if (n > 0x10ffff || (n >= 0xd800 && n <= 0xdfff)) return null;
                out.splice(i, 0, n);
                i += 1;
            }
            try { return String.fromCodePoint(...out); } catch (_) { return null; }
        };
        const SAFE_MULTI_PCT_RE = /%(?:25){1,8}(20|2[BbDdEe]|3[0-9]|4[1-9A-Fa-f]|5[0-9AaFf]|6[1-9A-Fa-f]|7[0-9AaEe]|[89A-Fa-f][0-9A-Fa-f])/g;
        const UNSAFE_LEFTOVER_PCT_RE = /%(?:25)+(?:20|2[BbDdEe]|3[0-9]|4[1-9A-Fa-f]|5[0-9AaFf]|6[1-9A-Fa-f]|7[0-9AaEe]|[89A-Fa-f][0-9A-Fa-f])|%[89A-Fa-f][0-9A-Fa-f]/;
        const decodeUrlSafeChars = (rawPart) => {
            let s = String(rawPart || '');
            if (!s.includes('%')) return { text: s, unsafe: false };
            s = s.replace(SAFE_MULTI_PCT_RE, '%$1');
            s = s.replace(
                /%(3[0-9]|4[1-9A-Fa-f]|5[0-9AaFf]|6[1-9A-Fa-f]|7[0-9AaEe]|2[DdEe])/g,
                (_m, hex) => String.fromCharCode(parseInt(hex, 16))
            );
            s = s.replace(
                /(?:%[C-Dc-d][0-9A-Fa-f]%[89ABab][0-9A-Fa-f]|%[Ee][0-9A-Fa-f](?:%[89ABab][0-9A-Fa-f]){2}|%[Ff][0-7](?:%[89ABab][0-9A-Fa-f]){3})/g,
                (cpSeq) => { try { return decodeURIComponent(cpSeq); } catch (_) { return cpSeq; } }
            );
            if (UNSAFE_LEFTOVER_PCT_RE.test(s)) {
                return { text: s, unsafe: true };
            }
            return { text: s, unsafe: false };
        };
        const encodeNonAsciiUrlChars = (partStr) => String(partStr || '').replace(
            /[^\\x00-\\x7F]+/gu,
            (seq) => { try { return encodeURIComponent(seq); } catch (_) { return ''; } }
        );
        const buildNameGroups = (namesList) => {
            const allGroups = [];
            for (const fullName of namesList) {
                if (!fullName || fullName.length < 3) continue;
                const ft = foldVi(fullName).split(' ').filter(Boolean);
                if (ft.length === 0) continue;
                allGroups.push(ft);
                if (ft.length >= 3) allGroups.push(ft.slice(-2));
            }
            return allGroups;
        };
        const buildGivenTokens = (namesList) => {
            const s = new Set();
            for (const fullName of namesList) {
                if (!fullName || fullName.length < 3) continue;
                const ft = foldVi(fullName).split(' ').filter(Boolean);
                if (ft.length >= 2) {
                    const lastTok = ft[ft.length - 1];
                    if (lastTok && lastTok.length >= 2) s.add(lastTok);
                }
            }
            return s;
        };
        const PROTECTED_GIVEN_BIGRAMS = new Set([
            'thai do', 'thai duong', 'hoan thanh', 'hoan thien', 'du an', 'san pham',
            'bai tap', 'bai hoc', 'buoi hoc', 'vong lap', 'lap trinh', 'lap rap',
            'kien thuc', 'ky nang', 'cuoi khoa', 'thuyet trinh', 'tu duy', 'chu dong',
            'tich cuc', 'nam vung', 'nam bai', 'nam duoc', 'hieu bai', 'phat bieu',
            'phat trien', 'thuc hanh', 'dieu kien', 'thuat toan', 'danh sach',
            'doi tuong', 'nhan vat', 'ung dung', 'thiet ke', 'giao dien', 'chuc nang',
            'tinh nang', 'thao tac', 'diem manh', 'cai thien', 'bo sung', 'sang tao',
            'chi tiet'
        ]);
        const SINGLE_TECH_STOPWORDS = new Set([
            'python', 'scratch', 'robot', 'robotics', 'code', 'coding',
            'slide', 'video', 'game', 'web', 'app', 'html', 'css', 'hs'
        ]);
        const scrubHonorificGivenNames = (str, givenTokensSet) => {
            if (!str || !givenTokensSet || givenTokensSet.size === 0) return str;
            let out = str.replace(
                /(\\b(?:bạn|ban|bé|be|em|con|hv|học\\s+viên|hoc\\s+vien)\\s+)([\\p{L}\\p{M}]+)(?![\\p{L}\\p{M}])/giu,
                (full, honPrefix, word) => givenTokensSet.has(foldVi(word)) ? honPrefix + '[HS]' : full
            );
            const spans = [];
            const wordRe = /[\\p{L}\\p{M}]+/gu;
            let m;
            while ((m = wordRe.exec(out)) !== null) {
                spans.push({
                    start: m.index,
                    end: m.index + m[0].length,
                    raw: m[0],
                    folded: foldVi(m[0]),
                });
            }
            if (spans.length === 0) return out;
            let rebuilt = '';
            let cursor = 0;
            for (let i = 0; i < spans.length; i++) {
                const sp = spans[i];
                if (!givenTokensSet.has(sp.folded) || SINGLE_TECH_STOPWORDS.has(sp.folded)) continue;
                if (sp.start > 0 && sp.end < out.length && out[sp.start - 1] === '[' && out[sp.end] === ']') continue;
                const prevSpaceOnly = i > 0 && /^\\s+$/.test(out.slice(spans[i - 1].end, sp.start));
                const nextSpaceOnly = i + 1 < spans.length && /^\\s+$/.test(out.slice(sp.end, spans[i + 1].start));
                if (
                    (prevSpaceOnly && PROTECTED_GIVEN_BIGRAMS.has(spans[i - 1].folded + ' ' + sp.folded)) ||
                    (nextSpaceOnly && PROTECTED_GIVEN_BIGRAMS.has(sp.folded + ' ' + spans[i + 1].folded))
                ) {
                    continue;
                }
                rebuilt += out.slice(cursor, sp.start) + '[HS]';
                cursor = sp.end;
            }
            if (cursor === 0) return out;
            return rebuilt + out.slice(cursor);
        };
        const scrubHostAuthority = (rawAuth, nameGroups) => {
            const atIdx = rawAuth.lastIndexOf('@');
            const hostPort = atIdx >= 0 ? rawAuth.slice(atIdx + 1) : rawAuth;
            if (!hostPort || hostPort.startsWith('[')) return hostPort;
            const pm = hostPort.match(/^([^:]+)(:\\d*)$/);
            const rawHost = pm ? pm[1] : hostPort;
            const port = pm ? pm[2] : '';
            const rawLabels = rawHost.split('.');
            const uniLabels = [];
            const asciiLabels = [];
            let hadInvalidPuny = false;
            for (const rl of rawLabels) {
                const dlRes = decodeUrlSafeChars(rl);
                if (dlRes.unsafe) {
                    uniLabels.push('hs');
                    asciiLabels.push('hs');
                    hadInvalidPuny = true;
                    continue;
                }
                const dl = dlRes.text;
                if (/^xn--/i.test(dl)) {
                    const puny = decodePunycode(dl.slice(4));
                    if (puny === null) {
                        uniLabels.push('hs');
                        asciiLabels.push('hs');
                        hadInvalidPuny = true;
                    } else {
                        uniLabels.push(puny);
                        asciiLabels.push(dl.toLowerCase());
                    }
                } else {
                    uniLabels.push(dl);
                    asciiLabels.push(/^[\\x00-\\x7F]*$/.test(dl) ? dl : null);
                }
            }
            const uniHost = uniLabels.join('.');
            let scrubbed = uniHost;
            for (const grp of nameGroups) {
                if (grp.length >= 2 || (grp.length === 1 && grp[0].length >= 5)) {
                    scrubbed = replaceTokenGroup(scrubbed, grp, 'hs', true);
                }
            }
            if (!hadInvalidPuny && scrubbed === uniHost && asciiLabels.every((x) => x !== null)) {
                return asciiLabels.join('.') + port;
            }
            const safeHost = foldViChars(scrubbed).replace(/[^a-z0-9.-]/g, '-').replace(/-{2,}/g, '-');
            return (safeHost || 'hs') + port;
        };
        const NON_HOST_PCT_RE = /%(?:25)*(?:2f|5c|20|2b|3a|3f|23)/i;
        const FILE_EXT_TLD_RE = /^(?:png|jpe?g|gif|webp|svg|bmp|ico|pdf|pptx?|docx?|xlsx?|zip|rar|7z|tar|gz|mp[34]|mov|avi|mkv|webm|wav|sb3|py|js|ts|jsx|tsx|html?|css|json|xml|txt|csv|md)$/i;
        const isPlausibleBareHost = (hostPart, hasPathAfter) => {
            if (!hostPart || NON_HOST_PCT_RE.test(hostPart)) return false;
            const noPort = hostPart.replace(/:\\d+$/, '');
            const labels = noPort.split('.');
            if (labels.length < 2 || labels.some((lb) => !lb)) return false;
            const tld = labels[labels.length - 1];
            if (/^xn--/i.test(tld)) return true;
            if (!/^[a-z]{2,24}$/i.test(tld)) return false;
            if (hasPathAfter) return true;
            if (FILE_EXT_TLD_RE.test(tld)) return false;
            return labels.length >= 3 || labels.some((lb) => /^xn--/i.test(lb));
        };
        const scrubStudentUrl = (rawUrl, namesList) => {
            const raw = String(rawUrl || '').trim().slice(0, 4096);
            if (!raw) return '';
            const nameGroups = buildNameGroups(namesList);
            const m = raw.match(/^((?:https?:)?\\/\\/)((?:(?!%(?:25)*2f)[^/?#])+)([\\s\\S]*)$/i);
            const bareRaw = !m
                ? raw.match(/^((?:[\\p{L}\\p{M}\\p{N}_%-]+\\.)+(?:xn--[^\\s/?#&='"<>.:]*|[a-z]{2,24})(?::\\d+)?)([/?#][\\s\\S]*|$)/iu)
                : null;
            const bareM = (bareRaw && isPlausibleBareHost(bareRaw[1], Boolean(bareRaw[2]))) ? bareRaw : null;
            const scheme = m ? m[1] : '';
            const cleanHost = m
                ? scrubHostAuthority(m[2], nameGroups)
                : (bareM ? scrubHostAuthority(bareM[1], nameGroups) : '');
            const restRes = decodeUrlSafeChars(m ? m[3] : (bareM ? bareM[2] : raw));
            if (restRes.unsafe) {
                return (scheme + encodeNonAsciiUrlChars(cleanHost) + (cleanHost ? '/HS' : 'HS')).slice(0, 1000);
            }
            let rest = restRes.text;
            if (/xn--/i.test(rest)) {
                rest = rest.replace(
                    /(%(?:25)*[0-9a-f]{2}|^|[^a-z0-9._-])((?:[a-z0-9_-]+\\.)*xn--[^\\s/?#&='"<>.:]*(?:\\.(?:xn--[^\\s/?#&='"<>.:]*|[a-z0-9_-]+))*)/gi,
                    (_full, pre, hostCandidate) => {
                        if (hostCandidate.includes('.')) {
                            return pre + scrubHostAuthority(hostCandidate, nameGroups);
                        }
                        const puny = decodePunycode(hostCandidate.slice(4));
                        if (puny === null) return pre + 'hs';
                        let sc = puny;
                        for (const grp of nameGroups) sc = replaceTokenGroup(sc, grp, 'hs', true);
                        if (sc === puny) return pre + hostCandidate;
                        return pre + (foldViChars(sc).replace(/[^a-z0-9_-]/gi, '-') || 'hs');
                    }
                );
            }
            for (const grp of nameGroups) {
                if (rest) rest = replaceTokenGroup(rest, grp, 'HS', true);
            }
            let finalUrl = scheme + encodeNonAsciiUrlChars(cleanHost) + encodeNonAsciiUrlChars(rest);
            if (finalUrl.length > 1000) finalUrl = finalUrl.slice(0, 1000).replace(/%[0-9A-Fa-f]?$/, '');
            return finalUrl;
        };
        const URL_IN_TEXT_RE = /(?:https?:\\/\\/|\\/\\/(?:(?:[\\p{L}\\p{M}\\p{N}_%-]+\\.)+(?:xn--[^\\s/?#&='"<>.:]*|[a-z]{2,24})(?::\\d+)?|localhost(?::\\d+)?|xn--[^\\s/?#&='"<>.:]*|[a-z0-9._:-]+\\/))[^\\s"'<>]*|(?:[\\p{L}\\p{M}\\p{N}_-]+\\.)*xn--[^\\s/?#&='"<>.:]*(?:\\.(?:xn--[^\\s/?#&='"<>.:]*|[\\p{L}\\p{M}\\p{N}_-]+))*(?::\\d+)?(?:[/?#][^\\s"'<>]*)?|(?:[\\p{L}\\p{M}\\p{N}_%-]+\\.)+(?:xn--[^\\s/?#&='"<>.:]*|[a-z]{2,24})(?::\\d+)?[/?#][^\\s"'<>]*|(?:[\\p{L}\\p{M}\\p{N}_%-]+\\.){2,}(?:xn--[^\\s/?#&='"<>.:]*|[a-z]{2,24})(?::\\d+)?/giu;
        const scrubFreeTextChunk = (chunk, nameGroups, givenTokensSet) => {
            if (!chunk) return '';
            let outChunk = chunk;
            if (/%[0-9a-f]{2}/i.test(outChunk)) {
                outChunk = outChunk.replace(/[^\\s"'<>]*%(?:25){9,}[0-9a-f]{2}[^\\s"'<>]*/gi, '[HS]');
                outChunk = outChunk.replace(/[^\\s"'<>]*%[0-9a-f]{2}[^\\s"'<>]*/gi, (tok) => {
                    const td = decodeUrlSafeChars(tok);
                    if (!td.text) return tok;
                    let tsc = td.text;
                    for (const grp of nameGroups) {
                        tsc = replaceTokenGroup(tsc, grp, '[HS]', true);
                        if (grp.length === 1) tsc = replaceTokenGroup(tsc, grp, '[HS]', false);
                    }
                    tsc = scrubHonorificGivenNames(tsc, givenTokensSet);
                    if (tsc !== td.text) return td.unsafe ? '[HS]' : tsc;
                    return tok;
                });
                const d = decodeUrlSafeChars(outChunk);
                if (d.text) {
                    let sc = d.text;
                    for (const grp of nameGroups) sc = replaceTokenGroup(sc, grp, '[HS]', false);
                    sc = scrubHonorificGivenNames(sc, givenTokensSet);
                    if (sc !== d.text) {
                        outChunk = d.unsafe
                            ? sc.replace(/[^\\s"'<>]*\\[HS\\][^\\s"'<>]*%[89a-f][0-9a-f][^\\s"'<>]*/gi, '[HS]')
                            : sc;
                    }
                }
            }
            for (const grp of nameGroups) {
                outChunk = replaceTokenGroup(outChunk, grp, '[HS]', false);
            }
            outChunk = scrubHonorificGivenNames(outChunk, givenTokensSet);
            return outChunk;
        };
        const scrubStudentNames = (rawText, namesList) => {
            const text = String(rawText || '');
            if (!text) return '';
            const nameGroups = buildNameGroups(namesList);
            const givenTokensSet = buildGivenTokens(namesList);
            let res = '';
            let lastIdx = 0;
            URL_IN_TEXT_RE.lastIndex = 0;
            let mUrl;
            while ((mUrl = URL_IN_TEXT_RE.exec(text)) !== null) {
                if (mUrl.index > lastIdx) {
                    res += scrubFreeTextChunk(text.slice(lastIdx, mUrl.index), nameGroups, givenTokensSet);
                }
                const matchedUrl = mUrl[0];
                const trailMatch = matchedUrl.match(/[.,;:!?)\\]}]+$/);
                const coreUrl = trailMatch ? matchedUrl.slice(0, -trailMatch[0].length) : matchedUrl;
                const trail = trailMatch ? trailMatch[0] : '';
                const hasSchemeOrPunyPrefix = /^(?:https?:\\/\\/|\\/\\/|xn--)/i.test(coreUrl);
                const bareCheck = !hasSchemeOrPunyPrefix ? coreUrl.match(/^([^/?#]+)([/?#][\\s\\S]*|$)/) : null;
                if (!hasSchemeOrPunyPrefix && (!bareCheck || !isPlausibleBareHost(bareCheck[1], Boolean(bareCheck[2])))) {
                    res += scrubFreeTextChunk(matchedUrl, nameGroups, givenTokensSet);
                } else {
                    res += scrubStudentUrl(coreUrl, namesList) + trail;
                }
                lastIdx = mUrl.index + matchedUrl.length;
                if (matchedUrl.length === 0) URL_IN_TEXT_RE.lastIndex += 1;
            }
            if (lastIdx < text.length) {
                res += scrubFreeTextChunk(text.slice(lastIdx), nameGroups, givenTokensSet);
            }
            return res;
        };
        const checkTitleHasStudentName = (rawTitle, ownerName, namesList) => {
            const rawStr = String(rawTitle || '');
            const decodedTitle = rawStr.includes('%') ? (decodeUrlSafeChars(rawStr).text || rawStr) : rawStr;
            const foldedTitle = foldVi(decodedTitle.replace(/(?:%(?:25)*(?:20|2b|2d|2e|5f)|[\\-_+./])+/gi, ' '));
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
            const inactiveStudentIds = new Set();
            let studentCounter = 0;
            let identityAmbiguous = false;
            const rawClassStudents = Array.isArray(item.students) ? item.students : [];
            for (const cs of rawClassStudents.slice(0, maxStudents * 2)) {
                const stuObj = (cs && cs.student && typeof cs.student === 'object') ? cs.student : null;
                const stuId = stuObj && stuObj.id != null ? String(stuObj.id).trim() : '';
                const fullName = stuObj && typeof stuObj.fullName === 'string' ? stuObj.fullName.trim() : '';
                if (fullName && fullName.length >= 3) {
                    studentNamesSet.add(fullName);
                }
                if (!stuId || studentIdToKey.has(stuId)) identityAmbiguous = true;
                if (stuId && !studentIdToKey.has(stuId)) {
                    studentCounter += 1;
                    studentIdToKey.set(stuId, `stu_${studentCounter}`);
                }
                if (stuId && fullName && !studentIdToName.has(stuId)) {
                    studentIdToName.set(stuId, fullName);
                }
                if (stuId && cs && cs.activeInClass === false) {
                    inactiveStudentIds.add(stuId);
                }
            }
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
                    if (stuId && fullName && studentIdToName.has(stuId) && studentIdToName.get(stuId) !== fullName) identityAmbiguous = true;
                    if (stuId && fullName && !studentIdToName.has(stuId)) {
                        studentIdToName.set(stuId, fullName);
                    }
                }
            }
            const classStudentNames = Array.from(studentNamesSet);
            const classIdStr = (item && item.id != null) ? String(item.id).trim() : '';
            if (codeKey) {
                matchedClassMeta[codeKey] = {
                    classId: classIdStr,
                    studentIdToKey,
                    studentIdToName,
                    inactiveStudentIds,
                    studentNames: classStudentNames
                };
            }
            const slotsTruncated = item.slots.length > maxSlots;
            return item.slots.slice(0, maxSlots).map((s) => {
                const rawAtt = Array.isArray(s && s.studentAttendance) ? s.studentAttendance : null;
                const attTruncated = Array.isArray(rawAtt) && rawAtt.length > maxStudents;
                const attendanceIds = Array.isArray(rawAtt) ? rawAtt.slice(0, maxStudents).map((a) => a && a.student && a.student.id) : [];
                const attendanceAmbiguous = attendanceIds.some((id) => typeof id !== 'string' || !id || id.trim() !== id || id.length > 64) || new Set(attendanceIds).size !== attendanceIds.length;
                return {
                    _id: (s && s._id != null) ? String(s._id) : '',
                    index: (s && typeof s.index === 'number' && Number.isFinite(s.index)) ? s.index : null,
                    date: (s && typeof s.date === 'string') ? s.date.slice(0, 64) : '',
                    startTime: (s && typeof s.startTime === 'string') ? s.startTime.slice(0, 64) : '',
                    endTime: (s && typeof s.endTime === 'string') ? s.endTime.slice(0, 64) : '',
                    ...(s && typeof s.status === 'string' ? { status: s.status.slice(0, 64) } : {}),
                    ...(s && typeof s.cancelled === 'boolean' ? { cancelled: s.cancelled } : {}),
                    ...(s && typeof s.isCancelled === 'boolean' ? { isCancelled: s.isCancelled } : {}),
                    summary: (s && typeof s.summary === 'string') ? scrubStudentNames(s.summary.slice(0, 6000), classStudentNames) : '',
                    truncated: Boolean(slotsTruncated || attTruncated || identityAmbiguous || rawClassStudents.length > maxStudents * 2 || (s && typeof s.summary === 'string' && s.summary.length > 6000) || (rawAtt && rawAtt.some((a) => a && typeof a.comment === 'string' && a.comment.length > 15000))),
                    attendanceAmbiguous,
                    studentAttendance: Array.isArray(rawAtt)
                        ? rawAtt.slice(0, maxStudents).map((att) => {
                            const stuObj = (att && att.student && typeof att.student === 'object') ? att.student : null;
                            const stuId = stuObj && stuObj.id != null ? String(stuObj.id).trim() : '';
                            const stuKey = stuId ? studentIdToKey.get(stuId) : '';
                            const fullName = String((stuObj && stuObj.fullName) || '').trim();
                            let cleanComment = (att && typeof att.comment === 'string') ? att.comment.slice(0, 15000) : '';
                            if (cleanComment) {
                                const attNamesList = fullName && fullName.length >= 3
                                    ? [fullName, ...classStudentNames]
                                    : classStudentNames;
                                cleanComment = scrubStudentNames(cleanComment, attNamesList);
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

        // The LMS silently caps unpaginated work queries at 20 rows: scope by session,
        // then verify every missing attendee (or every attendee at the cap) separately.
        const MAX_WORK_REQUESTS = 120;
        const MAX_WORKS_RESPONSE_BYTES = 5 * 1024 * 1024;
        const WORK_RESPONSE_CAP = 20;
        let workRequests = 0;
        const workDeadline = Date.now() + Math.max(15000, (timeoutMs || 12000) * 3);
            const readBoundedJson = async (resp, maxBytes) => {
                if (resp && resp.headers && typeof resp.headers.get === 'function') {
                    const rawCl = resp.headers.get('content-length');
                    if (rawCl != null && String(rawCl).trim() !== '') {
                        const parsedCl = Number(rawCl);
                        if (Number.isFinite(parsedCl) && parsedCl > maxBytes) {
                            if (resp.body && typeof resp.body.cancel === 'function') {
                                try { await resp.body.cancel(); } catch (_) {}
                            }
                            return { overflow: true, data: null };
                        }
                    }
                }
                if (resp && resp.body && typeof resp.body.getReader === 'function') {
                    const reader = resp.body.getReader();
                    const decoder = new TextDecoder('utf-8');
                    let totalBytes = 0;
                    const chunks = [];
                    while (true) {
                        const step = await reader.read();
                        if (!step || step.done) break;
                        const val = step.value;
                        const byteLen = (val && typeof val.byteLength === 'number')
                            ? val.byteLength
                            : (typeof val === 'string'
                                ? (typeof TextEncoder !== 'undefined' ? new TextEncoder().encode(val).byteLength : val.length)
                                : 0);
                        totalBytes += byteLen;
                        if (totalBytes > maxBytes) {
                            try { await reader.cancel(); } catch (_) {}
                            return { overflow: true, data: null };
                        }
                        if (typeof val === 'string') {
                            chunks.push(val);
                        } else if (val) {
                            chunks.push(decoder.decode(val, { stream: true }));
                        }
                    }
                    chunks.push(decoder.decode());
                    return { overflow: false, data: JSON.parse(chunks.join('')) };
                }
                if (resp && typeof resp.text === 'function') {
                    const txt = await resp.text();
                    if (txt.length > maxBytes) {
                        return { overflow: true, data: null };
                    }
                    const utf8Bytes = (typeof TextEncoder !== 'undefined')
                        ? new TextEncoder().encode(txt).byteLength
                        : txt.length;
                    if (utf8Bytes > maxBytes) {
                        return { overflow: true, data: null };
                    }
                    return { overflow: false, data: JSON.parse(txt) };
                }
                return { overflow: false, data: await resp.json() };
            };
        const fetchSessionWorks = async (sessionId, studentId) => {
            if (authFailed || workRequests >= MAX_WORK_REQUESTS || Date.now() >= workDeadline) return null;
            workRequests += 1;
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), Math.min(timeoutMs || 12000, Math.max(1, workDeadline - Date.now())));
            try {
                const resp = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'content-type': 'application/json', 'authorization': bearerHeader, 'token': rawTokenHeader },
                    body: JSON.stringify({
                        operationName: 'findAllStudentWorks',
                        variables: { sessionId, ...(studentId ? { studentId } : {}) },
                        query: studentWorksQuery
                    }),
                    signal: ctrl.signal
                });
                if (!resp.ok) {
                    if (isAuthErrorPayload(resp.status, null)) authFailed = true;
                    return null;
                }
                const bounded = await readBoundedJson(resp, MAX_WORKS_RESPONSE_BYTES);
                if (bounded.overflow || ctrl.signal.aborted || Date.now() >= workDeadline) return null;
                const payload = bounded.data;
                if (payload && Array.isArray(payload.errors) && payload.errors.length) {
                    if (isAuthErrorPayload(resp.status, payload)) authFailed = true;
                    return null;
                }
                const result = (((payload || {}).data || {}).findAllStudentWorks || {});
                const rows = result.data;
                if (!Array.isArray(rows) || rows.length > maxStudents * 2 || result.truncated === true || result.hasNextPage === true) return null;
                const total = result.total != null ? result.total : result.totalCount;
                if (total != null && (!Number.isInteger(Number(total)) || Number(total) !== rows.length)) return null;
                return rows;
            } catch (_) {
                return null;
            } finally {
                clearTimeout(timer);
            }
        };
        const exactId = (value) => typeof value === 'string' && value.trim() && value === value.trim() && value.length <= 64 ? value : '';
        for (const code of classCodes) {
            if ((spckExemptClasses || []).includes(code)) continue;
            const meta = matchedClassMeta[code];
            const slotsArr = Array.isArray(out[code]) ? out[code] : [];
            for (const s of slotsArr) {
                if (!s || ![11, 12, 13].includes(s.index)) continue;
                if (targetLessons && (!Array.isArray(targetLessons[code]) || !targetLessons[code].includes(s.index + 1))) continue;
                const slotTime = Date.parse(s.date);
                if (Number.isFinite(slotTime) && slotTime > Date.now()) continue;
                const attendance = Array.isArray(s.studentAttendance) ? s.studentAttendance : [];
                const attendedKeys = attendance.filter((a) => a && ['ATTENDED', 'LATE_ARRIVED'].includes(String(a.status || '').trim().toUpperCase())).map((a) => a.studentKey);
                if (!attendedKeys.length) { s.studentWorks = []; continue; }
                const slotId = exactId(s._id);
                if (!meta || !exactId(meta.classId) || !slotId || !Number.isFinite(slotTime) || s.truncated || s.attendanceAmbiguous ||
                    slotsArr.filter((v) => v && v._id === s._id).length !== 1 ||
                    attendedKeys.some((k) => !k) || new Set(attendedKeys).size !== attendedKeys.length) {
                    s.studentWorksFetchError = true;
                    continue;
                }
                const rawIds = Array.from(meta.studentIdToKey).filter(([, key]) => attendedKeys.includes(key)).map(([id]) => id);
                if (rawIds.length !== attendedKeys.length || rawIds.some((id) => !exactId(id))) {
                    s.studentWorksFetchError = true;
                    continue;
                }
                const validateWorks = (rows, studentId) => {
                    if (!rows) return false;
                    const ids = new Set();
                    return rows.every((w) => {
                        if (!w || typeof w !== 'object' || !exactId(w.id) || ids.has(w.id)) return false;
                        ids.add(w.id);
                        return w.classId === meta.classId && w.classSessionId === slotId &&
                            exactId(w.studentId) && meta.studentIdToKey.has(w.studentId) &&
                            (!studentId || w.studentId === studentId) &&
                            (w.classSessionNumber == null || [s.index, s.index + 1].includes(Number(w.classSessionNumber)));
                    });
                };
                const sessionWorks = await fetchSessionWorks(slotId);
                if (!validateWorks(sessionWorks)) { s.studentWorksFetchError = true; continue; }
                let matchedWorks = sessionWorks.filter((w) => w.isDeleted !== true && String(w.isDeleted || '').toLowerCase() !== 'true');
                const capped = sessionWorks.length >= WORK_RESPONSE_CAP;
                let complete = true;
                for (const studentId of rawIds) {
                    if (!capped && matchedWorks.some((w) => w.studentId === studentId)) continue;
                    const individual = await fetchSessionWorks(slotId, studentId);
                    if (!validateWorks(individual, studentId) || individual.length >= WORK_RESPONSE_CAP) { complete = false; break; }
                    const priorWorks = matchedWorks.filter((w) => w.studentId === studentId);
                    if (priorWorks.some((w) => !individual.some((v) => JSON.stringify(v) === JSON.stringify(w)))) { complete = false; break; }
                    matchedWorks = matchedWorks.filter((w) => w.studentId !== studentId).concat(individual.filter((w) => w.isDeleted !== true && String(w.isDeleted || '').toLowerCase() !== 'true'));
                }
                if (!complete || matchedWorks.length > maxStudents * 2 || new Set(matchedWorks.map((w) => w.id)).size !== matchedWorks.length) { s.studentWorksFetchError = true; continue; }
                        s.studentWorks = matchedWorks.map((w) => {
                            const rawStuId = w.studentId != null ? String(w.studentId).trim() : '';
                            if (!rawStuId || !meta || !meta.studentIdToKey.has(rawStuId)) {
                                return { unknownOwner: true };
                            }
                            const latestObj = (w.latestData && typeof w.latestData === 'object') ? w.latestData : null;
                            const pubObj = (w.publishedData && typeof w.publishedData === 'object') ? w.publishedData : null;
                            const pickStr = (field) => {
                                if (latestObj && typeof latestObj[field] === 'string' && latestObj[field].trim()) return latestObj[field];
                                if (pubObj && typeof pubObj[field] === 'string' && pubObj[field].trim()) return pubObj[field];
                                if (typeof w[field] === 'string' && w[field].trim()) return w[field];
                                return '';
                            };
                            const pickArr = (field) => {
                                if (latestObj && Array.isArray(latestObj[field]) && latestObj[field].length > 0) return latestObj[field];
                                if (pubObj && Array.isArray(pubObj[field]) && pubObj[field].length > 0) return pubObj[field];
                                if (Array.isArray(w[field]) && w[field].length > 0) return w[field];
                                return [];
                            };
                            for (const obj of [latestObj, pubObj]) {
                                if (!obj) continue;
                                if (['title', 'thumbnail', 'comment'].some((k) => typeof obj[k] === 'string' && obj[k].length > (k === 'title' ? 500 : k === 'thumbnail' ? 1000 : 15000)) ||
                                    ['imageUrl', 'attachmentUrls', 'videoUrls', 'relatedUrls'].some((k) => Array.isArray(obj[k]) && (obj[k].length > (k === 'videoUrls' || k === 'relatedUrls' ? 20 : 30) || obj[k].some((v) => typeof v === 'string' ? v.length > 1000 : v && (String(v.name || '').length > 300 || String(v.url || '').length > 1000))))) {
                                    s.studentWorksFetchError = true;
                                }
                            }
                            const stuKey = meta.studentIdToKey.get(rawStuId) || '';
                            const ownerName = meta.studentIdToName.get(rawStuId) || '';
                            const namesList = meta.studentNames || [];
                            const isInactive = Boolean(meta.inactiveStudentIds && meta.inactiveStudentIds.has(rawStuId));
                            const rawTitle = pickStr('title').trim().slice(0, 500);
                            const hasStudentInfo = checkTitleHasStudentName(rawTitle, ownerName, namesList);
                            const cleanTitle = scrubStudentNames(rawTitle, namesList).slice(0, 500);
                            const rawWorkComment = pickStr('comment').slice(0, 15000);
                            const cleanWorkComment = scrubStudentNames(rawWorkComment, namesList).slice(0, 15000);
                            const rawThumb = pickStr('thumbnail');
                            const rawImages = pickArr('imageUrl');
                            const rawAttachments = pickArr('attachmentUrls');
                            const rawVideos = pickArr('videoUrls');
                            const rawRelated = pickArr('relatedUrls');
                            return {
                                ...(stuKey ? { studentKey: stuKey } : {}),
                                ...(isInactive ? { inactiveInClass: true } : {}),
                                title: cleanTitle,
                                hasStudentInfoInTitle: Boolean(hasStudentInfo),
                                thumbnail: scrubStudentUrl(rawThumb, namesList),
                                imageUrl: rawImages
                                    .filter((u) => typeof u === 'string' && u.trim())
                                    .slice(0, 30)
                                    .map((u) => scrubStudentUrl(u, namesList)),
                                attachmentUrls: rawAttachments
                                    .filter((u) => typeof u === 'string' && u.trim())
                                    .slice(0, 30)
                                    .map((u) => scrubStudentUrl(u, namesList)),
                                videoUrls: rawVideos
                                    .filter((u) => typeof u === 'string' && u.trim())
                                    .slice(0, 20)
                                    .map((u) => scrubStudentUrl(u, namesList)),
                                relatedUrls: rawRelated
                                    .filter((r) => r && typeof r === 'object' && (r.url || r.name))
                                    .slice(0, 20)
                                    .map((r) => ({
                                        name: typeof r.name === 'string' ? scrubStudentNames(r.name.trim(), namesList).slice(0, 300) : '',
                                        url: typeof r.url === 'string' ? scrubStudentUrl(r.url, namesList) : ''
                                    })),
                                comment: cleanWorkComment
                            };
                        });
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

        async def _navigate_and_authenticate_lms(*, clear_stale_storage: bool = False) -> str:
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
                    elif ctx_obj is not None and callable(getattr(ctx_obj, "new_page", None)):
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
                        eval_parsed = urlsplit(str(getattr(eval_page, "url", "") or "").strip())
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
                    after_login = urlsplit(str(getattr(eval_page, "url", "") or "").strip())
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
            nav_status = await _navigate_and_authenticate_lms(clear_stale_storage=False)
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
                    code: [{"fetch_error": True} for _ in range(MAX_LMS_SLOTS_PER_CLASS)]
                    for code in all_codes
                }

        eval_args = {
            "endpoint": LMS_GRAPHQL_ENDPOINT,
            "classCodes": normalized_codes,
            "centres": resolved_centres,
            "maxSlots": MAX_LMS_SLOTS_PER_CLASS,
            "maxStudents": MAX_LMS_STUDENTS_PER_SLOT,
            "timeoutMs": int(timeout_ms),
            "targetLessons": target_lessons_by_class,
            "spckExemptClasses": [
                code for code in normalized_codes if _is_spck_exempt_course(code)
            ],
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
            retry_nav = await _navigate_and_authenticate_lms(clear_stale_storage=True)
            if retry_nav == "ok":
                eval_args["preResolvedToken"] = pre_resolved_token
                eval_args["centres"] = resolved_centres
                raw_res = await eval_page.evaluate(js_script, eval_args)
            elif retry_nav == "untrusted_redirect":
                return {
                    code: [{"fetch_error": True} for _ in range(MAX_LMS_SLOTS_PER_CLASS)]
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
        (st_dict.get("year") if st_dict else None) or ctx.get("target_year") or result.get("year")
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
            parsed = None
            with contextlib.suppress(ValueError):
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


def _clear_scan_results_ai_cache(scan_results: Sequence[Any]) -> None:
    for r_item in scan_results:
        if isinstance(r_item, dict):
            r_item.pop("_ai_comment_verdicts", None)
            r_item.pop("_ai_reviewer_id", None)
            raw_st = r_item.get("scan_target")
            if isinstance(raw_st, dict):
                raw_st.pop("_ai_comment_verdicts", None)
                raw_st.pop("_ai_reviewer_id", None)


def _reset_reviewer_batch_counter(active_reviewer: Any) -> None:
    reset_fn = (
        getattr(active_reviewer, "reset_batch_counter", None)
        if active_reviewer is not None
        else None
    )
    if callable(reset_fn) and not inspect.iscoroutinefunction(reset_fn):
        with contextlib.suppress(Exception):
            res_reset = reset_fn()
            if inspect.iscoroutine(res_reset):
                res_reset.close()


def inspect_and_attach_quality_issues(
    scan_results: list[dict[str, Any]],
    *,
    lms_slots_by_class: dict[str, list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
    ai_reviewer: Any = None,
    latest_week: Mapping[str, Any] | tuple[int, int] | None = None,
    reset_ai_batch_counter: bool = False,
) -> None:
    """Evaluate and attach LMS & Zalo quality issues onto each scan result in place."""
    is_internal_proxy = isinstance(
        ai_reviewer, (_BatchRecordingReviewer, _PrecomputedBatchReviewer)
    )
    active_rev = None if is_internal_proxy else _resolve_active_lms_ai_reviewer(ai_reviewer)
    if reset_ai_batch_counter and not is_internal_proxy:
        _reset_reviewer_batch_counter(active_rev)
        _clear_scan_results_ai_cache(scan_results)
    has_lms_map = isinstance(lms_slots_by_class, dict)
    slots_map: dict[str, list[dict[str, Any]]] = (
        lms_slots_by_class if isinstance(lms_slots_by_class, dict) else {}
    )
    effective_now = now or now_business()
    current_business = (
        effective_now.astimezone(BUSINESS_TIMEZONE)
        if effective_now.tzinfo is not None
        else effective_now.replace(tzinfo=BUSINESS_TIMEZONE)
    )
    current_business_date = current_business.date()
    iso_cal = current_business.isocalendar()
    inferred_latest_period: tuple[int, int] = (int(iso_cal[0]), int(iso_cal[1]))
    for r_item in scan_results:
        if not isinstance(r_item, Mapping):
            continue
        raw_r_st = r_item.get("scan_target")
        r_st: Mapping[str, Any] = raw_r_st if isinstance(raw_r_st, Mapping) else {}
        raw_r_ev = r_item.get("evidence")
        r_ev: Mapping[str, Any] = raw_r_ev if isinstance(raw_r_ev, Mapping) else {}
        raw_r_ctx = r_ev.get("date_context")
        r_ctx: Mapping[str, Any] = raw_r_ctx if isinstance(raw_r_ctx, Mapping) else {}
        raw_w = r_st.get("week") or r_ctx.get("target_week") or r_item.get("week")
        raw_y = r_st.get("year") or r_ctx.get("target_year") or r_item.get("year")
        with contextlib.suppress(TypeError, ValueError):
            w_int = int(str(raw_w).strip())
            y_int = int(str(raw_y).strip())
            if 2000 <= y_int <= 2100 and 1 <= w_int <= 53:
                if (y_int, w_int) > inferred_latest_period:
                    inferred_latest_period = (y_int, w_int)
    for result in scan_results:
        if not isinstance(result, dict):
            continue
        st = result.get("scan_target")
        st_dict: dict[str, Any] | None = st if isinstance(st, dict) else None
        ev = result.get("evidence")
        ev_dict: dict[str, Any] = ev if isinstance(ev, dict) else {}
        raw_ctx = ev_dict.get("date_context")
        ctx: dict[str, Any] = raw_ctx if isinstance(raw_ctx, dict) else {}
        early_target_date_iso = _resolve_target_class_date_iso(result, st_dict, ctx)
        group_name_val = str(
            (st_dict.get("group_name") if st_dict else "")
            or result.get("group_name")
            or result.get("group")
            or ""
        ).strip()
        ready_prev_week = False
        should_defer_target = False
        if latest_week is not None:
            candidate_target = {
                **(st_dict or {}),
                "class_code": (st_dict.get("class_code") if st_dict else None)
                or ctx.get("target_class_code")
                or result.get("class_code"),
                "lesson": (st_dict.get("lesson") if st_dict else None)
                or ctx.get("target_lesson")
                or result.get("lesson"),
                "week": (st_dict.get("week") if st_dict else None)
                or ctx.get("target_week")
                or result.get("week"),
                "year": (st_dict.get("year") if st_dict else None)
                or ctx.get("target_year")
                or result.get("year"),
                "class_date_iso": early_target_date_iso
                or (st_dict.get("class_date_iso") if st_dict else None)
                or result.get("class_date_iso"),
                "class_date": (st_dict.get("class_date") if st_dict else None)
                or result.get("class_date"),
                "group_name": group_name_val,
            }
            elig = evaluate_scan_target(
                candidate_target,
                latest_week=latest_week,
                now=current_business,
            )
            if elig.reason == READY_PREVIOUS_WEEK:
                ready_prev_week = True
            elif not elig.eligible and elig.reason != DEFERRED_INVALID_TARGET:
                should_defer_target = True
        else:
            raw_tw = (
                (st_dict.get("week") if st_dict else None)
                or ctx.get("target_week")
                or result.get("week")
            )
            raw_ty = (
                (st_dict.get("year") if st_dict else None)
                or ctx.get("target_year")
                or result.get("year")
            )
            with contextlib.suppress(TypeError, ValueError):
                tw_int = int(str(raw_tw).strip())
                ty_int = int(str(raw_ty).strip())
                if 2000 <= ty_int <= 2100 and 1 <= tw_int <= 53:
                    if (ty_int, tw_int) < inferred_latest_period:
                        ready_prev_week = True
        if not ready_prev_week and early_target_date_iso:
            if early_target_date_iso > current_business_date.isoformat():
                should_defer_target = True
            elif early_target_date_iso == current_business_date.isoformat():
                end_time = parse_group_end_time(group_name_val)
                target_day = date.fromisoformat(early_target_date_iso)
                if end_time is None or current_business < datetime.combine(
                    target_day, end_time, tzinfo=BUSINESS_TIMEZONE
                ):
                    should_defer_target = True
        if should_defer_target:
            result["session_deferred"] = True
            result["lms_quality_issues"] = []
            result["zalo_content_issues"] = []
            result["quality_issues"] = []
            result["is_failed"] = False
            if st_dict is not None:
                st_dict["session_deferred"] = True
                st_dict["lms_quality_issues"] = []
                st_dict["zalo_content_issues"] = []
                st_dict["quality_issues"] = []
                st_dict["is_failed"] = False
            continue
        result.pop("session_deferred", None)
        if st_dict is not None:
            st_dict.pop("session_deferred", None)
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
        khoi_str = str(result.get("khoi") or (st_dict.get("khoi") if st_dict else "") or "").strip()
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

        if not is_internal_proxy and active_rev is not None:
            rev_id = id(active_rev)
            if result.get("_ai_reviewer_id") not in (None, rev_id):
                result.pop("_ai_comment_verdicts", None)
                if st_dict is not None:
                    st_dict.pop("_ai_comment_verdicts", None)
            result["_ai_reviewer_id"] = rev_id
        raw_row_cache = result.get("_ai_comment_verdicts")
        if not isinstance(raw_row_cache, dict) and st_dict is not None:
            raw_row_cache = st_dict.get("_ai_comment_verdicts")
        row_verdict_cache: dict[str, str] = (
            dict(raw_row_cache) if isinstance(raw_row_cache, dict) else {}
        )

        slot_evaluated = False
        slot_eval: dict[str, Any] | None = None
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
                            ai_reviewer=ai_reviewer,
                            verdict_cache=row_verdict_cache,
                        )
                        lms_issues = list(slot_eval.get("issues") or [])
                    else:
                        target_date_iso = _resolve_target_class_date_iso(result, st_dict, ctx)
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
                                ai_reviewer=ai_reviewer,
                                verdict_cache=row_verdict_cache,
                            )
                            lms_summary_clean = str(slot_eval.get("summary_clean") or "").strip()
                            lms_issues = list(slot_eval.get("issues") or [])
                            result["lms_is_checkpoint"] = bool(slot_eval.get("is_checkpoint"))
                            if st_dict is not None:
                                st_dict["lms_is_checkpoint"] = bool(slot_eval.get("is_checkpoint"))
                        else:
                            lms_issues = [slot_issue] if slot_issue else []

        if row_verdict_cache:
            result["_ai_comment_verdicts"] = dict(row_verdict_cache)
            if st_dict is not None:
                st_dict["_ai_comment_verdicts"] = dict(row_verdict_cache)

        if slot_evaluated:
            metadata_eval = slot_eval or evaluate_lms_slot_quality(
                None,
                class_code=class_code,
                khoi=khoi_str,
                lesson=lesson_str,
                ai_reviewer=ai_reviewer,
            )
            for metadata_key in ("lms_attendance", "lms_comments", "lms_spck"):
                result[metadata_key] = dict(metadata_eval[metadata_key])
                if st_dict is not None:
                    st_dict[metadata_key] = dict(metadata_eval[metadata_key])

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

        comments_meta = result.get("lms_comments")
        comments_verified = (
            slot_evaluated
            and isinstance(comments_meta, Mapping)
            and str(comments_meta.get("status") or "").strip().upper()
            in {"COMPLETE", "REVIEW", "MISSING", "EXEMPT"}
        )
        spck_meta = result.get("lms_spck")
        spck_status = (
            str(spck_meta.get("status") or "").strip().upper()
            if isinstance(spck_meta, Mapping)
            else ""
        )
        spck_reason = (
            str(spck_meta.get("reason") or "").strip().lower()
            if isinstance(spck_meta, Mapping)
            else ""
        )
        att_meta = result.get("lms_attendance")
        spck_att = spck_meta.get("attendance") if isinstance(spck_meta, Mapping) else None
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
        spck_superseded = slot_evaluated and (
            spck_status in {"NOT_REQUIRED", "COMPLETE", "PARTIAL", "MISSING", "EXEMPT"}
            or spck_attendance_unverified
        )
        lms_eval_indeterminate = (
            (not slot_evaluated)
            or any(issue_code(x) == ISSUE_CODE_LMS_MANUAL_CHECK for x in lms_issues)
            or not comments_verified
            or not spck_superseded
        )
        if lms_eval_indeterminate and prior_lms_issues:
            seen_lms = {issue_text(x) for x in lms_issues if issue_text(x)}
            spck_codes = {
                ISSUE_CODE_LMS_SPCK_MISSING,
                ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
                ISSUE_CODE_LMS_SPCK_NOTE,
            }
            for prior_l in prior_lms_issues:
                pt = issue_text(prior_l)
                if pt and pt not in seen_lms:
                    is_prior_spck = issue_code(prior_l) in spck_codes or "spck" in fold_text(pt)
                    if slot_evaluated and (
                        (is_prior_spck and spck_superseded)
                        or (not is_prior_spck and comments_verified)
                    ):
                        previous = list(result.get("lms_previous_quality_issues") or [])
                        if pt not in {issue_text(item) for item in previous}:
                            previous.append(prior_l)
                        result["lms_previous_quality_issues"] = previous
                        if st_dict is not None:
                            st_dict["lms_previous_quality_issues"] = list(previous)
                        continue
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
            str((clf.get("decision") if isinstance(clf, Mapping) else "") or "").strip().upper()
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

        if (
            zalo_issues
            or "zalo_content_issues" in result
            or (st_dict is not None and "zalo_content_issues" in st_dict)
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
    class_keys = [normalize_class_code(k) for k in raw_keys if normalize_class_code(k)]
    if not class_keys:
        return False
    inspected = 0
    for code in class_keys:
        slots = lms_slots_by_class.get(code)
        if not isinstance(slots, Sequence) or isinstance(slots, (str, bytes)) or len(slots) == 0:
            return False
        inspected += 1
        if not all(isinstance(s, Mapping) and bool(s.get("fetch_error")) for s in slots):
            return False
    return inspected > 0


def lms_target_lessons_by_class(scan_results: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Limit SPCK reads to lessons explicitly requested by scan targets."""
    targets: dict[str, list[int]] = {}
    for row in scan_results:
        if not isinstance(row, dict):
            continue
        target = row.get("scan_target")
        target = target if isinstance(target, dict) else {}
        evidence = row.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        context = evidence.get("date_context")
        context = context if isinstance(context, dict) else {}
        code = normalize_class_code(
            target.get("class_code")
            or context.get("target_class_code")
            or row.get("class_code")
            or ""
        )
        if not code:
            continue
        lesson = _parse_lesson_number(
            target.get("lesson") or context.get("target_lesson") or row.get("lesson")
        )
        lessons = targets.setdefault(code, [])
        if lesson is not None and lesson not in lessons:
            lessons.append(lesson)
    return targets


async def fetch_lms_slots_for_targets(
    gateway: Any,
    page: Any,
    class_codes: list[str],
    target_lessons_by_class: dict[str, list[int]],
) -> Any:
    """Keep existing injected gateways compatible with their two-argument contract."""
    operation = gateway.fetch_class_slots
    parameters = inspect.signature(operation).parameters
    supports_targets = "target_lessons_by_class" in parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )
    if supports_targets:
        return await operation(page, class_codes, target_lessons_by_class=target_lessons_by_class)
    return await operation(page, class_codes)


async def inspect_and_attach_quality_issues_async(
    scan_results: list[dict[str, Any]],
    *,
    lms_slots_by_class: dict[str, list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
    ai_reviewer: Any = None,
    latest_week: Mapping[str, Any] | tuple[int, int] | None = None,
    reset_ai_batch_counter: bool = False,
) -> None:
    """Async non-blocking wrapper around inspect_and_attach_quality_issues."""
    active_reviewer = _resolve_active_lms_ai_reviewer(ai_reviewer)
    if reset_ai_batch_counter:
        _reset_reviewer_batch_counter(active_reviewer)
        _clear_scan_results_ai_cache(scan_results)
    elif active_reviewer is not None:
        rev_id = id(active_reviewer)
        for r_item in scan_results:
            if isinstance(r_item, dict):
                if r_item.get("_ai_reviewer_id") not in (None, rev_id):
                    r_item.pop("_ai_comment_verdicts", None)
                    raw_st = r_item.get("scan_target")
                    if isinstance(raw_st, dict):
                        raw_st.pop("_ai_comment_verdicts", None)
                r_item["_ai_reviewer_id"] = rev_id

    effective_ai_reviewer: Any = active_reviewer
    if active_reviewer is not None and isinstance(lms_slots_by_class, dict) and lms_slots_by_class:
        collector = _BatchRecordingReviewer()
        inspect_and_attach_quality_issues(
            copy.deepcopy(scan_results),
            lms_slots_by_class=lms_slots_by_class,
            now=now,
            ai_reviewer=collector,
            latest_week=latest_week,
            reset_ai_batch_counter=False,
        )
        if collector.batches:
            precomputed_verdicts: list[dict[str, str]] = []
            for batch_items, batch_kwargs in collector.batches:
                verdict_map = await _invoke_ai_comment_reviewer_async(
                    active_reviewer,
                    batch_items,
                    **batch_kwargs,
                )
                precomputed_verdicts.append(verdict_map)
            effective_ai_reviewer = _PrecomputedBatchReviewer(precomputed_verdicts)
        else:
            effective_ai_reviewer = _PrecomputedBatchReviewer([])

    inspect_and_attach_quality_issues(
        scan_results,
        lms_slots_by_class=lms_slots_by_class,
        now=now,
        ai_reviewer=effective_ai_reviewer,
        latest_week=latest_week,
        reset_ai_batch_counter=False,
    )


async def fetch_and_evaluate_lms_quality(
    page: Any,
    scan_results: list[dict[str, Any]],
    *,
    lms_slots_by_class: dict[str, list[dict[str, Any]]] | None = None,
    lms_quality_gateway: Any = None,
    allow_page_fallback: bool = False,
    target_lessons_by_class: dict[str, list[int]] | None = None,
    ai_reviewer: Any = None,
    latest_week: Mapping[str, Any] | tuple[int, int] | None = None,
    now: datetime | None = None,
    reset_ai_batch_counter: bool = True,
) -> dict[str, list[dict[str, Any]]] | None:
    """Shared helper to fetch LMS class slots (if needed) and attach quality issues onto scan_results."""
    resolved_lms_slots: dict[str, list[dict[str, Any]]] | None = (
        dict(lms_slots_by_class) if isinstance(lms_slots_by_class, dict) else None
    )
    requested_lessons = (
        target_lessons_by_class
        if target_lessons_by_class is not None
        else lms_target_lessons_by_class(scan_results)
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

            active_gateway = getattr(ExternalGateways.production(), "lms_quality", None)
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
                    fetched = await fetch_lms_slots_for_targets(
                        active_gateway, page, candidate_codes, requested_lessons
                    )
                    if isinstance(fetched, dict):
                        resolved_lms_slots = fetched
                    else:
                        resolved_lms_slots = {
                            code: [{"fetch_error": True}] for code in candidate_codes
                        }
                except Exception:
                    resolved_lms_slots = {code: [{"fetch_error": True}] for code in candidate_codes}
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
                    page, class_names=candidate_codes, target_lessons_by_class=requested_lessons
                )
                if isinstance(fetched_slots, dict) and fetched_slots:
                    resolved_lms_slots = fetched_slots
            except Exception:
                resolved_lms_slots = {code: [{"fetch_error": True}] for code in candidate_codes}

    resolved_ai_reviewer = ai_reviewer or (
        getattr(lms_quality_gateway, "ai_reviewer", None)
        if lms_quality_gateway is not None
        and "ai_reviewer" in getattr(lms_quality_gateway, "__dict__", {})
        else None
    )
    await inspect_and_attach_quality_issues_async(
        scan_results,
        lms_slots_by_class=resolved_lms_slots,
        now=now,
        ai_reviewer=resolved_ai_reviewer,
        latest_week=latest_week,
        reset_ai_batch_counter=reset_ai_batch_counter,
    )
    if is_lms_fetch_unavailable(resolved_lms_slots):
        for r in scan_results:
            if isinstance(r, dict):
                r["lms_fetch_unavailable"] = True
                if isinstance(r.get("scan_target"), dict):
                    r["scan_target"]["lms_fetch_unavailable"] = True
    return resolved_lms_slots
