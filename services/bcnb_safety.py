"""Shared, fail-closed helpers for reading the BCNB table."""

import re
import unicodedata

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.time_utils import previous_business_week


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def _canonical_class_code(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").strip().upper()
    normalized = normalized.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", normalized)


async def select_week_checked(page, week: int, year: int) -> tuple[int, int]:
    if not isinstance(week, int) or not 1 <= week <= 53:
        raise RuntimeError("Tuần BCNB không hợp lệ.")
    if not isinstance(year, int) or not 2000 < year <= 2100:
        raise RuntimeError("Năm BCNB không hợp lệ.")
    await page.wait_for_selector("table", timeout=60000)
    selected = await page.evaluate(
        """
        ([targetWeek, targetYear]) => {
            const inputs = Array.from(document.querySelectorAll('input'));
            const descriptor = i => [i.name, i.id, i.placeholder, i.getAttribute('aria-label')]
                .filter(Boolean).join(' ').toLowerCase();
            const chooseInput = (semantic, numeric) => {
                if (semantic.length === 1) return semantic[0];
                if (semantic.length > 1 || numeric.length !== 1) return null;
                return numeric[0];
            };
            const weekNumeric = inputs.filter(i => {
                const n = Number.parseInt(i.value, 10);
                return n > 0 && n <= 53;
            });
            const yearNumeric = inputs.filter(i => {
                const n = Number.parseInt(i.value, 10);
                return n > 2000 && n <= 2100;
            });
            const weekInput = chooseInput(
                inputs.filter(i => /(^|[^a-z])(week|tuan)([^a-z]|$)/.test(descriptor(i))),
                weekNumeric,
            );
            const yearInput = chooseInput(
                inputs.filter(i => /(^|[^a-z])(year|nam)([^a-z]|$)/.test(descriptor(i))),
                yearNumeric,
            );
            const controls = Array.from(document.querySelectorAll(
                'button, input[type="submit"]'
            ));
            const acceptedLabels = /^(xem|lọc lịch)$/i;
            const buttons = controls.filter(control => {
                const label = (control.innerText || control.value || '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                return acceptedLabels.test(label) && control.offsetParent !== null;
            });
            const button = buttons.length === 1 ? buttons[0] : null;
            if (!weekInput || !yearInput || !button) return false;
            weekInput.value = String(targetWeek);
            yearInput.value = String(targetYear);
            for (const input of [weekInput, yearInput]) {
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
            button.click();
            return true;
        }
        """,
        [week, year],
    )
    if not selected:
        raise RuntimeError(
            "Không xác định được ô tuần/năm hoặc nút Xem/Lọc lịch trên BCNB."
        )
    try:
        await page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        # Một số trang giữ kết nối nền; table/read-back bên dưới vẫn là điều kiện quyết định.
        pass
    await page.wait_for_selector("table", timeout=60000)
    await dismiss_current_week_notice(page)
    values = await page.evaluate(
        """
        () => {
            const inputs = Array.from(document.querySelectorAll('input'));
            const descriptor = i => [i.name, i.id, i.placeholder, i.getAttribute('aria-label')]
                .filter(Boolean).join(' ').toLowerCase();
            const chooseInput = (semantic, numeric) => {
                if (semantic.length === 1) return semantic[0];
                if (semantic.length > 1 || numeric.length !== 1) return null;
                return numeric[0];
            };
            const weekNumeric = inputs.filter(i => {
                const n = Number.parseInt(i.value, 10);
                return n > 0 && n <= 53;
            });
            const yearNumeric = inputs.filter(i => {
                const n = Number.parseInt(i.value, 10);
                return n > 2000 && n <= 2100;
            });
            const weekInput = chooseInput(
                inputs.filter(i => /(^|[^a-z])(week|tuan)([^a-z]|$)/.test(descriptor(i))),
                weekNumeric,
            );
            const yearInput = chooseInput(
                inputs.filter(i => /(^|[^a-z])(year|nam)([^a-z]|$)/.test(descriptor(i))),
                yearNumeric,
            );
            return {
                week: weekInput ? Number.parseInt(weekInput.value, 10) : null,
                year: yearInput ? Number.parseInt(yearInput.value, 10) : null,
            };
        }
        """
    )
    if values.get("week") != week or values.get("year") != year:
        raise RuntimeError("BCNB không xác nhận đúng tuần/năm yêu cầu.")
    return week, year


async def read_current_week_checked(page) -> tuple[int, int]:
    """Read BCNB's server-provided current week without changing the filter.

    The BCNB page exposes both the selected filter and its current calendar
    week in the inline page script.  Prefer that server value so a stale
    workstation clock cannot move discovery to the wrong week.  If a
    persistent session is filtered to another week, switch it to the server
    current period and verify the selection before returning.
    """
    await page.wait_for_selector("table", timeout=60000)
    values = await page.evaluate(
        """
        () => {
            const inputs = Array.from(document.querySelectorAll('input'));
            const descriptor = i => [i.name, i.id, i.placeholder, i.getAttribute('aria-label')]
                .filter(Boolean).join(' ').toLowerCase();
            const chooseInput = (semantic, numeric) => {
                if (semantic.length === 1) return semantic[0];
                if (semantic.length > 1 || numeric.length !== 1) return null;
                return numeric[0];
            };
            const weekNumeric = inputs.filter(i => {
                const n = Number.parseInt(i.value, 10);
                return n > 0 && n <= 53;
            });
            const yearNumeric = inputs.filter(i => {
                const n = Number.parseInt(i.value, 10);
                return n > 2000 && n <= 2100;
            });
            const weekInput = chooseInput(
                inputs.filter(i => /(^|[^a-z])(week|tuan)([^a-z]|$)/.test(descriptor(i))),
                weekNumeric,
            );
            const yearInput = chooseInput(
                inputs.filter(i => /(^|[^a-z])(year|nam)([^a-z]|$)/.test(descriptor(i))),
                yearNumeric,
            );
            const source = Array.from(document.scripts)
                .map(script => script.textContent || '')
                .join('\\n');
            const readScriptNumber = name => {
                const match = source.match(new RegExp(String.raw`(?:const|let|var)\\s+${name}\\s*=\\s*(\\d+)`));
                return match ? Number.parseInt(match[1], 10) : null;
            };
            return {
                selectedWeek: weekInput ? Number.parseInt(weekInput.value, 10) : null,
                selectedYear: yearInput ? Number.parseInt(yearInput.value, 10) : null,
                currentWeek: readScriptNumber('currentWeek'),
                currentYear: readScriptNumber('currentYear'),
            };
        }
        """
    )
    current_week = values.get("currentWeek")
    current_year = values.get("currentYear")
    selected_week = values.get("selectedWeek")
    selected_year = values.get("selectedYear")
    if current_week is None or current_year is None:
        raise RuntimeError("BCNB không cung cấp tuần hiện tại từ máy chủ.")
    if not 1 <= current_week <= 53 or not 2000 < current_year <= 2100:
        raise RuntimeError("BCNB trả về tuần/năm hiện tại không hợp lệ.")
    if (selected_week, selected_year) != (current_week, current_year):
        # A persistent browser session may have been left on the predecessor
        # week.  The server-provided current period is authoritative, so
        # select it explicitly before discovery continues.
        try:
            return await select_week_checked(page, int(current_week), int(current_year))
        except Exception as exc:
            raise RuntimeError(
                "BCNB đang hiển thị không đúng tuần hiện tại; không thể chọn lại an toàn."
            ) from exc
    return int(current_week), int(current_year)


async def dismiss_current_week_notice(page) -> bool:
    """Close BCNB's informational current-week warning, and nothing else."""
    # Keep the helper harmless for lightweight unit fakes; real Playwright
    # pages always expose ``locator``.
    if not hasattr(page, "locator"):
        return False
    notice = page.locator(".swal2-container:visible").first
    if await notice.count() == 0:
        return False
    text = (await notice.inner_text()).strip()
    if "Tuần bạn đang xem" not in text or "Đã hiểu" not in text:
        return False
    confirm = notice.locator(".swal2-confirm").first
    if await confirm.count() == 0:
        return False
    await confirm.click()
    try:
        await notice.wait_for(state="hidden", timeout=5000)
    except PlaywrightTimeoutError:
        pass
    return True


async def select_last_week_checked(page) -> tuple[int, int]:
    week, year = previous_business_week()
    return await select_week_checked(page, week, year)


async def resolve_report_columns(page) -> tuple[int, int, int]:
    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]
    class_indexes = [
        i for i, text in enumerate(headers) if "ma lop" in text or "class code" in text
    ]
    lesson_indexes = [i for i, text in enumerate(headers) if "buoi" in text]
    current_report_indexes = [
        i for i, text in enumerate(headers) if "nhan xet zalo" in text
    ]
    legacy_report_indexes = [
        i
        for i, text in enumerate(headers)
        if "lms" not in text
        and ("bao cao" in text or "da nop" in text or "nop bao cao" in text)
    ]
    report_indexes = current_report_indexes or legacy_report_indexes
    if not all(len(indexes) == 1 for indexes in (class_indexes, lesson_indexes, report_indexes)):
        raise RuntimeError(
            "Cấu trúc bảng BCNB đã thay đổi: cột mã lớp/buổi/báo cáo thiếu hoặc không duy nhất."
        )
    return class_indexes[0], lesson_indexes[0], report_indexes[0]


