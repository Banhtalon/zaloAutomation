import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { access, mkdir, readFile, writeFile, realpath, readdir, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as lifecycle from './harness-lifecycle.mjs';
import { freezeControlledTask, assertControlledContract, controlledConfigHash, validateControlledTask, resolveProductCheckContract, validateProductCheckResult } from './controlled-bridge.mjs';
import { assertSubscriptionSettings, validateReviewerBinding } from './bridge-adapters.mjs';
import { assertWorkerIsolation } from './implementation-worker.mjs';
import { auditSummary } from './harness-observability.mjs';
import { runRedacted } from './redact.mjs';
import { aggregateInvocations } from './report.mjs';

const sourceRoot = fileURLToPath(new URL('../../', import.meta.url));
const json = async file => JSON.parse((await readFile(file, 'utf8')).replace(/^\uFEFF/, ''));
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const git = (repo, ...args) => execFileSync('git', args, { cwd: repo, encoding: 'utf8', windowsHide: true }).trim();
const writeNew = async (file, value) => writeFile(file, JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
const requireValue = (ok, code) => { if (!ok) throw Object.assign(Error(code), { code }); };
const completedAttempts = state => {
  const recorded = Number.isInteger(state.attempt) ? state.attempt : null;
  const latest = state.latest_execution;
  const verified = latest?.status === 'SUCCEEDED' && latest.agent_status === 'SUCCESS' && latest.exit_code === 0 && Number.isInteger(latest.attempt) ? latest.attempt : null;
  return recorded === null && verified === null ? null : Math.max(recorded ?? 0, verified ?? 0);
};
const completedRepairs = state => { const attempts = completedAttempts(state); return attempts === null ? null : Math.max(0, attempts - 1); };
export function summarizeDirectUsage(state, config) {
  const items = [];
  if (state?.latest_execution) items.push({
    role: 'worker',
    provider: config.worker?.provider ?? 'unavailable',
    requested_model: config.worker?.model ?? 'unavailable',
    observed_models: state.latest_execution.observed_model ? [state.latest_execution.observed_model] : [],
    usage: state.latest_execution.usage ?? null
  });
  if (state?.review_result) items.push({
    role: 'reviewer',
    provider: config.reviewer?.provider ?? 'unavailable',
    requested_model: config.reviewer?.model ?? 'unavailable',
    observed_models: state.review_result.observed_models ?? [],
    usage: state.review_result.usage ?? null
  });
  return {
    worker_usage: state?.latest_execution?.usage ?? null,
    reviewer_usage: state?.review_result?.usage ?? null,
    usage: aggregateInvocations(items, true).usage
  };
}
const inside = (root, file) => { const rel = path.relative(root, file); return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel)); };
const errorCode = error => /^[A-Z][A-Z0-9_]{0,63}$/.test(error?.code ?? '') ? error.code : 'DIRECT_RUN_FAILED';
const requireDirectReviewer = (config, { allowLegacy = false } = {}) => {
  requireValue(config.reviewer?.provider === 'openai' && config.reviewer?.cli === 'codex' &&
    (config.reviewer?.model === 'gpt-6-luna' || (allowLegacy && config.reviewer?.model === 'gpt-5.6-luna')) &&
    config.reviewer?.effort === 'max', 'LUNA_REVIEWER_REQUIRED');
  requireValue(!config.fallback_reviewer, 'FALLBACK_REQUIRES_SEPARATE_DECISION');
};

const fixtureCommand = (repo, argv) => argv.map((arg, index) => index > 0 && !path.isAbsolute(arg) && /\.(?:[cm]?js|py|ps1|sh)$/i.test(arg) && existsSync(path.join(repo, arg)) ? path.join(repo, arg) : arg);
const reportedStatus = stdout => { try { return JSON.parse(stdout.trim()).status; } catch { return null; } };

