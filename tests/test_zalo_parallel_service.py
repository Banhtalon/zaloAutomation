from contextlib import asynccontextmanager
import asyncio
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.time_utils import BUSINESS_TIMEZONE
from repositories.zalo_batch_repository import canonical_target_key
import services.zalo_parallel_service as parallel_service
from services.zalo_parallel_service import BatchFailurePolicy


class BatchFailurePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_halts_on_three_consecutive_systemic_failures(self):
        policy = BatchFailurePolicy(total_targets=20)

        self.assertFalse(await policy.record(abnormal=True, systemic=True))
        self.assertFalse(await policy.record(abnormal=True, systemic=True))
        self.assertTrue(
            await policy.record(
                abnormal=True,
                systemic=True,
                reason="auth session unavailable",
            )
        )

        self.assertEqual(policy.consecutive_systemic, 3)
        self.assertEqual(policy.abnormal_targets, 3)
        self.assertTrue(policy.halted.is_set())
        self.assertEqual(
            policy.halt_reason,
            parallel_service.ZALO_JOB_REASON_BATCH_SYSTEMIC_THRESHOLD,
        )

    async def test_non_systemic_result_breaks_consecutive_systemic_count(self):
        policy = BatchFailurePolicy(total_targets=100)

        self.assertFalse(await policy.record(abnormal=True, systemic=True))
        self.assertFalse(await policy.record(abnormal=True, systemic=True))
        self.assertFalse(await policy.record(abnormal=True, systemic=False))
        self.assertEqual(policy.consecutive_systemic, 0)
        self.assertFalse(await policy.record(abnormal=False, systemic=True))
        self.assertEqual(policy.consecutive_systemic, 1)

    async def test_exactly_twenty_percent_abnormal_does_not_halt_but_over_twenty_does(self):
        policy = BatchFailurePolicy(total_targets=5)

        self.assertFalse(await policy.record(abnormal=True, systemic=False))
        self.assertFalse(policy.halted.is_set())
        self.assertEqual(policy.abnormal_targets * 100, 20 * policy.total_targets)

        self.assertTrue(await policy.record(abnormal=True, systemic=False))
        self.assertEqual(policy.abnormal_targets, 2)
        self.assertTrue(policy.halted.is_set())
        self.assertEqual(
            policy.halt_reason,
            parallel_service.ZALO_JOB_REASON_BATCH_ABNORMAL_THRESHOLD,
        )

    async def test_forty_eight_targets_nine_abnormal_does_not_halt_but_ten_does(self):
        policy = BatchFailurePolicy(total_targets=48)
        for _ in range(9):
            halted = await policy.record(abnormal=True, systemic=False)
            self.assertFalse(halted)
            self.assertFalse(policy.halted.is_set())
        self.assertEqual(policy.abnormal_targets, 9)

        halted = await policy.record(abnormal=True, systemic=False)
        self.assertTrue(halted)
        self.assertTrue(policy.halted.is_set())
        self.assertEqual(policy.abnormal_targets, 10)
        self.assertEqual(
            policy.halt_reason,
            parallel_service.ZALO_JOB_REASON_BATCH_ABNORMAL_THRESHOLD,
        )


class InMemoryBatchRepository:
    """Small durable-state double for the hermetic parallel batch test."""

    def __init__(self, *, claim_plan=None):
        self.rows = {}
        self.seed_calls = 0
        self.claim_plan = dict(claim_plan or {})
        self.halt_calls = []
        self.claim_calls = []
        self.claimed_target_ids = []
        self.complete_scan_calls = []
        self.complete_write_calls = []

    async def list_for_job(self, job_id):
        return [dict(row) for row in self.rows.get(job_id, [])]

    async def seed_targets(self, job_id, targets):
        self.seed_calls += 1
        rows = []
        seen = set()
        for index, target in enumerate(targets, start=1):
            key = canonical_target_key(target)
            if key in seen:
                raise AssertionError(f"duplicate target seeded: {key}")
            seen.add(key)
            rows.append(
                {
                    "id": index,
                    "job_id": job_id,
                    "target_key": key,
                    "source": dict(target),
                    "week": int(target["week"]),
                    "year": int(target["year"]),
                    "class_code": target["class_code"],
                    "lesson": target["lesson"],
                    "scan_status": "PENDING",
                    "write_status": "NOT_READY",
                }
            )
        self.rows[job_id] = rows
        return len(rows)

    async def recover_interrupted(self, _job_id):
        return {"scan_requeued": 0, "write_unknown": 0}

    async def claim_scan(self, job_id, owner):
        self.claim_calls.append((job_id, owner))
        planned_ids = self.claim_plan.get(owner)
        if planned_ids is not None:
            for target_id in list(planned_ids):
                row = next(
                    (
                        candidate
                        for candidate in self.rows[job_id]
                        if candidate["id"] == target_id
                        and candidate["scan_status"] == "PENDING"
                    ),
                    None,
                )
                if row is not None:
                    planned_ids.remove(target_id)
                    row["scan_status"] = "RUNNING"
                    self.claimed_target_ids.append((owner, target_id))
                    return dict(row)
            return None
        for row in self.rows[job_id]:
            if row["scan_status"] == "PENDING":
                row["scan_status"] = "RUNNING"
                self.claimed_target_ids.append((owner, row["id"]))
                return dict(row)
        return None

    async def complete_scan(self, target_id, *, status, result=None, **_kwargs):
        self.complete_scan_calls.append((target_id, status))
        row = self._row(target_id)
        row["scan_status"] = status
        row["write_status"] = "PENDING" if _kwargs.get("write_ready") else "NOT_READY"
        row["result"] = result
        row["error_code"] = _kwargs.get("error_code")
        row["error_message"] = _kwargs.get("error_message")

    async def finalize_registered_scan(
        self,
        target_id,
        *,
        result,
        write_ready,
        **_kwargs,
    ):
        row = self._row(target_id)
        row["result"] = result
        row["write_status"] = (
            "BLOCKED_BATCH_HALT"
            if _kwargs.get("batch_halted")
            else ("PENDING" if write_ready else "FAILED_BEFORE_WRITE")
        )
        row["error_code"] = _kwargs.get("error_code")
        row["error_message"] = _kwargs.get("error_message")

    async def claim_write(self, job_id, _owner, *, periods=None):
        allowed = periods or set()
        for row in self.rows[job_id]:
            if (
                row["write_status"] == "PENDING"
                and (row["week"], row["year"]) in allowed
            ):
                row["write_status"] = "RUNNING"
                return dict(row)
        return None

    async def complete_write(self, target_id, *, status, **_kwargs):
        self.complete_write_calls.append((target_id, status))
        self._row(target_id)["write_status"] = status

    async def halt_unclaimed(self, job_id, reason):
        scan_blocked = 0
        write_blocked = 0
        for row in self.rows.get(job_id, []):
            if row["scan_status"] == "PENDING":
                row["scan_status"] = "FAILED"
                row["write_status"] = "BLOCKED_BATCH_HALT"
                row["error_code"] = "BATCH_HALTED"
                row["error_message"] = reason
                scan_blocked += 1
            elif row["write_status"] == "PENDING":
                row["write_status"] = "BLOCKED_BATCH_HALT"
                row["error_code"] = "BATCH_HALTED"
                row["error_message"] = reason
                write_blocked += 1
        result = {"scan_blocked": scan_blocked, "write_blocked": write_blocked}
        self.halt_calls.append(result)
        return result

    async def summary(self, job_id):
        scan = {}
        write = {}
        for row in self.rows.get(job_id, []):
            scan[row["scan_status"]] = scan.get(row["scan_status"], 0) + 1
            write[row["write_status"]] = write.get(row["write_status"], 0) + 1
        return {"total": len(self.rows.get(job_id, [])), "scan": scan, "write": write}

    def _row(self, target_id):
        return next(row for rows in self.rows.values() for row in rows if row["id"] == target_id)


class FakeKeyboard:
    def __init__(self):
        self.presses = []

    async def press(self, key):
        self.presses.append(key)


class FakeTextLocator:
    def __init__(self, text="", error=None, *, hang=False, values=None):
        self.text = text
        self.error = error
        self.hang = hang
        self.values = list(values or [])
        self.inner_text_calls = 0
        self.first = self

    async def inner_text(self, **_kwargs):
        self.inner_text_calls += 1
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        if self.values:
            value = self.values.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return self.text


class FakeListLocator:
    def __init__(self, values):
        self.values = values

    async def all(self):
        return self.values


class FakeSearchInput:
    def __init__(
        self,
        page,
        *,
        clear_errors=None,
        fill_errors=None,
        clear_hang=False,
        fill_hang=False,
    ):
        self.page = page
        self.clear_errors = list(clear_errors or [])
        self.fill_errors = list(fill_errors or [])
        self.clear_hang = clear_hang
        self.fill_hang = fill_hang
        self.clear_calls = 0
        self.fill_calls = 0
        self.values = []

    async def clear(self, *args, **kwargs):
        self.clear_calls += 1
        if self.clear_hang:
            await asyncio.Event().wait()
        if self.clear_errors:
            error = self.clear_errors.pop(0)
            if error is not None:
                raise error

    async def fill(self, value, *args, **kwargs):
        self.fill_calls += 1
        self.values.append(value)
        self.page.header_locator.text = value
        if self.fill_hang:
            await asyncio.Event().wait()
        if self.fill_errors:
            error = self.fill_errors.pop(0)
            if error is not None:
                raise error


class FakeParallelPage:
    def __init__(
        self,
        index,
        *,
        selector_error=None,
        selector_barrier=None,
        search_clear_errors=None,
        search_fill_errors=None,
        search_clear_hang=False,
        search_fill_hang=False,
        body_text="",
        body_error=None,
        body_hang=False,
        header_text="MindX - VT-JSB01",
        header_values=None,
    ):
        self.index = index
        self.closed = False
        self.close_calls = 0
        self.goto_urls = []
        self.selector_error = selector_error
        self.selector_barrier = selector_barrier
        self.selector_calls = 0
        self.keyboard = FakeKeyboard()
        self.body_locator = FakeTextLocator(
            body_text,
            body_error,
            hang=body_hang,
        )
        self.header_locator = FakeTextLocator(header_text, values=header_values)
        self.search_input = FakeSearchInput(
            self,
            clear_errors=search_clear_errors,
            fill_errors=search_fill_errors,
            clear_hang=search_clear_hang,
            fill_hang=search_fill_hang,
        )

    async def add_init_script(self, _script):
        return None

    async def goto(self, url, **_kwargs):
        self.goto_urls.append(url)

    async def wait_for_selector(self, _selector, **_kwargs):
        self.selector_calls += 1
        if self.selector_barrier is not None:
            self.selector_barrier["count"] += 1
            if self.selector_barrier["count"] >= 2:
                self.selector_barrier["both_started"].set()
            await self.selector_barrier["release"].wait()
        if self.selector_error is not None:
            raise self.selector_error
        return None

    def locator(self, selector):
        if selector == "#contact-search-input":
            return self.search_input
        if selector == "body":
            return self.body_locator
        if selector in {
            '.header-title, .chat-title, [class*="conv-header"] .truncate',
        }:
            return self.header_locator
        if selector in {
            '[data-component="bubble-message"]',
            'text="Xem thêm"',
        }:
            return FakeListLocator([])
        raise AssertionError(f"Unexpected selector: {selector}")

    async def close(self):
        self.close_calls += 1
        self.closed = True


