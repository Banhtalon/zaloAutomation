"""Durable two-page local pipeline for one Zalo/BCNB batch."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import inspect
import json
import re
from typing import Any, cast
import unicodedata

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.time_utils import now_utc, parse_timestamp
from db.backup import backup_job
from googleform_autosubmit.config import Config
from logger_utils import log_message
from repositories.job_repository import JobCancelledError
from repositories.zalo_batch_repository import (
    ZaloBatchRepository,
    canonical_target_key,
)
from services.bcnb_safety import verify_checkbox_persisted
from services.zalo_bcnb_service import get_evidence_reference, update_bcnb_reports
from services.zalo_discovery_service import _login_bcnb_if_needed
from services.zalo_safety import open_chat_matches
from services.zalo_scan_evidence import (
    attach_zalo_sync_result,
    build_empty_evidence_result,
    ensure_zalo_history_synced,
    scan_open_zalo_chat,
)
from services.zalo_target_eligibility import split_scan_targets_for_execution
from services.zalo_auth_readiness import AUTH_READINESS_SELECTOR_CONTRACT_VERSION


SYSTEMIC_MARKERS = (
    "auth",
    "captcha",
    "mfa",
    "dom",
    "week",
    "session",
    "profile",
    "circuit",
    "login",
)


# Setup failures are deliberately classified before a worker claims a target.
# Keep these codes stable: they are safe to persist and are the only error
# identifiers emitted by the setup telemetry below.
SETUP_OK = "SETUP_OK"
SELECTOR_READINESS_TIMEOUT = "SELECTOR_READINESS_TIMEOUT"
SETUP_NAVIGATION_ERROR = "SETUP_NAVIGATION_ERROR"
SETUP_SELECTOR_ERROR = "SETUP_SELECTOR_ERROR"
SETUP_SYNC_TIMEOUT = "SETUP_SYNC_TIMEOUT"
SETUP_SYNC_ERROR = "SETUP_SYNC_ERROR"
SETUP_PAGE_CREATE_ERROR = "SETUP_PAGE_CREATE_ERROR"
SETUP_PAGE_CREATE_TIMEOUT = "SETUP_PAGE_CREATE_TIMEOUT"
SETUP_PAGE_CLOSE_ERROR = "SETUP_PAGE_CLOSE_ERROR"
SETUP_PAGE_CLOSE_TIMEOUT = "SETUP_PAGE_CLOSE_TIMEOUT"

# Landing/auth classification is deliberately a pure boundary over injected,
# sanitized facts.  No selector, title, modal, or DOM contract is guessed here.
AUTHENTICATED_READY = "AUTHENTICATED_READY"
LOGIN_OR_QR = "LOGIN_OR_QR"
AUTH_STATE_AMBIGUOUS = "AUTH_STATE_AMBIGUOUS"
LANDING_READINESS_TIMEOUT = "LANDING_READINESS_TIMEOUT"
LANDING_PAGE_CLOSED = "LANDING_PAGE_CLOSED"
LANDING_BLOCKING_MODAL = "LANDING_BLOCKING_MODAL"
LANDING_UNSUPPORTED_BROWSER = "LANDING_UNSUPPORTED_BROWSER"
LANDING_NETWORK_FAILED = "LANDING_NETWORK_FAILED"
LANDING_UNKNOWN_FAIL_CLOSED = "LANDING_UNKNOWN_FAIL_CLOSED"
LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN = "LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN"
LIVE_LOGIN_SELECTOR_CONTRACT_NOT_PROVEN = "LIVE_LOGIN_SELECTOR_CONTRACT_NOT_PROVEN"
AUTH_READINESS_AMBIGUOUS = "AUTH_READINESS_AMBIGUOUS"
AUTH_READINESS_LOADING = "AUTH_READINESS_LOADING"
AUTH_READINESS_LOADING_TIMEOUT = "AUTH_READINESS_LOADING_TIMEOUT"
AUTH_READINESS_SELECTOR_UNSUPPORTED = "AUTH_READINESS_SELECTOR_UNSUPPORTED"
AUTH_READINESS_BROWSER_ERROR = "AUTH_READINESS_BROWSER_ERROR"
AUTH_READINESS_PAGE_CLOSED = "AUTH_READINESS_PAGE_CLOSED"
# Keep the orchestration boundary aligned with the code-sealed adapter's
# approved per-page polling deadline.
LANDING_READINESS_TIMEOUT_SECONDS = 60.0

LANDING_SETUP_CODES = frozenset(
    {
        AUTHENTICATED_READY,
        LOGIN_OR_QR,
        AUTH_STATE_AMBIGUOUS,
        LANDING_READINESS_TIMEOUT,
        LANDING_PAGE_CLOSED,
        LANDING_BLOCKING_MODAL,
        LANDING_UNSUPPORTED_BROWSER,
        LANDING_NETWORK_FAILED,
        LANDING_UNKNOWN_FAIL_CLOSED,
        LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN,
        LIVE_LOGIN_SELECTOR_CONTRACT_NOT_PROVEN,
        AUTH_READINESS_AMBIGUOUS,
        AUTH_READINESS_LOADING,
        AUTH_READINESS_LOADING_TIMEOUT,
        AUTH_READINESS_SELECTOR_UNSUPPORTED,
        AUTH_READINESS_BROWSER_ERROR,
        AUTH_READINESS_PAGE_CLOSED,
    }
)


class LandingResultCode(str, Enum):
    AUTHENTICATED_READY = AUTHENTICATED_READY
    LOGIN_OR_QR = LOGIN_OR_QR
    AUTH_STATE_AMBIGUOUS = AUTH_STATE_AMBIGUOUS
    LANDING_READINESS_TIMEOUT = LANDING_READINESS_TIMEOUT
    LANDING_PAGE_CLOSED = LANDING_PAGE_CLOSED
    LANDING_BLOCKING_MODAL = LANDING_BLOCKING_MODAL
    LANDING_UNSUPPORTED_BROWSER = LANDING_UNSUPPORTED_BROWSER
    LANDING_NETWORK_FAILED = LANDING_NETWORK_FAILED
    LANDING_UNKNOWN_FAIL_CLOSED = LANDING_UNKNOWN_FAIL_CLOSED
    LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN = LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN
    LIVE_LOGIN_SELECTOR_CONTRACT_NOT_PROVEN = LIVE_LOGIN_SELECTOR_CONTRACT_NOT_PROVEN
    AUTH_READINESS_AMBIGUOUS = AUTH_READINESS_AMBIGUOUS
    AUTH_READINESS_LOADING = AUTH_READINESS_LOADING
    AUTH_READINESS_LOADING_TIMEOUT = AUTH_READINESS_LOADING_TIMEOUT
    AUTH_READINESS_SELECTOR_UNSUPPORTED = AUTH_READINESS_SELECTOR_UNSUPPORTED
    AUTH_READINESS_BROWSER_ERROR = AUTH_READINESS_BROWSER_ERROR
    AUTH_READINESS_PAGE_CLOSED = AUTH_READINESS_PAGE_CLOSED


LandingResult = LandingResultCode

# Per-target search failures are deliberately distinct from worker setup
# failures.  These identifiers are persisted instead of exposing a raw
# Playwright exception (which can contain DOM, URL, or query data).
TARGET_SEARCH_CLEAR_TIMEOUT = "TARGET_SEARCH_CLEAR_TIMEOUT"
TARGET_SEARCH_CLEAR_ERROR = "TARGET_SEARCH_CLEAR_ERROR"
TARGET_SEARCH_FILL_TIMEOUT = "TARGET_SEARCH_FILL_TIMEOUT"
TARGET_SEARCH_FILL_ERROR = "TARGET_SEARCH_FILL_ERROR"
TARGET_SEARCH_RESULT_TIMEOUT = "TARGET_SEARCH_RESULT_TIMEOUT"
TARGET_SEARCH_RESULT_ERROR = "TARGET_SEARCH_RESULT_ERROR"

SETUP_TIMEOUT_MS = 30_000
SETUP_SYNC_TIMEOUT_SECONDS = 30.0
# Keep both names available for hermetic callers that patch a timeout. The
# helpers below use the smaller value so either bounded test hook is honored.
PAGE_CREATE_TIMEOUT_SECONDS = 30.0
PAGE_CLOSE_TIMEOUT_SECONDS = 10.0
SETUP_PAGE_CREATE_TIMEOUT_SECONDS = PAGE_CREATE_TIMEOUT_SECONDS
SETUP_PAGE_CLOSE_TIMEOUT_SECONDS = PAGE_CLOSE_TIMEOUT_SECONDS
MAX_SETUP_ATTEMPTS = 2
MAX_SELECTOR_COUNT = 1
MAX_TELEMETRY_ELAPSED_MS = 10 * 60 * 1000
# Each target operation has its own finite boundary.  Keep these names
# separate so an operation cannot accidentally inherit a browser default.
TARGET_SEARCH_CLEAR_TIMEOUT_SECONDS = 5.0
TARGET_SEARCH_FILL_TIMEOUT_SECONDS = 5.0
TARGET_SEARCH_RESULT_TIMEOUT_SECONDS = 5.0
TARGET_SEARCH_IDLE_SECONDS = 2.0
MAX_TARGET_RETRY_ATTEMPTS = 2

ZALO_JOB_PHASE = "ZALO_SCAN"
ZALO_JOB_REASON_QUEUED = "QUEUED"
ZALO_JOB_REASON_COMPLETED = "COMPLETED"
ZALO_JOB_REASON_TARGETS_NEED_REVIEW = "TARGETS_NEED_REVIEW"
ZALO_JOB_REASON_NO_TARGET_ATTEMPTED = "NO_TARGET_ATTEMPTED"
ZALO_JOB_REASON_UNKNOWN_FAIL_CLOSED = "UNKNOWN_FAIL_CLOSED"
ZALO_JOB_REASON_BATCH_ABNORMAL_THRESHOLD = "BATCH_ABNORMAL_THRESHOLD"
ZALO_JOB_REASON_BATCH_SYSTEMIC_THRESHOLD = "BATCH_SYSTEMIC_THRESHOLD"
ZALO_JOB_REASON_TARGET_SEARCH_TIMEOUT = "TARGET_SEARCH_TIMEOUT"
ZALO_JOB_REASON_TARGET_SEARCH_ERROR = "TARGET_SEARCH_ERROR"

SETUP_CODES = frozenset(
    {
        SETUP_OK,
        SELECTOR_READINESS_TIMEOUT,
        SETUP_NAVIGATION_ERROR,
        SETUP_SELECTOR_ERROR,
        SETUP_SYNC_TIMEOUT,
        SETUP_SYNC_ERROR,
        SETUP_PAGE_CREATE_ERROR,
        SETUP_PAGE_CREATE_TIMEOUT,
        SETUP_PAGE_CLOSE_ERROR,
        SETUP_PAGE_CLOSE_TIMEOUT,
    }
)

TARGET_SEARCH_TIMEOUT_CODES = frozenset(
    {
        TARGET_SEARCH_CLEAR_TIMEOUT,
        TARGET_SEARCH_FILL_TIMEOUT,
        TARGET_SEARCH_RESULT_TIMEOUT,
    }
)

TARGET_SEARCH_ERROR_CODES = frozenset(
    {
        TARGET_SEARCH_CLEAR_ERROR,
        TARGET_SEARCH_FILL_ERROR,
        TARGET_SEARCH_RESULT_ERROR,
    }
)

_ZALO_JOB_REASON_CODES = frozenset(
    {
        ZALO_JOB_REASON_QUEUED,
        ZALO_JOB_REASON_COMPLETED,
        ZALO_JOB_REASON_TARGETS_NEED_REVIEW,
        ZALO_JOB_REASON_NO_TARGET_ATTEMPTED,
        ZALO_JOB_REASON_UNKNOWN_FAIL_CLOSED,
        ZALO_JOB_REASON_BATCH_ABNORMAL_THRESHOLD,
        ZALO_JOB_REASON_BATCH_SYSTEMIC_THRESHOLD,
        ZALO_JOB_REASON_TARGET_SEARCH_TIMEOUT,
        ZALO_JOB_REASON_TARGET_SEARCH_ERROR,
        *LANDING_SETUP_CODES,
        *SETUP_CODES,
    }
)


class ScanPageState(str, Enum):
    """Safe, finite page states allowed in setup telemetry."""

    NAVIGATING = "NAVIGATING"
    SELECTOR_WAIT = "SELECTOR_WAIT"
    SELECTOR_READY = "SELECTOR_READY"
    SYNCING = "SYNCING"
    READY = "READY"
    PAGE_CREATE_FAILED = "PAGE_CREATE_FAILED"
    PAGE_CLOSE_FAILED = "PAGE_CLOSE_FAILED"
    PAGE_CLOSE_TIMEOUT = "PAGE_CLOSE_TIMEOUT"


SAFE_PAGE_STATES = frozenset(state.value for state in ScanPageState)


class ZaloSetupError(RuntimeError):
    """Typed, sanitized setup failure for one scan worker."""

    def __init__(
        self,
        code: str,
        *,
        page_state: ScanPageState,
        selector_count: int = 0,
        selector_ready: bool = False,
    ) -> None:
        self.code = str(code)
        self.page_state = page_state
        self.selector_count = max(0, min(MAX_SELECTOR_COUNT, int(selector_count)))
        self.selector_ready = bool(selector_ready)
        # Do not retain or expose the underlying exception message. Browser
        # errors may contain URLs, DOM fragments or other sensitive details.
        super().__init__(self.code)

    @property
    def safe_message(self) -> str:
        return "Zalo scan setup không đạt; batch đã dừng an toàn."


class SelectorReadinessTimeoutError(ZaloSetupError):
    """The required Zalo search control did not become ready in time."""

    def __init__(
        self,
        *,
        page_state: ScanPageState = ScanPageState.SELECTOR_WAIT,
    ) -> None:
        super().__init__(
            SELECTOR_READINESS_TIMEOUT,
            page_state=page_state,
            selector_count=0,
            selector_ready=False,
        )


@dataclass(frozen=True, slots=True)
class LandingFacts:
    """Synthetic, sanitized landing facts accepted by the classifier."""

    authenticated_shell: bool = False
    search_control: bool = False
    login_or_qr_marker: bool = False
    selector_detached: bool = False
    readiness_timeout: bool = False
    page_closed: bool = False
    blocking_modal: bool = False
    unsupported_browser: bool = False
    network_error: bool = False
    # Canonical runtime schema fields.  The legacy aliases above remain for
    # existing hermetic fixtures and are never used to infer expiry.
    phase: str | None = None
    elapsed_ms: int | None = None
    page_state: str | None = None
    expected_host_reached: bool | None = None
    authenticated_shell_visible: bool | None = None
    search_control_visible: bool | None = None
    login_or_qr_visible: bool | None = None
    body_nav_visible: bool | None = None
    body_nav_count: int | None = None
    search_control_count: int | None = None
    reason_code: str | None = None
    blocking_modal_visible: bool | None = None
    unsupported_browser_visible: bool | None = None
    network_error_visible: bool | None = None
    selector_contract_version: str | None = None
    diagnostic_code: str | None = None


class LandingPageClosedError(RuntimeError):
    """Typed page-lifecycle signal for the landing readiness boundary."""


class LandingBlockingModalError(RuntimeError):
    """Typed blocking-modal signal for the landing readiness boundary."""


class LandingUnsupportedBrowserError(RuntimeError):
    """Typed unsupported-browser signal for the landing readiness boundary."""


class LandingNetworkError(RuntimeError):
    """Typed network/error-page signal for the landing readiness boundary."""


class LandingSelectorDetachedError(RuntimeError):
    """Typed selector-detach signal that remains unknown/fail-closed."""


def _fact_bool(
    facts: LandingFacts | Mapping[str, object],
    names: tuple[str, ...],
) -> tuple[bool, bool]:
    """Return ``(value, valid)`` without coercing arbitrary truthy values."""
    if isinstance(facts, LandingFacts):
        is_runtime = any(
            value is not None
            for value in (
                facts.phase,
                facts.selector_contract_version,
                facts.diagnostic_code,
            )
        )
        if is_runtime:
            names = tuple(
                name
                for name in names
                if name
                not in {
                    "authenticated_shell",
                    "search_control",
                    "login_or_qr_marker",
                    "selector_detached",
                    "readiness_timeout",
                    "page_closed",
                    "blocking_modal",
                    "unsupported_browser",
                    "network_error",
                }
            )
        unknown = False
        for name in names:
            if hasattr(facts, name):
                value = getattr(facts, name)
                if value is None:
                    unknown = True
                    continue
                return (value, isinstance(value, bool))
        return False, not unknown
    if not isinstance(facts, Mapping):
        return False, False
    found = False
    unknown = False
    value = False
    for name in names:
        if name not in facts:
            continue
        found = True
        candidate = facts[name]
        if candidate is None:
            unknown = True
            continue
        if not isinstance(candidate, bool):
            return False, False
        value = candidate
        break
    if unknown:
        return False, False
    return value if found else False, True


def _classify_runtime_facts(facts: Mapping[str, object]) -> LandingResultCode | None:
    """Classify the exact provider schema without trusting raw diagnostics."""
    # The sealed selector contract is sufficient to classify the positive
    # conjunction; telemetry fields remain additive and are validated by the
    # adapter itself.  Keeping this boundary tolerant of older sanitized
    # payloads preserves fail-closed behavior without requiring raw page data.
    required = {"selector_contract_version", "diagnostic_code"}
    if not required.issubset(facts):
        return None
    diagnostic = facts.get("diagnostic_code")
    if not isinstance(diagnostic, str):
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    try:
        diagnostic_code = LandingResultCode(diagnostic)
    except ValueError:
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED

    if facts.get("selector_contract_version") == AUTH_READINESS_SELECTOR_CONTRACT_VERSION:
        if diagnostic_code == LandingResultCode.AUTHENTICATED_READY:
            expected_host = facts.get("expected_host_reached") is True
            nav_visible = facts.get("body_nav_visible") is True
            search_visible = facts.get("search_control_visible") is True
            nav_count = facts.get("body_nav_count")
            search_count = facts.get("search_control_count")
            if (
                expected_host
                and nav_visible
                and search_visible
                and isinstance(nav_count, int)
                and not isinstance(nav_count, bool)
                and nav_count == 1
                and isinstance(search_count, int)
                and not isinstance(search_count, bool)
                and search_count == 1
            ):
                return diagnostic_code
            return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
        if diagnostic_code in {
            LandingResultCode.AUTH_READINESS_AMBIGUOUS,
            LandingResultCode.AUTH_READINESS_LOADING,
            LandingResultCode.AUTH_READINESS_LOADING_TIMEOUT,
            LandingResultCode.AUTH_READINESS_SELECTOR_UNSUPPORTED,
            LandingResultCode.AUTH_READINESS_BROWSER_ERROR,
            LandingResultCode.AUTH_READINESS_PAGE_CLOSED,
        }:
            return diagnostic_code
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED

    def strict_bool(name: str) -> bool | None:
        value = facts.get(name)
        return value if isinstance(value, bool) else None

    authenticated_shell = strict_bool("authenticated_shell_visible")
    search_control = strict_bool("search_control_visible")
    login_or_qr = strict_bool("login_or_qr_visible")
    if diagnostic_code == LandingResultCode.AUTHENTICATED_READY:
        if authenticated_shell is True and search_control is True and login_or_qr is False:
            return diagnostic_code
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    if diagnostic_code == LandingResultCode.AUTH_STATE_AMBIGUOUS:
        if authenticated_shell is True and login_or_qr is True:
            return diagnostic_code
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    if diagnostic_code == LandingResultCode.LOGIN_OR_QR:
        return (
            diagnostic_code
            if login_or_qr is True
            else LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
        )
    return diagnostic_code


def classify_landing_readiness(
    facts: LandingFacts | Mapping[str, object] | None,
) -> LandingResultCode:
    """Classify only positive injected facts; absence is never expiry."""
    if facts is None:
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    if not isinstance(facts, (LandingFacts, Mapping)):
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    if isinstance(facts, Mapping):
        runtime_result = _classify_runtime_facts(facts)
        if runtime_result is not None:
            return runtime_result

    page_closed, page_valid = _fact_bool(
        facts,
        ("page_closed", "page_is_closed"),
    )
    blocking_modal, modal_valid = _fact_bool(
        facts,
        ("blocking_modal_visible", "blocking_modal", "modal_visible"),
    )
    unsupported, unsupported_valid = _fact_bool(
        facts,
        ("unsupported_browser_visible", "unsupported_browser"),
    )
    network_error, network_valid = _fact_bool(
        facts,
        ("network_error_visible", "network_error", "network_or_error_state", "error_page"),
    )
    readiness_timeout, timeout_valid = _fact_bool(
        facts,
        ("readiness_timeout", "selector_timeout", "readiness_hang"),
    )
    selector_detached, detached_valid = _fact_bool(
        facts,
        ("selector_detached", "search_selector_detached"),
    )
    authenticated_shell, shell_valid = _fact_bool(
        facts,
        ("authenticated_shell_visible", "authenticated_shell", "shell_visible"),
    )
    search_control, search_valid = _fact_bool(
        facts,
        ("search_control_visible", "search_control", "search_ready"),
    )
    login_or_qr, login_valid = _fact_bool(
        facts,
        ("login_or_qr_visible", "login_or_qr_marker", "login_qr_visible"),
    )
    if not all(
        (
            page_valid,
            modal_valid,
            unsupported_valid,
            network_valid,
            timeout_valid,
            detached_valid,
            shell_valid,
            search_valid,
            login_valid,
        )
    ):
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    if page_closed:
        return LandingResultCode.LANDING_PAGE_CLOSED
    if network_error:
        return LandingResultCode.LANDING_NETWORK_FAILED
    if unsupported:
        return LandingResultCode.LANDING_UNSUPPORTED_BROWSER
    if blocking_modal:
        return LandingResultCode.LANDING_BLOCKING_MODAL
    if readiness_timeout:
        return LandingResultCode.LANDING_READINESS_TIMEOUT
    if authenticated_shell and login_or_qr:
        return LandingResultCode.AUTH_STATE_AMBIGUOUS
    if login_or_qr:
        return LandingResultCode.LOGIN_OR_QR
    if selector_detached:
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    if authenticated_shell and search_control:
        return LandingResultCode.AUTHENTICATED_READY
    return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED


async def probe_landing_readiness(
    facts_provider: (
        Callable[
            [],
            Awaitable[LandingFacts | Mapping[str, object] | None]
            | LandingFacts
            | Mapping[str, object]
            | None,
        ]
        | LandingFacts
        | Mapping[str, object]
        | None
    ),
    *,
    timeout_seconds: float = LANDING_READINESS_TIMEOUT_SECONDS,
) -> LandingResultCode:
    """Evaluate injected facts with one finite, browser-free timeout boundary."""
    try:
        value = facts_provider() if callable(facts_provider) else facts_provider
        if inspect.isawaitable(value):
            value = await asyncio.wait_for(
                value,
                timeout=max(0.001, float(timeout_seconds)),
            )
    except LandingPageClosedError:
        return LandingResultCode.LANDING_PAGE_CLOSED
    except LandingBlockingModalError:
        return LandingResultCode.LANDING_BLOCKING_MODAL
    except LandingUnsupportedBrowserError:
        return LandingResultCode.LANDING_UNSUPPORTED_BROWSER
    except LandingNetworkError:
        return LandingResultCode.LANDING_NETWORK_FAILED
    except LandingSelectorDetachedError:
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    except (asyncio.TimeoutError, TimeoutError):
        return LandingResultCode.LANDING_READINESS_TIMEOUT
    except Exception:
        return LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    return classify_landing_readiness(value)


async def _invoke_landing_provider(provider: Any, page: Any) -> Any:
    """Invoke a session provider with a page when its contract accepts one."""
    if not callable(provider):
        return provider
    try:
        signature = inspect.signature(provider)
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
        value = provider(page) if positional else provider()
    else:
        value = provider(page)
    if inspect.isawaitable(value):
        return await value
    return value


def landing_result_to_setup_error(
    result: LandingResultCode | str | None,
    *,
    page_state: ScanPageState = ScanPageState.SELECTOR_WAIT,
) -> ZaloSetupError | None:
    """Map unproven landing/auth states to a typed fail-closed setup error."""
    if result is None:
        result_code = LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    elif isinstance(result, LandingResultCode):
        result_code = result
    else:
        try:
            result_code = LandingResultCode(str(result))
        except ValueError:
            result_code = LandingResultCode.LANDING_UNKNOWN_FAIL_CLOSED
    if result_code == LandingResultCode.AUTHENTICATED_READY:
        return None
    return ZaloSetupError(result_code.value, page_state=page_state)


# Alias used by callers that describe the boundary as a classifier adapter.
map_landing_result_to_setup_error = landing_result_to_setup_error
classify_landing_auth = classify_landing_readiness


class TargetSearchError(RuntimeError):
    """Typed, sanitized failure for one target's search boundary."""

    def __init__(
        self,
        code: str,
        *,
        phase: str,
        attempt: int,
    ) -> None:
        self.code = str(code)
        self.phase = str(phase)
        self.attempt = max(1, int(attempt))
        self.page_state = ScanPageState.READY
        self.selector_count = 1
        self.selector_ready = True
        # Do not retain the browser exception text in the public message.
        super().__init__(self.code)

    @property
    def safe_message(self) -> str:
        return "Zalo search không đủ bằng chứng; target cần kiểm tra thủ công."