async function qualifyPreparation(repo, task, config, { required = false } = {}) {
  const probe = config.preflight;
  if (!probe) { requireValue(!required, 'PREFLIGHT_REQUIRED'); return; }
  requireValue(typeof probe.good_cwd === 'string' && path.isAbsolute(probe.good_cwd) &&
    typeof probe.bad_cwd === 'string' && path.isAbsolute(probe.bad_cwd) &&
    typeof probe.good_target_url === 'string' && probe.good_target_url.trim(), 'PREFLIGHT_INVALID');
  const [good, bad] = await Promise.all([stat(probe.good_cwd).catch(() => null), stat(probe.bad_cwd).catch(() => null)]);
  requireValue(good?.isDirectory() && bad?.isDirectory() &&
    (await realpath(probe.good_cwd)).toLowerCase() !== (await realpath(probe.bad_cwd)).toLowerCase(), 'PREFLIGHT_FIXTURE_INVALID');
  let wrongFixtureRejected = false;
  for (const gate of task.gates) {
    const argv = fixtureCommand(repo, gate.argv);
    const positive = await runRedacted(argv, { cwd: probe.good_cwd, timeoutSeconds: gate.timeout_seconds });
    const positiveStatus = reportedStatus(positive.stdout);
    requireValue(positive.code === 0 && !positive.timed_out && !positive.interrupted &&
      (positiveStatus === null || positiveStatus === 'PASS'), 'PREFLIGHT_GOOD_GATE_FAILED');
    const negative = await runRedacted(argv, { cwd: probe.bad_cwd, timeoutSeconds: gate.timeout_seconds });
    requireValue(!negative.timed_out && !negative.interrupted, 'PREFLIGHT_BAD_GATE_INVALID');
    wrongFixtureRejected ||= negative.code !== 0 && reportedStatus(negative.stdout) !== 'PASS';
  }
  requireValue(wrongFixtureRejected, 'PREFLIGHT_BAD_GATE_PASSED');
  const command = config.product_check?.command;
  requireValue(Array.isArray(command) && command.length > 0, 'PREFLIGHT_PRODUCT_REQUIRED');
  const result = await runRedacted(fixtureCommand(repo, command), { cwd: probe.good_cwd, timeoutSeconds: config.timeout_seconds ?? 300 });
  requireValue(result.code === 0 && !result.timed_out, 'PREFLIGHT_PRODUCT_FAILED');
  let parsed;
  try { parsed = JSON.parse(result.stdout.trim()); } catch { requireValue(false, 'PREFLIGHT_PRODUCT_INVALID'); }
  try {
    const contract = resolveProductCheckContract(task.product_checks ?? task.product_check);
    validateProductCheckResult(parsed, { ...contract, targetUrl: probe.good_target_url });
  } catch { requireValue(false, 'PREFLIGHT_PRODUCT_INVALID'); }
}

