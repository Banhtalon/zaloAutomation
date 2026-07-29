from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import re
import unicodedata


class LifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED_OR_TRANSITIONING = "PAUSED_OR_TRANSITIONING"
    ENDED_CONFIRMED = "ENDED_CONFIRMED"
    ARCHIVED = "ARCHIVED"


# Only progressions confirmed by the current business rules are automatic.
# Robotics codes stay outside this map until their ordering is verified.
PROGRESSION_LEVELS: dict[str, tuple[str, ...]] = {
    "PT": ("B", "A", "I"),
    "G": ("B", "A", "I"),
    "S": ("B", "A", "I"),
}

_TOKEN_PATTERN = re.compile(r"[A-Z0-9]+")
_PROGRESSION_PATTERN = re.compile(
    r"(?P<track>PT|G|S)(?P<level>B|A|I)(?P<number>\d{2,3})$"
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
    def progression_position(self) -> int:
        return PROGRESSION_LEVELS[self.track].index(self.level)


def _identity_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value or "").upper()
    return tuple(_TOKEN_PATTERN.findall(normalized))


def _find_progression(
    value: str,
) -> tuple[tuple[str, ...], int, re.Match[str]] | None:
    tokens = _identity_tokens(value)
    for index in range(len(tokens) - 1, -1, -1):
        match = _PROGRESSION_PATTERN.search(tokens[index])
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
    are the same.
    """
    found = _find_progression(class_code)
    if not found:
        return None
    tokens, index, match = found
    leading_fragment = tokens[index][: match.start()]
    center_parts = [*tokens[:index]]
    if leading_fragment:
        center_parts.append(leading_fragment)
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


def normalize_lesson_number(value: str | int | None) -> int | None:
    if isinstance(value, int):
        return value if value >= 0 else None
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


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
    retention_days: int = 14,
) -> bool:
    if LifecycleStatus(status) != LifecycleStatus.ENDED_CONFIRMED or ended_at is None:
        return False
    return ended_at <= now - timedelta(days=retention_days)
