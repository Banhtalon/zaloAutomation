# VERSION HISTORY — Zalo Automation (MindX Auto)

> Ghi lại lịch sử phát triển các phiên bản của dự án.

## Vòng đời lớp và chuyển cấp

- Vắng trên BCNB được xem là tạm nghỉ/chờ chuyển cấp, không tự động kết thúc.
- Hỗ trợ chuỗi PTB→PTA→PTI, GB→GA→GI và SB→SA→SI.
- Lưu lịch sử mã lớp, số buổi, xác nhận kết thúc thủ công và lưu trữ sau 14 ngày.
- Robotics vẫn dừng an toàn cho tới khi thứ tự chuyển cấp thực tế được xác nhận.

---

## v0.1.0 — Prototype (Giai đoạn khởi tạo)

**Mô tả**: Phiên bản đầu tiên, chỉ có chức năng quét Zalo cơ bản.

- ✅ Kết nối Zalo Web bằng Playwright (persistent session)
- ✅ Quét tin nhắn trong các nhóm Zalo
- ✅ Phân loại tin nhắn đơn giản (keyword matching)
- ✅ Hiển thị kết quả trên terminal

---

## v0.2.0 — Web Dashboard & Database

**Mô tả**: Chuyển từ script terminal sang ứng dụng web có giao diện.

- ✅ Tích hợp FastAPI làm backend server
- ✅ Database SQLite (bảng `groups`, `ignored_groups`)
- ✅ Giao diện HTML đầu tiên (Jinja2 template)
- ✅ API quản lý danh sách nhóm (`/api/groups`)

---

## v0.3.0 — Auto Discovery từ BCNB

**Mô tả**: Tự động đồng bộ danh sách lớp từ hệ thống BCNB.

- ✅ Tính năng `Auto Discovery` — quét BCNB lấy mã lớp, giáo viên, ngày học
- ✅ Thuật toán "Lõi Kép" (Core Matching) ghép tên nhóm Zalo ↔ Mã lớp BCNB
- ✅ Tự động phân loại nhóm: hoạt động vs. kết thúc (ignored)
- ✅ Auto login BCNB bằng credentials từ `.env`

---

## v0.4.0 — Rule-based Classifier nâng cấp

**Mô tả**: Cải thiện độ chính xác phân loại báo cáo.

- ✅ Hệ thống Scoring phân loại báo cáo (5 categories + 1 penalty)
- ✅ Trích xuất ngày tháng thông minh (3 mức fallback: dd/mm/yyyy → dd/mm → "ngày d tháng m")
- ✅ Tính toán trễ/đúng hạn dựa trên ngày học từ BCNB
- ✅ Penalty cho tin nhắn điểm danh đơn thuần
- ✅ Xác minh nhóm Zalo đang mở có đúng không (chống "mở nhầm nhóm")

---

## v0.5.0 — Smart Scroll & Tối ưu quét

**Mô tả**: Tối ưu tốc độ và độ tin cậy khi quét Zalo.

- ✅ Smart Scroll: dừng cuộn sớm nếu tin nhắn > 6 ngày
- ✅ Bấm "Xem thêm" tự động để bung tin nhắn dài
- ✅ Chụp ảnh khung chat (screenshot) lưu bằng chứng
- ✅ Kiểm tra nhóm ẩn — phục hồi nếu có "Thông báo khai giảng"

---

## v1.0.0 — Auto Tick BCNB

**Mô tả**: Hoàn thiện flow end-to-end: Quét Zalo → Tick BCNB tự động.

- ✅ Tự động tick checkbox trên BCNB cho các lớp đã nộp báo cáo
- ✅ Nhận diện Buổi 14 (miễn báo cáo) → tự động tick
- ✅ Chọn đúng tuần (tuần trước) trên BCNB
- ✅ Xử lý popup xác nhận (SweetAlert2) sau khi tick

---

## v1.5.0 — Google Form Auto Submit

**Mô tả**: Tính năng mới — tự động nộp Google Form phản hồi cho học viên.

- ✅ Module `googleform_autosubmit` hoàn chỉnh
- ✅ OCR bằng Gemini Vision — trích xuất tên học viên + mã lớp từ ảnh chụp
- ✅ Auto-fill Google Form: text fields + radio buttons
- ✅ Chuyển trang thông minh (multi-page Google Form)
- ✅ Phát hiện reCAPTCHA → dừng an toàn
- ✅ Chế độ `dry-run` (mô phỏng) và `production` (nộp thật)
- ✅ Cấu hình Q&A Mapping trên UI (CRUD)
- ✅ Lịch sử nộp form (bảng `form_submissions`)
- ✅ Rate limit + concurrency control

---

## v2.0.0 — Premium Dashboard & LMS Auto Tick (Phiên bản hiện tại)

**Mô tả**: Nâng cấp toàn diện giao diện + thêm tính năng LMS Auto Tick.

