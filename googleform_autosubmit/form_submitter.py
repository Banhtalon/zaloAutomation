import asyncio
import io
from pathlib import Path
import re
import time
import uuid
from PIL import Image
from playwright.async_api import async_playwright
from logger_utils import logger
from .browser_launcher import launch_chromium_with_fallback
from .config import Config


_RADIO_CHECK_TIMEOUT_MS = 2_000
_RADIO_CHECK_POLL_INTERVAL_MS = 100
_RADIO_MAX_CLICK_ATTEMPTS = 2

class CaptchaDetectedError(Exception):
    pass


class SubmissionOutcomeUnknownError(RuntimeError):
    """The submit click happened but the server confirmation was not observed."""

    pass

def _save_stitched_screenshots(screenshots: list[bytes], student_name: str = "") -> str | None:
    if not screenshots:
        return None
    try:
        screenshot_dir = Path(Config.RUNTIME_ROOT) / "screenshots" / "googleform"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        filename = f"sub_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
        output_path = screenshot_dir / filename

        images = [Image.open(io.BytesIO(b)) for b in screenshots if b]
        if not images:
            return None
        if len(images) == 1:
            images[0].save(output_path, format="PNG", optimize=True)
        else:
            total_width = max(img.width for img in images)
            total_height = sum(img.height for img in images)
            stitched = Image.new("RGB", (total_width, total_height), (255, 255, 255))
            current_y = 0
            for img in images:
                stitched.paste(img, (0, current_y))
                current_y += img.height
            stitched.save(output_path, format="PNG", optimize=True)

        return str(output_path)
    except Exception:
        logger.warning("Không thể tạo ảnh chụp màn hình ghép.")
        return None