# Compatibility aliases make the typed boundary easy for callers/tests to
# discover without creating a second error hierarchy.
SetupError = ZaloSetupError
SelectorReadinessTimeout = SelectorReadinessTimeoutError
SelectorReadinessSetupError = SelectorReadinessTimeoutError


async def _gather_workers(coroutines) -> None:
    """Cancel and await siblings, then preserve the original worker exception."""
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def prepare_frozen_scan_execution(
    *,
    job_id: int,
    scan_plan: dict[str, Any],
    scan_started_at,
    runtime,
    job_repo,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool] | None:
    """Validate expiry and shrink, but never expand, the admitted target set."""
    await job_repo.ensure_not_cancelled(job_id)
    plan = dict(scan_plan)
    reconciliation_only = False
    expires_at = parse_timestamp(plan.get("expires_at"))
    if expires_at is None:
        observed_at = parse_timestamp(plan.get("observed_at"))
        if observed_at is not None:
            expires_at = observed_at + timedelta(
                minutes=int(getattr(Config, "ZALO_PLAN_MAX_AGE_MINUTES", 30))
            )
        elif runtime is not None:
            await job_repo.update_job(
                job_id,
                status="FAILED",
                message="Kế hoạch BCNB thiếu hạn dùng; hãy Dò tìm lại.",
                progress=100,
            )
            return None
    if expires_at is not None and now_utc() >= expires_at:
        reconciliation_only = bool(
            runtime is not None
            and await ZaloBatchRepository().list_for_job(job_id)
        )
        if not reconciliation_only:
            await job_repo.update_job(
                job_id,
                status="FAILED",
                message="Kế hoạch BCNB đã quá 30 phút trong hàng đợi; hãy Dò tìm lại.",
                progress=100,
            )
            return None

    admitted_targets = list(plan.get("targets") or [])
    previously_deferred = list(plan.get("deferred_targets") or [])
    selected_keys_raw = plan.get("selected_target_keys")
    if isinstance(selected_keys_raw, list):
        selected_key_set = {
            str(item).strip() for item in selected_keys_raw if str(item).strip()
        }
        admitted_targets = [
            target
            for target in admitted_targets
            if isinstance(target, dict)
            and canonical_target_key(target) in selected_key_set
        ]
        previously_deferred = [
            target
            for target in previously_deferred
            if isinstance(target, dict)
            and canonical_target_key(target) in selected_key_set
        ]
    ready_targets, newly_deferred = split_scan_targets_for_execution(
        admitted_targets,
        latest_week=plan.get("latest_week"),
        now=scan_started_at,
    )
    plan["targets"] = ready_targets
    plan["deferred_targets"] = [*previously_deferred, *newly_deferred]
    if newly_deferred:
        log_message(
            f"Hoãn {len(newly_deferred)} lớp chưa đến giờ học; không quét và không tick BCNB.",
            event="zalo.scan.targets_deferred",
        )
    groups = [
        {
            "TenNhom": target.get("group_name", ""),
            "NguoiPhuTrach": target.get("person", "Chưa xác định"),
            "NgayHoc": target.get("class_date", ""),
            "scan_target": target,
        }
        for target in ready_targets
        if isinstance(target, dict) and str(target.get("group_name", "")).strip()
    ]
    if not groups and not reconciliation_only:
        if newly_deferred:
            await job_repo.update_job(
                job_id,
                status="SUCCESS",
                message=f"Chưa có lớp đủ giờ học để quét; đã hoãn {len(newly_deferred)} target.",
                progress=100,
                results=json.dumps([], ensure_ascii=False),
            )
        else:
            await job_repo.update_job(
                job_id,
                status="FAILED",
                message="Lỗi: Không có dữ liệu lớp học trong Database.",
            )
        return None
    return plan, groups, reconciliation_only


