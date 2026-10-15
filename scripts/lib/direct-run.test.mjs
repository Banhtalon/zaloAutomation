import assert from 'node:assert/strict';
import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { prepareDirect, checkDirect, runBounded, runDirect, verifyProductDirect, statusDirect, acceptDirect, summarizeDirectUsage } from './direct-run.mjs';
import * as lifecycle from './harness-lifecycle.mjs';
import { freezeControlledTask } from './controlled-bridge.mjs';
import { normalizeUsage } from './receipts.mjs';

test('Direct usage report preserves provider counters and leaves missing counters null', () => {
  const report = summarizeDirectUsage({
    latest_execution: {
      observed_model: 'gemini-3.8-flash-high',
      usage: { source: 'mcp', input_tokens: 11, output_tokens: 7, reasoning_tokens: null, cached_tokens: null, total_tokens: 18 }
    },
    review_result: {
      observed_models: ['gpt-6-luna'],
      usage: { source: 'openai', input_tokens: 5, output_tokens: 3, reasoning_tokens: null, cached_tokens: null, total_tokens: 8 }
    }
  }, { worker: { provider: 'mcp', model: 'gemini-3.8-flash-high' }, reviewer: { provider: 'openai', model: 'gpt-6-luna' } });
  assert.equal(report.worker_usage.total_tokens, 18);
  assert.equal(report.reviewer_usage.total_tokens, 8);
  assert.equal(report.usage.total_tokens, 26);
  assert.equal(report.usage.input_tokens, 16);
  assert.equal(report.usage.output_tokens, 10);
  const unavailable = summarizeDirectUsage({ latest_execution: { usage: null } }, { worker: { provider: 'mcp', model: 'gemini-3.8-flash-high' }, reviewer: {} });
  assert.equal(unavailable.usage.total_tokens, null);
  assert.equal(unavailable.reviewer_usage, null);
  assert.equal(normalizeUsage({ input_tokens: -1, output_tokens: 4, total_tokens: -2 }, 'fixture').input_tokens, null);
  assert.equal(normalizeUsage({ input_tokens: 1, output_tokens: 4, total_tokens: -2 }, 'fixture').total_tokens, 5);
});

test('Direct status and completed accept explain ownership without claiming integration', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'direct-owner-message-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const taskPath = path.join(root, 'task.json'), configPath = path.join(root, 'config.json'), manifestPath = path.join(root, 'prepared.json');
  const task = JSON.parse(await readFile(new URL('../../.ai-workflow/templates/task.json', import.meta.url), 'utf8'));
  Object.assign(task, { base_sha: 'a'.repeat(40), user_visible: false, product_check: { applicable: false, reason: 'synthetic fixture' } });
  const config = { mode: 'ASSISTED' };
  await freezeControlledTask(taskPath, task, config);
  await writeFile(configPath, JSON.stringify(config));
  await writeFile(manifestPath, JSON.stringify({ taskPath, configPath, packetDir: root }));
  for (const [status, expected] of Object.entries({ WAITING_FOR_CHECKPOINT: /Owner nghiệm thu/, CHECKPOINTED: /Lead.*kết thúc/, PRODUCT_CHECK_WAIT: /Lead.*không gọi lại AI/, RECOVERY_REQUIRED: /Lead đối soát/, BLOCKED: /Lead/, COMPLETED: /chưa xác nhận đưa vào dự án chính/ })) {
    await writeFile(path.join(root, 'state.json'), JSON.stringify({ status, task_id: task.task_id, error: { code: 'CHECKPOINT_REQUIRED', message: 'synthetic private detail' } }));
    const result = await statusDirect(manifestPath);
    assert.equal(result.status, status);
    assert.match(result.owner_message, expected);
    assert.match(result.owner_message, /CHECKPOINT_REQUIRED/);
    assert.doesNotMatch(result.owner_message, /synthetic private detail/);
    if (status === 'COMPLETED') assert.deepEqual(await acceptDirect(manifestPath, 'fixture-owner'), result);
  }
  await writeFile(path.join(root, 'state.json'), JSON.stringify({ status: 'RETRY_EXHAUSTED', task_id: task.task_id, attempt: 2, rework_count: 2 }));
  const exhausted = await statusDirect(manifestPath);
  assert.equal(exhausted.attempts, 2);
  assert.equal(exhausted.repairs, 1);
  assert.equal(exhausted.change_requests, 2);
  await writeFile(path.join(root, 'state.json'), JSON.stringify({ status: 'BLOCKED', task_id: task.task_id, attempt: 1, rework_count: 1,
    latest_execution: { status: 'SUCCEEDED', agent_status: 'SUCCESS', exit_code: 0, attempt: 2 } }));
  const blockedAfterWorker = await statusDirect(manifestPath);
  assert.equal(blockedAfterWorker.attempts, 2);
  assert.equal(blockedAfterWorker.repairs, 1);
});

