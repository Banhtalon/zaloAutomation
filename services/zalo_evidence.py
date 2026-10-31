from __future__ import annotations

from datetime import datetime

from core.time_utils import to_business, utc_iso
import hashlib
import mimetypes
import os
from typing import Any


SUBMITTED_VERIFIED = "SUBMITTED_VERIFIED"
SUBMITTED_LATE_VERIFIED = "SUBMITTED_LATE_VERIFIED"
NOT_FOUND_VERIFIED = "NOT_FOUND_VERIFIED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
LESSON_14_EXEMPTION = "LESSON_14_EXEMPTION"

SUBMITTED_STATUS = "✅ Đã nộp – Có minh chứng"
SUBMITTED_LATE_STATUS = "🟡 Đã nộp trễ – Có minh chứng"
NOT_FOUND_STATUS = "❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng"
INSUFFICIENT_STATUS = "⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công"
AI_SUGGESTED_REPORT_DECISION = "AI_SUGGESTED_REPORT_NEEDS_REVIEW"
AI_SUGGESTED_REPORT_STATUS = "⚠️ AI gợi ý có thể là báo cáo – Cần kiểm tra thủ công"
LESSON_14_STATUS = "✅ Miễn báo cáo (Buổi 14)"


def get_evidence_reference(
    result: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Return the selected evidence path and its stored digest."""
    if not result:
        return None, None
    evidence_path = str(result.get("screenshot_path") or "").strip()
    if not evidence_path:
        return None, None
    images = result.get("evidence", {}).get("images", {}) or {}
    for image in images.values():
        if image and image.get("path") == evidence_path:
            digest = str(image.get("sha256") or "").strip().upper()
            return evidence_path, digest or None
    return evidence_path, None


def apply_lesson_14_exemption(
    result: dict[str, Any] | None,
    *,
    class_code: str,
    applied_at: datetime,
    lesson: Any = 14,
) -> dict[str, Any]:
    """Apply the lesson-14 rule while preserving the source evidence state."""
    from services.report_status import is_lesson_14

    if not is_lesson_14(lesson):
        raise ValueError("Chỉ áp dụng miễn báo cáo cho Buổi 14.")
    if result is None:
        result = {
            "group": f"{class_code} (Buổi 14)",
            "zalo_group_name": "",
            "person": "Giáo viên",
            "screenshot_path": "",
            "timestamp": utc_iso(applied_at),
            "evidence": {
                "actual_group_name": None,
                "scanned_at": utc_iso(applied_at),
                "messages_checked": 0,
                "oldest_message_date": None,
                "newest_message_date": None,
                "required_window_start": None,
                "required_window_end": None,
                "classifier_result": {
                    "engine": "business_rule_v1",
                    "decision": LESSON_14_EXEMPTION,
                    "report_found": False,
                    "evaluated_messages": 0,
                },
                "images": {
                    "report": None,
                    "latest": None,
                    "oldest": None,
                    "oldest_message": None,
                },
                "validation_errors": [],
                "warnings": [],
            },
        }

    existing_rule = (
        result.get("evidence", {}).get("business_rule")
        if isinstance(result.get("evidence"), dict)
        else None
    )
    source_state = (
        existing_rule.get("source_evidence_state")
        if isinstance(existing_rule, dict) and "source_evidence_state" in existing_rule
        else result.get("evidence_state")
    )
    evidence = result.setdefault("evidence", {})
    evidence["business_rule"] = {
        "decision": LESSON_14_EXEMPTION,
        "class_code": class_code,
        "lesson": 14,
        "applied_at": utc_iso(applied_at),
        "source_evidence_state": source_state,
    }
    result.update(
        {
            "status": LESSON_14_STATUS,
            "status_detail": "Buổi 14 – miễn báo cáo theo quy tắc nghiệp vụ.",
            "evidence_state": LESSON_14_EXEMPTION,
            "submitted_for_bcnb": True,
        }
    )
    return result


def build_image_evidence(
    public_path: str,
    file_path: str,
    *,
    capture_type: str = "zalo_chat",
) -> dict[str, Any] | None:
    if not public_path or not os.path.isfile(file_path):
        return None
    digest = hashlib.sha256()
    with open(file_path, "rb") as evidence_file:
        for chunk in iter(lambda: evidence_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": public_path,
        "sha256": digest.hexdigest().upper(),
        "size_bytes": os.path.getsize(file_path),
        "mime_type": mimetypes.guess_type(file_path)[0] or "image/png",
        "created_at": utc_iso(),
        "capture_type": capture_type,
    }


def _iso_date(value: datetime | None) -> str | None:
    return value.date().isoformat() if value is not None else None


def _has_unresolved_date_candidate(classifier_result: dict[str, Any]) -> bool:
    """Prevent an ambiguous report from being presented as a verified absence."""
    return any(
        candidate.get("decision")
        in {
            "BEFORE_CLASS_DATE",
            "FUTURE_UI_SENT_AT",
            "TARGET_SESSION_AMBIGUOUS",
            AI_SUGGESTED_REPORT_DECISION,
        }
        for candidate in classifier_result.get("candidates", [])
    )


def build_scan_result(
    *,
    group: str,
    person: str,
    actual_group_name: str | None,
    scanned_at: datetime,
    messages_checked: int,
    classifier_result: dict[str, Any],
    required_window_start: datetime,
    required_window_end: datetime,
    oldest_message_date: datetime | None,
    newest_message_date: datetime | None,
    images: dict[str, dict[str, Any] | None],
    report_found: bool,
    report_detail: str | None = None,
    submitted_for_bcnb: bool = False,
    extra_reasons: list[str] | None = None,
    warnings: list[str] | None = None,
    verified_report_state: str = SUBMITTED_VERIFIED,
    date_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reasons = list(extra_reasons or [])
    if not actual_group_name:
        reasons.append("Không xác minh được tên nhóm Zalo thực tế.")
    if messages_checked <= 0:
        reasons.append("Không đọc được tin nhắn Zalo.")

    is_ai_suggested = (
        classifier_result.get("decision") == AI_SUGGESTED_REPORT_DECISION
        or any(
            candidate.get("decision") == AI_SUGGESTED_REPORT_DECISION
            for candidate in classifier_result.get("candidates", [])
        )
    )
    evidence_state = INSUFFICIENT_EVIDENCE
    status = INSUFFICIENT_STATUS
    if is_ai_suggested:
        report_found = False
        submitted_for_bcnb = False
        if report_detail and report_detail not in reasons:
            reasons.insert(0, report_detail)
        if images.get("report") is None:
            reasons.append(
                "Không chụp được ảnh tin nhắn do AI gợi ý; cần kiểm tra thủ công trên Zalo."
            )
        if not reasons:
            reasons.append(
                "AI gợi ý tin nhắn có thể là báo cáo buổi học; bắt buộc duyệt tay trước khi tích BCNB."
            )
        if actual_group_name and messages_checked > 0 and images.get("report") is not None:
            status = AI_SUGGESTED_REPORT_STATUS
    elif report_found:
        if images.get("report") is None:
            reasons.append(
                "Không chụp được ảnh tin nhắn báo cáo; đã nhận diện report nhưng không tạo được evidence."
            )
        if not reasons:
            evidence_state = verified_report_state
            status = (
                SUBMITTED_LATE_STATUS
                if verified_report_state == SUBMITTED_LATE_VERIFIED
                else SUBMITTED_STATUS
            )
    else:
        if _has_unresolved_date_candidate(classifier_result):
            reasons.append(
                "Có ứng viên báo cáo nhưng chưa xác định được buổi học tham chiếu; cần kiểm tra thủ công."
            )
        if oldest_message_date is None or newest_message_date is None:
            reasons.append("Không đọc được mốc ngày cũ nhất hoặc mới nhất.")
        elif oldest_message_date.date() > newest_message_date.date():
            reasons.append("Thứ tự mốc ngày Zalo không hợp lệ.")
        elif oldest_message_date.date() > required_window_start.date():
            reasons.append("Chưa tải đủ khoảng thời gian kiểm tra tối thiểu.")
        if images.get("latest") is None:
            reasons.append("Không chụp được ảnh tin nhắn mới nhất.")
        oldest_image = images.get("oldest")
        if oldest_image is None:
            reasons.append("Không chụp được ảnh mốc tin nhắn cũ nhất.")
        elif not oldest_image.get("ui_date_text"):
            reasons.append(
                "Ảnh mốc cũ nhất không xác nhận được separator ngày của giao diện Zalo."
            )
        if not reasons:
            evidence_state = NOT_FOUND_VERIFIED
            status = NOT_FOUND_STATUS

    evidence = {
        "actual_group_name": actual_group_name,
        "scanned_at": utc_iso(scanned_at),
        "messages_checked": messages_checked,
        "oldest_message_date": _iso_date(oldest_message_date),
        "newest_message_date": _iso_date(newest_message_date),
        "required_window_start": required_window_start.date().isoformat(),
        "required_window_end": required_window_end.date().isoformat(),
        "classifier_result": classifier_result,
        "date_context": dict(date_context or {}),
        "images": images,
        "validation_errors": reasons,
        "warnings": list(warnings or []),
    }
    available_image = next(
        (
            item["path"]
            for key in ("report", "latest", "oldest", "oldest_message")
            if (item := images.get(key)) is not None
        ),
        "",
    )
    return {
        "group": group,
        "zalo_group_name": actual_group_name or "",
        "person": person,
        "status": status,
        "status_detail": "; ".join(reasons) if reasons else (report_detail or ""),
        "evidence_state": evidence_state,
        "submitted_for_bcnb": bool(
            evidence_state in {SUBMITTED_VERIFIED, SUBMITTED_LATE_VERIFIED}
            and submitted_for_bcnb
        ),
        "screenshot_path": available_image,
        "evidence": evidence,
        "timestamp": to_business(scanned_at).strftime("%Y-%m-%d %H:%M:%S ICT"),
    }