def _is_systemic_error(error: BaseException | str) -> bool:
    text = str(error).casefold()
    return any(marker in text for marker in SYSTEMIC_MARKERS)


def _is_timeout_error(error: BaseException) -> bool:
    return isinstance(error, (PlaywrightTimeoutError, asyncio.TimeoutError, TimeoutError))


def _page_create_timeout_seconds() -> float:
    return max(
        0.001,
        min(
            float(PAGE_CREATE_TIMEOUT_SECONDS),
            float(SETUP_PAGE_CREATE_TIMEOUT_SECONDS),
        ),
    )


def _page_close_timeout_seconds() -> float:
    return max(
        0.001,
        min(
            float(PAGE_CLOSE_TIMEOUT_SECONDS),
            float(SETUP_PAGE_CLOSE_TIMEOUT_SECONDS),
        ),
    )


async def _new_scan_page(session):
    """Create one worker page with a finite, typed timeout boundary."""
    try:
        return await asyncio.wait_for(
            session.new_page(),
            timeout=_page_create_timeout_seconds(),
        )
    except JobCancelledError:
        raise
    except Exception as exc:
        if _is_timeout_error(exc):
            raise ZaloSetupError(
                SETUP_PAGE_CREATE_TIMEOUT,
                page_state=ScanPageState.PAGE_CREATE_FAILED,
            ) from exc
        raise ZaloSetupError(
            SETUP_PAGE_CREATE_ERROR,
            page_state=ScanPageState.PAGE_CREATE_FAILED,
        ) from exc


async def _close_scan_page(session, page) -> ZaloSetupError | None:
    """Close one page with a finite, sanitized setup boundary."""
    if page is None:
        return None
    try:
        await asyncio.wait_for(
            session.close_page(page),
            timeout=_page_close_timeout_seconds(),
        )
    except JobCancelledError:
        raise
    except Exception as exc:
        if _is_timeout_error(exc):
            return ZaloSetupError(
                SETUP_PAGE_CLOSE_TIMEOUT,
                page_state=ScanPageState.PAGE_CLOSE_TIMEOUT,
            )
        return ZaloSetupError(
            SETUP_PAGE_CLOSE_ERROR,
            page_state=ScanPageState.PAGE_CLOSE_FAILED,
        )
    return None


async def _setup_scan_page(
    page,
    *,
    job_id: int,
    job_repo,
    landing_facts: LandingFacts | Mapping[str, object] | None = None,
    landing_facts_provider: (
        Callable[
            [],
            Awaitable[LandingFacts | Mapping[str, object] | None]
            | LandingFacts
            | Mapping[str, object]
            | None,
        ]
        | None
    ) = None,
) -> dict[str, Any]:
    """Set up one page, preserving a typed boundary for each setup phase."""
    page_state = ScanPageState.NAVIGATING
    try:
        await page.goto("https://chat.zalo.me/", timeout=SETUP_TIMEOUT_MS)
    except JobCancelledError:
        raise
    except Exception as exc:
        raise ZaloSetupError(
            SETUP_NAVIGATION_ERROR,
            page_state=page_state,
        ) from exc

    if landing_facts_provider is not None:
        landing_result = await probe_landing_readiness(
            landing_facts_provider,
            timeout_seconds=LANDING_READINESS_TIMEOUT_SECONDS,
        )
        landing_error = landing_result_to_setup_error(
            landing_result,
            page_state=ScanPageState.SELECTOR_WAIT,
        )
        if landing_error is not None:
            raise landing_error
    elif landing_facts is not None:
        landing_result = classify_landing_readiness(landing_facts)
        landing_error = landing_result_to_setup_error(
            landing_result,
            page_state=ScanPageState.SELECTOR_WAIT,
        )
        if landing_error is not None:
            raise landing_error
    else:
        raise ZaloSetupError(
            LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN,
            page_state=ScanPageState.SELECTOR_WAIT,
        )

    page_state = ScanPageState.SELECTOR_WAIT
    try:
        await page.wait_for_selector(
            "#contact-search-input",
            timeout=SETUP_TIMEOUT_MS,
        )
    except JobCancelledError:
        raise
    except Exception as exc:
        if _is_timeout_error(exc):
            raise SelectorReadinessTimeoutError(page_state=page_state) from exc
        raise ZaloSetupError(
            SETUP_SELECTOR_ERROR,
            page_state=page_state,
        ) from exc

    page_state = ScanPageState.SELECTOR_READY
    try:
        page_state = ScanPageState.SYNCING
        sync_result = await asyncio.wait_for(
            ensure_zalo_history_synced(
                page,
                ensure_not_cancelled=lambda: job_repo.ensure_not_cancelled(job_id),
            ),
            timeout=SETUP_SYNC_TIMEOUT_SECONDS,
        )
    except JobCancelledError:
        raise
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise ZaloSetupError(
            SETUP_SYNC_TIMEOUT,
            page_state=page_state,
            selector_count=1,
            selector_ready=True,
        ) from exc
    except Exception as exc:
        raise ZaloSetupError(
            SETUP_SYNC_ERROR,
            page_state=page_state,
            selector_count=1,
            selector_ready=True,
        ) from exc
    return sync_result


def _setup_telemetry_fields(
    *,
    job_id: int,
    worker_id: str,
    started_at: float,
    page_state: ScanPageState,
    selector_count: int,
    selector_ready: bool,
    error_code: str,
) -> dict[str, str | int | bool]:
    elapsed_ms = int(
        max(0.0, asyncio.get_running_loop().time() - started_at) * 1000
    )
    return {
        "job_id": int(job_id),
        "worker_id": str(worker_id),
        "elapsed_ms": min(MAX_TELEMETRY_ELAPSED_MS, elapsed_ms),
        "page_state": page_state.value,
        "selector_count": max(0, min(MAX_SELECTOR_COUNT, int(selector_count))),
        "selector_ready": bool(selector_ready),
        "error_code": str(error_code),
    }


