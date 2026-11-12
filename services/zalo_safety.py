"""Pure identity checks used before any Zalo-derived external action."""

from datetime import datetime, tzinfo as TzInfo
import re
import unicodedata

from services.report_status import (
    REPORT_DATE_LOOKBACK_DAYS,
    identity_tokens as _identity_tokens,
)


def class_code_matches_group(class_code: str, group_name: str) -> bool:
    """Match a complete class code, never a prefix such as JSB14/JSB140."""
    parts = _identity_tokens(class_code)
    if len(parts) < 2:
        return False
    pattern = r"(?<![A-Z0-9])" + r"[\s_-]*".join(
        re.escape(part) for part in parts
    ) + r"(?![A-Z0-9])"
    return re.search(pattern, unicodedata.normalize("NFKC", group_name or "").upper()) is not None


def open_chat_matches(expected_group: str, actual_header: str) -> bool:
    """Require the complete expected group identity in the opened chat header."""
    expected = _identity_tokens(expected_group)
    actual = _identity_tokens(actual_header)
    if not expected or not actual or len(actual) < len(expected):
        return False
    width = len(expected)
    return any(actual[index : index + width] == expected for index in range(len(actual) - width + 1))


def report_delay_days(
    class_date: str,
    message_date: datetime,
    *,
    default_year: int,
) -> int | None:
    """Return report delay; only an absent class date may use legacy fallback."""
    class_day = parse_class_date(
        class_date,
        default_year=default_year,
        timezone_info=message_date.tzinfo,
    )
    if class_day is None:
        return None
    message_day = message_date.replace(hour=0, minute=0, second=0, microsecond=0)
    return (message_day - class_day).days


def infer_previous_lesson_date(
    content_dates: list[datetime],
    *,
    next_class_date: datetime | None,
    message_date: datetime,
    max_lookback_days: int = REPORT_DATE_LOOKBACK_DAYS,
) -> datetime | None:
    """Select a content date that can identify the completed lesson.

    A date written in a Zalo message is never treated as the send timestamp.
    It is used only to disambiguate a report sent before the BCNB date currently
    stored for the group.  The date must be before both the BCNB date and the
    UI send date, and within the evidence window; otherwise the caller must
    fail closed for manual review.
    """
    if next_class_date is None or max_lookback_days < 0:
        return None
    next_day = next_class_date.date()
    sent_day = message_date.date()
    lower_bound = next_day.fromordinal(next_day.toordinal() - max_lookback_days)
    candidates = [
        value
        for value in content_dates
        if lower_bound <= value.date() < next_day and value.date() <= sent_day
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda value: value.date())


def infer_report_lesson_date(
    content_dates: list[datetime],
    raw_content: str,
    *,
    message_date: datetime,
    max_lookback_days: int = 45,
) -> datetime | None:
    """Select the date of the completed lesson mentioned in a report.

    Report messages commonly contain several dates: the completed lesson,
    the next lesson and a homework deadline.  The UI separator remains the
    send date; this helper only chooses a content date when it is in the past
    and its surrounding text identifies it as the reported lesson.  Ambiguous
    candidates fail closed so the caller can request manual review.
    """
    if not content_dates or max_lookback_days < 0:
        return None
    sent_day = message_date.date()
    lower_bound = sent_day.fromordinal(sent_day.toordinal() - max_lookback_days)
    candidates = [
        value
        for value in content_dates
        if lower_bound <= value.date() <= sent_day
    ]
    if not candidates:
        return None

    normalized = unicodedata.normalize("NFKC", raw_content or "").casefold()
    positive_context = (
        "sau buổi",
        "buổi học",
        "buổi số",
        "ngày học",
        "thời gian",
        "tổng kết",
        "update sau",
    )
    negative_context = (
        "kế tiếp",
        "tiếp theo",
        "deadline",
        "hạn làm",
        "hạn nộp",
        "bài tập về nhà",
        "nhiệm vụ khi về nhà",
    )
    scored: list[tuple[int, datetime]] = []
    for value in candidates:
        variants = {
            value.strftime("%d/%m/%Y"),
            f"{value.day}/{value.month}/{value.year}",
            value.strftime("%d/%m"),
            f"ngày {value.day} tháng {value.month}",
        }
        variants.discard("")
        score = 0
        found = False
        for variant in variants:
            start = 0
            while True:
                index = normalized.find(variant.casefold(), start)
                if index < 0:
                    break
                found = True
                context = normalized[max(0, index - 60) : index + len(variant) + 30]
                score += 3
                score += sum(2 for marker in positive_context if marker in context)
                score -= sum(3 for marker in negative_context if marker in context)
                start = index + len(variant)
        if found:
            # A newer valid lesson date is the safer fallback when the report
            # uses a terse format without a nearby label.
            score += max(0, 10 - (sent_day - value.date()).days)
        scored.append((score, value))

    scored.sort(key=lambda item: (item[0], item[1].date()), reverse=True)
    if len(scored) == 1:
        return scored[0][1] if scored[0][0] > 0 else None
    best_score, best_value = scored[0]
    second_score = scored[1][0]
    if best_score <= 0 or best_score == second_score:
        return None
    return best_value