// One host/account writer at a time, as required by the temporary-permissions helper.
export async function prepareDirect(repo, task, config, output) {
  repo = path.resolve(repo); output = path.resolve(output);
  validateControlledTask(task);
  requireValue((task.execution?.policy ?? task.policy) === 'CONTROLLED_DELEGATION_V1' && task.lane !== 'FAST' && config.mode === 'ASSISTED', 'NEW_ROUTE_RETIRED');
  requireValue(/^TASK-[A-Z0-9_-]+$/i.test(task.task_id), 'INVALID_TASK_ID');
  requireValue(git(repo, 'rev-parse', '--show-toplevel').toLowerCase() === repo.replaceAll('\\', '/').toLowerCase(), 'REPO_ROOT_REQUIRED');
  requireValue(path.dirname(path.resolve(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))).toLowerCase() === repo.toLowerCase(), 'MAIN_CHECKOUT_REQUIRED');
  requireValue(git(repo, 'rev-parse', 'HEAD') === task.base_sha, 'BASE_MISMATCH');
  config = structuredClone(config);
  requireValue(!config.test_mode && config.worker?.local_trial === true, 'LOCAL_TRIAL_REQUIRED');
  requireValue(config.worker.skip_permissions !== true && config.billing === 'SUBSCRIPTION_ONLY', 'UNSAFE_CONFIG');
  requireDirectReviewer(config);
  config.lifecycle = { ...config.lifecycle, max_rework: 4, checkpoint_required: true, control_root: output,
    desired_state_path: '.workflow-local/ai-control.desired_state', lease_seconds: Math.min(3600, (config.timeout_seconds ?? 300) * 4 + 120) };
  assertWorkerIsolation(config, { repoRoot: repo, controlRoot: output });
  const root = path.join(repo, '.workflow-local', 'direct', task.task_id);
  requireValue(!existsSync(root) && !existsSync(output), 'OUTPUT_EXISTS');
  await qualifyPreparation(repo, task, config, { required: task.user_visible === true });
  const taskPath = path.join(root, `${task.task_id}.json`);
  const packetDir = path.join(output, task.task_id);
  const helper = path.join(root, 'antigravity_server.py');
  const helperBytes = await readFile(path.join(sourceRoot, 'mcp', 'antigravity_server.py'));
  requireValue(config.worker.command?.length === 2, 'INVALID_WORKER_COMMAND');
  config.worker.command[1] = helper;
  const workspace = path.join(config.worker.local_trial_root, task.task_id);
  const files = task.allowed_paths ?? task.write_paths;
  assert.deepEqual([...files].sort(), [...config.write_paths].sort());
  for (const file of files) requireValue(typeof file === 'string' && !path.isAbsolute(file) && !file.includes('\\') &&
    !file.split('/').some(part => ['..', '.', '', '.git', '.workflow-local'].includes(part)) && inside(workspace, path.resolve(workspace, file)), 'SCOPE_ESCAPE');
  config.worker.temporary_permissions = { enabled: true,
    settingsPath: path.join(os.homedir(), '.gemini', 'antigravity-cli', 'settings.json'),
    files: files.map(file => path.resolve(workspace, file)) };
  await mkdir(root, { recursive: true }); await mkdir(output, { recursive: true });
  requireValue((await realpath(root)).toLowerCase() === root.toLowerCase(), 'SYMLINK_CONTROL_PATH');
  await writeFile(helper, helperBytes, { flag: 'wx' });
  const control = path.join(repo, config.lifecycle.desired_state_path);
  if (!existsSync(control)) await writeFile(control, 'running\n', { flag: 'wx' });
  // Existing paused/stopped state is deliberately preserved.
  await writeNew(taskPath, task);
  await freezeControlledTask(taskPath, task, config);
  const configPath = path.join(output, 'config.json');
  await writeNew(configPath, config);
  const manifest = { repo, taskPath, configPath, packetDir, helper_sha256: digest(helperBytes),
    source_root: sourceRoot, source_head: git(sourceRoot, 'rev-parse', 'HEAD') };
  await writeNew(path.join(output, 'prepared.json'), manifest);
  return manifest;
}

