"""Server-side sessions and roles for the local/web deployment split."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
import time
from typing import Any

from core.time_utils import now_utc

ROLE_RANK = {"Viewer": 10, "Operator": 20, "Admin": 30}
PBKDF2_ITERATIONS = 310_000


@dataclass(frozen=True, slots=True)
class AuthUser:
    username: str
    role: str
    local: bool = False


@dataclass(slots=True)
class SessionRecord:
    user: AuthUser
    csrf_token: str
    expires_at: datetime


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("Password khong duoc de trong.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (AttributeError, TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", str(password).encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


class AuthManager:
    def __init__(self, settings: Any):
        self.mode = str(getattr(settings, "auth_mode", "local"))
        self.ttl_seconds = int(getattr(settings, "auth_session_ttl_seconds", 28800))
        self.cookie_name = str(getattr(settings, "auth_cookie_name", "zalo_session"))
        self.secure_cookie = bool(getattr(settings, "auth_secure_cookie", False))
        self._users = {
            username: (role, password_hash)
            for username, role, password_hash in getattr(settings, "auth_users", ())
        }
        self._sessions: dict[str, SessionRecord] = {}
        self._failed_logins: dict[str, list[float]] = {}
        self._login_lockout_seconds = 60
        self._login_attempt_window_seconds = 60
        self._max_login_attempts = 5
        self.local_user = AuthUser("local-admin", "Admin", local=True)

    @property
    def enabled(self) -> bool:
        return self.mode == "web"

    @property
    def configured(self) -> bool:
        return bool(self._users)

    def _cleanup(self) -> None:
        now = now_utc()
        for session_id, session in list(self._sessions.items()):
            if session.expires_at <= now:
                self._sessions.pop(session_id, None)

    def authenticate_request(self, request) -> tuple[AuthUser | None, SessionRecord | None]:
        if not self.enabled:
            return self.local_user, None
        self._cleanup()
        session_id = request.cookies.get(self.cookie_name)
        session = self._sessions.get(session_id or "")
        if session is None or session.expires_at <= now_utc():
            return None, None
        return session.user, session

    def login(
        self,
        username: str,
        password: str,
        *,
        client_key: str = "default",
    ) -> tuple[str, SessionRecord] | None:
        if not self.enabled or not self.configured:
            return None
        now = time.monotonic()
        recent_failures = [
            stamp
            for stamp in self._failed_logins.get(client_key, [])
            if now - stamp < self._login_attempt_window_seconds
        ]
        self._failed_logins[client_key] = recent_failures
        if len(recent_failures) >= self._max_login_attempts:
            return None
        username = str(username or "").strip()
        role_hash = self._users.get(username)
        password_hash = role_hash[1] if role_hash else (
            "pbkdf2_sha256$310000$00000000000000000000000000000000$"
            "0000000000000000000000000000000000000000000000000000000000000000"
        )
        valid = verify_password(password, password_hash)
        if not role_hash or not valid or role_hash[0] not in ROLE_RANK:
            recent_failures.append(now)
            return None
        self._failed_logins.pop(client_key, None)
        user = AuthUser(username, role_hash[0])
        session_id = secrets.token_urlsafe(32)
        record = SessionRecord(
            user=user,
            csrf_token=secrets.token_urlsafe(32),
            expires_at=now_utc() + timedelta(seconds=self.ttl_seconds),
        )
        self._sessions[session_id] = record
        return session_id, record

    def logout(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def csrf_matches(self, request, session: SessionRecord | None) -> bool:
        if not self.enabled:
            return True
        supplied = request.headers.get("x-csrf-token", "")
        return bool(
            session
            and supplied
            and hmac.compare_digest(supplied, session.csrf_token)
        )


def role_allows(user: AuthUser | None, required_role: str) -> bool:
    return bool(
        user
        and required_role in ROLE_RANK
        and ROLE_RANK.get(user.role, 0) >= ROLE_RANK[required_role]
    )
