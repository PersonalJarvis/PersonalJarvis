"""Starting a pane nobody is looking at (the office's spawn point).

A pane's agent is spawned by the first viewer that attaches. The spawn point
opens panes without showing them and hands each one a task, so it waited on a
pane that never started and gave the task up (live 2026-10-02).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry, SessionError
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return Registry(pty_manager=fake_pty)


async def _settle(registry: Registry) -> None:
    await asyncio.gather(*list(registry._cold_start_holds))


async def test_a_pane_nobody_opened_starts_without_a_viewer(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]
    assert term.status == "pending"

    registry.start_pending(term.name, session.id)
    await _settle(registry)

    assert term.status == "live"
    assert len(fake_pty.spawns) == 1
    assert term.viewer_output is None, "the background start must not hold the viewer slot"


async def test_a_running_pane_is_not_started_twice(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]
    registry.start_pending(term.name, session.id)
    await _settle(registry)

    registry.start_pending(term.name, session.id)
    await _settle(registry)

    assert len(fake_pty.spawns) == 1


async def test_an_unknown_pane_is_refused(registry: Registry, tmp_path: Path) -> None:
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])

    with pytest.raises(SessionError):
        registry.start_pending("Nobody", session.id)
