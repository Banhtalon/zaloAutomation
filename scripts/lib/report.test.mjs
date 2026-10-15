import assert from 'node:assert/strict';
import test from 'node:test';
import { buildReport, formatReport } from './report.mjs';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { freezeControlledTask } from './controlled-bridge.mjs';

test('owner markdown summarizes technical source in Vietnamese', () => {
  const report = {
    audience: 'owner', observed_status: 'NEEDS_FIX', blockers: ['English internal failure detail'], next_step: 'Run an internal repair command',
    next_actor: 'Sol/Lead', local_product_actions: [], local_product_url: null, source_reference: 'packet', report_bytes: null,
    owner_summary: { technical_checks: 'Một kiểm tra chưa đạt.', independent_review: 'Đánh giá yêu cầu sửa.', product_check: 'Chưa kiểm tra sản phẩm.' },
    truncation: { applied: false, omitted_items: 0, source_reference: 'packet' }
  };
  const output = formatReport(report, { format: 'md' });
  assert.equal(output.includes('English internal failure detail'), false);
  assert.equal(output.includes('Run an internal repair command'), false);
  assert.match(output, /Có 1 trở ngại/);
  assert.match(output, /Sol\/Lead cần xử lý/);
});

test('Owner report distinguishes frozen non-applicability, review validity and missing ASSISTED receipts', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'owner-report-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const packet = path.join(root, 'packet'); await mkdir(packet);
  const taskPath = path.join(root, 'task.json');
  const task = JSON.parse(await readFile(new URL('../../.ai-workflow/templates/task.json', import.meta.url), 'utf8'));
  Object.assign(task, { base_sha: 'a'.repeat(40), candidate_head: 'b'.repeat(40), user_visible: false, product_check: { applicable: false, reason: 'internal fixture' } });
  const frozen = await freezeControlledTask(taskPath, task);
  const originalTask = await readFile(taskPath, 'utf8');
  const identity = { task_id: task.task_id, revision: 1, head: task.candidate_head, contract_sha256: frozen.contract_sha256 };
  const state = { ...identity, status: 'DONE', mode: 'ASSISTED', task_path: taskPath };
  const write = (name, value) => writeFile(path.join(packet, name), JSON.stringify(value));
  const review = { ...identity, verdict: 'PASS', independent: true, material_findings: [] };
  await write('state.json', state);
  await write('evidence.json', { ...identity, status: 'PASS', gates: [{ id: 'fixture', code: 0 }] });
  await write('review.json', review);
  let report = await buildReport(packet);
  assert.match(report.owner_summary.product_check, /Không áp dụng/);
  assert.equal(report.owner_summary.independent_review, 'Đánh giá độc lập đã đạt.');
  const markdown = formatReport(report);
  assert.match(markdown, /chưa xác nhận đã gộp/);
  assert.match(markdown, /ASSISTED: chưa có biên nhận/);
  assert.match(markdown, /Lead xác minh việc gộp/);
  assert.ok(Buffer.byteLength(markdown) <= 8192);
  const lead = await buildReport(packet, { audience: 'lead' });
  assert.equal(lead.invocations.count, null);
  assert.equal(lead.invocations.usage.total_tokens, null);
  await write('product_check.json', { ...identity, status: 'PASS', criteria_passed: true, checks: [{ action: 'fixture', observed: 'fixture', passed: true }] });
  await writeFile(path.join(packet, 'ui_evidence.json'), '{broken-json');
  const mixedProduct = await buildReport(packet);
  assert.match(mixedProduct.owner_summary.product_check, /Chưa xác minh/);
  assert.ok(mixedProduct.blockers.length > 0);
  assert.doesNotMatch(mixedProduct.next_step, /gộp/);
  await rm(path.join(packet, 'product_check.json'));
  await rm(path.join(packet, 'ui_evidence.json'));
  await write('state.json', { ...state, run_id: 'fixture-run' });
  await write('.receipts-chain.json', { schema_version: 'qq.workflow.receipt.v1', entries: [], last_receipt_sha256: null });
  assert.match((await buildReport(packet)).execution_note, /chưa có biên nhận/);
  await rm(path.join(packet, '.receipts-chain.json'));
  await write('state.json', state);

  for (const changed of [{ ...review, independent: false }, { ...review, head: 'c'.repeat(40) }, { ...review, head: 'c'.repeat(40), candidate_head: identity.head }, { ...review, material_findings: ['fixture finding'] }]) {
    await write('review.json', changed);
    assert.match((await buildReport(packet)).owner_summary.independent_review, /Chưa xác minh/);
  }
  await write('review.json', review);
  await writeFile(taskPath, JSON.stringify({ ...JSON.parse(originalTask), goal: 'changed after freeze' }));
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);
  await writeFile(taskPath, originalTask);
  await writeFile(taskPath, JSON.stringify({ ...JSON.parse(originalTask), schema_version: 'qq.workflow.task.v10' }));
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);
  await writeFile(taskPath, originalTask);
  await write('product_check.json', { ...identity, head: 'c'.repeat(40), status: 'PASS' });
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);
  await rm(path.join(packet, 'product_check.json'));
  await writeFile(path.join(packet, 'product_check.json'), '{broken-json');
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);
  await rm(path.join(packet, 'product_check.json'));
  assert.match((await buildReport(packet)).owner_summary.product_check, /Không áp dụng/);
  await writeFile(path.join(packet, 'ui_evidence.json'), '{broken-json');
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);
  await rm(path.join(packet, 'ui_evidence.json'));
  await write('state.json', { ...state, contract_sha256: 'd'.repeat(64) });
  report = await buildReport(packet);
  assert.match(report.owner_summary.product_check, /Chưa xác minh/);
  await write('state.json', state);
  await rm(taskPath);
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);

  await write('state.json', { ...state, error: 'token=sk-abcdefghijklmnop' });
  const redacted = formatReport(await buildReport(packet, { audience: 'lead' }), { format: 'json' });
  assert.doesNotMatch(redacted, /sk-abcdefghijklmnop/);
  const large = { ...report, blockers: Array(100).fill('x'.repeat(1000)) };
  assert.ok(Buffer.byteLength(formatReport(large)) <= 8192);
});

