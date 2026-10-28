from collections.abc import Mapping
from datetime import datetime, timedelta
import re
from typing import Any
import unicodedata


AI_ASSIST_MIN_MESSAGE_CHARS = 80
MAX_AI_ASSIST_CANDIDATES = 3


def _date_without_year(day: int, month: int, today: datetime) -> datetime:
    candidate = datetime(today.year, month, day, tzinfo=today.tzinfo)
    if candidate.date() > today.date():
        candidate = datetime(today.year - 1, month, day, tzinfo=today.tzinfo)
    return candidate


def parse_zalo_date_header(date_text: str, today: datetime) -> datetime | None:
    text = date_text.strip().lower()
    if "hôm nay" in text:
        return today
    if "hôm qua" in text:
        return today - timedelta(days=1)
    
    # Định dạng dd/mm/yyyy hoặc dd/mm
    matches = re.findall(r'\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b', text)
    if matches:
        match = matches[-1]
        try:
            day = int(match[0])
            month = int(match[1])
            if match[2]:
                year = int(match[2])
                if year < 100:
                    year += 2000
                return datetime(year, month, day, tzinfo=today.tzinfo)
            return _date_without_year(day, month, today)
        except (TypeError, ValueError):
            return None
    return None

def _extract_content_dates(raw_content: str, today: datetime) -> list[datetime]:
    """Extract dates mentioned in text; these are never the Zalo send timestamp."""
    dates: list[datetime] = []

    def add(value: datetime) -> None:
        if value.date() not in [item.date() for item in dates]:
            dates.append(value)

    for day_text, _separator, month_text, year_text in re.findall(
        r'(?<!\d)(\d{1,2})([/.-])(\d{1,2})\2(\d{2,4})(?!\d)', raw_content
    ):
        try:
            year = int(year_text)
            if year < 100:
                year += 2000
            add(
                datetime(
                    year,
                    int(month_text),
                    int(day_text),
                    tzinfo=today.tzinfo,
                )
            )
        except (TypeError, ValueError):
            continue

    for match in re.finditer(r'ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})', raw_content, re.IGNORECASE):
        try:
            add(_date_without_year(int(match.group(1)), int(match.group(2)), today))
        except (TypeError, ValueError):
            continue

    for separator in ('/', '-', '.'):
        pattern = r'(?<!\d)(\d{1,2})' + re.escape(separator) + r'(\d{1,2})(?!\d)'
        for match in re.finditer(pattern, raw_content):
            day_text, month_text = match.groups()
            try:
                day, month = int(day_text), int(month_text)
                if not 1 <= day <= 31 or not 1 <= month <= 12:
                    continue
                context_before = raw_content[max(0, match.start() - 15):match.start()].lower()
                if "ngày" in context_before or "nộp" in context_before or day_text.startswith("0") or month_text.startswith("0"):
                    add(_date_without_year(day, month, today))
            except (TypeError, ValueError):
                continue
    return dates


def classify_report_content(
    raw_content: str,
    group_name: str,
    today: datetime,
) -> dict[str, Any]:
    """Classify message content without claiming its mentioned dates are send dates."""
    text = unicodedata.normalize('NFC', raw_content).lower().replace('\xa0', ' ')
    score = 0.0
    matched_signals: list[str] = []

    greetings = ["chào ba mẹ", "chào bố mẹ", "chào phụ huynh", "chào quý phụ huynh", "kính gửi phụ huynh", "kính gửi quý phụ huynh", "thân gửi phụ huynh"]
    greeting = next((value for value in greetings if value in text), None)
    if greeting:
        score += 1.0
        matched_signals.append(f"greeting:{greeting}")

    strong_headers = ["nội dung", "nhận xét", "mã lớp: vt", "bài tập về nhà", "btvn", "nhiệm vụ về nhà", "nhiệm vụ khi về nhà"]
    for header in strong_headers:
        if header in text:
            score += 2.0
            matched_signals.append(f"strong_header:{header}")

    substance_headers = ("nội dung", "nhận xét", "bài tập về nhà", "btvn", "nhiệm vụ về nhà", "nhiệm vụ khi về nhà")
    has_substance_header = any(header in text for header in substance_headers)

    weak_headers = ["tình hình lớp", "sĩ số", "vắng", "buổi số", "buổi học"]
    for header in weak_headers:
        if header in text:
            score += 1.0
            matched_signals.append(f"weak_header:{header}")

    report_keywords = ["báo cáo lớp", "báo cáo buổi học", "tổng kết buổi", "tổng kết lớp", "update tình hình", "update lớp", "update sau buổi"]
    report_keyword = next((value for value in report_keywords if value in text), None)
    if report_keyword:
        score += 3.0
        matched_signals.append(f"report_keyword:{report_keyword}")

    group_name_upper = group_name.upper()
    parts = [p.strip().lower() for p in group_name_upper.split('-') if len(p.strip()) >= 3]
    for part in parts:
        if part == "mindx":
            continue
        if part in text:
            score += 1.0
            matched_signals.append(f"group_token:{part}")
            break

    if "điểm danh" in text and "nội dung" not in text and "nhận xét" not in text:
        score -= 2.0
        matched_signals.append("penalty:attendance_only")

    admin_notice_markers = (
        "quản lí lớp học",
        "quản lý lớp học",
        "thông báo khai giảng",
    )
    is_admin_notice = any(marker in text for marker in admin_notice_markers)
    if is_admin_notice:
        score -= 6.0
        matched_signals.append("penalty:class_manager_or_opening_notice")

    has_report_substance = has_substance_header or bool(report_keyword)

    return {
        "is_report": score >= 4.0 and has_report_substance and not is_admin_notice,
        "score": score,
        "content_dates": _extract_content_dates(raw_content, today),
        "matched_signals": matched_signals,
    }


def is_eligible_for_ai_assist(
    raw_content: str,
    classification: Mapping[str, Any],
) -> bool:
    """Return True if a message that failed rule-based classification can be checked by AI."""
    if len(str(raw_content or "").strip()) < AI_ASSIST_MIN_MESSAGE_CHARS:
        return False
    if bool(classification.get("is_report")):
        return False
    signals = {str(item) for item in (classification.get("matched_signals") or ())}
    if "penalty:class_manager_or_opening_notice" in signals:
        return False
    if "penalty:attendance_only" in signals:
        return False
    return True


def check_is_report_and_extract_date(
    raw_content: str,
    group_name: str,
    today: datetime,
) -> tuple[bool, datetime | None]:
    """Legacy compatibility wrapper; callers must not treat the result as a send date."""
    classification = classify_report_content(raw_content, group_name, today)
    content_dates = classification["content_dates"]
    return bool(classification["is_report"]), (content_dates[0] if content_dates else None)
