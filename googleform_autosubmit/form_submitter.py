import asyncio
import re
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

    async def fill_field(self, page, question, rules):
        field_type = rules['type']
        value = rules['value']
        
        try:
            question_container = (
                page.locator("div[role='listitem']")
                .filter(has_text=question)
                .first
            )
            
            # --- TÍNH NĂNG CHUYỂN TRANG THÔNG MINH ---
            for _ in range(5):
                if await question_container.count() > 0 and await question_container.is_visible():
                    break
                
                next_btn = page.locator("div[role='button']").filter(has_text=re.compile(r"^\s*(Tiếp|Next)\s*$", re.IGNORECASE)).first
                if await next_btn.count() > 0 and await next_btn.is_visible():
                    logger.info("👉 Không thấy câu hỏi. Đang cuộn và bấm 'Tiếp' sang trang sau...")
                    await next_btn.scroll_into_view_if_needed()
                    await next_btn.click()
                    try:
                        await question_container.wait_for(state="visible", timeout=2000)
                    except Exception:
                        pass
                else:
                    break

            if await question_container.count() == 0:
                raise Exception("Không tìm thấy câu hỏi trên bất kỳ trang nào.")

            # --- TÍNH NĂNG TỰ ĐỘNG CUỘN TRANG (AUTO-SCROLL) ---
            await question_container.scroll_into_view_if_needed()

            # --- ĐIỀN DỮ LIỆU ---
            if field_type == "text":
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

            logger.info(f"Đã điền [{question}] -> {value}")

        except Exception as e:
            logger.error(f"❌ Lỗi khi điền [{question}]: {e}")
            raise RuntimeError(f"Không thể điền câu hỏi [{question}]") from e

    async def check_captcha(self, page):
        captcha_frames = await page.locator('iframe[src*="recaptcha"]').count()
        if captcha_frames > 0:
            raise CaptchaDetectedError("Đã phát hiện reCAPTCHA. Hệ thống tự động dừng.")

    async def _execute_submission(self, page, student_name, class_code):
        await page.goto(Config.FORM_URL, wait_until="domcontentloaded")
        await self.check_captcha(page)

        # Cập nhật tên câu hỏi chính xác theo ảnh số 1
        await self.fill_field(page, "Tên của em là?", {"type": "text", "value": student_name})
        await self.fill_field(page, "Mã lớp của em là?", {"type": "text", "value": class_code})

        # Điền các câu còn lại từ cấu hình mapping
        for question, rules in self.mapping.items():
            await self.fill_field(page, question, rules)

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
            logger.info(f"✅ Nộp thành công cho {student_name}")
        else:
            logger.info("Dry-Run: Đã điền form. Mô phỏng thành công.")

    async def submit_form(self, student_name, class_code, browser=None):
        if browser:
            context = await browser.new_context()
            page = await context.new_page()
            try:
                await self._execute_submission(page, student_name, class_code)
            except Exception as e:
                logger.error(f"❌ Thất bại cho {student_name}: {e}")
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
                    await self._execute_submission(page, student_name, class_code)
                except Exception as e:
                    logger.error(f"❌ Thất bại cho {student_name}: {e}")
                    raise
                finally:
                    await local_browser.close()
