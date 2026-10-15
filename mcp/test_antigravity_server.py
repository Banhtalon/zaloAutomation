from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters


MODULE_PATH = Path(__file__).with_name("antigravity_server.py")
SPEC = importlib.util.spec_from_file_location("antigravity_worker_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)

FAKE_OUTPUT = (
    '@echo off\r\n'
    'echo {"event":"init","conversation_id":"smoke-conversation","init":{"model":"fake-antigravity","agent":"fake-agent"}}\r\n'
    'echo {"event":"step_update","step_update":{"conversation_id":"smoke-conversation","step_type":"agent_response","text_delta":"smoke ok"}}\r\n'
    'echo {"event":"result","result":{"conversation_id":"smoke-conversation","status":"SUCCESS","response":"smoke ok","usage":{"input_tokens":12,"output_tokens":5,"total_tokens":17}}}\r\n'
)

TRIAL_MODEL = "gemini-3.8-flash-high"


def bounded_evidence_smoke() -> None:
    events_64 = [{"event": "step_update", "index": index} for index in range(64)]
    bounded_64, truncated_64 = SERVER._bounded_evidence(events_64)
    assert len(bounded_64) == 64
    assert truncated_64 is False

    events_65 = [{"event": "step_update", "index": index} for index in range(65)]
    bounded_65, truncated_65 = SERVER._bounded_evidence(events_65)
    assert len(bounded_65) == 64
    assert truncated_65 is True

    oversized, oversized_truncated = SERVER._bounded_evidence([{
        "event": "step_update",
        "text": "x" * (SERVER.MAX_EVIDENCE_BYTES + 1),
    }])
    assert oversized[0]["event"] == "evidence_summary"
    assert oversized_truncated is True


def _view_file_event(absolute_path: object) -> dict[str, object]:
    return {
        "event": "step_update",
        "step_update": {
            "step_type": "tool",
            "tool_name": "view_file",
            "tool_info": {"parameters": {"AbsolutePath": absolute_path}},
        },
    }


def nested_other_tool_output_is_ignored_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="antigravity-mcp-nested-tool-") as temp:
        root = Path(temp)
        workspace = root / "TASK-NESTED"
        workspace.mkdir()
        outside = root / "outside.txt"
        nested_output = {
            "event": "step_update",
            "step_update": {
                "step_type": "tool",
                "tool_name": "other_tool",
                "result": {"output": {"name": "view_file", "AbsolutePath": str(outside)}},
            },
        }
        assert SERVER._view_file_scope_violation([nested_output], workspace) is None


def worker_prompt_and_view_file_scope_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="antigravity-mcp-scope-") as temp:
        root = Path(temp)
        workspace = root / "TASK-SCOPE"
        workspace.mkdir()
        outside = root / "outside.txt"
        sibling = root / "TASK-SCOPE-sibling" / "file.txt"
        sibling.parent.mkdir()
        prompt = SERVER._worker_prompt("TASK-SCOPE", workspace, "inspect the fixture")
        assert str(workspace.resolve()) in prompt
        assert "do not guess" in prompt.lower()
        assert "missing" in prompt.lower()
        assert "controller, not you, runs gates and tests" in prompt.lower()

        cases = [
            ("workspace", str(workspace / "trial-data.txt"), False),
            ("sibling-prefix", str(sibling), True),
            ("parent-traversal", str(workspace / ".." / "outside.txt"), True),
            ("relative", "trial-data.txt", True),
            ("outside", str(outside), True),
            ("unc", r"\\server\share\trial-data.txt", True),
        ]
        if workspace.drive:
            cases.append(("drive-relative", f"{workspace.drive}trial-data.txt", True))
        for label, candidate, expected in cases:
            violation = SERVER._view_file_scope_violation([_view_file_event(candidate)], workspace)
            assert (violation is not None) is expected, label

        malformed_events = [
            {
                "event": "step_update",
                "step_update": {"step_type": "tool", "tool_name": "view_file", "tool_info": {"parameters": {}}},
            },
            {
                "event": "step_update",
                "step_update": {"step_type": "tool", "tool_name": "view_file", "tool_info": {"parameters": {"AbsolutePath": 42}}},
            },
        ]
        for event in malformed_events:
            assert SERVER._view_file_scope_violation([event], workspace) is not None

        junction = workspace / "linked"
        try:
            junction.symlink_to(root, target_is_directory=True)
        except OSError:
            pass
        else:
            violation = SERVER._view_file_scope_violation([_view_file_event(str(junction / "outside.txt"))], workspace)
            assert violation is not None


