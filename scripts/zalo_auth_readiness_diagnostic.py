"""Command-line entry point for the independent Zalo auth diagnostic."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from services.zalo_auth_readiness_diagnostic import (
    DIAGNOSTIC_CLEANUP_TIMEOUT_SECONDS,
    DIAGNOSTIC_MAX_CANDIDATES,
    DIAGNOSTIC_OBSERVATION_TIMEOUT_SECONDS,
    DiagnosticBootstrap,
    DiagnosticProfileKind,
    ZaloAuthReadinessDiagnostic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one bounded, read-only Zalo browser structural observation. "
            "No login, QR, logout, scan, or external write is performed."
        )
    )
    parser.add_argument(
        "--profile-kind",
        choices=tuple(item.value for item in DiagnosticProfileKind),
        required=True,
        help="worker uses the exact supplied profile; empty-temp creates and removes one private profile",
    )
    parser.add_argument(
        "--profile-path",
        help="absolute effective worker profile path (worker mode only)",
    )
    parser.add_argument(
        "--profile-lock-path",
        help="absolute effective worker profile lock path (worker mode only)",
    )
    parser.add_argument(
        "--artifact-root",
        required=True,
        help="absolute private directory outside the repository for structural metadata",
    )
    parser.add_argument(
        "--observation-timeout-seconds",
        type=float,
        default=DIAGNOSTIC_OBSERVATION_TIMEOUT_SECONDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--cleanup-timeout-seconds",
        type=float,
        default=DIAGNOSTIC_CLEANUP_TIMEOUT_SECONDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=DIAGNOSTIC_MAX_CANDIDATES,
        help=argparse.SUPPRESS,
    )
    return parser


def _safe_cli_error(profile_kind: str | None) -> dict[str, object]:
    return {
        "phase": "ZALO_AUTH_READINESS_DIAGNOSTIC",
        "status": "INVALID_BOOTSTRAP",
        "diagnostic_code": "INVALID_BOOTSTRAP",
        "profile_kind": profile_kind if profile_kind in {item.value for item in DiagnosticProfileKind} else None,
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    artifact_root = Path(args.artifact_root).expanduser()
    if not artifact_root.is_absolute():
        raise ValueError("artifact root must be absolute")
    bootstrap = DiagnosticBootstrap(
        profile_kind=args.profile_kind,
        profile_path=args.profile_path,
        profile_lock_path=args.profile_lock_path,
        artifact_root=artifact_root,
        observation_timeout_seconds=args.observation_timeout_seconds,
        cleanup_timeout_seconds=args.cleanup_timeout_seconds,
        max_candidates=args.max_candidates,
    )
    return await ZaloAuthReadinessDiagnostic(bootstrap).run()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except (ValueError, TypeError):
        result = _safe_cli_error(getattr(args, "profile_kind", None))
        print(json.dumps(result, sort_keys=True))
        return 2
    except KeyboardInterrupt:
        result = {
            "phase": "ZALO_AUTH_READINESS_DIAGNOSTIC",
            "status": "CANCELLED",
            "diagnostic_code": "CANCELLED",
        }
        print(json.dumps(result, sort_keys=True))
        return 130

    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

