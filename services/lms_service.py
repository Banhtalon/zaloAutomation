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
    build_lms_plan,
    compute_plan_digest,
    normalize_class_code,
)


job_repo = JobRepository()
lms_repo = LmsRepository()
audit_repo = AuditRepository()


async def _extract_missing_classes(
    image_path: str,
    gateway: LmsOcrGateway | None = None,
) -> list[str]:
    selected = gateway or ExternalGateways.production().lms_ocr
    return await selected.extract_missing_classes(image_path)


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
    image_path: str,
    execution_mode: str = "dry-run",
    *,
    runtime=None,
):
    dry_run = execution_mode != "production"
    external = runtime.external if runtime else ExternalGateways.production()
    await job_repo.update_job(
        job_id,
        status="RUNNING",
        message="Đang phân tích ảnh danh sách LMS...",
        progress=10,
    )
    try:
        await job_repo.ensure_not_cancelled(job_id)
        missing_classes = await _extract_missing_classes(image_path, external.lms_ocr)
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
            await job_repo.update_job(
                job_id,
                status="RUNNING",
                message="Đang đọc BCNB để lập kế hoạch an toàn...",
                progress=55,
            )
            async with external.browser.persistent_page(
                viewport={"width": 1280, "height": 720}
            ) as page:
                (
                    _page,
                    week,
                    year,
                    snapshot,
                    _class_index,
                    _lms_index,
                ) = await _read_bcnb_snapshot(page)
                plan = build_lms_plan(
                    missing_classes, snapshot, week, year, dry_run
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
    try:
        await job_repo.ensure_not_cancelled(job_id)
        if compute_plan_digest(plan) != expected_digest:
            raise LmsSafetyError("Kế hoạch LMS lưu trữ không còn toàn vẹn.")

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
                fresh_plan = build_lms_plan(
                    plan["missing_classes"],
                    snapshot,
                    week,
                    year,
                    dry_run,
                )
                if fresh_plan["plan_digest"] != expected_digest:
                    raise LmsSafetyError(
                        "Bảng BCNB đã thay đổi sau bước xem trước. Hãy phân tích lại ảnh."
                    )

                targets = set(plan["classes_to_tick"])
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
            "planned_classes": plan["classes_to_tick"],
            "verified_classes": verified,
            "failed_classes": failed,
        }
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
            "planned_classes": plan.get("classes_to_tick", []),
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
