from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from playwright.async_api import async_playwright
from starlette.requests import Request

from api.landing_probe_routes import (
    run_landing_probe,
    run_structural_observation,
)
from services.external_gateways import PlaywrightBrowserGateway

try:
    from services.zalo_auth_readiness_diagnostic import (
        DiagnosticBootstrap,
        DiagnosticFailure,
        DiagnosticProfileKind,
        ZaloAuthReadinessDiagnostic,
        _INTERACTIVE_SELECTOR,
    )
except ModuleNotFoundError:  # RED: the Stage 1 boundary does not exist yet.
    DiagnosticBootstrap = None  # type: ignore[assignment,misc]
    DiagnosticFailure = None  # type: ignore[assignment,misc]
    DiagnosticProfileKind = None  # type: ignore[assignment,misc]
    ZaloAuthReadinessDiagnostic = None  # type: ignore[assignment,misc]
    _INTERACTIVE_SELECTOR = None  # type: ignore[assignment,misc]


class _Candidate:
    def __init__(
        self,
        *,
        tag: str,
        attrs: dict[str, str],
        visible: bool,
        metadata: object | None = None,
        evaluate_error: BaseException | None = None,
    ):
        self.tag = tag
        self.attrs = attrs
        self.visible = visible
        self.metadata = metadata
        self.evaluate_error = evaluate_error


class _CandidateLocator:
    def __init__(
        self,
        candidates: list[_Candidate],
        page: "_Page",
        count_sequence: list[int] | None = None,
        interactive_only: bool = False,
    ):
        self.candidates = candidates
        self.page = page
        self.count_sequence = count_sequence
        self.interactive_only = interactive_only
        self.nth_calls: list[int] = []

    def _matching_candidates(self) -> list[_Candidate]:
        if not self.interactive_only:
            return self.candidates
        interactive_tags = {"button", "input", "textarea", "select"}
        interactive_roles = {
            "button",
            "checkbox",
            "combobox",
            "gridcell",
            "link",
            "listbox",
            "menuitem",
            "menuitemcheckbox",
            "menuitemradio",
            "option",
            "radio",
            "searchbox",
            "slider",
            "spinbutton",
            "switch",
            "tab",
            "textbox",
            "treeitem",
        }
        return [
            candidate
            for candidate in self.candidates
            if candidate.tag.casefold() in interactive_tags
            or candidate.attrs.get("role", "").casefold() in interactive_roles
        ]

    async def count(self) -> int:
        self.page.count_calls += 1
        if self.page.block_discovery:
            self.page.discovery_started.set()
            await self.page.release_discovery.wait()
        if self.interactive_only:
            return len(self._matching_candidates())
        if self.count_sequence:
            index = min(
                self.page.count_calls - 1,
                len(self.count_sequence) - 1,
            )
            return self.count_sequence[index]
        return len(self.candidates)

    def nth(self, index: int) -> "_CandidateHandle":
        self.nth_calls.append(index)
        return _CandidateHandle(self._matching_candidates()[index])


class _CandidateHandle:
    def __init__(self, candidate: _Candidate):
        self.candidate = candidate

    async def is_visible(self) -> bool:
        return self.candidate.visible

    async def evaluate(self, _expression: str) -> object:
        # The diagnostic must request only structural metadata from the page.
        if self.candidate.evaluate_error is not None:
            raise self.candidate.evaluate_error
        if self.candidate.metadata is not None:
            return self.candidate.metadata
        return {
            "tag": self.candidate.tag,
            "attributes": dict(self.candidate.attrs),
        }


