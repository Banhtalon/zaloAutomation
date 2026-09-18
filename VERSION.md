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

## Cách tạo phiên bản tiếp theo

Chỉ đặt tên phiên bản mới sau khi:

1. working tree được review và có commit rõ ràng;
2. full isolated quality gate xanh;
3. local acceptance tương ứng được ghi vào `PROJECT_STATE.md`;
4. nếu có thao tác thật, canary và read-back đã được Owner duyệt riêng.
