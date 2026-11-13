# PROJECT STATE — Zalo Automation

> Snapshot trạng thái hiện hành, cập nhật **2026-11-13**. Source/runtime kiểm chứng mới có độ tin cậy cao hơn tài liệu. Không dùng file này làm quyền chạy thao tác thật trên hệ thống ngoài. Toàn bộ lịch sử phát triển theo thời gian xem tại [CHANGELOG.md](CHANGELOG.md); các quyết định kiến trúc & quy trình xem tại [PROJECT_DECISIONS.md](PROJECT_DECISIONS.md).

## Tổng quan kiểm chứng & năng lực hiện hành

| Hạng mục | Trạng thái đã kiểm chứng gần nhất (`2026-11-13`) |
|---|---|
| Cổng chất lượng cô lập (`.\CHECK_QUALITY.ps1 -Isolated`) | `QUALITY GATE PASSED` (exit `0`), `719/719` regression tests `OK`, branch coverage `78.9%` |
| Static analysis & Bảo mật | Ruff `0` lỗi, Mypy `0` lỗi trên `78` source files, `secrets=0` |
| Hợp đồng Frontend JS (`node tests/check_template_js.js`) | `template-js-contract-ok scripts=7 handlers=37 behavior=13` (exit `0`) |
| Auto Discovery, Quét Zalo song song & Tích BCNB | Đã triển khai đầy đủ: quét chọn lọc (`selected_target_keys`), duyệt tay 1-Click có vết SHA-256, hoãn buổi học chưa kết thúc (`⏳ Chưa tới giờ kiểm tra`), AI gợi ý báo cáo Zalo (`DEC-036`, `DEC-045`) |
| Kiểm định chất lượng LMS, SPCK & Ghi chú QC Teaching | Đã triển khai: Tier-1 Bulk GraphQL + Phễu 2 tầng AI nhận xét LMS khử PII, kiểm định SPCK Buổi 12–14 theo điểm danh (miễn PTB/CSB `DEC-044`, quy tắc Robotics `DEC-045`), tự động hợp nhất & ghi `Mô tả vấn đề (TE/TF/QC)` có read-back (`DEC-038`) |
| Báo cáo tuần, Xuất CSV lỗi & Email Quản lý | Đã triển khai: bảng 9 cột, lỗi thiếu Zalo mức đỏ nặng nhất (`❌ Chưa nhận xét`, `#dc2626` bọc `<span>` tương thích Webmail BizFly Cloud), cảnh báo vàng `⚠️` (`#b45309`) cho SPCK thiếu nộp/thiếu nhận xét (`DEC-046`), khóa cứng `MANAGER_EMAIL_ALLOWLIST` |
| Tick LMS tự động & Nộp Google Form | Đã triển khai: quét Web LMS lập kế hoạch chọn từng lớp (`selection_digest`), lấy học viên trực tiếp từ LMS cho Google Form kèm idempotency & ảnh bằng chứng ẩn danh |
| Quy trình điều phối AI & Đồng bộ mã nguồn | Cơ chế 2 luồng `[FAST-TRACK]` / `[FULL GATE]` (`DEC-047`), nhánh chính chuẩn hóa `main` (`origin/main`) làm Source of Truth (`DEC-040`) |

## Các mốc cập nhật gần nhất

- **2026-11-13 (Bản đồ mã nguồn Graphify cục bộ & chặn dữ liệu thật — `[FAST-TRACK]`)**:
  - Thêm `.graphifyignore` chặn sạch session/DB/runtime/upload/secret, ẩn `graphify-out/` trong `.gitignore`, và dựng bản đồ AST cục bộ (`GRAPHIFY_BACKEND=none`, `--code-only`: `225` code files, `4,847` nodes, `14,514` edges, `168` communities, `0` rò rỉ dữ liệu thật).
- **2026-11-13 (Chuẩn hóa nhánh `main`, nâng cấp `README.md` & Tinh gọn tài liệu — `DEC-047`, `[FAST-TRACK]`)**:
  - Đổi nhánh mặc định trên cả GitHub (`origin/main`) và máy cục bộ sang `main`, đồng thời viết lại `README.md` chuẩn GitHub (badges, 4 luồng tính năng hiện hành, sơ đồ kiến trúc Mermaid, cây thư mục, hướng dẫn cài đặt & an toàn vận hành không lộ dữ liệu nhạy cảm).
  - Phân tách **Luồng Nhanh (`[FAST-TRACK]`)** và **Luồng Đầy đủ (`[FULL GATE]`)**; dọn sạch nội dung cũ mâu thuẫn trong `AGENTS.md`, `GEMINI.md`, `HANDOFF.md` và rút gọn `PROJECT_STATE.md` đúng vai trò snapshot ngắn.
- **2026-11-13 (Phase 6.6 & Hotfix — Phân cấp lỗi đỏ Zalo `❌` vs cảnh báo vàng SPCK `⚠️`, `DEC-046`, `[FULL GATE]`)**:
  - Thiếu nhận xét Zalo (`CATEGORY_MISSING`) là lỗi đỏ cao nhất (`❌ Chưa nhận xét`, `❌ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo`); giữ `CATEGORY_MISSING` cho `NOT_FOUND_VERIFIED` kèm `write_status="FAILED_BEFORE_WRITE"` (`EVIDENCE_NOT_READY`), và bọc `<span style="color:#dc2626;font-weight:700;">` / `<span style="color:#b45309;font-weight:600;">` để hiển thị chuẩn trên BizFly Cloud Webmail (đã kiểm chứng trên `Job #500`, `719/719 PASS`, `Sol 6.1 high` `VERDICT: PASS`).
- **2026-11-12 (Phase 6.5 — Chuẩn hóa FAIL/NOTE QC, Phễu 2 tầng AI nhận xét LMS khử PII & Hoãn buổi chưa kết thúc, `DEC-045`, `[FULL GATE]`)**:
  - Hoãn kiểm tra buổi tương lai/chưa hết giờ học; chuẩn hóa `FAIL` vs `NOTE` trên LMS/SPCK/Robotics; tích hợp `GeminiLmsCommentReviewerGateway` khử PII hai lớp (JS + Python) kèm fallback tất định (`715+ PASS`, `Sol 6.1 xhigh` `VERDICT: PASS`).

## Ranh giới an toàn & Lưu ý vận hành mở

1. **Một instance duy nhất**: SQLite (`database.db`), session và persistent browser profile (`zalo-profile.lock`) thiết kế cho 1 tiến trình vận hành tại một thời điểm; khi ứng dụng đang mở giữ lock trình duyệt, gate đầy đủ luôn chạy qua chế độ cô lập `-Isolated`.
2. **Đồng bộ Zalo Web trên máy**: Khi lịch sử tin nhắn trên trình duyệt chưa đồng bộ đủ cửa sổ buổi học, hệ thống dừng fail-closed ở `INSUFFICIENT_EVIDENCE` (`CATEGORY_REVIEW`), không suy đoán thiếu báo cáo.
3. **Thao tác ghi hệ thống ngoài (BCNB/Teaching, LMS, Google Form, SMTP)**: Luôn yêu cầu đúng tuần/target đã xác minh, preview/audit trước khi ghi và sự cho phép rõ ràng của Owner khi chạy live.
