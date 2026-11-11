"""Execute browser fetch JS against synthetic capped LMS responses; no live service."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import unittest
from unittest.mock import patch

from services.lms_quality_service import (
    evaluate_lms_spck_quality,
    fetch_lms_class_slots_via_page,
    fetch_and_evaluate_lms_quality,
)
from services.report_status import (
    ISSUE_CODE_LMS_MANUAL_CHECK,
    ISSUE_CODE_LMS_SPCK_MISSING,
    issue_code,
)


_RUNNER = r"""
const fs = require('fs');
const { script, args, mode, count } = JSON.parse(fs.readFileSync(0, 'utf8'));
global.window = { location: { hostname: 'lms.mindx.edu.vn' } };
const requests = [];
const roster = Array.from({length: count + 20}, (_, i) => ({
    activeInClass: i < count, student: {id: 'raw_' + i, fullName: 'Synthetic Person ' + i}
}));
const good = (i, cls = 'cls', session = 'slot') => ({
    id: 'work_' + i, classId: cls, classSessionId: session, classSessionNumber: null,
    studentId: 'raw_' + i, status: 'pending', isDeleted: false,
    latestData: {title: 'Synthetic Person ' + i + ' final project',
        thumbnail: 'https://cdn.example/clean.png', imageUrl: ['https://cdn.example/screen.png'], attachmentUrls: ['https://cdn.example/file.zip'],
        comment: '1. Ý tưởng: Sản phẩm thiết kế giao diện rõ ràng, có bố cục đẹp. 2. Chức năng: Hoàn thành tốt tính năng và tương tác.'}
});
const response = (data) => ({ok: true, status: 200, json: async () => data});
global.fetch = async (_url, req) => {
    const b = JSON.parse(req.body);
    if (b.operationName === 'classesMissAttendOrComment') {
        const codes = mode === 'budget' ? args.classCodes : [args.classCodes[0]];
        return response({data: {classesMissAttendOrComment: {data: codes.map((name, n) => ({
            id: 'cls' + (mode === 'budget' ? n : ''), name, students: roster,
            slots: (['budget', 'scope'].includes(mode) ? [11,12,13] : [11]).map((index) => ({
                _id: mode === 'budget' ? 'slot_' + n + '_' + index : (mode === 'scope' ? 'scope_' + index : 'slot'), index,
                date: mode === 'future' ? '2999-01-01T00:00:00Z' : '2020-01-01T00:00:00Z',
                summary: 'Synthetic complete lesson summary',
                studentAttendance: roster.slice(0, count).map((r) => ({student: mode === 'attendance_missing_id' ? {fullName:r.student.fullName} : (mode === 'attendance_duplicate_id' ? roster[0].student : r.student),
                    status: mode === 'absent' ? 'ABSENT' : 'ATTENDED', comment: ''}))
            }))
        }))}}});
    }
    requests.push(b.variables);
    if (!b.query.includes('classSessionId_equals: $sessionId') ||
        !b.query.includes('studentId_equals: $studentId') || b.query.includes('classId_in') ||
        b.query.includes('status_equals') || b.query.includes('pageIndex')) throw Error('Incorrect query');
    if (mode === 'timeout') return await new Promise((_resolve, reject) => {
        req.signal.addEventListener('abort', () => reject(Error('timeout')), {once: true});
    });
    if (mode === 'http_error') return {ok: false, status: 503};
    if (mode === 'graphql_error') return response({errors: [{message: 'synthetic error'}]});
    if (mode === 'invalid_json') return {ok: true, status: 200, text: async () => '{'};
    const pair = b.variables.studentId;
    let rows;
    if (mode === 'budget' || mode === 'confirmed_missing') rows = [];
    else if (pair) {
        rows = [good(Number(pair.slice(4)))];
        if (mode === 'pair_wrong_student') rows = [good(1)];
        if (mode === 'pair_cap') rows = Array.from({length: 20}, (_, i) => ({...good(0), id: 'repeated_' + i}));
        if (mode === 'pair_error') return {ok: false, status: 503};
        if (mode === 'pair_conflict') rows[0].latestData.title = 'Changed concurrently';
    } else if (['fallback', 'pair_wrong_student', 'pair_error', 'pair_cap'].includes(mode)) {
        rows = [];
    } else if (mode === 'cap' || mode === 'pair_conflict') {
        rows = Array.from({length: 20}, (_, i) => good(i + (mode === 'cap' ? count : 0)));
    } else rows = Array.from({length: count}, (_, i) => good(i, 'cls', b.variables.sessionId));
    if (mode === 'wrong_class') rows[0].classId = 'other';
    if (mode === 'wrong_session') rows[0].classSessionId = 'other';
    if (mode === 'missing_id') delete rows[0].id;
    if (mode === 'missing_session_id') delete rows[0].classSessionId;
    if (mode === 'unknown_owner') rows[0].studentId = 'unlisted';
    if (mode === 'duplicate') rows.push({...rows[0]});
    if (mode === 'number_conflict') rows[0].classSessionNumber = 5;
    if (mode === 'field_truncated') rows[0].latestData.attachmentUrls = Array.from({length:31}, () => 'https://cdn.example/file.zip');
    const result = {data: rows};
    if (mode === 'metadata_truncated') result.totalCount = rows.length + 1;
    return response({data: {findAllStudentWorks: result}});
};
(async () => {
    const data = await eval('(' + script + ')')(args);
    process.stdout.write(JSON.stringify({data, requests}));
})().catch((err) => {process.stderr.write(String(err)); process.exitCode = 1;});
"""


class _NodePage:
    url = "https://lms.mindx.edu.vn/admin/class-sessions"

    def __init__(self, mode: str, count: int) -> None:
        self.mode = mode
        self.count = count
        self.requests: list[dict] = []

    async def evaluate(self, script: str, args: dict) -> dict:
        payload = (
            base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 7200}).encode())
            .decode()
            .rstrip("=")
        )
        result = subprocess.run(
            ["node", "-e", _RUNNER],
            input=json.dumps(
                {
                    "script": script,
                    "args": {
                        **args,
                        "preResolvedToken": f"eyJhbGciOiJIUzI1NiJ9.{payload}.synthetic_signature",
                    },
                    "mode": self.mode,
                    "count": self.count,
                }
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=10,
        )
        parsed = json.loads(result.stdout)
        self.requests = parsed["requests"]
        return parsed["data"]


class SpckSessionFetchTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, mode: str, count: int = 7) -> tuple[dict, _NodePage]:
        page = _NodePage(mode, count)
        codes = [f"VT-JSB{i}" for i in range(60)] if mode == "budget" else ["VT-GA38"]
        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}):
            data = await fetch_lms_class_slots_via_page(
                page, class_names=codes, timeout_ms=20 if mode == "timeout" else 12000
            )
        return data, page

    async def test_pending_works_count_for_exact_session(self) -> None:
        for count in (7, 10, 4):
            with self.subTest(count=count):
                data, page = await self.fetch("direct", count)
                slot = data["VT-GA38"][0]
                self.assertEqual(len(page.requests), 1)
                self.assertEqual(page.requests[0], {"sessionId": "slot"})
                result = evaluate_lms_spck_quality(slot, class_code="VT-GA38", lesson=12)
                self.assertEqual(result["submitted_count"], count)
                self.assertEqual(result["issues"], [])
                self.assertNotIn("Synthetic Person", json.dumps(data))
                self.assertNotIn("raw_", json.dumps(data))

    async def test_twenty_cap_and_missing_attendee_use_exact_pair_fallback(self) -> None:
        for mode in ("cap", "fallback"):
            with self.subTest(mode=mode):
                data, page = await self.fetch(mode)
                slot = data["VT-GA38"][0]
                self.assertEqual(len(page.requests), 8)
                self.assertEqual(
                    [v.get("studentId") for v in page.requests[1:]], [f"raw_{i}" for i in range(7)]
                )
                result = evaluate_lms_spck_quality(slot, class_code="VT-GA38", lesson=12)
                self.assertEqual(result["submitted_count"], 7)
                self.assertEqual(result["issues"], [])

    async def test_verified_empty_pairs_can_establish_missing(self) -> None:
        data, page = await self.fetch("confirmed_missing")
        self.assertEqual(len(page.requests), 8)
        result = evaluate_lms_spck_quality(data["VT-GA38"][0], lesson=12)
        self.assertEqual([issue_code(v) for v in result["issues"]], [ISSUE_CODE_LMS_SPCK_MISSING])

    async def test_incomplete_ambiguous_failed_or_truncated_work_data_is_manual(self) -> None:
        for mode in (
            "attendance_missing_id",
            "attendance_duplicate_id",
            "wrong_class",
            "wrong_session",
            "missing_id",
            "missing_session_id",
            "unknown_owner",
            "duplicate",
            "number_conflict",
            "field_truncated",
            "metadata_truncated",
            "pair_wrong_student",
            "pair_error",
            "pair_cap",
            "pair_conflict",
            "http_error",
            "graphql_error",
            "invalid_json",
            "timeout",
        ):
            with self.subTest(mode=mode):
                data, _ = await self.fetch(mode)
                slot = data["VT-GA38"][0]
                self.assertTrue(slot.get("studentWorksFetchError"))
                result = evaluate_lms_spck_quality(slot, lesson=12)
                self.assertEqual(
                    [issue_code(v) for v in result["issues"]], [ISSUE_CODE_LMS_MANUAL_CHECK]
                )

    async def test_future_and_all_absent_slots_do_not_fetch_works(self) -> None:
        for mode in ("future", "absent"):
            data, page = await self.fetch(mode)
            self.assertEqual(page.requests, [])
            self.assertFalse(data["VT-GA38"][0].get("studentWorksFetchError"))

    async def test_batch_work_request_budget_fails_closed(self) -> None:
        data, page = await self.fetch("budget", count=1)
        self.assertEqual(len(page.requests), 120)
        self.assertTrue(data["VT-JSB59"][2].get("studentWorksFetchError"))

    async def test_only_selected_lesson_fetches_spck_even_with_three_slots(self) -> None:
        page = _NodePage("scope", 7)
        with patch.dict(os.environ, {"ZALO_QUALITY_ISOLATED": "0"}):
            data = await fetch_lms_class_slots_via_page(
                page, class_names=["VT-GA38"], target_lessons_by_class={"VT-GA38": [13]}
            )
        self.assertEqual(page.requests, [{"sessionId": "scope_12"}])
        self.assertNotIn("studentWorks", data["VT-GA38"][0])
        self.assertEqual(len(data["VT-GA38"][1]["studentWorks"]), 7)
        self.assertNotIn("studentWorks", data["VT-GA38"][2])

    async def test_row_target_scope_reaches_gateway_and_legacy_gateway_still_works(self) -> None:
        class ScopedGateway:
            seen = None

            async def fetch_class_slots(self, page, codes, *, target_lessons_by_class=None):
                self.seen = target_lessons_by_class
                return {}

        class LegacyGateway:
            called = False

            async def fetch_class_slots(self, page, codes):
                self.called = True
                return {}

        rows = [{"scan_target": {"class_code": "VT-GA38", "lesson": "13"}}]
        scoped = ScopedGateway()
        legacy = LegacyGateway()
        await fetch_and_evaluate_lms_quality(None, rows, lms_quality_gateway=scoped)
        self.assertEqual(scoped.seen, {"VT-GA38": [13]})
        await fetch_and_evaluate_lms_quality(None, rows, lms_quality_gateway=legacy)
        self.assertTrue(legacy.called)

    async def test_truncated_attendance_is_manual_without_fetching_works(self) -> None:
        data, page = await self.fetch("direct", count=51)
        self.assertEqual(page.requests, [])
        slot = data["VT-GA38"][0]
        self.assertTrue(slot["truncated"])
        self.assertTrue(slot["studentWorksFetchError"])
        result = evaluate_lms_spck_quality(slot, lesson=12)
        self.assertEqual([issue_code(v) for v in result["issues"]], [ISSUE_CODE_LMS_MANUAL_CHECK])
