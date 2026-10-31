import hashlib
import json
import re
import unicodedata
from datetime import datetime

from services.class_lifecycle import (
    ClassIdentity,
    extract_progression_signature,
    normalize_lesson_number,
    parse_bcnb_class_code,
)
from services.report_status import is_lesson_14
from services.zalo_discovery_matching import find_bcnb_match
from services.zalo_target_eligibility import (
    _parse_target_day,
    split_scan_targets_with_deduped,
)


DISCOVERY_PLAN_VERSION = 4
MIN_DISCOVERY_COVERAGE = 0.6
MIN_RECOGNIZED_TO_EXISTING = 0.75

MATCHED = "MATCHED"
MISSING_ZALO_GROUP = "MISSING_ZALO_GROUP"
AMBIGUOUS_ZALO_GROUP = "AMBIGUOUS_ZALO_GROUP"
TRANSITION_REVIEW = "TRANSITION_REVIEW"

DROPPED_ROW_GROUP_KEYS: tuple[str, ...] = (
    "transition_review",
    "missing_zalo",
    "ambiguous_zalo",
    "deduped",
    "invalid_date",
    "skipped_empty",
)


class BcnbWeekRowList(list):
    """List of BCNB week rows that also records empty rows skipped during DOM extraction."""

    def __init__(self, *args, skipped_empty: list[dict] | None = None):
        super().__init__(*args)
        self.skipped_empty: list[dict] = list(skipped_empty or [])


def build_dropped_rows_summary(
    *,
    transition_review: list[dict] | None = None,
    missing_zalo: list[dict] | None = None,
    ambiguous_zalo: list[dict] | None = None,
    deduped: list[dict] | None = None,
    invalid_date: list[dict] | None = None,
    skipped_empty: list[dict] | None = None,
) -> dict:
    deduped_items = list(deduped or [])
    canonical_groups = {
        "transition_review": list(transition_review or []),
        "missing_zalo": list(missing_zalo or []),
        "ambiguous_zalo": list(ambiguous_zalo or []),
        "deduped": deduped_items,
        "invalid_date": list(invalid_date or []),
        "skipped_empty": list(skipped_empty or []),
    }
    counts = {key: len(items) for key, items in canonical_groups.items()}
    counts["total"] = sum(counts.values())
    counts["deduped_targets"] = len(deduped_items)
    groups = {
        **canonical_groups,
        "deduped_targets": deduped_items,
    }
    return {
        **groups,
        "groups": dict(groups),
        "counts": counts,
    }


_DESCRIPTIVE_GROUP_WORDS = frozenset(
    {
        "MINDX",
        "LOP",
        "NHOM",
        "KHOA",
        "HOC",
        "PHU",
        "HUYNH",
        "GV",
        "HV",
        "LAP",
        "TRINH",
        "GAME",
        "WEB",
        "APP",
        "CODING",
        "ROBOTICS",
        "PYTHON",
        "SCRATCH",
        "T2",
        "T3",
        "T4",
        "T5",
        "T6",
        "T7",
        "CN",
    }
)


def _extract_group_class_identity(
    zalo_name: str,
    prog_token: str,
) -> ClassIdentity | None:
    """Extract full class identity from a Zalo group name when center tokens precede prog_token."""
    raw = unicodedata.normalize("NFKC", str(zalo_name or "")).strip()
    if not raw or not prog_token:
        return None
    prog_pattern = re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(prog_token) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    matches = list(prog_pattern.finditer(raw))
    if not matches:
        return None
    match = matches[-1]
    prefix_raw = raw[: match.start()]
    center_tokens: list[str] = []
    for word in re.split(r"[\W_]+", prefix_raw):
        if not word or not word.isascii() or not word.isalnum():
            continue
        if not any(ch.isalpha() for ch in word):
            continue
        upper_word = word.upper()
        if upper_word in _DESCRIPTIVE_GROUP_WORDS:
            continue
        center_tokens.append(upper_word)
    if not center_tokens:
        return None
    return parse_bcnb_class_code("-".join([*center_tokens, prog_token.upper()]))


