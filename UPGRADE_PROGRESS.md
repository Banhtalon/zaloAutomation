# KẾ HOẠCH CHI TIẾT & TIẾN ĐỘ NÂNG CẤP DỰ ÁN (UPGRADE PLAN & PROGRESS)

**Cập nhật lần cuối:** `2026-10-31 07:42 +07:00`
**Trạng thái hiện tại:** Đã hoàn tất **Phase 0 → Phase 3.2 (`45/45` ✅)**, hoàn tất **Wave 4 (Phase 4.0 → Phase 4.4: `25/25` ✅ — Kiểm định chất lượng nội dung LMS/Zalo, Tự động ghi `Mô tả vấn đề` lên Teaching & Bảng Email Quản lý 9 cột)**, và hoàn tất **Wave 5 (Phase 5.0 → 5.8: `59/59` ✅ — Hardening toàn diện luồng kiểm tra báo cáo)**.

---

## 📊 BẢNG THEO DÕI TIẾN ĐỘ TỔNG THỂ (MASTER CHECKLIST)

| Mã giai đoạn | Hạng mục nâng cấp | Mức ưu tiên | Trạng thái | Tiến độ |
| :--- | :--- | :---: | :---: | :---: |
| **Phase 0** | **Sao lưu toàn diện, Chuẩn bị môi trường & Đóng gói Baseline** | P0 (Bắt buộc) | ✅ Hoàn thành | `6/6` |
| **Phase 1.1** | **Copy danh sách nhắc giáo viên 1-Click (Zalo & LMS)** | P1 (Cao) | ✅ Hoàn thành | `5/5` |
| **Phase 1.2** | **Quét lại có chọn lọc (Selective Re-scan các lớp lỗi/nộp bù)** | P1 (Cao) | ✅ Hoàn thành | `6/6` |
| **Phase 1.3** | **Duyệt tay & Tích BCNB 1-Click có lưu vết (Manual Override)** | P1 (Cao) | ✅ Hoàn thành | `6/6` |
| **Phase 2.1** | **Nâng cấp bảng Xem trước LMS chi tiết (Buổi nợ + Chọn từng lớp)** | P2 (Trung bình) | ✅ Hoàn thành | `6/6` |
| **Phase 2.2** | **Lấy danh sách học viên trực tiếp từ LMS cho Google Form** | P2 (Trung bình) | ✅ Hoàn thành | `6/6` |
| **Phase 3.1** | **Bộ phân loại Báo cáo Zalo Hybrid (Rule-based + AI gợi ý)** | P3 (Mở rộng) | ✅ Hoàn thành | `5/5` |
| **Phase 3.2** | **Tra cứu Lịch sử theo Tuần & Xuất báo cáo CSV/Excel** | P3 (Mở rộng) | ✅ Hoàn thành | `5/5` |
| **Phase 4.0** | **Sao lưu toàn diện Pre-Wave 4 (SQLite + Restore Drill + Source ZIP)** | P0 (Bắt buộc) | ✅ Hoàn thành | `3/3` |
| **Phase 4.1** | **Đọc mở rộng bảng Teaching (`check_nhanxet.php`) & Đồng bộ trạng thái nộp LMS/Zalo** | P1 (Cao) | ✅ Hoàn thành | `5/5` |
| **Phase 4.2** | **Kiểm định chất lượng LMS (Nội dung bài + Nhận xét HV Buổi thường/Checkpoint) & Zalo** | P1 (Cao) | ✅ Hoàn thành | `6/6` |
| **Phase 4.3** | **Tự động ghi nhận vấn đề vào ô `Mô tả vấn đề (TE/TF/QC)` trên Teaching** | P1 (Cao) | ✅ Hoàn thành | `5/5` |
| **Phase 4.4** | **Bảng Email Quản lý định dạng Table + CSV lỗi chất lượng & Đồng bộ tài liệu** | P1 (Cao) | ✅ Hoàn thành | `6/6` |
| **Phase 5.0** | **Baseline & sao lưu trước Wave 5** | P0 (Bắt buộc) | ✅ Hoàn thành | `4/4` |
| **Phase 5.1** | **Độ bền vòng quét Zalo (retry, timeout, chụp lại ảnh)** | P0 | ✅ Hoàn thành | `5/5` |
| **Phase 5.2** | **Cô lập ghi BCNB: tick vs ghi chú `Mô tả vấn đề`** | P0 | ✅ Hoàn thành | `6/6` |
| **Phase 5.3** | **Đúng đắn bằng chứng & QC (slot LMS, note rò rỉ, trạng thái không xác định)** | P0 | ✅ Hoàn thành | `6/6` |
| **Phase 5.4** | **Toàn vẹn Weekly summary / Email / Duyệt tay** | P0 | ✅ Hoàn thành | `7/7` |
| **Phase 5.5** | **Một nguồn sự thật: `report_status.py` + helper dùng chung** | P1 | ✅ Hoàn thành | `8/8` |
| **Phase 5.6** | **Không rơi dòng im lặng (Discovery/Plan/Preflight)** | P1 | ✅ Hoàn thành | `8/8` |
| **Phase 5.7** | **Giao diện vận hành bám dữ liệu server** | P1 | ✅ Hoàn thành | `8/8` |
| **Phase 5.8** | **Dọn dẹp, tài liệu & nghiệm thu Wave 5** | P2 | ✅ Hoàn thành | `7/7` |

---

## ✅ PHASE 0: SAO LƯU TOÀN DIỆN & CHUẨN BỊ NỀN TẢNG (ĐÃ HOÀN THÀNH)

> **Ghi chú chuẩn bị:** Mọi thay đổi trong lộ trình nâng cấp đều có điểm khôi phục độc lập đã kiểm chứng (`integrity=ok`, `restore_drill_ok=true`). Nếu bất kỳ bước nâng cấp nào phát sinh rủi ro, có thể khôi phục nguyên trạng trong vòng 10 giây theo hướng dẫn tại `.backups/pre-upgrade-20261022/ROLLBACK_INSTRUCTIONS.md`.

### Checklist Phase 0
- [x] **0.1. Hoàn tất tinh gọn Trang chính & Tách tab Thông số kỹ thuật (`DEC-034`)**:
  - Đã rút gọn `templates/partials/dashboard.html` và đưa toàn bộ telemetry sang `templates/partials/specs.html`.
- [x] **0.2. Hoàn tất tự động quét web LMS MindX & Tự động đăng nhập SSO/BCNB (`DEC-035`)**:
  - Đã nâng cấp `services/lms_service.py`, `services/lms_plan.py`, `api/lms_routes.py`, chống sót lớp khi chuyển trang bằng `_wait_for_lms_table_settled`.
- [x] **0.3. Vá lỗi nhận diện nhầm thông báo khai giảng trên Zalo (`classifiers/rule_classifier.py`)**:
  - Đã áp dụng `penalty:class_manager_or_opening_notice` và kiểm tra `has_report_substance` kèm unit test hồi quy.
- [x] **0.4. Khởi tạo tài liệu lịch sử phát triển dự án (`CHANGELOG.md`)**:
  - Đã ghi nhận đầy đủ lịch sử từ `v1.0` đến `2026-10-20`.
- [x] **0.5. Sao lưu nóng SQLite (`database.db`) kèm diễn tập khôi phục (`restore_drill`)**:
  - File backup: `.backups/db/pre-upgrade-20261022-212728.sqlite3` (`36,237,312` bytes)
  - SHA-256: `60A6ED77446E5A024FD47928EDD2358578780B585D785404AD43372D8A6C3DBF`
  - Kết quả kiểm định: `integrity="ok"`, `restore_drill_ok=true` (`scan_jobs=365`, `external_action_audit=1626`, `schema_migrations=4`).
- [x] **0.6. Đóng gói bản chụp mã nguồn & cấu hình (`workspace_snapshot` + ZIP + `MANIFEST.json`)**:
  - File ZIP: `.backups/pre-upgrade-20261022/source-code-snapshot-20261022.zip` (`386` files, `11,405,771` bytes)
  - SHA-256: `CC1FAF688BB91C00346083D075029AA1AD64A52974AB4554FA5A73E069705A06`
  - Manifest & hướng dẫn khôi phục: `.backups/pre-upgrade-20261022/MANIFEST.json` & `.backups/pre-upgrade-20261022/ROLLBACK_INSTRUCTIONS.md`.

---

## 🚀 PHASE 1: TỐI ƯU TỐC ĐỘ & THAO TÁC VẬN HÀNH HÀNG NGÀY (ƯU TIÊN CAO - P1)

### Hạng mục 1.1 — Copy Danh Sách Nhắc Giáo Viên 1-Click (Zalo & LMS) ✅
- **Mục tiêu**: Sau khi quét Zalo hoặc phân tích LMS xong, tạo sẵn nội dung tin nhắn nhắc việc gom theo từng Giáo viên phụ trách (`NguoiPhuTrach`) kèm Mã lớp, Buổi học và Ngày học để dán ngay vào nhóm vận hành.
- **File dự kiến tác động**:
  - `templates/partials/dashboard.html`, `templates/partials/lms_modal.html`
  - `static/js/jobs.js`, `static/js/lms.js`
  - `tests/check_template_js.js`, `tests/check_app_smoke.py`
- **Checklist triển khai**:
  - [x] **1.1.1.** Thiết kế hàm tổng hợp danh sách lớp chưa báo cáo Zalo (`❌` / `⚠️`) từ `latestZaloResults`, gom nhóm theo `person` (`NguoiPhuTrach`), khóa chặt theo tuần bắt buộc (`requiredDashboardPeriod()`) và định dạng văn bản tiếng Việt rõ ràng theo Tuần (`Wxx/yyyy`).
  - [x] **1.1.2.** Thêm nút **"📋 Copy nhắc GV"** (`#btn-copy-reminders`) trên thanh công cụ bảng Kết quả Zalo (`templates/partials/dashboard.html`) kèm thông báo xác nhận khi đã sao chép vào Clipboard.
  - [x] **1.1.3.** Bổ sung nút **"📋 Copy"** (`#btnCopyLmsMissing`) ngay trong bước Xem trước của Modal LMS (`templates/partials/lms_modal.html` & `static/js/lms.js`).
  - [x] **1.1.4.** Đảm bảo fail-closed khi chưa xác định tuần cần báo cáo và không làm lộ dữ liệu nhạy cảm ngoài thông tin mã lớp, buổi học và tên giáo viên phụ trách hiển thị trên bảng.
  - [x] **1.1.5.** Viết kiểm thử tự động `checkTeacherReminderCopyHelpers` trong `tests/check_template_js.js`, vượt qua `CHECK_QUALITY.ps1 -Isolated` và đạt `PASS` từ Reviewer độc lập `gpt-6.1-sol (xhigh)` (Session `01a1641f-6e09-77b0-848d-cf1e4c1ad8d4`).

---

### Hạng mục 1.2 — Quét Lại Có Chọn Lọc (Selective Re-scan các lớp lỗi / vừa báo cáo bù) ✅
- **Mục tiêu**: Cho phép chỉ quét lại các lớp đang ở trạng thái `❌ Chưa báo cáo` / `⚠️ Cần xem lại` hoặc quét lại 1 lớp cụ thể mà không phải chạy lại toàn bộ 40–50 lớp từ đầu.
- **File dự kiến tác động**:
  - `api/zalo_routes.py`
  - `services/zalo_parallel_service.py`, `services/zalo_service.py`, `services/zalo_bcnb_service.py`
  - `templates/partials/dashboard.html`, `static/js/jobs.js`, `static/js/state.js`
  - `tests/check_template_js.js`, `tests/test_api_routes_unit.py`, `tests/test_zalo_parallel_service.py`
- **Checklist triển khai**:
  - [x] **1.2.1.** Mở rộng payload `POST /run-scan` nhận danh sách `selected_target_keys` nằm trong snapshot hai tuần hợp lệ hiện hành (chặn rỗng `422`, trùng `422`, sai kiểu `422`, ngoài snapshot `409 SCAN_SELECTED_TARGET_NOT_IN_PLAN`).
  - [x] **1.2.2.** Trong `prepare_frozen_scan_execution` (`services/zalo_parallel_service.py`) và `update_bcnb_reports` (`services/zalo_bcnb_service.py`), lọc `scan_plan["targets"]` và dòng BCNB theo `selected_target_keys` (bao gồm cả chốt bảo vệ miễn báo cáo Buổi 14 không tự động tác động lên dòng không chọn).
  - [x] **1.2.3.** Khi job quét chọn lọc chạy/hủy/hoàn tất, bảo toàn và hợp nhất (merge) kết quả mới vào bảng kết quả hiện tại trên Dashboard theo đúng gốc `(periodKey, snapshotId)` (chặn trộn khác snapshot hoặc dữ liệu cũ thiếu `snapshot_id`).
  - [x] **1.2.4.** Thêm nút **"🔄 Quét lại lớp lỗi"** (`#btn-rescan-failed`) trên thanh lọc của bảng Kết quả và nút **"Quét lại"** trên từng dòng lỗi (`❌`/`⚠️`).
  - [x] **1.2.5.** Giữ nguyên khóa nhận job nguyên tử (`BEGIN IMMEDIATE`, `reject_if_active=True`) và chốt xác nhận Preflight an toàn.
  - [x] **1.2.6.** Bổ sung unit test (`tests/test_api_routes_unit.py`, `tests/test_zalo_parallel_service.py`, `tests/check_template_js.js`), vượt qua `.\CHECK_QUALITY.ps1 -Isolated` (exit `0`, coverage `77.0%`) và đạt **PASS** từ `gpt-6.1-sol (xhigh)` (Session `01a16443-e06c-7eb0-812c-f7db96ccb5a8`).

---

