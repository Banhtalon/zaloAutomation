import os
from datetime import datetime, timezone
import sqlite3
import tempfile
import unittest

import db.database as database
from db.database import get_db_connection, init_db
from repositories.group_repository import DiscoverySnapshotError, GroupRepository
from repositories.report_repository import ReportRepository


class BasicRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "repositories.db")
        init_db()
        async with get_db_connection() as db:
            await db.execute("DELETE FROM form_mappings")
            await db.commit()
        self.groups = GroupRepository()
        self.reports = ReportRepository()

    async def asyncTearDown(self):
        database.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    async def test_group_crud_duplicate_and_transaction_rollback(self):
        self.assertEqual(await self.groups.get_all_groups(), [])

        await self.groups.add_group("MindX - B", "Teacher B", "Thứ 3")
        await self.groups.add_group("MindX - A", "Teacher A", "Thứ 2")
        await self.groups.add_group("MindX - A", "Ignored duplicate", "")
        self.assertEqual(
            await self.groups.get_all_groups(),
            [
                {
                    "TenNhom": "MindX - A",
                    "NguoiPhuTrach": "Teacher A",
                    "NgayHoc": "Thứ 2",
                },
                {
                    "TenNhom": "MindX - B",
                    "NguoiPhuTrach": "Teacher B",
                    "NgayHoc": "Thứ 3",
                },
            ],
        )

        await self.groups.delete_group("MindX - B")
        await self.groups.save_groups(
            [
                {"TenNhom": "MindX - C"},
                {
                    "TenNhom": "MindX - D",
                    "NguoiPhuTrach": "Teacher D",
                    "NgayHoc": "Chủ nhật",
                },
            ]
        )
        before = await self.groups.get_all_groups()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.groups.save_groups(
                [{"TenNhom": "Duplicate"}, {"TenNhom": "Duplicate"}]
            )
        self.assertEqual(await self.groups.get_all_groups(), before)

    async def test_ignored_group_requires_confirmation_before_retention_archive(self):
        await self.groups.add_ignored_group("Old group")
        await self.groups.add_ignored_group("Current group")
        await self.groups.add_ignored_group("Current group")

        await self.groups.archive_confirmed_ended_groups(
            days=14,
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(
            await self.groups.get_all_ignored_groups(),
            ["Current group", "Old group"],
        )
        await self.groups.confirm_ignored_group_ended(
            "Old group",
            ended_at="2026-07-01 00:00:00",
        )
        archived = await self.groups.archive_confirmed_ended_groups(
            days=14,
            now=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(archived, 1)
        self.assertEqual(await self.groups.get_all_ignored_groups(), ["Current group"])
        self.assertEqual(
            await self.groups.get_all_ignored_groups(include_archived=True),
            ["Current group", "Old group"],
        )
        self.assertFalse(
            await self.groups.confirm_ignored_group_ended(
                "Old group",
                ended_at="2026-07-20T00:00:00+00:00",
            )
        )
        archived_record = next(
            item
            for item in await self.groups.get_ignored_group_records(
                include_archived=True
            )
            if item["group_name"] == "Old group"
        )
        self.assertEqual(archived_record["status"], "ARCHIVED")
        self.assertEqual(archived_record["ended_at"], "2026-06-30T17:00:00+00:00")
        await self.groups.remove_ignored_group("Current group")
        self.assertEqual(await self.groups.get_all_ignored_groups(), [])

    async def test_bcnb_finished_starts_once_and_archives_after_seven_days(self):
        active_group = "MindX - VT-C4K-SA26"
        second_active_group = "MindX - VT-C4K-SA27"
        ended_group = "MindX - Lập trình Scratch SI25 T7 (10H-12H)"
        plan = {
            "reliable": True,
            "source_group_names": [
                active_group,
                second_active_group,
                ended_group,
            ],
            "discovered_groups": [
                {
                    "TenNhom": active_group,
                    "MaLop": "VT-C4K-SA26",
                    "CohortKey": "VT-C4K|S|26",
                },
                {
                    "TenNhom": second_active_group,
                    "MaLop": "VT-C4K-SA27",
                    "CohortKey": "VT-C4K|S|27",
                },
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [],
            "lifecycle_aliases": [],
            "bcnb_finished_groups": [
                {
                    "group_name": ended_group,
                    "class_code": "VT-C4K-SI25",
                    "cohort_key": "VT-C4K|S|25",
                    "bcnb_status": "FINISHED",
                    "evidence_source": "BCNB_STUDENT_CLASS_STATUS",
                }
            ],
        }

        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO class_cohorts (
                    cohort_key, center_code, track, cohort_number,
                    current_class_code, current_group_name, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    "VT-C4K|S|25",
                    "VT-C4K",
                    "S",
                    "25",
                    "VT-C4K-SI25",
                    ended_group,
                    "2026-07-31T10:00:00+00:00",
                ),
            )
            await db.commit()

        first = await self.groups.apply_discovery_snapshot(
            plan,
            observed_at="2026-08-01T10:00:00+00:00",
        )
        second = await self.groups.apply_discovery_snapshot(
            plan,
            observed_at="2026-08-03T10:00:00+00:00",
        )
        records = await self.groups.get_ignored_group_records()

        self.assertEqual(first["auto_finished_count"], 1)
        self.assertEqual(second["auto_finished_count"], 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "ENDED_CONFIRMED")
        self.assertEqual(records[0]["ended_at"], "2026-08-01T10:00:00+00:00")
        lifecycle = next(
            item
            for item in await self.groups.get_class_lifecycles()
            if item["cohort_key"] == "VT-C4K|S|25"
        )
        self.assertEqual(lifecycle["status"], "ENDED_CONFIRMED")
        self.assertEqual(lifecycle["ended_at"], "2026-08-01T10:00:00+00:00")
        self.assertEqual(
            await self.groups.archive_confirmed_ended_groups(
                now=datetime(2026, 8, 8, 9, 59, tzinfo=timezone.utc)
            ),
            0,
        )
        self.assertEqual(
            await self.groups.archive_confirmed_ended_groups(
                now=datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)
            ),
            1,
        )
        self.assertEqual(await self.groups.get_all_ignored_groups(), [])
        archived = await self.groups.get_ignored_group_records(include_archived=True)
        self.assertEqual(archived[0]["status"], "ARCHIVED")

        reappeared_plan = {
            "reliable": True,
            "source_group_names": [active_group, ended_group],
            "discovered_groups": [
                {
                    "TenNhom": active_group,
                    "MaLop": "VT-C4K-SA26",
                    "CohortKey": "VT-C4K|S|26",
                },
                {
                    "TenNhom": ended_group,
                    "MaLop": "VT-C4K-SI25",
                    "CohortKey": "VT-C4K|S|25",
                },
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [ended_group],
            "lifecycle_records": [
                {
                    "cohort_key": "VT-C4K|S|25",
                    "center_code": "VT-C4K",
                    "track": "S",
                    "cohort_number": "25",
                    "class_code": "VT-C4K-SI25",
                    "group_name": ended_group,
                    "lesson_number": 1,
                }
            ],
            "lifecycle_aliases": [],
            "bcnb_finished_groups": [],
        }
        await self.groups.apply_discovery_snapshot(
            reappeared_plan,
            observed_at="2026-08-09T10:00:00+00:00",
        )
        self.assertNotIn(
            ended_group,
            await self.groups.get_all_ignored_groups(include_archived=True),
        )
        self.assertIn(
            ended_group,
            [item["TenNhom"] for item in await self.groups.get_scan_groups()],
        )

    async def test_bcnb_finished_repository_defense_skips_current_or_archived_group(self):
        group_name = "MindX - VT-C4K-SI25"
        item = {
            "group_name": group_name,
            "class_code": "VT-C4K-SI25",
            "cohort_key": "VT-C4K|S|25",
            "bcnb_status": "FINISHED",
            "evidence_source": "BCNB_STUDENT_CLASS_STATUS",
        }
        current_plan = {
            "reliable": True,
            "source_group_names": [group_name],
            "discovered_groups": [{"TenNhom": group_name}],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [],
            "lifecycle_aliases": [],
            "bcnb_finished_groups": [item],
        }
        result = await self.groups.apply_discovery_snapshot(
            current_plan,
            observed_at="2026-08-01 10:00:00",
        )
        self.assertEqual(result["auto_finished_count"], 0)
        self.assertEqual(await self.groups.get_all_ignored_groups(), [])

        await self.groups.add_ignored_group(group_name)
        await self.groups.confirm_ignored_group_ended(
            group_name,
            ended_at="2026-07-01 00:00:00",
        )
        await self.groups.archive_confirmed_ended_groups(
            now=datetime(2026, 8, 1, tzinfo=timezone.utc)
        )
        absent_plan = {
            **current_plan,
            "source_group_names": [group_name, "MindX - A", "MindX - B"],
            "discovered_groups": [
                {"TenNhom": "MindX - A"},
                {"TenNhom": "MindX - B"},
            ],
        }
        result = await self.groups.apply_discovery_snapshot(
            absent_plan,
            observed_at="2026-08-02 10:00:00",
        )
        self.assertEqual(result["auto_finished_count"], 0)
        archived = await self.groups.get_ignored_group_records(include_archived=True)
        self.assertEqual(archived[0]["status"], "ARCHIVED")

    async def test_bcnb_finished_same_class_different_group_skips_all_mutation(self):
        ended_group = "MindX - Scratch SI25"
        active_group = "MindX - Scratch successor S25"
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO class_cohorts (
                    cohort_key, center_code, track, cohort_number,
                    current_class_code, current_group_name, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    "VT-C4K|S|25",
                    "VT-C4K",
                    "S",
                    "25",
                    "VT-C4K-SI25",
                    active_group,
                    "2026-08-01T09:00:00+00:00",
                ),
            )
            await db.commit()
        plan = {
            "reliable": True,
            "source_group_names": [ended_group, "MindX - A", "MindX - B"],
            "discovered_groups": [
                {"TenNhom": "MindX - A"},
                {"TenNhom": "MindX - B"},
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [],
            "lifecycle_aliases": [],
            "bcnb_finished_groups": [
                {
                    "group_name": ended_group,
                    "class_code": "VT-C4K-SI25",
                    "cohort_key": "VT-C4K|S|25",
                    "bcnb_status": "FINISHED",
                    "evidence_source": "BCNB_STUDENT_CLASS_STATUS",
                }
            ],
        }

        await self.groups.apply_discovery_snapshot(
            plan,
            observed_at="2026-08-01T10:00:00+00:00",
        )

        lifecycle = (await self.groups.get_class_lifecycles())[0]
        self.assertEqual(lifecycle["status"], "ACTIVE")
        self.assertEqual(lifecycle["class_code"], "VT-C4K-SI25")
        self.assertEqual(lifecycle["group_name"], active_group)
        self.assertEqual(
            await self.groups.get_all_ignored_groups(include_archived=True), []
        )

    async def test_bcnb_finished_same_group_different_class_skips_all_mutation(self):
        group_name = "MindX - Scratch SI25"
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO class_cohorts (
                    cohort_key, center_code, track, cohort_number,
                    current_class_code, current_group_name, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    "VT-C4K|S|25",
                    "VT-C4K",
                    "S",
                    "25",
                    "VT-C4K-SA25",
                    group_name,
                    "2026-08-01T09:00:00+00:00",
                ),
            )
            await db.commit()
        plan = {
            "reliable": True,
            "source_group_names": [group_name, "MindX - A", "MindX - B"],
            "discovered_groups": [
                {"TenNhom": "MindX - A"},
                {"TenNhom": "MindX - B"},
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [],
            "lifecycle_aliases": [],
            "bcnb_finished_groups": [
                {
                    "group_name": group_name,
                    "class_code": "VT-C4K-SI25",
                    "cohort_key": "VT-C4K|S|25",
                    "bcnb_status": "FINISHED",
                    "evidence_source": "BCNB_STUDENT_CLASS_STATUS",
                }
            ],
        }

        await self.groups.apply_discovery_snapshot(
            plan,
            observed_at="2026-08-01T10:00:00+00:00",
        )

        lifecycle = (await self.groups.get_class_lifecycles())[0]
        self.assertEqual(lifecycle["status"], "ACTIVE")
        self.assertEqual(lifecycle["class_code"], "VT-C4K-SA25")
        self.assertEqual(lifecycle["group_name"], group_name)
        self.assertEqual(
            await self.groups.get_all_ignored_groups(include_archived=True), []
        )

    async def test_bcnb_finished_transaction_rolls_back_ignored_and_cohort_together(self):
        group_name = "MindX - Scratch SI25"
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO class_cohorts (
                    cohort_key, center_code, track, cohort_number,
                    current_class_code, current_group_name, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    "VT-C4K|S|25",
                    "VT-C4K",
                    "S",
                    "25",
                    "VT-C4K-SI25",
                    group_name,
                    "2026-08-01T09:00:00+00:00",
                ),
            )
            await db.commit()
        valid_item = {
            "group_name": group_name,
            "class_code": "VT-C4K-SI25",
            "cohort_key": "VT-C4K|S|25",
            "bcnb_status": "FINISHED",
            "evidence_source": "BCNB_STUDENT_CLASS_STATUS",
        }
        invalid_item = {
            "group_name": "MindX - Invalid",
            "class_code": "VT-C4K-SI99",
            "cohort_key": "VT-C4K|S|99",
            "bcnb_status": "FINISHED",
            "evidence_source": "INVALID_SOURCE",
        }
        plan = {
            "reliable": True,
            "source_group_names": [group_name, "MindX - A", "MindX - B"],
            "discovered_groups": [
                {"TenNhom": "MindX - A"},
                {"TenNhom": "MindX - B"},
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [],
            "lifecycle_aliases": [],
            "bcnb_finished_groups": [valid_item, invalid_item],
        }

        with self.assertRaises(DiscoverySnapshotError):
            await self.groups.apply_discovery_snapshot(
                plan,
                observed_at="2026-08-01T10:00:00+00:00",
            )

        self.assertEqual(
            await self.groups.get_all_ignored_groups(include_archived=True), []
        )
        lifecycle = (await self.groups.get_class_lifecycles())[0]
        self.assertEqual(lifecycle["status"], "ACTIVE")
        self.assertIsNone(lifecycle["ended_at"])

    async def test_scan_groups_excludes_transitioning_groups_kept_for_history(self):
        await self.groups.add_group("Active group", "Teacher A", "02/08/2026")
        await self.groups.add_group("Transition group", "Teacher B", "02/08/2026")
        await self.groups.add_ignored_group("Transition group")

        self.assertEqual(
            [item["TenNhom"] for item in await self.groups.get_scan_groups()],
            ["Active group"],
        )
        self.assertEqual(
            [item["TenNhom"] for item in await self.groups.get_all_groups()],
            ["Active group", "Transition group"],
        )

    async def test_manual_restore_reactivates_exact_ended_cohort(self):
        group_name = "MindX - VT-C4K-SI25"
        await self.groups.save_groups(
            [
                {
                    "TenNhom": group_name,
                    "MaLop": "VT-C4K-SI25",
                    "CohortKey": "VT-C4K|S|25",
                }
            ]
        )
        await self.groups.add_ignored_group(
            group_name,
            class_code="VT-C4K-SI25",
            cohort_key="VT-C4K|S|25",
        )
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO class_cohorts (
                    cohort_key, center_code, track, cohort_number,
                    current_class_code, current_group_name, status, ended_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ENDED_CONFIRMED', ?, ?)
                """,
                (
                    "VT-C4K|S|25",
                    "VT-C4K",
                    "S",
                    "25",
                    "VT-C4K-SI25",
                    group_name,
                    "2026-08-01T00:00:00+00:00",
                    "2026-08-01T00:00:00+00:00",
                ),
            )
            await db.commit()

        self.assertTrue(await self.groups.restore_ignored_group(group_name))
        self.assertEqual(await self.groups.get_all_ignored_groups(), [])
        self.assertEqual(
            [item["TenNhom"] for item in await self.groups.get_scan_groups()],
            [group_name],
        )
        lifecycle = (await self.groups.get_class_lifecycles())[0]
        self.assertEqual(lifecycle["status"], "ACTIVE")
        self.assertIsNone(lifecycle["ended_at"])

    async def test_manual_restore_partial_identity_keeps_database_unchanged(self):
        cases = [
            {
                "group_name": "MindX - Restore SI31",
                "class_code": "VT-C4K-SI31",
                "cohort_key": "VT-C4K|S|31",
                "current_group_name": "MindX - Different SI31",
                "current_class_code": "VT-C4K-SI31",
            },
            {
                "group_name": "MindX - Restore SI32",
                "class_code": "VT-C4K-SI32",
                "cohort_key": "VT-C4K|S|32",
                "current_group_name": "MindX - Restore SI32",
                "current_class_code": "VT-C4K-SA32",
            },
        ]
        for case in cases:
            await self.groups.add_ignored_group(
                case["group_name"],
                class_code=case["class_code"],
                cohort_key=case["cohort_key"],
            )
            async with get_db_connection() as db:
                await db.execute(
                    """
                    INSERT INTO class_cohorts (
                        cohort_key, center_code, track, cohort_number,
                        current_class_code, current_group_name, status,
                        ended_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ENDED_CONFIRMED', ?, ?)
                    """,
                    (
                        case["cohort_key"],
                        "VT-C4K",
                        "S",
                        case["cohort_key"].rsplit("|", 1)[-1],
                        case["current_class_code"],
                        case["current_group_name"],
                        "2026-08-01T00:00:00+00:00",
                        "2026-08-01T00:00:00+00:00",
                    ),
                )
                await db.commit()

        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(
                    await self.groups.restore_ignored_group(case["group_name"])
                )

        records = await self.groups.get_ignored_group_records(include_archived=True)
        self.assertEqual(
            {item["group_name"] for item in records},
            {case["group_name"] for case in cases},
        )
        self.assertEqual(await self.groups.get_all_groups(), [])
        self.assertEqual(await self.groups.get_scan_groups(), [])
        lifecycle_by_key = {
            item["cohort_key"]: item for item in await self.groups.get_class_lifecycles()
        }
        for case in cases:
            lifecycle = lifecycle_by_key[case["cohort_key"]]
            self.assertEqual(lifecycle["status"], "ENDED_CONFIRMED")
            self.assertEqual(lifecycle["group_name"], case["current_group_name"])
            self.assertEqual(lifecycle["class_code"], case["current_class_code"])

    async def test_scan_groups_excludes_non_active_cohort_without_ignored_name(self):
        await self.groups.save_groups(
            [
                {
                    "TenNhom": "Paused cohort",
                    "NguoiPhuTrach": "Teacher P",
                    "NgayHoc": "02/08/2026",
                    "MaLop": "VT-C4K-PTB01",
                    "CohortKey": "VT-C4K|PT|01",
                    "BuoiSo": 14,
                }
            ]
        )
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO class_cohorts (
                    cohort_key, center_code, track, cohort_number,
                    current_class_code, current_group_name,
                    current_lesson_number, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "VT-C4K|PT|01",
                    "VT-C4K",
                    "PT",
                    "01",
                    "VT-C4K-PTB01",
                    "Paused cohort",
                    14,
                    "PAUSED_OR_TRANSITIONING",
                    "2026-08-13T00:00:00+00:00",
                ),
            )
            await db.commit()

        self.assertEqual(await self.groups.get_scan_groups(), [])

    async def test_discovery_snapshot_tracks_transition_and_reappearance(self):
        base_plan = {
            "reliable": True,
            "source_group_names": ["MindX - PTB06"],
            "discovered_groups": [
                {
                    "TenNhom": "MindX - PTB06",
                    "NguoiPhuTrach": "Teacher",
                    "NgayHoc": "T2",
                    "MaLop": "VT-C4K-PTB06",
                    "CohortKey": "VT-C4K|PT|06",
                    "BuoiSo": 14,
                }
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [
                {
                    "cohort_key": "VT-C4K|PT|06",
                    "center_code": "VT-C4K",
                    "track": "PT",
                    "cohort_number": "06",
                    "class_code": "VT-C4K-PTB06",
                    "group_name": "MindX - PTB06",
                    "lesson_number": 14,
                }
            ],
            "lifecycle_aliases": [],
        }
        preview = await self.groups.preview_discovery_snapshot(base_plan)
        self.assertFalse(preview["would_promote"])
        self.assertEqual(preview["diff"]["added_group_names"], ["MindX - PTB06"])
        await self.groups.apply_discovery_snapshot(
            base_plan,
            observed_at="2026-06-01 00:00:00",
        )

        await self.groups.add_ignored_group("MindX - PTB06")

        next_level = {
            "reliable": True,
            "source_group_names": ["MindX - PTA06"],
            "discovered_groups": [
                {
                    "TenNhom": "MindX - PTA06",
                    "NguoiPhuTrach": "Teacher",
                    "NgayHoc": "T2",
                    "MaLop": "VT-C4K-PTA06",
                    "CohortKey": "VT-C4K|PT|06",
                    "BuoiSo": 1,
                }
            ],
            "ignored_to_add": [],
            "ignored_to_remove": ["MindX - PTB06"],
            "lifecycle_records": [
                {
                    "cohort_key": "VT-C4K|PT|06",
                    "center_code": "VT-C4K",
                    "track": "PT",
                    "cohort_number": "06",
                    "class_code": "VT-C4K-PTA06",
                    "group_name": "MindX - PTA06",
                    "lesson_number": 1,
                }
            ],
            "lifecycle_aliases": [],
        }
        transition_preview = await self.groups.preview_discovery_snapshot(next_level)
        self.assertFalse(transition_preview["would_promote"])
        self.assertEqual(
            transition_preview["diff"]["not_seen_group_names"],
            ["MindX - PTB06"],
        )
        await self.groups.apply_discovery_snapshot(
            next_level,
            observed_at="2026-06-29 00:00:00",
        )
        observed = (await self.groups.get_class_lifecycles())[0]
        self.assertEqual(observed["status"], "ACTIVE")
        self.assertEqual(observed["class_code"], "VT-C4K-PTA06")
        self.assertIsNone(observed["missing_since"])
        await self.groups.apply_discovery_snapshot(
            next_level,
            observed_at="2026-07-06 00:00:00",
        )
        active = (await self.groups.get_class_lifecycles())[0]
        self.assertEqual(active["status"], "ACTIVE")
        self.assertEqual(active["class_code"], "VT-C4K-PTA06")
        self.assertIsNone(active["missing_since"])
        history = await self.groups.get_class_history("VT-C4K|PT|06")
        self.assertEqual(
            [item["class_code"] for item in history],
            ["VT-C4K-PTB06", "VT-C4K-PTA06"],
        )
        self.assertEqual(
            [item["TenNhom"] for item in await self.groups.get_all_groups()],
            ["MindX - PTA06"],
        )
        promoted_preview = await self.groups.preview_discovery_snapshot(next_level)
        self.assertTrue(promoted_preview["would_promote"])
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT status FROM discovery_snapshots ORDER BY id"
            ) as cursor:
                self.assertEqual(
                    [row[0] for row in await cursor.fetchall()],
                    ["OBSERVED_ONLY", "OBSERVED_ONLY", "PROMOTED"],
                )

    async def test_missing_group_survives_one_snapshot_and_pauses_after_two(self):
        seed = {
            "reliable": True,
            "source_group_names": ["MindX - A", "MindX - B"],
            "discovered_groups": [
                {
                    "TenNhom": "MindX - A",
                    "NguoiPhuTrach": "Teacher A",
                    "NgayHoc": "T2",
                    "MaLop": "VT-C4K-PTB01",
                    "CohortKey": "VT-C4K|PT|01",
                    "BuoiSo": 4,
                },
                {
                    "TenNhom": "MindX - B",
                    "NguoiPhuTrach": "Teacher B",
                    "NgayHoc": "T3",
                    "MaLop": "VT-C4K-PTB02",
                    "CohortKey": "VT-C4K|PT|02",
                    "BuoiSo": 4,
                },
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [
                {
                    "cohort_key": "VT-C4K|PT|01",
                    "center_code": "VT-C4K",
                    "track": "PT",
                    "cohort_number": "01",
                    "class_code": "VT-C4K-PTB01",
                    "group_name": "MindX - A",
                    "lesson_number": 4,
                },
                {
                    "cohort_key": "VT-C4K|PT|02",
                    "center_code": "VT-C4K",
                    "track": "PT",
                    "cohort_number": "02",
                    "class_code": "VT-C4K-PTB02",
                    "group_name": "MindX - B",
                    "lesson_number": 4,
                },
            ],
            "lifecycle_aliases": [],
        }
        await self.groups.apply_discovery_snapshot(seed, observed_at="2026-07-01")

        partial = {
            "reliable": True,
            "source_group_names": ["MindX - A", "MindX - C"],
            "discovered_groups": [
                seed["discovered_groups"][0],
                {
                    "TenNhom": "MindX - C",
                    "NguoiPhuTrach": "Teacher C",
                    "NgayHoc": "T4",
                    "MaLop": "VT-C4K-PTB03",
                    "CohortKey": "VT-C4K|PT|03",
                    "BuoiSo": 1,
                },
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [
                seed["lifecycle_records"][0],
                {
                    "cohort_key": "VT-C4K|PT|03",
                    "center_code": "VT-C4K",
                    "track": "PT",
                    "cohort_number": "03",
                    "class_code": "VT-C4K-PTB03",
                    "group_name": "MindX - C",
                    "lesson_number": 1,
                },
            ],
            "lifecycle_aliases": [],
        }
        await self.groups.apply_discovery_snapshot(partial, observed_at="2026-07-02")
        lifecycle = await self.groups.get_class_lifecycles()
        self.assertEqual(
            next(item for item in lifecycle if item["cohort_key"] == "VT-C4K|PT|02")[
                "status"
            ],
            "ACTIVE",
        )

        await self.groups.apply_discovery_snapshot(partial, observed_at="2026-07-03")
        lifecycle = await self.groups.get_class_lifecycles()
        self.assertEqual(
            next(item for item in lifecycle if item["cohort_key"] == "VT-C4K|PT|02")[
                "status"
            ],
            "PAUSED_OR_TRANSITIONING",
        )
        self.assertIn(
            "MindX - B",
            [item["TenNhom"] for item in await self.groups.get_all_groups()],
        )

    async def test_rejected_snapshot_breaks_consecutive_promotion(self):
        stable = {
            "reliable": True,
            "source_group_names": ["MindX - A"],
            "discovered_groups": [
                {
                    "TenNhom": "MindX - A",
                    "NguoiPhuTrach": "Teacher A",
                    "NgayHoc": "T2",
                    "MaLop": "VT-C4K-PTB01",
                    "CohortKey": "VT-C4K|PT|01",
                    "BuoiSo": 4,
                }
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [
                {
                    "cohort_key": "VT-C4K|PT|01",
                    "center_code": "VT-C4K",
                    "track": "PT",
                    "cohort_number": "01",
                    "class_code": "VT-C4K-PTB01",
                    "group_name": "MindX - A",
                    "lesson_number": 4,
                }
            ],
            "lifecycle_aliases": [],
        }
        first = await self.groups.apply_discovery_snapshot(
            stable, observed_at="2026-07-01"
        )
        self.assertEqual(first["status"], "OBSERVED_ONLY")
        rejected = {
            **stable,
            "reliable": False,
            "discovered_groups": [],
            "lifecycle_records": [],
        }
        rejected_result = await self.groups.apply_discovery_snapshot(
            rejected, observed_at="2026-07-02"
        )
        self.assertEqual(rejected_result["status"], "REJECTED")
        self.assertEqual(
            [item["TenNhom"] for item in await self.groups.get_all_groups()],
            ["MindX - A"],
        )
        after_rejection = await self.groups.apply_discovery_snapshot(
            stable, observed_at="2026-07-03"
        )
        self.assertEqual(after_rejection["status"], "OBSERVED_ONLY")
        promoted = await self.groups.apply_discovery_snapshot(
            stable, observed_at="2026-07-04"
        )
        self.assertEqual(promoted["status"], "PROMOTED")

    async def test_alternating_snapshots_do_not_mark_missing_cohort(self):
        seed = {
            "reliable": True,
            "source_group_names": ["MindX - A", "MindX - B"],
            "discovered_groups": [
                {
                    "TenNhom": "MindX - A",
                    "MaLop": "VT-C4K-PTB01",
                    "CohortKey": "VT-C4K|PT|01",
                },
                {
                    "TenNhom": "MindX - B",
                    "MaLop": "VT-C4K-PTB02",
                    "CohortKey": "VT-C4K|PT|02",
                },
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [
                {
                    "cohort_key": "VT-C4K|PT|01",
                    "center_code": "VT-C4K",
                    "track": "PT",
                    "cohort_number": "01",
                    "class_code": "VT-C4K-PTB01",
                    "group_name": "MindX - A",
                    "lesson_number": 4,
                },
                {
                    "cohort_key": "VT-C4K|PT|02",
                    "center_code": "VT-C4K",
                    "track": "PT",
                    "cohort_number": "02",
                    "class_code": "VT-C4K-PTB02",
                    "group_name": "MindX - B",
                    "lesson_number": 4,
                },
            ],
            "lifecycle_aliases": [],
        }
        partial = {
            **seed,
            "source_group_names": ["MindX - A", "MindX - C"],
            "discovered_groups": [
                seed["discovered_groups"][0],
                {
                    "TenNhom": "MindX - C",
                    "MaLop": "VT-C4K-PTB03",
                    "CohortKey": "VT-C4K|PT|03",
                },
            ],
            "lifecycle_records": [
                seed["lifecycle_records"][0],
                {
                    "cohort_key": "VT-C4K|PT|03",
                    "center_code": "VT-C4K",
                    "track": "PT",
                    "cohort_number": "03",
                    "class_code": "VT-C4K-PTB03",
                    "group_name": "MindX - C",
                    "lesson_number": 1,
                },
            ],
        }
        await self.groups.apply_discovery_snapshot(seed, observed_at="2026-07-01")
        await self.groups.apply_discovery_snapshot(partial, observed_at="2026-07-02")
        await self.groups.apply_discovery_snapshot(seed, observed_at="2026-07-03")
        lifecycle = await self.groups.get_class_lifecycles()
        self.assertEqual(
            next(item for item in lifecycle if item["cohort_key"] == "VT-C4K|PT|02")[
                "status"
            ],
            "ACTIVE",
        )

    async def test_pt_and_game_level_renames_keep_one_active_group_and_history(self):
        async def promote(
            *,
            track: str,
            level: str,
            cohort_number: str,
            lesson_number: int,
            ordinal: int,
        ) -> None:
            class_code = f"VT-C4K-{track}{level}{cohort_number}"
            group_name = f"MindX - {track}{level}{cohort_number}"
            cohort_key = f"VT-C4K|{track}|{cohort_number}"
            plan = {
                "reliable": True,
                "source_group_names": [group_name],
                "discovered_groups": [
                    {
                        "TenNhom": group_name,
                        "MaLop": class_code,
                        "CohortKey": cohort_key,
                        "BuoiSo": lesson_number,
                    }
                ],
                "ignored_to_add": [],
                "ignored_to_remove": [],
                "lifecycle_records": [
                    {
                        "cohort_key": cohort_key,
                        "center_code": "VT-C4K",
                        "track": track,
                        "cohort_number": cohort_number,
                        "class_code": class_code,
                        "group_name": group_name,
                        "lesson_number": lesson_number,
                    }
                ],
                "lifecycle_aliases": [],
            }
            first = await self.groups.apply_discovery_snapshot(
                plan, observed_at=f"2026-08-{ordinal:02d} 10:00:00"
            )
            second = await self.groups.apply_discovery_snapshot(
                plan, observed_at=f"2026-08-{ordinal:02d} 10:01:00"
            )
            self.assertEqual(first["status"], "OBSERVED_ONLY")
            self.assertEqual(second["status"], "PROMOTED")

        await promote(track="PT", level="B", cohort_number="06", lesson_number=14, ordinal=1)
        await promote(track="PT", level="A", cohort_number="06", lesson_number=1, ordinal=2)
        await promote(track="PT", level="I", cohort_number="06", lesson_number=1, ordinal=3)
        await promote(track="G", level="B", cohort_number="02", lesson_number=14, ordinal=4)
        await promote(track="G", level="A", cohort_number="02", lesson_number=1, ordinal=5)
        await promote(track="G", level="I", cohort_number="02", lesson_number=1, ordinal=6)

        groups = [item["TenNhom"] for item in await self.groups.get_all_groups()]
        self.assertEqual(groups, ["MindX - GI02", "MindX - PTI06"])
        pt_history = await self.groups.get_class_history("VT-C4K|PT|06")
        game_history = await self.groups.get_class_history("VT-C4K|G|02")
        self.assertEqual(
            [item["class_code"] for item in pt_history],
            ["VT-C4K-PTB06", "VT-C4K-PTA06", "VT-C4K-PTI06"],
        )
        self.assertEqual(
            [item["class_code"] for item in game_history],
            ["VT-C4K-GB02", "VT-C4K-GA02", "VT-C4K-GI02"],
        )

    async def test_invalid_duplicate_snapshot_rolls_back_everything(self):
        valid = {
            "reliable": True,
            "source_group_names": ["MindX - A"],
            "discovered_groups": [
                {
                    "TenNhom": "MindX - A",
                    "MaLop": "VT-C4K-PTB01",
                    "CohortKey": "VT-C4K|PT|01",
                }
            ],
            "ignored_to_add": [],
            "ignored_to_remove": [],
            "lifecycle_records": [],
            "lifecycle_aliases": [],
        }
        await self.groups.apply_discovery_snapshot(valid, observed_at="2026-07-01")
        invalid = {
            **valid,
            "source_group_names": ["MindX - A", "MindX - A"],
            "discovered_groups": [
                valid["discovered_groups"][0],
                valid["discovered_groups"][0],
            ],
        }
        with self.assertRaises(DiscoverySnapshotError):
            await self.groups.apply_discovery_snapshot(
                invalid, observed_at="2026-07-02"
            )
        async with get_db_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM discovery_snapshots") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 1)
        self.assertEqual(
            [item["TenNhom"] for item in await self.groups.get_all_groups()],
            ["MindX - A"],
        )

    async def test_form_mapping_crud_and_mapping_dictionary(self):
        self.assertEqual(await self.reports.get_form_mappings(), [])
        await self.reports.add_form_mapping("Question A", "text", "Answer A")
        await self.reports.add_form_mapping("Question B", "radio", "Answer B")
        mappings = await self.reports.get_form_mappings()
        self.assertEqual([item["question"] for item in mappings], ["Question A", "Question B"])
        self.assertEqual(
            await self.reports.get_form_mapping_dict(),
            {
                "Question A": {"type": "text", "value": "Answer A"},
                "Question B": {"type": "radio", "value": "Answer B"},
            },
        )

        await self.reports.update_form_mapping(
            mappings[0]["id"], "Question A2", "radio", "Updated"
        )
        await self.reports.delete_form_mapping(mappings[1]["id"])
        self.assertEqual(
            await self.reports.get_form_mapping_dict(),
            {"Question A2": {"type": "radio", "value": "Updated"}},
        )

    async def test_submission_report_and_classification_persistence(self):
        await self.reports.add_form_submission(
            "Student",
            "vt-jsb14",
            "Thành công",
            job_id=42,
            action_key="form:42:student",
        )
        submissions = await self.reports.get_form_submissions()
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0]["student_name"], "Student")
        self.assertEqual(submissions[0]["job_id"], 42)
        self.assertEqual(submissions[0]["action_key"], "form:42:student")

        await self.reports.save_report(
            "MindX - Group", "VT-JSB14", 7, "2026-07-15", 0.98
        )
        await self.reports.save_classification_log(
            "report message", 4.5, 0.9, "REPORT", "REPORT"
        )
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT group_name, class_code, lesson_number, confidence FROM reports"
            ) as cursor:
                report = await cursor.fetchone()
            async with db.execute(
                "SELECT message, rule_score, embedding_score, final_result "
                "FROM classification_logs"
            ) as cursor:
                classification = await cursor.fetchone()

        self.assertEqual(report[:3], ("MindX - Group", "VT-JSB14", 7))
        self.assertAlmostEqual(report[3], 0.98)
        self.assertEqual(classification, ("report message", 4.5, 0.9, "REPORT"))

    async def test_submission_screenshot_and_clear_submissions(self):
        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake image data")
            temp_path = f.name

        await self.reports.add_form_submission(
            "Student With Screenshot",
            "VT-JSB14",
            "Thành công",
            job_id=43,
            action_key="form:43:student",
            screenshot_path=temp_path,
        )
        submissions = await self.reports.get_form_submissions()
        sub = submissions[0]
        self.assertEqual(sub["screenshot_path"], temp_path)

        single = await self.reports.get_form_submission(sub["id"])
        self.assertIsNotNone(single)
        self.assertEqual(single["screenshot_path"], temp_path)

        # Delete screenshot
        deleted = await self.reports.delete_submission_screenshot(sub["id"])
        self.assertTrue(deleted)
        self.assertFalse(Path(temp_path).exists())

        updated = await self.reports.get_form_submission(sub["id"])
        self.assertIsNone(updated["screenshot_path"])

        # Clear submissions
        cleared_count = await self.reports.clear_form_submissions()
        self.assertGreaterEqual(cleared_count, 1)
        remaining = await self.reports.get_form_submissions()
        self.assertEqual(len(remaining), 0)

    async def test_5_8_1_legacy_js_cohort_key_remap_and_collision_fail_closed(self):
        group_name = "MindX - VT-JSB17 T7 17h30-19h30"
        other_group = "MindX - VT-C4K-PTB16 CN 09h00-11h00"
        # Seed legacy cohort key VT-J|S|17 directly in DB (simulating pre-5.8.1 data without migration)
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO groups (group_name, class_code, cohort_key)
                VALUES (?, 'VT-JSB17', 'VT-J|S|17'),
                       (?, 'VT-C4K-PTB16', 'VT-C4K|PT|16')
                """,
                (group_name, other_group),
            )
            await db.execute(
                """
                INSERT INTO class_cohorts (
                    cohort_key, center_code, track, cohort_number,
                    current_class_code, current_group_name, current_lesson_number,
                    status, last_seen_bcnb_at, updated_at
                ) VALUES (
                    'VT-J|S|17', 'VT-J', 'S', '17',
                    'VT-JSB17', ?, 14,
                    'ACTIVE', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00'
                )
                """,
                (group_name,),
            )
            await db.execute(
                """
                INSERT INTO class_code_history (
                    cohort_key, class_code, group_name, first_seen_at, last_seen_at, last_lesson_number
                ) VALUES (
                    'VT-J|S|17', 'VT-JSB17', ?, '2026-07-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00', 14
                )
                """,
                (group_name,),
            )
            await db.commit()

        # Before discovery save, read paths already resolve legacy key to canonical key
        pre_lifecycles = await self.groups.get_class_lifecycles()
        self.assertEqual(len(pre_lifecycles), 1)
        self.assertEqual(pre_lifecycles[0]["cohort_key"], "VT|JS|17")
        self.assertEqual(
            [h["class_code"] for h in await self.groups.get_class_history("VT-J|S|17")],
            ["VT-JSB17"],
        )
        self.assertEqual(
            [h["class_code"] for h in await self.groups.get_class_history("VT|JS|17")],
            ["VT-JSB17"],
        )

        # Discovery save #1 and #2 with progressed class VT-JSA17 under canonical key VT|JS|17
        # generated via build_discovery_plan (including transition alias for VT-JSB17)
        from services.zalo_discovery_plan import build_discovery_plan

        new_js_group = "MindX - VT-JSA17 T7 17h30-19h30"
        plan = build_discovery_plan(
            group_names=[group_name, new_js_group, other_group],
            bcnb_data=[
                {
                    "ma_lop": "VT-JSA17",
                    "giao_vien": "GV JS",
                    "ngay_hoc": "T7",
                    "buoi_so": "1",
                },
                {
                    "ma_lop": "VT-C4K-PTB16",
                    "giao_vien": "GV PT",
                    "ngay_hoc": "CN",
                    "buoi_so": "5",
                },
            ],
            ignored_groups=[],
            existing_groups=[
                {
                    "TenNhom": group_name,
                    "MaLop": "VT-JSB17",
                    "CohortKey": "VT-J|S|17",
                }
            ],
        )
        res1 = await self.groups.apply_discovery_snapshot(
            plan, observed_at="2026-08-10T10:00:00+00:00"
        )
        res2 = await self.groups.apply_discovery_snapshot(
            plan, observed_at="2026-08-10T10:05:00+00:00"
        )
        self.assertEqual(res1["status"], "OBSERVED_ONLY")
        self.assertEqual(res2["status"], "PROMOTED")
        self.assertEqual(res1["alias_collision_count"], 0)
        self.assertEqual(res2["alias_collision_count"], 0)

        # Verify no implicit DB backfill: the stored row in class_cohorts keeps its legacy key VT-J|S|17
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT cohort_key, current_class_code FROM class_cohorts WHERE cohort_key = 'VT-J|S|17'"
            ) as cursor:
                raw_cohort_rows = await cursor.fetchall()
        self.assertEqual(len(raw_cohort_rows), 1)
        self.assertEqual(raw_cohort_rows[0][0], "VT-J|S|17")
        self.assertEqual(raw_cohort_rows[0][1], "VT-JSA17")

        # Verify no duplicate cohort, contiguous class history, and ACTIVE lifecycle status
        lifecycles = await self.groups.get_class_lifecycles()
        by_ck = {item["cohort_key"]: item for item in lifecycles}
        self.assertIn("VT|JS|17", by_ck)
        self.assertNotIn("VT-J|S|17", by_ck)
        self.assertEqual(by_ck["VT|JS|17"]["class_code"], "VT-JSA17")
        self.assertEqual(by_ck["VT|JS|17"]["status"], "ACTIVE")

        history_via_new = await self.groups.get_class_history("VT|JS|17")
        history_via_old = await self.groups.get_class_history("VT-J|S|17")
        self.assertEqual(
            [item["class_code"] for item in history_via_new],
            ["VT-JSB17", "VT-JSA17"],
        )
        self.assertEqual(
            [item["class_code"] for item in history_via_old],
            ["VT-JSB17", "VT-JSA17"],
        )

        # Now seed an alias collision (both VT-C|S|04 and VT|CS|04 exist in class_cohorts) -> fail-closed
        cs_group_old = "MindX - VT-CSB04 Old"
        cs_group_new = "MindX - VT-CSA04 New"
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO class_cohorts (
                    cohort_key, center_code, track, cohort_number,
                    current_class_code, current_group_name, current_lesson_number,
                    status, last_seen_bcnb_at, updated_at
                ) VALUES
                    ('VT-C|S|04', 'VT-C', 'S', '04', 'VT-CSB04', ?, 12, 'ACTIVE', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00'),
                    ('VT|CS|04', 'VT', 'CS', '04', 'VT-CSA04', ?, 1, 'ACTIVE', '2026-08-05T00:00:00+00:00', '2026-08-05T00:00:00+00:00')
                """,
                (cs_group_old, cs_group_new),
            )
            await db.execute(
                """
                INSERT INTO class_code_history (
                    cohort_key, class_code, group_name, first_seen_at, last_seen_at, last_lesson_number
                ) VALUES
                    ('VT-C|S|04', 'VT-CSB04', ?, '2026-07-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00', 12),
                    ('VT|CS|04', 'VT-CSA04', ?, '2026-08-05T00:00:00+00:00', '2026-08-05T00:00:00+00:00', 1)
                """,
                (cs_group_old, cs_group_new),
            )
            await db.commit()

        collision_plan = {
            **plan,
            "fingerprint_sha256": None,
            "source_group_names": [group_name, new_js_group, other_group, cs_group_new],
            "discovered_groups": [
                *plan["discovered_groups"],
                {
                    "TenNhom": cs_group_new,
                    "MaLop": "VT-CSA04",
                    "CohortKey": "VT|CS|04",
                },
            ],
            "lifecycle_records": [
                *plan["lifecycle_records"],
                {
                    "cohort_key": "VT|CS|04",
                    "center_code": "VT",
                    "track": "CS",
                    "cohort_number": "04",
                    "class_code": "VT-CSA04",
                    "group_name": cs_group_new,
                    "lesson_number": 2,
                },
            ],
        }
        res_collision = await self.groups.apply_discovery_snapshot(
            collision_plan, observed_at="2026-08-11T10:00:00+00:00"
        )
        self.assertGreaterEqual(res_collision["alias_collision_count"], 1)
        # Fail-closed: collided keys do not merge histories
        self.assertEqual(
            [item["class_code"] for item in await self.groups.get_class_history("VT-C|S|04")],
            ["VT-CSB04"],
        )
        self.assertEqual(
            [item["class_code"] for item in await self.groups.get_class_history("VT|CS|04")],
            ["VT-CSA04"],
        )


if __name__ == "__main__":
    unittest.main()
