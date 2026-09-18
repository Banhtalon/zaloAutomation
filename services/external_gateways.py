from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
import inspect
import json
import os
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
import weakref

import PIL.Image
from google import genai
from google.genai import types
from playwright.async_api import async_playwright

from googleform_autosubmit.browser_launcher import launch_chromium_with_fallback
from services.lms_plan import LmsSafetyError, normalize_class_codes
from services.gateway_resilience import GatewayCircuitBreaker
from services.zalo_auth_readiness import ZaloAuthReadinessAdapter


def _config_value(name: str, default: Any) -> Any:
    """Resolve legacy application defaults lazily at runtime.

    Importing the browser adapter is also the import path used by the
    independent diagnostic.  Config/dotenv must therefore remain outside that
    import path; the normal application composition still resolves the same
    values when a production gateway is constructed.
    """
    from googleform_autosubmit.config import Config

    return getattr(Config, name, default)


def _default_form_submitter_factory(mapping: dict, execution_mode: str | None = None) -> Any:
    """Construct the real form submitter only for the form capability."""
    from googleform_autosubmit.form_submitter import FormSubmitter

    return FormSubmitter(mapping, execution_mode=execution_mode)


def _default_student_ocr_factory() -> Any:
    """Construct the student OCR gateway only when the app requests it."""
    from googleform_autosubmit.ocr_processor import OCRProcessor

    return OCRProcessor()


class StudentOcrGateway(Protocol):
    async def process_file_async(self, image_path: str) -> list[dict]: ...


class LmsOcrGateway(Protocol):
    async def extract_missing_classes(self, image_path: str) -> list[str]: ...


class QualityIsolationViolation(RuntimeError):
    """Raised before a real production browser entry in isolated quality mode."""


class FormBatch(Protocol):
    async def submit_form(self, student_name: str, class_code: str) -> str | None: ...


class BrowserGateway(Protocol):
    def persistent_session(
        self,
        *,
        viewport: dict[str, int] | None = None,
        cleanup_timeout_seconds: float | None = None,
    ) -> Any: ...

    def persistent_page(
        self,
        *,
        viewport: dict[str, int] | None = None,
    ) -> Any: ...

    def probe_landing_selector_proposals(
        self,
        *,
        timeout_seconds: float = 30.0,
        telemetry: Callable[..., Any] | None = None,
    ) -> Any: ...

    def observe_structural_candidates(
        self,
        *,
        telemetry: Callable[..., Any] | None = None,
    ) -> Any: ...

    def form_batch(
        self,
        mapping: dict,
        execution_mode: str,
        *,
        headless: bool,
    ) -> Any: ...


LANDING_PHASE = "LANDING_AUTH_READINESS"
LANDING_SELECTOR_CONTRACT_VERSION = "zalo-landing-v1"
LANDING_EXPECTED_HOST = "chat.zalo.me"
LANDING_MAX_ELAPSED_MS = 10 * 60 * 1000
# The code-sealed auth adapter owns a 60-second per-page polling budget.  The
# gateway wrapper must not truncate that contract for production pages.
LANDING_PROVIDER_TIMEOUT_SECONDS = 60.0
LANDING_PROBE_PHASE = "LANDING_SELECTOR_PROPOSAL_PROBE"
LANDING_PROBE_SELECTOR_CONTRACT_VERSION = "zalo-landing-probe-v1"
LANDING_PROBE_EXPECTED_URL = f"https://{LANDING_EXPECTED_HOST}/"
LANDING_PROBE_NAVIGATION_TIMEOUT_MS = 5000
LANDING_PROBE_OPERATION_TIMEOUT_SECONDS = 5.0
LANDING_PROBE_TOTAL_TIMEOUT_SECONDS = 30.0
STRUCTURAL_OBSERVATION_PHASE = "LANDING_STRUCTURAL_OBSERVATION"
STRUCTURAL_OBSERVATION_SELECTOR_CONTRACT_VERSION = "zalo-landing-structural-v1"
STRUCTURAL_OBSERVATION_NAVIGATION_TIMEOUT_MS = 5000
STRUCTURAL_OBSERVATION_OPERATION_TIMEOUT_SECONDS = 5.0
STRUCTURAL_OBSERVATION_TOTAL_TIMEOUT_SECONDS = 30.0
STRUCTURAL_CANDIDATE_COUNT = 2
LANDING_PROBE_ACTION_KEYS = (
    "navigation",
    "search",
    "click",
    "type",
    "chat",
    "bcnb",
    "evidence",
)

LANDING_FACT_KEYS = frozenset(
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
    }
)

# The search control is the only selector contract proven by the current
# source.  The other facts remain unproven unless an explicitly injected,
# accepted sanitized probe supplies them; no selector is guessed here.
LANDING_FACT_SOURCE_BASIS = {
    "expected_host_reached": "proven current source contract",
    "authenticated_shell_visible": "unproven",
    "search_control_visible": "proven current source contract",
    "login_or_qr_visible": "unproven",
    "blocking_modal_visible": "unproven",
    "unsupported_browser_visible": "unproven",
    "network_error_visible": "unproven",
}

_LandingProbe = Callable[[Any], Any]


def _landing_elapsed_ms(started_at: float) -> int:
    return min(
        LANDING_MAX_ELAPSED_MS,
        max(0, int((asyncio.get_running_loop().time() - started_at) * 1000)),
    )


def _landing_page_state(diagnostic_code: str) -> str:
    return {
        "LANDING_PAGE_CLOSED": "CLOSED",
        "LANDING_BLOCKING_MODAL": "BLOCKING_MODAL",
        "LANDING_UNSUPPORTED_BROWSER": "UNSUPPORTED_BROWSER",
        "LANDING_NETWORK_FAILED": "NETWORK_ERROR",
        "LANDING_READINESS_TIMEOUT": "TIMEOUT",
        "AUTHENTICATED_READY": "READY",
    }.get(diagnostic_code, "LANDING")


