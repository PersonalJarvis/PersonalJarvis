"""Placing IDE panes on a connected computer, and bringing them back.

The registry must: stop the local agent, start it again THROUGH the
computer's pool with a server-side argv and folder, continue the same
conversation, remember the placement across a restart, and route every
keystroke/resize/close for that pane to the computer instead of this machine.
The code transfer itself is covered in ``test_remote_sync``; here it is a
recorded stand-in.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import remote, resume_store
from jarvis.agentic_ide import session as ide
from jarvis.computers import remote_os, remote_terminal
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

    async def host(self) -> remote_os.RemoteHost:
        return remote_os.RemoteHost()


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


# -- creating panes directly on a computer -------------------------------------


def _pushes(moves: list[Any]) -> list[Any]:
    return [m for m in moves if m[0] == "push"]


async def test_a_workspace_opened_on_a_computer_needs_no_local_cli_and_copies_once(
    pools: tuple[FakePtyManager, RemotePool, list[Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, far, moves = pools
    monkeypatch.setattr(ide, "agent_argv", lambda name: None)  # nothing installed here
    registry = ide.Registry(pty_manager=local)

    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}], computer_id="c_1"
    )

    assert all(t.computer_id == "c_1" and t.remote_folder for t in session.terminals)
    assert _pushes(moves) == [("push", Path(str(tmp_path)))], "one copy for both panes"
    term = session.terminals[0]
    await registry.attach(term.key, 100, 30, _sink, _gone, workspace_id=session.id)
    assert local.spawns == [], "nothing starts on this machine"
    assert far.spawns[-1]["argv"][0] == "claude"
    assert far.spawns[-1]["cwd"] == "/home/u/jarvis-workspaces/app-abc123"


async def test_a_viewer_that_attaches_during_the_copy_is_told_to_wait(
    pools: tuple[FakePtyManager, RemotePool, list[Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, far, _moves = pools
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_push(_pool: object, folder: Path) -> remote.Placement:
        started.set()
        await release.wait()
        return remote.Placement("/srv/app", "a" * 40)

    monkeypatch.setattr(remote, "push_code", slow_push)
    registry = ide.Registry(pty_manager=local)
    opening = asyncio.create_task(
        registry.start(str(tmp_path), [{"agent": "claude"}], computer_id="c_1")
    )
    await started.wait()
    [session] = registry.sessions
    term = session.terminals[0]

    with pytest.raises(ide.SessionNotReady, match="Copying the folder"):
        await registry.attach(term.key, 100, 30, _sink, _gone, workspace_id=session.id)
    assert local.spawns == [] and far.spawns == []

    # A restart in the middle of the copy brings the pane back HERE: there is
    # no folder on the computer yet for it to start in.
    snapshot = registry.snapshot()
    assert snapshot is not None
    again = ide.Registry(pty_manager=FakePtyManager())
    await again.restore(snapshot)
    assert again.sessions[0].terminals[0].computer_id == ""

    release.set()
    await opening
    await registry.attach(term.key, 100, 30, _sink, _gone, workspace_id=session.id)
    assert far.spawns[-1]["cwd"] == "/srv/app"


async def test_new_panes_run_where_their_neighbours_run_unless_told(
    pools: tuple[FakePtyManager, RemotePool, list[Any]], tmp_path: Path
) -> None:
    local, _far, moves = pools
    registry = ide.Registry(pty_manager=local)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}], computer_id="c_1")

    split = await registry.add_terminal(workspace_id=session.id, anchor=session.terminals[0].name)
    appended = await registry.add_terminal(workspace_id=session.id)
    here = await registry.add_terminal(workspace_id=session.id, computer_id=None)

    assert split.computer_id == "c_1" and split.remote_folder
    assert appended.computer_id == "c_1"
    assert here.computer_id == "" and here.remote_folder == ""
    assert len(_pushes(moves)) == 1, "new panes join the copy already there"
    assert "copy already on" in split.notice

    # A workspace running here can still get one pane on the computer.
    plain = await registry.start(str(tmp_path), [{"agent": "claude"}])
    far_pane = await registry.add_terminal(workspace_id=plain.id, computer_id="c_1")
    assert far_pane.computer_id == "c_1"
    assert plain.terminals[0].computer_id == ""


async def test_a_second_placement_of_a_folder_joins_the_running_copy(
    pools: tuple[FakePtyManager, RemotePool, list[Any]], tmp_path: Path
) -> None:
    local, _far, moves = pools
    registry = ide.Registry(pty_manager=local)
    first = await registry.start(str(tmp_path), [{"agent": "claude"}], computer_id="c_1")
    second = await registry.start(str(tmp_path), [{"agent": "claude"}])

    await registry.place_workspace(second.id, computer_id="c_1")

    assert len(_pushes(moves)) == 1, "sending it again reset the server copy under the agent"
    assert second.terminals[0].remote_folder == first.terminals[0].remote_folder


async def test_a_server_without_the_cli_refuses_before_anything_is_copied(
    pools: tuple[FakePtyManager, RemotePool, list[Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, far, moves = pools

    async def run(command: str, *, timeout_s: float = 60.0) -> tuple[int, str, str]:
        far.commands.append(command)
        return 0, "missing:Claude Code\n", ""

    monkeypatch.setattr(far, "run", run)
    registry = ide.Registry(pty_manager=local)

    with pytest.raises(ide.PlacementError, match="not installed"):
        await registry.start(str(tmp_path), [{"agent": "claude"}], computer_id="c_1")
    assert registry.sessions == [], "the half-made workspace is closed again"
    assert _pushes(moves) == []

    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    with pytest.raises(ide.PlacementError):
        await registry.add_terminal(workspace_id=session.id, computer_id="c_1")
    assert len(session.terminals) == 1, "the pane that could never start is gone"


async def test_bringing_a_workspace_back_stops_every_agent_then_returns_each_folder_once(
    pools: tuple[FakePtyManager, RemotePool, list[Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, far, moves = pools
    closing = far.close

    def close(terminal_id: str) -> None:
        moves.append(("stop", terminal_id))
        closing(terminal_id)

    monkeypatch.setattr(far, "close", close)
    registry = ide.Registry(pty_manager=local)
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}], computer_id="c_1"
    )
    for term in session.terminals:
        await registry.attach(term.key, 100, 30, _sink, _gone, workspace_id=session.id)

    result = await registry.place_workspace(session.id, computer_id=None)

    pulls = [i for i, m in enumerate(moves) if m[0] == "pull"]
    stops = [i for i, m in enumerate(moves) if m[0] == "stop"]
    assert len(pulls) == 1, "one return for the shared folder, not one per pane"
    assert len(stops) == 2 and max(stops) < pulls[0], "both agents stopped before packing"
    assert all(t.computer_id == "" for t in session.terminals)
    assert len(local.spawns) == 2, "both run here again"
    assert result["messages"] == ["back"]


async def test_a_workspace_moving_on_leaves_panes_on_another_computer_alone(
    pools: tuple[FakePtyManager, RemotePool, list[Any]], tmp_path: Path
) -> None:
    local, _far, _moves = pools
    registry = ide.Registry(pty_manager=local)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}])
    first, second = session.terminals
    await registry.place_terminal(first.key, workspace_id=session.id, computer_id="c_1")

    result = await registry.place_workspace(session.id, computer_id="c_2")

    assert first.computer_id == "c_1"
    assert second.computer_id == "c_2"
    assert any("stays on" in m for m in result["messages"])
    with pytest.raises(ide.SessionError, match="Bring it back"):
        await registry.place_terminal(first.key, workspace_id=session.id, computer_id="c_2")


async def test_keep_working_on_quit_moves_running_workspaces(
    pools: tuple[FakePtyManager, RemotePool, list[Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.agentic_ide import offload_on_quit

    local, far, _moves = pools
    monkeypatch.setattr(offload_on_quit, "target", lambda: "c_1")
    registry = ide.Registry(pty_manager=local)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]
    await registry.attach(term.key, 100, 30, _sink, _gone, workspace_id=session.id)

    moved = await offload_on_quit.offload_before_quit(registry)

    assert moved == [session.id]
    assert term.computer_id == "c_1" and far.spawns, "the agent runs on the server now"


async def test_a_subfolder_of_the_same_repo_joins_the_running_copy(
    pools: tuple[FakePtyManager, RemotePool, list[Any]], tmp_path: Path
) -> None:
    import subprocess

    local, _far, moves = pools
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607, ASYNC221
    (tmp_path / "pkg").mkdir()
    registry = ide.Registry(pty_manager=local)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}], computer_id="c_1")

    inner = await registry.add_terminal(
        workspace_id=session.id, computer_id="c_1", folder=str(tmp_path / "pkg")
    )

    assert len(_pushes(moves)) == 1, "one repo, one copy — a second send reset it"
    assert inner.remote_folder == "/home/u/jarvis-workspaces/app-abc123/pkg"
