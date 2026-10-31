"""Shared, fail-closed helpers for reading the BCNB table."""

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any
import unicodedata

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.time_utils import previous_business_week
from services.report_status import (
    StructuredIssueStr,
    coerce_structured_issue,
    issue_text,
    normalize_class_code,
)


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    stripped = "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    )
    return stripped.replace("đ", "d").replace("Đ", "D").lower()


def _canonical_class_code(value: str) -> str:
    return normalize_class_code(value)


BCNB_CLASS_HEADER_SUBSTRINGS: tuple[str, ...] = ("ma lop", "class code")
BCNB_CLASS_HEADER_EXACT: tuple[str, ...] = ("lop",)
BCNB_LESSON_HEADER_SUBSTRINGS: tuple[str, ...] = ("buoi",)
BCNB_DATE_HEADER_SUBSTRINGS: tuple[str, ...] = ("ngay hoc", "lesson date")
BCNB_DATE_HEADER_EXACT: tuple[str, ...] = ("date",)
BCNB_ZALO_DEADLINE_HEADER_SUBSTRINGS: tuple[str, ...] = ("deadline zalo",)
BCNB_LMS_DEADLINE_HEADER_SUBSTRINGS: tuple[str, ...] = ("deadline lms",)
BCNB_ZALO_CHECKBOX_CURRENT_SUBSTRINGS: tuple[str, ...] = ("nhan xet zalo",)
BCNB_ZALO_CHECKBOX_LEGACY_SUBSTRINGS: tuple[str, ...] = (
    "bao cao",
    "da nop",
    "nop bao cao",
)
BCNB_LMS_CHECKBOX_CURRENT_SUBSTRINGS: tuple[str, ...] = ("nhan xet lms",)
BCNB_LMS_CHECKBOX_LEGACY_SUBSTRINGS: tuple[str, ...] = (
    "nhan xet",
    "da nop",
    "nop",
)
BCNB_CHECKER_NOTE_HEADER_SUBSTRINGS: tuple[str, ...] = (
    "mo ta van de",
    "te/tf/qc",
    "ghi chu qc",
)
BCNB_INSTRUCTOR_NOTE_HEADER_SUBSTRINGS: tuple[str, ...] = (
    "ghi chu giang vien",
    "ghi chu gv",
)
BCNB_EVALUATION_STATUS_HEADER_SUBSTRINGS: tuple[str, ...] = (
    "tinh trang danh gia",
    "tinh trang kiem tra",
    "trang thai danh gia",
)


