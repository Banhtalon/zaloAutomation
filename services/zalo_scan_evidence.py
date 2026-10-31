from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
import hashlib
import os
from typing import Any, cast
import unicodedata
import uuid

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from classifiers.rule_classifier import (
    MAX_AI_ASSIST_CANDIDATES,
    classify_report_content,
    is_eligible_for_ai_assist,
    parse_zalo_date_header,
)
from core.time_utils import now_utc, utc_iso
from logger_utils import log_message
from services.zalo_evidence import (
    AI_SUGGESTED_REPORT_DECISION,
    SUBMITTED_LATE_VERIFIED,
    SUBMITTED_VERIFIED,
    build_image_evidence,
    build_scan_result,
)
from services.zalo_safety import (
    extract_report_content_lesson_number,
    extract_report_lesson_number,
    infer_previous_lesson_date,
    infer_report_lesson_date,
    open_chat_matches,
    parse_class_date,
    report_delay_days,
)
from services.lms_quality_service import (
    evaluate_zalo_content_issues,
    extract_report_lesson_title,
)
from services.report_status import (
    ISSUE_CODE_ZALO_REPORT_NO_DATE,
    ISSUE_CODE_ZALO_REPORT_WRONG_DATE,
    make_structured_issue,
)
from services.zalo_target_matching import (
    BcnbScanTarget,
    TARGET_SAME_DAY_NO_DATE,
    TARGET_SAME_DAY_WRONG_DATE,
    TARGET_SESSION_AMBIGUOUS,
    TargetMatch,
    parse_bcnb_scan_target,
    resolve_report_target,
)


EVIDENCE_WINDOW_DAYS = 10
MAX_EVIDENCE_SCROLLS = 12
# A single-target canary may opt into a larger, still finite evidence window.
# These hard ceilings are enforced before ``locator.all()`` materializes UI
# messages; the normal batch path keeps its historical 12-scroll default.
MAX_CANARY_SCROLLS = 60
MAX_CANARY_MESSAGES = 1000
DEFAULT_BATCH_MAX_MESSAGES = 500
MESSAGE_CAP_REACHED_REASON = (
    "Đã chạm trần số lượng tin nhắn kiểm tra tối đa (capture_max_messages); "
    "cần kiểm tra thủ công."
)
# Kept as a compatibility export for callers that imported the old constant.
# Tick eligibility is no longer decided by a fixed number of days; the exact
# BCNB deadline is resolved per row in the service layer.
BCNB_TICK_MAX_DELAY_DAYS = 6
_AI_ASSIST_GROUP_BUDGET_LOCK = asyncio.Lock()
_AI_ASSIST_GROUP_COUNTS: dict[int, dict[str, int]] = {}


def _resolve_ai_assist_budget_key(
    group_name: str,
    target: BcnbScanTarget | None = None,
    scan_target: Mapping[str, Any] | None = None,
) -> str:
    if target is not None:
        return (
            f"{target.year:04d}-W{target.week:02d}|"
            f"{target.class_code.strip().upper()}|"
            f"{target.lesson.strip()}|"
            f"{target.class_date_iso.strip()}"
        )
    raw_tk = (
        str(scan_target.get("target_key") or "").strip()
        if isinstance(scan_target, Mapping)
        else ""
    )
    return raw_tk or (
        unicodedata.normalize("NFC", str(group_name or "")).strip().casefold()
        or "__unnamed_group__"
    )


async def _try_claim_ai_assist_target_slot(job_id: int, budget_key: str) -> bool:
    """Enforce MAX_AI_ASSIST_CANDIDATES per target_key (with group fallback) in one job."""
    normalized_key = (
        unicodedata.normalize("NFC", str(budget_key or "")).strip().casefold()
        or "__unnamed_target__"
    )
    async with _AI_ASSIST_GROUP_BUDGET_LOCK:
        if len(_AI_ASSIST_GROUP_COUNTS) >= 128 and int(job_id) not in _AI_ASSIST_GROUP_COUNTS:
            oldest_job_id = next(iter(_AI_ASSIST_GROUP_COUNTS))
            _AI_ASSIST_GROUP_COUNTS.pop(oldest_job_id, None)
        job_counts = _AI_ASSIST_GROUP_COUNTS.setdefault(int(job_id), {})
        used = job_counts.get(normalized_key, 0)
        if used >= MAX_AI_ASSIST_CANDIDATES:
            return False
        job_counts[normalized_key] = used + 1
        return True


_try_claim_ai_assist_group_slot = _try_claim_ai_assist_target_slot

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

# Capture is an evidence boundary, so every browser/file operation has an
# explicit finite timeout and a stable code.  These defaults are deliberately
# independent of Playwright's default timeout.
CAPTURE_SCROLL_TIMEOUT_SECONDS = 5.0
CAPTURE_SCREENSHOT_TIMEOUT_SECONDS = 10.0
CAPTURE_UI_DATE_TIMEOUT_SECONDS = 5.0
MAX_CAPTURE_TELEMETRY_ELAPSED_MS = 600_000
CAPTURE_PHASE_SCROLL = "CAPTURE_SCROLL"
CAPTURE_PHASE_SCREENSHOT = "CAPTURE_SCREENSHOT"
CAPTURE_PAGE_STATE_READY = "READY"
CAPTURE_DEFAULT_WORKER_ID = "unknown"
MAX_CAPTURE_ATTEMPTS = 2
CAPTURE_TARGET_MISSING = "CAPTURE_TARGET_MISSING"
CAPTURE_SCROLL_TIMEOUT = "CAPTURE_SCROLL_TIMEOUT"
CAPTURE_SCROLL_ERROR = "CAPTURE_SCROLL_ERROR"
CAPTURE_SCREENSHOT_TIMEOUT = "CAPTURE_SCREENSHOT_TIMEOUT"
CAPTURE_SCREENSHOT_ERROR = "CAPTURE_SCREENSHOT_ERROR"
CAPTURE_FILE_VALIDATION_ERROR = "CAPTURE_FILE_VALIDATION_ERROR"


