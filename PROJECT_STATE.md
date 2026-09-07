# PROJECT STATE — Zalo Automation

> Đây là snapshot điều hướng, không thay thế source, test, Git diff, database
> gate hoặc bằng chứng runtime. Mọi dữ liệu dễ thay đổi phải được xác minh lại
> trước khi hành động.

## Metadata

- `updated_at`: 2026-09-07 10:45 (Asia/Ho_Chi_Minh)
- `updated_by`: Codex Luna
- `task`: QQ AI Workflow v9 adoption — local workflow/configuration only;
  awaiting independent Codex/Terra review
- `branch_at_start`: `chore/adopt-qq-ai-workflow-v9`
- `head_at_start`: `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`
- `worktree_at_start`: DIRTY — immutable baseline
  `qq-v9-f28739cd42b04203809c93f23d159dc6` has 172 entries; all
  pre-existing paths outside the QQ v9 allowlist remain Owner-owned

## Historical orchestration objective (2026-08-29)

Áp dụng contract điều phối multi-model mới cho root Sol, Luna implementation và
Terra review: Sol low mặc định; read-only route đúng một Terra và không có Luna;
implement/fix/build route Luna rồi Terra tuần tự; mỗi model override dùng
`fork_turns="none"`; không lặp delegated work; và tối đa một repair loop bằng
`followup_task` tới agent hiện có.

- Allowlist chỉ gồm `AGENTS.md`, `AGENT_WORKFLOW.md`, `TASK_TEMPLATE.md`,
  `PROJECT_STATE.md`, `PROJECT_DECISIONS.md`, `.codex/config.toml`,
  `.codex/agents/luna-implementer.toml` và
  `.codex/agents/terra-reviewer.toml`.
- Phạm vi là workflow/config documentation local-only; không có application,
  test, `.env`, database, browser, Zalo/BCNB live action hoặc external mutation.
- Quyết định mới được append tại `DEC-005` và supersede `DEC-004`; lịch sử cũ
  vẫn được giữ lại.

## Lịch sử workflow/documentation repair (không áp dụng selector repair)

- `repair_round`: 1 — cập nhật `AGENTS.md`, `AGENT_WORKFLOW.md`,
  `TASK_TEMPLATE.md` và `PROJECT_STATE.md` để bắt buộc baseline manifest ngoài
  workspace, allowlist/before-hash, final comparison và verdict
  `OUT_OF_SCOPE_UNCHANGED` trước Terra.
- Limitation cần giữ nguyên: handoff ban đầu ghi **122 entries chỉ có count**,
  không có per-path status + SHA-256 manifest. Vì vậy không thể retroactively
  prove mọi pre-existing file đã không bị thay đổi; task hiện chỉ có scoped
  evidence, không được tuyên bố preservation toàn worktree.
- Acceptance boundary của repair là workflow/documentation local trong Git;
  không bao gồm application behavior, browser, database, staging hay production.

## Lịch sử workflow/documentation repair round 2 (không áp dụng selector repair)

- `repair_round`: 2 — thay Done when trong `TASK_TEMPLATE.md` bằng điều kiện
  final immutable-manifest comparison: mọi path ngoài task allowlist phải giữ
  nguyên status/hash; `git diff` chỉ review task-owned paths theo before-hash và
  intended delta.
- Original limitation vẫn giữ nguyên: handoff ban đầu chỉ có count `122`, không
  có per-path status + SHA-256 manifest; không thể retroactively prove mọi
  pre-existing file unchanged. Bằng chứng hiện tại của repair này chỉ là targeted
  whitespace/static/scoped-status evidence, không phải preservation proof toàn
  worktree.
- Acceptance boundary: chỉ `TASK_TEMPLATE.md` và `PROJECT_STATE.md` trong local
  Git; không bao gồm source, runtime, browser, database, staging hay production.

## Lịch sử post-review state sync (không áp dụng selector repair)

- Đây là bằng chứng của workflow/documentation task trước; không phải verdict
  cho selector repair hiện tại.
- Project-scoped config/docs được chấp nhận **local-only**; chưa phải staging,
  pilot hoặc production acceptance.
- Root validation: system Python kiểm tra TOML/model/effort/sandbox với
  `FINAL_TOML_OK` exit `0`; static contract exit `0`; obsolete rule absent;
  whitespace pass; scoped status có 8 untracked paths; total status `124`.
- Exception: root thử `.\.venv\Scripts\python.exe` trước nhưng executable không
  tồn tại; sau đó system Python pass. Không có file mutation từ lần thử lỗi.
- Original limitation giữ nguyên: handoff ban đầu chỉ có **122-count**, không có
  per-path baseline status + SHA-256; không thể retroactively prove toàn bộ
  pre-existing worktree unchanged.
- Residual policy risks: giới hạn concurrency không phải runtime mutex; Luna có
  `workspace-write`, còn read-only behavior được enforced bằng policy/prompt.

## Current multi-model orchestration task (2026-08-29)

- Luna implementation status: **AWAITING CODEX REVIEW**. The supplied baseline
  `orchestration-c1f402f33c6042f4bab19c3984a239b9` contains 140 entries and the
  eight-path task allowlist; its manifest is stored outside the workspace.
- Current contract is documentation/config only: root Sol is `low` by default;
  read-only checks route to exactly one Terra and no Luna; implementation routes
  Luna then Terra sequentially; every model override uses `fork_turns="none"`;
  Luna/Terra do not spawn; and one repair loop uses `followup_task` only.
- Local acceptance boundary: TOML parsing, static contract text/routing/schema,
  whitespace and scoped diff checks only. No application source/test, `.env`,
  database, browser, Zalo/BCNB, staging, pilot or production action is in scope.
- Final manifest `orchestration-c1f402f33c6042f4bab19c3984a239b9-repair1-final-20260829`
  has been captured outside the workspace with zero out-of-scope mismatches;
  Terra independent review remains required before any acceptance claim. A
  second Terra rejection remains a blocker.

### Fresh evidence — 2026-08-29 00:56 (Asia/Ho_Chi_Minh)

| Lệnh/Artifact | Kết quả | Giới hạn |
|---|---|---|
| Python 3.13 `tomllib` parse + static routing/schema/obsolete-rule contract | Exit `0`: `TOML_OK`, `STATIC_CONTRACT_OK`, `OBSOLETE_CURRENT_RULES_ABSENT` | Chỉ docs/config contract; không chạy application tests |
| Trailing-whitespace scan trên đúng 8 path allowlist | Exit `0`: `WHITESPACE_OK` | Hygiene gate |
| `git diff --check --` | Exit `0` | Có cảnh báo line-ending lịch sử ở path ngoài scope; không có finding |
| `git diff --no-index --check -- NUL <allowlisted path>` cho 8 path | Exit `0`: `SCOPED_DIFF_CHECK_OK`; underlying exit `1` là expected untracked diff | Untracked path được kiểm tra không có whitespace finding |
| Supplied baseline-v2 compare: 140 entries với current 140 entries | Exit `0`: outside mismatch `0`, task delta `8`, missing `0`, `OUT_OF_SCOPE_UNCHANGED=PASS` | Sensitive baseline entries chỉ dùng marker, không in/hash nội dung |
| `git status --short -- <8 allowlisted paths>` | Exit `0`: scoped `8`; total status `140` | Tất cả 8 path là untracked task-owned paths; dirty work ngoài scope giữ nguyên |

- Local/source acceptance của contract đã đạt các gate trên; Terra independent
  review chưa được thực hiện trong lượt Luna này. Không có staging, pilot,
  production, browser hoặc external acceptance.

## Repair loop 1 — Terra findings closure (2026-08-29)

- Terra returned two rejected findings: P1 required an authoritative pre-final
  audit proving actual agent results/model-effort-fork/non-duplication and
  result-backed claims; P2 required one token-control policy plus Luna-specific
  exact-error enforcement.
- Fixed P1 in `AGENT_WORKFLOW.md` with a mandatory pre-final checklist. Fixed P2
  in `AGENT_WORKFLOW.md` with one Token control section and in
  `.codex/agents/luna-implementer.toml` with executor output enforcement.
  `TASK_TEMPLATE.md` now carries the audit evidence fields only.
- No application source/test, `.env`, database, browser or external system was
  touched. This repair is **AWAITING CODEX REVIEW**; no acceptance claim is
  made until Terra rereview returns an actual result.

### Fresh repair validation — 2026-08-29 06:45 (Asia/Ho_Chi_Minh)

| Lệnh/Artifact | Kết quả | Giới hạn |
|---|---|---|
| Python 3.13 `tomllib` parse + repair static contract | Exit `0`: `TOML_OK`, `STATIC_CONTRACT_OK`, `REPAIR_FINDINGS_COVERED`, `OBSOLETE_CURRENT_RULES_ABSENT` | Chỉ docs/config contract |
| Trailing-whitespace scan trên đúng 8 path allowlist | Exit `0`: `WHITESPACE_OK` | Hygiene gate |
| `git diff --check --` | Exit `0` | Line-ending warnings chỉ ở path ngoài scope; không có finding |
| `git diff --no-index --check -- NUL <allowlisted path>` cho 8 path | Exit `0`: `SCOPED_DIFF_CHECK_OK`; underlying exit `1` expected cho untracked | Scoped hygiene gate |
| `git status --short -- <8 allowlisted paths>` | Exit `0`: scoped `8`; total status `140` | Không có path ngoài allowlist bị sửa bởi repair |
| Final immutable manifest compare sau repair | Exit `0`: final ID `orchestration-c1f402f33c6042f4bab19c3984a239b9-repair1-final-20260829`, 140 entries, outside mismatch `0`, task delta `8`, `OUT_OF_SCOPE_UNCHANGED=PASS` | Sensitive entries chỉ marker-only; final manifest lưu ngoài workspace |

## Trạng thái đã biết

### Historical acceptance context (superseded current pointers)

- Báo cáo Phase 9
  `docs/PHASE9_DRY_RUN_GUARD_FULL_QUALITY_REPORT.md`, cập nhật 2026-08-29,
  là historical evidence và không còn là current report pointer. Pointer cũ tới
  `docs/BCNB_AUTO_FINISHED_IMPLEMENTATION_REPORT.md` cũng chỉ là lịch sử của
  snapshot trước.
- Phạm vi của các báo cáo historical là documentation/evidence-only. Output
  full-quality trong đó không phải current full-quality acceptance và không phải
  staging, pilot, live canary hoặc production acceptance.
- Báo cáo ARMB19
  `docs/ZALO_ARMB19_LIVE_READ_ONLY_CANARY_REPORT.md` là historical/superseded
  canary context; không phải current acceptance pointer.
- Live canary và production acceptance: **CHƯA ĐẠT / CHƯA ĐƯỢC PHÊ DUYỆT**.
- Bằng chứng historical được ghi trong báo cáo: prior `CHECK_QUALITY.ps1`
  exit `0`, `323 tests`, branch coverage `77.9%`. Đây là historical evidence
  của full gate trước documentation repair, không phải fresh/current
  acceptance evidence.
- Historical full gate đã invoke `tests/check_phase9_quality.py`; gate này gọi
  `init_db()`, và database initialization path reaches `conn.commit()` trong
  `db/database.py`. Available marker-safe manifest chỉ ghi status/hash của
  repository files, không có database before/after evidence; vì vậy database
  preservation không được chứng minh độc lập.

### Operational safety

- Kiểm tra có giới hạn ngày 2026-08-27 thấy đúng một cấu hình hiệu lực
  `BCNB_DRY_RUN=false`. Không lưu nội dung `.env` trong tài liệu này.
- Flag trên không cấp quyền chạy browser hoặc external mutation.
- `/run-scan` vẫn là batch rộng, không phải single-target canary. Trước live run
  cần ràng buộc chính xác class/lesson/week/year ở API, service và job payload;
  sau đó cần preview, owner approval, hard-reload readback, audit và rollback.

## Blocker và rủi ro mở

- Worktree đang rất dirty; task sau phải tránh ghi đè thay đổi không liên quan.
- Baseline per-path của các task cấu hình trước là limitation lịch sử. Task hiện
  tại đã có supplied baseline 141 entries với status/hash từng path; final
  preservation claim vẫn chỉ được đưa ra sau khi so sánh final manifest.
- Chưa có bằng chứng live production cho lifecycle BCNB Finished.
- `PROJECT_SUMMARY.md` và `PROJECT_COMPLETION_PLAN.md` chứa nhiều baseline lịch
  sử; phải xem ngày và bằng chứng trước khi dùng làm trạng thái hiện tại.
- Mọi claim về test count, coverage, database, active jobs hoặc config đều có
  thể stale và phải được kiểm tra mới khi task phụ thuộc vào chúng.

## Bước an toàn tiếp theo

- Chụp final manifest ngoài workspace, đối chiếu mọi path ngoài allowlist với
  baseline hiện tại, review scoped diff/checks và chuyển handoff cho Terra
  read-only. Không claim acceptance trước Terra.
- Nếu Terra `REJECTED`, chỉ dùng một automatic `followup_task` repair loop;
  lần reject thứ hai giữ blocker và dừng.
- Với yêu cầu live BCNB: dừng ở preview cho đến khi single-target contract và
  human gate được chứng minh.

## Current landing-compatibility diagnosis (2026-08-30)

- Fresh marker-safe baseline: `zalo-landing-compatibility-diagnosis-20260830-162244`,
  `143` status entries, HEAD `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`, and
  manifest SHA-256
  `6B09B538F5F32F2707885307988737A216C0831EE0B62DE00785C7F118022C3C`.
  Allowlist is exactly `docs/ZALO_LANDING_COMPATIBILITY_DIAGNOSIS_REPORT.md`
  and `PROJECT_STATE.md`; baseline markers are report `ABSENT` and current state
  SHA-256 `431842CB831828C1CF3DA4B530141285C7D59417E51073E294EDE4E67FE3DAD9`.
  This 143-entry snapshot belongs to the original diagnosis; the current
  documentation repair uses the supplied external 144-entry baseline.
- Exact typed outcome: **`BLOCKED_ZALO_LOGIN_OR_QR`**. One landing navigation
  completed in `637 ms` at the expected Zalo host/title; the selected in-app-browser
  context displayed login/QR, the authenticated shell is unproven, and
  `#contact-search-input` had count `0` (status `absent`).
- `SESSION_EXPIRY_PROVEN=false`; `AUTH_STATE_SUPPORTED=LOGIN_OR_QR_IN_SELECTED_CONTEXT`.
  Persistent-session expiry is unproven. The selected context may be isolated from
  the repository persistent Playwright profile, and the evidence cannot distinguish
  an expired persistent session from an unauthenticated selected context or a
  different browser/profile context. No claim is made about DNS/TLS failure,
  selector staleness, a source defect, or session expiry.
- Browser skill `browser:control-in-app-browser` was used for exactly one URL-based
  selection. Selected bindings/contexts `1/1`; one page created, maximum page
  count `1`; navigation attempts `1`, retries `0`; no screenshot. Cleanup attempts
  `1`, failures `0`, remaining pages `0`.
- Fresh preflight: SQLite URI `mode=ro` active `PENDING/RUNNING=0`; Zalo
  automation workers `0`; profile-lock marker/holder `0/0`; evidence path outside
  repository; BCNB/Form/database write gateway initialization `false`; planned
  group searches, conversations, messages, writes, login attempts and intentional
  profile modifications all `0`.
- Fresh hermetic gate: 14 named tests, exit `0`, `Ran 14 tests in 3.960s`, `OK`;
  targeted Ruff exit `0`; targeted mypy exit `0` over 8 source files.
  `CHECK_QUALITY.ps1` was not run. Browser packet is external, `small` (1216
  bytes), `hash_verified=true`; exact evidence hash remains outside Git. The
  14-test gate does not prove landing-auth classification; local/hermetic
  lifecycle evidence is not live-canary acceptance.
- Postflight repeated read-only checks with active jobs/workers/lock `0`; all
  group-search, conversation, message, Zalo, BCNB, Form, database, login and
  profile-modification counters remain `0`. ARMB19 canary was **NOT RETRIED**;
  staging, pilot and production are not run or accepted.
- Group-search, opened-conversation, message-read, Zalo write, BCNB write, Google
  Form submit, automated login, and profile-mutation intent counters all remained
  `0`; cleanup closed the only created page and left remaining pages at `0`.
- Immediate blocker: login/QR in the selected in-app-browser context; the
  repository persistent profile remains unverified.