def _resolve_transition_alias(
    zalo_name: str,
    candidates: dict[str, ClassIdentity],
    existing_by_name: dict[str, dict],
) -> tuple[ClassIdentity, str] | None:
    signature = extract_progression_signature(zalo_name)
    if not signature or not candidates:
        return None
    track, level, cohort_number = signature
    prog_token = f"{track}{level}{cohort_number}"
    existing_row = existing_by_name.get(zalo_name) or {}
    existing_identity = parse_bcnb_class_code(str(existing_row.get("MaLop") or ""))
    name_identity = _extract_group_class_identity(zalo_name, prog_token)

    if (
        name_identity is not None
        and existing_identity is not None
        and name_identity.center_code != existing_identity.center_code
    ):
        return None

    explicit_identity = name_identity or existing_identity
    if explicit_identity is not None:
        matching_candidates = [
            ident
            for ident in candidates.values()
            if ident.center_code == explicit_identity.center_code
        ]
        if len(matching_candidates) == 1:
            transition_identity = matching_candidates[0]
            if level != transition_identity.level:
                return (
                    transition_identity,
                    f"{transition_identity.center_code}-{prog_token}",
                )
        return None

    if len(candidates) == 1:
        transition_identity = next(iter(candidates.values()))
        if level != transition_identity.level:
            return transition_identity, prog_token
    return None


