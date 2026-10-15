import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { access, mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { runBridge } from './bridge.mjs';
import { freezeControlledTask } from './controlled-bridge.mjs';
import { operationId } from './harness-errors.mjs';
import {
  LIFECYCLE_STATES,
  SAFE_STOP_STATES,
  approveCheckpoint,
  claimTask,
  completeTask,
  continueHarnessLifecycle,
  inspectTask,
  lifecycleStatus,
  readDesiredState,
  recoverTask,
  rejectCheckpoint,
  reconcileTask,
  requestChanges,
  resolveTaskWorktree,
  leaseSummary,
  pathsFor,
  runHarnessLifecycle,
  saveState,
  releaseLease,
  verifyProductCheck,
  buildReviewSource,
  buildReviewPrompt,
  validateLease,
  renewLease
} from './harness-lifecycle.mjs';

const git = (cwd, ...args) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
let serial = 0;

async function fixture({ maxRework = 3, reviewerVerdicts = ['PASS', 'PASS'], reviewerResult = null, temporaryPermissions = false, bindConfig = temporaryPermissions, productCheck = false, workerContext = null } = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'qq-harness-lifecycle-'));
  const productModePath = path.join(root, 'product-mode.txt');
  const productRunnerPath = path.join(root, 'product-runner.mjs');
  if (productCheck) {
    await writeFile(productModePath, String(productCheck));
    await writeFile(productRunnerPath, `import{readFileSync}from'node:fs';const mode=readFileSync(process.argv[2],'utf8').trim();if(mode==='timeout')setTimeout(()=>{},60000);else if(mode==='malformed')console.log('not-json');else{console.log(JSON.stringify(['fail','fail-exit'].includes(mode)?{status:'FAIL'}:mode==='invalid'?{status:'UNKNOWN'}:mode==='incomplete'?{schema_version:'qq.workflow.product-check-result.v1',status:'PASS',target_url:'http://localhost:4173',criterion_results:[],action_results:[]}:{schema_version:'qq.workflow.product-check-result.v1',status:'PASS',target_url:'http://localhost:4173',criterion_results:[{criterion_id:'criterion-001',status:'PASS',observed_result:'visible',evidence:'fixture'}],action_results:[{action_id:'action-001',status:'PASS',observed_result:'worked',evidence:'fixture'}]}));if(mode==='fail-exit')process.exitCode=1;}`);
  }
  git(root, 'init');
  git(root, 'config', 'user.email', 'harness-test@example.invalid');
  git(root, 'config', 'user.name', 'Harness Test');
  await writeFile(path.join(root, '.gitignore'), '.workflow-local/\n.worktrees/\n');
  await writeFile(path.join(root, 'README.md'), 'fixture\n');
  git(root, 'add', '.');
  git(root, 'commit', '-m', 'fixture base');
  const base = git(root, 'rev-parse', 'HEAD');
  const id = `TASK-TEST-${++serial}`;
  const worktreeRoot = path.join(root, '.worktrees');
  await mkdir(worktreeRoot, { recursive: true });
  const workspace = path.join(worktreeRoot, id);
  git(root, 'worktree', 'add', '--detach', workspace, base);
  const settingsPath = temporaryPermissions ? path.join(root, 'fake-settings.json') : null;
  const gateObservationPath = temporaryPermissions ? path.join(root, 'gate-observation.json') : null;
  const permissionObservations = [];
  if (settingsPath) await writeFile(settingsPath, JSON.stringify({ permissions: { allow: [], deny: [] }, marker: 'fixture' }, null, 2) + '\n');
  const packetRoot = path.join(workspace, '.workflow-local');
  await mkdir(packetRoot, { recursive: true });
  const taskPath = path.join(packetRoot, `${id}.json`);
  const task = {
    schema_version: 'qq.workflow.task.v10.1',
    task_id: id,
    revision: 1,
    base_sha: base,
    goal: 'test lifecycle',
    acceptance_criteria: ['create demo files in the frozen scope'],
    gates: [{
      id: 'tests',
      argv: [process.execPath, '-e', settingsPath
        ? `const fs=require('node:fs');const s=JSON.parse(fs.readFileSync(${JSON.stringify(settingsPath)},'utf8'));fs.writeFileSync(${JSON.stringify(gateObservationPath)},JSON.stringify(s.permissions.allow));`
        : 'process.exit(0)'],
      timeout_seconds: 20
    }],
    user_visible: Boolean(productCheck),
    risk: 'LOW',
    complexity: 'SIMPLE',
    execution: { policy: 'CONTROLLED_DELEGATION_V1' },
    write_paths: ['demo.py', 'test_demo.py'],
    allowed_paths: ['demo.py', 'test_demo.py'],
    lane: 'NORMAL',
    initial_lane: 'NORMAL',
    initial_risk: 'LOW',
    product_check: productCheck ? { applicable: true, target_url: 'http://localhost:4173', criteria: ['visible'], actions: ['worked'] } : { applicable: false, reason: 'test-only task' },
    candidate_head: null,
    contract_sha256: null
  };
  await writeFile(taskPath, JSON.stringify(task, null, 2) + '\n');

  const controlPath = path.join(root, '.workflow-local', 'ai-control.desired_state');
  await mkdir(path.dirname(controlPath), { recursive: true });
  await writeFile(controlPath, 'running\n');
  const config = {
    schema_version: 'qq.bridge.v2',
    billing: 'SUBSCRIPTION_ONLY',
    mode: 'ASSISTED',
    timeout_seconds: productCheck === 'timeout' ? 1 : 30,
    write_paths: task.write_paths,
    gate_paths: ['test_demo.py'],
    worker: {
      transport: 'mcp',
      server: 'antigravity_worker',
      provider: 'mcp',
      command: ['python', 'mcp/antigravity_server.py']
    },
    reviewer: {
      provider: 'openai',
      cli: 'codex',
      command: ['codex'],
      model: 'gpt-5.6-terra',
      effort: 'xhigh'
    },
    lifecycle: {
      worktree_root: '.worktrees',
      desired_state_path: '.workflow-local/ai-control.desired_state',
      lease_seconds: 300,
      max_rework: maxRework,
      checkpoint_required: true
    },
    test_mode: true
  };
  if (productCheck && productCheck !== 'missing') config.product_check = { command: [process.execPath, productRunnerPath, productModePath] };
  if (temporaryPermissions) {
    config.worker.temporary_permissions = {
      enabled: true,
      settingsPath,
      files: [path.join(workspace, 'demo.py'), path.join(workspace, 'test_demo.py')]
    };
  }
  if (workerContext) {
    config.review_context_paths = ['test_demo.py'];
    config.worker.context = { base_sha: base, paths: ['test_demo.py'], summary: workerContext };
  }
  await freezeControlledTask(taskPath, task, bindConfig ? config : null);
  const reviewerCalls = [];
  const reviewer = async ({ state, packet, prompt }) => {
    if (settingsPath) permissionObservations.push({ phase: 'review', allow: JSON.parse(await readFile(settingsPath, 'utf8')).permissions.allow });
    const verdict = reviewerVerdicts[Math.min(reviewerCalls.length, reviewerVerdicts.length - 1)];
    reviewerCalls.push({ verdict, attempt: state.attempt, packet, prompt });
    return {
      code: 0,
      session_id: `codex-review-${reviewerCalls.length}`,
      observed_models: ['gpt-5.6-terra'],
      usage: { input_tokens: 5, output_tokens: 3, total_tokens: 8 },
      result: reviewerResult ?? {
        verdict,
        summary: verdict === 'PASS' ? 'fixture review passed' : 'fixture review requests changes',
        material_findings: verdict === 'PASS' ? [] : ['requested fixture change'],
        risk_checks_completed: true
      }
    };
  };
  const resultFor = (currentTask, operation) => ({
    task_id: currentTask.task_id,
    status: 'SUCCEEDED',
    agent_status: 'SUCCESS',
    exit_code: 0,
    operation_id: operation?.id ?? null,
    invocation_kind: operation?.kind ?? null,
    attempt: operation?.attempt ?? null,
    rework_count: operation?.rework_count ?? null,
    conversation_id: 'agy-conversation-1',
    observed_model: 'antigravity',
    usage: { input_tokens: 11, output_tokens: 7, total_tokens: 18 },
    stderr: ''
  });
  const worker = {
    calls: [],
    prompts: [],
    resultCalls: 0,
    lastResult: null,
    resultOverride: null,
    async execute(currentTask, prompt, operation) {
      this.calls.push({ tool: 'antigravity_execute', task_id: currentTask.task_id });
      this.prompts.push(prompt);
      if (settingsPath) permissionObservations.push({ phase: 'execute', allow: JSON.parse(await readFile(settingsPath, 'utf8')).permissions.allow });
      await writeFile(path.join(workspace, 'demo.py'), 'VALUE = "ok"\n');
      await writeFile(path.join(workspace, 'test_demo.py'), 'assert True\n');
      this.lastResult = resultFor(currentTask, operation);
      return this.lastResult;
    },
    async continue(currentTask, instruction, operation) {
      this.calls.push({ tool: 'antigravity_continue', task_id: currentTask.task_id, instruction });
      if (settingsPath) permissionObservations.push({ phase: 'continue', allow: JSON.parse(await readFile(settingsPath, 'utf8')).permissions.allow });
      await writeFile(path.join(workspace, 'test_demo.py'), 'assert True\n# lowercase input coverage\n');
      this.lastResult = resultFor(currentTask, operation);
      return this.lastResult;
    },
    async result(currentTask) {
      this.resultCalls += 1;
      if (settingsPath) permissionObservations.push({ phase: 'result', allow: JSON.parse(await readFile(settingsPath, 'utf8')).permissions.allow });
      return this.resultOverride ?? this.lastResult ?? resultFor(currentTask, null);
    }
  };
  const options = { taskPath, config, owner: 'fixture-owner', worker, reviewerInvoker: reviewer };
  return {
    root, id, base, workspace, worktreeRoot, taskPath, controlPath, config, worker, reviewerCalls, permissionObservations, settingsPath, gateObservationPath, productModePath, options,
    async cleanup() { await rm(root, { recursive: true, force: true }); }
  };
}

