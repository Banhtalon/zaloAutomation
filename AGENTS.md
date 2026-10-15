# AGENTS.md — Zalo Automation

Preserve the existing FastAPI/Python/Playwright/Gemini/SQLite application and
all unfinished product work. Do not rebuild product code from documentation.

## Phạm vi

Hướng dẫn này áp dụng cho toàn bộ repository. Nếu một thư mục con có
`AGENTS.md` riêng, hướng dẫn gần file đang làm việc hơn được ưu tiên trong phạm
vi thư mục đó.

## Trình tự bắt đầu mỗi task

1. Đọc yêu cầu hiện tại của người dùng và xác định đúng một mục tiêu chính.
2. Đọc `PROJECT_SUMMARY.md` để hiểu contract và kiến trúc dự án.
3. Đọc `PROJECT_STATE.md` để biết trạng thái, blocker và bằng chứng gần nhất.
4. Đọc `PROJECT_DECISIONS.md` để không đảo ngược quyết định đã được chấp nhận.
5. Với task nhiều bước, lập kế hoạch ngắn nêu mục tiêu, phạm vi an toàn và
   điều kiện hoàn thành trước khi sửa code.
6. Kiểm tra source, `git status` và artifact hiện tại. Không mặc định tài liệu
   trạng thái vẫn còn mới.

## Vai trò tài liệu

- `PROJECT_SUMMARY.md`: specification và kiến trúc tổng quan.
- `PROJECT_STATE.md`: snapshot ngắn của trạng thái đã kiểm chứng gần nhất.
- `PROJECT_DECISIONS.md`: quyết định lâu dài, ghi nối tiếp và không viết lại
  lịch sử.
- `PROJECT_COMPLETION_PLAN.md`: roadmap và acceptance criteria hiện hành;
  không phải nguồn trạng thái hiện tại.
- `CLASS_LIFECYCLE.md`: contract nghiệp vụ vòng đời lớp.
- `VERSION.md`: lịch sử năng lực; không tự chứng minh production acceptance.
- `deploy/staging/README.md`: hướng dẫn staging tùy chọn, chỉ dùng khi Owner yêu
  cầu triển khai qua mạng.

Không tạo báo cáo theo từng task nếu kết quả có thể cập nhật ngắn gọn vào
`PROJECT_STATE.md`. Báo cáo tạm hoặc log chi tiết phải nằm ngoài repository.

Thứ tự tin cậy là: yêu cầu hiện tại của người dùng; source/runtime an toàn được
kiểm chứng; test, Git diff và artifact; sau đó mới đến tài liệu tóm tắt. Khi có
mâu thuẫn, phải xác minh lại và nêu rõ tài liệu nào đã cũ.

## Chế độ làm việc

### Check, review hoặc kiểm tra báo cáo

- Mặc định chỉ đọc.
- Tìm artifact mới nhất, đối chiếu claim với source hiện tại và chạy lại gate
  liên quan khi khả thi.
- Báo đúng lệnh, exit code, số test và giới hạn của bằng chứng.
- Không sửa source, `.env`, database, browser state hoặc hệ thống ngoài.
- Không tự cập nhật `PROJECT_STATE.md`; nếu cần, đề xuất phần thay đổi để người
  dùng phê duyệt riêng.

### Change, build hoặc fix

- Chỉ sửa trong phạm vi được yêu cầu; giữ nguyên thay đổi không liên quan của
  người dùng.
- Không `reset`, `clean`, `checkout`, commit, push, deploy, seed hoặc migrate
  nếu chưa được yêu cầu rõ ràng.
- Với thay đổi có rủi ro lên dữ liệu/cấu hình, tạo backup phù hợp và kiểm tra
  khả năng khôi phục trước khi sửa.
- Sau khi sửa, review diff và chạy gate tương xứng với rủi ro.

### Browser, external action hoặc production

- Fail closed: preview/audit trước, mutate sau.
- Yêu cầu target chính xác, bằng chứng mới, owner approval và rollback sẵn sàng.
- `BCNB_DRY_RUN=false` không phải là quyền chạy `/run-scan` và không biến batch
  scan thành single-target canary.
