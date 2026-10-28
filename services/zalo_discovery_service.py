import asyncio
import contextlib
import json
import re
from datetime import datetime

from googleform_autosubmit.config import Config
from logger_utils import log_message
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobRepository
from core.time_utils import previous_iso_week
from services.bcnb_safety import (
    dismiss_current_week_notice,
    read_current_week_checked,
    resolve_instructor_column,
    resolve_schedule_columns,
    select_week_checked,
)
from services.browser_job_guard import exclusive_browser_job
from services.external_gateways import ExternalGateways
from services.zalo_discovery_collection import (
    BcnbClassStatusError,
    collect_bcnb_class_states,
    collect_zalo_group_names,
)
from services.zalo_discovery_matching import find_bcnb_match
from services.zalo_discovery_plan import build_discovery_plan


group_repo = GroupRepository()
job_repo = JobRepository()


class DiscoverySafetyError(RuntimeError):
    pass


def _schedule_date_iso(value: str) -> str | None:
    """Extract an ISO lesson date from a BCNB date cell."""
    matches = re.findall(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?!\d)", value or "")
    if not matches:
        return None
    day, month, year = matches[-1]
    year_value = int(year) if len(year) == 4 else 2000 + int(year)
    try:
        return datetime(year_value, int(month), int(day)).date().isoformat()
    except ValueError:
        return None


def _build_discovery_plan(
    group_names: list[str], bcnb_data: list[dict[str, str]],
    ignored_groups: list[str], existing_groups: list[dict],
    *, bcnb_week_plan: dict | None = None,
    bcnb_class_states: list[dict[str, str]] | None = None,
    now: datetime | None = None,
) -> dict:
    return build_discovery_plan(
        group_names, bcnb_data, ignored_groups, existing_groups,
        match_group=find_bcnb_match, bcnb_week_plan=bcnb_week_plan,
        bcnb_class_states=bcnb_class_states, now=now,
    )


async def _login_bcnb_if_needed(page) -> None:
    username_input = page.locator('input[name="username"]').first
    if await username_input.count() > 0 and await username_input.is_visible():
        log_message("Hệ thống yêu cầu đăng nhập BCNB. Đang tự động điền tài khoản...")
        password_input = page.locator('input[name="password"]').first
        submit_button = page.locator('button[type="submit"]:visible, input[type="submit"]:visible').first
        if await password_input.count() == 0 or await submit_button.count() == 0:
            raise DiscoverySafetyError("Biểu mẫu đăng nhập BCNB đã thay đổi; không thể tiếp tục an toàn.")
        await username_input.fill(Config.BCNB_USERNAME)
        await password_input.fill(Config.BCNB_PASSWORD)
        remember = page.locator('input[name="remember"]').first
        if await remember.count() > 0:
            with contextlib.suppress(Exception):
                if not await remember.is_checked():
                    await remember.check()
        await submit_button.click()
        if hasattr(page, "wait_for_url"):
            with contextlib.suppress(PlaywrightTimeoutError):
                await page.wait_for_url(lambda u: "login.php" not in str(u).lower(), timeout=15000)
        if hasattr(page, "wait_for_load_state"):
            with contextlib.suppress(PlaywrightTimeoutError):
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
        current_url = getattr(page, "url", "") or ""
        if "login.php" in str(current_url).lower() and await username_input.is_visible():
            log_message(
                "Đăng nhập BCNB không thành công; biểu mẫu đăng nhập vẫn hiển thị.",
                level="ERROR",
                error_type="BCNBLoginFailed",
            )
            raise DiscoverySafetyError("Đăng nhập BCNB không thành công; dữ liệu cũ được giữ nguyên.")
        log_message("Đăng nhập BCNB hoàn tất.")


async def _collect_bcnb_data(page, job_id: int) -> list[dict[str, str]]:
    """Backward-compatible wrapper returning the latest BCNB week rows."""
    period = await _collect_bcnb_week_plan(page, job_id)
    return period["latest_data"]


async def _read_bcnb_week_rows(
    page,
    job_id: int,
    *,
    week: int,
    year: int,
    already_selected: bool = False,
) -> list[dict[str, str]]:
    if not already_selected:
        await select_week_checked(page, week, year)
    await dismiss_current_week_notice(page)
    await page.wait_for_selector("table", timeout=60000)
    await asyncio.sleep(1)
    class_index, lesson_index, date_index, deadline_index = await resolve_schedule_columns(page)
    instructor_index = await resolve_instructor_column(page)
    rows = await page.locator("table tr").all()
    result: list[dict[str, str]] = []
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
        instructor_name = (
            (await cells[instructor_index].inner_text()).strip()
            if instructor_index is not None and len(cells) > instructor_index
            else ""
        )
        result.append(
            {
                "ma_lop": class_code,
                "buoi_so": (await cells[lesson_index].inner_text()).strip(),
                "ngay_hoc": class_date,
                "ngay_hoc_iso": _schedule_date_iso(class_date) or "",
                "giao_vien": instructor_name,
                "zalo_deadline": (await cells[deadline_index].inner_text()).strip(),
                "week": str(week),
                "year": str(year),
            }
        )
    log_message(f"Đã lấy thành công {len(result)} dòng BCNB W{week}/{year}.")
    return result