- Current source comparison: `_setup_scan_page` uses finite 30-second navigation
  and selector waits, but only waits for `#contact-search-input`; it has no
  login/QR, title, modal, unsupported-browser, network/error, or selector-count
  classification. No selector repair is justified because the authenticated shell
  is unproven; no source-defect conclusion is made. Owner next action: manually
  verify authentication in the intended repository persistent profile and restore
  it manually only if unauthenticated. If authenticated, a separate task verifies
  automation uses the intended profile/context. No automatic canary retry; do not
  use `/run-scan` as a substitute.
- Browser-managed persistence was intentionally not inspected; no screenshot was
  captured, and no live canary success or staging/pilot/production acceptance is
  claimed.
- Report: `docs/ZALO_LANDING_COMPATIBILITY_DIAGNOSIS_REPORT.md`. Plans, browser
  facts, manifests, comparison and review packet remain outside the repository.
  The prior diagnosis snapshot is superseded; the final marker-safe manifest
  and comparison for this documentation repair are captured outside the
  repository in the supplied repair directory. Exact final hashes remain
  external and are not embedded in this state file.
- Workflow exception: Root interrupted the overlong executor after repeated
  bounded waits/reminder, then allowed packaging and cleanup-only continuation.
  Existing cleanup proof is `attempted=1`, `failures=0`, remaining pages `0`;
  no second browser/page/context, navigation, test, database query or diagnosis
  action was performed after interruption.

### Landing conclusion documentation repair (2026-08-30)

- Documentation-only correction: the current landing diagnosis now classifies
  the selected login/QR state as `BLOCKED_ZALO_LOGIN_OR_QR`; the unsupported
  persistent-session-expiry conclusion was removed from the current diagnosis.
  The two exact auth-state flags above remain aligned between this state snapshot
  and the report.
- Fresh repair validation is limited to exact outcome/flag/count checks,
  report/state consistency and accepted zero-count invariants, direct trailing-
  whitespace scans, scoped diff hygiene, sanitized report safety, and the final
  marker-safe manifest comparison. No application tests, browser action, full
  quality gate, `.env`, database, profile, source or external system was touched.
- Supplied repair baseline `landing-doc-repair-a8534394d9c449ec89fa9b112e53a433`
  has `144` entries. Final external comparison has outside missing `0`, outside
  new `0`, outside status/hash mismatches `0`, and
  `OUT_OF_SCOPE_UNCHANGED=PASS`; the supplied baseline does not contain branch
  or HEAD fields, so only exact status plus SHA-256/marker equality is claimed.
- Review state remains **AWAITING CODEX REVIEW**. The immediate blocker remains
  login/QR in the selected in-app-browser context; the repository persistent
  profile is unverified. No automatic canary retry is authorized.

## Evidence của lần cập nhật này

| Thời điểm | Lệnh/Artifact | Kết quả | Giới hạn |
|---|---|---|---|
| 2026-08-27 | `git branch --show-current` | `master` | Snapshot đầu task |
| 2026-08-27 | `git rev-parse HEAD` | `ec8b50e1a259b6264724f13a7f7d8c01095ed15c` | Snapshot đầu task |
| 2026-08-27 | `git status --short` | 118 mục trước khi thêm tài liệu | Không phân loại lại thay đổi của người dùng |
| 2026-08-27 | `docs/BCNB_AUTO_FINISHED_IMPLEMENTATION_REPORT.md` | Báo cáo mới nhất được xác định | Gate trong báo cáo là bằng chứng 2026-08-25 |
| 2026-08-27 | Kiểm tra riêng key `BCNB_DRY_RUN` | Đúng một giá trị hiệu lực, `false` | Không đọc hoặc lưu các key `.env` khác |
| 2026-08-27 | `git status --short -- AGENTS.md PROJECT_STATE.md PROJECT_DECISIONS.md TASK_TEMPLATE.md` | Đúng bốn file mới, đều untracked | Không sửa file có sẵn |
| 2026-08-27 | `git status --short` sau khi thêm tài liệu | 122 mục, tăng đúng 4 | Worktree cũ được giữ nguyên |
| 2026-08-27 | `git diff --no-index --check -- NUL <file>` cho từng file mới | Không có whitespace finding | Exit `1` là expected new-file diff, không phải lỗi kiểm tra |
| 2026-08-27 | `python -c "import pathlib,tomllib; paths=[pathlib.Path('.codex/config.toml'),pathlib.Path('.codex/agents/luna-implementer.toml'),pathlib.Path('.codex/agents/terra-reviewer.toml')]; [tomllib.loads(p.read_text(encoding='utf-8')) for p in paths]; print('TOML_OK')"` | Exit `0`, `TOML_OK` | Chỉ xác nhận cú pháp TOML |
| 2026-08-27 | `$env:PYTHONIOENCODING='utf-8'; python -c "import pathlib,sys; paths=[pathlib.Path('.codex/config.toml'),pathlib.Path('.codex/agents/luna-implementer.toml'),pathlib.Path('.codex/agents/terra-reviewer.toml'),pathlib.Path('AGENT_WORKFLOW.md'),pathlib.Path('AGENTS.md'),pathlib.Path('TASK_TEMPLATE.md'),pathlib.Path('PROJECT_DECISIONS.md'),pathlib.Path('PROJECT_STATE.md')]; bad=[f'{p}:{i}' for p in paths for i,line in enumerate(p.read_text(encoding='utf-8').splitlines(),1) if line.rstrip() != line]; print('WHITESPACE_OK' if not bad else '\\n'.join(bad)); sys.exit(0 if not bad else 1)"` | Exit `0`, `WHITESPACE_OK` | Chỉ kiểm tra trailing whitespace trên 8 file scope |
| 2026-08-27 | `git status --short -- .codex/config.toml .codex/agents/luna-implementer.toml .codex/agents/terra-reviewer.toml AGENT_WORKFLOW.md AGENTS.md TASK_TEMPLATE.md PROJECT_DECISIONS.md PROJECT_STATE.md` | Exit `0`, đúng 8 dòng scope | Không thay đổi các file dirty ngoài scope |
| 2026-08-27 | Handoff trước repair round 1 | 122-entry state chỉ có count, không có per-path status/SHA-256 baseline | Không thể hồi cứu bằng chứng preservation toàn worktree; chỉ còn scoped evidence |
| 2026-08-27 | Repair round 1: whitespace scan trên `AGENTS.md`, `AGENT_WORKFLOW.md`, `TASK_TEMPLATE.md`, `PROJECT_STATE.md` | Exit `0`, `WHITESPACE_OK` | Hygiene gate cho 4 file allowlist |
| 2026-08-27 | Repair round 1: `rg` required terms trên 4 file allowlist | Exit `0` | Xác nhận baseline/hash/allowlist/comparison/sequence/report contract |
| 2026-08-27 | Repair round 1: `git status --short -- AGENTS.md AGENT_WORKFLOW.md TASK_TEMPLATE.md PROJECT_STATE.md` | Exit `0`, đúng 4 dòng allowlist | Các file đang untracked; không suy luận preservation từ status |
| 2026-08-27 | Repair round 1: `git diff --name-only -- AGENTS.md AGENT_WORKFLOW.md TASK_TEMPLATE.md PROJECT_STATE.md` và `git diff --check --` cùng scope | Exit `0`, không có tracked diff/finding | Untracked content được kiểm tra bằng whitespace scan và status scope |
| 2026-08-27 | `$env:PYTHONIOENCODING='utf-8'; python -c "import pathlib,sys; paths=[pathlib.Path('TASK_TEMPLATE.md'),pathlib.Path('PROJECT_STATE.md')]; bad=[f'{p}:{i}' for p in paths for i,line in enumerate(p.read_text(encoding='utf-8').splitlines(),1) if line.rstrip()!=line]; print('WHITESPACE_OK' if not bad else '\\n'.join(bad)); sys.exit(0 if not bad else 1)"` | Exit `0`, `WHITESPACE_OK` | Chỉ kiểm tra hai file allowlist của vòng cuối |
| 2026-08-27 | `$env:PYTHONIOENCODING='utf-8'; python -c "import pathlib; p=pathlib.Path('TASK_TEMPLATE.md').read_text(encoding='utf-8'); old='Git diff chỉ chứa thay đổi thuộc phạm vi.'; terms=['final immutable manifest','task allowlist','before-hash','intended delta']; assert old not in p, 'OBSOLETE_PHRASE_PRESENT'; assert all(t in p for t in terms), 'BASELINE_TERMS_MISSING'; print('STATIC_CONTRACT_OK')"` | Exit `0`, `STATIC_CONTRACT_OK` | Xác nhận template dùng final manifest/allowlist/before-hash/intended-delta contract |
| 2026-08-27 | `git status --short -- TASK_TEMPLATE.md PROJECT_STATE.md` | Exit `0`, đúng 2 dòng allowlist (`?? PROJECT_STATE.md`, `?? TASK_TEMPLATE.md`) | Các file đang untracked; không suy luận preservation ngoài baseline evidence |

| 2026-08-27 | Post-review root validation lần đầu gọi `.\.venv\Scripts\python.exe` | FAIL trước khi chạy TOML check vì executable không tồn tại (`Test-Path=False`) | Không gây file mutation; repo thực tế có `.\venv\Scripts\python.exe` nhưng lệnh retry dùng Python hệ thống |
| 2026-08-27 | Retry bằng `C:\Users\Quang\AppData\Local\Programs\Python\Python313\python.exe`: `FINAL_TOML_OK`; static contract; obsolete rule; whitespace; scoped status; total status | Exit `0` cho các gate; scoped `8` untracked paths; total `124` | Chấp nhận config/docs local-only; không chứng minh runtime mutex hoặc production acceptance |
| 2026-08-27 | Terra final review repair round 2 của task trước | Historical workflow/documentation evidence; không áp dụng selector repair | Review read-only, không phải acceptance hiện tại |
| 2026-08-27 | Residual agent enforcement | `max_concurrent_threads_per_session=2`; Luna có static `workspace-write`; Terra là `read-only` | Luna→Terra tuần tự và Luna read-only cho task check được enforcement bằng workflow/delegated prompt, không phải runtime mutex |

## Historical selector readiness retry repair (2026-08-27; Terra repair round 1; superseded by current per-target remediation)

- Source hiện phân loại setup theo mã typed; timeout của `#contact-search-input`
  dùng `SELECTOR_READINESS_TIMEOUT`. Mỗi scan worker tối đa hai setup attempt;
  page lỗi được đóng một lần trước khi tạo fresh page, và cả tạo/đóng page đều
  có timeout hữu hạn. Close lỗi/timeout không tạo replacement page và giữ
  batch-halt fail-closed. Không thêm selector fallback.
- Setup failure claimant được bảo vệ bằng lock theo batch: đúng một worker
  thất bại được claim tối đa một target review với `INSUFFICIENT_EVIDENCE`;
  worker thất bại sau đó không claim target, còn pending target được xử lý bởi
  một lần `halt_unclaimed`.
- Telemetry setup chỉ gồm `job_id`, `worker_id`, `elapsed_ms`, enum page-state,
  bounded selector count/readiness và typed error code; không có DOM/body,
  message, URL, cookie, token hoặc dữ liệu live.
- Acceptance mới chỉ **local/hermetic**; không chạy Zalo/BCNB live, không sửa
  `.env`, database hoặc browser profile.
- Terra final re-review của repair round 1: **PASS**, không có actionable
  findings; targeted rerun vẫn là local/hermetic evidence.
- Report standalone: `docs/ZALO_SELECTOR_READINESS_TIMEOUT_REMEDIATION_REPORT.md`;
  documentation-only evidence cho Terra PASS/local source-hermetic scope, không
  mở rộng thành live acceptance.

| Thời điểm | Lệnh/Artifact | Kết quả | Giới hạn |
|---|---|---|---|
| 2026-08-27 11:10 | `.\\venv\\Scripts\\python.exe -m unittest tests.test_zalo_parallel_service` | Exit `0`, 11 tests, 0.304s | Hermetic in-memory browser/repository doubles; không phải live acceptance |
| 2026-08-27 | Terra targeted rerun: `.\\venv\\Scripts\\python.exe -m unittest tests.test_zalo_parallel_service` | Exit `0`, 11 tests, 0.280s; final repair round 1 **PASS**, không actionable findings | Terra review local/read-only; không phải live acceptance |
| 2026-08-27 | `docs/ZALO_SELECTOR_READINESS_TIMEOUT_REMEDIATION_REPORT.md` | Standalone Vietnamese remediation report được tạo; ghi Terra PASS đúng local source/hermetic scope | Không chứa secrets, raw DOM/body, live data hoặc sensitive path |
| 2026-08-27 | `$stateCheckPaths = @('docs/ZALO_SELECTOR_READINESS_TIMEOUT_REMEDIATION_REPORT.md', 'PROJECT_STATE.md'); $stateWhitespaceFindings = foreach ($stateCheckPath in $stateCheckPaths) { $stateLineNumber = 0; foreach ($stateLine in (Get-Content -LiteralPath $stateCheckPath)) { $stateLineNumber++; if ($stateLine -cne $stateLine.TrimEnd()) { "${stateCheckPath}:${stateLineNumber}" } } }; if ($stateWhitespaceFindings) { $stateWhitespaceFindings; exit 1 }; Write-Output 'WHITESPACE_OK'` | Exit `0`, `WHITESPACE_OK` | Exactly hai file allowlist của documentation task |
| 2026-08-27 | `$reportText = Get-Content -Raw -LiteralPath 'docs/ZALO_SELECTOR_READINESS_TIMEOUT_REMEDIATION_REPORT.md'; $reportNormalized = $reportText.ToLowerInvariant(); $reportRequired = @('Terra PASS', 'SELECTOR_READINESS_TIMEOUT', 'asyncio.wait_for', '-m unittest tests.test_zalo_parallel_service', 'CHECK_QUALITY.ps1', 'local source/hermetic', 'Không chạy Zalo/BCNB live', 'Không inspect', '`.env`', 'OUT_OF_SCOPE_UNCHANGED=PASS'); $reportMissing = @($reportRequired | Where-Object { -not $reportNormalized.Contains($_.ToLowerInvariant()) }); if ($reportMissing.Count -gt 0) { $reportMissing; exit 1 }; Write-Output 'REPORT_STATIC_CONTENT_OK'` | Exit `0`, `REPORT_STATIC_CONTENT_OK` | Static content check; không inspect `.env` hoặc runtime/live data |
| 2026-08-27 10:34 | `.\\venv\\Scripts\\ruff.exe check services/zalo_parallel_service.py tests/test_zalo_parallel_service.py` | Exit `0` | Targeted static gate |
| 2026-08-27 10:34 | `.\\venv\\Scripts\\mypy.exe services/zalo_parallel_service.py` | Exit `0` | Targeted type gate |
| 2026-08-27 | `.\\CHECK_QUALITY.ps1` | Exit `1` after Ruff PASS, mypy PASS (69 files), `307` tests PASS in `273.136s`, total coverage `77.5%`, Phase 9A and Phase 3–8 PASS; Phase 9 local readiness rejects current configuration because it requires `GOOGLE_FORM_MODE=dry-run` and `BCNB_DRY_RUN=true` | Không inspect/change `.env`; Phase 9B and later steps did not run; local-only evidence |

### Blocker và bước an toàn tiếp theo

- Full quality gate cần owner xử lý cấu hình local dry-run trong một task riêng;
  không suy luận quyền live từ việc đổi cờ cấu hình.
- Terra final review đã PASS trong phạm vi allowlist/local source và không có
  actionable findings; không mở rộng verdict này thành live acceptance.

## Current per-target search and evidence-capture remediation (2026-08-28; prior Terra audit PASS; current correction AWAITING CODEX REVIEW)

- Mục tiêu là giữ fail-closed khi clear/fill/result search hoặc evidence capture
  timeout/failure. Source hiện có typed `SELECTOR_READINESS_TIMEOUT`, các mã
  `TARGET_SEARCH_*` riêng, boundary hữu hạn, tối đa một fresh-page retry cho
  đúng target, close-failure no-replacement và retire worker. Trong nhánh
  target-search retry failure, pending target chỉ được halt khi không còn worker
  khỏe; cụm “halt khi không còn worker khỏe” chỉ áp dụng cho nhánh này. Setup
  failure vẫn batch-global fail-closed, với claimant setup-failure có lock và
  one-shot claimant behavior; sticky setup error không lan sang target kế tiếp.
