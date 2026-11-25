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

## DEC-032 — Dashboard đặt chốt an toàn trước nút chạy

- Date: 2026-09-26
- Status: Accepted for local implementation; chưa phải production acceptance.
- Context: Owner không nên phải đoán mode đang chạy hoặc bấm nút trước khi biết
  kế hoạch và preflight đã đạt hay chưa.
- Decision: Dashboard luôn hiển thị kế hoạch, chốt preflight và workflow trước
  CTA `Bắt đầu quét`. Mode `Production`/`Dry-run` phải lấy từ server tại lần
  kiểm tra gần nhất; khi chưa có preflight, trạng thái lạ hoặc trạng thái là
  `RED`, UI khóa nút và hướng dẫn ngắn gọn. `YELLOW` chỉ hiện là cảnh báo cần
  xác nhận, không giả thành `GREEN`. Nút `Kiểm tra lại` phải kiểm tra lại đúng
  snapshot hiện tại.
- Consequences: UI chỉ làm rõ trạng thái, không thay thế chốt server-side và
  không tạo force-run. Việc kiểm tra trực quan và nghiệm thu Zalo/BCNB thật vẫn
  cần Owner thực hiện riêng.
- Evidence: Source/UI ngày 2026-09-26; `node tests/check_template_js.js`, app
  smoke và `CHECK_QUALITY.ps1 -Isolated` đạt; không đổi API, database, `.env`,
  browser profile hoặc dữ liệu thật.
- Supersedes: Không; bổ sung DEC-030 và DEC-031.

## DEC-033 — Discovery phải dừng an toàn có nguyên nhân và mode hiển thị theo job

- Date: 2026-09-26
- Status: Accepted for local implementation; chưa phải production acceptance.
- Context: Job Discovery #295 dừng ngay sau khi truy cập BCNB nhưng dispatcher
  chỉ lưu “Worker dừng an toàn do lỗi nội bộ”. Đồng thời dashboard lấy badge
  `Production` từ preflight hiện tại, dù job Discovery là read-only/dry-run.
- Decision: Các lỗi khi mở/đọc BCNB hoặc lấy nhóm Zalo trong Auto Discovery phải
  được chuyển thành `DiscoverySafetyError` và kết thúc job `FAILED` với thông
  báo tiếng Việt hướng dẫn kiểm tra kết nối, phiên đăng nhập hoặc tab trùng.
  Log chỉ giữ event và `error_type` an toàn; không lưu exception raw. Badge mode
  ưu tiên mode của job đang hiển thị, lấy từ `execution_mode` hoặc fallback
  `mutates_external`; preflight chỉ cung cấp mode mặc định khi chưa có job.
  Discovery được gắn nhãn `Dò tìm · chỉ đọc` để Owner không hiểu nhầm là thao
  tác production.
- Consequences: Lỗi thực tế vẫn fail-closed và không mở rộng quyền ghi BCNB;
  Owner có thể biết bước xử lý tiếp theo thay vì xem traceback. Việc sửa này
  không tự retry, không đổi `.env`, database, profile trình duyệt hoặc luồng
  quét production. Cần kiểm tra live riêng nếu muốn xác nhận popup/phiên thật.
- Evidence: Regression Discovery + frontend mode tests; `CHECK_QUALITY.ps1
  -Isolated` ngày 2026-09-26 đạt exit `0`, 562 test, coverage 76,9%, toàn bộ
  invariant/frontend/app smoke/compile đạt.
- Supersedes: Không; bổ sung DEC-030 và DEC-032.

## DEC-034 — Tinh gọn trang chính Zalo Auto và tách tab Thông số kỹ thuật

- Date: 2026-10-20
- Status: Accepted for local implementation; chưa phải production acceptance.
- Context: Trang chính `Zalo Auto` chứa đồng thời nhiều khối thông tin kỹ thuật
  (kế hoạch chi tiết, danh sách mã kiểm tra preflight, 3 ô stepper lặp lại,
  14 chỉ số tiến độ worker và dòng metadata cửa sổ tin nhắn trong từng dòng kết
  quả), làm bảng kết quả bị đẩy sâu và khó theo dõi nhanh.
- Decision: Giữ trang chính `Zalo Auto` ở dạng tinh gọn gồm 4 thẻ thống kê thu
  gọn, 1 thanh điều khiển kèm tóm tắt trạng thái an toàn trước nút `Bắt đầu quét`
  (vẫn giữ nguyên chốt khóa nút theo DEC-032) và bảng kết quả chính chỉ hiển thị
  trạng thái báo cáo cùng các nút xem ảnh minh chứng. Tạo tab riêng `Thông số kỹ
  thuật` (`tab-specs` / `templates/partials/specs.html`) trên Sidebar để tập
  trung toàn bộ thông số chẩn đoán: kế hoạch 2 tuần đầy đủ, quy trình 3 bước,
  danh sách kiểm tra preflight, telemetry của phiên job (`#job-meta`, reason
  code, 14 chỉ số target progress) và bảng thông số kiểm chứng tin nhắn/cửa sổ
  thời gian theo từng nhóm.
- Consequences: Người vận hành quan sát bảng báo cáo trên trang chính rõ ràng
  hơn mà không mất bất kỳ thông tin chẩn đoán kỹ thuật hay rào chắn an toàn nào.
  Không thay đổi API backend, cơ sở dữ liệu, `.env` hay luồng trình duyệt.
- Evidence: Kiểm tra `node tests/check_template_js.js`, `tests/check_phase3_quality.py`,
  `tests/check_app_smoke.py` và cổng `CHECK_QUALITY.ps1 -Isolated`.
- Supersedes: Không; bổ sung DEC-032.

## DEC-035 — Quét trực tiếp trang LMS để lọc lớp quá hạn nhận xét và đối chiếu BCNB

- Date: 2026-10-20
- Status: Accepted for local implementation; chưa phải production acceptance.
- Context: Trước đây tính năng Tick LMS yêu cầu người vận hành chụp màn hình
  danh sách lớp chưa nhận xét và tải ảnh lên để OCR. Owner yêu cầu nâng cấp để
  worker tự động truy cập trực tiếp trang `admin/class-sessions` trên MindX LMS,
  tự động đăng nhập LMS và BCNB nếu phiên bị thoát dựa trên cấu hình `.env`, và
  đối chiếu với trang `check_nhanxet.php` trên BCNB.
- Decision:
  1. Bổ sung `LMS_USERNAME`, `LMS_PASSWORD` và `LMS_URL` vào `.env`, `.env.example`
     và `googleform_autosubmit/config.py` bên cạnh `BCNB_USERNAME`, `BCNB_PASSWORD`,
     `BCNB_URL`.
  2. Trên trang LMS (`Config.LMS_URL`), vì bộ lọc URL gồm cả `MISS_ATTEND` và
     `DUE_COMMENT`, chỉ những dòng có giá trị khác rỗng tại cột `"Buổi học gần
     nhất quá hạn nhận xét"` mới được tính là lớp chưa nhận xét (`missing_classes`).
     Worker tự động duyệt qua phân trang để thu thập đầy đủ danh sách.
  3. Khi đối chiếu với bảng BCNB của tuần kiểm tra (`select_last_week_checked`),
     lấy danh sách lớp trên BCNB làm gốc, loại trừ các lớp nằm trong
     `missing_classes` của LMS và lập kế hoạch tick các lớp chưa tích còn lại.
     Các lớp trên LMS thuộc khóa/tuần khác không nằm trong bảng BCNB hiện tại
     được ghi nhận ở `unmatched_classes` nhưng không chặn xác nhận kế hoạch
     (`allow_unmatched_missing=True`).
  4. Giữ nguyên bước Xem trước kế hoạch (`lms_preview`) và xác nhận một lần qua
     `plan_digest` trước khi thực thi (`execute_verified_checkbox_action`), đồng
     thời giữ tương thích ngược với luồng phân tích qua `upload_id`.
- Consequences: Người vận hành chỉ cần bấm `Tick LMS` -> `Quét LMS & Lập kế hoạch`
  mà không phải chụp/tải ảnh thủ công, trong khi vẫn giữ đầy đủ chốt duyệt kế
  hoạch hai bước và kiểm chứng server readback trên BCNB.
- Evidence: Unit test mới trong `tests/test_lms_plan.py`, `tests/test_lms_service.py`,
  `tests/test_api_routes_unit.py`, `tests/test_job_dispatcher_unit.py`;
  `node tests/check_template_js.js` pass; `.\CHECK_QUALITY.ps1 -Isolated` exit
  `0`, `QUALITY GATE PASSED`, `567` test, coverage `77.1%`.
- Supersedes: Không; mở rộng luồng LMS sang quét trực tiếp web LMS.

## DEC-036 — Flow AI mới: Lead thực thi + Test + Sol 6.1 xhigh Review độc lập + Báo cáo Chuẩn Hàng Không

- Date: 2026-10-22
- Status: Accepted by Owner (Active operational workflow).
- Context: Owner yêu cầu cập nhật quy trình điều phối AI phù hợp với thế hệ model
  hiện tại và tối ưu hóa token tối đa khi triển khai lộ trình nâng cấp trong
  `UPGRADE_PROGRESS.md`.
- Decision:
  1. Lead vừa điều phối vừa trực tiếp thực thi từng phần công việc trong phạm vi
     `allowlist`.
  2. Hoàn tất code từng phần phải chạy kiểm thử đầy đủ (targeted unit tests,
     `node tests/check_template_js.js`, `.\CHECK_QUALITY.ps1 -Isolated`).
  3. Gọi reviewer độc lập `gpt-6.1-sol` ở mức `xhigh` (`codex exec -m gpt-6.1-sol -c model_reasoning_effort="xhigh" -s read-only -`)
     để kiểm tra diff, contract và kết quả test trước khi báo cáo hoàn thành phần đó.
  4. Mọi báo cáo gửi Owner trình bày theo phong cách "Chuẩn Hàng Không" (dạng
     điện tín/checklist kỹ thuật súc tích, tối ưu token tối đa).
- Consequences: Giữ nguyên toàn bộ rào chắn an toàn Zalo/BCNB/LMS/SQLite của dự án,
  có thêm bước kiểm định độc lập từ `gpt-6.1-sol (xhigh)` đã xác minh hoạt động
  trực tiếp trên máy (`v0.163.0`, `sandbox: read-only`) và giảm thiểu chi phí
  token giao tiếp.
- Evidence: Kiểm tra live CLI `codex exec -m gpt-6.1-sol -c model_reasoning_effort="xhigh" -s read-only -`
  ngày 2026-10-22 đạt exit `0`.
- Supersedes: Cập nhật phương thức điều phối thực thi hàng ngày trên repo local.

## DEC-037 - BCNB 2026-10: trạng thái lớp null là UNKNOWN; chọn tuần chỉ qua trigger an toàn

- Date: 2026-10-28
- Status: Accepted (Lead, Sol 6.1 xhigh `VERDICT: PASS` vòng 3); chờ Owner nghiệm thu.
- Context: BCNB đổi cấu trúc (kiểm tra live chỉ đọc 2026-10-28): cột `NX Zalo`/`NX LMS`;
  nút lọc tuần thành nhãn khoảng ngày trong form GET; `hocsinh.php?datatable=students`
  phân trang (mặc định 15 dòng, bỏ qua `length=-1`) và có dòng `class_status = null`.
- Decision:
  1. `class_status` null/rỗng được giữ lại dưới dạng `UNKNOWN`; một mã lớp có
     `UNKNOWN` không bao giờ được coi là Finished-only (không tự kết thúc lớp).
  2. Collector luôn yêu cầu `start=0&length=MAX_CLASS_STATUS_ROWS` và vẫn bắt buộc
     `len(data) == recordsTotal == recordsFiltered`; thiếu dòng là fail-closed.
  3. Mọi đường chọn tuần (nhãn mới hoặc `Xem`/`Lọc lịch` cũ) chỉ bấm control hiển thị,
     không disabled, không có `formaction`/`formmethod`, và nếu thuộc form thì form đó
     phải là form GET sở hữu cả ô tuần và năm. Sau khi lọc, `selectedWeek`/`selectedYear`
     do server render (khi có) phải khớp tuần yêu cầu.
- Consequences: Không đoán cột theo vị trí; nhận cột `NX Zalo`/`NX LMS` và `Giảng viên chính` (`resolve_instructor_column`) theo header ngữ nghĩa thay vì chỉ số cột cố định.
- Evidence: `tests/test_bcnb_dom_contract.py` (Chromium headless, BCNB tổng hợp được
  route cục bộ); kiểm tra live chỉ đọc với 0 request không phải GET.
- Supersedes: Không.

## DEC-038 — Wave 4: Kiểm định chất lượng nội dung LMS/Zalo, tự động ghi `Mô tả vấn đề (TE/TF/QC)` lên Teaching và Bảng Email Quản lý 9 cột

- Date: 2026-10-29
- Status: Accepted for local implementation (`gpt-6.1-sol xhigh` `VERDICT: PASS` cho tất cả các chặng `4.1` → `4.4`); chờ Owner nghiệm thu thực tế.
- Context: Owner yêu cầu nâng cấp toàn diện hệ thống kiểm tra báo cáo tuần (Wave 4):
  1. Kẻ bảng trực quan cho danh sách lớp lỗi trong Email gửi Quản lý (`quangda@mindx.net.vn`, `tuanpa@mindx.com.vn`).
  2. Báo cáo chính xác cả trạng thái nộp LMS (`✅ Đã nộp LMS` / `❌ Chưa nộp LMS`) và `Tình trạng đánh giá` (`⏳ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo`, ...) khớp với trang Teaching (`check_nhanxet.php`) ngay cả khi tuần đó chưa chạy job LMS riêng.
  3. Kiểm tra tự động chất lượng **Nội dung bài học (`slot.summary`)** trên LMS (chỉ ghi `"Bài 1"`, `"Buổi 1"` hoặc trống là lỗi) và lỗi giáo viên ghi sai ngày/số buổi/tên bài trên Zalo.
  4. Kiểm tra tự động chất lượng **Nhận xét từng học viên đi học (`ATTENDED`/`LATE_ARRIVED`)** trên LMS: phân biệt **Buổi Thường** (phải đủ 3 ý: `Điểm mạnh`, `Điểm cần cải thiện`, `Lời khuyên`; cảnh báo viết vài dòng sơ sài hoặc copy-paste trùng lặp) và **Buổi Checkpoint** (`Coding` Buổi 5, 9; `Robotics` Buổi 4, 8 — phải đủ 3 trụ cột `KIẾN THỨC`, `KỸ NĂNG`, `THÁI ĐỘ`).
  5. Tự động ghi nhận các vấn đề phát hiện được vào cột **`Mô tả vấn đề (TE/TF/QC)`** (`note_type="checker"`) trên trang Teaching ở chế độ `production` và đưa đầy đủ vào CSV + Email Quản lý.
