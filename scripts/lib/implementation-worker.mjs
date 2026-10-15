import path from 'node:path';
import { access, lstat, realpath } from 'node:fs/promises';
import { McpClient } from './mcp-client.mjs';
import { subscriptionEnv } from './bridge-process.mjs';
import { redactText } from './redact.mjs';
import { harnessError } from './harness-errors.mjs';
import { normalizeUsage } from './receipts.mjs';

const REQUIRED_TOOLS = Object.freeze(['antigravity_execute', 'antigravity_continue', 'antigravity_result']);
const TRIAL_MODEL = 'gemini-3.8-flash-high';
const TASK_ID = /^TASK-[A-Z0-9_-]+$/i;
const MAX_EVIDENCE_BYTES = 32 * 1024;
const MAX_RESULT_TEXT = 8 * 1024;

function requireText(value, message) {
  if (typeof value !== 'string' || !value.trim()) throw harnessError('INVALID_INPUT', message);
  return value.trim();
}

export function boundedEvidence(value) {
  if (!Array.isArray(value)) return [];
  const encoded = redactText(JSON.stringify(value));
  if (Buffer.byteLength(encoded, 'utf8') <= MAX_EVIDENCE_BYTES) {
    try { return JSON.parse(encoded); } catch {}
  }
  return [{
    event: 'evidence_summary',
    source: 'antigravity.stdout.stream-json',
    event_count: value.length,
    truncated: true
  }];
}

export function trialMetadataError(result) {
  if (result.requested_model !== TRIAL_MODEL || result.observed_model !== TRIAL_MODEL) {
    return 'local_trial result is missing or has a different model identity';
  }
  const permissionMode = typeof result.permission_mode === 'string' ? result.permission_mode.trim().toLowerCase() : null;
  if (permissionMode === 'always-proceed') return 'local_trial result explicitly enables always-proceed permissions';
  if (result.sandbox === false) return 'local_trial result explicitly disables sandbox';
  return null;
}

