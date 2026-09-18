"""Focused tests for the code-sealed production auth-readiness adapter."""

from __future__ import annotations

import unittest
import asyncio

from playwright.async_api import async_playwright

from services.external_gateways import ExternalGateways
from services.zalo_parallel_service import build_zalo_job_progress
from services.zalo_parallel_service import classify_landing_readiness
from services.zalo_auth_readiness import (
    AUTH_READINESS_SELECTOR_CONTRACT_VERSION,
    AUTH_READINESS_SEARCH_SELECTOR,
    ZaloAuthReadinessAdapter,
)


async def _blocked_context(playwright):
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context()

    async def fulfill_or_abort(route):
        request_url = route.request.url
        if request_url.startswith("https://chat.zalo.me/"):
            await route.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    "<!doctype html><html><head><title>fixture</title></head>"
                    "<body><nav id='shell-nav' style='display:block;width:10px;height:10px'></nav>"
                    "<input id='contact-search-input'></body></html>"
                ),
            )
            return
        await route.abort()

    await context.route("**/*", fulfill_or_abort)
    return browser, context


class ZaloAuthReadinessAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_browser_ready_requires_exact_host_and_singletons(self):
        async with async_playwright() as playwright:
            browser, context = await _blocked_context(playwright)
            try:
                page = await context.new_page()
                await page.goto("https://chat.zalo.me/")
                facts = await ZaloAuthReadinessAdapter(
                    timeout_seconds=1.0,
                    poll_seconds=0.02,
                )(page)
            finally:
                await context.close()
                await browser.close()

        self.assertEqual(facts["diagnostic_code"], "AUTHENTICATED_READY")
        self.assertEqual(facts["selector_contract_version"], AUTH_READINESS_SELECTOR_CONTRACT_VERSION)
        self.assertTrue(facts["expected_host_reached"])
        self.assertTrue(facts["body_nav_visible"])
        self.assertEqual(facts["body_nav_count"], 1)
        self.assertTrue(facts["search_control_visible"])
        self.assertEqual(facts["search_control_count"], 1)
        self.assertIsNone(facts["login_or_qr_visible"])

    async def test_real_browser_waits_for_late_controls(self):
        first_search_count = asyncio.Event()

        class SignalLocator:
            def __init__(self, locator):
                self._locator = locator

            async def count(self):
                value = await self._locator.count()
                if value == 0:
                    first_search_count.set()
                return value

            async def is_visible(self):
                return await self._locator.is_visible()

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            await context.route(
                "**/*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=(
                        "<!doctype html><html><body><nav style='display:block;width:10px;height:10px'></nav>"
                        "</body></html>"
                    ),
                )
                if route.request.url.startswith("https://chat.zalo.me/")
                else route.abort(),
            )
            try:
                page = await context.new_page()
                await page.goto("https://chat.zalo.me/")
                original_locator = page.locator

                def instrumented_locator(selector, *args, **kwargs):
                    locator = original_locator(selector, *args, **kwargs)
                    if selector == AUTH_READINESS_SEARCH_SELECTOR:
                        return SignalLocator(locator)
                    return locator

                page.locator = instrumented_locator
                observation = asyncio.create_task(ZaloAuthReadinessAdapter(
                    timeout_seconds=1.0,
                    poll_seconds=0.02,
                )(page))
                await asyncio.wait_for(first_search_count.wait(), timeout=1.0)
                await page.evaluate(
                    "document.body.insertAdjacentHTML('beforeend', "
                    "'<input id=\\\"contact-search-input\\\">')"
                )
                facts = await observation
            finally:
                await context.close()
                await browser.close()

        self.assertEqual(facts["diagnostic_code"], "AUTHENTICATED_READY")
        self.assertGreaterEqual(facts["poll_count"], 2)

    async def test_real_browser_ambiguity_is_not_ready(self):
        async with async_playwright() as playwright:
            browser, context = await _blocked_context(playwright)
            try:
                page = await context.new_page()
                await page.goto("https://chat.zalo.me/")
                await page.locator("body").evaluate(
                    "body => body.insertAdjacentHTML('beforeend', '<nav></nav>')"
                )
                facts = await ZaloAuthReadinessAdapter(
                    timeout_seconds=0.4,
                    poll_seconds=0.02,
                )(page)
            finally:
                await context.close()
                await browser.close()

        self.assertEqual(facts["diagnostic_code"], "AUTH_READINESS_AMBIGUOUS")
        self.assertEqual(facts["body_nav_count"], 2)

    async def test_closed_page_is_typed_without_login_inference(self):
        async with async_playwright() as playwright:
            browser, context = await _blocked_context(playwright)
            page = await context.new_page()
            await page.close()
            facts = await ZaloAuthReadinessAdapter(timeout_seconds=0.2)(page)
            await context.close()
            await browser.close()

        self.assertEqual(facts["diagnostic_code"], "AUTH_READINESS_PAGE_CLOSED")
        self.assertIsNone(facts["login_or_qr_visible"])

    def test_production_factory_wires_real_adapter_without_ready_injection(self):
        gateway = ExternalGateways.production(profile_path="C:/temp/zalo-profile")
        factory = getattr(gateway.browser, "_landing_facts_provider_factory", None)
        self.assertIsNotNone(factory)
        provider = factory()
        self.assertIsInstance(provider, ZaloAuthReadinessAdapter)

    def test_zero_attempted_is_not_reported_as_partial(self):
        progress = build_zalo_job_progress(
            scan_results=[],
            batch_summary={
                "scan": {"BLOCKED": 2},
                "write": {"BLOCKED_BATCH_HALT": 2},
            },
            halt_reason="AUTH_READINESS_LOADING_TIMEOUT",
            dry_run=True,
        )

        self.assertEqual(progress["attempted_target_count"], 0)
        self.assertEqual(progress["status"], "FAILED")
        self.assertEqual(progress["reason_code"], "AUTH_READINESS_LOADING_TIMEOUT")
        self.assertIn("Chưa quét được", progress["message"])

    def test_partial_requires_at_least_one_attempted_target(self):
        progress = build_zalo_job_progress(
            scan_results=[{"scan_target": {"class_code": "VT-SYN"}}],
            batch_summary={
                "scan": {"VERIFIED": 1},
                "write": {"VERIFIED": 1, "OUTCOME_UNKNOWN": 1},
            },
            halt_reason=None,
            dry_run=True,
        )

        self.assertEqual(progress["attempted_target_count"], 1)
        self.assertEqual(progress["status"], "PARTIAL_SUCCESS")
        self.assertEqual(progress["reason_code"], "TARGETS_NEED_REVIEW")

    def test_sealed_ready_facts_require_host_and_both_singletons(self):
        facts = {
            "phase": "ZALO_AUTH_READINESS",
            "selector_contract_version": AUTH_READINESS_SELECTOR_CONTRACT_VERSION,
            "diagnostic_code": "AUTHENTICATED_READY",
            "expected_host_reached": True,
            "body_nav_visible": True,
            "body_nav_count": 1,
            "search_control_visible": True,
            "search_control_count": 1,
            "login_or_qr_visible": None,
        }
        self.assertEqual(
            classify_landing_readiness(facts).value,
            "AUTHENTICATED_READY",
        )

        facts["body_nav_count"] = 2
        self.assertNotEqual(
            classify_landing_readiness(facts).value,
            "AUTHENTICATED_READY",
        )


if __name__ == "__main__":
    unittest.main()
