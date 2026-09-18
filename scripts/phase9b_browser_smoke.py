"""Phase 9B browser smoke for an isolated local app or read-only staging URL.

The default mode starts an isolated FastAPI instance with dry-run flags and an
ephemeral test account.  ``--external`` only visits a supplied staging URL and
requires credentials from environment variables; it never invokes mutation
controls.  Screenshots and metadata contain no passwords or session tokens.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from threading import Thread
import time
from typing import Iterator, cast
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EVIDENCE_ROOT = ROOT / "runtime" / "release-evidence" / "phase9b-browser-smoke"

from core.auth import hash_password  # noqa: E402
from core.settings import AppSettings  # noqa: E402


@dataclass(slots=True)
class LocalServer:
    base_url: str
    server: object
    thread: Thread
    temp_dir: tempfile.TemporaryDirectory[str]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _local_server() -> Iterator[LocalServer]:
    import uvicorn

    from main import create_app

    temp_dir = tempfile.TemporaryDirectory(prefix="phase9b-smoke-")
    root = Path(temp_dir.name)
    port = _free_port()
    settings = AppSettings.from_config().with_overrides(
        database_path=str(root / "smoke.db"),
        upload_root=str(root / "uploads"),
        log_dir=str(root / "logs"),
        evidence_root=str(root / "evidence"),
        runtime_root=str(root / "runtime"),
        browser_profile_path=str(root / "zalo_session"),
        lock_root=str(root / "locks"),
        db_backup_root=str(root / "backups"),
        min_free_disk_bytes=1,
        bcnb_dry_run=True,
        google_form_mode="dry-run",
        auth_mode="web",
        auth_allowed_hosts=("127.0.0.1", "localhost"),
        auth_allowed_origins=(f"http://127.0.0.1:{port}",),
        auth_users=(
            ("phase9b-smoke", "Admin", hash_password("phase9b-smoke-pass", iterations=100_000)),
        ),
    )
    application = create_app(settings)
    config = uvicorn.Config(
        application,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    setattr(server, "install_signal_handlers", lambda: None)
    thread = Thread(target=server.run, name="phase9b-smoke-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not getattr(server, "started", False):
        if not thread.is_alive() or time.monotonic() >= deadline:
            server.should_exit = True
            thread.join(timeout=5)
            temp_dir.cleanup()
            raise RuntimeError("Local smoke server did not start.")
        time.sleep(0.05)
    try:
        yield LocalServer(f"http://127.0.0.1:{port}", server, thread, temp_dir)
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        temp_dir.cleanup()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _write_screenshot(page, filename: str) -> dict[str, object]:
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_ROOT / filename
    page.screenshot(path=str(path), full_page=True)
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _smoke(base_url: str, *, external: bool, headed: bool) -> dict[str, object]:
    from playwright.sync_api import sync_playwright

    username = os.getenv("PHASE9B_SMOKE_USERNAME", "")
    password = os.getenv("PHASE9B_SMOKE_PASSWORD", "")
    if external and (not username or not password):
        raise ValueError(
            "External smoke requires PHASE9B_SMOKE_USERNAME and "
            "PHASE9B_SMOKE_PASSWORD in the process environment."
        )
    if external and urlparse(base_url).scheme.lower() != "https":
        raise ValueError("External staging smoke requires an HTTPS base URL.")
    if not external:
        username, password = "phase9b-smoke", "phase9b-smoke-pass"

    checks: list[str] = []
    page_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        login = context.request.post(
            f"{base_url.rstrip('/')}/api/auth/login",
            data={"username": username, "password": password},
        )
        if login.status != 200:
            raise RuntimeError(f"staging login failed with HTTP {login.status}")
        login_payload = login.json()
        if not login_payload.get("authenticated"):
            raise RuntimeError("staging login response is not authenticated")
        checks.append("web-auth-login")

        me = context.request.get(f"{base_url.rstrip('/')}/api/auth/me")
        if me.status != 200 or not me.json().get("authenticated"):
            raise RuntimeError("authenticated /api/auth/me check failed")
        checks.append("web-auth-session")

        response = page.goto(f"{base_url.rstrip('/')}/", wait_until="domcontentloaded")
        if response is None or response.status != 200:
            raise RuntimeError(f"dashboard failed with HTTP {getattr(response, 'status', None)}")
        page.locator("#header-title").wait_for(state="visible", timeout=10_000)
        page.locator("#results-table").wait_for(state="visible", timeout=10_000)
        if page.locator("#count-total").inner_text().strip() != "0":
            raise RuntimeError("isolated dashboard is not empty")
        checks.append("dashboard-empty-state")

        for asset in ("/static/css/app.css", "/static/js/state.js", "/static/js/jobs.js"):
            asset_response = context.request.get(f"{base_url.rstrip('/')}{asset}")
            if asset_response.status != 200:
                raise RuntimeError(f"asset failed: {asset} HTTP {asset_response.status}")
        checks.append("static-assets")
        screenshots = [_write_screenshot(page, "dashboard-desktop.png")]

        page.set_viewport_size({"width": 390, "height": 844})
        page.reload(wait_until="domcontentloaded")
        page.locator("#header-title").wait_for(state="visible", timeout=10_000)
        screenshots.append(_write_screenshot(page, "dashboard-mobile.png"))
        checks.append("responsive-mobile")

        browser.close()
    if page_errors:
        raise RuntimeError("browser page errors: " + "; ".join(page_errors[:3]))
    return {
        "base_url": base_url,
        "mode": "external-read-only" if external else "isolated-local-dry-run",
        "checks": checks,
        "screenshots": screenshots,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--external", action="store_true")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    if args.external and not args.base_url:
        raise SystemExit("--external requires --base-url")

    if args.external:
        result = _smoke(args.base_url, external=True, headed=args.headed)
    else:
        with _local_server() as server:
            result = _smoke(
                args.base_url or server.base_url,
                external=False,
                headed=args.headed,
            )
    screenshot_items = cast(list[dict[str, object]], result["screenshots"])
    result["screenshots_sha256"] = [str(item["sha256"]) for item in screenshot_items]
    output = ROOT / "runtime" / "release-evidence" / "phase9b-browser-smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
