from dataclasses import dataclass, field
import asyncio

from services.external_gateways import ExternalGateways


@dataclass(slots=True)
class WorkerRuntime:
    """Mutable locks owned by one application container/lifespan."""

    external: ExternalGateways = field(default_factory=ExternalGateways.production)
    browser_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ocr_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    form_submit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def reset_locks(self) -> None:
        if any(
            lock.locked()
            for lock in (self.browser_lock, self.ocr_lock, self.form_submit_lock)
        ):
            raise RuntimeError("Không thể reset runtime khi lock còn được sử dụng.")
        self.browser_lock = asyncio.Lock()
        self.ocr_lock = asyncio.Lock()
        self.form_submit_lock = asyncio.Lock()
