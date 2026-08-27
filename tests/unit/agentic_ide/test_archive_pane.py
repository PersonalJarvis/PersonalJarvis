"""Archiving a pane hides it from the chat list without closing it."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
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


async def test_archiving_hides_the_pane_from_the_list_but_keeps_it_running(
    registry: Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}])
    session, term = await registry.set_terminal_archived("T1", True)

    assert term.archived is True
    assert session.find("T1") is term
    rows = {row["name"]: row for row in registry.panes()}
    assert rows["T1"]["archived"] is True
    assert rows["T2"]["archived"] is False


async def test_restoring_puts_the_pane_back_on_the_list(registry: Registry, tmp_path: Path) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await registry.set_terminal_archived("T1", True)
    _, term = await registry.set_terminal_archived("T1", False)

    assert term.archived is False
    assert registry.panes()[0]["archived"] is False


async def test_the_resume_snapshot_remembers_an_archived_pane(
    registry: Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await registry.set_terminal_archived("T1", True)
    snap = registry.session.terminals[0].to_snapshot()

    assert snap.archived is True
    restored = resume_store.SnapshotTerminal.from_dict(snap.to_dict())
    assert restored is not None
    assert restored.archived is True
    # An older snapshot, written before archive existed, reads as live.
    older = resume_store.SnapshotTerminal.from_dict({"name": "T1", "agent": "claude", "key": "t1"})
    assert older is not None
    assert older.archived is False


async def test_closing_a_pane_in_a_background_workspace_leaves_the_front_one(
    registry: Registry, tmp_path: Path
) -> None:
    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    first.mkdir()
    second.mkdir()
    background = await registry.start(str(first), [{"agent": "claude"}])
    front = await registry.start(str(second), [{"agent": "claude"}])

    assert registry.active_id == front.id
    await registry.close_terminal("T1", workspace_id=background.id)

    assert front.find("T1") is not None
    assert background.find("T1") is None


async def test_archiving_pins_to_the_named_workspace(registry: Registry, tmp_path: Path) -> None:
    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    first.mkdir()
    second.mkdir()
    background = await registry.start(str(first), [{"agent": "claude"}])
    front = await registry.start(str(second), [{"agent": "claude"}])

    await registry.set_terminal_archived("T1", True, workspace_id=background.id)

    assert background.find("T1").archived is True
    assert front.find("T1").archived is False


async def test_archiving_an_unknown_pane_names_the_real_ones(
    registry: Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    with pytest.raises(SessionError, match="T1"):
        await registry.set_terminal_archived("Gandalf", True)
