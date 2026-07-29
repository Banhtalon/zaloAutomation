from datetime import datetime, timedelta
import os
import tempfile
import unittest

from services.zalo_evidence import (
    INSUFFICIENT_EVIDENCE,
    LESSON_14_EXEMPTION,
    NOT_FOUND_VERIFIED,
    SUBMITTED_VERIFIED,
    apply_lesson_14_exemption,
    build_image_evidence,
    build_scan_result,
    get_evidence_reference,
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

    def test_lesson_14_exemption_has_explicit_auditable_evidence(self):
        result = build_scan_result(**self.base)
        exempted = apply_lesson_14_exemption(
            result,
            class_code="VT-C4K-PTI08",
            applied_at=datetime.fromisoformat("2026-07-29T20:00:00+07:00"),
        )

        self.assertIs(exempted, result)
        self.assertEqual(exempted["evidence_state"], LESSON_14_EXEMPTION)
        self.assertTrue(exempted["submitted_for_bcnb"])
        self.assertEqual(
            exempted["evidence"]["business_rule"]["decision"],
            LESSON_14_EXEMPTION,
        )
        self.assertEqual(
            exempted["evidence"]["business_rule"]["source_evidence_state"],
            NOT_FOUND_VERIFIED,
        )
        self.assertEqual(
            exempted["evidence"]["business_rule"]["applied_at"],
            "2026-07-29T20:00:00+07:00",
        )

    def test_evidence_reference_returns_path_and_matching_sha(self):
        result = build_scan_result(**self.base)
        self.assertEqual(
            get_evidence_reference(result),
            ("/latest.png", "A"),
        )

    def test_lesson_14_without_zalo_match_still_has_complete_business_rule(self):
        exempted = apply_lesson_14_exemption(
            None,
            class_code="VT-C4K-PTI08",
            applied_at=datetime.fromisoformat("2026-07-29T20:00:00+07:00"),
        )
        self.assertEqual(exempted["evidence_state"], LESSON_14_EXEMPTION)
        self.assertTrue(exempted["submitted_for_bcnb"])
        self.assertEqual(
            exempted["evidence"]["classifier_result"]["decision"],
            LESSON_14_EXEMPTION,
        )
        self.assertEqual(
            exempted["evidence"]["business_rule"]["class_code"],
            "VT-C4K-PTI08",
        )
        self.assertEqual(get_evidence_reference(exempted), (None, None))


if __name__ == "__main__":
    unittest.main()