- Capture giữ classifier `report_found`/decision gốc nhưng chuyển final state sang
  `INSUFFICIENT_EVIDENCE` nếu không tạo được evidence. Structured diagnostics
  tách khỏi logger; target logger exact allowlist là `event`, `job_id`,
  `worker_id`, `phase`, `attempt`, `elapsed_ms`, `page_state`, `error_code`.
  Secret-marker absence được kiểm tra bằng fixture hermetic. Timeout hữu hạn của
  capture chỉ được chứng minh cho các operation bên trong
  `_capture_zalo_evidence` (scroll, screenshot, UI-date); broader history-scroll
  flow chưa được chứng minh có per-operation timeout ở mọi nơi. File validation
  là typed/fail-closed nhưng không phải timeout claim.
- Historical RED provenance (2026-08-28): initial focused `Ran 12`, exit `1`
  với behavior thiếu; external artifact
  `C:\Users\Quang\AppData\Local\Temp\codex-zalo-bounded-20260828-105115\task-1-red-report.md`
  có SHA-256 `BD69B61D076E8522D752B287D017AD3E8126FF8BC113D4D7B62B3519D0560692`
  và ghi `Ran 12 tests`, `FAILED (failures=15)`. Đây là historical RED evidence,
  không phải current failure. Repair Round 2 `Ran 2`, exit `1`, đúng một key-set
  failure còn hanging clear test đã pass.
- Historical 2026-08-28 UTF-8 rerun dùng đúng command:
  `$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONIOENCODING='utf-8'; .\venv\Scripts\python.exe -m unittest tests.test_zalo_parallel_service tests.test_zalo_scan_evidence tests.test_zalo_evidence` — exit `0`; `Ran 56 tests in 36.459s`; final output `OK`. Bằng chứng này chỉ áp dụng cho command UTF-8 nói trên và did not include the two genuine hanging fill/result cases. Ruff sáu file exit `0`; mypy ba service exit `0`; `git diff --check` exit `0`.
- Một bare command thiếu hai biến môi trường UTF-8 dưới console CP1252 có thể
  fail với `UnicodeEncodeError`; không có bằng chứng Sol đã reproduce CP1252
  failure. Current `tests/test_zalo_parallel_service.py` now contains the real
  bounded lifecycle tests `test_hanging_search_fill_retries_once_then_retires_worker`
  and `test_hanging_search_result_retries_once_then_retires_worker`; these correct
  the stale limitation about the historical 56-test evidence.
- Current decisive UTF-8-prefixed 10-test validation uses the exact named logger,
  retry/healthy-worker, hanging clear/fill/result, screenshot and capture-timeout
  tests from the Terra command; exit `0`, `Ran 10 tests`, final output `OK`.
  This is local/hermetic targeted evidence only, with no staging, pilot,
  production, browser, Zalo or BCNB acceptance.
- Historical full-quality gate evidence sau code repair cuối là
  historical/current boundary, không phải fresh run của documentation
  correction: `CHECK_QUALITY.ps1` exit `1`; Ruff PASS, mypy 69 files PASS,
  `Ran 321 tests`, `OK`, Phase 9A và Phases 3–8 PASS; gate dừng ở existing
  Phase 9 local dry-run configuration guard. Full gate **không fully passing**;
  không inspect hoặc thay đổi config/`.env`.
- Immutable evidence của remediation trước: `HEAD`
  `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`, baseline `139` entries, final
  `140` entries (`+1` là report mới nằm trong allowlist),
  `OUT_OF_SCOPE_UNCHANGED=PASS`, `0` outside-allowlist mismatch; sensitive paths
  chỉ marker-only, không đọc/hash. Đây không phải final manifest của correction
  hiện tại.
- Documentation correction manifest của vòng trước: baseline ID
  `zalo-doc-correction-20260828-175709`, `140` entries, SHA-256
  `A59CFCAD07C97A2F6E4151F03ACE888D4737D3138ADDEE21ED196587B249633B`; final
  manifest ID `zalo-doc-correction-final-20260828-181358`, `140` entries,
  `HEAD` unchanged `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`, outside-allowlist
  mismatch `0`, verdict `OUT_OF_SCOPE_UNCHANGED=PASS`. Final manifest lưu ngoài
  workspace; không embed manifest SHA trong repo vì Sol sẽ recapture sau
  bookkeeping edit này và ghi SHA kết quả trong Final Execution Evidence.
- Allowlist của correction hiện tại chỉ là report
  `docs/ZALO_PER_TARGET_SEARCH_AND_CAPTURE_REMEDIATION_REPORT.md` và section
  này. Standalone report đã ghi exact commands, exits, review history và
  acceptance boundary. Terra đã xác nhận hai finding Round 2 **ADDRESSED** và
  code quality **PASS**; Terra correction audit: **PASS**
  (`TERRA VERDICT: PASS`), không có Critical/Important findings; Spec/Evidence
  accuracy/Scope đều **PASS** cho bản remediation trước; đây không phải verdict
  cho correction hiện tại.
- Ranh giới vẫn local/source/hermetic: không browser/Zalo/BCNB live, không
  database/profile mutation, không commit/push/deploy. Documentation correction
  hiện tại dừng ở **AWAITING CODEX REVIEW**; Terra review là bước tiếp theo.
  Blocker historical/current còn lại là Phase 9 dry-run guard và bước an toàn
  tiếp theo là owner xử lý blocker đó trong task riêng.

## Historical Phase 9 dry-run guard task (2026-08-29; superseded current pointer)

- Status: **AWAITING CODEX REVIEW**. This bookkeeping task is
  documentation/evidence-only. The historical output below is local/full-quality
  evidence only; it is not current full-quality acceptance and is not live,
  staging, pilot or production acceptance.
- Root cause was the Phase 9 local-readiness guard in
  `tests/check_phase9_quality.py`: it rejects unless `Config.MODE` is
  `dry-run` and `Config.BCNB_DRY_RUN` is true. `googleform_autosubmit/config.py`
  reads process environment before its dotenv defaults, so the task used only
  the approved process overrides `GOOGLE_FORM_MODE=dry-run` and
  `BCNB_DRY_RUN=true`; persistent configuration was not edited or inspected.
- Historical full command was invoked exactly once on 2026-08-29 before this
  documentation repair with those two process overrides plus UTF-8/no-bytecode
  process settings; it was not rerun here. Historical result: exit `0`,
  historical test count `323 tests in 261.973s`, `OK`, historical total branch
  coverage `77.9%`. These numbers are not fresh/current acceptance evidence.
- Historical output reached and passed, in order: Ruff; mypy (69 source files);
  regression and branch coverage; coverage threshold and JSON; Phase 9A; Phases
  3, 4, 5, 6, 7 and 8; Phase 9 local readiness; Phase 9B staging contract;
  dependency compatibility; schema/active-jobs; hermetic app smoke; frontend
  contract; compile-all; and `QUALITY GATE PASSED`. Phase 9 reported
  `dry_run=on`, fail-closed production preflight, migrations `[1,2,3,4]`, and
  database integrity `ok`; active jobs were `0`. This is historical output,
  not current full-quality acceptance.
- The historical full `CHECK_QUALITY.ps1` execution invoked
  `tests/check_phase9_quality.py`, which called `init_db()` and reached
  `conn.commit()` in `db/database.py`. The available marker-safe manifest did
  not capture database before/after evidence, so database preservation is not
  independently proven.
- Historical gate evidence is documented in
  `docs/PHASE9_DRY_RUN_GUARD_FULL_QUALITY_REPORT.md`. No browser/Zalo/BCNB live
  action or external write was authorized or run by this documentation repair;
  this repair also did not run a database migration/seed or mutate persistent
  configuration.
- Marker-safe preservation baseline is outside the workspace: ID
  `phase9-doc-evidence-repair-20260829-094019`, 141 entries, supplied outside
  the workspace. It records repository file status/hash only, not database
  before/after state. Task allowlist is exactly this state file and the report;
  Sol must recapture the final manifest after this bookkeeping edit before Terra
  review.

## Quy tắc cập nhật file này

Mỗi lần cập nhật phải giữ file ngắn và ghi:

1. `updated_at`, task, branch/HEAD và tình trạng worktree.
2. Trạng thái acceptance theo từng môi trường.
3. Exact command, exit code và thời điểm của bằng chứng mới.
4. Blocker/rủi ro còn mở và bước an toàn tiếp theo.
5. Số liệu lịch sử phải được dán nhãn; xóa claim đã stale thay vì nối vô hạn.

## Historical ARMB19 live read-only canary (2026-08-29; superseded current pointer)

- Status: **BLOCKED**, review state **AWAITING CODEX REVIEW**. This was one
  Owner-authorized read-only canary for exactly `VT-ROB-ARMB19`, lesson `1`,
  class date `2026-08-23`, week/year `34/2026`, and permitted message window
  `2026-08-17..2026-08-29`. It was not batch, staging, pilot, or production
  acceptance.
- Marker-safe baseline before edit/live action: ID
  `zalo-armb19-live-canary-20260829-103731-01fd583a`, `141` entries, manifest
  SHA-256 `627F892C3B87725F12CBD574B7BFDFE0551BA5421F1357BF87577A8060377278`,
  HEAD `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`. Allowlist was exactly
  `docs/ZALO_ARMB19_LIVE_READ_ONLY_CANARY_REPORT.md` and this file; before
  markers were report `missing` and this file SHA-256
  `C77609ECCEC3AFE7B71C5A7E8EF578092DE537930BB6E5844C04B52AB4D59EDA`.
- Read-only preflight used SQLite URI `mode=ro`: active `PENDING/RUNNING`
  jobs `0`, profile lock `false`, profile-holder process count `0`, exact
  preview `target_count=1`, second target `false`, and direct-plan write flags
  `false`. The plan SHA-256 was
  `BF5848A2E18CD319B8FE99274FC9E86D459F3E6110D87FF66B759B1455E09EF4`.
- Focused hermetic readiness ran exactly once: `Ran 10 tests in 1.309s`,
  `OK`, exit `0` (outer elapsed `2.511s`) with the required telemetry/retry,
  hanging search, capture-timeout, missing-screenshot and final
  `INSUFFICIENT_EVIDENCE` coverage. Scoped Ruff exit `0` and scoped mypy exit
  `0` over the relevant six/three files. `CHECK_QUALITY.ps1` was not rerun;
  historical full-gate numbers remain historical only.
- Browser selection used documented URL-based selection. The initial in-app
  page had no app shell/search input and a login marker. One Chrome page then
  had one navigation attempt plus one bounded reload recovery, both ending at
  typed `BROWSER_NAVIGATION_FAILED`/`chrome-error` state with no app shell.
  Search, exact-group open, message classification, and evidence capture were
  not reached. No credentials, login, fallback, or raw browser data were used.
- Postflight used SQLite URI `mode=ro` again: active jobs `0`, profile lock
  `false`, holder count `0`. Cleanup closed the page and left current-session
  page count `0`. `target_count=1`, second target claim/read `0`, external
  writes `0`, BCNB writes `0`, and Zalo message sends `0`.
- The sanitized report is
  `docs/ZALO_ARMB19_LIVE_READ_ONLY_CANARY_REPORT.md`. External runtime packet,
  plan, baseline, and final manifests remain outside the workspace. Final
  manifest comparison and documentation hashes are supplied in the Luna
  handoff; no live-derived message, DOM/body, URL-sensitive data, credential,
  cookie, or token is stored here.
- Acceptance boundary: local hermetic `PASS`; live canary `BLOCKED`; staging,
  pilot, and production `NOT RUN`. Safe next action is Terra read-only review;
  do not retry the canary or broaden it to a batch endpoint without new Owner
  authorization and a fresh preflight.

## Current cleanup and landing/auth readiness remediation (2026-08-31)

- Status: **AWAITING CODEX REVIEW**. The historical implementation task changed
  exactly six task-owned paths: the two gateway/scan services, their two focused
  test modules, this state file, and
  `docs/ZALO_CLEANUP_AND_LANDING_READINESS_REMEDIATION_REPORT.md`. The current
  repair is a separate documentation-only change to the last two paths.
- Root cause: persistent browser cleanup had no typed/idempotent result or
  final invariant boundary; the accepted packet recorded bounded context and
  Playwright-stop failures despite final holders `0`. Landing setup only
  waited for `#contact-search-input` and had no safe login/QR selector
  contract.
- `services/external_gateways.py` now owns one-shot page/context/Playwright
  cleanup with finite timeouts, typed already-state agreement, fail-closed
  unknown errors, safe telemetry and no post-cleanup page recreation. Results
  are `CLEANUP_OK`, `CLEANUP_ALREADY_CLOSED`, `CONTEXT_CLOSE_TIMEOUT`,
  `CONTEXT_CLOSE_ERROR`, `PLAYWRIGHT_STOP_TIMEOUT`, `PLAYWRIGHT_STOP_ERROR`,
  or `CLEANUP_INVARIANT_FAILED`.
- `services/zalo_parallel_service.py` now has a browser-free injected-facts
  landing classifier. Positive shell+search evidence is
  `AUTHENTICATED_READY`; login/QR is `LOGIN_OR_QR`; simultaneous signals are
  `AUTH_STATE_AMBIGUOUS`; absence/detach is `LANDING_UNKNOWN_FAIL_CLOSED`.
  Timeout, closed-page, modal, unsupported-browser and network results are
  typed. Normal `_setup_scan_page()` now fails before legacy selector waiting
  when no injected facts provider exists, with exact
  `LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN`; synthetic sessions inject only
  sanitized facts for downstream tests.
- Terra repair-loop findings were addressed within the historical six-path
  implementation scope: `cleanup_persistent_resources()` now reuses one
  weakly keyed raw-context owner/result, and repeated public calls do not close
  or stop the same resource twice.
- Historical TDD RED before production edit: focused modules, exit `1`,
  `Ran 7 tests`, `FAILED (errors=11)`. Repair-loop named RED before production
  edit: exit `1`, `Ran 2 tests`, `FAILED (2 failures)`; after repair exit `0`,
  `Ran 2 tests`, `OK`. These RED claims are execution-session evidence only.

### Historical six-path manifest correction

- Immutable baseline: ID `cleanup-readiness-90002910347b4266919e58079022e78a`,
  `144` entries, byte SHA-256
  `2360c2b382fd192f99aa1c32fa3688cc4aeb4cda983e6d834bcf800444dac16e`.
- Original implementation final: ID
  `cleanup-readiness-root-repair-final-20260830-230908`, `145` entries, byte
  SHA-256
  `24398aa57ceb109e4d40da9cf337a7d2bdf7dd86c434020401f8207ad5c612c2`.
- Corrected comparison is external at
  `C:\Users\Quang\AppData\Local\Temp\codex-zalo-manifest-doc-repair-20260831-072937-294812d0\historical-six-path-corrected-comparison-20260831-003423.json`,
  byte SHA-256
  `28ab029a0bf82883e8b29df62fad7523d60f49a30d3198bfddb400f7023ce456`.
  `task_allowlist_count=6` and `task_owned_delta_count=6`; all six paths are
  classified `TASK_OWNED_DELTA` with exact before/after status and SHA-256 or
  safe marker in that artifact:
  `services/external_gateways.py`, `services/zalo_parallel_service.py`,
  `tests/test_external_gateways.py`, `tests/test_zalo_parallel_service.py`,
  `docs/ZALO_CLEANUP_AND_LANDING_READINESS_REMEDIATION_REPORT.md`, and
  `PROJECT_STATE.md`.
- Exact historical outside comparison: `outside_missing_count=0`,
  `outside_new_count=0`, `outside_status_mismatch_count=0`,
  `outside_hash_or_marker_mismatch_count=0`,
  `OUT_OF_SCOPE_UNCHANGED=PASS`. The previous comparison that listed only the
  new report is superseded as incomplete; no contents are embedded here.

### Current two-document documentation repair

- Fresh immutable baseline: ID `manifest-doc-repair-91f28041806e48f6bf9e3715326e2324`,
  `145` entries, byte SHA-256
  `a82406a54044c1d02ba846e86beb2ab67cfb0f42507a5c3c48df30f969605f5c`.
  Before hashes are report
  `7ec8eff1b0039378d5cac30dc0b45be16799c2340694e3504fa4641d5efc3d6b` and
  state `167cd18eed4aef84fcdee656c6a72d3d6bbb12604a4ed9d3b374b1a4288f742e`.
