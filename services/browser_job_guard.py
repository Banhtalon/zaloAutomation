import asyncio
from contextlib import asynccontextmanager


_browser_job_lock = asyncio.Lock()


@asynccontextmanager
async def exclusive_browser_job(job_id, job_repo, waiting_message=None, *, lock=None):
    """Serialize jobs that share the persistent Chrome/Zalo profile."""
    active_lock = lock or _browser_job_lock
    if active_lock.locked():
        await job_repo.update_job(
            job_id,
            message=waiting_message or "Đang chờ tác vụ trình duyệt trước đó hoàn tất...",
        )

    async def check_cancelled():
        checker = getattr(job_repo, "ensure_not_cancelled", None)
        if checker:
            await checker(job_id)

    while True:
        await check_cancelled()
        try:
            await asyncio.wait_for(active_lock.acquire(), timeout=1)
            break
        except asyncio.TimeoutError:
            continue
    try:
        await check_cancelled()
        yield
    finally:
        active_lock.release()


def browser_job_is_busy(lock=None):
    return (lock or _browser_job_lock).locked()