async function waitForFile(file, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await access(file);
      return;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
  }
  throw new Error(`timed out waiting for ${file}`);
}

function waitForChild(child) {
  let stderr = '';
  child.stderr?.on('data', chunk => { stderr += chunk.toString(); });
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => resolve({ code, signal, stderr }));
  });
}

test('passing Product Check is bound before checkpoint and review packet includes declared source', async t => {
  const f = await fixture({ productCheck: 'pass', bindConfig: true });
  t.after(f.cleanup);
  const result = await runHarnessLifecycle(f.options);
  assert.equal(result.status, LIFECYCLE_STATES.WAITING_FOR_CHECKPOINT);
  assert.equal(result.product_check.status, 'PASS');
  assert.equal(f.worker.calls.length, 1);
  assert.equal(f.reviewerCalls.length, 1);
  const source = f.reviewerCalls[0].packet.review_source;
  assert.equal(f.reviewerCalls[0].packet.schema_version, 'qq.workflow.review-packet.v2');
  assert.deepEqual(source.changed_paths.sort(), ['demo.py', 'test_demo.py']);
  assert(source.files.some(file => file.path === 'test_demo.py' && file.content.includes('assert True')));
  await approveCheckpoint({ ...f.options, approvedBy: 'fixture-owner' });
  assert.equal((await completeTask(f.options)).status, LIFECYCLE_STATES.COMPLETED);
});

test('verified existing UI context reaches Gemini without widening the write scope and usage reaches state', async t => {
  const f = await fixture({ workerContext: 'Trang hiện có dùng bố cục xanh; nút đăng nhập nằm trong thẻ trung tâm.' });
  t.after(f.cleanup);
  const result = await runHarnessLifecycle(f.options);
  assert.equal(result.status, LIFECYCLE_STATES.WAITING_FOR_CHECKPOINT);
  assert.match(f.worker.prompts[0], /Verified existing-source context/);
  assert.match(f.worker.prompts[0], /bố cục xanh/);
  assert.match(f.worker.prompts[0], /Short task context/);
  assert.match(f.worker.prompts[0], /allowed_paths/);
  assert.deepEqual(result.latest_execution.usage, {
    source: 'mcp', input_tokens: 11, output_tokens: 7, reasoning_tokens: null, cached_tokens: null, total_tokens: 18
  });
  assert.deepEqual(result.review_result.usage, {
    source: 'openai', input_tokens: 5, output_tokens: 3, reasoning_tokens: null, cached_tokens: null, total_tokens: 8
  });
  assert.equal(result.history.filter(item => item.phase === 'review').at(-1).review_prompt_metrics.prompt_bytes,
    f.reviewerCalls[0].prompt.length ? Buffer.byteLength(f.reviewerCalls[0].prompt, 'utf8') : 0);
  assert.ok(result.history.filter(item => item.phase === 'review').at(-1).review_prompt_metrics.task_context_bytes > 0);
});

test('reviewer repair context is bounded and redaction-safe before another worker call', async t => {
  const tooLong = await fixture({ reviewerResult: { verdict: 'NEEDS_FIX', summary: 'needs change', material_findings: ['x'.repeat(3501)], risk_checks_completed: true } });
  t.after(tooLong.cleanup);
  const blocked = await runHarnessLifecycle(tooLong.options);
  assert.equal(blocked.status, LIFECYCLE_STATES.BLOCKED);
  assert.equal(tooLong.worker.calls.length, 1);
  assert.match(blocked.review_result.summary, /too long|bounded|redaction-safe/);

  const secret = await fixture({ reviewerResult: { verdict: 'NEEDS_FIX', summary: 'needs change', material_findings: ['token=fixture-secret-value'], risk_checks_completed: true } });
  t.after(secret.cleanup);
  const secretBlocked = await runHarnessLifecycle(secret.options);
  assert.equal(secretBlocked.status, LIFECYCLE_STATES.BLOCKED);
  assert.equal(secret.worker.calls.length, 1);
  assert.doesNotMatch(JSON.stringify(secretBlocked.review_result), /fixture-secret-value/);
});

test('Product Check waits without another AI call and verifyProductCheck can finish it', async t => {
  for (const mode of ['malformed', 'invalid', 'incomplete', 'timeout']) {
    const f = await fixture({ productCheck: mode, bindConfig: true });
    t.after(f.cleanup);
    const first = await runHarnessLifecycle(f.options);
    assert.equal(first.status, LIFECYCLE_STATES.PRODUCT_CHECK_WAIT, mode);
    assert.equal(f.worker.calls.length, 1, mode);
    assert.equal(f.reviewerCalls.length, 1, mode);
    await writeFile(f.productModePath, 'pass');
    const verified = await verifyProductCheck(f.options);
    assert.equal(verified.status, LIFECYCLE_STATES.WAITING_FOR_CHECKPOINT, mode);
    assert.equal(verified.product_check.status, 'PASS', mode);
    assert.equal(f.worker.calls.length, 1, mode);
    assert.equal(f.reviewerCalls.length, 1, mode);
  }
});

test('checkpoint rejects missing or replaced Product Check evidence file', async t => {
  for (const replacement of [null, { status: 'PASS', task_id: 'stale-task' }]) {
    const f = await fixture({ productCheck: 'pass', bindConfig: true });
    t.after(f.cleanup);
    await runHarnessLifecycle(f.options);
    const evidencePath = path.join(f.workspace, '.workflow-local', f.id, 'product-check.json');
    if (replacement) await writeFile(evidencePath, JSON.stringify(replacement));
    else await rm(evidencePath);
    await assert.rejects(() => approveCheckpoint({ ...f.options, approvedBy: 'fixture-owner' }), /fresh passing Product Check/);
  }
});

test('checkpoint rejects a modified review packet', async t => {
  const f = await fixture({ productCheck: 'pass', bindConfig: true });
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const packetPath = path.join(f.workspace, '.workflow-local', f.id, 'review-packet.json');
  const packet = JSON.parse(await readFile(packetPath, 'utf8'));
  packet.acceptance_criteria.push('tampered after review');
  packet.review_source.files[0].content += '\ntampered';
  await writeFile(packetPath, JSON.stringify(packet, null, 2) + '\n');
  await assert.rejects(() => approveCheckpoint({ ...f.options, approvedBy: 'fixture-owner' }), error => error.code === 'REVIEW_STALE');
});

test('missing Product Check command waits, functional failure blocks, and stale source cannot be verified', async t => {
  const missing = await fixture({ productCheck: 'missing', bindConfig: true });
  t.after(missing.cleanup);
  assert.equal((await runHarnessLifecycle(missing.options)).status, LIFECYCLE_STATES.PRODUCT_CHECK_WAIT);

  for (const mode of ['fail', 'fail-exit']) {
    const failed = await fixture({ productCheck: mode, bindConfig: true });
    t.after(failed.cleanup);
    assert.equal((await runHarnessLifecycle(failed.options)).status, LIFECYCLE_STATES.BLOCKED, mode);
  }

  const stale = await fixture({ productCheck: 'malformed', bindConfig: true });
  t.after(stale.cleanup);
  assert.equal((await runHarnessLifecycle(stale.options)).status, LIFECYCLE_STATES.PRODUCT_CHECK_WAIT);
  await writeFile(path.join(stale.workspace, 'test_demo.py'), 'assert True\n# changed after review\n');
  await writeFile(stale.productModePath, 'pass');
  await assert.rejects(() => verifyProductCheck(stale.options), /changed before Product Check/);
});

test('review source rejects missing and oversized declared context', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'qq-review-source-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  git(root, 'init'); git(root, 'config', 'user.email', 'review@example.invalid'); git(root, 'config', 'user.name', 'Review Source');
  await writeFile(path.join(root, 'app.mjs'), 'export const value = 1;\n');
  await writeFile(path.join(root, 'test.mjs'), 'assert(true);\n');
  await writeFile(path.join(root, 'package.json'), '{}\n');
  git(root, 'add', '.'); git(root, 'commit', '-m', 'base');
  await writeFile(path.join(root, 'app.mjs'), 'export const value = 2;\n');
  const task = { gates: [{ argv: [process.execPath, 'test.mjs'] }] };
  const changeset = { changed_files: [{ path: 'app.mjs', status: 'modified' }], diff: git(root, 'diff'), tests: [] };
  const source = await buildReviewSource(root, task, { gate_paths: ['test.mjs'], review_context_paths: [] }, changeset);
  assert(source.files.some(file => file.path === 'app.mjs'));
  assert(source.base_files.some(file => file.path === 'app.mjs' && file.content.includes('value = 1')));
  await assert.rejects(() => buildReviewSource(root, task, { gate_paths: ['missing.mjs'] }, changeset), /declared review context is missing/);
  await writeFile(path.join(root, 'large.mjs'), 'x'.repeat(256 * 1024 + 1));
  await assert.rejects(() => buildReviewSource(root, task, { gate_paths: ['large.mjs'] }, changeset), /bounded regular file|exceeds 256 KiB/);
  await writeFile(path.join(root, 'binary.mjs'), Buffer.from([0xff, 0xfe, 0xfd]));
  await assert.rejects(() => buildReviewSource(root, task, { gate_paths: ['binary.mjs'] }, changeset), /valid UTF-8 text/);
});

