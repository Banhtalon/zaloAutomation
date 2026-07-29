import asyncio

from googleform_autosubmit.config import Config
from logger_utils import log_message
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobRepository
from services.bcnb_safety import resolve_class_lesson_columns, select_last_week_checked
from services.browser_job_guard import exclusive_browser_job
from services.external_gateways import ExternalGateways
from services.zalo_discovery_matching import find_bcnb_match
from services.zalo_discovery_plan import build_discovery_plan


group_repo = GroupRepository()
job_repo = JobRepository()


class DiscoverySafetyError(RuntimeError):
    pass


def _build_discovery_plan(
    group_names: list[str],
    bcnb_data: list[dict[str, str]],
    ignored_groups: list[str],
    existing_groups: list[dict],
) -> dict:
    return build_discovery_plan(
        group_names,
        bcnb_data,
        ignored_groups,
        existing_groups,
        match_group=find_bcnb_match,
    )


async def _login_bcnb_if_needed(page) -> None:
    username_input = page.locator('input[name="username"]').first
    if await username_input.count() > 0 and await username_input.is_visible():
        log_message("Hệ thống yêu cầu đăng nhập BCNB. Đang tự động điền tài khoản...")
        password_input = page.locator('input[name="password"]').first
        submit_button = page.locator(
            'button[type="submit"]:visible, input[type="submit"]:visible'
        ).first
        if (
            await password_input.count() == 0
            or await submit_button.count() == 0
        ):
            raise DiscoverySafetyError(
                "Biểu mẫu đăng nhập BCNB đã thay đổi; không thể tiếp tục an toàn."
            )
        await username_input.fill(Config.BCNB_USERNAME)
        await password_input.fill(Config.BCNB_PASSWORD)
        await submit_button.click()
        try:
            await page.wait_for_load_state("networkidle")
        except PlaywrightTimeoutError:
            # Một số trang BCNB giữ kết nối nền; hậu kiểm DOM bên dưới vẫn quyết định.
            pass
        if await username_input.is_visible():
            log_message(
                "Đăng nhập BCNB không thành công; biểu mẫu đăng nhập vẫn hiển thị.",
                level="ERROR",
                error_type="BCNBLoginFailed",
            )
            raise DiscoverySafetyError(
                "Đăng nhập BCNB không thành công; dữ liệu cũ được giữ nguyên."
            )


async def _collect_bcnb_data(page, job_id: int) -> list[dict[str, str]]:
    log_message("Truy cập trang check nhận xét BCNB...")
    await job_repo.ensure_not_cancelled(job_id)
    await page.goto(Config.BCNB_URL)
    await _login_bcnb_if_needed(page)

    try:
        week, year = await select_last_week_checked(page)
    except RuntimeError as exc:
        log_message(
            "Không kiểm chứng được tuần/năm BCNB trong Auto Discovery.",
            level="ERROR",
            error_type=type(exc).__name__,
            reason=str(exc),
        )
        raise DiscoverySafetyError(
            "Không kiểm chứng được tuần/năm BCNB."
        ) from exc
    log_message(f"Đã kiểm chứng BCNB ở Tuần {week} / {year} trước khi đồng bộ.")

    try:
        await page.wait_for_selector("table", timeout=60000)
        await asyncio.sleep(2)
        class_index, lesson_index = await resolve_class_lesson_columns(page)
        rows = await page.locator("table tr").all()
        result = []
        for row in rows:
            await job_repo.ensure_not_cancelled(job_id)
            cells = await row.locator("td").all()
            if len(cells) <= max(class_index, lesson_index, 3):
                continue
            class_code = (await cells[class_index].inner_text()).strip()
            if class_code:
                result.append(
                    {
                        "ma_lop": class_code,
                        "buoi_so": (await cells[lesson_index].inner_text()).strip(),
                        "ngay_hoc": (await cells[2].inner_text()).strip(),
                        "giao_vien": (await cells[3].inner_text()).strip(),
                    }
                )
    except Exception as exc:
        log_message(
            "Không thể xác minh cấu trúc bảng BCNB cho Discovery.",
            level="ERROR",
            error_type=type(exc).__name__,
            reason=str(exc) if isinstance(exc, RuntimeError) else None,
        )
        raise DiscoverySafetyError("Không thể đọc bảng dữ liệu BCNB.") from exc
    log_message(f"Đã lấy thành công {len(result)} lớp từ trang BCNB.")
    return result


async def _collect_zalo_group_names(page, job_id: int) -> list[str]:
    await job_repo.update_job(
        job_id,
        message="Đang quét danh sách nhóm trên Zalo...",
        progress=40,
    )
    log_message("Đang chuyển sang Zalo Web...")
    await page.goto("https://chat.zalo.me/")
    await page.wait_for_selector("#contact-search-input", timeout=120000)

    search = page.locator("#contact-search-input")
    await search.fill("MindX -")
    await asyncio.sleep(2)

    contacts_tab = page.get_by_text("Liên hệ", exact=True)
    if await contacts_tab.count() > 0:
        await contacts_tab.first.click()
        await asyncio.sleep(2)

    show_groups = page.get_by_text("Xem tất cả nhóm")
    if await show_groups.count() > 0:
        await show_groups.first.click()
        await asyncio.sleep(2)

    group_names = set()
    await page.mouse.move(150, 400)
    previous_count = 0
    for index in range(35):
        await job_repo.ensure_not_cancelled(job_id)
        for line in (await page.evaluate("document.body.innerText")).split("\n"):
            clean_line = line.strip().replace("\xa0", " ")
            if (
                clean_line.upper().startswith("MINDX")
                and "-" in clean_line
                and len(clean_line) > 10
            ):
                group_names.add(clean_line)
        await page.mouse.wheel(0, 800)
        await asyncio.sleep(0.5)
        if len(group_names) == previous_count and index > 5:
            break
        previous_count = len(group_names)
    result = sorted(group_names)
    log_message(f"Đã thu thập được {len(result)} tên nhóm từ Zalo.")
    return result


async def _persist_discovery(job_id: int, group_names: list[str], plan: dict) -> None:
    try:
        await group_repo.apply_discovery_snapshot(plan)
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
    await job_repo.update_job(
        job_id,
        status="SUCCESS",
        message=f"Hoàn tất! Đã đồng bộ {len(group_names)} nhóm.",
        progress=100,
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
            bcnb_data = await _collect_bcnb_data(page, job_id)
            group_names = await _collect_zalo_group_names(page, job_id)
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
    existing_groups = await group_repo.get_all_groups()
    plan = _build_discovery_plan(
        group_names,
        bcnb_data,
        ignored_groups,
        existing_groups,
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
    ):
        await _run_auto_discovery(
            job_id,
            external=(
                runtime.external
                if runtime
                else ExternalGateways.production()
            ),
        )
