# UPGRADE ROADMAP — Zalo Automation (MindX Auto)

> Hướng phát triển và nâng cấp dự án trong tương lai.

> Giai đoạn 2 đã hoàn thành phần nền của mục 3, 4 và 17: browser lock,
> heartbeat/cancel/retry/recovery, upload isolation, external-action audit và bộ
> test tự động. Phần notification vẫn nằm trong roadmap.

> Mốc 3.1 đã hoàn thành structured rotating log, redaction, health/readiness,
> aggregate metrics, mode pinning và fail-closed Zalo/date verification.

> Mốc 3.2 đã hoàn thành app factory/container, per-app dispatcher/log/runtime
> isolation và router dependency injection.

> Mốc 3.3 đã hoàn thành việc tách frontend thành Jinja partial, CSS và
> JavaScript theo chức năng, đồng thời thêm contract gate DOM/script và baseline
> accessibility/motion. Notification vẫn nằm trong roadmap.

> Mốc 3.4 đã hoàn thành transaction clone upload, startup reconciliation,
> BCNB hard-reload read-back và loại toàn bộ `bare except`.

> Mốc 3.5 đã pin dependency phát triển, đưa Ruff/mypy/branch coverage vào một
> quality gate tái lập và giữ 61 regression test đạt. Giai đoạn 3 đã hoàn tất.

> **Giai đoạn 4 đã hoàn tất**: `ExternalGateways` tách Playwright/Gemini/Form
> khỏi orchestration; browser lifecycle và external-outcome audit được harden.
> Có 85 test, coverage production 64,3% và gate bắt buộc 60,0%.

---

## 🔴 Ưu tiên cao (Nên làm sớm)

### 1. Tích hợp LLM Classifier thay thế Rule-based

**Hiện tại**: File `llm_classifier.py` là **stub** — chưa gọi Gemini API thật.  
**Đề xuất**: Tích hợp Gemini API để phân loại tin nhắn chính xác hơn.

- Khi rule classifier cho điểm `2.0 ≤ score < 4.0` (vùng mập mờ) → fallback sang LLM
- LLM trả về: `{is_report, confidence, lesson_number, class_code, lesson_date}`
- Kết hợp 3-tier classifier: Rule → Embedding → LLM (cascade)
- Lưu log phân loại vào bảng `classification_logs` (đã có schema nhưng chưa ghi dữ liệu)
- **Lợi ích**: Giảm false positive/negative, đặc biệt với các báo cáo format không chuẩn

### 2. Tích hợp Embedding Classifier (BAAI/bge-m3)

**Hiện tại**: File `embedding_classifier.py` là **stub**.  
**Đề xuất**: Dùng model multilingual embedding để so sánh similarity.

- Tạo tập template báo cáo chuẩn → tính embedding
- So sánh cosine similarity tin nhắn mới với template
- `similarity > 0.92` → REPORT, `< 0.50` → NON_REPORT, else → UNCERTAIN (gọi LLM)
- **Lợi ích**: Nhanh hơn LLM, tốn ít token hơn, phù hợp cho tầng trung gian

### 3. Xử lý race condition khi chạy đồng thời

**Trạng thái**: Đã hoàn tất nền tảng ở Giai đoạn 2–4.

- Browser lock thuộc từng app và tuần tự hóa các job dùng persistent profile
- Job có heartbeat/cancel và UI khóa thao tác theo trạng thái đang chạy
- External gateway giữ ownership browser/context và luôn cleanup trong `finally`

### 4. Cải thiện Error Handling & Retry

**Trạng thái**: Structured log, rotation/redaction, không còn `bare except`,
audit external outcome và fail-closed đã hoàn tất. Hướng tiếp theo:

- Phân loại timeout selector theo adapter thay vì retry mù
- Chuẩn hóa lỗi API để không trả chi tiết nội bộ
- Gửi notification (Telegram/Zalo) khi job thất bại

---

## 🟡 Ưu tiên trung bình (Nâng cấp trải nghiệm)

