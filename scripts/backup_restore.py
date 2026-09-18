"""Local/dev SQLite backup and restore-drill command.

Examples:
  python scripts/backup_restore.py backup --source database.db
  python scripts/backup_restore.py restore-drill --source database.db
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.backup import backup_job, restore_drill  # noqa: E402
from db.database import current_db_path  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SQLite backup/restore drill")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup")
    backup.add_argument("--source", default=current_db_path(), help="SQLite source path")
    backup.add_argument("--root", default=".backups/db")
    backup.add_argument("--keep", type=int, default=7)
    backup.add_argument("--label", default="scheduled")

    drill = subparsers.add_parser("restore-drill")
    drill.add_argument("--source", default=current_db_path(), help="SQLite source path")
    drill.add_argument("--destination", default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    source = Path(args.source).expanduser().resolve()
    if args.command == "backup":
        result = backup_job(source, args.root, keep_count=args.keep, label=args.label)
    else:
        destination = (
            Path(args.destination).expanduser().resolve()
            if args.destination
            else Path(".backups") / "restore-drill.sqlite3"
        )
        result = restore_drill(source, destination)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
