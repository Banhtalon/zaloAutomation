# KẾ HOẠCH CHI TIẾT & TIẾN ĐỘ NÂNG CẤP DỰ ÁN (UPGRADE PLAN & PROGRESS)

**Cập nhật lần cuối:** `2026-10-28 09:00 +07:00`
**Trạng thái hiện tại:** Đã hoàn tất **100% lộ trình nâng cấp (`45/45` hạng mục chi tiết thuộc 8 giai đoạn: Phase 0, Phase 1.1, Phase 1.2, Phase 1.3, Phase 2.1, Phase 2.2, Phase 3.1 & Phase 3.2 ✅)**.

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
