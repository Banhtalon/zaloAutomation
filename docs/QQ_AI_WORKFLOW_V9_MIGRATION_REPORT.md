# Báo cáo migrate QQ AI Workflow v9 — Zalo Automation

## Trạng thái Owner

STATUS: BLOCKED

Bạn yêu cầu: đưa project Zalo Automation hiện có sang QQ AI Workflow v9 mà
không làm lại product và không chạm dữ liệu/live state.

Hệ thống đã xác nhận: bộ workflow v9 canonical đã được cài ở local, routing
đang là `MANUAL`, profile/risk rules đã cấu hình riêng cho FastAPI/Python/
Playwright/Gemini/SQLite, và các gate hermetic đều có bằng chứng mới.

Bạn cần làm: NONE

Không cần bạn làm: đọc code, test, SQL, CI, security, log, chọn model, debug,
cấp secret, vận hành Controller hoặc thao tác kỹ thuật.

Giới hạn hiện tại: đây là local/source và local-hermetic evidence. Adoption gate
vẫn chờ independent Codex/Terra review và final immutable-manifest audit; chưa
phải hosted, live, staging, canary hoặc production acceptance.

## Phạm vi và baseline

- Canonical source: `Banhtalon/qq-ai-workflow`, exact HEAD
  `f2b6d7f7bf789837e64c0d3243f98829589a3b63`.
- Branch tạo riêng: `chore/adopt-qq-ai-workflow-v9`.
- Approved mode: workflow/configuration/documentation only.
- Project base/head trước migration:
  `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`.
- Immutable baseline: ID `qq-v9-f28739cd42b04203809c93f23d159dc6`, 172 entries,
  manifest SHA-256
  `0457ead737d41d09234190db136702ae7b53030adad7bc34841d1fbc754f08e6`.
- Baseline bao gồm mọi status/hash trước task; mọi path dirty/untracked ngoài
  allowlist vẫn được giữ nguyên. Final comparison là bước bắt buộc của Root
  trước Terra.
- Luna pre-audit snapshot (Root phải recapture độc lập sau mọi bookkeeping):
  final manifest có 207 entries; comparison artifact SHA-256
  `036d247adc00612c2440d3103478649f41b8a0d89e13eaf2506dd90ae1f1e42e`;
  task-owned delta 39, outside missing/new/status/hash mismatches `0/0/0/0`,
  `OUT_OF_SCOPE_UNCHANGED=PASS`. Final manifest bytes và hash được giữ ngoài
  workspace để tránh self-reference trong report.

## Phase A — Inventory và disposition

Inventory được thực hiện trước khi cài đặt. Không có product code nào được sửa
ở bước inventory.

| Artifact/nhóm | Disposition | Xử lý |
|---|---|---|
| `.ai-workflow/**` và templates v9 | REPLACE_WITH_V9 | Cài bộ canonical revision 2 từ exact HEAD; profile, task và manifest được cấu hình cho project |
| `AGENTS.md` | REPLACE_WITH_V9 | Giữ safety supplement của project, đặt v9 làm authority duy nhất |
| `GEMINI.md` | REPLACE_WITH_V9 | Entry point theo v9, routing manual, không tạo policy thứ hai |
| `.gitattributes`, `.gitignore` | REPLACE_WITH_V9 | Merge các rule v9 vào rule Windows/Python hiện có; không xóa rule local |
| `package.json`, `README.md`, `VERSION` | REPLACE_WITH_V9 | Thêm package/README/version canonical; README ghi rõ adoption hiện có |
| `scripts/{freeze-manifest,inspect-risk,install,owner-status,prepare-attempt,run-pilots,run-redacted,verify-task}.mjs` | REPLACE_WITH_V9 | Cài script canonical tại path được phép |
| `scripts/lib/{control,pilot-fixture,workflow}.mjs`, `test/workflow.test.mjs` | REPLACE_WITH_V9 | Cài thư viện/test canonical dependency-free |
| `evidence/pilot-summary.json` | REPLACE_WITH_V9 | Sinh lại bằng pilot runner, chỉ chứa synthetic evidence |
| `AGENT_WORKFLOW.md`, `TASK_TEMPLATE.md`, `.codex/config.toml`, `.codex/agents/*.toml` | SUPERSEDE | Giữ nguyên để bảo toàn lịch sử/context; `AGENTS.md` đánh dấu rõ SUPERSEDED BY QQ AI WORKFLOW v9 |
| `PROJECT_SUMMARY.md`, `PROJECT_COMPLETION_PLAN.md`, `VERSION.md`, `docs/*` report hiện có | KEEP | Product specification, roadmap và historical evidence không xung đột nên không xóa |
| `CHECK_QUALITY.ps1`, `QUALITY.bat`, Python scripts/tests, `.env.example`, `deploy/**` | KEEP | Gate/product tooling hiện có; không đổi nội dung |
| Artifact dead/conflicting cần xóa | REMOVE | Không có; không xóa product requirements, CI hoặc historical evidence |

