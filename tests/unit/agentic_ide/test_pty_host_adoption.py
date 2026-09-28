"""The registry's side of the PTY host: adopting agents that outlived the app.

Driven against a fake persistent pool (``FakeHostedPool``) that behaves like
``RemotePtyManager`` from the registry's point of view — ``persistent``,
``connected``, ``hosted()``, ``adopt()``, ``kill_hosted()`` — on top of the
ordinary fake PTY pool, so no host process and no pseudo-terminal is needed.
The resume store is redirected to a temporary file by this package's conftest.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as ide
from jarvis.terminal.pty_host_client import AdoptResult, HostedInfo
from tests.fakes.fake_pty_manager import FakePtyManager, FakePtySession


@dataclass
class FakeHostedPool(FakePtyManager):
    """A fake PTY pool that also holds terminals from before the app started."""

    persistent: bool = True
    connected: bool = True
    hosted_terminals: dict[str, HostedInfo] = field(default_factory=dict)
    replays: dict[str, str] = field(default_factory=dict)
    adopted: list[str] = field(default_factory=list)
    killed: list[str] = field(default_factory=list)
    metas: list[dict[str, Any]] = field(default_factory=list)
    appearances: list[tuple[str, str]] = field(default_factory=list)

    def add_hosted(
        self, terminal_id: str, *, history_id: str, replay: str = "", started_at: float = 1.0
    ) -> None:
        self.hosted_terminals[terminal_id] = HostedInfo(
            terminal_id=terminal_id,
            pid=500,
            meta={"history_id": history_id, "name": terminal_id},
            cols=100,
            rows=40,
            started_at=started_at,
        )
        self.replays[terminal_id] = replay

    def hosted(self) -> list[HostedInfo]:
        return list(self.hosted_terminals.values())

    async def adopt(self, terminal_id: str, on_output: Any, on_closed: Any) -> AdoptResult:
        self.hosted_terminals.pop(terminal_id, None)
        self.adopted.append(terminal_id)
        self._live.add(terminal_id)
        self._callbacks[terminal_id] = (on_output, on_closed)
        return AdoptResult(alive=True, replay=self.replays.get(terminal_id, ""), cols=100, rows=40)

    def kill_hosted(self, terminal_id: str) -> bool:
        self.hosted_terminals.pop(terminal_id, None)
        self.killed.append(terminal_id)
        return True

    def set_appearance(self, terminal_id: str, appearance: str) -> None:
        self.appearances.append((terminal_id, appearance))

    async def spawn(  # type: ignore[override]
        self,
        shell_argv: tuple[str, ...],
        shell_id: str,
        cwd: str | None,
        cols: int,
        rows: int,
        on_output: Any,
        on_closed: Any,
        env: Any = None,
        on_probe: Any = None,
        meta: dict[str, Any] | None = None,
    ) -> FakePtySession:
        self.metas.append(dict(meta or {}))
        return await FakePtyManager.spawn(
            self, shell_argv, shell_id, cwd, cols, rows, on_output, on_closed, env, on_probe
        )


class ConnectRecorder:
    """Replaces ``session._connect_pty_host``: hands out a pool, remembers calls."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.calls: list[bool] = []

    async def __call__(self, *, start: bool) -> Any:
        self.calls.append(start)
        return self.pool


@pytest.fixture(autouse=True)
def _no_real_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


def _save_snapshot(folder: Path, histories: list[str], saved_at: float) -> None:
    workspace = resume_store.SnapshotWorkspace(
        session_id="ide_restoreme01",
        folder=str(folder),
        terminals=[
            resume_store.SnapshotTerminal(
                key=f"t{index + 1}",
                name=f"T{index + 1}",
                agent="claude",
                history_id=history,
                column=index,
            )
            for index, history in enumerate(histories)
        ],
        saved_at=saved_at,
    )
    resume_store.save(
        resume_store.Snapshot(
            saved_at=saved_at, workspaces=[workspace], active_session_id=workspace.session_id
        )
    )


