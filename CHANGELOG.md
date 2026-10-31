# CHANGELOG — Lịch Sử Phát Triển Dự Án Zalo Automation (MindX Auto)

Tài liệu này ghi lại toàn bộ quá trình hình thành, phát triển kiến trúc, nâng cấp tính năng và củng cố an toàn vận hành của hệ thống **Zalo Automation / MindX Auto** theo trình tự thời gian (từ mới nhất đến giai đoạn khởi tạo).

---

## [2026-10-31] — Wave 5 (`5.0` → `5.8`): Hardening Toàn Diện Luồng Kiểm Tra Báo Cáo Zalo/BCNB/LMS (`DEC-036`, `DEC-039`)

### 🛡️ Củng cố độ bền, tính đúng đắn bằng chứng & Một nguồn sự thật (`UPGRADE_PROGRESS.md` — `Wave 5: 59/59 ✅`)
- **Chặng 5.0 — Baseline & Sao lưu trước Wave 5 (`.backups/db/pre-wave5-20261030-20261030-095820.sqlite3`)**:
  - Sao lưu nóng SQLite (`integrity="ok"`, `foreign_key_errors=[]`), diễn tập khôi phục `.backups/restore-drill.sqlite3` và khóa baseline manifest.
- **Chặng 5.1 — Độ bền vòng quét Zalo (`services/zalo_parallel_service.py`, `services/zalo_scan_evidence.py`)**:
  - Bọc `retry_target_once` và `setup_fresh_retry_page()` trong khối xử lý lỗi của từng target, thêm timeout cứng mỗi target (`ZALO_TARGET_TIMEOUT_SECONDS = 180`), kiểm tra hủy trong vòng quét khoảng trống lịch sử, áp trần mặc định `DEFAULT_BATCH_MAX_MESSAGES = 500` trả `INSUFFICIENT_EVIDENCE` (`MESSAGE_CAP_REACHED`) thay vì `NOT_FOUND_VERIFIED`, và retry chụp ảnh minh chứng báo cáo 1 lần (`MAX_CAPTURE_ATTEMPTS = 2`).
- **Chặng 5.2 — Cô lập ghi BCNB: Tick Checkbox vs Ghi chú `Mô tả vấn đề` (`services/zalo_bcnb_service.py`, `services/bcnb_actions.py`, `services/bcnb_safety.py`, `repositories/audit_repository.py`)**:
  - Tách hoàn toàn lỗi ghi chú `BCNB_UPDATE_CHECKER_NOTE` khỏi `BatchFailurePolicy.record` và kết quả tick `NX Zalo`; truyền `notes_only=True` cho dòng không nằm trong tập claim checkbox và ghi `SKIPPED_TICK_FAILED` khi tick checkbox lỗi.
  - Bảo vệ lạc quan chống ghi đè chỉnh sửa của QC (`read_checker_note_by_session` trước `POST` $\rightarrow$ `CONCURRENT_NOTE_EDIT`, đọc ngược sau hard reload $\rightarrow$ `CONFLICT_AFTER_WRITE`), phân tách đoạn auto và đoạn ghi tay theo 5 điều kiện provenance từ `external_action_audit` (`preserve_bcnb_note_text`, bảo toàn byte-for-byte phần ghi tay).
- **Chặng 5.3 — Đúng đắn bằng chứng & QC: Slot LMS theo ngày học + số buổi, Chống rò rỉ note, Trạng thái không xác định (`services/lms_quality_service.py`, `services/zalo_scan_evidence.py`, `services/zalo_bcnb_service.py`)**:
  - Bổ sung `_id`, `index`, `date`, `startTime`, `endTime` vào truy vấn GraphQL LMS và thay thế việc lấy slot theo vị trí `lesson_num - 1` bằng `select_matching_lms_slot` (đổi về múi giờ `Asia/Ho_Chi_Minh`, chỉ chọn khi đúng 1 slot khớp đồng thời ngày học và `index + 1 == lesson_num`).
  - Chỉ ghi `target_match_note` vào `date_context` khi candidate được match; chỉ chạy `evaluate_zalo_content_issues` khi `evidence_state ∈ SUBMITTED_*`; giữ nguyên các issue `[Zalo]` đang có trên BCNB khi quét ở trạng thái không xác định (`INSUFFICIENT_EVIDENCE` hoặc lỗi quét).
- **Chặng 5.4 — Toàn vẹn Weekly Summary, Email & Duyệt tay (`repositories/report_repository.py`, `services/report_email_service.py`, `services/zalo_bcnb_service.py`, `api/zalo_routes.py`, `repositories/audit_repository.py`)**:
  - Xác định điều kiện đủ tư cách theo từng nguồn (chỉ lấy job đã kết thúc và đúng `execution_mode`, gắn `execution_mode_badge="DRY-RUN"` khi xin xem dry-run), áp trần tài nguyên trước khi đọc dữ liệu, chặn gửi email bằng `409 SCAN_ACTIVE_FOR_WEEK` khi đang có job hoạt động cho tuần đó, không ghi vào `source_job`/ledger khi duyệt tay ở `dry-run`, tổng hợp dòng BCNB chưa quét thành `"Chưa quét Zalo"`, phân giải trạng thái LMS theo từng buổi `(class_code, lesson, class_date_iso)` và ghi audit nguyên tử `begin_smtp_send_intent` cho mỗi lần gửi SMTP.
