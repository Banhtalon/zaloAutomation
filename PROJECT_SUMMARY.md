# PROJECT SUMMARY — Zalo Automation

> Nguồn tham chiếu tổng quan cho AI và người phát triển. Nội dung này mô tả mã
> nguồn tại ngày 2026-09-26; dữ liệu và trạng thái chạy phải kiểm tra lại trong
> `PROJECT_STATE.md` và runtime trước mỗi task.

## 1. Mục tiêu sản phẩm

Zalo Automation, tên giao diện MindX Auto, hỗ trợ một người vận hành quản lý
báo cáo giảng dạy. Hệ thống không coi AI hoặc một lần quét trình duyệt là nguồn
sự thật duy nhất. Mọi trường hợp thiếu, trùng hoặc mơ hồ phải dừng để kiểm tra.

Ba luồng nghiệp vụ chính:

| Luồng | Đầu vào | Kết quả |
|---|---|---|
| Zalo + BCNB | Hai tuần BCNB và các nhóm Zalo | Bằng chứng báo cáo theo lớp; dry-run hoặc cập nhật BCNB có kiểm chứng |
| Google Form | Ảnh danh sách học viên | Danh sách OCR để duyệt; điền form ở dry-run hoặc production có chống gửi trùng |
| LMS + BCNB | Ảnh danh sách lớp chưa hoàn thành LMS | Kế hoạch có mã xác nhận dùng một lần; dry-run hoặc cập nhật BCNB có đọc lại |

## 2. Công nghệ và cách chạy

| Thành phần | Hiện tại |
|---|---|
| API và server | FastAPI `0.136.3`, Uvicorn `0.49.0` |
| Tự động trình duyệt | Playwright `1.60.0`, Chromium, profile cục bộ |
| AI đọc ảnh | Google Gemini qua `google-genai 2.8.0` |
| Dữ liệu | SQLite và `aiosqlite` |
| Giao diện | Jinja2, JavaScript thường, CSS/Tailwind CDN |
| Python | Cấu hình kiểm tra cho Python 3.14; máy hiện tại dùng Python 3.14.7 |
| Kiểm tra | `unittest`, Coverage, Ruff, mypy và các cổng bất biến theo giai đoạn |
| Khởi động | `START.bat` → `python -m scripts.run_local` → `main:app` |

`main.create_app()` tạo ứng dụng. `AppContainer` sở hữu cấu hình, repository,
kho upload, kho bằng chứng, gateway bên ngoài, dispatcher và vòng đời start/stop.
Không dùng nhiều Uvicorn worker với kiến trúc hiện tại.

## 3. Cấu trúc mã nguồn

| Khu vực | Trách nhiệm |
|---|---|
| `main.py` | Tạo FastAPI app, middleware bảo mật/ghi log và gắn router |
| `api/` | 10 router: giao diện, đăng nhập, Zalo, Form, LMS, job, upload, health, bằng chứng và probe đã nghỉ |
| `core/` | Cấu hình bất biến, container, phân quyền và kiểm tra sẵn sàng production |
| `services/` | Luồng nghiệp vụ, Playwright/Gemini gateway, dispatcher, bằng chứng và kiểm tra an toàn |
| `repositories/` | Đọc/ghi SQLite cho job, lớp, báo cáo, audit, LMS, upload và bằng chứng |
| `db/` | Tạo/nâng schema, transaction, backup và thử khôi phục |
| `googleform_autosubmit/` | Cấu hình, OCR và thao tác Google Form |
| `classifiers/` | Bộ phân loại báo cáo Zalo theo luật và trích ngày |
| `templates/`, `static/` | Dashboard bốn khu vực và mã giao diện |
| `tests/` | 55 file `test_*.py` cùng các cổng kiểm tra riêng |
| `scripts/` | Khởi động local, backup/restore, smoke test và runner chẩn đoán giới hạn |
| `deploy/staging/` | Mẫu cấu hình staging; không tự triển khai |

