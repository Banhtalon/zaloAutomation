import unittest

from services.bcnb_safety import (
    resolve_class_lesson_columns,
    resolve_instructor_column,
    resolve_lms_columns,
    resolve_report_columns,
    resolve_schedule_columns,
)


class FakeCell:
    def __init__(self, text):
        self.text = text

    async def inner_text(self):
        return self.text


class FakeHeaderRow:
    def __init__(self, headers):
        self.cells = [FakeCell(text) for text in headers]

    def locator(self, _selector):
        return self

    async def all(self):
        return self.cells


class FakeTableRows:
    def __init__(self, headers):
        self.first = FakeHeaderRow(headers)


class FakePage:
    def __init__(self, headers):
        self.headers = headers

    def locator(self, selector):
        if selector != "table tr":
            raise AssertionError(f"unexpected selector: {selector}")
        return FakeTableRows(self.headers)


class BcnbColumnResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_bcnb_headers_select_comment_not_deadline_columns(self):
        page = FakePage(
            [
                "Mã lớp",
                "Buổi số",
                "Ngày học",
                "Giảng viên chính",
                "Giảng viên trợ giảng",
                "Khối",
                "Deadline Zalo",
                "Nhận xét Zalo",
                "Deadline LMS",
                "Nhận xét LMS",
                "Tình trạng kiểm tra",
                "Hành động",
            ]
        )

        self.assertEqual(await resolve_report_columns(page), (0, 1, 7))
        self.assertEqual(await resolve_schedule_columns(page), (0, 1, 2, 6))
        self.assertEqual(await resolve_lms_columns(page), (0, 9))
        self.assertEqual(await resolve_class_lesson_columns(page), (0, 1))
        self.assertEqual(await resolve_instructor_column(page), 3)

    async def test_bcnb_2026_10_abbreviated_nx_headers(self):
        page = FakePage(
            [
                "STT",
                "Mã lớp",
                "Buổi",
                "Ngày học",
                "Giảng viên chính",
                "Trợ giảng",
                "Khối",
                "Deadline Zalo",
                "NX Zalo",
                "Deadline LMS",
                "NX LMS",
                "Mô tả vấn đề (TE/TF/QC)",
                "Ghi chú Giảng viên",
                "Tình trạng đánh giá",
                "Thao tác",
            ]
        )

        self.assertEqual(await resolve_report_columns(page), (1, 2, 8))
        self.assertEqual(await resolve_schedule_columns(page), (1, 2, 3, 7))
        self.assertEqual(await resolve_lms_columns(page), (1, 10))
        self.assertEqual(await resolve_class_lesson_columns(page), (1, 2))
        self.assertEqual(await resolve_instructor_column(page), 4)

    async def test_instructor_column_ignores_assistant_and_note_headers(self):
        page_with_fallback = FakePage(["STT", "Mã lớp", "Buổi", "Ngày học", "Giảng viên", "Trợ giảng", "Ghi chú Giảng viên"])
        self.assertEqual(await resolve_instructor_column(page_with_fallback), 4)

        page_missing = FakePage(
            [
                "STT",
                "Mã lớp",
                "Buổi",
                "Ngày học",
                "Trợ giảng",
                "Ghi chú Giảng viên",
                "Ghi chú Giảng viên chính",
            ]
        )
        self.assertIsNone(await resolve_instructor_column(page_missing))

        page_duplicate = FakePage(["Mã lớp", "Giảng viên chính", "Giảng viên chính"])
        self.assertIsNone(await resolve_instructor_column(page_duplicate))

    async def test_nx_prefix_does_not_match_unrelated_headers(self):
        page = FakePage(["Mã lớp", "Buổi", "NX Zalo cũ (tham khảo)", "Deadline LMS"])

        with self.assertRaisesRegex(RuntimeError, "báo cáo"):
            await resolve_report_columns(page)
        with self.assertRaisesRegex(RuntimeError, "LMS"):
            await resolve_lms_columns(page)

    async def test_legacy_report_and_lms_headers_remain_supported(self):
        page = FakePage(["Mã lớp", "Buổi", "Đã nộp báo cáo", "LMS"])

        self.assertEqual(await resolve_report_columns(page), (0, 1, 2))
        self.assertEqual(await resolve_lms_columns(page), (0, 3))

    async def test_missing_action_column_fails_closed(self):
        page = FakePage(["Mã lớp", "Buổi số", "Deadline Zalo", "Deadline LMS"])

        with self.assertRaisesRegex(RuntimeError, "báo cáo"):
            await resolve_report_columns(page)
        with self.assertRaisesRegex(RuntimeError, "LMS"):
            await resolve_lms_columns(page)


if __name__ == "__main__":
    unittest.main()
