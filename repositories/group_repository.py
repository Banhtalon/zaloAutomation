import hashlib
import json
from datetime import datetime, timedelta
import sqlite3
from typing import Any

from core.time_utils import now_utc, parse_timestamp, utc_iso
from db.database import get_db_connection
from services.class_lifecycle import (
    cohort_key_pair_from_key,
    extract_progression_signature,
    parse_bcnb_class_code,
    verify_cohort_key_for_class_code,
)
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

    @classmethod
    async def _inspect_cohort_alias_state(
        cls,
        db,
        *,
        incoming_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inspect DB and incoming plan for legacy JS/CS cohort keys and detect alias collisions.

        Mapping ``VT-J|S|NN -> VT|JS|NN`` and ``VT-C|S|NN -> VT|CS|NN`` is allowed
        only when every stored record under the legacy key has a full ``class_code``
        that parses to that exact canonical ``cohort_key`` and there is no collision
        (e.g. both legacy and canonical rows already existing independently in
        ``class_cohorts``, or multiple class identities sharing one legacy key).
        """
        async with db.execute(
            """
            SELECT cohort_key, center_code, track, cohort_number,
                   current_class_code, current_group_name, status
            FROM class_cohorts
            """
        ) as cursor:
            cohort_rows = await cursor.fetchall()
        async with db.execute(
            "SELECT cohort_key, class_code, group_name FROM class_code_history"
        ) as cursor:
            history_rows = await cursor.fetchall()
        async with db.execute(
            """
            SELECT group_name, class_code, cohort_key
            FROM groups
            WHERE cohort_key IS NOT NULL AND cohort_key != ''
            """
        ) as cursor:
            group_rows = await cursor.fetchall()
        async with db.execute(
            """
            SELECT group_name, class_code, cohort_key
            FROM ignored_groups
            WHERE cohort_key IS NOT NULL AND cohort_key != ''
            """
        ) as cursor:
            ignored_rows = await cursor.fetchall()

        cohort_by_key: dict[str, dict[str, Any]] = {}
        for row in cohort_rows:
            ck = str(row[0] or "").strip()
            if ck:
                cohort_by_key[ck] = {
                    "cohort_key": ck,
                    "center_code": str(row[1] or "").strip(),
                    "track": str(row[2] or "").strip(),
                    "cohort_number": str(row[3] or "").strip(),
                    "current_class_code": str(row[4] or "").strip(),
                    "current_group_name": str(row[5] or "").strip(),
                    "status": str(row[6] or "").strip(),
                }

        observations_by_key: dict[str, list[tuple[str, str]]] = {}

        def _add_obs(raw_ck: Any, raw_cc: Any, source_label: str) -> None:
            ck = str(raw_ck or "").strip()
            cc = str(raw_cc or "").strip()
            if not ck:
                return
            observations_by_key.setdefault(ck, []).append((cc, source_label))

        for row in cohort_rows:
            _add_obs(row[0], row[4], "class_cohorts")
        for row in history_rows:
            _add_obs(row[0], row[1], "class_code_history")
        for row in group_rows:
            _add_obs(row[2], row[1], "groups")
        for row in ignored_rows:
            _add_obs(row[2], row[1], "ignored_groups")

        if isinstance(incoming_plan, dict):
            for grp in incoming_plan.get("discovered_groups") or []:
                if isinstance(grp, dict):
                    _add_obs(grp.get("CohortKey"), grp.get("MaLop"), "plan_groups")
            for rec in incoming_plan.get("lifecycle_records") or []:
                if isinstance(rec, dict):
                    _add_obs(rec.get("cohort_key"), rec.get("class_code"), "plan_records")
            for al in incoming_plan.get("lifecycle_aliases") or []:
                if isinstance(al, dict):
                    _add_obs(al.get("cohort_key"), al.get("class_code"), "plan_aliases")
            for fin in incoming_plan.get("bcnb_finished_groups") or []:
                if isinstance(fin, dict):
                    _add_obs(fin.get("cohort_key"), fin.get("class_code"), "plan_finished")

        verified_legacy_to_canonical: dict[str, dict[str, str]] = {}
        canonical_to_legacy: dict[str, str] = {}
        collided_keys: set[str] = set()
        collisions: list[dict[str, Any]] = []

        def _mark_collision(leg_k: str, can_k: str, reason: str) -> None:
            if leg_k:
                collided_keys.add(leg_k)
            if can_k:
                collided_keys.add(can_k)
            collisions.append(
                {
                    "legacy_cohort_key": leg_k,
                    "canonical_cohort_key": can_k,
                    "reason": reason,
                }
            )

        def _obs_matches_pair(
            cc: str,
            src: str,
            canon_k: str,
            leg_k: str,
            expected_track: str,
            expected_num: str,
        ):
            identity = parse_bcnb_class_code(cc)
            if identity is not None:
                if (
                    identity.cohort_key == canon_k
                    and identity.legacy_cohort_key == leg_k
                ):
                    return True, identity
                return False, None
            if src in {"class_code_history", "ignored_groups", "plan_aliases"} and cc:
                sig = extract_progression_signature(cc)
                norm_cc = "".join(cc.upper().split())
                if (
                    sig is not None
                    and norm_cc == f"{sig[0]}{sig[1]}{sig[2]}"
                    and sig[0] == expected_track
                    and sig[2] == expected_num
                ):
                    return True, None
            return False, None

        candidate_pairs: set[tuple[str, str]] = set()
        for ck in set(observations_by_key) | set(cohort_by_key):
            canon_k, leg_k = cohort_key_pair_from_key(ck)
            if leg_k and canon_k and canon_k != leg_k:
                candidate_pairs.add((canon_k, leg_k))

        legacies_per_canon: dict[str, set[str]] = {}
        canons_per_legacy: dict[str, set[str]] = {}
        for canon_k, leg_k in sorted(candidate_pairs):
            legacies_per_canon.setdefault(canon_k, set()).add(leg_k)
            canons_per_legacy.setdefault(leg_k, set()).add(canon_k)

        for canon_k, leg_k in sorted(candidate_pairs):
            if len(legacies_per_canon.get(canon_k, set())) > 1 or len(
                canons_per_legacy.get(leg_k, set())
            ) > 1:
                _mark_collision(leg_k, canon_k, "MANY_TO_ONE_ALIAS_CONFLICT")
                continue

            leg_obs = observations_by_key.get(leg_k, [])
            canon_obs = observations_by_key.get(canon_k, [])
            if not leg_obs and not canon_obs:
                continue

            if leg_k in cohort_by_key and canon_k in cohort_by_key:
                _mark_collision(
                    leg_k,
                    canon_k,
                    "BOTH_LEGACY_AND_CANONICAL_COHORTS_EXIST",
                )
                continue

            canon_parts = canon_k.split("|")
            expected_track = canon_parts[1] if len(canon_parts) == 3 else ""
            expected_num = canon_parts[2] if len(canon_parts) == 3 else ""

            parsed_identities = []
            mismatch_found = False
            for cc, src in leg_obs:
                ok, identity = _obs_matches_pair(
                    cc, src, canon_k, leg_k, expected_track, expected_num
                )
                if not ok:
                    mismatch_found = True
                    break
                if identity is not None:
                    parsed_identities.append(identity)

            if leg_obs and (mismatch_found or not parsed_identities):
                has_canon_activity = canon_k in observations_by_key or canon_k in cohort_by_key
                distinct_resolved = {
                    parsed.cohort_key
                    if (parsed := parse_bcnb_class_code(cc)) is not None
                    else f"UNPARSED:{cc}"
                    for cc, _ in leg_obs
                }
                if has_canon_activity or len(distinct_resolved) > 1 or any(
                    r == canon_k for r in distinct_resolved
                ):
                    _mark_collision(
                        leg_k,
                        canon_k,
                        "STORED_CLASS_IDENTITY_MISMATCH",
                    )
                continue

            canon_mismatch = False
            for cc, src in canon_obs:
                ok, identity = _obs_matches_pair(
                    cc, src, canon_k, leg_k, expected_track, expected_num
                )
                if not ok:
                    canon_mismatch = True
                    break
                if identity is not None:
                    parsed_identities.append(identity)
            if canon_mismatch or not parsed_identities:
                if leg_obs:
                    _mark_collision(
                        leg_k,
                        canon_k,
                        "CANONICAL_CLASS_IDENTITY_MISMATCH",
                    )
                continue

            sample_identity = parsed_identities[0]
            verified_legacy_to_canonical[leg_k] = {
                "canonical_key": canon_k,
                "legacy_key": leg_k,
                "center_code": sample_identity.center_code,
                "track": sample_identity.track,
                "cohort_number": sample_identity.cohort_number,
            }
            canonical_to_legacy[canon_k] = leg_k

        return {
            "verified_legacy_to_canonical": verified_legacy_to_canonical,
            "canonical_to_legacy": canonical_to_legacy,
            "collided_keys": collided_keys,
            "collisions": collisions,
        }

    @staticmethod
    def _equivalent_cohort_keys(
        cohort_key: str | None,
        alias_state: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        """Return verified equivalent cohort keys (canonical + legacy) without mutating DB."""
        ck = str(cohort_key or "").strip()
        if not ck:
            return ()
        if not alias_state:
            return (ck,)
        collided = alias_state.get("collided_keys") or set()
        if ck in collided:
            return (ck,)
        verified_map = alias_state.get("verified_legacy_to_canonical") or {}
        canon_to_leg = alias_state.get("canonical_to_legacy") or {}
        keys = [ck]
        if ck in verified_map:
            canon_k = verified_map[ck]["canonical_key"]
            if canon_k not in collided and canon_k not in keys:
                keys.append(canon_k)
        if ck in canon_to_leg:
            leg_k = canon_to_leg[ck]
            if leg_k not in collided and leg_k not in keys:
                keys.append(leg_k)
        return tuple(keys)

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
            alias_state = await self._inspect_cohort_alias_state(db)
            verified_map = alias_state["verified_legacy_to_canonical"]
            collided_keys = alias_state["collided_keys"]
            async with db.execute(
                """
                SELECT cohort_key, status
                FROM class_cohorts
                WHERE status <> 'ACTIVE'
                """
            ) as cohort_cursor:
                inactive_raw = await cohort_cursor.fetchall()
            inactive_keys: set[str] = set()
            for raw_ck, _status in inactive_raw:
                ck = str(raw_ck or "").strip()
                if not ck:
                    continue
                inactive_keys.add(ck)
                if ck in verified_map and ck not in collided_keys:
                    inactive_keys.add(verified_map[ck]["canonical_key"])
            async with db.execute(
                """
                SELECT g.group_name, g.person_in_charge, g.class_date,
                       g.class_code, g.cohort_key
                FROM groups AS g
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM ignored_groups AS ignored
                    WHERE ignored.group_name = g.group_name
                )
                ORDER BY g.group_name
                """
            ) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    g_code = str(row[3] or "").strip()
                    g_ck = str(row[4] or "").strip()
                    resolved_ck = g_ck
                    if (
                        g_ck in verified_map
                        and g_ck not in collided_keys
                        and verify_cohort_key_for_class_code(g_ck, g_code)
                        == verified_map[g_ck]["canonical_key"]
                    ):
                        resolved_ck = verified_map[g_ck]["canonical_key"]
                    if g_ck in inactive_keys or resolved_ck in inactive_keys:
                        continue
                    results.append(
                        {
                            "TenNhom": row[0],
                            "NguoiPhuTrach": row[1],
                            "NgayHoc": row[2],
                        }
                    )
                return results

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
        resolved_key = cohort_key
        if class_code and cohort_key:
            verified = verify_cohort_key_for_class_code(cohort_key, class_code)
            if verified:
                resolved_key = verified
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
                (group_name, class_code, resolved_key, now),
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
                alias_state = await self._inspect_cohort_alias_state(db)
                collided_keys = alias_state["collided_keys"]
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
                equiv_keys: tuple[str, ...] = ()
                if cohort_key:
                    if not class_code or cohort_key in collided_keys:
                        await db.rollback()
                        return False
                    verified_ck = verify_cohort_key_for_class_code(
                        cohort_key, class_code
                    )
                    if verified_ck and verified_ck not in collided_keys:
                        cohort_key = verified_ck
                    equiv_keys = self._equivalent_cohort_keys(
                        cohort_key, alias_state
                    )
                    placeholders = ", ".join("?" for _ in equiv_keys)
                    async with db.execute(
                        f"""
                        SELECT cohort_key
                        FROM class_cohorts
                        WHERE cohort_key IN ({placeholders})
                          AND current_group_name = ?
                          AND current_class_code = ?
                        """,
                        (*equiv_keys, group_name, class_code),
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
                if cohort_key and equiv_keys:
                    placeholders = ", ".join("?" for _ in equiv_keys)
                    await db.execute(
                        f"""
                        UPDATE class_cohorts
                        SET status = 'ACTIVE', missing_since = NULL,
                            ended_at = NULL, archived_at = NULL, updated_at = ?
                        WHERE cohort_key IN ({placeholders})
                          AND current_group_name = ?
                          AND current_class_code = ?
                        """,
                        (timestamp, *equiv_keys, group_name, class_code),
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
            alias_state = await self._inspect_cohort_alias_state(db)
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
                async with db.execute(
                    "SELECT cohort_key FROM ignored_groups WHERE group_name = ?",
                    (group_name,),
                ) as ck_cursor:
                    row = await ck_cursor.fetchone()
                ig_ck = str((row[0] if row else "") or "").strip()
                equiv_keys = self._equivalent_cohort_keys(ig_ck, alias_state)
                if equiv_keys:
                    placeholders = ", ".join("?" for _ in equiv_keys)
                    await db.execute(
                        f"""
                        UPDATE class_cohorts
                        SET status = 'ENDED_CONFIRMED', ended_at = ?, archived_at = NULL,
                            updated_at = ?
                        WHERE cohort_key IN ({placeholders})
                        """,
                        (timestamp, timestamp, *equiv_keys),
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
                    snapshot.get("group_reliable", True)
                    and previous
                    and previous[2] in ("OBSERVED_ONLY", "PROMOTED")
                    and previous[1] == snapshot["fingerprint_sha256"]
                )
                status = "PROMOTED" if consistent else "OBSERVED_ONLY"
                trust_reason = (
                    "Hai snapshot liên tiếp nhất quán; cho phép cập nhật trạng thái vắng."
                    if consistent
                    else "Snapshot đầu tiên/đã thay đổi; chỉ upsert quan sát, chưa đánh dấu vắng."
                )

                alias_state = await self._inspect_cohort_alias_state(
                    db, incoming_plan=plan
                )
                collided_keys = alias_state["collided_keys"]
                diff = await self._compute_discovery_diff(
                    db, plan, alias_state=alias_state
                )
                await self._upsert_observed_groups(
                    db,
                    plan.get("discovered_groups", []),
                    collided_keys=collided_keys,
                )
                for record in plan.get("lifecycle_records", []):
                    rec_ck = str(record.get("cohort_key") or "").strip()
                    rec_cc = str(record.get("class_code") or "").strip()
                    verified_ck = verify_cohort_key_for_class_code(rec_ck, rec_cc)
                    effective_ck = verified_ck or rec_ck
                    if rec_ck in collided_keys or effective_ck in collided_keys:
                        continue
                    normalized_record = dict(record)
                    if verified_ck:
                        parsed_id = parse_bcnb_class_code(rec_cc)
                        if parsed_id is not None:
                            normalized_record["cohort_key"] = parsed_id.cohort_key
                            normalized_record["center_code"] = parsed_id.center_code
                            normalized_record["track"] = parsed_id.track
                            normalized_record["cohort_number"] = parsed_id.cohort_number
                    await self._upsert_active_cohort(
                        db,
                        normalized_record,
                        timestamp,
                        alias_state=alias_state,
                    )
                for alias in plan.get("lifecycle_aliases", []):
                    alias_ck = str(alias.get("cohort_key") or "").strip()
                    canon_alias_ck, _ = cohort_key_pair_from_key(alias_ck)
                    if alias_ck in collided_keys or canon_alias_ck in collided_keys:
                        continue
                    normalized_alias = dict(alias)
                    if (
                        alias_ck in alias_state["verified_legacy_to_canonical"]
                        and alias_ck not in collided_keys
                    ):
                        normalized_alias["cohort_key"] = alias_state[
                            "verified_legacy_to_canonical"
                        ][alias_ck]["canonical_key"]
                    await self._upsert_history_if_cohort_exists(
                        db,
                        normalized_alias,
                        timestamp,
                        alias_state=alias_state,
                    )
                await self._apply_ignored_updates(
                    db,
                    plan,
                    timestamp,
                    include_additions=consistent,
                    alias_state=alias_state,
                )
                auto_finished_count = await self._apply_bcnb_finished_groups(
                    db,
                    plan,
                    timestamp,
                    alias_state=alias_state,
                )
                if consistent:
                    await self._mark_missing_cohorts(
                        db,
                        plan.get("lifecycle_records", []),
                        timestamp,
                        collided_keys=collided_keys,
                        alias_state=alias_state,
                    )
                    await self._remove_superseded_groups(
                        db,
                        plan.get("lifecycle_records", []),
                        collided_keys=collided_keys,
                        alias_state=alias_state,
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
                    "alias_collisions": alias_state["collisions"],
                    "alias_collision_count": len(alias_state["collisions"]),
                    "needs_review": bool(alias_state["collisions"]),
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
            alias_state = await self._inspect_cohort_alias_state(
                db, incoming_plan=plan
            )
            diff = (
                await self._compute_discovery_diff(
                    db, plan, alias_state=alias_state
                )
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
            snapshot.get("group_reliable", True)
            and snapshot["reliable"]
            and previous
            and previous[1] in ("OBSERVED_ONLY", "PROMOTED")
            and previous[0] == snapshot["fingerprint_sha256"]
        )
        return {
            **snapshot,
            "status": "PROMOTED" if would_promote else "OBSERVED_ONLY",
            "would_promote": would_promote,
            "diff": diff,
            "alias_collisions": alias_state["collisions"],
            "alias_collision_count": len(alias_state["collisions"]),
            "needs_review": bool(alias_state["collisions"]),
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
        exempt_only_plan = bool(
            (plan.get("exempt_targets") or [])
            and not (plan.get("scan_targets") or [])
            and not (plan.get("deferred_targets") or [])
            and not (plan.get("candidate_targets") or [])
        )
        group_reliable = plan_reliable and bool(
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
        reliable = bool(
            group_reliable or (plan_reliable and source_count and exempt_only_plan)
        )
        plan_json = json.dumps(plan, ensure_ascii=False, sort_keys=True)
        return {
            "reliable": reliable,
            "group_reliable": group_reliable,
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
        *,
        alias_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if alias_state is None:
            alias_state = await cls._inspect_cohort_alias_state(
                db, incoming_plan=plan
            )
        verified_map = alias_state["verified_legacy_to_canonical"]
        collided_keys = alias_state["collided_keys"]
        observed = cls._observed_group_map(plan.get("discovered_groups", []))
        async with db.execute(
            """
            SELECT group_name, person_in_charge, class_date,
                   class_code, cohort_key, lesson_number
            FROM groups
            """
        ) as cursor:
            rows = await cursor.fetchall()
        existing = {}
        for row in rows:
            if row[0] is None:
                continue
            ex_code = row[3]
            ex_ck = row[4]
            if (
                isinstance(ex_ck, str)
                and ex_ck in verified_map
                and ex_ck not in collided_keys
                and verify_cohort_key_for_class_code(ex_ck, ex_code)
                == verified_map[ex_ck]["canonical_key"]
            ):
                ex_ck = verified_map[ex_ck]["canonical_key"]
            existing[str(row[0])] = {
                "NguoiPhuTrach": row[1],
                "NgayHoc": row[2],
                "MaLop": ex_code,
                "CohortKey": ex_ck,
                "BuoiSo": row[5],
            }
        added = sorted(set(observed) - set(existing))
        not_seen = sorted(set(existing) - set(observed))
        unchanged: list[str] = []
        updated: list[str] = []
        for group_name in sorted(set(observed) & set(existing)):
            group = observed[group_name]
            obs_code = group.get("MaLop")
            obs_ck = group.get("CohortKey")
            if (
                isinstance(obs_ck, str)
                and obs_ck not in collided_keys
                and (verified_obs_ck := verify_cohort_key_for_class_code(obs_ck, obs_code))
                and verified_obs_ck not in collided_keys
            ):
                obs_ck = verified_obs_ck
            expected = {
                "NguoiPhuTrach": group.get("NguoiPhuTrach", "Chưa xác định"),
                "NgayHoc": group.get("NgayHoc", ""),
                "MaLop": obs_code,
                "CohortKey": obs_ck,
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
    async def _upsert_observed_groups(
        cls,
        db,
        groups: list[dict[str, Any]],
        *,
        collided_keys: set[str] | None = None,
    ) -> None:
        blocked = collided_keys or set()
        for group_name, group in cls._observed_group_map(groups).items():
            raw_code = group.get("MaLop")
            raw_ck = group.get("CohortKey")
            resolved_ck = raw_ck
            if isinstance(raw_ck, str) and raw_ck and raw_ck not in blocked:
                verified_ck = verify_cohort_key_for_class_code(raw_ck, raw_code)
                if verified_ck and verified_ck not in blocked:
                    resolved_ck = verified_ck
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
                    raw_code,
                    resolved_ck,
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
        alias_state: dict[str, Any] | None = None,
    ) -> None:
        verified_map = (alias_state or {}).get("verified_legacy_to_canonical") or {}
        collided_keys = (alias_state or {}).get("collided_keys") or set()
        aliases_by_name = {
            item.get("group_name"): item
            for item in plan.get("lifecycle_aliases", [])
            if isinstance(item, dict) and item.get("group_name")
        }
        if include_additions:
            for group_name in dict.fromkeys(plan.get("ignored_to_add", [])):
                alias = aliases_by_name.get(group_name, {})
                alias_ck = alias.get("cohort_key")
                if (
                    isinstance(alias_ck, str)
                    and alias_ck in verified_map
                    and alias_ck not in collided_keys
                ):
                    alias_ck = verified_map[alias_ck]["canonical_key"]
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
                        alias_ck,
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
        *,
        alias_state: dict[str, Any] | None = None,
    ) -> int:
        """Auto-confirm only exact, non-current BCNB Finished identities."""
        raw_items = plan.get("bcnb_finished_groups", [])
        if not isinstance(raw_items, list):
            raise DiscoverySnapshotError("Danh sách lớp BCNB Finished không hợp lệ.")
        collided_keys = (alias_state or {}).get("collided_keys") or set()
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
            if cohort_key in collided_keys:
                continue
            verified_ck = verify_cohort_key_for_class_code(cohort_key, class_code)
            if verified_ck:
                if verified_ck in collided_keys:
                    continue
                cohort_key = verified_ck
            equiv_keys = cls._equivalent_cohort_keys(cohort_key, alias_state)
            if not equiv_keys:
                continue
            placeholders = ", ".join("?" for _ in equiv_keys)
            async with db.execute(
                f"""
                SELECT 1
                FROM class_cohorts
                WHERE cohort_key IN ({placeholders})
                  AND current_group_name = ?
                  AND current_class_code = ?
                  AND status <> 'ARCHIVED'
                """,
                (*equiv_keys, group_name, class_code),
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
                    f"""
                    UPDATE class_cohorts
                    SET status = 'ENDED_CONFIRMED',
                        ended_at = CASE
                            WHEN status = 'ENDED_CONFIRMED' AND ended_at IS NOT NULL
                            THEN ended_at
                            ELSE ?
                        END,
                        archived_at = NULL,
                        updated_at = ?
                    WHERE cohort_key IN ({placeholders}) AND status <> 'ARCHIVED'
                      AND current_group_name = ?
                      AND current_class_code = ?
                    """,
                    (
                        timestamp,
                        timestamp,
                        *equiv_keys,
                        group_name,
                        class_code,
                    ),
                )
            if cursor.rowcount > 0 and (
                existing is None or existing[0] != "ENDED_CONFIRMED"
            ):
                changed += 1
        return changed

    @classmethod
    async def _mark_missing_cohorts(
        cls,
        db,
        lifecycle_records: list[dict[str, Any]],
        timestamp: str,
        *,
        collided_keys: set[str] | None = None,
        alias_state: dict[str, Any] | None = None,
    ) -> None:
        blocked = collided_keys or set()
        observed_keys_set: set[str] = set()
        for record in lifecycle_records:
            if not isinstance(record, dict):
                continue
            cohort_key = record.get("cohort_key")
            class_code = record.get("class_code")
            if isinstance(cohort_key, str) and cohort_key:
                observed_keys_set.update(
                    cls._equivalent_cohort_keys(cohort_key, alias_state)
                )
                verified_ck = verify_cohort_key_for_class_code(cohort_key, class_code)
                if verified_ck:
                    observed_keys_set.update(
                        cls._equivalent_cohort_keys(verified_ck, alias_state)
                    )
        observed_keys_set.update(blocked)
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

    @classmethod
    async def _remove_superseded_groups(
        cls,
        db,
        lifecycle_records: list[dict[str, Any]],
        *,
        collided_keys: set[str] | None = None,
        alias_state: dict[str, Any] | None = None,
    ) -> None:
        blocked = collided_keys or set()
        current_groups: dict[str, str] = {}
        ambiguous_cohorts: set[str] = set()
        for record in lifecycle_records:
            if not isinstance(record, dict):
                continue
            cohort_key = record.get("cohort_key")
            class_code = record.get("class_code")
            group_name = record.get("group_name")
            if not cohort_key or not group_name:
                continue
            if cohort_key in blocked:
                continue
            verified_ck = verify_cohort_key_for_class_code(cohort_key, class_code)
            if verified_ck:
                if verified_ck in blocked:
                    continue
                cohort_key = verified_ck
            previous_name = current_groups.get(cohort_key)
            if previous_name and previous_name != group_name:
                ambiguous_cohorts.add(cohort_key)
            else:
                current_groups[cohort_key] = group_name
        for cohort_key, group_name in current_groups.items():
            if cohort_key in ambiguous_cohorts or cohort_key in blocked:
                continue
            equiv_keys = cls._equivalent_cohort_keys(cohort_key, alias_state)
            if not equiv_keys or any(k in blocked for k in equiv_keys):
                continue
            placeholders = ", ".join("?" for _ in equiv_keys)
            await db.execute(
                f"""
                DELETE FROM groups
                WHERE cohort_key IN ({placeholders}) AND group_name <> ?
                """,
                (*equiv_keys, group_name),
            )

    @classmethod
    async def _upsert_active_cohort(
        cls,
        db,
        record: dict[str, Any],
        timestamp: str,
        *,
        alias_state: dict[str, Any] | None = None,
    ) -> None:
        equiv_keys = cls._equivalent_cohort_keys(
            record.get("cohort_key"), alias_state
        )
        existing_ck: str | None = None
        if equiv_keys:
            placeholders = ", ".join("?" for _ in equiv_keys)
            async with db.execute(
                f"SELECT cohort_key FROM class_cohorts WHERE cohort_key IN ({placeholders}) LIMIT 1",
                tuple(equiv_keys),
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                existing_ck = str(row[0] or "").strip() or None

        if existing_ck is not None:
            await db.execute(
                """
                UPDATE class_cohorts
                SET current_class_code = ?,
                    current_group_name = ?,
                    current_lesson_number = ?,
                    status = 'ACTIVE',
                    last_seen_bcnb_at = ?,
                    missing_since = NULL,
                    ended_at = NULL,
                    archived_at = NULL,
                    updated_at = ?
                WHERE cohort_key = ?
                """,
                (
                    record["class_code"],
                    record["group_name"],
                    record.get("lesson_number"),
                    timestamp,
                    timestamp,
                    existing_ck,
                ),
            )
        else:
            await db.execute(
                """
                INSERT INTO class_cohorts (
                    cohort_key, center_code, track, cohort_number,
                    current_class_code, current_group_name, current_lesson_number,
                    status, last_seen_bcnb_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
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

        lookup_keys = equiv_keys or (str(record["cohort_key"]),)
        hist_placeholders = ", ".join("?" for _ in lookup_keys)
        async with db.execute(
            f"""
            SELECT id
            FROM class_code_history
            WHERE cohort_key IN ({hist_placeholders})
              AND class_code = ?
              AND group_name = ?
            LIMIT 1
            """,
            (*lookup_keys, record["class_code"], record["group_name"]),
        ) as hist_cursor:
            hist_row = await hist_cursor.fetchone()
        if hist_row is not None:
            await db.execute(
                """
                UPDATE class_code_history
                SET last_seen_at = ?, last_lesson_number = ?
                WHERE id = ?
                """,
                (timestamp, record.get("lesson_number"), hist_row[0]),
            )
        else:
            target_ck = existing_ck or record["cohort_key"]
            await db.execute(
                """
                INSERT INTO class_code_history (
                    cohort_key, class_code, group_name, first_seen_at,
                    last_seen_at, last_lesson_number
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    target_ck,
                    record["class_code"],
                    record["group_name"],
                    timestamp,
                    timestamp,
                    record.get("lesson_number"),
                ),
            )

    @classmethod
    async def _upsert_history_if_cohort_exists(
        cls,
        db,
        record: dict[str, Any],
        timestamp: str,
        *,
        alias_state: dict[str, Any] | None = None,
    ) -> None:
        equiv_keys = cls._equivalent_cohort_keys(
            record.get("cohort_key"), alias_state
        )
        if not equiv_keys:
            return
        placeholders = ", ".join("?" for _ in equiv_keys)
        async with db.execute(
            f"SELECT cohort_key FROM class_cohorts WHERE cohort_key IN ({placeholders}) LIMIT 1",
            tuple(equiv_keys),
        ) as cursor:
            exists = await cursor.fetchone()
        if not exists:
            return
        existing_ck = str(exists[0] or "").strip() or record["cohort_key"]
        async with db.execute(
            f"""
            SELECT id
            FROM class_code_history
            WHERE cohort_key IN ({placeholders})
              AND class_code = ?
              AND group_name = ?
            LIMIT 1
            """,
            (*equiv_keys, record["class_code"], record["group_name"]),
        ) as hist_cursor:
            hist_row = await hist_cursor.fetchone()
        if hist_row is not None:
            await db.execute(
                "UPDATE class_code_history SET last_seen_at = ? WHERE id = ?",
                (timestamp, hist_row[0]),
            )
        else:
            await db.execute(
                """
                INSERT INTO class_code_history (
                    cohort_key, class_code, group_name, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    existing_ck,
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
            alias_state = await self._inspect_cohort_alias_state(db)
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
        verified_map = alias_state["verified_legacy_to_canonical"]
        collided = alias_state["collided_keys"]
        return [
            {
                "cohort_key": (
                    verified_map[str(row[0] or "").strip()]["canonical_key"]
                    if str(row[0] or "").strip() in verified_map
                    and str(row[0] or "").strip() not in collided
                    else row[0]
                ),
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
        requested_key = str(cohort_key or "").strip()
        async with get_db_connection() as db:
            alias_state = await self._inspect_cohort_alias_state(db)
            lookup_keys = [requested_key]
            if requested_key and requested_key not in alias_state["collided_keys"]:
                if requested_key in alias_state["verified_legacy_to_canonical"]:
                    canon_k = alias_state["verified_legacy_to_canonical"][requested_key][
                        "canonical_key"
                    ]
                    if canon_k not in lookup_keys:
                        lookup_keys.append(canon_k)
                if requested_key in alias_state["canonical_to_legacy"]:
                    leg_k = alias_state["canonical_to_legacy"][requested_key]
                    if leg_k not in lookup_keys:
                        lookup_keys.append(leg_k)
            placeholders = ", ".join("?" for _ in lookup_keys)
            async with db.execute(
                f"""
                SELECT class_code, group_name, first_seen_at, last_seen_at,
                       last_lesson_number
                FROM class_code_history
                WHERE cohort_key IN ({placeholders})
                ORDER BY first_seen_at, id
                """,
                tuple(lookup_keys),
            ) as cursor:
                rows = await cursor.fetchall()
        deduped: list[dict[str, Any]] = []
        by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            pair = (str(row[0] or "").strip(), str(row[1] or "").strip())
            if pair in by_pair:
                existing = by_pair[pair]
                if row[2] and (not existing["first_seen_at"] or row[2] < existing["first_seen_at"]):
                    existing["first_seen_at"] = row[2]
                if row[3] and (not existing["last_seen_at"] or row[3] > existing["last_seen_at"]):
                    existing["last_seen_at"] = row[3]
                if row[4] is not None and (
                    existing["lesson_number"] is None or int(row[4]) >= int(existing["lesson_number"])
                ):
                    existing["lesson_number"] = row[4]
                continue
            item = {
                "class_code": row[0],
                "group_name": row[1],
                "first_seen_at": row[2],
                "last_seen_at": row[3],
                "lesson_number": row[4],
            }
            by_pair[pair] = item
            deduped.append(item)
        return deduped