Hai file lớn nhất là `services/external_gateways.py` và
`services/zalo_parallel_service.py`. Không tách chúng chỉ để “đẹp”; chỉ thay đổi
khi có yêu cầu chức năng hoặc lỗi cụ thể và phải kiểm tra tất cả caller liên quan.

## 4. API và quyền

Mã nguồn hiện có 43 route. Các nhóm chính:

  - `/auto-discover`, `/run-scan`, `/api/zalo-scan-plan`,
    `/api/zalo-scan-preflight`, `/api/groups`:
  discovery, kế hoạch và quét Zalo/BCNB.
- `/api/uploads`, `/api/googleform/*`: upload, OCR, mapping, lịch sử, ảnh bằng
  chứng và submit Google Form.
- `/api/lms/analyze`, `/api/lms/confirm`: phân tích và xác nhận LMS.
- `/api/jobs/*`: trạng thái, hủy, retry, timeline, audit và target.
- `/api/evidence/*`: phục vụ ảnh bằng chứng qua kiểm tra đường dẫn và quyền.
- `/api/auth/*`: đăng nhập, phiên hiện tại và đăng xuất khi chạy web mode.
- `/api/health/*`, `/api/metrics/jobs`: liveness, readiness và số liệu job.

Ba vai trò web là `Viewer`, `Operator` và `Admin`. Chế độ local chỉ cho phép
host loopback. Chế độ web yêu cầu đăng nhập, cookie an toàn, CSRF, host/origin
allowlist và HTTPS theo kiểm tra production readiness.

Hai route landing probe còn tồn tại để tương thích nhưng trả trạng thái nghỉ;
chẩn đoán tương ứng chỉ được chạy qua CLI giới hạn.

## 5. Job nền

SQLite là nguồn trạng thái bền vững. `JobDispatcher` hỗ trợ sáu loại job:

| Job | Chức năng | Retry/recovery |
|---|---|---|
| `ZALO_DISCOVERY` | Đọc BCNB/Zalo và tạo snapshot | Có |
| `ZALO_SCAN` | Quét target và lập/thực hiện kế hoạch BCNB | Chỉ khi dry-run |
| `GOOGLEFORM_OCR` | OCR ảnh học viên | Có |
| `GOOGLEFORM_SUBMIT` | Điền hoặc gửi form | Production không tự retry |
| `LMS_ANALYSIS` | OCR và tạo kế hoạch LMS | Có |
| `LMS_AUTOMATION` | Thực hiện kế hoạch đã xác nhận | Không retry mù |

`/run-scan` phải qua preflight GREEN hoặc xác nhận YELLOW; bước tạo job Zalo
kiểm tra job đang hoạt động trong cùng giao dịch SQLite để không nhận hai lần
chạy cạnh tranh. Production chỉ dùng một worker/trang Zalo trên mỗi profile.

Job có heartbeat, cancel hợp tác, timeout, giới hạn hàng đợi, event timeline và
external-action audit. Job production bị gián đoạn không được tự phát lại vì
có thể thao tác bên ngoài đã xảy ra nhưng chưa kịp ghi kết quả.

## 6. Luồng Zalo và BCNB

1. Discovery đọc đúng hai tuần BCNB, thu thập nhóm Zalo và tạo snapshot bất biến.
2. So khớp ưu tiên mã lớp đầy đủ; alias rút gọn chỉ được dùng khi ánh xạ duy nhất.
3. `/run-scan` từ chối snapshot cũ, thiếu hai tuần, trùng target hoặc chưa đến giờ học.
4. Dry-run giữ số worker cấu hình để kiểm thử; production hiện dùng một worker
   Zalo trên mỗi profile vì Zalo chỉ cho một phiên/tab ổn định. Muốn tăng worker
   phải dùng profile riêng và kiểm chứng riêng; thao tác ghi BCNB vẫn được giới
   hạn độc lập.
5. Tin nhắn được phân loại bằng luật. Ngày gửi lấy từ giao diện Zalo; ngày trong
   nội dung chỉ là bằng chứng hỗ trợ.
6. Thiếu lịch sử đồng bộ, header sai, ngày không chắc chắn hoặc target mơ hồ trả
   trạng thái cần xem xét, không suy luận “không có báo cáo”.
