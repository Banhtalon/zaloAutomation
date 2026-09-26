# HANDOFF — ZALO-BATCH-HALT-POLICY-002

**Trạng thái:** READY_FOR_TERRA  
**Owner đã chấp thuận:** lập kế hoạch này và giao Gemini triển khai.  
**Mục tiêu:** Không dừng cả đợt quét chỉ vì nhiều lớp đã được xác minh là không có báo cáo; vẫn dừng an toàn khi thiếu bằng chứng thực sự hoặc lỗi kỹ thuật vượt ngưỡng.

## 1. Bối cảnh và bằng chứng đã xác minh

Đây là lỗi chính còn lại sau khi sửa vòng đời timeout tìm kiếm Zalo.

Lần chạy thật nhưng **không ghi dữ liệu ngoài** gần nhất là Job `279` ngày `2026-09-21`:

| Nội dung | Số lượng |
| --- | ---: |
| Target của batch | 49 |
| Target đã thử quét trước khi dừng | 16 |
| `SUBMITTED_VERIFIED` | 5 |
| `NOT_FOUND_VERIFIED` | 7 |
| `INSUFFICIENT_EVIDENCE` | 4 |
| `SCAN_FAILED` trong số trên | 2 |
| Target còn lại bị chặn do batch halt | 33 |

Log có các timeout tìm kiếm Zalo tại `19:09:19`–`19:10:21` (`CLEAR` và `RESULT`, khoảng 5 giây). Tuy nhiên, Job 279 **không** có `TargetClosedError` hay `Future exception was never retrieved`; cleanup ghi `CLEANUP_OK`. Vì vậy không mở lại hoặc thay thế bản sửa vòng đời timeout trong task này.

Nguyên nhân của việc dừng sai đã xác minh từ source và dữ liệu Job 279:

1. `BatchFailurePolicy` hiện coi mọi kết quả ngoài ba trạng thái nộp hợp lệ là bất thường. Vì vậy `NOT_FOUND_VERIFIED` (đã đủ 10 ngày bằng chứng và xác minh không có báo cáo) cũng bị tính vào ngưỡng lỗi.
2. Với ngưỡng 20%, 10/49 target đã vượt 20%; bảy lớp "không có báo cáo" và bốn lớp thiếu/lỗi bằng chứng đã làm batch chạm ngưỡng dù không phải tất cả đều là rủi ro kỹ thuật.
3. Khi policy halt, kết quả hợp lệ đã có trước đó cũng bị đổi thành `BLOCKED_BATCH_HALT`; màn hình vì thế báo "0 target an toàn" dù đã quét được năm lớp có báo cáo.
4. Lý do ghi cho Job thành `UNKNOWN_FAIL_CLOSED` do reason nội bộ của policy không nằm trong tập reason hiển thị của job. Điều này làm Owner không biết batch dừng vì đâu.

Hai target có `SCAN_FAILED` chung chung là `VT-ROB-SEMIB07` và `VT-C4K-PTI11`. Dữ liệu lưu không còn nguyên nhân gốc chi tiết. Không được tự đoán rằng chúng là cùng một lỗi với timeout; chỉ được phân loại rõ hơn nếu có bằng chứng từ source/test kín, không dùng Zalo/BCNB thật.

## 2. Quyết định nghiệp vụ đã chấp thuận cho lần sửa này

`NOT_FOUND_VERIFIED` nghĩa là đã tải đủ phạm vi 10 ngày và **xác minh được** không có báo cáo. Nó là kết quả nghiệp vụ cần để Owner xem, không phải dấu hiệu hệ thống không đáng tin. Do đó:

- `NOT_FOUND_VERIFIED` **không tính** vào tỷ lệ bất thường dùng để tự dừng batch.
- `NOT_FOUND` có `INSUFFICIENT_EVIDENCE`, dữ liệu trùng/mơ hồ, không đủ lịch sử, lỗi truy cập, lỗi tìm kiếm, lỗi setup/cleanup vẫn là bất thường và vẫn được tính.
- Không thay đổi ngưỡng hiện hành: quá `20%` target bất thường hoặc `3` lỗi hệ thống liên tiếp vẫn phải dừng an toàn.
- Không suy luận lớp nào đã nộp, không fuzzy match, không tự ghi/đánh dấu BCNB cho lớp không có báo cáo. Dry-run vẫn không được thay đổi dữ liệu thật.

## 3. Phạm vi triển khai bắt buộc

### Hành vi mong muốn

1. Tách rõ **kết quả nghiệp vụ đã xác minh là chưa nộp** khỏi **kết quả không đủ tin cậy để quyết định** trong chỗ dùng chung tính Batch Failure Policy. Dùng cấu trúc/trạng thái đã có nếu đáp ứng được; không tạo framework hay cấu hình mới chỉ cho một nhánh.
2. Bảo đảm policy nhận đúng phân loại tại mọi caller trong luồng scan Zalo, gồm kết quả bình thường, `TargetSearchError` đã retry hết, và exception chung.
3. Nếu batch không halt, quét tiếp các target còn lại. Kết quả `NOT_FOUND_VERIFIED` vẫn phải hiển thị là cần Owner xem/không được ghi, nhưng không làm hỏng kết quả `SUBMITTED_VERIFIED` độc lập.
4. Nếu batch thực sự halt vì ngưỡng kỹ thuật/bằng chứng: target chưa chạy vẫn bị chặn an toàn; `result_json`/`evidence_state` của target đã quét phải giữ đúng sự thật đã quan sát. **Không thay đổi rào chắn ghi hiện tại:** mọi target chưa ghi vẫn giữ `write_status=BLOCKED_BATCH_HALT`, không đặt `write_ready=True` và không ghi BCNB sau khi batch halt. Chỉ phần thống kê kết quả quét được tách khỏi trạng thái đủ điều kiện ghi.
5. Chốt hai reason cấp batch: `BATCH_ABNORMAL_THRESHOLD` khi vượt tỷ lệ bất thường và `BATCH_SYSTEMIC_THRESHOLD` khi chạm số lỗi hệ thống liên tiếp. Đưa chúng vào `_ZALO_JOB_REASON_CODES`; lỗi cụ thể như `SCAN_FAILED` hoặc `TARGET_SEARCH_*` vẫn nằm ở target tương ứng. Job đã halt bởi policy không được rơi vào `UNKNOWN_FAIL_CLOSED`.
6. Payload/thông điệp kết thúc phải phản ánh được ít nhất: tổng target, đã quét, số đã xác minh có báo cáo, số đã xác minh chưa có báo cáo, số thiếu bằng chứng/lỗi kỹ thuật, số đủ điều kiện ghi và lý do dừng nếu có. Không gọi một target là "an toàn để ghi" chỉ dựa vào evidence state. Không cần sửa giao diện nếu payload hiện hữu đã truyền được các trường này; chỉ sửa UI khi test chứng minh contract hiện tại không thể hiển thị đúng.

### Điểm sửa tối thiểu được kỳ vọng

Ưu tiên sửa tại ba điểm dùng chung đang có trong `services/zalo_parallel_service.py`: phân loại `abnormal` sau khi quét target, `BatchFailurePolicy.record`, và `build_zalo_job_progress`. Không tạo policy/helper/framework mới nếu ba điểm này đã đủ. Không sửa repository hoặc contract ghi; nếu thật sự cần file ngoài allowlist thì dừng theo mục 7.

### Không nằm trong phạm vi

- Không tăng timeout 5 giây một cách phỏng đoán.
- Không thay kiến trúc Playwright, số worker, profile trình duyệt, Zalo Web, BCNB, database schema, hoặc giao diện ngoài phần cần cho contract trên.
- Không chạy quét thật, không mở profile thật, không dùng cookie/tài khoản, không sửa `.env`, không khởi động lại ứng dụng.
- Không sửa hành vi ghi dữ liệu BCNB hay tắt `BCNB_DRY_RUN`.
- Không sửa mã lỗi generic `SCAN_FAILED` nếu không tái tạo/định vị được nguồn lỗi trong kiểm thử kín. Khi đó hãy giữ nguyên an toàn, ghi rõ giới hạn này trong báo cáo.

