import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import services.external_gateways as external_gateways
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


class LifecycleContext:
    def __init__(self, *, close_error=None, initially_closed=False):
        self.closed = initially_closed
        self.close_error = close_error
        self.close_count = 0
        self.pages = []

    async def new_page(self):
        page = LifecyclePage()
        self.pages.append(page)
        return page

    async def close(self):
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class LifecyclePage:
    def __init__(self, *, close_error=None, initially_closed=False):
        self.closed = initially_closed
        self.close_error = close_error
        self.close_count = 0

    async def close(self):
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error
        self.closed = True

    async def add_init_script(self, _script):
        return None


class LifecycleStop:
    def __init__(self, *, error=None, initially_stopped=False):
        self.error = error
        self.stopped = initially_stopped
        self.stop_count = 0

    async def stop(self):
        self.stop_count += 1
        if self.error is not None:
            raise self.error
        self.stopped = True


class IndependentPersistentPage(FakePage):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.reload_count = 0
        self.close_count = 0

    async def reload(self):
        self.reload_count += 1

    async def close(self):
        self.close_count += 1


class IndependentPersistentContext:
    def __init__(self):
        self.pages = []
        self.close_count = 0

    async def new_page(self):
        page = IndependentPersistentPage(f"page-{len(self.pages) + 1}")
        self.pages.append(page)
        return page

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
        self.assertEqual(
            other_playwright.chromium.persistent_kwargs["viewport"],
            {"width": 1920, "height": 1200},
        )
        self.assertIn(
            "--window-size=1920,1200",
            other_playwright.chromium.persistent_kwargs["args"],
        )

    async def test_isolated_quality_denies_persistent_entry_before_playwright(self):
        gateway = PlaywrightBrowserGateway()

        class Bomb:
            def __call__(self):
                raise AssertionError("playwright factory was entered")

        # Keep the production-factory identity while swapping in a bomb after
        # construction; a missing guard would then fail without launching a
        # real browser during this regression.
        gateway._playwright_factory = Bomb()
        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "1"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "quality isolation"):
                async with gateway.persistent_session():
                    self.fail("isolated production browser entry must be denied")

        self.assertFalse(gateway._active_session)

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

    async def test_persistent_session_keeps_two_pages_independent(self):
        playwright = FakePlaywright()
        context = IndependentPersistentContext()
        playwright.chromium.context = context
        manager = FakePlaywrightManager(playwright)
        gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: manager,
            form_submitter_factory=FakeFormSubmitter,
        )

        async with gateway.persistent_session() as session:
            page_a = await session.new_page()
            page_b = await session.new_page()

            self.assertIsNot(page_a, page_b)
            self.assertEqual([page_a.name, page_b.name], ["page-1", "page-2"])

            await page_a.reload()
            self.assertEqual(page_a.reload_count, 1)
            self.assertEqual(page_b.reload_count, 0)

            await session.close_page(page_a)
            self.assertEqual(page_a.close_count, 1)
            self.assertEqual(page_b.close_count, 0)
            self.assertEqual(context.close_count, 0)

            await page_b.reload()
            self.assertEqual(page_b.reload_count, 1)

        self.assertEqual(context.close_count, 1)
        self.assertEqual(manager.exit_count, 1)

    async def test_persistent_session_does_not_use_retired_structural_rendezvous(self):
        rendezvous = AsyncMock()
        self.browser_gateway._service_structural_rendezvous = rendezvous

        async with self.browser_gateway.persistent_session():
            pass

        rendezvous.assert_not_awaited()

    async def test_cleanup_enforces_one_total_deadline_across_owned_resources(self):
        never = asyncio.Event()

        class SlowPage:
            def __init__(self):
                self.close_count = 0

            async def add_init_script(self, _script):
                return None

            async def close(self):
                self.close_count += 1
                await never.wait()

        class SlowContext:
            def __init__(self):
                self.pages = []
                self.close_count = 0

            async def new_page(self):
                page = SlowPage()
                self.pages.append(page)
                return page

            async def close(self):
                self.close_count += 1
                await never.wait()

        context = SlowContext()
        session = external_gateways.PersistentBrowserSession(context)
        page = await session.new_page()

        async def stop_playwright():
            await never.wait()

        started = asyncio.get_running_loop().time()
        result = await session.cleanup(
            playwright_stop=stop_playwright,
            timeout_seconds=0.05,
            total_timeout_seconds=0.05,
            holder_count=0,
        )
        elapsed = asyncio.get_running_loop().time() - started

        self.assertLess(elapsed, 0.11)
        self.assertEqual(page.close_count, 1)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(result.code, external_gateways.CleanupCode.CLEANUP_INCOMPLETE)
        self.assertTrue(result.cleanup_incomplete)
        self.assertFalse(result.invariant_ok)

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

    async def test_cleanup_reports_success_and_is_idempotent_per_resource(self):
        context = LifecycleContext()
        stopper = LifecycleStop()
        session = external_gateways.PersistentBrowserSession(context)
        page = await session.new_page()

        result = await external_gateways.cleanup_persistent_resources(
            session=session,
            playwright_stop=stopper.stop,
            timeout_seconds=0.05,
            holder_count=lambda: 0,
        )
        repeated = await external_gateways.cleanup_persistent_resources(
            session=session,
            playwright_stop=stopper.stop,
            timeout_seconds=0.05,
            holder_count=lambda: 0,
        )

        self.assertEqual(result.code, external_gateways.CleanupCode.CLEANUP_OK)
        self.assertEqual(repeated.code, external_gateways.CleanupCode.CLEANUP_OK)
        self.assertEqual(page.close_count, 1)
        self.assertEqual(context.close_count, 1)
        self.assertEqual(stopper.stop_count, 1)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await session.new_page()

    async def test_cleanup_with_missing_context_fails_closed_and_stops_owner(self):
        stopper = LifecycleStop()

        result = await external_gateways.cleanup_persistent_resources(
            context=None,
            playwright_stop=stopper.stop,
            timeout_seconds=0.05,
            holder_count=0,
        )

        self.assertEqual(
            result.code,
            external_gateways.CleanupCode.CONTEXT_CLOSE_ERROR,
        )
        self.assertFalse(result.invariant_ok)
        self.assertEqual(result.remaining_holders, 0)
        self.assertEqual(stopper.stop_count, 1)

    async def test_raw_context_cleanup_reuses_one_owner_across_public_calls(self):
        context = LifecycleContext()
        stopper = LifecycleStop()

        first = await external_gateways.cleanup_persistent_resources(
            context=context,
            playwright_stop=stopper.stop,
            timeout_seconds=0.05,
            holder_count=0,
        )
        second = await external_gateways.cleanup_persistent_resources(
            context=context,
            playwright_stop=stopper.stop,
            timeout_seconds=0.05,
            holder_count=0,
        )

        self.assertEqual(second, first)
        self.assertEqual(context.close_count, 1)
        self.assertEqual(stopper.stop_count, 1)

    async def test_cleanup_normalizes_only_typed_already_closed_states(self):
        context = LifecycleContext(
            close_error=external_gateways.ContextAlreadyClosedError(),
            initially_closed=True,
        )
        stopper = LifecycleStop(
            error=external_gateways.PlaywrightAlreadyStoppedError(),
            initially_stopped=True,
        )
        session = external_gateways.PersistentBrowserSession(context)

        result = await external_gateways.cleanup_persistent_resources(
            session=session,
            playwright_stop=stopper.stop,
            timeout_seconds=0.05,
            holder_count=lambda: 0,
        )

        self.assertEqual(
            result.code,
            external_gateways.CleanupCode.CLEANUP_ALREADY_CLOSED,
        )
        self.assertEqual(context.close_count, 1)
        self.assertEqual(stopper.stop_count, 1)

    async def test_cleanup_preserves_unknown_context_error_even_when_holders_are_zero(self):
        context = LifecycleContext(close_error=RuntimeError("synthetic secret"))
        stopper = LifecycleStop()
        session = external_gateways.PersistentBrowserSession(context)

        result = await external_gateways.cleanup_persistent_resources(
            session=session,
            playwright_stop=stopper.stop,
            timeout_seconds=0.05,
            holder_count=lambda: 0,
        )

        self.assertEqual(result.code, external_gateways.CleanupCode.CONTEXT_CLOSE_ERROR)
        self.assertEqual(result.remaining_holders, 0)
        self.assertNotIn("synthetic secret", repr(result))
        self.assertEqual(stopper.stop_count, 1)

    async def test_cleanup_distinguishes_context_and_playwright_timeouts(self):
        async def hanging_context_close():
            await asyncio.Event().wait()

        class HangingContext(LifecycleContext):
            async def close(self):
                self.close_count += 1
                await hanging_context_close()

        async def hanging_stop():
            await asyncio.Event().wait()

        context_result = await external_gateways.cleanup_persistent_resources(
            session=external_gateways.PersistentBrowserSession(HangingContext()),
            playwright_stop=LifecycleStop().stop,
            timeout_seconds=0.01,
            holder_count=lambda: 0,
        )
        stop_result = await external_gateways.cleanup_persistent_resources(
            session=external_gateways.PersistentBrowserSession(LifecycleContext()),
            playwright_stop=hanging_stop,
            timeout_seconds=0.01,
            holder_count=lambda: 0,
        )

        self.assertEqual(
            context_result.code,
            external_gateways.CleanupCode.CONTEXT_CLOSE_TIMEOUT,
        )
        self.assertEqual(
            stop_result.code,
            external_gateways.CleanupCode.PLAYWRIGHT_STOP_TIMEOUT,
        )

    async def test_cleanup_keeps_page_failures_fail_closed_and_stop_errors_typed(self):
        page = LifecyclePage(close_error=RuntimeError("page secret"))
        context = LifecycleContext()
        context.new_page = AsyncMock(return_value=page)
        session = external_gateways.PersistentBrowserSession(context)
        await session.new_page()
        stopper = LifecycleStop(error=RuntimeError("stop secret"))
        telemetry = []

        result = await external_gateways.cleanup_persistent_resources(
            session=session,
            playwright_stop=stopper.stop,
            timeout_seconds=0.01,
            holder_count=lambda: 0,
            telemetry=lambda **fields: telemetry.append(fields),
        )

        self.assertEqual(result.code, external_gateways.CleanupCode.PLAYWRIGHT_STOP_ERROR)
        self.assertFalse(result.invariant_ok)
        self.assertEqual(result.remaining_pages, 1)
        self.assertTrue(telemetry)
        for fields in telemetry:
            self.assertEqual(
                set(fields),
                {"event", "phase", "elapsed_ms", "error_code", "state"},
            )
            self.assertNotIn("secret", repr(fields))

    async def test_cleanup_rejects_typed_already_state_without_invariant_agreement(self):
        context = LifecycleContext(
            close_error=external_gateways.ContextAlreadyClosedError(),
            initially_closed=False,
        )
        stopper = LifecycleStop(initially_stopped=False)
        session = external_gateways.PersistentBrowserSession(context)

        result = await external_gateways.cleanup_persistent_resources(
            session=session,
            playwright_stop=stopper.stop,
            timeout_seconds=0.01,
            holder_count=1,
        )

        self.assertEqual(
            result.code,
            external_gateways.CleanupCode.CLEANUP_INVARIANT_FAILED,
        )
        self.assertFalse(result.invariant_ok)


