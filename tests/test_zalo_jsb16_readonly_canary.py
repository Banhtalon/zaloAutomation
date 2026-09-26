"""Contract checks for the fixed JSB16 read-only canary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from services.zalo_readonly_canary import (
    CANARY_DESCRIPTOR,
    JSB16_DESCRIPTOR,
    ReadOnlyCanaryRunner,
)


class Jsb16CanaryContractTests(unittest.IsolatedAsyncioTestCase):
    def test_descriptor_is_fixed_to_job_277_target(self):
        self.assertEqual(
            JSB16_DESCRIPTOR.as_dict(),
            {
                "class_code": "VT-JSB16",
                "group_name": "MindX - Lập trình WEB VT-JSB16 CN (14H-16H)",
                "week": 38,
                "year": 2026,
                "lesson": "6",
                "class_date_iso": "2026-09-20",
                "message_window_start": "2026-09-12",
                "message_window_end": "2026-09-22",
            },
        )
        self.assertEqual(JSB16_DESCRIPTOR.zalo_deadline, "2026-09-22T16:00:00+07:00")

    async def test_factory_is_fixed_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory(prefix="jsb16-canary-") as directory:
            root = Path(directory)
            runner = ReadOnlyCanaryRunner.from_jsb16(
                profile_path=root / "profile",
                profile_lock_path=root / "profile.lock",
                artifact_root=root / "artifacts",
            )
            self.assertEqual(runner._validated_descriptor(), JSB16_DESCRIPTOR)
            runner.descriptor = CANARY_DESCRIPTOR
            result = await runner.run()
            self.assertEqual(result["reason_code"], "INVALID_CANARY_DESCRIPTOR")

    def test_cli_rejects_arbitrary_target_and_relative_paths(self):
        command = [sys.executable, "-m", "scripts.zalo_jsb16_readonly_canary"]
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
        root = Path(__file__).parents[1]
        help_result = subprocess.run(
            [*command, "--help"], capture_output=True, text=True, encoding="utf-8", cwd=root, env=environment
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertNotIn("--class-code", help_result.stdout)
        self.assertNotIn("--group-name", help_result.stdout)
        self.assertNotIn("--descriptor", help_result.stdout)

        invalid = subprocess.run(
            [*command, "--profile-path", "relative", "--profile-lock-path", "relative", "--artifact-root", "relative"],
            capture_output=True, text=True, encoding="utf-8", cwd=root, env=environment
        )
        self.assertEqual(invalid.returncode, 2)
        payload = json.loads(invalid.stdout)
        self.assertEqual(payload["phase"], JSB16_DESCRIPTOR.phase)
        self.assertEqual(payload["reason_code"], "INVALID_CANARY_BOOTSTRAP")


if __name__ == "__main__":
    unittest.main()