### Giao diện
- ✅ Dark mode premium (Glassmorphism design)
- ✅ 4-tab layout: Zalo Auto | Nộp Google Form | Cấu hình Nhóm | Nhật ký
- ✅ Metrics cards (Tổng / Hoàn thành / Miễn / Trễ) với glow effects
- ✅ Real-time status indicator (pulse dot animation)
- ✅ TailwindCSS CDN + Font Awesome + Google Fonts (Inter, Fira Code)
- ✅ Chart.js donut chart cho thống kê
- ✅ Image modal preview (xem ảnh chụp báo cáo)
- ✅ Responsive sidebar navigation

### LMS Auto Tick
- ✅ Upload ảnh danh sách lớp chưa nhận xét LMS (drag/drop/paste/click)
- ✅ Gemini Vision AI đọc mã lớp từ ảnh
- ✅ Tự động tick cột LMS trên BCNB (logic đảo ngược — tick lớp KHÔNG nằm trong danh sách thiếu)

### Background Job System
- ✅ Tất cả tác vụ nặng chạy background (FastAPI BackgroundTasks)
- ✅ Bảng `scan_jobs` theo dõi progress
- ✅ Frontend polling mỗi 2 giây cập nhật trạng thái

### Phân loại (Classifiers)
- ✅ `rule_classifier.py` hoạt động chính thức
- ⬜ `llm_classifier.py` — Stub (chưa tích hợp Gemini API)
- ⬜ `embedding_classifier.py` — Stub (chưa tích hợp BAAI/bge-m3)

---

## v2.1.0 — Reliability & Audit (Giai đoạn 2)

**Mô tả**: Tách dữ liệu theo job và bổ sung cơ chế phục hồi có kiểm soát.

- ✅ Upload bằng ID và claim nguyên tử vào thư mục input riêng của job
- ✅ Job dispatcher bền vững: heartbeat, cancel, retry lineage và recovery
- ✅ Không tự phát lại tác vụ ghi thật bị gián đoạn
- ✅ Event timeline và audit before/intended/after cho thao tác bên ngoài
- ✅ Google Form idempotency để chặn nộp trùng cùng batch
- ✅ Đối sánh mã lớp đầy đủ, xác nhận tuần/năm, header và checkbox BCNB
- ✅ UI có heartbeat, Hủy job, Thử lại và timeline audit
- ✅ Bộ test Giai đoạn 2 cho isolation, state machine, recovery và audit

Chi tiết vận hành: `PHASE2_RELIABILITY.md`.

---

## v3.0.0 — Maintainability & Operability (Giai đoạn 3, mốc 3.1)

**Mô tả**: Khóa hành vi an toàn theo job và bổ sung khả năng vận hành cục bộ.

- ✅ Mode dry-run/production bất biến theo payload job
- ✅ LMS digest gắn với mode; config flip không thể biến preview thành ghi thật
- ✅ Zalo chat identity và ngày báo cáo fail-closed
- ✅ Shared BCNB week/header safety helpers
- ✅ Structured JSONL rotating logs, context/cursor và secret redaction
- ✅ Liveness, readiness và aggregate job metrics
- ✅ Hermetic smoke test và Phase 3 invariant gate

Chi tiết: `PHASE3_MAINTAINABILITY.md`.

---

## v3.1.0 — Per-app Runtime Isolation (Giai đoạn 3, mốc 3.2)

- ✅ `create_app(settings)` và `AppContainer` composition root
- ✅ Settings snapshot immutable và database context theo app/task
- ✅ Dispatcher/task registry, upload store, worker locks và logger theo app
- ✅ Router dependency injection; legacy dispatcher chỉ còn lazy shim
- ✅ Hai app/database/log độc lập và restart lifecycle ba lần
- ✅ Loopback Host/Origin, request ID và body-size security guards
- ✅ Readiness/metrics offload khỏi event loop và có index thời gian
- ✅ 47 regression/integration test

Chi tiết: `PHASE3_MAINTAINABILITY.md`.

---

## v3.2.0 — Frontend Modularization (Giai đoạn 3, mốc 3.3)

- ✅ `templates/index.html` còn 37 dòng và chỉ làm facade Jinja
- ✅ Tách 8 partial theo khu vực/tab/modal, giữ nguyên DOM ID và API
- ✅ Tách CSS và 7 tệp JavaScript classic theo chức năng, giữ thứ tự tải
- ✅ Contract gate kiểm tra cú pháp, global event handlers và tài nguyên tĩnh
- ✅ Smoke test render toàn trang và tải CSS/JavaScript qua FastAPI
- ✅ Dùng `h-dvh`, reduced motion, safe area và nhãn cho nút chỉ có biểu tượng
- ✅ Thời gian phản hồi tương tác không vượt quá 200 ms

Chi tiết: `PHASE3_MAINTAINABILITY.md`.

---

## v3.3.0 — Atomic Upload & BCNB Read-back (Giai đoạn 3, mốc 3.4)

