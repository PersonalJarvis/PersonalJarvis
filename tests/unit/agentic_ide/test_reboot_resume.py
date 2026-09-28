"""RUB-102: after a power-off every running agent comes back on its own conversation.

Herdr's "native agent-session resume": the agents died with the machine, so
the app starts each one again with the CLI's resume argument, in every
workspace, without waiting for somebody to open it. Driven against the fake
persistent pool, so nothing real is started.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from tests.unit.agentic_ide.test_pty_host_adoption import ConnectRecorder, FakeHostedPool


@pytest.fixture(autouse=True)
def _no_real_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    # Every handle points at a conversation that exists, unless a test says not.
    monkeypatch.setattr(ide, "has_conversation", lambda agent, handle, home=None: True)


def _pane(index: int, *, running: bool = True, working: bool = False, resume: bool = True):
    return resume_store.SnapshotTerminal(
        key=f"t{index}",
        name=f"T{index}",
        agent="claude",
        history_id=f"hist-{index}",
        column=index,
        resume=ResumeHandle("claude_session", f"conv-{index}", 1.0) if resume else None,
        continuation_needed=working,
        running=running,
    )


def _save(folder: Path, panes: list[resume_store.SnapshotTerminal], ws_id: str = "ide_reboot01"):
    now = time.time()
    workspace = resume_store.SnapshotWorkspace(
        session_id=ws_id, folder=str(folder), terminals=panes, saved_at=now
    )
    return workspace, now


def _store(*workspaces: resume_store.SnapshotWorkspace) -> None:
    now = max(w.saved_at for w in workspaces)
    resume_store.save(
        resume_store.Snapshot(
            saved_at=now, workspaces=list(workspaces), active_session_id=workspaces[0].session_id
        )
    )


async def _boot(monkeypatch: pytest.MonkeyPatch, pool: FakeHostedPool):
    monkeypatch.setattr(ide, "_connect_pty_host", ConnectRecorder(pool))
    registry = ide.Registry()
    nudged: list[str] = []

    async def record_prompt(name: str, text: str, **_kwargs: object) -> None:
        nudged.append(name)

    monkeypatch.setattr(registry, "send_prompt", record_prompt)
    await registry.boot_restore()
    return registry, nudged


async def _settle(pool: FakeHostedPool, want: int) -> None:
    for _ in range(100):
        if len(pool.metas) >= want:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)


async def test_every_running_agent_is_resumed_in_every_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    ws_a, _ = _save(first, [_pane(1), _pane(2)], "ide_rebootA")
    ws_b, _ = _save(second, [_pane(3)], "ide_rebootB")
    _store(ws_a, ws_b)
    pool = FakeHostedPool()  # a fresh host: nothing survived the power-off

    registry, nudged = await _boot(monkeypatch, pool)
    await _settle(pool, 3)

    # All three came back, including the one in the workspace nobody opened ...
    assert sorted(m["history_id"] for m in pool.metas) == ["hist-1", "hist-2", "hist-3"]
    assert all(t.status == "live" and t.resumed for s in registry.sessions for t in s.terminals)
    # ... on their OWN conversations, not as fresh CLIs.
    argvs = [" ".join(spawn["argv"]) for spawn in pool.spawns]
    for index in (1, 2, 3):
        assert any(f"--resume conv-{index}" in argv for argv in argvs), argvs
    # Idle agents are resumed but never typed into.
    assert nudged == []


async def test_working_agents_are_also_told_to_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, _ = _save(tmp_path, [_pane(1, working=True), _pane(2)])
    _store(ws)
    monkeypatch.setattr(
        ide,
        "_mark_restored_continuations",
        lambda terms: [
            setattr(t, "continuation_pending", t.resume_continuation_needed) for t in terms
        ],
    )
    pool = FakeHostedPool()

    registry, nudged = await _boot(monkeypatch, pool)
    await _settle(pool, 2)
    for _ in range(100):
        if nudged:
            break
        await asyncio.sleep(0.02)

    assert len(pool.metas) == 2
    assert nudged == ["T1"]


async def test_agents_that_ended_by_themselves_stay_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, _ = _save(tmp_path, [_pane(1, running=False), _pane(2)])
    _store(ws)
    pool = FakeHostedPool()

    registry, _nudged = await _boot(monkeypatch, pool)
    await _settle(pool, 1)

    assert [m["history_id"] for m in pool.metas] == ["hist-2"]
    assert registry.sessions[0].find("T1").status == "pending"


async def test_panes_without_a_conversation_are_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, _ = _save(tmp_path, [_pane(1, resume=False), _pane(2)])
    _store(ws)
    monkeypatch.setattr(
        ide, "has_conversation", lambda agent, handle, home=None: handle.id != "conv-2"
    )
    pool = FakeHostedPool()

    await _boot(monkeypatch, pool)
    await _settle(pool, 1)

    assert pool.metas == []


async def test_survivors_are_rejoined_and_only_the_rest_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, _ = _save(tmp_path, [_pane(1), _pane(2)])
    _store(ws)
    pool = FakeHostedPool()
    pool.add_hosted("h-1", history_id="hist-1")  # the app closed; this agent kept running

    registry, _nudged = await _boot(monkeypatch, pool)
    await _settle(pool, 1)

    assert pool.adopted == ["h-1"]
    assert [m["history_id"] for m in pool.metas] == ["hist-2"]
    session = registry.sessions[0]
    assert session.find("T1").reattached is False and session.find("T1").pty_id == "h-1"


def test_snapshot_records_whether_an_agent_was_running() -> None:
    term = ide.Terminal(key="t1", name="T1", agent="claude", display_name="Claude", index=0)
    term.status = "live"
    assert term.to_snapshot().running is True
    term.status, term.exit_code = "exited", 0  # /exit
    assert term.to_snapshot().running is False
    term.exit_code = -1  # the host went away with the machine
    assert term.to_snapshot().running is True
    term.status, term.was_running = "pending", True
    assert term.to_snapshot().running is True
    # Older snapshots carry no field: every pane counts as running.
    parsed = resume_store.SnapshotTerminal.from_dict({"name": "T1", "agent": "claude"})
    assert parsed is not None and parsed.running is True


def test_a_damaged_snapshot_is_kept_aside_instead_of_overwritten() -> None:
    path = resume_store._store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"workspaces": [', encoding="utf-8")

    assert resume_store.load() is None

    assert not path.exists()
    kept = list(path.parent.glob(f"{path.stem}.damaged-*{path.suffix}"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == '{"workspaces": ['
