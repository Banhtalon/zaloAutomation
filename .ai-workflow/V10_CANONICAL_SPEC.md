# QQ AI Workflow v10 — canonical local contract
Version 10.1.0-rc.9 (supporting v10 and v10.1 tasks). This revision intentionally replaces v9 for adopted v10 tasks.
Scope: one Owner, local personal projects, subscription access. Not a production
or adversarial agent isolation framework. CLI connection is a separate stage.

## Authority and scope
Explicit Owner intent > this spec > agreed task contract > project profile >
task/evidence > agent suggestions. Changes to product scope require Owner input.
Lead may choose implementation details and strengthen tests without Owner involvement.
Any contract change starts a new revision, records why, invalidates evidence/review;
never silently weaken gates. A task uses one workflow version throughout a revision.
Legacy v9 rules apply only to explicitly unconverted host tasks.

## Current entry and Owner workflow
For new work, the default operational route is Direct: Gemini 3.8 Flash High
implements, the controller runs gates, Luna Max independently reviews, then Owner
accepts. Existing frozen tasks retain their own version, policy and budgets; the
V1 schema discriminator used by init is not a request for Owner to choose a lane.
New tasks do not use Fast Lane, V2, GEMINI_FIRST or LOCAL_AUTO. The historical
runtime is available only to inspect, reconcile or resume its frozen packets.
HANDOFF.md is the current entry, linking this specification and explaining how
to find the active local packet and its own tracked progress checklist. Guides and checklists record execution,
not additional workflow rules. Owner-facing stages are assigned, implementing,
checking, awaiting acceptance, awaiting integration and integrated; blocked work
includes a reason and next actor. Internal state codes remain unchanged.

Before dispatch, Lead checks required review source, secret filtering and full
packet size using existing tools. A known oversized or otherwise unsuitable Direct
review is not dispatched. Within Owner-authorized scope Lead may select ASSISTED
before dispatch, record and announce the technical reason without asking Owner to
choose execution mechanics. Lead implements/tests; a fresh independent reviewer
reads the exact committed source and evidence, preferring Luna when available.
ASSISTED does not claim a Direct invocation or invent provider receipts/metadata.
Direct retains its 256 KiB cap and all required context and credential controls.
ASSISTED review may read the pinned source in parts rather than construct a Direct
prompt. Its contract freezes scope, gates, risk and a finite repair budget first.

After a Direct dispatch, permission/scope failures, quota exhaustion, uncertain
execution or exhausted budgets cannot trigger ASSISTED replay. Preserve the
checkpoint and reconcile; changing task/revision/route does not reset budgets.
Account permissions, cost, product scope, live data, integration and publication
still require the applicable Owner authorization. Integration is separate from
acceptance: DONE/COMPLETED records workflow completion, not proof of integration
or publication. Lead records the verified destination commit after authorized
integration; missing evidence remains unverified.

Handoffs bind task/revision, branch, base/final head, contract digest, write/read
scope, exclusions, exact checks with cwd, remaining budget, stop conditions,
evidence paths and next actor. Implementers report changes and actual checks,
never self-approve. Reviewers do not edit or recursively delegate; they return
PASS/NEEDS_FIX/BLOCKED with location, impact and reproduction for material findings.
A code change invalidates old-head review. A later progress-only documentation
commit identifies the reviewed source commit separately and does not claim a new
code review. Missing observed model/usage remains unavailable.

Reports describe Product Check as not applicable only for a matching frozen
internal task explicitly declaring it non-applicable. Unreadable, conflicting or
unverified records remain unverified. Review success requires matching evidence,
independence, PASS and no material findings. ASSISTED reports disclose absent
bridge receipts. Reporting never changes saved state or authorizes integration.

## Document hierarchy
This specification is the sole source of workflow rules. Supporting documents may
describe commands, file formats, recovery steps or Owner-facing examples, but cannot
add, remove or reinterpret workflow rules. When a supporting guide appears to add a
rule, the documentation checker reports it as an advisory; revise this specification
in a new task revision before treating that guidance as a rule.