### Hạng mục 1.3 — Duyệt Tay & Tích BCNB 1-Click Có Lưu Vết (Human-in-the-loop Manual Approve) ✅
- **Mục tiêu**: Khi một lớp bị dừng an toàn (`⚠️` / `❌`) do giáo viên viết báo cáo sai cú pháp nhưng người vận hành đã xem ảnh bằng chứng trên Dashboard và xác nhận đúng là báo cáo hợp lệ, cho phép bấm 1 nút để worker tự tích ô đó trên BCNB và lưu vết kiểm toán.
- **File dự kiến tác động**:
  - `api/zalo_routes.py`
  - `services/zalo_bcnb_service.py`, `services/bcnb_actions.py`, `services/job_dispatcher.py`
  - `repositories/job_repository.py`
  - `static/js/jobs.js`
  - `tests/check_template_js.js`, `tests/test_api_routes_unit.py`, `tests/test_zalo_parallel_service.py`
- **Checklist triển khai**:
  - [x] **1.3.1.** Tạo API `POST /api/zalo-manual-approve` (yêu cầu quyền `Operator`/`Admin`) nhận `job_id`, `group_name`, `class_code`, `week`, `year`, `lesson`, `class_date_iso` và `evidence_path` (bắt buộc bằng chứng thuộc target và `AVAILABLE` trong `EvidenceRepository`).
  - [x] **1.3.2.** Tạo tác vụ nền `ZALO_MANUAL_APPROVE` sử dụng `exclusive_browser_job` mở trang BCNB tuần tương ứng, kiểm tra khớp duy nhất trên toàn bảng BCNB (`find_bcnb_match(group_name, bcnb_rows)` + khớp mã lớp/ngày học/buổi học) và gọi `execute_verified_checkbox_action` để tích chọn + đọc ngược xác nhận từ server (`evidence_job_id=source_job_id`).
  - [x] **1.3.3.** Ghi bản ghi kiểm toán vào bảng `external_action_audit` với `action_type="BCNB_MANUAL_APPROVE_TICK"`, khóa `action_key` tách riêng theo `execution_mode`, đính kèm SHA-256 của ảnh bằng chứng và người xác nhận (`approved_by`).
  - [x] **1.3.4.** Thêm nút **"✅ Duyệt & Tích"** trên dòng kết quả `❌`/`⚠️` (chỉ hiển thị khi dòng đó đã có ảnh bằng chứng `AVAILABLE`, `scan_target` có `class_code` hợp lệ và xác định được `source_job_id` gốc kể cả sau khi quét chọn lọc).
  - [x] **1.3.5.** Chỉ cập nhật trạng thái dòng sang `✅ Đã duyệt tay & Tích BCNB` (`submitted_for_bcnb=True`, `MANUAL_APPROVED_VERIFIED`) khi chạy `production` và nhận kết quả `VERIFIED`; ở chế độ `dry-run` chỉ ghi nhận mô phỏng mà không khóa dòng.
  - [x] **1.3.6.** Viết unit test (`tests/check_template_js.js`, `tests/test_api_routes_unit.py`, `tests/test_zalo_parallel_service.py`), vượt qua `.\CHECK_QUALITY.ps1 -Isolated` (exit `0`, coverage `77.0%`) và đạt **PASS** từ `gpt-6.1-sol (xhigh)` (Session `01a16475-9d98-75b2-b895-c7804835822d`).

---

## ⚡ PHASE 2: TỰ ĐỘNG HÓA SÂU HƠN CHO LMS & GOOGLE FORM (ƯU TIÊN TRUNG BÌNH - P2)

### Hạng mục 2.1 — Nâng Cấp Bảng Xem Trước LMS Chi Tiết & Chọn Từng Lớp ✅
- **Mục tiêu**: Hiển thị rõ lớp đang nợ nhận xét ở **Buổi số mấy** trên LMS, trạng thái hiện tại trên BCNB, và cho phép người dùng tích/bỏ tích từng lớp cụ thể ngay trên Modal Preview LMS trước khi thực thi.
- **File dự kiến tác động**:
  - `services/lms_service.py`, `services/lms_plan.py`, `services/job_dispatcher.py`
  - `api/lms_routes.py`, `repositories/lms_repository.py`
  - `templates/partials/lms_modal.html`, `static/js/lms.js`, `static/js/jobs.js`, `static/js/state.js`
  - `tests/test_lms_plan.py`, `tests/test_lms_service.py`, `tests/test_api_routes_unit.py`, `tests/check_template_js.js`
- **Checklist triển khai**:
  - [x] **2.1.1.** Mở rộng kết quả quét LMS trong `services/lms_service.py` và `services/lms_plan.py` để lưu chi tiết `overdue_details` (`class_code` -> chuỗi buổi quá hạn cụ thể, ví dụ `"Buổi 3, Buổi 4"`, gộp buổi theo token không bị trùng chuỗi con và ràng buộc vào `plan_digest`).
  - [x] **2.1.2.** Hiển thị kèm số buổi nợ nhận xét và nhãn trạng thái BCNB (`BCNB: Chưa tích`, `BCNB: Đã tích ⚠️`, `Ngoài BCNB tuần này`) bên cạnh mỗi mã lớp trong cột **"Lớp chưa nhận xét (trên LMS)"** của `templates/partials/lms_modal.html` và khi copy danh sách nhắc giáo viên.
  - [x] **2.1.3.** Chuyển danh sách **"Lớp sẽ tích Đã nhận xét"** trong Modal LMS thành danh sách có checkbox cho từng lớp + nút Chọn tất cả / Bỏ chọn tất cả (`toggleLmsTickClass`, `toggleAllLmsTickSelection`) kèm khóa in-flight (`lmsConfirmInFlight`) trước khi bấm **Xác nhận & Tick BCNB**.
  - [x] **2.1.4.** Cập nhật `POST /api/lms/confirm` và alias `POST /run-lms` (`api/lms_routes.py`) cùng `LmsRepository.consume_confirmation` cho phép truyền tập con `selected_classes_to_tick` nằm trong `plan` đã ký (`confirmation_token`), sinh `selection_digest` ràng buộc với `plan_digest` và `execution_job_id`.
  - [x] **2.1.5.** Giữ nguyên cơ chế fail-closed: chặn rỗng/trùng/sai định dạng (`422`) và chặn mã lớp ngoài bản xem trước đã ký (`409 LMS_SELECTED_CLASS_NOT_IN_PLAN`) mà không làm mất hiệu lực `confirmation_token`; tại bước thực thi `run_lms_automation`, đối chiếu `get_consumed_selection_digest` theo `execution_job_id` trước khi mở trình duyệt.
  - [x] **2.1.6.** Cập nhật unit tests (`tests/test_lms_plan.py`, `tests/test_lms_service.py`, `tests/test_api_routes_unit.py`, `tests/check_template_js.js`), vượt qua `.\CHECK_QUALITY.ps1 -Isolated` (exit `0`, 575 tests, coverage `77.0%`) và đạt **PASS** từ `gpt-6.1-sol (xhigh)` (Session `01a164b5-4caa-70e0-a0e9-ac5331a0df46`).

---

### Hạng mục 2.2 — Lấy Danh Sách Học Viên Trực Tiếp Từ LMS Cho Google Form ✅
- **Mục tiêu**: Bổ sung tùy chọn lấy thẳng danh sách học viên (`Họ tên` + `Mã lớp`) từ trang quản trị MindX LMS theo Mã lớp mà không cần chụp ảnh màn hình và chạy OCR (vẫn giữ tính năng dán ảnh OCR cho trường hợp linh hoạt).
- **File dự kiến tác động**:
  - `services/lms_service.py`, `services/lms_plan.py`
  - `api/googleform_routes.py`, `services/job_dispatcher.py`
  - `templates/partials/googleform.html`, `static/js/googleform.js`, `static/js/jobs.js`, `static/js/state.js`
  - `googleform_autosubmit/form_submitter.py`, `services/googleform_service.py`
  - `tests/test_lms_plan.py`, `tests/test_lms_service.py`, `tests/test_api_routes_unit.py`, `tests/test_job_dispatcher_unit.py`, `tests/test_form_submitter.py`, `tests/test_googleform_submit_service.py`, `tests/check_template_js.js`
- **Checklist triển khai**:
  - [x] **2.2.1.** Khảo sát cấu trúc DOM trang chi tiết lớp học / danh sách học viên trên `lms.mindx.edu.vn` và viết hàm trích xuất danh sách học viên theo mã lớp (`_read_lms_students_by_classes`, kiểm tra khớp duy nhất mã lớp xuyên trang, kiểm tra phân trang đầy đủ, chặn bảng không có cột mã lớp trên trang landing).
  - [x] **2.2.2.** Tạo API `POST /api/googleform/fetch-lms-students` nhận một hoặc nhiều mã lớp (`MAX_LMS_STUDENT_CLASSES = 20`), chạy job nền `GOOGLEFORM_LMS_STUDENTS` và trả về đúng cấu trúc `students: [{name, class_code}]` tương thích với bảng hiện tại.
  - [x] **2.2.3.** Thêm ô nhập **"Lấy nhanh học viên từ LMS theo Mã lớp"** (`#lms-student-class-codes` + `#btnFetchLmsStudents`) trong tab Google Form (`templates/partials/googleform.html`) bên cạnh khung dán ảnh OCR truyền thống.
  - [x] **2.2.4.** Tự động sinh và bảo toàn khóa chống gửi trùng (`ensureGoogleFormIdempotencyKey` kèm `googleformBatchMarker` in-memory khi thiếu `localStorage`) ngay khi danh sách học viên từ LMS được nạp vào bảng và giữ nguyên khóa khi tải lại cùng danh sách.
  - [x] **2.2.5.** Đảm bảo giới hạn số lượng phần tử DOM qua `locator.count()` trước `.all()`, giới hạn học viên tối đa mỗi batch (`<= 200`), chặn học viên trùng tên trong cùng một lớp, và bảo vệ thông tin học viên (loại bỏ tên học viên khỏi log và tên file ảnh chụp màn hình).
  - [x] **2.2.6.** Viết unit test đầy đủ, vượt qua `.\CHECK_QUALITY.ps1 -Isolated` (exit `0`, coverage `76.6%`) và đạt **PASS** từ `gpt-6.1-sol (xhigh)` (Session `01a16550-9aa8-7042-99ee-3f1ab8a101fe`).

---

## 🌟 PHASE 3: PHÂN LOẠI THÔNG MINH & BÁO CÁO LỊCH SỬ (MỞ RỘNG - P3)

### Hạng mục 3.1 — Bộ Phân Loại Báo Cáo Zalo "Hybrid" (Rule-based + AI Gợi Ý Ca Mơ Hồ) ✅
- **Mục tiêu**: Giữ nguyên bộ quy tắc `classifiers/rule_classifier.py` làm chốt chặn quyết định chính, nhưng với các tin nhắn dài trong khung ngày học bị trượt Rule-based, tùy chọn dùng AI chấm phụ để gắn nhãn gợi ý `⚠️ Nghi ngờ là báo cáo (AI gợi ý - Cần duyệt)` giúp người vận hành không bỏ sót báo cáo viết khác mẫu.
- **File dự kiến tác động**:
  - `googleform_autosubmit/config.py`, `.env.example`, `CHECK_QUALITY.ps1`
  - `classifiers/rule_classifier.py`
  - `services/external_gateways.py`, `services/zalo_evidence.py`, `services/zalo_scan_evidence.py`, `services/zalo_parallel_service.py`, `services/zalo_service.py`
  - `tests/test_rule_classifier_dates.py`, `tests/test_zalo_scan_evidence.py`
- **Checklist triển khai**:
  - [x] **3.1.1.** Thêm cờ cấu hình tùy chọn `ZALO_AI_ASSIST_CLASSIFIER=false` (mặc định tắt để đảm bảo tính tất định, chỉ bật khi người dùng muốn; cô lập `false` trong `CHECK_QUALITY.ps1`).
  - [x] **3.1.2.** Chỉ gọi AI phân tích phụ khi chưa tìm thấy báo cáo hợp lệ (`not report_found`), có ngày buổi học hợp lệ (`target` hoặc `target_date`), tin nhắn nằm đúng trong khung ngày hợp lệ của buổi học (`is_in_window`), không vi phạm lỗi cấm (`penalty:class_manager_or_opening_notice`), có độ dài tối thiểu (`>= 80` ký tự) nhưng điểm Rule-based chưa đạt ngưỡng `threshold`, và giới hạn tối đa `3` lượt gọi AI cho mỗi nhóm trong một đợt quét (`_try_claim_ai_assist_group_slot`).
  - [x] **3.1.3.** **Nguyên tắc an toàn tuyệt đối**: Kết quả từ AI **không bao giờ tự động tích BCNB** (`report_found=False`, `submitted_for_bcnb=False`, `evidence_state="INSUFFICIENT_EVIDENCE"`, `decision="AI_SUGGESTED_REPORT_NEEDS_REVIEW"`); chỉ hiển thị nhãn gợi ý `⚠️ AI gợi ý có thể là báo cáo – Cần kiểm tra thủ công` trên bảng Kết quả kèm ảnh bằng chứng để người vận hành duyệt qua Hạng mục 1.3.
  - [x] **3.1.4.** Bổ sung `ZaloAiReportClassifierGateway` và `GeminiZaloReportClassifierGateway` trong `services/external_gateways.py` để kiểm thử cô lập (`-Isolated`) không gọi mạng thật và tự động đóng an toàn khi timeout/lỗi.
  - [x] **3.1.5.** Viết unit test kiểm chứng ranh giới không tự động mutate BCNB từ kết quả AI (`tests/test_rule_classifier_dates.py`, `tests/test_zalo_scan_evidence.py`), vượt qua `.\CHECK_QUALITY.ps1 -Isolated` (exit `0`, 582 tests, coverage `76.6%`) và đạt **PASS** từ `gpt-6.1-sol (xhigh)` (Session `01a1666f-96d1-7880-bee8-2903a96e14c4`).