export async function checkDirect(manifestPath, { allowExisting = false } = {}) {
  const m = await json(manifestPath), task = await json(m.taskPath), config = await json(m.configPath);
  requireValue(inside(m.repo, m.taskPath), 'TASK_OUTSIDE_REPO');
  requireValue(git(path.dirname(m.taskPath), 'rev-parse', '--show-toplevel').toLowerCase() === m.repo.replaceAll('\\', '/').toLowerCase(), 'TASK_REPO_MISMATCH');
  requireValue(git(m.repo, 'rev-parse', 'HEAD') === task.base_sha, 'BASE_MISMATCH');
  await assertControlledContract(m.taskPath, task);
  requireValue(controlledConfigHash(config) === task.config_sha256, 'CONFIG_MISMATCH');
  requireDirectReviewer(config, { allowLegacy: true });
  requireValue([2, 4].includes(config.lifecycle.max_rework) && config.lifecycle.checkpoint_required === true && !config.fallback_reviewer, 'BUDGET_MISMATCH');
  requireValue(config.worker.local_trial === true && !config.test_mode && !config.worker.skip_permissions, 'UNSAFE_CONFIG');
  assertWorkerIsolation(config, { repoRoot: m.repo, controlRoot: path.dirname(manifestPath) });
  requireValue((await lifecycle.readDesiredState(m.repo, config)).state === 'running', 'DESIRED_STATE_BLOCKED');
  if (!allowExisting) {
    requireValue(!existsSync(path.join(m.packetDir, 'state.json')), 'EXISTING_RUN_REQUIRES_DECISION');
    requireValue(!existsSync(path.join(path.dirname(path.resolve(manifestPath)), 'dispatch.json')), 'EXISTING_RUN_REQUIRES_DECISION');
  }
  requireValue(digest(await readFile(config.worker.command[1])) === m.helper_sha256, 'HELPER_CHANGED');
  requireValue(m.helper_sha256 === digest(await readFile(path.join(sourceRoot, 'mcp', 'antigravity_server.py'))), 'HELPER_SOURCE_MISMATCH');
  if (config.preflight && !allowExisting) await qualifyPreparation(m.repo, task, config);
  const settings = await json(config.worker.temporary_permissions.settingsPath);
  assertSubscriptionSettings(settings);
  requireValue(Array.isArray(settings.permissions?.allow), 'INVALID_PERMISSIONS');
  requireValue(!config.worker.temporary_permissions.files.some(file => settings.permissions.allow.includes(`write_file(${file})`)), 'EXISTING_TASK_PERMISSION');
  validateReviewerBinding(config.reviewer);
  // Resolve executables without invoking providers or spending a model call.
  for (const executable of [config.worker.command[0], config.worker.cli ?? 'agy', config.reviewer.command[0]]) {
    if (path.isAbsolute(executable)) await access(executable);
    else execFileSync(process.platform === 'win32' ? 'where.exe' : 'which', [executable], { stdio: 'pipe', windowsHide: true });
  }
  return { manifest: m, task, config };
}

export async function runBounded(options, api = lifecycle) {
  try {
    let state = await api.runHarnessLifecycle(options);
    for (let repair = 1; repair < options.config.lifecycle.max_rework && state.status === 'REQUEST_CHANGES'; repair++) {
      const instruction = state.requested_changes?.at(-1)?.instruction;
      requireValue(typeof instruction === 'string' && instruction.length > 0, 'REPAIR_INSTRUCTION_MISSING');
      state = await api.continueHarnessLifecycle({ ...options, instruction:
        `Use only file tools on the frozen allowed paths. Do not read/run gates, shell or control files; the controller runs tests.\n${instruction}` });
    }
    return state;
  } finally { await api.closeLifecycleWorkers(); }
}

