# PROJECT STATE — Zalo Automation

> Snapshot hiện tại, cập nhật 2026-09-18. Source và kiểm tra runtime mới có độ
> tin cậy cao hơn tài liệu. Không dùng file này làm quyền chạy thao tác thật.

## Trạng thái ngắn

- Ứng dụng local đang chạy và endpoint readiness trả `ready: true`.
- Python trong `venv` là 3.14.7; import FastAPI, Playwright, Gemini, aiosqlite và
  Pillow thành công.
- Runtime hiện báo BCNB và Google Form đang cấu hình `production`, Gemini đã có
  cấu hình. Đây chỉ là trạng thái cấu hình, **không phải phê duyệt chạy thật**.
- Dispatcher không có job đang chạy tại thời điểm kiểm tra.
- Mã nguồn có 74 file Python production, 43 route, 19 bảng SQLite, 6 loại job,
  55 file test và 525 hàm test được khai báo.
- Git ở commit `624143f`, branch `chore/adopt-qq-ai-workflow-v9`, chưa push trong
  task này. Checkpoint mã nguồn đã được đóng gói an toàn; dự án không còn khối thay
  đổi mã nguồn lớn chưa commit. Thư mục `scratch/` là thư mục debug cục bộ được
  `.gitignore` bỏ qua.

## Chức năng đã có trong source

| Khu vực | Trạng thái source/local |
|---|---|
| Dashboard, log, job progress | Đã triển khai |
| Auto Discovery hai tuần BCNB/Zalo | Đã triển khai, có snapshot và gap review |
| Quét Zalo song song và evidence | Đã triển khai, fail-closed khi thiếu đồng bộ |
| Dry-run/ghi BCNB có intent và read-back | Đã triển khai trong source |
| Google Form OCR, mapping, dropdown | Đã triển khai |
| Google Form idempotency, ảnh bằng chứng, xóa lịch sử | Đã triển khai |
| LMS preview, xác nhận một lần, read-back | Đã triển khai |
| Job heartbeat, cancel, retry/recovery có giới hạn | Đã triển khai |
| Web auth Viewer/Operator/Admin và CSRF | Đã triển khai |
| Backup/restore drill, migration checksum | Đã triển khai |
| Staging contract và browser smoke | Có mã/mẫu; chưa phải production acceptance |

## Thay đổi gần nhất đã ghi nhận

Ngày 2026-09-17, source được bổ sung hoặc sửa các phần sau:

- Chụp ảnh Google Form đã điền, xem/xóa ảnh và xóa lịch sử.
- Hỗ trợ câu hỏi dropdown và mapping cơ sở trong Google Form.
- Thu hồi profile lock khi process giữ lock đã chết.
- Sửa chờ chuyển hướng đăng nhập BCNB và lỗi console Unicode trên Windows.
- Sửa fallback tìm nhóm Zalo và phát hiện khoảng trống đồng bộ lịch sử.
- Gỡ QQ AI Workflow V10; không còn workflow/bridge đó trong dự án.

Ngày 2026-09-18:
- Đã chuẩn hóa UTF-8 các chuỗi tiếng Việt bị lỗi mã hóa trong `services/evidence_store.py`
  và `repositories/job_repository.py`.
- Đã bổ sung regression test kiểm tra chuỗi tiếng Việt trong `tests/test_evidence_store.py`
  và `tests/test_phase5_queue.py`.
- `CHECK_QUALITY.ps1 -Isolated` đạt 525/525 test, coverage 77,1%, Ruff/mypy và toàn bộ cổng đều đạt.

## Blocker và rủi ro mở