## Responsibilities
Owner: describe behavior, decide product tradeoffs, account-only actions,
functional acceptance, final merge approval. No code, logs, SQL or CI judgment.
LEAD: single contact; reads current files/state, plans briefly, classifies risk,
chooses implementer, operates tools, may code, collects evidence, coordinates repair.
IMPLEMENTER: one bounded feature; no self-approval, no scope/gate changes.
REVIEWER: fresh independent session, not involved in any implementation of the
feature (including its design); assess contract, diff, tests and risk against exact head and contract hash.
Different provider preferred; a fresh separate session on the same model is allowed
when competent. Identity is recorded, not authenticated by this local kit.
Technical handoffs between agents may use English. The Lead communicates every
Owner-facing result, blocker and next action in plain Vietnamese while preserving
model names, error codes, file names, commands and data keys. Technical reports are
source material; the Lead does not paste untranslated prose into the Owner report.

## Execution identity vocabulary
Workflow responsibility and execution identity are separate concepts and must not be
collapsed in packets or Owner reports:
- **role**: workflow responsibility such as `worker`, `senior`, `reviewer` or `elevated_reviewer`.
- **provider**: account/service family used by the CLI, such as `google` or `openai`.
- **CLI**: executable interface actually invoked, such as `antigravity` (`agy`) or `codex`.
- **requested_model**: model ID selected by the frozen bridge/configuration before invocation.
- **observed_models**: model IDs actually reported by the provider/CLI during the invocation.
- **session_id**: provider/CLI session identity returned by the invocation when available.
- **usage**: provider-reported counters, or `null` when unavailable.

Owner-facing reporting must distinguish requested identity from observed identity and
must never infer an observed model from a configuration value. A CLI name is not a
model name, and a provider name is not a role.

## Per-feature loop
1. Read host task state; protect uncommitted work. Create one feature branch/checkpoint.
2. Write a short contract: behavior, exclusions, base SHA, acceptance criteria, gates,
   user_visible and risk/complexity. Freeze before implementation.
   Lead checks each gate against the behavior the Owner requested before freezing:
   name the user action, observable success and a plausible wrong result the gate
   rejects. For interactive input, cover a material invalid or incomplete input
   when applicable. Do not add exact wording or presentation requirements that
   the Owner did not request. If no executable gate can distinguish the cases,
   record the manual check and its limit. A frozen task's gates and budget are not
   changed to rescue a failed run; contract changes need the normal new revision.
   Browser gates wait for the destination to render before checking visibility;
   a plausible slow render must not be reported as missing content. For a local UI
   that depends on remote styling or assets, check its readable layout when those
   resources do not load. A no-overflow assertion alone does not prove layout.
   Lead views the rendered result at relevant screen sizes before Owner handoff.
   Product copy must be supported by Owner intent or existing source; do not invent
   detailed gameplay or other product behavior to fill an introduction page.
   For a new user-visible Direct task, Lead supplies a local acceptable fixture and
   a plausible wrong fixture. Before freezing a packet, Direct `prepare` runs each
   frozen gate on both: all gates must pass the acceptable fixture and at least
   one must reject the wrong fixture.
   It also runs the actual Product Check command on the acceptable fixture and
   validates its complete result, including evidence for every frozen criterion
   and action. `check` repeats this no-model probe before dispatch. A failed probe
   creates no new packet or worker attempt. If representative fixtures cannot be
   prepared, Lead records the reason and selects ASSISTED before any Direct dispatch.
   Previously prepared tasks retain their frozen route and requirements.
3. Use one writer. Lead works or hands off via shared files and available tools.
4. Run relevant checks during development. No mandatory full suite for every tiny edit.
5. At feature completion, commit code, inspect actual diff, run agreed final gates.
6. Reviewer checks that head. Material issues go directly to implementer.
   A failed gate is a code repair request only when the reviewer can identify a
   concrete source defect that explains it. If the gate result conflicts with the
   source or the cause is uncertain (including when the missing gate source is
   needed to decide), return BLOCKED for Lead diagnosis; do not spend a Gemini
   repair on an unverified gate.
   Lead checks the frozen gate and browser observation without changing the task's
   budget or silently weakening the gate. Existing revision/recovery rules apply.
