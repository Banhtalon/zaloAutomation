import { spawn } from 'node:child_process';
import { StringDecoder } from 'node:string_decoder';
import { redactText } from './redact.mjs';
import { harnessError } from './harness-errors.mjs';

const MAX_STDERR_BYTES = 256 * 1024;

function jsonLine(value) {
  return JSON.stringify(value) + '\n';
}

/**
 * Minimal MCP stdio client for the Harness.
 *
 * It deliberately implements only the MCP operations needed by the worker
 * route: initialize, tools/list and tools/call. The worker process remains
 * alive so an in-memory Antigravity conversation can be continued safely.
 */
export class McpClient {
  constructor({ command, cwd, env, timeoutMs = 300_000 } = {}) {
    if (!Array.isArray(command) || command.length === 0) throw new Error('MCP command is required');
    this.command = command;
    this.cwd = cwd;
    this.env = env;
    this.timeoutMs = timeoutMs;
    this.child = null;
    this.buffer = '';
    this.stdoutDecoder = new StringDecoder('utf8');
    this.stderrDecoder = new StringDecoder('utf8');
    this.nextId = 1;
    this.pending = new Map();
    this.stderr = '';
    this.started = false;
    this.closed = false;
    this.tools = [];
  }

  async connect() {
    if (this.started && !this.closed) return { tools: this.tools };
    this.child = spawn(this.command[0], this.command.slice(1), {
      cwd: this.cwd,
      env: this.env,
      shell: false,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    });
    this.started = true;
    this.closed = false;
    this.child.stdout.on('data', chunk => this.#onStdout(chunk));
    this.child.stderr.on('data', chunk => {
      const text = this.stderrDecoder.write(chunk);
      const next = this.stderr + text;
      this.stderr = next.slice(-MAX_STDERR_BYTES);
    });
    this.child.on('error', error => this.#failPending(harnessError('MCP_UNAVAILABLE', `MCP process error: ${error.message}`)));
    this.child.on('close', code => {
      this.closed = true;
      this.#failPending(harnessError('MCP_UNAVAILABLE', `MCP process exited with code ${code ?? 127}`));
    });

    await this.request('initialize', {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'qq-harness', version: '1.0.0' }
    });
    this.notify('notifications/initialized', {});
    const listing = await this.request('tools/list', {});
    this.tools = Array.isArray(listing?.tools) ? listing.tools : [];
    return { tools: this.tools };
  }

  notify(method, params) {
    if (!this.child || this.closed) throw harnessError('MCP_UNAVAILABLE', 'MCP client is not running');
    this.child.stdin.write(jsonLine({ jsonrpc: '2.0', method, params }));
  }

  request(method, params) {
    if (!this.child || this.closed) return Promise.reject(harnessError('MCP_UNAVAILABLE', 'MCP client is not running'));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(harnessError('WORKER_TIMEOUT', `MCP request timed out: ${method}`));
        void this.close().catch(() => {});
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.child.stdin.write(jsonLine({ jsonrpc: '2.0', id, method, params }));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  async callTool(name, args) {
    const response = await this.request('tools/call', { name, arguments: args });
    if (response?.isError) throw harnessError('MCP_PROTOCOL_ERROR', `MCP tool failed: ${name}`);
    const structured = response?.structuredContent;
    if (structured && typeof structured === 'object') return structured;
    const text = Array.isArray(response?.content)
      ? response.content.find(item => item?.type === 'text')?.text
      : null;
    if (typeof text !== 'string') throw harnessError('MCP_PROTOCOL_ERROR', `MCP tool returned no structured result: ${name}`);
    try {
      return JSON.parse(text);
    } catch {
      throw harnessError('MCP_PROTOCOL_ERROR', `MCP tool returned invalid JSON: ${name}`);
    }
  }

  async close() {
    if (!this.child || this.closed) return;
    this.closed = true;
    this.#failPending(harnessError('MCP_UNAVAILABLE', 'MCP client closed'));
    try { this.child.stdin.end(); } catch {}
    if (this.child.exitCode == null) {
      if (process.platform === 'win32') {
        try {
          const killer = spawn('taskkill', ['/PID', String(this.child.pid), '/T', '/F'], {
            shell: false,
            windowsHide: true,
            stdio: 'ignore'
          });
          await new Promise(resolve => killer.on('close', resolve));
        } catch {}
      } else {
        try { this.child.kill('SIGTERM'); } catch {}
      }
    }
  }

  #onStdout(chunk) {
    this.buffer += this.stdoutDecoder.write(chunk);
    let newline;
    while ((newline = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (!line) continue;
      let message;
      try { message = JSON.parse(line); } catch { continue; }
      if (message?.id === undefined || message?.id === null) continue;
      const pending = this.pending.get(message.id);
      if (!pending) continue;
      clearTimeout(pending.timer);
      this.pending.delete(message.id);
      if (message.error) pending.reject(harnessError('MCP_PROTOCOL_ERROR', `MCP ${message.error.code ?? 'error'}: ${message.error.message ?? 'request failed'}`));
      else pending.resolve(message.result);
    }
  }

  #failPending(error) {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
      this.pending.delete(id);
    }
  }

  summary() {
    return { command: this.command, cwd: this.cwd, stderr: redactText(this.stderr) };
  }
}
