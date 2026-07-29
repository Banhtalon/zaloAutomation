"""Shared, fail-closed helpers for reading the BCNB table."""

from datetime import datetime, timedelta
import re
import unicodedata

from playwright.async_api import TimeoutError as PlaywrightTimeoutError


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


async def select_last_week_checked(page) -> tuple[int, int]:
    start_last_week = datetime.now() - timedelta(days=datetime.now().weekday() + 7)
    week = int(start_last_week.isocalendar()[1])
    year = int(start_last_week.isocalendar()[0])
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