def build_discovery_plan(
    group_names: list[str],
    bcnb_data: list[dict[str, str]],
    ignored_groups: list[str],
    existing_groups: list[dict],
    *,
    match_group=find_bcnb_match,
    bcnb_week_plan: dict | None = None,
    bcnb_class_states: list[dict[str, str]] | None = None,
    now: datetime | None = None,
) -> dict:
    source_group_names = sorted(
        {
            str(name).strip()
            for name in group_names
            if isinstance(name, str) and str(name).strip()
        }
    )
    discovered_groups = []
    ignored_to_add = []
    ignored_to_remove = []
    lifecycle_records = []
    lifecycle_aliases = []

    existing_by_name: dict[str, dict] = {
        str(item.get("TenNhom", "")).strip(): item
        for item in existing_groups
        if isinstance(item, dict) and str(item.get("TenNhom", "")).strip()
    }
    identities_by_progression: dict[tuple[str, str], dict[str, ClassIdentity]] = {}
    for item in bcnb_data:
        parsed_identity = parse_bcnb_class_code(item.get("ma_lop", ""))
        if not parsed_identity:
            continue
        progression = (parsed_identity.track, parsed_identity.cohort_number)
        identities_by_progression.setdefault(progression, {})[
            parsed_identity.cohort_key
        ] = parsed_identity

    for zalo_name in source_group_names:
        manager_name = "Chưa xác định"
        class_date = ""
        matched_item = match_group(zalo_name, bcnb_data)
        identity: ClassIdentity | None = None
        if matched_item:
            manager_name = matched_item["giao_vien"]
            class_date = matched_item.get("ngay_hoc", "")
            identity = parse_bcnb_class_code(matched_item.get("ma_lop", ""))

        if manager_name == "Chưa xác định":
            signature = extract_progression_signature(zalo_name)
            if signature:
                track, _level, cohort_number = signature
                candidates = identities_by_progression.get((track, cohort_number), {})
                resolved_alias = _resolve_transition_alias(
                    zalo_name,
                    candidates,
                    existing_by_name,
                )
                if resolved_alias is not None:
                    transition_identity, alias_class_code = resolved_alias
                    lifecycle_aliases.append(
                        {
                            "cohort_key": transition_identity.cohort_key,
                            "class_code": alias_class_code,
                            "group_name": zalo_name,
                        }
                    )
                    if zalo_name not in ignored_groups:
                        ignored_to_add.append(zalo_name)
                    continue
            if zalo_name not in ignored_groups:
                ignored_to_add.append(zalo_name)
            continue

        if zalo_name in ignored_groups:
            ignored_to_remove.append(zalo_name)
        discovered_group = {
            "TenNhom": zalo_name,
            "NguoiPhuTrach": manager_name,
            "NgayHoc": class_date,
            "MaLop": matched_item.get("ma_lop", "") if matched_item else "",
            "BuoiSo": normalize_lesson_number(
                matched_item.get("buoi_so") if matched_item else None
            ),
            "CohortKey": identity.cohort_key if identity else None,
        }
        discovered_groups.append(discovered_group)
        if identity:
            lifecycle_records.append(
                {
                    "cohort_key": identity.cohort_key,
                    "center_code": identity.center_code,
                    "track": identity.track,
                    "cohort_number": identity.cohort_number,
                    "class_code": identity.class_code,
                    "group_name": zalo_name,
                    "lesson_number": discovered_group["BuoiSo"],
                }
            )

    recognized_count = len(discovered_groups) + len(lifecycle_aliases)
    source_count = len(source_group_names)
    match_ratio = recognized_count / source_count if source_count else 0
    existing_count = len(existing_groups)
    recognized_to_existing = (
        recognized_count / existing_count if existing_count else 1.0
    )
    unexpectedly_small = bool(existing_count) and (
        recognized_to_existing < MIN_RECOGNIZED_TO_EXISTING
    )
    recognized_signatures = sorted(
        {
            (
                item.get("TenNhom", ""),
                item.get("MaLop", ""),
                item.get("CohortKey") or "",
            )
            for item in discovered_groups
        }
        | {
            (
                item.get("group_name", ""),
                item.get("class_code", ""),
                item.get("cohort_key", ""),
            )
            for item in lifecycle_aliases
        }
    )
    fingerprint_payload = {
        "version": DISCOVERY_PLAN_VERSION,
        "source_group_names": source_group_names,
        "recognized_signatures": recognized_signatures,
    }
    fingerprint_sha256 = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    coverage_rows = _coverage_rows(bcnb_data, bcnb_week_plan)
    bcnb_zalo_coverage = _build_bcnb_zalo_coverage(
        source_group_names,
        coverage_rows,
        match_group=match_group,
        existing_by_name=existing_by_name,
    )
    dropped_accumulator: dict[str, list[dict]] = {
        key: [] for key in DROPPED_ROW_GROUP_KEYS
    }
    exempt_targets: list[dict] = []
    candidate_targets = _build_scan_targets(
        group_names,
        bcnb_week_plan,
        match_group=match_group,
        coverage=bcnb_zalo_coverage,
        dropped_accumulator=dropped_accumulator,
        exempt_accumulator=exempt_targets,
    )
    exempt_only = bool(exempt_targets) and not candidate_targets
    group_reliable = bool(
        bcnb_data
        and source_group_names
        and discovered_groups
        and match_ratio >= MIN_DISCOVERY_COVERAGE
        and not unexpectedly_small
    )
    reliable = bool(
        group_reliable
        or (bcnb_data and source_group_names and exempt_only)
    )
    scan_targets, deferred_targets, deduped_targets = split_scan_targets_with_deduped(
        candidate_targets,
        latest_week=(bcnb_week_plan or {}).get("latest_week"),
        now=now,
    )
    dropped_accumulator["deduped"].extend(deduped_targets)
    if not isinstance(bcnb_week_plan, dict) or not (bcnb_week_plan.get("selected_weeks") or []):
        _populate_fallback_dropped_rows(
            bcnb_zalo_coverage,
            dropped_accumulator,
        )
    dropped_rows = build_dropped_rows_summary(**dropped_accumulator)
    observed_group_names = {
        str(item.get("TenNhom", "")).strip()
        for item in discovered_groups
        if isinstance(item, dict)
    }
    bcnb_finished_groups = _build_bcnb_finished_groups(
        source_group_names,
        bcnb_class_states or [],
        observed_group_names=observed_group_names,
        match_group=match_group,
    )
    return {
        "plan_version": DISCOVERY_PLAN_VERSION,
        "reliable": reliable,
        "group_reliable": group_reliable,
        "discovered_groups": discovered_groups,
        "ignored_to_add": ignored_to_add,
        "ignored_to_remove": ignored_to_remove,
        "lifecycle_records": lifecycle_records,
        "lifecycle_aliases": lifecycle_aliases,
        "match_ratio": match_ratio,
        "source_count": source_count,
        "recognized_count": recognized_count,
        "expected_count": max(existing_count, recognized_count),
        "existing_count": existing_count,
        "delta_count": recognized_count - existing_count,
        "coverage": match_ratio,
        "source_group_names": source_group_names,
        "fingerprint_sha256": fingerprint_sha256,
        "bcnb_week_plan": bcnb_week_plan or {},
        "scan_targets": scan_targets,
        "deferred_targets": deferred_targets,
        "exempt_targets": exempt_targets,
        "exempt_target_count": len(exempt_targets),
        "dropped_rows": dropped_rows,
        "bcnb_zalo_coverage": bcnb_zalo_coverage,
        "bcnb_finished_groups": bcnb_finished_groups,
        "bcnb_finished_source": {
            "available": bcnb_class_states is not None,
            "class_count": len(bcnb_class_states or []),
            "finished_group_count": len(bcnb_finished_groups),
            "date_semantics": "FIRST_FINISHED_OBSERVATION",
        },
    }


