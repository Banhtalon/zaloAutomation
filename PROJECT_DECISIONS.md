# PROJECT DECISIONS — Zalo Automation

> Nhật ký quyết định lâu dài. Append quyết định mới; không sửa lịch sử để làm
> biến mất bối cảnh cũ. Nếu thay thế quyết định, ghi entry mới và liên kết tới
> quyết định bị supersede.

## Mẫu quyết định

```md
## DEC-NNN — Tiêu đề

- Date:
- Status: Proposed | Accepted | Superseded | Rejected
- Context:
- Decision:
- Consequences:
- Evidence:
- Supersedes:
```

## DEC-001 — Dùng tài liệu trong repo làm durable project memory

- Date: 2026-08-27
- Status: Accepted
- Context: Trạng thái dự án đang phân tán giữa summary, completion plan, version
  history và nhiều phase/report; số liệu trong các file có thời điểm khác nhau.
- Decision: Dùng `AGENTS.md` làm chỉ dẫn khởi động; `PROJECT_SUMMARY.md` làm
  specification tổng quan; `PROJECT_STATE.md` làm snapshot hiện tại;
  `PROJECT_DECISIONS.md` làm nhật ký quyết định; `TASK_TEMPLATE.md` làm khuôn
  task. `PROJECT_COMPLETION_PLAN.md` tiếp tục là roadmap/evidence lịch sử.
- Consequences: Agent phải đọc bộ file này khi bắt đầu, nhưng vẫn phải xác minh
  source và bằng chứng hiện tại. Không tạo thêm file có vai trò trùng lặp nếu
  chưa có lý do rõ ràng.
- Evidence: Kiểm kê repository ngày 2026-08-27 không có `AGENTS.md`,
  `PROJECT_STATE.md`, `PROJECT_DECISIONS.md` hoặc `TASK_TEMPLATE.md`.
- Supersedes: Không.

## DEC-002 — Bằng chứng hiện tại có ưu tiên hơn trí nhớ và tài liệu tóm tắt

- Date: 2026-08-27
- Status: Accepted
- Context: Test count, coverage, config, database và runtime state thay đổi theo
  checkout/thời điểm; test xanh đơn lẻ có thể không chứng minh contract nguy
  hiểm.
- Decision: Kết luận phải dựa trên source, exact command/exit result, Git diff,
  database/runtime gate an toàn và artifact hiện tại. `PROJECT_STATE.md` là chỉ
  mục giúp tìm bằng chứng, không phải nguồn chân lý độc lập.
- Consequences: Mọi claim dễ stale phải có ngày xác minh. Task `check` mặc định
  read-only và không tự sửa trạng thái; task implementation phải cập nhật trạng
  thái trước khi kết thúc.
- Evidence: `PROJECT_COMPLETION_PLAN.md` còn baseline 143 tests trong khi báo
  cáo 2026-08-25 ghi gate 301 tests, minh họa việc số liệu lịch sử không thể
  được dùng như trạng thái hiện tại.
- Supersedes: Không.

## DEC-003 — Tách local acceptance khỏi quyền chạy production

- Date: 2026-08-27
- Status: Accepted
- Context: Lifecycle BCNB Finished đã có bằng chứng source/local/dry-run, trong
  khi `/run-scan` là batch rộng và cấu hình có thể đang là
  `BCNB_DRY_RUN=false`.
- Decision: Flag production không phải authorization. Live mutation chỉ được
  thực hiện sau khi có exact target contract, fresh preview/evidence, owner
  approval, audit/readback và rollback. Local acceptance không được tự nâng
  thành staging, pilot hoặc production acceptance.
- Consequences: Agent phải dừng trước browser/external mutation nếu thiếu bất kỳ
  gate nào; không suy luận approval từ config.
- Evidence: `docs/BCNB_AUTO_FINISHED_IMPLEMENTATION_REPORT.md` xác định rõ phạm
  vi source/local/dry-run và không phải live canary/production.
- Supersedes: Không.

## DEC-004 — Điều phối substantive task qua Sol, Luna và Terra

- Date: 2026-08-27
- Status: Superseded
- Note: Nội dung quyết định bên dưới được giữ nguyên làm lịch sử; các phần
  xung đột không còn operative sau DEC-005.
- Context: Cần một workflow project-scoped có vai trò tách biệt giữa điều phối,
  thực thi code và review độc lập, đồng thời bảo toàn dirty worktree và ranh
  giới local/external.
- Decision: Sol dùng `gpt-5.6-sol` với reasoning `medium` để đọc context và
  viết Luna Execution Prompt; Luna dùng `gpt-5.6-luna` với `max` để thực thi
  prompt trong scope; Terra dùng `gpt-5.6-terra` với `xhigh` ở chế độ read-only
  để review diff cuối. Mọi substantive repository task đi tuần tự Sol → Luna →
  Terra → Final Execution Report. Nếu Terra FAIL, cho phép tối đa hai vòng
  repair/re-review; pure greeting/clarification ghi delegation not applicable.
- Consequences: `.codex/config.toml` và hai custom agent TOML ghim runtime;
  `AGENT_WORKFLOW.md` và `TASK_TEMPLATE.md` chuẩn hóa prompt/báo cáo. Luna và
  Terra không chạy song song vì Terra phải thấy diff cuối; exception phải được
  ghi trong final report. Task read-only vẫn giữ read-only ở Luna.
- Evidence: `.codex/config.toml`, `.codex/agents/luna-implementer.toml`,
  `.codex/agents/terra-reviewer.toml` và `AGENT_WORKFLOW.md` trong task ngày
  2026-08-27.
- Supersedes: Không.

## DEC-005 — Orchestration contract Sol low, bounded Luna/Terra routing

- Date: 2026-08-29
- Status: Accepted
- Context: DEC-004 đã đặt Sol ở reasoning `medium`, cho phép hai repair vòng,
  và mô tả chuỗi chung chưa đủ chặt cho read-only routing. Owner yêu cầu một
  contract mới có delegation thật, output compact, preservation bằng manifest,
  và ranh giới repair rõ ràng.
- Decision: Root dùng `gpt-5.6-sol` với reasoning `low`; chỉ dùng `medium` cho
  material conflict hoặc failover đã nêu. Read-only check/review spawn đúng một
  Terra thật (`gpt-5.6-terra`/`xhigh`, `fork_turns="none"`) và không spawn
  Luna. Implement/fix/build spawn Luna thật (`gpt-5.6-luna`/`max`,
  `fork_turns="none"`) rồi Terra thật tuần tự với cùng fork boundary. Root
  không lặp công việc child; Luna và Terra không spawn agent. Terra rejection
  được sửa tối đa một vòng bằng `followup_task` tới các agent hiện có; lần
  rejection thứ hai giữ blocker và dừng. Implementation prompt phải dùng
  header cố định và Luna/Terra phải trả compact schema đã chuẩn hóa.
- Consequences: `.codex/config.toml`, agent TOML, `AGENTS.md`,
  `AGENT_WORKFLOW.md` và `TASK_TEMPLATE.md` là nguồn contract hiện hành.
  Mọi task vẫn phải giữ dirty worktree, immutable baseline/final manifest,
  approval gate và local-versus-live boundary. Root final cho Owner chỉ gồm
  outcome, evidence, blockers và next action, thường dưới 500 từ.
