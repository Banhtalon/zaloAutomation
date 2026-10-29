"""Failed-classes CSV report email builder and SMTP delivery service."""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
import html
import os
import re
import smtplib
import ssl
from typing import Any
import unicodedata
from urllib.parse import quote

from repositories.report_repository import (
    _resolve_row_evaluation_status,
    _resolve_row_issue_description,
    build_weekly_summary_csv,
    sanitize_csv_cell,
)

EMAIL_TABLE_HEADERS: tuple[str, ...] = (
    "STT",
    "Mã lớp",
    "Buổi",
    "Ngày học",
    "Giảng viên",
    "NX Zalo",
    "NX LMS",
    "Tình trạng đánh giá",
    "Mô tả vấn đề (TE/TF/QC)",
)

OWNER_TRIAL_EMAIL = "quangda@mindx.net.vn"
MANAGER_EMAIL_ALLOWLIST: tuple[str, ...] = (
    "quangda@mindx.net.vn",
    "tuanpa@mindx.com.vn",
)
DEFAULT_REPORT_EMAIL_RECIPIENTS: tuple[str, ...] = (OWNER_TRIAL_EMAIL,)
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class ReportEmailError(ValueError):
    """Structured error raised during failed-classes CSV email processing."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 400,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.extra = dict(extra) if isinstance(extra, dict) else {}


def _normalize_email_tokens(raw_input: Any) -> list[str]:
    if raw_input is None:
        return []
    if isinstance(raw_input, str):
        parts = re.split(r"[,;\s]+", raw_input.strip())
    elif isinstance(raw_input, (list, tuple, set)):
        parts = []
        for item in raw_input:
            if item is not None:
                parts.extend(re.split(r"[,;\s]+", str(item).strip()))
    else:
        raise ReportEmailError(
            "Danh sách email người nhận không hợp lệ.",
            code="INVALID_EMAIL_RECIPIENTS",
            status_code=422,
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        candidate = part.strip().lower()
        if not candidate:
            continue
        if not _EMAIL_RE.match(candidate) or "\r" in candidate or "\n" in candidate:
            raise ReportEmailError(
                f"Địa chỉ email không hợp lệ: {part}",
                code="INVALID_EMAIL_RECIPIENTS",
                status_code=422,
            )
        if candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return normalized


def resolve_report_email_recipients(
    requested_recipients: Any = None,
    configured_recipients: Any = None,
) -> list[str]:
    """Resolve and validate recipients against the authorized MindX manager allowlist."""
    configured_list = _normalize_email_tokens(configured_recipients)
    allowed_set = set(MANAGER_EMAIL_ALLOWLIST)
    for item in configured_list:
        if item in MANAGER_EMAIL_ALLOWLIST:
            allowed_set.add(item)

    requested_list = _normalize_email_tokens(requested_recipients)
    if requested_list:
        unauthorized = [email for email in requested_list if email not in allowed_set]
        if unauthorized:
            raise ReportEmailError(
                "Chỉ cho phép gửi báo cáo tới email Quản lý đã phê duyệt "
                f"({', '.join(MANAGER_EMAIL_ALLOWLIST)}).",
                code="UNAUTHORIZED_EMAIL_RECIPIENT",
                status_code=422,
            )
        return requested_list

    valid_configured = [email for email in configured_list if email in allowed_set]
    if valid_configured:
        return valid_configured
    return list(DEFAULT_REPORT_EMAIL_RECIPIENTS)


def _clean_single_line(value: Any, fallback: str = "—") -> str:
    if value is None:
        return fallback
    cleaned = re.sub(r"[\r\n\t]+", " ", str(value)).strip()
    return cleaned if cleaned else fallback


_EMOJI_ASCII_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("⚠️", "[!]"),
    ("⚠", "[!]"),
    ("✅", "[OK]"),
    ("❌", "[X]"),
    ("⏳", "[..]"),
    ("🟡", "[TRE]"),
    ("🧪", "[THU]"),
)


def _normalize_unicode_table_cell(value: str) -> str:
    text = str(value or "").strip()
    for emoji_seq, ascii_tag in _EMOJI_ASCII_REPLACEMENTS:
        text = text.replace(emoji_seq, ascii_tag)
    text = re.sub(r"[\ufe0e\ufe0f\u200b\u200d]", "", text)
    return text


def _char_display_width(ch: str) -> int:
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def _display_width(value: str) -> int:
    return sum(_char_display_width(ch) for ch in str(value or ""))


def _truncate_cell(value: str, max_width: int) -> str:
    text = _normalize_unicode_table_cell(value)
    if _display_width(text) <= max_width:
        return text
    if max_width <= 3:
        out_short: list[str] = []
        used_short = 0
        for ch in text:
            w = _char_display_width(ch)
            if used_short + w > max_width:
                break
            out_short.append(ch)
            used_short += w
        return "".join(out_short)
    budget = max_width - 1
    out: list[str] = []
    used = 0
    for ch in text:
        w = _char_display_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out).rstrip() + "…"


def _pad_cell(value: str, target_width: int) -> str:
    pad_len = max(0, target_width - _display_width(value))
    return value + (" " * pad_len)


def _prepare_email_row_fields(idx: int, row: dict[str, Any]) -> dict[str, str]:
    raw_st = row.get("scan_target")
    st: dict[str, Any] = raw_st if isinstance(raw_st, dict) else {}
    class_code = _clean_single_line(row.get("class_code"), "KHONG_RO_LOP")
    teacher = _clean_single_line(row.get("teacher"), "Chưa phân công")
    lesson = _clean_single_line(row.get("lesson"), "—")
    class_date = _clean_single_line(row.get("class_date"), "—")
    zalo_status = _clean_single_line(row.get("zalo_status"), "Chưa xác định")
    lms_status = _clean_single_line(row.get("lms_status"), "Chưa quét LMS")
    raw_eval = str(row.get("evaluation_status") or "").strip()
    if not raw_eval:
        raw_eval = _resolve_row_evaluation_status(
            zalo_status=zalo_status,
            lms_status=lms_status,
            nx_zalo_checked=row.get("nx_zalo_checked"),
            nx_lms_checked=row.get("nx_lms_checked"),
        )
    evaluation_status = _clean_single_line(raw_eval, "—")
    raw_issue = str(row.get("issue_description") or "").strip()
    if not raw_issue:
        raw_issue = _resolve_row_issue_description(row, st=st)
    issue_description = _clean_single_line(raw_issue, "—")
    verified_at = _clean_single_line(row.get("verified_at"), "—")
    return {
        "stt": str(idx),
        "class_code": class_code,
        "lesson": lesson,
        "class_date": class_date,
        "teacher": teacher,
        "zalo_status": zalo_status,
        "lms_status": lms_status,
        "evaluation_status": evaluation_status,
        "issue_description": issue_description,
        "verified_at": verified_at,
    }


def _build_unicode_table(prepared_rows: list[dict[str, str]]) -> str:
    col_specs: tuple[tuple[str, str, int], ...] = (
        ("stt", "STT", 4),
        ("class_code", "Mã lớp", 16),
        ("lesson", "Buổi", 5),
        ("class_date", "Ngày học", 10),
        ("teacher", "Giảng viên", 22),
        ("zalo_status", "NX Zalo", 22),
        ("lms_status", "NX LMS", 18),
        ("evaluation_status", "Tình trạng đánh giá", 28),
        ("issue_description", "Mô tả vấn đề", 36),
    )
    matrix: list[list[str]] = []
    for item in prepared_rows:
        matrix.append(
            [
                _truncate_cell(item.get(key, "—"), max_w)
                for key, _hdr, max_w in col_specs
            ]
        )
    widths: list[int] = []
    for col_idx, (_key, hdr, max_w) in enumerate(col_specs):
        col_max = _display_width(hdr)
        for row_cells in matrix:
            col_max = max(col_max, _display_width(row_cells[col_idx]))
        widths.append(min(max_w, col_max))

    top_border = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    mid_border = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
    bot_border = "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"

    header_line = (
        "│"
        + "│".join(
            f" {_pad_cell(_truncate_cell(hdr, widths[i]), widths[i])} "
            for i, (_key, hdr, _max_w) in enumerate(col_specs)
        )
        + "│"
    )
    lines = [top_border, header_line, mid_border]
    for row_cells in matrix:
        lines.append(
            "│"
            + "│".join(
                f" {_pad_cell(row_cells[i], widths[i])} "
                for i in range(len(col_specs))
            )
            + "│"
        )
    lines.append(bot_border)
    return "\n".join(lines)


def build_failed_classes_email_package(
    *,
    week: int,
    year: int,
    failed_rows: list[dict[str, Any]],
    total_rows_count: int,
    selected_job_id: int | None,
    recipients: list[str],
) -> dict[str, Any]:
    """Build CSV attachment, subject, text/HTML bodies, and mailto/Gmail compose links."""
    safe_week = int(week)
    safe_year = int(year)
    failed_count = len(failed_rows)
    total_count = max(failed_count, int(total_rows_count))
    filename = f"Bao_Cao_Lop_Loi_Tuan_W{safe_week:02d}_{safe_year}.csv"
    csv_text = build_weekly_summary_csv(failed_rows)

    job_suffix = f" (Job #{int(selected_job_id)})" if selected_job_id else ""
    subject = (
        f"[MindX BCNB] Báo cáo danh sách lớp lỗi Tuần W{safe_week:02d}/{safe_year}"
        f"{job_suffix} - {failed_count}/{total_count} lớp cần xử lý"
    )

    prepared_rows = [
        _prepare_email_row_fields(idx, row)
        for idx, row in enumerate(failed_rows, start=1)
        if isinstance(row, dict)
    ]
    text_table = _build_unicode_table(prepared_rows)

    detail_lines: list[str] = []
    ultra_compact_lines: list[str] = []
    for item in prepared_rows:
        issue_suffix = (
            f" | Vấn đề: {item['issue_description']}"
            if item["issue_description"] and item["issue_description"] != "—"
            else ""
        )
        detail_lines.append(
            f"{item['stt']}. [{item['class_code']}] GV: {item['teacher']} | Buổi {item['lesson']} ({item['class_date']}) "
            f"| Zalo: {item['zalo_status']} | LMS: {item['lms_status']} "
            f"| Đánh giá: {item['evaluation_status']}{issue_suffix}"
        )
        short_issue = (
            f" | {_truncate_cell(item['issue_description'], 48)}"
            if item["issue_description"] and item["issue_description"] != "—"
            else ""
        )
        ultra_compact_lines.append(
            f"{item['stt']}. [{item['class_code']}] B{item['lesson']} ({item['class_date']}) "
            f"| {_truncate_cell(item['teacher'], 22)} "
            f"| Zalo:{_truncate_cell(item['zalo_status'], 18)} "
            f"| LMS:{_truncate_cell(item['lms_status'], 16)}"
            f"{short_issue}"
        )

    header_lines = [
        "Kính gửi Quản lý,",
        "",
        f"Hệ thống Zalo & BCNB Automation gửi báo cáo danh sách các lớp lỗi/chưa hoàn tất trong Tuần W{safe_week:02d}/{safe_year}{job_suffix}:",
        f"- Tổng số lớp trong kỳ: {total_count}",
        f"- Số lớp lỗi / cần kiểm tra: {failed_count}",
        f"- File đính kèm: {filename}",
        "",
    ]
    footer_lines = [
        "",
        "Trân trọng,",
        "MindX Zalo & BCNB Automation",
    ]
    text_lines = [
        *header_lines,
        "Bảng tổng hợp các lớp cần xử lý (Mẹo: Bảng HTML kẻ khung đã được copy sẵn vào Clipboard, bạn có thể bấm Ctrl+V để dán bảng màu vào Gmail):",
        text_table,
        "",
        "Chi tiết từng lớp:",
        *detail_lines,
        *footer_lines,
    ]
    text_body = "\n".join(text_lines)
    compose_compact_body = "\n".join(
        [
            *header_lines,
            "Danh sách chi tiết các lớp cần xử lý (Mẹo: Bảng HTML kẻ khung đã được copy sẵn vào Clipboard, bạn có thể bấm Ctrl+V để dán bảng màu vào Gmail):",
            *detail_lines,
            *footer_lines,
        ]
    )
    compose_ultra_compact_body = "\n".join(
        [
            *header_lines,
            "Danh sách rút gọn các lớp cần xử lý (Mẹo: Bấm Ctrl+V để dán bảng đầy đủ từ Clipboard):",
            *ultra_compact_lines,
            *footer_lines,
        ]
    )

    html_rows: list[str] = []
    for row_idx, item in enumerate(prepared_rows):
        row_bg = "#ffffff" if row_idx % 2 == 0 else "#f8fafc"
        cells = [
            item["stt"],
            sanitize_csv_cell(item["class_code"]) or "—",
            sanitize_csv_cell(item["lesson"]) or "—",
            sanitize_csv_cell(item["class_date"]) or "—",
            sanitize_csv_cell(item["teacher"]) or "—",
            sanitize_csv_cell(item["zalo_status"]) or "—",
            sanitize_csv_cell(item["lms_status"]) or "—",
            sanitize_csv_cell(item["evaluation_status"]) or "—",
            sanitize_csv_cell(item["issue_description"]) or "—",
        ]
        td_parts: list[str] = []
        for col_i, cell in enumerate(cells):
            extra_style = ""
            if col_i == 0:
                extra_style = "text-align:center;font-weight:600;color:#475569;"
            elif col_i == 1:
                extra_style = "font-weight:700;color:#0f172a;white-space:nowrap;"
            elif col_i in (2, 3):
                extra_style = "text-align:center;white-space:nowrap;"
            elif col_i == 8 and cell != "—":
                extra_style = "color:#b91c1c;font-weight:600;background:#fef2f2;"
            td_parts.append(
                f'<td style="border:1px solid #cbd5e1;padding:7px 10px;font-size:13px;background:{row_bg};{extra_style}">'
                f"{html.escape(cell)}</td>"
            )
        html_rows.append(f"<tr>{''.join(td_parts)}</tr>")

    th_html = "".join(
        f'<th style="border:1px solid #1e293b;background:#0f172a;color:#ffffff;padding:8px 10px;font-size:12px;text-align:left;">{html.escape(header)}</th>'
        for header in EMAIL_TABLE_HEADERS
    )
    html_table = (
        '<table style="border-collapse:collapse;width:100%;max-width:1120px;margin-top:12px;font-family:Inter,Segoe UI,Arial,sans-serif;">'
        f"<thead><tr>{th_html}</tr></thead>"
        f"<tbody>{''.join(html_rows)}</tbody>"
        "</table>"
    )
    html_body = (
        '<div style="font-family:Inter,Segoe UI,Arial,sans-serif;color:#0f172a;line-height:1.5;">'
        f"<p>Kính gửi Quản lý,</p>"
        f"<p>Hệ thống <strong>MindX Zalo &amp; BCNB Automation</strong> gửi báo cáo danh sách các lớp lỗi/chưa hoàn tất trong "
        f"<strong>Tuần W{safe_week:02d}/{safe_year}{html.escape(job_suffix)}</strong>:</p>"
        "<ul>"
        f"<li>Tổng số lớp trong kỳ: <strong>{total_count}</strong></li>"
        f'<li>Số lớp lỗi / cần xử lý: <strong style="color:#dc2626;">{failed_count}</strong></li>'
        f"<li>File CSV đính kèm: <code>{html.escape(filename)}</code></li>"
        "</ul>"
        f"{html_table}"
        "<p style=\"margin-top:16px;font-size:12px;color:#475569;\">Trân trọng,<br>MindX Zalo &amp; BCNB Automation</p>"
        "</div>"
    )

    to_joined = ",".join(recipients)
    max_compose_url_body_chars = 6500
    if len(text_body) <= max_compose_url_body_chars:
        compose_source = text_body
    elif len(compose_compact_body) <= max_compose_url_body_chars:
        compose_source = compose_compact_body
    else:
        compose_source = compose_ultra_compact_body
    gmail_body_truncated = len(compose_source) > max_compose_url_body_chars
    if gmail_body_truncated:
        truncation_notice = (
            "\n... [Nội dung URL Gmail bị giới hạn độ dài — "
            "Hãy bấm Ctrl+V để dán bảng đầy đủ từ Clipboard hoặc đính kèm file CSV]"
        )
        url_body_preview = (
            compose_source[: max_compose_url_body_chars - len(truncation_notice)].rstrip()
            + truncation_notice
        )
    else:
        url_body_preview = compose_source
    gmail_compose_url = (
        "https://mail.google.com/mail/?view=cm&fs=1"
        f"&to={quote(to_joined)}"
        f"&su={quote(subject)}"
        f"&body={quote(url_body_preview)}"
    )
    mailto_url = (
        f"mailto:{quote(to_joined)}"
        f"?subject={quote(subject)}"
        f"&body={quote(url_body_preview)}"
    )

    return {
        "filename": filename,
        "csv_text": csv_text,
        "subject": subject,
        "text_table": text_table,
        "text_body": text_body,
        "html_table": html_table,
        "html_body": html_body,
        "gmail_compose_url": gmail_compose_url,
        "gmail_body_truncated": gmail_body_truncated,
        "mailto_url": mailto_url,
    }


def _deliver_smtp_message(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    message: EmailMessage,
) -> dict[str, Any]:
    tls_context = ssl.create_default_context()
    if int(port) == 465:
        with smtplib.SMTP_SSL(
            host=host,
            port=int(port),
            timeout=15.0,
            context=tls_context,
        ) as smtp:
            smtp.login(username, password)
            refused = smtp.send_message(message)
            return dict(refused) if isinstance(refused, dict) else {}

    with smtplib.SMTP(host=host, port=int(port), timeout=15.0) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls(context=tls_context)
            smtp.ehlo()
        smtp.login(username, password)
        refused = smtp.send_message(message)
        return dict(refused) if isinstance(refused, dict) else {}


async def send_failed_classes_report_email(
    *,
    settings: Any,
    week: int,
    year: int,
    all_rows: list[dict[str, Any]],
    failed_rows: list[dict[str, Any]],
    selected_job_id: int | None = None,
    requested_recipients: Any = None,
) -> dict[str, Any]:
    """Validate failed-classes rows, build CSV package, and dispatch via SMTP if configured."""
    if not all_rows:
        raise ReportEmailError(
            "Chưa có dữ liệu báo cáo trong tuần đã chọn để xuất CSV và gửi mail.",
            code="EMPTY_WEEKLY_SUMMARY",
            status_code=409,
        )
    if not failed_rows:
        raise ReportEmailError(
            "Không có lớp lỗi nào trong tuần đã chọn cần xuất CSV hoặc gửi mail.",
            code="NO_FAILED_CLASSES",
            status_code=409,
        )

    configured_recipients = getattr(
        settings, "report_email_recipients", DEFAULT_REPORT_EMAIL_RECIPIENTS
    )
    recipients = resolve_report_email_recipients(
        requested_recipients=requested_recipients,
        configured_recipients=configured_recipients,
    )
    package = build_failed_classes_email_package(
        week=week,
        year=year,
        failed_rows=failed_rows,
        total_rows_count=len(all_rows),
        selected_job_id=selected_job_id,
        recipients=recipients,
    )

    smtp_host = _clean_single_line(getattr(settings, "smtp_host", "smtp.gmail.com"), "")
    smtp_port = int(getattr(settings, "smtp_port", 587) or 587)
    smtp_username = _clean_single_line(getattr(settings, "smtp_username", ""), "")
    smtp_password = str(getattr(settings, "smtp_password", "") or "").strip()
    smtp_from = _clean_single_line(
        getattr(settings, "smtp_from", "") or smtp_username,
        "",
    )
    smtp_use_tls = bool(getattr(settings, "smtp_use_tls", True))
    isolated_quality = os.getenv("ZALO_QUALITY_ISOLATED") == "1"

    smtp_ready = bool(
        smtp_host and smtp_username and smtp_password and not isolated_quality
    )
    if not smtp_ready:
        return {
            "ok": True,
            "delivery_status": "SMTP_NOT_CONFIGURED",
            "message": (
                f"Đã xuất CSV {len(failed_rows)} lớp lỗi. Chưa cấu hình SMTP_USERNAME/SMTP_PASSWORD trong .env "
                f"nên hệ thống mở sẵn khung gửi mail tới {', '.join(recipients)}."
            ),
            "week": int(week),
            "year": int(year),
            "selected_job_id": selected_job_id,
            "total_count": len(all_rows),
            "failed_count": len(failed_rows),
            "recipients": recipients,
            "accepted_recipients": [],
            "refused_recipients": [],
            "available_managers": list(MANAGER_EMAIL_ALLOWLIST),
            "filename": package["filename"],
            "csv_text": package["csv_text"],
            "subject": package["subject"],
            "text_table": package["text_table"],
            "text_body": package["text_body"],
            "html_table": package["html_table"],
            "html_body": package["html_body"],
            "gmail_compose_url": package["gmail_compose_url"],
            "gmail_body_truncated": bool(package.get("gmail_body_truncated", False)),
            "mailto_url": package["mailto_url"],
        }

    msg = EmailMessage()
    msg["Subject"] = _clean_single_line(package["subject"], "[MindX BCNB] Báo cáo lớp lỗi")
    msg["From"] = smtp_from or smtp_username
    msg["To"] = ", ".join(recipients)
    msg.set_content(package["text_body"])
    msg.add_alternative(package["html_body"], subtype="html")
    msg.add_attachment(
        package["csv_text"].encode("utf-8"),
        maintype="text",
        subtype="csv",
        filename=package["filename"],
        params={"charset": "utf-8"},
    )

    try:
        refused_map = await asyncio.to_thread(
            _deliver_smtp_message,
            host=smtp_host,
            port=smtp_port,
            username=smtp_username,
            password=smtp_password,
            use_tls=smtp_use_tls,
            message=msg,
        )
    except Exception as exc:
        raise ReportEmailError(
            "Không thể gửi email tự động qua SMTP. Hãy kiểm tra cấu hình SMTP_HOST/SMTP_PORT/SMTP_USERNAME/SMTP_PASSWORD trong .env.",
            code="SMTP_SEND_FAILED",
            status_code=502,
            extra={
                "filename": package["filename"],
                "csv_text": package["csv_text"],
                "failed_count": len(failed_rows),
                "accepted_recipients": [],
                "refused_recipients": list(recipients),
            },
        ) from exc

    refused_lower = {
        str(addr).strip().lower()
        for addr in (refused_map.keys() if isinstance(refused_map, dict) else ())
        if str(addr).strip()
    }
    accepted_recipients = [
        email for email in recipients if email.lower() not in refused_lower
    ]
    refused_recipients = [
        email for email in recipients if email.lower() in refused_lower
    ]
    for raw_refused in sorted(refused_lower):
        if raw_refused not in refused_recipients:
            refused_recipients.append(raw_refused)
    if refused_recipients:
        accepted_text = ", ".join(accepted_recipients) if accepted_recipients else "không có"
        raise ReportEmailError(
            "Một số địa chỉ quản lý bị máy chủ SMTP từ chối: "
            f"{', '.join(refused_recipients)} (đã chấp nhận: {accepted_text}).",
            code="SMTP_RECIPIENT_REFUSED",
            status_code=502,
            extra={
                "filename": package["filename"],
                "csv_text": package["csv_text"],
                "failed_count": len(failed_rows),
                "accepted_recipients": accepted_recipients,
                "refused_recipients": refused_recipients,
            },
        )

    return {
        "ok": True,
        "delivery_status": "SENT",
        "message": (
            f"Đã xuất CSV {len(failed_rows)} lớp lỗi và gửi email tự động thành công tới {', '.join(accepted_recipients)}."
        ),
        "week": int(week),
        "year": int(year),
        "selected_job_id": selected_job_id,
        "total_count": len(all_rows),
        "failed_count": len(failed_rows),
        "recipients": recipients,
        "accepted_recipients": accepted_recipients,
        "refused_recipients": [],
        "available_managers": list(MANAGER_EMAIL_ALLOWLIST),
        "filename": package["filename"],
        "csv_text": package["csv_text"],
        "subject": package["subject"],
        "text_table": package["text_table"],
        "text_body": package["text_body"],
        "html_table": package["html_table"],
        "html_body": package["html_body"],
        "gmail_compose_url": package["gmail_compose_url"],
        "mailto_url": package["mailto_url"],
    }
