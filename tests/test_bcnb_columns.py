import unittest

from services.bcnb_safety import (
    resolve_class_lesson_columns,
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
