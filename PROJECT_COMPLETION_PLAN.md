# Kế hoạch hoàn thiện Zalo Automation

**Trạng thái tài liệu:** EXECUTING — Giai đoạn 1; runtime dry-run và ảnh negative evidence đã được nghiệm thu, các hạng mục Phase 1 còn mở tiếp tục theo checklist
**Execution Gate:** ALLOWED cho Local/Dev từ 2026-07-29; không mở Production Gate  
**Môi trường mặc định:** Local/Dev  
**Số vòng sửa tối đa cho mỗi giai đoạn:** 2  
**Production Gate:** BLOCKED cho đến khi người vận hành xác nhận riêng  
**Ngày lập:** 2026-07-29  

## 1. Mục tiêu

Hoàn thiện ứng dụng thành hệ thống có thể:

1. Dò đúng danh sách lớp từ BCNB và Zalo mà không làm mất dữ liệu hợp lệ.
2. Kiểm tra báo cáo Zalo theo bằng chứng kiểm toán được.
3. Chỉ tích BCNB khi có đủ bằng chứng và đúng quy tắc nghiệp vụ.
4. OCR và nộp Google Form đúng học viên, đúng lớp, chống gửi trùng.
5. Tích LMS theo dữ liệu đã kiểm chứng, không suy luận nguy hiểm từ ảnh thiếu.
6. Chạy ổn định trên máy chủ một người vận hành hoặc được bảo vệ khi truy cập qua web.
7. Có backup, migration, audit, monitoring và quy trình rollback rõ ràng.

## 2. Hiện trạng đã xác minh

- [x] Ruff đạt.
- [x] Mypy đạt trên 53 file nguồn.
- [x] 143 regression test đạt.
- [x] Coverage tổng đạt 75,1%.
- [x] Database `integrity_check=ok`.
- [x] Không có lỗi khóa ngoại.
- [x] Không có external action audit ở trạng thái `INTENT_RECORDED` bị treo.
- [x] Upload có kiểm tra định dạng, dung lượng, pixel, hash và đường dẫn.
- [x] BCNB write có intent audit và read-back sau hard reload.
- [x] LMS confirmation có token dùng một lần và plan digest.
- [ ] Coverage các vùng rủi ro chưa đạt:
  - `services/zalo_service.py`: 10,3%.
  - `services/zalo_discovery_service.py`: 31,0%.
  - `services/zalo_scan_evidence.py`: 49,7%.
- [ ] Git repository chưa hợp lệ; thư mục `.git` hiện không có metadata sử dụng được.
- [ ] Chưa đủ điều kiện deploy công khai.

## 3. Phạm vi

### Trong phạm vi

- Logic báo cáo Zalo và bằng chứng.
- Auto Discovery, lifecycle và chuyển cấp lớp.
- Tích nhận xét Zalo và LMS trên BCNB.
- OCR và nộp Google Form.
- Job queue, retry, cancel, audit và idempotency.
- Database schema, migration, backup và retention.
- Giao diện review/confirm.
- Bảo mật khi đưa ứng dụng lên web.
- Test, quality gate, dry-run và production smoke có phê duyệt.

### Không nằm trong phạm vi

- Không tự động deploy production trong quá trình thực hiện kế hoạch.
- Không tự gửi Google Form thật hoặc tích BCNB thật nếu chưa có xác nhận riêng.
- Không xóa lịch sử, database, ảnh hoặc session Zalo để “làm sạch”.
- Không force-push hoặc viết lại lịch sử Git.
- Không thay tài khoản, domain hay secret nếu người vận hành chưa cung cấp/phê duyệt.
- Không dùng AI làm nguồn sự thật duy nhất cho ngày gửi, mã lớp hoặc trạng thái đã nộp.

## 4. Nguyên tắc bất biến

1. **Fail closed:** thiếu bằng chứng phải thành `INSUFFICIENT_EVIDENCE`.
2. **Nguồn sự thật:** ngày gửi lấy từ giao diện/timestamp Zalo; ngày trong nội dung chỉ để đối chiếu.
3. **Không snapshot phá hủy:** một lần quét thiếu không được xóa lớp.
4. **Không ghi mù:** mọi thay đổi BCNB phải có intent, read-back và audit.
5. **Không retry mù:** outcome `UNKNOWN` không được tự chạy lại.
6. **Không production ngầm:** mode phải được chụp cố định vào job.
7. **Không lộ bằng chứng:** ảnh Zalo, thông tin học viên và log chi tiết không được phục vụ công khai.
8. **Bằng chứng trước tuyên bố:** test xanh không thay thế runtime acceptance.

## 5. Thứ tự thực hiện

```text
Giai đoạn 0: Baseline + Git + backup
        ↓
Giai đoạn 1: Độ chính xác báo cáo Zalo
        ↓
Giai đoạn 2: Discovery + lifecycle lớp
        ↓
Giai đoạn 3: Google Form
        ↓
Giai đoạn 4: LMS
        ↓
Giai đoạn 5: Bảo mật + dữ liệu riêng tư
        ↓
Giai đoạn 6: Worker + database + vận hành
        ↓
Giai đoạn 7: UI kiểm chứng + tính năng nâng cấp
        ↓
Giai đoạn 8: Nghiệm thu local/dev
        ↓
Human Gate: Production smoke
```

