import asyncio
import unittest

import services.browser_job_guard as guard


class FakeJobRepository:
    def __init__(self):
        self.updates = []

    async def update_job(self, job_id, **values):
        self.updates.append((job_id, values))


class BrowserJobGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        guard._browser_job_lock = asyncio.Lock()

    async def test_jobs_do_not_overlap(self):
        repo = FakeJobRepository()
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        second_entered = asyncio.Event()

        async def first():
            async with guard.exclusive_browser_job(1, repo):
                first_entered.set()
                await release_first.wait()

        async def second():
            await first_entered.wait()
            async with guard.exclusive_browser_job(2, repo):
                second_entered.set()

        first_task = asyncio.create_task(first())
        second_task = asyncio.create_task(second())
        await asyncio.wait_for(first_entered.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertFalse(second_entered.is_set())
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)
        self.assertTrue(second_entered.is_set())
        self.assertTrue(any(job_id == 2 for job_id, _ in repo.updates))

    async def test_lock_releases_after_exception(self):
        repo = FakeJobRepository()
        with self.assertRaises(RuntimeError):
            async with guard.exclusive_browser_job(1, repo):
                raise RuntimeError("boom")
        async with guard.exclusive_browser_job(2, repo):
            self.assertTrue(guard.browser_job_is_busy())
        self.assertFalse(guard.browser_job_is_busy())


if __name__ == "__main__":
    unittest.main()
