import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { withTemporaryWritePermissions } from './temporary-permissions.mjs';

test('grants only scoped write rules during the callback and removes only rules it added', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'qq-temporary-permissions-'));
  const workspaceRoot = path.join(root, 'TASK-FAKE');
  const target = path.join(workspaceRoot, 'src', 'demo.txt');
  const settingsPath = path.join(root, 'settings.json');
  const existingRule = `write_file(${path.join(root, 'already-allowed.txt')})`;
  const originalSettings = {
    permissions: { allow: [existingRule], deny: ['shell(*)'] },
    marker: 'keep'
  };
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, 'before\n');
  await writeFile(settingsPath, JSON.stringify(originalSettings, null, 2) + '\n');
  const originalBytes = await readFile(settingsPath);
  t.after(() => rm(root, { recursive: true, force: true }));

  let callbackState;
  const result = await withTemporaryWritePermissions({
    settingsPath,
    workspaceRoot,
    files: [target]
  }, async ({ addedRules }) => {
    callbackState = JSON.parse(await readFile(settingsPath, 'utf8'));
    return addedRules;
  });

  const rule = `write_file(${path.resolve(target)})`;
  assert.deepEqual(result, [rule]);
  assert.deepEqual(callbackState.permissions.allow, [existingRule, rule]);
  assert.deepEqual(callbackState.permissions.deny, ['shell(*)']);
  assert.deepEqual(JSON.parse(await readFile(settingsPath, 'utf8')), originalSettings);
  assert.deepEqual(await readFile(settingsPath), originalBytes);
});

async function fixture(settings) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'qq-temporary-permissions-'));
  const workspaceRoot = path.join(root, 'TASK-FAKE');
  const target = path.join(workspaceRoot, 'demo.txt');
  const settingsPath = path.join(root, 'settings.json');
  await mkdir(workspaceRoot, { recursive: true });
  await writeFile(target, 'before\n');
  await writeFile(settingsPath, JSON.stringify(settings, null, 2) + '\n');
  return { root, workspaceRoot, target, settingsPath };
}

test('keeps a rule that existed before this callback', async t => {
  const f = await fixture({ permissions: { allow: [], deny: [] } });
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const rule = `write_file(${path.resolve(f.target)})`;
  await writeFile(f.settingsPath, JSON.stringify({ permissions: { allow: [rule], deny: [] } }, null, 2) + '\n');
  const before = await readFile(f.settingsPath);

  let called = false;
  const addedRules = await withTemporaryWritePermissions({
    settingsPath: f.settingsPath,
    workspaceRoot: f.workspaceRoot,
    files: [f.target]
  }, async context => {
    called = true;
    assert.deepEqual(context.addedRules, []);
    return context.addedRules;
  });

  assert.equal(called, true);
  assert.deepEqual(addedRules, []);
  assert.deepEqual(await readFile(f.settingsPath), before);
});

test('rejects missing, outside, parent-directory, relative and wildcard scopes', async t => {
  const f = await fixture({ permissions: { allow: [], deny: [] } });
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const cases = [
    { files: [], code: 'INVALID_INPUT' },
    { files: [path.join(f.root, 'outside.txt')], code: 'SCOPE_VIOLATION' },
    { files: [f.workspaceRoot], code: 'SCOPE_VIOLATION' },
    { files: ['demo.txt'], code: 'INVALID_INPUT' },
    { files: [path.join(f.workspaceRoot, '*.txt')], code: 'INVALID_INPUT' }
  ];
  for (const scenario of cases) {
    await assert.rejects(
      () => withTemporaryWritePermissions({ settingsPath: f.settingsPath, workspaceRoot: f.workspaceRoot, files: scenario.files }, async () => {}),
      error => error?.code === scenario.code
    );
  }
});

test('requires an explicit settings path', async t => {
  const f = await fixture({ permissions: { allow: [], deny: [] } });
  t.after(() => rm(f.root, { recursive: true, force: true }));
  await assert.rejects(
    () => withTemporaryWritePermissions({ workspaceRoot: f.workspaceRoot, files: [f.target] }, async () => {}),
    error => error?.code === 'INVALID_INPUT'
  );
});

