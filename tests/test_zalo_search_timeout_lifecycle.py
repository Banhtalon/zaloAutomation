import asyncio
import gc
import traceback
import unittest
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright

from services.zalo_parallel_service import (
    TARGET_SEARCH_CLEAR_ERROR,
    TARGET_SEARCH_CLEAR_TIMEOUT,
    TARGET_SEARCH_FILL_ERROR,
    TARGET_SEARCH_FILL_TIMEOUT,
    TARGET_SEARCH_RESULT_ERROR,
    TARGET_SEARCH_RESULT_TIMEOUT,
    TargetSearchError,
    _run_target_search_operation,
    _wait_for_target_header,
)


class ZaloSearchTimeoutLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def _run_with_clean_gc_check(self, test_body):
        unhandled_exceptions = []
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _l, ctx: unhandled_exceptions.append(ctx))

        try:
            p = await async_playwright().start()
            try:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context()

                    async def fulfill_or_abort(route):
                        if route.request.url.startswith("https://chat.zalo.me/"):
                            await route.fulfill(
                                status=200,
                                content_type="text/html",
                                body="<!doctype html><html><body><div id='app'>Synthetic Zalo</div></body></html>",
                            )
                            return
                        await route.abort()

                    await context.route("**/*", fulfill_or_abort)
                    try:
                        page = await context.new_page()
                        await page.goto("https://chat.zalo.me/")
                        await test_body(page)
                        await page.close()
                    finally:
                        await context.close()
                finally:
                    await browser.close()
            finally:
                await p.stop()

            await asyncio.sleep(0.1)
            gc.collect()
            await asyncio.sleep(0.1)
        finally:
            loop.set_exception_handler(old_handler)

        self.assertEqual(
            unhandled_exceptions,
            [],
            f"Expected 0 unhandled future exceptions, got {len(unhandled_exceptions)}: {unhandled_exceptions}",
        )

    async def test_real_playwright_clear_timeout_lifecycle_has_no_unhandled_future_exception(self):
        async def body(page):
            caught_code = None
            try:
                await _run_target_search_operation(
                    lambda timeout_ms=None: page.locator("#contact-search-input").clear(
                        timeout=timeout_ms
                    ),
                    job_id=101,
                    worker_id="test-worker",
                    phase="SEARCH_CLEAR",
                    attempt=1,
                    timeout_seconds=0.2,
                    timeout_code=TARGET_SEARCH_CLEAR_TIMEOUT,
                    error_code=TARGET_SEARCH_CLEAR_ERROR,
                )
            except TargetSearchError as err:
                caught_code = err.code
                if err.__traceback__ is not None:
                    traceback.clear_frames(err.__traceback__)
            self.assertEqual(caught_code, TARGET_SEARCH_CLEAR_TIMEOUT)

        await self._run_with_clean_gc_check(body)

    async def test_real_playwright_fill_timeout_lifecycle_has_no_unhandled_future_exception(self):
        async def body(page):
            caught_code = None
            try:
                await _run_target_search_operation(
                    lambda timeout_ms=None: page.locator("#contact-search-input").fill(
                        "Test Query",
                        timeout=timeout_ms,
                    ),
                    job_id=102,
                    worker_id="test-worker",
                    phase="SEARCH_FILL",
                    attempt=1,
                    timeout_seconds=0.2,
                    timeout_code=TARGET_SEARCH_FILL_TIMEOUT,
                    error_code=TARGET_SEARCH_FILL_ERROR,
                )
            except TargetSearchError as err:
                caught_code = err.code
                if err.__traceback__ is not None:
                    traceback.clear_frames(err.__traceback__)
            self.assertEqual(caught_code, TARGET_SEARCH_FILL_TIMEOUT)

        await self._run_with_clean_gc_check(body)

    async def test_real_playwright_header_wait_timeout_has_no_unhandled_future_exception(self):
        async def body(page):
            await page.set_content(
                "<!doctype html><html><body><div class='chat-title'>Other Group</div></body></html>"
            )
            caught_code = None
            try:
                await _run_target_search_operation(
                    lambda: _wait_for_target_header(
                        page.locator('.header-title, .chat-title, [class*="conv-header"] .truncate'),
                        "Expected Group",
                        AsyncMock(),
                        timeout_seconds=0.2,
                    ),
                    job_id=103,
                    worker_id="test-worker",
                    phase="SEARCH_RESULT",
                    attempt=1,
                    timeout_seconds=0.2,
                    timeout_code=TARGET_SEARCH_RESULT_TIMEOUT,
                    error_code=TARGET_SEARCH_RESULT_ERROR,
                )
            except TargetSearchError as err:
                caught_code = err.code
                if err.__traceback__ is not None:
                    traceback.clear_frames(err.__traceback__)
            self.assertEqual(caught_code, TARGET_SEARCH_RESULT_TIMEOUT)

        await self._run_with_clean_gc_check(body)

    async def test_real_playwright_page_closed_mid_operation_handled_cleanly(self):
        async def body(page):
            async def close_soon():
                await asyncio.sleep(0.05)
                await page.close()

            close_task = asyncio.create_task(close_soon())
            caught = False
            try:
                await _run_target_search_operation(
                    lambda timeout_ms=None: page.locator("#contact-search-input").clear(
                        timeout=timeout_ms
                    ),
                    job_id=104,
                    worker_id="test-worker",
                    phase="SEARCH_CLEAR",
                    attempt=1,
                    timeout_seconds=1.0,
                    timeout_code=TARGET_SEARCH_CLEAR_TIMEOUT,
                    error_code=TARGET_SEARCH_CLEAR_ERROR,
                )
            except TargetSearchError:
                caught = True
            finally:
                await close_task

            self.assertTrue(caught)

        await self._run_with_clean_gc_check(body)

    async def test_cancellation_during_search_operation_is_preserved(self):
        loop = asyncio.get_running_loop()

        async def hung_operation(*_args, **_kwargs):
            await asyncio.Event().wait()

        task = loop.create_task(
            _run_target_search_operation(
                hung_operation,
                job_id=105,
                worker_id="test-worker",
                phase="SEARCH_CLEAR",
                attempt=1,
                timeout_seconds=5.0,
                timeout_code=TARGET_SEARCH_CLEAR_TIMEOUT,
                error_code=TARGET_SEARCH_CLEAR_ERROR,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_real_playwright_watchdog_timeout_has_no_unhandled_future_exception(self):
        async def body(page):
            caught_code = None
            try:
                await _run_target_search_operation(
                    lambda timeout_ms=None: page.locator("#missing").clear(timeout=0),
                    job_id=106,
                    worker_id="test-worker",
                    phase="SEARCH_CLEAR",
                    attempt=1,
                    timeout_seconds=0.2,
                    timeout_code=TARGET_SEARCH_CLEAR_TIMEOUT,
                    error_code=TARGET_SEARCH_CLEAR_ERROR,
                )
            except TargetSearchError as err:
                caught_code = err.code
                if err.__traceback__ is not None:
                    traceback.clear_frames(err.__traceback__)
            self.assertEqual(caught_code, TARGET_SEARCH_CLEAR_TIMEOUT)

        await self._run_with_clean_gc_check(body)

    async def test_real_playwright_cancellation_during_clear_has_no_unhandled_future_exception(self):
        async def body(page):
            task = asyncio.create_task(
                _run_target_search_operation(
                    lambda timeout_ms=None: page.locator("#missing").clear(
                        timeout=timeout_ms
                    ),
                    job_id=107,
                    worker_id="test-worker",
                    phase="SEARCH_CLEAR",
                    attempt=1,
                    timeout_seconds=5.0,
                    timeout_code=TARGET_SEARCH_CLEAR_TIMEOUT,
                    error_code=TARGET_SEARCH_CLEAR_ERROR,
                )
            )
            await asyncio.sleep(0.1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        await self._run_with_clean_gc_check(body)

    async def test_operation_raising_typeerror_is_invoked_exactly_once(self):
        calls = []

        def raises_type_error(*args, **kwargs):
            calls.append((args, kwargs))
            raise TypeError("operation-body-typeerror")

        with self.assertRaises(TargetSearchError) as cm:
            await _run_target_search_operation(
                raises_type_error,
                job_id=108,
                worker_id="test-worker",
                phase="SEARCH_CLEAR",
                attempt=1,
                timeout_seconds=0.2,
                timeout_code=TARGET_SEARCH_CLEAR_TIMEOUT,
                error_code=TARGET_SEARCH_CLEAR_ERROR,
            )

        self.assertEqual(cm.exception.code, TARGET_SEARCH_CLEAR_ERROR)
        self.assertEqual(len(calls), 1)

    async def test_run_with_clean_gc_check_restores_handler_on_failure(self):
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()

        async def fail(_page) -> None:
            raise RuntimeError("injected test failure")

        with self.assertRaises(RuntimeError):
            await self._run_with_clean_gc_check(fail)

        self.assertIs(loop.get_exception_handler(), old_handler)

    async def test_real_playwright_two_tabs_shared_connection_a_timeout_b_succeeds_no_future_warning(self):
        async def body(page_a):
            page_b = await page_a.context.new_page()
            try:
                await page_a.set_content("<main>A</main>")
                await page_b.set_content("<main>B</main>")

                task_a = asyncio.create_task(
                    _run_target_search_operation(
                        lambda timeout_ms=None: page_a.locator("#missing-a").clear(timeout=0),
                        job_id=701,
                        worker_id="worker-a",
                        phase="SEARCH_CLEAR",
                        attempt=1,
                        timeout_seconds=0.2,
                        timeout_code=TARGET_SEARCH_CLEAR_TIMEOUT,
                        error_code=TARGET_SEARCH_CLEAR_ERROR,
                    )
                )
                await asyncio.sleep(0.07)
                task_b = asyncio.create_task(page_b.wait_for_timeout(750))

                caught_code = None
                try:
                    await task_a
                except TargetSearchError as exc:
                    caught_code = exc.code
                    if exc.__traceback__ is not None:
                        traceback.clear_frames(exc.__traceback__)

                self.assertEqual(caught_code, TARGET_SEARCH_CLEAR_TIMEOUT)

                # Tab B must succeed and not be cancelled or error
                await task_b
                self.assertTrue(task_b.done())
                self.assertFalse(task_b.cancelled())
            finally:
                await page_b.close()

        await self._run_with_clean_gc_check(body)