- Evidence: Owner attachment `MULTI-MODEL ORCHESTRATION CONTRACT` ngày
  2026-08-29; task baseline `orchestration-c1f402f33c6042f4bab19c3984a239b9`.
- Supersedes: DEC-004.

## DEC-006 — Preserve persistent page ownership on fail-closed page-set abort

- Date: 2026-09-04
- Status: Accepted
- Context: A persistent-context landing observation can encounter an
  unexpected page set. Existing cleanup owns and closes tracked pages,
  context, and Playwright, so it cannot safely stop before observation when
  pre-existing-page provenance is not established.
- Decision: Use a bounded sanitized page-set classifier with only the six
  `PAGESET_*` categories and count buckets `0`, `1`, `2PLUS`. Any non-
  observer-only classification uses explicit `STOP_WITHOUT_OBSERVATION` and
  may select `PersistentBrowserSession.abort_without_observation()`. That
  path records typed `NON_OWNING_ABORT`, retains resources without page,
  context, or Playwright close, and reports cleanup as
  `CLEANUP_SKIPPED_NON_OWNING_ABORT`. It is not wired into scan, selector, or
  authentication behavior; normal owned cleanup is unchanged unless the
  explicit abort is selected.
- Consequences: Existing page facades are never read, focused, navigated, or
  closed by classification/abort. The pre-create state and explicit observer
  ownership must be available before observer-only classification; otherwise
  the result remains fail-closed. Future live use requires a separate Owner
  decision covering lifecycle handoff and resource release.
- Evidence: Owner-approved local/hermetic task baseline
  `pageset-ownership-abort-efd3b2a371d24c12a7647db8703b5057`; report
  `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`; focused hermetic test matrix
  covers zero/one/two-plus/unavailable/observer-only/post-create-mismatch and
  exact zero close counts.
- Supersedes: Không.

## DEC-007 — Context-affine page-set snapshot provenance

- Date: 2026-09-04
- Status: Accepted
- Context: Review found that a pre-create zero classification from one
  persistent context could be paired with an observer page from another
  context and incorrectly enter `PAGESET_OBSERVER_ONLY`.
- Decision: A pre-create page-set classification carries only a private weak
  reference to its exact context. Observer-only classification requires that
  reference to resolve to the current context, plus the existing zero-to-one
  identity and count checks. Unweakrefable, absent, stale, or cross-context
  provenance fails closed as `PAGESET_POSTCREATE_MISMATCH`.
- Consequences: The sanitized category/count/action representation and abort
  zero-close guarantees are unchanged. The provenance is omitted from
  equality, `repr`, and serialization, and no strong-reference cache or raw
  context escape hatch is added. Live/runtime/selector/auth wiring remains
  closed pending separate Owner approval.
- Evidence: Terra P1 repair tests and report
  `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`; focused suite `9/9` and full
  gateway suite `56/56` green on 2026-09-04.
- Supersedes: Không.

## DEC-008 — Keep page-set provenance classifier-owned and unforgeable

- Date: 2026-09-04
- Status: Accepted
- Context: DEC-007's private weak context field was still a public
  constructor/input surface. A caller could construct, copy, mutate, or
  serialize a snapshot carrying a usable current-context reference and enter
  `PAGESET_OBSERVER_ONLY` without a verified pre-create observation.
- Decision: `PageSetClassification` remains a frozen sanitized value containing
  only category, count, and action. Pre-create zero provenance is recorded
  only by the originating `PageSetClassifier` in a bounded identity table of
  weak references to the exact snapshot and context plus expected sanitized
  fields. Observer-only classification requires that exact unmodified
  classifier-owned snapshot, the same live context, exactly one current page,
  and identity equality with the explicitly observer-owned page. Any absent,
  unweakrefable, stale/evicted, copied, serialized, mutated, forged, or
  cross-context proof fails closed as `PAGESET_POSTCREATE_MISMATCH`.
- Consequences: Public equality, `repr`, and serialization cannot carry
  context/page references or proof capabilities; classifier tracking is bounded
  and holds no strong context/page references. The existing non-owning abort
  keeps zero page/context/Playwright close guarantees, and normal owned cleanup
  remains unchanged. Live/runtime/selector/auth wiring remains closed pending
  separate Owner approval.
- Evidence: Owner-approved task baseline
  `pageset-unforgeable-provenance-5b015939745641f887e8c844c6f68fe2`; report
  `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`; focused hermetic suite `15/15`
  and full gateway suite `62/62` green on 2026-09-04.
- Supersedes: DEC-007.

## DEC-009 — Classifier-controlled page-set transition (corrective)

- Date: 2026-09-04
- Status: Accepted
- Context: The DEC-008 bounded ledger still exposed mutable instance paths
  `_record_precreate_provenance()` and `_precreate_provenance`. Underscore
  naming was not an access boundary, so callers could mint, alias, or transfer
  a forged proof.
- Decision: Do not accept a public snapshot as page-set authority. The
  originating classifier records at most one pending weak context reference
  when it verifies zero pages; observer-only classification is available only
  through that classifier's controlled
  `classify_before_create(context)` to
  `classify_after_create(context, observer_page)` sequence. The legacy
  snapshot-taking form is fail-closed and returns
  `PAGESET_POSTCREATE_MISMATCH`. Closure-owned state is weak-only and has no
  caller-accessible cache/helper capable of minting or transferring proof.
- Consequences: Public classification remains frozen and sanitized to
  category/count/action. Same-context zero-to-one identity checks and
  non-owning abort zero-close guarantees remain unchanged; copied, serialized,
  mutated, stale, cross-context, unweakrefable, absent, and forged inputs fail
  closed. Live/runtime/selector/auth wiring remains closed pending separate
  Owner approval.
- Evidence: Single repair-loop baseline
  `pageset-unforgeable-provenance-5b015939745641f887e8c844c6f68fe2`; report
  `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`; adversarial repair RED
  `18 tests / 3 failures`; fresh focused `18/18` and gateway `65/65` suites,
  Ruff, mypy, phase-4 quality, and diff checks all exit `0`.
- Supersedes: DEC-008.

## DEC-010 — Remove public page-set provenance inputs (final corrective)

- Date: 2026-09-04
- Status: Accepted
- Context: DEC-009 still described a fail-closed compatibility form that
  accepted a public snapshot. Even a fail-closed snapshot input is an
  unnecessary authority surface and does not satisfy the unforgeable
  pre-create contract.
- Decision: Accept no public snapshot or `preexisting` argument anywhere in
  the page-set classifier, module wrapper, or persistent-session entry point.
  The only valid observer-only transition is the same classifier's
  `classify_before_create(context)` followed by
  `classify_after_create(context, observer_owned_page)`. The classifier has no
  instance proof cache or minting helper; its bounded transition state is
  closure-owned, weak-only, limited to one pending context reference per live
  classifier, and consumed after the transition. Copies and transfers start
  without pending state.
