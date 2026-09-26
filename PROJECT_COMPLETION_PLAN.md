# Kế hoạch hoàn thiện Zalo Automation

> Roadmap hiện hành từ 2026-09-18. Trạng thái thực tế nằm trong
> `PROJECT_STATE.md`; lịch sử quyết định nằm trong `PROJECT_DECISIONS.md`.

## Mục tiêu hoàn thành

Dự án chỉ được coi là hoàn thành khi một người vận hành có thể chạy từng luồng
với bằng chứng rõ ràng, phục hồi được khi lỗi và không vô tình ghi sai dữ liệu
bên ngoài.

## Thứ tự ưu tiên

### P0 — Ổn định mốc nguồn

- [x] Chuẩn hóa tài liệu trung tâm theo source hiện tại.
- [x] `CHECK_QUALITY.ps1 -Isolated` đạt ngày 2026-09-18: 525 test, coverage
  77,1%, exit `0`.
- [x] Review toàn bộ thay đổi sản phẩm đang chưa commit.
- [x] Tạo checkpoint Git local an toàn tại commit `624143f4ea54c7e2bc7e57191edd6e77d9d54bf4`
  (185 files); không commit secret/runtime; sinh ZIP sạch `F:\FINAL_PROJECT\zaloAutomation-git-checkpoint-624143f.zip`.

Hoàn thành khi: quality gate xanh, diff đã được review và có điểm quay lại rõ
ràng. Không yêu cầu chạy trình duyệt hoặc dịch vụ thật.

### P1 — Nghiệm thu local/dry-run

- [ ] Health/readiness và khởi động lại ứng dụng ổn định.
- [ ] Discovery tạo snapshot đúng hai tuần và danh sách gap có thể review.
- [ ] Zalo scan dry-run xử lý đủ target; target thiếu đồng bộ giữ trạng thái cần
  xem xét thay vì kết luận âm tính.
- [ ] Google Form OCR và dry-run hoạt động với ảnh đại diện đã loại dữ liệu nhạy cảm.
- [ ] LMS analysis tạo plan đúng; token hết hạn/dùng lại bị từ chối.
- [ ] Backup SQLite và restore drill trên bản sao tạm thành công.
- [ ] Sửa các chuỗi log tiếng Việt bị lỗi mã hóa và thêm một kiểm tra nhỏ để
  tránh tái diễn.

Hoàn thành khi: mỗi luồng có lệnh/job mới, kết quả, timestamp và giới hạn bằng
chứng. Không dùng test xanh thay cho kiểm tra hành vi nhìn thấy được.

### P2 — Canary ghi thật có giới hạn

- [ ] Owner chọn chính xác một target và duyệt bản xem trước mới.
- [ ] Tạo backup trước khi chạy và xác minh rollback.
- [ ] Chạy đúng một canary; không chuyển batch dry-run thành quyền ghi hàng loạt.
- [ ] Kiểm tra read-back và external-action audit sau thao tác.
- [ ] Nếu outcome `UNKNOWN`, dừng và kiểm tra thủ công; không retry.

Hoàn thành khi: target thật được xác minh bằng read-back, không có audit treo và
Owner chấp nhận kết quả. Mỗi hệ thống ngoài cần gate riêng.

### P3 — Staging/production nếu thực sự cần

- [ ] Chỉ thực hiện khi Owner yêu cầu truy cập qua mạng hoặc vận hành lâu dài.
- [ ] Dùng một replica, HTTPS reverse proxy, web auth, Secure cookie và allowlist.
- [ ] Lưu secret ngoài Git và chạy smoke chỉ-đọc qua URL staging.
- [ ] Có quy trình cập nhật, giám sát, backup và quay lại bản trước.

Hoàn thành khi: staging được nghiệm thu riêng. Local acceptance không tự mở
quyền staging hoặc production.

## Điều kiện dừng

Dừng ngay và báo Owner nếu gặp một trong các trường hợp:

- target, tuần, ngày hoặc identity mơ hồ;
- snapshot/plan hết hạn hoặc khác dữ liệu vừa đọc lại;
- profile Zalo bị process khác giữ;
- lịch sử Zalo chưa đồng bộ;
- audit `INTENT_RECORDED`/`UNKNOWN` chưa được xử lý;
- backup hoặc restore drill thất bại;
- phát hiện secret/dữ liệu thật sắp được đưa vào Git;
- thay đổi ngoài phạm vi task so với baseline.

## Cách quay lại

- Tài liệu: dùng Git hoặc bản ZIP được tạo trước lần chuẩn hóa.
- Source: chỉ quay lại bằng commit/checkpoint đã xác định; không dùng
  `reset --hard` trên working tree đang có thay đổi.
- Database: dùng backup đã kiểm tra integrity và restore drill; không chép đè
  database đang chạy.
- Hệ thống ngoài: dựa trên read-back/audit và thao tác phục hồi được Owner duyệt.

## Cập nhật kế hoạch ổn định và công cụ cho Owner — 2026-09-26

Phần này là checklist thực thi của kế hoạch hiện tại. `[x]` chỉ có nghĩa là
source và kiểm tra phù hợp đã đạt; nghiệm thu Zalo/BCNB thật vẫn là một human
gate riêng.

### Phase 0 — Bảo vệ công việc hiện có

- [x] Đọc `PROJECT_SUMMARY.md`, `PROJECT_STATE.md`,
  `PROJECT_DECISIONS.md` và file kế hoạch này; xác nhận ngày 2026-09-26.
- [x] Ghi nhận `git status --short` và SHA-256 của các file dirty trước task;
  các file cũ ngoài phạm vi không bị reset/xóa.
