"""Canonical Google Form intent fingerprints.

The raw client idempotency key is deliberately never persisted in the job
payload or audit trail.  The server stores only a SHA-256 digest and binds it
to the normalized request, actor, target cohort and execution mode.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


IDEMPOTENCY_VERSION = 1
GOOGLEFORM_OPERATION = "GOOGLEFORM_SUBMIT"


def _text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def normalize_students(students: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Normalize the ordered student cohort used by a submit intent."""

    return [
        {
            "name": _text(student.get("name")),
            "class_code": _text(student.get("class_code")).upper(),
        }
        for student in students
    ]


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def payload_hash(students: Iterable[Mapping[str, Any]], execution_mode: str) -> str:
    normalized = normalize_students(students)
    return _digest(
        {
            "version": IDEMPOTENCY_VERSION,
            "operation": GOOGLEFORM_OPERATION,
            "students": normalized,
            "execution_mode": _text(execution_mode).lower(),
        }
    )


def target_hash(students: Iterable[Mapping[str, Any]]) -> str:
    return _digest(
        {
            "version": IDEMPOTENCY_VERSION,
            "operation": GOOGLEFORM_OPERATION,
            "students": normalize_students(students),
        }
    )


def actor_key(username: str, role: str) -> str:
    return _digest(
        {
            "version": IDEMPOTENCY_VERSION,
            "username": _text(username),
            "role": _text(role),
        }
    )


def key_digest(raw_key: str) -> str:
    """Return the persisted, redacted form of a client key."""

    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()


def persisted_key_digest(value: str) -> str:
    """Keep an already-redacted digest stable across the worker boundary."""

    candidate = value.strip().lower()
    if len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate):
        return candidate
    return key_digest(value)


def dry_run_key(students: Iterable[Mapping[str, Any]], execution_mode: str) -> str:
    """Deterministic fallback for local dry-run clients without a key."""

    return "dry-run-" + payload_hash(students, execution_mode)[:48]