- Consequences: Public results remain category/count/action only. Exact
  context and observer-page identity are required after verified zero pages;
  absent, unweakrefable, stale, cross-context, copied, serialized, mutated,
  aliased, transferred, and forged cases fail closed as
  `PAGESET_POSTCREATE_MISMATCH` and retain non-owning abort zero-close
  guarantees. Live/runtime/selector/auth/scan behavior remains closed.
- Evidence: Baseline
  `pageset-unforgeable-provenance-5b015939745641f887e8c844c6f68fe2`; repair
  RED `18 tests / 3 failures`; fresh focused `18/18`, gateway `65/65`, Ruff,
  source mypy, Phase-4 quality, and allowlist diff checks all exit `0`.
- Supersedes: DEC-009 (and the unaccepted provenance model in DEC-008).

## DEC-011 — Owner selects cooperative-caller-only page-set threat model

- Date: 2026-09-04
- Status: Accepted
- Context: The preceding page-set repairs progressively tightened context and
  transition checks, but pure-Python closure state, weak references, identity
  checks, and frozen values cannot defend against arbitrary code in the same
  Python process that introspects closures, monkey-patches objects, or
  deliberately accesses implementation internals. Treating those checks as an
  unforgeable security boundary was incorrect.
- Decision: Owner selects `COOPERATIVE_CALLER_ONLY`. The source-level
  `PageSetThreatModel` enum and typed `PAGESET_THREAT_MODEL` constant declare a
  bounded internal correctness/fail-closed contract for cooperative project
  callers only. The contract explicitly is not a defense against same-process
  introspection, monkey-patching, or malicious code accessing implementation
  internals. It is not security proof, authentication proof, identity proof,
  readiness proof, or permission to take browser action.
- Accepted behavior: finite sanitized page-set categories/count buckets,
  no-page-facade-read classification, the same-classifier cooperative
  `classify_before_create(context)` to
  `classify_after_create(context, observer_owned_page)` lifecycle transition,
  public API rejection of detached snapshots/context references as authority, and the
  existing explicit non-owning abort with zero page/context/Playwright close
  counts. Normal owned cleanup remains unchanged.
- Consequences: Tests and reports must prove only the public API contract and
  cooperative fail-closed behavior. Private/closure introspection, monkey-
  patching, or malicious same-process callers are outside the supported threat
  model and are not blessed by tests. No new anti-introspection mechanism is
  added. Live browser, selector/auth/readiness, scan, staging, pilot, and
  production acceptance remain closed.
- Evidence: Owner-approved baseline
  `pageset-cooperative-threat-model-13765449372d4ac0afc49ed54c853598` (148
  entries; manifest SHA-256
  `5008586D7DF9BEE34D7A4F13FC9A6B3B3828949B74CCAEF9F2B4901E941E7E1C`),
  `docs/ZALO_PAGESET_OWNERSHIP_ABORT_REPORT.md`, and the fresh hermetic RED/GREEN
  evidence recorded there.
- Supersedes: Conflicting unforgeable or anti-introspection claims in
  DEC-007 through DEC-010. The lifecycle/cleanup safety decision in DEC-006
  remains operative.

## DEC-012 — Probe-specific page-set-validated landing lifecycle

- Date: 2026-09-04
- Status: Accepted
- Context: The internal landing selector-proposal probe previously created a
  page before classifying the persistent context page set, used the normal
  init-script page path, entered the retrying gateway circuit, and allowed
  normal cleanup to close the context and Playwright owner. That behavior was
  not safe for a pre-existing or shared page set.
- Decision: The probe uses a dedicated worker launch path with the exact
  persistent Chromium options but at most one launch and no circuit retry.
  After launch, it must use the same cooperative `PageSetClassifier` to
  classify before page creation. Unavailable, nonzero, and post-create
  mismatch states call the existing explicit non-owning abort and return
  sanitized typed page-set diagnostics with zero navigation/action counts.
  Only a verified same-context zero-to-one observer transition may create one
  page through a no-init-script path. The transition must be verified before
  any page-facade access or navigation; the valid path allows one fixed
  landing navigation and closes only the proven observer page after navigation
  starts. The probe reports only bounded launch/navigation/action counters in
  addition to its existing sanitized landing facts, never auth/readiness or
  selector fallback, and never invokes scan/BCNB/evidence/job actions.
- Consequences: Probe cleanup never closes a pre-existing/shared context or
  stops Playwright. Generic `persistent_session`, normal browser flows, and
  the existing non-owning abort semantics remain unchanged. This is a
  local/hermetic source contract only; separate Terra preflight/review and
  separate Owner approval are mandatory before any one-time live probe or
  resource handoff/release decision.
- Evidence: `docs/ZALO_LANDING_PROBE_SAFE_LIFECYCLE_REPORT.md`; supplied
  baseline `landing-probe-safe-lifecycle-b2bff46fd37b4253ad40d75c8b1d5c65`
  (148 entries; manifest SHA-256
  `801889AA828C9C93743083C1BA40D6245FCD3D8C0873492864BC5086B108C6BF`);
  focused fake-resource RED/GREEN and scoped quality gates recorded in the
  report.
- Supersedes: Không.

## DEC-013 — Release the owned observer on every pre-navigation terminal path

- Date: 2026-09-04
- Status: Accepted
- Context: Terra's single approved repair loop found that the initial
  probe-specific lifecycle selected the existing non-owning abort after the
  same-classifier zero-to-one `PAGESET_OBSERVER_ONLY` transition had already
  proven ownership. A cancellation or page-state error during the first
  observer check therefore left one known-owned page open while preserving the
  context and Playwright owner.
- Decision: After observer ownership is proven, every terminal
  pre-navigation page-state error, cancellation, or missing-navigation path
  must release that observer page exactly once through a bounded,
  cancellation-shielded close and must not call non-owning abort. The original
  cancellation is re-raised after the bounded release. The ownership marker is
  cleared before release so the finalizer cannot duplicate the page close.
  Context close and Playwright stop remain zero; pre-ownership failures,
  unavailable/pre-existing/multiple/mismatch stops, and generic persistent
  lifecycle semantics remain unchanged.
- Consequences: A proven observer page can be closed without taking ownership
  of the context or Playwright manager. Cleanup remains observer-only and
  sanitized; no selector/auth/readiness, scan, BCNB, evidence, job, or live
  action is authorized. Separate Terra preflight/review and separate Owner
  approval remain mandatory before any one-time live probe.
- Evidence: `docs/ZALO_LANDING_PROBE_SAFE_LIFECYCLE_REPORT.md`, synthetic RED
  (`Ran 11`, two expected close-count failures) before the repair, and the
  fresh focused/full gateway and scoped quality gates recorded there.
- Supersedes: The conflicting pre-navigation zero-close wording in DEC-012;
  all other DEC-012 lifecycle controls remain operative.

## DEC-014 — Audit owned-observer cancellation before navigation

- Date: 2026-09-04
- Status: Accepted
- Context: DEC-013 requires the proven observer page to be released on
  pre-navigation cancellation, but the cancellation path did not retain a
  typed audit result or emit the existing sanitized cleanup telemetry before
  re-raising cancellation.
