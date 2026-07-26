"""Adding and closing panes in a running workspace.

The workspace is COLUMNS of stacked panes, which is what makes the two split
buttons mean something: "right" opens a new column beside the anchor, "down" adds
a pane to the anchor's OWN column and leaves every other column at full height.
These tests pin that arithmetic, because an off-by-one renders as a blank stripe
in the grid, or squashes panes that should not have moved.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import MAX_TERMINALS, Registry, SessionError
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return Registry(pty_manager=fake_pty)


async def _open(registry: Registry, folder: Path, count: int = 2):
    return await registry.start(
        str(folder), [{"agent": "claude"} for _ in range(count)]
    )


def _layout(registry: Registry) -> list[tuple[str, int, int]]:
    """(name, column, slot) in render order — the shape the grid draws.

    The workspace is columns of stacked panes: `column` runs left to right,
    `slot` top to bottom inside one column.
    """
    return [(t.name, t.column, t.slot) for t in registry.session.terminals]


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


# --------------------------------------------------------------- initial rows
async def test_a_fresh_workspace_puts_every_pane_in_its_own_column(
    registry: Registry, tmp_path: Path
) -> None:
    """The wizard's panes stand side by side: one column each, top slot."""
    await _open(registry, tmp_path, 3)
    assert _layout(registry) == [("Alex", 0, 0), ("Blake", 1, 0), ("Casey", 2, 0)]


