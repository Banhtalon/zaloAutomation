# PROJECT SUMMARY — Zalo Automation (MindX Auto)

> **Mục đích**: File này là tài liệu tham chiếu để AI (hoặc developer mới) có thể hiểu toàn bộ dự án trong thời gian ngắn nhất.

> **Cập nhật Giai đoạn 2**: Job không còn dùng `FastAPI BackgroundTasks` làm
> nguồn trạng thái duy nhất. Hệ thống hiện dùng SQLite-backed dispatcher,
> upload theo `upload_id`/job, heartbeat/cancel/retry/recovery và external-action
> audit. Xem `PHASE2_RELIABILITY.md` cho contract vận hành mới nhất.

> **Bắt đầu Giai đoạn 3 (mốc 3.1)**: Mode thực thi đã được đóng băng theo job,
> Zalo/date verification chuyển sang fail-closed, BCNB safety helper dùng chung,
> đồng thời có structured rotating log, health/readiness, aggregate metrics và
> quality gate. Xem `PHASE3_MAINTAINABILITY.md`.

> **Mốc 3.2**: Ứng dụng đã có `create_app()`/`AppContainer`, settings bất biến,
> database/log/dispatcher/worker-lock theo từng app, router dependency injection,
> local Host/Origin/body/request-id guard và metrics không chặn event loop.

> **Mốc 3.3**: Frontend đã tách thành Jinja partial, CSS và JavaScript theo tính
> năng. Facade `index.html` còn 37 dòng; DOM/API và thiết kế hiện hữu được giữ
> nguyên, có contract gate và smoke test bảo vệ.

> **Mốc 3.4**: Clone upload đã failure-atomic theo batch và có startup
> reconciliation. Ghi BCNB chỉ được audit `VERIFIED` sau hard reload/read-back
> đúng tuần, mã lớp và buổi; production không còn `bare except`.

> **Mốc 3.5 — hoàn tất Giai đoạn 3**: Dependency phát triển đã được pin; Ruff
> đạt toàn workspace, mypy đạt trên 44 tệp production và branch coverage
> production đạt 53,4% với gate 52,0%. Toàn bộ cổng chất lượng chạy qua
> `CHECK_QUALITY.ps1` hoặc `QUALITY.bat`.

> **Giai đoạn 4 — hoàn tất**: `ExternalGateways` tách Playwright/Gemini/Form
> khỏi orchestration và được inject theo app qua `WorkerRuntime`. Browser lifecycle,
> Google Form audit `UNKNOWN` và BCNB dialog/login đã được harden. Có 85 test,
> branch coverage production 64,3% và gate bắt buộc 60,0%. Xem
> `PHASE4_EXTERNAL_BOUNDARIES.md`.

---

## 1. Tổng quan dự án

**Zalo Automation** (tên giao diện: **MindX Auto**) là hệ thống tự động hóa quy trình quản lý báo cáo giảng dạy cho trung tâm **MindX**. Hệ thống gồm 3 tính năng chính:

| # | Tính năng | Mô tả |
|---|-----------|-------|
| 1 | **Zalo Auto Scan** | Tự động quét các nhóm Zalo của giáo viên, phân loại tin nhắn để xác định báo cáo buổi học, rồi tick kết quả lên hệ thống BCNB (Bảng Chấm Nhận Bằng). |
| 2 | **Google Form Auto Submit** | Dùng AI (Gemini Vision) để OCR ảnh chụp danh sách học viên, sau đó tự động điền và nộp Google Form phản hồi cho từng học viên. |
| 3 | **LMS Auto Tick** | Dùng AI (Gemini Vision) để đọc ảnh danh sách lớp chưa nhận xét LMS, sau đó tự động tick checkbox trên BCNB cho các lớp đã hoàn thành. |

---

## 2. Tech Stack

| Thành phần | Công nghệ |
|------------|-----------|
| **Backend Framework** | FastAPI (Python 3.x) + Uvicorn |
| **Browser Automation** | Playwright (Chromium, persistent session) |
| **AI / LLM** | Google Gemini API (`google-genai` SDK) — model `gemini-3.1-flash-lite` |
| **Database** | SQLite (file `database.db`) + `aiosqlite` cho async |
| **Frontend** | Jinja2 partials + CSS/JavaScript classic modules + TailwindCSS CDN + Chart.js + Font Awesome |
| **Templating** | Jinja2 (FastAPI `TemplateResponse`) |
| **Image Processing** | Pillow (PIL) |
| **Config** | python-dotenv (file `.env`) |
| **Quality** | unittest (85 test) + branch coverage 64,3% (gate 60%) + Ruff + mypy |
| **OS** | Windows (file `START.bat` để khởi chạy) |

---

## 3. Cấu trúc thư mục