def _emit_setup_telemetry(
    *,
    job_id: int,
    worker_id: str,
    started_at: float,
    page_state: ScanPageState,
    selector_count: int,
    selector_ready: bool,
    error_code: str,
) -> dict[str, str | int | bool]:
    """Emit only bounded, non-content setup facts."""
    fields = _setup_telemetry_fields(
        job_id=job_id,
        worker_id=worker_id,
        started_at=started_at,
        page_state=page_state,
        selector_count=selector_count,
        selector_ready=selector_ready,
        error_code=error_code,
    )
    try:
        log_message(
            "Zalo scan setup telemetry",
            event="zalo.scan.setup_telemetry",
            **cast(Any, fields),
        )
    except Exception:
        # Telemetry must never turn a safe halt into an untyped worker crash.
        pass
    return fields


def _emit_target_telemetry(
    *,
    job_id: int,
    worker_id: str,
    phase: str,
    attempt: int,
    started_at: float,
    error_code: str,
) -> dict[str, str | int | bool]:
    """Emit only bounded facts about a target search operation."""
    elapsed_ms = int(
        max(0.0, asyncio.get_running_loop().time() - started_at) * 1000
    )
    fields: dict[str, str | int | bool] = {
        "job_id": int(job_id),
        "worker_id": str(worker_id),
        "phase": str(phase),
        "attempt": max(1, min(MAX_TARGET_RETRY_ATTEMPTS, int(attempt))),
        "elapsed_ms": min(MAX_TELEMETRY_ELAPSED_MS, elapsed_ms),
        "page_state": ScanPageState.READY.value,
        "error_code": str(error_code),
    }
    try:
        log_message(
            "Zalo scan target telemetry",
            event="zalo.scan.target_telemetry",
            **cast(Any, fields),
        )
    except Exception:
        # Telemetry is advisory and must never turn a safe target failure into
        # an untyped worker failure.
        pass
    return fields


def _invoke_search_operation(operation, *, timeout_ms: float):
    """Invoke search operation passing timeout_ms only if supported, invoking exactly once."""
    try:
        sig = inspect.signature(operation)
    except (ValueError, TypeError):
        return operation()

    params = list(sig.parameters.values())
    accepts_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
    accepts_timeout_kw = any(p.name == "timeout_ms" for p in params)
    if accepts_kw or accepts_timeout_kw:
        return operation(timeout_ms=timeout_ms)
    if len(params) == 1 and params[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        return operation(timeout_ms)
    return operation()


def _extract_playwright_connections(operation=None, awaitable=None) -> set[Any]:
    conns: set[Any] = set()

    def _is_real_playwright_connection(conn: Any) -> bool:
        return (
            conn is not None
            and type(conn).__name__ == "Connection"
            and getattr(type(conn), "__module__", "").startswith("playwright")
            and isinstance(getattr(conn, "_callbacks", None), dict)
        )

    def _inspect_obj(obj: Any) -> None:
        if obj is None:
            return
        if getattr(type(obj), "__module__", "").startswith("unittest.mock"):
            return
        impl = getattr(obj, "_impl_obj", obj)
        if getattr(type(impl), "__module__", "").startswith("unittest.mock"):
            return
        frame = getattr(impl, "_frame", None)
        chan = getattr(impl, "_channel", None) or getattr(frame, "_channel", None)
        conn = getattr(chan, "_connection", None)
        if _is_real_playwright_connection(conn):
            conns.add(conn)

    if operation is not None:
        if hasattr(operation, "__closure__") and operation.__closure__:
            for cell in operation.__closure__:
                try:
                    _inspect_obj(cell.cell_contents)
                except Exception:
                    pass
        if hasattr(operation, "__self__"):
            try:
                _inspect_obj(operation.__self__)
            except Exception:
                pass

    if awaitable is not None:
        coro = (
            getattr(awaitable, "get_coro", lambda: None)()
            or getattr(awaitable, "_coro", None)
            or awaitable
        )
        if hasattr(coro, "cr_frame") and coro.cr_frame:
            try:
                for val in coro.cr_frame.f_locals.values():
                    _inspect_obj(val)
            except Exception:
                pass

    return conns


def _get_callback_task(cb: Any) -> asyncio.Task | None:
    fut = getattr(cb, "future", None)
    if fut is None:
        return None
    callbacks = getattr(fut, "_callbacks", None)
    if not callbacks:
        return None
    items = callbacks if isinstance(callbacks, list) else [callbacks]
    for item in items:
        fn = item[0] if isinstance(item, tuple) else item
        closure = getattr(fn, "__closure__", None)
        if closure:
            for cell in closure:
                try:
                    val = cell.cell_contents
                    if isinstance(val, asyncio.Task):
                        return val
                except Exception:
                    pass
    return None


def _cleanup_operation_playwright_callbacks(
    conns: set[Any],
    before_cbs: dict[int, set[int]],
    current_task: asyncio.Task | None,
) -> None:
    if not conns:
        return
    for conn in conns:
        callbacks = getattr(conn, "_callbacks", None)
        if not isinstance(callbacks, dict):
            continue
        prev_ids = before_cbs.get(id(conn), set())
        new_ids = set(callbacks.keys()) - prev_ids
        for cid in new_ids:
            cb = callbacks.get(cid)
            if cb is None:
                continue
            # CRITICAL: Only cancel callbacks belonging to this operation/task.
            # Do NOT cancel callbacks belonging to other workers/tasks on a shared connection.
            if current_task is not None and _get_callback_task(cb) is not current_task:
                continue

            fut = getattr(cb, "future", None)
            if fut is not None:
                if not fut.done():
                    fut.cancel()

                def _swallow_exc(f: asyncio.Future) -> None:
                    if not f.cancelled():
                        try:
                            f.exception()
                        except Exception:
                            pass

                fut.add_done_callback(_swallow_exc)


async def _run_target_search_operation(
    operation,
    *,
    job_id: int,
    worker_id: str,
    phase: str,
    attempt: int,
    timeout_seconds: float,
    timeout_code: str,
    error_code: str,
):
    """Run one clear/fill/result operation with a finite typed boundary."""
    started_at = asyncio.get_running_loop().time()
    current_task = asyncio.current_task()
    timeout_seconds_val = max(0.001, float(timeout_seconds))
    timeout_ms = max(1.0, timeout_seconds_val * 1000.0)
    watchdog_timeout = timeout_seconds_val + min(0.5, max(0.05, timeout_seconds_val * 0.1))

    conns = _extract_playwright_connections(operation)
    before_cbs = {id(conn): set(conn._callbacks.keys()) for conn in conns}

    try:
        awaitable = _invoke_search_operation(operation, timeout_ms=timeout_ms)
        more_conns = _extract_playwright_connections(operation, awaitable=awaitable)
        if more_conns:
            for conn in more_conns:
                if id(conn) not in before_cbs:
                    before_cbs[id(conn)] = set(conn._callbacks.keys())
            conns.update(more_conns)

        if inspect.isawaitable(awaitable):
            return await asyncio.wait_for(
                awaitable,
                timeout=watchdog_timeout,
            )
        return awaitable
    except (JobCancelledError, asyncio.CancelledError):
        _cleanup_operation_playwright_callbacks(conns, before_cbs, current_task)
        raise
    except Exception as exc:
        _cleanup_operation_playwright_callbacks(conns, before_cbs, current_task)
        typed_code = timeout_code if _is_timeout_error(exc) else error_code
        _emit_target_telemetry(
            job_id=job_id,
            worker_id=worker_id,
            phase=phase,
            attempt=attempt,
            started_at=started_at,
            error_code=typed_code,
        )
        raise TargetSearchError(
            typed_code,
            phase=phase,
            attempt=attempt,
        ) from exc


def _setup_failure_result(
    target: dict[str, Any],
    *,
    today,
    setup_error: ZaloSetupError,
) -> dict[str, Any]:
    result = _empty_result(
        target,
        today=today,
        reason="Không đủ bằng chứng vì setup Zalo không sẵn sàng; batch đã dừng an toàn.",
        decision="INSUFFICIENT_EVIDENCE",
    )
    result.update(
        {
            "error_code": setup_error.code,
            "setup_error_code": setup_error.code,
            "setup_page_state": setup_error.page_state.value,
            "setup_selector_count": setup_error.selector_count,
            "setup_selector_ready": setup_error.selector_ready,
        }
    )
    return result


def _target_search_failure_result(
    target: dict[str, Any],
    *,
    today,
    error: TargetSearchError | ZaloSetupError,
) -> dict[str, Any]:
    """Build one fail-closed result without retaining browser exception text."""
    result = _empty_result(
        target,
        today=today,
        reason="Không đủ bằng chứng vì thao tác tìm kiếm Zalo không hoàn tất.",
        decision="INSUFFICIENT_EVIDENCE",
    )
    result.update(
        {
            "error_code": error.code,
            "error_message": error.safe_message,
            "target_error_code": error.code,
            "target_error_phase": getattr(error, "phase", "PAGE_LIFECYCLE"),
            "target_error_attempt": getattr(error, "attempt", 1),
        }
    )
    return result


def build_zalo_job_progress(
    *,
    scan_results: list[dict[str, Any]],
    batch_summary: Mapping[str, Mapping[str, int]],
    halt_reason: str | None,
    dry_run: bool,
) -> dict[str, object]:
    """Build the stable job payload/message outcome envelope.

    ``attempted_target_count`` is the number of target rows that produced a
    scan result, rather than the number admitted by the plan.  This keeps a
    setup/auth failure before the first claim explicitly "not scanned" and
    prevents an empty pre-search halt from being presented as partial work.
    """
    scan_counts = batch_summary.get("scan", {})
    write_counts = batch_summary.get("write", {})
    safe_count = sum(
        int(write_counts.get(status, 0))
        for status in ("VERIFIED", "ALREADY_DONE", "DRY_RUN")
    )
    review_count = sum(
        int(write_counts.get(status, 0))
        for status in (
            "FAILED_BEFORE_WRITE",
            "OUTCOME_UNKNOWN",
            "BLOCKED_BATCH_HALT",
        )
    )
    attempted = max(0, len(scan_results))
    raw_total = batch_summary.get("total")
    total_targets = (
        int(raw_total)
        if isinstance(raw_total, (int, float))
        else sum(int(v) for v in scan_counts.values())
    )
    if total_targets == 0:
        total_targets = max(attempted, sum(int(v) for v in write_counts.values()))

    verified_submitted_count = sum(
        1
        for r in scan_results
        if str(r.get("evidence_state") or "")
        in {
            "SUBMITTED_VERIFIED",
            "SUBMITTED_LATE_VERIFIED",
        }
    )
    exempt_count = sum(
        1
        for r in scan_results
        if str(r.get("evidence_state") or "") == "LESSON_14_EXEMPTION"
    )
    verified_not_found_count = sum(
        1
        for r in scan_results
        if str(r.get("evidence_state") or "") == "NOT_FOUND_VERIFIED"
    )
    insufficient_or_error_count = sum(
        1
        for r in scan_results
        if str(r.get("evidence_state") or "")
        not in {
            "SUBMITTED_VERIFIED",
            "SUBMITTED_LATE_VERIFIED",
            "LESSON_14_EXEMPTION",
            "NOT_FOUND_VERIFIED",
        }
    )

    supplied_reason = str(halt_reason or "").strip()
    if supplied_reason in _ZALO_JOB_REASON_CODES:
        safe_halt_reason = supplied_reason
    elif supplied_reason in TARGET_SEARCH_TIMEOUT_CODES or (
        supplied_reason.startswith("TARGET_SEARCH_") and supplied_reason.endswith("_TIMEOUT")
    ):
        safe_halt_reason = ZALO_JOB_REASON_TARGET_SEARCH_TIMEOUT
    elif supplied_reason in TARGET_SEARCH_ERROR_CODES or (
        supplied_reason.startswith("TARGET_SEARCH_") and supplied_reason.endswith("_ERROR")
    ):
        safe_halt_reason = ZALO_JOB_REASON_TARGET_SEARCH_ERROR
    elif supplied_reason in SETUP_CODES:
        safe_halt_reason = supplied_reason
    else:
        safe_halt_reason = ZALO_JOB_REASON_UNKNOWN_FAIL_CLOSED

    if attempted == 0:
        reason_code = safe_halt_reason or ZALO_JOB_REASON_NO_TARGET_ATTEMPTED
        if reason_code == ZALO_JOB_REASON_UNKNOWN_FAIL_CLOSED and not supplied_reason:
            reason_code = ZALO_JOB_REASON_NO_TARGET_ATTEMPTED
        final_status = "FAILED"
        message = f"Chưa quét được: {reason_code}."
    elif review_count:
        reason_code = safe_halt_reason or ZALO_JOB_REASON_TARGETS_NEED_REVIEW
        if reason_code == ZALO_JOB_REASON_UNKNOWN_FAIL_CLOSED and not supplied_reason:
            reason_code = ZALO_JOB_REASON_TARGETS_NEED_REVIEW
        final_status = "FAILED" if safe_count == 0 else "PARTIAL_SUCCESS"
        if supplied_reason:
            exempt_clause = f", {exempt_count} miễn báo cáo" if exempt_count else ""
            message = (
                f"Đợt quét dừng do {reason_code}: "
                f"đã quét {attempted}/{total_targets} target "
                f"({verified_submitted_count} có báo cáo"
                f"{exempt_clause}, "
                f"{verified_not_found_count} chưa có báo cáo, "
                f"{insufficient_or_error_count} thiếu bằng chứng/lỗi), "
                f"{safe_count} target đủ điều kiện ghi."
            )
        else:
            message = (
                f"Hoàn tất một phần: {safe_count} target an toàn, "
                f"{review_count} target cần xem trạng thái chi tiết."
            )
    else:
        reason_code = ZALO_JOB_REASON_COMPLETED
        final_status = "SUCCESS"
        message = (
            "Hoàn tất mô phỏng bằng 2 page, chưa thay đổi BCNB."
            if dry_run
            else "Hoàn tất và đã readback từng target BCNB."
        )

    return {
        "phase": ZALO_JOB_PHASE,
        "reason_code": reason_code,
        "total_targets": total_targets,
        "attempted_target_count": attempted,
        "verified_submitted_count": verified_submitted_count,
        "exempt_count": exempt_count,
        "verified_not_found_count": verified_not_found_count,
        "insufficient_or_error_count": insufficient_or_error_count,
        "write_eligible_count": safe_count,
        "safe_count": safe_count,
        "halt_reason": supplied_reason or None,
        "status": final_status,
        "message": message,
        "items": list(scan_results),
        "scan_counts": dict(scan_counts),
        "write_counts": dict(write_counts),
    }


@dataclass(slots=True)
class BatchFailurePolicy:
    total_targets: int
    systemic_limit: int = 3
    abnormal_percent_limit: int = 20
    consecutive_systemic: int = 0
    abnormal_targets: int = 0
    halt_reason: str | None = None
    halted: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def halt(self, reason: str) -> bool:
        """Force a fail-closed batch halt while preserving the first reason."""
        async with self._lock:
            if self.halt_reason is None:
                self.halt_reason = reason or "Batch halted"
            self.halted.set()
            return True

    async def record(self, *, abnormal: bool, systemic: bool, reason: str = "") -> bool:
        async with self._lock:
            if abnormal:
                self.abnormal_targets += 1
            if systemic:
                self.consecutive_systemic += 1
            else:
                self.consecutive_systemic = 0
            abnormal_limit_hit = (
                self.total_targets > 0
                and self.abnormal_targets * 100
                > self.total_targets * self.abnormal_percent_limit
            )
            systemic_limit_hit = self.consecutive_systemic >= self.systemic_limit
            if systemic_limit_hit:
                if self.halt_reason is None:
                    self.halt_reason = ZALO_JOB_REASON_BATCH_SYSTEMIC_THRESHOLD
                self.halted.set()
            elif abnormal_limit_hit:
                if self.halt_reason is None:
                    self.halt_reason = ZALO_JOB_REASON_BATCH_ABNORMAL_THRESHOLD
                self.halted.set()
            return self.halted.is_set()


def _empty_result(
    target: dict[str, Any],
    *,
    today,
    reason: str,
    decision: str | None = None,
) -> dict[str, Any]:
    result = build_empty_evidence_result(
        group=str(target.get("group_name") or ""),
        person=str(target.get("person") or "Chưa xác định"),
        actual_group_name=None,
        scanned_at=now_utc(),
        today=today,
        reason=reason,
        classifier_decision=decision or "INSUFFICIENT_EVIDENCE",
    )
    result["scan_target"] = target
    return result


async def _wait_for_target_header(
    locator,
    group_name: str,
    ensure_not_cancelled,
    *,
    timeout_seconds: float | None = None,
) -> str:
    deadline = None
    if timeout_seconds is not None:
        deadline = asyncio.get_running_loop().time() + max(0.001, float(timeout_seconds))
    while True:
        await ensure_not_cancelled()
        step_timeout_ms = 500.0
        if deadline is not None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise PlaywrightTimeoutError("Target header wait timeout")
            step_timeout_ms = min(500.0, max(1.0, remaining * 1000.0))
        try:
            header_text = await locator.first.inner_text(timeout=step_timeout_ms)
        except PlaywrightTimeoutError:
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.2)
            continue
        if open_chat_matches(group_name, header_text):
            return header_text.strip()
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            raise PlaywrightTimeoutError("Target header wait timeout")
        await asyncio.sleep(0.2)


