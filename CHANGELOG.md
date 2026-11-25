# CHANGELOG — Lịch Sử Phát Triển Dự Án Zalo Automation (MindX Auto)

Tài liệu này ghi lại toàn bộ quá trình hình thành, phát triển kiến trúc, nâng cấp tính năng và củng cố an toàn vận hành của hệ thống **Zalo Automation / MindX Auto** theo trình tự thời gian (từ mới nhất đến giai đoạn khởi tạo).

---

## [2026-11-25] — Tinh gọn Dashboard & Sửa bố cục bảng giám sát (`#tab-dashboard`)

- **Phân loại luồng**: `[FAST-TRACK]` (`DEC-047`) — chỉ thay đổi giao diện HTML/CSS/JS hiển thị trên Dashboard và test contract frontend; không chạm luồng quét/ghi BCNB, LMS, DB schema hay PII.
- **Thay đổi giao diện & logic hiển thị**:
  - `templates/partials/dashboard.html`: Đóng gói thanh tìm kiếm/chọn tuần/phiên, dải preflight `#dashboard-compact-strip` và hàng đợi duyệt tay `#zalo-review-queue` vào vùng thu gọn mặc định `#dashboard-advanced-panel` (`hidden`), thêm nút bật/tắt `#btn-toggle-dashboard-advanced` (`Bộ lọc`) trên thanh tiêu đề bảng, thêm `<colgroup>` (`col-group`, `col-person`, `col-time`, `col-status`, `col-evidence`) trên `.dashboard-results-table` và chỉnh padding tiêu đề/khoảng cách tab gọn hơn.
  - `static/css/app.css`: Khóa tỷ lệ cột cố định `22% / 14% / 11% / 36% / 17%` (`table-layout: fixed; min-width: 960px`) để cột `Trạng thái báo cáo & Chất lượng` rộng rãi dễ đọc, gỡ `max-width: 340px` trên `.dashboard-results-table .status-detail`, thêm `.status-cell--late`, tách màu mặc định `.status-detail:not([class*="text-"])` tránh đè màu `text-rose-700`/`text-amber-700`, căn lề trên theo dòng và thu gọn nhẹ `.metric-card`.
  - `static/js/jobs.js`: Bổ sung `toggleDashboardAdvancedPanel()` và `formatDashboardScanTimestamp()` quy đổi chuỗi ISO UTC sang giờ Việt Nam (`HH:mm` + `dd/MM/yyyy`, giữ nguyên ISO gốc ở thuộc tính `title` và fallback `"Hôm nay"`), đồng thời căn chỉnh padding ô bảng kết quả.
  - `tests/check_template_js.js`: Bổ sung kiểm tra hợp đồng ID `#dashboard-advanced-panel`, `#btn-toggle-dashboard-advanced`, `dashboard-results-table` / `<colgroup>`, `toggleDashboardAdvancedPanel()` và `formatDashboardScanTimestamp()`.
- **Bằng chứng kiểm chứng (`[FAST-TRACK]`)**:
  - `node tests/check_template_js.js`: `template-js-contract-ok scripts=8 handlers=49 behavior=14` (`exit 0`).
  - `venv\Scripts\python.exe -m tests.check_app_smoke`: `phase3-app-smoke-ok` (`exit 0`).
  - `venv\Scripts\python.exe tests\check_phase3_quality.py`: `phase3-quality-gate-ok` (`exit 0`).
  - `git diff --check`: `exit 0` (`graphify.exe` bị OS Application Control chặn trên shell máy chính).

---

## [2026-11-25] — Chuẩn hóa Báo cáo Quản lý 8 cột (`1A + 2A + 3A`), chống trùng `[SPCK] Kiểm tra tay trên LMS` & đổi nhãn `[LMS] Chưa có nội dung bài` (`DEC-056`)

- **Phân loại luồng & Review**: `[FULL GATE]` (`DEC-047`, `DEC-054`) · Review độc lập Read-Only `Neon XHigh` (`gemini-4-neon-xhigh`, `@verifier`: `#2` `NEEDS_FIX` 1 `[P0]` + 4 `[P2]` → `#4` `NEEDS_FIX` 1 `[P1]` ở `jobs.js` → **`#6` `VERDICT: PASS`**, `0` P0/P1/P2).
- **Thay đổi Logic & Báo cáo (1A, 2A, 3A, 4)**:
  - **Phương án 1A (`NX LMS`) — `services/report_status.py`, `services/report_email_service.py`, `static/js/jobs.js`**: Chuẩn hóa `resolve_lms_status` (kèm `TypeGuard[int]` `_is_non_negative_int`) và `resolveDashboardRowLmsStatus` hiển thị `"❌ Chưa nhận xét"` (0 HS) hoặc `"❌ Chưa nhận xét (thiếu k/N HS)"` khi thiếu nhận xét `≥ 1` HS đi học; tô chữ đỏ đậm (`#dc2626` bọc `<span>` tương thích Webmail) cho cột `NX LMS` khi bắt đầu bằng `❌` và gạch đầu dòng `[LMS] Chưa NX|nhận xét|điểm danh|Chưa có nhận xét|Thiếu nhận xét` trong Email HTML; tô `text-rose-700` dòng `LMS:` trên Dashboard.
  - **Phương án 2A (Email & CSV 8 cột) — `services/report_email_service.py`, `repositories/report_repository.py`**: Bỏ cột `Tình trạng đánh giá` khỏi `EMAIL_TABLE_HEADERS`, bảng HTML, bảng Unicode, `detail_lines`, `CSV_HEADERS` và `build_weekly_summary_csv` (dọn dead code `eval_status` & import thừa `NOT_FOUND_VERIFIED` trong CSV builder), giữ nguyên `_resolve_row_evaluation_status` / `evaluation_status` cho Dashboard và API nội bộ.
  - **Phương án 3A (Tách bảng SPCK & Chống trùng `Kiểm tra tay`) — `services/report_status.py`, `services/lms_quality_service.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`**:
    - Thêm `is_spck_only_warning_row` để tách các buổi đã xong NX Zalo & LMS nhưng chỉ thiếu/chưa NX SPCK sang **Bảng 2** cảnh báo vàng (`#b45309`) trong Email Quản lý, không cộng vào `nx_failed_count` ở tiêu đề và thống kê đầu thư (hỗ trợ cả ca 0 lỗi NX + có SPCK-only).
    - Trong `has_spck_missing_fail` / `has_spck_comment_missing_fail` (Python & JS), giữ khóa fail-closed trả `False` khi `lms_attendance.status` là `UNVERIFIED`/`EXEMPT`/`MISSING_ATTENDANCE`, nhưng vẫn kiểm tra `lms_quality_issues` / `quality_issues` khi điểm danh `VERIFIED` và `lms_spck.status == "UNVERIFIED"`.
    - Nâng trần `fetchSessionWorks` (`MAX_WORK_REQUESTS=240`, `workDeadline=max(45000, timeoutMs*4)`) kèm 1 lần retry `250ms` cho 5xx/429/network/abort; chặn ghép `[SPCK] Kiểm tra tay trên LMS` khi đã có lỗi SPCK xác minh (vẫn giữ `[LMS] Kiểm tra tay trên LMS` D4 và bảo toàn `[LMS] SPCK cần kiểm tra thủ công` độc lập).
  - **Đổi nhãn `[LMS] Chưa có nội dung bài`**: Sinh nhãn mới ở `evaluate_lms_slot_summary`, mở rộng nhận diện/khử trùng ở `report_status.py`, `zalo_bcnb_service.py`, `report_repository.py`, `report_email_service.py`, `jobs.js` và giữ nguyên `services/bcnb_safety.py` bảo toàn provenance `external_action_audit`.
- **Kiểm thử & Tệp thay đổi (15 code/test files + 3 docs)**:
  - `services/{report_status,lms_quality_service,zalo_bcnb_service,report_email_service}.py`, `repositories/report_repository.py`, `static/js/jobs.js`, `tests/{check_template_js.js,test_report_status.py,test_report_issue_display.py,test_lms_quality_service.py,test_api_routes_unit.py,test_attendance_reporting.py,test_spck_session_fetch.py,test_bcnb_actions.py,test_today_ops_service.py}`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`, `CHANGELOG.md`.
- **Bằng chứng kiểm chứng (`[FULL GATE]`)**:
  - Bộ kiểm chứng cô lập (`ZALO_QUALITY_ISOLATED=1`): **`791/791`** regression tests `OK` (`282.114s`), branch coverage **`80.4%`**, Ruff `0` lỗi, `PHASE 9A QUALITY PASS` (`secrets=0`), Phase 2–9B gates `PASS`, `phase3-app-smoke-ok`, `compileall` `exit 0`.
  - `node tests/check_template_js.js`: `template-js-contract-ok scripts=8 handlers=48 behavior=14` (`exit 0`).
  - Review độc lập Read-Only `Neon XHigh` (`@verifier`, `#6`): **`VERDICT: PASS`**, xác nhận `mypy` `0` lỗi trên `80` files (`ruff`/`mypy`/`check_template_js.js`/`git diff --check` đều exit `0`). Ghi nhận giới hạn OS trên shell máy chính: Windows Application Control chặn DLL `mypy` và `graphify.exe`.

---

## [2026-11-22] — Nâng cấp `Lớp Hôm Nay (Live)`: Mặc định `Tuần thực tế`, Bỏ qua lớp đã tick đủ & Tick-only BCNB + Lưu từng dòng (`Wave 8`, `DEC-055`)

- **Phân loại luồng & Review**: `[FULL GATE]` (`DEC-047`, `DEC-054`) · Pre-Wave 8 SQLite hot backup + restore drill (`5d16848` -> `.backups/db/pre-wave8-20261122-080139.sqlite3`) · Review độc lập Read-Only `Neon XHigh` (`gemini-4-neon-xhigh`, `@verifier`, `#11` `NEEDS_FIX` -> sửa đủ 4 findings & thêm 4 regression tests `#14` -> **`#16` `VERDICT: PASS`**, `[P0]=0, [P1]=0, [P2]=0`).
- **Thay đổi Backend & BCNB**:
  - `services/zalo_bcnb_service.py`: Bổ sung `defer_all_new_checker_notes: bool = False` vào `update_bcnb_reports` (giữ mặc định `False` để `ZALO_SCAN` nguyên vẹn); trong `_maybe_update_row_checker_note`, khi bật cờ này thì coi cả `zalo_expired=False` & `lms_expired=False` bất kể deadline (`DEFERRED_FOR_FULL_SCAN`), đồng thời thêm `_deferred_issue_family_keys` bảo toàn ghi chú auto đang mở trên BCNB khi thay đổi số đếm (`(3/5)->(2/5)`), cụm câu phụ Mức A hoặc tương đương legacy<->Mức A (0 POST, không xóa nhầm lỗi chưa sửa), vẫn xóa lỗi auto cũ đã khắc phục và bảo toàn ghi chú tay QC từng byte.
  - `services/today_ops_service.py`:
    - Bật `defer_all_new_checker_notes=True` cùng `defer_unexpired_checker_notes=True` trong `_run_bcnb_sync_on_session`, claim sẵn dòng ghi qua `ZaloBatchRepository.claim_write` và nối `on_row_finalized` để lưu `update_target_result` + `complete_write` ngay sau từng dòng, theo dõi `completed_claimed_ids` chống double-complete, và chuẩn hóa status qua `_ALLOWED_WRITE_FINAL_STATUSES` / `_normalize_write_final_status` (loại `"SKIPPED"` khỏi `complete_write` theo `ZaloBatchRepository.WRITE_STATUSES`, map về `"FAILED_BEFORE_WRITE"`).
    - Bổ sung `parse_today_scope` (`"date"` | `"current_week"`), mở rộng `_collect_today_entries_from_snapshot` lấy toàn bộ buổi thuộc `latest_week` giữ nguyên `class_date_iso` riêng từng buổi, khớp bảng BCNB ưu tiên `(kt, bcnb_session_id)` với fallback `kt` duy nhất không xung đột session ID, sắp xếp `_timeline_sort_key` theo `(class_date_iso, start_hm, end_hm, class_code, lesson_num)`.
    - Mở rộng `_load_persisted_today_overlays` cô lập banner `TODAY_REALTIME_SYNC` theo `scope`, lọc kết quả `(week, year)` và bỏ qua item `skipped_already_completed=True`; cập nhật `_merge_entry_with_overlay(..., snapshot_observed_at=observed_dt)` bảo toàn `nx_zalo_checked`/`nx_lms_checked`/`qc_issue_note`/`instructor_note` của snapshot mới hơn trên cả `merged` & `scan_target` mà vẫn giữ ảnh/bằng chứng của overlay cũ hơn.
    - Mở rộng `build_today_overview` (`scope`, `scope_week`, `scope_label`, `bcnb_completed_both`, `skipped_already_completed`, `completed_both_count`) và `run_today_realtime_sync` (`scope`, `_is_entry_bcnb_completed`, `skip_completed_bcnb` bỏ qua lớp tick đủ mà không ghi placeholder đè bằng chứng cũ, vẫn bị override bởi `selected_target_keys`, kết thúc `SUCCESS` sạch không mở trình duyệt khi toàn bộ lớp đã tick đủ).
  - `api/today_routes.py`: Bổ sung `scope` trên `GET /api/today/overview` (bỏ trống giữ nguyên hành vi `"date"` tương thích ngược) và `scope` + `skip_completed_bcnb` trên `POST /api/today/sync` kèm kiểm tra HTTP 400 `INVALID_TODAY_SCOPE`.
