import unittest

from playwright.async_api import Error as PlaywrightError

from googleform_autosubmit.browser_launcher import launch_chromium_with_fallback


class FakeBrowserType:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def launch(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def missing_browser(message="Executable doesn't exist at expected path"):
    return PlaywrightError(message)


class BrowserLauncherTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_bundled_chromium_when_available(self):
        browser = object()
        browser_type = FakeBrowserType([browser])

        result = await launch_chromium_with_fallback(browser_type, headless=True)

        self.assertIs(result, browser)
        self.assertEqual(browser_type.calls, [{"headless": True}])

    async def test_falls_back_to_chrome_then_edge_only_for_missing_browser(self):
        edge = object()
        browser_type = FakeBrowserType(
            [
                missing_browser(),
                missing_browser("Chromium distribution 'chrome' is not found"),
                edge,
            ]
        )

        result = await launch_chromium_with_fallback(browser_type, headless=False)

        self.assertIs(result, edge)
        self.assertEqual(
            browser_type.calls,
            [
                {"headless": False},
                {"headless": False, "channel": "chrome"},
                {"headless": False, "channel": "msedge"},
            ],
        )

    async def test_non_missing_browser_error_is_not_hidden_by_fallback(self):
        browser_type = FakeBrowserType([PlaywrightError("browser crashed")])

        with self.assertRaisesRegex(PlaywrightError, "browser crashed"):
            await launch_chromium_with_fallback(browser_type, headless=True)

        self.assertEqual(browser_type.calls, [{"headless": True}])

    async def test_all_missing_browsers_return_actionable_error(self):
        browser_type = FakeBrowserType(
            [
                missing_browser(),
                missing_browser("Chromium distribution 'chrome' is not found"),
                missing_browser("Chromium distribution 'msedge' is not found"),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "Chromium, Chrome hoặc Edge"):
            await launch_chromium_with_fallback(browser_type, headless=True)


if __name__ == "__main__":
    unittest.main()
