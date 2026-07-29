import unittest

from logger_utils import get_recent_logs, log_context, log_message


class StructuredLoggingTests(unittest.TestCase):
    def test_secret_text_and_fields_are_redacted(self):
        marker = get_recent_logs(limit=500)
        after = marker[-1]["seq"] if marker else 0
        with log_context(job_id=42, job_type="TEST", component="unit"):
            log_message(
                "request password=super-secret authorization=Bearer-token",
                event="test.redaction",
                api_key="do-not-log-this",
                safe_count=3,
            )
        records = get_recent_logs(after=after, job_id=42)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertNotIn("super-secret", record["message"])
        self.assertNotIn("do-not-log-this", str(record))
        self.assertEqual(record["api_key"], "[REDACTED]")
        self.assertEqual(record["safe_count"], 3)
        self.assertEqual(record["event"], "test.redaction")

    def test_cursor_returns_only_new_records(self):
        first = log_message("first", event="test.cursor")
        second = log_message("second", event="test.cursor")
        records = get_recent_logs(after=first["seq"])
        self.assertTrue(any(item["seq"] == second["seq"] for item in records))
        self.assertFalse(any(item["seq"] == first["seq"] for item in records))


if __name__ == "__main__":
    unittest.main()