class RuntimeLandingLocator:
    def __init__(self, page, *, visible, hang=False, error=None):
        self.page = page
        self.visible = visible
        self.hang = hang
        self.error = error
        self.count_calls = 0
        self.visible_calls = 0

    async def count(self):
        self.count_calls += 1
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return 1 if self.visible else 0

    async def is_visible(self):
        self.visible_calls += 1
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return self.visible


class RuntimeLandingPage:
    def __init__(
        self,
        *,
        search_visible=False,
        url="https://chat.zalo.me/",
        closed=False,
        locator_hang=False,
        locator_error=None,
    ):
        self.url = url
        self.closed = closed
        self.search_visible = search_visible
        self.locator_hang = locator_hang
        self.locator_error = locator_error
        self.locator_calls = []
        self.new_page_calls = 0

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        self.locator_calls.append(selector)
        if selector != "#contact-search-input":
            raise AssertionError(f"unproven selector used: {selector}")
        return RuntimeLandingLocator(
            self,
            visible=self.search_visible,
            hang=self.locator_hang,
            error=self.locator_error,
        )


class RuntimeLandingFactsProviderTests(unittest.IsolatedAsyncioTestCase):
    def _provider(self, **kwargs):
        provider_type = getattr(
            external_gateways,
            "RuntimeLandingFactsProvider",
            None,
        )
        self.assertIsNotNone(
            provider_type,
            "production break: PersistentBrowserSession lacks the runtime auth-readiness fact provider",
        )
        if provider_type is None:
            return None
        return provider_type(timeout_seconds=0.02, **kwargs)

    async def _facts(self, page, **kwargs):
        provider = self._provider(**kwargs)
        if provider is None:
            return None
        return await provider(page)

    async def test_shell_and_search_visible_provider_emits_authenticated_ready_facts(self):
        facts = await self._facts(
            RuntimeLandingPage(search_visible=True),
            authenticated_shell_probe=lambda _page: True,
            login_or_qr_probe=lambda _page: False,
        )
        if facts is None:
            return
        self.assertTrue(facts["authenticated_shell_visible"])
        self.assertTrue(facts["search_control_visible"])
        self.assertFalse(facts["login_or_qr_visible"])
        self.assertEqual(facts["diagnostic_code"], "AUTHENTICATED_READY")

    async def test_shell_visible_search_absent_provider_does_not_claim_ready(self):
        facts = await self._facts(
            RuntimeLandingPage(search_visible=False),
            authenticated_shell_probe=lambda _page: True,
            login_or_qr_probe=lambda _page: False,
        )
        if facts is None:
            return
        self.assertFalse(facts["search_control_visible"])
        self.assertEqual(facts["diagnostic_code"], "LANDING_UNKNOWN_FAIL_CLOSED")

    async def test_search_visible_without_auth_shell_fails_closed_with_auth_contract_code(self):
        facts = await self._facts(
            RuntimeLandingPage(search_visible=True),
            login_or_qr_probe=lambda _page: False,
        )
        if facts is None:
            return
        self.assertTrue(facts["search_control_visible"])
        self.assertIsNone(facts["authenticated_shell_visible"])
        self.assertEqual(
            facts["diagnostic_code"],
            "LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN",
        )

    async def test_proven_login_or_qr_probe_is_typed(self):
        facts = await self._facts(
            RuntimeLandingPage(),
            authenticated_shell_probe=lambda _page: False,
            login_or_qr_probe=lambda _page: True,
        )
        if facts is None:
            return
        self.assertTrue(facts["login_or_qr_visible"])
        self.assertEqual(facts["diagnostic_code"], "LOGIN_OR_QR")

    async def test_unproven_login_or_qr_contract_stays_unknown(self):
        facts = await self._facts(
            RuntimeLandingPage(search_visible=True),
            authenticated_shell_probe=lambda _page: True,
        )
        if facts is None:
            return
        self.assertIsNone(facts["login_or_qr_visible"])
        self.assertEqual(
            facts["diagnostic_code"],
            "LIVE_LOGIN_SELECTOR_CONTRACT_NOT_PROVEN",
        )

    async def test_provider_maps_blocking_modal_without_selector_guessing(self):
        facts = await self._facts(
            RuntimeLandingPage(),
            authenticated_shell_probe=lambda _page: False,
            login_or_qr_probe=lambda _page: False,
            blocking_modal_probe=lambda _page: True,
        )
        if facts is None:
            return
        self.assertTrue(facts["blocking_modal_visible"])
        self.assertEqual(facts["diagnostic_code"], "LANDING_BLOCKING_MODAL")

    async def test_provider_maps_unsupported_browser_without_raw_diagnostics(self):
        facts = await self._facts(
            RuntimeLandingPage(),
            authenticated_shell_probe=lambda _page: False,
            login_or_qr_probe=lambda _page: False,
            unsupported_browser_probe=lambda _page: True,
        )
        if facts is None:
            return
        self.assertTrue(facts["unsupported_browser_visible"])
        self.assertEqual(facts["diagnostic_code"], "LANDING_UNSUPPORTED_BROWSER")

    async def test_provider_maps_network_error_without_recording_url(self):
        facts = await self._facts(
            RuntimeLandingPage(
                url="https://chat.zalo.me/?secret-query=do-not-log",
            ),
            authenticated_shell_probe=lambda _page: False,
            login_or_qr_probe=lambda _page: False,
            network_error_probe=lambda _page: True,
        )
        if facts is None:
            return
        self.assertTrue(facts["network_error_visible"])
        self.assertEqual(facts["diagnostic_code"], "LANDING_NETWORK_FAILED")
        self.assertNotIn("secret-query", repr(facts))

    async def test_provider_maps_closed_page_before_observation(self):
        facts = await self._facts(
            RuntimeLandingPage(closed=True),
            authenticated_shell_probe=lambda _page: True,
            login_or_qr_probe=lambda _page: False,
        )
        if facts is None:
            return
        self.assertEqual(facts["page_state"], "CLOSED")
        self.assertEqual(facts["diagnostic_code"], "LANDING_PAGE_CLOSED")

    async def test_locator_detach_is_unknown_and_does_not_leak_exception(self):
        secret = "detached selector secret"
        facts = await self._facts(
            RuntimeLandingPage(locator_error=RuntimeError(secret)),
            authenticated_shell_probe=lambda _page: True,
            login_or_qr_probe=lambda _page: False,
        )
        if facts is None:
            return
        self.assertEqual(facts["diagnostic_code"], "LANDING_UNKNOWN_FAIL_CLOSED")
        self.assertNotIn(secret, repr(facts))

    async def test_individual_observation_hang_is_bounded(self):
        facts = await self._facts(
            RuntimeLandingPage(search_visible=True, locator_hang=True),
            authenticated_shell_probe=lambda _page: True,
            login_or_qr_probe=lambda _page: False,
        )
        if facts is None:
            return
        self.assertEqual(facts["diagnostic_code"], "LANDING_READINESS_TIMEOUT")
        self.assertLessEqual(facts["elapsed_ms"], 10_000)

    async def test_unknown_provider_exception_is_sanitized(self):
        secret = "provider exception secret"

        def raising_probe(_page):
            raise RuntimeError(secret)

        facts = await self._facts(
            RuntimeLandingPage(),
            authenticated_shell_probe=raising_probe,
            login_or_qr_probe=lambda _page: False,
        )
        if facts is None:
            return
        self.assertEqual(facts["diagnostic_code"], "LANDING_UNKNOWN_FAIL_CLOSED")
        self.assertNotIn(secret, repr(facts))

    async def test_provider_emits_exact_sanitized_fact_key_set(self):
        facts = await self._facts(
            RuntimeLandingPage(search_visible=True),
            authenticated_shell_probe=lambda _page: True,
            login_or_qr_probe=lambda _page: False,
        )
        if facts is None:
            return
        self.assertEqual(
            set(facts),
            {
                "phase",
                "elapsed_ms",
                "page_state",
                "expected_host_reached",
                "authenticated_shell_visible",
                "search_control_visible",
                "login_or_qr_visible",
                "blocking_modal_visible",
                "unsupported_browser_visible",
                "network_error_visible",
                "selector_contract_version",
                "diagnostic_code",
            },
        )

    async def test_provider_does_not_recreate_page_or_profile(self):
        page = RuntimeLandingPage(search_visible=True)
        facts = await self._facts(
            page,
            authenticated_shell_probe=lambda _page: True,
            login_or_qr_probe=lambda _page: False,
        )
        if facts is None:
            return
        self.assertEqual(page.new_page_calls, 0)
        self.assertEqual(page.locator_calls, ["#contact-search-input"])

    async def test_persistent_session_supplies_provider_for_existing_page(self):
        page = RuntimeLandingPage(search_visible=True)
        context = LifecycleContext()
        session = external_gateways.PersistentBrowserSession(context)

        provider = getattr(session, "landing_facts_provider", None)
        self.assertTrue(
            callable(provider),
            "production break: PersistentBrowserSession must supply the runtime provider",
        )
        if not callable(provider):
            return
        facts = await provider(page)

        self.assertEqual(facts["phase"], "LANDING_AUTH_READINESS")
        self.assertEqual(facts["diagnostic_code"], "LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN")
        self.assertEqual(context.pages, [])


