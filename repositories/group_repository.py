import hashlib
import json
from datetime import datetime, timedelta
import sqlite3
from typing import Any

from core.time_utils import now_utc, parse_timestamp, utc_iso
from db.database import get_db_connection
from services.zalo_discovery_plan import (
    DISCOVERY_PLAN_VERSION,
    MIN_DISCOVERY_COVERAGE,
    MIN_RECOGNIZED_TO_EXISTING,
)


def _utc_timestamp(value: datetime | str | None = None) -> str:
    if isinstance(value, str):
        parsed = parse_timestamp(value)
        if parsed is None:
            raise ValueError("Timestamp không hợp lệ.")
        return utc_iso(parsed, timespec="seconds")
    return utc_iso(value, timespec="seconds")


class DiscoverySnapshotError(RuntimeError):
    pass


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

    async def get_scan_groups(self):
        """Return only groups that are currently eligible for a Zalo scan.

        Discovery keeps historical/transitioning names in ``groups`` so that
        lifecycle history and reactivation checks remain possible.  Those
        rows are not active scan targets, however.  Keep the operational
        scan set separate from the administrative ``get_all_groups`` view and
        exclude every explicitly ignored name plus cohorts no longer marked
        ``ACTIVE``.
        """
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT g.group_name, g.person_in_charge, g.class_date
                FROM groups AS g
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM ignored_groups AS ignored
                    WHERE ignored.group_name = g.group_name
                )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM class_cohorts AS cohort
                    WHERE cohort.cohort_key = g.cohort_key
                      AND cohort.status <> 'ACTIVE'
                )
                ORDER BY g.group_name
                """
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

    async def restore_ignored_group(self, group_name: str) -> bool:
        """Restore an ignored class and its exact cohort in one transaction."""
        timestamp = _utc_timestamp()
        async with get_db_connection() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    """
                    SELECT class_code, cohort_key
                    FROM ignored_groups
                    WHERE group_name = ?
                    """,
                    (group_name,),
                ) as cursor:
                    ignored = await cursor.fetchone()
                if ignored is None:
                    await db.rollback()
                    return False
                class_code, cohort_key = ignored
                if cohort_key:
                    if not class_code:
                        await db.rollback()
                        return False
                    async with db.execute(
                        """
                        SELECT 1
                        FROM class_cohorts
                        WHERE cohort_key = ?
                          AND current_group_name = ?
                          AND current_class_code = ?
                        """,
                        (cohort_key, group_name, class_code),
                    ) as cursor:
                        cohort = await cursor.fetchone()
                    if cohort is None:
                        await db.rollback()
                        return False
                await db.execute(
                    "DELETE FROM ignored_groups WHERE group_name = ?",
                    (group_name,),
                )
                await db.execute(
                    """
                    INSERT INTO groups (
                        group_name, person_in_charge, class_date,
                        class_code, cohort_key
                    ) VALUES (?, 'Chưa xác định', '', ?, ?)
                    ON CONFLICT(group_name) DO UPDATE SET
                        class_code = COALESCE(groups.class_code, excluded.class_code),
                        cohort_key = COALESCE(groups.cohort_key, excluded.cohort_key)
                    """,
                    (group_name, class_code, cohort_key),
                )
                if cohort_key:
                    await db.execute(
                        """
                        UPDATE class_cohorts
                        SET status = 'ACTIVE', missing_since = NULL,
                            ended_at = NULL, archived_at = NULL, updated_at = ?
                        WHERE cohort_key = ?
                          AND current_group_name = ?
                          AND current_class_code = ?
                        """,
                        (timestamp, cohort_key, group_name, class_code),
                    )
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                raise

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
                SET status = 'ENDED_CONFIRMED',
                    ended_at = CASE
                        WHEN status = 'ENDED_CONFIRMED' AND ended_at IS NOT NULL
                        THEN ended_at
                        ELSE ?
                    END,
                    archived_at = NULL,
                    updated_at = ?
                WHERE group_name = ? AND status <> 'ARCHIVED'
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
        days: int = 7,
        *,
        now: datetime | str | None = None,
    ) -> int:
        if days < 0:
            raise ValueError("days must be non-negative")
        current = parse_timestamp(now) if isinstance(now, str) else now
        current = current or now_utc()
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

    async def cleanup_old_ignored_groups(self, days=7):
        """Compatibility alias: archive only explicitly confirmed ended groups."""
        return await self.archive_confirmed_ended_groups(days)

    async def apply_discovery_snapshot(
        self,
        plan: dict[str, Any],
        *,
        observed_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        timestamp = _utc_timestamp(observed_at)
        snapshot = self._snapshot_metadata(plan)
        async with get_db_connection() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """
                    INSERT INTO discovery_snapshots (
                        observed_at, source_count, recognized_count,
                        expected_count, coverage, delta_count,
                        fingerprint_sha256, status, trust_reason, plan_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'STAGED', NULL, ?)
                    """,
                    (
                        timestamp,
                        snapshot["source_count"],
                        snapshot["recognized_count"],
                        snapshot["expected_count"],
                        snapshot["coverage"],
                        snapshot["delta_count"],
                        snapshot["fingerprint_sha256"],
                        snapshot["plan_json"],
                    ),
                )
                snapshot_id = int(cursor.lastrowid)
                if not snapshot["reliable"]:
                    await db.execute(
                        """
                        UPDATE discovery_snapshots
                        SET status = 'REJECTED',
                            trust_reason = ?
                        WHERE id = ?
                        """,
                        (
                            "Snapshot không đủ độ tin cậy; giữ nguyên dữ liệu hiện tại.",
                            snapshot_id,
                        ),
                    )
                    await db.commit()
                    return {
                        **snapshot,
                        "snapshot_id": snapshot_id,
                        "status": "REJECTED",
                        "consistent": False,
                    }

                async with db.execute(
                    """
                    SELECT id, fingerprint_sha256, status
                    FROM discovery_snapshots
                    WHERE id < ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (snapshot_id,),
                ) as previous_cursor:
                    previous = await previous_cursor.fetchone()
                consistent = bool(
                    previous
                    and previous[2] in ("OBSERVED_ONLY", "PROMOTED")
                    and previous[1] == snapshot["fingerprint_sha256"]
                )
                status = "PROMOTED" if consistent else "OBSERVED_ONLY"
                trust_reason = (
                    "Hai snapshot liên tiếp nhất quán; cho phép cập nhật trạng thái vắng."
                    if consistent
                    else "Snapshot đầu tiên/đã thay đổi; chỉ upsert quan sát, chưa đánh dấu vắng."
                )

                diff = await self._compute_discovery_diff(db, plan)
                await self._upsert_observed_groups(
                    db,
                    plan.get("discovered_groups", []),
                )
                for record in plan.get("lifecycle_records", []):
                    await self._upsert_active_cohort(db, record, timestamp)
                for alias in plan.get("lifecycle_aliases", []):
                    await self._upsert_history_if_cohort_exists(db, alias, timestamp)
                await self._apply_ignored_updates(
                    db,
                    plan,
                    timestamp,
                    include_additions=consistent,
                )
                auto_finished_count = await self._apply_bcnb_finished_groups(
                    db,
                    plan,
                    timestamp,
                )
                if consistent:
                    await self._mark_missing_cohorts(
                        db,
                        plan.get("lifecycle_records", []),
                        timestamp,
                    )
                    await self._remove_superseded_groups(
                        db,
                        plan.get("lifecycle_records", []),
                    )
                await db.execute(
                    """
                    UPDATE discovery_snapshots
                    SET status = ?, trust_reason = ?, promoted_at = ?,
                        previous_snapshot_id = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        trust_reason,
                        timestamp if consistent else None,
                        previous[0] if consistent and previous else None,
                        snapshot_id,
                    ),
                )
                await db.commit()
                return {
                    **snapshot,
                    "snapshot_id": snapshot_id,
                    "status": status,
                    "consistent": consistent,
                    "diff": diff,
                    "auto_finished_count": auto_finished_count,
                    "previous_snapshot_id": previous[0]
                    if consistent and previous
                    else None,
                }
            except Exception:
                await db.rollback()
                raise

    async def preview_discovery_snapshot(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Return a read-only diff and promotion decision for a plan."""
        snapshot = self._snapshot_metadata(plan)
        async with get_db_connection() as db:
            diff = (
                await self._compute_discovery_diff(db, plan)
                if snapshot["reliable"]
                else {}
            )
            async with db.execute(
                """
                SELECT fingerprint_sha256, status
                FROM discovery_snapshots
                ORDER BY id DESC LIMIT 1
                """
            ) as cursor:
                previous = await cursor.fetchone()
        would_promote = bool(
            snapshot["reliable"]
            and previous
            and previous[1] in ("OBSERVED_ONLY", "PROMOTED")
            and previous[0] == snapshot["fingerprint_sha256"]
        )
        return {
            **snapshot,
            "status": "PROMOTED" if would_promote else "OBSERVED_ONLY",
            "would_promote": would_promote,
            "diff": diff,
        }

    async def get_discovery_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        """Read one immutable discovery snapshot and its scan-week plan."""
        if not isinstance(snapshot_id, int) or snapshot_id < 1:
            return None
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, observed_at, status, trust_reason, plan_json,
                       fingerprint_sha256
                FROM discovery_snapshots
                WHERE id = ?
                """,
                (snapshot_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        try:
            plan = json.loads(row[4])
        except (TypeError, json.JSONDecodeError) as exc:
            raise DiscoverySnapshotError(
                "Nội dung kế hoạch discovery không đọc được."
            ) from exc
        if not isinstance(plan, dict):
            raise DiscoverySnapshotError("Kế hoạch discovery không hợp lệ.")
        return {
            "snapshot_id": int(row[0]),
            "observed_at": row[1],
            "status": row[2],
            "trust_reason": row[3],
            "fingerprint_sha256": row[5],
            "plan": plan,
        }

    async def get_latest_discovery_snapshot(self) -> dict[str, Any] | None:
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT id, observed_at, status, trust_reason, plan_json,
                       fingerprint_sha256
                FROM discovery_snapshots
                WHERE status IN ('OBSERVED_ONLY', 'PROMOTED')
                ORDER BY id DESC LIMIT 1
                """
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        try:
            plan = json.loads(row[4])
        except (TypeError, json.JSONDecodeError) as exc:
            raise DiscoverySnapshotError(
                "Nội dung kế hoạch discovery mới nhất không đọc được."
            ) from exc
        if not isinstance(plan, dict):
            raise DiscoverySnapshotError("Kế hoạch discovery mới nhất không hợp lệ.")
        return {
            "snapshot_id": int(row[0]),
            "observed_at": row[1],
            "status": row[2],
            "trust_reason": row[3],
            "fingerprint_sha256": row[5],
            "plan": plan,
        }

    @staticmethod
    def _snapshot_metadata(plan: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(plan, dict):
            raise DiscoverySnapshotError("Discovery plan không hợp lệ.")
        discovered_groups = plan.get("discovered_groups", [])
        aliases = plan.get("lifecycle_aliases", [])
        if not isinstance(discovered_groups, list) or not isinstance(aliases, list):
            raise DiscoverySnapshotError("Nội dung snapshot không hợp lệ.")
        raw_names = plan.get("source_group_names")
        if raw_names is None:
            names = [
                item.get("TenNhom", "")
                for item in discovered_groups
                if isinstance(item, dict)
            ]
        elif isinstance(raw_names, list):
            names = raw_names
        else:
            raise DiscoverySnapshotError("Danh sách source group không hợp lệ.")
        source_names = sorted(
            {
                name.strip()
                for name in names
                if isinstance(name, str) and name.strip()
            }
        )
        if any(
            not isinstance(item, dict)
            for item in [*discovered_groups, *aliases]
        ):
            raise DiscoverySnapshotError("Dòng nhận diện snapshot không hợp lệ.")

        def _text(value: Any) -> str:
            return "" if value is None else str(value).strip()

        recognized_signatures = sorted(
            {
                (
                    _text(item.get("TenNhom")),
                    _text(item.get("MaLop")),
                    _text(item.get("CohortKey")),
                )
                for item in discovered_groups
            }
            | {
                (
                    _text(item.get("group_name")),
                    _text(item.get("class_code")),
                    _text(item.get("cohort_key")),
                )
                for item in aliases
            }
        )
        try:
            plan_version = int(plan.get("plan_version", DISCOVERY_PLAN_VERSION))
        except (TypeError, ValueError) as exc:
            raise DiscoverySnapshotError("plan_version không hợp lệ.") from exc
        fingerprint_payload = {
            "version": plan_version,
            "source_group_names": source_names,
            "recognized_signatures": recognized_signatures,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        supplied_raw = plan.get("fingerprint_sha256")
        supplied_fingerprint = (
            "" if supplied_raw is None else str(supplied_raw).strip().upper()
        )
        if supplied_fingerprint and supplied_fingerprint != fingerprint:
            raise DiscoverySnapshotError(
                "Checksum snapshot không khớp nội dung quan sát."
            )
        source_count = len(source_names)
        recognized_count = len(discovered_groups) + len(aliases)
        try:
            expected_count = max(
                recognized_count,
                int(plan.get("expected_count", recognized_count)),
            )
        except (TypeError, ValueError) as exc:
            raise DiscoverySnapshotError("expected_count không hợp lệ.") from exc
        coverage = recognized_count / source_count if source_count else 0.0
        delta_count = recognized_count - expected_count
        plan_reliable = plan.get("reliable", True)
        if not isinstance(plan_reliable, bool):
            raise DiscoverySnapshotError("Cờ reliable của snapshot không hợp lệ.")
        reliable = plan_reliable and bool(
            source_count
            and recognized_count
            and discovered_groups
            and coverage >= MIN_DISCOVERY_COVERAGE
            and (
                recognized_count / expected_count
                if expected_count
                else 0
            )
            >= MIN_RECOGNIZED_TO_EXISTING
        )
        plan_json = json.dumps(plan, ensure_ascii=False, sort_keys=True)
        return {
            "reliable": reliable,
            "source_count": source_count,
            "recognized_count": recognized_count,
            "expected_count": expected_count,
            "coverage": coverage,
            "delta_count": delta_count,
            "fingerprint_sha256": fingerprint,
            "plan_json": plan_json,
        }

    @staticmethod
    def _observed_group_map(groups: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        observed: dict[str, dict[str, Any]] = {}
        for group in groups:
            if not isinstance(group, dict):
                raise DiscoverySnapshotError("Dòng nhóm trong snapshot không hợp lệ.")
            group_name = str(group.get("TenNhom", "")).strip()
            if not group_name or group_name in observed:
                raise DiscoverySnapshotError(
                    "Snapshot có tên nhóm rỗng hoặc trùng; dữ liệu cũ được giữ nguyên."
                )
            observed[group_name] = group
        return observed

    @classmethod
    async def _compute_discovery_diff(
        cls,
        db,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        observed = cls._observed_group_map(plan.get("discovered_groups", []))
        async with db.execute(
            """
            SELECT group_name, person_in_charge, class_date,
                   class_code, cohort_key, lesson_number
            FROM groups
            """
        ) as cursor:
            rows = await cursor.fetchall()
        existing = {
            str(row[0]): {
                "NguoiPhuTrach": row[1],
                "NgayHoc": row[2],
                "MaLop": row[3],
                "CohortKey": row[4],
                "BuoiSo": row[5],
            }
            for row in rows
            if row[0] is not None
        }
        added = sorted(set(observed) - set(existing))
        not_seen = sorted(set(existing) - set(observed))
        unchanged: list[str] = []
        updated: list[str] = []
        for group_name in sorted(set(observed) & set(existing)):
            group = observed[group_name]
            expected = {
                "NguoiPhuTrach": group.get("NguoiPhuTrach", "Chưa xác định"),
                "NgayHoc": group.get("NgayHoc", ""),
                "MaLop": group.get("MaLop"),
                "CohortKey": group.get("CohortKey"),
                "BuoiSo": group.get("BuoiSo"),
            }
            if all(
                existing[group_name].get(key) == value
                for key, value in expected.items()
            ):
                unchanged.append(group_name)
            else:
                updated.append(group_name)
        return {
            "observed_count": len(observed),
            "added_group_names": added,
            "updated_group_names": updated,
            "unchanged_group_names": unchanged,
            "not_seen_group_names": not_seen,
            "ignored_to_add": sorted(set(plan.get("ignored_to_add", []))),
            "ignored_to_remove": sorted(set(plan.get("ignored_to_remove", []))),
        }

    @classmethod
    async def _upsert_observed_groups(cls, db, groups: list[dict[str, Any]]) -> None:
        for group_name, group in cls._observed_group_map(groups).items():
            await db.execute(
                """
                INSERT INTO groups (
                    group_name, person_in_charge, class_date,
                    class_code, cohort_key, lesson_number
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_name) DO UPDATE SET
                    person_in_charge = excluded.person_in_charge,
                    class_date = excluded.class_date,
                    class_code = excluded.class_code,
                    cohort_key = excluded.cohort_key,
                    lesson_number = excluded.lesson_number
                """,
                (
                    group_name,
                    group.get("NguoiPhuTrach", "Chưa xác định"),
                    group.get("NgayHoc", ""),
                    group.get("MaLop"),
                    group.get("CohortKey"),
                    group.get("BuoiSo"),
                ),
            )

    @staticmethod
    async def _apply_ignored_updates(
        db,
        plan: dict[str, Any],
        timestamp: str,
        *,
        include_additions: bool,
    ) -> None:
        aliases_by_name = {
            item.get("group_name"): item
            for item in plan.get("lifecycle_aliases", [])
            if isinstance(item, dict) and item.get("group_name")
        }
        if include_additions:
            for group_name in dict.fromkeys(plan.get("ignored_to_add", [])):
                alias = aliases_by_name.get(group_name, {})
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
                    (
                        group_name,
                        alias.get("class_code"),
                        alias.get("cohort_key"),
                        timestamp,
                    ),
                )
        for group_name in dict.fromkeys(plan.get("ignored_to_remove", [])):
            await db.execute(
                "DELETE FROM ignored_groups WHERE group_name = ?",
                (group_name,),
            )

    @classmethod
    async def _apply_bcnb_finished_groups(
        cls,
        db,
        plan: dict[str, Any],
        timestamp: str,
    ) -> int:
        """Auto-confirm only exact, non-current BCNB Finished identities."""
        raw_items = plan.get("bcnb_finished_groups", [])
        if not isinstance(raw_items, list):
            raise DiscoverySnapshotError("Danh sách lớp BCNB Finished không hợp lệ.")
        current_names = set(cls._observed_group_map(plan.get("discovered_groups", [])))
        seen_names: set[str] = set()
        changed = 0
        for item in raw_items:
            if not isinstance(item, dict):
                raise DiscoverySnapshotError("Dòng lớp BCNB Finished không hợp lệ.")
            group_name = str(item.get("group_name", "")).strip()
            class_code = str(item.get("class_code", "")).strip()
            cohort_key_raw = item.get("cohort_key")
            cohort_key = (
                str(cohort_key_raw).strip() if cohort_key_raw is not None else None
            )
            if (
                not group_name
                or not class_code
                or group_name in seen_names
                or item.get("bcnb_status") != "FINISHED"
                or item.get("evidence_source") != "BCNB_STUDENT_CLASS_STATUS"
            ):
                raise DiscoverySnapshotError(
                    "Identity/evidence lớp BCNB Finished không hợp lệ."
                )
            seen_names.add(group_name)
            if group_name in current_names or not cohort_key:
                continue
            async with db.execute(
                """
                SELECT 1
                FROM class_cohorts
                WHERE cohort_key = ?
                  AND current_group_name = ?
                  AND current_class_code = ?
                  AND status <> 'ARCHIVED'
                """,
                (cohort_key, group_name, class_code),
            ) as identity_cursor:
                exact_identity = await identity_cursor.fetchone()
            if exact_identity is None:
                continue
            async with db.execute(
                "SELECT status FROM ignored_groups WHERE group_name = ?",
                (group_name,),
            ) as existing_cursor:
                existing = await existing_cursor.fetchone()
            cursor = await db.execute(
                """
                INSERT INTO ignored_groups (
                    group_name, ignored_at, status, class_code, cohort_key,
                    ended_at, archived_at, updated_at
                ) VALUES (?, ?, 'ENDED_CONFIRMED', ?, ?, ?, NULL, ?)
                ON CONFLICT(group_name) DO UPDATE SET
                    status = 'ENDED_CONFIRMED',
                    class_code = excluded.class_code,
                    cohort_key = COALESCE(excluded.cohort_key, ignored_groups.cohort_key),
                    ended_at = CASE
                        WHEN ignored_groups.status = 'ENDED_CONFIRMED'
                         AND ignored_groups.ended_at IS NOT NULL
                        THEN ignored_groups.ended_at
                        ELSE excluded.ended_at
                    END,
                    archived_at = NULL,
                    updated_at = excluded.updated_at
                WHERE ignored_groups.status <> 'ARCHIVED'
                """,
                (
                    group_name,
                    timestamp,
                    class_code,
                    cohort_key or None,
                    timestamp,
                    timestamp,
                ),
            )
            if cursor.rowcount > 0:
                await db.execute(
                    """
                    UPDATE class_cohorts
                    SET status = 'ENDED_CONFIRMED',
                        ended_at = CASE
                            WHEN status = 'ENDED_CONFIRMED' AND ended_at IS NOT NULL
                            THEN ended_at
                            ELSE ?
                        END,
                        archived_at = NULL,
                        updated_at = ?
                    WHERE cohort_key = ? AND status <> 'ARCHIVED'
                      AND current_group_name = ?
                      AND current_class_code = ?
                    """,
                    (
                        timestamp,
                        timestamp,
                        cohort_key,
                        group_name,
                        class_code,
                    ),
                )
            if cursor.rowcount > 0 and (
                existing is None or existing[0] != "ENDED_CONFIRMED"
            ):
                changed += 1
        return changed

    @staticmethod
    async def _mark_missing_cohorts(
        db,
        lifecycle_records: list[dict[str, Any]],
        timestamp: str,
    ) -> None:
        observed_keys_set: set[str] = set()
        for record in lifecycle_records:
            if not isinstance(record, dict):
                continue
            cohort_key = record.get("cohort_key")
            if isinstance(cohort_key, str) and cohort_key:
                observed_keys_set.add(cohort_key)
        observed_keys = sorted(observed_keys_set)
        if not observed_keys:
            return
        placeholders = ", ".join("?" for _ in observed_keys)
        await db.execute(
            f"""
            UPDATE class_cohorts
            SET status = 'PAUSED_OR_TRANSITIONING',
                missing_since = COALESCE(missing_since, ?),
                updated_at = ?
            WHERE status = 'ACTIVE'
              AND cohort_key NOT IN ({placeholders})
            """,
            (timestamp, timestamp, *observed_keys),
        )

    @staticmethod
    async def _remove_superseded_groups(
        db,
        lifecycle_records: list[dict[str, Any]],
    ) -> None:
        current_groups: dict[str, str] = {}
        ambiguous_cohorts: set[str] = set()
        for record in lifecycle_records:
            cohort_key = record.get("cohort_key") if isinstance(record, dict) else None
            group_name = record.get("group_name") if isinstance(record, dict) else None
            if not cohort_key or not group_name:
                continue
            previous_name = current_groups.get(cohort_key)
            if previous_name and previous_name != group_name:
                ambiguous_cohorts.add(cohort_key)
            else:
                current_groups[cohort_key] = group_name
        for cohort_key, group_name in current_groups.items():
            if cohort_key in ambiguous_cohorts:
                continue
            await db.execute(
                """
                DELETE FROM groups
                WHERE cohort_key = ? AND group_name <> ?
                """,
                (cohort_key, group_name),
            )

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
