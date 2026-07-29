# Giai đoạn 4 — External Boundaries & Contract Testing

## Kết quả

Giai đoạn 4 tách quyền sở hữu SDK/browser khỏi orchestration mà không đổi HTTP
API, job payload, schema SQLite hoặc mode dry-run/production:

- `services/external_gateways.py` là ranh giới duy nhất tạo Playwright và Gemini
  LMS. Gateway quản lý persistent browser page, browser dùng chung theo batch
  Google Form, LMS OCR và factory OCR học viên.
- `WorkerRuntime.external` sở hữu bundle `ExternalGateways` theo từng app.
  `reset_locks()` chỉ thay lock và giữ nguyên gateway đã inject.
- Zalo Scan, Auto Discovery, LMS Analysis/Automation và Google Form Submit/OCR
  nhận gateway qua runtime. Ba orchestration service không còn khởi tạo trực
  tiếp `async_playwright()`, `genai.Client` hoặc `PIL.Image`.
- Gateway browser đóng context/browser trong `finally`, kể cả khi thành công,
  lỗi hoặc task bị hủy. Gemini LMS khởi tạo lazy và luôn đóng ảnh đầu vào.

Audit, idempotency, cancellation, plan digest và BCNB hard-reload/read-back vẫn
nằm trong service nghiệp vụ; adapter không được phép bỏ qua các lớp an toàn này.

## Hardening tại ranh giới

- LMS chỉ tự chấp nhận dialog BCNB loại `alert`/`confirm`; `prompt` và loại khác
  bị dismiss.
- Lỗi selector/credential trên form đăng nhập BCNB không còn bị hiểu nhầm là
  session đã đăng nhập.
- Question/value của Google Form được truyền qua `filter(has_text=...)`, không
  nội suy trực tiếp vào XPath/CSS selector.
- Nếu đã click Submit nhưng không quan sát được URL/trang xác nhận, audit ghi
  `UNKNOWN`. Hệ thống không tự retry và không khẳng định chắc chắn là `FAILED`.
- Batch Google Form dùng một browser, context riêng cho từng học viên và đóng
  browser rõ ràng khi kết thúc.

## Quality gate

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\CHECK_QUALITY.ps1
```

Baseline khi hoàn tất:

- 85 test hermetic, không gọi Zalo/BCNB/Gemini/Google Form thật.
- Production branch coverage 64,3%; gate bắt buộc 60,0%.
- `services/lms_service.py` tăng từ 0,0% lên 78,1%.
- Ruff đạt toàn workspace; mypy đạt 45 tệp production.
- Phase 3 và Phase 4 invariant gate, schema, active-job check, app smoke,
  frontend contract và compile đều đạt.

## Rollout và rollback

1. Chạy `QUALITY.bat` ở cấu hình dry-run.
2. Mở ứng dụng, xác nhận `/api/health/ready` trả `ready=true`.
3. Chạy một job mỗi loại ở dry-run và kiểm tra browser được đóng sau job.
4. Chỉ bật production thủ công cho job mới sau khi xem preview/audit.

Checkpoint ngay trước Giai đoạn 4:

`F:\FINAL_PROJECT\zaloAutomation\.backups\zaloAutomation-v3.4.0-before-phase4.1-20260715-141548.zip`

- Kích thước: 335.085.482 byte; 9.020 entry; không có entry lỗi.
- SHA-256: `09847E831434B6D84ADF094843A4D3F76268DBB7647FE303C2CC49959B5DB731`.
- Archive chứa `.env`, database và browser profile; phải giữ kín.

## Hướng tiếp theo

Ứng viên Giai đoạn 5: tách nhỏ orchestration Zalo đang có coverage 4,9%, bổ
sung contract test cho launch failure/cleanup của Zalo, tăng coverage dispatcher
và chuẩn hóa lỗi API để không trả chi tiết nội bộ.
