# PROJECT STATE — Zalo Automation

> Snapshot hiện tại, cập nhật 2026-09-26. Source và kiểm tra runtime mới có độ
> tin cậy cao hơn tài liệu. Không dùng file này làm quyền chạy thao tác thật.

## Trạng thái ngắn

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
