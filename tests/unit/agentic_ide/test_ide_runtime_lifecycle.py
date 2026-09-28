"""RUB-102 lifecycle pieces around the PTY host: focus, reboot recovery, stop.

* the focused pane is saved with its workspace and put back on reopen,
* after a reboot (nothing left in the host) panes that were working are
  started without a viewer and continued; panes whose agent survived are not,
* the explicit stop ends every agent and keeps the next start from reopening.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as ide
from tests.unit.agentic_ide.test_pty_host_adoption import FakeHostedPool


@pytest.fixture(autouse=True)
def _no_real_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))


def _snapshot(folder: Path, *, working: bool, focused: str = "") -> None:
    now = time.time()
    workspace = resume_store.SnapshotWorkspace(
        session_id="ide_lifecycle01",
        folder=str(folder),
        terminals=[
            resume_store.SnapshotTerminal(
                key=f"t{index + 1}",
                name=f"T{index + 1}",
                agent="claude",
                history_id=f"hist-{index + 1}",
                column=index,
                continuation_needed=working,
            )
            for index in range(2)
        ],
        saved_at=now,
        focused=focused,
    )
    resume_store.save(
        resume_store.Snapshot(
            saved_at=now, workspaces=[workspace], active_session_id=workspace.session_id
        )
    )




# ------------------------------------------------------------------ focus
async def test_focused_pane_is_saved_and_restored(tmp_path: Path) -> None:
    registry = ide.Registry(pty_manager=FakeHostedPool())
    session = await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}])
    second = session.terminals[1].name

    assert registry.set_surface_context(
        workspace_id=session.id, view="grid", on_screen=True, terminal=None, prompt_target=second
    )
    assert registry.take_focus_dirty() is True
    # The same report again is not a change and asks for no save.
    registry.set_surface_context(
        workspace_id=session.id, view="grid", on_screen=True, terminal=None, prompt_target=second
    )
    assert registry.take_focus_dirty() is False
    assert session.to_dict()["focused"] == second

    snapshot = registry.snapshot()
    assert snapshot is not None
    parsed = resume_store.Snapshot.from_dict(snapshot.to_dict())
    assert parsed is not None and parsed.workspaces[0].focused == second

    reopened = ide.Registry(pty_manager=FakeHostedPool())
    await reopened.restore(parsed)
    assert reopened.sessions[0].focused == second


async def test_a_focus_on_a_pane_that_is_gone_is_dropped(tmp_path: Path) -> None:
    _snapshot(tmp_path, working=False, focused="Nobody")
    snapshot = resume_store.load()
    assert snapshot is not None
    registry = ide.Registry(pty_manager=FakeHostedPool())
    await registry.restore(snapshot)
    assert registry.sessions[0].focused == ""


# ------------------------------------------------------------------- stop
async def test_stop_runtime_ends_everything_and_blocks_the_auto_reopen(tmp_path: Path) -> None:
    pool = FakeHostedPool()
    registry = ide.Registry(pty_manager=pool)
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    await registry.start(str(first), [{"agent": "claude"}])
    await registry.start(str(second), [{"agent": "claude"}])
    status = registry.runtime_status()
    assert status["mode"] == "host" and status["workspaces"] == 2

    assert await registry.stop_runtime() == 2
    assert registry.sessions == []
    closed_at = resume_store.all_closed_at()
    snapshot = resume_store.load()
    assert closed_at is not None and snapshot is not None
    newest = max(w.saved_at for w in snapshot.workspaces)
    assert closed_at >= newest
    assert registry.runtime_status()["workspaces"] == 0


def test_runtime_status_before_anything_started() -> None:
    status = ide.Registry().runtime_status()
    assert status == {
        "mode": "idle",
        "host_pid": 0,
        "persistent_enabled": False,
        "workspaces": 0,
        "live_agents": 0,
    }


# ------------------------------------------------- finished while closed
async def _idle_pane(registry: ide.Registry, folder: Path):
    async def noop(_value: object) -> None:
        return None

    session = await registry.start(str(folder), [{"agent": "claude", "name": "Alex"}])
    term = await registry.attach("Alex", 100, 30, noop, noop)
    term.transcript.feed("\r\n❯ \r\n")
    term.last_output_at = 0.0
    term.last_input_at = None
    return session, term


async def test_a_job_finished_while_the_app_was_closed_rings_the_bell(tmp_path: Path) -> None:
    from jarvis.agentic_ide import notifications
    from jarvis.agentic_ide.notifications import SETTLE_S

    notifications.reset()
    try:
        registry = ide.Registry(pty_manager=FakeHostedPool())
        _session, term = await _idle_pane(registry, tmp_path)
        # Re-joined after an app restart; the last checkpoint saw it working.
        term.worked_while_detached = True
        watcher = notifications.watcher()

        assert watcher.poll(registry, now=100.0) == []  # first sight files nothing
        filed: list[notifications.Notification] = []
        for moment in (105.0, 105.0 + SETTLE_S + 1.0, 130.0 + SETTLE_S):
            filed += watcher.poll(registry, now=moment)
        assert [entry.kind for entry in filed] == ["completed"]
        assert term.worked_while_detached is False
    finally:
        notifications.reset()


async def test_an_idle_rejoined_pane_stays_quiet(tmp_path: Path) -> None:
    from jarvis.agentic_ide import notifications

    notifications.reset()
    try:
        registry = ide.Registry(pty_manager=FakeHostedPool())
        await _idle_pane(registry, tmp_path)
        watcher = notifications.watcher()
        for moment in (100.0, 110.0, 200.0, 900.0):
            assert watcher.poll(registry, now=moment) == []
    finally:
        notifications.reset()