class InternalLandingProbeLocator:
    def __init__(self, *, visible=False, hang=False, error=None):
        self.visible = visible
        self.hang = hang
        self.error = error
        self.count_calls = 0
        self.visible_calls = 0

    async def count(self):
        self.count_calls += 1
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return 1 if self.visible else 0

    async def is_visible(self):
        self.visible_calls += 1
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return self.visible


class InternalLandingProbePage:
    def __init__(
        self,
        *,
        url="https://chat.zalo.me/",
        search_visible=False,
        closed=False,
        locator_hang=False,
        locator_error=None,
        closed_error=None,
        closed_hang=False,
        goto_error=None,
        goto_hang=False,
    ):
        self.url = url
        self.closed = closed
        self.search_visible = search_visible
        self.locator_hang = locator_hang
        self.locator_error = locator_error
        self.closed_error = closed_error
        self.closed_hang = closed_hang
        self.goto_error = goto_error
        self.goto_hang = goto_hang
        self.init_scripts = []
        self.goto_calls = []
        self.locator_calls = []
        self.close_count = 0
        self.closed_check_started = asyncio.Event()

    async def goto(self, url, *, timeout):
        self.goto_calls.append((url, timeout))
        if self.goto_hang:
            await asyncio.Event().wait()
        if self.goto_error is not None:
            raise self.goto_error
        self.url = url

    async def is_closed(self):
        self.closed_check_started.set()
        if self.closed_hang:
            await asyncio.Event().wait()
        if self.closed_error is not None:
            raise self.closed_error
        return self.closed

    def locator(self, selector):
        self.locator_calls.append(selector)
        if selector != "#contact-search-input":
            raise AssertionError(f"unproven selector used: {selector}")
        return InternalLandingProbeLocator(
            visible=self.search_visible,
            hang=self.locator_hang,
            error=self.locator_error,
        )

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    async def close(self):
        self.close_count += 1
        self.closed = True


class InternalLandingProbeContext:
    def __init__(self, page):
        self.page = page
        self.pages = []
        self.close_count = 0

    async def new_page(self):
        self.pages.append(self.page)
        return self.page

    async def close(self):
        self.close_count += 1


class InternalLandingProbeChromium:
    def __init__(self, context):
        self.context = context
        self.persistent_kwargs = None

    async def launch_persistent_context(self, **kwargs):
        self.persistent_kwargs = kwargs
        return self.context


class InternalLandingProbePlaywright:
    def __init__(self, context):
        self.chromium = InternalLandingProbeChromium(context)


class InternalLandingProbeManager:
    def __init__(self, playwright):
        self.playwright = playwright
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self):
        self.enter_count += 1
        return self.playwright

    async def __aexit__(self, exc_type, exc, traceback):
        self.exit_count += 1


class ProbeLifecycleContext:
    """Context fake with page-set and ownership instrumentation."""

    def __init__(
        self,
        *,
        pages=None,
        unavailable=False,
        create_page=None,
        create_error=None,
        create_hang=False,
        extra_pages=None,
    ):
        self._pages = list(pages or [])
        self.unavailable = unavailable
        self.create_page = create_page or InternalLandingProbePage(
            search_visible=True
        )
        self.create_error = create_error
        self.create_hang = create_hang
        self.extra_pages = list(extra_pages or [])
        self.new_page_calls = 0
        self.close_count = 0

    @property
    def pages(self):
        if self.unavailable:
            raise RuntimeError("page-set unavailable secret")
        return self._pages

    async def new_page(self):
        self.new_page_calls += 1
        if self.create_hang:
            await asyncio.Event().wait()
        if self.create_error is not None:
            raise self.create_error
        self._pages.append(self.create_page)
        self._pages.extend(self.extra_pages)
        return self.create_page

    async def close(self):
        self.close_count += 1


class ProbeLifecycleChromium:
    def __init__(self, context, *, launch_error=None):
        self.context = context
        self.launch_error = launch_error
        self.launch_calls = 0
        self.persistent_kwargs = None

    async def launch_persistent_context(self, **kwargs):
        self.launch_calls += 1
        self.persistent_kwargs = dict(kwargs)
        if self.launch_error is not None:
            raise self.launch_error
        return self.context


class ProbeLifecyclePlaywright:
    def __init__(self, context, *, launch_error=None):
        self.chromium = ProbeLifecycleChromium(
            context,
            launch_error=launch_error,
        )