- Decision:
  1. **Đọc mở rộng bảng Teaching (`services/bcnb_safety.py`, `services/zalo_discovery_service.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`)**: Dùng `resolve_extended_bcnb_columns` trích xuất `bcnb_session_id`, `khoi`, `nx_zalo_checked`, `nx_lms_checked`, `lms_deadline`, `qc_issue_note`, `instructor_note`, `evaluation_status` theo header ngữ nghĩa. Trong `ReportRepository.get_weekly_summary`, kết hợp `nx_lms_checked` và `evaluation_status` với cơ chế khóa chống gán nhầm giữa các buổi trùng mã lớp hoặc trùng `session_id`.
  2. **Kiểm định chất lượng LMS & Zalo không lộ PII (`services/lms_quality_service.py`, `services/external_gateways.py`)**: Truy vấn GraphQL `findClass` của LMS qua `LmsQualityGateway` (cô lập hoàn toàn trong `-Isolated`, dừng báo lỗi rõ ràng nếu trùng mã lớp hoặc thiếu dữ liệu phân trang). Chấm `evaluate_lms_slot_summary`, `evaluate_lms_student_comments` (chỉ báo số lượng học viên thiếu ý/sơ sài/trùng lặp, tuyệt đối không lưu tên thật học viên) và `evaluate_zalo_content_issues`.
  3. **Ghi tự động `Mô tả vấn đề (TE/TF/QC)` có kiểm chứng đọc ngược (`services/bcnb_safety.py`, `services/bcnb_actions.py`, `services/zalo_bcnb_service.py`)**: Dùng `merge_checker_issue_note` / `split_checker_issue_note` bảo toàn nguyên vẹn ghi chú thủ công của TE/TF/QC và chỉ cập nhật khối `[Auto QC: ...]` (idempotent). Ở chế độ `production`, `execute_verified_checker_note_action` gửi `POST /check_nhanxet.php` (`action=update_note`, `note_type=checker`, `csrf_token`, `session_id`), reload trang đọc ngược `verify_checker_note_persisted` đạt `VERIFIED` và ghi `BCNB_UPDATE_CHECKER_NOTE` vào `external_action_audit` (ở `dry-run` chỉ mô phỏng `DRY_RUN`).
  4. **Bảng Email Quản lý 9 cột & CSV lỗi chất lượng (`services/report_email_service.py`, `repositories/report_repository.py`, `static/js/jobs.js`)**: Mở rộng bảng Email và CSV lên 9 cột (`Mã lớp`, `Giáo viên`, `Buổi học`, `Ngày học`, `Trạng thái Zalo`, `Trạng thái LMS`, `Tình trạng đánh giá`, `Mô tả vấn đề (TE/TF/QC)`, `Thời gian kiểm chứng`); `is_failed_weekly_summary_row` bao gồm cả các lớp có `issue_description` khác rỗng; hỗ trợ đồng thời HTML Table (`html_table`), Clipboard Rich HTML (`copyFailedEmailReportToClipboard`) và bảng khung viền Unicode (`text_table`).
- Consequences: Quản lý nhận được bảng báo cáo đầy đủ trạng thái Zalo, LMS, Tình trạng đánh giá và chi tiết vấn đề chất lượng nội dung bài học/nhận xét học viên của từng giáo viên trên cả trang Teaching, Dashboard, CSV và Email mà vẫn giữ nguyên mọi chốt an toàn fail-closed và bảo vệ PII học viên.
- Evidence: Backup `.backups/pre-wave4-20261029/` (`restore_drill_ok=true`); bộ unit test `tests/test_lms_quality_service.py`, `tests/test_bcnb_actions.py`, `tests/test_bcnb_columns.py`, `tests/test_api_routes_unit.py` + `node tests/check_template_js.js` + `.\CHECK_QUALITY.ps1 -Isolated` (`616/616 OK`); Independent Review `gpt-6.1-sol (xhigh)` đạt `VERDICT: PASS`.
- Supersedes: Mở rộng Phase 3.2 và chức năng Xuất CSV lỗi & Gửi Mail Quản lý.

## DEC-039 — Wave 5 (Chặng 5.2): Rủi ro còn lại của cơ chế chống ghi đè lạc quan trên `check_nhanxet.php` (`action=update_note`)

- Date: 2026-10-30
- Status: Accepted for local implementation (Wave 5 — Chặng 5.2).
- Context: Khi cập nhật ô `Mô tả vấn đề (TE/TF/QC)` (`note_type="checker"`) qua
  `POST /check_nhanxet.php` (`services/bcnb_actions.py`), qua rà soát source và
  fixture DOM/endpoint hiện có (`tests/test_bcnb_dom_contract.py`,
  `services/bcnb_actions.py`), endpoint `action=update_note` chỉ nhận
  `csrf_token`, `action`, `session_id`, `note_type`, `note` và không có bằng
  chứng hỗ trợ tham số điều kiện phía server (ETag / `If-Match` / `old_note` /
  compare-and-swap nguyên tử).
- Decision:
  1. Thực thi kiểm tra lạc quan (optimistic pre-read) ngay trước lệnh `POST`
     (sau bước hard-reload của thao tác tick checkbox) bằng
     `read_checker_note_by_session` và kiểm tra trực tiếp trên DOM trong
     `_UPDATE_CHECKER_NOTE_JS`: nếu giá trị hiện tại trên trang khác
     `existing_note` ban đầu, dừng ngay với `FAILED_BEFORE_WRITE`
     (`CONCURRENT_NOTE_EDIT` — "QC vừa chỉnh sửa") và không gửi `POST`.
  2. Sau `POST` và hard-reload readback (`verify_checker_note_persisted`), nếu
     giá trị trên server khác `merged_note`, ghi nhận audit `UNKNOWN` kèm
     `conflict_status="CONFLICT_AFTER_WRITE"` và trả trạng thái
     `CONFLICT_AFTER_WRITE` / `OUTCOME_UNKNOWN` để QC kiểm tra lại, tuyệt đối
     không tự động ghi đè lại.
- Residual Risk: Do thiếu compare-and-swap nguyên tử phía server, vẫn còn một
  cửa sổ đua hẹp tính bằng mili-giây giữa lúc đọc kiểm tra trên trang và lúc
  server xử lý `POST`; nếu QC lưu đúng vào khoảng mili-giây đó, bước readback
  sau `POST` sẽ phát hiện sai khác hoặc bảo vệ không trọn vẹn nhưng không thể
  ngăn chặn nguyên tử trên server.
- Supersedes: Bổ sung cơ chế bảo vệ chống ghi đè và phân tách provenance ghi chú
  auto/manual cho DEC-038 mục 3.
- Bổ sung tổng kết Wave 5 (`Chặng 5.0 → 5.8`, 2026-10-31):
  1. **Quyết định D1–D6 của Owner**:
     - **D1**: Báo cáo Zalo nộp trễ hợp lệ (`SUBMITTED_LATE_VERIFIED`, `category="LATE"`) vẫn được tích `NX Zalo` trên BCNB và **không** tính là lỗi (`is_failed=False`), tách ô KPI `Nộp trễ` riêng trên giao diện.
     - **D2**: Báo cáo Zalo gửi đúng ngày học của target (`ui_sent_at.date() == target.class_date.date()`), không có 2 buổi học BCNB trùng ngày trong cùng nhóm và không ghi số buổi mâu thuẫn thì vẫn khớp (`SUBMITTED_*`) khi nội dung không ghi ngày/buổi (`ZALO_REPORT_NO_DATE`: `[Zalo] Báo cáo không ghi ngày/buổi`) hoặc ghi sai ngày (`ZALO_REPORT_WRONG_DATE`: `[Zalo] Báo cáo ghi sai ngày (dd/mm)`), kèm ghi chú QC mức `NOTE`.
     - **D3**: Mở rộng parser vòng đời lớp (`services/class_lifecycle.py`, `CLASS_LIFECYCLE.md`) hỗ trợ các track `ROB4`, `SEMI`, `PRE`, `ARM`, `PT`, `JS`, `CS`, `G`, `S` với chuỗi lên cấp `B → A → I` trong cùng track (so khớp toàn bộ token `fullmatch`, bỏ qua hậu tố `HB`); tương thích đọc/ghi với `cohort_key` cũ của `JS`/`CS` (`VT-J|S|NN`, `VT-C|S|NN`) và dừng fail-closed khi va chạm alias.
     - **D4**: Kết quả đối chiếu LMS không xác định (`[LMS] … cần kiểm tra thủ công`) dùng mã có cấu trúc `LMS_MANUAL_CHECK` mức `NOTE`, được ghi lên cột `Mô tả vấn đề (TE/TF/QC)` của đúng dòng mà không tính là lỗi trong email; nếu LMS lỗi toàn hệ thống (`is_lms_fetch_unavailable`) thì tạm ngưng ghi note `LMS_MANUAL_CHECK` hàng loạt, bảo toàn các issue `[LMS]` mức `FAIL` cũ đã verified và bật cờ `lms_fetch_unavailable=True`.
     - **D5**: Khi bật `ZALO_AI_ASSIST_CLASSIFIER=true` (mặc định `false` và cô lập trong gate), tin nhắn ứng viên được gửi nguyên văn tới Gemini để chấm phụ, nhưng tuyệt đối không ghi nguyên văn tin nhắn vào log, `classification_logs` hay kết quả job ngoài các trường chuẩn hiện có.
     - **D6**: Xóa thân luồng quét Zalo tuần tự chết trong `services/zalo_service.py` (chuyển hoàn toàn sang `run_and_finalize_parallel_zalo_batch` và chuyển các cổng invariant sang kiểm tra implementation song song) và xóa phần CSV builder chết phía JS (`static/js/jobs.js`).
  2. **Mức độ issue `FAIL` / `NOTE` (`services/report_status.py`)**:
     - Mọi issue QC mang cấu trúc `{code, severity, text}` (hoặc tiền tố chuẩn hóa `[NOTE:CODE]`/`[FAIL:CODE]`): mức `NOTE` gồm đúng 2 nhóm `LMS_MANUAL_CHECK` (D4) và `ZALO_REPORT_NO_DATE` / `ZALO_REPORT_WRONG_DATE` (D2); các issue chất lượng khác mang mức `FAIL` (kèm một bộ chuyển đổi tương thích ngược duy nhất `_adapt_legacy_issue`).
  3. **Taxonomy `category` và `is_failed` một nguồn sự thật (`services/report_status.py`)**:
     - `categorize_row(row)` trả một trong 6 giá trị `DONE | LATE | MISSING | REVIEW | EXEMPT | NOT_SCANNED`; `is_failed(row)` chỉ trả `True` khi `category ∈ {MISSING, REVIEW}`, trạng thái thực thi ledger lỗi, LMS xác nhận chưa nộp/chưa đánh giá, hoặc có ít nhất một issue mức `FAIL`. Giao diện (`static/js/jobs.js`), weekly summary (`repositories/report_repository.py`), job API (`api/job_routes.py`) và email (`services/report_email_service.py`) đều đọc từ nguồn duy nhất này.
  4. **Giới hạn của cơ chế bảo vệ ghi chú `Mô tả vấn đề (TE/TF/QC)`**:
     - Endpoint `POST /check_nhanxet.php` (`action=update_note`) trên BCNB không hỗ trợ compare-and-swap nguyên tử phía server; hệ thống kết hợp đọc kiểm tra lạc quan ngay trước `POST` (`CONCURRENT_NOTE_EDIT`), đối chiếu provenance 5 điều kiện từ `external_action_audit` (`preserve_bcnb_note_text`) và đọc ngược sau hard reload (`CONFLICT_AFTER_WRITE`).
  5. **Chọn slot LMS theo ngày học và số buổi (`select_matching_lms_slot`)**:
     - Truy vấn GraphQL lấy `_id`, `index`, `date`, `startTime`, `endTime`; đổi mốc thời gian slot về múi giờ `Asia/Ho_Chi_Minh` và chỉ chọn khi có đúng 1 slot không bị hủy khớp đồng thời ngày học (`slot_date_iso == target_date_iso`) và số buổi (`index + 1 == lesson_num`), bỏ hoàn toàn việc lấy theo vị trí `class_slots[lesson_num - 1]`.
  6. **Quy trình Review độc lập Wave 5**:
     - Toàn bộ các chặng `5.0 → 5.8` và diff tổng thể của Wave 5 được kiểm định độc lập bằng `codex exec -m gpt-6.1-sol -c model_reasoning_effort="high" -s read-only -` đạt `VERDICT: PASS`.

## DEC-040 — GitHub là Source of Truth; cập nhật `CHANGELOG.md`, commit & push lên GitHub sau mỗi tính năng hoặc bản sửa lỗi hoàn tất

- Date: 2026-11-04
- Status: Accepted by Owner (Standing Rule)
- Context: Owner chỉ đạo rõ: *"Ghi nhớ luôn, github sẽ là source of truth, xong tính năng hoặc fix gì xong thì update lên github. có changelog rõ"*.
- Decision:
  1. **GitHub (`origin`) là Source of Truth**: Sau khi hoàn tất bất kỳ tính năng mới hoặc bản sửa lỗi nào và đã vượt qua đầy đủ các cổng kiểm chứng (`.\CHECK_QUALITY.ps1 -Isolated` + Review độc lập `gpt-6.1-sol` đạt `VERDICT: PASS`), Lead chủ động cập nhật `CHANGELOG.md` (và `PROJECT_STATE.md` / `VERSION.md` khi phù hợp) với nội dung thay đổi rõ ràng.
  2. **Commit & Push an toàn**: Chỉ stage các file mã nguồn, test và tài liệu thuộc phạm vi task allowlist; tuyệt đối không stage `.env`, `database.db`, browser state, dữ liệu học viên thô hay secret; thực hiện `git commit` với thông điệp chuẩn mực và `git push` đồng bộ lên GitHub (`origin`).