class RuntimeLandingFactsProvider:
    """Collect bounded, sanitized auth-readiness facts for one landing page.

    The provider intentionally observes only the current search selector and
    explicit probe callbacks.  Authentication/login selectors are not guessed;
    without a proven callback their values are ``None`` and the resulting
    diagnostic is a typed live-contract blocker.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        authenticated_shell_probe: _LandingProbe | None = None,
        login_or_qr_probe: _LandingProbe | None = None,
        blocking_modal_probe: _LandingProbe | None = None,
        unsupported_browser_probe: _LandingProbe | None = None,
        network_error_probe: _LandingProbe | None = None,
    ) -> None:
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self.authenticated_shell_probe = authenticated_shell_probe
        self.login_or_qr_probe = login_or_qr_probe
        self.blocking_modal_probe = blocking_modal_probe
        self.unsupported_browser_probe = unsupported_browser_probe
        self.network_error_probe = network_error_probe

    async def __call__(self, page: Any) -> dict[str, object]:
        started_at = asyncio.get_running_loop().time()
        try:
            return await asyncio.wait_for(
                self._collect(page, started_at),
                timeout=self.timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            return self._facts(
                started_at=started_at,
                page_state="TIMEOUT",
                diagnostic_code="LANDING_READINESS_TIMEOUT",
            )
        except Exception:
            return self._facts(
                started_at=started_at,
                page_state="LANDING",
                diagnostic_code="LANDING_UNKNOWN_FAIL_CLOSED",
            )

    async def _collect(self, page: Any, started_at: float) -> dict[str, object]:
        if page is None:
            return self._facts(
                started_at=started_at,
                page_state="LANDING",
                diagnostic_code="LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN",
            )

        if await self._page_closed(page):
            return self._facts(
                started_at=started_at,
                page_state="CLOSED",
                diagnostic_code="LANDING_PAGE_CLOSED",
            )

        expected_host = await self._expected_host_reached(page)
        search_visible = await self._search_control_visible(page)
        authenticated_shell = await self._probe(
            self.authenticated_shell_probe,
            page,
        )
        login_or_qr = await self._probe(self.login_or_qr_probe, page)
        blocking_modal = await self._probe(self.blocking_modal_probe, page)
        unsupported_browser = await self._probe(
            self.unsupported_browser_probe,
            page,
        )
        network_error = await self._probe(self.network_error_probe, page)

        if expected_host is False:
            network_error = True

        if network_error is True:
            diagnostic_code = "LANDING_NETWORK_FAILED"
        elif unsupported_browser is True:
            diagnostic_code = "LANDING_UNSUPPORTED_BROWSER"
        elif blocking_modal is True:
            diagnostic_code = "LANDING_BLOCKING_MODAL"
        elif authenticated_shell is True and login_or_qr is True:
            diagnostic_code = "AUTH_STATE_AMBIGUOUS"
        elif login_or_qr is True:
            diagnostic_code = "LOGIN_OR_QR"
        elif authenticated_shell is None:
            diagnostic_code = "LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN"
        elif login_or_qr is None:
            diagnostic_code = "LIVE_LOGIN_SELECTOR_CONTRACT_NOT_PROVEN"
        elif authenticated_shell is True and search_visible is True:
            diagnostic_code = "AUTHENTICATED_READY"
        else:
            diagnostic_code = "LANDING_UNKNOWN_FAIL_CLOSED"

        return self._facts(
            started_at=started_at,
            page_state=_landing_page_state(diagnostic_code),
            expected_host_reached=expected_host,
            authenticated_shell_visible=authenticated_shell,
            search_control_visible=search_visible,
            login_or_qr_visible=login_or_qr,
            blocking_modal_visible=blocking_modal,
            unsupported_browser_visible=unsupported_browser,
            network_error_visible=network_error,
            diagnostic_code=diagnostic_code,
        )

    async def _bounded_call(self, callback: Callable[[], Any]) -> Any:
        value = callback()
        if inspect.isawaitable(value):
            return await asyncio.wait_for(
                value,
                timeout=self.timeout_seconds,
            )
        return value

    async def _page_closed(self, page: Any) -> bool:
        marker = getattr(page, "is_closed", None)
        if callable(marker):
            value = await self._bounded_call(marker)
        else:
            value = getattr(page, "closed", False)
        return value is True

    async def _expected_host_reached(self, page: Any) -> bool | None:
        raw_url = getattr(page, "url", None)
        if callable(raw_url):
            raw_url = await self._bounded_call(raw_url)
        if not isinstance(raw_url, str) or not raw_url:
            return None
        try:
            return urlsplit(raw_url).hostname == LANDING_EXPECTED_HOST
        except (TypeError, ValueError):
            return None

    async def _search_control_visible(self, page: Any) -> bool:
        locator = page.locator("#contact-search-input")
        count = await self._bounded_call(locator.count)
        if not isinstance(count, int):
            raise TypeError("invalid selector count")
        if count <= 0:
            return False
        is_visible = getattr(locator, "is_visible", None)
        if not callable(is_visible):
            raise TypeError("missing selector visibility operation")
        visible = await self._bounded_call(is_visible)
        if not isinstance(visible, bool):
            raise TypeError("invalid selector visibility")
        return visible

    async def _probe(self, probe: _LandingProbe | None, page: Any) -> bool | None:
        if probe is None:
            return None
        value = await self._bounded_call(lambda: probe(page))
        if value is None:
            return None
        if not isinstance(value, bool):
            raise TypeError("invalid landing fact")
        return value

    def _facts(
        self,
        *,
        started_at: float,
        page_state: str,
        diagnostic_code: str,
        expected_host_reached: bool | None = None,
        authenticated_shell_visible: bool | None = None,
        search_control_visible: bool | None = None,
        login_or_qr_visible: bool | None = None,
        blocking_modal_visible: bool | None = None,
        unsupported_browser_visible: bool | None = None,
        network_error_visible: bool | None = None,
    ) -> dict[str, object]:
        return {
            "phase": LANDING_PHASE,
            "elapsed_ms": _landing_elapsed_ms(started_at),
            "page_state": page_state,
            "expected_host_reached": expected_host_reached,
            "authenticated_shell_visible": authenticated_shell_visible,
            "search_control_visible": search_control_visible,
            "login_or_qr_visible": login_or_qr_visible,
            "blocking_modal_visible": blocking_modal_visible,
            "unsupported_browser_visible": unsupported_browser_visible,
            "network_error_visible": network_error_visible,
            "selector_contract_version": LANDING_SELECTOR_CONTRACT_VERSION,
            "diagnostic_code": diagnostic_code,
        }


LandingFactsProvider = RuntimeLandingFactsProvider


class LandingProbeProfileBinding(str, Enum):
    """Sanitized profile-binding states exposed by the internal probe."""

    WORKER_PERSISTENT_SESSION = "WORKER_PERSISTENT_SESSION"
    UNAVAILABLE = "UNAVAILABLE"


class LandingCandidateStatus(str, Enum):
    """Candidate vocabulary; positive outcomes need an approved contract."""

    CANDIDATE_PRESENT_WITH_LIVE_PROVENANCE = (
        "CANDIDATE_PRESENT_WITH_LIVE_PROVENANCE"
    )
    NO_CANDIDATE = "NO_CANDIDATE"
    UNPROVEN = "UNPROVEN"
    AMBIGUOUS = "AMBIGUOUS"
    LANDING_PROFILE_UNAVAILABLE = "LANDING_PROFILE_UNAVAILABLE"
    LANDING_CONTEXT_LOCKED = "LANDING_CONTEXT_LOCKED"
    LANDING_PAGE_CREATE_TIMEOUT = "LANDING_PAGE_CREATE_TIMEOUT"
    LANDING_PAGE_CREATE_FAILED = "LANDING_PAGE_CREATE_FAILED"
    LANDING_NAVIGATION_TIMEOUT = "LANDING_NAVIGATION_TIMEOUT"
    LANDING_NAVIGATION_FAILED = "LANDING_NAVIGATION_FAILED"
    LANDING_PAGE_CLOSED = "LANDING_PAGE_CLOSED"
    LANDING_EXPECTED_HOST_MISMATCH = "LANDING_EXPECTED_HOST_MISMATCH"
    LANDING_PAGE_DETACHED = "LANDING_PAGE_DETACHED"
    LANDING_CANDIDATE_PROBE_TIMEOUT = "LANDING_CANDIDATE_PROBE_TIMEOUT"
    LANDING_CANDIDATE_PROBE_FAILED = "LANDING_CANDIDATE_PROBE_FAILED"
    LANDING_CANDIDATE_PROVENANCE_INVALID = (
        "LANDING_CANDIDATE_PROVENANCE_INVALID"
    )
    LANDING_READINESS_TIMEOUT = "LANDING_READINESS_TIMEOUT"
    LANDING_CLEANUP_FAILED = "LANDING_CLEANUP_FAILED"
    LANDING_UNKNOWN_FAIL_CLOSED = "LANDING_UNKNOWN_FAIL_CLOSED"


class StructuralCandidateId(str, Enum):
    """Sealed candidate identifiers for the future reviewed observation."""

    AUTHENTICATED_SHELL = "AUTHENTICATED_SHELL"
    LOGIN_OR_QR = "LOGIN_OR_QR"


class StructuralCandidateStatus(str, Enum):
    """Fail-closed candidate outcomes safe for the structural schema."""

    UNPROVEN = "UNPROVEN"
    AMBIGUOUS = "AMBIGUOUS"
    NO_CANDIDATE = "NO_CANDIDATE"
    CANDIDATE_PRESENT_WITH_LIVE_PROVENANCE = (
        "CANDIDATE_PRESENT_WITH_LIVE_PROVENANCE"
    )
    STRUCTURAL_PAGESET_BLOCKED = "STRUCTURAL_PAGESET_BLOCKED"
    STRUCTURAL_PAGE_CLOSED = "STRUCTURAL_PAGE_CLOSED"
    STRUCTURAL_PAGE_DETACHED = "STRUCTURAL_PAGE_DETACHED"
    STRUCTURAL_OBSERVATION_TIMEOUT = "STRUCTURAL_OBSERVATION_TIMEOUT"
    STRUCTURAL_NAVIGATION_TIMEOUT = "STRUCTURAL_NAVIGATION_TIMEOUT"
    STRUCTURAL_NAVIGATION_FAILED = "STRUCTURAL_NAVIGATION_FAILED"
    STRUCTURAL_SEARCH_CONTROL_ABSENT = "STRUCTURAL_SEARCH_CONTROL_ABSENT"
    STRUCTURAL_UNKNOWN_FAIL_CLOSED = "STRUCTURAL_UNKNOWN_FAIL_CLOSED"
    STRUCTURAL_SESSION_UNAVAILABLE = "STRUCTURAL_SESSION_UNAVAILABLE"
    STRUCTURAL_CLEANUP_FAILED = "STRUCTURAL_CLEANUP_FAILED"
    STRUCTURAL_OBSERVATION_ALREADY_CONSUMED = (
        "STRUCTURAL_OBSERVATION_ALREADY_CONSUMED"
    )


class StructuralProvenanceCode(str, Enum):
    """Sanitized provenance vocabulary; no concrete selector is bound yet."""

    STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN = (
        "STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN"
    )
    STRUCTURAL_PAGESET_NOT_OBSERVED = "STRUCTURAL_PAGESET_NOT_OBSERVED"
    STRUCTURAL_PROVENANCE_NOT_VERIFIED = "STRUCTURAL_PROVENANCE_NOT_VERIFIED"
    STRUCTURAL_PROVENANCE_INVALID = "STRUCTURAL_PROVENANCE_INVALID"


# Deliberately no selector, attribute, text, or callback is carried by these
# definitions.  A later Owner-approved contract may replace the ``UNPROVEN``
# state after independent structural evidence exists.
_STRUCTURAL_CANDIDATE_DEFINITIONS = (
    (StructuralCandidateId.AUTHENTICATED_SHELL, StructuralCandidateStatus.UNPROVEN),
    (StructuralCandidateId.LOGIN_OR_QR, StructuralCandidateStatus.UNPROVEN),
)

# The current structural contract has no reviewed selector provenance.  Keep
# every candidate status permanently unproven; operational outcomes belong in
# the diagnostic field below and must never masquerade as candidate facts.
_STRUCTURAL_CANDIDATE_STATUS_ALLOWLIST = frozenset({"UNPROVEN"})

# Short aliases keep the future contract discoverable without exposing a raw
# selector or accepting a caller-supplied candidate definition.
StructuralObservationCandidateId = StructuralCandidateId
StructuralObservationCandidateStatus = StructuralCandidateStatus


class LandingProbeDetachedError(RuntimeError):
    """Typed synthetic signal for a detached landing locator/page."""


# Playwright versions and hermetic adapters do not share one public detached
# exception type.  Keep the structural error boundary narrow and classify only
# explicit typed signals or known detached type names.  The legacy message
# fallback is limited to the synthetic ``RuntimeError`` shape used by older
# fixtures; its text is never retained or emitted.
_LANDING_DETACHED_ERROR_NAMES = frozenset(
    {
        "LandingProbeDetachedError",
        "LocatorDetachedError",
        "PageDetachedError",
        "TargetClosedError",
    }
)


def _is_landing_probe_detached(exc: BaseException) -> bool:
    if isinstance(exc, LandingProbeDetachedError):
        return True
    if type(exc).__name__ in _LANDING_DETACHED_ERROR_NAMES:
        return True
    if isinstance(exc, RuntimeError):
        try:
            return "detached" in str(exc).casefold()
        except Exception:
            return False
    return False


# Short aliases make the sanitized enums convenient for programmatic callers.
ProfileBinding = LandingProbeProfileBinding
CandidateStatus = LandingCandidateStatus


def _landing_probe_action_counters(navigation: int = 0) -> dict[str, int]:
    """Return the fixed, sanitized action ledger for one probe invocation."""
    return {
        key: (navigation if key == "navigation" else 0)
        for key in LANDING_PROBE_ACTION_KEYS
    }


def _landing_probe_value(value: LandingCandidateStatus | str) -> str:
    if isinstance(value, LandingCandidateStatus):
        return value.value
    return str(value)


def _landing_probe_result(
    *,
    started_at: float,
    profile_binding: LandingProbeProfileBinding,
    expected_host_reached: bool | None,
    search_control_visible: bool | None,
    auth_shell_candidate_status: LandingCandidateStatus | str,
    login_or_qr_candidate_status: LandingCandidateStatus | str,
    diagnostic_code: LandingCandidateStatus | str,
    launch_count: int = 0,
    navigation_count: int = 0,
) -> dict[str, object]:
    """Create the exact result schema without retaining browser details."""
    safe_launch_count = min(1, max(0, int(launch_count)))
    return {
        "phase": LANDING_PROBE_PHASE,
        "elapsed_ms": min(
            LANDING_MAX_ELAPSED_MS,
            max(
                0,
                int((asyncio.get_running_loop().time() - started_at) * 1000),
            ),
        ),
        "profile_binding": profile_binding.value,
        "launch_count": safe_launch_count,
        "expected_host_reached": expected_host_reached,
        "search_control_visible": search_control_visible,
        "auth_shell_candidate_status": _landing_probe_value(
            auth_shell_candidate_status
        ),
        "login_or_qr_candidate_status": _landing_probe_value(
            login_or_qr_candidate_status
        ),
        "selector_contract_version": LANDING_PROBE_SELECTOR_CONTRACT_VERSION,
        "diagnostic_code": _landing_probe_value(diagnostic_code),
        "zero_action_counters": _landing_probe_action_counters(navigation_count),
    }


def _structural_value(value: Enum | str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _structural_candidates(
    *,
    status: StructuralCandidateStatus | str = StructuralCandidateStatus.UNPROVEN,
    provenance_code: StructuralProvenanceCode | str = (
        StructuralProvenanceCode.STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN
    ),
) -> list[dict[str, object]]:
    """Return the fixed two-candidate schema without selector-bearing data."""
    raw_status = _structural_value(status)
    safe_status = (
        raw_status
        if raw_status in _STRUCTURAL_CANDIDATE_STATUS_ALLOWLIST
        else "UNPROVEN"
    )
    safe_provenance = _structural_value(provenance_code)
    return [
        {
            "candidate_id": candidate_id.value,
            "status": safe_status,
            "match_count": None,
            "visible": None,
            "provenance_code": safe_provenance,
        }
        for candidate_id, _default_status in _STRUCTURAL_CANDIDATE_DEFINITIONS
    ]


def _structural_observation_result(
    *,
    started_at: float,
    profile_binding: LandingProbeProfileBinding,
    page_set_category: str | None,
    expected_host_reached: bool | None,
    search_control_visible: bool | None,
    diagnostic_code: StructuralCandidateStatus | str,
    candidate_status: StructuralCandidateStatus | str = (
        StructuralCandidateStatus.UNPROVEN
    ),
    provenance_code: StructuralProvenanceCode | str = (
        StructuralProvenanceCode.STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN
    ),
    navigation_count: int = 0,
) -> dict[str, object]:
    """Build the sealed, raw-selector-free structural observation result."""
    safe_navigation = min(1, max(0, int(navigation_count)))
    return {
        "phase": STRUCTURAL_OBSERVATION_PHASE,
        "elapsed_ms": min(
            LANDING_MAX_ELAPSED_MS,
            max(
                0,
                int((asyncio.get_running_loop().time() - started_at) * 1000),
            ),
        ),
        "profile_binding": profile_binding.value,
        "page_set_category": page_set_category,
        "navigation_count": safe_navigation,
        "candidate_count": STRUCTURAL_CANDIDATE_COUNT,
        "expected_host_reached": expected_host_reached,
        "search_control_visible": search_control_visible,
        "candidates": _structural_candidates(
            status=candidate_status,
            provenance_code=provenance_code,
        ),
        "selector_contract_version": STRUCTURAL_OBSERVATION_SELECTOR_CONTRACT_VERSION,
        "diagnostic_code": _structural_value(diagnostic_code),
        "zero_action_counters": _landing_probe_action_counters(safe_navigation),
    }


def _emit_landing_probe_telemetry(
    result: dict[str, object],
    telemetry: Callable[..., Any] | None,
) -> None:
    """Emit only the already-sanitized probe contract when requested."""
    if telemetry is None:
        return
    fields = dict(result)
    counters = fields.get("zero_action_counters")
    if isinstance(counters, dict):
        fields["zero_action_counters"] = dict(counters)
    try:
        telemetry(**fields)
    except Exception:
        # Diagnostics cannot turn a safe result into an untyped error.
        pass


def _emit_structural_observation_telemetry(
    result: dict[str, object],
    telemetry: Callable[..., Any] | None,
) -> None:
    """Emit only the fixed structural result fields when a sink is supplied."""
    if telemetry is None:
        return
    fields = dict(result)
    candidates = fields.get("candidates")
    if isinstance(candidates, list):
        fields["candidates"] = [
            dict(item) for item in candidates if isinstance(item, dict)
        ]
    counters = fields.get("zero_action_counters")
    if isinstance(counters, dict):
        fields["zero_action_counters"] = dict(counters)
    try:
        telemetry(**fields)
    except Exception:
        # Telemetry is advisory and cannot turn a safe observation into an
        # untyped failure.
        pass


class CleanupCode(str, Enum):
    """Stable, sanitized outcomes for persistent browser cleanup."""

    CLEANUP_OK = "CLEANUP_OK"
    CLEANUP_ALREADY_CLOSED = "CLEANUP_ALREADY_CLOSED"
    CLEANUP_SKIPPED_NON_OWNING_ABORT = "CLEANUP_SKIPPED_NON_OWNING_ABORT"
    CONTEXT_CLOSE_TIMEOUT = "CONTEXT_CLOSE_TIMEOUT"
    CONTEXT_CLOSE_ERROR = "CONTEXT_CLOSE_ERROR"
    PLAYWRIGHT_STOP_TIMEOUT = "PLAYWRIGHT_STOP_TIMEOUT"
    PLAYWRIGHT_STOP_ERROR = "PLAYWRIGHT_STOP_ERROR"
    CLEANUP_INCOMPLETE = "CLEANUP_INCOMPLETE"
    CLEANUP_INVARIANT_FAILED = "CLEANUP_INVARIANT_FAILED"


CleanupOutcome = CleanupCode

# String aliases keep the boundary convenient for persistence and callers that
# do not need to import the enum.
CLEANUP_OK = CleanupCode.CLEANUP_OK.value
CLEANUP_ALREADY_CLOSED = CleanupCode.CLEANUP_ALREADY_CLOSED.value
CLEANUP_SKIPPED_NON_OWNING_ABORT = (
    CleanupCode.CLEANUP_SKIPPED_NON_OWNING_ABORT.value
)
CONTEXT_CLOSE_TIMEOUT = CleanupCode.CONTEXT_CLOSE_TIMEOUT.value
CONTEXT_CLOSE_ERROR = CleanupCode.CONTEXT_CLOSE_ERROR.value
PLAYWRIGHT_STOP_TIMEOUT = CleanupCode.PLAYWRIGHT_STOP_TIMEOUT.value
PLAYWRIGHT_STOP_ERROR = CleanupCode.PLAYWRIGHT_STOP_ERROR.value
CLEANUP_INCOMPLETE = CleanupCode.CLEANUP_INCOMPLETE.value
CLEANUP_INVARIANT_FAILED = CleanupCode.CLEANUP_INVARIANT_FAILED.value

CLEANUP_TIMEOUT_SECONDS = 10.0
MAX_CLEANUP_ELAPSED_MS = 10 * 60 * 1000


class CleanupPhase(str, Enum):
    PAGE_CLOSE = "PAGE_CLOSE"
    CONTEXT_CLOSE = "CONTEXT_CLOSE"
    PLAYWRIGHT_STOP = "PLAYWRIGHT_STOP"
    FINAL = "FINAL"


class CleanupState(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"
    ALREADY_CLOSED = "ALREADY_CLOSED"
    ALREADY_STOPPED = "ALREADY_STOPPED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    INVARIANT_FAILED = "INVARIANT_FAILED"


class ContextAlreadyClosedError(RuntimeError):
    """Positive typed signal that the context was already closed."""


class PlaywrightAlreadyStoppedError(RuntimeError):
    """Positive typed signal that the Playwright owner already stopped."""


class PageAlreadyClosedError(RuntimeError):
    """Positive typed signal that a page was already closed."""


class CleanupDeadlineExceeded(TimeoutError):
    """The additive total cleanup deadline elapsed before an operation ended."""


# Compatibility aliases make the typed signals discoverable without accepting
# arbitrary exception text as an already-closed/stopped indication.
AlreadyClosedError = ContextAlreadyClosedError
AlreadyStoppedError = PlaywrightAlreadyStoppedError


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Sanitized cleanup result with no browser exception or DOM content."""

    code: CleanupCode
    remaining_pages: int = 0
    remaining_holders: int = 0
    page_state: CleanupState = CleanupState.CLOSED
    context_state: CleanupState = CleanupState.CLOSED
    playwright_state: CleanupState = CleanupState.STOPPED
    elapsed_ms: int = 0
    invariant_ok: bool = True
    cleanup_incomplete: bool = False

    @property
    def result(self) -> str:
        return self.code.value

    @property
    def error_code(self) -> str:
        return self.code.value