7. Khi ghi BCNB, hệ thống ghi intent, tải lại trang và đọc lại checkbox trước khi
   đánh dấu `VERIFIED`.

Vòng đời lớp nằm trong `CLASS_LIFECYCLE.md`.

## 7. Luồng Google Form

1. Ảnh được kiểm tra MIME, dung lượng, số pixel và lưu theo `upload_id`.
2. `GOOGLEFORM_OCR` dùng Gemini để trả danh sách `{name, class_code}` cho người
   vận hành rà soát.
3. Submit chuẩn hóa danh sách, khóa theo idempotency key và lưu mode vào payload.
4. Form hỗ trợ text, radio và dropdown/listbox; có mapping cơ sở theo mã lớp.
5. Mỗi học viên có outcome riêng. Kết quả không rõ sau khi bấm submit là
   `UNKNOWN`, không tự gửi lại.
6. Ảnh Trang 1 và Trang 3 được ghép làm bằng chứng; giao diện cho xem hoặc xóa.

## 8. Luồng LMS

1. Ảnh đầu vào được Gemini trích mã lớp.
2. Server lập kế hoạch theo tuần BCNB và trả mã xác nhận có hạn dùng.
3. Admin xác nhận đúng plan digest; plan cũ, dùng lại hoặc thay đổi mode bị từ chối.
4. Hệ thống chỉ tích các lớp đủ điều kiện, rồi tải lại và đọc trạng thái server.

## 9. Dữ liệu

`db/database.py` hiện khai báo 19 bảng, gồm các nhóm:

- lớp và discovery: `groups`, `ignored_groups`, `class_cohorts`,
  `class_code_history`, `discovery_snapshots`, `zalo_batch_targets`;
- job và file đầu vào: `scan_jobs`, `job_events`, `job_uploads`;
- báo cáo/Form: `reports`, `classification_logs`, `form_mappings`,
  `form_submissions`, `googleform_idempotency`;
- kiểm chứng: `lms_confirmations`, `external_action_audit`,
  `evidence_artifacts`, `evidence_access_log`;
- schema: `schema_migrations`.

Schema được nâng theo hướng cộng thêm và có checksum migration. Backup SQLite
dùng API backup của SQLite, kiểm tra hash/integrity và có restore drill.

## 10. Ranh giới an toàn

- Mặc định mẫu cấu hình là `BCNB_DRY_RUN=true` và
  `GOOGLE_FORM_MODE=dry-run`.
- Chế độ ghi thật phải vượt qua `core/production_readiness.py` trước khi tạo
  worker bên ngoài.
- Browser profile có khóa liên tiến trình; lock của PID đã chết được thu hồi.
- Ảnh bằng chứng có hash, metadata, retention và access log; không phục vụ trực
  tiếp qua `/static/screenshots`.
- Log có request/job context, rotation và che dữ liệu nhạy cảm.
- `CHECK_QUALITY.ps1 -Isolated` đặt biến chặn gateway thật và dùng database,
  upload, log, bằng chứng tạm.

## 11. Phần chưa được coi là hoàn tất

- Chưa có bằng chứng nghiệm thu mới cho toàn bộ luồng thật Zalo, BCNB, Google
  Form và LMS sau các thay đổi gần nhất.
- Cấu hình production trên một máy không đồng nghĩa hệ thống đã được duyệt để
  chạy production hoặc đưa lên Internet.
- Mã nguồn hiện tại đã có checkpoint Git `624143f` sạch; có thể dùng checkpoint
  này làm điểm bắt đầu an toàn cho AI hoặc kỹ sư khác. Checkpoint Git không đồng
  nghĩa với production acceptance; AI tiếp theo vẫn phải đọc `PROJECT_SUMMARY.md`,
  `PROJECT_STATE.md` và `PROJECT_DECISIONS.md` trước khi phát triển feature.
- Bộ phân loại đang dùng luật; không có `llm_classifier.py` hoặc
  `embedding_classifier.py`. Đây là chủ ý hiện tại, không phải file bị thiếu.