- Consequences: Mã nguồn và lịch sử thay đổi (`CHANGELOG.md`) trên GitHub luôn khớp tức thời với trạng thái đã kiểm chứng mới nhất ở local mà không cần Owner phải nhắc push thủ công sau mỗi lần làm tính năng/fix.
- Supersedes: Thay thế quy tắc chặn tự động commit/push sau khi hoàn tất task trong các hướng dẫn cũ, trong khi giữ nguyên 100% rào chắn an toàn dữ liệu và không tự ý thao tác hệ thống ngoài (Zalo/BCNB/LMS/Production).

## DEC-041 — Chỉ tính lỗi giáo viên (`is_failed=True`) khi chưa nhận xét LMS hoặc chưa báo cáo Zalo; vi phạm chất lượng nội dung là nhắc nhở (`NOTE`) ghi trên Teaching phần QC

- Date: 2026-11-05
- Status: Accepted by Owner (`gpt-6.1-sol high` Round 2 `VERDICT: PASS`, session `01a1ab11-f798-79e3-aea6-0bfe99eea431`)
- Context: Owner quyết định thay đổi quy tắc tính lỗi giáo viên: *"Bây giờ thay đổi quy tắc chỉ chưa nhận xét LMS hoặc chưa báo cáo zalo mới tính lỗi. Còn lại vi phạm chất lượng nội dung sẽ là nhắc nhở không tính lỗi. Nhưng sẽ được ghi chú trên Teaching phần QC"* và nâng cấp hiển thị ảnh minh chứng Zalo theo *"Kết hợp Hướng 1 (cắt bỏ cột trái + khoảng trắng phải và tăng độ nét khi chụp ảnh mới) + Hướng 3 (hỗ trợ click phóng to trên Dashboard để đọc rõ cả ảnh cũ lẫn ảnh mới)"*.
- Decision:
  1. **Điều kiện tính lỗi duy nhất (`services/report_status.py:is_failed`, `static/js/jobs.js:resolveRowIsFailed`)**: Một buổi học chỉ có `is_failed = True` khi `categorize_row(row) ∈ {CATEGORY_MISSING, CATEGORY_REVIEW}` (chưa báo cáo Zalo hoặc báo cáo Zalo cần kiểm tra) hoặc `lms_status` bắt đầu bằng `❌`/`⚠️` (bao gồm `nx_lms_checked is False` khi chưa có `lms_status`). Không dùng chuỗi `evaluation_status` hoặc các issue chất lượng nội dung làm điều kiện kích hoạt `is_failed`.
  2. **Vi phạm chất lượng nội dung là nhắc nhở QC (`NOTE`) nhưng vẫn ghi đầy đủ lên Teaching**: Khi cả Zalo và LMS đã hoàn thành (`zalo_done and lms_done`), các vi phạm chất lượng nội dung (`[LMS] Chưa ghi nội dung bài học`, `[LMS] Nhận xét thiếu ý`, `[Zalo] ...`) hiển thị dưới dạng `Ghi chú QC: ...` màu trung tính trên Dashboard (`resolveRowIssueSeverity` trả `'NOTE'` khi `!resolveRowIsFailed(r)`), không đưa vào danh sách lớp lỗi gửi Email/CSV, nhưng vẫn được ghi tự động vào cột `Mô tả vấn đề (TE/TF/QC)` trên trang Teaching (`execute_verified_checker_note_action`).
  3. **Nâng cấp chụp và xem ảnh minh chứng (`services/external_gateways.py`, `services/zalo_scan_evidence.py`, `templates/partials/image_modal.html`, `static/js/modals.js`)**: Đặt `"device_scale_factor": 2` trong `_persistent_launch_options`, cắt khung hội thoại bằng `_resolve_zalo_chat_clip(page)` (có fallback an toàn về full-viewport screenshot), và trang bị bộ điều khiển phóng to tương tác `100% → 185% → 260%` trên modal xem ảnh Dashboard.
- Consequences: Giảm báo lỗi đỏ đối với các lớp đã hoàn thành đầy đủ nghĩa vụ nộp báo cáo Zalo và nhận xét LMS nhưng chỉ cần nhắc nhở cải thiện chất lượng nội dung trên Teaching; đồng thời giúp QC đọc rõ chữ trong ảnh minh chứng Zalo trên cả ảnh cũ lẫn ảnh mới.
- Supersedes: Cập nhật điều kiện `is_failed(row)` trong `DEC-038` mục 4 và `DEC-039` mục 3.

## DEC-042 — Kiểm định nộp Sản Phẩm Cuối Khóa (SPCK) ở Buổi 12, 13, 14 trên MindX LMS; Phương án A (`0/N` = Lỗi `FAIL`, thiếu thành phần phụ = Nhắc nhở QC `NOTE`) & Quy tắc Robotics (`ROB`)

- Date: 2026-11-10
- Status: Accepted by Owner (`gpt-6.1-sol high` Round 3 `VERDICT: PASS`, session `01a1c424-8254-74a2-94a3-28b2ecaefafb`)
- Context: Owner khảo sát trang LMS qua lớp mẫu `VT-JSB15` và yêu cầu bổ sung kiểm soát việc **Nộp sản phẩm cuối khóa (SPCK)** ở **Buổi 12, 13 và 14** với 2 quyết định nghiệp vụ đã chốt:
  1. **Phương án A (Theo quy tắc `DEC-041` hiện hành)**: Nếu chưa nộp SPCK (`0/N` học viên đi học) thì tính là **Lỗi (`is_failed = True` — báo đỏ & đưa vào Email Quản lý / CSV)**; còn nếu đã nộp SPCK (`>0/N` học viên) nhưng thiếu số lượng học viên hoặc thiếu thành phần phụ (thiếu thông tin học viên, thiếu ảnh thumbnail/ảnh chụp, thiếu file đính kèm, nhận xét sơ sài, thiếu link YouTube/Slide ở Buổi 14) thì tính là **Nhắc nhở QC (`NOTE`, `is_failed = False`)** và ghi tự động đầy đủ lên cột **`Mô tả vấn đề (TE/TF/QC)`** trên trang Teaching.
  2. **Quy tắc "File đính kèm (`attachmentUrls`)" với khối Robotics (`ROB`)**: Các lớp Robot chỉ cần ảnh và nhận xét, không bắt lỗi file đính kèm, nhưng vẫn có **nhắc nhở bên QC (`NOTE`)** ghi lên cột `Mô tả vấn đề (TE/TF/QC)`.
- Decision:
  1. **Truy vấn `findAllStudentWorks` & khử PII học viên ngay trong trình duyệt (`services/lms_quality_service.py`)**:
     - Trong `fetch_lms_class_slots_via_page`, bổ sung trường `id` của lớp và `id` của từng học viên trong `students`; với các lớp có buổi học `index ∈ {11, 12, 13}` (Buổi 12, 13, 14), gọi batch GraphQL `findAllStudentWorks(filter: { classId_in: [...] })` (kèm fallback theo từng `classId` nếu filter `_in` không được hỗ trợ).
     - Trước khi dữ liệu rời trình duyệt về Python: đối chiếu tên học viên (`fullName` hoặc cụm 2 từ tên đệm + tên chính không dấu) với `work.title` để gán cờ boolean `hasStudentInfoInTitle`, sau đó thay thế mọi xuất hiện của tên học viên trong `title`/`description`/`comment` bằng `[HS]` và ẩn danh hóa `studentId` thành `stu_1`, `stu_2`, ... (bảo đảm 100% không lộ PII học viên vào Python, SQLite hay log).
  2. **Tiêu chí kiểm định SPCK theo từng học viên đi học (`ATTENDED` / `LATE_ARRIVED`) ở Buổi 12, 13, 14 (`evaluate_lms_spck_quality`)**:
     - **Mức `FAIL` (`ISSUE_CODE_LMS_SPCK_MISSING = "LMS_SPCK_MISSING"`, `is_failed = True`)**: Khi `attended_count > 0` và `submitted_count == 0` $\rightarrow$ `[LMS] Chưa nộp SPCK ({attended}/{attended} HS)`, tô đỏ trên Dashboard, cập nhật `Tình trạng đánh giá: ❌ Chưa nộp SPCK trên LMS` (khi Zalo và nhận xét LMS đã hoàn tất), đưa vào Email Quản lý / CSV và ghi tự động lên Teaching.
     - **Mức `NOTE` (`ISSUE_CODE_LMS_SPCK_NOTE = "LMS_SPCK_NOTE"`, `is_failed = False`)** khi `submitted_count > 0`:
       - Nộp thiếu học viên (`0 < submitted_count < attended_count`): `[LMS] Thiếu nộp SPCK ({missing}/{attended} HS)`
       - Thiếu thông tin học viên trong tiêu đề (`title`): `[LMS] SPCK thiếu thông tin học viên ({count}/{submitted} HS)`
       - Thiếu ảnh thumbnail hoặc ảnh chụp sản phẩm (`thumbnail` + `imageUrl`): `[LMS] SPCK thiếu ảnh thumbnail/ảnh chụp ({count}/{submitted} HS)`
       - Thiếu file đính kèm (`attachmentUrls`, áp dụng mức `NOTE` cho cả Coding lẫn Robotics theo Quyết định 2): `[LMS] SPCK thiếu file đính kèm ({count}/{submitted} HS)`
       - Chưa có nhận xét sản phẩm (`comment` rỗng): `[LMS] SPCK chưa có nhận xét ({count}/{submitted} HS)`
       - Nhận xét sản phẩm sơ sài/chỉ chấm dấu (`_is_substantive_spck_comment`: yêu cầu $\ge 12$ từ, $\ge 6$ từ khác nhau, $\ge 50$ ký tự, $\ge 2$ mệnh đề/câu và $\ge 2$ từ khóa chuyên môn/đánh giá sản phẩm): `[LMS] Nhận xét SPCK sơ sài ({count}/{submitted} HS)`
       - **Riêng Buổi 14 bắt buộc thêm 2 mục** (Buổi 12 và 13 không bắt buộc):
         - Link YouTube (`videoUrls` / `relatedUrls` / `attachmentUrls` khớp `youtube.com/watch?v=...`, `shorts/`, `embed/`, `live/`, `playlist?list=` hoặc `youtu.be/<id>`): `[LMS] SPCK Buổi 14 thiếu link YouTube ({count}/{submitted} HS)`
         - Link hoặc File Slide thuyết trình (`relatedUrls` / `attachmentUrls` khớp `canva.com`, `docs.google.com/presentation`, `slideshare.net`, `prezi.com`, `pitch.com`, `gamma.app`, `onedrive`, hoặc đuôi `.pptx`/`.ppt`/`.pdf`/`.key`/`.odp`, hoặc chứa từ khóa `slide`/`thuyết trình`/`presentation`): `[LMS] SPCK Buổi 14 thiếu link/file Slide thuyết trình ({count}/{submitted} HS)`
  3. **Mở khóa kiểm định & ghi chú QC trên Teaching cho Buổi 14 (`services/lms_quality_service.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`, `static/js/jobs.js`)**:
     - Giữ nguyên miễn trừ báo cáo Zalo cho Buổi 14 (`CATEGORY_EXEMPT`), nhưng cho phép `inspect_and_attach_quality_issues` và `_maybe_update_row_checker_note` đánh giá LMS SPCK và đồng bộ cột `Mô tả vấn đề (TE/TF/QC)` trên Teaching cho cả Buổi 14.
- Consequences: Tự động kiểm soát chặt chẽ nghĩa vụ nộp SPCK ở Buổi 12, 13, 14 theo đúng chuẩn học vụ MindX; phân biệt chính xác lỗi chưa nộp (`0/N` $\rightarrow$ báo đỏ & gửi Email Quản lý) với nhắc nhở hoàn thiện thành phần phụ (`NOTE` $\rightarrow$ ghi tự động lên Teaching cho TE/TF/QC), đồng thời bảo vệ tuyệt đối PII học viên.
- Supersedes: Mở rộng `DEC-038` và `DEC-041` với ngoại lệ lỗi `LMS_SPCK_MISSING` (`0/N` học viên nộp SPCK ở Buổi 12, 13, 14).

## DEC-043 — SPCK theo buổi và điểm danh, nhận xét có lý do kiểm tra

- Date: 2026-11-11.
- Status: Implemented locally, kiểm chứng và đồng bộ theo PROJECT_STATE.md.
- Context: Truy vấn SPCK gộp nhiều lớp có thể bị giới hạn 20 kết quả và tạo cảnh báo thiếu bài sai. LMS khóa nhận xét và sản phẩm đối với học viên nghỉ. Kiểm tra câu chữ cứng gây cảnh báo chung chung khi giáo viên đã nhận xét.
- Decision:
  - Xác định buổi bằng lớp, ngày, số buổi và mã buổi. Truy vấn SPCK theo mã buổi. Xác minh học viên đi học còn thiếu bằng cặp mã buổi và mã học viên. Bài chờ duyệt được tính đã nộp. Lỗi đọc, dữ liệu không đầy đủ hoặc không khớp danh tính chuyển sang chờ xác minh.
  - Sĩ số cần nhận xét/SPCK gồm ATTENDED và LATE_ARRIVED. ABSENT và ABSENT_WITH_NOTICE được miễn riêng buổi đã nghỉ. Buổi đi học tiếp theo được đánh giá theo điểm danh của buổi đó. Toàn lớp nghỉ có trạng thái không áp dụng. Điểm danh trống, trùng, không xác định hoặc bị cắt cần kiểm tra thủ công.
  - Buổi 14 tiếp tục miễn nhận xét theo quyết định hiện hành, vẫn kiểm tra SPCK của học viên đi học. Nghĩa vụ báo cáo Zalo tiếp tục áp dụng theo quy tắc từng buổi.
  - Nhận xét buổi thường đánh giá ba ý trong nội dung, chấp nhận viết cùng một đoạn. Các dấu hiệu ngắn hoặc chưa nhận ra ý tạo nhắc QC có số lượng và lý do. Cơ chế nhận diện dựa trên từ/cụm từ vẫn cần người kiểm tra khi câu chữ chưa được nhận diện.
  - Dashboard và báo cáo dùng số lượng được xác minh. Ghi chú hiển thị tự do không đủ để tự kết luận chưa nộp SPCK. Dữ liệu lịch sử thiếu thông tin điểm danh tiếp tục dùng quy tắc tương thích và cần lần kiểm tra mới để có số lượng mới.
  - Đính chính ghi chú cũ trên Teaching phải có bản xem trước, bằng chứng phần ghi tự động, duyệt của Owner và đọc lại để phát hiện thay đổi đồng thời trước khi ghi.
