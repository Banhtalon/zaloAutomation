"""Behavioural DOM contract tests for BCNB helpers against a synthetic BCNB.

All network traffic is fulfilled locally by Playwright routing; any request to
an unexpected URL is aborted, so these tests never reach the real BCNB.
"""

import json
import unittest
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright

from services import bcnb_safety
from services.zalo_discovery_collection import (
    MAX_CLASS_STATUS_ROWS,
    BcnbClassStatusError,
    collect_bcnb_class_states,
)

BASE = "https://bcnb.synthetic.test"
DEFAULT_WEEK = 44
DEFAULT_YEAR = 2026


DATE_RANGE_LABEL = "Tuần (01/01/2026 - 07/01/2026)"
LABELS = (DATE_RANGE_LABEL, "Xem", "Lọc lịch")


def _render(week, year, *, variant, label=DATE_RANGE_LABEL):
    submit_attrs = ""
    extra = ""
    form_submit = '<button type="submit" {attrs} class="btn">' + label + "</button>"
    if variant == "disabled":
        submit_attrs = "disabled"
    elif variant == "foreign":
        # The only visible submit belongs to a different POST form.
        form_submit = ""
        extra = (
            '<form id="other" method="POST" action="/mutate"></form>'
            f'<button type="submit" form="other">{label}</button>'
        )
    elif variant == "foreign_nested":
        # Nested inside the week form markup but owned by a POST form.
        submit_attrs = 'form="other"'
        extra = '<form id="other" method="POST" action="/mutate"></form>'
    elif variant == "formmethod_post":
        submit_attrs = 'formmethod="post"'
    elif variant == "formaction":
        submit_attrs = 'formaction="/mutate"'
    elif variant == "client_only":
        submit_attrs = "onclick=\"event.preventDefault()\""
    method = "POST" if variant == "post_form" else "GET"
    return f"""<!doctype html><html><body>
<form method="{method}" action="/check_nhanxet.php">
  <input id="week-input" type="number" name="week" value="{week}">
  <input id="year-input" type="number" name="year" value="{year}">
  {form_submit.format(attrs=submit_attrs)}
</form>
{extra}
<table><thead><tr><th>Mã lớp</th><th>Buổi</th><th>NX Zalo</th></tr></thead>
<tbody><tr><td>VT-C4K-SI25</td><td>3</td><td><input type="checkbox"></td></tr></tbody></table>
<script>
  const currentWeek = {DEFAULT_WEEK};
  const currentYear = {DEFAULT_YEAR};
  const selectedWeek = {week};
  const selectedYear = {year};
</script>
</body></html>"""


class FakeJobRepo:
    async def ensure_not_cancelled(self, _job_id):
        return None


class BcnbDomContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context()
        self.requests = []
        self.variant = "ok"
        self.label = DATE_RANGE_LABEL
        self.status_payload = None
        await self.context.route("**/*", self._handle)
        self.page = await self.context.new_page()

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def _handle(self, route):
        request = route.request
        self.requests.append((request.method, request.url))
        parts = urlsplit(request.url)
        if not request.url.startswith(BASE):
            await route.abort()
            return
        query = parse_qs(parts.query)
        if parts.path == "/check_nhanxet.php" and request.method == "GET":
            week = int(query.get("week", [DEFAULT_WEEK])[0])
            year = int(query.get("year", [DEFAULT_YEAR])[0])
            await route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=_render(week, year, variant=self.variant, label=self.label),
            )
            return
        if parts.path == "/hocsinh.php" and request.method == "GET":
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(self.status_payload(query)),
            )
            return
        await route.fulfill(status=405, body="blocked")

    def _non_get_requests(self):
        return [item for item in self.requests if item[0] != "GET"]

    async def _open(self, variant, label=DATE_RANGE_LABEL):
        self.variant = variant
        self.label = label
        await self.page.goto(f"{BASE}/check_nhanxet.php")

    async def test_date_range_submit_of_get_form_switches_server_week(self):
        for label in LABELS:
            with self.subTest(label=label):
                self.requests.clear()
                await self._open("ok", label)

                result = await bcnb_safety.select_week_checked(self.page, 43, 2026)

                self.assertEqual(result, (43, 2026))
                self.assertTrue(any("week=43" in url for _m, url in self.requests))
                self.assertEqual(self._non_get_requests(), [])

    async def test_unsafe_submit_controls_are_rejected(self):
        variants = (
            "disabled",
            "foreign",
            "foreign_nested",
            "formmethod_post",
            "formaction",
            "post_form",
        )
        for label in LABELS:
            for variant in variants:
                with self.subTest(label=label, variant=variant):
                    self.requests.clear()
                    await self._open(variant, label)
                    with self.assertRaisesRegex(RuntimeError, "Không xác định"):
                        await bcnb_safety.select_week_checked(self.page, 43, 2026)
                    self.assertEqual(self._non_get_requests(), [])
                    self.assertFalse(any("/mutate" in url for _m, url in self.requests))

    async def test_client_only_input_change_without_server_apply_fails_closed(self):
        await self._open("client_only")

        with self.assertRaisesRegex(RuntimeError, "máy chủ chưa áp dụng"):
            await bcnb_safety.select_week_checked(self.page, 43, 2026)

    async def test_collector_requests_full_page_and_maps_null_status_to_unknown(self):
        rows = [
            {"student_name": "Synthetic A", "class_code": "VT-A01", "class_status": "Finished"},
            {"student_name": "Synthetic B", "class_code": "VT-A01", "class_status": None},
            {"student_name": "Synthetic C", "class_code": "VT-B02", "class_status": "Finished"},
            {"student_name": "Synthetic D", "class_code": "VT-C03", "class_status": "  "},
        ] + [
            {"student_name": f"S{i}", "class_code": "VT-D04", "class_status": "Running"}
            for i in range(20)
        ]

        def paginated(query):
            start = int(query.get("start", ["0"])[0])
            length = int(query.get("length", ["15"])[0])
            if length < 0:
                length = 15
            return {
                "draw": 1,
                "recordsTotal": len(rows),
                "recordsFiltered": len(rows),
                "data": rows[start:start + length],
            }

        self.status_payload = paginated
        await self._open("ok")

        result = await collect_bcnb_class_states(
            self.page,
            1,
            job_repo=FakeJobRepo(),
            status_url=f"{BASE}/hocsinh.php?datatable=students",
        )

        states = {(item["class_code"], item["class_status"]) for item in result}
        self.assertEqual(
            states,
            {
                ("VT-A01", "FINISHED"),
                ("VT-A01", "UNKNOWN"),
                ("VT-B02", "FINISHED"),
                ("VT-C03", "UNKNOWN"),
                ("VT-D04", "RUNNING"),
            },
        )
        self.assertNotIn("synthetic", json.dumps(result).lower())
        status_urls = [url for _m, url in self.requests if "/hocsinh.php" in url]
        self.assertTrue(status_urls)
        query = parse_qs(urlsplit(status_urls[-1]).query)
        self.assertEqual(query["length"], [str(MAX_CLASS_STATUS_ROWS)])
        self.assertEqual(query["start"], ["0"])

    async def test_collector_rejects_server_that_ignores_length(self):
        rows = [
            {"class_code": f"VT-X{i:02d}", "class_status": "Finished"} for i in range(20)
        ]
        self.status_payload = lambda _query: {
            "draw": 1,
            "recordsTotal": len(rows),
            "recordsFiltered": len(rows),
            "data": rows[:15],
        }
        await self._open("ok")

        with self.assertRaises(BcnbClassStatusError):
            await collect_bcnb_class_states(
                self.page,
                2,
                job_repo=FakeJobRepo(),
                status_url=f"{BASE}/hocsinh.php?datatable=students",
            )


if __name__ == "__main__":
    unittest.main()