export async function runDirect(manifestPath, signal) {
  const output = path.dirname(path.resolve(manifestPath)), runId = randomUUID();
  const started = Date.now();
  let prepared, rulesBefore;
  let report = { schema_version: 'qq.direct-run.v1', run_id: runId, mode: 'live', status: 'BLOCKED', phase: 'preflight', provider_invocations: 0 };
  try {
    prepared = await checkDirect(manifestPath);
    const { manifest: m, config, task } = prepared;
    const settingsBefore = await json(config.worker.temporary_permissions.settingsPath);
    rulesBefore = JSON.stringify(settingsBefore.permissions ?? {});
    // Immutable fence: never automatically redispatch a previous failed/crashed run.
    await writeNew(path.join(output, 'dispatch.json'), { run_id: runId, task_id: task.task_id });
    report = { ...report, task_id: task.task_id, phase: 'lifecycle', provider_invocations: 'unknown',
      source_head: git(sourceRoot, 'rev-parse', 'HEAD'), config_sha256: task.config_sha256, packet_dir: m.packetDir };
    const state = await runBounded({ taskPath: m.taskPath, config, packetDir: m.packetDir, owner: `direct-${task.task_id}`, signal });
    report.status = state.status; report.phase = state.phase;
    if (state.error) report.error_code = errorCode(state.error);
  } catch (error) {
    report.status = 'BLOCKED';
    report.error_code = errorCode(error);
  } finally {
    if (prepared) {
      const { manifest: m, config } = prepared;
      try {
        if (existsSync(path.join(m.packetDir, 'state.json'))) {
          const saved = await json(path.join(m.packetDir, 'state.json'));
          report.worker_attempts_completed = completedAttempts(saved);
          report.repairs = completedRepairs(saved);
          report.change_requests = saved.rework_count;
          report.review = saved.review_result?.verdict ?? null;
          report.product_check = saved.product_check?.status ?? (saved.product_check === null ? 'NOT_APPLICABLE' : null);
          report.worker_requested_model = config.worker.model;
          report.worker_model = saved.latest_execution?.observed_model ?? null;
          report.worker_session = saved.conversation_id ?? null;
          report.reviewer_session = saved.review_result?.reviewer_session ?? null;
          report.reviewer_models = saved.review_result?.observed_models ?? [];
          Object.assign(report, summarizeDirectUsage(saved, config));
          const reviewMetrics = Array.isArray(saved.history)
            ? saved.history.filter(item => item.phase === 'review' && item.review_prompt_metrics).map(item => item.review_prompt_metrics)
            : [];
          report.review_prompt_metrics = reviewMetrics.length > 0 ? reviewMetrics : (saved.review_result?.review_prompt_metrics ? [saved.review_result.review_prompt_metrics] : []);
          report.changed_files = saved.changed_files?.map(file => file.path) ?? [];
          report.gates = saved.tests?.map(test => ({ id: test.id, code: test.code, timed_out: test.timed_out })) ?? [];
          const audit = await auditSummary(m.packetDir);
          report.worker_dispatches = audit.invalid_lines ? null : audit.operations.dispatch_started ?? 0;
          const reviews = (await readdir(m.packetDir)).filter(name => name.startsWith('review-'));
          report.reviewer_dispatches = 0;
          for (const name of reviews) if (existsSync(path.join(m.packetDir, name, 'receipts', 'assignment.json'))) report.reviewer_dispatches++;
          // Dispatch records prove attempts, not the provider's internal model calls.
          report.provider_invocations = 'unknown';
          report.evidence = { state: path.join(m.packetDir, 'state.json'), operations: path.join(m.packetDir, 'operations.jsonl') };
        }
      } catch { report.status = 'BLOCKED'; report.evidence_error = 'REPORT_EVIDENCE_UNAVAILABLE'; }
      try {
        const after = await json(config.worker.temporary_permissions.settingsPath);
        assertSubscriptionSettings(after);
        const temporary = config.worker.temporary_permissions.files.map(file => `write_file(${file})`);
        report.temporary_rules_remaining = temporary.filter(rule => after.permissions?.allow?.includes(rule)).length;
        report.existing_permissions_unchanged = rulesBefore === JSON.stringify(after.permissions ?? {});
        requireValue(report.temporary_rules_remaining === 0, 'CLEANUP_UNVERIFIED');
        requireValue(report.existing_permissions_unchanged, 'PERMISSIONS_CHANGED');
      } catch (error) { report.status = 'BLOCKED'; report.cleanup_error = errorCode(error); }
    }
  }
  report.duration_ms = Date.now() - started;
  await writeNew(path.join(output, `${runId}.json`), report);
  return report;
}

