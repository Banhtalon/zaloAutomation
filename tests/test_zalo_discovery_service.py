from contextlib import asynccontextmanager
from datetime import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.time_utils import BUSINESS_TIMEZONE
from services.external_gateways import ExternalGateways
from services.runtime import WorkerRuntime
from services.zalo_discovery_plan import _build_bcnb_finished_groups
import services.zalo_discovery_service as service


class FakeJobRepository:
    def __init__(self):
        self.updates = []
        self.cancel_checks = 0

    async def update_job(self, job_id, **changes):
        self.updates.append({"job_id": job_id, **changes})

    async def ensure_not_cancelled(self, _job_id):
        self.cancel_checks += 1


class FakeGroupRepository:
    def __init__(self, *, ignored=None, existing=None, save_error=None):
        self.ignored = list(ignored or [])
        self.existing = list(existing or [])
        self.save_error = save_error
        self.saved = []
        self.ignored_added = []
        self.ignored_removed = []
        self.applied_plans = []
        self.get_all_groups_calls = 0
        self.get_scan_groups_calls = 0

    async def get_all_ignored_groups(self, *, include_archived=False):
        return list(self.ignored)

    async def get_all_groups(self):
        self.get_all_groups_calls += 1
        return list(self.existing)

    async def get_scan_groups(self):
        self.get_scan_groups_calls += 1
        return list(self.existing)

    async def save_groups(self, groups):
        if self.save_error:
            raise self.save_error
        self.saved.append(groups)

    async def add_ignored_group(self, name):
        self.ignored_added.append(name)

    async def remove_ignored_group(self, name):
        self.ignored_removed.append(name)

    async def apply_discovery_snapshot(self, plan):
        if self.save_error:
            raise self.save_error
        self.applied_plans.append(plan)
        self.saved.append(plan["discovered_groups"])
        self.ignored_added.extend(plan["ignored_to_add"])
        self.ignored_removed.extend(plan["ignored_to_remove"])
        return {
            "snapshot_id": 12,
            "status": "OBSERVED_ONLY",
            "diff": {},
        }


class FakeBrowserGateway:
    def __init__(self, *, error=None):
        self.error = error
        self.page = object()
        self.enter_count = 0
        self.exit_count = 0

    @asynccontextmanager
    async def persistent_page(self, *, viewport=None):
        self.enter_count += 1
        if self.error:
            raise self.error
        try:
            yield self.page
        finally:
            self.exit_count += 1


class FakeClassStatusPage:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def evaluate(self, script, url):
        self.calls.append((script, url))
        return self.result


class ZaloDiscoveryServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_job_repo = service.job_repo
        self.original_group_repo = service.group_repo
        self.job_repo = FakeJobRepository()
        self.group_repo = FakeGroupRepository()
        service.job_repo = self.job_repo
        service.group_repo = self.group_repo

    def tearDown(self):
        service.job_repo = self.original_job_repo
        service.group_repo = self.original_group_repo

    def runtime(self, browser):
        return WorkerRuntime(
            external=ExternalGateways(
                browser=browser,
                lms_ocr=object(),
                student_ocr_factory=lambda: object(),
            )
        )

    def test_build_discovery_plan_matches_full_codes_and_tracks_ignored_groups(self):
        plan = service._build_discovery_plan(
            group_names=[
                "MindX - VT-JSB14 - Teacher A",
                "MindX - VT-C4K-SI24 - Teacher B",
                "MindX - UNKNOWN",
            ],
            bcnb_data=[
                {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "T2"},
                {"ma_lop": "VT-C4K-SI24", "giao_vien": "B", "ngay_hoc": "T3"},
            ],
            ignored_groups=["MindX - VT-JSB14 - Teacher A"],
            existing_groups=[],
        )

        self.assertTrue(plan["reliable"])
        self.assertEqual(len(plan["discovered_groups"]), 2)
        self.assertEqual(plan["ignored_to_add"], ["MindX - UNKNOWN"])
        self.assertEqual(
            plan["ignored_to_remove"],
            ["MindX - VT-JSB14 - Teacher A"],
        )

    def test_build_discovery_plan_fails_closed_for_empty_low_match_or_shrink(self):
        empty = service._build_discovery_plan([], [], [], [])
        self.assertFalse(empty["reliable"])

        low_match = service._build_discovery_plan(
            ["MindX - VT-JSB14", "MindX - UNKNOWN1", "MindX - UNKNOWN2"],
            [{"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "T2"}],
            [],
            [],
        )
        self.assertFalse(low_match["reliable"])

        shrink = service._build_discovery_plan(
            ["MindX - VT-JSB14"],
            [{"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "T2"}],
            [],
            [
                {"TenNhom": "old-1"},
                {"TenNhom": "old-2"},
                {"TenNhom": "old-3"},
                {"TenNhom": "old-4"},
            ],
        )
        self.assertFalse(shrink["reliable"])

    def test_half_snapshot_with_many_existing_groups_is_not_trusted(self):
        known = [f"MindX - VT-C4K-PTB{index:02d}" for index in range(1, 26)]
        unknown = [f"MindX - UNKNOWN{index:02d}" for index in range(1, 26)]
        plan = service._build_discovery_plan(
            group_names=known + unknown,
            bcnb_data=[
                {
                    "ma_lop": f"VT-C4K-PTB{index:02d}",
                    "giao_vien": "Teacher",
                    "ngay_hoc": "T2",
                }
                for index in range(1, 26)
            ],
            ignored_groups=[],
            existing_groups=[{"TenNhom": f"Existing {index:02d}"} for index in range(45)],
        )
        self.assertFalse(plan["reliable"])
        self.assertEqual(plan["source_count"], 50)
        self.assertEqual(plan["recognized_count"], 25)
        self.assertEqual(plan["expected_count"], 45)
        self.assertEqual(plan["delta_count"], -20)
        self.assertEqual(plan["coverage"], 0.5)
        self.assertEqual(len(plan["fingerprint_sha256"]), 64)

    def test_discovery_matches_unique_short_code_but_rejects_ambiguous_alias(self):
        pti09_group = "MindX - Lập trình App -PTI09 CN (14H-16H)"
        unique = service._build_discovery_plan(
            group_names=[pti09_group],
            bcnb_data=[
                {
                    "ma_lop": "VT-C4K-PTI09",
                    "giao_vien": "Teacher A",
                    "ngay_hoc": "12/07/2026",
                }
            ],
            ignored_groups=[pti09_group],
            existing_groups=[],
        )

        self.assertTrue(unique["reliable"])
        self.assertEqual(unique["discovered_groups"][0]["TenNhom"], pti09_group)
        self.assertEqual(unique["ignored_to_remove"], [pti09_group])

        ambiguous = service._build_discovery_plan(
            group_names=[pti09_group],
            bcnb_data=[
                {
                    "ma_lop": "VT-C4K-PTI09",
                    "giao_vien": "Teacher A",
                    "ngay_hoc": "12/07/2026",
                },
                {
                    "ma_lop": "HN-C4K-PTI09",
                    "giao_vien": "Teacher B",
                    "ngay_hoc": "12/07/2026",
                },
            ],
            ignored_groups=[],
            existing_groups=[],
        )

        self.assertFalse(ambiguous["reliable"])
        self.assertEqual(ambiguous["discovered_groups"], [])
        self.assertEqual(ambiguous["ignored_to_add"], [pti09_group])

    def test_finished_bcnb_status_auto_ends_only_exact_non_current_group(self):
        old_group = "MindX - Lập trình Scratch SI25 T7 (10H-12H)"
        current_group = "MindX - VT-C4K-SA26"
        plan = service._build_discovery_plan(
            group_names=[old_group, current_group],
            bcnb_data=[
                {
                    "ma_lop": "VT-C4K-SA26",
                    "giao_vien": "Teacher",
                    "ngay_hoc": "T7",
                }
            ],
            ignored_groups=[old_group],
            existing_groups=[],
            bcnb_class_states=[
                {"class_code": "VT-C4K-SI25", "class_status": "Finished"},
                {"class_code": "VT-C4K-SA26", "class_status": "Running"},
            ],
        )

        self.assertEqual(
            plan["bcnb_finished_groups"],
            [
                {
                    "group_name": old_group,
                    "class_code": "VT-C4K-SI25",
                    "cohort_key": "VT-C4K|S|25",
                    "bcnb_status": "FINISHED",
                    "evidence_source": "BCNB_STUDENT_CLASS_STATUS",
                }
            ],
        )
        self.assertEqual(
            plan["bcnb_finished_source"]["date_semantics"],
            "FIRST_FINISHED_OBSERVATION",
        )

    def test_finished_bcnb_status_fails_closed_for_ambiguous_or_conflicting_state(self):
        group = "MindX - Lập trình App - PTI09 CN"
        ambiguous = _build_bcnb_finished_groups(
            [group],
            [
                {"class_code": "VT-C4K-PTI09", "class_status": "Finished"},
                {"class_code": "HCM-C4K-PTI09", "class_status": "Running"},
            ],
        )
        conflicting = _build_bcnb_finished_groups(
            ["MindX - VT-C4K-SI25"],
            [
                {"class_code": "VT-C4K-SI25", "class_status": "Finished"},
                {"class_code": "VT-C4K-SI25", "class_status": "Running"},
            ],
        )

        self.assertEqual(ambiguous, [])
        self.assertEqual(conflicting, [])

    async def test_collect_class_states_returns_only_aggregate_identity_and_status(self):
        page = FakeClassStatusPage(
            {
                "body_size_bytes": 4096,
                "received_count": 58,
                "valid_row_count": 58,
                "total_count": 58,
                "rows": [
                    {"class_code": "VT-C4K-SI25", "class_status": "FINISHED"},
                    {"class_code": "VT-C4K-SA26", "class_status": "RUNNING"},
                ],
            }
        )

        result = await service._collect_bcnb_class_states(page, 33)

        self.assertEqual(
            result,
            [
                {"class_code": "VT-C4K-SI25", "class_status": "FINISHED"},
                {"class_code": "VT-C4K-SA26", "class_status": "RUNNING"},
            ],
        )
        self.assertEqual(self.job_repo.cancel_checks, 1)
        self.assertEqual(len(page.calls), 1)
        self.assertNotIn("student_name", str(result).lower())
        script, arguments = page.calls[0]
        self.assertIn("INVALID_CLASS_STATUS_ROW", script)
        self.assertNotIn("if (!code || !status) continue", script)
        self.assertEqual(arguments["maxCodeLength"], 100)
        self.assertEqual(arguments["maxStatusLength"], 50)

    async def test_collect_class_states_rejects_empty_payload(self):
        with self.assertRaises(service.DiscoverySafetyError):
            await service._collect_bcnb_class_states(
                FakeClassStatusPage(
                    {
                        "body_size_bytes": 10,
                        "received_count": 0,
                        "valid_row_count": 0,
                        "total_count": 0,
                        "rows": [],
                    }
                ),
                34,
            )

    async def test_collect_class_states_rejects_incomplete_page(self):
        with self.assertRaises(service.DiscoverySafetyError):
            await service._collect_bcnb_class_states(
                FakeClassStatusPage(
                    {
                        "body_size_bytes": 4096,
                        "received_count": 10,
                        "valid_row_count": 10,
                        "total_count": 58,
                        "rows": [
                            {
                                "class_code": "VT-C4K-SI25",
                                "class_status": "FINISHED",
                            }
                        ],
                    }
                ),
                35,
            )

    async def test_collect_class_states_rejects_mixed_valid_and_invalid_rows(self):
        with self.assertRaises(service.DiscoverySafetyError):
            await service._collect_bcnb_class_states(
                FakeClassStatusPage(
                    {
                        "body_size_bytes": 512,
                        "received_count": 2,
                        "valid_row_count": 1,
                        "total_count": 2,
                        "rows": [
                            {
                                "class_code": "VT-C4K-SI25",
                                "class_status": "FINISHED",
                            }
                        ],
                    }
                ),
                36,
            )

    async def test_collect_class_states_rejects_oversized_or_invalid_status(self):
        invalid_results = [
            {
                "body_size_bytes": 2_000_001,
                "received_count": 1,
                "valid_row_count": 1,
                "total_count": 1,
                "rows": [
                    {"class_code": "VT-C4K-SI25", "class_status": "FINISHED"}
                ],
            },
            {
                "body_size_bytes": 512,
                "received_count": 1,
                "valid_row_count": 1,
                "total_count": 1,
                "rows": [
                    {
                        "class_code": "VT-C4K-SI25",
                        "class_status": "X" * 51,
                    }
                ],
            },
            {
                "body_size_bytes": 512,
                "received_count": 1,
                "valid_row_count": 1,
                "total_count": 1,
                "rows": [
                    {"class_code": "", "class_status": "FINISHED"}
                ],
            },
        ]
        for result in invalid_results:
            with self.subTest(result=result):
                with self.assertRaises(service.DiscoverySafetyError):
                    await service._collect_bcnb_class_states(
                        FakeClassStatusPage(result),
                        37,
                    )

    async def test_collect_class_states_enforces_stream_limit_before_body_materialization(self):
        page = FakeClassStatusPage(
            {
                "body_size_bytes": 128,
                "received_count": 1,
                "valid_row_count": 1,
                "total_count": 1,
                "rows": [
                    {"class_code": "VT-C4K-SI25", "class_status": "FINISHED"}
                ],
            }
        )

        await service._collect_bcnb_class_states(page, 38)

        script, _ = page.calls[0]
        self.assertIn("response.body.getReader", script)
        self.assertIn("await reader.read()", script)
        self.assertIn("bodySizeBytes += value.byteLength", script)
        self.assertIn("await reader.cancel", script)
        self.assertNotIn("response.text()", script)
        counted = script.index("bodySizeBytes += value.byteLength")
        rejected = script.index("if (bodySizeBytes > maxBodyBytes)")
        materialized = script.index("new TextDecoder")
        self.assertLess(counted, rejected)
        self.assertLess(rejected, materialized)

    def test_progression_alias_is_not_mistaken_for_an_ended_class(self):
        old_group = "MindX - Lập trình Game - GB02 CN"
        current_group = "MindX - Lập trình Game - GA02 CN"
        plan = service._build_discovery_plan(
            group_names=[old_group, current_group],
            bcnb_data=[
                {
                    "ma_lop": "VT-C4K-GA02",
                    "giao_vien": "Teacher G",
                    "ngay_hoc": "T2",
                    "buoi_so": "1",
                }
            ],
            ignored_groups=[old_group],
            existing_groups=[{"TenNhom": old_group}],
        )

        self.assertTrue(plan["reliable"])
        self.assertEqual(plan["ignored_to_add"], [])
        self.assertEqual(plan["ignored_to_remove"], [])
        self.assertEqual(plan["discovered_groups"][0]["CohortKey"], "VT-C4K|G|02")
        self.assertEqual(plan["discovered_groups"][0]["BuoiSo"], 1)
        self.assertEqual(plan["lifecycle_aliases"][0]["class_code"], "GB02")

    def test_scan_plan_contains_exact_targets_for_both_bcnb_weeks(self):
        group = "MindX - VT-JSB14 - Teacher A"
        plan = service._build_discovery_plan(
            group_names=[group],
            bcnb_data=[
                {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "11/08/2026", "buoi_so": "12"}
            ],
            ignored_groups=[],
            existing_groups=[],
            bcnb_week_plan={
                "latest_week": {"week": 33, "year": 2026},
                "required_week": {"week": 32, "year": 2026},
                "selected_weeks": [
                    {"week": 32, "year": 2026},
                    {"week": 33, "year": 2026},
                ],
                "latest_data": [
                    {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "11/08/2026", "ngay_hoc_iso": "2026-08-11", "buoi_so": "12"},
                    {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "20/08/2026", "ngay_hoc_iso": "2026-08-20", "buoi_so": "13"},
                ],
                "required_data": [
                    {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "04/08/2026", "buoi_so": "11"}
                ],
            },
            now=datetime(2026, 8, 15, 9, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(
            {(target["week"], target["year"], target["lesson"]) for target in plan["scan_targets"]},
            {(32, 2026, "11"), (33, 2026, "12")},
        )
        target = next(item for item in plan["scan_targets"] if item["lesson"] == "12")
        self.assertEqual(target["class_date_iso"], "2026-08-11")
        self.assertEqual(target["week_source"], "BCNB_SERVER")

    def test_bcnb_zalo_coverage_reports_missing_ambiguous_and_transition(self):
        week_plan = {
            "latest_week": {"week": 33, "year": 2026},
            "required_week": {"week": 32, "year": 2026},
            "selected_weeks": [
                {"week": 32, "year": 2026},
                {"week": 33, "year": 2026},
            ],
            "latest_data": [
                {"ma_lop": "VT-C4K-GA01", "giao_vien": "A", "ngay_hoc": "14/08/2026", "ngay_hoc_iso": "2026-08-14", "buoi_so": "8"},
                {"ma_lop": "VT-C4K-GA02", "giao_vien": "B", "ngay_hoc": "14/08/2026", "ngay_hoc_iso": "2026-08-14", "buoi_so": "8"},
                {"ma_lop": "VT-C4K-PTI09", "giao_vien": "C", "ngay_hoc": "15/08/2026", "ngay_hoc_iso": "2026-08-15", "buoi_so": "8"},
                {"ma_lop": "HCM-C4K-PTI09", "giao_vien": "D", "ngay_hoc": "15/08/2026", "ngay_hoc_iso": "2026-08-15", "buoi_so": "8"},
                {"ma_lop": "VT-C4K-SA03", "giao_vien": "E", "ngay_hoc": "16/08/2026", "ngay_hoc_iso": "2026-08-16", "buoi_so": "8"},
            ],
            "required_data": [
                {"ma_lop": "VT-C4K-GA01", "giao_vien": "A", "ngay_hoc": "07/08/2026", "ngay_hoc_iso": "2026-08-07", "buoi_so": "7"},
            ],
        }
        plan = service._build_discovery_plan(
            group_names=[
                "MindX - VT-C4K-GA01",
                "MindX - VT-C4K-GB02",
                "MindX - PTI09",
            ],
            bcnb_data=week_plan["latest_data"],
            ignored_groups=[],
            existing_groups=[],
            bcnb_week_plan=week_plan,
            now=datetime(2026, 8, 17, 9, 0, tzinfo=BUSINESS_TIMEZONE),
        )

        coverage = plan["bcnb_zalo_coverage"]
        by_code = {item["class_code"]: item for item in coverage["records"]}
        self.assertEqual(plan["plan_version"], 4)
        self.assertEqual(by_code["VT-C4K-GA01"]["status"], "MATCHED")
        self.assertEqual(len(by_code["VT-C4K-GA01"]["periods"]), 2)
        self.assertEqual(by_code["VT-C4K-GA02"]["status"], "TRANSITION_REVIEW")
        self.assertEqual(by_code["VT-C4K-PTI09"]["status"], "AMBIGUOUS_ZALO_GROUP")
        self.assertEqual(by_code["HCM-C4K-PTI09"]["status"], "AMBIGUOUS_ZALO_GROUP")
        self.assertEqual(by_code["VT-C4K-SA03"]["status"], "MISSING_ZALO_GROUP")
        self.assertEqual(coverage["matched_count"], 1)
        self.assertEqual(coverage["missing_count"], 1)
        self.assertEqual(coverage["ambiguous_count"], 2)
        self.assertEqual(coverage["transition_review_count"], 1)
        self.assertEqual(
            {target["class_code"] for target in plan["scan_targets"]},
            {"VT-C4K-GA01"},
        )

    def test_scan_plan_defers_same_day_class_until_its_end_time(self):
        group = "MindX -LEGO-VT-KIND-ROB4I01 T6 18H-19H30"
        week_plan = {
            "latest_week": {"week": 33, "year": 2026},
            "required_week": {"week": 32, "year": 2026},
            "selected_weeks": [
                {"week": 32, "year": 2026},
                {"week": 33, "year": 2026},
            ],
            "latest_data": [
                {
                    "ma_lop": "VT-KIND-ROB4I01",
                    "giao_vien": "Lê Thị Dương",
                    "ngay_hoc": "14/08/2026",
                    "ngay_hoc_iso": "2026-08-14",
                    "buoi_so": "2",
                }
            ],
            "required_data": [
                {
                    "ma_lop": "VT-KIND-ROB4I01",
                    "giao_vien": "Lê Thị Dương",
                    "ngay_hoc": "07/08/2026",
                    "ngay_hoc_iso": "2026-08-07",
                    "buoi_so": "1",
                }
            ],
        }
        plan = service._build_discovery_plan(
            group_names=[group],
            bcnb_data=week_plan["latest_data"],
            ignored_groups=[],
            existing_groups=[],
            bcnb_week_plan=week_plan,
            now=datetime(2026, 8, 14, 16, 5, tzinfo=BUSINESS_TIMEZONE),
        )

        self.assertEqual(
            {(target["week"], target["lesson"]) for target in plan["scan_targets"]},
            {(32, "1")},
        )
        self.assertEqual(
            {(target["week"], target["lesson"]) for target in plan["deferred_targets"]},
            {(33, "2")},
        )

    async def test_success_uses_gateway_persists_plan_and_releases_lock(self):
        browser = FakeBrowserGateway()
        runtime = self.runtime(browser)
        self.group_repo = FakeGroupRepository(
            ignored=["MindX - VT-JSB14"],
            existing=[],
        )
        service.group_repo = self.group_repo
        with (
            patch.object(
                service,
                "_collect_bcnb_week_plan",
                AsyncMock(
                    return_value={
                        "latest_week": {"week": 33, "year": 2026},
                        "required_week": {"week": 32, "year": 2026},
                        "selected_weeks": [
                            {"week": 32, "year": 2026},
                            {"week": 33, "year": 2026},
                        ],
                        "latest_data": [
                            {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "T2", "buoi_so": "12"},
                            {"ma_lop": "VT-C4K", "giao_vien": "B", "ngay_hoc": "T3", "buoi_so": "12"},
                        ],
                        "required_data": [
                            {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "T2", "buoi_so": "11"},
                            {"ma_lop": "VT-C4K", "giao_vien": "B", "ngay_hoc": "T3", "buoi_so": "11"},
                        ],
                    }
                ),
            ),
            patch.object(
                service,
                "_collect_zalo_group_names",
                AsyncMock(return_value=["MindX - VT-JSB14", "MindX - VT-C4K"]),
            ),
            patch.object(
                service,
                "_collect_bcnb_class_states",
                AsyncMock(
                    return_value=[
                        {"class_code": "VT-JSB14", "class_status": "RUNNING"},
                        {"class_code": "VT-C4K", "class_status": "RUNNING"},
                    ]
                ),
            ),
        ):
            await service.run_auto_discovery(10, runtime=runtime)

        self.assertEqual(browser.enter_count, 1)
        self.assertEqual(browser.exit_count, 1)
        self.assertFalse(runtime.browser_lock.locked())
        self.assertEqual(len(self.group_repo.saved[0]), 2)
        self.assertEqual(self.group_repo.ignored_removed, ["MindX - VT-JSB14"])
        self.assertEqual(self.group_repo.get_scan_groups_calls, 1)
        self.assertEqual(self.group_repo.get_all_groups_calls, 0)
        self.assertEqual(self.job_repo.updates[-1]["status"], "SUCCESS")

    async def test_launch_and_persistence_failures_release_resources(self):
        launch_browser = FakeBrowserGateway(error=RuntimeError("launch failed"))
        launch_runtime = self.runtime(launch_browser)
        with self.assertRaisesRegex(RuntimeError, "launch failed"):
            await service.run_auto_discovery(20, runtime=launch_runtime)
        self.assertFalse(launch_runtime.browser_lock.locked())

        browser = FakeBrowserGateway()
        runtime = self.runtime(browser)
        self.group_repo = FakeGroupRepository(save_error=RuntimeError("db secret"))
        service.group_repo = self.group_repo
        with (
            patch.object(
                service,
                "_collect_bcnb_week_plan",
                AsyncMock(
                    return_value={
                        "latest_week": {"week": 33, "year": 2026},
                        "required_week": {"week": 32, "year": 2026},
                        "selected_weeks": [
                            {"week": 32, "year": 2026},
                            {"week": 33, "year": 2026},
                        ],
                        "latest_data": [
                            {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "T2", "buoi_so": "12"}
                        ],
                        "required_data": [
                            {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "T2", "buoi_so": "11"}
                        ],
                    }
                ),
            ),
            patch.object(
                service,
                "_collect_zalo_group_names",
                AsyncMock(return_value=["MindX - VT-JSB14"]),
            ),
            patch.object(
                service,
                "_collect_bcnb_class_states",
                AsyncMock(
                    return_value=[
                        {"class_code": "VT-JSB14", "class_status": "RUNNING"}
                    ]
                ),
            ),
        ):
            await service.run_auto_discovery(21, runtime=runtime)
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
        self.assertEqual(self.job_repo.updates[-1]["message"], "Lỗi: Không thể ghi Database.")
        self.assertEqual(browser.exit_count, 1)

    async def test_login_bcnb_if_needed_noop_when_not_login_page(self):
        page = MagicMock()
        username_locator = AsyncMock()
        username_locator.count.return_value = 0
        page.locator.return_value.first = username_locator

        await service._login_bcnb_if_needed(page)
        username_locator.fill.assert_not_called()

    async def test_login_bcnb_if_needed_success(self):
        page = MagicMock()
        page.url = "https://teachingmindx.top/check_nhanxet.php"
        page.wait_for_url = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        username_locator = AsyncMock()
        username_locator.count.return_value = 1
        username_locator.is_visible.return_value = True

        password_locator = AsyncMock()
        password_locator.count.return_value = 1

        submit_locator = AsyncMock()
        submit_locator.count.return_value = 1

        remember_locator = AsyncMock()
        remember_locator.count.return_value = 1
        remember_locator.is_checked.return_value = False

        def locator_side_effect(selector):
            mock = MagicMock()
            if "username" in selector:
                mock.first = username_locator
            elif "password" in selector:
                mock.first = password_locator
            elif "submit" in selector:
                mock.first = submit_locator
            elif "remember" in selector:
                mock.first = remember_locator
            else:
                mock.first = AsyncMock()
            return mock

        page.locator.side_effect = locator_side_effect

        await service._login_bcnb_if_needed(page)
        username_locator.fill.assert_called_once()
        password_locator.fill.assert_called_once()
        remember_locator.check.assert_called_once()
        submit_locator.click.assert_called_once()
        page.wait_for_url.assert_called_once()

    async def test_login_bcnb_if_needed_fails_when_still_on_login_page(self):
        page = MagicMock()
        page.url = "https://teachingmindx.top/login.php"
        page.wait_for_url = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        username_locator = AsyncMock()
        username_locator.count.return_value = 1
        username_locator.is_visible.return_value = True

        password_locator = AsyncMock()
        password_locator.count.return_value = 1

        submit_locator = AsyncMock()
        submit_locator.count.return_value = 1

        remember_locator = AsyncMock()
        remember_locator.count.return_value = 0

        def locator_side_effect(selector):
            mock = MagicMock()
            if "username" in selector:
                mock.first = username_locator
            elif "password" in selector:
                mock.first = password_locator
            elif "submit" in selector:
                mock.first = submit_locator
            elif "remember" in selector:
                mock.first = remember_locator
            else:
                mock.first = AsyncMock()
            return mock

        page.locator.side_effect = locator_side_effect

        with self.assertRaises(service.DiscoverySafetyError):
            await service._login_bcnb_if_needed(page)


if __name__ == "__main__":
    unittest.main()