class EvidenceBoundsExceeded(RuntimeError):
    """Raised before materializing evidence beyond an approved hard bound."""

_CAPTURE_LABELS = frozenset({"report", "latest", "oldest", "oldest-message"})
_CAPTURE_PHASES = frozenset(
    {CAPTURE_PHASE_SCROLL, CAPTURE_PHASE_SCREENSHOT}
)
_CAPTURE_PAGE_STATES = frozenset({CAPTURE_PAGE_STATE_READY})
_CAPTURE_CODES = frozenset(
    {
        CAPTURE_TARGET_MISSING,
        CAPTURE_SCROLL_TIMEOUT,
        CAPTURE_SCROLL_ERROR,
        CAPTURE_SCREENSHOT_TIMEOUT,
        CAPTURE_SCREENSHOT_ERROR,
        CAPTURE_FILE_VALIDATION_ERROR,
    }
)


def _capture_timeout(error: BaseException) -> bool:
    return isinstance(error, (PlaywrightTimeoutError, asyncio.TimeoutError, TimeoutError))


def _safe_capture_worker_id(worker_id: str) -> str:
    value = str(worker_id or "")
    if value == CAPTURE_DEFAULT_WORKER_ID:
        return value
    if value.startswith("scan-") and value[5:].isdigit() and len(value[5:]) <= 3:
        return value
    return CAPTURE_DEFAULT_WORKER_ID


def _safe_capture_attempt(attempt: int) -> int:
    try:
        value = int(attempt)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(MAX_CAPTURE_ATTEMPTS, value))


def _capture_diagnostic(
    diagnostics: list[dict[str, Any]] | None,
    *,
    job_id: int = 0,
    worker_id: str = CAPTURE_DEFAULT_WORKER_ID,
    attempt: int = 1,
    label: str,
    phase: str,
    error_code: str,
    started_at: float,
) -> None:
    """Append/log only bounded capture facts; never serialize browser errors."""
    safe_label = label if label in _CAPTURE_LABELS else "unknown"
    safe_phase = phase if phase in _CAPTURE_PHASES else CAPTURE_PHASE_SCREENSHOT
    safe_code = error_code if error_code in _CAPTURE_CODES else CAPTURE_FILE_VALIDATION_ERROR
    safe_worker_id = _safe_capture_worker_id(worker_id)
    safe_attempt = _safe_capture_attempt(attempt)
    elapsed_ms = min(
        MAX_CAPTURE_TELEMETRY_ELAPSED_MS,
        int(max(0.0, asyncio.get_running_loop().time() - started_at) * 1000),
    )
    item: dict[str, str | int] = {
        "label": safe_label,
        "phase": safe_phase,
        "error_code": safe_code,
        "attempt": safe_attempt,
        "elapsed_ms": elapsed_ms,
    }
    if diagnostics is not None:
        diagnostics.append(item)
    log_fields: dict[str, str | int] = {
        "job_id": int(job_id),
        "worker_id": safe_worker_id,
        "phase": safe_phase,
        "attempt": safe_attempt,
        "elapsed_ms": elapsed_ms,
        "page_state": CAPTURE_PAGE_STATE_READY,
        "error_code": safe_code,
    }
    try:
        log_message(
            "Zalo evidence capture telemetry",
            event="zalo.scan.capture_telemetry",
            **cast(Any, log_fields),
        )
    except Exception:
        # Telemetry must not change the fail-closed evidence decision.
        pass


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