---

## Giai đoạn 0 — Baseline, backup và source control

### Công việc

- [x] Xác nhận không có job external-write đang chạy.
- [x] Sao lưu source trước mỗi giai đoạn.
- [ ] Trước migration, dừng ứng dụng và tạo SQLite backup nhất quán.
- [x] Ghi SHA-256 và kiểm tra mở/list được archive.
- [x] Kiểm tra thư mục `.git` hiện tại.
- [ ] Chỉ sau khi người vận hành xác nhận, khởi tạo hoặc khôi phục Git repository.
- [x] Thêm `.env`, `database.db*`, `zalo_session/`, ảnh, log, upload và backup vào ignore.
- [x] Tạo commit/checkpoint baseline sau khi quality gate đạt.
- [x] Pin phiên bản trong `requirements.txt` và `requirements-dev.txt`.
- [x] Tạo file lock dependency hoặc quy trình cập nhật dependency có kiểm thử.

### Acceptance Criteria

- [x] Backup source có đường dẫn, số file, dung lượng và SHA-256.
- [x] Backup database chạy `PRAGMA integrity_check` thành công sau khi restore thử ở file tạm.
- [x] `git status` chạy được và không đưa secret/runtime vào index.
- [x] Cài mới môi trường từ dependency đã pin thành công.
- [x] `CHECK_QUALITY.ps1` đạt trên baseline.

### Human Gate

- Cần xác nhận trước khi thay đổi `.git`, remote hoặc lịch sử source control.

---

## Giai đoạn 1 — Chính xác hóa báo cáo Zalo

### 1.1 Tách phân loại nội dung và ngày gửi

- [x] Refactor classifier để trả:
  - `is_report`.
  - `score`.
  - `content_dates`.
  - `matched_signals`.
- [x] Không dùng `content_dates` làm ngày gửi.
- [x] Đọc `ui_sent_at` từ timestamp/ARIA/DOM của Zalo.
- [x] Nếu chỉ có separator ngày, ánh xạ message theo timeline trong đúng chat container.
- [ ] Chuẩn hóa timezone `Asia/Ho_Chi_Minh`.
- [x] Lưu cảnh báo `CONTENT_DATE_MISMATCH` khi ngày nội dung lệch ngày gửi.

### 1.2 Kiểm tra đồng bộ và độ bao phủ

- [x] Phát hiện banner:
  - “Zalo Web của bạn hiện chưa có đầy đủ tin nhắn gần đây”.
  - “Đồng bộ tin nhắn gần đây”.
  - “Nhấn để đồng bộ ngay”.
- [ ] Có thể thử đồng bộ một lần và chờ postcondition.
- [x] Nếu banner không biến mất: `INSUFFICIENT_EVIDENCE`.
- [x] Tính:

```python
required_start = min(
    today - timedelta(days=10),
    class_date - timedelta(days=2),
)
```

- [x] Chỉ coi đã tải đủ khi:
  - Mốc cũ nhất đạt `required_start`.
  - Message count và mốc cũ nhất ổn định sau ít nhất hai lần cuộn.
  - Không có banner thiếu đồng bộ.

### 1.3 Trạng thái nghiệp vụ

- [x] `SUBMITTED_VERIFIED`: đủ điều kiện tích BCNB.
- [x] `SUBMITTED_LATE_VERIFIED`: có báo cáo quá 6 ngày, không tích; 3–6 ngày vẫn nghiệm thu và ghi rõ trễ.
- [x] `NOT_FOUND_VERIFIED`: không tìm thấy trong khoảng đã kiểm chứng.
- [x] `INSUFFICIENT_EVIDENCE`: không được kết luận.
- [ ] Buổi 14 dùng cùng audit/evidence model, ghi rõ `LESSON_14_EXEMPTION`.

### 1.4 Audit từng candidate

- [x] Lưu cho từng candidate:
  - `ui_sent_at`.
  - `content_dates`.
  - `class_date`.
  - `delay_days`.
  - `decision`.
  - `rejection_reason`.
  - `message_fingerprint`.
- [ ] Audit BCNB lưu SHA-256 ảnh, không chỉ đường dẫn. Chờ bổ sung payload audit BCNB ở vòng tiếp theo.

### Test bắt buộc

- [x] GB40: nội dung ghi `26/07/2025`, UI gửi `29/07/2026`, ngày học `26/07/2026` → được nghiệm thu, cảnh báo sai năm.
- [x] Nội dung đúng nhưng UI gửi quá hạn → `SUBMITTED_LATE_VERIFIED`.
- [x] Banner thiếu đồng bộ → `INSUFFICIENT_EVIDENCE`.
- [x] Không đọc được ngày UI → `INSUFFICIENT_EVIDENCE`.
- [x] Chưa cuộn đủ 10 ngày → `INSUFFICIENT_EVIDENCE`.
- [x] Sai header nhóm → không phân loại.
- [x] Ảnh chụp thất bại → không được `VERIFIED`.
- [x] Hai mã lớp gần giống không được match nhầm.