# ------------------------------------------------------------- _adopt_hosted
async def test_adopt_hosted_rejoins_matching_panes_with_their_screen(tmp_path: Path) -> None:
    pool = FakeHostedPool()
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(
        str(tmp_path), [{"agent": "claude", "name": "T1"}, {"agent": "claude", "name": "T2"}]
    )
    t1, t2 = session.find("T1"), session.find("T2")
    # Two hosted terminals claim T1's history: the newer one wins.
    pool.add_hosted("old", history_id=t1.history_id, replay="stale", started_at=1.0)
    pool.add_hosted("new", history_id=t1.history_id, replay="hello from before", started_at=2.0)
    pool.add_hosted("unrelated", history_id="someone-else", replay="x")

    await registry._adopt_hosted(session)  # noqa: SLF001 - the unit under test

    assert pool.adopted == ["new"]
    assert t1.status == "live"
    assert t1.pty_id == "new"
    assert (t1.pty_cols, t1.pty_rows) == (100, 40)
    assert "hello from before" in t1.replay.text()
    assert t1.resumed is False
    assert t1.exit_code is None
    assert t1.started_at == 2.0
    # The pane without a hosted agent is untouched: it resumes on connect.
    assert t2.status == "pending"
    assert t2.pty_id is None
    # Nothing is killed here; that is boot_restore's job.
    assert pool.killed == []
    assert {info.terminal_id for info in pool.hosted()} == {"old", "unrelated"}

    # Live output keeps flowing into the pane, and an exit ends it.
    await pool.emit("new", "more output")
    assert "more output" in t1.replay.text()
    assert t1.last_output_at is not None
    await pool.die("new", 3)
    assert t1.status == "exited"
    assert t1.exit_code == 3
    assert t1.pty_id is None


async def test_an_adopted_pane_is_rejoined_on_attach_not_respawned(tmp_path: Path) -> None:
    pool = FakeHostedPool()
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    pool.add_hosted("h1", history_id=session.find("T1").history_id, replay="screen")
    await registry._adopt_hosted(session)  # noqa: SLF001

    seen: list[str] = []

    async def _viewer(text: str) -> None:
        seen.append(text)

    await registry.attach("T1", 100, 40, _viewer, _noop_exit)
    assert pool.spawns == []
    await pool.emit("h1", "live bytes")
    assert "live bytes" in "".join(seen)


# --------------------------------------------------------------- boot_restore
async def test_boot_restore_reopens_the_snapshot_and_ends_unclaimed_terminals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    _save_snapshot(tmp_path, ["hist-1", "hist-2"], saved_at=now)
    # An older all-closed marker does not stop the reopen.
    resume_store.note_all_closed(now - 100)
    pool = FakeHostedPool()
    pool.add_hosted("h-1", history_id="hist-1", replay="agent one was here")
    pool.add_hosted("orphan", history_id="closed-long-ago")
    connect = ConnectRecorder(pool)
    monkeypatch.setattr(ide, "_connect_pty_host", connect)

    registry = ide.Registry()
    await registry.boot_restore()

    # Attach only — a boot must never START a host nobody needs.
    assert connect.calls == [False]
    assert len(registry.sessions) == 1
    session = registry.sessions[0]
    t1, t2 = session.find("T1"), session.find("T2")
    assert t1.status == "live" and t1.pty_id == "h-1"
    assert "agent one was here" in t1.replay.text()
    assert t2.status == "pending"
    assert pool.adopted == ["h-1"]
    assert pool.killed == ["orphan"]
    assert pool.hosted() == []

    # Once per process.
    await registry.boot_restore()
    assert connect.calls == [False]
    assert len(registry.sessions) == 1


async def test_boot_restore_respects_closing_everything_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    _save_snapshot(tmp_path, ["hist-1"], saved_at=now - 50)
    resume_store.note_all_closed(now)
    pool = FakeHostedPool()
    pool.add_hosted("h-1", history_id="hist-1")
    monkeypatch.setattr(ide, "_connect_pty_host", ConnectRecorder(pool))

    registry = ide.Registry()
    await registry.boot_restore()

    assert registry.sessions == []
    assert pool.adopted == []
    # Nothing reopened claims it, so it is ended rather than kept alive forever.
    assert pool.killed == ["h-1"]


async def test_boot_restore_without_a_host_still_reopens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_snapshot(tmp_path, ["hist-1"], saved_at=time.time())
    connect = ConnectRecorder(None)
    monkeypatch.setattr(ide, "_connect_pty_host", connect)

    registry = ide.Registry(pty_manager=FakePtyManager())
    await registry.boot_restore()

    # A pool that already exists is kept; no attach is even attempted.
    assert connect.calls == []
    assert len(registry.sessions) == 1
    assert registry.sessions[0].find("T1").status == "pending"


