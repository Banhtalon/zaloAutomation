import asyncio
import json
import secrets
import traceback

from googleform_autosubmit.config import Config
from logger_utils import log_message
from repositories.audit_repository import AuditRepository
from repositories.job_repository import JobCancelledError
from repositories.job_repository import JobRepository
from repositories.lms_repository import LmsRepository
from services.browser_job_guard import exclusive_browser_job
from services.bcnb_safety import (
    resolve_lms_columns,
    select_last_week_checked,
)
from services.bcnb_actions import execute_verified_checkbox_action
from services.external_gateways import ExternalGateways, LmsOcrGateway
from services.lms_plan import (
    LmsSafetyError,
    MAX_LMS_STUDENTS_PER_BATCH,
    build_lms_plan,
    compute_plan_digest,
    compute_selection_digest,
    format_overdue_session_detail,
    merge_overdue_session_details,
    normalize_class_code,
    normalize_lms_student_class_codes,
    validate_selected_classes_to_tick,
)


job_repo = JobRepository()
lms_repo = LmsRepository()
audit_repo = AuditRepository()

LMS_CLASS_HEADERS = (
    "tên lớp học",
    "mã lớp",
    "lớp học",
    "class name",
    "class code",
)
LMS_DUE_COMMENT_HEADERS = (
    "buổi học gần nhất quá hạn nhận xét",
    "quá hạn nhận xét",
    "last session due comments",
    "due comments",
    "due comment",
)
LMS_STUDENT_NAME_HEADERS = (
    "họ và tên",
    "họ tên",
    "tên học viên",
    "học viên",
    "student name",
    "full name",
    "fullname",
)
LMS_STUDENT_EXACT_HEADERS = {"name", "student", "students"}
LMS_STUDENT_COUNT_EXCLUDES = (
    "student count",
    "student counts",
    "số lượng",
    "sĩ số",
    "count",
)
EMPTY_CELL_MARKERS = {"", "-", "–", "—", "n/a", "không có", "none"}
LMS_UI_NOISE_MARKERS = EMPTY_CELL_MARKERS | {
    "default",
    "class site",
    "running",
    "finished",
    "pending",
    "active",
    "inactive",
    "tên học viên",
    "họ và tên",
    "họ tên",
    "student name",
    "full name",
    "stt",
}
LMS_MAX_TABLE_COLUMNS = 50
LMS_MAX_TABLE_ROWS_PER_PAGE = MAX_LMS_STUDENTS_PER_BATCH + 1


async def _extract_missing_classes(
    image_path: str,
    gateway: LmsOcrGateway | None = None,
) -> list[str]:
    selected = gateway or ExternalGateways.production().lms_ocr
    return await selected.extract_missing_classes(image_path)


async def _wait_for_lms_login_or_table(page, max_attempts: int = 40) -> None:
    for _ in range(max_attempts):
        password_input = page.locator(
            'input[type="password"], input[name="password"]'
        ).first
        if await password_input.count() > 0 and await password_input.is_visible():
            return
        try:
            th_loc = page.locator("table th")
            if hasattr(th_loc, "count"):
                th_count = await th_loc.count()
                if isinstance(th_count, int) and th_count > LMS_MAX_TABLE_COLUMNS:
                    return
            headers = await th_loc.all()
            if len(headers) > LMS_MAX_TABLE_COLUMNS:
                return
            if headers:
                tr_loc = page.locator("table tr")
                if hasattr(tr_loc, "count"):
                    tr_count = await tr_loc.count()
                    if (
                        isinstance(tr_count, int)
                        and tr_count > LMS_MAX_TABLE_ROWS_PER_PAGE
                    ):
                        return
                rows = await tr_loc.all()
                if len(rows) > LMS_MAX_TABLE_ROWS_PER_PAGE:
                    return
                for row in rows:
                    td_loc = row.locator("td")
                    if hasattr(td_loc, "count"):
                        td_count = await td_loc.count()
                        if (
                            isinstance(td_count, int)
                            and td_count > LMS_MAX_TABLE_COLUMNS
                        ):
                            return
                    cells = await td_loc.all()
                    if len(cells) > 1:
                        return
        except Exception:
            pass
        await asyncio.sleep(0.5)


async def _login_lms_if_needed(page) -> None:
    await _wait_for_lms_login_or_table(page)
    password_input = page.locator(
        'input[type="password"], input[name="password"]'
    ).first
    if await password_input.count() == 0 or not await password_input.is_visible():
        return

    if not Config.LMS_USERNAME or not Config.LMS_PASSWORD:
        raise LmsSafetyError(
            "Trang LMS yêu cầu đăng nhập nhưng chưa cấu hình LMS_USERNAME/LMS_PASSWORD trong .env."
        )

    username_input = page.locator(
        'input[name="username"], input[name="email"], input[type="email"], input[type="text"]'
    ).first
    if await username_input.count() == 0 or not await username_input.is_visible():
        raise LmsSafetyError(
            "Không tìm thấy ô nhập tài khoản trên trang đăng nhập LMS."
        )

    await username_input.fill(Config.LMS_USERNAME)
    await password_input.fill(Config.LMS_PASSWORD)
    submit_btn = page.locator(
        'button[type="submit"], input[type="submit"]'
    ).first
    if await submit_btn.count() == 0:
        raise LmsSafetyError("Không tìm thấy nút đăng nhập trên trang LMS.")
    await submit_btn.click()
    await page.wait_for_load_state("networkidle")

    current_url = str(getattr(page, "url", "") or "")
    if "login" in current_url:
        for _ in range(50):
            await asyncio.sleep(0.5)
            current_url = str(getattr(page, "url", "") or "")
            if "login" not in current_url:
                break

    current_url = str(getattr(page, "url", "") or "")
    if current_url and "class-sessions" not in current_url:
        await page.goto(Config.LMS_URL)
        await page.wait_for_load_state("networkidle")

    await _wait_for_lms_login_or_table(page)


