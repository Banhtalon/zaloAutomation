"""Pure identity checks used before any Zalo-derived external action."""

from datetime import datetime, tzinfo as TzInfo
import re
import unicodedata


REPORT_DATE_LOOKBACK_DAYS = 10


def _identity_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value or "").upper()
    return tuple(re.findall(r"[A-Z0-9]+", normalized))


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


def extract_report_lesson_number(raw_content: str) -> int | None:
    """Read an explicit ``buổi`` number without treating ``bài`` as a lesson."""
    text = unicodedata.normalize("NFKC", raw_content or "").casefold()
    pattern = re.compile(r"(?:buổi(?:\s+học)?|buổi\s+số)\s*(?:số\s*)?(\d{1,2})")
    for match in pattern.finditer(text):
        context = text[max(0, match.start() - 24) : match.start()]
        if "kế tiếp" in context or "tiếp theo" in context:
            continue
        return int(match.group(1))
    return None


def extract_report_content_lesson_number(raw_content: str) -> int | None:
    """Read a distinct ``Bài số N`` marker used to corroborate a stale header.

    ``Bài`` is not normally interchangeable with a BCNB ``buổi``.  Callers
    must therefore use this only as a second signal beside an explicit target
    row and a one-session-old report header.
    """
    text = unicodedata.normalize("NFKC", raw_content or "").casefold()
    pattern = re.compile(r"(?:bài\s+số|bài\s+học\s+số|lesson)\s*(\d{1,2})")
    match = pattern.search(text)
    return int(match.group(1)) if match else None


def parse_class_date(
    class_date: str,
    *,
    default_year: int,
    timezone_info: TzInfo | None = None,
) -> datetime | None:
    """Parse BCNB's class date without silently accepting a malformed value."""
    value = (class_date or "").strip()
    if not value:
        return None
    parts = value.split("/")
    if len(parts) not in {2, 3}:
        raise ValueError("Ngày học phải có định dạng dd/mm hoặc dd/mm/yyyy.")
    day, month = int(parts[0]), int(parts[1])
    year = int(parts[2]) if len(parts) == 3 else int(default_year)
    return datetime(year, month, day, tzinfo=timezone_info)