test('review prompt omits duplicate base content but keeps current code, diff, and deleted/renamed base files', () => {
  const packet = {
    review_source_sha256: 'f'.repeat(64),
    review_source: {
      changed_paths: ['app.mjs', 'deleted.mjs'],
      declared_context_paths: [],
      files: [{ path: 'app.mjs', content: 'export const value = "new";\n' }],
      base_files: [
        { path: 'app.mjs', content: 'export const value = "old";\n' },
        { path: 'deleted.mjs', content: 'export const removed = true;\n' }
      ],
      diff: '-old\n+new\n',
      sha256: 'f'.repeat(64)
    },
    review_input_sha256: 'e'.repeat(64),
    changed_files: [{ path: 'app.mjs', status: 'modified' }],
    tests: [{ id: 'fixture', code: 0 }]
  };
  const { prompt, metrics } = buildReviewPrompt({ task_id: 'TASK-PROMPT', acceptance_criteria: ['value is new'] }, packet);
  assert.match(prompt, /export const value = \\\"new\\\"/);
  assert.match(prompt, /export const removed = true/);
  assert.match(prompt, /-old/);
  assert.doesNotMatch(prompt, /export const value = \\\"old\\\"/);
  assert.deepEqual(metrics.omitted_base_paths, ['app.mjs']);
  assert.ok(metrics.compact_source_bytes < metrics.full_source_bytes);
  assert.equal(metrics.prompt_bytes, Buffer.byteLength(prompt, 'utf8'));
});

test('claim success and deterministic second claim rejection', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'qq-claim-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const taskPath = path.join(dir, 'TASK-CLAIM.json');
  await writeFile(taskPath, JSON.stringify({ task_id: 'TASK-CLAIM' }));
  const first = await claimTask(taskPath, { owner: 'one', now: () => 1_000 });
  const second = await claimTask(taskPath, { owner: 'two', now: () => 1_001 });
  assert.equal(first.status, LIFECYCLE_STATES.CLAIMED);
  assert.equal(second.status, 'ALREADY_CLAIMED');
  assert.equal(second.claim.owner, 'one');
});

test('expired and wrong-owner leases are rejected; renewal extends lease', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'qq-lease-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const taskPath = path.join(dir, 'TASK-LEASE.json');
  await writeFile(taskPath, JSON.stringify({ task_id: 'TASK-LEASE' }));
  const claim = await claimTask(taskPath, { owner: 'one', leaseSeconds: 1, now: () => 10_000 });
  await assert.rejects(() => validateLease(taskPath, { owner: 'two', token: claim.lease.token, now: () => 10_500 }), /another owner/);
  const renewed = await renewLease(taskPath, { owner: 'one', token: claim.lease.token, version: claim.claim.version, leaseSeconds: 5, now: () => 10_500 });
  assert.equal(renewed.status, 'LEASE_RENEWED');
  assert.equal(Date.parse(renewed.claim.lease_expires_at), 15_500);
  assert.equal(renewed.claim.version, claim.claim.version);
  await assert.rejects(() => validateLease(taskPath, { owner: 'one', token: claim.lease.token, now: () => 15_501 }), /expired/);
  const reclaimed = await claimTask(taskPath, { owner: 'two', leaseSeconds: 5, now: () => 15_501 });
  assert.equal(reclaimed.status, LIFECYCLE_STATES.CLAIMED);
  assert.equal(reclaimed.reclaimed, true);
  assert.equal(reclaimed.claim.version, 2);
});

test('desired state allows running and blocks paused/stopped without writing it', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  const before = await readFile(f.controlPath, 'utf8');
  assert.equal((await readDesiredState(f.root, f.config)).state, 'running');
  await writeFile(f.controlPath, 'paused\n');
  const paused = await runHarnessLifecycle(f.options);
  assert.equal(paused.status, 'PAUSED');
  assert.equal(f.worker.calls.length, 0);
  assert.equal(await readFile(f.controlPath, 'utf8'), 'paused\n');
  await writeFile(f.controlPath, 'stopped\n');
  const stopped = await runHarnessLifecycle(f.options);
  assert.equal(stopped.status, 'PAUSED');
  assert.equal(f.worker.calls.length, 0);
  assert.equal(await readFile(f.controlPath, 'utf8'), 'stopped\n');
  await writeFile(f.controlPath, before);
});

