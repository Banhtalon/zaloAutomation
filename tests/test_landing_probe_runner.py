from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

import api.landing_probe_routes as landing_probe_routes
from api.landing_probe_routes import router, run_landing_probe
import services.landing_probe_runner as landing_probe_runner
from services.landing_probe_runner import (
    LANDING_PROBE_RUNNER_TIMEOUT_SECONDS,
    LandingProbeRunStatus,
    run_landing_probe_once,
)


class FakeBrowser:
    def __init__(self, result=None, error=None, started=None, release=None):
        self.result = result or {
            "phase": "LANDING_SELECTOR_PROPOSAL_PROBE",
            "elapsed_ms": 12,
            "profile_binding": "WORKER_PERSISTENT_SESSION",
            "launch_count": 1,
            "expected_host_reached": True,
            "search_control_visible": True,
            "auth_shell_candidate_status": "UNPROVEN",
            "login_or_qr_candidate_status": "UNPROVEN",
            "selector_contract_version": "zalo-landing-probe-v1",
            "diagnostic_code": "LANDING_PROBE_NO_PROVEN_CANDIDATE",
            "zero_action_counters": {
                "navigation": 1,
                "search": 0,
                "click": 0,
                "type": 0,
                "chat": 0,
                "bcnb": 0,
                "evidence": 0,
            },
        }
        self.error = error
        self.started = started
        self.release = release
        self.calls = 0
        self.call_arguments = []
        self.observed_identity = None

    async def probe_landing_selector_proposals(self, **kwargs):
        self.calls += 1
        self.observed_identity = self
        self.call_arguments.append(kwargs)
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.result


class FakeRuntime:
    def __init__(self, browser):
        self.external = SimpleNamespace(browser=browser)
        self.browser_lock = asyncio.Lock()
        self._consumed = False

    @property
    def landing_probe_consumed(self):
        return self._consumed

    def consume_landing_probe_attempt(self):
        if self._consumed:
            return False
        self._consumed = True
        return True


class FakeRequestApp:
    def __init__(self, runtime=None):
        self.state = SimpleNamespace(
            container=SimpleNamespace(worker_runtime=runtime)
        )


def make_request(
    *,
    host="127.0.0.1",
    query=b"",
    body=b"",
    headers=(),
    runtime=None,
):
    encoded_headers = [(key.lower().encode(), value.encode()) for key, value in headers]
    if body and not any(key == b"content-length" for key, _ in encoded_headers):
        encoded_headers.append((b"content-length", str(len(body)).encode()))
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/internal/zalo/landing-probe/one-shot",
        "raw_path": b"/api/internal/zalo/landing-probe/one-shot",
        "query_string": query,
        "headers": encoded_headers,
        "client": (host, 54321),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
        "root_path": "",
        "http_version": "1.1",
    }
    scope["app"] = FakeRequestApp(runtime)
    request = Request(scope, receive)
    return request


def response_json(response: JSONResponse):
    return json.loads(response.body.decode("utf-8"))


def router_app(runtime):
    application = FastAPI()
    application.state.container = SimpleNamespace(worker_runtime=runtime)
    application.include_router(router)
    return application


async def invoke_router(
    application,
    *,
    path,
    client=("127.0.0.1", 54321),
    headers=(),
):
    encoded_headers = [
        (key.lower().encode(), value.encode()) for key, value in headers
    ]
    received = False
    messages = []

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": encoded_headers,
        "client": client,
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
        "root_path": "",
        "http_version": "1.1",
        "app": application,
    }
    await application(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], json.loads(body.decode("utf-8"))


class LandingProbeRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_or_misbound_runtime_fails_closed(self):
        runtime = FakeRuntime(browser=SimpleNamespace())

        result = await run_landing_probe_once(runtime)

        self.assertEqual(result["status"], LandingProbeRunStatus.UNAVAILABLE.value)
        self.assertFalse(result["attempt_consumed"])
        self.assertFalse(result["gateway_invoked"])
        self.assertIsNone(result["probe"])
        self.assertFalse(runtime.landing_probe_consumed)

    async def test_uses_exact_bound_browser_probe_without_construction(self):
        browser = FakeBrowser()
        runtime = FakeRuntime(browser)

        result = await run_landing_probe_once(runtime)

        self.assertEqual(result["status"], LandingProbeRunStatus.COMPLETED.value)
        self.assertIs(browser.observed_identity, browser)
        self.assertEqual(browser.calls, 1)
        self.assertEqual(
            browser.call_arguments,
            [{"timeout_seconds": LANDING_PROBE_RUNNER_TIMEOUT_SECONDS}],
        )

    async def test_first_invocation_only_and_replay_is_rejected(self):
        browser = FakeBrowser()
        runtime = FakeRuntime(browser)

        first = await run_landing_probe_once(runtime)
        second = await run_landing_probe_once(runtime)

        self.assertEqual(first["status"], LandingProbeRunStatus.COMPLETED.value)
        self.assertEqual(second["status"], LandingProbeRunStatus.ALREADY_CONSUMED.value)
        self.assertFalse(second["gateway_invoked"])
        self.assertEqual(browser.calls, 1)

    async def test_busy_lock_fails_without_consuming_then_allows_one_attempt(self):
        browser = FakeBrowser()
        runtime = FakeRuntime(browser)
        await runtime.browser_lock.acquire()
        try:
            busy = await run_landing_probe_once(runtime)
        finally:
            runtime.browser_lock.release()

        self.assertEqual(busy["status"], LandingProbeRunStatus.BUSY.value)
        self.assertFalse(busy["attempt_consumed"])
        self.assertEqual(browser.calls, 0)
        self.assertFalse(runtime.landing_probe_consumed)

        completed = await run_landing_probe_once(runtime)
        self.assertEqual(completed["status"], LandingProbeRunStatus.COMPLETED.value)
        self.assertEqual(browser.calls, 1)

    async def test_concurrent_replay_cannot_call_gateway_twice(self):
        started = asyncio.Event()
        release = asyncio.Event()
        browser = FakeBrowser(started=started, release=release)
        runtime = FakeRuntime(browser)

        first_task = asyncio.create_task(run_landing_probe_once(runtime))
        await asyncio.wait_for(started.wait(), timeout=1)
        second = await run_landing_probe_once(runtime)
        release.set()
        first = await first_task

        self.assertEqual(second["status"], LandingProbeRunStatus.BUSY.value)
        self.assertFalse(second["gateway_invoked"])
        self.assertEqual(first["status"], LandingProbeRunStatus.COMPLETED.value)
        self.assertEqual(browser.calls, 1)

    async def test_cancellation_propagates_and_consumes_attempt(self):
        started = asyncio.Event()
        release = asyncio.Event()
        browser = FakeBrowser(started=started, release=release)
        runtime = FakeRuntime(browser)

        task = asyncio.create_task(run_landing_probe_once(runtime))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(runtime.landing_probe_consumed)
        self.assertEqual(browser.calls, 1)
        replay = await run_landing_probe_once(runtime)
        self.assertEqual(replay["status"], LandingProbeRunStatus.ALREADY_CONSUMED.value)

    async def test_result_and_telemetry_are_sanitized(self):
        browser = FakeBrowser(
            result={
                "phase": "raw https://private.example/page",
                "elapsed_ms": 999999999,
                "profile_binding": "C:\\private\\profile",
                "launch_count": 900,
                "expected_host_reached": "yes",
                "search_control_visible": "yes",
                "auth_shell_candidate_status": "secret=password",
                "login_or_qr_candidate_status": "https://private.example",
                "selector_contract_version": "secret-token",
                "diagnostic_code": "raw exception message password=secret",
                "zero_action_counters": {
                    "navigation": 99,
                    "search": "private",
                    "raw": "cookie=secret",
                },
                "raw_page": object(),
            }
        )
        runtime = FakeRuntime(browser)
        telemetry = []

        result = await run_landing_probe_once(
            runtime,
            telemetry=lambda **fields: telemetry.append(fields),
        )
        encoded = json.dumps(result, default=str)
        telemetry_text = json.dumps(telemetry, default=str)

        self.assertNotIn("private", encoded)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("private", telemetry_text)
        self.assertNotIn("secret", telemetry_text)
        self.assertEqual(
            set(result),
            {"phase", "status", "diagnostic_code", "attempt_consumed", "gateway_invoked", "probe"},
        )
        self.assertEqual(len(telemetry), 1)

    async def test_gateway_error_is_sanitized(self):
        browser = FakeBrowser(error=RuntimeError("password=secret path=C:\\private"))
        runtime = FakeRuntime(browser)

        result = await run_landing_probe_once(runtime)

        self.assertEqual(result["status"], LandingProbeRunStatus.FAILED.value)
        self.assertNotIn("secret", json.dumps(result, default=str))
        self.assertNotIn("private", json.dumps(result, default=str))

    async def test_gateway_timeout_is_bounded_and_consumes_attempt(self):
        started = asyncio.Event()
        release = asyncio.Event()
        browser = FakeBrowser(started=started, release=release)
        runtime = FakeRuntime(browser)

        with patch(
            "services.landing_probe_runner.LANDING_PROBE_RUNNER_TIMEOUT_SECONDS",
            0.01,
        ):
            result = await run_landing_probe_once(runtime)

        self.assertEqual(result["status"], LandingProbeRunStatus.TIMEOUT.value)
        self.assertTrue(result["attempt_consumed"])
        self.assertTrue(result["gateway_invoked"])
        self.assertEqual(browser.calls, 1)


class LandingProbeRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_retired_landing_route_does_not_forward_runtime(self):
        browser = FakeBrowser()
        runtime = FakeRuntime(browser)
        response = await run_landing_probe(make_request(runtime=runtime))

        self.assertEqual(response.status_code, 410)
        self.assertEqual(browser.calls, 0)

    async def test_retired_landing_route_returns_sanitized_result(self):
        browser = FakeBrowser()
        runtime = FakeRuntime(browser)
        response = await run_landing_probe(make_request(runtime=runtime))
        payload = response_json(response)

        self.assertEqual(response.status_code, 410)
        self.assertEqual(payload["status"], "RETIRED")
        self.assertEqual(payload["diagnostic_code"], "LANDING_PROBE_ROUTE_RETIRED")
        self.assertEqual(browser.calls, 0)
        self.assertNotIn("raw_page", json.dumps(payload, default=str))

    async def test_retired_landing_route_ignores_client_address(self):
        browser = FakeBrowser()
        runtime = FakeRuntime(browser)
        response = await run_landing_probe(
            make_request(host="192.0.2.10", runtime=runtime)
        )
        payload = response_json(response)

        self.assertEqual(response.status_code, 410)
        self.assertEqual(payload["diagnostic_code"], "LANDING_PROBE_ROUTE_RETIRED")
        self.assertEqual(browser.calls, 0)

    async def test_retired_landing_route_ignores_body_and_query(self):
        for request in (
            make_request(body=b"{}", runtime=FakeRuntime(FakeBrowser())),
            make_request(query=b"payload=1", runtime=FakeRuntime(FakeBrowser())),
        ):
            response = await run_landing_probe(request)
            payload = response_json(response)
            self.assertEqual(response.status_code, 410)
            self.assertEqual(payload["diagnostic_code"], "LANDING_PROBE_ROUTE_RETIRED")

    async def test_retired_landing_route_does_not_require_runtime(self):
        response = await run_landing_probe(make_request(runtime=None))
        payload = response_json(response)

        self.assertEqual(response.status_code, 410)
        self.assertEqual(payload["status"], "RETIRED")
        self.assertNotIn("detail", payload)


class LandingProbeRouterRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_path_is_retired_and_old_path_has_no_alias(self):
        browser = FakeBrowser()
        runtime = FakeRuntime(browser)
        application = router_app(runtime)

        required_status, required_payload = await invoke_router(
            application,
            path="/api/internal/zalo/landing-probe/one-shot",
        )
        old_status, _old_payload = await invoke_router(
            application,
            path="/api/internal/landing-probe/one-shot",
        )

        self.assertEqual(required_status, 410)
        self.assertEqual(required_payload["status"], "RETIRED")
        self.assertEqual(old_status, 404)
        self.assertEqual(browser.calls, 0)

    async def test_absent_client_still_gets_retired_response(self):
        browser = FakeBrowser()
        runtime = FakeRuntime(browser)
        application = router_app(runtime)

        status, payload = await invoke_router(
            application,
            path="/api/internal/zalo/landing-probe/one-shot",
            client=None,
        )

        self.assertEqual(status, 410)
        self.assertEqual(payload["status"], "RETIRED")
        self.assertEqual(browser.calls, 0)

    async def test_forwarded_headers_cannot_reactivate_retired_route(self):
        browser = FakeBrowser()
        runtime = FakeRuntime(browser)
        application = router_app(runtime)

        status, payload = await invoke_router(
            application,
            path="/api/internal/zalo/landing-probe/one-shot",
            client=("192.0.2.10", 54321),
            headers=(
                ("x-forwarded-for", "127.0.0.1"),
                ("x-real-ip", "127.0.0.1"),
            ),
        )

        self.assertEqual(status, 410)
        self.assertEqual(payload["status"], "RETIRED")
        self.assertEqual(browser.calls, 0)


class StructuralFakeBrowser:
    """Minimal browser sentinel proving the retired runner never invokes it."""

    def __init__(self):
        self.calls = 0


class StructuralFakeRuntime:
    def __init__(self, browser):
        self.external = SimpleNamespace(browser=browser)
        self.browser_lock = asyncio.Lock()


class RetiredStructuralObservationRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_retired_runner_rejects_without_runtime_or_browser_access(self):
        browser = StructuralFakeBrowser()
        runtime = StructuralFakeRuntime(browser)

        result = await landing_probe_runner.run_structural_observation_once(runtime)

        self.assertEqual(result["status"], "REQUEST_REJECTED")
        self.assertEqual(result["diagnostic_code"], "STRUCTURAL_RUNNER_RETIRED")
        self.assertFalse(result["attempt_consumed"])
        self.assertFalse(result["gateway_invoked"])
        self.assertEqual(browser.calls, 0)


class StructuralObservationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_structural_route_is_retired_before_runtime_access(self):
        route = getattr(landing_probe_routes, "run_structural_observation", None)
        self.assertTrue(callable(route), "production break: missing structural route")
        if not callable(route):
            return
        browser = StructuralFakeBrowser()
        runtime = StructuralFakeRuntime(browser)
        response = await route(
            make_request(runtime=runtime),
        )
        payload = response_json(response)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(payload["status"], "RETIRED")
        self.assertEqual(browser.calls, 0)

    async def test_structural_route_stays_retired_for_non_loopback_and_input(self):
        route = getattr(landing_probe_routes, "run_structural_observation", None)
        self.assertTrue(callable(route), "production break: missing structural route")
        if not callable(route):
            return
        for request in (
            make_request(host="192.0.2.10", runtime=StructuralFakeRuntime(StructuralFakeBrowser())),
            make_request(body=b"{}", runtime=StructuralFakeRuntime(StructuralFakeBrowser())),
            make_request(query=b"candidate=auth", runtime=StructuralFakeRuntime(StructuralFakeBrowser())),
        ):
            response = await route(request)
            payload = response_json(response)
            self.assertEqual(response.status_code, 410)
            self.assertEqual(payload["status"], "RETIRED")

    async def test_structural_router_registration_has_exact_path_only(self):
        application = router_app(StructuralFakeRuntime(StructuralFakeBrowser()))
        required_status, required_payload = await invoke_router(
            application,
            path="/api/internal/zalo/structural-observation/one-shot",
        )
        old_status, _old_payload = await invoke_router(
            application,
            path="/api/internal/structural-observation/one-shot",
        )
        self.assertEqual(required_status, 410)
        self.assertEqual(required_payload["status"], "RETIRED")
        self.assertEqual(old_status, 404)


if __name__ == "__main__":
    unittest.main()
