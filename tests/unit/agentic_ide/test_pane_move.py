"""Rearranging panes that already exist — the drag-and-drop half of the grid.

`add_terminal` decides where a NEW pane lands; this decides where a running one
moves to, in the same two axes (columns of stacked panes). The arithmetic is
pinned here because every failure mode is silent on screen: a pane that lands
one column off looks like the grid ignored the drop, and a pane that collides
with another leaves a blank stripe where an agent should be.

Nothing here starts or stops an agent, which is the whole reason rearranging is
offered at all — the last test says so explicitly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry, SessionError
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


async def _open(registry: Registry, folder: Path, count: int = 2):
    return await registry.start(str(folder), [{"agent": "claude"} for _ in range(count)])


async def _open_in_a_row(registry: Registry, folder: Path, count: int):
    """``count`` panes side by side — one column each, every slot 0.

    The starting arrangement of nearly every test here, and stated rather than
    inherited: a wizard workspace OPENS as columns of two now
    (``WIZARD_COLUMN_HEIGHT``), and these tests are about what a drop does to an
    arrangement, not about which one the wizard produces. Built the way a user
    builds a row — open one pane, split it rightwards.
    """
    session = await _open(registry, folder, 1)
    for _ in range(max(0, count - 1)):
        await registry.add_terminal(direction="right")
    return session


def _layout(registry: Registry) -> list[tuple[str, int, int]]:
    """(name, column, slot) in render order — the shape the grid draws."""
    return [(t.name, t.column, t.slot) for t in registry.session.terminals]


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


# ----------------------------------------------------------------------- swap
async def test_swap_exchanges_two_panes_and_keeps_the_shape(
    registry: Registry, tmp_path: Path
) -> None:
    """The move a user reaches for most: these two are the wrong way round."""
    await _open_in_a_row(registry, tmp_path, 3)
    await registry.move_terminal("T1", target="T3", position="swap")
    assert _layout(registry) == [("T3", 0, 0), ("T2", 1, 0), ("T1", 2, 0)]


async def test_swap_works_between_a_stacked_pane_and_a_lone_one(
    registry: Registry, tmp_path: Path
) -> None:
    """Columns of different heights: the two places trade, the stacks do not."""
    await _open_in_a_row(registry, tmp_path, 2)
    lower = await registry.add_terminal(anchor="T1", direction="down")
    assert _layout(registry) == [("T1", 0, 0), (lower.name, 0, 1), ("T2", 1, 0)]

    await registry.move_terminal(lower.name, target="T2", position="swap")
    assert _layout(registry) == [("T1", 0, 0), ("T2", 0, 1), (lower.name, 1, 0)]


# ------------------------------------------------------------ left and right
async def test_moving_right_of_a_pane_puts_it_in_its_own_column(
    registry: Registry, tmp_path: Path
) -> None:
    await _open_in_a_row(registry, tmp_path, 3)
    await registry.move_terminal("T1", target="T2", position="right")
    assert _layout(registry) == [("T2", 0, 0), ("T1", 1, 0), ("T3", 2, 0)]


async def test_moving_left_of_a_pane_shifts_that_column_over(
    registry: Registry, tmp_path: Path
) -> None:
    await _open_in_a_row(registry, tmp_path, 3)
    await registry.move_terminal("T3", target="T2", position="left")
    assert _layout(registry) == [("T1", 0, 0), ("T3", 1, 0), ("T2", 2, 0)]


async def test_moving_to_the_side_it_is_already_on_changes_nothing(
    registry: Registry, tmp_path: Path
) -> None:
    """The no-op drag must not walk a pane one column further every time."""
    await _open_in_a_row(registry, tmp_path, 3)
    await registry.move_terminal("T1", target="T2", position="left")
    assert _layout(registry) == [("T1", 0, 0), ("T2", 1, 0), ("T3", 2, 0)]


async def test_pulling_a_stacked_pane_out_into_its_own_column(
    registry: Registry, tmp_path: Path
) -> None:
    """The way back out of a stack — otherwise a split down is a one-way door."""
    await _open(registry, tmp_path, 1)
    lower = await registry.add_terminal(anchor="T1", direction="down")
    await registry.move_terminal(lower.name, target="T1", position="right")
    assert _layout(registry) == [("T1", 0, 0), (lower.name, 1, 0)]


# ----------------------------------------------------------- above and below
async def test_moving_below_a_pane_joins_its_column(
    registry: Registry, tmp_path: Path
) -> None:
    """Only the target's column moves; every other column keeps its height."""
    await _open_in_a_row(registry, tmp_path, 3)
    await registry.move_terminal("T3", target="T1", position="below")
    assert _layout(registry) == [("T1", 0, 0), ("T3", 0, 1), ("T2", 1, 0)]