test('normal lifecycle routes through MCP worker abstraction and does not spawn agy directly', async () => {
  const lifecycleSource = await readFile(new URL('./harness-lifecycle.mjs', import.meta.url), 'utf8');
  const bridgeSource = await readFile(new URL('./bridge.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(lifecycleSource, /\bspawn\s*\(/);
  assert.doesNotMatch(lifecycleSource, /['"]agy['"]/);
  assert.match(bridgeSource, /worker\?\.transport==='mcp'/);
  assert.match(bridgeSource, /runHarnessLifecycle/);
});

test('worktree resolver maps task_id and rejects traversal and symlink escape', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  const resolved = await resolveTaskWorktree(f.id, { task: { task_id: f.id, base_sha: f.base }, taskPath: f.taskPath, repoRoot: f.root, config: f.config });
  assert.equal(path.resolve(resolved.workspace), path.resolve(f.workspace));
  await assert.rejects(() => resolveTaskWorktree('../outside', { taskPath: f.taskPath, repoRoot: f.root, config: f.config }), /invalid task_id/);
  const outside = path.join(f.root, 'outside');
  await mkdir(outside);
  const escaped = path.join(f.worktreeRoot, 'TASK-LINK');
  try {
    await symlink(outside, escaped, 'junction');
    await assert.rejects(() => resolveTaskWorktree('TASK-LINK', { taskPath: f.taskPath, repoRoot: f.root, config: f.config }), /real directory|outside|escape/);
  } catch (error) {
    if (!['EPERM', 'EACCES'].includes(error.code)) throw error;
  }
});

test('local trial resolver uses an external absolute root and rejects repository overlap', async t => {
  const f = await fixture();
  const trialRoot = await mkdtemp(path.join(os.tmpdir(), 'qq-antigravity-local-trial-'));
  const trialId = 'TASK-TRIAL';
  const config = {
    ...f.config,
    test_mode: false,
    worker: {
      ...f.config.worker,
      local_trial: true,
      local_trial_root: trialRoot,
      model: 'gemini-3.8-flash-high'
    }
  };
  t.after(async () => {
    execFileSync('git', ['worktree', 'remove', '--force', path.join(trialRoot, trialId)], { cwd: f.root, stdio: 'ignore' });
    await rm(trialRoot, { recursive: true, force: true });
    await f.cleanup();
  });
  const resolved = await resolveTaskWorktree(trialId, {
    task: { task_id: trialId, base_sha: f.base },
    taskPath: f.taskPath,
    repoRoot: f.root,
    config
  });
  assert.equal(path.resolve(resolved.worktreeRoot), path.resolve(trialRoot));
  assert.equal(path.resolve(resolved.workspace), path.resolve(trialRoot, trialId));
  await assert.rejects(
    () => resolveTaskWorktree('TASK-OVERLAP', { task: { task_id: 'TASK-OVERLAP', base_sha: f.base }, taskPath: f.taskPath, repoRoot: f.root, config: { ...config, worker: { ...config.worker, local_trial_root: path.join(f.root, 'trial-root') } } }),
    error => error.code === 'WORKTREE_ROOT_INVALID'
  );
});

test('MCP abstraction dispatches, captures untracked files, and does not commit', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  const before = git(f.workspace, 'rev-parse', 'HEAD');
  const result = await runHarnessLifecycle(f.options);
  assert.equal(result.status, 'WAITING_FOR_CHECKPOINT');
  assert.deepEqual(f.worker.calls.map(call => call.tool), ['antigravity_execute']);
  assert.equal(git(f.workspace, 'rev-parse', 'HEAD'), before);
  assert.ok(result.changed_files.some(file => file.path === 'demo.py' && file.status === 'untracked'));
  const packet = JSON.parse(await readFile(path.join(f.workspace, '.workflow-local', f.id, 'review-packet.json'), 'utf8'));
  assert.equal(packet.changed_files.find(file => file.path === 'demo.py').status, 'untracked');
  assert.equal(packet.review_result.verdict, 'PASS');
});

test('temporary write permissions exist only during execute/continue and are gone before gates and review', async t => {
  const f = await fixture({ temporaryPermissions: true });
  t.after(f.cleanup);
  const originalSettings = await readFile(f.settingsPath);
  const rules = f.config.worker.temporary_permissions.files.map(file => `write_file(${path.resolve(file)})`);

  const first = await runHarnessLifecycle(f.options);
  assert.equal(first.status, 'WAITING_FOR_CHECKPOINT');
  assert.deepEqual(f.permissionObservations.map(item => item.phase), ['execute', 'review']);
  assert.deepEqual(f.permissionObservations[0].allow, rules);
  assert.deepEqual(f.permissionObservations[1].allow, []);
  assert.equal(await readFile(f.gateObservationPath, 'utf8'), '[]');
  assert.deepEqual(await readFile(f.settingsPath), originalSettings);

  await requestChanges({ ...f.options, instruction: 'bounded change' });
  const continued = await continueHarnessLifecycle({ ...f.options, instruction: 'bounded change' });
  assert.equal(continued.status, 'WAITING_FOR_CHECKPOINT');
  assert.deepEqual(f.permissionObservations.map(item => item.phase), ['execute', 'review', 'continue', 'review']);
  assert.deepEqual(f.permissionObservations[2].allow, rules);
  assert.deepEqual(f.permissionObservations[3].allow, []);
  assert.deepEqual(await readFile(f.settingsPath), originalSettings);
});

test('frozen temporary permission config rejects enabled, settingsPath and files changes before settings or worker access', async t => {
  const scenarios = [
    {
      name: 'enabled',
      mutate: f => { f.config.worker.temporary_permissions.enabled = false; }
    },
    {
      name: 'settingsPath',
      mutate: async f => {
        const alternate = path.join(f.root, 'alternate-settings.json');
        await writeFile(alternate, await readFile(f.settingsPath));
        f.config.worker.temporary_permissions.settingsPath = alternate;
      }
    },
    {
      name: 'files',
      mutate: f => { f.config.worker.temporary_permissions.files = [path.join(f.workspace, 'demo.py')]; }
    }
  ];

  for (const scenario of scenarios) {
    const f = await fixture({ temporaryPermissions: true });
    t.after(f.cleanup);
    const before = await readFile(f.settingsPath);
    await scenario.mutate(f);
    await assert.rejects(
      () => runHarnessLifecycle(f.options),
      error => error?.code === 'CONFIG_MISMATCH',
      scenario.name
    );
    assert.equal(f.worker.calls.length, 0, scenario.name);
    assert.equal(f.reviewerCalls.length, 0, scenario.name);
    assert.deepEqual(await readFile(f.settingsPath), before, scenario.name);
  }
});

test('temporary permission opt-in requires a frozen config binding and guards continue and reconcile', async t => {
  const unbound = await fixture({ temporaryPermissions: true, bindConfig: false });
  t.after(unbound.cleanup);
  const unboundSettings = await readFile(unbound.settingsPath);
  await assert.rejects(
    () => runHarnessLifecycle(unbound.options),
    error => error?.code === 'CONFIG_MISMATCH'
  );
  assert.equal(unbound.worker.calls.length, 0);
  assert.deepEqual(await readFile(unbound.settingsPath), unboundSettings);

  const continued = await fixture({ temporaryPermissions: true });
  t.after(continued.cleanup);
  await runHarnessLifecycle(continued.options);
  await requestChanges({ ...continued.options, instruction: 'bounded change' });
  const changedSettingsPath = path.join(continued.root, 'changed-settings.json');
  await writeFile(changedSettingsPath, await readFile(continued.settingsPath));
  continued.config.worker.temporary_permissions.settingsPath = changedSettingsPath;
  await assert.rejects(
    () => continueHarnessLifecycle({ ...continued.options, instruction: 'bounded change' }),
    error => error?.code === 'CONFIG_MISMATCH'
  );
  assert.equal(continued.worker.calls.length, 1);

  const reconciled = await fixture({ temporaryPermissions: true });
  t.after(reconciled.cleanup);
  await runHarnessLifecycle(reconciled.options);
  const statePath = path.join(reconciled.workspace, '.workflow-local', reconciled.id, 'state.json');
  const state = JSON.parse(await readFile(statePath, 'utf8'));
  state.phase = 'RECOVERY_REQUIRED';
  state.status = 'RECOVERY_REQUIRED';
  state.in_flight = {
    id: state.latest_execution.operation_id,
    idempotency_key: state.latest_execution.operation_id,
    kind: state.latest_execution.invocation_kind,
    invocation_kind: state.latest_execution.invocation_kind,
    attempt: state.latest_execution.attempt,
    rework_count: state.latest_execution.rework_count,
    started_at: state.updated_at
  };
  state.recovery = { required: true, reason: 'config binding test' };
  reconciled.worker.resultOverride = reconciled.worker.lastResult;
  await writeFile(statePath, JSON.stringify(state, null, 2) + '\n');
  reconciled.config.worker.temporary_permissions.files = [path.join(reconciled.workspace, 'demo.py')];
  await assert.rejects(
    () => reconcileTask(reconciled.options),
    error => error?.code === 'CONFIG_MISMATCH'
  );
  assert.equal(reconciled.worker.resultCalls, 0);
});

test('changed temporary permission scope stops before worker dispatch', async t => {
  const f = await fixture({ temporaryPermissions: true });
  t.after(f.cleanup);
  f.config.worker.temporary_permissions.files = [path.join(f.root, 'outside.txt')];

  await assert.rejects(
    () => runHarnessLifecycle(f.options),
    error => error?.code === 'CONFIG_MISMATCH'
  );
  assert.equal(f.worker.calls.length, 0);
  assert.equal(f.reviewerCalls.length, 0);
});

test('permission cleanup mutation blocks gates and review after worker completion', async t => {
  const f = await fixture({ temporaryPermissions: true });
  t.after(f.cleanup);
  const originalExecute = f.worker.execute.bind(f.worker);
  f.worker.execute = async (...args) => {
    const result = await originalExecute(...args);
    const settings = JSON.parse(await readFile(f.settingsPath, 'utf8'));
    settings.marker = 'concurrent-change';
    await writeFile(f.settingsPath, JSON.stringify(settings, null, 2) + '\n');
    return result;
  };

  await assert.rejects(
    () => runHarnessLifecycle(f.options),
    error => error?.code === 'CONTROL_STATE_MUTATED'
  );
  assert.equal(f.worker.calls.length, 1);
  assert.equal(f.reviewerCalls.length, 0);
  await assert.rejects(() => access(f.gateObservationPath), error => error?.code === 'ENOENT');
  assert.equal(JSON.parse(await readFile(f.settingsPath, 'utf8')).marker, 'concurrent-change');
  const state = JSON.parse(await readFile(path.join(f.workspace, '.workflow-local', f.id, 'state.json'), 'utf8'));
  assert.equal(state.error.code, 'CONTROL_STATE_MUTATED');
  assert.equal(state.latest_execution.status, 'SUCCEEDED');
  const replay = await runHarnessLifecycle(f.options);
  assert.equal(replay.status, 'FAILED');
  assert.equal(f.worker.calls.length, 1);
});

test('lifecycle preserves unknown sandbox and bounded redacted worker evidence', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  const originalExecute = f.worker.execute.bind(f.worker);
  f.worker.execute = async (...args) => ({
    ...await originalExecute(...args),
    sandbox: null,
    evidence_source: 'antigravity.stdout.stream-json',
    evidence_truncated: true,
    stdout_events: [{ event: 'tool_result', result: { token: 'sk-abcdefghijklmnop' } }]
  });

  const result = await runHarnessLifecycle(f.options);
  assert.equal(result.status, 'WAITING_FOR_CHECKPOINT');
  assert.equal(result.latest_execution.sandbox, null);
  assert.equal(result.latest_execution.evidence_truncated, true);
  const evidence = JSON.stringify(result.latest_execution.stdout_events);
  assert.match(evidence, /REDACTED/);
  assert.doesNotMatch(evidence, /sk-abcdefghijklmnop/);
});

test('REQUEST_CHANGES increments rework_count and continue reuses conversation', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const requested = await requestChanges({ ...f.options, instruction: 'Add lowercase input coverage without changing the public API.' });
  assert.equal(requested.status, 'REQUEST_CHANGES');
  assert.equal(requested.rework_count, 1);
  const continued = await continueHarnessLifecycle({ ...f.options, instruction: 'Add lowercase input coverage without changing the public API.' });
  assert.equal(continued.status, 'WAITING_FOR_CHECKPOINT');
  assert.equal(continued.conversation_id, 'agy-conversation-1');
  assert.deepEqual(f.worker.calls.map(call => call.tool), ['antigravity_execute', 'antigravity_continue']);
  assert.equal(continued.attempt, 2);
});

test('REQUEST_CHANGES rejects oversized repair context before dispatch', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await assert.rejects(
    () => requestChanges({ ...f.options, instruction: 'x'.repeat(3501) }),
    error => error.code === 'CONTENT_MISMATCH'
  );
  assert.equal(f.worker.calls.length, 1);
});

test('duplicate REQUEST_CHANGES is idempotent and conflicting replay is rejected', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const first = await requestChanges({ ...f.options, instruction: 'bounded change' });
  const replay = await requestChanges({ ...f.options, instruction: 'bounded change' });
  assert.equal(replay.requested_changes[0].id, first.requested_changes[0].id);
  assert.equal(replay.rework_count, 1);
  await assert.rejects(
    () => requestChanges({ ...f.options, instruction: 'a different change' }),
    error => error.code === 'IDEMPOTENCY_CONFLICT'
  );
});

test('no-op rework is blocked with an auditable error instead of leaving RUNNING state', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await requestChanges({ ...f.options, instruction: 'bounded change' });
  f.worker.continue = async (task, instruction, operation) => ({
    task_id: task.task_id,
    status: 'SUCCEEDED',
    agent_status: 'SUCCESS',
    exit_code: 0,
    operation_id: operation.id,
    invocation_kind: operation.kind,
    attempt: operation.attempt,
    rework_count: operation.rework_count,
    conversation_id: 'agy-conversation-1',
    observed_model: 'antigravity',
    stderr: ''
  });
  const result = await continueHarnessLifecycle({ ...f.options, instruction: 'bounded change' });
  assert.equal(result.status, 'BLOCKED');
  assert.equal(result.error.code, 'NOOP_REWORK');
  assert.equal(result.in_flight, null);
});