class PageSetThreatModel(str, Enum):
    """Declared threat model for page-set lifecycle classification.

    ``COOPERATIVE_CALLER_ONLY`` is a bounded internal correctness contract. It
    is not a defense against same-process introspection, monkey-patching, or
    malicious code that deliberately accesses implementation internals. It is
    not security proof, auth proof, identity proof, or readiness proof. It is
    not permission to take browser action.
    """

    COOPERATIVE_CALLER_ONLY = "COOPERATIVE_CALLER_ONLY"


PAGESET_THREAT_MODEL: PageSetThreatModel = (
    PageSetThreatModel.COOPERATIVE_CALLER_ONLY
)


class PageSetCategory(str, Enum):
    """Finite, sanitized states for a cooperative internal page-set check."""

    PAGESET_UNAVAILABLE = "PAGESET_UNAVAILABLE"
    PAGESET_PREEXISTING_ZERO = "PAGESET_PREEXISTING_ZERO"
    PAGESET_PREEXISTING_ONE = "PAGESET_PREEXISTING_ONE"
    PAGESET_PREEXISTING_MANY = "PAGESET_PREEXISTING_MANY"
    PAGESET_OBSERVER_ONLY = "PAGESET_OBSERVER_ONLY"
    PAGESET_POSTCREATE_MISMATCH = "PAGESET_POSTCREATE_MISMATCH"


class PageSetCount(str, Enum):
    """Bounded page-count buckets exposed by the classifier."""

    ZERO = "0"
    ONE = "1"
    TWO_PLUS = "2PLUS"


class PageSetAction(str, Enum):
    """Finite action for a cooperative internal lifecycle transition.

    ``OBSERVE_OWNED_PAGE`` is not security proof, auth proof, identity proof,
    readiness proof, or permission to take browser action.
    """

    STOP_WITHOUT_OBSERVATION = "STOP_WITHOUT_OBSERVATION"
    OBSERVE_OWNED_PAGE = "OBSERVE_OWNED_PAGE"


class PageSetOwnership(str, Enum):
    """Explicit ownership disposition for the non-owning abort path."""

    NON_OWNING_ABORT = "NON_OWNING_ABORT"


# Short aliases make the contract discoverable without creating a second
# vocabulary.  They do not expose a raw context/page handle.
PageSetState = PageSetCategory
PageSetCountBucket = PageSetCount
PageSetDecision = PageSetAction


@dataclass(frozen=True, slots=True, weakref_slot=True)
class PageSetClassification:
    """Sanitized cooperative lifecycle signal with no page identity/content.

    ``PAGESET_OBSERVER_ONLY`` denotes a cooperative internal lifecycle
    transition. It is not security proof, auth proof, identity proof, readiness
    proof, or permission to take browser action.
    """

    category: PageSetCategory
    count: PageSetCount | None
    action: PageSetAction

    def __post_init__(self) -> None:
        if not isinstance(self.category, PageSetCategory):
            raise TypeError("category must be PageSetCategory")
        if self.count is not None and not isinstance(self.count, PageSetCount):
            raise TypeError("count must be PageSetCount or None")
        if not isinstance(self.action, PageSetAction):
            raise TypeError("action must be PageSetAction")
        expected = {
            PageSetCategory.PAGESET_UNAVAILABLE: None,
            PageSetCategory.PAGESET_PREEXISTING_ZERO: PageSetCount.ZERO,
            PageSetCategory.PAGESET_PREEXISTING_ONE: PageSetCount.ONE,
            PageSetCategory.PAGESET_PREEXISTING_MANY: PageSetCount.TWO_PLUS,
            PageSetCategory.PAGESET_OBSERVER_ONLY: PageSetCount.ONE,
        }
        required_count = expected.get(self.category, self.count)
        if self.count is not required_count:
            raise ValueError("category/count mismatch")
        required_action = (
            PageSetAction.OBSERVE_OWNED_PAGE
            if self.category is PageSetCategory.PAGESET_OBSERVER_ONLY
            else PageSetAction.STOP_WITHOUT_OBSERVATION
        )
        if self.action is not required_action:
            raise ValueError("category/action mismatch")

    @property
    def state(self) -> PageSetCategory:
        return self.category

    @property
    def count_bucket(self) -> PageSetCount | None:
        return self.count

    @property
    def stop_action(self) -> PageSetAction:
        return self.action

    @property
    def is_observer_only(self) -> bool:
        return self.category is PageSetCategory.PAGESET_OBSERVER_ONLY

    def as_dict(self) -> dict[str, str | None]:
        return {
            "category": self.category.value,
            "count": self.count.value if self.count is not None else None,
            "action": self.action.value,
        }


@dataclass(frozen=True, slots=True)
class PageSetAbortResult:
    """Auditable result for an ownership-preserving, non-owning abort."""

    classification: PageSetClassification
    ownership: PageSetOwnership = PageSetOwnership.NON_OWNING_ABORT
    cleanup_performed: bool = False
    page_close_count: int = 0
    context_close_count: int = 0
    playwright_stop_count: int = 0

    @property
    def category(self) -> PageSetCategory:
        return self.classification.category

    @property
    def count(self) -> PageSetCount | None:
        return self.classification.count

    @property
    def action(self) -> PageSetAction:
        return self.classification.action

    @property
    def zero_close_counts(self) -> dict[str, int]:
        return {
            "page": self.page_close_count,
            "context": self.context_close_count,
            "playwright": self.playwright_stop_count,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            **self.classification.as_dict(),
            "ownership": self.ownership.value,
            "cleanup_performed": self.cleanup_performed,
            "page_close_count": self.page_close_count,
            "context_close_count": self.context_close_count,
            "playwright_stop_count": self.playwright_stop_count,
        }


def _page_set_from_count(
    count: PageSetCount,
    *,
    observer_only: bool = False,
    mismatch: bool = False,
) -> PageSetClassification:
    if observer_only:
        return PageSetClassification(
            category=PageSetCategory.PAGESET_OBSERVER_ONLY,
            count=PageSetCount.ONE,
            action=PageSetAction.OBSERVE_OWNED_PAGE,
        )
    if mismatch:
        category = PageSetCategory.PAGESET_POSTCREATE_MISMATCH
    elif count is PageSetCount.ZERO:
        category = PageSetCategory.PAGESET_PREEXISTING_ZERO
    elif count is PageSetCount.ONE:
        category = PageSetCategory.PAGESET_PREEXISTING_ONE
    else:
        category = PageSetCategory.PAGESET_PREEXISTING_MANY
    return PageSetClassification(
        category=category,
        count=count,
        action=PageSetAction.STOP_WITHOUT_OBSERVATION,
    )


def _weak_context_ref(context: Any | None) -> weakref.ReferenceType[Any] | None:
    """Keep cooperative context continuity without a strong reference.

    This supports an internal lifecycle consistency check only; it does not
    establish a security, auth, identity, readiness, or permission boundary.
    """
    if context is None:
        return None
    try:
        return weakref.ref(context)
    except TypeError:
        return None


def _make_page_set_classifier() -> type:
    """Build a classifier with cooperative transition state outside results."""
    pending_contexts: weakref.WeakKeyDictionary[
        Any,
        weakref.ReferenceType[Any],
    ] = weakref.WeakKeyDictionary()

    class _PageSetClassifier:
        """Classify page-set count and explicit observer ownership cooperatively.

        The classifier reads the context's ``pages`` collection and, for the
        one-page observer-only case, compares that sole object by identity with
        the explicitly supplied observer-owned page. It never reads a page
        facade, title, URL, DOM, text, or selector and never mutates a
        resource. The pre-create state lives only in the closure-owned weak
        table and is consumed by the documented after-create call. This is a
        cooperative internal lifecycle contract, not a security boundary or
        proof of auth, identity, or permission.
        """

        __slots__ = ("__weakref__",)

        _MISSING = object()

        def __copy__(self) -> Any:
            """Do not let a shallow copy inherit the pending transition."""
            return type(self)()

        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            """Do not let a deep copy inherit the pending transition."""
            del memo
            return type(self)()

        def classify(
            self,
            context: Any,
            *,
            observer_owned_page: Any | None = None,
            observer_page: Any | None = None,
            post_create: bool = False,
        ) -> PageSetClassification:
            """Return one finite, sanitized classification for cooperative use.

            A zero-page call records a classifier-controlled pending transition.
            Observer-only classification is available only through the same
            classifier's subsequent call; the public API accepts no snapshot or
            detached context reference as authority. This sequence is not security,
            auth, identity, readiness, or permission proof.
            """
            if observer_page is not None:
                if observer_owned_page is not None:
                    raise TypeError("observer page ownership supplied twice")
                observer_owned_page = observer_page
            if not isinstance(post_create, bool):
                raise TypeError("post_create must be a boolean")

            current_count, sole_page = self._read_count(context)
            if current_count is None:
                pending_contexts.pop(self, None)
                return PageSetClassification(
                    category=PageSetCategory.PAGESET_UNAVAILABLE,
                    count=None,
                    action=PageSetAction.STOP_WITHOUT_OBSERVATION,
                )

            if observer_owned_page is None and not post_create:
                result = _page_set_from_count(current_count)
                if current_count is PageSetCount.ZERO:
                    context_ref = _weak_context_ref(context)
                    if context_ref is None:
                        pending_contexts.pop(self, None)
                    else:
                        pending_contexts[self] = context_ref
                else:
                    pending_contexts.pop(self, None)
                return result

            context_ref = pending_contexts.pop(self, None)
            same_context = False
            if context_ref is not None:
                try:
                    same_context = context_ref() is context
                except BaseException:
                    same_context = False
            if (
                current_count is PageSetCount.ONE
                and observer_owned_page is not None
                and same_context
                and sole_page is observer_owned_page
            ):
                return _page_set_from_count(
                    current_count,
                    observer_only=True,
                )

            return _page_set_from_count(current_count, mismatch=True)

        def classify_before_create(self, context: Any) -> PageSetClassification:
            """Record a cooperative pre-create lifecycle state.

            The returned sanitized value is informational and is not a security
            or ownership proof.
            """
            return self.classify(context)

        def classify_after_create(
            self,
            context: Any,
            observer_owned_page: Any,
        ) -> PageSetClassification:
            """Classify one explicit observer page after a cooperative call.

            This method intentionally accepts only the context and explicit
            observer page. An extra public snapshot or detached context
            reference cannot be supplied as authority. ``PAGESET_OBSERVER_ONLY``
            is an internal
            lifecycle transition, not security/auth/identity/readiness proof or
            permission to take browser action.
            """
            return self.classify(
                context,
                observer_owned_page=observer_owned_page,
                post_create=True,
            )

        classify_post_create = classify_after_create

        @classmethod
        def _read_count(
            cls,
            context: Any,
        ) -> tuple[PageSetCount | None, Any | None]:
            if context is None:
                return None, None
            try:
                pages = cast(Any, getattr(context, "pages", cls._MISSING))
                if pages is cls._MISSING or pages is None:
                    return None, None
                if isinstance(pages, (str, bytes, bytearray, dict, set, frozenset)):
                    return None, None
                raw_count = len(pages)
                if isinstance(raw_count, bool) or not isinstance(raw_count, int):
                    return None, None
                if raw_count < 0:
                    return None, None
                if raw_count == 0:
                    return PageSetCount.ZERO, None
                if raw_count > 1:
                    return PageSetCount.TWO_PLUS, None
                try:
                    sole_page = pages[0]
                except (IndexError, KeyError, TypeError, AttributeError):
                    try:
                        sole_page = next(iter(pages))
                    except (StopIteration, TypeError):
                        return None, None
                return PageSetCount.ONE, sole_page
            except BaseException:
                return None, None

    _PageSetClassifier.__name__ = "PageSetClassifier"
    _PageSetClassifier.__qualname__ = "PageSetClassifier"
    return _PageSetClassifier


