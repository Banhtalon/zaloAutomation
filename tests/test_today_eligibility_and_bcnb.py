from __future__ import annotations

from datetime import date, datetime
import unittest

from core.time_utils import BUSINESS_TIMEZONE
from services.bcnb_safety import verify_checkbox_persisted
from services.lms_quality_service import (
    extract_lms_slot_time_range,
    inspect_and_attach_quality_issues,
)
from services.report_status import (
    ISSUE_CODE_LMS_MANUAL_CHECK,
    ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
    ISSUE_CODE_LMS_SPCK_MISSING,
    SEVERITY_FAIL,
    classify_course_track,
    is_lms_ready_for_bcnb_tick,
    make_structured_issue,
)
from services.zalo_target_eligibility import (
    DEFERRED_SAME_DAY_BEFORE_CLASS_END,
    DEFERRED_SAME_DAY_TIME_UNKNOWN,
    READY_CURRENT_DAY_AFTER_CLASS_END,
    evaluate_scan_target,
    parse_group_end_time,
    parse_group_time_range,
    resolve_session_time_window,
)


class _FakeCheckbox:
    def __init__(self, checked: bool = False) -> None:
        self._checked = checked

    async def count(self) -> int:
        return 1

    async def is_checked(self) -> bool:
        return self._checked


class _FakeCell:
    def __init__(self, text: str, checkbox: _FakeCheckbox | None = None) -> None:
        self._text = text
        self._checkbox = checkbox

    async def inner_text(self) -> str:
        return self._text

    def locator(self, selector: str):
        cb = self._checkbox

        class _CellLoc:
            @property
            def first(self):
                return cb

        return _CellLoc()


class _FakeRow:
    def __init__(self, cells: list[_FakeCell], *, session_id: str = "") -> None:
        self._cells = cells
        self._session_id = session_id

    async def get_attribute(self, name: str) -> str | None:
        if name == "data-session-id":
            return self._session_id
        return None

    def locator(self, selector: str):
        cells = self._cells

        class _Loc:
            async def all(self):
                return cells

        return _Loc()


class _FakeMultiSessionBcnbPage:
    def __init__(self, rows: list[_FakeRow], *, week: int = 46, year: int = 2026) -> None:
        self._rows = rows
        self._week = week
        self._year = year

    async def reload(self, wait_until: str = "domcontentloaded") -> None:
        return None

    async def wait_for_load_state(self, state: str = "networkidle", timeout: int = 30000) -> None:
        return None

    async def wait_for_selector(self, selector: str, timeout: int = 60000) -> None:
        return None

    async def evaluate(self, script: str, arg=None):
        if "button.click()" in script and isinstance(arg, list):
            return True
        if "selectedWeek" in script:
            return {
                "week": self._week,
                "year": self._year,
                "serverWeek": self._week,
                "serverYear": self._year,
            }
        return False

    def locator(self, selector: str):
        rows = self._rows

        class _RowLoc:
            def locator(self, inner: str):
                return rows[0].locator(inner)

            @property
            def first(self):
                return self

            async def count(self) -> int:
                return 0

            async def all(self):
                return rows

        return _RowLoc()


class Phase71TimeEligibilityAndBcnbTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_group_time_range_and_extract_lms_slot_time_range(self) -> None:
        parsed = parse_group_time_range("MindX - VT-C4K-SI25 T7 (18H-20H)")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0].strftime("%H:%M"), "18:00")
        self.assertEqual(parsed[1].strftime("%H:%M"), "20:00")
        self.assertEqual(parse_group_end_time("T7 20H-18H"), None)

        utc_slot = {
            "date": "2026-11-13T17:00:00.000Z",
            "startTime": "2026-11-14T11:00:00.000Z",
            "endTime": "2026-11-14T13:00:00.000Z",
        }
        start_t, end_t = extract_lms_slot_time_range(utc_slot)
        self.assertIsNotNone(start_t)
        self.assertIsNotNone(end_t)
        assert start_t is not None and end_t is not None
        self.assertEqual(start_t.strftime("%H:%M"), "18:00")
        self.assertEqual(end_t.strftime("%H:%M"), "20:00")

        self.assertEqual(
            extract_lms_slot_time_range({"startTime": "2026-11-14T18:00:00", "endTime": "2026-11-14T20:00:00"}),
            (None, None),
        )
        self.assertEqual(
            extract_lms_slot_time_range({"startTime": "20:00", "endTime": "18:00"}),
            (None, None),
        )

    def test_resolve_session_time_window_phases_and_fail_closed_end_time(self) -> None:
        day = date(2026, 11, 14)
        upcoming = resolve_session_time_window(
            day,
            lms_start_time="18:00",
            lms_end_time="20:00",
            group_name="VT-JSB15 T7 18H-19H30",
            now=datetime(2026, 11, 14, 15, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(upcoming["session_phase"], "UPCOMING")
        self.assertEqual(upcoming["end_time_hm"], "20:00")
        self.assertEqual(upcoming["time_range_display"], "18:00 – 20:00")
        self.assertFalse(upcoming["is_ended"])

        ongoing = resolve_session_time_window(
            day,
            lms_start_time="18:00",
            lms_end_time="20:00",
            group_name="VT-JSB15 T7 18H-19H30",
            now=datetime(2026, 11, 14, 19, 45, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(ongoing["session_phase"], "ONGOING")
        self.assertFalse(ongoing["is_ended"])

        ended = resolve_session_time_window(
            day,
            lms_start_time="18:00",
            lms_end_time="20:00",
            group_name="VT-JSB15 T7 18H-19H30",
            now=datetime(2026, 11, 14, 20, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(ended["session_phase"], "ENDED")
        self.assertTrue(ended["is_ended"])

        unknown = resolve_session_time_window(
            day,
            now=datetime(2026, 11, 14, 23, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(unknown["session_phase"], "TIME_UNKNOWN")
        self.assertFalse(unknown["is_ended"])

        target = {
            "class_code": "VT-JSB15",
            "lesson": "12",
            "class_date": "14/11/2026",
            "class_date_iso": "2026-11-14",
            "week": 46,
            "year": 2026,
            "group_name": "VT-JSB15 T7 18H-19H30",
            "lms_start_time": "18:00",
            "lms_end_time": "20:00",
        }
        self.assertEqual(
            evaluate_scan_target(
                target,
                latest_week=(46, 2026),
                now=datetime(2026, 11, 14, 19, 45, tzinfo=BUSINESS_TIMEZONE),
            ).reason,
            DEFERRED_SAME_DAY_BEFORE_CLASS_END,
        )
        self.assertEqual(
            evaluate_scan_target(
                target,
                latest_week=(46, 2026),
                now=datetime(2026, 11, 14, 20, 1, tzinfo=BUSINESS_TIMEZONE),
            ).reason,
            READY_CURRENT_DAY_AFTER_CLASS_END,
        )
        self.assertEqual(
            evaluate_scan_target(
                {"class_code": "VT-JSB15", "lesson": "12", "class_date_iso": "2026-11-14", "week": 46, "year": 2026, "group_name": ""},
                latest_week=(46, 2026),
                now=datetime(2026, 11, 14, 22, 0, tzinfo=BUSINESS_TIMEZONE),
            ).reason,
            DEFERRED_SAME_DAY_TIME_UNKNOWN,
        )

    def test_inspect_quality_attaches_lms_times_while_deferring_and_unlocks_b14_after_end(self) -> None:
        slots_by_class = {
            "VT-JSB15": [
                {
                    "_id": "slot14",
                    "index": 13,
                    "date": "2026-11-14T00:00:00.000+07:00",
                    "startTime": "2026-11-14T18:00:00.000+07:00",
                    "endTime": "2026-11-14T20:00:00.000+07:00",
                    "summary": "",
                    "studentAttendance": [
                        {"studentKey": "stu_1", "status": "ATTENDED", "comment": ""}
                    ],
                    "studentWorks": [],
                    "studentWorksMeta": {"complete": True, "source": "session"},
                }
            ]
        }
        before_end = [
            {
                "class_code": "VT-JSB15",
                "lesson": "14",
                "class_date_iso": "2026-11-14",
                "week": 46,
                "year": 2026,
                "group": "",
                "scan_target": {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "class_date": "14/11/2026",
                    "class_date_iso": "2026-11-14",
                    "week": 46,
                    "year": 2026,
                    "group_name": "",
                },
            }
        ]
        inspect_and_attach_quality_issues(
            before_end,
            lms_slots_by_class=slots_by_class,
            latest_week=(46, 2026),
            now=datetime(2026, 11, 14, 19, 0, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertTrue(before_end[0]["session_deferred"])
        self.assertEqual(before_end[0]["lms_start_time"], "18:00")
        self.assertEqual(before_end[0]["lms_end_time"], "20:00")
        self.assertEqual(before_end[0]["lms_quality_issues"], [])

        after_end = [
            {
                "class_code": "VT-JSB15",
                "lesson": "14",
                "class_date_iso": "2026-11-14",
                "week": 46,
                "year": 2026,
                "group": "",
                "scan_target": {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "class_date": "14/11/2026",
                    "class_date_iso": "2026-11-14",
                    "week": 46,
                    "year": 2026,
                    "group_name": "",
                },
            }
        ]
        inspect_and_attach_quality_issues(
            after_end,
            lms_slots_by_class=slots_by_class,
            latest_week=(46, 2026),
            now=datetime(2026, 11, 14, 20, 5, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertFalse(after_end[0].get("session_deferred", False))
        self.assertEqual(after_end[0]["lms_spck"]["status"], "MISSING")

    def test_classify_course_track_and_lms_ready_for_bcnb_tick(self) -> None:
        c_jsb = classify_course_track("VT-JSB15", lesson="12")
        self.assertEqual(c_jsb["course_track"], "CODING")
        self.assertTrue(c_jsb["requires_spck"])

        c_ptb = classify_course_track("VT-C4K-PTB16", lesson="12")
        self.assertEqual(c_ptb["course_track"], "CODING")
        self.assertTrue(c_ptb["spck_exempt_course"])
        self.assertFalse(c_ptb["requires_spck"])

        c_rob = classify_course_track("VT-ROB-ARMI12", lesson="14")
        self.assertEqual(c_rob["course_track"], "ROBOTICS")
        self.assertTrue(c_rob["requires_spck"])

        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "lesson": "10",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 5, "unverified_count": 0},
                    "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
                }
            ),
            (True, "LMS_COMMENTS_VERIFIED"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "lesson": "10",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 5, "unverified_count": 1},
                    "lms_comments": {"status": "COMPLETE", "submitted_count": 5},
                }
            ),
            (False, "LMS_ATTENDANCE_UNVERIFIED"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "lesson": "10",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 5, "unverified_count": 0},
                    "lms_comments": {"status": "MISSING", "submitted_count": 4},
                }
            ),
            (False, "LMS_COMMENTS_INCOMPLETE"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "lesson": "14",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 4, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                    "lms_spck": {"status": "MISSING"},
                    "lms_quality_issues": [
                        make_structured_issue(
                            ISSUE_CODE_LMS_SPCK_MISSING,
                            "[LMS] Chưa nộp SPCK (4/4 HS)",
                            severity=SEVERITY_FAIL,
                        )
                    ],
                }
            ),
            (False, "LESSON_14_SPCK_INCOMPLETE"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "lesson": "14",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 4, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                    "lms_spck": {"status": "COMPLETE"},
                    "lms_quality_issues": [
                        make_structured_issue(
                            ISSUE_CODE_LMS_SPCK_COMMENT_MISSING,
                            "[LMS] SPCK chưa có nhận xét (4/4 HS)",
                            severity=SEVERITY_FAIL,
                        )
                    ],
                }
            ),
            (False, "LESSON_14_SPCK_INCOMPLETE"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "lesson": "14",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 4, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                    "lms_spck": {"status": "COMPLETE", "submitted_count": 4, "missing_comment_count": 0},
                }
            ),
            (True, "LESSON_14_VERIFIED"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 4, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                }
            ),
            (False, "LESSON_14_SPCK_UNVERIFIED"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 4, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                    "lms_spck": {},
                }
            ),
            (False, "LESSON_14_SPCK_UNVERIFIED"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 4, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                    "lms_spck": {"status": "COMPLETE", "submitted_count": 0, "missing_comment_count": 0},
                }
            ),
            (False, "LESSON_14_SPCK_INCOMPLETE"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 4, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                    "lms_spck": {"status": "COMPLETE", "submitted_count": 4},
                }
            ),
            (False, "LESSON_14_SPCK_INCOMPLETE"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 4, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                    "lms_spck": {"status": "PARTIAL", "submitted_count": 2},
                }
            ),
            (False, "LESSON_14_SPCK_INCOMPLETE"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "class_code": "VT-PTB09",
                    "lesson": "14",
                    "lms_attendance": {"status": "VERIFIED", "attended_count": 4, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                    "lms_spck": {"status": "NOT_REQUIRED"},
                }
            ),
            (True, "LESSON_14_VERIFIED"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "lesson": "10",
                    "lms_attendance": {"status": "EXEMPT", "attended_count": 0, "unverified_count": 0},
                    "lms_comments": {"status": "EXEMPT", "submitted_count": 0},
                }
            ),
            (True, "ALL_ABSENT_EXEMPT"),
        )
        self.assertEqual(
            is_lms_ready_for_bcnb_tick(
                {
                    "lesson": "10",
                    "lms_quality_issues": [
                        make_structured_issue(
                            ISSUE_CODE_LMS_MANUAL_CHECK,
                            "[LMS] Chưa tìm thấy slot, cần kiểm tra thủ công",
                        )
                    ],
                }
            ),
            (False, "LMS_MANUAL_CHECK_REQUIRED"),
        )

    async def test_verify_checkbox_persisted_lms_disambiguates_two_sessions_same_week(self) -> None:
        header = _FakeRow(
            [
                _FakeCell("Mã lớp"),
                _FakeCell("Buổi"),
                _FakeCell("Ngày học"),
                _FakeCell("Nhận xét LMS"),
                _FakeCell("Nhận xét Zalo"),
            ]
        )
        row_b12 = _FakeRow(
            [
                _FakeCell("VT-JSB15"),
                _FakeCell("12"),
                _FakeCell("11/11/2026"),
                _FakeCell("", _FakeCheckbox(False)),
                _FakeCell("", _FakeCheckbox(True)),
            ],
            session_id="901",
        )
        row_b13 = _FakeRow(
            [
                _FakeCell("VT-JSB15"),
                _FakeCell("13"),
                _FakeCell("14/11/2026"),
                _FakeCell("", _FakeCheckbox(True)),
                _FakeCell("", _FakeCheckbox(True)),
            ],
            session_id="902",
        )
        page = _FakeMultiSessionBcnbPage([header, row_b12, row_b13], week=46, year=2026)

        self.assertTrue(
            await verify_checkbox_persisted(
                page,
                class_code="VT-JSB15",
                column_kind="lms",
                week=46,
                year=2026,
                lesson="13",
                class_date_iso="2026-11-14",
                session_id="902",
            )
        )
        self.assertFalse(
            await verify_checkbox_persisted(
                page,
                class_code="VT-JSB15",
                column_kind="lms",
                week=46,
                year=2026,
                lesson="12",
                class_date_iso="2026-11-11",
                session_id="901",
            )
        )

    async def test_verify_checkbox_persisted_fails_closed_when_requested_date_or_sid_unreadable(
        self,
    ) -> None:
        header_no_date = _FakeRow(
            [
                _FakeCell("Mã lớp"),
                _FakeCell("Buổi"),
                _FakeCell("Nhận xét LMS"),
            ]
        )
        row_no_date = _FakeRow(
            [
                _FakeCell("VT-JSB15"),
                _FakeCell("13"),
                _FakeCell("", _FakeCheckbox(True)),
            ],
            session_id="902",
        )
        page_missing_date_col = _FakeMultiSessionBcnbPage(
            [header_no_date, row_no_date], week=46, year=2026
        )
        with self.assertRaisesRegex(RuntimeError, "Ngày học"):
            await verify_checkbox_persisted(
                page_missing_date_col,
                class_code="VT-JSB15",
                column_kind="lms",
                week=46,
                year=2026,
                lesson="13",
                class_date_iso="2026-11-14",
            )
        with self.assertRaisesRegex(RuntimeError, "Ngày học"):
            await verify_checkbox_persisted(
                page_missing_date_col,
                class_code="VT-JSB15",
                column_kind="lms",
                week=46,
                year=2026,
                lesson="13",
                class_date_iso="   ",
            )
        with self.assertRaisesRegex(RuntimeError, "không hợp lệ"):
            await verify_checkbox_persisted(
                page_missing_date_col,
                class_code="VT-JSB15",
                column_kind="lms",
                week=46,
                year=2026,
                lesson="13",
                class_date_iso="2026-02-30",
            )
        with self.assertRaisesRegex(RuntimeError, "Mã buổi"):
            await verify_checkbox_persisted(
                page_missing_date_col,
                class_code="VT-JSB15",
                column_kind="lms",
                week=46,
                year=2026,
                lesson="13",
                session_id="   ",
            )

        header_with_date = _FakeRow(
            [
                _FakeCell("Mã lớp"),
                _FakeCell("Buổi"),
                _FakeCell("Ngày học"),
                _FakeCell("Nhận xét LMS"),
            ]
        )
        row_without_sid = _FakeRow(
            [
                _FakeCell("VT-JSB15"),
                _FakeCell("13"),
                _FakeCell("14/11/2026"),
                _FakeCell("", _FakeCheckbox(True)),
            ],
            session_id="",
        )
        page_missing_sid = _FakeMultiSessionBcnbPage(
            [header_with_date, row_without_sid], week=46, year=2026
        )
        with self.assertRaisesRegex(RuntimeError, "đúng một dòng"):
            await verify_checkbox_persisted(
                page_missing_sid,
                class_code="VT-JSB15",
                column_kind="lms",
                week=46,
                year=2026,
                lesson="13",
                class_date_iso="2026-11-14",
                session_id="902",
            )

    def test_cross_date_and_subminute_lms_end_times_fail_closed(self) -> None:
        self.assertEqual(
            extract_lms_slot_time_range(
                {
                    "date": "2026-11-14T00:00:00+07:00",
                    "startTime": "2026-11-14T18:00:00+07:00",
                    "endTime": "2026-11-15T20:00:00+07:00",
                }
            ),
            (None, None),
        )
        self.assertEqual(
            extract_lms_slot_time_range(
                {
                    "date": "2026-11-14T00:00:00+07:00",
                    "startTime": "2026-11-14T18:00:00+07:00",
                    "endTime": "2026-11-13T20:00:00+07:00",
                }
            ),
            (None, None),
        )
        self.assertEqual(
            extract_lms_slot_time_range(
                {
                    "date": "2026-11-15",
                    "startTime": "2026-11-14T18:00:00+07:00",
                    "endTime": "2026-11-14T20:00:00+07:00",
                }
            ),
            (None, None),
        )
        for blank_val in ("", "   "):
            for blank_key in ("date", "class_date_iso", "slot_date", "start_time", "end_time"):
                self.assertEqual(
                    extract_lms_slot_time_range(
                        {
                            "startTime": "2026-11-14T18:00:00+07:00",
                            "endTime": "2026-11-14T20:00:00+07:00",
                            blank_key: blank_val,
                        }
                    ),
                    (None, None),
                )

        cross_date_result = [
            {
                "group": "MindX - VT-JSB15",
                "class_code": "VT-JSB15",
                "lesson": "14",
                "status": "⚪ Miễn báo cáo (Buổi 14)",
                "scan_target": {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "week": 46,
                    "year": 2026,
                    "class_date_iso": "2026-11-14",
                    "group_name": "MindX - VT-JSB15",
                },
            }
        ]
        inspect_and_attach_quality_issues(
            cross_date_result,
            lms_slots_by_class={
                "VT-JSB15": [
                    {
                        "_id": "cross-date",
                        "index": 13,
                        "date": "2026-11-14T00:00:00+07:00",
                        "startTime": "2026-11-14T18:00:00+07:00",
                        "endTime": "2026-11-15T20:00:00+07:00",
                        "studentAttendance": [
                            {"studentKey": "stu_1", "status": "ATTENDED", "comment": ""}
                        ],
                        "studentWorks": [],
                        "studentWorksMeta": {"truncated": False, "fetchError": False},
                    }
                ]
            },
            latest_week=(46, 2026),
            now=datetime(2026, 11, 14, 20, 5, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertTrue(cross_date_result[0].get("session_deferred"))
        self.assertIsNone(cross_date_result[0].get("lms_end_time"))

        subminute_payload = [
            {
                "group": "MindX - VT-JSB15",
                "class_code": "VT-JSB15",
                "lesson": "14",
                "status": "⚪ Miễn báo cáo (Buổi 14)",
                "scan_target": {
                    "class_code": "VT-JSB15",
                    "lesson": "14",
                    "week": 46,
                    "year": 2026,
                    "class_date_iso": "2026-11-14",
                    "group_name": "MindX - VT-JSB15",
                },
            }
        ]
        subminute_slots = {
            "VT-JSB15": [
                {
                    "_id": "submin",
                    "index": 13,
                    "date": "2026-11-14T00:00:00+07:00",
                    "startTime": "2026-11-14T18:00:00+07:00",
                    "endTime": "2026-11-14T20:00:59+07:00",
                    "studentAttendance": [
                        {"studentKey": "stu_1", "status": "ATTENDED", "comment": ""}
                    ],
                    "studentWorks": [],
                    "studentWorksMeta": {"truncated": False, "fetchError": False},
                }
            ]
        }
        before_sec = [dict(subminute_payload[0], scan_target=dict(subminute_payload[0]["scan_target"]))]
        inspect_and_attach_quality_issues(
            before_sec,
            lms_slots_by_class=subminute_slots,
            latest_week=(46, 2026),
            now=datetime(2026, 11, 14, 20, 0, 1, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertEqual(before_sec[0].get("lms_end_time"), "20:00:59")
        self.assertEqual(before_sec[0].get("lms_end_time_hm"), "20:00")
        self.assertTrue(before_sec[0].get("session_deferred"))

        at_sec = [dict(subminute_payload[0], scan_target=dict(subminute_payload[0]["scan_target"]))]
        inspect_and_attach_quality_issues(
            at_sec,
            lms_slots_by_class=subminute_slots,
            latest_week=(46, 2026),
            now=datetime(2026, 11, 14, 20, 0, 59, tzinfo=BUSINESS_TIMEZONE),
        )
        self.assertFalse(at_sec[0].get("session_deferred"))
        self.assertEqual(at_sec[0].get("lms_spck", {}).get("status"), "MISSING")
