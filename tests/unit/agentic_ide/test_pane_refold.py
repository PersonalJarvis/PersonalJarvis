"""Re-folding a whole workspace into columns of a given depth.

`test_pane_move.py` pins what ONE drop does; this pins what the app does on its
own initiative when a row has become too narrow to show the agents in it. The
two share an axis and nothing else: a move is a gesture the user made, a re-fold
is the grid answering a measurement.

Every failure mode here is silent on screen, which is why the arithmetic is
pinned rather than trusted. A fold that loses the reading order shuffles panes
the user knows by position; a fold that is not idempotent walks the workspace
one column further on every measurement; and a fold that touched a PTY would
resize a working agent's terminal for a layout change it has no part in.
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


async def _open_in_a_row(registry: Registry, folder: Path, count: int) -> None:
    """``count`` panes side by side — the shape a re-fold exists to repair.

    Built the way a user builds a row (open one, split it rightwards) rather
    than through the wizard, which opens columns of two of its own accord.
    """
    await registry.start(str(folder), [{"agent": "claude"}])
    for _ in range(max(0, count - 1)):
        await registry.add_terminal(direction="right")


def _layout(registry: Registry) -> list[tuple[str, int, int]]:
    """(name, column, slot) in render order — the shape the grid draws."""
    return [(t.name, t.column, t.slot) for t in registry.session.terminals]


# ------------------------------------------------------------------ the fold
async def test_folds_a_row_of_six_into_three_columns_of_two(
    registry: Registry, tmp_path: Path
) -> None:
    """The reported workspace, at the depth its measurement asks for.

    Six panes in a row at the maintainer's text size were ~410 px each where a
    60-column agent grid needs ~660, so every terminal drew a third of itself
    past its own tile edge (2026-08-11). Three columns of two is twice the
    width, at the same text size.
    """
    await _open_in_a_row(registry, tmp_path, 6)
    await registry.refold(2)
    assert _layout(registry) == [
        ("T1", 0, 0),
        ("T2", 0, 1),
        ("T3", 1, 0),
        ("T4", 1, 1),
        ("T5", 2, 0),
        ("T6", 2, 1),
    ]


async def test_keeps_reading_order_across_the_fold(
    registry: Registry, tmp_path: Path
) -> None:
    """Panes stay in the sequence the user knows them by.

    A fold is not a shuffle: the pane that was fourth from the left is still the
    fourth pane, it has simply moved onto the second row. Losing that is how a
    layout repair turns into "the app moved my agents around".
    """
    await _open_in_a_row(registry, tmp_path, 5)
    before = [name for name, _, _ in _layout(registry)]
    await registry.refold(2)
    assert [name for name, _, _ in _layout(registry)] == before


async def test_an_odd_count_leaves_the_last_column_short(
    registry: Registry, tmp_path: Path
) -> None:
    """Five panes at depth two is two full columns and a lone one.

    The grid gives a short column the same total height (`paneGrid` spans its
    rows), so the odd pane is tall rather than leaving a hole.
    """
    await _open_in_a_row(registry, tmp_path, 5)
    await registry.refold(2)
    assert _layout(registry) == [
        ("T1", 0, 0),
        ("T2", 0, 1),
        ("T3", 1, 0),
        ("T4", 1, 1),
        ("T5", 2, 0),
    ]


async def test_folding_back_to_one_returns_the_single_row(
    registry: Registry, tmp_path: Path
) -> None:
    """The fold is reversible — a wider window undoes it.

    Without this the repair would be a one-way door: plugging in a big display
    would leave the workspace folded for a narrowness that is over.
    """
    await _open_in_a_row(registry, tmp_path, 4)
    await registry.refold(2)
    await registry.refold(1)
    assert _layout(registry) == [("T1", 0, 0), ("T2", 1, 0), ("T3", 2, 0), ("T4", 3, 0)]


# ------------------------------------------------------------- not a ratchet
async def test_refolding_to_the_current_depth_changes_nothing(
    registry: Registry, tmp_path: Path
) -> None:
    """Idempotent, because the grid asks on every measurement.

    Once the shape is right the grid keeps arriving at the same answer, and a
    fold that moved something each time would walk the workspace apart one
    measurement at a time.
    """
    await _open_in_a_row(registry, tmp_path, 6)
    await registry.refold(2)
    settled = _layout(registry)
    await registry.refold(2)
    await registry.refold(2)
    assert _layout(registry) == settled


async def test_a_depth_past_the_pane_count_is_one_column_not_an_error(
    registry: Registry, tmp_path: Path
) -> None:
    """The caller derives the depth from a measurement; it may overshoot.

    Clamping here rather than refusing keeps that arithmetic out of the browser,
    where it would drift from this one.
    """
    await _open_in_a_row(registry, tmp_path, 3)
    await registry.refold(99)
    assert _layout(registry) == [("T1", 0, 0), ("T2", 0, 1), ("T3", 0, 2)]


async def test_a_depth_below_one_is_refused(registry: Registry, tmp_path: Path) -> None:
    """Zero deep is not a shape. It would also divide by zero on the way in."""
    await _open_in_a_row(registry, tmp_path, 2)
    with pytest.raises(SessionError, match="at least 1"):
        await registry.refold(0)


async def test_refolding_without_a_workspace_is_refused(registry: Registry) -> None:
    with pytest.raises(SessionError, match="No Agentic-IDE session"):
        await registry.refold(2)


# ------------------------------------------------------- nothing else moves
async def test_folding_starts_and_stops_no_agent(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The property that makes folding safe to do on the app's own initiative.

    A re-fold changes the two numbers that say where a pane is DRAWN. If it ever
    reached the pty layer it would be spawning or resizing working agents'
    terminals from the backend — geometry the panes themselves own and measure
    — so the pty manager is asserted to have seen nothing at all.
    """
    await _open_in_a_row(registry, tmp_path, 4)
    spawns = len(fake_pty.spawns)
    resizes = len(fake_pty.resizes)
    before = {t.key: t.name for t in registry.session.terminals}

    await registry.refold(2)

    assert len(fake_pty.spawns) == spawns
    assert len(fake_pty.resizes) == resizes
    # Same panes, same agents, same keys — only their coordinates moved.
    assert {t.key: t.name for t in registry.session.terminals} == before