- **Chặng 5.5 — Một nguồn sự thật `services/report_status.py` + Helper dùng chung (`services/report_status.py`, `services/bcnb_safety.py`, `services/zalo_preflight.py`)**:
  - Hợp nhất hằng trạng thái, `is_lesson_14`, `categorize_row` (`DONE | LATE | MISSING | REVIEW | EXEMPT | NOT_SCANNED`), `issue_severity` (`FAIL | NOTE` theo mã có cấu trúc `{code, severity, text}`), `is_failed(row)` (nộp trễ `LATE` và issue mức `NOTE` không tính là lỗi theo **D1/D2/D4**), `validate_two_week_plan()`, `normalize_class_code()`, bộ nhận diện cột BCNB và helper `fetch_and_evaluate_lms_quality`.
- **Chặng 5.6 — Không rơi dòng im lặng trong Discovery / Plan / Preflight (`services/zalo_discovery_plan.py`, `services/zalo_preflight.py`, `api/zalo_routes.py`, `services/zalo_target_eligibility.py`, `services/zalo_scan_evidence.py`)**:
  - Đếm và liệt kê đầy đủ 6 nhóm `dropped_rows` (`transition_review`, `missing_zalo`, `ambiguous_zalo`, `deduped`, `invalid_date`, `skipped_empty`) kèm cảnh báo preflight `YELLOW`; dựng miễn trừ Buổi 14 trực tiếp từ dòng BCNB (`exempt_targets`) không cần bịa tên nhóm Zalo; ghim `snapshot_id` xuyên suốt `/run-scan` (`409 SNAPSHOT_CHANGED`); hoãn an toàn khi tên nhóm Zalo có nhiều khung giờ; gắn `deadline_verification="PENDING"` (`⏳`) ở bước quét cho tới khi bước ghi BCNB đối chiếu xong.
- **Chặng 5.7 — Giao diện vận hành bám dữ liệu server (`static/js/jobs.js`, `static/js/state.js`, `templates/partials/dashboard.html`, `templates/partials/specs.html`, `api/job_routes.py`, `repositories/job_repository.py`)**:
  - Dựng lại bảng từ `weekly-summary` của server khi tải trang và sau khi job ghi kết thúc (F5 không mất dữ liệu quét chọn lọc), bảo toàn chế độ xem lịch sử khi bấm ⟳ hoặc polling, chặn bật lại modal/alert cho job đã kết thúc trước khi tải trang, khóa các nút hành động khi `currentJobActive`, bổ sung dải preflight/tiến độ thu gọn trên Tab chính, tách ô KPI `Nộp trễ` (`#count-late`), hiển thị issue `NOTE` màu trung tính và xóa các hàm CSV builder chết phía JS (**D6**).
- **Chặng 5.8 — Parser vòng đời lớp mở rộng (D3), Dọn luồng tuần tự chết (D6), AI Assist nguyên văn không lộ log (D5), Khớp báo cáo cùng ngày thiếu/sai ngày (D2) & Chốt an toàn LMS toàn hệ thống (D4)**:
  - Mở rộng `services/class_lifecycle.py` và `CLASS_LIFECYCLE.md` hỗ trợ `ROB4`, `SEMI`, `PRE`, `ARM`, `PT`, `JS`, `CS`, `G`, `S` (`B → A → I` trong cùng track, `fullmatch` token, bỏ qua hậu tố `HB`), tương thích đọc/ghi với `cohort_key` cũ của `JS`/`CS` và dừng fail-closed khi va chạm alias (**D3**).
  - Xóa thân luồng quét Zalo tuần tự chết trong `services/zalo_service.py` (`166` dòng) và chuyển các cổng invariant sang kiểm tra implementation song song (**D6**); xác nhận AI assist gửi nguyên văn tin nhắn mà không ghi nguyên văn vào log (**D5**).
  - Bổ sung nhánh khớp cùng ngày học có ghi chú QC mức `NOTE` (`ZALO_REPORT_NO_DATE`, `ZALO_REPORT_WRONG_DATE`) trong `services/zalo_target_matching.py` và `services/zalo_scan_evidence.py` (**D2**).
  - Ghi chú `LMS_MANUAL_CHECK` mức `NOTE` lên cột `Mô tả vấn đề (TE/TF/QC)` mà không làm dòng bị tính lỗi trong email, kèm chốt an toàn `is_lms_fetch_unavailable` tạm ngưng ghi note `LMS_MANUAL_CHECK` hàng loạt và bật cờ cảnh báo `lms_fetch_unavailable` khi LMS lỗi toàn hệ thống (**D4**).

---

## [2026-10-29] — Wave 4 (`4.0` → `4.4`): Kiểm Định Chất Lượng Nội Dung LMS/Zalo, Tự Động Ghi `Mô Tả Vấn Đề` Lên Teaching & Bảng Email Quản Lý (`DEC-036`, `DEC-038`)

### ✨ Tính năng mới & Nâng cấp toàn diện (`UPGRADE_PROGRESS.md` — `Wave 4: 25/25 ✅`)
- **Chặng 4.0 — Sao lưu toàn diện Pre-Wave 4 (`.backups/db/pre-wave4-20261029.sqlite3`, `.backups/pre-wave4-20261029/`)**:
  - Sao lưu nóng cơ sở dữ liệu SQLite kèm diễn tập khôi phục (`integrity="ok"`, `foreign_key_errors=()`, `restore_drill_ok=true`) và đóng gói bản chụp mã nguồn ZIP kèm `MANIFEST.json` & `ROLLBACK_INSTRUCTIONS.md`.