test('one process, at most three repairs for new tasks and one for frozen legacy tasks', async () => {
  for (const [status, maxRework] of [['WAITING_FOR_CHECKPOINT', 4], ['REQUEST_CHANGES', 4], ['REQUEST_CHANGES', 2], ['BLOCKED', 4], ['WAITING_QUOTA', 4], ['throw', 4]]) {
    const calls = [], worker = {};
    const api = {
      async runHarnessLifecycle(options) { assert.equal(options.worker, worker); calls.push('run'); if (status === 'throw') throw Error('sentinel'); return { status, requested_changes: [{ instruction: 'fix only the target' }] }; },
      async continueHarnessLifecycle(options) { assert.equal(options.worker, worker); assert.match(options.instruction, /fix only the target/); calls.push('continue'); return { status: 'REQUEST_CHANGES', requested_changes: [{ instruction: 'fix only the target' }] }; },
      async closeLifecycleWorkers() { calls.push('close'); }
    };
    const options = { worker, config: { lifecycle: { max_rework: maxRework } } };
    if (status === 'throw') await assert.rejects(runBounded(options, api), /sentinel/);
    else await runBounded(options, api);
    assert.deepEqual(calls, status === 'REQUEST_CHANGES' ? ['run', ...Array(maxRework - 1).fill('continue'), 'close'] : ['run', 'close']);
  }
});