async def _resolve_lms_session_columns(page) -> tuple[int, int]:
    headers = [
        " ".join((await cell.inner_text()).strip().lower().split())
        for cell in await page.locator("table th").all()
    ]
    class_index = next(
        (
            idx
            for idx, text in enumerate(headers)
            if any(token in text for token in LMS_CLASS_HEADERS)
        ),
        None,
    )
    due_comment_index = next(
        (
            idx
            for idx, text in enumerate(headers)
            if any(token in text for token in LMS_DUE_COMMENT_HEADERS)
        ),
        None,
    )
    if (
        class_index is None
        or due_comment_index is None
        or class_index == due_comment_index
    ):
        raise LmsSafetyError(
            "Không xác định được cột 'Tên lớp học' hoặc 'Buổi học gần nhất quá hạn nhận xét' trên trang LMS."
        )
    return class_index, due_comment_index


async def _snapshot_lms_page_rows(
    page, class_index: int, due_comment_index: int
) -> list[tuple[str, str]]:
    rows = await page.locator("table tr").all()
    snapshot: list[tuple[str, str]] = []
    for row in rows:
        cells = await row.locator("td").all()
        if len(cells) <= max(class_index, due_comment_index):
            continue
        raw_class = (await cells[class_index].inner_text()).strip()
        if not raw_class:
            continue
        class_code = normalize_class_code(raw_class)
        due_comment_text = (
            await cells[due_comment_index].inner_text()
        ).strip()
        snapshot.append((class_code, due_comment_text))
    return snapshot


async def _wait_for_lms_table_settled(
    page,
    class_index: int,
    due_comment_index: int,
    *,
    previous_codes: list[str] | None = None,
) -> list[tuple[str, str]]:
    await asyncio.sleep(3.0)
    last_snapshot: list[tuple[str, str]] = []
    saw_duplicate_previous = False
    for _ in range(16):
        try:
            spinners = page.locator(
                '[role="progressbar"], .MuiCircularProgress-root, .MuiLinearProgress-root, .ant-spin-spinning'
            )
            if await spinners.count() > 0 and await spinners.first.is_visible():
                last_snapshot = []
                await asyncio.sleep(0.8)
                continue
        except Exception:
            pass

        current = await _snapshot_lms_page_rows(
            page, class_index, due_comment_index
        )
        current_codes = [code for code, _ in current]
        if not current_codes:
            last_snapshot = []
            await asyncio.sleep(0.8)
            continue
        if previous_codes is not None and current_codes == previous_codes:
            saw_duplicate_previous = True
            last_snapshot = []
            await asyncio.sleep(0.8)
            continue

        if current == last_snapshot:
            return current
        last_snapshot = current
        await asyncio.sleep(1.2)
    if saw_duplicate_previous:
        raise LmsSafetyError(
            "Phân trang LMS bị kẹt ở trang trùng lặp; dừng an toàn để tránh bỏ sót lớp."
        )
    raise LmsSafetyError(
        "Bảng dữ liệu LMS chưa tải xong hoặc không ổn định; dừng an toàn để tránh đọc thiếu lớp."
    )


async def _advance_lms_next_page(page) -> bool:
    next_btn = page.locator(
        'button[aria-label="Next Page"]:not([disabled]):not(.Mui-disabled), '
        'button[title="Go to next page"]:not([disabled]):not(.Mui-disabled), '
        'li.ant-pagination-next:not(.ant-pagination-disabled) button, '
        'li.ant-pagination-next:not(.ant-pagination-disabled), '
        'button[aria-label="next page"]:not([disabled]), '
        'button[aria-label="Go to next page"]:not([disabled])'
    ).first
    if await next_btn.count() == 0 or not await next_btn.is_visible():
        return False
    await next_btn.click()
    await page.wait_for_load_state("networkidle")
    return True


async def _read_lms_missing_classes(
    page,
    max_pages: int = 20,
    *,
    details_out: dict[str, str] | None = None,
) -> list[str]:
    if not Config.LMS_URL:
        raise LmsSafetyError("Chưa cấu hình LMS_URL trong .env.")
    if max_pages <= 0:
        raise LmsSafetyError("Giới hạn số trang quét LMS không hợp lệ; dừng an toàn.")
    await page.goto(Config.LMS_URL)
    await page.wait_for_load_state("networkidle")
    await _login_lms_if_needed(page)

    missing_classes: list[str] = []
    seen_missing: set[str] = set()
    previous_page_codes: list[str] | None = None

    for page_idx in range(max_pages):
        class_index, due_comment_index = await _resolve_lms_session_columns(page)
        page_rows = await _wait_for_lms_table_settled(
            page,
            class_index,
            due_comment_index,
            previous_codes=previous_page_codes,
        )
        page_codes = [code for code, _ in page_rows]
        if not page_codes:
            raise LmsSafetyError(
                "Không đọc được dữ liệu lớp từ trang LMS; dừng an toàn để tránh bỏ sót lớp."
            )
        if previous_page_codes is not None and page_codes == previous_page_codes:
            raise LmsSafetyError(
                "Phân trang LMS bị kẹt ở trang trùng lặp; dừng an toàn để tránh bỏ sót lớp."
            )
        for class_code, due_comment_text in page_rows:
            if due_comment_text.lower() not in EMPTY_CELL_MARKERS:
                if class_code not in seen_missing:
                    seen_missing.add(class_code)
                    missing_classes.append(class_code)
                if isinstance(details_out, dict):
                    formatted = format_overdue_session_detail(due_comment_text)
                    if formatted:
                        details_out[class_code] = merge_overdue_session_details(
                            details_out.get(class_code),
                            formatted,
                        )

        previous_page_codes = page_codes
        if not await _advance_lms_next_page(page):
            break
        if page_idx == max_pages - 1:
            raise LmsSafetyError(
                f"Bảng danh sách lớp LMS vượt quá giới hạn phân trang an toàn ({max_pages} trang); dừng an toàn để tránh bỏ sót lớp."
            )

    return missing_classes