### Acceptance Criteria

- [x] Không còn đường code dùng ngày nội dung làm ngày gửi.
- [x] Mọi negative result đều có proof of coverage.
- [x] Coverage `zalo_scan_evidence.py` và classifier ≥ 85%.
- [x] Dry-run GB38, GB40, GB41 cho kết quả khớp kiểm tra thủ công.
- [x] Không có audit external write trong dry-run.

---

## Giai đoạn 2 — Discovery và lifecycle lớp

### 2.1 Đọc bảng BCNB theo header

- [ ] Resolve duy nhất các cột:
  - Mã lớp.
  - Số buổi.
  - Ngày học.
  - Giáo viên.
  - Nhận xét Zalo.
  - LMS.
- [ ] Không dùng `cells[2]`, `cells[3]` hoặc vị trí cố định.
- [ ] Validate ngày học và số buổi trước khi lưu.
- [ ] Nếu header thiếu/trùng: dừng và giữ snapshot cũ.

### 2.2 Snapshot không phá hủy

- [ ] Bỏ `DELETE FROM groups` trong luồng Discovery bình thường.
- [ ] Upsert lớp quan sát được.
- [ ] Lớp không thấy được đánh dấu `missing_since`.
- [ ] Chỉ chuyển `PAUSED_OR_TRANSITIONING` sau ít nhất hai snapshot đáng tin liên tiếp.
- [ ] Không tự `ENDED_CONFIRMED`.
- [ ] Chỉ archive lớp đã được người vận hành xác nhận kết thúc.
- [ ] Lưu snapshot ID, số lớp trước/sau, match ratio và checksum.

### 2.3 Chuyển cấp

- [ ] Giữ cohort ổn định qua:
  - PTB → PTA → PTI.
  - GB → GA → GI.
  - SB → SA → SI.
- [ ] BCNB xuất hiện trở lại là bằng chứng lớp active.
- [ ] Không dùng số ngày nghỉ cố định để kết luận kết thúc.
- [ ] Xác minh center code để không gộp hai lớp cùng số.

### 2.4 Khôi phục lớp tạm nghỉ

- [ ] Xác minh header nhóm trước khi đọc tin.
- [ ] Ưu tiên BCNB presence hơn cụm từ trong tin nhắn.
- [ ] Nếu dùng “Thông báo khai giảng”, lưu ảnh và candidate decision.

### Test bắt buộc

- [ ] Snapshot 58 → 29 lớp không được xóa 29 lớp cũ.
- [ ] Snapshot rỗng giữ nguyên database.
- [ ] BCNB đổi thứ tự cột vẫn đọc đúng.
- [ ] Cột thiếu/trùng phải fail closed.
- [ ] PTB06 → PTA06 → PTI06 cùng cohort.
- [ ] GB02 → GA02 → GI02 cùng cohort.
- [ ] Hai center có cùng PTI06 không bị gộp.
- [ ] Mở nhầm nhóm không được khôi phục lớp.

### Acceptance Criteria

- [ ] Không còn destructive replace trong Discovery.
- [ ] Mỗi thay đổi lifecycle có event/audit.
- [ ] Coverage Discovery và lifecycle ≥ 85%.
- [ ] Chạy Discovery dry-run ba lần liên tiếp không làm giảm bất thường số lớp.

---

## Giai đoạn 3 — Hoàn thiện Google Form

### 3.1 Xác minh OCR

- [ ] Validate tên và mã lớp bằng rule deterministic.
- [ ] Đối chiếu mã lớp với danh sách BCNB/database hiện tại.
- [ ] Dedupe học viên phía server.
- [ ] Đánh dấu OCR confidence thấp hoặc tên bất thường.
- [ ] Cho phép sửa tên/mã lớp trên UI trước khi submit.
- [ ] Hiển thị nguồn ảnh của từng học viên.

### 3.2 Preflight Form

- [ ] Validate `FORM_URL`.
- [ ] Chụp schema câu hỏi thực tế trước khi chạy batch.
- [ ] Kiểm tra tất cả mapping tồn tại và duy nhất.
- [ ] Snapshot mapping, form URL và digest vào job payload.
- [ ] Dry-run phải đọc lại giá trị đã điền.
- [ ] Chụp ảnh cuối form cho từng học viên hoặc theo batch.

### 3.3 Gửi thật an toàn

- [ ] Idempotency key do server tạo hoặc xác minh định dạng.
- [ ] Dùng global rate limiter thay cho `idx * 1.5`.
- [ ] Không giữ semaphore trong lúc chờ không cần thiết.
- [ ] `UNKNOWN` phải vào hàng chờ kiểm tra thủ công.
- [ ] Không tự retry production.
- [ ] Lưu confirmation evidence khi Google Form báo đã ghi nhận.
- [ ] Không log toàn bộ dữ liệu nhạy cảm.