Canonical installer không được chạy trực tiếp vì installer sẽ tạo
`scripts/qq-ai-workflow/**`, nằm ngoài allowlist. Các file canonical cần thiết
được copy có kiểm soát vào đúng các path allowlist sau khi đã kiểm tra chúng
vắng mặt; không overwrite mù artifact hiện có.

## Phase B/C — v9 và project configuration

Đã cài:

- Canonical spec, policy, actor registry, state machine, data model, Owner
  status, Controller operations và new-project adoption guide.
- Canonical task/verification templates và task snapshot tại
  `.ai-workflow/tasks/qq-v9-migration/`.
- `.ai-workflow/PROJECT_PROFILE.json` từ example, với stack FastAPI/Python/
  Playwright/Gemini/SQLite, evidence tiers
  `LOCAL_HERMETIC/CI/HOSTED/LIVE/PRODUCTION`, Owner không làm technical
  review, và `routing_mode=MANUAL`.
- `.ai-workflow/RISK_RULES.json` project-specific. Risk/complexity là hai
  trục độc lập; task snapshot khai báo `YELLOW` và complexity `L`.

Risk controls:

| Risk surface | Mức | Required roles/điều kiện |
|---|---|---|
| workflow, app/runtime, CI/quality | YELLOW | `QUALIFIED_REVIEWER` |
| authentication/permission, secrets/credentials | RED | Reviewer + Technical Operator; secret path/material fail closed |
| database/schema/migrations | RED | Reviewer + Technical Operator |
| deployment/infrastructure | RED | Reviewer + Technical Operator |
| Playwright/Zalo/BCNB/Gemini/Form/external services | RED | Reviewer + Technical Operator |
| user/student/privacy data | RED | Reviewer + Technical Operator |
| production writes, destructive/privileged diff | RED | `STOP_AND_ESCALATE` |

Risk floor không được hạ trong cùng scope revision. Không có selector,
fallback, fuzzy identity, secret hoặc live data nào được thêm.

## Phase D — Frozen deterministic verification

Manifest được tạo và freeze trước khi chạy pilot:

- Manifest:
  `.ai-workflow/tasks/qq-v9-migration/verification-manifest.json`.
- Lock digest:
  `sha256:ba9ff744ed9ae49e86eb62fcfaafe4cfe6163bf78c671f664de80188d204bb1b`.
- Base SHA trong manifest:
  `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`.
- Criteria: canonical artifact/manual routing; profile/risk coverage; workflow
  tests + three pilots; preservation/no live or destructive action.
- Frozen argv gates: canonical Node tests; profile/risk validation; and
  `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File
  CHECK_QUALITY.ps1 -Isolated`.
- `prepare-attempt.mjs`, `inspect-risk.mjs` và `verify-task.mjs` chưa
  chạy trên project candidate: chúng yêu cầu external Controller snapshot,
  clean-slate candidate/head binding và/hoặc committed clean worktree. Đây là
  giới hạn authority/scope có chủ đích, không phải bỏ qua gate.

## Phase F — Fresh verification

| Command | Result | Boundary |
|---|---|---|
| `npm test` | Exit 0; 23/23 canonical tests pass | Local workflow tests |
| Node profile/risk validator | Exit 0; `PROFILE_RISK_VALID` | JSON/control contract |
| `node scripts/run-pilots.mjs --output evidence/pilot-summary.json` | Exit 0; summary PASS; GREEN/YELLOW/RED all pass; `routing_automation_present=false`; `adoption_decision=PENDING_INDEPENDENT_REVIEW` | Synthetic only |
| `node scripts/owner-status.mjs .ai-workflow/tasks/qq-v9-migration/task.json` | Exit 0; Owner action `NONE`; technical duties excluded | Owner-facing mapping |
| `.\\CHECK_QUALITY.ps1 -Isolated` | Final rerun exit 0; regression 490 tests in 180.717s, Ruff/mypy pass (74 files), Phase 3–9B, dependency, schema/active-jobs, hermetic app smoke, frontend and compile pass; final marker `QUALITY GATE PASSED` | Private isolated DB/config/cache/profile/lock context only |

Một lần quality đầu tiên dừng ở frontend contract vì package canonical đặt
`"type": "module"` trong khi existing `tests/check_template_js.js` dùng
CommonJS `require`. Đã sửa duy nhất compatibility setting ở workflow
`package.json` thành `"type": "commonjs"`; canonical scripts vẫn là
`.mjs`. Rerun đầy đủ pass như bảng trên.