async def late_view_file_scope_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="antigravity-mcp-late-scope-") as temp:
        temp_path = Path(temp)
        repo = temp_path / "repo"
        root = temp_path / "trial-root"
        workspace = root / "TASK-LATE-SCOPE"
        workspace.mkdir(parents=True)
        repo.mkdir()
        fake_cli = temp_path / "fake-agy.cmd"
        events = [
            {"event": "init", "conversation_id": "late-scope-conversation", "init": {"model": TRIAL_MODEL}},
            *({"event": "step_update", "index": index} for index in range(64)),
            _view_file_event(str(temp_path / "outside.txt")),
            {"event": "result", "result": {"conversation_id": "late-scope-conversation", "status": "SUCCESS", "response": "fixture"}},
        ]
        fake_cli.write_text(
            "@echo off\r\n" + "\r\n".join(f"echo {json.dumps(event)}" for event in events) + "\r\n",
            encoding="utf-8",
        )
        old_env = os.environ.copy()
        os.environ.update({
            "WORKTREE_ROOT": str(root),
            "ANTIGRAVITY_CLI": str(fake_cli),
            "ANTIGRAVITY_TIMEOUT_SECONDS": "10",
            "ANTIGRAVITY_SKIP_PERMISSIONS": "false",
            "ANTIGRAVITY_FAKE_CLI_TEST_MODE": "false",
            "ANTIGRAVITY_LOCAL_TRIAL": "true",
            "ANTIGRAVITY_MODEL": TRIAL_MODEL,
            "ANTIGRAVITY_REPO_ROOT": str(repo),
            "ANTIGRAVITY_CONTROL_ROOT": str(repo),
        })
        SERVER._states.clear()
        try:
            result = await SERVER.antigravity_execute("TASK-LATE-SCOPE", "op-late-scope", "inspect fixture", "execute", 1, 0)
            assert result["status"] == "FAILED"
            assert result["error_code"] == "WORKER_SCOPE_VIOLATION"
            assert result["timed_out"] is False
            assert result["observed_model"] == TRIAL_MODEL
            assert result["conversation_id"] == "late-scope-conversation"
            assert result["evidence_truncated"] is True
            assert len(result["stdout_events"]) <= SERVER.MAX_EVIDENCE_EVENTS
        finally:
            os.environ.clear()
            os.environ.update(old_env)


def trial_metadata_validation_smoke() -> None:
    invalid = [
        ("wrong-model", None, None),
        (TRIAL_MODEL, "always-proceed", None),
        (TRIAL_MODEL, "request-review", False),
    ]
    for observed_model, permission_mode, sandbox in invalid:
        assert SERVER._trial_metadata_error(observed_model, permission_mode, sandbox)
    for observed_model, permission_mode, sandbox in [
        (TRIAL_MODEL, None, None),
        (TRIAL_MODEL, "request-review", None),
        (TRIAL_MODEL, "accept-edits", True),
    ]:
        assert SERVER._trial_metadata_error(observed_model, permission_mode, sandbox) is None


