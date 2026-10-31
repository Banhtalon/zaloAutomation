from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import re

from services.report_status import (
    identity_tokens as _identity_tokens,
    normalize_lesson_number as normalize_lesson_number,
)


class LifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED_OR_TRANSITIONING = "PAUSED_OR_TRANSITIONING"
    ENDED_CONFIRMED = "ENDED_CONFIRMED"
    ARCHIVED = "ARCHIVED"


# Automatic progressions confirmed by Owner (D3, 2026-10-30):
# B -> A -> I within the same track only. Cross-track transitions (e.g. PREI -> ARMB)
# are never automatic and remain manual review.
PROGRESSION_LEVELS: dict[str, tuple[str, ...]] = {
    "ROB4": ("B", "A", "I"),
    "SEMI": ("B", "A", "I"),
    "PRE": ("B", "A", "I"),
    "ARM": ("B", "A", "I"),
    "PT": ("B", "A", "I"),
    "JS": ("B", "A", "I"),
    "CS": ("B", "A", "I"),
    "G": ("B", "A", "I"),
    "S": ("B", "A", "I"),
}

_TOKEN_PATTERN = re.compile(r"[A-Z0-9]+")
_PROGRESSION_PATTERN = re.compile(
    r"^(?P<track>ROB4|SEMI|PRE|ARM|PT|JS|CS|G|S)(?P<level>B|A|I)(?P<number>\d{2,3})$"
)
_LEGACY_JS_CS_KEY_PATTERN = re.compile(
    r"^(?P<center>.+)-(?P<prefix>J|C)\|S\|(?P<number>\d{2,3})$"
)
_CANONICAL_JS_CS_KEY_PATTERN = re.compile(
    r"^(?P<center>.+)\|(?P<track>JS|CS)\|(?P<number>\d{2,3})$"
)


@dataclass(frozen=True, slots=True)
class ClassIdentity:
    class_code: str
    center_code: str
    track: str
    level: str
    cohort_number: str

    @property
    def cohort_key(self) -> str:
        return f"{self.center_code}|{self.track}|{self.cohort_number}"

    @property
    def legacy_cohort_key(self) -> str | None:
        if self.track in {"JS", "CS"}:
            return f"{self.center_code}-{self.track[0]}|S|{self.cohort_number}"
        return None

    @property
    def progression_position(self) -> int:
        return PROGRESSION_LEVELS[self.track].index(self.level)


def _find_progression(
    value: str,
) -> tuple[tuple[str, ...], int, re.Match[str]] | None:
    tokens = _identity_tokens(value)
    for index in range(len(tokens) - 1, -1, -1):
        match = _PROGRESSION_PATTERN.fullmatch(tokens[index])
        if match:
            return tokens, index, match
    return None


def extract_progression_signature(value: str) -> tuple[str, str, str] | None:
    """Return track, level and cohort number without guessing a center."""
    found = _find_progression(value)
    if not found:
        return None
    _tokens, _index, match = found
    return match.group("track"), match.group("level"), match.group("number")


def parse_bcnb_class_code(class_code: str) -> ClassIdentity | None:
    """Parse a BCNB code into a stable cohort identity.

    The center/program prefix is retained so similarly numbered classes from
    different centers can never be merged merely because their short aliases
    are the same. Suffix tokens after the progression token (such as ``HB``)
    are ignored when resolving ``center_code`` and ``cohort_key``.
    """
    found = _find_progression(class_code)
    if not found:
        return None
    tokens, index, match = found
    center_parts = [*tokens[:index]]
    if not center_parts:
        return None
    center_code = "-".join(center_parts)
    normalized_code = "-".join(tokens)
    return ClassIdentity(
        class_code=normalized_code,
        center_code=center_code,
        track=match.group("track"),
        level=match.group("level"),
        cohort_number=match.group("number"),
    )


def cohort_key_pair_from_key(cohort_key: str | None) -> tuple[str, str | None]:
    """Return ``(canonical_key, legacy_key)`` candidate pair for a cohort_key string."""
    raw = str(cohort_key or "").strip()
    if not raw:
        return "", None
    legacy_match = _LEGACY_JS_CS_KEY_PATTERN.fullmatch(raw)
    if legacy_match:
        center = legacy_match.group("center")
        track = f"{legacy_match.group('prefix')}S"
        number = legacy_match.group("number")
        return f"{center}|{track}|{number}", raw
    canonical_match = _CANONICAL_JS_CS_KEY_PATTERN.fullmatch(raw)
    if canonical_match:
        center = canonical_match.group("center")
        track = canonical_match.group("track")
        number = canonical_match.group("number")
        return raw, f"{center}-{track[0]}|S|{number}"
    return raw, None


def verify_cohort_key_for_class_code(
    cohort_key: str | None,
    class_code: str | None,
) -> str | None:
    """Return the canonical cohort_key if ``class_code`` proves ownership of ``cohort_key``."""
    raw_key = str(cohort_key or "").strip()
    identity = parse_bcnb_class_code(str(class_code or ""))
    if identity is None:
        return None
    if not raw_key or raw_key in {identity.cohort_key, identity.legacy_cohort_key}:
        return identity.cohort_key
    return None


def status_after_bcnb_snapshot(
    current_status: LifecycleStatus | str,
    *,
    observed_on_bcnb: bool,
    days_absent: int | None = None,
) -> LifecycleStatus:
    """BCNB presence is positive evidence; absence is never proof of ending."""
    del days_absent  # Deliberately ignored: transition breaks have variable length.
    if observed_on_bcnb:
        return LifecycleStatus.ACTIVE
    if LifecycleStatus(current_status) == LifecycleStatus.ACTIVE:
        return LifecycleStatus.PAUSED_OR_TRANSITIONING
    return LifecycleStatus(current_status)


def eligible_for_archive(
    status: LifecycleStatus | str,
    *,
    ended_at: datetime | None,
    now: datetime,
    retention_days: int = 7,
) -> bool:
    if LifecycleStatus(status) != LifecycleStatus.ENDED_CONFIRMED or ended_at is None:
        return False
    return ended_at <= now - timedelta(days=retention_days)
