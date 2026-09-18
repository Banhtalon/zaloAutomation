"""Read-only two-page BCNB isolation probe for the real persistent profile.

The probe never starts FastAPI, creates jobs, opens Zalo/Google Forms, or
invokes any checkbox action. It requires an existing authenticated session,
permits at most one exact same-host authentication POST per page before the
probe, then blocks every POST while the two probe pages run. Live row contents and checkbox
fingerprints never leave browser memory.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
from urllib.parse import urljoin, urlparse
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from googleform_autosubmit.config import Config  # noqa: E402
from db.database import DB_PATH  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
from services.bcnb_safety import resolve_report_columns, select_week_checked  # noqa: E402
from core.time_utils import previous_iso_week  # noqa: E402


ACK_VALUE = "FILTER_ONLY_NO_CHECKBOX"
REQUIRED_ENV = {
    "BCNB_DRY_RUN": "true",
    "GOOGLE_FORM_MODE": "dry-run",
    "MAX_CONCURRENT_JOBS": "1",
    "BCNB_WRITE_CONCURRENCY": "2",
    "BCNB_READ_ONLY_PROBE_ACK": ACK_VALUE,
}
STATIC_READ_HOSTS = {
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "mindx.edu.vn",
}


class ProbeBlocked(RuntimeError):
    """The live probe could not establish a safe, known browser state."""


class ProbeConflict(RuntimeError):
    """The two pages do not retain independent BCNB week filters."""


class ProbeMutation(RuntimeError):
    """A forbidden checkbox event or changed checkbox vector was observed."""


def _progress(stage: str) -> None:
    print(json.dumps({"probe_stage": stage}, ensure_ascii=False), flush=True)


@dataclass(slots=True)
class RequestLedger:
    bcnb_host: str
    phase: str = "auth"
    auth_post_path: str | None = None
    counts: Counter[str] = field(default_factory=Counter)
    disallowed: list[str] = field(default_factory=list)

    async def route(self, route: Any) -> None:
        request = route.request
        parsed = urlparse(request.url)
        method = request.method.upper()
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        self.counts[f"{method}:{host}"] += 1
        try:
            port = parsed.port or 443
        except ValueError:
            port = -1
        safe_read = (
            method in {"GET", "HEAD", "OPTIONS"}
            and host in ({self.bcnb_host} | STATIC_READ_HOSTS)
            and parsed.scheme.lower() == "https"
            and port == 443
        )
        safe_auth_post = (
            self.phase == "auth"
            and method == "POST"
            and host == self.bcnb_host
            and self.auth_post_path is not None
            and path.rstrip("/") == self.auth_post_path.rstrip("/")
        )
        if not (safe_read or safe_auth_post):
            self.disallowed.append(f"{method}:{host}")
            _progress("request-blocked-by-allowlist")
            await route.abort()
            return
        await route.continue_()

    def begin_probe(self) -> dict[str, int]:
        if self.disallowed:
            raise ProbeBlocked("Auth bootstrap có request ngoài allowlist.")
        auth_counts = dict(sorted(self.counts.items()))
        self.phase = "probe"
        self.auth_post_path = None
        self.counts.clear()
        self.disallowed.clear()
        return auth_counts


class ProbeProfileLock:
    """Atomic profile lock that never removes another process's stale lock."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.token = uuid.uuid4().hex
        self._owned = False

    async def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "token": self.token}).encode()

        def create() -> None:
            try:
                descriptor = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError as exc:
                raise ProbeBlocked("Profile lock đang tồn tại; probe không tự xóa.") from exc
            try:
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

        await asyncio.to_thread(create)
        self._owned = True

    async def release(self) -> None:
        if not self._owned:
            return

        def remove_owned() -> None:
            try:
                stored = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                return
            if stored.get("token") == self.token:
                self.path.unlink()

        await asyncio.to_thread(remove_owned)
        self._owned = False


