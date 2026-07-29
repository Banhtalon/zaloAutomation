from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from typing import Any, Protocol

import PIL.Image
from google import genai
from google.genai import types
from playwright.async_api import async_playwright

from googleform_autosubmit.config import Config
from googleform_autosubmit.browser_launcher import launch_chromium_with_fallback
from googleform_autosubmit.form_submitter import FormSubmitter
from googleform_autosubmit.ocr_processor import OCRProcessor
from services.lms_plan import LmsSafetyError, normalize_class_codes


class StudentOcrGateway(Protocol):
    async def process_file_async(self, image_path: str) -> list[dict]: ...


class LmsOcrGateway(Protocol):
    async def extract_missing_classes(self, image_path: str) -> list[str]: ...


class FormBatch(Protocol):
    async def submit_form(self, student_name: str, class_code: str) -> None: ...


class BrowserGateway(Protocol):
    def persistent_page(
        self,
        *,
        viewport: dict[str, int] | None = None,
    ) -> Any: ...

    def form_batch(
        self,
        mapping: dict,
        execution_mode: str,
        *,
        headless: bool,
    ) -> Any: ...


class _SharedBrowserFormBatch:
    def __init__(self, submitter: Any, browser: Any):
        self._submitter = submitter
        self._browser = browser

    async def submit_form(self, student_name: str, class_code: str) -> None:
        await self._submitter.submit_form(
            student_name,
            class_code,
            browser=self._browser,
        )


class PlaywrightBrowserGateway:
    """Owns Playwright/browser lifecycles while exposing raw pages to services."""

    def __init__(
        self,
        *,
        playwright_factory: Callable[[], Any] = async_playwright,
        form_submitter_factory: Callable[..., Any] = FormSubmitter,
    ):
        self._playwright_factory = playwright_factory
        self._form_submitter_factory = form_submitter_factory

    @asynccontextmanager
    async def persistent_page(
        self,
        *,
        viewport: dict[str, int] | None = None,
    ) -> AsyncIterator[Any]:
        async with self._playwright_factory() as playwright:
            launch_options: dict[str, Any] = {
                "user_data_dir": "./zalo_session",
                "headless": False,
                "channel": "chrome",
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if viewport is not None:
                launch_options["viewport"] = viewport
            context = await playwright.chromium.launch_persistent_context(
                **launch_options
            )
            try:
                page = await context.new_page()
                await page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{get: () => undefined})"
                )
                yield page
            finally:
                await context.close()

    @asynccontextmanager
    async def form_batch(
        self,
        mapping: dict,
        execution_mode: str,
        *,
        headless: bool,
    ) -> AsyncIterator[FormBatch]:
        async with self._playwright_factory() as playwright:
            browser = await launch_chromium_with_fallback(
                playwright.chromium,
                headless=headless,
            )
            try:
                submitter = self._form_submitter_factory(
                    mapping,
                    execution_mode=execution_mode,
                )
                yield _SharedBrowserFormBatch(submitter, browser)
            finally:
                await browser.close()


class GeminiLmsOcrGateway:
    """Lazy Gemini adapter for extracting missing LMS class codes."""

    def __init__(
        self,
        *,
        api_key_provider: Callable[[], str | None] = lambda: Config.GEMINI_API_KEY,
        model_provider: Callable[[], str] = lambda: Config.GEMINI_MODEL,
        client_factory: Callable[[str], Any] | None = None,
        image_opener: Callable[[str], Any] = PIL.Image.open,
    ):
        self._api_key_provider = api_key_provider
        self._model_provider = model_provider
        self._client_factory = client_factory
        self._image_opener = image_opener

    async def extract_missing_classes(self, image_path: str) -> list[str]:
        api_key = self._api_key_provider()
        if not api_key:
            raise LmsSafetyError("Chưa cấu hình GEMINI_API_KEY.")

        client = (
            self._client_factory(api_key)
            if self._client_factory
            else genai.Client(api_key=api_key)
        )
        image = await asyncio.to_thread(self._image_opener, image_path)
        try:
            prompt = """
            Đây là ảnh chụp danh sách các lớp học CHƯA nhận xét LMS.
            Hãy trích xuất TOÀN BỘ mã lớp có trong ảnh.
            Chỉ trả về một JSON array các chuỗi mã lớp viết hoa.
            Ví dụ: ["VT-C4K-SI24", "VT-JSB14"]
            """
            response = await client.aio.models.generate_content(
                model=self._model_provider(),
                contents=[prompt, image],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                ),
            )
            if not response.text:
                raise LmsSafetyError("Gemini không trả về nội dung OCR LMS.")
            try:
                raw = json.loads(response.text)
            except (json.JSONDecodeError, TypeError) as exc:
                raise LmsSafetyError("Gemini trả về JSON OCR LMS không hợp lệ.") from exc
            return normalize_class_codes(raw)
        finally:
            image.close()


@dataclass(slots=True)
class ExternalGateways:
    browser: BrowserGateway
    lms_ocr: LmsOcrGateway
    student_ocr_factory: Callable[[], StudentOcrGateway]

    @classmethod
    def production(cls) -> ExternalGateways:
        return cls(
            browser=PlaywrightBrowserGateway(),
            lms_ocr=GeminiLmsOcrGateway(),
            student_ocr_factory=OCRProcessor,
        )
