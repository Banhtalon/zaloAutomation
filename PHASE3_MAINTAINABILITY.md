# Giai đoạn 3 — Maintainability & Operability

## Mốc 3.1 đã triển khai

Mốc đầu tiên ưu tiên khóa hành vi an toàn trước khi tách giao diện hoặc thay đổi
kiến trúc lớn:

- Chế độ `dry-run`/`production` được lưu bất biến trong payload của từng job.
  Worker không còn đọc lại mode hiện thời khi chuẩn bị ghi Zalo/BCNB, LMS hoặc
  Google Form. Job cũ thiếu mode mặc định về `dry-run`.
- Metadata job và mode phải khớp. Job production phải là external mutation,
  không retry và không recovery; sai invariant sẽ bị từ chối.
- Digest kế hoạch LMS chứa cả mode. Đổi cấu hình sau khi preview không thể biến
  kế hoạch dry-run thành thao tác thật.
- Tiêu đề chat Zalo phải chứa đầy đủ identity của nhóm theo token; thiếu
  selector, header rỗng hoặc va chạm `JSB14`/`JSB140` đều dừng theo fail-closed.
- Ngày tin nhắn không đọc được không còn mặc định thành hôm nay. Báo cáo đó bị
  bỏ qua; ngày không năm ở đầu năm được suy ra về năm trước.
- Logic chọn tuần BCNB và kiểm tra read-back được dùng chung bởi Zalo và LMS.
- Log JSONL có rotation, cursor, context request/job, level/event và redaction.
- Có liveness, readiness và metrics tổng hợp, không gọi dịch vụ ngoài.
- Smoke test dùng database tạm; quality gate khóa các invariant trên.

## Mốc 3.2 đã triển khai

- `create_app(settings)` và `AppContainer` là composition root. `main.app` vẫn
  được giữ để `START.bat`, Uvicorn và code cũ không đổi cách chạy.
- Mỗi app sở hữu settings immutable, database context, repositories, upload
  store, worker locks, structured logger và `JobDispatcher` riêng.
- Dispatcher là instance, sở hữu task registry/shutdown state; sau shutdown từ
  chối nhận job mới và có thể khởi động lại an toàn ở lifespan kế tiếp.
- Router lấy dependency từ `request.app.state.container`; production không còn
  dùng dispatcher/repository global của router. Các hàm dispatcher module-level
  chỉ còn là compatibility shim lazy.
- Hai app trong cùng process có database/upload/log/cursor/instance ID/locks
  độc lập. Cùng một app đã được kiểm thử ba chu kỳ start/stop liên tiếp.
- Host ngoài loopback bị từ chối kể cả GET; mutation chỉ nhận Origin trùng chính
  xác host/port; request ID sai định dạng được thay mới và body quá giới hạn trả
  HTTP 413 trước bước giải mã ảnh.
- Metrics/readiness được chuyển sang worker thread để không chặn event loop;
  SQLite có index thời gian cho job, audit và upload.
- Dọn ảnh bằng chứng được tách khỏi `zalo_service.py` sang
  `services/evidence_store.py`.

## Mốc 3.3 đã triển khai

- `templates/index.html` giảm từ 1.524 xuống 37 dòng, chỉ còn khung tài liệu,
  các Jinja include và thứ tự tải tài nguyên.
- Giao diện được chia thành 8 partial độc lập cho sidebar, header, 4 vùng tab và
  2 modal; toàn bộ DOM ID và handler cũ được giữ nguyên.
- CSS nội tuyến chuyển sang `static/css/app.css`. JavaScript được chia thành
  cấu hình Tailwind, state/navigation, groups, jobs/dashboard, Google Form, LMS
  và modal; vẫn dùng classic script theo thứ tự phụ thuộc hiện hữu.
- `tests/check_template_js.js` khóa thứ tự script, cú pháp từng tệp, 20 global
  event handler, các DOM ID trọng yếu và ngân sách tối đa 120 dòng cho facade.
- Smoke test render template thật qua FastAPI và xác nhận các CSS/JavaScript
  mới đều được phục vụ.
- Baseline UI dùng viewport `dvh`, phản hồi tương tác tối đa 200 ms,
  `prefers-reduced-motion`, safe-area cho modal, số liệu tabular và nhãn hỗ trợ
  đọc màn hình cho nút chỉ có biểu tượng. Thiết kế và luồng nghiệp vụ không đổi.

## Mốc 3.4 đã triển khai

- `clone_job_uploads()` chuẩn bị toàn bộ file qua tên `.pending-*`, flush/`fsync`,
  kiểm tra lại size và SHA-256 rồi mới `os.replace` sang tên cuối.
- Metadata clone được ghi bằng một `BEGIN IMMEDIATE`/`executemany`; lỗi ở bất kỳ
  phần tử nào rollback toàn batch và dọn mọi file đã chuẩn bị.
- Startup chạy upload reconciliation trước cleanup/recovery: file pending hoặc
  mồ côi được xóa; row còn hiệu lực nhưng thiếu file được báo lỗi và giữ nguyên
  để worker dừng fail-closed thay vì che mất bằng cleanup.
