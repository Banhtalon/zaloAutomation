from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer


logger = logging.getLogger("antigravity-worker")
mcp = MCPServer("antigravity-worker")

TASK_ID = re.compile(r"^TASK-[A-Z0-9_-]+$", re.IGNORECASE)
TRIAL_MODEL = "gemini-3.8-flash-high"
MAX_PROMPT_CHARS = 32_000
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_BYTES = 32 * 1024
MAX_EVIDENCE_EVENTS = 64


@dataclass
class _TaskState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    result: dict[str, Any] | None = None
    conversation_id: str | None = None
    operation_id: str | None = None
    invocation_kind: str | None = None
    attempt: int | None = None
    rework_count: int | None = None
    running: bool = False


_states: dict[str, _TaskState] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value: str) -> str:
    value = re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)\s*([:=])\s*([^\s,}\"']+)",
        r"\1\2<redacted>",
        value,
    )
    return re.sub(
        r"(?i)\b(?:sk-[A-Za-z0-9_-]{10,}|AIza[0-9A-Za-z_-]{20,}|ya29\.[A-Za-z0-9._-]{20,})\b",
        "<redacted>",
        value,
    )


def _operation_valid(operation_id: str, invocation_kind: str, attempt: int, rework_count: int) -> bool:
    return (
        isinstance(operation_id, str)
        and bool(operation_id.strip())
        and invocation_kind in {"execute", "continue"}
        and isinstance(attempt, int)
        and attempt >= 1
        and isinstance(rework_count, int)
        and rework_count >= 0
    )


def _result_base(
    task_id: str,
    workspace: Path | None,
    operation_id: str | None,
    invocation_kind: str | None,
    attempt: int | None,
    rework_count: int | None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "workspace": str(workspace) if workspace else None,
        "operation_id": operation_id,
        "invocation_kind": invocation_kind,
        "attempt": attempt,
        "rework_count": rework_count,
    }


def _empty_result(
    task_id: str,
    status: str,
    error: str | None = None,
    error_code: str | None = None,
    workspace: Path | None = None,
    operation_id: str | None = None,
    invocation_kind: str | None = None,
    attempt: int | None = None,
    rework_count: int | None = None,
) -> dict[str, Any]:
    timestamp = _now()
    result: dict[str, Any] = {
        **_result_base(task_id, workspace, operation_id, invocation_kind, attempt, rework_count),
        "status": status,
        "agent_status": None,
        "exit_code": None,
        "timed_out": False,
        "output_limited": False,
        "conversation_id": None,
        "requested_model": None,
        "observed_model": None,
        "observed_agent": None,
        "permission_mode": None,
        "sandbox": None,
        "evidence_source": "antigravity.stdout.stream-json",
        "evidence_truncated": False,
        "started_at": timestamp,
        "finished_at": timestamp,
        "command": [],
        "stdout": "",
        "stdout_events": [],
        "stderr": "",
        "final_summary": None,
        "error": _redact(error) if error else None,
    }
    if error_code:
        result["error_code"] = error_code
    return result


def _error(
    task_id: str,
    code: str,
    message: str,
    workspace: Path | None = None,
    operation_id: str | None = None,
    invocation_kind: str | None = None,
    attempt: int | None = None,
    rework_count: int | None = None,
) -> dict[str, Any]:
    return _empty_result(task_id, "FAILED", message, code, workspace, operation_id, invocation_kind, attempt, rework_count)


def _configured_timeout() -> float:
    raw = os.environ.get("ANTIGRAVITY_TIMEOUT_SECONDS", "300")
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError("ANTIGRAVITY_TIMEOUT_SECONDS must be a number") from exc
    if not 1 <= timeout <= 3600:
        raise ValueError("ANTIGRAVITY_TIMEOUT_SECONDS must be between 1 and 3600")
    return timeout


