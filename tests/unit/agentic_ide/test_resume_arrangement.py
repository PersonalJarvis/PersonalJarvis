"""Resume must give back the SAME arrangement, not a reshuffled one.

Two separate promises hide in one list today:

* which workspace was **on screen** (so you carry on where you were), and
* the **order the tabs sat in** (so the bar looks like the one you left).

`snapshot()` currently encodes the first by reordering the second — it sorts
the active workspace to the front. Reopen after working in the third tab and
the bar comes back as 3, 1, 2. Nothing is lost, but the arrangement is not the
one you left, and with several workspaces open that reads as "my terminals got
mixed up".

These tests pin both promises, plus the one they depend on: a terminal belongs
to its own workspace and never migrates to a neighbour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as ide


@pytest.fixture
def registry(tmp_path: Path, monkeypatch) -> ide.Registry:
    monkeypatch.setattr(resume_store, "save", lambda snapshot: None)
    monkeypatch.setattr(resume_store, "clear", lambda: True)
    return ide.Registry()


def folder(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


async def open_three(registry: ide.Registry, tmp_path: Path) -> list[str]:
    """Three workspaces in a known tab order, each with its own terminals."""
    ids = []
    for name, crew in (
        ("alpha", ["Ada", "Ben"]),
        ("beta", ["Cleo"]),
        ("gamma", ["Dev", "Eli", "Fay"]),
    ):
        session = await registry.start(
            folder(tmp_path, name),
            [{"agent": "claude", "name": member} for member in crew],
        )
        ids.append(session.id)
    return ids


def tab_folders(registry: ide.Registry) -> list[str]:
    return [Path(card["folder"]).name for card in registry.workspaces()]


def crew_of(session: ide.Session) -> list[str]:
    return [terminal.name for terminal in session.terminals]


# ---------------------------------------------------------------------------
# What the snapshot records
# ---------------------------------------------------------------------------


async def test_the_snapshot_keeps_the_tab_order(registry, tmp_path):
    await open_three(registry, tmp_path)
    order_on_screen = tab_folders(registry)

    snapshot = registry.snapshot()

    assert [Path(w.folder).name for w in snapshot.workspaces] == order_on_screen


async def test_the_snapshot_keeps_the_tab_order_after_switching_tabs(registry, tmp_path):
    ids = await open_three(registry, tmp_path)
    order_on_screen = tab_folders(registry)
    # Work in the middle tab for a while — that must not renumber the bar.
    await registry.activate(ids[1])

    snapshot = registry.snapshot()

    assert [Path(w.folder).name for w in snapshot.workspaces] == order_on_screen


async def test_the_snapshot_records_which_workspace_was_on_screen(registry, tmp_path):
    ids = await open_three(registry, tmp_path)
    await registry.activate(ids[2])

    snapshot = registry.snapshot()

    assert snapshot.active_session_id == ids[2]


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------


async def test_resume_rebuilds_the_bar_in_the_same_order(registry, tmp_path):
    ids = await open_three(registry, tmp_path)
    await registry.activate(ids[1])
    snapshot = registry.snapshot()
    for workspace_id in list(ids):
        await registry.end(workspace_id)

    await registry.restore(snapshot)

    assert tab_folders(registry) == ["alpha", "beta", "gamma"]


async def test_resume_puts_you_back_in_the_workspace_you_were_using(registry, tmp_path):
    ids = await open_three(registry, tmp_path)
    await registry.activate(ids[1])
    snapshot = registry.snapshot()
    for workspace_id in list(ids):
        await registry.end(workspace_id)

    result = await registry.restore(snapshot)

    assert Path(registry.session.folder).name == "beta"
    assert len(result.sessions) == 3


async def test_terminals_stay_with_their_own_workspace(registry, tmp_path):
    await open_three(registry, tmp_path)
    snapshot = registry.snapshot()
    for card in list(registry.workspaces()):
        await registry.end(card["id"])

    await registry.restore(snapshot)

    by_folder = {Path(session.folder).name: crew_of(session) for session in registry.sessions}
    assert by_folder == {
        "alpha": ["Ada", "Ben"],
        "beta": ["Cleo"],
        "gamma": ["Dev", "Eli", "Fay"],
    }


async def test_terminal_order_inside_a_workspace_survives(registry, tmp_path):
    await open_three(registry, tmp_path)
    snapshot = registry.snapshot()
    for card in list(registry.workspaces()):
        await registry.end(card["id"])

    await registry.restore(snapshot)

    gamma = next(s for s in registry.sessions if Path(s.folder).name == "gamma")
    assert crew_of(gamma) == ["Dev", "Eli", "Fay"]


async def test_grid_coordinates_survive_per_workspace(registry, tmp_path):
    await open_three(registry, tmp_path)
    before = {
        Path(s.folder).name: [(t.name, t.column, t.slot) for t in s.terminals]
        for s in registry.sessions
    }
    snapshot = registry.snapshot()
    for card in list(registry.workspaces()):
        await registry.end(card["id"])

    await registry.restore(snapshot)

    after = {
        Path(s.folder).name: [(t.name, t.column, t.slot) for t in s.terminals]
        for s in registry.sessions
    }
    assert after == before


# ---------------------------------------------------------------------------
# Back-compat: a snapshot written before the active id existed
# ---------------------------------------------------------------------------


async def test_the_arrangement_survives_a_real_restart(tmp_path, monkeypatch):
    """The tests above hold the snapshot in memory. A restart does not: it
    reads the file back, so anything the on-disk format drops is lost exactly
    where the user would notice it — the morning after."""
    store = tmp_path / "store" / "last_session.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(resume_store, "_store_path", lambda: store)

    live = ide.Registry()
    ids = await open_three(live, tmp_path)
    await live.activate(ids[1])
    resume_store.save(live.snapshot())

    # A fresh process: nothing in memory, everything from the file.
    reloaded = resume_store.load()
    assert reloaded is not None
    after_restart = ide.Registry()
    await after_restart.restore(reloaded)

    assert tab_folders(after_restart) == ["alpha", "beta", "gamma"]
    assert Path(after_restart.session.folder).name == "beta"
    assert {Path(s.folder).name: crew_of(s) for s in after_restart.sessions} == {
        "alpha": ["Ada", "Ben"],
        "beta": ["Cleo"],
        "gamma": ["Dev", "Eli", "Fay"],
    }


async def test_a_snapshot_without_an_active_id_focuses_the_first(registry, tmp_path):
    await open_three(registry, tmp_path)
    snapshot = registry.snapshot()
    # What a snapshot written before the field existed looks like.
    snapshot.active_session_id = ""
    for card in list(registry.workspaces()):
        await registry.end(card["id"])

    await registry.restore(snapshot)

    # The old contract said "first in the list is the one on screen"; a stored
    # snapshot from that era must still land somewhere sensible.
    assert Path(registry.session.folder).name == "alpha"