async def resolve_class_lesson_columns(page) -> tuple[int, int]:
    """Resolve the two columns Discovery needs without requiring report columns."""
    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]
    class_indexes = [
        i for i, text in enumerate(headers) if "ma lop" in text or "class code" in text
    ]
    lesson_indexes = [i for i, text in enumerate(headers) if "buoi" in text]
    if len(class_indexes) != 1 or len(lesson_indexes) != 1:
        raise RuntimeError(
            "Cấu trúc bảng BCNB đã thay đổi: cột mã lớp/buổi thiếu hoặc không duy nhất."
        )
    return class_indexes[0], lesson_indexes[0]


async def resolve_schedule_columns(page) -> tuple[int, int, int, int]:
    """Resolve class, lesson, lesson-date and Zalo-deadline columns.

    A schedule snapshot is only safe for cross-week matching when both the
    lesson date and the deadline are identified from semantic headers.  Do not
    fall back to positional columns here: BCNB has added columns in the past
    and a positional fallback can silently associate a deadline with the
    wrong class.
    """
    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]
    class_indexes = [
        i for i, text in enumerate(headers) if "ma lop" in text or "class code" in text
    ]
    lesson_indexes = [i for i, text in enumerate(headers) if "buoi" in text]
    class_date_indexes = [
        i
        for i, text in enumerate(headers)
        if "ngay hoc" in text or "lesson date" in text or text == "date"
    ]
    deadline_indexes = [
        i
        for i, text in enumerate(headers)
        if "deadline zalo" in text or ("zalo" in text and "deadline" in text)
    ]
    if not all(
        len(indexes) == 1
        for indexes in (
            class_indexes,
            lesson_indexes,
            class_date_indexes,
            deadline_indexes,
        )
    ):
        raise RuntimeError(
            "Cấu trúc bảng BCNB đã thay đổi: không xác định duy nhất cột "
            "mã lớp/buổi/ngày học/deadline Zalo."
        )
    return (
        class_indexes[0],
        lesson_indexes[0],
        class_date_indexes[0],
        deadline_indexes[0],
    )


