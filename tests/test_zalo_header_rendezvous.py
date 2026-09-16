"""Integration regression coverage for the Zalo target-header rendezvous.

Verifies that the target-header wait polls through transient unreadable headers
and stale headers until the exact claimed group appears before evidence
collection, retries a typed header failure once on a fresh page, and preserves
fail-closed cancellation, identity invariants, and error sanitization.
"""

from contextlib import asynccontextmanager
import asyncio
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.time_utils import BUSINESS_TIMEZONE
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from repositories.job_repository import JobCancelledError
import services.zalo_parallel_service as parallel_service
from services.zalo_parallel_service import (
    TARGET_SEARCH_RESULT_ERROR,
    TARGET_SEARCH_RESULT_TIMEOUT,
    _wait_for_target_header,
)
from services.zalo_safety import open_chat_matches
from tests.test_zalo_parallel_service import (
    FakeParallelBrowser,
    FakeParallelPage,
    FakeParallelSession,
    FakeTextLocator,
    InMemoryBatchRepository,
    _make_search_target,
    _verified_scan_result,
)


class RendezvousHeaderLocator(FakeTextLocator):
    """Header locator double supporting sequence replay, locked text, and error injection."""

    def __init__(
        self,
        text: str = "",
        error: BaseException | None = None,
        *,
        hang: bool = False,
        values: list[str | BaseException] | None = None,
        locked: bool = False,
        on_read=None,
    ) -> None:
        self._text = text
        self._locked_text = text
        self.locked = locked
        self.on_read = on_read
        self.read_history: list[str | BaseException] = []
        super().__init__(text, error, hang=hang, values=values)

    @property
    def text(self) -> str:
        return self._locked_text if self.locked else self._text

    @text.setter
    def text(self, val: str) -> None:
        if not self.locked:
            self._text = val

    async def inner_text(self, **_kwargs) -> str:
        self.inner_text_calls += 1
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            self.read_history.append(self.error)
            if self.on_read is not None:
                res = self.on_read(self.error)
                if asyncio.iscoroutine(res):
                    await res
            raise self.error
        if self.values:
            val = self.values.pop(0)
            self.read_history.append(val)
            if self.on_read is not None:
                res = self.on_read(val)
                if asyncio.iscoroutine(res):
                    await res
            if isinstance(val, BaseException):
                raise val
            return str(val)
        val = self.text
        self.read_history.append(val)
        if self.on_read is not None:
            res = self.on_read(val)
            if asyncio.iscoroutine(res):
                await res
        return str(val)


class RendezvousFakePage(FakeParallelPage):
    """Page double equipped with RendezvousHeaderLocator."""

    def __init__(
        self,
        index: int,
        *,
        selector_error=None,
        selector_barrier=None,
        search_clear_errors=None,
        search_fill_errors=None,
        search_clear_hang: bool = False,
        search_fill_hang: bool = False,
        body_text: str = "",
        body_error=None,
        body_hang: bool = False,
        header_text: str = "MindX - VT-JSB01",
        header_values: list[str | BaseException] | None = None,
        header_error: BaseException | None = None,
        header_hang: bool = False,
        header_locked: bool = False,
        on_header_read=None,
    ) -> None:
        super().__init__(
            index,
            selector_error=selector_error,
            selector_barrier=selector_barrier,
            search_clear_errors=search_clear_errors,
            search_fill_errors=search_fill_errors,
            search_clear_hang=search_clear_hang,
            search_fill_hang=search_fill_hang,
            body_text=body_text,
            body_error=body_error,
            body_hang=body_hang,
            header_text=header_text,
            header_values=header_values,
        )
        self.header_locator = RendezvousHeaderLocator(
            text=header_text,
            error=header_error,
            hang=header_hang,
            values=header_values,
            locked=header_locked,
            on_read=on_header_read,
        )


class RendezvousFakeSession(FakeParallelSession):
    """Session double producing RendezvousFakePage instances."""

    async def new_page(self) -> RendezvousFakePage:
        if self.new_page_hang:
            await asyncio.Event().wait()
        if self.new_page_errors:
            error = self.new_page_errors.pop(0)
            if error is not None:
                raise error
        config = (
            self.page_configs[len(self.pages)]
            if len(self.pages) < len(self.page_configs)
            else {}
        )
        page = RendezvousFakePage(len(self.pages) + 1, **dict(config or {}))
        self.pages.append(page)
        self.lifecycle_events.append(("new_page", page.index, id(page)))
        return page


