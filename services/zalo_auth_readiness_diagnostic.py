"""Independent, read-only structural observation for the Zalo browser.

This module is intentionally a narrow bootstrap boundary.  It does not build
the FastAPI application, load dotenv/Config, initialize a database, dispatch a
job, or import any mutation-capable Zalo/BCNB/Form service.  The browser
gateway remains the owner of Playwright resources; this module owns only the
two diagnostic locks, the optional temporary profile, and its private
structural artifact.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import inspect
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, TypeVar
from urllib.parse import urlsplit
import uuid

from services.external_gateways import (
    LANDING_EXPECTED_HOST,
    PlaywrightBrowserGateway,
)
from services.profile_lock import InterProcessFileLock, ProfileLockTimeout


DIAGNOSTIC_PHASE = "ZALO_AUTH_READINESS_DIAGNOSTIC"
DIAGNOSTIC_SCHEMA_VERSION = "zalo-auth-readiness-diagnostic-v2"
DIAGNOSTIC_OBSERVATION_TIMEOUT_SECONDS = 120.0
DIAGNOSTIC_CLEANUP_TIMEOUT_SECONDS = 15.0
DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS = 5.0
DIAGNOSTIC_NAVIGATION_TIMEOUT_MS = 5_000
DIAGNOSTIC_MAX_CANDIDATES = 64
DIAGNOSTIC_SETTLE_TIMEOUT_SECONDS = 20.0
DIAGNOSTIC_SETTLE_POLL_SECONDS = 0.25
DIAGNOSTIC_MAX_SETTLE_ATTEMPTS = 128
DIAGNOSTIC_EXPECTED_URL = f"https://{LANDING_EXPECTED_HOST}/"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

_STRUCTURAL_SELECTOR = (
    "body button, body input, body textarea, body select, body [role], "
    "body [id], body [name], body [data-testid], body [data-qa]"
)
_INTERACTIVE_ARIA_ROLES = (
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
)
_INTERACTIVE_SELECTOR = ", ".join(
    (
        "body button",
        "body input",
        "body textarea",
        "body select",
        *(
            f"body [role='{role}']"
            for role in _INTERACTIVE_ARIA_ROLES
        ),
    )
)
_STRUCTURAL_ATTRIBUTE_NAMES = (
    "id",
    "name",
    "type",
    "role",
    "data-testid",
    "data-qa",
    "aria-controls",
    "aria-expanded",
    "aria-haspopup",
)
_SAFE_ATTRIBUTE_VALUE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_SAFE_TAG = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_FORBIDDEN_VALUE_MARKERS = (
    "@",
    "email",
    "phone",
    "mobile",
    "account",
    "username",
    "user-name",
)


class DiagnosticProfileKind(str, Enum):
    WORKER = "worker"
    EMPTY_TEMP = "empty-temp"


class DiagnosticFailure(RuntimeError):
    """Internal typed failure whose details never cross the output boundary."""

    def __init__(
        self,
        code: str,
        *,
        collection: Mapping[str, object] | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.collection = dict(collection) if collection is not None else None


@dataclass(frozen=True, slots=True)
class DiagnosticBootstrap:
    """Explicit, non-secret inputs for one diagnostic invocation."""

    profile_kind: DiagnosticProfileKind | str
    artifact_root: str | Path
    profile_path: str | Path | None = None
    profile_lock_path: str | Path | None = None
    observation_timeout_seconds: float = DIAGNOSTIC_OBSERVATION_TIMEOUT_SECONDS
    cleanup_timeout_seconds: float = DIAGNOSTIC_CLEANUP_TIMEOUT_SECONDS
    max_candidates: int = DIAGNOSTIC_MAX_CANDIDATES

    def __post_init__(self) -> None:
        try:
            kind = (
                self.profile_kind
                if isinstance(self.profile_kind, DiagnosticProfileKind)
                else DiagnosticProfileKind(str(self.profile_kind))
            )
        except ValueError as exc:
            raise ValueError("profile_kind must be worker or empty-temp") from exc
        object.__setattr__(self, "profile_kind", kind)

        artifact_root = _absolute_path(self.artifact_root)
        if _is_within(_REPOSITORY_ROOT, artifact_root):
            raise ValueError("artifact_root must be outside the repository")
        object.__setattr__(self, "artifact_root", str(artifact_root))

        if self.profile_kind is DiagnosticProfileKind.WORKER:
            if self.profile_path is None or self.profile_lock_path is None:
                raise ValueError(
                    "worker mode requires explicit profile_path and profile_lock_path"
                )
            profile_path = _require_absolute_path(self.profile_path, "profile_path")
            lock_path = _require_absolute_path(
                self.profile_lock_path,
                "profile_lock_path",
            )
            if _is_within(profile_path, artifact_root):
                raise ValueError("artifact_root must not be inside worker profile")
            object.__setattr__(self, "profile_path", str(profile_path))
            object.__setattr__(self, "profile_lock_path", str(lock_path))
        else:
            if self.profile_path is not None or self.profile_lock_path is not None:
                raise ValueError("empty-temp mode owns its profile and lock paths")
            object.__setattr__(self, "profile_path", None)
            object.__setattr__(self, "profile_lock_path", None)

        if self.observation_timeout_seconds <= 0:
            raise ValueError("observation_timeout_seconds must be positive")
        if self.observation_timeout_seconds > DIAGNOSTIC_OBSERVATION_TIMEOUT_SECONDS:
            raise ValueError("observation_timeout_seconds exceeds diagnostic bound")
        if self.cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup_timeout_seconds must be positive")
        if self.cleanup_timeout_seconds > DIAGNOSTIC_CLEANUP_TIMEOUT_SECONDS:
            raise ValueError("cleanup_timeout_seconds exceeds diagnostic bound")
        if self.max_candidates <= 0:
            raise ValueError("max_candidates must be positive")
        if self.max_candidates > DIAGNOSTIC_MAX_CANDIDATES:
            raise ValueError("max_candidates exceeds diagnostic bound")
        object.__setattr__(
            self,
            "observation_timeout_seconds",
            float(self.observation_timeout_seconds),
        )
        object.__setattr__(
            self,
            "cleanup_timeout_seconds",
            float(self.cleanup_timeout_seconds),
        )
        object.__setattr__(self, "max_candidates", int(self.max_candidates))


@dataclass(frozen=True, slots=True)
class _EffectiveBootstrap:
    value: Any
    temporary_profile_path: Path | None
    lock_path: Path


@dataclass(frozen=True, slots=True)
class _GatewayBootstrap:
    """Resolved non-secret values handed to the adapter factory only."""

    profile_kind: DiagnosticProfileKind
    artifact_root: str
    profile_path: str
    profile_lock_path: str
    observation_timeout_seconds: float
    cleanup_timeout_seconds: float
    max_candidates: int


T = TypeVar("T")


def _absolute_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def _require_absolute_path(value: str | Path, name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return path.resolve()


def _is_within(parent: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _default_gateway_factory(bootstrap: Any) -> PlaywrightBrowserGateway:
    """Build the same adapter with retries disabled for one diagnostic run."""
    return PlaywrightBrowserGateway(
        profile_path=bootstrap.profile_path,
        max_retries=0,
        backoff_seconds=0.0,
        failure_threshold=1,
        cooldown_seconds=1.0,
    )


def _default_process_lock_factory(path: str) -> InterProcessFileLock:
    return InterProcessFileLock(
        path,
        timeout_seconds=0.1,
        poll_seconds=0.05,
        reclaim_stale=False,
    )


def _profile_kind_value(bootstrap: DiagnosticBootstrap) -> str:
    value = bootstrap.profile_kind
    return value.value if isinstance(value, DiagnosticProfileKind) else str(value)


async def _bounded(value: T | Any, *, timeout_seconds: float) -> T:
    if inspect.isawaitable(value):
        return await asyncio.wait_for(value, timeout=max(0.001, timeout_seconds))
    return value


async def _shielded(awaitable: Any) -> Any:
    """Finish one cleanup awaitable even when its caller is cancelled."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def _safe_count(value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return min(maximum, max(0, value))