function samePath(left, right) {
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function overlaps(left, right) {
  return under(left, right) || under(right, left);
}

export function assertWorkerIsolation(config, { repoRoot, controlRoot } = {}) {
  const worker = config?.worker ?? {};
  const localTrial = worker.local_trial === true;
  if (worker.local_trial !== undefined && typeof worker.local_trial !== 'boolean') {
    throw harnessError('CONFIG_MISMATCH', 'worker.local_trial must be true or false');
  }
  if (config?.test_mode === true && localTrial) {
    throw harnessError('CONFIG_MISMATCH', 'test_mode and worker.local_trial cannot be enabled together');
  }
  if (localTrial) {
    if (config?.production === true) throw harnessError('CONFIG_MISMATCH', 'local_trial is not allowed for production');
    if (worker.model !== TRIAL_MODEL) throw harnessError('CONFIG_MISMATCH', `local_trial requires worker.model=${TRIAL_MODEL}`);
    if (worker.skip_permissions === true) throw harnessError('CONFIG_MISMATCH', 'local_trial requires skip_permissions=false');
    if (typeof worker.local_trial_root !== 'string' || !path.isAbsolute(worker.local_trial_root)) {
      throw harnessError('WORKTREE_ROOT_INVALID', 'local_trial_root must be an absolute path');
    }
    const root = path.resolve(worker.local_trial_root);
    for (const forbidden of [repoRoot, controlRoot].filter(Boolean)) {
      if (overlaps(root, forbidden)) throw harnessError('WORKTREE_ROOT_INVALID', 'local_trial_root must be outside the repository and control roots');
    }
    return { mode: 'local-trial', root, model: TRIAL_MODEL };
  }
  if (worker.local_trial_root !== undefined) {
    throw harnessError('CONFIG_MISMATCH', 'local_trial_root requires worker.local_trial=true');
  }
  // The current Windows host has no restricted worker identity, ACL launcher,
  // or sandbox. Test fixtures inject a fake worker and opt into test_mode;
  // real MCP dispatch fails closed until an OS boundary is supplied.
  if (config?.test_mode === true) return { mode: 'test-fixture' };
  throw harnessError(
    'WORKER_ISOLATION_UNAVAILABLE',
    'Antigravity worker isolation is unavailable; refusing to start an unrestricted worker'
  );
}

function operationArgs(operation) {
  if (!operation || typeof operation !== 'object' || typeof operation.id !== 'string' ||
      !['execute', 'continue'].includes(operation.kind) ||
      !Number.isInteger(operation.attempt) || operation.attempt < 1 ||
      !Number.isInteger(operation.rework_count) || operation.rework_count < 0) {
    throw harnessError('MCP_RESULT_CORRELATION_MISMATCH', 'external worker operation metadata is invalid');
  }
  return {
    operation_id: operation.id,
    invocation_kind: operation.kind,
    attempt: operation.attempt,
    rework_count: operation.rework_count
  };
}

function under(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}

export class ImplementationWorker {
  async execute() { throw new Error('ImplementationWorker.execute is not implemented'); }
  async continue() { throw new Error('ImplementationWorker.continue is not implemented'); }
  async result() { throw new Error('ImplementationWorker.result is not implemented'); }
  async close() {}
}

export class AntigravityMcpWorker extends ImplementationWorker {
  constructor({ repoRoot, worktreeRoot, config, timeoutSeconds = 300 } = {}) {
    super();
    this.repoRoot = path.resolve(repoRoot);
    this.worktreeRoot = path.resolve(worktreeRoot);
    this.config = config;
    this.timeoutSeconds = timeoutSeconds;
    this.client = null;
    this.toolNames = [];
  }

  #command() {
    const binding = this.config?.worker;
    if (binding?.transport !== 'mcp' || binding.server !== 'antigravity_worker') {
      throw harnessError('CONFIG_MISMATCH', 'MCP Antigravity worker binding is required');
    }
    if (!Array.isArray(binding.command) || binding.command.length < 1 || binding.command.length > 2) {
      throw harnessError('CONFIG_MISMATCH', 'MCP worker command must contain an executable and optional script');
    }
    if (binding.command.some(value => typeof value !== 'string' || !value.trim() || /[\x00\r\n]/.test(value))) {
      throw harnessError('CONFIG_MISMATCH', 'MCP worker command contains an invalid value');
    }
    const command = [...binding.command];
    if (command.length === 2 && !path.isAbsolute(command[1])) command[1] = path.resolve(this.repoRoot, command[1]);
    if (command.length === 2 && !under(this.repoRoot, path.resolve(command[1]))) {
      throw harnessError('WORKER_UNAVAILABLE', 'MCP server script must be inside the project root');
    }
    return command;
  }

  async #connect() {
    if (this.client) return;
    const mode = assertWorkerIsolation(this.config, {
      repoRoot: this.repoRoot,
      controlRoot: path.resolve(this.config?.lifecycle?.control_root ?? this.repoRoot)
    });
    if (mode.mode === 'local-trial') {
      this.worktreeRoot = await canonicalDirectory(mode.root, 'local_trial_root');
    }
    const command = this.#command();
    if (command.length === 2) {
      try { await access(command[1]); }
      catch (error) { throw harnessError('MCP_UNAVAILABLE', `MCP server script is unavailable: ${error.message}`); }
    }
    const serverCwd = path.resolve(this.repoRoot, this.config.worker.cwd ?? '.');
    if (!under(this.repoRoot, serverCwd)) throw harnessError('WORKER_UNAVAILABLE', 'MCP server cwd must be inside the project root');
    const env = {
      ...subscriptionEnv(),
      WORKTREE_ROOT: this.worktreeRoot,
      ANTIGRAVITY_LOCAL_TRIAL: mode.mode === 'local-trial' ? 'true' : 'false',
      ANTIGRAVITY_MODEL: this.config.worker.model ?? '',
      ANTIGRAVITY_REPO_ROOT: this.repoRoot,
      ANTIGRAVITY_CONTROL_ROOT: path.resolve(this.config?.lifecycle?.control_root ?? this.repoRoot),
      ANTIGRAVITY_CLI: this.config.worker.cli ?? 'agy',
      ANTIGRAVITY_TIMEOUT_SECONDS: String(this.timeoutSeconds),
      ANTIGRAVITY_SKIP_PERMISSIONS: mode.mode === 'local-trial' ? 'false' : this.config.worker.skip_permissions === true ? 'true' : 'false'
    };
    this.client = new McpClient({
      command,
      cwd: serverCwd,
      env,
      timeoutMs: Math.max(1, this.timeoutSeconds) * 1000
    });
    try {
      const listing = await this.client.connect();
      this.toolNames = listing.tools.map(tool => tool?.name).filter(name => typeof name === 'string');
      const missing = REQUIRED_TOOLS.filter(name => !this.toolNames.includes(name));
      if (missing.length) throw harnessError('MCP_UNAVAILABLE', `MCP worker missing tools: ${missing.join(', ')}`);
    } catch (error) {
      const detail = this.client.summary().stderr;
      await this.client.close();
      this.client = null;
      if (['CONFIG_MISMATCH', 'WORKER_UNAVAILABLE', 'WORKER_TIMEOUT', 'MCP_PROTOCOL_ERROR'].includes(error?.harness?.code)) throw error;
      throw harnessError('MCP_UNAVAILABLE', `${error.message}${detail ? `; stderr: ${detail}` : ''}`);
    }
  }

  async #call(name, args) {
    await this.#connect();
    let result;
    try { result = await this.client.callTool(name, args); }
    catch (error) {
      if (error?.harness) throw error;
      const code = /timed out/i.test(error?.message ?? '') ? 'WORKER_TIMEOUT' : 'MCP_UNAVAILABLE';
      throw harnessError(code, `${name} failed: ${error.message}`);
    }
    if (!result || typeof result !== 'object') throw harnessError('MCP_PROTOCOL_ERROR', `MCP ${name} returned an invalid result`);
    return result;
  }

  async execute(task, prompt, operation) {
    const taskId = requireText(task?.task_id, 'task_id is required');
    await this.#validateTrialWorkspace(taskId);
    const result = await this.#call('antigravity_execute', { task_id: taskId, ...operationArgs(operation), prompt: requireText(prompt, 'prompt is required') });
    return this.#validateResult(result, taskId);
  }

  async continue(task, instruction, operation) {
    const taskId = requireText(task?.task_id, 'task_id is required');
    await this.#validateTrialWorkspace(taskId);
    const result = await this.#call('antigravity_continue', { task_id: taskId, ...operationArgs(operation), instruction: requireText(instruction, 'instruction is required') });
    return this.#validateResult(result, taskId);
  }

  async result(task) {
    const taskId = requireText(task?.task_id, 'task_id is required');
    await this.#validateTrialWorkspace(taskId);
    return this.#validateResult(await this.#call('antigravity_result', { task_id: taskId }), taskId);
  }

  async tools() {
    await this.#connect();
    return [...this.toolNames];
  }

  async health() {
    const tools = await this.tools();
    return { status: 'AVAILABLE', tools, server: this.config.worker.server, cwd: this.client?.cwd ?? null };
  }

  async #validateTrialWorkspace(taskId) {
    const mode = assertWorkerIsolation(this.config, {
      repoRoot: this.repoRoot,
      controlRoot: path.resolve(this.config?.lifecycle?.control_root ?? this.repoRoot)
    });
    if (mode.mode !== 'local-trial') return;
    if (!TASK_ID.test(taskId)) throw harnessError('INVALID_INPUT', 'invalid task_id');
    const root = await canonicalDirectory(mode.root, 'local_trial_root');
    if (!samePath(root, this.worktreeRoot)) throw harnessError('WORKTREE_ROOT_INVALID', 'worker root does not match local_trial_root');
    const candidate = path.resolve(root, taskId);
    if (!under(root, candidate) || path.basename(candidate).toLowerCase() !== taskId.toLowerCase()) {
      throw harnessError('WORKTREE_ESCAPE', 'task worktree escapes local_trial_root');
    }
    const canonicalCandidate = await canonicalDirectory(candidate, 'task worktree');
    if (!under(root, canonicalCandidate) || samePath(root, canonicalCandidate)) {
      throw harnessError('WORKTREE_ESCAPE', 'task worktree escapes local_trial_root');
    }
  }

  #validateResult(result, taskId) {
    if (result.task_id !== taskId) throw harnessError('MCP_PROTOCOL_ERROR', 'MCP result task_id mismatch');
    if (typeof result.workspace !== 'string' || !samePath(result.workspace, path.resolve(this.worktreeRoot, taskId))) {
      throw harnessError('WORKTREE_ESCAPE', 'MCP result workspace does not match the resolved task worktree');
    }
    if (this.config?.worker?.local_trial === true && result.status === 'SUCCEEDED') {
      const metadataError = trialMetadataError(result);
      if (metadataError) throw harnessError('MCP_PROTOCOL_ERROR', metadataError);
    }
    return {
      task_id: taskId,
      status: result.status ?? null,
      agent_status: result.agent_status ?? null,
      exit_code: result.exit_code ?? null,
      timed_out: result.timed_out === true,
      output_limited: result.output_limited === true,
      operation_id: result.operation_id ?? null,
      invocation_kind: result.invocation_kind ?? null,
      attempt: Number.isInteger(result.attempt) ? result.attempt : null,
      rework_count: Number.isInteger(result.rework_count) ? result.rework_count : null,
      conversation_id: result.conversation_id ?? null,
      usage: normalizeUsage(result.usage, 'mcp'),
      requested_model: typeof result.requested_model === 'string' ? result.requested_model : null,
      observed_model: typeof result.observed_model === 'string' ? result.observed_model : null,
      observed_agent: typeof result.observed_agent === 'string' ? result.observed_agent : null,
      permission_mode: typeof result.permission_mode === 'string' ? result.permission_mode : null,
      sandbox: typeof result.sandbox === 'boolean' ? result.sandbox : null,
      evidence_source: typeof result.evidence_source === 'string' ? result.evidence_source : null,
      evidence_truncated: result.evidence_truncated === true,
      stdout_events: boundedEvidence(result.stdout_events),
      command: Array.isArray(result.command) ? result.command.filter(value => typeof value === 'string') : [],
      started_at: result.started_at ?? null,
      finished_at: result.finished_at ?? null,
      final_summary: typeof result.final_summary === 'string' ? redactText(result.final_summary).slice(-MAX_RESULT_TEXT) : null,
      error: redactText(typeof result.error === 'string' ? result.error : JSON.stringify(result.error ?? '')).slice(-MAX_RESULT_TEXT),
      error_code: typeof result.error_code === 'string' ? result.error_code : null,
      stderr: redactText(String(result.stderr ?? '')).slice(-MAX_RESULT_TEXT),
      mcp_tools: [...this.toolNames]
    };
  }

  async close() {
    await this.client?.close();
    this.client = null;
    this.toolNames = [];
  }
}

async function canonicalDirectory(directory, label) {
  let info;
  try { info = await lstat(directory); }
  catch (error) { throw harnessError('WORKTREE_ROOT_INVALID', `${label} is unavailable: ${error.message}`); }
  if (!info.isDirectory() || info.isSymbolicLink()) throw harnessError('WORKTREE_ROOT_INVALID', `${label} must be a real directory`);
  let canonical;
  try { canonical = await realpath(directory); }
  catch (error) { throw harnessError('WORKTREE_ROOT_INVALID', `${label} cannot be resolved: ${error.message}`); }
  if (!samePath(canonical, directory)) throw harnessError('WORKTREE_ROOT_INVALID', `${label} must not be a junction or symlink`);
  return path.resolve(canonical);
}

export const ANTIGRAVITY_MCP_TOOLS = REQUIRED_TOOLS;
