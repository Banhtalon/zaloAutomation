# PROJECT STATE — Zalo Automation

> Snapshot hiện tại, cập nhật 2026-10-31. Source và kiểm tra runtime mới có độ
> tin cậy cao hơn tài liệu. Không dùng file này làm quyền chạy thao tác thật.

## Trạng thái ngắn

- 2026-10-31 (Hoàn tất Chặng 5.8 `7/7 ✅` & Toàn bộ Wave 5 `59/59 ✅` — Dọn dẹp, mở rộng vòng đời lớp D3, khớp cùng ngày D2, chốt LMS D4 & đồng bộ tài liệu `DEC-039`):
  - **5.8.1 (D3)**: Mở rộng parser vòng đời lớp (`services/class_lifecycle.py`, `CLASS_LIFECYCLE.md`) hỗ trợ các track `ROB4`, `SEMI`, `PRE`, `ARM`, `PT`, `JS`, `CS`, `G`, `S` với chuỗi lên cấp `B → A → I` trong cùng track (so khớp `fullmatch` token, bỏ qua hậu tố `HB`); đếm chỉ đọc trên `.backups/db/pre-wave5-20261030-20261030-095820.sqlite3` (`8/25` dòng `class_cohorts`, `8/44` dòng `groups`, `8/25` dòng `class_code_history` mang key `VT-J|S|*`/`VT-C|S|*`); ánh xạ tương thích đọc/ghi `cohort_key` cũ của `JS`/`CS` (không tự backfill/migrate DB thật) trong `services/class_lifecycle.py`, `services/zalo_discovery_plan.py`, `repositories/group_repository.py` và dừng fail-closed khi va chạm alias.
  - **5.8.2–5.8.3 (D6 & D5)**: Xóa thân luồng tuần tự chết trong `services/zalo_service.py` (còn `166 <= 520` dòng, ủy quyền hoàn toàn cho `run_and_finalize_parallel_zalo_batch`) và chuyển các cổng invariant (`tests/check_phase3_quality.py`, `tests/check_phase4_quality.py`, `tests/test_zalo_service.py`) sang kiểm tra implementation song song; xác nhận AI assist gửi nguyên văn tin nhắn ứng viên mà không bao giờ ghi nguyên văn vào log, `classification_logs` hay kết quả job (`test_5_8_3_ai_assist_passes_verbatim_text_and_never_logs_raw_message`).
  - **5.8.4–5.8.5 (D2 & D4)**: Bổ sung nhánh khớp cùng ngày học có ghi chú QC mức `NOTE` (`ZALO_REPORT_NO_DATE`: `[Zalo] Báo cáo không ghi ngày/buổi`, `ZALO_REPORT_WRONG_DATE`: `[Zalo] Báo cáo ghi sai ngày (dd/mm)`) trong `services/zalo_target_matching.py` và `services/zalo_scan_evidence.py`, giữ nguyên `REVIEW` cho các trường hợp không đọc được ngày gửi, gửi khác ngày học không khớp ngày/buổi, ghi số buổi mâu thuẫn hoặc có $\ge 2$ buổi BCNB trùng ngày, và bảo toàn hồi quy D1 (`LATE`, `is_failed=False`); chuyển kết quả LMS không xác định sang mã có cấu trúc `LMS_MANUAL_CHECK` mức `NOTE` được ghi lên ô `Mô tả vấn đề (TE/TF/QC)` mà không làm dòng bị tính lỗi trong email, kèm chốt an toàn `is_lms_fetch_unavailable` (`services/lms_quality_service.py`, `services/zalo_bcnb_service.py`, `services/zalo_parallel_service.py`, `repositories/report_repository.py`, `static/js/jobs.js`, `static/js/state.js`) tạm ngưng ghi note `LMS_MANUAL_CHECK` hàng loạt và bật cờ `lms_fetch_unavailable` khi LMS lỗi toàn hệ thống (vẫn giữ nguyên các issue `[LMS]` mức `FAIL` cũ đã verified).
  - **5.8.6–5.8.7**: Cập nhật `PROJECT_SUMMARY.md`, `PROJECT_STATE.md`, `CHANGELOG.md`, `VERSION.md`, `UPGRADE_PROGRESS.md` và append tổng kết Wave 5 vào `DEC-039` trong `PROJECT_DECISIONS.md`; Gate G1 (`189/189 OK`), G2 (`node tests/check_template_js.js` pass, `scripts=7 handlers=34 behavior=13`), G3 (`.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `663/663` tests OK, coverage `78.4%`), G4 (`git diff --check` exit `0` + manifest check `PASS`, `outside_delta=0`), G5 (`Sol 6.1 high` R1–R3 `NEEDS_FIX` -> R4 `VERDICT: PASS`, session `01a18fa5-a4c3-7293-93d3-1cae60e87f5e`). Bước an toàn tiếp theo: Owner thực hiện checklist nghiệm thu dry-run chỉ đọc trên BCNB/Zalo/LMS thật (1 tuần có học bù, 1 lớp Buổi 14, 1 lớp chuyển cấp, F5 sau quét lại); chỉ chạy production khi Owner duyệt riêng một target cụ thể.

- 2026-10-31 (Hoàn tất Chặng 5.7 `8/8 ✅` — Giao diện vận hành bám dữ liệu server):
  - **5.7.1–5.7.4**: Giữ `zaloScanPlanState.requiredWeek` khi kế hoạch cũ để bảng vẫn hiển thị được dữ liệu tuần bắt buộc trong khi khóa các nút quét, và hiển thị banner `"Kế hoạch đã cũ — Dò lại"` kèm `observed_at` (`static/js/jobs.js`); mở rộng `GET /api/jobs/latest?job_type=...` (`api/job_routes.py`, `repositories/job_repository.py`) và làm giàu `category`/`is_failed` cả khi `job["results"]` lưu dạng chuỗi JSON; dựng lại bảng từ `refreshZaloRequiredWeekSummary({ keepLiveMode: true })` khi tải trang và sau khi job ghi kết thúc (`ZALO_SCAN`, `LMS_AUTOMATION`, `ZALO_MANUAL_APPROVE`) để F5 sau quét lại chọn lọc vẫn hiện đủ dòng; nút ⟳ (`loadSelectedWeeklyHistory({ preserveLiveMode: !weeklyHistoryView.active })`) và polling bảo toàn chế độ đang xem (live/lịch sử); chặn bật lại alert/modal cho job kết thúc trước khi tải trang qua `pageLoadAtMs` + `sessionStorage` (`mindx_seen_terminal_jobs_v1`).
  - **5.7.5–5.7.7**: Khóa toàn bộ nút hành động khi `currentJobActive`, áp gate plan/preflight cho nút `Quét lại` từng dòng, ẩn nút `Tích thử (Dry-run)` sau khi đã mô phỏng xong (`manual_approve_simulated`); hộp xác nhận YELLOW liệt kê chi tiết các cảnh báo preflight và bổ sung dải preflight/tiến độ thu gọn (`#dashboard-compact-strip`, `#dashboard-preflight-pill`, `#dashboard-plan-strip-text`, `#dashboard-progress-strip-text`, `#dashboard-preflight-drawer`) trên Tab chính (`templates/partials/dashboard.html`); đồng bộ KPI 5 ô (thêm ô `Nộp trễ` `#count-late`, `#count-failed` khớp 100% với `is_failed` / bộ lọc `"Cần xem lại"` ở mọi tổ hợp `(category, is_failed)`), Copy nhắc GV, Quét lại, Duyệt & Tích theo `category`/`is_failed` từ server; hiển thị issue mức `NOTE` màu trung tính (`Ghi chú QC`), xóa toàn bộ CSV builder chết phía JS (`D6`) trong khi giữ nguyên xuất CSV qua server (`format=csv`) và Gửi Mail, sửa tooltip `\n`, bỏ nút `"Kiểm tra lại"` bị lặp trong `templates/partials/specs.html` và thống nhất nhãn `"Dò kế hoạch"`.
  - **Gate G1–G5**: G1 (`unittest tests.test_api_routes_unit`: `41/41 OK`), G2 (`node tests/check_template_js.js` pass, `scripts=7 handlers=34 behavior=13`), G3 (`.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `655/655` tests OK, coverage `77.5%`), G4 (`git diff --check` exit `0` + manifest check `PASS`), G5 (`Sol 6.1 high` R1–R3 `NEEDS_FIX` -> R4 `VERDICT: PASS`, session `01a18f26-2405-7f20-8260-248a1f8eb3c0`). Bước tiếp theo: triển khai Chặng 5.8 (`52/59` hạng mục Wave 5 đã hoàn tất).

- 2026-10-31 (Hoàn tất Chặng 5.6 `8/8 ✅` — Không rơi dòng im lặng: Discovery / Plan / Preflight):
  - **5.6.1–5.6.4**: Tạo `build_dropped_rows_summary` (`services/zalo_discovery_plan.py`) và đồng bộ `validate_two_week_plan` (`services/report_status.py`) để đếm + liệt kê đầy đủ 6 nhóm dòng không trở thành target (`transition_review`, `missing_zalo`, `ambiguous_zalo`, `deduped`/`deduped_targets`, `invalid_date`, `skipped_empty`) kèm `counts`, `total` và `groups`; giữ `dropped_rows` trong `_plan_snapshot` (`services/zalo_preflight.py`) kể cả khi plan có lỗi RED và phát cảnh báo **YELLOW** `SCAN_PLAN_DROPPED_ROWS` khi `total > 0`; hiện dòng chuyển cấp tuần trước thành mục `transition_review` trên plan và UI (`static/js/jobs.js`) mà không tự tạo target; tách miễn trừ Buổi 14 thành `exempt_targets` dựng trực tiếp từ dòng BCNB (không cần match Zalo hay qua time gate, không tạo `group_name` giả trong `zalo_batch_targets`), tách `group_reliable` khỏi `reliable` trong `services/zalo_discovery_plan.py` và `repositories/group_repository.py` để chấp nhận snapshot chỉ có `exempt_targets` ở chế độ `OBSERVED_ONLY` mà không ghi đè hay đánh dấu thiếu các nhóm/cohort đang có, và cho phép nhận job (`/run-scan` + `prepare_frozen_scan_execution`) ở cả 2 trường hợp plan chỉ có miễn trừ hoặc miễn trừ + toàn bộ target thường bị hoãn; chuyển dòng có ngày học không parse được sang `invalid_date` (YELLOW) thay vì chặn cả plan bằng `SCAN_PLAN_INVALID_TARGET`.
  - **5.6.5–5.6.7**: Ghim `snapshot_id` xuyên suốt `/run-scan` (`api/zalo_routes.py`) bằng `_ensure_snapshot_not_drifted` trước bước đọc snapshot và sau `latest_preflight`, trả `409 SNAPSHOT_CHANGED` nếu phát hiện snapshot mới hơn; `parse_group_end_time` (`services/zalo_target_eligibility.py`) trả `None` khi tên nhóm có $\ge 2$ khung giờ khác nhau để hoãn với lý do `"Không tách được giờ kết thúc buổi học từ tên nhóm Zalo (cần xác nhận giờ học)"`; gắn `deadline_verification="PENDING"` ngay ở bước quét (`services/zalo_scan_evidence.py`) cho cả `✅` lẫn `🟡` khi chưa đối chiếu deadline BCNB và hiển thị `⏳ Đã báo cáo – Minh chứng hợp lệ (chờ kiểm tra deadline BCNB)` trên UI/summary/email (`repositories/report_repository.py`, `static/js/jobs.js`, `services/report_status.py`) cho tới khi bước ghi BCNB giải quyết; tính quota AI assist theo `target_key` (chỉ fallback `group_name` khi thiếu `target_key`) và ghi rõ `ai_assist_budget_exhausted`/`AI_CLASSIFIER_ERROR` vào `classifier_result`.
  - **Gate G1–G5**: G1 (`unittest tests.test_zalo_discovery_service tests.test_zalo_target_eligibility tests.test_report_status tests.test_api_routes_unit tests.test_zalo_parallel_service tests.test_zalo_scan_evidence`: `176/176 OK`), G2 (`node tests/check_template_js.js` pass), G3 (`.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `651/651` tests OK, coverage `77.4%`), G4 (`git diff --check` exit `0` + manifest check `PASS`, `services/zalo_discovery_service.py` `395 <= 400` dòng), G5 (`Sol 6.1 high` R1–R2 `NEEDS_FIX` -> R3 `VERDICT: PASS`, session `01a18edc-054f-7e42-b2a3-5564c0df97d5`). Bước tiếp theo: triển khai Chặng 5.7 (`44/59` hạng mục Wave 5 đã hoàn tất).

- 2026-10-31 (Hoàn tất Chặng 5.5 `8/8 ✅` — Một nguồn sự thật: `services/report_status.py` + helper dùng chung):
  - **5.5.1–5.5.4**: Tạo mới `services/report_status.py` và `tests/test_report_status.py`; hợp nhất hằng `evidence_state`, các tập trạng thái kết thúc, nhãn trạng thái, `is_lesson_14`, `categorize_row` (`DONE | LATE | MISSING | REVIEW | EXEMPT | NOT_SCANNED`), `issue_severity` (`FAIL | NOTE` theo mã có cấu trúc `{code, severity, text}` hoặc tiền tố `[NOTE:CODE]`/`[FAIL:CODE]` kèm 1 adapter tương thích ngược duy nhất `_adapt_legacy_issue`), và `is_failed(row)` theo đúng quyết định Owner D1/D2/D4; đồng nhất kiểm tra lỗi thực thi ledger (`bcnb_update_state` và `write_status` trong `{FAILED, FAILED_BEFORE_WRITE, OUTCOME_UNKNOWN, CONFLICT_AFTER_WRITE, BLOCKED_BATCH_HALT}`) trên cả `get_weekly_summary` (`repositories/report_repository.py`) lẫn `/api/jobs/{id}/targets` (`api/job_routes.py`); bảo toàn các issue `[Zalo]`/`[LMS]` đã khôi phục từ provenance qua `attach_lms_and_zalo_quality_to_result` (`services/lms_quality_service.py`) và lưu DB.
  - **5.5.5–5.5.7**: Gom `validate_two_week_plan()` dùng chung cho `services/zalo_preflight.py`, `/run-scan` và `/api/zalo-scan-plan` (`api/zalo_routes.py`); gom `normalize_class_code()` + `identity_tokens()` cho `class_lifecycle.py`, `zalo_discovery_matching.py`, `zalo_target_eligibility.py`, `zalo_batch_repository.py` và ưu tiên `class_date_iso` trong `parse_bcnb_scan_target` (`services/zalo_safety.py`); gom bộ nhận diện cột BCNB (`BCNB_HEADER_PATTERNS` + JS header specs) dùng chung giữa `services/bcnb_safety.py` và `services/bcnb_actions.py`, và gom `fetch_and_evaluate_lms_quality` dùng chung giữa `services/zalo_bcnb_service.py` và `services/zalo_parallel_service.py`.
  - **Gate G1–G5**: G1 (`unittest tests.test_report_status tests.test_lms_quality_service tests.test_api_routes_unit tests.test_zalo_parallel_service tests.test_bcnb_columns`: `125/125 OK`), G2 (`node tests/check_template_js.js` pass), G3 (`.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `649/649` tests OK, coverage `77.2%`), G4 (`git diff --check` exit `0` + manifest check `PASS`), G5 (`Sol 6.1 high` R1–R3 `NEEDS_FIX` -> R4 `VERDICT: PASS`, session `01a18e7f-c706-7e81-a946-1603a43e918c`). Bước tiếp theo: triển khai Chặng 5.6 (`36/59` hạng mục Wave 5 đã hoàn tất).

- 2026-10-31 (Hoàn tất Chặng 5.3 `6/6 ✅` — Đúng đắn bằng chứng & QC: slot LMS, note rò rỉ, trạng thái không xác định):
  - **5.3.1**: Bổ sung `_id index date startTime endTime` vào truy vấn GraphQL `fetch_lms_class_slots_via_page` và `formatItemSlots` (`services/lms_quality_service.py:1148-1183`); thay thế hoàn toàn việc chọn slot theo vị trí `class_slots[lesson_num - 1]` bằng `select_matching_lms_slot` (`services/lms_quality_service.py:1503-1686`), chuẩn hóa mốc thời gian slot về `Asia/Ho_Chi_Minh` (`_slot_timestamp_to_hcm_date_iso`) và chỉ chọn khi có **đúng 1** slot không bị huỷ khớp đồng thời `slot_date_iso == target_date_iso` và `slot_lesson == lesson_num` (`index + 1 == lesson_num`); mọi trường hợp thiếu metadata ngày/số buổi, trùng ngày, lệch buổi hoặc không tìm thấy đều trả `[LMS] ... cần kiểm tra thủ công` (không fallback theo vị trí).
  - **5.3.2–5.3.5**: Chỉ ghi `target_match_note` và `_apply_target_match_context` vào `date_context` sau khi candidate đã vượt qua cả `evaluate_target_match`, `BEFORE_CLASS_DATE` và `FUTURE_UI_SENT_AT` (`services/zalo_scan_evidence.py:1173-1267, 1495-1498`), giữ nguyên `target_match.note` cụ thể của candidate bị loại (kể cả `TARGET_SESSION_AMBIGUOUS`) bên trong `classifier_result["candidates"][i]["target_match"]["note"]`; giới hạn `evaluate_zalo_content_issues` chỉ chạy khi `evidence_state` thuộc `SUBMITTED_*` và chỉ đọc candidate đã match (`services/lms_quality_service.py:763-825`); bổ sung `_is_zalo_scan_indeterminate` trong `_collect_row_verifiable_auto_issues` (`services/zalo_bcnb_service.py:412-477`) để bảo toàn issue `[Zalo]` hiện có trên BCNB khi `INSUFFICIENT_EVIDENCE` hoặc quét lỗi; chỉ gắn `CONTENT_DATE_MISMATCH` cho candidate được chọn và loại trùng `warnings`/`extra_reasons`.
  - **Gate G1–G5**: G1 (`unittest tests.test_lms_quality_service tests.test_zalo_scan_evidence tests.test_zalo_parallel_service tests.test_zalo_target_matching`: `113/113 OK`), G2 (`node tests/check_template_js.js` pass), G3 (`.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `643/643` tests OK, coverage `76.8%`), G4 (`git diff --check` exit `0` + manifest check `PASS`), G5 (`Sol 6.1 high` R1 `NEEDS_FIX` -> R2 `VERDICT: PASS`, session `01a18e0e-f611-7233-97fc-c25809bb6967`). Bước tiếp theo: triển khai Chặng 5.5 (`28/59` hạng mục Wave 5 đã hoàn tất).

- 2026-10-31 (Hoàn tất Chặng 5.4 `7/7 ✅` — Toàn vẹn Weekly Summary / Email / Duyệt tay):
  - **5.4.1–5.4.6**: Bổ sung điều kiện đủ tư cách theo từng nguồn tại `repositories/report_repository.py` (`FINISHED_ZALO_SCAN_STATUSES={'SUCCESS','PARTIAL_SUCCESS'}`, `execution_mode`/`include_dry_run` mặc định `production`, trả `409 INELIGIBLE_SCAN_JOB` khi `job_id` tường minh không đủ tư cách, `422 INVALID_EXECUTION_MODE`, gắn `execution_mode_badge="DRY-RUN"` cho cả summary, file CSV xuất ra `_DRY_RUN.csv` + header `X-Execution-Mode-Badge: DRY-RUN` + tiền tố `[DRY-RUN]` trên cột trạng thái Zalo, và gói email/subject/filename; khóa prefix `f"snap_{baseline_snapshot_id}_"` chống rò rỉ lịch của snapshot cũ); áp trần tài nguyên trước khi đọc dữ liệu (`MAX_WEEKLY_SUMMARY_SCAN_JOBS=250`, `MAX_WEEKLY_SUMMARY_BATCH_TARGETS=2000` kèm kiểm tra `json_array_length` trong SQL và chỉ trích xuất scalar `snap_id` cho manual-approval source job tại `report_repository.py:1970`, `MAX_WEEKLY_SUMMARY_LMS_JOBS=250`, `MAX_WEEKLY_SUMMARY_AUDITS=1000`, `MAX_WEEKLY_SUMMARY_AVAILABLE_WEEKS=260`, `MAX_WEEKLY_SUMMARY_GROUPS=2000`); chặn gửi email bằng `409 SCAN_ACTIVE_FOR_WEEK` khi có job `ZALO_SCAN`/`LMS_AUTOMATION`/`LMS_ANALYSIS`/`ZALO_MANUAL_APPROVE` ở trạng thái `PENDING`/`QUEUED`/`RUNNING`/`CLAIMED`; bỏ ghi `source_job` và `zalo_batch_targets` khi duyệt tay `dry_run=True` và ghi log/event `MANUAL_APPROVE_LEDGER_UPDATE_FAILED` chỉ chứa `error_type` (không lộ raw error text); tổng hợp dòng BCNB chưa quét thành `"Chưa quét Zalo"` và phân giải `lms_status` theo từng buổi `(class_code, lesson, class_date_iso)` qua `_resolve_lms_status_for_session` + `_match_session_entry` (ưu tiên execution/audit mới hơn preview cũ); ghi audit nguyên tử `begin_smtp_send_intent` (`BEGIN IMMEDIATE`, lọc `recipients_sha256` trong SQL trước `LIMIT 201`, trả `_overflow=True` vô điều kiện khi vượt trần, ghi `UNKNOWN` khi đứt kết nối trong/sau `send_message`).
  - **Gate G1–G5**: G1 (`unittest tests.test_api_routes_unit tests.test_zalo_parallel_service tests.test_lms_quality_service`: `111/111 OK`), G2 (`node tests/check_template_js.js` pass), G3 (`.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `637/637` tests OK, coverage `76.9%`), G4 (`git diff --check` exit `0` + manifest check `PASS`), G5 (`Sol 6.1 high` Round 9 `VERDICT: PASS`, session `01a18ddf-0483-7991-a4b3-6148b1db74a7`). Bước tiếp theo: triển khai Chặng 5.3 (đã có kết quả xác minh chỉ đọc `5.3.1a`).

- 2026-10-30 (Hoàn tất Chặng 5.2 `6/6 ✅` — Cô lập ghi BCNB: Tick vs Ghi chú `Mô tả vấn đề`, `DEC-039`):
  - Tách hoàn toàn lỗi ghi chú `BCNB_UPDATE_CHECKER_NOTE` khỏi `BatchFailurePolicy.record` ở cả đường `on_target_outcome` lẫn nhánh `except Exception as note_sync_err:` của đường notes-only (`checker_note_failures`, `bcnb.notes_only_sync_failed`); truyền `notes_only=True` ở nhánh unclaimed để không gọi `execute_verified_checkbox_action` ngoài ledger; ghi `SKIPPED_TICK_FAILED` khi tick checkbox lỗi.
  - Bảo vệ lạc quan chống ghi đè chỉnh sửa của QC (`read_checker_note_by_session` + `preserveDomNote` trước POST, `BcnbConflictAfterWriteError` / `CONFLICT_AFTER_WRITE` sau POST, ghi nhận rủi ro còn lại tại `DEC-039`); phân biệt đoạn auto và đoạn ghi tay theo 5 điều kiện provenance từ `external_action_audit` (`extract_proven_auto_issues_from_audits`, `_provenance_issue_key`, `preserve_bcnb_note_text`), bảo toàn byte-for-byte phần ghi tay (kể cả khoảng trắng đầu/cuối, `\n`, `|`, `;`, `[Auto QC: ...]` chưa chứng minh ownership).
  - Gate G1–G5: `unittest tests.test_bcnb_actions tests.test_zalo_parallel_service tests.test_bcnb_columns tests.test_bcnb_dom_contract` (`87/87 OK`), `node tests/check_template_js.js` pass, `.\CHECK_QUALITY.ps1 -Isolated` exit `0` (`QUALITY GATE PASSED`, coverage `77.2%`), `git diff --check` exit `0`; Sol 6.1 `high` R1–R3 `NEEDS_FIX` (đã sửa hết) -> R4 `VERDICT: PASS` (session `01a18cce-0a4d-77c1-a911-aef4f4d945a3`). Bước tiếp theo: triển khai Chặng 5.3.

- 2026-10-30 (Hoàn tất Chặng 5.1 `5/5 ✅` — Độ bền vòng quét Zalo):
  - Bọc `retry_target_once` và `setup_fresh_retry_page()` trong khối xử lý lỗi của target (`JobCancelledError` -> `JOB_CANCELLED`, `TargetTimeoutError` -> `TARGET_TIMEOUT`, `Exception` -> `SCAN_FAILED` + `policy.record(abnormal)`); thêm per-target timeout `ZALO_TARGET_TIMEOUT_SECONDS=180` (cô lập trong `CHECK_QUALITY.ps1`), tự đóng page treo và làm mới page cho target kế tiếp.
  - Kiểm tra hủy trong vòng quét khoảng trống lịch sử, áp trần mặc định `DEFAULT_BATCH_MAX_MESSAGES=500` trả `INSUFFICIENT_EVIDENCE` (`MESSAGE_CAP_REACHED`) thay vì `NOT_FOUND_VERIFIED`, và retry chụp ảnh minh chứng báo cáo 1 lần theo `MAX_CAPTURE_ATTEMPTS=2`.
  - Gate G1–G5: `unittest tests.test_zalo_parallel_service tests.test_zalo_scan_evidence` (`91/91 OK`), `node tests/check_template_js.js` pass, `.\CHECK_QUALITY.ps1 -Isolated` exit `0` (`627/627` tests OK, coverage `76.7%`), `git diff --check` exit `0`; Sol 6.1 `high` R1 `NEEDS_FIX` (đã sửa P2) -> R2 `VERDICT: PASS` (session `01a18c63-a713-7023-923a-8f87291b776b`). Bước tiếp theo: triển khai Chặng 5.2.

- 2026-10-30 (Hoàn tất Chặng 5.0 `4/4 ✅` — Baseline & Sao lưu trước Wave 5):
  - HEAD `b395b94ef92b70687239eec5542548274ffe8d01`; manifest baseline lưu ngoài repo (`$env:TEMP\wave5_5.0_baseline_manifest.json`).
  - Backup nóng SQLite: `.backups/db/pre-wave5-20261030-20261030-095820.sqlite3` (`1,134,592` bytes, SHA-256: `B9870B52A6F4916B5305607959BA81B47889F695867C571762F55BDC6C8E53F9`, `integrity="ok"`, `foreign_key_errors=[]`); restore drill `.backups/restore-drill.sqlite3` đạt `integrity="ok"`, `smoke={"scan_jobs": 11, "external_action_audit": 70, "schema_migrations": 4}`.
  - Baseline gate: `node tests/check_template_js.js` pass (`scripts=7 handlers=34 behavior=12`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `620/620` tests OK, coverage `76.6%`. Bước tiếp theo: triển khai Chặng 5.1.

- 2026-10-30 (Lập kế hoạch Wave 5 — Hardening flow kiểm tra báo cáo; chỉ sửa tài liệu):
  - Audit chỉ đọc trên HEAD `b395b94` (4 auditor; Lead đã xác minh lại trên source các lỗi P0: retry ở lần thử 2 làm hỏng batch, lỗi ghi chú làm dừng tick NX Zalo, nhánh notes-only vẫn tick ngoài ledger, slot LMS chọn theo vị trí, parser vòng đời chỉ có PT/G/S).
  - Kế hoạch: `UPGRADE_PROGRESS.md` Phase 5.0 → 5.8, `0/59`, có cổng G1–G6 và 6 quyết định Owner D1–D6. Review Sol 6.1 `high`: R1–R3 `NEEDS_FIX` đã sửa, R4 `VERDICT: PASS` (session `01a18bde-94ca-7cf3-a157-769051ab7fc7`).
  - Không thay đổi code, DB, `.env` hay browser state; chưa chạy test. Bước tiếp theo: Owner chốt D1–D6 (hoặc dùng mặc định) rồi duyệt bắt đầu Chặng 5.0.
  - 2026-10-30 15:38: Owner đã chốt D1–D6:
    - D1: nộp trễ không tính lỗi;
    - D2: báo cáo đúng ngày học nhưng không ghi hoặc ghi sai ngày vẫn khớp, chỉ thêm ghi chú QC;
    - D3: lên cấp B→A→I trong từng track JS/CS/PRE/ARM/SEMI/ROB4;
    - D4: "[LMS] cần kiểm tra thủ công" chỉ là ghi chú QC;
    - D5: được gửi nguyên văn tin nhắn cho AI;
    - D6: xoá luồng tuần tự cũ và CSV chết.
    - Plan đã cập nhật, thêm mức độ issue `FAIL`/`NOTE`. Phát hiện thêm: parser hiện tại đọc nhầm `JSB17`/`CSA03` thành track `S` (do dùng `.search` trên token). Sol 6.1 `high` R5 `NEEDS_FIX` → R6 `VERDICT: PASS` (session `01a18bf2-dd8a-79f0-aa45-3d68b761fcd6`).
    - Vẫn chỉ là plan, chưa sửa code.

- 2026-10-30 (Tinh chỉnh kiểm định chất lượng LMS, gỡ tiền tố `[Auto QC: ...]` trên Teaching & Mở rộng khung hiển thị Bảng giám sát Dashboard — source/local, `DEC-036`):
  - **Bỏ cảnh báo trùng lặp nhận xét học sinh trên LMS (`services/lms_quality_service.py`)**: `evaluate_lms_student_comments` không còn gắn lỗi `[LMS] Nhận xét HS copy-paste trùng lặp (...)` khi các nhận xét của học viên đi học (`ATTENDED`/`LATE_ARRIVED`) đã đạt chuẩn 3 ý (`Điểm mạnh`, `Điểm cần cải thiện`, `Lời khuyên`) hoặc chuẩn Checkpoint (`KIẾN THỨC`, `KỸ NĂNG`, `THÁI ĐỘ`).
  - **Gỡ tiền tố `[Auto QC: ...]` khi ghi lên Teaching (`services/bcnb_safety.py`, `repositories/report_repository.py`, `static/js/jobs.js`)**:
    - `merge_checker_issue_note` ghi trực tiếp `[LMS] ...; [Zalo] ...` (hoặc `f"{manual_part} | {joined_issues}"` khi có ghi chú tay), chuẩn hóa dấu `;`/`|` nội bộ trong một `raw_issue` thành `, ` và lọc bỏ cảnh báo `[LMS] Nhận xét HS copy-paste trùng lặp...` cũ.
    - `split_checker_issue_note` và `splitCheckerIssueNoteInJs` tương thích ngược với cả định dạng `[Auto QC: ...]` cũ lẫn định dạng `[LMS]`/`[Zalo]` trực tiếp mới, đảm bảo gộp lại không nhân đôi (idempotent), tự động xóa sạch ghi chú trên Teaching khi hết lỗi tự động và bảo toàn 100% ghi chú thủ công của QC (kể cả khi ghi chú thủ công chứa cụm `"copy-paste trùng lặp"` hoặc dấu `;`).
  - **Tinh gọn Giao diện Tab `Zalo Auto`, Mở rộng khung Bảng giám sát & Khắc phục lỗi `Bad Request Error 400` khi gửi Mail Gmail Web Compose (`services/report_email_service.py`, `templates/partials/dashboard.html`, `templates/partials/specs.html`, `static/css/app.css`, `static/js/jobs.js`, `tests/check_template_js.js`, `tests/test_lms_quality_service.py`)**:
    - Chuyển thanh trạng thái `#zalo-preflight` (`#zalo-preflight-status`, `#zalo-dashboard-plan-brief`, `#zalo-preflight-refresh`) từ trang chính `Zalo Auto` sang Tab `Chẩn đoán & Nhật ký` (`templates/partials/specs.html`).
    - Bỏ giới hạn co ép chiều cao Flexbox (`min-height: 0` trên `.tab-content` và `flex-1` trên `#tab-dashboard` / `.data-panel`), chuyển sang `flex: 1 0 auto !important` + `shrink-0` và `#tab-dashboard .table-scroll { overflow-y: visible !important }` (`min-height: 580px`) để bảng **Bảng giám sát tiến độ báo cáo nhóm Zalo** mở rộng hiển thị đầy đủ danh sách lớp theo thanh cuộn trang chính thay vì bị bó hẹp trong một khung nhỏ.
    - Khắc phục lỗi `Bad Request Error 400` trên `mail.google.com/mail/?view=cm&fs=1` khi gửi báo cáo nhiều lớp lỗi (như 22 lớp của W43/2026 làm URL sau percent-encoding phình lên `14,897` bytes vượt ngưỡng `8 KB` của Google Frontend): kiểm tra trực tiếp độ dài URL sau khi mã hóa (`len(gmail_compose_url) <= MAX_GMAIL_COMPOSE_URL_LEN = 5400`) và bổ sung tầng nén URL `compose_url_compact_body` giúp 22–25 lớp lỗi nằm gọn trong `~4,286` bytes URL (không bị cắt dòng, đồng thời tự động copy bảng 9 cột kẻ khung vào Clipboard để bấm `Ctrl+V` dán bảng màu đầy đủ vào Gmail).
    - Tắt tự động tải file CSV xuống máy khi nhấn nút **`Gửi Mail lớp lỗi`** (`#btn-export-weekly-csv` / `exportAndEmailFailedClassesCsv()`); chỉ gửi/mở khung soạn email báo cáo theo đúng yêu cầu của Owner.
    - Chuẩn hóa câu chữ ngắn gọn trong nhận xét và trạng thái báo cáo (`services/lms_quality_service.py`, `services/bcnb_safety.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`):
      - `[LMS] Nhận xét HS sơ sài, thiếu đủ 3 ý (...)` $\rightarrow$ `[LMS] Nhận xét thiếu ý`
      - `[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực (...)` $\rightarrow$ `[LMS] Nhận xét Checkpoint chưa đủ cấu trúc năng lực`
      - `[Zalo] Đã báo cáo nhưng ghi sai ngày/buổi trong mẫu — 'Bài số N' khớp với BCNB ...` $\rightarrow$ `[Zalo] Đã báo cáo nhưng ghi sai ngày/buổi ( Bài số N )`
      - `[Zalo] Đã báo cáo nhưng ghi sai ngày DD/MM — đã ghép theo BCNB W../...., ngày DD/MM, buổi N.` $\rightarrow$ `[Zalo] Đã báo cáo nhưng ghi sai ngày ( ghi ngày DD/MM nhưng lịch là ngày DD/MM, buổi N)`
      - `❌ Chưa tìm thấy trong khoảng đã kiểm chứng – Có minh chứng` $\rightarrow$ `Chưa nhận xét` (bảo toàn đầy đủ nút `✅ Duyệt & Tích`, `Quét lại`, `Copy nhắc GV` và `Quét lại lớp lỗi`).
    - Xử lý triệt để miễn báo cáo Zalo cho các lớp **Buổi 14** (`services/zalo_evidence.py`, `services/zalo_parallel_service.py`, `services/lms_quality_service.py`, `repositories/report_repository.py`, `static/js/jobs.js`):
      - Áp dụng `apply_lesson_14_exemption` ngay tại `_scan_one_target` (`services/zalo_parallel_service.py`) khi `lesson == "14"`, đảm bảo kết quả quét Buổi 14 luôn mang trạng thái `✅ Miễn báo cáo (Buổi 14)` (`status_detail = "Buổi 14 – miễn báo cáo theo quy tắc nghiệp vụ."`, `evidence_state = "LESSON_14_EXEMPTION"`, `submitted_for_bcnb = True`, `validation_errors = []`) ngay cả trước pha ghi BCNB hoặc khi trang BCNB bị timeout.
      - Bổ sung `_is_lesson_14_exempt_item` (`repositories/report_repository.py`) và `isLesson14ExemptRow` (`static/js/jobs.js`) để tự động chuẩn hóa mọi bản ghi Buổi 14 cũ về `✅ Miễn báo cáo (Buổi 14)` và `Đánh giá: ✅ Đã hoàn thành nhận xét` (khi LMS đã nộp), loại trừ hoàn khỏi diện cảnh báo/lỗi, nút `Quét lại`, `Copy nhắc GV`, `Quét lại lớp lỗi`, và danh sách lớp lỗi gửi Email/CSV; đồng thời cập nhật sạch dữ liệu các lớp Buổi 14 W43 (`VT-C4K-GA37`, `VT-C4K-PTA12`, `VT-JSB15`) trong `database.db`.
  - **Bằng chứng**: `git diff --check` exit `0`; `node tests/check_template_js.js` exit `0` (`scripts=7 handlers=34 behavior=12`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `620/620` tests OK, coverage `76.6%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a18788-3c79-7ad2-90b6-5f925fbd7939`).

- 2026-10-29 (Hotfix tải dữ liệu buổi học từ LMS trong luồng `ZALO_SCAN` & Kiểm chứng live 47/47 lớp Job `#400` — source/local + live read-only LMS fetch, `DEC-036`):
  - **Nguyên nhân lỗi `[LMS] Không tải được đầy đủ dữ liệu buổi học từ LMS, cần kiểm tra thủ công` trên Job `#400`**:
    1. Trong luồng `ZALO_SCAN`, trang trình duyệt đang mở ở `https://teachingmindx.top/check_nhanxet.php` (không phải `https://lms.mindx.edu.vn`), trong khi token xác thực Firebase của MindX LMS lưu trong **IndexedDB** (`firebaseLocalStorageDb` $\rightarrow$ `firebaseLocalStorage`) trên origin `lms.mindx.edu.vn` và gửi trong header `authorization` tới `https://lms-api.mindx.edu.vn/`.
    2. Schema GraphQL `classesMissAttendOrComment` yêu cầu tham số `filter_textSearch: $search, centre_in: $centres, status_in: $status, type: $type, pageIndex: $pageIndex, itemsPerPage: $itemsPerPage, orderBy: $orderBy`; việc bắn 47 truy vấn riêng lẻ làm `lms-api.mindx.edu.vn` bị timeout.
    3. Nhận xét Checkpoint HTML trên LMS dài `4,500–6,000` ký tự nên giới hạn cắt chuỗi `slice(0, 3000)` cũ làm mất phần `THÁI ĐỘ:` ở cuối.
  - **Khắc phục (`services/lms_quality_service.py`, `services/zalo_parallel_service.py`, `tests/test_lms_quality_service.py`)**:
    - Tự động mở tab tạm `temp_page` (luôn đóng và gỡ listener trong khối `try / finally` kể cả khi `CancelledError`), kiểm tra chặt `scheme == "https"` và `hostname == "lms.mindx.edu.vn"` trước & sau điều hướng trước khi gọi `_login_lms_if_needed`, bắt token từ request `POST https://lms-api.mindx.edu.vn/` kết hợp đọc IndexedDB `firebaseLocalStorageDb`.
    - Bổ sung **Tier-1 Bulk Query** (`classCodes.length > 3`, `itemsPerPage: 100`, lọc theo `centre_in` của cơ sở, chỉ chấp nhận khi phân trang đọc trọn vẹn `bulkComplete === true` với trang cuối `< 100` dòng) giúp tải toàn bộ 47/47 lớp chỉ trong 1 request (~14s, `0` lỗi tải), kèm **Tier-2 Per-Code Query** dự phòng (concurrency 3 + retry 1 lần).
    - Nâng giới hạn `s.summary.slice(0, 6000)` và `att.comment.slice(0, 15000)`, hỗ trợ nhận diện tiêu đề Checkpoint đánh số (`1. KIẾN THỨC`, `2. KỸ NĂNG`, `3. THÁI ĐỘ`), tách cụm động viên (`cần phát huy`, `cần duy trì`, `cần tiếp tục`) khỏi `IMPROVEMENT_SIGNALS`, và prefetch LMS 1 lần cho toàn bộ batch trong `services/zalo_parallel_service.py`.
    - Đã chạy cập nhật lại Job `#400` trong `database.db` (`47/47` lớp tải thành công, `0` lỗi fetch, `27/52` dòng có vấn đề chất lượng thực sự).
  - **Bằng chứng**: `git diff --check` exit `0`; `node tests/check_template_js.js` exit `0` (`scripts=7 handlers=34 behavior=12`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, coverage `76.4%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a1873f-906e-79e3-aceb-a1ebd5e6eb2a`).

- 2026-10-29 (Hoàn tất Wave 4 `4.0` → `4.4` `25/25 ✅` — Kiểm định chất lượng nội dung LMS/Zalo, Tự động ghi `Mô tả vấn đề (TE/TF/QC)` lên Teaching & Bảng Email Quản lý 9 cột — source/local, `DEC-036`, `DEC-038`):
  - **Chặng 4.0 (`3/3` ✅ — Sao lưu Pre-Wave 4)**: Sao lưu nóng SQLite `.backups/db/pre-wave4-20261029.sqlite3` (`40,898,560` bytes, `restore_drill_ok=true`, `integrity="ok"`, `foreign_key_errors=()`) và snapshot ZIP `.backups/pre-wave4-20261029/source-code-snapshot-20261029.zip` (`377` files) kèm `MANIFEST.json` & `ROLLBACK_INSTRUCTIONS.md`.
  - **Chặng 4.1 (`5/5` ✅ — Đọc mở rộng bảng Teaching & Đồng bộ trạng thái nộp LMS/Zalo)**: Bổ sung `resolve_extended_bcnb_columns` trong `services/bcnb_safety.py`; thu thập `bcnb_session_id`, `khoi`, `nx_zalo_checked`, `nx_lms_checked`, `lms_deadline`, `qc_issue_note`, `instructor_note`, `evaluation_status` trong `services/zalo_discovery_service.py` và `services/zalo_bcnb_service.py`; đồng bộ `lms_status` (`✅ Đã nộp LMS` / `❌ Chưa nộp LMS`) và `evaluation_status` vào `ReportRepository.get_weekly_summary` kèm khóa chống rò rỉ dữ liệu giữa các buổi trùng mã lớp hoặc trùng `session_id` (`VERDICT: PASS`, Session `01a185d0-5aeb-7a92-815f-5d889e616c20`).
  - **Chặng 4.2 (`6/6` ✅ — Động cơ kiểm định chất lượng LMS & Zalo)**: Tạo `services/lms_quality_service.py` + `LmsQualityGateway` (`services/external_gateways.py`, cô lập trong `-Isolated`); chấm `evaluate_lms_slot_summary`, phân biệt chuẩn **Buổi Thường** (3 ý: `Điểm mạnh`, `Điểm cần cải thiện`, `Lời khuyên`; cảnh báo viết sơ sài hoặc copy-paste trùng lặp) và **Buổi Checkpoint** (`Coding` B5/B9, `Robotics` B4/B8 — 3 trụ cột `KIẾN THỨC`, `KỸ NĂNG`, `THÁI ĐỘ`) cho học viên đi học (`ATTENDED`/`LATE_ARRIVED`) không lộ PII học viên, và kiểm tra lỗi ghi sai ngày/số buổi/tên bài trên Zalo (`VERDICT: PASS`, Session `01a18614-ef6e-7a50-90f8-2ee4afc5d03f`).
  - **Chặng 4.3 (`5/5` ✅ — Tự động ghi `Mô tả vấn đề (TE/TF/QC)` lên Teaching)**: Bổ sung `merge_checker_issue_note` / `split_checker_issue_note` (`services/bcnb_safety.py`) bảo toàn ghi chú tay của QC và cập nhật phần `[Auto QC: ...]` idempotent; `execute_verified_checker_note_action` (`services/bcnb_actions.py`) gửi `POST /check_nhanxet.php` (`action=update_note`, `note_type=checker`, `csrf_token`, `session_id`), đọc ngược DOM sau hard reload (`verify_checker_note_persisted` $\rightarrow$ `VERIFIED`) ở chế độ `production` (hoặc mô phỏng `DRY_RUN`) và lưu vết `BCNB_UPDATE_CHECKER_NOTE` vào `external_action_audit` (`VERDICT: PASS`, Session `01a18672-2bc8-7430-ae31-bf8a2ec24a7a`).
  - **Chặng 4.4 (`6/6` ✅ — Bảng Email Quản lý 9 cột, CSV lỗi chất lượng & Hiển thị Dashboard)**: Mở rộng `CSV_HEADERS` / `WEEKLY_CSV_HEADERS` và bảng Email lên 9 cột (`STT`, `Mã lớp`, `Buổi`, `Ngày học`, `Giảng viên`, `NX Zalo`, `NX LMS`, `Tình trạng đánh giá`, `Mô tả vấn đề (TE/TF/QC)`); `is_failed_weekly_summary_row` bao gồm cả các lớp có `issue_description` khác rỗng; Email hỗ trợ đồng thời HTML Table (`html_table`), Clipboard Rich HTML (`copyFailedEmailReportToClipboard` kèm cảnh báo và `window.prompt` fallback nếu trình duyệt chặn quyền Clipboard), bảng Unicode căn cột theo độ rộng hiển thị (`_display_width`) và 3 tầng co giãn URL Gmail (`text_body` $\rightarrow$ `compose_compact_body` $\rightarrow$ `compose_ultra_compact_body`, `max_compose_url_body_chars=6500`); hiển thị `LMS`, `Đánh giá`, `Vấn đề` và `Giải trình GV` trên Dashboard (`templates/partials/dashboard.html`, `static/js/jobs.js`) (`VERDICT: PASS`, Session `01a186d6-070e-77a1-81f1-d48f1cf46330`).
  - **Bằng chứng**: `git diff --check` exit `0`; `node tests/check_template_js.js` exit `0` (`scripts=7 handlers=34 behavior=12`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `617/617` tests OK, coverage `76.8%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` ở cả 4 chặng (`01a185d0-5aeb-7a92-815f-5d889e616c20`, `01a18614-ef6e-7a50-90f8-2ee4afc5d03f`, `01a18672-2bc8-7430-ae31-bf8a2ec24a7a`, `01a186d6-070e-77a1-81f1-d48f1cf46330`). Bước an toàn tiếp theo: khởi động lại ứng dụng khi không có job đang chạy, chạy dry-run quét Zalo/BCNB để xem trước kết quả kiểm định chất lượng và chỉ chạy production khi Owner duyệt.

- 2026-10-29 (Bổ sung chức năng Xuất CSV Lớp lỗi & Gửi Email Quản lý thay cho nút Xuất CSV Tuần — source/local, `DEC-036`):
  - **Giao diện & Luồng thao tác (`templates/partials/dashboard.html`, `static/js/jobs.js`, `static/js/state.js`)**: Thay nút `#btn-export-weekly-csv` từ `Xuất CSV Tuần` sang **`Xuất CSV lỗi & Gửi Mail`** (`onclick="exportAndEmailFailedClassesCsv()"`). Giữ `exportWeeklySummaryCsv(options)` tương thích ngược và bổ sung bộ lọc đồng nhất `isFailedWeeklySummaryRow` / `filterFailedWeeklySummaryRows` (lọc các lớp chưa đạt Zalo — không có `✅` và không có `Miễn` — hoặc lỗi/trễ LMS bắt đầu bằng `❌`/`⚠️`). Khi `sendEmail=true`, giao diện gọi 1 lần `POST /api/reports/send-failed-csv-email` và tải về đúng nội dung `csv_text` do máy chủ tạo (đồng nhất 100% với file đính kèm email); khi đứt kết nối phản hồi sau POST, cảnh báo `Chưa xác định trạng thái gửi email` và không tự tạo CSV cục bộ hay tự gửi lại.
  - **Backend & Bảo mật Email (`repositories/report_repository.py`, `services/report_email_service.py`, `api/zalo_routes.py`, `core/settings.py`, `googleform_autosubmit/config.py`, `.env.example`, `CHECK_QUALITY.ps1`)**:
    - Bổ sung `is_failed_weekly_summary_row`, `filter_failed_weekly_summary_rows`, tham số `failed_only: bool = False` trên `GET /api/reports/weekly-summary`, và endpoint `POST /api/reports/send-failed-csv-email` (`Depends(require_operator)`).
    - Khóa cứng allowlist email quản lý `MANAGER_EMAIL_ALLOWLIST = ("quangda@mindx.net.vn", "tuanpa@mindx.com.vn")`, mặc định giai đoạn thử nghiệm gửi về email Owner `DEFAULT_REPORT_EMAIL_RECIPIENTS = ("quangda@mindx.net.vn",)` (có thể bật cả 2 quản lý qua `REPORT_EMAIL_RECIPIENTS=quangda@mindx.net.vn,tuanpa@mindx.com.vn` trong `.env`).
    - Xác minh chứng chỉ TLS máy chủ bằng `ssl.create_default_context()` cho cả `smtplib.SMTP_SSL` (port 465) và `smtp.starttls()`; kiểm tra kết quả trả về của `smtp.send_message()` để phát hiện người nhận bị từ chối một phần (`502 SMTP_RECIPIENT_REFUSED` kèm `accepted_recipients`, `refused_recipients`, `csv_text`); hỗ trợ mở nhanh khung soạn Gmail điền sẵn (`SMTP_NOT_CONFIGURED`) khi chưa khai báo `SMTP_USERNAME`/`SMTP_PASSWORD` trong `.env`.
  - **Bằng chứng**: `git diff --check` exit `0`; `node tests/check_template_js.js` exit `0` (`scripts=7 handlers=34 behavior=12`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, coverage `76.4%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a183bf-39d4-78d0-8eb4-158bbb9c68cb`).

- 2026-10-28 (Trang trí lại Giao diện Tactile High-Contrast 3D Embossed đồng bộ toàn bộ 4 Tab & Modal + Tinh gọn Quy trình — source/local):
  - **Hệ thống Thiết kế Tactile High-Contrast 3D Embossed (`static/css/app.css`, `static/js/tailwind-config.js`, `templates/partials/*.html`, `static/js/state.js`)**: Đồng bộ toàn bộ 4 Tab (`Zalo Auto`, `Nộp Google Form`, `Cấu hình Nhóm`, `Chẩn đoán & Nhật ký`) và `Modal Tick LMS` theo mẫu Gemini Canvas (`https://gemini.google.com/app/1089da51c60f9d2a`): nền `#DCE3EC` chấm bi kỹ thuật, Sidebar Deep Navy `#0B1120` kèm huy hiệu `PRO OPS v2.0` và trạng thái `Zalo Worker Engine — ONLINE`, thẻ nổi `#FFFFFF` viền `1.5px solid #94A3B8` đế 3D `4px solid #64748B`, 4 thẻ KPI 3D có dải màu đỉnh `6px` + hộp icon 3D màu khối đặc, nút bấm 3D Tactile đạt chuẩn tương phản WCAG AA ($\ge 4.5:1$ trên cả hai đầu dải màu), khung lõm `.well-inset` / `.input-3d`, và bảng có thanh tiêu đề Dark Slate `#0F172A`.
  - **Hướng 1 (Tab 1 `Zalo Auto` — `templates/partials/dashboard.html`, `static/css/app.css`)**: Phân tách 7 nút thành Cụm thực thi chính 3 bước đánh số (`1. Dò kế hoạch`, `2. Bắt đầu quét`, `3. Tick LMS`) và Cụm công cụ hậu kiểm (`Cần xem lại`, `Quét lại lớp lỗi`, `Copy nhắc GV`, `Xuất CSV Tuần` đã bỏ icon trùng lặp); gộp thanh Tìm kiếm và Lịch sử tuần (`#history-week-select`, `#history-job-select`, `#btn-load-weekly-history`, `#weekly-history-status-badge`) vào rãnh lõm `.well-inset` trên 1 hàng ngang.
  - **Hướng 2 (Tab 2 `Nộp Google Form` — `templates/partials/googleform.html`, `static/js/googleform.js`)**: Thu gọn `Cấu hình Mẫu Form (Q&A Mapping)` thành thanh `<details id="googleform-mapping-panel">` ở đầu trang kèm badge trạng thái `#mapping-count-badge` (mặc định `Chưa xác minh cấu hình`, kiểm tra `res.ok` và báo `Không tải được cấu hình` khi lỗi HTTP/mạng); tái bố cục phần chính thành 3 cột thẻ 3D có Header Dark Slate (`1. Nguồn Học viên LMS / AI` $\rightarrow$ `2. Danh sách Học viên chờ nộp` kèm nút `Bắt đầu Nộp Form` $\rightarrow$ `3. Lịch sử Nộp Form`).
  - **Hướng 3 (Gộp Tab 4 `Chẩn đoán & Nhật ký` & Lọc nhiễu Log — `templates/partials/sidebar.html`, `templates/partials/specs.html`, `templates/partials/logs.html`, `static/js/state.js`, `static/js/jobs.js`)**: Rút gọn Sidebar hiển thị 4 tab (`Zalo Auto`, `Nộp Google Form`, `Cấu hình Nhóm`, `Chẩn đoán & Nhật ký`) và giữ `#btn-logs` ẩn tương thích ngược; `switchTab('specs')` và `switchTab('logs')` cùng kích hoạt `#tab-specs` và `#tab-logs` trên 1 trang cuộn hợp nhất; `appendLogLines()` lọc bỏ dòng `http.completed` / `HTTP request hoàn tất.` thành công (`2xx`) nhưng bảo toàn đầy đủ bản ghi HTTP lỗi (`>= 400` hoặc `ERROR`).
  - **Hướng 4 (Chuẩn hóa độ tương phản & Responsive Mobile — `templates/partials/lms_modal.html`, `templates/partials/groups.html`, `static/js/jobs.js`, `static/js/groups.js`, `static/js/googleform.js`)**: Đồng bộ Header Dark Slate `#0F172A`, khung đối chiếu viền màu độ tương phản cao và ẩn gọn phần tử phụ trên màn hình nhỏ (`<= 767px`); giữ nguyên 100% DOM contract IDs và chốt an toàn.
  - **Bằng chứng**: `git diff --check` exit `0`; `node tests/check_template_js.js` exit `0` (`scripts=7 handlers=34 behavior=12`); `.\venv\Scripts\python.exe -B tests\check_phase3_quality.py` exit `0` (`phase3-quality-gate-ok`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, 597 test, coverage `76.2%`; Independent Review `gpt-6.1-sol (high)` đạt `VERDICT: PASS` (Session `01a1824e-8270-7101-b10a-04ea7eb63d4c`).

- 2026-10-28 (Sửa lỗi khóa nút "Xác nhận kế hoạch" khi quét Web LMS có lớp đã tích sẵn trên BCNB — source/local):
  - Nguyên nhân (Job #9): Khi quét trực tiếp Web LMS (`use_web_lms=True`), lớp `VT-ROB-PREA16 (Buổi 9)` xuất hiện trong danh sách quá hạn trên LMS nhưng dòng `VT-ROB-PREA16` trên BCNB tuần `43/2026` đã được tích sẵn (`checked=True`). `build_lms_plan` vẫn áp dụng chốt chặn của chế độ OCR cũ (`Lớp được OCR là chưa làm nhưng BCNB đã tick`), làm `can_confirm=False`, `confirmation_token=None` và khóa nút **Xác nhận kế hoạch** dù còn 41 lớp hợp lệ trong cột **Dự kiến tick**.
  - Sửa: Bổ sung `allow_checked_missing: bool = False` trong `services/lms_plan.py` (`build_lms_plan`) và truyền `allow_checked_missing=use_web_lms` / `allow_web_mode` tại `services/lms_service.py` (cả bước lập kế hoạch và bước đối chiếu `fresh_plan` trước khi thực thi). Các lớp `missing_but_checked_classes` vẫn được giữ trong `plan`, gắn nhãn `BCNB: Đã tích ⚠️` ở cột **Không tick**, ràng buộc vào `plan_digest` và loại trừ tuyệt đối khỏi `classes_to_tick`; chế độ OCR ảnh truyền thống vẫn chặn như cũ.
  - Bằng chứng: `tests.test_lms_plan` + `tests.test_lms_service` (24 test OK); `node tests/check_template_js.js` pass (`scripts=7 handlers=33 behavior=12`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, 597 test, coverage `76.2%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a180cf-809d-7d01-8534-c8b1228e08c5`).

- 2026-10-28 (Cập nhật độ phân giải trình duyệt Chrome mặc định `1920 x 1200` — source/local):
  - Cấu hình `DEFAULT_BROWSER_VIEWPORT = {"width": 1920, "height": 1200}` và cờ `--window-size=1920,1200` trong `services/external_gateways.py` (`_persistent_launch_options`), đồng bộ `viewport={"width": 1920, "height": 1200}` tại `services/lms_service.py` (3 điểm gọi `persistent_page`), `googleform_autosubmit/form_submitter.py` (`new_context`), và `DANG_NHAP_ZALO.bat` (`--window-size=1920,1200`).
  - Bằng chứng: targeted 127 test OK; `node tests/check_template_js.js` pass (`scripts=7 handlers=33 behavior=12`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, 597 test, coverage `76.2%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a180c0-9ada-7c32-8588-c9228628a057`).

- 2026-10-28 (Sửa tương thích cấu trúc BCNB mới — source/local, `DEC-037`):
  - Thay đổi live (kiểm tra chỉ đọc, chặn mọi request không phải GET): cột `STT` chèn ở vị trí 0 đẩy `Giảng viên chính` từ cột 3 sang cột 4 (`cells[3]` thành `Ngày học`); tiêu đề `NX Zalo`/`NX LMS`; nút lọc tuần là nhãn `Tuần (dd/mm/yyyy - dd/mm/yyyy)` trong form GET (`#week-input`, `#year-input`); `hocsinh.php?datatable=students` phân trang 15 dòng, bỏ qua `length=-1`, có 22/1828 dòng `class_status=null`.
  - Sửa: `services/bcnb_safety.py` (nhận cột `NX ...` khớp chính xác; thêm `resolve_instructor_column` tìm cột `Giảng viên chính` theo header ngữ nghĩa, bỏ qua `Trợ giảng`/`Ghi chú Giảng viên`; `isSafeTrigger` dùng chung cho mọi đường chọn tuần; xác nhận `selectedWeek/selectedYear` do server render), `services/zalo_discovery_service.py` (`_read_bcnb_week_rows` dùng `resolve_instructor_column` thay cho `cells[3]` cố định), `services/zalo_discovery_collection.py` (`complete_class_status_url` thêm `start=0&length=10000`, giữ kiểm tra đủ dòng; null/rỗng → `UNKNOWN`). Test mới `tests/test_bcnb_dom_contract.py` + bổ sung `test_bcnb_columns.py`, `test_bcnb_week_selector.py`, `test_zalo_discovery_service.py`.
  - Bằng chứng: live chỉ đọc PASS (tuần 44→43, cột `(1,2,8)`/`(1,10)`/`(1,2,3,7)`/`instructor=4`, 47/47 dòng có đủ 2 checkbox và parse được ngày + deadline, 266 trạng thái lớp, `BLOCKED_NON_GET: []`); targeted 48 test OK; `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, 597 test; `git diff --check` exit `0`; Sol 6.1 xhigh `VERDICT: PASS`.
  - Rủi ro mở: 1 lần gate trước đó (code vòng 2) fail Phase 9A do coverage `bcnb_safety.py` 57.21% < 75; chạy lại riêng lẻ/đầy đủ đều >85% và gate cuối PASS — nghi coverage chập chờn khi test Playwright thật chạy giữa bộ test; theo dõi.
  - Chưa làm: chưa bấm checkbox thật trên BCNB (luồng ghi `updateCheckbox` chưa được canary). Bước an toàn tiếp theo: Owner chạy Dò tìm lại trên server đã khởi động lại, chạy dry-run quét Zalo/BCNB và xem bản xem trước; chỉ chạy production với một target cụ thể khi Owner duyệt.

- 2026-10-23 (Hoàn tất Phase 3.2 — Tra cứu Lịch sử theo Tuần & Xuất báo cáo CSV/Excel `5/5` ✅ — Hoàn thành 100% lộ trình `UPGRADE_PROGRESS.md` theo flow `DEC-036`):
  Đã thực thi và nghiệm thu độc lập bởi `gpt-6.1-sol (xhigh)`:
  - Bổ sung `ReportRepository.get_weekly_summary`, `build_weekly_summary_csv`, `sanitize_csv_cell` trong `repositories/report_repository.py` và endpoint `GET /api/reports/weekly-summary` trong `api/zalo_routes.py` (hỗ trợ lọc `week`, `year`, `job_id`, và `format=csv` trả về file đính kèm UTF-8 có BOM `\ufeff`).
  - Bảo toàn ranh giới `snapshot_id` liên tiếp khi gộp các job quét chọn lọc (`selected_target_keys`) lên bản quét toàn bộ nền; kết hợp vết duyệt tay `BCNB_MANUAL_APPROVE_TICK` (`production` + `VERIFIED`); tách bạch số liệu `dry_run_count` (`lms_dry_run`) khỏi `ticked_count` (`lms_completed`); phân loại báo cáo nộp trễ (`🟡 Đã nộp trễ – Có minh chứng`) vào `zalo_late`/`zalo_pending` và không bao giờ gộp vào `zalo_exempt`; bảo toàn danh sách tuần lịch sử kể cả khi cơ sở dữ liệu có trên `200` job quét.
  - Bổ sung bộ điều khiển tra cứu lịch sử theo tuần/job (`#history-week-select`, `#history-job-select`, `#btn-load-weekly-history`, `#weekly-history-status-badge`) và nút **"📥 Xuất CSV Tuần"** (`#btn-export-weekly-csv`) trên `templates/partials/dashboard.html`, `static/js/state.js` và `static/js/jobs.js` (chống đua bất đồng bộ bằng `weeklyHistoryRequestSeq` và chụp `snapshotFallbackRows` trước `await fetch`).
  - Bằng chứng: `git diff --check` exit `0`; `node tests/check_template_js.js` pass (`scripts=7 handlers=33 behavior=12`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, 584 test, coverage `76.0%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a166a8-d27b-7fa1-91e2-81bfdda63240`). Toàn bộ 8 giai đoạn trong `UPGRADE_PROGRESS.md` đã đạt `100%` (`45/45` checklist items).

- 2026-10-23 (Hoàn tất Phase 3.1 — Bộ phân loại Báo cáo Zalo Hybrid Rule-based + AI gợi ý `5/5` ✅ theo flow `DEC-036`):
  Đã thực thi và nghiệm thu độc lập bởi `gpt-6.1-sol (xhigh)`:
  - Bổ sung cấu hình `ZALO_AI_ASSIST_CLASSIFIER=false` (mặc định tắt) trong `googleform_autosubmit/config.py`, `.env.example` và cô lập trong `CHECK_QUALITY.ps1`.
  - Bổ sung bộ lọc đủ điều kiện gọi AI phụ (`is_eligible_for_ai_assist`, `AI_ASSIST_MIN_MESSAGE_CHARS = 80`, `MAX_AI_ASSIST_CANDIDATES = 3`) trong `classifiers/rule_classifier.py`: loại bỏ tin nhắn đã khớp rule, tin nhắn ngắn `< 80` ký tự hoặc dính `penalty:class_manager_or_opening_notice`.
  - Bổ sung `ZaloAiReportClassifierGateway` và `GeminiZaloReportClassifierGateway` trong `services/external_gateways.py` (tự động fail-closed trong chế độ `-Isolated`, khi thiếu API key hoặc khi timeout/lỗi).
  - Tích hợp chấm phụ trong `services/zalo_scan_evidence.py`, `services/zalo_evidence.py`, `services/zalo_parallel_service.py` và `services/zalo_service.py`: chỉ gọi AI khi `not report_found`, có ngày buổi học hợp lệ (`target` hoặc `target_date`), tin nhắn nằm trong cửa sổ ngày học (`is_in_window`) và giới hạn tối đa `3` lượt gọi AI cho mỗi nhóm trong toàn bộ một job quét (`_try_claim_ai_assist_group_slot`). Khi AI gợi ý có thể là báo cáo, chụp ảnh bằng chứng `images["report"]` nhưng tuyệt đối giữ `report_found=False`, `submitted_for_bcnb=False`, `evidence_state="INSUFFICIENT_EVIDENCE"`, `decision="AI_SUGGESTED_REPORT_NEEDS_REVIEW"`, và trạng thái `⚠️ AI gợi ý có thể là báo cáo – Cần kiểm tra thủ công` để người vận hành duyệt qua Hạng mục 1.3.
  - Bằng chứng: `node tests/check_template_js.js` pass (`scripts=7 handlers=29 behavior=11`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, 582 test, coverage `76.6%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a1666f-96d1-7880-bee8-2903a96e14c4`). Bước tiếp theo: triển khai **Phase 3.2** (Tra cứu Lịch sử theo Tuần & Xuất báo cáo CSV/Excel).

- 2026-10-23 (Hoàn tất Phase 2.2 — Lấy danh sách học viên trực tiếp từ LMS cho Google Form `6/6` ✅ theo flow `DEC-036`):
  Đã thực thi và nghiệm thu độc lập bởi `gpt-6.1-sol (xhigh)`:
  - Bổ sung chuẩn hóa danh sách mã lớp LMS (`normalize_lms_student_class_codes`, `MAX_LMS_STUDENT_CLASSES = 20`, `MAX_LMS_STUDENTS_PER_BATCH = 200`) trong `services/lms_plan.py`.
  - Bổ sung luồng trích xuất học viên trực tiếp từ LMS (`_read_lms_students_by_classes`, `run_googleform_fetch_lms_students` trong `services/lms_service.py`), kiểm tra `locator.count()` trước `.all()` (`_bounded_locator_items`), xác minh quyền sở hữu lớp và khớp duy nhất mã lớp xuyên trang (`_find_and_open_lms_class_in_table`), duyệt phân trang đầy đủ cả bảng và detail locators, và dừng fail-closed khi có học viên trùng tên trong cùng một lớp.
  - Tạo endpoint `POST /api/googleform/fetch-lms-students` (`api/googleform_routes.py`), job type `GOOGLEFORM_LMS_STUDENTS` (`services/job_dispatcher.py`), và giao diện nhập mã lớp lấy nhanh học viên từ LMS trên tab Google Form (`templates/partials/googleform.html`, `static/js/googleform.js`, `static/js/jobs.js`, `static/js/state.js`).
  - Bảo toàn khóa chống gửi trùng (`ensureGoogleFormIdempotencyKey` kèm `googleformBatchMarker` in-memory khi thiếu `localStorage`) kể cả khi tải lại cùng danh sách học viên; ẩn/loại bỏ hoàn toàn tên học viên khỏi log và tên file ảnh chụp màn hình trong `services/googleform_service.py` và `googleform_autosubmit/form_submitter.py`.
  - Bằng chứng: `node tests/check_template_js.js` pass (`scripts=7 handlers=29 behavior=11`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, coverage `76.6%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a16550-9aa8-7042-99ee-3f1ab8a101fe`). Bước tiếp theo: triển khai **Phase 3.1** (Bộ phân loại Báo cáo Zalo Hybrid Rule-based + AI gợi ý).

- 2026-10-23 (Hoàn tất Phase 2.1 — Nâng cấp bảng Xem trước LMS chi tiết & Chọn từng lớp `6/6` ✅ theo flow `DEC-036`):
  Đã thực thi và nghiệm thu độc lập bởi `gpt-6.1-sol (xhigh)`:
  - Trích xuất, gộp theo token và ràng buộc `overdue_details` (`class_code -> "Buổi ..."` ) vào `plan_digest` trong `services/lms_plan.py` và `services/lms_service.py`.
  - Hiển thị số buổi nợ nhận xét + trạng thái BCNB (`BCNB: Chưa tích`, `BCNB: Đã tích ⚠️`, `Ngoài BCNB tuần này`) trên cột lớp nợ nhận xét trong `templates/partials/lms_modal.html`, `static/js/jobs.js` và nội dung copy nhắc giáo viên (`static/js/lms.js`).
  - Bổ sung chọn/bỏ chọn từng lớp tích BCNB (`selected_classes_to_tick`), nút Chọn tất cả / Bỏ chọn tất cả và khóa `lmsConfirmInFlight` chống thao tác cạnh tranh khi đang gửi `/api/lms/confirm`.
  - Ràng buộc toàn vẹn tập con được chọn bằng `selection_digest` (`compute_selection_digest`) lưu trong `lms_confirmations.plan_payload` và `scan_jobs.request_payload`; tại bước thực thi `run_lms_automation`, `LmsRepository.get_consumed_selection_digest` tra cứu theo `execution_job_id` trước và xác minh `analysis_job_id`, `plan_digest`, `selection_digest` fail-closed trước khi mở trình duyệt.
  - Bằng chứng: `node tests/check_template_js.js` pass (`scripts=7 handlers=28 behavior=10`); `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, 575 test, coverage `77.0%`; Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session `01a164b5-4caa-70e0-a0e9-ac5331a0df46`). Bước tiếp theo: triển khai **Phase 2.2** (Lấy danh sách học viên trực tiếp từ LMS cho Google Form).

- 2026-10-22 (Hoàn tất Phase 0 & Phase 1 — `1.1`, `1.2`, `1.3` theo flow `DEC-036`):
  Đã thực thi và nghiệm thu độc lập bởi `gpt-6.1-sol (xhigh)` toàn bộ Phase 0
  (Backup nóng SQLite + Snapshot ZIP + `CHANGELOG.md`) và Phase 1:
  - **Phase 1.1 (`5/5` ✅)**: Copy danh sách nhắc giáo viên 1-Click cho Zalo & LMS
    (`copyZaloTeacherReminders`, `copyLmsMissingClasses`), khóa theo tuần bắt buộc.
  - **Phase 1.2 (`6/6` ✅)**: Quét lại có chọn lọc (`selected_target_keys` trên
    `POST /run-scan`), lọc target trong `prepare_frozen_scan_execution` và
    `update_bcnb_reports`, hợp nhất kết quả vào bảng hiện tại theo đúng
    `(periodKey, snapshotId)`.
  - **Phase 1.3 (`6/6` ✅)**: Duyệt tay & Tích BCNB 1-Click có lưu vết
    (`POST /api/zalo-manual-approve` -> job `ZALO_MANUAL_APPROVE` ->
    `run_zalo_manual_approve` -> `BCNB_MANUAL_APPROVE_TICK` kèm SHA-256 ảnh bằng
    chứng thuộc `source_job_id` và `approved_by`; tách khóa `action_key` theo
    `execution_mode`, chỉ đánh dấu `✅ Đã duyệt tay & Tích BCNB` khi `production`
    + `VERIFIED`).
  - Bằng chứng: `node tests/check_template_js.js` pass (`scripts=7 handlers=27 behavior=9`);
    `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, coverage `77.0%`;
    Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS` (Session
    `01a16475-9d98-75b2-b895-c7804835822d`). Bước tiếp theo: triển khai **Phase 2.1**
    (Bảng Xem trước LMS chi tiết buổi nợ & chọn từng lớp).

- 2026-10-20 (Nâng cấp tính năng Tự động Tick LMS quét trực tiếp web LMS & BCNB):
  Nâng cấp luồng `LMS_ANALYSIS` để worker tự động truy cập trang MindX LMS
  (`Config.LMS_URL` — `admin/class-sessions`), tự động đăng nhập nếu phiên bị
  thoát bằng `LMS_USERNAME` / `LMS_PASSWORD`, quét qua các trang phân trang và
  lọc đúng những lớp có hiện số buổi tại cột `"Buổi học gần nhất quá hạn nhận xét"`
  là những lớp chưa nhận xét. Sau đó worker chuyển sang trang BCNB (`Config.BCNB_URL`
  — `check_nhanxet.php`, tự động đăng nhập bằng `BCNB_USERNAME` / `BCNB_PASSWORD`
  nếu bị thoát), lấy danh sách lớp của tuần kiểm tra làm gốc, loại trừ các lớp
  quá hạn nhận xét trên LMS (không chặn xác nhận nếu LMS có thêm lớp thuộc khóa/tuần
  khác ngoài bảng BCNB hiện tại) và hiển thị bảng xem trước (`Không tick` vs
  `Dự kiến tick`) trước khi xác nhận thực thi. Đã bổ sung biến `LMS_USERNAME`,
  `LMS_PASSWORD`, `LMS_URL` vào `.env`, `.env.example`, `googleform_autosubmit/config.py`
  và cô lập biến `LMS_URL` trong `CHECK_QUALITY.ps1`. Bằng chứng:
  `node tests/check_template_js.js` pass (`scripts=7 handlers=24 behavior=6`);
  `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `567` test,
  coverage `77.1%`. Bước an toàn tiếp theo là Owner điền tài khoản/mật khẩu thật
  của LMS và BCNB vào file `.env` và bấm `Tick LMS` -> `Quét LMS & Lập kế hoạch`
  để kiểm tra bảng xem trước.

- 2026-10-20 (Tinh gọn Trang chính Zalo Auto và tách Tab Thông số kỹ thuật):
  Trước khi sửa giao diện, đã sao lưu SQLite (`pre-ui-redesign-20261016-025713.sqlite3`,
  `integrity=ok`), cấu hình `.env`, snapshot source/UI tại
  `.backups/pre-ui-redesign-20261016/` và gắn Git tag
  `checkpoint-pre-ui-redesign-20261016` tại commit `ab3ad0b`. Trang chính
  `Zalo Auto` (`templates/partials/dashboard.html`) được rút gọn còn 4 thẻ thống
  kê thu gọn, 1 thanh điều khiển kèm tóm tắt trạng thái an toàn trước nút `Bắt
  đầu quét` (giữ nguyên chốt khóa nút theo DEC-032) và bảng kết quả chính chỉ
  hiển thị trạng thái cùng nút xem ảnh minh chứng. Thêm tab riêng `Thông số kỹ
  thuật` (`tab-specs` / `templates/partials/specs.html`) trên Sidebar để gom
  toàn bộ thông số chẩn đoán: kế hoạch 2 tuần đầy đủ (`#zalo-scan-plan`), quy
  trình 3 bước (`#zalo-run-workflow`), danh sách kiểm tra preflight
  (`#zalo-preflight-checks`), telemetry phiên job (`#job-meta`, `reason_code`,
  14 chỉ số `#zalo-target-progress`) và bảng thông số kiểm chứng tin nhắn/cửa sổ
  thời gian theo từng nhóm (`#specs-evidence-table-body`). Không thay đổi API
  backend, database, `.env` hay luồng trình duyệt. Bằng chứng: `git diff --check`
  pass; `node tests/check_template_js.js` pass (`scripts=7 handlers=24 behavior=6`);
  `tests/check_phase3_quality.py` và `tests/check_app_smoke.py` pass;
  `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, `564` test,
  coverage `77.0%`. Bước an toàn tiếp theo là Owner mở dashboard local để kiểm
  tra trực quan trang chính và tab Thông số kỹ thuật.

- 2026-09-26 (Fix lỗi Discovery Job #295 và badge mode): Đã sửa điểm khiến
  lỗi mở/đọc BCNB hoặc đọc nhóm Zalo bị đẩy lên dispatcher thành thông báo mơ
  hồ “Worker dừng an toàn do lỗi nội bộ”. Các lỗi trình duyệt/nguồn ngoài giờ
  được chuyển thành `DiscoverySafetyError`, job dừng fail-closed với hướng dẫn
  tiếng Việt có thể làm theo; log chỉ ghi loại lỗi an toàn, không ghi traceback,
  cookie hay dữ liệu thô. Badge trên header giờ lấy mode từ job đang xem (kể cả
  `mutates_external` khi API không trả trực tiếp mode), nên job
  `ZALO_DISCOVERY` #295 hiện là `Dò tìm · chỉ đọc`, không còn hiện nhầm
  `PRODUCTION`; preflight không ghi đè mode của job hiện tại. Regression mới:
  kiểm tra BCNB navigation failure, lỗi đọc nhóm Zalo và mode production/dry-run
  của UI. Bằng chứng: `node tests/check_template_js.js` pass;
  `tests.test_zalo_discovery_service` + `tests.test_job_dispatcher_unit` 35/35
  pass; `CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, 562
  test, coverage 76,9%. Chưa chạy Discovery live, Zalo live hoặc ghi BCNB thật.

- 2026-09-26 (UI-UX Phase 1): Đã làm gọn luồng dashboard Zalo trên nền
  checkpoint `12c465c` (`chore: checkpoint before UI UX refresh`). Khu vực kế
  hoạch và preflight được đưa lên trước nút chạy; thêm workflow 3 bước để Owner
  biết đang ở Dò tìm, Kiểm tra an toàn hay Bắt đầu quét; mode Production/Dry-run
  hiển thị từ response preflight thay vì chữ cố định; nút quét bị khóa khi chưa
  có preflight hoặc gặp RED; response status lạ cũng fail-closed như RED; các
  nhãn hành động được đổi sang tiếng Việt dễ hiểu. Phạm vi source UI gồm 5 file
  giao diện/JavaScript/CSS; thêm một kiểm
  tra frontend và ba tài liệu trạng thái/quyết định. Không đổi API, database,
  `.env`, browser profile, cookie hoặc dữ liệu thật. Bằng chứng mới:
  `git diff --check` pass; `node tests/check_template_js.js` pass với
  `scripts=7 handlers=24 behavior=6`; `tests/check_app_smoke.py` pass;
  `CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`, 560 test,
  coverage 76,9%. Chưa mở trình duyệt để kiểm tra trực quan và chưa chạy
  Zalo/BCNB thật; Phase UI-2 (báo cáo sau job/lịch sử nâng cao) chưa bắt đầu.

- 2026-09-26 (đợt dọn code/UI trước): Hoàn tất theo phạm vi `Gọn giao diện`;
  đã xóa phần xác minh thủ công không có API backend khỏi `static/js/jobs.js` và
  `static/css/app.css`, xóa hai nhãn thông tin cố định khỏi dashboard/sidebar,
  bỏ phép gán evidence bị ghi đè và sửa mô tả giao diện trong
  `PROJECT_SUMMARY.md`. Không thay đổi Zalo/BCNB, database, `.env`, API
  deprecated hoặc dữ liệu live. Kiểm tra mới: `node tests/check_template_js.js`
  pass; targeted `tests.test_app_factory` + `tests.test_api_routes_unit`
  27/27 pass; `git diff --check` pass; `CHECK_QUALITY.ps1 -Isolated` pass với
  550 test, coverage 77.1%, Ruff/mypy, các invariant, schema, app smoke và
  frontend contract đều pass. Chưa chạy quét Zalo/BCNB thật; bước an toàn tiếp
 theo là mở local dashboard để kiểm tra trực quan các tab trước khi sử dụng.

- 2026-09-26: Đã triển khai phần ổn định và công cụ cho Owner trong phạm vi
  source/local, chưa chạy Zalo Web hoặc ghi BCNB thật. Có preflight tổng hợp tại
  `services/zalo_preflight.py` và panel trên dashboard với ba mức GREEN/YELLOW/
  RED; RED chặn cả giao diện lẫn `/run-scan`, YELLOW production cần Owner xác
  nhận rõ ràng, còn server kiểm tra lại ngay trước khi tạo job. Việc tạo job
  Zalo production/dry-run còn có chốt SQLite nguyên tử để hai lần bấm cạnh
  tranh không cùng được nhận. Production chỉ dùng một worker Zalo trên mỗi
  profile; dry-run giữ số worker cấu hình. Thêm
  báo cáo đếm kết quả và `review_queue` chỉ đọc cho target thiếu bằng chứng,
  lỗi kỹ thuật, `UNKNOWN` hoặc batch halt; không tự ghi BCNB. Nhãn cảnh báo
  HTML/PHP từ dữ liệu nhóm/giáo viên được thay bằng thông báo an toàn, không
  fuzzy-match.

  Bằng chứng kiểm tra mới: `node tests/check_template_js.js` pass;
  PowerShell `$env:PYTHONPATH='.'; .\venv\Scripts\python.exe -X utf8
  tests/check_app_smoke.py` pass; targeted combined unittest `102/102`, exit
  `0` bằng lệnh PowerShell `$env:PYTHONPATH='.';
  .\venv\Scripts\python.exe -X utf8 -m unittest tests.test_api_routes_unit
  tests.test_zalo_preflight tests.test_audit_repository
  tests.test_production_readiness tests.test_app_factory
  tests.test_zalo_search_timeout_lifecycle tests.test_zalo_parallel_service -v`
  (gồm API route, preflight backup, audit, production readiness, timeout
  lifecycle, app factory và parallel service); route + job lifecycle `35/35`
  cũng pass, gồm test admission nguyên tử; `git diff --check` pass. Cổng
  đầy đủ `.\CHECK_QUALITY.ps1 -Isolated` exit `0`, `QUALITY GATE PASSED`,
  `560` test pass, coverage `76,9%`, Ruff/mypy, invariant, frontend contract,
  app smoke và compile đều pass. Không xuất hiện cảnh báo
  `Future exception was never retrieved` trong các test lifecycle.

  Giới hạn bằng chứng: chưa kiểm tra trực tiếp popup “đang mở Zalo trên Tab
  khác”, nhiều profile production, dry-run live, canary hoặc production batch;
  các mục này vẫn là human gate của Owner. Task không đổi `.env`, database,
  browser profile, cookie hoặc dữ liệu thật. Bước an toàn tiếp theo là Owner
  mở dashboard local, bấm “Kiểm tra trước khi chạy” và chỉ chạy khi preflight
  GREEN hoặc đã đọc/xác nhận YELLOW; RED thì xử lý theo hướng dẫn và không tìm
  cách bỏ qua.

- 2026-09-23: Theo yêu cầu Owner, cấu hình local đã chuyển riêng BCNB sang
  production (`BCNB_DRY_RUN=false` trong `.env`); Google Form vẫn `dry-run`,
  `AUTH_MODE=local` với host chỉ loopback. Readiness production đạt đủ 5/5 kiểm
  tra, blocker `NONE`; `tests.test_production_readiness` đạt 7/7 (exit 0).
  Trước khi đổi, `.env` và SQLite được sao lưu ở
  `F:\FINAL_PROJECT\zaloAutomation-production-preflight-20260923`;
  backup SQLite và restore drill đều có `integrity=ok`. Chỉ một dòng `.env`
  thay đổi so với bản sao. Không có job đang chạy hoặc audit
  `INTENT_RECORDED`/`UNKNOWN` tại preflight; ứng dụng chưa chạy lại và chưa tạo
  job production. Job dry-run gần nhất 285 có 51 target, 37 target đủ điều kiện
  ghi, vì vậy `/run-scan` có thể ghi hàng loạt khi khởi động với cấu hình mới.
  Phải tạo discovery/plan mới, xác định chính xác phạm vi và được Owner duyệt
  riêng trước khi chạy thật; không coi việc đổi `.env` là quyền chạy batch.

- 2026-09-22: Hoàn thành task `ZALO-BATCH-HALT-POLICY-002` (chính sách dừng batch Zalo quét song song), bao gồm xử lý 3 finding từ Terra/Codex review:
  - Sửa lỗi batch dừng sai khi có nhiều target `NOT_FOUND_VERIFIED` trong `services/zalo_parallel_service.py`: loại trừ `NOT_FOUND_VERIFIED` khỏi tập `abnormal_targets`; giữ nguyên tính bất thường fail-closed cho `INSUFFICIENT_EVIDENCE` và lỗi kỹ thuật khi vượt ngưỡng 20% hoặc 3 lỗi hệ thống liên tiếp.
  - Chuẩn hóa hai mã kết thúc cấp batch: `BATCH_ABNORMAL_THRESHOLD` và `BATCH_SYSTEMIC_THRESHOLD`, loại bỏ hoàn toàn `UNKNOWN_FAIL_CLOSED`.
  - Xử lý 3 finding từ Terra/Codex review:
    1. Finding 1 (P1): Worker cuối nghỉ vì `TARGET_SEARCH_FILL_TIMEOUT`, `TARGET_SEARCH_CLEAR_TIMEOUT`, `TARGET_SEARCH_RESULT_TIMEOUT` được map chuẩn hóa tại điểm dùng chung thành mã an toàn cấp batch `TARGET_SEARCH_TIMEOUT` (thuộc whitelist `_ZALO_JOB_REASON_CODES`), giữ nguyên mã chi tiết tại target; không còn rơi vào `UNKNOWN_FAIL_CLOSED`.
    2. Finding 2 (P2): Loại bỏ `LESSON_14_EXEMPTION` khỏi `verified_submitted_count` và theo dõi riêng qua trường `exempt_count`.
    3. Finding 3 (P2): Format thông điệp dừng đợt quét `Đợt quét dừng do {reason_code}: ...` hiển thị đầy đủ lý do dừng và phân rã bộ đếm ngay cả khi `safe_count > 0` (`status == "PARTIAL_SUCCESS"`).
  - Bảo toàn tuyệt đối rào chắn ghi BCNB: khi batch halt, mọi target chưa ghi đều giữ `BLOCKED_BATCH_HALT` (`write_ready=False`, không ghi BCNB). Target đã quét trước lúc halt giữ nguyên `result_json`/`evidence_state` trung thực.
  - Bổ sung test end-to-end cho cả 3 finding trong `tests/test_zalo_parallel_service.py` (`BatchHaltPolicyAcceptanceTests`), nâng tổng số test suite lên 48/48.
  - Bằng chứng kiểm chứng mới: Targeted unit test 48/48 passed (5.565s, exit 0); Timeout lifecycle 10/10 passed (7.935s, exit 0); Auth readiness 8/8 passed (1.966s, exit 0); Cổng chất lượng cách ly `.\CHECK_QUALITY.ps1 -Isolated` exit 0, **550 tests passed**, branch coverage **77.2%** (11,032 statements, 2,243 missed, 3,092 branches, 625 partial), Ruff/Mypy pass, Invariant Phase 3–9B pass, outside-mismatch = 0.
  - Trạng thái bàn giao: `READY_FOR_TERRA` (chi tiết tại `HANDOFF_ZALO_BATCH_HALT_POLICY.md`). Chưa chạy Zalo Web/BCNB live; không tự ý nhận production acceptance.

- 2026-09-22: Job dry-run `279` xác nhận lỗi chính còn lại là **chính sách dừng
  batch**, không phải `TargetClosedError`: 49 target, mới quét 16 thì dừng;
  trong đó có 5 `SUBMITTED_VERIFIED`, 7 `NOT_FOUND_VERIFIED`, 4
  `INSUFFICIENT_EVIDENCE` (gồm 2 `SCAN_FAILED`) và 33 target bị
  `BLOCKED_BATCH_HALT`. Log có timeout Zalo được retry/cleanup an toàn, không có
  unhandled Future. Source hiện đếm cả `NOT_FOUND_VERIFIED` vào ngưỡng 20%, rồi
  đổi cả kết quả tốt trước đó thành blocked và hiện `UNKNOWN_FAIL_CLOSED`.
  Handoff có kiểm thử P1–P8 và giới hạn an toàn đã lưu tại
  `HANDOFF_ZALO_BATCH_HALT_POLICY.md`. Antigravity Remote Control đang báo
  instance local Offline nên Gemini chưa được nhận việc từ xa; không có code,
  database, profile Zalo hay BCNB nào bị thay đổi bởi bước lập kế hoạch này.

- 2026-09-22: Owner chọn `VT-JSB16` để quan sát Zalo thật sau bản sửa timeout.
  Codex bổ sung canary khóa cứng cho đúng target Job 277: buổi 6, ngày 20/09/2026,
  cửa sổ 12–22/09. Cổng chất lượng cách ly chạy mới đạt exit `0`: **540 tests**,
  coverage `77.1%`, `QUALITY GATE PASSED`. Canary thật chỉ-đọc chạy một lần đạt
  xác thực Zalo, tải đủ cửa sổ 10 ngày và cleanup `CLEANUP_OK`; không có
  `TargetClosedError`, không tạo job mới, không ghi BCNB, gửi Zalo, Form hoặc
  database. Kết quả nghiệp vụ của chính target là `NOT_FOUND_VERIFIED` /
  `LESSON_REPORT_ABSENT` (12 tin nhắn được kiểm tra, cửa sổ được phủ tới
  08/09); trạng thái canary `INSUFFICIENT_EVIDENCE` để bắt buộc kiểm tra thủ
  công, không được tự suy luận đã nộp. Artifact riêng tư nằm ngoài repository;
  JSON đã rút gọn có SHA-256 `20F238C3058B944D2EC729F2EAC7B3BF037E5AFCB209587146ECCC881D157790`.
  Owner sau đó đã kiểm tra thủ công và xác nhận **chưa có báo cáo**; kết luận
  nghiệp vụ này khớp với canary. Không ghi lại database hoặc BCNB vì canary và
  xác nhận này không phải một thao tác cập nhật dữ liệu.

- 2026-09-22: Codex đối chiếu candidate vòng 3 với Terra High: **LOCAL_ACCEPTED**
  cho ZALO-SEARCH-TIMEOUT-001. Ba file source/test giữ đúng SHA-256 tại mục 9
  `HANDOFF_ZALO_SEARCH_TIMEOUT.md`. Terra chạy mới 48/48 targeted tests, exit 0;
  kiểm chứng timeout A không hủy thao tác B và không còn unhandled Future trong fixture.
  Full isolated 537 tests/77.1%/exit 0 là bằng chứng Gemini, không chạy lại trong lần chốt này.
  Chưa nghiệm thu Zalo thật; bước tiếp theo là thống nhất target kiểm tra dry-run có giới hạn.
  Các thông tin runtime, cấu hình, Git và số lượng test cũ bên dưới là snapshot lịch sử,
  không khẳng định trạng thái app đang chạy ngày 2026-09-22.

- 2026-09-21 (Vòng 3): Gemini 3.8 Flash High xử lý triệt để Finding P1 Vòng 2 của Terra High trong
  task ZALO-SEARCH-TIMEOUT-001 (thuộc allowlist):
  1) P1: Sửa cơ chế dọn dẹp Playwright RPC callbacks để chỉ lọc và hủy các callback thuộc về đúng task asyncio thực thi thao tác hiện tại (`_get_callback_task(cb) is current_task`), không hủy nhầm các RPC callback của tab/worker khác chạy đồng thời trên cùng kết nối Playwright dùng chung.
  2) Script tái hiện hai tab của Terra (`terra_round2_shared_connection.py`): Tab A timeout (`TARGET_SEARCH_CLEAR_TIMEOUT`), Tab B RPC thành công (`b_rpc_result=success`, vòng 2 bị cancelled), unhandled Future warning = 0.
  3) Bổ sung test regression hai tab dùng chung connection vào repo: `test_real_playwright_two_tabs_shared_connection_a_timeout_b_succeeds_no_future_warning` trong `tests/test_zalo_search_timeout_lifecycle.py`.
  - Kết quả kiểm thử: targeted test `.\venv\Scripts\python.exe -X utf8 -m unittest tests.test_zalo_search_timeout_lifecycle tests.test_zalo_parallel_service -v` exit 0, 48/48 tests (17.886s).
  - Full isolated gate `.\CHECK_QUALITY.ps1 -Isolated` exit 0, 537 tests (188.645s), coverage 77.1%, outside-mismatch = 0.
  - Trạng thái lúc bàn giao: `READY_FOR_TERRA`; đã được Terra vòng 3 PASS và Codex chốt local ngày 2026-09-22.
- 2026-09-21 (Vòng 2): Gemini 3.8 Flash High xử lý 4 finding vòng 1 của Terra High (watchdog native timeout, kiểm tra chữ ký operation, khôi phục handler exception, regression cancellation).

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
2. Với `VT-JSB16`, kiểm tra thủ công với giáo viên/nhóm lớp vì canary đã xác nhận
   không có báo cáo trong cửa sổ có bằng chứng; không chạy lại batch Job 277 chỉ
   để đổi kết quả của một lớp.
3. Canary Zalo này không thay thế nghiệm thu ghi thật. Mọi lần dùng profile thật
   tiếp theo cần target khóa cứng mới, preflight, artifact ngoài repository và
   duyệt riêng của Owner.


## Cập nhật mới nhất — chuyển flow hiện hành, 2026-10-03

Xác minh lúc 2026-10-03T09:33:09+07:00. Phần mã nguồn/cài đặt local đã kiểm chứng; chưa nghiệm thu
AI thật hoặc production. Các mục ngày cũ phía trên là lịch sử, không thay thế
bằng chứng của lần chuyển flow này.

### Nguồn, quyết định và phạm vi

- Đã kiểm tra trước sửa: repo nguồn sạch, main/HEAD/origin/main và main trên
  remote cùng commit 3056879dc47d9a3b2846b99c1178b9fd8b553b8f; canonical
  QQ AI Workflow 10.1.0-rc.9. Nguồn: F:/MINDX_project test/Trạm Điều Phối AI.
- Đã đọc DEC-027 (gỡ workflow trước đây). Yêu cầu hiện tại của Owner cho phép
  đưa flow trở lại; giữ nguyên PROJECT_DECISIONS.md và lịch sử quyết định.
- Dự án vẫn ở branch chore/adopt-qq-ai-workflow-v9, HEAD
  12c465c9001822994c12bb79af4a0bcec3897159. Không commit, push, deploy,
  seed/migrate dữ liệu thật, gọi provider AI thật hoặc thao tác ứng dụng live.
- Đã bổ sung bộ .ai-workflow/, scripts workflow và mcp; hướng dẫn HANDOFF.md,
  OWNER_QUICKSTART.md, project profile và template Direct riêng cho Zalo.
  AGENTS.md/GEMINI.md chỉ nối thêm hướng dẫn; .gitignore thêm .workflow-local/.
  package.json giữ CommonJS cho test .js hiện tại, workflow dùng .mjs.
- Flow việc mới: Gemini thực hiện -> controller kiểm tra -> Luna Max review
  độc lập -> Product Check nếu áp dụng -> Owner nghiệm thu; quyền gộp/phát hành
  được duyệt riêng. Profile ở ASSISTED, capability UNVERIFIED; tên model trong
  template không phải bằng chứng model/tài khoản đã chạy.
- MCP 2.2.0 dùng requirements-workflow.txt và .venv/Scripts/python.exe mới,
  tách khỏi venv/requirements.txt của ứng dụng. Kiểm tra tương thích dạng
  dry-run với requirements.lock.txt không thấy xung đột; không cài MCP vào
  venv ứng dụng hoặc Python toàn máy.

### Kiểm tra mới và giới hạn

| Lệnh/bằng chứng | Kết quả mới |
|---|---|
| venv/Scripts/python.exe -m pip install --dry-run --disable-pip-version-check -r requirements.lock.txt mcp==2.2.0 | Exit 0; không cài vào môi trường ứng dụng |
| .venv/Scripts/python.exe -m pip check; import MCPServer/Client | Exit 0; MCP 2.2.0 |
| .venv/Scripts/python.exe -X utf8 mcp/test_antigravity_server.py | Exit 0; CLI giả lập, không gọi provider thật |
| node --test scripts/lib/*.test.mjs | Exit 0; 112/112, 0 fail; PATH ưu tiên Python flow riêng |
| powershell -NoProfile -ExecutionPolicy Bypass -File scripts/provisioning-safety.test.ps1 | Exit 0; 57/57, fixture tạm/dry-run |
| node tests/check_template_js.js | Exit 0; scripts=7, handlers=24, behavior=6 |
| Profile schema + Direct reviewer binding/worker isolation | Exit 0; host-check.log; không gọi provider |
| node scripts/workflow.mjs help | Exit 0 |
| workflow init khi workspace dirty | Exit 1 như mong đợi: candidate must be clean; không tạo packet |
| CHECK_QUALITY.ps1 -Isolated trong repo đang chạy ứng dụng | Exit 1; 562 test, 3 fail tại LMS do ProfileLockTimeout; Ruff/mypy đạt |
| Targeted tests.test_lms_service trên bản sao source riêng | Exit 0; 7/7 |
| CHECK_QUALITY.ps1 -Isolated trên bản sao source riêng | Exit 0; 562/562 trong 319.832s, coverage 77.0%; QUALITY GATE PASSED |

Lệnh đầy đủ của gate cuối:
powershell -NoProfile -ExecutionPolicy Bypass -File CHECK_QUALITY.ps1 -Isolated
-LogPath <audit-root>/quality-snapshot-full.log; chạy tại
<audit-root>/source-snapshot. Bản sao chỉ chứa source, không chứa .env/database/
browser state; dùng lại interpreter/thư viện của venv qua junction, không
cài lại venv ứng dụng. SHA-256 của 270 file source đã được đối chiếu với repo
trước khi bổ sung mục trạng thái này. Ruff đạt; mypy đạt 75 file; các cổng
Phase 3–9B, thư viện, schema, app smoke giả lập, frontend và compile đều đạt.
Đây là gate đầy đủ trên bản sao local, không phải nghiệm thu live/staging.

Ba lỗi lần đầu xuất phát từ tests/test_lms_service.py dùng runtime lock mặc
định tại runtime/locks/zalo-profile.lock dù bật -Isolated; ứng dụng Owner
đang dùng giữ cùng lock. Không sửa test/runtime để che lỗi, không dừng ứng dụng.
Với ứng dụng đang mở, gate sau này phải chạy trong source workspace tách riêng;
-Isolated đơn lẻ tại repo đang chạy app chưa đủ cách ly đường dẫn lock này.

### Đối chiếu thay đổi và dữ liệu đang dùng

- Manifest source: PASS. 10 file dirty ban đầu ngoài PROJECT_STATE.md giữ
  nguyên SHA-256 và trạng thái Git; PROJECT_STATE.md giữ nguyên toàn bộ nội
  dung cũ và chỉ nối mục này. 65 file toolkit giữ byte-exact theo nguồn;
  71 file mới đều thuộc allowlist. Không sửa source sản phẩm.
- requirements.txt, requirements.lock.txt, .env và .env.bak giữ nguyên.
  Không ghi đè database/browser state hay khôi phục dữ liệu từ baseline.
- Guard yêu cầu dữ liệu runtime giữ nguyên tuyệt đối: CHANGED/FAIL, không
  được đổi thành PASS. Database/browser state đã thay đổi trong thời gian
  Owner sử dụng ứng dụng; Owner xác nhận "Có, tôi đang dùng ứng dụng".
  Đối chiếu process cho thấy app chạy trước task và browser là con của app,
  tách khỏi tiến trình test. Không khẳng định database/profile giữ nguyên
  từng byte và không mở rộng sự xác nhận này thành quyền thao tác live.
- Baseline bất biến, backup source gốc, log và final-manifest.json ở ngoài
  repository: C:\Users\QQ\AppData\Local\Temp\zalo-flow-migration-20261003-3vri0054. Không đưa fingerprint/dữ liệu runtime vào tài liệu.
  Chỉ dùng backup để đối chiếu/khôi phục đúng source theo yêu cầu; không
  ghi đè dữ liệu Owner đang sử dụng.

### Blocker và bước an toàn tiếp theo

Bộ flow đã áp dụng ở source/local. Điều kiện dữ liệu runtime bất biến không
đạt do phiên Owner đang hoạt động; capability/provider và nghiệm thu thật
chưa kiểm chứng. Có agy/codex CLI trên máy nhưng chưa xác minh đăng nhập,
model/quota hoặc review độc lập thật. Không tạo task từ HEAD cũ khi 11 file
đang sửa chưa được đóng gói; không coi template Direct là task runnable.

Task sau đọc HANDOFF.md và bản cập nhật này, lập scope/gate theo đúng source.
Khi Owner yêu cầu riêng, có thể chuẩn bị checkpoint source sạch đã kiểm chứng
rồi tạo contract/task Direct với write_paths/gate_paths chính xác. Việc chuyển
flow này không cấp quyền commit/push, đăng nhập, quét hay ghi hệ thống thật.
# Cập nhật 2026-10-03 09:54 (Asia/Saigon) — Phân biệt cảnh báo báo cáo và BCNB

- Theo xác nhận Owner: SA26 buổi 8 và JSB15 buổi 10 đã có báo cáo; các buổi
  mới nhất còn lại trong danh sách đang xem chưa báo cáo. Chỉ sửa diễn đạt tại
  `api/job_routes.py`, thêm regression tại `tests/test_api_routes_unit.py`.
- Target có `SUBMITTED_VERIFIED`/`SUBMITTED_LATE_VERIFIED` nhưng cần review hiện
  "Đã có báo cáo đúng buổi nhưng bước cập nhật BCNB cần kiểm tra.";
  `NOT_FOUND_VERIFIED` hiện "Chưa tìm thấy báo cáo đúng buổi.". Cảnh báo lỗi
  kỹ thuật vẫn được ưu tiên; thiếu bằng chứng/không rõ vẫn giữ câu thận trọng.
  Không đổi điều kiện review, số đếm, evidence, luồng quét hoặc ghi BCNB.
- Thực hiện ASSISTED do Direct không thể dùng HEAD cũ thay cho workspace dirty
  và chưa có quyền commit. Không gọi Gemini/Direct. Review độc lập yêu cầu
  `gpt-6-luna`, effort `max`, PASS trên hai file nguồn đã khóa SHA-256; không
  coi đây là completion canonical gắn commit hoặc nghiệm thu Owner.
- Kiểm chứng trên bản sao source riêng, không chứa `.env`, database/profile
  thật, dùng interpreter `F:/FINAL_PROJECT/zaloAutomation/venv/Scripts/python.exe`
  và `ZALO_QUALITY_ISOLATED=1`, đường dẫn dữ liệu/lock/log tạm, dry-run:
  `python -X utf8 -B -m unittest tests.test_api_routes_unit
  tests.test_zalo_evidence tests.test_app_factory -v`: exit 0, 46/46.
  Regression chạy trước sửa: exit 1 với đúng 3 subcase diễn đạt cũ.
  `node tests/check_template_js.js`: exit 0, scripts=7 handlers=24 behavior=6;
  `python -m ruff check api/job_routes.py tests/test_api_routes_unit.py`: exit 0;
  `python -m mypy api/job_routes.py`: exit 0, 1 source file;
  `git diff --check -- api/job_routes.py tests/test_api_routes_unit.py`: exit 0.
- Product Check: đã nhìn bản giả lập cục bộ dùng hàm tạo cảnh báo và renderer/
  markup hiện có; hai dòng có báo cáo và hai dòng chưa tìm thấy hiển thị đúng.
  CSS của bản giả lập được giản lược. Chưa xác nhận trên dashboard đang chạy;
  chưa chạy quality gate đầy đủ hoặc quét Zalo/BCNB thật trong task này.
- Hồ sơ/baseline/backup source và bằng chứng ở ngoài repository:
  `C:/Users/QQ/AppData/Local/Temp/zalo-warning-fix-20261003-zvknc88z`.
  Allowlist chỉ gồm hai file nguồn/test trên và `PROJECT_STATE.md`; 84 path
  dirty ngoài phạm vi giữ nguyên trạng thái Git và SHA-256 khi đối chiếu.
- Bước tiếp theo: khi không có job đang chạy, khởi động lại ứng dụng rồi mở
  kết quả quét cũ để xem câu cảnh báo mới; không cần quét lại hoặc ghi BCNB.


## Cập nhật 2026-10-05 — Sửa báo đỏ giả của bộ quét bí mật

- Xác minh lúc 2026-10-05T16:24:27+07:00; Owner yêu cầu "sao lưu và tiến hành" cho bản sửa nhỏ đã đề xuất.
- Sao lưu 270 file mã nguồn hiện tại, gồm công việc chưa commit, tại `C:/Users/QQ/Documents/Codex Backups/zaloAutomation/20261005-secret-scan-1a95b8f2/source-before-fix.zip`; CRC và SHA-256 từng file khớp baseline. Bản này không chứa cấu hình thật, database hoặc browser profile; hướng dẫn khôi phục ở `RESTORE.txt` cùng thư mục. Không chuyển/xóa backup cũ.
- Chỉ thêm `.venv` vào `EXCLUDED_PARTS` trong `scripts/phase9a_audit.py`; thêm một unittest tại `tests/test_phase9a_audit.py`. Không đổi secret patterns hoặc điều kiện ghi dữ liệu. Fixture chứng minh `venv`/`.venv` được bỏ qua nhưng `app.py`/`.venv-source` vẫn bị phát hiện khi chứa chuỗi giống credential.
- Regression: `./venv/Scripts/python.exe -X utf8 -B -m unittest tests.test_phase9a_audit -v` chạy trước sửa exit 1 với đúng 1 failure; sau sửa exit 0, 1/1. Ruff và scoped diff check exit 0.
- Bộ audit tại dự án thật: `./venv/Scripts/python.exe -X utf8 -B -m scripts.phase9a_audit` exit 0, secret findings=0, dependency lock khớp 73 entry. Không chạy pip-audit qua mạng.
- Gate đầy đủ tại `C:/Users/QQ/Documents/Codex Backups/zaloAutomation/20261005-secret-scan-1a95b8f2/source-snapshot`: `powershell -NoProfile -ExecutionPolicy Bypass -File CHECK_QUALITY.ps1 -Isolated -LogPath "C:/Users/QQ/Documents/Codex Backups/zaloAutomation/20261005-secret-scan-1a95b8f2/full-quality.log"` exit 0, `QUALITY GATE PASSED`, 564/564 trong 328.478s, coverage 77.0%; Ruff, mypy 75 file và mọi bước còn lại đạt. Snapshot không chứa `.env`, database hoặc profile thật; dùng interpreter/thư viện qua junction `venv` và `.venv`, không cài lại môi trường.
- ASSISTED được chọn trước dispatch vì HEAD không đại diện workspace dirty và chưa có quyền commit. Không gọi Direct/Gemini. Reviewer độc lập yêu cầu `gpt-6-luna`/`max`, PASS trên hai SHA nguồn đã khóa; reviewer chạy lại 1/1 test (exit 0), không có finding. Đây là review source/local, không phải completion canonical gắn commit hoặc nghiệm thu production.
- Allowlist chỉ gồm `scripts/phase9a_audit.py`, `tests/test_phase9a_audit.py`, `PROJECT_STATE.md`; nội dung trạng thái cũ giữ nguyên và chỉ nối mục này. Manifest cuối và bằng chứng ở thư mục backup ngoài repo. Không sửa dữ liệu/cấu hình/profile, không mở Zalo/BCNB/Form, không commit/push/deploy.
- Bước tiếp theo: tiếp tục sử dụng ứng dụng; bản sửa công cụ kiểm tra không yêu cầu khởi động lại. Việc dọn ổ F và chốt mã nguồn vẫn là công việc riêng. Test xanh chỉ xác nhận source/local.

## Cập nhật 2026-10-05 — Dọn file cũ, giữ bản khôi phục trên ổ C

- Owner yêu cầu dọn các file quá cũ không dùng. Đã hoàn tất xóa bản gốc lúc `2026-10-05T17:02:38+07:00`, sau khi chép và đối chiếu SHA-256 nguồn/đích từng file. Phạm vi chỉ gồm artifact bị Git bỏ qua: các nhánh `.backups` cũ trước 2026-09-01, `.pytest_cache`, `groups_dump.json`, `jobs_dump.json`; không xóa file nguồn đang được quản lý hoặc công việc chưa commit.
- Chuyển 4.558 file / 5.000.197.178 byte sang `C:/Users/QQ/Documents/Codex Backups/zaloAutomation/20261005-legacy-cleanup-bbb81bf7/archived`, rồi xóa đúng các file trong manifest và 343 thư mục đã rỗng. Hai phase `cleanup.ps1 -Phase Archive` và `-Phase Delete` đều exit 0; post-delete xác nhận 0 file/thư mục candidate còn lại, tổng file/byte trên ổ C khớp. Ổ F còn 11.894.530.048 byte trống khi hoàn tất (khoảng 11,9 GB).
- Giữ `.backups/db` đang dùng và ba ZIP hoàn chỉnh gần nhất của nhóm cũ: `pre-required-week-dashboard-20260815-170153.zip`, `pre-ui-lag-fix-source-state-20260814-230450.zip`, `pre-scan-target-timing-fix-source-state-20260814-173859.zip`; SHA-256 ba ZIP không đổi. Cấu hình, database thật, browser profile, ảnh bằng chứng, `scratch`, `venv` và `.venv` nằm ngoài allowlist xóa. Không lấy byte/hash của database hoặc profile đang chạy làm bằng chứng bất biến.
- Bản lưu có thể chứa mật khẩu/dữ liệu lịch sử: ACL giới hạn đúng tài khoản hiện tại, SYSTEM và Administrators; không đưa nội dung, tên file dữ liệu chi tiết hoặc manifest nhạy cảm vào Git. `RESTORE.txt` cùng thư mục hướng dẫn yêu cầu khôi phục từng file, không ghi đè công việc mới. Hai ZIP nén dở cũ được giữ nguyên byte; không khẳng định giải nén được. Đây là bản lưu trên cùng máy, chưa phải sao lưu ngoài máy.
- Trước dọn đã sao lưu 271 file nguồn hiện tại vào `source-before-cleanup.zip`, kiểm tra CRC và SHA từng file; không chứa cấu hình thật/database/profile. `source_manifest.py` đối chiếu path, trạng thái Git và SHA ngoài `PROJECT_STATE.md`, cấu hình thật được kiểm tra riêng trong artifact private; nội dung trạng thái cũ giữ nguyên và chỉ nối mục này.
- ASSISTED chọn trước dispatch vì HEAD không đại diện workspace dirty và chưa có quyền commit; không gọi Direct/Gemini. Reviewer độc lập yêu cầu `gpt-6-luna`/`max`, PASS trên SHA helper/summary đã khóa và kiểm tra ACL; hồ sơ `independent-review.json` ngoài repo. Check `verify_backup_root.py` exit 0 xác nhận cấu hình theo `START.bat` bảo vệ đúng `.backups/db`, không khởi động server/mở DB; không quan sát môi trường bên trong tiến trình ứng dụng nếu được khởi động bằng cách khác. Đây là review an toàn local, không completion canonical gắn commit hoặc nghiệm thu production.
- Sau dọn: `./venv/Scripts/python.exe -X utf8 -B -m scripts.phase9a_audit` exit 0, secret findings=0, dependency lock khớp 73 entry; pip-audit qua mạng không chạy. Không chạy lại quality gate đầy đủ trong task chỉ dọn artifact, không đổi code ứng dụng. Manifest cuối và diff riêng của tài liệu ở thư mục lưu trữ trên ổ C.
- Bước tiếp theo: tiếp tục dùng ứng dụng bình thường, không cần khởi động lại vì lượt dọn này. Khi cần file cũ, đối chiếu `transfer-manifest.json` private và chỉ khôi phục file cần thiết từ `archived`. Không mở Zalo/BCNB/Form, không ghi dữ liệu thật, không commit/push/deploy.


## Cập nhật 2026-10-22 — DEC-036, Backup pre-upgrade-20261022 & Hoàn thành Phase 1.1

- **Backup**: `.backups/pre-upgrade-20261022/` (`pre-upgrade-20261022-212728.sqlite3`, SHA-256 `60A6ED77446E5A024FD47928EDD2358578780B585D785404AD43372D8A6C3DBF`, `restore_drill_ok=true`; `source-code-snapshot-20261022.zip`, SHA-256 `CC1FAF688BB91C00346083D075029AA1AD64A52974AB4554FA5A73E069705A06`).
- **Phase 1.1 (Copy nhắc GV 1-Click Zalo & LMS)**:
  - Đã triển khai `#btn-copy-reminders` (`copyZaloTeacherReminders()`) trong `templates/partials/dashboard.html` & `static/js/jobs.js` (fail-closed khi thiếu `requiredDashboardPeriod()`, gom theo giáo viên) và `#btnCopyLmsMissing` (`copyLmsMissingClasses()`) trong `templates/partials/lms_modal.html` & `static/js/lms.js`.
  - Test: `node tests/check_template_js.js` (`scripts=7 handlers=26 behavior=7`, exit `0`), `tests.check_app_smoke` (`phase3-app-smoke-ok`), `.\CHECK_QUALITY.ps1 -Isolated` (`QUALITY GATE PASSED`, exit `0`, coverage `77.1%`).
  - Independent Review (`gpt-6.1-sol`, `effort: xhigh`, read-only): Session `01a1641a` phát hiện P2 (thiếu tuần bắt buộc) -> đã vá & thêm test -> Session `01a1641f` xác nhận **PASS**.
- **Bước tiếp theo**: Triển khai **Phase 1.2 (Quét lại có chọn lọc / Selective Re-scan)** theo `UPGRADE_PROGRESS.md`.

## Cập nhật 2026-10-22 — Hoàn thành Phase 1.2 (Selective Re-scan các lớp lỗi/nộp bù)

- **Phạm vi**: `api/zalo_routes.py`, `services/zalo_parallel_service.py`, `services/zalo_service.py`, `services/zalo_bcnb_service.py`, `templates/partials/dashboard.html`, `static/js/state.js`, `static/js/jobs.js`, `tests/check_template_js.js`, `tests/test_api_routes_unit.py`, `tests/test_zalo_parallel_service.py`.
- **Chức năng & An toàn**:
  - Mở rộng `POST /run-scan` nhận `selected_target_keys` (fail-closed: chặn rỗng, trùng, sai kiểu `422`; chặn khóa ngoài snapshot `409 SCAN_SELECTED_TARGET_NOT_IN_PLAN`).
  - Ràng buộc tập con trong `prepare_frozen_scan_execution` và lọc dòng BCNB trong `update_bcnb_reports` (bảo vệ dòng không chọn, bao gồm không tự miễn/ghi Buổi 14 của lớp không chọn).
  - Giao diện thêm nút `#btn-rescan-failed` (`startSelectiveRescanFailed()`) và nút `Quét lại` từng dòng lỗi; gộp kết quả theo đúng gốc `(periodKey, snapshotId)` đã xác thực, giữ nguyên bảng khi `RUNNING`/`CANCELLED`, từ chối gộp dữ liệu khác snapshot hoặc dữ liệu cũ thiếu `snapshot_id`.
- **Kiểm chứng & Review**:
  - `node tests/check_template_js.js`: exit `0` (`scripts=7 handlers=27 behavior=8`).
  - `.\venv\Scripts\python.exe -m unittest tests.test_api_routes_unit tests.test_zalo_parallel_service`: exit `0` (`81/81` passed).
  - `.\CHECK_QUALITY.ps1 -Isolated`: exit `0` (`QUALITY GATE PASSED`, coverage `77.0%`).
  - Independent Review (`gpt-6.1-sol`, `effort: xhigh`, read-only): Phát hiện & vá triệt để `P1` (cross-snapshot / legacy missing `snapshot_id`) và `P2` (`RUNNING`/`CANCELLED` & initial-load-order) -> Session `01a16443-e06c-7eb0-812c-f7db96ccb5a8` xác nhận **PASS**.
- **Bước tiếp theo**: Triển khai **Phase 1.3 (Duyệt tay & Tích BCNB 1-Click có lưu vết)** theo `UPGRADE_PROGRESS.md`.

## Cập nhật 2026-10-24 — Vá 4 Lỗi Owner Xác Nhận Sau Rollout Phase 3.2 & Review Độc Lập Sol 6.1 xhigh

- **Phạm vi**: `services/lms_service.py`, `repositories/report_repository.py`, `static/js/jobs.js`, `tests/test_lms_service.py`, `tests/test_api_routes_unit.py`, `tests/check_template_js.js`.
- **Chi tiết khắc phục**:
  1. **Lỗi #1 (Ưu tiên cao — LMS đọc thiếu vẫn cho lập kế hoạch tích)**: `_wait_for_lms_table_settled` và `_read_lms_missing_classes` trong `services/lms_service.py` chuyển sang fail-closed (`raise LmsSafetyError`) khi trang không ổn định, rỗng (`0` dòng), bị kẹt lặp chữ ký trang hoặc đạt `max_pages` (`20` trang) khi vẫn còn trang tiếp theo.
  2. **Lỗi #2 (Báo cáo tuần bỏ sót kết quả duyệt tay giữa các lần quét)**: `ReportRepository.get_weekly_summary` trong `repositories/report_repository.py` đối chiếu toàn bộ bản ghi `BCNB_MANUAL_APPROVE_TICK` (`mode='production'`, `outcome='VERIFIED'`) từ `external_action_audit` và các job quét cùng tuần theo đúng khóa `(week, year, class_code, lesson, class_date_iso)`, giữ kèm `snapshot_id` từng dòng và xóa `snapshot_id` cấp tổng hợp khi ghép thêm dòng duyệt tay từ snapshot khác.
  3. **Lỗi #3 (Giao diện có thể trộn dữ liệu lịch sử/tổng hợp tuần vào kế hoạch mới)**: `loadWeeklyHistorySummary`, `onHistoryWeekChange`, `renderZaloResults` và `rememberSelectiveScanBaseline` trong `static/js/jobs.js` bảo toàn nguồn `snapshotId`/`periodKey` từ API, chặn làm baseline quét chọn lọc khi thiếu hoặc lệch `snapshot_id` (kể cả `snapshot_id: null` ở từng dòng), và ngăn request live-mode cũ đã bị thay thế xóa nhầm bảng tuần lịch sử mới tải.
  4. **Lỗi #4 (Lấy học viên LMS dừng nhầm ở trang hợp lệ có cùng mã lớp ngoài yêu cầu)**: `_extract_students_from_table_for_classes` trong `services/lms_service.py` đưa định danh học viên (`row_student_sig`) vào `page_signature` cho mọi dòng (kể cả lớp ngoài `target_set`).
- **Kiểm chứng & Review**:
  - `.\venv\Scripts\python.exe -m unittest tests.test_lms_service tests.test_api_routes_unit`: exit `0` (`46/46` passed).
  - `node tests/check_template_js.js`: exit `0` (`scripts=7 handlers=33 behavior=12`).
  - `.\CHECK_QUALITY.ps1 -Isolated`: exit `0` (`QUALITY GATE PASSED`, coverage `75.9%`).
  - Independent Review (`gpt-6.1-sol`, `effort: xhigh`, read-only): Session `01a169cf` phát hiện 2 ca biên `P2` -> đã vá & bổ sung test -> Session `01a169e2-61a2-7422-ae5c-4179b0e91166` xác nhận **VERDICT: PASS**.
- **Bước an toàn tiếp theo**: Khởi động lại ứng dụng khi không có job đang chạy để áp dụng bản vá; không tự ý commit/push hoặc chạy tác vụ ghi thật nếu chưa có yêu cầu riêng của Owner.
