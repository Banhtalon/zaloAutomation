import asyncio
import json
import os
import re
from typing import Any
import unicodedata

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from googleform_autosubmit.config import Config
from core.time_utils import business_day_start, now_business
from logger_utils import log_message
from repositories.audit_repository import AuditRepository
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobCancelledError, JobRepository
from services.browser_job_guard import exclusive_browser_job
from services.bcnb_actions import execute_verified_checkbox_action
from services.bcnb_safety import (
    resolve_report_columns,
    select_last_week_checked,
)
from services.evidence_store import cleanup_old_screenshots
from services.external_gateways import ExternalGateways
from services.zalo_discovery_matching import find_scan_result_matches
from services.zalo_discovery_service import run_auto_discovery as run_auto_discovery
from services.zalo_safety import open_chat_matches
from services.zalo_scan_evidence import (
    build_empty_evidence_result,
    scan_open_zalo_chat,
)
from services.zalo_evidence import (
    LESSON_14_EXEMPTION,
    apply_lesson_14_exemption,
    get_evidence_reference,
)


group_repo = GroupRepository()
job_repo = JobRepository()
audit_repo = AuditRepository()

async def run_zalo_automation(
    job_id: int,
    execution_mode: str = "dry-run",
    *,
    runtime=None,
):
    try:
        async with exclusive_browser_job(
            job_id,
            job_repo,
            lock=runtime.browser_lock if runtime else None,
        ):
            await _run_zalo_automation(
                job_id,
                execution_mode,
                external=(
                    runtime.external
                    if runtime
                    else ExternalGateways.production()
                ),
            )
    except JobCancelledError as exc:
        actions = await audit_repo.list_for_job(job_id, 200)
        verified = [item for item in actions if item["outcome"] == "VERIFIED"]
        await job_repo.update_job(
            job_id,
            status="PARTIAL_SUCCESS" if verified else "CANCELLED",
            progress=100,
            message=(
                f"Đã dừng theo yêu cầu sau {len(verified)} thay đổi đã kiểm chứng."
                if verified
                else str(exc)
            ),
            results=json.dumps(
                {
                    "kind": "zalo_scan_cancelled",
                    "verified_actions": len(verified),
                    "cancelled": True,
                },
                ensure_ascii=False,
            ),
        )