- Không mở rộng local acceptance thành staging, pilot hoặc production
  acceptance nếu chưa có human gate riêng.

## An toàn dữ liệu

- Không ghi credential, token, cookie, storage state, nội dung `.env`, dữ liệu
  học viên thô hoặc fingerprint lấy từ hệ thống live vào Git hay tài liệu.
- Chỉ dùng fixture tổng hợp đã loại dữ liệu nhạy cảm trong test và báo cáo.
- Candidate thiếu, trùng, mơ hồ hoặc không khớp phải giữ ở trạng thái review;
  không fuzzy-match hay tự suy luận identity mới.
- Giới hạn tài nguyên phải được thực thi trước khi materialize dữ liệu lớn.

## Kiểm chứng

Quality gate đầy đủ của repository:

```powershell
.\CHECK_QUALITY.ps1
```

Khi báo kết quả, phân biệt rõ:

- gate đầy đủ với gate targeted;
- kết quả chạy mới với số liệu lịch sử;
- local/source acceptance với production acceptance;
- test xanh với contract nghiệp vụ đã được chứng minh.

Không dùng “toàn bộ test đã xanh” làm bằng chứng duy nhất cho hành vi rủi ro.

## Trước khi kết thúc task có thay đổi

1. Chụp final manifest và so sánh với immutable baseline: mọi path dirty có
   trước ngoài task allowlist phải giữ nguyên status và SHA-256; path mới hoặc
   thay đổi ngoài allowlist là FAIL/escalate. Chỉ review `git diff`/delta riêng
   cho các path thuộc task allowlist.
2. Chạy các gate đã nêu trong `Done when` và ghi exact result.
3. Cập nhật `PROJECT_STATE.md` bằng trạng thái, thời điểm xác minh, lệnh/bằng
   chứng, blocker và bước an toàn tiếp theo.
4. Chỉ append `PROJECT_DECISIONS.md` khi có quyết định kiến trúc, nghiệp vụ,
   an toàn hoặc quy trình có giá trị lâu dài.
5. Không tuyên bố hoàn thành nếu còn tiêu chí `Done when` chưa đạt.

## Flow AI hiện hành — chuyển đổi 2026-10-03

- Nguồn: QQ AI Workflow 10.1.0-rc.9, main commit `3056879dc47d9a3b2846b99c1178b9fd8b553b8f`.
- [Canonical](.ai-workflow/V10_CANONICAL_SPEC.md) là nguồn quy tắc điều phối AI; [HANDOFF](HANDOFF.md) là điểm vào, [project profile](.ai-workflow/PROJECT_PROFILE.json) ghi thông tin dự án. Các yêu cầu hiện tại của Owner và ranh giới an toàn Zalo ở trên vẫn có ưu tiên.
- Owner đã yêu cầu chuyển lại sang flow hiện hành; DEC-027 giữ nguyên như lịch sử gỡ flow trước đây. Trạng thái mới nằm trong PROJECT_STATE.md. Không khôi phục packet, tài khoản hoặc receipt cũ.
- Việc mới dùng Direct theo canonical: Gemini thực hiện, controller kiểm tra, Luna Max review độc lập, kiểm tra sản phẩm nếu áp dụng, Owner nghiệm thu. Ngoại lệ ASSISTED chỉ theo canonical trước dispatch và có lý do được ghi. Tác vụ check vẫn chỉ đọc.
- Bộ flow dùng requirements-workflow.txt và .venv/Scripts/python.exe riêng; ứng dụng giữ requirements.txt và venv/Scripts/python.exe. Không dùng Python toàn máy để gọi worker.
- Đây là cài đặt source/local; chưa có bằng chứng gọi Gemini/Luna thật hay nghiệm thu production. Không tạo task từ HEAD cũ khi workspace còn thay đổi chưa commit; giữ nguyên công việc đó và báo điều kiện còn thiếu. Commit/push vẫn cần yêu cầu rõ ràng của Owner.