test('Owner Product Check is bound to the frozen product URL and all evidence records', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'owner-product-url-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const packet = path.join(root, 'packet'); await mkdir(packet);
  const taskPath = path.join(root, 'task.json');
  const task = JSON.parse(await readFile(new URL('../../.ai-workflow/templates/task.json', import.meta.url), 'utf8'));
  delete task.execution.product_evidence_storage; // Historical task: all old records must agree.
  Object.assign(task, { base_sha: 'a'.repeat(40), candidate_head: 'b'.repeat(40), user_visible: true,
    product_check: { target_url: 'http://localhost:3000', criteria: ['home'], actions: ['open'] } });
  const frozen = await freezeControlledTask(taskPath, task);
  const identity = { task_id: task.task_id, revision: task.revision, head: task.candidate_head, contract_sha256: frozen.contract_sha256 };
  const write = (name, value) => writeFile(path.join(packet, name), JSON.stringify(value));
  await write('state.json', { ...identity, status: 'DONE', mode: 'ASSISTED', task_path: taskPath });
  await write('evidence.json', { ...identity, status: 'PASS', gates: [{ id: 'fixture', code: 0 }] });
  await write('review.json', { ...identity, verdict: 'PASS', independent: true, material_findings: [] });
  const product = target_url => ({ ...identity, schema_version: 'qq.workflow.ui-evidence.v10', status: 'PASS', criteria_passed: true,
    result_schema_version: 'qq.workflow.product-check-result.v1', target_url,
    criterion_results: [{ criterion_id: 'criterion-001', status: 'PASS', observed_result: 'visible', evidence: 'synthetic' }],
    action_results: [{ action_id: 'action-001', status: 'PASS', observed_result: 'opened', evidence: 'synthetic' }],
    checks: [{ action: 'open', observed: 'opened', passed: true }] });
  await write('product_check.json', product('http://localhost:4000'));
  await write('ui_evidence.json', product('http://localhost:3000'));
  const mismatch = await buildReport(packet);
  assert.match(mismatch.owner_summary.product_check, /Chưa xác minh/);
  assert.ok(mismatch.blockers.length > 0);
  assert.doesNotMatch(mismatch.next_step, /gộp/);
  await write('product_check.json', product('http://localhost:3000'));
  const matching = await buildReport(packet);
  assert.match(matching.owner_summary.product_check, /đã đạt/);
  assert.equal(matching.blockers.length, 0);
  const frozenTask = await readFile(taskPath, 'utf8');
  const changedTask = JSON.parse(frozenTask);
  changedTask.product_check.target_url = 'http://localhost:4000';
  await writeFile(taskPath, JSON.stringify(changedTask));
  await write('product_check.json', product('http://localhost:4000'));
  await write('ui_evidence.json', product('http://localhost:4000'));
  const changedContract = await buildReport(packet);
  assert.match(changedContract.owner_summary.product_check, /Chưa xác minh/);
  assert.ok(changedContract.blockers.length > 0);
  await writeFile(taskPath, frozenTask);
  await write('product_check.json', product('http://localhost:3000'));
  await write('ui_evidence.json', product('http://localhost:3000'));
  await rm(taskPath);
  const missingTask = await buildReport(packet);
  assert.match(missingTask.owner_summary.product_check, /Chưa xác minh/);
  assert.ok(missingTask.blockers.length > 0);
  assert.doesNotMatch(missingTask.next_step, /gộp/);
});

