"""Adding and closing panes in a running workspace.

The row model is what makes the two split buttons mean something: "right" joins
the anchor's row so the panes render side by side, "down" opens a new row
beneath it. These tests pin that arithmetic, because an off-by-one in the row
numbers renders as an empty band in the grid or as two panes stacked where one
was expected.
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


def _layout(registry: Registry) -> list[tuple[str, int]]:
    """(name, row) in render order — the shape the grid draws."""
    return [(t.name, t.row) for t in registry.session.terminals]


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


# --------------------------------------------------------------- initial rows
async def test_a_fresh_workspace_puts_every_pane_in_row_zero(
    registry: Registry, tmp_path: Path
) -> None:
    """The wizard's grid is one row; splitting is what creates more."""
    await _open(registry, tmp_path, 3)
    assert _layout(registry) == [("Mika", 0), ("Nova", 0), ("Aria", 0)]


# ---------------------------------------------------------------- split right
async def test_split_right_joins_the_anchor_row(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, 2)
    term = await registry.add_terminal(anchor="Mika", direction="right")
    assert term.row == 0
    # Inserted directly after its anchor, not appended at the end.
    assert _layout(registry) == [("Mika", 0), (term.name, 0), ("Nova", 0)]


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
    term = await registry.add_terminal(anchor="Mika", direction="right", agent="codex")
    assert term.agent == "codex"


# ----------------------------------------------------------------- split down
async def test_split_down_opens_a_new_row_below_the_anchor(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    term = await registry.add_terminal(anchor="Mika", direction="down")
    assert term.row == 1
    assert _layout(registry) == [("Mika", 0), (term.name, 1), ("Nova", 0)]


async def test_split_down_pushes_existing_lower_rows_down(
    registry: Registry, tmp_path: Path
) -> None:
    """Inserting between rows must not collide with the row already there."""
    await _open(registry, tmp_path, 1)
    bottom = await registry.add_terminal(anchor="Mika", direction="down")
    middle = await registry.add_terminal(anchor="Mika", direction="down")
    rows = {name: row for name, row in _layout(registry)}
    assert rows["Mika"] == 0
    assert rows[middle.name] == 1, "the newest pane sits directly under its anchor"
    assert rows[bottom.name] == 2, "the older row moved down"


# --------------------------------------------------------------------- naming
async def test_new_panes_take_the_next_free_call_sign(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)  # Mika, Nova
    term = await registry.add_terminal(direction="right")
    assert term.name == "Aria"


async def test_an_explicit_name_is_deduplicated(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, 1)
    term = await registry.add_terminal(name="Mika", direction="right")
    assert term.name == "Mika 2"


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
        await registry.add_terminal(anchor="Mika", direction="sideways")


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
    await registry.attach("Mika", 80, 24, _noop, _noop_exit)
    pty_id = registry.session.terminals[0].pty_id
    closed = await registry.close_terminal("Mika")
    assert closed.name == "Mika"
    assert pty_id in fake_pty.closed
    assert [t.name for t in registry.session.terminals] == ["Nova"]


async def test_closing_repacks_the_rows(registry: Registry, tmp_path: Path) -> None:
    """A row left empty would render as a blank band, so rows are re-packed."""
    await _open(registry, tmp_path, 1)
    middle = await registry.add_terminal(anchor="Mika", direction="down")
    bottom = await registry.add_terminal(anchor=middle.name, direction="down")
    assert [row for _n, row in _layout(registry)] == [0, 1, 2]

    await registry.close_terminal(middle.name)
    assert _layout(registry) == [("Mika", 0), (bottom.name, 1)]


async def test_closing_the_last_pane_leaves_an_empty_workspace(
    registry: Registry, tmp_path: Path
) -> None:
    """Allowed on purpose: the grid then offers to open a fresh terminal."""
    await _open(registry, tmp_path, 1)
    await registry.close_terminal("Mika")
    assert registry.session is not None
    assert registry.session.terminals == []


async def test_closing_an_unknown_pane_names_the_real_ones(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 1)
    with pytest.raises(SessionError, match="Mika"):
        await registry.close_terminal("Gandalf")


async def test_a_closed_pane_refuses_further_prompts(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    await registry.attach("Mika", 80, 24, _noop, _noop_exit)
    await registry.close_terminal("Mika")
    with pytest.raises(SessionError, match="No terminal called"):
        await registry.send_prompt("Mika", "still there?")


async def test_a_reopened_call_sign_is_a_fresh_pane(
    registry: Registry, tmp_path: Path
) -> None:
    """Closing Mika and splitting again must not resurrect the old transcript."""
    await _open(registry, tmp_path, 1)
    await registry.attach("Mika", 80, 24, _noop, _noop_exit)
    registry.session.terminals[0].transcript.feed("old work\r\n")
    await registry.close_terminal("Mika")
    fresh = await registry.add_terminal(name="Mika", direction="right")
    assert fresh.transcript.tail() == []
    assert fresh.prompts_sent == 0
