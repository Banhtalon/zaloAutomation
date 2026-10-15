"""Secret scanning must ignore virtual environments while checking source."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import phase9a_audit


class SecretScanTests(unittest.TestCase):
    def test_venvs_are_ignored_without_hiding_source_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                "venv/Lib/package.py",
                ".venv/Lib/package.py",
                ".venv-source/package.py",
                "app.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("https://" + "example:example@localhost", encoding="utf-8")
            with patch.object(phase9a_audit, "ROOT", root):
                result = phase9a_audit.secret_scan()
            self.assertFalse(result["ok"])
            self.assertEqual(
                {finding["path"] for finding in result["findings"]},
                {".venv-source/package.py", "app.py"},
            )
