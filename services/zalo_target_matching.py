"""Match a Zalo report to the authoritative BCNB scan target.

The week/year in a scan target come from BCNB.  Dates mentioned by a teacher
are evidence about the report, never a replacement source for the BCNB
business period.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from services.zalo_safety import parse_class_date


TARGET_EXACT = "TARGET_EXACT"
TARGET_DATE_TYPO_CORRECTED = "TARGET_DATE_TYPO_CORRECTED"
TARGET_BEFORE_CLASS_DATE = "TARGET_BEFORE_CLASS_DATE"
TARGET_SESSION_AMBIGUOUS = "TARGET_SESSION_AMBIGUOUS"


@dataclass(frozen=True)
class BcnbScanTarget:
    """Immutable, BCNB-sourced target for one class row."""

    week: int
    year: int
    class_code: str
    lesson: str
    class_date: datetime
    zalo_deadline: str

    @property
    def class_date_iso(self) -> str:
        return self.class_date.date().isoformat()


@dataclass(frozen=True)
class TargetMatch:
    """Resolution of a report candidate against one BCNB target row."""

    decision: str
    matches: bool
    corrected_teacher_date: bool = False
    note: str | None = None


def parse_bcnb_scan_target(
    value: Mapping[str, Any] | None,
    *,
    default_year: int,
    timezone_info,
) -> BcnbScanTarget | None:
    """Validate a persisted target without deriving its period from a date."""
    if value is None:
        return None
    try:
        week = int(value["week"])
        year = int(value["year"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Target BCNB thiếu tuần hoặc năm.") from exc
    if not 1 <= week <= 53 or not 2000 < year <= 2100:
        raise ValueError("Tuần/năm target BCNB không hợp lệ.")

    class_code = str(value.get("class_code") or "").strip()
    lesson = str(value.get("lesson") or "").strip()
    class_date_text = str(value.get("class_date") or "").strip()
    if not class_code or not lesson or not class_date_text:
        raise ValueError("Target BCNB thiếu mã lớp, buổi hoặc ngày học.")
    class_date = parse_class_date(
        class_date_text,
        default_year=default_year,
        timezone_info=timezone_info,
    )
    if class_date is None:
        raise ValueError("Ngày học target BCNB không hợp lệ.")
    return BcnbScanTarget(
        week=week,
        year=year,
        class_code=class_code,
        lesson=lesson,
        class_date=class_date,
        zalo_deadline=str(value.get("zalo_deadline") or "").strip(),
    )


def _lesson_number(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _target_label(target: BcnbScanTarget) -> str:
    return (
        f"BCNB W{target.week}/{target.year}, ngày "
        f"{target.class_date.strftime('%d/%m')}, buổi {target.lesson}"
    )


def resolve_report_target(
    target: BcnbScanTarget,
    *,
    ui_sent_at: datetime,
    claimed_lesson_date: datetime | None,
    claimed_lesson: int | None,
    content_lesson: int | None,
) -> TargetMatch:
    """Resolve a candidate without letting a stale teacher header move its week.

    Exact BCNB date or explicit session number wins.  A template-error repair
    is deliberately narrow: the message must identify the BCNB lesson through
    ``Bài số N`` while its header is exactly one normal weekly session behind.
    Anything else remains manual review instead of being silently retargeted.
    """
    target_day = target.class_date.date()
    sent_day = ui_sent_at.date()
    expected_lesson = _lesson_number(target.lesson)
    claimed_day = claimed_lesson_date.date() if claimed_lesson_date is not None else None

    if sent_day < target_day:
        return TargetMatch(
            decision=TARGET_BEFORE_CLASS_DATE,
            matches=False,
            note=(
                f"Có báo cáo gửi {ui_sent_at.strftime('%d/%m')} trước ngày học "
                f"target {_target_label(target)}; không dùng cho dòng này."
            ),
        )

    # An explicit class date is the strongest report-side identifier.  The
    # teacher may still type the lesson number incorrectly, so retain a note
    # rather than rejecting an otherwise exact BCNB date.
    if claimed_day == target_day:
        if claimed_lesson is None or expected_lesson is None or claimed_lesson == expected_lesson:
            return TargetMatch(decision=TARGET_EXACT, matches=True)
        return TargetMatch(
            decision=TARGET_DATE_TYPO_CORRECTED,
            matches=True,
            corrected_teacher_date=True,
            note=(
                "Đã báo cáo nhưng ghi sai buổi "
                f"{claimed_lesson}; đã ghép theo {_target_label(target)}."
            ),
        )

    # An explicit "buổi N" equal to BCNB's row is sufficient to correct a
    # mistaken date in the copied report template.
    if expected_lesson is not None and claimed_lesson == expected_lesson:
        claimed_text = claimed_lesson_date.strftime("%d/%m") if claimed_lesson_date else "không rõ ngày"
        return TargetMatch(
            decision=TARGET_DATE_TYPO_CORRECTED,
            matches=True,
            corrected_teacher_date=True,
            note=(
                f"Đã báo cáo nhưng ghi sai ngày {claimed_text}; "
                f"đã ghép theo {_target_label(target)}."
            ),
        )

    # GA35-style stale headers are only repaired when a separate curriculum
    # marker corroborates the expected BCNB lesson and the header is exactly
    # one weekly session behind.  This prevents an old, genuinely late report
    # from being reused for the next week.
    if (
        expected_lesson is not None
        and content_lesson == expected_lesson
        and claimed_lesson == expected_lesson - 1
        and claimed_day is not None
        and 1 <= (target_day - claimed_day).days <= 8
    ):
        return TargetMatch(
            decision=TARGET_DATE_TYPO_CORRECTED,
            matches=True,
            corrected_teacher_date=True,
            note=(
                "Đã báo cáo nhưng ghi sai ngày/buổi trong mẫu; "
                f"'Bài số {content_lesson}' khớp với {_target_label(target)}."
            ),
        )

    return TargetMatch(
        decision=TARGET_SESSION_AMBIGUOUS,
        matches=False,
        note=(
            "Có báo cáo nhưng ngày/buổi ghi trong nội dung không khớp "
            f"{_target_label(target)}; chưa đủ căn cứ để tự ghép."
        ),
    )
