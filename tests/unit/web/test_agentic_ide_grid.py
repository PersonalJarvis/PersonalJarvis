"""Grid geometry of the Agentic-IDE workspace: where a split puts a pane.

The workspace is a left-to-right list of columns, each a top-to-bottom stack.
The distinction these tests defend is that "split down" affects the anchor's
column ONLY. The earlier one-axis model could not express that — a downward
split opened a window-wide row, so splitting one pane halved the height of
every other pane in the workspace.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry


@pytest.fixture(autouse=True)
def _agents_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend both coding CLIs are on PATH — this suite tests geometry only.

    Without it the fixture depends on what the developer happens to have
    installed, which is exactly the maintainer-config trap (AP-23).
    """
    monkeypatch.setattr(session_mod, "agent_argv", lambda agent: ("fake", agent))


async def _workspace(tmp_path, panes: int = 3) -> Registry:
    registry = Registry()
    await registry.start(
        str(tmp_path),
        [{"agent": "claude"} for _ in range(panes)],
    )
    return registry


async def _row(tmp_path, panes: int = 3) -> Registry:
    """``panes`` side by side — one column each, every slot 0.

    A workspace no longer OPENS in this shape: the wizard fills columns two
    deep (``WIZARD_COLUMN_HEIGHT``). So a row is built the way a user builds
    one, by splitting rightwards, and the split tests below say out loud which
    arrangement they are splitting rather than inheriting whichever one the
    wizard happens to produce.
    """
    registry = await _workspace(tmp_path, 1)
    for _ in range(max(0, panes - 1)):
        await registry.add_terminal(direction="right")
    return registry


def grid(registry: Registry) -> list[tuple[str, int, int]]:
    """The workspace as (name, column, slot), in the order the UI renders it."""
    session = registry.session
    assert session is not None
    return [(t.name, t.column, t.slot) for t in session.terminals]


async def test_wizard_opens_columns_of_two(tmp_path) -> None:
    """Four terminals are two columns of two, not four columns of one.

    The workspace is one screenful, so a row of columns divides the window
    between every pane: six terminals left each about 410 px on the
    maintainer's display, under the width their agent needs to draw in, so
    every pane was clipped at its tile edge and the six read as overlapping
    one another (2026-08-11). Filling columns two deep halves the column count
    and so doubles each pane's width.
    """
    registry = await _workspace(tmp_path, 4)
    assert [(c, s) for _, c, s in grid(registry)] == [(0, 0), (0, 1), (1, 0), (1, 1)]


async def test_wizard_stands_an_odd_pane_in_a_column_of_its_own(tmp_path) -> None:
    registry = await _workspace(tmp_path, 3)
    assert [(c, s) for _, c, s in grid(registry)] == [(0, 0), (0, 1), (1, 0)]


async def test_split_down_stacks_inside_the_anchors_own_column(tmp_path) -> None:
    registry = await _row(tmp_path, 3)
    names = [name for name, _, _ in grid(registry)]
    await registry.add_terminal(anchor=names[1], direction="down")

    placed = grid(registry)
    # The middle column now holds two panes; the neighbours keep their own
    # column to themselves and are NOT pushed into a shorter row.
    assert [(c, s) for _, c, s in placed] == [(0, 0), (1, 0), (1, 1), (2, 0)]
    assert placed[1][1] == placed[2][1]  # anchor and new pane share a column


async def test_split_right_opens_a_column_beside_the_anchor(tmp_path) -> None:
    registry = await _row(tmp_path, 3)
    names = [name for name, _, _ in grid(registry)]
    added = await registry.add_terminal(anchor=names[0], direction="right")

    placed = grid(registry)
    assert [(c, s) for _, c, s in placed] == [(0, 0), (1, 0), (2, 0), (3, 0)]
    # It landed directly right of the anchor, pushing the rest one column over.
    assert placed[1][0] == added.name
    assert added.column == 1
    assert added.slot == 0


async def test_a_stacked_column_survives_a_split_right_elsewhere(tmp_path) -> None:
    registry = await _row(tmp_path, 2)
    names = [name for name, _, _ in grid(registry)]
    await registry.add_terminal(anchor=names[0], direction="down")
    await registry.add_terminal(anchor=names[1], direction="right")

    # Column 0 keeps its stack of two; the new pane is a column of its own.
    assert [(c, s) for _, c, s in grid(registry)] == [(0, 0), (0, 1), (1, 0), (2, 0)]


async def test_closing_the_top_of_a_stack_packs_the_slots(tmp_path) -> None:
    registry = await _row(tmp_path, 2)
    names = [name for name, _, _ in grid(registry)]
    await registry.add_terminal(anchor=names[0], direction="down")
    await registry.close_terminal(names[0])

    # No hole where the closed pane was: the survivor moves up to slot 0,
    # otherwise the grid renders a blank half-column.
    assert [(c, s) for _, c, s in grid(registry)] == [(0, 0), (1, 0)]


async def test_closing_a_whole_column_packs_the_columns(tmp_path) -> None:
    registry = await _row(tmp_path, 3)
    names = [name for name, _, _ in grid(registry)]
    await registry.close_terminal(names[1])

    assert [(c, s) for _, c, s in grid(registry)] == [(0, 0), (1, 0)]


async def test_terminals_stay_in_reading_order(tmp_path) -> None:
    """Left to right, top to bottom — the order the prompt-bar chips use."""
    registry = await _row(tmp_path, 2)
    first, second = [name for name, _, _ in grid(registry)]
    await registry.add_terminal(anchor=second, direction="down")
    await registry.add_terminal(anchor=first, direction="down")

    placed = grid(registry)
    assert [(c, s) for _, c, s in placed] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert [t.index for t in registry.session.terminals] == [0, 1, 2, 3]


async def test_split_may_name_a_different_agent(tmp_path) -> None:
    """The pane the UI's CLI picker opens: same split, other coding agent."""
    registry = await _workspace(tmp_path, 1)
    names = [name for name, _, _ in grid(registry)]
    added = await registry.add_terminal(anchor=names[0], direction="down", agent="codex")

    assert added.agent == "codex"
    assert added.display_name == "Codex"
    # ...and it landed in the anchor's column, like any other downward split.
    assert (added.column, added.slot) == (0, 1)


async def test_split_inherits_the_anchors_agent_when_none_is_named(tmp_path) -> None:
    registry = await _workspace(tmp_path, 1)
    names = [name for name, _, _ in grid(registry)]
    await registry.add_terminal(anchor=names[0], direction="right", agent="codex")

    session = registry.session
    assert session is not None
    codex_pane = session.terminals[1].name
    inherited = await registry.add_terminal(anchor=codex_pane, direction="down")
    assert inherited.agent == "codex"
