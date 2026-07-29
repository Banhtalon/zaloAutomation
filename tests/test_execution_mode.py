import unittest

from services.execution_mode import (
    ExecutionModeError,
    mode_from_job,
    normalize_execution_mode,
)


class ExecutionModeTests(unittest.TestCase):
    def test_persisted_dry_run_wins_over_future_process_config(self):
        job = {
            "request_payload": {"execution_mode": "dry-run"},
            "mutates_external": False,
            "retryable": True,
            "recoverable": True,
        }
        self.assertEqual(mode_from_job(job), "dry-run")

    def test_legacy_job_without_mode_fails_safe_to_dry_run(self):
        job = {
            "request_payload": {},
            "mutates_external": False,
            "retryable": True,
            "recoverable": True,
        }
        self.assertEqual(mode_from_job(job), "dry-run")

    def test_metadata_mismatch_is_rejected(self):
        with self.assertRaises(ExecutionModeError):
            mode_from_job(
                {
                    "request_payload": {"execution_mode": "production"},
                    "mutates_external": False,
                    "retryable": True,
                    "recoverable": True,
                }
            )
        with self.assertRaises(ExecutionModeError):
            mode_from_job(
                {
                    "request_payload": {"execution_mode": "dry-run"},
                    "mutates_external": True,
                    "retryable": False,
                    "recoverable": False,
                }
            )

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ExecutionModeError):
            normalize_execution_mode("write-now")


if __name__ == "__main__":
    unittest.main()