async def _scan_one_target(
    page,
    *,
    job_id: int,
    target: dict[str, Any],
    sync_result: dict[str, Any],
    screenshots_dir: str,
    today,
    job_repo,
    worker_id: str = "unknown",
    attempt: int = 1,
    capture_max_scrolls: int | None = None,
    capture_max_messages: int | None = None,
    capture_window_start: datetime | None = None,
    capture_window_end: datetime | None = None,
    ai_report_classifier: Any = None,
) -> dict[str, Any]:
    group_name = str(target.get("group_name") or "").strip()
    person = str(target.get("person") or "Chưa xác định").strip()
    class_code = str(target.get("class_code") or "").strip()
    if str(target.get("lesson") or "").strip() == "14":
        result = _empty_result(
            target,
            today=today,
            reason="Buổi 14 được miễn báo cáo Zalo.",
            decision="LESSON_14_EXEMPTION",
        )
        result["evidence_state"] = "LESSON_14_EXEMPTION"
        context = result.setdefault("evidence", {}).setdefault("date_context", {})
        context.update(
            {
                "target_week": target.get("week"),
                "target_year": target.get("year"),
                "target_week_source": "BCNB_SERVER",
                "target_class_code": target.get("class_code"),
                "target_lesson": target.get("lesson"),
                "target_class_date": target.get("class_date_iso")
                or target.get("class_date"),
            }
        )
        return attach_zalo_sync_result(result, sync_result)
    if not sync_result.get("verified"):
        return attach_zalo_sync_result(
            _empty_result(
                target,
                today=today,
                reason=str(sync_result.get("reason") or "Zalo chưa đồng bộ."),
                decision="ZALO_SYNC_INCOMPLETE",
            ),
            sync_result,
        )

    await job_repo.ensure_not_cancelled(job_id)
    await _run_target_search_operation(
        lambda timeout_ms=None: page.locator("#contact-search-input").clear(timeout=timeout_ms),
        job_id=job_id,
        worker_id=worker_id,
        phase="SEARCH_CLEAR",
        attempt=attempt,
        timeout_seconds=TARGET_SEARCH_CLEAR_TIMEOUT_SECONDS,
        timeout_code=TARGET_SEARCH_CLEAR_TIMEOUT,
        error_code=TARGET_SEARCH_CLEAR_ERROR,
    )
    search_query = re.sub(r"\s*\([^)]*\)", "", group_name).strip()
    await _run_target_search_operation(
        lambda timeout_ms=None: page.locator("#contact-search-input").fill(search_query, timeout=timeout_ms),
        job_id=job_id,
        worker_id=worker_id,
        phase="SEARCH_FILL",
        attempt=attempt,
        timeout_seconds=TARGET_SEARCH_FILL_TIMEOUT_SECONDS,
        timeout_code=TARGET_SEARCH_FILL_TIMEOUT,
        error_code=TARGET_SEARCH_FILL_ERROR,
    )
    await _run_target_search_operation(
        lambda: asyncio.sleep(TARGET_SEARCH_IDLE_SECONDS),
        job_id=job_id,
        worker_id=worker_id,
        phase="SEARCH_RESULT",
        attempt=attempt,
        timeout_seconds=TARGET_SEARCH_RESULT_TIMEOUT_SECONDS,
        timeout_code=TARGET_SEARCH_RESULT_TIMEOUT,
        error_code=TARGET_SEARCH_RESULT_ERROR,
    )
    body_text = await _run_target_search_operation(
        lambda timeout_ms=None: page.locator("body").inner_text(timeout=timeout_ms),
        job_id=job_id,
        worker_id=worker_id,
        phase="SEARCH_RESULT",
        attempt=attempt,
        timeout_seconds=TARGET_SEARCH_RESULT_TIMEOUT_SECONDS,
        timeout_code=TARGET_SEARCH_RESULT_TIMEOUT,
        error_code=TARGET_SEARCH_RESULT_ERROR,
    )
    if "Không tìm thấy" in body_text or "No results found" in body_text:
        fallback = class_code
        if not fallback:
            clean_name = re.sub(
                r"\s*\([^)]*\)", "", unicodedata.normalize("NFC", group_name)
            ).strip()
            clean_name = re.sub(r"\[[^\]]*\d+[hH][^\]]*\]", "", clean_name).strip()
            parts = re.split(r"\s+[-|—–]\s+|[|\[\]\u2013\u2014]", clean_name)
            _time_token_pattern = re.compile(r"^\d{1,2}[hH]\d{0,2}(\s*-\s*\d{1,2}[hH]\d{0,2})?$")
            _day_token_pattern = re.compile(r"^(t[2-7]|cn|sáng|chiều|tối|off|on)$", re.IGNORECASE)
            fallback = next(
                (
                    item.strip()
                    for item in reversed(parts)
                    if len(item.strip()) >= 3
                    and not _time_token_pattern.match(item.strip())
                    and not _day_token_pattern.match(item.strip())
                    and not item.strip().isdigit()
                    and not any(
                        word in item.casefold()
                        for word in (
                            "mindx",
                            "lập trình",
                            "game",
                            "web",
                            "app",
                            "robotics",
                            "lego",
                            "vũng tàu",
                            "hà nội",
                            "hcm",
                        )
                    )
                ),
                "",
            )
        if fallback and fallback != search_query:
            await _run_target_search_operation(
                lambda timeout_ms=None: page.locator("#contact-search-input").clear(timeout=timeout_ms),
                job_id=job_id,
                worker_id=worker_id,
                phase="SEARCH_CLEAR",
                attempt=attempt,
                timeout_seconds=TARGET_SEARCH_CLEAR_TIMEOUT_SECONDS,
                timeout_code=TARGET_SEARCH_CLEAR_TIMEOUT,
                error_code=TARGET_SEARCH_CLEAR_ERROR,
            )
            await _run_target_search_operation(
                lambda timeout_ms=None: page.locator("#contact-search-input").fill(fallback, timeout=timeout_ms),
                job_id=job_id,
                worker_id=worker_id,
                phase="SEARCH_FILL",
                attempt=attempt,
                timeout_seconds=TARGET_SEARCH_FILL_TIMEOUT_SECONDS,
                timeout_code=TARGET_SEARCH_FILL_TIMEOUT,
                error_code=TARGET_SEARCH_FILL_ERROR,
            )
            await _run_target_search_operation(
                lambda: asyncio.sleep(TARGET_SEARCH_IDLE_SECONDS),
                job_id=job_id,
                worker_id=worker_id,
                phase="SEARCH_RESULT",
                attempt=attempt,
                timeout_seconds=TARGET_SEARCH_RESULT_TIMEOUT_SECONDS,
                timeout_code=TARGET_SEARCH_RESULT_TIMEOUT,
                error_code=TARGET_SEARCH_RESULT_ERROR,
            )
            body_text = await _run_target_search_operation(
                lambda timeout_ms=None: page.locator("body").inner_text(timeout=timeout_ms),
                job_id=job_id,
                worker_id=worker_id,
                phase="SEARCH_RESULT",
                attempt=attempt,
                timeout_seconds=TARGET_SEARCH_RESULT_TIMEOUT_SECONDS,
                timeout_code=TARGET_SEARCH_RESULT_TIMEOUT,
                error_code=TARGET_SEARCH_RESULT_ERROR,
            )
        if "Không tìm thấy" in body_text or "No results found" in body_text:
            await page.keyboard.press("Escape")
            return attach_zalo_sync_result(
                _empty_result(
                    target,
                    today=today,
                    reason="Không tìm thấy nhóm trên Zalo.",
                    decision="GROUP_NOT_FOUND",
                ),
                sync_result,
            )

    await page.keyboard.press("Enter")
    try:
        await page.wait_for_selector('[data-component="bubble-message"]', timeout=4000)
    except PlaywrightTimeoutError:
        await asyncio.sleep(2)
    header_text = await _run_target_search_operation(
        lambda: _wait_for_target_header(
            page.locator('.header-title, .chat-title, [class*="conv-header"] .truncate'),
            group_name,
            lambda: job_repo.ensure_not_cancelled(job_id),
            timeout_seconds=TARGET_SEARCH_RESULT_TIMEOUT_SECONDS,
        ),
        job_id=job_id,
        worker_id=worker_id,
        phase="SEARCH_RESULT",
        attempt=attempt,
        timeout_seconds=TARGET_SEARCH_RESULT_TIMEOUT_SECONDS,
        timeout_code=TARGET_SEARCH_RESULT_TIMEOUT,
        error_code=TARGET_SEARCH_RESULT_ERROR,
    )

    scan_extra_kwargs: dict[str, Any] = {}
    if ai_report_classifier is not None:
        scan_extra_kwargs["ai_report_classifier"] = ai_report_classifier
    result = await scan_open_zalo_chat(
        page,
        job_id=job_id,
        screenshots_dir=screenshots_dir,
        group_name=group_name,
        actual_group_name=header_text,
        person=person,
        class_date_str=str(target.get("class_date") or ""),
        scan_target=target,
        today=today,
        ensure_not_cancelled=lambda: job_repo.ensure_not_cancelled(job_id),
        worker_id=worker_id,
        attempt=attempt,
        capture_max_scrolls=capture_max_scrolls,
        capture_max_messages=capture_max_messages,
        capture_window_start=capture_window_start,
        capture_window_end=capture_window_end,
        **scan_extra_kwargs,
    )
    context = result.setdefault("evidence", {}).setdefault("date_context", {})
    context.update(
        {
            "target_week": target.get("week"),
            "target_year": target.get("year"),
            "target_week_source": "BCNB_SERVER",
            "target_class_code": target.get("class_code"),
            "target_lesson": target.get("lesson"),
        }
    )
    context.setdefault("target_class_date", target.get("class_date"))
    result["scan_target"] = target
    return attach_zalo_sync_result(result, sync_result)


