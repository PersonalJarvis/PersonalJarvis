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
    # Fake panes never draw an input line, so the cold-start gate would hold
    # each slot for its full ceiling. Its slot count follows the CPU count
    # (2 on a 4-core CI runner), which made a third pane miss the settle
    # window there. The gate has its own tests; here it must not pace.
    monkeypatch.setattr(ide, "COLD_START_SETTLE_S", 0.0)
    monkeypatch.setattr(ide, "COLD_START_HOLD_MAX_S", 0.0)


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


async def test_agents_carry_on_by_themselves_never_by_a_typed_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jarvis types nothing; Claude Code finishes an interrupted turn itself.

    Every resumed Claude pane is started with the CLI's own interrupted-turn
    resume in its environment (measured: a pane killed at step 26 of 30 went on
    to 30 with nothing typed; without it, it waited at its prompt).
    """
    ws, _ = _save(tmp_path, [_pane(1, working=True), _pane(2)])
    _store(ws)
    pool = FakeHostedPool()

    _registry, nudged = await _boot(monkeypatch, pool)
    await _settle(pool, 2)

    assert len(pool.spawns) == 2
    for spawn in pool.spawns:
        env = spawn["env"] or {}
        assert env.get("CLAUDE_CODE_RESUME_INTERRUPTED_TURN") == "1"
        assert env.get("CLAUDE_CODE_RESUME_INTERRUPTED_TURN_MAX_AGE_MS") == "0"
    assert nudged == []


async def test_an_agent_resumed_mid_turn_reads_working_while_it_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-run turn is the old job: movement counts as work, not as noise.

    Nothing is submitted in this lifetime, so without the resume proof both
    panes below read "waiting" forever — the Agents tab filed a pane rendering
    a film under Done (maintainer report 2026-09-29). An agent that was idle
    when it died gets no such proof: its redraws must not invent work.
    """
    from jarvis.agentic_ide.activity import read_activity

    ws, _ = _save(tmp_path, [_pane(1, working=True), _pane(2)])
    _store(ws)
    pool = FakeHostedPool()

    registry, _nudged = await _boot(monkeypatch, pool)
    await _settle(pool, 2)

    session = registry.sessions[0]
    interrupted, idle = session.find("T1"), session.find("T2")
    now = time.time()
    for term in (interrupted, idle):
        term.last_output_at = now
    assert read_activity(interrupted, now=now) == "working"
    assert read_activity(idle, now=now) == "waiting"


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


async def test_a_survivor_that_was_working_is_marked_for_the_bell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, _ = _save(tmp_path, [_pane(1, working=True), _pane(2)])
    _store(ws)
    pool = FakeHostedPool()
    pool.add_hosted("h-1", history_id="hist-1")
    pool.add_hosted("h-2", history_id="hist-2")

    registry, nudged = await _boot(monkeypatch, pool)

    session = registry.sessions[0]
    assert session.find("T1").worked_while_detached is True
    assert session.find("T2").worked_while_detached is False
    # Re-joined agents are never nudged: they never stopped.
    assert nudged == []


async def test_a_live_host_that_does_not_answer_is_never_taken_for_a_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2026-09-28 failure: agents started a second time beside living ones.

    A host that is running but silent holds agents. Boot must wait for it, not
    resume every pane in-process as if the machine had restarted.
    """
    ws, _ = _save(tmp_path, [_pane(1), _pane(2)])
    _store(ws)
    starts: list[bool] = []

    async def silent_host(*, start: bool) -> object:
        starts.append(start)
        raise ide.SessionNotReady("The terminal host is busy.")

    monkeypatch.setattr(ide, "_connect_pty_host", silent_host)
    registry = ide.Registry()
    await registry.boot_restore()
    await asyncio.sleep(0.05)

    assert all(t.status == "pending" for t in registry.sessions[0].terminals)
    assert starts and not any(starts), "a host is never STARTED beside a living one"
    assert registry._rejoin_task is not None
    registry._rejoin_task.cancel()


async def test_agents_lost_with_a_crashed_host_come_back_on_their_conversations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, _ = _save(tmp_path, [_pane(1)])
    _store(ws)
    first = FakeHostedPool()
    first.add_hosted("h-1", history_id="hist-1")
    second = FakeHostedPool()

    async def connect(*, start: bool) -> object:
        return second if start else first

    monkeypatch.setattr(ide, "_connect_pty_host", connect)
    registry = ide.Registry()
    await registry.boot_restore()
    term = registry.sessions[0].find("T1")
    assert term.pty_id == "h-1"

    # The host dies under the running app: every agent reports the unknown code.
    first.connected = False
    _on_output, on_closed = first._callbacks["h-1"]
    await on_closed("h-1", -1)
    for _ in range(200):
        if second.spawns:
            break
        await asyncio.sleep(0.02)

    argv = " ".join(second.spawns[0]["argv"]) if second.spawns else ""
    assert "--resume conv-1" in argv, argv
    assert (second.spawns[0]["env"] or {}).get("CLAUDE_CODE_RESUME_INTERRUPTED_TURN") == "1"