- Decision: After the cooperative same-classifier
  `PAGESET_OBSERVER_ONLY` transition is proven, a pre-navigation page-state
  cancellation closes that known observer page exactly once, stores a
  probe-scoped typed `CleanupResult`, and emits exactly one existing
  `browser.cleanup` telemetry event using only typed lifecycle fields and
  bounded integer counters. Audit and telemetry failures are fail-safe and
  cannot suppress, replace, or mask the original `asyncio.CancelledError`,
  which is re-raised after finalization. The persistent context and Playwright
  manager remain open; non-owning abort is not selected. Non-cancellation
  pre-navigation errors retain the existing sanitized landing-result path and
  do not receive cancellation audit telemetry.
- Consequences: The cancellation contract is observable through the existing
  `cleanup_result` property and gateway `last_cleanup_result` without exposing
  page/context, URL, selector, profile, configuration, cookie, account,
  message, content, or exception data. The probe remains local/hermetic and
  read-only; no selector/auth/readiness, scan, BCNB, evidence, job, live,
  staging, pilot, or production behavior is authorized or changed.
- Evidence: Hermetic fake-resource RED (`Ran 13`, exit `1`, three expected
  missing-audit failures) followed by focused `13/13`, internal probe `16/16`,
  and full gateway `73/73` GREEN, plus scoped Ruff, no-incremental mypy, and
  Phase-4 quality gates, all on 2026-09-04. Detailed evidence is in
  `docs/ZALO_LANDING_PROBE_SAFE_LIFECYCLE_REPORT.md`.
- Supersedes: Không; this extends DEC-013 while preserving its ownership and
  zero context/Playwright close guarantees.

## DEC-015 — Bind the landing probe to one existing worker-runtime attempt

- Date: 2026-09-04
- Status: Accepted
- Context: The internal landing probe is intentionally programmatic and
  read-only, but it must not be exposed as a generic browser API or create a
  second gateway/profile lifecycle. Replayed or concurrent local calls must
  not launch another attempt.
- Decision: Add one loopback-only `POST
  /api/internal/landing-probe/one-shot` control route. It obtains exactly the
  already-running `request.app.state.container.worker_runtime` and passes that
  object to an in-process runner. The runner uses the runtime's existing
  `browser_lock`, waits only a fixed bounded interval, consumes a
  process-lifetime latch before invoking the exact bound
  `runtime.external.browser.probe_landing_selector_proposals` once, and uses a
  fixed code-level total timeout. It never constructs gateways/apps or reads
  settings/configuration. The route rejects non-loopback, body, and query
  input, and both route and runner return only a fixed sanitized envelope.
- Consequences: Busy calls do not consume the attempt; replayed calls do not
  invoke the browser gateway; cancellation and gateway failure after
  consumption cannot be replayed. The route is a local control endpoint only;
  it does not enqueue jobs or touch scan, evidence, database, BCNB, Google
  Form, selector/auth/readiness, or production behavior. A separate Owner
  approval and preflight remain mandatory before any live invocation.
- Evidence: `docs/ZALO_LANDING_PROBE_ONE_SHOT_RUNNER_REPORT.md`; hermetic
  focused runner suite `14/14`, existing gateway suite `73/73`, API unit/error
  suite `26/26`, targeted Ruff, no-incremental mypy, and Phase-4 quality all
  passed on 2026-09-04. Supplied baseline
  `landing-probe-one-shot-runner-v2-2fbff2c95df6483f9d8785158c4d92c8` has 149
  entries and is stored outside the workspace.
- Supersedes: Không.

## DEC-016 — Correct the exact landing-probe route contract

- Date: 2026-09-04
- Status: Accepted correction
- Context: Terra review found that DEC-015's implementation registered
  `/api/internal/landing-probe/one-shot`, while the Owner contract requires
  exactly `/api/internal/zalo/landing-probe/one-shot`. The former path must not
  remain as an alias.
- Decision: Register only `POST /api/internal/zalo/landing-probe/one-shot`.
  Keep the route loopback-only, body/query-free, and bound to the existing
  `request.app.state.container.worker_runtime`; leave runner, runtime
  latch/lock, and gateway behavior unchanged.
- Consequences: The required path invokes the existing one-shot boundary;
  the former path is rejected by route registration with zero gateway calls.
  Absent clients and spoofed forwarding headers remain fail-closed. No live,
  browser, profile, network, database, configuration, scan, evidence, BCNB,
  or production action is authorized by this correction.
- Evidence: Fresh in-memory ASGI registration tests prove required-path
  success with one call, former-path `404` with zero calls, and absent-client
  plus spoofed-forwarding-header `403` with zero calls. Focused repair suite:
  `Ran 17`, exit `0`, `OK`.
- Supersedes: The route string in DEC-015 only; all other DEC-015 controls
  remain operative.

## DEC-017 — Keep auth-selector binding closed until structural provenance exists

- Date: 2026-09-04
- Status: Accepted
- Context: The current source proves only the Zalo search control
  `#contact-search-input`. Authenticated-shell and login/QR observations are
  represented by optional injected fact probes and hermetic fixtures, but no
  concrete structural selector definition has a non-circular provenance chain
  in the current tree.
- Decision: Do not bind, guess, discover, configure, or expose an auth-shell or
  login/QR selector. Preserve the existing typed fail-closed behavior and
  `UNPROVEN` candidate statuses. Callback-supplied facts, URL/text/class
  inference, screenshots, historical/manual observations, fake tests, and
  public web recollection are not production selector provenance. Any future
  binding requires a separately authorized fresh structural observation for
  both candidates, a sealed typed gateway/provider contract with bounded exact
  visibility checks, no public callback or Config/.env selector input, no
  fallback/fuzzy logic, and raw-selector-free telemetry/results.
- Consequences: No application or test source is changed by the current audit;
  `services/zalo_parallel_service.py` remains untouched. Unknown, timeout,
  detach, ambiguous, login/QR, modal, unsupported-browser, and network states
  continue to halt before target claim/search/evidence/BCNB. Local/hermetic
  evidence cannot be promoted to authentication, readiness, scan, staging,
  pilot, live, or production acceptance.
- Evidence: `docs/ZALO_AUTH_SELECTOR_PROVENANCE_CONTRACT_REPORT.md`; supplied
  baseline `auth-selector-provenance-contract-6f75a6526f2242ffa7065be5dbcd9480`
  (153 entries; manifest SHA-256
  `BA56D7705F7E560C2804738F729576DCAFACD132F15701003F519B7E2F73EE8B`);
  fresh hermetic focused suites `104/104` and source-only provenance audit
  both exited `0` on 2026-09-04.
- Supersedes: Không.

## DEC-018 — Keep structural auth observation sealed and one-shot

- Date: 2026-09-04
- Status: Accepted for implementation, **AWAITING CODEX REVIEW**
- Context: DEC-017 leaves authenticated-shell and login/QR selector
  provenance unproven. A later owner-approved observation needs fresh facts
  from the intended persistent browser context without turning the existing
  readiness or landing-probe path into a generic browser API.
