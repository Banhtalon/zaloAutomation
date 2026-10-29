import json
import unittest

from unittest.mock import AsyncMock, patch

from services.bcnb_safety import (
    clean_bcnb_note_text,
    extract_bcnb_extended_row_fields,
    resolve_class_lesson_columns,
    resolve_extended_bcnb_columns,
    resolve_instructor_column,
    resolve_lms_columns,
    resolve_report_columns,
    resolve_schedule_columns,
    verify_checker_note_persisted,
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

    async def test_extended_bcnb_columns_and_row_extraction(self):
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
        ext = await resolve_extended_bcnb_columns(page)
        self.assertEqual(
            ext,
            {
                "block_index": 6,
                "zalo_checkbox_index": 8,
                "lms_deadline_index": 9,
                "lms_checkbox_index": 10,
                "checker_note_index": 11,
                "instructor_note_index": 12,
                "evaluation_status_index": 13,
            },
        )
        self.assertEqual(clean_bcnb_note_text("+ Ghi chú QC"), "")
        self.assertEqual(clean_bcnb_note_text("+ GV nhập giải trình"), "")
        self.assertEqual(
            clean_bcnb_note_text('"Chưa nội dung bài học"'),
            "Chưa nội dung bài học",
        )

        class _FakeCheckbox:
            def __init__(self, checked: bool):
                self._checked = checked

            async def count(self) -> int:
                return 1

            async def is_checked(self) -> bool:
                return self._checked

        class _FakeCellWithExtras(FakeCell):
            def __init__(self, text: str, *, checked: bool | None = None, note_attr: str | None = None):
                super().__init__(text)
                self._checked = checked
                self._note_attr = note_attr

            def locator(self, selector: str):
                cell = self

                class _Loc:
                    @property
                    def first(self_inner):
                        return self_inner

                    async def count(self_inner) -> int:
                        if "checkbox" in selector and cell._checked is not None:
                            return 1
                        if "note-cell" in selector and cell._note_attr is not None:
                            return 1
                        return 0

                    async def is_checked(self_inner) -> bool:
                        return bool(cell._checked)

                    async def get_attribute(self_inner, name: str):
                        if name == "data-current-note":
                            return cell._note_attr
                        return None

                return _Loc()

        class _FakeRow:
            async def get_attribute(self, name: str):
                if name == "data-session-id":
                    return "6274"
                if name == "data-block":
                    return "Coding"
                return None

        cells = [
            _FakeCellWithExtras("1"),
            _FakeCellWithExtras("VT-C4K-PTB16"),
            _FakeCellWithExtras("1"),
            _FakeCellWithExtras("24/10/2026"),
            _FakeCellWithExtras("Đỗ Thị Thùy Trang"),
            _FakeCellWithExtras("Không có"),
            _FakeCellWithExtras("Coding"),
            _FakeCellWithExtras("20:00 26/10/2026"),
            _FakeCellWithExtras("", checked=False),
            _FakeCellWithExtras("20:00 26/10/2026"),
            _FakeCellWithExtras("", checked=True),
            _FakeCellWithExtras("+ Ghi chú QC", note_attr='""'),
            _FakeCellWithExtras("Chưa nội dung bài học", note_attr='"Chưa nội dung bài học"'),
            _FakeCellWithExtras("⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo"),
            _FakeCellWithExtras(""),
        ]
        fields = await extract_bcnb_extended_row_fields(_FakeRow(), cells, ext)
        self.assertEqual(fields["bcnb_session_id"], "6274")
        self.assertEqual(fields["khoi"], "Coding")
        self.assertIs(fields["nx_zalo_checked"], False)
        self.assertIs(fields["nx_lms_checked"], True)
        self.assertEqual(fields["lms_deadline"], "20:00 26/10/2026")
        self.assertEqual(fields["qc_issue_note"], "")
        self.assertEqual(fields["instructor_note"], "Chưa nội dung bài học")
        self.assertEqual(
            fields["evaluation_status"],
            "⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo",
        )

        class _UnreadableCell:
            async def inner_text(self) -> str:
                raise RuntimeError("DOM detached while reading note cell")

        broken_cells = list(cells)
        broken_cells[11] = _UnreadableCell()  # type: ignore[call-overload]
        fields_unreadable_inc = await extract_bcnb_extended_row_fields(
            _FakeRow(), broken_cells, ext, include_empty=True
        )
        self.assertIsNone(fields_unreadable_inc["qc_issue_note"])
        self.assertEqual(fields_unreadable_inc["instructor_note"], "Chưa nội dung bài học")
        fields_unreadable_exc = await extract_bcnb_extended_row_fields(
            _FakeRow(), broken_cells, ext, include_empty=False
        )
        self.assertNotIn("qc_issue_note", fields_unreadable_exc)
        self.assertEqual(fields_unreadable_exc["instructor_note"], "Chưa nội dung bài học")

        # Verify verify_checker_note_persisted readback and unreadable cell fail-closed
        expected_note = "GV báo bận | [Auto QC: [LMS] Chưa ghi nội dung bài học]"
        persisted_cells = list(cells)
        persisted_cells[11] = _FakeCellWithExtras(
            expected_note, note_attr=json.dumps(expected_note, ensure_ascii=False)
        )

        class _DataRow(_FakeRow):
            def __init__(self, row_cells):
                self._cells = row_cells

            def locator(self, _sel):
                row_self = self

                class _RowLoc:
                    async def all(self_inner):
                        return row_self._cells

                return _RowLoc()

        class _ReloadPage:
            def __init__(self, headers, data_rows):
                self._header = FakeHeaderRow(headers)
                self._rows = [self._header, *data_rows]
                self.reload = AsyncMock()

            def locator(self, selector):
                if selector != "table tr":
                    raise AssertionError(f"unexpected selector: {selector}")
                page_self = self

                class _TableLoc:
                    first = page_self._header

                    async def all(self_inner):
                        return page_self._rows

                return _TableLoc()

        reload_page = _ReloadPage(page.headers, [_DataRow(persisted_cells)])
        with patch(
            "services.bcnb_safety.select_week_checked",
            AsyncMock(return_value=(43, 2026)),
        ):
            ok, obs = await verify_checker_note_persisted(
                reload_page,
                class_code="VT-C4K-PTB16",
                week=43,
                year=2026,
                expected_note=expected_note,
                lesson="1",
                class_date_iso="2026-10-24",
                session_id="6274",
            )
            self.assertTrue(ok)
            self.assertEqual(obs, expected_note)

            unreadable_page = _ReloadPage(page.headers, [_DataRow(broken_cells)])
            with self.assertRaisesRegex(RuntimeError, "không đọc được ô Mô tả vấn đề"):
                await verify_checker_note_persisted(
                    unreadable_page,
                    class_code="VT-C4K-PTB16",
                    week=43,
                    year=2026,
                    expected_note="",
                    lesson="1",
                    class_date_iso="2026-10-24",
                    session_id="6274",
                )



if __name__ == "__main__":
    unittest.main()
