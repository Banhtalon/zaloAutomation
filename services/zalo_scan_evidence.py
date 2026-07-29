from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import hashlib
import os
from typing import Any
import uuid

from classifiers.rule_classifier import (
    classify_report_content,
    parse_zalo_date_header,
)
from core.time_utils import now_business
from logger_utils import log_message
from services.zalo_evidence import (
    SUBMITTED_LATE_VERIFIED,
    SUBMITTED_VERIFIED,
    build_image_evidence,
    build_scan_result,
)
from services.zalo_safety import (
    open_chat_matches,
    parse_class_date,
    report_delay_days,
)


EVIDENCE_WINDOW_DAYS = 10
MAX_EVIDENCE_SCROLLS = 12
BCNB_TICK_MAX_DELAY_DAYS = 6
ZALO_INCOMPLETE_SYNC_BANNERS = (
    "Zalo Web của bạn hiện chưa có đầy đủ tin nhắn gần đây",
    "Đồng bộ tin nhắn gần đây",
    "Nhấn để đồng bộ ngay",
)
ZALO_SYNC_ACTION_TEXTS = (
    "Nhấn để đồng bộ ngay",
    "Đồng bộ tin nhắn gần đây",
)
ZALO_SYNC_WAIT_SECONDS = 180.0
ZALO_SYNC_VERIFY_OBSERVATIONS = 3


def _required_window_start(class_date_str: str, today: datetime) -> datetime:
    """Use the earlier of the standard window and two days before class date."""
    standard_window = today - timedelta(days=EVIDENCE_WINDOW_DAYS)
    if not (class_date_str or "").strip():
        return standard_window
    class_date = parse_class_date(
        class_date_str,
        default_year=today.year,
        timezone_info=today.tzinfo,
    )
    assert class_date is not None
    return min(standard_window, class_date - timedelta(days=2))


async def _message_date_header_from_zalo_ui(message):
    try:
        date_headers = message.locator(
            'xpath=preceding::div[contains(@class, "chat-date") or contains(@class, "block-date")]'
        )
        if await date_headers.count() == 0:
            return None
        return date_headers.last
    except Exception:
        return None


async def _message_date_from_zalo_ui(message, today: datetime) -> datetime | None:
    date_header = await _message_date_header_from_zalo_ui(message)
    if date_header is None:
        return None
    try:
        return parse_zalo_date_header(await date_header.inner_text(), today)
    except Exception:
        return None


async def _zalo_sync_is_incomplete(page) -> bool:
    """Fail closed when Zalo announces that the local chat history is incomplete."""
    if not hasattr(page, "get_by_text"):
        return False
    for banner_text in ZALO_INCOMPLETE_SYNC_BANNERS:
        try:
            banner = page.get_by_text(banner_text, exact=False)
            if await banner.count() > 0 and await banner.first.is_visible():
                return True
        except Exception:
            continue
    return False


async def _visible_zalo_sync_action(page):
    if not hasattr(page, "get_by_text"):
        return None
    for action_text in ZALO_SYNC_ACTION_TEXTS:
        try:
            action = page.get_by_text(action_text, exact=False)
            if await action.count() > 0 and await action.first.is_visible():
                return action.first
        except Exception:
            continue
    return None


def _sync_result(
    decision: str,
    *,
    attempted: bool,
    verified: bool,
    reload_verified: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "attempted": attempted,
        "verified": verified,
        "reload_verified": reload_verified,
        "checked_at": now_business().isoformat(timespec="seconds"),
        "reason": reason,
    }


def attach_zalo_sync_result(
    result: dict[str, Any],
    sync_result: dict[str, Any],
) -> dict[str, Any]:
    """Persist the session-level sync decision alongside each class result."""
    evidence = result.setdefault("evidence", {})
    persisted_sync = dict(sync_result)
    classifier_decision = evidence.get("classifier_result", {}).get("decision")
    if classifier_decision == "ZALO_SYNC_INCOMPLETE":
        persisted_sync.update(
            {
                "decision": "SYNC_LOST_DURING_SCAN",
                "verified": False,
                "reload_verified": False,
                "reason": ("Zalo hiển thị lại cảnh báo thiếu lịch sử trong lúc quét lớp."),
            }
        )
    evidence["zalo_sync"] = persisted_sync
    return result