PageSetClassifier = _make_page_set_classifier()


# The longer name documents the persistent-context boundary while preserving
# one implementation and one finite vocabulary.
PersistentContextPageSetClassifier = PageSetClassifier
PageSetClassifierResult = PageSetClassification


def classify_persistent_context_page_set(
    context: Any,
    *,
    observer_owned_page: Any | None = None,
    observer_page: Any | None = None,
    post_create: bool = False,
) -> PageSetClassification:
    """Perform one cooperative, local/hermetic page-set classification."""
    return PageSetClassifier().classify(
        context,
        observer_owned_page=observer_owned_page,
        observer_page=observer_page,
        post_create=post_create,
    )


classify_page_set = classify_persistent_context_page_set


def _safe_resource_marker(resource: Any, names: tuple[str, ...]) -> bool | None:
    """Read only explicit boolean lifecycle markers from a synthetic resource."""
    for name in names:
        marker = getattr(resource, name, None)
        if isinstance(marker, bool):
            return marker
        if callable(marker):
            if inspect.iscoroutinefunction(marker):
                return None
            try:
                value = marker()
            except Exception:
                return None
            if inspect.isawaitable(value):
                return None
            if isinstance(value, bool):
                return value
    return None


def _typed_already_closed(resource: Any, error: BaseException) -> bool:
    if not isinstance(error, (ContextAlreadyClosedError, PageAlreadyClosedError)):
        return False
    marker = _safe_resource_marker(resource, ("closed", "is_closed"))
    return marker is True


def _typed_already_stopped(resource: Any, error: BaseException) -> bool:
    if not isinstance(error, PlaywrightAlreadyStoppedError):
        return False
    marker = _safe_resource_marker(resource, ("stopped", "is_stopped"))
    return marker is True


def _cleanup_error_code(
    error: BaseException,
    *,
    timeout_code: CleanupCode,
    error_code: CleanupCode,
) -> CleanupCode:
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return timeout_code
    return error_code


def _bounded_elapsed_ms(started_at: float) -> int:
    return min(
        MAX_CLEANUP_ELAPSED_MS,
        max(0, int((asyncio.get_running_loop().time() - started_at) * 1000)),
    )


def _emit_cleanup_telemetry(
    *,
    phase: CleanupPhase,
    started_at: float,
    code: CleanupCode,
    state: CleanupState,
    telemetry: Callable[..., Any] | None = None,
) -> None:
    """Emit only typed cleanup fields and safe lifecycle enums."""
    fields = {
        "event": "browser.cleanup",
        "phase": phase.value,
        "elapsed_ms": _bounded_elapsed_ms(started_at),
        "error_code": code.value,
        "state": state.value,
    }
    try:
        if telemetry is not None:
            telemetry(**fields)
        else:
            # Importantly, the fixed message contains no exception or resource
            # detail; all variable fields above are bounded enums/integers.
            from logger_utils import log_message

            cast(Any, log_message)("Browser cleanup telemetry", **fields)
    except Exception:
        # Telemetry cannot turn a fail-closed cleanup result into an untyped
        # exception and must never retain the original browser error.
        pass


async def _call_cleanup_operation(
    operation: Callable[[], Awaitable[Any]],
    *,
    timeout_seconds: float,
    deadline: float | None = None,
) -> Any:
    timeout = max(0.001, float(timeout_seconds))
    if deadline is not None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise CleanupDeadlineExceeded
        timeout = min(timeout, remaining)
    try:
        return await asyncio.wait_for(
            operation(),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, TimeoutError):
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            raise CleanupDeadlineExceeded from None
        raise


class _SharedBrowserFormBatch:
    def __init__(self, submitter: Any, browser: Any):
        self._submitter = submitter
        self._browser = browser

    async def submit_form(self, student_name: str, class_code: str) -> str | None:
        return await self._submitter.submit_form(
            student_name,
            class_code,
            browser=self._browser,
        )