## 4. Allowlist file và bảo toàn thay đổi có sẵn

Chỉ được sửa các file sau, trừ khi dừng để xin mở rộng phạm vi:

- `services/zalo_parallel_service.py`
- `tests/test_zalo_parallel_service.py`
- `HANDOFF_ZALO_BATCH_HALT_POLICY.md` (chỉ cập nhật phần báo cáo phía cuối)
- `PROJECT_STATE.md` (snapshot cuối task, ngắn gọn, đã kiểm chứng)

Các thay đổi có sẵn từ task trước phải giữ nguyên, đặc biệt: `PROJECT_DECISIONS.md`, `PROJECT_STATE.md`, `services/zalo_parallel_service.py`, `services/zalo_readonly_canary.py`, `tests/test_zalo_parallel_service.py`, `HANDOFF_ZALO_SEARCH_TIMEOUT.md`, `scripts/zalo_jsb16_readonly_canary.py`, `tests/test_zalo_jsb16_readonly_canary.py`, và `tests/test_zalo_search_timeout_lifecycle.py`.

Trước khi sửa phải chụp `git status --short` và hash manifest; sau khi làm phải so sánh để bảo đảm chỉ allowlist thay đổi thêm.

## 5. Tiêu chí chấp nhận có thể kiểm chứng

Viết test nhỏ, sát luồng thực tế, dùng fixture tổng hợp. Không chỉ sửa expected test cũ mà không chứng minh policy mới.

| ID | Tình huống | Kết quả bắt buộc |
| --- | --- | --- |
| P1 | 49 target gồm 10 `NOT_FOUND_VERIFIED` và 39 kết quả nộp hợp lệ | Không halt theo ngưỡng 20%; scan đi hết batch; không có ghi BCNB cho 10 target chưa nộp. |
| P2 | 49 target, có 9 thiếu bằng chứng/lỗi kỹ thuật (có thể kèm nhiều `NOT_FOUND_VERIFIED`) | Không halt bởi ngưỡng phần trăm. |
| P3 | 49 target, có 10 thiếu bằng chứng/lỗi kỹ thuật | Halt fail-closed vì vượt 20%; target chưa chạy bị chặn an toàn. |
| P4 | `NOT_FOUND` nhưng evidence state là `INSUFFICIENT_EVIDENCE` | Vẫn được tính bất thường; không bị nhầm với `NOT_FOUND_VERIFIED`. |
| P5 | Ba lỗi hệ thống liên tiếp | Vẫn halt theo ngưỡng hệ thống, không phụ thuộc số `NOT_FOUND_VERIFIED`. |
| P6 | Job bị halt do ngưỡng tỷ lệ hoặc ngưỡng hệ thống | API/progress lần lượt dùng `BATCH_ABNORMAL_THRESHOLD` hoặc `BATCH_SYSTEMIC_THRESHOLD`, không trả `UNKNOWN_FAIL_CLOSED`. |
| P7 | Một vài target nộp hợp lệ hoàn tất trước lúc có halt thật | `result_json`/`evidence_state` vẫn trung thực, nhưng `write_status` vẫn là `BLOCKED_BATCH_HALT`; không target nào được ghi sau halt. |
| P8 | Bộ test timeout cũ | Vẫn chứng minh không có `Future exception was never retrieved` và không làm yếu cleanup/retry đã sửa. |

### Gate bắt buộc

