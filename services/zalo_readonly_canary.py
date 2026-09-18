"""Reviewed, code-sealed, single-target read-only canaries.

This module is intentionally independent from :mod:`core.container`, the job
dispatcher, repositories, BCNB services, and Google Form services.  It owns
one browser observation and can persist only a bounded private evidence
summary outside the repository.  No readiness fact is injected by callers;
the production gateway supplies the code-sealed adapter.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time
import json
from pathlib import Path
from time import monotonic
from typing import Any, Mapping
import uuid

from services.external_gateways import (
    CLEANUP_ALREADY_CLOSED,
    CLEANUP_OK,
    ExternalGateways,
)
from core.time_utils import now_business, utc_iso
from services.profile_lock import InterProcessFileLock, ProfileLockTimeout
from services.zalo_evidence import (
    INSUFFICIENT_EVIDENCE,
    INSUFFICIENT_STATUS,
    LESSON_14_EXEMPTION,
    LESSON_14_STATUS,
    NOT_FOUND_STATUS,
    NOT_FOUND_VERIFIED,
    SUBMITTED_LATE_STATUS,
    SUBMITTED_LATE_VERIFIED,
    SUBMITTED_STATUS,
    SUBMITTED_VERIFIED,
)
from services.zalo_auth_readiness import (
    AUTH_READINESS_SELECTOR_CONTRACT_VERSION,
    READY_REASON,
    ZaloAuthReadinessAdapter,
)
from services.zalo_parallel_service import (
    _scan_one_target,
    classify_landing_readiness,
)
from services.zalo_scan_evidence import (
    EvidenceBoundsExceeded,
    ensure_zalo_history_synced,
)


CANARY_PHASE = "ZALO_ARMB19_READ_ONLY_CANARY"
CANARY_SCHEMA_VERSION = "zalo-armb19-read-only-canary-v1"
GA38_CANARY_PHASE = "ZALO_VT_C4K_GA38_READ_ONLY_CANARY"
GA38_CANARY_SCHEMA_VERSION = "zalo-vt-c4k-ga38-read-only-canary-v1"
GA38_CANARY_ARTIFACT_PREFIX = "zalo-vt-c4k-ga38-canary"
GA38_CANARY_WORKER_ID = "ga38-canary"
GA38_CANARY_PERSON = "GA38_READ_ONLY_CANARY"
GA38_CANARY_DEADLINE = "2026-09-01T20:00:00+07:00"
PTI10_CANARY_PHASE = "ZALO_VT_C4K_PTI10_READ_ONLY_CANARY"
PTI10_CANARY_SCHEMA_VERSION = "zalo-vt-c4k-pti10-read-only-canary-v1"
PTI10_CANARY_ARTIFACT_PREFIX = "zalo-vt-c4k-pti10-canary"
PTI10_CANARY_WORKER_ID = "pti10-canary"
PTI10_CANARY_PERSON = "PTI10_READ_ONLY_CANARY"
PTI10_CANARY_DEADLINE = "2026-09-16T23:59:59+07:00"
CANARY_DESCRIPTOR_INVALID = "INVALID_CANARY_DESCRIPTOR"
CANARY_TARGET_MISMATCH = "CANARY_DESCRIPTOR_MISMATCH"
CANARY_MAX_RUNTIME_SECONDS = 300.0
CANARY_MAX_SCROLLS = 60
CANARY_MAX_MESSAGES = 1000
CANARY_CLEANUP_TIMEOUT_SECONDS = 15.0
CANARY_NAVIGATION_TIMEOUT_SECONDS = 30.0

CANARY_READY = "READY"
CANARY_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
CANARY_AUTH_NOT_READY = "AUTH_NOT_READY"
CANARY_GROUP_IDENTITY_UNVERIFIED = "GROUP_IDENTITY_UNVERIFIED"
CANARY_MESSAGE_WINDOW_UNVERIFIED = "MESSAGE_WINDOW_UNVERIFIED"
CANARY_CAPTURE_INCOMPLETE = "CAPTURE_INCOMPLETE"
CANARY_BOUNDS_EXCEEDED = "BOUNDS_EXCEEDED"
CANARY_BROWSER_ERROR = "BROWSER_ERROR"
CANARY_CLEANUP_INCOMPLETE = "CLEANUP_INCOMPLETE"
CANARY_BUSY = "BUSY"

CANARY_REASON_PROFILE_BUSY = "PROFILE_BUSY"
CANARY_REASON_HTTP_ERROR = "HTTP_ERROR"
CANARY_REASON_PAGESET_AMBIGUOUS = "PAGESET_AMBIGUOUS"
CANARY_REASON_CLEANUP_INCOMPLETE = "CLEANUP_INCOMPLETE"
CANARY_REASON_HISTORY_UNVERIFIED = "HISTORY_UNVERIFIED"
CANARY_REASON_GROUP_NOT_FOUND = "GROUP_NOT_FOUND"
CANARY_REASON_GROUP_MISMATCH = "GROUP_IDENTITY_MISMATCH"
CANARY_REASON_CAPTURE_FAILED = "CAPTURE_INCOMPLETE"
CANARY_REASON_REPORT_ABSENT = "LESSON_REPORT_ABSENT"
CANARY_REASON_BOUND_EXCEEDED = "CANARY_BOUND_EXCEEDED"
CANARY_REASON_TIMEOUT = "CANARY_TIMEOUT"
CANARY_REASON_UNEXPECTED = "CANARY_BROWSER_ERROR"

_SAFE_STATUS_CODES = frozenset(
    {
        CANARY_READY,
        CANARY_INSUFFICIENT_EVIDENCE,
        CANARY_AUTH_NOT_READY,
        CANARY_GROUP_IDENTITY_UNVERIFIED,
        CANARY_MESSAGE_WINDOW_UNVERIFIED,
        CANARY_CAPTURE_INCOMPLETE,
        CANARY_BOUNDS_EXCEEDED,
        CANARY_BROWSER_ERROR,
        CANARY_CLEANUP_INCOMPLETE,
        CANARY_BUSY,
    }
)
_SAFE_SCAN_DECISIONS = frozenset(
    {
        "SUBMITTED_ON_TIME",
        "SUBMITTED_LATE_ACCEPTED",
        "SUBMITTED_LATE",
        "NOT_FOUND",
        "NOT_FOUND_VERIFIED",
        "INSUFFICIENT_EVIDENCE",
        "GROUP_IDENTITY_UNVERIFIED",
        "GROUP_IDENTITY_MISMATCH",
        "GROUP_NOT_FOUND",
        "ZALO_SYNC_INCOMPLETE",
        "TARGET_SESSION_AMBIGUOUS",
        "INVALID_BCNB_TARGET",
        "INVALID_CLASS_DATE",
    }
)
_SAFE_SCAN_STATUSES = frozenset(
    {
        SUBMITTED_STATUS,
        SUBMITTED_LATE_STATUS,
        NOT_FOUND_STATUS,
        INSUFFICIENT_STATUS,
        LESSON_14_STATUS,
    }
)
_SAFE_EVIDENCE_STATES = frozenset(
    {
        SUBMITTED_VERIFIED,
        SUBMITTED_LATE_VERIFIED,
        NOT_FOUND_VERIFIED,
        INSUFFICIENT_EVIDENCE,
        LESSON_14_EXEMPTION,
    }
)
_SAFE_CLEANUP_CODES = frozenset(
    {CLEANUP_OK, CLEANUP_ALREADY_CLOSED, CANARY_REASON_CLEANUP_INCOMPLETE}
)


@dataclass(frozen=True, slots=True)
class CanaryDescriptor:
    """The immutable Owner-approved canary target."""

    class_code: str
    group_name: str
    week: int
    year: int
    lesson: str
    class_date_iso: str
    message_window_start: str
    message_window_end: str
    phase: str = CANARY_PHASE
    schema_version: str = CANARY_SCHEMA_VERSION
    artifact_prefix: str = "zalo-armb19-canary"
    worker_id: str = "armb19-canary"
    person: str = "ARMB19_READ_ONLY_CANARY"
    zalo_deadline: str = "2026-08-27T23:59:59+07:00"

    def as_dict(self) -> dict[str, object]:
        return {
            "class_code": self.class_code,
            "group_name": self.group_name,
            "week": self.week,
            "year": self.year,
            "lesson": self.lesson,
            "class_date_iso": self.class_date_iso,
            "message_window_start": self.message_window_start,
            "message_window_end": self.message_window_end,
        }

    @property
    def class_date(self) -> date:
        return date.fromisoformat(self.class_date_iso)

    @property
    def window_start(self) -> date:
        return date.fromisoformat(self.message_window_start)

    @property
    def window_end(self) -> date:
        return date.fromisoformat(self.message_window_end)


CANARY_DESCRIPTOR = CanaryDescriptor(
    class_code="VT-ROB-ARMB19",
    group_name="MindX - Robotics-VT-ROB-ARMB19 CN (18H-20H)",
    week=34,
    year=2026,
    lesson="1",
    class_date_iso="2026-08-23",
    message_window_start="2026-08-17",
    message_window_end="2026-08-27",
)

GA38_DESCRIPTOR = CanaryDescriptor(
    class_code="VT-C4K-GA38",
    group_name="MindX- Lập trình Game VT-C4K-GA38 CN (18H-20H)",
    week=35,
    year=2026,
    lesson="3",
    class_date_iso="2026-08-30",
    message_window_start="2026-08-24",
    message_window_end="2026-09-05",
    phase=GA38_CANARY_PHASE,
    schema_version=GA38_CANARY_SCHEMA_VERSION,
    artifact_prefix=GA38_CANARY_ARTIFACT_PREFIX,
    worker_id=GA38_CANARY_WORKER_ID,
    person=GA38_CANARY_PERSON,
    zalo_deadline=GA38_CANARY_DEADLINE,
)

PTI10_DESCRIPTOR = CanaryDescriptor(
    class_code="VT-C4K-PTI10",
    group_name="MindX - Lập trình App -PTI10 CN (8H-10H)",
    week=37,
    year=2026,
    lesson="13",
    class_date_iso="2026-09-13",
    message_window_start="2026-09-13",
    message_window_end="2026-09-16",
    phase=PTI10_CANARY_PHASE,
    schema_version=PTI10_CANARY_SCHEMA_VERSION,
    artifact_prefix=PTI10_CANARY_ARTIFACT_PREFIX,
    worker_id=PTI10_CANARY_WORKER_ID,
    person=PTI10_CANARY_PERSON,
    zalo_deadline=PTI10_CANARY_DEADLINE,
)

_ARMB19_TARGET_KEY = "ARMB19"
_GA38_TARGET_KEY = "GA38"
_PTI10_TARGET_KEY = "PTI10"


def _descriptor_for_target_key(value: object) -> CanaryDescriptor | None:
    if value == _ARMB19_TARGET_KEY:
        return CANARY_DESCRIPTOR
    if value == _GA38_TARGET_KEY:
        return GA38_DESCRIPTOR
    if value == _PTI10_TARGET_KEY:
        return PTI10_DESCRIPTOR
    return None


@dataclass(frozen=True, slots=True)
class CanaryBounds:
    """Finite canary limits validated before any large message list exists."""

    max_runtime_seconds: float = CANARY_MAX_RUNTIME_SECONDS
    max_scrolls: int = CANARY_MAX_SCROLLS
    max_messages: int = CANARY_MAX_MESSAGES

    def __post_init__(self) -> None:
        if isinstance(self.max_runtime_seconds, bool):
            raise ValueError("canary runtime bound must be numeric")
        runtime = float(self.max_runtime_seconds)
        scrolls = int(self.max_scrolls)
        messages = int(self.max_messages)
        if runtime <= 0 or runtime > CANARY_MAX_RUNTIME_SECONDS:
            raise ValueError("canary runtime exceeds approved bound")
        if scrolls <= 0 or scrolls > CANARY_MAX_SCROLLS:
            raise ValueError("canary scroll bound exceeds approved limit")
        if messages <= 0 or messages > CANARY_MAX_MESSAGES:
            raise ValueError("canary message bound exceeds approved limit")
        if isinstance(self.max_scrolls, bool) or isinstance(self.max_messages, bool):
            raise ValueError("canary bounds must be integers")
        object.__setattr__(self, "max_runtime_seconds", runtime)
        object.__setattr__(self, "max_scrolls", scrolls)
        object.__setattr__(self, "max_messages", messages)


def canary_descriptor_dict() -> dict[str, object]:
    """Return a copy of the fixed reviewed descriptor."""

    return CANARY_DESCRIPTOR.as_dict()


def ga38_descriptor_dict() -> dict[str, object]:
    """Return a copy of the fixed reviewed GA38 descriptor."""

    return GA38_DESCRIPTOR.as_dict()


def pti10_descriptor_dict() -> dict[str, object]:
    """Return a copy of the fixed reviewed PTI10 descriptor."""

    return PTI10_DESCRIPTOR.as_dict()


def _bounded_int(value: object, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(maximum, value))


def _safe_reason(value: object, fallback: str = CANARY_REASON_UNEXPECTED) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    if not normalized or len(normalized) > 80:
        return fallback
    if not all(char.isupper() or char.isdigit() or char == "_" for char in normalized):
        return fallback
    return normalized


def _safe_label(
    value: object,
    *,
    allowed: frozenset[str],
    fallback: str,
) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    return fallback


def _paths_overlap(left: Path, right: Path) -> bool:
    """Return whether either resolved destination contains the other."""
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _validate_artifact_root(
    artifact_root: Path,
    *,
    profile_path: Path | None,
    profile_lock_path: Path,
) -> None:
    """Reject stale, repository-local, or browser-destination artifact roots."""
    repository_root = Path(__file__).resolve().parents[1]
    if _paths_overlap(artifact_root, repository_root):
        raise ValueError("artifact root must be outside the repository")
    protected = [profile_lock_path]
    if profile_path is not None:
        protected.append(profile_path)
    if any(_paths_overlap(artifact_root, path) for path in protected):
        raise ValueError("artifact root must be disjoint from browser destinations")
    if artifact_root.exists():
        if not artifact_root.is_dir() or artifact_root.is_symlink():
            raise ValueError("artifact root must be a fresh private directory")
        try:
            next(artifact_root.iterdir())
        except StopIteration:
            return
        raise ValueError("artifact root must be empty for a new canary")


def _cleanup_summary(cleanup: Any, *, lock_released: bool | None) -> dict[str, object]:
    if cleanup is None:
        complete = False
        code = "NOT_RUN"
        invariant_ok = False
        remaining_pages = 0
    else:
        raw_code = getattr(cleanup, "code", None)
        code_value = getattr(raw_code, "value", raw_code)
        code = (
            code_value
            if isinstance(code_value, str) and code_value in _SAFE_CLEANUP_CODES
            else CANARY_REASON_CLEANUP_INCOMPLETE
        )
        invariant_ok = bool(getattr(cleanup, "invariant_ok", False))
        incomplete = bool(getattr(cleanup, "cleanup_incomplete", False))
        remaining_pages = _bounded_int(
            getattr(cleanup, "remaining_pages", 0),
            maximum=100,
        )
        complete = (
            code in {CLEANUP_OK, CLEANUP_ALREADY_CLOSED}
            and invariant_ok
            and not incomplete
            and remaining_pages == 0
        )
    if lock_released is False:
        complete = False
    return {
        "code": code,
        "complete": complete,
        "invariant_ok": invariant_ok and lock_released is not False,
        "lock_released": lock_released,
        "remaining_pages": remaining_pages,
    }


class _CanaryJobProbe:
    """Small cancellation probe used by the production scanner without DB access."""

    async def ensure_not_cancelled(self, _job_id: int) -> None:
        return None

    async def ensure_sync_not_cancelled(self) -> None:
        return None


class ReadOnlyCanaryRunner:
    """Run exactly one bounded browser observation for a fixed descriptor."""

    def __init__(
        self,
        *,
        browser_gateway: Any,
        profile_lock_path: str | Path,
        artifact_root: str | Path,
        bounds: CanaryBounds | None = None,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.browser_gateway = browser_gateway
        self.profile_lock_path = Path(profile_lock_path).expanduser().resolve()
        self.artifact_root = Path(artifact_root).expanduser().resolve()
        raw_profile_path = getattr(browser_gateway, "profile_path", None)
        profile_path = (
            Path(raw_profile_path).expanduser().resolve()
            if isinstance(raw_profile_path, (str, Path)) and str(raw_profile_path)
            else None
        )
        _validate_artifact_root(
            self.artifact_root,
            profile_path=profile_path,
            profile_lock_path=self.profile_lock_path,
        )
        self.bounds = bounds or CanaryBounds()
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))
        self._target_key = _ARMB19_TARGET_KEY
        self.descriptor = CANARY_DESCRIPTOR
        self._cleanup_reuse_blocked = False
        self._last_cleanup_result: object | None = None
        self._lock = InterProcessFileLock(
            self.profile_lock_path,
            timeout_seconds=self.lock_timeout_seconds,
            reclaim_stale=False,
        )

    @classmethod
    def from_production(
        cls,
        *,
        profile_path: str | Path,
        profile_lock_path: str | Path,
        artifact_root: str | Path,
        bounds: CanaryBounds | None = None,
    ) -> "ReadOnlyCanaryRunner":
        """Compose the real sealed gateway; readiness cannot be injected."""

        gateway = ExternalGateways.production(profile_path=str(profile_path)).browser
        return cls(
            browser_gateway=gateway,
            profile_lock_path=profile_lock_path,
            artifact_root=artifact_root,
            bounds=bounds,
        )

    @classmethod
    def from_ga38(
        cls,
        *,
        profile_path: str | Path,
        profile_lock_path: str | Path,
        artifact_root: str | Path,
        bounds: CanaryBounds | None = None,
    ) -> "ReadOnlyCanaryRunner":
        """Compose the fixed GA38 target with the production gateway."""

        gateway = ExternalGateways.production(profile_path=str(profile_path)).browser
        runner = cls(
            browser_gateway=gateway,
            profile_lock_path=profile_lock_path,
            artifact_root=artifact_root,
            bounds=bounds,
        )
        runner._target_key = _GA38_TARGET_KEY
        runner.descriptor = GA38_DESCRIPTOR
        return runner

    @classmethod
    def from_pti10(
        cls,
        *,
        profile_path: str | Path,
        profile_lock_path: str | Path,
        artifact_root: str | Path,
        bounds: CanaryBounds | None = None,
    ) -> "ReadOnlyCanaryRunner":
        """Compose the fixed PTI10 target with the production gateway."""

        gateway = ExternalGateways.production(profile_path=str(profile_path)).browser
        runner = cls(
            browser_gateway=gateway,
            profile_lock_path=profile_lock_path,
            artifact_root=artifact_root,
            bounds=bounds,
        )
        runner._target_key = _PTI10_TARGET_KEY
        runner.descriptor = PTI10_DESCRIPTOR
        return runner

    def _validated_descriptor(self) -> CanaryDescriptor | None:
        expected = _descriptor_for_target_key(getattr(self, "_target_key", None))
        current = getattr(self, "descriptor", None)
        if expected is None or current != expected:
            return None
        return expected

    @classmethod
    def capabilities(cls) -> dict[str, object]:
        return {
            "write_allowed": False,
            "denied_capabilities": (
                "BCNB",
                "GOOGLE_FORM",
                "ZALO_SEND",
                "PRODUCTION_DB",
                "JOB_DISPATCH",
            ),
        }

    @staticmethod
    def sanitize_scan_result(result: Mapping[str, object] | None) -> dict[str, object]:
        """Keep aggregate evidence while dropping text, IDs, paths and fingerprints."""

        if not isinstance(result, Mapping):
            return {
                "status": INSUFFICIENT_STATUS,
                "evidence_state": INSUFFICIENT_EVIDENCE,
                "messages_checked": 0,
                "capture_count": 0,
                "validation_error_count": 1,
            }
        evidence = result.get("evidence")
        evidence_map = evidence if isinstance(evidence, Mapping) else {}
        raw_messages_checked = evidence_map.get(
            "messages_checked", result.get("messages_checked")
        )
        classifier = evidence_map.get("classifier_result")
        classifier_map = classifier if isinstance(classifier, Mapping) else {}
        images = evidence_map.get("images")
        image_map = images if isinstance(images, Mapping) else {}
        capture_count = sum(
            1
            for key in ("report", "latest", "oldest", "oldest_message")
            if image_map.get(key) is not None
        )
        validation_errors = evidence_map.get("validation_errors")
        error_count = len(validation_errors) if isinstance(validation_errors, list) else 0
        decision = classifier_map.get("decision")
        safe_decision = _safe_label(
            decision,
            allowed=_SAFE_SCAN_DECISIONS,
            fallback="UNKNOWN",
        )
        return {
            "status": _safe_label(
                result.get("status"),
                allowed=_SAFE_SCAN_STATUSES,
                fallback=INSUFFICIENT_STATUS,
            ),
            "evidence_state": _safe_label(
                result.get("evidence_state"),
                allowed=_SAFE_EVIDENCE_STATES,
                fallback=INSUFFICIENT_EVIDENCE,
            ),
            "messages_checked": _bounded_int(
                raw_messages_checked, maximum=CANARY_MAX_MESSAGES
            ),
            "oldest_message_date": (
                evidence_map.get("oldest_message_date")
                if isinstance(evidence_map.get("oldest_message_date"), str)
                else None
            ),
            "newest_message_date": (
                evidence_map.get("newest_message_date")
                if isinstance(evidence_map.get("newest_message_date"), str)
                else None
            ),
            "required_window_start": evidence_map.get("required_window_start"),
            "required_window_end": evidence_map.get("required_window_end"),
            "classifier_decision": safe_decision,
            "evaluated_messages": _bounded_int(
                classifier_map.get("evaluated_messages"), maximum=CANARY_MAX_MESSAGES
            ),
            "candidate_reports": _bounded_int(
                classifier_map.get("candidate_reports"), maximum=CANARY_MAX_MESSAGES
            ),
            "capture_count": capture_count,
            "validation_error_count": min(100, max(0, error_count)),
        }

    async def observe_readiness(self, page: Any) -> dict[str, object]:
        """Observe the sealed readiness contract on this exact page."""

        timeout = min(60.0, self.bounds.max_runtime_seconds)
        return await ZaloAuthReadinessAdapter(timeout_seconds=timeout)(page)

    async def run(self) -> dict[str, object]:
        started = monotonic()
        observed_at = _utc_observed_at()
        descriptor = self._validated_descriptor()
        safe_descriptor = descriptor or _descriptor_for_target_key(
            getattr(self, "_target_key", None)
        ) or CANARY_DESCRIPTOR
        artifact_name = f"{safe_descriptor.artifact_prefix}-{uuid.uuid4().hex}.json"
        artifact_path = self.artifact_root / artifact_name
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        lock_released: bool | None = None
        cleanup = None

        if descriptor is None:
            result = self._result(
                status=CANARY_BROWSER_ERROR,
                reason_code=CANARY_DESCRIPTOR_INVALID,
                observed_at=observed_at,
                started=started,
                cleanup=None,
                lock_released=None,
                artifact_path=artifact_path,
                descriptor=safe_descriptor,
            )
            self._write_artifact(artifact_path, result)
            return result

        if self._cleanup_reuse_blocked:
            result = self._result(
                status=CANARY_CLEANUP_INCOMPLETE,
                reason_code=CANARY_REASON_CLEANUP_INCOMPLETE,
                observed_at=observed_at,
                started=started,
                cleanup=self._last_cleanup_result,
                lock_released=False,
                artifact_path=artifact_path,
                descriptor=descriptor,
            )
            self._write_artifact(artifact_path, result)
            return result

        try:
            await self._lock.acquire(timeout_seconds=self.lock_timeout_seconds)
        except ProfileLockTimeout:
            result = self._result(
                status=CANARY_BUSY,
                reason_code=CANARY_REASON_PROFILE_BUSY,
                observed_at=observed_at,
                started=started,
                cleanup=None,
                lock_released=None,
                artifact_path=artifact_path,
                descriptor=descriptor,
            )
            self._write_artifact(artifact_path, result)
            return result

        try:
            try:
                outcome = await asyncio.wait_for(
                    self._observe_once(),
                    timeout=self.bounds.max_runtime_seconds,
                )
            except asyncio.TimeoutError:
                outcome = {
                    "status": CANARY_BOUNDS_EXCEEDED,
                    "reason_code": CANARY_REASON_TIMEOUT,
                    "readiness": None,
                    "scan": None,
                }
            except EvidenceBoundsExceeded:
                outcome = {
                    "status": CANARY_BOUNDS_EXCEEDED,
                    "reason_code": CANARY_REASON_BOUND_EXCEEDED,
                    "readiness": None,
                    "scan": None,
                }
            except asyncio.CancelledError:
                raise
            except Exception:
                outcome = {
                    "status": CANARY_BROWSER_ERROR,
                    "reason_code": CANARY_REASON_UNEXPECTED,
                    "readiness": None,
                    "scan": None,
                }
        finally:
            cleanup = getattr(self.browser_gateway, "last_cleanup_result", None)
            self._last_cleanup_result = cleanup
            cleanup_complete = _cleanup_is_complete(cleanup, True)
            if cleanup_complete:
                try:
                    await self._lock.release()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                lock_released = not self.profile_lock_path.exists()
            else:
                # Keep the ownership marker in place when owned browser
                # resources are uncertain.  A later call on this instance is
                # also blocked rather than treating the still-held lock as a
                # successful fresh acquire.
                self._cleanup_reuse_blocked = True
                lock_released = False
            if lock_released is False:
                self._cleanup_reuse_blocked = True

        if not _cleanup_is_complete(cleanup, lock_released):
            outcome = {
                **outcome,
                "status": CANARY_CLEANUP_INCOMPLETE,
                "reason_code": CANARY_REASON_CLEANUP_INCOMPLETE,
            }
        result = self._result(
            status=str(outcome.get("status") or CANARY_BROWSER_ERROR),
            reason_code=_safe_reason(outcome.get("reason_code")),
            observed_at=observed_at,
            started=started,
            readiness=outcome.get("readiness"),
            scan=outcome.get("scan"),
            cleanup=cleanup,
            lock_released=lock_released,
            artifact_path=artifact_path,
            descriptor=descriptor,
        )
        self._write_artifact(artifact_path, result)
        return result

    async def _observe_once(self) -> dict[str, object]:
        descriptor = self._validated_descriptor()
        if descriptor is None:
            return {
                "status": CANARY_BROWSER_ERROR,
                "reason_code": CANARY_DESCRIPTOR_INVALID,
                "readiness": None,
                "scan": None,
            }
        readiness: Mapping[str, object] | None = None
        async with self.browser_gateway.persistent_session(
            cleanup_timeout_seconds=CANARY_CLEANUP_TIMEOUT_SECONDS,
        ) as session:
            page = await self._select_owned_page(session)
            if page is None:
                return {
                    "status": CANARY_BROWSER_ERROR,
                    "reason_code": CANARY_REASON_PAGESET_AMBIGUOUS,
                    "readiness": None,
                    "scan": None,
                }
            try:
                response = await asyncio.wait_for(
                    page.goto(
                        "https://chat.zalo.me/",
                        timeout=int(CANARY_NAVIGATION_TIMEOUT_SECONDS * 1000),
                    ),
                    timeout=CANARY_NAVIGATION_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return {
                    "status": CANARY_BROWSER_ERROR,
                    "reason_code": CANARY_REASON_UNEXPECTED,
                    "readiness": None,
                    "scan": None,
                }
            response_status = getattr(response, "status", None)
            if isinstance(response_status, int) and response_status >= 400:
                return {
                    "status": CANARY_BROWSER_ERROR,
                    "reason_code": CANARY_REASON_HTTP_ERROR,
                    "readiness": None,
                    "scan": None,
                }
            readiness = await self.observe_readiness(page)
            if classify_landing_readiness(readiness) != READY_REASON:
                return {
                    "status": CANARY_AUTH_NOT_READY,
                    "reason_code": _safe_reason(
                        readiness.get("reason_code"), "AUTH_READINESS_SELECTOR_UNSUPPORTED"
                    ),
                    "readiness": readiness,
                    "scan": None,
                }

            sync_result = await ensure_zalo_history_synced(
                page,
                ensure_not_cancelled=_CanaryJobProbe().ensure_sync_not_cancelled,
                wait_timeout_seconds=min(180.0, self.bounds.max_runtime_seconds),
            )
            if not sync_result.get("verified"):
                return {
                    "status": CANARY_MESSAGE_WINDOW_UNVERIFIED,
                    "reason_code": CANARY_REASON_HISTORY_UNVERIFIED,
                    "readiness": readiness,
                    "scan": None,
                }

            target = {
                "class_code": descriptor.class_code,
                "group_name": descriptor.group_name,
                "person": descriptor.person,
                "week": descriptor.week,
                "year": descriptor.year,
                "lesson": descriptor.lesson,
                "class_date": descriptor.class_date.strftime("%d/%m/%Y"),
                "class_date_iso": descriptor.class_date_iso,
                "zalo_deadline": descriptor.zalo_deadline,
            }
            today = now_business()
            window_start = datetime.combine(
                descriptor.window_start,
                time.min,
                tzinfo=today.tzinfo,
            )
            window_end = datetime.combine(
                descriptor.window_end,
                time.max,
                tzinfo=today.tzinfo,
            )
            scan = await _scan_one_target(
                page,
                job_id=0,
                target=target,
                sync_result=sync_result,
                screenshots_dir=str(self.artifact_root / "evidence"),
                today=today,
                job_repo=_CanaryJobProbe(),
                worker_id=descriptor.worker_id,
                attempt=1,
                capture_max_scrolls=self.bounds.max_scrolls,
                capture_max_messages=self.bounds.max_messages,
                capture_window_start=window_start,
                capture_window_end=window_end,
            )
            safe_scan = self.sanitize_scan_result(scan)
            if not self._scan_target_matches_descriptor(scan, descriptor):
                return {
                    "status": CANARY_GROUP_IDENTITY_UNVERIFIED,
                    "reason_code": CANARY_TARGET_MISMATCH,
                    "readiness": readiness,
                    "scan": safe_scan,
                }
            status, reason = self._classify_scan(safe_scan)
            return {
                "status": status,
                "reason_code": reason,
                "readiness": readiness,
                "scan": safe_scan,
            }

    @staticmethod
    def _scan_target_matches_descriptor(
        scan: object,
        descriptor: CanaryDescriptor,
    ) -> bool:
        if not isinstance(scan, Mapping):
            return False
        target = scan.get("scan_target")
        if not isinstance(target, Mapping):
            return False
        expected = {
            "class_code": descriptor.class_code,
            "group_name": descriptor.group_name,
            "person": descriptor.person,
            "week": descriptor.week,
            "year": descriptor.year,
            "lesson": descriptor.lesson,
            "class_date": descriptor.class_date.strftime("%d/%m/%Y"),
            "class_date_iso": descriptor.class_date_iso,
            "zalo_deadline": descriptor.zalo_deadline,
        }
        return all(target.get(key) == value for key, value in expected.items())

    def _classify_scan(self, scan: Mapping[str, object]) -> tuple[str, str]:
        descriptor = self._validated_descriptor()
        if descriptor is None:
            return CANARY_GROUP_IDENTITY_UNVERIFIED, CANARY_TARGET_MISMATCH
        return _classify_scan_for_descriptor(scan, descriptor)

    async def _select_owned_page(self, session: Any) -> Any | None:
        context = getattr(session, "context", None)
        pages = getattr(context, "pages", None)
        if pages is None:
            return await session.new_page()
        try:
            count = len(pages)
        except (TypeError, ValueError):
            return None
        if count > 1:
            return None
        if count == 1:
            return pages[0]
        return await session.new_page()

    def _result(
        self,
        *,
        status: str,
        reason_code: str,
        observed_at: str,
        started: float,
        readiness: object = None,
        scan: object = None,
        cleanup: object = None,
        lock_released: bool | None,
        artifact_path: Path,
        descriptor: CanaryDescriptor | None = None,
    ) -> dict[str, object]:
        safe_status = status if status in _SAFE_STATUS_CODES else CANARY_BROWSER_ERROR
        safe_reason_code = _safe_reason(reason_code)
        output_descriptor = descriptor or self._validated_descriptor() or CANARY_DESCRIPTOR
        readiness_map = readiness if isinstance(readiness, Mapping) else None
        readiness_summary = None
        if readiness_map is not None:
            readiness_summary = {
                "reason_code": _safe_reason(readiness_map.get("reason_code")),
                "selector_contract_version": (
                    AUTH_READINESS_SELECTOR_CONTRACT_VERSION
                    if readiness_map.get("selector_contract_version")
                    == AUTH_READINESS_SELECTOR_CONTRACT_VERSION
                    else "UNKNOWN"
                ),
                "expected_host_reached": readiness_map.get("expected_host_reached") is True,
                "body_nav_count": _bounded_int(
                    readiness_map.get("body_nav_count"), maximum=2
                ),
                "search_control_count": _bounded_int(
                    readiness_map.get("search_control_count"), maximum=2
                ),
                "body_nav_visible": readiness_map.get("body_nav_visible") is True,
                "search_control_visible": readiness_map.get("search_control_visible") is True,
            }
        scan_map = scan if isinstance(scan, Mapping) else None
        return {
            "phase": output_descriptor.phase,
            "schema_version": output_descriptor.schema_version,
            "status": safe_status,
            "reason_code": safe_reason_code,
            "observed_at": observed_at,
            "elapsed_ms": min(
                int(CANARY_MAX_RUNTIME_SECONDS * 1000),
                max(0, int((monotonic() - started) * 1000)),
            ),
            "descriptor": output_descriptor.as_dict(),
            "readiness": readiness_summary,
            "scan": dict(scan_map) if scan_map is not None else None,
            "cleanup": _cleanup_summary(cleanup, lock_released=lock_released),
            "artifact_name": artifact_path.name,
        }

    @staticmethod
    def _write_artifact(path: Path, result: Mapping[str, object]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            # The browser outcome remains available to the caller.  Do not
            # export the filesystem exception or retry an ambiguous run.
            return


def _cleanup_is_complete(cleanup: Any, lock_released: bool | None) -> bool:
    summary = _cleanup_summary(cleanup, lock_released=lock_released)
    return bool(summary["complete"])


def _classify_scan_for_descriptor(
    scan: Mapping[str, object],
    descriptor: CanaryDescriptor,
) -> tuple[str, str]:
    decision = _safe_label(
        scan.get("classifier_decision"),
        allowed=_SAFE_SCAN_DECISIONS,
        fallback="UNKNOWN",
    )
    messages_checked = _bounded_int(
        scan.get("messages_checked"), maximum=CANARY_MAX_MESSAGES
    )
    capture_count = _bounded_int(scan.get("capture_count"), maximum=4)
    errors = _bounded_int(scan.get("validation_error_count"), maximum=100)
    required_start = scan.get("required_window_start")
    required_end = scan.get("required_window_end")
    if (
        required_start != descriptor.message_window_start
        or required_end != descriptor.message_window_end
    ):
        return CANARY_MESSAGE_WINDOW_UNVERIFIED, CANARY_REASON_HISTORY_UNVERIFIED
    if decision in {"GROUP_IDENTITY_UNVERIFIED", "GROUP_IDENTITY_MISMATCH", "GROUP_NOT_FOUND"}:
        return CANARY_GROUP_IDENTITY_UNVERIFIED, str(decision)
    if messages_checked <= 0:
        return CANARY_INSUFFICIENT_EVIDENCE, "NO_MESSAGES_MATERIALIZED"
    if errors or capture_count <= 0:
        return CANARY_CAPTURE_INCOMPLETE, CANARY_REASON_CAPTURE_FAILED
    if decision == "NOT_FOUND_VERIFIED" or (
        decision == "NOT_FOUND" and scan.get("evidence_state") == NOT_FOUND_VERIFIED
    ):
        return CANARY_INSUFFICIENT_EVIDENCE, CANARY_REASON_REPORT_ABSENT
    if decision in {"SUBMITTED_ON_TIME", "SUBMITTED_LATE_ACCEPTED", "SUBMITTED_LATE", "NOT_FOUND_VERIFIED"}:
        return CANARY_READY, str(decision)
    return CANARY_INSUFFICIENT_EVIDENCE, "CLASSIFIER_INSUFFICIENT_EVIDENCE"


def _classify_scan(scan: Mapping[str, object]) -> tuple[str, str]:
    """Classify the legacy ARMB19 scan envelope for compatibility callers."""

    return _classify_scan_for_descriptor(scan, CANARY_DESCRIPTOR)


def _utc_observed_at() -> str:
    return utc_iso()


__all__ = [
    "CANARY_BOUNDS_EXCEEDED",
    "CANARY_BUSY",
    "CANARY_CLEANUP_INCOMPLETE",
    "CANARY_DESCRIPTOR",
    "CANARY_GROUP_IDENTITY_UNVERIFIED",
    "CANARY_INSUFFICIENT_EVIDENCE",
    "CANARY_MAX_MESSAGES",
    "CANARY_MAX_RUNTIME_SECONDS",
    "CANARY_MAX_SCROLLS",
    "CANARY_MESSAGE_WINDOW_UNVERIFIED",
    "CANARY_PHASE",
    "CANARY_READY",
    "GA38_CANARY_PHASE",
    "GA38_CANARY_SCHEMA_VERSION",
    "GA38_DESCRIPTOR",
    "PTI10_CANARY_PHASE",
    "PTI10_CANARY_SCHEMA_VERSION",
    "PTI10_DESCRIPTOR",
    "CanaryBounds",
    "CanaryDescriptor",
    "ReadOnlyCanaryRunner",
    "canary_descriptor_dict",
    "ga38_descriptor_dict",
    "pti10_descriptor_dict",
]
