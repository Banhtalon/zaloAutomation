"""Code-sealed, read-only Zalo auth-readiness observation.

The adapter is deliberately independent from application configuration.  It
observes one already-owned page and reports only bounded structural facts.  A
page is ready only when the expected host, one visible body ``nav`` and one
visible ``#contact-search-input`` are observed together on that same page.
Login/QR state is intentionally not inferred from absence.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlsplit


AUTH_READINESS_PHASE = "ZALO_AUTH_READINESS"
AUTH_READINESS_SELECTOR_CONTRACT_VERSION = "zalo-auth-readiness-v1"
AUTH_READINESS_EXPECTED_HOST = "chat.zalo.me"
AUTH_READINESS_SEARCH_SELECTOR = "#contact-search-input"
AUTH_READINESS_NAV_SELECTOR = "body nav"
AUTH_READINESS_TIMEOUT_SECONDS = 60.0
AUTH_READINESS_POLL_SECONDS = 0.25
AUTH_READINESS_OPERATION_TIMEOUT_SECONDS = 5.0
AUTH_READINESS_MAX_COUNT = 2

READY_REASON = "AUTHENTICATED_READY"
LOADING_REASON = "AUTH_READINESS_LOADING"
LOADING_TIMEOUT_REASON = "AUTH_READINESS_LOADING_TIMEOUT"
AMBIGUOUS_REASON = "AUTH_READINESS_AMBIGUOUS"
SELECTOR_UNSUPPORTED_REASON = "AUTH_READINESS_SELECTOR_UNSUPPORTED"
BROWSER_ERROR_REASON = "AUTH_READINESS_BROWSER_ERROR"
PAGE_CLOSED_REASON = "AUTH_READINESS_PAGE_CLOSED"


class _ReadinessOperationError(RuntimeError):
    """Internal typed operation failure without browser exception details."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _elapsed_ms(started: float) -> int:
    return min(
        int(AUTH_READINESS_TIMEOUT_SECONDS * 1000),
        max(0, int((monotonic() - started) * 1000)),
    )


def _safe_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ReadinessOperationError(SELECTOR_UNSUPPORTED_REASON)
    return min(AUTH_READINESS_MAX_COUNT, max(0, value))