def _coverage_code(row: dict[str, str]) -> str:
    identity = parse_bcnb_class_code(str(row.get("ma_lop", "")))
    if identity:
        return identity.class_code
    return " ".join(str(row.get("ma_lop", "")).strip().upper().split())


def _build_bcnb_finished_groups(
    group_names: list[str],
    class_states: list[dict[str, str]],
    *,
    observed_group_names: set[str] | None = None,
    match_group=find_bcnb_match,
) -> list[dict[str, str | None]]:
    """Resolve authoritative BCNB Finished states to one Zalo identity.

    Matching runs against every BCNB class code, not just Finished codes, so
    a shared short alias can never be auto-ended. Groups still present in the
    current schedule are also excluded as contradictory active evidence.
    """
    statuses_by_code: dict[str, set[str]] = {}
    row_by_code: dict[str, dict[str, str]] = {}
    for item in class_states:
        if not isinstance(item, dict):
            continue
        code = _coverage_code({"ma_lop": str(item.get("class_code", ""))})
        status = str(item.get("class_status", "")).strip().upper()
        if not code or not status:
            continue
        statuses_by_code.setdefault(code, set()).add(status)
        row_by_code.setdefault(code, {"ma_lop": code})

    finished_codes = {
        code for code, statuses in statuses_by_code.items() if statuses == {"FINISHED"}
    }
    if not finished_codes:
        return []
    all_rows = [row_by_code[code] for code in sorted(row_by_code)]
    current_names = observed_group_names or set()
    results: list[dict[str, str | None]] = []
    for group_name in sorted(set(group_names)):
        if group_name in current_names:
            continue
        match = match_group(group_name, all_rows)
        if not match:
            continue
        class_code = _coverage_code(match)
        if class_code not in finished_codes:
            continue
        identity = parse_bcnb_class_code(class_code)
        results.append(
            {
                "group_name": group_name,
                "class_code": class_code,
                "cohort_key": identity.cohort_key if identity else None,
                "bcnb_status": "FINISHED",
                "evidence_source": "BCNB_STUDENT_CLASS_STATUS",
            }
        )
    return results


def _coverage_rows(
    bcnb_data: list[dict[str, str]], bcnb_week_plan: dict | None
) -> list[dict[str, str]]:
    """Return both selected BCNB weeks with explicit period metadata."""
    if not isinstance(bcnb_week_plan, dict):
        return [dict(row) for row in bcnb_data if isinstance(row, dict)]

    rows: list[dict[str, str]] = []
    for period_key, data_key in (
        ("required_week", "required_data"),
        ("latest_week", "latest_data"),
    ):
        period = bcnb_week_plan.get(period_key)
        if not isinstance(period, dict):
            continue
        for source in bcnb_week_plan.get(data_key) or []:
            if not isinstance(source, dict):
                continue
            row = dict(source)
            row.setdefault("week", str(period.get("week", "")))
            row.setdefault("year", str(period.get("year", "")))
            rows.append(row)
    return rows or [dict(row) for row in bcnb_data if isinstance(row, dict)]


