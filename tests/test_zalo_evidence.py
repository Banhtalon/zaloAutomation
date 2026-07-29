from datetime import datetime, timedelta
import os
import tempfile
import unittest

from services.zalo_evidence import (
    INSUFFICIENT_EVIDENCE,
    NOT_FOUND_VERIFIED,
    SUBMITTED_VERIFIED,
    build_image_evidence,
    build_scan_result,
)


class ZaloEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 29, 16, 0)
        self.base = {
            "group": "MindX - VT-C4K-PTI09",
            "person": "Teacher",
            "actual_group_name": "MindX - VT-C4K-PTI09",
            "scanned_at": self.now,
            "messages_checked": 25,
            "classifier_result": {"report_found": False, "evaluated_messages": 25},
            "required_window_start": self.now - timedelta(days=10),
            "required_window_end": self.now,
            "oldest_message_date": self.now - timedelta(days=11),
            "newest_message_date": self.now,
            "images": {
                "report": None,
                "latest": {"path": "/latest.png", "sha256": "A"},
                "oldest": {
                    "path": "/oldest.png",
                    "sha256": "B",
                    "ui_date_text": "18/07/2026",
                },
                "oldest_message": {
                    "path": "/oldest-message.png",
                    "sha256": "C",
                },
            },
            "report_found": False,
        }

    def test_not_found_requires_complete_window_and_two_images(self):
        result = build_scan_result(**self.base)
        self.assertEqual(result["evidence_state"], NOT_FOUND_VERIFIED)
        self.assertIn("Chưa tìm thấy", result["status"])
        self.assertEqual(result["evidence"]["messages_checked"], 25)

    def test_incomplete_window_fails_closed(self):
        payload = dict(self.base)
        payload["oldest_message_date"] = self.now - timedelta(days=7)
        result = build_scan_result(**payload)
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertIn("Chưa tải đủ", result["status_detail"])

    def test_missing_negative_image_fails_closed(self):
        payload = dict(self.base)
        payload["images"] = dict(self.base["images"], oldest=None)
        result = build_scan_result(**payload)
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertIn("mốc tin nhắn cũ nhất", result["status_detail"])

    def test_oldest_image_without_ui_date_separator_fails_closed(self):
        payload = dict(self.base)
        payload["images"] = dict(
            self.base["images"],
            oldest={"path": "/oldest.png", "sha256": "B"},
        )
        result = build_scan_result(**payload)
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertIn("separator ngày", result["status_detail"])

    def test_ambiguous_undated_candidate_fails_closed(self):
        payload = dict(self.base)
        payload["extra_reasons"] = [
            "Có tin nhắn giống báo cáo nhưng không đọc được ngày gửi."
        ]
        result = build_scan_result(**payload)
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertIn("không đọc được ngày", result["status_detail"])

    def test_reversed_date_boundaries_fail_closed(self):
        payload = dict(self.base)
        payload["oldest_message_date"] = self.now
        payload["newest_message_date"] = self.now - timedelta(days=1)
        result = build_scan_result(**payload)
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertIn("Thứ tự mốc ngày", result["status_detail"])

    def test_submitted_requires_report_image(self):
        payload = dict(self.base)
        payload.update(
            report_found=True,
            report_detail="Nộp đúng hạn",
            submitted_for_bcnb=True,
            classifier_result={"report_found": True, "evaluated_messages": 3},
            images={
                "report": {"path": "/report.png", "sha256": "C"},
                "latest": None,
                "oldest": None,
            },
        )
        result = build_scan_result(**payload)
        self.assertEqual(result["evidence_state"], SUBMITTED_VERIFIED)
        self.assertTrue(result["submitted_for_bcnb"])

        payload["images"] = {"report": None, "latest": None, "oldest": None}
        result = build_scan_result(**payload)
        self.assertEqual(result["evidence_state"], INSUFFICIENT_EVIDENCE)
        self.assertFalse(result["submitted_for_bcnb"])

    def test_image_evidence_contains_sha256(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "proof.png")
            with open(path, "wb") as image:
                image.write(b"proof")
            evidence = build_image_evidence("/proof.png", path)
        self.assertIsNotNone(evidence)
        self.assertEqual(len(evidence["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