async def _run_zalo_automation(
    job_id: int,
    execution_mode: str,
    *,
    external: ExternalGateways | None = None,
):
    dry_run = execution_mode != "production"
    cleanup_old_screenshots(days=14)
    job_screenshots_dir = os.path.join("static", "screenshots", f"job-{job_id}")
    os.makedirs(job_screenshots_dir, exist_ok=True)
    scan_results: list[dict[str, Any]] = []
    await job_repo.update_job(job_id, status="RUNNING", message="Đang quét báo cáo...", progress=5)
    log_message("Bắt đầu tiến trình quét báo cáo tự động...")
    
    try:
        await job_repo.ensure_not_cancelled(job_id)
        groups_list = await group_repo.get_all_groups()
        if not groups_list:
            await job_repo.update_job(job_id, status="FAILED", message="Lỗi: Không có dữ liệu lớp học trong Database.")
            log_message("Lỗi: Không có dữ liệu lớp học trong Database.")
            return
    except Exception as e:
        await job_repo.update_job(job_id, status="FAILED", message="Lỗi: Không thể lấy danh sách nhóm từ Database")
        log_message(f"Lỗi: Không thể lấy danh sách nhóm ({e})")
        return

    today = business_day_start()

    external = external or ExternalGateways.production()
    async with external.browser.persistent_page() as page:
        
        log_message("Đang truy cập Zalo Web...")
        await page.goto('https://chat.zalo.me/')
        await page.wait_for_selector('#contact-search-input', timeout=120000)

        # --- KIỂM TRA NHÓM BỊ ẨN ĐỂ PHỤC HỒI (NẾU CÓ THÔNG BÁO KHAI GIẢNG) ---
        ignored_groups_list = await group_repo.get_all_ignored_groups(
            include_archived=True
        )
        for group_name in ignored_groups_list:
            await job_repo.ensure_not_cancelled(job_id)
            log_message(f"Kiểm tra nhanh nhóm ẩn: {group_name}...")
            search = page.locator('#contact-search-input')
            await search.clear()
            await search.fill(group_name)
            await asyncio.sleep(1)
            await page.keyboard.press('Enter')
            
            try:
                await page.wait_for_selector('[data-component="bubble-message"]', timeout=4000)
                messages = await page.locator('[data-component="bubble-message"]').all()
                last_3_messages_text = []
                for msg in messages[-3:]:
                    try:
                        last_3_messages_text.append(await msg.inner_text())
                    except Exception:
                        continue
                    
                is_new_course = any("Thông báo khai giảng" in text for text in last_3_messages_text)
                if is_new_course:
                    await group_repo.remove_ignored_group(group_name)
                    await group_repo.add_group(group_name, "Chưa xác định")
                    log_message(f"🌟 Nhóm {group_name} có thông báo khai giảng! Đã phục hồi vào danh sách kiểm tra.")
            except PlaywrightTimeoutError:
                log_message(
                    f"Không đọc kịp tin nhắn nhóm ẩn {group_name}; giữ nguyên trạng thái.",
                    level="WARNING",
                )
        
        # --- CẬP NHẬT LẠI DANH SÁCH NHÓM CHÍNH (Vì có thể vừa phục hồi) ---
        groups_list = await group_repo.get_all_groups()

        for g in groups_list:
            await job_repo.ensure_not_cancelled(job_id)
            group_name = g.get('TenNhom')
            person = g.get('NguoiPhuTrach', 'Chưa xác định')
            class_date_str = g.get('NgayHoc', '').strip()
            scanned_at = now_business()
            
            log_message(f"Đang kiểm tra nhóm: {group_name} ({person})...")
            # --- TỐI ƯU 1: CHỐNG TRÔI NHÓM & SMART WAIT ---
            search = page.locator('#contact-search-input')
            await search.clear() # Xóa sạch bộ nhớ đệm
            
            # Tạo chuỗi tìm kiếm an toàn (loại bỏ phần trong ngoặc đơn vì dễ gây lỗi khoảng trắng trên Zalo)
            search_query = re.sub(r'\s*\([^)]*\)', '', group_name).strip()
            await search.fill(search_query)
            await asyncio.sleep(2.0) # Zalo render search results nhanh hơn
            
            # 1. Kiểm tra nếu Zalo báo "Không tìm thấy"
            try:
                body_text = await page.locator('body').inner_text()
                if "Không tìm thấy" in body_text or "No results found" in body_text:
                    # Fallback tìm kiếm bằng phần tử cốt lõi (Mã lớp)
                    normalized_original = unicodedata.normalize('NFC', group_name)
                    parts = re.split(r'[-|()\[\]\u2013\u2014]', normalized_original)
                    fallback_query = ""
                    for part in reversed(parts): # Thường mã lớp nằm ở các phần cuối
                        clean_p = part.strip()
                        if len(clean_p) >= 4 and "mindx" not in clean_p.lower() and "lập trình" not in clean_p.lower() and "game" not in clean_p.lower() and "web" not in clean_p.lower() and "app" not in clean_p.lower():
                            fallback_query = clean_p
                            break
                    if fallback_query:
                        log_message(f"Không tìm thấy tên đầy đủ, thử tìm bằng mã: '{fallback_query}'")
                        await search.clear()
                        await search.fill(fallback_query)
                        await asyncio.sleep(2.0)
                        
                        body_text_fb = await page.locator('body').inner_text()
                        if "Không tìm thấy" in body_text_fb or "No results found" in body_text_fb:
                            log_message(f"❌ Lỗi: Không tìm thấy nhóm {group_name} trên Zalo (Kể cả khi tìm bằng mã)!")
                            scan_results.append(
                                build_empty_evidence_result(
                                    group=group_name,
                                    person=person,
                                    actual_group_name=None,
                                    scanned_at=scanned_at,
                                    today=today,
                                    reason="Không tìm thấy nhóm trên Zalo.",
                                )
                            )
                            await page.keyboard.press('Escape')
                            continue
                    else:
                        log_message(f"❌ Lỗi: Không tìm thấy nhóm {group_name} trên Zalo!")
                        scan_results.append(
                            build_empty_evidence_result(
                                group=group_name,
                                person=person,
                                actual_group_name=None,
                                scanned_at=scanned_at,
                                today=today,
                                reason="Không tìm thấy nhóm trên Zalo.",
                            )
                        )
                        await page.keyboard.press('Escape')
                        continue
            except PlaywrightTimeoutError:
                log_message(
                    f"Không đọc kịp trạng thái tìm kiếm của {group_name}; "
                    "sẽ dựa vào bước xác minh header tiếp theo.",
                    level="WARNING",
                )
                
            await page.keyboard.press('Enter')
            
            try:
                await page.wait_for_selector('[data-component="bubble-message"]', timeout=4000)
            except PlaywrightTimeoutError:
                await asyncio.sleep(2) # Fallback nếu timeout
                
            # 2. XÁC MINH CHAT ĐANG MỞ CÓ ĐÚNG KHÔNG (fail closed)
            try:
                header_text = await page.locator('.header-title, .chat-title, [class*="conv-header"] .truncate').first.inner_text(timeout=1500)
                if not open_chat_matches(group_name, header_text):
                    log_message(f"❌ CẢNH BÁO: Mở nhầm nhóm Zalo! (Đang mở: {header_text.strip()[:50]} | Cần mở: {group_name})")
                    scan_results.append(
                        build_empty_evidence_result(
                            group=group_name,
                            person=person,
                            actual_group_name=header_text.strip(),
                            scanned_at=scanned_at,
                            today=today,
                            reason="Tên nhóm Zalo thực tế không khớp cấu hình.",
                            classifier_decision="GROUP_IDENTITY_MISMATCH",
                        )
                    )
                    continue
            except Exception as exc:
                log_message(f"Không xác minh được tiêu đề chat Zalo cho {group_name}: {exc}")
                scan_results.append(
                    build_empty_evidence_result(
                        group=group_name,
                        person=person,
                        actual_group_name=None,
                        scanned_at=scanned_at,
                        today=today,
                        reason="Không xác minh được nhóm Zalo đang mở.",
                        classifier_decision="GROUP_IDENTITY_UNVERIFIED",
                    )
                )
                continue
            
            result = await scan_open_zalo_chat(
                page,
                job_id=job_id,
                screenshots_dir=job_screenshots_dir,
                group_name=group_name,
                actual_group_name=header_text.strip(),
                person=person,
                class_date_str=class_date_str,
                today=today,
                ensure_not_cancelled=lambda: job_repo.ensure_not_cancelled(job_id),
            )
            scan_results.append(result)
            log_message(f"Kết quả nhóm {group_name}: {result['status']}")

        # ==========================================
        # BƯỚC 5: QUAY LẠI TRANG BCNB ĐỂ TICK TỰ ĐỘNG
        # ==========================================
        await job_repo.update_job(job_id, message="Đang cập nhật kết quả lên BCNB...", progress=80)
        log_message("Đang truy cập BCNB để cập nhật trạng thái tích chọn...")
        await job_repo.ensure_not_cancelled(job_id)
        await page.goto(Config.BCNB_URL)
        
        # --- AUTO LOGIN BCNB ---
        try:
            username_input = page.locator('input[name="username"]')
            try:
                await username_input.first.wait_for(state="visible", timeout=10000)
            except Exception:
                log_message("BCNB không hiện form đăng nhập; tiếp tục với session hiện tại.")
            if await username_input.count() > 0 and await username_input.first.is_visible():
                log_message("Hệ thống yêu cầu đăng nhập BCNB. Đang tự động điền tài khoản...")
                await username_input.first.fill(Config.BCNB_USERNAME)
                await page.locator('input[name="password"]').first.fill(Config.BCNB_PASSWORD)
                await page.locator('button[type="submit"]').first.click()
                await page.wait_for_load_state('networkidle')
        except Exception as e:
            log_message(f"Lỗi trong quá trình tự động đăng nhập BCNB: {e}")
        # -----------------------
        
        try:
            await job_repo.ensure_not_cancelled(job_id)
            selected_week, selected_year = await select_last_week_checked(page)
            class_index, lesson_index, report_index = await resolve_report_columns(page)
            log_message(f"Đã kiểm chứng BCNB ở Tuần {selected_week} / {selected_year}.")
            await asyncio.sleep(2)

            rows = await page.locator('table tr').all()
            log_message(f"Tìm thấy {len(rows)} dòng trên bảng BCNB. Bắt đầu đối chiếu...")

            bcnb_rows = []
            for row in rows:
                await job_repo.ensure_not_cancelled(job_id)
                cells = await row.locator('td').all()
                if len(cells) > max(class_index, lesson_index, report_index):
                    ma_lop_bcnb = await cells[class_index].inner_text()
                    ma_lop_bcnb = ma_lop_bcnb.strip().upper()
                    buoi_so = await cells[lesson_index].inner_text()
                    buoi_so = buoi_so.strip()
                    bcnb_rows.append({
                        "cells": cells,
                        "ma_lop": ma_lop_bcnb,
                        "buoi_so": buoi_so,
                    })

            bcnb_data = [{"ma_lop": item["ma_lop"]} for item in bcnb_rows]
            for bcnb_row in bcnb_rows:
                await job_repo.ensure_not_cancelled(job_id)
                cells = bcnb_row["cells"]
                ma_lop_bcnb = bcnb_row["ma_lop"]
                buoi_so = bcnb_row["buoi_so"]
                is_submitted = False
                is_buoi_14 = (buoi_so == "14")
                matched_result = None
                result_matches = find_scan_result_matches(
                    ma_lop_bcnb,
                    scan_results,
                    bcnb_data,
                )
                if len(result_matches) > 1:
                    raise RuntimeError(
                        f"Mã lớp {ma_lop_bcnb} khớp nhiều nhóm Zalo; dừng để tránh tick nhầm."
                    )
                if result_matches:
                    matched_result = result_matches[0]
                    is_submitted = bool(
                        matched_result.get("submitted_for_bcnb", False)
                    )

                if is_buoi_14:
                    is_submitted = True
                    matched_result = apply_lesson_14_exemption(
                        matched_result,
                        class_code=ma_lop_bcnb,
                        applied_at=now_business(),
                    )
                    if matched_result not in scan_results:
                        scan_results.append(matched_result)
                    log_message(
                        f"Lớp {ma_lop_bcnb}: Buổi 14 được MIỄN BÁO CÁO. "
                        "Đã ghi LESSON_14_EXEMPTION."
                    )

                if is_submitted:
                    checkbox = cells[report_index].locator('input[type="checkbox"]').first
                    if await checkbox.count() > 0:
                        is_checked = await checkbox.is_checked()
                        if not is_checked or is_buoi_14:
                            evidence_path, evidence_sha256 = (
                                get_evidence_reference(matched_result)
                            )
                            outcome = await execute_verified_checkbox_action(
                                page=page,
                                checkbox=checkbox,
                                audit_repo=audit_repo,
                                job_repo=job_repo,
                                job_id=job_id,
                                action_key=(
                                    f"bcnb-report:{job_id}:{selected_year}:W{selected_week}:"
                                    f"{ma_lop_bcnb}:{buoi_so}"
                                ),
                                action_type="BCNB_REPORT_TICK",
                                class_code=ma_lop_bcnb,
                                column_kind="report",
                                week=selected_week,
                                year=selected_year,
                                lesson=buoi_so,
                                execution_mode=execution_mode,
                                reason={
                                    "week": selected_week,
                                    "year": selected_year,
                                    "lesson": buoi_so,
                                    "submitted": is_submitted,
                                    "evidence_state": (
                                        matched_result.get("evidence_state")
                                        if matched_result
                                        else None
                                    ),
                                    "lesson_14_exemption": is_buoi_14,
                                    "evidence_decision": (
                                        LESSON_14_EXEMPTION
                                        if is_buoi_14
                                        else None
                                    ),
                                },
                                evidence_path=evidence_path,
                                evidence_sha256=evidence_sha256,
                                settle_seconds=0.7,
                            )
                            if outcome == "DRY_RUN":
                                log_message(f"[DRY-RUN] Sẽ Tick xanh cho lớp: {ma_lop_bcnb}" + (" (Miễn báo cáo Buổi 14)" if is_buoi_14 else ""))
                            else:
                                log_message(f"Đã tick và kiểm chứng BCNB cho lớp: {ma_lop_bcnb}")

            log_message("ĐÃ CẬP NHẬT TOÀN BỘ LÊN HỆ THỐNG BCNB THÀNH CÔNG!")
        except Exception as e:
            log_message(f"Lỗi khi cập nhật BCNB: {e}")
            raise RuntimeError("Không thể hoàn tất cập nhật BCNB an toàn.") from e

        # ------------------------------------------
    final_message = "Hoàn tất mô phỏng, chưa thay đổi BCNB." if dry_run else "Hoàn tất!"
    await job_repo.update_job(job_id, status="SUCCESS", message=final_message, progress=100, results=json.dumps(scan_results, ensure_ascii=False))
    log_message("Tiến trình quét hoàn tất.")