- **Chặng 4.1 — Đọc mở rộng bảng Teaching (`check_nhanxet.php`) & Đồng bộ trạng thái nộp LMS/Zalo (`services/bcnb_safety.py`, `services/zalo_discovery_service.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`)**:
  - Bổ sung `resolve_extended_bcnb_columns` trích xuất đầy đủ `bcnb_session_id`, `khoi`, `nx_zalo_checked`, `nx_lms_checked`, `lms_deadline`, `qc_issue_note`, `instructor_note`, `evaluation_status` từ bảng `check_nhanxet.php` trong cả pha Dò tìm (`ZALO_DISCOVERY`) và pha Quét (`ZALO_SCAN`).
  - Đồng bộ trạng thái nộp LMS (`✅ Đã nộp LMS` / `❌ Chưa nộp LMS`) và `Tình trạng đánh giá` (`⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo`, `✅ Đã hoàn thành nhận xét`, ...) vào `ReportRepository.get_weekly_summary` ngay cả khi tuần đó chưa chạy job LMS riêng, kèm bảo vệ chống rò rỉ dữ liệu giữa các buổi trùng mã lớp hoặc trùng `session_id`.
- **Chặng 4.2 — Động cơ kiểm định chất lượng nội dung LMS & Zalo (`services/lms_quality_service.py`, `services/external_gateways.py`, `services/zalo_scan_evidence.py`, `services/zalo_parallel_service.py`, `repositories/zalo_batch_repository.py`)**:
  - Kiểm tra **Nội dung bài học LMS (`slot.summary`)**: phát hiện ô trống hoặc chỉ ghi tiêu đề cụt lủn (`"B1"`, `"Bài 1"`, `"Buổi 1"`, `"Kiểm tra checkpoint 1"`, mô tả thực chất `< 15` ký tự) $\rightarrow$ `[LMS] Chưa ghi nội dung bài học`.
  - Phân biệt chuẩn **Buổi Thường** (đủ 3 ý: `Điểm mạnh`, `Điểm cần cải thiện`, `Lời khuyên`; cảnh báo viết sơ sài vài dòng hoặc copy-paste trùng lặp) và **Buổi Checkpoint** (`Coding` Buổi 5, 9; `Robotics` Buổi 4, 8 — đủ 3 trụ cột `KIẾN THỨC`, `KỸ NĂNG`, `THÁI ĐỘ`) cho học viên đi học (`ATTENDED`, `LATE_ARRIVED`) mà không bao giờ ghi tên thật học viên (bảo vệ PII).
  - Phát hiện lỗi giáo viên ghi sai ngày học, sai số buổi hoặc sai tên bài học trên Zalo đối chiếu với `slot.summary` của LMS; tích hợp `LmsQualityGateway` trên `ExternalGateways` (cô lập hoàn toàn trong `-Isolated`).
- **Chặng 4.3 — Tự động ghi vấn đề vào ô `Mô tả vấn đề (TE/TF/QC)` trên Teaching (`services/bcnb_safety.py`, `services/bcnb_actions.py`, `services/zalo_bcnb_service.py`)**:
  - Bổ sung `merge_checker_issue_note` và `split_checker_issue_note` bảo toàn nguyên vẹn ghi chú thủ công của TE/TF/QC và cập nhật phần `[Auto QC: ...]` idempotent 100%.
  - Bổ sung `execute_verified_checker_note_action` gửi `POST /check_nhanxet.php` (`action=update_note`, `note_type=checker`, `csrf_token`, `session_id`), kiểm chứng đọc ngược DOM sau hard reload (`verify_checker_note_persisted` $\rightarrow$ `VERIFIED`) ở chế độ `production` (hoặc mô phỏng `DRY_RUN` ở chế độ chạy thử) và lưu vết kiểm toán `BCNB_UPDATE_CHECKER_NOTE` vào `external_action_audit`.
- **Chặng 4.4 — Bảng Email Quản lý 9 cột (HTML Table + Clipboard Rich Table + Unicode Table), CSV lỗi chất lượng & Hiển thị Dashboard (`services/report_email_service.py`, `repositories/report_repository.py`, `templates/partials/dashboard.html`, `static/js/jobs.js`)**:
  - Nâng cấp email báo cáo lớp lỗi gửi Quản lý (`quangda@mindx.net.vn`, `tuanpa@mindx.com.vn`) thành bảng 9 cột (`STT`, `Mã lớp`, `Buổi`, `Ngày học`, `Giảng viên`, `NX Zalo`, `NX LMS`, `Tình trạng đánh giá`, `Mô tả vấn đề (TE/TF/QC)`) với cả bảng HTML có màu sắc trực quan (`html_table` / `html_body`), bảng khung viền Unicode (`text_table` / `text_body`) và tự động sao chép bảng Rich HTML vào Clipboard khi mở Gmail Web Compose (`copyFailedEmailReportToClipboard`).
  - Mở rộng `CSV_HEADERS` / `WEEKLY_CSV_HEADERS` lên 9 cột và cập nhật `is_failed_weekly_summary_row` để bao gồm cả các lớp đã nộp Zalo & LMS nhưng còn lỗi chất lượng nội dung (`issue_description` khác rỗng); hiển thị trực quan `LMS`, `Đánh giá`, `Vấn đề` và `Giải trình GV` ngay trên bảng kết quả Dashboard.