def _safe_host(value: Any) -> bool | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return (urlsplit(value).hostname or "").casefold() == AUTH_READINESS_EXPECTED_HOST
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class ZaloAuthReadinessAdapter:
    """Observe the approved readiness conjunction on one page.

    ``timeout_seconds`` is a per-page budget, never shared between pages or
    cached across retries.  The production default is the approved 60-second
    deadline; smaller values are useful for hermetic tests.
    """

    timeout_seconds: float = AUTH_READINESS_TIMEOUT_SECONDS
    poll_seconds: float = AUTH_READINESS_POLL_SECONDS

    def __post_init__(self) -> None:
        self.timeout_seconds = float(self.timeout_seconds)
        self.poll_seconds = float(self.poll_seconds)
        if self.timeout_seconds <= 0 or self.timeout_seconds > AUTH_READINESS_TIMEOUT_SECONDS:
            raise ValueError("timeout_seconds exceeds auth-readiness bound")
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")

    async def __call__(self, page: Any) -> dict[str, object]:
        started = monotonic()
        deadline = started + self.timeout_seconds
        poll_count = 0
        last: dict[str, object] | None = None

        while True:
            poll_count += 1
            remaining = deadline - monotonic()
            if remaining <= 0:
                return self._timeout_result(
                    started,
                    poll_count,
                    last,
                )
            try:
                observed = await self._observe_once(
                    page,
                    started=started,
                    poll_count=poll_count,
                    remaining=remaining,
                )
            except asyncio.CancelledError:
                raise
            except _ReadinessOperationError as exc:
                return self._terminal_result(
                    started=started,
                    poll_count=poll_count,
                    expected_host=last.get("expected_host_reached") if last else None,
                    nav_count=last.get("body_nav_count", 0) if last else 0,
                    search_count=last.get("search_control_count", 0) if last else 0,
                    nav_visible=last.get("body_nav_visible") if last else None,
                    search_visible=last.get("search_control_visible") if last else None,
                    reason=exc.reason,
                    page_state="ERROR" if exc.reason == BROWSER_ERROR_REASON else "UNSUPPORTED",
                )

            last = observed
            reason = observed["diagnostic_code"]
            if reason in {
                READY_REASON,
                AMBIGUOUS_REASON,
                SELECTOR_UNSUPPORTED_REASON,
                BROWSER_ERROR_REASON,
                PAGE_CLOSED_REASON,
            }:
                return observed

            remaining = deadline - monotonic()
            if remaining <= 0:
                return self._timeout_result(started, poll_count, last)
            await asyncio.sleep(min(self.poll_seconds, remaining))

    async def _observe_once(
        self,
        page: Any,
        *,
        started: float,
        poll_count: int,
        remaining: float,
    ) -> dict[str, object]:
        if page is None:
            raise _ReadinessOperationError(BROWSER_ERROR_REASON)

        closed = await self._page_closed(page, remaining)
        if closed:
            return self._terminal_result(
                started=started,
                poll_count=poll_count,
                expected_host=None,
                nav_count=0,
                search_count=0,
                nav_visible=None,
                search_visible=None,
                reason=PAGE_CLOSED_REASON,
                page_state="CLOSED",
            )

        try:
            raw_url = getattr(page, "url", None)
        except Exception as exc:
            raise _ReadinessOperationError(BROWSER_ERROR_REASON) from exc
        if callable(raw_url):
            raw_url = await self._call(raw_url, remaining)
        expected_host = _safe_host(raw_url)
        base_kwargs: dict[str, Any] = {
            "started": started,
            "poll_count": poll_count,
            "expected_host": expected_host,
            "nav_count": 0,
            "search_count": 0,
            "nav_visible": None,
            "search_visible": None,
        }
        if expected_host is not True:
            return self._terminal_result(
                **base_kwargs,
                reason=LOADING_REASON,
                page_state="LOADING",
            )

        try:
            nav_locator = page.locator(AUTH_READINESS_NAV_SELECTOR)
            search_locator = page.locator(AUTH_READINESS_SEARCH_SELECTOR)
        except (AttributeError, TypeError) as exc:
            raise _ReadinessOperationError(SELECTOR_UNSUPPORTED_REASON) from exc
        except Exception as exc:
            raise _ReadinessOperationError(BROWSER_ERROR_REASON) from exc

        nav_count = _safe_count(await self._call(nav_locator.count, remaining))
        search_count = _safe_count(await self._call(search_locator.count, remaining))
        base_kwargs.update(nav_count=nav_count, search_count=search_count)
        if nav_count > 1 or search_count > 1:
            return self._terminal_result(
                **base_kwargs,
                reason=AMBIGUOUS_REASON,
                page_state="AMBIGUOUS",
            )

        nav_visible = (
            await self._visible(nav_locator, remaining) if nav_count == 1 else False
        )
        search_visible = (
            await self._visible(search_locator, remaining)
            if search_count == 1
            else False
        )
        base_kwargs.update(nav_visible=nav_visible, search_visible=search_visible)
        if nav_visible and search_visible:
            return self._terminal_result(
                **base_kwargs,
                reason=READY_REASON,
                page_state="READY",
            )
        return self._terminal_result(
            **base_kwargs,
            reason=LOADING_REASON,
            page_state="LOADING",
        )

    async def _page_closed(self, page: Any, remaining: float) -> bool:
        try:
            marker = getattr(page, "is_closed", None)
            if callable(marker):
                value = await self._call(marker, remaining)
            else:
                value = getattr(page, "closed", False)
        except _ReadinessOperationError:
            raise
        except Exception as exc:
            raise _ReadinessOperationError(BROWSER_ERROR_REASON) from exc
        if not isinstance(value, bool):
            raise _ReadinessOperationError(BROWSER_ERROR_REASON)
        return value

    async def _call(self, callback: Any, remaining: float) -> Any:
        if not callable(callback):
            raise _ReadinessOperationError(SELECTOR_UNSUPPORTED_REASON)
        try:
            value = callback()
            if hasattr(value, "__await__"):
                return await asyncio.wait_for(
                    value,
                    timeout=min(
                        AUTH_READINESS_OPERATION_TIMEOUT_SECONDS,
                        max(0.001, remaining),
                    ),
                )
            return value
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise _ReadinessOperationError(LOADING_TIMEOUT_REASON) from exc
        except (AttributeError, TypeError) as exc:
            raise _ReadinessOperationError(SELECTOR_UNSUPPORTED_REASON) from exc
        except Exception as exc:
            raise _ReadinessOperationError(BROWSER_ERROR_REASON) from exc

    async def _visible(self, locator: Any, remaining: float) -> bool:
        marker = getattr(locator, "is_visible", None)
        value = await self._call(marker, remaining)
        if not isinstance(value, bool):
            raise _ReadinessOperationError(SELECTOR_UNSUPPORTED_REASON)
        return value

    @staticmethod
    def _terminal_result(
        *,
        started: float,
        poll_count: int,
        expected_host: object,
        nav_count: object,
        search_count: object,
        nav_visible: object,
        search_visible: object,
        reason: str,
        page_state: str,
    ) -> dict[str, object]:
        safe_nav_count = (
            min(AUTH_READINESS_MAX_COUNT, max(0, nav_count))
            if isinstance(nav_count, int) and not isinstance(nav_count, bool)
            else 0
        )
        safe_search_count = min(
            AUTH_READINESS_MAX_COUNT,
            max(0, search_count)
            if isinstance(search_count, int) and not isinstance(search_count, bool)
            else 0,
        )
        safe_expected_host = (
            expected_host if isinstance(expected_host, bool) else None
        )
        safe_nav_visible = nav_visible if isinstance(nav_visible, bool) else None
        safe_search_visible = (
            search_visible if isinstance(search_visible, bool) else None
        )
        ready = reason == READY_REASON
        return {
            "phase": AUTH_READINESS_PHASE,
            "reason_code": reason,
            "diagnostic_code": reason,
            "selector_contract_version": AUTH_READINESS_SELECTOR_CONTRACT_VERSION,
            "elapsed_ms": _elapsed_ms(started),
            "poll_count": max(1, min(10_000, int(poll_count))),
            "status": "READY" if ready else page_state,
            "page_state": page_state,
            "expected_host_reached": safe_expected_host,
            # This compatibility field means the approved visible body-nav
            # shell evidence; it is not a QR/login assertion.
            "authenticated_shell_visible": safe_nav_visible,
            "body_nav_visible": safe_nav_visible,
            "body_nav_count": safe_nav_count,
            "search_control_visible": safe_search_visible,
            "search_control_count": safe_search_count,
            "login_or_qr_visible": None,
            "blocking_modal_visible": None,
            "unsupported_browser_visible": None,
            "network_error_visible": None,
        }

    def _timeout_result(
        self,
        started: float,
        poll_count: int,
        last: dict[str, object] | None,
    ) -> dict[str, object]:
        result = self._terminal_result(
            started=started,
            poll_count=poll_count,
            expected_host=last.get("expected_host_reached") if last else None,
            nav_count=last.get("body_nav_count", 0) if last else 0,
            search_count=last.get("search_control_count", 0) if last else 0,
            nav_visible=last.get("body_nav_visible") if last else None,
            search_visible=last.get("search_control_visible") if last else None,
            reason=LOADING_TIMEOUT_REASON,
            page_state="TIMEOUT",
        )
        result["elapsed_ms"] = min(
            int(AUTH_READINESS_TIMEOUT_SECONDS * 1000),
            max(0, int((monotonic() - started) * 1000)),
        )
        return result


__all__ = [
    "AUTH_READINESS_EXPECTED_HOST",
    "AUTH_READINESS_NAV_SELECTOR",
    "AUTH_READINESS_PHASE",
    "AUTH_READINESS_POLL_SECONDS",
    "AUTH_READINESS_SEARCH_SELECTOR",
    "AUTH_READINESS_SELECTOR_CONTRACT_VERSION",
    "AUTH_READINESS_TIMEOUT_SECONDS",
    "AMBIGUOUS_REASON",
    "BROWSER_ERROR_REASON",
    "LOADING_REASON",
    "LOADING_TIMEOUT_REASON",
    "PAGE_CLOSED_REASON",
    "READY_REASON",
    "SELECTOR_UNSUPPORTED_REASON",
    "ZaloAuthReadinessAdapter",
]
