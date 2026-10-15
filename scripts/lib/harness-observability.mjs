import { mkdir, open, readFile } from 'node:fs/promises';
import path from 'node:path';
import { redactText } from './redact.mjs';
import { harnessError } from './harness-errors.mjs';

function line(value) {
  return redactText(JSON.stringify(value)) + '\n';
}

async function append(file, value) {
  try {
    await mkdir(path.dirname(file), { recursive: true });
    const fd = await open(file, 'a');
    try {
      await fd.writeFile(line(value), 'utf8');
      await fd.sync();
    } finally {
      await fd.close();
    }
  } catch (error) {
    throw harnessError('AUDIT_WRITE_FAILED', `cannot append audit event: ${error.message}`);
  }
}

function baseEvent(event, fields = {}) {
  return {
    schema_version: 'qq.workflow.audit.v1',
    timestamp: new Date().toISOString(),
    event,
    task_id: fields.task_id ?? null,
    operation_id: fields.operation_id ?? null,
    state: fields.state ?? null,
    previous_state: fields.previous_state ?? null,
    next_state: fields.next_state ?? null,
    lease_owner: fields.lease_owner ?? null,
    attempt: Number.isInteger(fields.attempt) ? fields.attempt : null,
    rework_count: Number.isInteger(fields.rework_count) ? fields.rework_count : null,
    worker: fields.worker ?? null,
    conversation_id: fields.conversation_id ?? null,
    result: fields.result ?? null,
    duration_ms: Number.isFinite(fields.duration_ms) ? fields.duration_ms : null,
    error_code: fields.error_code ?? null,
    ...fields
  };
}

export async function auditEvent(packetDir, event, fields = {}) {
  await append(path.join(packetDir, 'audit.jsonl'), baseEvent(event, fields));
}

export async function operationEvent(packetDir, event, fields = {}) {
  await append(path.join(packetDir, 'operations.jsonl'), baseEvent(event, fields));
}

export async function auditSummary(packetDir) {
  const summary = { events: {}, operations: {}, invalid_lines: 0 };
  for (const [kind, fileName] of [['events', 'audit.jsonl'], ['operations', 'operations.jsonl']]) {
    let raw;
    try { raw = await readFile(path.join(packetDir, fileName), 'utf8'); }
    catch (error) { if (error.code === 'ENOENT') continue; throw error; }
    for (const rawLine of raw.split(/\r?\n/).filter(Boolean)) {
      try {
        const event = JSON.parse(rawLine);
        summary[kind][event.event] = (summary[kind][event.event] ?? 0) + 1;
      } catch { summary.invalid_lines += 1; }
    }
  }
  return summary;
}

export function transitionFields(state, patch = {}) {
  return {
    task_id: state.task_id,
    state: state.phase,
    previous_state: state.phase,
    next_state: patch.phase ?? state.phase,
    lease_owner: state.owner,
    attempt: state.attempt,
    rework_count: state.rework_count,
    worker: state.worker,
    conversation_id: state.conversation_id,
    ...patch
  };
}