async def test_moving_above_a_pane_pushes_its_stack_down(
    registry: Registry, tmp_path: Path
) -> None:
    await _open_in_a_row(registry, tmp_path, 2)
    lower = await registry.add_terminal(anchor="T1", direction="down")
    await registry.move_terminal("T2", target=lower.name, position="above")
    assert _layout(registry) == [
        ("T1", 0, 0),
        ("T2", 0, 1),
        (lower.name, 0, 2),
    ]


async def test_reordering_inside_one_column(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, 1)
    middle = await registry.add_terminal(anchor="T1", direction="down")
    bottom = await registry.add_terminal(anchor=middle.name, direction="down")

    await registry.move_terminal("T1", target=bottom.name, position="below")
    assert _layout(registry) == [
        (middle.name, 0, 0),
        (bottom.name, 0, 1),
        ("T1", 0, 2),
    ]


async def test_emptying_a_column_repacks_the_grid(
    registry: Registry, tmp_path: Path
) -> None:
    """A vacated column would otherwise render as a blank stripe."""
    await _open_in_a_row(registry, tmp_path, 3)
    await registry.move_terminal("T2", target="T3", position="below")
    assert _layout(registry) == [("T1", 0, 0), ("T3", 1, 0), ("T2", 1, 1)]


# --------------------------------------------------------------- refusals
async def test_dropping_a_pane_on_itself_changes_nothing(
    registry: Registry, tmp_path: Path
) -> None:
    """A cancelled gesture, not an error — it must not raise at the user."""
    await _open_in_a_row(registry, tmp_path, 2)
    await registry.move_terminal("T1", target="T1", position="right")
    assert _layout(registry) == [("T1", 0, 0), ("T2", 1, 0)]


async def test_an_unknown_pane_names_the_running_ones(
    registry: Registry, tmp_path: Path
) -> None:
    await _open_in_a_row(registry, tmp_path, 2)
    with pytest.raises(SessionError, match="T1, T2"):
        await registry.move_terminal("Gandalf", target="T1", position="swap")


async def test_an_unknown_target_is_refused(registry: Registry, tmp_path: Path) -> None:
    await _open_in_a_row(registry, tmp_path, 2)
    with pytest.raises(SessionError, match="Gandalf"):
        await registry.move_terminal("T1", target="Gandalf", position="swap")


async def test_an_impossible_position_is_refused(
    registry: Registry, tmp_path: Path
) -> None:
    await _open_in_a_row(registry, tmp_path, 2)
    with pytest.raises(SessionError, match="Position must be"):
        await registry.move_terminal("T1", target="T2", position="diagonally")


async def test_moving_without_a_workspace_is_refused(registry: Registry) -> None:
    with pytest.raises(SessionError, match="No Agentic-IDE session"):
        await registry.move_terminal("T1", target="T2", position="swap")


# ------------------------------------------------------------------- safety
async def test_moving_a_pane_leaves_its_agent_running(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The whole premise: rearranging is not a close-and-reopen."""
    await _open_in_a_row(registry, tmp_path, 2)
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    pty_id = registry.session.find("T1").pty_id

    await registry.move_terminal("T1", target="T2", position="right")

    term = registry.session.find("T1")
    assert term.pty_id == pty_id, "the same PTY, so the same live agent"
    assert pty_id not in fake_pty.closed
