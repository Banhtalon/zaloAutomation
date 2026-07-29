from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.evidence_store import cleanup_old_screenshots


class EvidenceStoreTests(unittest.TestCase):
    def test_missing_root_is_a_noop(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(
                cleanup_old_screenshots(root=os.path.join(root, "missing")),
                0,
            )

    def test_removes_only_old_png_and_prunes_empty_nested_directories(self):
        with tempfile.TemporaryDirectory() as root:
            nested = Path(root, "job-1")
            nested.mkdir()
            old_png = nested / "old.png"
            new_png = Path(root, "new.png")
            ignored = Path(root, "notes.txt")
            old_png.write_bytes(b"old")
            new_png.write_bytes(b"new")
            ignored.write_text("keep", encoding="utf-8")
            old_time = (datetime.now() - timedelta(days=30)).timestamp()
            os.utime(old_png, (old_time, old_time))

            count = cleanup_old_screenshots(days=14, root=root)

            self.assertEqual(count, 1)
            self.assertFalse(old_png.exists())
            self.assertTrue(new_png.exists())
            self.assertTrue(ignored.exists())
            self.assertFalse(nested.exists())

    def test_remove_error_is_fail_closed_and_walk_error_is_logged(self):
        with tempfile.TemporaryDirectory() as root:
            old_png = Path(root, "old.png")
            old_png.write_bytes(b"old")
            old_time = (datetime.now() - timedelta(days=30)).timestamp()
            os.utime(old_png, (old_time, old_time))

            with patch("services.evidence_store.os.remove", side_effect=OSError("locked")):
                self.assertEqual(cleanup_old_screenshots(root=root), 0)
            self.assertTrue(old_png.exists())

            with (
                patch("services.evidence_store.os.walk", side_effect=OSError("walk")),
                patch("services.evidence_store.log_message") as log,
            ):
                self.assertEqual(cleanup_old_screenshots(root=root), 0)
                log.assert_called_once()
                self.assertEqual(log.call_args.kwargs["level"], "WARNING")


if __name__ == "__main__":
    unittest.main()