7. Rerun affected checks; refresh final evidence and review for the final head.
8. Present a local build with 1–3 ordinary user actions for small changes, more when
   needed to cover the behavior. Record Owner acceptance from the Lead chat.
9. Completion requires evidence, independent PASS, and acceptance if user-visible.
   Merge/publish remains a separate explicitly authorized action.

## Small-project execution and reporting
For new work, group related changes into one small, independently acceptable
feature. Under the existing eligible Google-worker route, the worker handles
implementation, relevant tests and bounded repairs. Lead checks the decisive
source before freezing scope, risk and gates; worker reconnaissance is advisory.
Lead intervenes at completion, a blocker, a budget boundary or a decision request,
rather than repeatedly requesting progress. This adds no routing policy, changes
no model binding, and does not make elevated work automatically worker-eligible.

During implementation run affected tests; at completion run the agreed final
gates. After repair refresh affected checks and final-head evidence/review. Never
select gates by filename alone, weaken a frozen gate set, or reuse old-head PASS.
Host projects need their own agreed tests, not this kit's internal test suite.

A reportable checkpoint is `DONE`, `READY_FOR_OWNER`, `BLOCKED_TECHNICAL`,
`WAITING_QUOTA` or `WAITING_CAPABILITY`. Once one is persisted, Lead must generate
its current Owner report and present it in the Owner chat. A report-generation
failure blocks an Owner-facing completion claim, while leaving the persisted
checkpoint unchanged. The same reporting requirement applies to ASSISTED work;
when no bridge receipt exists, the Lead report identifies that absence rather than
inventing execution data.

For `bridge.mjs pilot`, `run` and `resume`, the read-only report is generated
automatically after a reportable checkpoint. Its single JSON response adds
`reports.owner_markdown` for Owner delivery and `reports.lead` for technical
coordination. This derived step calls no provider and writes no packet or receipt.
Running and repair statuses have no automatic report. If report generation fails,
the CLI returns the core status JSON with a redacted `report_error` and a non-zero
exit code; it does not rewrite the saved checkpoint.

The read-only bridge report is a derived, redacted summary, not a verifier,
acceptance record or activation proof. Owner output describes progress, blockers
and the next product action in plain language, including who acts next. Outside
the automatic bridge result, Sol/Lead runs the deterministic report command and
presents the Owner version; Owner is not expected to run the CLI or inspect
packets. Lead output adds packet references, counters and provider-reported usage.
Each formatted Owner or Lead report is bounded to 8 KiB UTF-8, including its
truncation notice and references. Omitted detail must be disclosed; blockers
cannot silently disappear. Missing usage remains unavailable, not zero, and bytes
are not token or subscription-quota measurements. Requested model and observed
model remain distinct. Full source and gate evidence remain available and mandatory
for review under the existing contract.

Resume uses the existing checkpoint and preflight. An uncertain in-flight action
requires reconciliation, not automatic replay. A summary of saved work does not
promise native provider-session memory restoration. Existing frozen tasks,
repair budgets, FAST eligibility, Product Check, independent review, Owner
acceptance and merge/publish authorization remain unchanged.

