"""Straightening a workspace into even, full-width rows.

The complaint this answers arrived as a screenshot on 2026-08-14: two panes
across the top of the workspace, and the seam between them sitting two thirds
of the way across rather than in the middle — lined up with the seam of the
BOTTOM row instead. Nothing was dragged there. The split tree makes every
split local (that is the point of it, see ``layout_tree``), so a pane whose
branch was split again below it ends up spanning both halves, and its own seam
is inherited from a pane it has nothing to do with.

"Even them out" cannot fix that and must not try: it levels sizes and is
contractually forbidden from rearranging (`test_pane_move.py` guards the
neighbouring promise). By its rule the reported workspace already IS even — a
pane above two panes is honestly worth two shares. So the repair is a second,
admitted rearrangement, and this file pins it:

* the row split (five panes are two above three, never three above two),
* the order panes are dealt in — what the SCREEN reads, not what the tree
  walks, or a straightening would teleport panes across the workspace,
* and the property that makes it safe on a wall of working agents: no PTY is
  touched, no pane is created or destroyed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import layout_tree as lt
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry, SessionError
from jarvis.ui.web import agentic_ide_routes
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
    return Registry(pty_manager=fake_pty)


def _stack(*panes: str, weights: list[float] | None = None) -> lt.Split:
    return lt.Split(
        direction="column",
        children=[lt.Leaf(pane=pane) for pane in panes],
        weights=weights or [1.0] * len(panes),
    )


def _row(*children: lt.LayoutNode, weights: list[float] | None = None) -> lt.Split:
    return lt.Split(
        direction="row",
        children=list(children),
        weights=weights or [1.0] * len(children),
    )


def _reported() -> lt.Split:
    """The workspace from the screenshot, weights and all.

    ``row[ column[T1, row[T2, T5]], column[T3, T4] ]`` — T1 alone above two
    panes, so its branch is two panes wide and takes two thirds of the window.
    The weights are exactly what "even them out" produces for this shape, which
    is why the button was no help: by its own rule this is already level.
    """
    return _row(
        _stack_with_row(),
        _stack("t3", "t4"),
        weights=[2.0, 1.0],
    )


def _stack_with_row() -> lt.Split:
    return lt.Split(
        direction="column",
        children=[lt.Leaf(pane="t1"), _row(lt.Leaf(pane="t2"), lt.Leaf(pane="t5"))],
        weights=[1.0, 1.0],
    )


# ------------------------------------------------------------- reading order
def test_panes_are_dealt_in_the_order_the_screen_reads_them() -> None:
    """Top to bottom, left to right — never the tree's own walk.

    ``leaves()`` reports t1, t2, t5, t3, t4 for the reported workspace: it goes
    all the way down the left branch before it ever reaches the top-right pane.
    Straightening by that order would drop T2 into the top row and send T3 to
    the bottom — two panes swapping ends of the workspace for a change the user
    asked to be purely cosmetic.
    """
    assert lt.leaves(_reported()) == ["t1", "t2", "t5", "t3", "t4"]
    assert lt.visual_order(_reported()) == ["t1", "t3", "t2", "t5", "t4"]


def test_panes_starting_on_one_line_are_ordered_by_their_left_edge() -> None:
    """A hairline of float noise must not reorder a row.

    Dragged weights leave sums like 0.4999999 behind, and two panes that
    visibly start on the same line have to read left to right regardless.
    """
    tree = _row(
        _stack("a", "b", weights=[1.0, 1.0]),
        _stack("c", "d", weights=[1.0000001, 0.9999999]),
    )
    assert lt.visual_order(tree) == ["a", "c", "b", "d"]


def test_boxes_are_fractions_that_fill_the_workspace() -> None:
    """The geometry the grid paints, computed server-side.

    Pinned because the row split is dealt from these numbers: a box arithmetic
    that disagreed with the frontend's would straighten a workspace into an
    order nobody on screen can see.
    """
    boxes = lt.pane_boxes(_reported())
    assert boxes["t1"] == lt.Box(x=0.0, y=0.0, w=2 / 3, h=0.5)
    assert boxes["t5"] == lt.Box(x=1 / 3, y=0.5, w=1 / 3, h=0.5)
    assert boxes["t3"] == lt.Box(x=2 / 3, y=0.0, w=1 / 3, h=0.5)


# ----------------------------------------------------------------- the rows
def test_five_panes_are_two_above_three() -> None:
    """The shape the maintainer drew: the short row on top.

    A short row at the top reads as the top of a wall; a short row at the
    bottom reads as an unfinished one. It also keeps the widest row nearest
    the prompt bar, where the pane being typed into usually is.
    """
    assert lt.row_sizes(5, 2) == [2, 3]
    assert lt.row_sizes(7, 2) == [3, 4]
    assert lt.row_sizes(7, 3) == [2, 2, 3]
    assert lt.row_sizes(8, 2) == [4, 4]
    assert lt.row_sizes(1, 3) == [1]


def test_the_reported_workspace_straightens_to_a_centred_top_seam() -> None:
    """The fix itself, on the exact tree from the screenshot.

    Two panes on top of a full-width row means one weight each, which is a seam
    down the middle — where the maintainer drew his line — however many panes
    the row below holds.
    """
    tidy = lt.tidy_tree(_reported(), 2)
    assert lt.to_dict(tidy) == {
        "direction": "column",
        "weights": [1.0, 1.0],
        "children": [
            {
                "direction": "row",
                "weights": [1.0, 1.0],
                "children": [{"pane": "t1"}, {"pane": "t3"}],
            },
            {
                "direction": "row",
                "weights": [1.0, 1.0, 1.0],
                "children": [{"pane": "t2"}, {"pane": "t5"}, {"pane": "t4"}],
            },
        ],
    }


def test_every_row_divides_the_whole_width() -> None:
    """The property the whole change is about, stated as geometry.

    A row of two is halved whatever the row beneath it does — no seam is
    inherited from another row any more.
    """
    boxes = lt.pane_boxes(lt.tidy_tree(_reported(), 2))
    assert boxes["t1"].x == 0.0
    assert boxes["t1"].w == 0.5
    assert boxes["t3"].x == 0.5
    # ...and the row below divides the same width among three.
    assert [round(boxes[pane].w, 6) for pane in ("t2", "t5", "t4")] == [round(1 / 3, 6)] * 3
    # Both rows are the same height, so the workspace reads as rows at all.
    assert boxes["t1"].h == 0.5 and boxes["t2"].h == 0.5


def test_one_row_is_a_plain_row_and_never_a_wrapper() -> None:
    """Canonical form holds: no column with a single child survives."""
    assert lt.to_dict(lt.tidy_tree(_reported(), 1)) == {
        "direction": "row",
        "weights": [1.0] * 5,
        "children": [{"pane": pane} for pane in ("t1", "t3", "t2", "t5", "t4")],
    }


def test_straightening_twice_changes_nothing() -> None:
    """Idempotent, or the button would walk the workspace apart per click."""
    once = lt.tidy_tree(_reported(), 2)
    assert lt.to_dict(lt.tidy_tree(once, 2)) == lt.to_dict(once)


def test_an_empty_workspace_straightens_to_nothing() -> None:
    assert lt.tidy_tree(None, 2) is None


# ------------------------------------------------------------ the session
async def _reported_workspace(registry: Registry, folder: Path) -> None:
    """Build the screenshot's shape the way the user built it — by splitting.

    Not by assigning a tree: the point is that ordinary use of the split
    buttons produces this, which is what makes the straightening worth having.
    """
    await registry.start(str(folder), [{"agent": "claude"}])
    first = registry.session.terminals[0].name
    right = (await registry.add_terminal(anchor=first, direction="right")).name
    below = (await registry.add_terminal(anchor=first, direction="down")).name
    await registry.add_terminal(anchor=right, direction="down")
    await registry.add_terminal(anchor=below, direction="right")


def _shape(registry: Registry) -> dict:
    return lt.to_dict(registry.session.layout)


async def test_the_session_lines_the_reported_workspace_up(
    registry: Registry, tmp_path: Path
) -> None:
    """Five split panes become two full-width rows, in screen order."""
    await _reported_workspace(registry, tmp_path)
    expected = lt.visual_order(registry.session.layout)

    await registry.tidy(2)

    assert _shape(registry) == {
        "direction": "column",
        "weights": [1.0, 1.0],
        "children": [
            {
                "direction": "row",
                "weights": [1.0, 1.0],
                "children": [{"pane": expected[0]}, {"pane": expected[1]}],
            },
            {
                "direction": "row",
                "weights": [1.0, 1.0, 1.0],
                "children": [{"pane": key} for key in expected[2:]],
            },
        ],
    }


async def test_rows_past_the_pane_count_are_clamped_not_refused(
    registry: Registry, tmp_path: Path
) -> None:
    """The caller derives the row count from a measurement; it may overshoot.

    Clamping here keeps that arithmetic out of the browser, where a second copy
    would drift from this one.
    """
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await registry.add_terminal(direction="right")

    await registry.tidy(99)

    assert _shape(registry) == {
        "direction": "column",
        "weights": [1.0, 1.0],
        "children": [{"pane": t.key} for t in registry.session.terminals],
    }


async def test_zero_rows_is_refused(registry: Registry, tmp_path: Path) -> None:
    """Not a shape, and it would divide by zero on the way in."""
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    with pytest.raises(SessionError, match="at least one row"):
        await registry.tidy(0)


async def test_straightening_without_a_workspace_is_refused(registry: Registry) -> None:
    with pytest.raises(SessionError, match="No Agentic-IDE session"):
        await registry.tidy(2)


async def test_straightening_starts_and_stops_no_agent(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """What makes this safe to offer on a wall of working agents.

    It changes where a pane is DRAWN. If it ever reached the pty layer it would
    be resizing working agents' terminals from the backend — geometry the panes
    themselves own and measure.
    """
    await _reported_workspace(registry, tmp_path)
    spawns = len(fake_pty.spawns)
    resizes = len(fake_pty.resizes)
    before = {t.key: t.name for t in registry.session.terminals}

    await registry.tidy(2)

    assert len(fake_pty.spawns) == spawns
    assert len(fake_pty.resizes) == resizes
    # Same panes, same agents, same call-signs — only their boxes moved.
    assert {t.key: t.name for t in registry.session.terminals} == before


# -------------------------------------------------------------- the route
@pytest.fixture
def client() -> TestClient:
    session_mod.reset_registry()
    app = FastAPI()
    app.include_router(agentic_ide_routes.router)
    with TestClient(app) as test_client:
        yield test_client
    session_mod.reset_registry()


def _mounted(tmp_path: Path) -> None:
    """An open workspace in the reported shape, without spawning anything."""
    registry = session_mod.get_registry()
    session = session_mod.Session(
        id="ide_test",
        folder=str(tmp_path),
        name="Test",
        profile=session_mod.probe_project(tmp_path),
        terminals=[
            session_mod.Terminal(
                key=f"t{number}",
                name=f"T{number}",
                agent="claude",
                display_name="Claude Code",
                index=index,
                column=index,
                slot=0,
            )
            for index, number in enumerate((1, 2, 5, 3, 4))
        ],
        created_at=0.0,
        layout=_reported(),
    )
    registry._sessions[session.id] = session  # noqa: SLF001 - no spawn in a unit test
    registry._active = session.id  # noqa: SLF001


def test_the_route_answers_with_the_straightened_workspace(client, tmp_path) -> None:
    """The grid redraws from this answer alone rather than reading again."""
    _mounted(tmp_path)

    reply = client.post("/api/agentic-ide/terminals/tidy", json={"rows": 2})

    assert reply.status_code == 200
    body = reply.json()
    assert body["ok"] is True and body["rows"] == 2
    assert body["state"]["session"]["layout"] == {
        "direction": "column",
        "weights": [1.0, 1.0],
        "children": [
            {
                "direction": "row",
                "weights": [1.0, 1.0],
                "children": [{"pane": "t1"}, {"pane": "t3"}],
            },
            {
                "direction": "row",
                "weights": [1.0, 1.0, 1.0],
                "children": [{"pane": "t2"}, {"pane": "t5"}, {"pane": "t4"}],
            },
        ],
    }
    # The panes are re-listed in the new reading order, so the prompt-bar chips
    # and every "the top-left terminal" consumer follow the straightening.
    assert [t["name"] for t in body["state"]["session"]["terminals"]] == [
        "T1",
        "T3",
        "T2",
        "T5",
        "T4",
    ]


def test_a_row_count_below_one_is_a_bad_request(client, tmp_path) -> None:
    """Refused by the schema, so the browser never has to clamp it."""
    _mounted(tmp_path)
    assert client.post("/api/agentic-ide/terminals/tidy", json={"rows": 0}).status_code == 422


def test_straightening_a_closed_workspace_is_a_conflict(client) -> None:
    """409, not 422: nothing about the request is wrong, the workspace is gone."""
    reply = client.post("/api/agentic-ide/terminals/tidy", json={"rows": 2})
    assert reply.status_code == 409
