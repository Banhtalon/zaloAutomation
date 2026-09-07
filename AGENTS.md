# AGENTS.md — Zalo Automation / QQ AI Workflow v9

## Active workflow authority

This project adopts QQ Evidence-Gated Workflow v9 from the pinned canonical
source. The only active workflow authority is the canonical set under
`.ai-workflow/`: `V9_CANONICAL_SPEC.md`, `POLICY.md`, `STATE_MACHINE.md`,
`DATA_MODEL.md`, `RISK_RULES.json`, `CONTROLLER_OPERATIONS.md`, the project
profile, and the frozen verification manifest for the current task revision.

Routing is `MANUAL`. Risk and complexity are independent; risk is monotonic
within a scope revision. Deterministic gates and independent qualified review
outrank model recommendations. The Owner provides product intent and
functional acceptance only, and must never be asked to inspect code, tests,
SQL, CI, schemas, security controls, logs, or technical evidence.

`AGENT_WORKFLOW.md`, `TASK_TEMPLATE.md`, `.codex/config.toml`, and
`.codex/agents/*.toml` are
retained historical/context artifacts and are explicitly **SUPERSEDED BY QQ AI
WORKFLOW v9** as workflow authority. They must not override `.ai-workflow/`.
Product requirements, roadmap, and historical evidence remain retained.

Before a substantive change, read the canonical specification and policy,
`.ai-workflow/PROJECT_PROFILE.json`, the current task and frozen manifest,
`CONTROLLER_OPERATIONS.md`, plus `PROJECT_SUMMARY.md`, `PROJECT_STATE.md`,
`PROJECT_DECISIONS.md`, and the relevant report. Missing, stale, conflicting,
or unverifiable control state fails closed.

This migration is workflow/configuration/documentation-only for the existing
FastAPI/Python/Playwright/Gemini/SQLite application. Preserve the current
product phases and dirty worktree; do not rebuild product code.

## Phạm vi

Hướng dẫn này áp dụng cho toàn bộ repository. Nếu một thư mục con có
`AGENTS.md` riêng, hướng dẫn gần file đang làm việc hơn được ưu tiên trong phạm
vi thư mục đó.

## Trình tự bắt đầu mỗi task

1. Đọc yêu cầu hiện tại của người dùng và xác định đúng một mục tiêu chính.
2. Đọc `PROJECT_SUMMARY.md` để hiểu contract và kiến trúc dự án.
3. Đọc `PROJECT_STATE.md` để biết trạng thái, blocker và bằng chứng gần nhất.
4. Đọc `PROJECT_DECISIONS.md` để không đảo ngược quyết định đã được chấp nhận.
5. Với task nhiều bước, dùng `TASK_TEMPLATE.md` hoặc một execution plan có
   `Goal`, `Context`, `Constraints` và `Done when` trước khi sửa code.
6. Kiểm tra source, `git status` và artifact hiện tại. Không mặc định tài liệu
   trạng thái vẫn còn mới.

## Điều phối lịch sử (SUPERSEDED BY QQ AI WORKFLOW v9)

Toàn bộ nội dung dưới đây chỉ được giữ để truy nguyên lịch sử; không được áp
dụng như policy active khi khác với canonical v9.

- Đọc và tuân thủ [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) cho mọi substantive
  repository task; file này là nguồn chi tiết cho sequence và schema.
- Root dùng `gpt-5.6-sol` với reasoning `low` mặc định. Chỉ nâng lên
  `medium` khi có conflict vật chất, failover, thiếu quyết định Owner hoặc
  cần tổng hợp bằng chứng mâu thuẫn; độ dài implementation không phải lý do.
- Delegation phải là lời gọi `spawn_agent` thật, không phải chỉ ghi tên vai trò.
  Task `check`, `review`, `kiểm tra tiến độ` và acceptance review phải route tới
  đúng một Terra `gpt-5.6-terra`/`xhigh` với `fork_turns="none"`, không spawn
  Luna. Task implement/fix/build phải route tuần tự tới Luna
  `gpt-5.6-luna`/`max` rồi Terra `gpt-5.6-terra`/`xhigh`; mọi model override
  dùng `fork_turns="none"`.
- Root không lặp lại inspection, implementation, test hoặc review đã giao cho
  agent. Luna và Terra không spawn agent khác. Nếu Terra từ chối, chỉ một vòng
  repair tự động được phép: dùng `followup_task` tới agent hiện có, rồi Terra
  rereview; sau lần từ chối thứ hai phải giữ blocker và dừng.
- Khi Luna available, Sol không trực tiếp sửa application code. Mọi exception
  phải nêu trong Final Execution Report. Final trả Owner chỉ gồm outcome,
  evidence, blockers và next action, thường dưới 500 từ.

## Vai trò tài liệu

- `PROJECT_SUMMARY.md`: specification và kiến trúc tổng quan.
- `PROJECT_STATE.md`: snapshot ngắn của trạng thái đã kiểm chứng gần nhất.
- `PROJECT_DECISIONS.md`: quyết định lâu dài, ghi nối tiếp và không viết lại
  lịch sử.
- `PROJECT_COMPLETION_PLAN.md`: roadmap, acceptance criteria và evidence lịch
  sử; không phải nguồn trạng thái hiện tại.
- `VERSION.md`: lịch sử phiên bản.
- `docs/*REPORT*.md`: báo cáo theo phạm vi và thời điểm cụ thể.

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