async def _collect_bcnb_week_plan(page, job_id: int) -> dict:
    """Read BCNB's current week and its required predecessor from BCNB itself."""
    log_message("Truy cập trang check nhận xét BCNB...")
    await job_repo.ensure_not_cancelled(job_id)
    try:
        await page.goto(Config.BCNB_URL)
        await _login_bcnb_if_needed(page)
        await dismiss_current_week_notice(page)
        latest_week, latest_year = await read_current_week_checked(page)
        required_week, required_year = previous_iso_week(latest_week, latest_year)
    except DiscoverySafetyError:
        raise
    except RuntimeError as exc:
        log_message(
            "Không thể mở hoặc đọc BCNB cho Auto Discovery.",
            level="ERROR",
            event="discovery.bcnb_read_failed",
            error_type=type(exc).__name__,
        )
        raise DiscoverySafetyError(
            "Không thể mở hoặc đọc BCNB; hãy kiểm tra kết nối và phiên đăng nhập rồi bấm Dò tìm lại."
        ) from exc
    except ValueError as exc:
        raise DiscoverySafetyError("BCNB trả về cặp tuần/năm không hợp lệ.") from exc
    except Exception as exc:
        log_message(
            "Không thể mở hoặc đọc BCNB cho Auto Discovery.",
            level="ERROR",
            event="discovery.bcnb_read_failed",
            error_type=type(exc).__name__,
        )
        raise DiscoverySafetyError(
            "Không thể mở hoặc đọc BCNB; hãy kiểm tra giao diện và phiên đăng nhập rồi bấm Dò tìm lại."
        ) from exc
    log_message(
        f"Đã kiểm chứng BCNB: tuần mới nhất W{latest_week}/{latest_year}; "
        f"tuần phải quét W{required_week}/{required_year}."
    )

    try:
        latest_data = await _read_bcnb_week_rows(
            page, job_id, week=latest_week, year=latest_year, already_selected=True
        )
        required_data = await _read_bcnb_week_rows(
            page, job_id, week=required_week, year=required_year
        )
    except Exception as exc:
        log_message(
            "Không thể xác minh cấu trúc bảng BCNB cho Discovery.",
            level="ERROR",
            error_type=type(exc).__name__,
            reason=str(exc) if isinstance(exc, RuntimeError) else None,
        )
        raise DiscoverySafetyError("Không thể đọc bảng dữ liệu BCNB.") from exc
    return {
        "latest_week": {"week": latest_week, "year": latest_year},
        "required_week": {"week": required_week, "year": required_year},
        "selected_weeks": [
            {"week": required_week, "year": required_year},
            {"week": latest_week, "year": latest_year},
        ],
        "latest_data": latest_data,
        "required_data": required_data,
    }


async def _collect_bcnb_class_states(page, job_id: int) -> list[dict[str, str]]:
    try:
        return await collect_bcnb_class_states(
            page,
            job_id,
            job_repo=job_repo,
            status_url=Config.BCNB_CLASS_STATUS_URL,
        )
    except BcnbClassStatusError as exc:
        raise DiscoverySafetyError(str(exc)) from exc


async def _collect_zalo_group_names(page, job_id: int) -> list[str]:
    return await collect_zalo_group_names(page, job_id, job_repo=job_repo)


