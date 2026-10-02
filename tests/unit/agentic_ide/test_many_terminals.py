"""Large workspaces, independent sessions and bounded persistent metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from jarvis.agentic_ide.names import default_names
from tests.fakes.fake_pty_manager import FakePtyManager

#: A workspace well past the sixteen panes that used to be the ceiling. There
#: is no ceiling any more; this is just "large" for the tests below.
LARGE = 24


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> ide.Registry:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return ide.Registry(pty_manager=FakePtyManager())


# ----------------------------------------------------------------- call-signs
def test_the_call_sign_pool_covers_a_large_workspace() -> None:
    """Every pane in one workspace needs its own speakable name.

    Names are scoped to the front workspace, so other tabs may reuse them
    without ambiguity. The pool grows with the workspace: there is no ceiling.
    """
    pool = default_names(ide.MAX_PANES_PER_REQUEST)
    assert len(pool) == ide.MAX_PANES_PER_REQUEST
    assert len(set(pool)) == ide.MAX_PANES_PER_REQUEST, "a repeated call-sign is ambiguous"


async def test_opening_a_full_workspace_gives_each_a_distinct_name(
    registry: ide.Registry, tmp_path: Path
) -> None:
    session = await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(LARGE)])
    names = [t.name for t in session.terminals]
    keys = [t.key for t in session.terminals]
    assert len(set(names)) == LARGE
    assert len(set(keys)) == LARGE
    assert "" not in keys


async def test_a_full_workspace_can_each_be_found_by_name(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Voice reaches a pane by its call-sign; that must not degrade with size."""
    session = await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(LARGE)])
    for term in session.terminals:
        assert session.find(term.name) is term
        assert session.find(term.key) is term


# --------------------------------------------------------------------- layout
async def test_the_grid_stays_gapless_at_a_full_workspace(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """A hole in the coordinates renders as a blank stripe in the UI."""
    session = await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(LARGE)])
    columns = sorted({t.column for t in session.terminals})
    assert columns == list(range(len(columns))), "column numbers must be packed"
    for column in columns:
        slots = sorted(t.slot for t in session.terminals if t.column == column)
        assert slots == list(range(len(slots))), "slots must be packed per column"
    assert [t.index for t in session.terminals] == list(range(LARGE))


async def test_closing_the_middle_of_a_large_workspace_repacks_it(
    registry: ide.Registry, tmp_path: Path
) -> None:
    session = await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(LARGE)])
    victim = session.terminals[LARGE // 2].name
    await registry.close_terminal(victim)

    assert len(session.terminals) == LARGE - 1
    columns = sorted({t.column for t in session.terminals})
    assert columns == list(range(len(columns)))
    assert [t.index for t in session.terminals] == list(range(LARGE - 1))


async def test_a_workspace_has_no_pane_limit(registry: ide.Registry, tmp_path: Path) -> None:
    """Past the sixteen that used to be the ceiling, one more pane still opens."""
    await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(16)])
    for _ in range(LARGE - 16):
        await registry.add_terminal(agent="claude")
    session = registry.session
    assert session is not None
    assert len(session.terminals) == LARGE
    assert len(set(ide.layout_tree.leaves(session.layout))) == LARGE


async def test_a_batch_is_never_refused_for_the_size_of_the_workspace(
    registry: ide.Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(LARGE)])
    created, capped = await registry.add_terminals(10)
    assert len(created) == 10 and not capped
    assert registry.session is not None
    assert len(registry.session.terminals) == LARGE + 10


async def test_one_request_opens_at_most_the_per_request_guard(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """A misheard "open 500" must not start 500 processes in one go."""
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    with pytest.raises(ide.SessionError, match="in one go"):
        await registry.add_terminals(ide.MAX_PANES_PER_REQUEST + 1)
    assert registry.session is not None
    assert len(registry.session.terminals) == 1
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ide.SessionError, match="in one go"):
        await registry.start(
            str(other), [{"agent": "claude"} for _ in range(ide.MAX_PANES_PER_REQUEST + 1)]
        )


