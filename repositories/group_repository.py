from datetime import datetime, timedelta, timezone
import sqlite3
from typing import Any

from db.database import get_db_connection


def _utc_timestamp(value: datetime | str | None = None) -> str:
    if isinstance(value, str):
        return value
    selected = value or datetime.now(timezone.utc)
    if selected.tzinfo is None:
        selected = selected.replace(tzinfo=timezone.utc)
    return selected.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class GroupRepository:
    async def get_all_groups(self):
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT group_name, person_in_charge, class_date "
                "FROM groups ORDER BY group_name"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "TenNhom": row[0],
                        "NguoiPhuTrach": row[1],
                        "NgayHoc": row[2],
                    }
                    for row in rows
                ]

    async def save_groups(self, groups_list):
        async with get_db_connection() as db:
            try:
                await db.execute("BEGIN")
                await self._replace_groups(db, groups_list)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    @staticmethod
    async def _replace_groups(db, groups_list) -> None:
        await db.execute("DELETE FROM groups")
        for group in groups_list:
            await db.execute(
                """
                INSERT INTO groups (
                    group_name, person_in_charge, class_date,
                    class_code, cohort_key, lesson_number
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    group.get("TenNhom"),
                    group.get("NguoiPhuTrach", "Chưa xác định"),
                    group.get("NgayHoc", ""),
                    group.get("MaLop"),
                    group.get("CohortKey"),
                    group.get("BuoiSo"),
                ),
            )

    async def delete_group(self, group_name):
        async with get_db_connection() as db:
            await db.execute("DELETE FROM groups WHERE group_name=?", (group_name,))
            await db.commit()

    async def add_group(
        self,
        group_name,
        person_in_charge="Chưa xác định",
        class_date="",
    ):
        async with get_db_connection() as db:
            try:
                await db.execute(
                    "INSERT INTO groups (group_name, person_in_charge, class_date) "
                    "VALUES (?, ?, ?)",
                    (group_name, person_in_charge, class_date),
                )
                await db.commit()
            except sqlite3.IntegrityError:
                pass

    async def get_all_ignored_groups(self, *, include_archived: bool = False):
        query = "SELECT group_name FROM ignored_groups"
        if not include_archived:
            query += " WHERE status != 'ARCHIVED'"
        query += " ORDER BY group_name"
        async with get_db_connection() as db:
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]

    async def get_ignored_group_records(
        self,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT group_name, status, class_code, cohort_key, ignored_at, "
            "ended_at, archived_at FROM ignored_groups"
        )
        if not include_archived:
            query += " WHERE status != 'ARCHIVED'"
        query += " ORDER BY group_name"
        async with get_db_connection() as db:
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "group_name": row[0],
                "status": row[1],
                "class_code": row[2],
                "cohort_key": row[3],
                "ignored_at": row[4],
                "ended_at": row[5],
                "archived_at": row[6],
            }
            for row in rows
        ]

    async def add_ignored_group(
        self,
        group_name: str,
        *,
        class_code: str | None = None,
        cohort_key: str | None = None,
    ) -> None:
        now = _utc_timestamp()
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO ignored_groups (
                    group_name, status, class_code, cohort_key, updated_at
                ) VALUES (?, 'PAUSED_OR_TRANSITIONING', ?, ?, ?)
                ON CONFLICT(group_name) DO UPDATE SET
                    class_code = COALESCE(excluded.class_code, ignored_groups.class_code),
                    cohort_key = COALESCE(excluded.cohort_key, ignored_groups.cohort_key),
                    updated_at = excluded.updated_at
                """,
                (group_name, class_code, cohort_key, now),
            )
            await db.commit()

    async def remove_ignored_group(self, group_name):
        async with get_db_connection() as db:
            await db.execute("DELETE FROM ignored_groups WHERE group_name=?", (group_name,))
            await db.commit()

    async def confirm_ignored_group_ended(
        self,
        group_name: str,
        *,
        ended_at: datetime | str | None = None,
    ) -> bool:
        timestamp = _utc_timestamp(ended_at)
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                UPDATE ignored_groups
                SET status = 'ENDED_CONFIRMED', ended_at = ?, archived_at = NULL,
                    updated_at = ?
                WHERE group_name = ?
                """,
                (timestamp, timestamp, group_name),
            )
            changed = cursor.rowcount > 0
            if changed:
                await db.execute(
                    """
                    UPDATE class_cohorts
                    SET status = 'ENDED_CONFIRMED', ended_at = ?, archived_at = NULL,
                        updated_at = ?
                    WHERE cohort_key = (
                        SELECT cohort_key FROM ignored_groups WHERE group_name = ?
                    )
                    """,
                    (timestamp, timestamp, group_name),
                )
            await db.commit()
        return changed

    async def archive_confirmed_ended_groups(
        self,
        days: int = 14,
        *,
        now: datetime | str | None = None,
    ) -> int:
        if days < 0:
            raise ValueError("days must be non-negative")
        if isinstance(now, str):
            current = datetime.fromisoformat(now.replace("Z", "+00:00"))
        else:
            current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        threshold = _utc_timestamp(current - timedelta(days=days))
        archived_at = _utc_timestamp(current)
        async with get_db_connection() as db:
            cursor = await db.execute(
                """
                UPDATE ignored_groups
                SET status = 'ARCHIVED', archived_at = ?, updated_at = ?
                WHERE status = 'ENDED_CONFIRMED'
                  AND ended_at IS NOT NULL
                  AND ended_at <= ?
                """,
                (archived_at, archived_at, threshold),
            )
            changed = cursor.rowcount
            await db.execute(
                """
                UPDATE class_cohorts
                SET status = 'ARCHIVED', archived_at = ?, updated_at = ?
                WHERE status = 'ENDED_CONFIRMED'
                  AND ended_at IS NOT NULL
                  AND ended_at <= ?
                """,
                (archived_at, archived_at, threshold),
            )
            await db.commit()
        return changed

    async def cleanup_old_ignored_groups(self, days=14):
        """Compatibility alias: archive only explicitly confirmed ended groups."""
        return await self.archive_confirmed_ended_groups(days)

    async def apply_discovery_snapshot(
        self,
        plan: dict[str, Any],
        *,
        observed_at: datetime | str | None = None,
    ) -> None:
        timestamp = _utc_timestamp(observed_at)
        async with get_db_connection() as db:
            try:
                await db.execute("BEGIN")
                await self._replace_groups(db, plan["discovered_groups"])
                await db.execute(
                    """
                    UPDATE class_cohorts
                    SET status = 'PAUSED_OR_TRANSITIONING',
                        missing_since = COALESCE(missing_since, ?),
                        updated_at = ?
                    WHERE status = 'ACTIVE'
                    """,
                    (timestamp, timestamp),
                )
                for record in plan.get("lifecycle_records", []):
                    await self._upsert_active_cohort(db, record, timestamp)
                for alias in plan.get("lifecycle_aliases", []):
                    await self._upsert_history_if_cohort_exists(db, alias, timestamp)
                for group_name in plan["ignored_to_add"]:
                    await db.execute(
                        """
                        INSERT INTO ignored_groups (group_name, status, updated_at)
                        VALUES (?, 'PAUSED_OR_TRANSITIONING', ?)
                        ON CONFLICT(group_name) DO UPDATE SET
                            updated_at = excluded.updated_at
                        """,
                        (group_name, timestamp),
                    )
                for group_name in plan["ignored_to_remove"]:
                    await db.execute(
                        "DELETE FROM ignored_groups WHERE group_name = ?",
                        (group_name,),
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    @staticmethod
    async def _upsert_active_cohort(db, record: dict[str, Any], timestamp: str) -> None:
        await db.execute(
            """
            INSERT INTO class_cohorts (
                cohort_key, center_code, track, cohort_number,
                current_class_code, current_group_name, current_lesson_number,
                status, last_seen_bcnb_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
            ON CONFLICT(cohort_key) DO UPDATE SET
                center_code = excluded.center_code,
                track = excluded.track,
                cohort_number = excluded.cohort_number,
                current_class_code = excluded.current_class_code,
                current_group_name = excluded.current_group_name,
                current_lesson_number = excluded.current_lesson_number,
                status = 'ACTIVE',
                last_seen_bcnb_at = excluded.last_seen_bcnb_at,
                missing_since = NULL,
                ended_at = NULL,
                archived_at = NULL,
                updated_at = excluded.updated_at
            """,
            (
                record["cohort_key"],
                record["center_code"],
                record["track"],
                record["cohort_number"],
                record["class_code"],
                record["group_name"],
                record.get("lesson_number"),
                timestamp,
                timestamp,
            ),
        )
        await db.execute(
            """
            INSERT INTO class_code_history (
                cohort_key, class_code, group_name, first_seen_at,
                last_seen_at, last_lesson_number
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cohort_key, class_code, group_name) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                last_lesson_number = excluded.last_lesson_number
            """,
            (
                record["cohort_key"],
                record["class_code"],
                record["group_name"],
                timestamp,
                timestamp,
                record.get("lesson_number"),
            ),
        )

    @staticmethod
    async def _upsert_history_if_cohort_exists(
        db,
        record: dict[str, Any],
        timestamp: str,
    ) -> None:
        async with db.execute(
            "SELECT 1 FROM class_cohorts WHERE cohort_key = ?",
            (record["cohort_key"],),
        ) as cursor:
            exists = await cursor.fetchone()
        if not exists:
            return
        await db.execute(
            """
            INSERT INTO class_code_history (
                cohort_key, class_code, group_name, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cohort_key, class_code, group_name) DO UPDATE SET
                last_seen_at = excluded.last_seen_at
            """,
            (
                record["cohort_key"],
                record["class_code"],
                record["group_name"],
                timestamp,
                timestamp,
            ),
        )

    async def get_class_lifecycles(
        self,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT cohort_key, current_class_code, current_group_name, "
            "current_lesson_number, status, last_seen_bcnb_at, missing_since, "
            "ended_at, archived_at FROM class_cohorts"
        )
        if not include_archived:
            query += " WHERE status != 'ARCHIVED'"
        query += " ORDER BY current_class_code"
        async with get_db_connection() as db:
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "cohort_key": row[0],
                "class_code": row[1],
                "group_name": row[2],
                "lesson_number": row[3],
                "status": row[4],
                "last_seen_bcnb_at": row[5],
                "missing_since": row[6],
                "ended_at": row[7],
                "archived_at": row[8],
            }
            for row in rows
        ]

    async def get_class_history(self, cohort_key: str) -> list[dict[str, Any]]:
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT class_code, group_name, first_seen_at, last_seen_at,
                       last_lesson_number
                FROM class_code_history
                WHERE cohort_key = ?
                ORDER BY first_seen_at, id
                """,
                (cohort_key,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "class_code": row[0],
                "group_name": row[1],
                "first_seen_at": row[2],
                "last_seen_at": row[3],
                "lesson_number": row[4],
            }
            for row in rows
        ]