### Test bắt buộc

- [ ] OCR sai một ký tự mã lớp bị chặn.
- [ ] Hai học viên trùng không gửi hai lần.
- [ ] Mapping thiếu không được tạo production job.
- [ ] Form đổi câu hỏi dừng trước submit.
- [ ] Confirmation mất → `UNKNOWN`, không retry.
- [ ] Request lặp cùng idempotency key không gửi lại.
- [ ] Batch 200 học viên có thời gian/rate limit hợp lý.

### Acceptance Criteria

- [ ] Mỗi học viên có outcome và audit duy nhất.
- [ ] Dry-run toàn bộ cohort có ảnh/read-back.
- [ ] Production smoke chỉ chạy với cohort test và xác nhận người vận hành.
- [ ] History, audit và expected cohort khớp 1:1.

---

## Giai đoạn 4 — Hoàn thiện LMS

### Công việc

- [ ] Không suy luận “mọi lớp không nằm trong missing list đều được tick”.
- [ ] OCR từng dòng gồm mã lớp và trạng thái.
- [ ] Chứng minh ảnh đã bao phủ đủ danh sách hoặc đủ trang.
- [ ] Nếu không chứng minh coverage: không cho confirm.
- [ ] Ưu tiên tích hợp dữ liệu trực tiếp thay vì OCR ảnh nếu có API/trang nguồn.
- [ ] Lưu OCR evidence, BCNB snapshot và plan digest.
- [ ] Khi confirm, đọc lại BCNB và so snapshot mới.
- [ ] Tách `already_checked`, `to_tick`, `blocked`, `unknown`.
- [ ] Mỗi checkbox có audit, read-back và evidence.

### Test bắt buộc

- [ ] Ảnh bị cắt không cho tick.
- [ ] OCR bỏ sót một dòng không làm lớp đó thành `to_tick`.
- [ ] BCNB thay đổi sau preview bắt buộc phân tích lại.
- [ ] Mã OCR không có trên BCNB chặn plan.
- [ ] Checkbox đã tick không bị ghi lặp.
- [ ] Hard reload không giữ trạng thái → `FAILED/UNKNOWN`.

### Acceptance Criteria

- [ ] Không còn negative-list inference nguy hiểm.
- [ ] 100% lớp được tick có bằng chứng riêng.
- [ ] Production smoke giới hạn 1–3 lớp test và có read-back.

---

## Giai đoạn 5 — Bảo mật và dữ liệu riêng tư

### Công việc

- [ ] Thêm xác thực người vận hành.
- [ ] Phân quyền:
  - Viewer: xem trạng thái.
  - Operator: chạy dry-run và duyệt.
  - Admin: bật production/cấu hình.
- [ ] Thêm CSRF protection cho mutation endpoint.
- [ ] Thêm rate limit và giới hạn pending jobs.
- [ ] Di chuyển ảnh minh chứng ra khỏi `/static`.
- [ ] Cung cấp evidence endpoint có auth, authorization và audit truy cập.
- [ ] Bundle Tailwind/Font Awesome local; bỏ remote script khỏi privileged UI.
- [ ] Thêm CSP, frame protection, referrer policy và permissions policy.
- [ ] Không trả confirmation token cho người dùng không có quyền.
- [ ] Redact tên học viên/tin nhắn khỏi log không cần thiết.
- [ ] Định nghĩa retention và quyền xóa dữ liệu.

### Acceptance Criteria

- [ ] Người chưa đăng nhập không đọc được dashboard, job, history hoặc evidence.
- [ ] Viewer không tạo/cancel/retry/confirm job.
- [ ] Screenshot URL trực tiếp trả 401/403.
- [ ] Không còn JavaScript remote trong trang quản trị.
- [ ] Security headers có trong response.
- [ ] Mutation không có CSRF token bị từ chối.

---

## Giai đoạn 6 — Worker, database và vận hành

### Worker/browser

- [ ] Tách API và browser worker hoặc dùng một hàng đợi bền vững.
- [ ] Chỉ một worker được sở hữu `zalo_session`.
- [ ] Thêm database lease/OS lock chống hai process mở cùng profile.
- [ ] Dùng đường dẫn tuyệt đối cấu hình cho session, upload, log, evidence và database.
- [ ] Thêm timeout/circuit breaker cho Zalo, BCNB, Gemini và Google Form.
- [ ] Giới hạn số pending jobs.
- [ ] Hiển thị queue position.

### Database

- [ ] Dùng UTC ISO thống nhất cho mọi timestamp.
- [ ] Thêm schema version và migration tuần tự.
- [ ] Thêm CHECK constraint cho status/mode/outcome khi migration cho phép.
- [ ] Thêm index theo truy vấn thực tế.
- [ ] Thêm backup tự động và restore drill.
- [ ] Dọn dữ liệu theo retention có audit.
- [ ] Không để DB giữ đường dẫn evidence đã bị xóa mà không có trạng thái expired.