async def _login_and_select_last_week(page):
    await page.goto(Config.BCNB_URL)
    username_input = page.locator('input[name="username"]').first
    if await username_input.count() > 0 and await username_input.is_visible():
        await username_input.fill(Config.BCNB_USERNAME)
        await page.locator('input[name="password"]').first.fill(Config.BCNB_PASSWORD)
        await page.locator('button[type="submit"]').first.click()
        await page.wait_for_load_state("networkidle")

    try:
        week, year = await select_last_week_checked(page)
    except RuntimeError as exc:
        raise LmsSafetyError(str(exc)) from exc
    await asyncio.sleep(2)
    return week, year


async def _read_lms_table(page):
    try:
        class_index, lms_index = await resolve_lms_columns(page)
    except RuntimeError as exc:
        raise LmsSafetyError(str(exc)) from exc

    snapshot = []
    rows = await page.locator("table tr").all()
    for row in rows:
        cells = await row.locator("td").all()
        if len(cells) <= max(class_index, lms_index):
            continue
        class_code = normalize_class_code((await cells[class_index].inner_text()).strip())
        checkbox = cells[lms_index].locator('input[type="checkbox"]').first
        if await checkbox.count() == 0:
            raise LmsSafetyError(f"Không tìm thấy checkbox LMS của lớp {class_code}.")
        snapshot.append(
            {"class_code": class_code, "checked": await checkbox.is_checked()}
        )
    if not snapshot:
        raise LmsSafetyError("Không đọc được dòng lớp nào từ bảng BCNB.")
    return snapshot, class_index, lms_index


async def _handle_bcnb_dialog(dialog) -> None:
    if dialog.type in {"alert", "confirm"}:
        await dialog.accept()
    else:
        await dialog.dismiss()


async def _read_bcnb_snapshot(page):
    page.on(
        "dialog",
        lambda dialog: asyncio.create_task(_handle_bcnb_dialog(dialog)),
    )
    week, year = await _login_and_select_last_week(page)
    snapshot, class_index, lms_index = await _read_lms_table(page)
    return page, week, year, snapshot, class_index, lms_index


async def run_lms_analysis(
    job_id: int,
    image_path: str | None = None,
    execution_mode: str = "dry-run",
    *,
    runtime=None,
):
    dry_run = execution_mode != "production"
    external = runtime.external if runtime else ExternalGateways.production()
    use_web_lms = not image_path
    await job_repo.update_job(
        job_id,
        status="RUNNING",
        message=(
            "Đang quét danh sách lớp trên trang LMS..."
            if use_web_lms
            else "Đang phân tích ảnh danh sách LMS..."
        ),
        progress=10,
    )
    try:
        await job_repo.ensure_not_cancelled(job_id)
        missing_classes: list[str] = []
        overdue_details: dict[str, str] = {}
        if image_path is not None:
            missing_classes = await _extract_missing_classes(
                image_path, external.lms_ocr
            )
            await job_repo.ensure_not_cancelled(job_id)
            await job_repo.update_job(
                job_id,
                message="Đang đọc BCNB để lập kế hoạch an toàn...",
                progress=45,
            )

        async with exclusive_browser_job(
            job_id,
            job_repo,
            lock=runtime.browser_lock if runtime else None,
            process_lock=getattr(runtime, "profile_lock", None) if runtime else None,
        ):
            async with external.browser.persistent_page(
                viewport={"width": 1280, "height": 720}
            ) as page:
                if use_web_lms:
                    await job_repo.update_job(
                        job_id,
                        status="RUNNING",
                        message="Đang quét trang LMS để lấy lớp quá hạn nhận xét...",
                        progress=25,
                    )
                    try:
                        missing_classes = await _read_lms_missing_classes(
                            page, details_out=overdue_details
                        )
                    except TypeError:
                        missing_classes = await _read_lms_missing_classes(page)
                    await job_repo.ensure_not_cancelled(job_id)

                await job_repo.update_job(
                    job_id,
                    status="RUNNING",
                    message="Đang đọc BCNB để lập kế hoạch an toàn...",
                    progress=55,
                )
                (
                    _page,
                    week,
                    year,
                    snapshot,
                    _class_index,
                    _lms_index,
                ) = await _read_bcnb_snapshot(page)
                plan = build_lms_plan(
                    missing_classes,
                    snapshot,
                    week,
                    year,
                    dry_run,
                    allow_unmatched_missing=use_web_lms,
                    allow_empty_missing=use_web_lms,
                    overdue_details=overdue_details,
                )

        if not plan["can_confirm"]:
            plan["analysis_job_id"] = job_id
            plan["confirmation_token"] = None
            await job_repo.update_job(
                job_id,
                status="FAILED",
                message="Dừng an toàn: " + "; ".join(plan["blocking_issues"]),
                progress=100,
                results=json.dumps(plan, ensure_ascii=False),
            )
            return

        token = secrets.token_urlsafe(32)
        plan["analysis_job_id"] = job_id
        plan["confirmation_token"] = token
        stored_plan = dict(plan)
        stored_plan.pop("confirmation_token", None)
        await lms_repo.save_confirmation(
            job_id, token, plan["plan_digest"], stored_plan
        )
        await job_repo.update_job(
            job_id,
            status="SUCCESS",
            message=(
                f"Đã lập kế hoạch: {len(plan['classes_to_tick'])} lớp sẽ được "
                + ("mô phỏng." if dry_run else "tick sau khi xác nhận.")
            ),
            progress=100,
            results=json.dumps(plan, ensure_ascii=False),
        )
        log_message(
            f"LMS preview tuần {week}/{year}: {len(plan['missing_classes'])} lớp không tick, "
            f"{len(plan['classes_to_tick'])} lớp dự kiến tick."
        )
    except JobCancelledError as exc:
        await job_repo.update_job(
            job_id,
            status="CANCELLED",
            message=str(exc),
            progress=100,
        )
    except Exception as exc:
        log_message(f"Lỗi phân tích LMS: {exc}")
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message=f"Dừng an toàn: {str(exc)[:180]}",
            progress=100,
        )
    finally:
        # Giai đoạn 2 giữ input theo retention để job đọc có thể retry an toàn.
        pass