- **Thay đổi Frontend & Hợp đồng JS**:
  - `templates/partials/today.html`: Thêm nút chuyển phạm vi `#today-scope-week-btn` / `#today-scope-date-btn`, checkbox `#today-skip-completed-checkbox`, nhãn động `#today-kpi-total-label` & `#today-table-title`.
  - `static/js/today.js`: Mặc định `todayActiveScope='current_week'` và `todaySkipCompletedBcnb=true`, hiển thị thứ + ngày `dd/mm` đa ngày trên timeline & thẻ SPCK, gắn huy hiệu `✅ Đã tick đủ BCNB`, truyền `scope` + `skip_completed_bcnb` khi gọi API và cập nhật `copyTodayReminders`.
- **Kiểm thử & Tài liệu**:
  - `tests/test_today_ops_service.py`, `tests/test_today_routes.py`, `tests/check_template_js.js`: Bổ sung unit test cho `defer_all_new_checker_notes`, bảo toàn họ lỗi `(3/5)->(2/5)` / SPCK / composite / legacy<->Mức A, lưu `complete_write` từng dòng & non-SKIPPED status, `current_week` đa ngày, giữ bằng chứng cũ khi `skip_completed_bcnb=True`, khớp `(kt, bcnb_session_id)`, ưu tiên snapshot mới hơn overlay cũ, API `INVALID_TODAY_SCOPE` và hợp đồng JS.
  - `UPGRADE_PROGRESS.md` (Wave 8 `19/19` ✅), `PROJECT_DECISIONS.md` (`DEC-055`), `PROJECT_STATE.md`, `CHANGELOG.md`.
- **Tệp thay đổi (12 files)**: `api/today_routes.py`, `services/{today_ops_service,zalo_bcnb_service}.py`, `templates/partials/today.html`, `static/js/today.js`, `tests/{check_template_js.js,test_today_ops_service.py,test_today_routes.py}`, `UPGRADE_PROGRESS.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`, `CHANGELOG.md`.
- **Bằng chứng kiểm chứng (`[FULL GATE]`)**:
  - Targeted suites (`tests.test_today_ops_service`, `tests.test_today_routes`, `tests.test_bcnb_actions`, `tests.test_report_status`): `77/77 OK` (`11.011s` ở `@backend` `#14`; `21.525s` ở `@verifier` `#16`).
  - `powershell -ExecutionPolicy Bypass -File .\CHECK_QUALITY.ps1 -Isolated`: **`QUALITY GATE PASSED`** (`exit 0`, **`788/788`** tests `OK` trong `378.646s`, branch coverage **`80.3%`**, Ruff `0`, Mypy `0` trên `80` files, `PHASE 9A QUALITY PASS` `secrets=0`, Phase 3–9B + schema `scan_jobs_columns=23` + `phase3-app-smoke-ok` + compileall).
  - `node tests/check_template_js.js`: `template-js-contract-ok scripts=8 handlers=48 behavior=14` (`exit 0`).
  - `$env:GRAPHIFY_BACKEND="none"; graphify update .`: `Rebuilt: 5258 nodes, 15927 edges, 204 communities` (`.graphifyignore` nguyên vẹn).
  - Review độc lập Read-Only `Neon XHigh` (`gemini-4-neon-xhigh`, `@verifier`, `#16`): `0` P0/P1/P2, **`VERDICT: PASS`**.

---

## [2026-11-22] — Nâng cấp Reviewer độc lập luồng `[FULL GATE]` lên `Neon XHigh` (`gemini-4-neon-xhigh`, `DEC-054`)

- **Phân loại luồng**: `[FAST-TRACK]` (`DEC-047`) — chỉ cập nhật quy chuẩn điều phối/tài liệu, không chạm mã nguồn runtime, trình duyệt, SQLite hay PII.
- **Thay đổi quy trình & tài liệu**:
  - `AGENTS.md`: Cập nhật mục `Luồng Nhanh (FAST-TRACK)` và `Luồng Đầy đủ (FULL GATE)` chuyển mô hình Review độc lập read-only (`swarm` không `fork`) từ `Argon High` (`gemini-4-argon-high`) sang **`Neon XHigh` (`gemini-4-neon-xhigh`, `DEC-054`)**.
  - `HANDOFF.md`: Đồng bộ mô hình Review độc lập `[FULL GATE]` sang `Neon XHigh` (`gemini-4-neon-xhigh`, `read-only`, `DEC-054`).
  - `PROJECT_DECISIONS.md`: Ghi nối tiếp `DEC-054` (giữ nguyên toàn bộ lịch sử `DEC-049` và các mốc lịch sử khác).
  - `PROJECT_STATE.md`: Cập nhật trạng thái điều phối `DEC-054` và thêm mốc `2026-11-22`.
- **Tệp thay đổi (5 files)**: `AGENTS.md`, `HANDOFF.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`, `CHANGELOG.md`.
- **Bằng chứng kiểm chứng (`[FAST-TRACK]`)**: `swarm(action="models", all=true)` xác nhận `"gemini-4-neon-xhigh"` (`"Gemini 4 Neon (XHigh)"`) khả dụng; chuẩn hóa `LF` cả 5 tệp; `git diff --check` exit `0`; `venv\Scripts\python.exe -m scripts.phase9a_audit` exit `0` (`secrets=0`, `dependency_lock` `73` entries `ok`); `node --test scripts/lib/*.test.mjs` (kèm `.venv\Scripts` trong `PATH`) `112/112 PASS`.

---

## [2026-11-20] — Sửa lỗi `Lấy từ LMS` trong `Nộp Google Form` bằng truy vấn GraphQL không lọc quá hạn (`type: []`) & hỗ trợ hậu tố `-HB` (`DEC-053`)

- **Phân loại luồng & Review**: `[FULL GATE]` (`DEC-047`) · Review độc lập Read-Only `Carbon High` (`verifier-4`, `#33`, **`VERDICT: PASS`**).
- **Nguyên nhân gốc (`Job #373`, `#376`, `#547`, `#548`)**:
  - Luồng `GOOGLEFORM_LMS_STUDENTS` trước đây mở `Config.LMS_URL` vốn gắn sẵn `type=["MISS_ATTEND","DUE_COMMENT"]`, khiến mọi lớp hoàn tất đúng hạn không quá hạn trên LMS (điển hình `VT-CSA04-HB`) biến mất khỏi bảng dù tồn tại hợp lệ trên BCNB và `discovery_snapshots`; đồng thời ô tìm kiếm LMS rơi kết quả với mã có hậu tố `-HB`.
- **Thay đổi mã nguồn**:
  - `services/lms_quality_service.py`: Tách gọn helper Python/JS dùng chung (`_CLEAR_STALE_LMS_AUTH_JS`, `_LMS_JS_COMMON_HELPERS`, `_extract_lms_centres_from_config`, `_extract_pre_resolved_lms_token`, `_create_lms_graphql_request_sniffer`, `_open_and_authenticate_lms_page`, `_cleanup_lms_eval_resources`) và bổ sung `fetch_lms_active_students_via_page(page, *, class_codes, timeout_ms=15000)` truy vấn GraphQL `classesMissAttendOrComment` với `type: []`. Hỗ trợ ứng viên tìm kiếm mã ngắn cho `-HB`, ép khớp chính xác `item.name === code`, lọc `activeInClass !== false`, điều hướng từ `"about:blank"` về đúng origin LMS khi khởi động lạnh để tránh CORS `Origin: null`, triển khai Tier-1 bulk + Tier-2 3-pass fallback (`Pass 1`, `Pass 2a` bỏ `status`, `Pass 2b` bỏ `centres`) và giữ nguyên hành vi `fetch_lms_class_slots_via_page`.
  - `services/lms_service.py`: Bổ sung `_parse_graphql_active_students` và tích hợp vào `_read_lms_students_by_classes` (fail-closed `LmsSafetyError` khi `__error__`/`__auth_error__`/`fetch_error`/`duplicate_class`/thiếu lớp/`0` học viên/trùng tên khác `stu_id`/vượt trần `200` học viên, kiểm tra hủy job, `0` PII trong log, và fallback nguyên vẹn về đọc DOM khi `ZALO_QUALITY_ISOLATED=1` hoặc test fake không có `.evaluate`).
  - `tests/test_lms_service.py`: Thêm `test_googleform_graphql_active_roster_handles_hb_non_overdue_fallbacks_and_fail_closed` kiểm chứng Node + mock `global.fetch` cho cold-start `"about:blank"`, `VT-CSA04-HB` không quá hạn, `activeInClass !== false`, Pass 2a/2b, bulk `>3` lớp, 6 ca fail-closed và `0` PII trong `log_message`.
- **Kiểm chứng**:
  - Unit tests mục tiêu (`tests.test_lms_service`, `tests.test_lms_quality_service`, `tests.test_spck_session_fetch`, `tests.test_lms_plan`, `tests.test_lms_confirmation`): `75/75 OK`.
  - `powershell -ExecutionPolicy Bypass -File .\CHECK_QUALITY.ps1 -Isolated`: **`QUALITY GATE PASSED`** (`exit 0`, **`777/777`** tests `OK`, branch coverage **`80.1%`**, Ruff `0`, Mypy `0` trên `80` files, `secrets=0`, `template-js-contract-ok scripts=8 handlers=46 behavior=14`).
  - `$env:GRAPHIFY_BACKEND="none"; graphify update .`: `5213 nodes, 15744 edges, 188 communities`; `.graphifyignore` nguyên vẹn.
  - Review độc lập Read-Only `verifier-4` (`gemini-4-carbon-high`, `#33`): `0` P0/P1/P2, **`VERDICT: PASS`** (`67/67` & `53/53` `OK`, `ruff`/`mypy`/`git diff --check` `0`).

---

## [2026-11-20] — Đối chiếu đúng buổi theo tuần kiểm tra trong Tự động Tick LMS (`Hướng 1`) & Hoãn ghi lỗi sớm trước deadline trên `Lớp Hôm Nay` (`DEC-052`)

- **Đối chiếu đúng buổi tuần kiểm tra trong `Tự động Tick LMS` (`services/lms_plan.py`, `services/lms_service.py`, `static/js/jobs.js`)**:
  - Đọc thêm thông tin tùy chọn `lesson`, `class_date`/`class_date_iso` và `khoi` trong `_read_lms_table` (giữ nguyên hợp đồng `observed_classes` và `compute_plan_digest` của `run_lms_automation`).
  - Bổ sung `parse_bcnb_lesson_number`, `parse_overdue_lesson_numbers` và `_reconcile_mismatched_overdue_classes`: khi trang tổng hợp MindX LMS báo quá hạn ở một buổi mới hơn/khác với buổi của tuần BCNB đang kiểm tra (như `VT-ROB-ARMA18` vừa học xong `Buổi 2` Tuần 47 tối thứ Tư trong khi BCNB đang kiểm tra `Buổi 1` Tuần 46), worker tự động truy vấn đích danh slot của đúng buổi tuần BCNB qua `fetch_lms_slots_for_targets` + `evaluate_lms_slot_quality` (`DisabledLmsCommentReviewerGateway`) + `is_lms_ready_for_bcnb_tick`. Chỉ loại khỏi danh sách quá hạn khi buổi tuần kiểm tra đã hoàn tất; nếu buổi tuần cũ thực sự chưa xong thì giữ lại và chỉnh về đúng `Buổi {bcnb_lesson}`; luôn fail-closed khi lỗi mạng hoặc slot mơ hồ.
  - Bổ sung `all_eligible_already_checked` + `info_message` trong `build_lms_plan` (kèm trạng thái `SUCCESS` trên `run_lms_analysis` và dải thông báo xanh ngọc trên `showLmsReview`) khi các lớp hợp lệ của tuần đều đã tích BCNB từ trước để thay cho thông báo lỗi đỏ `"Không có lớp nào đủ điều kiện để tick."` while giữ nút xác nhận khóa an toàn.
- **Hoãn ghi lỗi mới trước deadline lên Teaching khi chạy `Quét & Tick BCNB đúng tuần` trên `Lớp Hôm Nay (LIVE)` (`services/zalo_bcnb_service.py`, `services/today_ops_service.py`)**:
  - Bổ sung `_is_bcnb_deadline_expired` phân tích chính xác mốc deadline 4 chữ số năm (`HH:MM DD/MM/YYYY`, `DD/MM/YYYY HH:MM`, `YYYY-MM-DD HH:MM`, tránh nhầm dòng timestamp tick thứ 3 trên BCNB) và bật `defer_unexpired_checker_notes=True` riêng cho `services/today_ops_service.py:_run_bcnb_sync_on_session` (giữ nguyên hành vi mặc định `False` của `ZALO_SCAN`).
  - Trước deadline, bot vẫn tự tick xanh `NX Zalo` & `NX LMS` cho GV đã hoàn thành, vẫn giữ đầy đủ danh sách lỗi trên giao diện Live + `Copy nhắc GV`, vẫn dọn ngay ghi chú lỗi tự động cũ vừa được GV sửa xong và bảo toàn ghi chú tay QC, nhưng hoãn ghi lỗi mới phát sinh lên ô `Mô tả vấn đề` của Teaching (`checker_note_outcome = "DEFERRED_BEFORE_DEADLINE"`); sau deadline vẫn ghi bình thường.