def trial_command_smoke() -> None:
    command = SERVER._command("agy", "safe prompt", "conversation-1", False, local_trial=True, model=TRIAL_MODEL, timeout=60)
    assert command == [
        "agy",
        "--model",
        TRIAL_MODEL,
        "--sandbox",
        "--mode",
        "accept-edits",
        "--output-format",
        "stream-json",
        "--print-timeout",
        "60s",
        "--print",
        "safe prompt",
        "--conversation",
        "conversation-1",
    ]
    assert "--dangerously-skip-permissions" not in command


def trial_root_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="antigravity-mcp-trial-root-") as temp:
        temp_path = Path(temp)
        repo = temp_path / "repo"
        trial_root = temp_path / "trial-root"
        workspace = trial_root / "TASK-ROOT"
        repo.mkdir()
        workspace.mkdir(parents=True)
        old_env = os.environ.copy()
        os.environ.update({
            "WORKTREE_ROOT": str(trial_root),
            "ANTIGRAVITY_LOCAL_TRIAL": "true",
            "ANTIGRAVITY_MODEL": TRIAL_MODEL,
            "ANTIGRAVITY_REPO_ROOT": str(repo),
            "ANTIGRAVITY_CONTROL_ROOT": str(repo),
        })
        try:
            assert SERVER.resolve_workspace("TASK-ROOT") == workspace.resolve()
            os.environ["WORKTREE_ROOT"] = str(repo / "inside")
            (repo / "inside" / "TASK-ROOT").mkdir(parents=True)
            try:
                SERVER.resolve_workspace("TASK-ROOT")
            except ValueError:
                pass
            else:
                raise AssertionError("trial root inside the repository was accepted")
        finally:
            os.environ.clear()
            os.environ.update(old_env)


async def trial_metadata_smoke() -> None:
    for missing_model in (False, True):
        with tempfile.TemporaryDirectory(prefix="antigravity-mcp-trial-") as temp:
            temp_path = Path(temp)
            repo = temp_path / "repo"
            root = temp_path / "trial-root"
            (root / "TASK-META").mkdir(parents=True)
            repo.mkdir()
            fake_cli = temp_path / "fake-agy.cmd"
            output = FAKE_OUTPUT.replace(
                '"model":"fake-antigravity"',
                '' if missing_model else '"model":"wrong-model"',
            )
            fake_cli.write_text(output, encoding="utf-8")
            old_env = os.environ.copy()
            os.environ.update({
                "WORKTREE_ROOT": str(root),
                "ANTIGRAVITY_CLI": str(fake_cli),
                "ANTIGRAVITY_TIMEOUT_SECONDS": "10",
                "ANTIGRAVITY_SKIP_PERMISSIONS": "false",
                "ANTIGRAVITY_FAKE_CLI_TEST_MODE": "false",
                "ANTIGRAVITY_LOCAL_TRIAL": "true",
                "ANTIGRAVITY_MODEL": TRIAL_MODEL,
                "ANTIGRAVITY_REPO_ROOT": str(repo),
                "ANTIGRAVITY_CONTROL_ROOT": str(repo),
            })
            SERVER._states.clear()
            try:
                result = await SERVER.antigravity_execute("TASK-META", "op-meta", "report metadata", "execute", 1, 0)
                assert result["status"] == "FAILED"
                assert result["error_code"] == "MCP_PROTOCOL_ERROR"
                assert result["requested_model"] == TRIAL_MODEL
            finally:
                os.environ.clear()
                os.environ.update(old_env)