---

### Hạng mục 3.2 — Tra Cứu Lịch Sử Theo Tuần & Xuất Báo Cáo Tổng Hợp (CSV/Excel) ✅
- **Mục tiêu**: Cho phép chọn xem lại kết quả quét của các tuần trước và xuất file CSV tổng hợp trạng thái Báo cáo Zalo + Nhận xét LMS theo từng tuần.
- **File dự kiến tác động**:
  - `repositories/report_repository.py`, `api/zalo_routes.py`
  - `templates/partials/dashboard.html`, `static/js/state.js`, `static/js/jobs.js`
  - `tests/test_api_routes_unit.py`, `tests/check_template_js.js`
- **Checklist triển khai**:
  - [x] **3.2.1.** Tạo API `GET /api/reports/weekly-summary?week=..&year=..&job_id=..&format=csv|json` (`api/zalo_routes.py`, `repositories/report_repository.py`) tổng hợp kết quả quét Zalo (hợp nhất selective rescan liên tiếp trong cùng `snapshot_id`, áp dụng bản ghi duyệt tay `BCNB_MANUAL_APPROVE_TICK` `production` + `VERIFIED` xuyên suốt các lần quét trong tuần, gắn `snapshot_id` từng dòng và xóa `snapshot_id` tổng hợp nếu ghép dòng khác snapshot, phân tách rõ `zalo_late` với `zalo_exempt`) và trạng thái LMS (phân tách `dry_run_count` / `lms_dry_run` khỏi `ticked_count` / `lms_completed`) của tuần được chọn từ cơ sở dữ liệu (giữ nguyên danh sách tuần lịch sử kể cả khi tổng số job vượt quá `200`).
  - [x] **3.2.2.** Bổ sung nút **"📥 Xuất CSV Tuần"** (`#btn-export-weekly-csv`) trên thanh công cụ bảng Kết quả Zalo (`templates/partials/dashboard.html`) với bảng mã UTF-8 có BOM (`\uFEFF`) để mở trực tiếp bằng Microsoft Excel không bị lỗi font tiếng Việt, kèm khóa chụp bản sao dữ liệu (`snapshotFallbackRows`) trước `await fetch` chống xung đột khi chuyển tuần trong lúc tải.
  - [x] **3.2.3.** Thêm bộ chọn xem lại các tuần lịch sử (`#history-week-select`), các job quét theo tuần (`#history-job-select`), nút nạp (`#btn-load-weekly-history`) và nhãn trạng thái chế độ xem lịch sử (`#weekly-history-status-badge`) kèm bộ đếm tuần tự `weeklyHistoryRequestSeq` chống phản hồi mạng đến lệch thứ tự và giữ nguyên nguồn `snapshot_id` khi quay về tuần hiện tại.
  - [x] **3.2.4.** Đảm bảo dữ liệu xuất CSV chỉ chứa 7 cột quản lý lớp học (`Mã lớp`, `Giáo viên`, `Buổi học`, `Ngày học`, `Trạng thái Zalo`, `Trạng thái LMS`, `Thời gian kiểm chứng`) và khử hoàn toàn nguy cơ CSV Formula Injection (`=`, `+`, `-`, `@`, `\t`, `\r`) qua `sanitize_csv_cell` / `sanitizeCsvCell`.
  - [x] **3.2.5.** Viết unit test và kiểm thử hợp đồng giao diện (`tests/test_api_routes_unit.py`, `tests/check_template_js.js`), cộng thêm đợt vá 4 lỗi hậu kiểm của Owner (`services/lms_service.py`, `repositories/report_repository.py`, `static/js/jobs.js`), vượt qua `.\CHECK_QUALITY.ps1 -Isolated` (exit `0`, coverage `75.9%`) và đạt **PASS** từ `gpt-6.1-sol (xhigh)` (Session `01a169e2-61a2-7422-ae5c-4179b0e91166`).

---

## 🛡️ PHASE 4: KIỂM ĐỊNH CHẤT LƯỢNG NỘI DUNG LMS/ZALO, TỰ ĐỘNG GHI `MÔ TẢ VẤN ĐỀ` LÊN TEACHING & BẢNG EMAIL QUẢN LÝ (WAVE 4 — 2026-10-29)

### Chặng 4.0 — Sao Lưu Toàn Diện Pre-Wave 4 (SQLite + Restore Drill + Source ZIP) ✅
- **Checklist triển khai**:
  - [x] **4.0.1.** Sao lưu nóng SQLite (`database.db`) sang `.backups/db/pre-wave4-20261029.sqlite3` (`40,898,560` bytes, SHA-256: `5525C5A7F7053C2633A9DC44CE81D020C45D0244C7DD700E1164813470112773`) và chạy `restore_drill` xác minh `integrity="ok"`, `foreign_key_errors=()`, `smoke={"scan_jobs": 397, "external_action_audit": 1735, "schema_migrations": 4}`.
  - [x] **4.0.2.** Đóng gói snapshot mã nguồn & cấu hình vào `.backups/pre-wave4-20261029/source-code-snapshot-20261029.zip` (`377` files, SHA-256: `EB7F3B8E89FB0FB5A6178B802EB102C8612AA33174FBB9686C904E277F4650EA`) kèm `MANIFEST.json` và `ROLLBACK_INSTRUCTIONS.md`.
  - [x] **4.0.3.** Khởi tạo kế hoạch chi tiết Wave 4 (`4.0` → `4.4`) trong `UPGRADE_PROGRESS.md` và artifact `implementation_plan.md`.

---

### Chặng 4.1 — Đọc Mở Rộng Bảng Teaching (`check_nhanxet.php`) & Đồng Bộ Trạng Thái Nộp LMS/Zalo (Yêu cầu Thứ 2) ✅
- **Mục tiêu**: Thu thập đầy đủ các cột mở rộng từ bảng `check_nhanxet.php` trên Teaching (`session_id`, `khoi`, `nx_zalo_checked`, `nx_lms_checked`, `lms_deadline`, `qc_issue_note`, `instructor_note`, `evaluation_status`) để ngay cả khi chưa chạy job LMS riêng, báo cáo tuần và Email Quản lý vẫn hiển thị chính xác **`LMS: ✅ Đã nộp LMS`** và **`Tình trạng đánh giá: Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo`** khớp với trang Teaching.
- **File dự kiến tác động**:
  - `services/bcnb_safety.py`, `services/zalo_discovery_service.py`, `services/zalo_discovery_plan.py`, `services/zalo_bcnb_service.py`
  - `repositories/report_repository.py`
  - `tests/test_bcnb_columns.py`, `tests/test_api_routes_unit.py`
- **Checklist triển khai**:
  - [x] **4.1.1.** Bổ sung `resolve_extended_bcnb_columns(page)` trong `services/bcnb_safety.py` để xác định vị trí các cột `Khối`, `NX Zalo`, `Deadline LMS`, `NX LMS`, `Mô tả vấn đề (TE/TF/QC)`, `Ghi chú Giảng viên`, `Tình trạng đánh giá` theo header ngữ nghĩa.
  - [x] **4.1.2.** Mở rộng `_read_bcnb_week_rows` (`services/zalo_discovery_service.py`) và `_read_bcnb_schedule_rows` (`services/zalo_bcnb_service.py`) thu thập `bcnb_session_id` (`tr.dataset.sessionId`), `khoi`, `nx_zalo_checked`, `nx_lms_checked`, `lms_deadline`, `qc_issue_note`, `instructor_note`, `evaluation_status`.
  - [x] **4.1.3.** Gắn metadata trạng thái BCNB (`nx_lms_checked`, `evaluation_status`, `qc_issue_note`, `instructor_note`, `bcnb_session_id`, `khoi`) vào từng dòng kết quả quét Zalo (`evidence.date_context` / `result`).
  - [x] **4.1.4.** Cập nhật `ReportRepository.get_weekly_summary` (`repositories/report_repository.py`) tổng hợp trạng thái `lms_status` kết hợp dữ liệu `nx_lms_checked` từ Teaching (`✅ Đã nộp LMS` / `❌ Chưa nộp LMS`) cùng `evaluation_status` và `issue_description`.
  - [x] **4.1.5.** Chạy unit tests (`44/44 OK`) + `node tests/check_template_js.js` + `.\CHECK_QUALITY.ps1 -Isolated` (exit `0`) và đạt `VERDICT: PASS` từ `gpt-6.1-sol (xhigh)` (Session `01a185d0-5aeb-7a92-815f-5d889e616c20`).

---

### Chặng 4.2 — Kiểm Định Chất Lượng Nội Dung LMS (`slot.summary` + Nhận Xét HV Buổi Thường vs Checkpoint) & Kiểm Tra Lỗi Nội Dung Zalo (Yêu cầu Thứ 3 & Thứ 4) ✅
- **Mục tiêu**: Tự động kiểm tra chất lượng nội dung bài học và nhận xét từng học viên trên MindX LMS (phân biệt chuẩn **Buổi Thường 3 ý** và **Buổi Checkpoint Coding B5/B9, Robotics B4/B8**), kết hợp phát hiện lỗi giáo viên ghi sai ngày / sai số buổi / sai tên bài trên Zalo.
- **File dự kiến tác động**:
  - `services/lms_quality_service.py` (mới)
  - `services/external_gateways.py`, `services/zalo_scan_evidence.py`, `services/zalo_bcnb_service.py`, `services/zalo_parallel_service.py`, `services/zalo_service.py`, `repositories/zalo_batch_repository.py`, `repositories/report_repository.py`
  - `tests/test_lms_quality_service.py` (mới)
- **Checklist triển khai**:
  - [x] **4.2.1.** Xây dựng bộ kiểm tra **Nội dung bài học LMS (`evaluate_lms_slot_summary`)**: phát hiện rỗng hoặc chỉ ghi tiêu đề cụt lủn (`"B1"`, `"Bài 1"`, `"Buổi 1"`, `"Kiểm tra checkpoint 1"`, mô tả thực chất `< 15` ký tự) $\rightarrow$ xuất vấn đề `[LMS] Chưa ghi nội dung bài học`.
  - [x] **4.2.2.** Xây dựng bộ nhận diện **Buổi Checkpoint vs Buổi Thường (`is_checkpoint_session`)**: lớp Lập trình (`C4K`, `JS`, `CS`, `PTB`, `PTI`, `GA`, `GI`, `SA`, `SI`...) tại **Buổi 5, 9**; lớp Robotics (`ROB`) tại **Buổi 4, 8**; hoặc comment có cấu trúc Rubric (`Điểm năng lực` + `KIẾN THỨC`/`KỸ NĂNG`/`THÁI ĐỘ`).
  - [x] **4.2.3.** Xây dựng bộ chấm **Chất lượng nhận xét từng học viên đi học (`evaluate_lms_student_comments`)**:
    - Chỉ xét học viên có mặt (`ATTENDED`, `LATE_ARRIVED`); tuyệt đối không lưu tên thật học viên vào thông báo lỗi (bảo vệ PII).
    - Với **Buổi Checkpoint**: kiểm tra đủ cả 3 trụ cột năng lực (`KIẾN THỨC`, `KỸ NĂNG`, `THÁI ĐỘ`) cho học viên đi học.
    - Với **Buổi Thường** (trừ buổi 14): kiểm tra đủ **3 ý** (`Điểm mạnh`, `Điểm cần cải thiện`, `Lời khuyên` — hoặc đủ 3 ý nhận xét chi tiết), bắt lỗi viết vài dòng sơ sài (như `VT-C4K-PTB16` B1, `VT-C4K-PTI11` B8) và cảnh báo copy-paste trùng lặp (`>= 2` học viên giống nhau).
  - [x] **4.2.4.** Mở rộng kiểm tra **Zalo sai ngày / sai buổi / sai tên bài (`evaluate_zalo_content_issues`)**: tổng hợp lỗi ghi sai ngày/số buổi từ `resolve_report_target` và đối chiếu tên bài học trong báo cáo Zalo với tiêu đề bài học của buổi đó trên LMS (`slot.summary`).
  - [x] **4.2.5.** Tích hợp hàm trích xuất GraphQL LMS theo tuần/lớp (`fetch_lms_class_slots_via_page` qua `LmsQualityGateway` trên `ExternalGateways`) có giới hạn tài nguyên, bắt lỗi trùng mã lớp/thiếu dữ liệu/vượt giới hạn, lưu kết quả vào `zalo_batch_targets` và cô lập hoàn toàn trong `-Isolated`.
  - [x] **4.2.6.** Chạy unit tests (`tests/test_lms_quality_service.py`: `12/12 OK`) + `node tests/check_template_js.js` + `.\CHECK_QUALITY.ps1 -Isolated` (exit `0`) và đạt `VERDICT: PASS` từ `gpt-6.1-sol (xhigh)` (Session `01a18614-ef6e-7a50-90f8-2ee4afc5d03f`).

---

### Chặng 4.3 — Tự Động Ghi Nhận Vấn Đề Vào Ô `Mô tả vấn đề (TE/TF/QC)` Trên Trang Teaching (`check_nhanxet.php`) ✅
- **Mục tiêu**: Khi chạy ở chế độ `production`, bot tự động ghi các vấn đề phát hiện được (Zalo & LMS) vào cột **`Mô tả vấn đề (TE/TF/QC)`** (`note_type="checker"`) của đúng dòng buổi học trên trang `check_nhanxet.php`, bảo toàn ghi chú tay của QC và lưu vết kiểm toán `BCNB_UPDATE_CHECKER_NOTE`.
- **File dự kiến tác động**:
  - `services/bcnb_safety.py`, `services/bcnb_actions.py`, `services/zalo_bcnb_service.py`, `services/zalo_parallel_service.py`, `services/lms_quality_service.py`, `repositories/report_repository.py`
  - `tests/test_bcnb_actions.py`, `tests/test_bcnb_columns.py`, `tests/test_lms_quality_service.py`
