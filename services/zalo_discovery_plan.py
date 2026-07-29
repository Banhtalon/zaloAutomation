from services.class_lifecycle import (
    ClassIdentity,
    extract_progression_signature,
    normalize_lesson_number,
    parse_bcnb_class_code,
)
from services.zalo_discovery_matching import find_bcnb_match


def build_discovery_plan(
    group_names: list[str],
    bcnb_data: list[dict[str, str]],
    ignored_groups: list[str],
    existing_groups: list[dict],
    *,
    match_group=find_bcnb_match,
) -> dict:
    discovered_groups = []
    ignored_to_add = []
    ignored_to_remove = []
    lifecycle_records = []
    lifecycle_aliases = []

    identities_by_progression: dict[tuple[str, str], dict[str, ClassIdentity]] = {}
    for item in bcnb_data:
        parsed_identity = parse_bcnb_class_code(item.get("ma_lop", ""))
        if not parsed_identity:
            continue
        progression = (parsed_identity.track, parsed_identity.cohort_number)
        identities_by_progression.setdefault(progression, {})[
            parsed_identity.cohort_key
        ] = parsed_identity

    for zalo_name in group_names:
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
                track, level, cohort_number = signature
                candidates = identities_by_progression.get((track, cohort_number), {})
                if len(candidates) == 1:
                    transition_identity = next(iter(candidates.values()))
                    if level != transition_identity.level:
                        lifecycle_aliases.append(
                            {
                                "cohort_key": transition_identity.cohort_key,
                                "class_code": f"{track}{level}{cohort_number}",
                                "group_name": zalo_name,
                            }
                        )
                        if zalo_name in ignored_groups:
                            ignored_to_remove.append(zalo_name)
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
    match_ratio = recognized_count / len(group_names) if group_names else 0
    unexpectedly_small = bool(existing_groups) and recognized_count < max(
        1,
        len(existing_groups) // 2,
    )
    reliable = bool(
        bcnb_data
        and group_names
        and discovered_groups
        and match_ratio >= 0.5
        and not unexpectedly_small
    )
    return {
        "reliable": reliable,
        "discovered_groups": discovered_groups,
        "ignored_to_add": ignored_to_add,
        "ignored_to_remove": ignored_to_remove,
        "lifecycle_records": lifecycle_records,
        "lifecycle_aliases": lifecycle_aliases,
        "match_ratio": match_ratio,
    }