async def metadata_unknown_does_not_block_valid_trial() -> None:
    with tempfile.TemporaryDirectory(prefix="antigravity-mcp-side-effect-") as temp:
        temp_path = Path(temp)
        repo = temp_path / "repo"
        root = temp_path / "trial-root"
        workspace = root / "TASK-SIDE-EFFECT"
        workspace.mkdir(parents=True)
        repo.mkdir()
        fake_cli = temp_path / "fake-agy.cmd"
        fake_cli.write_text(
            '@echo off\r\n'
            'echo changed>side-effect.txt\r\n'
            'echo {"event":"init","conversation_id":"side-effect-conversation","init":{"model":"gemini-3.8-flash-high","permission_mode":"request-review"}}\r\n'
            'echo {"event":"result","result":{"conversation_id":"side-effect-conversation","status":"SUCCESS","response":"fixture"}}\r\n',
            encoding="utf-8",
        )
        old_env = os.environ.copy()
        os.environ.update({
            "WORKTREE_ROOT": str(root),
            "ANTIGRAVITY_CLI": str(fake_cli),
            "ANTIGRAVITY_TIMEOUT_SECONDS": "10",
            "ANTIGRAVITY_SKIP_PERMISSIONS": "false",
            "ANTIGRAVITY_FAKE_CLI_TEST_MODE": "false",
            "ANTIGRAVITY_LOCAL_TRIAL": "true",
            "ANTIGRAVITY_MODEL": TRIAL_MODEL,
            "ANTIGRAVITY_REPO_ROOT": str(repo),
            "ANTIGRAVITY_CONTROL_ROOT": str(repo),
        })
        SERVER._states.clear()
        try:
            side_effect = workspace / "side-effect.txt"
            before = side_effect.read_bytes() if side_effect.exists() else None
            result = await SERVER.antigravity_execute("TASK-SIDE-EFFECT", "op-side-effect", "fixture", "execute", 1, 0)
            after = side_effect.read_bytes()
            assert result["status"] == "SUCCEEDED"
            assert result["permission_mode"] == "request-review"
            assert result["sandbox"] is None
            assert before != after
        finally:
            os.environ.clear()
            os.environ.update(old_env)


async def mode_conflict_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="antigravity-mcp-mode-conflict-") as temp:
        temp_path = Path(temp)
        root = temp_path / "trial-root"
        (root / "TASK-CONFLICT").mkdir(parents=True)
        fake_cli = temp_path / "fake-agy.cmd"
        fake_cli.write_text(FAKE_OUTPUT, encoding="utf-8")
        old_env = os.environ.copy()
        os.environ.update({
            "WORKTREE_ROOT": str(root),
            "ANTIGRAVITY_CLI": str(fake_cli),
            "ANTIGRAVITY_LOCAL_TRIAL": "true",
            "ANTIGRAVITY_MODEL": TRIAL_MODEL,
            "ANTIGRAVITY_FAKE_CLI_TEST_MODE": "true",
            "ANTIGRAVITY_REPO_ROOT": str(temp_path / "repo"),
            "ANTIGRAVITY_CONTROL_ROOT": str(temp_path / "repo"),
        })
        (temp_path / "repo").mkdir()
        SERVER._states.clear()
        try:
            result = await SERVER.antigravity_execute("TASK-CONFLICT", "op-conflict", "must stop", "execute", 1, 0)
            assert result["error_code"] == "CONFIG_MISMATCH"
        finally:
            os.environ.clear()
            os.environ.update(old_env)