def _build_bcnb_zalo_coverage(
    group_names: list[str],
    bcnb_data: list[dict[str, str]],
    *,
    match_group=find_bcnb_match,
    existing_by_name: dict[str, dict] | None = None,
) -> dict:
    """Class-level, deterministic BCNB-to-Zalo identity coverage.

    A class is matched only when exactly one Zalo group resolves to it. A
    unique alias inside a larger ambiguous BCNB snapshot is not promoted.
    Transition aliases remain review-only and never become scan targets.
    """
    rows_by_code: dict[str, list[dict[str, str]]] = {}
    for row in bcnb_data:
        if not isinstance(row, dict):
            continue
        code = _coverage_code(row)
        if code:
            rows_by_code.setdefault(code, []).append(row)
    records = []
    for code in sorted(rows_by_code):
        class_rows = rows_by_code[code]
        row = class_rows[0]
        single_matches = sorted(
            name for name in group_names if match_group(name, [row])
        )
        global_matches = sorted(
            name
            for name in group_names
            if (
                (matched := match_group(name, bcnb_data))
                and _coverage_code(matched) == code
            )
        )
        if len(global_matches) == 1:
            status = MATCHED
            matched_names = global_matches
        elif single_matches:
            status = AMBIGUOUS_ZALO_GROUP
            matched_names = single_matches
        else:
            cohort = parse_bcnb_class_code(str(row.get("ma_lop", "")))
            transition = False
            if cohort:
                same_prog_centers = {
                    parsed.center_code
                    for code_k in rows_by_code
                    if (parsed := parse_bcnb_class_code(code_k)) is not None
                    and parsed.track == cohort.track
                    and parsed.cohort_number == cohort.cohort_number
                }
                for name in group_names:
                    candidate = extract_progression_signature(name)
                    if (
                        candidate
                        and candidate[0] == cohort.track
                        and candidate[2] == cohort.cohort_number
                        and candidate[1] != cohort.level
                    ):
                        prog_token = f"{candidate[0]}{candidate[1]}{candidate[2]}"
                        name_identity = _extract_group_class_identity(
                            name, prog_token
                        )
                        existing_row = (existing_by_name or {}).get(name) or {}
                        existing_identity = parse_bcnb_class_code(
                            str(existing_row.get("MaLop") or "")
                        )
                        if (
                            name_identity is not None
                            and existing_identity is not None
                            and name_identity.center_code != existing_identity.center_code
                        ):
                            continue
                        explicit_identity = name_identity or existing_identity
                        if explicit_identity is not None:
                            if explicit_identity.center_code == cohort.center_code:
                                transition = True
                                break
                            continue
                        if len(same_prog_centers) == 1:
                            transition = True
                            break
            status = TRANSITION_REVIEW if transition else MISSING_ZALO_GROUP
            matched_names = []
        periods = []
        seen_periods: set[tuple[int, int, str, str]] = set()
        for source in class_rows:
            try:
                week = int(str(source.get("week", "")))
                year = int(str(source.get("year", "")))
            except ValueError:
                continue
            lesson = str(source.get("buoi_so", "")).strip()
            class_date_iso = str(source.get("ngay_hoc_iso", "")).strip()
            period_key = (week, year, lesson, class_date_iso)
            if period_key in seen_periods:
                continue
            seen_periods.add(period_key)
            periods.append(
                {
                    "week": week,
                    "year": year,
                    "lesson": lesson,
                    "class_date": str(source.get("ngay_hoc", "")).strip(),
                    "class_date_iso": class_date_iso,
                }
            )
        periods.sort(
            key=lambda item: (
                item["year"],
                item["week"],
                item["lesson"],
                item["class_date_iso"],
            )
        )
        records.append(
            {
                "class_code": code,
                "status": status,
                "zalo_group_name": matched_names[0] if len(matched_names) == 1 else None,
                "candidate_group_names": matched_names,
                "candidate_group_count": len(matched_names),
                "teachers": sorted(
                    {
                        str(source.get("giao_vien", "")).strip()
                        for source in class_rows
                        if str(source.get("giao_vien", "")).strip()
                    }
                ),
                "periods": periods,
            }
        )
    matched_count = sum(item["status"] == MATCHED for item in records)
    source_count = len(records)
    return {
        "source_count": source_count,
        "matched_count": matched_count,
        "missing_count": sum(
            item["status"] == MISSING_ZALO_GROUP for item in records
        ),
        "ambiguous_count": sum(
            item["status"] == AMBIGUOUS_ZALO_GROUP for item in records
        ),
        "transition_review_count": sum(
            item["status"] == TRANSITION_REVIEW for item in records
        ),
        "coverage": matched_count / source_count if source_count else 0.0,
        "records": records,
    }


def _populate_fallback_dropped_rows(
    coverage: dict | None,
    dropped_accumulator: dict[str, list[dict]],
) -> None:
    for item in (coverage or {}).get("records", []):
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        class_code = str(item.get("class_code") or "").strip()
        periods = item.get("periods") or [{}]
        for period in periods:
            entry = {
                "class_code": class_code,
                "lesson": str(period.get("lesson") or "").strip(),
                "class_date": str(period.get("class_date") or "").strip(),
                "class_date_iso": str(period.get("class_date_iso") or "").strip(),
                "week": period.get("week"),
                "year": period.get("year"),
                "candidate_group_names": list(item.get("candidate_group_names") or []),
                "reason": status,
            }
            if status == TRANSITION_REVIEW:
                dropped_accumulator["transition_review"].append(entry)
            elif status == AMBIGUOUS_ZALO_GROUP:
                dropped_accumulator["ambiguous_zalo"].append(entry)
            elif status == MISSING_ZALO_GROUP:
                dropped_accumulator["missing_zalo"].append(entry)