# ------------------------------------------------------------------- resuming
async def test_a_full_workspace_survive_the_snapshot_round_trip(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """The restore point has to carry a full workspace, not a readable prefix."""
    session = await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(LARGE)])
    expected = [(t.name, t.column, t.slot) for t in session.terminals]

    loaded = resume_store.load()
    assert loaded is not None
    assert loaded.terminal_count == LARGE
    stored = [(t.name, t.column, t.slot) for t in loaded.workspaces[0].terminals]
    assert stored == expected


async def test_restoring_a_full_workspace_keeps_every_position(
    registry: ide.Registry, tmp_path: Path
) -> None:
    panes = [
        resume_store.SnapshotTerminal(
            key=f"t{i}",
            name=f"T{i}",
            agent="claude",
            column=i % 4,
            slot=i // 4,
            resume=ResumeHandle(kind="claude_session", id=f"conv-{i}", captured_at=1.0),
        )
        for i in range(LARGE)
    ]
    snapshot = resume_store.Snapshot(
        saved_at=1.0,
        workspaces=[
            resume_store.SnapshotWorkspace(
                session_id="ide_big", folder=str(tmp_path), terminals=panes
            )
        ],
    )

    result = await registry.restore(snapshot)
    session = result.sessions[0]
    assert len(session.terminals) == LARGE
    # Every pane sits exactly where it sat — four columns of six — read
    # back across the bands rather than down the columns, because a grid
    # stands on its rows once it is restored (`layout_tree.rows_outermost`).
    across = 4
    assert [(t.column, t.slot) for t in session.terminals] == [
        (i % across, i // across) for i in range(LARGE)
    ]
    # Every pane keeps its own conversation — no two share a handle.
    ids = [t.resume.id for t in session.terminals if t.resume]
    assert len(set(ids)) == LARGE


async def test_a_full_house_across_every_workspace_round_trips(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """The absolute worst case: every workspace open, each one full.

    Sixteen panes per workspace so the test stays
    fast — what is being pinned is that nothing collapses when several large
    workspaces are remembered at once, including every workspace numbering its
    panes T1..Tn on its own.

    Call-signs REPEAT across workspaces on purpose: a position is what the user
    reads off the screen, and only one workspace is on screen at a time. A
    second tab starting at T21 would keep names globally unique at the price of
    the whole point of numbering them.
    """
    folders = []
    workspace_count = 16
    per_workspace = 16
    for index in range(workspace_count):
        folder = tmp_path / f"repo{index}"
        folder.mkdir()
        folders.append(folder)
        await registry.start(str(folder), [{"agent": "claude"} for _ in range(per_workspace)])

    loaded = resume_store.load()
    assert loaded is not None
    assert len(loaded.workspaces) == workspace_count
    assert loaded.terminal_count == per_workspace * workspace_count
    for workspace in loaded.workspaces:
        names = [t.name for t in workspace.terminals]
        assert names == default_names(per_workspace), "each workspace numbers its own panes"


async def test_the_snapshot_of_a_full_house_stays_a_sane_size(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """It is read on the first screen of the IDE, so it must stay small."""
    await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(LARGE)])
    raw = resume_store._store_path().read_text(encoding="utf-8")
    # Eight panes of metadata, not a transcript: comfortably under 100 KB.
    assert len(raw) < 100_000
    # And still valid JSON rather than something truncated.
    assert json.loads(raw)["workspaces"][0]["terminals"]


# ------------------------------------------------------------- grid shape
@pytest.mark.parametrize(
    ("count", "columns", "rows"),
    [
        (1, 1, 1),
        (2, 2, 1),
        (6, 3, 2),
        (8, 4, 2),
        (9, 3, 3),
        (12, 4, 3),
        (16, 4, 4),
        (17, 5, 4),
        (20, 5, 4),
        (25, 5, 5),
        (30, 6, 5),
        (100, 10, 10),
    ],
)
def test_an_even_grid_grows_square_past_sixteen(count: int, columns: int, rows: int) -> None:
    """Four wide up to sixteen panes, then about square — never a limit.

    Same table as the frontend's ``workspaceDocking.test.ts``.
    """
    assert ide.balanced_columns(count) == columns
    assert -(-count // columns) == rows


async def test_reordering_a_large_workspace_deals_the_even_grid(
    registry: ide.Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(LARGE)])
    session = registry.session
    assert session is not None
    await registry.reorder_terminals(session.id, [term.history_id for term in session.terminals])
    assert ide.layout_tree.grid_span(session.layout) == (5, 5)
    await registry.add_terminal(agent="claude")
    assert len(ide.layout_tree.leaves(session.layout)) == LARGE + 1


@pytest.mark.parametrize(("count", "direction"), [(15, "down"), (16, "right"), (16, "down")])
async def test_a_split_is_kept_where_it_was_put_at_any_size(
    registry: ide.Registry, tmp_path: Path, count: int, direction: str
) -> None:
    """No grid bound: a split past four columns or rows is not re-dealt."""
    await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(count)])
    session = registry.session
    assert session is not None
    anchor = session.terminals[0]
    before = ide.layout_tree.leaves(session.layout)
    added = await registry.add_terminal(anchor=anchor.name, agent="claude", direction=direction)
    after = ide.layout_tree.leaves(session.layout)
    # The new pane went right beside its anchor; every other pane kept its order.
    assert after.index(added.key) == after.index(anchor.key) + 1
    assert [key for key in after if key != added.key] == before
    columns, rows = ide.layout_tree.grid_span(session.layout)
    assert (columns if direction == "right" else rows) == 5, "the grid grew past four"