test('real preparation/layout + lifecycle fake boundary, missing helper and paused state block offline', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'direct-gemini-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo'), output = path.join(root, 'packet'), worktrees = path.join(root, 'worktrees');
  await mkdir(repo);
  const git = (...args) => execFileSync('git', args, { cwd: repo, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  git('init'); git('config', 'user.name', 'Direct test'); git('config', 'user.email', 'test@example.invalid');
  await writeFile(path.join(repo, '.gitignore'), '.workflow-local/\n');
  await writeFile(path.join(repo, 'old.txt'), 'before\n');
  await writeFile(path.join(repo, 'gate.mjs'), "import{readFileSync}from'node:fs';if(readFileSync('old.txt','utf8')!=='after\\n'||readFileSync('new.txt','utf8').trim()!=='created')process.exit(1);");
  git('add', '.'); git('commit', '-m', 'fixture');
  const task = { schema_version: 'qq.workflow.task.v10.1', task_id: 'TASK-DIRECT-TEST', revision: 1, base_sha: git('rev-parse', 'HEAD'), goal: 'update old and create new', acceptance_criteria: ['old=after and new=created'],
    gates: [{ id: 'content', argv: [process.execPath, 'gate.mjs'], timeout_seconds: 20 }], user_visible: false, risk: 'LOW', complexity: 'SIMPLE', execution: { policy: 'CONTROLLED_DELEGATION_V1' },
    write_paths: ['old.txt', 'new.txt'], allowed_paths: ['old.txt', 'new.txt'], lane: 'NORMAL', initial_lane: 'NORMAL', initial_risk: 'LOW', product_check: { applicable: false, reason: 'fixture' }, candidate_head: null, contract_sha256: null };
  const config = { schema_version: 'qq.bridge.v2', billing: 'SUBSCRIPTION_ONLY', mode: 'ASSISTED', timeout_seconds: 30, write_paths: task.write_paths, gate_paths: ['gate.mjs'],
    worker: { transport: 'mcp', server: 'antigravity_worker', provider: 'mcp', command: ['python', 'mcp/antigravity_server.py'], model: 'gemini-3.8-flash-high', local_trial: true, local_trial_root: worktrees, skip_permissions: false },
    reviewer: { provider: 'openai', cli: 'codex', command: ['codex'], model: 'gpt-6-luna', effort: 'max' } };
  const m = await prepareDirect(repo, task, config, output), manifest = path.join(output, 'prepared.json');
  await assert.rejects(prepareDirect(repo, task, config, output), /OUTPUT_EXISTS/);
  const control = path.join(repo, '.workflow-local', 'ai-control.desired_state');
  await writeFile(control, 'paused\n');
  await assert.rejects(checkDirect(manifest), /DESIRED_STATE_BLOCKED/);
  await writeFile(control, 'running\n');
  const preparedConfig = JSON.parse(await readFile(m.configPath));
  assert.equal(preparedConfig.lifecycle.max_rework, 4);
  const helper = preparedConfig.worker.command[1], originalHelper = await readFile(helper);
  await writeFile(helper, 'wrong helper');
  await assert.rejects(checkDirect(manifest), /HELPER_CHANGED/);
  const fail = await runDirect(manifest);
  assert.equal(fail.status, 'BLOCKED'); assert.equal(fail.provider_invocations, 0); assert.equal(fail.error_code, 'HELPER_CHANGED');
  await writeFile(helper, originalHelper);
  // Separate fake frozen task: never write or read real account settings in this test.
  const settings = path.join(root, 'settings.json');
  const settingsBytes = JSON.stringify({ permissions: { allow: ['read_file(existing.txt)'], deny: [] } });
  await writeFile(settings, settingsBytes);
  const fakeConfig = structuredClone(preparedConfig);
  fakeConfig.worker.temporary_permissions.settingsPath = settings;
  const fakeRoot = path.join(path.dirname(m.taskPath), 'fake');
  await mkdir(fakeRoot);
  const fakePath = path.join(fakeRoot, `${task.task_id}.json`);
  await writeFile(fakePath, JSON.stringify(task)); await freezeControlledTask(fakePath, task, fakeConfig);
  const workspace = path.join(worktrees, task.task_id), calls = [];
  const result = op => ({ task_id: task.task_id, status: 'SUCCEEDED', agent_status: 'SUCCESS', exit_code: 0, operation_id: op.id, invocation_kind: op.kind, attempt: op.attempt, rework_count: op.rework_count, conversation_id: 'synthetic-session', requested_model: config.worker.model, observed_model: config.worker.model });
  const worker = { async tools() { return ['antigravity_execute','antigravity_continue','antigravity_result']; },
    async execute(_task, prompt, op) { assert.match(prompt, /controller runs all tests/); assert.match(prompt, /Do not invent product behavior or gameplay details/); assert.doesNotMatch(prompt, /run the relevant tests/); calls.push('execute'); await writeFile(path.join(workspace, 'old.txt'), 'after\n'); await writeFile(path.join(workspace, 'new.txt'), 'created\n\n'); return result(op); },
    async continue(_task, prompt, op) { calls.push('continue'); assert.match(prompt, /Do not read\/run gates/); await writeFile(path.join(workspace, 'new.txt'), 'created\n'); return result(op); } };
  let reviews = 0;
  const final = await runBounded({ taskPath: fakePath, packetDir: m.packetDir, config: fakeConfig, worker,
    reviewerInvoker: async ({ prompt }) => { assert.equal(await readFile(settings, 'utf8'), settingsBytes); assert.match(prompt, /return BLOCKED for Lead diagnosis rather than spending a worker repair/); assert.match(prompt, /absence of horizontal overflow alone does not prove usable layout/); reviews++; return { code: 0, session_id: `synthetic-review-${reviews}`, observed_models: [config.reviewer.model], result: { verdict: reviews === 1 ? 'NEEDS_FIX' : 'PASS', summary: 'fixture', material_findings: reviews === 1 ? ['check content'] : [], risk_checks_completed: true } }; } });
  assert.equal(final.status, 'WAITING_FOR_CHECKPOINT'); assert.deepEqual(calls, ['execute', 'continue']); assert.equal(reviews, 2);
  assert.equal(await readFile(settings, 'utf8'), settingsBytes);
  const statePath = path.join(m.packetDir, 'state.json');
  const waiting = JSON.parse(await readFile(statePath, 'utf8'));
  waiting.status = 'PRODUCT_CHECK_WAIT'; waiting.phase = 'PRODUCT_CHECK_WAIT';
  await writeFile(statePath, JSON.stringify(waiting, null, 2) + '\n');
  let verificationCalls = 0;
  const verified = await verifyProductDirect(manifest, undefined, {
    async claimTask() { return { status: 'ALREADY_CLAIMED' }; },
    async verifyProductCheck() { verificationCalls++; return { status: 'WAITING_FOR_CHECKPOINT', task_id: task.task_id, product_check: { status: 'PASS' } }; }
  });
  assert.deepEqual(verified, { status: 'WAITING_FOR_CHECKPOINT', task_id: task.task_id, product_check: 'PASS', provider_invocations: 0 });
  assert.equal(verificationCalls, 1);
});