async def test_closing_the_last_workspace_writes_the_all_closed_marker(tmp_path: Path) -> None:
    registry = ide.Registry(pty_manager=FakePtyManager())
    assert resume_store.all_closed_at() is None
    other = tmp_path / "other"
    other.mkdir()
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.start(str(other), [{"agent": "claude", "name": "T1"}])
    await registry.end(registry.sessions[0].id)
    assert resume_store.all_closed_at() is None, "one workspace is still open"
    before = time.time()
    await registry.end(registry.sessions[0].id)
    stamp = resume_store.all_closed_at()
    assert stamp is not None and stamp >= before - 1


# ------------------------------------------------------------- _live_manager
async def test_the_host_is_never_used_without_boot_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect = ConnectRecorder(FakeHostedPool())
    monkeypatch.setattr(ide, "_connect_pty_host", connect)
    fake = FakePtyManager()
    registry = ide.Registry(pty_manager=fake)
    assert registry._host_enabled is False  # noqa: SLF001

    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)

    assert connect.calls == []
    assert len(fake.spawns) == 1
    assert await registry._live_manager() is fake  # noqa: SLF001


async def test_after_boot_restore_new_panes_start_in_the_host_with_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = FakeHostedPool()
    connect = ConnectRecorder(pool)
    monkeypatch.setattr(ide, "_connect_pty_host", connect)
    registry = ide.Registry()
    await registry.boot_restore()
    assert connect.calls == [False]

    session = await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit, appearance="dark")
    term = session.find("T1")

    assert len(pool.spawns) == 1
    assert pool.metas == [
        {
            "history_id": term.history_id,
            "name": "T1",
            "workspace_id": session.id,
            "agent": "claude",
        }
    ]
    assert term.pty_id == "fake-pty-1"


async def test_a_lost_host_is_reconnected_with_start_for_the_next_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = FakeHostedPool()
    connect = ConnectRecorder(first)
    monkeypatch.setattr(ide, "_connect_pty_host", connect)
    registry = ide.Registry()
    await registry.boot_restore()

    first.connected = False
    second = FakeHostedPool()
    connect.pool = second
    assert await registry._live_manager() is second  # noqa: SLF001
    assert connect.calls == [False, True]

    # A host that cannot come back degrades to an in-process pool, for good.
    second.connected = False
    connect.pool = None
    fallback = await registry._live_manager()  # noqa: SLF001
    assert not getattr(fallback, "persistent", False)
    assert await registry._live_manager() is fallback  # noqa: SLF001
    assert connect.calls == [False, True, True]


# ------------------------------------------- a re-joined agent keeps its job
async def test_a_rejoined_agent_that_had_a_job_reads_working_while_it_moves(
    tmp_path: Path,
) -> None:
    """Its instruction was sent before the restart; movement must still count.

    Maintainer report 2026-09-28: after an app restart, three agents visibly
    mid-task were listed "done" for the next half hour, because no submit stamp
    of the NEW app proved their work.
    """
    from jarvis.agentic_ide import activity

    pool = FakeHostedPool()
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(
        str(tmp_path), [{"agent": "claude", "name": "T1"}, {"agent": "claude", "name": "T2"}]
    )
    t1, t2 = session.find("T1"), session.find("T2")
    t1.prompts_sent = 2  # it had been given work before the restart
    pool.add_hosted("busy", history_id=t1.history_id, replay="Processing…")
    pool.add_hosted("fresh", history_id=t2.history_id, replay="")

    await registry._adopt_hosted(session)  # noqa: SLF001 - the unit under test

    now = time.time()
    t1.last_output_at = now
    t2.last_output_at = now
    assert activity.read_activity(t1, now=now) == "working"
    # A re-joined pane nobody ever tasked keeps the strict rule: its repaint is
    # not work.
    assert activity.read_activity(t2, now=now) == "waiting"
    assert activity.has_work_behind_it(t1)

    # The proof belongs to that one process: a respawn moves the generation on.
    t1.process_generation += 1
    assert activity.read_activity(t1, now=now) == "waiting"