- Current allowlist is exactly these two documents. The post-document final
  manifest and comparison are captured after this edit in the same external
  evidence root as `post-doc-final-manifest-20260831-003423.json` and
  `post-doc-final-comparison-20260831-003423.json`. They must record actual
  final entries, `task_allowlist_count=2`, `task_owned_delta_count=2` only if
  both documents changed, all four outside mismatch counts `0`, and
  `OUT_OF_SCOPE_UNCHANGED=PASS`; their byte hashes are authoritative there.
- No self-referential current document hash is stored in this state file. No
  database, `.env`, browser/profile, Zalo/BCNB, ARMB19 or external action was
  performed.

- Fresh local gates on 2026-08-30: cleanup `Ran 8`, landing/readiness `Ran 3`,
  `tests.test_external_gateways` `Ran 16`, and
  `tests.test_zalo_parallel_service` `Ran 26`; all exit `0`, `OK`.
  Targeted Ruff on four historical task paths exit `0`; targeted mypy on two
  services exit `0`; `tests/check_phase4_quality.py` exit `0`
  (`phase4-quality-gate-ok`).
- `CHECK_QUALITY.ps1` was not run: process-scoped dry-run and isolated temp
  database preservation were not proven. Full-gate status is
  `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`; historical gate counts are
  not current acceptance. No fresh full-quality, database-preservation,
  staging, pilot, live-canary or production claim is made.
- Explicit limitation: `IMMUTABLE_RED_STDOUT_ARTIFACT_NOT_AVAILABLE`.
  Immutable manifests prove file/status markers, not independent RED timing or
  stdout capture; no RED evidence is fabricated or backdated.
- Local/hermetic acceptance remains bounded to the recorded tests and static
  checks. Terra review remains pending; the next safe action is read-only Terra
  review, with live authentication/profile and all staging, pilot and
  production blockers retained.

## Current runtime auth-readiness provider (2026-08-31)

- Status: **AWAITING CODEX REVIEW**. This bounded implementation uses only the
  six-path allowlist supplied for `auth-readiness-4974b77cfe9a4330804be45eccd16757`.
  No browser, persistent profile, Zalo/BCNB, database, `.env`, `/run-scan`,
  external action, commit, push, deploy, seed, or migration was performed.
- `PersistentBrowserSession` now supplies a bounded
  `RuntimeLandingFactsProvider` with the exact sanitized runtime fact schema:
  `phase`, `elapsed_ms`, `page_state`, `expected_host_reached`,
  `authenticated_shell_visible`, `search_control_visible`,
  `login_or_qr_visible`, `blocking_modal_visible`,
  `unsupported_browser_visible`, `network_error_visible`,
  `selector_contract_version`, and `diagnostic_code`.
- The provider has finite per-observation and combined bounds, preserves
  cancellation, sanitizes unknown errors, and never emits raw exception,
  URL/query, DOM/message, credential, token, QR, or profile data. Only the
  current search-control contract is proven. Missing auth evidence maps to
  `LIVE_AUTH_SELECTOR_CONTRACT_NOT_PROVEN`; missing login/QR evidence maps to
  `LIVE_LOGIN_SELECTOR_CONTRACT_NOT_PROVEN`; no selector was guessed.
- Batch setup caches one provider observation behind a lock. Only
  `AUTHENTICATED_READY` (positive authenticated shell plus search control)
  reaches selector readiness and target search. All other landing outcomes stop
  before target claim/message/evidence work and BCNB write. Landing setup
  failures halt pending rows without claiming a target. Existing selector
  retry behavior remains unchanged after auth-ready facts.
- Immutable TDD RED artifact (captured before production edits) is outside the
  workspace at
  `C:\Users\Quang\AppData\Local\Temp\auth-readiness-4974b77cfe9a4330804be45eccd16757\red-provider-20260831-2150\red-evidence.json`;
  artifact SHA-256 is
  `4B12FCE36F9FEA6E407D3698E76B3F8E8145CEF066AFD29A9E0720EE9423C6A4` and
  combined stdout/stderr SHA-256 is
  `55EFAFB9D917C96358C373A85AD1D880E73FB6F9672643917F64F3373B309AB7`.
  The focused pre-edit command exited `1`, ran `61` tests, and had `17`
  assertion failures for the missing provider/cache/no-claim behavior; it was
  not a syntax/import failure.
- Fresh local GREEN evidence on 2026-08-31: `tests.test_external_gateways`
  exit `0`, `Ran 31 tests`, `OK`; `tests.test_zalo_parallel_service` exit `0`,
  `Ran 31 tests`, `OK`; combined focused suite exit `0`, `Ran 62 tests`, `OK`;
  `tests/check_phase4_quality.py` exit `0`, `phase4-quality-gate-ok`; targeted
  Ruff on four source/test paths exit `0`; targeted mypy on two service paths
  exit `0`, `Success: no issues found in 2 source files`.
- `CHECK_QUALITY.ps1` was not run because isolated temporary database/config
  preservation was not proven. Full-gate status remains
  `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`; no full-quality, staging,
  pilot, live-canary, or production acceptance claim is made.
- Cleanup P2 inspection did not change `_UNWEAKREFABLE_CONTEXT_CLEANUP`.
  Its process-global id-keyed dictionary retains strong context references and
  has no bounded eviction/finalization path. Separate follow-up is required:
  `FOLLOWUP_REQUIRED_UNWEAKREFABLE_CACHE_BOUND`.
- Report: `docs/ZALO_RUNTIME_AUTH_READINESS_PROVIDER_REPORT.md`. Final
  marker-safe preservation evidence is captured outside the workspace at
  `C:\Users\Quang\AppData\Local\Temp\auth-readiness-4974b77cfe9a4330804be45eccd16757\final-20260831-2217\final-manifest.json`
  and the paired
  `C:\Users\Quang\AppData\Local\Temp\auth-readiness-4974b77cfe9a4330804be45eccd16757\final-20260831-2217\final-comparison.json`;
  sidecar files record their SHA-256 values. The comparison has
  `baseline_entry_count=145`, `final_entry_count=146`,
  `task_owned_delta_count=6`, all outside mismatch counts `0`, and
  `OUT_OF_SCOPE_UNCHANGED=PASS`. Safe next action is read-only Terra review of
  this exact diff; live authentication/profile and all staging, pilot, canary,
  and production gates remain closed.

## Current internal landing selector-proposal probe (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. The implementation is limited to the
  supplied four-path allowlist: `services/external_gateways.py`,
  `tests/test_external_gateways.py`,
  `docs/ZALO_INTERNAL_LANDING_PROBE_REPORT.md`, and this state file. No
  `PROJECT_DECISIONS.md` change was made.
- `PersistentBrowserSession.probe_landing_selector_proposals` is a
  programmatic-only read-only boundary. It uses the same gateway
  `persistent_session()` launch/binding path as the worker, creates and owns
  its page through that context, performs one fixed landing navigation,
  observes only the expected host and the existing proven search-control
  count/visibility, and never calls `landing_facts_provider`, scan setup,
  search, click, type, chat, BCNB, or evidence operations. Caller-supplied
  pages are rejected.
- The exact result is sanitized to `phase`, `elapsed_ms`, `profile_binding`,
  `expected_host_reached`, `search_control_visible`,
  `auth_shell_candidate_status`, `login_or_qr_candidate_status`,
  `selector_contract_version`, `diagnostic_code`, and
  `zero_action_counters`. The latter records one allowed navigation at most and
  zero search/click/type/chat/BCNB/evidence actions. No selector, URL, text,
  DOM, profile identifier, cookie, token, QR data, exception text, or browser
  state is returned or emitted. Gateway telemetry emits the sanitized result
  exactly once.
- Candidate statuses are fail-closed by default: both candidate fields remain
  `UNPROVEN` and the diagnostic is `LANDING_PROBE_NO_PROVEN_CANDIDATE` until an
  Owner-approved concrete structural selector contract exists. The probe
  accepts no arbitrary candidate callback, selector, DOM/text observation, or
  asserted provenance. The enum vocabulary retains
  `CANDIDATE_PRESENT_WITH_LIVE_PROVENANCE`, `NO_CANDIDATE`, `UNPROVEN`, and
  `AMBIGUOUS` for a separately approved future contract. The probe never emits
  `AUTHENTICATED_READY` and does not alter the runtime provider or scan policy.
- The initial immutable RED artifact from the interrupted implementation is
  preserved at
  `C:\Users\Quang\AppData\Local\Temp\internal-landing-probe-01dcd4603ac04b6a9df7bcd2326eb365-red-20260904-02\red-evidence.json`;
  its recorded artifact SHA-256 is
  `9B8E20B487266715BB5F2F71B9EAAAFBC352EFE1A6FBEFD8E38EA5830CFCE509`.
  A fresh repair-loop RED captured before the P1 repair is at
  `C:\Users\Quang\AppData\Local\Temp\internal-landing-probe-01dcd4603ac04b6a9df7bcd2326eb365-red-repair-20260904-0910\red-evidence.json`;
  artifact SHA-256
  `09561E5E6E66B5A04AF3E04590FE0B462A17AE7400C9026E7B75DD2D750516E6`,
  combined stdout/stderr SHA-256
  `309778A1BA2944C011B974BE34B0C8638CC7920AA7C667BCF456209F56A73368`,
  command exit `1`, `Ran 17 tests`, `FAILED (failures=3)`. Failures were
  behavior assertions for foreign-page rejection, unapproved candidate
  callbacks, and duplicate gateway telemetry; they were not import/syntax
  failures.
- Fresh hermetic GREEN evidence on 2026-09-04: focused probe exit `0`,
  `Ran 16 tests`, `OK`; `tests.test_external_gateways` exit `0`, `Ran 47
  tests`, `OK`; targeted Ruff exit `0`, `All checks passed!`; targeted mypy on
  `services/external_gateways.py` exit `0`, no issues; and
  `tests/check_phase4_quality.py` exit `0`, `phase4-quality-gate-ok`.
- `CHECK_QUALITY.ps1` was not run under the resumed-task boundary. Full-gate
  status is `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`; no full-quality,
  staging, pilot, live-canary, or production acceptance claim is made.
- Live landing-only invocation: **NOT RUN** after Owner instruction to stop
  before browser/live work. Blocker is `LIVE_PROBE_NOT_RUN_OWNER_BOUNDARY`;
  there was no browser navigation, profile binding, or retry in this task.
- The report is `docs/ZALO_INTERNAL_LANDING_PROBE_REPORT.md`. Final immutable
  manifest comparison remains Root's next step against supplied baseline
  `internal-landing-probe-01dcd4603ac04b6a9df7bcd2326eb365` (146 entries,
  manifest SHA-256
  `02050AEBED36DC55385E5C04F989D9511A0E24D1CF45DACB6C71E7E462D0A81A`). Safe
  next action is read-only Terra review; keep selector wiring, scan,
  provider-readiness, staging, pilot, production, and live browser actions
  closed.

## Current page-set ownership-preserving abort contract (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. This task changed only the supplied
  allowlist: `services/external_gateways.py`,
  `tests/test_external_gateways.py`,
  `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`, this state file, and one
  append-only safety decision. No browser, Playwright, persistent profile,
  Zalo/BCNB, database, `.env`, scan, or external action was performed.
- `PageSetClassifier` exposes only six finite sanitized categories:
  `PAGESET_UNAVAILABLE`, `PAGESET_PREEXISTING_ZERO`,
  `PAGESET_PREEXISTING_ONE`, `PAGESET_PREEXISTING_MANY`,
  `PAGESET_OBSERVER_ONLY`, and `PAGESET_POSTCREATE_MISMATCH`; count buckets
  are only `0`, `1`, and `2PLUS`. `PAGESET_OBSERVER_ONLY` now additionally
  requires a private weak provenance link to the exact same context captured
  before creation; cross-context, stale, absent, or unbindable provenance is
  `PAGESET_POSTCREATE_MISMATCH`. It reads context page-set availability/count
  and explicit observer ownership only. It does not read or return page
  identity, title, URL, DOM, text, selectors, exceptions, profile data,
  credentials, tokens, or raw context references.
- `PersistentBrowserSession.abort_without_observation()` is explicit and
  non-owning. It accepts only a non-observer-only classification, records
  typed `NON_OWNING_ABORT` plus zero page/context/Playwright close counts, and
  causes later cleanup to return typed
  `CLEANUP_SKIPPED_NON_OWNING_ABORT` without invoking page close, context
  close, or Playwright stop. Normal owned cleanup remains unchanged unless
  this method is selected. No scan runtime/probe/selector/auth wiring was
  added.
- Fresh TDD RED before production edit: focused page-set suite exit `1`,
  `Ran 6 tests`, `FAILED (errors=6)` because `PageSetClassifier` was absent;
  it was not a syntax/import failure. Fresh GREEN: focused suite exit `0`,
  `Ran 7 tests`, `OK`; entire `tests.test_external_gateways` exit `0`,
  `Ran 54 tests`, `OK`; Ruff on both changed Python paths exit `0`; mypy on
  `services/external_gateways.py` exit `0`; `tests/check_phase4_quality.py`
  exit `0` with `phase4-quality-gate-ok`; and `git diff --check` on changed
  Python paths exit `0`.
- `CHECK_QUALITY.ps1` remains unrun because isolated database/config
  preservation was not proven. Full-gate status remains
  `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`; no staging, pilot, live,
  authentication, or production acceptance is claimed.
- Report: `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`. Final immutable
  manifest comparison against baseline
  `pageset-ownership-abort-efd3b2a371d24c12a7647db8703b5057` (147 entries,
  manifest SHA-256
  `E279CF52C80B4EC3477A4C53391B9DA68E722DAD9B2D7471E12B086894447E22`) is
  Root's post-Luna audit step. Safe next action is independent Terra review;
  keep live probe, selector/auth wiring, scan, staging, pilot, and production
  actions closed.
- Terra P1 repair evidence: focused page-set suite exit `1` before the repair
  (`Ran 9`, `FAILED (failures=2)`) because a Context A snapshot incorrectly
  yielded observer-only for Context B. After adding weak context-affine
  provenance, the focused suite is exit `0` (`Ran 9`, `OK`), the full gateway
  suite is exit `0` (`Ran 56`, `OK`), and Ruff, source mypy, phase-4 quality,
  and allowlist `git diff --check` are all exit `0`. No live action occurred;
  status remains **AWAITING CODEX REVIEW**.

## Current unforgeable page-set provenance repair (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. This repair remains local/hermetic and
  changed only the supplied allowlist. No browser, Playwright, persistent
  profile, Zalo/BCNB, database, `.env`, scan, selector/auth wiring, or external
  action was performed.
- The rejected public `_context_ref` path is removed from
  `PageSetClassification`. Public snapshots are frozen sanitized values with
  only `category`, `count`, and `action`; equality, `repr`, JSON-facing
  `as_dict()`, and pickle serialization contain no context, page, callable, or
  provenance token. Each classifier records only exact pre-create zero
  snapshots in a bounded 64-entry identity table of weak snapshot/context
  references plus expected sanitized fields. Copied classifiers do not inherit
  the table, and no page or context is strongly retained by this tracking.
- `PAGESET_OBSERVER_ONLY` requires the exact snapshot identity owned by the
  same classifier, unchanged pre-create zero fields, the same live context,
  exactly one current page, and identity equality with the explicit observer
  page. Unweakrefable, absent, stale/evicted, cross-context, copied,
  serialized, mutated, or forged provenance fails closed as
  `PAGESET_POSTCREATE_MISMATCH`; the only follow-on action is the existing
  non-owning abort with zero page/context/Playwright close calls.
- Fresh TDD RED before production edit: focused page-set suite exit `1`,
  `Ran 13 tests`, `FAILED (failures=4)`, reproducing copied/current-context
  provenance acceptance and public `_context_ref` construction. Fresh GREEN:
  focused suite exit `0`, `Ran 15 tests`, `OK`; entire
  `tests.test_external_gateways` exit `0`, `Ran 62 tests`, `OK`; targeted Ruff,
  source mypy, phase-4 quality, and allowlist `git diff --check` all exit `0`.
- `CHECK_QUALITY.ps1` remains unrun because database/config isolation is not
  proven or authorized. Full-gate status remains
  `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`; no staging, pilot, live,
  authentication, selector, scan, or production acceptance is claimed.
- Task baseline: `pageset-unforgeable-provenance-5b015939745641f887e8c844c6f68fe2`,
  148 entries, manifest SHA-256
  `55A016D109AD9ECF1802FFC6A5AF298704A28B1B7F5544787A02BE48E79C526E`. Final
  immutable manifest comparison and out-of-scope preservation remain Root's
  audit step. Report: `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`.