test('reports an ambiguous recreated temporary rule without removing it', async t => {
  const f = await fixture({ permissions: { allow: [], deny: [] } });
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const rule = `write_file(${path.resolve(f.target)})`;

  await assert.rejects(
    () => withTemporaryWritePermissions({
      settingsPath: f.settingsPath,
      workspaceRoot: f.workspaceRoot,
      files: [f.target]
    }, async () => {
      const current = JSON.parse(await readFile(f.settingsPath, 'utf8'));
      current.marker = 'concurrent-change';
      current.permissions.allow = [rule];
      await writeFile(f.settingsPath, JSON.stringify(current, null, 2) + '\n');
    }),
    error => error?.code === 'CONTROL_STATE_MUTATED'
  );

  const final = JSON.parse(await readFile(f.settingsPath, 'utf8'));
  assert.equal(final.marker, 'concurrent-change');
  assert.deepEqual(final.permissions.allow, [rule]);
});

test('stops safely and preserves concurrent settings changes when temporary ownership is ambiguous', async t => {
  const existingRule = 'read_file(C:/fixture/existing.txt)';
  const concurrentRule = 'write_file(C:/fixture/concurrent.txt)';
  const f = await fixture({ permissions: { allow: [existingRule], deny: [] }, marker: 'original' });
  t.after(() => rm(f.root, { recursive: true, force: true }));

  const rule = `write_file(${path.resolve(f.target)})`;
  await assert.rejects(
    () => withTemporaryWritePermissions({
      settingsPath: f.settingsPath,
      workspaceRoot: f.workspaceRoot,
      files: [f.target]
    }, async () => {
      const current = JSON.parse(await readFile(f.settingsPath, 'utf8'));
      current.marker = 'concurrent-change';
      current.permissions.allow.push(concurrentRule);
      await writeFile(f.settingsPath, JSON.stringify(current, null, 2) + '\n');
    }),
    error => error?.code === 'CONTROL_STATE_MUTATED'
  );

  const final = JSON.parse(await readFile(f.settingsPath, 'utf8'));
  assert.equal(final.marker, 'concurrent-change');
  assert.deepEqual(final.permissions.allow, [existingRule, rule, concurrentRule]);
});

test('cleans up when the callback reports a timeout-like failure', async t => {
  const f = await fixture({ permissions: { allow: [], deny: [] } });
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const before = await readFile(f.settingsPath);

  await assert.rejects(
    () => withTemporaryWritePermissions({ settingsPath: f.settingsPath, workspaceRoot: f.workspaceRoot, files: [f.target] }, async () => {
      throw new Error('simulated timeout');
    }),
    /simulated timeout/
  );
  assert.deepEqual(await readFile(f.settingsPath), before);
});

test('cleans up when the callback is cancelled', async t => {
  const f = await fixture({ permissions: { allow: [], deny: [] } });
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const before = await readFile(f.settingsPath);

  const cancellation = Object.assign(new Error('simulated cancellation'), { name: 'AbortError' });
  await assert.rejects(
    () => withTemporaryWritePermissions({ settingsPath: f.settingsPath, workspaceRoot: f.workspaceRoot, files: [f.target] }, async () => {
      throw cancellation;
    }),
    error => error === cancellation
  );
  assert.deepEqual(await readFile(f.settingsPath), before);
});

test('does not overwrite malformed settings during grant or cleanup', async t => {
  const f = await fixture({ permissions: { allow: 'not-an-array' } });
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const before = await readFile(f.settingsPath);

  await assert.rejects(
    () => withTemporaryWritePermissions({ settingsPath: f.settingsPath, workspaceRoot: f.workspaceRoot, files: [f.target] }, async () => {}),
    error => error?.code === 'INVALID_CONFIG'
  );
  assert.deepEqual(await readFile(f.settingsPath), before);
});

test('reports cleanup as unsafe instead of overwriting a malformed concurrent change', async t => {
  const f = await fixture({ permissions: { allow: [], deny: [] } });
  t.after(() => rm(f.root, { recursive: true, force: true }));

  await assert.rejects(
    () => withTemporaryWritePermissions({ settingsPath: f.settingsPath, workspaceRoot: f.workspaceRoot, files: [f.target] }, async () => {
      await writeFile(f.settingsPath, '{"permissions":{"allow":42}}\n');
    }),
    error => error?.code === 'CONTROL_STATE_MUTATED'
  );
  assert.equal(await readFile(f.settingsPath, 'utf8'), '{"permissions":{"allow":42}}\n');
});
