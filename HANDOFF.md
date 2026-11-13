# Bàn giao hiện hành — Zalo Automation

Điểm vào điều phối hiện hành của dự án tuân theo **[AGENTS.md](AGENTS.md)** (`DEC-036`, `DEC-040`, `DEC-047`), với yêu cầu trực tiếp của Owner và ranh giới an toàn Zalo/BCNB/LMS/Form luôn có độ ưu tiên cao nhất.

## Thứ tự đọc khi bắt đầu phiên mới

1. `AGENTS.md`: quy tắc an toàn dữ liệu, ranh giới thao tác ngoài và cơ chế 2 luồng kiểm chứng (`[FAST-TRACK]` / `[FULL GATE]`).
2. `PROJECT_SUMMARY.md`: kiến trúc và hợp đồng nghiệp vụ tổng quan.
3. `PROJECT_STATE.md`: snapshot ngắn của trạng thái đã kiểm chứng gần nhất (lịch sử chi tiết xem tại `CHANGELOG.md`).
4. `PROJECT_DECISIONS.md`: sổ quyết định kiến trúc & quy trình ghi nối tiếp (`DEC-001` → hiện tại).

## Môi trường Python & Kiểm chứng

- **Ứng dụng chính**: dùng `venv/Scripts/python.exe` và `requirements.txt` (không dùng Python toàn máy).
- **Công cụ workflow phụ trợ (`.ai-workflow/`)**: tách riêng ở `.venv/Scripts/python.exe` và `requirements-workflow.txt` (dữ liệu cục bộ `.workflow-local/` được Git bỏ qua).
- **Cổng kiểm chứng**:
  - **Luồng Nhanh (`[FAST-TRACK]`)**: unit test mục tiêu + `node tests/check_template_js.js` (nếu chạm JS/HTML) + `ruff check` & `mypy` phạm vi hẹp + `git diff --check`.
  - **Luồng Đầy đủ (`[FULL GATE]`)**: unit test mục tiêu + `node tests/check_template_js.js` + `.\CHECK_QUALITY.ps1 -Isolated` (`exit 0`) + Review độc lập `gpt-6.1-sol` (`read-only`) đạt `PASS`.
- **Đồng bộ**: Cập nhật `CHANGELOG.md` + `PROJECT_STATE.md`, commit các tệp trong `allowlist` (không bao giờ stage `.env`, `database.db`, browser profile hay dữ liệu nhạy cảm) và `git push` lên GitHub (`origin`).