- [x] Giữ nguyên `.env`, database, browser profile và dữ liệu thật; không chạy
  job Zalo/BCNB trong task.
- [x] Append quyết định GREEN/YELLOW/RED, không force-run và một profile Zalo
  chỉ dùng một phiên production ổn định vào `PROJECT_DECISIONS.md` (DEC-030).

### Phase 1 — Preflight và chốt server

- [x] Có service preflight tổng hợp read-only tại `services/zalo_preflight.py`;
  response chỉ chứa mã kiểm tra, màu, hướng dẫn, thời điểm, mode và plan đã
  rút gọn, không chứa secret/cookie/nội dung học viên thô.
- [x] Kiểm tra plan hai tuần, plan stale, snapshot chưa tin cậy, target thiếu
  identity và target trùng; lỗi trả về theo mã có cấu trúc.
- [x] Kiểm tra active job, production readiness, database/readiness, profile
  lock, audit `INTENT_RECORDED`/`UNKNOWN` và khả năng tạo backup SQLite tạm.
- [x] Thêm `GET /api/zalo-scan-preflight` và panel dashboard hiển thị mode thật,
  target, worker, màu và hướng dẫn Owner.
- [x] `RED` khóa nút và chặn `/run-scan`; `YELLOW` production bắt buộc
  `confirm_yellow`; không có route hoặc tham số force-run.
- [x] Server gọi lại preflight ngay trước khi tạo job, không tin kết quả cũ của
  giao diện.
- [x] Chốt thêm admission nguyên tử tại `JobRepository.create_job` bằng
  `BEGIN IMMEDIATE`: nếu một job khác vừa được tạo cạnh tranh, job Zalo thứ hai
  bị từ chối thay vì được xếp hàng âm thầm; có regression test riêng.
- [x] Production trả worker Zalo bằng `1` trên mỗi profile; dry-run giữ cấu
  hình hiện tại. `get_job_targets` báo đúng mode/worker theo job.
- [x] Kiểm tra ngày 2026-09-26: `tests.test_api_routes_unit` 30/30 và
  `tests.test_zalo_preflight` 2/2; targeted combined gate 102/102, exit `0`.
  Các ca GREEN/YELLOW/RED, active job, audit, backup, plan malformed và
  confirmation đã có test.

### Phase 2 — Zalo và TargetClosedError

- [x] Giữ lại cơ chế timeout/cleanup hiện có; task bị hủy được thu hồi trước
  khi page retry bị đóng. Regression suite hai tab shared connection không còn
  unhandled future.
- [x] Production không mở song song hai worker trên cùng profile; không tự đóng
  tab Owner. Khi profile lock bị process khác giữ, preflight chặn.
- [x] Kết quả kỹ thuật và nghiệp vụ vẫn tách riêng: `NOT_FOUND_VERIFIED`,
  `INSUFFICIENT_EVIDENCE`, `SCAN_FAILED`, `BLOCKED_BATCH_HALT`.
- [ ] Chưa có bằng chứng live rằng popup “đang mở Zalo trên Tab khác” được phát
  hiện trước khi mở browser Owner; hiện worker vẫn kiểm tra landing/auth ở điểm
  setup và fail-closed nếu phiên không sẵn sàng.
- [ ] Chưa kiểm chứng nhiều profile production độc lập; không tăng worker cho
  đến khi có profile riêng và canary được Owner duyệt.

### Phase 3 — Báo cáo và hàng đợi xem lại

- [x] `/api/jobs/{id}/targets` trả report read-only: checked, submitted,
  not-found, thiếu evidence, lỗi kỹ thuật và batch halt.
- [x] Endpoint trả `review_queue` cho target thiếu evidence, lỗi kỹ thuật,
  `UNKNOWN` hoặc bị batch halt; không có thao tác tự ghi BCNB.
- [x] Dashboard hiển thị tóm tắt sau job và danh sách target cần xem lại; ảnh
  evidence vẫn mở qua endpoint kiểm soát đường dẫn.
- [x] Giá trị cảnh báo HTML/PHP từ nguồn ngoài được thay bằng “Dữ liệu nguồn
  không hợp lệ” trong phần nhóm/giáo viên; không fuzzy-match.
- [ ] Lịch sử job riêng với bộ lọc nâng cao chưa thêm mới; màn hình Nhật ký,
  `/api/jobs/latest`, timeline và target read-only hiện là nguồn xem lịch sử.

### Phase 4 — Gate và rollout

- [x] `node tests/check_template_js.js` pass sau khi thêm DOM contract preflight/
  review queue (ngày 2026-09-26).
- [x] Targeted API, production-readiness, timeout lifecycle, app-factory và
  preflight backup tests pass (số cuối ghi trong `PROJECT_STATE.md`).
- [x] `CHECK_QUALITY.ps1 -Isolated` ngày 2026-09-26: exit `0`,
  `QUALITY GATE PASSED`, 560 test, coverage 76,9%; Ruff, mypy, invariant,
  frontend/app smoke và compile đều pass.
- [ ] Chưa chạy dry-run live, canary hoặc production batch trong task này.
- [ ] Chưa có production acceptance; chỉ Owner mới quyết định mở human gate.

### Rollback an toàn

- [x] Rollback chỉ dùng patch ngược/Git checkpoint cho các file thuộc task;
  không dùng `git reset --hard`, `git clean`, xóa database, xóa profile hoặc
  đổi `.env`.
- [ ] Nếu quality gate cuối thất bại: giữ log/diff, không chạy production,
  đánh dấu mục tương ứng `[ ]`, rồi khôi phục riêng patch phase lỗi và kiểm tra
  lại manifest SHA-256.