_NEXT_LESSON_SECTION_PHRASES = (
    r"buổi\s+(?:học\s+)?(?:kế\s+tiếp|tiếp\s+theo|sau|tới)"
    r"|lịch\s+học\s+(?:buổi\s+)?(?:kế\s+tiếp|tiếp\s+theo|sau|tới|tuần\s+sau)"
    r"|nội\s+dung\s+(?:buổi|bài)(?:\s+học)?\s+(?:kế\s+tiếp|tiếp\s+theo|sau|tới)"
    r"|bài\s+học\s+(?:kế\s+tiếp|tiếp\s+theo|sau|tới)"
    r"|chuẩn\s+bị\s+(?:cho\s+)?(?:buổi|bài)(?:\s+học)?\s+(?:kế\s+tiếp|tiếp\s+theo|sau|tới)"
    r"|bài\s+tập\s+về\s+nhà"
    r"|nhiệm\s+vụ\s+(?:khi\s+)?về\s+nhà"
    r"|dặn\s+dò\s+(?:cho\s+)?(?:buổi|bài)(?:\s+học)?\s+(?:kế\s+tiếp|tiếp\s+theo|sau|tới)"
)

_LEADING_LINE_BULLET_OR_NUMBER_RE = re.compile(
    r"^(?:[^\w\n]+|(?:[ivxlcdm]+|\d+)\s*[.):\-]\s*)+",
    flags=re.IGNORECASE | re.UNICODE,
)

_TRAILING_NEXT_LESSON_HEADER_RE = re.compile(
    rf"^(?:{_NEXT_LESSON_SECTION_PHRASES})\b",
    flags=re.IGNORECASE,
)

_INLINE_NEXT_LESSON_SECTION_RE = re.compile(
    rf"(?:{_NEXT_LESSON_SECTION_PHRASES})\s*[:\-–—]",
    flags=re.IGNORECASE,
)

_REPORT_START_RE = re.compile(
    r"\b(?:báo\s+cáo|tổng\s+kết|update(?:\s+sau)?)\s+buổi\b",
    flags=re.IGNORECASE,
)


_HOMEWORK_SECTION_HEADER_RE = re.compile(
    r"^(?:bài\s+tập\s+về\s+nhà|nhiệm\s+vụ\s+(?:khi\s+)?về\s+nhà"
    r"|chuẩn\s+bị\s+(?:cho\s+)?(?:buổi|bài)(?:\s+học)?\s+(?:kế\s+tiếp|tiếp\s+theo|sau|tới)"
    r"|dặn\s+dò\s+(?:cho\s+)?(?:buổi|bài)(?:\s+học)?\s+(?:kế\s+tiếp|tiếp\s+theo|sau|tới))\b",
    flags=re.IGNORECASE,
)

