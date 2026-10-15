import { createHash } from 'node:crypto';

export const ERROR_CODES = Object.freeze([
  'TASK_NOT_FOUND',
  'INVALID_TASK_STATE',
  'ALREADY_CLAIMED',
  'LEASE_EXPIRED',
  'LEASE_OWNER_MISMATCH',
  'DESIRED_STATE_BLOCKED',
  'WORKTREE_NOT_FOUND',
  'WORKTREE_ESCAPE',
  'WORKER_UNAVAILABLE',
  'MCP_UNAVAILABLE',
  'WORKER_EXECUTION_FAILED',
  'WORKER_TIMEOUT',
  'WORKER_OUTPUT_LIMIT',
  'WORKER_SCOPE_VIOLATION',
  'CONVERSATION_LOST',
  'REVIEW_FAILED',
  'RETRY_EXHAUSTED',
  'CHECKPOINT_REQUIRED',
  'CHECKPOINT_REJECTED',
  'RECOVERY_REQUIRED',
  'IDEMPOTENCY_CONFLICT',
  'STATE_CORRUPTION',
  'WORKTREE_DIRTY',
  'REVIEW_STALE',
  'EXECUTION_OUTCOME_UNKNOWN',
  'LEASE_MISSING',
  'LEASE_TOKEN_MISMATCH',
  'LEASE_GENERATION_MISMATCH',
  'CLAIM_INVALID',
  'INVALID_INPUT',
  'INVALID_CONFIG',
  'INVALID_TASK_ID',
  'INVALID_TRANSITION',
  'CHECKPOINT_NOT_READY',
  'COMPLETION_GUARD',
  'NOOP_REWORK',
  'SCOPE_VIOLATION',
  'CONTROL_STATE_MUTATED',
  'AUTO_COMMIT_DETECTED',
  'CONVERSATION_MISSING',
  'CONVERSATION_MISMATCH',
  'STATE_MISSING',
  'STATE_SCHEMA_MISMATCH',
  'STATE_MISMATCH',
  'CONFIG_MISMATCH',
  'WORKTREE_ROOT_INVALID',
  'WORKTREE_INVALID',
  'WORKTREE_BASE_INVALID',
  'WORKTREE_BASE_MISMATCH',
  'WORKTREE_REPOSITORY_MISMATCH',
  'CONTENT_MISMATCH',
  'MCP_PROTOCOL_ERROR',
  'MCP_RESULT_CORRELATION_MISMATCH',
  'WORKER_ISOLATION_UNAVAILABLE',
  'AUDIT_WRITE_FAILED',
  'CLAIM_BUSY',
  'CONTROL_PATH_INVALID',
  'PACKET_PATH_INVALID',
  'INTERNAL_ERROR'
]);

const RETRYABLE = new Set([
  'MCP_UNAVAILABLE',
  'WORKER_UNAVAILABLE',
  'WORKER_TIMEOUT',
  'WORKER_OUTPUT_LIMIT',
  'WORKER_EXECUTION_FAILED',
  'LEASE_EXPIRED',
  'DESIRED_STATE_BLOCKED',
  'REVIEW_FAILED',
  'RECOVERY_REQUIRED'
]);

export function harnessError(code, message, {
  taskId = null,
  operation = null,
  retryable = RETRYABLE.has(code),
  details = {}
} = {}) {
  const safeCode = ERROR_CODES.includes(code) ? code : 'INTERNAL_ERROR';
  const error = new Error(String(message || safeCode));
  error.name = 'HarnessError';
  error.code = safeCode;
  error.harness = {
    code: safeCode,
    message: error.message,
    task_id: taskId,
    operation,
    retryable: retryable === true,
    timestamp: new Date().toISOString()
  };
  Object.assign(error, details);
  return error;
}

export function errorRecord(error, { taskId = null, operation = null } = {}) {
  if (error?.harness) {
    return {
      ...error.harness,
      task_id: error.harness.task_id ?? taskId,
      operation: error.harness.operation ?? operation
    };
  }
  return {
    code: ERROR_CODES.includes(error?.code) ? error.code : 'INTERNAL_ERROR',
    message: String(error?.message ?? error ?? 'unknown error'),
    task_id: taskId,
    operation,
    retryable: RETRYABLE.has(error?.code),
    timestamp: new Date().toISOString()
  };
}

export function operationId(taskId, kind, attempt, rework, contractSha) {
  const seed = `${taskId}:${kind}:${attempt}:${rework}:${contractSha}`;
  return `op-${createHash('sha256').update(seed).digest('hex').slice(0, 32)}`;
}

export function isKnownErrorCode(code) {
  return ERROR_CODES.includes(code);
}