## Current corrective page-set transition repair (2026-09-04)

- Status: **AWAITING CODEX REVIEW** under the single authorized Terra repair
  loop. Terra found that the prior bounded ledger still exposed
  `_record_precreate_provenance()` and mutable `_precreate_provenance`, so a
  caller could mint, alias, or transfer a forged current-context proof.
- The corrective implementation removes both instance paths and no longer
  accepts a public snapshot as authority. A zero-page classifier call records
  only one weak pending context reference in closure-owned state for that
  classifier. The supported controlled sequence is
  `classify_before_create(context)` followed by the same classifier's
  `classify_after_create(context, observer_page)`. The legacy snapshot-taking
  form remains only as a fail-closed compatibility path.
- Public results remain frozen sanitized `category`, `count`, and `action`
  values. Observer-only requires the pending classifier sequence, exact same
  live context, exactly one page, and identity equality with the supplied
  observer page. Private-helper/cache injection, cache alias/transfer, copied
  or serialized snapshots, other classifiers, mutation, absent/unweakrefable,
  stale, and cross-context cases fail closed as
  `PAGESET_POSTCREATE_MISMATCH`; abort retains zero page/context/Playwright
  close counts and normal owned cleanup is unchanged.
- Repair-loop RED before production edit: focused page-set suite exit `1`,
  `Ran 18 tests`, `FAILED (failures=3)`, reproducing private helper minting,
  mutable cache injection, and cache transfer. Fresh GREEN: focused suite exit
  `0`, `Ran 18 tests`, `OK`; entire `tests.test_external_gateways` exit `0`,
  `Ran 65 tests`, `OK`; targeted Ruff, source mypy, phase-4 quality, and
  allowlist `git diff --check` all exit `0`.
- The repair remains local/hermetic. No browser, Playwright, persistent
  profile, Zalo/BCNB, database, `.env`, selector/auth, scan, or external action
  was performed. `CHECK_QUALITY.ps1` remains unrun because database/config
  isolation is not proven or authorized; full-gate status remains
  `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`.
- Baseline remains `pageset-unforgeable-provenance-5b015939745641f887e8c844c6f68fe2`,
  148 entries, manifest SHA-256
  `55A016D109AD9ECF1802FFC6A5AF298704A28B1B7F5544787A02BE48E79C526E`. Final
  manifest comparison and out-of-scope preservation remain Root's audit step.
  Report: `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`.

## Final unforgeable page-set transition (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. The final corrective state remains within
  the supplied five-path allowlist and local/hermetic boundary. No browser,
  Playwright, persistent profile, Zalo/BCNB, database, `.env`, selector/auth,
  scan, runtime, or external action was used.
- The public snapshot is frozen and contains only sanitized `category`,
  `count`, and `action`. `PageSetClassifier` has no instance
  `_precreate_provenance` cache and no `_record_precreate_provenance()` helper.
  No classifier, module wrapper, or `PersistentBrowserSession` entry point
  accepts a public snapshot or `preexisting` keyword as authority. The only
  observer-only sequence is the same classifier's
  `classify_before_create(context)` followed by
  `classify_after_create(context, observer_owned_page)`.
- Transition state is closure-owned and weak-only, with at most one pending
  context reference per live classifier and one-shot consumption after the
  controlled transition. Copying, aliasing, or transferring classifier state
  cannot mint proof. Observer-only additionally requires exact context and
  observer-page identity after a verified zero-page pre-state. Missing,
  stale, unweakrefable, cross-context, copied, serialized, mutated, aliased,
  transferred, or forged cases fail closed as
  `PAGESET_POSTCREATE_MISMATCH`; non-owning abort retains zero page/context/
  Playwright close counts.
- Repair-loop RED: focused page-set exit `1`, `Ran 18 tests`,
  `FAILED (failures=3)`, reproducing private-helper minting, mutable cache
  injection, and cache transfer. Fresh GREEN: focused exit `0` (`Ran 18`,
  `OK`) and full `tests.test_external_gateways` exit `0` (`Ran 65`, `OK`).
  Targeted Ruff, source mypy, `tests/check_phase4_quality.py`, and allowlist
  `git diff --check` all exit `0`.
- `CHECK_QUALITY.ps1` remains unrun because database/config isolation is not
  proven or authorized. Full-gate status remains
  `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`; no live/auth/readiness,
  staging, pilot, or production acceptance is claimed. Root's next safe step
  is the immutable manifest audit followed by Terra review.

## Current owner-selected cooperative-caller page-set contract (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. The strict claim that page-set provenance
  was unforgeable under arbitrary same-process introspection is rejected. The
  preceding unforgeable repair sections are historical and superseded by this
  corrective section; their history is not rewritten.
- Source now declares the typed `PageSetThreatModel` contract and
  `PAGESET_THREAT_MODEL = PageSetThreatModel.COOPERATIVE_CALLER_ONLY`. This is
  a bounded internal correctness/fail-closed contract for cooperative project
  callers only. It is not a defense against same-process introspection,
  monkey-patching, or malicious code deliberately accessing implementation
  internals; it is not security, authentication, identity, readiness, or
  permission proof.
- Accepted behavior remains the finite sanitized page-set vocabulary,
  no-page-facade-read classification, and the documented cooperative
  same-classifier `classify_before_create(context)` to
  `classify_after_create(context, observer_owned_page)` transition when its
  checks pass. The public/documented APIs accept no detached snapshot or
  context reference as authority. Observer-only denotes an internal lifecycle
  transition only, not permission to take browser action.
- Fail-closed behavior remains for unavailable, absent, unbindable,
  cross-context, or wrong-classifier transitions. Explicit non-owning abort
  still records `NON_OWNING_ABORT`, performs zero page/context/Playwright
  closes, and causes cleanup to return
  `CLEANUP_SKIPPED_NON_OWNING_ABORT`; normal owned cleanup remains unchanged.
- Fresh TDD RED before the production edit: the new threat-model test exit `1`,
  `Ran 1 test`, `FAILED (errors=1)` due to missing
  `PAGESET_THREAT_MODEL`. Fresh GREEN: focused page-set suite exit `0`,
  `Ran 13 tests`, `OK`; entire `tests.test_external_gateways` exit `0`,
  `Ran 60 tests`, `OK`; targeted Ruff, source mypy, and
  `tests/check_phase4_quality.py` each exit `0`. Allowlist
  `git diff --check` exit `0` for tracked paths; the three untracked-path
  no-index checks returned expected diff exit `1` with no whitespace output.
- Scope is local/hermetic only. No browser, Playwright, persistent profile,
  Zalo/BCNB, database, `.env`, selector/auth/readiness, scan, staging, pilot,
  live, or production action occurred. `CHECK_QUALITY.ps1` remains unrun
  because database/config isolation is not proven or authorized; full-gate
  status remains `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`.
- Supplied baseline for this corrective task:
  `pageset-cooperative-threat-model-13765449372d4ac0afc49ed54c853598`, 148
  entries, manifest SHA-256
  `5008586D7DF9BEE34D7A4F13FC9A6B3B3828949B74CCAEF9F2B4901E941E7E1C`.
  Final immutable manifest comparison and out-of-scope preservation remain
  Root's audit step. Report: `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`.

## Current safe lifecycle for the internal landing probe (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. This local/hermetic implementation is
  limited to `services/external_gateways.py`,
  `tests/test_external_gateways.py`,
  `docs/ZALO_LANDING_PROBE_SAFE_LIFECYCLE_REPORT.md`, this state file, and
  one append-only lifecycle decision. No browser, Playwright launch,
  persistent profile, Zalo/BCNB, HTTP scan, database, `.env`, cookie/session
  inspection, screenshot, or external action occurred.
- The gateway probe now uses a dedicated worker launch path with the exact
  existing persistent Chromium options, one launch maximum, and no circuit
  retry. It classifies the context page set before creating a page. Unavailable,
  nonzero, and post-create mismatch states select the existing explicit
  non-owning abort, preserve zero page/context/Playwright close counts, and
  return only sanitized typed diagnostics/counters.
- Only the same classifier's zero-to-one observer transition may continue. The
  observer page is created through a no-init-script path; validity is checked
  before any page-facade access/navigation. Once ownership is proven, every
  terminal pre-navigation page-state error/cancellation closes that observer
  page exactly once; the valid path permits one fixed landing navigation and
  closes only that proven observer page. It never invokes landing-facts
  provider, search/click/type/scroll,
  chat/login/QR, BCNB, evidence, job, selector fallback, or auth/readiness
  behavior. Generic persistent-session cleanup remains unchanged.
- Fresh fake-resource RED: focused safe-lifecycle command exit `1`,
  `Ran 10 tests`, `FAILED (failures=4, errors=6)` before production edits.
  Fresh GREEN: focused lifecycle `10/10`, existing internal probe `16/16`,
  and full `tests.test_external_gateways` `70/70` all exit `0`; scoped Ruff,
  no-incremental source mypy, Phase-4 quality, and scoped diff checks all exit
  `0`.
- `CHECK_QUALITY.ps1` remains intentionally unrun because database/config
  isolation is not authorized or proven; full-gate status remains
  `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`. A separate Terra
  preflight/review and separate Owner live-action approval remain mandatory;
  no local result is staging, pilot, live, authentication, readiness, or
  production acceptance.
- Supplied baseline: `landing-probe-safe-lifecycle-b2bff46fd37b4253ad40d75c8b1d5c65`,
  148 entries, manifest SHA-256
  `801889AA828C9C93743083C1BA40D6245FCD3D8C0873492864BC5086B108C6BF`.
  Final immutable manifest comparison and out-of-scope preservation remain
  Root's audit step. Report:
  `docs/ZALO_LANDING_PROBE_SAFE_LIFECYCLE_REPORT.md`.

## Corrective owned-observer release (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. Terra's single approved repair loop found
  that a pre-navigation page-state cancellation/error after the
  `PAGESET_OBSERVER_ONLY` transition selected non-owning abort and stranded
  the known-owned observer page. The source now clears ownership and performs
  one bounded, cancellation-shielded observer-page close before re-raising
  cancellation; the finalizer cannot close it a second time. Missing
  navigation uses the same release. No context close, Playwright stop, or
  non-owning abort occurs after ownership is proven.
- Pre-ownership create/cancellation/error/mismatch paths and unavailable,
  pre-existing, multiple, and generic persistent-session boundaries retain
  their prior zero-close/stop semantics. The correction is covered only by
  synthetic fake resources; no browser, persistent profile, Zalo/BCNB, HTTP,
  database, `.env`, cookie/session, selector/auth/readiness, scan, or external
  action occurred.
- Repair RED before the production edit: focused lifecycle exit `1`,
  `Ran 11 tests`, `FAILED (failures=2)`, for the two expected zero-close
  assertions. Fresh repair GREEN and scoped quality evidence are recorded in
  `docs/ZALO_LANDING_PROBE_SAFE_LIFECYCLE_REPORT.md`; full quality remains
  intentionally unrun because database/config isolation is not authorized or
  proven. Separate Terra review and separate Owner live-action approval remain
  mandatory.

## Current owned-observer cancellation audit (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. This implementation is limited to the
  supplied five-path allowlist: `services/external_gateways.py`,
  `tests/test_external_gateways.py`,
  `docs/ZALO_LANDING_PROBE_SAFE_LIFECYCLE_REPORT.md`, this state file, and the
  append-only cancellation decision. No browser, Playwright, persistent
  profile, Zalo/BCNB, HTTP scan, database, `.env`, cookie/session inspection,
  screenshot, or external action occurred.
- After the cooperative same-classifier `PAGESET_OBSERVER_ONLY` transition,
  pre-navigation page-state cancellation closes the known observer page once,
  stores a probe-scoped typed `CleanupResult`, and emits one sanitized
  `browser.cleanup` event before re-raising the original cancellation.
  Successful page-only audit state is `CLEANUP_OK`/`CLOSED`; context and
  Playwright remain `OPEN`. Audit/telemetry failure is fail-safe. Non-owning
  pre-ownership cancellation and ordinary non-cancellation page-state errors
  retain their prior semantics.
- Fresh TDD RED before the production edit: focused lifecycle command exit `1`,
  `Ran 13 tests`, `FAILED (failures=3)`, for missing stored result and cleanup
  telemetry. Fresh GREEN: focused lifecycle `13/13`, existing internal probe
  `16/16`, and full `tests.test_external_gateways` `73/73`, all exit `0`,
  `OK`; scoped Ruff, no-incremental source mypy, and Phase-4 quality also exit
  `0`.
- The focused telemetry-failure test confirms a failing sink cannot replace
  cancellation. Result and telemetry contain only typed lifecycle enums,
  bounded integers, and fixed event fields; no raw page/context, URL,
  selector, profile, configuration, cookie, account, message, content, or
  exception text is retained or emitted.
- `CHECK_QUALITY.ps1` remains intentionally unrun because database/config
  isolation is not authorized or proven. Full-gate status remains
  `FULL_GATE_BLOCKED_DATABASE_ISOLATION_NOT_PROVEN`; no staging, pilot, live,
  authentication, readiness, or production acceptance is claimed. Root must
  capture the final immutable manifest and perform the out-of-scope status/hash
  comparison before Terra review.
- Supplied baseline: ID
  `landing-probe-cancel-audit-f3a934c017fe41299a8f6f7a0322baf3`, `149` entries,
  manifest SHA-256
  `6B48126203F085112452C8996586E67E3B6631E0E3A5FD89B67CE5230DF72E5C`,
  stored outside the workspace. Report:
  `docs/ZALO_LANDING_PROBE_SAFE_LIFECYCLE_REPORT.md`.

## Current internal landing probe one-shot runner (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. The implementation is limited to the
  supplied eight-path allowlist: `api/landing_probe_routes.py`, `main.py`,
  `services/landing_probe_runner.py`, `services/runtime.py`,
  `tests/test_landing_probe_runner.py`,
  `docs/ZALO_LANDING_PROBE_ONE_SHOT_RUNNER_REPORT.md`, this state file, and
  `PROJECT_DECISIONS.md`.
- `run_landing_probe_once()` accepts an injected existing runtime, waits on its
  existing `browser_lock` for a fixed bounded interval, consumes the
  process-lifetime runtime latch before calling the exact bound
  `runtime.external.browser.probe_landing_selector_proposals` once, and has no
  request-controlled timeout. Busy lock, replay, unavailable binding, timeout,
  and ordinary gateway failure are finite sanitized statuses. Cancellation is
  re-raised after releasing the lock and remains consumed once the gateway
  call has started.
- `POST /api/internal/zalo/landing-probe/one-shot` is loopback-client-only and
  rejects query/body input before reading runtime state. It obtains exactly
  `request.app.state.container.worker_runtime`, does not enqueue a job, and
  returns only the fixed runner envelope. No route or runner invocation is
  performed on import or startup.
- Fresh hermetic RED: the initial focused command exited `1` with `Ran 1`
  import error while the new route was absent; the behavior RED after minimal
  stubs exited `1` with `Ran 12`, `FAILED (failures=8, errors=2)`. These were
  test-session observations before the production behavior was implemented.
- Fresh hermetic GREEN: focused runner suite exited `0`, `Ran 14 tests`,
  `OK`; existing `tests.test_external_gateways` exited `0`, `Ran 73 tests`,
  `OK`; `tests.test_api_routes_unit tests.test_api_errors` exited `0`,
  `Ran 26 tests`, `OK`; targeted Ruff, no-incremental mypy over the four
  edited production modules, and `tests/check_phase4_quality.py` all exited
  `0`.
- No browser, Playwright/profile launch, HTTP listener, external request,
  Zalo/BCNB action, selector/auth/readiness action, job/dispatcher/database,
  `.env`/Config read by the runner, cookie/session inspection, or production
  action occurred. `services/external_gateways.py` was not changed. Local
  hermetic evidence is not live, staging, pilot, readiness, or production
  acceptance.
- Supplied immutable baseline: ID
  `landing-probe-one-shot-runner-v2-2fbff2c95df6483f9d8785158c4d92c8`, `149`
  entries, manifest SHA-256
  `FB3CF0EFC78D2EA06DB1DE0D338F1259E12359D5C2DD38D471716E2BF5C00FB6`, stored
  outside the workspace. Root must capture the final manifest and compare all
  non-task paths by exact status and SHA-256/marker before Terra review.
