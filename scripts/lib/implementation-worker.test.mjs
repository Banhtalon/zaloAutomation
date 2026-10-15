import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { AntigravityMcpWorker, assertWorkerIsolation, trialMetadataError } from './implementation-worker.mjs';

const trialConfig = root => ({
  test_mode: false,
  worker: {
    local_trial: true,
    local_trial_root: root,
    model: 'gemini-3.8-flash-high'
  }
});

test('local_trial is explicit, model-bound, and separate from test_mode', () => {
  const root = path.join(os.tmpdir(), 'qq-antigravity-local-trial');
  assert.equal(assertWorkerIsolation(trialConfig(root)).mode, 'local-trial');
  assert.throws(() => assertWorkerIsolation({ ...trialConfig(root), test_mode: true }), error => error.code === 'CONFIG_MISMATCH');
  assert.throws(() => assertWorkerIsolation({ ...trialConfig(root), worker: { ...trialConfig(root).worker, model: 'wrong-model' } }), error => error.code === 'CONFIG_MISMATCH');
  assert.equal(assertWorkerIsolation({ test_mode: true, worker: {} }).mode, 'test-fixture');
  assert.throws(() => assertWorkerIsolation({ test_mode: false, worker: {} }), error => error.code === 'WORKER_ISOLATION_UNAVAILABLE');
});

test('local_trial accepts provider permission metadata without guessed sandbox schema', async t => {
  const repoRoot = path.resolve('.');
  const trialRoot = await mkdtemp(path.join(os.tmpdir(), 'qq-antigravity-worker-trial-'));
  const cliRoot = await mkdtemp(path.join(os.tmpdir(), 'qq-antigravity-worker-cli-'));
  const taskId = 'TASK-METADATA';
  const cli = path.join(cliRoot, 'fake-agy.cmd');
  await mkdir(path.join(trialRoot, taskId));
  await writeFile(cli, [
    '@echo off',
    `echo ${JSON.stringify({ event: 'init', conversation_id: 'trial-conversation', init: { model: 'gemini-3.8-flash-high', permission_mode: 'request-review' } })}`,
    `echo ${JSON.stringify({ event: 'tool_result', result: { token: 'sk-abcdefghijklmnop', status: 'fixture' } })}`,
    ...Array.from({ length: 65 }, (_, index) => `echo ${JSON.stringify({ event: 'step_update', index })}`),
    `echo ${JSON.stringify({ event: 'result', result: { conversation_id: 'trial-conversation', status: 'SUCCESS', response: 'fixture' } })}`
  ].join('\r\n') + '\r\n', 'utf8');
  const config = {
    schema_version: 'qq.bridge.v2',
    worker: {
      transport: 'mcp',
      server: 'antigravity_worker',
      provider: 'mcp',
      command: ['python', path.join(repoRoot, 'mcp', 'antigravity_server.py')],
      cli,
      local_trial: true,
      local_trial_root: trialRoot,
      model: 'gemini-3.8-flash-high'
    },
    lifecycle: { control_root: repoRoot }
  };
  const worker = new AntigravityMcpWorker({ repoRoot, worktreeRoot: trialRoot, config, timeoutSeconds: 10 });
  t.after(async () => { await worker.close(); await rm(trialRoot, { recursive: true, force: true }); await rm(cliRoot, { recursive: true, force: true }); });

  const result = await worker.execute({ task_id: taskId }, 'fixture', { id: 'op-metadata', kind: 'execute', attempt: 1, rework_count: 0 });
  assert.equal(result.requested_model, 'gemini-3.8-flash-high');
  assert.equal(result.observed_model, 'gemini-3.8-flash-high');
  assert.equal(result.permission_mode, 'request-review');
  assert.equal(result.sandbox, null);
  assert.equal(result.evidence_source, 'antigravity.stdout.stream-json');
  assert.equal(result.evidence_truncated, true);
  assert.ok(Array.isArray(result.stdout_events));
  const evidence = JSON.stringify(result.stdout_events);
  assert.match(evidence, /REDACTED/);
  assert.doesNotMatch(evidence, /sk-abcdefghijklmnop/);
  assert.deepEqual(result.command.slice(1, 10), [
    '--model', 'gemini-3.8-flash-high', '--sandbox', '--mode', 'accept-edits', '--output-format', 'stream-json', '--print-timeout', '10s'
  ]);
});

test('local_trial rejects explicit unsafe metadata contradictions', () => {
  const base = { requested_model: 'gemini-3.8-flash-high', observed_model: 'gemini-3.8-flash-high' };
  assert.equal(trialMetadataError({ ...base, permission_mode: null, sandbox: null }), null);
  assert.equal(trialMetadataError({ ...base, permission_mode: 'request-review', sandbox: null }), null);
  assert.match(trialMetadataError({ ...base, permission_mode: 'always-proceed', sandbox: null }) ?? '', /always-proceed/);
  assert.match(trialMetadataError({ ...base, permission_mode: 'request-review', sandbox: false }) ?? '', /sandbox/);
});