1. Chạy targeted test liên quan trước khi chạy full gate.
2. Chạy `.\CHECK_QUALITY.ps1 -Isolated` từ thư mục gốc dự án.
3. Không chạy bất kỳ script live/canary nào trong task này.
4. Review diff sau cùng, và đối chiếu manifest với baseline. Nếu path ngoài allowlist đổi hash hoặc trạng thái, dừng và báo rõ; không tự xóa/khôi phục thay đổi của người khác.

## 6. Quy trình Gemini phải làm

- [x] Đọc `AGENTS.md`, `PROJECT_SUMMARY.md`, `PROJECT_STATE.md`, `PROJECT_DECISIONS.md`, `HANDOFF_ZALO_SEARCH_TIMEOUT.md`, và toàn bộ handoff này.
- [x] Xác minh facts bằng source hiện tại trước khi thay đổi; nêu file/hàm/caller đang khiến `NOT_FOUND_VERIFIED` bị tính bất thường.
- [x] Chụp Git baseline như phần 4.
- [x] Chọn diff nhỏ nhất tại điểm dùng chung; không thêm dependency, database migration hay config.
- [x] Viết/chỉnh test để bao phủ P1–P8.
- [x] Chạy targeted test, sau đó full isolated quality gate.
- [x] So sánh final manifest/diff và cập nhật `PROJECT_STATE.md` với lệnh, kết quả, thời điểm, giới hạn production.
- [x] Điền báo cáo bên dưới, nhưng không ghi dữ liệu nhạy cảm, log thô Zalo, cookie hoặc `.env` vào repository.

## 7. Điều kiện phải dừng để báo Owner thay vì tự đoán

- Cần sửa file ngoài allowlist hoặc migration schema.
- Không phân biệt được `NOT_FOUND_VERIFIED` và evidence chưa đủ ở source hiện tại mà không làm thay đổi contract lớn.
- Giữ kết quả target đã hoàn tất khi halt có thể làm phát sinh ghi dữ liệu ngoài hoặc mâu thuẫn với contract write hiện hành.
- Full quality gate có lỗi mới, hoặc manifest final có thay đổi ngoài allowlist.

## 8. Báo cáo hoàn thành của Gemini — điền khi xong

**Trạng thái:** HOÀN THÀNH (READY_FOR_TERRA)

### Thay đổi đã làm (kèm xử lý 3 finding từ Terra/Codex)

- `services/zalo_parallel_service.py`:
  1. **P1 — Nhánh hết worker không trả `UNKNOWN_FAIL_CLOSED`:**
     - Bổ sung các mã lý do chuẩn hóa cấp batch vào `_ZALO_JOB_REASON_CODES`: `ZALO_JOB_REASON_TARGET_SEARCH_TIMEOUT = "TARGET_SEARCH_TIMEOUT"`, `ZALO_JOB_REASON_TARGET_SEARCH_ERROR = "TARGET_SEARCH_ERROR"`, cùng `SETUP_CODES` và `TARGET_SEARCH_*_CODES`.
     - Trong `build_zalo_job_progress`: ánh xạ các lỗi tìm kiếm chi tiết (`TARGET_SEARCH_FILL_TIMEOUT`, `TARGET_SEARCH_CLEAR_TIMEOUT`, `TARGET_SEARCH_RESULT_TIMEOUT`) sang mã cấp batch `TARGET_SEARCH_TIMEOUT`; các lỗi tìm kiếm không timeout sang `TARGET_SEARCH_ERROR`; các mã lỗi setup đã biết được giữ nguyên trong whitelist. Không để bất kỳ lỗi đã biết nào bị chuyển thành `UNKNOWN_FAIL_CLOSED`.
     - Target row trong database và `result_json` vẫn giữ nguyên mã lỗi chi tiết `TARGET_SEARCH_*` để phục vụ audit/review.
  2. **P2 — Loại `LESSON_14_EXEMPTION` khỏi `verified_submitted_count`:**
     - `verified_submitted_count` chỉ đếm các kết quả thực sự nộp bài (`SUBMITTED_VERIFIED`, `SUBMITTED_LATE_VERIFIED`).
     - Trạng thái miễn báo cáo buổi 14 (`LESSON_14_EXEMPTION`) được tách riêng vào trường `exempt_count`. Khi có `LESSON_14_EXEMPTION`, `verified_submitted_count == 0` và `exempt_count == 1`.
  3. **P2 — Thông báo halt giữ nguyên lý do và thống kê khi `safe_count > 0`:**
     - Trong `build_zalo_job_progress`, điều kiện hiển thị thông báo dừng đợt quét được kích hoạt mỗi khi có `supplied_reason`, không phụ thuộc vào `safe_count == 0`.
     - Khi batch halt sau khi đã có một số target thành công (`safe_count > 0`), trạng thái là `PARTIAL_SUCCESS`, thông báo hiển thị đầy đủ: mã lý do dừng (`reason_code`), số target đã quét/tổng số, số có báo cáo, số miễn báo cáo (nếu có), số chưa có báo cáo, số thiếu bằng chứng/lỗi, và số target đủ điều kiện ghi.
  4. **Logic cốt lõi đã đạt từ vòng trước:**
     - `NOT_FOUND_VERIFIED` được loại khỏi tập `abnormal_targets` trong `scan_worker`.
     - Giữ nguyên các ngưỡng fail-closed: 20% bất thường (`BATCH_ABNORMAL_THRESHOLD`) và 3 lỗi hệ thống liên tiếp (`BATCH_SYSTEMIC_THRESHOLD`).
     - Khi batch halt, mọi target chưa ghi tiếp tục giữ `BLOCKED_BATCH_HALT`, `write_ready=False`, không phát sinh ghi BCNB.