- **Checklist triển khai**:
  - [x] **4.3.1.** Xây dựng hàm ghép ghi chú an toàn `merge_checker_issue_note(existing_note, auto_issues)` để bảo toàn mọi ghi chú thủ công của TE/TF/QC đã có trước đó và chỉ cập nhật phần `[Auto QC: ...]` (idempotent 100%).
  - [x] **4.3.2.** Xây dựng `execute_verified_checker_note_action` trong `services/bcnb_actions.py`: xác minh `session_id` và `csrfToken` trên dòng mục tiêu, gửi `POST /check_nhanxet.php` (`action=update_note`, `note_type=checker`), kiểm tra phản hồi JSON `success` và đọc ngược DOM (`data-current-note` / text) để xác nhận `VERIFIED`.
  - [x] **4.3.3.** Tích hợp tự động ghi `Mô tả vấn đề (TE/TF/QC)` vào luồng `update_bcnb_reports` (`services/zalo_bcnb_service.py`) khi `execution_mode == "production"` (ở chế độ `dry-run` chỉ ghi nhận `DRY_RUN` để xem trước).
  - [x] **4.3.4.** Lưu vết đầy đủ vào `external_action_audit` với `action_type="BCNB_UPDATE_CHECKER_NOTE"` kèm `before_state`, `after_state`, `outcome`.
  - [x] **4.3.5.** Chạy unit tests (`80/80 OK`) + `node tests/check_template_js.js` + `.\CHECK_QUALITY.ps1 -Isolated` (`616/616 OK`) và đạt `VERDICT: PASS` từ `gpt-6.1-sol (xhigh)` (Session `01a18672-2bc8-7430-ae31-bf8a2ec24a7a`).

---

### Chặng 4.4 — Nâng Cấp Bảng Email Quản Lý (HTML Table + Clipboard Rich Table + Unicode Table), CSV Lỗi Chất Lượng & Đồng Bộ Tài Liệu (Yêu cầu Thứ 1 & Nâng Cao) ✅
- **Mục tiêu**: Hiển thị đầy đủ cột `Trạng thái LMS`, `Tình trạng đánh giá` và `Mô tả vấn đề (Zalo & LMS)` trên Dashboard, CSV và Email gửi Quản lý; hỗ trợ kẻ bảng đẹp mắt cho cả chế độ SMTP và chế độ Gmail Web Compose; cập nhật toàn bộ tài liệu dự án (`CHANGELOG.md`, `PROJECT_STATE.md`, `PROJECT_DECISIONS.md`, `UPGRADE_PROGRESS.md`, `VERSION.md`, `PROJECT_SUMMARY.md`).
- **File dự kiến tác động**:
  - `repositories/report_repository.py`, `services/report_email_service.py`, `api/zalo_routes.py`
  - `templates/partials/dashboard.html`, `static/js/jobs.js`, `static/js/state.js`
  - `tests/test_api_routes_unit.py`, `tests/test_lms_quality_service.py`, `tests/check_template_js.js`
  - `CHANGELOG.md`, `PROJECT_STATE.md`, `PROJECT_DECISIONS.md`, `UPGRADE_PROGRESS.md`, `VERSION.md`, `PROJECT_SUMMARY.md`
- **Checklist triển khai**:
  - [x] **4.4.1.** Mở rộng `is_failed_weekly_summary_row` và `build_weekly_summary_csv` (`repositories/report_repository.py` & `static/js/jobs.js`) để bao gồm cả các lớp có vấn đề chất lượng nội dung (`issue_description` khác rỗng) và xuất đủ 9 cột (`Tình trạng đánh giá`, `Mô tả vấn đề (TE/TF/QC)`).
  - [x] **4.4.2.** Nâng cấp `services/report_email_service.py` và `static/js/jobs.js`:
    - Bảng HTML (`html_body` / `html_table`) có đầy đủ 9 cột `STT`, `Mã lớp`, `Buổi`, `Ngày học`, `Giảng viên`, `NX Zalo`, `NX LMS`, `Tình trạng đánh giá`, `Mô tả vấn đề (TE/TF/QC)`.
    - Chế độ fallback mở Gmail Web Compose: tự động copy bảng Rich HTML (`text/html`) vào Clipboard để dán 1 chạm (`Ctrl+V`), kẻ khung bảng Unicode căn cột theo độ rộng hiển thị (`_display_width`) cho phần plain-text, và cảnh báo kèm hộp thoại sao chép đầy đủ (`window.prompt`) nếu trình duyệt chặn quyền Clipboard.
  - [x] **4.4.3.** Hiển thị trực quan nhãn `LMS`, `Đánh giá`, `Vấn đề` và `Giải trình GV` ngay trên bảng kết quả của trang `Dashboard` (`templates/partials/dashboard.html`, `static/js/jobs.js`).
  - [x] **4.4.4.** Viết/cập nhật unit tests (`tests/test_api_routes_unit.py`, `tests/test_lms_quality_service.py`, `tests/check_template_js.js`), vượt qua `.\CHECK_QUALITY.ps1 -Isolated` (`617/617 OK`, coverage `76.8%`) và đạt `VERDICT: PASS` từ `gpt-6.1-sol (xhigh)` (Session `01a186d6-070e-77a1-81f1-d48f1cf46330`).
  - [x] **4.4.5.** Cập nhật đầy đủ `CHANGELOG.md`, `PROJECT_STATE.md`, `PROJECT_DECISIONS.md` (`DEC-038`), `UPGRADE_PROGRESS.md`, `VERSION.md`, `PROJECT_SUMMARY.md` và chạy lại gate kiểm định cuối cùng.
  - [x] **4.4.6.** Review tổng thể lần cuối với `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a186d6-070e-77a1-81f1-d48f1cf46330`).

---

## 🧰 PHASE 5: HARDENING FLOW KIỂM TRA BÁO CÁO (WAVE 5 — LẬP KẾ HOẠCH 2026-10-30)

> **Nguồn**: Audit chỉ đọc 2026-10-30 trên HEAD `b395b94` (4 auditor song song, Lead xác minh lại các phát hiện HIGH). Báo cáo đầy đủ nằm ngoài repo, ở artifact `report_flow_audit.md` của phiên Lead.
> **Mục tiêu Wave 5**: (1) loại bỏ các lỗi có thể ghi sai BCNB hoặc dừng sai batch; (2) mỗi luật nghiệp vụ chỉ có **một nguồn sự thật**; (3) không để dòng nào rơi khỏi danh sách mà không báo; (4) UI hiển thị theo dữ liệu server, không ghép tạm trong bộ nhớ.
> **Ngoài phạm vi**: chạy thật trên BCNB/Zalo/LMS, commit/push, đổi `.env`/`database.db`. Mọi việc này cần Owner yêu cầu riêng.
> **Review kế hoạch**: Sol 6.1 `high` (read-only) qua 4 vòng: R1–R3 `NEEDS_FIX`, đã sửa hết; R4 **`VERDICT: PASS`** (session `01a18bde-94ca-7cf3-a157-769051ab7fc7`, 2026-10-30). Sau khi Owner chốt D1–D6: R5 `NEEDS_FIX` (đã sửa) → R6 **`VERDICT: PASS`** (session `01a18bf2-dd8a-79f0-aa45-3d68b761fcd6`).

### 5.A Quy tắc thực thi cho AI (bắt buộc đọc trước mỗi chặng)

1. **Thứ tự**: 5.0 → (5.1, 5.2, 5.3, 5.4: được đổi thứ tự cho nhau) → 5.5 → 5.6 → 5.7 → 5.8. **5.5 phải xong trước 5.7**, vì UI dùng trường `category`/`is_failed` do backend sinh ra.
2. **Mỗi lần chỉ làm một chặng**. Trước khi sửa: chụp manifest baseline (`git status --porcelain` + SHA-256 các path đang dirty) và lưu **ngoài repo**. Chỉ sửa các file trong allowlist của chặng; muốn thêm file phải ghi lý do vào dòng checklist. `PROJECT_STATE.md` và `UPGRADE_PROGRESS.md` **luôn được phép** sửa ở mọi chặng (dùng cho G6).
3. **Tích `[x]`** chỉ khi đã có bằng chứng; ghi kèm ngay dòng đó tên test, số test và session review. Hạng mục bị Owner từ chối ghi `[-]` kèm lý do. Các hạng mục gắn **(Dn đã chốt)** phải làm đúng cột "Cách hiện thực" ở mục 5.B; không tự mở rộng phạm vi quyết định.
4. **An toàn**:
   - Không thao tác live trên browser/BCNB/Zalo/LMS; test chỉ dùng fixture tổng hợp.
   - Config mới phải có giá trị mặc định an toàn và được khai báo cô lập trong `CHECK_QUALITY.ps1`.
   - Giữ nguyên các bất biến của DEC-030 → DEC-038: preflight fail-closed, `exclusive_browser_job`, readback checkbox, `selection_digest`, ngân sách số dòng (`zalo_discovery_service.py ≤ 400`, `zalo_service.py ≤ 520`).
5. **Cổng hoàn tất chặng (G1–G6)**: dòng checklist cuối mỗi chặng chỉ được tích khi đạt đủ:
   - **G1**: test mục tiêu của chặng chạy `venv\Scripts\python.exe -m unittest <modules>` đều OK.
   - **G2**: `node tests/check_template_js.js` pass.
   - **G3**: `.\CHECK_QUALITY.ps1 -Isolated` exit `0`; ghi số test và coverage.
   - **G4**: `git diff --check` exit `0`; so manifest với baseline, chỉ path trong allowlist được thay đổi.
   - **G5 — Review độc lập Sol 6.1 `high`** (theo yêu cầu Owner 2026-10-30; Wave 5 dùng mức `high` thay cho `xhigh` của DEC-036). PowerShell 5.1 mặc định không dùng UTF-8, nên phải đặt UTF-8 trước khi chạy và kiểm tra exit code:
     ```powershell
     $utf8 = [Text.UTF8Encoding]::new($false)
     [Console]::InputEncoding = $utf8; [Console]::OutputEncoding = $utf8; $OutputEncoding = $utf8
     $log = "$env:TEMP\wave5_review_5x.log"
     Get-Content "$env:TEMP\wave5_review_5x.txt" -Raw -Encoding UTF8 |
       codex exec -m gpt-6.1-sol -c model_reasoning_effort="high" -s read-only - 2>&1 |
       Tee-Object -FilePath $log
     if ($LASTEXITCODE -ne 0) { throw "codex exit $LASTEXITCODE" }
     Select-String -Path $log -Pattern '^VERDICT: (PASS|NEEDS_FIX)\s*$' | Select-Object -Last 1
     ```
     Prompt (lưu ngoài repo) gồm: mã chặng, allowlist, mục tiêu từng hạng mục, các bất biến cần giữ, **kết quả test G1–G3 do Lead chạy** (Sol chạy read-only nên có thể không tự chạy được Python/Node), và yêu cầu trả `VERDICT: PASS` hoặc `VERDICT: NEEDS_FIX`. Chỉ tính là đạt khi `exit 0` **và** dòng verdict cuối là `PASS`. Nếu `NEEDS_FIX`: sửa → chạy lại G1–G4 → review lại cho tới khi `PASS`. Ghi session id.
   - **G6**: thêm mục ngắn vào `PROJECT_STATE.md` (trạng thái, lệnh, kết quả, rủi ro mở); cập nhật bộ đếm ở bảng Master; **không commit/push** nếu Owner chưa yêu cầu; khởi động lại server local nếu đang chạy.

### 5.B Quyết định Owner (đã chốt 2026-10-30)

| Mã | Câu hỏi | Ảnh hưởng | Chốt của Owner (2026-10-30) | Cách hiện thực trong plan |
| :--- | :--- | :--- | :--- | :--- |
| D1 | `🟡 Đã nộp trễ` tính là lỗi hay đã xong? | 5.5.1, 5.5.3, 5.5.4, 5.7.7 | ✅ **Nộp trễ không tính là lỗi.** | `category = LATE`, `is_failed = false`. Vẫn tích NX Zalo như hiện tại. Không vào danh sách lỗi của email, không vào Copy nhắc GV hay Quét lại lớp lỗi. KPI tách ô "Trễ" riêng, không tô đỏ chung với "Chưa nộp". |
| D2 | Báo cáo gửi đúng ngày học nhưng không ghi buổi/ngày (hoặc ghi sai ngày) có khớp không? | 5.5.1, 5.8.4 | ✅ **Khớp, không phải lỗi**, nhưng ghi chú vào ô QC rằng báo cáo không ghi ngày / ghi sai ngày. | Đánh là `SUBMITTED_*` và sinh issue mức **NOTE**: `[Zalo] Báo cáo không ghi ngày/buổi` hoặc `[Zalo] Báo cáo ghi sai ngày (dd/mm)`. Issue này ghi lên ô `Mô tả vấn đề` nhưng không làm dòng bị tính lỗi. |
| D3 | Thứ tự lên cấp của JS / CS / Robotics? | 5.8.1 | ✅ `JSB → JSA → JSI`; `CSB → CSA → CSI`; Robotics: `PREB → PREA → PREI`, `ARMB → ARMA → ARMI`, `SEMIB → SEMIA → SEMII`; Robotics nhỏ: `ROB4B → ROB4A → ROB4I`. | Mỗi dòng là một track riêng, lên cấp tự động B → A → I **trong cùng track**. Owner chưa xác nhận việc chuyển giữa các track (ví dụ `PREI → ARMB`), nên phần này **không tự động** và vẫn để review. |
| D4 | "[LMS] … cần kiểm tra thủ công" có tính là lỗi không? | 5.5.1, 5.8.5 | ✅ **Không tính lỗi**, chỉ ghi chú QC trên Teaching. | Issue mức **NOTE**, được ghi lên ô `Mô tả vấn đề`, không làm dòng bị tính lỗi trong email. Chốt an toàn: khi LMS **sập toàn hệ thống** (mọi lớp `fetch_error`) thì **không** ghi note hàng loạt, chỉ hiện cảnh báo chung trên UI/summary. |
| D5 | AI assist có được gửi nguyên văn tin nhắn lên Gemini? | 5.8.3 | ✅ **Có.** | Không che tên. Cờ `ZALO_AI_ASSIST_CLASSIFIER` vẫn mặc định `false`. Nguyên văn tin nhắn không được ghi vào log hay DB ngoài các chỗ đang có. Ghi quyết định vào DEC-039. |
| D6 | Có xoá luồng quét tuần tự cũ và phần CSV chết phía JS không? | 5.8.2, 5.7.7 | ✅ **Có.** | Xoá thân `zalo_service.py:176-503` (giữ entrypoint), chuyển invariant sang luồng song song; xoá phần CSV builder chết trong `jobs.js`. |

