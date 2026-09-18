"""Bounded retry and circuit-breaker primitives for external gateways."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic


class GatewayCircuitOpen(RuntimeError):
    """Raised while an external gateway is in its cooldown window."""


class GatewayCircuitBreaker:
    def __init__(
        self,
        *,
        max_retries: int = 2,
        backoff_seconds: float = 0.25,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.1, float(cooldown_seconds))
        self.failure_count = 0
        self.opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None and (
            monotonic() - self.opened_at < self.cooldown_seconds
        )

    async def call(self, operation: Callable[[], Awaitable[object]]) -> object:
        if self.is_open:
            raise GatewayCircuitOpen("External gateway đang tạm ngưng để tránh quá tải.")
        if self.opened_at is not None:
            self.opened_at = None
            self.failure_count = 0

        for attempt in range(self.max_retries + 1):
            try:
                result = await operation()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failure_count += 1
                if self.failure_count >= self.failure_threshold:
                    self.opened_at = monotonic()
                if attempt >= self.max_retries:
                    raise
                if self.backoff_seconds:
                    await asyncio.sleep(self.backoff_seconds * (2**attempt))
            else:
                self.failure_count = 0
                self.opened_at = None
                return result
        raise RuntimeError("External gateway retry exhausted.")