- Decision: Add a separate structural-observation schema and boundary whose
  only sealed candidate IDs are `AUTHENTICATED_SHELL` and `LOGIN_OR_QR`.
  Because no concrete selector contract is justified, both candidates remain
  `UNPROVEN`; no selector, attribute, text, callback, URL inference, positive
  provenance, or readiness promotion is permitted. The observer may run only
  through the already-bound `PersistentBrowserSession` and
  `WorkerRuntime`, after a cooperative zero-page check, with one bounded
  landing navigation and typed host/search structural facts. The runtime lock
  and process-lifetime latch permit one attempt; the loopback route exposes
  only the fixed sanitized schema and rejects body/query input.
- Consequences: The capability can provide a reviewed, raw-selector-free
  provenance envelope later, but cannot bind auth, emit `AUTHENTICATED_READY`,
  enable scan/search/chat/login/QR/evidence/BCNB work, or construct another
  browser/context/profile. Page-set ambiguity, foreign pages, detach,
  timeout, cancellation, unsupported or unavailable states fail closed; only
  an observer-owned page may be closed, while context and Playwright cleanup
  remain caller-owned. The cooperative-caller-only threat model is retained.
- Evidence: `docs/ZALO_STRUCTURAL_SELECTOR_OBSERVATION_REPORT.md`; fresh
  hermetic structural RED `Ran 13` with exit `1`, followed by focused GREEN
  `15/15`, combined gateway/runner `105/105`, targeted Ruff, and
  no-incremental mypy all exiting `0` on 2026-09-04. `CHECK_QUALITY.ps1` was
  not run because database/configuration isolation is not proven for this
  task. No live browser, profile, network, database, configuration, Zalo,
  BCNB, scan, readiness, or external action occurred.
- Supersedes: Không; extends DEC-017 while preserving its no-binding and
  fail-closed requirements.

### DEC-018 repair amendment — 2026-09-05

- Owner approval: `APPROVE ACTIVE-SESSION RENDEZVOUS DESIGN`.
- Correction: the route/runner does not acquire the worker browser lock or
  invoke the observer directly. It requests the exact bound gateway. The
  normal session owner, while holding that runtime lock, opens one fixed
  short pre-yield barrier, claims at most one request, consumes the latch only
  after claim, and executes the observer before yielding. No active session,
  unavailable binding, unlocked/foreign owner, post-yield busy, duplicate,
  timeout, or cancellation-before-claim path consumes the latch. Owner
  cancellation resolves pending requests with a sanitized diagnostic while
  preserving normal cleanup.
- Candidate correction: all structural candidate records remain `UNPROVEN`.
  Page-set, navigation, detach, timeout, cleanup, rendezvous, and session
  failures are diagnostics only; they are never candidate statuses. The
  transport is therefore operable only when an admissible normal session is
  already active, and it still does not provide authenticated-shell or
  login/QR selector provenance, auth readiness, or scan authority.
- Evidence: fresh actual-`WorkerRuntime` lifecycle tests cover handoff,
  no-active/no-consume, busy, replay, cancellation before/after claim,
  zero-page enforcement, observer-only cleanup, and sanitization. Structural
  focused suites `22/22`, combined gateway/runner suites `112/112`, scoped
  Ruff, and mypy over five production modules all exited `0` on 2026-09-05.
  Full `CHECK_QUALITY.ps1` remains blocked because DB/config/browser isolation
  is not proven. Terra rereview and any later live invocation still require
  their separate gates.

## DEC-019 — Independent auth-readiness diagnostic before selector binding

- Date: 2026-09-05
- Status: Accepted for Stage 1 implementation, **AWAITING TERRA PREFLIGHT**
- Context: The current source proves only `#contact-search-input`; auth-shell
  and login/QR selectors remain unproven. The Owner-approved end-to-end plan
  requires a separate diagnostic that can observe the intended worker profile
  and a fresh empty temporary profile sequentially without logging in,
  logging out, reading QR data, or invoking application jobs.
- Decision: Add an independent CLI boundary with explicit, non-secret profile
  and lock binding. It may use the same Playwright persistent adapter, but it
  must not construct `AppContainer`, start the application, dispatch jobs,
  access the database, import Config/dotenv as a side effect, or use any
  external write capability. Discovery is bounded to structural candidate
  selector/fingerprint, role/tag, filtered attributes, count, and visibility;
  text, DOM/body content, cookies, account values, QR values, screenshots, and
  arbitrary URLs are excluded. Worker and empty-temp observations are
  sequential; a worker lock is never reclaimed and temporary ownership is
  deleted only after cleanup and lock release.
- Consequences: Stage 1 can produce private structural evidence for later
  review but cannot bind selectors, emit production READY, or authorize scan,
  search, chat, login, QR, evidence, BCNB, Form, or message actions. The two
  legacy one-shot routes are retired with HTTP 410 before runtime access. A
  diagnostic result is local/hermetic evidence only until Terra preflight and
  fresh live observations pass their own gates.
- Evidence: `docs/ZALO_E2E_AUTH_READINESS_IMPLEMENTATION_PLAN.md` and
  `docs/ZALO_AUTH_READINESS_DIAGNOSTIC_REPORT.md`; Owner-approved plan
  `C:\Users\Quang\AppData\Local\Temp\zalo-e2e-approved-plan.md`.
- Supersedes: DEC-017/DEC-018 only for the explicitly approved sequencing and
  observation ownership; their fail-closed prohibition on unproven selector
  binding remains in force.

## DEC-020 — Retire structural rendezvous and enforce diagnostic cleanup deadline

- Date: 2026-09-05
- Status: Accepted for Stage 1 repair, **AWAITING TERRA PREFLIGHT**
- Context: Terra found that the normal persistent-session path still called
  the dormant structural rendezvous and that diagnostic cleanup applied the
  15-second timeout independently to each owned resource, allowing cumulative
  cleanup to exceed the approved budget.
- Decision: Remove the rendezvous wait and runtime binding from the production
  persistent-session path. Retire the legacy structural runner so it returns
  `REQUEST_REJECTED` / `STRUCTURAL_RUNNER_RETIRED` without touching runtime,
  locks, latches, or browser state; preserve the HTTP 410 route boundary.
  Add an optional monotonic total cleanup deadline used by the diagnostic
  across owned page, context, Playwright, and manager operations while
  preserving legacy behavior when the option is omitted.
- Safety consequence: On deadline expiry, return
  `cleanup_incomplete=true` / `CLEANUP_INCOMPLETE`, fail the cleanup
  invariant, retain the profile lock, and block replay or profile reuse. Do
  not claim closure or retry when cleanup is uncertain. Worker profiles are
  never deleted; empty temporary profiles are deleted only after confirmed
  cleanup and lock release.
- Evidence: pre-edit RED showed the rendezvous spy was awaited and fake slow
  cleanup exceeded its bound (`0.1891408s` vs `0.11s`). Post-repair focused
  regressions exited `0` (`Ran 3 tests`, `OK`), and affected gateway/runner/
  diagnostic suites exited `0` (`Ran 115 tests`, `OK`, no skips). Full quality,
  Terra preflight, and live observations remain separate gates.
