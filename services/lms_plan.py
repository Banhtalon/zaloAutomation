import hashlib
import json
import re
import unicodedata


CLASS_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+$")
MAX_CLASS_CODES = 200
MAX_LMS_STUDENT_CLASSES = 20
MAX_LMS_STUDENTS_PER_BATCH = 200
LMS_PLAN_KIND = "lms_preview"
LMS_PLAN_VERSION = 2
LMS_PLAN_TARGET = "BCNB"
LMS_PLAN_ACTION_SET = ("BCNB_LMS_TICK",)


class LmsSafetyError(ValueError):
    def __init__(self, message: str, *, code: str = "LMS_SAFETY_ERROR"):
        super().__init__(message)
        self.code = code


def format_overdue_session_detail(raw_text: str) -> str:
    if not isinstance(raw_text, str):
        return ""
    cleaned = " ".join(raw_text.strip().split())
    if not cleaned:
        return ""
    parts = [part.strip() for part in re.split(r"[,;/]+", cleaned) if part.strip()]
    if not parts:
        return ""
    formatted_parts: list[str] = []
    for part in parts:
        if re.fullmatch(r"\d+", part):
            label = f"Buổi {int(part)}"
        elif re.match(r"(?i)^buổi\s*\d+$", part):
            num = re.search(r"\d+", part)
            label = f"Buổi {int(num.group(0))}" if num else part
        else:
            label = part
        if label not in formatted_parts:
            formatted_parts.append(label)
    return ", ".join(formatted_parts)


def merge_overdue_session_details(
    existing: str | None,
    incoming: str | None,
) -> str:
    combined_raw = ", ".join(
        part
        for part in (
            str(existing or "").strip(),
            str(incoming or "").strip(),
        )
        if part
    )
    return format_overdue_session_detail(combined_raw)


def normalize_overdue_details(
    raw_details: dict | None,
    *,
    allowed_codes: set[str] | None = None,
) -> dict[str, str]:
    if not raw_details:
        return {}
    if not isinstance(raw_details, dict):
        raise LmsSafetyError("Chi tiết buổi quá hạn LMS không hợp lệ.")
    normalized: dict[str, str] = {}
    for raw_code, raw_detail in raw_details.items():
        if not isinstance(raw_code, str) or not raw_code.strip():
            continue
        code = normalize_class_code(raw_code)
        if allowed_codes is not None and code not in allowed_codes:
            continue
        detail = format_overdue_session_detail(
            str(raw_detail) if raw_detail is not None else ""
        )
        if not detail:
            continue
        normalized[code] = merge_overdue_session_details(
            normalized.get(code), detail
        )
    return normalized