- **Hotfix Tải Dữ Liệu Buổi Học LMS Live, Tinh Chỉnh Báo Cáo Teaching & Mở Rộng Bảng Giám Sát Dashboard (`services/lms_quality_service.py`, `services/zalo_parallel_service.py`, `services/bcnb_safety.py`, `repositories/report_repository.py`, `static/js/jobs.js`, `templates/partials/dashboard.html`, `templates/partials/specs.html`, `static/css/app.css`)**:
  - Hỗ trợ tự động mở tab tạm `lms.mindx.edu.vn` (đóng an toàn trong `try / finally`), xác thực tên miền HTTPS chặt chẽ, bắt token Firebase từ request `POST https://lms-api.mindx.edu.vn/` kết hợp đọc IndexedDB `firebaseLocalStorageDb`, truy vấn gộp **Tier-1 Bulk Query** theo `centre_in` (`itemsPerPage: 100`, `filter_textSearch`) và nâng giới hạn độ dài nhận xét Checkpoint lên `15,000` ký tự (`47/47` lớp trên Job `#400` tải thành công `100%`, `0` lỗi tải).
  - Bỏ kiểm tra lỗi trùng lặp (`copy-paste trùng lặp`) đối với nhận xét học sinh trên LMS miễn đảm bảo đủ cấu trúc 3 phần (`Điểm mạnh`, `Điểm cần cải thiện`, `Lời khuyên` hoặc 3 trụ cột Checkpoint); gỡ tiền tố `[Auto QC: ...]` khi ghi vào cột `Mô tả vấn đề (TE/TF/QC)` trên Teaching (ghi trực tiếp `[LMS] ...; [Zalo] ...` kèm tương thích ngược để tự động gỡ bỏ `[Auto QC: ...]` và lỗi trùng lặp cũ trong lần quét tiếp theo).
  - Chuyển thanh trạng thái `#zalo-preflight` từ trang chính `Zalo Auto` sang Tab `Chẩn đoán & Nhật ký` (`specs.html`) và mở rộng chiều cao tự nhiên cho `.data-panel` / `#tab-dashboard .table-scroll` để hiển thị đầy đủ danh sách lớp trên bảng giám sát tiến độ.
  - Khắc phục lỗi `Bad Request Error 400` trên `mail.google.com` khi mở khung gửi mail báo cáo nhiều lớp lỗi bằng cách kiểm soát trực tiếp độ dài URL sau percent-encoding (`MAX_GMAIL_COMPOSE_URL_LEN = 5400` bytes) kết hợp tầng nén `compose_url_compact_body` và tự động copy bảng HTML 9 cột vào Clipboard; đồng thời tắt tự động tải file CSV khi nhấn nút **`Gửi Mail lớp lỗi`** (`exportAndEmailFailedClassesCsv`).
  - Chuẩn hóa câu chữ ngắn gọn trong nhận xét chất lượng và trạng thái Zalo trên Teaching, Dashboard, Email & CSV: `[LMS] Nhận xét thiếu ý`, `[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực`, `[Zalo] Đã báo cáo nhưng ghi sai ngày/buổi ( Bài số N )`, `[Zalo] Đã báo cáo nhưng ghi sai ngày ( ghi ngày DD/MM nhưng lịch là ngày DD/MM, buổi N)`, và `Chưa nhận xét` (thay cho `❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng`).
  - Xử lý triệt để miễn báo cáo Zalo cho các lớp **Buổi 14** (`services/zalo_evidence.py`, `services/zalo_parallel_service.py`, `services/lms_quality_service.py`, `repositories/report_repository.py`, `static/js/jobs.js`): áp dụng `apply_lesson_14_exemption` ngay từ `_scan_one_target` (`status = "✅ Miễn báo cáo (Buổi 14)"`, `status_detail = "Buổi 14 – miễn báo cáo theo quy tắc nghiệp vụ."`, `submitted_for_bcnb = True`, `validation_errors = []`), đồng thời chuẩn hóa tự động các bản ghi Buổi 14 cũ trên Dashboard, `weekly-summary`, CSV và Email để không bao giờ bị tính là lỗi hay hiển thị nút `Quét lại`.

---

## [2026-10-24] — Củng Cố 4 Chốt An Toàn Hậu Kiểm Sau Rollout Phase 3.2 (`DEC-036`)

### 🛡️ Sửa lỗi & Củng cố an toàn vận hành
- **Chặn lập kế hoạch LMS khi bảng phân trang đọc chưa đầy đủ (`services/lms_service.py`)**:
  - Chuyển `_wait_for_lms_table_settled` và `_read_lms_missing_classes` sang chế độ fail-closed (`raise LmsSafetyError`) khi bảng chưa ổn định, rỗng (`0` dòng), bị kẹt ở trang có chữ ký trùng hoặc chạm giới hạn `20` trang khi nút trang kế tiếp vẫn khả dụng.
- **Đối soát lịch sử duyệt tay xuyên suốt các lần quét trong tuần (`repositories/report_repository.py`)**:
  - Trong `ReportRepository.get_weekly_summary`, đối chiếu toàn bộ bản ghi `BCNB_MANUAL_APPROVE_TICK` (`mode='production'`, `outcome='VERIFIED'`) từ `external_action_audit` và các job quét cùng tuần theo đúng khóa `(week, year, class_code, lesson, class_date_iso)` để lần quét sau không ghi đè mất trạng thái đã duyệt tay từ lần quét trước; giữ kèm `snapshot_id` từng dòng và xóa `snapshot_id` cấp tổng hợp nếu ghép thêm dòng từ snapshot khác.
- **Cách ly nguồn `snapshot_id` khi xem lịch sử và quay về tuần hiện tại (`static/js/jobs.js`)**:
  - Bảo toàn đúng nguồn `snapshotId`/`periodKey` của dữ liệu `weekly-summary`, chặn nạp làm baseline quét chọn lọc khi thiếu hoặc lệch `snapshot_id` ở cấp bảng hoặc từng dòng, và ngăn phản hồi live-mode cũ đến trễ xóa nhầm bảng tuần lịch sử mới chọn.