# ---------------------------------------------------------------- split right
async def test_split_right_opens_a_column_next_to_the_anchor(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    term = await registry.add_terminal(anchor="Alex", direction="right")
    # Immediately right of its anchor — not appended at the end — and everything
    # further right shifts over rather than being overwritten.
    assert _layout(registry) == [("Alex", 0, 0), (term.name, 1, 0), ("Blake", 2, 0)]


async def test_split_right_inherits_the_anchor_agent(
    registry: Registry, tmp_path: Path
) -> None:
    """Splitting a Codex pane means "another Codex", not the default agent."""
    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    term = await registry.add_terminal(anchor="Cody", direction="right")
    assert term.agent == "codex"


async def test_an_explicit_agent_wins_over_the_anchor(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 1)
    term = await registry.add_terminal(anchor="Alex", direction="right", agent="codex")
    assert term.agent == "codex"


# ----------------------------------------------------------------- split down
async def test_split_down_stacks_inside_the_anchor_column(
    registry: Registry, tmp_path: Path
) -> None:
    """The point of the column model: splitting one pane must not squash the
    others. Blake keeps its own full-height column."""
    await _open(registry, tmp_path, 2)
    term = await registry.add_terminal(anchor="Alex", direction="down")
    assert (term.column, term.slot) == (0, 1)
    assert _layout(registry) == [("Alex", 0, 0), (term.name, 0, 1), ("Blake", 1, 0)]


async def test_split_down_pushes_the_existing_stack_down(
    registry: Registry, tmp_path: Path
) -> None:
    """Inserting into the middle of a stack must not collide with what is there."""
    await _open(registry, tmp_path, 1)
    bottom = await registry.add_terminal(anchor="Alex", direction="down")
    middle = await registry.add_terminal(anchor="Alex", direction="down")
    slots = {name: (column, slot) for name, column, slot in _layout(registry)}
    assert slots["Alex"] == (0, 0)
    assert slots[middle.name] == (0, 1), "the newest pane sits under its anchor"
    assert slots[bottom.name] == (0, 2), "the older pane moved down"


# --------------------------------------------------------------------- naming
async def test_new_panes_take_the_next_free_call_sign(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)  # Alex, Blake
    term = await registry.add_terminal(direction="right")
    assert term.name == "Casey"


async def test_an_explicit_name_is_deduplicated(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, 1)
    term = await registry.add_terminal(name="Alex", direction="right")
    assert term.name == "Alex 2"


async def test_the_new_pane_starts_pending_and_addressable(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 1)
    term = await registry.add_terminal(direction="down")
    assert term.status == "pending"
    assert registry.session.find(term.name) is term
    assert registry.session.find(term.name.lower()) is term


# --------------------------------------------------------------------- limits
async def test_adding_past_the_limit_is_refused(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, MAX_TERMINALS)
    with pytest.raises(SessionError, match="maximum"):
        await registry.add_terminal(direction="right")


async def test_an_unknown_anchor_is_refused(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, 1)
    with pytest.raises(SessionError, match="Gandalf"):
        await registry.add_terminal(anchor="Gandalf", direction="right")


async def test_a_bad_direction_is_refused(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, 1)
    with pytest.raises(SessionError, match="right.*down"):
        await registry.add_terminal(anchor="Alex", direction="sideways")


async def test_adding_without_a_workspace_is_refused(registry: Registry) -> None:
    with pytest.raises(SessionError, match="No Agentic-IDE session"):
        await registry.add_terminal(direction="right")


async def test_a_missing_agent_binary_is_reported(
    fake_pty: FakePtyManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = Registry(pty_manager=fake_pty)
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: None)
    with pytest.raises(SessionError, match="not installed"):
        await registry.add_terminal(direction="right")


# ---------------------------------------------------------------------- close
async def test_closing_a_pane_stops_its_agent(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    await registry.attach("Alex", 80, 24, _noop, _noop_exit)
    pty_id = registry.session.terminals[0].pty_id
    closed = await registry.close_terminal("Alex")
    assert closed.name == "Alex"
    assert pty_id in fake_pty.closed
    assert [t.name for t in registry.session.terminals] == ["Blake"]


async def test_closing_repacks_the_stack(registry: Registry, tmp_path: Path) -> None:
    """A gap in a stack would render as a blank cell, so slots are re-packed."""
    await _open(registry, tmp_path, 1)
    middle = await registry.add_terminal(anchor="Alex", direction="down")
    bottom = await registry.add_terminal(anchor=middle.name, direction="down")
    assert [slot for _n, _c, slot in _layout(registry)] == [0, 1, 2]

    await registry.close_terminal(middle.name)
    assert _layout(registry) == [("Alex", 0, 0), (bottom.name, 0, 1)]


async def test_closing_a_whole_column_repacks_the_columns(
    registry: Registry, tmp_path: Path
) -> None:
    """An emptied column would render as a blank stripe."""
    await _open(registry, tmp_path, 3)
    await registry.close_terminal("Blake")
    assert _layout(registry) == [("Alex", 0, 0), ("Casey", 1, 0)]


async def test_closing_the_last_pane_leaves_an_empty_workspace(
    registry: Registry, tmp_path: Path
) -> None:
    """Allowed on purpose: the grid then offers to open a fresh terminal."""
    await _open(registry, tmp_path, 1)
    await registry.close_terminal("Alex")
    assert registry.session is not None
    assert registry.session.terminals == []


async def test_closing_an_unknown_pane_names_the_real_ones(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 1)
    with pytest.raises(SessionError, match="Alex"):
        await registry.close_terminal("Gandalf")


async def test_a_closed_pane_refuses_further_prompts(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    await registry.attach("Alex", 80, 24, _noop, _noop_exit)
    await registry.close_terminal("Alex")
    with pytest.raises(SessionError, match="No terminal called"):
        await registry.send_prompt("Alex", "still there?")


async def test_a_reopened_call_sign_is_a_fresh_pane(
    registry: Registry, tmp_path: Path
) -> None:
    """Closing Alex and splitting again must not resurrect the old transcript."""
    await _open(registry, tmp_path, 1)
    await registry.attach("Alex", 80, 24, _noop, _noop_exit)
    registry.session.terminals[0].transcript.feed("old work\r\n")
    await registry.close_terminal("Alex")
    fresh = await registry.add_terminal(name="Alex", direction="right")
    assert fresh.transcript.tail() == []
    assert fresh.prompts_sent == 0
