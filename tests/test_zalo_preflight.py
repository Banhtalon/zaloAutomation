import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.zalo_preflight import _check_production_backup


class ProductionBackupPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_backup_probe_creates_and_verifies_disposable_copy(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            database_path = root_path / "source.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
                connection.commit()
            finally:
                connection.close()

            settings = SimpleNamespace(
                database_path=str(database_path),
                db_backup_root=str(root_path / "backups"),
            )
            result = await _check_production_backup(settings, SimpleNamespace())

        self.assertEqual(result["status"], "GREEN")
        self.assertEqual(result["code"], "DATABASE_BACKUP")

    async def test_backup_probe_fails_closed_when_backup_raises(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.sqlite3"
            source.touch()
            settings = SimpleNamespace(
                database_path=str(source),
                db_backup_root=str(Path(root) / "backups"),
            )
            with patch(
                "services.zalo_preflight.backup_database",
                side_effect=OSError("permission denied"),
            ):
                result = await _check_production_backup(settings, SimpleNamespace())

        self.assertEqual(result["status"], "RED")
        self.assertEqual(result["code"], "DATABASE_BACKUP")


if __name__ == "__main__":
    unittest.main()