- Safe next action is Root's final manifest comparison followed by one
  read-only Terra review. Any live probe still requires separate Owner
  approval, preflight, rollback/resource handoff, and must not use this route
  as a scan or production canary.

## Current one-shot route-contract repair (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. Terra identified a P1 route-contract
  breach: the first implementation used `/api/internal/landing-probe/one-shot`.
  The sole registered route is now exactly
  `POST /api/internal/zalo/landing-probe/one-shot`; the former path is not an
  alias.
- Fresh hermetic router-registration coverage is included in the focused
  runner suite: the required path invokes the bound probe once; the former
  path returns `404` with zero calls; absent client and spoofed forwarding
  headers return `403` with zero calls. The repair focused suite exited `0`,
  `Ran 17 tests`, `OK`.
- Repair scope is limited to `api/landing_probe_routes.py`,
  `tests/test_landing_probe_runner.py`, this report, this state file, and the
  append-only correction decision. `main.py`, the runner, runtime latch/lock,
  and `services/external_gateways.py` were not changed in the repair.
- No browser, Playwright/profile, listener, live network, Zalo/BCNB, database,
  `.env`/Config, cookie/session, job, evidence, scan, or production action
  occurred. Local hermetic evidence is not live or production acceptance.
- Safe next action is Root's final marker-safe manifest comparison against
  baseline `landing-probe-one-shot-runner-v2-2fbff2c95df6483f9d8785158c4d92c8`
  followed by the same Terra reviewer. `CHECK_QUALITY.ps1` remains prohibited.

## Current auth-selector provenance contract audit (2026-09-04)

- Status: **AWAITING CODEX REVIEW**; result is a source-only blocker,
  `BLOCKED_SELECTOR_PROVENANCE_NOT_PROVEN`. The audited allowlist is exactly
  `services/external_gateways.py`, `core/container.py`,
  `tests/test_external_gateways.py`, `tests/test_zalo_parallel_service.py`,
  `docs/ZALO_AUTH_SELECTOR_PROVENANCE_CONTRACT_REPORT.md`, this file, and
  `PROJECT_DECISIONS.md`.
- Current source explicitly marks `#contact-search-input` as the only proven
  selector contract. Authenticated-shell and login/QR facts are `unproven` in
  `LANDING_FACT_SOURCE_BASIS`; runtime values can only come from optional
  injected callbacks, which are not structural selector provenance. Production
  composition (`ExternalGateways.production` and `AppContainer`) supplies no
  auth-shell or login/QR selector definitions. No production binding, selector,
  callback, environment value, fallback, dynamic discovery, or test change was
  added. `services/zalo_parallel_service.py` was not modified.
- The internal landing probe retains `UNPROVEN` candidate statuses and
  `LANDING_PROBE_NO_PROVEN_CANDIDATE`; URL/text/class inference is rejected.
  Existing readiness behavior remains fail-closed before target claim/search/
  evidence/BCNB work for unknown, timeout, detach, ambiguous, login/QR, modal,
  unsupported-browser, and network states.
- Fresh bounded evidence at 2026-09-04 22:25 (+07:00):
  `\.\venv\Scripts\python.exe -m unittest tests.test_external_gateways
  tests.test_zalo_parallel_service` exited `0`, `Ran 104 tests`, `OK`;
  source-only assertions over the four audited source/test files exited `0`
  with `SOURCE_PROVENANCE_AUDIT=PASS`. These are hermetic/static checks only;
  no browser, Playwright, profile, Zalo/BCNB, live endpoint, network, database,
  `.env`/Config, scan, search, chat, login, QR, screenshot, or external action
  was run. `CHECK_QUALITY.ps1` remains unrun by instruction.
- Supplied immutable baseline: ID
  `auth-selector-provenance-contract-6f75a6526f2242ffa7065be5dbcd9480`, 153
  entries, manifest SHA-256
  `BA56D7705F7E560C2804738F729576DCAFACD132F15701003F519B7E2F73EE8B`.
  Final marker-safe manifest capture/comparison must be performed by Root after
  this state/report/decision bookkeeping; all non-allowlisted dirty paths
  remain Owner-owned. Safe next action is one read-only Terra review of the
  exact documentation-only diff. No auth-selector, readiness, scan, staging,
  pilot, live, or production acceptance is claimed.
- Report: `docs/ZALO_AUTH_SELECTOR_PROVENANCE_CONTRACT_REPORT.md`. A future
  selector task needs separately authorized fresh structural observations from
  the intended persistent profile/context, exact selector definitions with
  bounded visibility semantics, independent provenance, ambiguity/error gates,
  sealed typed production binding, and raw-selector-free telemetry/results.

## Current structural selector observation boundary (2026-09-04)

- Status: **AWAITING CODEX REVIEW**. A separate local/hermetic structural
  observation capability is implemented within the supplied allowlist. It is
  deliberately not an auth-selector implementation: the sealed candidates
  are exactly `AUTHENTICATED_SHELL` and `LOGIN_OR_QR`, both remain `UNPROVEN`,
  and no concrete selector, attribute, text, callback, or positive provenance
  is defined or exposed.
- `PersistentBrowserSession.observe_structural_candidates()` accepts only an
  optional sanitized telemetry sink. It uses the existing persistent context,
  classifies the cooperative page set before any observer operation, creates
  at most one observer page only from a zero-page state, navigates at most once
  to the approved landing URL, reads only typed host/search landing facts, and
  closes only its proven observer page. Context and Playwright ownership remain
  with the caller. It never invokes search, click, typing, chat, login/QR,
  screenshot, DOM/body/text/content export, scan, evidence, BCNB, job, DB, or
  readiness behavior.
- Fixed bounds are 5 seconds per structural operation, 5 seconds for landing
  navigation, and 30 seconds total. The existing worker runtime browser lock
  and a process-lifetime structural latch make the runner and
  `POST /api/internal/zalo/structural-observation/one-shot` route one-shot;
  busy, replay, unavailable, timeout, cancellation, page-set, detach, and
  unknown states remain typed and fail closed. No route body/query input is
  accepted, and the route reads exactly the existing worker runtime.
- Fresh hermetic RED before production edits: the focused structural command
  exited `1`, `Ran 13 tests`, `FAILED (failures=12, errors=1)`. Fresh GREEN:
  focused structural tests exited `0`, `Ran 15 tests`, `OK`; combined
  `tests.test_external_gateways tests.test_landing_probe_runner` exited `0`,
  `Ran 105 tests`, `OK`; targeted Ruff exited `0` (`All checks passed!`); and
  no-incremental mypy over the four production modules exited `0` (`Success:
  no issues found in 4 source files`). These are synthetic/local checks only.
- No browser, Playwright/profile launch, listener, live network, Zalo/BCNB,
  scan/search/chat/login/QR, screenshot, database, `.env`/Config,
  cookie/session, or external action occurred. `CHECK_QUALITY.ps1` was not run:
  database/configuration isolation is not proven for this bounded task, so the
  full gate remains blocked rather than weakening the guards. Local GREEN is
  not live, staging, pilot, readiness, or production acceptance.
- Supplied immutable baseline: ID
  `zalo-structural-observation-ff029f6a27bb45aaa0fdc0f399d37730`, `154` entries,
  manifest SHA-256
  `0ECDC7F0C2684487815D6D256A61C3367810EA020B5CEA794C0434C96598E652`.
  Root must perform the final marker-safe manifest comparison and preserve all
  pre-existing non-allowlisted paths before one read-only Terra review.
- Report: `docs/ZALO_STRUCTURAL_SELECTOR_OBSERVATION_REPORT.md`. Safe next
  action is that final manifest comparison and Terra review. Any later live
  invocation needs separate Owner approval, Terra preflight, exact target and
  context proof, and rollback/resource handoff.

## Structural observation rendezvous repair snapshot (2026-09-05)

- `status`: **AWAITING TERRA REREVIEW** after the Owner-approved
  `APPROVE ACTIVE-SESSION RENDEZVOUS DESIGN` repair loop. The implementation
  remains local/hermetic and does not claim auth-selector provenance or live
  readiness.
- Terra P1 repair: `run_structural_observation_once()` now only requests the
  exact bound gateway rendezvous; it never acquires or bypasses
  `WorkerRuntime.browser_lock`. `PlaywrightBrowserGateway.persistent_session()`
  binds the active session, verifies that exact runtime lock is held, and
  services one request in a fixed `0.1` second pre-yield barrier before normal
  work receives the session. This is reachable through the existing worker
  lifecycle; no out-of-allowlist file or browser-launch authority was added.
- Attempt semantics: the owner consumes the process-lifetime latch only after
  claiming a rendezvous request. No active session, unavailable binding,
  unlocked/foreign owner, post-yield busy, duplicate, timeout, or
  cancellation-before-claim path consumes it. Replay after a successful claim
  is `ALREADY_CONSUMED`; cancellation after claim leaves owner observation and
  observer-only cleanup intact. Zero-page, pre-existing/multiple/foreign,
  detach, navigation, timeout, and cleanup outcomes remain typed diagnostics.
- Terra P2 repair: the sealed `AUTHENTICATED_SHELL` and `LOGIN_OR_QR`
  candidates remain `UNPROVEN` with no selector, attribute, text, count, or
  visibility provenance. Sanitization forces every candidate status to
  `UNPROVEN`; operational errors are diagnostics only. A completed transport
  envelope is not useful candidate provenance and cannot emit
  `AUTHENTICATED_READY` or enable scan/search/chat/login/QR/BCNB work.
- Fresh evidence: structural focused suites exit `0`, `Ran 22 tests`, `OK`;
  combined gateway/runner suites exit `0`, `Ran 112 tests`, `OK`; scoped Ruff
  exits `0` (`All checks passed!`); and mypy over five production modules exits
  `0` (`Success: no issues found in 5 source files`). Genuine RED before
  production edits is recorded in the report: rendezvous lifecycle `Ran 7`
  with exit `1` (`5` failures, `1` error), plus the known operational-status
  sanitization regression `Ran 1` with exit `1`.
- Full `CHECK_QUALITY.ps1` remains intentionally unrun because DB/config
  isolation and browser/external side-effect safety are not proven under this
  task's hermetic boundary. This is the exact full-gate blocker, not a reason
  to weaken the guards. Root must perform the final marker-safe manifest
  comparison and obtain Terra rereview. Any later live invocation still needs
  separate Owner approval, Terra preflight, exact target/context proof, and
  rollback/resource handoff.

## Stage 1 independent auth-readiness diagnostic (2026-09-05)

- Status: **AWAITING TERRA PREFLIGHT**. The Owner-approved end-to-end plan is
  being implemented locally/hermetically. No live browser/profile launch,
  network, login/logout, QR/OTP, database, Zalo, BCNB, Google Form, message,
  screenshot, or external write occurred in this stage.
- Planned boundary: `DiagnosticBootstrap` accepts only explicit non-secret
  worker profile/lock paths or creates an owned empty temporary profile. The
  diagnostic uses the production persistent Playwright adapter, a process
  lock plus non-reclaiming interprocess lock, one accepted default page, at
  most one fixed landing navigation, and 120-second observation/15-second
  cleanup deadlines. It emits only bounded structural selector/fingerprint,
  role/tag, filtered attribute, count, and visibility metadata to a private
  artifact outside Git. It does not bind auth selectors or claim readiness.
- Safety boundary: cancellation, partial launch, timeout, and failure must
  close only owned browser resources exactly once; worker profiles are never
  deleted; empty-temp profiles are deleted only after cleanup and lock
  release. Existing/stale locks are `BUSY` and are never reclaimed. Legacy
  landing/structural one-shot routes return HTTP 410 before runtime access.
- Evidence: `docs/ZALO_E2E_AUTH_READINESS_IMPLEMENTATION_PLAN.md` and
  `docs/ZALO_AUTH_READINESS_DIAGNOSTIC_REPORT.md`. Fresh Stage 1 RED was
  captured before the boundary existed; GREEN diagnostic tests exited `0`,
  `Ran 11 tests`, `OK`; affected gateway/route tests exited `0`, `Ran 112
  tests`, `OK`; scoped Ruff exited `0` (`All checks passed!`); and mypy over
  five production modules exited `0` (`Success: no issues found in 5 source
  files`). These are hermetic fake-I/O checks only. Full quality and live
  worker/empty-profile observations require Terra preflight and later gates.

## Stage 1 P1 lifecycle repair (2026-09-05)

- Status: **AWAITING TERRA PREFLIGHT**. This is the current superseding
  snapshot for the independent auth-readiness diagnostic; no live browser,
  profile, network, login/logout, QR/OTP, database, Zalo, BCNB, Form, message,
  screenshot, or external write was performed.
- P1a fixed: the normal production `persistent_session()` path no longer
  invokes the retired structural rendezvous or binds a diagnostic runner to
  `WorkerRuntime`; `run_structural_observation_once()` returns
  `REQUEST_REJECTED` / `STRUCTURAL_RUNNER_RETIRED` without runtime, lock,
  latch, or browser access. The old one-shot endpoints remain HTTP 410 before
  runtime access. Obsolete rendezvous tests were removed and replaced with
  retired-runner/410 and normal-lifecycle coverage.
- P1b fixed: diagnostic cleanup supplies one monotonic total budget across
  owned page/context/Playwright/manager operations. Deadline expiry is
  reported as `cleanup_incomplete=true`, `CLEANUP_INCOMPLETE`, and a false
  cleanup invariant; the profile lock remains held and replay is blocked as
  `BUSY` / `CLEANUP_INCOMPLETE`. Legacy callers without a total budget retain
  their prior per-operation cleanup behavior.
- Fresh evidence: the three focused repair regressions exited `0` (`Ran 3
  tests`, `OK`); the affected suites exited `0` (`Ran 115 tests`, `OK`, no
  skips). The pre-edit RED evidence is recorded in
  `docs/ZALO_AUTH_READINESS_DIAGNOSTIC_REPORT.md`. Scoped Ruff exited `0`
  (`All checks passed!`), mypy over five production modules exited `0`
  (`Success: no issues found in 5 source files`), and affected production
  modules compiled successfully. Full `CHECK_QUALITY.ps1` is intentionally
  deferred because its DB/config/browser isolation is not proven.
- Safe next action: Root captures the allowlist/baseline delta and dispatches
  Terra's read-only preflight. Do not bind selectors or start live worker or
  empty-profile observations until Terra accepts this repair.

## Stage 1 EMPTY_TEMP retention repair (2026-09-05)

- Status: **AWAITING TERRA FOCUSED REREVIEW**. Terra reproduced that the
  incomplete-cleanup path retained the EMPTY_TEMP lock but deleted its owned
  temporary profile directory. No live browser or external action was run.
- Fix: finalization now deletes an EMPTY_TEMP profile only after cleanup is
  verified complete, the process lock release succeeds, and the lock path is
  confirmed absent. Incomplete, failed, or unproven cleanup retains both
  directory and lock, reports sanitized `CLEANUP_INCOMPLETE` with a false
  invariant, and blocks diagnostic instance reuse. Worker profiles remain
  untouched; non-reclaiming lock behavior and 120/15-second bounds are
  unchanged.
- Evidence: RED focused regression exited `1` because retained profile count
  was `0`; GREEN focused regression exited `0` (`Ran 1 test`, `OK`). The
  affected suites then exited `0` (`Ran 116 tests`, `OK`, no skips), scoped
  Ruff exited `0` (`All checks passed!`), and mypy over five production
  modules exited `0` (`Success: no issues found in 5 source files`).
- Safe next action: Root records the unchanged allowlist/baseline delta and
  requests Terra's focused rereview; live observation remains blocked until
  that gate accepts the retention behavior.

## Stage 2 live auth-readiness observations (2026-09-05)

- Status: **OBSERVATION COMPLETE; USEFUL CANDIDATE EVIDENCE NOT FOUND**. Terra
  accepted Stage 1 before this authorized live sequence. No code changed
  before or during observation. No login/logout, QR/OTP, scan/search/chat,
  screenshot, database, BCNB, Google Form, Zalo message, or external write
  occurred.
- Binding: named source keys were independently absent; preflight used only
  source defaults `BROWSER_PROFILE_PATH` → `zalo_session` and `LOCK_ROOT` →
  `runtime/locks`, resolved against the repository root. Absolute profile and
  lock values are intentionally omitted from repository state. Non-reclaiming
  lock semantics were used; an absent lock file was not treated as proof of
  unused ownership.