> **Khái niệm mới do D2/D4 — mức độ issue**: mỗi issue QC có `severity ∈ {FAIL, NOTE}`. **NOTE** chỉ ghi chú lên Teaching và **không** làm `is_failed = true`; **FAIL** giữ hành vi Wave 4 (dòng vào bảng lỗi của email). Mặc định mọi issue hiện có là **FAIL**, trừ các nhóm Owner đã chốt là NOTE: (a) D2 không ghi ngày/buổi hoặc ghi sai ngày; (b) D4 "[LMS] … cần kiểm tra thủ công". Muốn thêm nhóm NOTE khác phải hỏi Owner.

---

### Chặng 5.0 — Baseline & Sao Lưu Trước Wave 5 ✅
- **Allowlist**: chỉ `UPGRADE_PROGRESS.md`, `PROJECT_STATE.md`. Backup nằm ngoài Git (`.backups/`).
- **Checklist**:
  - [x] **5.0.1.** `git status` sạch (ngoài `PROJECT_STATE.md` và `UPGRADE_PROGRESS.md`). Đã ghi HEAD `b395b94ef92b70687239eec5542548274ffe8d01`, lưu manifest baseline ngoài repo (`$env:TEMP\wave5_5.0_baseline_manifest.json`).
  - [x] **5.0.2.** Backup nóng `database.db` bằng `scripts/backup_restore.py` sang `.backups/db/pre-wave5-20261030-20261030-095820.sqlite3` (`1,134,592` bytes, SHA-256: `B9870B52A6F4916B5305607959BA81B47889F695867C571762F55BDC6C8E53F9`) và chạy restore drill `.backups/restore-drill.sqlite3` (`integrity="ok"`, `foreign_key_errors=[]`, `smoke={"scan_jobs": 11, "external_action_audit": 70, "schema_migrations": 4}`).
  - [x] **5.0.3.** Chạy baseline `node tests/check_template_js.js` (`scripts=7 handlers=34 behavior=12`) và `.\CHECK_QUALITY.ps1 -Isolated` (`QUALITY GATE PASSED`, `620/620` tests OK, coverage `76.6%`).
  - [x] **5.0.4.** Xác nhận D1–D6 đã chốt (bảng 5.B, 2026-10-30) và các giới hạn an toàn kèm theo: D2 không áp dụng khi nội dung ghi rõ số buổi khác; D3 không tự nối giữa các track; D4 không ghi note hàng loạt khi LMS sập toàn hệ thống. Nếu Owner đổi ý thì cập nhật bảng 5.B **trước khi** làm hạng mục bị ảnh hưởng.

---

### Chặng 5.1 — Độ Bền Vòng Quét Zalo ✅
- **Mục tiêu**: lỗi xảy ra ở một target không được làm huỷ cả batch hay để dòng kẹt `RUNNING`; target bị treo phải có giới hạn thời gian.
- **Allowlist**: `services/zalo_parallel_service.py`, `services/zalo_scan_evidence.py`, `services/zalo_evidence.py`, `googleform_autosubmit/config.py`, `.env.example`, `CHECK_QUALITY.ps1`, `tests/test_zalo_parallel_service.py`, `tests/test_zalo_scan_evidence.py`.
- **Checklist**:
  - [x] **5.1.1.** Bọc lời gọi `retry_target_once` (`zalo_parallel_service.py` ~L2248-2289):
    - `JobCancelledError` → `complete_scan(JOB_CANCELLED)` + `remember_result` rồi raise lại;
    - `Exception` khác → `SCAN_FAILED` + `policy.record(abnormal)` + `continue`, giống lần thử 1.
    - **Test**: lần thử 2 ném `RuntimeError` thì dòng thành `FAILED/SCAN_FAILED` và batch quét tiếp target sau (`test_retry_target_once_second_attempt_runtime_error_marks_scan_failed_and_scans_next`); lần thử 2 ném `JobCancelledError` thì không còn dòng nào ở `RUNNING` (`test_retry_target_once_second_attempt_job_cancelled_leaves_no_target_running`).
  - [x] **5.1.2.** Thêm timeout cho từng target: `asyncio.wait_for` quanh `_scan_one_target`/`scan_open_zalo_chat`, config `ZALO_TARGET_TIMEOUT_SECONDS` (mặc định 180, cô lập trong gate). Hết giờ → `SCAN_FAILED` với `error_code=TARGET_TIMEOUT`, trang được đóng hoặc làm mới an toàn (nằm trong khối `try:` bắt `JobCancelledError`). **Test**: fake page bị treo (`test_per_target_timeout_marks_target_timeout_refreshes_page_and_scans_next`, `test_per_target_timeout_cancelled_during_fresh_page_setup_leaves_no_target_running`).
  - [x] **5.1.3.** Vòng quét khoảng trống lịch sử (`zalo_scan_evidence.py` ~L1426-1431) kiểm tra lệnh huỷ định kỳ. Batch có trần `capture_max_messages` (mặc định an toàn `DEFAULT_BATCH_MAX_MESSAGES=500`); chạm trần thì trả `INSUFFICIENT_EVIDENCE` có lý do (`MESSAGE_CAP_REACHED`), **không** trả `NOT_FOUND_VERIFIED`. **Test**: `test_history_gap_loop_checks_cancellation_and_message_cap_returns_insufficient`, `test_scan_one_target_exceeding_message_cap_returns_insufficient_evidence`.
  - [x] **5.1.4.** Chụp lại ảnh báo cáo 1 lần (dùng thật `MAX_CAPTURE_ATTEMPTS`, `zalo_evidence.py` ~L203-207). Vẫn lỗi thì trả `INSUFFICIENT` và ghi lý do vào `capture_diagnostics`. **Test**: lần 1 lỗi, lần 2 ok → `SUBMITTED_*` (`test_report_screenshot_retries_once_and_succeeds_on_second_attempt`, `test_report_found_screenshot_error_persists_typed_capture_diagnostic`).
  - [x] **5.1.5.** Cổng G1–G6 (test: `tests.test_zalo_parallel_service`, `tests.test_zalo_scan_evidence`: `91/91 OK`; `node tests/check_template_js.js`: pass; `.\CHECK_QUALITY.ps1 -Isolated`: `627/627 OK`, coverage `76.7%`) + Sol 6.1 `high` `VERDICT: PASS` (session `01a18c63-a713-7023-923a-8f87291b776b`).

---

### Chặng 5.2 — Cô Lập Ghi BCNB: Tick vs Ghi Chú `Mô tả vấn đề` ✅
- **Mục tiêu**: lỗi ghi chú không được chặn việc tích NX Zalo; mọi lần tích phải đi qua ledger; không ghi đè chỉnh sửa tay của QC.
- **Allowlist**: `services/zalo_bcnb_service.py`, `services/zalo_parallel_service.py`, `services/bcnb_actions.py`, `services/bcnb_safety.py`, `repositories/audit_repository.py`, `tests/test_bcnb_actions.py`, `tests/test_zalo_parallel_service.py`, `tests/test_bcnb_columns.py`, `PROJECT_DECISIONS.md` (chỉ để ghi rủi ro còn lại của 5.2.3).
- **Checklist**:
  - [x] **5.2.1.** Ghi chú (`BCNB_UPDATE_CHECKER_NOTE`) không được làm dừng tick. Phải chặn ở **cả hai** đường:
    - outcome ghi chú qua `on_target_outcome` (`zalo_bcnb_service.py` ~L669, ~L748) không đi vào `policy.record`;
    - nhánh `except Exception as note_sync_err` của đường notes-only (`zalo_parallel_service.py` ~L2794) cũng không gọi `policy.record(abnormal=True)`.
    - Thay vào đó đếm riêng `checker_note_failures` trong summary của job và ghi log event (`bcnb.notes_only_sync_failed`).
    - **Test**: (a) cột ghi chú không đọc được + 10 dòng verified → cả 10 dòng vẫn được tích, không `BLOCKED_BATCH_HALT` (`test_5_2_1_unreadable_checker_note_with_ten_verified_rows_ticks_all_ten_without_halt`); (b) đường notes-only ném exception → `policy.halted` vẫn chưa bật (`test_5_2_1_and_5_2_2_notes_only_unclaimed_sync_exception_never_halts_policy`).
  - [x] **5.2.2.** Thêm tham số `notes_only: bool = False` cho `update_bcnb_reports`. Nhánh unclaimed (`zalo_parallel_service.py` ~L2768) truyền `notes_only=True` để bỏ qua toàn bộ khối tick. **Test**: ở `notes_only`, `execute_verified_checkbox_action` không được gọi (`test_5_2_2_notes_only_skips_checkbox_actions_and_column_resolution`).
  - [x] **5.2.3.** Chống ghi đè chỉnh sửa của QC. Lưu ý: POST hiện tại (`bcnb_actions.py` ~L340) không gửi giá trị cũ hay version, nên đọc trước khi ghi **chỉ là bảo vệ lạc quan, không phải compare-and-swap nguyên tử**.
    - (a) Kiểm tra (chỉ qua source/fixture, không thử live) xem endpoint BCNB có hỗ trợ ghi có điều kiện không. Đã ghi rủi ro còn lại vào `DEC-039` (`PROJECT_DECISIONS.md`).
    - (b) Ngay trước POST: đọc lại ô theo `session_id` (`read_checker_note_by_session` + `preserveDomNote` trong `_UPDATE_CHECKER_NOTE_JS`). Nếu khác `existing_note` → `FAILED_BEFORE_WRITE` ("QC vừa chỉnh sửa"), không ghi.
    - (c) Sau POST: readback (`verify_checker_note_persisted`). Nếu giá trị trên server khác `merged_note` thì gắn `CONFLICT_AFTER_WRITE` / `OUTCOME_UNKNOWN` (`BcnbConflictAfterWriteError`) để QC xem lại; không tự ghi lại.
    - **Test**: ô bị đổi trước POST → không POST; ô bị đổi giữa POST và readback → báo xung đột (`test_5_2_3_optimistic_pre_read_blocks_post_and_post_readback_mismatch_flags_conflict`, `test_read_checker_note_by_session_and_verify_checker_note_persisted_dom_paths`).
  - [x] **5.2.4.** Phân biệt đoạn auto và đoạn ghi tay. Định dạng Wave 4 không có marker riêng (`bcnb_safety.py` ~L922), nên chuỗi giống nhau không cho biết ai viết. Quy tắc bảo thủ:
    - Một đoạn chỉ được coi là **auto** khi nó khớp chính xác (sau chuẩn hoá, giữ nguyên tag `[LMS]`/`[Zalo]` trên chuỗi nguyên bản trước `normalize_auto_issue_text`) một issue mà hệ thống **đã tự sinh và đã ghi thành công** cho đúng dòng đó. Không dùng `merged_note` làm bằng chứng, vì nó chứa cả phần ghi tay được giữ lại và được lưu **trước** khi thực thi (`zalo_bcnb_service.py` ~L709-717). Dry-run cũng ghi intent rồi thoát mà không POST (`bcnb_actions.py` ~L517). Bằng chứng hợp lệ phải đến từ một audit thoả **đủ cả 5 điều kiện**:
      1. `action_type = BCNB_UPDATE_CHECKER_NOTE` và cùng định danh dòng (lớp + buổi + tuần/năm + `session_id`/ngày);
      2. `execution_mode = production`;
      3. outcome = `VERIFIED` **có POST thật** (`post_verified is True`, `not no_change`, `before_state` có `qc_issue_note` kiểu `str` khác `after_state`, không phải nhánh "không đổi" hay nhánh recover `INTENT_RECORDED`);
      4. đoạn đó nằm trong danh sách **`reason.auto_issues`** (danh sách do hệ thống sinh), không phải chỉ xuất hiện trong `merged_note`;
      5. đoạn đó có mặt trong **`after_state` đã readback** và không phải đoạn đã có sẵn từ `before_state` của lần ghi đó (`key in after_keys and key not in before_keys`).
    - Các audit `DRY_RUN`, `FAILED`, `OUTCOME_UNKNOWN`, intent còn treo, audit "không đổi", hoặc audit của dòng khác **không** được dùng làm bằng chứng.
    - Mọi đoạn khác, kể cả bắt đầu bằng `[LMS]`/`[Zalo]` hoặc `[Auto QC: ...]`, được coi là **ghi tay** và giữ nguyên từng byte (`preserve_bcnb_note_text`).
    - Không đổi định dạng hiển thị trên BCNB.
    - Quy tắc này áp dụng cho **cả** đường dọn issue cũ ở nhánh notes-only (`resolve_row_proven_auto_issues`).
    - **Test** table-driven (`test_5_2_4_table_driven_provenance_auto_vs_manual_note`, 15 cases) + **integration** (`test_5_2_4_integration_direct_issue_verified_then_clean_rescan_removes_auto_preserves_manual_bytes`).
  - [x] **5.2.5.** Tick lỗi thì không `continue` im lặng: ghi outcome ghi chú `SKIPPED_TICK_FAILED` để hiện rõ trong summary. **Test**: `test_5_2_5_checkbox_tick_failure_records_skipped_tick_failed_for_checker_note`.
  - [x] **5.2.6.** Cổng G1–G6 (test: `tests.test_bcnb_actions`, `tests.test_zalo_parallel_service`, `tests.test_bcnb_columns`, `tests.test_bcnb_dom_contract`: `87/87 OK`; `node tests/check_template_js.js`: pass; `.\CHECK_QUALITY.ps1 -Isolated`: exit `0`, coverage `77.2%`) + Sol 6.1 `high` `VERDICT: PASS` (session `01a18cce-0a4d-77c1-a911-aef4f4d945a3`).