## Routing and budgets
Risk LOW/ELEVATED is potential harm; complexity SIMPLE/COMPLEX is reasoning effort.
Auth, permissions, migrations, data destruction, privacy, credential handling or
deployment changes elevate risk even if one line. Lead inspects content and behavior;
path heuristics alone cannot certify safety. Risk cannot decrease within a revision.
Simple low-risk work: configured fast implementer on the configured Google worker route.
Complex or elevated work: configured senior-capable agent; independent competent
reviewer. Model IDs/effort must be discovered and tested on the Owner's account.
These defaults remain in force for existing frozen tasks. A new contract may opt
into `execution.policy=GEMINI_FIRST_V1`. The literal policy name is retained for v10
schema compatibility; it means the configured Google worker is preferred and does not
imply use of the legacy Gemini CLI. Its `prepared`, `local_synthetic`, `rationale`,
`design_sessions` and `browser_required` fields are frozen with it.
Lead inspects actual code before setting prepared; it means the implementation
approach, scope and checks are settled. Prepared work uses the worker by default,
including complex work; elevated work additionally requires local synthetic data.
Unprepared complex/elevated work uses senior. Elevated tasks in this policy require
an explicitly configured elevated reviewer, never a silent ordinary-review fallback.
Google-worker bridge configurations include that reviewer even for initially LOW
tasks so later risk elevation does not require changing the checkpoint configuration.
Google-worker review records bind effective risk, reviewer tier and the configured
reviewer binding digest. Risk elevation needs a fresh elevated review even at the
same head. Cached readiness is revalidated; material findings go to bounded repair,
not to another approval attempt without implementation repair.
Tasks adopting versioned schema `qq.workflow.task.v10.1` may declare
`execution.policy=CONTROLLED_DELEGATION_V1` or `execution.policy=CONTROLLED_DELEGATION_V2`.
This policy family is subscription-only.
The V1 policy uses a designated Gemini 3.8 Flash High worker, an independent ordinary
reviewer bound to Gemini 3.8 Flash High through a guarded Antigravity no-tools agent,
and exactly `gpt-6-astra` at low effort for senior escalation or elevated review.
Terra High is an explicit reviewer fallback frozen in the task configuration only when the Gemini reviewer cannot be
invoked; Gemini findings never trigger that fallback. Astra never escalates to Astra;
an unavailable elevated reviewer returns WAIT/STOP and never falls back to Terra.
The V2 policy preserves verification quality while conserving Codex tokens: it uses Sol Medium as
Lead, Gemini 3.8 Flash High as primary survey/implementation/test/repair worker, GPT-5.6 Luna Max
as standby fallback worker, Gemini 3.8 Flash High through a guarded Antigravity no-tools agent as
independent ordinary reviewer, GPT-5.6 Terra High as the explicit reviewer fallback only when
Gemini cannot be invoked, GPT-5.6 Sol Medium as senior, and a fresh independent GPT-5.6 Sol Medium
session as elevated reviewer. Lead and senior
participants cannot serve as reviewer for that feature. Model IDs and reasoning effort are verified
via CLI and cannot be substituted silently. V2 allocates one initial worker attempt plus four shared
repair rounds between Gemini and Luna. Fallback from Gemini to Luna is permitted exclusively on
invocation or provider execution failures (unavailability, quota, connection, timeout, or invalid protocol
output); test failures and reviewer repair requests never trigger fallback. Scope violations,
permission denials, contract mismatches, or tampered evidence cannot trigger fallback and fail closed
with STOP/BLOCKED. Pre-handoff reconciliation verifies Gemini has terminated, reconciles workspace state,
and records a durable checkpoint before invoking Luna; Luna runs sequentially without parallel execution
and remains active worker for subsequent rounds. Failed invocations do not consume completed repair rounds
and are recorded in failed call history. If Luna is unavailable, WAIT/BLOCKED is recorded without falling
back to senior or paid APIs. Luna capabilities are probed only when fallback is needed. If material failure
persists after four repair rounds, Sol senior has at most two passes (one initial pass and one follow-up
repair pass); tasks starting at senior also have at most two senior passes without worker budget. Budget
exhaustion with unresolved issues stops with BLOCKED_TECHNICAL.
The shipped new-task template and `workflow.mjs init` use `CONTROLLED_DELEGATION_V1`.
For Owner-authorized direct local work, the Lead uses `scripts/direct.mjs` with the
existing task/config contract and MCP lifecycle, without a Luna task as a CLI runner.
This entrypoint has a bounded per-task budget for newly prepared tasks: one worker
execution, at most three same-process repairs and four independent reviews. Previously
frozen Direct tasks retain their one-repair, two-review budget. It stops on failures or
exhausted budget; a new task/revision does not authorize another attempt. Worker file scope is
exact; the controller runs gates, and the worker must not run shell/gates or access
control files. The direct workflow requires a Gemini 3.8 Flash High MCP worker to
implement, controller-run gates to test, an independent OpenAI Codex model gpt-6-luna
at effort max (Luna Max) review for newly prepared Direct tasks, then Owner acceptance
at the checkpoint. Previously frozen Direct tasks keep their gpt-5.6-luna binding. Luna is
reviewer only, never worker or runner. The direct entrypoint does not accept or dispatch
a fallback reviewer. PASS stops at the Owner checkpoint; only explicit Owner acceptance
permits `accept`. Forced termination requires manual permission/workspace reconciliation
before any new dispatch. For a task with `user_visible=true` or an applicable Product
Check contract, review PASS proceeds to the frozen Product Check before that checkpoint.
Missing, unavailable, timed-out or malformed evidence remains `PRODUCT_CHECK_WAIT`;
a functional failure blocks. `verify-product` repeats only this check after revalidating
the unchanged reviewed source and never dispatches a worker or reviewer. Checkpoint and
completion revalidate the full declared review source and bound Product Check evidence.
Review packet v2 includes changed base/head source plus declared gate/context source and
rejects a complete reviewer prompt over 256 KiB. See `DIRECT_GEMINI.md`.
`GEMINI_FIRST_V1` remains supported for legacy task compatibility rather than as the
default for newly initialized tasks.
The controlled lanes are FAST, NORMAL and ELEVATED_PROCESS; risk remains LOW or
ELEVATED. FAST is documentation-only with a frozen allowlist and a bound waiver;
out-of-scope or behavioral content stops with SCOPE_VIOLATION. User-visible tasks
need a frozen local Product Check; unavailable Product Check is UNVERIFIED/WAIT and
does not consume implementation repair budget. Controlled repair budgets are tracked
by origin and policy and never reset by a new scope revision; supplemental recovery requires
explicit Owner authorization and a dedicated budget ledger.
For every controlled invocation, the Bridge writes an assignment receipt before
invocation and an execution receipt on every terminal path. Raw invocation identity
is authoritative; candidate custody references its chain root, receipt ID and hash.
Missing provider metadata stays null or unavailable; matching fields that conflict
fail closed. A valid later chain append does not invalidate an earlier receipt.
Design participants cannot review that feature. Lead may obtain at most one senior
design consultation before freezing the contract and records its session identity;
consultation does not reset or extend implementation repair budgets. Missing design
decisions after that consultation are a technical blocker or a product question.
The intended execution profile is a capable Lead, configured Google fast worker,
configured senior, ordinary reviewer and independent elevated reviewer. Exact CLI IDs,
model IDs and effort require capability probes; product names are preferences, not
evidence of capability.
Two repair rounds at the initial tier, then at most one senior implementation pass.
Any unresolved material failure after that => BLOCKED_TECHNICAL, preserved checkpoint.
A reviewer finding causes repair, not a debate loop. A new scope revision must not
be invented to reset the budget. Quota/auth failure pauses, never counts as success,
never triggers paid API fallback. Switching eligible providers preserves counters.
Reviewer runs have no recursive delegation. Default one concurrent writer.