class FormSubmitter:
    def __init__(self, mapping_dict, execution_mode=None):
        self.mapping = mapping_dict
        self.execution_mode = execution_mode or Config.MODE

    async def _wait_for_radio_checked(self, radio_locator):
        check_count = (
            _RADIO_CHECK_TIMEOUT_MS + _RADIO_CHECK_POLL_INTERVAL_MS - 1
        ) // _RADIO_CHECK_POLL_INTERVAL_MS + 1

        for check_index in range(check_count):
            if await radio_locator.get_attribute("aria-checked") == "true":
                return True
            if check_index < check_count - 1:
                await asyncio.sleep(_RADIO_CHECK_POLL_INTERVAL_MS / 1_000)

        return False

    async def fill_field(self, page, question, rules, class_code=None, captured_screenshots=None):
        field_type = rules['type']
        value = rules['value']
        
        try:
            question_container = (
                page.locator("div[role='listitem']")
                .filter(has_text=question)
                .first
            )

            # --- TÍNH NĂNG CHUYỂN TRANG THÔNG MINH ---
            question_aliases = {
                "Mã cơ sở của em là?": "Cơ sở trường MindX mà em đang học",
                "Cơ sở trường MindX mà em đang học": "Mã cơ sở của em là?",
            }
            alt_question = question_aliases.get(question)

            for _ in range(5):
                if await question_container.count() > 0 and await question_container.is_visible():
                    break
                if alt_question:
                    alt_container = page.locator("div[role='listitem']").filter(has_text=alt_question).first
                    if await alt_container.count() > 0 and await alt_container.is_visible():
                        question_container = alt_container
                        break
                
                next_btn = page.locator("div[role='button']").filter(has_text=re.compile(r"^\s*(Tiếp|Next)\s*$", re.IGNORECASE)).first
                if await next_btn.count() > 0 and await next_btn.is_visible():
                    if captured_screenshots is not None and hasattr(page, "screenshot"):
                        try:
                            img_bytes = await page.screenshot(full_page=False)
                            captured_screenshots.append(img_bytes)
                        except Exception:
                            pass
                    logger.info("👉 Không thấy câu hỏi. Đang cuộn và bấm 'Tiếp' sang trang sau...")
                    await next_btn.scroll_into_view_if_needed()
                    await next_btn.click()
                    try:
                        await question_container.wait_for(state="visible", timeout=2000)
                    except Exception:
                        pass
                else:
                    break

            if await question_container.count() == 0 and alt_question:
                alt_container = page.locator("div[role='listitem']").filter(has_text=alt_question).first
                if await alt_container.count() > 0:
                    question_container = alt_container

            if await question_container.count() == 0:
                raise Exception("Không tìm thấy câu hỏi trên bất kỳ trang nào.")

            # --- TÍNH NĂNG TỰ ĐỘNG CUỘN TRANG (AUTO-SCROLL) ---
            await question_container.scroll_into_view_if_needed()

            # --- ĐIỀN DỮ LIỆU ---
            is_listbox = False
            if hasattr(question_container, "locator"):
                try:
                    is_listbox = (await question_container.locator("div[role='listbox']").count()) > 0
                except Exception:
                    is_listbox = False

            if field_type in ("select", "dropdown", "listbox") or (is_listbox and field_type not in ("text",)):
                listbox = question_container.locator("div[role='listbox']").first
                if await listbox.count() == 0:
                    raise ValueError(f"Không tìm thấy hộp chọn (dropdown) cho câu hỏi [{question}].")

                target_value = value.strip()
                if target_value.lower() == "auto" and class_code:
                    target_value = class_code.split("-")[0].strip().upper()

                campus_code_map = {
                    "VT": "Vũng Tàu - 205A Lê Hồng Phong",
                    "HN": "HN - 71 Nguyễn Chí Thanh",
                    "HCM": "HCM - Phan Văn Trị",
                    "DN": "Đà Nẵng",
                    "HP": "Hải Phòng",
                    "CT": "Cần Thơ",
                    "BD": "Bình Dương",
                    "NA": "Nghệ An",
                    "BN": "Bắc Ninh",
                    "QN": "Quảng Ninh",
                    "PT": "Phú Thọ",
                    "VP": "Vĩnh Phúc",
                    "TN": "Thái Nguyên",
                    "TH": "Thanh Hóa",
                    "HY": "Hưng Yên",
                }
                if target_value.upper() in campus_code_map:
                    target_value = campus_code_map[target_value.upper()]

                option_locator = page.get_by_role("option", name=target_value, exact=True)

                # Mở dropdown với cơ chế retry: đợi option visible thay vì sleep cố định
                _DROPDOWN_MAX_RETRIES = 3
                _DROPDOWN_OPTION_TIMEOUT_MS = 5000
                for dropdown_attempt in range(_DROPDOWN_MAX_RETRIES):
                    await listbox.scroll_into_view_if_needed()
                    await listbox.click()
                    try:
                        await option_locator.first.wait_for(
                            state="visible", timeout=_DROPDOWN_OPTION_TIMEOUT_MS
                        )
                        break  # Dropdown đã mở, option visible
                    except Exception:
                        if dropdown_attempt < _DROPDOWN_MAX_RETRIES - 1:
                            logger.warning(
                                f"Dropdown chưa mở (lần {dropdown_attempt + 1}/"
                                f"{_DROPDOWN_MAX_RETRIES}), thử lại..."
                            )
                            # Bấm lại listbox để đóng dropdown bị kẹt, rồi đợi
                            try:
                                await listbox.click()
                            except Exception:
                                pass
                            await asyncio.sleep(0.5)
                        else:
                            # Fallback: thử tìm option không exact
                            option_locator = page.get_by_role(
                                "option", name=target_value
                            )
                            if await option_locator.count() == 0:
                                raise ValueError(
                                    f"Không tìm thấy lựa chọn [{value}] trong danh sách."
                                )

                target_option = option_locator.first
                await target_option.scroll_into_view_if_needed()
                await target_option.click()
                await asyncio.sleep(0.3)

            elif field_type == "text":
                input_locator = question_container.locator("input[type='text'], textarea").first
                await input_locator.fill(value)
                
            elif field_type == "radio":
                radio_locator = question_container.get_by_role(
                    "radio",
                    name=value,
                    exact=True,
                )
                radio_count = await radio_locator.count()
                if radio_count != 1:
                    raise ValueError(
                        f"Cần đúng 1 đáp án [{value}], tìm thấy {radio_count}."
                    )

                await radio_locator.scroll_into_view_if_needed()
                for click_attempt in range(_RADIO_MAX_CLICK_ATTEMPTS):
                    await radio_locator.click()
                    if await self._wait_for_radio_checked(radio_locator):
                        break
                    if click_attempt < _RADIO_MAX_CLICK_ATTEMPTS - 1:
                        logger.warning(
                            f"Đáp án [{value}] chưa cập nhật; đang thử bấm lại một lần."
                        )
                else:
                    raise RuntimeError(f"Đáp án [{value}] chưa được chọn sau khi bấm.")
            else:
                raise ValueError(f"Loại trường không được hỗ trợ: {field_type}")

            is_name_field = (
                "tên" in question.casefold() or "name" in question.casefold()
            )
            logged_val = "[REDACTED_STUDENT_NAME]" if is_name_field else value
            logger.info(f"Đã điền [{question}] -> {logged_val}")

        except Exception as e:
            logger.error(f"❌ Lỗi khi điền [{question}].")
            raise RuntimeError(f"Không thể điền câu hỏi [{question}]") from e

    async def check_captcha(self, page):
        captcha_frames = await page.locator('iframe[src*="recaptcha"]').count()
        if captcha_frames > 0:
            raise CaptchaDetectedError("Đã phát hiện reCAPTCHA. Hệ thống tự động dừng.")

    async def _execute_submission(self, page, student_name, class_code):
        await page.goto(Config.FORM_URL, wait_until="domcontentloaded")
        await self.check_captcha(page)

        captured_screenshots: list[bytes] = []

        # Cập nhật tên câu hỏi chính xác theo ảnh số 1
        await self.fill_field(
            page,
            "Tên của em là?",
            {"type": "text", "value": student_name},
            captured_screenshots=captured_screenshots,
        )
        await self.fill_field(
            page,
            "Mã lớp của em là?",
            {"type": "text", "value": class_code},
            captured_screenshots=captured_screenshots,
        )

        # Điền các câu còn lại từ cấu hình mapping
        for question, rules in self.mapping.items():
            await self.fill_field(
                page,
                question,
                rules,
                class_code=class_code,
                captured_screenshots=captured_screenshots,
            )

        # Chụp màn hình trang cuối cùng sau khi điền xong
        if hasattr(page, "screenshot"):
            try:
                img_bytes = await page.screenshot(full_page=False)
                captured_screenshots.append(img_bytes)
            except Exception:
                pass

        screenshot_path = _save_stitched_screenshots(captured_screenshots, student_name)

        if self.execution_mode == "production":
            submit_button = page.locator("div[role='button']").filter(has_text=re.compile(r"^\s*(Gửi|Submit|Gửi|Hoàn thành)\s*$", re.IGNORECASE)).first
            
            for _ in range(5):
                if await submit_button.count() > 0 and await submit_button.is_visible():
                    break
                
                next_btn = page.locator("div[role='button']").filter(has_text=re.compile(r"^\s*(Tiếp|Next)\s*$", re.IGNORECASE)).first
                if await next_btn.count() > 0 and await next_btn.is_visible():
                    logger.info("👉 Vẫn còn trang phụ, đang bấm 'Tiếp' để tìm nút Gửi...")
                    await next_btn.scroll_into_view_if_needed()
                    await next_btn.click()
                    try:
                        await submit_button.wait_for(state="visible", timeout=2000)
                    except Exception:
                        pass
                else:
                    break

            await submit_button.scroll_into_view_if_needed(timeout=10000)
            await submit_button.click(timeout=10000, trial=True)

            try:
                await submit_button.click(timeout=10000)
                confirmation = page.get_by_text(
                    re.compile(
                        r"(Câu trả lời của bạn đã được ghi lại|Your response has been recorded)",
                        re.IGNORECASE,
                    )
                ).first
                await confirmation.wait_for(state="visible", timeout=10000)
            except Exception as exc:
                raise SubmissionOutcomeUnknownError(
                    "Không tìm thấy xác nhận Google Form đã ghi nhận câu trả lời."
                ) from exc
            logger.info(f"✅ Nộp thành công form lớp {class_code}")
        else:
            logger.info("Dry-Run: Đã điền form. Mô phỏng thành công.")

        return screenshot_path

    async def submit_form(self, student_name, class_code, browser=None):
        if browser:
            context = await browser.new_context()
            page = await context.new_page()
            try:
                return await self._execute_submission(page, student_name, class_code)
            except Exception as e:
                logger.error(f"❌ Thất bại khi nộp form lớp {class_code}.")
                raise e
            finally:
                await context.close()
        else:
            async with async_playwright() as p:
                local_browser = await launch_chromium_with_fallback(
                    p.chromium,
                    headless=Config.HEADLESS,
                )
                context = await local_browser.new_context()
                page = await context.new_page()
                try:
                    return await self._execute_submission(page, student_name, class_code)
                except Exception:
                    logger.error(f"❌ Thất bại khi nộp form lớp {class_code}.")
                    raise
                finally:
                    await local_browser.close()