class ProbeLifecycleGatewayTests(unittest.IsolatedAsyncioTestCase):
    def _gateway(self, context, *, launch_error=None, **kwargs):
        playwright = ProbeLifecyclePlaywright(
            context,
            launch_error=launch_error,
        )
        manager = InternalLandingProbeManager(playwright)
        gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: manager,
            max_retries=2,
            backoff_seconds=0,
            **kwargs,
        )
        return gateway, manager, playwright.chromium

    async def test_stop_states_abort_before_page_creation_and_preserve_zero_close_boundary(self):
        existing = PageSetFacade("preexisting-secret")
        cases = [
            (ProbeLifecycleContext(unavailable=True), "PAGESET_UNAVAILABLE"),
            (ProbeLifecycleContext(pages=[existing]), "PAGESET_PREEXISTING_ONE"),
            (
                ProbeLifecycleContext(
                    pages=[existing, PageSetFacade("second-secret")]
                ),
                "PAGESET_PREEXISTING_MANY",
            ),
        ]

        for context, diagnostic in cases:
            gateway, manager, chromium = self._gateway(context)
            result = await gateway.probe_landing_selector_proposals(
                timeout_seconds=0.05
            )

            self.assertEqual(result["diagnostic_code"], diagnostic)
            self.assertEqual(result["launch_count"], 1)
            self.assertEqual(chromium.launch_calls, 1)
            self.assertEqual(context.new_page_calls, 0)
            self.assertEqual(context.close_count, 0)
            self.assertEqual(manager.exit_count, 0)
            self.assertIsNotNone(gateway.last_cleanup_result)
            self.assertEqual(
                gateway.last_cleanup_result.code.value,
                "CLEANUP_SKIPPED_NON_OWNING_ABORT",
            )
            self.assertEqual(
                result["zero_action_counters"],
                {
                    "navigation": 0,
                    "search": 0,
                    "click": 0,
                    "type": 0,
                    "chat": 0,
                    "bcnb": 0,
                    "evidence": 0,
                },
            )

    async def test_zero_to_one_transition_is_observer_only_and_has_explicit_page_cleanup(self):
        page = InternalLandingProbePage(search_visible=True)
        context = ProbeLifecycleContext(create_page=page)
        gateway, manager, chromium = self._gateway(context)

        result = await gateway.probe_landing_selector_proposals(
            timeout_seconds=0.5
        )

        self.assertEqual(result["diagnostic_code"], "LANDING_PROBE_NO_PROVEN_CANDIDATE")
        self.assertEqual(result["launch_count"], 1)
        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(
            chromium.persistent_kwargs,
            {
                "user_data_dir": gateway.profile_path,
                "headless": False,
                "channel": "chrome",
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1920,1200",
                ],
                "viewport": {"width": 1920, "height": 1200},
            },
        )
        self.assertEqual(context.new_page_calls, 1)
        self.assertEqual(page.init_scripts, [])
        self.assertEqual(page.goto_calls, [("https://chat.zalo.me/", 5000)])
        self.assertEqual(page.close_count, 1)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)
        self.assertEqual(result["zero_action_counters"]["navigation"], 1)

    async def test_zero_page_creation_failure_aborts_without_navigation_or_cleanup(self):
        page = InternalLandingProbePage(search_visible=True)
        context = ProbeLifecycleContext(
            create_page=page,
            create_error=RuntimeError("create secret"),
        )
        gateway, manager, chromium = self._gateway(context)

        result = await gateway.probe_landing_selector_proposals(
            timeout_seconds=0.05
        )

        self.assertEqual(result["diagnostic_code"], "LANDING_PAGE_CREATE_FAILED")
        self.assertEqual(result["launch_count"], 1)
        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(context.new_page_calls, 1)
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.init_scripts, [])
        self.assertEqual(page.close_count, 0)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)
        self.assertEqual(result["zero_action_counters"]["navigation"], 0)

    async def test_post_create_mismatch_aborts_before_navigation_without_cleanup(self):
        page = InternalLandingProbePage(search_visible=True)
        context = ProbeLifecycleContext(
            create_page=page,
            extra_pages=[PageSetFacade("unexpected-second-page")],
        )
        gateway, manager, chromium = self._gateway(context)

        result = await gateway.probe_landing_selector_proposals(
            timeout_seconds=0.05
        )

        self.assertEqual(result["diagnostic_code"], "PAGESET_POSTCREATE_MISMATCH")
        self.assertEqual(result["launch_count"], 1)
        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(context.new_page_calls, 1)
        self.assertEqual(page.init_scripts, [])
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.close_count, 0)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)

    async def test_owned_page_state_error_closes_observer_without_owner_cleanup(self):
        page = InternalLandingProbePage(
            search_visible=True,
            closed_error=RuntimeError("closed marker secret"),
        )
        context = ProbeLifecycleContext(create_page=page)
        gateway, manager, chromium = self._gateway(context)

        result = await gateway.probe_landing_selector_proposals(
            timeout_seconds=0.05
        )

        self.assertEqual(result["diagnostic_code"], "LANDING_UNKNOWN_FAIL_CLOSED")
        self.assertEqual(result["launch_count"], 1)
        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(page.init_scripts, [])
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.locator_calls, [])
        self.assertEqual(page.close_count, 1)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)
        self.assertIsNone(gateway.last_cleanup_result)
        self.assertEqual(
            result["zero_action_counters"],
            {
                "navigation": 0,
                "search": 0,
                "click": 0,
                "type": 0,
                "chat": 0,
                "bcnb": 0,
                "evidence": 0,
            },
        )

    async def test_owned_page_state_cancellation_closes_observer_without_owner_cleanup(self):
        page = InternalLandingProbePage(
            search_visible=True,
            closed_hang=True,
        )
        context = ProbeLifecycleContext(create_page=page)
        gateway, manager, chromium = self._gateway(context)

        task = asyncio.create_task(
            gateway.probe_landing_selector_proposals(timeout_seconds=1.0)
        )
        await page.closed_check_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(context.new_page_calls, 1)
        self.assertEqual(page.close_count, 1)
        self.assertEqual(page.init_scripts, [])
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.locator_calls, [])
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)
        cleanup = gateway.last_cleanup_result
        self.assertIsNotNone(cleanup)
        if cleanup is None:
            return
        self.assertIsInstance(cleanup, external_gateways.CleanupResult)
        self.assertEqual(cleanup.code, external_gateways.CleanupCode.CLEANUP_OK)
        self.assertEqual(cleanup.page_state, external_gateways.CleanupState.CLOSED)
        self.assertEqual(cleanup.context_state, external_gateways.CleanupState.OPEN)
        self.assertEqual(
            cleanup.playwright_state,
            external_gateways.CleanupState.OPEN,
        )
        self.assertEqual(cleanup.remaining_pages, 0)
        self.assertEqual(cleanup.remaining_holders, 0)
        self.assertTrue(cleanup.invariant_ok)

    async def test_owned_observer_cancellation_audits_once_with_sanitized_result_and_telemetry(self):
        page = InternalLandingProbePage(
            search_visible=True,
            closed_hang=True,
        )
        context = ProbeLifecycleContext(create_page=page)
        gateway, manager, chromium = self._gateway(context)
        telemetry = []

        task = asyncio.create_task(
            gateway.probe_landing_selector_proposals(
                timeout_seconds=1.0,
                telemetry=lambda **fields: telemetry.append(fields),
            )
        )
        await page.closed_check_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(context.new_page_calls, 1)
        self.assertEqual(page.close_count, 1)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)

        cleanup = gateway.last_cleanup_result
        self.assertIsInstance(cleanup, external_gateways.CleanupResult)
        if cleanup is None:
            return
        self.assertEqual(cleanup.code, external_gateways.CleanupCode.CLEANUP_OK)
        self.assertEqual(cleanup.page_state, external_gateways.CleanupState.CLOSED)
        self.assertEqual(cleanup.context_state, external_gateways.CleanupState.OPEN)
        self.assertEqual(
            cleanup.playwright_state,
            external_gateways.CleanupState.OPEN,
        )
        self.assertEqual(cleanup.remaining_pages, 0)
        self.assertEqual(cleanup.remaining_holders, 0)
        self.assertTrue(cleanup.invariant_ok)
        self.assertEqual(len(telemetry), 1)
        self.assertEqual(
            set(telemetry[0]),
            {"event", "phase", "elapsed_ms", "error_code", "state"},
        )
        self.assertEqual(telemetry[0]["event"], "browser.cleanup")
        self.assertEqual(telemetry[0]["phase"], "PAGE_CLOSE")
        self.assertEqual(telemetry[0]["error_code"], "CLEANUP_OK")
        self.assertEqual(telemetry[0]["state"], "CLOSED")
        self.assertIsInstance(telemetry[0]["elapsed_ms"], int)
        sanitized = repr((cleanup, telemetry))
        self.assertNotIn("chat.zalo.me", sanitized)
        self.assertNotIn("#contact-search-input", sanitized)
        self.assertNotIn("secret", sanitized)

    async def test_owned_observer_cancellation_survives_cleanup_telemetry_failure(self):
        page = InternalLandingProbePage(
            search_visible=True,
            closed_hang=True,
        )
        context = ProbeLifecycleContext(create_page=page)
        gateway, manager, _chromium = self._gateway(context)

        def failing_telemetry(**_fields):
            raise RuntimeError("cleanup telemetry secret")

        task = asyncio.create_task(
            gateway.probe_landing_selector_proposals(
                timeout_seconds=1.0,
                telemetry=failing_telemetry,
            )
        )
        await page.closed_check_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(page.close_count, 1)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)
        self.assertIsInstance(
            gateway.last_cleanup_result,
            external_gateways.CleanupResult,
        )

    async def test_cancellation_during_zero_to_one_creation_preserves_non_owning_boundary(self):
        page = InternalLandingProbePage(search_visible=True)
        context = ProbeLifecycleContext(create_page=page, create_hang=True)
        gateway, manager, chromium = self._gateway(context)

        task = asyncio.create_task(
            gateway.probe_landing_selector_proposals(timeout_seconds=1.0)
        )
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(context.new_page_calls, 1)
        self.assertEqual(page.init_scripts, [])
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.close_count, 0)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)

    async def test_probe_launch_failure_is_one_attempt_without_circuit_retry(self):
        context = ProbeLifecycleContext()
        gateway, manager, chromium = self._gateway(
            context,
            launch_error=RuntimeError("launch secret"),
        )

        result = await gateway.probe_landing_selector_proposals(
            timeout_seconds=0.05
        )

        self.assertEqual(result["diagnostic_code"], "LANDING_PROFILE_UNAVAILABLE")
        self.assertEqual(result["launch_count"], 1)
        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(context.new_page_calls, 0)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)
        self.assertEqual(gateway._circuit.failure_count, 0)

    async def test_probe_does_not_add_init_script_before_page_set_validity(self):
        existing = InternalLandingProbePage(search_visible=True)
        context = ProbeLifecycleContext(pages=[existing])
        gateway, manager, chromium = self._gateway(context)

        result = await gateway.probe_landing_selector_proposals(
            timeout_seconds=0.05
        )

        self.assertEqual(result["diagnostic_code"], "PAGESET_PREEXISTING_ONE")
        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(context.new_page_calls, 0)
        self.assertEqual(existing.init_scripts, [])
        self.assertEqual(existing.goto_calls, [])
        self.assertEqual(existing.close_count, 0)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)

    async def test_navigation_failure_is_at_most_one_and_closes_only_observer_page(self):
        page = InternalLandingProbePage(
            search_visible=True,
            goto_error=RuntimeError("navigation secret"),
        )
        context = ProbeLifecycleContext(create_page=page)
        gateway, manager, chromium = self._gateway(context)

        result = await gateway.probe_landing_selector_proposals(
            timeout_seconds=0.05
        )

        self.assertEqual(result["diagnostic_code"], "LANDING_NAVIGATION_FAILED")
        self.assertEqual(result["launch_count"], 1)
        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(len(page.goto_calls), 1)
        self.assertEqual(result["zero_action_counters"]["navigation"], 1)
        self.assertEqual(page.close_count, 1)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(manager.exit_count, 0)

    async def test_normal_persistent_session_still_owns_context_and_playwright_cleanup(self):
        context = LifecycleContext()
        playwright = InternalLandingProbePlaywright(context)
        manager = InternalLandingProbeManager(playwright)
        gateway = PlaywrightBrowserGateway(playwright_factory=lambda: manager)

        async with gateway.persistent_session() as session:
            await session.new_page()

        self.assertEqual(context.close_count, 1)
        self.assertEqual(manager.exit_count, 1)


