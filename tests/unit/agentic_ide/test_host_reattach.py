"""RUB-102 regression: a reopened app re-joins what the PTY host kept running.

Live failure 2026-09-28: the UI restored and attached panes before the boot
pass ran, a synchronous ``_manager()`` call pinned an in-process pool, and
every agent was started again inside the app while the originals kept running
unseen in the host. Whatever order things arrive in, a pane must re-join its
hosted agent and never start a second one.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import host_mode, resume_store
from jarvis.agentic_ide import session as ide
from tests.unit.agentic_ide.test_pty_host_adoption import ConnectRecorder, FakeHostedPool


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    host_mode.reset()
    yield
    host_mode.reset()


async def _noop(_value: object) -> None:
    return None


def _snapshot(folder: Path, *, ws_id: str = "ide_reattach01") -> resume_store.Snapshot:
    now = time.time()
    workspace = resume_store.SnapshotWorkspace(
        session_id=ws_id,
        folder=str(folder),
        terminals=[
            resume_store.SnapshotTerminal(
                key=f"t{i}", name=f"T{i}", agent="claude", history_id=f"hist-{i}", column=i
            )
            for i in (1, 2)
        ],
        saved_at=now,
    )
    snap = resume_store.Snapshot(saved_at=now, workspaces=[workspace], active_session_id=ws_id)
    resume_store.save(snap)
    return snap


def _host_with_both(ws_id: str = "ide_reattach01") -> FakeHostedPool:
    pool = FakeHostedPool()
    for i in (1, 2):
        pool.add_hosted(f"h-{i}", history_id=f"hist-{i}", replay=f"agent {i} kept working")
        pool.hosted_terminals[f"h-{i}"].meta["workspace_id"] = ws_id
    return pool


def test_the_switch_is_read_when_the_registry_is_created() -> None:
    assert ide.Registry()._host_enabled is False
    host_mode.enable()
    assert ide.Registry()._host_enabled is True


def test_a_sync_caller_never_pins_an_in_process_pool_in_host_mode() -> None:
    host_mode.enable()
    registry = ide.Registry()
    pool = registry._manager()
    assert registry._pty is None  # not pinned: the host can still be attached
    assert pool.has("anything") is False


async def test_ui_restoring_before_the_boot_pass_rejoins_the_hosted_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = _snapshot(tmp_path)
    pool = _host_with_both()
    connect = ConnectRecorder(pool)
    monkeypatch.setattr(ide, "_connect_pty_host", connect)
    host_mode.enable()
    registry = ide.Registry()
    registry._manager()  # an early synchronous touch, as in the live failure

    # The UI restores the workspace; the boot pass has not run.
    await registry.restore_workspace(snap.workspaces[0].session_id)

    session = registry.sessions[0]
    assert [t.pty_id for t in session.terminals] == ["h-1", "h-2"]
    assert all(t.status == "live" for t in session.terminals)
    assert connect.calls == [False]  # attached to the running host, started none
    assert pool.spawns == []

    # A viewer connecting now re-joins; nothing is started again.
    term = await registry.attach("T1", 100, 30, _noop, _noop, workspace_id=session.id)
    assert term.reattached is True
    assert pool.spawns == []


async def test_a_pane_restored_before_the_host_answered_rejoins_on_attach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = _snapshot(tmp_path)
    pool = _host_with_both()
    answers: list[Any] = [None]  # the first look finds no host yet

    async def connect(*, start: bool) -> Any:
        return answers.pop(0) if answers else pool

    monkeypatch.setattr(ide, "_connect_pty_host", connect)
    host_mode.enable()
    registry = ide.Registry()
    await registry.restore(snap)
    session = registry.sessions[0]
    assert all(t.status == "pending" for t in session.terminals)

    term = await registry.attach("T2", 100, 30, _noop, _noop, workspace_id=session.id)

    assert term.pty_id == "h-2" and term.reattached is True
    assert pool.spawns == []  # re-joined, not started a second time
    assert pool.adopted == ["h-2"]


async def test_boot_keeps_hosted_agents_of_remembered_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _snapshot(tmp_path)
    resume_store.note_all_closed(time.time() + 10)  # nothing is reopened at boot
    pool = _host_with_both()
    pool.add_hosted("stray", history_id="nobody")  # belongs to no known workspace
    monkeypatch.setattr(ide, "_connect_pty_host", ConnectRecorder(pool))

    registry = ide.Registry()
    await registry.boot_restore()

    assert pool.killed == ["stray"]
    assert {info.terminal_id for info in pool.hosted()} == {"h-1", "h-2"}
