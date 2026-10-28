from contextlib import asynccontextmanager
import json
import unittest
from unittest.mock import AsyncMock, patch

from repositories.job_repository import JobCancelledError
from services.external_gateways import ExternalGateways
from services.lms_plan import build_lms_plan, compute_selection_digest
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
    def __init__(self, *, consumed_selection_digest=None, analysis_job_id=None):
        self.confirmations = []
        self.consumed_selection_digest = consumed_selection_digest
        self.analysis_job_id = analysis_job_id

    async def save_confirmation(self, *args):
        self.confirmations.append(args)

    async def get_consumed_selection_digest(self, **kwargs):
        if (
            self.analysis_job_id is not None
            and kwargs.get("analysis_job_id") is not None
            and int(kwargs["analysis_job_id"]) != int(self.analysis_job_id)
        ):
            raise service.LmsSafetyError(
                "Mã job phân tích LMS không khớp với bản xác nhận đã lưu.",
                code="LMS_CONFIRMATION_CONTRACT_MISMATCH",
            )
        return self.consumed_selection_digest


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

    async def test_lms_web_scraping_filters_overdue_comment_rows_and_paginates(self):
        class FakeLmsSessionRow:
            def __init__(self, values):
                self.cells = [FakeCell(v) for v in values]

            def locator(self, selector):
                if selector != "td":
                    raise AssertionError(selector)
                return type("Cells", (), {"all": AsyncMock(return_value=self.cells)})()

        class FakeLmsSessionPage:
            def __init__(self):
                self.url = "https://lms.mindx.edu.vn/admin/class-sessions"
                self.goto_urls = []
                self.page_index = 0
                self.password_loc = FakeLoginLocator(count=0, visible=False)
                self.next_btn = FakeLoginLocator(count=1, visible=True)
                self.headers = [
                    FakeCell("STT"),
                    FakeCell("Tên lớp học"),
                    FakeCell("Buổi học gần nhất chưa điểm danh"),
                    FakeCell("Buổi học gần nhất quá hạn nhận xét"),
                ]
                self.pages = [
                    [
                        FakeLmsSessionRow(["1", "VT-C4K-SB24", "7", "10"]),
                        FakeLmsSessionRow(["2", "VT-C4K-JSB25", "13", ""]),
                    ],
                    [
                        FakeLmsSessionRow(["3", "VT-C4K-SB24", "", "1"]),
                        FakeLmsSessionRow(["4", "VT-ROB-PA19", "", "1"]),
                    ],
                ]

            async def goto(self, url):
                self.goto_urls.append(url)

            async def wait_for_load_state(self, _state):
                pass

            def locator(self, selector):
                if selector == "table th":
                    return type("Headers", (), {"all": AsyncMock(return_value=self.headers)})()
                if selector == "table tr":
                    rows = self.pages[self.page_index]
                    return type("Rows", (), {"all": AsyncMock(return_value=rows)})()
                if "password" in selector:
                    return self.password_loc
                if "ant-pagination-next" in selector:
                    if self.page_index == 0:
                        page_ref = self

                        class PageAdvanceLocator(FakeLoginLocator):
                            async def click(self_inner):
                                page_ref.page_index = 1

                        return PageAdvanceLocator(count=1, visible=True)
                    return FakeLoginLocator(count=0, visible=False)
                raise AssertionError(selector)

        lms_page = FakeLmsSessionPage()
        overdue_details = {}
        with (
            patch.object(service.Config, "LMS_URL", "https://lms.mindx.edu.vn/admin/class-sessions"),
            patch.object(service.asyncio, "sleep", AsyncMock()),
        ):
            missing = await service._read_lms_missing_classes(
                lms_page, details_out=overdue_details
            )

        self.assertEqual(missing, ["VT-C4K-SB24", "VT-ROB-PA19"])
        self.assertEqual(
            overdue_details,
            {"VT-C4K-SB24": "Buổi 10, Buổi 1", "VT-ROB-PA19": "Buổi 1"},
        )

        # Fail closed when LMS page fails to load (empty), pagination is stuck on duplicate page,
        # or table exceeds max_pages limit (never allow partial missing_classes plan)
        with (
            patch.object(
                service.Config,
                "LMS_URL",
                "https://lms.mindx.edu.vn/admin/class-sessions",
            ),
            patch.object(service.asyncio, "sleep", AsyncMock()),
        ):
            # Case A: Page 1 fails to load / empty rows
            lms_page.pages = [[]]
            lms_page.page_index = 0
            with self.assertRaises(service.LmsSafetyError):
                await service._read_lms_missing_classes(lms_page)

            # Case B: Page 2 is stuck on exact same rows as Page 1
            stuck_rows = [FakeLmsSessionRow(["1", "VT-C4K-SB24", "7", "10"])]
            lms_page.pages = [stuck_rows, stuck_rows]
            lms_page.page_index = 0
            with self.assertRaises(service.LmsSafetyError):
                await service._read_lms_missing_classes(lms_page)

            # Case C: Pagination exceeds max_pages while Next Page is still active
            lms_page.pages = [
                [FakeLmsSessionRow(["1", "VT-C4K-SB24", "7", "10"])],
                [FakeLmsSessionRow(["2", "VT-ROB-PA19", "", "1"])],
            ]
            lms_page.page_index = 0
            with self.assertRaises(service.LmsSafetyError):
                await service._read_lms_missing_classes(lms_page, max_pages=1)

    async def test_lms_auto_login_and_web_analysis_success(self):
        class FakeLmsAuthPage:
            def __init__(self):
                self.url = "https://id.mindx.edu.vn/login"
                self.goto_urls = []
                self.user_loc = FakeLoginLocator(count=1, visible=True)
                self.pass_loc = FakeLoginLocator(count=1, visible=True)
                self.submit_loc = FakeLoginLocator(count=1, visible=True)

            async def goto(self, url):
                self.goto_urls.append(url)
                self.url = url

            async def wait_for_load_state(self, _state):
                pass

            def locator(self, selector):
                if "password" in selector:
                    return self.pass_loc
                if "username" in selector or "email" in selector:
                    return self.user_loc
                if "submit" in selector:
                    return self.submit_loc
                raise AssertionError(selector)

        auth_page = FakeLmsAuthPage()
        with (
            patch.object(service.Config, "LMS_URL", "https://lms.mindx.edu.vn/admin/class-sessions"),
            patch.object(service.Config, "LMS_USERNAME", "teacher@mindx.edu.vn"),
            patch.object(service.Config, "LMS_PASSWORD", "secret-pass"),
            patch.object(service.asyncio, "sleep", AsyncMock()),
        ):
            await service._login_lms_if_needed(auth_page)
        self.assertEqual(auth_page.user_loc.values, ["teacher@mindx.edu.vn"])
        self.assertEqual(auth_page.pass_loc.values, ["secret-pass"])
        self.assertEqual(auth_page.submit_loc.click_count, 1)

        page = FakeAutomationPage()
        runtime, browser = self.runtime(page=page)
        snapshot = [
            {"class_code": "VT-C4K-SB24", "checked": False},
            {"class_code": "VT-C4K-JSB25", "checked": False},
        ]
        with (
            patch.object(
                service,
                "_read_lms_missing_classes",
                AsyncMock(return_value=["VT-C4K-SB24", "VT-OTHER-WEEK99"]),
            ),
            patch.object(
                service,
                "_read_bcnb_snapshot",
                AsyncMock(return_value=(page, 28, 2026, snapshot, 0, 1)),
            ),
        ):
            await service.run_lms_analysis(40, None, runtime=runtime)

        self.assertEqual(browser.enter_count, 1)
        final = self.job_repo.updates[-1]
        self.assertEqual(final["status"], "SUCCESS")
        result = json.loads(final["results"])
        self.assertEqual(result["classes_to_tick"], ["VT-C4K-JSB25"])
        self.assertEqual(result["unmatched_classes"], ["VT-OTHER-WEEK99"])
        self.assertEqual(result.get("source"), "lms_web")

    async def test_automation_respects_selected_classes_subset_and_rejects_outside_plan(self):
        snapshot = [
            {"class_code": "VT-MISSING", "checked": False},
            {"class_code": "VT-TARGET1", "checked": False},
            {"class_code": "VT-TARGET2", "checked": False},
        ]
        plan = build_lms_plan(
            ["VT-MISSING"],
            snapshot,
            28,
            2026,
            True,
            overdue_details={"VT-MISSING": "3"},
        )
        plan["selected_classes_to_tick"] = ["VT-TARGET2"]
        plan["selection_digest"] = compute_selection_digest(
            plan["plan_digest"], ["VT-TARGET2"]
        )
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
                AsyncMock(return_value="DRY_RUN"),
            ) as action,
        ):
            await service.run_lms_automation(
                50,
                plan,
                plan["plan_digest"],
                runtime=runtime,
                expected_selection_digest=plan["selection_digest"],
            )
        self.assertEqual(action.await_count, 1)
        final = self.job_repo.updates[-1]
        self.assertEqual(final["status"], "SUCCESS")
        result = json.loads(final["results"])
        self.assertEqual(result["planned_classes"], ["VT-TARGET2"])
        self.assertEqual(result["selected_classes_to_tick"], ["VT-TARGET2"])
        self.assertEqual(result["verified_classes"], ["VT-TARGET2"])

        # Tampering with selected_classes_to_tick after confirmation must fail closed
        for tampered_selected in (
            ["VT-TARGET1", "VT-TARGET2"],
            None,
            ["VT-OUTSIDE-99"],
        ):
            tampered_plan = dict(plan)
            if tampered_selected is None:
                tampered_plan.pop("selected_classes_to_tick", None)
            else:
                tampered_plan["selected_classes_to_tick"] = tampered_selected
            self.job_repo = FakeJobRepository()
            service.job_repo = self.job_repo
            await service.run_lms_automation(
                51,
                tampered_plan,
                plan["plan_digest"],
                runtime=runtime,
                expected_selection_digest=plan["selection_digest"],
            )
            self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")

        # Even if request_payload expected_selection_digest, plan["selection_digest"],
        # and plan["analysis_job_id"] are tampered, lookup by execution_job_id must fail closed
        # before opening the browser.
        forged_plan = dict(plan)
        forged_plan["analysis_job_id"] = 999999
        forged_plan["selected_classes_to_tick"] = ["VT-TARGET1", "VT-TARGET2"]
        forged_digest = compute_selection_digest(
            plan["plan_digest"], ["VT-TARGET1", "VT-TARGET2"]
        )
        forged_plan["selection_digest"] = forged_digest
        self.job_repo = FakeJobRepository()
        service.job_repo = self.job_repo
        service.lms_repo = FakeLmsRepository(
            consumed_selection_digest=plan["selection_digest"],
            analysis_job_id=40,
        )
        tampered_runtime, tampered_browser = self.runtime(page=page)
        with patch.object(
            service,
            "execute_verified_checkbox_action",
            AsyncMock(return_value="DRY_RUN"),
        ) as tampered_action:
            await service.run_lms_automation(
                52,
                forged_plan,
                plan["plan_digest"],
                runtime=tampered_runtime,
                expected_selection_digest=forged_digest,
            )
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
        self.assertEqual(tampered_browser.enter_count, 0)
        self.assertEqual(tampered_action.await_count, 0)

        # Real SQLite LmsRepository end-to-end check against analysis_job_id + digest forgery
        import tempfile
        from db.database import database_context, init_db
        from repositories.job_repository import JobRepository
        from repositories.lms_repository import LmsRepository

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = f"{tmp_dir}/test_lms_sel.sqlite3"
            with database_context(db_path):
                init_db(db_path)
                real_job_repo = JobRepository()
                analysis_id = await real_job_repo.create_job("LMS_ANALYSIS", {})
                real_repo = LmsRepository()
                stored = build_lms_plan(
                    ["VT-MISSING"],
                    snapshot,
                    28,
                    2026,
                    True,
                    overdue_details={"VT-MISSING": "3"},
                )
                stored["analysis_job_id"] = analysis_id
                await real_repo.save_confirmation(
                    analysis_id, "tok-41", stored["plan_digest"], stored, ttl_minutes=30
                )
                consumed = await real_repo.consume_confirmation(
                    analysis_id,
                    "tok-41",
                    dry_run=True,
                    selected_classes_to_tick=["VT-TARGET2"],
                )
                tampered_db_plan = dict(consumed["plan"])
                tampered_db_plan["analysis_job_id"] = 999999
                tampered_db_plan["selected_classes_to_tick"] = [
                    "VT-TARGET1",
                    "VT-TARGET2",
                ]
                forged_db_digest = compute_selection_digest(
                    consumed["plan_digest"], ["VT-TARGET1", "VT-TARGET2"]
                )
                tampered_db_plan["selection_digest"] = forged_db_digest

                self.job_repo = FakeJobRepository()
                service.job_repo = self.job_repo
                service.lms_repo = real_repo
                db_runtime, db_browser = self.runtime(page=page)
                with patch.object(
                    service,
                    "execute_verified_checkbox_action",
                    AsyncMock(return_value="DRY_RUN"),
                ) as db_action:
                    await service.run_lms_automation(
                        consumed["job_id"],
                        tampered_db_plan,
                        consumed["plan_digest"],
                        runtime=db_runtime,
                        expected_selection_digest=forged_db_digest,
                    )
                self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
                self.assertEqual(db_browser.enter_count, 0)
                self.assertEqual(db_action.await_count, 0)

    async def test_lms_students_extraction_from_class_list_and_multi_class_tables(self):
        class FakeStudentRow:
            def __init__(self, values, on_click=None):
                self.cells = [FakeCell(v) for v in values]
                self._on_click = on_click

            async def click(self):
                if self._on_click:
                    self._on_click()

            def locator(self, selector):
                if selector == "td":
                    return type(
                        "Cells", (), {"all": AsyncMock(return_value=self.cells)}
                    )()
                return FakeLoginLocator(count=0, visible=False)

        class FakeLmsClassDetailFlowPage:
            def __init__(self):
                self.url = "https://lms.mindx.edu.vn/admin/class-sessions"
                self.opened_class = None
                self.student_tab_clicked = False
                self.password_loc = FakeLoginLocator(count=0, visible=False)

            async def goto(self, url):
                self.url = url
                self.opened_class = None

            async def wait_for_load_state(self, _state):
                pass

            def locator(self, selector):
                if "password" in selector:
                    return self.password_loc
                if "Học viên" in selector:
                    page_ref = self

                    class TabLoc(FakeLoginLocator):
                        async def click(self_inner):
                            page_ref.student_tab_clicked = True

                    return TabLoc(count=1, visible=True)
                if selector == "table th":
                    if self.opened_class is None:
                        # Includes "Student counts" to prove it is NOT mistaken for student name column
                        headers = [
                            FakeCell("Tên lớp học"),
                            FakeCell("Student counts"),
                            FakeCell("Buổi học gần nhất quá hạn nhận xét"),
                        ]
                    else:
                        headers = [
                            FakeCell("STT"),
                            FakeCell("Họ và tên"),
                            FakeCell("Trạng thái"),
                        ]
                    return type(
                        "Headers", (), {"all": AsyncMock(return_value=headers)}
                    )()
                if selector == "table tr":
                    if self.opened_class is None:
                        page_ref = self
                        rows = [
                            FakeStudentRow(
                                ["VT-C4K-GA41", "2", "1"],
                                on_click=lambda: setattr(
                                    page_ref, "opened_class", "VT-C4K-GA41"
                                ),
                            ),
                            FakeStudentRow(
                                ["VT-C4K-GI35", "1", ""],
                                on_click=lambda: setattr(
                                    page_ref, "opened_class", "VT-C4K-GI35"
                                ),
                            ),
                        ]
                    elif self.opened_class == "VT-C4K-GA41":
                        rows = [
                            FakeStudentRow(["1", "Nguyễn Văn A", "Running"]),
                            FakeStudentRow(["2", "Default", "Running"]),
                            FakeStudentRow(["3", "Trần Thị B", "Running"]),
                        ]
                    else:
                        rows = [
                            FakeStudentRow(["1", "Lê Hoàng C", "Running"]),
                        ]
                    return type(
                        "Rows", (), {"all": AsyncMock(return_value=rows)}
                    )()
                return FakeLoginLocator(count=0, visible=False)

        flow_page = FakeLmsClassDetailFlowPage()
        runtime, browser = self.runtime(page=flow_page)
        logged_messages: list[str] = []
        with (
            patch.object(
                service.Config,
                "LMS_URL",
                "https://lms.mindx.edu.vn/admin/class-sessions",
            ),
            patch.object(
                service,
                "log_message",
                side_effect=lambda msg, *a, **k: logged_messages.append(str(msg)),
            ),
        ):
            res = await service.run_googleform_fetch_lms_students(
                81,
                ["vt-c4k-ga41", "VT-C4K-GI35"],
                runtime=runtime,
            )

        self.assertEqual(browser.enter_count, 1)
        self.assertTrue(flow_page.student_tab_clicked)
        self.assertEqual(
            res["students"],
            [
                {"name": "Nguyễn Văn A", "class_code": "VT-C4K-GA41"},
                {"name": "Trần Thị B", "class_code": "VT-C4K-GA41"},
                {"name": "Lê Hoàng C", "class_code": "VT-C4K-GI35"},
            ],
        )
        final = self.job_repo.updates[-1]
        self.assertEqual(final["status"], "SUCCESS")
        stored_payload = json.loads(final["results"])
        self.assertEqual(stored_payload["kind"], "googleform_lms_students")
        self.assertEqual(len(stored_payload["students"]), 3)
        # Ensure no student PII is written to log_message
        for msg in logged_messages:
            self.assertNotIn("Nguyễn Văn A", msg)
            self.assertNotIn("Trần Thị B", msg)
            self.assertNotIn("Lê Hoàng C", msg)

        # Fail closed when requested class is not found on LMS
        self.job_repo = FakeJobRepository()
        service.job_repo = self.job_repo
        with patch.object(
            service.Config,
            "LMS_URL",
            "https://lms.mindx.edu.vn/admin/class-sessions",
        ):
            missing_res = await service.run_googleform_fetch_lms_students(
                82,
                ["VT-NOT-FOUND99"],
                runtime=runtime,
            )
        self.assertIn("error", missing_res)
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")

        # Fail closed BEFORE .all() when locator.count() exceeds limit
        class FakeOverLimitPage:
            def __init__(self):
                self.url = "https://lms.mindx.edu.vn/admin/students"
                self.password_loc = FakeLoginLocator(count=0, visible=False)
                self.headers = [FakeCell("Mã lớp"), FakeCell("Họ và tên")]
                self.rows = [
                    FakeStudentRow(["VT-C4K-GA41", f"Học viên {idx}"])
                    for idx in range(205)
                ]
                self.rows_all_mock = AsyncMock(return_value=self.rows)

            async def goto(self, url):
                self.url = url

            async def wait_for_load_state(self, _state):
                pass

            def locator(self, selector):
                if "password" in selector:
                    return self.password_loc
                if selector == "table th":
                    return type(
                        "Headers",
                        (),
                        {
                            "count": AsyncMock(return_value=len(self.headers)),
                            "all": AsyncMock(return_value=self.headers),
                        },
                    )()
                if selector == "table tr":
                    return type(
                        "Rows",
                        (),
                        {
                            "count": AsyncMock(return_value=len(self.rows)),
                            "all": self.rows_all_mock,
                        },
                    )()
                return FakeLoginLocator(count=0, visible=False)

        self.job_repo = FakeJobRepository()
        service.job_repo = self.job_repo
        over_page = FakeOverLimitPage()
        over_runtime, _ = self.runtime(page=over_page)
        with patch.object(
            service.Config,
            "LMS_URL",
            "https://lms.mindx.edu.vn/admin/students",
        ):
            over_res = await service.run_googleform_fetch_lms_students(
                83,
                ["VT-C4K-GA41"],
                runtime=over_runtime,
            )
        self.assertIn("error", over_res)
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
        self.assertIn("200", self.job_repo.updates[-1]["message"])
        self.assertEqual(over_page.rows_all_mock.await_count, 0)

        # Fail closed on duplicate student name within the same class, classless landing table,
        # duplicate class row in class table, and pagination overflow (> max_pages)
        class FakeSimpleTablePage:
            def __init__(self, headers, rows, next_page_enabled=False):
                self.url = "https://lms.mindx.edu.vn/admin/students"
                self.password_loc = FakeLoginLocator(count=0, visible=False)
                self.headers = [FakeCell(h) for h in headers]
                self.rows = [FakeStudentRow(r) for r in rows]
                self.next_page_enabled = next_page_enabled
                self.page_num = 0

            async def goto(self, url):
                self.url = url

            async def wait_for_load_state(self, _state):
                pass

            def locator(self, selector):
                if "password" in selector:
                    return self.password_loc
                if "Next Page" in selector or "next page" in selector:
                    page_ref = self

                    class NextBtn(FakeLoginLocator):
                        async def click(self_inner):
                            page_ref.page_num += 1
                            page_ref.rows = [
                                FakeStudentRow(
                                    [
                                        "VT-C4K-GA41",
                                        f"Học viên trang {page_ref.page_num}",
                                    ]
                                )
                            ]

                    return NextBtn(
                        count=1 if self.next_page_enabled else 0,
                        visible=self.next_page_enabled,
                    )
                if selector == "table th":
                    return type(
                        "Headers", (), {"all": AsyncMock(return_value=self.headers)}
                    )()
                if selector == "table tr":
                    return type(
                        "Rows", (), {"all": AsyncMock(return_value=self.rows)}
                    )()
                return FakeLoginLocator(count=0, visible=False)

        for idx_case, bad_page in enumerate(
            [
                # 1) Duplicate student name in same class
                FakeSimpleTablePage(
                    ["Mã lớp", "Họ và tên"],
                    [
                        ["VT-C4K-GA41", "Nguyễn Văn Trùng"],
                        ["VT-C4K-GA41", "Nguyễn Văn Trùng"],
                    ],
                ),
                # 2) Classless landing table (no class column to verify ownership)
                FakeSimpleTablePage(
                    ["STT", "Họ và tên"],
                    [["1", "Nguyễn Văn A"]],
                ),
                # 3) Duplicate class rows in class list table
                FakeSimpleTablePage(
                    ["Mã lớp", "Quá hạn nhận xét"],
                    [
                        ["VT-C4K-GA41", "1"],
                        ["VT-C4K-GA41", "2"],
                    ],
                ),
                # 4) Pagination overflow (more pages exist beyond max_pages=10)
                FakeSimpleTablePage(
                    ["Mã lớp", "Họ và tên"],
                    [["VT-C4K-GA41", "Học viên trang 0"]],
                    next_page_enabled=True,
                ),
            ],
            start=84,
        ):
            self.job_repo = FakeJobRepository()
            service.job_repo = self.job_repo
            bad_runtime, _ = self.runtime(page=bad_page)
            case_logs: list[str] = []
            with (
                patch.object(
                    service.Config,
                    "LMS_URL",
                    "https://lms.mindx.edu.vn/admin/students",
                ),
                patch.object(
                    service,
                    "log_message",
                    side_effect=lambda msg, *a, **k: case_logs.append(str(msg)),
                ),
            ):
                bad_res = await service.run_googleform_fetch_lms_students(
                    idx_case,
                    ["VT-C4K-GA41"],
                    runtime=bad_runtime,
                )
            self.assertIn("error", bad_res)
            self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
            for msg in case_logs:
                self.assertNotIn("Nguyễn Văn Trùng", msg)


        # 5) Duplicate class row on Page 2 of class list table must fail closed
        class FakeCrossPageDupClassPage:
            def __init__(self):
                self.url = "https://lms.mindx.edu.vn/admin/class-sessions"
                self.password_loc = FakeLoginLocator(count=0, visible=False)
                self.headers = [FakeCell("Mã lớp"), FakeCell("Quá hạn nhận xét")]
                self.page_num = 0
                self.clicked_row = False

            async def goto(self, url):
                self.url = url
                self.page_num = 0

            async def wait_for_load_state(self, _state):
                pass

            def locator(self, selector):
                if "password" in selector:
                    return self.password_loc
                if "Next Page" in selector or "next page" in selector:
                    page_ref = self

                    class NextBtn(FakeLoginLocator):
                        async def click(self_inner):
                            page_ref.page_num += 1

                    has_next = self.page_num == 0
                    return NextBtn(count=1 if has_next else 0, visible=has_next)
                if selector == "table th":
                    return type(
                        "Headers", (), {"all": AsyncMock(return_value=self.headers)}
                    )()
                if selector == "table tr":
                    page_ref = self
                    if self.page_num == 0:
                        rows = [
                            FakeStudentRow(
                                ["VT-C4K-GA41", "1"],
                                on_click=lambda: setattr(page_ref, "clicked_row", True),
                            ),
                            FakeStudentRow(["VT-C4K-GI35", "0"]),
                        ]
                    else:
                        rows = [
                            FakeStudentRow(
                                ["VT-C4K-GA41", "2"],
                                on_click=lambda: setattr(page_ref, "clicked_row", True),
                            ),
                        ]
                    return type(
                        "Rows", (), {"all": AsyncMock(return_value=rows)}
                    )()
                return FakeLoginLocator(count=0, visible=False)

        self.job_repo = FakeJobRepository()
        service.job_repo = self.job_repo
        cross_dup_page = FakeCrossPageDupClassPage()
        cross_dup_rt, _ = self.runtime(page=cross_dup_page)
        with patch.object(
            service.Config,
            "LMS_URL",
            "https://lms.mindx.edu.vn/admin/class-sessions",
        ):
            cross_dup_res = await service.run_googleform_fetch_lms_students(
                88,
                ["VT-C4K-GA41"],
                runtime=cross_dup_rt,
            )
        self.assertIn("error", cross_dup_res)
        self.assertEqual(self.job_repo.updates[-1]["status"], "FAILED")
        self.assertFalse(cross_dup_page.clicked_row)

        # 6) Multi-page student table where Page 1 and Page 2 have DIFFERENT students
        # of the SAME non-target class (VT-OTHER-01) and Page 3 has target class (VT-C4K-GA41)
        # must NOT falsely abort on Page 2 as a duplicate page.
        class FakeMultiPageNonTargetStudentPage:
            def __init__(self):
                self.url = "https://lms.mindx.edu.vn/admin/students"
                self.password_loc = FakeLoginLocator(count=0, visible=False)
                self.headers = [FakeCell("Mã lớp"), FakeCell("Họ và tên")]
                self.page_num = 0
                self.pages = [
                    [
                        FakeStudentRow(["VT-OTHER-01", "Học viên Khác 1"]),
                        FakeStudentRow(["VT-OTHER-01", "Học viên Khác 2"]),
                    ],
                    [
                        FakeStudentRow(["VT-OTHER-01", "Học viên Khác 3"]),
                        FakeStudentRow(["VT-OTHER-01", "Học viên Khác 4"]),
                    ],
                    [
                        FakeStudentRow(["VT-C4K-GA41", "Nguyễn Thành Công"]),
                    ],
                ]

            async def goto(self, url):
                self.url = url
                self.page_num = 0

            async def wait_for_load_state(self, _state):
                pass

            def locator(self, selector):
                if "password" in selector:
                    return self.password_loc
                if "Next Page" in selector or "next page" in selector:
                    page_ref = self

                    class NextBtn(FakeLoginLocator):
                        async def click(self_inner):
                            page_ref.page_num += 1

                    has_next = self.page_num < len(self.pages) - 1
                    return NextBtn(count=1 if has_next else 0, visible=has_next)
                if selector == "table th":
                    return type(
                        "Headers", (), {"all": AsyncMock(return_value=self.headers)}
                    )()
                if selector == "table tr":
                    rows = self.pages[self.page_num]
                    return type(
                        "Rows", (), {"all": AsyncMock(return_value=rows)}
                    )()
                return FakeLoginLocator(count=0, visible=False)

        self.job_repo = FakeJobRepository()
        service.job_repo = self.job_repo
        multi_non_target_page = FakeMultiPageNonTargetStudentPage()
        multi_non_target_rt, _ = self.runtime(page=multi_non_target_page)
        with patch.object(
            service.Config,
            "LMS_URL",
            "https://lms.mindx.edu.vn/admin/students",
        ):
            multi_ok_res = await service.run_googleform_fetch_lms_students(
                89,
                ["VT-C4K-GA41"],
                runtime=multi_non_target_rt,
            )
        self.assertEqual(
            multi_ok_res["students"],
            [{"name": "Nguyễn Thành Công", "class_code": "VT-C4K-GA41"}],
        )
        self.assertEqual(self.job_repo.updates[-1]["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
