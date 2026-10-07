"""Closing the final pane retires its workspace across registry entry points."""

from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as ide
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> ide.Registry:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return ide.Registry(pty_manager=FakePtyManager())


async def test_final_close_selects_the_most_recent_survivor(
    registry: ide.Registry, tmp_path: Path
) -> None:
    first = await registry.start(str(tmp_path), [{"agent": "claude"}], name="First")
    second = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Second")
    target = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Target")
    await registry.activate(first.id)
    await registry.activate(target.id)

    await registry.close_terminal("T1", workspace_id=target.id)

    assert registry.get(target.id) is None
    assert registry.session is first
    assert registry.get(second.id) is second
    stored = resume_store.load()
    assert stored is not None
    assert stored.active_session_id == first.id
    assert {space.session_id for space in stored.workspaces} == {first.id, second.id}


async def test_final_background_close_preserves_the_active_workspace(
    registry: ide.Registry, tmp_path: Path
) -> None:
    background = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Background")
    front = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Front")

    await registry.close_terminal("T1", workspace_id=background.id)

    assert registry.get(background.id) is None
    assert registry.session is front
    assert len(front.terminals) == 1


@pytest.mark.parametrize("invalid_names", [[], ["missing", "T1"]])
async def test_batch_closing_every_pane_retires_the_workspace(
    registry: ide.Registry, tmp_path: Path, invalid_names: list[str]
) -> None:
    workspace = await registry.start(str(tmp_path), [{"agent": "claude"}] * 2)

    closed, failed = await registry.close_terminals(["T1", "T2", *invalid_names])

    assert len(closed) == 2
    assert len(failed) == len(invalid_names)
    assert registry.get(workspace.id) is None
    assert registry.session is None
    assert resume_store.load() is None


async def test_empty_close_selection_does_not_close_a_workspace(
    registry: ide.Registry, tmp_path: Path
) -> None:
    workspace = await registry.start(str(tmp_path), [{"agent": "claude"}])

    assert await registry.close_terminals([]) == ([], [])

    assert registry.session is workspace
    assert len(workspace.terminals) == 1


async def test_auto_close_preserves_other_closed_workspace_restore_points(
    registry: ide.Registry, tmp_path: Path
) -> None:
    remembered = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Remembered")
    await registry.end(remembered.id)
    target = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Target")

    await registry.close_terminal("T1", workspace_id=target.id)

    stored = resume_store.load()
    assert stored is not None
    assert [space.session_id for space in stored.workspaces] == [remembered.id]
    assert registry.session is None


async def test_transferring_the_last_pane_closes_only_the_source_workspace(
    registry: ide.Registry, tmp_path: Path
) -> None:
    source = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Source")
    target = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Target")
    moved = source.terminals[0]
    await registry.activate(source.id)

    await registry.transfer_terminal("T1", workspace_id=source.id, target_workspace_id=target.id)

    assert registry.get(source.id) is None
    assert registry.session is target
    assert moved in target.terminals
    assert not moved.stopping
    stored = resume_store.load()
    assert stored is not None
    assert [space.session_id for space in stored.workspaces] == [target.id]
