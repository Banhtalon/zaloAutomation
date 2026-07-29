import unittest

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


class BcnbWeekSelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_current_bcnb_filter_button_label(self):
        page = FakeWeekPage()

        result = await bcnb_safety.select_week_checked(page, 30, 2026)

        self.assertEqual(result, (30, 2026))
        self.assertIn("lọc lịch", page.evaluate_calls[0].lower())
        self.assertIn("xem", page.evaluate_calls[0].lower())


if __name__ == "__main__":
    unittest.main()