## State and evidence
Local JSON packets are the working state; GitHub records meaningful milestones only.
Contract digest detects accidental edits; it is not a secure external authority store.
Verification records base/head/hash, actual argv, exit codes, timeouts and redacted
output. A process exit 0 alone is not product acceptance. Review and Owner acceptance
bind to the same head/hash. A later edit invalidates them.
An elevated-risk review records completed risk checks and their result in its summary.
Fresh-context review is organizational independence, not OS-level isolation.
For Google-worker-first user-visible tasks with browser_required, readiness also requires
ui_evidence containing matching head/contract hash, a localhost URL, PASS status
and nonempty checks with action, observed result and passed=true. Missing evidence
is WAITING_CAPABILITY, not a repair request or Owner acceptance. Lead verifies the
running build and records actual browser observations; JSON alone is not proof.
New v10.1 tasks may freeze `execution.product_evidence_storage=single_file_v1`.
For the controlled bridge, Product Check evidence is stored only in packet
`product_check.json`; reports, readiness, resume and pilot verification read that
file. Missing, unreadable, mismatched or additional legacy Product Check records
remain unverified. Tasks without this frozen marker keep their existing evidence
format and conflict checks. Direct already uses its own `product-check.json` and
verifies it against the saved checkpoint; this marker does not change that route.
The marker does not change Product Check criteria, model bindings, review, repair
budgets or Owner acceptance.
Owner sees the local link, short steps and status in Lead chat, never a requirement
to inspect code/SQL/logs. UI changes invalidate old UI evidence with the head.
Usage records retain provider-reported counters or null when unavailable; API price
does not establish subscription quota. Full redacted gate evidence remains available.
The packet checker cannot prove a human/model identity, detect fabricated JSON,
or enforce all transitions. Lead must retain genuine execution/review records.
For source inspection, Lead may predeclare exact test-file paths and SHA-256
content approvals for personally inspected synthetic test data in bridge config.
Their JSON digest is frozen as execution.source_approvals_sha256 in the task and
checked against the config before work. The checkpoint also binds configuration;
changed bytes invalidate them. No directory-wide secret exemption is supported.
Recognizable credential formats and current secret environment values remain blocked
even with an approval. Runtime output and argument redaction remain unchanged.
Review packets persist the exact inspected source, base/head content hashes,
declared context and gate sources alongside the source digest. Missing required
source context or oversized packets stop review. Technical review may pass before
browser evidence exists; that absence alone is a readiness wait, not a code defect.
Readiness and activation recheck persisted bridge source against its recorded
digest and task/configuration bindings. New tasks can freeze
execution.review_source_required=true to reject reviews without this source proof.
Provider result fields cannot override Lead-recorded identities or evidence bindings.

