"""Tests for subprocess-scoped quality isolation."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"


class QualityIsolationTests(unittest.TestCase):
    def test_config_import_has_no_implicit_dotenv_load(self):
        script = (
            "import sys, types\n"
            "module = types.ModuleType('dotenv')\n"
            "def forbidden_load(*args, **kwargs):\n"
            "    raise RuntimeError('implicit dotenv load')\n"
            "module.load_dotenv = forbidden_load\n"
            "sys.modules['dotenv'] = module\n"
            "import googleform_autosubmit.config\n"
        )
        result = subprocess.run(
            [str(PYTHON), "-X", "utf8", "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_isolated_context_points_database_and_runtime_to_temp_root(self):
        with tempfile.TemporaryDirectory(prefix="quality-isolation-") as directory:
            root = Path(directory)
            env = os.environ.copy()
            env.update(
                {
                    "ZALO_QUALITY_ISOLATED": "1",
                    "DATABASE_PATH": str(root / "database.sqlite3"),
                    "RUNTIME_ROOT": str(root / "runtime"),
                    "BROWSER_PROFILE_PATH": str(root / "profile"),
                    "LOCK_ROOT": str(root / "locks"),
                    "EVIDENCE_ROOT": str(root / "evidence"),
                    "LOG_DIR": str(root / "logs"),
                    "DB_BACKUP_ROOT": str(root / "backups"),
                    "UPLOAD_ROOT": str(root / "uploads"),
                }
            )
            result = subprocess.run(
                [str(PYTHON), "-X", "utf8", "-c", "from db.database import current_db_path; print(current_db_path())"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()).resolve(), (root / "database.sqlite3").resolve())

    def test_quality_script_declares_explicit_isolated_mode(self):
        script = (ROOT / "CHECK_QUALITY.ps1").read_text(encoding="utf-8")
        self.assertIn("Isolated", script)
        self.assertIn("ZALO_QUALITY_ISOLATED", script)
        self.assertIn("DATABASE_PATH", script)
        self.assertIn("LogPath", script)
        self.assertIn("Start-Transcript", script)

    def test_isolated_mode_denies_production_persistent_browser_entry(self):
        script = (
            "import asyncio\n"
            "from services.external_gateways import PlaywrightBrowserGateway\n"
            "class Bomb:\n"
            "    def __call__(self):\n"
            "        raise AssertionError('playwright factory was entered')\n"
            "async def main():\n"
            "    gateway = PlaywrightBrowserGateway()\n"
            "    gateway._playwright_factory = Bomb()\n"
            "    try:\n"
            "        async with gateway.persistent_session():\n"
            "            pass\n"
            "    except RuntimeError as exc:\n"
            "        print(type(exc).__name__)\n"
            "        return\n"
            "    raise AssertionError('isolated browser entry was not denied')\n"
            "asyncio.run(main())\n"
        )
        env = os.environ.copy()
        env["ZALO_QUALITY_ISOLATED"] = "1"
        result = subprocess.run(
            [str(PYTHON), "-X", "utf8", "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("QualityIsolation", result.stdout)


if __name__ == "__main__":
    unittest.main()