def _build_target_entry(
    row: dict,
    *,
    group_name: str,
    class_date_iso: str,
    week: int,
    year: int,
    target_kind: str = "SCAN",
) -> dict:
    target_entry: dict = {
        "group_name": group_name,
        "person": str(row.get("giao_vien", "")).strip() or "Chưa xác định",
        "class_code": str(row.get("ma_lop", "")).strip(),
        "lesson": str(row.get("buoi_so", "")).strip(),
        "class_date": str(row.get("ngay_hoc", "")).strip(),
        "class_date_iso": class_date_iso,
        "zalo_deadline": str(row.get("zalo_deadline", "")).strip(),
        "week": week,
        "year": year,
        "week_source": "BCNB_SERVER",
    }
    if target_kind != "SCAN":
        target_entry["target_kind"] = target_kind
    for ext_key in (
        "bcnb_session_id",
        "khoi",
        "lms_deadline",
        "evaluation_status",
    ):
        if ext_key in row and row.get(ext_key) is not None:
            val = str(row.get(ext_key) or "").strip()
            if val:
                target_entry[ext_key] = val
    for note_key in ("qc_issue_note", "instructor_note"):
        if note_key in row and row.get(note_key) is not None:
            target_entry[note_key] = str(row.get(note_key) or "").strip()
    for bool_key in ("nx_zalo_checked", "nx_lms_checked"):
        if bool_key in row and isinstance(row.get(bool_key), bool):
            target_entry[bool_key] = bool(row[bool_key])
    return target_entry