def compute_selection_digest(
    plan_digest: str,
    selected_classes_to_tick: list[str],
) -> str:
    canonical = json.dumps(
        {
            "plan_digest": str(plan_digest or ""),
            "selected_classes_to_tick": list(selected_classes_to_tick or []),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_selected_classes_to_tick(
    selected_raw,
    allowed_classes_to_tick: list[str],
) -> list[str]:
    if not isinstance(selected_raw, list):
        raise LmsSafetyError(
            "Danh sách lớp được chọn phải là một mảng.",
            code="LMS_SELECTED_CLASSES_INVALID",
        )
    if not selected_raw:
        raise LmsSafetyError(
            "Phải chọn ít nhất 1 lớp để thực hiện tick BCNB.",
            code="LMS_SELECTED_CLASSES_EMPTY",
        )
    if len(selected_raw) > MAX_CLASS_CODES:
        raise LmsSafetyError(
            "Danh sách lớp được chọn vượt quá giới hạn an toàn.",
            code="LMS_SELECTED_CLASSES_INVALID",
        )
    selected_set: set[str] = set()
    for item in selected_raw:
        try:
            code = normalize_class_code(item)
        except LmsSafetyError as exc:
            raise LmsSafetyError(
                str(exc),
                code="LMS_SELECTED_CLASSES_INVALID",
            ) from exc
        if code in selected_set:
            raise LmsSafetyError(
                f"Mã lớp được chọn bị trùng lặp: {code}",
                code="LMS_SELECTED_CLASSES_DUPLICATE",
            )
        selected_set.add(code)

    allowed_list = [normalize_class_code(c) for c in (allowed_classes_to_tick or [])]
    allowed_set = set(allowed_list)
    outside = sorted(selected_set - allowed_set)
    if outside:
        raise LmsSafetyError(
            "Mã lớp được chọn không nằm trong danh sách đủ điều kiện của kế hoạch đã ký: "
            + ", ".join(outside),
            code="LMS_SELECTED_CLASS_NOT_IN_PLAN",
        )
    return [code for code in allowed_list if code in selected_set]


def normalize_class_code(value: str) -> str:
    if not isinstance(value, str):
        raise LmsSafetyError("Mã lớp phải là chuỗi.")
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    normalized = normalized.replace("–", "-").replace("—", "-")
    normalized = re.sub(r"\s+", "", normalized)
    if not CLASS_CODE_PATTERN.fullmatch(normalized):
        raise LmsSafetyError(f"Mã lớp không hợp lệ: {value!r}")
    return normalized


def normalize_lms_student_class_codes(
    raw_codes: object = None,
    raw_single: object = None,
) -> list[str]:
    if raw_codes is not None and raw_single is not None:
        raise LmsSafetyError(
            "Chỉ được truyền class_codes hoặc class_code, không truyền đồng thời cả hai.",
            code="LMS_CLASS_CODES_INVALID",
        )
    source = raw_codes if raw_codes is not None else raw_single
    if isinstance(source, str):
        tokens: list[object] = [
            part.strip()
            for part in re.split(r"[,;\s]+", source.strip())
            if part.strip()
        ]
    elif isinstance(source, list):
        tokens = list(source)
    else:
        raise LmsSafetyError(
            "Danh sách mã lớp LMS phải là mảng hoặc chuỗi mã lớp hợp lệ.",
            code="LMS_CLASS_CODES_INVALID",
        )

    if not tokens:
        raise LmsSafetyError(
            "Phải nhập ít nhất 1 mã lớp để lấy danh sách học viên từ LMS.",
            code="LMS_CLASS_CODES_EMPTY",
        )
    if len(tokens) > MAX_LMS_STUDENT_CLASSES:
        raise LmsSafetyError(
            f"Số lượng mã lớp vượt quá giới hạn an toàn (tối đa {MAX_LMS_STUDENT_CLASSES} lớp mỗi lần).",
            code="LMS_CLASS_CODES_INVALID",
        )

    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not isinstance(token, str) or re.search(r"[,;\s]", token.strip()):
            raise LmsSafetyError(
                f"Mã lớp không hợp lệ: {token!r}",
                code="LMS_CLASS_CODES_INVALID",
            )
        try:
            code = normalize_class_code(token)
        except LmsSafetyError as exc:
            raise LmsSafetyError(
                str(exc),
                code="LMS_CLASS_CODES_INVALID",
            ) from exc
        if code in seen:
            raise LmsSafetyError(
                f"Mã lớp bị trùng lặp trong yêu cầu: {code}",
                code="LMS_CLASS_CODES_DUPLICATE",
            )
        seen.add(code)
        result.append(code)
    return result


def normalize_class_codes(raw, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(raw, list):
        raise LmsSafetyError("AI phải trả về một mảng mã lớp.")
    if not raw:
        if allow_empty:
            return []
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


def build_lms_plan(
    missing_classes,
    bcnb_rows,
    week: int,
    year: int,
    dry_run=True,
    *,
    allow_unmatched_missing: bool = False,
    allow_empty_missing: bool = False,
    overdue_details: dict[str, str] | None = None,
):
    missing = normalize_class_codes(
        list(missing_classes), allow_empty=allow_empty_missing
    )
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
    normalized_overdue_details = normalize_overdue_details(
        overdue_details,
        allowed_codes=missing_set,
    )
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
    if unmatched and not allow_unmatched_missing:
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
        "overdue_details": normalized_overdue_details,
        "observed_classes": observed,
        "classes_to_tick": classes_to_tick,
        "already_checked_classes": already_checked,
        "unmatched_classes": unmatched,
        "missing_but_checked_classes": missing_but_checked,
        "blocking_issues": blocking_issues,
        "can_confirm": not blocking_issues,
        "dry_run": bool(dry_run),
    }
    if allow_unmatched_missing or allow_empty_missing:
        plan["source"] = "lms_web"
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
    if plan.get("overdue_details"):
        digest_payload["overdue_details"] = dict(plan["overdue_details"])
    canonical = json.dumps(
        digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