def _active_jobs(database_path: Path) -> int:
    if not database_path.is_file():
        raise ProbeBlocked("Không tìm thấy SQLite để kiểm tra active jobs.")
    uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM scan_jobs WHERE status IN ('PENDING', 'RUNNING')"
        ).fetchone()
    return int(row[0] if row else 0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _assert_preflight(
    database_path: Path, profile_path: Path, lock_path: Path
) -> dict[str, Any]:
    mismatches = [
        key
        for key, expected in REQUIRED_ENV.items()
        if os.getenv(key, "").strip().lower() != expected.lower()
    ]
    if mismatches:
        raise ProbeBlocked(
            "Guard môi trường không đúng: " + ", ".join(sorted(mismatches))
        )
    if not Config.BCNB_DRY_RUN or Config.MODE != "dry-run":
        raise ProbeBlocked("Effective config không ở dry-run.")
    expected_database = Path(DB_PATH).resolve()
    expected_profile = Path(Config.BROWSER_PROFILE_PATH).resolve()
    expected_lock = (ROOT / "runtime" / "locks" / "zalo-profile.lock").resolve()
    if database_path.resolve() != expected_database:
        raise ProbeBlocked("Probe phải kiểm tra đúng SQLite runtime của app.")
    if profile_path.resolve() != expected_profile:
        raise ProbeBlocked("Probe phải dùng đúng persistent profile của app.")
    if lock_path.resolve() != expected_lock:
        raise ProbeBlocked("Probe phải dùng đúng profile lock của app.")
    parsed = urlparse(Config.BCNB_URL)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ProbeBlocked("BCNB_URL phải là HTTPS có hostname rõ ràng.")
    if not profile_path.is_dir():
        raise ProbeBlocked("Không tìm thấy persistent browser profile.")
    active = _active_jobs(database_path)
    if active:
        raise ProbeBlocked(f"Đang có {active} job PENDING/RUNNING.")
    return {
        "bcnb_dry_run": True,
        "google_form_mode": "dry-run",
        "max_concurrent_jobs": 1,
        "bcnb_write_concurrency": 2,
        "active_jobs": 0,
        "database_path": str(expected_database),
        "profile_path": str(expected_profile),
        "lock_path": str(expected_lock),
        "profile_exists": True,
        "https": True,
    }


async def _install_checkbox_tripwire(page: Any, events: list[str]) -> None:
    await page.expose_function(
        "__bcnbProbeCheckboxEvent",
        lambda event_type: events.append(str(event_type)),
    )
    await page.add_init_script(
        """
        for (const eventName of ['click', 'input', 'change']) {
          document.addEventListener(eventName, event => {
            const target = event.target;
            if (target instanceof HTMLInputElement && target.type === 'checkbox') {
              event.preventDefault();
              event.stopImmediatePropagation();
              window.__bcnbProbeCheckboxEvent(eventName);
            }
          }, true);
        }
        """
    )


async def _captcha_present(page: Any) -> bool:
    captcha = page.locator(
        '.g-recaptcha, iframe[src*="recaptcha"], iframe[src*="captcha"]'
    )
    return bool(await captcha.count())


async def _visible_count(locator: Any) -> int:
    visible = 0
    for index in range(await locator.count()):
        if await locator.nth(index).is_visible():
            visible += 1
    return visible


async def _bootstrap_authenticated(page: Any, ledger: RequestLedger) -> bool:
    login = page.locator(
        'input[name="username"], input[name="password"], input[type="password"]'
    )
    if await _captcha_present(page):
        raise ProbeBlocked("BCNB yêu cầu CAPTCHA/MFA.")
    if await _visible_count(login) == 0:
        await _wait_for_table(page, stage="auth-existing-session")
        return False

    username = page.locator('input[name="username"]')
    password = page.locator('input[name="password"], input[type="password"]')
    submit = page.locator(
        'button[type="submit"]:visible, input[type="submit"]:visible'
    )
    if not Config.BCNB_USERNAME or not Config.BCNB_PASSWORD:
        raise ProbeBlocked("Thiếu credential BCNB trong cấu hình local.")
    if (
        await _visible_count(username) != 1
        or await _visible_count(password) != 1
        or await submit.count() != 1
    ):
        raise ProbeBlocked("Form đăng nhập BCNB thiếu hoặc không duy nhất.")
    form = password.locator("xpath=ancestor::form[1]")
    if await form.count() != 1:
        raise ProbeBlocked("Không xác định duy nhất form đăng nhập BCNB.")
    action_url = urljoin(page.url, (await form.get_attribute("action")) or page.url)
    action = urlparse(action_url)
    configured = urlparse(Config.BCNB_URL)
    if (
        action.scheme.lower() != "https"
        or action.hostname != configured.hostname
    ):
        raise ProbeBlocked("Form đăng nhập BCNB trỏ ngoài HTTPS host được phép.")
    ledger.auth_post_path = action.path or "/"
    await username.fill(Config.BCNB_USERNAME)
    await password.fill(Config.BCNB_PASSWORD)
    await submit.click()
    await page.wait_for_load_state("domcontentloaded")
    if await _captcha_present(page):
        raise ProbeBlocked("BCNB yêu cầu CAPTCHA/MFA sau đăng nhập.")
    if await _visible_count(login):
        raise ProbeBlocked("Đăng nhập BCNB không thành công.")
    await _wait_for_table(page, stage="auth-after-login")
    return True


async def _require_authenticated(page: Any) -> None:
    login = page.locator(
        'input[name="username"], input[name="password"], input[type="password"]'
    )
    if await _captcha_present(page):
        raise ProbeBlocked("BCNB yêu cầu CAPTCHA/MFA.")
    if await _visible_count(login):
        raise ProbeBlocked("BCNB session chưa đăng nhập; probe cấm auto-login.")
    await _wait_for_table(page, stage="existing-session")
    _assert_bcnb_origin(page.url)


def _assert_bcnb_origin(url: str) -> None:
    observed = urlparse(url)
    configured = urlparse(Config.BCNB_URL)
    observed_port = observed.port or 443
    configured_port = configured.port or 443
    if (
        observed.scheme.lower() != "https"
        or observed.hostname != configured.hostname
        or observed_port != configured_port
    ):
        raise ProbeBlocked("BCNB page redirect ra ngoài origin được phép.")


async def _wait_for_table(page: Any, *, stage: str) -> None:
    try:
        await page.wait_for_selector("table", timeout=60_000)
    except Exception as exc:
        parsed = urlparse(page.url)
        login = page.locator(
            'input[name="username"], input[name="password"], input[type="password"]'
        )
        table_count = await page.locator("table").count()
        ready_state = await page.evaluate("document.readyState")
        raise ProbeBlocked(
            "BCNB table không hiển thị "
            f"({stage}; host={parsed.hostname}; path={parsed.path}; "
            f"login_visible={await _visible_count(login)}; "
            f"captcha={await _captcha_present(page)}; tables={table_count}; "
            f"ready={ready_state})."
        ) from exc


async def _read_state(page: Any) -> dict[str, Any]:
    _assert_bcnb_origin(page.url)
    class_index, lesson_index, checkbox_index = await resolve_report_columns(page)
    values = await page.evaluate(
        r"""
        ([classIndex, lessonIndex, checkboxIndex]) => {
          const inputs = Array.from(document.querySelectorAll('input'));
          const descriptor = input => [
            input.name,
            input.id,
            input.placeholder,
            input.getAttribute('aria-label'),
          ].filter(Boolean).join(' ').toLowerCase();
          const weekSemantic = inputs.filter(input =>
            /(^|[^a-z])(week|tuan)([^a-z]|$)/.test(descriptor(input)));
          const yearSemantic = inputs.filter(input =>
            /(^|[^a-z])(year|nam)([^a-z]|$)/.test(descriptor(input)));
          const weekNumeric = inputs.filter(input => {
            const value = Number.parseInt(input.value, 10);
            return value > 0 && value <= 53;
          });
          const yearNumeric = inputs.filter(input => {
            const value = Number.parseInt(input.value, 10);
            return value > 2000 && value <= 2100;
          });
          const choose = (semantic, numeric) => {
            if (semantic.length === 1) return semantic[0];
            if (semantic.length === 0 && numeric.length === 1) return numeric[0];
            return null;
          };
          const weekInput = choose(weekSemantic, weekNumeric);
          const yearInput = choose(yearSemantic, yearNumeric);
          const source = Array.from(document.scripts)
            .map(script => script.textContent || '')
            .join('\n');
          const scriptNumber = name => {
            const pattern = new RegExp(`(?:const|let|var)\\s+${name}\\s*=\\s*(\\d+)`);
            const match = source.match(pattern);
            return match ? Number.parseInt(match[1], 10) : null;
          };
          const identityVector = [];
          const rows = Array.from(document.querySelectorAll('table tr'));
          for (const row of rows) {
            const cells = Array.from(row.querySelectorAll('td'));
            if (cells.length <= Math.max(classIndex, lessonIndex, checkboxIndex)) continue;
            const classCode = (cells[classIndex].innerText || '')
              .normalize('NFKC').replace(/\s+/g, '').toUpperCase();
            const lesson = (cells[lessonIndex].innerText || '').trim();
            if (!classCode || !lesson) continue;
            const boxes = cells[checkboxIndex].querySelectorAll('input[type="checkbox"]');
            if (boxes.length !== 1) {
              identityVector.push(`INVALID:${classCode}:${lesson}:${boxes.length}`);
              continue;
            }
            identityVector.push(`${classCode}:${lesson}:${Boolean(boxes[0].checked)}`);
          }
          return {
            selectedWeek: weekInput ? Number.parseInt(weekInput.value, 10) : null,
            selectedYear: yearInput ? Number.parseInt(yearInput.value, 10) : null,
            currentWeek: scriptNumber('currentWeek'),
            currentYear: scriptNumber('currentYear'),
            tableCount: document.querySelectorAll('table').length,
            dataRowCount: identityVector.length,
            checkboxCount: identityVector.filter(value => !value.startsWith('INVALID:')).length,
            identityUnique: new Set(
              identityVector.map(value => value.replace(/:(true|false)$/, ''))
            ).size === identityVector.length,
            identityVector,
          };
        }
        """,
        [class_index, lesson_index, checkbox_index],
    )
    if values["tableCount"] != 1:
        raise ProbeBlocked("BCNB không có đúng một bảng dữ liệu.")
    selected = (values["selectedWeek"], values["selectedYear"])
    if (
        selected[0] is None
        or not 1 <= int(selected[0]) <= 53
        or selected[1] is None
        or not 2000 < int(selected[1]) <= 2100
    ):
        raise ProbeBlocked("Không đọc duy nhất tuần/năm đang chọn.")
    if values["dataRowCount"] <= 0 or values["checkboxCount"] <= 0:
        raise ProbeBlocked("BCNB không có dòng/checkbox báo cáo thật để probe.")
    if values["dataRowCount"] != values["checkboxCount"]:
        raise ProbeBlocked("Có dòng BCNB thiếu hoặc thừa checkbox báo cáo.")
    if not values["identityUnique"]:
        raise ProbeBlocked("Identity lớp/buổi BCNB không duy nhất trong tuần.")
    return values


async def _raw_reload_state(page: Any) -> dict[str, Any]:
    await page.reload(wait_until="domcontentloaded")
    await _require_authenticated(page)
    return await _read_state(page)


def _assert_period(state: dict[str, Any], expected: tuple[int, int], label: str) -> None:
    observed = (int(state["selectedWeek"]), int(state["selectedYear"]))
    if observed != expected:
        raise ProbeConflict(
            f"{label} mất isolation: expected W{expected[0]}/{expected[1]}, "
            f"observed W{observed[0]}/{observed[1]}."
        )


def _assert_snapshot(
    state: dict[str, Any], baseline: tuple[str, ...], label: str
) -> None:
    observed = tuple(str(value) for value in state["identityVector"])
    if observed != baseline:
        raise ProbeMutation(
            f"Identity/thứ tự/trạng thái checkbox của {label} thay đổi trong probe."
        )


async def _run_probe(
    *,
    database_path: Path,
    profile_path: Path,
    lock_path: Path,
    rounds: int,
) -> dict[str, Any]:
    guards = _assert_preflight(database_path, profile_path, lock_path)
    _progress("preflight-passed")
    parsed = urlparse(Config.BCNB_URL)
    ledger = RequestLedger(bcnb_host=(parsed.hostname or "").lower())
    checkbox_events: dict[str, list[str]] = {"A": [], "B": []}
    original_periods: dict[str, tuple[int, int]] = {}
    restored = False
    profile_lock = ProbeProfileLock(lock_path)

    await profile_lock.acquire()
    try:
        _progress("profile-lock-acquired")
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path.resolve()),
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
                service_workers="block",
            )
            async def route_request(route: Any) -> None:
                await ledger.route(route)

            await context.route("**/*", route_request)
            _progress("persistent-context-open")
            for existing_page in context.pages:
                await existing_page.close()
            page_a = await context.new_page()
            page_b = await context.new_page()
            try:
                _progress("two-pages-created")
                if page_a is page_b or page_a.context is not page_b.context:
                    raise ProbeBlocked("Hai page không thuộc cùng một persistent context.")
                await _install_checkbox_tripwire(page_a, checkbox_events["A"])
                await _install_checkbox_tripwire(page_b, checkbox_events["B"])
                await asyncio.gather(
                    page_a.goto(Config.BCNB_URL, wait_until="domcontentloaded"),
                    page_b.goto(Config.BCNB_URL, wait_until="domcontentloaded"),
                )
                _progress("two-pages-navigated")
                login_a = await _bootstrap_authenticated(page_a, ledger)
                login_b = await _bootstrap_authenticated(page_b, ledger)
                auth_request_counts = ledger.begin_probe()
                auth_post_count = sum(
                    count
                    for key, count in auth_request_counts.items()
                    if key.startswith("POST:")
                )
                expected_auth_posts = int(login_a) + int(login_b)
                if auth_post_count != expected_auth_posts:
                    raise ProbeBlocked("Số auth POST không đúng hợp đồng bootstrap.")
                _progress("authentication-bootstrap-complete")
                await _require_authenticated(page_a)
                _progress("page-a-authenticated")
                await _require_authenticated(page_b)
                _progress("page-b-authenticated")
                initial_a, initial_b = await asyncio.gather(
                    _read_state(page_a), _read_state(page_b)
                )
                original_periods = {
                    "A": (int(initial_a["selectedWeek"]), int(initial_a["selectedYear"])),
                    "B": (int(initial_b["selectedWeek"]), int(initial_b["selectedYear"])),
                }
                _progress("initial-state-read")
                current_week = initial_a.get("currentWeek")
                current_year = initial_a.get("currentYear")
                if current_week is None or current_year is None:
                    raise ProbeBlocked("BCNB không cung cấp tuần hiện tại từ máy chủ.")
                current = (int(current_week), int(current_year))
                page_b_week = initial_b.get("currentWeek")
                page_b_year = initial_b.get("currentYear")
                if page_b_week is None or page_b_year is None:
                    raise ProbeBlocked("Page B thiếu tuần hiện tại từ máy chủ.")
                page_b_current = (
                    int(page_b_week),
                    int(page_b_year),
                )
                if page_b_current != current:
                    raise ProbeBlocked("Hai page nhận tuần hiện tại khác nhau từ BCNB.")
                previous = previous_iso_week(*current)

                await select_week_checked(page_a, *previous)
                baseline_previous = await _read_state(page_a)
                _assert_period(baseline_previous, previous, "page A baseline")
                await select_week_checked(page_b, *current)
                baseline_current = await _read_state(page_b)
                _assert_period(baseline_current, current, "page B baseline")
                previous_snapshot = tuple(
                    str(value) for value in baseline_previous["identityVector"]
                )
                current_snapshot = tuple(
                    str(value) for value in baseline_current["identityVector"]
                )
                _progress("two-period-baseline-read")

                await select_week_checked(page_b, *current)
                untouched_a = await _read_state(page_a)
                _assert_period(untouched_a, previous, "page A after page B filter")
                _assert_snapshot(untouched_a, previous_snapshot, "page A")
                reloaded_a = await _raw_reload_state(page_a)
                untouched_b = await _read_state(page_b)
                _assert_period(reloaded_a, previous, "page A after own raw reload")
                _assert_period(untouched_b, current, "page B after page A reload")
                _assert_snapshot(reloaded_a, previous_snapshot, "page A")
                _assert_snapshot(untouched_b, current_snapshot, "page B")
                _progress("sequential-b-then-a-reload-isolated")

                await select_week_checked(page_a, *previous)
                untouched_b = await _read_state(page_b)
                _assert_period(untouched_b, current, "page B after page A filter")
                _assert_snapshot(untouched_b, current_snapshot, "page B")
                reloaded_b = await _raw_reload_state(page_b)
                untouched_a = await _read_state(page_a)
                _assert_period(reloaded_b, current, "page B after own raw reload")
                _assert_period(untouched_a, previous, "page A after page B reload")
                _assert_snapshot(reloaded_b, current_snapshot, "page B")
                _assert_snapshot(untouched_a, previous_snapshot, "page A")
                _progress("sequential-a-then-b-reload-isolated")

                for index in range(rounds):
                    await asyncio.gather(
                        select_week_checked(page_a, *previous),
                        select_week_checked(page_b, *current),
                    )
                    after_filter_a, after_filter_b = await asyncio.gather(
                        _read_state(page_a), _read_state(page_b)
                    )
                    _assert_period(after_filter_a, previous, f"page A filter round {index + 1}")
                    _assert_period(after_filter_b, current, f"page B filter round {index + 1}")
                    _assert_snapshot(after_filter_a, previous_snapshot, "page A")
                    _assert_snapshot(after_filter_b, current_snapshot, "page B")
                    _progress(f"round-{index + 1}-filter-isolated")

                    reloaded_a, reloaded_b = await asyncio.gather(
                        _raw_reload_state(page_a), _raw_reload_state(page_b)
                    )
                    _assert_period(reloaded_a, previous, f"page A reload round {index + 1}")
                    _assert_period(reloaded_b, current, f"page B reload round {index + 1}")
                    _assert_snapshot(reloaded_a, previous_snapshot, "page A")
                    _assert_snapshot(reloaded_b, current_snapshot, "page B")
                    _progress(f"round-{index + 1}-reload-isolated")

                await asyncio.gather(
                    select_week_checked(page_a, *original_periods["A"]),
                    select_week_checked(page_b, *original_periods["B"]),
                )
                restored_a, restored_b = await asyncio.gather(
                    _raw_reload_state(page_a), _raw_reload_state(page_b)
                )
                _assert_period(restored_a, original_periods["A"], "page A restore")
                _assert_period(restored_b, original_periods["B"], "page B restore")
                _assert_snapshot(
                    restored_a,
                    tuple(str(value) for value in initial_a["identityVector"]),
                    "page A restore",
                )
                _assert_snapshot(
                    restored_b,
                    tuple(str(value) for value in initial_b["identityVector"]),
                    "page B restore",
                )
                if checkbox_events["A"] or checkbox_events["B"]:
                    raise ProbeMutation("Checkbox tripwire đã bắt được event bị cấm.")
                if ledger.disallowed:
                    raise ProbeBlocked("Có request ngoài allowlist trong probe.")
                if _active_jobs(database_path) != 0:
                    raise ProbeBlocked("Active jobs thay đổi trong thời gian probe.")
                restored = True
                _progress("original-filter-restored")
                return {
                    "status": "PASS",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "guards": guards,
                    "browser": {
                        "persistent_contexts": 1,
                        "distinct_pages": 2,
                        "rounds": rounds,
                        "host": ledger.bcnb_host,
                        "login_performed": login_a or login_b,
                        "service_workers_blocked": True,
                    },
                    "authentication_bootstrap": {
                        "page_a_login_performed": login_a,
                        "page_b_login_performed": login_b,
                        "post_count": auth_post_count,
                        "request_counts": auth_request_counts,
                        "checkbox_events": 0,
                    },
                    "periods": {
                        "page_a": {"week": previous[0], "year": previous[1]},
                        "page_b": {"week": current[0], "year": current[1]},
                    },
                    "sanitized_table_counts": {
                        "page_a_rows": baseline_previous["dataRowCount"],
                        "page_a_checkboxes": baseline_previous["checkboxCount"],
                        "page_b_rows": baseline_current["dataRowCount"],
                        "page_b_checkboxes": baseline_current["checkboxCount"],
                    },
                    "checks": {
                        "cross_page_filter_isolated": True,
                        "sequential_interleaving_isolated": True,
                        "raw_reload_isolated": True,
                        "checkbox_state_unchanged": True,
                        "checkbox_events": 0,
                        "disallowed_requests": 0,
                        "original_filter_restored": restored,
                        "active_jobs_after": 0,
                    },
                    "request_counts": dict(sorted(ledger.counts.items())),
                }
            finally:
                if original_periods and not restored:
                    try:
                        await select_week_checked(page_a, *original_periods["A"])
                        await select_week_checked(page_b, *original_periods["B"])
                    except Exception:
                        pass
                await page_a.close()
                await page_b.close()
                await context.close()
    finally:
        await profile_lock.release()