- **Tệp thay đổi (12 files)**: `services/{lms_plan,lms_service,today_ops_service,zalo_bcnb_service}.py`, `static/js/jobs.js`, `tests/{check_template_js.js,test_lms_plan.py,test_lms_service.py,test_today_ops_service.py}`, `CHANGELOG.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`.
- **Bằng chứng kiểm chứng (`[FULL GATE]`)**:
  - Targeted unit suite (`test_lms_plan`, `test_lms_service`, `test_lms_confirmation`, `test_today_ops_service`, `test_today_eligibility_and_bcnb`): `66/66 OK` (`9.319s`); `ruff` & `mypy` trên các file sửa `0` lỗi; `node tests/check_template_js.js` -> `template-js-contract-ok scripts=8 handlers=46 behavior=14` (`exit 0`); offline `graphify update .` -> `5200` nodes, `15708` edges, `196` communities (`exit 0`, `.graphifyignore` nguyên vẹn).
  - `powershell -ExecutionPolicy Bypass -File .\CHECK_QUALITY.ps1 -Isolated`: `exit 0` (`QUALITY GATE PASSED`), `776/776` regression tests `OK` (`344.104s`), branch coverage `80.2%`, Ruff `0`, Mypy `0` lỗi trên `80` source files, Phase 3/4/5/6/7/8/9/9A/9B PASS, `secrets=0`, `migrations=[1, 2, 3, 4]`.
  - Review độc lập read-only (`verifier-3` trên Carbon High, `swarm #30`): `VERDICT: PASS` (`[P0]=0, [P1]=0, [P2]=0`, re-run 85 targeted tests `OK` + static analysis PASS).

## [2026-11-18] — Sửa cắt khung ảnh minh chứng Zalo, phát hiện `[LMS] Chưa điểm danh` (`MISSING_ATTENDANCE`), sửa Tier-2 `fetchOneCode` & Khử trùng Legacy + Mức A (`DEC-051`)

- **Sửa cắt khung ảnh minh chứng Zalo không mất chữ bên trái & loại bỏ khoảng trống xám bên phải (`services/zalo_scan_evidence.py`, `tests/test_zalo_scan_evidence.py`)**:
  - Ưu tiên neo trực tiếp khung chat bên phải (`#messageView`, `#chatView`, `#main-tab`, `main` hoặc cột chứa header / `#richInput` / `#input_line_0`) trước khi fallback sang tổ tiên `#contact-search-input` (`220 <= width <= 385`, bỏ cộng lấn `+16px`); áp dụng chốt chặn vô điều kiện `chatLeft <= minContentLeft - 24` (loại trừ `sidebarRoot`) để không bao giờ cắt lẹm tiêu đề nhóm, avatar, tin nhắn hoặc thanh nhập liệu.
  - Xử lý các phần tử bọc rộng toàn khung chat (`r.width >= (vpW - chatLeft) * 0.85` như dòng mốc ngày căn giữa) bằng cách đo biên phải của phần tử nội dung con thực tế thay vì kéo `clipRight` ra sát `vpW` (`1920px`).
