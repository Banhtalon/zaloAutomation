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

from services.report_status import normalize_class_code
from services.zalo_safety import parse_class_date


TARGET_EXACT = "TARGET_EXACT"
TARGET_DATE_TYPO_CORRECTED = "TARGET_DATE_TYPO_CORRECTED"
TARGET_SAME_DAY_NO_DATE = "TARGET_SAME_DAY_NO_DATE"
TARGET_SAME_DAY_WRONG_DATE = "TARGET_SAME_DAY_WRONG_DATE"
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
    same_day_target_ambiguous: bool = False

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

    class_code = normalize_class_code(value.get("class_code"))
    lesson = str(value.get("lesson") or "").strip()
    class_date_iso_text = str(value.get("class_date_iso") or "").strip()
    class_date_raw_text = str(value.get("class_date") or "").strip()
    class_date_text = class_date_iso_text or class_date_raw_text
    if not class_code or not lesson or not class_date_text:
        raise ValueError("Target BCNB thiếu mã lớp, buổi hoặc ngày học.")
    class_date = parse_class_date(
        class_date_text,
        default_year=default_year,
        timezone_info=timezone_info,
    )
    if class_date is None and class_date_iso_text and class_date_raw_text:
        class_date = parse_class_date(
            class_date_raw_text,
            default_year=default_year,
            timezone_info=timezone_info,
        )
    if class_date is None:
        raise ValueError("Ngày học target BCNB không hợp lệ.")
    same_day_count = 0
    try:
        same_day_count = int(value.get("same_day_target_count") or 0)
    except (TypeError, ValueError):
        same_day_count = 0
    same_day_ambiguous = bool(value.get("same_day_target_ambiguous")) or (
        same_day_count > 1
    )
    return BcnbScanTarget(
        week=week,
        year=year,
        class_code=class_code,
        lesson=lesson,
        class_date=class_date,
        zalo_deadline=str(value.get("zalo_deadline") or "").strip(),
        same_day_target_ambiguous=same_day_ambiguous,
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
    ui_sent_at: datetime | None,
    claimed_lesson_date: datetime | None,
    claimed_lesson: int | None,
    content_lesson: int | None,
    same_day_target_ambiguous: bool = False,
) -> TargetMatch:
    """Resolve a candidate without letting a stale teacher header move its week.

    Exact BCNB date or explicit session number wins.  A template-error repair
    is deliberately narrow: the message must identify the BCNB lesson through
    ``Bài số N`` while its header is exactly one normal weekly session behind.
    Under D2, a report sent on the exact BCNB class date with no conflicting
    lesson number and no same-day target ambiguity matches with a QC NOTE issue
    when its content omits date/lesson or writes a wrong date.
    Anything else remains manual review instead of being silently retargeted.
    """
    if ui_sent_at is None:
        return TargetMatch(
            decision=TARGET_SESSION_AMBIGUOUS,
            matches=False,
            note=(
                "Có báo cáo nhưng không đọc được ngày gửi từ giao diện Zalo "
                f"cho {_target_label(target)}; chưa đủ căn cứ để tự ghép."
            ),
        )
    target_day = target.class_date.date()
    sent_day = ui_sent_at.date()
    expected_lesson = _lesson_number(target.lesson)
    claimed_day = claimed_lesson_date.date() if claimed_lesson_date is not None else None
    is_same_day_ambiguous = bool(
        target.same_day_target_ambiguous or same_day_target_ambiguous
    )

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
    # mistaken date in the copied report template.  When the report was sent on
    # the exact BCNB class date without same-day target ambiguity, D2 classifies
    # a wrong/missing content date as a non-failing QC note.
    if expected_lesson is not None and claimed_lesson == expected_lesson:
        if (
            sent_day == target_day
            and not is_same_day_ambiguous
            and (content_lesson is None or content_lesson == expected_lesson)
        ):
            if claimed_lesson_date is not None:
                return TargetMatch(
                    decision=TARGET_SAME_DAY_WRONG_DATE,
                    matches=True,
                    corrected_teacher_date=True,
                    note=f"[Zalo] Báo cáo ghi sai ngày ({claimed_lesson_date.strftime('%d/%m')})",
                )
            return TargetMatch(
                decision=TARGET_SAME_DAY_NO_DATE,
                matches=True,
                corrected_teacher_date=False,
                note="[Zalo] Báo cáo không ghi ngày/buổi",
            )
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

    # D2 fallback: report sent on the exact BCNB class date with no same-day
    # target ambiguity and no conflicting lesson number in content.
    has_conflicting_lesson = bool(
        claimed_lesson is not None
        or (
            expected_lesson is not None
            and content_lesson is not None
            and content_lesson != expected_lesson
        )
    )
    if sent_day == target_day and not is_same_day_ambiguous and not has_conflicting_lesson:
        if claimed_day is None:
            return TargetMatch(
                decision=TARGET_SAME_DAY_NO_DATE,
                matches=True,
                corrected_teacher_date=False,
                note="[Zalo] Báo cáo không ghi ngày/buổi",
            )
        if claimed_lesson_date is not None:
            return TargetMatch(
                decision=TARGET_SAME_DAY_WRONG_DATE,
                matches=True,
                corrected_teacher_date=True,
                note=f"[Zalo] Báo cáo ghi sai ngày ({claimed_lesson_date.strftime('%d/%m')})",
            )

    if sent_day == target_day and is_same_day_ambiguous:
        return TargetMatch(
            decision=TARGET_SESSION_AMBIGUOUS,
            matches=False,
            note=(
                "Có báo cáo gửi đúng ngày học nhưng nhóm có nhiều buổi học BCNB trùng ngày "
                f"cho {_target_label(target)}; chưa đủ căn cứ để tự ghép."
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
