from contextlib import asynccontextmanager
import json
import unittest
from unittest.mock import AsyncMock, patch

from repositories.job_repository import JobCancelledError
from services.external_gateways import ExternalGateways
from services.lms_plan import build_lms_plan
import services.lms_service as service
from services.runtime import WorkerRuntime


class FakeJobRepository:
    def __init__(self, *, cancel_on_check=None):
        self.updates = []
        self.check_count = 0
        self.cancel_on_check = cancel_on_check

    async def update_job(self, job_id, **changes):
        self.updates.append({"job_id": job_id, **changes})

    async def ensure_not_cancelled(self, _job_id):
        self.check_count += 1
        if self.cancel_on_check == self.check_count:
            raise JobCancelledError("Đã hủy theo yêu cầu.")


class FakeLmsRepository:
    def __init__(self):
        self.confirmations = []

    async def save_confirmation(self, *args):
        self.confirmations.append(args)


class FakeLmsOcr:
    def __init__(self, result=None, error=None):
        self.result = result or ["VT-MISSING"]
        self.error = error
        self.paths = []

    async def extract_missing_classes(self, path):
        self.paths.append(path)
        if self.error:
            raise self.error
        return list(self.result)


class FakeBrowserGateway:
    def __init__(self, page):
        self.page = page
        self.enter_count = 0
        self.exit_count = 0
        self.viewports = []

    @asynccontextmanager
    async def persistent_page(self, *, viewport=None):
        self.enter_count += 1
        self.viewports.append(viewport)
        try:
            yield self.page
        finally:
            self.exit_count += 1


class FakeCheckbox:
    pass


class FakeCell:
    def __init__(self, text=""):
        self.text = text
        self.checkbox = FakeCheckbox()

    async def inner_text(self):
        return self.text

    def locator(self, _selector):
        return type("First", (), {"first": self.checkbox})()


class FakeRow:
    def __init__(self, code):
        self.cells = [FakeCell(code), FakeCell()]

    def locator(self, selector):
        if selector != "td":
            raise AssertionError(selector)
        return type("Cells", (), {"all": AsyncMock(return_value=self.cells)})()


class FakeAutomationPage:
    def __init__(self, codes=()):
        self.rows = [FakeRow(code) for code in codes]

    def locator(self, selector):
        if selector != "table tr":
            raise AssertionError(selector)
        return type("Rows", (), {"all": AsyncMock(return_value=self.rows)})()


class FakeLoginLocator:
    def __init__(self, *, count=1, visible=True, error=None):
        self._count = count
        self._visible = visible
        self.error = error
        self.values = []
        self.click_count = 0

    @property
    def first(self):
        return self

    async def count(self):
        if self.error:
            raise self.error
        return self._count

    async def is_visible(self):
        return self._visible

    async def fill(self, value):
        self.values.append(value)

    async def click(self):
        self.click_count += 1


class FakeLoginPage:
    def __init__(self, username):
        self.username = username
        self.password = FakeLoginLocator()
        self.submit = FakeLoginLocator()
        self.goto_urls = []
        self.load_states = []

    async def goto(self, url):
        self.goto_urls.append(url)

    def locator(self, selector):
        return {
            'input[name="username"]': self.username,
            'input[name="password"]': self.password,
            'button[type="submit"]': self.submit,
        }[selector]

    async def wait_for_load_state(self, state):
        self.load_states.append(state)


class FakeDialog:
    def __init__(self, dialog_type):
        self.type = dialog_type
        self.accept_count = 0
        self.dismiss_count = 0

    async def accept(self):
        self.accept_count += 1

    async def dismiss(self):
        self.dismiss_count += 1


class LmsServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_job_repo = service.job_repo
        self.original_lms_repo = service.lms_repo
        self.original_audit_repo = service.audit_repo
        self.job_repo = FakeJobRepository()
        self.lms_repo = FakeLmsRepository()
        service.job_repo = self.job_repo
        service.lms_repo = self.lms_repo
        service.audit_repo = object()

    def tearDown(self):
        service.job_repo = self.original_job_repo
        service.lms_repo = self.original_lms_repo
        service.audit_repo = self.original_audit_repo

    def runtime(self, *, page=None, ocr=None):
        browser = FakeBrowserGateway(page or FakeAutomationPage())
        external = ExternalGateways(
            browser=browser,
            lms_ocr=ocr or FakeLmsOcr(),
            student_ocr_factory=lambda: object(),
        )
        return WorkerRuntime(external=external), browser

    async def test_login_skips_absent_form_but_propagates_selector_errors(self):
        absent = FakeLoginPage(FakeLoginLocator(count=0))
        with (
            patch.object(service, "select_last_week_checked", AsyncMock(return_value=(28, 2026))),
            patch.object(service.asyncio, "sleep", AsyncMock()),
        ):
            self.assertEqual(await service._login_and_select_last_week(absent), (28, 2026))
        self.assertEqual(absent.password.values, [])

        broken = FakeLoginPage(
            FakeLoginLocator(error=RuntimeError("selector failure"))
        )
        with self.assertRaisesRegex(RuntimeError, "selector failure"):
            await service._login_and_select_last_week(broken)

    async def test_login_fills_visible_form_and_wraps_week_failure(self):
        page = FakeLoginPage(FakeLoginLocator())
        with (
            patch.object(service, "select_last_week_checked", AsyncMock(return_value=(28, 2026))),
            patch.object(service.asyncio, "sleep", AsyncMock()),
        ):
            result = await service._login_and_select_last_week(page)
        self.assertEqual(result, (28, 2026))
        self.assertTrue(page.username.values)
        self.assertTrue(page.password.values)
        self.assertEqual(page.submit.click_count, 1)

        with patch.object(
            service,
            "select_last_week_checked",
            AsyncMock(side_effect=RuntimeError("week changed")),
        ):
            with self.assertRaisesRegex(service.LmsSafetyError, "week changed"):
                await service._login_and_select_last_week(
                    FakeLoginPage(FakeLoginLocator(count=0))
                )

    async def test_dialog_policy_accepts_only_alert_and_confirm(self):
        confirm = FakeDialog("confirm")
        prompt = FakeDialog("prompt")
        await service._handle_bcnb_dialog(confirm)
        await service._handle_bcnb_dialog(prompt)
        self.assertEqual((confirm.accept_count, confirm.dismiss_count), (1, 0))
        self.assertEqual((prompt.accept_count, prompt.dismiss_count), (0, 1))

    async def test_analysis_success_stores_confirmation_and_closes_gateway(self):
        page = FakeAutomationPage()
        ocr = FakeLmsOcr(["VT-MISSING"])
        runtime, browser = self.runtime(page=page, ocr=ocr)
        snapshot = [
            {"class_code": "VT-MISSING", "checked": False},
            {"class_code": "VT-TARGET", "checked": False},
        ]
        with patch.object(
            service,
            "_read_bcnb_snapshot",
            AsyncMock(return_value=(page, 28, 2026, snapshot, 0, 1)),
        ):
            await service.run_lms_analysis(10, "lms.png", runtime=runtime)

        self.assertEqual(ocr.paths, ["lms.png"])
        self.assertEqual(browser.enter_count, 1)
        self.assertEqual(browser.exit_count, 1)
        self.assertEqual(len(self.lms_repo.confirmations), 1)
        final = self.job_repo.updates[-1]
        self.assertEqual(final["status"], "SUCCESS")
        result = json.loads(final["results"])
        self.assertEqual(result["classes_to_tick"], ["VT-TARGET"])
        self.assertTrue(result["confirmation_token"])

    async def test_analysis_blocks_drift_and_handles_cancel_or_ocr_error(self):
        page = FakeAutomationPage()
        runtime, _browser = self.runtime(page=page)
        snapshot = [{"class_code": "VT-TARGET", "checked": False}]
        with patch.object(
            service,
            "_read_bcnb_snapshot",
            AsyncMock(return_value=(page, 28, 2026, snapshot, 0, 1)),
        ):
            await service.run_lms_analysis(11, "lms.png", runtime=runtime)
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
        self.assertIn("Dừng an toàn", self.job_repo.updates[-1]["message"])

        self.job_repo = FakeJobRepository(cancel_on_check=1)
        service.job_repo = self.job_repo
        await service.run_lms_analysis(12, "lms.png", runtime=runtime)
        self.assertEqual(self.job_repo.updates[-1]["status"], "CANCELLED")

        self.job_repo = FakeJobRepository()
        service.job_repo = self.job_repo
        failing_runtime, _ = self.runtime(
            ocr=FakeLmsOcr(error=service.LmsSafetyError("bad OCR"))
        )
        await service.run_lms_analysis(13, "lms.png", runtime=failing_runtime)
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
        self.assertIn("bad OCR", self.job_repo.updates[-1]["message"])

    async def test_automation_dry_run_success_and_plan_drift_failure(self):
        page = FakeAutomationPage(["VT-TARGET"])
        runtime, browser = self.runtime(page=page)
        snapshot = [
            {"class_code": "VT-MISSING", "checked": False},
            {"class_code": "VT-TARGET", "checked": False},
        ]
        plan = build_lms_plan(["VT-MISSING"], snapshot, 28, 2026, True)
        with (
            patch.object(
                service,
                "_read_bcnb_snapshot",
                AsyncMock(return_value=(page, 28, 2026, snapshot, 0, 1)),
            ),
            patch.object(
                service,
                "execute_verified_checkbox_action",
                AsyncMock(return_value="DRY_RUN"),
            ) as action,
        ):
            await service.run_lms_automation(
                20,
                plan,
                plan["plan_digest"],
                runtime=runtime,
            )
        self.assertEqual(action.await_count, 1)
        self.assertEqual(browser.exit_count, 1)
        self.assertEqual(self.job_repo.updates[-1]["status"], "SUCCESS")
        result = json.loads(self.job_repo.updates[-1]["results"])
        self.assertEqual(result["verified_classes"], ["VT-TARGET"])

        changed = [
            {"class_code": "VT-MISSING", "checked": False},
            {"class_code": "VT-TARGET", "checked": True},
        ]
        self.job_repo = FakeJobRepository()
        service.job_repo = self.job_repo
        with patch.object(
            service,
            "_read_bcnb_snapshot",
            AsyncMock(return_value=(page, 28, 2026, changed, 0, 1)),
        ):
            await service.run_lms_automation(
                21,
                plan,
                plan["plan_digest"],
                runtime=runtime,
            )
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
        self.assertIn("đã thay đổi", self.job_repo.updates[-1]["message"])

    async def test_automation_partial_missing_and_cancelled_outcomes(self):
        snapshot = [
            {"class_code": "VT-MISSING", "checked": False},
            {"class_code": "VT-TARGET1", "checked": False},
            {"class_code": "VT-TARGET2", "checked": False},
        ]
        plan = build_lms_plan(["VT-MISSING"], snapshot, 28, 2026, True)
        page = FakeAutomationPage(["VT-TARGET1", "VT-TARGET2"])
        runtime, _browser = self.runtime(page=page)
        with (
            patch.object(
                service,
                "_read_bcnb_snapshot",
                AsyncMock(return_value=(page, 28, 2026, snapshot, 0, 1)),
            ),
            patch.object(
                service,
                "execute_verified_checkbox_action",
                AsyncMock(side_effect=["DRY_RUN", RuntimeError("click failed")]),
            ),
        ):
            await service.run_lms_automation(
                30, plan, plan["plan_digest"], runtime=runtime
            )
        self.assertEqual(self.job_repo.updates[-1]["status"], "PARTIAL_SUCCESS")
        result = json.loads(self.job_repo.updates[-1]["results"])
        self.assertEqual(result["verified_classes"], ["VT-TARGET1"])
        self.assertEqual(result["failed_classes"][0]["class_code"], "VT-TARGET2")

        self.job_repo = FakeJobRepository(cancel_on_check=4)
        service.job_repo = self.job_repo
        cancel_page = FakeAutomationPage(["VT-TARGET1"])
        cancel_runtime, _ = self.runtime(page=cancel_page)
        with patch.object(
            service,
            "_read_bcnb_snapshot",
            AsyncMock(return_value=(cancel_page, 28, 2026, snapshot, 0, 1)),
        ):
            await service.run_lms_automation(
                31, plan, plan["plan_digest"], runtime=cancel_runtime
            )
        self.assertEqual(self.job_repo.updates[-1]["status"], "CANCELLED")
        self.assertTrue(json.loads(self.job_repo.updates[-1]["results"])["cancelled"])


if __name__ == "__main__":
    unittest.main()