def match_bcnb_column_indexes(headers: Sequence[str]) -> dict[str, list[int]]:
    """Classify folded BCNB table headers into canonical column index lists."""
    headers = [fold_text(str(h or "")).strip() for h in headers]
    class_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_CLASS_HEADER_SUBSTRINGS)
        or text in BCNB_CLASS_HEADER_EXACT
    ]
    lesson_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_LESSON_HEADER_SUBSTRINGS)
    ]
    class_date_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_DATE_HEADER_SUBSTRINGS)
        or text in BCNB_DATE_HEADER_EXACT
    ]
    zalo_deadline_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_ZALO_DEADLINE_HEADER_SUBSTRINGS)
        or ("zalo" in text and "deadline" in text)
    ]
    lms_deadline_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_LMS_DEADLINE_HEADER_SUBSTRINGS)
        or ("lms" in text and "deadline" in text)
    ]
    current_zalo_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_ZALO_CHECKBOX_CURRENT_SUBSTRINGS)
        or re.fullmatch(r"nx\s*zalo", text)
    ]
    legacy_zalo_indexes = [
        i
        for i, text in enumerate(headers)
        if "lms" not in text
        and any(sub in text for sub in BCNB_ZALO_CHECKBOX_LEGACY_SUBSTRINGS)
    ]
    current_lms_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_LMS_CHECKBOX_CURRENT_SUBSTRINGS)
        or re.fullmatch(r"nx\s*lms", text)
    ]
    legacy_lms_indexes = [
        i
        for i, text in enumerate(headers)
        if "lms" in text
        and "deadline" not in text
        and (
            text == "lms"
            or any(sub in text for sub in BCNB_LMS_CHECKBOX_LEGACY_SUBSTRINGS)
        )
    ]
    block_indexes = [
        i
        for i, text in enumerate(headers)
        if text == "khoi" or "khoi" in text or text == "block"
    ]
    checker_note_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_CHECKER_NOTE_HEADER_SUBSTRINGS)
    ]
    instructor_note_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_INSTRUCTOR_NOTE_HEADER_SUBSTRINGS)
    ]
    evaluation_status_indexes = [
        i
        for i, text in enumerate(headers)
        if any(sub in text for sub in BCNB_EVALUATION_STATUS_HEADER_SUBSTRINGS)
    ]
    return {
        "class_indexes": class_indexes,
        "lesson_indexes": lesson_indexes,
        "class_date_indexes": class_date_indexes,
        "zalo_deadline_indexes": zalo_deadline_indexes,
        "lms_deadline_indexes": lms_deadline_indexes,
        "report_indexes": current_zalo_indexes or legacy_zalo_indexes,
        "lms_indexes": current_lms_indexes or legacy_lms_indexes,
        "block_indexes": block_indexes,
        "checker_note_indexes": checker_note_indexes,
        "instructor_note_indexes": instructor_note_indexes,
        "evaluation_status_indexes": evaluation_status_indexes,
    }


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
    matched = match_bcnb_column_indexes(headers)
    class_indexes = matched["class_indexes"]
    lesson_indexes = matched["lesson_indexes"]
    report_indexes = matched["report_indexes"]
    if not all(len(indexes) == 1 for indexes in (class_indexes, lesson_indexes, report_indexes)):
        raise RuntimeError(
            "Cấu trúc bảng BCNB đã thay đổi: cột mã lớp/buổi/báo cáo thiếu hoặc không duy nhất."
        )
    return class_indexes[0], lesson_indexes[0], report_indexes[0]


async def resolve_class_lesson_columns(page) -> tuple[int, int]:
    """Resolve the two columns Discovery needs without requiring report columns."""
    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]
    matched = match_bcnb_column_indexes(headers)
    class_indexes = matched["class_indexes"]
    lesson_indexes = matched["lesson_indexes"]
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
    matched = match_bcnb_column_indexes(headers)
    class_indexes = matched["class_indexes"]
    lesson_indexes = matched["lesson_indexes"]
    class_date_indexes = matched["class_date_indexes"]
    deadline_indexes = matched["zalo_deadline_indexes"]
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
    matched = match_bcnb_column_indexes(headers)
    class_indexes = matched["class_indexes"]
    lms_indexes = matched["lms_indexes"]
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


def preserve_bcnb_note_text(raw: Any) -> str:
    """Return note text preserving exact leading/trailing whitespace and newlines, or '' for placeholders."""
    if raw is None:
        return ""
    text = str(raw)
    if not text:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith('"') and stripped.endswith('"'):
        try:
            decoded = json.loads(stripped)
            if isinstance(decoded, str):
                text = decoded
                stripped = decoded.strip()
                if not stripped:
                    return ""
        except (TypeError, ValueError):
            pass
    folded = re.sub(r"\s+", " ", fold_text(stripped)).strip()
    if folded in _BCNB_NOTE_PLACEHOLDERS:
        return ""
    return text