Pilot semantics:

- GREEN: synthetic `notes.md`, effective `GREEN`, complexity `XL`,
  `CONTINUE_WITH_CONTROLS`, verification PASS.
- YELLOW: synthetic `scripts/check.mjs`, effective `YELLOW`, complexity
  `S`, required role `QUALIFIED_REVIEWER`, verification PASS.
- RED: synthetic `auth/login.ts`, effective `RED`, required Reviewer +
  Technical Operator, action `STOP_AND_ESCALATE`; no unsafe gate executed
  (`gates_executed=false`).

## Phase G — Preserve progress

Project hiện ở Stage 3/GA38 local workflow checkpoint với historical Terra
acceptance cho bounded local/single-target scope. Migration không reset roadmap,
không đổi product phase, không retry canary và không chuyển local evidence thành
live/production acceptance. Các report/state/decision lịch sử được giữ nguyên;
chỉ append state/decision migration bookkeeping.

## An toàn và blocker

- Không đọc/ghi nội dung `.env`; không đổi database thật; không mở browser
  profile/live endpoint; không login/logout/QR, scan, chat, BCNB/Form submit,
  deployment, seed, migration, commit hoặc push.
- Isolated quality chỉ tạo temporary test context riêng rồi tự dọn; mọi browser
  behavior nếu có đều nằm trong synthetic/hermetic tests, không phải profile/live
  action.
- Không có secret trong file mới, report, pilot evidence hoặc output được lưu.
- Không có product source path ngoài allowlist thay đổi theo status/hash.
- Blocker hiện tại: final marker-safe manifest comparison của Root và
  independent Terra review; external Controller readiness/hosted operations
  cũng chưa được thiết lập trong task này.
- Local/source acceptance ≠ CI/hosted/live/staging/canary/production
  acceptance.

## Next action an toàn

Root recaptures final manifest against baseline
`qq-v9-f28739cd42b04203809c93f23d159dc6`, verifies
`OUT_OF_SCOPE_UNCHANGED=PASS`, reviews task-owned diff against before-state,
then dispatches exactly one read-only Terra review. Không yêu cầu Owner làm
technical work.

## Exact evidence references

- Canonical commit: `f2b6d7f7bf789837e64c0d3243f98829589a3b63`.
- Branch: `chore/adopt-qq-ai-workflow-v9`.
- Baseline ID/count/SHA: `qq-v9-f28739cd42b04203809c93f23d159dc6` /
  172 / `0457ead737d41d09234190db136702ae7b53030adad7bc34841d1fbc754f08e6`.
- Frozen lock digest:
  `sha256:ba9ff744ed9ae49e86eb62fcfaafe4cfe6163bf78c671f664de80188d204bb1b`.
- Pilot artifact: `evidence/pilot-summary.json`.
- Task artifact: `.ai-workflow/tasks/qq-v9-migration/task.json`.

## Technical Operator activation — đóng blocker P1 của Terra

Ngày 2026-09-07, Owner đã ủy quyền riêng cho Technical Operator đóng đúng
blocker về committed candidate và external Controller; ủy quyền này không mở
rộng sang product code, database, `.env`, browser, Zalo/BCNB/Form, deploy,
production hay live state.

Trình tự bất biến được chốt trước commit:

1. Chỉ stage và commit các path trong allowlist của task trên branch
   `chore/adopt-qq-ai-workflow-v9`; không stage toàn bộ worktree, không amend,
   push, merge hoặc rewrite history.
2. Giữ nguyên frozen manifest và lock hiện có; Controller ngoài mọi candidate
   checkout sẽ pin task `TASK-QQ-V9-MIGRATION`, scope revision `1`, base SHA
   `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`, manifest lock
   `sha256:ba9ff744ed9ae49e86eb62fcfaafe4cfe6163bf78c671f664de80188d204bb1b`
   và routing `MANUAL`.
3. Tạo clean detached worktree ngoài repository từ commit migration mới, sau
   đó dùng trusted canonical `inspect-risk.mjs` và `verify-task.mjs` với
   Controller digest mới; evidence JSON cũng nằm ngoài Git.
4. Không sửa repository files sau commit để exact candidate head giữ nguyên;
   digest/head và kết quả lifecycle cuối được trả trong execution packet và
   external Controller record, không ghi ngược vào candidate.

Baseline immutable mới cho vòng activation là
`qq-v9-controller-8378b9cb4c554fd18a755554a4f72e6e` (207 entries; SHA-256
`b30866ca380b6841a3e7f2a05bb35f48bc5669d392b4bdb6df18f0853b14a114`).
Trước khi chạy lifecycle, mọi path ngoài allowlist phải giữ nguyên status và
marker/hash của baseline này; mismatch là STOP_AND_ESCALATE.