class RendezvousFakeBrowser(FakeParallelBrowser):
    """Browser double producing RendezvousFakeSession instances."""

    @asynccontextmanager
    async def persistent_session(self):
        session = RendezvousFakeSession(
            self.page_configs,
            close_hang=self.close_hang,
            new_page_hang=self.new_page_hang,
            new_page_errors=self.new_page_errors,
            landing_facts=self.landing_facts,
            landing_facts_by_page=self.landing_facts_by_page,
        )
        self.sessions.append(session)
        yield session


class ZaloTargetHeaderRendezvousIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Integration test suite proving rendezvous, retry, and fail-closed properties."""

    async def _run_integration_batch(
        self,
        *,
        job_id: int,
        targets: list[dict[str, object]],
        page_configs: list[dict[str, object]],
        repository: InMemoryBatchRepository | None = None,
        browser: RendezvousFakeBrowser | None = None,
        claim_plan: dict[str, list[int]] | None = None,
        result_timeout_seconds: float = 0.05,
        evidence_calls: list[dict[str, object]] | None = None,
        ensure_not_cancelled_side_effect=None,
        scan_handler=None,
    ) -> tuple[
        dict[str, object],
        InMemoryBatchRepository,
        RendezvousFakeBrowser,
        list[dict[str, object]],
        list[object],
    ]:
        if repository is None:
            repository = InMemoryBatchRepository(claim_plan=claim_plan)
        if browser is None:
            browser = RendezvousFakeBrowser(page_configs)
        if evidence_calls is None:
            evidence_calls = []

        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(side_effect=ensure_not_cancelled_side_effect),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )

        async def fake_scan(page, **kwargs):
            if scan_handler is not None:
                return await scan_handler(page, **kwargs)
            evidence_calls.append(
                {
                    "group_name": kwargs.get("group_name"),
                    "actual_group_name": kwargs.get("actual_group_name"),
                    "page_index": getattr(page, "index", None),
                    "scan_target": kwargs.get("scan_target"),
                }
            )
            return _verified_scan_result(kwargs["scan_target"])

        real_sleep = asyncio.sleep

        async def controlled_sleep(delay: float = 0.0) -> None:
            await real_sleep(min(0.005, max(0.0, float(delay))))

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "scan_open_zalo_chat", side_effect=fake_scan),
            patch.object(
                parallel_service,
                "update_bcnb_reports",
                AsyncMock(return_value={"target_outcomes": []}),
            ),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service, "TARGET_SEARCH_IDLE_SECONDS", 0.0),
            patch.object(
                parallel_service,
                "TARGET_SEARCH_RESULT_TIMEOUT_SECONDS",
                result_timeout_seconds,
            ),
            patch.object(parallel_service.asyncio, "sleep", side_effect=controlled_sleep),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=job_id,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [],
                    "targets": targets,
                },
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused-evidence-root",
                screenshots_dir="unused-screenshots-dir",
                runtime=runtime,
                job_repo=job_repo,
                audit_repo=object(),
                evidence_repo=evidence_repo,
            )

        return result, repository, browser, evidence_calls, telemetry_log.call_args_list

    async def test_stale_header_eventually_matching_collects_evidence_only_after_exact_match(self):
        """Criterion 1: A stale header that later becomes the exact claimed group

        reaches evidence collection only after the exact match.
        """
        timeline: list[str] = []
        target = _make_search_target(1)
        claimed_group = str(target["group_name"])

        def record_read(val):
            timeline.append(f"header_read:{val}")

        page_config = {
            "header_values": [
                "MindX - VT-OLD-CHAT",
                claimed_group,
            ],
            "header_locked": True,
            "on_header_read": record_read,
        }

        evidence_calls: list[dict[str, object]] = []

        async def tracking_scan(page, **kwargs):
            timeline.append(f"evidence_collected:{kwargs.get('actual_group_name')}")
            evidence_calls.append(
                {
                    "group_name": kwargs.get("group_name"),
                    "actual_group_name": kwargs.get("actual_group_name"),
                    "page_index": getattr(page, "index", None),
                    "scan_target": kwargs.get("scan_target"),
                }
            )
            return _verified_scan_result(kwargs["scan_target"])

        repository = InMemoryBatchRepository()
        browser = RendezvousFakeBrowser([page_config])

        result, repository, browser, _, _ = (
            await self._run_integration_batch(
                job_id=901,
                targets=[target],
                page_configs=[page_config],
                repository=repository,
                browser=browser,
                evidence_calls=evidence_calls,
                scan_handler=tracking_scan,
            )
        )

        self.assertEqual(len(evidence_calls), 1)
        self.assertEqual(evidence_calls[0]["group_name"], claimed_group)
        self.assertEqual(evidence_calls[0]["actual_group_name"], claimed_group)
        self.assertEqual(
            timeline,
            [
                "header_read:MindX - VT-OLD-CHAT",
                f"header_read:{claimed_group}",
                f"evidence_collected:{claimed_group}",
            ],
        )

        rows = await repository.list_for_job(901)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scan_status"], "VERIFIED")
        self.assertIsNone(rows[0]["error_code"])
        self.assertEqual(len(browser.sessions[0].pages), 1)
        self.assertFalse(result["halted"])

    async def test_transient_unreadable_header_eventually_matching_collects_evidence_only_after_exact_match(
        self,
    ):
        """Criterion 2: A transient unreadable header followed by the exact claimed group

        reaches evidence collection only after the exact match, through the full
        target scan path rather than a direct helper-only test.
        """
        timeline: list[str] = []
        target = _make_search_target(1)
        claimed_group = str(target["group_name"])

        def record_read(val):
            timeline.append(f"header_read:{val}")

        page_config = {
            "header_values": [
                PlaywrightTimeoutError("header loading"),
                claimed_group,
            ],
            "header_locked": True,
            "on_header_read": record_read,
        }

        evidence_calls: list[dict[str, object]] = []

        async def tracking_scan(page, **kwargs):
            timeline.append(f"evidence_collected:{kwargs.get('actual_group_name')}")
            evidence_calls.append(
                {
                    "group_name": kwargs.get("group_name"),
                    "actual_group_name": kwargs.get("actual_group_name"),
                    "page_index": getattr(page, "index", None),
                    "scan_target": kwargs.get("scan_target"),
                }
            )
            return _verified_scan_result(kwargs["scan_target"])

        repository = InMemoryBatchRepository()
        browser = RendezvousFakeBrowser([page_config])

        result, repository, browser, _, _ = (
            await self._run_integration_batch(
                job_id=909,
                targets=[target],
                page_configs=[page_config],
                repository=repository,
                browser=browser,
                evidence_calls=evidence_calls,
                scan_handler=tracking_scan,
            )
        )

        self.assertEqual(len(evidence_calls), 1)
        self.assertEqual(evidence_calls[0]["group_name"], claimed_group)
        self.assertEqual(evidence_calls[0]["actual_group_name"], claimed_group)
        self.assertEqual(
            timeline,
            [
                "header_read:header loading",
                f"header_read:{claimed_group}",
                f"evidence_collected:{claimed_group}",
            ],
        )

        rows = await repository.list_for_job(909)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scan_status"], "VERIFIED")
        self.assertIsNone(rows[0]["error_code"])
        self.assertEqual(len(browser.sessions[0].pages), 1)
        self.assertFalse(result["halted"])

    async def test_permanently_wrong_header_retries_once_on_fresh_page_and_fails_closed(self):
        """Criterion 3: A permanently wrong header becomes the existing typed target-search

        failure, retries once on a fresh page, then records one fail-closed target result
        without duplicate claim or completion.
        """
        target = _make_search_target(1)
        page_configs = [
            {"header_text": "MindX - VT-WRONG-A", "header_locked": True},
            {"header_text": "MindX - VT-WRONG-B", "header_locked": True},
        ]
        result, repository, browser, evidence_calls, telemetry_calls = (
            await self._run_integration_batch(
                job_id=902,
                targets=[target],
                page_configs=page_configs,
                result_timeout_seconds=0.03,
            )
        )

        self.assertEqual(len(evidence_calls), 0)

        rows = await repository.list_for_job(902)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["scan_status"], "FAILED")
        self.assertEqual(row["error_code"], TARGET_SEARCH_RESULT_TIMEOUT)
        self.assertEqual(
            row["error_message"],
            "Zalo search không đủ bằng chứng; target cần kiểm tra thủ công.",
        )
        self.assertEqual(row["result"]["evidence_state"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(
            row["result"]["target_error_code"],
            TARGET_SEARCH_RESULT_TIMEOUT,
        )
        self.assertEqual(row["result"]["target_error_attempt"], 2)

        self.assertEqual(repository.claimed_target_ids, [("scan-1", 1)])
        self.assertEqual(repository.complete_scan_calls, [(1, "FAILED")])

        self.assertEqual(len(browser.sessions[0].pages), 2)
        self.assertTrue(browser.sessions[0].pages[0].closed)
        self.assertTrue(browser.sessions[0].pages[1].closed)

        timeout_telemetry = [
            call
            for call in telemetry_calls
            if call.kwargs.get("error_code") == TARGET_SEARCH_RESULT_TIMEOUT
        ]
        self.assertEqual(len(timeout_telemetry), 2)
        self.assertEqual([c.kwargs.get("attempt") for c in timeout_telemetry], [1, 2])
        self.assertEqual(
            [c.kwargs.get("phase") for c in timeout_telemetry],
            ["SEARCH_RESULT", "SEARCH_RESULT"],
        )
        self.assertTrue(result["halted"])

    async def test_missing_header_times_out_retries_on_fresh_page_and_fails_closed(self):
        """Criterion 3 (missing header): Repeated PlaywrightTimeoutError during

        header wait times out into TARGET_SEARCH_RESULT_TIMEOUT, retries once on
        a fresh page, then completes with one fail-closed result.
        """
        target = _make_search_target(1)
        page_configs = [
            {"header_error": PlaywrightTimeoutError("locator not attached")},
            {"header_error": PlaywrightTimeoutError("locator not attached")},
        ]
        _, repository, browser, evidence_calls, telemetry_calls = (
            await self._run_integration_batch(
                job_id=903,
                targets=[target],
                page_configs=page_configs,
                result_timeout_seconds=0.03,
            )
        )

        self.assertEqual(len(evidence_calls), 0)
        rows = await repository.list_for_job(903)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["scan_status"], "FAILED")
        self.assertEqual(row["error_code"], TARGET_SEARCH_RESULT_TIMEOUT)
        self.assertEqual(repository.claimed_target_ids, [("scan-1", 1)])
        self.assertEqual(repository.complete_scan_calls, [(1, "FAILED")])
        self.assertEqual(len(browser.sessions[0].pages), 2)
        self.assertTrue(all(page.closed for page in browser.sessions[0].pages))

    async def test_transient_wrong_header_recovers_on_fresh_page_retry(self):
        """Criterion 3 recovery: A header failure on page 1 retries on a fresh page,

        and successfully completes when page 2 matches the claimed group.
        """
        target = _make_search_target(1)
        claimed_group = str(target["group_name"])
        page_configs = [
            {"header_text": "MindX - VT-WRONG", "header_locked": True},
            {"header_text": claimed_group, "header_locked": True},
        ]
        result, repository, browser, evidence_calls, _ = (
            await self._run_integration_batch(
                job_id=904,
                targets=[target],
                page_configs=page_configs,
                result_timeout_seconds=0.03,
            )
        )

        self.assertEqual(len(evidence_calls), 1)
        self.assertEqual(evidence_calls[0]["page_index"], 2)
        self.assertEqual(evidence_calls[0]["actual_group_name"], claimed_group)

        rows = await repository.list_for_job(904)
        self.assertEqual(rows[0]["scan_status"], "VERIFIED")
        self.assertIsNone(rows[0]["error_code"])
        self.assertEqual(repository.claimed_target_ids, [("scan-1", 1)])
        self.assertEqual(repository.complete_scan_calls, [(1, "VERIFIED")])

        self.assertEqual(len(browser.sessions[0].pages), 2)
        self.assertTrue(browser.sessions[0].pages[0].closed)
        self.assertFalse(result["halted"])

    async def test_cancellation_while_waiting_for_header_preserves_safe_path(self):
        """Criterion 4: Cancellation while waiting leaves the target on the existing safe

        cancellation path and does not collect evidence.
        """
        target = _make_search_target(1)
        cancellation_armed = False
        cancellation_invoked = False

        def arm_cancellation(_val):
            nonlocal cancellation_armed
            cancellation_armed = True

        async def cancellable_check(_job_id=None):
            nonlocal cancellation_invoked
            if cancellation_armed:
                cancellation_invoked = True
                raise JobCancelledError("Cancellation requested while polling header")

        page_config = {
            "header_text": "MindX - VT-OLD-WAITING",
            "header_locked": True,
            "on_header_read": arm_cancellation,
        }
        repository = InMemoryBatchRepository()
        browser = RendezvousFakeBrowser([page_config])
        evidence_calls: list[dict[str, object]] = []

        with self.assertRaises(JobCancelledError):
            await self._run_integration_batch(
                job_id=905,
                targets=[target],
                page_configs=[page_config],
                repository=repository,
                browser=browser,
                evidence_calls=evidence_calls,
                ensure_not_cancelled_side_effect=cancellable_check,
                result_timeout_seconds=1.0,
            )

        self.assertTrue(cancellation_invoked)
        self.assertEqual(len(evidence_calls), 0)

        rows = await repository.list_for_job(905)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["scan_status"], "FAILED")
        self.assertEqual(row["error_code"], "JOB_CANCELLED")
        self.assertEqual(row["error_message"], "Đã dừng tại điểm an toàn.")
        self.assertEqual(
            row["result"]["evidence"]["classifier_result"]["decision"],
            "JOB_CANCELLED",
        )
        self.assertEqual(row["result"]["error_message"], "Đã dừng tại điểm an toàn.")
        self.assertEqual(repository.complete_scan_calls, [(1, "FAILED")])
        self.assertEqual(repository.claimed_target_ids, [("scan-1", 1)])
        self.assertTrue(browser.sessions[0].pages[0].closed)

    async def test_near_identical_group_name_is_never_treated_as_claimed_group(self):
        """Criterion 5: A near-identical group name is never treated as the claimed group;

        a permanently wrong near-match times out and retries without collecting evidence.
        """
        self.assertFalse(open_chat_matches("MindX - VT-JSB14", "MindX - VT-JSB140"))
        self.assertFalse(open_chat_matches("MindX - VT-C4K-GB40", "MindX - VT-C4K-GB41"))
        self.assertFalse(open_chat_matches("MindX - VT-C4K-PTI08", "MindX - VT-C4K-PTI09"))
        self.assertFalse(open_chat_matches("MindX - VT-JSB14", "MindX - JSB14"))
        self.assertFalse(open_chat_matches("MindX - VT-JSB14", "MindX - VT-JSB"))
        self.assertFalse(open_chat_matches("MindX - VT-JSB14", ""))

        target = _make_search_target(1)
        target["group_name"] = "MindX - VT-JSB14"
        target["class_code"] = "VT-JSB14"
        near_match_header = "MindX - VT-JSB140"

        page_configs = [
            {"header_text": near_match_header, "header_locked": True},
            {"header_text": near_match_header, "header_locked": True},
        ]
        _, repository, browser, evidence_calls, _ = (
            await self._run_integration_batch(
                job_id=906,
                targets=[target],
                page_configs=page_configs,
                result_timeout_seconds=0.03,
            )
        )

        self.assertEqual(len(evidence_calls), 0)

        rows = await repository.list_for_job(906)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scan_status"], "FAILED")
        self.assertEqual(rows[0]["error_code"], TARGET_SEARCH_RESULT_TIMEOUT)
        self.assertEqual(len(browser.sessions[0].pages), 2)

    async def test_closed_page_non_timeout_error_uses_typed_error_path_and_sanitizes_message(self):
        """Criterion 5: A closed page or non-timeout header-read error uses the existing

        typed error path (TARGET_SEARCH_RESULT_ERROR), retries once on a fresh page,
        and sanitizes raw error details.
        """
        target = _make_search_target(1)
        secret_marker = "synthetic-closed-page-memory-leak-marker-987"
        closed_error = RuntimeError(
            f"Target page, context or browser has been closed: {secret_marker}"
        )

        page_configs = [
            {"header_error": closed_error},
            {"header_error": closed_error},
        ]
        _, repository, browser, evidence_calls, telemetry_calls = (
            await self._run_integration_batch(
                job_id=907,
                targets=[target],
                page_configs=page_configs,
                result_timeout_seconds=1.0,
            )
        )

        self.assertEqual(len(evidence_calls), 0)
        rows = await repository.list_for_job(907)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["scan_status"], "FAILED")
        self.assertEqual(row["error_code"], TARGET_SEARCH_RESULT_ERROR)
        self.assertEqual(
            row["error_message"],
            "Zalo search không đủ bằng chứng; target cần kiểm tra thủ công.",
        )
        self.assertEqual(row["result"]["target_error_code"], TARGET_SEARCH_RESULT_ERROR)
        self.assertEqual(row["result"]["target_error_attempt"], 2)

        self.assertNotIn(secret_marker, repr(row))
        self.assertTrue(all(secret_marker not in repr(call) for call in telemetry_calls))

        error_telemetry = [
            call
            for call in telemetry_calls
            if call.kwargs.get("error_code") == TARGET_SEARCH_RESULT_ERROR
        ]
        self.assertEqual(len(error_telemetry), 2)
        self.assertEqual([c.kwargs.get("attempt") for c in error_telemetry], [1, 2])

        self.assertEqual(repository.claimed_target_ids, [("scan-1", 1)])
        self.assertEqual(repository.complete_scan_calls, [(1, "FAILED")])
        self.assertEqual(len(browser.sessions[0].pages), 2)
        self.assertTrue(all(page.closed for page in browser.sessions[0].pages))

    async def test_non_timeout_error_recovers_on_fresh_page_retry(self):
        """Criterion 5 recovery: A non-timeout header-read error on attempt 1 retries

        once on a fresh page and successfully collects evidence if page 2 succeeds.
        """
        target = _make_search_target(1)
        claimed_group = str(target["group_name"])
        page_configs = [
            {"header_error": RuntimeError("Transient DOM execution context was destroyed")},
            {"header_text": claimed_group, "header_locked": True},
        ]
        result, repository, browser, evidence_calls, _ = (
            await self._run_integration_batch(
                job_id=908,
                targets=[target],
                page_configs=page_configs,
                result_timeout_seconds=1.0,
            )
        )

        self.assertEqual(len(evidence_calls), 1)
        self.assertEqual(evidence_calls[0]["page_index"], 2)
        self.assertEqual(evidence_calls[0]["actual_group_name"], claimed_group)

        rows = await repository.list_for_job(908)
        self.assertEqual(rows[0]["scan_status"], "VERIFIED")
        self.assertIsNone(rows[0]["error_code"])
        self.assertEqual(repository.complete_scan_calls, [(1, "VERIFIED")])
        self.assertEqual(len(browser.sessions[0].pages), 2)
        self.assertTrue(browser.sessions[0].pages[0].closed)
        self.assertFalse(result["halted"])


class ZaloTargetHeaderWaitUnitInvariantsTests(unittest.IsolatedAsyncioTestCase):
    """Unit-level invariants verifying the _wait_for_target_header loop directly."""

    async def test_wait_polls_through_timeouts_and_mismatches_to_exact_match(self):
        claimed_group = "MindX - VT-JSB14"
        locator = RendezvousHeaderLocator(
            values=[
                PlaywrightTimeoutError("loading selector"),
                "MindX - VT-OLD",
                "  MindX - VT-JSB14  ",
            ]
        )
        ensure_not_cancelled = AsyncMock()

        matched = await _wait_for_target_header(
            locator,
            claimed_group,
            ensure_not_cancelled,
        )

        self.assertEqual(matched, "MindX - VT-JSB14")
        self.assertEqual(locator.inner_text_calls, 3)
        self.assertEqual(ensure_not_cancelled.await_count, 3)

    async def test_wait_propagates_non_timeout_exception_immediately(self):
        locator = RendezvousHeaderLocator(
            error=RuntimeError("Page context destroyed")
        )
        ensure_not_cancelled = AsyncMock()

        with self.assertRaises(RuntimeError) as cm:
            await _wait_for_target_header(
                locator,
                "MindX - VT-JSB14",
                ensure_not_cancelled,
            )
        self.assertIn("Page context destroyed", str(cm.exception))
        self.assertEqual(locator.inner_text_calls, 1)

    async def test_wait_preserves_cancellation_on_every_cycle(self):
        locator = RendezvousHeaderLocator(text="MindX - VT-OLD", locked=True)
        ensure_not_cancelled = AsyncMock(
            side_effect=[None, None, JobCancelledError("Safe cancellation")]
        )

        with self.assertRaises(JobCancelledError):
            await _wait_for_target_header(
                locator,
                "MindX - VT-JSB14",
                ensure_not_cancelled,
            )
        self.assertEqual(ensure_not_cancelled.await_count, 3)

    async def test_wait_rejects_near_identical_group_names_until_exact_tokens(self):
        locator = RendezvousHeaderLocator(
            values=[
                "MindX - VT-JSB140",
                "MindX - VT-JSB",
                "MindX - VT-JSB14",
            ]
        )
        matched = await _wait_for_target_header(
            locator,
            "MindX - VT-JSB14",
            AsyncMock(),
        )
        self.assertEqual(matched, "MindX - VT-JSB14")
        self.assertEqual(locator.inner_text_calls, 3)

    async def test_wait_strips_surrounding_whitespace_from_exact_header(self):
        locator = RendezvousHeaderLocator(text=" \n\t MindX - VT-JSB14 \t\n ", locked=True)
        matched = await _wait_for_target_header(
            locator,
            "MindX - VT-JSB14",
            AsyncMock(),
        )
        self.assertEqual(matched, "MindX - VT-JSB14")


if __name__ == "__main__":
    unittest.main()
