import os
import unittest
from unittest.mock import AsyncMock, patch

from services.external_gateways import PlaywrightLmsQualityGateway
from services.lms_quality_service import (
    evaluate_lms_slot_quality,
    evaluate_lms_slot_summary,
    evaluate_lms_student_comments,
    evaluate_zalo_content_issues,
    extract_report_lesson_title,
    fetch_lms_class_slots_via_page,
    inspect_and_attach_quality_issues,
    is_checkpoint_session,
)
from services.report_status import (
    ISSUE_CODE_LMS_MANUAL_CHECK,
    SEVERITY_NOTE,
    issue_code,
    issue_severity,
    issue_text,
)


class LmsQualityServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_evaluate_lms_slot_summary_rejects_empty_and_placeholders(self) -> None:
        for bad_summary in (
            None,
            "",
            "   ",
            "<p>&nbsp;</p>",
            "Chưa nội dung bài học",
            "Chưa có nội dung",
            "B1",
            "Bài 1",
            "Buổi 5",
            "Kiểm tra checkpoint 1",
            "Checkpoint 2",
            "B9: Kiểm tra checkpoint",
            "Buổi 5: Kiểm tra checkpoint 1",
            "Bài 1: Chưa nội dung bài học",
        ):
            res = evaluate_lms_slot_summary(bad_summary)
            self.assertFalse(res["has_valid_summary"], msg=f"Should fail: {bad_summary}")
            self.assertEqual(issue_text(res["issue"]), "[LMS] Chưa có nội dung bài")
            self.assertEqual(issue_severity(res["issue"]), SEVERITY_NOTE)

        for good_summary in (
            "<p>Học viên làm quen với khái niệm biến, kiểu dữ liệu và vòng lặp trong Python.</p>",
            "B10: Tìm hiểu cảm biến siêu âm và lập trình xe tránh vật cản tự động",
            "Buổi 5: Ôn tập kiến thức danh sách (List), hàm (Function) và làm bài kiểm tra định kỳ số 1",
        ):
            res = evaluate_lms_slot_summary(good_summary)
            self.assertTrue(res["has_valid_summary"], msg=f"Should pass: {good_summary}")
            self.assertEqual(res["issue"], "")

    def test_is_checkpoint_session_distinguishes_coding_and_robotics(self) -> None:
        # Coding classes: Checkpoint at lessons 5 and 9
        self.assertFalse(is_checkpoint_session("VT-C4K-PTB16", "Coding", 1))
        self.assertFalse(is_checkpoint_session("VT-C4K-PTI11", "Coding", 4))
        self.assertTrue(is_checkpoint_session("VT-C4K-PTI11", "Coding", 5))
        self.assertFalse(is_checkpoint_session("VT-C4K-PTI11", "Coding", 8))
        self.assertTrue(is_checkpoint_session("VT-C4K-PTI11", "Coding", 9))
        self.assertFalse(is_checkpoint_session("VT-C4K-PTI11", "Coding", 14))

        # Robotics classes: Checkpoint at lessons 4 and 8
        self.assertFalse(is_checkpoint_session("VT-ROB-ARMB19", "Robotics", 1))
        self.assertTrue(is_checkpoint_session("VT-ROB-ARMB19", "Robotics", 4))
        self.assertFalse(is_checkpoint_session("VT-ROB-ARMB19", "Robotics", 5))
        self.assertTrue(is_checkpoint_session("VT-ROB-ARMB19", "Robotics", 8))
        self.assertFalse(is_checkpoint_session("VT-ROB-ARMB19", "Robotics", 9))
        self.assertFalse(is_checkpoint_session("VT-ROB-ARMB19", "Robotics", 10))

        # Rubric fallback detection
        rubric_slot = {
            "studentAttendance": [
                {
                    "status": "ATTENDED",
                    "comment": (
                        "Điểm năng lực: 8.5/10\n"
                        "- KIẾN THỨC: Nắm vững các khối lệnh điều khiển.\n"
                        "- KỸ NĂNG: Lắp ráp mạch chuẩn xác.\n"
                        "- THÁI ĐỘ: Tập trung, hăng hái phát biểu."
                    ),
                }
            ]
        }
        self.assertTrue(is_checkpoint_session("VT-UNKNOWN-01", "", 6, slot_data=rubric_slot))

    def test_regular_session_comments_catch_shallow_and_copy_paste_without_pii(
        self,
    ) -> None:
        # Synthetic fixture modeled on VT-C4K-PTB16 Lesson 1 (shallow 1-line generic comment + copy-paste)
        shallow_body = "Con đi học đúng giờ, chú ý lắng nghe giáo viên giảng bài."
        attendance = [
            {
                "status": "ATTENDED",
                "student": {"fullName": f"Học Viên Ẩn Danh {idx}"},
                "comment": f"NHẬN XÉT BUỔI HỌC SỐ 1: Học Viên Ẩn Danh {idx} (VT-C4K-PTB16)\n{shallow_body}",
            }
            for idx in range(1, 7)
        ]
        res = evaluate_lms_student_comments(
            attendance,
            is_checkpoint=False,
            lesson=1,
        )
        self.assertEqual(res["attended_count"], 6)
        self.assertEqual(res["shallow_count"], 6)
        self.assertEqual(res["duplicate_count"], 6)
        self.assertEqual(res["status"], "REVIEW")
        self.assertEqual(res["review_reasons"], {"nhận xét sơ sài": 6})
        self.assertEqual(issue_severity(res["issues"][0]), SEVERITY_NOTE)
        joined_issues = " ; ".join(issue_text(item) for item in res["issues"])
        self.assertIn("[LMS] Viết kỹ NX", joined_issues)
        self.assertNotIn("Học Viên Ẩn Danh", joined_issues)

    def test_regular_session_comments_accept_3_part_structure(self) -> None:
        good_attendance = [
            {
                "status": "ATTENDED",
                "student": {"fullName": "HV Mẫu 1"},
                "comment": (
                    "Điểm mạnh: Con hiểu nhanh nguyên lý hoạt động của cảm biến siêu âm và tự lắp hoàn chỉnh khung xe.\n"
                    "Điểm cần cải thiện: Đôi lúc con còn nhầm chân cắm Trig và Echo nên cần chú ý kiểm tra sơ đồ mạch kỹ hơn.\n"
                    "Lời khuyên: Về nhà con ôn tập lại khối lệnh đo khoảng cách trên LMS để buổi sau lập trình thuật toán rẽ nhánh nhé."
                ),
            },
            {
                "status": "LATE_ARRIVED",
                "student": {"fullName": "HV Mẫu 2"},
                "comment": (
                    "- Ưu điểm: Con nắm vững cú pháp vòng lặp và chủ động thử nghiệm nhiều tốc độ động cơ khác nhau.\n"
                    "- Cần cải thiện: Tuy nhiên thao tác đi dây cáp của con còn hơi rối làm bánh xe bị vướng khi quay.\n"
                    "- Gợi ý: Con nên dùng chốt cố định dây gọn vào khung robot và luyện tập thêm bài tập bổ sung trên LMS."
                ),
            },
            {
                "status": "ABSENT",
                "student": {"fullName": "HV Vắng"},
                "comment": "",
            },
        ]
        res = evaluate_lms_student_comments(
            good_attendance,
            is_checkpoint=False,
            lesson=10,
        )
        self.assertEqual(res["attended_count"], 2)
        self.assertEqual(res["failed_count"], 0)
        self.assertEqual(res["issues"], [])

    def test_checkpoint_session_comments_validate_competency_rubric(self) -> None:
        # Valid checkpoint comments (modeled on VT-C4K-PTI11 B5/B9 and VT-ROB-ARMB19 B4/B8)
        valid_cp = [
            {
                "status": "ATTENDED",
                "comment": (
                    "Điểm năng lực: 9/10\n"
                    "1. KIẾN THỨC: Nắm chắc kiến thức vòng lặp, danh sách và hàm trong Python.\n"
                    "2. KỸ NĂNG: Vận dụng tốt vào bài kiểm tra, xử lý lỗi cú pháp nhanh.\n"
                    "3. THÁI ĐỘ: Nghiêm túc, chủ động hoàn thành bài đúng thời gian."
                ),
            },
            {
                "status": "ABSENT",
                "comment": "",
            },
        ]
        cp_ok = evaluate_lms_student_comments(valid_cp, is_checkpoint=True, lesson=5)
        self.assertEqual(cp_ok["issues"], [])

        # Missing / incomplete checkpoint comments (including missing THÁI ĐỘ or regular 3-part comment)
        incomplete_cp = [
            {
                "status": "ATTENDED",
                "comment": "Làm bài tốt 9 điểm.",
            },
            {
                "status": "ATTENDED",
                "comment": (
                    "Điểm năng lực: 8/10\n"
                    "- KIẾN THỨC: Nắm vững kiến thức.\n"
                    "- KỸ NĂNG: Thao tác nhanh."
                ),
            },
            {
                "status": "ATTENDED",
                "comment": (
                    "Điểm mạnh: Con hiểu bài nhanh.\n"
                    "Điểm cần cải thiện: Cần chú ý hơn.\n"
                    "Lời khuyên: Về nhà ôn tập thêm bài tập trên LMS."
                ),
            },
        ]
        cp_bad = evaluate_lms_student_comments(incomplete_cp, is_checkpoint=True, lesson=8)
        self.assertEqual(cp_bad["attended_count"], 3)
        self.assertEqual(cp_bad["failed_count"], 3)
        self.assertEqual(
            [issue_text(x) for x in cp_bad["issues"]],
            ["[LMS] Đủ khung Checkpoint"],
        )
        self.assertEqual(issue_severity(cp_bad["issues"][0]), SEVERITY_NOTE)

    def test_evaluate_zalo_content_issues_detects_wrong_date_lesson_and_title(
        self,
    ) -> None:
        item = {
            "evidence_state": "SUBMITTED_VERIFIED",
            "scan_target": {
                "class_code": "VT-C4K-PTI11",
                "lesson": "5",
                "class_date_iso": "2026-10-25",
            },
            "evidence": {
                "date_context": {
                    "target_lesson": "5",
                    "content_lesson": 2,
                    "target_match_note": "[Zalo] Sai ngày (18/10)",
                    "reported_lesson_title": "Thiết kế giao diện trang web bằng HTML và CSS",
                }
            },
        }
        issues = evaluate_zalo_content_issues(
            item,
            lms_summary_text="Buổi 5: Cấu trúc dữ liệu danh sách (List) và vòng lặp For trong Python",
        )
        self.assertEqual(
            issues,
            ["[Zalo] Sai ngày (18/10), sai số buổi, lệch tên bài với LMS"],
        )

        template_mismatch_item = {
            "evidence_state": "SUBMITTED_LATE_VERIFIED",
            "scan_target": {
                "class_code": "VT-ROB-SEMIB07",
                "lesson": "6",
                "class_date_iso": "2026-10-25",
            },
            "evidence": {
                "date_context": {
                    "target_lesson": "6",
                    "content_lesson": 6,
                    "target_match_note": "[Zalo] Sai ngày/buổi",
                }
            },
        }
        self.assertEqual(
            evaluate_zalo_content_issues(template_mismatch_item),
            ["[Zalo] Sai ngày/buổi"],
        )

        # Matching topic keywords should not trigger title mismatch
        matching_item = {
            "evidence_state": "SUBMITTED_VERIFIED",
            "scan_target": {"class_code": "VT-C4K-PTI11", "lesson": "5"},
            "raw_content": "Tên bài học: Danh sách List và vòng lặp For trong Python",
        }
        self.assertEqual(
            evaluate_zalo_content_issues(
                matching_item,
                lms_summary_text="Buổi 5: Cấu trúc dữ liệu danh sách (List) và vòng lặp For trong Python",
            ),
            [],
        )

        # 5.3.3: NOT_FOUND_VERIFIED and INSUFFICIENT_EVIDENCE never produce Zalo content issues
        for non_submitted_state in ("NOT_FOUND_VERIFIED", "INSUFFICIENT_EVIDENCE", ""):
            non_submitted_item = {
                **item,
                "evidence_state": non_submitted_state,
            }
            self.assertEqual(
                evaluate_zalo_content_issues(
                    non_submitted_item,
                    lms_summary_text="Buổi 5: Cấu trúc dữ liệu danh sách (List) và vòng lặp For trong Python",
                ),
                [],
            )

    def test_inspect_and_attach_quality_issues_populates_scan_results(self) -> None:
        scan_results = [
            {
                "group": "MindX - VT-C4K-PTB16",
                "khoi": "Coding",
                "scan_target": {
                    "class_code": "VT-C4K-PTB16",
                    "lesson": "1",
                    "class_date_iso": "2026-10-24",
                    "khoi": "Coding",
                },
                "evidence": {
                    "date_context": {
                        "target_class_code": "VT-C4K-PTB16",
                        "target_lesson": "1",
                        "target_class_date": "2026-10-24",
                    }
                },
            }
        ]
        slots_by_class = {
            "VT-C4K-PTB16": [
                {
                    "_id": "slot-ptb16-0",
                    "index": 0,
                    "date": "2026-10-23T17:00:00.000Z",
                    "startTime": "2026-10-24T01:00:00.000Z",
                    "summary": "",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            "comment": "NHẬN XÉT BUỔI HỌC SỐ 1: HS A (VT-C4K-PTB16)\nCon học ngoan, chú ý nghe giảng.",
                        }
                    ],
                }
            ]
        }
        inspect_and_attach_quality_issues(
            scan_results,
            lms_slots_by_class=slots_by_class,
        )
        self.assertEqual(len(scan_results[0]["lms_quality_issues"]), 1)
        self.assertEqual(
            issue_text(scan_results[0]["lms_quality_issues"][0]),
            "[LMS] Chưa có nội dung bài, viết kỹ NX",
        )
        self.assertEqual(
            issue_severity(scan_results[0]["lms_quality_issues"][0]),
            SEVERITY_NOTE,
        )

        # Re-evaluating after teacher fixes LMS content must clear stale issues
        fixed_slots_by_class = {
            "VT-C4K-PTB16": [
                {
                    "_id": "slot-ptb16-0",
                    "index": 0,
                    "date": "2026-10-23T17:00:00.000Z",
                    "startTime": "2026-10-24T01:00:00.000Z",
                    "summary": "Buổi 1: Làm quen ngôn ngữ Python, biến và kiểu dữ liệu cơ bản",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            "comment": (
                                "Điểm mạnh: Con nắm bài tốt và hoàn thành các bài thực hành trên lớp.\n"
                                "Điểm cần cải thiện: Cần chú ý đặt tên biến đúng quy tắc hơn.\n"
                                "Lời khuyên: Về nhà con làm thêm bài tập tự luyện trên LMS nhé."
                            ),
                        }
                    ],
                }
            ]
        }
        inspect_and_attach_quality_issues(
            scan_results,
            lms_slots_by_class=fixed_slots_by_class,
        )
        self.assertEqual(scan_results[0]["lms_quality_issues"], [])
        self.assertEqual(
            scan_results[0]["scan_target"]["lms_quality_issues"],
            [],
        )

    def test_two_identical_comments_in_three_student_class_and_overflow_flagged(
        self,
    ) -> None:
        dup_comment = (
            "Điểm mạnh: Con hiểu bài rất nhanh và làm tốt bài thực hành.\n"
            "Điểm cần cải thiện: Cần chú ý kiểm tra lại cú pháp trước khi chạy.\n"
            "Lời khuyên: Về nhà con ôn tập thêm các bài tập nâng cao trên LMS."
        )
        unique_comment = (
            "Điểm mạnh: Con chủ động đặt câu hỏi và hoàn thành dự án đúng hạn.\n"
            "Điểm cần cải thiện: Thao tác gõ phím của con còn hơi chậm.\n"
            "Lời khuyên: Con nên luyện gõ 10 ngón mỗi ngày 15 phút tại nhà nhé."
        )
        three_students = [
            {"status": "ATTENDED", "comment": dup_comment},
            {"status": "ATTENDED", "comment": dup_comment},
            {"status": "ATTENDED", "comment": unique_comment},
        ]
        res_dup = evaluate_lms_student_comments(three_students, is_checkpoint=False, lesson=2)
        self.assertEqual(res_dup["duplicate_count"], 2)
        self.assertEqual(res_dup["issues"], [])

        # 51 students overflow (> 50 limit) must flag manual check even if first 50 are valid
        overflow_students = [
            {
                "status": "ATTENDED",
                "comment": f"{unique_comment} Mã bài {i}.",
            }
            for i in range(50)
        ] + [{"status": "ATTENDED", "comment": ""}]
        res_overflow = evaluate_lms_student_comments(
            overflow_students, is_checkpoint=False, lesson=3
        )
        self.assertTrue(
            any(
                issue_text(issue) == "[LMS] Kiểm tra tay trên LMS"
                and issue_code(issue) == ISSUE_CODE_LMS_MANUAL_CHECK
                and issue_severity(issue) == SEVERITY_NOTE
                for issue in res_overflow["issues"]
            )
        )

        # Fetch error slot must flag manual check instead of returning issues=[]
        err_eval = evaluate_lms_slot_quality(
            {"fetch_error": True},
            class_code="VT-C4K-PTB16",
            khoi="Coding",
            lesson=1,
        )
        self.assertEqual(len(err_eval["issues"]), 1)
        self.assertEqual(issue_code(err_eval["issues"][0]), ISSUE_CODE_LMS_MANUAL_CHECK)
        self.assertEqual(issue_severity(err_eval["issues"][0]), SEVERITY_NOTE)
        self.assertEqual(issue_text(err_eval["issues"][0]), "[LMS] Kiểm tra tay trên LMS")

    async def test_gateway_isolated_mode_and_mock_page_fetch(self) -> None:
        fake_page = AsyncMock()
        fake_page.evaluate.return_value = {
            "VT-ROB-ARMB19": [
                {
                    "summary": "Buổi 1: Làm quen bộ kit Arduino",
                    "studentAttendance": [{"status": "ATTENDED", "comment": "OK"}],
                }
            ]
        }
        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "1"}):
            isolated_res = await fetch_lms_class_slots_via_page(
                fake_page, class_names=["VT-ROB-ARMB19"]
            )
            self.assertEqual(isolated_res, {})
            fake_page.evaluate.assert_not_called()

        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}):
            fake_page.context = AsyncMock()
            fake_page.context.storage_state.return_value = {
                "origins": [
                    {
                        "origin": "https://teaching.mindx.edu.vn",
                        "localStorage": [
                            {
                                "name": "csrf_token",
                                "value": "teaching_token_should_be_ignored_1234567890",
                            }
                        ],
                    },
                    {
                        "origin": "https://lms.mindx.edu.vn",
                        "localStorage": [
                            {
                                "name": "id_token",
                                "value": "valid_lms_jwt_token_12345678901234567890",
                            }
                        ],
                    },
                ]
            }
            gw = PlaywrightLmsQualityGateway(timeout_ms=5000)
            live_res = await gw.fetch_class_slots(fake_page, ["vt-rob-armb19"])
            self.assertIn("VT-ROB-ARMB19", live_res)
            self.assertEqual(len(live_res["VT-ROB-ARMB19"]), 1)
            eval_args = fake_page.evaluate.call_args[0][1]
            self.assertEqual(
                eval_args["preResolvedToken"],
                "valid_lms_jwt_token_12345678901234567890",
            )

    def test_extract_report_lesson_title_and_evaluate_slot_quality_lesson_14(self) -> None:
        self.assertEqual(
            extract_report_lesson_title("📌 Tên bài học: Cảm biến siêu âm HC-SR04\nĐiểm danh: 6/6"),
            "Cảm biến siêu âm HC-SR04",
        )
        multiline_report = (
            "Chào bố mẹ, thầy gửi báo cáo buổi học số 8 ngày 31/10/2026 của lớp VT-C4K-GI34:\n"
            "📘 NỘI DUNG BUỔI HỌC:\n"
            "BÀI 8: CAMERA THÔNG MINH\n"
            "- Biết cách sử dụng biến để thao tác với Camera.\n"
            "📌 NỘI DUNG BUỔI HỌC KẾ TIẾP:\n"
            "Thời gian: Thứ Bảy (07/11/2026)\n"
            "Nội dung bài học: BUỔI 9: KIỂM THỬ VÀ SỬA LỖI TRÒ CHƠI"
        )
        self.assertEqual(
            extract_report_lesson_title(multiline_report),
            "BÀI 8: CAMERA THÔNG MINH",
        )
        no_inline_title_with_next_section = (
            "📘 NỘI DUNG BUỔI HỌC:\n"
            "- Tìm hiểu về danh sách (list) và biến cục bộ.\n"
            "📌 NỘI DUNG BUỔI HỌC KẾ TIẾP:\n"
            "Nội dung bài học: BUỔI 5: WORKSHOP KỸ NĂNG (Checkpoint 1)"
        )
        self.assertEqual(
            extract_report_lesson_title(no_inline_title_with_next_section),
            "",
        )
        attendance_between_header_and_title = (
            "📘 NỘI DUNG BUỔI HỌC:\n👥 ĐIỂM DANH: 6/6\n📌 Tên bài học: Camera thông minh"
        )
        self.assertEqual(
            extract_report_lesson_title(attendance_between_header_and_title),
            "Camera thông minh",
        )
        exempt = evaluate_lms_slot_quality(
            {"summary": "", "studentAttendance": []},
            class_code="VT-JSB16",
            khoi="Coding",
            lesson=14,
        )
        self.assertTrue(exempt["is_exempt"])
        self.assertEqual(exempt["lms_comments"]["status"], "EXEMPT")
        self.assertEqual(exempt["lms_spck"]["status"], "UNVERIFIED")
        self.assertEqual(issue_code(exempt["issues"][0]), ISSUE_CODE_LMS_MANUAL_CHECK)

    async def test_missing_data_61st_class_21st_slot_and_duplicate_class_flagged(
        self,
    ) -> None:
        # 1. Missing studentAttendance list or empty attendance list flags manual check
        res_none_att = evaluate_lms_student_comments(None, is_checkpoint=False, lesson=1)
        self.assertEqual(len(res_none_att["issues"]), 1)
        self.assertEqual(issue_code(res_none_att["issues"][0]), ISSUE_CODE_LMS_MANUAL_CHECK)
        self.assertEqual(
            issue_text(res_none_att["issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        res_empty_att = evaluate_lms_student_comments([], is_checkpoint=False, lesson=1)
        self.assertEqual(len(res_empty_att["issues"]), 1)
        self.assertEqual(issue_code(res_empty_att["issues"][0]), ISSUE_CODE_LMS_MANUAL_CHECK)
        self.assertEqual(
            issue_text(res_empty_att["issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 2. 61st class overflow & missing class in slots_map
        fake_page = AsyncMock()
        codes = [f"SYNTH-JSB{i:02d}" for i in range(61)]
        fake_page.evaluate.return_value = {
            c: [
                {
                    "summary": "Buổi 1: Làm quen lập trình Python",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            "comment": (
                                "Điểm mạnh: Con hiểu bài tốt.\n"
                                "Điểm cần cải thiện: Cần chú ý cú pháp.\n"
                                "Lời khuyên: Về nhà làm thêm bài tập trên LMS."
                            ),
                        }
                    ],
                }
            ]
            for c in codes[:60]
        }
        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}):
            slots = await fetch_lms_class_slots_via_page(fake_page, class_names=codes)
        item_61 = {"scan_target": {"class_code": codes[60], "lesson": "1"}}
        inspect_and_attach_quality_issues([item_61], lms_slots_by_class=slots)
        self.assertTrue(item_61.get("lms_quality_issues"))
        self.assertEqual(
            issue_code(item_61["lms_quality_issues"][0]),
            ISSUE_CODE_LMS_MANUAL_CHECK,
        )
        self.assertEqual(
            issue_text(item_61["lms_quality_issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 3. 21st lesson (beyond MAX_LMS_SLOTS_PER_CLASS = 20)
        slots_20 = {
            "SYNTH-JSB01": [
                {
                    "summary": "Buổi học lập trình Python",
                    "truncated": True,
                    "studentAttendance": [],
                }
                for _ in range(20)
            ]
        }
        item_21 = {"scan_target": {"class_code": "SYNTH-JSB01", "lesson": "21"}}
        inspect_and_attach_quality_issues([item_21], lms_slots_by_class=slots_20)
        self.assertTrue(item_21.get("lms_quality_issues"))
        self.assertEqual(
            issue_code(item_21["lms_quality_issues"][0]),
            ISSUE_CODE_LMS_MANUAL_CHECK,
        )
        self.assertEqual(
            issue_text(item_21["lms_quality_issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 4. Duplicate class code on LMS must flag manual check even if second record has slots=null
        import json
        import subprocess

        runner_js = r"""
        const fs = require('fs');
        const { script, args } = JSON.parse(fs.readFileSync(0, 'utf8'));
        global.window = { location: { hostname: 'lms.mindx.edu.vn' } };
        global.localStorage = {
            length: 1,
            key: () => 'id_token',
            getItem: () => 'SYNTHETIC_TOKEN_123456789012345'
        };
        global.fetch = async (_url, req) => {
            const code = JSON.parse(req.body).variables.search;
            return {
                ok: true,
                json: async () => ({
                    data: {
                        classesMissAttendOrComment: {
                            data: [
                                {
                                    name: code,
                                    slots: [
                                        {
                                            summary: 'Buổi 1: Làm quen lập trình Python',
                                            studentAttendance: [
                                                {
                                                    status: 'ATTENDED',
                                                    comment: 'Điểm mạnh: Tốt.\nĐiểm cần cải thiện: Chú ý.\nLời khuyên: Ôn bài.'
                                                }
                                            ]
                                        }
                                    ]
                                },
                                { name: code, slots: null }
                            ]
                        }
                    }
                })
            };
        };
        (async () => {
            const res = await eval('(' + script + ')')(args);
            process.stdout.write(JSON.stringify(res));
        })();
        """

        class NodeEvalPage:
            async def evaluate(self, script: str, args: dict) -> dict:
                payload_json = json.dumps({"script": script, "args": args})
                proc = subprocess.run(
                    ["node", "-e", runner_js],
                    input=payload_json,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=True,
                )
                return json.loads(proc.stdout)

        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}):
            dup_slots = await fetch_lms_class_slots_via_page(
                NodeEvalPage(), class_names=["SYNTH-DUP01"]
            )
        dup_item = {"scan_target": {"class_code": "SYNTH-DUP01", "lesson": "1"}}
        inspect_and_attach_quality_issues([dup_item], lms_slots_by_class=dup_slots)
        self.assertEqual(
            dup_item["lms_quality_issues"],
            [
                {
                    "code": ISSUE_CODE_LMS_MANUAL_CHECK,
                    "severity": SEVERITY_NOTE,
                    "text": "[LMS] Kiểm tra tay trên LMS",
                }
            ],
        )

    async def test_period_scoping_and_parallel_batch_persistence(self) -> None:
        import copy
        from datetime import datetime
        from types import SimpleNamespace
        from core.time_utils import BUSINESS_TIMEZONE
        import services.zalo_bcnb_service as bcnb
        import services.zalo_parallel_service as par
        from services.zalo_parallel_service import _empty_result
        from tests.test_zalo_parallel_service import (
            FakeParallelBrowser,
            InMemoryBatchRepository,
            _make_search_target,
            _verified_scan_result,
        )

        # 1. Period scoping when scan_result is an _empty_result (week in scan_target, not date_context)
        rows = [
            _empty_result(
                {"class_code": f"SYNTH-JSB{week}", "week": week, "year": 2026, "lesson": "1"},
                today=datetime(2026, 10, 29),
                reason="synthetic scan failure",
            )
            for week in (43, 44)
        ]
        gateway = SimpleNamespace(
            fetch_class_slots=AsyncMock(
                return_value={f"SYNTH-JSB{week}": [{"fetch_error": True}] for week in (43, 44)}
            )
        )
        with (
            patch.object(bcnb, "_read_bcnb_schedule_rows", AsyncMock(return_value=[])),
            patch.object(bcnb, "resolve_report_columns", AsyncMock(return_value=(0, 1, 2))),
            patch.object(bcnb, "log_message"),
        ):
            await bcnb.update_bcnb_reports(
                object(),
                job_id=1,
                execution_mode="dry-run",
                scan_results=rows,
                today=datetime(2026, 10, 29),
                scan_plan={
                    "selected_weeks": [
                        {"week": 43, "year": 2026},
                        {"week": 44, "year": 2026},
                    ],
                    "latest_week": {"week": 44, "year": 2026},
                    "targets": [],
                },
                job_repo=SimpleNamespace(ensure_not_cancelled=AsyncMock()),
                audit_repo=object(),
                period_filter={(43, 2026)},
                lms_quality_gateway=gateway,
            )
        self.assertEqual(gateway.fetch_class_slots.call_args.args[1], ["SYNTH-JSB43"])
        self.assertIn("lms_quality_issues", rows[0])
        self.assertNotIn("lms_quality_issues", rows[1])

        # 2. Persistence of LMS quality issues into batch target snapshots
        class SnapshotRepository(InMemoryBatchRepository):
            def __init__(self) -> None:
                super().__init__()
                self.snapshots: dict[int, dict] = {}

            async def complete_scan(self, target_id: int, **kwargs) -> None:
                await super().complete_scan(target_id, **kwargs)
                self.snapshots[target_id] = copy.deepcopy(kwargs.get("result"))

            async def finalize_registered_scan(self, target_id: int, **kwargs) -> None:
                await super().finalize_registered_scan(target_id, **kwargs)
                self.snapshots[target_id] = copy.deepcopy(kwargs.get("result"))

        repo = SnapshotRepository()
        target = _make_search_target(1)
        target["lesson"] = "1"
        code = target["class_code"]
        batch_gw = SimpleNamespace(
            fetch_class_slots=AsyncMock(
                return_value={
                    code: [
                        {
                            "index": 0,
                            "date": "2026-08-20T17:00:00.000Z",
                            "startTime": "2026-08-21T01:00:00.000Z",
                            "summary": "",
                            "studentAttendance": [{"status": "ATTENDED", "comment": ""}],
                        }
                    ]
                }
            )
        )
        runtime = SimpleNamespace(
            external=SimpleNamespace(
                browser=FakeParallelBrowser([{}, {}]),
                lms_quality=batch_gw,
            ),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=1,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        with (
            patch.object(par, "ZaloBatchRepository", return_value=repo),
            patch.object(
                par,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(par, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(
                par,
                "_scan_one_target",
                AsyncMock(return_value=_verified_scan_result(target)),
            ),
            patch.object(par, "log_message"),
            patch.object(bcnb, "_read_bcnb_schedule_rows", AsyncMock(return_value=[])),
            patch.object(bcnb, "resolve_report_columns", AsyncMock(return_value=(0, 1, 2))),
            patch.object(bcnb, "log_message"),
            patch.object(par.asyncio, "sleep", AsyncMock()),
        ):
            batch_res = await par.run_parallel_zalo_batch(
                job_id=1001,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [
                        {"week": 34, "year": 2026},
                        {"week": 35, "year": 2026},
                    ],
                    "latest_week": {"week": 35, "year": 2026},
                    "targets": [target],
                },
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused-evidence-root",
                screenshots_dir="unused-screenshots-dir",
                runtime=runtime,
                job_repo=SimpleNamespace(ensure_not_cancelled=AsyncMock(), update_job=AsyncMock()),
                audit_repo=object(),
                evidence_repo=SimpleNamespace(register_scan_results=AsyncMock()),
            )
        self.assertEqual(batch_gw.fetch_class_slots.await_count, 1)
        self.assertEqual(len(batch_res["scan_results"][0].get("lms_quality_issues", [])), 2)
        self.assertEqual(len(repo.snapshots[1].get("lms_quality_issues", [])), 2)

    def test_stage4_4_email_table_alignment_truncation_and_csv_contract(self) -> None:
        import csv
        import io
        from urllib.parse import unquote
        from repositories.report_repository import (
            CSV_HEADERS,
            is_failed_weekly_summary_row,
        )
        from services.report_email_service import (
            MAX_GMAIL_COMPOSE_URL_LEN,
            _display_width,
            _short_url_status,
            build_failed_classes_email_package,
        )

        self.assertEqual(_short_url_status("🟡 Đã nộp trễ – Có minh chứng"), "Trễ")
        self.assertEqual(_short_url_status("⚠️ AI gợi ý – Cần kiểm tra"), "Trễ")
        self.assertEqual(_short_url_status("✅ Đã nộp – Có minh chứng"), "OK")

        ten_rows = [
            {
                "class_code": f"VT-C4K-PTB{idx:02d}",
                "teacher": f"Giảng viên {idx}",
                "lesson": str((idx % 9) + 1),
                "class_date": "24/10/2026",
                "zalo_status": "✅ Đã nộp – Có minh chứng"
                if idx % 2 == 0
                else "❌ Chưa tìm thấy báo cáo",
                "lms_status": "❌ Chưa nhận xét (Buổi 2)" if idx % 2 == 0 else "✅ Đã nộp LMS",
                "evaluation_status": (
                    "⏳ Đã hoàn tất nhận xét Zalo, chưa nhận xét LMS"
                    if idx % 2 == 0
                    else "⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo"
                ),
                "issue_description": f"[LMS] Chưa ghi nội dung bài học buổi {idx}",
                "verified_at": "2026-10-29T01:00:00Z",
            }
            for idx in range(1, 11)
        ]
        for r in ten_rows:
            self.assertTrue(is_failed_weekly_summary_row(r))
        self.assertFalse(
            is_failed_weekly_summary_row(
                {
                    "class_code": "VT-C4K-QUAL01",
                    "zalo_status": "✅ Đã nộp – Có minh chứng",
                    "lms_status": "✅ Đã nộp LMS",
                    "evaluation_status": "✅ Đã hoàn thành nhận xét",
                    "issue_description": "[LMS] Chưa ghi nội dung bài học",
                }
            )
        )

        pkg10 = build_failed_classes_email_package(
            week=43,
            year=2026,
            failed_rows=ten_rows,
            total_rows_count=47,
            selected_job_id=397,
            recipients=["quangda@mindx.net.vn"],
        )
        self.assertEqual(len(CSV_HEADERS), 8)
        parsed_csv = list(csv.reader(io.StringIO(pkg10["csv_text"].lstrip("\ufeff"))))
        self.assertEqual(parsed_csv[0], list(CSV_HEADERS))
        self.assertEqual(len(parsed_csv), 11)

        # Every line of the Unicode table must have the exact same display width
        table_lines = pkg10["text_table"].splitlines()
        line_widths = {_display_width(line) for line in table_lines}
        self.assertEqual(len(line_widths), 1)

        # All 10 rows and 25 rows fit inside Gmail compose URL without truncation and stay within encoded byte limit
        self.assertFalse(pkg10["gmail_body_truncated"])
        self.assertLessEqual(len(pkg10["gmail_compose_url"]), MAX_GMAIL_COMPOSE_URL_LEN)
        decoded_url = unquote(pkg10["gmail_compose_url"])
        self.assertIn("VT-C4K-PTB10", decoded_url)

        twenty_five_rows = [
            {
                **ten_rows[idx % 2],
                "class_code": f"VT-C4K-PTB{idx:02d}",
                "teacher": f"Giảng viên {idx}",
                "lesson": str((idx % 9) + 1),
                "issue_description": f"[LMS] Chưa ghi nội dung bài học buổi {idx}",
            }
            for idx in range(1, 26)
        ]
        pkg25 = build_failed_classes_email_package(
            week=43,
            year=2026,
            failed_rows=twenty_five_rows,
            total_rows_count=47,
            selected_job_id=397,
            recipients=["quangda@mindx.net.vn"],
        )
        self.assertFalse(pkg25["gmail_body_truncated"])
        self.assertLessEqual(len(pkg25["gmail_compose_url"]), MAX_GMAIL_COMPOSE_URL_LEN)
        decoded_url25 = unquote(pkg25["gmail_compose_url"])
        for idx in range(1, 26):
            self.assertIn(f"VT-C4K-PTB{idx:02d}", decoded_url25)

        # Very large list sets gmail_body_truncated=True, appends explicit notice, and strictly respects encoded URL limit
        many_rows = [
            {
                **ten_rows[0],
                "class_code": f"VT-LARGE-{idx:03d}",
                "issue_description": "[LMS] Chưa ghi nội dung bài học; [LMS] Nhận xét học viên quá ngắn/sơ sài",
            }
            for idx in range(1, 80)
        ]
        pkg_large = build_failed_classes_email_package(
            week=43,
            year=2026,
            failed_rows=many_rows,
            total_rows_count=60,
            selected_job_id=397,
            recipients=["quangda@mindx.net.vn"],
        )
        self.assertTrue(pkg_large["gmail_body_truncated"])
        self.assertLessEqual(len(pkg_large["gmail_compose_url"]), MAX_GMAIL_COMPOSE_URL_LEN)
        self.assertIn("Ctrl+V", unquote(pkg_large["gmail_compose_url"]))

    async def test_hotfix_hostname_validation_temp_page_cleanup_and_rubric_regressions(
        self,
    ) -> None:
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from googleform_autosubmit.config import Config

        # 1. Under Phase 6.5 2-tier funnel: substantive >= 25-word comment passes Tier 1 without 3-part keywords,
        # while a short 1-line generic comment is flagged as REVIEW ("Nhận xét sơ sài").
        positive_substantive = (
            "NHẬN XÉT BUỔI HỌC SỐ 1: Học viên A (VT-C4K-PTB16)\n"
            "Con đi học đúng giờ, có thái độ học tập nghiêm túc và tập trung lắng nghe giáo viên giảng bài. "
            "Con cần tiếp tục duy trì và phát huy tinh thần học tập tích cực để tiếp thu kiến thức tốt hơn. "
            "Về nhà con làm bài tập đầy đủ nhé."
        )
        pos_slot = {
            "summary": "Tìm hiểu khái niệm biến và vòng lặp trong Python",
            "studentAttendance": [{"status": "ATTENDED", "comment": positive_substantive}],
        }
        pos_res = evaluate_lms_slot_quality(
            pos_slot, class_code="VT-C4K-PTB16", khoi="Coding", lesson=1
        )
        self.assertEqual(pos_res["lms_comments"]["status"], "COMPLETE")
        self.assertEqual(pos_res["issues"], [])

        short_generic_slot = {
            "summary": "Tìm hiểu khái niệm biến và vòng lặp trong Python",
            "studentAttendance": [
                {
                    "status": "ATTENDED",
                    "comment": "NHẬN XÉT BUỔI HỌC SỐ 1: Học viên A (VT-C4K-PTB16)\nCon học ngoan, cần phát huy.",
                }
            ],
        }
        short_res = evaluate_lms_slot_quality(
            short_generic_slot, class_code="VT-C4K-PTB16", khoi="Coding", lesson=1
        )
        self.assertEqual(short_res["lms_comments"]["status"], "REVIEW")
        self.assertEqual(issue_text(short_res["issues"][0]), "[LMS] Viết kỹ NX")

        # 2. Numbered Checkpoint rubric without "Điểm năng lực" on non-default session (B6) is recognized as Checkpoint
        numbered_rubric = (
            "1. KIẾN THỨC\nCon nắm vững các khái niệm thuật toán và cấu trúc dữ liệu.\n"
            "2. KỸ NĂNG\nCon vận dụng thành thạo kỹ năng lập trình và giải quyết vấn đề.\n"
            "3. THÁI ĐỘ\nCon tập trung, chủ động phát biểu và hợp tác tốt trong giờ học."
        )
        num_slot = {
            "summary": "Ôn tập các thuật toán và làm bài kiểm tra lập trình Python",
            "studentAttendance": [{"status": "ATTENDED", "comment": numbered_rubric}],
        }
        num_res = evaluate_lms_slot_quality(
            num_slot, class_code="SYNTH-JSB01", khoi="Coding", lesson=6
        )
        self.assertTrue(num_res["is_checkpoint"])
        self.assertEqual(num_res["issues"], [])

        # 3. Hostname spoofing in LMS_URL or redirect off lms.mindx.edu.vn must never trigger login,
        # and temp_page must close even if goto raises CancelledError.
        class _FakeTempPage:
            def __init__(self, *, cancel_on_goto: bool = False, redirect_url: str = "") -> None:
                self.url = "about:blank"
                self.closed = False
                self.listeners: dict[str, object] = {}
                self.removed_listeners: list[str] = []
                self._cancel_on_goto = cancel_on_goto
                self._redirect_url = redirect_url

            def on(self, event: str, cb: object) -> None:
                self.listeners[event] = cb

            def remove_listener(self, event: str, cb: object) -> None:
                self.removed_listeners.append(event)

            async def goto(self, target_url: str) -> None:
                if self._cancel_on_goto:
                    raise asyncio.CancelledError("simulated cancel during goto")
                self.url = self._redirect_url or target_url

            async def wait_for_load_state(self, state: str) -> None:
                return None

            async def evaluate(self, script: str, arg: dict) -> dict:
                return {
                    c: [{"summary": "Bài học hợp lệ đầy đủ nội dung", "studentAttendance": []}]
                    for c in arg["classCodes"]
                }

            async def close(self) -> None:
                self.closed = True

        class _FakeMainPage:
            def __init__(self, temp_page: _FakeTempPage) -> None:
                self.url = "https://teachingmindx.top/check_nhanxet.php"
                self.context = SimpleNamespace(
                    storage_state=AsyncMock(return_value={"origins": []}),
                    new_page=AsyncMock(return_value=temp_page),
                )

            async def evaluate(self, script: str, arg: dict) -> dict:
                return {}

        # Case 3a: Spoofed LMS_URL (lms.mindx.edu.vn.attacker.invalid) -> must not open temp_page or login
        temp_spoof = _FakeTempPage()
        main_spoof = _FakeMainPage(temp_spoof)
        main_spoof.evaluate = AsyncMock(return_value={})
        with (
            patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}),
            patch.object(Config, "LMS_URL", "https://lms.mindx.edu.vn.attacker.invalid/admin"),
            patch(
                "services.lms_service._login_lms_if_needed", new_callable=AsyncMock
            ) as login_mock,
        ):
            await fetch_lms_class_slots_via_page(main_spoof, class_names=["VT-C4K-PTB16"])
            self.assertEqual(login_mock.await_count, 0)
            self.assertEqual(main_spoof.context.new_page.await_count, 0)

        # Case 3b: Redirect to untrusted domain after goto -> must not call _login_lms_if_needed and must close temp_page
        temp_redirect = _FakeTempPage(redirect_url="https://evil.example.com/login")
        main_redirect = _FakeMainPage(temp_redirect)
        with (
            patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}),
            patch.object(Config, "LMS_URL", "https://lms.mindx.edu.vn/admin/class-sessions"),
            patch(
                "services.lms_service._login_lms_if_needed", new_callable=AsyncMock
            ) as login_mock,
        ):
            await fetch_lms_class_slots_via_page(main_redirect, class_names=["VT-C4K-PTB16"])
            self.assertEqual(login_mock.await_count, 0)
            self.assertTrue(temp_redirect.closed)
            self.assertIn("request", temp_redirect.removed_listeners)

        # Case 3c: CancelledError during goto -> temp_page is still closed in finally
        temp_cancel = _FakeTempPage(cancel_on_goto=True)
        main_cancel = _FakeMainPage(temp_cancel)
        with (
            patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}),
            patch.object(Config, "LMS_URL", "https://lms.mindx.edu.vn/admin/class-sessions"),
            self.assertRaises(asyncio.CancelledError),
        ):
            await fetch_lms_class_slots_via_page(main_cancel, class_names=["VT-C4K-PTB16"])
        self.assertTrue(temp_cancel.closed)
        self.assertIn("request", temp_cancel.removed_listeners)

    def test_stage5_3_lms_slot_selection_by_date_and_lesson_and_zalo_indeterminate(
        self,
    ) -> None:
        from services.zalo_bcnb_service import _collect_row_verifiable_auto_issues

        good_comment = (
            "Điểm mạnh: Con hiểu bài nhanh và hoàn thành tốt dự án.\n"
            "Điểm cần cải thiện: Cần chú ý kiểm tra kỹ cú pháp trước khi chạy.\n"
            "Lời khuyên: Về nhà con làm thêm bài tập trên LMS nhé."
        )
        valid_slot_base = {
            "summary": "Buổi học: Tìm hiểu cấu trúc dữ liệu danh sách và vòng lặp trong Python",
            "studentAttendance": [{"status": "ATTENDED", "comment": good_comment}],
        }
        bad_slot_base = {
            "summary": "",
            "studentAttendance": [{"status": "ATTENDED", "comment": "Học tốt."}],
        }

        # 1. Makeup-shifted slot order: Buổi 3 (index=2, date 2026-10-24) is at array position 1,
        # while position 2 has Buổi 2 rescheduled to 2026-10-27. Must select by (date, index), never position!
        makeup_slots = {
            "VT-C4K-PTB16": [
                {
                    **bad_slot_base,
                    "_id": "s0",
                    "index": 0,
                    "date": "2026-10-09T17:00:00.000Z",
                },
                {
                    **valid_slot_base,
                    "_id": "s2-at-pos1",
                    "index": 2,
                    "date": "2026-10-23T17:00:00.000Z",
                    "startTime": "2026-10-24T11:00:00.000Z",
                },
                {
                    **bad_slot_base,
                    "_id": "s1-makeup-at-pos2",
                    "index": 1,
                    "date": "2026-10-26T17:00:00.000Z",
                    "startTime": "2026-10-27T11:00:00.000Z",
                },
            ]
        }
        row_buoi3 = {
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "3",
                "class_date_iso": "2026-10-24",
                "khoi": "Coding",
            }
        }
        inspect_and_attach_quality_issues([row_buoi3], lms_slots_by_class=makeup_slots)
        self.assertEqual(row_buoi3["lms_quality_issues"], [])

        # And if BCNB checks Buổi 2 on original date 2026-10-17 (shifted on LMS to 2026-10-27),
        # must flag manual check instead of falling back to position 1!
        row_buoi2_shifted = {
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "2",
                "class_date_iso": "2026-10-17",
                "khoi": "Coding",
            }
        }
        inspect_and_attach_quality_issues([row_buoi2_shifted], lms_slots_by_class=makeup_slots)
        self.assertEqual(len(row_buoi2_shifted["lms_quality_issues"]), 1)
        self.assertEqual(
            issue_code(row_buoi2_shifted["lms_quality_issues"][0]),
            ISSUE_CODE_LMS_MANUAL_CHECK,
        )
        self.assertEqual(
            issue_text(row_buoi2_shifted["lms_quality_issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 2. Cancelled slot: excluded from matching; if no active replacement -> manual check
        cancelled_slots = {
            "VT-C4K-PTB16": [
                {
                    **valid_slot_base,
                    "index": 0,
                    "date": "2026-10-23T17:00:00.000Z",
                    "cancelled": True,
                }
            ]
        }
        row_cancelled = {
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "1",
                "class_date_iso": "2026-10-24",
            }
        }
        inspect_and_attach_quality_issues([row_cancelled], lms_slots_by_class=cancelled_slots)
        self.assertEqual(len(row_cancelled["lms_quality_issues"]), 1)
        self.assertEqual(
            issue_text(row_cancelled["lms_quality_issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 3. Two active slots on the same date -> manual check
        dup_date_slots = {
            "VT-C4K-PTB16": [
                {
                    **valid_slot_base,
                    "index": 0,
                    "date": "2026-10-23T17:00:00.000Z",
                },
                {
                    **valid_slot_base,
                    "index": 0,
                    "date": "2026-10-23T17:00:00.000Z",
                },
            ]
        }
        row_dup_date = {
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "1",
                "class_date_iso": "2026-10-24",
            }
        }
        inspect_and_attach_quality_issues([row_dup_date], lms_slots_by_class=dup_date_slots)
        self.assertEqual(len(row_dup_date["lms_quality_issues"]), 1)
        self.assertEqual(
            issue_text(row_dup_date["lms_quality_issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 4. Same date but wrong lesson number (index=3 -> Buổi 4 vs BCNB Buổi 1) -> manual check
        wrong_lesson_slots = {
            "VT-C4K-PTB16": [
                {
                    **valid_slot_base,
                    "index": 3,
                    "date": "2026-10-23T17:00:00.000Z",
                }
            ]
        }
        row_wrong_lesson = {
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "1",
                "class_date_iso": "2026-10-24",
            }
        }
        inspect_and_attach_quality_issues([row_wrong_lesson], lms_slots_by_class=wrong_lesson_slots)
        self.assertEqual(len(row_wrong_lesson["lms_quality_issues"]), 1)
        self.assertEqual(
            issue_text(row_wrong_lesson["lms_quality_issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 5. Missing date metadata on slot -> manual check (no positional fallback!)
        no_date_slots = {"VT-C4K-PTB16": [{**valid_slot_base, "index": 0}]}
        row_no_date = {
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "1",
                "class_date_iso": "2026-10-24",
            }
        }
        inspect_and_attach_quality_issues([row_no_date], lms_slots_by_class=no_date_slots)
        self.assertEqual(len(row_no_date["lms_quality_issues"]), 1)
        self.assertEqual(
            issue_text(row_no_date["lms_quality_issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 5b. Missing lesson metadata (no index/slot_index/lesson) on slot with matching date -> manual check!
        no_lesson_slots = {
            "VT-C4K-PTB16": [
                {
                    **valid_slot_base,
                    "date": "2026-10-23T17:00:00.000Z",
                }
            ]
        }
        row_no_lesson = {
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "1",
                "class_date_iso": "2026-10-24",
            }
        }
        inspect_and_attach_quality_issues([row_no_lesson], lms_slots_by_class=no_lesson_slots)
        self.assertEqual(len(row_no_lesson["lms_quality_issues"]), 1)
        self.assertEqual(
            issue_text(row_no_lesson["lms_quality_issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 6. Timezone boundary: 2026-10-23T16:59:59.000Z is 2026-10-23 23:59:59 ICT (does not match 2026-10-24),
        # while 2026-10-23T17:00:00.000Z is 2026-10-24 00:00:00 ICT (matches 2026-10-24).
        tz_prev_day_slots = {
            "VT-C4K-PTB16": [
                {
                    **valid_slot_base,
                    "index": 0,
                    "date": "2026-10-23T16:59:59.000Z",
                }
            ]
        }
        row_tz_prev = {
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "1",
                "class_date_iso": "2026-10-24",
            }
        }
        inspect_and_attach_quality_issues([row_tz_prev], lms_slots_by_class=tz_prev_day_slots)
        self.assertEqual(len(row_tz_prev["lms_quality_issues"]), 1)
        self.assertEqual(
            issue_text(row_tz_prev["lms_quality_issues"][0]),
            "[LMS] Kiểm tra tay trên LMS",
        )

        # 7. 5.3.4: Indeterminate Zalo scan (INSUFFICIENT_EVIDENCE or scan error) preserves existing [Zalo] issues on BCNB
        existing_bcnb_note = (
            "Ghi chú tay | [Zalo] Báo cáo ghi sai buổi; [LMS] Chưa ghi nội dung bài học"
        )
        proven_issues = [
            "[Zalo] Báo cáo ghi sai buổi",
            "[LMS] Chưa ghi nội dung bài học",
        ]
        insufficient_res = {
            "evidence_state": "INSUFFICIENT_EVIDENCE",
            "zalo_content_issues": [],
            "lms_quality_issues": [],
        }
        combined_insuff, should_sync_insuff = _collect_row_verifiable_auto_issues(
            insufficient_res,
            existing_note=existing_bcnb_note,
            lms_slots_by_class={"VT-C4K-PTB16": []},
            proven_auto_issues=proven_issues,
        )
        self.assertEqual(combined_insuff, ["[Zalo] Báo cáo ghi sai buổi"])
        self.assertTrue(should_sync_insuff)

        error_res = {
            "evidence_state": "NOT_FOUND_VERIFIED",
            "error_code": "TARGET_TIMEOUT",
            "scan_status": "FAILED",
            "zalo_content_issues": [],
            "lms_quality_issues": [],
        }
        combined_err, _ = _collect_row_verifiable_auto_issues(
            error_res,
            existing_note=existing_bcnb_note,
            lms_slots_by_class={"VT-C4K-PTB16": []},
            proven_auto_issues=proven_issues,
        )
        self.assertEqual(combined_err, ["[Zalo] Báo cáo ghi sai buổi"])

    async def test_5_8_5_lms_manual_check_note_written_and_system_outage_suppressed(
        self,
    ) -> None:
        from datetime import datetime
        from types import SimpleNamespace
        import services.zalo_bcnb_service as bcnb
        from services.lms_quality_service import is_lms_fetch_unavailable
        from services.report_status import is_failed
        from services.zalo_bcnb_service import _collect_row_verifiable_auto_issues

        # 1. Single class missing slot -> structured LMS_MANUAL_CHECK NOTE is included in auto issues to write to BCNB
        #    and evaluate_row_status keeps is_failed=False.
        single_missing_res = {
            "evidence_state": "SUBMITTED_VERIFIED",
            "status": "✅ Đã báo cáo – Minh chứng hợp lệ",
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "2",
                "class_date_iso": "2026-10-25",
                "week": 43,
                "year": 2026,
            },
        }
        slots_one_missing = {
            "VT-C4K-PTB16": [
                {
                    "index": 0,
                    "date": "2026-10-17T17:00:00.000Z",
                    "summary": "Buổi 1: Làm quen lập trình Python",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            "comment": "Điểm mạnh: Tốt.\nĐiểm cần cải thiện: Chú ý.\nLời khuyên: Ôn bài.",
                        }
                    ],
                }
            ]
        }
        inspect_and_attach_quality_issues(
            [single_missing_res],
            lms_slots_by_class=slots_one_missing,
        )
        self.assertFalse(is_lms_fetch_unavailable(slots_one_missing))
        self.assertFalse(is_failed(single_missing_res))
        auto_issues_single, should_sync_single = _collect_row_verifiable_auto_issues(
            single_missing_res,
            existing_note="",
            lms_slots_by_class=slots_one_missing,
        )
        self.assertTrue(should_sync_single)
        self.assertEqual(len(auto_issues_single), 1)
        self.assertEqual(issue_text(auto_issues_single[0]), "[LMS] Kiểm tra tay trên LMS")

        # 2. Prior verified [LMS] FAIL issue is preserved alongside single-class LMS_MANUAL_CHECK when indeterminate
        prior_fail_res = {
            "evidence_state": "SUBMITTED_VERIFIED",
            "status": "✅ Đã báo cáo – Minh chứng hợp lệ",
            "lms_quality_issues": ["[LMS] Chưa ghi nội dung bài học"],
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "2",
                "class_date_iso": "2026-10-25",
                "week": 43,
                "year": 2026,
            },
        }
        inspect_and_attach_quality_issues(
            [prior_fail_res],
            lms_slots_by_class=slots_one_missing,
        )
        texts_prior = [issue_text(x) for x in prior_fail_res["lms_quality_issues"]]
        self.assertIn("[LMS] Chưa ghi nội dung bài học", texts_prior)
        auto_with_prior, _ = _collect_row_verifiable_auto_issues(
            prior_fail_res,
            existing_note="[LMS] Chưa ghi nội dung bài học",
            lms_slots_by_class=slots_one_missing,
            proven_auto_issues=["[LMS] Chưa ghi nội dung bài học"],
        )
        self.assertIn(
            "[LMS] Chưa ghi nội dung bài học",
            [issue_text(x) for x in auto_with_prior],
        )

        # 3. System-wide LMS outage (all requested classes fetch_error or gateway raises) ->
        #    zero note POSTs for LMS_MANUAL_CHECK, lms_fetch_unavailable=True in summary,
        #    while prior verified [LMS] FAIL issues are still preserved!
        outage_slots = {
            "VT-C4K-PTB16": [{"fetch_error": True}],
            "VT-C4K-PTI11": [{"fetch_error": True}],
        }
        self.assertTrue(is_lms_fetch_unavailable(outage_slots))
        outage_row_clean = {
            "evidence_state": "SUBMITTED_VERIFIED",
            "status": "✅ Đã báo cáo – Minh chứng hợp lệ",
            "scan_target": {
                "class_code": "VT-C4K-PTB16",
                "lesson": "2",
                "class_date": "25/10/2026",
                "class_date_iso": "2026-10-25",
                "week": 43,
                "year": 2026,
            },
        }
        inspect_and_attach_quality_issues(
            [outage_row_clean],
            lms_slots_by_class=outage_slots,
        )
        self.assertTrue(outage_row_clean.get("lms_fetch_unavailable"))
        auto_outage, should_sync_outage = _collect_row_verifiable_auto_issues(
            outage_row_clean,
            existing_note="",
            lms_slots_by_class=outage_slots,
        )
        self.assertEqual(auto_outage, [])
        self.assertFalse(should_sync_outage)

        # Verify update_bcnb_reports sets lms_fetch_unavailable=True and makes 0 checker note POSTs on system-wide outage
        outage_gw = SimpleNamespace(
            fetch_class_slots=AsyncMock(side_effect=RuntimeError("LMS API 503"))
        )
        note_post_mock = AsyncMock()
        with (
            patch.object(bcnb, "_read_bcnb_schedule_rows", AsyncMock(return_value=[])),
            patch.object(bcnb, "resolve_report_columns", AsyncMock(return_value=(0, 1, 2))),
            patch.object(bcnb, "execute_verified_checker_note_action", note_post_mock),
            patch.object(bcnb, "log_message"),
        ):
            summary_outage = await bcnb.update_bcnb_reports(
                object(),
                job_id=585,
                execution_mode="production",
                scan_results=[outage_row_clean],
                today=datetime(2026, 10, 26),
                scan_plan={
                    "selected_weeks": [
                        {"week": 42, "year": 2026},
                        {"week": 43, "year": 2026},
                    ],
                    "latest_week": {"week": 43, "year": 2026},
                    "targets": [],
                },
                job_repo=SimpleNamespace(ensure_not_cancelled=AsyncMock()),
                audit_repo=SimpleNamespace(record_intent=AsyncMock()),
                lms_quality_gateway=outage_gw,
                notes_only=True,
            )
        self.assertTrue(summary_outage["lms_fetch_unavailable"])
        note_post_mock.assert_not_awaited()

    async def test_expired_jwt_rejection_sso_login_and_auth_recovery_retry(
        self,
    ) -> None:
        import base64
        import contextlib
        import json
        import subprocess
        import time
        from types import SimpleNamespace
        from googleform_autosubmit.config import Config
        from services.lms_quality_service import _is_usable_lms_token

        def _make_jwt(exp_epoch: int) -> str:
            hdr = (
                base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
                .decode("ascii")
                .rstrip("=")
            )
            body = (
                base64.urlsafe_b64encode(
                    json.dumps({"sub": "u1", "exp": exp_epoch}).encode("utf-8")
                )
                .decode("ascii")
                .rstrip("=")
            )
            return f"{hdr}.{body}.synthetic_signature_part_123456"

        now_sec = int(time.time())
        expired_jwt = _make_jwt(now_sec - 600)
        valid_jwt = _make_jwt(now_sec + 3600)

        self.assertFalse(_is_usable_lms_token(expired_jwt, now_epoch=now_sec))
        self.assertTrue(_is_usable_lms_token(valid_jwt, now_epoch=now_sec))
        self.assertFalse(
            _is_usable_lms_token(
                valid_jwt,
                expiration_time=(now_sec - 10) * 1000,
                now_epoch=now_sec,
            )
        )

        # 1. Expired JWT in storage_state is ignored; page navigates to LMS_URL -> redirects to id.mindx.edu.vn/login ->
        #    _login_lms_if_needed logs in and lands back on lms.mindx.edu.vn -> fresh token captured via request listener.
        class _SsoLoginPage:
            def __init__(self) -> None:
                self.url = "about:blank"
                self.listeners: dict[str, object] = {}
                self.goto_calls: list[str] = []
                self.eval_calls: list[dict] = []
                self.context = SimpleNamespace(
                    storage_state=AsyncMock(
                        return_value={
                            "origins": [
                                {
                                    "origin": "https://lms.mindx.edu.vn",
                                    "localStorage": [{"name": "id_token", "value": expired_jwt}],
                                }
                            ]
                        }
                    )
                )

            def on(self, event: str, cb: object) -> None:
                self.listeners[event] = cb

            def remove_listener(self, event: str, cb: object) -> None:
                self.listeners.pop(event, None)

            async def goto(self, target_url: str) -> None:
                self.goto_calls.append(target_url)
                if len(self.goto_calls) == 1:
                    self.url = "https://id.mindx.edu.vn/login"
                    req_cb = self.listeners.get("request")
                    if callable(req_cb):
                        # Stale request with expired token must be ignored
                        req_cb(
                            SimpleNamespace(
                                url="https://lms-api.mindx.edu.vn/",
                                method="POST",
                                headers={"authorization": f"Bearer {expired_jwt}"},
                                post_data="{}",
                            )
                        )
                else:
                    self.url = target_url

            async def wait_for_load_state(self, state: str) -> None:
                return None

            async def evaluate(self, script: str, arg: dict) -> dict:
                if isinstance(arg, dict) and "classCodes" in arg:
                    self.eval_calls.append(dict(arg))
                    if arg.get("preResolvedToken") == f"Bearer {valid_jwt}":
                        return {
                            c: [
                                {
                                    "_id": "s1",
                                    "index": 0,
                                    "date": "2026-10-31T07:00:00.000Z",
                                    "summary": "Buổi 1: Lập trình điều khiển cảm biến khoảng cách",
                                    "studentAttendance": [],
                                }
                            ]
                            for c in arg["classCodes"]
                        }
                    return {
                        "__auth_error__": True,
                        "__failure_reason__": "missing_or_expired_token",
                        **{c: [{"fetch_error": True}] for c in arg["classCodes"]},
                    }
                return {}

        sso_page = _SsoLoginPage()

        async def _fake_sso_login(p: _SsoLoginPage) -> None:
            p.url = "https://lms.mindx.edu.vn/admin/class-sessions"
            req_cb = p.listeners.get("request")
            if callable(req_cb):
                req_cb(
                    SimpleNamespace(
                        url="https://lms-api.mindx.edu.vn/",
                        method="POST",
                        headers={"authorization": f"Bearer {valid_jwt}"},
                        post_data='{"variables":{"centres":["62d170076063450019b720be"]}}',
                    )
                )

        with (
            patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}),
            patch.object(Config, "LMS_URL", "https://lms.mindx.edu.vn/admin/class-sessions"),
            patch(
                "services.lms_service._login_lms_if_needed",
                side_effect=_fake_sso_login,
            ) as sso_login_mock,
        ):
            fetched_sso = await fetch_lms_class_slots_via_page(
                sso_page, class_names=["VT-ROB-ARMB20"]
            )
            self.assertEqual(sso_login_mock.await_count, 1)
            self.assertEqual(len(sso_page.eval_calls), 1)
            self.assertEqual(
                sso_page.eval_calls[0]["preResolvedToken"],
                f"Bearer {valid_jwt}",
            )
            self.assertFalse(fetched_sso["VT-ROB-ARMB20"][0].get("fetch_error", False))

        # 1b. Redirect to https://base.mindx.edu.vn/login?callbackUrl=... is also trusted
        class _BaseSsoLoginPage(_SsoLoginPage):
            async def goto(self, target_url: str) -> None:
                self.goto_calls.append(target_url)
                if len(self.goto_calls) == 1:
                    self.url = (
                        "https://base.mindx.edu.vn/login?callbackUrl="
                        "https://lms.mindx.edu.vn/admin/class-sessions"
                    )
                else:
                    self.url = target_url

        base_sso_page = _BaseSsoLoginPage()
        with (
            patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}),
            patch.object(Config, "LMS_URL", "https://lms.mindx.edu.vn/admin/class-sessions"),
            patch(
                "services.lms_service._login_lms_if_needed",
                side_effect=_fake_sso_login,
            ) as base_sso_login_mock,
        ):
            fetched_base_sso = await fetch_lms_class_slots_via_page(
                base_sso_page, class_names=["VT-ROB-ARMB20"]
            )
            self.assertEqual(base_sso_login_mock.await_count, 1)
            self.assertEqual(len(base_sso_page.eval_calls), 1)
            self.assertFalse(fetched_base_sso["VT-ROB-ARMB20"][0].get("fetch_error", False))

        # 2. JS findToken() ignores expired JWT in localStorage and picks valid JWT;
        #    and when 401/UNAUTHENTICATED occurs, JS short-circuits with __auth_error__=True.
        runner_auth_js = r"""
        const fs = require('fs');
        const { script, args, expiredTok, validTok, mode } = JSON.parse(fs.readFileSync(0, 'utf8'));
        global.window = { location: { hostname: 'lms.mindx.edu.vn' } };
        const items = mode === 'fallback_valid'
            ? [['id_token', expiredTok], ['access_token', validTok]]
            : [['id_token', validTok]];
        global.localStorage = {
            length: items.length,
            key: (i) => items[i][0],
            getItem: (k) => (items.find((x) => x[0] === k) || [null, ''])[1]
        };
        let fetchCount = 0;
        global.fetch = async (_url, req) => {
            fetchCount += 1;
            const authHdr = req.headers.authorization || '';
            if (mode === 'graphql_401' || authHdr.includes(expiredTok)) {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        errors: [{ message: 'Unauthenticated', extensions: { code: 'UNAUTHENTICATED' } }]
                    })
                };
            }
            const code = JSON.parse(req.body).variables.search;
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    data: {
                        classesMissAttendOrComment: {
                            data: [{
                                name: code,
                                slots: [{
                                    _id: 's1',
                                    index: 0,
                                    date: '2026-10-31T07:00:00.000Z',
                                    summary: 'Buổi 1: Lập trình Scratch cơ bản',
                                    studentAttendance: []
                                }]
                            }]
                        }
                    }
                })
            };
        };
        (async () => {
            const res = await eval('(' + script + ')')(args);
            res.__fetch_count__ = fetchCount;
            process.stdout.write(JSON.stringify(res));
        })();
        """

        class _NodeAuthEvalPage:
            def __init__(self, mode: str) -> None:
                self.mode = mode
                self.last_raw: dict = {}

            async def evaluate(self, script: str, args: dict) -> dict:
                payload_json = json.dumps(
                    {
                        "script": script,
                        "args": args,
                        "expiredTok": expired_jwt,
                        "validTok": valid_jwt,
                        "mode": self.mode,
                    }
                )
                proc = subprocess.run(
                    ["node", "-e", runner_auth_js],
                    input=payload_json,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=True,
                )
                self.last_raw = json.loads(proc.stdout)
                return self.last_raw

        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}):
            page_valid_fallback = _NodeAuthEvalPage("fallback_valid")
            res_fb = await fetch_lms_class_slots_via_page(
                page_valid_fallback, class_names=["VT-C4K-SA28"]
            )
            self.assertFalse(res_fb["VT-C4K-SA28"][0].get("fetch_error", False))

            page_401 = _NodeAuthEvalPage("graphql_401")
            res_401 = await fetch_lms_class_slots_via_page(
                page_401,
                class_names=["VT-C4K-SA28", "VT-C4K-GB43", "VT-ROB-ARMB20"],
            )
            self.assertTrue(page_401.last_raw.get("__auth_error__"))
            # Short-circuits on first UNAUTHENTICATED without retrying every class twice
            self.assertLessEqual(page_401.last_raw.get("__fetch_count__", 99), 3)
            self.assertTrue(res_401["VT-C4K-SA28"][0].get("fetch_error"))

        # 3. Clearing lms_fetch_unavailable when subsequent evaluation succeeds
        rec_row = {
            "evidence_state": "SUBMITTED_VERIFIED",
            "scan_target": {
                "class_code": "VT-C4K-SA28",
                "lesson": "1",
                "class_date": "31/10/2026",
                "class_date_iso": "2026-10-31",
            },
        }
        inspect_and_attach_quality_issues(
            [rec_row],
            lms_slots_by_class={"VT-C4K-SA28": [{"fetch_error": True}]},
        )
        self.assertTrue(rec_row.get("lms_fetch_unavailable"))
        self.assertTrue(rec_row["scan_target"].get("lms_fetch_unavailable"))

        inspect_and_attach_quality_issues(
            [rec_row],
            lms_slots_by_class={
                "VT-C4K-SA28": [
                    {
                        "_id": "s1",
                        "index": 0,
                        "date": "2026-10-31T07:00:00.000Z",
                        "summary": "Buổi 1: Tìm hiểu giao diện lập trình Scratch và hệ trục tọa độ",
                        "studentAttendance": [
                            {
                                "status": "ATTENDED",
                                "comment": (
                                    "Điểm mạnh: Con hiểu bài rất nhanh và thao tác tốt.\n"
                                    "Điểm cần cải thiện: Cần chú ý đặt tên biến rõ ràng hơn.\n"
                                    "Lời khuyên: Về nhà con ôn lại bài và hoàn thành bài tập trên LMS nhé."
                                ),
                            }
                        ],
                    }
                ]
            },
        )
        self.assertIsNone(rec_row.get("lms_fetch_unavailable"))
        self.assertIsNone(rec_row["scan_target"].get("lms_fetch_unavailable"))
        self.assertEqual(rec_row.get("lms_quality_issues"), [])

        # 4. Post-login HTTP downgrade (http://lms.mindx.edu.vn) must be rejected without evaluating js_script
        http_downgrade_page = _SsoLoginPage()

        async def _fake_http_downgrade_login(p: _SsoLoginPage) -> None:
            p.url = "http://lms.mindx.edu.vn/admin/class-sessions"

        with (
            patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}),
            patch.object(Config, "LMS_URL", "https://lms.mindx.edu.vn/admin/class-sessions"),
            patch(
                "services.lms_service._login_lms_if_needed",
                side_effect=_fake_http_downgrade_login,
            ),
        ):
            res_http = await fetch_lms_class_slots_via_page(
                http_downgrade_page, class_names=["VT-ROB-ARMB20"]
            )
            self.assertEqual(len(http_downgrade_page.eval_calls), 0)
            self.assertTrue(res_http["VT-ROB-ARMB20"][0].get("fetch_error"))

        # 5. Auth recovery from about:blank when pre_resolved_token is not expired locally but rejected by server:
        #    must navigate to https://lms.mindx.edu.vn first, clear stale storage there, reload, login, and retry.
        class _BlankRecoveryPage(_SsoLoginPage):
            def __init__(self) -> None:
                super().__init__()
                self.clear_storage_origins: list[str] = []
                self.context = SimpleNamespace(
                    storage_state=AsyncMock(
                        return_value={
                            "origins": [
                                {
                                    "origin": "https://lms.mindx.edu.vn",
                                    "localStorage": [
                                        {
                                            "name": "id_token",
                                            "value": _make_jwt(now_sec + 1800),
                                        }
                                    ],
                                }
                            ]
                        }
                    )
                )

            async def goto(self, target_url: str) -> None:
                self.goto_calls.append(target_url)
                self.url = target_url

            async def evaluate(self, script: str, arg: dict) -> dict:
                if isinstance(arg, dict) and "allowedTokenKeys" in arg and "classCodes" not in arg:
                    self.clear_storage_origins.append(self.url)
                    return {}
                if isinstance(arg, dict) and "classCodes" in arg:
                    self.eval_calls.append(dict(arg))
                    if len(self.eval_calls) == 1:
                        return {
                            "__auth_error__": True,
                            "__failure_reason__": "graphql_auth_rejected",
                            **{c: [{"fetch_error": True}] for c in arg["classCodes"]},
                        }
                    return {
                        c: [
                            {
                                "_id": "s1",
                                "index": 0,
                                "date": "2026-10-31T07:00:00.000Z",
                                "summary": "Buổi 1: Khôi phục phiên LMS thành công",
                                "studentAttendance": [],
                            }
                        ]
                        for c in arg["classCodes"]
                    }
                return {}

        blank_rec_page = _BlankRecoveryPage()
        with (
            patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}),
            patch.object(Config, "LMS_URL", "https://lms.mindx.edu.vn/admin/class-sessions"),
            patch(
                "services.lms_service._login_lms_if_needed",
                side_effect=_fake_sso_login,
            ) as rec_login_mock,
        ):
            res_rec = await fetch_lms_class_slots_via_page(
                blank_rec_page, class_names=["VT-ROB-ARMB20"]
            )
            self.assertEqual(
                blank_rec_page.clear_storage_origins,
                ["https://lms.mindx.edu.vn/admin/class-sessions"],
            )
            self.assertEqual(rec_login_mock.await_count, 1)
            self.assertEqual(len(blank_rec_page.eval_calls), 2)
            self.assertFalse(res_rec["VT-ROB-ARMB20"][0].get("fetch_error", False))

        # 6. Concurrent write_workers wait on lms_prefetch_retry_lock until the single retry finishes,
        #    and both workers receive the refreshed LMS slots before update_bcnb_reports runs.
        import asyncio
        from datetime import datetime
        from core.time_utils import BUSINESS_TIMEZONE
        import services.zalo_parallel_service as par
        from tests.test_zalo_parallel_service import (
            FakeParallelBrowser,
            InMemoryBatchRepository,
            _make_search_target,
            _verified_scan_result,
        )

        t_w34 = _make_search_target(1)
        t_w34["week"] = 34
        t_w34["year"] = 2026
        t_w34["lesson"] = "1"
        t_w35 = _make_search_target(2)
        t_w35["week"] = 35
        t_w35["year"] = 2026
        t_w35["lesson"] = "1"

        fetch_calls = 0
        observed_worker_slots: list[dict] = []

        async def _fake_pw_fetch(
            _page: object, codes: list[str], *, target_lessons_by_class: dict | None = None
        ) -> dict:
            nonlocal fetch_calls
            fetch_calls += 1
            if fetch_calls == 1:
                return {c: [{"fetch_error": True}] for c in codes}
            await asyncio.sleep(0.02)
            return {
                c: [
                    {
                        "_id": "s1",
                        "index": 0,
                        "date": "2026-08-20T17:00:00.000Z",
                        "summary": "Buổi 1: Nội dung bài học sau khi retry thành công",
                        "studentAttendance": [
                            {
                                "status": "ATTENDED",
                                "comment": (
                                    "Điểm mạnh: Con nắm bài tốt.\n"
                                    "Điểm cần cải thiện: Chú ý cú pháp.\n"
                                    "Lời khuyên: Ôn lại bài ở nhà."
                                ),
                            }
                        ],
                    }
                ]
                for c in codes
            }

        async def _fake_update_bcnb(
            _page: object,
            *,
            scan_results: list[dict],
            period_filter: set | None = None,
            lms_slots_by_class: dict | None = None,
            **_kwargs: object,
        ) -> dict:
            observed_worker_slots.append(dict(lms_slots_by_class or {}))
            outcomes = []
            for r in scan_results:
                st = r.get("scan_target") or {}
                pair = (int(st.get("week") or 0), int(st.get("year") or 0))
                if period_filter is None or pair in period_filter:
                    outcomes.append(
                        {
                            "class_code": st.get("class_code"),
                            "lesson": st.get("lesson"),
                            "week": pair[0],
                            "year": pair[1],
                            "class_date_iso": st.get("class_date_iso"),
                            "status": "DRY_RUN",
                        }
                    )
            return {
                "selected_weeks": [],
                "updated_count": len(outcomes),
                "target_outcomes": outcomes,
                "lms_fetch_unavailable": False,
            }

        base_browser = FakeParallelBrowser([{}, {}, {}, {}])

        class _LiveBrowserAdapter:
            @contextlib.asynccontextmanager
            async def persistent_session(self):
                async with base_browser.persistent_session() as sess:
                    LiveSessCls = type(
                        "_LiveSessionProxy",
                        (type(sess),),
                        {"__module__": "services.external_gateways"},
                    )
                    sess.__class__ = LiveSessCls
                    yield sess

        pw_gw = PlaywrightLmsQualityGateway(timeout_ms=5000)
        par_repo = InMemoryBatchRepository()
        par_runtime = SimpleNamespace(
            external=SimpleNamespace(
                browser=_LiveBrowserAdapter(),
                lms_quality=pw_gw,
            ),
            zalo_scan_concurrency=1,
            bcnb_write_concurrency=2,
            batch_systemic_failure_limit=3,
            batch_abnormal_percent_limit=20,
        )
        with (
            patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}),
            patch.object(
                par.Config,
                "LMS_URL",
                "https://lms.mindx.edu.vn/admin/class-sessions",
            ),
            patch.object(pw_gw, "fetch_class_slots", side_effect=_fake_pw_fetch),
            patch.object(par, "ZaloBatchRepository", return_value=par_repo),
            patch.object(
                par,
                "ensure_zalo_history_synced",
                AsyncMock(return_value={"verified": True, "reason": "fixture"}),
            ),
            patch.object(par, "_login_bcnb_if_needed", AsyncMock()),
            patch.object(
                par,
                "_scan_one_target",
                AsyncMock(side_effect=lambda _p, **kw: _verified_scan_result(kw["target"])),
            ),
            patch.object(par, "update_bcnb_reports", new=_fake_update_bcnb),
            patch.object(par, "log_message"),
        ):
            par_res = await par.run_parallel_zalo_batch(
                job_id=2002,
                execution_mode="dry-run",
                scan_plan={
                    "selected_weeks": [
                        {"week": 34, "year": 2026},
                        {"week": 35, "year": 2026},
                    ],
                    "latest_week": {"week": 35, "year": 2026},
                    "targets": [t_w34, t_w35],
                },
                today=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                scan_started_at=datetime(2026, 8, 25, tzinfo=BUSINESS_TIMEZONE),
                evidence_root="unused-evidence-root",
                screenshots_dir="unused-screenshots-dir",
                runtime=par_runtime,
                job_repo=SimpleNamespace(ensure_not_cancelled=AsyncMock(), update_job=AsyncMock()),
                audit_repo=object(),
                evidence_repo=SimpleNamespace(register_scan_results=AsyncMock()),
            )
        # 1 initial prefetch + 1 shared retry across both concurrent write_workers = 2 calls total
        self.assertEqual(fetch_calls, 2)
        self.assertEqual(len(observed_worker_slots), 2)
        for worker_map in observed_worker_slots:
            self.assertEqual(set(worker_map.keys()), {"VT-SYN01", "VT-SYN02"})
            for c_slots in worker_map.values():
                self.assertFalse(c_slots[0].get("fetch_error", False))
        self.assertFalse(par_res["lms_fetch_unavailable"])

    async def test_spck_quality_evaluation_and_browser_fetch_pii_scrubbing(self) -> None:
        import base64
        from datetime import datetime
        import json
        import subprocess
        import time
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from core.time_utils import BUSINESS_TIMEZONE
        from services.lms_quality_service import (
            ISSUE_CODE_LMS_SPCK_MISSING,
            ISSUE_CODE_LMS_SPCK_NOTE,
            evaluate_lms_spck_quality,
        )
        from services.report_status import (
            LESSON_14_EXEMPTION,
            LESSON_14_STATUS,
            SEVERITY_FAIL,
            is_failed,
            issue_code,
            issue_severity,
            issue_text,
        )
        import services.zalo_bcnb_service as bcnb

        substantive_comment = (
            "1. Nhận xét chung:\n"
            "Sản phẩm cuối khóa của [HS] có ý tưởng rõ ràng, bố cục hợp lý.\n"
            "2. Giao diện & Chức năng:\n"
            "Trang web thiết kế đẹp mắt, các chức năng tìm kiếm và giỏ hàng hoạt động ổn định, "
            "cần bổ sung thêm hiệu ứng chuyển trang để hoàn thiện hơn."
        )
        valid_work_b12 = {
            "studentKey": "stu_1",
            "title": "[HS] - Quản lý thú cưng",
            "hasStudentInfoInTitle": True,
            "thumbnail": "https://static.mindx.edu.vn/uploads/thumb1.png",
            "imageUrl": ["https://static.mindx.edu.vn/uploads/screen1.png"],
            "attachmentUrls": ["https://static.mindx.edu.vn/uploads/source.zip"],
            "videoUrls": [],
            "relatedUrls": [],
            "comment": substantive_comment,
        }
        attended_two = [
            {
                "status": "ATTENDED",
                "studentKey": "stu_1",
                "comment": (
                    "Điểm mạnh: Con nắm vững kiến thức bài học và hoàn thành tốt phần giao diện.\n"
                    "Điểm cần cải thiện: Cần chú ý đặt tên biến rõ ràng hơn.\n"
                    "Lời khuyên: Về nhà con ôn tập thêm các thuộc tính Flexbox nhé."
                ),
            },
            {
                "status": "LATE_ARRIVED",
                "studentKey": "stu_2",
                "comment": (
                    "Điểm mạnh: Con chủ động tích cực xây dựng chức năng cho sản phẩm.\n"
                    "Điểm cần cải thiện: Đôi khi còn quên đóng thẻ HTML.\n"
                    "Lời khuyên: Con nên kiểm tra lại code trước khi chạy thử nhé."
                ),
            },
        ]
        valid_work_b12_stu2 = {
            **valid_work_b12,
            "studentKey": "stu_2",
            "title": "[HS] - Web bán đồ ăn",
        }

        # 1. Buổi 12 & 13: Valid SPCK without YouTube/Slide -> 0 issues
        for lesson_no in (12, 13):
            res_ok = evaluate_lms_slot_quality(
                {
                    "summary": f"Buổi {lesson_no}: Hoàn thiện sản phẩm cuối khóa",
                    "studentAttendance": attended_two,
                    "studentWorks": [valid_work_b12, valid_work_b12_stu2],
                },
                class_code="VT-JSB15",
                khoi="Coding",
                lesson=lesson_no,
            )
            self.assertEqual(res_ok["issues"], [])

        from services.report_status import ISSUE_CODE_LMS_SUMMARY_NOTE

        # 2. Buổi 14: Valid B12/13 SPCK but missing summary ranking & YouTube & Slide -> NOTE issues (not FAIL)
        res_b14_missing_media = evaluate_lms_slot_quality(
            {
                "summary": "",
                "studentAttendance": attended_two,
                "studentWorks": [valid_work_b12, valid_work_b12_stu2],
            },
            class_code="VT-JSB15",
            khoi="Coding",
            lesson=14,
        )
        self.assertTrue(res_b14_missing_media["is_exempt"])
        self.assertEqual(
            [issue_text(x) for x in res_b14_missing_media["issues"]],
            ["[LMS] Thiếu xếp hạng", "[SPCK] Bổ sung YouTube, Slide"],
        )
        self.assertEqual(
            [issue_code(x) for x in res_b14_missing_media["issues"]],
            [ISSUE_CODE_LMS_SUMMARY_NOTE, ISSUE_CODE_LMS_SPCK_NOTE],
        )
        for item in res_b14_missing_media["issues"]:
            self.assertEqual(issue_severity(item), SEVERITY_NOTE)

        # 3. Buổi 14: With valid ranking summary, YouTube link and Canva/Slide link or .pptx file -> 0 issues
        valid_b14_stu1 = {
            **valid_work_b12,
            "videoUrls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            "relatedUrls": [
                {
                    "name": "Slide thuyết trình Canva",
                    "url": "https://www.canva.com/design/DAG123456/view",
                }
            ],
        }
        valid_b14_stu2 = {
            **valid_work_b12_stu2,
            "relatedUrls": [{"name": "Video demo", "url": "https://youtu.be/abc123XYZ89"}],
            "attachmentUrls": [
                "https://static.mindx.edu.vn/uploads/source.zip",
                "https://static.mindx.edu.vn/uploads/slide_thuyet_trinh.pptx",
            ],
        }
        res_b14_ok = evaluate_lms_slot_quality(
            {
                "summary": "1. Nguyễn Văn An\n2. Trần Văn Bình",
                "studentAttendance": attended_two,
                "studentWorks": [valid_b14_stu1, valid_b14_stu2],
            },
            class_code="VT-JSB15",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(res_b14_ok["issues"], [])

        # 4. 0/N submitted SPCK on Buổi 12, 13, 14 -> LMS_SPCK_MISSING (SEVERITY_FAIL, is_failed=True)
        for lesson_no in (12, 13, 14):
            res_zero = evaluate_lms_slot_quality(
                {
                    "summary": f"Buổi {lesson_no}: Làm dự án cuối khóa",
                    "studentAttendance": attended_two,
                    "studentWorks": [],
                },
                class_code="VT-JSB15",
                khoi="Coding",
                lesson=lesson_no,
            )
            spck_fails = [
                x for x in res_zero["issues"] if issue_code(x) == ISSUE_CODE_LMS_SPCK_MISSING
            ]
            self.assertEqual(len(spck_fails), 1)
            self.assertEqual(issue_text(spck_fails[0]), "[SPCK] Chưa nộp (0/2)")
            self.assertEqual(issue_severity(spck_fails[0]), SEVERITY_FAIL)
            row_check = {
                "lesson": str(lesson_no),
                "status": LESSON_14_STATUS if lesson_no == 14 else "✅ Đã nộp",
                "evidence_state": (
                    LESSON_14_EXEMPTION if lesson_no == 14 else "SUBMITTED_VERIFIED"
                ),
                "lms_status": "✅ Đã nộp LMS",
                "lms_quality_issues": res_zero["issues"],
            }
            self.assertTrue(is_failed(row_check))

        # 5. Partial submission + missing sub-components + Robotics attachment rule -> NOTE (is_failed=False)
        flawed_work = {
            "studentKey": "stu_1",
            "title": "SPCK",
            "hasStudentInfoInTitle": False,
            "thumbnail": "",
            "imageUrl": [],
            "attachmentUrls": [],
            "comment": "Làm tốt",
        }
        res_rob_partial = evaluate_lms_spck_quality(
            {
                "studentAttendance": attended_two,
                "studentWorks": [flawed_work],
            },
            class_code="VT-ROB-ARMB20",
            khoi="Robotics",
            lesson=12,
        )
        self.assertTrue(res_rob_partial["is_robotics"])
        self.assertEqual(
            [issue_text(x) for x in res_rob_partial["issues"]],
            ["[SPCK] Bổ sung nộp đủ bài, tên SP, ảnh, file code, viết kỹ NX"],
        )
        self.assertTrue(
            all(
                issue_code(x) == ISSUE_CODE_LMS_SPCK_NOTE and issue_severity(x) == SEVERITY_NOTE
                for x in res_rob_partial["issues"]
            )
        )
        self.assertFalse(
            is_failed(
                {
                    "lesson": "12",
                    "status": "✅ Đã nộp",
                    "evidence_state": "SUBMITTED_VERIFIED",
                    "lms_status": "✅ Đã nộp LMS",
                    "lms_quality_issues": res_rob_partial["issues"],
                }
            )
        )

        # 5b. Absent student's work must NOT mask missing SPCK of an attended student;
        #     and work with unknown/unmatched studentKey must trigger LMS_MANUAL_CHECK.
        res_absent_work_ignored = evaluate_lms_spck_quality(
            {
                "studentAttendance": [
                    {
                        "status": "ATTENDED",
                        "studentKey": "stu_1",
                        "comment": attended_two[0]["comment"],
                    },
                    {
                        "status": "ABSENT",
                        "studentKey": "stu_2",
                        "comment": "",
                    },
                ],
                "studentWorks": [valid_work_b12_stu2],
            },
            class_code="VT-JSB15",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(res_absent_work_ignored["submitted_count"], 0)
        self.assertEqual(
            [issue_code(x) for x in res_absent_work_ignored["issues"]],
            [ISSUE_CODE_LMS_SPCK_MISSING],
        )

        res_unknown_owner = evaluate_lms_spck_quality(
            {
                "studentAttendance": [attended_two[0]],
                "studentWorks": [{**valid_work_b12, "studentKey": ""}],
            },
            class_code="VT-JSB15",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(
            [issue_code(x) for x in res_unknown_owner["issues"]],
            [ISSUE_CODE_LMS_MANUAL_CHECK],
        )

        # 6. Browser JS findAllStudentWorks session fetch, 2-word given name detection (including accentless),
        #    PII scrubbing (including relatedUrls.name), pagination cap safety, and Lesson 14 BCNB checker note update
        exp_future = int(time.time()) + 7200
        payload_b64 = (
            base64.urlsafe_b64encode(json.dumps({"exp": exp_future}).encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
        )
        valid_jwt = f"eyJhbGciOiJIUzI1NiJ9.{payload_b64}.signature_valid_token_12345"

        runner_spck_js = r"""
        const fs = require('fs');
        const { script, args, paginateOverflow, byteStreamOverflow, unknownOwnerMode, videoUrlMode } = JSON.parse(fs.readFileSync(0, 'utf8'));
        let worksFetchCount = 0;
        let streamChunksRead = 0;
        let streamReaderCancelled = false;
        let streamJsonCalled = false;
        global.window = { location: { hostname: 'lms.mindx.edu.vn' } };
        global.fetch = async (_url, req) => {
            const body = JSON.parse(req.body);
            if (body.operationName === 'classesMissAttendOrComment') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        data: {
                            classesMissAttendOrComment: {
                                data: [{
                                    id: 'cls_jsb15_synthetic',
                                    name: 'VT-JSB15',
                                    students: [
                                        {
                                            activeInClass: true,
                                            student: { id: 'stu_raw_id_99', fullName: 'Nguyễn Văn Minh Quân' }
                                        },
                                        {
                                            activeInClass: false,
                                            student: { id: 'stu_dropped_88', fullName: 'Trần Hoàng Bách' }
                                        },
                                        {
                                            activeInClass: false,
                                            student: { id: 'stu_thai_77', fullName: 'Nguyễn Văn Thái' }
                                        }
                                    ],
                                    slots: [{
                                        _id: 'slot_14_id',
                                        index: 13,
                                        date: '2026-03-15T10:00:00.000Z',
                                        summary: 'Giải nhất: Nguyễn Văn Minh Quân',
                                        studentAttendance: [{
                                            student: { id: 'stu_raw_id_99', fullName: 'Nguyễn Văn Minh Quân' },
                                            status: 'ATTENDED',
                                            comment: 'Thái Độ Học Tập tích cực, riêng Thái nắm vững vòng lặp Python. Còn Thái làm bài rất nhanh. Với Thái, con nắm bài tốt.'
                                        }]
                                    }]
                                }]
                            }
                        }
                    })
                };
            }
            if (body.operationName === 'findAllStudentWorks') {
                worksFetchCount += 1;
                const qText = String(body.query || '');
                const varsObj = body.variables || {};
                // Enforce exact MindX LMS GraphQL schema invariants:
                // 1) Exact session/student filters use String IDs; never class-wide queries
                // 2) StudentWorkQuery does NOT accept pageIndex or itemsPerPage
                // 3) StudentWork fields title/thumbnail/comment live inside latestData / publishedData
                if (
                    !qText.includes('$sessionId: String') ||
                    !qText.includes('classSessionId_equals: $sessionId') ||
                    !qText.includes('studentId_equals: $studentId') ||
                    qText.includes('classId_in') ||
                    varsObj.sessionId !== 'slot_14_id' ||
                    qText.includes('pageIndex') ||
                    qText.includes('itemsPerPage') ||
                    varsObj.pageIndex !== undefined ||
                    varsObj.itemsPerPage !== undefined ||
                    !qText.includes('latestData') ||
                    !qText.includes('publishedData')
                ) {
                    return {
                        ok: false,
                        status: 400,
                        json: async () => ({
                            errors: [{ message: 'GRAPHQL_VALIDATION_FAILED', extensions: { code: 'GRAPHQL_VALIDATION_FAILED' } }]
                        })
                    };
                }
                if (byteStreamOverflow) {
                    const oneMbChunk = new Uint8Array(1024 * 1024).fill(65);
                    let emitted = 0;
                    return {
                        ok: true,
                        status: 200,
                        headers: { get: () => null },
                        body: {
                            getReader: () => ({
                                read: async () => {
                                    if (emitted >= 8) {
                                        return { done: true, value: undefined };
                                    }
                                    emitted += 1;
                                    streamChunksRead += 1;
                                    return { done: false, value: oneMbChunk };
                                },
                                cancel: async () => {
                                    streamReaderCancelled = true;
                                }
                            })
                        },
                        json: async () => {
                            streamJsonCalled = true;
                            return { data: { findAllStudentWorks: { data: [] } } };
                        }
                    };
                }
                if (paginateOverflow) {
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({
                            data: {
                                findAllStudentWorks: {
                                    data: Array.from({ length: 2001 }, (_, idx) => ({
                                        id: 'work_overflow_' + idx,
                                        classId: 'other_cls',
                                        classSessionId: 'other_slot',
                                        isDeleted: 'false',
                                        latestData: { title: 'Other work', thumbnail: '', comment: '' },
                                        publishedData: null
                                    }))
                                }
                            }
                        })
                    };
                }
                if (unknownOwnerMode) {
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({
                            data: {
                                findAllStudentWorks: {
                                    data: [{
                                        id: 'work_unknown',
                                        classId: 'cls_jsb15_synthetic',
                                        classSessionId: 'slot_14_id',
                                        classSessionNumber: 14,
                                        studentId: 'unknown_stu_777',
                                        isDeleted: 'false',
                                        latestData: {
                                            title: 'Le Anh Dung - Secret Project',
                                            thumbnail: '',
                                            relatedUrls: [{ name: 'Le Anh Dung Deck', url: 'https://example.org/deck' }],
                                            comment: 'Le Anh Dung lam bai'
                                        },
                                        publishedData: null
                                    }]
                                }
                            }
                        })
                    };
                }
                if (videoUrlMode) {
                    const ten25 = '25'.repeat(10);
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({
                            data: {
                                findAllStudentWorks: {
                                    data: [{
                                        id: 'work_video_url',
                                        classId: 'cls_jsb15_synthetic',
                                        classSessionId: 'slot_14_id',
                                        classSessionNumber: 14,
                                        studentId: 'stu_raw_id_99',
                                        isDeleted: 'false',
                                        latestData: {
                                            title: 'SPCK %4Dinh%20Qu%C3%A2n / %254Dinh%2520Qu%25C3%25A2n (//minh-quân.github.io/app & minh-quân.github.io/app)',
                                            thumbnail: 'https://cdn.example/spck%2F%25254Dinh-Qu%2525C3%2525A2n.png',
                                            imageUrl: [
                                                'https://static.mindx.edu.vn/clean-up.png',
                                                'https://cdn.example/%254Dinh-Qu%25C3%25A2n.png',
                                                'https://cdn.example/%2525254Dinh-Qu%252525C3%252525A2n.png',
                                                'https://cdn.example/%' + ten25 + '4Dinh-Qu%' + ten25 + 'C3%' + ten25 + 'A2n.png',
                                                'https://xn--minh-qun-oza.github.io/screen.png',
                                                'https://xn--.example.com/bad-host.png'
                                            ],
                                            attachmentUrls: [
                                                'https://static.mindx.edu.vn/Slide_%254Eguy%25E1%25BB%2585n%2520V%25C4%2583n%2520%254Dinh%2520Qu%25C3%25A2n.pptx',
                                                'https://xn--bcher-kva.example.com/archive.zip'
                                            ],
                                            videoUrls: [
                                                'https://www.youtube.com/watch?v=dQw4w9WgXcQ&author=%4Dinh-Qu%C3%A2n&ref=https%3A%2F%2Fxn--minh-qun-oza.github.io'
                                            ],
                                            relatedUrls: [
                                                {
                                                    name: 'Demo IDNA Punycode',
                                                    url: 'https://xn--minh-qun-oza.github.io/demo'
                                                },
                                                {
                                                    name: 'Bare Punycode Host',
                                                    url: 'xn--minh-qun-oza.github.io/app'
                                                },
                                                {
                                                    name: 'Malformed bare xn--.example.com/demo link',
                                                    url: 'xn--.example.com/demo'
                                                },
                                                {
                                                    name: 'Malformed query xn--',
                                                    url: 'https://example.com/?ref=xn--.example.com&tok=xn--'
                                                },
                                                {
                                                    name: 'Demo //minh-quan.github.io/app & //cdn.example/%4Dinh-Qu%C3%A2n.png',
                                                    url: '//minh-quan.github.io/app'
                                                },
                                                {
                                                    name: 'Unicode & Pct %4Dinh%20Qu%C3%A2n | %254Dinh%2520Qu%25C3%25A2n | %4Dinh%2BQu%C3%A2n | spck%2F%4Dinh%20Qu%C3%A2n.png | //minh-quân.github.io/app | minh-quân.github.io/app',
                                                    url: '//minh-quân.github.io/app'
                                                },
                                                {
                                                    name: 'Bare Unicode Host URL',
                                                    url: 'minh-quân.github.io/app'
                                                }
                                            ],
                                            comment: '1. Ý tưởng: Bố cục tốt của %4Dinh%20Qu%C3%A2n (%254Dinh%2520Qu%25C3%25A2n, file spck%2F%4Dinh%20Qu%C3%A2n.png) tại https://xn--minh-qun-oza.github.io/demo, //cdn.example/%4Dinh-Qu%C3%A2n.png và xn--.example.com/demo.\n2. Chức năng: Chạy mượt mà tại //minh-quan.github.io/app, //minh-quân.github.io/app, minh-quân.github.io/app và https://example.com/?ref=xn--.example.com (//nhận xét rõ ràng), hoàn thiện đầy đủ tính năng.'
                                        },
                                        publishedData: null
                                    }, {
                                        id: 'work_v2_rel_pct_slash',
                                        status: 'pending',
                                        classId: 'cls_jsb15_synthetic',
                                        classSessionId: 'slot_14_id',
                                        classSessionNumber: 14,
                                        studentId: 'stu_raw_id_99',
                                        isDeleted: 'false',
                                        latestData: {
                                            title: 'spck%2F%4Dinh%20Qu%C3%A2n.png',
                                            thumbnail: 'spck%2F%4Dinh%20Qu%C3%A2n.png',
                                            imageUrl: ['spck%2F%4Dinh%20Qu%C3%A2n.png'],
                                            attachmentUrls: ['spck%2F%4Dinh%20Qu%C3%A2n.pptx'],
                                            videoUrls: ['spck%2F%4Dinh%20Qu%C3%A2n.mp4'],
                                            relatedUrls: [{ name: 'spck%2F%4Dinh%20Qu%C3%A2n.png', url: 'spck%2F%4Dinh%20Qu%C3%A2n.png' }],
                                            comment: ''
                                        },
                                        publishedData: null
                                    }]
                                }
                            }
                        })
                    };
                }
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        data: {
                            findAllStudentWorks: {
                                data: [
                                    {
                                        id: 'work_1',
                                        status: 'pending',
                                        classId: 'cls_jsb15_synthetic',
                                        classSessionId: 'slot_14_id',
                                        classSessionNumber: 14,
                                        studentId: 'stu_raw_id_99',
                                        isDeleted: 'false',
                                        latestData: {
                                            title: 'Minh  Quan - Giới thiệu Tết cổ truyền',
                                            thumbnail: 'https://static.mindx.edu.vn/thumb_Nguyễn-Văn-Minh-Quân.png',
                                            imageUrl: [
                                                'https://static.mindx.edu.vn/img_minh-quan_1.png',
                                                'https://static.mindx.edu.vn/img_Nguy%E1%BB%85n-V%C4%83n-Minh-Qu%C3%A2n_2.png',
                                                'https://static.mindx.edu.vn/img_Nguye%CC%82%CC%83n_Va%CC%86n_Minh_Qua%CC%82n_nfd.png'
                                            ],
                                            attachmentUrls: [
                                                'https://resources.mindx.edu.vn/uploads/documents/spck_STU_VT-JSB15_NGUYEN_VAN_MINH_QUAN_123.zip',
                                                'https://firebasestorage.googleapis.com/v0/b/app/o/spck%2FB%C3%A0i_t%E1%BA%ADp%2FMinh_Qu%C3%A2n.zip?alt=media'
                                            ],
                                            videoUrls: [],
                                            relatedUrls: [
                                                {
                                                    name: 'GitHub của Nguyễn  Văn   Minh  Quân - Minh  Quan',
                                                    url: 'https://github.com/example/VT-JSB15/nguyen-van-minh-quan/?author=Nguy%E1%BB%85n+V%C4%83n+Minh+Qu%C3%A2n'
                                                },
                                                {
                                                    name: 'Web chạy thử 🚀',
                                                    url: 'https://minh-quan.github.io/🚀-tet-project'
                                                }
                                            ],
                                            comment: ''
                                        },
                                        publishedData: {
                                            title: '',
                                            thumbnail: '',
                                            imageUrl: [],
                                            attachmentUrls: [],
                                            videoUrls: [],
                                            relatedUrls: [],
                                            comment: '1. Ý tưởng: Sản phẩm của Nguyễn  Văn  Minh  Quân có bố cục trang web rõ ràng.\n2. Chức năng: Minh  Quan đã hoàn thiện tốt hiệu ứng hình ảnh và tương tác.'
                                        }
                                    },
                                    {
                                        id: 'work_dropped_inactive',
                                        status: 'pending',
                                        classId: 'cls_jsb15_synthetic',
                                        classSessionId: 'slot_14_id',
                                        classSessionNumber: 14,
                                        studentId: 'stu_dropped_88',
                                        isDeleted: 'false',
                                        latestData: {
                                            title: 'Trần Hoàng Bách - Old Draft',
                                            thumbnail: '',
                                            imageUrl: [],
                                            attachmentUrls: [],
                                            videoUrls: [],
                                            relatedUrls: [],
                                            comment: ''
                                        },
                                        publishedData: null
                                    }
                                ]
                            }
                        }
                    })
                };
            }
            return { ok: false, status: 500, json: async () => ({}) };
        };
        (async () => {
            const res = await eval('(' + script + ')')(args);
            if (byteStreamOverflow) {
                res.__stream_stats__ = {
                    worksFetchCount,
                    streamChunksRead,
                    streamReaderCancelled,
                    streamJsonCalled
                };
            }
            process.stdout.write(JSON.stringify(res));
        })();
        """

        class _SpckNodePage:
            url = "https://lms.mindx.edu.vn/admin/class-sessions"

            def __init__(
                self,
                *,
                paginate_overflow: bool = False,
                byte_stream_overflow: bool = False,
                unknown_owner_mode: bool = False,
                video_url_mode: bool = False,
            ) -> None:
                self.paginate_overflow = paginate_overflow
                self.byte_stream_overflow = byte_stream_overflow
                self.unknown_owner_mode = unknown_owner_mode
                self.video_url_mode = video_url_mode
                self.last_raw_eval: dict = {}

            async def evaluate(self, script: str, args: dict) -> dict:
                args_with_tok = {**args, "preResolvedToken": valid_jwt}
                proc = subprocess.run(
                    ["node", "-e", runner_spck_js],
                    input=json.dumps(
                        {
                            "script": script,
                            "args": args_with_tok,
                            "paginateOverflow": self.paginate_overflow,
                            "byteStreamOverflow": self.byte_stream_overflow,
                            "unknownOwnerMode": self.unknown_owner_mode,
                            "videoUrlMode": self.video_url_mode,
                        }
                    ),
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=True,
                )
                parsed = json.loads(proc.stdout)
                self.last_raw_eval = dict(parsed)
                parsed.pop("__stream_stats__", None)
                return parsed

        byte_overflow_page = _SpckNodePage(byte_stream_overflow=True)
        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}):
            fetched_jsb15 = await fetch_lms_class_slots_via_page(
                _SpckNodePage(), class_names=["VT-JSB15"]
            )
            fetched_overflow = await fetch_lms_class_slots_via_page(
                _SpckNodePage(paginate_overflow=True), class_names=["VT-JSB15"]
            )
            fetched_byte_overflow = await fetch_lms_class_slots_via_page(
                byte_overflow_page, class_names=["VT-JSB15"]
            )
            fetched_unknown_owner = await fetch_lms_class_slots_via_page(
                _SpckNodePage(unknown_owner_mode=True), class_names=["VT-JSB15"]
            )
            fetched_video_url = await fetch_lms_class_slots_via_page(
                _SpckNodePage(video_url_mode=True), class_names=["VT-JSB15"]
            )
        serialized_fetched = json.dumps(fetched_jsb15, ensure_ascii=False)
        for forbidden_token in (
            "Nguyễn",
            "Nguyen",
            "nguyen",
            "Nguy%E1%BB%85n",
            "nguy%e1%bb%85n",
            "Nguye%CC%82%CC%83n",
            "Minh Quân",
            "Minh Quan",
            "Minh  Quan",
            "Minh-Quân",
            "Minh_Qu%C3%A2n",
            "Minh+Qu%C3%A2n",
            "NGUYEN_VAN_MINH_QUAN",
            "nguyen-van-minh-quan",
            "minh-quan",
            "Trần Hoàng Bách",
            "Tran Hoang Bach",
            "stu_raw_id_99",
            "stu_dropped_88",
        ):
            self.assertNotIn(forbidden_token, serialized_fetched)
        slot_14 = fetched_jsb15["VT-JSB15"][0]
        self.assertEqual(len(slot_14["studentWorks"]), 2)
        self.assertTrue(slot_14["studentWorks"][0]["hasStudentInfoInTitle"])
        self.assertIn("[HS]", slot_14["studentWorks"][0]["title"])
        self.assertIn("[HS]", slot_14["studentWorks"][0]["relatedUrls"][0]["name"])
        self.assertEqual(
            slot_14["studentWorks"][0]["thumbnail"],
            "https://static.mindx.edu.vn/thumb_HS.png",
        )
        self.assertEqual(
            slot_14["studentWorks"][0]["attachmentUrls"][1],
            "https://firebasestorage.googleapis.com/v0/b/app/o/spck%2FB%C3%A0i_t%E1%BA%ADp%2FHS.zip?alt=media",
        )
        self.assertEqual(
            slot_14["studentWorks"][0]["relatedUrls"][0]["url"],
            "https://github.com/example/VT-JSB15/HS/?author=HS",
        )
        self.assertEqual(
            slot_14["studentWorks"][0]["relatedUrls"][1]["url"],
            "https://hs.github.io/%F0%9F%9A%80-tet-project",
        )
        self.assertTrue(slot_14["studentWorks"][1].get("inactiveInClass", False))

        # Verify videoUrls, multi-layer ASCII percent-encoding (%4D/%254D/%25254D/11-layer) & Punycode (xn--) scrubbing
        serialized_video_url = json.dumps(fetched_video_url, ensure_ascii=False)
        for forbidden_video_tok in (
            "Nguy",
            "%4Eguy",
            "%254Eguy",
            "Minh",
            "minh",
            "%4Dinh",
            "%254Dinh",
            "%25254Dinh",
            "4Dinh",
            "Qu%C3%A2n",
            "Qu%25C3%25A2n",
            "Qu%2525C3%2525A2n",
            "xn--minh-qun-oza",
            "xn--.example.com",
            "tok=xn--",
        ):
            self.assertNotIn(forbidden_video_tok, serialized_video_url)
        video_work = fetched_video_url["VT-JSB15"][0]["studentWorks"][0]
        self.assertTrue(video_work["hasStudentInfoInTitle"])
        self.assertEqual(
            video_work["title"],
            "SPCK [HS] / [HS] (//hs.github.io/app & hs.github.io/app)",
        )
        self.assertEqual(
            video_work["thumbnail"],
            "https://cdn.example/spck%2FHS.png",
        )
        self.assertEqual(
            video_work["imageUrl"],
            [
                "https://static.mindx.edu.vn/clean-up.png",
                "https://cdn.example/HS.png",
                "https://cdn.example/HS.png",
                "https://cdn.example/HS",
                "https://hs.github.io/screen.png",
                "https://hs.example.com/bad-host.png",
            ],
        )
        self.assertEqual(
            video_work["attachmentUrls"][1],
            "https://xn--bcher-kva.example.com/archive.zip",
        )
        self.assertEqual(
            video_work["relatedUrls"][0]["url"],
            "https://hs.github.io/demo",
        )
        self.assertEqual(
            video_work["relatedUrls"][1]["url"],
            "hs.github.io/app",
        )
        self.assertEqual(
            video_work["relatedUrls"][2]["name"],
            "Malformed bare hs.example.com/demo link",
        )
        self.assertEqual(
            video_work["relatedUrls"][2]["url"],
            "hs.example.com/demo",
        )
        self.assertEqual(
            video_work["relatedUrls"][3]["url"],
            "https://example.com/?ref=hs.example.com&tok=hs",
        )
        self.assertEqual(
            video_work["relatedUrls"][4]["name"],
            "Demo //hs.github.io/app & //cdn.example/HS.png",
        )
        self.assertEqual(
            video_work["relatedUrls"][4]["url"],
            "//hs.github.io/app",
        )
        self.assertEqual(
            video_work["relatedUrls"][5]["name"],
            "Unicode & Pct [HS] | [HS] | [HS] | spck%2F[HS].png | //hs.github.io/app | hs.github.io/app",
        )
        self.assertEqual(
            video_work["relatedUrls"][5]["url"],
            "//hs.github.io/app",
        )
        self.assertEqual(
            video_work["relatedUrls"][6]["url"],
            "hs.github.io/app",
        )
        self.assertIn(
            "Bố cục tốt của [HS] ([HS], file spck%2F[HS].png) tại https://hs.github.io/demo, //cdn.example/HS.png và hs.example.com/demo.",
            video_work["comment"],
        )
        self.assertIn(
            "//hs.github.io/app, //hs.github.io/app, hs.github.io/app và https://example.com/?ref=hs.example.com (//nhận xét rõ ràng),",
            video_work["comment"],
        )
        rel_work = fetched_video_url["VT-JSB15"][0]["studentWorks"][1]
        self.assertEqual(rel_work["title"], "spck%2F[HS].png")
        self.assertEqual(rel_work["thumbnail"], "spck%2FHS.png")
        self.assertEqual(rel_work["imageUrl"], ["spck%2FHS.png"])
        self.assertEqual(rel_work["attachmentUrls"], ["spck%2FHS.pptx"])
        self.assertEqual(rel_work["videoUrls"], ["spck%2FHS.mp4"])
        self.assertEqual(
            rel_work["relatedUrls"],
            [{"name": "spck%2F[HS].png", "url": "spck%2FHS.png"}],
        )
        self.assertEqual(
            fetched_video_url["VT-JSB15"][0]["studentAttendance"][0]["comment"],
            "Thái Độ Học Tập tích cực, riêng [HS] nắm vững vòng lặp Python. Còn [HS] làm bài rất nhanh. Với [HS], con nắm bài tốt.",
        )
        eval_video_slide_ok = evaluate_lms_spck_quality(
            fetched_video_url["VT-JSB15"][0],
            class_code="VT-JSB15",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(eval_video_slide_ok["issues"], [])

        # Verify streaming byte limit cancels reader mid-stream before JSON.parse without retrying
        stream_stats = byte_overflow_page.last_raw_eval.get("__stream_stats__", {})
        self.assertEqual(stream_stats.get("worksFetchCount"), 1)
        self.assertEqual(stream_stats.get("streamChunksRead"), 6)
        self.assertTrue(stream_stats.get("streamReaderCancelled"))
        self.assertFalse(stream_stats.get("streamJsonCalled"))
        self.assertTrue(fetched_byte_overflow["VT-JSB15"][0].get("studentWorksFetchError", False))
        eval_byte_overflow = evaluate_lms_slot_quality(
            fetched_byte_overflow["VT-JSB15"][0],
            class_code="VT-JSB15",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(
            [issue_code(x) for x in eval_byte_overflow["issues"]],
            [ISSUE_CODE_LMS_MANUAL_CHECK],
        )
        serialized_unknown_owner = json.dumps(fetched_unknown_owner, ensure_ascii=False)
        self.assertNotIn("Le Anh Dung", serialized_unknown_owner)
        self.assertNotIn("unknown_stu_777", serialized_unknown_owner)
        self.assertTrue(fetched_unknown_owner["VT-JSB15"][0].get("studentWorksFetchError"))
        eval_unknown_owner = evaluate_lms_slot_quality(
            fetched_unknown_owner["VT-JSB15"][0],
            class_code="VT-JSB15",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(
            [issue_code(x) for x in eval_unknown_owner["issues"]],
            [ISSUE_CODE_LMS_MANUAL_CHECK],
        )
        self.assertTrue(fetched_overflow["VT-JSB15"][0].get("studentWorksFetchError", False))
        eval_overflow = evaluate_lms_slot_quality(
            fetched_overflow["VT-JSB15"][0],
            class_code="VT-JSB15",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(
            [issue_code(x) for x in eval_overflow["issues"]],
            [ISSUE_CODE_LMS_MANUAL_CHECK],
        )

        # Verify update_bcnb_reports writes Lesson 14 SPCK notes to Teaching's Mô tả vấn đề column
        # both when scan_results has a pre-populated row AND when scan_results=[] (notes_only=True synthesized row via lms_quality_gateway)
        l14_checkbox = AsyncMock()
        l14_checkbox.count.return_value = 1
        l14_checkbox.is_checked.return_value = False
        l14_cell = MagicMock()
        l14_cell.locator.return_value.first = l14_checkbox
        l14_bcnb_row = {
            "row": MagicMock(),
            "cells": [l14_cell] * 12,
            "week": 11,
            "year": 2026,
            "ma_lop": "VT-JSB15",
            "buoi_so": "14",
            "ngay_hoc": "15/03/2026",
            "ngay_hoc_iso": "2026-03-15",
            "zalo_deadline": "20:00 17/03/2026",
            "bcnb_session_id": "9914",
            "khoi": "Coding",
            "nx_zalo_checked": False,
            "nx_lms_checked": True,
            "qc_issue_note": "",
            "checker_note_unreadable": False,
        }
        l14_scan_results = [
            {
                "group": "",
                "person": "GV JSB15",
                "status": LESSON_14_STATUS,
                "evidence_state": LESSON_14_EXEMPTION,
                "scan_target": {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "class_date": "15/03/2026",
                    "class_date_iso": "2026-03-15",
                    "week": 11,
                    "year": 2026,
                },
                "evidence": {
                    "date_context": {
                        "target_week": 11,
                        "target_year": 2026,
                        "target_lesson": "14",
                        "target_class_date": "2026-03-15",
                    }
                },
            }
        ]
        expected_l14_note = "[SPCK] Bổ sung YouTube, Slide"
        l14_page = MagicMock()
        l14_page.evaluate = AsyncMock(
            return_value={
                "ok": True,
                "stage": "after_write",
                "http_status": 200,
                "error": "",
            }
        )
        l14_audit_repo = AsyncMock()
        l14_audit_repo.record_intent.return_value = (
            {"id": 1401, "outcome": "INTENT_RECORDED"},
            True,
        )
        l14_audit_repo.list_for_target = AsyncMock(return_value=[])
        l14_job_repo = AsyncMock()
        fake_lms_gw = SimpleNamespace(fetch_class_slots=AsyncMock(return_value=fetched_jsb15))
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(return_value=[l14_bcnb_row]),
            ),
            patch(
                "services.zalo_bcnb_service.resolve_report_columns",
                AsyncMock(return_value=(1, 2, 8)),
            ),
            patch(
                "services.zalo_bcnb_service.execute_verified_checkbox_action",
                AsyncMock(return_value="VERIFIED"),
            ),
            patch(
                "services.bcnb_actions.dismiss_current_week_notice",
                AsyncMock(),
            ),
            patch(
                "services.bcnb_actions.verify_checker_note_persisted",
                AsyncMock(return_value=(True, expected_l14_note)),
            ),
            patch("services.bcnb_actions.asyncio.sleep", AsyncMock()),
            patch("services.zalo_bcnb_service.log_message"),
        ):
            summary_l14 = await bcnb.update_bcnb_reports(
                l14_page,
                job_id=1401,
                execution_mode="production",
                scan_results=l14_scan_results,
                today=datetime(2026, 3, 16, tzinfo=BUSINESS_TIMEZONE),
                scan_plan={
                    "selected_weeks": [
                        {"week": 11, "year": 2026},
                        {"week": 10, "year": 2026},
                    ],
                    "latest_week": {"week": 11, "year": 2026},
                    "targets": [],
                    "exempt_targets": [l14_scan_results[0]["scan_target"]],
                },
                job_repo=l14_job_repo,
                audit_repo=l14_audit_repo,
                period_filter={(11, 2026)},
                lms_slots_by_class=fetched_jsb15,
            )
            synthesized_l14_results: list[dict] = []
            l14_bcnb_row_synth = {**l14_bcnb_row, "qc_issue_note": ""}
            with patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(return_value=[l14_bcnb_row_synth]),
            ):
                summary_l14_synth = await bcnb.update_bcnb_reports(
                    l14_page,
                    job_id=1402,
                    execution_mode="production",
                    scan_results=synthesized_l14_results,
                    today=datetime(2026, 3, 16, tzinfo=BUSINESS_TIMEZONE),
                    scan_plan={
                        "selected_weeks": [
                            {"week": 11, "year": 2026},
                            {"week": 10, "year": 2026},
                        ],
                        "latest_week": {"week": 11, "year": 2026},
                        "targets": [],
                        "exempt_targets": [l14_scan_results[0]["scan_target"]],
                    },
                    job_repo=l14_job_repo,
                    audit_repo=l14_audit_repo,
                    period_filter={(11, 2026)},
                    lms_quality_gateway=fake_lms_gw,
                    notes_only=True,
                )
        self.assertEqual(summary_l14["checker_note_updated_count"], 1)
        self.assertEqual(l14_scan_results[0]["qc_issue_note"], expected_l14_note)
        self.assertEqual(fake_lms_gw.fetch_class_slots.await_count, 1)
        self.assertEqual(summary_l14_synth["checker_note_updated_count"], 1)
        self.assertEqual(len(synthesized_l14_results), 1)
        self.assertEqual(synthesized_l14_results[0]["qc_issue_note"], expected_l14_note)

    def test_phase65_dot_bypass_and_spck_missing_comment_fail(self) -> None:
        from repositories.report_repository import _resolve_row_evaluation_status
        from services.lms_quality_service import evaluate_lms_spck_quality
        from services.report_email_service import build_failed_classes_email_package
        from services.report_status import (
            ISSUE_CODE_LMS_QUALITY_FAIL,
            ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
            SEVERITY_FAIL,
            has_spck_comment_missing_fail,
            is_failed,
        )

        # 1. LMS Session Comments: empty, ".", "...", "-", "ok", "[HS] ." -> FAIL (missing_count)
        for bad_comment in ("", "   ", ".", "...", "-", "ok", "[HS] ."):
            res = evaluate_lms_student_comments(
                [
                    {"status": "ATTENDED", "comment": bad_comment},
                    {
                        "status": "ATTENDED",
                        "comment": (
                            "Con nắm vững kiến thức bài học hôm nay, tự giác hoàn thành đầy đủ "
                            "các thử thách lập trình trên lớp và biết vận dụng vòng lặp rất tốt."
                        ),
                    },
                ],
                is_checkpoint=False,
                lesson=5,
            )
            self.assertEqual(
                res["missing_count"], 1, msg=f"Should count as missing: {bad_comment!r}"
            )
            self.assertEqual(res["status"], "MISSING")
            self.assertEqual(issue_code(res["issues"][0]), ISSUE_CODE_LMS_QUALITY_FAIL)
            self.assertEqual(issue_severity(res["issues"][0]), SEVERITY_FAIL)
            self.assertEqual(issue_text(res["issues"][0]), "[LMS] Chưa NX (1/2)")

        # 2. SPCK Comments (Option A): empty or "." -> LMS_SPCK_COMMENT_MISSING (FAIL, is_failed=True)
        attended_two = [
            {
                "status": "ATTENDED",
                "studentKey": "stu_1",
                "comment": "Con hoàn thành tốt sản phẩm cuối khóa và nắm vững các khối lệnh lập trình trong dự án.",
            },
            {
                "status": "ATTENDED",
                "studentKey": "stu_2",
                "comment": "Con hoàn thành tốt sản phẩm cuối khóa và nắm vững các khối lệnh lập trình trong dự án.",
            },
        ]
        works_dot = [
            {
                "studentKey": "stu_1",
                "title": "GA38 - Nguyễn Văn A - Dự án Game",
                "hasStudentInfoInTitle": True,
                "thumbnail": "https://img/thumb1.png",
                "imageUrl": ["https://img/shot1.png"],
                "attachmentUrls": ["https://files/game.sb3"],
                "comment": ".",
            },
            {
                "studentKey": "stu_2",
                "title": "GA38 - Trần Thị B - Dự án Game",
                "hasStudentInfoInTitle": True,
                "thumbnail": "https://img/thumb2.png",
                "imageUrl": ["https://img/shot2.png"],
                "attachmentUrls": ["https://files/game2.sb3"],
                "comment": "Sản phẩm hoàn thiện tốt, giao diện đẹp mắt, nhân vật di chuyển mượt mà và xử lý va chạm chính xác.",
            },
        ]
        spck_res = evaluate_lms_spck_quality(
            {"studentAttendance": attended_two, "studentWorks": works_dot},
            class_code="VT-C4K-GA38",
            khoi="Coding",
            lesson=13,
        )
        self.assertEqual(spck_res["missing_comment_count"], 1)
        self.assertEqual(spck_res["shallow_comment_count"], 0)
        self.assertEqual(len(spck_res["issues"]), 1)
        self.assertEqual(issue_code(spck_res["issues"][0]), ISSUE_CODE_LMS_SPCK_COMMENT_MISSING)
        self.assertEqual(issue_severity(spck_res["issues"][0]), SEVERITY_FAIL)
        self.assertEqual(issue_text(spck_res["issues"][0]), "[SPCK] Chưa NX (1/2)")

        row_spck_comment_fail = {
            "group": "VT-C4K-GA38",
            "class_code": "VT-C4K-GA38",
            "lesson": "13",
            "person": "GV GA38",
            "status": "✅ Đã báo cáo",
            "evidence_state": "SUBMITTED_VERIFIED",
            "lms_status": "✅ Đã nộp LMS",
            "lms_quality_issues": spck_res["issues"],
            "quality_issues": spck_res["issues"],
        }
        self.assertTrue(has_spck_comment_missing_fail(row_spck_comment_fail))
        self.assertTrue(is_failed(row_spck_comment_fail))
        self.assertEqual(
            _resolve_row_evaluation_status(
                zalo_status="✅ Đã báo cáo",
                lms_status="✅ Đã nộp LMS",
                nx_zalo_checked=True,
                nx_lms_checked=True,
                raw_evaluation_status="",
                row=row_spck_comment_fail,
            ),
            "⚠️ SPCK chưa có nhận xét trên LMS",
        )
        email_pkg = build_failed_classes_email_package(
            week=46,
            year=2026,
            failed_rows=[row_spck_comment_fail],
            total_rows_count=1,
            selected_job_id=None,
            recipients=["owner@example.com"],
        )
        self.assertIn("color:#b45309;font-weight:600;", email_pkg["html_table"])
        self.assertNotIn("color:#b91c1c;font-weight:600;\">[SPCK]", email_pkg["html_table"])
        self.assertEqual(email_pkg["nx_failed_count"], 0)
        self.assertEqual(email_pkg["spck_only_count"], 1)
        self.assertIn("[SPCK] Chưa NX (1/2)", email_pkg["html_table"])
        self.assertIn("Lưu ý lớp chưa hoàn tất SPCK trên LMS", email_pkg["html_table"])
        self.assertNotIn("Tình trạng đánh giá", email_pkg["html_table"])

    def test_phase65_attendance_conflict_spck_wording_and_robotics_rules(self) -> None:
        from services.lms_quality_service import evaluate_lms_spck_quality
        from services.report_status import (
            ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
            ISSUE_CODE_LMS_SPCK_NOTE,
            is_failed,
        )

        # 1. CSA04-HB / JSA15: LATE_ARRIVED or ATTENDED + "Bạn nghỉ" -> LMS_ATTENDANCE_CONFLICT_NOTE (NOTE, is_failed=False)
        conflict_res = evaluate_lms_student_comments(
            [
                {"status": "LATE_ARRIVED", "comment": "Bạn nghỉ"},
                {"status": "ATTENDED", "comment": "HV xin nghỉ phép buổi hôm nay"},
                {
                    "status": "ATTENDED",
                    "comment": (
                        "Con nắm vững kiến thức bài học hôm nay, tự giác hoàn thành đầy đủ "
                        "các thử thách lập trình trên lớp và biết vận dụng vòng lặp rất tốt."
                    ),
                },
            ],
            is_checkpoint=False,
            lesson=4,
        )
        self.assertEqual(conflict_res["attendance_conflict_count"], 2)
        self.assertEqual(conflict_res["shallow_count"], 0)
        self.assertEqual(conflict_res["status"], "REVIEW")
        self.assertEqual(len(conflict_res["issues"]), 1)
        self.assertEqual(
            issue_code(conflict_res["issues"][0]),
            ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
        )
        self.assertEqual(issue_severity(conflict_res["issues"][0]), SEVERITY_NOTE)
        self.assertEqual(
            issue_text(conflict_res["issues"][0]),
            "[LMS] Kiểm tra điểm danh-NX",
        )
        self.assertFalse(
            is_failed(
                {
                    "status": "✅ Đã báo cáo",
                    "evidence_state": "SUBMITTED_VERIFIED",
                    "lms_status": "✅ Đã nộp LMS",
                    "lms_quality_issues": conflict_res["issues"],
                }
            )
        )

        # 2. SPCK Title (Chặng 2: real product title without student name is valid!) & Mức A grouped SPCK note
        attended_three = [
            {
                "status": "ATTENDED",
                "studentKey": f"stu_{i}",
                "comment": "Con hoàn thành tốt sản phẩm cuối khóa và nắm vững các khối lệnh lập trình trong dự án.",
            }
            for i in range(1, 4)
        ]
        good_comment = "Sản phẩm hoàn thiện tốt, giao diện đẹp mắt, nhân vật di chuyển mượt mà và xử lý va chạm chính xác."
        works_granularity = [
            {
                "studentKey": "stu_1",
                "title": ".",
                "hasStudentInfoInTitle": False,
                "thumbnail": "",
                "imageUrl": [],
                "attachmentUrls": ["https://files/a.sb3"],
                "comment": good_comment,
            },
            {
                "studentKey": "stu_2",
                "title": "Game Bắn Thiên Thạch",
                "hasStudentInfoInTitle": False,
                "thumbnail": "https://img/thumb.png",
                "imageUrl": [],
                "attachmentUrls": ["https://files/b.sb3"],
                "comment": good_comment,
            },
            {
                "studentKey": "stu_3",
                "title": "CSI02 - HV 3 - Web Bán Hàng",
                "hasStudentInfoInTitle": True,
                "thumbnail": "",
                "imageUrl": ["https://img/shot.png"],
                "attachmentUrls": ["https://files/c.sb3"],
                "comment": good_comment,
            },
        ]
        gran_res = evaluate_lms_spck_quality(
            {"studentAttendance": attended_three, "studentWorks": works_granularity},
            class_code="VT-C4K-CSI02",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(gran_res["no_title_count"], 1)
        self.assertEqual(gran_res["missing_student_name_in_title_count"], 0)
        self.assertEqual(gran_res["missing_student_info_count"], 1)
        self.assertEqual(
            [issue_text(x) for x in gran_res["issues"]],
            ["[SPCK] Bổ sung tên SP, ảnh"],
        )
        for item in gran_res["issues"]:
            self.assertEqual(issue_code(item), ISSUE_CODE_LMS_SPCK_NOTE)
            self.assertEqual(issue_severity(item), SEVERITY_NOTE)

        # 3. Robotics B14: checks YouTube + images, skips Slide check, and notes missing code file with Robotics wording
        rob_b14_res = evaluate_lms_spck_quality(
            {
                "studentAttendance": [attended_three[0]],
                "studentWorks": [
                    {
                        "studentKey": "stu_1",
                        "title": "ROB-ARMI12 - HV 1 - Robot Cứu Hộ",
                        "hasStudentInfoInTitle": True,
                        "thumbnail": "https://img/thumb.png",
                        "imageUrl": ["https://img/shot.png"],
                        "attachmentUrls": [],
                        "videoUrls": [],
                        "relatedUrls": [],
                        "comment": good_comment,
                    }
                ],
            },
            class_code="VT-ROB-ARMI12",
            khoi="Robotics",
            lesson=14,
        )
        self.assertEqual(
            [issue_text(x) for x in rob_b14_res["issues"]],
            ["[SPCK] Bổ sung file code, YouTube"],
        )

    def test_phase65_two_tier_ai_funnel_pii_scrubbing_and_future_date_guard(self) -> None:
        from datetime import datetime
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from core.time_utils import BUSINESS_TIMEZONE
        from services.lms_quality_service import (
            _scrub_comment_for_ai,
            evaluate_lms_spck_quality,
        )
        from services.report_status import (
            ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
            ISSUE_CODE_LMS_COMMENT_NOTE,
        )

        # 1. Python PII scrubber removes names, emails, phones, and URLs before AI prompt
        scrubbed = _scrub_comment_for_ai(
            "Bạn Nguyễn Hoàng Minh (minh@example.com, 0912345678, https://lms.mindx.edu.vn/u/minh) hiểu bài tốt."
        )
        self.assertNotIn("Nguyễn Hoàng Minh", scrubbed)
        self.assertNotIn("minh@example.com", scrubbed)
        self.assertNotIn("0912345678", scrubbed)
        self.assertNotIn("lms.mindx.edu.vn", scrubbed)
        self.assertIn("[HS]", scrubbed)

        # 2. Tier 1 (>= 25 words & >= 80 chars) passes immediately without calling AI
        mock_ai = SimpleNamespace(review_comments=MagicMock(return_value={}))
        long_comment = (
            "Hôm nay con nắm rất vững kiến thức về cảm biến siêu âm, tự tay lắp ráp hoàn chỉnh "
            "mô hình xe robot tránh vật cản và chủ động hỗ trợ các bạn trong nhóm kiểm tra lỗi mạch điện."
        )
        tier1_res = evaluate_lms_student_comments(
            [{"status": "ATTENDED", "comment": long_comment}],
            is_checkpoint=False,
            lesson=3,
            ai_reviewer=mock_ai,
        )
        self.assertEqual(tier1_res["status"], "COMPLETE")
        self.assertEqual(tier1_res["issues"], [])
        self.assertEqual(mock_ai.review_comments.call_count, 0)

        # 3. Tier 2 Gray Zone (1..24 words) routes to AI reviewer & respects safety invariant (PLACEHOLDER_BYPASS -> NOTE, never FAIL)
        gray_ai = SimpleNamespace(
            review_comments=MagicMock(
                return_value={
                    "stu_c_0": "VALID_CONCISE",
                    "stu_c_1": "SHALLOW_GENERIC",
                    "stu_c_2": "ATTENDANCE_CONFLICT",
                    "stu_c_3": "PLACEHOLDER_BYPASS",
                }
            )
        )
        gray_res = evaluate_lms_student_comments(
            [
                {
                    "status": "ATTENDED",
                    "comment": "Con hiểu bài vòng lặp for và tự code xong bài thực hành.",
                },
                {"status": "ATTENDED", "comment": "Con học ngoan, chú ý nghe giảng."},
                {
                    "status": "ATTENDED",
                    "comment": "Hôm nay gia đình báo con bận việc riêng không tới lớp.",
                },
                {"status": "ATTENDED", "comment": "Nhận xét ngắn gọn thử nghiệm."},
            ],
            is_checkpoint=False,
            lesson=3,
            ai_reviewer=gray_ai,
            lesson_summary="Buổi 3: Vòng lặp for trong Python",
        )
        self.assertEqual(gray_ai.review_comments.call_count, 1)
        self.assertEqual(gray_res["missing_count"], 0)
        self.assertEqual(gray_res["shallow_count"], 2)
        self.assertEqual(gray_res["attendance_conflict_count"], 1)
        self.assertEqual(
            [issue_code(x) for x in gray_res["issues"]],
            [ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE, ISSUE_CODE_LMS_COMMENT_NOTE],
        )
        self.assertTrue(all(issue_severity(x) == SEVERITY_NOTE for x in gray_res["issues"]))

        # 4. SPCK gray-zone comment with AI reviewer
        spck_ai = SimpleNamespace(
            review_comments=MagicMock(return_value={"spck_c_0": "VALID_CONCISE"})
        )
        spck_concise = evaluate_lms_spck_quality(
            {
                "studentAttendance": [{"status": "ATTENDED", "studentKey": "stu_1"}],
                "studentWorks": [
                    {
                        "studentKey": "stu_1",
                        "title": "GB42-HB - HV 1 - Dự án",
                        "hasStudentInfoInTitle": True,
                        "thumbnail": "https://img/t.png",
                        "imageUrl": ["https://img/s.png"],
                        "attachmentUrls": ["https://files/p.sb3"],
                        "comment": "Code chạy mượt, nhân vật di chuyển đúng logic.",
                    }
                ],
            },
            class_code="VT-C4K-GB42-HB",
            khoi="Coding",
            lesson=13,
            ai_reviewer=spck_ai,
        )
        self.assertEqual(spck_concise["issues"], [])

        # 5. Future date defense-in-depth guard in inspect_and_attach_quality_issues
        future_scan = [
            {
                "class_code": "VT-C4K-GB41",
                "lesson": "14",
                "class_date_iso": "2026-11-15",
                "scan_target": {
                    "class_code": "VT-C4K-GB41",
                    "lesson": "14",
                    "class_date_iso": "2026-11-15",
                },
            }
        ]
        inspect_and_attach_quality_issues(
            future_scan,
            lms_slots_by_class={"VT-C4K-GB41": [{"index": 13, "date": "2026-11-15T07:00:00.000Z"}]},
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(future_scan[0]["lms_quality_issues"], [])
        self.assertEqual(future_scan[0]["quality_issues"], [])

        # 6. Scrubber preserves lowercase verbs after Con/Bạn/Em & scrubs unprefixed 3-word names, www URLs, intl phones
        preserved_1 = _scrub_comment_for_ai("Con lập trình tốt.")
        preserved_2 = _scrub_comment_for_ai("Con lắp ráp robot tốt.")
        self.assertIn("lập trình tốt", preserved_1)
        self.assertIn("lắp ráp robot tốt", preserved_2)

        scrubbed_edge = _scrub_comment_for_ai(
            "Cao Gia Bảo nộp bài tại www.example.invalid/student/42 SĐT +1 (202) 555-0123 hoặc (+84) 912 345 678.",
            known_student_names=["Trần Bảo"],
        )
        self.assertNotIn("Cao Gia Bảo", scrubbed_edge)
        self.assertNotIn("example.invalid", scrubbed_edge)
        self.assertNotIn("555-0123", scrubbed_edge)
        self.assertNotIn("912 345 678", scrubbed_edge)
        self.assertIn("[HS]", scrubbed_edge)
        self.assertIn("[URL]", scrubbed_edge)
        self.assertIn("[PHONE]", scrubbed_edge)

        # 7. Attendance conflict negation guard (including "chưa từng nghỉ" / "chưa bao giờ nghỉ") & >15-word absence note detection
        negated_And_Long_Absence = evaluate_lms_student_comments(
            [
                {
                    "status": "ATTENDED",
                    "comment": (
                        "Con không nghỉ học buổi nào, luôn đi học đầy đủ, nắm vững kiến thức "
                        "về vòng lặp và tự hoàn thành toàn bộ bài tập thực hành trên lớp rất tốt."
                    ),
                },
                {
                    "status": "ATTENDED",
                    "comment": "Con chưa từng nghỉ học, vẫn làm bài tốt và hoàn thành dự án Scratch.",
                },
                {
                    "status": "ATTENDED",
                    "comment": "Con chưa bao giờ nghỉ học, nắm bài rất tốt và chủ động lắp ráp mô hình.",
                },
                {
                    "status": "ATTENDED",
                    "comment": (
                        "Hôm nay gia đình xin phép cho bạn nghỉ học vì bị ốm sốt cao, giáo viên "
                        "đã gửi tài liệu và video bài giảng để con xem lại ở nhà trước buổi tới."
                    ),
                },
            ],
            is_checkpoint=False,
            lesson=4,
        )
        self.assertEqual(negated_And_Long_Absence["attendance_conflict_count"], 1)
        self.assertEqual(
            [issue_code(x) for x in negated_And_Long_Absence["issues"]],
            [ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE],
        )

        # 8. Same-day before-class-end & unknown-time guard in inspect_and_attach_quality_issues + _sanitize_dashboard_item
        from repositories.report_repository import _sanitize_dashboard_item

        same_day_pending = [
            {
                "class_code": "VT-C4K-GB42-HB",
                "lesson": "14",
                "class_date_iso": "2026-11-12",
                "group": "MindX - VT-C4K-GB42-HB T5 18H-20H",
                "nx_lms_checked": False,
                "scan_target": {
                    "class_code": "VT-C4K-GB42-HB",
                    "lesson": "14",
                    "class_date": "12/11/2026",
                    "class_date_iso": "2026-11-12",
                    "week": 46,
                    "year": 2026,
                    "group_name": "MindX - VT-C4K-GB42-HB T5 18H-20H",
                    "nx_lms_checked": False,
                },
            },
            {
                "class_code": "VT-C4K-GI32",
                "lesson": "14",
                "class_date_iso": "2026-11-12",
                "group": "MindX - VT-C4K-GI32",
                "nx_lms_checked": False,
                "scan_target": {
                    "class_code": "VT-C4K-GI32",
                    "lesson": "14",
                    "class_date": "12/11/2026",
                    "class_date_iso": "2026-11-12",
                    "week": 46,
                    "year": 2026,
                    "group_name": "MindX - VT-C4K-GI32",
                    "nx_lms_checked": False,
                },
            },
        ]
        inspect_and_attach_quality_issues(
            same_day_pending,
            lms_slots_by_class={},
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
            latest_week={"week": 46, "year": 2026},
        )
        self.assertEqual(same_day_pending[0]["lms_quality_issues"], [])
        self.assertFalse(same_day_pending[0]["is_failed"])
        self.assertTrue(same_day_pending[0]["session_deferred"])
        sanitized_deferred = _sanitize_dashboard_item(
            same_day_pending[0],
            week=46,
            year=2026,
            source_job_id=101,
            lms_status="Chưa quét LMS",
            job_timestamp="2026-11-12T10:00:00+07:00",
        )
        self.assertFalse(sanitized_deferred["is_failed"])
        self.assertEqual(sanitized_deferred["lms_status"], "⏳ Chưa tới giờ kiểm tra")
        self.assertEqual(sanitized_deferred["evaluation_status"], "⏳ Chưa tới giờ kiểm tra")

        # 9. Previous-week (W45) same-day target checked at 12/11 10:00 (18H-20H) WITHOUT latest_week is NOT deferred
        w45_same_day = [
            {
                "class_code": "VT-C4K-GB42-HB",
                "lesson": "13",
                "class_date": "12/11/2026",
                "class_date_iso": "2026-11-12",
                "week": 45,
                "year": 2026,
                "group": "MindX - VT-C4K-GB42-HB (18H-20H)",
                "scan_target": {
                    "class_code": "VT-C4K-GB42-HB",
                    "lesson": "13",
                    "class_date": "12/11/2026",
                    "class_date_iso": "2026-11-12",
                    "week": 45,
                    "year": 2026,
                    "group_name": "MindX - VT-C4K-GB42-HB (18H-20H)",
                },
            }
        ]
        inspect_and_attach_quality_issues(
            w45_same_day,
            lms_slots_by_class={},
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertFalse(w45_same_day[0].get("session_deferred", False))
        self.assertEqual(len(w45_same_day[0]["lms_quality_issues"]), 1)

    async def test_phase65_real_gemini_gateway_wiring_through_fetch_and_evaluate(self) -> None:
        import json
        from datetime import datetime
        from types import SimpleNamespace
        from core.time_utils import BUSINESS_TIMEZONE
        from services.external_gateways import GeminiLmsCommentReviewerGateway
        from services.lms_quality_service import fetch_and_evaluate_lms_quality
        from services.report_status import ISSUE_CODE_LMS_COMMENT_NOTE

        captured_prompts: list[str] = []

        def _fake_generate_content(*, model: str, contents: str, config: object = None) -> object:
            captured_prompts.append(str(contents))
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "results": [
                            {
                                "item_id": "stu_c_0",
                                "classification": "VALID_CONCISE",
                                "reason": "Nêu rõ kỹ năng lập trình",
                            },
                            {
                                "item_id": "stu_c_1",
                                "classification": "SHALLOW_GENERIC",
                                "reason": "Nhận xét chung chung",
                            },
                            {
                                "item_id": "stu_c_2",
                                "classification": "ATTENDANCE_CONFLICT",
                                "reason": "Báo nghỉ học nhưng điểm danh có mặt",
                            },
                        ]
                    }
                )
            )

        fake_client = SimpleNamespace(
            models=SimpleNamespace(generate_content=_fake_generate_content)
        )
        real_gateway = GeminiLmsCommentReviewerGateway(
            model="gemini-3.1-flash-lite-preview",
            timeout_ms=3000,
            client_factory=lambda: fake_client,
        )

        scan_items = [
            {
                "class_code": "VT-C4K-GB42-HB",
                "lesson": "3",
                "class_date_iso": "2026-11-10",
                "scan_target": {
                    "class_code": "VT-C4K-GB42-HB",
                    "lesson": "3",
                    "class_date": "10/11/2026",
                    "class_date_iso": "2026-11-10",
                    "week": 46,
                    "year": 2026,
                    "khoi": "Coding",
                },
            }
        ]
        slots_by_class = {
            "VT-C4K-GB42-HB": [
                {
                    "index": 2,
                    "date": "2026-11-10",
                    "summary": "Buổi 3: Cao Gia Bảo học lập trình Scratch cơ bản",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            "comment": "Cao Gia Bảo lập trình tốt tại www.example.invalid/student/42 SĐT +1 (202) 555-0123.",
                        },
                        {
                            "status": "ATTENDED",
                            "comment": "Con lắp ráp robot tốt, ngoan.",
                        },
                        {
                            "status": "ATTENDED",
                            "comment": "Bố nhắn tin báo con đang đi du lịch cùng gia đình.",
                        },
                    ],
                }
            ]
        }

        await fetch_and_evaluate_lms_quality(
            page=None,
            scan_results=scan_items,
            lms_slots_by_class=slots_by_class,
            allow_page_fallback=False,
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
            ai_reviewer=real_gateway,
            latest_week={"week": 46, "year": 2026},
        )
        self.assertEqual(len(captured_prompts), 1)
        prompt_sent = captured_prompts[0]
        self.assertNotIn("Cao Gia Bảo", prompt_sent)
        self.assertNotIn("example.invalid", prompt_sent)
        self.assertNotIn("555-0123", prompt_sent)
        self.assertIn("lập trình tốt", prompt_sent)
        self.assertIn("lắp ráp robot tốt", prompt_sent)

        issues = scan_items[0]["lms_quality_issues"]
        self.assertEqual(
            [issue_code(x) for x in issues],
            [ISSUE_CODE_LMS_COMMENT_NOTE],
        )
        self.assertEqual(
            [issue_text(x) for x in issues],
            ["[LMS] Kiểm tra điểm danh-NX, viết kỹ NX"],
        )

        # 2. Per-scan batch counter reset: max_batches_per_scan=1 allows 1 batch per scan across 2 consecutive scans
        single_batch_gw = GeminiLmsCommentReviewerGateway(
            model="gemini-3.1-flash-lite-preview",
            timeout_ms=3000,
            max_batches_per_scan=1,
            client_factory=lambda: fake_client,
        )
        before_calls = len(captured_prompts)
        await fetch_and_evaluate_lms_quality(
            page=None,
            scan_results=scan_items,
            lms_slots_by_class=slots_by_class,
            allow_page_fallback=False,
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
            ai_reviewer=single_batch_gw,
            latest_week={"week": 46, "year": 2026},
        )
        await fetch_and_evaluate_lms_quality(
            page=None,
            scan_results=scan_items,
            lms_slots_by_class=slots_by_class,
            allow_page_fallback=False,
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
            ai_reviewer=single_batch_gw,
            latest_week={"week": 46, "year": 2026},
        )
        self.assertEqual(len(captured_prompts) - before_calls, 2)

        # 3. Non-blocking async + strict 0.5s timeout enforcement against a 0.8s sleeping client
        import asyncio
        from datetime import datetime
        import time

        def _slow_generate_content(*, model: str, contents: str, config: object = None) -> object:
            time.sleep(0.8)
            return SimpleNamespace(
                text='{"items":[{"id":"stu_c_0","classification":"VALID_CONCISE"}]}'
            )

        slow_gateway = GeminiLmsCommentReviewerGateway(
            model="gemini-3.1-flash-lite-preview",
            timeout_seconds=0.5,
            client_factory=lambda: SimpleNamespace(
                models=SimpleNamespace(generate_content=_slow_generate_content)
            ),
        )
        heartbeat_ticks = 0
        stop_hb = False

        async def _heartbeat() -> None:
            nonlocal heartbeat_ticks
            while not stop_hb:
                await asyncio.sleep(0.05)
                heartbeat_ticks += 1

        hb_task = asyncio.create_task(_heartbeat())
        t0 = time.perf_counter()
        try:
            await fetch_and_evaluate_lms_quality(
                page=None,
                scan_results=scan_items,
                lms_slots_by_class=slots_by_class,
                allow_page_fallback=False,
                now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
                ai_reviewer=slow_gateway,
                latest_week={"week": 46, "year": 2026},
            )
            elapsed = time.perf_counter() - t0
        finally:
            stop_hb = True
            await hb_task
        self.assertLess(elapsed, 0.72)
        self.assertGreaterEqual(heartbeat_ticks, 4)

        # 4. Async-only protocol implementation (class with only async def review_short_comments)
        async_only_calls = 0

        class AsyncOnlyCustomReviewer:
            async def review_short_comments(
                self, comments: list[dict[str, object]], **kwargs: object
            ) -> dict[str, str]:
                nonlocal async_only_calls
                async_only_calls += 1
                await asyncio.sleep(0.01)
                return {str(c.get("id")): "VALID_CONCISE" for c in comments}

        custom_rev = AsyncOnlyCustomReviewer()
        await fetch_and_evaluate_lms_quality(
            page=None,
            scan_results=scan_items,
            lms_slots_by_class=slots_by_class,
            allow_page_fallback=False,
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
            ai_reviewer=custom_rev,
            latest_week={"week": 46, "year": 2026},
        )
        self.assertEqual(async_only_calls, 1)
        self.assertEqual(scan_items[0]["lms_quality_issues"], [])

        # Re-inspecting already-evaluated scan_items with default reset_ai_batch_counter=False makes 0 extra AI requests
        inspect_and_attach_quality_issues(
            scan_items,
            lms_slots_by_class=slots_by_class,
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
            ai_reviewer=custom_rev,
            latest_week={"week": 46, "year": 2026},
        )
        self.assertEqual(async_only_calls, 1)
        self.assertEqual(scan_items[0]["lms_quality_issues"], [])

        # Passing reset_ai_batch_counter=True starts a fresh scan and invokes the reviewer inside a running event loop
        inspect_and_attach_quality_issues(
            scan_items,
            lms_slots_by_class=slots_by_class,
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
            ai_reviewer=custom_rev,
            latest_week={"week": 46, "year": 2026},
            reset_ai_batch_counter=True,
        )
        self.assertEqual(async_only_calls, 2)
        self.assertEqual(scan_items[0]["lms_quality_issues"], [])

    async def test_phase65_round3_scrubber_absence_and_bcnb_batch_counter_invariants(
        self,
    ) -> None:
        import asyncio
        from datetime import datetime
        import time
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.time_utils import BUSINESS_TIMEZONE
        from services.external_gateways import GeminiLmsCommentReviewerGateway
        from services.lms_quality_service import (
            _is_absence_statement_in_comment,
            _scrub_comment_for_ai,
            evaluate_lms_student_comments,
            inspect_and_attach_quality_issues_async,
        )
        from services.report_status import (
            ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
            ISSUE_CODE_LMS_COMMENT_NOTE,
            issue_code,
        )
        from services.zalo_bcnb_service import update_bcnb_reports

        # 1. Bare hostnames scrubbed to [URL] while tech framework/file names stay intact (Finding #4)
        scrubbed_hosts = _scrub_comment_for_ai(
            "Con triển khai sản phẩm tại hv42.example.com và student.mindx.edu.vn bằng Node.js, Next.js, main.py, project.sb3."
        )
        self.assertNotIn("hv42.example.com", scrubbed_hosts)
        self.assertNotIn("student.mindx.edu.vn", scrubbed_hosts)
        self.assertEqual(scrubbed_hosts.count("[URL]"), 2)
        for preserved_tech in ("Node.js", "Next.js", "main.py", "project.sb3"):
            self.assertIn(preserved_tech, scrubbed_hosts)

        # 2. Name scrubber preserves capitalized course/tech terms while scrubbing real names including single given name ("Con Thái") (Finding #5 & Round 4 #1)
        scrubbed_course = _scrub_comment_for_ai(
            "Con hoàn thành Bài Tập Vòng Lặp Python. Con Python cơ bản nắm vững. Thái Độ Học Tập tích cực. Cao Gia Bảo, Con Gia Bảo và Con Thái nắm vững vòng lặp Python.",
            known_student_names=["Nguyễn Văn Thái"],
        )
        self.assertIn("Con hoàn thành Bài Tập Vòng Lặp Python", scrubbed_course)
        self.assertIn("Con Python cơ bản nắm vững", scrubbed_course)
        self.assertIn("Thái Độ Học Tập", scrubbed_course)
        self.assertNotIn("Cao Gia Bảo", scrubbed_course)
        self.assertNotIn("Gia Bảo", scrubbed_course)
        self.assertNotIn("Con Thái", scrubbed_course)
        self.assertIn("Con [HS] nắm vững vòng lặp Python", scrubbed_course)

        # Also without known_student_names, "Con Thái nắm vững vòng lặp Python" scrubs "Thái" -> "Con [HS]"
        scrubbed_single_given = _scrub_comment_for_ai("Con Thái nắm vững vòng lặp Python")
        self.assertEqual(scrubbed_single_given, "Con [HS] nắm vững vòng lặp Python")

        # 3. Clause-by-clause absence conflict detection with past-session participation + >65 words, both period-joined and comma-joined (Finding #6 & Round 4 #4)
        long_mixed_absence_comment = (
            "Trong giờ học con tập trung ở buổi trước và hoàn thành các thử thách lập trình rất tích cực. "
            "Hôm nay con nghỉ học do bị sốt cao, gia đình đã nhắn tin báo giáo viên trước giờ vào lớp; "
            "thầy đã gửi toàn bộ tài liệu hướng dẫn chi tiết về vòng lặp, hàm, biến, danh sách và bài tập thực hành "
            "để con tự xem lại tại nhà khi sức khỏe ổn định hơn trước khi bước sang buổi học tuần sau."
        )
        self.assertGreater(len(long_mixed_absence_comment.split()), 65)
        self.assertTrue(_is_absence_statement_in_comment(long_mixed_absence_comment))
        comma_joined_absence_comment = (
            "Trong giờ học con tập trung ở buổi trước và hoàn thành các thử thách lập trình rất tích cực, "
            "hôm nay con nghỉ học do bị sốt cao, gia đình đã nhắn tin báo giáo viên trước giờ vào lớp, "
            "thầy đã gửi toàn bộ tài liệu hướng dẫn chi tiết về vòng lặp, hàm, biến, danh sách và bài tập thực hành "
            "để con tự xem lại tại nhà khi sức khỏe ổn định hơn trước khi bước sang buổi học tuần sau."
        )
        self.assertGreater(len(comma_joined_absence_comment.split()), 65)
        self.assertTrue(_is_absence_statement_in_comment(comma_joined_absence_comment))
        eval_conflict = evaluate_lms_student_comments(
            [{"status": "ATTENDED", "comment": comma_joined_absence_comment}],
            is_checkpoint=False,
            lesson=6,
        )
        self.assertEqual(eval_conflict["attendance_conflict_count"], 1)
        self.assertEqual(
            [issue_code(x) for x in eval_conflict["issues"]],
            [ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE],
        )

        # 4. Async non-blocking inspect_and_attach_quality_issues_async during 0.30s slow AI reviewer (Finding #2a)
        def _slow_300ms_gen(*, model: str, contents: str, config: object = None) -> object:
            time.sleep(0.30)
            return SimpleNamespace(
                text='{"items":[{"id":"stu_c_0","classification":"VALID_CONCISE"}]}'
            )

        slow_300_gw = GeminiLmsCommentReviewerGateway(
            model="gemini-3.1-flash-lite-preview",
            timeout_seconds=2.0,
            client_factory=lambda: SimpleNamespace(
                models=SimpleNamespace(generate_content=_slow_300ms_gen)
            ),
        )
        hb_count = 0
        stop_flag = False

        async def _hb_runner() -> None:
            nonlocal hb_count
            while not stop_flag:
                await asyncio.sleep(0.05)
                hb_count += 1

        async_scan_results = [
            {
                "class_code": "VT-C4K-Async1",
                "lesson": "3",
                "evidence_state": "SUBMITTED_VERIFIED",
                "scan_target": {
                    "class_code": "VT-C4K-Async1",
                    "lesson": "3",
                    "week": 46,
                    "year": 2026,
                    "class_date_iso": "2026-11-10",
                },
            }
        ]
        async_slots = {
            "VT-C4K-ASYNC1": [
                {
                    "index": 2,
                    "date": "2026-11-10",
                    "summary": "Buổi 3: Lập trình vòng lặp và hàm cơ bản trong Python",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            "comment": "Con hiểu bài và thực hành viết vòng lặp tốt.",
                        }
                    ],
                }
            ]
        }
        hb_future = asyncio.create_task(_hb_runner())
        try:
            await inspect_and_attach_quality_issues_async(
                async_scan_results,
                lms_slots_by_class=async_slots,
                now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
                ai_reviewer=slow_300_gw,
                latest_week={"week": 46, "year": 2026},
                reset_ai_batch_counter=True,
            )
        finally:
            stop_flag = True
            await hb_future
        self.assertGreaterEqual(hb_count, 3)
        self.assertEqual(async_scan_results[0]["lms_quality_issues"], [])

        # 5. update_bcnb_reports with 2 classes and max_batches_per_scan=1 resets counter once,
        # sends at most 1 AI request total, and preserves Class 1's AI verdict through final row sync (Finding #2b)
        sdk_calls: list[str] = []

        def _quota1_gen(*, model: str, contents: str, config: object = None) -> object:
            sdk_calls.append(contents)
            return SimpleNamespace(
                text='{"items":[{"id":"stu_c_0","classification":"VALID_CONCISE"}]}'
            )

        quota1_gw = GeminiLmsCommentReviewerGateway(
            model="gemini-3.1-flash-lite-preview",
            timeout_seconds=2.0,
            max_batches_per_scan=1,
            client_factory=lambda: SimpleNamespace(
                models=SimpleNamespace(generate_content=_quota1_gen)
            ),
        )
        orig_reset = quota1_gw.reset_batch_counter
        reset_call_count = 0

        def _counted_reset() -> None:
            nonlocal reset_call_count
            reset_call_count += 1
            orig_reset()

        quota1_gw.reset_batch_counter = _counted_reset  # type: ignore[method-assign]

        two_class_results = [
            {
                "group": "[Vũng Tàu] C4K-CLS1 T3 (18h-20h)",
                "class_code": "VT-C4K-CLS1",
                "lesson": "3",
                "status": "✅ Đã nộp báo cáo",
                "evidence_state": "SUBMITTED_VERIFIED",
                "submitted_for_bcnb": True,
                "scan_target": {
                    "class_code": "VT-C4K-CLS1",
                    "group_name": "[Vũng Tàu] C4K-CLS1 T3 (18h-20h)",
                    "lesson": "3",
                    "week": 46,
                    "year": 2026,
                    "class_date": "10/11/2026",
                    "class_date_iso": "2026-11-10",
                },
            },
            {
                "group": "[Vũng Tàu] C4K-CLS2 T3 (18h-20h)",
                "class_code": "VT-C4K-CLS2",
                "lesson": "3",
                "status": "✅ Đã nộp báo cáo",
                "evidence_state": "SUBMITTED_VERIFIED",
                "submitted_for_bcnb": True,
                "scan_target": {
                    "class_code": "VT-C4K-CLS2",
                    "group_name": "[Vũng Tàu] C4K-CLS2 T3 (18h-20h)",
                    "lesson": "3",
                    "week": 46,
                    "year": 2026,
                    "class_date": "10/11/2026",
                    "class_date_iso": "2026-11-10",
                },
            },
        ]
        two_class_slots = {
            "VT-C4K-CLS1": [
                {
                    "index": 2,
                    "date": "2026-11-10",
                    "summary": "Buổi 3: Lập trình sự kiện và biến trong Scratch cơ bản",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            # < 12 words so deterministic fallback would mark shallow if AI verdict were lost!
                            "comment": "Con hiểu bài và ghép khối lệnh tốt.",
                        }
                    ],
                }
            ],
            "VT-C4K-CLS2": [
                {
                    "index": 2,
                    "date": "2026-11-10",
                    "summary": "Buổi 3: Lập trình sự kiện và biến trong Scratch cơ bản",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            "comment": "Con tập trung nghe giảng và làm bài.",
                        }
                    ],
                }
            ],
        }
        fake_plan = {
            "selected_weeks": [
                {"week": 45, "year": 2026},
                {"week": 46, "year": 2026},
            ],
            "latest_week": {"week": 46, "year": 2026},
            "targets": [
                dict(two_class_results[0]["scan_target"]),
                dict(two_class_results[1]["scan_target"]),
            ],
        }
        fake_job_repo = SimpleNamespace(ensure_not_cancelled=AsyncMock())
        fake_audit_repo = SimpleNamespace(
            record_intent=AsyncMock(return_value=({"id": 1}, True)),
            mark_verified=AsyncMock(),
            get_by_action_key=AsyncMock(return_value=None),
        )
        fake_bcnb_rows_w46 = [
            {
                "row_index": 0,
                "cells": [MagicMock(), MagicMock(), MagicMock()],
                "ma_lop": "VT-C4K-CLS1",
                "buoi_so": "3",
                "ngay_hoc": "10/11/2026",
                "ngay_hoc_iso": "2026-11-10",
                "qc_issue_note": "",
                "nx_zalo_checked": False,
                "nx_lms_checked": True,
            },
            {
                "row_index": 1,
                "cells": [MagicMock(), MagicMock(), MagicMock()],
                "ma_lop": "VT-C4K-CLS2",
                "buoi_so": "3",
                "ngay_hoc": "10/11/2026",
                "ngay_hoc_iso": "2026-11-10",
                "qc_issue_note": "",
                "nx_zalo_checked": False,
                "nx_lms_checked": True,
            },
        ]
        with (
            patch(
                "services.zalo_bcnb_service._read_bcnb_schedule_rows",
                AsyncMock(
                    side_effect=lambda _p, *, week, year, **_kw: (
                        list(fake_bcnb_rows_w46) if week == 46 else []
                    )
                ),
            ),
            patch(
                "services.zalo_bcnb_service.resolve_report_columns",
                AsyncMock(return_value=(0, 1, 2)),
            ),
            patch(
                "services.zalo_bcnb_service.resolve_row_proven_auto_issues",
                AsyncMock(return_value=[]),
            ),
            patch(
                "services.zalo_bcnb_service.execute_verified_checkbox_action",
                AsyncMock(return_value="DRY_RUN"),
            ),
            patch(
                "services.zalo_bcnb_service.execute_verified_checker_note_action",
                AsyncMock(return_value="DRY_RUN"),
            ),
        ):
            await update_bcnb_reports(
                MagicMock(),
                job_id=77,
                execution_mode="dry-run",
                scan_results=two_class_results,
                today=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
                now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
                scan_plan=fake_plan,
                job_repo=fake_job_repo,
                audit_repo=fake_audit_repo,
                lms_slots_by_class=two_class_slots,
                notes_only=True,
                lms_ai_reviewer=quota1_gw,
            )

        self.assertEqual(reset_call_count, 1)
        self.assertEqual(len(sdk_calls), 1)
        # Class 1's AI verdict (VALID_CONCISE) is preserved through per-row note sync and final re-inspection!
        self.assertEqual(two_class_results[0]["lms_quality_issues"], [])
        # Keys in _ai_comment_verdicts and _verdict_cache are SHA-256 digests, never raw text!
        verdict_map = (two_class_results[0].get("scan_target") or {}).get(
            "_ai_comment_verdicts", {}
        )
        self.assertTrue(verdict_map)
        for k in verdict_map:
            self.assertRegex(k, r"^[0-9a-f]{64}$")
        for k in quota1_gw._verdict_cache:
            self.assertRegex(k, r"^[0-9a-f]{64}$")
        # Class 2 fell back deterministically after quota=1 was reached
        self.assertEqual(
            [issue_code(x) for x in two_class_results[1]["lms_quality_issues"]],
            [ISSUE_CODE_LMS_COMMENT_NOTE],
        )

        # (d) Bare given name at start of comment or after focus words (e.g. "Riêng Thái...", "Còn Thái...", "Với Thái,...")
        # is scrubbed to "[HS]", while common bigrams ("Thái Độ Học Tập...") remain intact.
        cases_with_and_without_roster = [
            (
                "Riêng Thái nắm vững vòng lặp Python và biết tự sửa lỗi.",
                "Riêng [HS] nắm vững vòng lặp Python và biết tự sửa lỗi.",
            ),
            (
                "Còn Thái làm bài rất nhanh.",
                "Còn [HS] làm bài rất nhanh.",
            ),
            (
                "Với Thái, con nắm bài tốt.",
                "Với [HS], con nắm bài tốt.",
            ),
            (
                "Thái Độ Học Tập tích cực, riêng Thái nắm vững vòng lặp Python.",
                "Thái Độ Học Tập tích cực, riêng [HS] nắm vững vòng lặp Python.",
            ),
        ]
        for raw_c, expected_c in cases_with_and_without_roster:
            self.assertEqual(
                _scrub_comment_for_ai(raw_c, known_student_names=["Nguyễn Văn Thái"]),
                expected_c,
            )
            self.assertEqual(
                _scrub_comment_for_ai(raw_c),
                expected_c,
            )

        # Verify end-to-end prompt capture via GeminiLmsCommentReviewerGateway
        captured_prompts: list[str] = []

        def _capture_gen(*, model: str, contents: str, config: object = None) -> object:
            captured_prompts.append(contents)
            return SimpleNamespace(
                text='{"items":[{"id":"stu_c_0","classification":"VALID_CONCISE"}]}'
            )

        thai_gw = GeminiLmsCommentReviewerGateway(
            model="gemini-3.1-flash-lite-preview",
            timeout_seconds=2.0,
            max_batches_per_scan=5,
            client_factory=lambda: SimpleNamespace(
                models=SimpleNamespace(generate_content=_capture_gen)
            ),
        )
        thai_scan_results = [
            {
                "class_code": "VT-CS-THAI",
                "lesson": "3",
                "evidence_state": "SUBMITTED_VERIFIED",
                "scan_target": {
                    "class_code": "VT-CS-THAI",
                    "lesson": "3",
                    "week": 46,
                    "year": 2026,
                    "class_date_iso": "2026-11-10",
                },
            }
        ]
        thai_slots = {
            "VT-CS-THAI": [
                {
                    "index": 2,
                    "date": "2026-11-10",
                    "summary": "Buổi 3: Lập trình vòng lặp và hàm cơ bản trong Python",
                    "studentAttendance": [
                        {
                            "status": "ATTENDED",
                            "comment": "Thái Độ tốt, riêng Thái làm bài nhanh. Với Thái, con chăm.",
                        }
                    ],
                }
            ]
        }
        await inspect_and_attach_quality_issues_async(
            thai_scan_results,
            lms_slots_by_class=thai_slots,
            now=datetime(2026, 11, 12, 10, 0, tzinfo=BUSINESS_TIMEZONE),
            ai_reviewer=thai_gw,
            latest_week={"week": 46, "year": 2026},
            reset_ai_batch_counter=True,
        )
        self.assertEqual(thai_scan_results[0]["lms_quality_issues"], [])
        self.assertEqual(len(captured_prompts), 1)
        self.assertIn("Thái Độ tốt", captured_prompts[0])
        self.assertIn("riêng [HS] làm bài nhanh", captured_prompts[0])
        self.assertIn("Với [HS], con chăm", captured_prompts[0])
        self.assertNotIn("riêng Thái", captured_prompts[0])
        self.assertNotIn("Với Thái", captured_prompts[0])

    def test_chang2_spck_title_rule_and_raw_title_key_duplicates(self) -> None:
        from services.lms_quality_service import (
            _merge_student_works,
            evaluate_lms_spck_quality,
        )
        from services.report_status import (
            ISSUE_CODE_LMS_SPCK_NOTE,
            SEVERITY_NOTE,
            issue_code,
            issue_severity,
            issue_text,
        )

        good_comment = (
            "Sản phẩm hoàn thiện tốt, bố cục hợp lý, nhân vật điều khiển mượt mà "
            "và xử lý đầy đủ các tình huống va chạm trong dự án."
        )
        single_attended = [{"status": "ATTENDED", "studentKey": "stu_1"}]

        # 1. Valid product titles WITHOUT student name -> 0 title issues
        for valid_title in (
            "Website cửa hàng sách",
            "Game bắn thiên thạch",
            "SPCK - Game bắn thiên thạch",
            "Web bán hàng mini",
            "Game bắn gà",
        ):
            res_ok = evaluate_lms_spck_quality(
                {
                    "studentAttendance": single_attended,
                    "studentWorks": [
                        {
                            "studentKey": "stu_1",
                            "title": valid_title,
                            "hasStudentInfoInTitle": False,
                            "thumbnail": "https://img/thumb.png",
                            "imageUrl": ["https://img/shot.png"],
                            "attachmentUrls": ["https://files/proj.sb3"],
                            "comment": good_comment,
                        }
                    ],
                },
                class_code="VT-C4K-CSI02",
                khoi="Coding",
                lesson=12,
            )
            self.assertEqual(res_ok["issues"], [], msg=f"Valid title flagged: {valid_title!r}")
            self.assertEqual(res_ok["bad_title_count"], 0)
            self.assertEqual(res_ok["no_title_count"], 0)
            self.assertEqual(res_ok["duplicate_title_count"], 0)
            self.assertEqual(res_ok["missing_student_name_in_title_count"], 0)
            self.assertEqual(res_ok["missing_student_info_count"], 0)

        # 2. Exception: student's own full name alone (no generic bypass words) -> valid (0 issues)
        res_hs_scrubbed = evaluate_lms_spck_quality(
            {
                "studentAttendance": single_attended,
                "studentWorks": [
                    {
                        "studentKey": "stu_1",
                        "title": "[HS]",
                        "hasStudentInfoInTitle": True,
                        "thumbnail": "https://img/thumb.png",
                        "imageUrl": ["https://img/shot.png"],
                        "attachmentUrls": ["https://files/proj.sb3"],
                        "comment": good_comment,
                    }
                ],
            },
            class_code="VT-C4K-CSI02",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(res_hs_scrubbed["issues"], [])
        self.assertEqual(res_hs_scrubbed["bad_title_count"], 0)

        res_raw_student_name = evaluate_lms_spck_quality(
            {
                "studentAttendance": [
                    {
                        "status": "ATTENDED",
                        "studentKey": "stu_1",
                        "studentName": "Nguyễn Minh Khôi",
                    }
                ],
                "studentWorks": [
                    {
                        "studentKey": "stu_1",
                        "title": "Nguyễn Minh Khôi",
                        "hasStudentInfoInTitle": False,
                        "thumbnail": "https://img/thumb.png",
                        "imageUrl": ["https://img/shot.png"],
                        "attachmentUrls": ["https://files/proj.sb3"],
                        "comment": good_comment,
                    }
                ],
            },
            class_code="VT-C4K-CSI02",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(res_raw_student_name["issues"], [])
        self.assertEqual(res_raw_student_name["bad_title_count"], 0)

        # 3. Placeholder / generic bypass titles -> [SPCK] Bổ sung tên SP
        for bad_title in (
            "",
            ".",
            "...",
            "SPCK",
            "Sản phẩm cuối khóa",
            "SPCK - [HS]",
            "Dự án cuối khóa",
            "VT-CSI02 Buổi 13 SPCK 1",
            "Bài nộp của em [HS] lớp JSB15",
        ):
            res_bad = evaluate_lms_spck_quality(
                {
                    "studentAttendance": single_attended,
                    "studentWorks": [
                        {
                            "studentKey": "stu_1",
                            "title": bad_title,
                            "hasStudentInfoInTitle": "[HS]" in bad_title,
                            "thumbnail": "https://img/thumb.png",
                            "imageUrl": ["https://img/shot.png"],
                            "attachmentUrls": ["https://files/proj.sb3"],
                            "comment": good_comment,
                        }
                    ],
                },
                class_code="VT-C4K-CSI02",
                khoi="Coding",
                lesson=12,
            )
            self.assertEqual(res_bad["bad_title_count"], 1, msg=f"Missed bypass: {bad_title!r}")
            self.assertEqual(res_bad["no_title_count"], 1)
            self.assertEqual(res_bad["missing_student_info_count"], 1)
            self.assertEqual([issue_text(x) for x in res_bad["issues"]], ["[SPCK] Bổ sung tên SP"])
            self.assertEqual(issue_code(res_bad["issues"][0]), ISSUE_CODE_LMS_SPCK_NOTE)
            self.assertEqual(issue_severity(res_bad["issues"][0]), SEVERITY_NOTE)

        # Also "SPCK - Nguyễn Minh Khôi" with raw studentName -> generic bypass!
        res_spck_plus_name = evaluate_lms_spck_quality(
            {
                "studentAttendance": [
                    {
                        "status": "ATTENDED",
                        "studentKey": "stu_1",
                        "studentName": "Nguyễn Minh Khôi",
                    }
                ],
                "studentWorks": [
                    {
                        "studentKey": "stu_1",
                        "title": "SPCK - Nguyễn Minh Khôi",
                        "hasStudentInfoInTitle": True,
                        "thumbnail": "https://img/thumb.png",
                        "imageUrl": ["https://img/shot.png"],
                        "attachmentUrls": ["https://files/proj.sb3"],
                        "comment": good_comment,
                    }
                ],
            },
            class_code="VT-C4K-CSI02",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(res_spck_plus_name["bad_title_count"], 1)
        self.assertEqual(
            [issue_text(x) for x in res_spck_plus_name["issues"]], ["[SPCK] Bổ sung tên SP"]
        )

        # 4. Plain-dict duplicate copy-pasted titles (>= 2 students) vs [HS] non-collision
        two_attended = [
            {"status": "ATTENDED", "studentKey": "stu_1"},
            {"status": "ATTENDED", "studentKey": "stu_2"},
        ]
        dup_plain = evaluate_lms_spck_quality(
            {
                "studentAttendance": two_attended,
                "studentWorks": [
                    {
                        "studentKey": "stu_1",
                        "title": "Game Scratch",
                        "thumbnail": "https://img/t1.png",
                        "imageUrl": ["https://img/s1.png"],
                        "attachmentUrls": ["https://files/1.sb3"],
                        "comment": good_comment,
                    },
                    {
                        "studentKey": "stu_2",
                        "title": "  game   scratch ",
                        "thumbnail": "https://img/t2.png",
                        "imageUrl": ["https://img/s2.png"],
                        "attachmentUrls": ["https://files/2.sb3"],
                        "comment": good_comment,
                    },
                ],
            },
            class_code="VT-C4K-CSI02",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(dup_plain["duplicate_title_count"], 2)
        self.assertEqual(dup_plain["bad_title_count"], 2)
        self.assertEqual(dup_plain["no_title_count"], 0)
        self.assertEqual([issue_text(x) for x in dup_plain["issues"]], ["[SPCK] Bổ sung tên SP"])

        # Without rawTitleKey, 2 students scrubbed to "[HS]" must NOT collide
        hs_no_key = evaluate_lms_spck_quality(
            {
                "studentAttendance": two_attended,
                "studentWorks": [
                    {
                        "studentKey": "stu_1",
                        "title": "[HS]",
                        "hasStudentInfoInTitle": True,
                        "thumbnail": "https://img/t1.png",
                        "imageUrl": ["https://img/s1.png"],
                        "attachmentUrls": ["https://files/1.sb3"],
                        "comment": good_comment,
                    },
                    {
                        "studentKey": "stu_2",
                        "title": "[HS]",
                        "hasStudentInfoInTitle": True,
                        "thumbnail": "https://img/t2.png",
                        "imageUrl": ["https://img/s2.png"],
                        "attachmentUrls": ["https://files/2.sb3"],
                        "comment": good_comment,
                    },
                ],
            },
            class_code="VT-C4K-CSI02",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(hs_no_key["duplicate_title_count"], 0)
        self.assertEqual(hs_no_key["issues"], [])

        # _merge_student_works preserves rawTitleKey across split work entries
        merged_work = _merge_student_works(
            [
                {"studentKey": "stu_1", "title": "[HS]", "rawTitleKey": "rk_abc123"},
                {"studentKey": "stu_1", "thumbnail": "https://img/t1.png"},
            ]
        )
        self.assertEqual(merged_work["rawTitleKey"], "rk_abc123")

        # 5. Distinct rawTitleKey on [HS]-scrubbed student names vs identical rawTitleKey on copy-paste
        eval_distinct_keys = evaluate_lms_spck_quality(
            {
                "studentAttendance": two_attended,
                "studentWorks": [
                    {
                        "studentKey": "stu_1",
                        "title": "[HS]",
                        "hasStudentInfoInTitle": True,
                        "rawTitleKey": "rk_1111aaaa",
                        "thumbnail": "https://img/t1.png",
                        "imageUrl": ["https://img/s1.png"],
                        "attachmentUrls": ["https://files/1.sb3"],
                        "comment": good_comment,
                    },
                    {
                        "studentKey": "stu_2",
                        "title": "[HS]",
                        "hasStudentInfoInTitle": True,
                        "rawTitleKey": "rk_2222bbbb",
                        "thumbnail": "https://img/t2.png",
                        "imageUrl": ["https://img/s2.png"],
                        "attachmentUrls": ["https://files/2.sb3"],
                        "comment": good_comment,
                    },
                ],
            },
            class_code="VT-C4K-CSI02",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(eval_distinct_keys["duplicate_title_count"], 0)
        self.assertEqual(eval_distinct_keys["bad_title_count"], 0)
        self.assertEqual(eval_distinct_keys["issues"], [])

        eval_same_keys = evaluate_lms_spck_quality(
            {
                "studentAttendance": two_attended,
                "studentWorks": [
                    {
                        "studentKey": "stu_1",
                        "title": "[HS] - Game Bắn Thiên Thạch",
                        "hasStudentInfoInTitle": True,
                        "rawTitleKey": "rk_same9999",
                        "thumbnail": "https://img/t1.png",
                        "imageUrl": ["https://img/s1.png"],
                        "attachmentUrls": ["https://files/1.sb3"],
                        "comment": good_comment,
                    },
                    {
                        "studentKey": "stu_2",
                        "title": "[HS] - Game Bắn Thiên Thạch",
                        "hasStudentInfoInTitle": True,
                        "rawTitleKey": "rk_same9999",
                        "thumbnail": "https://img/t2.png",
                        "imageUrl": ["https://img/s2.png"],
                        "attachmentUrls": ["https://files/2.sb3"],
                        "comment": good_comment,
                    },
                ],
            },
            class_code="VT-C4K-CSI02",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(eval_same_keys["duplicate_title_count"], 2)
        self.assertEqual(eval_same_keys["bad_title_count"], 2)
        self.assertEqual(
            [issue_text(x) for x in eval_same_keys["issues"]], ["[SPCK] Bổ sung tên SP"]
        )

    def test_chang3_muc_a_task_grouped_notes_zalo_lms_spck(self) -> None:
        from services.lms_quality_service import (
            evaluate_lms_slot_quality,
            evaluate_lms_spck_quality,
            evaluate_zalo_content_issues,
        )
        from services.report_status import (
            ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
            ISSUE_CODE_LMS_COMMENT_NOTE,
            ISSUE_CODE_LMS_MANUAL_CHECK,
            ISSUE_CODE_LMS_QUALITY_FAIL,
            ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
            ISSUE_CODE_LMS_SPCK_MISSING,
            ISSUE_CODE_LMS_SPCK_NOTE,
            ISSUE_CODE_LMS_SUMMARY_NOTE,
            ISSUE_CODE_ZALO_CONTENT_FAIL,
            ISSUE_CODE_ZALO_REPORT_NO_DATE,
            ISSUE_CODE_ZALO_REPORT_WRONG_DATE,
            SEVERITY_FAIL,
            SEVERITY_NOTE,
            issue_code,
            issue_severity,
            issue_text,
        )

        # 1. LMS Session: FAIL stays separate with (x/y); all NOTE items merge into 1 sentence
        slot_mixed = evaluate_lms_slot_quality(
            {
                "summary": "",
                "studentAttendance": [
                    {"status": "ATTENDED", "comment": ""},
                    {"status": "ATTENDED", "comment": "Bạn nghỉ phép buổi hôm nay"},
                    {"status": "ATTENDED", "comment": "Con ngoan."},
                ],
            },
            class_code="VT-C4K-GA38",
            khoi="Coding",
            lesson=3,
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in slot_mixed["issues"]],
            [
                (ISSUE_CODE_LMS_QUALITY_FAIL, SEVERITY_FAIL, "[LMS] Chưa NX (1/3)"),
                (
                    ISSUE_CODE_LMS_COMMENT_NOTE,
                    SEVERITY_NOTE,
                    "[LMS] Chưa có nội dung bài, kiểm tra điểm danh-NX, viết kỹ NX",
                ),
            ],
        )

        # Checkpoint lesson (Coding B5): summary + shallow checkpoint -> "[LMS] Chưa có nội dung bài, đủ khung Checkpoint"
        slot_cp = evaluate_lms_slot_quality(
            {
                "summary": "",
                "studentAttendance": [
                    {"status": "ATTENDED", "comment": "Con hiểu bài tốt nhưng chưa đủ ba mục."}
                ],
            },
            class_code="VT-C4K-GA38",
            khoi="Coding",
            lesson=5,
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in slot_cp["issues"]],
            [(ISSUE_CODE_LMS_COMMENT_NOTE, SEVERITY_NOTE, "[LMS] Chưa có nội dung bài, đủ khung Checkpoint")],
        )

        # Summary + attendance conflict only -> code = LMS_ATTENDANCE_CONFLICT_NOTE
        slot_sum_conf = evaluate_lms_slot_quality(
            {
                "summary": "",
                "studentAttendance": [{"status": "ATTENDED", "comment": "HV xin nghỉ"}],
            },
            class_code="VT-C4K-GA38",
            khoi="Coding",
            lesson=4,
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in slot_sum_conf["issues"]],
            [
                (
                    ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE,
                    SEVERITY_NOTE,
                    "[LMS] Chưa có nội dung bài, kiểm tra điểm danh-NX",
                )
            ],
        )

        # Summary only -> code = LMS_SUMMARY_NOTE
        slot_sum_only = evaluate_lms_slot_quality(
            {
                "summary": "",
                "studentAttendance": [
                    {
                        "status": "ATTENDED",
                        "comment": (
                            "Con nắm vững kiến thức bài học hôm nay, tự giác hoàn thành đầy đủ "
                            "các thử thách lập trình trên lớp và biết vận dụng vòng lặp rất tốt."
                        ),
                    }
                ],
            },
            class_code="VT-C4K-GA38",
            khoi="Coding",
            lesson=4,
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in slot_sum_only["issues"]],
            [(ISSUE_CODE_LMS_SUMMARY_NOTE, SEVERITY_NOTE, "[LMS] Chưa có nội dung bài")],
        )

        # 2. SPCK: 0 submitted -> [SPCK] Chưa nộp (0/4); missing NX -> [SPCK] Chưa NX (x/y);
        #    all 7 NOTE tasks grouped in exact canonical order + manual check
        spck_zero = evaluate_lms_spck_quality(
            {
                "studentAttendance": [
                    {"status": "ATTENDED", "studentKey": f"s{i}"} for i in range(4)
                ],
                "studentWorks": [],
            },
            class_code="VT-C4K-GA38",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in spck_zero["issues"]],
            [(ISSUE_CODE_LMS_SPCK_MISSING, SEVERITY_FAIL, "[SPCK] Chưa nộp (0/4)")],
        )

        spck_all_tasks = evaluate_lms_spck_quality(
            {
                "studentAttendance": [
                    {"status": "ATTENDED", "studentKey": "s1"},
                    {"status": "ATTENDED", "studentKey": "s2"},
                    {"status": "ATTENDED", "studentKey": "s3"},
                ],
                "studentWorks": [
                    {
                        "studentKey": "s1",
                        "title": "SPCK",
                        "thumbnail": "",
                        "imageUrl": [],
                        "attachmentUrls": [],
                        "videoUrls": [],
                        "relatedUrls": [],
                        "comment": "",
                    },
                    {
                        "studentKey": "s2",
                        "title": "Website Thư Viện",
                        "thumbnail": "https://img/t.png",
                        "imageUrl": ["https://img/s.png"],
                        "attachmentUrls": ["https://files/p.zip"],
                        "videoUrls": ["https://youtu.be/abc12345"],
                        "relatedUrls": ["https://docs.google.com/presentation/d/1"],
                        "comment": "Tốt.",
                    },
                ],
            },
            class_code="VT-C4K-GA38",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in spck_all_tasks["issues"]],
            [
                (ISSUE_CODE_LMS_SPCK_COMMENT_MISSING, SEVERITY_FAIL, "[SPCK] Chưa NX (1/2)"),
                (
                    ISSUE_CODE_LMS_SPCK_NOTE,
                    SEVERITY_NOTE,
                    "[SPCK] Bổ sung nộp đủ bài, tên SP, ảnh, file, viết kỹ NX, YouTube, Slide",
                ),
            ],
        )

        spck_manual = evaluate_lms_spck_quality(
            {"studentAttendance": [{"status": "ATTENDED", "studentKey": "s1"}]},
            class_code="VT-C4K-GA38",
            khoi="Coding",
            lesson=12,
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in spck_manual["issues"]],
            [(ISSUE_CODE_LMS_MANUAL_CHECK, SEVERITY_NOTE, "[SPCK] Kiểm tra tay trên LMS")],
        )

        # 3. Zalo: NOTE-only preserves NOTE severity + code; FAIL+NOTE groups into 1 ZALO_CONTENT_FAIL
        zalo_no_date = evaluate_zalo_content_issues(
            {
                "evidence_state": "SUBMITTED_VERIFIED",
                "scan_target": {"lesson": "5"},
                "evidence": {
                    "date_context": {
                        "target_lesson": "5",
                        "target_match_decision": "TARGET_SAME_DAY_NO_DATE",
                        "target_match_note": "[Zalo] Thiếu ngày/buổi",
                    }
                },
            }
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in zalo_no_date],
            [(ISSUE_CODE_ZALO_REPORT_NO_DATE, SEVERITY_NOTE, "[Zalo] Thiếu ngày/buổi")],
        )

        zalo_wrong_date = evaluate_zalo_content_issues(
            {
                "evidence_state": "SUBMITTED_VERIFIED",
                "scan_target": {"lesson": "5"},
                "evidence": {
                    "date_context": {
                        "target_lesson": "5",
                        "target_match_decision": "TARGET_SAME_DAY_WRONG_DATE",
                        "target_match_note": "[Zalo] Sai ngày (03/11)",
                    }
                },
            }
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in zalo_wrong_date],
            [(ISSUE_CODE_ZALO_REPORT_WRONG_DATE, SEVERITY_NOTE, "[Zalo] Sai ngày (03/11)")],
        )

        zalo_mixed_fail = evaluate_zalo_content_issues(
            {
                "evidence_state": "SUBMITTED_VERIFIED",
                "reported_lesson_title": "Làm game đua xe vũ trụ hoàn chỉnh",
                "scan_target": {"lesson": "5"},
                "evidence": {
                    "date_context": {
                        "target_lesson": "5",
                        "content_lesson": 6,
                        "target_match_decision": "TARGET_SAME_DAY_NO_DATE",
                        "target_match_note": "[Zalo] Thiếu ngày/buổi",
                    }
                },
            },
            lms_summary_text="Buổi 5: Biến và kiểu dữ liệu cơ bản trong Python",
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in zalo_mixed_fail],
            [
                (
                    ISSUE_CODE_ZALO_CONTENT_FAIL,
                    SEVERITY_FAIL,
                    "[Zalo] Thiếu ngày/buổi, sai số buổi, lệch tên bài với LMS",
                )
            ],
        )

    def test_inspect_and_attach_missing_attendance_and_legacy_muc_a_dedup(self) -> None:
        from datetime import datetime
        from core.time_utils import BUSINESS_TIMEZONE

        # 1. MISSING_ATTENDANCE supersedes prior [LMS] Chưa ghi nội dung bài học / [SPCK] Chưa nộp
        #    and leaves strictly [LMS] Chưa điểm danh on LMS side
        row_rob = {
            "class_code": "VT-KIND-ROB4B04",
            "lesson": "12",
            "class_date_iso": "2026-11-15",
            "evidence_state": "SUBMITTED_VERIFIED",
            "lms_quality_issues": [
                "[LMS] Chưa ghi nội dung bài học",
                "[SPCK] Chưa nộp (0/6)",
            ],
            "quality_issues": [
                "[LMS] Chưa ghi nội dung bài học",
                "[SPCK] Chưa nộp (0/6)",
            ],
            "scan_target": {
                "class_code": "VT-KIND-ROB4B04",
                "lesson": "12",
                "class_date": "15/11/2026",
                "class_date_iso": "2026-11-15",
                "khoi": "Robotics",
            },
        }
        inspect_and_attach_quality_issues(
            [row_rob],
            lms_slots_by_class={
                "VT-KIND-ROB4B04": [
                    {
                        "_id": "s12",
                        "index": 11,
                        "date": "2026-11-15",
                        "summary": "",
                        "studentAttendance": [],
                        "activeClassStudentCount": 6,
                        "studentWorks": [],
                    }
                ]
            },
            now=datetime(2026, 11, 16, 10, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(row_rob["lms_attendance"]["status"], "MISSING_ATTENDANCE")
        self.assertEqual(row_rob["lms_comments"]["status"], "MISSING_ATTENDANCE")
        self.assertEqual(row_rob["lms_spck"]["status"], "MISSING_ATTENDANCE")
        self.assertEqual(
            [issue_text(x) for x in row_rob["lms_quality_issues"]],
            ["[LMS] Chưa điểm danh"],
        )
        self.assertEqual(
            [issue_text(x) for x in row_rob["quality_issues"]],
            ["[LMS] Chưa điểm danh"],
        )

        # 2. When attendance is genuinely UNVERIFIED (truncated), legacy [LMS] Chưa ghi nội dung bài học
        #    is deduplicated in favor of Mức A [LMS] Chưa có nội dung bài so they never coexist
        row_unverified = {
            "class_code": "VT-KIND-ROB4B04",
            "lesson": "12",
            "class_date_iso": "2026-11-15",
            "evidence_state": "SUBMITTED_VERIFIED",
            "lms_quality_issues": ["[LMS] Chưa ghi nội dung bài học"],
            "quality_issues": ["[LMS] Chưa ghi nội dung bài học"],
            "scan_target": {
                "class_code": "VT-KIND-ROB4B04",
                "lesson": "12",
                "class_date": "15/11/2026",
                "class_date_iso": "2026-11-15",
                "khoi": "Robotics",
            },
        }
        inspect_and_attach_quality_issues(
            [row_unverified],
            lms_slots_by_class={
                "VT-KIND-ROB4B04": [
                    {
                        "_id": "s12",
                        "index": 11,
                        "date": "2026-11-15",
                        "summary": "",
                        "studentAttendance": [],
                        "activeClassStudentCount": 6,
                        "truncated": True,
                        "studentWorks": [],
                    }
                ]
            },
            now=datetime(2026, 11, 16, 10, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(
            [issue_text(x) for x in row_unverified["lms_quality_issues"]],
            ["[LMS] Chưa có nội dung bài", "[LMS] Kiểm tra tay trên LMS"],
        )
        self.assertIn(
            "[LMS] Chưa ghi nội dung bài học",
            [issue_text(x) for x in row_unverified["lms_previous_quality_issues"]],
        )

        # 3. Prior verified [SPCK] Chưa nộp (0/6) + transient studentWorksFetchError -> keeps [SPCK] Chưa nộp (0/6),
        #    never coexists with [SPCK] Kiểm tra tay trên LMS
        row_semia06 = {
            "class_code": "VT-ROB-SEMIA06",
            "lesson": "12",
            "class_date_iso": "2026-11-22",
            "evidence_state": "NOT_FOUND_VERIFIED",
            "lms_quality_issues": [
                {"code": "LMS_SUMMARY_NOTE", "severity": "NOTE", "text": "[LMS] Điền tóm tắt bài"},
                {"code": "LMS_SPCK_MISSING", "severity": "FAIL", "text": "[SPCK] Chưa nộp (0/6)"},
            ],
            "quality_issues": [
                {"code": "LMS_SUMMARY_NOTE", "severity": "NOTE", "text": "[LMS] Điền tóm tắt bài"},
                {"code": "LMS_SPCK_MISSING", "severity": "FAIL", "text": "[SPCK] Chưa nộp (0/6)"},
            ],
            "scan_target": {
                "class_code": "VT-ROB-SEMIA06",
                "lesson": "12",
                "class_date": "22/11/2026",
                "class_date_iso": "2026-11-22",
                "khoi": "Robotics",
            },
        }
        inspect_and_attach_quality_issues(
            [row_semia06],
            lms_slots_by_class={
                "VT-ROB-SEMIA06": [
                    {
                        "_id": "s12-semi",
                        "index": 11,
                        "date": "2026-11-22",
                        "summary": "",
                        "studentAttendance": [
                            {
                                "status": "ATTENDED",
                                "studentKey": f"s{i}",
                                "comment": (
                                    "Điểm mạnh: Con hiểu bài tốt và thực hành đúng quy trình.\n"
                                    "Điểm cần cải thiện: Chú ý lắp nối chắc chắn hơn.\n"
                                    "Lời khuyên: Ôn lại kiến thức buổi học."
                                ),
                            }
                            for i in range(6)
                        ],
                        "studentWorksFetchError": True,
                    }
                ]
            },
            now=datetime(2026, 11, 25, 10, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        semia_texts = [issue_text(x) for x in row_semia06["lms_quality_issues"]]
        self.assertEqual(semia_texts, ["[LMS] Chưa có nội dung bài", "[SPCK] Chưa nộp (0/6)"])
        self.assertNotIn("[SPCK] Kiểm tra tay trên LMS", semia_texts)

    async def test_lesson_14_ranking_and_full_name_check_all_classes_and_pii_free(self) -> None:
        import json
        import os
        import subprocess
        from unittest.mock import patch
        from services.lms_quality_service import evaluate_lms_spck_quality
        from services.report_status import (
            ISSUE_CODE_LMS_QUALITY_FAIL,
            ISSUE_CODE_LMS_SUMMARY_NOTE,
            LESSON_14_EXEMPTION,
            LESSON_14_STATUS,
            SEVERITY_FAIL,
            SEVERITY_NOTE,
            is_failed,
            is_lms_ready_for_bcnb_tick,
            issue_code,
            issue_severity,
            issue_text,
        )

        roster_4 = [
            "Nguyễn Văn An",
            "Trần Thị Bình",
            "Lê Hoàng Cường",
            "Phạm Minh Đức",
        ]
        att_4 = [
            {
                "status": "ATTENDED",
                "studentKey": f"stu_{idx + 1}",
                "student": {"fullName": name},
                "comment": "",
            }
            for idx, name in enumerate(roster_4)
        ]

        # 1. Passing formats: numbered, Giải nhất/nhì/ba, Top 1/2/3, Hạng 1/2/3, emoji, inline, <ol><li>
        passing_summaries = [
            "1. Nguyễn Văn An\n2. Trần Thị Bình\n3. Lê Hoàng Cường",
            "Giải nhất: Nguyễn Văn An\nGiải nhì: Trần Thị Bình\nGiải ba: Lê Hoàng Cường",
            "Top 1: Nguyễn Văn An, Top 2: Trần Thị Bình, Top 3: Lê Hoàng Cường",
            "Top 1: Bạn Nguyễn Văn An\nTop 2: Bạn Trần Thị Bình\nTop 3: Bạn Lê Hoàng Cường",
            "No. 1: Bạn Nguyễn Văn An\nNo. 2: Bạn Trần Thị Bình\nNo. 3: Bạn Lê Hoàng Cường",
            "#1: Học viên Nguyễn Văn An\n#2: Học viên Trần Thị Bình\n#3: Học viên Lê Hoàng Cường",
            "Top 3 bạn xuất sắc nhất:\nTop 1: Bạn Nguyễn Văn An\nTop 2: Bạn Trần Thị Bình\nTop 3: Bạn Lê Hoàng Cường",
            "Hạng 1 - Nguyễn Văn An\nHạng 2 - Trần Thị Bình\nHạng 3 - Lê Hoàng Cường",
            "🥇 Nguyễn Văn An\n🥈 Trần Thị Bình\n🥉 Lê Hoàng Cường",
            "<p>Xếp hạng SPCK:</p><ol><li>Nguyễn Văn An - Game Python</li><li>Trần Thị Bình - App</li><li>Lê Hoàng Cường - Web</li></ol>",
            "<ol><li></li><li><br></li><li>Nguyễn Văn An</li><li>Trần Thị Bình</li><li>Lê Hoàng Cường</li></ol>",
            "1-Nguyễn Văn An\n2:Trần Thị Bình\n3)Lê Hoàng Cường",
            "Xếp hạng: 1. nguyen van an; 2. tran thi binh; 3. le hoang cuong",
        ]
        for idx, summary_text in enumerate(passing_summaries):
            res = evaluate_lms_slot_quality(
                {
                    "summary": summary_text,
                    "studentAttendance": att_4,
                    "studentWorks": [],
                },
                class_code="VT-C4K-PTB05",
                khoi="Coding",
                lesson=14,
            )
            self.assertEqual(res["issues"], [], msg=f"case={idx}")
            self.assertTrue(res["has_valid_summary"], msg=f"case={idx}")
            self.assertEqual(res["summary_clean"], "", msg=f"case={idx}")

        # 2. Fewer than 3 attendees -> only min(3, attended_count) ranks required
        res_2_att = evaluate_lms_slot_quality(
            {
                "summary": "Giải nhất: Nguyễn Văn An\nGiải nhì: Trần Thị Bình",
                "studentAttendance": att_4[:2],
                "studentWorks": [],
            },
            class_code="VT-CSB02",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(res_2_att["issues"], [])

        res_1_att = evaluate_lms_slot_quality(
            {
                "summary": "Top 1: Nguyễn Văn An",
                "studentAttendance": att_4[:1],
                "studentWorks": [],
            },
            class_code="VT-C4K-PTB05",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(res_1_att["issues"], [])

        # 3. Missing ranking across ALL courses (PTB, CSB, Coding, Robotics) -> [LMS] Thiếu xếp hạng (NOTE)
        for cls_code, khoi_name, summary_bad in (
            ("VT-C4K-PTB05", "Coding", ""),
            ("VT-CSB02", "Coding", "Buổi 14: Thuyết trình sản phẩm cuối khóa"),
            ("VT-C4K-PTB06", "Coding", "1. Ôn tập kiến thức\n2. Thuyết trình sản phẩm\n3. Tổng kết khóa học"),
            ("VT-C4K-PTB07", "Coding", "1. Nguyễn Văn An\n2. Trần Thị Bình"),
            ("VT-C4K-PTB08", "Coding", "Top 3 bạn xuất sắc nhất:\n1. Nguyễn Văn An\n2. Trần Thị Bình"),
            ("VT-C4K-PTB09", "Coding", "Buổi 14: Thuyết trình và chọn Top 3 xuất sắc nhất lớp"),
            ("VT-C4K-PTB10", "Coding", "Top 3 bạn xuất sắc nhất:\nTop 1: Nguyễn Văn An\nTop 2: Trần Thị Bình"),
            ("VT-ROB-ARMI12", "Robotics", "Buổi 14: Thi đấu robot"),
        ):
            extra_works = (
                [
                    {
                        "studentKey": f"stu_{i + 1}",
                        "title": f"Robot Cứu Hộ {i + 1}",
                        "thumbnail": "https://img/t.png",
                        "imageUrl": ["https://img/s.png"],
                        "attachmentUrls": ["https://files/code.sb3"],
                        "videoUrls": ["https://youtu.be/abc12345"],
                        "relatedUrls": [],
                        "comment": "Hoàn thành tốt thuyết trình và demo sản phẩm cuối khóa xuất sắc.",
                    }
                    for i in range(4)
                ]
                if cls_code.startswith("VT-ROB")
                else []
            )
            res_missing = evaluate_lms_slot_quality(
                {
                    "summary": summary_bad,
                    "studentAttendance": att_4,
                    "studentWorks": extra_works,
                },
                class_code=cls_code,
                khoi=khoi_name,
                lesson=14,
            )
            self.assertEqual(
                [
                    (issue_code(x), issue_severity(x), issue_text(x))
                    for x in res_missing["issues"]
                ],
                [(ISSUE_CODE_LMS_SUMMARY_NOTE, SEVERITY_NOTE, "[LMS] Thiếu xếp hạng")],
                msg=f"cls={cls_code}",
            )
            row_state = {
                "class_code": cls_code,
                "khoi": khoi_name,
                "lesson": "14",
                "status": LESSON_14_STATUS,
                "evidence_state": LESSON_14_EXEMPTION,
                "lms_status": "✅ Đã hoàn tất (Buổi 14 miễn NX)",
                **{
                    k: res_missing[k]
                    for k in ("lms_attendance", "lms_comments", "lms_spck")
                },
                "lms_quality_issues": res_missing["issues"],
            }
            self.assertFalse(is_failed(row_state))
            ready_ok, ready_reason = is_lms_ready_for_bcnb_tick(row_state)
            self.assertTrue(ready_ok, msg=ready_reason)

        # 4. Incomplete names (only given name or missing surname) -> [LMS] Thiếu xếp hạng, chưa đủ họ tên (NOTE)
        incomplete_summaries = [
            "1. An\n2. Bình\n3. Cường",
            "1. Văn An\n2. Thị Bình\n3. Hoàng Cường",
            "Giải nhất: Nguyễn Văn An\nGiải nhì: Trần Thị Bình\nGiải ba: Cường",
            "Top 1: Bạn Nguyễn Văn An\nTop 2: Bạn Trần Thị Bình\nTop 3: Bạn Cường",
            "Top 1: An\nTop 2: Trần Thị Bình",
            "Giải nhất: Bạn Minh",
        ]
        for idx, summary_short in enumerate(incomplete_summaries):
            res_short = evaluate_lms_slot_quality(
                {
                    "summary": summary_short,
                    "studentAttendance": att_4,
                    "studentWorks": [],
                },
                class_code="VT-C4K-PTB05",
                khoi="Coding",
                lesson=14,
            )
            self.assertEqual(
                [
                    (issue_code(x), issue_severity(x), issue_text(x))
                    for x in res_short["issues"]
                ],
                [
                    (
                        ISSUE_CODE_LMS_SUMMARY_NOTE,
                        SEVERITY_NOTE,
                        "[LMS] Thiếu xếp hạng, chưa đủ họ tên",
                    )
                ],
                msg=f"short_idx={idx}",
            )

        # 5. Lesson 13 does NOT apply Lesson 14 ranking wording
        res_l13 = evaluate_lms_slot_quality(
            {
                "summary": "Buổi 13: Hoàn thiện và kiểm thử sản phẩm cuối khóa",
                "studentAttendance": [
                    {
                        "status": "ATTENDED",
                        "studentKey": "stu_1",
                        "comment": (
                            "Hôm nay con hoàn thiện tốt tính năng chính của sản phẩm và biết cách tự kiểm tra lỗi."
                        ),
                    }
                ],
                "studentWorks": [],
            },
            class_code="VT-C4K-PTB05",
            khoi="Coding",
            lesson=13,
        )
        self.assertEqual(res_l13["issues"], [])

        # 6. MISSING_ATTENDANCE, EXEMPT (all absent), and UNVERIFIED guards at Lesson 14
        res_missing_att = evaluate_lms_slot_quality(
            {
                "summary": "",
                "activeClassStudentCount": 4,
                "studentAttendance": [],
            },
            class_code="VT-C4K-PTB05",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(
            [(issue_code(x), issue_severity(x), issue_text(x)) for x in res_missing_att["issues"]],
            [(ISSUE_CODE_LMS_QUALITY_FAIL, SEVERITY_FAIL, "[LMS] Chưa điểm danh")],
        )

        res_all_absent = evaluate_lms_slot_quality(
            {
                "summary": "",
                "studentAttendance": [{"status": "ABSENT", "studentKey": "stu_1"}],
            },
            class_code="VT-C4K-PTB05",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual(res_all_absent["issues"], [])

        res_unverified = evaluate_lms_slot_quality(
            {
                "summary": "",
                "studentAttendance": [],
            },
            class_code="VT-C4K-PTB05",
            khoi="Coding",
            lesson=14,
        )
        self.assertNotIn("[LMS] Thiếu xếp hạng", [issue_text(x) for x in res_unverified["issues"]])

        # 6b. P1/P2 verifier regressions: outline words colliding with student names, empty-roster Top 3 header,
        #     HTML entities (&ndash;/&mdash;/&#39;), and Lesson 14 summary NOT forwarded to SPCK AI reviewer
        outline_collision_roster = [
            {
                "status": "ATTENDED",
                "studentKey": f"s_{i}",
                "student": {"fullName": nm},
                "comment": "",
            }
            for i, nm in enumerate(
                ("Nguyễn Văn Thành", "Trần Minh Khoa", "Trần Thị Bình", "Phạm Gia Huy")
            )
        ]
        for outline_txt in (
            "1. Hoàn thành sản phẩm\n2. Giới thiệu dự án\n3. Tổng kết khóa học",
            "1. Giới thiệu bài học\n2. Bình chọn dự án\n3. Tổng kết khóa học",
        ):
            res_outline = evaluate_lms_slot_quality(
                {"summary": outline_txt, "studentAttendance": outline_collision_roster, "studentWorks": []},
                class_code="VT-C4K-PTB05",
                khoi="Coding",
                lesson=14,
            )
            self.assertEqual([issue_text(x) for x in res_outline["issues"]], ["[LMS] Thiếu xếp hạng"])

        # Empty roster Top 3 header with only 2 items must NOT falsely pass
        res_empty_roster_top3 = evaluate_lms_slot_quality(
            {
                "summary": "Top 3 bạn xuất sắc nhất:\n1. Nguyễn Văn An\n2. Trần Thị Bình",
                "studentAttendance": [{"status": "ATTENDED", "studentKey": f"s_{i}"} for i in range(3)],
                "studentWorks": [],
            },
            class_code="VT-C4K-PTB05",
            khoi="Coding",
            lesson=14,
        )
        self.assertEqual([issue_text(x) for x in res_empty_roster_top3["issues"]], ["[LMS] Thiếu xếp hạng"])

        # Lesson 14 SPCK AI reviewer must receive empty lesson_summary (0 residual PII)
        seen_ai_summaries: list[str] = []

        class _SpySpckAiReviewer:
            def review_short_comments_sync(self, items, **kwargs):
                seen_ai_summaries.append(str(kwargs.get("lesson_summary", "")))
                return {item["id"]: "VALID_CONCISE" for item in items}

        evaluate_lms_spck_quality(
            {
                "summary": "1. Nguyễn An\n2. Trần Bình",
                "studentAttendance": [{"status": "ATTENDED", "studentKey": "s_1"}],
                "studentWorks": [
                    {
                        "studentKey": "s_1",
                        "title": "Game",
                        "thumbnail": "https://img/t.png",
                        "imageUrl": ["https://img/s.png"],
                        "attachmentUrls": ["https://files/a.zip"],
                        "videoUrls": ["https://youtu.be/abc"],
                        "relatedUrls": ["https://docs.google.com/presentation/d/1"],
                        "comment": "Làm tốt lắm.",
                    }
                ],
            },
            class_code="VT-JSB16",
            khoi="Coding",
            lesson=14,
            ai_reviewer=_SpySpckAiReviewer(),
        )
        self.assertEqual(seen_ai_summaries, [""])

        # 7. Browser JS formatItemSlots computes PII-free lesson14Ranking for PTB/CSB & Coding slots
        runner_l14_js = """
        const { script, args } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        global.fetch = async (url, opts) => {
            const body = JSON.parse((opts && opts.body) || '{}');
            if (body.operationName === 'classesMissAttendOrComment') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        data: {
                            classesMissAttendOrComment: {
                                data: [
                                    {
                                        id: 'cls_ptb_05',
                                        name: 'VT-C4K-PTB05',
                                        students: [
                                            { activeInClass: true, student: { id: 's1', fullName: 'Nguyễn Văn An' } },
                                            { activeInClass: true, student: { id: 's2', fullName: 'Trần Thị Bình' } },
                                            { activeInClass: true, student: { id: 's3', fullName: 'Lê Hoàng Cường' } }
                                        ],
                                        slots: [{
                                            _id: 'slot_ptb_14',
                                            index: 13,
                                            date: '2026-11-22T10:00:00.000Z',
                                            summary: '<ol><li>Nguyễn Văn An</li><li>Trần Thị Bình</li><li>Cường</li></ol>',
                                            studentAttendance: [
                                                { student: { id: 's1', fullName: 'Nguyễn Văn An' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 's2', fullName: 'Trần Thị Bình' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 's3', fullName: 'Lê Hoàng Cường' }, status: 'ATTENDED', comment: '' }
                                            ]
                                        }]
                                    },
                                    {
                                        id: 'cls_jsb_16',
                                        name: 'VT-JSB16',
                                        students: [
                                            { activeInClass: true, student: { id: 'j1', fullName: 'Phạm Minh Đức' } },
                                            { activeInClass: true, student: { id: 'j2', fullName: 'Võ Gia Huy' } },
                                            { activeInClass: true, student: { id: 'j3', fullName: 'Đỗ Ngọc Lan' } }
                                        ],
                                        slots: [{
                                            _id: 'slot_jsb_14',
                                            index: 13,
                                            date: '2026-11-22T10:00:00.000Z',
                                            summary: 'Giải nhất: Phạm Minh Đức\\nGiải nhì: Võ Gia Huy\\nGiải ba: Đỗ Ngọc Lan',
                                            studentAttendance: [
                                                { student: { id: 'j1', fullName: 'Phạm Minh Đức' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 'j2', fullName: 'Võ Gia Huy' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 'j3', fullName: 'Đỗ Ngọc Lan' }, status: 'ATTENDED', comment: '' }
                                            ]
                                        }]
                                    },
                                    {
                                        id: 'cls_csb_03',
                                        name: 'VT-CSB03',
                                        students: [
                                            { activeInClass: true, student: { id: 'c1', fullName: 'Nguyễn Văn Thành' } },
                                            { activeInClass: true, student: { id: 'c2', fullName: 'Trần Minh Khoa' } },
                                            { activeInClass: true, student: { id: 'c3', fullName: 'Lê Bảo Chung' } }
                                        ],
                                        slots: [{
                                            _id: 'slot_csb_14',
                                            index: 13,
                                            date: '2026-11-22T10:00:00.000Z',
                                            summary: '<p>Top 3 bạn xuất sắc nhất:</p><p>1-Nguyễn Văn Thành &ndash; Game</p><p>2:Trần Minh Khoa &mdash; App</p>',
                                            studentAttendance: [
                                                { student: { id: 'c1', fullName: 'Nguyễn Văn Thành' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 'c2', fullName: 'Trần Minh Khoa' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 'c3', fullName: 'Lê Bảo Chung' }, status: 'ATTENDED', comment: '' }
                                            ]
                                        }]
                                    },
                                    {
                                        id: 'cls_csb_04',
                                        name: 'VT-CSB04',
                                        students: [
                                            { activeInClass: true, student: { id: 'd1', fullName: 'Nguyễn Văn Thành' } },
                                            { activeInClass: true, student: { id: 'd2', fullName: 'Trần Minh Khoa' } },
                                            { activeInClass: true, student: { id: 'd3', fullName: 'Lê Bảo Chung' } }
                                        ],
                                        slots: [{
                                            _id: 'slot_csb_04_14',
                                            index: 13,
                                            date: '2026-11-22T10:00:00.000Z',
                                            summary: 'Top 3 bạn xuất sắc nhất:\\nTop 1: Nguyễn Văn Thành\\nTop 2: Trần Minh Khoa',
                                            studentAttendance: [
                                                { student: { id: 'd1', fullName: 'Nguyễn Văn Thành' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 'd2', fullName: 'Trần Minh Khoa' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 'd3', fullName: 'Lê Bảo Chung' }, status: 'ATTENDED', comment: '' }
                                            ]
                                        }]
                                    },
                                    {
                                        id: 'cls_csb_05',
                                        name: 'VT-CSB05',
                                        students: [
                                            { activeInClass: true, student: { id: 'e1', fullName: 'Nguyễn Văn Thành' } },
                                            { activeInClass: true, student: { id: 'e2', fullName: 'Trần Minh Khoa' } },
                                            { activeInClass: true, student: { id: 'e3', fullName: 'Lê Bảo Chung' } }
                                        ],
                                        slots: [{
                                            _id: 'slot_csb_05_14',
                                            index: 13,
                                            date: '2026-11-22T10:00:00.000Z',
                                            summary: 'Top 3 bạn xuất sắc nhất:\\nTop 1: Bạn Nguyễn Văn Thành\\nTop 2: Bạn Trần Minh Khoa\\nTop 3: Bạn Lê Bảo Chung',
                                            studentAttendance: [
                                                { student: { id: 'e1', fullName: 'Nguyễn Văn Thành' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 'e2', fullName: 'Trần Minh Khoa' }, status: 'ATTENDED', comment: '' },
                                                { student: { id: 'e3', fullName: 'Lê Bảo Chung' }, status: 'ATTENDED', comment: '' }
                                            ]
                                        }]
                                    }
                                ]
                            }
                        }
                    })
                };
            }
            return {
                ok: true,
                status: 200,
                json: async () => ({ data: { findAllStudentWorks: { data: [] } } })
            };
        };
        (async () => {
            const res = await eval('(' + script + ')')(args);
            process.stdout.write(JSON.stringify(res));
        })();
        """

        class _L14JsPage:
            url = "https://lms.mindx.edu.vn/admin/class-sessions"

            async def evaluate(self, script: str, args: dict) -> dict:
                proc = subprocess.run(
                    ["node", "-e", runner_l14_js],
                    input=json.dumps(
                        {
                            "script": script,
                            "args": {**args, "preResolvedToken": "eyJhbGciOiJub25lIn0.eyJleHAiOjQ5OTk5OTk5OTl9."},
                        }
                    ),
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=True,
                )
                return json.loads(proc.stdout)

        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}):
            fetched_l14 = await fetch_lms_class_slots_via_page(
                _L14JsPage(),
                class_names=["VT-C4K-PTB05", "VT-JSB16", "VT-CSB03", "VT-CSB04", "VT-CSB05"],
            )
        serialized_l14 = json.dumps(fetched_l14, ensure_ascii=False)
        for pii_name in ("Nguyễn", "Trần", "Cường", "Phạm", "Đức", "Huy", "Lan", "Thành", "Khoa", "Chung"):
            self.assertNotIn(pii_name, serialized_l14)

        ptb_slot = fetched_l14["VT-C4K-PTB05"][0]
        self.assertEqual(ptb_slot["lesson14Ranking"]["fullNameRanks"], [1, 2])
        self.assertEqual(ptb_slot["lesson14Ranking"]["incompleteNameRanks"], [3])
        ptb_eval = evaluate_lms_slot_quality(
            ptb_slot, class_code="VT-C4K-PTB05", khoi="Coding", lesson=14
        )
        self.assertEqual(
            [issue_text(x) for x in ptb_eval["issues"]],
            ["[LMS] Thiếu xếp hạng, chưa đủ họ tên"],
        )

        jsb_slot = fetched_l14["VT-JSB16"][0]
        self.assertEqual(jsb_slot["lesson14Ranking"]["fullNameRanks"], [1, 2, 3])
        self.assertEqual(jsb_slot["lesson14Ranking"]["incompleteNameRanks"], [])
        jsb_eval = evaluate_lms_slot_quality(
            jsb_slot, class_code="VT-JSB16", khoi="Coding", lesson=14
        )
        self.assertEqual(
            [issue_text(x) for x in jsb_eval["issues"]],
            ["[SPCK] Chưa nộp (0/3)"],
        )

        csb_slot = fetched_l14["VT-CSB03"][0]
        self.assertEqual(csb_slot["lesson14Ranking"]["presentRanks"], [1, 2])
        self.assertEqual(csb_slot["lesson14Ranking"]["fullNameRanks"], [1, 2])
        self.assertEqual(csb_slot["lesson14Ranking"]["incompleteNameRanks"], [])
        csb_eval = evaluate_lms_slot_quality(
            csb_slot, class_code="VT-CSB03", khoi="Coding", lesson=14
        )
        self.assertEqual(
            [issue_text(x) for x in csb_eval["issues"]],
            ["[LMS] Thiếu xếp hạng"],
        )

        csb4_slot = fetched_l14["VT-CSB04"][0]
        self.assertEqual(csb4_slot["lesson14Ranking"]["presentRanks"], [1, 2])
        self.assertEqual(csb4_slot["lesson14Ranking"]["fullNameRanks"], [1, 2])
        self.assertEqual(csb4_slot["lesson14Ranking"]["incompleteNameRanks"], [])
        csb4_eval = evaluate_lms_slot_quality(
            csb4_slot, class_code="VT-CSB04", khoi="Coding", lesson=14
        )
        self.assertEqual(
            [issue_text(x) for x in csb4_eval["issues"]],
            ["[LMS] Thiếu xếp hạng"],
        )

        csb5_slot = fetched_l14["VT-CSB05"][0]
        self.assertEqual(csb5_slot["lesson14Ranking"]["fullNameRanks"], [1, 2, 3])
        self.assertEqual(csb5_slot["lesson14Ranking"]["incompleteNameRanks"], [])
        csb5_eval = evaluate_lms_slot_quality(
            csb5_slot, class_code="VT-CSB05", khoi="Coding", lesson=14
        )
        self.assertEqual(
            [issue_text(x) for x in csb5_eval["issues"]],
            [],
        )


if __name__ == "__main__":
    unittest.main()