class PersistentBrowserSession:
    """One persistent context that owns independent Playwright pages.

    Cleanup is intentionally owned by this object so repeated callers cannot
    close a page, context, or Playwright owner twice.  It never creates a
    replacement resource after cleanup starts.
    """

    def __init__(
        self,
        context: Any,
        *,
        landing_facts_provider: Callable[..., Any] | None = None,
        authenticated_shell_probe: _LandingProbe | None = None,
        login_or_qr_probe: _LandingProbe | None = None,
        blocking_modal_probe: _LandingProbe | None = None,
        unsupported_browser_probe: _LandingProbe | None = None,
        network_error_probe: _LandingProbe | None = None,
    ):
        self.context = context
        self._pages: list[Any] = []
        self._page_attempts: set[int] = set()
        self._page_close_failure: CleanupCode | None = None
        self._page_already_closed = False
        self._cleanup_lock = asyncio.Lock()
        self._cleanup_result: CleanupResult | None = None
        self._observer_cleanup_result: CleanupResult | None = None
        self._cleanup_started = False
        self._structural_observation_started = False
        self._structural_observation_result: dict[str, object] | None = None
        self._page_set_classifier = PageSetClassifier()
        self._page_set_abort_result: PageSetAbortResult | None = None
        self._landing_facts_source = landing_facts_provider
        self._landing_facts_runtime_provider = RuntimeLandingFactsProvider(
            authenticated_shell_probe=authenticated_shell_probe,
            login_or_qr_probe=login_or_qr_probe,
            blocking_modal_probe=blocking_modal_probe,
            unsupported_browser_probe=unsupported_browser_probe,
            network_error_probe=network_error_probe,
        )

    def classify_page_set(
        self,
        *,
        observer_owned_page: Any | None = None,
        observer_page: Any | None = None,
        post_create: bool = False,
    ) -> PageSetClassification:
        """Classify this context for cooperative lifecycle use only.

        Classification does not touch a page facade. Observer-only remains an
        internal lifecycle transition, not security/auth/identity/readiness
        proof or permission to take browser action.
        """
        return self._page_set_classifier.classify(
            self.context,
            observer_owned_page=observer_owned_page,
            observer_page=observer_page,
            post_create=post_create,
        )

    classify_persistent_context_page_set = classify_page_set

    @property
    def page_set_abort_result(self) -> PageSetAbortResult | None:
        return self._page_set_abort_result

    async def abort_without_observation(
        self,
        classification: PageSetClassification,
        *,
        telemetry: Callable[..., Any] | None = None,
    ) -> PageSetAbortResult:
        """Retain resources and stop before observation for a cooperative caller.

        This is an explicit ownership-preserving handoff marker.  It does not
        close a page, close the context, stop Playwright, inspect a page
        facade, or invoke any browser operation.  Once selected, normal
        session cleanup is intentionally skipped and reported with a typed
        result; normal owned-session cleanup is unchanged until this method is
        explicitly called. The marker is not security/auth/identity/readiness
        proof or permission to take browser action.
        """
        if not isinstance(classification, PageSetClassification):
            raise TypeError("classification must be PageSetClassification")
        if classification.category is PageSetCategory.PAGESET_OBSERVER_ONLY:
            raise ValueError("observer-only state is not an abort state")

        async with self._cleanup_lock:
            if self._page_set_abort_result is not None:
                if self._page_set_abort_result.classification != classification:
                    raise ValueError("page-set abort classification changed")
                return self._page_set_abort_result
            if self._cleanup_result is not None or self._cleanup_started:
                raise RuntimeError("persistent browser session is already closed")

            result = PageSetAbortResult(classification=classification)
            self._page_set_abort_result = result
            self._cleanup_started = True
            self._cleanup_result = CleanupResult(
                code=CleanupCode.CLEANUP_SKIPPED_NON_OWNING_ABORT,
                remaining_pages=len(self._pages),
                remaining_holders=len(self._pages),
                page_state=CleanupState.OPEN,
                context_state=CleanupState.OPEN,
                playwright_state=CleanupState.OPEN,
                elapsed_ms=0,
                invariant_ok=False,
            )

        if telemetry is not None:
            try:
                telemetry(**result.as_dict())
            except Exception:
                pass
        return result

    # Explicit aliases keep the stop boundary discoverable as a lifecycle API;
    # they are not a security boundary or browser-action authorization.
    abort_page_set_without_observation = abort_without_observation
    abort_non_owning = abort_without_observation

    async def landing_facts_provider(self, page: Any | None = None) -> Any:
        """Provide one bounded, sanitized landing observation for setup.

        The explicit source callback is reserved for accepted/synthetic fact
        evidence.  The default runtime provider observes the current page and
        never creates a page, context, browser, or profile.
        """
        candidate = page
        if candidate is None and self._pages:
            candidate = self._pages[-1]
        if self._landing_facts_source is None:
            return await self._landing_facts_runtime_provider(candidate)
        source = self._landing_facts_source
        try:
            signature = inspect.signature(source)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            positional = [
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                }
            ]
            value = source() if not positional else source(candidate)
        else:
            value = source(candidate)
        if inspect.isawaitable(value):
            return await asyncio.wait_for(
                value,
                timeout=LANDING_PROVIDER_TIMEOUT_SECONDS,
            )
        return value

    async def _probe_bounded_call(
        self,
        callback: Callable[[], Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        value = callback()
        if inspect.isawaitable(value):
            return await asyncio.wait_for(value, timeout=timeout_seconds)
        return value

    async def _probe_page_closed(
        self,
        page: Any,
        *,
        timeout_seconds: float,
    ) -> bool:
        marker = getattr(page, "is_closed", None)
        if callable(marker):
            value = await self._probe_bounded_call(
                marker,
                timeout_seconds=timeout_seconds,
            )
        else:
            value = getattr(page, "closed", False)
        return value is True

    async def _probe_search_control_visible(
        self,
        page: Any,
        *,
        timeout_seconds: float,
    ) -> bool:
        """Observe only the one selector already proven by source."""
        locator = page.locator("#contact-search-input")
        count = await self._probe_bounded_call(
            locator.count,
            timeout_seconds=timeout_seconds,
        )
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("invalid selector count")
        if count <= 0:
            return False
        visible = getattr(locator, "is_visible", None)
        if not callable(visible):
            raise TypeError("missing selector visibility operation")
        result = await self._probe_bounded_call(
            visible,
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(result, bool):
            raise TypeError("invalid selector visibility")
        return result

    async def observe_structural_candidates(
        self,
        *,
        telemetry: Callable[..., Any] | None = None,
    ) -> dict[str, object]:
        """Observe the sealed auth/login candidate schema once on this session.

        This is a separate, read-only structural boundary.  The current source
        has no concrete authenticated-shell or login/QR selector contract, so
        both candidate records intentionally remain ``UNPROVEN`` with no count
        or visibility claim.  The method accepts no page, context, selector, or
        candidate callback and never creates a browser or persistent context.
        When used, it owns only the page created through this already-bound
        session and leaves context/Playwright ownership to the caller.
        """
        started_at = asyncio.get_running_loop().time()
        if self._structural_observation_started:
            if self._structural_observation_result is not None:
                cached_result = dict(self._structural_observation_result)
            else:
                cached_result = _structural_observation_result(
                    started_at=started_at,
                    profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                    page_set_category=None,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_ALREADY_CONSUMED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_ALREADY_CONSUMED,
                    provenance_code=StructuralProvenanceCode.STRUCTURAL_PROVENANCE_INVALID,
                )
            _emit_structural_observation_telemetry(cached_result, telemetry)
            return cached_result
        self._structural_observation_started = True

        page_holder: list[Any] = [None]
        observer_owned = False
        navigation_count = 0
        result: dict[str, object] | None = None
        operation_timeout = STRUCTURAL_OBSERVATION_OPERATION_TIMEOUT_SECONDS

        def make_result(
            *,
            page_set_category: str | None,
            expected_host_reached: bool | None,
            search_control_visible: bool | None,
            diagnostic_code: StructuralCandidateStatus | str,
            candidate_status: StructuralCandidateStatus | str = (
                StructuralCandidateStatus.UNPROVEN
            ),
            provenance_code: StructuralProvenanceCode | str = (
                StructuralProvenanceCode.STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN
            ),
        ) -> dict[str, object]:
            return _structural_observation_result(
                started_at=started_at,
                profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                page_set_category=page_set_category,
                expected_host_reached=expected_host_reached,
                search_control_visible=search_control_visible,
                diagnostic_code=diagnostic_code,
                candidate_status=candidate_status,
                provenance_code=provenance_code,
                navigation_count=navigation_count,
            )

        async def close_owned_observer_page() -> None:
            """Close only the page proven owned by this observation."""
            nonlocal observer_owned
            if not observer_owned or page_holder[0] is None:
                return
            observer_owned = False
            close_task = asyncio.create_task(
                asyncio.wait_for(
                    self.close_page(page_holder[0]),
                    timeout=operation_timeout,
                )
            )
            cancellation_seen = False
            while not close_task.done():
                try:
                    await asyncio.shield(close_task)
                except asyncio.CancelledError:
                    cancellation_seen = True
                    continue
            try:
                close_task.result()
            except BaseException:
                raise
            if cancellation_seen:
                raise asyncio.CancelledError

        preclassification = self.classify_page_set()
        if preclassification.category is not PageSetCategory.PAGESET_PREEXISTING_ZERO:
            result = make_result(
                page_set_category=preclassification.category.value,
                expected_host_reached=None,
                search_control_visible=None,
                diagnostic_code=preclassification.category.value,
                candidate_status=StructuralCandidateStatus.STRUCTURAL_PAGESET_BLOCKED,
                provenance_code=StructuralProvenanceCode.STRUCTURAL_PAGESET_NOT_OBSERVED,
            )
            self._structural_observation_result = result
            _emit_structural_observation_telemetry(result, telemetry)
            return result

        async def run_observation() -> dict[str, object]:
            nonlocal observer_owned, navigation_count
            try:
                current_page = await self._probe_bounded_call(
                    self._probe_new_observer_page,
                    timeout_seconds=operation_timeout,
                )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                )
            except Exception:
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                )

            if current_page is None:
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                )
            page_holder[0] = current_page

            try:
                postclassification = self._page_set_classifier.classify_after_create(
                    self.context,
                    current_page,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return make_result(
                    page_set_category="PAGESET_POSTCREATE_MISMATCH",
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_PAGESET_BLOCKED,
                    provenance_code=StructuralProvenanceCode.STRUCTURAL_PAGESET_NOT_OBSERVED,
                )

            if not postclassification.is_observer_only:
                return make_result(
                    page_set_category=postclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=postclassification.category.value,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_PAGESET_BLOCKED,
                    provenance_code=StructuralProvenanceCode.STRUCTURAL_PAGESET_NOT_OBSERVED,
                )
            observer_owned = True

            try:
                if await self._probe_page_closed(
                    current_page,
                    timeout_seconds=operation_timeout,
                ):
                    try:
                        await close_owned_observer_page()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        return make_result(
                            page_set_category=preclassification.category.value,
                            expected_host_reached=None,
                            search_control_visible=None,
                            diagnostic_code=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                            candidate_status=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                        )
                    return make_result(
                        page_set_category=preclassification.category.value,
                        expected_host_reached=None,
                        search_control_visible=None,
                        diagnostic_code=StructuralCandidateStatus.STRUCTURAL_PAGE_CLOSED,
                        candidate_status=StructuralCandidateStatus.STRUCTURAL_PAGE_CLOSED,
                    )
            except asyncio.CancelledError:
                try:
                    await close_owned_observer_page()
                except BaseException:
                    pass
                raise
            except (asyncio.TimeoutError, TimeoutError):
                try:
                    await close_owned_observer_page()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return make_result(
                        page_set_category=preclassification.category.value,
                        expected_host_reached=None,
                        search_control_visible=None,
                        diagnostic_code=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                        candidate_status=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                    )
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                )
            except Exception:
                try:
                    await close_owned_observer_page()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return make_result(
                        page_set_category=preclassification.category.value,
                        expected_host_reached=None,
                        search_control_visible=None,
                        diagnostic_code=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                        candidate_status=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                    )
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                )

            goto = getattr(current_page, "goto", None)
            if not callable(goto):
                try:
                    await close_owned_observer_page()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return make_result(
                        page_set_category=preclassification.category.value,
                        expected_host_reached=False,
                        search_control_visible=None,
                        diagnostic_code=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                        candidate_status=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                    )
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=False,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_NAVIGATION_FAILED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_NAVIGATION_FAILED,
                )
            navigation_count = 1
            try:
                await self._probe_bounded_call(
                    lambda: goto(
                        LANDING_PROBE_EXPECTED_URL,
                        timeout=STRUCTURAL_OBSERVATION_NAVIGATION_TIMEOUT_MS,
                    ),
                    timeout_seconds=operation_timeout,
                )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=False,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_NAVIGATION_TIMEOUT,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_NAVIGATION_TIMEOUT,
                )
            except Exception:
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=False,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_NAVIGATION_FAILED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_NAVIGATION_FAILED,
                )

            try:
                if await self._probe_page_closed(
                    current_page,
                    timeout_seconds=operation_timeout,
                ):
                    return make_result(
                        page_set_category=preclassification.category.value,
                        expected_host_reached=None,
                        search_control_visible=None,
                        diagnostic_code=StructuralCandidateStatus.STRUCTURAL_PAGE_CLOSED,
                        candidate_status=StructuralCandidateStatus.STRUCTURAL_PAGE_CLOSED,
                    )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                )
            except Exception:
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                )

            try:
                raw_url = getattr(current_page, "url", None)
                if callable(raw_url):
                    raw_url = await self._probe_bounded_call(
                        raw_url,
                        timeout_seconds=operation_timeout,
                    )
                expected_host_reached = (
                    isinstance(raw_url, str)
                    and urlsplit(raw_url).hostname == LANDING_EXPECTED_HOST
                )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                )
            except Exception:
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=None,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                )
            if expected_host_reached is not True:
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=expected_host_reached,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_NAVIGATION_FAILED,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_NAVIGATION_FAILED,
                )

            try:
                search_control_visible = await self._probe_search_control_visible(
                    current_page,
                    timeout_seconds=operation_timeout,
                )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=True,
                    search_control_visible=None,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                )
            except Exception as exc:
                code = (
                    StructuralCandidateStatus.STRUCTURAL_PAGE_DETACHED
                    if _is_landing_probe_detached(exc)
                    else StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED
                )
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=True,
                    search_control_visible=None,
                    diagnostic_code=code,
                    candidate_status=code,
                )
            if search_control_visible is not True:
                return make_result(
                    page_set_category=preclassification.category.value,
                    expected_host_reached=True,
                    search_control_visible=search_control_visible,
                    diagnostic_code=StructuralCandidateStatus.STRUCTURAL_SEARCH_CONTROL_ABSENT,
                    candidate_status=StructuralCandidateStatus.STRUCTURAL_SEARCH_CONTROL_ABSENT,
                )
            return make_result(
                page_set_category=preclassification.category.value,
                expected_host_reached=True,
                search_control_visible=True,
                diagnostic_code=(
                    StructuralProvenanceCode.STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN
                ),
            )

        try:
            result = await asyncio.wait_for(
                run_observation(),
                timeout=STRUCTURAL_OBSERVATION_TOTAL_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            if observer_owned:
                try:
                    await close_owned_observer_page()
                except BaseException:
                    pass
            raise
        except (asyncio.TimeoutError, TimeoutError):
            result = _structural_observation_result(
                started_at=started_at,
                profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                page_set_category=preclassification.category.value,
                expected_host_reached=None,
                search_control_visible=None,
                diagnostic_code=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                candidate_status=StructuralCandidateStatus.STRUCTURAL_OBSERVATION_TIMEOUT,
                navigation_count=navigation_count,
            )
        except Exception:
            result = _structural_observation_result(
                started_at=started_at,
                profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                page_set_category=preclassification.category.value,
                expected_host_reached=None,
                search_control_visible=None,
                diagnostic_code=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                candidate_status=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                navigation_count=navigation_count,
            )
        finally:
            if observer_owned and page_holder[0] is not None:
                try:
                    await close_owned_observer_page()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if result is not None:
                        result = _structural_observation_result(
                            started_at=started_at,
                            profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                            page_set_category=cast(
                                str | None,
                                result.get("page_set_category"),
                            ),
                            expected_host_reached=cast(
                                bool | None,
                                result.get("expected_host_reached"),
                            ),
                            search_control_visible=cast(
                                bool | None,
                                result.get("search_control_visible"),
                            ),
                            diagnostic_code=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                            candidate_status=StructuralCandidateStatus.STRUCTURAL_CLEANUP_FAILED,
                            navigation_count=navigation_count,
                        )

        assert result is not None
        self._structural_observation_result = result
        _emit_structural_observation_telemetry(result, telemetry)
        return result

    # Descriptive aliases keep the capability discoverable without adding a
    # caller-controlled page/context/selector surface.
    observe_structural_selector_candidates = observe_structural_candidates
    observe_landing_structural_candidates = observe_structural_candidates

    async def _probe_new_observer_page(self) -> Any:
        """Create one probe-owned page without the normal init script.

        The generic ``new_page`` path intentionally keeps its existing
        anti-automation init script.  The internal probe has a narrower
        observer-only contract, so it records ownership through this separate
        path and does not touch the page facade until page-set validity has
        been checked by the caller.
        """
        async with self._cleanup_lock:
            if self._cleanup_started:
                raise RuntimeError("persistent browser session is closed")
            page = await self.context.new_page()
            self._pages.append(page)
            return page

    async def probe_landing_selector_proposals(
        self,
        *,
        timeout_seconds: float = LANDING_PROBE_TOTAL_TIMEOUT_SECONDS,
        telemetry: Callable[..., Any] | None = None,
        _launch_count: int = 0,
        _cleanup_telemetry: Callable[..., Any] | None = None,
    ) -> dict[str, object]:
        """Run one read-only, programmatic landing selector proposal probe.

        This boundary uses a cooperative, page-set-checked observer lifecycle.
        It never invokes ``landing_facts_provider`` or any scan operation.  A
        context must report zero pages before the one probe-owned page is
        created, and the same classifier must then prove the zero-to-one
        observer-only transition before any page-facade access.  All other
        states select the explicit non-owning abort path, which retains the
        context and Playwright owner and performs no page/context/Playwright
        cleanup.  Once observer ownership is proven, every terminal path closes
        only that observer page, including pre-navigation error/cancellation;
        the valid path uses one fixed landing navigation at most.

        Authentication and login/QR candidate statuses remain ``UNPROVEN``
        until an Owner-approved, concrete structural selector contract exists;
        this boundary does not accept caller-supplied pages, callbacks,
        selectors, or page content.
        """
        started_at = asyncio.get_running_loop().time()
        try:
            total_timeout = max(0.001, float(timeout_seconds))
        except (TypeError, ValueError):
            total_timeout = LANDING_PROBE_TOTAL_TIMEOUT_SECONDS
        operation_timeout = min(
            total_timeout,
            LANDING_PROBE_OPERATION_TIMEOUT_SECONDS,
        )
        page_holder: list[Any] = [None]
        observer_owned = False
        navigation_started = False
        navigation_count = 0
        result: dict[str, object] | None = None

        def make_result(
            *,
            expected_host_reached: bool | None,
            search_control_visible: bool | None,
            auth_shell_candidate_status: LandingCandidateStatus | str,
            login_or_qr_candidate_status: LandingCandidateStatus | str,
            diagnostic_code: LandingCandidateStatus | str,
            navigation: int = 0,
        ) -> dict[str, object]:
            return _landing_probe_result(
                started_at=started_at,
                profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                expected_host_reached=expected_host_reached,
                search_control_visible=search_control_visible,
                auth_shell_candidate_status=auth_shell_candidate_status,
                login_or_qr_candidate_status=login_or_qr_candidate_status,
                diagnostic_code=diagnostic_code,
                launch_count=_launch_count,
                navigation_count=navigation,
            )

        preclassification = self.classify_page_set()

        async def abort_before_navigation(
            fallback: PageSetClassification,
        ) -> PageSetAbortResult:
            classification = self.classify_page_set()
            if classification.is_observer_only:
                classification = fallback
            if classification.is_observer_only:
                classification = _page_set_from_count(PageSetCount.ZERO)
            return await self.abort_without_observation(classification)

        observer_release_started = False

        cleanup_telemetry = (
            _cleanup_telemetry if _cleanup_telemetry is not None else telemetry
        )

        async def close_owned_observer_page() -> None:
            """Release the proven observer without changing context ownership.

            Once the same-classifier observer-only transition has succeeded,
            the page is ours to close even when the probe has not navigated.
            The close runs in a shielded, bounded task so cancellation cannot
            strand the known-owned page.  The ownership bit is cleared before
            the close starts, making the release idempotent with the finalizer.
            """
            nonlocal observer_owned, observer_release_started
            if not observer_owned or page_holder[0] is None:
                return
            observer_owned = False
            observer_release_started = True
            close_task = asyncio.create_task(
                asyncio.wait_for(
                    self.close_page(page_holder[0]),
                    timeout=operation_timeout,
                )
            )
            cancellation_seen = False
            while not close_task.done():
                try:
                    await asyncio.shield(close_task)
                except asyncio.CancelledError:
                    cancellation_seen = True
                    continue
            try:
                close_task.result()
            except BaseException:
                raise
            if cancellation_seen:
                raise asyncio.CancelledError

        async def finalize_owned_observer_cancellation() -> None:
            """Record observer-only cleanup before preserving cancellation.

            This audit is intentionally separate from full persistent-session
            cleanup: the context and its Playwright owner remain open by
            contract, while the already-proven observer page is released once.
            The result and telemetry contain only typed lifecycle fields.
            """
            if self._observer_cleanup_result is not None:
                return

            close_error: BaseException | None = None
            try:
                await close_owned_observer_page()
            except BaseException as exc:
                close_error = exc

            if close_error is None:
                page_state = CleanupState.CLOSED
                code = CleanupCode.CLEANUP_OK
                invariant_ok = not self._pages
            else:
                page_state = (
                    CleanupState.TIMEOUT
                    if isinstance(close_error, (asyncio.TimeoutError, TimeoutError))
                    else CleanupState.ERROR
                )
                code = CleanupCode.CLEANUP_INVARIANT_FAILED
                invariant_ok = False

            result = CleanupResult(
                code=code,
                remaining_pages=len(self._pages),
                remaining_holders=len(self._pages),
                page_state=page_state,
                context_state=CleanupState.OPEN,
                playwright_state=CleanupState.OPEN,
                elapsed_ms=_bounded_elapsed_ms(started_at),
                invariant_ok=invariant_ok,
            )
            self._observer_cleanup_result = result
            try:
                _emit_cleanup_telemetry(
                    phase=CleanupPhase.PAGE_CLOSE,
                    started_at=started_at,
                    code=code,
                    state=page_state,
                    telemetry=cleanup_telemetry,
                )
            except BaseException:
                # Audit/telemetry failures must never replace cancellation.
                pass

        if preclassification.category is not PageSetCategory.PAGESET_PREEXISTING_ZERO:
            await self.abort_without_observation(preclassification)
            result = make_result(
                expected_host_reached=None,
                search_control_visible=None,
                auth_shell_candidate_status=preclassification.category.value,
                login_or_qr_candidate_status=preclassification.category.value,
                diagnostic_code=preclassification.category.value,
            )
            _emit_landing_probe_telemetry(result, telemetry)
            return result

        async def run_probe() -> dict[str, object]:
            nonlocal observer_owned, navigation_started, navigation_count
            try:
                current_page = await self._probe_bounded_call(
                    self._probe_new_observer_page,
                    timeout_seconds=operation_timeout,
                )
            except asyncio.CancelledError:
                await abort_before_navigation(preclassification)
                raise
            except (asyncio.TimeoutError, TimeoutError):
                await abort_before_navigation(preclassification)
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=(
                        LandingCandidateStatus.LANDING_PAGE_CREATE_TIMEOUT
                    ),
                    login_or_qr_candidate_status=(
                        LandingCandidateStatus.LANDING_PAGE_CREATE_TIMEOUT
                    ),
                    diagnostic_code=LandingCandidateStatus.LANDING_PAGE_CREATE_TIMEOUT,
                )
            except Exception:
                await abort_before_navigation(preclassification)
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=(
                        LandingCandidateStatus.LANDING_PAGE_CREATE_FAILED
                    ),
                    login_or_qr_candidate_status=(
                        LandingCandidateStatus.LANDING_PAGE_CREATE_FAILED
                    ),
                    diagnostic_code=LandingCandidateStatus.LANDING_PAGE_CREATE_FAILED,
                )

            if current_page is None:
                await abort_before_navigation(preclassification)
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=(
                        LandingCandidateStatus.LANDING_PAGE_CREATE_FAILED
                    ),
                    login_or_qr_candidate_status=(
                        LandingCandidateStatus.LANDING_PAGE_CREATE_FAILED
                    ),
                    diagnostic_code=LandingCandidateStatus.LANDING_PAGE_CREATE_FAILED,
                )

            page_holder[0] = current_page

            try:
                postclassification = self._page_set_classifier.classify_after_create(
                    self.context,
                    current_page,
                )
            except asyncio.CancelledError:
                await abort_before_navigation(preclassification)
                raise
            except Exception:
                await abort_before_navigation(preclassification)
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    diagnostic_code=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                )

            if not postclassification.is_observer_only:
                await self.abort_without_observation(postclassification)
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=postclassification.category.value,
                    login_or_qr_candidate_status=postclassification.category.value,
                    diagnostic_code=postclassification.category.value,
                )

            observer_owned = True

            try:
                if await self._probe_page_closed(
                    current_page,
                    timeout_seconds=operation_timeout,
                ):
                    await close_owned_observer_page()
                    return make_result(
                        expected_host_reached=None,
                        search_control_visible=None,
                        auth_shell_candidate_status=LandingCandidateStatus.LANDING_PAGE_CLOSED,
                        login_or_qr_candidate_status=LandingCandidateStatus.LANDING_PAGE_CLOSED,
                        diagnostic_code=LandingCandidateStatus.LANDING_PAGE_CLOSED,
                    )
            except asyncio.CancelledError:
                await finalize_owned_observer_cancellation()
                raise
            except (asyncio.TimeoutError, TimeoutError):
                await close_owned_observer_page()
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                    diagnostic_code=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                )
            except Exception:
                await close_owned_observer_page()
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    diagnostic_code=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                )

            goto = getattr(current_page, "goto", None)
            if not callable(goto):
                await close_owned_observer_page()
                return make_result(
                    expected_host_reached=False,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_NAVIGATION_FAILED,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_NAVIGATION_FAILED,
                    diagnostic_code=LandingCandidateStatus.LANDING_NAVIGATION_FAILED,
                )
            navigation_count = 1
            navigation_started = True
            try:
                await self._probe_bounded_call(
                    lambda: goto(
                        LANDING_PROBE_EXPECTED_URL,
                        timeout=LANDING_PROBE_NAVIGATION_TIMEOUT_MS,
                    ),
                    timeout_seconds=operation_timeout,
                )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return make_result(
                    expected_host_reached=False,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_NAVIGATION_TIMEOUT,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_NAVIGATION_TIMEOUT,
                    diagnostic_code=LandingCandidateStatus.LANDING_NAVIGATION_TIMEOUT,
                    navigation=navigation_count,
                )
            except Exception:
                return make_result(
                    expected_host_reached=False,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_NAVIGATION_FAILED,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_NAVIGATION_FAILED,
                    diagnostic_code=LandingCandidateStatus.LANDING_NAVIGATION_FAILED,
                    navigation=navigation_count,
                )

            try:
                if await self._probe_page_closed(
                    current_page,
                    timeout_seconds=operation_timeout,
                ):
                    return make_result(
                        expected_host_reached=None,
                        search_control_visible=None,
                        auth_shell_candidate_status=LandingCandidateStatus.LANDING_PAGE_CLOSED,
                        login_or_qr_candidate_status=LandingCandidateStatus.LANDING_PAGE_CLOSED,
                        diagnostic_code=LandingCandidateStatus.LANDING_PAGE_CLOSED,
                        navigation=navigation_count,
                    )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                    diagnostic_code=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                    navigation=navigation_count,
                )
            except Exception:
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    diagnostic_code=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    navigation=navigation_count,
                )

            raw_url = getattr(current_page, "url", None)
            try:
                if callable(raw_url):
                    raw_url = await self._probe_bounded_call(
                        raw_url,
                        timeout_seconds=operation_timeout,
                    )
                expected_host_reached = (
                    isinstance(raw_url, str)
                    and urlsplit(raw_url).hostname == LANDING_EXPECTED_HOST
                )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_NAVIGATION_TIMEOUT,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_NAVIGATION_TIMEOUT,
                    diagnostic_code=LandingCandidateStatus.LANDING_NAVIGATION_TIMEOUT,
                    navigation=navigation_count,
                )
            except (TypeError, ValueError):
                expected_host_reached = None
            except Exception:
                return make_result(
                    expected_host_reached=None,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    diagnostic_code=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    navigation=navigation_count,
                )
            if expected_host_reached is not True:
                return make_result(
                    expected_host_reached=expected_host_reached,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_EXPECTED_HOST_MISMATCH,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_EXPECTED_HOST_MISMATCH,
                    diagnostic_code=LandingCandidateStatus.LANDING_EXPECTED_HOST_MISMATCH,
                    navigation=navigation_count,
                )

            try:
                search_control_visible = await self._probe_search_control_visible(
                    current_page,
                    timeout_seconds=operation_timeout,
                )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return make_result(
                    expected_host_reached=True,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                    diagnostic_code=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                    navigation=navigation_count,
                )
            except Exception as exc:
                if _is_landing_probe_detached(exc):
                    return make_result(
                        expected_host_reached=True,
                        search_control_visible=None,
                        auth_shell_candidate_status=LandingCandidateStatus.LANDING_PAGE_DETACHED,
                        login_or_qr_candidate_status=LandingCandidateStatus.LANDING_PAGE_DETACHED,
                        diagnostic_code=LandingCandidateStatus.LANDING_PAGE_DETACHED,
                        navigation=navigation_count,
                    )
                return make_result(
                    expected_host_reached=True,
                    search_control_visible=None,
                    auth_shell_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    login_or_qr_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    diagnostic_code=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                    navigation=navigation_count,
                )

            return make_result(
                expected_host_reached=True,
                search_control_visible=search_control_visible,
                auth_shell_candidate_status=LandingCandidateStatus.UNPROVEN,
                login_or_qr_candidate_status=LandingCandidateStatus.UNPROVEN,
                diagnostic_code="LANDING_PROBE_NO_PROVEN_CANDIDATE",
                navigation=navigation_count,
            )

        try:
            result = await asyncio.wait_for(
                run_probe(),
                timeout=total_timeout,
            )
        except asyncio.CancelledError:
            if observer_owned and not navigation_started:
                await finalize_owned_observer_cancellation()
            elif (
                not navigation_started
                and not observer_release_started
                and self._page_set_abort_result is None
            ):
                await abort_before_navigation(preclassification)
            raise
        except (asyncio.TimeoutError, TimeoutError):
            result = _landing_probe_result(
                started_at=started_at,
                profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                expected_host_reached=None,
                search_control_visible=None,
                auth_shell_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                login_or_qr_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                diagnostic_code=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                launch_count=_launch_count,
                navigation_count=navigation_count,
            )
        except Exception:
            result = _landing_probe_result(
                started_at=started_at,
                profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                expected_host_reached=None,
                search_control_visible=None,
                auth_shell_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                login_or_qr_candidate_status=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                diagnostic_code=LandingCandidateStatus.LANDING_UNKNOWN_FAIL_CLOSED,
                launch_count=_launch_count,
                navigation_count=navigation_count,
            )
        finally:
            if observer_owned and page_holder[0] is not None:
                try:
                    await close_owned_observer_page()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if result is not None:
                        result = _landing_probe_result(
                            started_at=started_at,
                            profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                            expected_host_reached=cast(
                                bool | None,
                                result.get("expected_host_reached"),
                            ),
                            search_control_visible=cast(
                                bool | None,
                                result.get("search_control_visible"),
                            ),
                            auth_shell_candidate_status=LandingCandidateStatus.LANDING_CLEANUP_FAILED,
                            login_or_qr_candidate_status=LandingCandidateStatus.LANDING_CLEANUP_FAILED,
                            diagnostic_code=LandingCandidateStatus.LANDING_CLEANUP_FAILED,
                            launch_count=_launch_count,
                            navigation_count=int(
                                cast(
                                    dict[str, int],
                                    result["zero_action_counters"],
                                ).get("navigation", 0)
                            ),
                        )

        assert result is not None
        _emit_landing_probe_telemetry(result, telemetry)
        return result

    # Singular aliases keep the programmatic boundary discoverable without
    # exposing another route or changing the production scan/provider policy.
    probe_landing_selector_proposal = probe_landing_selector_proposals
    probe_internal_landing_selectors = probe_landing_selector_proposals

    async def new_page(self) -> Any:
        async with self._cleanup_lock:
            if self._cleanup_started:
                raise RuntimeError("persistent browser session is closed")
            page = await self.context.new_page()
            self._pages.append(page)
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            return page

    async def close_page(self, page: Any) -> None:
        """Close one page at most once, preserving the existing API shape."""
        async with self._cleanup_lock:
            if self._page_set_abort_result is not None:
                raise RuntimeError("page-set abort retained browser ownership")
            if page is None:
                return
            page_key = id(page)
            if page_key in self._page_attempts:
                return
            self._page_attempts.add(page_key)
            close = getattr(page, "close", None)
            if close is None:
                self._page_close_failure = CleanupCode.CLEANUP_INVARIANT_FAILED
                return
            close_succeeded = False
            try:
                await _call_cleanup_operation(
                    close,
                    timeout_seconds=CLEANUP_TIMEOUT_SECONDS,
                )
                close_succeeded = True
            except BaseException as exc:
                if _typed_already_closed(page, exc):
                    close_succeeded = True
                    self._page_already_closed = True
                else:
                    self._page_close_failure = CleanupCode.CLEANUP_INVARIANT_FAILED
                    raise
            finally:
                if page in self._pages and close_succeeded:
                    self._pages.remove(page)

    @property
    def cleanup_result(self) -> CleanupResult | None:
        return self._cleanup_result or self._observer_cleanup_result

    @property
    def remaining_pages(self) -> int:
        return len(self._pages)

    async def cleanup(
        self,
        *,
        playwright_stop: Callable[[], Awaitable[Any]] | None = None,
        timeout_seconds: float = CLEANUP_TIMEOUT_SECONDS,
        total_timeout_seconds: float | None = None,
        holder_count: Callable[[], int | Awaitable[int]] | int | None = None,
        telemetry: Callable[..., Any] | None = None,
    ) -> CleanupResult:
        """Close pages/context and stop Playwright exactly once.

        Typed already-closed/stopped exceptions are normalized only when an
        explicit resource marker (or zero-holder final invariant) agrees.  All
        unknown failures, including timeouts, remain fail-closed.
        """
        async with self._cleanup_lock:
            if self._cleanup_result is not None:
                return self._cleanup_result
            self._cleanup_started = True
            started_at = asyncio.get_running_loop().time()
            cleanup_deadline = (
                None
                if total_timeout_seconds is None
                else started_at + max(0.001, float(total_timeout_seconds))
            )
            cleanup_incomplete = False
            page_state = CleanupState.CLOSED
            page_code: CleanupCode | None = self._page_close_failure
            if self._page_close_failure is not None:
                page_state = CleanupState.ERROR

            for page in tuple(self._pages):
                page_started_at = asyncio.get_running_loop().time()
                page_key = id(page)
                if page_key in self._page_attempts:
                    continue
                self._page_attempts.add(page_key)
                close = getattr(page, "close", None)
                if close is None:
                    page_state = CleanupState.INVARIANT_FAILED
                    page_code = CleanupCode.CLEANUP_INVARIANT_FAILED
                    _emit_cleanup_telemetry(
                        phase=CleanupPhase.PAGE_CLOSE,
                        started_at=page_started_at,
                        code=CleanupCode.CLEANUP_INVARIANT_FAILED,
                        state=page_state,
                        telemetry=telemetry,
                    )
                    continue
                marker = _safe_resource_marker(page, ("closed", "is_closed"))
                if marker is True:
                    page_state = CleanupState.ALREADY_CLOSED
                    self._page_already_closed = True
                    self._pages.remove(page)
                    _emit_cleanup_telemetry(
                        phase=CleanupPhase.PAGE_CLOSE,
                        started_at=page_started_at,
                        code=CleanupCode.CLEANUP_ALREADY_CLOSED,
                        state=page_state,
                        telemetry=telemetry,
                    )
                    continue
                try:
                    await _call_cleanup_operation(
                        close,
                        timeout_seconds=timeout_seconds,
                        deadline=cleanup_deadline,
                    )
                except CleanupDeadlineExceeded:
                    cleanup_incomplete = True
                    page_state = CleanupState.TIMEOUT
                    page_code = CleanupCode.CLEANUP_INCOMPLETE
                    _emit_cleanup_telemetry(
                        phase=CleanupPhase.PAGE_CLOSE,
                        started_at=page_started_at,
                        code=page_code,
                        state=page_state,
                        telemetry=telemetry,
                    )
                    break
                except BaseException as exc:
                    if _typed_already_closed(page, exc):
                        marker = _safe_resource_marker(page, ("closed", "is_closed"))
                        if marker is not False:
                            page_state = CleanupState.ALREADY_CLOSED
                            self._page_already_closed = True
                            _emit_cleanup_telemetry(
                                phase=CleanupPhase.PAGE_CLOSE,
                                started_at=page_started_at,
                                code=CleanupCode.CLEANUP_ALREADY_CLOSED,
                                state=page_state,
                                telemetry=telemetry,
                            )
                            self._pages.remove(page)
                            continue
                    page_state = (
                        CleanupState.TIMEOUT
                        if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                        else CleanupState.ERROR
                    )
                    page_code = CleanupCode.CLEANUP_INVARIANT_FAILED
                    _emit_cleanup_telemetry(
                        phase=CleanupPhase.PAGE_CLOSE,
                        started_at=page_started_at,
                        code=page_code,
                        state=page_state,
                        telemetry=telemetry,
                    )
                    continue
                else:
                    if page_code is None:
                        page_state = CleanupState.CLOSED
                    if page in self._pages:
                        self._pages.remove(page)
                    _emit_cleanup_telemetry(
                        phase=CleanupPhase.PAGE_CLOSE,
                        started_at=page_started_at,
                        code=CleanupCode.CLEANUP_OK,
                        state=page_state,
                        telemetry=telemetry,
                    )

            context_started_at = asyncio.get_running_loop().time()
            context_state = CleanupState.CLOSED
            context_code: CleanupCode | None = None
            context_marker = _safe_resource_marker(
                self.context,
                ("closed", "is_closed"),
            )
            context_was_closed = context_marker is True
            context = self.context

            async def close_context() -> Any:
                return await context.close()

            if context is None and cleanup_deadline is not None:
                # A launch that failed before a context was created has no
                # context operation to perform.  In the additive diagnostic
                # mode this is a known-safe no-op; legacy callers retain their
                # historical fail-closed missing-context result below.
                context_marker = True
                context_state = CleanupState.CLOSED
                context_code = CleanupCode.CLEANUP_OK
            else:
                try:
                    await _call_cleanup_operation(
                        close_context,
                        timeout_seconds=timeout_seconds,
                        deadline=cleanup_deadline,
                    )
                except CleanupDeadlineExceeded:
                    cleanup_incomplete = True
                    context_state = CleanupState.TIMEOUT
                    context_code = CleanupCode.CLEANUP_INCOMPLETE
                except BaseException as exc:
                    if isinstance(exc, ContextAlreadyClosedError):
                        context_marker = _safe_resource_marker(
                            self.context,
                            ("closed", "is_closed"),
                        )
                        if context_marker is not False:
                            context_state = CleanupState.ALREADY_CLOSED
                            context_code = CleanupCode.CLEANUP_ALREADY_CLOSED
                        else:
                            context_state = CleanupState.INVARIANT_FAILED
                            context_code = CleanupCode.CLEANUP_INVARIANT_FAILED
                    else:
                        context_state = (
                            CleanupState.TIMEOUT
                            if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                            else CleanupState.ERROR
                        )
                        context_code = _cleanup_error_code(
                            exc,
                            timeout_code=CleanupCode.CONTEXT_CLOSE_TIMEOUT,
                            error_code=CleanupCode.CONTEXT_CLOSE_ERROR,
                        )
                else:
                    context_marker = _safe_resource_marker(
                        self.context,
                        ("closed", "is_closed"),
                    )
                    context_state = (
                        CleanupState.ALREADY_CLOSED
                        if context_was_closed
                        else CleanupState.CLOSED
                    )
                    context_code = (
                        CleanupCode.CLEANUP_ALREADY_CLOSED
                        if context_was_closed
                        else CleanupCode.CLEANUP_OK
                    )
            _emit_cleanup_telemetry(
                phase=CleanupPhase.CONTEXT_CLOSE,
                started_at=context_started_at,
                code=context_code,
                state=context_state,
                telemetry=telemetry,
            )

            stop_started_at = asyncio.get_running_loop().time()
            playwright_state = CleanupState.STOPPED
            stop_code: CleanupCode | None = None
            stop_resource = getattr(playwright_stop, "__self__", None)
            stop_marker = _safe_resource_marker(
                stop_resource,
                ("stopped", "is_stopped"),
            )
            stop_was_stopped = stop_marker is True
            if playwright_stop is None:
                playwright_state = CleanupState.INVARIANT_FAILED
                stop_code = CleanupCode.PLAYWRIGHT_STOP_ERROR
            else:
                try:
                    await _call_cleanup_operation(
                        playwright_stop,
                        timeout_seconds=timeout_seconds,
                        deadline=cleanup_deadline,
                    )
                except CleanupDeadlineExceeded:
                    cleanup_incomplete = True
                    playwright_state = CleanupState.TIMEOUT
                    stop_code = CleanupCode.CLEANUP_INCOMPLETE
                except BaseException as exc:
                    if isinstance(exc, PlaywrightAlreadyStoppedError):
                        stop_marker = _safe_resource_marker(
                            stop_resource,
                            ("stopped", "is_stopped"),
                        )
                        if stop_marker is not False:
                            playwright_state = CleanupState.ALREADY_STOPPED
                            stop_code = CleanupCode.CLEANUP_ALREADY_CLOSED
                        else:
                            playwright_state = CleanupState.INVARIANT_FAILED
                            stop_code = CleanupCode.CLEANUP_INVARIANT_FAILED
                    else:
                        playwright_state = (
                            CleanupState.TIMEOUT
                            if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                            else CleanupState.ERROR
                        )
                        stop_code = _cleanup_error_code(
                            exc,
                            timeout_code=CleanupCode.PLAYWRIGHT_STOP_TIMEOUT,
                            error_code=CleanupCode.PLAYWRIGHT_STOP_ERROR,
                        )
                else:
                    stop_marker = _safe_resource_marker(
                        stop_resource,
                        ("stopped", "is_stopped"),
                    )
                    playwright_state = (
                        CleanupState.ALREADY_STOPPED
                        if stop_was_stopped
                        else CleanupState.STOPPED
                    )
                    stop_code = (
                        CleanupCode.CLEANUP_ALREADY_CLOSED
                        if stop_was_stopped
                        else CleanupCode.CLEANUP_OK
                    )
            _emit_cleanup_telemetry(
                phase=CleanupPhase.PLAYWRIGHT_STOP,
                started_at=stop_started_at,
                code=stop_code,
                state=playwright_state,
                telemetry=telemetry,
            )

            remaining_holders, holders_known = await _resolve_holder_count(
                holder_count,
                fallback=len(self._pages),
                timeout_seconds=timeout_seconds,
                deadline=cleanup_deadline,
            )
            if (
                cleanup_deadline is not None
                and asyncio.get_running_loop().time() >= cleanup_deadline
            ):
                cleanup_incomplete = True
            invariant_ok = (
                not self._pages
                and remaining_holders == 0
                and holders_known
                and context_marker is not False
                and context_state in {
                    CleanupState.CLOSED,
                    CleanupState.ALREADY_CLOSED,
                }
                and playwright_state
                in {CleanupState.STOPPED, CleanupState.ALREADY_STOPPED}
                and stop_marker is not False
            )
            final_code: CleanupCode
            if cleanup_incomplete:
                final_code = CleanupCode.CLEANUP_INCOMPLETE
                final_state = CleanupState.TIMEOUT
                invariant_ok = False
            elif context_code in {
                CleanupCode.CONTEXT_CLOSE_TIMEOUT,
                CleanupCode.CONTEXT_CLOSE_ERROR,
            }:
                final_code = context_code
                final_state = context_state
            elif stop_code in {
                CleanupCode.PLAYWRIGHT_STOP_TIMEOUT,
                CleanupCode.PLAYWRIGHT_STOP_ERROR,
            }:
                final_code = stop_code
                final_state = playwright_state
            elif not invariant_ok:
                final_code = CleanupCode.CLEANUP_INVARIANT_FAILED
                final_state = CleanupState.INVARIANT_FAILED
            elif page_code is not None:
                final_code = page_code
                final_state = page_state
            elif CleanupCode.CLEANUP_ALREADY_CLOSED in {
                context_code,
                stop_code,
            } or context_state == CleanupState.ALREADY_CLOSED or self._page_already_closed:
                final_code = CleanupCode.CLEANUP_ALREADY_CLOSED
                final_state = CleanupState.ALREADY_CLOSED
            else:
                final_code = CleanupCode.CLEANUP_OK
                final_state = CleanupState.CLOSED
            result = CleanupResult(
                code=final_code,
                remaining_pages=len(self._pages),
                remaining_holders=remaining_holders,
                page_state=page_state,
                context_state=context_state,
                playwright_state=playwright_state,
                elapsed_ms=_bounded_elapsed_ms(started_at),
                invariant_ok=invariant_ok,
                cleanup_incomplete=cleanup_incomplete,
            )
            _emit_cleanup_telemetry(
                phase=CleanupPhase.FINAL,
                started_at=started_at,
                code=final_code,
                state=final_state,
                telemetry=telemetry,
            )
            self._cleanup_result = result
            return result


