from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import os
from typing import Any
import uuid

from classifiers.rule_classifier import (
    check_is_report_and_extract_date,
    parse_zalo_date_header,
)
from logger_utils import log_message
from services.zalo_evidence import build_image_evidence, build_scan_result
from services.zalo_safety import report_delay_days


EVIDENCE_WINDOW_DAYS = 10
MAX_EVIDENCE_SCROLLS = 12


async def _message_date_from_zalo_ui(message, today: datetime) -> datetime | None:
    try:
        date_headers = message.locator(
            'xpath=preceding::div[contains(@class, "chat-date") '
            'or contains(@class, "block-date")]'
        )
        if await date_headers.count() == 0:
            return None
        return parse_zalo_date_header(await date_headers.last.inner_text(), today)
    except Exception:
        return None


async def _capture_zalo_evidence(
    page,
    target_message,
    *,
    job_id: int,
    screenshots_dir: str,
    label: str,
) -> dict[str, str] | None:
    if target_message is None:
        return None
    filename = f"{label}-{uuid.uuid4().hex[:10]}.png"
    file_path = os.path.join(screenshots_dir, filename)
    public_path = f"/static/screenshots/job-{job_id}/{filename}"
    try:
        await target_message.scroll_into_view_if_needed(timeout=2000)
        await asyncio.sleep(0.4)
        await page.screenshot(path=file_path)
        return build_image_evidence(public_path, file_path)
    except Exception as exc:
        log_message(
            f"Không chụp được ảnh minh chứng Zalo ({label}): {exc}",
            level="WARNING",
        )
        return None


def build_empty_evidence_result(
    *,
    group: str,
    person: str,
    actual_group_name: str | None,
    scanned_at: datetime,
    today: datetime,
    reason: str,
) -> dict[str, Any]:
    return build_scan_result(
        group=group,
        person=person,
        actual_group_name=actual_group_name,
        scanned_at=scanned_at,
        messages_checked=0,
        classifier_result={
            "engine": "rule_classifier_v1",
            "decision": "NOT_EVALUATED",
            "report_found": False,
            "evaluated_messages": 0,
        },
        required_window_start=today - timedelta(days=EVIDENCE_WINDOW_DAYS),
        required_window_end=today,
        oldest_message_date=None,
        newest_message_date=None,
        images={"report": None, "latest": None, "oldest": None},
        report_found=False,
        extra_reasons=[reason],
    )


