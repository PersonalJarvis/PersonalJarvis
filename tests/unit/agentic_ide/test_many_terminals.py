"""What holds — and what does not — once a workspace has far more than 12 panes.

The pane cap was 12 and is now 100 per workspace, while workspace count itself
is unrestricted. Twelve panes was small enough that several things could get
away with being O(n) in disguise or quietly bounded; a hundred is not. These
tests pin the parts that must scale and name the ones that are deliberately
bounded, so raising the pane cap again is a measurement rather than a hope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from jarvis.agentic_ide.names import default_names
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> ide.Registry:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return ide.Registry(pty_manager=FakePtyManager())


# ----------------------------------------------------------------- call-signs
def test_the_call_sign_pool_covers_one_full_workspace() -> None:
    """Every pane in one workspace needs its own speakable name.

    Names are scoped to the front workspace, so other tabs may reuse them
    without ambiguity. The pool therefore only has to cover the per-workspace
    pane ceiling, regardless of how many workspaces are open.
    """
    pool = default_names(ide.MAX_TERMINALS)
    assert len(pool) == ide.MAX_TERMINALS
    assert len(set(pool)) == ide.MAX_TERMINALS, "a repeated call-sign is ambiguous"


async def test_opening_a_hundred_panes_gives_each_a_distinct_name(
    registry: ide.Registry, tmp_path: Path
) -> None:
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"} for _ in range(ide.MAX_TERMINALS)]
    )
    names = [t.name for t in session.terminals]
    keys = [t.key for t in session.terminals]
    assert len(set(names)) == ide.MAX_TERMINALS
    assert len(set(keys)) == ide.MAX_TERMINALS
    assert "" not in keys


async def test_a_hundred_panes_can_each_be_found_by_name(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Voice reaches a pane by its call-sign; that must not degrade with size."""
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"} for _ in range(ide.MAX_TERMINALS)]
    )
    for term in session.terminals:
        assert session.find(term.name) is term
        assert session.find(term.key) is term


# --------------------------------------------------------------------- layout
async def test_the_grid_stays_gapless_at_a_hundred_panes(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """A hole in the coordinates renders as a blank stripe in the UI."""
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"} for _ in range(ide.MAX_TERMINALS)]
    )
    columns = sorted({t.column for t in session.terminals})
    assert columns == list(range(len(columns))), "column numbers must be packed"
    for column in columns:
        slots = sorted(t.slot for t in session.terminals if t.column == column)
        assert slots == list(range(len(slots))), "slots must be packed per column"
    assert [t.index for t in session.terminals] == list(range(ide.MAX_TERMINALS))


async def test_closing_the_middle_of_a_large_workspace_repacks_it(
    registry: ide.Registry, tmp_path: Path
) -> None:
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"} for _ in range(40)]
    )
    victim = session.terminals[17].name
    await registry.close_terminal(victim)

    assert len(session.terminals) == 39
    columns = sorted({t.column for t in session.terminals})
    assert columns == list(range(len(columns)))
    assert [t.index for t in session.terminals] == list(range(39))


async def test_the_cap_is_enforced_rather_than_exceeded(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Whatever the cap is, it has to be the real ceiling."""
    await registry.start(
        str(tmp_path), [{"agent": "claude"} for _ in range(ide.MAX_TERMINALS)]
    )
    with pytest.raises(ide.SessionError, match="maximum"):
        await registry.add_terminal()


async def test_a_batch_past_the_cap_reports_being_capped(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Asking for more than fits is a partial success the caller must surface."""
    await registry.start(
        str(tmp_path), [{"agent": "claude"} for _ in range(ide.MAX_TERMINALS - 3)]
    )
    created, capped = await registry.add_terminals(10)
    assert len(created) == 3
    assert capped is True


# ------------------------------------------------------------------- resuming
async def test_a_hundred_panes_survive_the_snapshot_round_trip(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """The restore point has to carry a full workspace, not a readable prefix."""
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"} for _ in range(ide.MAX_TERMINALS)]
    )
    expected = [(t.name, t.column, t.slot) for t in session.terminals]

    loaded = resume_store.load()
    assert loaded is not None
    assert loaded.terminal_count == ide.MAX_TERMINALS
    stored = [
        (t.name, t.column, t.slot) for t in loaded.workspaces[0].terminals
    ]
    assert stored == expected


async def test_restoring_a_hundred_panes_keeps_every_position(
    registry: ide.Registry, tmp_path: Path
) -> None:
    panes = [
        resume_store.SnapshotTerminal(
            key=f"t{i}",
            name=f"T{i}",
            agent="claude",
            column=i // 5,
            slot=i % 5,
            resume=ResumeHandle(
                kind="claude_session", id=f"conv-{i}", captured_at=1.0
            ),
        )
        for i in range(ide.MAX_TERMINALS)
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
    assert len(session.terminals) == ide.MAX_TERMINALS
    assert [(t.column, t.slot) for t in session.terminals] == [
        (i // 5, i % 5) for i in range(ide.MAX_TERMINALS)
    ]
    # Every pane keeps its own conversation — no two share a handle.
    ids = [t.resume.id for t in session.terminals if t.resume]
    assert len(set(ids)) == ide.MAX_TERMINALS


async def test_a_full_house_across_every_workspace_round_trips(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """The absolute worst case: every workspace open, each one full.

    Deliberately smaller than MAX_TERMINALS per workspace so the test stays
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
    for index in range(workspace_count):
        folder = tmp_path / f"repo{index}"
        folder.mkdir()
        folders.append(folder)
        await registry.start(
            str(folder), [{"agent": "claude"} for _ in range(20)]
        )

    loaded = resume_store.load()
    assert loaded is not None
    assert len(loaded.workspaces) == workspace_count
    assert loaded.terminal_count == 20 * workspace_count
    for workspace in loaded.workspaces:
        names = [t.name for t in workspace.terminals]
        assert names == default_names(20), "each workspace numbers its own panes"


async def test_the_snapshot_of_a_full_house_stays_a_sane_size(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """It is read on the first screen of the IDE, so it must stay small."""
    await registry.start(
        str(tmp_path), [{"agent": "claude"} for _ in range(ide.MAX_TERMINALS)]
    )
    raw = resume_store._store_path().read_text(encoding="utf-8")
    # A hundred panes of metadata, not a transcript: comfortably under 100 KB.
    assert len(raw) < 100_000
    # And still valid JSON rather than something truncated.
    assert json.loads(raw)["workspaces"][0]["terminals"]
