# PROJECT STATE — Zalo Automation

> Snapshot hiện tại, cập nhật 2026-09-26. Source và kiểm tra runtime mới có độ
> tin cậy cao hơn tài liệu. Không dùng file này làm quyền chạy thao tác thật.

## Trạng thái ngắn

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
