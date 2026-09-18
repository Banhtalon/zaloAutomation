"""Week-aware BCNB matching and audited report checkbox actions."""

from collections.abc import Awaitable, Callable
from datetime import datetime
import re
from typing import Any

from core.time_utils import now_utc
from logger_utils import log_message
from repositories.job_repository import JobCancelledError
from services.bcnb_actions import (
    BcnbOutcomeUnknownError,
    execute_verified_checkbox_action,
)
from services.bcnb_safety import (
    resolve_report_columns,
    resolve_schedule_columns,
    select_week_checked,
)
from services.zalo_discovery_matching import find_bcnb_match
from services.zalo_evidence import (
    LESSON_14_EXEMPTION,
    apply_lesson_14_exemption,
    get_evidence_reference,
)
from services.zalo_safety import parse_class_date
from services.zalo_target_eligibility import (
    evaluate_scan_target,
    scan_target_identity,
)


def _parse_bcnb_date(value: str, *, today: datetime) -> datetime | None:
    """Extract a date from a BCNB cell such as ``18:00\n10/08/2026``."""
    iso_match = re.fullmatch(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*", value or "")
    if iso_match:
        try:
            return parse_class_date(
                f"{int(iso_match.group(3)):02d}/{int(iso_match.group(2)):02d}/{iso_match.group(1)}",
                default_year=today.year,
                timezone_info=today.tzinfo,
            )
        except (TypeError, ValueError):
            return None
    matches = re.findall(
        r"(?<!\d)(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?(?!\d)",
        value or "",
    )
    if not matches:
        return None
    day, month, year = matches[-1]
    year_value = year or str(today.year)
    if len(year_value) == 2:
        year_value = f"20{year_value}"
    try:
        return parse_class_date(
            f"{int(day):02d}/{int(month):02d}/{int(year_value):04d}",
            default_year=today.year,
            timezone_info=today.tzinfo,
        )
    except (TypeError, ValueError):
        return None


def _result_target_week(result: dict[str, Any]) -> tuple[int, int] | None:
    context = result.get("evidence", {}).get("date_context", {}) or {}
    try:
        week_value = context.get("target_week")
        year_value = context.get("target_year")
        if week_value is None or year_value is None:
            raise ValueError("missing target week")
        week = int(str(week_value))
        year = int(str(year_value))
        if 1 <= week <= 53 and 2000 < year <= 2100:
            return week, year
    except (TypeError, ValueError):
        return None
    return None


def _result_lesson_date(result: dict[str, Any]) -> str | None:
    context = result.get("evidence", {}).get("date_context", {}) or {}
    value = str(
        context.get("matched_lesson_date")
        or context.get("target_class_date")
        or context.get("lesson_date")
        or ""
    ).strip()
    return value or None


def _result_matches_schedule_row(
    result: dict[str, Any],
    row: dict[str, Any],
    pair: tuple[int, int],
) -> bool:
    """Match a report to one week/date row; never match by class code alone."""
    if not find_bcnb_match(result.get("group", ""), [row]):
        return False
    if _result_target_week(result) != pair:
        return False
    lesson_date = _result_lesson_date(result)
    if lesson_date and row.get("ngay_hoc_iso") and lesson_date != row["ngay_hoc_iso"]:
        return False
    target_lesson = str(
        (result.get("evidence", {}).get("date_context", {}) or {}).get(
            "target_lesson"
        )
        or ""
    ).strip()
    if target_lesson and target_lesson != str(row.get("buoi_so") or "").strip():
        return False
    return True


def _apply_lesson_14_result(
    scan_results: list[dict[str, Any]],
    row: dict[str, Any],
) -> dict[str, Any]:
    """Replace a stale/renamed lesson-14 scan result with one exemption row.

    A class can finish lesson 14 under its old Zalo name and be renamed before
    the next course starts.  The old configured group may therefore produce a
    fail-closed identity warning even though BCNB already exempts that row.
    Preserve the audit trail, but expose one business exemption instead of
    leaving a duplicate manual-review result beside it.
    """
    class_code = str(row.get("ma_lop") or "").strip()
    try:
        row_pair = (int(row["week"]), int(row["year"]))
    except (KeyError, TypeError, ValueError):
        row_pair = None
    if row_pair is not None:
        matches = [
            result
            for result in scan_results
            if _result_matches_schedule_row(result, row, row_pair)
        ]
    else:
        matches = [
            result
            for result in scan_results
            if find_bcnb_match(str(result.get("group") or ""), [row])
        ]
    if len(matches) > 1:
        raise RuntimeError(
            f"Dòng {class_code}/14 khớp nhiều kết quả Zalo; "
            "dừng để tránh gắn nhầm miễn báo cáo."
        )
    source = matches[0] if matches else None
    exempted = apply_lesson_14_exemption(
        source,
        class_code=class_code,
        applied_at=now_utc(),
    )
    if source is None:
        scan_results.append(exempted)
    else:
        scan_results[scan_results.index(source)] = exempted
    return exempted


def _apply_bcnb_deadline_state(
    result: dict[str, Any],
    row: dict[str, Any],
    *,
    today: datetime,
) -> bool:
    """Reconcile timing against the exact BCNB deadline row."""
    evidence = result.setdefault("evidence", {})
    context = evidence.setdefault("date_context", {})
    context.update(
        {
            "bcnb_class_date": row.get("ngay_hoc_iso"),
            "bcnb_zalo_deadline": row.get("zalo_deadline"),
        }
    )
    classifier = evidence.setdefault("classifier_result", {})
    sent_text = classifier.get("matched_ui_sent_at")
    sent_date = _parse_bcnb_date(str(sent_text or ""), today=today)
    deadline_date = _parse_bcnb_date(str(row.get("zalo_deadline") or ""), today=today)
    if sent_date is None or deadline_date is None:
        context["deadline_verification"] = "UNREADABLE"
        return False

    delay = (sent_date.date() - deadline_date.date()).days
    context.update(
        {
            "report_sent_at": sent_date.date().isoformat(),
            "deadline_date": deadline_date.date().isoformat(),
            "deadline_delay_days": delay,
            "deadline_verification": "DATE_LEVEL",
        }
    )
    candidates = classifier.get("candidates") or []
    if candidates:
        candidates[-1].update(
            {
                "deadline_date": deadline_date.date().isoformat(),
                "deadline_delay_days": delay,
            }
        )
    result["submitted_for_bcnb"] = True
    result["evidence_state"] = (
        "SUBMITTED_LATE_VERIFIED" if delay > 0 else "SUBMITTED_VERIFIED"
    )
    result["status"] = (
        "🟡 Đã nộp trễ – Có minh chứng" if delay > 0 else "✅ Đã nộp – Có minh chứng"
    )
    status_detail = (
        f"Nộp {sent_date.strftime('%d/%m')} – Trễ {delay} ngày"
        if delay > 0
        else f"Nộp {sent_date.strftime('%d/%m')} đúng hạn"
    )
    target_match_note = str(context.get("target_match_note") or "").strip()
    result["status_detail"] = (
        f"{status_detail}; {target_match_note}" if target_match_note else status_detail
    )
    classifier["decision"] = "SUBMITTED_LATE" if delay > 0 else "SUBMITTED_ON_TIME"
    return True


async def _read_bcnb_schedule_rows(
    page,
    *,
    week: int,
    year: int,
    job_id: int,
    today: datetime,
    job_repo,
) -> list[dict[str, Any]]:
    """Select and read one BCNB week into an auditable in-memory snapshot."""
    await job_repo.ensure_not_cancelled(job_id)
    await select_week_checked(page, week, year)
    class_index, lesson_index, date_index, deadline_index = await resolve_schedule_columns(page)
    rows = await page.locator("table tr").all()
    snapshot: list[dict[str, Any]] = []
    for row in rows:
        await job_repo.ensure_not_cancelled(job_id)
        cells = await row.locator("td").all()
        required_index = max(class_index, lesson_index, date_index, deadline_index)
        if len(cells) <= required_index:
            continue
        class_code = (await cells[class_index].inner_text()).strip()
        class_date = (await cells[date_index].inner_text()).strip()
        if not class_code or not class_date:
            continue
        parsed_class_date = _parse_bcnb_date(class_date, today=today)
        iso_date = parsed_class_date.date().isoformat() if parsed_class_date else None
        snapshot.append(
            {
                "cells": cells,
                "week": week,
                "year": year,
                "ma_lop": class_code,
                "buoi_so": (await cells[lesson_index].inner_text()).strip(),
                "ngay_hoc": class_date,
                "ngay_hoc_iso": iso_date,
                "zalo_deadline": (await cells[deadline_index].inner_text()).strip(),
            }
        )
    return snapshot


async def update_bcnb_reports(
    page,
    *,
    job_id: int,
    execution_mode: str,
    scan_results: list[dict[str, Any]],
    today: datetime,
    now: datetime | None = None,
    scan_plan: dict | None = None,
    job_repo,
    audit_repo,
    period_filter: set[tuple[int, int]] | None = None,
    should_halt: Callable[[], bool] | None = None,
    on_target_outcome: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Update both bounded weeks with exact-row matching and server read-back."""
    await job_repo.ensure_not_cancelled(job_id)
    if scan_plan:
        raw_weeks = scan_plan.get("selected_weeks") or []
        week_pairs = []
        for item in raw_weeks:
            if not isinstance(item, dict):
                continue
            try:
                pair = (int(item["week"]), int(item["year"]))
            except (KeyError, TypeError, ValueError):
                continue
            if pair not in week_pairs:
                week_pairs.append(pair)
        if len(week_pairs) != 2:
            raise RuntimeError("Kế hoạch scan BCNB phải có đúng hai tuần.")
        latest_block = scan_plan.get("latest_week") or {}
        try:
            latest_pair = (int(latest_block["week"]), int(latest_block["year"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Kế hoạch scan thiếu tuần BCNB mới nhất.") from exc
        active_pairs = [
            pair for pair in week_pairs if period_filter is None or pair in period_filter
        ]
        if period_filter is not None and not active_pairs:
            raise RuntimeError("Worker BCNB không được phân công tuần hợp lệ.")
    else:
        raise RuntimeError(
            "Không có kế hoạch tuần BCNB đã đọc từ máy chủ; từ chối suy tuần từ ngày hệ thống."
        )
    target_by_identity: dict[tuple[int, int, str, str, str], dict[str, Any]] = {}
    for source in (
        *(scan_plan.get("targets") or []),
        *(scan_plan.get("deferred_targets") or []),
    ):
        if not isinstance(source, dict):
            continue
        identity = scan_target_identity(source)
        if identity is not None:
            target_by_identity.setdefault(identity, source)
    current_time = now or today
    target_pairs: set[tuple[int, int]] = set()
    for result in scan_results:
        result_pair = _result_target_week(result)
        if result_pair is not None and result_pair in week_pairs:
            target_pairs.add(result_pair)
    # Buổi 14 is a business exemption and does not require a Zalo result.
    target_pairs.update(active_pairs)
    failures: list[dict[str, Any]] = []
    target_outcomes: list[dict[str, Any]] = []

    async def emit_outcome(item: dict[str, Any]) -> None:
        target_outcomes.append(item)
        if on_target_outcome is not None:
            await on_target_outcome(item)

    updated_count = 0
    log_message(
        "Đối chiếu BCNB theo các tuần: "
        + ", ".join(f"W{week}/{year}" for week, year in active_pairs)
    )

    for selected_week, selected_year in active_pairs:
        await job_repo.ensure_not_cancelled(job_id)
        if (selected_week, selected_year) not in target_pairs:
            continue
        bcnb_rows = await _read_bcnb_schedule_rows(
            page,
            week=selected_week,
            year=selected_year,
            job_id=job_id,
            today=today,
            job_repo=job_repo,
        )
        _, _, report_index = await resolve_report_columns(page)
        is_current_week = (selected_week, selected_year) == latest_pair
        log_message(
            f"Đã kiểm chứng BCNB ở Tuần {selected_week} / {selected_year}: "
            f"{len(bcnb_rows)} dòng."
        )

        for bcnb_row in bcnb_rows:
            await job_repo.ensure_not_cancelled(job_id)
            if is_current_week:
                row_identity = scan_target_identity(
                    {
                        "week": selected_week,
                        "year": selected_year,
                        "class_code": bcnb_row.get("ma_lop"),
                        "lesson": bcnb_row.get("buoi_so"),
                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                    }
                )
                scheduled_target = (
                    target_by_identity.get(row_identity) if row_identity is not None else None
                )
                if scheduled_target is None:
                    # A current-day or future row without an immutable target
                    # cannot be safely timed or matched to a Zalo result.
                    if (
                        not bcnb_row.get("ngay_hoc_iso")
                        or str(bcnb_row["ngay_hoc_iso"]) >= current_time.date().isoformat()
                    ):
                        log_message(
                            f"Bỏ qua {bcnb_row['ma_lop']}/{bcnb_row['buoi_so']}: "
                            "chưa có target Zalo đủ giờ học để đối chiếu.",
                            level="INFO",
                        )
                        continue
                else:
                    eligibility = evaluate_scan_target(
                        scheduled_target,
                        latest_week=latest_pair,
                        now=current_time,
                    )
                    if not eligibility.eligible:
                        log_message(
                            f"Bỏ qua {bcnb_row['ma_lop']}/{bcnb_row['buoi_so']}: "
                            f"{eligibility.reason}; chưa quét và không tick BCNB.",
                            level="INFO",
                        )
                        continue
            if execution_mode == "production":
                refreshed_rows = await _read_bcnb_schedule_rows(
                    page,
                    week=selected_week,
                    year=selected_year,
                    job_id=job_id,
                    today=today,
                    job_repo=job_repo,
                )
                _, _, report_index = await resolve_report_columns(page)
                refreshed = next(
                    (
                        item
                        for item in refreshed_rows
                        if item["ma_lop"].strip().upper()
                        == bcnb_row["ma_lop"].strip().upper()
                        and item["buoi_so"].strip() == bcnb_row["buoi_so"].strip()
                        and item.get("ngay_hoc_iso") == bcnb_row.get("ngay_hoc_iso")
                    ),
                    None,
                )
                if refreshed is None:
                    log_message(
                        f"Không tìm lại được dòng BCNB {bcnb_row['ma_lop']}/{bcnb_row['buoi_so']}; "
                        "bỏ qua để kiểm tra thủ công.",
                        level="WARNING",
                    )
                    continue
                bcnb_row = refreshed

            cells = bcnb_row["cells"]
            class_code = bcnb_row["ma_lop"].strip().upper()
            lesson = bcnb_row["buoi_so"].strip()
            is_lesson_14 = lesson == "14"
            result_matches = [
                result
                for result in scan_results
                if _result_matches_schedule_row(
                    result,
                    bcnb_row,
                    (selected_week, selected_year),
                )
                and result.get("evidence_state")
                in {"SUBMITTED_VERIFIED", "SUBMITTED_LATE_VERIFIED"}
            ]
            if len(result_matches) > 1:
                raise RuntimeError(
                    f"Dòng {class_code}/{lesson} khớp nhiều báo cáo Zalo; "
                    "dừng để tránh tick nhầm."
                )
            matched_result = result_matches[0] if result_matches else None
            is_submitted = False

            if is_lesson_14:
                is_submitted = True
                matched_result = _apply_lesson_14_result(scan_results, bcnb_row)
                log_message(
                    f"Lớp {class_code}: Buổi 14 được MIỄN BÁO CÁO. "
                    "Đã ghi LESSON_14_EXEMPTION."
                )
            elif matched_result is not None:
                is_submitted = _apply_bcnb_deadline_state(
                    matched_result,
                    bcnb_row,
                    today=today,
                )
                if not is_submitted:
                    log_message(
                        f"Bỏ qua {class_code}/{lesson}: không đọc được deadline "
                        "Zalo của đúng dòng BCNB.",
                        level="WARNING",
                    )

            if not is_submitted:
                continue
            checkbox = cells[report_index].locator('input[type="checkbox"]').first
            if await checkbox.count() == 0:
                raise RuntimeError(f"BCNB không có checkbox báo cáo cho {class_code}/{lesson}.")
            is_checked = await checkbox.is_checked()
            if is_checked and not is_lesson_14:
                await emit_outcome(
                    {
                        "class_code": class_code,
                        "lesson": lesson,
                        "week": selected_week,
                        "year": selected_year,
                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                        "status": "ALREADY_DONE",
                    }
                )
                continue

            if should_halt is not None and should_halt():
                await emit_outcome(
                    {
                        "class_code": class_code,
                        "lesson": lesson,
                        "week": selected_week,
                        "year": selected_year,
                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                        "status": "BLOCKED_BATCH_HALT",
                        "error": "Batch đã halt trước click; target không bị thay đổi.",
                    }
                )
                continue

            evidence = (matched_result or {}).get("evidence", {})
            context = evidence.get("date_context", {}) or {}
            classifier = evidence.get("classifier_result", {}) or {}
            candidates = classifier.get("candidates") or []
            candidate = candidates[-1] if candidates else {}
            evidence_path, evidence_sha256 = get_evidence_reference(matched_result)
            try:
                outcome = await execute_verified_checkbox_action(
                    page=page,
                    checkbox=checkbox,
                    audit_repo=audit_repo,
                    job_repo=job_repo,
                    job_id=job_id,
                    action_key=(
                        f"bcnb-report:{job_id}:{selected_year}:W{selected_week}:"
                        f"{class_code}:{lesson}"
                    ),
                    action_type="BCNB_REPORT_TICK",
                    class_code=class_code,
                    column_kind="report",
                    week=selected_week,
                    year=selected_year,
                    lesson=lesson,
                    execution_mode=execution_mode,
                    reason={
                        "week": selected_week,
                        "year": selected_year,
                        "lesson": lesson,
                        "class_date": bcnb_row.get("ngay_hoc_iso"),
                        "zalo_deadline": bcnb_row.get("zalo_deadline"),
                        "report_sent_at": context.get("report_sent_at")
                        or classifier.get("matched_ui_sent_at"),
                        "delay_days": context.get("deadline_delay_days")
                        if context.get("deadline_delay_days") is not None
                        else candidate.get("delay_days"),
                        "message_fingerprint": candidate.get("message_fingerprint"),
                        "target_week_source": context.get("target_week_source"),
                        "target_match_decision": context.get("target_match_decision"),
                        "target_match_note": context.get("target_match_note"),
                        "reported_lesson_date": context.get("reported_lesson_date"),
                        "reported_lesson": context.get("reported_lesson"),
                        "matched_lesson_date": context.get("matched_lesson_date"),
                        "matched_lesson": context.get("matched_lesson"),
                        "submitted": is_submitted,
                        "evidence_state": (
                            matched_result.get("evidence_state") if matched_result else None
                        ),
                        "lesson_14_exemption": is_lesson_14,
                        "evidence_decision": LESSON_14_EXEMPTION if is_lesson_14 else None,
                    },
                    evidence_path=evidence_path,
                    evidence_sha256=evidence_sha256,
                    require_evidence_registry=True,
                    settle_seconds=0.7,
                    should_halt=should_halt,
                )
            except JobCancelledError:
                raise
            except Exception as action_error:
                unknown = isinstance(action_error, BcnbOutcomeUnknownError)
                failure = {
                    "class_code": class_code,
                    "lesson": lesson,
                    "week": selected_week,
                    "year": selected_year,
                    "error": str(action_error)[:500],
                }
                failures.append(failure)
                await emit_outcome(
                    {
                        **failure,
                        "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                        "status": (
                            "OUTCOME_UNKNOWN"
                            if execution_mode == "production" and unknown
                            else "FAILED_BEFORE_WRITE"
                        ),
                    }
                )
                if matched_result is not None:
                    matched_result["status"] = (
                        "⚠️ BCNB chưa cập nhật – Cần kiểm tra thủ công"
                    )
                    matched_result["status_detail"] = (
                        f"BCNB W{selected_week}/{selected_year}, buổi {lesson}: "
                        "ghi thất bại; xem audit để xử lý."
                    )
                    matched_result["bcnb_update_state"] = "FAILED"
                log_message(
                    f"Bỏ qua lỗi cập nhật BCNB {class_code}/{lesson} "
                    f"W{selected_week}/{selected_year}; tiếp tục lớp kế tiếp: {action_error}",
                    level="ERROR",
                    event="bcnb.update_row_failed",
                    class_code=class_code,
                    lesson=lesson,
                    week=selected_week,
                    year=selected_year,
                )
                continue
            updated_count += 1
            await emit_outcome(
                {
                    "class_code": class_code,
                    "lesson": lesson,
                    "week": selected_week,
                    "year": selected_year,
                    "class_date_iso": bcnb_row.get("ngay_hoc_iso"),
                    "status": outcome,
                }
            )
            if outcome == "DRY_RUN":
                log_message(
                    f"[DRY-RUN] Sẽ Tick xanh cho lớp: {class_code} "
                    f"W{selected_week}/{selected_year}"
                    + (" (Miễn báo cáo Buổi 14)" if is_lesson_14 else "")
                )
            else:
                log_message(
                    f"Đã tick và kiểm chứng BCNB cho lớp: {class_code} "
                    f"W{selected_week}/{selected_year}"
                )

    if failures:
        log_message(
            f"Đối chiếu BCNB hoàn tất với {len(failures)} dòng lỗi; "
            "các lớp còn lại đã được xử lý.",
            level="WARNING",
            event="bcnb.update_partial",
            failed_count=len(failures),
        )
    else:
        log_message("ĐÃ ĐỐI CHIẾU TOÀN BỘ CÁC TUẦN BCNB THÀNH CÔNG!")
    return {
        "selected_weeks": [
            {"week": week, "year": year} for week, year in active_pairs
        ],
        "updated_count": updated_count,
        "failed_targets": failures,
        "target_outcomes": target_outcomes,
    }