- Consequences: Giảm cảnh báo sai và thể hiện rõ học viên được miễn. Kết quả kiểm tra mã nguồn và đối chiếu chỉ đọc được ghi riêng với nghiệm thu thao tác ghi trên hệ thống thật.
- Supersedes: Bổ sung điều kiện xác minh và miễn theo điểm danh cho DEC-041, DEC-042.

## DEC-044 — Miễn SPCK cho khóa PTB và CSB ở buổi 12, 13, 14

- Date: 2026-11-11.
- Status: Accepted by Owner, thực hiện và kiểm chứng theo PROJECT_STATE.md.
- Context: Owner xác nhận các lớp khóa PTB và CSB không cần nộp SPCK ở cả ba buổi 12, 13, 14.
- Decision: Nhận diện chính xác khóa PTB/CSB trong mã lớp. Bỏ truy vấn và kiểm định SPCK cho hai khóa này ở ba buổi, trả NOT_REQUIRED. Tiếp tục kiểm tra nhận xét LMS và báo cáo Zalo theo quy tắc từng buổi. PTA/PTI, CSA/CSI và các khóa khác tiếp tục dùng quy tắc hiện hành. Lần kiểm tra mới đưa cảnh báo SPCK tự động cũ của lớp được miễn vào lịch sử, bảo vệ ghi chú tay theo cơ chế chứng minh nguồn hiện có.
- Consequences: Không phát sinh cảnh báo thiếu SPCK hoặc thiếu thành phần sản phẩm đối với PTB/CSB ở buổi 12–14. Dữ liệu lịch sử thật cần quy trình sửa riêng đã được duyệt.
- Supersedes: Bổ sung ngoại lệ theo khóa cho DEC-042, DEC-043.

## DEC-045 — Phase 6.5: Chuẩn hóa mức lỗi QC (FAIL vs NOTE), Phễu 2 tầng AI nhận xét LMS khử PII, làm rõ SPCK/Robotics và hoãn buổi học chưa kết thúc

- Date: 2026-11-12.
- Status: Accepted by Owner, thực hiện và kiểm chứng source/local theo PROJECT_STATE.md.
- Context: Thực tế vận hành phát sinh các ca lấy nhầm số buổi từ mục giới thiệu buổi kế tiếp trên Zalo, quét sớm các buổi học chưa kết thúc (đặc biệt Buổi 14 trong tuần), gõ dấu chấm `"."` đối phó ở ô nhận xét LMS/SPCK, cảnh báo cứng nhắc do bộ đếm từ khóa 3 ý, và quy tắc SPCK/Robotics cần phân tách rõ giữa lỗi `FAIL` với nhắc nhở `NOTE` cho QC.
- Decision:
  - Trích xuất số buổi báo cáo Zalo cắt bỏ các đoạn giới thiệu buổi kế tiếp hoặc bài tập về nhà ở cuối tin nhắn (`Buổi học kế tiếp`, `Bài tập về nhà`, ...) nhưng giữ nguyên các đoạn nối tiếp của buổi học hiện tại.
  - Các buổi học ở tương lai hoặc trong ngày nhưng chưa qua giờ kết thúc dự kiến (kể cả Buổi 14 của tuần hiện tại) được hoãn kiểm tra (`⏳ Chưa tới giờ kiểm tra`, `session_deferred=True`, `is_failed=False`), bỏ qua tích BCNB và không gắn cảnh báo QC sớm, áp dụng thứ tự ưu tiên kết quả mới nhất (`newest-wins`) xuyên suốt giữa snapshot và job.
  - Quy tắc nhận xét, tóm tắt bài học và điểm danh LMS: bỏ trống hoặc chỉ điền placeholder `"."` ở nhận xét buổi học LMS hoặc nhận xét SPCK là lỗi `FAIL` (`ISSUE_CODE_LMS_SPCK_COMMENT_MISSING`, tô đỏ trong email quản lý); tóm tắt nội dung buổi học trống/`"."`/chỉ ghi `"Bài X"` giữ mức `NOTE` (`ISSUE_CODE_LMS_SUMMARY_NOTE`); học viên có trạng thái `ATTENDED`/`LATE_ARRIVED` nhưng nhận xét ghi nghỉ học (không có từ phủ định) tạo `NOTE` (`ISSUE_CODE_LMS_ATTENDANCE_CONFLICT_NOTE`).
  - Quy tắc SPCK và Robotics (`ROB`): không bắt buộc có tên học viên trong tiêu đề sản phẩm (chỉ gắn `NOTE` `[LMS] Tiêu đề SPCK chưa ghi kèm tên học viên`); có thumbnail hoặc ảnh chụp màn hình đều đạt yêu cầu hình ảnh kèm `NOTE` tách riêng cho thành phần ảnh còn thiếu; khóa Robotics (`ROB`) không bắt buộc đính kèm file code (gắn `NOTE` `[LMS] SPCK Robotics chưa có file code đính kèm`) và ở Buổi 14 kiểm tra link YouTube + hình ảnh (bỏ yêu cầu Slide).
  - Phễu 2 tầng kiểm tra nhận xét LMS (`LMS_AI_COMMENT_CHECK` + `GeminiLmsCommentReviewerGateway`): gỡ bỏ bộ đếm từ khóa 3 ý cứng nhắc, thực hiện khử PII tên học viên theo token-span không chồng lấp ở cả JavaScript trình duyệt và Python (`[HS]`, `[URL]`, `[EMAIL]`, `[PHONE]`), dùng khóa cache SHA-256, gọi Gemini bất đồng bộ không chặn vòng lặp sự kiện với giới hạn batch mỗi lượt quét và fallback tất định (`ISSUE_CODE_LMS_COMMENT_NOTE`, AI không bao giờ tự sinh lỗi `FAIL`).
- Consequences: Chặn triệt để hành vi điền `"."` đối phó ở nhận xét LMS/SPCK, loại bỏ cảnh báo sớm cho buổi học chưa kết thúc và giảm cảnh báo giả từ bộ đếm từ khóa mà vẫn đảm bảo bảo mật PII và tính tất định khi kiểm định.
- Supersedes: Cập nhật và tinh chỉnh chi tiết phân loại `FAIL`/`NOTE`, quy tắc SPCK/Robotics và cơ chế đánh giá nhận xét LMS của DEC-041, DEC-042, DEC-043.

## DEC-046 — Phân cấp mức độ hiển thị trong Email Quản lý & Dashboard: Chưa nhận xét Zalo là lỗi đỏ nặng nhất (`❌`), thiếu nộp/nhận xét SPCK là cảnh báo vàng (`⚠️`)

- Date: 2026-11-13.
- Status: Accepted by Owner, thực hiện và kiểm chứng source/local theo PROJECT_STATE.md.
- Context: Sau khi đối chiếu bảng Email Quản lý 9 cột thực tế, Owner yêu cầu làm nổi bật lỗi chưa nhận xét Zalo là lỗi nặng nhất (có tích chéo đỏ `❌` và tô đỏ đậm cả chữ), trong khi các lớp đã hoàn tất Zalo và nhận xét buổi học LMS nhưng chưa nộp SPCK (`LMS_SPCK_MISSING`) hoặc SPCK chưa có nhận xét (`LMS_SPCK_COMMENT_MISSING`) vẫn gửi trong Email Quản lý nhưng chỉ hiển thị mức cảnh báo (`⚠️`, màu vàng cam) thay vì tích chéo đỏ `❌`.
- Decision:
  - **Chưa nhận xét Zalo (`CATEGORY_MISSING`) là lỗi mức cao nhất**:
    - `normalize_zalo_status_text` (`repositories/report_repository.py`) và `normalizeZaloStatusDisplay` (`static/js/jobs.js`) chuẩn hóa hiển thị thành `"❌ Chưa nhận xét"` (giữ nguyên tiền tố `[DRY-RUN] ` nếu có).
    - `_resolve_row_evaluation_status` và `resolveDashboardRowEvaluationStatus` trả `"❌ Đã hoàn tất nhận xét LMS, chưa nhận xét Zalo"` khi `lms_done=True` (ghi đè chuỗi cũ `⚠️ ... Zalo cần kiểm tra` / `⏳ ...` từ Teaching), và trả `"❌ Chưa hoàn thành nhận xét"` (hoặc giữ chuỗi bắt đầu bằng `❌`) khi chưa xong LMS; giữ nguyên `⏳` cho `CATEGORY_NOT_SCANNED` và `⚠️` cho `CATEGORY_REVIEW`.
    - Trong bảng HTML Email Quản lý (`services/report_email_service.py`), cột `NX Zalo`, cột `Tình trạng đánh giá` (khi `❌`) và gạch đầu dòng `[Zalo] Chưa gửi/nhận xét/báo cáo...` ở cột `Mô tả vấn đề (TE/TF/QC)` đều được tô chữ đỏ đậm (`#b91c1c`).
  - **Chưa nộp SPCK (`LMS_SPCK_MISSING`) và SPCK chưa có nhận xét (`LMS_SPCK_COMMENT_MISSING`) hiển thị mức Warning (`⚠️`)**:
    - Giữ nguyên `is_failed=True` để dòng vẫn được đưa vào danh sách gửi Email Quản lý và bộ lọc cần xử lý.
    - Khi Zalo hoàn tất và nhận xét buổi học LMS không bị thiếu/chờ xác minh/cần kiểm tra, `Tình trạng đánh giá` hiển thị `"⚠️ Chưa nộp SPCK trên LMS"` hoặc `"⚠️ SPCK chưa có nhận xét trên LMS"` (tự động chuyển đổi cả chuỗi cache cũ `"❌ ..."` nhưng xóa cảnh báo SPCK cũ nếu nhận xét LMS đang `MISSING`, `UNVERIFIED` hoặc `⚠️` để không che lấp lỗi nhận xét LMS).
    - Trong bảng HTML Email Quản lý, ô `Tình trạng đánh giá` mang dấu `⚠️` và các gạch đầu dòng `[LMS] Chưa nộp SPCK` / `[LMS] SPCK chưa có nhận xét` hiển thị màu vàng cam cảnh báo (`#b45309`); trên Dashboard (`static/js/jobs.js`), các dòng chỉ vướng cảnh báo SPCK cũng đồng bộ màu vàng cam (`text-amber-700`) cho cả `Đánh giá:` và `Vấn đề:`.
- Consequences: Quản lý nhìn vào bảng Email phân biệt tức thì lỗi nặng nhất (chưa báo cáo Zalo — đỏ đậm `❌`) với cảnh báo hoàn thiện SPCK (vàng cam `⚠️`), đồng bộ 100% giữa Email HTML, CSV, API và Dashboard mà không cần quét lại Zalo cho dữ liệu đã lưu.
- Supersedes: Tinh chỉnh cách hiển thị `Tình trạng đánh giá` và màu sắc Email/Dashboard của `LMS_SPCK_MISSING`, `LMS_SPCK_COMMENT_MISSING` và `CATEGORY_MISSING` trong DEC-042, DEC-045 (giữ nguyên quy tắc tính `is_failed`).

### DEC-046 hotfix — 2026-11-13

- Context: Trên dữ liệu thật `Job #500` (`VT-ROB-ARMI12`), bản ghi `NOT_FOUND_VERIFIED` đi kèm `write_status = "FAILED_BEFORE_WRITE"` và `error_code = "EVIDENCE_NOT_READY"` (do từ chối tích BCNB khi chưa có báo cáo Zalo) khiến `categorize_row` xếp nhầm sang `CATEGORY_REVIEW`; đồng thời một số webmail (như BizFly Cloud) ghi đè thuộc tính `color` của thẻ `<td>`.
- Correction: `categorize_row` (`services/report_status.py`) và `resolveRowCategory` (`static/js/jobs.js`) giữ `CATEGORY_MISSING` cho `NOT_FOUND_VERIFIED` khi `bcnb_update_state`/`write_status` chỉ là `FAILED_BEFORE_WRITE` và `error_code` thuộc `{"", "EVIDENCE_NOT_READY", "LESSON_REPORT_ABSENT"}` (tự tính lại cả cache `REVIEW` cũ sang `MISSING`, `EXEMPT` ở Buổi 14 hoặc `DONE` khi duyệt tay, đồng thời giữ nguyên `REVIEW` cho mọi lỗi ghi/quét thật như `OUTCOME_UNKNOWN`, `CONFLICT_AFTER_WRITE`, `BLOCKED_BATCH_HALT`, `BCNB_WRITE_FAILED`). Bảng HTML Email Quản lý bọc thêm `<span style="color:#dc2626;font-weight:700;">` và `<span style="color:#b45309;font-weight:600;">` sau `html.escape` để hiển thị chuẩn màu trên mọi trình đọc webmail.

## DEC-047 — Cơ chế 2 luồng kiểm chứng (Luồng Nhanh `FAST-TRACK` / Luồng Đầy đủ `FULL GATE`)