class InternalLandingProbeTests(unittest.IsolatedAsyncioTestCase):
    def _probe(self, session, **kwargs):
        probe = getattr(session, "probe_landing_selector_proposals", None)
        self.assertTrue(
            callable(probe),
            "production break: PersistentBrowserSession lacks the internal landing probe",
        )
        return probe(**kwargs)

    async def test_probe_reuses_worker_persistent_session_launch_path(self):
        page = InternalLandingProbePage()
        context = InternalLandingProbeContext(page)
        playwright = InternalLandingProbePlaywright(context)
        manager = InternalLandingProbeManager(playwright)
        gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: manager,
        )

        probe = getattr(gateway, "probe_landing_selector_proposals", None)
        self.assertTrue(callable(probe))
        if not callable(probe):
            return
        result = await probe(timeout_seconds=0.05)

        self.assertEqual(manager.enter_count, 1)
        self.assertEqual(manager.exit_count, 0)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(page.close_count, 1)
        self.assertEqual(result["launch_count"], 1)
        self.assertEqual(len(page.goto_calls), 1)
        self.assertEqual(page.goto_calls[0][0], "https://chat.zalo.me/")
        self.assertNotIn("user_data_dir", result)

    async def test_probe_navigates_only_expected_landing_and_has_zero_mutation_actions(self):
        page = InternalLandingProbePage(search_visible=True)
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page)
        )

        result = await self._probe(session, timeout_seconds=0.05)

        self.assertEqual(page.goto_calls, [("https://chat.zalo.me/", 5000)])
        self.assertEqual(page.locator_calls, ["#contact-search-input"])
        counters = result["zero_action_counters"]
        self.assertEqual(
            counters,
            {
                "navigation": 1,
                "search": 0,
                "click": 0,
                "type": 0,
                "chat": 0,
                "bcnb": 0,
                "evidence": 0,
            },
        )

    async def test_probe_sanitizes_host_and_search_observation(self):
        page = InternalLandingProbePage(
            url="https://chat.zalo.me/?secret-query=do-not-log",
            search_visible=True,
        )
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page)
        )

        result = await self._probe(session, timeout_seconds=0.05)

        self.assertTrue(result["expected_host_reached"])
        self.assertTrue(result["search_control_visible"])
        self.assertNotIn("secret-query", repr(result))
        self.assertNotIn("chat.zalo.me", repr(result))

    async def test_probe_never_guesses_auth_selector_from_url_text_or_class(self):
        page = InternalLandingProbePage(
            url="https://chat.zalo.me/login?qr=secret",
        )
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page)
        )

        result = await self._probe(session, timeout_seconds=0.05)

        self.assertEqual(
            result["auth_shell_candidate_status"],
            "UNPROVEN",
        )
        self.assertEqual(
            result["login_or_qr_candidate_status"],
            "UNPROVEN",
        )
        self.assertNotEqual(result["diagnostic_code"], "AUTHENTICATED_READY")

    async def test_probe_fails_closed_without_owner_approved_candidate_contract(self):
        page = InternalLandingProbePage(search_visible=True)
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page),
        )

        result = await self._probe(session, timeout_seconds=0.05)

        self.assertEqual(result["auth_shell_candidate_status"], "UNPROVEN")
        self.assertEqual(result["login_or_qr_candidate_status"], "UNPROVEN")
        self.assertEqual(
            result["diagnostic_code"],
            "LANDING_PROBE_NO_PROVEN_CANDIDATE",
        )
        self.assertNotEqual(result["diagnostic_code"], "AUTHENTICATED_READY")

    async def test_probe_schema_is_exact_and_contains_no_raw_browser_data(self):
        page = InternalLandingProbePage(
            url="https://chat.zalo.me/?profile=secret&text=message",
            search_visible=True,
        )
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page)
        )

        result = await self._probe(session, timeout_seconds=0.05)

        self.assertEqual(
            set(result),
            {
                "phase",
                "elapsed_ms",
                "profile_binding",
                "launch_count",
                "expected_host_reached",
                "search_control_visible",
                "auth_shell_candidate_status",
                "login_or_qr_candidate_status",
                "selector_contract_version",
                "diagnostic_code",
                "zero_action_counters",
            },
        )
        self.assertEqual(result["profile_binding"], "WORKER_PERSISTENT_SESSION")
        self.assertEqual(result["selector_contract_version"], "zalo-landing-probe-v1")
        self.assertNotIn("secret", repr(result))
        self.assertNotIn("message", repr(result))
        self.assertNotIn("chat.zalo.me", repr(result))

    async def test_probe_rejects_unapproved_candidate_callback_contract(self):
        page = InternalLandingProbePage(search_visible=True)

        with self.assertRaises(TypeError):
            external_gateways.PersistentBrowserSession(
                InternalLandingProbeContext(page),
                landing_auth_shell_candidate_probe=lambda _page: None,
            )

        self.assertEqual(page.goto_calls, [])

    async def test_probe_telemetry_is_the_same_sanitized_schema(self):
        page = InternalLandingProbePage(
            url="https://chat.zalo.me/?secret-query=do-not-log",
            search_visible=True,
        )
        telemetry = []
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page)
        )

        result = await self._probe(
            session,
            timeout_seconds=0.05,
            telemetry=lambda **fields: telemetry.append(fields),
        )

        self.assertEqual(len(telemetry), 1)
        self.assertEqual(telemetry[0], result)
        self.assertNotIn("secret-query", repr(telemetry))
        self.assertNotIn("chat.zalo.me", repr(telemetry))

    async def test_probe_maps_closed_page_and_locator_detachment_to_typed_failures(self):
        closed_page = InternalLandingProbePage(closed=True)
        closed_result = await self._probe(
            external_gateways.PersistentBrowserSession(
                InternalLandingProbeContext(closed_page)
            ),
            timeout_seconds=0.05,
        )
        self.assertEqual(closed_result["diagnostic_code"], "LANDING_PAGE_CLOSED")
        self.assertEqual(
            closed_result["auth_shell_candidate_status"],
            "LANDING_PAGE_CLOSED",
        )

        detached_page = InternalLandingProbePage(
            locator_error=RuntimeError("detached selector secret")
        )
        detached_result = await self._probe(
            external_gateways.PersistentBrowserSession(
                InternalLandingProbeContext(detached_page)
            ),
            timeout_seconds=0.05,
        )
        self.assertEqual(
            detached_result["diagnostic_code"],
            "LANDING_PAGE_DETACHED",
        )
        self.assertNotIn("detached selector secret", repr(detached_result))

    async def test_probe_bounds_timeout_and_preserves_cancellation(self):
        timeout_page = InternalLandingProbePage(
            locator_hang=True,
        )
        timeout_result = await self._probe(
            external_gateways.PersistentBrowserSession(
                InternalLandingProbeContext(timeout_page)
            ),
            timeout_seconds=0.01,
        )
        self.assertEqual(
            timeout_result["diagnostic_code"],
            "LANDING_READINESS_TIMEOUT",
        )
        self.assertLessEqual(timeout_result["elapsed_ms"], 10_000)

        cancellation_page = InternalLandingProbePage(locator_hang=True)
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(cancellation_page)
        )
        task = asyncio.create_task(self._probe(session, timeout_seconds=1.0))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_probe_does_not_call_provider_or_claim_runtime_readiness(self):
        source_calls = []

        async def forbidden_provider(_page):
            source_calls.append(True)
            return {
                "diagnostic_code": "AUTHENTICATED_READY",
            }

        page = InternalLandingProbePage(search_visible=True)
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page),
            landing_facts_provider=forbidden_provider,
        )

        result = await self._probe(session, timeout_seconds=0.05)

        self.assertEqual(source_calls, [])
        self.assertNotEqual(result["diagnostic_code"], "AUTHENTICATED_READY")

    async def test_gateway_probe_maps_profile_launch_failure_without_leak(self):
        class LockedManager:
            async def __aenter__(self):
                raise RuntimeError("profile lock secret")

            async def __aexit__(self, exc_type, exc, traceback):
                return None

        gateway = PlaywrightBrowserGateway(
            playwright_factory=LockedManager,
        )
        probe = getattr(gateway, "probe_landing_selector_proposals", None)
        self.assertTrue(callable(probe))
        if not callable(probe):
            return
        result = await probe(timeout_seconds=0.05)

        self.assertIn(
            result["diagnostic_code"],
            {"LANDING_PROFILE_UNAVAILABLE", "LANDING_SESSION_LAUNCH_FAILED"},
        )
        self.assertEqual(result["profile_binding"], "UNAVAILABLE")
        self.assertNotIn("profile lock secret", repr(result))

    async def test_session_rejects_foreign_page_without_navigating_or_binding_it(self):
        worker_page = InternalLandingProbePage()
        foreign_page = InternalLandingProbePage()
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(worker_page)
        )

        with self.assertRaises(TypeError):
            await session.probe_landing_selector_proposals(
                page=foreign_page,
                timeout_seconds=0.05,
            )

        self.assertEqual(worker_page.goto_calls, [])
        self.assertEqual(foreign_page.goto_calls, [])

    async def test_gateway_probe_emits_telemetry_exactly_once(self):
        page = InternalLandingProbePage()
        context = InternalLandingProbeContext(page)
        manager = InternalLandingProbeManager(
            InternalLandingProbePlaywright(context)
        )
        gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: manager,
        )
        telemetry = []

        result = await gateway.probe_landing_selector_proposals(
            timeout_seconds=0.05,
            telemetry=lambda **fields: telemetry.append(fields),
        )

        self.assertEqual(len(telemetry), 1)
        self.assertEqual(telemetry[0], result)

    def test_candidate_status_vocabulary_keeps_present_no_ambiguous_and_unproven_distinct(self):
        statuses = external_gateways.LandingCandidateStatus

        self.assertEqual(
            {
                statuses.CANDIDATE_PRESENT_WITH_LIVE_PROVENANCE.value,
                statuses.NO_CANDIDATE.value,
                statuses.UNPROVEN.value,
                statuses.AMBIGUOUS.value,
            },
            {
                "CANDIDATE_PRESENT_WITH_LIVE_PROVENANCE",
                "NO_CANDIDATE",
                "UNPROVEN",
                "AMBIGUOUS",
            },
        )

    async def test_probe_keeps_unknown_locator_exception_distinct_from_detachment(self):
        page = InternalLandingProbePage(
            locator_error=ValueError("unexpected locator failure secret")
        )
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page)
        )

        result = await self._probe(session, timeout_seconds=0.05)

        self.assertEqual(
            result["diagnostic_code"],
            "LANDING_UNKNOWN_FAIL_CLOSED",
        )
        self.assertEqual(
            result["auth_shell_candidate_status"],
            "LANDING_UNKNOWN_FAIL_CLOSED",
        )
        self.assertEqual(
            result["login_or_qr_candidate_status"],
            "LANDING_UNKNOWN_FAIL_CLOSED",
        )
        self.assertNotIn("unexpected locator failure secret", repr(result))


