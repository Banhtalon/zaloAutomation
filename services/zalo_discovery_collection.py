import asyncio
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from logger_utils import log_message


MAX_CLASS_STATUS_BODY_BYTES = 2_000_000
MAX_CLASS_STATUS_ROWS = 10_000
MAX_CLASS_CODE_LENGTH = 100
MAX_CLASS_STATUS_LENGTH = 50
MAX_AGGREGATE_CLASS_STATES = 5_000


class BcnbClassStatusError(RuntimeError):
    pass


def complete_class_status_url(status_url: str) -> str:
    """Request one complete DataTables page instead of the server default.

    BCNB 2026-10 paginates ``hocsinh.php?datatable=students`` (15 rows by
    default) and ignores ``length=-1``.  The completeness check below still
    requires ``len(data) == recordsTotal``, so a larger population fails closed.
    """
    parts = urlsplit(status_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in {"start", "length"}
    ]
    query += [("start", "0"), ("length", str(MAX_CLASS_STATUS_ROWS))]
    return urlunsplit(parts._replace(query=urlencode(query)))


async def collect_bcnb_class_states(
    page,
    job_id: int,
    *,
    job_repo,
    status_url: str,
) -> list[dict[str, str]]:
    """Return complete aggregate class states without exposing student names."""
    await job_repo.ensure_not_cancelled(job_id)
    try:
        raw = await page.evaluate(
            """
            async ({url, maxBodyBytes, maxRows, maxCodeLength, maxStatusLength}) => {
                const response = await fetch(url, {credentials: 'same-origin'});
                if (!response.ok) throw new Error(`HTTP_${response.status}`);
                if (!response.body || typeof response.body.getReader !== 'function') {
                    throw new Error('CLASS_STATUS_STREAM_UNSUPPORTED');
                }
                const reader = response.body.getReader();
                const contentLength = Number(response.headers.get('content-length'));
                if (Number.isFinite(contentLength) && contentLength > maxBodyBytes) {
                    try {
                        await reader.cancel('CLASS_STATUS_BODY_TOO_LARGE');
                    } catch (_) {
                        // The size guard remains fail-closed if cancellation itself fails.
                    }
                    throw new Error('CLASS_STATUS_BODY_TOO_LARGE');
                }
                const chunks = [];
                let bodySizeBytes = 0;
                let streamComplete = false;
                let streamCancelled = false;
                try {
                    while (true) {
                        const {done, value} = await reader.read();
                        if (done) {
                            streamComplete = true;
                            break;
                        }
                        if (!value || typeof value.byteLength !== 'number' ||
                            value.byteLength < 0) {
                            throw new Error('CLASS_STATUS_STREAM_INVALID_CHUNK');
                        }
                        bodySizeBytes += value.byteLength;
                        if (bodySizeBytes > maxBodyBytes) {
                            streamCancelled = true;
                            try {
                                await reader.cancel('CLASS_STATUS_BODY_TOO_LARGE');
                            } catch (_) {
                                // The size guard remains fail-closed if cancellation itself fails.
                            }
                            throw new Error('CLASS_STATUS_BODY_TOO_LARGE');
                        }
                        chunks.push(value);
                    }
                } catch (error) {
                    if (!streamCancelled) {
                        streamCancelled = true;
                        try {
                            await reader.cancel('CLASS_STATUS_STREAM_ABORTED');
                        } catch (_) {
                            // Preserve the original stream error and fail closed.
                        }
                    }
                    throw error;
                } finally {
                    if (!streamComplete && !streamCancelled) {
                        try {
                            await reader.cancel('CLASS_STATUS_STREAM_ABORTED');
                        } catch (_) {
                            // No unbounded reader may remain after a failed collection.
                        }
                    }
                }
                const bodyBytes = new Uint8Array(bodySizeBytes);
                let bodyOffset = 0;
                for (const chunk of chunks) {
                    bodyBytes.set(chunk, bodyOffset);
                    bodyOffset += chunk.byteLength;
                }
                const body = new TextDecoder('utf-8', {fatal: true}).decode(bodyBytes);
                if (bodySizeBytes <= 0 || bodySizeBytes > maxBodyBytes) {
                    throw new Error('CLASS_STATUS_BODY_TOO_LARGE');
                }
                const payload = JSON.parse(body);
                if (!payload || !Array.isArray(payload.data)) {
                    throw new Error('INVALID_CLASS_STATUS_PAYLOAD');
                }
                if (payload.data.length > maxRows) {
                    throw new Error('CLASS_STATUS_PAYLOAD_TOO_LARGE');
                }
                const states = new Map();
                let validRowCount = 0;
                for (const row of payload.data) {
                    // BCNB 2026-10 returns ``class_status: null`` for some
                    // students.  Keep the row but record it as UNKNOWN so the
                    // class can never be considered Finished-only.
                    if (!row || typeof row !== 'object' || Array.isArray(row) ||
                        typeof row.class_code !== 'string' ||
                        (row.class_status !== null &&
                         typeof row.class_status !== 'string')) {
                        throw new Error('INVALID_CLASS_STATUS_ROW');
                    }
                    const code = row.class_code.trim().toUpperCase();
                    const status = (row.class_status || '').trim().toUpperCase() ||
                        'UNKNOWN';
                    if (!code || !status || code.length > maxCodeLength ||
                        status.length > maxStatusLength) {
                        throw new Error('INVALID_CLASS_STATUS_ROW');
                    }
                    if (!states.has(code)) states.set(code, new Set());
                    states.get(code).add(status);
                    validRowCount += 1;
                }
                const total = Number(payload.recordsTotal);
                const filtered = Number(payload.recordsFiltered);
                if (!Number.isInteger(total) || !Number.isInteger(filtered) ||
                    total < 0 || filtered < 0 || total !== filtered ||
                    payload.data.length !== total) {
                    throw new Error('INCOMPLETE_CLASS_STATUS_PAYLOAD');
                }
                return {
                    body_size_bytes: bodySizeBytes,
                    received_count: payload.data.length,
                    valid_row_count: validRowCount,
                    total_count: total,
                    rows: [...states.entries()].flatMap(([class_code, values]) =>
                        [...values].sort().map(class_status => ({class_code, class_status}))
                    )
                };
            }
            """,
            {
                "url": complete_class_status_url(status_url),
                "maxBodyBytes": MAX_CLASS_STATUS_BODY_BYTES,
                "maxRows": MAX_CLASS_STATUS_ROWS,
                "maxCodeLength": MAX_CLASS_CODE_LENGTH,
                "maxStatusLength": MAX_CLASS_STATUS_LENGTH,
            },
        )
    except Exception as exc:
        raise BcnbClassStatusError(
            "Không đọc được trạng thái kết thúc lớp từ BCNB."
        ) from exc
    if not isinstance(raw, dict):
        raise BcnbClassStatusError("Dữ liệu trạng thái lớp BCNB không hợp lệ.")
    rows = raw.get("rows")
    body_size_bytes = raw.get("body_size_bytes")
    received_count = raw.get("received_count")
    valid_row_count = raw.get("valid_row_count")
    total_count = raw.get("total_count")
    if (
        not isinstance(rows, list)
        or len(rows) > MAX_AGGREGATE_CLASS_STATES
        or not isinstance(body_size_bytes, int)
        or not 0 < body_size_bytes <= MAX_CLASS_STATUS_BODY_BYTES
        or not isinstance(received_count, int)
        or not isinstance(valid_row_count, int)
        or not isinstance(total_count, int)
        or valid_row_count != received_count
        or received_count != total_count
        or received_count > MAX_CLASS_STATUS_ROWS
        or total_count <= 0
    ):
        raise BcnbClassStatusError("Snapshot trạng thái lớp BCNB không đầy đủ.")
    result: list[dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            raise BcnbClassStatusError("Dòng trạng thái lớp BCNB không hợp lệ.")
        class_code = str(item.get("class_code", "")).strip()
        class_status = str(item.get("class_status", "")).strip().upper()
        if (
            not class_code
            or len(class_code) > MAX_CLASS_CODE_LENGTH
            or not class_status
            or len(class_status) > MAX_CLASS_STATUS_LENGTH
        ):
            raise BcnbClassStatusError(
                "Identity trạng thái lớp BCNB không hợp lệ."
            )
        result.append({"class_code": class_code, "class_status": class_status})
    if not result:
        raise BcnbClassStatusError("BCNB không trả về trạng thái lớp nào.")
    log_message(
        f"Đã đọc trạng thái tổng hợp của {len(result)} lớp từ BCNB; "
        "không thu thập tên học sinh."
    )
    return result


async def collect_zalo_group_names(page, job_id: int, *, job_repo) -> list[str]:
    await job_repo.update_job(
        job_id,
        message="Đang quét danh sách nhóm trên Zalo...",
        progress=40,
    )
    log_message("Đang chuyển sang Zalo Web...")
    await page.goto("https://chat.zalo.me/")
    await page.wait_for_selector("#contact-search-input", timeout=120000)
    search = page.locator("#contact-search-input")
    await search.fill("MindX -")
    await asyncio.sleep(2)
    contacts_tab = page.get_by_text("Liên hệ", exact=True)
    if await contacts_tab.count() > 0:
        await contacts_tab.first.click()
        await asyncio.sleep(2)
    show_groups = page.get_by_text("Xem tất cả nhóm")
    if await show_groups.count() > 0:
        await show_groups.first.click()
        await asyncio.sleep(2)
    group_names: set[str] = set()
    await page.mouse.move(150, 400)
    previous_count = 0
    for index in range(35):
        await job_repo.ensure_not_cancelled(job_id)
        body_text: Any = await page.evaluate("document.body.innerText")
        for line in str(body_text).split("\n"):
            clean_line = line.strip().replace("\xa0", " ")
            if (
                clean_line.upper().startswith("MINDX")
                and "-" in clean_line
                and len(clean_line) > 10
            ):
                group_names.add(clean_line)
        await page.mouse.wheel(0, 800)
        await asyncio.sleep(0.5)
        if len(group_names) == previous_count and index > 5:
            break
        previous_count = len(group_names)
    result = sorted(group_names)
    log_message(f"Đã thu thập được {len(result)} tên nhóm từ Zalo.")
    return result