```
zaloAutomation/
├── main.py                          # Entry point — khởi tạo FastAPI app, mount routers
├── START.bat                        # Script khởi chạy (activate venv + run uvicorn + mở Chrome)
├── requirements.txt                 # Dependencies
├── requirements-dev.txt             # Development dependencies được pin
├── pyproject.toml                    # Cấu hình Ruff, mypy và coverage
├── CHECK_QUALITY.ps1                 # Cổng chất lượng tái lập đầy đủ
├── QUALITY.bat                       # Entry point một lần bấm trên Windows
├── .env / .env.example              # Biến môi trường (API keys, credentials)
├── database.db                      # SQLite database file
├── logger_utils.py                  # Hệ thống log in-memory (system_logs[])
│
├── api/                             # FastAPI Routers (API Layer)
│   ├── __init__.py
│   ├── view_routes.py               # GET / (serve HTML), GET /api/logs, GET /favicon.ico
│   ├── zalo_routes.py               # POST /auto-discover, POST /run-scan, GET /api/groups
│   ├── googleform_routes.py         # CRUD mappings, upload image, run OCR, submit forms
│   ├── lms_routes.py                # POST /api/lms/upload-and-run
│   └── job_routes.py                # GET /api/jobs/{id}, GET /api/jobs/active/latest
│
├── services/                        # Business Logic Layer
│   ├── __init__.py
│   ├── zalo_service.py              # [CORE] Điều phối quét Zalo + ghi BCNB an toàn
│   ├── googleform_service.py        # Logic OCR + nộp Google Form
│   ├── lms_service.py               # Logic AI Vision + tick LMS trên BCNB
│   ├── external_gateways.py         # Ranh giới Playwright/Gemini/Form inject theo app
│   └── ocr_service.py               # Wrapper OCRProcessor (ít sử dụng trực tiếp)
│
├── classifiers/                     # Hệ thống phân loại tin nhắn
│   ├── __init__.py
│   ├── rule_classifier.py           # [ACTIVE] Scoring-based classifier (regex + keyword matching)
│   ├── llm_classifier.py            # [STUB] Placeholder cho Gemini LLM classifier
│   └── embedding_classifier.py      # [STUB] Placeholder cho embedding classifier (BAAI/bge-m3)
│
├── models/                          # Pydantic Models (Data Schema)
│   ├── group.py                     # GroupBase, Group, IgnoredGroup
│   ├── job.py                       # JobStatus, JobCreate, Job
│   └── report.py                    # ReportCreate, Report
│
├── repositories/                    # Data Access Layer (SQLite)
│   ├── group_repository.py          # CRUD groups + ignored_groups
│   ├── job_repository.py            # CRUD scan_jobs
│   └── report_repository.py         # CRUD form_mappings, form_submissions, reports, classification_logs
│
├── db/
│   └── database.py                  # Khởi tạo DB schema (7 bảng), async connection context manager
│
├── googleform_autosubmit/           # Module Google Form riêng biệt
│   ├── __init__.py
│   ├── config.py                    # Config class (đọc từ .env)
│   ├── form_submitter.py            # FormSubmitter — tự động điền và nộp Google Form bằng Playwright
│   └── ocr_processor.py             # OCRProcessor — dùng Gemini Vision để OCR ảnh → JSON [{name, class_code}]
│
├── templates/
│   ├── index.html                   # Facade Jinja 37 dòng, giữ thứ tự tài nguyên
│   └── partials/                    # Sidebar, header, 4 vùng tab và 2 modal
│
├── static/
│   ├── css/app.css                  # Style giao diện dùng chung
│   ├── js/                          # State, groups, jobs, Google Form, LMS, modal
│   └── screenshots/                 # Ảnh chụp màn hình Zalo được lưu tự động khi quét
│
├── zalo_session/                    # Chromium persistent user data (session Zalo đã login)
├── temp_lms/                        # Thư mục chứa ảnh tạm cho LMS Auto Tick
└── venv/                            # Python virtual environment
```

---

## 4. Database Schema (SQLite — 7 bảng)

| Bảng | Mục đích |
|------|----------|
| `groups` | Lưu danh sách nhóm Zalo đang hoạt động (group_name, person_in_charge, class_date) |
| `ignored_groups` | Nhóm đã kết thúc khóa / bị loại bỏ (group_name) |
| `form_mappings` | Cấu hình Q&A mapping cho Google Form (question, field_type, value) |
| `form_submissions` | Lịch sử nộp Google Form (student_name, class_code, status, error_message) |
| `scan_jobs` | Tiến trình quét/job (job_type, status, progress, message, results) |
| `reports` | Báo cáo buổi học (group_name, class_code, lesson_number, lesson_date, confidence) |
| `classification_logs` | Log phân loại tin nhắn (message, rule_score, embedding_score, llm_result, final_result) |