def _skip_permissions() -> bool:
    raw = os.environ.get("ANTIGRAVITY_SKIP_PERMISSIONS", "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"", "0", "false", "no", "off"}:
        return False
    raise ValueError("ANTIGRAVITY_SKIP_PERMISSIONS must be true or false")


def _local_trial_mode() -> bool:
    raw = os.environ.get("ANTIGRAVITY_LOCAL_TRIAL", "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"", "0", "false", "no", "off"}:
        return False
    raise ValueError("ANTIGRAVITY_LOCAL_TRIAL must be true or false")


def _trial_model() -> str:
    model = os.environ.get("ANTIGRAVITY_MODEL", "").strip()
    if model != TRIAL_MODEL:
        raise ValueError(f"ANTIGRAVITY_MODEL must be {TRIAL_MODEL}")
    return model


def _overlaps(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _trial_root(raw: str, name: str) -> Path:
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError(f"{name} must not be a symlink or junction")
    try:
        root = root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{name} does not exist") from exc
    if not root.is_dir():
        raise ValueError(f"{name} must be a directory")
    return root


def _test_fixture_mode() -> bool:
    """Allow only the local fake CLI used by unit tests.

    An environment flag is never accepted as proof for a real worker.  The
    production path stays fail-closed until a real OS provider is installed.
    """
    enabled = os.environ.get("ANTIGRAVITY_FAKE_CLI_TEST_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
    configured = os.environ.get("ANTIGRAVITY_CLI", "").strip().lower()
    return enabled and configured.endswith((".cmd", ".bat")) and Path(configured).name.startswith("fake-")


def resolve_workspace(task_id: str) -> Path:
    """Resolve a task worktree from WORKTREE_ROOT; callers cannot provide a path."""
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise ValueError("invalid task_id")
    configured_root = os.environ.get("WORKTREE_ROOT", "").strip()
    if not configured_root:
        raise ValueError("WORKTREE_ROOT is not configured")
    if _local_trial_mode():
        root = _trial_root(configured_root, "WORKTREE_ROOT")
        repo_raw = os.environ.get("ANTIGRAVITY_REPO_ROOT", "").strip()
        control_raw = os.environ.get("ANTIGRAVITY_CONTROL_ROOT", "").strip()
        if not repo_raw or not control_raw:
            raise ValueError("local trial repository and control roots are required")
        repo_root = _trial_root(repo_raw, "ANTIGRAVITY_REPO_ROOT")
        control_root = _trial_root(control_raw, "ANTIGRAVITY_CONTROL_ROOT")
        if _overlaps(root, repo_root) or _overlaps(root, control_root):
            raise ValueError("local trial root must be outside the repository and control roots")
    else:
        root = Path(configured_root).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        root = root.resolve(strict=False)
        if not root.is_dir():
            raise FileNotFoundError("WORKTREE_ROOT does not exist or is not a directory")
    candidate = root / task_id
    if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
        raise ValueError("task worktree cannot be a symlink or junction")
    workspace = candidate.resolve(strict=False)
    try:
        workspace.relative_to(root)
    except ValueError as exc:
        raise ValueError("task worktree escapes WORKTREE_ROOT") from exc
    if not workspace.is_dir():
        raise FileNotFoundError("task worktree does not exist")
    return workspace


def _antigravity_executable() -> str:
    configured = os.environ.get("ANTIGRAVITY_CLI", "agy").strip()
    if not configured or "\x00" in configured:
        raise ValueError("ANTIGRAVITY_CLI is invalid")
    executable = shutil.which(configured)
    if not executable:
        raise FileNotFoundError("Antigravity CLI was not found; install it or configure ANTIGRAVITY_CLI")
    return executable


def _iter_view_file_absolute_paths(value: Any):
    if not isinstance(value, dict) or value.get("event") != "step_update":
        return
    step = value.get("step_update")
    if not isinstance(step, dict) or step.get("step_type") != "tool" or step.get("tool_name") != "view_file":
        return
    tool_info = step.get("tool_info")
    parameters = tool_info.get("parameters") if isinstance(tool_info, dict) else None
    yield parameters.get("AbsolutePath") if isinstance(parameters, dict) else None


def _canonical_view_file_path(raw_path: Any, workspace: Path) -> Path | None:
    if not isinstance(raw_path, str):
        return None
    text = raw_path.strip()
    if not text or text.startswith(("\\\\", "//")) or any(part == ".." for part in re.split(r"[\\/]+", text)):
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        return None
    try:
        lexical = Path(os.path.abspath(text))
        canonical = candidate.resolve(strict=False)
        if os.path.normcase(str(lexical)) != os.path.normcase(str(canonical)):
            return None
        canonical.relative_to(workspace.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return None
    return canonical


def _view_file_scope_violation(events: list[Any], workspace: Path) -> str | None:
    for event in events:
        for raw_path in _iter_view_file_absolute_paths(event):
            if _canonical_view_file_path(raw_path, workspace) is None:
                return "view_file AbsolutePath is not a canonical path inside the resolved workspace"
    return None


def _worker_prompt(task_id: str, workspace: Path, request: str) -> str:
    resolved_workspace = workspace.resolve(strict=True)
    return f"""You are the implementation worker for task {task_id}.

Hard boundaries:
- The only workspace is this absolute path: {resolved_workspace}
- Use that exact absolute path for every file operation; do not guess, derive, or try another path.
- If a required file is missing there, report the missing file and stop.
- For view_file, AbsolutePath must be a canonical path inside that workspace.
- Use only file tools on files assigned in the implementation request. The controller, not you, runs gates and tests; do not use shell or run_command.
- Never modify ai-control.desired_state, workflow packets, leases, receipts, checkpoints, or Harness control state.
- Never claim or approve a task, resume the Harness, change routing, commit, reset, clean, merge, publish, or deploy.
- Leave changes in the worktree for Codex/the Harness to inspect.

Implementation request:
{request}

Report only actions and checks actually performed."""


def _command(
    executable: str,
    prompt: str,
    conversation_id: str | None,
    skip_permissions: bool,
    *,
    local_trial: bool = False,
    model: str | None = None,
    timeout: float | None = None,
) -> list[str]:
    command = [executable]
    if local_trial:
        if not model or timeout is None:
            raise ValueError("local trial command metadata is incomplete")
        command.extend(["--model", model, "--sandbox", "--mode", "accept-edits", "--output-format", "stream-json", "--print-timeout", f"{timeout:g}s"])
    elif skip_permissions:
        command.append("--dangerously-skip-permissions")
    command.extend(["--print", prompt])
    if conversation_id:
        command.extend(["--conversation", conversation_id])
    if not local_trial:
        command.extend(["--output-format", "stream-json"])
    return command


def _display_command(command: list[str]) -> list[str]:
    return ["<prompt>" if index > 0 and command[index - 1] in {"-p", "--prompt", "--print"} else value for index, value in enumerate(command)]


async def _read_limited(stream: asyncio.StreamReader, overflow: asyncio.Event) -> str:
    data = bytearray()
    total = 0
    while chunk := await stream.read(64 * 1024):
        total += len(chunk)
        if len(data) < MAX_OUTPUT_BYTES:
            data.extend(chunk[: MAX_OUTPUT_BYTES - len(data)])
        if total > MAX_OUTPUT_BYTES:
            overflow.set()
    return bytes(data).decode("utf-8", errors="replace")


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await killer.communicate()
        except OSError:
            pass
        if process.returncode is None:
            process.kill()
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            process.kill()


async def _run(command: list[str], workspace: Path, timeout: float) -> dict[str, Any]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workspace),
            env=os.environ.copy(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        return {"exit_code": None, "stdout": "", "stderr": "", "timed_out": False, "output_limited": False, "spawn_error": str(exc)}
    overflow = asyncio.Event()
    stdout_task = asyncio.create_task(_read_limited(process.stdout, overflow))
    stderr_task = asyncio.create_task(_read_limited(process.stderr, overflow))
    wait_task = asyncio.create_task(process.wait())
    overflow_task = asyncio.create_task(overflow.wait())
    timed_out = False
    output_limited = False
    done, _ = await asyncio.wait({wait_task, overflow_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    if not done:
        timed_out = True
        await _terminate(process)
    elif overflow.is_set():
        output_limited = True
        await _terminate(process)
    exit_code = await wait_task
    stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
    overflow_task.cancel()
    await asyncio.gather(overflow_task, return_exceptions=True)
    return {"exit_code": exit_code, "stdout": _redact(stdout), "stderr": _redact(stderr), "timed_out": timed_out, "output_limited": output_limited, "spawn_error": None}


def _events(stdout: str) -> tuple[list[Any], bool]:
    parsed: list[Any] = []
    parse_error = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            parsed.append({"event": "text", "text": line})
            parse_error = True
    return parsed, parse_error


def _conversation_id(events: list[Any]) -> str | None:
    for event in events:
        if isinstance(event, dict) and event.get("event") == "init" and isinstance(event.get("conversation_id"), str):
            return event["conversation_id"].strip() or None
    for event in reversed(events):
        if isinstance(event, dict) and event.get("event") == "result" and isinstance(event.get("result"), dict):
            value = event["result"].get("conversation_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _metadata(events: list[Any]) -> tuple[str | None, str | None, str | None, bool | None]:
    for event in events:
        if isinstance(event, dict) and event.get("event") == "init" and isinstance(event.get("init"), dict):
            init = event["init"]
            return (
                init.get("model") if isinstance(init.get("model"), str) else None,
                init.get("agent") if isinstance(init.get("agent"), str) else None,
                init.get("permission_mode") if isinstance(init.get("permission_mode"), str) else None,
                init.get("sandbox") if isinstance(init.get("sandbox"), bool) else None,
            )
    return None, None, None, None


def _trial_metadata_error(observed_model: str | None, permission_mode: str | None, sandbox: bool | None) -> str | None:
    if observed_model != TRIAL_MODEL:
        return f"Antigravity reported model {observed_model or '<missing>'}, expected {TRIAL_MODEL}"
    if isinstance(permission_mode, str) and permission_mode.strip().lower() == "always-proceed":
        return "Antigravity explicitly reported always-proceed permissions"
    if sandbox is False:
        return "Antigravity explicitly reported sandbox=false"
    return None


def _redact_nested(value: Any, depth: int = 0) -> Any:
    if depth >= 6:
        return "<evidence-depth-limit>"
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_redact_nested(item, depth + 1) for item in value[:MAX_EVIDENCE_EVENTS]]
    if isinstance(value, dict):
        return {
            _redact(str(key)): _redact_nested(item, depth + 1)
            for key, item in list(value.items())[:MAX_EVIDENCE_EVENTS]
        }
    return value


def _bounded_evidence(events: list[Any]) -> tuple[list[Any], bool]:
    safe_events = [_redact_nested(event) for event in events[:MAX_EVIDENCE_EVENTS]]
    encoded = json.dumps(safe_events, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= MAX_EVIDENCE_BYTES:
        return safe_events, len(events) > MAX_EVIDENCE_EVENTS
    event_types = [
        event.get("event")
        for event in events[:MAX_EVIDENCE_EVENTS]
        if isinstance(event, dict) and isinstance(event.get("event"), str)
    ]
    return [{
        "event": "evidence_summary",
        "source": "antigravity.stdout.stream-json",
        "event_count": len(events),
        "event_types": event_types[:MAX_EVIDENCE_EVENTS],
        "truncated": True,
    }], True


def _terminal_result(events: list[Any]) -> dict[str, Any] | None:
    for event in reversed(events):
        if isinstance(event, dict) and event.get("event") == "result" and isinstance(event.get("result"), dict):
            return event["result"]
    return None


def _final_summary(events: list[Any]) -> str | None:
    terminal = _terminal_result(events)
    if terminal:
        for key in ("response", "final_summary", "summary", "message"):
            if isinstance(terminal.get(key), str) and terminal[key].strip():
                return terminal[key].strip()
    parts = []
    for event in events:
        update = event.get("step_update") if isinstance(event, dict) and event.get("event") == "step_update" else None
        if isinstance(update, dict) and update.get("step_type") == "agent_response" and isinstance(update.get("text_delta"), str):
            parts.append(update["text_delta"])
    return "".join(parts).strip() or None


def _protocol_errors(events: list[Any], parse_error: bool, conversation_id: str | None, terminal: dict[str, Any] | None) -> list[str]:
    errors = []
    if parse_error:
        errors.append("invalid stream-json line")
    if not any(isinstance(event, dict) and event.get("event") == "init" for event in events):
        errors.append("missing init event")
    if terminal is None:
        errors.append("missing terminal result event")
    if not conversation_id:
        errors.append("missing conversation_id")
    if terminal is not None and not isinstance(terminal.get("status"), str):
        errors.append("missing terminal result status")
    return errors


async def _execute(task_id: str, operation_id: str, invocation_kind: str, attempt: int, rework_count: int, request: str, conversation_id: str | None) -> dict[str, Any]:
    workspace = resolve_workspace(task_id)
    local_trial = _local_trial_mode()
    fixture_mode = _test_fixture_mode()
    if local_trial and fixture_mode:
        return _error(task_id, "CONFIG_MISMATCH", "test_mode and local trial cannot be enabled together", workspace, operation_id, invocation_kind, attempt, rework_count)
    if not local_trial and not fixture_mode:
        return _error(task_id, "WORKER_ISOLATION_UNAVAILABLE", "Antigravity worker isolation is not verified", workspace, operation_id, invocation_kind, attempt, rework_count)
    try:
        skip_permissions = _skip_permissions()
        requested_model = _trial_model() if local_trial else None
        if local_trial and skip_permissions:
            return _error(task_id, "CONFIG_MISMATCH", "local trial refuses skip_permissions", workspace, operation_id, invocation_kind, attempt, rework_count)
    except ValueError as exc:
        return _error(task_id, "CONFIG_MISMATCH", str(exc), workspace, operation_id, invocation_kind, attempt, rework_count)
    executable = _antigravity_executable()
    timeout = _configured_timeout()
    command = _command(executable, _worker_prompt(task_id, workspace, request), conversation_id, skip_permissions, local_trial=local_trial, model=requested_model, timeout=timeout)
    logger.info("antigravity start task_id=%s operation_id=%s kind=%s cwd=%s resumed=%s timeout=%ss", task_id, operation_id, invocation_kind, workspace, bool(conversation_id), timeout)
    started_at = _now()
    raw = await _run(command, workspace, timeout)
    finished_at = _now()
    events, parse_error = _events(raw["stdout"])
    observed_conversation = _conversation_id(events) or conversation_id
    observed_model, observed_agent, permission_mode, sandbox = _metadata(events)
    scope_violation = _view_file_scope_violation(events, workspace)
    evidence_events, evidence_truncated = _bounded_evidence(events)
    terminal = _terminal_result(events)
    terminal_usage = None
    if terminal:
        candidate_usage = terminal.get("usage")
        if not isinstance(candidate_usage, dict):
            candidate_usage = terminal.get("stats")
        if isinstance(candidate_usage, dict):
            terminal_usage = candidate_usage
    agent_status = terminal.get("status") if terminal and isinstance(terminal.get("status"), str) else None
    protocol_errors = _protocol_errors(events, parse_error, observed_conversation, terminal)
    if scope_violation:
        status = "TIMED_OUT" if raw["timed_out"] else "OUTPUT_LIMIT" if raw["output_limited"] else "FAILED"
        error, error_code = scope_violation, "WORKER_SCOPE_VIOLATION"
    elif raw["timed_out"]:
        status, error, error_code = "TIMED_OUT", "Antigravity CLI timed out", "WORKER_TIMEOUT"
    elif raw["output_limited"]:
        status, error, error_code = "OUTPUT_LIMIT", "Antigravity CLI output exceeded the configured limit", "WORKER_OUTPUT_LIMIT"
    elif raw["spawn_error"] or raw["exit_code"] != 0 or protocol_errors:
        status, error, error_code = "FAILED", raw["spawn_error"] or "Antigravity stream-json protocol error: " + "; ".join(protocol_errors), "MCP_PROTOCOL_ERROR"
    elif local_trial and (metadata_error := _trial_metadata_error(observed_model, permission_mode, sandbox)):
        status, error, error_code = "FAILED", metadata_error, "MCP_PROTOCOL_ERROR"
    elif agent_status != "SUCCESS":
        status, error, error_code = "FAILED", f"Antigravity terminal status: {agent_status or 'missing'}", "WORKER_EXECUTION_FAILED"
    else:
        status, error, error_code = "SUCCEEDED", None, None
    result: dict[str, Any] = {
        **_result_base(task_id, workspace, operation_id, invocation_kind, attempt, rework_count),
        "status": status,
        "agent_status": agent_status,
        "exit_code": raw["exit_code"],
        "timed_out": raw["timed_out"],
        "output_limited": raw["output_limited"],
        "conversation_id": observed_conversation,
        "usage": terminal_usage,
        "requested_model": requested_model,
        "observed_model": observed_model,
        "observed_agent": observed_agent,
        "permission_mode": permission_mode,
        "sandbox": sandbox,
        "evidence_source": "antigravity.stdout.stream-json",
        "evidence_truncated": evidence_truncated,
        "started_at": started_at,
        "finished_at": finished_at,
        "command": _display_command(command),
        "stdout": raw["stdout"],
        "stdout_events": evidence_events,
        "stderr": raw["stderr"],
        "final_summary": _redact(_final_summary(events) or ""),
        "error": _redact(error) if error else None,
    }
    if error_code:
        result["error_code"] = error_code
    logger.info("antigravity finish task_id=%s operation_id=%s status=%s exit_code=%s conversation=%s events=%s", task_id, operation_id, status, raw["exit_code"], observed_conversation or "-", len(events))
    return result


def _state(task_id: str) -> _TaskState:
    return _states.setdefault(task_id, _TaskState())


def _validate_operation(task_id: str, operation_id: str, invocation_kind: str, attempt: int, rework_count: int, workspace: Path) -> dict[str, Any] | None:
    if not _operation_valid(operation_id, invocation_kind, attempt, rework_count):
        return _error(task_id, "MCP_RESULT_CORRELATION_MISMATCH", "operation correlation metadata is invalid", workspace, operation_id, invocation_kind, attempt, rework_count)
    return None


@mcp.tool()
async def antigravity_execute(task_id: str, operation_id: str, prompt: str, invocation_kind: str = "execute", attempt: int = 1, rework_count: int = 0) -> dict[str, Any]:
    """Run Antigravity in the task worktree for one durable operation."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        return _error(task_id, "INVALID_PROMPT", "prompt must be non-empty and at most 32000 characters")
    try:
        workspace = resolve_workspace(task_id)
        invalid = _validate_operation(task_id, operation_id, invocation_kind, attempt, rework_count, workspace)
        if invalid:
            return invalid
    except (OSError, ValueError) as exc:
        logger.warning("antigravity execute rejected task_id=%s reason=%s", task_id, type(exc).__name__)
        return _error(task_id, "WORKSPACE_RESOLUTION_FAILED", str(exc))
    state = _state(task_id)
    async with state.lock:
        if state.result is not None and state.operation_id == operation_id:
            return dict(state.result)
        state.running = True
        try:
            state.result = await _execute(task_id, operation_id, invocation_kind, attempt, rework_count, prompt, None)
            state.conversation_id = state.result.get("conversation_id")
            state.operation_id = operation_id
            state.invocation_kind = invocation_kind
            state.attempt = attempt
            state.rework_count = rework_count
            return state.result
        finally:
            state.running = False


@mcp.tool()
async def antigravity_continue(task_id: str, operation_id: str, instruction: str, invocation_kind: str = "continue", attempt: int = 1, rework_count: int = 0) -> dict[str, Any]:
    """Continue the prior conversation with a new operation identity."""
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > MAX_PROMPT_CHARS:
        return _error(task_id, "INVALID_INSTRUCTION", "instruction must be non-empty and at most 32000 characters")
    try:
        workspace = resolve_workspace(task_id)
        invalid = _validate_operation(task_id, operation_id, invocation_kind, attempt, rework_count, workspace)
        if invalid:
            return invalid
    except (OSError, ValueError) as exc:
        logger.warning("antigravity continue rejected task_id=%s reason=%s", task_id, type(exc).__name__)
        return _error(task_id, "WORKSPACE_RESOLUTION_FAILED", str(exc))
    state = _state(task_id)
    async with state.lock:
        if not state.conversation_id:
            return _error(task_id, "NO_CONVERSATION", "no Antigravity conversation exists for this task", workspace, operation_id, invocation_kind, attempt, rework_count)
        if state.operation_id == operation_id:
            return _error(task_id, "MCP_RESULT_CORRELATION_MISMATCH", "continue must use a new operation_id", workspace, operation_id, invocation_kind, attempt, rework_count)
        state.running = True
        try:
            state.result = await _execute(task_id, operation_id, invocation_kind, attempt, rework_count, instruction, state.conversation_id)
            state.conversation_id = state.result.get("conversation_id") or state.conversation_id
            state.operation_id = operation_id
            state.invocation_kind = invocation_kind
            state.attempt = attempt
            state.rework_count = rework_count
            return state.result
        finally:
            state.running = False


@mcp.tool()
async def antigravity_result(task_id: str) -> dict[str, Any]:
    """Return the latest result without launching a worker or accepting a path."""
    try:
        workspace = resolve_workspace(task_id)
    except (OSError, ValueError) as exc:
        logger.warning("antigravity result rejected task_id=%s reason=%s", task_id, type(exc).__name__)
        return _error(task_id, "WORKSPACE_RESOLUTION_FAILED", str(exc))
    state = _states.get(task_id)
    if state is not None and state.running:
        return _empty_result(task_id, "RUNNING", workspace=workspace, operation_id=state.operation_id, invocation_kind=state.invocation_kind, attempt=state.attempt, rework_count=state.rework_count) | {"conversation_id": state.conversation_id}
    if state is None or state.result is None:
        return _empty_result(task_id, "NO_RESULT", "no Antigravity result exists for this task", "NO_RESULT", workspace)
    return dict(state.result)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("ANTIGRAVITY_MCP_LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stderr)
    mcp.run(transport="stdio")
