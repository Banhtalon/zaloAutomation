import os
from datetime import datetime, timezone
import sqlite3
import tempfile
import unittest

import db.database as database
from db.database import get_db_connection, init_db
from repositories.group_repository import GroupRepository
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
        await self.groups.remove_ignored_group("Current group")
        self.assertEqual(await self.groups.get_all_ignored_groups(), [])

    async def test_discovery_snapshot_tracks_transition_and_reappearance(self):
        base_plan = {
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
        await self.groups.apply_discovery_snapshot(
            base_plan,
            observed_at="2026-06-01 00:00:00",
        )

        paused_plan = {
            "discovered_groups": [],
            "ignored_to_add": ["MindX - PTB06"],
            "ignored_to_remove": [],
            "lifecycle_records": [],
            "lifecycle_aliases": [],
        }
        await self.groups.apply_discovery_snapshot(
            paused_plan,
            observed_at="2026-06-22 00:00:00",
        )
        paused = (await self.groups.get_class_lifecycles())[0]
        self.assertEqual(paused["status"], "PAUSED_OR_TRANSITIONING")
        self.assertIsNone(paused["ended_at"])

        next_level = {
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
        await self.groups.apply_discovery_snapshot(
            next_level,
            observed_at="2026-06-29 00:00:00",
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


if __name__ == "__main__":
    unittest.main()
