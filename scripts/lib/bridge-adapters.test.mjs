import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  antigravityReviewerArgs,
  antigravityReviewerInput,
  assertSubscriptionSettings,
  codexSessionIdentity,
  invocation,
  parseProtocol,
  validateNoToolsAgentDefinition,
  validateReviewerBinding
} from './bridge-adapters.mjs';
import { execute } from './bridge-process.mjs';
import { isEligibleWorkerFallback, validateControlledConfig } from './controlled-bridge.mjs';

const hash = 'a'.repeat(64);
const binding = {
  provider: 'google',
  cli: 'antigravity',
  command: ['agy'],
  model: 'gemini-3.8-flash-high',
  agent: 'reviewer-no-tools',
  agent_definition_sha256: hash
};

const definition = `---
name: reviewer-no-tools
tools: []
excludeDefaultComponents: true
inheritCustomizations: false
mainAgent: true
subagent: false
model: inherit
commandExecutionPolicy: off
mcpServers: []
---
Review only and return JSON.
`;

test('Gemini reviewer definition must explicitly disable tools', () => {
  assert.equal(validateNoToolsAgentDefinition(definition, binding.agent), true);
  assert.equal(validateNoToolsAgentDefinition(definition.replace('tools: []', 'tools:\n  - view_file'), binding.agent), false);
  assert.equal(validateNoToolsAgentDefinition(`${definition}tools: [shell]\n`, binding.agent), true);
  assert.equal(validateNoToolsAgentDefinition(definition.replace('mcpServers: []\n---', 'mcpServers: []\nunknown: true\n---'), binding.agent), false);
  assert.equal(validateNoToolsAgentDefinition(definition.replace('tools: []', 'tools: []\ntools: [shell]'), binding.agent), false);
});

test('Gemini reviewer binding builds a guarded plan command', () => {
  validateReviewerBinding(binding);
  const prompt = 'x'.repeat(100_000);
  const args = antigravityReviewerArgs(binding, prompt, 90);
  assert.deepEqual(args.slice(0, 8), ['agy', '--agent', 'reviewer-no-tools', '--model', 'gemini-3.8-flash-high', '--sandbox', '--mode', 'plan']);
  assert.equal(args.includes('--input-format'), true);
  assert.equal(args.includes('--print'), false);
  assert.equal(args.includes(prompt), false);
  assert.equal(JSON.parse(antigravityReviewerInput(prompt)).message.content, prompt);
  assert.equal(args.includes('--dangerously-skip-permissions'), false);
});

test('Luna Max reviewer runs through Codex in read-only mode', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'qq-luna-reviewer-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const spec = await invocation({ provider: 'openai', cli: 'codex', command: ['codex'], model: 'gpt-5.6-luna', effort: 'max' },
    { cwd: root, packetDir: root, role: 'reviewer', prompt: 'review', timeoutSeconds: 30 });
  assert.equal(spec.argv.includes('gpt-5.6-luna'), true);
  assert.equal(spec.argv.includes('read-only'), true);
  assert.equal(spec.argv.includes('model_reasoning_effort="max"'), true);
});

test('Codex reviewer identity is read from its saved session', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'qq-codex-session-'));
  const day = path.join(root, '2026', '09', '22');
  const session = '01a0c786-b40d-7cf2-a17a-fc00e05fc449';
  await mkdir(day, { recursive: true });
  await writeFile(path.join(day, `rollout-${session}.jsonl`), [
    JSON.stringify({ type: 'session_meta', payload: { id: session } }),
    JSON.stringify({ type: 'turn_context', payload: { model: 'gpt-5.6-luna', effort: 'max' } })
  ].join('\n'));
  t.after(() => rm(root, { recursive: true, force: true }));
  assert.deepEqual(await codexSessionIdentity(session, root), { model: 'gpt-5.6-luna', effort: 'max' });
  assert.equal(await codexSessionIdentity('not-a-session', root), null);
});

test('large reviewer prompt spawns locally through stdin instead of argv', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'qq-reviewer-stdin-'));
  const cli = path.join(root, 'fake-reviewer.mjs');
  await writeFile(cli, "process.stdin.resume(); process.stdin.on('end', () => process.stdout.write('stdin-ok\\n'));\n");
  t.after(() => rm(root, { recursive: true, force: true }));
  const prompt = 'x'.repeat(200_000);
  const localBinding = { ...binding, command: ['node', cli] };
  const result = await execute(antigravityReviewerArgs(localBinding, prompt, 10), {
    cwd: root,
    input: antigravityReviewerInput(prompt),
    timeoutSeconds: 10
  });
  assert.equal(result.code, 0);
  assert.equal(result.stdout, 'stdin-ok\n');
});