test('local_trial keeps the original failure when metadata is missing', async t => {
  const repoRoot = path.resolve('.');
  const trialRoot = await mkdtemp(path.join(os.tmpdir(), 'qq-antigravity-worker-failure-'));
  const cliRoot = await mkdtemp(path.join(os.tmpdir(), 'qq-antigravity-worker-cli-'));
  const taskId = 'TASK-FAILURE';
  const cli = path.join(cliRoot, 'fake-agy.cmd');
  await mkdir(path.join(trialRoot, taskId));
  await writeFile(cli, [
    '@echo off',
    'echo {"event":"init","conversation_id":"failed-conversation","init":{"permission_mode":"request-review","sandbox":true}}',
    'exit /b 7'
  ].join('\r\n') + '\r\n', 'utf8');
  const config = {
    schema_version: 'qq.bridge.v2',
    worker: {
      transport: 'mcp',
      server: 'antigravity_worker',
      provider: 'mcp',
      command: ['python', path.join(repoRoot, 'mcp', 'antigravity_server.py')],
      cli,
      local_trial: true,
      local_trial_root: trialRoot,
      model: 'gemini-3.8-flash-high'
    },
    lifecycle: { control_root: repoRoot }
  };
  const worker = new AntigravityMcpWorker({ repoRoot, worktreeRoot: trialRoot, config, timeoutSeconds: 10 });
  t.after(async () => { await worker.close(); await rm(trialRoot, { recursive: true, force: true }); await rm(cliRoot, { recursive: true, force: true }); });

  const result = await worker.execute({ task_id: taskId }, 'fixture', { id: 'op-failure', kind: 'execute', attempt: 1, rework_count: 0 });
  assert.equal(result.status, 'FAILED');
  assert.match(result.error, /missing terminal result event/);
  assert.equal(result.observed_model, null);
  assert.equal(result.sandbox, true);
});

test('local_trial returns late view_file scope violations without dropping bounded evidence', async t => {
  const repoRoot = path.resolve('.');
  const trialRoot = await mkdtemp(path.join(os.tmpdir(), 'qq-antigravity-worker-scope-'));
  const cliRoot = await mkdtemp(path.join(os.tmpdir(), 'qq-antigravity-worker-cli-'));
  const taskId = 'TASK-SCOPE';
  const cli = path.join(cliRoot, 'fake-agy.cmd');
  await mkdir(path.join(trialRoot, taskId));
  const events = [
    { event: 'init', conversation_id: 'scope-conversation', init: { model: 'gemini-3.8-flash-high' } },
    ...Array.from({ length: 64 }, (_, index) => ({ event: 'step_update', index })),
    { event: 'step_update', step_update: { step_type: 'tool', tool_name: 'view_file', tool_info: { parameters: { AbsolutePath: path.join(cliRoot, 'outside.txt') } } } },
    { event: 'result', result: { conversation_id: 'scope-conversation', status: 'SUCCESS', response: 'fixture' } }
  ];
  await writeFile(cli, ['@echo off', ...events.map(event => `echo ${JSON.stringify(event)}`)].join('\r\n') + '\r\n', 'utf8');
  const config = {
    schema_version: 'qq.bridge.v2',
    worker: {
      transport: 'mcp',
      server: 'antigravity_worker',
      provider: 'mcp',
      command: ['python', path.join(repoRoot, 'mcp', 'antigravity_server.py')],
      cli,
      local_trial: true,
      local_trial_root: trialRoot,
      model: 'gemini-3.8-flash-high'
    },
    lifecycle: { control_root: repoRoot }
  };
  const worker = new AntigravityMcpWorker({ repoRoot, worktreeRoot: trialRoot, config, timeoutSeconds: 10 });
  t.after(async () => { await worker.close(); await rm(trialRoot, { recursive: true, force: true }); await rm(cliRoot, { recursive: true, force: true }); });

  const result = await worker.execute({ task_id: taskId }, 'fixture', { id: 'op-scope', kind: 'execute', attempt: 1, rework_count: 0 });
  assert.equal(result.status, 'FAILED');
  assert.equal(result.error_code, 'WORKER_SCOPE_VIOLATION');
  assert.equal(result.timed_out, false);
  assert.equal(result.observed_model, 'gemini-3.8-flash-high');
  assert.equal(result.conversation_id, 'scope-conversation');
  assert.equal(result.evidence_truncated, true);
  assert.ok(result.stdout_events.length <= 64);
});