class PageSetFacade:
    """A page fake that fails if the classifier touches the page facade."""

    def __init__(self, name):
        self.name = name
        self.close_count = 0
        self.touched = []

    def __getattribute__(self, name):
        if name not in {
            "name",
            "close_count",
            "touched",
            "__class__",
            "__dict__",
            "__getattribute__",
            "__repr__",
        }:
            object.__getattribute__(self, "touched").append(name)
            raise AssertionError(f"page facade touched: {name}")
        return object.__getattribute__(self, name)

    def __repr__(self):
        return "PageSetFacade(<sanitized>)"


class PageSetContext:
    def __init__(self, pages=None, *, unavailable=False):
        self._pages = list(pages or [])
        self.unavailable = unavailable
        self.close_count = 0

    @property
    def pages(self):
        if self.unavailable:
            raise RuntimeError("context page-set secret")
        return self._pages

    async def close(self):
        self.close_count += 1


class UnboundPageSetContext:
    """A context-shaped fake that cannot carry weak context continuity."""

    __slots__ = ("_pages", "close_count")

    def __init__(self, pages=None):
        self._pages = list(pages or [])
        self.close_count = 0

    @property
    def pages(self):
        return self._pages

    async def close(self):
        self.close_count += 1


class PageSetStop:
    def __init__(self):
        self.stop_count = 0

    async def stop(self):
        self.stop_count += 1


class PageSetLifecycleContractTests(unittest.TestCase):
    def test_page_set_threat_model_is_explicitly_cooperative_only(self):
        threat_model = external_gateways.PAGESET_THREAT_MODEL

        self.assertIs(
            threat_model,
            external_gateways.PageSetThreatModel.COOPERATIVE_CALLER_ONLY,
        )
        self.assertEqual(threat_model.value, "COOPERATIVE_CALLER_ONLY")
        contract = external_gateways.PageSetThreatModel.__doc__ or ""
        contract_lower = contract.lower()
        self.assertIn("cooperative", contract_lower)
        self.assertIn("not a defense", contract_lower)
        self.assertIn("same-process introspection", contract_lower)
        self.assertIn("monkey-patching", contract_lower)
        self.assertIn("malicious code", contract_lower)
        self.assertIn("readiness proof", contract_lower)

    def test_classifier_returns_only_finite_sanitized_states_and_count_buckets(self):
        classifier = external_gateways.PageSetClassifier()
        page = PageSetFacade("secret-page")

        cases = [
            ([], "PAGESET_PREEXISTING_ZERO", "0"),
            ([page], "PAGESET_PREEXISTING_ONE", "1"),
            ([page, PageSetFacade("another-secret")], "PAGESET_PREEXISTING_MANY", "2PLUS"),
        ]
        for pages, category, count in cases:
            result = classifier.classify(PageSetContext(pages))
            self.assertEqual(result.category.value, category)
            self.assertEqual(result.count.value, count)
            self.assertEqual(result.action.value, "STOP_WITHOUT_OBSERVATION")
            self.assertEqual(set(result.as_dict()), {"category", "count", "action"})
            self.assertNotIn("secret-page", repr(result))
            self.assertNotIn("another-secret", repr(result))

        unavailable = classifier.classify(PageSetContext(unavailable=True))
        self.assertEqual(unavailable.category.value, "PAGESET_UNAVAILABLE")
        self.assertIsNone(unavailable.count)
        self.assertEqual(unavailable.action.value, "STOP_WITHOUT_OBSERVATION")

    def test_classifier_distinguishes_observer_only_and_post_create_mismatch(self):
        classifier = external_gateways.PageSetClassifier()
        observer_page = PageSetFacade("observer-secret")
        existing_page = PageSetFacade("existing-secret")

        observer_context = PageSetContext()
        classifier.classify_before_create(observer_context)
        observer_context.pages.append(observer_page)
        observer_only = classifier.classify_after_create(
            observer_context,
            observer_page,
        )
        self.assertEqual(observer_only.category.value, "PAGESET_OBSERVER_ONLY")
        self.assertEqual(observer_only.count.value, "1")
        self.assertEqual(observer_only.action.value, "OBSERVE_OWNED_PAGE")

        observer_context.pages.clear()
        classifier.classify_before_create(observer_context)
        observer_context.pages.append(observer_page)
        observer_only_after_create = classifier.classify_after_create(
            observer_context,
            observer_page,
        )
        self.assertEqual(
            observer_only_after_create.category.value,
            "PAGESET_OBSERVER_ONLY",
        )

        mismatch = classifier.classify(
            PageSetContext([existing_page, observer_page]),
            observer_owned_page=observer_page,
        )
        self.assertEqual(mismatch.category.value, "PAGESET_POSTCREATE_MISMATCH")
        self.assertEqual(mismatch.count.value, "2PLUS")
        self.assertEqual(mismatch.action.value, "STOP_WITHOUT_OBSERVATION")

        no_baseline = classifier.classify(
            PageSetContext([observer_page]),
            observer_owned_page=observer_page,
        )
        self.assertEqual(no_baseline.category.value, "PAGESET_POSTCREATE_MISMATCH")
        self.assertEqual(no_baseline.action.value, "STOP_WITHOUT_OBSERVATION")

        self.assertEqual(observer_page.touched, [])
        self.assertEqual(existing_page.touched, [])

    def test_classifier_does_not_read_page_facades_for_count_or_ownership(self):
        classifier = external_gateways.PageSetClassifier()
        page = PageSetFacade("page-secret")
        result = classifier.classify(PageSetContext([page]))
        self.assertEqual(result.category.value, "PAGESET_PREEXISTING_ONE")
        self.assertEqual(page.touched, [])

    def test_cooperative_context_affinity_and_fail_closed_cases(self):
        classifier = external_gateways.PageSetClassifier()

        context_a = PageSetContext()
        classifier.classify_before_create(context_a)
        observer_b = PageSetFacade("cross-context-observer-secret")
        context_b = PageSetContext([observer_b])
        cross_context = classifier.classify_after_create(
            context_b,
            observer_b,
        )
        self.assertEqual(
            cross_context.category.value,
            "PAGESET_POSTCREATE_MISMATCH",
        )
        self.assertEqual(cross_context.action.value, "STOP_WITHOUT_OBSERVATION")
        self.assertNotIn("PageSetContext", repr(cross_context))

        same_context = PageSetContext()
        classifier.classify_before_create(same_context)
        observer_same = PageSetFacade("same-context-observer-secret")
        same_context.pages.append(observer_same)
        same_context_result = classifier.classify_after_create(
            same_context,
            observer_same,
        )
        self.assertEqual(
            same_context_result.category.value,
            "PAGESET_OBSERVER_ONLY",
        )

        unbound_context = UnboundPageSetContext()
        classifier.classify_before_create(unbound_context)
        unbound_observer = PageSetFacade("unbound-observer-secret")
        unbound_context.pages.append(unbound_observer)
        unbound_result = classifier.classify_after_create(
            unbound_context,
            unbound_observer,
        )
        self.assertEqual(
            unbound_result.category.value,
            "PAGESET_POSTCREATE_MISMATCH",
        )
        self.assertEqual(unbound_result.action.value, "STOP_WITHOUT_OBSERVATION")
        self.assertEqual(unbound_observer.touched, [])

        absent_context = PageSetContext()
        absent_observer = PageSetFacade("absent-provenance-secret")
        absent_context.pages.append(absent_observer)
        absent_result = classifier.classify_after_create(
            absent_context,
            absent_observer,
        )
        self.assertEqual(
            absent_result.category.value,
            "PAGESET_POSTCREATE_MISMATCH",
        )
        self.assertEqual(absent_observer.touched, [])

    def test_public_page_set_api_accepts_no_snapshot_or_context_authority(self):
        classifier = external_gateways.PageSetClassifier()
        context = PageSetContext()
        snapshot = classifier.classify_before_create(context)
        observer = PageSetFacade("public-snapshot-secret")
        context.pages.append(observer)

        with self.assertRaises(TypeError):
            classifier.classify_after_create(context, snapshot, observer)

        with self.assertRaises(TypeError):
            classifier.classify_after_create(
                context,
                observer,
                preexisting=snapshot,
            )

        with self.assertRaises(TypeError):
            classifier.classify(
                context,
                preexisting=snapshot,
                observer_owned_page=observer,
            )

        with self.assertRaises(TypeError):
            external_gateways.classify_persistent_context_page_set(
                context,
                preexisting=snapshot,
                observer_owned_page=observer,
            )

        with self.assertRaises(TypeError):
            external_gateways.PersistentBrowserSession(context).classify_page_set(
                preexisting=snapshot,
                observer_owned_page=observer,
            )

        with self.assertRaises(TypeError):
            classifier.classify_after_create(context, context, observer)

        with self.assertRaises(TypeError):
            external_gateways.classify_persistent_context_page_set(
                context,
                preexisting=context,
                observer_owned_page=observer,
            )

        self.assertEqual(observer.touched, [])

    def test_different_classifier_without_cooperative_precreate_fails_closed(self):
        source_classifier = external_gateways.PageSetClassifier()
        current_classifier = external_gateways.PageSetClassifier()
        context = PageSetContext()
        source_classifier.classify_before_create(context)
        observer = PageSetFacade("different-classifier-secret")
        context.pages.append(observer)

        result = current_classifier.classify_after_create(context, observer)

        self.assertEqual(
            result.category.value,
            "PAGESET_POSTCREATE_MISMATCH",
        )
        self.assertEqual(result.action.value, "STOP_WITHOUT_OBSERVATION")
        self.assertEqual(observer.touched, [])


class PageSetOwnershipAbortTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_observer_states_use_explicit_non_owning_abort_without_cleanup(self):
        classifier = external_gateways.PageSetClassifier()
        page = PageSetFacade("existing-secret")
        stop = PageSetStop()
        contexts = [
            PageSetContext(),
            PageSetContext([page]),
            PageSetContext([page, PageSetFacade("second-secret")]),
            PageSetContext(unavailable=True),
        ]
        classifications = [classifier.classify(context) for context in contexts]
        mismatch = classifier.classify(
            PageSetContext([page]),
            observer_owned_page=PageSetFacade("untracked-observer-secret"),
        )
        classifications.append(mismatch)

        for context, classification in zip(contexts + [PageSetContext([page])], classifications):
            session = external_gateways.PersistentBrowserSession(context)
            result = await session.abort_without_observation(classification)

            self.assertEqual(result.category, classification.category)
            self.assertEqual(result.action.value, "STOP_WITHOUT_OBSERVATION")
            self.assertEqual(result.ownership.value, "NON_OWNING_ABORT")
            self.assertFalse(result.cleanup_performed)
            self.assertEqual(result.page_close_count, 0)
            self.assertEqual(result.context_close_count, 0)
            self.assertEqual(result.playwright_stop_count, 0)

            cleanup = await session.cleanup(
                playwright_stop=stop.stop,
                timeout_seconds=0.01,
                holder_count=0,
            )
            self.assertEqual(cleanup.code.value, "CLEANUP_SKIPPED_NON_OWNING_ABORT")
            self.assertEqual(context.close_count, 0)

        self.assertEqual(stop.stop_count, 0)
        self.assertEqual(page.close_count, 0)
        self.assertEqual(page.touched, [])

    async def test_observer_only_cannot_be_aborted_as_non_owning_stop(self):
        page = PageSetFacade("observer-secret")
        context = PageSetContext()
        classifier = external_gateways.PageSetClassifier()
        classifier.classify_before_create(context)
        context.pages.append(page)
        observer_only = classifier.classify(
            context,
            observer_owned_page=page,
        )
        session = external_gateways.PersistentBrowserSession(context)

        with self.assertRaises(ValueError):
            await session.abort_without_observation(observer_only)

        self.assertEqual(context.close_count, 0)
        self.assertEqual(page.close_count, 0)
        self.assertEqual(page.touched, [])

    async def test_cross_context_mismatch_abort_has_zero_close_counts(self):
        classifier = external_gateways.PageSetClassifier()
        context_a = PageSetContext()
        classifier.classify_before_create(context_a)
        observer_b = PageSetFacade("cross-context-abort-secret")
        context_b = PageSetContext([observer_b])
        mismatch = classifier.classify_after_create(
            context_b,
            observer_b,
        )
        self.assertEqual(
            mismatch.category.value,
            "PAGESET_POSTCREATE_MISMATCH",
        )

        stop = PageSetStop()
        session = external_gateways.PersistentBrowserSession(context_b)
        result = await session.abort_without_observation(mismatch)
        cleanup = await session.cleanup(
            playwright_stop=stop.stop,
            timeout_seconds=0.01,
            holder_count=0,
        )

        self.assertEqual(result.zero_close_counts, {
            "page": 0,
            "context": 0,
            "playwright": 0,
        })
        self.assertEqual(cleanup.code.value, "CLEANUP_SKIPPED_NON_OWNING_ABORT")
        self.assertEqual(observer_b.close_count, 0)
        self.assertEqual(observer_b.touched, [])
        self.assertEqual(context_b.close_count, 0)
        self.assertEqual(stop.stop_count, 0)

    async def test_non_cooperative_transition_abort_has_zero_close_counts(self):
        source_classifier = external_gateways.PageSetClassifier()
        current_classifier = external_gateways.PageSetClassifier()
        context = PageSetContext()
        source_classifier.classify_before_create(context)
        observer = PageSetFacade("different-classifier-abort-secret")
        context.pages.append(observer)
        mismatch = current_classifier.classify_after_create(
            context,
            observer,
        )
        self.assertEqual(
            mismatch.category.value,
            "PAGESET_POSTCREATE_MISMATCH",
        )

        stop = PageSetStop()
        session = external_gateways.PersistentBrowserSession(context)
        result = await session.abort_without_observation(mismatch)
        cleanup = await session.cleanup(
            playwright_stop=stop.stop,
            timeout_seconds=0.01,
            holder_count=0,
        )

        self.assertEqual(result.zero_close_counts, {
            "page": 0,
            "context": 0,
            "playwright": 0,
        })
        self.assertEqual(cleanup.code.value, "CLEANUP_SKIPPED_NON_OWNING_ABORT")
        self.assertEqual(observer.close_count, 0)
        self.assertEqual(observer.touched, [])
        self.assertEqual(context.close_count, 0)
        self.assertEqual(stop.stop_count, 0)

    async def test_abort_is_idempotent_and_normal_cleanup_stays_owned(self):
        page = PageSetFacade("existing-secret")
        context = PageSetContext([page])
        classifier = external_gateways.PageSetClassifier()
        classification = classifier.classify(context)
        session = external_gateways.PersistentBrowserSession(context)
        first = await session.abort_without_observation(classification)
        second = await session.abort_without_observation(classification)
        self.assertEqual(first, second)
        self.assertEqual(context.close_count, 0)
        self.assertEqual(page.close_count, 0)

        owned_context = LifecycleContext()
        owned_session = external_gateways.PersistentBrowserSession(owned_context)
        owned_page = await owned_session.new_page()
        owned_stop = LifecycleStop()
        cleanup = await owned_session.cleanup(
            playwright_stop=owned_stop.stop,
            timeout_seconds=0.05,
            holder_count=0,
        )
        self.assertEqual(cleanup.code, external_gateways.CleanupCode.CLEANUP_OK)
        self.assertEqual(owned_page.close_count, 1)
        self.assertEqual(owned_context.close_count, 1)
        self.assertEqual(owned_stop.stop_count, 1)

    async def test_gateway_contextmanager_honors_explicit_abort_without_stop(self):
        page = PageSetFacade("gateway-existing-secret")
        context = PageSetContext([page])
        playwright = FakePlaywright()
        playwright.chromium.context = context
        manager = FakePlaywrightManager(playwright)
        gateway = PlaywrightBrowserGateway(
            playwright_factory=lambda: manager,
            form_submitter_factory=FakeFormSubmitter,
        )

        async with gateway.persistent_session() as session:
            classification = session.classify_page_set()
            result = await session.abort_without_observation(classification)
            self.assertEqual(result.category.value, "PAGESET_PREEXISTING_ONE")

        self.assertEqual(context.close_count, 0)
        self.assertEqual(page.close_count, 0)
        self.assertEqual(manager.exit_count, 0)
        self.assertEqual(
            gateway.last_cleanup_result.code.value,
            "CLEANUP_SKIPPED_NON_OWNING_ABORT",
        )