- Date: 2026-11-13.
- Status: Accepted by Owner.
- Context: Quy trình một luồng duy nhất trong `DEC-036` và `DEC-040` buộc mọi chỉnh sửa nhỏ (đổi màu, câu chữ, CSS/HTML, định dạng email/hiển thị, tài liệu) đều phải chạy toàn bộ `.\CHECK_QUALITY.ps1 -Isolated` (~6 phút) và gọi Review độc lập `gpt-6.1-sol` (~3–5 phút/vòng), gây chậm phản hồi và tốn quota không tương xứng với rủi ro.
- Decision:
  - **Luồng Nhanh (`FAST-TRACK`)**: Áp dụng cho chỉnh sửa nhỏ, rủi ro thấp (UI/CSS/HTML, màu sắc, câu chữ/nhãn hiển thị, định dạng Email/CSV, tài liệu, hoặc sửa nhỏ cục bộ không đụng vào điều khiển trình duyệt, thao tác ghi BCNB/Teaching, DB schema/migration hay bảo mật/PII). Chỉ yêu cầu chạy unit test mục tiêu liên quan + `node tests/check_template_js.js` (nếu chạm JS/HTML) + `ruff check` & `mypy` trên các file Python thay đổi + `git diff --check`; miễn `.\CHECK_QUALITY.ps1 -Isolated` đầy đủ và miễn Review độc lập `gpt-6.1-sol` (trừ khi Owner yêu cầu riêng).
  - **Luồng Đầy đủ (`FULL GATE`)**: Áp dụng cho thay đổi lõi hoặc rủi ro cao (tự động hóa trình duyệt Zalo/LMS/Teaching/Form, thao tác ghi BCNB/Teaching, SQLite schema/migration, bảo mật/xác thực/khử PII, concurrency/lock, hoặc thay đổi quy tắc nghiệp vụ cốt lõi liên module). Bắt buộc chạy đầy đủ unit test mục tiêu + `node tests/check_template_js.js` + `.\CHECK_QUALITY.ps1 -Isolated` (`exit 0`) và Review độc lập `gpt-6.1-sol` đạt `VERDICT: PASS`.
  - Khi ranh giới rủi ro chưa rõ hoặc phát hiện ảnh hưởng sang luồng ghi/bảo mật, tự động nâng lên **Luồng Đầy đủ**. Cả hai luồng đều giữ nguyên nguyên tắc cập nhật `CHANGELOG.md` + `PROJECT_STATE.md`, kiểm tra `allowlist` (không stage `.env`, `database.db`, secret, dữ liệu thật), đồng bộ GitHub (`DEC-040`) và báo cáo ngắn gọn "Chuẩn Hàng Không" kèm nhãn `[FAST-TRACK]` hoặc `[FULL GATE]`.
- Consequences: Các chỉnh sửa nhỏ về hiển thị/giao diện/câu chữ chỉ mất ~15–30 giây để kiểm chứng và đồng bộ GitHub, trong khi các thay đổi rủi ro cao vẫn giữ nguyên lớp bảo vệ nghiêm ngặt của cổng cô lập và review độc lập.
- Supersedes: Bổ sung cơ chế phân tầng cổng kiểm chứng cho `DEC-036` và `DEC-040`.

## DEC-048 — Wave 7: Trang riêng `Lớp Hôm Nay (Live)`, spotlight SPCK Coding B12–14 & đồng bộ BCNB 2 tuần

- Date: 2026-11-15.
- Status: Accepted by Owner.
- Context: Chủ sở hữu cần một màn hình chuyên biệt theo ngày hiện tại để theo dõi thời gian thực các lớp diễn ra hôm nay theo khung giờ (`Chưa diễn ra` / `Đang diễn ra` / `Đã kết thúc`), trạng thái báo cáo Zalo (kèm ảnh chụp chứng cứ `2x`), điểm danh & nhận xét LMS, tiến độ SPCK Coding Buổi 12–14 (miễn `PTB`/`CSB`), và tick 2 cột `NX Zalo` + `NX LMS` trên BCNB đúng tuần mà không làm xáo trộn luồng batch `/run-scan` hay `Dashboard` tuần hiện có.
- Decision:
  - **Backend tổng hợp & đồng bộ theo ngày (`services/today_ops_service.py`, `api/today_routes.py`)**: Cung cấp `GET /api/today/overview` và `POST /api/today/sync` (job `TODAY_REALTIME_SYNC`), gom tối đa 2 tuần liên quan (`required_week` của buổi học + `latest_week`), tính `phase` (`UPCOMING`/`ONGOING`/`ENDED`), tái sử dụng `zalo_target_eligibility` cho các lớp đủ điều kiện giờ học, chụp ảnh chứng cứ tỷ lệ `capture_scale=2.0`, và khóa ghi an toàn qua `bcnb_update_lock` + `job_dispatcher`.
  - **Quy chuẩn SPCK & phân loại LMS đồng bộ backend–frontend**: Tái sử dụng `report_status.classify_course_track` cho Coding vs Robotics vs `PTB`/`CSB` (`spck_exempt_course=True`, fallback `"⚪ Miễn SPCK (PTB/CSB)"` khi chưa quét/`NOT_REQUIRED`), tách hàm `isTodaySpckComplete` trong `static/js/today.js`, và chỉ tính thiếu nhận xét LMS/SPCK sau khi `phase === 'ENDED'`.
  - **Giao diện tách biệt hoàn toàn (`#btn-today` / `#tab-today`)**: Thêm `templates/partials/today.html`, `static/js/today.js` và style `#tab-today` trong `static/css/app.css` với 6 bộ lọc, panel spotlight SPCK B12–14, hộp thoại bằng chứng và nút sao chép danh sách nhắc giáo viên; bảo toàn nguyên trạng các tab `Dashboard` tuần và giữ ranh giới fail-closed (`BCNB_DRY_RUN` / `confirm` / target matching của `DEC-032`, `DEC-044`).
- Consequences: Vận hành theo ngày có công cụ kiểm tra và đối chiếu tức thì, giảm bỏ sót báo cáo Zalo/LMS/SPCK B12–14 trước hạn cuối ngày nhưng vẫn tách biệt hoàn toàn với chu kỳ quét tuần.

## DEC-049 — Đổi Review độc lập luồng `[FULL GATE]` sang `Argon High` (`gemini-4-argon-high`) qua Swarm read-only

- Date: 2026-11-15.
- Status: Accepted by Owner.
- Context: Trong quá trình triển khai Wave 7, việc gọi review độc lập ngoài qua Codex CLI (`gpt-6.1-sol`) gặp giới hạn quota giữa chừng (`02:21 AM` reset), gây nghẽn chuỗi xác minh `#2`, `#3` và `#4`; Owner chỉ đạo chuyển sang `Argon High` để tiếp tục tiêu chuẩn review độc lập chất lượng cao.
- Decision:
  - **Mô hình review chuẩn mới**: Thay `gpt-6.1-sol` bằng **`Argon High` (`gemini-4-argon-high`)** cho bước Review độc lập bắt buộc của luồng **Đầy đủ (`FULL GATE`)** trong `AGENTS.md` và `HANDOFF.md`.
  - **Cô lập ngữ cảnh qua `swarm` read-only**: Khởi tạo verifier riêng bằng `swarm` (`action="add"`, `model="gemini-4-argon-high"`, `workspace="inherit"`, không truyền `fork`) để người review bắt đầu hoàn toàn độc lập với hội thoại Lead, tự đọc diff/mã nguồn/test ở chế độ chỉ đọc và trả về `VERDICT: PASS` hoặc `NEEDS_FIX` kèm vị trí dòng cụ thể cho đến khi mọi điểm `[P0]/[P1]/[P2]` được khắc phục và tái xác minh đạt `PASS`.
  - **Vận hành swarm gọn sạch**: Sau khi hoàn tất, Lead post `[done] ...` không gắn `@` để monitor tự nghỉ và `kill` các worker review đã hoàn thành.
- Consequences: Quy trình `[FULL GATE]` duy trì được tính độc lập hai cặp mắt mà không phụ thuộc tiến trình Codex CLI bên ngoài và thống nhất hạ tầng điều phối trên `swarm`.
- Supersedes: Cập nhật công cụ Review độc lập trong `DEC-036`, `DEC-040` và `DEC-047`.

## DEC-050 — Tối ưu tốc độ Phase 3 BCNB (`dom_dirty` + 1-IPC `page.evaluate` + `complete_write` từng dòng + timeout `3600s`), quy tắc Tiêu đề SPCK mới & Chuẩn ghi chú QC ngắn gọn Mức A (`[Zalo]` · `[LMS]` · `[SPCK]`)

- Date: 2026-11-18.
- Status: Accepted by Owner, thực hiện và kiểm chứng source/local theo `PROJECT_STATE.md`.
- Context: Thực tế chạy batch lớn (`Job #530`, 80+ dòng BCNB) gặp hiện tượng hết hạn `JOB_TIMEOUT_SECONDS=1800` ở dòng 50/51 do `update_bcnb_reports` đọc lại bảng BCNB `O(N^2)` qua hàng trăm IPC locator mỗi dòng dù trang không reload, đồng thời `write_worker` chỉ gọi `complete_write` sau khi cả tuần hoàn tất khiến `terminalize_interrupted` đánh dấu toàn bộ 36 target đang `RUNNING` thành `OUTCOME_UNKNOWN / PARENT_INTERRUPTED` dù 35 dòng đã `VERIFIED`. Song song, Owner chỉ đạo: (1) bỏ yêu cầu bắt buộc ghi tên học viên trong tiêu đề SPCK trên LMS (chỉ bắt lỗi tiêu đề trống/placeholder đối phó hoặc copy-paste trùng lặp từ 2 học viên trở lên), và (2) rút gọn câu chữ ghi chú QC tự động theo **Mức A** (gộp tác vụ theo thẻ `[Zalo]` · `[LMS]` · `[SPCK]`, chỉ giữ tỷ lệ `(x/y)` ở 3 lỗi `FAIL` chính) để QC và giảng viên đọc lướt nắm ngay việc cần xử lý.
- Decision:
  - **Tăng tốc Phase 3 BCNB, lưu trạng thái ghi từng dòng & nâng đệm timeout (`services/zalo_bcnb_service.py`, `services/zalo_parallel_service.py`, `core/settings.py`, `googleform_autosubmit/config.py`, `.env.example`)**:
    - Theo dõi cờ `dom_dirty` và bản chụp `latest_bcnb_rows` theo từng tuần trong `update_bcnb_reports`: chỉ gọi `_relocate_fresh_row` khi `dom_dirty == True` hoặc `need_row_refresh == True` (sau thao tác click checkbox Stage A/B, POST ghi chú Stage C khác `NO_CHANGE`/lỗi, hoặc truy vấn LMS trên `page`), và tái sử dụng `latest_bcnb_rows` trong `0ms` cho các dòng kế tiếp không làm thay đổi DOM.
    - Bổ sung đường đọc nhanh 1 vòng IPC `await page.evaluate(_READ_BCNB_ROWS_FAST_JS, ext_cols)` trong `_read_bcnb_schedule_rows` cho trang Playwright thật (giữ nguyên parity `checker_note_unreadable`, `bcnb_session_id`, `nx_zalo_checked`, `nx_lms_checked`, khoảng trắng ghi chú và fallback 100% về vòng lặp locator cho unit-test double).
    - Bổ sung callback `on_row_finalized` vào `update_bcnb_reports` và `finalize_claimed_row` / `completed_claimed_ids` trong `write_worker` để cập nhật `result_json` + `complete_write` ngay khi từng dòng kết thúc Stage A+B+C, bảo toàn trạng thái `VERIFIED` / `ALREADY_DONE` trong SQLite nếu batch bị hủy hoặc timeout giữa chừng; nâng mặc định `JOB_TIMEOUT_SECONDS` từ `1800` (30 phút) lên `3600` (60 phút).
  - **Quy tắc Tiêu đề SPCK mới (`services/lms_quality_service.py`)**:
    - Gỡ bỏ hoàn toàn cảnh báo `[LMS] Tiêu đề SPCK chưa ghi kèm tên học viên`; chấp nhận hợp lệ (`0` lỗi tiêu đề) mọi tên sản phẩm/dự án thực chất không kèm tên học viên.
    - Chỉ gắn nhắc nhở tên sản phẩm (`bad_title_count` -> `tên SP`) khi: (a) tiêu đề trống, chỉ có dấu chấm/ký tự câu hoặc từ khóa đối phó chung chung (`"SPCK"`, `"Sản phẩm cuối khóa"`, `"SPCK - [HS]"`, `"Dự án cuối khóa"`, mã lớp/số buổi đơn thuần; ngoại trừ tiêu đề chỉ gồm tên riêng học viên không chứa từ khóa bypass), hoặc (b) tiêu đề copy-paste trùng nhau giữa `>= 2` học viên đi học trong cùng buổi thông qua khóa băm tất định không chứa PII `rawTitleKey` tính ngay trên trình duyệt trước bước `scrubStudentNames`.
  - **Chuẩn ghi chú QC ngắn gọn Mức A & tương thích ngược hoàn toàn (`services/lms_quality_service.py`, `services/zalo_target_matching.py`, `services/bcnb_safety.py`, `services/zalo_bcnb_service.py`, `services/report_status.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`, `static/js/today.js`)**:
    - Tách thẻ riêng `[SPCK]` cho Sản phẩm cuối khóa; chỉ giữ số lượng `(x/y)` ở 3 nghĩa vụ `FAIL` cốt lõi (`[LMS] Chưa NX (x/y)`, `[SPCK] Chưa nộp (0/y)`, `[SPCK] Chưa NX (x/y)`) và gom toàn bộ nhắc nhở `NOTE` thành tối đa 1 câu hành động ngắn gọn trên mỗi thẻ (`[LMS] Điền tóm tắt bài, kiểm tra điểm danh-NX, viết kỹ NX`, `[SPCK] Bổ sung nộp đủ bài, tên SP, ảnh, file/file code, viết kỹ NX, YouTube, Slide`, `[Zalo] Sai số buổi, lệch tên bài với LMS`, ...).
    - Mở rộng bộ khóa provenance (`_provenance_issue_key`), phân loại trạng thái, Email HTML/CSV (`DEC-046`: đỏ `#dc2626` cho thiếu Zalo, vàng cam `#b45309` cho `LMS_SPCK_MISSING` / `LMS_SPCK_COMMENT_MISSING`) và Dashboard/Today JS để nhận diện đồng thời cả câu chữ dài lịch sử trong `external_action_audit`/DB lẫn câu chữ Mức A mới, giữ nguyên ghi chú tay của QC từng byte và không gây va chạm khóa giữa `[LMS] Chưa NX` với `[SPCK] Chưa NX`.
- Consequences: Loại bỏ nghẽn `O(N^2)` ở bước đối chiếu/ghi BCNB, bảo vệ kết quả các dòng đã `VERIFIED` trước sự cố ngắt giữa tuần, chấm dứt cảnh báo giả về tên học viên trong tiêu đề SPCK và rút gọn đáng kể độ dài cột `Mô tả vấn đề (TE/TF/QC)` trên Teaching, Dashboard và Email Quản lý.
- Supersedes: Cập nhật quy tắc tiêu đề SPCK và biểu mẫu câu chữ ghi chú QC tự động trong `DEC-038`, `DEC-039`, `DEC-042`, `DEC-045` và `DEC-046` (giữ nguyên điều kiện tính `is_failed` và phân cấp màu `DEC-046`).