async def resolve_lms_columns(page) -> tuple[int, int]:
    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]
    class_indexes = [
        i for i, text in enumerate(headers) if "ma lop" in text or "class code" in text
    ]
    current_lms_indexes = [
        i for i, text in enumerate(headers) if "nhan xet lms" in text
    ]
    legacy_lms_indexes = [
        i
        for i, text in enumerate(headers)
        if "lms" in text
        and "deadline" not in text
        and (
            text == "lms"
            or "nhan xet" in text
            or "da nop" in text
            or "nop" in text
        )
    ]
    lms_indexes = current_lms_indexes or legacy_lms_indexes
    if len(class_indexes) != 1 or len(lms_indexes) != 1:
        raise RuntimeError(
            "Cấu trúc bảng BCNB đã thay đổi: cột mã lớp/LMS thiếu hoặc không duy nhất."
        )
    return class_indexes[0], lms_indexes[0]


async def verify_checkbox_persisted(
    page,
    *,
    class_code: str,
    column_kind: str,
    week: int,
    year: int,
    lesson: str | None = None,
) -> bool:
    """Hard reload and read a newly resolved checkbox from the BCNB server view."""
    await page.reload(wait_until="domcontentloaded")
    selected_week, selected_year = await select_week_checked(page, week, year)
    if (selected_week, selected_year) != (week, year):
        raise RuntimeError("BCNB reload trả về sai tuần/năm cần xác minh.")

    lesson_index = None
    if column_kind == "report":
        class_index, lesson_index, checkbox_index = await resolve_report_columns(page)
        if lesson is None:
            raise RuntimeError("Thiếu số buổi khi xác minh cột báo cáo BCNB.")
    elif column_kind == "lms":
        class_index, checkbox_index = await resolve_lms_columns(page)
    else:
        raise RuntimeError("Loại checkbox BCNB không hợp lệ.")

    target_code = _canonical_class_code(class_code)
    target_lesson = str(lesson).strip() if lesson is not None else None
    matches = []
    rows = await page.locator("table tr").all()
    for row in rows:
        cells = await row.locator("td").all()
        required_index = max(
            class_index,
            checkbox_index,
            lesson_index if lesson_index is not None else 0,
        )
        if len(cells) <= required_index:
            continue
        observed_code = _canonical_class_code((await cells[class_index].inner_text()).strip())
        if observed_code != target_code:
            continue
        if lesson_index is not None:
            observed_lesson = (await cells[lesson_index].inner_text()).strip()
            if observed_lesson != target_lesson:
                continue
        matches.append(cells[checkbox_index].locator('input[type="checkbox"]').first)

    if len(matches) != 1:
        raise RuntimeError(
            f"BCNB reload cần đúng một dòng cho {class_code}, nhưng tìm thấy {len(matches)}."
        )
    checkbox = matches[0]
    if await checkbox.count() != 1:
        raise RuntimeError(f"BCNB reload không có đúng một checkbox cho {class_code}.")
    return await checkbox.is_checked()