- **Phân biệt chữ ký phân trang bằng thông tin học viên ở cả lớp ngoài mục tiêu (`services/lms_service.py`)**:
  - Đưa định danh học viên (`row_student_sig`) vào `page_signature` cho mọi dòng trong `_extract_students_from_table_for_classes`, ngăn dừng nhầm khi hai trang liên tiếp chứa học viên khác nhau của cùng một mã lớp ngoài yêu cầu.

---

## [2026-10-23] — Hoàn Tất Lộ Trình Nâng Cấp Toàn Diện Phase 0 → Phase 3.2 (`DEC-036`)

### ✨ Tính năng mới & Nâng cấp vận hành (`UPGRADE_PROGRESS.md` — `100%`)
- **Phase 1.1 — Copy danh sách nhắc giáo viên 1-Click (`static/js/jobs.js`, `static/js/lms.js`, `templates/partials/dashboard.html`, `templates/partials/lms_modal.html`)**:
  - Tự động tổng hợp các lớp chưa nộp báo cáo Zalo (`❌`/`⚠️`) hoặc còn nợ nhận xét LMS gom theo từng Giáo viên phụ trách (`NguoiPhuTrach`), khóa chặt theo tuần kiểm tra hiện tại (`effectiveDashboardPeriod`) và sao chép vào Clipboard chỉ với 1 cú nhấp chuột.
- **Phase 1.2 — Quét lại có chọn lọc các lớp lỗi/nộp bù (`api/zalo_routes.py`, `services/zalo_service.py`, `services/bcnb_service.py`, `static/js/jobs.js`)**:
  - Hỗ trợ chọn từng dòng trên bảng kết quả Zalo hoặc bấm nút **"🔄 Quét lại N lớp lỗi"** để chỉ quét lại tập `selected_target_keys`, sau đó hợp nhất kết quả vào bảng hiện tại trong cùng `(periodKey, snapshotId)`.
- **Phase 1.3 — Duyệt tay & Tích BCNB 1-Click có lưu vết kiểm toán (`api/zalo_routes.py`, `services/zalo_service.py`, `services/job_dispatcher.py`)**:
  - Bổ sung endpoint `POST /api/zalo-manual-approve` và job `ZALO_MANUAL_APPROVE` cho phép duyệt tay các ca báo cáo hợp lệ nhưng khác mẫu chuẩn, tự động mở BCNB tích chọn có kiểm chứng đọc ngược và lưu vết kiểm toán `BCNB_MANUAL_APPROVE_TICK` kèm mã băm SHA-256 của ảnh minh chứng thuộc đúng `source_job_id`.
- **Phase 2.1 — Bảng Xem trước LMS chi tiết buổi nợ & Chọn từng lớp (`services/lms_plan.py`, `services/lms_service.py`, `repositories/lms_repository.py`, `static/js/lms.js`)**:
  - Hiển thị cụ thể các buổi quá hạn nhận xét (`Buổi ...`) cùng trạng thái checkbox BCNB trên bảng Xem trước LMS; cho phép chọn/bỏ chọn từng lớp trước khi xác nhận thực thi với mã băm toàn vẹn `selection_digest`.
- **Phase 2.2 — Lấy danh sách học viên trực tiếp từ LMS cho Google Form (`services/lms_service.py`, `api/googleform_routes.py`, `static/js/googleform.js`)**:
  - Bổ sung job `GOOGLEFORM_LMS_STUDENTS` và `POST /api/googleform/fetch-lms-students` tự động trích xuất danh sách học viên từ LMS theo danh sách mã lớp (kiểm tra khớp duy nhất xuyên trang, dừng fail-closed nếu trùng tên học viên trong cùng lớp, bảo toàn khóa chống gửi trùng `idempotency_key` và ẩn tên học viên khỏi nhật ký/tên file ảnh).
- **Phase 3.1 — Bộ phân loại Báo cáo Zalo Hybrid Rule-based + AI gợi ý (`classifiers/rule_classifier.py`, `services/external_gateways.py`, `services/zalo_scan_evidence.py`)**:
  - Bổ sung tùy chọn `ZALO_AI_ASSIST_CLASSIFIER=false` (mặc định tắt) để gọi AI chấm phụ tối đa 3 tin nhắn dài (`>= 80` ký tự) trong khung ngày học khi bị trượt Rule-based; giữ nguyên tắc fail-closed tuyệt đối (`report_found=False`, `submitted_for_bcnb=False`, nhãn `⚠️ AI gợi ý có thể là báo cáo – Cần kiểm tra thủ công`).
- **Phase 3.2 — Tra cứu Lịch sử theo Tuần & Xuất báo cáo CSV/Excel (`repositories/report_repository.py`, `api/zalo_routes.py`, `static/js/jobs.js`, `templates/partials/dashboard.html`)**:
  - Bổ sung `GET /api/reports/weekly-summary` tổng hợp kết quả Zalo + LMS theo tuần/job (hợp nhất selective rescan trong cùng `snapshot_id`, kết hợp vết duyệt tay `BCNB_MANUAL_APPROVE_TICK`, tách bạch `dry_run` với `production`, bảo toàn danh sách tuần lịch sử qua `>200` job) và nút **"📥 Xuất CSV Tuần"** định dạng UTF-8 có BOM (`\uFEFF`) kèm chống CSV Formula Injection.

