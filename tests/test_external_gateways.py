import asyncio
import unittest
from unittest.mock import AsyncMock

from services.external_gateways import (
    ExternalGateways,
    GeminiLmsOcrGateway,
    PlaywrightBrowserGateway,
)
from services.lms_plan import LmsSafetyError
from services.runtime import WorkerRuntime


class FakePage:
    def __init__(self):
        self.init_scripts = []

    async def add_init_script(self, script):
        self.init_scripts.append(script)


class FakePersistentContext:
    def __init__(self):
        self.page = FakePage()
        self.close_count = 0

    async def new_page(self):
        return self.page

    async def close(self):
        self.close_count += 1


class FakeBrowser:
    def __init__(self):
        self.close_count = 0

    async def close(self):
        self.close_count += 1


class FakeChromium:
    def __init__(self):
        self.context = FakePersistentContext()
        self.browser = FakeBrowser()
        self.persistent_kwargs = None
        self.launch_kwargs = None

    async def launch_persistent_context(self, **kwargs):
        self.persistent_kwargs = kwargs
        return self.context

    async def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()


class FakePlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self):
        self.enter_count += 1
        return self.playwright

    async def __aexit__(self, exc_type, exc, traceback):
        self.exit_count += 1


class FakeFormSubmitter:
    instances = []

    def __init__(self, mapping, execution_mode=None):
        self.mapping = mapping
        self.execution_mode = execution_mode
        self.calls = []
        self.__class__.instances.append(self)

    async def submit_form(self, student_name, class_code, browser=None):
        self.calls.append((student_name, class_code, browser))


class FakeImage:
    def __init__(self):
        self.close_count = 0

    def close(self):
        self.close_count += 1


class FakeGeminiModels:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"text": self.text})()


class FakeGeminiClient:
    def __init__(self, text):
        self.models = FakeGeminiModels(text)
        self.aio = type("Aio", (), {"models": self.models})()


class ExternalGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeFormSubmitter.instances = []
        self.playwright = FakePlaywright()
        self.manager = FakePlaywrightManager(self.playwright)
        self.browser_gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: self.manager,
            form_submitter_factory=FakeFormSubmitter,
        )

    async def test_persistent_page_closes_context_on_success_and_error(self):
        async with self.browser_gateway.persistent_page(
            viewport={"width": 800, "height": 600}
        ) as page:
            self.assertIs(page, self.playwright.chromium.context.page)

        self.assertEqual(self.playwright.chromium.context.close_count, 1)
        self.assertEqual(self.manager.exit_count, 1)
        self.assertEqual(
            self.playwright.chromium.persistent_kwargs["user_data_dir"],
            "./zalo_session",
        )
        self.assertEqual(
            self.playwright.chromium.persistent_kwargs["viewport"],
            {"width": 800, "height": 600},
        )
        self.assertTrue(page.init_scripts)

        other_playwright = FakePlaywright()
        other_manager = FakePlaywrightManager(other_playwright)
        gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: other_manager,
            form_submitter_factory=FakeFormSubmitter,
        )
        with self.assertRaisesRegex(RuntimeError, "boom"):
            async with gateway.persistent_page():
                raise RuntimeError("boom")
        self.assertEqual(other_playwright.chromium.context.close_count, 1)
        self.assertEqual(other_manager.exit_count, 1)

    async def test_persistent_page_closes_context_on_cancellation(self):
        entered = asyncio.Event()

        async def use_page():
            async with self.browser_gateway.persistent_page():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(use_page())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.playwright.chromium.context.close_count, 1)
        self.assertEqual(self.manager.exit_count, 1)

    async def test_persistent_page_cleanup_during_launch_and_page_setup_failures(self):
        launch_playwright = FakePlaywright()
        launch_playwright.chromium.launch_persistent_context = AsyncMock(
            side_effect=RuntimeError("launch failed")
        )
        launch_manager = FakePlaywrightManager(launch_playwright)
        launch_gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: launch_manager,
            form_submitter_factory=FakeFormSubmitter,
        )
        with self.assertRaisesRegex(RuntimeError, "launch failed"):
            async with launch_gateway.persistent_page():
                self.fail("gateway yielded after launch failure")
        self.assertEqual(launch_manager.exit_count, 1)

        page_playwright = FakePlaywright()
        page_playwright.chromium.context.new_page = AsyncMock(
            side_effect=RuntimeError("page failed")
        )
        page_manager = FakePlaywrightManager(page_playwright)
        page_gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: page_manager,
            form_submitter_factory=FakeFormSubmitter,
        )
        with self.assertRaisesRegex(RuntimeError, "page failed"):
            async with page_gateway.persistent_page():
                self.fail("gateway yielded after page failure")
        self.assertEqual(page_playwright.chromium.context.close_count, 1)
        self.assertEqual(page_manager.exit_count, 1)

        script_playwright = FakePlaywright()
        script_playwright.chromium.context.page.add_init_script = AsyncMock(
            side_effect=RuntimeError("script failed")
        )
        script_manager = FakePlaywrightManager(script_playwright)
        script_gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: script_manager,
            form_submitter_factory=FakeFormSubmitter,
        )
        with self.assertRaisesRegex(RuntimeError, "script failed"):
            async with script_gateway.persistent_page():
                self.fail("gateway yielded after init script failure")
        self.assertEqual(script_playwright.chromium.context.close_count, 1)
        self.assertEqual(script_manager.exit_count, 1)

    async def test_form_batch_reuses_browser_and_always_closes_it(self):
        async with self.browser_gateway.form_batch(
            {"Question": {"type": "text", "value": "Answer"}},
            "dry-run",
            headless=True,
        ) as batch:
            await batch.submit_form("Student", "VT-CODE")

        submitter = FakeFormSubmitter.instances[0]
        self.assertEqual(submitter.execution_mode, "dry-run")
        self.assertEqual(
            submitter.calls,
            [("Student", "VT-CODE", self.playwright.chromium.browser)],
        )
        self.assertEqual(self.playwright.chromium.browser.close_count, 1)
        self.assertEqual(self.playwright.chromium.launch_kwargs, {"headless": True})

    async def test_lms_ocr_is_lazy_normalizes_and_closes_image(self):
        image = FakeImage()
        client = FakeGeminiClient('[" vt-jsb14 ", "VT-JSB14", "VT-C4K"]')
        created_keys = []
        gateway = GeminiLmsOcrGateway(
            api_key_provider=lambda: "secret",
            model_provider=lambda: "fake-model",
            client_factory=lambda api_key: (
                created_keys.append(api_key) or client
            ),
            image_opener=lambda _path: image,
        )

        result = await gateway.extract_missing_classes("input.png")

        self.assertEqual(result, ["VT-JSB14", "VT-C4K"])
        self.assertEqual(created_keys, ["secret"])
        self.assertEqual(image.close_count, 1)
        self.assertEqual(client.models.calls[0]["model"], "fake-model")

    async def test_lms_ocr_fails_closed_without_key_or_content(self):
        no_key = GeminiLmsOcrGateway(api_key_provider=lambda: "")
        with self.assertRaises(LmsSafetyError):
            await no_key.extract_missing_classes("input.png")

        image = FakeImage()
        empty = GeminiLmsOcrGateway(
            api_key_provider=lambda: "secret",
            client_factory=lambda _key: FakeGeminiClient(None),
            image_opener=lambda _path: image,
        )
        with self.assertRaises(LmsSafetyError):
            await empty.extract_missing_classes("input.png")
        self.assertEqual(image.close_count, 1)

    def test_worker_runtime_preserves_injected_external_gateways(self):
        external = ExternalGateways(
            browser=self.browser_gateway,
            lms_ocr=object(),
            student_ocr_factory=lambda: object(),
        )
        runtime = WorkerRuntime(external=external)
        old_browser_lock = runtime.browser_lock

        runtime.reset_locks()

        self.assertIs(runtime.external, external)
        self.assertIsNot(runtime.browser_lock, old_browser_lock)


if __name__ == "__main__":
    unittest.main()
