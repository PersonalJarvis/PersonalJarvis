"""Placing IDE panes on a connected computer, and bringing them back.

The registry must: stop the local agent, start it again THROUGH the
computer's pool with a server-side argv and folder, continue the same
conversation, remember the placement across a restart, and route every
keystroke/resize/close for that pane to the computer instead of this machine.
The code transfer itself is covered in ``test_remote_sync``; here it is a
recorded stand-in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import remote, resume_store
from jarvis.agentic_ide import session as ide
from jarvis.computers import remote_terminal
from tests.fakes.fake_pty_manager import FakePtyManager


class RemotePool(FakePtyManager):
    """A computer's pool: a FakePtyManager that also answers ``run``."""

    def __init__(self) -> None:
        super().__init__()
        self.commands: list[str] = []

    computer_id = "c_1"

    async def spawn(self, *args: Any, meta: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        spawned = await super().spawn(*args, **kwargs)
        self.spawns[-1]["meta"] = meta or {}
        return spawned

    async def run(self, command: str, *, timeout_s: float = 60.0) -> tuple[int, str, str]:
        self.commands.append(command)
        return 0, "", ""


@pytest.fixture
def pools(monkeypatch: pytest.MonkeyPatch) -> tuple[FakePtyManager, RemotePool, list[Any]]:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"C:/tools/{name}.exe",))
    monkeypatch.setattr(ide, "COLD_START_SETTLE_S", 0.0)
    local = FakePtyManager()
    far = RemotePool()
    moves: list[Any] = []
    monkeypatch.setattr(remote_terminal, "pool_for", lambda computer_id: far)

    async def push_code(_pool: object, folder: Path) -> remote.Placement:
        moves.append(("push", folder))
        return remote.Placement("/home/u/jarvis-workspaces/app-abc123", "f" * 40)

    async def push_conversation(
        _pool: object, agent: str, cid: str | None, cwd: str, home: object
    ) -> bool:
        moves.append(("chat-up", agent, cid, cwd))
        return cid is not None

    async def pull_code(_pool: object, folder: Path, rf: str, snap: str | None, name: str) -> Any:
        moves.append(("pull", rf, snap))
        return remote.Return(branch="jarvis/vps/1", applied=True, message="back")

    async def pull_conversation(
        _pool: object, agent: str, cid: str | None, cwd: Path, home: object
    ) -> bool:
        moves.append(("chat-down", cid))
        return True

    monkeypatch.setattr(remote, "push_code", push_code)
    monkeypatch.setattr(remote, "push_conversation", push_conversation)
    monkeypatch.setattr(remote, "pull_code", pull_code)
    monkeypatch.setattr(remote, "pull_conversation", pull_conversation)
    return local, far, moves


async def _sink(_text: str) -> None:
    return None


async def _gone(_code: int) -> None:
    return None


async def test_a_live_pane_moves_to_the_computer_and_continues_its_chat(
    pools: tuple[FakePtyManager, RemotePool, list[Any]], tmp_path: Path
) -> None:
    local, far, moves = pools
    registry = ide.Registry(pty_manager=local)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]
    await registry.attach(term.key, 100, 30, _sink, _gone, workspace_id=session.id)
    conversation = term.resume.id if term.resume else None
    local_id = term.pty_id

    result = await registry.place_terminal(term.key, workspace_id=session.id, computer_id="c_1")

    assert result["moved"] is True
    assert local_id in local.closed, "the local agent was stopped"
    assert term.computer_id == "c_1"
    spawn = far.spawns[-1]
    assert spawn["argv"][0] == "claude", "the server's own binary, not C:/tools"
    assert "--resume" in spawn["argv"] and conversation in spawn["argv"]
    assert spawn["cwd"] == "/home/u/jarvis-workspaces/app-abc123"
    assert spawn.get("meta", {}).get("history_id") == term.history_id
    assert ("chat-up", "claude", conversation, "/home/u/jarvis-workspaces/app-abc123") in moves

    # Input and resize for this pane now go to the computer.
    registry.write(term.key, "x", session.id)
    assert far.writes and far.writes[-1][1] == "x"


async def test_placement_survives_a_restart(
    pools: tuple[FakePtyManager, RemotePool, list[Any]], tmp_path: Path
) -> None:
    local, _far, _moves = pools
    registry = ide.Registry(pty_manager=local)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]
    await registry.place_terminal(term.key, workspace_id=session.id, computer_id="c_1")

    saved = term.to_snapshot()
    again = resume_store.SnapshotTerminal.from_dict(saved.to_dict())

    assert again is not None
    assert again.computer_id == "c_1"
    assert again.remote_folder == "/home/u/jarvis-workspaces/app-abc123"
    assert again.offload_snapshot == "f" * 40


async def test_a_workspace_moves_with_one_folder_transfer(
    pools: tuple[FakePtyManager, RemotePool, list[Any]], tmp_path: Path
) -> None:
    local, far, moves = pools
    registry = ide.Registry(pty_manager=local)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}])

    result = await registry.place_workspace(session.id, computer_id="c_1")

    assert len(result["moved"]) == 2
    assert [m for m in moves if m[0] == "push"] == [("push", Path(str(tmp_path)))]
    assert all(t.computer_id == "c_1" for t in session.terminals)
    assert far.spawns == [], "panes that were not running only change place"


async def test_bringing_back_kills_the_remote_agent_and_runs_here_again(
    pools: tuple[FakePtyManager, RemotePool, list[Any]], tmp_path: Path
) -> None:
    local, far, moves = pools
    registry = ide.Registry(pty_manager=local)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]
    await registry.attach(term.key, 100, 30, _sink, _gone, workspace_id=session.id)
    await registry.place_terminal(term.key, workspace_id=session.id, computer_id="c_1")
    remote_id = term.pty_id

    result = await registry.place_terminal(term.key, workspace_id=session.id, computer_id=None)

    assert result["message"] == "back"
    assert remote_id in far.closed
    assert term.computer_id == "" and term.remote_folder == ""
    assert local.spawns[-1]["argv"][0] == "C:/tools/claude.exe"
    assert ("pull", "/home/u/jarvis-workspaces/app-abc123", "f" * 40) in moves


def test_remote_argv_uses_the_command_name_only() -> None:
    argv = ide.remote_agent_argv("claude")
    assert argv is not None and argv[0] == "claude"