- **Phát hiện `MISSING_ATTENDANCE` (`[LMS] Chưa điểm danh`) & Đồng bộ trạng thái lỗi (`services/lms_quality_service.py`, `services/report_status.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `services/today_ops_service.py`, `static/js/jobs.js`, `static/js/today.js`)**:
  - Tính số nguyên `activeClassStudentCount` (`0` PII) cho từng lớp trong `formatItemSlots`; nhận diện sạch `MISSING_ATTENDANCE` khi buổi học đã kết thúc tải hợp lệ (`!partial`) có học viên đang theo học nhưng `studentAttendance == []` hoặc toàn bộ dòng mang trạng thái chưa điểm danh tường minh (`""`, `"NONE"`, `"NOT_ATTENDED"`, `"UNMARKED"`, `"PENDING"`, `"NULL"`).
  - Thực thi đúng quy tắc câu chữ của Owner: trên Teaching và kết quả đánh giá slot chỉ xuất duy nhất **`[LMS] Chưa điểm danh`** (`ISSUE_CODE_LMS_QUALITY_FAIL`, `SEVERITY_FAIL`), bỏ toàn bộ phần đuôi `[LMS] Điền tóm tắt bài` / `[SPCK]` phụ và tự động thay thế sạch ghi chú `[LMS]`/`[SPCK]` cũ trên Teaching.
  - Chuẩn hóa hiển thị `"❌ Chưa điểm danh LMS"` (`is_failed=True`, chặn tuyệt đối tích `NX LMS`), `"SPCK khóa (chưa điểm danh)"`, rút gọn URL Gmail `"Chưa ĐD LMS"` và cập nhật KPI/nhắc việc trên `Lớp Hôm Nay`.
- **Sửa Tier-2 Selective Rescan (`fetchOneCode`) & Khử trùng câu chữ Legacy + Mức A (`services/lms_quality_service.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`, `static/js/jobs.js`)**:
  - Bổ sung `centres: activeCentres` và `itemsPerPage: 50` cho Tier-2 `fetchOneCode` (khi quét chọn lọc `<= 3` lớp); tự động thử lại theo hậu tố mã lớp ngắn (kể cả lớp `-HB` như `VT-C4K-PTI11 -> PTI11`, `VT-C4K-GB42-HB -> GB42-HB -> GB42`) khi `search: code` trả `0` kết quả mà vẫn giữ lọc khớp tuyệt đối `itemName === code`.
  - Khử trùng tương đương ngữ nghĩa giữa ghi chú lịch sử và ghi chú Mức A (`[LMS] Chưa ghi nội dung bài học` -> `[LMS] Điền tóm tắt bài`, `cần kiểm tra thủ công` -> `Kiểm tra tay trên LMS`, `[Zalo] Báo cáo không ghi ngày/buổi` -> `[Zalo] Thiếu ngày/buổi`) trên Teaching, Dashboard, Báo cáo tuần, CSV và Email Quản lý.
- **Tệp thay đổi (21 files)**: `repositories/report_repository.py`, `services/{lms_quality_service,report_email_service,report_status,today_ops_service,zalo_bcnb_service,zalo_scan_evidence}.py`, `static/js/{jobs,today}.js`, `tests/{check_template_js.js,test_bcnb_actions,test_lms_attendance_quality,test_lms_quality_service,test_report_issue_display,test_report_status,test_spck_session_fetch,test_today_ops_service,test_zalo_scan_evidence}.py`, `CHANGELOG.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`.
- **Bằng chứng kiểm chứng (`[FULL GATE]`)**:
  - `.\CHECK_QUALITY.ps1 -Isolated`: `exit 0` (`QUALITY GATE PASSED`), `770/770` regression tests `OK` (`308.379s`), branch coverage `80.1%`, Ruff `0`, Mypy `0` lỗi trên `80` source files, Phase 3/4/5/6/7/8/9/9A/9B PASS, `secrets=0`, `migrations=[1, 2, 3, 4]`.
  - `node tests/check_template_js.js` -> `template-js-contract-ok scripts=8 handlers=46 behavior=14` (`exit 0`); offline `graphify update .` -> `5176` nodes, `15639` edges, `180` communities (`exit 0`).
  - Review độc lập (`verifier-2` trên Carbon High, read-only swarm `#25`): `VERDICT: PASS` (`[P0]=0, [P1]=0, [P2]=0`).

## [2026-11-18] — Tối ưu tốc độ Phase 3 BCNB, lưu `complete_write` từng dòng, Quy tắc Tiêu đề SPCK mới & Chuẩn Ghi chú QC ngắn gọn Mức A (`DEC-050`)

- **Chặng 1 — Tốc độ Phase 3 BCNB, lưu trạng thái ghi từng dòng & nâng đệm timeout (`services/zalo_bcnb_service.py`, `services/zalo_parallel_service.py`, `core/settings.py`, `googleform_autosubmit/config.py`, `.env.example`)**:
  - Khắc phục nghẽn `O(N^2)` gây timeout ở batch lớn (`Job #530`): bổ sung cờ `dom_dirty` và bản chụp `latest_bcnb_rows` theo từng tuần trong `update_bcnb_reports`, bỏ qua đọc lại bảng khi `dom_dirty == False` (tái sử dụng `latest_bcnb_rows` trong `0ms` cho các dòng `ALREADY_DONE` / `NO_CHANGE`), và chỉ đọc lại khi có thao tác click checkbox Stage A/B, POST ghi chú Stage C khác `NO_CHANGE`/lỗi hoặc truy vấn LMS trên `page`.
  - Tăng tốc `_read_bcnb_schedule_rows` bằng 1 lệnh `await page.evaluate(_READ_BCNB_ROWS_FAST_JS, ext_cols)` duy nhất trên trang Playwright thật để trích xuất toàn bộ cell text, trạng thái checkbox `NX Zalo`/`NX LMS`, `bcnb_session_id` và ghi chú QC/GV trong một vòng IPC (giữ nguyên parity `checker_note_unreadable` fail-closed và fallback 100% về vòng lặp locator cho unit test).
  - Bổ sung callback `on_row_finalized` trong `update_bcnb_reports` kết nối với `finalize_claimed_row` + `completed_claimed_ids` trong `write_worker` (`zalo_parallel_service.py`) để gọi `update_target_result` + `complete_write` ngay sau khi từng dòng hoàn tất Stage A+B+C, bảo toàn vĩnh viễn các dòng đã `VERIFIED` / `ALREADY_DONE` trong SQLite nếu job bị hủy hoặc timeout giữa tuần.
  - Nâng mặc định `JOB_TIMEOUT_SECONDS` từ `1800` (30 phút) lên `3600` (60 phút).
- **Chặng 2 — Quy tắc Tiêu đề SPCK mới trên LMS (`services/lms_quality_service.py`)**:
  - Gỡ bỏ yêu cầu bắt buộc có tên học viên trong tiêu đề SPCK (`StudentWork.title`): mọi tên sản phẩm/dự án cụ thể không kèm tên học viên (`"Website cửa hàng sách"`, `"Game bắn thiên thạch"`, `"SPCK - Game bắn thiên thạch"`, ...) đều hợp lệ (`0` lỗi tiêu đề).
  - Chỉ gắn nhắc nhở tên sản phẩm (`bad_title_count` -> `[SPCK] Bổ sung ... tên SP`) khi: (a) tiêu đề trống, chỉ gồm dấu câu hoặc từ khóa đối phó chung chung (`"SPCK"`, `"Sản phẩm cuối khóa"`, `"SPCK - [HS]"`, `"Dự án cuối khóa"`, mã lớp/số buổi), hoặc (b) tiêu đề copy-paste trùng nhau ở `>= 2` học viên đi học trong cùng buổi qua khóa băm tất định không chứa PII `rawTitleKey` tính trên trình duyệt trước bước khử tên học viên.
- **Chặng 3 — Chuẩn Ghi chú QC ngắn gọn Mức A (`[Zalo]` · `[LMS]` · `[SPCK]`) & Tương thích ngược (`services/lms_quality_service.py`, `services/zalo_target_matching.py`, `services/bcnb_safety.py`, `services/zalo_bcnb_service.py`, `services/report_status.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`, `static/js/today.js`)**:
  - Tách tiền tố `[SPCK]` riêng cho Sản phẩm cuối khóa; chỉ giữ tỷ lệ `(x/y)` trên 3 lỗi `FAIL` cốt lõi (`[LMS] Chưa NX (x/y)`, `[SPCK] Chưa nộp (0/y)`, `[SPCK] Chưa NX (x/y)`) và gom toàn bộ nhắc nhở `NOTE` thành tối đa 1 câu hành động ngắn gọn cho mỗi thẻ (`[LMS] Điền tóm tắt bài, viết kỹ NX`, `[SPCK] Bổ sung nộp đủ bài, tên SP, ảnh, file/file code, viết kỹ NX, YouTube, Slide`, `[Zalo] Thiếu ngày/buổi`, `[Zalo] Sai ngày (dd/mm)`, `[Zalo] Sai số buổi, lệch tên bài với LMS`, ...).
  - Mở rộng provenance audit (`_provenance_issue_key`), bộ phân loại trạng thái, Email Quản lý (`DEC-046` đỏ `#dc2626` / vàng cam `#b45309`) và Dashboard/Today JS để tương thích hai chiều với dữ liệu lịch sử trong `external_action_audit`/SQLite, tự động thay thế câu chữ dài cũ bằng ghi chú Mức A ngắn gọn (hoặc xóa sạch khi đã khắc phục) mà vẫn bảo toàn nguyên vẹn ghi chú tay của QC từng byte.
- **Tệp thay đổi (27 files)**: `.env.example`, `core/settings.py`, `googleform_autosubmit/config.py`, `repositories/report_repository.py`, `services/{bcnb_safety,lms_quality_service,report_email_service,report_status,zalo_bcnb_service,zalo_parallel_service,zalo_target_matching}.py`, `static/js/{jobs,today}.js`, `tests/{check_template_js.js,test_bcnb_actions,test_lms_attendance_quality,test_lms_quality_service,test_report_issue_display,test_report_status,test_spck_session_fetch,test_today_ops_service,test_zalo_parallel_service,test_zalo_scan_evidence,test_zalo_target_matching}.py`, `CHANGELOG.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`.
- **Bằng chứng kiểm chứng (`[FULL GATE]`)**:
  - `.\CHECK_QUALITY.ps1 -Isolated`: `exit 0` (`QUALITY GATE PASSED`), `761/761` regression tests `OK` (`386.157s`), branch coverage `79.9%`, Ruff `0`, Mypy `0` lỗi trên `80` source files, Phase 3/4/5/6/7/8/9/9A/9B PASS, `secrets=0`, `migrations=[1, 2, 3, 4]`.
  - `node tests/check_template_js.js` -> `template-js-contract-ok scripts=8 handlers=46 behavior=14` (`exit 0`); offline `graphify update .` -> `5160` nodes, `15599` edges, `166` communities (`exit 0`).
  - Review độc lập (`verifier`, read-only swarm `#11`): `VERDICT: PASS` (`[P0]=0, [P1]=0, [P2]=0`).

## [2026-11-15] — Wave 7 Live Hotfix: Sửa lỗi `TimeoutError` khi nhấn `Kiểm tra Live ngay` trên `Lớp Hôm Nay` & Cô lập lỗi setup Zalo (`DEC-048`, `DEC-049`)

- **Sửa nguyên nhân gốc gây `TimeoutError` (`services/today_ops_service.py` — `_scan_ended_zalo_targets`, `run_today_realtime_sync`)**:
  - Trước đây `_scan_ended_zalo_targets` gọi `_invoke_landing_provider(landing_provider, zalo_page)` trên trang `about:blank` mới mở **trước** khi `_setup_scan_page` điều hướng tới `https://chat.zalo.me/` và truyền callback chưa bind page vào `landing_facts_provider`, làm `ZaloAuthReadinessAdapter` poll hết `60.0s` trên `about:blank` và làm sập toàn bộ job `TODAY_REALTIME_SYNC` khi có lớp đã kết thúc.
  - Sửa bằng cách truyền zero-arg callable đã bind `target_page` (`landing_facts_provider=landing_provider_for(candidate_page)`) vào `_setup_scan_page` để chỉ kiểm tra trạng thái đăng nhập **sau** `page.goto("https://chat.zalo.me/")`.
- **Đồng bộ cơ chế phục hồi & cô lập lỗi với `run_parallel_zalo_batch` (`services/today_ops_service.py`)**:
  - Bổ sung retry khởi tạo trang Zalo tới `MAX_SETUP_ATTEMPTS` (ngắt ngay với nhóm mã `LANDING_SETUP_CODES` như `LOGIN_OR_QR`), phát setup telemetry bounded qua `_emit_setup_telemetry`, đóng trang an toàn chống double-close, timeout từng target qua `_await_with_target_timeout` (`TARGET_TIMEOUT`), retry `TargetSearchError` 1 lần trên trang Zalo làm mới (`attempt=2`), cập nhật tiến độ từng lớp (`50%..74%`), và giữ nguyên `error_code` / `error_message` / `batch_halted` trong `finalize_registered_scan`.
  - Cô lập lỗi `ZaloSetupError` về trạng thái `PARTIAL_SUCCESS` kèm `zalo_setup_notice` trong message & `results` JSON thay vì làm `FAILED` toàn bộ job — giữ nguyên toàn bộ kết quả live LMS, SPCK B12–14 và BCNB đọc được trong ngày trong khi vẫn giữ các lớp Zalo ở trạng thái fail-closed (`INSUFFICIENT_EVIDENCE`, không ghi `NX Zalo`).
- **Hồi quy & Kiểm chứng (`tests/test_today_ops_service.py`, `[FULL GATE]`)**:
  - Thêm 3 regression unit tests mới (`test_run_today_realtime_sync_navigates_zalo_before_landing_facts_provider`, `test_run_today_realtime_sync_isolates_zalo_setup_login_qr_and_preserves_lms_spck`, `test_scan_ended_zalo_targets_retries_transient_setup_and_target_search_error`).
  - Targeted unit tests (`tests.test_today_ops_service`, `tests.test_today_routes`, `tests.test_today_eligibility_and_bcnb`): `33/33 OK`; `ruff`, `mypy`, `node tests/check_template_js.js` PASS; offline `graphify update .` (`5126` nodes, `15502` edges, `180` communities); `.\CHECK_QUALITY.ps1 -Isolated`: `exit 0` (`QUALITY GATE PASSED`, `752/752` regression tests `OK` trong `366.395s`, branch coverage `79.5%`, `services/today_ops_service.py` `78.7%`). Review độc lập `Argon High` (`reviewer-75`, swarm read-only `#46 -> #47`): `VERDICT: PASS` (`[P0]=0, [P1]=0, [P2]=0`).

## [2026-11-15] — Wave 7: Trang riêng `Lớp Hôm Nay` (Live LMS/Zalo, SPCK Coding B12–14, Tick BCNB đúng tuần) & Đổi Review độc lập sang `Argon High` (`DEC-048`, `DEC-049`)

- **Backend theo dõi lớp trong ngày & đồng bộ BCNB đúng tuần (`services/today_ops_service.py`, `api/today_routes.py`, `services/job_dispatcher.py`, `repositories/report_repository.py`)**:
  - Thêm API `GET /api/today/overview` và `POST /api/today/sync` (`api/today_routes.py`, gắn vào `main.py`, chuẩn hóa lỗi qua `public_error` không rò rỉ `str(exc)`).
  - Thêm dịch vụ `services/today_ops_service.py` (`build_today_overview`, `run_today_realtime_sync`) và job `TODAY_REALTIME_SYNC`: gom lớp diễn ra trong ngày xuyên tối đa 2 tuần BCNB (`required_week` + `latest_week`), lấy khung giờ + điểm danh + nhận xét + SPCK live từ LMS (`lms_quality_service.py`), xác định pha `UPCOMING` / `ONGOING` / `ENDED` qua `max(group_end, lms_end)` (`zalo_target_eligibility.py`), quét Zalo kèm ảnh chụp Retina `2x` cho ca đủ điều kiện và đồng bộ kết quả về `Báo Cáo Tuần` khi `check_only=False`.
  - Hỗ trợ `check_only` (100% chỉ đọc, không click/ghi BCNB hay ghi đè `zalo_batch_targets`) và nâng cấp `update_bcnb_reports` / `bcnb_actions.py` / `bcnb_safety.py` để tick an toàn cả `NX Zalo` + `NX LMS` (`is_lms_ready_for_bcnb_tick`) định danh đúng dòng theo `(class_code, lesson, class_date_iso, session_id)` với exception khử PII.
  - Chuẩn hóa quy tắc SPCK theo khối trong `services/report_status.py` và `services/today_ops_service.py`: Coding B12–14 theo dõi SPCK, Robotics chỉ kiểm tra nhận xét buổi học, khóa miễn `PTB`/`CSB` bổ sung fallback `"⚪ Miễn SPCK (PTB/CSB)"` khi chưa quét/`NOT_REQUIRED`.
- **Giao diện Trang riêng `Lớp Hôm Nay` (`templates/partials/today.html`, `static/js/today.js`, `templates/partials/sidebar.html`, `templates/index.html`, `static/js/state.js`, `static/js/jobs.js`, `static/css/app.css`)**:
  - Bổ sung tab `#btn-today` / `#tab-today` tách biệt hoàn toàn với `Dashboard` tuần: chọn ngày, 5 thẻ KPI, panel spotlight **SPCK Buổi 12–14 Coding / Robotics**, 8 bộ lọc nhanh + tìm kiếm, bảng Timeline 7 cột theo dõi `NX Zalo` & `NX LMS`, modal xem ảnh minh chứng `2x`, và nút sao chép danh sách nhắc giảng viên thiếu Zalo / LMS / SPCK (đánh dấu thiếu LMS/SPCK sau khi `phase === 'ENDED'`, dùng chung `isTodaySpckComplete`).
- **Quy trình Review độc lập `Argon High` (`DEC-049`, `AGENTS.md`, `HANDOFF.md`)**:
  - Chuyển reviewer độc lập luồng `[FULL GATE]` từ Codex CLI `gpt-6.1-sol` sang **`Argon High` (`gemini-4-argon-high`)** chạy trên `swarm` read-only không `fork`.
- **Tệp thay đổi (25 file mã nguồn/test/config + 6 tài liệu = 31 files)**: `api/today_routes.py`, `main.py`, `repositories/report_repository.py`, `services/{bcnb_actions,bcnb_safety,job_dispatcher,lms_quality_service,report_status,today_ops_service,zalo_bcnb_service,zalo_target_eligibility}.py`, `static/css/app.css`, `static/js/{jobs,state,today}.js`, `templates/{index.html,partials/sidebar.html,partials/today.html}`, `tests/{check_app_smoke,check_phase3_quality,check_template_js.js,test_today_eligibility_and_bcnb,test_today_ops_service,test_today_routes}.py`, `AGENTS.md`, `CHANGELOG.md`, `HANDOFF.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`, `README.md`, `UPGRADE_PROGRESS.md`.
- **Bằng chứng kiểm chứng (`[FULL GATE]`)**:
  - `.\CHECK_QUALITY.ps1 -Isolated`: `exit 0` (`QUALITY GATE PASSED`), `749/749` regression tests `OK` (`326.261s`), branch coverage `79.4%` (`api/today_routes.py` `82.2%`, `services/today_ops_service.py` `77.5%`), Ruff `0`, Mypy `0` lỗi trên `80` source files, Phase 3/4/5/6/7/8/9/9B + `check_app_smoke` PASS, `migrations=[1, 2, 3, 4]`, `secrets=0`.
  - `node tests/check_template_js.js` -> `template-js-contract-ok scripts=8 handlers=46 behavior=14`; offline `graphify update .` -> `5089` nodes, `15379` edges, `183` communities.
  - Review độc lập: Gate #1 `gpt-6.1-sol (high)` PASS; Gate #2 `Argon High` (`reviewer-72` `#37`/`#41`) `VERDICT: PASS`; Gate #3 `Argon High` (`reviewer-73` `#42`) `VERDICT: PASS`; Gate #4 tổng thể `Argon High` (`reviewer-74` `#44`) `VERDICT: PASS` (`[P0]=0, [P1]=0, [P2]=0`). Chưa chạy quét/tick BCNB thật trên production.

## [2026-11-14] — Bổ sung quy tắc bắt buộc tự động dùng Graphify Offline 100% cho AI (`.agents/rules/graphify.md`, `AGENTS.md`, `GEMINI.md`)

- **Thiết lập Always-On Rule cho Antigravity & các Agent (`.agents/rules/graphify.md`, `AGENTS.md`, `GEMINI.md`)**:
  - Tạo `.agents/rules/graphify.md` với frontmatter `trigger: always_on` và bổ sung quy tắc đồng bộ ngắn gọn vào `AGENTS.md` + `GEMINI.md` (chuẩn hóa LF theo `.gitattributes`).
  - Yêu cầu AI và mọi subagent luôn tra cứu `graphify query`, `graphify explain`, `graphify path` trên `graphify-out/graph.json` trước khi grep diện rộng hoặc đọc tràn lan tệp code, và chạy `$env:GRAPHIFY_BACKEND="none"; graphify update .` sau khi sửa code.
  - Khóa cứng ranh giới an toàn: giữ nguyên `.graphifyignore`, ép chế độ offline 100% (`$env:GRAPHIFY_BACKEND="none"`, `--code-only`), tuyệt đối không gọi API bên ngoài qua Graphify.
- **Tệp thay đổi (5 files)**: `.agents/rules/graphify.md`, `AGENTS.md`, `CHANGELOG.md`, `GEMINI.md`, `PROJECT_STATE.md`.
- **Bằng chứng kiểm chứng (`[FAST-TRACK]`)**: `git diff --check` exit `0`, `venv\Scripts\python.exe -m scripts.phase9a_audit` exit `0` (`secrets=0`), `.graphifyignore` giữ nguyên SHA-256 (`5285E81D...`), kiểm tra allowlist 5 file sạch (`outside-mismatch=0`).

## [2026-11-13] — Thiết lập `.graphifyignore` chặn dữ liệu thật/session, ẩn `graphify-out/` & quét bản đồ AST `--code-only`

- **Chặn dữ liệu thật, phiên trình duyệt & tạo tác runtime (`.graphifyignore`, `.gitignore`)**:
  - Tạo `.graphifyignore` chặn độc lập toàn bộ `.env*`, `zalo_session/`, `*session*/`, `.locks/`, `*.db*`/`*.sqlite*`, `*_dump.json`, `.backups/`, `runtime/`, `evidence/`, `static/screenshots/`, `googleform_autosubmit/uploads/`, `temp_lms/`, `scratch/`, `.workflow-local/`, `venv/`, `.venv/` và `graphify-out/` (đạt `0` rò rỉ kể cả khi kiểm thử với `gitignore=False`).
  - Bổ sung `graphify-out/` vào `.gitignore` (chuẩn hóa LF theo `.gitattributes`) để ẩn hoàn toàn kết quả quét cục bộ khỏi Git.
- **Quét bản đồ mã nguồn cục bộ (`GRAPHIFY_BACKEND=none`, `--code-only`)**:
  - Chạy `graphify extract . --code-only` và `graphify cluster-only .` với `GRAPHIFY_BACKEND=none` (`0` token LLM): lập chỉ mục AST `225` file mã nguồn (`4,847` nodes, `14,514` edges, `168` communities), xuất `graph.json`, `GRAPH_REPORT.md` và `graph.html` trong `graphify-out/`.
- **Tệp thay đổi (4 files)**: `.gitignore`, `.graphifyignore`, `CHANGELOG.md`, `PROJECT_STATE.md`.
- **Bằng chứng kiểm chứng (`[FAST-TRACK]`)**: `git diff --check` exit `0`, `venv\Scripts\python.exe -m scripts.phase9a_audit` exit `0` (`secrets=0`), `venv\Scripts\python.exe -m unittest tests.test_phase9a_audit` exit `0`, kiểm chứng `0` đường dẫn nhạy cảm/session trong `graphify-out/graph.json` & `manifest.json`.

## [2026-11-13] — Chuẩn hóa nhánh `main` trên GitHub & Viết lại `README.md` chuẩn đẹp

- **Chuẩn hóa nhánh mặc định `main`**:
  - Đổi tên nhánh mặc định từ `chore/adopt-qq-ai-workflow-v9` sang `main` đồng bộ trên cả GitHub (`origin/main`, `origin/HEAD -> origin/main`) và máy cục bộ.
- **Nâng cấp toàn diện `README.md`**:
  - Cập nhật trang chủ repository trên GitHub với huy hiệu kỹ thuật (Python 3.14, FastAPI, Playwright, SQLite, Gemini, `719` tests, `78.9%` branch coverage), tổng quan 4 luồng tính năng mới nhất (Quét Zalo + Kiểm định LMS/SPCK khử PII, Báo cáo & Email Quản lý, Tick LMS, Google Form), sơ đồ kiến trúc `mermaid`, cấu trúc thư mục, hướng dẫn khởi chạy nhanh và nguyên tắc an toàn Fail-Closed (tuyệt đối không chứa dữ liệu nội bộ/nhạy cảm).
- **Tệp thay đổi (3 files)**: `CHANGELOG.md`, `PROJECT_STATE.md`, `README.md`.
- **Bằng chứng kiểm chứng (`[FAST-TRACK]`)**: `git diff --check` exit `0`, `scripts.phase9a_audit` exit `0` (`secrets=0`), kiểm tra allowlist 3 file sạch (`outside-mismatch=0`).

## [2026-11-13] — Tinh gọn tài liệu điều phối & snapshot trạng thái dự án (AGENTS.md, GEMINI.md, HANDOFF.md, PROJECT_STATE.md)

- **Loại bỏ mục lỗi thời & trùng lặp (`AGENTS.md`, `GEMINI.md`, `HANDOFF.md`)**:
  - Xóa khối hướng dẫn cũ `2026-10-03` (Luna Max, cấm commit) vốn đã bị thay thế và mâu thuẫn với cơ chế hiện hành (`DEC-036`, `DEC-040`, `DEC-047`), hợp nhất quy tắc môi trường Python (`venv` cho ứng dụng, `.venv` cho `.ai-workflow/`) và khử lặp 3 lần quy tắc commit/push để giảm token nạp vào System Prompt mỗi lượt.
  - Làm gọn `GEMINI.md` và viết lại `HANDOFF.md` ngắn gọn, trỏ trực tiếp về `AGENTS.md`.
- **Thu gọn `PROJECT_STATE.md` đúng vai trò snapshot ngắn**:
  - Rút gọn `PROJECT_STATE.md` từ `779` dòng xuống bản snapshot hiện hành (`78` source files, `719` tests, `78.9%` branch coverage, các mốc `2026-11-12` → `2026-11-13` và ranh giới vận hành mở), bảo toàn nguyên vẹn lịch sử chi tiết trong `CHANGELOG.md` và `PROJECT_DECISIONS.md`.
- **Tệp thay đổi (5 files)**: `AGENTS.md`, `CHANGELOG.md`, `GEMINI.md`, `HANDOFF.md`, `PROJECT_STATE.md`.
- **Bằng chứng kiểm chứng (`[FAST-TRACK]`)**: `git diff --check` exit `0`, kiểm tra allowlist 5 file tài liệu sạch (`outside-mismatch=0`).

## [2026-11-13] — Quy trình AI: Áp dụng Cơ chế 2 Luồng Nhanh (`FAST-TRACK`) / Đầy đủ (`FULL GATE`) theo mức độ rủi ro (DEC-047)

- **Tách quy trình kiểm chứng thành 2 luồng (`AGENTS.md`, `GEMINI.md`, `HANDOFF.md`, `PROJECT_DECISIONS.md`)**:
  - **Luồng Nhanh (`FAST-TRACK`)**: Dành cho chỉnh sửa nhỏ về UI/CSS/HTML, màu sắc, câu chữ/nhãn hiển thị, định dạng Email/CSV, tài liệu hoặc fix cục bộ rủi ro thấp $\rightarrow$ chạy unit test mục tiêu + `node tests/check_template_js.js` (nếu chạm JS/HTML) + `ruff check` & `mypy` trên file thay đổi + `git diff --check`, miễn cổng `.\CHECK_QUALITY.ps1 -Isolated` đầy đủ và miễn Review độc lập `gpt-6.1-sol`.
  - **Luồng Đầy đủ (`FULL GATE`)**: Giữ nguyên `.\CHECK_QUALITY.ps1 -Isolated` + Review độc lập `gpt-6.1-sol` đạt `PASS` đối với thay đổi tự động hóa trình duyệt (Zalo/LMS/Teaching/Form), ghi BCNB/Teaching, DB schema, bảo mật/PII, concurrency hoặc logic nghiệp vụ cốt lõi liên module.
- **Tệp thay đổi (6 files)**: `AGENTS.md`, `CHANGELOG.md`, `GEMINI.md`, `HANDOFF.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`.
- **Bằng chứng kiểm chứng (`[FAST-TRACK]`)**: `git diff --check` exit `0`, kiểm tra allowlist 6 file tài liệu sạch (`outside-mismatch=0`).

## [2026-11-13] — Hotfix Phase 6.6: Giữ `CATEGORY_MISSING` cho `NOT_FOUND_VERIFIED` kèm `FAILED_BEFORE_WRITE` (`EVIDENCE_NOT_READY`) & bọc `<span>` màu đỏ `#dc2626` tương thích BizFly Cloud Webmail (DEC-046)

- **Phân loại đúng `CATEGORY_MISSING` cho dữ liệu thật `Job #500` (`services/report_status.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`)**:
  - Khắc phục hiện tượng bản ghi `evidence_state = "NOT_FOUND_VERIFIED"` đi kèm `write_status = "FAILED_BEFORE_WRITE"` và `error_code = "EVIDENCE_NOT_READY"` (trạng thái bình thường khi không tích BCNB do thiếu báo cáo Zalo) bị xếp nhầm vào `CATEGORY_REVIEW` (`⚠️ ... Zalo cần kiểm tra`).
  - Kiểm tra độc lập cả `bcnb_update_state` và `write_status` trên Python lẫn JavaScript; tự động cập nhật lại cache `category = "REVIEW"` cũ trên dòng `NOT_FOUND_VERIFIED` sang `MISSING` (buổi thường), `EXEMPT` (Buổi 14) hoặc `DONE` (`manual_approved=True`, xóa cảnh báo Zalo cũ), trong khi bảo toàn `CATEGORY_REVIEW` cho mọi lỗi ghi/quét thật (`OUTCOME_UNKNOWN`, `CONFLICT_AFTER_WRITE`, `BLOCKED_BATCH_HALT`, `FAILED`, `BCNB_WRITE_FAILED`, ...).
- **Tương thích trình đọc Webmail (`services/report_email_service.py`)**:
  - Bọc nội dung đã `html.escape` trong `<span style="color:#dc2626;font-weight:700;">` (cho lỗi đỏ `❌ Chưa nhận xét` / `❌ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo`) và `<span style="color:#b45309;font-weight:600;">` (cho cảnh báo vàng `⚠️` SPCK), đồng bộ màu `#dc2626` ở cột `Mô tả vấn đề` để không bị stylesheet của BizFly Cloud Webmail ghi đè màu chữ `<td>`.
- **Tệp thay đổi (10 files)**: `CHANGELOG.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`, `repositories/report_repository.py`, `services/report_email_service.py`, `services/report_status.py`, `static/js/jobs.js`, `tests/check_template_js.js`, `tests/test_report_issue_display.py`, `tests/test_report_status.py`.
- **Bằng chứng kiểm chứng (`DEC-040`)**:
  - Unit tests mục tiêu (4 module, 67 tests): `67/67 PASS` (exit `0`).
  - Frontend JS contract (`node tests/check_template_js.js`): `template-js-contract-ok scripts=7 handlers=37 behavior=13` (exit `0`).
  - Full isolated gate (`powershell -ExecutionPolicy Bypass -File .\CHECK_QUALITY.ps1 -Isolated`): `QUALITY GATE PASSED` (exit `0`, Ruff `0`, Mypy `0` trên 78 files, `719/719` regression tests `OK` trong `356.666s`, branch coverage `78.9%`, `secrets=0`).
  - Independent Review (`codex exec -m gpt-6.1-sol -c model_reasoning_effort="high" -s read-only -`): Round 1 `NEEDS_FIX` $\rightarrow$ Round 2 **`VERDICT: PASS`** (session `01a1d3e6-b342-7c22-be78-0513fca56b86`).

## [2026-11-13] — Phase 6.6: Làm nổi bật lỗi đỏ `❌ Chưa nhận xét` Zalo và chuyển thiếu nộp / thiếu nhận xét SPCK sang cảnh báo vàng `⚠️` trong Email Quản lý (DEC-046)

- **Chưa nhận xét Zalo (`CATEGORY_MISSING`) là lỗi đỏ nặng nhất**:
  - `repositories/report_repository.py` (`normalize_zalo_status_text`, `_resolve_row_evaluation_status`) và `static/js/jobs.js` (`normalizeZaloStatusDisplay`, `resolveDashboardRowEvaluationStatus`) hiển thị `"❌ Chưa nhận xét"` (giữ tiền tố `[DRY-RUN] ` nếu có) và nâng `Tình trạng đánh giá` thành `"❌ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo"` khi đã xong LMS (ghi đè trạng thái cũ `⚠️ ... Zalo cần kiểm tra` / `⏳ ...`), hoặc `"❌ Chưa hoàn thành nhận xét"` khi chưa xong LMS; giữ nguyên `⏳` cho `CATEGORY_NOT_SCANNED` và `⚠️` cho `CATEGORY_REVIEW`.
  - `services/report_email_service.py` tô chữ đỏ đậm (`#b91c1c`) cho cột `NX Zalo`, cột `Tình trạng đánh giá` và dòng `[Zalo] Chưa gửi/nhận xét/báo cáo...` ở cột `Mô tả vấn đề (TE/TF/QC)` trong bảng HTML Email Quản lý, cũng như `text-rose-700` trên Dashboard.
- **Chưa nộp SPCK (`LMS_SPCK_MISSING`) & SPCK chưa có nhận xét (`LMS_SPCK_COMMENT_MISSING`) hiển thị mức Warning (`⚠️`)**:
  - Giữ nguyên `is_failed=True` để vẫn gửi trong Email Quản lý, nhưng đổi `Tình trạng đánh giá` sang `"⚠️ Chưa nộp SPCK trên LMS"` và `"⚠️ SPCK chưa có nhận xét trên LMS"` (tự chuẩn hóa cả dữ liệu cache cũ `"❌ ..."` mà không che khuất trạng thái nhận xét LMS `MISSING` / `UNVERIFIED` / `⚠️`).
  - Trong Email Quản lý HTML (`services/report_email_service.py`), các ô `⚠️` ở cột `Tình trạng đánh giá` và gạch đầu dòng `[LMS] Chưa nộp SPCK` / `[LMS] SPCK chưa có nhận xét` hiển thị màu vàng cam cảnh báo (`#b45309`); trên Dashboard (`static/js/jobs.js`), các dòng chỉ có cảnh báo SPCK đồng bộ `text-amber-700` ở cả `Đánh giá:` và `Vấn đề:`.
- **Tệp thay đổi (13 files)**: `CHANGELOG.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`, `UPGRADE_PROGRESS.md`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`, `tests/check_template_js.js`, `tests/test_api_routes_unit.py`, `tests/test_attendance_reporting.py`, `tests/test_lms_quality_service.py`, `tests/test_report_issue_display.py`, `tests/test_report_status.py`.
- **Bằng chứng kiểm chứng (`DEC-040`)**:
  - Unit tests mục tiêu (5 module, 89 tests): `89/89 PASS` (exit `0`).
  - Frontend JS contract (`node tests/check_template_js.js`): `template-js-contract-ok scripts=7 handlers=37 behavior=13` (exit `0`).
  - Full isolated gate (`powershell -ExecutionPolicy Bypass -File .\CHECK_QUALITY.ps1 -Isolated`): `QUALITY GATE PASSED` (exit `0`, Ruff `0`, Mypy `0` trên 78 files, `718/718` regression tests `OK` trong `340.477s`, branch coverage `78.9%`, `secrets=0`).
  - Independent Review (`codex exec -m gpt-6.1-sol -c model_reasoning_effort="high" -s read-only -`): Round 1 & Round 2 `NEEDS_FIX` (đã xử lý triệt để các ca biên trạng thái LMS thiếu số đếm / cần kiểm tra và đồng bộ màu Dashboard) $\rightarrow$ Round 3 **`VERDICT: PASS`** (session `01a1d348-f2e4-7381-be02-525a25d81e7c`, đối chiếu 143 ca Python/email/CSV/JS).

## [2026-11-12] — Phase 6.5: Chuẩn hóa quy tắc QC, Phễu 2 tầng AI nhận xét LMS, quy tắc SPCK/Robotics & hoãn buổi học chưa kết thúc (DEC-045)

- **Trích xuất buổi học Zalo & Hoãn buổi học chưa kết thúc**:
  - `services/zalo_safety.py` loại bỏ phần giới thiệu buổi kế tiếp / bài tập về nhà (`Buổi học kế tiếp`, `Bài tập về nhà`, ...) khi nhận diện số buổi báo cáo Zalo nhưng vẫn giữ các đoạn nội dung nối tiếp của buổi hiện tại.
  - `services/zalo_discovery_plan.py`, `services/report_status.py`, `services/zalo_bcnb_service.py`, `services/lms_quality_service.py`, `services/zalo_parallel_service.py`, `repositories/report_repository.py`, `static/js/jobs.js` hoãn kiểm tra đối với buổi học ở tương lai hoặc cùng ngày nhưng chưa hết giờ học (bao gồm Buổi 14 của tuần hiện tại) với trạng thái `⏳ Chưa tới giờ kiểm tra` (`session_deferred=True`, `is_failed=False`), không tích BCNB và không ghi chú QC sớm, bảo đảm thứ tự ưu tiên kết quả mới nhất (`newest-wins`) giữa snapshot và job.
- **Chuẩn hóa phân loại `FAIL` vs `NOTE` và quy tắc SPCK / Robotics (`ROB`)**:
  - Nhận xét buổi học LMS hoặc nhận xét SPCK bỏ trống hay chỉ gõ `"."` được tính thẳng là lỗi `FAIL` (`ISSUE_CODE_LMS_SPCK_COMMENT_MISSING`, tô đỏ trong email quản lý); nội dung tóm tắt buổi học trống/`"."`/chỉ ghi `"Bài X"` giữ mức `NOTE` (`ISSUE_CODE_LMS_SUMMARY_NOTE`); học viên `ATTENDED`/`LATE_ARRIVED` nhưng nhận xét ghi nghỉ học (không có từ phủ định) tạo `NOTE` (`ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE`).
  - Tiêu đề SPCK chưa kèm tên học viên chuyển sang `NOTE` (`[LMS] Tiêu đề SPCK chưa ghi kèm tên học viên`); có thumbnail hoặc ảnh chụp màn hình đều thỏa điều kiện hình ảnh kèm `NOTE` tách riêng thành phần thiếu; khóa Robotics (`ROB`) chuyển thiếu file code thành `NOTE` (`[LMS] SPCK Robotics chưa có file code đính kèm`) và tại Buổi 14 kiểm tra link YouTube + hình ảnh (bỏ bắt buộc Slide).
- **Phễu 2 tầng AI đánh giá nhận xét LMS khử PII (`LMS_AI_COMMENT_CHECK` + `GeminiLmsCommentReviewerGateway`)**:
  - Gỡ bỏ bộ đếm từ khóa 3 ý cứng nhắc; bổ sung cổng kiểm tra nhận xét 2 tầng kết hợp quy tắc và Gemini AI bất đồng bộ không chặn vòng lặp sự kiện, giới hạn batch mỗi lượt quét, khóa cache SHA-256, khử PII tên học viên theo token-span không chồng lấp ở cả trình duyệt và Python (`[HS]`, `[URL]`, `[EMAIL]`, `[PHONE]`), và fallback tất định (`ISSUE_CODE_LMS_COMMENT_NOTE`, không tự sinh `FAIL`).
- **Tệp thay đổi (27 files)**: `.env.example`, `CHECK_QUALITY.ps1`, `CHANGELOG.md`, `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`, `UPGRADE_PROGRESS.md`, `googleform_autosubmit/config.py`, `repositories/report_repository.py`, `services/external_gateways.py`, `services/lms_quality_service.py`, `services/report_email_service.py`, `services/report_status.py`, `services/zalo_bcnb_service.py`, `services/zalo_discovery_plan.py`, `services/zalo_parallel_service.py`, `services/zalo_safety.py`, `static/js/jobs.js`, `tests/check_template_js.js`, `tests/test_bcnb_actions.py`, `tests/test_lms_attendance_quality.py`, `tests/test_lms_quality_service.py`, `tests/test_quality_isolation.py`, `tests/test_report_status.py`, `tests/test_rule_classifier_dates.py`, `tests/test_zalo_discovery_service.py`, `tests/test_zalo_parallel_service.py`, `tests/test_zalo_safety.py`.
- **Bằng chứng kiểm chứng (`DEC-040`)**:
  - Unit tests mục tiêu (7 module, 111 tests): `111/111 PASS` (exit `0`).
  - Frontend JS contract (`node tests/check_template_js.js`): `template-js-contract-ok scripts=7 handlers=37 behavior=13` (exit `0`).
  - Full isolated gate (`powershell -ExecutionPolicy Bypass -File .\CHECK_QUALITY.ps1 -Isolated`): `QUALITY GATE PASSED` (exit `0`, Ruff `0`, Mypy `0` trên 78 files, `715+` regression tests `OK`, branch coverage `78.9%`, `secrets=0`).
  - Independent Review (`codex exec -m gpt-6.1-sol -c model_reasoning_effort="xhigh" -s read-only -`): **`VERDICT: PASS`** (session `01a1d035-ec02-7183-a03e-013fb069b571`). Chưa thực hiện quét trình duyệt live hoặc ghi Teaching production.

## [2026-11-11] — Loại cảnh báo lặp và làm gọn cột mô tả vấn đề

- `repositories/report_repository.py` tách nhóm mô tả có nhãn LMS/Zalo trước khi khử trùng giữa ghi chú Teaching và danh sách vấn đề. Giữ nguyên lý do, số lượng và các nội dung khác nhau. Chuỗi mô tả cũ cũng được xử lý khi tạo email/CSV mới.
- `services/report_email_service.py` trình bày từng vấn đề thành gạch đầu dòng, bỏ nền hồng/chữ đỏ đậm toàn ô, chỉ nhấn đỏ cảnh báo chưa nộp SPCK, canh trên các ô trong bảng.
- Thêm 7 ca kiểm thử `tests/test_report_issue_display.py`. Nhóm mục tiêu mở rộng 60/60 PASS, JavaScript/Ruff PASS, bản xem trước HTML đã render và xem bằng Chromium. Review độc lập phát hiện và đã sửa trường hợp mất ghi chú tay do bộ lọc cảnh báo cũ, follow-up Sol 6.1 high PASS với 8/8 test độc lập. Gate cô lập cuối exit 0, 705/705 tests, coverage 78.7%, Ruff/Mypy/JavaScript PASS, secret scan 0. Dữ liệu thật và email đã gửi chưa được thay đổi.

## [2026-11-11] — Miễn SPCK cho khóa PTB và CSB ở buổi 12, 13, 14

- Theo Owner, các lớp thuộc khóa PTB/CSB được miễn SPCK buổi 12–14. `services/lms_quality_service.py` bỏ truy vấn bài nộp và trả `NOT_REQUIRED`, kể cả khi chưa đọc được dữ liệu buổi. Nhận xét học viên tiếp tục theo quy tắc hiện hành.
- `services/zalo_bcnb_service.py` lưu cảnh báo SPCK tự động cũ trong lịch sử khi kiểm tra mới xác nhận không yêu cầu SPCK. Cơ chế bảo vệ ghi chú tay tiếp tục được kiểm thử.
- Ba file kiểm thử LMS/điểm danh/báo cáo bổ sung ca kiểm tra đúng khóa và hậu tố lớp, khóa gần tên, dữ liệu chưa rõ, ghi chú Teaching, CSV và email. Hai mẫu lớp cũ trong `tests/test_lms_quality_service.py`, `tests/test_report_status.py` chuyển PTB sang JSB để giữ kiểm tra SPCK ở khóa bắt buộc nộp. 53/53 test mục tiêu PASS, review Sol 6.1 high PASS về source. Gate cô lập cuối exit 0, 698/698 tests, coverage 78.6%, Ruff/Mypy/JavaScript PASS, secret scan 0. Quyết định tại DEC-044, trạng thái tại PROJECT_STATE.md. Chưa quét ghi production hoặc sửa dữ liệu lịch sử thật.

## [2026-11-11] — Phase 6.2, kiểm tra SPCK theo buổi và nhận xét theo điểm danh

- Sửa `services/lms_quality_service.py`, `services/external_gateways.py`, `services/zalo_parallel_service.py` để giới hạn truy vấn vào buổi được quét, xác minh SPCK còn thiếu theo học viên và nhận bài chờ duyệt. Dữ liệu lỗi hoặc không đầy đủ chuyển sang chờ xác minh.
- Dùng chung điểm danh cho nhận xét và SPCK, miễn học viên nghỉ ở đúng buổi. Nhận xét một đoạn có ba ý được chấp nhận, lời nhắc chất lượng giữ số lượng và lý do.
- Đồng bộ kết quả qua `services/report_status.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `api/job_routes.py`, `static/js/jobs.js`. `services/bcnb_safety.py` giữ chi tiết QC khi chuẩn hóa ghi chú. `services/zalo_bcnb_service.py` giữ cảnh báo SPCK cũ trong lịch sử khi kết quả hiện tại chưa xác minh. Dashboard ưu tiên kết quả mới khi nhận xét MISSING. Ghi chú hiển thị đơn lẻ không tạo kết luận thiếu SPCK.
- Thêm `tests/test_spck_session_fetch.py`, `tests/test_lms_attendance_quality.py`, `tests/test_attendance_reporting.py`, cập nhật các kiểm thử LMS/report/BCNB/JavaScript liên quan.
- Cập nhật kế hoạch chi tiết ở `UPGRADE_PROGRESS.md`, bổ sung `DEC-043`, ghi bằng chứng mới ở `PROJECT_STATE.md`. Review độc lập Sol 6.1 high đã PASS sau sửa, 37 test mục tiêu và kiểm tra JavaScript PASS. Gate cô lập cuối đạt `QUALITY GATE PASSED`, 695/695 test, coverage 78.6%, Ruff/Mypy PASS, secret scan 0. Manifest SHA-256 xác nhận source/test giữ đúng bản đã kiểm tra. Bản đối chiếu chỉ đọc 14 buổi và ghi chú lịch sử nằm ngoài repository. Thao tác ghi production chưa được nghiệm thu.

## [2026-11-11] — Hotfix Truy Vấn GraphQL `findAllStudentWorks` (`StudentWorkQuery` & `latestData`/`publishedData`), Khử PII URL & Giới Hạn Stream Byte Khi Kiểm Tra SPCK Trên MindX LMS (`DEC-036`, `DEC-040`, `DEC-042`)

### 🛠️ Sửa lỗi & Củng cố khử PII URL + giới hạn bộ nhớ trên LMS
- **Chuẩn hóa schema GraphQL `findAllStudentWorks` khớp 100% với MindX LMS (`services/lms_quality_service.py`)**:
  - Khắc phục lỗi `GRAPHQL_VALIDATION_FAILED` khiến các lớp Buổi 12, 13, 14 trên `Job #465, #469` (`VT-JSA13-HB`, `VT-ROB-ARMI12`, ...) rơi vào `[LMS] Chưa kiểm tra được dữ liệu SPCK trên LMS, cần kiểm tra thủ công`: đổi biến `$classIds` sang `[String]`, bỏ `pageIndex`/`itemsPerPage` không tồn tại trong `StudentWorkQuery`, chia batch tối đa `10` `classId`/request (kèm retry 1 lần, trần stream `MAX_WORKS_RESPONSE_BYTES = 5 MB` qua `readBoundedJson` hủy `resp.body.getReader()` trước `JSON.parse`, và trần `2000` bản ghi/chunk), đọc chi tiết bài nộp từ đối tượng con `latestData` / `publishedData` (`StudentWorkData`).
  - Mở rộng `classesMissAttendOrComment` lấy thêm `students { activeInClass student { id fullName } }` ở cấp lớp để ánh xạ đầy đủ học viên thuộc lớp, tự động bỏ qua bài nháp cũ của học viên đã nghỉ/bảo lưu (`activeInClass = false`) không có trong danh sách điểm danh buổi đó.
  - Nâng cấp `buildFoldedWithMap`, `decodeUrlSafeChars`, `decodePunycode`, `isPlausibleBareHost`, `scrubStudentUrl` và `scrubStudentNames` khử triệt để tên học viên trên mọi trường URL (`thumbnail`, `imageUrl`, `attachmentUrls`, `videoUrls`, `relatedUrls[].url` kể cả tên miền Unicode có dấu, IDNA Punycode `xn--` hợp lệ/lỗi và đường dẫn tương đối mã hóa `%2F`) lẫn văn bản tự do (`title`, `comment`, `relatedUrls[].name`) ở cả dạng có dấu/không dấu, NFC/NFD, ASCII & UTF-8 percent-encoded đơn/đa tầng (`%4D`, `%254D`, `%4Dinh%20Qu%C3%A2n`, `>=10` tầng fail-closed) và nối bằng khoảng trắng/`-`/`_`/`+`/`.` hoặc viết liền, đồng thời bảo toàn `%2F`, chú thích `//nhận xét` và ký tự UTF-8 không phải tên học viên trước khi trả dữ liệu về Python.
  - Kiểm chứng chỉ đọc trực tiếp trên LMS thật với `VT-JSB15` (B12/B13/B14), `VT-JSA13-HB` (B13) và `VT-ROB-ARMI12` (B12/B13): trả về đúng kết quả chấm SPCK chi tiết, `fetchErr = None`, không lộ bất kỳ tên học viên nào.
- **Bằng chứng kiểm chứng**:
  - Unit test mục tiêu (`tests/test_lms_quality_service.py`, `tests/test_report_status.py`, `tests/test_bcnb_actions.py`): `44/44 OK`.
  - Kiểm tra hợp đồng JavaScript giao diện: `node tests/check_template_js.js` pass (`scripts=7 handlers=37 behavior=13`).
  - Full isolated quality gate: `.\CHECK_QUALITY.ps1 -Isolated` exit `0` (`QUALITY GATE PASSED`, `669/669` tests OK, Ruff 0, Mypy 0 trên 78 source files, branch coverage `78.3%`).
  - Independent Review: `gpt-6.1-sol (high)` Round 7 `VERDICT: PASS` (session `01a1c85a-7d58-7b32-b183-825d52dd24cf`).

---

## [2026-11-10] — Kiểm Định Nộp Sản Phẩm Cuối Khóa (SPCK) Ở Buổi 12, 13, 14 Trên MindX LMS (`DEC-036`, `DEC-040`, `DEC-042`)

### ✨ Nâng cấp kiểm soát học vụ LMS & Tự động ghi chú QC lên Teaching
- **Truy vấn `findAllStudentWorks` & khử PII học viên ngay trong trình duyệt (`services/lms_quality_service.py`)**:
  - Mở rộng truy vấn GraphQL trong `fetch_lms_class_slots_via_page` lấy thêm `id` lớp và `id` của từng học viên trong `students`; với các lớp có buổi học `index ∈ {11, 12, 13}` (Buổi 12, 13, 14), tự động gọi batch GraphQL `findAllStudentWorks(filter: { classId_in: [...] })` (kèm fallback theo từng `classId` nếu filter `_in` không được hỗ trợ).
  - Ngay trong ngữ cảnh trình duyệt trước khi dữ liệu trả về Python: đối chiếu tên học viên (`fullName` hoặc cụm 2 từ tên đệm + tên chính không dấu) với `work.title` để gán cờ `hasStudentInfoInTitle`, sau đó thay thế mọi xuất hiện của tên học viên trong `title`/`description`/`comment` bằng `[HS]` và ẩn danh hóa `studentId` thành `stu_1`, `stu_2`, ... (bảo đảm 100% không lộ PII học viên vào Python, SQLite hay log).
- **Chấm chất lượng SPCK Buổi 12, 13, 14 theo Phương án A & Quy tắc Robotics (`ROB`) (`DEC-042` — `services/lms_quality_service.py`, `services/report_status.py`, `repositories/report_repository.py`, `static/js/jobs.js`)**:
  - **Chưa nộp SPCK (`0/N` học viên đi học) $\rightarrow$ Lỗi (`ISSUE_CODE_LMS_SPCK_MISSING = "LMS_SPCK_MISSING"`, `SEVERITY_FAIL`, `is_failed = True`)**:
    - Tạo issue `[LMS] Chưa nộp SPCK (N/N HS)` mức `FAIL`, kích hoạt `has_spck_missing_fail(row) = True` và `hasRowSpckMissingFail(r) = true`, hiển thị đỏ trên Dashboard, cập nhật `Tình trạng đánh giá: ❌ Chưa nộp SPCK trên LMS` (khi Zalo và nhận xét LMS đã hoàn thành), đưa vào danh sách lớp lỗi gửi Email Quản lý / CSV và ghi tự động lên cột `Mô tả vấn đề (TE/TF/QC)` của Teaching.
  - **Đã nộp SPCK (`>0/N` học viên) nhưng thiếu số lượng hoặc thiếu thành phần phụ $\rightarrow$ Nhắc nhở QC (`ISSUE_CODE_LMS_SPCK_NOTE = "LMS_SPCK_NOTE"`, `SEVERITY_NOTE`, `is_failed = False`)**:
    - Nộp thiếu học viên (`0 < submitted < attended`): `[LMS] Thiếu nộp SPCK (missing/attended HS)`.
    - Thiếu thông tin học viên trong tiêu đề (`title`): `[LMS] SPCK thiếu thông tin học viên (x/submitted HS)`.
    - Thiếu ảnh thumbnail hoặc ảnh chụp sản phẩm (`thumbnail` + `imageUrl`): `[LMS] SPCK thiếu ảnh thumbnail/ảnh chụp (x/submitted HS)`.
    - Thiếu file đính kèm (`attachmentUrls`, áp dụng mức `NOTE` cho cả Coding lẫn khối Robotics `ROB` theo Quyết định 2): `[LMS] SPCK thiếu file đính kèm (x/submitted HS)`.
    - Chưa có nhận xét sản phẩm (`comment` rỗng): `[LMS] SPCK chưa có nhận xét (x/submitted HS)`.
    - Nhận xét sản phẩm sơ sài/chỉ chấm dấu (`_is_substantive_spck_comment`: yêu cầu $\ge 12$ từ, $\ge 6$ từ khác nhau, $\ge 50$ ký tự, $\ge 2$ mệnh đề/câu và $\ge 2$ từ khóa chuyên môn/đánh giá sản phẩm): `[LMS] Nhận xét SPCK sơ sài (x/submitted HS)`.
    - **Riêng Buổi 14 bắt buộc thêm 2 thành phần** (Buổi 12 và 13 không bắt buộc):
      - Link YouTube (`_has_spck_youtube_link` trên `videoUrls`/`relatedUrls`/`attachmentUrls`): `[LMS] SPCK Buổi 14 thiếu link YouTube (x/submitted HS)`.
      - Link hoặc File Slide thuyết trình (`_has_spck_slide_link_or_file` trên `relatedUrls`/`attachmentUrls`): `[LMS] SPCK Buổi 14 thiếu link/file Slide thuyết trình (x/submitted HS)`.
- **Mở khóa kiểm định LMS SPCK & ghi chú QC trên Teaching cho Buổi 14 (`services/lms_quality_service.py`, `services/zalo_bcnb_service.py`)**:
  - Giữ nguyên miễn trừ báo cáo Zalo cho Buổi 14 (`CATEGORY_EXEMPT`), đồng thời cho phép `inspect_and_attach_quality_issues`, `_attach_bcnb_row_metadata`, `_maybe_update_row_checker_note` (kèm gọi `fetch_and_evaluate_lms_quality` on-demand cho dòng Buổi 14 tổng hợp khi chưa prefetch) và `update_bcnb_reports` đánh giá SPCK và ghi tự động các issue `[LMS]` lên cột `Mô tả vấn đề (TE/TF/QC)` của Teaching cho cả Buổi 14.
- **Bằng chứng kiểm chứng**:
  - Unit test mục tiêu (`tests/test_lms_quality_service.py`, `tests/test_report_status.py`, `tests/test_bcnb_actions.py`): `44/44 OK`.
  - Kiểm tra hợp đồng JavaScript giao diện: `node tests/check_template_js.js` pass (`scripts=7 handlers=37 behavior=13`).
  - Full isolated quality gate: `.\CHECK_QUALITY.ps1 -Isolated` exit `0` (`QUALITY GATE PASSED`, Ruff 0, Mypy 0 trên 78 source files, branch coverage `78.0%`).
  - Independent Review: `gpt-6.1-sol (high)` Round 1 (`01a1c401-ead8-77e3-8502-c11bb16a7af2`) `NEEDS_FIX` -> Round 2 (`01a1c41b-006c-7bd1-9ffc-436145595176`) `NEEDS_FIX` -> Round 3 (`01a1c424-8254-74a2-94a3-28b2ecaefafb`) `VERDICT: PASS`.

---

## [2026-11-06] — Sửa Lỗi Tự Động Gửi Email Báo Cáo Lớp Lỗi Cho Quản Lý Qua SMTP Gmail (`DEC-036`, `DEC-040`)

### 🛠️ Sửa lỗi & Chuẩn hóa cấu hình SMTP
- **Tự động nhận diện & chuẩn hóa thông tin đăng nhập SMTP (`googleform_autosubmit/config.py`, `services/report_email_service.py`)**:
  - Khi `SMTP_USERNAME` để placeholder (`email_gui_di_cua_ban@gmail.com`) nhưng `SMTP_FROM` đã điền email thật của người gửi, hệ thống tự động fallback dùng `SMTP_FROM` làm tài khoản xác thực SMTP.
  - Tự động chuẩn hóa App Password của Gmail (`smtp.gmail.com` / `smtp.googlemail.com`): loại bỏ toàn bộ khoảng trắng phân cách 4x4 do Google sinh ra mà người dùng copy dán vào, đồng thời giữ nguyên mật khẩu có khoảng trắng cho các máy chủ SMTP ngoài Gmail.
  - Hỗ trợ tải cấu hình SMTP động từ file `.env` khi chạy dịch vụ thực tế mà không cần restart lại server, đồng thời giữ nguyên tính cô lập hermetic của các unit test.
- **Tương tác thông minh trên giao diện gửi email báo cáo (`static/js/jobs.js`, `tests/check_template_js.js`)**:
  - Khi máy chủ trả về `DUPLICATE_EMAIL_SEND` (do vừa bấm gửi cách đây ít phút), giao diện hiển thị hộp thoại xác nhận trực quan hỏi người vận hành có muốn gửi lại ngay không và tự động truyền `confirm_duplicate=true`.
  - Phân biệt rõ trạng thái gửi: khi `delivery_status === 'SENT'`, thông báo gửi email tự động thành công và không mở thêm tab Gmail; chỉ mở khung Gmail Web Compose và copy bảng vào Clipboard khi ở chế độ `DRY_RUN_PREVIEW` hoặc `SMTP_NOT_CONFIGURED`.
- **Bằng chứng kiểm chứng**:
  - Unit test mục tiêu `tests.test_api_routes_unit`: `41/41 OK`.
  - Kiểm tra hợp đồng JavaScript giao diện: `node tests/check_template_js.js` pass (`scripts=7 handlers=37 behavior=13`).
  - Full isolated quality gate: `.\CHECK_QUALITY.ps1 -Isolated` exit `0` (`QUALITY GATE PASSED`, 663 tests, Ruff 0, Mypy 0 trên 78 source files, branch coverage 78.2%).
  - Independent Review: `gpt-6.1-sol (high)` Round 3 `VERDICT: PASS` (session `01a1b013-0ed7-7472-8247-a77458d5743f`).

---

## [2026-11-05] — Cập Nhật Quy Tắc Tính Lỗi Giáo Viên (`DEC-041`: Chỉ Tính Lỗi Khi Chưa Nhận Xét LMS Hoặc Chưa Báo Cáo Zalo) & Nâng Cấp Độ Nét + Phóng To Ảnh Minh Chứng Zalo (`DEC-036`, `DEC-040`)

### ✨ Thay đổi quy tắc nghiệp vụ & Nâng cấp trải nghiệm vận hành
- **Phần 1 — Thay đổi quy tắc tính lỗi giáo viên (`is_failed` / `issue_severity`) (`DEC-041` — `services/report_status.py`, `repositories/report_repository.py`, `static/js/jobs.js`, `tests/test_report_status.py`, `tests/test_api_routes_unit.py`, `tests/test_lms_quality_service.py`, `tests/check_template_js.js`)**:
  - **Chỉ tính lỗi (`is_failed = True`) khi chưa báo cáo Zalo hoặc chưa nhận xét LMS**: Một buổi học chỉ bị tính là lỗi (tô đỏ trên Dashboard, đếm vào ô `#count-failed` / bộ lọc `"Cần xem lại"`, đưa vào danh sách lớp lỗi gửi Email Quản lý / CSV) khi **chưa nộp hoặc cần kiểm tra lại báo cáo Zalo** (`categorize_row(row) ∈ {MISSING, REVIEW}`) hoặc **chưa hoàn tất nhận xét LMS** (`lms_status` bắt đầu bằng `❌`/`⚠️` hoặc `nx_lms_checked is False` khi chưa có `lms_status`). Không dùng chuỗi `evaluation_status` cũ làm điều kiện kích hoạt lỗi độc lập.
  - **Vi phạm chất lượng nội dung là nhắc nhở (`NOTE` / `Ghi chú QC`), vẫn ghi đầy đủ lên Teaching**: Khi giáo viên đã hoàn thành cả báo cáo Zalo (`DONE`/`LATE`/`EXEMPT`) và nhận xét LMS (`✅`), các lỗi chất lượng nội dung bài học hoặc nhận xét học viên (`[LMS] Chưa ghi nội dung bài học`, `[LMS] Nhận xét thiếu ý`, `[Zalo] ...`) được hiển thị ở mức nhắc nhở (`Ghi chú QC: ...` màu trung tính trên Dashboard, `Tình trạng đánh giá: ✅ Đã hoàn thành nhận xét`, `is_failed = False`), đồng thời **vẫn được ghi tự động đầy đủ vào cột `Mô tả vấn đề (TE/TF/QC)` trên trang Teaching** (`execute_verified_checker_note_action`).
- **Phần 2 — Kết hợp Hướng 1 (Tăng độ nét Retina 2x + Cắt gọn khung chat khi chụp ảnh mới) & Hướng 3 (Click phóng to + Thanh công cụ Zoom trên Dashboard cho cả ảnh cũ lẫn ảnh mới) (`services/external_gateways.py`, `services/zalo_scan_evidence.py`, `templates/partials/image_modal.html`, `static/js/modals.js`, `tests/test_external_gateways.py`, `tests/test_zalo_scan_evidence.py`, `tests/check_template_js.js`)**:
  - **Hướng 1 — Chụp ảnh minh chứng mới sắc nét gấp đôi và cắt bỏ vùng thừa**:
    - Bổ sung `"device_scale_factor": 2` vào `_persistent_launch_options` (`services/external_gateways.py`) để Chromium kết xuất font chữ tiếng Việt có dấu ở mật độ điểm ảnh Retina 2x trong khi giữ nguyên bố cục viewport `1920x1200`.
    - Bổ sung `_resolve_zalo_chat_clip(page)` trong `services/zalo_scan_evidence.py` tự động tính vùng `clip` của khung nội dung hội thoại Zalo (cắt bỏ cột danh sách nhóm bên trái ~`340px` và khoảng trắng dư bên phải, giữ trọn tiêu đề nhóm, pill mốc ngày ở giữa và bong bóng báo cáo), kèm cơ chế fallback an toàn về `page.screenshot(path=file_path)` khi không đọc được DOM hoặc trong môi trường mock.
  - **Hướng 3 — Phóng to tương tác (`Click-to-Zoom` + Thanh công cụ `- / 100% / + / Vừa khung`) trên Dashboard**:
    - Nâng cấp `templates/partials/image_modal.html` và `static/js/modals.js`: hỗ trợ nhấp trực tiếp vào ảnh minh chứng (`toggleImageModalZoom`) để phóng to tuần tự `100% → 185% → 260% → 100%` (tự động căn cuộn vào đúng vị trí con trỏ chuột vừa bấm) hoặc dùng nút `-` / `+` (`100% ↔ 140% ↔ 185% ↔ 225% ↔ 260%`) và `Vừa khung` (`resetImageModalZoom`).
    - Sử dụng khung cuộn `block` (`#modal-image-scroll`) kết hợp `block shrink-0` và tự động gỡ `max-w-full`/`max-h-[82dvh]` khi `zoom > 100%`, đảm bảo ảnh phóng to đúng tỷ lệ thực tế (`100% = 1050px < 185% = 2087px < 260% = 2933px`) và kéo thanh cuộn xem được trọn vẹn cả 4 mép ảnh.

---

## [2026-11-04] — Hotfix Hỗ Trợ SSO `base.mindx.edu.vn` & Fallback Tick Checkbox BCNB (`Job #440`), Tự Động Phục Hồi Phiên Đăng Nhập LMS (`Jobs #434, #435, #438`), Sửa 3 Điểm Báo Lỗi Giả / Kẹt Lỗi Cũ (`W44/2026` Job `#419`) & Quy Tắc Đồng Bộ GitHub (`DEC-036`, `DEC-040`)

### 🛠️ Sửa lỗi & Củng cố độ chính xác kiểm định Zalo/LMS/BCNB
- **Hỗ trợ cổng SSO `base.mindx.edu.vn` của LMS & Khắc phục lỗi `Clicking the checkbox did not change its state` khi tick BCNB (`Job #440` — `services/lms_quality_service.py`, `services/lms_service.py`, `services/bcnb_actions.py`, `tests/test_lms_quality_service.py`, `tests/test_lms_service.py`, `tests/test_bcnb_actions.py`)**:
  - **Cho phép chuyển hướng SSO qua `base.mindx.edu.vn` và khôi phục tham số URL LMS bị cắt**: Trong `Job #440`, `AuthGuard` của `lms.mindx.edu.vn` chuyển hướng sang `https://base.mindx.edu.vn/login?callbackUrl=https://lms.mindx.edu.vn/admin/class-sessions?centres=[...]` (không URL-encode `callbackUrl`, làm rơi các tham số sau dấu `&` đầu tiên như `&type=[...]`). Đã bổ sung `"base.mindx.edu.vn"` vào `LMS_ALLOWED_LOGIN_HOSTNAMES` (`services/lms_quality_service.py`), bọc kiểm tra `password_input` trong `_wait_for_lms_login_or_table` bằng `try/except` chống lỗi hủy ngữ cảnh khi redirect client-side, và trích xuất `_restore_lms_target_url_if_needed(page)` (`services/lms_service.py`) để khôi phục đầy đủ `Config.LMS_URL` cả sau khi điền form đăng nhập lẫn khi SSO tự đăng nhập bằng phiên sẵn có.
  - **Thử lại + Fallback click DOM khóa cột khi `checkbox.check(force=True)` không đổi trạng thái (`VT-ROB-PREI14` Buổi 4)**: Khi `await checkbox.check(force=True)` ném lỗi `"did not change its state"`, chờ `0.25s`, đóng `dismiss_current_week_notice(page)`, kiểm tra cancel/halt, cuộn checkbox vào tâm viewport (`scrollIntoView({block: 'center', inline: 'center'})`), kiểm tra lại cancel/halt rồi thử `await checkbox.check(force=True)` lần 2; nếu vẫn báo `"did not change its state"`, kiểm tra cancel/halt và gọi `_CLICK_CHECKBOX_FALLBACK_JS` xác minh nghiêm ngặt đúng cột `Mã lớp` và cột `Buổi` trên dòng `<tr>` chứa checkbox trước khi gọi `el.click()`, sau đó bắt buộc qua bước `verify_checkbox_persisted` (hard reload + đối chiếu trạng thái máy chủ BCNB).
- **Tự động phát hiện JWT LMS hết hạn, hỗ trợ SSO `id.mindx.edu.vn` & tự phục hồi phiên đăng nhập LMS (`Jobs #434, #435, #438` — `services/lms_quality_service.py`, `services/lms_service.py`, `services/zalo_parallel_service.py`, `tests/test_lms_quality_service.py`)**:
  - **Loại bỏ token hết hạn trước khi gọi API**: Bổ sung `_is_usable_lms_token` (Python) và `isTokenUsable` (JS) giải mã payload JWT kiểm tra hạn `exp` (còn ít nhất `60s`) và `stsTokenManager.expirationTime` trên cả `context.storage_state` (`localStorage`), `_on_lms_request`, `IndexedDB` (`firebaseLocalStorageDb`) và `window.localStorage`, ngăn tái sử dụng token cũ đã hết hạn.
  - **Hỗ trợ đăng nhập qua cổng SSO `id.mindx.edu.vn` & kiểm tra HTTPS sau đăng nhập**: Cho phép luồng đăng nhập LMS chạy khi trình duyệt redirect sang `LMS_ALLOWED_LOGIN_HOSTNAMES = {"lms.mindx.edu.vn", "id.mindx.edu.vn"}`, đợi hoàn tất redirect khỏi `"login"` và `"id.mindx.edu.vn"` trong `_login_lms_if_needed`, sau đó xác minh nghiêm ngặt trang đích trở về `https://lms.mindx.edu.vn` (từ chối mọi downgrade `http://`).
  - **Phát hiện sớm lỗi xác thực GraphQL & Tự động xóa cache đăng nhập lại 1 lần**: Trong `js_script`, nhận diện ngay HTTP `401/403` hoặc GraphQL `UNAUTHENTICATED` / `UNAUTHORIZED` / `jwt expired` (`isAuthErrorPayload`) để dừng ngay thay vì bắn lặp hàng chục truy vấn vô ích; khi gặp `__auth_error__`, `_navigate_and_authenticate_lms(clear_stale_storage=True)` điều hướng về `https://lms.mindx.edu.vn`, dọn sạch các khóa auth cũ trong `localStorage`/`IndexedDB`, reload trang và đăng nhập lại tự động.
  - **Đồng bộ hóa retry LMS giữa các `write_worker` song song & xóa cờ `lms_fetch_unavailable` cũ**: Trong `run_parallel_zalo_batch`, bổ sung `_retry_lms_prefetch_if_unavailable` khóa bằng `lms_prefetch_retry_lock` để khi prefetch đầu tiên bị lỗi (`is_lms_fetch_unavailable`), các `write_worker` chạy song song chờ đúng 1 lượt retry duy nhất, cập nhật lại `prefetched_lms_slots`, chạy lại `inspect_and_attach_quality_issues` (tự động gỡ cờ `lms_fetch_unavailable` trên `result` và `scan_target`) và lưu lại vào `repository.update_target_result`.
- **Điểm 1 — Xử lý báo cáo Zalo tách làm 2 bubble (`VT-ROB-ARMB20`, `VT-ROB-PREB19`) & Chuẩn hóa câu chữ thiếu ngày (`classifiers/rule_classifier.py`, `services/zalo_safety.py`, `services/zalo_scan_evidence.py`, `services/zalo_target_matching.py`)**:
  - Loại trừ cụm `"nội dung bài tập"` khỏi việc tự kích hoạt `strong_header:nội dung` trong `classify_report_content`, ngăn bubble đuôi chỉ chứa nhận xét chung và bài tập về nhà tự đạt ngưỡng báo cáo độc lập.
  - Trong `extract_report_lesson_number` (`services/zalo_safety.py`), bỏ qua số buổi nằm trong tên file bài tập về nhà (`"homework"`, `"bài tập"`, `"tại file"`) trên cùng dòng (sau khi tách từ khóa `"báo cáo"`, `"tổng kết"`, `"update"`), không ăn sang dòng trước (`"Đã chữa bài tập về nhà.\nBáo cáo buổi 4"` vẫn nhận đúng buổi `4`).
  - Trong `scan_open_zalo_chat` (`services/zalo_scan_evidence.py`), khi quét ngược danh sách tin nhắn có `target` và bubble mới hơn chỉ khớp fallback (`target_match.decision != TARGET_EXACT`), lưu trạng thái tạm `provisional_state` và kiểm tra tiếp các bubble cũ hơn; nếu gặp bubble chính khớp `TARGET_EXACT` thì ưu tiên chọn `TARGET_EXACT`, nếu không thì khôi phục nguyên trạng `provisional_state`.
  - Trong `resolve_report_target` (`services/zalo_target_matching.py`), khi `claimed_lesson == expected_lesson` và `claimed_lesson_date is None` (gửi khác ngày học), chuẩn hóa ghi chú thành `"Đã báo cáo nhưng không ghi ngày; đã ghép theo ..."` thay vì câu chữ tối nghĩa `"ghi sai ngày không rõ ngày"`.
- **Điểm 2 — Sửa trích xuất tên bài học trên Zalo (`VT-C4K-GI34`, `VT-C4K-GB42-HB`, `VT-CSA04-HB`, `VT-C4K-SA27`, `VT-JSB16` — `services/lms_quality_service.py`)**:
  - Trong `extract_report_lesson_title`, dừng quét ngay khi gặp tiêu đề mục buổi học kế tiếp (`_NEXT_LESSON_SECTION_MARKERS`: `"buổi học kế tiếp"`, `"buổi tiếp theo"`, `"bài học kế tiếp"`, ...), ngăn lấy nhầm dòng `"Nội dung bài học: BUỔI N+1: ..."` ở phần giới thiệu buổi sau đem so với LMS của buổi hiện tại.
  - Loại bỏ toàn bộ ký tự biểu tượng/non-word đầu dòng và hỗ trợ mẫu tiêu đề nhiều dòng khi dòng `📘 NỘI DUNG BUỔI HỌC:` / `📝 NỘI DUNG CỦA BUỔI HỌC:` đứng riêng và dòng kế tiếp mang cấu trúc đánh số `Bài/Buổi/Lesson N: <Title>` (không nhận nhầm các dòng thông tin khác như `👥 ĐIỂM DANH: 6/6`).
- **Điểm 3 — Provenance cho các audit Wave 4 legacy bọc `[Auto QC: ...]` (`VT-C4K-GB42-HB` `session_id = 6052` — `services/bcnb_safety.py`)**:
  - Trong `extract_proven_auto_issues_from_audits`, chấp nhận các bản ghi audit `VERIFIED` production từ giai đoạn Wave 4 (chưa có trường `post_verified`) khi `after_state.qc_issue_note` có bọc `[Auto QC: ...]`.
  - Giúp `split_checker_issue_note` và `merge_checker_issue_note` nhận diện đúng các lỗi tự động cũ (`[Zalo] Có báo cáo gửi 18/10 trước ngày học target ...`, `[LMS] Chưa ghi nội dung bài học`, `[LMS] Chưa nhận xét học viên (3/3 HS)`) và tự động xóa sạch khi giáo viên đã khắc phục, đồng thời giữ nguyên 15/15 case bảo vệ ghi chú tay trong `test_5_2_4_table_driven_provenance_auto_vs_manual_note`.
- **Quy trình (`DEC-040` — GitHub là Source of Truth)**:
  - Thiết lập quy tắc cố định trong `AGENTS.md`, `GEMINI.md` và `PROJECT_DECISIONS.md`: sau khi hoàn tất bất kỳ tính năng hoặc bản sửa lỗi nào và vượt qua đầy đủ các cổng kiểm chứng (`.\CHECK_QUALITY.ps1 -Isolated` + Independent Review `gpt-6.1-sol` `VERDICT: PASS`), bắt buộc cập nhật `CHANGELOG.md` rõ ràng, commit và push đồng bộ lên GitHub (`origin`).

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