async def _resolve_holder_count(
    provider: Callable[[], int | Awaitable[int]] | int | None,
    *,
    fallback: int,
    timeout_seconds: float,
    deadline: float | None = None,
) -> tuple[int, bool]:
    if provider is None:
        return max(0, int(fallback)), True
    try:
        value = provider() if callable(provider) else provider
        if inspect.isawaitable(value):
            timeout = max(0.001, float(timeout_seconds))
            if deadline is not None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return max(0, int(fallback)), False
                timeout = min(timeout, remaining)
            value = await asyncio.wait_for(
                value,
                timeout=timeout,
            )
        return max(0, int(value)), True
    except BaseException:
        return max(0, int(fallback)), False


@dataclass
class _RawCleanupState:
    session: PersistentBrowserSession | None = None
    result: CleanupResult | None = None


_RAW_CONTEXT_CLEANUP: weakref.WeakKeyDictionary[Any, _RawCleanupState] = (
    weakref.WeakKeyDictionary()
)
_UNWEAKREFABLE_CONTEXT_CLEANUP: dict[
    int, tuple[Any, _RawCleanupState]
] = {}


def _raw_cleanup_state(context: Any) -> _RawCleanupState:
    """Return one cleanup owner for repeated calls with the same raw context."""
    try:
        state = _RAW_CONTEXT_CLEANUP.get(context)
        if state is None:
            state = _RawCleanupState()
            _RAW_CONTEXT_CLEANUP[context] = state
        return state
    except TypeError:
        # A synthetic/unusual context may be unhashable or not weak-referenceable.
        # Retain the exact object alongside its id to avoid id reuse ambiguity.
        key = id(context)
        entry = _UNWEAKREFABLE_CONTEXT_CLEANUP.get(key)
        if entry is not None and entry[0] is context:
            return entry[1]
        state = _RawCleanupState()
        _UNWEAKREFABLE_CONTEXT_CLEANUP[key] = (context, state)
        return state