---

### Chặng 5.3 — Đúng Đắn Bằng Chứng & QC ✅ (`6/6`)
- **Mục tiêu**: không ghi issue `[LMS]`/`[Zalo]` của buổi khác hay của candidate bị loại; lần quét không kết luận được không được xoá issue cũ.
- **Allowlist**: `services/lms_quality_service.py`, `services/zalo_scan_evidence.py`, `services/zalo_target_matching.py`, `services/zalo_bcnb_service.py`, `tests/test_lms_quality_service.py`, `tests/test_zalo_scan_evidence.py`, `tests/test_zalo_target_matching.py` (tạo mới nếu chưa có), `tests/test_bcnb_actions.py` (cập nhật fixture LMS slot có `date`/`index` theo 5.3.1).
- **Checklist**:
  - [x] **5.3.1.** Chọn slot LMS theo **ngày học và số buổi**, bỏ cách lấy theo vị trí `lesson_num - 1` (`lms_quality_service.py` ~L1518). Truy vấn GraphQL hiện chưa lấy ngày hay định danh buổi (~L1132), nên:
    - (a) ✅ **Đã xác minh tên trường thật (2026-10-30 23:14, Owner duyệt 1 truy vấn chỉ đọc)**: `ClassSlot` trên `https://lms-api.mindx.edu.vn/` có `_id: ID`, `index: Int` (0-based: `buổi N` tương ứng `index == N - 1`), `date: Date` (00:00 `Asia/Ho_Chi_Minh` lưu dạng UTC `17:00:00.000Z` hôm trước), `startTime: Date`, `endTime: Date`, `summary: String`, `studentAttendance: [StudentAttendance]`; schema `ClassSlot` trên LMS không có trường trạng thái huỷ riêng.
    - (b) Đổi thời gian slot về múi giờ `Asia/Ho_Chi_Minh` rồi so với `ngay_hoc_iso` của BCNB. Slot chỉ được chọn khi **đúng 1** slot khớp ngày **và** khớp số buổi (`index + 1 == lesson_num`). Slot huỷ bị loại khi có cờ huỷ.
    - (c) Mọi trường hợp còn lại (thiếu metadata ngày/buổi, 0 hoặc >1 slot khớp, lệch số buổi) → "[LMS] … cần kiểm tra thủ công" (không xác định), không fallback theo vị trí.
    - **Test**: slot lệch do học bù; slot bị huỷ; 2 slot trùng ngày; cùng ngày nhưng sai số buổi; thiếu metadata ngày; thiếu metadata số buổi; ranh giới múi giờ (17:00 UTC hôm trước).
  - [x] **5.3.2.** Chỉ ghi `target_match_note` vào `date_context` khi candidate **được match**; note của candidate bị loại giữ trong danh sách candidate (kể cả `TARGET_SESSION_AMBIGUOUS`). **Test**: target NOT_FOUND/AMBIGUOUS không rò note cụ thể sang `date_context`/`status_detail`.
  - [x] **5.3.3.** `evaluate_zalo_content_issues` chỉ chạy khi `evidence_state ∈ SUBMITTED_*`; NOT_FOUND/INSUFFICIENT không sinh issue nội dung. **Test**.
  - [x] **5.3.4.** Thêm nhánh "không xác định" cho Zalo: khi `evidence_state = INSUFFICIENT_EVIDENCE` hoặc quét lỗi, giữ nguyên các issue `[Zalo]` đang có trên BCNB (giống cách LMS đang làm). **Test**.
  - [x] **5.3.5.** `CONTENT_DATE_MISMATCH` chỉ gắn cho candidate được match và loại trùng. **Test**.
  - [x] **5.3.6.** Cổng G1–G6 (test: `tests.test_lms_quality_service`, `tests.test_zalo_scan_evidence`, `tests.test_zalo_parallel_service`, `tests.test_zalo_target_matching`: `113/113 OK`; `node tests/check_template_js.js`: pass; `.\CHECK_QUALITY.ps1 -Isolated`: `643/643 OK`, coverage `76.8%`) + Sol 6.1 `high` `VERDICT: PASS` (session `01a18e0e-f611-7233-97fc-c25809bb6967`).

---

### Chặng 5.4 — Toàn Vẹn Weekly Summary / Email / Duyệt Tay ✅ (`7/7`)
- **Mục tiêu**: email và bảng tổng hợp chỉ lấy dữ liệu đáng tin; dry-run không làm thay đổi trạng thái thật; mỗi lần gửi mail có audit.
- **Allowlist**: `repositories/report_repository.py`, `services/report_email_service.py`, `services/zalo_bcnb_service.py`, `api/zalo_routes.py`, `repositories/audit_repository.py`, `tests/test_api_routes_unit.py`, `tests/test_zalo_parallel_service.py`.
- **Checklist**:
  - [x] **5.4.1.** Điều kiện đủ tư cách được định nghĩa **theo từng loại nguồn**, áp dụng thống nhất cho weekly summary và email:
    - **Dữ liệu quét Zalo** (baseline; overlay quét lại có chọn lọc `report_repository.py` ~L2013; job chọn tường minh qua `job_id` ~L1953): job `ZALO_SCAN` **đã kết thúc** (SUCCESS/PARTIAL_SUCCESS) và đúng `execution_mode` yêu cầu (mặc định production; dry-run phải xin rõ và trả kèm badge). Job tường minh không đủ tư cách → trả lỗi/cảnh báo rõ, không âm thầm dùng. Vẫn giữ quy tắc chỉ ghép cùng `snapshot_id`.
    - **Dữ liệu LMS** (`LMS_ANALYSIS`/`LMS_AUTOMATION`, ~L1052, ~L1542, ~L1559): giữ quy tắc hiện có (dry-run tách khỏi đã tích); chỉ thêm điều kiện job đã kết thúc.
    - **Duyệt tay** (audit `BCNB_MANUAL_APPROVE_TICK`, ~L1074, ~L1616): chỉ nhận audit `production` + `VERIFIED`, như hiện tại.
    - **Test**:
      - job CANCELLED/RUNNING/dry-run mới hơn không được chọn làm baseline;
      - baseline production + overlay quét lại dry-run → overlay bị loại;
      - yêu cầu tường minh một job CANCELLED → bị từ chối;
      - ghép cùng snapshot vẫn đúng;
      - đóng góp hợp lệ từ LMS và từ duyệt tay production vẫn được giữ.
  - [x] **5.4.2.** Giới hạn tài nguyên **trước khi** đọc dữ liệu: lọc theo tuần + `LIMIT` số job/target/audit (hằng số có tên). Vượt trần thì trả lỗi rõ ràng, không cắt bớt im lặng. **Test**.
  - [x] **5.4.3.** Gửi email trả `409 SCAN_ACTIVE_FOR_WEEK` khi đang có job ZALO_SCAN/LMS_AUTOMATION/ZALO_MANUAL_APPROVE hoạt động cho tuần đó. **Test**.
  - [x] **5.4.4.** Duyệt tay ở dry-run **không** ghi vào `results` của job gốc hay `write_status` của ledger; chỉ ghi audit `DRY_RUN`. Bỏ `suppress(Exception)` ở bước cập nhật ledger, thay bằng log event có `error_type`. **Test**.
  - [x] **5.4.5.** Email đầy đủ dòng:
    - các dòng BCNB không có target Zalo hiện là "Chưa quét Zalo";
    - trạng thái LMS lấy theo `(mã lớp, buổi/ngày)`, không chỉ theo mã lớp.
    - **Test**: 2 buổi cùng lớp trong 1 tuần.
  - [x] **5.4.6.** Audit mỗi lần gửi SMTP (tuần, năm, job_id, hash người nhận, SHA-256 nội dung). Gửi trùng trong cửa sổ ngắn phải có `confirm_duplicate=true`. Sửa nhãn "Tổng số lớp" cho đúng (đếm buổi hay đếm lớp). **Test**.
  - [x] **5.4.7.** Cổng G1–G6 (test: `tests.test_api_routes_unit`, `tests.test_zalo_parallel_service`, `tests.test_lms_quality_service`: `111/111 OK`; `node tests/check_template_js.js`: pass; `.\CHECK_QUALITY.ps1 -Isolated`: `637/637 OK`, coverage `76.9%`) + Sol 6.1 `high` `VERDICT: PASS` (session `01a18ddf-0483-7991-a4b3-6148b1db74a7`).

---

### Chặng 5.5 — Một Nguồn Sự Thật: `services/report_status.py` + Helper Dùng Chung ✅ (`8/8`)
- **Mục tiêu**: mỗi luật (trạng thái/verdict, Buổi 14, kiểm tra plan, mã lớp, cột BCNB) chỉ có một implementation; ra quyết định theo `evidence_state`/`category`, không theo emoji.
- **Allowlist**:
  - tạo mới `services/report_status.py`, `tests/test_report_status.py`;
  - `services/zalo_parallel_service.py`, `services/zalo_bcnb_service.py`, `services/zalo_evidence.py`, `services/lms_quality_service.py`, `repositories/report_repository.py`, `services/report_email_service.py`;
  - `services/zalo_preflight.py`, `api/zalo_routes.py`, `services/zalo_safety.py`, `services/zalo_discovery_matching.py`, `services/class_lifecycle.py`, `services/zalo_target_eligibility.py`, `services/zalo_target_matching.py`, `repositories/zalo_batch_repository.py`;
  - `services/bcnb_safety.py`, `services/bcnb_actions.py`;
  - `api/job_routes.py` (bổ sung `category`/`is_failed` vào kết quả job API);
  - `static/js/jobs.js`, `tests/check_template_js.js` (chỉ để consumer JS đọc được issue có cấu trúc; phần chuyển UI sang `category` thuộc 5.7);
  - test liên quan.