async def run_lms_automation(
    job_id: int,
    plan: dict,
    expected_digest: str,
    execution_mode: str = "dry-run",
    *,
    runtime=None,
    expected_selection_digest: str | None = None,
):
    dry_run = execution_mode != "production"
    external = runtime.external if runtime else ExternalGateways.production()
    await job_repo.update_job(
        job_id,
        message="Đang chờ quyền sử dụng trình duyệt...",
        progress=5,
    )
    verified = []
    failed = []
    planned_classes = list(
        plan.get("selected_classes_to_tick") or plan.get("classes_to_tick", [])
    )
    try:
        await job_repo.ensure_not_cancelled(job_id)
        if compute_plan_digest(plan) != expected_digest:
            raise LmsSafetyError("Kế hoạch LMS lưu trữ không còn toàn vẹn.")

        has_explicit_selection_fields = (
            expected_selection_digest is not None
            or "selection_digest" in plan
            or "selected_classes_to_tick" in plan
            or "analysis_job_id" in plan
        )
        db_selection_digest = None
        lookup_fn = getattr(lms_repo, "get_consumed_selection_digest", None)
        if callable(lookup_fn):
            raw_analysis_id = plan.get("analysis_job_id")
            if raw_analysis_id is not None and (
                isinstance(raw_analysis_id, bool)
                or not isinstance(raw_analysis_id, int)
                or raw_analysis_id <= 0
            ):
                raise LmsSafetyError(
                    "Mã job phân tích LMS trong kế hoạch không hợp lệ.",
                    code="LMS_CONFIRMATION_CONTRACT_MISMATCH",
                )
            analysis_id = int(raw_analysis_id) if raw_analysis_id is not None else None
            db_selection_digest = await lookup_fn(
                execution_job_id=job_id,
                analysis_job_id=analysis_id,
                plan_digest=expected_digest,
                require_record=isinstance(lms_repo, LmsRepository)
                and has_explicit_selection_fields,
            )

        effective_selection_digest = (
            db_selection_digest
            if db_selection_digest is not None
            else (
                expected_selection_digest
                if expected_selection_digest is not None
                else plan.get("selection_digest")
            )
        )
        has_selection_contract = (
            effective_selection_digest is not None
            or has_explicit_selection_fields
        )
        base_classes = list(plan.get("classes_to_tick", []))
        if has_selection_contract:
            planned_classes = validate_selected_classes_to_tick(
                plan.get("selected_classes_to_tick"),
                base_classes,
            )
            required_digests = [
                digest
                for digest in (
                    db_selection_digest,
                    expected_selection_digest,
                    plan.get("selection_digest"),
                )
                if digest is not None
            ]
            if required_digests or "selection_digest" in plan:
                plan_sel_digest = plan.get("selection_digest")
                computed_sel_digest = compute_selection_digest(
                    expected_digest, planned_classes
                )
                if (
                    not required_digests
                    or any(
                        not isinstance(digest, str)
                        or not digest
                        or not secrets.compare_digest(digest, computed_sel_digest)
                        for digest in required_digests
                    )
                    or (
                        "selection_digest" in plan
                        and (
                            not isinstance(plan_sel_digest, str)
                            or not secrets.compare_digest(
                                plan_sel_digest, computed_sel_digest
                            )
                        )
                    )
                ):
                    raise LmsSafetyError(
                        "Danh sách lớp được chọn để tick LMS không còn toàn vẹn.",
                        code="LMS_SELECTION_DIGEST_MISMATCH",
                    )
        else:
            planned_classes = base_classes

        async with exclusive_browser_job(
            job_id,
            job_repo,
            lock=runtime.browser_lock if runtime else None,
            process_lock=getattr(runtime, "profile_lock", None) if runtime else None,
        ):
            await job_repo.update_job(
                job_id,
                status="RUNNING",
                message="Đang kiểm tra lại bảng BCNB trước khi thực hiện...",
                progress=20,
            )
            async with external.browser.persistent_page(
                viewport={"width": 1280, "height": 720}
            ) as page:
                (
                    page,
                    week,
                    year,
                    snapshot,
                    class_index,
                    lms_index,
                ) = await _read_bcnb_snapshot(page)
                allow_web_mode = plan.get("source") == "lms_web"
                fresh_plan = build_lms_plan(
                    plan["missing_classes"],
                    snapshot,
                    week,
                    year,
                    dry_run,
                    allow_unmatched_missing=allow_web_mode,
                    allow_empty_missing=allow_web_mode,
                    overdue_details=plan.get("overdue_details"),
                )
                if fresh_plan["plan_digest"] != expected_digest:
                    raise LmsSafetyError(
                        "Bảng BCNB đã thay đổi sau bước xem trước. Hãy phân tích lại ảnh."
                    )

                targets = set(planned_classes)
                rows = await page.locator("table tr").all()
                for row in rows:
                    cells = await row.locator("td").all()
                    if len(cells) <= max(class_index, lms_index):
                        continue
                    code = normalize_class_code(
                        (await cells[class_index].inner_text()).strip()
                    )
                    if code not in targets:
                        continue
                    await job_repo.ensure_not_cancelled(job_id)
                    checkbox = cells[lms_index].locator('input[type="checkbox"]').first
                    try:
                        outcome = await execute_verified_checkbox_action(
                            page=page,
                            checkbox=checkbox,
                            audit_repo=audit_repo,
                            job_repo=job_repo,
                            job_id=job_id,
                            action_key=f"lms:{job_id}:{year}:W{week}:{code}",
                            action_type="BCNB_LMS_TICK",
                            class_code=code,
                            column_kind="lms",
                            week=week,
                            year=year,
                            execution_mode=execution_mode,
                            reason={
                                "week": week,
                                "year": year,
                                "plan_digest": expected_digest,
                            },
                        )
                        verified.append(code)
                        if outcome == "DRY_RUN":
                            log_message(f"[DRY-RUN] Sẽ tick LMS cho lớp {code}")
                        else:
                            log_message(f"Đã tick và kiểm chứng LMS cho lớp {code}")
                    except Exception as exc:
                        failed.append({"class_code": code, "error": str(exc)})
                        break

                missing_targets = sorted(
                    targets
                    - set(verified)
                    - {f["class_code"] for f in failed}
                )
                failed.extend(
                    {"class_code": code, "error": "Không tìm thấy dòng mục tiêu."}
                    for code in missing_targets
                )

        result = {
            "kind": "lms_execution",
            "dry_run": dry_run,
            "week": plan["week"],
            "year": plan["year"],
            "planned_classes": planned_classes,
            "verified_classes": verified,
            "failed_classes": failed,
        }
        if plan.get("selected_classes_to_tick") is not None:
            result["selected_classes_to_tick"] = planned_classes
        if failed and verified:
            status = "PARTIAL_SUCCESS"
        elif failed:
            status = "FAILED"
        else:
            status = "SUCCESS"
        message = (
            f"Dry-run hoàn tất: {len(verified)} lớp dự kiến tick."
            if dry_run
            else f"Đã tick và kiểm chứng {len(verified)} lớp."
        )
        if failed:
            message += f" Có {len(failed)} lớp lỗi."
        await job_repo.update_job(
            job_id,
            status=status,
            message=message,
            progress=100,
            results=json.dumps(result, ensure_ascii=False),
        )
    except JobCancelledError as exc:
        result = {
            "kind": "lms_execution",
            "dry_run": dry_run,
            "week": plan.get("week"),
            "year": plan.get("year"),
            "planned_classes": planned_classes,
            "verified_classes": verified,
            "failed_classes": failed,
            "cancelled": True,
        }
        await job_repo.update_job(
            job_id,
            status="PARTIAL_SUCCESS" if verified else "CANCELLED",
            message=(
                f"Đã dừng theo yêu cầu sau {len(verified)} lớp."
                if verified
                else str(exc)
            ),
            progress=100,
            results=json.dumps(result, ensure_ascii=False),
        )
    except Exception as exc:
        log_message(f"Lỗi LMS Automation: {exc}\n{traceback.format_exc()}")
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message=f"Dừng an toàn: {str(exc)[:180]}",
            progress=100,
        )