test('new Owner report trusts only the single official product record', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'owner-single-product-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const packet = path.join(root, 'packet'); await mkdir(packet);
  const taskPath = path.join(root, 'task.json');
  const task = JSON.parse(await readFile(new URL('../../.ai-workflow/templates/task.json', import.meta.url), 'utf8'));
  Object.assign(task, {base_sha: 'a'.repeat(40), candidate_head: 'b'.repeat(40), product_check: {target_url: 'http://localhost:3000', criteria: ['home'], actions: ['open']}});
  const frozen = await freezeControlledTask(taskPath, task);
  const identity = {task_id: task.task_id, revision: 1, head: task.candidate_head, contract_sha256: frozen.contract_sha256};
  const write = (name, value) => writeFile(path.join(packet, name), JSON.stringify(value));
  await write('state.json', {...identity, status: 'DONE', mode: 'ASSISTED', task_path: taskPath});
  await write('evidence.json', {...identity, status: 'PASS', gates: [{id: 'fixture', code: 0}]});
  await write('review.json', {...identity, verdict: 'PASS', independent: true, material_findings: []});
  const product = {...identity, status: 'PASS', criteria_passed: true, target_url: 'http://localhost:3000', checks: [{action: 'open', observed: 'opened', passed: true}]};
  await write('product_check.json', product);
  assert.match((await buildReport(packet)).owner_summary.product_check, /đã đạt/);
  await write('product_check.json', {...product, target_url: 'http://localhost:4000'});
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);
  await write('product_check.json', product);
  await write('ui_evidence.json', product);
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);
  await rm(path.join(packet, 'ui_evidence.json'));
  await writeFile(taskPath, JSON.stringify({...JSON.parse(await readFile(taskPath, 'utf8')), ui_evidence: product}));
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);
  await writeFile(taskPath, JSON.stringify({...JSON.parse(await readFile(taskPath, 'utf8')), ui_evidence: undefined}));
  await rm(path.join(packet, 'product_check.json'));
  assert.match((await buildReport(packet)).owner_summary.product_check, /Chưa xác minh/);
});
