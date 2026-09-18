import hashlib
import json
import re
import unicodedata


CLASS_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+$")
MAX_CLASS_CODES = 200
LMS_PLAN_KIND = "lms_preview"
LMS_PLAN_VERSION = 2
LMS_PLAN_TARGET = "BCNB"
LMS_PLAN_ACTION_SET = ("BCNB_LMS_TICK",)


class LmsSafetyError(ValueError):
    pass


def normalize_class_code(value: str) -> str:
    if not isinstance(value, str):
        raise LmsSafetyError("Mã lớp phải là chuỗi.")
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    normalized = normalized.replace("–", "-").replace("—", "-")
    normalized = re.sub(r"\s+", "", normalized)
    if not CLASS_CODE_PATTERN.fullmatch(normalized):
        raise LmsSafetyError(f"Mã lớp không hợp lệ: {value!r}")
    return normalized


def normalize_class_codes(raw) -> list[str]:
    if not isinstance(raw, list):
        raise LmsSafetyError("AI phải trả về một mảng mã lớp.")
    if not raw:
        raise LmsSafetyError(
            "AI không tìm thấy mã lớp nào. Hệ thống dừng để tránh tick nhầm toàn bộ."
        )
    if len(raw) > MAX_CLASS_CODES:
        raise LmsSafetyError("Danh sách mã lớp vượt quá giới hạn an toàn.")

    result = []
    seen = set()
    for value in raw:
        code = normalize_class_code(value)
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def build_lms_plan(missing_classes, bcnb_rows, week: int, year: int, dry_run=True):
    missing = normalize_class_codes(list(missing_classes))
    if not isinstance(bcnb_rows, list) or not bcnb_rows:
        raise LmsSafetyError("Bảng BCNB không có dữ liệu lớp.")

    observed = []
    seen = set()
    for row in bcnb_rows:
        if not isinstance(row, dict):
            raise LmsSafetyError("Dữ liệu dòng BCNB không hợp lệ.")
        code = normalize_class_code(row.get("class_code", ""))
        if code in seen:
            raise LmsSafetyError(f"BCNB có mã lớp trùng lặp: {code}")
        seen.add(code)
        observed.append({"class_code": code, "checked": bool(row.get("checked"))})

    missing_set = set(missing)
    observed_map = {row["class_code"]: row["checked"] for row in observed}
    unmatched = sorted(missing_set - set(observed_map))
    missing_but_checked = sorted(
        code for code in missing_set if observed_map.get(code) is True
    )
    classes_to_tick = [
        row["class_code"]
        for row in observed
        if row["class_code"] not in missing_set and not row["checked"]
    ]
    already_checked = [
        row["class_code"]
        for row in observed
        if row["class_code"] not in missing_set and row["checked"]
    ]

    blocking_issues = []
    if unmatched:
        blocking_issues.append(
            "Mã OCR không tồn tại chính xác trên BCNB: " + ", ".join(unmatched)
        )
    if missing_but_checked:
        blocking_issues.append(
            "Lớp được OCR là chưa làm nhưng BCNB đã tick: "
            + ", ".join(missing_but_checked)
        )
    if not classes_to_tick:
        blocking_issues.append("Không có lớp nào đủ điều kiện để tick.")

    plan = {
        "kind": LMS_PLAN_KIND,
        "plan_version": LMS_PLAN_VERSION,
        "target": LMS_PLAN_TARGET,
        "action_set": list(LMS_PLAN_ACTION_SET),
        "week": int(week),
        "year": int(year),
        "missing_classes": missing,
        "observed_classes": observed,
        "classes_to_tick": classes_to_tick,
        "already_checked_classes": already_checked,
        "unmatched_classes": unmatched,
        "missing_but_checked_classes": missing_but_checked,
        "blocking_issues": blocking_issues,
        "can_confirm": not blocking_issues,
        "dry_run": bool(dry_run),
    }
    plan["plan_digest"] = compute_plan_digest(plan)
    return plan


def compute_plan_digest(plan: dict) -> str:
    digest_payload = {
        "kind": plan.get("kind"),
        # A missing version intentionally hashes differently from a current
        # preview, so confirmations created before the contract change cannot
        # be replayed silently.
        "plan_version": plan.get("plan_version", 0),
        "target": plan.get("target"),
        "action_set": plan.get("action_set", []),
        "week": plan.get("week"),
        "year": plan.get("year"),
        "dry_run": bool(plan.get("dry_run", True)),
        "missing_classes": plan.get("missing_classes", []),
        "observed_classes": plan.get("observed_classes", []),
        "classes_to_tick": plan.get("classes_to_tick", []),
        "already_checked_classes": plan.get("already_checked_classes", []),
        "unmatched_classes": plan.get("unmatched_classes", []),
        "missing_but_checked_classes": plan.get("missing_but_checked_classes", []),
        "blocking_issues": plan.get("blocking_issues", []),
        "can_confirm": bool(plan.get("can_confirm", False)),
    }
    canonical = json.dumps(
        digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
