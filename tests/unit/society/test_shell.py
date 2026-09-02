"""The agent's own shell: containment, tiers, timeout, cap — local by decision."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.society.agent_tools import ShellTool
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.shell import (
    OUTPUT_CAP_CHARS,
    ContainmentError,
    LocalBackend,
    ShellResult,
    resolve_contained,
)

CTX = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)


def test_containment(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sub").mkdir()
    assert resolve_contained(ws, None) == ws.resolve()
    assert resolve_contained(ws, "sub") == (ws / "sub").resolve()
    assert resolve_contained(ws, str(ws / "sub")) == (ws / "sub").resolve()
    with pytest.raises(ContainmentError):
        resolve_contained(ws, "..")
    with pytest.raises(ContainmentError):
        resolve_contained(ws, str(tmp_path))
    with pytest.raises(ContainmentError):
        resolve_contained(ws, "sub/../../")
    with pytest.raises(ContainmentError):
        resolve_contained(ws, "~")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_symlink_out_of_workspace_is_refused(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "escape").symlink_to(tmp_path)
    with pytest.raises(ContainmentError):
        resolve_contained(ws, "escape")


async def test_local_backend_runs_in_cwd_and_caps(tmp_path: Path):
    backend = LocalBackend()
    result = await backend.run("echo hello", cwd=tmp_path, timeout_s=30)
    assert result.ok and "hello" in result.output
    long = await backend.run("python -c \"print('x' * 70000)\"", cwd=tmp_path, timeout_s=60)
    assert long.ok and len(long.output) <= OUTPUT_CAP_CHARS + 100
    assert "characters cut" in long.output


async def test_local_backend_timeout(tmp_path: Path):
    backend = LocalBackend()
    result = await backend.run('python -c "import time; time.sleep(5)"', cwd=tmp_path, timeout_s=1)
    assert result.timed_out and not result.ok


class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, float]] = []

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ShellResult:
        self.calls.append((command, cwd, timeout_s))
        return ShellResult(output="ok", exit_code=0, seconds=0.01)


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout")
    try:
        yield runtime
    finally:
        await runtime.close()


async def test_tool_runs_inside_the_workspace_only(rt: SocietyRuntime, tmp_path: Path):
    ws = tmp_path / "ws"
    backend = FakeBackend()
    tool = ShellTool(rt, "scout", workspace=ws, backend=backend)
    res = await tool.execute({"command": "ls", "cwd": "notes"}, CTX)
    assert res.success, res.error
    assert backend.calls[0][1] == (ws / "notes").resolve()
    assert (ws / "notes").is_dir()
    assert res.output["backend"] == "fake"
    out = await tool.execute({"command": "ls", "cwd": ".."}, CTX)
    assert out.success is False and out.output["reason"] == "blocked_by_policy"
    assert len(backend.calls) == 1
    empty = await tool.execute({"command": "  "}, CTX)
    assert empty.success is False


async def test_tool_escalates_destructive_commands(rt: SocietyRuntime, tmp_path: Path):
    tool = ShellTool(rt, "scout", workspace=tmp_path / "ws", backend=FakeBackend())
    assert tool.risk_tier == "monitor"
    assert tool.risk_tier_for_args({"command": "rm -rf build"}) == "ask"
    assert tool.risk_tier_for_args({"command": "ls -la"}) is None
    assert tool.describe_args({"command": "rm -rf build"}) == {
        "command": "rm -rf build",
        "folder": str(tmp_path / "ws"),
    }


async def test_tool_respects_kill_switch_and_state(rt: SocietyRuntime, tmp_path: Path):
    backend = FakeBackend()
    tool = ShellTool(rt, "scout", workspace=tmp_path / "ws", backend=backend)
    await rt.store.set_kill_switch(True)
    assert (await tool.execute({"command": "ls"}, CTX)).output["reason"] == "kill_switch"
    await rt.store.set_kill_switch(False)
    await rt.roster.update("scout", {"state": "paused"})
    assert (await tool.execute({"command": "ls"}, CTX)).success is False
    assert backend.calls == []


def test_container_probe_is_false_by_decision():
    from jarvis.platform.probes import has_container

    assert has_container() is False


async def test_folder_tools_are_contained(rt: SocietyRuntime, tmp_path: Path):
    from jarvis.society.surface import society_tools

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "inside.txt").write_text("in", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("out", encoding="utf-8")
    cfg = SimpleNamespace(
        wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")),
        memory=SimpleNamespace(data_dir=str(tmp_path / "data")),
    )
    session = SimpleNamespace(session_id="society:scout", cwd=str(ws), permission_mode="ask")
    tools = society_tools(cfg, None, session)
    read = tools["Read"]
    ok = await read.execute({"file_path": "inside.txt"}, CTX)
    assert ok.success and "in" in str(ok.output)
    refused = await read.execute({"file_path": str(tmp_path / "outside.txt")}, CTX)
    assert refused.success is False and "leaves the agent workspace" in str(refused.error)
    assert "RunCommand" not in tools and "society_shell" in tools
