import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.backup import backup_database, backup_job, restore_drill, sha256_file
from db.database import init_db


class BackupRestoreTests(unittest.TestCase):
    def test_online_backup_has_hash_and_restore_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            init_db(str(source))
            connection = sqlite3.connect(source)
            try:
                connection.execute(
                    "INSERT INTO groups(group_name, person_in_charge, class_date) "
                    "VALUES ('backup-test', 'QA', 'CN')"
                )
                connection.commit()
            finally:
                connection.close()

            destination = root / "backup.sqlite3"
            artifact = backup_database(source, destination)
            self.assertEqual(artifact.integrity, "ok")
            self.assertEqual(artifact.foreign_key_errors, ())
            self.assertEqual(artifact.sha256, sha256_file(destination))

            restored = root / "restore.sqlite3"
            result = restore_drill(source, restored)
            self.assertEqual(result["integrity"], "ok")
            self.assertEqual(result["foreign_key_errors"], ())
            self.assertGreaterEqual(result["smoke"]["schema_migrations"], 2)
            self.assertIn("groups", result["tables"])

    def test_backup_job_applies_bounded_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            init_db(str(source))
            backup_root = root / "scheduled"
            first = backup_job(source, backup_root, keep_count=1, label="test")
            self.assertTrue(Path(first["destination_path"]).is_file())
            # The timestamp has second precision; use a distinct destination
            # through the lower-level helper to exercise retention semantics.
            second = backup_root / "manual.sqlite3"
            backup_database(source, second)
            from db.backup import retention_cleanup

            removed = retention_cleanup(backup_root, keep_count=1)
            self.assertEqual(len(removed), 1)


if __name__ == "__main__":
    unittest.main()