class StructuralObservationContractTests(unittest.IsolatedAsyncioTestCase):
    def _observe(self, session, **kwargs):
        observe = getattr(session, "observe_structural_candidates", None)
        self.assertTrue(
            callable(observe),
            "production break: PersistentBrowserSession lacks structural observation",
        )
        if not callable(observe):
            return None
        return observe(**kwargs)

    async def test_observes_both_sealed_candidates_without_promoting_auth(self):
        page = InternalLandingProbePage(search_visible=True)
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page)
        )

        result = await self._observe(session)

        self.assertEqual(result["phase"], "LANDING_STRUCTURAL_OBSERVATION")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(
            [item["candidate_id"] for item in result["candidates"]],
            ["AUTHENTICATED_SHELL", "LOGIN_OR_QR"],
        )
        for item in result["candidates"]:
            self.assertEqual(item["status"], "UNPROVEN")
            self.assertIsNone(item["match_count"])
            self.assertIsNone(item["visible"])
            self.assertEqual(
                item["provenance_code"],
                "STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN",
            )
        self.assertEqual(
            result["diagnostic_code"],
            "STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN",
        )
        self.assertNotEqual(result["diagnostic_code"], "AUTHENTICATED_READY")
        self.assertEqual(page.goto_calls, [("https://chat.zalo.me/", 5000)])
        self.assertEqual(page.locator_calls, ["#contact-search-input"])
        self.assertEqual(page.close_count, 1)
        self.assertEqual(session.context.close_count, 0)

    async def test_structural_observation_rejects_caller_page_context_callback_and_selector(self):
        page = InternalLandingProbePage(search_visible=True)
        session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(page)
        )

        for rejected in (
            {"page": page},
            {"context": session.context},
            {"candidate_callback": lambda _page: True},
            {"selector": "#private-selector"},
        ):
            with self.assertRaises(TypeError):
                await self._observe(session, **rejected)
        self.assertEqual(page.goto_calls, [])

    async def test_preexisting_and_multiple_pages_stop_without_observation(self):
        existing = PageSetFacade("structural-existing-secret")
        for context, expected in (
            (ProbeLifecycleContext(pages=[existing]), "PAGESET_PREEXISTING_ONE"),
            (
                ProbeLifecycleContext(
                    pages=[existing, PageSetFacade("structural-second-secret")]
                ),
                "PAGESET_PREEXISTING_MANY",
            ),
        ):
            session = external_gateways.PersistentBrowserSession(context)
            result = await self._observe(session)
            self.assertEqual(result["diagnostic_code"], expected)
            self.assertEqual(result["page_set_category"], expected)
            self.assertEqual(result["navigation_count"], 0)
            self.assertEqual(context.new_page_calls, 0)
            self.assertEqual(context.close_count, 0)

    async def test_page_set_block_does_not_take_over_normal_session_cleanup(self):
        existing = PageSetFacade("structural-owner-secret")
        context = ProbeLifecycleContext(pages=[existing])
        session = external_gateways.PersistentBrowserSession(context)

        result = await self._observe(session)

        self.assertEqual(result["diagnostic_code"], "PAGESET_PREEXISTING_ONE")
        self.assertIsNone(session.page_set_abort_result)
        self.assertFalse(session._cleanup_started)

        stop = LifecycleStop()
        cleanup = await session.cleanup(
            playwright_stop=stop.stop,
            timeout_seconds=0.05,
            holder_count=0,
        )

        self.assertNotEqual(
            cleanup.code,
            external_gateways.CleanupCode.CLEANUP_SKIPPED_NON_OWNING_ABORT,
        )
        self.assertEqual(context.close_count, 1)
        self.assertEqual(stop.stop_count, 1)

    async def test_postcreate_foreign_page_set_mismatch_stops_without_closing_observer(self):
        page = InternalLandingProbePage(search_visible=True)
        context = ProbeLifecycleContext(
            create_page=page,
            extra_pages=[PageSetFacade("structural-foreign-secret")],
        )
        session = external_gateways.PersistentBrowserSession(context)

        result = await self._observe(session)

        self.assertEqual(result["diagnostic_code"], "PAGESET_POSTCREATE_MISMATCH")
        self.assertEqual(result["page_set_category"], "PAGESET_POSTCREATE_MISMATCH")
        self.assertEqual(result["navigation_count"], 0)
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.close_count, 0)
        self.assertEqual(context.close_count, 0)

    async def test_navigation_detach_timeout_and_cancellation_fail_closed(self):
        detached = InternalLandingProbePage(
            search_visible=True,
            locator_error=RuntimeError("detached structural secret"),
        )
        detached_result = await self._observe(
            external_gateways.PersistentBrowserSession(
                InternalLandingProbeContext(detached)
            )
        )
        self.assertEqual(detached_result["diagnostic_code"], "STRUCTURAL_PAGE_DETACHED")
        self.assertNotIn("detached structural secret", repr(detached_result))

        timed_out = InternalLandingProbePage(
            search_visible=True,
            locator_hang=True,
        )
        timeout_result = await self._observe(
            external_gateways.PersistentBrowserSession(
                InternalLandingProbeContext(timed_out)
            )
        )
        self.assertEqual(timeout_result["diagnostic_code"], "STRUCTURAL_OBSERVATION_TIMEOUT")

        navigation_failed = InternalLandingProbePage(
            goto_error=RuntimeError("network structural secret"),
        )
        navigation_result = await self._observe(
            external_gateways.PersistentBrowserSession(
                InternalLandingProbeContext(navigation_failed)
            )
        )
        self.assertEqual(
            navigation_result["diagnostic_code"],
            "STRUCTURAL_NAVIGATION_FAILED",
        )
        self.assertNotIn("network structural secret", repr(navigation_result))

        cancellation_page = InternalLandingProbePage(locator_hang=True)
        cancellation_session = external_gateways.PersistentBrowserSession(
            InternalLandingProbeContext(cancellation_page)
        )
        task = asyncio.create_task(self._observe(cancellation_session))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(cancellation_page.close_count, 1)
        self.assertEqual(cancellation_session.context.close_count, 0)

    async def test_gateway_structural_observation_requires_existing_active_session(self):
        context = ProbeLifecycleContext()
        gateway, manager, chromium = ProbeLifecycleGatewayTests()._gateway(context)

        unavailable = await gateway.observe_structural_candidates()

        self.assertEqual(unavailable["profile_binding"], "UNAVAILABLE")
        self.assertEqual(unavailable["diagnostic_code"], "STRUCTURAL_SESSION_UNAVAILABLE")
        self.assertEqual(chromium.launch_calls, 0)
        self.assertEqual(manager.enter_count, 0)

        page = InternalLandingProbePage(search_visible=True)
        context = ProbeLifecycleContext(create_page=page)
        gateway, manager, chromium = ProbeLifecycleGatewayTests()._gateway(context)
        async with gateway.persistent_session() as session:
            result = await gateway.observe_structural_candidates()
            self.assertEqual(result["profile_binding"], "WORKER_PERSISTENT_SESSION")
            self.assertIsNotNone(session)
        self.assertEqual(chromium.launch_calls, 1)
        self.assertEqual(manager.enter_count, 1)


if __name__ == "__main__":
    unittest.main()