def _safe_page_host(value: Any) -> str | None:
    """Return only the parsed host; never retain or expose the URL itself."""
    if not isinstance(value, str):
        return None
    try:
        return (urlsplit(value).hostname or "").casefold()
    except (TypeError, ValueError):
        return None


def _safe_attribute_value(name: str, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    folded = normalized.casefold()
    if (
        not normalized
        or len(normalized) > 128
        or any(marker in folded for marker in _FORBIDDEN_VALUE_MARKERS)
        or _SAFE_ATTRIBUTE_VALUE.fullmatch(normalized) is None
    ):
        return None
    return normalized


def _selector_for(tag: str, attributes: Mapping[str, str]) -> str:
    identifier = attributes.get("id")
    if identifier:
        return f"#{identifier}"
    name = attributes.get("name")
    if name:
        return f'{tag}[name="{name}"]'
    role = attributes.get("role")
    if role:
        return f'{tag}[role="{role}"]'
    test_id = attributes.get("data-testid") or attributes.get("data-qa")
    if test_id:
        attribute_name = "data-testid" if attributes.get("data-testid") else "data-qa"
        return f'{tag}[{attribute_name}="{test_id}"]'
    return tag


def _metadata_evaluate_expression() -> str:
    """Return a fixed expression that cannot read text, URL, or page state."""
    names = ",".join(
        f"{json.dumps(name)}: element.getAttribute({json.dumps(name)})"
        for name in _STRUCTURAL_ATTRIBUTE_NAMES
    )
    return "element => ({tag: element.tagName.toLowerCase(), attributes: {" + names + "}})"


def _sanitize_candidate(
    value: Any,
) -> tuple[str, str, str | None, dict[str, str]] | None:
    if not isinstance(value, dict):
        return None
    raw_tag = value.get("tag")
    tag = str(raw_tag).strip().casefold() if isinstance(raw_tag, str) else ""
    if _SAFE_TAG.fullmatch(tag) is None:
        return None
    raw_attributes = value.get("attributes")
    if not isinstance(raw_attributes, dict):
        raw_attributes = {}
    attributes: dict[str, str] = {}
    for name in _STRUCTURAL_ATTRIBUTE_NAMES:
        safe_value = _safe_attribute_value(name, raw_attributes.get(name))
        if safe_value is not None:
            attributes[name] = safe_value
    selector = _selector_for(tag, attributes)
    role = attributes.get("role")
    return selector, tag, role, attributes


class ZaloAuthReadinessDiagnostic:
    """Run one owned, read-only structural browser observation."""

    def __init__(
        self,
        bootstrap: DiagnosticBootstrap,
        *,
        gateway_factory: Callable[[DiagnosticBootstrap], Any] | None = None,
        process_lock_factory: Callable[[str], InterProcessFileLock] | None = None,
        runtime_lock: asyncio.Lock | None = None,
    ) -> None:
        if not isinstance(bootstrap, DiagnosticBootstrap):
            raise TypeError("bootstrap must be DiagnosticBootstrap")
        self.bootstrap = bootstrap
        self._gateway_factory = gateway_factory or _default_gateway_factory
        self._process_lock_factory = (
            process_lock_factory or _default_process_lock_factory
        )
        self._runtime_lock = runtime_lock or asyncio.Lock()
        self._profile_reuse_blocked = False

    async def _effective_bootstrap(self) -> _EffectiveBootstrap:
        root = Path(self.bootstrap.artifact_root)
        root.mkdir(parents=True, exist_ok=True)
        if self.bootstrap.profile_kind is DiagnosticProfileKind.WORKER:
            assert self.bootstrap.profile_path is not None
            assert self.bootstrap.profile_lock_path is not None
            return _EffectiveBootstrap(
                value=self.bootstrap,
                temporary_profile_path=None,
                lock_path=Path(self.bootstrap.profile_lock_path),
            )

        temporary = Path(
            tempfile.mkdtemp(
                prefix=".zalo-auth-diagnostic-profile-",
                dir=root,
            )
        ).resolve()
        lock_path = root / f"{temporary.name}.lock"
        value = _GatewayBootstrap(
            profile_kind=DiagnosticProfileKind.EMPTY_TEMP,
            artifact_root=str(root),
            profile_path=str(temporary),
            profile_lock_path=str(lock_path),
            observation_timeout_seconds=self.bootstrap.observation_timeout_seconds,
            cleanup_timeout_seconds=self.bootstrap.cleanup_timeout_seconds,
            max_candidates=self.bootstrap.max_candidates,
        )
        return _EffectiveBootstrap(
            value=value,
            temporary_profile_path=temporary,
            lock_path=lock_path,
        )

    @staticmethod
    def _base_result(
        *,
        bootstrap: DiagnosticBootstrap,
        status: str,
        diagnostic_code: str,
        page_set: str | None = None,
        navigation_count: int = 0,
        candidate_count: int = 0,
        artifact_name: str | None = None,
        collection: Mapping[str, object] | None = None,
        cleanup_code: str | None = None,
        cleanup_invariant_ok: bool | None = None,
        cleanup_incomplete: bool | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "phase": DIAGNOSTIC_PHASE,
            "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "status": status,
            "diagnostic_code": diagnostic_code,
            "profile_kind": _profile_kind_value(bootstrap),
            "page_set": page_set,
            "navigation_count": min(1, max(0, int(navigation_count))),
            "candidate_count": min(
                max(0, int(bootstrap.max_candidates)),
                max(0, int(candidate_count)),
            ),
            "artifact_name": artifact_name,
        }
        if cleanup_code is not None:
            result["cleanup_code"] = cleanup_code
        if cleanup_invariant_ok is not None:
            result["cleanup_invariant_ok"] = cleanup_invariant_ok
        if cleanup_incomplete is not None:
            result["cleanup_incomplete"] = cleanup_incomplete
        if collection is not None:
            result["collection"] = dict(collection)
        return result

    def _collection_summary(
        self,
        *,
        broad_matches: int = 0,
        visible_interactive_matches: int = 0,
        evaluated: int = 0,
        accepted: int = 0,
        rejected: int = 0,
        element_errors: int = 0,
        settle_attempts: int = 0,
        outcome: str = "NOT_RUN",
    ) -> dict[str, object]:
        return {
            "collection_scope": "MAIN_FRAME",
            "broad_matches": _safe_count(
                broad_matches,
                maximum=self.bootstrap.max_candidates,
            ),
            "visible_interactive_matches": _safe_count(
                visible_interactive_matches,
                maximum=self.bootstrap.max_candidates,
            ),
            "evaluated": _safe_count(
                evaluated,
                maximum=self.bootstrap.max_candidates,
            ),
            "accepted": _safe_count(
                accepted,
                maximum=self.bootstrap.max_candidates,
            ),
            "rejected": _safe_count(
                rejected,
                maximum=self.bootstrap.max_candidates,
            ),
            "element_errors": _safe_count(
                element_errors,
                maximum=self.bootstrap.max_candidates,
            ),
            "settle_attempts": _safe_count(
                settle_attempts,
                maximum=DIAGNOSTIC_MAX_SETTLE_ATTEMPTS,
            ),
            "outcome": outcome,
        }

    async def _select_default_page(self, session: Any) -> tuple[Any, str]:
        context = getattr(session, "context", None)
        pages = getattr(context, "pages", None)
        if pages is None or isinstance(pages, (str, bytes, dict, set, frozenset)):
            raise DiagnosticFailure("PAGESET_UNAVAILABLE")
        try:
            page_count = len(pages)
        except (TypeError, AttributeError):
            raise DiagnosticFailure("PAGESET_UNAVAILABLE") from None
        if page_count > 1:
            raise DiagnosticFailure("PAGESET_AMBIGUOUS")
        if page_count == 1:
            return pages[0], "DEFAULT_PAGE_ACCEPTED"
        new_page = getattr(session, "new_page", None)
        if not callable(new_page):
            raise DiagnosticFailure("PAGESET_EMPTY_NO_OWNED_PAGE")
        page = await _bounded(
            new_page(),
            timeout_seconds=DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS,
        )
        if page is None:
            raise DiagnosticFailure("PAGE_CREATE_FAILED")
        return page, "OWNED_PAGE_CREATED"

    async def _prepare_page(self, page: Any) -> int:
        marker = getattr(page, "is_closed", None)
        if callable(marker):
            closed = await _bounded(
                marker(),
                timeout_seconds=DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS,
            )
            if closed is True:
                raise DiagnosticFailure(
                    "PAGE_CLOSED",
                    collection=self._collection_summary(outcome="PAGE_CLOSED"),
                )
        elif getattr(page, "closed", False) is True:
            raise DiagnosticFailure(
                "PAGE_CLOSED",
                collection=self._collection_summary(outcome="PAGE_CLOSED"),
            )

        try:
            current_url = getattr(page, "url", "")
        except Exception:
            current_url = None
        if _safe_page_host(current_url) == LANDING_EXPECTED_HOST:
            return 0

        goto = getattr(page, "goto", None)
        if not callable(goto):
            raise DiagnosticFailure(
                "NAVIGATION_UNSUPPORTED",
                collection=self._collection_summary(
                    outcome="NAVIGATION_UNSUPPORTED",
                ),
            )

        response: Any = None
        try:
            response = await _bounded(
                goto(
                    DIAGNOSTIC_EXPECTED_URL,
                    timeout=DIAGNOSTIC_NAVIGATION_TIMEOUT_MS,
                ),
                timeout_seconds=DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError):
            raise DiagnosticFailure(
                "NAVIGATION_TIMEOUT",
                collection=self._collection_summary(
                    outcome="NAVIGATION_TIMEOUT",
                ),
            ) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise DiagnosticFailure(
                "NAVIGATION_FAILED",
                collection=self._collection_summary(
                    outcome="NAVIGATION_FAILED",
                ),
            ) from None

        if response is not None:
            try:
                response_ok = getattr(response, "ok", None)
                response_status = getattr(response, "status", None)
            except Exception:
                raise DiagnosticFailure(
                    "NAVIGATION_HTTP_ERROR",
                    collection=self._collection_summary(
                        outcome="NAVIGATION_HTTP_ERROR",
                    ),
                ) from None
            if response_ok is False or (
                isinstance(response_status, int) and response_status >= 400
            ):
                raise DiagnosticFailure(
                    "NAVIGATION_HTTP_ERROR",
                    collection=self._collection_summary(
                        outcome="NAVIGATION_HTTP_ERROR",
                    ),
                )

        try:
            post_navigation_url = getattr(page, "url", "")
        except Exception:
            post_navigation_url = None
        if _safe_page_host(post_navigation_url) != LANDING_EXPECTED_HOST:
            raise DiagnosticFailure(
                "NAVIGATION_HOST_MISMATCH",
                collection=self._collection_summary(
                    outcome="NAVIGATION_HOST_MISMATCH",
                ),
            )
        return 1

    async def _discover(
        self,
        page: Any,
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        locator_factory = getattr(page, "locator", None)
        if not callable(locator_factory):
            raise DiagnosticFailure(
                "STRUCTURAL_DISCOVERY_UNSUPPORTED",
                collection=self._collection_summary(
                    outcome="STRUCTURAL_DISCOVERY_UNSUPPORTED",
                ),
            )
        try:
            candidates_locator = locator_factory(_STRUCTURAL_SELECTOR)
            interactive_locator = locator_factory(_INTERACTIVE_SELECTOR)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise DiagnosticFailure(
                "STRUCTURAL_DISCOVERY_FAILED",
                collection=self._collection_summary(
                    outcome="STRUCTURAL_DISCOVERY_FAILED",
                ),
            ) from None

        settle_started = asyncio.get_running_loop().time()
        settle_budget = min(
            DIAGNOSTIC_SETTLE_TIMEOUT_SECONDS,
            max(
                0.001,
                self.bootstrap.observation_timeout_seconds * 0.9,
            ),
        )
        settle_deadline = settle_started + max(0.001, settle_budget)
        settle_attempts = 0
        broad_matches = 0
        visible_interactive_matches = 0
        previous_sample: tuple[int, int] | None = None
        stable_samples = 0

        while settle_attempts < DIAGNOSTIC_MAX_SETTLE_ATTEMPTS:
            remaining = settle_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            settle_attempts += 1
            try:
                raw_count = await _bounded(
                    candidates_locator.count(),
                    timeout_seconds=min(
                        DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS,
                        remaining,
                    ),
                )
                raw_interactive_count = await _bounded(
                    interactive_locator.count(),
                    timeout_seconds=min(
                        DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS,
                        remaining,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                if (
                    previous_sample is not None
                    and asyncio.get_running_loop().time() >= settle_deadline
                ):
                    break
                raise DiagnosticFailure(
                    "STRUCTURAL_DISCOVERY_TIMEOUT",
                    collection=self._collection_summary(
                        settle_attempts=settle_attempts,
                        outcome="STRUCTURAL_DISCOVERY_TIMEOUT",
                    ),
                ) from None
            except Exception:
                raise DiagnosticFailure(
                    "STRUCTURAL_DISCOVERY_FAILED",
                    collection=self._collection_summary(
                        settle_attempts=settle_attempts,
                        outcome="STRUCTURAL_DISCOVERY_FAILED",
                    ),
                ) from None

            broad_matches = _safe_count(
                raw_count,
                maximum=self.bootstrap.max_candidates,
            )
            interactive_matches = _safe_count(
                raw_interactive_count,
                maximum=self.bootstrap.max_candidates,
            )
            visible_interactive_matches = 0
            for index in range(interactive_matches):
                try:
                    visible = await _bounded(
                        interactive_locator.nth(index).is_visible(),
                        timeout_seconds=DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS,
                    )
                except asyncio.CancelledError:
                    raise
                except (asyncio.TimeoutError, TimeoutError):
                    raise DiagnosticFailure(
                        "STRUCTURAL_DISCOVERY_TIMEOUT",
                        collection=self._collection_summary(
                            broad_matches=broad_matches,
                            visible_interactive_matches=visible_interactive_matches,
                            settle_attempts=settle_attempts,
                            outcome="STRUCTURAL_DISCOVERY_TIMEOUT",
                        ),
                    ) from None
                except Exception:
                    raise DiagnosticFailure(
                        "STRUCTURAL_DISCOVERY_FAILED",
                        collection=self._collection_summary(
                            broad_matches=broad_matches,
                            visible_interactive_matches=visible_interactive_matches,
                            settle_attempts=settle_attempts,
                            outcome="STRUCTURAL_DISCOVERY_FAILED",
                        ),
                    ) from None
                if visible is True:
                    visible_interactive_matches += 1

            sample = (broad_matches, visible_interactive_matches)
            if (
                broad_matches > 0
                and visible_interactive_matches > 0
                and sample == previous_sample
            ):
                stable_samples += 1
            elif broad_matches > 0 or visible_interactive_matches > 0:
                stable_samples = 1
            else:
                stable_samples = 0
            previous_sample = sample

            if (
                broad_matches > 0
                and visible_interactive_matches > 0
                and stable_samples >= 2
            ):
                break
            remaining = settle_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(
                min(DIAGNOSTIC_SETTLE_POLL_SECONDS, remaining)
            )

        if (
            broad_matches == 0
            or visible_interactive_matches == 0
            or stable_samples < 2
        ):
            raise DiagnosticFailure(
                "UI_NOT_MATERIALIZED",
                collection=self._collection_summary(
                    broad_matches=broad_matches,
                    visible_interactive_matches=visible_interactive_matches,
                    settle_attempts=settle_attempts,
                    outcome="UI_NOT_MATERIALIZED",
                ),
            )

        expression = _metadata_evaluate_expression()
        results: list[dict[str, object]] = []
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        evaluated = 0
        accepted = 0
        rejected = 0
        element_errors = 0
        for index in range(broad_matches):
            evaluated += 1
            try:
                item = candidates_locator.nth(index)
                visible_value = await _bounded(
                    item.is_visible(),
                    timeout_seconds=DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS,
                )
                metadata = await _bounded(
                    item.evaluate(expression),
                    timeout_seconds=DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS,
                )
                sanitized = _sanitize_candidate(metadata)
                if sanitized is None:
                    rejected += 1
                    continue
                selector, tag, role, attributes = sanitized
                key = (selector, tuple(sorted(attributes.items())))
                if key in seen:
                    rejected += 1
                    continue
                seen.add(key)
                selector_count = broad_matches
                try:
                    selector_locator = locator_factory(selector)
                    selector_count = _safe_count(
                        await _bounded(
                            selector_locator.count(),
                            timeout_seconds=DIAGNOSTIC_OPERATION_TIMEOUT_SECONDS,
                        ),
                        maximum=self.bootstrap.max_candidates,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    element_errors += 1
                    selector_count = 1
                results.append(
                    {
                        "selector": selector,
                        "selector_fingerprint": hashlib.sha256(
                            selector.encode("utf-8")
                        ).hexdigest()[:16],
                        "tag": tag,
                        "role": role,
                        "attributes": attributes,
                        "count": selector_count,
                        "visible": visible_value is True,
                    }
                )
                accepted += 1
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                element_errors += 1
            except Exception:
                element_errors += 1

        if element_errors > 0:
            outcome = "ELEMENT_COLLECTION_FAILED"
        elif accepted == 0:
            outcome = "METADATA_REJECTED"
        else:
            outcome = "METADATA_COLLECTED"
        return results, self._collection_summary(
            broad_matches=broad_matches,
            visible_interactive_matches=visible_interactive_matches,
            evaluated=evaluated,
            accepted=accepted,
            rejected=rejected,
            element_errors=element_errors,
            settle_attempts=settle_attempts,
            outcome=outcome,
        )

    async def _observe(self, session: Any) -> tuple[dict[str, object], str, int]:
        page, page_set = await self._select_default_page(session)
        navigation_count = await self._prepare_page(page)
        candidates, collection = await self._discover(page)
        return {
            "page_set": page_set,
            "navigation_count": navigation_count,
            "candidates": candidates,
            "collection": collection,
        }, page_set, navigation_count

    @staticmethod
    def _cleanup_summary(
        gateway: Any,
    ) -> tuple[str | None, bool | None, bool | None]:
        cleanup = getattr(gateway, "last_cleanup_result", None)
        if cleanup is None:
            return None, None, None
        code = getattr(cleanup, "result", None) or getattr(cleanup, "error_code", None)
        invariant = getattr(cleanup, "invariant_ok", None)
        incomplete = getattr(cleanup, "cleanup_incomplete", None)
        return (
            code if isinstance(code, str) else None,
            invariant if isinstance(invariant, bool) else None,
            incomplete if isinstance(incomplete, bool) else None,
        )

    async def _persistent_session(self, gateway: Any, timeout_seconds: float) -> Any:
        try:
            return gateway.persistent_session(
                cleanup_timeout_seconds=timeout_seconds,
            )
        except TypeError:
            # A narrow compatibility path for deterministic adapter fakes; the
            # production gateway accepts the explicit cleanup budget.
            return gateway.persistent_session()

    async def _write_artifact(
        self,
        bootstrap: DiagnosticBootstrap,
        *,
        status: str,
        diagnostic_code: str,
        page_set: str | None,
        navigation_count: int,
        candidates: list[dict[str, object]],
        collection: Mapping[str, object],
    ) -> str:
        root = Path(bootstrap.artifact_root)
        root.mkdir(parents=True, exist_ok=True)
        name = f"zalo-auth-{_profile_kind_value(bootstrap)}-{uuid.uuid4().hex}.json"
        target = root / name
        temporary = root / f".{name}.tmp"
        payload = {
            "phase": DIAGNOSTIC_PHASE,
            "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "diagnostic_code": diagnostic_code,
            "profile_kind": _profile_kind_value(bootstrap),
            "page_set": page_set,
            "navigation_count": min(1, max(0, int(navigation_count))),
            "collection": dict(collection),
            "candidates": candidates[: bootstrap.max_candidates],
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(target)
        return name

    async def _remove_temporary_profile(self, path: Path | None) -> bool:
        if path is None:
            return True
        root = Path(self.bootstrap.artifact_root).resolve()
        if not _is_within(root, path.resolve()) or not path.name.startswith(
            ".zalo-auth-diagnostic-profile-"
        ):
            return False
        removal = asyncio.create_task(asyncio.to_thread(shutil.rmtree, path))
        try:
            while not removal.done():
                try:
                    await asyncio.shield(removal)
                except asyncio.CancelledError:
                    # The owned deletion must finish before the diagnostic
                    # releases its runtime lock or propagates cancellation.
                    continue
            removal.result()
        except OSError:
            return False
        finally:
            if not removal.done():
                removal.cancel()
        # The caller's active cancellation is still propagated by run()'s
        # surrounding finally; this helper deliberately does not interrupt
        # lock release after the deletion has completed.
        return not path.exists()

    async def run(self) -> dict[str, object]:
        """Run once and return only sanitized lifecycle facts."""
        if self._profile_reuse_blocked:
            return self._base_result(
                bootstrap=self.bootstrap,
                status="BUSY",
                diagnostic_code="CLEANUP_INCOMPLETE",
                cleanup_invariant_ok=False,
                cleanup_incomplete=True,
            )
        if self._runtime_lock.locked():
            return self._base_result(
                bootstrap=self.bootstrap,
                status="BUSY",
                diagnostic_code="RUNTIME_LOCK_BUSY",
            )

        await self._runtime_lock.acquire()
        effective: _EffectiveBootstrap | None = None
        process_lock: InterProcessFileLock | None = None
        process_lock_acquired = False
        gateway: Any | None = None
        browser_ownership_attempted = False
        profile_lock_must_remain = False
        profile_lock_released = not process_lock_acquired
        result: dict[str, object] | None = None
        try:
            effective = await self._effective_bootstrap()
            process_lock = self._process_lock_factory(str(effective.lock_path))
            try:
                await process_lock.acquire(timeout_seconds=0.1)
                process_lock_acquired = True
            except ProfileLockTimeout:
                result = self._base_result(
                    bootstrap=self.bootstrap,
                    status="BUSY",
                    diagnostic_code="PROFILE_LOCK_BUSY",
                )
            else:
                gateway = self._gateway_factory(effective.value)
                configured_profile = getattr(gateway, "profile_path", None)
                expected_profile = Path(
                    effective.value.profile_path or ""
                ).resolve()
                configured_profile_path = (
                    Path(configured_profile).expanduser().resolve()
                    if isinstance(configured_profile, (str, Path))
                    else None
                )
                if configured_profile_path != expected_profile:
                    result = self._base_result(
                        bootstrap=self.bootstrap,
                        status="BROWSER_ERROR",
                        diagnostic_code="PROFILE_BINDING_MISMATCH",
                    )
                else:
                    status = "COMPLETED"
                    diagnostic_code = "STRUCTURAL_METADATA_COLLECTED"
                    page_set: str | None = None
                    navigation_count = 0
                    candidates: list[dict[str, object]] = []
                    collection: Mapping[str, object] = self._collection_summary()
                    try:
                        # Once browser ownership is attempted, an uncertain
                        # cleanup result must retain the profile lease rather
                        # than allowing an automatic reuse.
                        browser_ownership_attempted = True
                        profile_lock_must_remain = True
                        session_context = await self._persistent_session(
                            gateway,
                            self.bootstrap.cleanup_timeout_seconds,
                        )
                        async def observe_session() -> tuple[
                            dict[str, object],
                            str,
                            int,
                        ]:
                            async with session_context as session:
                                return await self._observe(session)

                        observation, page_set, navigation_count = await asyncio.wait_for(
                            observe_session(),
                            timeout=self.bootstrap.observation_timeout_seconds,
                        )
                        candidates = observation["candidates"]  # type: ignore[assignment]
                        collection = observation["collection"]  # type: ignore[assignment]
                        outcome = collection.get("outcome")
                        if outcome == "NO_MATCHES_AFTER_SETTLE":
                            diagnostic_code = "NO_MATCHES_AFTER_SETTLE"
                        elif outcome == "METADATA_REJECTED":
                            status = "BROWSER_ERROR"
                            diagnostic_code = "METADATA_REJECTED"
                        elif outcome == "ELEMENT_COLLECTION_FAILED":
                            status = "BROWSER_ERROR"
                            diagnostic_code = "ELEMENT_COLLECTION_FAILED"
                        elif outcome == "MAIN_FRAME_SETTLE_TIMEOUT":
                            status = "BROWSER_ERROR"
                            diagnostic_code = "MAIN_FRAME_SETTLE_TIMEOUT"
                    except asyncio.CancelledError:
                        raise
                    except (asyncio.TimeoutError, TimeoutError):
                        status = "TIMEOUT"
                        diagnostic_code = "OBSERVATION_TIMEOUT"
                    except DiagnosticFailure as exc:
                        status = "BROWSER_ERROR"
                        diagnostic_code = exc.code
                        if exc.collection is not None:
                            collection = exc.collection
                    except Exception:
                        status = "BROWSER_ERROR"
                        diagnostic_code = "BROWSER_ERROR"

                    (
                        cleanup_code,
                        cleanup_invariant,
                        cleanup_incomplete,
                    ) = self._cleanup_summary(gateway)
                    if cleanup_incomplete is True:
                        status = "BROWSER_ERROR"
                        diagnostic_code = "CLEANUP_INCOMPLETE"
                    if cleanup_invariant is False:
                        status = "BROWSER_ERROR"
                        if cleanup_incomplete is not True:
                            diagnostic_code = "CLEANUP_FAILED"
                    profile_lock_must_remain = (
                        profile_lock_must_remain
                        and (
                            cleanup_incomplete is True
                            or cleanup_invariant is not True
                        )
                    )

                    artifact_name: str | None = None
                    try:
                        artifact_name = await self._write_artifact(
                            self.bootstrap,
                            status=status,
                            diagnostic_code=diagnostic_code,
                            page_set=page_set,
                            navigation_count=navigation_count,
                            candidates=candidates,
                            collection=collection,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        status = "BROWSER_ERROR"
                        diagnostic_code = "ARTIFACT_WRITE_FAILED"
                    result = self._base_result(
                        bootstrap=self.bootstrap,
                        status=status,
                        diagnostic_code=diagnostic_code,
                        page_set=page_set,
                        navigation_count=navigation_count,
                        candidate_count=len(candidates),
                        artifact_name=artifact_name,
                        collection=collection,
                        cleanup_code=cleanup_code,
                        cleanup_invariant_ok=cleanup_invariant,
                        cleanup_incomplete=cleanup_incomplete,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep bootstrap, lock, adapter, and artifact failures inside the
            # sanitized diagnostic envelope; paths and exception messages are
            # never printed by the CLI.
            if result is None:
                result = self._base_result(
                    bootstrap=self.bootstrap,
                    status="BROWSER_ERROR",
                    diagnostic_code="DIAGNOSTIC_FAILED",
                )
        finally:
            if browser_ownership_attempted and gateway is not None:
                cleanup = getattr(gateway, "last_cleanup_result", None)
                profile_lock_must_remain = (
                    cleanup is None
                    or getattr(cleanup, "cleanup_incomplete", False) is True
                    or getattr(cleanup, "invariant_ok", None) is not True
                )
            if (
                process_lock_acquired
                and process_lock is not None
                and not profile_lock_must_remain
            ):
                try:
                    await _shielded(process_lock.release())
                    profile_lock_released = (
                        not process_lock.held
                        and effective is not None
                        and not effective.lock_path.exists()
                    )
                except asyncio.CancelledError:
                    # An interrupted release is not proof that the profile
                    # lease is gone; retain the lock and owned temp profile.
                    profile_lock_must_remain = True
                except Exception:
                    profile_lock_must_remain = True
                if not profile_lock_released:
                    profile_lock_must_remain = True
            if profile_lock_must_remain:
                self._profile_reuse_blocked = True
            temporary_cleanup_ok = True
            if (
                effective is not None
                and effective.temporary_profile_path is not None
                and profile_lock_released
                and not profile_lock_must_remain
            ):
                temporary_cleanup_ok = await self._remove_temporary_profile(
                    effective.temporary_profile_path
                )
            self._runtime_lock.release()
            if not temporary_cleanup_ok and result is not None:
                result = dict(result)
                result["status"] = "BROWSER_ERROR"
                result["diagnostic_code"] = "TEMP_PROFILE_CLEANUP_FAILED"
                result["cleanup_invariant_ok"] = False
        if result is None:
            raise RuntimeError("diagnostic produced no sanitized result")
        return result


__all__ = [
    "DIAGNOSTIC_CLEANUP_TIMEOUT_SECONDS",
    "DIAGNOSTIC_EXPECTED_URL",
    "DIAGNOSTIC_OBSERVATION_TIMEOUT_SECONDS",
    "DIAGNOSTIC_PHASE",
    "DiagnosticBootstrap",
    "DiagnosticFailure",
    "DiagnosticProfileKind",
    "ZaloAuthReadinessDiagnostic",
]