def detect_history_gap(
    distinct_dates: list[datetime],
    target_date: datetime,
    min_gap_days: int = 5,
) -> tuple[datetime, datetime, int] | None:
    """Detect if target_date falls inside a suspicious gap between observed message dates.

    When chat history on Zalo Web is only partially synchronized, messages from older
    sessions and newest sessions may be present while the date window around the target
    class is completely missing. This returns (d_prev, d_curr, gap_days) if a gap >= min_gap_days
    straddles target_date.
    """
    sorted_dates = sorted(distinct_dates)
    for d_prev, d_curr in zip(sorted_dates, sorted_dates[1:]):
        if d_prev.date() < target_date.date() < d_curr.date():
            gap = (d_curr.date() - d_prev.date()).days
            if gap >= min_gap_days:
                return d_prev, d_curr, gap
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
        "checked_at": utc_iso(),
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
    diagnostics: list[dict[str, Any]] | None = None,
    worker_id: str = CAPTURE_DEFAULT_WORKER_ID,
    attempt: int = 1,
) -> dict[str, Any] | None:
    started_at = asyncio.get_running_loop().time()
    if target_message is None or (require_date_header and date_header is None):
        _capture_diagnostic(
            diagnostics,
            job_id=job_id,
            worker_id=worker_id,
            attempt=attempt,
            label=label,
            phase=CAPTURE_PHASE_SCROLL,
            error_code=CAPTURE_TARGET_MISSING,
            started_at=started_at,
        )
        return None
    filename = f"{label}-{uuid.uuid4().hex[:10]}.png"
    file_path = os.path.join(screenshots_dir, filename)
    public_path = f"/api/evidence/{job_id}/{filename}"
    scroll_target = date_header if date_header is not None else target_message
    try:
        await asyncio.wait_for(
            scroll_target.scroll_into_view_if_needed(timeout=2000),
            timeout=max(0.001, float(CAPTURE_SCROLL_TIMEOUT_SECONDS)),
        )
        await asyncio.wait_for(
            asyncio.sleep(0.4),
            timeout=max(0.001, float(CAPTURE_SCROLL_TIMEOUT_SECONDS)),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _capture_diagnostic(
            diagnostics,
            job_id=job_id,
            worker_id=worker_id,
            attempt=attempt,
            label=label,
            phase=CAPTURE_PHASE_SCROLL,
            error_code=(
                CAPTURE_SCROLL_TIMEOUT if _capture_timeout(exc) else CAPTURE_SCROLL_ERROR
            ),
            started_at=started_at,
        )
        return None

    try:
        await asyncio.wait_for(
            page.screenshot(path=file_path),
            timeout=max(0.001, float(CAPTURE_SCREENSHOT_TIMEOUT_SECONDS)),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _capture_diagnostic(
            diagnostics,
            job_id=job_id,
            worker_id=worker_id,
            attempt=attempt,
            label=label,
            phase=CAPTURE_PHASE_SCREENSHOT,
            error_code=(
                CAPTURE_SCREENSHOT_TIMEOUT
                if _capture_timeout(exc)
                else CAPTURE_SCREENSHOT_ERROR
            ),
            started_at=started_at,
        )
        return None

    try:
        evidence = build_image_evidence(public_path, file_path)
    except asyncio.CancelledError:
        raise
    except Exception:
        _capture_diagnostic(
            diagnostics,
            job_id=job_id,
            worker_id=worker_id,
            attempt=attempt,
            label=label,
            phase=CAPTURE_PHASE_SCREENSHOT,
            error_code=CAPTURE_FILE_VALIDATION_ERROR,
            started_at=started_at,
        )
        return None
    if evidence is None:
        _capture_diagnostic(
            diagnostics,
            job_id=job_id,
            worker_id=worker_id,
            attempt=attempt,
            label=label,
            phase=CAPTURE_PHASE_SCREENSHOT,
            error_code=CAPTURE_FILE_VALIDATION_ERROR,
            started_at=started_at,
        )
        return None

    if date_header is not None:
        try:
            ui_date_text = (
                await asyncio.wait_for(
                    date_header.inner_text(),
                    timeout=max(0.001, float(CAPTURE_UI_DATE_TIMEOUT_SECONDS)),
                )
            ).strip()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _capture_diagnostic(
                diagnostics,
                job_id=job_id,
                worker_id=worker_id,
                attempt=attempt,
                label=label,
                phase=CAPTURE_PHASE_SCROLL,
                error_code=(
                    CAPTURE_SCROLL_TIMEOUT if _capture_timeout(exc) else CAPTURE_SCROLL_ERROR
                ),
                started_at=started_at,
            )
            return None
        if ui_date_text:
            evidence["ui_date_text"] = ui_date_text
    return evidence


async def _bounded_message_count(message_locator) -> int | None:
    count_fn = getattr(message_locator, "count", None)
    if callable(count_fn):
        return await count_fn()
    return None


async def _capture_report_evidence_with_retry(
    page,
    target_message,
    *,
    job_id: int,
    screenshots_dir: str,
    diagnostics: list[dict[str, Any]],
    worker_id: str,
    attempt: int,
) -> dict[str, Any] | None:
    max_attempts = max(1, int(MAX_CAPTURE_ATTEMPTS))
    for capture_try in range(1, max_attempts + 1):
        effective_attempt = (
            capture_try
            if int(attempt or 1) <= 1
            else _safe_capture_attempt(attempt)
        )
        report_date_header = await _message_date_header_from_zalo_ui(target_message)
        captured = await _capture_zalo_evidence(
            page,
            target_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="report",
            date_header=report_date_header,
            diagnostics=diagnostics,
            worker_id=worker_id,
            attempt=effective_attempt,
        )
        if captured is not None:
            return captured
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


def _set_lesson_date_context(
    date_context: dict[str, Any],
    lesson_date: datetime,
    *,
    source: str,
    reported_lesson: int | None = None,
) -> None:
    date_context.update(
        {
            "lesson_date": lesson_date.date().isoformat(),
            "lesson_date_source": source,
            "reported_lesson_date": lesson_date.date().isoformat(),
            "reported_lesson_date_source": source,
        }
    )
    if reported_lesson is not None:
        date_context["reported_lesson"] = reported_lesson


def _set_bcnb_target_context(date_context: dict[str, Any], target: BcnbScanTarget) -> None:
    """Keep the BCNB period authoritative even when report text is stale."""
    date_context.update(
        {
            "target_week": target.week,
            "target_year": target.year,
            "target_week_source": "BCNB_SERVER",
            "target_class_code": target.class_code,
            "target_class_date": target.class_date_iso,
            "target_lesson": target.lesson,
            "report_due_at": target.zalo_deadline or None,
        }
    )


def _apply_target_match_context(
    date_context: dict[str, Any],
    *,
    target: BcnbScanTarget,
    match: TargetMatch,
    claimed_lesson_date: datetime | None,
    claimed_lesson: int | None,
) -> None:
    """Record the claimed report fields and the BCNB row it was matched to."""
    _set_bcnb_target_context(date_context, target)
    date_context.update(
        {
            "lesson_date": target.class_date_iso,
            "lesson_date_source": (
                "bcnb_target_date_typos_corrected"
                if match.corrected_teacher_date
                else "bcnb_target_date"
            ),
            "matched_lesson_date": target.class_date_iso,
            "matched_lesson": target.lesson,
            "target_match_decision": match.decision,
            "target_match_note": match.note,
            "reported_lesson_date": (
                claimed_lesson_date.date().isoformat()
                if claimed_lesson_date is not None
                else None
            ),
            "reported_lesson": claimed_lesson,
        }
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
    scan_target: Mapping[str, Any] | None = None,
    previous_lesson_date_str: str = "",
    next_class_date_str: str = "",
    report_due_at_str: str = "",
    today: datetime,
    ensure_not_cancelled: Callable[[], Awaitable[None]],
    worker_id: str = CAPTURE_DEFAULT_WORKER_ID,
    attempt: int = 1,
    capture_max_scrolls: int | None = None,
    capture_max_messages: int | None = None,
    capture_window_start: datetime | None = None,
    capture_window_end: datetime | None = None,
    ai_report_classifier: Any = None,
    ai_assist_enabled: bool | None = None,
) -> dict[str, Any]:
    scanned_at = now_utc()
    if capture_max_scrolls is not None:
        if (
            isinstance(capture_max_scrolls, bool)
            or not isinstance(capture_max_scrolls, int)
            or capture_max_scrolls <= 0
            or capture_max_scrolls > MAX_CANARY_SCROLLS
        ):
            raise EvidenceBoundsExceeded
    if capture_max_messages is not None:
        if (
            isinstance(capture_max_messages, bool)
            or not isinstance(capture_max_messages, int)
            or capture_max_messages <= 0
            or capture_max_messages > MAX_CANARY_MESSAGES
        ):
            raise EvidenceBoundsExceeded
    if capture_window_start is not None and capture_window_start.tzinfo is None:
        raise ValueError("capture window start must be timezone-aware")
    if capture_window_end is not None and capture_window_end.tzinfo is None:
        raise ValueError("capture window end must be timezone-aware")
    if (
        capture_window_start is not None
        and capture_window_end is not None
        and capture_window_start > capture_window_end
    ):
        raise ValueError("capture window is reversed")
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

    date_context: dict[str, Any] = {
        "lesson_date": None,
        "lesson_date_source": None,
        "reported_lesson_date": None,
        "reported_lesson_date_source": None,
        "target_week": None,
        "target_year": None,
        "target_week_source": None,
        "target_class_code": None,
        "target_class_date": None,
        "target_lesson": None,
        "matched_lesson_date": None,
        "matched_lesson": None,
        "target_match_decision": None,
        "target_match_note": None,
        "reported_lesson": None,
        "previous_lesson_date": None,
        "next_class_date": None,
        "report_due_at": (report_due_at_str or "").strip() or None,
        "reference": "legacy_class_date",
    }
    target: BcnbScanTarget | None = None
    target_date: datetime | None = None
    invalid_decision = "INVALID_BCNB_TARGET" if scan_target is not None else "INVALID_CLASS_DATE"
    try:
        target = parse_bcnb_scan_target(
            scan_target,
            default_year=today.year,
            timezone_info=today.tzinfo,
        )
        if target is not None:
            _set_bcnb_target_context(date_context, target)
            next_class_date_str = target.class_date.strftime("%d/%m/%Y")
            report_due_at_str = target.zalo_deadline
        reference_class_date_str = (next_class_date_str or class_date_str or "").strip()
        explicit_next_class_date = bool((next_class_date_str or "").strip())
        required_window_start = (
            capture_window_start
            if capture_window_start is not None
            else _required_window_start(reference_class_date_str, today)
        )
        required_window_end = capture_window_end or today
        next_class_date = (
            parse_class_date(
                next_class_date_str,
                default_year=today.year,
                timezone_info=today.tzinfo,
            )
            if explicit_next_class_date
            else None
        )
        previous_lesson_date = (
            parse_class_date(
                previous_lesson_date_str,
                default_year=today.year,
                timezone_info=today.tzinfo,
            )
            if (previous_lesson_date_str or "").strip()
            else None
        )
        date_context.update(
            {
                "previous_lesson_date": (
                    previous_lesson_date.date().isoformat()
                    if previous_lesson_date is not None
                    else None
                ),
                "next_class_date": (
                    next_class_date.date().isoformat()
                    if next_class_date is not None
                    else None
                ),
                "reference": (
                    "previous_lesson_date"
                    if previous_lesson_date is not None
                    else ("next_class_date" if explicit_next_class_date else "legacy_class_date")
                ),
            }
        )
        if not explicit_next_class_date and previous_lesson_date is None:
            configured_class_date = parse_class_date(
                class_date_str,
                default_year=today.year,
                timezone_info=today.tzinfo,
            )
            if configured_class_date is not None:
                _set_lesson_date_context(
                    date_context,
                    configured_class_date,
                    source="configured_bcnb_date",
                )
        target_date = (
            target.class_date
            if target is not None
            else (next_class_date or previous_lesson_date or configured_class_date)
        )
    except (TypeError, ValueError) as exc:
        return build_scan_result(
            group=group_name,
            person=person,
            actual_group_name=actual_group_name,
            scanned_at=scanned_at,
            messages_checked=0,
            classifier_result={
                "engine": "rule_classifier_v2",
                "decision": invalid_decision,
                "report_found": False,
                "evaluated_messages": 0,
            },
            required_window_start=today - timedelta(days=EVIDENCE_WINDOW_DAYS),
            required_window_end=capture_window_end or today,
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
            date_context=date_context,
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
            required_window_end=capture_window_end or today,
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
            date_context=date_context,
        )

    await page.mouse.move(500, 400)
    stable_coverage_observations = 0
    previous_coverage_observation: tuple[int, str] | None = None
    max_scrolls = capture_max_scrolls or MAX_EVIDENCE_SCROLLS
    effective_max_messages = (
        capture_max_messages
        if capture_max_messages is not None
        else DEFAULT_BATCH_MAX_MESSAGES
    )
    message_cap_reached = False
    message_locator = page.locator('[data-component="bubble-message"]')
    for _scroll_idx in range(max_scrolls):
        await ensure_not_cancelled()
        if capture_max_messages is not None:
            loaded_count = await _bounded_message_count(message_locator)
            if loaded_count is not None and (
                isinstance(loaded_count, bool)
                or not isinstance(loaded_count, int)
                or loaded_count > capture_max_messages
            ):
                raise EvidenceBoundsExceeded
        else:
            loaded_count = await _bounded_message_count(message_locator)
            if loaded_count is not None and (
                isinstance(loaded_count, bool)
                or not isinstance(loaded_count, int)
                or loaded_count > effective_max_messages
            ):
                message_cap_reached = True
                break
        loaded_messages = await message_locator.all()
        if capture_max_messages is not None and len(loaded_messages) > capture_max_messages:
            raise EvidenceBoundsExceeded
        if len(loaded_messages) >= effective_max_messages:
            message_cap_reached = True
            break
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
                if target_date is not None and len(loaded_messages) < 15 and _scroll_idx < 4:
                    pass
                else:
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

    if capture_max_messages is not None:
        final_count = await _bounded_message_count(message_locator)
        if final_count is not None and (
            isinstance(final_count, bool)
            or not isinstance(final_count, int)
            or final_count > capture_max_messages
        ):
            raise EvidenceBoundsExceeded
        messages = await message_locator.all()
        if len(messages) > capture_max_messages:
            raise EvidenceBoundsExceeded
    else:
        final_count = await _bounded_message_count(message_locator)
        if final_count is not None and (
            isinstance(final_count, bool)
            or not isinstance(final_count, int)
            or final_count > effective_max_messages
        ):
            message_cap_reached = True
            messages = []
        else:
            messages = await message_locator.all()
            if len(messages) > effective_max_messages:
                message_cap_reached = True
                messages = messages[-effective_max_messages:]
    if len(messages) >= effective_max_messages:
        message_cap_reached = True
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
        "ai_assist_evaluated": 0,
        "matched_ui_sent_at": None,
        "candidates": [],
    }
    report_found = False
    submitted_for_bcnb = False
    report_detail = None
    matched_message = None
    ai_suggested_message = None
    ai_candidate_pool: list[tuple[Any, str, dict[str, Any]]] = []
    verified_report_state = SUBMITTED_VERIFIED
    warnings: list[str] = []
    has_ambiguous_target_candidate = False

    for message in reversed(messages):
        await ensure_not_cancelled()
        raw_content = await message.inner_text()
        if len(raw_content.strip()) < 50:
            continue
        classifier_result["evaluated_messages"] += 1
        classification = classify_report_content(raw_content, group_name, today)
        if not classification["is_report"]:
            if is_eligible_for_ai_assist(raw_content, classification):
                ai_candidate_pool.append((message, raw_content, classification))
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

        has_content_date_mismatch = bool(
            content_dates and ui_sent_at.date().isoformat() not in content_dates
        )

        delay = None
        date_reference = "no_class_date"
        reported_lesson = extract_report_lesson_number(raw_content)
        content_lesson = extract_report_content_lesson_number(raw_content)
        reported_lesson_title = extract_report_lesson_title(raw_content)
        if content_lesson is not None:
            candidate["content_lesson"] = content_lesson
        if reported_lesson_title:
            candidate["reported_lesson_title"] = reported_lesson_title
        claimed_lesson_date: datetime | None = None
        pending_lesson_date: datetime | None = None
        pending_lesson_source: str | None = None
        pending_prev_lesson_iso: str | None = None
        inferred_lesson_date = infer_report_lesson_date(
            classification["content_dates"],
            raw_content,
            message_date=ui_sent_at,
        )
        if (
            inferred_lesson_date is None
            and target is not None
            and ui_sent_at.date() == target.class_date.date()
            and classification["content_dates"]
        ):
            inferred_lesson_date = infer_report_lesson_date(
                classification["content_dates"],
                raw_content,
                message_date=max(
                    classification["content_dates"], key=lambda d: d.date()
                ),
            )
        if inferred_lesson_date is not None:
            claimed_lesson_date = inferred_lesson_date
            delay = (ui_sent_at.date() - inferred_lesson_date.date()).days
            date_reference = "report_content_lesson_date"
            candidate["lesson_date"] = inferred_lesson_date.date().isoformat()
            candidate["lesson_date_source"] = date_reference
            if reported_lesson is not None:
                candidate["reported_lesson"] = reported_lesson
            if (
                explicit_next_class_date
                and next_class_date is not None
                and inferred_lesson_date.date() < next_class_date.date()
            ):
                date_reference = "previous_lesson_date_from_content"
                candidate["lesson_date_source"] = date_reference
                candidate["previous_lesson_date"] = inferred_lesson_date.date().isoformat()
                pending_prev_lesson_iso = inferred_lesson_date.date().isoformat()
            pending_lesson_date = inferred_lesson_date
            pending_lesson_source = date_reference
        elif (previous_lesson_date_str or "").strip():
            delay = report_delay_days(
                previous_lesson_date_str,
                ui_sent_at,
                default_year=today.year,
            )
            date_reference = "previous_lesson_date"
            previous_lesson = parse_class_date(
                previous_lesson_date_str,
                default_year=today.year,
                timezone_info=today.tzinfo,
            )
            if previous_lesson is not None:
                claimed_lesson_date = previous_lesson
                pending_lesson_date = previous_lesson
                pending_lesson_source = date_reference
        elif reference_class_date_str:
            delay = report_delay_days(
                reference_class_date_str,
                ui_sent_at,
                default_year=today.year,
            )
            date_reference = "next_class_date" if explicit_next_class_date else "legacy_class_date"

            # The BCNB snapshot can already point at the next/current session
            # while the Zalo report belongs to the completed session before it.
            # Only a dated report body can disambiguate that relationship.
            if target is None and explicit_next_class_date and delay is not None and delay < 0:
                inferred_previous = infer_previous_lesson_date(
                    classification["content_dates"],
                    next_class_date=next_class_date,
                    message_date=ui_sent_at,
                )
                if inferred_previous is None:
                    candidate["decision"] = "BEFORE_CLASS_DATE"
                    candidate["delay_days"] = delay
                    candidate["date_reference"] = date_reference
                    classifier_result["candidates"].append(candidate)
                    continue
                delay = (ui_sent_at.date() - inferred_previous.date()).days
                claimed_lesson_date = inferred_previous
                date_reference = "previous_lesson_date_from_content"
                candidate["previous_lesson_date"] = inferred_previous.date().isoformat()
                pending_prev_lesson_iso = inferred_previous.date().isoformat()
                pending_lesson_date = inferred_previous
                pending_lesson_source = date_reference

        target_match: TargetMatch | None = None
        if target is not None:
            target_match = resolve_report_target(
                target,
                ui_sent_at=ui_sent_at,
                claimed_lesson_date=claimed_lesson_date,
                claimed_lesson=reported_lesson,
                content_lesson=content_lesson,
            )
            candidate["target_match"] = {
                "decision": target_match.decision,
                "matches": target_match.matches,
                "corrected_teacher_date": target_match.corrected_teacher_date,
                "note": target_match.note,
                "target_week": target.week,
                "target_year": target.year,
                "target_class_date": target.class_date_iso,
                "target_lesson": target.lesson,
            }
            if not target_match.matches:
                _set_bcnb_target_context(date_context, target)
                candidate["decision"] = target_match.decision
                classifier_result["candidates"].append(candidate)
                if target_match.decision == TARGET_SESSION_AMBIGUOUS:
                    has_ambiguous_target_candidate = True
                continue
            delay = (ui_sent_at.date() - target.class_date.date()).days
            date_reference = "bcnb_target_date"

        if delay is not None:
            if delay < 0:
                candidate["decision"] = "BEFORE_CLASS_DATE"
                candidate["delay_days"] = delay
                candidate["date_reference"] = date_reference
                classifier_result["candidates"].append(candidate)
                continue
            if delay <= 2:
                report_detail = f"Nộp {ui_sent_at.strftime('%d/%m')} đúng hạn"
                submitted_for_bcnb = True
                candidate["decision"] = "SUBMITTED_ON_TIME"
            elif delay <= BCNB_TICK_MAX_DELAY_DAYS:
                report_detail = f"Nộp {ui_sent_at.strftime('%d/%m')} – Trễ {delay} ngày"
                submitted_for_bcnb = True
                candidate["decision"] = "SUBMITTED_LATE_ACCEPTED"
            else:
                # This is only a provisional classifier state.  The service
                # layer replaces it with the exact BCNB deadline state before
                # any checkbox action; it never blocks a verified report just
                # because this compatibility window was exceeded.
                report_detail = f"Nộp {ui_sent_at.strftime('%d/%m')} – Trễ {delay} ngày"
                submitted_for_bcnb = True
                verified_report_state = SUBMITTED_LATE_VERIFIED
                candidate["decision"] = "SUBMITTED_LATE"
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
                submitted_for_bcnb = True
                verified_report_state = SUBMITTED_LATE_VERIFIED
                candidate["decision"] = "SUBMITTED_LATE_WITHOUT_CLASS_DATE"
            else:
                candidate["decision"] = "FUTURE_UI_SENT_AT"
                classifier_result["candidates"].append(candidate)
                continue

        if target is not None and target_match is not None:
            _apply_target_match_context(
                date_context,
                target=target,
                match=target_match,
                claimed_lesson_date=claimed_lesson_date,
                claimed_lesson=reported_lesson,
            )
            if content_lesson is not None:
                date_context["content_lesson"] = content_lesson
            if reported_lesson_title:
                date_context["reported_lesson_title"] = reported_lesson_title
        elif target is None:
            if pending_prev_lesson_iso is not None:
                date_context.update(
                    {
                        "previous_lesson_date": pending_prev_lesson_iso,
                        "previous_lesson_source": "report_content_date",
                        "reference": pending_lesson_source or date_reference,
                    }
                )
            if pending_lesson_date is not None and pending_lesson_source is not None:
                _set_lesson_date_context(
                    date_context,
                    pending_lesson_date,
                    source=pending_lesson_source,
                    reported_lesson=reported_lesson,
                )
            if content_lesson is not None:
                date_context["content_lesson"] = content_lesson
            if reported_lesson_title:
                date_context["reported_lesson_title"] = reported_lesson_title

        if has_content_date_mismatch:
            candidate["warning"] = "CONTENT_DATE_MISMATCH"
            mismatch_warning = (
                "CONTENT_DATE_MISMATCH: ngày trong nội dung khác ngày gửi trên Zalo."
            )
            if mismatch_warning not in warnings:
                warnings.append(mismatch_warning)

        if target_match is not None:
            if target_match.corrected_teacher_date:
                candidate["decision"] = "SUBMITTED_DATE_TYPO"
            if target_match.note:
                report_detail = f"{report_detail}; {target_match.note}"

        report_found = True
        matched_message = message
        date_context["deadline_verification"] = "PENDING"
        candidate["delay_days"] = delay
        candidate["date_reference"] = date_reference
        classifier_result["candidates"].append(candidate)
        classifier_result.update(
            {
                "decision": candidate["decision"],
                "report_found": True,
                "matched_ui_sent_at": ui_sent_at.date().isoformat(),
            }
        )
        break

    if (
        not report_found
        and ai_report_classifier is not None
        and ai_candidate_pool
        and (target is not None or target_date is not None)
    ):
        if ai_assist_enabled is None:
            from googleform_autosubmit.config import Config

            use_ai_assist = bool(getattr(Config, "ZALO_AI_ASSIST_CLASSIFIER", False))
        else:
            use_ai_assist = bool(ai_assist_enabled)
        if use_ai_assist:
            ai_budget_key = _resolve_ai_assist_budget_key(
                group_name,
                target=target,
                scan_target=scan_target,
            )
            classifier_result["ai_assist_budget_key"] = ai_budget_key
            classifier_result["ai_assist_budget_exhausted"] = False
            for ai_msg, ai_raw, ai_cls in ai_candidate_pool:
                await ensure_not_cancelled()
                ai_sent_at = await _message_date_from_zalo_ui(ai_msg, today)
                if ai_sent_at is None or ai_sent_at.date() > today.date():
                    continue
                ai_reported_lesson = extract_report_lesson_number(ai_raw)
                ai_content_lesson = extract_report_content_lesson_number(ai_raw)
                ai_inferred_date = infer_report_lesson_date(
                    ai_cls["content_dates"],
                    ai_raw,
                    message_date=ai_sent_at,
                )
                ai_delay: int | None = None
                ai_date_ref = "no_class_date"
                if target is not None:
                    ai_delay = (ai_sent_at.date() - target.class_date.date()).days
                    ai_date_ref = "bcnb_target_date"
                    if ai_delay < 0 or ai_delay > BCNB_TICK_MAX_DELAY_DAYS:
                        continue
                    if ai_inferred_date is not None or ai_reported_lesson is not None:
                        ai_target_match = resolve_report_target(
                            target,
                            ui_sent_at=ai_sent_at,
                            claimed_lesson_date=ai_inferred_date,
                            claimed_lesson=ai_reported_lesson,
                            content_lesson=ai_content_lesson,
                        )
                        if not ai_target_match.matches:
                            continue
                elif target_date is not None:
                    ai_delay = (ai_sent_at.date() - target_date.date()).days
                    ai_date_ref = "legacy_class_date"
                    if ai_delay < 0 or ai_delay > BCNB_TICK_MAX_DELAY_DAYS:
                        continue

                if (
                    classifier_result["ai_assist_evaluated"] >= MAX_AI_ASSIST_CANDIDATES
                    or not await _try_claim_ai_assist_target_slot(job_id, ai_budget_key)
                ):
                    classifier_result["ai_assist_budget_exhausted"] = True
                    break
                classifier_result["ai_assist_evaluated"] += 1
                try:
                    ai_verdict = await ai_report_classifier.evaluate_ambiguous_message(
                        ai_raw,
                        group_name=group_name,
                        lesson=target.lesson if target is not None else "",
                        class_date_iso=(
                            target.class_date_iso
                            if target is not None
                            else (target_date.date().isoformat() if target_date is not None else "")
                        ),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    classifier_result["ai_assist_error"] = "AI_CLASSIFIER_ERROR"
                    ai_verdict = {
                        "is_likely_report": False,
                        "confidence": 0.0,
                        "reason": "AI_CLASSIFIER_ERROR",
                    }
                if (
                    isinstance(ai_verdict, Mapping)
                    and str(ai_verdict.get("reason") or "").strip() == "AI_CLASSIFIER_ERROR"
                ):
                    classifier_result["ai_assist_error"] = "AI_CLASSIFIER_ERROR"
                is_likely = bool(
                    isinstance(ai_verdict, Mapping)
                    and ai_verdict.get("is_likely_report") is True
                )
                ai_confidence = 0.0
                if isinstance(ai_verdict, Mapping):
                    try:
                        ai_confidence = max(
                            0.0,
                            min(1.0, float(ai_verdict.get("confidence", 0.0) or 0.0)),
                        )
                    except (TypeError, ValueError):
                        ai_confidence = 0.0
                ai_reason = (
                    str(ai_verdict.get("reason") or "").strip()[:160]
                    if isinstance(ai_verdict, Mapping)
                    else ""
                )
                ai_candidate: dict[str, Any] = {
                    "score": ai_cls["score"],
                    "matched_signals": ai_cls["matched_signals"],
                    "content_dates": [
                        content_date.date().isoformat()
                        for content_date in ai_cls["content_dates"]
                    ],
                    "ui_sent_at": ai_sent_at.date().isoformat(),
                    "message_fingerprint": hashlib.sha256(
                        ai_raw.strip().encode("utf-8")
                    ).hexdigest(),
                    "delay_days": ai_delay,
                    "date_reference": ai_date_ref,
                    "decision": (
                        AI_SUGGESTED_REPORT_DECISION
                        if is_likely
                        else "AI_ASSIST_REJECTED"
                    ),
                    "ai_assist": {
                        "is_likely_report": is_likely,
                        "confidence": ai_confidence,
                        "reason": ai_reason
                        or ("AI_SUGGESTED_REPORT" if is_likely else "AI_REJECTED"),
                    },
                }
                classifier_result["candidates"].append(ai_candidate)
                if is_likely:
                    # Safety boundary: AI assist NEVER sets report_found=True or
                    # submitted_for_bcnb=True.  It only flags the candidate for
                    # human inspection and captures the message screenshot.
                    report_found = False
                    submitted_for_bcnb = False
                    ai_suggested_message = ai_msg
                    classifier_result["decision"] = AI_SUGGESTED_REPORT_DECISION
                    classifier_result["matched_ui_sent_at"] = ai_sent_at.date().isoformat()
                    report_detail = (
                        f"AI gợi ý tin nhắn nộp {ai_sent_at.strftime('%d/%m')} "
                        "có thể là báo cáo học tập; cần kiểm tra thủ công"
                    )
                    break

    images: dict[str, dict[str, Any] | None] = {
        "report": None,
        "latest": None,
        "oldest": None,
        "oldest_message": None,
    }
    capture_diagnostics: list[dict[str, Any]] = []
    if report_found:
        images["report"] = await _capture_report_evidence_with_retry(
            page,
            matched_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            diagnostics=capture_diagnostics,
            worker_id=worker_id,
            attempt=attempt,
        )
    else:
        if ai_suggested_message is not None:
            images["report"] = await _capture_report_evidence_with_retry(
                page,
                ai_suggested_message,
                job_id=job_id,
                screenshots_dir=screenshots_dir,
                diagnostics=capture_diagnostics,
                worker_id=worker_id,
                attempt=attempt,
            )
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
            diagnostics=capture_diagnostics,
            worker_id=worker_id,
            attempt=attempt,
        )
        images["oldest"] = await _capture_zalo_evidence(
            page,
            oldest_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="oldest",
            date_header=oldest_date_header,
            require_date_header=True,
            diagnostics=capture_diagnostics,
            worker_id=worker_id,
            attempt=attempt,
        )
        images["oldest_message"] = await _capture_zalo_evidence(
            page,
            oldest_message,
            job_id=job_id,
            screenshots_dir=screenshots_dir,
            label="oldest-message",
            diagnostics=capture_diagnostics,
            worker_id=worker_id,
            attempt=attempt,
        )

    extra_reasons = []
    if not report_found and message_cap_reached:
        extra_reasons.append(MESSAGE_CAP_REACHED_REASON)
        if classifier_result.get("decision") == "NOT_FOUND":
            classifier_result["decision"] = "MESSAGE_CAP_REACHED"
    if not report_found and classifier_result["undated_report_candidates"]:
        extra_reasons.append("Có tin nhắn giống báo cáo nhưng không đọc được ngày gửi.")
    if not report_found and has_ambiguous_target_candidate:
        extra_reasons.append(
            "Có tin nhắn giống báo cáo nhưng chưa đủ căn cứ xác nhận thuộc buổi học target."
        )
    if not report_found and target_date is not None and messages:
        distinct_dates: list[datetime] = []
        for msg in messages:
            await ensure_not_cancelled()
            msg_date = await _message_date_from_zalo_ui(msg, today)
            if msg_date is not None:
                if not distinct_dates or msg_date.date() != distinct_dates[-1].date():
                    distinct_dates.append(msg_date)
        gap_info = detect_history_gap(distinct_dates, target_date)
        if gap_info is not None:
            d_prev, d_curr, gap_days = gap_info
            extra_reasons.append(
                f"Phát hiện khoảng trống tin nhắn quanh ngày học ({d_prev.strftime('%d/%m')} → {d_curr.strftime('%d/%m')}, cách {gap_days} ngày); lịch sử Zalo có thể chưa đồng bộ đầy đủ."
            )

    extra_reasons = list(dict.fromkeys(extra_reasons))
    warnings = list(dict.fromkeys(warnings))
    result = build_scan_result(
        group=group_name,
        person=person,
        actual_group_name=actual_group_name,
        scanned_at=scanned_at,
        messages_checked=messages_checked,
        classifier_result=classifier_result,
        required_window_start=required_window_start,
        required_window_end=required_window_end,
        oldest_message_date=oldest_message_date,
        newest_message_date=newest_message_date,
        images=images,
        report_found=report_found,
        report_detail=report_detail,
        submitted_for_bcnb=submitted_for_bcnb,
        extra_reasons=extra_reasons,
        warnings=warnings,
        verified_report_state=verified_report_state,
        date_context=date_context,
    )
    result.setdefault("evidence", {})["capture_diagnostics"] = capture_diagnostics
    if report_found:
        match_decision = str(date_context.get("target_match_decision") or "").strip()
        match_note = str(date_context.get("target_match_note") or "").strip()
        if match_decision == TARGET_SAME_DAY_NO_DATE and match_note:
            d2_issue = make_structured_issue(
                ISSUE_CODE_ZALO_REPORT_NO_DATE, match_note
            )
            result["zalo_content_issues"] = [d2_issue]
            result["quality_issues"] = [d2_issue]
        elif match_decision == TARGET_SAME_DAY_WRONG_DATE and match_note:
            d2_issue = make_structured_issue(
                ISSUE_CODE_ZALO_REPORT_WRONG_DATE, match_note
            )
            result["zalo_content_issues"] = [d2_issue]
            result["quality_issues"] = [d2_issue]
        else:
            zalo_issues = evaluate_zalo_content_issues(result)
            if zalo_issues:
                result["zalo_content_issues"] = list(zalo_issues)
                result["quality_issues"] = list(zalo_issues)
    return result