- **Checklist**:
  - [x] **5.5.1.** Tạo `services/report_status.py` gồm:
    - hằng `evidence_state` và các tập trạng thái kết thúc (terminal sets);
    - nhãn trạng thái (lấy từ `zalo_evidence.py`, không chép);
    - hằng số cửa sổ ngày;
    - `is_lesson_14(value)` (qua `normalize_lesson_number`);
    - `categorize_row(row) -> DONE | LATE | MISSING | REVIEW | EXEMPT | NOT_SCANNED`;
    - `issue_severity(issue) -> FAIL | NOTE`, dựa trên **mã issue có cấu trúc** (`{code, severity, text}`), không dựa trên chuỗi con tự do:
      - NOTE gồm đúng 2 nhóm Owner đã chốt: `LMS_MANUAL_CHECK` (D4; producer trong `lms_quality_service` chuyển sang phát mã ngay ở chặng này) và `ZALO_REPORT_NO_DATE`/`ZALO_REPORT_WRONG_DATE` (D2; producer làm ở 5.8.4);
      - issue cũ đã lưu trong kết quả job trước Wave 5 (chưa có mã) được coi là FAIL, **trừ** đúng marker văn bản cũ của D4. Bộ chuyển đổi tương thích này chỉ được nằm ở **một chỗ** trong `report_status.py`;
      - **chuyển tiếp cùng lúc**: khi producer đổi sang phát issue có cấu trúc thì mọi consumer phải đọc được ngay trong cùng chặng, gồm `lms_quality_service` (~L1503), `zalo_bcnb_service` (~L418-436), `report_repository` (~L549-557) và `jobs.js` (~L1083). Văn bản ghi lên Teaching và văn bản hiển thị phải là **chữ thường**, không phải chuỗi repr của dict. Mã và severity phải giữ nguyên qua lưu DB, audit provenance (5.2.4), summary/email và sau F5;
    - `is_failed(row)`: `True` khi có **ít nhất một** trong các điều kiện:
      - `category ∈ {MISSING, REVIEW}`;
      - **LMS xác nhận chưa nộp/chưa đánh giá** (giữ đúng tiêu chí trạng thái LMS hiện có ở `report_repository.py` ~L177-180, độc lập với Zalo và issue QC). D4 chỉ hạ mức cho trường hợp *không xác định*;
      - có issue `FAIL`.
      - `LATE` (D1), `DONE` và `EXEMPT` mà không vướng điều kiện nào ở trên → `False`.
    - **Test** table-driven (`test_5_5_1_to_5_5_4_status_category_failure_and_structured_issues`), bắt buộc có:
      - 🟡 trễ → `LATE`/không lỗi; ✅ + chỉ issue NOTE → không lỗi; ✅ + issue FAIL → lỗi; NOT_FOUND → lỗi; Buổi 14 → `EXEMPT`;
      - Zalo `DONE`/`LATE` + LMS xác nhận chưa nộp + không có issue QC → **lỗi**;
      - issue có cấu trúc đi qua lưu DB → summary → email → hiện đúng chữ.
  - [x] **5.5.2.** Thay mọi bản sao luật Buổi 14 (PS ~1556; BS ~254/619/905; EV ~44; RR ~48/69; LQ ~619/776/1496) bằng `is_lesson_14`. **Test**: `"14"`, `"Buổi 14"`, `"014"`, `14`.
  - [x] **5.5.3.** Thay các quyết định theo emoji/chuỗi con (RR ~177-179, ~461-506; BS ~1226; LQ ~760) bằng `categorize_row`/`is_failed`/`evidence_state`. Theo **D1**: `is_failed_weekly_summary_row` không còn tính 🟡 là lỗi; bộ đếm summary đưa trễ vào nhóm "đã nộp (trễ)", không vào "pending". Tiêu chí lỗi của email đổi từ `bool(issue_description)` sang `is_failed(row)`, nên issue NOTE không làm dòng thành lỗi. **Test** hồi quy: fixture cũ cho kết quả giống hệt, trừ đúng các thay đổi do D1 và mức NOTE (liệt kê trong test).
  - [x] **5.5.4.** Backend trả `category` và `is_failed` cho từng dòng trong weekly summary và trong kết quả job API (UI dùng ở 5.7). **Test** contract.
  - [x] **5.5.5.** Gom một helper `validate_two_week_plan()` dùng chung cho `zalo_preflight`, `/run-scan` và `/api/zalo-scan-plan`. Chỉ yêu cầu khớp nhau ở **lỗi kiểm tra plan** (thiếu tuần, target không hợp lệ, trùng, snapshot cũ), **không** gồm 409 do YELLOW chưa xác nhận hay do đang có job (`zalo_routes.py` ~L71). **Test**: scan-plan báo unavailable với mọi lỗi kiểm tra plan mà `/run-scan` từ chối; test riêng cho xác nhận YELLOW, từ chối khi có job, và RED được ưu tiên hơn YELLOW (`test_5_5_5_validate_two_week_plan_shared_across_preflight_scan_plan_and_run_scan`).
  - [x] **5.5.6.** Gom một hàm `normalize_class_code()` dùng cho identity, coverage và canonical key (chỉ giữ một bản `_identity_tokens`). `parse_bcnb_scan_target` ưu tiên `class_date_iso`. **Test**: mã có khoảng trắng hoặc ký tự phân cách lạ (`test_5_5_6_normalize_class_code_and_parse_bcnb_scan_target_prefers_iso`).
  - [x] **5.5.7.** Gom một bộ nhận diện cột BCNB (NX Zalo, NX LMS, ngày, mã lớp, buổi) dùng chung cho `bcnb_safety` và danh sách header JS trong `bcnb_actions`. Gom helper "lấy + chấm LMS" dùng chung cho `zalo_bcnb_service` (~554-602) và `zalo_parallel_service` (~2689-2734). **Test**: `test_bcnb_columns`, `test_bcnb_dom_contract`, `test_5_5_7_bcnb_header_matchers_and_shared_lms_quality_helper`.
  - [x] **5.5.8.** Cổng G1–G6 (test: `tests.test_report_status`, `tests.test_lms_quality_service`, `tests.test_api_routes_unit`, `tests.test_zalo_parallel_service`, `tests.test_bcnb_columns`: `125/125 OK`; `node tests/check_template_js.js`: pass; `.\CHECK_QUALITY.ps1 -Isolated`: `649/649 OK`, coverage `77.2%`) + Sol 6.1 `high` `VERDICT: PASS` (session `01a18e7f-c706-7e81-a946-1603a43e918c`).

---

### Chặng 5.6 — Không Rơi Dòng Im Lặng (Discovery / Plan / Preflight) ✅ (`8/8`)
- **Mục tiêu**: dòng BCNB nào không trở thành target đều phải được đếm, liệt kê và đẩy lên preflight.
- **Allowlist**: `services/zalo_discovery_plan.py`, `services/zalo_discovery_service.py` (≤ 400 dòng), `services/zalo_target_eligibility.py`, `services/zalo_preflight.py`, `services/report_status.py` (nơi đặt `validate_two_week_plan` dùng chung từ 5.5.5), `api/zalo_routes.py`, `repositories/zalo_batch_repository.py`, `repositories/group_repository.py` (mở rộng theo §5.A.2 để `_snapshot_metadata` chấp nhận snapshot chỉ có `exempt_targets` Buổi 14 theo mục `5.6.3`), `db/database.py` (chỉ khi cần migration cộng thêm cho 5.6.3), `services/zalo_parallel_service.py`, `services/zalo_scan_evidence.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`, `static/js/state.js`, `tests/test_zalo_discovery_service.py`, `tests/test_zalo_target_eligibility.py`, `tests/test_api_routes_unit.py`, `tests/test_zalo_parallel_service.py`, `tests/test_zalo_scan_evidence.py`, `tests/check_template_js.js`.
- **Checklist**:
  - [x] **5.6.1.** Plan sinh bộ đếm + danh sách cho các nhóm `transition_review`, `missing_zalo`, `ambiguous_zalo`, `deduped`, `invalid_date`, `skipped_empty`. Preflight chuyển **YELLOW** khi có nhóm nào > 0, kèm danh sách. **Test**.
  - [x] **5.6.2.** Dòng chuyển cấp của tuần trước được hiện thành mục cần xem lại trong plan và UI (không tự tạo target); sửa docstring `_build_scan_targets` cho đúng. **Test**.
  - [x] **5.6.3.** Miễn trừ Buổi 14 được dựng trực tiếp từ dòng BCNB, không cần match Zalo hay qua time gate, và **không bịa danh tính Zalo**. Hiện `seed_targets` từ chối `group_name` rỗng (`zalo_batch_repository.py` ~L98), nên cần một đường nạp riêng cho miễn trừ:
    - Ưu tiên không đổi schema: plan có danh sách `exempt_targets` riêng, bước ghi BCNB xử lý chúng và ghi audit/ledger với loại `EXEMPTION`.
    - Nếu buộc phải đổi schema: dùng migration cộng thêm có checksum (ví dụ cột `target_kind`), đã có backup ở 5.0.2 và phải qua Phase 7 gate.
    - **Nhận job (admission)**: hiện preflight yêu cầu có candidate thường và target quét đủ điều kiện (`zalo_preflight.py` ~L191, ~L227), và `/run-scan` cũng tự yêu cầu candidate (`zalo_routes.py` ~L203). Phải định nghĩa rõ hai trường hợp, và vẫn giữ nguyên các chốt preflight RED/YELLOW, khóa job độc quyền (`reject_if_active`) và `selected_target_keys`:
      - **plan chỉ có miễn trừ**;
      - **miễn trừ + toàn bộ target thường bị hoãn**.
    - **Test** xuyên suốt discovery → preflight → route `/run-scan` → nạp miễn trừ → ledger → tích miễn có readback:
      - nhóm Zalo MISSING nhưng Buổi 14 vẫn được miễn;
      - plan chỉ có miễn trừ;
      - miễn trừ + target thường bị hoãn hết;
      - không có dòng scan nào được tạo với `group_name` giả;
      - RED vẫn chặn; đang có job vẫn bị từ chối.
  - [x] **5.6.4.** Dòng có ngày học không parse được thành mục cần xem lại riêng; plan vẫn chạy cho các dòng khác (không còn `SCAN_PLAN_INVALID_TARGET` cho cả plan). **Test**.
  - [x] **5.6.5.** Ghim `snapshot_id`: `/run-scan` dùng snapshot của lần preflight đầu cho cả bước lấy dữ liệu và preflight lần 2; lệch thì trả `409 SNAPSHOT_CHANGED`. **Test**.
  - [x] **5.6.6.** Tên nhóm có nhiều khung giờ → `parse_group_end_time` trả `None` → hoãn với lý do "cần xác nhận giờ học". **Test**.
  - [x] **5.6.7.** Gắn cờ `deadline_verification=PENDING` ngay ở bước quét và giải quyết ở bước ghi; UI/email hiện ⏳ khi còn PENDING. Quota AI tính theo `target_key`; ghi lý do `ai_assist_budget_exhausted`/`AI_CLASSIFIER_ERROR` vào `classifier_result`. **Test**.
  - [x] **5.6.8.** Cổng G1–G6 (test: `tests.test_zalo_discovery_service`, `tests.test_zalo_target_eligibility`, `tests.test_report_status`, `tests.test_api_routes_unit`, `tests.test_zalo_parallel_service`, `tests.test_zalo_scan_evidence`: `176/176 OK`; `node tests/check_template_js.js`: pass; `.\CHECK_QUALITY.ps1 -Isolated`: `651/651 OK`, coverage `77.4%`) + Sol 6.1 `high` `VERDICT: PASS` (session `01a18edc-054f-7e42-b2a3-5564c0df97d5`).

---

### Chặng 5.7 — Giao Diện Vận Hành Bám Dữ Liệu Server ✅ (`8/8`)
- **Mục tiêu**: bảng kết quả luôn dựng lại được từ server (F5 không mất dữ liệu); nút bấm phản ánh đúng trạng thái; mọi luật hiển thị đọc từ `category`.
- **Phụ thuộc**: 5.5.4 đã xong.
- **Allowlist**: `static/js/jobs.js`, `static/js/state.js`, `templates/partials/dashboard.html`, `templates/partials/specs.html`, `api/job_routes.py`, `repositories/job_repository.py`, `tests/check_template_js.js`, `tests/test_api_routes_unit.py`.
- **Checklist**:
  - [x] **5.7.1.** Plan cũ vẫn giữ `required_week` để hiển thị, chỉ chặn nút quét. Banner ghi đúng "Kế hoạch đã cũ — Dò lại" kèm `observed_at`. **Test** node (`checkPhase57FrontendHardening`).
  - [x] **5.7.2.** Khi tải trang và sau khi một job ghi kết thúc (ZALO_SCAN, LMS_AUTOMATION, ZALO_MANUAL_APPROVE): dựng lại bảng từ weekly summary của server (`keepLiveMode:true`), bỏ phụ thuộc vào baseline lưu trong bộ nhớ. `/api/jobs/latest` nhận thêm tham số `job_type`. **Test**: F5 sau khi quét lại có chọn lọc vẫn hiện đủ dòng (`test_5_7_2_get_latest_job_supports_job_type_filter_in_route_and_repo`, `checkPhase57FrontendHardening`).
  - [x] **5.7.3.** Nút ⟳ giữ nguyên chế độ đang xem (live/lịch sử); polling không tự xoá chế độ xem lịch sử. **Test** (`checkPhase57FrontendHardening`).
  - [x] **5.7.4.** Không bật lại alert/modal cho job đã kết thúc trước khi tải trang (`finished_at < pageLoadAt` hoặc lưu `sessionStorage`). **Test** (`checkPhase57FrontendHardening`).
  - [x] **5.7.5.** Khoá các nút hành động khi `currentJobActive`. Nút Quét lại trên từng dòng tuân theo gate plan/preflight. Ẩn nút duyệt dry-run sau khi đã mô phỏng xong. **Test** (`checkPhase57FrontendHardening`).
  - [x] **5.7.6.** Hộp xác nhận YELLOW liệt kê các cảnh báo. Tab chính có dải preflight/tiến độ thu gọn. **Test** (`checkPhase57FrontendHardening`).
  - [x] **5.7.7.** KPI, bộ lọc, Copy nhắc GV, Quét lại, Duyệt & Tích đều đọc `category`/`is_failed` từ server. KPI tách ô "Trễ" riêng (D1), không tô đỏ. Issue NOTE hiện dạng ghi chú (màu trung tính), không gắn nhãn lỗi. Xoá logic trạng thái/đánh giá bị trùng trong JS và **xoá phần CSV builder chết** (D6: `jobs.js` ~1959-2109, ~2651-2710; giữ xuất CSV qua server `format=csv` nếu có nút). Lưu ý phụ thuộc: `exportWeeklySummaryCsv` hiện **dựng fallback rows bằng các builder này trước khi** rẽ sang nhánh email (`jobs.js` ~L2529-2542). Đã gỡ lời gọi đó trước khi xoá builder; nút Gửi Mail và xuất CSV qua server chạy đúng, có **test hành vi cho cả hai** (`checkPhase57FrontendHardening`, `checkPhase32WeeklyHistoryAndCsvExport`, `checkPhase44DashboardAndClipboardEmail`). Sửa tooltip `\n`, nút "Kiểm tra lại" bị lặp, thống nhất chữ "Dò kế hoạch". **Test** node behavior.
  - [x] **5.7.8.** Cổng G1–G6 (test: `tests.test_api_routes_unit`: `41/41 OK`; `node tests/check_template_js.js`: `scripts=7 handlers=34 behavior=13`; `.\CHECK_QUALITY.ps1 -Isolated`: `655/655 OK`, coverage `77.5%`) + Sol 6.1 `high` `VERDICT: PASS` (session `01a18f26-2405-7f20-8260-248a1f8eb3c0`).

---

### Chặng 5.8 — Dọn Dẹp, Tài Liệu & Nghiệm Thu Wave 5 ✅ (`7/7`)
- **Allowlist**:
  - `CLASS_LIFECYCLE.md`, `services/class_lifecycle.py`, `tests/test_class_lifecycle*.py`;
  - `services/zalo_service.py`, `tests/test_zalo_service.py`, `tests/check_phase3_quality.py`, `tests/check_phase4_quality.py` (chỉ để chuyển invariant sang kiểm tra implementation song song còn lại; không nới lỏng kiểm tra hành vi hay ngân sách số dòng);
  - 5.8.1: `services/zalo_discovery_plan.py`, `services/zalo_discovery_matching.py`, `repositories/group_repository.py`, `tests/test_zalo_discovery_service.py`, `tests/test_basic_repositories.py`;
  - 5.8.4 / 5.8.5: `services/zalo_scan_evidence.py`, `services/lms_quality_service.py`, `services/zalo_bcnb_service.py`, `services/zalo_parallel_service.py`, `services/report_status.py`, `static/js/state.js`, `tests/test_zalo_scan_evidence.py`, `tests/test_lms_quality_service.py`, `tests/test_zalo_parallel_service.py`, `tests/test_report_status.py`, `tests/test_zalo_target_matching.py`, `tests/check_template_js.js`;
  - `services/external_gateways.py`, `services/zalo_target_matching.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`;
  - `PROJECT_SUMMARY.md`, `PROJECT_STATE.md`, `PROJECT_DECISIONS.md`, `CHANGELOG.md`, `VERSION.md`, `UPGRADE_PROGRESS.md`.
