import unittest
from unittest.mock import patch

from services import bcnb_safety


class FakeWeekPage:
    def __init__(self):
        self.evaluate_calls = []

    async def wait_for_selector(self, _selector, *, timeout):
        self.timeout = timeout

    async def evaluate(self, script, args=None):
        self.evaluate_calls.append(script)
        if args is not None:
            return True
        return {"week": 30, "year": 2026}

    async def wait_for_load_state(self, _state, *, timeout):
        self.timeout = timeout


class CurrentWeekPage(FakeWeekPage):
    async def evaluate(self, script, args=None):
        self.evaluate_calls.append(script)
        if args is not None:
            return True
        return {
            "selectedWeek": 33,
            "selectedYear": 2026,
            "currentWeek": 33,
            "currentYear": 2026,
        }


class NoticeLocator:
    def __init__(self, *, count=1, text="Tuần bạn đang xem có thể có buổi học chưa diễn ra. Đã hiểu", confirm_count=1, wait_error=None):
        self._count = count
        self._text = text
        self._confirm_count = confirm_count
        self._wait_error = wait_error
        self.clicked = False

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def inner_text(self):
        return self._text

    def locator(self, _selector):
        return NoticeConfirm(self, self._confirm_count)

    async def wait_for(self, *, state, timeout):
        if self._wait_error:
            raise self._wait_error


class NoticeConfirm:
    def __init__(self, notice, count):
        self.notice = notice
        self._count = count

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def click(self):
        self.notice.clicked = True


class NoticePage:
    def __init__(self, notice):
        self.notice = notice

    def locator(self, _selector):
        return self.notice


class BcnbWeekSelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_current_bcnb_filter_button_label(self):
        page = FakeWeekPage()

        result = await bcnb_safety.select_week_checked(page, 30, 2026)

        self.assertEqual(result, (30, 2026))
        self.assertIn("lọc lịch", page.evaluate_calls[0].lower())
        self.assertIn("xem", page.evaluate_calls[0].lower())

    async def test_last_week_uses_business_clock_not_host_clock(self):
        seen = {}

        async def fake_select(_page, week, year):
            seen.update(week=week, year=year)
            return week, year

        with patch.object(bcnb_safety, "previous_business_week", return_value=(52, 2023)):
            with patch.object(bcnb_safety, "select_week_checked", new=fake_select):
                result = await bcnb_safety.select_last_week_checked(FakeWeekPage())

        self.assertEqual(result, (52, 2023))
        self.assertEqual(seen, {"week": 52, "year": 2023})

    async def test_reads_current_period_from_bcnb_page(self):
        page = CurrentWeekPage()
        result = await bcnb_safety.read_current_week_checked(page)
        self.assertEqual(result, (33, 2026))
        script = page.evaluate_calls[0]
        self.assertIn(".join('\\n')", script)
        self.assertIn("new RegExp(String.raw`", script)

    async def test_current_period_validation_is_fail_closed(self):
        for values, message in (
            ({"currentWeek": None, "currentYear": 2026, "selectedWeek": 33, "selectedYear": 2026}, "không cung cấp"),
            ({"currentWeek": 33, "currentYear": 2026, "selectedWeek": 32, "selectedYear": 2026}, "không đúng"),
            ({"currentWeek": 54, "currentYear": 2026, "selectedWeek": 54, "selectedYear": 2026}, "không hợp lệ"),
        ):
            page = CurrentWeekPage()
            page.evaluate = lambda script, args=None, values=values: _evaluate_current(page, script, args, values)
            with self.assertRaisesRegex(RuntimeError, message):
                await bcnb_safety.read_current_week_checked(page)

    async def test_dismisses_only_known_current_week_notice(self):
        notice = NoticeLocator(wait_error=bcnb_safety.PlaywrightTimeoutError("hidden timeout"))
        self.assertTrue(await bcnb_safety.dismiss_current_week_notice(NoticePage(notice)))
        self.assertTrue(notice.clicked)
        self.assertFalse(
            await bcnb_safety.dismiss_current_week_notice(
                NoticePage(NoticeLocator(text="Một thông báo khác"))
            )
        )
        self.assertFalse(await bcnb_safety.dismiss_current_week_notice(FakeWeekPage()))


async def _evaluate_current(page, script, args, values):
    page.evaluate_calls.append(script)
    return values


if __name__ == "__main__":
    unittest.main()