- Supersedes: DEC-018's active-session rendezvous implementation only; DEC-019
  independent diagnostic sequencing and all fail-closed selector-binding
  requirements remain in force.

## DEC-021 — Stage 3 sealed readiness integration and isolated quality boundary

- Date: 2026-09-05
- Status: **AWAITING CODEX/TERRA INTEGRATED REVIEW**
- Context: Terra reviewed the paired worker/empty structural observations and
  accepted one code-sealed positive conjunction, while login/QR-specific
  semantics remained unproven. The approved plan also requires a preflight-
  ready read-only single-target canary and a quality run that cannot touch the
  real database or browser profile.
- Decision: The production gateway constructs the sealed readiness adapter
  internally. Every page, retry, and newly created page receives a fresh
  60-second readiness poll; first-page facts are never reused. Readiness is
  positive only when the expected host, visible singleton body navigation, and
  visible singleton `#contact-search-input` coexist on that page. Login/QR
  markers remain unproven and are not guessed.
- Decision: Keep the canary in a separate read-only runner/CLI with the fixed
  reviewed descriptor and hard pre-materialization limits of 300 seconds, 60
  scrolls, and 1,000 messages. It accepts explicit effective profile/lock
  bindings and a private outside-Git artifact root, and denies BCNB, Form,
  Zalo-send, production-DB, repository, and job-dispatch capabilities. No
  canary has run in this local stage.
- Decision: Extend job progress with `phase`, `reason_code`, and
  `attempted_target_count` without migration. A zero-attempt run must report
  `Chưa quét được` with its actual reason; `partial` requires an attempted
  target.
- Decision: `CHECK_QUALITY.ps1 -Isolated` owns temporary configuration,
  database, evidence, logs, caches, profile, and locks, forces dry-run
  loopback behavior, restores the process environment, and removes only that
  owned context. Import-time dotenv loading is absent; default non-isolated
  behavior remains available through process configuration.
- Evidence: local focused canary/isolation `9/9`, scanner `28/28`, readiness
  `8/8`, affected Ruff and mypy passed. The isolated full gate exited 0 with
  468 tests passed, Ruff passed, mypy passed for 74 source files, Phase 9A/9B,
  temporary schema/active-job, hermetic app smoke, and frontend contract all
  passing; final output was `QUALITY GATE PASSED` and the owned temporary
  context was removed.
- Consequences: This is local implementation evidence only. Codex/Terra must
  review the integration and quality boundary before the separately authorized
  live canary. No selector values beyond the reviewed public sealed
  conjunction are persisted in repository state.
- Supersedes: None. Extends DEC-019/DEC-020 while preserving their
  fail-closed lifecycle, cleanup, and no-unproven-selector requirements.

## DEC-022 — Terra repair closure and retained isolated gate evidence

- Date: 2026-09-05
- Status: **Accepted for local handoff; AWAITING CODEX/TERRA INTEGRATED
  REVIEW**
- Context: Terra identified five bounded integration risks in the canary,
  isolated-quality boundary, deterministic late-UI test, and artifact output.
  The implementation was repaired under the existing approved plan.
- Decision: Preserve profile ownership whenever cleanup is incomplete or lock
  release is uncertain; classify verified lesson absence as non-ready; deny
  real persistent/browser entry in isolated quality mode before launch; use
  actual coordinated late-UI observation; and expose only a generated artifact
  name plus bounded aggregates after validating an outside-Git root disjoint
  from profile and lock destinations.
- Evidence: focused repair tests and affected Ruff/mypy passed; one fresh
  `CHECK_QUALITY.ps1 -Isolated` run returned exit `0` with `476` regression
  tests (`OK`, `333.835s`), all later quality phases through 9B, dependency,
  temporary schema/active-job, hermetic app smoke, frontend, and compile-all
  checks passing. The retained transcript basename is
  `zalo-quality-stage3-repair-resume-20260905-01.log`; the exact test result
  and exit were observed in the completed Codex tool-session output and are
  preserved in the separately labeled private extract
  `zalo-quality-stage3-repair-resume-20260905-01.tool-session.txt`. The
  transcript itself contains phase headings/final marker, not native test
  output. The owned isolated context was removed.
- Consequences: The historical `468`-test report and interrupted attempt are
  not treated as current full-gate proof. No live canary has run. Login/QR
  classification remains **UNPROVEN**, and Codex/Terra review is required
  before the separately authorized canary.
- Allowlist audit: edits to `api/zalo_routes.py` and
  `repositories/job_repository.py` were made before the extension was
  registered; Root reconstructed and hash-matched both before-copies to the
  immutable baseline, then registered the exact two-path extension. The
  current manifest has outside-mismatch `0`.
- Supersedes: None. Extends DEC-021 and preserves the earlier cleanup,
  no-live-write, and no-unproven-selector decisions.

## DEC-023 — Finite GA38 extension for the read-only canary

- Date: 2026-09-05
- Status: **LOCAL ACCEPTED BY TERRA; AWAITING CODEX FINAL MANIFEST**
- Context: The Owner approved extending the existing bounded ARMB19
  read-only-canary runner to the reviewed `VT-C4K-GA38` class. The extension
  must preserve the ARMB19 CLI and defaults, avoid a generic caller-controlled
  target or descriptor entrypoint, and keep local/hermetic and live acceptance
  as separate gates.
- Decision: Keep exactly two code-defined target descriptors/factories in the
  shared runner: the existing ARMB19 default and the new immutable GA38
  descriptor. The GA38 CLI selects only its internal factory and accepts no
  arbitrary target or descriptor flags. Every GA38 target field, inclusive
  message window, worker, phase, schema, and artifact prefix is derived from
  the validated descriptor; unknown or tampered descriptor state fails closed
  before browser entry. The reviewed GA38 deadline is context only and cannot
  force a classifier PASS.
- Safety consequence: The existing 300-second, 60-scroll, 1,000-message,
  15-second cleanup, profile ownership, no-reuse-after-incomplete-cleanup,
  outside-Git artifact, and write-denial contracts remain in force. Verified
  absence stays non-ready. The producer envelope
  `NOT_FOUND + NOT_FOUND_VERIFIED` may use the explicit non-positive reason
  `LESSON_REPORT_ABSENT` instead of the generic insufficient-evidence reason;
  this does not widen readiness or alter timestamp semantics and requires Terra
  review as a shared reason refinement.
- Evidence: GA38/ARMB focused tests `26/26`, synthetic scan/evidence tests
  `39/39`, scoped Ruff, affected-production no-incremental mypy, and one
  isolated full gate (`490` regression tests, exit `0`, `QUALITY GATE PASSED`)
  passed. Terra’s fresh local/hermetic review found no P0/P1/P2 findings.
  Details and private log basenames are recorded in
  `docs/ZALO_GA38_CANARY_EXTENSION_REPORT.md`.
- Consequences: This decision authorizes only local implementation evidence.
  It does not authorize a live browser run, batch scan, BCNB/Form action,
  message send, pilot, or rollout. Terra review and the final immutable
  manifest remain required.