def _clean_lms_student_name(raw_name: str) -> str:
    if not isinstance(raw_name, str):
        return ""
    cleaned = " ".join(raw_name.strip().split())
    if not cleaned:
        return ""
    if cleaned.casefold() in LMS_UI_NOISE_MARKERS:
        return ""
    if cleaned.isdigit() or len(cleaned) > 200:
        return ""
    return cleaned


async def _bounded_locator_items(
    locator, max_items: int, *, error_message: str
) -> list:
    if hasattr(locator, "count"):
        count_val = await locator.count()
        if isinstance(count_val, int) and count_val > max_items:
            raise LmsSafetyError(error_message)
    items = await locator.all()
    if len(items) > max_items:
        raise LmsSafetyError(error_message)
    return items


async def _find_lms_student_and_class_columns(
    page,
) -> tuple[int | None, int | None]:
    try:
        header_cells = await _bounded_locator_items(
            page.locator("table th"),
            LMS_MAX_TABLE_COLUMNS,
            error_message="Số lượng cột bảng trên trang LMS vượt quá giới hạn an toàn.",
        )
    except LmsSafetyError:
        raise
    except Exception:
        header_cells = []
    headers = [
        " ".join((await cell.inner_text()).strip().lower().split())
        for cell in header_cells
    ]
    class_index = next(
        (
            idx
            for idx, text in enumerate(headers)
            if any(token in text for token in LMS_CLASS_HEADERS)
        ),
        None,
    )
    student_index = next(
        (
            idx
            for idx, text in enumerate(headers)
            if idx != class_index
            and not any(excl in text for excl in LMS_STUDENT_COUNT_EXCLUDES)
            and (
                any(token in text for token in LMS_STUDENT_NAME_HEADERS)
                or text in LMS_STUDENT_EXACT_HEADERS
            )
        ),
        None,
    )
    return student_index, class_index


async def _try_lms_search_class(page, target_code: str) -> bool:
    try:
        search_loc = page.locator(
            'input[placeholder="Tìm kiếm"], input[placeholder*="Tìm kiếm"], '
            'input[name="search"], input[type="search"]'
        ).first
        if await search_loc.count() == 0 or not await search_loc.is_visible():
            return False
        await search_loc.fill(target_code)
    except Exception:
        return False
    try:
        refresh_btn = page.locator(
            'button:has-text("Làm mới"), button:has-text("Tìm kiếm"), button[type="submit"]'
        ).first
        if await refresh_btn.count() > 0 and await refresh_btn.is_visible():
            await refresh_btn.click()
    except Exception:
        pass
    await page.wait_for_load_state("networkidle")
    return True


async def _open_lms_class_row(page, row, class_cell) -> bool:
    clicked = False
    for target in (class_cell, row):
        if clicked or target is None:
            break
        if hasattr(target, "locator"):
            try:
                link = target.locator("a").first
                if await link.count() > 0 and await link.is_visible():
                    await link.click()
                    clicked = True
                    break
            except Exception:
                pass
        if hasattr(target, "click"):
            try:
                await target.click()
                clicked = True
                break
            except Exception:
                pass
    if not clicked:
        return False
    await page.wait_for_load_state("networkidle")
    try:
        student_tab = page.locator(
            '[role="tab"]:has-text("Học viên"), button:has-text("Học viên"), '
            'a:has-text("Học viên"), [role="tab"]:has-text("Students"), '
            'button:has-text("Students")'
        ).first
        if await student_tab.count() > 0 and await student_tab.is_visible():
            await student_tab.click()
            await page.wait_for_load_state("networkidle")
    except Exception:
        pass
    return True