### Health/monitoring

- [ ] Readiness kiểm tra capability:
  - BCNB URL/credential.
  - FORM_URL.
  - Gemini khi tính năng OCR được bật.
  - Chrome/Playwright.
  - Session/profile lock.
- [ ] Metrics không chứa PII.
- [ ] Alert heartbeat stale, disk thấp, UNKNOWN audit và failure spike.
- [ ] Export diagnostics bundle đã redact.

### Acceptance Criteria

- [ ] Khởi động hai worker không thể cùng chiếm browser profile.
- [ ] Restart giữa job read-only phục hồi đúng.
- [ ] Restart giữa production mutation không tự retry.
- [ ] Restore database backup thành công và integrity check đạt.
- [ ] Readiness đỏ khi thiếu capability cần thiết.

---

## Giai đoạn 7 — UI kiểm chứng và nâng cấp chức năng

### Review Center

- [ ] Một màn hình thống nhất cho Zalo, LMS và Google Form.
- [ ] Bộ lọc theo trạng thái, giáo viên, lớp, thời gian, confidence.
- [ ] Xem ảnh, SHA-256, classifier trace và coverage.
- [ ] Nút:
  - Quét lại.
  - Chấp nhận.
  - Từ chối.
  - Yêu cầu kiểm tra thủ công.
- [ ] Manual override bắt buộc lý do và audit.

### Quản lý lớp

- [ ] Timeline PTB/PTA/PTI, GB/GA/GI, SB/SA/SI.
- [ ] Hiển thị `last_seen_bcnb`, `missing_since`, lịch sử đổi tên.
- [ ] Không gọi lớp “đã kết thúc” nếu chưa có xác nhận.
- [ ] Cho cấu hình quy tắc theo chương trình nhưng không tự suy đoán ngày nghỉ.

### Báo cáo vận hành

- [ ] Dashboard success/failure/unknown theo ngày.
- [ ] Xuất CSV/PDF audit theo job.
- [ ] Báo cáo lớp chưa đủ bằng chứng.
- [ ] Lịch sử ai đã duyệt và thời điểm duyệt.
- [ ] Cảnh báo evidence sắp hết retention.

### UI reliability

- [ ] Mọi `fetch` kiểm tra `response.ok`.
- [ ] Không còn `catch {}` rỗng.
- [ ] Hiển thị mã lỗi và request ID.
- [ ] Trạng thái loading/disabled nhất quán.
- [ ] Accessibility: keyboard, focus, label, contrast.

### Acceptance Criteria

- [ ] Người vận hành hiểu lý do của từng kết quả mà không đọc database/log.
- [ ] Lỗi API luôn hiển thị, không báo thành công giả.
- [ ] Tất cả thao tác manual override có audit.

---

## Giai đoạn 8 — Nghiệm thu local/dev

### Quality Gate

```powershell
.\CHECK_QUALITY.ps1
```

- [ ] Ruff đạt.
- [ ] Mypy đạt.
- [ ] Tất cả regression test đạt.
- [ ] Coverage tổng ≥ 85%.
- [ ] Coverage module external-write/evidence ≥ 85%.
- [ ] Frontend contract đạt.
- [ ] Compile đạt.
- [ ] Không có warning dependency compatibility chưa xử lý.

### Database Gate

- [ ] `PRAGMA integrity_check` trả `ok`.
- [ ] `PRAGMA foreign_key_check` không có dòng lỗi.
- [ ] Không có audit `INTENT_RECORDED` stale.
- [ ] Không có job RUNNING stale.
- [ ] Không có history/action key trùng ngoài dự kiến.

### Runtime Dry-run Gate

- [ ] Discovery chạy ba lần ổn định.
- [ ] Zalo scan chạy đủ toàn bộ lớp.
- [ ] GB40 regression đúng.
- [ ] Negative evidence có đủ hai ảnh và coverage.
- [ ] Google Form dry-run cho cohort test có read-back/evidence.
- [ ] LMS preview không tạo external write.
- [ ] Audit dry-run và job results đối chiếu đủ số lượng.

### Security Gate

- [ ] Unauthenticated API probe bị từ chối.
- [ ] Evidence direct URL bị từ chối.
- [ ] CSRF test đạt.
- [ ] Không có secret trong source, log, artifact hoặc backup.
- [ ] Dependency vulnerability scan không còn lỗi mức cao/chí mạng.

### Status khi đạt

Chỉ được ghi:

`ACCEPTED FOR LOCAL/DEV`

Không được ghi `PRODUCTION ACCEPTED` ở bước này.

---

## 6. Production Smoke — Human Gate bắt buộc

Chỉ thực hiện khi người vận hành xác nhận chính xác:

- Môi trường.
- Job/function được phép ghi thật.
- Cohort/lớp/học viên test.
- Số lượng tối đa.
- Backup và rollback.

### Smoke test tối thiểu