---

## 5. Các Flow chính

### 5.1. Zalo Auto Discovery (`POST /auto-discover`)
1. Mở Chromium → truy cập trang BCNB (`teachingmindx.top/check_nhanxet.php`) → auto login
2. Chọn tuần trước → đọc bảng dữ liệu lấy: mã lớp, ngày học, giáo viên
3. Chuyển sang Zalo Web → tìm kiếm "MindX -" → cuộn để gom tất cả tên nhóm
4. Đối chiếu tên nhóm Zalo ↔ mã lớp BCNB bằng **thuật toán "Lõi Kép"** (tách mã lớp theo dấu `-`, so khớp phần lõi)
5. Nhóm không khớp → đưa vào `ignored_groups`. Nhóm khớp → lưu vào `groups` kèm tên giáo viên + ngày học

### 5.2. Zalo Auto Scan (`POST /run-scan`)
1. Kiểm tra nhóm bị ẩn — nếu có "Thông báo khai giảng" → phục hồi
2. Lặp qua từng nhóm: tìm kiếm trên Zalo → mở chat → xác minh đúng nhóm (normalize Unicode + regex)
3. Smart Scroll: cuộn lên tối đa 6 lần, dừng sớm nếu tin nhắn > 6 ngày
4. Bấm "Xem thêm" để bung tin nhắn dài
5. Duyệt 50 tin nhắn mới nhất (ngược), phân loại bằng `rule_classifier.py`:
   - **Scoring System**: Lời chào (+1.0), Đầu mục chính (+2.0/mỗi), Đầu mục phụ (+1.0/mỗi), Từ khóa báo cáo (+3.0), Mã lớp (+1.0), Penalty điểm danh (-2.0)
   - Ngưỡng ≥ 4.0 → là báo cáo
6. Trích xuất ngày tháng (3 mức: dd/mm/yyyy → dd/mm → "ngày d tháng m")
7. Tính trễ/đúng hạn dựa trên ngày học từ BCNB
8. Chụp ảnh khung chat → lưu `static/screenshots/`
9. Quay lại BCNB → tick checkbox cho các lớp đã nộp + lớp Buổi 14 (miễn báo cáo)

### 5.3. Google Form Auto Submit (`POST /api/googleform/submit`)
1. User paste/upload ảnh chụp màn hình danh sách học viên
2. Gọi `Gemini Vision` OCR → trả về JSON `[{name, class_code}]`
3. User review/chỉnh sửa danh sách trên UI
4. Hệ thống dùng `FormSubmitter` (Playwright) mở Google Form → điền tên, mã lớp, các câu hỏi mapping → Submit
5. Có tính năng: chuyển trang thông minh (multi-page form), phát hiện reCAPTCHA, rate limit, concurrency

### 5.4. LMS Auto Tick (`POST /api/lms/upload-and-run`)
1. User upload ảnh danh sách lớp CHƯA nhận xét LMS
2. Gemini Vision → trích xuất mã lớp (JSON array)
3. Mở BCNB → tick cột LMS cho tất cả lớp KHÔNG nằm trong danh sách chưa làm (logic đảo ngược)

---

## 6. Frontend (Dashboard module hóa)

`templates/index.html` là facade Jinja 37 dòng. Nội dung được chia thành 8
partial và các tệp CSS/JavaScript theo chức năng, nhưng phía người dùng vẫn là
một dashboard với 4 tab:

| Tab | Nội dung |
|-----|----------|
| **Zalo Auto** | Bảng kết quả quét, metrics cards (Tổng/Hoàn thành/Miễn/Trễ), nút Quét/Dò tìm/Tick LMS/Lọc lỗi, thanh tìm kiếm |
| **Nộp Google Form** | Upload ảnh (paste/drag/click), kết quả OCR (editable), cấu hình mapping Q&A, lịch sử nộp, nút Submit |
| **Cấu hình Nhóm** | Danh sách nhóm đang hoạt động + nhóm bị loại bỏ (với nút phục hồi) |
| **Nhật ký** | Terminal-style log viewer (copy-able) |

- **Design**: Dark mode, Glassmorphism, TailwindCSS CDN, Font Awesome icons, Chart.js donut chart
- **Polling**: Dashboard tự động poll `GET /api/jobs/{id}` mỗi 2 giây để cập nhật trạng thái
- **Contract**: Cổng kiểm tra khóa DOM ID, global handler, thứ tự script và khả năng phục vụ tài nguyên tĩnh

---

## 7. Background Job System

