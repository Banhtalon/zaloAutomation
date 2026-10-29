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
            self.assertEqual(res["issue"], "[LMS] Chưa ghi nội dung bài học")

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
        self.assertTrue(
            is_checkpoint_session("VT-UNKNOWN-01", "", 6, slot_data=rubric_slot)
        )

    def test_regular_session_comments_catch_shallow_and_copy_paste_without_pii(
        self,
    ) -> None:
        # Synthetic fixture modeled on VT-C4K-PTB16 Lesson 1 (shallow 2-sentence generic comment + copy-paste)
        shallow_body = (
            "Con đi học đúng giờ, có thái độ học tập nghiêm túc và tập trung lắng nghe giáo viên giảng bài. "
            "Con cần tiếp tục duy trì tinh thần học tập tích cực để tiếp thu kiến thức tốt hơn trong các buổi học tiếp theo."
        )
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
        self.assertIn(
            "[LMS] Nhận xét HS sơ sài, thiếu đủ 3 ý (6/6 HS)",
            res["issues"],
        )
        self.assertIn(
            "[LMS] Nhận xét HS copy-paste trùng lặp (6/6 HS)",
            res["issues"],
        )
        joined_issues = " ; ".join(res["issues"])
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
        cp_bad = evaluate_lms_student_comments(
            incomplete_cp, is_checkpoint=True, lesson=8
        )
        self.assertEqual(cp_bad["attended_count"], 3)
        self.assertEqual(cp_bad["failed_count"], 3)
        self.assertEqual(
            cp_bad["issues"],
            ["[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực (3/3 HS)"],
        )

    def test_evaluate_zalo_content_issues_detects_wrong_date_lesson_and_title(
        self,
    ) -> None:
        item = {
            "scan_target": {
                "class_code": "VT-C4K-PTI11",
                "lesson": "5",
                "class_date_iso": "2026-10-25",
            },
            "evidence": {
                "date_context": {
                    "target_lesson": "5",
                    "content_lesson": 2,
                    "target_match_note": "Đã báo cáo nhưng ghi sai ngày 18/10; đã ghép theo BCNB W43/2026, ngày 25/10, buổi 5.",
                    "reported_lesson_title": "Thiết kế giao diện trang web bằng HTML và CSS",
                }
            },
        }
        issues = evaluate_zalo_content_issues(
            item,
            lms_summary_text="Buổi 5: Cấu trúc dữ liệu danh sách (List) và vòng lặp For trong Python",
        )
        self.assertEqual(len(issues), 3)
        self.assertIn("ghi sai ngày 18/10", issues[0])
        self.assertIn(
            "[Zalo] Ghi sai số bài học trong nội dung (ghi Bài 2, lịch là Buổi 5)",
            issues,
        )
        self.assertIn(
            "[Zalo] Tên bài học trên Zalo không khớp với nội dung bài trên LMS",
            issues,
        )

        # Matching topic keywords should not trigger title mismatch
        matching_item = {
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

    def test_inspect_and_attach_quality_issues_populates_scan_results(self) -> None:
        scan_results = [
            {
                "group": "MindX - VT-C4K-PTB16",
                "khoi": "Coding",
                "scan_target": {
                    "class_code": "VT-C4K-PTB16",
                    "lesson": "1",
                    "khoi": "Coding",
                },
                "evidence": {
                    "date_context": {
                        "target_class_code": "VT-C4K-PTB16",
                        "target_lesson": "1",
                    }
                },
            }
        ]
        slots_by_class = {
            "VT-C4K-PTB16": [
                {
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
        self.assertEqual(
            scan_results[0]["lms_quality_issues"],
            [
                "[LMS] Chưa ghi nội dung bài học",
                "[LMS] Nhận xét HS sơ sài, thiếu đủ 3 ý (1/1 HS)",
            ],
        )

        # Re-evaluating after teacher fixes LMS content must clear stale issues
        fixed_slots_by_class = {
            "VT-C4K-PTB16": [
                {
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
        res_dup = evaluate_lms_student_comments(
            three_students, is_checkpoint=False, lesson=2
        )
        self.assertEqual(res_dup["duplicate_count"], 2)
        self.assertIn(
            "[LMS] Nhận xét HS copy-paste trùng lặp (2/3 HS)",
            res_dup["issues"],
        )

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
            any("vượt giới hạn kiểm tra" in issue for issue in res_overflow["issues"])
        )

        # Fetch error slot must flag manual check instead of returning issues=[]
        err_eval = evaluate_lms_slot_quality(
            {"fetch_error": True},
            class_code="VT-C4K-PTB16",
            khoi="Coding",
            lesson=1,
        )
        self.assertEqual(len(err_eval["issues"]), 1)
        self.assertIn("Không tải được đầy đủ dữ liệu", err_eval["issues"][0])

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
                            {"name": "csrf_token", "value": "teaching_token_should_be_ignored_1234567890"}
                        ],
                    },
                    {
                        "origin": "https://lms.mindx.edu.vn",
                        "localStorage": [
                            {"name": "id_token", "value": "valid_lms_jwt_token_12345678901234567890"}
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
        exempt = evaluate_lms_slot_quality(
            {"summary": "", "studentAttendance": []},
            class_code="VT-C4K-PTB16",
            khoi="Coding",
            lesson=14,
        )
        self.assertTrue(exempt["is_exempt"])
        self.assertEqual(exempt["issues"], [])

    async def test_missing_data_61st_class_21st_slot_and_duplicate_class_flagged(
        self,
    ) -> None:
        # 1. Missing studentAttendance list or empty attendance list flags manual check
        res_none_att = evaluate_lms_student_comments(
            None, is_checkpoint=False, lesson=1
        )
        self.assertEqual(len(res_none_att["issues"]), 1)
        self.assertIn("Chưa kiểm tra được danh sách học viên", res_none_att["issues"][0])

        res_empty_att = evaluate_lms_student_comments(
            [], is_checkpoint=False, lesson=1
        )
        self.assertEqual(len(res_empty_att["issues"]), 1)
        self.assertIn("Chưa kiểm tra được danh sách học viên", res_empty_att["issues"][0])

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
            slots = await fetch_lms_class_slots_via_page(
                fake_page, class_names=codes
            )
        item_61 = {"scan_target": {"class_code": codes[60], "lesson": "1"}}
        inspect_and_attach_quality_issues([item_61], lms_slots_by_class=slots)
        self.assertTrue(item_61.get("lms_quality_issues"))
        self.assertIn(
            "Vượt giới hạn số lớp kiểm tra LMS",
            item_61["lms_quality_issues"][0],
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
        self.assertIn(
            "Buổi 21 vượt giới hạn kiểm tra LMS",
            item_21["lms_quality_issues"][0],
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
                inline_js = (
                    f"const {{ script, args }} = {payload_json};\n"
                    + runner_js.replace(
                        "const fs = require('fs');\n        const { script, args } = JSON.parse(fs.readFileSync(0, 'utf8'));",
                        "",
                    )
                )
                proc = subprocess.run(
                    ["node", "-e", inline_js],
                    text=True,
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
            ["[LMS] Phát hiện trùng mã lớp trên LMS, cần kiểm tra thủ công"],
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
                return_value={
                    f"SYNTH-JSB{week}": [{"fetch_error": True}]
                    for week in (43, 44)
                }
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
                            "summary": "",
                            "studentAttendance": [
                                {"status": "ATTENDED", "comment": ""}
                            ],
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
                job_repo=SimpleNamespace(
                    ensure_not_cancelled=AsyncMock(), update_job=AsyncMock()
                ),
                audit_repo=object(),
                evidence_repo=SimpleNamespace(register_scan_results=AsyncMock()),
            )
        self.assertEqual(batch_gw.fetch_class_slots.await_count, 1)
        self.assertEqual(
            len(batch_res["scan_results"][0].get("lms_quality_issues", [])), 2
        )
        self.assertEqual(
            len(repo.snapshots[1].get("lms_quality_issues", [])), 2
        )

    def test_stage4_4_email_table_alignment_truncation_and_csv_contract(self) -> None:
        import csv
        import io
        from urllib.parse import unquote
        from repositories.report_repository import (
            CSV_HEADERS,
            is_failed_weekly_summary_row,
        )
        from services.report_email_service import (
            _display_width,
            build_failed_classes_email_package,
        )

        ten_rows = [
            {
                "class_code": f"VT-C4K-PTB{idx:02d}",
                "teacher": f"Giảng viên {idx}",
                "lesson": str((idx % 9) + 1),
                "class_date": "24/10/2026",
                "zalo_status": "✅ Đã nộp – Có minh chứng" if idx % 2 == 0 else "❌ Chưa tìm thấy báo cáo",
                "lms_status": "✅ Đã nộp LMS",
                "evaluation_status": (
                    "⚠️ Đã nộp nhận xét nhưng còn lỗi chất lượng nội dung"
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

        pkg10 = build_failed_classes_email_package(
            week=43,
            year=2026,
            failed_rows=ten_rows,
            total_rows_count=47,
            selected_job_id=397,
            recipients=["quangda@mindx.net.vn"],
        )
        self.assertEqual(len(CSV_HEADERS), 9)
        parsed_csv = list(csv.reader(io.StringIO(pkg10["csv_text"].lstrip("\ufeff"))))
        self.assertEqual(parsed_csv[0], list(CSV_HEADERS))
        self.assertEqual(len(parsed_csv), 11)

        # Every line of the Unicode table must have the exact same display width
        table_lines = pkg10["text_table"].splitlines()
        line_widths = {_display_width(line) for line in table_lines}
        self.assertEqual(len(line_widths), 1)

        # All 10 rows and 25 rows fit inside Gmail compose URL without truncation
        self.assertFalse(pkg10["gmail_body_truncated"])
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
        decoded_url25 = unquote(pkg25["gmail_compose_url"])
        for idx in range(1, 26):
            self.assertIn(f"VT-C4K-PTB{idx:02d}", decoded_url25)

        # Very large list (>6500 chars even in ultra-compact mode) sets gmail_body_truncated=True and appends explicit notice
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
        self.assertIn("Ctrl+V", unquote(pkg_large["gmail_compose_url"]))

    async def test_hotfix_hostname_validation_temp_page_cleanup_and_rubric_regressions(self) -> None:
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from googleform_autosubmit.config import Config

        # 1. Positive-only comment with "cần tiếp tục duy trì / phát huy" (no improvement) must be flagged as shallow
        positive_only = (
            "NHẬN XÉT BUỔI HỌC SỐ 1: Học viên A (VT-C4K-PTB16)\n"
            "Con đi học đúng giờ, có thái độ học tập nghiêm túc và tập trung lắng nghe giáo viên giảng bài. "
            "Con cần tiếp tục duy trì và phát huy tinh thần học tập tích cực để tiếp thu kiến thức tốt hơn. "
            "Về nhà con làm bài tập đầy đủ nhé."
        )
        pos_slot = {
            "summary": "Tìm hiểu khái niệm biến và vòng lặp trong Python",
            "studentAttendance": [{"status": "ATTENDED", "comment": positive_only}],
        }
        pos_res = evaluate_lms_slot_quality(
            pos_slot, class_code="VT-C4K-PTB16", khoi="Coding", lesson=1
        )
        self.assertTrue(
            any("sơ sài" in issue for issue in pos_res["issues"]),
            pos_res["issues"],
        )

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
                return {c: [{"summary": "Bài học hợp lệ đầy đủ nội dung", "studentAttendance": []}] for c in arg["classCodes"]}

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
            patch("services.lms_service._login_lms_if_needed", new_callable=AsyncMock) as login_mock,
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
            patch("services.lms_service._login_lms_if_needed", new_callable=AsyncMock) as login_mock,
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


if __name__ == "__main__":
    unittest.main()