async def test_an_unanchored_add_never_reshuffles_a_hand_built_layout(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Four columns wide, one more pane splits the largest pane; nobody else moves."""
    await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(8)])
    session = registry.session
    assert session is not None
    assert ide.layout_tree.grid_span(session.layout) == (4, 2)
    # Shape it by hand: one pane split in two.
    await registry.add_terminal(anchor=session.terminals[1].name, agent="claude", direction="down")
    order_before = ide.layout_tree.leaves(session.layout)
    added = await registry.add_terminal(agent="claude")
    order_after = ide.layout_tree.leaves(session.layout)
    assert [key for key in order_after if key != added.key] == order_before
    assert ide.layout_tree.grid_span(session.layout)[0] == 4, "no fifth column was appended"


def _unanchored(tree: ide.layout_tree.LayoutNode | None, added: str):
    return ide.layout_tree.add_unanchored(
        tree, added, max_columns=4, balanced_columns=ide.balanced_columns
    )


def test_an_unanchored_add_appends_a_column_while_the_workspace_is_narrow() -> None:
    grown = _unanchored(ide.layout_tree.from_grid([("a", 0, 0), ("b", 1, 0)]), "c")
    assert ide.layout_tree.grid_span(grown) == (3, 1)
    assert ide.layout_tree.leaves(grown) == ["a", "b", "c"]


def test_an_unanchored_add_deals_a_shape_nobody_arranged_into_the_even_grid() -> None:
    row = ide.layout_tree.from_grid([(key, column, 0) for column, key in enumerate("abcd")])
    grown = _unanchored(row, "e")
    assert ide.layout_tree.grid_span(grown) == (3, 2)
    assert ide.layout_tree.leaves(grown) == ["a", "b", "c", "d", "e"]


def test_an_unanchored_add_splits_the_largest_pane_of_a_hand_built_layout() -> None:
    tree = ide.layout_tree.from_grid(
        [("a", 0, 0), ("b", 1, 0), ("c", 1, 1), ("d", 2, 0), ("e", 3, 0)]
    )
    grown = _unanchored(tree, "f")
    # "a", "d" and "e" are equally large full-height columns; "e" is last in
    # reading order. A column a quarter wide is taller than wide, so "f" goes
    # beneath it and lands at the end of the reading order.
    boxes = ide.layout_tree.pane_boxes(grown)
    assert boxes["f"][0] == pytest.approx(boxes["e"][0])
    assert boxes["f"][1] > boxes["e"][1]
    assert ide.layout_tree.grid_span(grown)[0] == 4
    assert ide.layout_tree.leaves(grown) == ["a", "b", "c", "d", "e", "f"]
