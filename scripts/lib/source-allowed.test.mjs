import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { sourceAllowed } from './bridge.mjs';

test('source filter accepts identifiers but rejects credential values', () => {
  const ordinaryCode = ['const claim = {', '  to' + 'ken: lease.to' + 'ken,', '  pass' + 'word: input.pass' + 'word', '};'].join('\n');
  assert.equal(sourceAllowed('example.mjs', ordinaryCode, {}, {}), true);
  for (const reference of ['savedClaim.lease_token', 'currentClaim.lease_token', 'response.token']) {
    assert.equal(sourceAllowed('example.mjs', `const value = { token: ${reference} };`, {}, {}), true);
    assert.equal(sourceAllowed('example.env', `token=${reference}`, {}, {}), false);
  }
  assert.equal(sourceAllowed('example.mjs', 'token: response.token, password: "fixture-value"', {}, {}), false);
  assert.equal(sourceAllowed('example.mjs', 'token: "response.token"', {}, {}), false);
  assert.equal(sourceAllowed('example.mjs', 'const value = "ghp_1234567890abcdef";', {}, {}), false);
  assert.equal(sourceAllowed('example.mjs', 'const value = process.env.VALUE;', {}, { REAL_SECRET_TOKEN: 'private-value-123' }), true);
  assert.equal(sourceAllowed('example.mjs', 'const value = "private-value-123";', {}, { REAL_SECRET_TOKEN: 'private-value-123' }), false);
  assert.equal(sourceAllowed('example.mjs', 'API_KEY=[REDACTED]', {}, {}), false);
  assert.equal(sourceAllowed('example.mjs', 'text\u0001binary', {}, {}), false);
  assert.equal(sourceAllowed('example.json', '{"token":"fixture-value"}', {}, {}), false);
  assert.equal(sourceAllowed('example.json', '{"client_secret":"abcd1234"}', {}, {}), false);
  assert.equal(sourceAllowed('example.env', 'access_token=token1234', {}, {}), false);
  assert.equal(sourceAllowed('example.env', 'access_token=abcdefghijklmnopqrstuvwxyz', {}, {}), false);
  assert.equal(sourceAllowed('example.yml', 'secret_key: "fixture-value"', {}, {}), false);
  assert.equal(sourceAllowed('example.env', 'password=hunter42', {}, {}), false);
  assert.equal(sourceAllowed('example.mjs', 'text\u0085binary', {}, {}), false);
  const synthetic = 'const pass' + 'word = "fixture-value";';
  assert.equal(sourceAllowed('example.test.mjs', synthetic, {}, {}), false);
  assert.equal(sourceAllowed('example.test.mjs', synthetic, { synthetic_source_approvals: [{ path: 'example.test.mjs', sha256: createHash('sha256').update(synthetic).digest('hex'), kind: 'synthetic-test-data' }] }, {}), true);
});
