import asyncio
import json
import os
import re
from typing import Any
import unicodedata

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from googleform_autosubmit.config import Config
from core.time_utils import business_day_start, now_business, now_utc
from logger_utils import log_message
from repositories.audit_repository import AuditRepository
from repositories.evidence_repository import EvidenceRepository
from repositories.group_repository import GroupRepository
from repositories.job_repository import JobCancelledError, JobRepository
from services.browser_job_guard import exclusive_browser_job
from services.external_gateways import ExternalGateways
from services.zalo_discovery_service import run_auto_discovery as run_auto_discovery
from services.zalo_safety import open_chat_matches
from services.zalo_scan_evidence import (
    attach_zalo_sync_result,
    build_empty_evidence_result,
    ensure_zalo_history_synced,
    scan_open_zalo_chat,
)
from services.zalo_bcnb_service import update_bcnb_reports


group_repo = GroupRepository()
job_repo = JobRepository()
audit_repo = AuditRepository()
evidence_repo = EvidenceRepository()


async def run_zalo_automation(
    job_id: int,
    execution_mode: str = "dry-run",
    *,
    scan_plan: dict | None = None,
    runtime=None,
):
    try:
        async with exclusive_browser_job(
            job_id,
            job_repo,
            lock=runtime.browser_lock if runtime else None,
            process_lock=getattr(runtime, "profile_lock", None) if runtime else None,
        ):
            await _run_zalo_automation(
                job_id,
                execution_mode,
                scan_plan=scan_plan,
                external=(runtime.external if runtime else ExternalGateways.production()),
                evidence_root=(getattr(runtime, "evidence_root", None) if runtime else None),
                runtime=runtime,
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
    scan_plan: dict | None = None,
    external: ExternalGateways | None = None,
    evidence_root: str | None = None,
    runtime=None,
):
    dry_run = execution_mode != "production"
    evidence_root = evidence_root or os.path.abspath(
        os.path.join("static", "screenshots")
    )
    await evidence_repo.expire_old(
        root=evidence_root,
        retention_days=getattr(Config, "EVIDENCE_RETENTION_DAYS", 7),
        policy=getattr(Config, "EVIDENCE_RETENTION_POLICY", "tombstone"),
        delete_files=getattr(Config, "EVIDENCE_DELETE_EXPIRED", True),
    )
    job_screenshots_dir = os.path.join(evidence_root, f"job-{job_id}")
    os.makedirs(job_screenshots_dir, exist_ok=True)
    scan_results: list[dict[str, Any]] = []
    await job_repo.update_job(job_id, status="RUNNING", message="Đang quét báo cáo...", progress=5)
    log_message("Bắt đầu tiến trình quét báo cáo tự động...")

    if not scan_plan:
        await job_repo.update_job(
            job_id,
            status="FAILED",
            message="Dừng an toàn: quét Zalo bắt buộc có snapshot hai tuần do BCNB cung cấp.",
            progress=100,
        )
        log_message(
            "Từ chối quét Zalo không có snapshot hai tuần do BCNB cung cấp.",
            level="ERROR",
            event="zalo.scan_plan_required",
        )
        return

    scan_started_at = now_business()
    try:
        from services.zalo_parallel_service import prepare_frozen_scan_execution

        prepared = await prepare_frozen_scan_execution(
            job_id=job_id,
            scan_plan=scan_plan,
            scan_started_at=scan_started_at,
            runtime=runtime,
            job_repo=job_repo,
        )
        if prepared is None:
            return
        scan_plan, groups_list, reconciliation_only = prepared
    except JobCancelledError:
        raise
    except Exception as e:
        await job_repo.update_job(
            job_id, status="FAILED", message="Lỗi: Không thể lấy danh sách nhóm từ Database"
        )
        log_message(f"Lỗi: Không thể lấy danh sách nhóm ({e})")
        return

    today = business_day_start(scan_started_at)

    external = external or ExternalGateways.production()
    if runtime is not None and hasattr(external.browser, "persistent_session"):
        from services.zalo_parallel_service import (
            run_and_finalize_parallel_zalo_batch,
        )

        await run_and_finalize_parallel_zalo_batch(
            job_id=job_id,
            execution_mode=execution_mode,
            scan_plan=scan_plan,
            today=today,
            scan_started_at=scan_started_at,
            evidence_root=evidence_root,
            screenshots_dir=job_screenshots_dir,
            runtime=runtime,
            job_repo=job_repo,
            audit_repo=audit_repo,
            evidence_repo=evidence_repo,
            reconciliation_only=reconciliation_only,
        )
        return
    async with external.browser.persistent_page() as page:
        log_message("Đang truy cập Zalo Web...")
        await page.goto("https://chat.zalo.me/")
        await page.wait_for_selector("#contact-search-input", timeout=120000)
        sync_result = await ensure_zalo_history_synced(
            page,
            ensure_not_cancelled=lambda: job_repo.ensure_not_cancelled(job_id),
        )
        sync_verified = bool(sync_result["verified"])
        if sync_verified:
            log_message(f"Kiểm tra đồng bộ Zalo: {sync_result['reason']}")
        else:
            log_message(
                f"Không xác minh được đồng bộ Zalo: {sync_result['reason']}",
                level="WARNING",
            )

        # --- KIỂM TRA NHÓM BỊ ẨN ĐỂ PHỤC HỒI (NẾU CÓ THÔNG BÁO KHAI GIẢNG) ---
        ignored_groups_list = (
            await group_repo.get_all_ignored_groups(include_archived=True) if sync_verified else []
        )
        for group_name in ignored_groups_list:
            await job_repo.ensure_not_cancelled(job_id)
            log_message(f"Kiểm tra nhanh nhóm ẩn: {group_name}...")
            search = page.locator("#contact-search-input")
            await search.clear()
            await search.fill(group_name)
            await asyncio.sleep(1)
            await page.keyboard.press("Enter")

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
                    log_message(
                        f"🌟 Nhóm {group_name} có thông báo khai giảng! Đã phục hồi vào danh sách kiểm tra."
                    )
            except PlaywrightTimeoutError:
                log_message(
                    f"Không đọc kịp tin nhắn nhóm ẩn {group_name}; giữ nguyên trạng thái.",
                    level="WARNING",
                )

        # --- CẬP NHẬT LẠI DANH SÁCH NHÓM CHÍNH (Vì có thể vừa phục hồi) ---
        if scan_plan:
            groups_list = [
                {
                    "TenNhom": target.get("group_name", ""),
                    "NguoiPhuTrach": target.get("person", "Chưa xác định"),
                    "NgayHoc": target.get("class_date", ""),
                    "scan_target": target,
                }
                for target in scan_plan.get("targets", [])
                if isinstance(target, dict) and str(target.get("group_name", "")).strip()
            ]
        else:
            groups_list = await group_repo.get_scan_groups()

        for g in groups_list:
            await job_repo.ensure_not_cancelled(job_id)
            group_name = str(g.get("TenNhom") or "").strip()
            person = str(g.get("NguoiPhuTrach") or "Chưa xác định").strip()
            class_date_str = str(g.get("NgayHoc") or "").strip()
            scan_target = g.get("scan_target") or {}
            scanned_at = now_utc()

            log_message(f"Đang kiểm tra nhóm: {group_name} ({person})...")
            if not sync_verified:
                result = build_empty_evidence_result(
                    group=group_name,
                    person=person,
                    actual_group_name=None,
                    scanned_at=scanned_at,
                    today=today,
                    reason=sync_result["reason"],
                    classifier_decision="ZALO_SYNC_INCOMPLETE",
                )
                scan_results.append(attach_zalo_sync_result(result, sync_result))
                continue

            # --- TỐI ƯU 1: CHỐNG TRÔI NHÓM & SMART WAIT ---
            search = page.locator("#contact-search-input")
            await search.clear()  # Xóa sạch bộ nhớ đệm

            # Tạo chuỗi tìm kiếm an toàn (loại bỏ phần trong ngoặc đơn vì dễ gây lỗi khoảng trắng trên Zalo)
            search_query = re.sub(r"\s*\([^)]*\)", "", group_name).strip()
            await search.fill(search_query)
            await asyncio.sleep(2.0)  # Zalo render search results nhanh hơn

            # 1. Kiểm tra nếu Zalo báo "Không tìm thấy"
            try:
                body_text = await page.locator("body").inner_text()
                if "Không tìm thấy" in body_text or "No results found" in body_text:
                    # Fallback tìm kiếm bằng phần tử cốt lõi (Mã lớp)
                    normalized_original = unicodedata.normalize("NFC", group_name)
                    parts = re.split(r"[-|()\[\]\u2013\u2014]", normalized_original)
                    fallback_query = ""
                    for part in reversed(parts):  # Thường mã lớp nằm ở các phần cuối
                        clean_p = part.strip()
                        if (
                            len(clean_p) >= 4
                            and "mindx" not in clean_p.lower()
                            and "lập trình" not in clean_p.lower()
                            and "game" not in clean_p.lower()
                            and "web" not in clean_p.lower()
                            and "app" not in clean_p.lower()
                        ):
                            fallback_query = clean_p
                            break
                    if fallback_query:
                        log_message(
                            f"Không tìm thấy tên đầy đủ, thử tìm bằng mã: '{fallback_query}'"
                        )
                        await search.clear()
                        await search.fill(fallback_query)
                        await asyncio.sleep(2.0)

                        body_text_fb = await page.locator("body").inner_text()
                        if "Không tìm thấy" in body_text_fb or "No results found" in body_text_fb:
                            log_message(
                                f"❌ Lỗi: Không tìm thấy nhóm {group_name} trên Zalo (Kể cả khi tìm bằng mã)!"
                            )
                            scan_results.append(
                                attach_zalo_sync_result(
                                    build_empty_evidence_result(
                                        group=group_name,
                                        person=person,
                                        actual_group_name=None,
                                        scanned_at=scanned_at,
                                        today=today,
                                        reason="Không tìm thấy nhóm trên Zalo.",
                                    ),
                                    sync_result,
                                )
                            )
                            await page.keyboard.press("Escape")
                            continue
                    else:
                        log_message(f"❌ Lỗi: Không tìm thấy nhóm {group_name} trên Zalo!")
                        scan_results.append(
                            attach_zalo_sync_result(
                                build_empty_evidence_result(
                                    group=group_name,
                                    person=person,
                                    actual_group_name=None,
                                    scanned_at=scanned_at,
                                    today=today,
                                    reason="Không tìm thấy nhóm trên Zalo.",
                                ),
                                sync_result,
                            )
                        )
                        await page.keyboard.press("Escape")
                        continue
            except PlaywrightTimeoutError:
                log_message(
                    f"Không đọc kịp trạng thái tìm kiếm của {group_name}; "
                    "sẽ dựa vào bước xác minh header tiếp theo.",
                    level="WARNING",
                )

            await page.keyboard.press("Enter")

            try:
                await page.wait_for_selector('[data-component="bubble-message"]', timeout=4000)
            except PlaywrightTimeoutError:
                await asyncio.sleep(2)  # Fallback nếu timeout

            # 2. XÁC MINH CHAT ĐANG MỞ CÓ ĐÚNG KHÔNG (fail closed)
            try:
                header_text = await page.locator(
                    '.header-title, .chat-title, [class*="conv-header"] .truncate'
                ).first.inner_text(timeout=1500)
                if not open_chat_matches(group_name, header_text):
                    log_message(
                        f"❌ CẢNH BÁO: Mở nhầm nhóm Zalo! (Đang mở: {header_text.strip()[:50]} | Cần mở: {group_name})"
                    )
                    scan_results.append(
                        attach_zalo_sync_result(
                            build_empty_evidence_result(
                                group=group_name,
                                person=person,
                                actual_group_name=header_text.strip(),
                                scanned_at=scanned_at,
                                today=today,
                                reason="Tên nhóm Zalo thực tế không khớp cấu hình.",
                                classifier_decision="GROUP_IDENTITY_MISMATCH",
                            ),
                            sync_result,
                        )
                    )
                    continue
            except Exception as exc:
                log_message(f"Không xác minh được tiêu đề chat Zalo cho {group_name}: {exc}")
                scan_results.append(
                    attach_zalo_sync_result(
                        build_empty_evidence_result(
                            group=group_name,
                            person=person,
                            actual_group_name=None,
                            scanned_at=scanned_at,
                            today=today,
                            reason="Không xác minh được nhóm Zalo đang mở.",
                            classifier_decision="GROUP_IDENTITY_UNVERIFIED",
                        ),
                        sync_result,
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
                scan_target=scan_target if isinstance(scan_target, dict) else None,
                today=today,
                ensure_not_cancelled=lambda: job_repo.ensure_not_cancelled(job_id),
            )
            if scan_target:
                context = result.setdefault("evidence", {}).setdefault("date_context", {})
                context.update(
                    {
                        "target_week": scan_target.get("week"),
                        "target_year": scan_target.get("year"),
                        "target_week_source": "BCNB_SERVER",
                        "target_class_code": scan_target.get("class_code"),
                        "target_lesson": scan_target.get("lesson"),
                    }
                )
                if not context.get("target_class_date"):
                    context["target_class_date"] = scan_target.get("class_date")
                result["scan_target"] = scan_target
            scan_results.append(attach_zalo_sync_result(result, sync_result))
            log_message(f"Kết quả nhóm {group_name}: {result['status']}")

        # ==========================================
        # BƯỚC 5: QUAY LẠI TRANG BCNB ĐỂ TICK TỰ ĐỘNG
        # ==========================================
        await job_repo.update_job(job_id, message="Đang cập nhật kết quả lên BCNB...", progress=80)
        log_message("Đang truy cập BCNB để cập nhật trạng thái tích chọn...")
        await job_repo.ensure_not_cancelled(job_id)
        await evidence_repo.register_scan_results(
            job_id=job_id,
            results=scan_results,
            evidence_root=evidence_root,
            retention_days=getattr(Config, "EVIDENCE_RETENTION_DAYS", 7),
        )
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
                await page.wait_for_load_state("networkidle")
        except Exception as e:
            log_message(f"Lỗi trong quá trình tự động đăng nhập BCNB: {e}")
        # -----------------------

        try:
            # update_bcnb_reports delegates every write to the audited
            # execute_verified_checkbox_action helper with server read-back.
            bcnb_summary = await update_bcnb_reports(
                page,
                job_id=job_id,
                execution_mode=execution_mode,
                scan_results=scan_results,
                today=today,
                now=scan_started_at,
                scan_plan=scan_plan,
                job_repo=job_repo,
                audit_repo=audit_repo,
            )
        except Exception as e:
            log_message(f"Lỗi khi cập nhật BCNB: {e}")
            raise RuntimeError("Không thể hoàn tất cập nhật BCNB an toàn.") from e

        # ------------------------------------------
    failed_targets = (bcnb_summary or {}).get("failed_targets", [])
    if failed_targets:
        final_status = "PARTIAL_SUCCESS"
        final_message = (
            ("Hoàn tất mô phỏng" if dry_run else "Hoàn tất một phần")
            + f"; {len(failed_targets)} dòng BCNB chưa cập nhật, cần kiểm tra audit."
        )
    else:
        final_status = "SUCCESS"
        final_message = "Hoàn tất mô phỏng, chưa thay đổi BCNB." if dry_run else "Hoàn tất!"
    await job_repo.update_job(
        job_id,
        status=final_status,
        message=final_message,
        progress=100,
        results=json.dumps(scan_results, ensure_ascii=False),
    )
    log_message("Tiến trình quét hoàn tất.")