class _Page:
    def __init__(
        self,
        candidates: list[_Candidate],
        *,
        count_sequence: list[int] | None = None,
        goto_response: object | None = None,
    ):
        self.url = "about:blank"
        self.goto_calls: list[tuple[str, int]] = []
        self.locator_calls: list[str] = []
        self.close_count = 0
        self.count_calls = 0
        self._locator = _CandidateLocator(candidates, self, count_sequence)
        self._interactive_locator = _CandidateLocator(
            candidates,
            self,
            interactive_only=True,
        )
        self.goto_response = goto_response
        self.discovery_started = asyncio.Event()
        self.release_discovery = asyncio.Event()
        self.block_discovery = False

    async def goto(self, url: str, *, timeout: int):
        self.goto_calls.append((url, timeout))
        self.url = url
        return self.goto_response

    def locator(self, selector: str) -> _CandidateLocator:
        self.locator_calls.append(selector)
        if selector == _INTERACTIVE_SELECTOR:
            return self._interactive_locator
        return self._locator

    async def close(self):
        self.close_count += 1


class _NonZaloPage(_Page):
    async def goto(self, url: str, *, timeout: int):
        self.goto_calls.append((url, timeout))
        self.url = "https://non-zalo.invalid/"


class _HttpResponse:
    def __init__(self, *, ok: bool, status: int):
        self.ok = ok
        self.status = status


class _Context:
    def __init__(self, page: _Page):
        self.pages = [page]
        self.close_count = 0

    async def close(self):
        self.close_count += 1


class _Chromium:
    def __init__(self, context: _Context, *, launch_error: BaseException | None = None):
        self.context = context
        self.launch_error = launch_error
        self.launch_calls: list[dict[str, object]] = []

    async def launch_persistent_context(self, **kwargs):
        self.launch_calls.append(kwargs)
        if self.launch_error is not None:
            raise self.launch_error
        return self.context


class _Playwright:
    def __init__(self, chromium: _Chromium):
        self.chromium = chromium


class _Manager:
    def __init__(self, playwright: _Playwright):
        self.playwright = playwright
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self):
        self.enter_count += 1
        return self.playwright

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.exit_count += 1


def _request(path: str) -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 44000),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        },
        receive,
    )


class ZaloAuthReadinessDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def _require_stage1_boundary(self):
        self.assertIsNotNone(
            ZaloAuthReadinessDiagnostic,
            "Stage 1 diagnostic boundary is not implemented",
        )

    def _bootstrap(self, root: Path, *, profile_path: Path | None = None):
        self._require_stage1_boundary()
        return DiagnosticBootstrap(
            profile_kind=DiagnosticProfileKind.WORKER,
            profile_path=profile_path or root / "worker-profile",
            profile_lock_path=root / "worker-profile.lock",
            artifact_root=root / "private-artifacts",
            observation_timeout_seconds=2.0,
            cleanup_timeout_seconds=1.0,
        )

    async def test_collects_unknown_structural_metadata_and_cleans_owned_resources(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = _Page(
                [
                    _Candidate(
                        tag="button",
                        attrs={"id": "auth-shell", "role": "button"},
                        visible=True,
                    ),
                    _Candidate(
                        tag="input",
                        attrs={"id": "contact-search-input", "type": "text"},
                        visible=True,
                    ),
                ]
            )
            context = _Context(page)
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "worker-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                self._bootstrap(root),
                gateway_factory=lambda _bootstrap: gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(result["page_set"], "DEFAULT_PAGE_ACCEPTED")
            self.assertEqual(result["navigation_count"], 1)
            self.assertGreaterEqual(result["candidate_count"], 1)
            self.assertEqual(len(page.goto_calls), 1)
            self.assertEqual(context.close_count, 1)
            self.assertEqual(manager.enter_count, 1)
            self.assertEqual(manager.exit_count, 1)
            self.assertFalse((root / "worker-profile.lock").exists())

            artifact = root / "private-artifacts" / result["artifact_name"]
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["schema_version"],
                "zalo-auth-readiness-diagnostic-v2",
            )
            self.assertEqual(
                payload["collection"]["collection_scope"],
                "MAIN_FRAME",
            )
            self.assertTrue(payload["candidates"])
            self.assertTrue(
                all(
                    set(candidate) >= {"selector", "tag", "role", "attributes", "count", "visible"}
                    for candidate in payload["candidates"]
                )
            )
            forbidden = {"text", "inner_text", "body", "cookies", "qr", "account"}
            self.assertFalse(forbidden & set(json.dumps(payload).casefold().split()))

    async def test_real_playwright_extractor_collects_button_input_and_static_id(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bootstrap = DiagnosticBootstrap(
                profile_kind=DiagnosticProfileKind.EMPTY_TEMP,
                artifact_root=root / "private-artifacts",
                observation_timeout_seconds=5.0,
                cleanup_timeout_seconds=1.0,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(bootstrap)
            html = """
                <!doctype html>
                <html><body>
                  <button id="sendButton" type="button" data-testid="sendButton">
                    Synthetic button
                  </button>
                  <input id="lessonInput" name="lessonId" type="text">
                  <div id="staticPanel" role="region"></div>
                </body></html>
            """

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()

                    async def abort_route(route):
                        await route.abort()

                    await page.route("**/*", abort_route)
                    await page.set_content(html)

                    candidates, collection = await diagnostic._discover(page)

                    self.assertEqual(collection["collection_scope"], "MAIN_FRAME")
                    self.assertEqual(collection["broad_matches"], 3)
                    self.assertEqual(collection["evaluated"], 3)
                    self.assertEqual(collection["accepted"], 3)
                    self.assertEqual(collection["rejected"], 0)
                    self.assertEqual(collection["element_errors"], 0)
                    self.assertEqual(collection["outcome"], "METADATA_COLLECTED")
                    self.assertEqual(len(candidates), 3)
                    self.assertEqual(
                        [(item["tag"], item["selector"]) for item in candidates],
                        [
                            ("button", "#sendButton"),
                            ("input", "#lessonInput"),
                            ("div", "#staticPanel"),
                        ],
                    )
                    self.assertEqual(
                        [item["visible"] for item in candidates],
                        [True, True, False],
                    )
                finally:
                    await browser.close()

    async def test_body_ui_settle_waits_past_boot_metadata_for_late_controls(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bootstrap = DiagnosticBootstrap(
                profile_kind=DiagnosticProfileKind.EMPTY_TEMP,
                artifact_root=root / "private-artifacts",
                observation_timeout_seconds=5.0,
                cleanup_timeout_seconds=1.0,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(bootstrap)
            html = """
                <!doctype html>
                <html>
                  <head>
                    <meta name="boot-marker" content="boot">
                    <link id="boot-link" rel="stylesheet">
                  </head>
                  <body>
                    <div id="bootRoot"></div>
                  </body>
                </html>
            """

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()

                    async def abort_route(route):
                        await route.abort()

                    await page.route("**/*", abort_route)
                    await page.set_content(html)
                    await page.evaluate(
                        """
                        () => setTimeout(() => {
                            document.getElementById('bootRoot').innerHTML = `
                                <button id="lateButton" type="button">Late</button>
                                <input id="lateInput" name="lessonId" type="text">
                            `;
                        }, 500)
                        """
                    )

                    candidates, collection = await diagnostic._discover(page)

                    self.assertEqual(collection["collection_scope"], "MAIN_FRAME")
                    self.assertEqual(collection["outcome"], "METADATA_COLLECTED")
                    self.assertEqual(collection["visible_interactive_matches"], 2)
                    self.assertGreaterEqual(collection["settle_attempts"], 4)
                    self.assertEqual(
                        [(item["tag"], item["selector"]) for item in candidates],
                        [
                            ("div", "#bootRoot"),
                            ("button", "#lateButton"),
                            ("input", "#lateInput"),
                        ],
                    )
                finally:
                    await browser.close()

    async def test_body_ui_never_materializes_with_only_boot_root(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bootstrap = DiagnosticBootstrap(
                profile_kind=DiagnosticProfileKind.EMPTY_TEMP,
                artifact_root=root / "private-artifacts",
                observation_timeout_seconds=1.0,
                cleanup_timeout_seconds=1.0,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(bootstrap)

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.set_content(
                        """
                        <!doctype html>
                        <html><head><meta name="boot" content="only"></head>
                        <body><div id="bootRoot"></div></body></html>
                        """
                    )

                    with self.assertRaises(DiagnosticFailure) as raised:
                        await diagnostic._discover(page)

                    self.assertEqual(raised.exception.code, "UI_NOT_MATERIALIZED")
                    self.assertEqual(
                        raised.exception.collection["collection_scope"],
                        "MAIN_FRAME",
                    )
                    self.assertEqual(
                        raised.exception.collection["visible_interactive_matches"],
                        0,
                    )
                    self.assertGreater(
                        raised.exception.collection["settle_attempts"],
                        1,
                    )
                finally:
                    await browser.close()

    async def test_first_structural_count_timeout_remains_typed(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            page = _Page([])
            page.block_discovery = True
            diagnostic = ZaloAuthReadinessDiagnostic(
                DiagnosticBootstrap(
                    profile_kind=DiagnosticProfileKind.EMPTY_TEMP,
                    artifact_root=Path(temp) / "private-artifacts",
                    observation_timeout_seconds=0.05,
                    cleanup_timeout_seconds=1.0,
                )
            )

            with self.assertRaises(DiagnosticFailure) as raised:
                await diagnostic._discover(page)

            self.assertEqual(
                raised.exception.code,
                "STRUCTURAL_DISCOVERY_TIMEOUT",
            )

    async def test_post_navigation_host_mismatch_is_typed_without_exporting_url(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = _NonZaloPage([])
            context = _Context(page)
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "worker-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                self._bootstrap(root),
                gateway_factory=lambda _bootstrap: gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "BROWSER_ERROR")
            self.assertEqual(result["diagnostic_code"], "NAVIGATION_HOST_MISMATCH")
            self.assertEqual(page.goto_calls, [("https://chat.zalo.me/", 5000)])
            self.assertNotIn("non-zalo.invalid", repr(result))

    async def test_post_navigation_http_error_is_typed_without_reading_body(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = _Page(
                [],
                goto_response=_HttpResponse(ok=False, status=503),
            )
            context = _Context(page)
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "worker-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                self._bootstrap(root),
                gateway_factory=lambda _bootstrap: gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "BROWSER_ERROR")
            self.assertEqual(result["diagnostic_code"], "NAVIGATION_HTTP_ERROR")
            self.assertEqual(page.goto_calls, [("https://chat.zalo.me/", 5000)])
            self.assertEqual(page.locator_calls, [])

    async def test_discovery_waits_for_stable_main_frame_matches(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = _Page(
                [
                    _Candidate(
                        tag="button",
                        attrs={"id": "auth-shell", "role": "button"},
                        visible=True,
                    )
                ],
                count_sequence=[0, 1, 1],
            )
            context = _Context(page)
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "worker-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                self._bootstrap(root),
                gateway_factory=lambda _bootstrap: gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(result["diagnostic_code"], "STRUCTURAL_METADATA_COLLECTED")
            self.assertEqual(result["candidate_count"], 1)
            collection = result["collection"]
            self.assertEqual(collection["collection_scope"], "MAIN_FRAME")
            self.assertEqual(collection["broad_matches"], 1)
            self.assertEqual(collection["settle_attempts"], 3)
            self.assertEqual(collection["outcome"], "METADATA_COLLECTED")

    async def test_discovery_reports_filtered_and_element_errors(self):
        self._require_stage1_boundary()
        cases = (
            (
                _Candidate(
                    tag="button",
                    attrs={},
                    visible=True,
                    metadata={"tag": "!!!", "attributes": {}},
                ),
                "METADATA_REJECTED",
                0,
                0,
                1,
            ),
            (
                _Candidate(
                    tag="button",
                    attrs={},
                    visible=True,
                    evaluate_error=RuntimeError("raw element secret"),
                ),
                "ELEMENT_COLLECTION_FAILED",
                1,
                0,
                0,
            ),
        )
        for candidate, expected_code, expected_errors, expected_accepted, expected_rejected in cases:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                page = _Page([candidate])
                context = _Context(page)
                chromium = _Chromium(context)
                manager = _Manager(_Playwright(chromium))
                gateway = PlaywrightBrowserGateway(
                    profile_path=str(root / "worker-profile"),
                    playwright_factory=lambda: manager,
                    max_retries=0,
                    backoff_seconds=0,
                    failure_threshold=1,
                    cooldown_seconds=1,
                )
                diagnostic = ZaloAuthReadinessDiagnostic(
                    self._bootstrap(root),
                    gateway_factory=lambda _bootstrap: gateway,
                )

                result = await diagnostic.run()

                self.assertEqual(result["status"], "BROWSER_ERROR")
                self.assertEqual(result["diagnostic_code"], expected_code)
                collection = result["collection"]
                self.assertEqual(collection["collection_scope"], "MAIN_FRAME")
                self.assertEqual(collection["broad_matches"], 1)
                self.assertEqual(collection["evaluated"], 1)
                self.assertEqual(collection["element_errors"], expected_errors)
                self.assertEqual(collection["accepted"], expected_accepted)
                self.assertEqual(collection["rejected"], expected_rejected)
                self.assertNotIn("raw element secret", repr(result))

    async def test_mixed_valid_and_rejected_metadata_still_completes(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = _Page(
                [
                    _Candidate(
                        tag="button",
                        attrs={"id": "validButton"},
                        visible=True,
                    ),
                    _Candidate(
                        tag="button",
                        attrs={},
                        visible=True,
                        metadata={"tag": "!!!", "attributes": {}},
                    ),
                ]
            )
            context = _Context(page)
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "worker-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                self._bootstrap(root),
                gateway_factory=lambda _bootstrap: gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(result["diagnostic_code"], "STRUCTURAL_METADATA_COLLECTED")
            self.assertEqual(result["candidate_count"], 1)
            collection = result["collection"]
            self.assertEqual(collection["broad_matches"], 2)
            self.assertEqual(collection["evaluated"], 2)
            self.assertEqual(collection["accepted"], 1)
            self.assertEqual(collection["rejected"], 1)
            self.assertEqual(collection["element_errors"], 0)
            self.assertEqual(collection["outcome"], "METADATA_COLLECTED")

    async def test_busy_profile_is_fail_closed_without_stale_reclaim_or_browser_launch(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = root / "worker-profile.lock"
            lock_path.write_text(
                json.dumps({"owner_id": "other-owner", "pid": 99999}),
                encoding="utf-8",
            )
            page = _Page([])
            context = _Context(page)
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "worker-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                self._bootstrap(root),
                gateway_factory=lambda _bootstrap: gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "BUSY")
            self.assertEqual(manager.enter_count, 0)
            self.assertTrue(lock_path.exists())

    def test_worker_artifacts_cannot_be_nested_in_worker_profile(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                DiagnosticBootstrap(
                    profile_kind=DiagnosticProfileKind.WORKER,
                    profile_path=root / "worker-profile",
                    profile_lock_path=root / "worker-profile.lock",
                    artifact_root=root / "worker-profile" / "private-artifacts",
                )

    async def test_partial_launch_failure_stops_manager_and_does_not_retry(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = _Context(_Page([]))
            chromium = _Chromium(context, launch_error=RuntimeError("synthetic launch failure"))
            manager = _Manager(_Playwright(chromium))
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "worker-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                self._bootstrap(root),
                gateway_factory=lambda _bootstrap: gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "BROWSER_ERROR")
            self.assertEqual(len(chromium.launch_calls), 1)
            self.assertEqual(manager.exit_count, 1)
            self.assertFalse((root / "worker-profile.lock").exists())

    async def test_gateway_profile_binding_mismatch_fails_before_browser_launch(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = _Context(_Page([]))
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "different-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                self._bootstrap(root),
                gateway_factory=lambda _bootstrap: gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "BROWSER_ERROR")
            self.assertEqual(result["diagnostic_code"], "PROFILE_BINDING_MISMATCH")
            self.assertEqual(manager.enter_count, 0)
            self.assertFalse((root / "worker-profile.lock").exists())

    async def test_cleanup_deadline_is_reported_and_profile_lock_is_retained(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = _Context(_Page([]))
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            never = asyncio.Event()

            async def slow_exit(_exc_type, _exc, _traceback):
                manager.exit_count += 1
                await never.wait()

            manager.__aexit__ = slow_exit
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "worker-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            bootstrap = DiagnosticBootstrap(
                profile_kind=DiagnosticProfileKind.WORKER,
                profile_path=root / "worker-profile",
                profile_lock_path=root / "worker-profile.lock",
                artifact_root=root / "private-artifacts",
                observation_timeout_seconds=2.0,
                cleanup_timeout_seconds=0.05,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                bootstrap,
                gateway_factory=lambda _bootstrap: gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "BROWSER_ERROR")
            self.assertEqual(result["diagnostic_code"], "CLEANUP_INCOMPLETE")
            self.assertTrue(result["cleanup_incomplete"])
            self.assertFalse(result["cleanup_invariant_ok"])
            self.assertEqual(manager.exit_count, 1)
            self.assertTrue((root / "worker-profile.lock").exists())

            replay = await diagnostic.run()
            self.assertEqual(replay["status"], "BUSY")
            self.assertEqual(replay["diagnostic_code"], "CLEANUP_INCOMPLETE")
            self.assertEqual(manager.enter_count, 1)

    async def test_empty_temp_cleanup_incomplete_retains_profile_and_lock(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact_root = root / "private-artifacts"
            context = _Context(_Page([]))
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            never = asyncio.Event()

            async def slow_exit(_exc_type, _exc, _traceback):
                manager.exit_count += 1
                await never.wait()

            manager.__aexit__ = slow_exit

            def make_gateway(bootstrap):
                return PlaywrightBrowserGateway(
                    profile_path=bootstrap.profile_path,
                    playwright_factory=lambda: manager,
                    max_retries=0,
                    backoff_seconds=0,
                    failure_threshold=1,
                    cooldown_seconds=1,
                )

            diagnostic = ZaloAuthReadinessDiagnostic(
                DiagnosticBootstrap(
                    profile_kind=DiagnosticProfileKind.EMPTY_TEMP,
                    artifact_root=artifact_root,
                    observation_timeout_seconds=2.0,
                    cleanup_timeout_seconds=0.05,
                ),
                gateway_factory=make_gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "BROWSER_ERROR")
            self.assertEqual(result["diagnostic_code"], "CLEANUP_INCOMPLETE")
            self.assertTrue(result["cleanup_incomplete"])
            self.assertFalse(result["cleanup_invariant_ok"])
            self.assertEqual(manager.exit_count, 1)
            retained_profiles = [
                path
                for path in artifact_root.glob(".zalo-auth-diagnostic-profile-*")
                if path.is_dir()
            ]
            retained_locks = [
                path
                for path in artifact_root.glob(
                    ".zalo-auth-diagnostic-profile-*.lock"
                )
                if path.is_file()
            ]
            self.assertEqual(len(retained_profiles), 1)
            self.assertEqual(len(retained_locks), 1)

            replay = await diagnostic.run()
            self.assertEqual(replay["status"], "BUSY")
            self.assertEqual(replay["diagnostic_code"], "CLEANUP_INCOMPLETE")
            self.assertEqual(manager.enter_count, 1)

    async def test_cancellation_waits_for_owned_cleanup(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = _Page([])
            page.block_discovery = True
            context = _Context(page)
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway = PlaywrightBrowserGateway(
                profile_path=str(root / "worker-profile"),
                playwright_factory=lambda: manager,
                max_retries=0,
                backoff_seconds=0,
                failure_threshold=1,
                cooldown_seconds=1,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                self._bootstrap(root),
                gateway_factory=lambda _bootstrap: gateway,
            )

            task = asyncio.create_task(diagnostic.run())
            await page.discovery_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

            self.assertEqual(context.close_count, 1)
            self.assertEqual(manager.exit_count, 1)
            self.assertFalse((root / "worker-profile.lock").exists())

    async def test_empty_temp_mode_removes_only_its_owned_profile(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = _Page([])
            context = _Context(page)
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway_paths: list[str] = []

            def make_gateway(bootstrap):
                gateway_paths.append(bootstrap.profile_path)
                return PlaywrightBrowserGateway(
                    profile_path=bootstrap.profile_path,
                    playwright_factory=lambda: manager,
                    max_retries=0,
                    backoff_seconds=0,
                    failure_threshold=1,
                    cooldown_seconds=1,
                )

            bootstrap = DiagnosticBootstrap(
                profile_kind=DiagnosticProfileKind.EMPTY_TEMP,
                artifact_root=root / "private-artifacts",
                observation_timeout_seconds=2.0,
                cleanup_timeout_seconds=1.0,
            )
            diagnostic = ZaloAuthReadinessDiagnostic(
                bootstrap,
                gateway_factory=make_gateway,
            )

            result = await diagnostic.run()

            self.assertEqual(result["status"], "BROWSER_ERROR")
            self.assertEqual(result["diagnostic_code"], "UI_NOT_MATERIALIZED")
            self.assertEqual(
                result["collection"]["outcome"],
                "UI_NOT_MATERIALIZED",
            )
            self.assertEqual(result["collection"]["visible_interactive_matches"], 0)
            self.assertEqual(len(gateway_paths), 1)
            self.assertFalse(Path(gateway_paths[0]).exists())
            self.assertEqual(manager.exit_count, 1)

    async def test_empty_temp_cancellation_completes_owned_profile_deletion(self):
        self._require_stage1_boundary()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page = _Page([])
            page.block_discovery = True
            context = _Context(page)
            chromium = _Chromium(context)
            manager = _Manager(_Playwright(chromium))
            gateway_paths: list[str] = []

            def make_gateway(bootstrap):
                gateway_paths.append(bootstrap.profile_path)
                return PlaywrightBrowserGateway(
                    profile_path=bootstrap.profile_path,
                    playwright_factory=lambda: manager,
                    max_retries=0,
                    backoff_seconds=0,
                    failure_threshold=1,
                    cooldown_seconds=1,
                )

            diagnostic = ZaloAuthReadinessDiagnostic(
                DiagnosticBootstrap(
                    profile_kind=DiagnosticProfileKind.EMPTY_TEMP,
                    artifact_root=root / "private-artifacts",
                    observation_timeout_seconds=2.0,
                    cleanup_timeout_seconds=1.0,
                ),
                gateway_factory=make_gateway,
            )

            task = asyncio.create_task(diagnostic.run())
            await page.discovery_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

            self.assertEqual(manager.exit_count, 1)
            self.assertEqual(len(gateway_paths), 1)
            self.assertFalse(Path(gateway_paths[0]).exists())

    async def test_legacy_one_shot_routes_are_retired_before_runtime_access(self):
        for handler, path in (
            (run_landing_probe, "/api/internal/zalo/landing-probe/one-shot"),
            (run_structural_observation, "/api/internal/zalo/structural-observation/one-shot"),
        ):
            response = await handler(_request(path))
            self.assertEqual(response.status_code, 410)

    def test_diagnostic_import_does_not_load_config_or_dotenv(self):
        script = (
            "import sys; "
            "import services.zalo_auth_readiness_diagnostic; "
            "assert 'googleform_autosubmit.config' not in sys.modules"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_cli_exposes_only_worker_binding_and_empty_temp_modes(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.zalo_auth_readiness_diagnostic",
                "--help",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("worker", completed.stdout)
        self.assertIn("empty-temp", completed.stdout)
        self.assertIn("profile-lock-path", completed.stdout)
        self.assertIn("artifact-root", completed.stdout)