async def _async_main(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    try:
        result = await _run_probe(
            database_path=Path(args.database),
            profile_path=Path(args.profile_path),
            lock_path=Path(args.lock_path),
            rounds=args.rounds,
        )
        return 0, result
    except ProbeConflict as exc:
        return 2, {"status": "FAIL_CONFLICT", "error": str(exc)}
    except ProbeMutation as exc:
        return 3, {"status": "FAIL_MUTATION", "error": str(exc)}
    except Exception as exc:
        return 4, {"status": "BLOCKED", "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DB_PATH)
    parser.add_argument("--profile-path", default=Config.BROWSER_PROFILE_PATH)
    parser.add_argument(
        "--lock-path", default=str(ROOT / "runtime" / "locks" / "zalo-profile.lock")
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument(
        "--output", default="runtime/release-evidence/bcnb-two-page-readonly.json"
    )
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10:
        raise SystemExit("--rounds phải nằm trong 1..10")
    exit_code, result = asyncio.run(_async_main(args))
    script_path = Path(__file__).resolve()
    database_path = Path(args.database).resolve()
    profile_path = Path(args.profile_path).resolve()
    lock_path = Path(args.lock_path).resolve()
    result["evidence_contract"] = {
        "harness_path": str(script_path),
        "harness_sha256": _sha256(script_path),
        "rounds": args.rounds,
        "database_path": str(database_path),
        "profile_path": str(profile_path),
        "lock_path": str(lock_path),
        "profile_lock_released": not lock_path.exists(),
        "active_jobs_after": _active_jobs(database_path),
        "exit_code": exit_code,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
