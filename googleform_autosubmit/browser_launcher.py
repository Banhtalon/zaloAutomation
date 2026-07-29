from __future__ import annotations

from typing import Any

from playwright.async_api import Error as PlaywrightError


_MISSING_BROWSER_MARKERS = (
    "executable doesn't exist",
    "chromium distribution",
)


def _is_missing_browser_error(exc: PlaywrightError) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in _MISSING_BROWSER_MARKERS)


async def launch_chromium_with_fallback(browser_type: Any, *, headless: bool) -> Any:
    """Launch bundled Chromium, then installed Chrome/Edge when it is absent."""

    attempts: tuple[dict[str, str], ...] = (
        {},
        {"channel": "chrome"},
        {"channel": "msedge"},
    )
    last_missing_error: PlaywrightError | None = None

    for browser_options in attempts:
        try:
            return await browser_type.launch(headless=headless, **browser_options)
        except PlaywrightError as exc:
            if not _is_missing_browser_error(exc):
                raise
            last_missing_error = exc

    raise RuntimeError(
        "Không tìm thấy Chromium, Chrome hoặc Edge để chạy Google Form. "
        "Hãy cài Chrome/Edge hoặc chạy `playwright install chromium`."
    ) from last_missing_error
