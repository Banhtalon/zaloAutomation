import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';

for (const command of ['pilot', 'run', 'quota-drill', 'activate']) {
  test(`legacy ${command} cannot start a new route`, () => {
    const result = spawnSync(process.execPath, ['scripts/bridge.mjs', command], { encoding: 'utf8' });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /LEGACY_NEW_DISPATCH_DISABLED/);
  });
}

test('legacy resume requires an existing packet before reading config or dispatching', () => {
  const packet = path.join(os.tmpdir(), `missing-legacy-${randomUUID()}`);
  const result = spawnSync(process.execPath, ['scripts/bridge.mjs', 'resume', 'missing-config', 'missing-task', 'missing-repo', packet], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /LEGACY_RESUME_REQUIRES_PACKET/);
  assert.equal(existsSync(packet), false);
});

test('legacy recover requires the claim owner and a reason before reading config', () => {
  const result = spawnSync(process.execPath, ['scripts/bridge.mjs', 'recover', 'missing-config', 'missing-task', 'missing-repo', 'missing-packet', 'reason-only'], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /RECOVERY_ARGUMENTS_REQUIRED/);
});
