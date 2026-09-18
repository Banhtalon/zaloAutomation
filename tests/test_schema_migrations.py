import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.database import SchemaMigrationError, get_schema_migrations, init_db


class SchemaMigrationTests(unittest.TestCase):
    def test_fresh_database_records_sequential_versioned_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "fresh.sqlite3")
            init_db(path)
            migrations = get_schema_migrations(path)
            self.assertEqual([item["version"] for item in migrations], [1, 2, 3, 4])
            self.assertEqual(
                [item["name"] for item in migrations],
                [
                    "baseline_schema_v1",
                    "phase7_integrity_indexes",
                    "phase8_googleform_idempotency",
                    "local_parallel_zalo_batch_targets",
                ],
            )
            self.assertTrue(all(len(str(item["checksum"])) == 64 for item in migrations))

            # A second startup is idempotent and does not append duplicate rows.
            init_db(path)
            self.assertEqual(len(get_schema_migrations(path)), 4)

    def test_checksum_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "tampered.sqlite3")
            init_db(path)
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE schema_migrations SET checksum = ? WHERE version = 3",
                    ("0" * 64,),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(SchemaMigrationError):
                init_db(path)

    def test_v3_database_upgrades_forward_without_changing_old_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "legacy-v3.sqlite3")
            init_db(path)
            old_checksums = {
                item["version"]: item["checksum"]
                for item in get_schema_migrations(path)
                if item["version"] <= 3
            }
            connection = sqlite3.connect(path)
            try:
                connection.execute("DROP TABLE zalo_batch_targets")
                connection.execute("DELETE FROM schema_migrations WHERE version = 4")
                connection.commit()
            finally:
                connection.close()

            init_db(path)
            migrations = get_schema_migrations(path)
            self.assertEqual([item["version"] for item in migrations], [1, 2, 3, 4])
            self.assertEqual(
                {
                    item["version"]: item["checksum"]
                    for item in migrations
                    if item["version"] <= 3
                },
                old_checksums,
            )
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(zalo_batch_targets)"
                    ).fetchall()
                }
            finally:
                connection.close()
            self.assertIn("source_json", columns)
            self.assertIn("write_status", columns)


if __name__ == "__main__":
    unittest.main()