async def _extract_students_from_table_for_classes(
    page,
    target_codes: list[str],
    student_index: int,
    class_index: int | None,
    *,
    allow_unscoped_class: bool = False,
    seen_keys: set[tuple[str, str]],
    students_by_class: dict[str, list[dict[str, str]]],
    total_counter: list[int],
    max_pages: int = 10,
    job_id: int | None = None,
) -> None:
    target_set = set(target_codes)
    previous_signature: list[tuple[str, str]] | None = None

    for page_idx in range(max_pages):
        if job_id is not None:
            await job_repo.ensure_not_cancelled(job_id)
        if page_idx > 0:
            s_idx, c_idx = await _find_lms_student_and_class_columns(page)
            if s_idx is not None:
                student_index = s_idx
                class_index = c_idx

        if class_index is None and (
            not allow_unscoped_class or len(target_codes) != 1
        ):
            raise LmsSafetyError(
                "Bảng học viên LMS không có cột mã lớp để xác minh quyền sở hữu lớp."
            )

        rows = await _bounded_locator_items(
            page.locator("table tr"),
            LMS_MAX_TABLE_ROWS_PER_PAGE,
            error_message=(
                f"Số lượng dòng bảng trên LMS vượt quá giới hạn an toàn "
                f"(tối đa {MAX_LMS_STUDENTS_PER_BATCH} học viên mỗi đợt)."
            ),
        )
        page_signature: list[tuple[str, str]] = []
        for row in rows:
            cells = await _bounded_locator_items(
                row.locator("td"),
                LMS_MAX_TABLE_COLUMNS,
                error_message="Số lượng cột trong dòng bảng LMS vượt quá giới hạn an toàn.",
            )
            required_idx = (
                student_index
                if class_index is None
                else max(student_index, class_index)
            )
            if len(cells) <= required_idx:
                continue
            raw_name = (await cells[student_index].inner_text()).strip()
            name = _clean_lms_student_name(raw_name)
            extra_ident = ""
            if 0 not in {student_index, class_index} and len(cells) > 0:
                extra_ident = (await cells[0].inner_text()).strip()
            row_student_sig = (
                f"{name or raw_name}|{extra_ident}"
                if extra_ident
                else (name or raw_name)
            )
            if class_index is not None:
                raw_class = (await cells[class_index].inner_text()).strip()
                if not raw_class:
                    if row_student_sig:
                        page_signature.append(("", row_student_sig))
                    continue
                try:
                    row_code = normalize_class_code(raw_class)
                except LmsSafetyError:
                    page_signature.append((raw_class, row_student_sig))
                    continue
                if row_code not in target_set:
                    page_signature.append((row_code, row_student_sig))
                    continue
            else:
                row_code = target_codes[0]

            page_signature.append((row_code, row_student_sig))
            if not name:
                continue
            key = (name.casefold(), row_code)
            if key in seen_keys:
                raise LmsSafetyError(
                    f"Phát hiện học viên trùng tên trong lớp {row_code} trên LMS; dừng an toàn để kiểm tra thủ công."
                )
            if total_counter[0] >= MAX_LMS_STUDENTS_PER_BATCH:
                raise LmsSafetyError(
                    f"Số lượng học viên vượt quá giới hạn an toàn (tối đa {MAX_LMS_STUDENTS_PER_BATCH} học viên mỗi đợt)."
                )
            seen_keys.add(key)
            total_counter[0] += 1
            students_by_class[row_code].append(
                {"name": name, "class_code": row_code}
            )

        if not page_signature:
            if page_idx > 0:
                raise LmsSafetyError(
                    "Phân trang bảng học viên LMS trả về trang rỗng; dừng an toàn."
                )
            break
        if page_signature == previous_signature:
            raise LmsSafetyError(
                "Phân trang bảng học viên LMS bị kẹt ở trang trùng lặp; dừng an toàn."
            )
        previous_signature = page_signature
        if not await _advance_lms_next_page(page):
            break
        if page_idx == max_pages - 1:
            raise LmsSafetyError(
                f"Bảng học viên LMS vượt quá giới hạn phân trang an toàn ({max_pages} trang); dừng an toàn."
            )


async def _extract_students_from_detail_locators(
    page,
    target_code: str,
    *,
    seen_keys: set[tuple[str, str]],
    students_by_class: dict[str, list[dict[str, str]]],
    total_counter: list[int],
    max_pages: int = 10,
    job_id: int | None = None,
) -> bool:
    previous_signature: list[str] | None = None
    for page_idx in range(max_pages):
        if job_id is not None:
            await job_repo.ensure_not_cancelled(job_id)
        remaining = max(0, MAX_LMS_STUDENTS_PER_BATCH - total_counter[0])
        try:
            items = await _bounded_locator_items(
                page.locator(
                    '[data-student-name], .student-name, [data-testid="student-name"]'
                ),
                remaining,
                error_message=(
                    f"Số lượng học viên vượt quá giới hạn an toàn "
                    f"(tối đa {MAX_LMS_STUDENTS_PER_BATCH} học viên mỗi đợt)."
                ),
            )
        except LmsSafetyError:
            raise
        except Exception:
            if page_idx > 0:
                raise LmsSafetyError(
                    f"Phân trang chi tiết học viên lớp {target_code} trên LMS gặp lỗi đọc phần tử; dừng an toàn."
                )
            return False

        page_signature: list[str] = []
        for item in items:
            raw_name = await item.inner_text()
            name = _clean_lms_student_name(raw_name)
            if not name:
                continue
            page_signature.append(name)
            key = (name.casefold(), target_code)
            if key in seen_keys:
                raise LmsSafetyError(
                    f"Phát hiện học viên trùng tên trong lớp {target_code} trên LMS; dừng an toàn để kiểm tra thủ công."
                )
            if total_counter[0] >= MAX_LMS_STUDENTS_PER_BATCH:
                raise LmsSafetyError(
                    f"Số lượng học viên vượt quá giới hạn an toàn (tối đa {MAX_LMS_STUDENTS_PER_BATCH} học viên mỗi đợt)."
                )
            seen_keys.add(key)
            total_counter[0] += 1
            students_by_class[target_code].append(
                {"name": name, "class_code": target_code}
            )

        if not page_signature:
            if page_idx > 0:
                raise LmsSafetyError(
                    f"Phân trang chi tiết học viên lớp {target_code} trên LMS trả về trang rỗng; dừng an toàn."
                )
            return False
        if page_signature == previous_signature:
            raise LmsSafetyError(
                f"Phân trang chi tiết học viên lớp {target_code} trên LMS bị kẹt ở trang trùng lặp; dừng an toàn."
            )
        previous_signature = page_signature
        if not await _advance_lms_next_page(page):
            break
        if page_idx == max_pages - 1:
            raise LmsSafetyError(
                f"Danh sách học viên lớp {target_code} trên LMS vượt quá giới hạn phân trang an toàn ({max_pages} trang); dừng an toàn."
            )
    return bool(students_by_class[target_code])