- Supersedes: None. Extends DEC-021/DEC-022 while preserving their
  fail-closed lifecycle, cleanup, no-live-write, and no-unproven-selector
  constraints. ARMB19 CLI bytes/default target remain unchanged; this does not
  claim every ARMB19 output is byte-identical because the shared verified-
  absence reason is refined as documented above.
## DEC-024 — Adopt QQ AI Workflow v9 as the active project workflow

- Date: 2026-09-07
- Status: Accepted for local migration; **AWAITING CODEX REVIEW**
- Context: The existing project already contains a dirty, progressing
  FastAPI/Python/Playwright/Gemini/SQLite worktree and project-specific safety,
  quality, and evidence history. Owner requested adoption of the pinned QQ AI
  Workflow v9 without rebuilding product code or changing live state.
- Decision: Install the canonical v9 `.ai-workflow/` artifacts, keep routing
  `MANUAL`, create a project profile and project-specific monotonic risk
  rules, freeze deterministic migration criteria, and use hermetic GREEN,
  YELLOW, and RED/escalation pilots. Keep product requirements, roadmap,
  historical reports, and the full isolated project quality gate. Retain
  `AGENT_WORKFLOW.md` and `.codex/**` unchanged as historical/context
  artifacts, but mark them superseded by v9 in `AGENTS.md` when they conflict.
  No product source, database, `.env`, browser profile, external service,
  deployment, or live system is changed.
- Consequences: v9 is the sole active repository workflow authority for future
  task/profile/verification decisions. Local evidence is limited to source and
  hermetic acceptance; hosted, live, staging, canary, and production acceptance
  remain closed. The Owner is not assigned technical review or operation.
- Evidence: canonical commit
  `f2b6d7f7bf789837e64c0d3243f98829589a3b63`; branch
  `chore/adopt-qq-ai-workflow-v9`; baseline
  `qq-v9-f28739cd42b04203809c93f23d159dc6` (172 entries; SHA-256
  `0457ead737d41d09234190db136702ae7b53030adad7bc34841d1fbc754f08e6`);
  frozen manifest lock
  `sha256:ba9ff744ed9ae49e86eb62fcfaafe4cfe6163bf78c671f664de80188d204bb1b`;
  canonical tests `23/23`; pilot summary PASS; isolated project gate
  `490` tests, exit 0, `QUALITY GATE PASSED`.
- Supersedes: Conflicting workflow authority claims in the pre-existing
  `AGENT_WORKFLOW.md` and `.codex/**`; project/product history is not
  superseded or deleted.

## DEC-025 — Technical Operator closure for QQ v9 Controller binding

- Date: 2026-09-07
- Status: Accepted by Owner for one activation loop; **AWAITING CODEX REVIEW**
- Context: Terra's P1 finding was that the migration had not yet been bound to
  a committed clean candidate and a digest-pinned external Controller record.
  The Owner explicitly authorized the Technical Operator to close only that
  control-plane gap.
- Decision: Commit exactly the already-reviewed QQ v9 migration allowlist on
  `chore/adopt-qq-ai-workflow-v9`, preserving the existing dirty product work.
  Create the authoritative non-secret Controller snapshot outside every
  candidate checkout, pinning task `TASK-QQ-V9-MIGRATION`, scope revision `1`,
  base SHA `ec8b50e1a259b6264724f13a7f7d8c01095ed15c`, frozen manifest lock
  `sha256:ba9ff744ed9ae49e86eb62fcfaafe4cfe6163bf78c671f664de80188d204bb1b`,
  effective risk `YELLOW`, complexity `L`, and routing `MANUAL`. Bind the
  exact migration commit to a clean detached worktree and run the canonical
  risk inspection and frozen verification commands with the returned digest.
- Safety consequence: No product behavior, database, migration, `.env`,
  browser/profile, Zalo/BCNB/Form, deployment, live service, production write,
  push, merge, amend, or history rewrite is authorized. Controller evidence and
  verification output remain outside Git; repository files are not edited after
  the migration commit so its candidate head stays immutable.
- Baseline: activation baseline
  `qq-v9-controller-8378b9cb4c554fd18a755554a4f72e6e` (207 entries; SHA-256
  `b30866ca380b6841a3e7f2a05bb35f48bc5669d392b4bdb6df18f0853b14a114`). Any
  outside-allowlist status or marker/hash mismatch is a hard stop.
- Consequences: This closes only the local/source Controller-readiness gate.
  Hosted, live, staging, canary, and production acceptance remain separate;
  independent Codex/Terra review remains required.
- Supersedes: None. Extends DEC-024 without changing its scope or safety
  boundaries.

## DEC-026 — Bridge-only product implementation in LOCAL_AUTO

- Date: 2026-09-11
- Status: Accepted by Owner
- Context: v10 previously allowed Lead to code and completion readiness accepted
  a manually recorded implementer session. That did not prove the configured
  worker had been invoked and could spend Lead/Codex tokens on implementation.
- Decision: In LOCAL_AUTO, Sol Medium Lead scopes, classifies, coordinates
  deterministic gates and reports but does not edit product code or product
  tests. Gemini 3.8 Flash High is the default worker. All gpt-6-astra senior and
  elevated-review bindings use effort `low`. A bridge-generated receipt bound
  to config, frozen task, execution history and exact candidate code is required
  before implementation gates can be recognized, review can begin or workflow
  completion can be reported. A task-packet session name is insufficient.
- Budget: initial Gemini gets two successful repair rounds, followed by at most
  one Astra-low senior pass. Initial Astra gets one successful repair. Failed or
  interrupted provider calls preserve pending state and do not consume a
  successful repair round.
- Exception: Lead product implementation requires explicit Owner intent, is
  marked `OWNER_AUTHORIZED_ASSISTED_LEAD`, and is reported as ASSISTED. Workflow
  completion never grants merge or publish authority.
- Consequence: bridge source/config changes invalidate the prior activation
  receipt. The project returns to ASSISTED until a fresh real Windows worker →
  gate → independent reviewer → repair pilot, quota drill and activation receipt
  pass under the new rules.

## DEC-027 — Retire QQ AI Workflow and bridge

- Date: 2026-09-17
- Status: Accepted by Owner
- Decision: Remove the QQ AI Workflow V10 control layer, its bridge, local task
  packets, project-specific AI-agent configuration and its entry points. Keep
  the Zalo Automation application, its safety controls, product documentation
  and historical project decisions.
- Consequence: No V10 packet, bridge receipt, routing mode or AI-workflow
  instruction is active in this repository. A recovery archive is stored outside
  the repository; restoring it is a separate explicit decision.

## DEC-028 — Chuẩn hóa bộ tài liệu hiện hành

- Date: 2026-09-18
- Status: Accepted by Owner
- Context: Tài liệu dự án bị phân tán giữa nhiều phase report, canary report và
  kế hoạch cũ. `PROJECT_SUMMARY.md`, `PROJECT_STATE.md`,
  `PROJECT_COMPLETION_PLAN.md` và `VERSION.md` có số liệu mâu thuẫn với source,
  khiến AI mới có thể hiểu nhầm lịch sử là trạng thái hiện tại.