- Worker command used `python.exe -B -X utf8 -m
  scripts.zalo_auth_readiness_diagnostic --profile-kind worker` with explicit
  resolved profile/lock and a fresh private system-temp artifact root. Exit
  `0` in approximately `7.458s`; observed at
  `2026-09-05T16:49:14.20202+07:00`; schema
  `zalo-auth-readiness-diagnostic-v1`; `COMPLETED` /
  `NO_STRUCTURAL_CANDIDATES`; `DEFAULT_PAGE_ACCEPTED`; navigation `1`;
  candidates `0`; cleanup `CLEANUP_OK`, incomplete `false`, invariant `true`.
- EMPTY_TEMP then used the same CLI with `--profile-kind empty-temp`. Exit
  `0` in approximately `2.657s`; observed at
  `2026-09-05T16:49:28.606574+07:00`; same schema and lifecycle result;
  candidates `0`; cleanup `CLEANUP_OK`, incomplete `false`, invariant
  `true`; verified remaining owned temp profile directories `0` and lock
  files `0`.
- Private artifacts (outside Git) are recorded in
  `docs/ZALO_AUTH_READINESS_OBSERVATION_REPORT.md` by sanitized filename only.
  No raw selector, text, DOM/body, cookie, account, QR, screenshot, or
  arbitrary URL was persisted. Both runs yielded zero useful candidates, so
  auth selector binding and Stage 3 production readiness remain blocked.
- Safe next action: Terra reviews the sanitized observation evidence. Do not
  rerun worker/EMPTY_TEMP or bind selectors autonomously; preserve the private
  artifacts and report the zero-candidate blocker for an explicitly reviewed
  next step.

## Stage 2 collector repair (2026-09-05)

- Status: **AWAITING TERRA FOCUSED REREVIEW**. The original worker and
  EMPTY_TEMP observations remain zero-candidate and **NOT PROVEN**; no live
  rerun has occurred after this repair.
- Fix: post-navigation host and response status are checked without exporting
  URL/body data; main-frame structural counts settle every 250 ms for a finite
  20-second budget inside the 120-second observation bound; and collection
  counters (`MAIN_FRAME`, broad/evaluated/accepted/rejected/element_errors,
  settle_attempts, outcome) surface `NO_MATCHES_AFTER_SETTLE`,
  `METADATA_REJECTED`, and `ELEMENT_COLLECTION_FAILED` instead of hiding
  errors behind `COMPLETED`.
- Revised upcoming artifact schema: `zalo-auth-readiness-diagnostic-v2`,
  adding only the sanitized `collection` envelope. The original private v1
  artifacts remain preserved outside Git and are not binding evidence.
- Evidence: focused collector RED `Ran 3`, exit `1`; diagnostic GREEN
  `Ran 16`, `OK`; Ruff `All checks passed!`; mypy over five production
  modules `Success: no issues found in 5 source files`; compile exit `0`.
- Safe next action: Terra focused rereviews the collector and schema, then
  authorizes the sequential worker/EMPTY_TEMP re-observation. Do not bind
  selectors or invoke live again before that review.

## Stage 2 collector-v2 re-observation — worker stopped (2026-09-05)

- Status: **WORKER BROWSER ERROR; EMPTY_TEMP NOT RUN**. Terra accepted the
  collector-v2 repair for one fresh bounded worker observation. The worker
  process exited `1` in approximately `22.325s`; observed at
  `2026-09-05T17:18:11.182656+07:00`; schema
  `zalo-auth-readiness-diagnostic-v2`; artifact filename
  `zalo-auth-worker-1a75b2a3afb748a5ba3dc750411d9445.json` is retained in the
  private artifact root outside Git.
- Sanitized result: `BROWSER_ERROR` / `ELEMENT_COLLECTION_FAILED`, page set
  `DEFAULT_PAGE_ACCEPTED`, navigation `1`, candidate count `0`.
  Collection counters: scope `MAIN_FRAME`, broad `36`, evaluated `36`,
  accepted `0`, rejected `0`, element errors `36`, settle attempts `5`,
  outcome `ELEMENT_COLLECTION_FAILED`.
- Cleanup was complete and safe to inspect: `CLEANUP_OK`, incomplete
  `false`, invariant `true`; worker lock absent after cleanup; owned
  temporary-profile directories and lock files under the private artifact
  root were both `0`. Since the worker had a genuine browser error, the
  fail-closed sequence did not invoke EMPTY_TEMP. No raw URL, selector,
  text, DOM/body, cookie, account, QR, screenshot, or external write was
  performed or persisted.
- Safe next action: Terra reviews this v2 worker evidence and the surfaced
  collection failure. Do not bind selectors or run EMPTY_TEMP before that
review authorizes the next bounded action.

## Stage 2 corrected v2 re-observation (2026-09-05)

- Status: **OBSERVATION COMPLETE; AWAITING TERRA EVIDENCE REVIEW**. After
  Terra accepted the corrected collector, one fresh worker then EMPTY_TEMP
  sequence ran with no source changes before execution. Worker artifact
  filename: `zalo-auth-worker-c80c632caead4c39a5ad10c684c1a6d5.json`;
  EMPTY_TEMP artifact filename:
  `zalo-auth-empty-temp-83b2e06ac83f4d5187797169fe14d27e.json`; both are
  schema-v2 private artifacts outside Git.
- Worker: exit `0`, approximately `5.105s`, observed
  `2026-09-05T17:29:19.143491+07:00`; `COMPLETED` /
  `STRUCTURAL_METADATA_COLLECTED`; `DEFAULT_PAGE_ACCEPTED`; navigation `1`;
  candidates `7`; collection `MAIN_FRAME`, broad/evaluated/accepted
  `7/7/7`, rejected `0`, element errors `0`, settle attempts `3`, outcome
  `METADATA_COLLECTED`. Safe tags: `meta=4`, `link=1`, `style=1`, `div=1`;
  one visible; no button/input/textarea/select candidate.
- Worker cleanup: `CLEANUP_OK`, incomplete `false`, invariant `true`; worker
  lock absent afterward; private-root temporary-profile and lock counts
  `0/0`. This satisfied the gate for EMPTY_TEMP.
- EMPTY_TEMP: exit `0`, approximately `3.063s`, observed
  `2026-09-05T17:29:53.972753+07:00`; `COMPLETED` /
  `STRUCTURAL_METADATA_COLLECTED`; `DEFAULT_PAGE_ACCEPTED`; navigation `1`;
  candidates `6`; collection `MAIN_FRAME`, broad/evaluated/accepted
  `6/6/6`, rejected `0`, element errors `0`, settle attempts `2`, outcome
  `METADATA_COLLECTED`. Safe tags: `meta=4`, `link=1`, `div=1`; one visible;
  no button/input/textarea/select candidate.
- EMPTY_TEMP cleanup: `CLEANUP_OK`, incomplete `false`, invariant `true`;
  remaining owned temporary-profile directories and lock files `0/0`.
  No login/logout, QR/OTP, search/chat/scan, screenshot, body/text/content,
  cookie/account, database, BCNB, Form, Zalo message, or external write was
  performed or persisted.
- Safe next action: Terra reviews the private v2 artifacts and bounded safe
  summary. Structural collection is not authenticated-readiness proof; do
  not bind production selectors or adapter behavior until that review.

## Stage 2 collector root-cause repair (2026-09-05)

- Status: **REPAIRED; CORRECTED LIVE OBSERVATION RECORDED**. Before Terra's
  acceptance, the prior v2 worker observation was
  `BROWSER_ERROR` / `ELEMENT_COLLECTION_FAILED` with `36` broad/evaluated
  elements and `36` element errors; cleanup was proven complete and
  EMPTY_TEMP was not run. The subsequent corrected worker/EMPTY_TEMP results
  are recorded above.
- Root cause: an owned headless Playwright synthetic page reproduced the
  failure without external navigation or network. `is_visible` succeeded for
  all three normal elements, but `evaluate` failed for all three because the
  generated JS object literal emitted fixed hyphenated keys (`data-testid`,
  `aria-controls`, and similar) without quotes.
- Repair: quote all fixed property and attribute names via `json.dumps` in
  the production metadata expression. No new attributes, frames, body/text
  reads, or output fields were added.
- Evidence: the real Playwright regression was RED (`accepted 0 != 3`) before
  the edit and GREEN (`Ran 1`, `OK`) after it; focused collector/navigation
  tests pass (`Ran 5`, `OK`, 4.306s); diagnostic suite passes (`Ran 18`,
  `OK`, 13.971s); Ruff `All checks passed!`; mypy `Success: no issues found
  in 5 source files`.
- Safe next action: Terra reviews the corrected private v2 artifacts and
  bounded synthetic evidence. Structural collection is not authenticated
readiness proof; do not bind production selectors without that review.

## Stage 2 BODY/UI settle correction (2026-09-05)

- Status: **AWAITING TERRA REVIEW; NO LIVE RERUN**. The corrected v2 live
  artifacts remain valid structural metadata but contain only
  `meta/link/style/div` candidates and one visible node per profile; this is
  insufficient proof that usable body UI materialized or that auth selectors
  exist.
- Fix: broad discovery is now restricted to `BODY` structural elements,
  excluding head metadata/startup nodes. Settling samples repeated body
  structure plus visible generic controls (button/input/textarea/select or a
  fixed documented interactive ARIA role), and requires both stable samples
  and at least one visible interactive match. A root/id count alone cannot
  complete; deadline expiry emits `UI_NOT_MATERIALIZED` with capped
  `MAIN_FRAME` counters. No text/content/DOM dump, cookies, or extra frames
  are read.
- TDD evidence: real Playwright synthetic delayed-controls/root-only tests
  were RED (`Ran 2`, exit `1`: old collector completed too early and did not
  raise); after the fix they were GREEN (`Ran 2`, `OK`). The diagnostic suite
  passes `Ran 20`, `OK`, 19.432s; Ruff `All checks passed!`; mypy `Success:
  no issues found in 5 source files`.
- Safe next action: Terra reviews this narrow settle correction with the
  preserved private v2 artifacts. Do not rerun Zalo or bind production
  selectors before review; structural UI availability is not auth proof.

## Stage 2 BODY/UI gated re-observation — worker stopped (2026-09-05)

- Status: **WORKER GATE FAILED; EMPTY_TEMP NOT RUN**. One fresh worker run
  used the approved explicit binding and private artifact root, with no source
  changes before execution. Artifact filename:
  `zalo-auth-worker-973f3bcabb494e6288965da578fc80b7.json` (schema-v2).
- Result: exit `1`, approximately `6.134s`, observed
  `2026-09-05T17:42:28.880895+07:00`; `BROWSER_ERROR` /
  `METADATA_REJECTED`; `DEFAULT_PAGE_ACCEPTED`; navigation `1`; candidates
  `16`. Collection: `MAIN_FRAME`, broad/evaluated `27/27`, accepted `16`,
  rejected `11`, element errors `0`, visible interactive matches `2`,
  settle attempts `5`, outcome `METADATA_REJECTED`. Safe accepted tags:
  `div=11`, `button=1`, `nav=1`, `input=1`, `span=1`, `canvas=1`; visible
  `14`, hidden `2`.
- Cleanup: `CLEANUP_OK`, incomplete `false`, invariant `true`; worker lock
  absent; private-root temporary-profile and lock counts `0/0`. Strict gate
  failure prevented EMPTY_TEMP. No raw selectors/attributes, profile data,
  account data, text/content, QR, cookies, or external writes were persisted.
- Safe next action: Terra reviews the private artifact and the `11` rejected
metadata items before any further run or selector binding. The `2` visible
interactive matches are structural evidence only, not auth proof.

## Stage 2 repaired-accounting v2 re-observation (2026-09-05)

- Status: **OBSERVATION COMPLETE; AWAITING TERRA EVIDENCE REVIEW**. After
  Terra accepted mixed accepted/rejected accounting, one fresh worker then
  EMPTY_TEMP sequence ran with no source changes before execution. Private
  artifacts retained for Terra comparison:
  `zalo-auth-worker-63bee88c011b4655b3990bd615b6ff7c.json` and
  `zalo-auth-empty-temp-cbc7f26d072645d1979844b94365ac42.json` (schema-v2).
- Worker: exit `0`, approximately `6.657s`, observed
  `2026-09-05T17:53:57.470781+07:00`; `COMPLETED` /
  `STRUCTURAL_METADATA_COLLECTED`; `DEFAULT_PAGE_ACCEPTED`; navigation `1`;
  candidates `16`; collection `MAIN_FRAME`, broad/evaluated/accepted
  `27/27/16`, rejected `11`, element errors `0`, visible interactive matches
  `2`, settle attempts `5`, outcome `METADATA_COLLECTED`. Safe tags:
  `div=11`, `button=1`, `nav=1`, `input=1`, `span=1`, `canvas=1`; visible
  `14`, hidden `2`. Fifteen selector counts were `1`; one was `12`.
- Worker cleanup: `CLEANUP_OK`, incomplete `false`, invariant `true`; worker
  lock absent. This satisfied the EMPTY_TEMP gate.
- EMPTY_TEMP: exit `0`, approximately `7.632s`, observed
  `2026-09-05T17:54:35.647845+07:00`; `COMPLETED` /
  `STRUCTURAL_METADATA_COLLECTED`; `DEFAULT_PAGE_ACCEPTED`; navigation `1`;
  candidates `11`; collection `MAIN_FRAME`, broad/evaluated/accepted
  `11/11/11`, rejected `0`, element errors `0`, visible interactive matches
  `1`, settle attempts `10`, outcome `METADATA_COLLECTED`. Safe tags:
  `div=2`, `button=1`, `clippath=8`; visible `2`, hidden `9`; all selector
  counts were `1`. Cleanup `CLEANUP_OK`, incomplete `false`, invariant `true`;
  remaining owned temporary-profile/lock counts `0/0`.
- No raw ids/roles/selectors/attributes, profile/account data, text/content,
  cookies, QR, screenshots, or external writes were persisted. The private
  accepted candidate sets remain available for Terra comparison. These are
  structural observations only; generic controls and safe ids/roles do not
  prove auth or authorize production binding.
- Safe next action: Terra reviews the private pair and ambiguity signal before
  any selector binding or further live action.

## Stage 2 metadata accounting and selector-provenance assessment (2026-09-05)

- The earlier stopped worker artifact predates the accounting repair and
  remains accurate as `METADATA_REJECTED` with accepted and rejected items.
  The repair now completes mixed accepted/rejected metadata as
  `METADATA_COLLECTED`, preserving counters; zero accepted still rejects and
  any element error still fails closed. The subsequent repaired-accounting
  live sequence above exercised this behavior.
- The v2 private candidate schema retains safe `id`, `name`, `role`,
  `data-testid`, and `data-qa` values with selector fingerprint, selector
  count, tag, and visibility. Sanitization drops unsafe/identity-like values;
  the selector builder falls back to generic tag selection when no safe
  identifying value survives.
- These fields can provide partial technical selector provenance for a future
  reviewed candidate when its safe value is stable, visible, and unambiguous.
  They cannot prove auth semantics: no accessible name/label, text, page/auth
  state, or account signal is collected, and selector count is recorded but
  not required to equal one. Fingerprints are non-semantic. Terra must map
  structural evidence to the intended auth control before any production
  selector binding; no selector values are persisted in state.
- Safe next action: Terra reviews the repaired accounting and provenance
  limitation together with the stopped private artifact. Do not rerun live or
  bind production selectors before that review.

## Stage 3 local readiness integration — 2026-09-05

- Status: **IMPLEMENTED LOCALLY; AWAITING CODEX/TERRA INTEGRATED REVIEW**.
  No live browser or canary action was taken during this implementation stage.
- Production composition now constructs the code-sealed readiness adapter
  internally, with a fresh per-page 60-second poll and no first-page auth-fact
  cache. The reviewed positive conjunction is expected host + visible
  singleton body navigation + visible singleton `#contact-search-input` on the
  same page. Login/QR-specific classification remains **UNPROVEN**; no login
  state is guessed from absence of evidence.
- The separate read-only canary runner/CLI fixes the approved target descriptor,
  requires explicit effective profile/lock binding and an outside-Git artifact
  root, denies BCNB/Form/Zalo-send/production-DB/repository/job-dispatch
  capabilities, and enforces 300-second/60-scroll/1,000-message ceilings
  before materialization. It has not been run.