test('max rework is enforced and does not loop', async t => {
  const f = await fixture({ maxRework: 1, reviewerVerdicts: ['PASS', 'NEEDS_FIX'], temporaryPermissions: true });
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await requestChanges({ ...f.options, instruction: 'one bounded change' });
  const result = await continueHarnessLifecycle({ ...f.options, instruction: 'one bounded change' });
  assert.equal(result.status, 'RETRY_EXHAUSTED');
  assert.equal(result.rework_count, 2);
  assert.equal(f.worker.calls.length, 2);
  assert.deepEqual(JSON.parse(await readFile(f.settingsPath, 'utf8')).permissions.allow, []);
});

test('four review change requests permit exactly three completed repairs', async t => {
  const f = await fixture({ maxRework: 4, reviewerVerdicts: ['NEEDS_FIX'] });
  t.after(f.cleanup);
  const originalContinue = f.worker.continue;
  f.worker.continue = async (task, instruction, operation) => {
    const result = await originalContinue.call(f.worker, task, instruction, operation);
    await writeFile(path.join(f.workspace, 'test_demo.py'), `assert True\n# repair ${operation.attempt}\n`);
    return result;
  };
  let result = await runHarnessLifecycle(f.options);
  assert.equal(result.status, 'REQUEST_CHANGES');
  for (let repair = 1; repair <= 3; repair++) {
    result = await continueHarnessLifecycle({ ...f.options, instruction: result.requested_changes.at(-1).instruction });
    assert.equal(result.status, repair === 3 ? 'RETRY_EXHAUSTED' : 'REQUEST_CHANGES');
    assert.equal(result.attempt, repair + 1);
  }
  assert.equal(result.rework_count, 4);
  assert.equal(f.reviewerCalls.length, 4);
  assert.equal(f.worker.calls.length, 4);
});

test('lost lease prevents continue without calling MCP', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await requestChanges({ ...f.options, instruction: 'bounded change' });
  await rm(f.taskPath + '.claim.json');
  const result = await continueHarnessLifecycle({ ...f.options, instruction: 'bounded change' }).catch(error => ({ status: error.code, error: error.message }));
  assert.equal(result.status, 'LEASE_MISSING');
  assert.equal(f.worker.calls.length, 1);
});

test('checkpoint guard blocks completion until explicit approval, then completion is idempotent', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await assert.rejects(() => completeTask(f.options), /explicit checkpoint/);
  await assert.rejects(() => approveCheckpoint(f.options), /explicit checkpoint approver/);
  const checkpoint = await approveCheckpoint({ ...f.options, approvedBy: 'owner-fixture' });
  assert.equal(checkpoint.status, 'CHECKPOINTED');
  const checkpointReplay = await approveCheckpoint({ ...f.options, approvedBy: 'different-owner' });
  assert.equal(checkpointReplay.status, 'CHECKPOINTED');
  assert.equal(checkpointReplay.checkpoint.approved_by, 'owner-fixture');
  const completed = await completeTask(f.options);
  assert.equal(completed.status, 'COMPLETED');
  const replay = await runHarnessLifecycle(f.options);
  assert.equal(replay.status, 'COMPLETED');
  assert.equal(f.worker.calls.length, 1);
});

test('checkpoint and completion revalidate frozen task and config, including replay', async t => {
  const f = await fixture({ bindConfig: true });
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const original = await readFile(f.taskPath, 'utf8');
  const options = { ...f.options, approvedBy: 'fixture-owner' };
  const { acceptDirect } = await import('./direct-run.mjs');
  const configPath = path.join(f.root, 'accept-config.json');
  const manifestPath = path.join(f.root, 'accept-manifest.json');
  await writeFile(configPath, JSON.stringify(f.config));
  await writeFile(manifestPath, JSON.stringify({ taskPath: f.taskPath, configPath, packetDir: pathsFor(f.taskPath, null, f.id).packetDir }));
  for (const phase of ['WAITING_FOR_CHECKPOINT', 'CHECKPOINTED', 'COMPLETED']) {
    const changed = JSON.parse(original);
    changed.acceptance_criteria = ['changed after review'];
    await writeFile(f.taskPath, JSON.stringify(changed));
    await assert.rejects(() => approveCheckpoint(options), /CONTRACT_MISMATCH/);
    await assert.rejects(() => completeTask(options), /CONTRACT_MISMATCH/);
    await assert.rejects(() => acceptDirect(manifestPath, 'fixture-owner'), /CONTRACT_MISMATCH/);
    await writeFile(f.taskPath, original);
    const config = { ...f.config, timeout_seconds: f.config.timeout_seconds + 1 };
    await assert.rejects(() => approveCheckpoint({ ...options, config }), error => error.code === 'CONFIG_MISMATCH');
    await assert.rejects(() => completeTask({ ...options, config }), error => error.code === 'CONFIG_MISMATCH');
    await writeFile(configPath, JSON.stringify(config));
    await assert.rejects(() => acceptDirect(manifestPath, 'fixture-owner'), /CONFIG_MISMATCH/);
    await writeFile(configPath, JSON.stringify(f.config));
    assert.equal((await inspectTask(f.options)).status, phase);
    if (phase === 'WAITING_FOR_CHECKPOINT') await approveCheckpoint(options);
    else assert.equal((await completeTask(options)).status, 'COMPLETED');
  }
});

test('replaying continue after a completed review does not call the worker twice', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await requestChanges({ ...f.options, instruction: 'bounded change' });
  const first = await continueHarnessLifecycle({ ...f.options, instruction: 'bounded change' });
  const second = await continueHarnessLifecycle({ ...f.options, instruction: 'different replay instruction' });
  assert.equal(first.status, 'WAITING_FOR_CHECKPOINT');
  assert.equal(second.status, 'WAITING_FOR_CHECKPOINT');
  assert.equal(f.worker.calls.length, 2);
});

test('restart/load preserves lifecycle metadata and terminal state', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await requestChanges({ ...f.options, instruction: 'bounded change' });
  const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
  const saved = JSON.parse(await readFile(statePath, 'utf8'));
  assert.equal(saved.attempt, 1);
  assert.equal(saved.rework_count, 1);
  assert.equal(saved.conversation_id, 'agy-conversation-1');
  assert.equal(saved.worker, 'antigravity');
  const loaded = await lifecycleStatus({ taskPath: f.taskPath });
  assert.equal(loaded.status, 'REQUEST_CHANGES');
  assert.equal(loaded.conversation_id, saved.conversation_id);
});

test('state corruption fails closed and preserves evidence', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
  await writeFile(statePath, '{not-json');
  await assert.rejects(() => lifecycleStatus({ taskPath: f.taskPath }), error => error.code === 'STATE_CORRUPTION');
  const evidence = (await import('node:fs/promises')).readdir(path.dirname(statePath));
  assert.ok((await evidence).some(name => name.startsWith('state.json.corrupt.')));
});

test('MCP unavailable is reported before RUNNING and keeps the lease', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  f.worker.tools = async () => { const error = new Error('server offline'); error.code = 'MCP_UNAVAILABLE'; throw error; };
  const result = await runHarnessLifecycle(f.options);
  assert.equal(result.status, 'READY_TO_DISPATCH');
  assert.equal(result.error.code, 'MCP_UNAVAILABLE');
  assert.equal(f.worker.calls.length, 0);
  assert.ok(result.lease);
});

test('dirty worktree is rejected without reset or clean', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await writeFile(path.join(f.workspace, 'demo.py'), 'pre-existing\n');
  await assert.rejects(() => runHarnessLifecycle(f.options), error => error.code === 'WORKTREE_DIRTY');
  assert.equal(f.worker.calls.length, 0);
  assert.equal(await readFile(path.join(f.workspace, 'demo.py'), 'utf8'), 'pre-existing\n');
});

test('pause at the worker boundary persists review input and resumes without executing twice', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  let paused = false;
  const originalExecute = f.worker.execute.bind(f.worker);
  f.worker.execute = async (task, prompt, operation) => {
    const result = await originalExecute(task, prompt, operation);
    await writeFile(f.controlPath, 'paused\n');
    paused = true;
    return result;
  };
  const pausedResult = await runHarnessLifecycle(f.options);
  assert.equal(pausedResult.status, 'PAUSED');
  assert.equal(pausedResult.pending_review, true);
  assert.equal(paused, true);
  await writeFile(f.controlPath, 'running\n');
  const resumed = await runHarnessLifecycle(f.options);
  assert.equal(resumed.status, 'WAITING_FOR_CHECKPOINT');
  assert.equal(f.worker.calls.length, 1);
  assert.equal(f.reviewerCalls.length, 1);
});

test('paused pending review reports structured retry exhaustion', async t => {
  const f = await fixture({ maxRework: 1, reviewerVerdicts: ['NEEDS_FIX'] });
  t.after(f.cleanup);
  const originalExecute = f.worker.execute.bind(f.worker);
  f.worker.execute = async (task, prompt, operation) => {
    const result = await originalExecute(task, prompt, operation);
    await writeFile(f.controlPath, 'paused\n');
    return result;
  };
  const paused = await runHarnessLifecycle(f.options);
  assert.equal(paused.status, 'PAUSED');
  await writeFile(f.controlPath, 'running\n');
  const resumed = await runHarnessLifecycle(f.options);
  assert.equal(resumed.status, 'RETRY_EXHAUSTED');
  assert.equal(resumed.error.code, 'RETRY_EXHAUSTED');
  assert.equal(f.worker.calls.length, 1);
});

test('review and checkpoint are invalidated by a changed workspace', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await writeFile(path.join(f.workspace, 'demo.py'), 'changed after review\n');
  await assert.rejects(() => approveCheckpoint({ ...f.options, approvedBy: 'owner-fixture' }), error => error.code === 'REVIEW_STALE');
});

