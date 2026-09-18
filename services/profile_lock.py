"""Cross-process lock for the persistent Zalo/Chrome profile.

The async lock in :mod:`services.runtime` only protects coroutines that share
one Python process.  Playwright's persistent context also writes browser state
to disk, so a second process must be excluded explicitly.  This small lock
uses an atomic ``O_EXCL`` create and a bounded stale-owner recovery window; it
does not depend on third-party packages and works on Windows and POSIX.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path


class ProfileLockTimeout(TimeoutError):
    """Raised when another process owns the persistent browser profile."""


def _is_pid_alive(pid: int | None) -> bool:
    if not pid or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return False
        handle = windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class InterProcessFileLock:
    """An async, owner-aware lock backed by an atomic lock file."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        owner_id: str | None = None,
        timeout_seconds: float = 30.0,
        stale_after_seconds: float = 10 * 60,
        poll_seconds: float = 0.25,
        reclaim_stale: bool = True,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.owner_id = owner_id or uuid.uuid4().hex
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.stale_after_seconds = max(1.0, float(stale_after_seconds))
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.reclaim_stale = bool(reclaim_stale)
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def _try_create(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "owner_id": self.owner_id,
            "pid": os.getpid(),
            "acquired_at": time.time(),
        }
        try:
            fd = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            return False
        try:
            os.write(fd, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _reclaim_if_stale(self) -> bool:
        try:
            stat = self.path.stat()
            age = max(0.0, time.time() - stat.st_mtime)
        except FileNotFoundError:
            return False

        is_dead_owner = False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = payload.get("pid")
            if pid and isinstance(pid, int) and not _is_pid_alive(pid):
                is_dead_owner = True
        except Exception:
            pass

        if not is_dead_owner and age <= self.stale_after_seconds:
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return True

    async def acquire(self, *, timeout_seconds: float | None = None) -> None:
        if self._held:
            return
        timeout = self.timeout_seconds if timeout_seconds is None else max(0.1, float(timeout_seconds))
        deadline = time.monotonic() + timeout
        while True:
            if await asyncio.to_thread(self._try_create):
                self._held = True
                return
            if self.reclaim_stale:
                await asyncio.to_thread(self._reclaim_if_stale)
            if time.monotonic() >= deadline:
                raise ProfileLockTimeout(
                    "Persistent browser profile đang được tiến trình khác sử dụng."
                )
            await asyncio.sleep(self.poll_seconds)

    def _owned_by_self(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return False
        return payload.get("owner_id") == self.owner_id

    async def release(self) -> None:
        if not self._held:
            return
        try:
            if await asyncio.to_thread(self._owned_by_self):
                try:
                    await asyncio.to_thread(self.path.unlink)
                except FileNotFoundError:
                    pass
        finally:
            self._held = False

    async def __aenter__(self) -> "InterProcessFileLock":
        await self.acquire()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.release()