- Job progress now includes `phase`, `reason_code`, and
  `attempted_target_count` without migration. Zero attempted targets map to
  `Chưa quét được` plus the actual reason; partial status requires an attempted
  target.
- Isolated full-quality evidence: `CHECK_QUALITY.ps1 -Isolated` exit 0;
  468 tests passed; Ruff passed; mypy passed for 74 source files; Phase 9A,
  Phase 9B, temporary schema/active-job checks, hermetic app smoke, and
  frontend contract passed; final output `QUALITY GATE PASSED`. The temporary
  run context was removed and no real database/profile was used.
- Focused local evidence: canary/isolation `9/9`, scanner `28/28`, readiness
  `8/8`, affected Ruff and mypy passed. These are local gates, not production
  acceptance. The next safe action is Codex/Terra integrated review, then the
  separately authorized live single-target canary if accepted.

## Stage 3 repair and retained full-quality gate — 2026-09-05

- Status: **LOCAL GATE PASSED; TERRA ACCEPTED THE EXACT SINGLE-TARGET
  READ-ONLY CANARY**. No batch/rollout was accepted.
- Terra repair mapping is complete: uncertain owned cleanup retains the lock
  and blocks reuse; verified lesson absence is explicitly non-ready; isolated
  mode denies real persistent/browser entry before launch; late UI is tested
  with deterministic coordination and the actual adapter; and canary artifact
  roots/output are private, outside Git, disjoint from profile/lock paths, and
  sanitized to artifact name plus aggregates.
- Fresh command:
  `CHECK_QUALITY.ps1 -Isolated -LogPath <private-log>`.
  Start `2026-09-05T20:25:39.1413645+07:00`; end
  `2026-09-05T20:32:32.9480960+07:00`; wall duration approximately
  `413.807s`; the completed tool-session output recorded regression `476`
  tests in `333.835s`, `OK`; wrapper exit `0`. Ruff, mypy, coverage, Phase
  9A, Phases 3–9, Phase 9B, dependency, temporary schema/active-job,
  hermetic app smoke, frontend, and compile-all checks passed. Final marker:
  `QUALITY GATE PASSED`.
- Retained private transcript basename:
  `zalo-quality-stage3-repair-resume-20260905-01.log`. Its transcript has
  phase headings/final marker but not native unittest output; the labeled
  tool-session extract is
  `zalo-quality-stage3-repair-resume-20260905-01.tool-session.txt`. The
  isolated context was removed and no active owned gate process remains. The
  prior `468` result is historical/unretained; the earlier interrupted run is
  not a pass.
- The procedural allowlist exception remains auditable: before-copies for
  `api/zalo_routes.py` and `repositories/job_repository.py` were reconstructed
  and independently hash-matched to baseline before Root registered the exact
  two-path extension; current outside-mismatch is `0`.
- Remaining limitation: login/QR-specific classification is **UNPROVEN**.
  The local gate is complete; the next boundary is Terra's final read-only
  evidence review.

## Authorized worker canary evidence — 2026-09-05

- Status: **TERRA ACCEPTED SINGLE-TARGET READ-ONLY CANARY; NO BATCH/ROLLOUT**.
  Exactly one worker-profile canary ran; no EMPTY_TEMP follow-up, retry, or
  batch action was performed.
- CLI exit `0`; top-level result `READY` / `SUBMITTED_ON_TIME`; observed at
  `2026-09-05T13:47:45+00:00` (`20:47:45` Asia/Ho_Chi_Minh); elapsed
  `46884ms`. Descriptor matched the fixed `VT-ROB-ARMB19` W34/2026 lesson-1
  class-date-2026-08-23 contract and window `2026-08-17..2026-08-27`.
- Same-page readiness was positive: expected host reached, body navigation
  count `1` and visible, search-control count `1` and visible, contract
  `zalo-auth-readiness-v1`.
- Safe scan aggregates: messages checked/evaluated `18/15`, candidate reports
  `2`, private captures `1`, validation errors `0`, classifier
  `SUBMITTED_ON_TIME`. The sanitized scan status/evidence state were both
  `INSUFFICIENT_EVIDENCE`; the top-level explicit submitted-report decision
  remained `READY`.
- Cleanup was `CLEANUP_OK`, complete `true`, invariant `true`, lock released
  `true`, remaining pages `0`; the post-run lock check was performed and the
  lock file was absent. The private
  artifact root was outside Git and disjoint from profile/lock destinations;
  it retained one JSON result and one non-empty private capture image.
- Private basenames retained for Terra: root
  `zalo-auth-readiness-canary-20f0e9939cbb4ee99b2cadea64a40d7f`, result
  `zalo-armb19-canary-cbe3e3e47a2646df852c4a8e3fe1dd9b.json`, capture
  `report-ad38443a86.png`, and CLI log
  `zalo-auth-readiness-canary-20f0e9939cbb4ee99b2cadea64a40d7f-cli.log`.
  Absolute private paths are not stored in Git.
- Terra directly viewed the private capture and confirmed approved class/group
  identity, lesson `1`, class date `23/08/2026`, and
  `capture_incompleteness=false`.
- The sanitized scan status/evidence-state label remained
  `INSUFFICIENT_EVIDENCE` while classifier decision was
  `SUBMITTED_ON_TIME`. Terra marked this a nonblocking label-mapping defect
  for later local/hermetic follow-up, not missing canary evidence. No source
  fix or rerun is part of this closure.
- The canary performed only the authorized bounded exact-target group search and
  message/history reads. No login/logout/QR/OTP, interactive chat action or
  message send, or external write occurred. Login/QR selector provenance
  remains **UNPROVEN**. The accepted result is limited to this exact single
  target and does not authorize batch operation or rollout.

## Canary JSON label repair — 2026-09-05 22:02 Asia/Ho_Chi_Minh

- Status: **LOCAL FIX ACCEPTED BY TERRA; AWAITING CODEX FINAL MANIFEST**. This repair is
  hermetic only and does not renew, rewrite, or reinterpret the accepted
  historical ARMB19 canary artifacts.
- Root cause: `build_scan_result` emits fixed UI `status` labels and separate
  evidence-state values, while the canary sanitizer checked both against the
  classifier-decision vocabulary. The accepted top-level `READY` /
  `SUBMITTED_ON_TIME` decision was therefore accompanied by misleading
  nested `INSUFFICIENT_EVIDENCE` labels.
- Repair: `services/zalo_readonly_canary.py` now uses type-safe, field-specific
  finite allowlists for producer status and evidence state, retains the
  existing classifier decision allowlist, and falls back to fixed insufficient
  labels for unknown, malformed, or non-mapping values. No raw human text is
  exported and no positive classifier condition was widened.
- Fresh TDD evidence: RED `Ran 17`, exit `1` with five expected label/fallback
  failures; GREEN `Ran 17`, exit `0`, `OK`. Focused canary/evidence tests
  `Ran 56`, exit `0`, `OK`; scoped Ruff and no-incremental mypy both exit `0`.
- Isolated full gate was run once on 2026-09-05 22:04–22:08 with
  `CHECK_QUALITY.ps1 -Isolated`; exit `0`, regression `Ran 481 tests in
  258.133s`, `OK`, final marker `QUALITY GATE PASSED`, plus Ruff, mypy (74
  source files), coverage, Phase 3–9B, schema, hermetic app smoke, frontend,
  and compile-all checks. Complete outer stdout/stderr and exit metadata are
  retained outside Git; the script transcript is separate and not treated as
  complete output. Exact commands, counts, exits, and private log paths are
  recorded in `docs/ZALO_CANARY_JSON_LABEL_REPAIR_REPORT.md`.
- Fresh Terra review: **ACCEPTED**, with no P0/P1/P2 findings. Terra's fresh
  `F:\FINAL_PROJECT\zaloAutomation\venv\Scripts\python.exe -m unittest tests.test_zalo_readonly_canary -v`
  exited `0` (`Ran 17`, `OK`); scoped Ruff and no-incremental mypy each exited
  `0`. Review confirmed field-specific finite allowlists preserve safe
  producer `status`/`evidence_state`, while classifier decisions remain
  separate, unchanged, and fail-closed.
- Safe next action: Codex captures the final manifest. This task did not rerun
  live or change the prior historically accepted exact single-target ARMB19
  canary; it claims no new live, batch, or rollout acceptance.

## GA38 read-only canary extension — 2026-09-05 23:31 Asia/Ho_Chi_Minh

- Status: **LOCAL ACCEPTED BY TERRA; AWAITING CODEX FINAL MANIFEST**. Terra’s
  fresh local/hermetic review found no P0/P1/P2 findings. This remains bounded
  local/hermetic evidence only; no GA38 live readiness, batch authorization, or
  rollout is claimed.
- Owner-approved finite target: `VT-C4K-GA38`, group
  `MindX- Lập trình Game VT-C4K-GA38 CN (18H-20H)`, ISO year/week `2026/35`,
  lesson `3`, class date `2026-08-30`, and inclusive history window
  `2026-08-24..2026-09-05`. The BCNB deadline context is
  `2026-09-01T20:00:00+07:00`; it does not force a positive classifier result.
- The extension uses a finite code-sealed GA38 factory and descriptor, routes
  every target-specific field through validated descriptor state, emits a
  separate phase/schema/worker/artifact namespace, and rejects tampered or
  unknown descriptor state before browser entry. The new CLI exposes no generic
  target or descriptor option. Existing bounds remain 300 seconds, 60 scrolls,
  and 1,000 messages; existing cleanup, ownership, and write-denial contracts
  remain active.
- TDD evidence: initial RED at private artifact
  `red-ga38-20260905-2305/focused-red.log` exited `1` with `Ran 8 tests` and
  `7` expected factory/CLI failures; `tamper-red.log` separately exited `1`
  with one expected missing-factory failure. After routing code but before the
  verified-absence envelope hunk, the decisive focused absence failure was
  `CLASSIFIER_INSUFFICIENT_EVIDENCE` instead of the required
  `LESSON_REPORT_ABSENT` reason, with status remaining
  `INSUFFICIENT_EVIDENCE`.
- Focused GREEN: `python -m unittest
  tests.test_zalo_ga38_readonly_canary tests.test_zalo_readonly_canary -v`
  exited `0` (`Ran 26 tests`, `OK`); synthetic scan/evidence suites exited `0`
  (`Ran 39 tests`, `OK`). Scoped Ruff and no-incremental mypy on the two
  affected production files each exited `0`.
- The required isolated full gate ran exactly once after GREEN via
  `CHECK_QUALITY.ps1 -Isolated`: exit `0`, `Ran 490 tests in 303.023s`, `OK`,
  final marker `QUALITY GATE PASSED`; Ruff, mypy (74 files), coverage, Phase
  9A, Phases 3–9, Phase 9B, dependency, isolated schema/active-job,
  hermetic app smoke, frontend, and compile-all checks passed. Wrapper timing
  was `2026-09-05T23:23:10.8894873+07:00` to
  `2026-09-05T23:28:59.0073595+07:00` (`348.1144587s`). Complete outer
  stdout/stderr, transcript, and metadata are retained under the private
  baseline directory `full-quality-20260905-final/`.
- The shared verified-absence mapping is intentionally non-positive: it changes
  only the reason from `CLASSIFIER_INSUFFICIENT_EVIDENCE` to
  `LESSON_REPORT_ABSENT` for the producer envelope
  `NOT_FOUND + NOT_FOUND_VERIFIED`; it does not widen readiness or alter
  timestamp handling. Terra must review this shared reason refinement. ARMB19
  CLI bytes are unchanged (SHA-256
  `3D8A4EF1E6BE75F3D811A8E9E33EF7922E0D3E0B16BA693E2EB27163E3416753`).
- Evidence report: `docs/ZALO_GA38_CANARY_EXTENSION_REPORT.md`. Safe next
  action: Root performs final immutable-manifest review; no live action is
  authorized by this state.

## QQ AI Workflow v9 adoption (2026-09-07)

- Status: **AWAITING CODEX REVIEW**. This migration is limited to the
  approved workflow/configuration/documentation allowlist on branch
  `chore/adopt-qq-ai-workflow-v9`, base/head
  `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`. The supplied immutable
  baseline `qq-v9-f28739cd42b04203809c93f23d159dc6` has 172 entries and
  SHA-256
  `0457ead737d41d09234190db136702ae7b53030adad7bc34841d1fbc754f08e6`.
  Pre-existing dirty and untracked paths remain unchanged and Owner-owned.
- QQ v9 canonical artifacts are active under `.ai-workflow/`; project profile
  `PROJECT_PROFILE.json` is configured for FastAPI/Python/Playwright/Gemini/
  SQLite, routing is `MANUAL`, and risk rules independently cover workflow,
  CI, authentication/secrets, database/migrations, deployment, external
  services, user data, production writes, and destructive operations.
  `AGENT_WORKFLOW.md` and `.codex/**` remain retained historical/context
  artifacts and are explicitly superseded by v9 in `AGENTS.md`.
- The frozen local manifest is
  `.ai-workflow/tasks/qq-v9-migration/verification-manifest.json` with lock
  digest `sha256:ba9ff744ed9ae49e86eb62fcfaafe4cfe6163bf78c671f664de80188d204bb1b`.
  Its criteria freeze canonical tests, profile/risk validation, and the
  isolated project quality gate. It is not an external Controller authority.
- Luna pre-audit preservation snapshot has 207 entries, task-owned delta 39,
  outside missing/new/status/hash mismatches `0/0/0/0`, and
  `OUT_OF_SCOPE_UNCHANGED=PASS`; Root must recapture independently before
  Terra after any further bookkeeping.
- Fresh evidence: `npm test` exit 0 with 23/23 canonical tests; profile/risk
  validation exit 0 (`PROFILE_RISK_VALID`); `node scripts/run-pilots.mjs
  --output evidence/pilot-summary.json` exit 0 with GREEN/YELLOW/RED all
  passing, `routing_automation_present=false`, and
  `adoption_decision=PENDING_INDEPENDENT_REVIEW`. `node
  scripts/owner-status.mjs .ai-workflow/tasks/qq-v9-migration/task.json`
  exited 0 and correctly asks the Owner for no technical action.
- Fresh isolated project gate
  `.\\CHECK_QUALITY.ps1 -Isolated` exited 0: Ruff and mypy passed
  (74 source files), regression suite `490` tests in `180.717s`, all
  Phase 3–9B, dependency, schema/active-job, hermetic app smoke, frontend,
  and compile gates passed; final marker `QUALITY GATE PASSED`. The gate
  created and removed only its private isolated context; no real database,
  browser profile, `.env`, live service, or production action was used.
- Existing product progress is preserved at the current Stage 3/GA38 local
  workflow checkpoint and is not reset to an earlier phase. Local migration
  evidence does not establish hosted, live, staging, canary, or production
  acceptance.
- Safe next action: Root captures the final marker-safe manifest comparison
  and dispatches one independent read-only Terra review. Any Controller
  binding, clean-slate attempt, hosted/live operation, deployment, database,
  browser, or production action requires its own authority and remains closed.

## QQ AI Workflow v9 — Technical Operator activation (2026-09-07)

- Owner đã ủy quyền một vòng Technical Operator duy nhất để đóng blocker P1 của
  Terra về committed candidate và external Controller. Phạm vi vẫn chỉ là các
  artifact v9 đã được review; product runtime, database, `.env`, browser,
  external/live service, deployment và production không thuộc ủy quyền.
- Baseline immutable của vòng này là
  `qq-v9-controller-8378b9cb4c554fd18a755554a4f72e6e` (207 entries; SHA-256
  `b30866ca380b6841a3e7f2a05bb35f48bc5669d392b4bdb6df18f0853b14a114`). Chỉ
  stage exact task allowlist; mọi pre-existing path ngoài allowlist phải giữ
  nguyên status và marker/hash.
- Frozen manifest/lock và scope revision không đổi. Controller authoritative
  sẽ ở ngoài mọi candidate checkout, giữ task `TASK-QQ-V9-MIGRATION`, base
  `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`, lock digest
  `sha256:ba9ff744ed9ae49e86eb62fcfaafe4cfe6163bf78c671f664de80188d204bb1b`
  và routing `MANUAL`.
- Sau commit, không sửa repository files nữa. Technical Operator sẽ tạo clean
  detached worktree từ exact commit mới, bind Controller, chạy canonical
  `inspect-risk.mjs` và `verify-task.mjs`, lưu evidence ngoài Git, rồi báo
  candidate head/controller digest trong execution packet. Hosted/live/
  production acceptance vẫn chưa được thiết lập.