## DEC-051 — Cắt khung ảnh minh chứng Zalo không mất lề trái/khoảng trống phải, phát hiện `[LMS] Chưa điểm danh` (`MISSING_ATTENDANCE`), sửa Tier-2 `fetchOneCode` (`centres` + short token) & khử trùng Legacy + Mức A

- Date: 2026-11-18.
- Status: Accepted by Owner, thực hiện và kiểm chứng source/local theo `PROJECT_STATE.md`.
- Context: Kiểm tra thực tế sau `Job #535` và `Job #538` phát hiện 3 vấn đề cụ thể: (1) ảnh minh chứng Zalo (`latest-178e5f658a.png`, viewport `1920x1200` `2x`) bị ăn lẹm ~65–80px bên trái (mất chữ đầu tiêu đề nhóm, avatar, chữ đầu tin nhắn và ô nhập) do `_resolve_zalo_chat_clip` lấy `Math.max` theo container tổ tiên `rect.right = 409` của `#contact-search-input`, đồng thời bị dư ~700px khoảng xám bên phải do dòng chia ngày rộng toàn khung kéo `clipRight = 1920`; (2) lớp chưa điểm danh trên LMS sau khi kết thúc ca học (`VT-KIND-ROB4B04` Buổi 12: `item.students` có học viên đang theo học nhưng `slot.studentAttendance == []`) bị gộp chung vào `UNVERIFIED` (`⏳ Chờ xác minh...`, `[LMS] Kiểm tra tay trên LMS` mức `NOTE`, không tính `is_failed=True` khi đã gửi Zalo) và giữ lại cả câu cũ `[LMS] Chưa ghi nội dung bài học` cạnh câu Mức A `[LMS] Điền tóm tắt bài`; (3) quét lại chọn lọc `<= 3` lớp (`Job #538`, `VT-C4K-PTI11`) đi qua Tier-2 `fetchOneCode` bị trượt lớp do thiếu biến `centres` và không thử lại theo hậu tố mã lớp ngắn khi `filter_textSearch` trả `0` kết quả.
- Decision:
  - **Cắt khung hội thoại Zalo chính xác (`services/zalo_scan_evidence.py`)**:
    - Ưu tiên neo trực tiếp vào container khung chat bên phải (`#messageView`, `#chatView`, `#main-tab`, `main` hoặc cột chứa header / `#richInput` / `#input_line_0` thỏa `180 <= rect.left <= vpW * 0.42`, `width >= 500`, `height >= vpH * 0.5`); chỉ fallback sang tổ tiên sidebar `#contact-search-input` trong ngưỡng chặt `220 <= width <= 385` và không cộng biên dương lấn sang khung chat.
    - Áp dụng chốt chặn bảo vệ lề trái vô điều kiện `chatLeft <= minContentLeft - 24` trên mọi phần tử hiển thị thuộc khung chat (loại trừ `sidebarRoot`) gồm bong bóng tin nhắn, thẻ card, avatar, tiêu đề header và thanh nhập liệu để `clip.x` không bao giờ cắt mất chữ/avatar.
    - Khi đo `maxBubbleRight` cho cạnh phải `clipRight`, nếu gặp phần tử bọc rộng gần hết khung chat (`r.width >= (vpW - chatLeft) * 0.85` như dòng mốc ngày căn giữa), đi sâu đo các phần tử con hiển thị thực tế (`.card`, `[class*="content"]`, `img`/`video`/`canvas`) thay vì để khung bọc trong suốt kéo giãn `clipRight = vpW`.
  - **Phát hiện `MISSING_ATTENDANCE` & Quy tắc câu chữ Teaching `[LMS] Chưa điểm danh` (`services/lms_quality_service.py`, `services/report_status.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `services/today_ops_service.py`, `static/js/jobs.js`, `static/js/today.js`)**:
    - Trích số nguyên không chứa PII `activeClassStudentCount` (`activeInClass !== false` có `stuId` hợp lệ) trong `formatItemSlots`; xác định `status = "MISSING_ATTENDANCE"` khi slot tải sạch (`not partial`, `student_attendance` là `list`) và rơi vào (a) `rows == []` với `activeClassStudentCount > 0` hoặc (b) toàn bộ dòng có trạng thái chưa điểm danh tường minh (`""`, `"NONE"`, `"NOT_ATTENDED"`, `"UNMARKED"`, `"PENDING"`, `"NULL"`) kèm khóa học viên hợp lệ không trùng (giữ nguyên `UNVERIFIED` cho mọi trường hợp lỗi tải, cắt trang, trộn trạng thái hoặc test dict cũ không truyền `activeClassStudentCount`).
    - Theo đúng chỉ đạo của Owner (*"trên Teaching chỉ ghi `[LMS] Chưa điểm danh`, không cần phần sau"*): khi `attendance.status == "MISSING_ATTENDANCE"`, `evaluate_lms_slot_quality` chỉ phát duy nhất 1 lỗi `FAIL` `[LMS] Chưa điểm danh` (`ISSUE_CODE_LMS_QUALITY_FAIL`), triệt tiêu đuôi `[LMS] Điền tóm tắt bài` và các ghi chú phụ `[SPCK]`, đồng thời supersede sạch các ghi chú `[LMS]`/`[SPCK]` tự động cũ trên Teaching (vẫn bảo toàn ghi chú tay của QC và ghi chú `[Zalo]` độc lập nếu có).
    - Hiển thị đồng bộ `resolve_lms_status` -> `"❌ Chưa điểm danh LMS"` (`is_failed=True`, `is_lms_ready_for_bcnb_tick=False` — chặn tuyệt đối tích `NX LMS`), `resolve_spck_status` -> `"SPCK khóa (chưa điểm danh)"`, rút gọn URL Gmail `"Chưa ĐD LMS"` và cập nhật bộ đếm/nhắc việc trên `Lớp Hôm Nay`.
  - **Sửa Tier-2 `fetchOneCode` & Khử trùng ngữ nghĩa Legacy + Mức A (`services/lms_quality_service.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`, `static/js/jobs.js`)**:
    - Đưa `centres: activeCentres` và `itemsPerPage: 50` vào Tier-2 `fetchOneCode`; khi `search: code` trả `0` kết quả khớp tên lớp, tự động thử lại hữu hạn theo hậu tố mã lớp sau dấu gạch ngang (xử lý cả hậu tố `-HB` như `GB42-HB` -> `GB42`) nhưng vẫn lọc cứng `itemName === code` và fail-closed khi trùng lớp (`duplicate_class: true`).
    - Khử trùng theo tương đương ngữ nghĩa giữa câu chữ lịch sử và câu chữ Mức A (`[LMS] Chưa ghi nội dung bài học` <-> `[LMS] Điền tóm tắt bài` / `[LMS] Điền tóm tắt bài, ...`, `...cần kiểm tra thủ công` <-> `Kiểm tra tay trên LMS`, `[Zalo] Báo cáo không ghi ngày/buổi` <-> `[Zalo] Thiếu ngày/buổi`, `[Zalo] Báo cáo ghi sai ngày` <-> `[Zalo] Sai ngày`), ưu tiên giữ câu Mức A ngắn gọn mới hơn trên cả Teaching, Dashboard, Báo cáo tuần, CSV và Email Quản lý mà vẫn giữ nguyên khóa provenance chính xác cho `external_action_audit`.
- Consequences: Ảnh minh chứng Zalo hiển thị trọn vẹn lề trái và gọn gàng bên phải; các ca quên điểm danh LMS được báo đỏ chính xác (`❌ Chưa điểm danh LMS`), đưa vào Email Quản lý và ghi gọn đúng `[LMS] Chưa điểm danh` lên Teaching; quét lại chọn lọc 1–3 lớp hoạt động đồng nhất với quét tổng và không còn hiện tượng lặp song song câu chữ cũ + mới.
- Supersedes: Tinh chỉnh chi tiết cắt khung ảnh của `DEC-041` và bổ sung trạng thái `MISSING_ATTENDANCE`, Tier-2 LMS fetch cùng khử trùng tương thích cho `DEC-043`, `DEC-045`, `DEC-046` và `DEC-050`.

## DEC-052 — Đối chiếu đúng buổi theo tuần kiểm tra trong Tự động Tick LMS (Hướng 1) & Chặn ghi lỗi sớm trước deadline trên Lớp Hôm Nay (LIVE)

- Date: 2026-11-20.
- Status: Accepted by Owner, thực hiện và kiểm chứng source/local theo `PROJECT_STATE.md`.
- Context: Thực tế vận hành ghi nhận hai vấn đề nghiệp vụ cần xử lý: (1) ở tính năng **Tự động Tick LMS** (`Job #540`), khi BCNB đang kiểm tra tuần trước (ví dụ Tuần `46/2026` với `VT-ROB-ARMA18` học `Buổi 1` ngày `11/11`, đã hoàn tất và đã tích `NX LMS`) nhưng right after buổi mới của tuần kế tiếp vừa học xong (`18/11` `Buổi 2` — Tuần `47/2026`), trang tổng hợp MindX LMS lập tức liệt kê lớp vào mục quá hạn ở `Buổi 2`; luồng `run_lms_analysis` chỉ khớp `class_code` nên báo giả `VT-ROB-ARMA18 (Buổi 2) — BCNB: Đã tích ⚠️`, đưa vào nút `Copy` nhắc giảng viên và khi toàn bộ lớp hợp lệ trong tuần đã được tích hết lại báo lỗi đỏ `FAILED: "Dừng an toàn: Không có lớp nào đủ điều kiện để tick."`; (2) trên trang **Lớp Hôm Nay (`LIVE`)**, khi người vận hành chạy **`Quét & Tick BCNB đúng tuần`** trong ngày để tick xanh cho giáo viên đã hoàn thành, `update_bcnb_reports` đồng thời ghi luôn lỗi thiếu (`[Zalo] Chưa gửi báo cáo`, `[LMS] Chưa điểm danh`, ...) vào ô `Mô tả vấn đề` trên Teaching dù lớp đó vẫn còn hạn deadline (48h).
- Decision:
  - **Đối chiếu đúng buổi theo tuần kiểm tra trong Tự động Tick LMS — Hướng 1 (`services/lms_plan.py`, `services/lms_service.py`, `static/js/jobs.js`)**:
    - Bổ sung `parse_bcnb_lesson_number` và `parse_overdue_lesson_numbers` (fail closed `[]` nếu có token không parse được sang số buổi dương) trong `services/lms_plan.py`.
    - Mở rộng `_read_lms_table(page, *, year=year)` trong `services/lms_service.py` để trích thêm metadata tùy chọn `lesson`, `class_date`/`class_date_iso` (qua `_parse_bcnb_cell_date_iso`), và `khoi` (`block_index` hoặc `data-block`) mà vẫn giữ nguyên `observed_classes` dạng `[{"class_code": code, "checked": bool}]` trong `build_lms_plan` để bảo toàn tuyệt đối hợp đồng `compute_plan_digest` và kiểm tra drift của `run_lms_automation`.
    - Bổ sung `_reconcile_mismatched_overdue_classes` trong `run_lms_analysis`: chỉ khi lớp tồn tại trên BCNB tuần kiểm tra với đủ `(bcnb_lesson, class_date_iso)` và `overdue_nums` LMS parse sạch không chứa `bcnb_lesson`, mới truy vấn đúng slot của `bcnb_lesson` qua `fetch_lms_slots_for_targets` + `select_matching_lms_slot` + `evaluate_lms_slot_quality` (`DisabledLmsCommentReviewerGateway`) + `is_lms_ready_for_bcnb_tick`. Chỉ gỡ lớp khỏi `missing_classes`/`overdue_details` khi `is_lms_ready_for_bcnb_tick == True`; nếu buổi thuộc tuần kiểm tra thực sự chưa hoàn tất thì giữ lớp ở ô `Không tick` kèm đúng số buổi `Buổi {bcnb_lesson}`; nếu lỗi tải/slot mơ hồ/thiếu gateway thì giữ nguyên trạng thái fail-closed ban đầu (re-raise `JobCancelledError`).
    - Khi `classes_to_tick == []` và `already_checked` > 0 với `blocking_issues == []`: `build_lms_plan` không đẩy thông báo lỗi `"Không có lớp nào đủ điều kiện để tick."`, đặt `can_confirm = False`, `all_eligible_already_checked = True` kèm `info_message` tiếng Việt thân thiện; `run_lms_analysis` kết thúc ở trạng thái `SUCCESS` (`confirmation_token = None`) và `showLmsReview` (`static/js/jobs.js`) hiển thị dải thông báo xanh ngọc `emerald` trong khi vẫn khóa nút xác nhận.
  - **Chặn ghi lỗi mới trước deadline lên Teaching khi đồng bộ `Lớp Hôm Nay (LIVE)` (`services/zalo_bcnb_service.py`, `services/today_ops_service.py`)**:
    - Bổ sung `_is_bcnb_deadline_expired(raw_deadline, *, now_dt)` trong `services/zalo_bcnb_service.py` ưu tiên bắt mốc 4 chữ số năm (`HH:MM DD/MM/YYYY`, `DD/MM/YYYY HH:MM`, `YYYY-MM-DD HH:MM`) theo múi giờ nghiệp vụ (tránh nhầm lẫn với dòng timestamp `DD/MM HH:MM` thứ 3 của BCNB), fallback an toàn về ngày/văn bản và trả `False` khi trống/không đọc được.
    - Thêm tham số keyword `defer_unexpired_checker_notes: bool = False` vào `update_bcnb_reports` (mặc định `False` giữ nguyên hành vi `ZALO_SCAN`) và bật `defer_unexpired_checker_notes=True` trong `services/today_ops_service.py:_run_bcnb_sync_on_session`.
    - Trong `_maybe_update_row_checker_note`, kiểm tra độc lập `zalo_deadline` và `lms_deadline` (`or zalo_deadline`): trước thời điểm hết hạn, Stage A (`NX Zalo`) và Stage B (`NX LMS`) vẫn tick bình thường cho GV đã xong, mọi lỗi thiếu live trên giao diện `Lớp Hôm Nay` & `Copy nhắc GV` (`today_items`) vẫn hiển thị đầy đủ, các lỗi tự động cũ đã có trên BCNB nhưng GV vừa khắc phục vẫn được dọn xóa ngay lập tức và ghi chú tay của QC vẫn bảo toàn từng byte, còn lỗi mới phát sinh chưa quá hạn được hoãn ghi lên Teaching (`checker_note_outcome = "DEFERRED_BEFORE_DEADLINE"`); sau khi quá hạn vẫn ghi bình thường.
- Consequences: Chấm dứt hoàn toàn cảnh báo lệch buổi/tuần giữa MindX LMS thời gian thực và BCNB tuần trước mà vẫn kiểm chứng chắc chắn buổi tuần cũ trên LMS; đồng thời giúp thao tác **`Quét & Tick BCNB đúng tuần`** trong ngày tick ngay các lớp GV làm sớm mà không bôi lỗi sớm lên bảng Teaching trước deadline.
- Supersedes: Mở rộng quy tắc đối chiếu LMS Web của `DEC-035` và luồng đồng bộ `Lớp Hôm Nay` của `DEC-048`.

## DEC-053 — Truy vấn trực tiếp GraphQL danh sách học viên đang học (`type: []`) cho tính năng Lấy từ LMS của Nộp Google Form

- Date: 2026-11-20.
- Status: Accepted by Owner, thực hiện và kiểm chứng source/local theo `PROJECT_STATE.md`.
- Context: Tính năng **Nộp Google Form -> Lấy từ LMS** (`GOOGLEFORM_LMS_STUDENTS`) bị thất bại toàn bộ trên các job thực tế (`Job #373`, `#376`, `#547`, `#548`), điển hình là lớp `VT-CSA04-HB` dừng an toàn với lỗi `"Không tìm thấy hoặc không thể mở lớp VT-CSA04-HB trên trang LMS."`. Hai nguyên nhân gốc được xác định: (1) `_read_lms_students_by_classes` mở `Config.LMS_URL` vốn gắn sẵn bộ lọc `type=["MISS_ATTEND","DUE_COMMENT"]`, nên các lớp hoàn tất điểm danh/nhận xét đúng hạn (không bị quá hạn LMS, kể cả `VT-CSA04-HB` đã có trong `discovery_snapshots` và `bcnb_week_plan`) hoàn toàn không xuất hiện trên trang quá hạn; (2) thanh tìm kiếm MindX LMS bỏ sót mã đầy đủ có hậu tố `-HB`, đồng thời việc bấm từng dòng bảng DOM để mở sĩ số dễ gãy trước thay đổi giao diện.
- Decision:
  - **Truy vấn trực tiếp GraphQL `type: []` qua phiên trình duyệt đã đăng nhập (`services/lms_quality_service.py`)**:
    - Bổ sung `fetch_lms_active_students_via_page(page, *, class_codes, timeout_ms=15000)` truy vấn `classesMissAttendOrComment` với **`type: []`** (không lọc quá hạn) để lấy đầy đủ lớp đủ chuẩn lẫn lớp quá hạn.
    - Xử lý khởi động lạnh an toàn: khi trang mở ở `"about:blank"` hoặc host khác `LMS_ALLOWED_HOSTNAME`, luôn điều hướng sang `Config.LMS_URL` trước khi `evaluate` `fetch()` nhằm tránh lỗi CORS `Origin: null` và lệch IndexedDB ngay cả khi `storage_state` đã có sẵn token (vẫn giữ nguyên trigger `not pre_resolved_token` trong `fetch_lms_class_slots_via_page` để bảo toàn hành vi test hiện có).
    - Tái cấu trúc gọn các helper Python/JS dùng chung (`_CLEAR_STALE_LMS_AUTH_JS`, `_LMS_JS_COMMON_HELPERS`, `_extract_lms_centres_from_config`, `_extract_pre_resolved_lms_token`, `_create_lms_graphql_request_sniffer`, `_open_and_authenticate_lms_page`, `_cleanup_lms_eval_resources`) giữa `fetch_lms_class_slots_via_page` và `fetch_lms_active_students_via_page`, bổ sung ứng viên tìm kiếm mã ngắn cho hậu tố `-HB` (ví dụ `VT-CSA04-HB` -> `VT-CSA04-HB`, `CSA04-HB`, `CSA04`, bỏ qua `'HB'`) và vẫn ép khớp chính xác `item.name === code` + `dedupeExactById`.
    - Duy trì 2 tầng dự phòng hữu hạn: Tier-1 bulk (`> 3` lớp, tối đa 15 trang) và Tier-2 từng lớp qua 3 lượt (`Pass 1`: `status=['FINISHED','RUNNING']` + `centres`; `Pass 2a`: bỏ `status` + giữ `centres`; `Pass 2b`: bỏ cả `status` và `centres` cho lớp khác cơ sở), chỉ lấy học viên `activeInClass !== false` và giới hạn bộ đệm trước khi materialize (`5 MiB` JSON, `MAX_LMS_STUDENTS_PER_BATCH = 200`).
  - **Tích hợp fail-closed & tương thích ngược DOM (`services/lms_service.py`)**:
    - Bổ sung `_parse_graphql_active_students` và tích hợp vào `_read_lms_students_by_classes`: dừng an toàn (`LmsSafetyError`) khi lỗi xác thực/mạng (`__error__`, `__auth_error__`, `fetch_error`), thiếu lớp, trùng tên lớp (`duplicate_class`), lớp không có học viên đang học (`0`), trùng tên chuẩn hóa khác `stu_id`, hoặc tổng batch vượt `MAX_LMS_STUDENTS_PER_BATCH` (`200`); lọc bỏ `"Default"` qua `_clean_lms_student_name`, gộp trùng cùng `stu_id`, kiểm tra hủy job và tuyệt đối không ghi PII tên học viên vào log/lỗi.
    - Khi `ZALO_QUALITY_ISOLATED == "1"` hoặc page giả trong unit test không có `.evaluate`, trả `None` để chạy nguyên vẹn luồng đọc bảng DOM lịch sử.
- Consequences: `Lấy từ LMS` trên `Nộp Google Form` lấy được danh sách học viên đang học cho mọi lớp hợp lệ (kể cả lớp không quá hạn LMS, lớp `PENDING`, lớp cơ sở khác và lớp có hậu tố `-HB`) với độ tin cậy cao, vẫn bảo toàn tuyệt đối các chốt chặn fail-closed và `0` PII trong log.
- Supersedes: Nâng cấp cơ chế lấy học viên từ LMS của `DEC-035` và tái sử dụng hạ tầng xác thực/GraphQL của `DEC-051`.

## DEC-054 — Nâng cấp Review độc lập luồng `[FULL GATE]` sang `Neon XHigh` (`gemini-4-neon-xhigh`) qua Swarm read-only

- Date: 2026-11-22.
- Status: Accepted by Owner.
- Context: Owner chỉ đạo nâng cấp mô hình Reviewer độc lập từ `Argon High` (`gemini-4-argon-high`) sang mô hình mới tốt nhất của Google là **`Neon XHigh`** (`gemini-4-neon-xhigh` — `Gemini 4 Neon (XHigh)`).
- Decision:
  - Thay `Argon High` (`gemini-4-argon-high`) bằng **`Neon XHigh` (`gemini-4-neon-xhigh`)** cho bước Review độc lập bắt buộc của luồng **Đầy đủ (`[FULL GATE]`)** trong `AGENTS.md` và `HANDOFF.md` (luồng `[FAST-TRACK]` tiếp tục miễn Review độc lập `Neon XHigh` trừ khi Owner yêu cầu riêng).
  - Giữ nguyên cơ chế cô lập ngữ cảnh qua `swarm` read-only (`action="add"`, `model="gemini-4-neon-xhigh"`, `workspace="inherit"`, không truyền `fork`) và dọn dẹp worker sau khi hoàn tất theo `DEC-049`.
- Consequences: Bước Review độc lập của `[FULL GATE]` tận dụng mức suy luận cao nhất (`XHigh`) của dòng Gemini 4 Neon mới nhất trên cùng hạ tầng `swarm` read-only.
- Evidence: Kiểm chứng danh mục model qua `swarm(action="models", all=true)` ngày `2026-11-22` xác nhận `"gemini-4-neon-xhigh"` (`"Gemini 4 Neon (XHigh)"`) khả dụng; cập nhật `AGENTS.md`, `HANDOFF.md`, `PROJECT_STATE.md`, `CHANGELOG.md`.
- Supersedes: Cập nhật mô hình Review độc lập trong `DEC-049` (bổ sung cho `DEC-047`).

## DEC-055 — Mặc định Trang Lớp Hôm Nay (Live) theo Tuần thực tế (`current_week`), Bỏ qua lớp đã tick đủ (`skip_completed_bcnb`) & Tick-only BCNB + Lưu từng dòng (`Wave 8`)

- Date: 2026-11-22.
- Status: Accepted by Owner, thực hiện và kiểm chứng source/local theo `PROJECT_STATE.md` (`[FULL GATE]`).
- Context: Trong vận hành thực tế trang **`Lớp Hôm Nay (Live)`**, khi kiểm tra cuối tuần hoặc xử lý các lớp đầu tuần còn thiếu tick trên BCNB: (1) phạm vi mặc định chỉ xem theo từng ngày đơn lẻ khiến điều phối phải đổi ngày thủ công để rà soát toàn bộ `Tuần thực tế` (`latest_week`) và dễ quét lại các lớp đã có đủ cả `NX Zalo` lẫn `NX LMS` trên BCNB; (2) thao tác **`Quét & Tick xanh BCNB`** trên trang Live chỉ nhằm kiểm tra nhanh & tick xanh cho GV đã làm xong, nhưng các buổi học quá hạn `>48h` từ đầu tuần lại bị `update_bcnb_reports` ghi thêm lỗi mới lên cột `Mô tả vấn đề` của Teaching (vốn nên do luồng quét đầy đủ `ZALO_SCAN` đảm nhiệm); (3) khi quét nhiều lớp trong tuần mà bị ngắt giữa chừng, kết quả `complete_write` từng dòng cần được bền vững ngay sau mỗi lớp xong trên `zalo_batch_targets`.
- Decision:
  - **Tick-only BCNB cho Live & Bảo toàn họ lỗi đang mở (`services/zalo_bcnb_service.py`, `services/today_ops_service.py`)**:
    - Thêm `defer_all_new_checker_notes: bool = False` vào `update_bcnb_reports` (mặc định `False` giữ nguyên 100% hành vi `ZALO_SCAN` và `DEC-052`). Trong `_run_bcnb_sync_on_session`, truyền cả `defer_unexpired_checker_notes=True` và `defer_all_new_checker_notes=True`.
    - Trong `_maybe_update_row_checker_note`, khi `defer_all_new_checker_notes=True`, ép `zalo_expired=False` và `lms_expired=False` bất kể deadline; dùng `_deferred_issue_family_keys` (so khớp chính xác, prefix khử bộ đếm `\s*\(\s*\d+\s*/\s*\d+(?:\s*hs)?\s*\)`, và các họ ngữ nghĩa `[LMS]`/`[SPCK]`/`[Zalo]` tương thích legacy<->Mức A) để giữ nguyên ghi chú tự động đã có trên BCNB khi họ lỗi đó vẫn còn hoạt động (ví dụ `(3/5)->(2/5)` hay ghép câu phụ, **0 POST**), chỉ xóa khi họ lỗi đó thực sự đã được GV khắc phục, bảo toàn ghi chú tay QC từng byte, tuyệt đối không ghi lỗi mới và gắn `checker_note_outcome = "DEFERRED_FOR_FULL_SCAN"`.
  - **Lưu bền vững từng dòng qua `on_row_finalized` (`services/today_ops_service.py`)**:
    - Claim sẵn dòng ghi (`ZaloBatchRepository.claim_write`) trước `update_bcnb_reports` khi chạy luồng ghi, truyền `on_row_finalized` để gọi ngay `update_target_result` + `complete_write` sau từng dòng hoàn tất, theo dõi `completed_claimed_ids` không double-complete, và chuẩn hóa trạng thái kết thúc qua `_ALLOWED_WRITE_FINAL_STATUSES` / `_normalize_write_final_status` (khớp `ZaloBatchRepository.WRITE_STATUSES`, map trạng thái lạ hoặc `"SKIPPED"` sang `"FAILED_BEFORE_WRITE"`).
  - **Phạm vi `current_week` | `date`, `skip_completed_bcnb` & An toàn khớp snapshot/overlay (`services/today_ops_service.py`, `api/today_routes.py`, `templates/partials/today.html`, `static/js/today.js`)**:
    - Thêm `parse_today_scope` (`"date"` | `"current_week"`, lỗi -> HTTP 400 `INVALID_TODAY_SCOPE`), mở rộng `GET /api/today/overview` (`scope`, giữ mặc định `"date"` khi bỏ trống để tương thích ngược API) và `POST /api/today/sync` (`scope`, `skip_completed_bcnb`).
    - Khi `scope="current_week"`, `_collect_today_entries_from_snapshot` gom toàn bộ buổi của `latest_week` giữ nguyên `class_date_iso` từng buổi (không ghi đè bằng `target_date_iso`), khớp bảng BCNB theo `(kt, bcnb_session_id)` trước rồi mới fallback `kt` duy nhất không xung đột session ID, và sắp xếp `_timeline_sort_key` theo `(class_date_iso, start_hm, end_hm, class_code, lesson_num)`.
    - `_load_persisted_today_overlays` cô lập banner `TODAY_REALTIME_SYNC` theo `scope`, lọc kết quả theo `(week, year)`, bỏ qua item `skipped_already_completed=True`; `_merge_entry_with_overlay` nhận `snapshot_observed_at` để bảo toàn `nx_zalo_checked`/`nx_lms_checked`/`qc_issue_note`/`instructor_note` của snapshot mới hơn mà vẫn giữ ảnh/bằng chứng từ overlay cũ hơn.
    - `run_today_realtime_sync` dùng `_is_entry_bcnb_completed` fail-closed (`nx_zalo_checked is True and nx_lms_checked is True`), chỉ bật `skip_completed_bcnb` khi không truyền `selected_target_keys`, không ghi item giữ chỗ cho lớp đã bỏ qua, và trả `SUCCESS` sạch không mở trình duyệt khi toàn bộ lớp đã tick đủ.
    - Giao diện `Lớp Hôm Nay` (`templates/partials/today.html`, `static/js/today.js`): mặc định `todayActiveScope='current_week'` và `todaySkipCompletedBcnb=true` kèm công tắc chuyển `Tuần thực tế` / `Theo ngày`, checkbox lọc bỏ qua lớp đã tick đủ, hiển thị thứ + ngày `dd/mm` trên timeline/SPCK, huy hiệu `✅ Đã tick đủ BCNB`, và hỗ trợ trong `copyTodayReminders`.
- Consequences: Người vận hành mặc định rà soát trọn `Tuần thực tế` trên `Lớp Hôm Nay (Live)`, tự động bỏ qua các lớp đã tick đủ để tập trung xử lý phần còn tồn, không bao giờ bôi lỗi mới từ thao tác tick xanh Live lên Teaching trước khi chạy `ZALO_SCAN`, và bảo toàn tiến độ từng dòng nếu gián đoạn giữa chừng.
- Evidence: Sao lưu nóng Pre-Wave 8 + Restore Drill (`.backups/db/pre-wave8-20261122-080139.sqlite3`); `.\CHECK_QUALITY.ps1 -Isolated` (`788/788` `OK`, `80.3%` branch coverage, Ruff `0`, Mypy `0` trên `80` files, `secrets=0`, `template-js-contract-ok scripts=8 handlers=48 behavior=14`); `graphify update .` (`5258 nodes, 15927 edges, 204 communities`); Review độc lập `Neon XHigh` (`#16`, `VERDICT: PASS`, `0` P0/P1/P2).
- Supersedes: Mở rộng thiết kế trang `Lớp Hôm Nay` của `DEC-048`, `DEC-050`, `DEC-052`.

## DEC-056 — Chuẩn hóa Báo cáo Quản lý 8 cột, tô đỏ `NX LMS` (Phương án 1A), tách bảng lưu ý SPCK (Phương án 3A), chống trùng `[SPCK] Kiểm tra tay trên LMS` và đổi nhãn `[LMS] Chưa có nội dung bài`

- Date: 2026-11-25.
- Status: Accepted by Owner, thực hiện và kiểm chứng source/local theo `PROJECT_STATE.md` (`[FULL GATE]`).
- Context: Đối chiếu Email Báo cáo Quản lý thực tế (lớp `VT-C4K-PTA15` Buổi 1, `VT-ROB-SEMIA06` Buổi 12, `VT-ROB-PREA16` Buổi 12) phát sinh bốn vấn đề: (1) thiếu nhận xét LMS (`0/6` hoặc thiếu một phần) hiển thị `"❌ Đã nhận xét 0/6 HS đi học"` gây ngược nghĩa và chưa tô chữ đỏ đậm trên Email/Dashboard; (2) cột `Tình trạng đánh giá` lặp lại thông tin của `NX Zalo` + `NX LMS`, gây rối; (3) lớp `VT-ROB-SEMIA06` hiện song song `[SPCK] Chưa nộp (0/6)` và `[SPCK] Kiểm tra tay trên LMS` khi lần quét sau bị lỗi fetch tạm thời trên `findAllStudentWorks`, đồng thời lớp đã xong NX Zalo & LMS nhưng chưa nộp SPCK bị gộp chung vào nhóm lỗi nhận xét; (4) nhãn `[LMS] Điền tóm tắt bài` chưa rõ nghĩa bằng `[LMS] Chưa có nội dung bài`.
- Decision:
  - **Phương án 1A (`NX LMS`) — Rõ nghĩa, chéo đỏ, chữ đỏ đậm (`services/report_status.py`, `services/report_email_service.py`, `static/js/jobs.js`)**:
    - Thiếu nhận xét `≥ 1` HS đi học (`comments.status == "MISSING"`): `submitted == 0` hiển thị `"❌ Chưa nhận xét"`; `0 < submitted < attended` hiển thị `"❌ Chưa nhận xét (thiếu {attended - submitted}/{attended} HS)"` (giữ hậu tố nghỉ miễn / chờ xác minh).
    - Trong Email HTML (`_build_html_rows_table`), mọi ô cột `NX LMS` bắt đầu bằng `❌` và các gạch đầu dòng `[LMS] Chưa NX|nhận xét|điểm danh|Chưa có nhận xét|Thiếu nhận xét` được tô đỏ đậm `#dc2626` (bọc `<span>` tương thích Webmail); trên Dashboard, dòng `LMS: ❌...` dùng `text-rose-700`.
  - **Phương án 2A (Bảng Email & CSV 8 cột) (`services/report_email_service.py`, `repositories/report_repository.py`)**:
    - Gỡ cột `Tình trạng đánh giá` khỏi `EMAIL_TABLE_HEADERS`, HTML Email, Unicode table, `detail_lines`, `CSV_HEADERS` và `build_weekly_summary_csv` (dọn luôn dead code tính `eval_status` trong `build_weekly_summary_csv`), giữ nguyên `_resolve_row_evaluation_status` cho Dashboard/API nội bộ.
  - **Phương án 3A (Tách bảng SPCK & Chống trùng `[SPCK] Kiểm tra tay trên LMS`) (`services/report_status.py`, `services/lms_quality_service.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`)**:
    - Bổ sung `is_spck_only_warning_row(row)` để tách riêng các lớp đã hoàn tất NX Zalo & LMS nhưng chỉ thiếu/chưa NX SPCK sang **Bảng 2** cảnh báo vàng cam (`#b45309`) trong Email Quản lý, không cộng vào `nx_failed_count` ở tiêu đề và thống kê đầu thư (hỗ trợ cả ca 0 lỗi NX + có SPCK-only).
    - Trong `has_spck_missing_fail` / `has_spck_comment_missing_fail` (Python & JS), giữ nguyên khóa fail-closed trả `False` khi `lms_attendance.status ∈ {"UNVERIFIED", "EXEMPT", "MISSING_ATTENDANCE"}`, nhưng khi điểm danh đã `VERIFIED` mà `lms_spck.status == "UNVERIFIED"` thì vẫn kiểm tra `lms_quality_issues` / `quality_issues` để không làm rơi `[SPCK] Chưa nộp (0/N)` đã xác minh.
    - Nâng giới hạn an toàn `fetchSessionWorks` (`MAX_WORK_REQUESTS = 240`, `workDeadline = max(45000, timeoutMs * 4)`) kèm 1 lần retry `250ms` chỉ cho HTTP `5xx` / `429` / network / abort (không retry `authFailed`/4xx khác); chặn ghép thêm `[SPCK] Kiểm tra tay trên LMS` khi đã có issue SPCK xác minh ở cả `lms_quality_service.py`, `zalo_bcnb_service.py`, `report_repository.py` và `jobs.js` (giữ nguyên `[LMS] Kiểm tra tay trên LMS` theo D4 và bảo toàn `[LMS] SPCK cần kiểm tra thủ công` khi đứng độc lập).
  - **Đổi nhãn tóm tắt bài LMS (`[LMS] Chưa có nội dung bài`)**:
    - Sinh thống nhất `[LMS] Chưa có nội dung bài` tại `evaluate_lms_slot_summary`, mở rộng regex & dedup trong `report_status.py`, `zalo_bcnb_service.py`, `report_repository.py`, `report_email_service.py`, `jobs.js` và giữ nguyên `services/bcnb_safety.py` để bảo toàn provenance `external_action_audit` với dữ liệu cũ `[LMS] Điền tóm tắt bài` / `[LMS] Chưa ghi nội dung bài học`.
- Consequences: Email Quản lý và file CSV gọn 8 cột, phân biệt rạch ròi lỗi nhận xét (đỏ `#dc2626`) với lưu ý SPCK (vàng cam `#b45309`), chấm dứt cảnh báo kép `[SPCK] Chưa nộp` + `[SPCK] Kiểm tra tay` và giữ nguyên tương thích ngược provenance.
- Evidence: Bộ kiểm chứng cô lập (`791/791 OK`, `80.4%` branch coverage, Ruff `0`, Phase 2–9B `PASS`, `phase3-app-smoke-ok`, `template-js-contract-ok scripts=8 handlers=48 behavior=14`, `compileall` `exit 0`); Review độc lập `Neon XHigh` (`@verifier`, `#6`, **`VERDICT: PASS`**, `mypy` `0` lỗi trên `80` files).
- Supersedes: Tinh chỉnh trình bày Email/CSV 9 cột của `DEC-038`, cách phân nhóm SPCK trong Email của `DEC-042` / `DEC-046`, và câu chữ tóm tắt bài học LMS của `DEC-050`.

## DEC-057 — Kiểm tra xếp hạng Top 1–2–3 đủ họ tên học viên ở ô Nội dung buổi học Buổi 14 LMS (mọi khóa, mức `NOTE`, 0 PII)

- Date: 2026-11-25.
- Status: Accepted by Owner, thực hiện và kiểm chứng source/local theo `PROJECT_STATE.md` (`[FULL GATE]`).
- Context: Theo quy định vận hành mới (ví dụ lớp `VT-JSB16`), ở **Buổi 14**, giảng viên cần ghi xếp hạng `1 / 2 / 3` (hoặc `Giải nhất / nhì / ba`, `Top 1 / 2 / 3`, `Hạng 1 / 2 / 3`, `🥇🥈🥉`, `<ol><li>`) kèm **đủ họ và tên học viên** trong ô Nội dung buổi học trên LMS (`slot.summary`). Trước đó `evaluate_lms_slot_quality` bỏ qua hoàn toàn `slot.summary` ở Buổi 14 (`has_valid_summary = True`), và dữ liệu trình duyệt `formatItemSlots` lại khử PII tên học viên trước khi trả về Python nên Python không thể tự đối chiếu đủ họ tên nếu không có tín hiệu phân tích từ trình duyệt.
- Decision:
  - **Phạm vi & Chốt chặn an toàn (`services/lms_quality_service.py`)**:
    - Chỉ áp dụng cho **Buổi 14** (`is_lesson_14`), áp dụng cho **mọi lớp** (kể cả `PTB`, `CSB`, `Robotics`, `Coding`); Buổi 1–13 giữ nguyên `evaluate_lms_slot_summary` (`[LMS] Chưa có nội dung bài`).
    - Ngưỡng yêu cầu tự động theo sĩ số đi học đã xác minh (`attendance.status == "VERIFIED"`): `required_ranks = min(3, attended_count)`. Khi `MISSING_ATTENDANCE` vẫn giữ duy nhất `[LMS] Chưa điểm danh` (`DEC-051`); khi `EXEMPT` (cả lớp nghỉ) hoặc `UNVERIFIED` không sinh ghi chú thiếu xếp hạng.
  - **Phân tích xếp hạng 0 PII tại trình duyệt + Đối xứng Python (`analyzeLesson14Ranking` / `evaluate_lms_lesson_14_ranking`)**:
    - Trong `formatItemSlots` (JS trình duyệt), trước khi gọi `scrubStudentNames` trên `s.summary`, chạy `analyzeLesson14Ranking` cho mọi slot Buổi 14 (`index === 13 || lesson == 14`) đối chiếu danh sách họ tên đầy đủ của lớp (`classStudentNames`), hỗ trợ `<ol><li>` (bỏ qua `<li>` rỗng đồng bộ giữa JS và Python), các định dạng `1./2./3.`, `1-/1:/1)`, `Giải nhất/nhì/ba`, `Top 1/2/3`, `No. 1/2/3`, `#1/2/3`, `Hạng 1/2/3`, `🥇🥈🥉`, chặn nhận nhầm tiêu đề `Top 3 ...` (chỉ áp dụng cho `top` không có dấu tách mục hoặc đứng trước rank 1/2, giữ nguyên `Top 3: Bạn/Học viên/HS <Tên>`) và lọc cụm từ dàn ý bài học (`on tap`, `thuyet trinh`, `hoan thanh`, `binh chon`, `tong ket`...) **trước** bước khớp tên rút gọn.
    - Chỉ trả về trường không chứa PII `lesson14Ranking: { presentRanks, fullNameRanks, incompleteNameRanks, hasExplicitRanking, rosterNameCount }`, giữ nguyên `summary` đã khử PII, đặt `summary_clean = ""` ở Buổi 14 và truyền `lesson_summary = ""` cho `ai_reviewer` của SPCK ở Buổi 14 để không bao giờ lộ tên học viên.
  - **Mức độ `NOTE` & Câu chữ chuẩn (`services/report_status.py`, `services/zalo_bcnb_service.py`, `repositories/report_repository.py`, `services/report_email_service.py`, `static/js/jobs.js`)**:
    - Phân loại vào `ISSUE_CODE_LMS_SUMMARY_NOTE` (`SEVERITY_NOTE`): không đánh dấu `is_failed=True`, không tô đỏ `❌` trong Email Quản lý, không chặn tick `NX LMS` trên BCNB và giữ nguyên nhóm cảnh báo vàng SPCK-only khi đi kèm lỗi SPCK.
    - Câu chữ: **`[LMS] Thiếu xếp hạng`** (trống, chỉ ghi tên bài/dàn ý, hoặc thiếu hạng cần thiết trong khi các hạng đã ghi đều đủ họ tên) và **`[LMS] Thiếu xếp hạng, chưa đủ họ tên`** (khi có hạng đã ghi nhưng thiếu họ/tên đệm hoặc tên chưa khớp đầy đủ); đồng bộ khóa dedup `[lms:lesson14_ranking]` và khóa họ trì hoãn `family:lms_summary`.
- Consequences: QC nhìn thấy ngay trên Dashboard và bảng Teaching lớp Buổi 14 nào chưa ghi xếp hạng 1–2–3 hoặc ghi thiếu họ tên học viên mà không báo đỏ sai vào Email Quản lý và không làm rò rỉ dữ liệu cá nhân học viên.
- Evidence: `.\CHECK_QUALITY.ps1 -Isolated` (`QUALITY GATE PASSED`, `exit 0`, `793/793 OK` trong `307.912s`, `80.5%` branch coverage, Ruff `0`, Mypy `0` trên `80` files, `secrets=0`, Phase 3–9B `PASS`, `phase3-app-smoke-ok`, `template-js-contract-ok scripts=8 handlers=49 behavior=14`); Review độc lập `Neon XHigh` (`@verifier`, `#6`, **`VERDICT: PASS`**, `0` P0/P1/P2, đối chiếu song song `21` edge cases Python–JS đạt `100%`).
- Supersedes: Mở rộng phần kiểm tra `slot.summary` ở Buổi 14 (giữ nguyên miễn nhận xét học viên Buổi 14 trong `DEC-038`, `DEC-043`, `DEC-051`).