- `tests/test_zalo_parallel_service.py`:
  1. Cập nhật assertions trong `BatchFailurePolicyTests`.
  2. Bổ sung test suite `BatchHaltPolicyAcceptanceTests` bao phủ trọn vẹn P1–P7 và 3 finding mới:
     - `test_finding_1_worker_timeout_to_build_progress_never_unknown_fail_closed`: chạy xuyên suốt luồng worker cuối bị `TARGET_SEARCH_FILL_TIMEOUT` qua `run_parallel_zalo_batch` đến `build_zalo_job_progress`. Xác nhận `progress["reason_code"] == "TARGET_SEARCH_TIMEOUT"`, không phải `UNKNOWN_FAIL_CLOSED`, target lưu đúng `TARGET_SEARCH_FILL_TIMEOUT`, target chưa quét bị chặn `BLOCKED_BATCH_HALT`.
     - `test_finding_2_lesson_14_exemption_excluded_from_verified_submitted_count`: xác nhận `LESSON_14_EXEMPTION` cho ra `verified_submitted_count == 0` và `exempt_count == 1`.
     - `test_finding_3_halt_message_preserves_reason_when_safe_count_positive`: xác nhận khi có target thành công (`safe_count >= 1`) và batch halt với `BATCH_ABNORMAL_THRESHOLD` hoặc `BATCH_SYSTEMIC_THRESHOLD`, thông báo cuối vẫn chứa mã lý do dừng và đầy đủ các số lượng chính.

### Bằng chứng kiểm tra

1. Targeted unit tests:
   - Lệnh: `.\venv\Scripts\python.exe -X utf8 -m unittest tests.test_zalo_parallel_service -v`
   - Kết quả: **48/48 tests passed** (5.565s, exit 0).
2. Regression timeout lifecycle tests (P8):
   - Lệnh: `.\venv\Scripts\python.exe -X utf8 -m unittest tests.test_zalo_search_timeout_lifecycle -v`
   - Kết quả: **10/10 tests passed** (7.935s, exit 0, không có `Future exception was never retrieved` hay `TargetClosedError`).
3. Readiness tests:
   - Lệnh: `.\venv\Scripts\python.exe -X utf8 -m unittest tests.test_zalo_auth_readiness -v`
   - Kết quả: **8/8 tests passed** (1.966s, exit 0).