test('Gemini reviewer requires the pinned agent digest', () => {
  assert.throws(() => validateReviewerBinding({ ...binding, agent_definition_sha256: undefined }), /agent_definition_sha256/);
  assert.throws(() => validateReviewerBinding({ ...binding, effort: 'xhigh' }), /does not accept an effort override/);
});

test('subscription settings honor the documented default-account contract', () => {
  assert.doesNotThrow(() => assertSubscriptionSettings({}));
  assert.doesNotThrow(() => assertSubscriptionSettings({ useG1Credits: false }));
  for (const value of [true, null, undefined, 'false', 0, {}, []]) {
    assert.throws(() => assertSubscriptionSettings({ useG1Credits: value }), /useG1Credits/);
  }
  for (const key of ['modelProvider', 'apiKey', 'apiKeyEnv', 'baseUrl', 'endpoint']) {
    assert.throws(() => assertSubscriptionSettings({ [key]: 'override' }), new RegExp(key));
  }
  assert.throws(() => assertSubscriptionSettings(null), /JSON object/);
  assert.throws(() => assertSubscriptionSettings([]), /JSON object/);
});

const reviewJson = JSON.stringify({ verdict: 'PASS', summary: 'ok', material_findings: [], risk_checks_completed: true });
const stream = (...events) => events.map(event => JSON.stringify(event)).join('\n');

test('Antigravity protocol accepts registry metadata but rejects tools, model and agent identity violations', () => {
  const init = { event: 'init', init: { model: binding.model, agent: binding.agent, registry57: { tools: ['view_file'] } } };
  const result = { event: 'result', result: { conversation_id: 'review-1', status: 'SUCCESS', response: reviewJson } };
  assert.equal(parseProtocol('google', stream(init, result), 'antigravity', { expectedModel: binding.model, expectedAgent: binding.agent }).result.verdict, 'PASS');
  assert.throws(() => parseProtocol('google', stream(init, { event: 'view_file' }, result), 'antigravity', { expectedModel: binding.model, expectedAgent: binding.agent }), /tool event/);
  assert.throws(() => parseProtocol('google', stream({ ...init, init: { ...init.init, model: 'other-model' } }, result), 'antigravity', { expectedModel: binding.model, expectedAgent: binding.agent }), /model/);
  assert.throws(() => parseProtocol('google', stream({ ...init, init: { ...init.init, agent: 'other-agent' } }, result), 'antigravity', { expectedModel: binding.model, expectedAgent: binding.agent }), /agent/);
});

test('fallback eligibility is limited to provider failures, never reviewer findings or guards', () => {
  assert.equal(isEligibleWorkerFallback({ status: 'BLOCKED_TECHNICAL', reason: 'INVALID_PROTOCOL' }), true);
  assert.equal(isEligibleWorkerFallback({ status: 'BLOCKED_TECHNICAL', reason: 'PROTOCOL_GUARD: tool event' }), false);
  assert.equal(isEligibleWorkerFallback({ code: 0, session_id: 's', result: { verdict: 'NEEDS_FIX', material_findings: ['x'] } }), false);
});

test('controlled config freezes a Terra High reviewer fallback alongside Gemini', () => {
  const config = {
    schema_version: 'qq.bridge.v2',
    billing: 'SUBSCRIPTION_ONLY',
    write_paths: ['src/example.mjs'],
    gate_paths: ['test'],
    worker: { provider: 'google', cli: 'antigravity', command: ['agy'], model: binding.model },
    reviewer: binding,
    fallback_reviewer: { provider: 'openai', cli: 'codex', command: ['codex'], model: 'gpt-5.6-terra', effort: 'high' },
    senior: { provider: 'openai', cli: 'codex', command: ['codex'], model: 'gpt-6-astra', effort: 'low' }
  };
  assert.doesNotThrow(() => validateControlledConfig(config, 'CONTROLLED_DELEGATION_V1'));
  assert.throws(() => validateControlledConfig({ ...config, fallback_reviewer: { ...config.fallback_reviewer, effort: 'xhigh' } }, 'CONTROLLED_DELEGATION_V1'), /fallback_reviewer effort/);
});