---

## [2026-10-20] — Tự Động Quét Web LMS MindX, Tinh Gọn Giao Diện & Chống Nhận Diện Nhầm Báo Cáo Khai Giảng

### ✨ Tính năng mới & Nâng cấp (`DEC-034`, `DEC-035`)
- **Tự động quét trực tiếp trang quản trị MindX LMS (`services/lms_service.py`, `services/lms_plan.py`, `api/lms_routes.py`)**:
  - Thay thế quy trình tải file CSV thủ công bằng cơ chế worker tự mở trang `https://lms.mindx.edu.vn/admin/class-sessions?...`, tự động lọc các lớp có dữ liệu tại cột **"Buổi học gần nhất quá hạn nhận xét"** (`Last Session Due Comments`) để xác định danh sách lớp quá hạn.
  - Đối chiếu danh sách lớp trên trang BCNB (`check_nhanxet.php`): lớp không nằm trong danh sách quá hạn trên LMS được lập kế hoạch tích chọn (`tick`), lớp còn buổi quá hạn được giữ trống hoặc bỏ tích (`untick` khi bật tùy chọn).
  - Bổ sung cơ chế tự động đăng nhập SSO (`id.mindx.edu.vn` / `base.mindx.edu.vn/login`) và tự động đăng nhập BCNB bằng thông tin cấu hình trong `.env` (`LMS_USERNAME`, `LMS_PASSWORD`, `LMS_URL`, `BCNB_USERNAME`, `BCNB_PASSWORD`).
  - Vẫn duy trì API xem trước từ dữ liệu CSV (`POST /api/lms-preview`) để tương thích ngược và phục vụ kiểm thử độc lập.
- **Tinh gọn Trang chính & Tách tab Thông số kỹ thuật (`templates/partials/dashboard.html`, `templates/partials/specs.html`)**:
  - Trước khi thiết kế lại giao diện, thiết lập bản sao lưu toàn diện (`.backups/pre-ui-redesign-20261016/`, snapshot SQLite `pre-ui-redesign-20261016-025713.sqlite3` và Git tag `checkpoint-pre-ui-redesign-20261016`).
  - Rút gọn trang chính **Zalo Auto** chỉ giữ lại các điều khiển vận hành thiết yếu hàng ngày (Cấu hình tuần quét, Khám phá lớp mới, Hộp kiểm Chạy thử, Chốt an toàn trước khi chạy, Nhật ký trực tiếp và Bảng kết quả).
  - Chuyển toàn bộ bảng chẩn đoán kỹ thuật, quản lý phiên trình duyệt, sao lưu SQLite và nhật ký kiểm toán sang tab thứ 5 độc lập trên thanh điều hướng: **Thông số kỹ thuật**.

### 🐛 Sửa lỗi & Củng cố độ ổn định
- **Chờ bảng dữ liệu LMS ổn định khi chuyển trang (`_wait_for_lms_table_settled`)**:
  - Khắc phục hiện tượng quét thiếu lớp do React/Ant Design render bảng bất đồng bộ: chờ vòng quay tải dữ liệu (`.ant-spin-spinning`) biến mất, kiểm tra số dòng ổn định liên tiếp tối thiểu `1.2s` và xác thực nội dung bảng đã chuyển sang trang mới trước khi đọc từng trang phân trang.
- **Loại trừ thông báo khai giảng của Quản lý lớp học khỏi bộ phân loại báo cáo Zalo (`classifiers/rule_classifier.py`)**:
  - Khắc phục lỗi nhận diện nhầm (false positive) ở các lớp mới khai giảng (như `VT-C4K-GI35`, `VT-C4K-GA41`) khi tin nhắn `📣 THÔNG BÁO KHAI GIẢNG...` của `Mindx Vũng Tàu Quản Lí Lớp Học` có chứa cụm `Nội dung học phần` và ngày khai giảng trùng tuần kiểm tra.
  - Áp dụng điểm phạt loại trừ tuyệt đối (`penalty:class_manager_or_opening_notice`) cho tin nhắn từ Quản lý lớp học / Thông báo khai giảng, đồng thời bắt buộc tin nhắn phải có ít nhất một mục nội dung báo cáo thực chất của giáo viên (`has_report_substance`).
  - Bổ sung kiểm thử hồi quy `test_class_manager_opening_announcement_is_not_classified_as_report` trong `tests/test_rule_classifier_dates.py`.

---

## [2026-10-15] — Đồng Bộ Quy Trình Điều Phối & Cập Nhật Kiểm Thử (`ab3ad0b`)

### 🔧 Cải tiến kỹ thuật
- Đồng bộ cấu hình quy trình làm việc (`HANDOFF.md`, `.ai-workflow/V10_CANONICAL_SPEC.md`, `.ai-workflow/PROJECT_PROFILE.json`) tách biệt môi trường công cụ điều phối (`.venv`) với môi trường chạy ứng dụng (`venv`).
- Cập nhật giao diện bảng điều khiển (`templates/partials/dashboard.html`, `static/js/dashboard.js`, `static/css/style.css`) và mở rộng kiểm thử hợp đồng giao diện (`tests/test_frontend_contracts.py`, `tests/test_route_contracts.py`).

---

## [2026-09-26] — Hệ Thống Kiểm Tra An Toàn Trước Khi Quét (Preflight Gate) & Khóa Nhận Job Nguyên Tử (`12c465c`)