- ✅ Clone nhiều upload bằng một SQLite transaction, không còn batch nửa vời
- ✅ Copy qua file tạm, `fsync`, kiểm tra size/SHA-256 rồi mới promote nguyên tử
- ✅ Startup reconciliation dọn file pending/mồ côi trước khi recover job
- ✅ BCNB hard reload, chọn lại đúng tuần/năm và tìm duy nhất mã lớp/buổi
- ✅ Audit chỉ `VERIFIED` sau server read-back; lỗi không xác định ghi `UNKNOWN`
- ✅ Action key chứa tuần/năm/lớp/buổi và đối soát intent dang dở trước khi retry
- ✅ Loại toàn bộ `bare except`; ngày lớp sai bị bỏ qua fail-closed
- ✅ 61 test tự động cùng static quality gate cho các invariant mốc 3.4

Chi tiết: `PHASE3_MAINTAINABILITY.md`.

---

## v3.4.0 — Reproducible Quality Gate (Giai đoạn 3, mốc 3.5)

- ✅ Pin `coverage==7.15.0`, `ruff==0.15.20`, `mypy==2.2.0`
- ✅ Ruff critical lint đạt trên toàn workspace
- ✅ Mypy đạt toàn bộ 44 file production với `check_untyped_defs`
- ✅ Production branch coverage baseline 53,4%, gate chống tụt 52,0%
- ✅ Sửa 22 lỗi lint và 21 lỗi type; chỉ giữ 2 ignore hẹp cho Gemini type stub
- ✅ `CHECK_QUALITY.ps1` chạy lint/type/coverage/test/invariant/schema/smoke/JS/compile
- ✅ `QUALITY.bat` cung cấp entrypoint một lần bấm trên Windows
- ✅ 61 regression test tiếp tục đạt; Giai đoạn 3 hoàn tất

Chi tiết: `PHASE3_MAINTAINABILITY.md`.

---

## v4.0.0 — External Boundaries & Contract Testing (Giai đoạn 4)

- ✅ `ExternalGateways` tách Playwright/Gemini/Form khỏi orchestration service
- ✅ Gateway được sở hữu theo app qua `WorkerRuntime`, inject được fake hermetic
- ✅ Browser/context đóng trong `finally` trên success, error và cancellation
- ✅ LMS orchestration có contract test cho preview, drift, dry-run, partial và cancel
- ✅ BCNB login không nuốt lỗi; dialog chỉ accept `alert`/`confirm`
- ✅ Google Form selector không nội suy user text; kết quả submit mơ hồ audit `UNKNOWN`
- ✅ Repository/evidence test dùng SQLite và filesystem tạm, không chạm dữ liệu thật
- ✅ 85 test đạt; production branch coverage 64,3%, gate bắt buộc 60,0%
- ✅ Ruff đạt toàn workspace; mypy đạt 45 tệp production

Chi tiết: `PHASE4_EXTERNAL_BOUNDARIES.md`.

---

## v5.0.1 — Discovery Short-code Matching

- ✅ Sửa nhóm dùng mã rút gọn như `PTI09`/`PTI10` bị nhận nhầm là đã kết thúc
- ✅ Ưu tiên mã BCNB đầy đủ; chỉ dùng alias rút gọn khi ánh xạ tới đúng một mã
- ✅ Alias mơ hồ tiếp tục fail-closed, không ảnh hưởng so khớp nghiêm ngặt khi quét/ghi BCNB
- ✅ Kiểm thử thực tế job #90: 46 lớp hoạt động, 11 lớp kết thúc; PTI09/PTI10 đã phục hồi
- ✅ 106 test đạt; production branch coverage 71,7%

---

## v5.0.0 — Service Hardening & Error Contracts (Giai đoạn 5)

- ✅ Tách Auto Discovery thành service riêng, giữ route/payload/re-export tương thích
- ✅ Browser gateway được kiểm thử cleanup ở launch, page setup, init script, lỗi và hủy
- ✅ Dispatcher có contract test cho 6 loại job, retry, recovery, shutdown và cancel
- ✅ API trả mã lỗi ổn định, không lộ exception/đường dẫn/secret ra client hoặc job message
- ✅ Phân biệt `JobNotFoundError` bằng kiểu lỗi, không còn dò chuỗi thông báo
- ✅ 105 test đạt; production branch coverage 71,7%, gate bắt buộc 70,0%
- ✅ Ruff, mypy, invariant Giai đoạn 3/4/5, schema, smoke, frontend và compile đều đạt

Chi tiết: `PHASE5_SERVICE_HARDENING.md`.

---

## Tóm tắt trạng thái hiện tại

| Thành phần | Trạng thái | Ghi chú |
|------------|-----------|---------|
| Zalo Auto Scan | ✅ Production | Hoạt động ổn định |
| Auto Discovery (BCNB) | ✅ Production | Hoạt động ổn định |
| Auto Tick BCNB | ✅ Production | Hoạt động ổn định |
| Google Form OCR | ✅ Production | Dùng Gemini Vision |
| Google Form Submit | ✅ Production | Có dry-run mode |
| LMS Auto Tick | ✅ Production | Dùng Gemini Vision |
| Rule Classifier | ✅ Production | Scoring-based, hoạt động tốt |
| LLM Classifier | ⬜ Stub | Chưa implement |
| Embedding Classifier | ⬜ Stub | Chưa implement |
| Dashboard UI | ✅ Production | Glassmorphism design |
| Background Jobs | ✅ Production | Polling mỗi 2s |