- [ ] Zalo/BCNB: 1 lớp đã nộp + 1 lớp insufficient, chỉ lớp đủ bằng chứng được tick.
- [ ] LMS: 1–3 lớp test, hard reload xác minh từng checkbox.
- [ ] Google Form: 1 học viên test, confirmation + history + audit khớp.
- [ ] Không có UNKNOWN.
- [ ] Không có duplicate action.
- [ ] Người vận hành kiểm tra trực tiếp hệ thống đích.

### Trạng thái cuối

Chỉ ghi `PRODUCTION ACCEPTED` khi toàn bộ smoke test có bằng chứng.

---

## 7. Stop/Escalation Rules

Dừng ngay và chuyển `ESCALATED` nếu:

- Không có backup hợp lệ trước migration hoặc external write.
- Zalo báo chưa đồng bộ đầy đủ.
- Không xác minh được đúng nhóm hoặc đúng tuần/năm BCNB.
- Không đọc được ngày gửi, header hoặc cấu trúc bảng.
- Ảnh bằng chứng/chụp hash thất bại.
- Có outcome `UNKNOWN`.
- Snapshot Discovery giảm bất thường.
- Database integrity/foreign key check thất bại.
- Hai process cùng sử dụng browser profile.
- Thiếu secret/quyền/truy cập hệ thống ngoài.
- Một acceptance criterion vẫn thất bại sau hai vòng sửa.

Escalation report phải ghi:

1. Điều đã đạt.
2. Điều thất bại.
3. Lệnh/log/ảnh chứng minh.
4. Rủi ro nếu tiếp tục.
5. Quyết định nhỏ nhất cần người vận hành xác nhận.

## 8. Rollback

### Source

- Dừng ứng dụng.
- Xác minh đường dẫn backup.
- So SHA-256 với Evidence Log.
- Giải nén vào thư mục mới để kiểm tra trước.
- Không ghi đè workspace hiện tại trước khi so sánh.

### Database

- Dừng mọi API/worker.
- Giữ nguyên database lỗi để điều tra.
- Restore sang file tạm.
- Chạy integrity và foreign key checks.
- Đổi sang database restore chỉ sau khi đạt.

### External systems

- Không tự “bỏ tick” hoặc gửi lại để rollback.
- Dùng audit xác định chính xác target đã thay đổi.
- Người vận hành sửa thủ công trên BCNB/Google Form khi cần.
- Ghi một audit correction riêng, không sửa lịch sử cũ.

## 9. Evidence Log

Mỗi giai đoạn phải cập nhật bảng sau:

