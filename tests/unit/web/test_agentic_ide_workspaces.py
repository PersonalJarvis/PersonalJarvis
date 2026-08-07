"""The workspace bar's endpoints: list, switch, close one.

What these defend is a single promise the UI makes and the backend has to keep:
**switching workspaces neither starts nor stops anything.** Everything else in
the feature follows from it — the tab that shows a live-pane count is telling
the truth, the pane you come back to is the process you left, and the only
button that stops an agent is the one labelled close.

Driven through the route functions against a real ``Registry`` (with a fake PTY
pool), so a change that satisfies the registry but forgets the route — or the
other way round — still fails here.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry
from jarvis.ui.web import agentic_ide_routes as routes
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _agents_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both coding CLIs are "on PATH" — this suite is about workspaces.

    Never asks what the developer happens to have installed (AP-23).
    """
    monkeypatch.setattr(session_mod, "agent_argv", lambda agent: ("/usr/bin/" + agent,))


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the resume snapshot and the recents list out of the real profile.

    A test that opens a workspace must not rewrite the user's own recent-folder
    list — it happened, and every entry in the live app became a pytest path.
    """
    from jarvis.agentic_ide import recents, resume_store

    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(resume_store, "_store_path", lambda: store / "last.json")
    monkeypatch.setattr(recents, "remember", lambda *a, **k: None)


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    """A real registry the routes reach through ``get_registry``."""
    instance = Registry(pty_manager=fake_pty)
    monkeypatch.setattr(routes, "get_registry", lambda: instance)
    return instance


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


def _request(bus: object | None = None) -> SimpleNamespace:
    """The one thing the open route needs from a Request: ``app.state.bus``.

    Opening a workspace announces itself on the bus so every OTHER window can
    redraw its grid instead of talking to a workspace that moved. Without a bus
    the announcement is skipped, which is what these tests want: they are about
    what opening DOES, and the announcement has its own suite.
    """
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=bus)))


async def _open(registry: Registry, folder: Path, panes: int = 1) -> object:
    folder.mkdir(parents=True, exist_ok=True)
    return await routes.start_session(
        _request(),
        routes.StartSessionRequest(
            folder=str(folder),
            terminals=[routes.TerminalRequest(agent="claude") for _ in range(panes)],
        ),
    )


# ------------------------------------------------------------------- listing
async def test_the_bar_lists_every_open_workspace_with_the_front_one_marked(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path / "alpha")
    await _open(registry, tmp_path / "beta", panes=2)

    listed = await routes.get_workspaces()

    assert [w.name for w in listed.workspaces] == ["alpha", "beta"]
    assert [w.active for w in listed.workspaces] == [False, True]
    assert listed.active_id == registry.active_id
    assert listed.workspaces[1].terminals == 2
    assert listed.max_workspaces == session_mod.MAX_WORKSPACES


async def test_a_tab_counts_the_panes_that_are_really_running(
    registry: Registry, tmp_path: Path
) -> None:
    """``live_terminals`` is what makes a background workspace honest.

    A tab claiming two running agents while both are dead would be worse than
    no count at all — the point of the number is to tell the user what is still
    burning tokens somewhere they cannot see.
    """
    await _open(registry, tmp_path / "alpha", panes=2)
    session = registry.session
    assert session is not None
    await registry.attach(session.terminals[0].name, 80, 24, _noop, _noop_exit)

    card = (await routes.get_workspaces()).workspaces[0]

    assert card.terminals == 2
    assert card.live_terminals == 1


# ----------------------------------------------------------------- switching
async def test_switching_starts_and_stops_nothing(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    first = await _open(registry, tmp_path / "alpha")
    first_id = first["session"]["id"]
    first_pane = registry.get(first_id).terminals[0]
    await registry.attach(first_pane.name, 80, 24, _noop, _noop_exit, first_id)
    first_pty = first_pane.pty_id
    spawns = len(fake_pty.spawns)

    await _open(registry, tmp_path / "beta")
    result = await routes.activate_workspace(
        _request(), routes.ActivateWorkspaceRequest(id=first_id)
    )

    assert result["active_id"] == first_id
    assert first_pty not in fake_pty.closed, "switching must not stop an agent"
    assert len(fake_pty.spawns) == spawns, "switching must not start one either"
    assert len(registry.sessions) == 2


async def test_the_wizard_state_keeps_every_workspace_open(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """"Add a workspace" clears the front WITHOUT closing what is open.

    This is the state the UI is in while the folder wizard is showing. If it
    closed anything, pressing + and then changing your mind would cost you a
    workspace.
    """
    opened = await _open(registry, tmp_path / "alpha")
    pane = registry.session.terminals[0]
    await registry.attach(pane.name, 80, 24, _noop, _noop_exit)

    result = await routes.activate_workspace(_request(), routes.ActivateWorkspaceRequest(id=None))

    assert result["active_id"] is None
    assert result["state"]["session"] is None, "no workspace is on screen"
    assert len(result["state"]["workspaces"]) == 1, "but it is still open"
    assert pane.pty_id not in fake_pty.closed
    assert opened["session"]["id"] == result["state"]["workspaces"][0]["id"]


async def test_switching_to_a_workspace_that_is_gone_is_a_404(
    registry: Registry, tmp_path: Path
) -> None:
    from fastapi import HTTPException

    await _open(registry, tmp_path / "alpha")
    with pytest.raises(HTTPException) as caught:
        await routes.activate_workspace(
            _request(), routes.ActivateWorkspaceRequest(id="ide_never_existed")
        )
    assert caught.value.status_code == 404


# ------------------------------------------------------------------ closing
async def test_closing_one_workspace_stops_only_its_agents(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    first = await _open(registry, tmp_path / "alpha")
    first_id = first["session"]["id"]
    first_pane = registry.get(first_id).terminals[0]
    await registry.attach(first_pane.name, 80, 24, _noop, _noop_exit, first_id)

    second = await _open(registry, tmp_path / "beta")
    second_id = second["session"]["id"]
    second_pane = registry.get(second_id).terminals[0]
    await registry.attach(second_pane.name, 80, 24, _noop, _noop_exit, second_id)

    result = await routes.close_workspace(_request(), second_id)

    assert result["closed"] == second_id
    assert second_pane.pty_id in fake_pty.closed
    assert first_pane.pty_id not in fake_pty.closed
    assert [w["id"] for w in result["state"]["workspaces"]] == [first_id]
    assert result["state"]["active_id"] == first_id, "the survivor takes the front"


async def test_closing_an_unknown_workspace_is_a_404(
    registry: Registry, tmp_path: Path
) -> None:
    from fastapi import HTTPException

    await _open(registry, tmp_path / "alpha")
    with pytest.raises(HTTPException) as caught:
        await routes.close_workspace(_request(), "ide_never_existed")
    assert caught.value.status_code == 404


async def test_the_plain_close_still_closes_the_front_one(
    registry: Registry, tmp_path: Path
) -> None:
    """The toolbar button and every existing CLI caller keep working."""
    await _open(registry, tmp_path / "alpha")
    second = await _open(registry, tmp_path / "beta")

    result = await routes.end_session(_request())

    assert result["closed"] is True
    remaining = [w["name"] for w in result["state"]["workspaces"]]
    assert remaining == ["alpha"]
    assert second["session"]["id"] not in [
        w["id"] for w in result["state"]["workspaces"]
    ]


# -------------------------------------------------------------------- state
async def test_state_carries_the_bar_and_the_front_workspace_together(
    registry: Registry, tmp_path: Path
) -> None:
    """One fetch answers both questions, so the two can never disagree."""
    await _open(registry, tmp_path / "alpha")
    beta = await _open(registry, tmp_path / "beta")

    state = await routes.get_state()

    assert state["session"]["id"] == beta["session"]["id"]
    assert state["active_id"] == beta["session"]["id"]
    assert [w["name"] for w in state["workspaces"]] == ["alpha", "beta"]
    assert state["max_workspaces"] == session_mod.MAX_WORKSPACES


async def test_opening_the_same_folder_adds_a_distinct_tab(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    first = await _open(registry, tmp_path / "alpha")
    pane = registry.session.terminals[0]
    await registry.attach(pane.name, 80, 24, _noop, _noop_exit)
    await _open(registry, tmp_path / "beta")

    again = await _open(registry, tmp_path / "alpha")

    assert again["session"]["id"] != first["session"]["id"]
    assert pane.pty_id not in fake_pty.closed, "the running agent must survive"
    assert len(again["state"]["workspaces"]) == 3
    assert [space["name"] for space in again["state"]["workspaces"]] == [
        "alpha",
        "beta",
        "alpha 2",
    ]
    assert again["state"]["active_id"] == again["session"]["id"]


async def test_renaming_a_workspace_changes_only_its_tab(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    opened = await _open(registry, tmp_path / "alpha")
    workspace_id = opened["session"]["id"]
    pane = registry.session.terminals[0]
    await registry.attach(pane.name, 80, 24, _noop, _noop_exit)
    pty_id = pane.pty_id

    result = await routes.rename_workspace(
        _request(),
        workspace_id,
        routes.RenameWorkspaceRequest(name="Backend review"),
    )

    assert result["workspace"]["name"] == "Backend review"
    assert result["state"]["workspaces"][0]["name"] == "Backend review"
    assert registry.session.folder == str(tmp_path / "alpha")
    assert pty_id not in fake_pty.closed