async def _create_production_backup(job_id: int, runtime, job_repo) -> dict[str, Any]:
    artifact = await asyncio.to_thread(
        backup_job,
        runtime.database_path,
        runtime.db_backup_root,
        keep_count=runtime.db_backup_retention_count,
        label=f"zalo-job-{job_id}",
    )
    add_event = getattr(job_repo, "add_event", None)
    if add_event is not None:
        await add_event(
            job_id,
            "DB_BACKUP_VERIFIED",
            "Đã tạo và kiểm chứng SQLite online backup trước batch production.",
            {
                "path": artifact["destination_path"],
                "sha256": artifact["sha256"],
                "integrity": artifact["integrity"],
                "foreign_key_errors": len(artifact["foreign_key_errors"]),
            },
        )
    return artifact


async def run_parallel_zalo_batch(
    *,
    job_id: int,
    execution_mode: str,
    scan_plan: dict[str, Any],
    today,
    scan_started_at,
    evidence_root: str,
    screenshots_dir: str,
    runtime,
    job_repo,
    audit_repo,
    evidence_repo,
    reconciliation_only: bool = False,
) -> dict[str, Any]:
    """Run scan and write phases sequentially, with independent pages per worker."""
    repository = ZaloBatchRepository()
    targets = [
        item for item in scan_plan.get("targets", []) if isinstance(item, dict)
    ]
    existing = await repository.list_for_job(job_id)
    if existing:
        await repository.recover_interrupted(job_id)
        existing = await repository.list_for_job(job_id)
    else:
        await repository.seed_targets(job_id, targets)
    policy = BatchFailurePolicy(
        total_targets=len(targets),
        systemic_limit=runtime.batch_systemic_failure_limit,
        abnormal_percent_limit=runtime.batch_abnormal_percent_limit,
    )
    scan_results_by_key: dict[str, dict[str, Any]] = {}
    scan_result_ids: dict[str, int] = {}
    result_lock = asyncio.Lock()
    for item in existing:
        if item.get("result") and item.get("scan_status") in {
            "VERIFIED",
            "SKIPPED",
            "FAILED",
        }:
            scan_results_by_key[item["target_key"]] = item["result"]
            scan_result_ids[item["target_key"]] = int(item["id"])

    if execution_mode == "production":
        try:
            await _create_production_backup(job_id, runtime, job_repo)
        except Exception as exc:
            await repository.halt_unclaimed(job_id, f"SQLite backup lỗi: {exc}")
            raise RuntimeError("Backup SQLite production không đạt; không mở browser.") from exc

    if reconciliation_only:
        await repository.terminalize_interrupted(
            job_id,
            production=execution_mode == "production",
            reason="Kế hoạch đã cũ; chỉ cho phép readback target write dở.",
        )

    async with runtime.external.browser.persistent_session() as session:
        landing_facts_provider = getattr(session, "landing_facts_provider", None)

        async def get_landing_facts(page) -> LandingFacts | Mapping[str, object] | None:
            """Observe readiness freshly for this page attempt.

            A readiness fact is scoped to one concrete page.  Sharing the
            first result across workers or retries could authorize a detached
            or newly-created page without measuring its own host and controls.
            """
            try:
                return await _invoke_landing_provider(
                    landing_facts_provider,
                    page,
                )
            except JobCancelledError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception:
                # ``probe_landing_readiness`` performs the final typed
                # mapping; an adapter failure remains fail-closed for this
                # page only and never poisons another page attempt.
                return None

        def landing_provider_for(page):
            if landing_facts_provider is None:
                return None
            return lambda: get_landing_facts(page)

        batch_halt_applied = False
        landing_readiness_blocked = False
        setup_failure_lock = asyncio.Lock()
        setup_failure_claimed = False
        # Zalo Web can invalidate one tab when the same profile is opened in
        # another tab.  Keep dry-run parallelism, but use one scan page for a
        # production profile until separate profiles are explicitly proven.
        scan_worker_count = (
            1
            if execution_mode == "production"
            else max(1, int(runtime.zalo_scan_concurrency))
        )
        active_scan_workers = {
            f"scan-{worker_index + 1}" for worker_index in range(scan_worker_count)
        }
        worker_state_lock = asyncio.Lock()

        async def halt_unclaimed_if_needed() -> None:
            nonlocal batch_halt_applied
            if policy.halted.is_set() and not batch_halt_applied:
                await repository.halt_unclaimed(
                    job_id,
                    policy.halt_reason or "Batch halted",
                )
                batch_halt_applied = True

        async def retire_scan_worker(owner: str, reason: str) -> bool:
            """Retire one unhealthy worker and halt only if no worker remains."""
            async with worker_state_lock:
                active_scan_workers.discard(owner)
                no_healthy_workers = not active_scan_workers
            if not no_healthy_workers:
                return False
            # A target-local retry failure retires the final usable worker.
            # Keep the batch fail-closed even when this was the final target;
            # halt_unclaimed remains responsible for counting pending rows.
            await policy.halt(reason)
            return True

        async def scan_worker(index: int) -> None:
            owner = f"scan-{index + 1}"
            page = None
            sync_result: dict[str, Any] | None = None
            closed_pages: list[Any] = []

            async def close_page_once(candidate) -> ZaloSetupError | None:
                if candidate is None:
                    return None
                if any(candidate is closed for closed in closed_pages):
                    return None
                closed_pages.append(candidate)
                return await _close_scan_page(session, candidate)

            async def record_failed_setup(setup_error: ZaloSetupError) -> None:
                nonlocal setup_failure_claimed, batch_halt_applied
                nonlocal landing_readiness_blocked
                async with setup_failure_lock:
                    if setup_error.code in LANDING_SETUP_CODES:
                        no_healthy_workers = await retire_scan_worker(
                            owner,
                            setup_error.code,
                        )
                        if not no_healthy_workers:
                            return
                    if setup_failure_claimed:
                        return
                    setup_failure_claimed = True
                    await policy.halt(setup_error.code)
                    await policy.record(
                        abnormal=True,
                        systemic=False,
                        reason=setup_error.code,
                    )
                    if setup_error.code in LANDING_SETUP_CODES:
                        landing_readiness_blocked = True
                        await repository.halt_unclaimed(job_id, setup_error.code)
                        batch_halt_applied = True
                        return
                    claimed = await repository.claim_scan(job_id, owner)
                    if claimed is None:
                        return
                    source = dict(claimed.get("source") or {})
                    result = _setup_failure_result(
                        source,
                        today=today,
                        setup_error=setup_error,
                    )
                    await repository.complete_scan(
                        claimed["id"],
                        status="FAILED",
                        result=result,
                        error_code=setup_error.code,
                        error_message=setup_error.safe_message,
                    )
                    async with result_lock:
                        key = claimed["target_key"]
                        scan_results_by_key[key] = result
                        scan_result_ids[key] = int(claimed["id"])

            async def setup_fresh_retry_page() -> ZaloSetupError | None:
                """Close the failed page, then perform one full bounded setup."""
                nonlocal page, sync_result
                close_started_at = asyncio.get_running_loop().time()
                close_error = await close_page_once(page)
                page = None
                if close_error is not None:
                    _emit_setup_telemetry(
                        job_id=job_id,
                        worker_id=owner,
                        started_at=close_started_at,
                        page_state=close_error.page_state,
                        selector_count=close_error.selector_count,
                        selector_ready=close_error.selector_ready,
                        error_code=close_error.code,
                    )
                    return close_error

                setup_started_at = asyncio.get_running_loop().time()
                try:
                    page = await _new_scan_page(session)
                except JobCancelledError:
                    raise
                except ZaloSetupError as exc:
                    _emit_setup_telemetry(
                        job_id=job_id,
                        worker_id=owner,
                        started_at=setup_started_at,
                        page_state=exc.page_state,
                        selector_count=exc.selector_count,
                        selector_ready=exc.selector_ready,
                        error_code=exc.code,
                    )
                    return exc

                try:
                    sync_result = await _setup_scan_page(
                        page,
                        job_id=job_id,
                        job_repo=job_repo,
                        landing_facts_provider=landing_provider_for(page),
                    )
                except JobCancelledError:
                    raise
                except ZaloSetupError as exc:
                    _emit_setup_telemetry(
                        job_id=job_id,
                        worker_id=owner,
                        started_at=setup_started_at,
                        page_state=exc.page_state,
                        selector_count=exc.selector_count,
                        selector_ready=exc.selector_ready,
                        error_code=exc.code,
                    )
                    retry_close_started_at = asyncio.get_running_loop().time()
                    retry_close_error = await close_page_once(page)
                    page = None
                    if retry_close_error is not None:
                        _emit_setup_telemetry(
                            job_id=job_id,
                            worker_id=owner,
                            started_at=retry_close_started_at,
                            page_state=retry_close_error.page_state,
                            selector_count=retry_close_error.selector_count,
                            selector_ready=retry_close_error.selector_ready,
                            error_code=retry_close_error.code,
                        )
                        return retry_close_error
                    return exc

                _emit_setup_telemetry(
                    job_id=job_id,
                    worker_id=owner,
                    started_at=setup_started_at,
                    page_state=ScanPageState.READY,
                    selector_count=1,
                    selector_ready=True,
                    error_code=SETUP_OK,
                )
                return None

            async def retry_target_once(
                source: dict[str, Any],
            ) -> tuple[dict[str, Any] | None, TargetSearchError | ZaloSetupError | None]:
                nonlocal page
                lifecycle_error = await setup_fresh_retry_page()
                if lifecycle_error is not None:
                    return None, lifecycle_error
                assert page is not None
                assert sync_result is not None
                try:
                    retried = await _scan_one_target(
                        page,
                        job_id=job_id,
                        target=source,
                        sync_result=sync_result,
                        screenshots_dir=screenshots_dir,
                        today=today,
                        job_repo=job_repo,
                        worker_id=owner,
                        attempt=2,
                        **(
                            {
                                "ai_report_classifier": getattr(
                                    runtime.external, "zalo_ai_classifier", None
                                )
                            }
                            if getattr(runtime.external, "zalo_ai_classifier", None)
                            is not None
                            else {}
                        ),
                    )
                except JobCancelledError:
                    raise
                except TargetSearchError as exc:
                    retry_close_started_at = asyncio.get_running_loop().time()
                    retry_close_error = await close_page_once(page)
                    page = None
                    if retry_close_error is not None:
                        _emit_setup_telemetry(
                            job_id=job_id,
                            worker_id=owner,
                            started_at=retry_close_started_at,
                            page_state=retry_close_error.page_state,
                            selector_count=retry_close_error.selector_count,
                            selector_ready=retry_close_error.selector_ready,
                            error_code=retry_close_error.code,
                        )
                        return None, retry_close_error
                    return None, exc
                return retried, None

            async def remember_result(claimed, result: dict[str, Any]) -> None:
                async with result_lock:
                    key = claimed["target_key"]
                    scan_results_by_key[key] = result
                    scan_result_ids[key] = int(claimed["id"])

            try:
                setup_error: ZaloSetupError | None = None
                for attempt in range(MAX_SETUP_ATTEMPTS):
                    setup_started_at = asyncio.get_running_loop().time()
                    if page is None:
                        try:
                            page = await _new_scan_page(session)
                        except JobCancelledError:
                            raise
                        except ZaloSetupError as exc:
                            setup_error = exc
                            _emit_setup_telemetry(
                                job_id=job_id,
                                worker_id=owner,
                                started_at=setup_started_at,
                                page_state=setup_error.page_state,
                                selector_count=setup_error.selector_count,
                                selector_ready=setup_error.selector_ready,
                                error_code=setup_error.code,
                            )
                            if attempt + 1 < MAX_SETUP_ATTEMPTS:
                                continue
                            break
                    try:
                        sync_result = await _setup_scan_page(
                            page,
                            job_id=job_id,
                            job_repo=job_repo,
                            landing_facts_provider=landing_provider_for(page),
                        )
                    except JobCancelledError:
                        raise
                    except ZaloSetupError as exc:
                        setup_error = exc
                        _emit_setup_telemetry(
                            job_id=job_id,
                            worker_id=owner,
                            started_at=setup_started_at,
                            page_state=exc.page_state,
                            selector_count=exc.selector_count,
                            selector_ready=exc.selector_ready,
                            error_code=exc.code,
                        )
                        if exc.code in LANDING_SETUP_CODES:
                            break
                        if attempt + 1 < MAX_SETUP_ATTEMPTS:
                            close_started_at = asyncio.get_running_loop().time()
                            close_error = await close_page_once(page)
                            if close_error is not None:
                                setup_error = close_error
                                _emit_setup_telemetry(
                                    job_id=job_id,
                                    worker_id=owner,
                                    started_at=close_started_at,
                                    page_state=close_error.page_state,
                                    selector_count=close_error.selector_count,
                                    selector_ready=close_error.selector_ready,
                                    error_code=close_error.code,
                                )
                                break
                            page = None
                            continue
                        break
                    else:
                        _emit_setup_telemetry(
                            job_id=job_id,
                            worker_id=owner,
                            started_at=setup_started_at,
                            page_state=ScanPageState.READY,
                            selector_count=1,
                            selector_ready=True,
                            error_code=SETUP_OK,
                        )
                        setup_error = None
                        break

                if setup_error is not None:
                    await record_failed_setup(setup_error)
                    return
                if page is None or sync_result is None:
                    # This branch is defensive: every successful setup returns
                    # a sync result, while an unsuccessful setup returns above.
                    await record_failed_setup(
                        ZaloSetupError(
                            SETUP_SYNC_ERROR,
                            page_state=ScanPageState.SYNCING,
                            selector_count=1,
                            selector_ready=True,
                        )
                    )
                    return
                while not policy.halted.is_set():
                    claimed = await repository.claim_scan(job_id, owner)
                    if claimed is None:
                        return
                    source = dict(claimed.get("source") or {})
                    try:
                        result = await _scan_one_target(
                            page,
                            job_id=job_id,
                            target=source,
                            sync_result=sync_result,
                            screenshots_dir=screenshots_dir,
                            today=today,
                            job_repo=job_repo,
                            worker_id=owner,
                            attempt=1,
                            **(
                                {
                                    "ai_report_classifier": getattr(
                                        runtime.external, "zalo_ai_classifier", None
                                    )
                                }
                                if getattr(runtime.external, "zalo_ai_classifier", None)
                                is not None
                                else {}
                            ),
                        )
                    except TargetSearchError as first_error:
                        retry_result, terminal_error = await retry_target_once(source)
                        if terminal_error is not None:
                            result = _target_search_failure_result(
                                source,
                                today=today,
                                error=terminal_error,
                            )
                            await repository.complete_scan(
                                claimed["id"],
                                status="FAILED",
                                result=result,
                                error_code=terminal_error.code,
                                error_message=terminal_error.safe_message,
                            )
                            await remember_result(claimed, result)
                            await retire_scan_worker(owner, terminal_error.code)
                            await policy.record(
                                abnormal=True,
                                systemic=False,
                                reason=terminal_error.code,
                            )
                            return
                        if retry_result is None:
                            # Defensive guard: a retry either returns a result
                            # or a terminal typed error, never a sticky state.
                            result = _target_search_failure_result(
                                source,
                                today=today,
                                error=first_error,
                            )
                            await repository.complete_scan(
                                claimed["id"],
                                status="FAILED",
                                result=result,
                                error_code=first_error.code,
                                error_message=first_error.safe_message,
                            )
                            await remember_result(claimed, result)
                            await retire_scan_worker(owner, first_error.code)
                            return
                        result = retry_result
                    except JobCancelledError:
                        result = _empty_result(
                            source,
                            today=today,
                            reason="Đã dừng tại điểm an toàn.",
                            decision="JOB_CANCELLED",
                        )
                        result["error_code"] = "JOB_CANCELLED"
                        result["error_message"] = "Đã dừng tại điểm an toàn."
                        await repository.complete_scan(
                            claimed["id"],
                            status="FAILED",
                            result=result,
                            error_code="JOB_CANCELLED",
                            error_message="Đã dừng tại điểm an toàn.",
                        )
                        await remember_result(claimed, result)
                        raise
                    except Exception as exc:
                        result = _empty_result(
                            source,
                            today=today,
                            reason="Không đủ bằng chứng do lỗi xử lý target.",
                            decision="SCAN_FAILED",
                        )
                        result["error_code"] = "SCAN_FAILED"
                        result["error_message"] = "Không đủ bằng chứng do lỗi xử lý target."
                        await repository.complete_scan(
                            claimed["id"],
                            status="FAILED",
                            result=result,
                            error_code="SCAN_FAILED",
                            error_message="Không đủ bằng chứng do lỗi xử lý target.",
                        )
                        await policy.record(
                            abnormal=True,
                            systemic=_is_systemic_error(exc),
                            reason="SCAN_FAILED",
                        )
                        await remember_result(claimed, result)
                        continue

                    await repository.complete_scan(
                        claimed["id"],
                        status=(
                            "SKIPPED"
                            if result.get("evidence_state") == "LESSON_14_EXEMPTION"
                            else "VERIFIED"
                        ),
                        result=result,
                    )
                    evidence_state = str(result.get("evidence_state") or "")
                    abnormal = evidence_state not in {
                        "SUBMITTED_VERIFIED",
                        "SUBMITTED_LATE_VERIFIED",
                        "LESSON_14_EXEMPTION",
                        "NOT_FOUND_VERIFIED",
                    }
                    systemic = result.get("evidence", {}).get(
                        "classifier_result", {}
                    ).get("decision") == "ZALO_SYNC_INCOMPLETE"
                    await policy.record(
                        abnormal=abnormal,
                        systemic=systemic,
                        reason=(
                            "TARGET_EVIDENCE_INSUFFICIENT"
                            if abnormal
                            else ""
                        ),
                    )
                    await remember_result(claimed, result)
            finally:
                close_started_at = asyncio.get_running_loop().time()
                close_error = await close_page_once(page)
                if close_error is not None:
                    _emit_setup_telemetry(
                        job_id=job_id,
                        worker_id=owner,
                        started_at=close_started_at,
                        page_state=close_error.page_state,
                        selector_count=close_error.selector_count,
                        selector_ready=close_error.selector_ready,
                        error_code=close_error.code,
                    )
                    await retire_scan_worker(owner, close_error.code)
                # A worker that exits after exhausting its claims is still a
                # healthy batch participant.  Keep it in the health set so a
                # later target-local failure cannot misclassify an already
                # completed healthy worker as unavailable.  Only the bounded
                # retry path retires a worker from this set.

        workers = scan_worker_count
        if not reconciliation_only:
            await _gather_workers(scan_worker(index) for index in range(workers))
        await halt_unclaimed_if_needed()

        if landing_readiness_blocked:
            # A landing/auth contract failure is a batch boundary.  Do not
            # register empty evidence or enter the independent BCNB write
            # stage after setup stopped before any target was claimed.
            return {
                "scan_results": [],
                "batch_summary": await repository.summary(job_id),
                "bcnb_summaries": [],
                "halted": policy.halted.is_set(),
                "halt_reason": policy.halt_reason,
                "abnormal_targets": policy.abnormal_targets,
                "worker_count": scan_worker_count,
            }

        scan_results = list(scan_results_by_key.values())
        await evidence_repo.register_scan_results(
            job_id=job_id,
            results=scan_results,
            evidence_root=evidence_root,
            retention_days=getattr(Config, "EVIDENCE_RETENTION_DAYS", 7),
        )
        current_by_key = {
            item["target_key"]: item
            for item in await repository.list_for_job(job_id)
        }
        for key, result in scan_results_by_key.items():
            current = current_by_key[key]
            if current["write_status"] not in {"NOT_READY", "PENDING"}:
                continue
            state = str(result.get("evidence_state") or "")
            ready = state in {
                "SUBMITTED_VERIFIED",
                "SUBMITTED_LATE_VERIFIED",
                "LESSON_14_EXEMPTION",
            }
            evidence_path, evidence_sha256 = get_evidence_reference(result)
            setup_error_code = str(result.get("setup_error_code") or "").strip()
            result_error_code = str(result.get("error_code") or "").strip()
            preserved_error_code = (
                result_error_code
                if (
                    result_error_code in {
                        TARGET_SEARCH_CLEAR_TIMEOUT,
                        TARGET_SEARCH_CLEAR_ERROR,
                        TARGET_SEARCH_FILL_TIMEOUT,
                        TARGET_SEARCH_FILL_ERROR,
                        TARGET_SEARCH_RESULT_TIMEOUT,
                        TARGET_SEARCH_RESULT_ERROR,
                        "SCAN_FAILED",
                    }
                    or result_error_code == SELECTOR_READINESS_TIMEOUT
                    or result_error_code.startswith("SETUP_")
                )
                else ""
            )
            result_error_message = str(result.get("error_message") or "").strip()
            setup_error_message = (
                "Zalo scan setup không đạt; batch đã dừng an toàn."
                if setup_error_code
                else None
            )
            await repository.finalize_registered_scan(
                scan_result_ids[key],
                result=result,
                write_ready=ready and not policy.halted.is_set(),
                batch_halted=policy.halted.is_set(),
                evidence_path=evidence_path,
                evidence_sha256=evidence_sha256,
                error_code=(
                    preserved_error_code
                    if preserved_error_code
                    else setup_error_code
                    if setup_error_code
                    else (
                        "BATCH_HALTED"
                        if policy.halted.is_set()
                        else (None if ready else "EVIDENCE_NOT_READY")
                    )
                ),
                error_message=(
                    result_error_message
                    if preserved_error_code and result_error_message
                    else setup_error_message
                    if setup_error_message
                    else (
                        policy.halt_reason
                        if policy.halted.is_set()
                        else (None if ready else "Evidence không đủ điều kiện ghi BCNB.")
                    )
                ),
            )

        await job_repo.update_job(
            job_id,
            message="Đã đăng ký evidence; đang cập nhật BCNB bằng các page độc lập...",
            progress=80,
        )
        raw_periods = scan_plan.get("selected_weeks") or []
        periods = [
            (int(item["week"]), int(item["year"]))
            for item in raw_periods
            if isinstance(item, dict) and item.get("week") and item.get("year")
        ]
        summaries: list[dict[str, Any]] = []

        async def write_worker(index: int, assigned: list[tuple[int, int]]) -> None:
            if not assigned:
                return
            owner = f"write-{index + 1}"
            page = None
            try:
                setup_started_at = asyncio.get_running_loop().time()
                try:
                    page = await _new_scan_page(session)
                except JobCancelledError:
                    raise
                except ZaloSetupError as exc:
                    _emit_setup_telemetry(
                        job_id=job_id,
                        worker_id=owner,
                        started_at=setup_started_at,
                        page_state=exc.page_state,
                        selector_count=exc.selector_count,
                        selector_ready=exc.selector_ready,
                        error_code=exc.code,
                    )
                    await policy.halt(exc.code)
                    return
                await page.goto(Config.BCNB_URL)
                await _login_bcnb_if_needed(page)
                unknown = [
                    item
                    for item in await repository.list_for_job(job_id)
                    if item["write_status"] == "OUTCOME_UNKNOWN"
                    and (int(item["week"]), int(item["year"])) in assigned
                ]
                for target in unknown:
                    try:
                        checked = await verify_checkbox_persisted(
                            page,
                            class_code=target["class_code"],
                            column_kind="report",
                            week=int(target["week"]),
                            year=int(target["year"]),
                            lesson=target["lesson"],
                        )
                    except Exception:
                        await repository.resolve_unknown(
                            target["id"], status="OUTCOME_UNKNOWN"
                        )
                        await policy.record(
                            abnormal=True,
                            systemic=True,
                            reason="Không readback được target recovery.",
                        )
                    else:
                        if checked:
                            action_key = (
                                f"bcnb-report:{job_id}:{target['year']}:W{target['week']}:"
                                f"{target['class_code']}:{target['lesson']}"
                            )
                            audit = await audit_repo.get_by_action_key(action_key)
                            if audit is None:
                                audit, _ = await audit_repo.record_intent(
                                    action_key=action_key,
                                    job_id=job_id,
                                    action_type="BCNB_REPORT_TICK",
                                    target_type="CLASS",
                                    target_id=target["class_code"],
                                    mode=execution_mode,
                                    before_state={"checked": True},
                                    intended_state={"checked": True},
                                    reason={"reconciled_after_restart": True},
                                    evidence_path=target.get("evidence_path"),
                                    evidence_sha256=target.get("evidence_sha256"),
                                )
                            if audit.get("outcome") in {"INTENT_RECORDED", "UNKNOWN"}:
                                await audit_repo.reconcile_verified(audit["id"])
                        await repository.resolve_unknown(
                            target["id"], status="VERIFIED" if checked else "PENDING"
                        )

                if reconciliation_only:
                    return

                claimed: list[dict[str, Any]] = []
                while not policy.halted.is_set():
                    claimed_target = await repository.claim_write(
                        job_id, owner, periods=set(assigned)
                    )
                    if claimed_target is None:
                        break
                    claimed.append(claimed_target)
                if not claimed:
                    return
                try:
                    async def record_row_outcome(outcome: dict[str, Any]) -> None:
                        status = str(outcome.get("status") or "")
                        error = str(outcome.get("error") or "")
                        abnormal = status in {
                            "FAILED_BEFORE_WRITE",
                            "OUTCOME_UNKNOWN",
                        }
                        await policy.record(
                            abnormal=abnormal,
                            systemic=abnormal and _is_systemic_error(error),
                            reason=error,
                        )

                    summary = await update_bcnb_reports(
                        page,
                        job_id=job_id,
                        execution_mode=execution_mode,
                        scan_results=scan_results,
                        today=today,
                        now=scan_started_at,
                        scan_plan=scan_plan,
                        job_repo=job_repo,
                        audit_repo=audit_repo,
                        period_filter=set(assigned),
                        should_halt=policy.halted.is_set,
                        on_target_outcome=record_row_outcome,
                    )
                    summaries.append(summary)
                    outcome_by_key = {
                        canonical_target_key(item): item
                        for item in summary.get("target_outcomes", [])
                    }
                    for target in claimed:
                        outcome = outcome_by_key.get(target["target_key"])
                        if outcome is None:
                            await repository.complete_write(
                                target["id"],
                                status="FAILED_BEFORE_WRITE",
                                error_code="TARGET_NOT_UNIQUE_OR_MISSING",
                                error_message="Không tìm đúng một dòng BCNB cho target.",
                            )
                            await policy.record(
                                abnormal=True,
                                systemic=False,
                                reason="Target BCNB không duy nhất hoặc không tồn tại.",
                            )
                            continue
                        status = str(outcome["status"])
                        await repository.complete_write(
                            target["id"],
                            status=status,
                            error_code=("BCNB_WRITE_FAILED" if status in {
                                "FAILED_BEFORE_WRITE", "OUTCOME_UNKNOWN"
                            } else None),
                            error_message=outcome.get("error"),
                        )
                except JobCancelledError:
                    status = (
                        "OUTCOME_UNKNOWN"
                        if execution_mode == "production"
                        else "FAILED_BEFORE_WRITE"
                    )
                    for target in claimed:
                        await repository.complete_write(
                            target["id"],
                            status=status,
                            error_code="JOB_CANCELLED",
                            error_message="Đã dừng tại điểm an toàn trước/đang readback.",
                        )
                    raise
                except Exception as exc:
                    status = (
                        "OUTCOME_UNKNOWN"
                        if execution_mode == "production"
                        else "FAILED_BEFORE_WRITE"
                    )
                    for target in claimed:
                        await repository.complete_write(
                            target["id"],
                            status=status,
                            error_code=type(exc).__name__,
                            error_message=str(exc),
                        )
                    await policy.record(
                        abnormal=True,
                        systemic=_is_systemic_error(exc),
                        reason=str(exc),
                    )
            finally:
                close_started_at = asyncio.get_running_loop().time()
                close_error = await _close_scan_page(session, page)
                if close_error is not None:
                    _emit_setup_telemetry(
                        job_id=job_id,
                        worker_id=owner,
                        started_at=close_started_at,
                        page_state=close_error.page_state,
                        selector_count=close_error.selector_count,
                        selector_ready=close_error.selector_ready,
                        error_code=close_error.code,
                    )
                    await policy.halt(close_error.code)

        write_workers = max(1, int(runtime.bcnb_write_concurrency))
        assignments: list[list[tuple[int, int]]] = [
            [] for _ in range(write_workers)
        ]
        for index, period in enumerate(periods):
            assignments[index % write_workers].append(period)
        if not policy.halted.is_set():
            await _gather_workers(
                write_worker(index, assignment)
                for index, assignment in enumerate(assignments)
            )
        await halt_unclaimed_if_needed()

    summary = await repository.summary(job_id)
    return {
        "scan_results": list(scan_results_by_key.values()),
        "batch_summary": summary,
        "bcnb_summaries": summaries,
        "halted": policy.halted.is_set(),
        "halt_reason": policy.halt_reason,
        "abnormal_targets": policy.abnormal_targets,
        "worker_count": scan_worker_count,
    }