test('pending review rejects a packet result that was not produced by the reviewer', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  const originalExecute = f.worker.execute.bind(f.worker);
  f.worker.execute = async (task, prompt, operation) => {
    const result = await originalExecute(task, prompt, operation);
    await writeFile(f.controlPath, 'paused\n');
    return result;
  };
  assert.equal((await runHarnessLifecycle(f.options)).status, 'PAUSED');
  const packetPath = path.join(f.workspace, '.workflow-local', f.id, 'review-packet.json');
  const packet = JSON.parse(await readFile(packetPath, 'utf8'));
  packet.review_result = { verdict: 'PASS', summary: 'forged', material_findings: [], risk_checks_completed: true };
  await writeFile(packetPath, JSON.stringify(packet, null, 2) + '\n');
  await writeFile(f.controlPath, 'running\n');
  assert.equal((await runHarnessLifecycle(f.options)).status, LIFECYCLE_STATES.RECOVERY_REQUIRED);
  assert.equal(f.reviewerCalls.length, 0);
});

test('review and checkpoint are invalidated when HEAD changes outside reviewed paths', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await writeFile(path.join(f.workspace, 'README.md'), 'outside reviewed paths\n');
  git(f.workspace, 'add', 'README.md');
  git(f.workspace, 'commit', '-m', 'outside reviewed paths');
  await assert.rejects(() => approveCheckpoint({ ...f.options, approvedBy: 'owner-fixture' }), error => error.code === 'REVIEW_STALE');
});

test('checkpoint rejection is explicit and is not a retry', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const rejected = await rejectCheckpoint({ ...f.options, rejectedBy: 'owner-fixture', reason: 'Owner requested a product decision first.' });
  assert.equal(rejected.status, 'CHECKPOINT_REJECTED');
  assert.equal(rejected.rework_count, 0);
  assert.equal(f.worker.calls.length, 1);
});

test('conversation loss requires explicit recovery and never starts a new conversation', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await requestChanges({ ...f.options, instruction: 'bounded change' });
  const lost = await continueHarnessLifecycle({ ...f.options, worker: undefined, instruction: 'bounded change' });
  assert.equal(lost.status, 'RECOVERY_REQUIRED');
  assert.equal(lost.error.code, 'CONVERSATION_LOST');
  const recovered = await recoverTask({ ...f.options, worker: undefined, operator: 'fixture-owner', decision: 'block', reason: 'MCP session was lost.' });
  assert.equal(recovered.status, 'BLOCKED');
  assert.equal(f.worker.calls.length, 1);
});

test('reconcile uses antigravity_result and does not execute again', async t => {
  const f = await fixture({ temporaryPermissions: true });
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
  const saved = JSON.parse(await readFile(statePath, 'utf8'));
  saved.phase = 'RECOVERY_REQUIRED';
  saved.status = 'RECOVERY_REQUIRED';
  saved.in_flight = {
    id: saved.latest_execution.operation_id,
    idempotency_key: saved.latest_execution.operation_id,
    kind: saved.latest_execution.invocation_kind,
    attempt: saved.latest_execution.attempt,
    rework_count: saved.latest_execution.rework_count,
    started_at: saved.updated_at
  };
  f.worker.resultOverride = f.worker.lastResult;
  saved.recovery = { required: true, reason: 'crash boundary' };
  await writeFile(statePath, JSON.stringify(saved, null, 2) + '\n');
  const reconciled = await reconcileTask(f.options);
  assert.equal(reconciled.status, 'READY_FOR_REVIEW');
  assert.equal(f.worker.calls.length, 1);
  assert.deepEqual(f.permissionObservations.at(-1), { phase: 'result', allow: [] });
});

test('completion receipt, audit and read-only inspection are durable and replayable', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await approveCheckpoint({ ...f.options, approvedBy: 'owner-fixture' });
  const completed = await completeTask(f.options);
  assert.equal(completed.status, 'COMPLETED');
  const receiptPath = path.join(f.workspace, '.workflow-local', f.id, 'completion-receipt.json');
  const receipt = JSON.parse(await readFile(receiptPath, 'utf8'));
  const replay = await completeTask(f.options);
  assert.equal(replay.completion.completion_id, receipt.completion_id);
  const inspected = await inspectTask(f.options);
  assert.equal(inspected.status, 'COMPLETED');
  assert.ok(inspected.audit.events.completion >= 1);
  assert.ok(inspected.audit.events.task_initialized >= 1);
  assert.ok(inspected.audit.events.frozen >= 1);
  assert.equal(inspected.lease, null);
});

test('crash recovery marker prevents automatic replay at the next run', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
  const saved = JSON.parse(await readFile(statePath, 'utf8'));
  saved.phase = 'RUNNING';
  saved.status = 'RUNNING';
  saved.in_flight = { id: 'op-unknown', kind: 'execute' };
  await writeFile(statePath, JSON.stringify(saved, null, 2) + '\n');
  const result = await runHarnessLifecycle(f.options);
  assert.equal(result.status, 'RECOVERY_REQUIRED');
  assert.equal(f.worker.calls.length, 1);
});

test('worker timeout and output limit never trigger a second automatic execution', async t => {
  for (const failure of [
    { status: 'TIMED_OUT', timed_out: true, message: 'timeout' },
    { status: 'OUTPUT_LIMIT', output_limited: true, message: 'output limit' }
  ]) {
    const f = await fixture();
    t.after(f.cleanup);
    f.worker.execute = async (task, prompt, operation) => ({ task_id: task.task_id, status: failure.status, agent_status: 'FAILED', exit_code: 124, operation_id: operation.id, invocation_kind: operation.kind, attempt: operation.attempt, rework_count: operation.rework_count, timed_out: failure.timed_out === true, output_limited: failure.output_limited === true, error: failure.message });
    const result = await runHarnessLifecycle(f.options);
    assert.equal(result.status, 'RECOVERY_REQUIRED');
    assert.equal(result.error.code, failure.timed_out ? 'WORKER_TIMEOUT' : 'WORKER_OUTPUT_LIMIT');
    const replay = await runHarnessLifecycle(f.options);
    assert.equal(replay.status, 'RECOVERY_REQUIRED');
    assert.equal(f.worker.calls.length, 0);
  }
});

test('scope violation wins over timeout without dropping execution evidence or retrying', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  let dispatches = 0;
  f.worker.execute = async (task, prompt, operation) => {
    dispatches += 1;
    return {
      task_id: task.task_id,
      status: 'TIMED_OUT',
      agent_status: 'FAILED',
      exit_code: 1,
      operation_id: operation.id,
      invocation_kind: operation.kind,
      attempt: operation.attempt,
      rework_count: operation.rework_count,
      timed_out: true,
      output_limited: false,
      conversation_id: 'scope-timeout-conversation',
      requested_model: 'gemini-3.8-flash-high',
      observed_model: 'gemini-3.8-flash-high',
      evidence_source: 'antigravity.stdout.stream-json',
      evidence_truncated: true,
      stdout_events: [{ event: 'evidence_summary', truncated: true }],
      error_code: 'WORKER_SCOPE_VIOLATION',
      error: 'view_file AbsolutePath is outside the resolved workspace',
      stderr: ''
    };
  };
  const result = await runHarnessLifecycle(f.options);
  assert.equal(result.status, 'FAILED');
  assert.equal(result.error.code, 'WORKER_SCOPE_VIOLATION');
  assert.equal(result.error.retryable, false);
  assert.equal(result.in_flight, null);
  assert.equal(result.latest_execution.error_code, 'WORKER_SCOPE_VIOLATION');
  assert.equal(result.latest_execution.timed_out, true);
  assert.equal(result.latest_execution.exit_code, 1);
  assert.equal(result.latest_execution.observed_model, 'gemini-3.8-flash-high');
  assert.equal(result.latest_execution.conversation_id, 'scope-timeout-conversation');
  assert.equal(result.latest_execution.evidence_truncated, true);
  assert.equal(dispatches, 1);
  const replay = await runHarnessLifecycle(f.options);
  assert.equal(replay.status, 'FAILED');
  assert.equal(dispatches, 1);
});

test('invalid MCP preflight response stays before dispatch', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  f.worker.tools = async () => { const error = new Error('invalid tools/list'); error.code = 'MCP_PROTOCOL_ERROR'; throw error; };
  const result = await runHarnessLifecycle(f.options);
  assert.equal(result.status, 'READY_TO_DISPATCH');
  assert.equal(result.error.code, 'MCP_PROTOCOL_ERROR');
  assert.equal(f.worker.calls.length, 0);
});