async def resolve_extended_bcnb_columns(page) -> dict[str, int | None]:
    """Resolve optional extended columns on BCNB check_nhanxet.php by semantic header."""
    header_cells = await page.locator("table tr").first.locator("th, td").all()
    headers = [fold_text((await cell.inner_text()).strip()) for cell in header_cells]

    def _unique(indices: list[int]) -> int | None:
        return indices[0] if len(indices) == 1 else None

    matched = match_bcnb_column_indexes(headers)
    return {
        "block_index": _unique(matched["block_indexes"]),
        "zalo_checkbox_index": _unique(matched["report_indexes"]),
        "lms_deadline_index": _unique(matched["lms_deadline_indexes"]),
        "lms_checkbox_index": _unique(matched["lms_indexes"]),
        "checker_note_index": _unique(matched["checker_note_indexes"]),
        "instructor_note_index": _unique(matched["instructor_note_indexes"]),
        "evaluation_status_index": _unique(matched["evaluation_status_indexes"]),
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
                        attr_value = preserve_bcnb_note_text(str(raw_attr))
                        if attr_value:
                            return attr_value
        except Exception:
            attr_value = None
    if hasattr(cell, "inner_text"):
        try:
            raw_text = await cell.inner_text()
            if raw_text is not None:
                return preserve_bcnb_note_text(str(raw_text))
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


def _provenance_issue_key(text: Any) -> str:
    """Return a canonical provenance key that requires and preserves the leading [LMS]/[Zalo] tag."""
    cleaned = " ".join(issue_text(text).split()).strip(" ;|")
    if not cleaned:
        return ""
    if not re.match(r"^\[(lms|zalo)\]\s+.+$", cleaned, flags=re.IGNORECASE):
        return ""
    norm = normalize_auto_issue_text(cleaned)
    norm = re.sub(r"\s*[;|]+\s*", ", ", norm)
    balanced = _balance_issue_brackets(norm).strip(" ;|")
    tag_match = re.match(r"^\[(lms|zalo)\]\s+(.+)$", balanced, flags=re.IGNORECASE)
    if not tag_match:
        return ""
    tag = tag_match.group(1).lower()
    body = tag_match.group(2).replace(" — ", "; ").strip(" ;|").lower()
    if body.endswith(".") and not body.endswith(".."):
        body = body[:-1].rstrip(" ;|")
    body = " ".join(body.split())
    if not body:
        return ""
    return f"[{tag}] {body}"


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


def _extract_raw_candidate_segments(raw_note: Any) -> list[str]:
    """Extract all candidate issue strings from a note for after_state provenance matching."""
    text = clean_bcnb_note_text(raw_note)
    if not text:
        return []
    segments: list[str] = []
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
            cleaned_seg = " ".join(seg.split()).strip(" ;|")
            if cleaned_seg:
                segments.append(cleaned_seg)
        before = re.sub(r"\s*\|\s*$", "", remaining[:start_idx])
        after = re.sub(r"^\s*\|\s*", "", remaining[end_idx:])
        remaining = f"{before} | {after}" if (before and after) else (before or after)
    for pipe_chunk in remaining.split("|"):
        for seg in pipe_chunk.split(";"):
            cleaned_seg = " ".join(seg.split()).strip(" ;|")
            if cleaned_seg:
                segments.append(cleaned_seg)
    return segments


def extract_proven_auto_issues_from_audits(
    audits: Sequence[Mapping[str, Any]] | None,
    *,
    class_code: str,
    lesson: str,
    week: int,
    year: int,
    session_id: str | None = None,
    class_date_iso: str | None = None,
) -> list[str]:
    """Return auto issues proven by a production VERIFIED checker-note audit for this exact row.

    Enforces all 5 conditions of 5.2.4:
    1. action_type == BCNB_UPDATE_CHECKER_NOTE and same row identity (class_code, lesson, week, year, session_id/class_date_iso).
    2. execution_mode == production.
    3. outcome == VERIFIED with a real POST (not the no-change branch).
    4. Segment is present in reason.auto_issues.
    5. Segment is present in after_state.qc_issue_note readback.
    """
    if not audits:
        return []
    target_code = _canonical_class_code(class_code)
    target_lesson = str(lesson or "").strip()
    try:
        target_week = int(week)
        target_year = int(year)
    except (TypeError, ValueError):
        return []
    if not target_code or not target_lesson:
        return []
    row_sid = str(session_id or "").strip()
    row_date = _normalize_bcnb_date_iso(str(class_date_iso or ""))
    if not row_sid and not row_date:
        return []

    proven: list[str] = []
    seen_keys: set[str] = set()
    for audit in audits:
        if not isinstance(audit, Mapping):
            continue
        if str(audit.get("action_type") or "").strip() != "BCNB_UPDATE_CHECKER_NOTE":
            continue
        reason = audit.get("reason")
        if not isinstance(reason, Mapping):
            continue
        audit_code = _canonical_class_code(
            str(audit.get("target_id") or reason.get("class_code") or "")
        )
        if audit_code != target_code:
            continue
        audit_lesson = str(reason.get("lesson") or "").strip()
        if audit_lesson != target_lesson:
            continue
        try:
            audit_week = int(reason.get("week") or 0)
            audit_year = int(reason.get("year") or 0)
        except (TypeError, ValueError):
            continue
        if audit_week != target_week or audit_year != target_year:
            continue
        before_state = audit.get("before_state")
        after_state = audit.get("after_state")
        if not isinstance(after_state, Mapping):
            continue
        audit_sid = str(
            reason.get("bcnb_session_id")
            or after_state.get("bcnb_session_id")
            or (
                before_state.get("bcnb_session_id")
                if isinstance(before_state, Mapping)
                else ""
            )
            or ""
        ).strip()
        audit_date = _normalize_bcnb_date_iso(
            str(reason.get("class_date") or reason.get("class_date_iso") or "")
        )
        if row_sid and audit_sid and row_sid != audit_sid:
            continue
        if row_date and audit_date and row_date != audit_date:
            continue
        has_positive_row_match = (
            (bool(row_sid) and bool(audit_sid) and row_sid == audit_sid)
            or (bool(row_date) and bool(audit_date) and row_date == audit_date)
        )
        if not has_positive_row_match:
            continue

        mode = str(audit.get("mode") or audit.get("execution_mode") or "").strip().lower()
        if mode != "production":
            continue

        if str(audit.get("outcome") or "").strip().upper() != "VERIFIED":
            continue
        if bool(after_state.get("no_change")) or after_state.get("post_verified") is not True:
            continue
        if not isinstance(before_state, Mapping):
            continue
        before_note_raw = before_state.get("qc_issue_note")
        if not isinstance(before_note_raw, str):
            continue
        after_note_raw = after_state.get("qc_issue_note")
        if not isinstance(after_note_raw, str) or not preserve_bcnb_note_text(after_note_raw):
            continue
        if preserve_bcnb_note_text(before_note_raw) == preserve_bcnb_note_text(after_note_raw):
            continue

        raw_auto_list = reason.get("auto_issues")
        if not isinstance(raw_auto_list, Sequence) or isinstance(
            raw_auto_list, (str, bytes)
        ):
            continue
        reason_auto_by_key: dict[str, str] = {}
        for raw_item in raw_auto_list:
            cleaned_item = " ".join(issue_text(raw_item).split()).strip(" ;|")
            if not cleaned_item:
                continue
            key = _provenance_issue_key(cleaned_item)
            if not key:
                continue
            norm_item = normalize_auto_issue_text(cleaned_item)
            norm_item = re.sub(r"\s*[;|]+\s*", ", ", norm_item)
            balanced_item = _balance_issue_brackets(norm_item)
            if key not in reason_auto_by_key:
                adapted_meta = coerce_structured_issue(raw_item)
                reason_auto_by_key[key] = StructuredIssueStr(
                    balanced_item,
                    code=adapted_meta["code"],
                    severity=adapted_meta["severity"],
                )
        if not reason_auto_by_key:
            continue

        before_segments = _extract_raw_candidate_segments(before_note_raw)
        before_keys = {
            _provenance_issue_key(seg)
            for seg in before_segments
            if _provenance_issue_key(seg)
        }
        after_segments = _extract_raw_candidate_segments(after_note_raw)
        after_keys = {
            _provenance_issue_key(seg)
            for seg in after_segments
            if _provenance_issue_key(seg)
        }
        for key, canonical_issue in reason_auto_by_key.items():
            if key in after_keys and key not in before_keys and key not in seen_keys:
                seen_keys.add(key)
                proven.append(canonical_issue)
    return proven


async def resolve_row_proven_auto_issues(
    audit_repo: Any,
    *,
    class_code: str,
    lesson: str,
    week: int,
    year: int,
    session_id: str | None = None,
    class_date_iso: str | None = None,
) -> list[str]:
    """Query ``audit_repo`` (when supported) and return proven auto issues for one BCNB row."""
    if audit_repo is None or not hasattr(audit_repo, "list_checker_note_audits_for_class"):
        return []
    try:
        call_res = audit_repo.list_checker_note_audits_for_class(class_code)
        raw_audits = await call_res if hasattr(call_res, "__await__") else call_res
    except Exception:
        return []
    if not isinstance(raw_audits, (list, tuple)):
        return []
    return extract_proven_auto_issues_from_audits(
        raw_audits,
        class_code=class_code,
        lesson=lesson,
        week=week,
        year=year,
        session_id=session_id,
        class_date_iso=class_date_iso,
    )


def _split_by_pattern_with_seps(text: str, pattern: re.Pattern[str]) -> tuple[list[str], list[str]]:
    parts: list[str] = []
    seps: list[str] = []
    last_end = 0
    for match in pattern.finditer(text):
        parts.append(text[last_end : match.start()])
        seps.append(match.group(0))
        last_end = match.end()
    parts.append(text[last_end:])
    return parts, seps


def split_checker_issue_note(
    raw_note: Any,
    *,
    proven_auto_issues: Sequence[Any] | None = None,
    audit_records: Sequence[Mapping[str, Any]] | None = None,
    row_identity: Mapping[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Split a BCNB checker note into ``(manual_note, auto_issues)``.

    Direct ``[LMS]``/``[Zalo]`` segments are classified as auto ONLY when proven by
    ``proven_auto_issues`` or ``audit_records`` + ``row_identity`` (5.2.4).
    All unproven segments are treated as manual and preserved byte-for-byte.
    """
    text = preserve_bcnb_note_text(raw_note)
    if not text:
        return "", []

    proven_list: list[Any] = list(proven_auto_issues or [])
    if audit_records is not None and isinstance(row_identity, Mapping):
        proven_list.extend(
            extract_proven_auto_issues_from_audits(
                audit_records,
                class_code=str(row_identity.get("class_code") or ""),
                lesson=str(row_identity.get("lesson") or ""),
                week=int(row_identity.get("week") or 0),
                year=int(row_identity.get("year") or 0),
                session_id=(
                    str(
                        row_identity.get("session_id")
                        or row_identity.get("bcnb_session_id")
                        or ""
                    ).strip()
                    or None
                ),
                class_date_iso=(
                    str(
                        row_identity.get("class_date_iso")
                        or row_identity.get("class_date")
                        or ""
                    ).strip()
                    or None
                ),
            )
        )
    proven_keys: set[str] = set()
    proven_meta_by_key: dict[str, dict[str, str]] = {}
    for item in proven_list:
        key_p = _provenance_issue_key(item)
        if key_p:
            proven_keys.add(key_p)
            if key_p not in proven_meta_by_key:
                proven_meta_by_key[key_p] = coerce_structured_issue(item)

    if not proven_keys:
        return text, []

    auto_issues: list[str] = []
    seen_keys: set[str] = set()
    remaining = text
    pattern = re.compile(r"\[Auto\s+QC:\s*", flags=re.IGNORECASE)
    search_from = 0
    while True:
        match = pattern.search(remaining, search_from)
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
                    inside = remaining[pos:idx]
                    end_idx = idx + 1
                    break
        if depth > 0:
            inside = remaining[pos:]
            end_idx = len(remaining)
        semi_re_inner = re.compile(r" ?; ?")
        inner_parts, inner_seps = _split_by_pattern_with_seps(inside, semi_re_inner)
        kept_inner: list[tuple[int, str]] = []
        wrapper_removed_any = False
        for i_idx, raw_seg in enumerate(inner_parts):
            cleaned_seg = " ".join(raw_seg.split()).strip(" ;|")
            if not cleaned_seg:
                kept_inner.append((i_idx, raw_seg))
                continue
            key = _provenance_issue_key(cleaned_seg)
            if key and key in proven_keys:
                norm_seg = normalize_auto_issue_text(cleaned_seg)
                norm_seg = re.sub(r"\s*[;|]+\s*", ", ", norm_seg)
                balanced_seg = _balance_issue_brackets(norm_seg)
                wrapper_removed_any = True
                if key not in seen_keys:
                    seen_keys.add(key)
                    meta = proven_meta_by_key.get(key) or coerce_structured_issue(balanced_seg)
                    auto_issues.append(
                        StructuredIssueStr(
                            balanced_seg,
                            code=meta["code"],
                            severity=meta["severity"],
                        )
                    )
            else:
                kept_inner.append((i_idx, raw_seg))
        if not wrapper_removed_any:
            search_from = end_idx
            continue
        non_empty_inner = [(idx, val) for idx, val in kept_inner if val.strip()]
        if non_empty_inner:
            rebuilt_inside = non_empty_inner[0][1]
            for prev_item, curr_item in zip(non_empty_inner, non_empty_inner[1:]):
                sep = (
                    inner_seps[prev_item[0]]
                    if curr_item[0] == prev_item[0] + 1
                    else "; "
                )
                rebuilt_inside += sep + curr_item[1]
            closing = "]" if depth == 0 else ""
            rebuilt_wrapper = f"{remaining[start_idx:pos]}{rebuilt_inside}{closing}"
            remaining = remaining[:start_idx] + rebuilt_wrapper + remaining[end_idx:]
            search_from = start_idx + len(rebuilt_wrapper)
        else:
            before = re.sub(r" ?\| ?$", "", remaining[:start_idx])
            after = re.sub(r"^ ?\| ?", "", remaining[end_idx:])
            if before and after:
                remaining = f"{before} | {after}"
                search_from = len(before) + 3
            else:
                remaining = before or after
                search_from = len(before) if before else 0

    pipe_re = re.compile(r" ?\| ?")
    semi_re = re.compile(r" ?; ?")
    pipe_parts, pipe_seps = _split_by_pattern_with_seps(remaining, pipe_re)
    kept_pipe_parts: list[tuple[int, str]] = []
    any_removed = False

    for p_idx, pipe_chunk in enumerate(pipe_parts):
        semi_parts, semi_seps = _split_by_pattern_with_seps(pipe_chunk, semi_re)
        kept_semi: list[tuple[int, str]] = []
        chunk_removed = False
        for s_idx, seg in enumerate(semi_parts):
            cleaned_seg = " ".join(seg.split()).strip(" ;|")
            if not cleaned_seg:
                kept_semi.append((s_idx, seg))
                continue
            key = _provenance_issue_key(cleaned_seg)
            if key and key in proven_keys:
                norm_seg = normalize_auto_issue_text(cleaned_seg)
                norm_seg = re.sub(r"\s*[;|]+\s*", ", ", norm_seg)
                balanced_seg = _balance_issue_brackets(norm_seg)
                chunk_removed = True
                any_removed = True
                if key not in seen_keys:
                    seen_keys.add(key)
                    meta = proven_meta_by_key.get(key) or coerce_structured_issue(balanced_seg)
                    auto_issues.append(
                        StructuredIssueStr(
                            balanced_seg,
                            code=meta["code"],
                            severity=meta["severity"],
                        )
                    )
            else:
                kept_semi.append((s_idx, seg))

        if not chunk_removed:
            if pipe_chunk:
                kept_pipe_parts.append((p_idx, pipe_chunk))
        else:
            non_empty_semi = [(idx, val) for idx, val in kept_semi if val.strip()]
            if non_empty_semi:
                rebuilt_chunk = non_empty_semi[0][1]
                for prev_item, curr_item in zip(non_empty_semi, non_empty_semi[1:]):
                    sep = (
                        semi_seps[prev_item[0]]
                        if curr_item[0] == prev_item[0] + 1
                        else "; "
                    )
                    rebuilt_chunk += sep + curr_item[1]
                kept_pipe_parts.append((p_idx, rebuilt_chunk))

    if not any_removed:
        return remaining, auto_issues
    if not kept_pipe_parts:
        return "", auto_issues

    rebuilt_manual = kept_pipe_parts[0][1]
    for prev_part, curr_part in zip(kept_pipe_parts, kept_pipe_parts[1:]):
        sep = (
            pipe_seps[prev_part[0]]
            if curr_part[0] == prev_part[0] + 1
            else " | "
        )
        rebuilt_manual += sep + curr_part[1]
    return rebuilt_manual, auto_issues


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
    auto_issues: Sequence[Any] | None,
    *,
    max_length: int = 500,
    proven_auto_issues: Sequence[Any] | None = None,
    audit_records: Sequence[Mapping[str, Any]] | None = None,
    row_identity: Mapping[str, Any] | None = None,
) -> str:
    """Merge auto-detected QC issues into a checker note while preserving manual QC notes."""
    limit = max(48, int(max_length))
    manual_part, _ = split_checker_issue_note(
        existing_note,
        proven_auto_issues=proven_auto_issues,
        audit_records=audit_records,
        row_identity=row_identity,
    )
    existing_manual_keys: set[str] = {
        _provenance_issue_key(seg)
        for seg in _extract_raw_candidate_segments(manual_part)
        if _provenance_issue_key(seg)
    }
    deduped_issues: list[str] = []
    seen_keys: set[str] = set(existing_manual_keys)
    for raw_issue in auto_issues or []:
        cleaned = " ".join(issue_text(raw_issue).split()).strip(" ;|")
        if not cleaned or _is_legacy_lms_duplicate_issue(cleaned):
            continue
        prov_key = _provenance_issue_key(cleaned)
        norm_cleaned = normalize_auto_issue_text(cleaned)
        norm_cleaned = re.sub(r"\s*[;|]+\s*", ", ", norm_cleaned)
        balanced = _balance_issue_brackets(norm_cleaned)
        key = prov_key or _normalize_auto_issue_key(balanced)
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
    observed_preserved = preserve_bcnb_note_text(observed_raw)
    expected_preserved = preserve_bcnb_note_text(expected_note)
    return (observed_preserved == expected_preserved, observed_preserved)


async def read_checker_note_by_session(
    page,
    *,
    session_id: str,
    class_code: str | None = None,
    lesson: str | None = None,
) -> tuple[bool, str | None]:
    """Read the current checker note cell by ``data-session-id`` without reloading.

    Returns ``(row_inspected, observed_note)``:
    - ``(False, None)`` when ``page`` does not expose a live DOM table (e.g., generic mock).
    - ``(True, None)`` when the live DOM table is inspected and the row/note is missing or unreadable.
    - ``(True, note_text)`` when the row's checker note cell is read cleanly.
    """
    target_sid = str(session_id or "").strip()
    if not target_sid or page is None or not hasattr(page, "locator"):
        return (False, None)
    try:
        table_rows_loc = page.locator("table tr")
        if not hasattr(table_rows_loc, "all"):
            return (False, None)
        rows = await table_rows_loc.all()
        if not isinstance(rows, (list, tuple)) or len(rows) == 0:
            return (False, None)
    except Exception:
        return (False, None)

    try:
        class_index, lesson_index = await resolve_class_lesson_columns(page)
        ext_cols = await resolve_extended_bcnb_columns(page)
    except Exception:
        return (True, None)

    checker_index = ext_cols.get("checker_note_index")
    if checker_index is None:
        return (True, None)

    target_code = _canonical_class_code(class_code or "") if class_code else ""
    target_lesson = str(lesson).strip() if lesson is not None else None
    matched_cells = []
    for row in rows:
        row_sid = ""
        if hasattr(row, "get_attribute"):
            try:
                row_sid = str((await row.get_attribute("data-session-id")) or "").strip()
            except Exception:
                row_sid = ""
        if row_sid != target_sid:
            continue
        cells = await row.locator("td").all()
        required_index = max(class_index, lesson_index, checker_index)
        if len(cells) <= required_index:
            return (True, None)
        if target_code:
            observed_code = _canonical_class_code(
                (await cells[class_index].inner_text()).strip()
            )
            if observed_code != target_code:
                return (True, None)
        if target_lesson is not None:
            observed_lesson = (await cells[lesson_index].inner_text()).strip()
            if observed_lesson != target_lesson:
                return (True, None)
        matched_cells.append(cells[checker_index])

    if len(matched_cells) != 1:
        return (True, None)
    observed_raw = await _read_optional_note_cell(matched_cells[0], note_type="checker")
    if observed_raw is None:
        return (True, None)
    return (True, preserve_bcnb_note_text(observed_raw))
