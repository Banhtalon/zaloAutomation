import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp, readFile, writeFile, rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {readSingleFileProductEvidence} from './product-evidence.mjs';
import {validateControlledTask, freezeControlledTask, assertControlledContract, bridgeSourceHash} from './controlled-bridge.mjs';

test('one official product record, no legacy copy', async t => {
  const packet = await mkdtemp(path.join(os.tmpdir(), 'single-product-'));
  t.after(() => rm(packet, {recursive: true, force: true}));
  assert.equal(readSingleFileProductEvidence(packet).invalid, true);
  await writeFile(path.join(packet, 'product_check.json'), JSON.stringify({status: 'PASS'}));
  assert.deepEqual(readSingleFileProductEvidence(packet), {value: {status: 'PASS'}, invalid: false});
  await writeFile(path.join(packet, 'ui_evidence.json'), JSON.stringify({status: 'PASS'}));
  assert.equal(readSingleFileProductEvidence(packet).invalid, true);
});

test('single-file record must belong to the frozen task and current commit', async t => {
  const packet = await mkdtemp(path.join(os.tmpdir(), 'bound-product-'));
  t.after(() => rm(packet, {recursive: true, force: true}));
  const task = {task_id: 'TASK-ONE', revision: 1, candidate_head: 'a'.repeat(40), contract_sha256: 'b'.repeat(64)};
  const record = {task_id: task.task_id, revision: 1, head: task.candidate_head, contract_sha256: task.contract_sha256};
  const write = value => writeFile(path.join(packet, 'product_check.json'), JSON.stringify(value));
  await write(record);
  assert.equal(readSingleFileProductEvidence(packet, task).invalid, false);
  for (const altered of [{...record, task_id: 'TASK-OTHER'}, {...record, revision: 2}, {...record, head: 'c'.repeat(40)}, {...record, candidate_head: 'c'.repeat(40)}, {...record, contract_sha256: 'd'.repeat(64)}]) {
    await write(altered);
    assert.equal(readSingleFileProductEvidence(packet, task).invalid, true);
  }
});

test('bridge source hash covers the product evidence reader', async () => {
  const current = await bridgeSourceHash();
  assert.notEqual(await bridgeSourceHash({'product-evidence.mjs': 'tampered'}), current);
});

test('new task template freezes the single-file choice and rejects other values', async () => {
  const task = JSON.parse(await readFile(new URL('../../.ai-workflow/templates/task.json', import.meta.url), 'utf8'));
  task.base_sha = 'a'.repeat(40);
  assert.equal(task.execution.product_evidence_storage, 'single_file_v1');
  assert.doesNotThrow(() => validateControlledTask(task));
  task.execution.product_evidence_storage = 'unknown';
  assert.throws(() => validateControlledTask(task), /product_evidence_storage/);
});

test('storage choice cannot be removed after a task is frozen', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'frozen-product-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const taskPath = path.join(root, 'task.json');
  const task = JSON.parse(await readFile(new URL('../../.ai-workflow/templates/task.json', import.meta.url), 'utf8'));
  task.base_sha = 'a'.repeat(40);
  await freezeControlledTask(taskPath, task);
  const frozen = JSON.parse(await readFile(taskPath, 'utf8'));
  assert.ok(await assertControlledContract(taskPath, frozen));
  delete frozen.execution.product_evidence_storage;
  await assert.rejects(assertControlledContract(taskPath, frozen), /contract|mismatch/i);
});