test('prepareDirect and checkDirect reject former Gemini reviewer, incorrect Luna model/effort bindings, and fallback reviewer', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'direct-reviewer-validation-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo'), output = path.join(root, 'packet'), worktrees = path.join(root, 'worktrees');
  await mkdir(repo);
  const git = (...args) => execFileSync('git', args, { cwd: repo, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  git('init'); git('config', 'user.name', 'Direct test'); git('config', 'user.email', 'test@example.invalid');
  await writeFile(path.join(repo, '.gitignore'), '.workflow-local/\n');
  await writeFile(path.join(repo, 'file.txt'), 'hello\n');
  git('add', '.'); git('commit', '-m', 'fixture');
  const task = { schema_version: 'qq.workflow.task.v10.1', task_id: 'TASK-REVIEWER-CHECK', revision: 1, base_sha: git('rev-parse', 'HEAD'), goal: 'validate reviewer', acceptance_criteria: ['reject bad reviewer'],
    gates: [{ id: 'noop', argv: [process.execPath, '-p', '1'], timeout_seconds: 20 }], user_visible: false, risk: 'LOW', complexity: 'SIMPLE', execution: { policy: 'CONTROLLED_DELEGATION_V1' },
    write_paths: ['file.txt'], allowed_paths: ['file.txt'], lane: 'NORMAL', initial_lane: 'NORMAL', initial_risk: 'LOW', product_check: { applicable: false, reason: 'fixture' }, candidate_head: null, contract_sha256: null };
  const validConfig = JSON.parse((await readFile(new URL('../../.ai-workflow/BRIDGE_CONFIG.direct.example.json', import.meta.url), 'utf8')).replace(/^\uFEFF/, ''));
  validConfig.write_paths = task.write_paths;
  validConfig.worker.local_trial_root = worktrees;
  assert.equal(validConfig.reviewer.model, 'gpt-6-luna');
  assert.equal(validConfig.reviewer.effort, 'max');
  assert.equal(validConfig.fallback_reviewer, undefined);

  for (const [name, changedTask, changedConfig] of [
    ['v2', { ...task, execution: { policy: 'CONTROLLED_DELEGATION_V2' } }, validConfig],
    ['fast', { ...task, lane: 'FAST', initial_lane: 'FAST' }, validConfig],
    ['local-auto', task, { ...validConfig, mode: 'LOCAL_AUTO' }]
  ]) {
    await assert.rejects(prepareDirect(repo, changedTask, changedConfig, path.join(root, `retired-${name}`)), /NEW_ROUTE_RETIRED/);
  }

  const formerGemini = { provider: 'google', cli: 'antigravity', command: ['agy'], model: 'gemini-3.8-flash-high', agent: 'ag-reviewer-011-r1', agent_definition_sha256: 'ef23a4c0616a77d1d5b982c02ea07ec281c00de5d86aaa42523714b0678bdaea' };

  const invalidReviewers = [formerGemini, { ...validConfig.reviewer, model: 'gpt-5.6-luna' }, { ...validConfig.reviewer, model: 'gpt-5.6-terra' },
    { ...validConfig.reviewer, effort: 'high' }, { ...validConfig.reviewer, provider: 'google' },
    { ...validConfig.reviewer, cli: 'antigravity' }];
  for (const [index, reviewer] of invalidReviewers.entries()) {
    await assert.rejects(prepareDirect(repo, task, { ...validConfig, reviewer }, path.join(root, `out-${index}`)), /LUNA_REVIEWER_REQUIRED/);
  }
  await assert.rejects(prepareDirect(repo, task, { ...validConfig, fallback_reviewer: { provider: 'openai', cli: 'codex', command: ['codex'], model: 'gpt-5.6-terra', effort: 'high' } }, path.join(root, 'out-fallback')), /FALLBACK_REQUIRES_SEPARATE_DECISION/);

  const m = await prepareDirect(repo, task, validConfig, output);
  const preparedConfig = JSON.parse(await readFile(m.configPath));
  const testCheckDirectRejection = async (badReviewer, badFallback, expectedCode) => {
    const testConfig = structuredClone(preparedConfig);
    if (badReviewer) testConfig.reviewer = badReviewer;
    if (badFallback) testConfig.fallback_reviewer = badFallback;
    const testRoot = path.join(path.dirname(m.taskPath), `chk-${Math.random().toString(36).slice(2)}`);
    await mkdir(testRoot, { recursive: true });
    const taskPath = path.join(testRoot, `${task.task_id}.json`);
    const configPath = path.join(testRoot, 'config.json');
    const preparedPath = path.join(testRoot, 'prepared.json');
    const clonedTask = structuredClone(task);
    await writeFile(taskPath, JSON.stringify(clonedTask));
    await freezeControlledTask(taskPath, clonedTask, testConfig);
    await writeFile(configPath, JSON.stringify(testConfig));
    await writeFile(preparedPath, JSON.stringify({ repo, taskPath, configPath, packetDir: path.join(testRoot, 'packet'), helper_sha256: m.helper_sha256 }));
    await assert.rejects(checkDirect(preparedPath), new RegExp(expectedCode));
  };

  await testCheckDirectRejection(formerGemini, undefined, 'LUNA_REVIEWER_REQUIRED');
  await testCheckDirectRejection({ ...validConfig.reviewer, model: 'gpt-5.6-terra' }, undefined, 'LUNA_REVIEWER_REQUIRED');
  await testCheckDirectRejection({ ...validConfig.reviewer, effort: 'high' }, undefined, 'LUNA_REVIEWER_REQUIRED');
  await testCheckDirectRejection(undefined, { provider: 'openai', cli: 'codex', command: ['codex'], model: 'gpt-5.6-terra', effort: 'high' }, 'FALLBACK_REQUIRES_SEPARATE_DECISION');

  const legacyRoot = path.join(root, 'legacy-output'), legacyTaskDir = path.join(path.dirname(m.taskPath), 'legacy');
  await Promise.all([mkdir(legacyRoot), mkdir(legacyTaskDir)]);
  const legacySettings = path.join(root, 'legacy-settings.json');
  await writeFile(legacySettings, JSON.stringify({ permissions: { allow: [], deny: [] } }));
  const legacyConfig = structuredClone(preparedConfig);
  legacyConfig.reviewer = { ...legacyConfig.reviewer, model: 'gpt-5.6-luna', command: [process.execPath] };
  legacyConfig.worker.command[0] = process.execPath;
  legacyConfig.worker.cli = process.execPath;
  legacyConfig.worker.temporary_permissions.settingsPath = legacySettings;
  legacyConfig.lifecycle.control_root = legacyRoot;
  const legacyTaskPath = path.join(legacyTaskDir, `${task.task_id}.json`);
  const legacyConfigPath = path.join(legacyRoot, 'config.json');
  await freezeControlledTask(legacyTaskPath, task, legacyConfig);
  await writeFile(legacyConfigPath, JSON.stringify(legacyConfig));
  const legacyManifestPath = path.join(legacyRoot, 'prepared.json');
  await writeFile(legacyManifestPath, JSON.stringify({ repo, taskPath: legacyTaskPath, configPath: legacyConfigPath,
    packetDir: path.join(legacyRoot, task.task_id), helper_sha256: m.helper_sha256 }));
  assert.equal((await checkDirect(legacyManifestPath)).config.reviewer.model, 'gpt-5.6-luna');
});

