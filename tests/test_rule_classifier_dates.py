import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from classifiers.rule_classifier import (
    check_is_report_and_extract_date,
    classify_report_content,
    is_eligible_for_ai_assist,
    parse_zalo_date_header,
)


class RuleClassifierDateTests(unittest.TestCase):
    def test_unknown_or_invalid_ui_date_fails_closed(self):
        today = datetime(2026, 7, 15)
        self.assertIsNone(parse_zalo_date_header("Thứ tư", today))
        self.assertIsNone(parse_zalo_date_header("31/02/2026", today))

    def test_relative_dates_are_supported(self):
        today = datetime(2026, 7, 15)
        self.assertEqual(parse_zalo_date_header("Hôm nay", today), today)
        self.assertEqual(
            parse_zalo_date_header("Hôm qua", today),
            datetime(2026, 7, 14),
        )

    def test_yearless_date_at_new_year_uses_previous_year(self):
        self.assertEqual(
            parse_zalo_date_header("31/12", datetime(2026, 1, 2)),
            datetime(2025, 12, 31),
        )

    def test_explicit_ui_date_preserves_business_timezone(self):
        business_tz = ZoneInfo("Asia/Ho_Chi_Minh")
        parsed = parse_zalo_date_header(
            "T4 29/07/2026",
            datetime(2026, 7, 29, tzinfo=business_tz),
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.isoformat(), "2026-07-29T00:00:00+07:00")

    def test_report_without_a_date_keeps_date_unknown(self):
        is_report, report_date = check_is_report_and_extract_date(
            "Chào phụ huynh. Báo cáo buổi học: Nội dung và nhận xét của lớp.",
            "MindX - VT-JSB14",
            datetime(2026, 7, 15),
        )
        self.assertTrue(is_report)
        self.assertIsNone(report_date)

    def test_class_manager_opening_announcement_is_not_classified_as_report(self):
        opening_notice = (
            "Mindx Vũng Tàu Quản Lí Lớp Học\n"
            "📣 THÔNG BÁO KHAI GIẢNG LỚP LẬP TRÌNH VT-C4K-GI35 📣 @All\n"
            "Em chào quý phụ huynh và học viên của lớp.\n"
            "Em xin gửi lại một số thông tin quan trọng để gia đình cập nhật dễ hơn:\n"
            "+ Mã lớp: VT-C4K-GI35\n"
            "+ Lịch học : 10:00 - 12:00 CHỦ NHẬT hàng tuần\n"
            "+ Khai giảng : 18/10/2026"
        )
        result = classify_report_content(
            opening_notice,
            "MindX - Lập trình Game VT-C4K-GI35-CN (10H-12H)",
            datetime(2026, 10, 20),
        )
        self.assertFalse(result["is_report"])
        self.assertIn(
            "penalty:class_manager_or_opening_notice",
            result["matched_signals"],
        )

        metadata_only = (
            "Em chào quý phụ huynh của lớp.\n"
            "+ Mã lớp: VT-C4K-GI35\n"
            "+ Lịch học: Chủ nhật 10:00 - 12:00"
        )
        meta_result = classify_report_content(
            metadata_only,
            "MindX - Lập trình Game VT-C4K-GI35-CN (10H-12H)",
            datetime(2026, 10, 20),
        )
        self.assertFalse(meta_result["is_report"])
        self.assertFalse(is_eligible_for_ai_assist(opening_notice, result))

    def test_is_eligible_for_ai_assist_filters_short_penalized_and_already_matched_messages(self):
        today = datetime(2026, 10, 20)
        group = "MindX - Lập trình Game VT-C4K-GI35-CN (10H-12H)"

        short_msg = "Hôm nay các con học rất ngoan và hoàn thành tốt sản phẩm trên lớp ạ."
        short_cls = classify_report_content(short_msg, group, today)
        self.assertFalse(is_eligible_for_ai_assist(short_msg, short_cls))

        attendance_msg = (
            "Điểm danh lớp VT-C4K-GI35 hôm nay: tất cả các bạn học sinh đều có mặt "
            "đầy đủ đúng giờ và chuẩn bị máy tính sẵn sàng."
        )
        attendance_cls = classify_report_content(attendance_msg, group, today)
        self.assertIn("penalty:attendance_only", attendance_cls["matched_signals"])
        self.assertFalse(is_eligible_for_ai_assist(attendance_msg, attendance_cls))

        valid_report = (
            "Kính gửi phụ huynh. Báo cáo buổi học lớp VT-C4K-GI35. "
            "Nội dung hôm nay học vòng lặp và Nhận xét các con tiếp thu tốt."
        )
        valid_cls = classify_report_content(valid_report, group, today)
        self.assertTrue(valid_cls["is_report"])
        self.assertFalse(is_eligible_for_ai_assist(valid_report, valid_cls))

        informal_report = (
            "Dạ thầy gửi ba mẹ tình hình học hôm nay của các bạn lớp mình nhé ạ: "
            "Hôm nay cả lớp đã cùng nhau lắp ráp mô hình robot cảm biến khoảng cách "
            "và lập trình cho robot tự tránh vật cản trên sa bàn thành công."
        )
        informal_cls = classify_report_content(informal_report, group, today)
        self.assertFalse(informal_cls["is_report"])
        self.assertTrue(is_eligible_for_ai_assist(informal_report, informal_cls))


if __name__ == "__main__":
    unittest.main()