- **Checklist**:
  - [x] **5.8.1.** **(D3 đã chốt)** Mở rộng parser vòng đời lớp theo đúng các chuỗi Owner đã xác nhận (`test_5_8_1_table_driven_real_class_codes_and_progression_rules`, `test_5_8_1_js_transition_same_cohort_and_cross_track_pre_to_arm_requires_review`, `test_5_8_1_legacy_js_cs_cohort_key_read_write_continuity_and_collision_fail_closed`; đếm chỉ đọc trên `.backups/db/pre-wave5-20261030-20261030-095820.sqlite3`: `8/25` dòng `class_cohorts`, `8/44` dòng `groups`, `8/25` dòng `class_code_history` mang key `VT-J|S|*`/`VT-C|S|*`):
    - **Lỗi hiện tại**: `_PROGRESSION_PATTERN.search(token)` neo `$` (`class_lifecycle.py` ~L24-26, ~L56, ~L82-85). Hệ quả là `JSB17`/`CSA03` vô tình bị đọc thành track `S` với center giả `VT-J`/`VT-C`, còn `PRE*/ARM*/SEMI*/ROB4*` trả `None` (không có cohort, không tự kết thúc khi BCNB báo Finished).
    - **Mới**:
      - `PROGRESSION_LEVELS` thêm `JS`, `CS`, `PRE`, `ARM`, `SEMI`, `ROB4`, mỗi track `(B, A, I)`.
      - So khớp **toàn bộ token** (fullmatch), các track xếp dài trước: `ROB4|SEMI|PRE|ARM|PT|JS|CS|G|S`.
      - Bỏ qua token hậu tố như `HB`; giữ center gồm các token đứng trước (ví dụ `VT-ROB`, `VT-KIND`, `VT-C4K`).
      - Chỉ lên cấp tự động B → A → I **trong cùng track**. Chuyển giữa track (ví dụ `PREI → ARMB`) không tự động, vẫn để review.
    - **Tương thích dữ liệu**: các dòng `class_cohorts`/`cohort_key` đang có của JS/CS mang key "vô tình" (`VT-J|S|NN`, `VT-C|S|NN`). Discovery hiện ghi key mới vào `groups`, chèn cohort theo key đó và đánh dấu key cũ không còn thấy là đang chuyển cấp (`group_repository.py` ~L780-784, ~L969-974, ~L1011-1016). Vì vậy alias chỉ ở bước đọc là **không đủ**, có thể tách lịch sử hoặc tạo cohort trùng.
      - Trước hết đếm số dòng bị ảnh hưởng trên **bản sao backup của 5.0.2** (chỉ đọc).
      - **Không** tự migrate DB thật. Ở mọi đường đọc **và ghi** (lưu discovery, chèn cohort, đánh dấu lifecycle, lịch sử mã lớp), key cũ được ánh xạ về key mới. Việc ánh xạ chỉ được thực hiện khi danh tính lớp đã lưu (mã lớp đầy đủ) khớp; nếu va chạm (nhiều cohort cũ ứng với một key mới, hoặc ngược lại) thì **fail-closed**: không gộp, đánh dấu cần xem lại.
      - Backfill dữ liệu thật chỉ làm khi Owner duyệt riêng một script có backup + báo cáo dry-run.
    - Sửa `CLASS_LIFECYCLE.md` cho khớp code mới. Chạy lại test của 5.6 (transition/coverage).
    - **Test** table-driven với các định dạng mã thật: `VT-C4K-PTB16`, `VT-C4K-GI35`, `VT-C4K-SA27`, `VT-JSB17`, `VT-JSI12-HB`, `VT-CSA04-HB`, `VT-CSI02`, `VT-ROB-PREA18`, `VT-ROB-ARMB20`, `VT-ROB-SEMIB07`, `VT-KIND-ROB4I02`. Bắt buộc có: `CSA03` **không** ra track `S`; `JSB17 → JSA17` cùng cohort; `PREI16 → ARMB16` không tự nối; key cũ vẫn tra được. Thêm test SQLite tổng hợp (không migrate): DB có cohort key cũ → lưu discovery → lưu lại lần 2 → không tạo cohort trùng, lịch sử mã lớp liền mạch, trạng thái lifecycle đúng; trường hợp va chạm alias → fail-closed.
  - [x] **5.8.2.** **(D6 đã chốt: xoá)** Xoá thân luồng tuần tự `zalo_service.py:176-503` (còn `166 <= 520` dòng), giữ entrypoint chuyển sang luồng song song; xoá/viết lại các test chỉ nhắm luồng tuần tự (`tests/test_zalo_service.py`: `8/8 OK`); giữ ngân sách ≤ 520 dòng. Các invariant đang bám chuỗi trong `zalo_service.py` (`tests/check_phase3_quality.py`, `tests/check_phase4_quality.py`) đã được **chuyển sang kiểm tra implementation song song** với độ phủ hành vi tương đương; **không** giữ chuỗi giả cho qua gate và không bỏ kiểm tra. Phần CSV builder chết phía JS được xoá ở 5.7.7.
  - [x] **5.8.3.** **(D5 đã chốt: được gửi nguyên văn)** Không thêm bước che tên. Giữ cờ `ZALO_AI_ASSIST_CLASSIFIER=false` mặc định và cô lập trong gate. Xác nhận nguyên văn tin nhắn gửi Gemini **không** bị ghi thêm vào log, `classification_logs` hay kết quả job ngoài các trường đang có. **Test**: log của lời gọi AI không chứa nguyên văn tin nhắn (`test_5_8_3_ai_assist_passes_verbatim_text_and_never_logs_raw_message`).
  - [x] **5.8.4.** **(D2 đã chốt: khớp + ghi chú QC)** Trong `resolve_report_target` (`zalo_target_matching.py`) và chuỗi chấm bằng chứng (`zalo_scan_evidence.py`):
    - Điều kiện chung: ngày gửi lấy từ header ngày của Zalo đọc được và **trùng ngày học của target**.
    - Nếu nội dung **không ghi ngày lẫn buổi** → khớp (`SUBMITTED_*`) + issue NOTE `[Zalo] Báo cáo không ghi ngày/buổi` (`ZALO_REPORT_NO_DATE`).
    - Nếu nội dung **ghi ngày khác ngày học** nhưng không ghi số buổi mâu thuẫn → khớp + issue NOTE `[Zalo] Báo cáo ghi sai ngày (dd/mm)` (`ZALO_REPORT_WRONG_DATE`).
    - **Vẫn để REVIEW** (chỉ áp cho **nhánh fallback D2 mới**, không áp cho các nhánh khớp đang có): không đọc được ngày gửi; gửi khác ngày học **mà không có ngày/buổi khớp target**; nội dung ghi rõ **số buổi khác** target; cùng nhóm có 2 target BCNB trùng ngày đó.
    - **Giữ nguyên** nhánh khớp hiện có cho báo cáo gửi sau ngày học có ghi đúng ngày/buổi (`zalo_target_matching.py` ~L126-164): báo cáo trễ hợp lệ vẫn khớp và vẫn là `LATE` theo D1.
    - Issue NOTE đi qua đường ghi chú của 5.2 (có audit provenance 5.2.4) và không làm dòng bị tính lỗi (5.5.3).
    - **Test**: từng nhánh trên; báo cáo gửi hôm sau không ghi ngày vẫn REVIEW; **hồi quy D1**: báo cáo trễ ghi đúng ngày/buổi → `LATE`, vẫn được tích NX Zalo; nhãn trạng thái và `is_failed=false` (`test_5_8_4_same_day_missing_or_wrong_date_fallback_and_review_branches`, `test_5_8_4_same_day_missing_or_wrong_date_e2e_and_d1_late_regression`).
  - [x] **5.8.5.** **(D4 đã chốt: chỉ ghi chú QC)**
    - Chuyển "[LMS] … cần kiểm tra thủ công" thành issue **mã có cấu trúc `LMS_MANUAL_CHECK`, mức NOTE**, thay cho chuỗi điều khiển `"cần kiểm tra thủ công"` (`zalo_bcnb_service.py`, `zalo_parallel_service.py`).
    - Issue này được **ghi lên ô `Mô tả vấn đề`** của đúng dòng (trước đây bị lọc bỏ), và không làm dòng bị tính lỗi trong email.
    - **Chốt an toàn**: nếu LMS lỗi **toàn hệ thống** (`is_lms_fetch_unavailable`: mọi lớp được yêu cầu đều `fetch_error`, hoặc lời gọi lấy dữ liệu ném exception) thì **không** ghi note hàng loạt. Khi đó đặt cờ `lms_fetch_unavailable` trong summary và hiện cảnh báo chung trên UI (`static/js/jobs.js`, `static/js/state.js`).
    - Các issue `[LMS]` FAIL cũ đã verified được giữ nguyên khi kết quả là "không xác định".
    - **Test**: một lớp thiếu slot → note được ghi, `is_failed=false`; mọi lớp `fetch_error` → không POST note nào, có cờ cảnh báo; issue FAIL cũ không bị xoá (`test_5_8_5_lms_manual_check_note_written_and_system_outage_suppressed`, `checkPhase57FrontendHardening`).
  - [x] **5.8.6.** Cập nhật `PROJECT_SUMMARY.md`, `PROJECT_STATE.md`, `CHANGELOG.md`, `VERSION.md`; append `PROJECT_DECISIONS.md` **DEC-039** gồm:
    - quyết định D1–D6 của Owner;
    - mức độ issue `FAIL`/`NOTE`;
    - taxonomy `category` một nguồn;
    - giới hạn của bảo vệ ghi chú (không phải compare-and-swap nguyên tử);
    - slot LMS theo ngày;
    - review Wave 5 dùng Sol `high`.
  - [x] **5.8.7.** Cổng cuối:
    - G1 (`189/189 OK`), G2 (`node tests/check_template_js.js` pass, `scripts=7 handlers=34 behavior=13`), G3 (`.\CHECK_QUALITY.ps1 -Isolated` đạt exit `0`, `QUALITY GATE PASSED`, `663/663` tests OK, coverage `78.4%`), G4 (`git diff --check` exit `0` + manifest check `PASS`, `outside_delta=0`);
    - Sol 6.1 `high` review **toàn bộ diff Wave 5** (`git diff b395b94..HEAD` hoặc working tree) đạt `VERDICT: PASS` (session `01a18fa5-a4c3-7293-93d3-1cae60e87f5e`);
    - soạn **checklist nghiệm thu cho Owner**: dry-run chỉ đọc trên BCNB/Zalo/LMS thật, gồm 1 tuần có học bù, 1 lớp Buổi 14, 1 lớp chuyển cấp, F5 sau quét lại. Chỉ chạy production khi Owner duyệt riêng một target cụ thể.

---

## 📝 GHI CHÚ CHUẨN BỊ NÂNG CẤP (PREPARATION NOTES)



1. **Nguyên tắc triển khai từng bước (Incremental & Reversible)**:
   - Mỗi hạng mục (`1.1`, `1.2`, `1.3`...) sẽ được thực hiện độc lập, chạy đủ cổng kiểm tra chất lượng `.\CHECK_QUALITY.ps1` (cả chế độ thường và `-Isolated`), cập nhật tích `[x]` vào file `UPGRADE_PROGRESS.md` này trước khi chuyển sang hạng mục tiếp theo.
2. **Bảo toàn tuyệt đối dữ liệu & cấu hình hiện tại**:
   - Không thay đổi cấu trúc `.env` đang chạy ổn định của bạn (chỉ thêm biến tùy chọn có giá trị mặc định an toàn nếu cần).
   - Giữ nguyên toàn bộ cơ chế khóa hồ sơ trình duyệt (`exclusive_browser_job`), kiểm tra sẵn sàng (`zalo_preflight.py`) và kiểm chứng đọc ngược checkbox trên BCNB (`execute_verified_checkbox_action`).
3. **Thứ tự khuyến nghị bắt đầu**:
   - Bắt đầu từ **Phase 1.1 (Copy danh sách nhắc giáo viên 1-Click)** và **Phase 1.2 (Quét lại có chọn lọc các lớp lỗi)** vì đây là hai tính năng giúp tiết kiệm thời gian rõ rệt nhất ngay trong đợt kiểm tra hàng tuần và hoàn toàn không làm xáo trộn luồng dữ liệu cốt lõi.

4. **Quy trình Điều phối AI chuẩn Hàng Không (`DEC-036` — Cập nhật 2026-10-22)**:
   - **Bước 1 (EXEC)**: Lead trực tiếp code từng hạng mục (`1.1`, `1.2`, `1.3`...).
   - **Bước 2 (TEST)**: Chạy unit test + `node tests/check_template_js.js` + `.\CHECK_QUALITY.ps1 -Isolated`.
   - **Bước 3 (REVIEW)**: Gọi độc lập `codex exec -m gpt-6.1-sol -c model_reasoning_effort="xhigh" -s read-only -`.
   - **Bước 4 (REPORT)**: Báo cáo dạng điện tín hàng không (tối ưu token tối đa) và tích `[x]` vào checklist.