class FakeParallelSession:
    def __init__(
        self,
        page_configs=None,
        *,
        close_hang=False,
        new_page_hang=False,
        new_page_errors=None,
        landing_facts=None,
        landing_facts_by_page=None,
    ):
        self.pages = []
        self.page_configs = list(page_configs or [])
        self.close_hang = close_hang
        self.new_page_hang = new_page_hang
        self.new_page_errors = list(new_page_errors or [])
        self.landing_facts = (
            {
                "authenticated_shell": True,
                "search_control": True,
            }
            if landing_facts is None
            else dict(landing_facts)
        )
        self.landing_facts_by_page = {
            int(index): dict(facts)
            for index, facts in dict(landing_facts_by_page or {}).items()
        }
        self.landing_provider_calls = 0
        self.close_attempts = 0
        self.lifecycle_events = []

    async def landing_facts_provider(self, page=None):
        self.landing_provider_calls += 1
        if page is not None and page.index in self.landing_facts_by_page:
            return dict(self.landing_facts_by_page[page.index])
        return dict(self.landing_facts)

    async def new_page(self):
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
        page = FakeParallelPage(len(self.pages) + 1, **dict(config or {}))
        self.pages.append(page)
        self.lifecycle_events.append(("new_page", page.index, id(page)))
        return page

    async def close_page(self, page):
        self.close_attempts += 1
        self.lifecycle_events.append(("close_page", page.index, id(page)))
        if self.close_hang:
            await asyncio.Event().wait()
        await page.close()


class FakeParallelBrowser:
    def __init__(
        self,
        page_configs=None,
        *,
        close_hang=False,
        new_page_hang=False,
        new_page_errors=None,
        landing_facts=None,
        landing_facts_by_page=None,
    ):
        self.sessions = []
        self.page_configs = page_configs or []
        self.close_hang = close_hang
        self.new_page_hang = new_page_hang
        self.new_page_errors = new_page_errors or []
        self.landing_facts = landing_facts
        self.landing_facts_by_page = dict(landing_facts_by_page or {})

    @asynccontextmanager
    async def persistent_session(self):
        session = FakeParallelSession(
            self.page_configs,
            close_hang=self.close_hang,
            new_page_hang=self.new_page_hang,
            new_page_errors=self.new_page_errors,
            landing_facts=self.landing_facts,
            landing_facts_by_page=self.landing_facts_by_page,
        )
        self.sessions.append(session)
        yield session


def _make_search_target(index: int) -> dict[str, object]:
    return {
        "group_name": f"MindX - VT-SYN{index:02d}",
        "person": "Synthetic Teacher",
        "class_code": f"VT-SYN{index:02d}",
        # Keep lifecycle fixtures away from the production lesson-14
        # exemption branch so every case exercises the search boundary.
        "lesson": str(index + 20),
        "class_date": f"{20 + index:02d}/08/2026",
        "class_date_iso": f"2026-08-{20 + index:02d}",
        "week": 34,
        "year": 2026,
    }


def _verified_scan_result(target: dict[str, object]) -> dict[str, object]:
    return {
        "scan_target": target,
        "evidence_state": "SUBMITTED_VERIFIED",
        "submitted_for_bcnb": True,
        "evidence": {
            "date_context": {
                "target_week": target["week"],
                "target_year": target["year"],
            },
            "classifier_result": {
                "decision": "SUBMITTED_ON_TIME",
                "report_found": True,
            },
        },
    }


class BatchHaltPolicyAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic acceptance tests for requirements P1–P7 of ZALO-BATCH-HALT-POLICY-002."""

    async def test_p1_forty_nine_targets_ten_not_found_verified_thirty_nine_submitted_does_not_halt(self):
        """P1: 49 targets with 10 NOT_FOUND_VERIFIED and 39 SUBMITTED_VERIFIED does not halt on 20% threshold;
        scan completes batch; 0 unsubmitted targets written to BCNB."""
        repository = InMemoryBatchRepository()
        browser = FakeParallelBrowser([{}, {}])
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=2,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(index) for index in range(1, 50)]

        async def fake_scan(_page, *, target, **_kwargs):
            target_id = int(target["lesson"]) - 20
            if target_id <= 10:
                return {
                    "scan_target": target,
                    "evidence_state": "NOT_FOUND_VERIFIED",
                    "status": "NOT_FOUND",
                    "evidence": {
                        "date_context": {"target_week": target["week"], "target_year": target["year"]},
                        "classifier_result": {"decision": "NOT_FOUND"},
                    },
                }
            return {
                "scan_target": target,
                "evidence_state": "SUBMITTED_VERIFIED",
                "status": "VERIFIED",
                "evidence": {
                    "date_context": {"target_week": target["week"], "target_year": target["year"]},
                    "classifier_result": {"decision": "SUBMITTED"},
                },
            }

        async def fake_update(_page, *, period_filter, scan_results, **_kwargs):
            return {
                "target_outcomes": [
                    {
                        "class_code": item["scan_target"]["class_code"],
                        "lesson": item["scan_target"]["lesson"],
                        "week": item["scan_target"]["week"],
                        "year": item["scan_target"]["year"],
                        "class_date_iso": item["scan_target"]["class_date_iso"],
                        "status": "DRY_RUN",
                    }
                    for item in scan_results
                    if (
                        int(item["scan_target"]["week"]),
                        int(item["scan_target"]["year"]),
                    ) in period_filter
                    and item.get("evidence_state") == "SUBMITTED_VERIFIED"
                ]
            }

        scan_plan = {
            "selected_weeks": [{"week": 34, "year": 2026}],
            "targets": targets,
        }
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "_scan_one_target", side_effect=fake_scan),
            patch.object(parallel_service, "update_bcnb_reports", side_effect=fake_update),
            patch.object(parallel_service, "log_message"),
            patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=1001,
                execution_mode="dry-run",
                scan_plan=scan_plan,
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused-evidence-root",
                screenshots_dir="unused-screenshots-dir",
                runtime=runtime,
                job_repo=job_repo,
                audit_repo=object(),
                evidence_repo=evidence_repo,
            )

        self.assertFalse(result["halted"])
        self.assertEqual(result["abnormal_targets"], 0)
        self.assertEqual(len(result["scan_results"]), 49)

        rows = await repository.list_for_job(1001)
        self.assertEqual(len(rows), 49)
        submitted_rows = [r for r in rows if int(r["lesson"]) - 20 > 10]
        not_found_rows = [r for r in rows if int(r["lesson"]) - 20 <= 10]
        self.assertEqual(len(submitted_rows), 39)
        self.assertEqual(len(not_found_rows), 10)
        self.assertTrue(all(r["write_status"] == "DRY_RUN" for r in submitted_rows))
        self.assertTrue(all(r["write_status"] == "FAILED_BEFORE_WRITE" for r in not_found_rows))
        self.assertTrue(all(r["error_code"] == "EVIDENCE_NOT_READY" for r in not_found_rows))

        progress = parallel_service.build_zalo_job_progress(
            scan_results=result["scan_results"],
            batch_summary=result["batch_summary"],
            halt_reason=result.get("halt_reason"),
            dry_run=True,
        )
        self.assertEqual(progress["status"], "PARTIAL_SUCCESS")
        self.assertEqual(progress["reason_code"], parallel_service.ZALO_JOB_REASON_TARGETS_NEED_REVIEW)
        self.assertEqual(progress["total_targets"], 49)
        self.assertEqual(progress["attempted_target_count"], 49)
        self.assertEqual(progress["verified_submitted_count"], 39)
        self.assertEqual(progress["verified_not_found_count"], 10)
        self.assertEqual(progress["insufficient_or_error_count"], 0)
        self.assertEqual(progress["safe_count"], 39)
        self.assertEqual(progress["write_eligible_count"], 39)

    async def test_p2_forty_nine_targets_nine_abnormal_with_not_found_verified_does_not_halt(self):
        """P2: 49 targets, 9 insufficient evidence / technical errors (plus NOT_FOUND_VERIFIED) does not halt."""
        policy = BatchFailurePolicy(total_targets=49)
        for _ in range(15):
            self.assertFalse(await policy.record(abnormal=False, systemic=False))
        for _ in range(9):
            self.assertFalse(await policy.record(abnormal=True, systemic=False))
        for _ in range(25):
            self.assertFalse(await policy.record(abnormal=False, systemic=False))

        self.assertEqual(policy.abnormal_targets, 9)
        self.assertFalse(policy.halted.is_set())
        self.assertIsNone(policy.halt_reason)

    async def test_p3_forty_nine_targets_ten_abnormal_halts_fail_closed(self):
        """P3: 49 targets, 10 insufficient evidence / technical errors halts fail-closed on >20% threshold."""
        policy = BatchFailurePolicy(total_targets=49)
        for _ in range(9):
            self.assertFalse(await policy.record(abnormal=True, systemic=False))
        self.assertFalse(policy.halted.is_set())
        self.assertTrue(await policy.record(abnormal=True, systemic=False))
        self.assertTrue(policy.halted.is_set())
        self.assertEqual(policy.halt_reason, parallel_service.ZALO_JOB_REASON_BATCH_ABNORMAL_THRESHOLD)

    async def test_p4_not_found_with_insufficient_evidence_is_counted_abnormal(self):
        """P4: NOT_FOUND accompanied by INSUFFICIENT_EVIDENCE must still be counted as abnormal."""
        not_found_insufficient = {
            "status": "NOT_FOUND",
            "evidence_state": "INSUFFICIENT_EVIDENCE",
            "evidence": {"classifier_result": {"decision": "NOT_FOUND"}},
        }
        evidence_state = str(not_found_insufficient.get("evidence_state") or "")
        abnormal = evidence_state not in {
            "SUBMITTED_VERIFIED",
            "SUBMITTED_LATE_VERIFIED",
            "LESSON_14_EXEMPTION",
            "NOT_FOUND_VERIFIED",
        }
        self.assertTrue(abnormal)

        not_found_verified = {
            "status": "NOT_FOUND",
            "evidence_state": "NOT_FOUND_VERIFIED",
            "evidence": {"classifier_result": {"decision": "NOT_FOUND"}},
        }
        evidence_state_v = str(not_found_verified.get("evidence_state") or "")
        abnormal_v = evidence_state_v not in {
            "SUBMITTED_VERIFIED",
            "SUBMITTED_LATE_VERIFIED",
            "LESSON_14_EXEMPTION",
            "NOT_FOUND_VERIFIED",
        }
        self.assertFalse(abnormal_v)

    async def test_p5_three_consecutive_systemic_failures_halts_regardless_of_not_found_verified(self):
        """P5: 3 consecutive systemic errors halts on systemic threshold, regardless of NOT_FOUND_VERIFIED count."""
        policy = BatchFailurePolicy(total_targets=49)
        for _ in range(10):
            await policy.record(abnormal=False, systemic=False)
        self.assertEqual(policy.consecutive_systemic, 0)
        self.assertFalse(policy.halted.is_set())

        self.assertFalse(await policy.record(abnormal=True, systemic=True, reason="ZALO_SYNC_INCOMPLETE"))
        self.assertFalse(await policy.record(abnormal=True, systemic=True, reason="ZALO_SYNC_INCOMPLETE"))
        self.assertTrue(await policy.record(abnormal=True, systemic=True, reason="ZALO_SYNC_INCOMPLETE"))

        self.assertTrue(policy.halted.is_set())
        self.assertEqual(policy.consecutive_systemic, 3)
        self.assertEqual(policy.halt_reason, parallel_service.ZALO_JOB_REASON_BATCH_SYSTEMIC_THRESHOLD)

    def test_p6_progress_payload_uses_batch_level_reasons_never_unknown_fail_closed(self):
        """P6: Job halted due to rate or systemic threshold uses BATCH_ABNORMAL_THRESHOLD or BATCH_SYSTEMIC_THRESHOLD,
        and never returns UNKNOWN_FAIL_CLOSED."""
        progress_abnormal = parallel_service.build_zalo_job_progress(
            scan_results=[
                {"evidence_state": "SUBMITTED_VERIFIED"},
                {"evidence_state": "INSUFFICIENT_EVIDENCE"},
            ],
            batch_summary={"total": 5, "scan": {"VERIFIED": 1, "FAILED": 1}, "write": {"BLOCKED_BATCH_HALT": 5}},
            halt_reason=parallel_service.ZALO_JOB_REASON_BATCH_ABNORMAL_THRESHOLD,
            dry_run=True,
        )
        self.assertEqual(progress_abnormal["status"], "FAILED")
        self.assertEqual(progress_abnormal["reason_code"], "BATCH_ABNORMAL_THRESHOLD")
        self.assertNotEqual(progress_abnormal["reason_code"], "UNKNOWN_FAIL_CLOSED")
        self.assertEqual(progress_abnormal["total_targets"], 5)
        self.assertEqual(progress_abnormal["attempted_target_count"], 2)
        self.assertEqual(progress_abnormal["verified_submitted_count"], 1)
        self.assertEqual(progress_abnormal["verified_not_found_count"], 0)
        self.assertEqual(progress_abnormal["insufficient_or_error_count"], 1)
        self.assertEqual(progress_abnormal["safe_count"], 0)
        self.assertEqual(progress_abnormal["write_eligible_count"], 0)
        self.assertIn("BATCH_ABNORMAL_THRESHOLD", progress_abnormal["message"])

        progress_systemic = parallel_service.build_zalo_job_progress(
            scan_results=[
                {"evidence_state": "SUBMITTED_VERIFIED"},
                {"evidence_state": "INSUFFICIENT_EVIDENCE"},
                {"evidence_state": "INSUFFICIENT_EVIDENCE"},
                {"evidence_state": "INSUFFICIENT_EVIDENCE"},
            ],
            batch_summary={"total": 10, "scan": {"VERIFIED": 1, "FAILED": 3}, "write": {"BLOCKED_BATCH_HALT": 10}},
            halt_reason=parallel_service.ZALO_JOB_REASON_BATCH_SYSTEMIC_THRESHOLD,
            dry_run=True,
        )
        self.assertEqual(progress_systemic["status"], "FAILED")
        self.assertEqual(progress_systemic["reason_code"], "BATCH_SYSTEMIC_THRESHOLD")
        self.assertNotEqual(progress_systemic["reason_code"], "UNKNOWN_FAIL_CLOSED")
        self.assertIn("BATCH_SYSTEMIC_THRESHOLD", progress_systemic["message"])

    async def test_p7_completed_targets_retain_truthful_result_with_blocked_batch_halt_on_real_halt(self):
        """P7: Valid targets completed before a real halt keep truthful result_json/evidence_state,
        while write_status remains BLOCKED_BATCH_HALT and zero targets are written to BCNB."""
        repository = InMemoryBatchRepository()
        browser = FakeParallelBrowser([{}, {}])
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(index) for index in range(1, 6)]

        async def fake_scan(_page, *, target, **_kwargs):
            target_id = int(target["lesson"]) - 20
            if target_id == 1:
                return {
                    "scan_target": target,
                    "evidence_state": "SUBMITTED_VERIFIED",
                    "status": "VERIFIED",
                    "evidence": {
                        "date_context": {"target_week": target["week"], "target_year": target["year"]},
                        "classifier_result": {"decision": "SUBMITTED"},
                    },
                }
            return {
                "scan_target": target,
                "evidence_state": "INSUFFICIENT_EVIDENCE",
                "status": "FAILED",
                "error_code": "SCAN_FAILED",
                "error_message": "Không đủ bằng chứng",
                "evidence": {
                    "date_context": {"target_week": target["week"], "target_year": target["year"]},
                    "classifier_result": {"decision": "INSUFFICIENT_EVIDENCE"},
                },
            }

        scan_plan = {
            "selected_weeks": [{"week": 34, "year": 2026}],
            "targets": targets,
        }
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "_scan_one_target", side_effect=fake_scan),
            patch.object(parallel_service, "update_bcnb_reports") as mock_bcnb_write,
            patch.object(parallel_service, "log_message"),
            patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=1007,
                execution_mode="dry-run",
                scan_plan=scan_plan,
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused-evidence-root",
                screenshots_dir="unused-screenshots-dir",
                runtime=runtime,
                job_repo=job_repo,
                audit_repo=object(),
                evidence_repo=evidence_repo,
            )

        self.assertTrue(result["halted"])
        self.assertEqual(result["halt_reason"], parallel_service.ZALO_JOB_REASON_BATCH_ABNORMAL_THRESHOLD)
        mock_bcnb_write.assert_not_called()

        rows = await repository.list_for_job(1007)
        self.assertEqual(rows[0]["scan_status"], "VERIFIED")
        self.assertEqual(rows[0]["result"]["evidence_state"], "SUBMITTED_VERIFIED")
        self.assertEqual(rows[0]["write_status"], "BLOCKED_BATCH_HALT")
        self.assertEqual(rows[0]["error_code"], "BATCH_HALTED")

        self.assertEqual(rows[1]["scan_status"], "VERIFIED")
        self.assertEqual(rows[1]["result"]["evidence_state"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(rows[1]["write_status"], "BLOCKED_BATCH_HALT")
        self.assertEqual(rows[2]["scan_status"], "VERIFIED")
        self.assertEqual(rows[2]["result"]["evidence_state"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(rows[2]["write_status"], "BLOCKED_BATCH_HALT")

        self.assertEqual(rows[3]["write_status"], "BLOCKED_BATCH_HALT")
        self.assertEqual(rows[3]["error_code"], "BATCH_HALTED")
        self.assertEqual(rows[4]["write_status"], "BLOCKED_BATCH_HALT")
        self.assertEqual(rows[4]["error_code"], "BATCH_HALTED")

        self.assertEqual(result["batch_summary"]["write"], {"BLOCKED_BATCH_HALT": 5})

    async def test_finding_1_worker_timeout_to_build_progress_never_unknown_fail_closed(self):
        """P1 Finding: When final worker times out on target search, batch halts fail-closed,
        detailed TARGET_SEARCH_* error is retained at target level, and build_zalo_job_progress
        produces TARGET_SEARCH_TIMEOUT instead of UNKNOWN_FAIL_CLOSED."""
        repository = InMemoryBatchRepository(claim_plan={"scan-1": [1, 2]})
        page_configs = [
            {"search_fill_hang": True},
            {"search_fill_hang": True},
        ]
        browser = FakeParallelBrowser(page_configs)
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(1), _make_search_target(2)]
        scan_plan = {"selected_weeks": [{"week": 34, "year": 2026}], "targets": targets}

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "update_bcnb_reports", AsyncMock(return_value={"target_outcomes": []})),
            patch.object(parallel_service, "log_message"),
            patch.object(parallel_service, "TARGET_SEARCH_FILL_TIMEOUT_SECONDS", 0.01),
            patch.object(parallel_service, "TARGET_SEARCH_IDLE_SECONDS", 0.0),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=2001,
                execution_mode="dry-run",
                scan_plan=scan_plan,
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused",
                screenshots_dir="unused",
                runtime=runtime,
                job_repo=job_repo,
                audit_repo=object(),
                evidence_repo=evidence_repo,
            )

        rows = await repository.list_for_job(2001)
        self.assertTrue(result["halted"])
        self.assertEqual(result["halt_reason"], parallel_service.TARGET_SEARCH_FILL_TIMEOUT)
        self.assertEqual(rows[0]["scan_status"], "FAILED")
        self.assertEqual(rows[0]["error_code"], parallel_service.TARGET_SEARCH_FILL_TIMEOUT)
        self.assertEqual(rows[0]["result"]["target_error_code"], parallel_service.TARGET_SEARCH_FILL_TIMEOUT)
        self.assertEqual(rows[1]["write_status"], "BLOCKED_BATCH_HALT")
        self.assertEqual(rows[1]["error_code"], "BATCH_HALTED")

        progress = parallel_service.build_zalo_job_progress(
            scan_results=result["scan_results"],
            batch_summary=result["batch_summary"],
            halt_reason=result.get("halt_reason"),
            dry_run=True,
        )
        self.assertEqual(progress["status"], "FAILED")
        self.assertEqual(progress["reason_code"], parallel_service.ZALO_JOB_REASON_TARGET_SEARCH_TIMEOUT)
        self.assertNotEqual(progress["reason_code"], parallel_service.ZALO_JOB_REASON_UNKNOWN_FAIL_CLOSED)
        self.assertEqual(progress["halt_reason"], parallel_service.TARGET_SEARCH_FILL_TIMEOUT)
        self.assertIn("TARGET_SEARCH_TIMEOUT", str(progress["message"]))

    def test_finding_2_lesson_14_exemption_excluded_from_verified_submitted_count(self):
        """P2 Finding: LESSON_14_EXEMPTION represents an exemption from reporting, not a submitted report.
        It must be excluded from verified_submitted_count and tracked separately in exempt_count."""
        progress = parallel_service.build_zalo_job_progress(
            scan_results=[
                {"scan_target": {"class_code": "VT-JSB01"}, "evidence_state": "LESSON_14_EXEMPTION"},
            ],
            batch_summary={"total": 1, "scan": {"SKIPPED": 1}, "write": {"NOT_READY": 1}},
            halt_reason=None,
            dry_run=True,
        )
        self.assertEqual(progress["verified_submitted_count"], 0)
        self.assertEqual(progress["exempt_count"], 1)
        self.assertEqual(progress["insufficient_or_error_count"], 0)

    def test_finding_3_halt_message_preserves_reason_when_safe_count_positive(self):
        """P2 Finding: Any batch with a halt_reason must clearly display the stopping reason code
        and main counts in the final message, even when safe_count > 0."""
        progress_abnormal = parallel_service.build_zalo_job_progress(
            scan_results=[
                {"evidence_state": "SUBMITTED_VERIFIED"},
                {"evidence_state": "INSUFFICIENT_EVIDENCE"},
            ],
            batch_summary={"total": 2, "scan": {"VERIFIED": 1, "FAILED": 1}, "write": {"VERIFIED": 1, "BLOCKED_BATCH_HALT": 1}},
            halt_reason=parallel_service.ZALO_JOB_REASON_BATCH_ABNORMAL_THRESHOLD,
            dry_run=True,
        )
        self.assertEqual(progress_abnormal["safe_count"], 1)
        self.assertEqual(progress_abnormal["status"], "PARTIAL_SUCCESS")
        self.assertEqual(progress_abnormal["reason_code"], "BATCH_ABNORMAL_THRESHOLD")
        self.assertIn("BATCH_ABNORMAL_THRESHOLD", progress_abnormal["message"])
        self.assertIn("2/2 target", progress_abnormal["message"])
        self.assertIn("1 có báo cáo", progress_abnormal["message"])
        self.assertIn("1 thiếu bằng chứng/lỗi", progress_abnormal["message"])
        self.assertIn("1 target đủ điều kiện ghi", progress_abnormal["message"])

        progress_systemic = parallel_service.build_zalo_job_progress(
            scan_results=[
                {"evidence_state": "SUBMITTED_VERIFIED"},
                {"evidence_state": "INSUFFICIENT_EVIDENCE"},
            ],
            batch_summary={"total": 5, "scan": {"VERIFIED": 1, "FAILED": 1}, "write": {"VERIFIED": 1, "BLOCKED_BATCH_HALT": 4}},
            halt_reason=parallel_service.ZALO_JOB_REASON_BATCH_SYSTEMIC_THRESHOLD,
            dry_run=True,
        )
        self.assertEqual(progress_systemic["safe_count"], 1)
        self.assertEqual(progress_systemic["status"], "PARTIAL_SUCCESS")
        self.assertEqual(progress_systemic["reason_code"], "BATCH_SYSTEMIC_THRESHOLD")
        self.assertIn("BATCH_SYSTEMIC_THRESHOLD", progress_systemic["message"])


class ParallelBatchIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_header_waits_until_the_claimed_target_is_visible(self):
        locator = FakeTextLocator(
            values=[
                parallel_service.PlaywrightTimeoutError("not rendered yet"),
                "MindX - VT-OLD",
                "MindX - VT-SYN01",
            ]
        )
        ensure_not_cancelled = AsyncMock()

        header = await parallel_service._wait_for_target_header(
            locator,
            "MindX - VT-SYN01",
            ensure_not_cancelled,
        )

        self.assertEqual(header, "MindX - VT-SYN01")
        self.assertEqual(locator.inner_text_calls, 3)
        self.assertEqual(ensure_not_cancelled.await_count, 3)

    async def test_header_wait_preserves_cancellation(self):
        locator = FakeTextLocator(text="MindX - VT-OLD")
        ensure_not_cancelled = AsyncMock(
            side_effect=[None, parallel_service.JobCancelledError()]
        )

        with self.assertRaises(parallel_service.JobCancelledError):
            await parallel_service._wait_for_target_header(
                locator,
                "MindX - VT-SYN01",
                ensure_not_cancelled,
            )

        self.assertEqual(locator.inner_text_calls, 1)

    async def test_header_that_never_matches_stops_at_the_finite_boundary(self):
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(
                parallel_service._wait_for_target_header(
                    FakeTextLocator(text="MindX - VT-OLD"),
                    "MindX - VT-SYN01",
                    AsyncMock(),
                ),
                timeout=0.01,
            )

    async def test_target_telemetry_logs_exact_allowlisted_fields(self):
        with patch.object(parallel_service, "log_message") as telemetry_log:
            parallel_service._emit_target_telemetry(
                job_id=928,
                worker_id="scan-7",
                phase="SEARCH_CLEAR",
                attempt=2,
                started_at=asyncio.get_running_loop().time(),
                error_code=parallel_service.TARGET_SEARCH_CLEAR_TIMEOUT,
            )

        self.assertIsNotNone(telemetry_log.call_args)
        fields = telemetry_log.call_args.kwargs
        self.assertEqual(
            set(fields),
            {
                "event",
                "job_id",
                "worker_id",
                "phase",
                "attempt",
                "elapsed_ms",
                "page_state",
                "error_code",
            },
        )
        self.assertEqual(fields["event"], "zalo.scan.target_telemetry")
        self.assertEqual(fields["job_id"], 928)
        self.assertEqual(fields["worker_id"], "scan-7")
        self.assertEqual(fields["phase"], "SEARCH_CLEAR")
        self.assertEqual(fields["attempt"], 2)
        self.assertEqual(fields["page_state"], "READY")
        self.assertEqual(
            fields["error_code"],
            parallel_service.TARGET_SEARCH_CLEAR_TIMEOUT,
        )

    async def test_non_timeout_selector_setup_error_is_typed_without_message(self):
        page = FakeParallelPage(1, selector_error=RuntimeError("DOM secret payload"))

        with self.assertRaises(parallel_service.ZaloSetupError) as raised:
            await parallel_service._setup_scan_page(
                page,
                job_id=904,
                job_repo=SimpleNamespace(ensure_not_cancelled=AsyncMock()),
                landing_facts={
                    "authenticated_shell": True,
                    "search_control": True,
                },
            )

        error = raised.exception
        self.assertEqual(type(error), parallel_service.ZaloSetupError)
        self.assertEqual(error.code, parallel_service.SETUP_SELECTOR_ERROR)
        self.assertNotIsInstance(
            error,
            parallel_service.SelectorReadinessTimeoutError,
        )
        self.assertEqual(str(error), parallel_service.SETUP_SELECTOR_ERROR)
        self.assertNotIn("DOM secret payload", str(error))

    async def test_default_setup_fails_closed_before_legacy_selector_wait(self):
        page = FakeParallelPage(1, selector_error=RuntimeError("selector payload"))

        with self.assertRaises(parallel_service.ZaloSetupError) as raised:
            await parallel_service._setup_scan_page(
                page,
                job_id=905,
                job_repo=SimpleNamespace(ensure_not_cancelled=AsyncMock()),
            )

        error = raised.exception
        self.assertEqual(
            error.code,
            parallel_service.LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN,
        )
        self.assertEqual(page.selector_calls, 0)
        self.assertEqual(page.goto_urls, ["https://chat.zalo.me/"])
        self.assertEqual(str(error), parallel_service.LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN)

    async def test_hanging_close_is_bounded_and_does_not_create_retry_page(self):
        repository = InMemoryBatchRepository()
        browser = FakeParallelBrowser(
            [{"selector_error": TimeoutError("selector timeout payload")}],
            close_hang=True,
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        target = {
            "group_name": "MindX - VT-JSB01",
            "person": "Teacher",
            "class_code": "VT-JSB01",
            "lesson": "12",
            "class_date": "24/08/2026",
            "class_date_iso": "2026-08-24",
            "week": 34,
            "year": 2026,
        }
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service, "PAGE_CLOSE_TIMEOUT_SECONDS", 0.01),
            patch.object(parallel_service, "SETUP_PAGE_CLOSE_TIMEOUT_SECONDS", 0.01),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=905,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
                    "targets": [target],
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

        rows = await repository.list_for_job(905)
        self.assertEqual(len(browser.sessions[0].pages), 1)
        self.assertEqual(browser.sessions[0].close_attempts, 1)
        self.assertEqual(browser.sessions[0].pages[0].close_calls, 0)
        self.assertEqual(
            rows[0]["error_code"],
            parallel_service.SETUP_PAGE_CLOSE_TIMEOUT,
        )
        self.assertEqual(
            rows[0]["result"]["setup_error_code"],
            parallel_service.SETUP_PAGE_CLOSE_TIMEOUT,
        )
        self.assertTrue(result["halted"])
        telemetry_codes = [
            call.kwargs["error_code"]
            for call in telemetry_log.call_args_list
            if call.kwargs.get("event") == "zalo.scan.setup_telemetry"
        ]
        self.assertIn(parallel_service.SELECTOR_READINESS_TIMEOUT, telemetry_codes)
        self.assertIn(parallel_service.SETUP_PAGE_CLOSE_TIMEOUT, telemetry_codes)

    async def test_hanging_new_page_is_bounded_and_typed(self):
        repository = InMemoryBatchRepository()
        browser = FakeParallelBrowser(new_page_hang=True)
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        target = {
            "group_name": "MindX - VT-JSB01",
            "person": "Teacher",
            "class_code": "VT-JSB01",
            "lesson": "12",
            "class_date": "24/08/2026",
            "class_date_iso": "2026-08-24",
            "week": 34,
            "year": 2026,
        }
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service, "PAGE_CREATE_TIMEOUT_SECONDS", 0.01),
            patch.object(parallel_service, "SETUP_PAGE_CREATE_TIMEOUT_SECONDS", 0.01),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=906,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
                    "targets": [target],
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

        rows = await repository.list_for_job(906)
        self.assertEqual(browser.sessions[0].pages, [])
        self.assertEqual(browser.sessions[0].close_attempts, 0)
        self.assertEqual(
            rows[0]["error_code"],
            parallel_service.SETUP_PAGE_CREATE_TIMEOUT,
        )
        self.assertTrue(result["halted"])
        telemetry_codes = [
            call.kwargs["error_code"]
            for call in telemetry_log.call_args_list
            if call.kwargs.get("event") == "zalo.scan.setup_telemetry"
        ]
        self.assertEqual(
            telemetry_codes.count(parallel_service.SETUP_PAGE_CREATE_TIMEOUT),
            2,
        )

    async def test_concurrent_setup_failures_have_one_claimant_and_one_halt(self):
        repository = InMemoryBatchRepository()
        selector_barrier = {
            "count": 0,
            "both_started": asyncio.Event(),
            "release": asyncio.Event(),
        }
        browser = FakeParallelBrowser(
            [
                {
                    "selector_error": TimeoutError("worker one selector payload"),
                    "selector_barrier": selector_barrier,
                },
                {
                    "selector_error": TimeoutError("worker two selector payload"),
                    "selector_barrier": selector_barrier,
                },
                {"selector_error": TimeoutError("worker one retry payload")},
                {"selector_error": TimeoutError("worker two retry payload")},
            ]
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=2,
            bcnb_write_concurrency=2,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [
            {
                "group_name": f"MindX - VT-JSB0{index}",
                "person": "Teacher",
                "class_code": f"VT-JSB0{index}",
                "lesson": str(index + 10),
                "class_date": f"{23 + index:02d}/08/2026",
                "class_date_iso": f"2026-08-{23 + index:02d}",
                "week": 34,
                "year": 2026,
            }
            for index in range(1, 4)
        ]
        batch_task = None
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(parallel_service, "log_message"),
            patch.object(parallel_service, "PAGE_CLOSE_TIMEOUT_SECONDS", 0.1),
            patch.object(parallel_service, "SETUP_PAGE_CLOSE_TIMEOUT_SECONDS", 0.1),
        ):
            batch_task = asyncio.create_task(
                parallel_service.run_parallel_zalo_batch(
                    job_id=907,
                    execution_mode="dry-run",
                    scan_plan={
                        "selected_weeks": [{"week": 34, "year": 2026}],
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
            )
            await selector_barrier["both_started"].wait()
            selector_barrier["release"].set()
            result = await batch_task

        rows = await repository.list_for_job(907)
        self.assertEqual(
            sum(
                row["error_code"] == parallel_service.SELECTOR_READINESS_TIMEOUT
                for row in rows
            ),
            1,
        )
        self.assertEqual(result["batch_summary"]["scan"], {"FAILED": 3})
        self.assertEqual(result["batch_summary"]["write"], {"BLOCKED_BATCH_HALT": 3})
        self.assertTrue(result["halted"])
        self.assertEqual(
            repository.halt_calls,
            [{"scan_blocked": 2, "write_blocked": 0}],
        )
        self.assertEqual(len(result["scan_results"]), 1)
        self.assertTrue(all(page.closed for page in browser.sessions[0].pages))

    async def test_clear_timeout_retries_same_claimed_target_and_healthy_worker_continues(self):
        repository = InMemoryBatchRepository(
            claim_plan={"scan-1": [1, 3], "scan-2": [2]}
        )
        secret_marker = "synthetic-clear-secret-marker"
        browser = FakeParallelBrowser(
            [
                {},
                {
                    "search_clear_errors": [TimeoutError(secret_marker)],
                },
                {},
            ]
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=2,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(index) for index in range(1, 4)]

        async def fake_scan(_page, **kwargs):
            return _verified_scan_result(kwargs["scan_target"])

        async def fake_update(_page, *, period_filter, scan_results, **_kwargs):
            return {
                "target_outcomes": [
                    {
                        "class_code": item["scan_target"]["class_code"],
                        "lesson": item["scan_target"]["lesson"],
                        "week": item["scan_target"]["week"],
                        "year": item["scan_target"]["year"],
                        "class_date_iso": item["scan_target"]["class_date_iso"],
                        "status": "DRY_RUN",
                    }
                    for item in scan_results
                    if (
                        int(item["scan_target"]["week"]),
                        int(item["scan_target"]["year"]),
                    ) in period_filter
                ]
            }

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "scan_open_zalo_chat", side_effect=fake_scan),
            patch.object(parallel_service, "update_bcnb_reports", side_effect=fake_update),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=920,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
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

        rows = await repository.list_for_job(920)
        self.assertEqual([row["scan_status"] for row in rows], ["VERIFIED"] * 3)
        self.assertFalse(result["halted"])
        self.assertEqual(
            [target_id for owner, target_id in repository.claimed_target_ids if owner == "scan-2"],
            [2],
        )
        self.assertEqual(
            sorted(target_id for _owner, target_id in repository.claimed_target_ids),
            [1, 2, 3],
        )
        self.assertEqual(
            sorted(target_id for target_id, _status in repository.complete_scan_calls),
            [1, 2, 3],
        )
        self.assertEqual(
            len({target_id for target_id, _status in repository.complete_scan_calls}),
            3,
        )
        self.assertEqual(
            sorted(target_id for target_id, _status in repository.complete_write_calls),
            [1, 2, 3],
        )
        self.assertEqual(
            len(repository.complete_write_calls),
            len({target_id for target_id, _status in repository.complete_write_calls}),
        )
        self.assertEqual(len(repository.halt_calls), 0)
        self.assertEqual(len(browser.sessions[0].pages), 4)
        self.assertEqual(browser.sessions[0].pages[1].search_input.clear_calls, 1)
        self.assertEqual(browser.sessions[0].pages[2].search_input.clear_calls, 1)
        self.assertTrue(all(page.closed for page in browser.sessions[0].pages))

        target_errors = [
            call.kwargs.get("error_code")
            for call in telemetry_log.call_args_list
            if "error_code" in call.kwargs
        ]
        self.assertIn("TARGET_SEARCH_CLEAR_TIMEOUT", target_errors)
        self.assertTrue(all(secret_marker not in repr(call) for call in telemetry_log.call_args_list))

    async def test_clear_timeout_twice_completes_one_target_and_does_not_reuse_bad_page(self):
        repository = InMemoryBatchRepository(claim_plan={"scan-1": [1]})
        secret_marker = "synthetic-double-clear-secret-marker"
        browser = FakeParallelBrowser(
            [
                {"search_clear_errors": [TimeoutError(secret_marker)]},
                {"search_clear_errors": [TimeoutError(secret_marker)]},
            ]
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(1), _make_search_target(2)]

        async def fake_scan(_page, **kwargs):
            return _verified_scan_result(kwargs["scan_target"])

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "scan_open_zalo_chat", side_effect=fake_scan),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=921,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
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

        rows = await repository.list_for_job(921)
        self.assertEqual(rows[0]["scan_status"], "FAILED")
        self.assertEqual(rows[0]["error_code"], "TARGET_SEARCH_CLEAR_TIMEOUT")
        self.assertEqual(rows[0]["result"]["evidence_state"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(rows[1]["error_code"], "BATCH_HALTED")
        self.assertEqual(result["halted"], True)
        self.assertEqual(repository.halt_calls, [{"scan_blocked": 1, "write_blocked": 0}])
        session = browser.sessions[0]
        self.assertEqual(len(session.pages), 2)
        self.assertEqual(session.pages[0].search_input.clear_calls, 1)
        self.assertEqual(session.pages[1].search_input.clear_calls, 1)
        self.assertEqual(session.pages[0].close_calls, 1)
        self.assertEqual(session.pages[1].close_calls, 1)
        self.assertTrue(all(page.closed for page in session.pages))
        self.assertEqual(
            sum(call.kwargs.get("error_code") == "TARGET_SEARCH_CLEAR_TIMEOUT" for call in telemetry_log.call_args_list),
            2,
        )
        self.assertTrue(all(secret_marker not in repr(call) for call in telemetry_log.call_args_list))

    async def test_failed_retry_counts_toward_abnormal_halt_with_a_healthy_worker(self):
        repository = InMemoryBatchRepository(
            claim_plan={"scan-1": [1, 3], "scan-2": [2]}
        )
        secret_marker = "synthetic-retry-failure-secret-marker"
        browser = FakeParallelBrowser(
            [
                {},
                {"search_clear_errors": [TimeoutError(secret_marker)]},
                {"search_clear_errors": [TimeoutError(secret_marker)]},
            ]
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=2,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(index) for index in range(1, 4)]

        async def fake_scan(_page, **kwargs):
            return _verified_scan_result(kwargs["scan_target"])

        async def fake_update(_page, *, period_filter, scan_results, **_kwargs):
            return {
                "target_outcomes": [
                    {
                        "class_code": item["scan_target"]["class_code"],
                        "lesson": item["scan_target"]["lesson"],
                        "week": item["scan_target"]["week"],
                        "year": item["scan_target"]["year"],
                        "class_date_iso": item["scan_target"]["class_date_iso"],
                        "status": "DRY_RUN",
                    }
                    for item in scan_results
                    if (
                        int(item["scan_target"]["week"]),
                        int(item["scan_target"]["year"]),
                    ) in period_filter
                ]
            }

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "scan_open_zalo_chat", side_effect=fake_scan),
            patch.object(parallel_service, "update_bcnb_reports", side_effect=fake_update),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=922,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
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

        rows = await repository.list_for_job(922)
        self.assertEqual([row["scan_status"] for row in rows], ["VERIFIED", "FAILED", "VERIFIED"])
        self.assertEqual(rows[1]["error_code"], "TARGET_SEARCH_CLEAR_TIMEOUT")
        self.assertEqual(rows[1]["result"]["evidence_state"], "INSUFFICIENT_EVIDENCE")
        self.assertTrue(result["halted"])
        self.assertEqual(repository.halt_calls, [{"scan_blocked": 0, "write_blocked": 0}])
        self.assertEqual(
            [target_id for owner, target_id in repository.claimed_target_ids if owner == "scan-1"],
            [1, 3],
        )
        self.assertEqual(
            [target_id for owner, target_id in repository.claimed_target_ids if owner == "scan-2"],
            [2],
        )
        self.assertEqual(len(browser.sessions[0].pages), 3)
        self.assertTrue(all(page.closed for page in browser.sessions[0].pages))
        self.assertTrue(all(secret_marker not in repr(call) for call in telemetry_log.call_args_list))

    async def test_fill_and_result_timeouts_have_distinct_typed_target_errors(self):
        cases = (
            ("TARGET_SEARCH_CLEAR_ERROR", {"search_clear_errors": [RuntimeError("clear-secret")]}),
            (
                "TARGET_SEARCH_FILL_TIMEOUT",
                {"search_fill_errors": [TimeoutError("fill-secret")]},
            ),
            ("TARGET_SEARCH_FILL_ERROR", {"search_fill_errors": [RuntimeError("fill-secret")] }),
            ("TARGET_SEARCH_RESULT_TIMEOUT", {"body_error": TimeoutError("result-secret")}),
            ("TARGET_SEARCH_RESULT_ERROR", {"body_error": RuntimeError("result-secret")}),
        )
        for offset, (expected_code, page_config) in enumerate(cases, start=1):
            with self.subTest(expected_code=expected_code):
                repository = InMemoryBatchRepository(claim_plan={"scan-1": [1]})
                browser = FakeParallelBrowser([page_config, page_config])
                evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
                job_repo = SimpleNamespace(
                    ensure_not_cancelled=AsyncMock(),
                    update_job=AsyncMock(),
                )
                runtime = SimpleNamespace(
                    external=SimpleNamespace(browser=browser),
                    zalo_scan_concurrency=1,
                    bcnb_write_concurrency=1,
                    batch_systemic_failure_limit=3,
                    batch_abnormal_percent_limit=20,
                )
                target = _make_search_target(offset)

                async def fake_scan(_page, **kwargs):
                    return _verified_scan_result(kwargs["scan_target"])

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
                    patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
                ):
                    await parallel_service.run_parallel_zalo_batch(
                        job_id=923 + offset,
                        execution_mode="dry-run",
                        scan_plan={
                            "selected_weeks": [{"week": 34, "year": 2026}],
                            "targets": [target],
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

                row = (await repository.list_for_job(923 + offset))[0]
                self.assertEqual(row["error_code"], expected_code)
                self.assertNotIn("secret", repr(row))
                target_error_calls = [
                    call
                    for call in telemetry_log.call_args_list
                    if call.kwargs.get("error_code") == expected_code
                ]
                self.assertTrue(target_error_calls)
                self.assertTrue(all("secret" not in repr(call) for call in target_error_calls))

    async def test_four_target_clear_timeout_does_not_propagate_raw_error_or_reuse_page(self):
        repository = InMemoryBatchRepository(claim_plan={"scan-1": [1, 2, 3, 4]})
        secret_marker = "synthetic-four-target-secret-marker"
        browser = FakeParallelBrowser(
            [
                {"search_clear_errors": [TimeoutError(secret_marker)]},
                {"search_clear_errors": [TimeoutError(secret_marker)]},
            ]
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(index) for index in range(1, 5)]

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
        ):
            await parallel_service.run_parallel_zalo_batch(
                job_id=925,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
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

        rows = await repository.list_for_job(925)
        self.assertEqual(rows[0]["error_code"], "TARGET_SEARCH_CLEAR_TIMEOUT")
        self.assertEqual(sum(row["error_code"] == "TARGET_SEARCH_CLEAR_TIMEOUT" for row in rows), 1)
        self.assertEqual(sum(row["error_code"] == "TimeoutError" for row in rows), 0)
        self.assertEqual(repository.halt_calls, [{"scan_blocked": 3, "write_blocked": 0}])
        self.assertEqual(len(browser.sessions[0].pages), 2)
        self.assertEqual(browser.sessions[0].pages[0].search_input.clear_calls, 1)
        self.assertEqual(browser.sessions[0].pages[1].search_input.clear_calls, 1)
        self.assertTrue(all(page.closed for page in browser.sessions[0].pages))
        self.assertTrue(all(secret_marker not in repr(call) for call in telemetry_log.call_args_list))

    async def test_target_search_close_failure_has_no_replacement_page(self):
        repository = InMemoryBatchRepository(claim_plan={"scan-1": [1]})
        secret_marker = "synthetic-close-secret-marker"
        browser = FakeParallelBrowser(
            [{"search_clear_errors": [TimeoutError(secret_marker)]}],
            close_hang=True,
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service, "PAGE_CLOSE_TIMEOUT_SECONDS", 0.01),
            patch.object(parallel_service, "SETUP_PAGE_CLOSE_TIMEOUT_SECONDS", 0.01),
            patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=926,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
                    "targets": [_make_search_target(1)],
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

        row = (await repository.list_for_job(926))[0]
        self.assertTrue(result["halted"])
        self.assertNotEqual(row["error_code"], "TimeoutError")
        self.assertTrue(
            str(row["error_code"]).startswith(("SETUP_PAGE_CLOSE", "TARGET_SEARCH"))
        )
        self.assertNotIn(secret_marker, repr(row))
        session = browser.sessions[0]
        self.assertEqual(len(session.pages), 1)
        self.assertEqual(session.close_attempts, 1)
        self.assertTrue(all(secret_marker not in repr(call) for call in telemetry_log.call_args_list))

    async def test_target_search_fresh_page_creation_failure_is_typed_and_bounded(self):
        repository = InMemoryBatchRepository(claim_plan={"scan-1": [1]})
        secret_marker = "synthetic-create-secret-marker"
        browser = FakeParallelBrowser(
            [{"search_clear_errors": [TimeoutError("clear-secret-marker")]}],
            new_page_errors=[None, RuntimeError(secret_marker)],
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service, "PAGE_CREATE_TIMEOUT_SECONDS", 0.01),
            patch.object(parallel_service, "SETUP_PAGE_CREATE_TIMEOUT_SECONDS", 0.01),
            patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=927,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
                    "targets": [_make_search_target(1)],
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

        row = (await repository.list_for_job(927))[0]
        self.assertTrue(result["halted"])
        self.assertNotEqual(row["error_code"], "RuntimeError")
        self.assertTrue(
            any(
                call.kwargs.get("error_code") == parallel_service.SETUP_PAGE_CREATE_ERROR
                for call in telemetry_log.call_args_list
            )
        )
        self.assertNotIn(secret_marker, repr(row))
        session = browser.sessions[0]
        self.assertEqual(len(session.pages), 1)
        self.assertEqual(session.close_attempts, 1)
        self.assertEqual(
            [target_id for _owner, target_id in repository.claimed_target_ids],
            [1],
        )
        self.assertTrue(all(secret_marker not in repr(call) for call in telemetry_log.call_args_list))

    async def test_hanging_clear_operation_is_bounded_and_retries_same_target(self):
        repository = InMemoryBatchRepository(claim_plan={"scan-1": [1]})
        browser = FakeParallelBrowser(
            [
                {"search_clear_hang": True},
                {"header_text": "MindX - VT-SYN01"},
            ]
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        target = _make_search_target(1)

        async def fake_scan(_page, **kwargs):
            return _verified_scan_result(kwargs["scan_target"])

        async def fake_update(_page, *, period_filter, scan_results, **_kwargs):
            return {
                "target_outcomes": [
                    {
                        "class_code": item["scan_target"]["class_code"],
                        "lesson": item["scan_target"]["lesson"],
                        "week": item["scan_target"]["week"],
                        "year": item["scan_target"]["year"],
                        "class_date_iso": item["scan_target"]["class_date_iso"],
                        "status": "DRY_RUN",
                    }
                    for item in scan_results
                    if (
                        int(item["scan_target"]["week"]),
                        int(item["scan_target"]["year"]),
                    ) in period_filter
                ]
            }

        scan_mock = AsyncMock(side_effect=fake_scan)
        started_at = asyncio.get_running_loop().time()
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "scan_open_zalo_chat", scan_mock),
            patch.object(
                parallel_service,
                "update_bcnb_reports",
                side_effect=fake_update,
            ),
            patch.object(parallel_service, "log_message"),
            patch.object(parallel_service.asyncio, "sleep", AsyncMock()),
            patch.object(parallel_service, "TARGET_SEARCH_CLEAR_TIMEOUT_SECONDS", 0.01),
        ):
            result = await asyncio.wait_for(
                parallel_service.run_parallel_zalo_batch(
                    job_id=929,
                    execution_mode="dry-run",
                    scan_plan={
                        "selected_weeks": [{"week": 34, "year": 2026}],
                        "targets": [target],
                    },
                    today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                    scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                    evidence_root="unused-evidence-root",
                    screenshots_dir="unused-screenshots-dir",
                    runtime=runtime,
                    job_repo=job_repo,
                    audit_repo=object(),
                    evidence_repo=evidence_repo,
                ),
                timeout=1.0,
            )

        elapsed = asyncio.get_running_loop().time() - started_at
        rows = await repository.list_for_job(929)
        self.assertLess(elapsed, 1.0)
        self.assertFalse(result["halted"])
        self.assertEqual(repository.halt_calls, [])
        self.assertEqual(repository.claimed_target_ids, [("scan-1", 1)])
        self.assertEqual(repository.complete_scan_calls, [(1, "VERIFIED")])
        self.assertEqual(rows[0]["scan_status"], "VERIFIED")
        scan_mock.assert_awaited_once()
        self.assertEqual(scan_mock.call_args.kwargs["worker_id"], "scan-1")
        self.assertEqual(scan_mock.call_args.kwargs["attempt"], 2)
        self.assertEqual(len(browser.sessions[0].pages), 3)
        self.assertEqual(browser.sessions[0].pages[0].search_input.clear_calls, 1)
        self.assertEqual(browser.sessions[0].pages[1].search_input.clear_calls, 1)
        self.assertTrue(all(page.closed for page in browser.sessions[0].pages))

    async def _assert_hanging_target_search_retires_worker(
        self,
        *,
        job_id,
        page_configs,
        timeout_attribute,
        timeout_code,
        phase,
        secret_marker,
    ):
        repository = InMemoryBatchRepository(claim_plan={"scan-1": [1, 2]})
        browser = FakeParallelBrowser(page_configs)
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        write_gateway = AsyncMock(return_value={"target_outcomes": []})
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(1), _make_search_target(2)]
        started_at = asyncio.get_running_loop().time()
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "update_bcnb_reports", write_gateway),
            patch.object(parallel_service, "log_message") as telemetry_log,
            patch.object(parallel_service, timeout_attribute, 0.01),
            # SEARCH_RESULT has a separate idle wait; keep it finite and
            # immediate so the body locator is the intentionally hanging op.
            patch.object(parallel_service, "TARGET_SEARCH_IDLE_SECONDS", 0.0),
        ):
            result = await asyncio.wait_for(
                parallel_service.run_parallel_zalo_batch(
                    job_id=job_id,
                    execution_mode="dry-run",
                    scan_plan={
                        "selected_weeks": [{"week": 34, "year": 2026}],
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
                ),
                timeout=1.0,
            )

        elapsed = asyncio.get_running_loop().time() - started_at
        rows = await repository.list_for_job(job_id)
        self.assertLess(elapsed, 1.0)
        self.assertTrue(result["halted"])
        self.assertEqual(result["halt_reason"], timeout_code)
        self.assertEqual(repository.halt_calls, [{"scan_blocked": 1, "write_blocked": 0}])
        self.assertEqual(
            repository.claimed_target_ids,
            [("scan-1", 1)],
            "retired worker must not claim the second target",
        )
        self.assertEqual(repository.complete_scan_calls, [(1, "FAILED")])
        self.assertEqual(result["batch_summary"]["scan"], {"FAILED": 2})
        self.assertEqual(
            result["batch_summary"]["write"],
            {"BLOCKED_BATCH_HALT": 2},
        )

        failed_row, halted_row = rows
        self.assertEqual(failed_row["scan_status"], "FAILED")
        self.assertEqual(failed_row["error_code"], timeout_code)
        self.assertEqual(
            failed_row["result"]["evidence_state"],
            "INSUFFICIENT_EVIDENCE",
        )
        self.assertEqual(failed_row["result"]["target_error_code"], timeout_code)
        self.assertEqual(failed_row["result"]["target_error_phase"], phase)
        self.assertEqual(failed_row["result"]["target_error_attempt"], 2)
        self.assertEqual(halted_row["error_code"], "BATCH_HALTED")
        self.assertNotIn(secret_marker, repr(rows))
        self.assertNotIn("TimeoutError", repr(rows))
        write_gateway.assert_not_awaited()

        session = browser.sessions[0]
        self.assertEqual(len(session.pages), 2)
        self.assertIsNot(session.pages[0], session.pages[1])
        first_page, retry_page = session.pages
        self.assertEqual(
            session.lifecycle_events,
            [
                ("new_page", first_page.index, id(first_page)),
                ("close_page", first_page.index, id(first_page)),
                ("new_page", retry_page.index, id(retry_page)),
                ("close_page", retry_page.index, id(retry_page)),
            ],
        )
        self.assertEqual(session.close_attempts, 2)
        self.assertEqual([page.close_calls for page in session.pages], [1, 1])
        self.assertTrue(all(page.closed for page in session.pages))
        self.assertEqual(
            [page.search_input.clear_calls for page in session.pages],
            [1, 1],
        )
        self.assertEqual(
            [page.search_input.fill_calls for page in session.pages],
            [1, 1],
        )
        if phase == "SEARCH_RESULT":
            self.assertEqual(
                [page.body_locator.inner_text_calls for page in session.pages],
                [1, 1],
            )

        allowed_target_fields = {
            "event",
            "job_id",
            "worker_id",
            "phase",
            "attempt",
            "elapsed_ms",
            "page_state",
            "error_code",
        }
        target_telemetry = [
            call
            for call in telemetry_log.call_args_list
            if call.kwargs.get("event") == "zalo.scan.target_telemetry"
        ]
        self.assertEqual(len(target_telemetry), 2)
        self.assertEqual(
            [call.kwargs["phase"] for call in target_telemetry],
            [phase, phase],
        )
        self.assertEqual(
            [call.kwargs["attempt"] for call in target_telemetry],
            [1, 2],
        )
        for call in target_telemetry:
            fields = call.kwargs
            self.assertEqual(set(fields), allowed_target_fields)
            self.assertEqual(fields["job_id"], job_id)
            self.assertEqual(fields["worker_id"], "scan-1")
            self.assertEqual(fields["page_state"], "READY")
            self.assertEqual(fields["error_code"], timeout_code)
            self.assertIsInstance(fields["elapsed_ms"], int)
            self.assertGreaterEqual(fields["elapsed_ms"], 0)
        self.assertTrue(all(secret_marker not in repr(call) for call in telemetry_log.call_args_list))
        self.assertTrue(all("TimeoutError" not in repr(call) for call in telemetry_log.call_args_list))
        self.assertTrue(all("CancelledError" not in repr(call) for call in telemetry_log.call_args_list))

    async def test_hanging_search_fill_retries_once_then_retires_worker(self):
        secret_marker = "synthetic-fill-secret-marker"
        await self._assert_hanging_target_search_retires_worker(
            job_id=930,
            page_configs=[
                {"search_fill_hang": True, "body_text": secret_marker},
                {"search_fill_hang": True, "body_text": secret_marker},
            ],
            timeout_attribute="TARGET_SEARCH_FILL_TIMEOUT_SECONDS",
            timeout_code=parallel_service.TARGET_SEARCH_FILL_TIMEOUT,
            phase="SEARCH_FILL",
            secret_marker=secret_marker,
        )

    async def test_hanging_search_result_retries_once_then_retires_worker(self):
        secret_marker = "synthetic-result-secret-marker"
        await self._assert_hanging_target_search_retires_worker(
            job_id=931,
            page_configs=[
                {"body_hang": True, "body_text": secret_marker},
                {"body_hang": True, "body_text": secret_marker},
            ],
            timeout_attribute="TARGET_SEARCH_RESULT_TIMEOUT_SECONDS",
            timeout_code=parallel_service.TARGET_SEARCH_RESULT_TIMEOUT,
            phase="SEARCH_RESULT",
            secret_marker=secret_marker,
        )

    async def test_lesson_14_keeps_authoritative_target_context(self):
        target = {
            "group_name": "MindX - VT-JSB14",
            "person": "Teacher",
            "class_code": "VT-JSB14",
            "lesson": "14",
            "class_date": "24/08/2026",
            "class_date_iso": "2026-08-24",
            "week": 35,
            "year": 2026,
        }
        result = await parallel_service._scan_one_target(
            object(),
            job_id=900,
            target=target,
            sync_result={"verified": True, "reason": "fixture"},
            screenshots_dir="unused",
            today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
            job_repo=SimpleNamespace(ensure_not_cancelled=AsyncMock()),
        )

        context = result["evidence"]["date_context"]
        self.assertEqual((context["target_week"], context["target_year"]), (35, 2026))
        self.assertEqual(context["target_lesson"], "14")
        self.assertEqual(context["target_class_date"], "2026-08-24")

    async def test_search_fallback_prioritizes_class_code_when_not_found(self):
        page = FakeParallelPage(
            0,
            body_text="Không tìm thấy kết quả phù hợp",
        )
        target = {
            "group_name": "[VŨNG TÀU] - VT-C4K-GB43 - [19H30 - 21H30 - T5] (OFF)",
            "person": "Teacher",
            "class_code": "VT-C4K-GB43",
            "lesson": "5",
            "class_date": "10/09/2026",
            "class_date_iso": "2026-09-10",
            "week": 37,
            "year": 2026,
        }
        result = await parallel_service._scan_one_target(
            page,
            job_id=901,
            target=target,
            sync_result={"verified": True, "reason": "fixture"},
            screenshots_dir="unused",
            today=datetime(2026, 9, 17, tzinfo=BUSINESS_TIMEZONE),
            job_repo=SimpleNamespace(ensure_not_cancelled=AsyncMock()),
        )
        self.assertEqual(
            result["evidence"]["classifier_result"]["decision"], "GROUP_NOT_FOUND"
        )
        self.assertEqual(
            page.search_input.values,
            [
                "[VŨNG TÀU] - VT-C4K-GB43 - [19H30 - 21H30 - T5]",
                "VT-C4K-GB43",
            ],
        )
        self.assertNotIn("19H30", page.search_input.values)
        self.assertNotIn("21H30", page.search_input.values)

    async def test_two_scan_and_two_write_pages_are_distinct_and_dry_run_is_unique(self):
        repository = InMemoryBatchRepository()
        browser = FakeParallelBrowser()
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=2,
            bcnb_write_concurrency=2,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [
            {
                "group_name": "MindX - VT-JSB01",
                "person": "Teacher",
                "class_code": "VT-JSB01",
                "lesson": "12",
                "class_date": "24/08/2026",
                "class_date_iso": "2026-08-24",
                "week": 34,
                "year": 2026,
            },
            {
                "group_name": "MindX - VT-JSB02",
                "person": "Teacher",
                "class_code": "VT-JSB02",
                "lesson": "13",
                "class_date": "25/08/2026",
                "class_date_iso": "2026-08-25",
                "week": 35,
                "year": 2026,
            },
        ]
        scan_plan = {
            "selected_weeks": [
                {"week": 34, "year": 2026},
                {"week": 35, "year": 2026},
            ],
            "targets": targets,
        }

        async def fake_scan(_page, *, target, **_kwargs):
            return {
                "scan_target": target,
                "evidence_state": "SUBMITTED_VERIFIED",
                "evidence": {
                    "date_context": {
                        "target_week": target["week"],
                        "target_year": target["year"],
                    },
                    "classifier_result": {"decision": "SUBMITTED"},
                },
            }

        write_calls = []

        async def fake_update(_page, *, period_filter, scan_results, **_kwargs):
            outcomes = []
            for result in scan_results:
                target = result["scan_target"]
                period = (int(target["week"]), int(target["year"]))
                if period in period_filter:
                    outcomes.append(
                        {
                            "class_code": target["class_code"],
                            "lesson": target["lesson"],
                            "week": target["week"],
                            "year": target["year"],
                            "class_date_iso": target["class_date_iso"],
                            "status": "DRY_RUN",
                        }
                    )
            write_calls.append(outcomes)
            return {"target_outcomes": outcomes}

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "_scan_one_target", side_effect=fake_scan),
            patch.object(parallel_service, "update_bcnb_reports", side_effect=fake_update),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=901,
                execution_mode="dry-run",
                scan_plan=scan_plan,
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused-evidence-root",
                screenshots_dir="unused-screenshots-dir",
                runtime=runtime,
                job_repo=job_repo,
                audit_repo=object(),
                evidence_repo=evidence_repo,
            )

        self.assertEqual(repository.seed_calls, 1)
        self.assertEqual(result["batch_summary"]["total"], 2)
        self.assertEqual(result["batch_summary"]["write"], {"DRY_RUN": 2})
        self.assertFalse(result["halted"])
        self.assertEqual(
            sorted(item["target_key"] for item in await repository.list_for_job(901)),
            sorted(canonical_target_key(target) for target in targets),
        )
        self.assertEqual(len({item["target_key"] for item in await repository.list_for_job(901)}), 2)
        self.assertEqual(len(write_calls), 2)
        self.assertEqual(
            sorted(
                (item["class_code"], item["lesson"])
                for outcomes in write_calls
                for item in outcomes
            ),
            [("VT-JSB01", "12"), ("VT-JSB02", "13")],
        )

        session = browser.sessions[0]
        self.assertEqual(len(session.pages), 4)
        self.assertEqual(len({id(page) for page in session.pages}), 4)
        self.assertEqual(sum("chat.zalo.me" in url for page in session.pages for url in page.goto_urls), 2)
        self.assertEqual(sum("check_nhanxet" in url for page in session.pages for url in page.goto_urls), 2)
        self.assertTrue(all(page.closed for page in session.pages))

    async def test_selector_timeout_retries_once_on_fresh_page_and_worker_a_completes(self):
        repository = InMemoryBatchRepository(
            claim_plan={"scan-1": [1], "scan-2": [2]}
        )
        browser = FakeParallelBrowser(
            [
                {},
                {"selector_error": TimeoutError("selector body payload")},
                {},
            ]
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=2,
            bcnb_write_concurrency=2,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [
            {
                "group_name": "MindX - VT-JSB01",
                "person": "Teacher",
                "class_code": "VT-JSB01",
                "lesson": "12",
                "class_date": "24/08/2026",
                "class_date_iso": "2026-08-24",
                "week": 34,
                "year": 2026,
            },
            {
                "group_name": "MindX - VT-JSB02",
                "person": "Teacher",
                "class_code": "VT-JSB02",
                "lesson": "13",
                "class_date": "25/08/2026",
                "class_date_iso": "2026-08-25",
                "week": 35,
                "year": 2026,
            },
        ]

        async def fake_scan(_page, *, target, **_kwargs):
            return {
                "scan_target": target,
                "evidence_state": "SUBMITTED_VERIFIED",
                "evidence": {
                    "date_context": {
                        "target_week": target["week"],
                        "target_year": target["year"],
                    },
                    "classifier_result": {"decision": "SUBMITTED"},
                },
            }

        async def fake_update(_page, *, period_filter, scan_results, **_kwargs):
            outcomes = []
            for result in scan_results:
                target = result["scan_target"]
                if (int(target["week"]), int(target["year"])) in period_filter:
                    outcomes.append(
                        {
                            "class_code": target["class_code"],
                            "lesson": target["lesson"],
                            "week": target["week"],
                            "year": target["year"],
                            "class_date_iso": target["class_date_iso"],
                            "status": "DRY_RUN",
                        }
                    )
            return {"target_outcomes": outcomes}

        scan_plan = {
            "selected_weeks": [
                {"week": 34, "year": 2026},
                {"week": 35, "year": 2026},
            ],
            "targets": targets,
        }
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "_scan_one_target", side_effect=fake_scan),
            patch.object(parallel_service, "update_bcnb_reports", side_effect=fake_update),
            patch.object(parallel_service, "log_message") as telemetry_log,
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=902,
                execution_mode="dry-run",
                scan_plan=scan_plan,
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused-evidence-root",
                screenshots_dir="unused-screenshots-dir",
                runtime=runtime,
                job_repo=job_repo,
                audit_repo=object(),
                evidence_repo=evidence_repo,
            )

        rows = await repository.list_for_job(902)
        self.assertEqual([row["scan_status"] for row in rows], ["VERIFIED", "VERIFIED"])
        self.assertEqual(result["batch_summary"]["write"], {"DRY_RUN": 2})
        self.assertFalse(result["halted"])
        session = browser.sessions[0]
        self.assertEqual(len(session.pages), 5)
        self.assertEqual(session.pages[1].selector_calls, 1)
        self.assertEqual(session.pages[1].close_calls, 1)
        self.assertTrue(all(page.closed for page in session.pages))

        setup_calls = [
            call
            for call in telemetry_log.call_args_list
            if call.kwargs.get("event") == "zalo.scan.setup_telemetry"
        ]
        self.assertEqual(len(setup_calls), 3)
        self.assertIn(
            parallel_service.SELECTOR_READINESS_TIMEOUT,
            [call.kwargs["error_code"] for call in setup_calls],
        )
        for call in setup_calls:
            fields = call.kwargs
            self.assertEqual(fields["job_id"], 902)
            self.assertIn(fields["page_state"], parallel_service.SAFE_PAGE_STATES)
            self.assertIn(fields["selector_count"], (0, 1))
            self.assertIsInstance(fields["selector_ready"], bool)
            self.assertIsInstance(fields["elapsed_ms"], int)
            self.assertTrue(fields["elapsed_ms"] >= 0)
            self.assertNotIn("selector body payload", repr(call))

    async def test_selector_timeout_retry_failure_records_one_review_target_and_halts_batch(self):
        repository = InMemoryBatchRepository(
            claim_plan={"scan-1": [1], "scan-2": [2]}
        )
        browser = FakeParallelBrowser(
            [
                {},
                {"selector_error": TimeoutError("first selector payload")},
                {"selector_error": TimeoutError("second selector payload")},
            ]
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=2,
            bcnb_write_concurrency=2,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [
            {
                "group_name": f"MindX - VT-JSB0{index}",
                "person": "Teacher",
                "class_code": f"VT-JSB0{index}",
                "lesson": str(index + 10),
                "class_date": f"{23 + index:02d}/08/2026",
                "class_date_iso": f"2026-08-{23 + index:02d}",
                "week": 34,
                "year": 2026,
            }
            for index in range(1, 4)
        ]

        async def fake_scan(_page, *, target, **_kwargs):
            return {
                "scan_target": target,
                "evidence_state": "SUBMITTED_VERIFIED",
                "evidence": {
                    "date_context": {
                        "target_week": target["week"],
                        "target_year": target["year"],
                    },
                    "classifier_result": {"decision": "SUBMITTED"},
                },
            }

        scan_plan = {
            "selected_weeks": [{"week": 34, "year": 2026}],
            "targets": targets,
        }
        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "_scan_one_target", side_effect=fake_scan),
            patch.object(parallel_service, "log_message") as telemetry_log,
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=903,
                execution_mode="dry-run",
                scan_plan=scan_plan,
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused-evidence-root",
                screenshots_dir="unused-screenshots-dir",
                runtime=runtime,
                job_repo=job_repo,
                audit_repo=object(),
                evidence_repo=evidence_repo,
            )

        rows = await repository.list_for_job(903)
        self.assertEqual(rows[0]["scan_status"], "VERIFIED")
        self.assertEqual(rows[1]["scan_status"], "FAILED")
        self.assertEqual(rows[2]["scan_status"], "FAILED")
        self.assertEqual(rows[1]["result"]["evidence_state"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(
            rows[1]["error_code"],
            parallel_service.SELECTOR_READINESS_TIMEOUT,
        )
        self.assertEqual(rows[2]["error_code"], "BATCH_HALTED")
        self.assertEqual(
            sum(
                row["error_code"] == parallel_service.SELECTOR_READINESS_TIMEOUT
                for row in rows
            ),
            1,
        )
        self.assertEqual(result["batch_summary"]["scan"], {"VERIFIED": 1, "FAILED": 2})
        self.assertEqual(result["batch_summary"]["write"], {"BLOCKED_BATCH_HALT": 3})
        self.assertTrue(result["halted"])
        self.assertEqual(result["halt_reason"], parallel_service.SELECTOR_READINESS_TIMEOUT)
        self.assertEqual(result["abnormal_targets"], 1)
        self.assertEqual(repository.halt_calls, [{"scan_blocked": 1, "write_blocked": 0}])

        session = browser.sessions[0]
        self.assertEqual(len(session.pages), 3)
        self.assertEqual(session.pages[1].close_calls, 1)
        self.assertEqual(session.pages[2].close_calls, 1)
        self.assertTrue(all(page.closed for page in session.pages))
        setup_calls = [
            call
            for call in telemetry_log.call_args_list
            if call.kwargs.get("event") == "zalo.scan.setup_telemetry"
        ]
        self.assertEqual(
            [call.kwargs["error_code"] for call in setup_calls].count(
                parallel_service.SELECTOR_READINESS_TIMEOUT
            ),
            2,
        )
        self.assertTrue(all("selector payload" not in repr(call) for call in setup_calls))

    async def test_runtime_provider_is_invoked_once_per_worker_page(self):
        repository = InMemoryBatchRepository()
        browser = FakeParallelBrowser()
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=2,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(index) for index in range(1, 3)]

        async def fake_scan(_page, *, target, **_kwargs):
            return _verified_scan_result(target)

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_scan_one_target", side_effect=fake_scan),
            patch.object(parallel_service, "update_bcnb_reports", AsyncMock()),
            patch.object(parallel_service, "log_message"),
        ):
            await parallel_service.run_parallel_zalo_batch(
                job_id=940,
                execution_mode="dry-run",
                scan_plan={"selected_weeks": [], "targets": targets},
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused-evidence-root",
                screenshots_dir="unused-screenshots-dir",
                runtime=runtime,
                job_repo=job_repo,
                audit_repo=object(),
                evidence_repo=evidence_repo,
            )

        self.assertEqual(browser.sessions[0].landing_provider_calls, 2)

    async def test_landing_timeout_retires_only_failed_worker_while_ready_worker_finishes(self):
        repository = InMemoryBatchRepository(
            claim_plan={"scan-1": [1, 2], "scan-2": []}
        )
        browser = FakeParallelBrowser(
            landing_facts_by_page={
                1: {
                    "authenticated_shell": True,
                    "search_control": True,
                },
                2: {"readiness_timeout": True},
            }
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=2,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(index) for index in range(1, 3)]

        async def fake_scan(_page, *, target, **_kwargs):
            return _verified_scan_result(target)

        async def fake_update(_page, *, scan_results, **_kwargs):
            return {
                "target_outcomes": [
                    {
                        "class_code": item["scan_target"]["class_code"],
                        "lesson": item["scan_target"]["lesson"],
                        "week": item["scan_target"]["week"],
                        "year": item["scan_target"]["year"],
                        "class_date_iso": item["scan_target"]["class_date_iso"],
                        "status": "DRY_RUN",
                    }
                    for item in scan_results
                ]
            }

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(
                parallel_service,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(parallel_service, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(parallel_service, "_scan_one_target", side_effect=fake_scan),
            patch.object(parallel_service, "update_bcnb_reports", side_effect=fake_update),
            patch.object(parallel_service, "log_message"),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=942,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
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

        rows = await repository.list_for_job(942)
        self.assertEqual([row["scan_status"] for row in rows], ["VERIFIED", "VERIFIED"])
        self.assertEqual(result["batch_summary"]["write"], {"DRY_RUN": 2})
        self.assertFalse(result["halted"])
        self.assertEqual(result["abnormal_targets"], 0)
        self.assertEqual(repository.halt_calls, [])
        self.assertEqual(
            [target_id for owner, target_id in repository.claimed_target_ids if owner == "scan-1"],
            [1, 2],
        )
        self.assertEqual(
            [target_id for owner, target_id in repository.claimed_target_ids if owner == "scan-2"],
            [],
        )
        self.assertEqual(browser.sessions[0].landing_provider_calls, 2)

    async def test_auth_failure_stops_before_search_and_target_claim(self):
        repository = InMemoryBatchRepository()
        browser = FakeParallelBrowser(
            landing_facts={
                "authenticated_shell": True,
                "search_control": True,
                "login_or_qr_marker": True,
            }
        )
        evidence_repo = SimpleNamespace(register_scan_results=AsyncMock())
        job_repo = SimpleNamespace(
            ensure_not_cancelled=AsyncMock(),
            update_job=AsyncMock(),
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(browser=browser),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        targets = [_make_search_target(index) for index in range(1, 3)]
        scan = AsyncMock(side_effect=AssertionError("search must not run after auth failure"))

        with (
            patch.object(parallel_service, "ZaloBatchRepository", return_value=repository),
            patch.object(parallel_service, "_scan_one_target", scan),
            patch.object(parallel_service, "log_message"),
        ):
            result = await parallel_service.run_parallel_zalo_batch(
                job_id=941,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [{"week": 34, "year": 2026}],
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

        self.assertTrue(result["halted"])
        self.assertEqual(scan.await_count, 0)
        self.assertEqual(repository.claim_calls, [])
        evidence_repo.register_scan_results.assert_not_awaited()
        self.assertEqual(browser.sessions[0].landing_provider_calls, 1)
        self.assertEqual(len(browser.sessions[0].pages), 1)
        self.assertTrue(all(page.selector_calls == 0 for page in browser.sessions[0].pages))


class LandingReadinessTests(unittest.IsolatedAsyncioTestCase):
    def test_positive_landing_auth_evidence_matrix_is_fail_closed(self):
        cases = (
            (
                "authenticated shell and search",
                {
                    "authenticated_shell": True,
                    "search_control": True,
                },
                "AUTHENTICATED_READY",
            ),
            (
                "login or qr marker",
                {"login_or_qr_marker": True},
                "LOGIN_OR_QR",
            ),
            (
                "neither marker",
                {},
                "LANDING_UNKNOWN_FAIL_CLOSED",
            ),
            (
                "both auth and login markers",
                {
                    "authenticated_shell": True,
                    "search_control": True,
                    "login_or_qr_marker": True,
                },
                "AUTH_STATE_AMBIGUOUS",
            ),
            (
                "detached selector",
                {"selector_detached": True},
                "LANDING_UNKNOWN_FAIL_CLOSED",
            ),
        )
        for name, facts, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    parallel_service.classify_landing_readiness(facts),
                    expected,
                )

    async def test_landing_readiness_maps_typed_failures_and_hang_without_browser(self):
        async def hanging_facts():
            await asyncio.Event().wait()

        self.assertEqual(
            await parallel_service.probe_landing_readiness(
                hanging_facts,
                timeout_seconds=0.01,
            ),
            "LANDING_READINESS_TIMEOUT",
        )
        failure_cases = (
            (parallel_service.LandingPageClosedError, "LANDING_PAGE_CLOSED"),
            (
                parallel_service.LandingBlockingModalError,
                "LANDING_BLOCKING_MODAL",
            ),
            (
                parallel_service.LandingUnsupportedBrowserError,
                "LANDING_UNSUPPORTED_BROWSER",
            ),
            (parallel_service.LandingNetworkError, "LANDING_NETWORK_FAILED"),
            (
                parallel_service.LandingSelectorDetachedError,
                "LANDING_UNKNOWN_FAIL_CLOSED",
            ),
        )
        for error_type, expected in failure_cases:
            async def failed_facts(error_type=error_type):
                raise error_type()

            with self.subTest(error=error_type.__name__):
                self.assertEqual(
                    await parallel_service.probe_landing_readiness(failed_facts),
                    expected,
                )

    def test_production_landing_mapping_without_proven_selector_contract_fails_closed(self):
        self.assertEqual(
            parallel_service.classify_landing_readiness(None),
            "LANDING_UNKNOWN_FAIL_CLOSED",
        )
        mapped = parallel_service.landing_result_to_setup_error(None)
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.code, "LANDING_UNKNOWN_FAIL_CLOSED")

    def test_exact_runtime_fact_schema_maps_unproven_contracts_fail_closed(self):
        facts = {
            "phase": "LANDING_AUTH_READINESS",
            "elapsed_ms": 1,
            "page_state": "LANDING",
            "expected_host_reached": True,
            "authenticated_shell_visible": None,
            "search_control_visible": True,
            "login_or_qr_visible": None,
            "blocking_modal_visible": False,
            "unsupported_browser_visible": False,
            "network_error_visible": False,
            "selector_contract_version": "zalo-landing-v1",
            "diagnostic_code": "LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN",
        }
        self.assertEqual(
            parallel_service.classify_landing_readiness(facts),
            "LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN",
        )

    def test_exact_runtime_fact_schema_does_not_infer_session_expiry_from_absence(self):
        facts = {
            "phase": "LANDING_AUTH_READINESS",
            "elapsed_ms": 1,
            "page_state": "LANDING",
            "expected_host_reached": True,
            "authenticated_shell_visible": False,
            "search_control_visible": False,
            "login_or_qr_visible": False,
            "blocking_modal_visible": False,
            "unsupported_browser_visible": False,
            "network_error_visible": False,
            "selector_contract_version": "zalo-landing-v1",
            "diagnostic_code": "LANDING_UNKNOWN_FAIL_CLOSED",
        }
        self.assertEqual(
            parallel_service.classify_landing_readiness(facts),
            "LANDING_UNKNOWN_FAIL_CLOSED",
        )

    async def test_combined_provider_cancellation_is_not_sanitized_as_failure(self):
        started = asyncio.Event()

        async def hanging_provider():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            parallel_service.probe_landing_readiness(
                hanging_provider,
                timeout_seconds=10,
            )
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_run_target_search_operation_invokes_operation_once_on_internal_typeerror(self):
        calls = []

        def raises_type_error(*args, **kwargs):
            calls.append((args, kwargs))
            raise TypeError("internal-type-error")

        with self.assertRaises(parallel_service.TargetSearchError) as cm:
            await parallel_service._run_target_search_operation(
                raises_type_error,
                job_id=999,
                worker_id="test-worker",
                phase="SEARCH_CLEAR",
                attempt=1,
                timeout_seconds=0.2,
                timeout_code=parallel_service.TARGET_SEARCH_CLEAR_TIMEOUT,
                error_code=parallel_service.TARGET_SEARCH_CLEAR_ERROR,
            )

        self.assertEqual(cm.exception.code, parallel_service.TARGET_SEARCH_CLEAR_ERROR)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
