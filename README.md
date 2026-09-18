# Zalo Automation / MindX Auto

Ứng dụng Windows chạy cục bộ để hỗ trợ ba công việc:

1. Dò lớp từ BCNB, tìm báo cáo trong nhóm Zalo và lập kế hoạch cập nhật BCNB.
2. Đọc ảnh danh sách học viên bằng Gemini và điền Google Form.
3. Đọc ảnh tình trạng LMS và lập kế hoạch tích LMS trên BCNB.

Ứng dụng dùng FastAPI, Python, Playwright, Gemini, SQLite, Jinja2 và JavaScript.

## Đọc tài liệu theo thứ tự

1. [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) — chức năng và kiến trúc hiện tại.
2. [PROJECT_STATE.md](PROJECT_STATE.md) — trạng thái, bằng chứng và việc còn mở.
3. [PROJECT_COMPLETION_PLAN.md](PROJECT_COMPLETION_PLAN.md) — thứ tự hoàn thiện tiếp theo.
4. [PROJECT_DECISIONS.md](PROJECT_DECISIONS.md) — các quyết định không được tự ý đảo ngược.
5. [CLASS_LIFECYCLE.md](CLASS_LIFECYCLE.md) — quy tắc vòng đời lớp.
6. [VERSION.md](VERSION.md) — lịch sử thay đổi cấp cao.

AI hoặc người phát triển mới phải đọc `AGENTS.md` trước khi sửa dự án.

## Khởi động trên máy hiện tại

1. Giữ file `.env` ở máy cục bộ; không gửi file này cho người khác.
2. Chạy `START.bat`.
3. Mở `http://127.0.0.1:8000` nếu trình duyệt không tự mở.
4. Kiểm tra `http://127.0.0.1:8000/api/health/ready`. Thành công khi có
   `"ready": true`.

`START.bat` dùng môi trường Python trong `venv` và chạy
`python -m scripts.run_local`. Chỉ chạy một tiến trình ứng dụng vì SQLite và
profile Zalo đang được thiết kế cho một máy/một người vận hành.

## Kiểm tra chất lượng

Chạy tại thư mục gốc của dự án:

```powershell
.\CHECK_QUALITY.ps1 -Isolated
```

Chế độ `-Isolated` dùng dữ liệu tạm và chặn trình duyệt/dịch vụ thật. Một lần
kiểm tra xanh chỉ chứng minh mã nguồn chạy được trong môi trường cách ly; không
phải quyền ghi Zalo, BCNB, Google Form hoặc triển khai production.

## Quy tắc an toàn

- `.env`, `database.db`, `runtime/`, ảnh bằng chứng và `zalo_session/` không
  được đưa vào Git.
- Thiếu hoặc mơ hồ về lớp, tuần, ngày, nhóm Zalo hay kết quả ghi phải dừng để
  người vận hành kiểm tra.
- Không chạy thao tác thật chỉ vì `.env` đang để chế độ production. Mỗi lần chạy
  thật vẫn cần đúng mục tiêu, bản xem trước mới và xác nhận riêng của Owner.
- Trước khi cập nhật hoặc triển khai, phải sao lưu SQLite và giữ khả năng quay
  lại phiên bản trước.
