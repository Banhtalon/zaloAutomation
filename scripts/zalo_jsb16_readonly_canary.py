"""Run the fixed, single-target, read-only JSB16 canary."""

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
    JSB16_CANARY_PHASE,
    JSB16_CANARY_SCHEMA_VERSION,
    CanaryBounds,
    ReadOnlyCanaryRunner,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one bounded VT-JSB16 Zalo observation. No login, QR, logout, "
            "send, form, BCNB, database, or batch-dispatch action is allowed."
        )
    )
    parser.add_argument("--profile-path", required=True)
    parser.add_argument("--profile-lock-path", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--max-runtime-seconds", type=float, default=CANARY_MAX_RUNTIME_SECONDS, help=argparse.SUPPRESS)
    parser.add_argument("--max-scrolls", type=int, default=CANARY_MAX_SCROLLS, help=argparse.SUPPRESS)
    parser.add_argument("--max-messages", type=int, default=CANARY_MAX_MESSAGES, help=argparse.SUPPRESS)
    return parser


def _absolute_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    return path.resolve()


def _safe_error(code: str) -> dict[str, object]:
    return {
        "phase": JSB16_CANARY_PHASE,
        "schema_version": JSB16_CANARY_SCHEMA_VERSION,
        "status": "BROWSER_ERROR",
        "reason_code": code,
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    profile_path = _absolute_path(args.profile_path)
    profile_lock_path = _absolute_path(args.profile_lock_path)
    artifact_root = _absolute_path(args.artifact_root)
    try:
        artifact_root.relative_to(Path(__file__).resolve().parents[1])
    except ValueError:
        pass
    else:
        raise ValueError("artifact root must be outside the repository")
    runner = ReadOnlyCanaryRunner.from_jsb16(
        profile_path=profile_path,
        profile_lock_path=profile_lock_path,
        artifact_root=artifact_root,
        bounds=CanaryBounds(
            max_runtime_seconds=args.max_runtime_seconds,
            max_scrolls=args.max_scrolls,
            max_messages=args.max_messages,
        ),
    )
    return await runner.run()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