async def cleanup_persistent_resources(
    *,
    session: PersistentBrowserSession | None = None,
    context: Any | None = None,
    playwright_stop: Callable[[], Awaitable[Any]] | None = None,
    timeout_seconds: float = CLEANUP_TIMEOUT_SECONDS,
    total_timeout_seconds: float | None = None,
    holder_count: Callable[[], int | Awaitable[int]] | int | None = None,
    telemetry: Callable[..., Any] | None = None,
) -> CleanupResult:
    """Public hermetic cleanup boundary for a persistent browser session."""
    if session is not None:
        return await session.cleanup(
            playwright_stop=playwright_stop,
            timeout_seconds=timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
            holder_count=holder_count,
            telemetry=telemetry,
        )
    if context is None:
        owner = PersistentBrowserSession(context)
        return await owner.cleanup(
            playwright_stop=playwright_stop,
            timeout_seconds=timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
            holder_count=holder_count,
            telemetry=telemetry,
        )

    state = _raw_cleanup_state(context)
    if state.result is not None:
        return state.result
    if state.session is None:
        state.session = PersistentBrowserSession(context)
    result = await state.session.cleanup(
        playwright_stop=playwright_stop,
        timeout_seconds=timeout_seconds,
        total_timeout_seconds=total_timeout_seconds,
        holder_count=holder_count,
        telemetry=telemetry,
    )
    state.result = result
    state.session = None
    return result