async def scan_open_zalo_chat(
    page,
    *,
    job_id: int,
    screenshots_dir: str,
    group_name: str,
    actual_group_name: str,
    person: str,
    class_date_str: str,
    today: datetime,
    ensure_not_cancelled: Callable[[], Awaitable[None]],
) -> dict[str, Any]:
    scanned_at = datetime.now()
    required_window_start = today - timedelta(days=EVIDENCE_WINDOW_DAYS)

    await page.mouse.move(500, 400)
    for _scroll_idx in range(MAX_EVIDENCE_SCROLLS):
        await ensure_not_cancelled()
        loaded_messages = await page.locator(
            '[data-component="bubble-message"]'
        ).all()
        oldest_loaded_date = (
            await _message_date_from_zalo_ui(loaded_messages[0], today)
            if loaded_messages
            else None
        )
        if (
            oldest_loaded_date is not None
            and oldest_loaded_date.date() <= required_window_start.date()
        ):
            log_message(
                "  -> Đã tải đủ cửa sổ kiểm chứng "
                f"{EVIDENCE_WINDOW_DAYS} ngày trên Zalo."
            )
            break
        await page.mouse.wheel(0, -2000)
        await asyncio.sleep(0.5)

    for button in await page.locator('text="Xem thêm"').all():
        try:
            await button.click(timeout=1000)
        except Exception:
            continue

    messages = await page.locator('[data-component="bubble-message"]').all()
    messages_checked = len(messages)
    oldest_message_date = (
        await _message_date_from_zalo_ui(messages[0], today) if messages else None
    )
    newest_message_date = (
        await _message_date_from_zalo_ui(messages[-1], today) if messages else None
    )

    classifier_result: dict[str, Any] = {
        "engine": "rule_classifier_v1",
        "decision": "NOT_FOUND",
        "report_found": False,
        "evaluated_messages": 0,
        "candidate_reports": 0,
        "undated_report_candidates": 0,
        "matched_message_date": None,
    }
    report_found = False
    submitted_for_bcnb = False
    report_detail = None
    matched_message = None

    for message in reversed(messages):
        await ensure_not_cancelled()
        raw_content = await message.inner_text()
        if len(raw_content.strip()) < 50:
            continue
        classifier_result["evaluated_messages"] += 1
        is_report, message_date = check_is_report_and_extract_date(
            raw_content,
            group_name,
            today,
        )
        if not is_report:
            continue
        classifier_result["candidate_reports"] += 1
        if message_date is None:
            message_date = await _message_date_from_zalo_ui(message, today)
        if message_date is None:
            classifier_result["undated_report_candidates"] += 1
            continue

        delay = None
        if class_date_str:
            try:
                delay = report_delay_days(
                    class_date_str,
                    message_date,
                    default_year=today.year,
                )
            except (TypeError, ValueError) as exc:
                classifier_result["decision"] = "INVALID_CLASS_DATE"
                return build_scan_result(
                    group=group_name,
                    person=person,
                    actual_group_name=actual_group_name,
                    scanned_at=scanned_at,
                    messages_checked=messages_checked,
                    classifier_result=classifier_result,
                    required_window_start=required_window_start,
                    required_window_end=today,
                    oldest_message_date=oldest_message_date,
                    newest_message_date=newest_message_date,
                    images={"report": None, "latest": None, "oldest": None},
                    report_found=False,
                    extra_reasons=[f"Ngày học không hợp lệ: {exc}"],
                )

        if delay is not None:
            if -2 <= delay <= 2:
                report_detail = f"Nộp {message_date.strftime('%d/%m')} đúng hạn"
                submitted_for_bcnb = True
            elif 2 < delay <= 8:
                report_detail = (
                    f"Nộp {message_date.strftime('%d/%m')} – Trễ {delay} ngày"
                )
                submitted_for_bcnb = True
            elif delay > 8:
                report_detail = (
                    f"Nộp {message_date.strftime('%d/%m')} – Quá hạn {delay} ngày"
                )
            else:
                continue
        else:
            normalized_date = message_date.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            delta_days = (today - normalized_date).days
            if 0 <= delta_days <= 8:
                report_detail = (
                    f"Nộp {message_date.strftime('%d/%m')} – "
                    f"Cách thời điểm quét {delta_days} ngày"
                )
                submitted_for_bcnb = True
            elif delta_days > 8:
                report_detail = (
                    f"Nộp {message_date.strftime('%d/%m')} – "
                    f"Quá hạn {delta_days} ngày"
                )
            else:
                continue

        report_found = True
        matched_message = message
        classifier_result.update(
            {
                "decision": "REPORT_FOUND",
                "report_found": True,
                "matched_message_date": message_date.date().isoformat(),
            }
        )
        break

    images: dict[str, dict[str, str] | None] = {
        "report": None,
        "latest": None,
        "oldest": None,
    }
    if report_found:
        images["report"] = await _capture_zalo_evidence(
            page,
            matched_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="report",
        )
    else:
        latest_message = messages[-1] if messages else None
        oldest_message = messages[0] if messages else None
        images["latest"] = await _capture_zalo_evidence(
            page,
            latest_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="latest",
        )
        images["oldest"] = await _capture_zalo_evidence(
            page,
            oldest_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="oldest",
        )

    extra_reasons = []
    if not report_found and classifier_result["undated_report_candidates"]:
        extra_reasons.append(
            "Có tin nhắn giống báo cáo nhưng không đọc được ngày gửi."
        )

    return build_scan_result(
        group=group_name,
        person=person,
        actual_group_name=actual_group_name,
        scanned_at=scanned_at,
        messages_checked=messages_checked,
        classifier_result=classifier_result,
        required_window_start=required_window_start,
        required_window_end=today,
        oldest_message_date=oldest_message_date,
        newest_message_date=newest_message_date,
        images=images,
        report_found=report_found,
        report_detail=report_detail,
        submitted_for_bcnb=submitted_for_bcnb,
        extra_reasons=extra_reasons,
    )