_RESUMED_CURRENT_SESSION_PHRASES = (
    r"nội\s+dung\s+(?:buổi\s+học|chính|đã\s+học|học\s+tập)(?!\s*(?:kế\s+tiếp|tiếp\s+theo|sau|tới))"
    r"|kiến\s+thức(?:\s+trọng\s+tâm|\s+đã\s+học|\s+buổi\s+học)?"
    r"|chủ\s+đề(?:\s+buổi\s+học|\s+bài\s+học)?"
    r"|tình\s+hình(?:\s+lớp(?:\s+học)?|\s+học\s+tập|\s+buổi\s+học)?"
    r"|nhận\s+xét(?:\s+chung|\s+buổi\s+học|\s+lớp|\s+học\s+viên|\s+từng\s+bạn)?"
    r"|đánh\s+giá(?:\s+chung|\s+buổi\s+học|\s+lớp)?"
    r"|sĩ\s+số|điểm\s+danh"
    r"|sản\s+phẩm(?:\s+học\s+viên|\s+trong\s+buổi|\s+buổi\s+học)?"
    r"|hoạt\s+động(?:\s+trên\s+lớp|\s+buổi\s+học)?"
    r"|kết\s+quả(?:\s+buổi\s+học|\s+học\s+tập)?"
)

_RESUMED_CURRENT_SESSION_HEADER_RE = re.compile(
    rf"^(?:{_RESUMED_CURRENT_SESSION_PHRASES})\b",
    flags=re.IGNORECASE,
)

_RESUMED_AFTER_HOMEWORK_PHRASES = (
    r"nội\s+dung(?:\s+bài\s+học)?(?!\s*(?:buổi|bài)?\s*(?:kế\s+tiếp|tiếp\s+theo|sau|tới))"
    r"|tên\s+bài(?:\s+học)?"
)

_RESUMED_AFTER_HOMEWORK_HEADER_RE = re.compile(
    rf"^(?:{_RESUMED_AFTER_HOMEWORK_PHRASES})\s*[:\-–—]",
    flags=re.IGNORECASE,
)

_INLINE_SECTION_BREAK_RE = re.compile(
    rf"(?P<sep>[.;!|]|\s+[-–—]\s+)\s*(?=(?:{_NEXT_LESSON_SECTION_PHRASES}|{_RESUMED_CURRENT_SESSION_PHRASES}|{_RESUMED_AFTER_HOMEWORK_PHRASES})\s*[:\-–—]|(?:báo\s+cáo|tổng\s+kết|update(?:\s+sau)?)\s+buổi\b)",
    flags=re.IGNORECASE,
)


def _strip_trailing_next_lesson_sections(message_text: str) -> str:
    """Truncate next-lesson / homework / preparation sections from a report message without losing later report sections."""
    normalized = unicodedata.normalize("NFKC", message_text or "")
    if not normalized:
        return ""
    normalized = _INLINE_SECTION_BREAK_RE.sub(
        lambda m: f"{m.group('sep').rstrip()}\n", normalized
    )
    lines = normalized.splitlines()
    kept_lines: list[str] = []
    skip_mode: str | None = None
    for idx, line in enumerate(lines):
        cleaned = _LEADING_LINE_BULLET_OR_NUMBER_RE.sub("", line.strip()).strip()
        if cleaned and _TRAILING_NEXT_LESSON_HEADER_RE.match(cleaned):
            remainder = "\n".join(lines[idx:])
            prefix = "\n".join(kept_lines).strip()
            if not prefix and _REPORT_START_RE.search(remainder):
                m_inline = _INLINE_NEXT_LESSON_SECTION_RE.search(line)
                if m_inline and not _REPORT_START_RE.search(line[m_inline.start() :]):
                    kept_lines.append(line[: m_inline.start()])
                    skip_mode = (
                        "homework"
                        if _HOMEWORK_SECTION_HEADER_RE.match(cleaned)
                        else "next_lesson"
                    )
                elif _REPORT_START_RE.search(line):
                    kept_lines.append(line)
                else:
                    skip_mode = (
                        "homework"
                        if _HOMEWORK_SECTION_HEADER_RE.match(cleaned)
                        else "next_lesson"
                    )
                continue
            skip_mode = (
                "homework"
                if _HOMEWORK_SECTION_HEADER_RE.match(cleaned)
                else "next_lesson"
            )
            continue

        if skip_mode is not None:
            can_resume = bool(
                cleaned
                and not _TRAILING_NEXT_LESSON_HEADER_RE.match(cleaned)
                and (
                    _REPORT_START_RE.search(cleaned)
                    or _RESUMED_CURRENT_SESSION_HEADER_RE.match(cleaned)
                    or (
                        skip_mode == "homework"
                        and _RESUMED_AFTER_HOMEWORK_HEADER_RE.match(cleaned)
                    )
                )
            )
            if not can_resume:
                continue
            skip_mode = None

        m_inline = _INLINE_NEXT_LESSON_SECTION_RE.search(line)
        if m_inline and not _REPORT_START_RE.search(line[m_inline.start() :]):
            kept_lines.append(line[: m_inline.start()])
            inline_header = line[m_inline.start() :].strip()
            skip_mode = (
                "homework"
                if _HOMEWORK_SECTION_HEADER_RE.match(inline_header)
                else "next_lesson"
            )
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)