export async function statusDirect(manifestPath) {
  const m = await json(manifestPath);
  const state = await json(path.join(m.packetDir, 'state.json'));
  const owner_message = {
    WAITING_FOR_CHECKPOINT: 'Đã đạt các kiểm tra bắt buộc; chờ Owner nghiệm thu.',
    CHECKPOINTED: 'Đã ghi nhận nghiệm thu; Lead tiếp tục bước kết thúc.',
    COMPLETED: 'Đã hoàn tất trong vùng làm việc riêng; chưa xác nhận đưa vào dự án chính. Lead xác minh việc gộp sau khi Owner cho phép.',
    PRODUCT_CHECK_WAIT: 'Đang chờ kiểm tra sản phẩm; Lead xử lý điều kiện kiểm tra, không gọi lại AI.',
    RECOVERY_REQUIRED: 'Kết quả thao tác trước chưa rõ; Lead đối soát trước khi tiếp tục.',
    PAUSED: 'Đang tạm dừng; Lead kiểm tra yêu cầu tiếp tục.',
    RUNNING: 'Đang thực hiện; Lead theo dõi kết quả.',
    READY_FOR_REVIEW: 'Đang chờ đánh giá độc lập; Lead xử lý bước review.',
    REQUEST_CHANGES: 'Cần sửa theo kết quả kiểm tra; Lead xử lý trong số lượt còn lại.',
    RETRY_EXHAUSTED: 'Đã hết số lượt sửa; Lead báo trở ngại, không tự chạy lại.',
    CHECKPOINT_REJECTED: 'Chưa được nghiệm thu; Lead xử lý lý do từ chối.',
    LEASE_EXPIRED: 'Quyền giữ tác vụ đã hết hạn; Lead đối soát trước khi tiếp tục.'
  }[state.status] ?? 'Chưa xác nhận hoàn tất; Lead kiểm tra hồ sơ và xử lý bước tiếp theo.';
  const reason = /^[A-Z][A-Z0-9_]{0,63}$/.test(state.error?.code ?? '') ? ` Mã nguyên nhân: ${state.error.code}.` : '';
  return { status: state.status, owner_message: owner_message + reason, task_id: state.task_id, attempts: completedAttempts(state),
    repairs: completedRepairs(state), change_requests: state.rework_count ?? null,
    review: state.review_result?.verdict ?? null, product_check: state.product_check?.status ?? (state.product_check === null ? 'NOT_APPLICABLE' : null), approved: state.checkpoint?.approved === true };
}

export async function verifyProductDirect(manifestPath, signal, api = lifecycle) {
  const { manifest: m, config } = await checkDirect(manifestPath, { allowExisting: true });
  const state = await json(path.join(m.packetDir, 'state.json'));
  requireValue(state.status === 'PRODUCT_CHECK_WAIT', 'PRODUCT_CHECK_NOT_WAITING');
  const claim = await api.claimTask(m.taskPath, { owner: state.owner, leaseSeconds: 300 });
  requireValue(['CLAIMED', 'ALREADY_CLAIMED'].includes(claim.status), 'CLAIM_FAILED');
  const result = await api.verifyProductCheck({ taskPath: m.taskPath, packetDir: m.packetDir, config, owner: state.owner, signal });
  return { status: result.status, task_id: result.task_id, product_check: result.product_check?.status ?? null, provider_invocations: 0 };
}

export async function acceptDirect(manifestPath, approvedBy) {
  requireValue(typeof approvedBy === 'string' && approvedBy.trim().length > 0, 'APPROVER_REQUIRED');
  const m = await json(manifestPath), config = await json(m.configPath);
  const task = await json(m.taskPath);
  await assertControlledContract(m.taskPath, task);
  requireValue(controlledConfigHash(config) === task.config_sha256, 'CONFIG_MISMATCH');
  const state = await json(path.join(m.packetDir, 'state.json'));
  requireValue(['WAITING_FOR_CHECKPOINT', 'CHECKPOINTED', 'COMPLETED'].includes(state.status), 'CHECKPOINT_NOT_READY');
  if (state.status !== 'COMPLETED') requireValue(state.product_check === null || state.product_check?.status === 'PASS', 'PRODUCT_CHECK_REQUIRED');
  if (state.status !== 'COMPLETED') {
    const claim = await lifecycle.claimTask(m.taskPath, { owner: state.owner, leaseSeconds: 300 });
    requireValue(['CLAIMED', 'ALREADY_CLAIMED'].includes(claim.status), 'CLAIM_FAILED');
    const options = { taskPath: m.taskPath, packetDir: m.packetDir, config, owner: state.owner, approvedBy };
    await lifecycle.approveCheckpoint(options);
    await lifecycle.completeTask(options);
  }
  return statusDirect(manifestPath);
}