test('cross-process stale owner cannot overwrite state after deterministic reclaim', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const packetDir = path.join(f.workspace, '.workflow-local', f.id);
  const paths = pathsFor(f.taskPath, packetDir, f.id);
  const barrier = path.join(f.root, 'p0-fence-barrier');
  const childPath = fileURLToPath(new URL('./harness-fence-child.mjs', import.meta.url));
  const child = spawn(process.execPath, [childPath, f.taskPath, packetDir, barrier], {
    cwd: path.dirname(childPath),
    stdio: ['ignore', 'ignore', 'pipe'],
    windowsHide: true
  });
  try {
    await waitForFile(`${barrier}.validated`);
    const reclaimed = await claimTask(f.taskPath, {
      owner: 'owner-b',
      leaseSeconds: 300,
      now: () => Date.now() + 301_000
    });
    assert.equal(reclaimed.reclaimed, true);
    const bState = JSON.parse(await readFile(paths.statePath, 'utf8'));
    bState.owner = reclaimed.claim.owner;
    bState.lease = leaseSummary(reclaimed.claim);
    bState.history = [...bState.history, { marker: 'B-authoritative-write' }];
    await saveState(bState, paths, {
      owner: bState.owner,
      lease: bState.lease,
      history: bState.history
    }, reclaimed.claim);
    await writeFile(`${barrier}.resume`, 'resume\n', { flag: 'wx' });
    const childExit = await waitForChild(child);
    assert.equal(childExit.code, 0, childExit.stderr);
    const childResult = JSON.parse(await readFile(`${barrier}.result.json`, 'utf8'));
    assert.equal(childResult.status, 'REJECTED');
    assert.ok(['LEASE_OWNER_MISMATCH', 'LEASE_TOKEN_MISMATCH', 'LEASE_GENERATION_MISMATCH'].includes(childResult.code));
    const finalState = JSON.parse(await readFile(paths.statePath, 'utf8'));
    assert.ok(finalState.history.some(item => item.marker === 'B-authoritative-write'));
    assert.equal(finalState.history.some(item => item.marker === 'A-stale-write'), false);
    assert.equal(finalState.owner, 'owner-b');
    assert.equal(finalState.lease.version, reclaimed.claim.version);
    const currentClaim = JSON.parse(await readFile(paths.claimPath, 'utf8'));
    assert.equal(currentClaim.owner, 'owner-b');
    assert.equal(currentClaim.version, reclaimed.claim.version);
  } finally {
    if (child.exitCode === null) {
      await writeFile(`${barrier}.resume`, 'cleanup\n').catch(() => {});
      child.kill();
      await waitForChild(child).catch(() => {});
    }
  }
});

test('state revision CAS rejects a same-generation stale snapshot', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const packetDir = path.join(f.workspace, '.workflow-local', f.id);
  const paths = pathsFor(f.taskPath, packetDir, f.id);
  const claim = JSON.parse(await readFile(paths.claimPath, 'utf8'));
  const staleSnapshot = JSON.parse(await readFile(paths.statePath, 'utf8'));
  const current = JSON.parse(JSON.stringify(staleSnapshot));
  current.history = [...current.history, { marker: 'CAS-first' }];
  await saveState(current, paths, { history: current.history }, claim);
  await assert.rejects(
    () => saveState(staleSnapshot, paths, { history: [...staleSnapshot.history, { marker: 'CAS-stale' }] }, claim),
    error => error?.code === 'IDEMPOTENCY_CONFLICT'
  );
  const persisted = JSON.parse(await readFile(paths.statePath, 'utf8'));
  assert.equal(persisted.history.some(item => item.marker === 'CAS-first'), true);
  assert.equal(persisted.history.some(item => item.marker === 'CAS-stale'), false);
});

test('Windows wx mutation lock rejects a competing Node process while held', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const packetDir = path.join(f.workspace, '.workflow-local', f.id);
  const paths = pathsFor(f.taskPath, packetDir, f.id);
  const barrier = path.join(f.root, 'p0-lock-barrier');
  const childPath = fileURLToPath(new URL('./harness-fence-child.mjs', import.meta.url));
  const child = spawn(process.execPath, [childPath, f.taskPath, packetDir, barrier, 'hold-lock'], {
    cwd: path.dirname(childPath),
    stdio: ['ignore', 'ignore', 'pipe'],
    windowsHide: true
  });
  try {
    await waitForFile(`${barrier}.locked`);
    const claim = JSON.parse(await readFile(paths.claimPath, 'utf8'));
    const state = JSON.parse(await readFile(paths.statePath, 'utf8'));
    await assert.rejects(
      () => saveState(state, paths, { history: [...state.history, { marker: 'competing-process' }] }, claim),
      error => error?.code === 'CLAIM_BUSY'
    );
    await writeFile(`${barrier}.release`, 'release\n', { flag: 'wx' });
    const childExit = await waitForChild(child);
    assert.equal(childExit.code, 0, childExit.stderr);
    const childResult = JSON.parse(await readFile(`${barrier}.result.json`, 'utf8'));
    assert.equal(childResult.status, 'RELEASED');
  } finally {
    if (child.exitCode === null) {
      await writeFile(`${barrier}.release`, 'cleanup\n').catch(() => {});
      child.kill();
      await waitForChild(child).catch(() => {});
    }
  }
});

test('stale worker-result, request, checkpoint, completion and release mutations are fenced', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const packetDir = path.join(f.workspace, '.workflow-local', f.id);
  const paths = pathsFor(f.taskPath, packetDir, f.id);
  const staleClaim = JSON.parse(await readFile(paths.claimPath, 'utf8'));
  const reclaimed = await claimTask(f.taskPath, {
    owner: 'owner-b',
    leaseSeconds: 300,
    now: () => Date.now() + 301_000
  });
  const bState = JSON.parse(await readFile(paths.statePath, 'utf8'));
  bState.owner = reclaimed.claim.owner;
  bState.lease = leaseSummary(reclaimed.claim);
  await saveState(bState, paths, { owner: bState.owner, lease: bState.lease }, reclaimed.claim);
  const protectedState = await readFile(paths.statePath, 'utf8');
  const staleError = error => ['LEASE_OWNER_MISMATCH', 'LEASE_TOKEN_MISMATCH', 'LEASE_GENERATION_MISMATCH'].includes(error?.code);

  const oldState = JSON.parse(protectedState);
  await assert.rejects(
    () => saveState(oldState, paths, { latest_execution: { marker: 'stale-worker-result' } }, staleClaim),
    staleError
  );
  await assert.rejects(() => requestChanges({ ...f.options, instruction: 'stale request' }), staleError);
  await assert.rejects(() => approveCheckpoint({ ...f.options, approvedBy: 'stale-owner' }), staleError);
  await assert.rejects(() => completeTask(f.options), staleError);
  await assert.rejects(() => releaseLease(f.taskPath, { owner: staleClaim.owner, token: staleClaim.lease_token, version: staleClaim.version }), staleError);

  assert.equal(await readFile(paths.statePath, 'utf8'), protectedState);
  const currentClaim = JSON.parse(await readFile(paths.claimPath, 'utf8'));
  assert.equal(currentClaim.owner, 'owner-b');
  assert.equal(f.worker.calls.length, 1);
});

test('normal run and continue are safe-stopped without dispatching from every P0 stop state', async t => {
  for (const phase of [...SAFE_STOP_STATES, LIFECYCLE_STATES.RECOVERY_REQUIRED]) {
    const f = await fixture();
    t.after(f.cleanup);
    await runHarnessLifecycle(f.options);
    const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
    const state = JSON.parse(await readFile(statePath, 'utf8'));
    state.phase = phase;
    state.status = phase;
    await writeFile(statePath, JSON.stringify(state, null, 2) + '\n');
    const before = await readFile(statePath, 'utf8');
    const sourceBefore = git(f.workspace, 'status', '--porcelain=v1');
    const result = await runHarnessLifecycle(f.options);
    assert.equal(result.status, phase);
    if (SAFE_STOP_STATES.includes(phase)) assert.equal(result.error.code, 'INVALID_TRANSITION');
    assert.equal(f.worker.calls.length, 1);
    assert.equal(git(f.workspace, 'status', '--porcelain=v1'), sourceBefore);
    assert.equal(await readFile(statePath, 'utf8'), before);
    const continued = await continueHarnessLifecycle(f.options);
    assert.equal(continued.status, phase);
    assert.equal(f.worker.calls.length, 1);
    assert.equal(await readFile(statePath, 'utf8'), before);
  }
});

test('public bridge run entry point reaches the same safe-stop guard', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const packetDir = path.join(f.workspace, '.workflow-local', f.id);
  const statePath = path.join(packetDir, 'state.json');
  const state = JSON.parse(await readFile(statePath, 'utf8'));
  state.phase = LIFECYCLE_STATES.BLOCKED;
  state.status = LIFECYCLE_STATES.BLOCKED;
  await writeFile(statePath, JSON.stringify(state, null, 2) + '\n');
  const before = await readFile(statePath, 'utf8');
  const sourceBefore = git(f.workspace, 'status', '--porcelain=v1');
  const result = await runBridge({ ...f.options, cwd: f.workspace, packetDir });
  assert.equal(result.error.code, 'INVALID_TRANSITION');
  assert.equal(f.worker.calls.length, 1);
  assert.equal(git(f.workspace, 'status', '--porcelain=v1'), sourceBefore);
  assert.equal(await readFile(statePath, 'utf8'), before);
});

test('COMPLETED is terminal and never dispatches again', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  await approveCheckpoint({ ...f.options, approvedBy: 'fixture-owner' });
  const completed = await completeTask(f.options);
  assert.equal(completed.status, LIFECYCLE_STATES.COMPLETED);
  const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
  const before = await readFile(statePath, 'utf8');
  const replay = await runHarnessLifecycle(f.options);
  assert.equal(replay.status, LIFECYCLE_STATES.COMPLETED);
  assert.equal(f.worker.calls.length, 1);
  assert.equal(await readFile(statePath, 'utf8'), before);
});

test('existing worktree with the wrong HEAD is rejected without repair actions', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await writeFile(path.join(f.workspace, 'README.md'), 'second commit\n');
  git(f.workspace, 'add', 'README.md');
  git(f.workspace, 'commit', '-m', 'wrong base fixture');
  await assert.rejects(
    () => resolveTaskWorktree(f.id, { task: { task_id: f.id, base_sha: f.base }, taskPath: f.taskPath, repoRoot: f.root, config: f.config }),
    error => error.code === 'WORKTREE_BASE_MISMATCH'
  );
});