- Logic ghi checkbox được gom vào `services/bcnb_actions.py`. Audit intent cũ
  được đối soát server trước khi thử lại; action key chứa job, tuần/năm, mã lớp
  và buổi học khi áp dụng.
- Sau click/confirm, hệ thống hard reload BCNB, chọn lại chính xác tuần/năm đã
  chốt, resolve lại header và yêu cầu đúng một dòng mã lớp/buổi trước khi đọc
  checkbox mới. Chỉ kết quả đọc lại `true` mới được ghi `VERIFIED`.
- Không đọc được server/session/table sau thao tác sẽ ghi audit `UNKNOWN` và
  dừng batch. Trạng thái chắc chắn chưa lưu ghi `FAILED`.
- Đã loại cả 5 `bare except` còn lại. Ngày học có giá trị nhưng sai định dạng
  không còn đi vào fallback theo ngày hiện tại, tránh tạo kết quả tick nhầm.

## Mốc 3.5 đã triển khai

- Dependency phát triển được tách vào `requirements-dev.txt` và pin chính xác:
  `coverage==7.15.0`, `ruff==0.15.20`, `mypy==2.2.0`.
- `pyproject.toml` là nguồn cấu hình chung cho Ruff, mypy và branch coverage;
  các thư mục runtime, backup, browser profile và môi trường ảo đều bị loại khỏi
  phạm vi phân tích phù hợp.
- Đã sửa 22 lỗi lint; Ruff hiện đạt trên toàn workspace. Đã sửa 21 lỗi type;
  mypy đạt trên toàn bộ 44 tệp production với `check_untyped_defs`.
- Branch coverage chỉ tính mã production đạt baseline 53,4%. Gate 52,0% được
  đặt thấp hơn baseline để phát hiện tụt coverage mà không tạo ngưỡng giả tạo.
- `CHECK_QUALITY.ps1` gom lint, type-check, coverage, 61 regression test,
  invariant/schema/smoke, frontend contract và compile thành một lệnh tái lập.
  `QUALITY.bat` là entrypoint một lần bấm trên Windows.
- Cache/báo cáo cục bộ của coverage, Ruff và mypy đã được thêm vào `.gitignore`.
  Mốc 3.5 khép lại Giai đoạn 3.

## API vận hành

- `GET /api/health/live`: process, version, instance và uptime.
- `GET /api/health/ready`: SQLite, thư mục upload/log, dung lượng đĩa và
  dispatcher. Trả HTTP 503 khi chưa sẵn sàng.
- `GET /api/metrics/jobs?window_hours=24`: số job theo loại/trạng thái, retry,
  recovery, thời gian chạy, audit outcome, upload và dung lượng. Không trả
  payload, tên học viên, tên nhóm hoặc credential.
- `GET /api/logs?after=0&limit=200&level=INFO&job_id=123`: vẫn có mảng `logs`
  tương thích UI, đồng thời trả `records` có cấu trúc và `cursor`.

Log được ghi mặc định vào `runtime/logs/app.jsonl`, xoay ở 5 MiB và giữ 5 file.
Các trường password/token/API key/cookie/payload ảnh được che trước khi ghi.

## Cấu hình

```env
APP_VERSION=3.4.0
LOG_LEVEL=INFO
LOG_DIR=runtime/logs
LOG_MAX_BYTES=5242880
LOG_BACKUP_COUNT=5
LOG_MEMORY_LINES=500
METRICS_WINDOW_HOURS=24
HEALTH_STALE_SECONDS=30
MIN_FREE_DISK_BYTES=104857600
MAX_HTTP_BODY_BYTES=12582912
```

Không log giá trị `.env`. Credential hiện dùng cần được người vận hành đổi định
kỳ; thao tác rotate nằm ngoài rollout mã nguồn này.

## Quality gate

```powershell
.\venv\Scripts\python.exe -X utf8 -m pip install -r requirements-dev.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\CHECK_QUALITY.ps1
```

Trên Windows có thể chạy trực tiếp `QUALITY.bat`. Khi kết thúc Giai đoạn 3, gate
gồm 61 test, mypy trên 44 tệp production và branch coverage 53,4% với ngưỡng
52,0%. Baseline hiện tại đã được nâng ở `PHASE4_EXTERNAL_BOUNDARIES.md`.

## Rollout và rollback

1. Chạy toàn bộ quality gate ở `BCNB_DRY_RUN=true` và
   `GOOGLE_FORM_MODE=dry-run`.
2. Mở `/api/health/ready`; chỉ tiếp tục khi `ready=true`.
3. Chạy một job mỗi loại ở dry-run, kiểm tra `/api/logs`, event và audit không có
   dữ liệu nhạy cảm.
4. Chỉ bật production thủ công cho job mới sau khi xem preview/audit.

Mốc rollback ngay trước mốc 3.5:

`F:\FINAL_PROJECT\zaloAutomation\.backups\zaloAutomation-phase3.4-20260715-120419.zip`

Archive có `.env`, database và browser profile; giữ kín và giải nén sang thư mục
riêng để kiểm tra trước khi thay workspace.

## Giai đoạn 3 đã hoàn tất

Hướng tách adapter Playwright/dịch vụ ngoài và nâng gate coverage lên 60% đã
được hoàn tất trong `PHASE4_EXTERNAL_BOUNDARIES.md`.