async def run_and_finalize_parallel_zalo_batch(
    *,
    job_id: int,
    execution_mode: str,
    scan_plan: dict[str, Any],
    today,
    scan_started_at,
    evidence_root: str,
    screenshots_dir: str,
    runtime,
    job_repo,
    audit_repo,
    evidence_repo,
    reconciliation_only: bool,
) -> None:
    """Run the durable pipeline and enforce terminal parent/target invariants."""
    dry_run = execution_mode != "production"
    repository = ZaloBatchRepository()
    try:
        result = await run_parallel_zalo_batch(
            job_id=job_id,
            execution_mode=execution_mode,
            scan_plan=scan_plan,
            today=today,
            scan_started_at=scan_started_at,
            evidence_root=evidence_root,
            screenshots_dir=screenshots_dir,
            runtime=runtime,
            job_repo=job_repo,
            audit_repo=audit_repo,
            evidence_repo=evidence_repo,
            reconciliation_only=reconciliation_only,
        )
    except (Exception, asyncio.CancelledError) as exc:
        await repository.terminalize_interrupted(
            job_id,
            production=not dry_run,
            reason=str(exc) or type(exc).__name__,
        )
        raise

    batch_summary = result["batch_summary"]
    scan_counts = batch_summary["scan"]
    write_counts = batch_summary["write"]
    active_count = sum(
        int(scan_counts.get(status, 0)) for status in ("PENDING", "RUNNING")
    ) + sum(
        int(write_counts.get(status, 0))
        for status in ("NOT_READY", "PENDING", "RUNNING")
    )
    if active_count:
        await repository.terminalize_interrupted(
            job_id,
            production=not dry_run,
            reason="Batch kết thúc nhưng còn target chưa terminal.",
        )
        write_counts = (await repository.summary(job_id))["write"]
    progress = build_zalo_job_progress(
        scan_results=list(result["scan_results"]),
        batch_summary=batch_summary,
        halt_reason=result.get("halt_reason"),
        dry_run=dry_run,
    )
    if scan_plan.get("snapshot_id") is not None:
        progress["snapshot_id"] = scan_plan.get("snapshot_id")
    if isinstance(scan_plan.get("required_week"), dict):
        progress["required_week"] = dict(scan_plan["required_week"])
    if scan_plan.get("selective_rescan"):
        progress["selective_rescan"] = True
        progress["selected_target_keys"] = list(
            scan_plan.get("selected_target_keys") or []
        )
    await job_repo.update_job(
        job_id,
        status=progress["status"],
        message=progress["message"],
        progress=100,
        results=json.dumps(progress, ensure_ascii=False),
    )
