from contextlib import asynccontextmanager
import unittest
from unittest.mock import AsyncMock, patch

from services.external_gateways import ExternalGateways
from services.runtime import WorkerRuntime
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

    async def get_all_ignored_groups(self, *, include_archived=False):
        return list(self.ignored)

    async def get_all_groups(self):
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
        self.assertEqual(plan["ignored_to_remove"], [old_group])
        self.assertEqual(plan["discovered_groups"][0]["CohortKey"], "VT-C4K|G|02")
        self.assertEqual(plan["discovered_groups"][0]["BuoiSo"], 1)
        self.assertEqual(plan["lifecycle_aliases"][0]["class_code"], "GB02")

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
                "_collect_bcnb_data",
                AsyncMock(
                    return_value=[
                        {"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "T2"},
                        {"ma_lop": "VT-C4K", "giao_vien": "B", "ngay_hoc": "T3"},
                    ]
                ),
            ),
            patch.object(
                service,
                "_collect_zalo_group_names",
                AsyncMock(return_value=["MindX - VT-JSB14", "MindX - VT-C4K"]),
            ),
        ):
            await service.run_auto_discovery(10, runtime=runtime)

        self.assertEqual(browser.enter_count, 1)
        self.assertEqual(browser.exit_count, 1)
        self.assertFalse(runtime.browser_lock.locked())
        self.assertEqual(len(self.group_repo.saved[0]), 2)
        self.assertEqual(self.group_repo.ignored_removed, ["MindX - VT-JSB14"])
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
                "_collect_bcnb_data",
                AsyncMock(return_value=[{"ma_lop": "VT-JSB14", "giao_vien": "A", "ngay_hoc": "T2"}]),
            ),
            patch.object(
                service,
                "_collect_zalo_group_names",
                AsyncMock(return_value=["MindX - VT-JSB14"]),
            ),
        ):
            await service.run_auto_discovery(21, runtime=runtime)
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
        self.assertEqual(self.job_repo.updates[-1]["message"], "Lỗi: Không thể ghi Database.")
        self.assertEqual(browser.exit_count, 1)


if __name__ == "__main__":
    unittest.main()
