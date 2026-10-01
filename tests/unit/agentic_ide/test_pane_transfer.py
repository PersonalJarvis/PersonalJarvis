"""Moving a pane into another open workspace — the chat started in the wrong tab.

The promise is the same one rearranging makes inside a grid: nothing is started
or stopped. The agent's process, conversation and history belong to the pane,
so moving it only changes which tab lists and draws it. What a tab owns on its
own — the call-sign, the key, the folder — is what these tests pin, because
every failure there is silent on screen: two panes answering to one name, or a
pane that ends up listed under another project's folder. Moving is only
allowed between workspaces on the same folder.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import MAX_TERMINALS, Registry, SessionError
from jarvis.ui.web import agentic_ide_routes as routes
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the recents file out of the developer's real data directory."""
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    instance = Registry(pty_manager=fake_pty)
    monkeypatch.setattr(routes, "get_registry", lambda: instance)
    return instance


def _request() -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=None)))


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _two_tabs(registry: Registry, folder: Path, first: int = 2, second: int = 2):
    source = await registry.start(str(folder), [{"agent": "claude"}] * first, name="Jarvis")
    target = await registry.start(str(folder), [{"agent": "claude"}] * second, name="Blog")
    return source, target


async def test_a_running_agent_moves_without_a_restart(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The whole point: the agent keeps working, the new tab re-joins it."""
    source, target = await _two_tabs(registry, tmp_path)
    await registry.attach("T1", 80, 24, _noop, _noop_exit, workspace_id=source.id)
    term = source.find("T1")
    assert term is not None and term.status == "live"
    pty_id, history_id = term.pty_id, term.history_id

    _, _, moved = await registry.transfer_terminal(
        "T1", workspace_id=source.id, target_workspace_id=target.id
    )

    assert moved is term
    assert fake_pty.closed == []
    assert len(fake_pty.spawns) == 1
    assert moved.pty_id == pty_id and moved.status == "live"
    assert all(t.history_id != history_id for t in source.terminals)
    assert any(t is moved for t in target.terminals)

    # The new tab's viewer takes the re-join path: still exactly one process.
    await registry.attach(moved.name, 100, 30, _noop, _noop_exit, workspace_id=target.id)
    assert len(fake_pty.spawns) == 1
    assert moved.viewer_output is _noop


async def test_the_old_tabs_viewer_lets_go(registry: Registry, tmp_path: Path) -> None:
    """The old grid's socket stops being fed, and its late detach hits nothing."""
    source, target = await _two_tabs(registry, tmp_path)
    await registry.attach("T2", 80, 24, _noop, _noop_exit, workspace_id=source.id)

    _, _, moved = await registry.transfer_terminal(
        "T2", workspace_id=source.id, target_workspace_id=target.id
    )

    assert moved.viewer_output is None and moved.watchers == []
    registry.detach("T2", workspace_id=source.id, viewer=_noop)
    assert registry.write("T2", "x", workspace_id=source.id) is False


async def test_a_taken_position_moves_to_the_lowest_free_number(
    registry: Registry, tmp_path: Path
) -> None:
    """A "T2" joining a tab that has a T2 must not leave two panes called T2."""
    source, target = await _two_tabs(registry, tmp_path)

    _, _, moved = await registry.transfer_terminal(
        "T2", workspace_id=source.id, target_workspace_id=target.id
    )

    assert moved.name == "T3"
    assert sorted(t.name for t in target.terminals) == ["T1", "T2", "T3"]
    assert len({t.key for t in target.terminals}) == 3
    assert [t.name for t in source.terminals] == ["T1"]


async def test_a_custom_name_travels_and_is_suffixed_only_when_taken(
    registry: Registry, tmp_path: Path
) -> None:
    source, target = await _two_tabs(registry, tmp_path)
    await registry.rename_terminal("T1", "Mika")  # the front tab is "Blog"
    await registry.activate(source.id)
    await registry.rename_terminal("T1", "Mika")

    _, _, moved = await registry.transfer_terminal(
        "Mika", workspace_id=source.id, target_workspace_id=target.id
    )

    assert moved.name == "Mika 2"
    assert target.find("Mika 2") is moved


async def test_its_key_never_collides_with_a_renamed_pane(
    registry: Registry, tmp_path: Path
) -> None:
    """A renamed pane keeps its key, so a free NAME does not mean a free key."""
    source, target = await _two_tabs(registry, tmp_path, first=1, second=1)
    await registry.rename_terminal("T1", "Writer")  # Blog's t1 is now "Writer"

    _, _, moved = await registry.transfer_terminal(
        "T1", workspace_id=source.id, target_workspace_id=target.id
    )

    assert moved.name == "T1"
    assert len({t.key for t in target.terminals}) == 2
    assert target.find("T1") is moved


async def test_a_workspace_on_another_folder_is_refused(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A pane never moves to another project's folder — and nothing changes."""
    jarvis_dir, blog_dir = tmp_path / "jarvis", tmp_path / "blog"
    jarvis_dir.mkdir()
    blog_dir.mkdir()
    source = await registry.start(str(jarvis_dir), [{"agent": "claude"}] * 2, name="Jarvis")
    target = await registry.start(str(blog_dir), [{"agent": "claude"}], name="Blog")
    await registry.attach("T2", 80, 24, _noop, _noop_exit, workspace_id=source.id)

    with pytest.raises(SessionError, match="same folder"):
        await registry.transfer_terminal(
            "T2", workspace_id=source.id, target_workspace_id=target.id
        )

    assert [t.name for t in source.terminals] == ["T1", "T2"]
    assert [t.name for t in target.terminals] == ["T1"]
    assert fake_pty.closed == []


async def test_the_route_refuses_another_folder_as_a_conflict(
    registry: Registry, tmp_path: Path
) -> None:
    jarvis_dir, blog_dir = tmp_path / "jarvis", tmp_path / "blog"
    jarvis_dir.mkdir()
    blog_dir.mkdir()
    source = await registry.start(str(jarvis_dir), [{"agent": "claude"}], name="Jarvis")
    target = await registry.start(str(blog_dir), [{"agent": "claude"}], name="Blog")

    with pytest.raises(HTTPException) as refused:
        await routes.transfer_terminal(
            _request(),
            "T1",
            routes.TransferTerminalRequest(workspace_id=source.id, target_workspace_id=target.id),
        )

    assert refused.value.status_code == 409


async def test_the_same_folder_needs_no_folder_of_its_own(
    registry: Registry, tmp_path: Path
) -> None:
    source, target = await _two_tabs(registry, tmp_path)
    _, _, moved = await registry.transfer_terminal(
        "T1", workspace_id=source.id, target_workspace_id=target.id
    )
    assert moved.folder == ""


async def test_the_move_is_saved_with_the_new_tab(registry: Registry, tmp_path: Path) -> None:
    """After a restart the pane comes back in the tab it was moved to."""
    source, target = await _two_tabs(registry, tmp_path)
    term = source.find("T1")
    assert term is not None

    await registry.transfer_terminal("T1", workspace_id=source.id, target_workspace_id=target.id)

    saved = {
        w.session_id: {t.history_id for t in w.terminals} for w in resume_store.load().workspaces
    }
    assert term.history_id in saved[target.id]
    assert term.history_id not in saved[source.id]


async def test_the_old_tab_forgets_a_selection_of_the_moved_pane(
    registry: Registry, tmp_path: Path
) -> None:
    source, target = await _two_tabs(registry, tmp_path)
    source.focused = "T2"
    source.surface_prompt_target = "T2"

    await registry.transfer_terminal("T2", workspace_id=source.id, target_workspace_id=target.id)

    assert source.focused == "" and source.surface_prompt_target == ""


async def test_moving_into_the_same_tab_changes_nothing(
    registry: Registry, tmp_path: Path
) -> None:
    source, _ = await _two_tabs(registry, tmp_path)
    before = [(t.name, t.key) for t in source.terminals]

    await registry.transfer_terminal("T1", workspace_id=source.id, target_workspace_id=source.id)

    assert [(t.name, t.key) for t in source.terminals] == before


async def test_a_full_or_closed_target_is_refused(registry: Registry, tmp_path: Path) -> None:
    source, target = await _two_tabs(registry, tmp_path, second=MAX_TERMINALS)

    with pytest.raises(SessionError, match="maximum"):
        await registry.transfer_terminal(
            "T1", workspace_id=source.id, target_workspace_id=target.id
        )
    with pytest.raises(SessionError, match="not open"):
        await registry.transfer_terminal(
            "T1", workspace_id=source.id, target_workspace_id="ide_missing"
        )
    assert len(source.terminals) == 2


async def test_the_route_answers_with_the_new_name_and_both_tabs(
    registry: Registry, tmp_path: Path
) -> None:
    source, target = await _two_tabs(registry, tmp_path)

    body = await routes.transfer_terminal(
        _request(),
        "T2",
        routes.TransferTerminalRequest(workspace_id=source.id, target_workspace_id=target.id),
    )

    assert body["terminal"]["name"] == "T3"
    assert body["source_workspace_id"] == source.id
    assert body["target_workspace_id"] == target.id


async def test_the_route_tells_a_missing_pane_from_a_missing_tab(
    registry: Registry, tmp_path: Path
) -> None:
    source, target = await _two_tabs(registry, tmp_path)

    with pytest.raises(HTTPException) as missing_pane:
        await routes.transfer_terminal(
            _request(),
            "T9",
            routes.TransferTerminalRequest(workspace_id=source.id, target_workspace_id=target.id),
        )
    with pytest.raises(HTTPException) as missing_tab:
        await routes.transfer_terminal(
            _request(),
            "T1",
            routes.TransferTerminalRequest(workspace_id=source.id, target_workspace_id="ide_x"),
        )

    assert missing_pane.value.status_code == 404
    assert missing_tab.value.status_code == 409


# ------------------------------------------------------------------ placement
async def test_a_chosen_place_shares_that_panes_room(registry: Registry, tmp_path: Path) -> None:
    """Beside the pane the user picked, the way a split carves it."""
    source = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Jarvis")
    target = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Blog")
    await registry.add_terminal(workspace_id=target.id, direction="right")  # Blog: T1 | T2

    _, _, moved = await registry.transfer_terminal(
        "T1", workspace_id=source.id, target_workspace_id=target.id, anchor="T1", side="below"
    )

    assert moved.name == "T3"
    assert [(t.name, t.column, t.slot) for t in target.terminals] == [
        ("T1", 0, 0),
        ("T3", 0, 1),
        ("T2", 1, 0),
    ]


async def test_left_of_a_pane_puts_it_first_in_the_row(
    registry: Registry, tmp_path: Path
) -> None:
    source, target = await _two_tabs(registry, tmp_path, first=1, second=1)

    await registry.transfer_terminal(
        "T1", workspace_id=source.id, target_workspace_id=target.id, anchor="T1", side="left"
    )

    assert [t.name for t in target.terminals] == ["T2", "T1"]


async def test_a_place_without_room_changes_nothing(registry: Registry, tmp_path: Path) -> None:
    """Refused before the pane leaves its tab — never half-way between two."""
    source = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Jarvis")
    target = await registry.start(str(tmp_path), [{"agent": "claude"}], name="Blog")
    for _ in range(session_mod.MAX_GRID_COLUMNS - 1):
        await registry.add_terminal(workspace_id=target.id, direction="right")
    before = [t.name for t in target.terminals]

    with pytest.raises(SessionError, match="No room"):
        await registry.transfer_terminal(
            "T1", workspace_id=source.id, target_workspace_id=target.id, anchor="T1", side="right"
        )

    assert [t.name for t in source.terminals] == ["T1"]
    assert [t.name for t in target.terminals] == before


async def test_an_unknown_anchor_is_refused(registry: Registry, tmp_path: Path) -> None:
    source, target = await _two_tabs(registry, tmp_path)

    with pytest.raises(SessionError, match="No terminal called 'Mika' in Blog"):
        await registry.transfer_terminal(
            "T1", workspace_id=source.id, target_workspace_id=target.id, anchor="Mika"
        )
    assert len(source.terminals) == 2


async def test_the_layout_route_describes_another_tab(registry: Registry, tmp_path: Path) -> None:
    """The map the move dialog draws: shape and panes, nothing heavier."""
    _, target = await _two_tabs(registry, tmp_path)

    body = routes.get_workspace_layout(target.id)

    assert body["name"] == "Blog"
    assert [t["name"] for t in body["terminals"]] == ["T1", "T2"]
    assert all(isinstance(t["title"], str) for t in body["terminals"])
    assert body["layout"] is not None
    with pytest.raises(HTTPException) as missing:
        routes.get_workspace_layout("ide_missing")
    assert missing.value.status_code == 404


async def test_the_route_passes_the_chosen_place(registry: Registry, tmp_path: Path) -> None:
    source, target = await _two_tabs(registry, tmp_path, first=1, second=1)

    await routes.transfer_terminal(
        _request(),
        "T1",
        routes.TransferTerminalRequest(
            workspace_id=source.id, target_workspace_id=target.id, anchor="T1", side="above"
        ),
    )

    assert [(t.name, t.slot) for t in target.terminals] == [("T2", 0), ("T1", 1)]
