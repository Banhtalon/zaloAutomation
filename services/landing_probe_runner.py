"""Bounded, process-local execution boundary for the internal landing probe.

The runner deliberately knows only about the already-constructed worker
runtime. It does not build gateways, inspect settings, or own browser
resources. The browser gateway remains the sole owner of the landing probe.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from enum import Enum
import inspect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.runtime import WorkerRuntime


LANDING_PROBE_RUNNER_PHASE = "LANDING_PROBE_ONE_SHOT_RUNNER"
LANDING_PROBE_RUNNER_TIMEOUT_SECONDS = 30.0
LANDING_PROBE_RUNNER_LOCK_WAIT_SECONDS = 0.05
LANDING_PROBE_RUNNER_MAX_ELAPSED_MS = 10 * 60 * 1000
LANDING_PROBE_SELECTOR_CONTRACT_VERSION = "zalo-landing-probe-v1"
STRUCTURAL_OBSERVATION_RUNNER_PHASE = "STRUCTURAL_OBSERVATION_ONE_SHOT_RUNNER"
STRUCTURAL_OBSERVATION_RUNNER_TIMEOUT_SECONDS = 30.0
STRUCTURAL_OBSERVATION_SELECTOR_CONTRACT_VERSION = "zalo-landing-structural-v1"
STRUCTURAL_CANDIDATE_IDS = ("AUTHENTICATED_SHELL", "LOGIN_OR_QR")

_ACTION_KEYS = (
    "navigation",
    "search",
    "click",
    "type",
    "chat",
    "bcnb",
    "evidence",
)
_SAFE_PROFILE_BINDINGS = frozenset(
    {"WORKER_PERSISTENT_SESSION", "UNAVAILABLE"}
)
_SAFE_PROBE_STATUSES = frozenset(
    {
        "CANDIDATE_PRESENT_WITH_LIVE_PROVENANCE",
        "NO_CANDIDATE",
        "UNPROVEN",
        "AMBIGUOUS",
        "LANDING_PROFILE_UNAVAILABLE",
        "LANDING_CONTEXT_LOCKED",
        "LANDING_PAGE_CREATE_TIMEOUT",
        "LANDING_PAGE_CREATE_FAILED",
        "LANDING_NAVIGATION_TIMEOUT",
        "LANDING_NAVIGATION_FAILED",
        "LANDING_PAGE_CLOSED",
        "LANDING_EXPECTED_HOST_MISMATCH",
        "LANDING_PAGE_DETACHED",
        "LANDING_CANDIDATE_PROBE_TIMEOUT",
        "LANDING_CANDIDATE_PROBE_FAILED",
        "LANDING_CANDIDATE_PROVENANCE_INVALID",
        "LANDING_READINESS_TIMEOUT",
        "LANDING_CLEANUP_FAILED",
        "LANDING_UNKNOWN_FAIL_CLOSED",
        "LANDING_PROBE_NO_PROVEN_CANDIDATE",
    }
)


class LandingProbeRunStatus(str, Enum):
    """Finite runner outcomes safe to expose from the local endpoint."""

    COMPLETED = "COMPLETED"
    BUSY = "BUSY"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"
    REQUEST_REJECTED = "REQUEST_REJECTED"


class StructuralObservationRunStatus(str, Enum):
    """Finite one-shot outcomes for the structural observation boundary."""

    COMPLETED = "COMPLETED"
    BUSY = "BUSY"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"
    REQUEST_REJECTED = "REQUEST_REJECTED"


_SAFE_RUN_STATUSES = frozenset(item.value for item in LandingProbeRunStatus)
_SAFE_STRUCTURAL_RUN_STATUSES = frozenset(
    item.value for item in StructuralObservationRunStatus
)
_RUNNER_DIAGNOSTICS = {
    LandingProbeRunStatus.COMPLETED.value: "RUNNER_COMPLETED",
    LandingProbeRunStatus.BUSY.value: "RUNNER_BROWSER_BUSY",
    LandingProbeRunStatus.ALREADY_CONSUMED.value: "RUNNER_ALREADY_CONSUMED",
    LandingProbeRunStatus.UNAVAILABLE.value: "RUNTIME_UNAVAILABLE",
    LandingProbeRunStatus.TIMEOUT.value: "RUNNER_TIMEOUT",
    LandingProbeRunStatus.FAILED.value: "RUNNER_FAILED",
    LandingProbeRunStatus.REQUEST_REJECTED.value: "REQUEST_REJECTED",
}

_STRUCTURAL_RUNNER_DIAGNOSTICS = {
    StructuralObservationRunStatus.COMPLETED.value: "STRUCTURAL_RUNNER_COMPLETED",
    StructuralObservationRunStatus.BUSY.value: "STRUCTURAL_RUNNER_BROWSER_BUSY",
    StructuralObservationRunStatus.ALREADY_CONSUMED.value: "STRUCTURAL_RUNNER_ALREADY_CONSUMED",
    StructuralObservationRunStatus.UNAVAILABLE.value: "STRUCTURAL_RUNTIME_UNAVAILABLE",
    StructuralObservationRunStatus.TIMEOUT.value: "STRUCTURAL_RUNNER_TIMEOUT",
    StructuralObservationRunStatus.FAILED.value: "STRUCTURAL_RUNNER_FAILED",
    StructuralObservationRunStatus.REQUEST_REJECTED.value: "STRUCTURAL_RUNNER_RETIRED",
}

_STRUCTURAL_PAGESET_CATEGORIES = frozenset(
    {
        "PAGESET_UNAVAILABLE",
        "PAGESET_PREEXISTING_ZERO",
        "PAGESET_PREEXISTING_ONE",
        "PAGESET_PREEXISTING_MANY",
        "PAGESET_POSTCREATE_MISMATCH",
    }
)
_STRUCTURAL_CANDIDATE_STATUSES = frozenset(
    {
        "UNPROVEN",
    }
)
_STRUCTURAL_OPERATIONAL_DIAGNOSTICS = frozenset(
    {
        "STRUCTURAL_PAGESET_BLOCKED",
        "STRUCTURAL_PAGE_CLOSED",
        "STRUCTURAL_PAGE_DETACHED",
        "STRUCTURAL_OBSERVATION_TIMEOUT",
        "STRUCTURAL_NAVIGATION_TIMEOUT",
        "STRUCTURAL_NAVIGATION_FAILED",
        "STRUCTURAL_SEARCH_CONTROL_ABSENT",
        "STRUCTURAL_UNKNOWN_FAIL_CLOSED",
        "STRUCTURAL_SESSION_UNAVAILABLE",
        "STRUCTURAL_CLEANUP_FAILED",
        "STRUCTURAL_OBSERVATION_ALREADY_CONSUMED",
    }
)
_STRUCTURAL_PROVENANCE_CODES = frozenset(
    {
        "STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN",
        "STRUCTURAL_PAGESET_NOT_OBSERVED",
        "STRUCTURAL_PROVENANCE_NOT_VERIFIED",
        "STRUCTURAL_PROVENANCE_INVALID",
    }
)
_STRUCTURAL_DIAGNOSTICS = (
    _STRUCTURAL_CANDIDATE_STATUSES
    | _STRUCTURAL_OPERATIONAL_DIAGNOSTICS
    | _STRUCTURAL_PAGESET_CATEGORIES
    | _STRUCTURAL_PROVENANCE_CODES
)


def _empty_action_counters(navigation: int = 0) -> dict[str, int]:
    safe_navigation = 1 if navigation == 1 else 0
    return {
        key: safe_navigation if key == "navigation" else 0
        for key in _ACTION_KEYS
    }


def _safe_int(value: Any, *, maximum: int, fallback: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return min(maximum, max(0, value))


def _safe_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_probe_status(value: Any, *, diagnostic: bool = False) -> str:
    fallback = "LANDING_UNKNOWN_FAIL_CLOSED" if diagnostic else "UNPROVEN"
    if isinstance(value, str) and value in _SAFE_PROBE_STATUSES:
        return value
    return fallback


def sanitize_landing_probe_result(value: Any) -> dict[str, object] | None:
    """Copy only the fixed, sanitized fields of the existing gateway result."""

    if not isinstance(value, Mapping):
        return None
    try:
        raw_counters = value.get("zero_action_counters")
        raw_navigation = (
            raw_counters.get("navigation")
            if isinstance(raw_counters, Mapping)
            else 0
        )
        safe_navigation = _safe_int(raw_navigation, maximum=1)
        profile_binding = value.get("profile_binding")
        return {
            "phase": "LANDING_SELECTOR_PROPOSAL_PROBE",
            "elapsed_ms": _safe_int(
                value.get("elapsed_ms"),
                maximum=LANDING_PROBE_RUNNER_MAX_ELAPSED_MS,
            ),
            "profile_binding": (
                profile_binding
                if isinstance(profile_binding, str)
                and profile_binding in _SAFE_PROFILE_BINDINGS
                else "UNAVAILABLE"
            ),
            "launch_count": _safe_int(value.get("launch_count"), maximum=1),
            "expected_host_reached": _safe_bool(value.get("expected_host_reached")),
            "search_control_visible": _safe_bool(value.get("search_control_visible")),
            "auth_shell_candidate_status": _safe_probe_status(
                value.get("auth_shell_candidate_status")
            ),
            "login_or_qr_candidate_status": _safe_probe_status(
                value.get("login_or_qr_candidate_status")
            ),
            "selector_contract_version": LANDING_PROBE_SELECTOR_CONTRACT_VERSION,
            "diagnostic_code": _safe_probe_status(
                value.get("diagnostic_code"), diagnostic=True
            ),
            "zero_action_counters": _empty_action_counters(safe_navigation),
        }
    except Exception:
        return None


def _runner_result(
    status: LandingProbeRunStatus | str,
    *,
    diagnostic_code: str | None = None,
    attempt_consumed: bool = False,
    gateway_invoked: bool = False,
    probe: dict[str, object] | None = None,
) -> dict[str, object]:
    safe_status = status.value if isinstance(status, LandingProbeRunStatus) else status
    if safe_status not in _SAFE_RUN_STATUSES:
        safe_status = LandingProbeRunStatus.FAILED.value
    allowed_diagnostics = _SAFE_PROBE_STATUSES | frozenset(_RUNNER_DIAGNOSTICS.values())
    safe_diagnostic = (
        diagnostic_code
        if isinstance(diagnostic_code, str) and diagnostic_code in allowed_diagnostics
        else _RUNNER_DIAGNOSTICS[safe_status]
    )
    return {
        "phase": LANDING_PROBE_RUNNER_PHASE,
        "status": safe_status,
        "diagnostic_code": safe_diagnostic,
        "attempt_consumed": bool(attempt_consumed),
        "gateway_invoked": bool(gateway_invoked),
        "probe": probe,
    }


def sanitize_runner_result(value: Any) -> dict[str, object]:
    """Normalize a runner value before a route or caller can expose it."""

    if not isinstance(value, Mapping):
        return _runner_result(LandingProbeRunStatus.FAILED)
    try:
        raw_status = value.get("status")
        status = raw_status if isinstance(raw_status, str) else ""
        if status not in _SAFE_RUN_STATUSES:
            status = LandingProbeRunStatus.FAILED.value
        probe = sanitize_landing_probe_result(value.get("probe"))
        if status == LandingProbeRunStatus.COMPLETED.value and probe is None:
            status = LandingProbeRunStatus.FAILED.value
        raw_diagnostic = value.get("diagnostic_code")
        allowed_diagnostics = _SAFE_PROBE_STATUSES | frozenset(_RUNNER_DIAGNOSTICS.values())
        diagnostic = (
            raw_diagnostic
            if isinstance(raw_diagnostic, str)
            and raw_diagnostic in allowed_diagnostics
            else _RUNNER_DIAGNOSTICS[status]
        )
        return _runner_result(
            status,
            diagnostic_code=diagnostic,
            attempt_consumed=value.get("attempt_consumed") is True,
            gateway_invoked=value.get("gateway_invoked") is True,
            probe=probe,
        )
    except Exception:
        return _runner_result(LandingProbeRunStatus.FAILED)


def _emit_runner_telemetry(
    result: Mapping[str, object],
    telemetry: Callable[..., Any] | None,
) -> None:
    if telemetry is None:
        return
    fields = {
        "event": "landing_probe.runner",
        "phase": LANDING_PROBE_RUNNER_PHASE,
        "status": result["status"],
        "diagnostic_code": result["diagnostic_code"],
        "attempt_consumed": result["attempt_consumed"],
        "gateway_invoked": result["gateway_invoked"],
    }
    try:
        telemetry(**fields)
    except Exception:
        # A telemetry sink is advisory and cannot turn a safe result into an
        # untyped failure. Cancellation is intentionally not caught here.
        pass


def _safe_optional_int(value: Any, *, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return min(maximum, max(0, value))


def _safe_structural_status(value: Any) -> str:
    # Candidate provenance is not reviewed yet; operational errors belong in
    # ``diagnostic_code`` and must not become candidate statuses.
    return "UNPROVEN"


def _safe_structural_provenance(value: Any) -> str:
    if isinstance(value, str) and value in _STRUCTURAL_PROVENANCE_CODES:
        return value
    return "STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN"


def _safe_structural_diagnostic(value: Any) -> str:
    if isinstance(value, str) and value in _STRUCTURAL_DIAGNOSTICS:
        return value
    return "STRUCTURAL_PROVENANCE_NOT_VERIFIED"


def sanitize_structural_observation_result(value: Any) -> dict[str, object] | None:
    """Copy only the sealed candidate/provenance observation fields."""
    if not isinstance(value, Mapping):
        return None
    try:
        raw_candidates = value.get("candidates")
        candidate_by_id: dict[str, Mapping[str, object]] = {}
        if isinstance(raw_candidates, (list, tuple)):
            for raw_candidate in raw_candidates:
                if not isinstance(raw_candidate, Mapping):
                    continue
                raw_id = raw_candidate.get("candidate_id")
                if isinstance(raw_id, str) and raw_id in STRUCTURAL_CANDIDATE_IDS:
                    candidate_by_id.setdefault(raw_id, raw_candidate)

        candidates: list[dict[str, object]] = []
        for candidate_id in STRUCTURAL_CANDIDATE_IDS:
            raw_candidate = candidate_by_id.get(candidate_id)
            if raw_candidate is None:
                candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "status": "UNPROVEN",
                        "match_count": None,
                        "visible": None,
                        "provenance_code": "STRUCTURAL_SELECTOR_CONTRACT_NOT_PROVEN",
                    }
                )
                continue
            candidate_status = _safe_structural_status(raw_candidate.get("status"))
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "status": candidate_status,
                    # No reviewed candidate contract exists yet, so the
                    # permanent UNPROVEN state carries no count/visibility
                    # claim even when an untrusted gateway supplies values.
                    "match_count": (
                        _safe_optional_int(
                            raw_candidate.get("match_count"),
                            maximum=2,
                        )
                        if candidate_status != "UNPROVEN"
                        else None
                    ),
                    "visible": (
                        _safe_bool(raw_candidate.get("visible"))
                        if candidate_status != "UNPROVEN"
                        else None
                    ),
                    "provenance_code": _safe_structural_provenance(
                        raw_candidate.get("provenance_code")
                    ),
                }
            )

        raw_counters = value.get("zero_action_counters")
        raw_navigation = (
            raw_counters.get("navigation")
            if isinstance(raw_counters, Mapping)
            else value.get("navigation_count")
        )
        navigation_count = _safe_int(raw_navigation, maximum=1)
        page_set_category = value.get("page_set_category")
        safe_page_set = (
            page_set_category
            if isinstance(page_set_category, str)
            and page_set_category in _STRUCTURAL_PAGESET_CATEGORIES
            else None
        )
        profile_binding = value.get("profile_binding")
        safe_profile = (
            profile_binding
            if isinstance(profile_binding, str)
            and profile_binding in _SAFE_PROFILE_BINDINGS
            else "UNAVAILABLE"
        )
        return {
            "phase": "LANDING_STRUCTURAL_OBSERVATION",
            "elapsed_ms": _safe_int(
                value.get("elapsed_ms"),
                maximum=LANDING_PROBE_RUNNER_MAX_ELAPSED_MS,
            ),
            "profile_binding": safe_profile,
            "page_set_category": safe_page_set,
            "navigation_count": navigation_count,
            "candidate_count": len(candidates),
            "expected_host_reached": _safe_bool(value.get("expected_host_reached")),
            "search_control_visible": _safe_bool(
                value.get("search_control_visible")
            ),
            "candidates": candidates,
            "selector_contract_version": STRUCTURAL_OBSERVATION_SELECTOR_CONTRACT_VERSION,
            "diagnostic_code": _safe_structural_diagnostic(
                value.get("diagnostic_code")
            ),
            "zero_action_counters": _empty_action_counters(navigation_count),
        }
    except Exception:
        return None


def _structural_runner_result(
    status: StructuralObservationRunStatus | str,
    *,
    diagnostic_code: str | None = None,
    attempt_consumed: bool = False,
    gateway_invoked: bool = False,
    observation: dict[str, object] | None = None,
) -> dict[str, object]:
    safe_status = status.value if isinstance(status, StructuralObservationRunStatus) else status
    if safe_status not in _SAFE_STRUCTURAL_RUN_STATUSES:
        safe_status = StructuralObservationRunStatus.FAILED.value
    allowed_diagnostics = _STRUCTURAL_DIAGNOSTICS | frozenset(
        _STRUCTURAL_RUNNER_DIAGNOSTICS.values()
    )
    safe_diagnostic = (
        diagnostic_code
        if isinstance(diagnostic_code, str) and diagnostic_code in allowed_diagnostics
        else _STRUCTURAL_RUNNER_DIAGNOSTICS[safe_status]
    )
    return {
        "phase": STRUCTURAL_OBSERVATION_RUNNER_PHASE,
        "status": safe_status,
        "diagnostic_code": safe_diagnostic,
        "attempt_consumed": bool(attempt_consumed),
        "gateway_invoked": bool(gateway_invoked),
        "observation": observation,
    }


def sanitize_structural_runner_result(value: Any) -> dict[str, object]:
    """Normalize a structural runner result before API exposure."""
    if not isinstance(value, Mapping):
        return _structural_runner_result(StructuralObservationRunStatus.FAILED)
    try:
        raw_status = value.get("status")
        status = (
            raw_status
            if isinstance(raw_status, str)
            and raw_status in _SAFE_STRUCTURAL_RUN_STATUSES
            else StructuralObservationRunStatus.FAILED.value
        )
        observation = sanitize_structural_observation_result(
            value.get("observation")
        )
        if status == StructuralObservationRunStatus.COMPLETED.value and observation is None:
            status = StructuralObservationRunStatus.FAILED.value
        raw_diagnostic = value.get("diagnostic_code")
        allowed_diagnostics = _STRUCTURAL_DIAGNOSTICS | frozenset(
            _STRUCTURAL_RUNNER_DIAGNOSTICS.values()
        )
        diagnostic = (
            raw_diagnostic
            if isinstance(raw_diagnostic, str) and raw_diagnostic in allowed_diagnostics
            else _STRUCTURAL_RUNNER_DIAGNOSTICS[status]
        )
        return _structural_runner_result(
            status,
            diagnostic_code=diagnostic,
            attempt_consumed=value.get("attempt_consumed") is True,
            gateway_invoked=value.get("gateway_invoked") is True,
            observation=observation,
        )
    except Exception:
        return _structural_runner_result(StructuralObservationRunStatus.FAILED)


def _emit_structural_runner_telemetry(
    result: Mapping[str, object],
    telemetry: Callable[..., Any] | None,
) -> None:
    if telemetry is None:
        return
    fields = {
        "event": "structural_observation.runner",
        "phase": STRUCTURAL_OBSERVATION_RUNNER_PHASE,
        "status": result["status"],
        "diagnostic_code": result["diagnostic_code"],
        "attempt_consumed": result["attempt_consumed"],
        "gateway_invoked": result["gateway_invoked"],
        "observation": result.get("observation"),
    }
    try:
        telemetry(**fields)
    except Exception:
        pass


async def run_structural_observation_once(
    runtime: WorkerRuntime,
    telemetry: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """Reject the retired one-shot path without touching worker state."""
    del runtime
    result = _structural_runner_result(
        StructuralObservationRunStatus.REQUEST_REJECTED,
        diagnostic_code="STRUCTURAL_RUNNER_RETIRED",
    )
    _emit_structural_runner_telemetry(result, telemetry)
    return result


def _bound_probe(runtime: Any) -> tuple[Any, Callable[..., Any]] | None:
    """Return the runtime's existing lock and bound browser probe only."""

    try:
        lock = getattr(runtime, "browser_lock", None)
        consume = getattr(runtime, "consume_landing_probe_attempt", None)
        external = getattr(runtime, "external", None)
        browser = getattr(external, "browser", None)
        probe = getattr(browser, "probe_landing_selector_proposals", None)
    except Exception:
        return None
    if (
        lock is None
        or not callable(getattr(lock, "acquire", None))
        or not callable(getattr(lock, "release", None))
        or not callable(consume)
        or not callable(probe)
    ):
        return None
    return lock, probe