- Decision: Dùng bộ tài liệu trung tâm gồm `README.md`, `AGENTS.md`,
  `PROJECT_SUMMARY.md`, `PROJECT_STATE.md`, `PROJECT_COMPLETION_PLAN.md`,
  `PROJECT_DECISIONS.md`, `VERSION.md` và contract nghiệp vụ
  `CLASS_LIFECYCLE.md`. Kết quả task cập nhật ngắn vào `PROJECT_STATE.md`; không
  tạo report theo từng lần sửa nếu không có yêu cầu lưu bằng chứng độc lập.
  Loại bỏ phase report, kế hoạch thực thi cũ và báo cáo canary đã được thay thế.
- Consequences: AI có một đường đọc ngắn và không phải tự chọn giữa nhiều file
  có nhãn “current”. Chi tiết lịch sử bị loại khỏi Git nhưng có bản ZIP khôi
  phục bên ngoài repository. `PROJECT_DECISIONS.md` vẫn giữ nguyên lịch sử nên
  các tham chiếu cũ có thể trỏ tới artifact đã được lưu trong bản ZIP.
- Evidence: Review source ngày 2026-09-18 xác nhận 72 file Python production,
  43 route, 19 bảng, 6 loại job, 55 file test và 524 hàm test. Backup trước sửa:
  `F:\FINAL_PROJECT\zaloAutomation-docs-before-normalize-20260918-093444.zip`.
- Supersedes: Cách tổ chức report theo từng task trong DEC-001; không thay đổi
  các quyết định an toàn hoặc nghiệp vụ khác.

## DEC-029 — Canary chỉ-đọc hữu hạn cho JSB16 sau sửa timeout

- Date: 2026-09-22
- Status: Accepted for one completed read-only observation
- Context: Owner chọn `VT-JSB16` để kiểm tra trên Zalo thật sau khi Terra đã
  chấp nhận bản sửa ZALO-SEARCH-TIMEOUT-001 ở local/source. Target chính xác
  được đối chiếu từ Job 277: buổi 6 ngày 20/09/2026, không phải buổi 7 sắp tới.
- Decision: Bổ sung một descriptor bất biến và CLI riêng chỉ cho JSB16; không
  nhận mã lớp, tên nhóm hay descriptor từ dòng lệnh. Cửa sổ bằng chứng là
  12–22/09/2026. Giữ nguyên giới hạn 300 giây, 60 lần cuộn, 1.000 tin nhắn,
  profile lock, cleanup fail-closed, artifact ngoài Git và toàn bộ cấm ghi
  BCNB, Zalo, Form, database, repository và job dispatch.
- Evidence: Contract tests của ba canary đạt 16/16; `CHECK_QUALITY.ps1
  -Isolated` đạt exit 0, 540 tests, coverage 77.1%, `QUALITY GATE PASSED`.
  Canary thật chạy một lần: Zalo authenticated, cửa sổ 10 ngày được tải đủ,
  cleanup `CLEANUP_OK`, không có `TargetClosedError`, không tạo job mới. Kết
  quả target là `NOT_FOUND_VERIFIED`/`LESSON_REPORT_ABSENT`; artifact riêng tư
  ngoài repository, JSON rút gọn SHA-256
  `20F238C3058B944D2EC729F2EAC7B3BF037E5AFCB209587146ECCC881D157790`.
- Consequences: Đây là bằng chứng đúng cho một quan sát chỉ-đọc, không làm
  Job 277 thành công và không cho phép chạy batch, retry chung, mutation,
  staging, pilot hay production. JSB16 cần kiểm tra nghiệp vụ thủ công; nếu
  cần canary cho lớp khác, phải tạo descriptor khóa cứng và được Owner duyệt
  riêng.
- Supersedes: None. Extends DEC-021/DEC-022 và không thay đổi các rào chắn
  fail-closed.

## DEC-030 — Preflight fail-closed và một phiên Zalo production trên mỗi profile

- Date: 2026-09-26
- Status: Accepted for local implementation; chưa phải production acceptance.
- Context: Owner gặp Zalo mở hai tab, popup phiên khác và `TargetClosedError` khi
  retry đóng page cũ. Trước đây nút chạy chưa có một báo cáo tổng hợp để phân
  biệt điều kiện an toàn với cảnh báo cần xác nhận.
- Decision: Trước `/run-scan`, server trả báo cáo `GREEN`, `YELLOW` hoặc `RED`
  gồm kế hoạch, job đang chạy, cấu hình production, database/backup, profile
  lock và audit chưa xử lý. `RED` chặn cả UI lẫn API; `YELLOW` chỉ được chạy
  production sau xác nhận rõ ràng; không có force-run. Server kiểm tra lại lần
  cuối ngay trước khi tạo job. Production dùng một worker/trang Zalo trên mỗi
  profile; dry-run vẫn giữ số worker cấu hình. Hàng đợi target cần xem lại chỉ
  đọc, không tự kết luận hoặc ghi BCNB. Chuỗi cảnh báo HTML/PHP từ nguồn ngoài
  không được dùng làm nhãn chính trên giao diện.
- Consequences: Cấu hình `BCNB_DRY_RUN=false` chỉ cho phép hệ thống xem xét
  production; Owner vẫn phải qua preflight. Nếu cần nhiều worker production,
  phải có profile riêng và kiểm chứng độc lập. Backup preflight tạo một bản sao
  SQLite tạm để kiểm chứng rồi dọn ngay; không thay đổi database nguồn.
- Evidence: Source và test local ngày 2026-09-26; không chạy browser Zalo thật,
  không ghi BCNB và không thay đổi `.env`, database hoặc browser profile trong
  task này. Quality gate đầy đủ được ghi bổ sung trong `PROJECT_STATE.md` sau
  khi chạy xong.
- Supersedes: Không; mở rộng các rào chắn fail-closed của DEC-021/DEC-022 và
  giữ nguyên DEC-029.

## DEC-031 — Admission job Zalo nguyên tử khi có cạnh tranh

- Date: 2026-09-26
- Status: Accepted for local implementation; chưa phải production acceptance.
- Context: Preflight và lần kiểm tra lại ngay trước khi tạo job vẫn có thể bị
  hai HTTP request chạy đồng thời cùng nhìn thấy trạng thái “chưa có job”.
- Decision: `JobRepository.create_job(..., reject_if_active=True)` kiểm tra job
  `PENDING`/`RUNNING` và chèn job trong cùng giao dịch SQLite `BEGIN IMMEDIATE`.
  Route `/run-scan` dùng chốt này và trả `ACTIVE_JOB_PRESENT` nếu request khác
  đã nhận trước. Không thêm hàng đợi production hoặc nút force-run.
- Consequences: Chỉ một request được nhận trong cuộc cạnh tranh; request còn lại
  dừng an toàn trước khi mở browser hoặc ghi BCNB. Các loại job khác không đổi
  vì tùy chọn này mặc định tắt.
- Evidence: Regression `test_exclusive_job_admission_rejects_active_job_atomically`
  pass; quality gate ngày 2026-09-26 đạt exit 0, 560 test.
- Supersedes: Không; bổ sung DEC-030.
