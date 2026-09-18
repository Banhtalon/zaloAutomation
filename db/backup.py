"""Consistent SQLite backup, hash and restore-drill helpers.

The application uses SQLite WAL mode. Copying database.db directly can
leave a backup without its -wal contents, so this module always uses the
SQLite online-backup API and verifies the resulting artifact before returning.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import sqlite3

from core.time_utils import now_utc, utc_iso


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    source_path: str
    destination_path: str
    sha256: str
    size_bytes: int
    integrity: str
    foreign_key_errors: tuple[tuple[object, ...], ...]
    created_at: str

    def as_dict(self) -> dict:
        return asdict(self)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _verify(path: Path) -> tuple[str, tuple[tuple[object, ...], ...]]:
    conn = sqlite3.connect(path, timeout=10)
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = tuple(
            tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()
        )
        return integrity, foreign_keys
    finally:
        conn.close()


def backup_database(source_path: str | Path, destination_path: str | Path) -> BackupArtifact:
    """Create and verify a consistent online backup of source_path."""
    source = Path(source_path).expanduser().resolve()
    destination = Path(destination_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Khong tim thay SQLite source: {source}")
    if source == destination:
        raise ValueError("Backup destination phai khac source database.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    source_conn = sqlite3.connect(source, timeout=10)
    destination_conn = sqlite3.connect(destination, timeout=10)
    try:
        destination_conn.execute("PRAGMA journal_mode=DELETE")
        source_conn.backup(destination_conn)
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()

    integrity, foreign_keys = _verify(destination)
    if integrity.lower() != "ok" or foreign_keys:
        raise RuntimeError(
            f"SQLite backup verification failed: integrity={integrity}, "
            f"foreign_keys={foreign_keys}"
        )
    return BackupArtifact(
        source_path=str(source),
        destination_path=str(destination),
        sha256=sha256_file(destination),
        size_bytes=destination.stat().st_size,
        integrity=integrity,
        foreign_key_errors=foreign_keys,
        created_at=utc_iso(),
    )


def restore_drill(
    source_path: str | Path,
    restore_path: str | Path,
    *,
    run_schema_smoke: bool = True,
) -> dict:
    """Restore to a disposable path and run integrity/FK/schema smoke checks."""
    artifact = backup_database(source_path, restore_path)
    restored = Path(artifact.destination_path)
    if run_schema_smoke:
        # Import lazily to keep db.database independent from this utility.
        from db.database import init_db

        init_db(str(restored))
    integrity, foreign_keys = _verify(restored)
    conn = sqlite3.connect(restored, timeout=10)
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        migration_count = (
            int(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])
            if "schema_migrations" in tables
            else 0
        )
        smoke = {
            "scan_jobs": int(conn.execute("SELECT COUNT(*) FROM scan_jobs").fetchone()[0]),
            "external_action_audit": int(
                conn.execute("SELECT COUNT(*) FROM external_action_audit").fetchone()[0]
            ),
            "schema_migrations": migration_count,
        }
    finally:
        conn.close()
    result = artifact.as_dict()
    # init_db() may apply forward migrations to a legacy restored copy, so
    # report the digest of the final verified restore as well as the source
    # backup digest used to create it.
    result["source_backup_sha256"] = artifact.sha256
    result["sha256"] = sha256_file(restored)
    result["size_bytes"] = restored.stat().st_size
    result.update(
        {
            "restore_path": str(restored),
            "integrity": integrity,
            "foreign_key_errors": foreign_keys,
            "tables": sorted(tables),
            "smoke": smoke,
            "verified_at": utc_iso(),
        }
    )
    if integrity.lower() != "ok" or foreign_keys or migration_count < 1:
        raise RuntimeError(f"Restore drill failed: {result}")
    return result


def retention_cleanup(root: str | Path, *, keep_count: int = 7) -> list[str]:
    """Keep newest backup DBs and return removed paths.

    Retention is explicit and only considers files named *.sqlite3 or *.db
    directly under root; source databases in other directories are never
    touched.
    """
    if keep_count < 1:
        raise ValueError("keep_count phai lon hon 0.")
    directory = Path(root).expanduser().resolve()
    candidates = [
        path
        for pattern in ("*.sqlite3", "*.db")
        for path in directory.glob(pattern)
        if path.is_file()
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    removed: list[str] = []
    for path in candidates[keep_count:]:
        path.unlink()
        removed.append(str(path))
    return removed


def backup_job(
    source_path: str | Path,
    backup_root: str | Path,
    *,
    keep_count: int = 7,
    label: str = "scheduled",
) -> dict:
    """Create timestamped backup artifact and apply bounded retention."""
    stamp = now_utc().strftime("%Y%m%d-%H%M%S")
    destination = Path(backup_root).expanduser().resolve() / f"{label}-{stamp}.sqlite3"
    artifact = backup_database(source_path, destination)
    removed = retention_cleanup(destination.parent, keep_count=keep_count)
    result = artifact.as_dict()
    result["removed_paths"] = removed
    result["retention_keep_count"] = keep_count
    return result
