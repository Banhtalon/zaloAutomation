import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalLauncherTests(unittest.TestCase):
    def _run_probe(self, *, process_user: str | None = None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "BCNB_USERNAME=fixture-file-user\n"
                "BCNB_PASSWORD=fixture-file-password\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.pop("BCNB_USERNAME", None)
            env.pop("BCNB_PASSWORD", None)
            if process_user is not None:
                env["BCNB_USERNAME"] = process_user
            code = (
                "from pathlib import Path; "
                "from scripts.run_local import load_local_environment; "
                "load_local_environment(Path(__import__('sys').argv[1])); "
                "from googleform_autosubmit.config import Config; "
                "expected_user=__import__('sys').argv[2]; "
                "assert Config.BCNB_USERNAME == expected_user; "
                "assert Config.BCNB_PASSWORD == 'fixture-file-password'; "
                "print('LOCAL_ENV_READY')"
            )
            expected_user = process_user or "fixture-file-user"
            return subprocess.run(
                [sys.executable, "-B", "-X", "utf8", "-c", code, str(env_file), expected_user],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_explicit_local_launcher_loads_env_before_config_import(self):
        result = self._run_probe()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "LOCAL_ENV_READY")

    def test_explicit_local_launcher_preserves_process_environment_override(self):
        result = self._run_probe(process_user="fixture-process-user")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "LOCAL_ENV_READY")


if __name__ == "__main__":
    unittest.main()