def _build_scan_targets(
    group_names: list[str],
    bcnb_week_plan: dict | None,
    *,
    match_group=find_bcnb_match,
    coverage: dict | None = None,
    dropped_accumulator: dict[str, list[dict]] | None = None,
    exempt_accumulator: list[dict] | None = None,
) -> list[dict]:
    """Create immutable per-week targets for the subsequent Zalo scan.

    Lifecycle promotion still uses the latest-week discovery rows.  The scan
    plan retains previous-week rows only when they still resolve to a single
    verified Zalo group (`status == MATCHED`).  Previous-week rows whose class
    code has already transitioned to a new level are routed to
    `dropped_rows["transition_review"]` for manual review rather than scanned
    under the new level's group name.  Lesson 14 rows are routed to
    `exempt_targets` so they can be auto-ticked without requiring a Zalo group
    or waiting for class end time.  Each target carries its own week/date/lesson
    so one Zalo result cannot be reused for another BCNB row.
    """
    if not isinstance(bcnb_week_plan, dict):
        return []
    dropped = dropped_accumulator if isinstance(dropped_accumulator, dict) else None
    exempt_out = exempt_accumulator if isinstance(exempt_accumulator, list) else None
    if dropped is not None:
        for skipped in bcnb_week_plan.get("skipped_empty") or []:
            if isinstance(skipped, dict):
                dropped.setdefault("skipped_empty", []).append(dict(skipped))
    selected = bcnb_week_plan.get("selected_weeks") or []
    data_by_period = {
        (
            int(str(block.get("week"))),
            int(str(block.get("year"))),
        ): bcnb_week_plan.get("required_data", [])
        if block == bcnb_week_plan.get("required_week")
        else bcnb_week_plan.get("latest_data", [])
        for block in selected
        if isinstance(block, dict)
        and str(block.get("week", "")).isdigit()
        and str(block.get("year", "")).isdigit()
    }
    targets: list[dict] = []
    coverage_records = {
        item["class_code"]: item
        for item in (coverage or {}).get("records", [])
        if isinstance(item, dict) and item.get("class_code")
    }
    matched_groups = {
        code: item["zalo_group_name"]
        for code, item in coverage_records.items()
        if item.get("status") == MATCHED and item.get("zalo_group_name")
    }
    seen_exempt_keys: set[tuple[int, int, str, str, str]] = set()
    local_exempt_entries: list[dict] = []
    for pair in selected:
        if not isinstance(pair, dict):
            continue
        try:
            week = int(pair["week"])
            year = int(pair["year"])
        except (KeyError, TypeError, ValueError):
            continue
        rows = data_by_period.get((week, year), [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            class_code = str(row.get("ma_lop", "")).strip()
            lesson = str(row.get("buoi_so", "")).strip()
            class_date = str(row.get("ngay_hoc", "")).strip()
            raw_iso = str(row.get("ngay_hoc_iso", "")).strip()
            if not class_code or not class_date:
                if dropped is not None:
                    dropped.setdefault("skipped_empty", []).append(
                        {
                            "ma_lop": class_code,
                            "class_code": class_code,
                            "buoi_so": lesson,
                            "lesson": lesson,
                            "ngay_hoc": class_date,
                            "class_date": class_date,
                            "week": week,
                            "year": year,
                            "reason": "SKIPPED_EMPTY",
                        }
                    )
                continue
            parsed_date = _parse_target_day(raw_iso or class_date)
            if parsed_date is None:
                if dropped is not None:
                    dropped.setdefault("invalid_date", []).append(
                        {
                            "class_code": class_code,
                            "lesson": lesson,
                            "class_date": class_date,
                            "class_date_iso": raw_iso,
                            "week": week,
                            "year": year,
                            "reason": "INVALID_DATE",
                        }
                    )
                continue
            class_date_iso = parsed_date.isoformat()
            code_key = _coverage_code(row)
            matches = [
                group_name
                for group_name in group_names
                if (
                    match_group(group_name, [row])
                    and matched_groups.get(code_key) == group_name
                )
            ]
            if is_lesson_14(lesson):
                matched_group_name = matches[0] if len(matches) == 1 else ""
                exempt_key = (week, year, code_key, lesson, class_date_iso)
                exempt_entry = _build_target_entry(
                    row,
                    group_name=matched_group_name,
                    class_date_iso=class_date_iso,
                    week=week,
                    year=year,
                    target_kind="EXEMPTION",
                )
                if exempt_key in seen_exempt_keys:
                    if dropped is not None:
                        dup_entry = dict(exempt_entry)
                        dup_entry["reason"] = "DUPLICATE_TARGET"
                        dropped.setdefault("deduped", []).append(dup_entry)
                else:
                    seen_exempt_keys.add(exempt_key)
                    local_exempt_entries.append(exempt_entry)
                    if exempt_out is not None:
                        exempt_out.append(exempt_entry)
                continue
            if len(matches) != 1:
                if dropped is not None:
                    cov_item = coverage_records.get(code_key, {})
                    cov_status = cov_item.get("status")
                    dropped_item = {
                        "class_code": class_code,
                        "lesson": lesson,
                        "class_date": class_date,
                        "class_date_iso": class_date_iso,
                        "week": week,
                        "year": year,
                        "candidate_group_names": list(
                            cov_item.get("candidate_group_names") or []
                        ),
                        "reason": cov_status or MISSING_ZALO_GROUP,
                    }
                    if cov_status == TRANSITION_REVIEW:
                        dropped.setdefault("transition_review", []).append(dropped_item)
                    elif cov_status == AMBIGUOUS_ZALO_GROUP:
                        dropped.setdefault("ambiguous_zalo", []).append(dropped_item)
                    else:
                        dropped.setdefault("missing_zalo", []).append(dropped_item)
                continue
            target_entry = _build_target_entry(
                row,
                group_name=matches[0],
                class_date_iso=class_date_iso,
                week=week,
                year=year,
            )
            targets.append(target_entry)
    group_date_sessions: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for item in [*targets, *local_exempt_entries]:
        g_name = str(item.get("group_name") or "").strip()
        c_date = str(item.get("class_date_iso") or "").strip()
        c_code = str(item.get("class_code") or "").strip()
        c_lesson = str(item.get("lesson") or "").strip()
        if g_name and c_date:
            group_date_sessions.setdefault((g_name, c_date), set()).add(
                (c_code, c_lesson)
            )
    for item in targets:
        g_name = str(item.get("group_name") or "").strip()
        c_date = str(item.get("class_date_iso") or "").strip()
        count = len(group_date_sessions.get((g_name, c_date), set()))
        if count > 1:
            item["same_day_target_ambiguous"] = True
            item["same_day_target_count"] = count
    return targets
