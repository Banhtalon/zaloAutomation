# Giai đoạn 2 — Reliability Runbook

## Những bảo vệ đã triển khai

- Upload không còn được quét từ thư mục dùng chung. Server cấp `upload_id`, sau
  đó claim đúng ảnh vào `runtime/uploads/jobs/<job_id>/inputs/`.
- Job lưu payload bền vững trong SQLite, có heartbeat, cancel hợp tác, attempt,
  retry lineage và event timeline.
- Khi khởi động lại, job đọc/dry-run có thể được xếp lại. Job ghi thật đang ở
  `RUNNING` bị chuyển sang `FAILED` và yêu cầu kiểm tra audit; hệ thống không tự
  phát lại thao tác bên ngoài.
- Mỗi thao tác BCNB/Google Form ghi một audit intent đã commit trước khi click
  hoặc submit, rồi cập nhật `DRY_RUN`, `VERIFIED`, `FAILED` hoặc trạng thái chưa
  xác định sau bước kiểm chứng.
- Zalo chỉ ghép mã lớp đầy đủ, xác nhận tuần/năm, tìm cột theo header và đọc lại
  checkbox sau khi ghi. Ảnh bằng chứng nằm trong thư mục riêng của job.
- Google Form dùng khóa idempotency theo batch/học viên để chặn double-submit.
- Request ghi có header `Origin` ngoài localhost bị từ chối để tránh website khác
  kích hoạt controller đang chạy trên máy người vận hành.

## Quy tắc retry và recovery

| Loại job | Retry | Recovery sau restart |
|---|---:|---:|
| Google OCR | Có | Có |
| LMS analysis | Có | Có |
| Zalo discovery | Có | Có |
| Zalo scan dry-run | Có | Có |
| Google Form dry-run | Có | Có |
| Zalo/Google Form ghi thật | Không | Không tự phát lại |
| LMS execution | Không retry thủ công | Chỉ recovery nếu dry-run |

Job `PENDING` có payload bền vững có thể được chạy sau restart vì chưa có worker
claim. Job ghi thật đã ở `RUNNING` được coi là có khả năng ghi dở dang; xem
`/api/jobs/<id>/audit` trước khi quyết định thao tác thủ công.

## API vận hành

- `POST /api/uploads` — lưu ảnh và trả `upload_id`.
- `POST /api/jobs/<id>/cancel` — yêu cầu dừng tại ranh giới an toàn kế tiếp.
- `POST /api/jobs/<id>/retry` — tạo job mới nếu backend đánh dấu retryable.
- `GET /api/jobs/<id>/events` — timeline trạng thái.
- `GET /api/jobs/<id>/audit` — before/intended/after của external action.

## Retention và cấu hình

```env
UPLOAD_ROOT=runtime/uploads
UPLOAD_RETENTION_HOURS=24
MAX_UPLOAD_BYTES=8388608
MAX_IMAGE_PIXELS=24000000
JOB_HEARTBEAT_SECONDS=5
LMS_CONFIRMATION_TTL_MINUTES=30
```

Input được giữ mặc định 24 giờ để retry tác vụ đọc và điều tra lỗi. Runtime,
database, screenshots, browser profile, `.env` và backups đều bị loại khỏi Git.

## Kiểm thử

```powershell
.\venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

Bộ test bao phủ upload isolation/claim, path traversal, giới hạn pixel, state
machine, cancel race, heartbeat, recovery, retry lineage, audit ordering,
idempotency key và exact class-code matching.

## Rollback

Mốc trước Giai đoạn 2:

`F:\FINAL_PROJECT\zaloAutomation\.backups\zaloAutomation-phase1-20260715-092856.zip`

Archive chứa `.env`, database và browser profile nên phải được giữ kín. Giải nén
ra thư mục riêng để kiểm tra trước khi thay thế workspace hiện tại.