- `services/job_dispatcher.py` claim job `PENDING → RUNNING`, phát heartbeat và dispatch từ payload lưu trong SQLite.
- Cancel là hợp tác: worker dừng giữa các target, không hard-cancel giữa click và verify.
- Retry tạo job mới có `retry_of_job_id` và `attempt + 1`; backend chặn retry tác vụ ghi thật.
- Startup chỉ chạy lại tác vụ đọc/dry-run an toàn. External mutation bị gián đoạn được quarantine để kiểm tra audit.
- Frontend poll `/api/jobs/{id}`, hiển thị heartbeat, cancel/retry và timeline events/audit.
- Job types: `ZALO_DISCOVERY`, `ZALO_SCAN`, `GOOGLEFORM_OCR`, `GOOGLEFORM_SUBMIT`, `LMS_ANALYSIS`, `LMS_AUTOMATION`.
- Retry có upload clone theo batch transaction; startup dọn file pending/mồ côi trước khi recovery.
- BCNB report/LMS chỉ hoàn tất audit sau khi tải lại dữ liệu server và đọc checkbox mới.

---

## 8. Các thành phần chưa hoàn thiện (Stub)

| File | Trạng thái | Ghi chú |
|------|-----------|---------|
| `classifiers/llm_classifier.py` | **STUB** | `GeminiClassifier` chỉ return hardcoded values. Chưa tích hợp Gemini API thật |
| `classifiers/embedding_classifier.py` | **STUB** | `EmbeddingClassifier` return `UNCERTAIN`. Chưa tích hợp BAAI/bge-m3 |
| `models/report.py` | Có định nghĩa nhưng **chưa sử dụng** trong flow chính | Chỉ lưu qua repository |
| `services/ocr_service.py` | Wrapper nhưng **không được gọi trực tiếp** | Google Form service gọi trực tiếp `OCRProcessor` |

---

## 9. Biến môi trường (.env)

| Key | Mô tả |
|-----|-------|
| `GEMINI_API_KEY` | API key cho Google Gemini |
| `GEMINI_MODEL` | Model name (mặc định: `gemini-3.1-flash-lite`) |
| `BCNB_USERNAME` / `BCNB_PASSWORD` | Tài khoản đăng nhập BCNB |
| `FORM_URL` | URL Google Form cần nộp |
| `GOOGLE_FORM_MODE` | `dry-run` (chỉ điền, không submit) hoặc `production` |
| `GOOGLE_FORM_CONCURRENCY_LIMIT` | Số form nộp đồng thời (mặc định: 3) |
| `GOOGLE_FORM_RATE_LIMIT` | Delay giữa các lần nộp (giây, mặc định: 5) |
| `GOOGLE_FORM_HEADLESS` | `true`/`false` — chạy browser ẩn hay hiện |
| `BCNB_DRY_RUN` | Mặc định `true`; chỉ `false` sau khi duyệt dry-run |
| `UPLOAD_ROOT` / `UPLOAD_RETENTION_HOURS` | Nơi lưu input riêng theo job và thời gian giữ để retry |
| `MAX_UPLOAD_BYTES` / `MAX_IMAGE_PIXELS` | Giới hạn byte và pixel chống ảnh quá lớn |
| `JOB_HEARTBEAT_SECONDS` | Chu kỳ heartbeat của worker |

---

## 10. Các thuật toán đặc biệt

### Exact Class-code Matching
Tên nhóm Zalo chỉ khớp khi chứa đầy đủ mã lớp với biên ký tự hợp lệ. Không còn
dùng phần lõi/substring, nên `VT-JSB14` không thể khớp nhầm `VT-JSB140`.

### Rule-based Scoring Classifier
Phân loại tin nhắn Zalo có phải là báo cáo hay không:
- 5 category tính điểm + 1 penalty
- Ngưỡng ≥ 4.0 → báo cáo
- Kèm trích xuất ngày tháng thông minh (3 mức fallback)

### Smart Scroll
Tối ưu cuộn trên Zalo Web:
- Cuộn tối đa 6 lần, mỗi lần -2000px
- Dừng sớm nếu tin nhắn đầu tiên trên màn hình đã > 6 ngày

---

## 11. Lưu ý kỹ thuật

1. **Playwright Persistent Context**: Dùng `launch_persistent_context` với `user_data_dir='./zalo_session'` để giữ session Zalo đã login sẵn (không cần quét QR mỗi lần).
2. **Anti-detection**: Inject script loại bỏ flag `navigator.webdriver` và dùng `--disable-blink-features=AutomationControlled`.
3. **Concurrency**: Browser job dùng lock dùng chung và chờ có kiểm tra cancel. Vẫn phải chạy Uvicorn một worker cho tới khi có distributed lease.
4. **Unicode Normalization**: Tất cả so sánh text tiếng Việt đều normalize NFC trước khi xử lý.
5. **Logging**: Hệ thống log in-memory (`system_logs[]`), giới hạn 200 dòng, hiển thị trên UI tab "Nhật ký".