test('existing worktree with uncommitted files still passes frozen base identity', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await writeFile(path.join(f.workspace, 'uncommitted.txt'), 'allowed resolver dirt\n');
  const resolved = await resolveTaskWorktree(f.id, { task: { task_id: f.id, base_sha: f.base }, taskPath: f.taskPath, repoRoot: f.root, config: f.config });
  assert.equal(path.resolve(resolved.workspace), path.resolve(f.workspace));
});

test('existing repository at the task path cannot masquerade as the frozen worktree', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  const other = path.join(f.worktreeRoot, 'TASK-OTHER');
  await mkdir(other);
  git(other, 'init');
  git(other, 'config', 'user.email', 'other@example.invalid');
  git(other, 'config', 'user.name', 'Other Fixture');
  await writeFile(path.join(other, 'README.md'), 'different repository\n');
  git(other, 'add', '.');
  git(other, 'commit', '-m', 'other base');
  await assert.rejects(
    () => resolveTaskWorktree('TASK-OTHER', { task: { task_id: 'TASK-OTHER', base_sha: f.base }, taskPath: f.taskPath, repoRoot: f.root, config: f.config, create: false }),
    error => ['WORKTREE_BASE_MISMATCH', 'WORKTREE_REPOSITORY_MISMATCH', 'WORKTREE_ESCAPE'].includes(error.code)
  );
});

test('reconcile accepts only the exact persisted external operation', async t => {
  const scenarios = [
    { name: 'matching result', expected: 'READY_FOR_REVIEW', mutate: () => ({}) },
    { name: 'stale execute result for continue', expected: 'RECOVERY_REQUIRED', mutate: state => { state.rework_count = 1; return { kind: 'continue', attempt: 2, rework_count: 1 }; } },
    { name: 'same conversation with wrong operation', expected: 'RECOVERY_REQUIRED', mutate: () => ({ result: { operation_id: 'op-wrong' } }) },
    { name: 'wrong attempt', expected: 'RECOVERY_REQUIRED', mutate: () => ({ result: { attempt: 99 } }) },
    { name: 'wrong rework count', expected: 'RECOVERY_REQUIRED', mutate: () => ({ result: { rework_count: 99 } }) },
    { name: 'missing operation id', expected: 'RECOVERY_REQUIRED', mutate: () => ({ result: { operation_id: null } }) },
    { name: 'later operation overwrote latest result', expected: 'RECOVERY_REQUIRED', mutate: state => ({ result: { operation_id: operationId(state.task_id, 'continue', 2, 1, state.contract_sha256), invocation_kind: 'continue', attempt: 2, rework_count: 1 } }) }
  ];
  for (const scenario of scenarios) {
    const f = await fixture();
    t.after(f.cleanup);
    await runHarnessLifecycle(f.options);
    const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
    const state = JSON.parse(await readFile(statePath, 'utf8'));
    const baseOperation = {
      id: state.latest_execution.operation_id,
      idempotency_key: state.latest_execution.operation_id,
      kind: state.latest_execution.invocation_kind,
      invocation_kind: state.latest_execution.invocation_kind,
      attempt: state.latest_execution.attempt,
      rework_count: state.latest_execution.rework_count,
      started_at: state.updated_at
    };
    const changes = scenario.mutate(state);
    const operation = changes.kind ? {
      id: operationId(state.task_id, changes.kind, changes.attempt, changes.rework_count, state.contract_sha256),
      idempotency_key: operationId(state.task_id, changes.kind, changes.attempt, changes.rework_count, state.contract_sha256),
      kind: changes.kind,
      invocation_kind: changes.kind,
      attempt: changes.attempt,
      rework_count: changes.rework_count,
      started_at: state.updated_at
    } : baseOperation;
    state.phase = 'RECOVERY_REQUIRED';
    state.status = 'RECOVERY_REQUIRED';
    state.in_flight = operation;
    state.recovery = { required: true, reason: 'test correlation boundary' };
    await writeFile(statePath, JSON.stringify(state, null, 2) + '\n');
    const result = { ...f.worker.lastResult, ...(changes.result ?? {}) };
    if (scenario.name === 'stale execute result for continue') f.worker.resultOverride = f.worker.lastResult;
    else f.worker.resultOverride = result;
    const reconciled = await reconcileTask(f.options);
    assert.equal(reconciled.status, scenario.expected, scenario.name);
    if (scenario.expected !== 'READY_FOR_REVIEW') assert.notEqual(reconciled.status, 'READY_FOR_REVIEW', scenario.name);
  }
});

test('NO_RESULT after an MCP restart stays in recovery and does not execute again', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
  const state = JSON.parse(await readFile(statePath, 'utf8'));
  state.phase = 'RECOVERY_REQUIRED';
  state.status = 'RECOVERY_REQUIRED';
  state.in_flight = {
    id: state.latest_execution.operation_id,
    idempotency_key: state.latest_execution.operation_id,
    kind: state.latest_execution.invocation_kind,
    invocation_kind: state.latest_execution.invocation_kind,
    attempt: state.latest_execution.attempt,
    rework_count: state.latest_execution.rework_count,
    started_at: state.updated_at
  };
  await writeFile(statePath, JSON.stringify(state, null, 2) + '\n');
  f.worker.resultOverride = { task_id: f.id, workspace: f.workspace, status: 'NO_RESULT', error_code: 'NO_RESULT', conversation_id: null };
  const result = await reconcileTask(f.options);
  assert.equal(result.status, 'RECOVERY_REQUIRED');
  assert.equal(result.error.code, 'CONVERSATION_LOST');
  assert.equal(f.worker.calls.length, 1);
  assert.equal(f.worker.resultCalls, 1);
});

test('reconcile never calls MCP or mutates terminal and safe-stop states', async t => {
  for (const phase of [...SAFE_STOP_STATES, LIFECYCLE_STATES.COMPLETED]) {
    const f = await fixture();
    t.after(f.cleanup);
    await runHarnessLifecycle(f.options);
    if (phase === LIFECYCLE_STATES.COMPLETED) {
      await approveCheckpoint({ ...f.options, approvedBy: 'fixture-owner' });
      await completeTask(f.options);
    }
    const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
    const state = JSON.parse(await readFile(statePath, 'utf8'));
    state.phase = phase;
    state.status = phase;
    state.in_flight = { id: 'stale-operation', kind: 'execute' };
    await writeFile(statePath, JSON.stringify(state, null, 2) + '\n');
    const before = await readFile(statePath, 'utf8');
    const result = await reconcileTask(f.options);
    assert.equal(result.status, phase);
    assert.equal(f.worker.resultCalls, 0);
    assert.equal(await readFile(statePath, 'utf8'), before);
  }
});

test('expired RUNNING lease cannot be reclaimed into a second worker dispatch', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  await runHarnessLifecycle(f.options);
  const statePath = path.join(f.workspace, '.workflow-local', f.id, 'state.json');
  const state = JSON.parse(await readFile(statePath, 'utf8'));
  state.phase = 'RUNNING';
  state.status = 'RUNNING';
  state.in_flight = {
    id: state.latest_execution.operation_id,
    idempotency_key: state.latest_execution.operation_id,
    kind: state.latest_execution.invocation_kind,
    invocation_kind: state.latest_execution.invocation_kind,
    attempt: state.latest_execution.attempt,
    rework_count: state.latest_execution.rework_count,
    started_at: state.updated_at
  };
  await writeFile(statePath, JSON.stringify(state, null, 2) + '\n');
  const claimPath = f.taskPath + '.claim.json';
  const claim = JSON.parse(await readFile(claimPath, 'utf8'));
  claim.lease_expires_at = new Date(Date.now() - 1000).toISOString();
  await writeFile(claimPath, JSON.stringify(claim, null, 2) + '\n');
  const reclaimed = await claimTask(f.taskPath, { owner: 'owner-b', now: () => Date.now() + 10_000 });
  assert.equal(reclaimed.status, 'RECOVERY_REQUIRED');
  assert.equal(JSON.parse(await readFile(claimPath, 'utf8')).owner, 'fixture-owner');
  const result = await runHarnessLifecycle({ ...f.options, owner: 'owner-b' });
  assert.equal(result.status, 'RECOVERY_REQUIRED');
  assert.equal(f.worker.calls.length, 1);
});

test('real MCP dispatch fails closed when worker isolation is unavailable', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  const result = await runHarnessLifecycle({ ...f.options, config: { ...f.config, test_mode: false } });
  assert.equal(result.status, 'READY_TO_DISPATCH');
  assert.equal(result.error.code, 'WORKER_ISOLATION_UNAVAILABLE');
  assert.equal(f.worker.calls.length, 0);
});

test('control artifact mutation is detected after the worker while source edits remain possible', async t => {
  const f = await fixture();
  t.after(f.cleanup);
  const originalExecute = f.worker.execute.bind(f.worker);
  f.worker.execute = async (task, prompt, operation) => {
    const result = await originalExecute(task, prompt, operation);
    await writeFile(path.join(f.workspace, '.workflow-local', f.id, 'completion-receipt.json'), 'worker tampered\n');
    return result;
  };
  await assert.rejects(() => runHarnessLifecycle(f.options), error => error.code === 'CONTROL_STATE_MUTATED');
  const state = JSON.parse(await readFile(path.join(f.workspace, '.workflow-local', f.id, 'state.json'), 'utf8'));
  assert.equal(state.status, 'BLOCKED');
  assert.equal(await readFile(path.join(f.workspace, 'demo.py'), 'utf8'), 'VALUE = "ok"\n');
});