async def _scan_lms_class_rows_on_page(
    page, target_code: str, class_index: int
) -> tuple[list[str], list[tuple[object, object]]]:
    rows = await _bounded_locator_items(
        page.locator("table tr"),
        LMS_MAX_TABLE_ROWS_PER_PAGE,
        error_message="Số lượng dòng bảng lớp học trên LMS vượt quá giới hạn an toàn.",
    )
    page_codes: list[str] = []
    matching_rows: list[tuple[object, object]] = []
    for row in rows:
        cells = await _bounded_locator_items(
            row.locator("td"),
            LMS_MAX_TABLE_COLUMNS,
            error_message="Số lượng cột trong dòng bảng LMS vượt quá giới hạn an toàn.",
        )
        if len(cells) <= class_index:
            continue
        raw_class = (await cells[class_index].inner_text()).strip()
        if not raw_class:
            continue
        try:
            row_code = normalize_class_code(raw_class)
        except LmsSafetyError:
            continue
        page_codes.append(row_code)
        if row_code == target_code:
            matching_rows.append((row, cells[class_index]))
    return page_codes, matching_rows


async def _find_and_open_lms_class_in_table(
    page,
    target_code: str,
    class_index: int,
    *,
    max_pages: int = 20,
    job_id: int | None = None,
) -> bool:
    previous_codes: list[str] | None = None
    found_page_idx: int | None = None
    for page_idx in range(max_pages):
        if job_id is not None:
            await job_repo.ensure_not_cancelled(job_id)
        if page_idx > 0:
            _, c_idx = await _find_lms_student_and_class_columns(page)
            if c_idx is not None:
                class_index = c_idx

        page_codes, matching_rows = await _scan_lms_class_rows_on_page(
            page, target_code, class_index
        )
        if len(matching_rows) > 1 or (
            found_page_idx is not None and len(matching_rows) >= 1
        ):
            raise LmsSafetyError(
                f"Phát hiện nhiều dòng trùng mã lớp {target_code} trên trang LMS; dừng an toàn."
            )
        if len(matching_rows) == 1:
            found_page_idx = page_idx

        if not page_codes:
            if page_idx > 0:
                raise LmsSafetyError(
                    "Phân trang danh sách lớp LMS trả về trang rỗng; dừng an toàn."
                )
            break
        if page_codes == previous_codes:
            raise LmsSafetyError(
                "Phân trang danh sách lớp LMS bị kẹt ở trang trùng lặp; dừng an toàn."
            )
        previous_codes = page_codes
        if not await _advance_lms_next_page(page):
            if found_page_idx == page_idx and len(matching_rows) == 1:
                match_row, match_cell = matching_rows[0]
                return await _open_lms_class_row(page, match_row, match_cell)
            break
        if page_idx == max_pages - 1:
            raise LmsSafetyError(
                f"Danh sách lớp LMS vượt quá giới hạn phân trang an toàn ({max_pages} trang); dừng an toàn."
            )

    if found_page_idx is None:
        return False

    await page.goto(Config.LMS_URL)
    await page.wait_for_load_state("networkidle")
    await _try_lms_search_class(page, target_code)
    _, c_idx = await _find_lms_student_and_class_columns(page)
    if c_idx is not None:
        class_index = c_idx
    for _ in range(found_page_idx):
        if job_id is not None:
            await job_repo.ensure_not_cancelled(job_id)
        if not await _advance_lms_next_page(page):
            raise LmsSafetyError(
                f"Không thể quay lại trang chứa lớp {target_code} trên LMS; dừng an toàn."
            )
        _, c_idx = await _find_lms_student_and_class_columns(page)
        if c_idx is not None:
            class_index = c_idx

    _, matching_rows = await _scan_lms_class_rows_on_page(
        page, target_code, class_index
    )
    if len(matching_rows) != 1:
        raise LmsSafetyError(
            f"Bảng danh sách lớp LMS đã thay đổi khi mở lớp {target_code}; dừng an toàn."
        )
    match_row, match_cell = matching_rows[0]
    return await _open_lms_class_row(page, match_row, match_cell)