async def run_landing_probe_once(
    runtime: WorkerRuntime,
    telemetry: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """Run the exact bound landing probe at most once for this runtime.

    The latch is consumed while the existing browser lock is held and before
    the gateway method is called. A busy lock therefore does not consume the
    attempt, while cancellation or an error after consumption cannot replay.
    No caller-controlled timeout is accepted.
    """

    bound = _bound_probe(runtime)
    if bound is None:
        unavailable_result = _runner_result(LandingProbeRunStatus.UNAVAILABLE)
        _emit_runner_telemetry(unavailable_result, telemetry)
        return unavailable_result
    lock, probe = bound

    try:
        acquired = await asyncio.wait_for(
            lock.acquire(),
            timeout=LANDING_PROBE_RUNNER_LOCK_WAIT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        busy_result = _runner_result(LandingProbeRunStatus.BUSY)
        _emit_runner_telemetry(busy_result, telemetry)
        return busy_result
    except Exception:
        unavailable_result = _runner_result(LandingProbeRunStatus.UNAVAILABLE)
        _emit_runner_telemetry(unavailable_result, telemetry)
        return unavailable_result

    if acquired is not True:
        busy_result = _runner_result(LandingProbeRunStatus.BUSY)
        _emit_runner_telemetry(busy_result, telemetry)
        return busy_result

    result: dict[str, object] | None = None
    try:
        try:
            consumed = getattr(runtime, "consume_landing_probe_attempt")()
        except asyncio.CancelledError:
            raise
        except Exception:
            result = _runner_result(LandingProbeRunStatus.UNAVAILABLE)
        else:
            if consumed is not True:
                result = _runner_result(LandingProbeRunStatus.ALREADY_CONSUMED)
            else:
                try:
                    value = probe(
                        timeout_seconds=LANDING_PROBE_RUNNER_TIMEOUT_SECONDS
                    )
                    if inspect.isawaitable(value):
                        value = await asyncio.wait_for(
                            value,
                            timeout=LANDING_PROBE_RUNNER_TIMEOUT_SECONDS,
                        )
                except asyncio.CancelledError:
                    raise
                except (asyncio.TimeoutError, TimeoutError):
                    result = _runner_result(
                        LandingProbeRunStatus.TIMEOUT,
                        attempt_consumed=True,
                        gateway_invoked=True,
                    )
                except Exception:
                    result = _runner_result(
                        LandingProbeRunStatus.FAILED,
                        attempt_consumed=True,
                        gateway_invoked=True,
                    )
                else:
                    safe_probe = sanitize_landing_probe_result(value)
                    if safe_probe is None:
                        result = _runner_result(
                            LandingProbeRunStatus.FAILED,
                            attempt_consumed=True,
                            gateway_invoked=True,
                        )
                    else:
                        result = _runner_result(
                            LandingProbeRunStatus.COMPLETED,
                            attempt_consumed=True,
                            gateway_invoked=True,
                            probe=safe_probe,
                        )
    finally:
        lock.release()

    assert result is not None
    _emit_runner_telemetry(result, telemetry)
    return result


# A descriptive alias keeps the one-shot boundary easy to discover for local
# callers without adding another HTTP route.
run_landing_probe = run_landing_probe_once

# Keep the retired structural names as compatibility aliases for imports that
# have not yet been removed from downstream deployments. They reject before
# touching runtime, locks, latches, or browser state.
run_structural_observation = run_structural_observation_once
run_structural_selector_observation_once = run_structural_observation_once