test('visible Direct preparation proves good/bad gate behavior and Product Check shape before freezing', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'direct-preflight-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo'), good = path.join(root, 'good'), bad = path.join(root, 'bad'), output = path.join(root, 'output');
  await Promise.all([mkdir(repo), mkdir(good), mkdir(bad)]);
  const git = (...args) => execFileSync('git', args, { cwd: repo, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  git('init'); git('config', 'user.name', 'Direct test'); git('config', 'user.email', 'test@example.invalid');
  await writeFile(path.join(repo, '.gitignore'), '.workflow-local/\n');
  await writeFile(path.join(repo, 'check.mjs'), `import {readFileSync, existsSync} from 'node:fs';
import {pathToFileURL} from 'node:url'; import path from 'node:path';
const ok=readFileSync('page.html','utf8').trim()==='good';
const item=(id,kind)=>({[kind+'_id']:id,status:'PASS',observed_result:'observed',...(existsSync('omit-evidence')?{}:{evidence:'local fixture'})});
process.stdout.write(JSON.stringify({schema_version:'qq.workflow.product-check-result.v1',status:ok?'PASS':'FAIL',target_url:pathToFileURL(path.resolve('page.html')).href,criterion_results:[item('page','criterion')],action_results:[item('open','action')]}));
if(!ok)process.exitCode=1;`);
  git('add', '.'); git('commit', '-m', 'fixture');
  await writeFile(path.join(good, 'page.html'), 'good\n');
  await writeFile(path.join(bad, 'page.html'), 'bad\n');
  const task = { schema_version: 'qq.workflow.task.v10.1', task_id: 'TASK-PREFLIGHT-TEST', revision: 1, base_sha: git('rev-parse', 'HEAD'), goal: 'new page',
    acceptance_criteria: ['open the page'], gates: [{ id: 'page', argv: [process.execPath, 'check.mjs'], timeout_seconds: 10 },
      { id: 'noop', argv: [process.execPath, '-e', 'process.exit(0)'], timeout_seconds: 10 }],
    user_visible: true, risk: 'LOW', complexity: 'SIMPLE', execution: { policy: 'CONTROLLED_DELEGATION_V1' },
    write_paths: ['page.html'], allowed_paths: ['page.html'], lane: 'NORMAL', initial_lane: 'NORMAL', initial_risk: 'LOW',
    product_check: { target_url: pathToFileURL(path.join(root, 'worktrees', 'TASK-PREFLIGHT-TEST', 'page.html')).href,
      criteria: [{ id: 'page', text: 'page' }], actions: [{ id: 'open', text: 'open' }] }, candidate_head: null };
  const config = { schema_version: 'qq.bridge.v2', billing: 'SUBSCRIPTION_ONLY', mode: 'ASSISTED', timeout_seconds: 20,
    write_paths: task.write_paths, gate_paths: ['check.mjs'],
    worker: { transport: 'mcp', server: 'antigravity_worker', provider: 'mcp', command: ['python', 'placeholder'], model: 'gemini-3.8-flash-high', local_trial: true, local_trial_root: path.join(root, 'worktrees'), skip_permissions: false },
    reviewer: { provider: 'openai', cli: 'codex', command: ['codex'], model: 'gpt-6-luna', effort: 'max' },
    product_check: { command: [process.execPath, 'check.mjs'] },
    preflight: { good_cwd: good, bad_cwd: bad, good_target_url: pathToFileURL(path.join(good, 'page.html')).href } };
  await assert.rejects(prepareDirect(repo, task, { ...config, preflight: undefined }, output), /PREFLIGHT_REQUIRED/);
  assert.equal(existsSync(output), false);
  await writeFile(path.join(good, 'omit-evidence'), '1');
  await assert.rejects(prepareDirect(repo, task, config, output), /PREFLIGHT_PRODUCT_INVALID/);
  assert.equal(existsSync(output), false);
  await rm(path.join(good, 'omit-evidence'));
  await writeFile(path.join(bad, 'page.html'), 'good\n');
  await assert.rejects(prepareDirect(repo, task, config, output), /PREFLIGHT_BAD_GATE_PASSED/);
  await writeFile(path.join(bad, 'page.html'), 'bad\n');
  await writeFile(path.join(good, 'page.html'), 'bad\n');
  await assert.rejects(prepareDirect(repo, task, config, output), /PREFLIGHT_GOOD_GATE_FAILED/);
  await writeFile(path.join(good, 'page.html'), 'good\n');
  const prepared = await prepareDirect(repo, task, config, output);
  assert.equal(prepared.repo, repo);
  assert.equal(existsSync(path.join(output, 'prepared.json')), true);
  await writeFile(path.join(good, 'omit-evidence'), '1');
  await assert.rejects(checkDirect(path.join(output, 'prepared.json')), /PREFLIGHT_PRODUCT_INVALID/);
  await checkDirect(path.join(output, 'prepared.json'), { allowExisting: true }).catch(error => {
    assert.doesNotMatch(error.message, /PREFLIGHT_/);
  });
});