async def _persist_discovery(job_id: int, group_names: list[str], plan: dict) -> None:
    try:
        promotion = await group_repo.apply_discovery_snapshot(plan)
    except Exception as exc:
        log_message(
            "Lưu kết quả Auto Discovery thất bại.",
            level="ERROR",
            error_type=type(exc).__name__,
        )
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message="Lỗi: Không thể ghi Database.",
            progress=100,
        )
        return
    if isinstance(promotion, dict) and promotion.get("status") == "REJECTED":
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message="Dừng an toàn: snapshot không đủ tin cậy; dữ liệu cũ được giữ nguyên.",
            progress=100,
        )
        return
    if isinstance(promotion, dict) and isinstance(promotion.get("diff"), dict):
        diff = promotion["diff"]
        log_message(
            "Dry-run diff discovery đã được tạo trước promotion.",
            event="discovery.diff",
            promotion=promotion.get("status", "OBSERVED_ONLY"),
            added_count=len(diff.get("added_group_names", [])),
            updated_count=len(diff.get("updated_group_names", [])),
            not_seen_count=len(diff.get("not_seen_group_names", [])),
            diff=diff,
        )
    promotion_status = (
        promotion.get("status", "OBSERVED_ONLY")
        if isinstance(promotion, dict)
        else "OBSERVED_ONLY"
    )
    auto_finished_count = (
        int(promotion.get("auto_finished_count", 0))
        if isinstance(promotion, dict)
        else 0
    )
    if auto_finished_count:
        log_message(
            f"Đã tự xác nhận {auto_finished_count} lớp có trạng thái Finished "
            "trên BCNB.",
            event="class_lifecycle.bcnb_finished_observed",
            auto_finished_count=auto_finished_count,
        )
    result_payload = {
        "kind": "zalo_discovery",
        "snapshot_id": (
            promotion.get("snapshot_id") if isinstance(promotion, dict) else None
        ),
        "latest_week": plan.get("bcnb_week_plan", {}).get("latest_week"),
        "required_week": plan.get("bcnb_week_plan", {}).get("required_week"),
        "selected_weeks": plan.get("bcnb_week_plan", {}).get("selected_weeks", []),
        "auto_finished_count": auto_finished_count,
    }
    await job_repo.update_job(
        job_id,
        status="SUCCESS",
        message=(
            f"Hoàn tất! Đã đồng bộ {len(group_names)} nhóm; "
            f"tự xác nhận {auto_finished_count} lớp Finished; "
            f"promotion={promotion_status}."
        ),
        progress=100,
        results=json.dumps(result_payload, ensure_ascii=False),
    )
    log_message("Đồng bộ danh sách thành công và lưu vào Database SQLite!")


async def _run_auto_discovery(
    job_id: int,
    *,
    external: ExternalGateways | None = None,
) -> None:
    await job_repo.update_job(
        job_id,
        status="RUNNING",
        message="Đang lấy dữ liệu từ hệ thống BCNB...",
        progress=10,
    )
    log_message("Bắt đầu tiến trình dò tìm nhóm MindX từ BCNB...")
    selected = external or ExternalGateways.production()
    try:
        async with selected.browser.persistent_page() as page:
            bcnb_week_plan = await _collect_bcnb_week_plan(page, job_id)
            bcnb_data = bcnb_week_plan["latest_data"]
            try:
                bcnb_class_states = await _collect_bcnb_class_states(page, job_id)
            except DiscoverySafetyError as exc:
                bcnb_class_states = None
                log_message(
                    "Không có evidence trạng thái Finished; bỏ qua tự kết thúc lớp.",
                    level="WARNING",
                    event="discovery.bcnb_class_status_unavailable",
                    error_type=type(exc).__name__,
                )
            try:
                group_names = await _collect_zalo_group_names(page, job_id)
            except Exception as exc:
                log_message("Zalo Discovery lỗi.", level="ERROR", error_type=type(exc).__name__)
                raise DiscoverySafetyError(
                    "Không thể đọc danh sách nhóm Zalo; hãy kiểm tra phiên Zalo và tab trùng rồi bấm Dò tìm lại."
                ) from exc
    except DiscoverySafetyError as exc:
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message=f"Dừng an toàn: {exc}",
            progress=100,
        )
        return

    await job_repo.update_job(
        job_id,
        message="Đang đồng bộ giáo viên...",
        progress=80,
    )
    ignored_groups = await group_repo.get_all_ignored_groups(include_archived=True)
    existing_groups = await group_repo.get_scan_groups()
    plan = _build_discovery_plan(
        group_names,
        bcnb_data,
        ignored_groups,
        existing_groups,
        bcnb_week_plan=bcnb_week_plan,
        bcnb_class_states=bcnb_class_states,
    )
    if not plan["reliable"]:
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message=(
                "Dừng an toàn: kết quả dò tìm rỗng hoặc không khớp; "
                "dữ liệu cũ được giữ nguyên."
            ),
            progress=100,
        )
        return
    await _persist_discovery(job_id, group_names, plan)


async def run_auto_discovery(job_id: int, *, runtime=None) -> None:
    async with exclusive_browser_job(
        job_id,
        job_repo,
        lock=runtime.browser_lock if runtime else None,
        process_lock=getattr(runtime, "profile_lock", None) if runtime else None,
    ):
        await _run_auto_discovery(
            job_id,
            external=(
                runtime.external
                if runtime
                else ExternalGateways.production()
            ),
        )