4. Full isolated quality gate:
   - Lệnh: `.\CHECK_QUALITY.ps1 -Isolated`
   - Kết quả: **Exit 0**, **550 tests passed** (185.342s), branch coverage **77.2%** (11,032 statements, 2,243 missed, 3,092 branches, 625 partial), Ruff passed, Mypy passed (74 source files), Invariant Gates Phase 3–9B passed, app smoke passed (`QUALITY GATE PASSED`).
5. Kiểm tra định dạng:
   - Lệnh: `git diff --check`
   - Kết quả: Exit 0 (không có lỗi khoảng trắng hay định dạng).

### Diff và bảo toàn workspace

- File thay đổi trong allowlist:
  - `services/zalo_parallel_service.py` (SHA256: `7DF2CB6AB8AF414983211B36E57D2FA66FC95012F85DB31CEE1C34F3DC86E5E4`)
  - `tests/test_zalo_parallel_service.py` (SHA256: `BA1689362FE8BD0AF82ED85B36D30F5420F8C4C27B7F946DAC05C3F3B04700A6`)
  - `HANDOFF_ZALO_BATCH_HALT_POLICY.md` (chỉ cập nhật mục 6 và mục 8)
  - `PROJECT_STATE.md` (cập nhật snapshot trạng thái)
- Kiểm tra toàn vẹn Git ngoài allowlist:
  - 193 files được quản lý trong Git ngoài allowlist có **0 mismatches / 0 thay đổi hash** so với baseline ban đầu.
  - Toàn bộ thay đổi và artifacts có sẵn từ trước được bảo toàn nguyên vẹn: `PROJECT_DECISIONS.md`, `services/zalo_readonly_canary.py`, `scripts/zalo_jsb16_readonly_canary.py`, `tests/test_zalo_jsb16_readonly_canary.py`, `tests/test_zalo_search_timeout_lifecycle.py`, `HANDOFF_ZALO_SEARCH_TIMEOUT.md`.

### Chưa kiểm tra / rủi ro còn lại

1. Chưa kiểm tra với Zalo Web thật hoặc profile browser trực tiếp (tuân thủ nguyên tắc fail-closed, không tự ý can thiệp dữ liệu thật).
2. Chưa kiểm tra ghi dữ liệu BCNB thật (`BCNB_DRY_RUN=false` chưa kích hoạt và không thuộc phạm vi task).
3. Mã lỗi generic `SCAN_FAILED` cho các target đơn lẻ (như Job 279 `VT-ROB-SEMIB07` và `VT-C4K-PTI11`) chưa được xác định do thiếu log vết sâu; cơ chế fail-closed hiện tại xử lý chúng an toàn như một lỗi bất thường.
4. Cần human review từ Terra/Codex và Owner approval trước khi lên lịch chạy batch thật tiếp theo.

### Hướng dẫn cho reviewer Terra

Reviewer đối chiếu 3 finding vừa sửa cùng P1–P8 với source, test và diff thực tế:
1. P1: Kiểm tra luồng worker timeout cuối cùng (ví dụ `TARGET_SEARCH_FILL_TIMEOUT`), xác nhận `progress["reason_code"]` trả về `TARGET_SEARCH_TIMEOUT` (thuộc whitelist `_ZALO_JOB_REASON_CODES`), không trả `UNKNOWN_FAIL_CLOSED`. Lỗi chi tiết `TARGET_SEARCH_*` vẫn ở target row.
2. P2: Kiểm tra `LESSON_14_EXEMPTION` không làm tăng `verified_submitted_count` (trả về 0) mà được đưa vào `exempt_count`.
3. P2: Kiểm tra khi `safe_count > 0` và batch halt, `progress["message"]` vẫn nêu rõ lý do dừng và các số lượng chính.
Reviewer không chạy Zalo/BCNB thật và không sửa source; nếu phát hiện vấn đề, ghi nhận chính xác file, dòng, tái tạo tối thiểu và mức độ ưu tiên.
