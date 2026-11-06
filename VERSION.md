# Lịch sử phiên bản — Zalo Automation

> Dự án đã có checkpoint Git local tại commit `624143f` và gói ZIP mã nguồn sạch
> sinh từ `git archive` (`F:\FINAL_PROJECT\zaloAutomation-git-checkpoint-624143f.zip`).
> Commit này chưa được push lên remote. `APP_VERSION` mặc định trong cấu hình là `4.0.0`;
> các mốc dưới đây là lịch sử năng lực, không phải tuyên bố production acceptance.

## Checkpoint hiện tại — 2026-09-18 (Commit `624143f`)

- Ứng dụng local FastAPI với dashboard bốn khu vực.
- 43 API route, 6 loại background job và 19 bảng SQLite.
- Auto Discovery/Scan theo snapshot hai tuần, target bất biến và bằng chứng Zalo.
- Google Form OCR/submit có idempotency, dropdown và ảnh bằng chứng.
- LMS preview/confirm có token một lần, digest và read-back.
- Viewer/Operator/Admin, CSRF, host/origin guard và production preflight.
- Backup/restore drill, migration checksum, evidence retention và audit.
- Quality gate gồm Ruff, mypy, coverage, unittest, schema, smoke và frontend.
- Thư mục `scratch/` chứa ảnh debug của Owner được `.gitignore` bỏ qua.

## Các mốc lịch sử

| Mốc | Nội dung chính |
|---|---|
| Prototype | Quét Zalo bằng Playwright và phân loại báo cáo theo luật |
| Dashboard + SQLite | Giao diện local, nhóm, job và báo cáo được lưu bền vững |
| BCNB + LMS + Form | Discovery, tích BCNB, LMS OCR và Google Form OCR/submit |
| Reliability | Upload theo job, heartbeat, cancel, retry/recovery giới hạn và audit |
| Maintainability | `create_app`, `AppContainer`, cấu hình bất biến, log/health/metrics |
| External boundaries | Tách Playwright/Gemini gateway để test cách ly |
| Service hardening | Lỗi công khai ổn định, cleanup browser và discovery an toàn |
| Lifecycle + evidence | Vòng đời lớp, snapshot, evidence artifact và access log |
| Security + readiness | Phân quyền web, CSRF, backup, staging contract và production preflight |
| Local parallel scan | Hai worker Zalo, BCNB write giới hạn, target repository và batch policy |
| September 2026 fixes | Zalo sync gap, login/lock, Form dropdown và ảnh bằng chứng |
| October 2026 Wave 1–3 | Quét trực tiếp Web LMS, Quét chọn lọc, Duyệt tay BCNB 1-Click, AI gợi ý ca mơ hồ, Tra cứu Lịch sử Tuần & Giao diện Tactile 3D |
| October 2026 Wave 4 (`DEC-038`) | Đọc mở rộng bảng Teaching (`check_nhanxet.php`), Kiểm định chất lượng nội dung bài học & nhận xét học viên LMS (Buổi thường 3 ý vs Checkpoint Coding B5/B9, Robotics B4/B8), Tự động ghi `Mô tả vấn đề (TE/TF/QC)` có kiểm chứng đọc ngược, và Bảng Email Quản lý 9 cột (`HTML Table` + `Clipboard Rich Table` + `Unicode Table`) |
| October 2026 Wave 5 (`DEC-039`) | Hardening toàn diện luồng kiểm tra báo cáo (`5.0` → `5.8`): cô lập lỗi tick checkbox và ghi chú QC kèm bảo vệ lạc quan & provenance 5 điều kiện; chọn slot LMS theo ngày học + số buổi; một nguồn sự thật `services/report_status.py` (`DONE/LATE/MISSING/REVIEW/EXEMPT/NOT_SCANNED`, mức `FAIL/NOTE`, quyết định D1–D6); thống kê `dropped_rows` và miễn trừ Buổi 14 trực tiếp từ BCNB; mở rộng parser vòng đời lớp (`ROB4/SEMI/PRE/ARM/PT/JS/CS/G/S`) kèm tương thích `cohort_key` cũ; giao diện bám dữ liệu server sau F5 và chốt an toàn khi LMS lỗi toàn hệ thống |
| November 2026 Hotfix & GitHub Source of Truth (`DEC-040`) | Xử lý báo cáo Zalo tách 2 bubble (`provisional_state` ưu tiên `TARGET_EXACT`, lọc `"nội dung bài tập"` và tên file homework), trích xuất tên bài học nhiều dòng & dừng trước mục buổi học kế tiếp, nhận diện provenance cho audit Wave 4 bọc `[Auto QC: ...]` để tự động gỡ lỗi cũ đã khắc phục, hỗ trợ SSO `id.mindx.edu.vn`/`base.mindx.edu.vn` & fallback click checkbox BCNB, và chuẩn hóa quy trình cập nhật `CHANGELOG.md` + đồng bộ GitHub (`origin`) làm Source of Truth sau mỗi tính năng/fix |
| November 2026 Rule & Evidence Upgrade (`DEC-041`) | Cập nhật quy tắc tính lỗi giáo viên: chỉ tính lỗi (`is_failed=True`) khi chưa báo cáo Zalo (`MISSING`/`REVIEW`) hoặc chưa hoàn tất nhận xét LMS (`❌`/`⚠️`), chuyển các vi phạm chất lượng nội dung thành nhắc nhở (`NOTE` / `Ghi chú QC`) nhưng vẫn ghi đầy đủ lên cột `Mô tả vấn đề (TE/TF/QC)` của Teaching; nâng cấp chụp ảnh minh chứng Zalo (`device_scale_factor=2` + cắt gọn vùng hội thoại `_resolve_zalo_chat_clip`) và trang bị bộ điều khiển phóng to tương tác `100% → 185% → 260%` trên Dashboard |

## Cách tạo phiên bản tiếp theo

Chỉ đặt tên phiên bản mới sau khi:

1. working tree được review và có commit rõ ràng;
2. full isolated quality gate xanh;
3. local acceptance tương ứng được ghi vào `PROJECT_STATE.md`;
4. nếu có thao tác thật, canary và read-back đã được Owner duyệt riêng.
