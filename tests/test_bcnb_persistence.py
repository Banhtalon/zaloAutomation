import unittest
from unittest.mock import AsyncMock, patch

from services import bcnb_safety


class FakeCheckboxCell:
    def __init__(self, checkbox):
        self.checkbox = checkbox

    def locator(self, _selector):
        return type("FirstLocator", (), {"first": self.checkbox})()


class FakeTextCell:
    def __init__(self, value):
        self.value = value

    async def inner_text(self):
        return self.value


class FakeRow:
    def __init__(self, cells):
        self.cells = cells

    def locator(self, _selector):
        cells = self.cells

        class CellList:
            async def all(self):
                return cells

        return CellList()


class FakePage:
    def __init__(self, rows):
        self.rows = rows
        self.reload = AsyncMock()

    def locator(self, _selector):
        rows = self.rows

        class RowList:
            async def all(self):
                return rows

        return RowList()


def report_row(class_code, lesson, checked):
    checkbox = AsyncMock()
    checkbox.count.return_value = 1
    checkbox.is_checked.return_value = checked
    return FakeRow(
        [FakeTextCell(class_code), FakeTextCell(str(lesson)), FakeCheckboxCell(checkbox)]
    )


class BcnbPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def read_state(self, page):
        with (
            patch.object(
                bcnb_safety,
                "select_week_checked",
                AsyncMock(return_value=(27, 2026)),
            ),
            patch.object(
                bcnb_safety,
                "resolve_report_columns",
                AsyncMock(return_value=(0, 1, 2)),
            ),
        ):
            return await bcnb_safety.verify_checkbox_persisted(
                page,
                class_code="VT-JSB14",
                column_kind="report",
                week=27,
                year=2026,
                lesson="14",
            )

    async def test_server_readback_requires_hard_reload(self):
        page = FakePage([report_row("VT-JSB14", "14", True)])
        self.assertTrue(await self.read_state(page))
        page.reload.assert_awaited_once_with(wait_until="domcontentloaded")

    async def test_optimistic_dom_state_is_rejected_when_reload_is_unchecked(self):
        page = FakePage([report_row("VT-JSB14", "14", False)])
        self.assertFalse(await self.read_state(page))

    async def test_duplicate_class_and_lesson_fail_closed(self):
        page = FakePage(
            [
                report_row("VT-JSB14", "14", True),
                report_row("VT-JSB14", "14", True),
            ]
        )
        with self.assertRaises(RuntimeError):
            await self.read_state(page)

    async def test_week_readback_mismatch_fails_closed(self):
        page = FakePage([report_row("VT-JSB14", "14", True)])
        with (
            patch.object(
                bcnb_safety,
                "select_week_checked",
                AsyncMock(return_value=(26, 2026)),
            ),
            patch.object(
                bcnb_safety,
                "resolve_report_columns",
                AsyncMock(return_value=(0, 1, 2)),
            ),
        ):
            with self.assertRaises(RuntimeError):
                await bcnb_safety.verify_checkbox_persisted(
                    page,
                    class_code="VT-JSB14",
                    column_kind="report",
                    week=27,
                    year=2026,
                    lesson="14",
                )


if __name__ == "__main__":
    unittest.main()
