from __future__ import annotations

from datetime import datetime
import hashlib
import os
from typing import Any


SUBMITTED_VERIFIED = "SUBMITTED_VERIFIED"
SUBMITTED_LATE_VERIFIED = "SUBMITTED_LATE_VERIFIED"
NOT_FOUND_VERIFIED = "NOT_FOUND_VERIFIED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

SUBMITTED_STATUS = "✅ Đã nộp – Có minh chứng"
SUBMITTED_LATE_STATUS = "🟡 Đã nộp trễ – Có minh chứng"
NOT_FOUND_STATUS = "❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng"
INSUFFICIENT_STATUS = "⚠️ Không đủ bằng chứng – Cần kiểm tra thủ công"


def build_image_evidence(public_path: str, file_path: str) -> dict[str, str] | None:
    if not public_path or not os.path.isfile(file_path):
        return None
    digest = hashlib.sha256()
    with open(file_path, "rb") as evidence_file:
        for chunk in iter(lambda: evidence_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": public_path, "sha256": digest.hexdigest().upper()}


def _iso_date(value: datetime | None) -> str | None:
    return value.date().isoformat() if value is not None else None


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
    images: dict[str, dict[str, str] | None],
    report_found: bool,
    report_detail: str | None = None,
    submitted_for_bcnb: bool = False,
    extra_reasons: list[str] | None = None,
    warnings: list[str] | None = None,
    verified_report_state: str = SUBMITTED_VERIFIED,
) -> dict[str, Any]:
    reasons = list(extra_reasons or [])
    if not actual_group_name:
        reasons.append("Không xác minh được tên nhóm Zalo thực tế.")
    if messages_checked <= 0:
        reasons.append("Không đọc được tin nhắn Zalo.")

    evidence_state = INSUFFICIENT_EVIDENCE
    status = INSUFFICIENT_STATUS
    if report_found:
        if images.get("report") is None:
            reasons.append("Không chụp được ảnh tin nhắn báo cáo.")
        if not reasons:
            evidence_state = verified_report_state
            status = (
                SUBMITTED_LATE_STATUS
                if verified_report_state == SUBMITTED_LATE_VERIFIED
                else SUBMITTED_STATUS
            )
    else:
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
        "scanned_at": scanned_at.isoformat(timespec="seconds"),
        "messages_checked": messages_checked,
        "oldest_message_date": _iso_date(oldest_message_date),
        "newest_message_date": _iso_date(newest_message_date),
        "required_window_start": required_window_start.date().isoformat(),
        "required_window_end": required_window_end.date().isoformat(),
        "classifier_result": classifier_result,
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
        "status_detail": report_detail or ("; ".join(reasons) if reasons else ""),
        "evidence_state": evidence_state,
        "submitted_for_bcnb": bool(
            evidence_state == SUBMITTED_VERIFIED and submitted_for_bcnb
        ),
        "screenshot_path": available_image,
        "evidence": evidence,
        "timestamp": scanned_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