cleanup_persistent_session = cleanup_persistent_resources


class PlaywrightBrowserGateway:
    """Owns Playwright/browser lifecycles while exposing raw pages to services."""

    def __init__(
        self,
        *,
        playwright_factory: Callable[[], Any] = async_playwright,
        form_submitter_factory: Callable[..., Any] | None = None,
        profile_path: str | None = None,
        landing_facts_provider_factory: Callable[[], Callable[..., Any]] | None = None,
        authenticated_shell_probe: _LandingProbe | None = None,
        login_or_qr_probe: _LandingProbe | None = None,
        blocking_modal_probe: _LandingProbe | None = None,
        unsupported_browser_probe: _LandingProbe | None = None,
        network_error_probe: _LandingProbe | None = None,
        max_retries: int | None = None,
        backoff_seconds: float | None = None,
        failure_threshold: int | None = None,
        cooldown_seconds: float | None = None,
    ):
        self._playwright_factory = playwright_factory
        self._uses_real_playwright_factory = playwright_factory is async_playwright
        self._form_submitter_factory = (
            form_submitter_factory or _default_form_submitter_factory
        )
        self._landing_facts_provider_factory = landing_facts_provider_factory
        self._authenticated_shell_probe = authenticated_shell_probe
        self._login_or_qr_probe = login_or_qr_probe
        self._blocking_modal_probe = blocking_modal_probe
        self._unsupported_browser_probe = unsupported_browser_probe
        self._network_error_probe = network_error_probe
        self.profile_path = (
            str(Path(profile_path).expanduser().resolve())
            if profile_path is not None
            else "./zalo_session"
        )
        self.last_cleanup_result: CleanupResult | None = None
        self._active_session: PersistentBrowserSession | None = None
        self._circuit = GatewayCircuitBreaker(
            max_retries=(
                _config_value("GATEWAY_MAX_RETRIES", 2)
                if max_retries is None
                else max_retries
            ),
            backoff_seconds=(
                _config_value("GATEWAY_BACKOFF_SECONDS", 0.25)
                if backoff_seconds is None
                else backoff_seconds
            ),
            failure_threshold=(
                _config_value("GATEWAY_FAILURE_THRESHOLD", 3)
                if failure_threshold is None
                else failure_threshold
            ),
            cooldown_seconds=(
                _config_value("GATEWAY_COOLDOWN_SECONDS", 30.0)
                if cooldown_seconds is None
                else cooldown_seconds
            ),
        )

    def _persistent_launch_options(
        self,
        viewport: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Return the worker's exact persistent Chromium launch contract."""
        launch_options: dict[str, Any] = {
            "user_data_dir": self.profile_path,
            "headless": False,
            "channel": "chrome",
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if viewport is not None:
            launch_options["viewport"] = viewport
        return launch_options

    @asynccontextmanager
    async def persistent_session(
        self,
        *,
        viewport: dict[str, int] | None = None,
        cleanup_timeout_seconds: float | None = None,
    ) -> AsyncIterator[PersistentBrowserSession]:
        if os.environ.get("ZALO_QUALITY_ISOLATED") == "1" and self._uses_real_playwright_factory:
            raise QualityIsolationViolation(
                "quality isolation denies production persistent browser entry"
            )
        cleanup_timeout = (
            CLEANUP_TIMEOUT_SECONDS
            if cleanup_timeout_seconds is None
            else max(0.001, float(cleanup_timeout_seconds))
        )
        cleanup_total_timeout = (
            None
            if cleanup_timeout_seconds is None
            else cleanup_timeout
        )
        manager = self._playwright_factory()
        entered = False
        manager_exit_called = False
        session: PersistentBrowserSession | None = None

        async def stop_playwright() -> None:
            nonlocal manager_exit_called
            if manager_exit_called:
                return
            manager_exit_called = True
            await manager.__aexit__(None, None, None)

        try:
            playwright = await manager.__aenter__()
            entered = True
            launch_options = self._persistent_launch_options(viewport)

            async def launch_context():
                return await playwright.chromium.launch_persistent_context(
                    **launch_options
                )

            context = cast(Any, await self._circuit.call(launch_context))
            provider = (
                self._landing_facts_provider_factory()
                if self._landing_facts_provider_factory is not None
                else None
            )
            session = PersistentBrowserSession(
                context,
                landing_facts_provider=provider,
                authenticated_shell_probe=self._authenticated_shell_probe,
                login_or_qr_probe=self._login_or_qr_probe,
                blocking_modal_probe=self._blocking_modal_probe,
                unsupported_browser_probe=self._unsupported_browser_probe,
                network_error_probe=self._network_error_probe,
            )
            self._active_session = session
            self.last_cleanup_result = None
            yield session
        finally:
            try:
                if not entered:
                    pass
                elif session is None:
                    # Launch failed before a context existed.  Still stop the
                    # Playwright owner once, while retaining the launch error.
                    self.last_cleanup_result = await cleanup_persistent_resources(
                        context=None,
                        playwright_stop=stop_playwright,
                        timeout_seconds=cleanup_timeout,
                        total_timeout_seconds=cleanup_total_timeout,
                        holder_count=0,
                    )

                else:
                    self.last_cleanup_result = await session.cleanup(
                        playwright_stop=stop_playwright,
                        timeout_seconds=cleanup_timeout,
                        total_timeout_seconds=cleanup_total_timeout,
                        holder_count=0,
                    )
            finally:
                if self._active_session is session:
                    self._active_session = None

    async def observe_structural_candidates(
        self,
        *,
        telemetry: Callable[..., Any] | None = None,
    ) -> dict[str, object]:
        """Observe candidates only through an already-active worker session.

        This method deliberately never launches Playwright or creates a
        context/profile.  A caller must already be inside the gateway's normal
        persistent-session lifecycle; otherwise the result is unavailable.
        """
        started_at = asyncio.get_running_loop().time()
        session = self._active_session
        if session is None:
            result = _structural_observation_result(
                started_at=started_at,
                profile_binding=LandingProbeProfileBinding.UNAVAILABLE,
                page_set_category=None,
                expected_host_reached=None,
                search_control_visible=None,
                diagnostic_code=StructuralCandidateStatus.STRUCTURAL_SESSION_UNAVAILABLE,
                candidate_status=StructuralCandidateStatus.STRUCTURAL_SESSION_UNAVAILABLE,
                provenance_code=StructuralProvenanceCode.STRUCTURAL_PROVENANCE_NOT_VERIFIED,
            )
            _emit_structural_observation_telemetry(result, telemetry)
            return result
        try:
            result = await session.observe_structural_candidates(telemetry=None)
        except asyncio.CancelledError:
            raise
        except Exception:
            result = _structural_observation_result(
                started_at=started_at,
                profile_binding=LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION,
                page_set_category=None,
                expected_host_reached=None,
                search_control_visible=None,
                diagnostic_code=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                candidate_status=StructuralCandidateStatus.STRUCTURAL_UNKNOWN_FAIL_CLOSED,
                provenance_code=StructuralProvenanceCode.STRUCTURAL_PROVENANCE_NOT_VERIFIED,
            )
        _emit_structural_observation_telemetry(result, telemetry)
        return result

    observe_structural_selector_candidates = observe_structural_candidates
    observe_landing_structural_candidates = observe_structural_candidates

    async def probe_landing_selector_proposals(
        self,
        *,
        timeout_seconds: float = LANDING_PROBE_TOTAL_TIMEOUT_SECONDS,
        telemetry: Callable[..., Any] | None = None,
    ) -> dict[str, object]:
        """Run one bounded probe through a probe-specific worker lifecycle.

        The normal persistent-session path remains circuit-protected and owns
        context/Playwright cleanup.  This probe deliberately bypasses that
        retry/cleanup policy: it launches at most once with the same options,
        classifies the page set before page creation, and leaves the manager
        and context untouched after the probe.  Only the proven observer page
        can be closed by the session's narrow probe path.
        """
        if os.environ.get("ZALO_QUALITY_ISOLATED") == "1" and self._uses_real_playwright_factory:
            raise QualityIsolationViolation(
                "quality isolation denies production persistent browser entry"
            )
        started_at = asyncio.get_running_loop().time()
        try:
            total_timeout = max(0.001, float(timeout_seconds))
        except (TypeError, ValueError):
            total_timeout = LANDING_PROBE_TOTAL_TIMEOUT_SECONDS
        operation_timeout = min(
            total_timeout,
            LANDING_PROBE_OPERATION_TIMEOUT_SECONDS,
        )
        launch_count = 0
        session: PersistentBrowserSession | None = None
        manager = self._playwright_factory()

        async def run_probe() -> dict[str, object]:
            nonlocal launch_count, session
            playwright = await manager.__aenter__()
            launch_count = 1
            launch_options = self._persistent_launch_options()
            context = cast(
                Any,
                await asyncio.wait_for(
                    playwright.chromium.launch_persistent_context(
                        **launch_options,
                    ),
                    timeout=operation_timeout,
                ),
            )
            session = PersistentBrowserSession(
                context,
                authenticated_shell_probe=self._authenticated_shell_probe,
                login_or_qr_probe=self._login_or_qr_probe,
                blocking_modal_probe=self._blocking_modal_probe,
                unsupported_browser_probe=self._unsupported_browser_probe,
                network_error_probe=self._network_error_probe,
            )
            result = await session.probe_landing_selector_proposals(
                timeout_seconds=total_timeout,
                telemetry=None,
                _cleanup_telemetry=telemetry,
                _launch_count=launch_count,
            )
            # A non-owning page-set stop records a typed skipped-cleanup result;
            # a successful probe has no full-session cleanup result because only
            # its observer page may be closed.  A cancelled owned probe may
            # expose its separate observer-page audit result here.
            self.last_cleanup_result = session.cleanup_result
            return result

        try:
            result = await asyncio.wait_for(
                run_probe(),
                timeout=total_timeout,
            )
        except asyncio.CancelledError:
            if session is not None:
                self.last_cleanup_result = session.cleanup_result
            raise
        except (asyncio.TimeoutError, TimeoutError):
            result = _landing_probe_result(
                started_at=started_at,
                profile_binding=(
                    LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION
                    if session is not None
                    else LandingProbeProfileBinding.UNAVAILABLE
                ),
                expected_host_reached=None,
                search_control_visible=None,
                auth_shell_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                login_or_qr_candidate_status=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                diagnostic_code=LandingCandidateStatus.LANDING_READINESS_TIMEOUT,
                launch_count=launch_count,
            )
        except Exception:
            # Launch errors include profile lock/unavailable states.  The
            # public result deliberately does not distinguish them by message
            # or expose the profile binding/path.
            result = _landing_probe_result(
                started_at=started_at,
                profile_binding=(
                    LandingProbeProfileBinding.WORKER_PERSISTENT_SESSION
                    if session is not None
                    else LandingProbeProfileBinding.UNAVAILABLE
                ),
                expected_host_reached=None,
                search_control_visible=None,
                auth_shell_candidate_status=LandingCandidateStatus.LANDING_PROFILE_UNAVAILABLE,
                login_or_qr_candidate_status=LandingCandidateStatus.LANDING_PROFILE_UNAVAILABLE,
                diagnostic_code=LandingCandidateStatus.LANDING_PROFILE_UNAVAILABLE,
                launch_count=launch_count,
            )
        _emit_landing_probe_telemetry(result, telemetry)
        return result

    probe_landing_selector_proposal = probe_landing_selector_proposals
    probe_internal_landing_selectors = probe_landing_selector_proposals

    @asynccontextmanager
    async def persistent_page(
        self,
        *,
        viewport: dict[str, int] | None = None,
    ) -> AsyncIterator[Any]:
        async with self.persistent_session(viewport=viewport) as session:
            page = await session.new_page()
            try:
                yield page
            finally:
                await session.close_page(page)

    @asynccontextmanager
    async def form_batch(
        self,
        mapping: dict,
        execution_mode: str,
        *,
        headless: bool,
    ) -> AsyncIterator[FormBatch]:
        async with self._playwright_factory() as playwright:
            async def launch_browser():
                return await launch_chromium_with_fallback(
                    playwright.chromium,
                    headless=headless,
                )

            browser = cast(Any, await self._circuit.call(launch_browser))
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
        api_key_provider: Callable[[], str | None] = lambda: _config_value(
            "GEMINI_API_KEY", None
        ),
        model_provider: Callable[[], str] = lambda: _config_value(
            "GEMINI_MODEL", "gemini-3.1-flash-lite"
        ),
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
    def production(cls, *, profile_path: str | None = None) -> ExternalGateways:
        from googleform_autosubmit.config import Config

        return cls(
            browser=PlaywrightBrowserGateway(
                profile_path=(
                    profile_path
                    or str(Path(Config.BROWSER_PROFILE_PATH).expanduser().resolve())
                ),
                landing_facts_provider_factory=ZaloAuthReadinessAdapter,
            ),
            lms_ocr=GeminiLmsOcrGateway(),
            student_ocr_factory=_default_student_ocr_factory,
        )
