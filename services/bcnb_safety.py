"""Shared, fail-closed helpers for reading the BCNB table."""

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any
import unicodedata

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.time_utils import previous_business_week


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    stripped = "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    )
    return stripped.replace("đ", "d").replace("Đ", "D").lower()


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
            // BCNB 2026-10: the week filter button label became the selected
            // date range, e.g. "Tuần (26/10/2026 - 01/11/2026)".  Every
            // selection path (new form submit or legacy "Xem"/"Lọc lịch"
            // label) must pass the same safety check: visible, enabled, no
            // formaction/formmethod override, and if it belongs to a form,
            // that form must be the GET form owning both week and year inputs.
            const ownerForm = weekInput && yearInput && weekInput.form &&
                weekInput.form === yearInput.form ? weekInput.form : null;
            const ownerIsGet = !!ownerForm &&
                (ownerForm.getAttribute('method') || 'get').trim().toLowerCase() === 'get';
            const isSafeTrigger = control =>
                control.offsetParent !== null &&
                !control.disabled &&
                !control.hasAttribute('formaction') &&
                !control.hasAttribute('formmethod') &&
                (!control.form || (control.form === ownerForm && ownerIsGet));
            const acceptedLabels = /^(xem|lọc lịch)$/i;
            const buttons = controls.filter(control => {
                const label = (control.innerText || control.value || '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                return acceptedLabels.test(label) && isSafeTrigger(control);
            });
            const formSubmits = ownerIsGet ? Array.from(ownerForm.elements).filter(control =>
                control.matches(
                    'button[type="submit"], button:not([type]), input[type="submit"]'
                ) && isSafeTrigger(control)
            ) : [];
            const button = formSubmits.length === 1
                ? formSubmits[0]
                : (buttons.length === 1 ? buttons[0] : null);
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
            const source = Array.from(document.scripts)
                .map(script => script.textContent || '')
                .join('\\n');
            const readScriptNumber = name => {
                const match = source.match(new RegExp(String.raw`(?:const|let|var)\\s+${name}\\s*=\\s*(\\d+)`));
                return match ? Number.parseInt(match[1], 10) : null;
            };
            return {
                week: weekInput ? Number.parseInt(weekInput.value, 10) : null,
                year: yearInput ? Number.parseInt(yearInput.value, 10) : null,
                serverWeek: readScriptNumber('selectedWeek'),
                serverYear: readScriptNumber('selectedYear'),
            };
        }
        """
    )
    if values.get("week") != week or values.get("year") != year:
        raise RuntimeError("BCNB không xác nhận đúng tuần/năm yêu cầu.")
    # Server-rendered evidence: the input values alone can be changed client-side
    # without the filter ever being submitted.  When BCNB renders its selected
    # period into the page script, it must agree with the requested week.
    server_week = values.get("serverWeek")
    server_year = values.get("serverYear")
    if (server_week is not None or server_year is not None) and (
        server_week != week or server_year != year
    ):
        raise RuntimeError("BCNB máy chủ chưa áp dụng đúng tuần/năm yêu cầu.")
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
        i
        for i, text in enumerate(headers)
        if "nhan xet zalo" in text or re.fullmatch(r"nx\s*zalo", text)
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


async def resolve_instructor_column(page) -> int | None:
    """Resolve the main-instructor column from semantic headers.

    Never fall back to a positional column index: BCNB 2026-10 inserted an
    ``STT`` column at index 0, shifting ``Ngày học`` into index 3 where
    ``Giảng viên chính`` previously sat.
    """
    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]

    def _is_excluded(text: str) -> bool:
        return "tro giang" in text or "ghi chu" in text or "note" in text

    primary_indexes = [
        i
        for i, text in enumerate(headers)
        if not _is_excluded(text)
        and (
            "giang vien chinh" in text
            or "giao vien chinh" in text
            or text == "main instructor"
        )
    ]
    if len(primary_indexes) == 1:
        return primary_indexes[0]
    if len(primary_indexes) > 1:
        return None
    fallback_indexes = [
        i
        for i, text in enumerate(headers)
        if not _is_excluded(text)
        and (
            "giang vien" in text
            or "giao vien" in text
            or text in {"instructor", "teacher", "gv"}
        )
    ]
    return fallback_indexes[0] if len(fallback_indexes) == 1 else None


async def resolve_lms_columns(page) -> tuple[int, int]:
    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]
    class_indexes = [
        i for i, text in enumerate(headers) if "ma lop" in text or "class code" in text
    ]
    current_lms_indexes = [
        i
        for i, text in enumerate(headers)
        if "nhan xet lms" in text or re.fullmatch(r"nx\s*lms", text)
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


_BCNB_NOTE_PLACEHOLDERS = frozenset(
    {
        "+ ghi chu qc",
        "ghi chu qc",
        "+ gv nhap giai trinh",
        "gv nhap giai trinh",
    }
)


def clean_bcnb_note_text(raw: Any) -> str:
    """Normalize a BCNB note cell value and strip UI placeholder labels."""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    if text.startswith('"') and text.endswith('"'):
        try:
            decoded = json.loads(text)
            if isinstance(decoded, str):
                text = decoded.strip()
        except (TypeError, ValueError):
            pass
    folded = re.sub(r"\s+", " ", fold_text(text)).strip()
    if folded in _BCNB_NOTE_PLACEHOLDERS:
        return ""
    return text


async def resolve_extended_bcnb_columns(page) -> dict[str, int | None]:
    """Resolve optional extended columns on BCNB check_nhanxet.php by semantic header."""
    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]

    def _unique(indices: list[int]) -> int | None:
        return indices[0] if len(indices) == 1 else None

    block_indexes = [
        i for i, text in enumerate(headers) if text == "khoi" or "khoi" in text or text == "block"
    ]
    current_zalo_indexes = [
        i
        for i, text in enumerate(headers)
        if "nhan xet zalo" in text or re.fullmatch(r"nx\s*zalo", text)
    ]
    legacy_zalo_indexes = [
        i
        for i, text in enumerate(headers)
        if "lms" not in text
        and ("bao cao" in text or "da nop" in text or "nop bao cao" in text)
    ]
    lms_deadline_indexes = [
        i
        for i, text in enumerate(headers)
        if "deadline lms" in text or ("lms" in text and "deadline" in text)
    ]
    current_lms_indexes = [
        i
        for i, text in enumerate(headers)
        if "nhan xet lms" in text or re.fullmatch(r"nx\s*lms", text)
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
    checker_note_indexes = [
        i
        for i, text in enumerate(headers)
        if "mo ta van de" in text or "te/tf/qc" in text or "ghi chu qc" in text
    ]
    instructor_note_indexes = [
        i
        for i, text in enumerate(headers)
        if "ghi chu giang vien" in text or "ghi chu gv" in text
    ]
    evaluation_status_indexes = [
        i
        for i, text in enumerate(headers)
        if "tinh trang danh gia" in text
        or "tinh trang kiem tra" in text
        or "trang thai danh gia" in text
    ]
    return {
        "block_index": _unique(block_indexes),
        "zalo_checkbox_index": _unique(current_zalo_indexes or legacy_zalo_indexes),
        "lms_deadline_index": _unique(lms_deadline_indexes),
        "lms_checkbox_index": _unique(current_lms_indexes or legacy_lms_indexes),
        "checker_note_index": _unique(checker_note_indexes),
        "instructor_note_index": _unique(instructor_note_indexes),
        "evaluation_status_index": _unique(evaluation_status_indexes),
    }


async def _read_optional_checkbox_state(cell) -> bool | None:
    if cell is None or not hasattr(cell, "locator"):
        return None
    try:
        checkbox = cell.locator('input[type="checkbox"]').first
        if hasattr(checkbox, "count") and await checkbox.count() == 0:
            return None
        return bool(await checkbox.is_checked())
    except Exception:
        return None


async def _read_optional_note_cell(cell, *, note_type: str) -> str | None:
    if cell is None:
        return None
    attr_value: str | None = None
    if hasattr(cell, "locator"):
        try:
            note_el = cell.locator(f'.note-cell[data-note-type="{note_type}"]').first
            if hasattr(note_el, "count") and await note_el.count() > 0:
                if hasattr(note_el, "get_attribute"):
                    raw_attr = await note_el.get_attribute("data-current-note")
                    if raw_attr is not None and not isinstance(raw_attr, Exception):
                        attr_value = clean_bcnb_note_text(str(raw_attr))
                        if attr_value:
                            return attr_value
        except Exception:
            attr_value = None
    if hasattr(cell, "inner_text"):
        try:
            raw_text = await cell.inner_text()
            if raw_text is not None:
                return clean_bcnb_note_text(str(raw_text))
        except Exception:
            return attr_value
    return attr_value


async def extract_bcnb_extended_row_fields(
    row,
    cells: list[Any],
    ext_cols: Mapping[str, int | None] | None,
    *,
    include_empty: bool = True,
) -> dict[str, Any]:
    """Extract extended BCNB columns safely for both real Playwright rows and unit-test fakes."""
    cols = ext_cols or {}
    session_id = ""
    row_block_attr = ""
    if hasattr(row, "get_attribute"):
        try:
            session_id = str((await row.get_attribute("data-session-id")) or "").strip()
        except Exception:
            session_id = ""
        try:
            row_block_attr = str((await row.get_attribute("data-block")) or "").strip()
        except Exception:
            row_block_attr = ""

    def _cell_at(key: str):
        idx = cols.get(key)
        if isinstance(idx, int) and 0 <= idx < len(cells):
            return cells[idx]
        return None

    block_cell = _cell_at("block_index")
    khoi = ""
    if block_cell is not None and hasattr(block_cell, "inner_text"):
        try:
            khoi = str((await block_cell.inner_text()) or "").strip()
        except Exception:
            khoi = ""
    if not khoi and row_block_attr:
        khoi = row_block_attr

    lms_deadline_cell = _cell_at("lms_deadline_index")
    lms_deadline = ""
    if lms_deadline_cell is not None and hasattr(lms_deadline_cell, "inner_text"):
        try:
            lms_deadline = str((await lms_deadline_cell.inner_text()) or "").strip()
        except Exception:
            lms_deadline = ""

    eval_cell = _cell_at("evaluation_status_index")
    evaluation_status = ""
    if eval_cell is not None and hasattr(eval_cell, "inner_text"):
        try:
            evaluation_status = re.sub(
                r"\s+",
                " ",
                str((await eval_cell.inner_text()) or ""),
            ).strip()
        except Exception:
            evaluation_status = ""

    nx_zalo_checked = await _read_optional_checkbox_state(_cell_at("zalo_checkbox_index"))
    nx_lms_checked = await _read_optional_checkbox_state(_cell_at("lms_checkbox_index"))
    checker_cell = _cell_at("checker_note_index")
    instructor_cell = _cell_at("instructor_note_index")
    qc_issue_note = await _read_optional_note_cell(checker_cell, note_type="checker")
    instructor_note = await _read_optional_note_cell(
        instructor_cell, note_type="instructor"
    )

    full_result: dict[str, Any] = {
        "bcnb_session_id": session_id,
        "khoi": khoi,
        "nx_zalo_checked": nx_zalo_checked,
        "nx_lms_checked": nx_lms_checked,
        "lms_deadline": lms_deadline,
        "qc_issue_note": qc_issue_note,
        "instructor_note": instructor_note,
        "evaluation_status": evaluation_status,
    }
    if include_empty:
        return full_result
    return {
        k: v
        for k, v in full_result.items()
        if (
            isinstance(v, bool)
            or (
                isinstance(v, str)
                and (bool(v.strip()) or k in {"qc_issue_note", "instructor_note"})
            )
        )
    }


def _balance_issue_brackets(text: str) -> str:
    """Ensure a single issue string never contains unbalanced '[' or ']'."""
    chars = list(text)
    open_indices: list[int] = []
    for idx, ch in enumerate(chars):
        if ch == "[":
            open_indices.append(idx)
        elif ch == "]":
            if open_indices:
                open_indices.pop()
            else:
                chars[idx] = ")"
    for idx in open_indices:
        chars[idx] = "("
    return "".join(chars)


def _normalize_auto_issue_key(text: str) -> str:
    stripped = re.sub(r"^\[[^\]]+\]\s*", "", text.strip())
    stripped = stripped.replace(" — ", "; ").strip(" .;|").lower()
    return " ".join(stripped.split())


def normalize_auto_issue_text(text: str) -> str:
    """Normalize auto-generated LMS/Zalo issue strings to the concise canonical wording."""
    cleaned = " ".join(str(text or "").split()).strip(" ;|")
    if not cleaned:
        return ""
    if re.match(
        r"^\[LMS\]\s*(?:(?:Nhận|Nhan)\s+x[eé]t\s+(?:HS\s+)?s[oơ]\s+s[aà]i\s*,?\s*thi[eế]u\s*(?:đủ\s*)?3\s*[yý]|(?:Nhận|Nhan)\s+x[eé]t\s+thi[eế]u\s+[yý])(?:\s*\([^)]*\))?\.?$",
        cleaned,
        flags=re.IGNORECASE,
    ):
        return "[LMS] Nhận xét thiếu ý"
    if re.match(
        r"^\[LMS\]\s*(?:Nhận|Nhan)\s+x[eé]t\s+Checkpoint\s+ch[uư]a\s+[đd][uủ]\s+c[aấ]u\s+tr[uú]c\s+n[aă]ng\s+l[uự]c(?:\s*\([^)]*\))?\.?$",
        cleaned,
        flags=re.IGNORECASE,
    ):
        return "[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực"
    m_template = re.match(
        r"^(?:\[Zalo\]\s*)?Đã báo cáo nhưng ghi sai ngày/buổi(?:\s+trong\s+mẫu)?\s*(?:[;—,\-]+|\()\s*['\"]?(Bài\s+số\s+\d+)['\"]?(?:\s+khớp\s+với\b.*|\s*\))\.?$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if m_template:
        lesson_label = " ".join(m_template.group(1).split())
        if lesson_label[:1].islower():
            lesson_label = lesson_label[:1].upper() + lesson_label[1:]
        return f"[Zalo] Đã báo cáo nhưng ghi sai ngày/buổi ( {lesson_label} )"
    m_wrong_date = re.match(
        r"^(?:\[Zalo\]\s*)?Đã báo cáo nhưng ghi sai ngày\s+([^\s;—,\)]+)\s*[;—,\-]+\s*đã ghép theo BCNB\s+W\d+/\d+\s*,\s*ngày\s+([^,.\)]+)\s*,\s*buổi\s+([^\s.\)]+)\.?$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if m_wrong_date:
        claimed_date = m_wrong_date.group(1).strip()
        target_date = m_wrong_date.group(2).strip()
        target_lesson = m_wrong_date.group(3).strip()
        return (
            f"[Zalo] Đã báo cáo nhưng ghi sai ngày "
            f"( ghi ngày {claimed_date} nhưng lịch là ngày {target_date}, buổi {target_lesson})"
        )
    m_wrong_lesson = re.match(
        r"^(?:\[Zalo\]\s*)?Đã báo cáo nhưng ghi sai buổi\s+([^\s;—,\)]+)\s*[;—,\-]+\s*đã ghép theo BCNB\s+W\d+/\d+\s*,\s*ngày\s+([^,.\)]+)\s*,\s*buổi\s+([^\s.\)]+)\.?$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if m_wrong_lesson:
        claimed_lesson = m_wrong_lesson.group(1).strip()
        target_date = m_wrong_lesson.group(2).strip()
        target_lesson = m_wrong_lesson.group(3).strip()
        return (
            f"[Zalo] Đã báo cáo nhưng ghi sai buổi "
            f"( ghi buổi {claimed_lesson} nhưng lịch là ngày {target_date}, buổi {target_lesson})"
        )
    return cleaned


def split_checker_issue_note(raw_note: Any) -> tuple[str, list[str]]:
    """Split a BCNB checker note into ``(manual_note, auto_issues)`` using balanced brackets."""
    text = clean_bcnb_note_text(raw_note)
    if not text:
        return "", []
    auto_issues: list[str] = []
    seen_keys: set[str] = set()
    remaining = text
    pattern = re.compile(r"\[Auto\s+QC:\s*", flags=re.IGNORECASE)
    while True:
        match = pattern.search(remaining)
        if not match:
            break
        start_idx = match.start()
        pos = match.end()
        depth = 1
        end_idx = len(remaining)
        inside = ""
        for idx in range(pos, len(remaining)):
            ch = remaining[idx]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    inside = remaining[pos:idx].strip()
                    end_idx = idx + 1
                    break
        if depth > 0:
            inside = remaining[pos:].strip(" ]")
            end_idx = len(remaining)
        for seg in inside.split(";"):
            cleaned_seg = normalize_auto_issue_text(seg)
            if not cleaned_seg:
                continue
            key = _normalize_auto_issue_key(cleaned_seg)
            if key and key not in seen_keys:
                seen_keys.add(key)
                auto_issues.append(cleaned_seg)
        before = re.sub(r"\s*\|\s*$", "", remaining[:start_idx]).strip()
        after = re.sub(r"^\s*\|\s*", "", remaining[end_idx:]).strip()
        if before and after:
            remaining = f"{before} | {after}"
        else:
            remaining = before or after

    manual_chunks: list[str] = []
    auto_tag_re = re.compile(r"^\[(?:LMS|Zalo)\]\s*", flags=re.IGNORECASE)
    for pipe_chunk in remaining.split("|"):
        chunk_manual_segs: list[str] = []
        for seg in pipe_chunk.split(";"):
            cleaned_seg = " ".join(seg.split()).strip(" ;|")
            if not cleaned_seg:
                continue
            if auto_tag_re.match(cleaned_seg):
                norm_seg = normalize_auto_issue_text(cleaned_seg)
                key = _normalize_auto_issue_key(norm_seg)
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    auto_issues.append(norm_seg)
            else:
                chunk_manual_segs.append(cleaned_seg)
        if chunk_manual_segs:
            manual_chunks.append("; ".join(chunk_manual_segs))
    manual_clean = " | ".join(manual_chunks).strip()
    return manual_clean, auto_issues


def _truncate_single_issue_preserving_tag(issue_text: str, item_budget: int) -> str:
    """Truncate one auto issue to ``item_budget`` while keeping any leading ``[TAG]`` intact."""
    tag_match = re.match(r"^(\[[^\]]+\]\s*)", issue_text)
    tag_prefix = tag_match.group(1) if tag_match else ""
    body = issue_text[len(tag_prefix) :]
    effective_budget = max(len(tag_prefix) + 8, int(item_budget))
    if len(issue_text) <= effective_budget:
        return _balance_issue_brackets(issue_text)
    body_budget = max(8, effective_budget - len(tag_prefix))
    sliced_body = body[:body_budget].rstrip(" ;|,[")
    return f"{tag_prefix}{_balance_issue_brackets(sliced_body)}"


def _is_legacy_lms_duplicate_issue(text: str) -> bool:
    cleaned = " ".join(str(text or "").split()).strip()
    return bool(
        re.match(
            r"^\[LMS\]\s*(?:Nhận|Nhan)\s+x[eé]t\s+HS\s+copy-paste\s+tr[uù]ng\s+l[aặ]p\b",
            cleaned,
            flags=re.IGNORECASE,
        )
    )


def merge_checker_issue_note(
    existing_note: Any,
    auto_issues: Sequence[str] | None,
    *,
    max_length: int = 500,
) -> str:
    """Merge auto-detected QC issues into a checker note while preserving manual QC notes."""
    limit = max(48, int(max_length))
    manual_part, _ = split_checker_issue_note(existing_note)
    deduped_issues: list[str] = []
    seen_keys: set[str] = set()
    for raw_issue in auto_issues or []:
        cleaned = " ".join(str(raw_issue or "").split()).strip(" ;|")
        if not cleaned or _is_legacy_lms_duplicate_issue(cleaned):
            continue
        cleaned = normalize_auto_issue_text(cleaned)
        cleaned = re.sub(r"\s*[;|]+\s*", ", ", cleaned)
        balanced = _balance_issue_brackets(cleaned)
        key = _normalize_auto_issue_key(balanced)
        if key and key not in seen_keys:
            seen_keys.add(key)
            deduped_issues.append(balanced)

    if not deduped_issues:
        return manual_part

    joined_issues = "; ".join(deduped_issues)
    combined = f"{manual_part} | {joined_issues}" if manual_part else joined_issues
    if len(combined) <= limit:
        return combined

    suffix = "..."
    count = len(deduped_issues)
    min_inner_budget = max(20, 18 * count + 2 * max(0, count - 1))

    if manual_part:
        raw_avail = limit - len(manual_part) - 3 - len(suffix)
        inner_budget = max(min_inner_budget, raw_avail)
    else:
        inner_budget = max(min_inner_budget, limit - len(suffix))

    sep_cost = 2 * max(0, count - 1)
    per_issue_budget = max(18, (inner_budget - sep_cost) // count)
    truncated_parts = [
        _truncate_single_issue_preserving_tag(issue, per_issue_budget)
        for issue in deduped_issues
    ]
    truncated_inner = "; ".join(truncated_parts).rstrip(" ;|,[")
    if manual_part:
        return f"{manual_part} | {truncated_inner}{suffix}"
    return f"{truncated_inner}{suffix}"


def _normalize_bcnb_date_iso(raw_date: str) -> str:
    cleaned = str(raw_date or "").strip()
    if not cleaned:
        return ""
    iso_m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", cleaned)
    if iso_m:
        return f"{int(iso_m.group(1)):04d}-{int(iso_m.group(2)):02d}-{int(iso_m.group(3)):02d}"
    dmy_m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", cleaned)
    if dmy_m:
        return f"{int(dmy_m.group(3)):04d}-{int(dmy_m.group(2)):02d}-{int(dmy_m.group(1)):02d}"
    return cleaned


async def verify_checker_note_persisted(
    page,
    *,
    class_code: str,
    week: int,
    year: int,
    expected_note: str,
    lesson: str | None = None,
    class_date_iso: str | None = None,
    session_id: str | None = None,
) -> tuple[bool, str]:
    """Hard reload and verify that the BCNB checker note matches ``expected_note``."""
    await page.reload(wait_until="domcontentloaded")
    selected_week, selected_year = await select_week_checked(page, week, year)
    if (selected_week, selected_year) != (week, year):
        raise RuntimeError("BCNB reload trả về sai tuần/năm cần xác minh ghi chú.")

    class_index, lesson_index = await resolve_class_lesson_columns(page)
    ext_cols = await resolve_extended_bcnb_columns(page)
    checker_index = ext_cols.get("checker_note_index")
    if checker_index is None:
        raise RuntimeError(
            "Cấu trúc bảng BCNB đã thay đổi: thiếu cột Mô tả vấn đề (TE/TF/QC)."
        )

    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]
    date_indexes = [i for i, text in enumerate(headers) if "ngay hoc" in text]
    date_index = date_indexes[0] if len(date_indexes) == 1 else None

    target_code = _canonical_class_code(class_code)
    target_lesson = str(lesson).strip() if lesson is not None else None
    target_date_iso = _normalize_bcnb_date_iso(class_date_iso or "")
    target_sid = str(session_id or "").strip()

    if target_date_iso and date_index is None and not target_sid:
        raise RuntimeError("BCNB reload thiếu cột Ngày học để xác minh duy nhất dòng ghi chú.")

    matches = []
    rows = await page.locator("table tr").all()
    for row in rows:
        cells = await row.locator("td").all()
        required_index = max(
            class_index,
            lesson_index,
            checker_index,
            date_index if date_index is not None else 0,
        )
        if len(cells) <= required_index:
            continue
        observed_code = _canonical_class_code((await cells[class_index].inner_text()).strip())
        if observed_code != target_code:
            continue
        if target_lesson is not None:
            observed_lesson = (await cells[lesson_index].inner_text()).strip()
            if observed_lesson != target_lesson:
                continue
        if target_sid:
            row_sid = ""
            if hasattr(row, "get_attribute"):
                try:
                    row_sid = str((await row.get_attribute("data-session-id")) or "").strip()
                except Exception:
                    row_sid = ""
            if row_sid != target_sid:
                continue
        if target_date_iso and date_index is not None:
            observed_date_iso = _normalize_bcnb_date_iso(
                (await cells[date_index].inner_text()).strip()
            )
            if observed_date_iso != target_date_iso:
                continue
        matches.append(cells[checker_index])

    if len(matches) != 1:
        raise RuntimeError(
            f"BCNB reload cần đúng một dòng ghi chú cho {class_code}, nhưng tìm thấy {len(matches)}."
        )
    observed_raw = await _read_optional_note_cell(matches[0], note_type="checker")
    if observed_raw is None:
        raise RuntimeError(f"BCNB reload không đọc được ô Mô tả vấn đề cho {class_code}.")
    observed_clean = clean_bcnb_note_text(observed_raw)
    expected_clean = clean_bcnb_note_text(expected_note)
    return (observed_clean == expected_clean, observed_clean)
