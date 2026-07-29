import unittest
from unittest.mock import AsyncMock, patch

from googleform_autosubmit.form_submitter import (
    FormSubmitter,
    SubmissionOutcomeUnknownError,
)


class BrokenPage:
    def locator(self, _selector):
        raise RuntimeError("selector changed")


class FormSubmitterTests(unittest.IsolatedAsyncioTestCase):
    async def test_fill_failure_is_not_swallowed(self):
        submitter = FormSubmitter({})
        with self.assertRaises(RuntimeError):
            await submitter.fill_field(
                BrokenPage(),
                "Tên của em là?",
                {"type": "text", "value": "Student"},
            )

    async def test_unknown_field_type_is_rejected(self):
        class VisibleContainer:
            @property
            def first(self):
                return self

            def filter(self, **_kwargs):
                return self

            async def count(self):
                return 1

            async def is_visible(self):
                return True

            async def scroll_into_view_if_needed(self):
                return None

        class Page:
            def locator(self, _selector):
                return VisibleContainer()

        submitter = FormSubmitter({})
        with self.assertRaises(RuntimeError):
            await submitter.fill_field(
                Page(), "Question", {"type": "unsupported", "value": "x"}
            )

    async def test_user_text_is_passed_as_filter_data_not_selector_syntax(self):
        class Locator:
            def __init__(self):
                self.filter_values = []
                self.filled = []

            @property
            def first(self):
                return self

            def filter(self, *, has_text):
                self.filter_values.append(has_text)
                return self

            async def count(self):
                return 1

            async def is_visible(self):
                return True

            async def scroll_into_view_if_needed(self):
                return None

            def locator(self, _selector):
                return self

            async def fill(self, value):
                self.filled.append(value)

        class Page:
            def __init__(self):
                self.selectors = []
                self.root = Locator()

            def locator(self, selector):
                self.selectors.append(selector)
                return self.root

        question = "Question ' ] //div[@role='button']"
        page = Page()
        await FormSubmitter({}).fill_field(
            page,
            question,
            {"type": "text", "value": "safe value"},
        )
        self.assertEqual(page.selectors, ["div[role='listitem']"])
        self.assertEqual(page.root.filter_values, [question])
        self.assertEqual(page.root.filled, ["safe value"])

    async def test_radio_uses_exact_accessible_name_and_verifies_checked_state(self):
        class Radio:
            def __init__(self):
                self.clicked = False

            async def count(self):
                return 1

            async def scroll_into_view_if_needed(self):
                return None

            async def click(self):
                self.clicked = True

            async def get_attribute(self, name):
                self.assert_name = name
                return "true"

        class Container:
            def __init__(self):
                self.radio = Radio()
                self.role_calls = []

            @property
            def first(self):
                return self

            def filter(self, **_kwargs):
                return self

            async def count(self):
                return 1

            async def is_visible(self):
                return True

            async def scroll_into_view_if_needed(self):
                return None

            def get_by_role(self, role, **kwargs):
                self.role_calls.append((role, kwargs))
                return self.radio

        class Page:
            def __init__(self):
                self.container = Container()

            def locator(self, _selector):
                return self.container

        page = Page()
        await FormSubmitter({}).fill_field(
            page,
            "Mã cơ sở của em là?",
            {"type": "radio", "value": "VT"},
        )

        self.assertEqual(
            page.container.role_calls,
            [("radio", {"name": "VT", "exact": True})],
        )
        self.assertTrue(page.container.radio.clicked)
        self.assertEqual(page.container.radio.assert_name, "aria-checked")

    async def test_radio_waits_for_delayed_checked_state(self):
        class Radio:
            def __init__(self):
                self.click_calls = 0
                self.attribute_values = iter(["false", "false", "true"])
                self.attribute_calls = 0

            async def count(self):
                return 1

            async def scroll_into_view_if_needed(self):
                return None

            async def click(self):
                self.click_calls += 1

            async def get_attribute(self, name):
                self.attribute_calls += 1
                if name != "aria-checked":
                    raise AssertionError(f"Unexpected attribute: {name}")
                return next(self.attribute_values)

        class Container:
            def __init__(self):
                self.radio = Radio()
                self.role_calls = []

            @property
            def first(self):
                return self

            def filter(self, **_kwargs):
                return self

            async def count(self):
                return 1

            async def is_visible(self):
                return True

            async def scroll_into_view_if_needed(self):
                return None

            def get_by_role(self, role, **kwargs):
                self.role_calls.append((role, kwargs))
                return self.radio

        class Page:
            def __init__(self):
                self.container = Container()

            def locator(self, _selector):
                return self.container

        page = Page()
        with patch(
            "asyncio.sleep",
            new=AsyncMock(),
        ) as mocked_sleep:
            await FormSubmitter({}).fill_field(
                page,
                "Campus question",
                {"type": "radio", "value": "VT"},
            )

        self.assertEqual(
            page.container.role_calls,
            [("radio", {"name": "VT", "exact": True})],
        )
        self.assertEqual(page.container.radio.click_calls, 1)
        self.assertEqual(page.container.radio.attribute_calls, 3)
        self.assertEqual(mocked_sleep.await_count, 2)

    async def test_radio_that_does_not_become_checked_is_rejected(self):
        class Radio:
            def __init__(self):
                self.click_calls = 0

            async def count(self):
                return 1

            async def scroll_into_view_if_needed(self):
                return None

            async def click(self):
                self.click_calls += 1

            async def get_attribute(self, _name):
                return "false"

        class Container:
            def __init__(self):
                self.radio = Radio()

            @property
            def first(self):
                return self

            def filter(self, **_kwargs):
                return self

            async def count(self):
                return 1

            async def is_visible(self):
                return True

            async def scroll_into_view_if_needed(self):
                return None

            def get_by_role(self, _role, **_kwargs):
                return self.radio

        class Page:
            def __init__(self):
                self.container = Container()

            def locator(self, _selector):
                return self.container

        page = Page()
        with patch(
            "asyncio.sleep",
            new=AsyncMock(),
        ):
            with self.assertRaisesRegex(RuntimeError, "Không thể điền câu hỏi"):
                await FormSubmitter({}).fill_field(
                    page,
                    "Mã cơ sở của em là?",
                    {"type": "radio", "value": "VT"},
                )

        self.assertEqual(
            page.container.radio.click_calls,
            2,
            "Radio retry must be bounded to one retry after the initial click",
        )

    async def test_missing_post_submit_confirmation_has_unknown_outcome_type(self):
        class Locator:
            @property
            def first(self):
                return self

            def filter(self, **_kwargs):
                return self

            async def count(self):
                return 1

            async def is_visible(self):
                return True

            async def scroll_into_view_if_needed(self, **_kwargs):
                return None

            async def click(self, **_kwargs):
                return None

            async def wait_for(self, **_kwargs):
                raise RuntimeError("confirmation missing")

        class Page:
            def __init__(self):
                self.wait_for_url_calls = 0

            async def goto(self, *_args, **_kwargs):
                return None

            def locator(self, _selector):
                return Locator()

            async def wait_for_url(self, *_args, **_kwargs):
                self.wait_for_url_calls += 1
                raise RuntimeError("url did not change")

            def get_by_text(self, _pattern):
                return Locator()

        page = Page()
        submitter = FormSubmitter({}, execution_mode="production")
        submitter.check_captcha = AsyncMock()
        submitter.fill_field = AsyncMock()
        with self.assertRaises(SubmissionOutcomeUnknownError):
            await submitter._execute_submission(page, "Student", "VT-CODE")
        self.assertEqual(page.wait_for_url_calls, 0)

    async def test_confirmation_dom_is_required_and_url_is_not_consulted(self):
        class Locator:
            def __init__(self, *, confirmation=False):
                self.confirmation = confirmation
                self.click_calls = []

            @property
            def first(self):
                return self

            def filter(self, **_kwargs):
                return self

            async def count(self):
                return 1

            async def is_visible(self):
                return True

            async def scroll_into_view_if_needed(self, **_kwargs):
                return None

            async def click(self, **kwargs):
                self.click_calls.append(kwargs)

            async def wait_for(self, **_kwargs):
                if not self.confirmation:
                    raise RuntimeError("not a confirmation")

        class Page:
            def __init__(self):
                self.submit = Locator()
                self.confirmation = Locator(confirmation=True)

            async def goto(self, *_args, **_kwargs):
                return None

            def locator(self, _selector):
                return self.submit

            async def wait_for_url(self, *_args, **_kwargs):
                raise AssertionError("URL must not be used as submission proof")

            def get_by_text(self, _pattern):
                return self.confirmation

        page = Page()
        submitter = FormSubmitter({}, execution_mode="production")
        submitter.check_captcha = AsyncMock()
        submitter.fill_field = AsyncMock()

        await submitter._execute_submission(page, "Student", "VT-CODE")

        self.assertEqual(len(page.submit.click_calls), 2)
        self.assertTrue(page.submit.click_calls[0]["trial"])
        self.assertNotIn("trial", page.submit.click_calls[1])

    async def test_dry_run_never_looks_for_or_clicks_submit(self):
        class Page:
            async def goto(self, *_args, **_kwargs):
                return None

            def locator(self, _selector):
                raise AssertionError("dry-run must not locate the submit button")

        submitter = FormSubmitter({}, execution_mode="dry-run")
        submitter.check_captcha = AsyncMock()
        submitter.fill_field = AsyncMock()

        await submitter._execute_submission(Page(), "Student", "VT-CODE")


if __name__ == "__main__":
    unittest.main()