### ✨ Tính năng mới & An toàn vận hành (`DEC-030` – `DEC-033`)
- **Hệ thống Zalo Scan Preflight (`services/zalo_preflight.py`, `GET /api/zalo-scan-preflight`)**:
  - Kiểm tra và phân loại trạng thái sẵn sàng trước khi quét theo 3 mức: `GREEN` (Sẵn sàng), `YELLOW` (Chế độ chạy thử / xem trước) và `RED` (Chặn chạy thật nếu thiếu điều kiện an toàn như trình duyệt chưa sẵn sàng, đang có job chạy hoặc bật song song không an toàn).
  - Yêu cầu xác nhận rõ ràng (`acknowledge_live_mutation=true`) trước khi cho phép `POST /run-scan` ghi tích chọn thật lên BCNB.
- **Chốt nhận job nguyên tử chống bấm đúp cạnh tranh (`repositories/jobs_repo.py`, `services/job_dispatcher.py`)**:
  - Sử dụng giao dịch SQLite `BEGIN IMMEDIATE` với cờ `reject_if_active=True` để đảm bảo tại một thời điểm chỉ có duy nhất một job `zalo_scan` hoặc `zalo_discovery` được nhận chạy.
  - Giới hạn `ZALO_PARALLEL_MAX_WORKERS=1` ở chế độ chạy thật trên cùng một hồ sơ trình duyệt (`PersistentBrowserSession`) để tránh xung đột tab Zalo Web.
- **Trải nghiệm người dùng & Thông báo lỗi tiếng Việt (`templates/partials/dashboard.html`, `services/discovery_service.py`)**:
  - Tổ chức lại bảng điều khiển theo quy trình 3 bước trực quan; đưa khung kiểm tra an toàn lên ngay phía trên nút **Bắt đầu quét**.
  - Bổ sung ngoại lệ `DiscoverySafetyError` trả về hướng dẫn khắc phục bằng tiếng Việt rõ ràng khi BCNB yêu cầu đăng nhập lại hoặc cấu trúc bảng thay đổi.

---

## [2026-09-18 – 2026-09-22] — Đóng Gói Mốc Mã Nguồn Sạch & Sửa Lỗi Tìm Nhóm Zalo (`624143f`, `f58922c`)

### 🐛 Sửa lỗi & Kiểm chứng (`DEC-028`, `DEC-029`)
- **Khắc phục lỗi timeout ô tìm kiếm Zalo Web (`ZALO-SEARCH-TIMEOUT-001`, `2026-09-22`)**:
  - Cập nhật bộ chọn (selector) và cơ chế chờ hộp tìm kiếm nhóm trên Zalo Web (`services/zalo_service.py`), kiểm chứng thành công bằng canary chỉ-đọc trên lớp `VT-JSB16`.
- **Chuẩn hóa tài liệu & Đóng gói mốc mã nguồn sạch (`2026-09-18`)**:
  - Chuẩn hóa toàn bộ tài liệu kiến trúc và vận hành (`AGENTS.md`, `PROJECT_SUMMARY.md`, `PROJECT_STATE.md`, `PROJECT_DECISIONS.md`, `PROJECT_COMPLETION_PLAN.md`, `CLASS_LIFECYCLE.md`, `VERSION.md`).
  - Xác lập điểm mốc ổn định với **72 file Python**, **43 API route**, **19 bảng SQLite**, **6 loại job** và **525 bài kiểm thử tự động** vượt qua 100% ở cả hai chế độ tiêu chuẩn và cô lập (`-Isolated`).

---

## [2026-09-04 – 2026-09-05] — Củng Cố Quản Lý Trình Duyệt & Cơ Chế Canary Chỉ-Đọc (`DEC-006` – `DEC-023`)

### 🛡️ Củng cố hạ tầng trình duyệt
- **Quản lý tập trang trình duyệt an toàn (`services/browser_session.py`)**:
  - Kiểm soát chặt chẽ danh sách trang (`page-set`) trong `PersistentBrowserSession`, tự động thu hồi các tab rò rỉ hoặc đóng phiên theo nguyên tắc fail-closed khi phát hiện trạng thái bất thường.
  - Đóng vĩnh viễn các đường dẫn dò tìm (probe routes) cũ không còn an toàn với mã phản hồi HTTP `410 Gone`.
- **Kiểm chứng sẵn sàng từng trang (`Sealed Readiness`) & Canary chỉ-đọc**:
  - Tích hợp kiểm tra trạng thái đăng nhập và DOM sẵn sàng riêng biệt cho Zalo Web và BCNB.
  - Xây dựng cơ chế kiểm thử thăm dò một mục tiêu (single-target read-only canary) được kiểm chứng trên các lớp mẫu (`ARMB19`, `GA38`) mà không làm thay đổi dữ liệu trên hệ thống đích.

---

## [2026-08-27] — Phát Hành `v4.0.0`: Hoàn Thiện Kiến Trúc 9 Giai Đoạn (Phase 0 – Phase 9B)

Giai đoạn nâng cấp toàn diện biến ứng dụng từ công cụ chạy kịch bản đơn lẻ thành hệ thống có kiến trúc phân tầng, kiểm toán đầy đủ và kiểm thử tự động khép kín:

### 🔹 Phase 0 — Baseline, Xác Thực Ngày Gửi Zalo & Đồng Bộ Lịch Sử (`2026-07-29`, `4077a3f` – `ec8b50e`)
- Thiết lập cổng kiểm tra chất lượng `CHECK_QUALITY.ps1` và kho dữ liệu mẫu tổng hợp (synthetic fixtures).
- **Xác thực ngày gửi từ giao diện Zalo (`services/zalo_service.py`)**: Đối chiếu ngày gửi thực tế trên thanh tiêu đề ngày của Zalo Web thay vì chỉ tin vào văn bản do người gửi tự gõ (`0dc00b5`, `3e6ec3e`).
- **Fail-closed khi tên nhóm không khớp (`c654199`)**: Ngăn chặn việc đọc nhầm tin nhắn từ nhóm có tên gần giống.
- **Miễn báo cáo buổi 14 theo múi giờ nghiệp vụ (`2a64d24`)**: Tự động nhận diện các lớp ở buổi 14 (buổi tổng kết/thi cuối khóa) để áp dụng quy tắc miễn báo cáo hợp lệ.
- **Kiểm chứng đồng bộ lịch sử tin nhắn (`ensure_zalo_history_synced`, `ec8b50e`)**: Đảm bảo Zalo Web đã tải xong tin nhắn mới nhất trước khi quét.

### 🔹 Phase 1 & Phase 2 — Điều Phối Job Bền Vững & Kiểm Toán Tác Vụ Ngoài
- Xây dựng `JobDispatcher` (`services/job_dispatcher.py`) hỗ trợ theo dõi nhịp sống (`heartbeat`), hủy tác vụ chủ động (`cooperative cancel`), thu hồi phiên treo có giới hạn và lưu vết sự kiện vào bảng `job_events`.
- Quản lý vòng đời tệp tải lên qua bảng `job_uploads` và ghi nhận mọi thao tác tương tác hệ thống ngoài vào bảng kiểm toán `external_action_audit`.

### 🔹 Phase 3 & Phase 4 — Kiến Trúc `AppContainer` & Ranh Giới Gateway Cô Lập
- Chuẩn hóa hàm khởi tạo `main.create_app()` và `AppContainer` (`core/container.py`), loại bỏ trạng thái toàn cục rời rạc.
- Tách biệt hoàn toàn giao tiếp với Playwright và Gemini AI qua lớp cổng `ExternalGateways` (`core/gateways.py`), cho phép chạy toàn bộ bộ kiểm thử trong môi trường cô lập hoàn toàn không cần mạng (`CHECK_QUALITY.ps1 -Isolated`).

### 🔹 Phase 5 & Phase 6 — Khóa Hồ Sơ Liên Tiến Trình, Vòng Đời Lớp & Kho Bằng Chứng Ảnh
- Triển khai `InterProcessFileLock` (`core/process_lock.py`) ngăn chặn nhiều tiến trình mở cùng một thư mục hồ sơ Chromium (`chrome_profile/`).
- Chuẩn hóa hợp đồng nghiệp vụ vòng đời lớp (`CLASS_LIFECYCLE.md`, `services/class_lifecycle_service.py`, bảng `class_cohorts` và `discovery_snapshots` hai tuần bất biến).
- Xây dựng dịch vụ lưu trữ ảnh bằng chứng (`services/evidence_service.py`) kèm mã băm toàn vẹn SHA-256, chính sách lưu giữ 7 ngày và nhật ký truy cập `evidence_access_log`.

### 🔹 Phase 7 & Phase 8 — Sao Lưu/Khôi Phục SQLite & Chống Gửi Trùng Google Form
- Cung cấp API sao lưu nóng cơ sở dữ liệu SQLite kèm kiểm tra diễn tập khôi phục tự động (`restore drill` trong `services/backup_service.py`) và kiểm tra toàn vẹn `schema_migrations`.
- Bổ sung bảng khóa `googleform_idempotency` ngăn chặn gửi trùng nhận xét của cùng một học viên trên Google Form, đồng thời tự động ghép ảnh chụp màn hình Trang 1 + Trang 3 làm bằng chứng hoàn thành.

### 🔹 Phase 9A & Phase 9B — Phân Quyền Web, Production Readiness & Quét Song Song Có Kiểm Soát
- Tích hợp phân quyền theo vai trò (`Viewer`, `Operator`, `Admin`), bảo vệ CSRF, kiểm tra Host/Origin và bộ đánh giá mức độ sẵn sàng vận hành (`core/production_readiness.py`).
- Xây dựng cơ chế tích chọn BCNB có xác thực đọc ngược từ máy chủ (`execute_verified_checkbox_action`) và dịch vụ quét Zalo song song có kiểm soát (`services/zalo_parallel_service.py`).

---

## [Trước 07/2026] — Giai Đoạn Khởi Tạo (`v1.0` – `v3.0`)

- **v1.0 (Prototype)**: Xây dựng kịch bản Playwright quét tin nhắn Zalo Web bằng hồ sơ trình duyệt lưu sẵn (`Persistent Context`) và bộ phân loại báo cáo sau buổi học dựa trên quy tắc từ khóa (`classifiers/rule_classifier.py`).
- **v2.0 (Web Dashboard & SQLite)**: Đóng gói thành ứng dụng web FastAPI + SQLite (`database.db`), bổ sung giao diện quản lý danh sách nhóm lớp (`groups`, `ignored_groups`), lưu trữ lịch sử báo cáo và tự động khám phá lớp mới từ BCNB (`DiscoveryService`).
- **v3.0 (Đa luồng nghiệp vụ & Tích hợp AI)**:
  - Mở rộng thành 3 phân hệ: **Zalo + BCNB**, **Google Form Auto-Submit** (`googleform_autosubmit/` sử dụng Gemini Vision OCR để trích xuất danh sách học viên từ ảnh chụp LMS và tự động điền biểu mẫu nhận xét), và **Tự động Tick LMS** phiên bản đầu tiên dựa trên đối chiếu tệp CSV.