1. **Checkpoint Git local đã được thiết lập:** Commit `624143f` đã đóng gói toàn bộ
   185 file thay đổi (59 thêm mới, 90 sửa, 36 xóa). Đã tạo file ZIP mã nguồn sạch
   bằng `git archive`: `F:\FINAL_PROJECT\zaloAutomation-git-checkpoint-624143f.zip`
   (SHA-256: `c504574463efecd9dfbaf126dfddec7455a6b9211d149578566c20c6677bccfa`).
   Thư mục `scratch/` là dữ liệu debug cục bộ đã được `.gitignore` bỏ qua mà không
   ảnh hưởng 2 ảnh debug của Owner.
2. **Nghiệm thu thật chưa đầy đủ:** Dự án chưa được nghiệm thu production thật.
   Các giới hạn chưa kiểm chứng bao gồm:
   - Chưa chạy Zalo Web trên hồ sơ người dùng thật;
   - Chưa ghi dữ liệu thật vào BCNB (`BCNB_DRY_RUN=false`);
   - Chưa submit Google Form thật;
   - Chưa gọi Gemini bằng tài nguyên mạng thật trong môi trường live.
3. **Zalo phụ thuộc dữ liệu đồng bộ trên máy:** khi lịch sử Web thiếu, hệ thống
   phải trả `INSUFFICIENT_EVIDENCE`; người vận hành cần đồng bộ rồi chạy lại.
4. **Chỉ một instance:** SQLite, session và profile browser chưa hỗ trợ nhiều
   replica/máy cùng ghi.
5. **Cấu hình hiện cho phép ghi thật:** không bấm các nút tác vụ trước khi kiểm
   tra đúng tuần, đúng target và có bản xem trước mới.
6. **Lỗi mã hóa tiếng Việt đã giải quyết:** các chuỗi trong `services/evidence_store.py`
   và `repositories/job_repository.py` đã hiển thị đúng tiếng Việt chuẩn; đã có test bảo vệ.

## Bằng chứng của lần rà soát 2026-09-18

| Kiểm tra | Kết quả |
|---|---|
| `venv\Scripts\python.exe --version` | Python 3.14.7 |
| Import thư viện production chính | Thành công |
| `GET /api/health/ready` | `ready: true`, capabilities: `bcnb=dry-run`, `google_form=dry-run` |
| Kiểm kê source | 74 file Python, 43 route, 19 bảng |
| Kiểm kê test | 55 file, 525 hàm test |
| `CHECK_QUALITY.ps1 -Isolated` | Exit `0`; `QUALITY GATE PASSED`; 525 test; coverage 77,1% |
| Ruff / mypy | Ruff đạt; mypy đạt trên 74 file nguồn |
| Cổng bổ sung | Phase 3–9B, dependency, schema, smoke app, frontend và compile đều đạt |
| Auto Discovery dry-run | Job 272 `SUCCESS`, tạo Snapshot 38 (49 lớp BCNB, 48 khớp, 1 thiếu Zalo `VT-NG-A261005`) |
| Zalo Scan dry-run (Job 273) | `PARTIAL_SUCCESS`: 52 target quét Zalo `VERIFIED`, 1 `SKIPPED`; BCNB: 47 `ALREADY_DONE`, 5 `FAILED_BEFORE_WRITE`, 1 `DRY_RUN` |
| Mục tiêu `VT-C4K-GA39` (Buổi 3) | `SUBMITTED_VERIFIED` (nộp 16/09 đúng hạn); ảnh minh chứng lưu tại `static/screenshots/job-273/report-4b7d6039b6.png`; BCNB `ALREADY_DONE` |
| External Action Audit | 1 intent ghi `DRY_RUN` (`VT-JSA12-HB`), 0 mutation trên BCNB thật |

## Bước an toàn tiếp theo

1. Phát triển hoặc kiểm thử tiếp từ checkpoint Git sạch `624143f`. Tuyệt đối không
   push lên remote nếu chưa có yêu cầu từ Owner.
2. Chạy tiếp acceptance local dry-run cho module Google Form OCR / Auto-Submit và
   LMS preview nếu cần trước khi chuyển sang canary.
3. Chỉ sau đó mới xin Owner duyệt một canary thật có target cụ thể và rollback.