async def _read_lms_students_by_classes(
    page,
    class_codes: list[str] | str,
    *,
    job_id: int | None = None,
) -> list[dict[str, str]]:
    if not Config.LMS_URL:
        raise LmsSafetyError("Chưa cấu hình LMS_URL trong .env.")
    normalized_codes = normalize_lms_student_class_codes(class_codes)
    await page.goto(Config.LMS_URL)
    await page.wait_for_load_state("networkidle")
    await _login_lms_if_needed(page)

    seen_keys: set[tuple[str, str]] = set()
    students_by_class: dict[str, list[dict[str, str]]] = {
        code: [] for code in normalized_codes
    }
    total_counter = [0]

    student_idx, class_idx = await _find_lms_student_and_class_columns(page)
    if student_idx is not None and class_idx is not None:
        await _extract_students_from_table_for_classes(
            page,
            normalized_codes,
            student_idx,
            class_idx,
            allow_unscoped_class=False,
            seen_keys=seen_keys,
            students_by_class=students_by_class,
            total_counter=total_counter,
            job_id=job_id,
        )
        for target_code in normalized_codes:
            if not students_by_class[target_code] and await _try_lms_search_class(
                page, target_code
            ):
                s_idx, c_idx = await _find_lms_student_and_class_columns(page)
                if s_idx is not None:
                    if c_idx is None:
                        raise LmsSafetyError(
                            f"Bảng kết quả tìm kiếm lớp {target_code} trên LMS thiếu cột mã lớp để xác minh quyền sở hữu."
                        )
                    await _extract_students_from_table_for_classes(
                        page,
                        [target_code],
                        s_idx,
                        c_idx,
                        allow_unscoped_class=False,
                        seen_keys=seen_keys,
                        students_by_class=students_by_class,
                        total_counter=total_counter,
                        job_id=job_id,
                    )
    elif student_idx is not None and class_idx is None:
        raise LmsSafetyError(
            "Bảng học viên trên trang LMS không có cột mã lớp để xác minh học viên thuộc đúng lớp yêu cầu."
        )
    else:
        if class_idx is None:
            raise LmsSafetyError(
                "Không xác định được bảng lớp học hoặc cột học viên trên trang LMS."
            )
        for idx, target_code in enumerate(normalized_codes):
            if job_id is not None:
                await job_repo.ensure_not_cancelled(job_id)
            if idx > 0:
                await page.goto(Config.LMS_URL)
                await page.wait_for_load_state("networkidle")
            await _try_lms_search_class(page, target_code)
            _, current_class_idx = await _find_lms_student_and_class_columns(
                page
            )
            effective_class_idx = (
                current_class_idx
                if current_class_idx is not None
                else class_index_fallback(class_idx)
            )
            opened = await _find_and_open_lms_class_in_table(
                page,
                target_code,
                effective_class_idx,
                job_id=job_id,
            )
            if not opened:
                raise LmsSafetyError(
                    f"Không tìm thấy hoặc không thể mở lớp {target_code} trên trang LMS."
                )
            detail_s_idx, detail_c_idx = (
                await _find_lms_student_and_class_columns(page)
            )
            if detail_s_idx is not None:
                await _extract_students_from_table_for_classes(
                    page,
                    [target_code],
                    detail_s_idx,
                    detail_c_idx,
                    allow_unscoped_class=True,
                    seen_keys=seen_keys,
                    students_by_class=students_by_class,
                    total_counter=total_counter,
                    job_id=job_id,
                )
            else:
                found_detail = await _extract_students_from_detail_locators(
                    page,
                    target_code,
                    seen_keys=seen_keys,
                    students_by_class=students_by_class,
                    total_counter=total_counter,
                    job_id=job_id,
                )
                if not found_detail:
                    raise LmsSafetyError(
                        f"Không xác định được bảng danh sách học viên của lớp {target_code} trên LMS."
                    )

    missing_student_classes = [
        code for code in normalized_codes if not students_by_class.get(code)
    ]
    if missing_student_classes:
        raise LmsSafetyError(
            "Không tìm thấy danh sách học viên hợp lệ trên LMS cho lớp: "
            + ", ".join(missing_student_classes)
        )

    return [
        student
        for code in normalized_codes
        for student in students_by_class[code]
    ]


def class_index_fallback(class_idx: int | None) -> int:
    return 0 if class_idx is None else class_idx


async def run_googleform_fetch_lms_students(
    job_id: int,
    class_codes: list[str] | str,
    *,
    runtime=None,
) -> dict:
    gateways = (
        runtime.external if runtime else ExternalGateways.production()
    )
    try:
        normalized_codes = normalize_lms_student_class_codes(class_codes)
        await job_repo.update_job(
            job_id,
            status="RUNNING",
            message=f"Đang kết nối LMS để lấy học viên ({len(normalized_codes)} lớp)...",
            progress=15,
        )
        await job_repo.ensure_not_cancelled(job_id)

        async with exclusive_browser_job(
            job_id,
            job_repo,
            lock=runtime.browser_lock if runtime else None,
            process_lock=getattr(runtime, "profile_lock", None) if runtime else None,
        ):
            async with gateways.browser.persistent_page(
                viewport={"width": 1280, "height": 720}
            ) as page:
                await job_repo.update_job(
                    job_id,
                    status="RUNNING",
                    message=f"Đang trích xuất học viên từ LMS ({len(normalized_codes)} lớp)...",
                    progress=45,
                )
                students = await _read_lms_students_by_classes(
                    page,
                    normalized_codes,
                    job_id=job_id,
                )
                await job_repo.ensure_not_cancelled(job_id)

        if not students:
            raise LmsSafetyError(
                "Không trích xuất được học viên hợp lệ nào từ LMS."
            )

        payload = {
            "kind": "googleform_lms_students",
            "students": students,
            "class_codes": normalized_codes,
            "source_count": len(normalized_codes),
        }
        log_message(
            f"Đã trích xuất {len(students)} học viên từ {len(normalized_codes)} lớp trên LMS."
        )
        await job_repo.update_job(
            job_id,
            status="SUCCESS",
            message=f"Đã lấy {len(students)} học viên từ {len(normalized_codes)} lớp trên LMS.",
            progress=100,
            results=json.dumps(payload, ensure_ascii=False),
        )
        return {"students": students, "class_codes": normalized_codes}
    except JobCancelledError as exc:
        await job_repo.update_job(
            job_id,
            status="CANCELLED",
            progress=100,
            message=str(exc),
        )
        return {"cancelled": True}
    except LmsSafetyError as exc:
        log_message(f"Dừng lấy học viên LMS an toàn: {exc}", level="ERROR")
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message=f"Dừng an toàn: {exc}",
            progress=100,
        )
        return {"error": str(exc)}
    except Exception:
        log_message(
            "Lỗi không mong đợi khi lấy học viên từ LMS.",
            level="ERROR",
        )
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message="Worker lấy học viên LMS dừng an toàn do lỗi không mong đợi.",
            progress=100,
        )
        return {"error": "Worker lấy học viên LMS dừng an toàn do lỗi không mong đợi."}