def extract_report_lesson_number(raw_content: str) -> int | None:
    """Read an explicit ``buổi`` number without treating ``bài`` as a lesson."""
    stripped = _strip_trailing_next_lesson_sections(raw_content)
    text = unicodedata.normalize("NFKC", stripped).casefold()
    pattern = re.compile(r"(?:buổi(?:\s+học)?|buổi\s+số)\s*(?:số\s*)?(\d{1,2})")
    for match in pattern.finditer(text):
        context = text[max(0, match.start() - 24) : match.start()]
        if "kế tiếp" in context or "tiếp theo" in context or "buổi sau" in context:
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        same_line_before = text[line_start : match.start()]
        if any(
            marker in same_line_before
            for marker in ("kế tiếp", "tiếp theo", "buổi sau", "chuẩn bị cho buổi")
        ):
            continue
        tail_after_report_kw = re.split(
            r"(?:báo\s+cáo|tổng\s+kết|update)", same_line_before
        )[-1]
        if any(
            marker in tail_after_report_kw
            for marker in ("homework", "bài tập", "tại file")
        ):
            continue
        return int(match.group(1))
    return None


def extract_report_content_lesson_number(raw_content: str) -> int | None:
    """Read a distinct ``Bài số N`` marker used to corroborate a stale header.

    ``Bài`` is not normally interchangeable with a BCNB ``buổi``.  Callers
    must therefore use this only as a second signal beside an explicit target
    row and a one-session-old report header.
    """
    stripped = _strip_trailing_next_lesson_sections(raw_content)
    text = unicodedata.normalize("NFKC", stripped).casefold()
    pattern = re.compile(r"(?:bài\s+số|bài\s+học\s+số|lesson)\s*(\d{1,2})")
    for match in pattern.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        same_line_before = text[line_start : match.start()]
        if any(
            marker in same_line_before
            for marker in (
                "kế tiếp",
                "tiếp theo",
                "buổi sau",
                "homework",
                "bài tập",
                "nhiệm vụ",
                "tại file",
            )
        ):
            continue
        return int(match.group(1))
    return None


def parse_class_date(
    class_date: str,
    *,
    default_year: int,
    timezone_info: TzInfo | None = None,
) -> datetime | None:
    """Parse BCNB's class date (dd/mm, dd/mm/yyyy, or YYYY-MM-DD) without silently accepting a malformed value."""
    value = (class_date or "").strip()
    if not value:
        return None
    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if iso_match:
        return datetime(
            int(iso_match.group(1)),
            int(iso_match.group(2)),
            int(iso_match.group(3)),
            tzinfo=timezone_info,
        )
    parts = value.split("/")
    if len(parts) not in {2, 3}:
        raise ValueError("Ngày học phải có định dạng dd/mm hoặc dd/mm/yyyy.")
    day, month = int(parts[0]), int(parts[1])
    year = int(parts[2]) if len(parts) == 3 else int(default_year)
    return datetime(year, month, day, tzinfo=timezone_info)
