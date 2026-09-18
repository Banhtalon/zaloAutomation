"""Canonical UTC/business-clock helpers for Zalo and BCNB decisions.

All persisted timestamps should be produced with :func:`utc_iso`.  Business
rules (week/day selection and user-facing timestamps) must first convert
through :func:`to_business`, so the result is independent of the host OS
timezone.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE_NAME = "Asia/Ho_Chi_Minh"
BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)
UTC_TIMEZONE = timezone.utc


def now_utc() -> datetime:
    """Return the current instant as an aware UTC datetime."""
    return datetime.now(UTC_TIMEZONE)


def now_business() -> datetime:
    """Return the current business time as an aware datetime."""
    return datetime.now(BUSINESS_TIMEZONE)


def to_business(value: datetime | None = None) -> datetime:
    """Normalize an instant to the business timezone.

    Legacy naive values are interpreted as business-local values at the
    boundary.  New persisted values must be aware and should use UTC.
    """
    current = value or now_business()
    if current.tzinfo is None:
        return current.replace(tzinfo=BUSINESS_TIMEZONE)
    return current.astimezone(BUSINESS_TIMEZONE)


def to_utc(value: datetime | None = None) -> datetime:
    """Normalize an instant to an aware UTC datetime."""
    current = value or now_utc()
    if current.tzinfo is None:
        # Historical DB values were local business time.  Keep this explicit
        # rather than allowing the host machine timezone to reinterpret them.
        current = current.replace(tzinfo=BUSINESS_TIMEZONE)
    return current.astimezone(UTC_TIMEZONE)


def utc_iso(value: datetime | None = None, *, timespec: str = "seconds") -> str:
    """Return a canonical, timezone-aware UTC ISO-8601 string."""
    return to_utc(value).isoformat(timespec=timespec)


def parse_timestamp(value: object, *, naive_timezone: ZoneInfo = BUSINESS_TIMEZONE) -> datetime | None:
    """Parse an ISO timestamp and return an aware UTC datetime.

    ``Z`` and explicit offsets are accepted.  A naive legacy value is
    interpreted in ``naive_timezone`` so parsing never depends on the host
    process timezone.
    """
    if value is None or value == "":
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_timezone)
    return parsed.astimezone(UTC_TIMEZONE)


def previous_business_week(value: datetime | None = None) -> tuple[int, int]:
    """Return the ISO week/year immediately before the current business week."""
    current = to_business(value)
    previous_week_day = current - timedelta(days=current.weekday() + 7)
    iso = previous_week_day.isocalendar()
    return int(iso.week), int(iso.year)


def previous_iso_week(week: int, year: int) -> tuple[int, int]:
    """Return the ISO week immediately before an explicit BCNB week.

    Discovery receives the current week from BCNB itself.  It must not
    recompute the previous week from the workstation clock because the BCNB
    calendar can lag or lead the local calendar during a transition.
    """
    if not isinstance(week, int) or not 1 <= week <= 53:
        raise ValueError("Tuần ISO không hợp lệ.")
    if not isinstance(year, int) or not 2000 < year <= 2100:
        raise ValueError("Năm ISO không hợp lệ.")
    try:
        current_monday = date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError("Cặp tuần/năm ISO không hợp lệ.") from exc
    previous = current_monday - timedelta(days=7)
    iso = previous.isocalendar()
    return int(iso.week), int(iso.year)


def business_date(value: datetime | None = None) -> date:
    """Return the business-local calendar date for an instant."""
    return to_business(value).date()


def business_day_start(value: datetime | None = None) -> datetime:
    """Convert to business time before truncating to the local day boundary."""
    current = to_business(value)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)
