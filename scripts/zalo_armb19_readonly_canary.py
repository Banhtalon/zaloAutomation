"""Run the reviewed, single-target, read-only ARMB19 canary.

The command accepts only the already-resolved worker profile binding and a
private artifact directory.  It never loads the application container,
dispatcher, repositories, database, BCNB client, or form writer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from services.zalo_readonly_canary import (
    CANARY_MAX_MESSAGES,
    CANARY_MAX_RUNTIME_SECONDS,
    CANARY_MAX_SCROLLS,
    CANARY_READY,
    CanaryBounds,
    ReadOnlyCanaryRunner,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one bounded ARMB19 Zalo observation. No login, QR, logout, "
            "send, form, BCNB, database, or batch-dispatch action is allowed."
        )
    )
    parser.add_argument(
        "--profile-path",
        required=True,
        help="absolute effective worker profile path",
    )
    parser.add_argument(
        "--profile-lock-path",
        required=True,
        help="absolute effective worker profile lock path",
    )
    parser.add_argument(
        "--artifact-root",
        required=True,
        help="absolute private evidence directory outside the repository",
    )
    parser.add_argument(
        "--max-runtime-seconds",
        type=float,
        default=CANARY_MAX_RUNTIME_SECONDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=CANARY_MAX_SCROLLS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=CANARY_MAX_MESSAGES,
        help=argparse.SUPPRESS,
    )
    return parser


def _absolute_path(value: str, *, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    return path.resolve()


def _safe_error(code: str) -> dict[str, object]:
    return {
        "phase": "ZALO_ARMB19_READ_ONLY_CANARY",
        "schema_version": "zalo-armb19-read-only-canary-v1",
        "status": "BROWSER_ERROR",
        "reason_code": code,
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    profile_path = _absolute_path(args.profile_path, label="profile path")
    profile_lock_path = _absolute_path(
        args.profile_lock_path,
        label="profile lock path",
    )
    artifact_root = _absolute_path(args.artifact_root, label="artifact root")
    repository_root = Path(__file__).resolve().parents[1]
    try:
        artifact_root.relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise ValueError("artifact root must be outside the repository")

    bounds = CanaryBounds(
        max_runtime_seconds=args.max_runtime_seconds,
        max_scrolls=args.max_scrolls,
        max_messages=args.max_messages,
    )
    runner = ReadOnlyCanaryRunner.from_production(
        profile_path=profile_path,
        profile_lock_path=profile_lock_path,
        artifact_root=artifact_root,
        bounds=bounds,
    )
    return await runner.run()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except (TypeError, ValueError):
        result = _safe_error("INVALID_CANARY_BOOTSTRAP")
        print(json.dumps(result, sort_keys=True))
        return 2
    except KeyboardInterrupt:
        result = _safe_error("CANCELLED")
        result["status"] = "CANCELLED"
        print(json.dumps(result, sort_keys=True))
        return 130

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == CANARY_READY else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
