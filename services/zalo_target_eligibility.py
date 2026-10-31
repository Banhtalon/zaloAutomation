"""Time-aware eligibility for BCNB-backed Zalo scan targets.

The BCNB period remains the source of truth for a target's week/year.  This
module only decides whether a target in BCNB's *latest* week is ready to be
checked at the current business time.  In particular, a class later today is
not a missing report yet.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
import re
from typing import Any

from core.time_utils import BUSINESS_TIMEZONE, now_business, to_business
from services.report_status import normalize_class_code


READY_PREVIOUS_WEEK = "READY_PREVIOUS_WEEK"
READY_PAST_CLASS_DATE = "READY_PAST_CLASS_DATE"
READY_CURRENT_DAY_AFTER_CLASS_END = "READY_CURRENT_DAY_AFTER_CLASS_END"
DEFERRED_INVALID_TARGET = "DEFERRED_INVALID_TARGET"
DEFERRED_FUTURE_CLASS_DATE = "DEFERRED_FUTURE_CLASS_DATE"
DEFERRED_SAME_DAY_BEFORE_CLASS_END = "DEFERRED_SAME_DAY_BEFORE_CLASS_END"
DEFERRED_SAME_DAY_TIME_UNKNOWN = "DEFERRED_SAME_DAY_TIME_UNKNOWN"


_TIME_RANGE_RE = re.compile(
    r"(?<!\d)"
    r"(?P<start_hour>[01]?\d|2[0-3])\s*(?:h|:)\s*(?P<start_min>[0-5]\d)?"
    r"\s*[-\u2013\u2014]\s*"
    r"(?P<end_hour>[01]?\d|2[0-3])\s*(?:h|:)\s*(?P<end_min>[0-5]\d)?"
    r"(?!\d)",
    flags=re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")
_VN_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?!\d)")


@dataclass(frozen=True)
class TargetEligibility:
    eligible: bool
    reason: str
    eligible_after: str | None = None


def parse_group_end_time(group_name: object) -> time | None:
    """Read the end of the class time range embedded in a Zalo group title.

    Known group-title forms include ``18H-19H30``, ``(14H-16H)`` and
    ``18:00-20:00``.  Unknown or multi-range forms deliberately return ``None``
    so a class scheduled today is deferred rather than falsely reported as
    missing.
    """

    matches = list(_TIME_RANGE_RE.finditer(str(group_name or "")))
    if len(matches) != 1:
        return None
    match = matches[0]
    try:
        return time(int(match.group("end_hour")), int(match.group("end_min") or 0))
    except ValueError:
        return None


def _parse_target_day(value: object) -> date | None:
    raw = str(value or "").strip()
    iso_match = _ISO_DATE_RE.fullmatch(raw)
    if iso_match:
        year, month, day = (int(item) for item in iso_match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    matches = _VN_DATE_RE.findall(raw)
    if not matches:
        return None
    day_text, month_text, year_text = matches[-1]
    year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
    try:
        return date(year, int(month_text), int(day_text))
    except ValueError:
        return None


def _period_from_mapping(value: Mapping[str, Any] | tuple[int, int] | None) -> tuple[int, int] | None:
    raw_week: Any
    raw_year: Any
    if isinstance(value, tuple) and len(value) == 2:
        raw_week, raw_year = value
    elif isinstance(value, Mapping):
        raw_week, raw_year = value.get("week"), value.get("year")
    else:
        return None
    try:
        week, year = int(raw_week), int(raw_year)
    except (TypeError, ValueError):
        return None
    if not 1 <= week <= 53 or not 2000 < year <= 2100:
        return None
    return week, year


def scan_target_identity(target: Mapping[str, Any]) -> tuple[int, int, str, str, str] | None:
    """Return the exact BCNB row identity carried by a scan target."""

    period = _period_from_mapping(target)
    class_code = normalize_class_code(target.get("class_code"))
    lesson = str(target.get("lesson") or "").strip()
    target_day = _parse_target_day(target.get("class_date_iso") or target.get("class_date"))
    if period is None or not class_code or not lesson or target_day is None:
        return None
    return (*period, class_code, lesson, target_day.isoformat())


def evaluate_scan_target(
    target: Mapping[str, Any],
    *,
    latest_week: Mapping[str, Any] | tuple[int, int] | None,
    now: datetime | None = None,
) -> TargetEligibility:
    """Decide whether a target is ready without deriving its BCNB week.

    Previous-week targets are ready regardless of their calendar date.  For
    targets in the current BCNB week, only a completed past day or a same-day
    class whose stated end time has passed may be scanned.
    """

    latest_period = _period_from_mapping(latest_week)
    target_period = _period_from_mapping(target)
    target_day = _parse_target_day(target.get("class_date_iso") or target.get("class_date"))
    if latest_period is None or target_period is None or target_day is None:
        return TargetEligibility(False, DEFERRED_INVALID_TARGET)
    if target_period != latest_period:
        return TargetEligibility(True, READY_PREVIOUS_WEEK)

    current = to_business(now or now_business())
    if target_day < current.date():
        return TargetEligibility(True, READY_PAST_CLASS_DATE)
    if target_day > current.date():
        return TargetEligibility(False, DEFERRED_FUTURE_CLASS_DATE)

    end_time = parse_group_end_time(target.get("group_name"))
    if end_time is None:
        return TargetEligibility(False, DEFERRED_SAME_DAY_TIME_UNKNOWN)
    end_at = datetime.combine(target_day, end_time, tzinfo=BUSINESS_TIMEZONE)
    if current >= end_at:
        return TargetEligibility(True, READY_CURRENT_DAY_AFTER_CLASS_END)
    return TargetEligibility(
        False,
        DEFERRED_SAME_DAY_BEFORE_CLASS_END,
        eligible_after=end_at.isoformat(timespec="minutes"),
    )


def split_scan_targets_with_deduped(
    targets: Iterable[Mapping[str, Any]],
    *,
    latest_week: Mapping[str, Any] | tuple[int, int] | None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split candidate targets into scan-ready, deferred, and deduplicated rows."""

    ready: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str, str, str] | tuple[str, str, str]] = set()
    for raw_target in targets:
        if not isinstance(raw_target, Mapping):
            continue
        target = dict(raw_target)
        identity = scan_target_identity(target)
        fallback_identity = (
            "raw",
            str(target.get("group_name") or ""),
            str(target.get("class_date") or ""),
        )
        dedupe_key = identity or fallback_identity
        if dedupe_key in seen:
            dup_item = dict(target)
            dup_item.setdefault("reason", "DUPLICATE_TARGET")
            deduped.append(dup_item)
            continue
        seen.add(dedupe_key)

        eligibility = evaluate_scan_target(target, latest_week=latest_week, now=now)
        if eligibility.eligible:
            target.pop("deferred_reason", None)
            target.pop("deferred_reason_detail", None)
            target.pop("eligible_after", None)
            ready.append(target)
            continue
        target["deferred_reason"] = eligibility.reason
        if eligibility.reason == DEFERRED_SAME_DAY_TIME_UNKNOWN:
            target["deferred_reason_detail"] = "cần xác nhận giờ học"
        else:
            target.pop("deferred_reason_detail", None)
        target["eligible_after"] = eligibility.eligible_after
        deferred.append(target)
    return ready, deferred, deduped


def split_scan_targets_for_execution(
    targets: Iterable[Mapping[str, Any]],
    *,
    latest_week: Mapping[str, Any] | tuple[int, int] | None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split candidate targets into scan-ready and explicitly deferred rows."""

    ready, deferred, _ = split_scan_targets_with_deduped(
        targets,
        latest_week=latest_week,
        now=now,
    )
    return ready, deferred
