from typing import Any

from services.report_status import identity_tokens as _identity_tokens
from services.zalo_safety import class_code_matches_group


def _short_alias_tokens(class_code: str) -> tuple[str, ...]:
    """Build a short alias such as PTI09 from VT-C4K-PTI09."""
    tokens = _identity_tokens(class_code)
    for index in range(len(tokens) - 1, -1, -1):
        token = tokens[index]
        if (
            len(token) >= 4
            and any(character.isalpha() for character in token)
            and any(character.isdigit() for character in token)
        ):
            trailing_numbers = []
            for trailing in tokens[index + 1 :]:
                if not trailing.isdigit():
                    break
                trailing_numbers.append(trailing)
            return (token, *trailing_numbers)
    return ()


def _tokens_contain(sequence: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    if not expected or len(sequence) < len(expected):
        return False
    width = len(expected)
    return any(
        sequence[index : index + width] == expected
        for index in range(len(sequence) - width + 1)
    )


def _single_code_item(items: list[dict[str, str]]) -> dict[str, str] | None:
    codes = {"-".join(_identity_tokens(item.get("ma_lop", ""))) for item in items}
    codes.discard("")
    return items[0] if len(codes) == 1 else None


def find_bcnb_match(
    zalo_name: str,
    bcnb_data: list[dict[str, str]],
) -> dict[str, str] | None:
    """Match full identity first, then a unique short alias.

    If two full BCNB codes share an alias, matching fails closed instead of
    choosing one.
    """
    strict_matches = [
        item
        for item in bcnb_data
        if class_code_matches_group(item.get("ma_lop", ""), zalo_name)
    ]
    strict_match = _single_code_item(strict_matches)
    if strict_match:
        return strict_match
    if strict_matches:
        return None

    alias_codes: dict[tuple[str, ...], set[str]] = {}
    for item in bcnb_data:
        alias = _short_alias_tokens(item.get("ma_lop", ""))
        normalized_code = "-".join(_identity_tokens(item.get("ma_lop", "")))
        if alias and normalized_code:
            alias_codes.setdefault(alias, set()).add(normalized_code)

    group_tokens = _identity_tokens(zalo_name)
    alias_matches = [
        item
        for item in bcnb_data
        if (
            (alias := _short_alias_tokens(item.get("ma_lop", "")))
            and len(alias_codes.get(alias, set())) == 1
            and _tokens_contain(group_tokens, alias)
        )
    ]
    return _single_code_item(alias_matches)


def find_scan_result_matches(
    class_code: str,
    scan_results: list[dict[str, Any]],
    bcnb_data: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Return Zalo scan rows that resolve uniquely to one BCNB class.

    Resolution uses the same full-code-first, unique-short-alias rule as
    Discovery. A short alias shared by multiple BCNB class codes matches
    nothing, so callers cannot update the wrong class.
    """
    normalized_code = "-".join(_identity_tokens(class_code))
    if not normalized_code:
        return []

    matches = []
    for result in scan_results:
        matched_class = find_bcnb_match(result.get("group", ""), bcnb_data)
        if not matched_class:
            continue
        matched_code = "-".join(_identity_tokens(matched_class.get("ma_lop", "")))
        if matched_code == normalized_code:
            matches.append(result)
    return matches