| Thời điểm | Giai đoạn | Artifact/Lệnh | Kết quả | Bằng chứng | Người thực hiện |
|---|---|---|---|---|---|
| 2026-07-29 | Plan baseline | Source backup | PASS | `.backups/zaloAutomation-before-completion-plan-20260729-180548.zip`; SHA-256 `E885EBB31336BF21E73C617DBA593413BD601AB4F422171A2D4FF49BA1B1DB20`; 144 entries | Codex |
| 2026-07-29 | Review baseline | `CHECK_QUALITY.ps1` | PASS | Ruff; Mypy 53 files; 143 tests; coverage 75,1%; schema/smoke/frontend/compile pass | Codex |
| 2026-07-29 | Database baseline | Integrity/foreign key/audit checks | PASS | integrity `ok`; no FK errors; no stale intent | Codex |
| 2026-07-29 | Phase 0 | Không có job/audit ghi ngoài đang chạy | PASS | `scan_jobs`: active `0`; `external_action_audit`: `DRY_RUN=33`, `FAILED=2`, `VERIFIED=180`; unresolved `0` | Codex |
| 2026-07-29 | Phase 0 | Source backup | PASS | `.backups/zaloAutomation-phase0-source-20260729-181336.zip`; 160 entries; 323,334 bytes; SHA-256 `5358000DBA4FA65AF9CE8EDA5F9A787AD39E3791F4967A924014D74DDD8BEB14` | Codex |
| 2026-07-29 | Phase 0 | SQLite backup/restore validation | PASS | `.backups/zaloAutomation-phase0-database-20260729-181336.db`; integrity `ok`; foreign keys `0`; SHA-256 `E43ECD8876640A3682DFB8D2C6EAD3BA9B531C7CE0DCEAE638A17D0872FD7666` | Codex |
| 2026-07-29 | Phase 0 | Dependency lock | PASS | `requirements.txt` direct pins + `requirements.lock.txt` (49 lines); consistency check passed | Codex |
| 2026-07-29 | Phase 0 | Quality gate | PASS | Ruff, Mypy 53 files, 143 tests, phase invariant gates, schema/active jobs, smoke, frontend contract and compile passed; coverage 75.1% | Codex |
| 2026-07-29 | Phase 0 | Git checkpoint | PASS | fresh local repository; 128 tracked source/documentation files; no `.env`, database, session, runtime, evidence or backup staged; commit `4077a3ff0cc1248b5d66491c1effacaa5ebc3b3d` | Codex |
| 2026-07-29 | Phase 0 | Clean environment install | PASS | 49 pinned packages installed in a fresh Python environment; `pip check` and core imports passed | Codex |
| 2026-07-29 | Phase 0 | Existing whitespace debt | OPEN P3 | `git diff --cached --check` reports 183 pre-existing whitespace findings; deferred to a dedicated formatting-only checkpoint to avoid mixing a mechanical rewrite with the baseline | Codex |
| 2026-07-29 | Phase 1 | GB40/UI date regression | PASS | 10 Zalo evidence tests plus classifier/safety/evidence tests; report content `26/07/2025` and UI date `29/07/2026` produce `SUBMITTED_LATE_ACCEPTED`, BCNB eligible, with `CONTENT_DATE_MISMATCH` warning | Codex |
| 2026-07-29 | Phase 1 | Evidence source coverage | PASS | `zalo_scan_evidence.py` 90.4%; `rule_classifier.py` 86.8%; `zalo_evidence.py` 97.5%; `zalo_safety.py` 95.6% | Codex |
| 2026-07-29 | Phase 1 | Full local quality gate | PASS | Ruff; Mypy 53 files; 152 tests; overall coverage 77.6%; schema/smoke/frontend/compile pass | Codex |
| 2026-07-29 | Phase 1 | Pre-runtime database backup | PASS | `.backups/zaloAutomation-before-phase1-runtime-dryrun-20260729-184146.db`; integrity `ok`; 774,144 bytes | Codex |
| 2026-07-29 | Phase 1 | External dry-run job `#128` | ESCALATED | Job `SUCCESS`, `dry_run=true`, `mutates_external=0`, audit violations `0`; 43 groups are `INSUFFICIENT_EVIDENCE` and 3 are exempt at session 14, but all 46 lack Zalo scan evidence because Zalo reported incomplete history sync; 0 screenshots, including GB38/GB40/GB41. No BCNB tick occurred. | Codex |
| 2026-07-29 | Phase 1 | Zalo history sync persistence | PARTIAL | Sync link clicked. Banner disappeared after 180 seconds but reappeared after page reload in the same `zalo_session`; screenshots `runtime/phase1-zalo-sync-persist-*.png`, SHA-256 before `5edb3f3932ef4ec1dca44e206174c42d090c258c5ee771c24c5aef7556a29974`, after `167f733188afa62b5d878c1ec565465646bc3a38c86ca2127089cd8ff2e596b9`. | Codex |
| 2026-07-29 | Phase 1 | External dry-run job `#129` | ESCALATED | Job `SUCCESS`, `dry_run=true`, `mutates_external=0`, audit violations `0`; results unchanged: 43 `INSUFFICIENT_EVIDENCE`, 3 exempt, 0 report/latest/oldest images. GB38/GB40/GB41 remain manual-review only. No BCNB tick occurred. | Codex |
| 2026-07-29 | Phase 1 | Phone-assisted Zalo sync preflight | PASS | Banner absent before and after reload in the persistent `zalo_session`; screenshot `runtime/phase1-zalo-phone-sync-after-reload.png`; SHA-256 `0787f6dc9595f5ad427f2bf825e7b73fcf7b0f4ce94d2b4acbf4e7135c38a048`. | Codex |
| 2026-07-29 | Phase 1 | External dry-run job `#133` | PARTIAL | Job `SUCCESS`, `dry_run=true`, `mutates_external=0`; 46 results: 42 `SUBMITTED_VERIFIED`, 2 `SUBMITTED_LATE_VERIFIED`, 2 `NOT_FOUND_VERIFIED`; 48/48 evidence files exist and match stored SHA-256; six external audits are all `DRY_RUN`; no BCNB write. GB38/GB40/GB41 are submitted with report images. | Codex |
| 2026-07-29 | Phase 1 | Negative evidence visual review | FAIL | Two `NOT_FOUND_VERIFIED` groups have both latest/oldest images and adequate recorded coverage, but the oldest screenshots do not visibly include the Zalo UI date separator; an in-message date is not accepted as proof of UI time. | Codex |
| 2026-07-29 | Safety | Unexpected production-mode job `#132` | SAFE FAILURE / OPEN RISK | Pre-existing job used `dry_run=false`, `mutates_external=1` and failed safely with zero audit rows and zero unresolved actions. Root `.env` remains production, so normal UI scan is unsafe until configuration is deliberately changed. | Codex |
| 2026-07-29 | Phase 1 | Pre-runtime database backup | PASS | `.backups/zaloAutomation-before-phone-sync-dryrun-20260729-194723.db`; integrity `ok`; 917,504 bytes; SHA-256 `F6F9F0A648E1522EA15070449D16CC2A4BBD939188964E73A92EAFBA4BB107A2`. | Codex |
| 2026-07-29 | Phase 1 | Source backup before date-separator capture change | PASS | `.backups/zaloAutomation-before-evidence-date-screenshot-20260729-195806.zip`; 224,764 bytes; SHA-256 `BA6954AF6EF6BE75ABCF5EEEAEB44D8D4DB691DAA13722BAD4C43410A4DC16EC`. | Codex |
| 2026-07-29 | Phase 1 | Date-separator capture quality gate | PASS | Ruff; Mypy 53 files; 153 tests; overall coverage 77.7%; `zalo_scan_evidence.py` 90.1%; `zalo_evidence.py` 97.6%; schema/smoke/frontend/compile pass. | Codex |
| 2026-07-29 | Phase 1 | Pre-runtime database backup for job `#134` | PASS | `.backups/zaloAutomation-before-evidence-screenshot-dryrun-20260729-200321.db`; integrity `ok`; 1,003,520 bytes; SHA-256 `E7A398412D145600E88C565194BD86F2DBC07C0174A78173133FB1816B7068EC`. | Codex |
| 2026-07-29 | Phase 1 | External dry-run job `#134` | PASS | Job `SUCCESS`, request `dry_run=true`, `execution_mode=dry-run`, `mutates_external=0`; 46 results: 42 `SUBMITTED_VERIFIED`, 2 `SUBMITTED_LATE_VERIFIED`, 2 `NOT_FOUND_VERIFIED`; 50/50 evidence files exist and match stored SHA-256; six audits are `DRY_RUN`, before/after unchanged; active jobs `0`; database integrity `ok`, FK violations `0`. GB38/GB40/GB41 all have report evidence. | Codex |
| 2026-07-29 | Phase 1 | Negative evidence visual review after fix | PASS | Both negative groups show the real Zalo UI separator `T4 15/07/2026` in `job-134/oldest-d920d7411d.png` and `job-134/oldest-2c208fc694.png`; a third `oldest_message` image preserves the message at that boundary. Sample oldest SHA-256 `19B135D0C7C0861EF3054FCD4B58CA288C5D74EA886CEB91251611BE84B81602`. | Codex |
| 2026-07-29 | Phase 1 | Source backup before exact group matching | PASS | `.backups/zaloAutomation-before-exact-group-match-20260729-201445.zip`; 142 entries; 225,705 bytes; SHA-256 `7D1AE7F28A82289AA682443CB6271FBEDC00EE4AD2CB4F627DAEA91B46041860`. | Codex |
| 2026-07-29 | Phase 1 | Exact group matching red-green regression | PASS | Initial focused run reproduced two failures: GB40 was classified while actual header was GB41, and report screenshot failure still displayed “Nộp đúng hạn”. After the source fix, 26/26 focused tests passed; nearby `GB4/GB40`, `GB40/GB41` and `PTI08/PTI09` pairs fail closed. | Codex |
| 2026-07-29 | Phase 1 | Exact group matching full quality gate | PASS | Ruff; Mypy 53 files; 156 tests; overall coverage 77.7%; `zalo_scan_evidence.py` 90.3%; `zalo_evidence.py` 97.6%; `zalo_safety.py` 95.6%; schema/smoke/frontend/compile pass. | Codex |
| 2026-07-29 | Phase 1 | Pre-runtime database backup for job `#135` | PASS | `.backups/zaloAutomation-before-exact-group-dryrun-20260729-201922.db`; integrity `ok`; FK violations `0`; 1,093,632 bytes; SHA-256 `EB2D76E10F4AF059C0D6CD2D1B7E5247576F998827657FB57A49D085227BC905`. | Codex |
| 2026-07-29 | Phase 1 | Exact group matching dry-run job `#135` | PASS | Job `SUCCESS`, request `dry_run=true`, `execution_mode=dry-run`, `mutates_external=0`; 46 results with identity mismatch count `0`; 42 `SUBMITTED_VERIFIED`, 2 `SUBMITTED_LATE_VERIFIED`, 2 `NOT_FOUND_VERIFIED`; 50/50 images exist and match SHA-256; six audits are `DRY_RUN` with before/after unchanged; active jobs `0`; database integrity `ok`, FK violations `0`. GB38, GB40, GB41 and PTI09 resolve to distinct verified groups. | Codex |
| | | | | | |

## 10. Definition of Done toàn dự án

Dự án chỉ được xem là hoàn thiện khi:

- [ ] Không còn finding P1.
- [ ] P2 đã sửa hoặc có accepted-risk ghi rõ chủ sở hữu và thời hạn.
- [ ] Tất cả acceptance criteria của Giai đoạn 0–8 đạt.
- [ ] Có evidence cho từng tiêu chí.
- [ ] Git/checkpoint và backup/rollback hoạt động.
- [ ] Local/dev runtime acceptance đạt.
- [ ] Nếu chạy production: smoke test có phê duyệt và bằng chứng.
- [ ] Tài liệu vận hành, backup, restore, deploy và xử lý sự cố được cập nhật.
- [ ] Không còn quyết định nghiệp vụ quan trọng nằm dưới dạng hằng số không tài liệu.

**Không được dùng “test tổng đã xanh” làm bằng chứng duy nhất để tuyên bố hoàn thiện.**