async def ensure_zalo_history_synced(
    page,
    *,
    ensure_not_cancelled: Callable[[], Awaitable[None]],
    wait_timeout_seconds: float = ZALO_SYNC_WAIT_SECONDS,
    poll_interval_seconds: float = 1.0,
    verify_observations: int = ZALO_SYNC_VERIFY_OBSERVATIONS,
) -> dict[str, Any]:
    """Try one Zalo history sync and verify that it survives a page reload."""
    if not await _zalo_sync_is_incomplete(page):
        return _sync_result(
            "SYNC_NOT_REQUIRED",
            attempted=False,
            verified=True,
            reload_verified=False,
            reason="Zalo không hiển thị cảnh báo thiếu lịch sử sau khi tải trang.",
        )

    await ensure_not_cancelled()
    action = await _visible_zalo_sync_action(page)
    if action is None:
        return _sync_result(
            "SYNC_ACTION_NOT_FOUND",
            attempted=False,
            verified=False,
            reload_verified=False,
            reason="Có cảnh báo thiếu lịch sử nhưng không tìm thấy nút đồng bộ.",
        )

    try:
        await action.click(timeout=5000)
    except Exception as exc:
        return _sync_result(
            "SYNC_CLICK_FAILED",
            attempted=True,
            verified=False,
            reload_verified=False,
            reason=f"Không nhấn được nút đồng bộ Zalo: {exc}",
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, wait_timeout_seconds)
    while await _zalo_sync_is_incomplete(page):
        await ensure_not_cancelled()
        if loop.time() >= deadline:
            return _sync_result(
                "SYNC_TIMEOUT",
                attempted=True,
                verified=False,
                reload_verified=False,
                reason="Cảnh báo thiếu lịch sử không biến mất trong thời gian chờ.",
            )
        await asyncio.sleep(max(0.0, poll_interval_seconds))

    try:
        await ensure_not_cancelled()
        await page.reload(wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_selector("#contact-search-input", timeout=120000)
        for observation in range(max(1, verify_observations)):
            await ensure_not_cancelled()
            if await _zalo_sync_is_incomplete(page):
                return _sync_result(
                    "SYNC_REAPPEARED",
                    attempted=True,
                    verified=False,
                    reload_verified=False,
                    reason="Cảnh báo thiếu lịch sử xuất hiện lại sau khi tải mới Zalo.",
                )
            if observation + 1 < max(1, verify_observations):
                await asyncio.sleep(max(0.0, poll_interval_seconds))
    except Exception as exc:
        return _sync_result(
            "SYNC_RELOAD_UNVERIFIED",
            attempted=True,
            verified=False,
            reload_verified=False,
            reason=f"Không xác minh được trạng thái đồng bộ sau khi tải mới: {exc}",
        )

    return _sync_result(
        "SYNC_VERIFIED",
        attempted=True,
        verified=True,
        reload_verified=True,
        reason="Đã đồng bộ và cảnh báo không quay lại sau khi tải mới.",
    )


async def _capture_zalo_evidence(
    page,
    target_message,
    *,
    job_id: int,
    screenshots_dir: str,
    label: str,
    date_header=None,
    require_date_header: bool = False,
) -> dict[str, str] | None:
    if target_message is None or (require_date_header and date_header is None):
        return None
    filename = f"{label}-{uuid.uuid4().hex[:10]}.png"
    file_path = os.path.join(screenshots_dir, filename)
    public_path = f"/static/screenshots/job-{job_id}/{filename}"
    try:
        scroll_target = date_header if date_header is not None else target_message
        await scroll_target.scroll_into_view_if_needed(timeout=2000)
        await asyncio.sleep(0.4)
        await page.screenshot(path=file_path)
        evidence = build_image_evidence(public_path, file_path)
        if evidence is not None and date_header is not None:
            ui_date_text = (await date_header.inner_text()).strip()
            if ui_date_text:
                evidence["ui_date_text"] = ui_date_text
        return evidence
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
    classifier_decision: str = "NOT_EVALUATED",
) -> dict[str, Any]:
    return build_scan_result(
        group=group,
        person=person,
        actual_group_name=actual_group_name,
        scanned_at=scanned_at,
        messages_checked=0,
        classifier_result={
            "engine": "rule_classifier_v2",
            "decision": classifier_decision,
            "report_found": False,
            "evaluated_messages": 0,
        },
        required_window_start=today - timedelta(days=EVIDENCE_WINDOW_DAYS),
        required_window_end=today,
        oldest_message_date=None,
        newest_message_date=None,
        images={
            "report": None,
            "latest": None,
            "oldest": None,
            "oldest_message": None,
        },
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
    scanned_at = now_business()
    if not open_chat_matches(group_name, actual_group_name):
        return build_empty_evidence_result(
            group=group_name,
            person=person,
            actual_group_name=actual_group_name or None,
            scanned_at=scanned_at,
            today=today,
            reason="Tên nhóm Zalo thực tế không khớp cấu hình.",
            classifier_decision="GROUP_IDENTITY_MISMATCH",
        )

    try:
        required_window_start = _required_window_start(class_date_str, today)
    except (TypeError, ValueError) as exc:
        return build_scan_result(
            group=group_name,
            person=person,
            actual_group_name=actual_group_name,
            scanned_at=scanned_at,
            messages_checked=0,
            classifier_result={
                "engine": "rule_classifier_v2",
                "decision": "INVALID_CLASS_DATE",
                "report_found": False,
                "evaluated_messages": 0,
            },
            required_window_start=today - timedelta(days=EVIDENCE_WINDOW_DAYS),
            required_window_end=today,
            oldest_message_date=None,
            newest_message_date=None,
            images={
                "report": None,
                "latest": None,
                "oldest": None,
                "oldest_message": None,
            },
            report_found=False,
            extra_reasons=[f"Ngày học không hợp lệ: {exc}"],
        )

    if await _zalo_sync_is_incomplete(page):
        return build_scan_result(
            group=group_name,
            person=person,
            actual_group_name=actual_group_name,
            scanned_at=scanned_at,
            messages_checked=0,
            classifier_result={
                "engine": "rule_classifier_v2",
                "decision": "ZALO_SYNC_INCOMPLETE",
                "report_found": False,
                "evaluated_messages": 0,
            },
            required_window_start=required_window_start,
            required_window_end=today,
            oldest_message_date=None,
            newest_message_date=None,
            images={
                "report": None,
                "latest": None,
                "oldest": None,
                "oldest_message": None,
            },
            report_found=False,
            extra_reasons=["Zalo thông báo lịch sử tin nhắn chưa đồng bộ đầy đủ."],
        )

    await page.mouse.move(500, 400)
    stable_coverage_observations = 0
    previous_coverage_observation: tuple[int, str] | None = None
    for _scroll_idx in range(MAX_EVIDENCE_SCROLLS):
        await ensure_not_cancelled()
        loaded_messages = await page.locator('[data-component="bubble-message"]').all()
        oldest_loaded_date = (
            await _message_date_from_zalo_ui(loaded_messages[0], today) if loaded_messages else None
        )
        if (
            oldest_loaded_date is not None
            and oldest_loaded_date.date() <= required_window_start.date()
        ):
            observation = (len(loaded_messages), oldest_loaded_date.date().isoformat())
            stable_coverage_observations = (
                stable_coverage_observations + 1
                if observation == previous_coverage_observation
                else 1
            )
            previous_coverage_observation = observation
            if stable_coverage_observations >= 2:
                log_message(
                    f"  -> Đã tải đủ cửa sổ kiểm chứng {EVIDENCE_WINDOW_DAYS} ngày trên Zalo."
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
    oldest_message_date = await _message_date_from_zalo_ui(messages[0], today) if messages else None
    newest_message_date = (
        await _message_date_from_zalo_ui(messages[-1], today) if messages else None
    )

    classifier_result: dict[str, Any] = {
        "engine": "rule_classifier_v2",
        "decision": "NOT_FOUND",
        "report_found": False,
        "evaluated_messages": 0,
        "candidate_reports": 0,
        "undated_report_candidates": 0,
        "matched_ui_sent_at": None,
        "candidates": [],
    }
    report_found = False
    submitted_for_bcnb = False
    report_detail = None
    matched_message = None
    verified_report_state = SUBMITTED_VERIFIED
    warnings: list[str] = []

    for message in reversed(messages):
        await ensure_not_cancelled()
        raw_content = await message.inner_text()
        if len(raw_content.strip()) < 50:
            continue
        classifier_result["evaluated_messages"] += 1
        classification = classify_report_content(raw_content, group_name, today)
        if not classification["is_report"]:
            continue
        classifier_result["candidate_reports"] += 1
        ui_sent_at = await _message_date_from_zalo_ui(message, today)
        content_dates = [
            content_date.date().isoformat() for content_date in classification["content_dates"]
        ]
        candidate: dict[str, Any] = {
            "score": classification["score"],
            "matched_signals": classification["matched_signals"],
            "content_dates": content_dates,
            "ui_sent_at": ui_sent_at.date().isoformat() if ui_sent_at else None,
            "message_fingerprint": hashlib.sha256(raw_content.strip().encode("utf-8")).hexdigest(),
        }
        if ui_sent_at is None:
            classifier_result["undated_report_candidates"] += 1
            candidate["decision"] = "UI_SENT_AT_UNREADABLE"
            classifier_result["candidates"].append(candidate)
            continue

        if content_dates and ui_sent_at.date().isoformat() not in content_dates:
            candidate["warning"] = "CONTENT_DATE_MISMATCH"
            warnings.append("CONTENT_DATE_MISMATCH: ngày trong nội dung khác ngày gửi trên Zalo.")

        delay = None
        if class_date_str:
            delay = report_delay_days(
                class_date_str,
                ui_sent_at,
                default_year=today.year,
            )

        if delay is not None:
            if -2 <= delay <= 2:
                report_detail = f"Nộp {ui_sent_at.strftime('%d/%m')} đúng hạn"
                submitted_for_bcnb = True
                candidate["decision"] = "SUBMITTED_ON_TIME"
            elif 2 < delay <= BCNB_TICK_MAX_DELAY_DAYS:
                report_detail = f"Nộp {ui_sent_at.strftime('%d/%m')} – Trễ {delay} ngày"
                submitted_for_bcnb = True
                candidate["decision"] = "SUBMITTED_LATE_ACCEPTED"
            elif delay > BCNB_TICK_MAX_DELAY_DAYS:
                report_detail = f"Nộp {ui_sent_at.strftime('%d/%m')} – Quá hạn {delay} ngày"
                verified_report_state = SUBMITTED_LATE_VERIFIED
                candidate["decision"] = "SUBMITTED_LATE"
            else:
                candidate["decision"] = "BEFORE_CLASS_DATE"
                candidate["delay_days"] = delay
                classifier_result["candidates"].append(candidate)
                continue
        else:
            normalized_date = ui_sent_at.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            delta_days = (today - normalized_date).days
            if 0 <= delta_days <= 8:
                report_detail = (
                    f"Nộp {ui_sent_at.strftime('%d/%m')} – Cách thời điểm quét {delta_days} ngày"
                )
                submitted_for_bcnb = True
                candidate["decision"] = "SUBMITTED_WITHOUT_CLASS_DATE"
            elif delta_days > 8:
                report_detail = f"Nộp {ui_sent_at.strftime('%d/%m')} – Quá hạn {delta_days} ngày"
                verified_report_state = SUBMITTED_LATE_VERIFIED
                candidate["decision"] = "SUBMITTED_LATE_WITHOUT_CLASS_DATE"
            else:
                candidate["decision"] = "FUTURE_UI_SENT_AT"
                classifier_result["candidates"].append(candidate)
                continue

        report_found = True
        matched_message = message
        candidate["delay_days"] = delay
        classifier_result["candidates"].append(candidate)
        classifier_result.update(
            {
                "decision": candidate["decision"],
                "report_found": True,
                "matched_ui_sent_at": ui_sent_at.date().isoformat(),
            }
        )
        break

    images: dict[str, dict[str, str] | None] = {
        "report": None,
        "latest": None,
        "oldest": None,
        "oldest_message": None,
    }
    if report_found:
        report_date_header = await _message_date_header_from_zalo_ui(matched_message)
        images["report"] = await _capture_zalo_evidence(
            page,
            matched_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="report",
            date_header=report_date_header,
        )
    else:
        latest_message = messages[-1] if messages else None
        oldest_message = messages[0] if messages else None
        latest_date_header = await _message_date_header_from_zalo_ui(latest_message)
        oldest_date_header = await _message_date_header_from_zalo_ui(oldest_message)
        images["latest"] = await _capture_zalo_evidence(
            page,
            latest_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="latest",
            date_header=latest_date_header,
        )
        images["oldest"] = await _capture_zalo_evidence(
            page,
            oldest_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="oldest",
            date_header=oldest_date_header,
            require_date_header=True,
        )
        images["oldest_message"] = await _capture_zalo_evidence(
            page,
            oldest_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="oldest-message",
        )

    extra_reasons = []
    if not report_found and classifier_result["undated_report_candidates"]:
        extra_reasons.append("Có tin nhắn giống báo cáo nhưng không đọc được ngày gửi.")

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
        warnings=warnings,
        verified_report_state=verified_report_state,
    )
