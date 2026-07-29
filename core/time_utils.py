"""Business-clock helpers for Zalo and BCNB decisions."""

from datetime import datetime
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE_NAME = "Asia/Ho_Chi_Minh"
BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)


def now_business() -> datetime:
    """Return the current business time as an aware datetime."""
    return datetime.now(BUSINESS_TIMEZONE)


def business_day_start(value: datetime | None = None) -> datetime:
    """Convert to business time before truncating to the local day boundary."""
    current = value or now_business()
    if current.tzinfo is None:
        current = current.replace(tzinfo=BUSINESS_TIMEZONE)
    else:
        current = current.astimezone(BUSINESS_TIMEZONE)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)
