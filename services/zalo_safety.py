"""Pure identity checks used before any Zalo-derived external action."""

from datetime import datetime, tzinfo as TzInfo
import re
import unicodedata


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