async def smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="antigravity-mcp-") as temp:
        temp_path = Path(temp)
        root = temp_path / "worktrees"
        workspace = root / "TASK-SMOKE"
        workspace.mkdir(parents=True)
        fake_cli = temp_path / "fake-agy.cmd"
        fake_cli.write_text(FAKE_OUTPUT, encoding="utf-8")
        old_env = os.environ.copy()
        os.environ.update({"WORKTREE_ROOT": str(root), "ANTIGRAVITY_CLI": str(fake_cli), "ANTIGRAVITY_TIMEOUT_SECONDS": "10", "ANTIGRAVITY_SKIP_PERMISSIONS": "true", "ANTIGRAVITY_FAKE_CLI_TEST_MODE": "true"})
        SERVER._states.clear()
        try:
            first = await SERVER.antigravity_execute("TASK-SMOKE", "op-execute", "edit a test file", "execute", 1, 0)
            assert first["status"] == "SUCCEEDED"
            assert first["operation_id"] == "op-execute"
            assert first["usage"]["total_tokens"] == 17
            continued = await SERVER.antigravity_continue("TASK-SMOKE", "op-continue", "run the test again", "continue", 2, 1)
            assert continued["status"] == "SUCCEEDED"
            assert continued["operation_id"] == "op-continue"
            assert continued["conversation_id"] == "smoke-conversation"
            latest = await SERVER.antigravity_result("TASK-SMOKE")
            assert latest["operation_id"] == "op-continue"
            assert latest["invocation_kind"] == "continue"
            assert latest["attempt"] == 2
            assert latest["rework_count"] == 1
            SERVER._states.clear()
            after_restart = await SERVER.antigravity_result("TASK-SMOKE")
            assert after_restart["status"] == "NO_RESULT"
            assert after_restart["error_code"] == "NO_RESULT"
            try:
                SERVER.resolve_workspace("TASK-../escape")
            except ValueError:
                pass
            else:
                raise AssertionError("path traversal was not rejected")
        finally:
            os.environ.clear()
            os.environ.update(old_env)


async def stdio_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="antigravity-mcp-stdio-") as temp:
        temp_path = Path(temp)
        root = temp_path / "worktrees"
        (root / "TASK-STDIO").mkdir(parents=True)
        fake_cli = temp_path / "fake-agy.cmd"
        fake_cli.write_text(FAKE_OUTPUT, encoding="utf-8")
        env = {**os.environ, "WORKTREE_ROOT": str(root), "ANTIGRAVITY_CLI": str(fake_cli), "ANTIGRAVITY_TIMEOUT_SECONDS": "10", "ANTIGRAVITY_SKIP_PERMISSIONS": "true", "ANTIGRAVITY_FAKE_CLI_TEST_MODE": "true"}
        params = StdioServerParameters(command=sys.executable, args=[str(MODULE_PATH)], env=env)
        async with Client(params) as client:
            tools = await client.list_tools()
            assert sorted(tool.name for tool in tools.tools) == ["antigravity_continue", "antigravity_execute", "antigravity_result"]
            call = await client.call_tool("antigravity_execute", {"task_id": "TASK-STDIO", "operation_id": "op-stdio", "prompt": "run the safe smoke test", "invocation_kind": "execute", "attempt": 1, "rework_count": 0})
            assert call.is_error is False
            assert call.structured_content["operation_id"] == "op-stdio"


async def fail_closed_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="antigravity-mcp-fail-closed-") as temp:
        root = Path(temp) / "worktrees"
        (root / "TASK-REAL").mkdir(parents=True)
        old_env = os.environ.copy()
        os.environ.update({
            "WORKTREE_ROOT": str(root),
            "ANTIGRAVITY_CLI": "agy",
            "ANTIGRAVITY_WORKER_ISOLATED": "true",
            "ANTIGRAVITY_FAKE_CLI_TEST_MODE": "false",
        })
        SERVER._states.clear()
        try:
            result = await SERVER.antigravity_execute("TASK-REAL", "op-real", "must not launch", "execute", 1, 0)
            assert result["error_code"] == "WORKER_ISOLATION_UNAVAILABLE"
        finally:
            os.environ.clear()
            os.environ.update(old_env)


if __name__ == "__main__":
    bounded_evidence_smoke()
    nested_other_tool_output_is_ignored_smoke()
    worker_prompt_and_view_file_scope_smoke()
    trial_metadata_validation_smoke()
    trial_command_smoke()
    trial_root_smoke()
    asyncio.run(smoke())
    asyncio.run(stdio_smoke())
    asyncio.run(fail_closed_smoke())
    asyncio.run(trial_metadata_smoke())
    asyncio.run(late_view_file_scope_smoke())
    asyncio.run(metadata_unknown_does_not_block_valid_trial())
    asyncio.run(mode_conflict_smoke())