## Retired Fast Lane
Fast Lane no longer accepts new work. Its historical rules and source remain at
commit `fc5c5bc587714244ed71f1da956fb9fc8fd673f0` for audit or recovery of
a frozen task. Do not reclassify a frozen task or reset its budget.

## Safety and operational boundaries
Keep credentials in official account stores; never copy them into task files.
Run gates only from a trusted local project. Tools are not a sandbox.
Local auth/database code may be implemented/tested with synthetic data and elevated
review. Existing live writes, migration execution, deletion and publishing restrictions
remain in force. Confirm exact target and existing authorization before external writes.
Owner approves intent/consequences, never technical waivers. If safe resolution is
unavailable, stop with a plain-language blocker and keep the working version.
No hosted, production, or live acceptance claim from local tests.

The supervised Antigravity local trial is an explicit opt-in only: `worker.local_trial`
defaults to false, requires `worker.model=gemini-3.8-flash-high` and an absolute
`worker.local_trial_root` outside the repository and Harness/control roots, and must
not be combined with `test_mode` or `skip_permissions`. The bridge passes sandbox,
accept-edits and a unit-bearing timeout to the CLI, but does not infer provider
permission metadata from those requested flags: `accept-edits` is an execution mode,
while `request-review` is the documented default permission mode. The provider must
report the requested model and pass protocol/status/exit checks; nullable permission
and sandbox metadata remains observational, with missing or unknown values kept as
unknown. Explicit `always-proceed` or `sandbox=false` is rejected, while file
snapshot/diff evidence is still required for trial acceptance.
Post-spawn validation rejects an unsafe result but cannot undo side effects, so every
trial records a bounded redacted init/result/tool evidence summary and a workspace
snapshot/diff before and after execution, including rejection and timeout. This trial
proves neither OS isolation nor production readiness; the default path remains
fail-closed.
The parsed stream is checked before evidence is bounded: when a proven `view_file`
step reports `AbsolutePath`, it must be an absolute canonical path contained by the
resolved workspace. Relative, drive-relative, UNC, parent-traversal and reparse/junction
paths are rejected with `WORKER_SCOPE_VIOLATION`. This is a post-run acceptance guard;
it cannot undo a read or other side effect and it does not claim to monitor every tool.
The code is non-retryable and preserves timeout, exit, model, conversation and bounded
evidence fields so a scope finding cannot hide a simultaneous execution failure.

## Stage boundary
The current stage is ASSISTED. Within it, new work uses Direct as defined above;
the pre-dispatch ASSISTED exception applies when Direct is known unsuitable.
LOCAL_AUTO is retired for new tasks. Its old activation and quota-drill controls
remain available in the pre-retirement commit for historical inspection. A frozen
legacy task may be inspected, reconciled or resumed under its original contract;
no new pilot, legacy run or activation is started from the current entrypoint.