### 5. WebSocket thay thế Polling

**Hiện tại**: Frontend poll `GET /api/jobs/{id}` mỗi 2 giây.  
**Đề xuất**: Dùng FastAPI WebSocket để push real-time.

- Giảm tải HTTP request không cần thiết
- Log terminal cập nhật tức thì (không cần poll `/api/logs`)
- Progress bar mượt mà hơn
- **Tech**: FastAPI WebSocket + JavaScript `new WebSocket()`

### 6. Lên lịch quét tự động (Scheduler)

**Hiện tại**: User phải bấm nút thủ công mỗi lần quét.  
**Đề xuất**: Thêm cron scheduler tự động chạy hàng tuần.

- Cấu hình ngày/giờ quét trên UI (ví dụ: mỗi Thứ 2 lúc 8:00 AM)
- Dùng `APScheduler` hoặc `asyncio` background task
- Gửi báo cáo tóm tắt (Telegram/Email) sau khi quét xong
- Hỗ trợ retry nếu quét thất bại

### 7. Export kết quả (Excel/PDF)

**Hiện tại**: Kết quả chỉ hiển thị trên web.  
**Đề xuất**: Cho phép export.

- Export bảng kết quả quét → file Excel (.xlsx)
- Export lịch sử nộp Google Form → CSV
- Báo cáo tổng hợp theo tuần → PDF (kèm biểu đồ)
- Nút "Tải về" trên UI

### 8. Quản lý người dùng & Phân quyền

**Hiện tại**: Không có authentication, ai mở được URL đều truy cập được.  
**Đề xuất**:

- Thêm login page (username/password hoặc Google OAuth)
- Phân quyền: Admin (toàn quyền) vs. Viewer (chỉ xem)
- JWT token cho API authentication
- Session management

### 9. Dashboard Analytics nâng cấp

**Hiện tại**: Chỉ có 4 metrics cards + 1 donut chart.  
**Đề xuất**:

- Biểu đồ xu hướng theo tuần (line chart): tỷ lệ nộp đúng hạn qua các tuần
- Ranking giáo viên (ai nộp đúng hạn nhất)
- Heatmap ngày nộp báo cáo
- Thống kê theo chi nhánh/khu vực
- Lưu lịch sử kết quả quét (hiện tại chỉ lưu job cuối cùng)

### 10. Tách Frontend thành SPA riêng

**Hiện tại**: Frontend đã tách thành Jinja partial, CSS và 7 tệp JavaScript;
facade `index.html` còn 37 dòng.  
**Đề xuất**: Chỉ chuyển sang SPA khi cần state hoặc luồng giao diện phức tạp hơn
mô hình hiện tại.

- Chuyển sang React/Vue/Svelte + Vite
- Tách components: Sidebar, MetricsCards, ResultsTable, LogTerminal, FormUploader...
- State management (Zustand/Pinia)
- Build & serve static files riêng biệt
- **Lợi ích**: Dễ bảo trì, test, mở rộng

---

## 🟢 Ưu tiên thấp (Tính năng bổ sung)

### 11. Hỗ trợ nhiều trung tâm

**Hiện tại**: Hardcode cho 1 tài khoản BCNB.  
**Đề xuất**:

- Multi-tenant: mỗi trung tâm/chi nhánh có tài khoản riêng
- UI chọn chi nhánh trước khi quét
- Database tách biệt theo tenant (hoặc thêm cột `tenant_id`)

### 12. Mobile Responsive

**Hiện tại**: Sidebar ẩn trên mobile (`hidden md:flex`), nhưng chưa có menu hamburger.  
**Đề xuất**:

- Thêm hamburger menu cho mobile
- Responsive layout cho các bảng dữ liệu (card view thay table trên mobile)
- Touch-friendly buttons

### 13. API Documentation (OpenAPI/Swagger)

**Hiện tại**: FastAPI tự generate Swagger UI tại `/docs`, nhưng chưa có mô tả chi tiết.  
**Đề xuất**:

- Thêm docstring cho mỗi endpoint
- Mô tả request/response schema
- Ví dụ (examples) cho mỗi API
- Tài liệu hướng dẫn tích hợp cho bên thứ 3

### 14. Docker Containerization

**Hiện tại**: Chạy trực tiếp trên Windows với venv.  
**Đề xuất**:

- Dockerfile + docker-compose.yml
- Tách Playwright browser thành container riêng
- Dễ deploy lên server Linux (VPS/Cloud)
- CI/CD pipeline (GitHub Actions)

### 15. Tính năng Backup & Restore Database

**Hiện tại**: Database là file SQLite đơn giản, không có backup.  
**Đề xuất**:

- Auto backup database hàng ngày
- UI cho phép download/upload backup
- Migration script khi thay đổi schema

### 16. Notification System

**Hiện tại**: Không có thông báo ngoài web.  
**Đề xuất**:

- Gửi thông báo qua Telegram Bot khi:
  - Quét hoàn tất (kèm tóm tắt kết quả)
  - Có lớp trễ báo cáo
  - Job thất bại
- Tích hợp Zalo Notification API (nếu có)
- Email summary hàng tuần

### 17. Unit Tests & Integration Tests

**Hiện tại**: Có 85 regression/integration/contract test và branch coverage
production 64,3%, được khóa bởi gate 60,0%.  
**Đề xuất**:

- Tách adapter Zalo/BCNB, Gemini và Google Form để mock/contract-test rõ ràng
- Bổ sung nhánh lỗi, timeout và kết quả không xác định của Playwright
- Nâng branch coverage production lên ít nhất 60% trước khi mở rộng tính năng
- Giữ test hermetic, không gọi hoặc ghi thật ra dịch vụ ngoài

### 18. Nâng cấp Zalo Session Management

**Hiện tại**: Dùng persistent browser context (`./zalo_session`). Nếu session hết hạn phải quét QR lại thủ công.  
**Đề xuất**:

- Phát hiện session expired tự động → thông báo user
- Hướng dẫn trên UI cách login lại
- Backup/restore session folder
- Hỗ trợ nhiều tài khoản Zalo (multi-account)

---

## 📋 Tóm tắt Priority Matrix

| # | Tính năng | Độ khó | Tác động | Ưu tiên |
|---|-----------|--------|----------|---------|
| 1 | LLM Classifier | Trung bình | Cao | 🔴 Cao |
| 2 | Embedding Classifier | Trung bình | Cao | 🔴 Cao |
| 3 | Race condition fix | Thấp | Cao | ✅ Hoàn tất nền tảng |
| 4 | Error Handling & Retry | Thấp | Trung bình | ✅ Hoàn tất nền tảng |
| 5 | WebSocket real-time | Trung bình | Trung bình | 🟡 Trung bình |
| 6 | Scheduler tự động | Trung bình | Cao | 🟡 Trung bình |
| 7 | Export Excel/PDF | Thấp | Trung bình | 🟡 Trung bình |
| 8 | Authentication | Trung bình | Trung bình | 🟡 Trung bình |
| 9 | Dashboard Analytics | Trung bình | Trung bình | 🟡 Trung bình |
| 10 | Tách Frontend SPA | Cao | Thấp | 🟡 Trung bình |
| 11 | Multi-tenant | Cao | Thấp | 🟢 Thấp |
| 12 | Mobile Responsive | Thấp | Thấp | 🟢 Thấp |
| 13 | API Documentation | Thấp | Thấp | 🟢 Thấp |
| 14 | Docker | Trung bình | Trung bình | 🟢 Thấp |
| 15 | Backup Database | Thấp | Thấp | 🟢 Thấp |
| 16 | Notification (Telegram) | Thấp | Trung bình | 🟢 Thấp |
| 17 | Unit Tests | Trung bình | Trung bình | ✅ Gate 60% |
| 18 | Zalo Session Management | Trung bình | Trung bình | 🟢 Thấp |
