"""Giving a running pane another call-sign.

A rename touches one string and must touch nothing else — that is the entire
contract, and every test here is a way of failing it. The pane's key is what
its running pseudo-terminal is filed under, so a rename that moved the key
would take the agent away from the viewer; the call-sign is what a spoken
instruction resolves against, so a rename that left the OLD name answering
would silently split one pane into two addresses.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import MAX_TERMINAL_NAME, Registry, SessionError
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


# ------------------------------------------------------------------ the rename
async def test_renaming_changes_the_call_sign_and_nothing_else(
    registry: Registry, tmp_path: Path
) -> None:
    """The label moves; the pane's identity, place and agent stay put."""
    await _open(registry, tmp_path, 2)
    before = registry.session.terminals[0]
    key, column, slot, agent = before.key, before.column, before.slot, before.agent

    _, term = await registry.rename_terminal("T1", "Frontend")

    assert term.name == "Frontend"
    assert (term.key, term.column, term.slot, term.agent) == (key, column, slot, agent)
    assert [t.name for t in registry.session.terminals] == ["Frontend", "T2"]


async def test_the_pane_answers_to_its_new_name(
    registry: Registry, tmp_path: Path
) -> None:
    """A rename is only real if the next instruction reaches the pane by it."""
    await _open(registry, tmp_path, 2)
    await registry.rename_terminal("T1", "Frontend")

    found = registry.find_terminal("Frontend")
    assert found is not None
    assert found[1].key == registry.session.terminals[0].key


async def test_a_freed_call_sign_goes_to_the_next_pane_that_asks_for_it(
    registry: Registry, tmp_path: Path
) -> None:
    """T1 renamed frees T1 — and the pane that takes it is the one T1 means.

    The renamed pane keeps the key ``t1`` (its pseudo-terminal is filed under
    it), so this is precisely where a key-first lookup would hand "T1" back to
    the pane the user renamed to stop it being T1.
    """
    await _open(registry, tmp_path, 1)
    await registry.rename_terminal("T1", "Frontend")
    fresh = await registry.add_terminal(direction="right")

    assert fresh.name == "T1"
    found = registry.find_terminal("T1")
    assert found is not None
    assert found[1].key == fresh.key


async def test_renaming_never_touches_the_running_agent(
    registry: Registry, tmp_path: Path, fake_pty: FakePtyManager
) -> None:
    """No pane is closed, no process is spawned — that is what makes it safe."""
    await _open(registry, tmp_path, 2)
    spawns = len(fake_pty.spawns)
    closed = len(fake_pty.closed)

    await registry.rename_terminal("T2", "Tests")

    assert len(fake_pty.spawns) == spawns
    assert len(fake_pty.closed) == closed


async def test_renaming_to_the_same_name_is_a_no_op(
    registry: Registry, tmp_path: Path
) -> None:
    """Opening the editor and pressing Save unchanged is not an error."""
    await _open(registry, tmp_path, 1)
    _, term = await registry.rename_terminal("T1", "T1")
    assert term.name == "T1"


async def test_surrounding_whitespace_is_cleaned_up(
    registry: Registry, tmp_path: Path
) -> None:
    """A pasted name arrives with spaces; the pane must not be called ' Api '."""
    await _open(registry, tmp_path, 1)
    _, term = await registry.rename_terminal("T1", "  Api   layer  ")
    assert term.name == "Api layer"


# ------------------------------------------------------------------- refusals
async def test_a_name_another_pane_already_carries_is_refused(
    registry: Registry, tmp_path: Path
) -> None:
    """Two panes on one call-sign make every spoken instruction a coin flip."""
    await _open(registry, tmp_path, 2)
    await registry.rename_terminal("T1", "Frontend")

    with pytest.raises(SessionError, match="already called"):
        await registry.rename_terminal("T2", "frontend")

    assert [t.name for t in registry.session.terminals] == ["Frontend", "T2"]


async def test_an_empty_name_is_refused(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, 1)
    with pytest.raises(SessionError, match="Give the terminal a name"):
        await registry.rename_terminal("T1", "   ")


async def test_a_name_with_nothing_to_match_on_is_refused(
    registry: Registry, tmp_path: Path
) -> None:
    """Punctuation normalizes to nothing, leaving a pane nobody can address."""
    await _open(registry, tmp_path, 1)
    with pytest.raises(SessionError, match="letters or numbers"):
        await registry.rename_terminal("T1", "!!! ---")
    assert registry.session.terminals[0].name == "T1"


async def test_an_overlong_name_is_refused(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, 1)
    with pytest.raises(SessionError, match="at most"):
        await registry.rename_terminal("T1", "x" * (MAX_TERMINAL_NAME + 1))


async def test_an_unknown_pane_says_which_ones_exist(
    registry: Registry, tmp_path: Path
) -> None:
    """The error names the running panes — the answer to the question asked."""
    await _open(registry, tmp_path, 2)
    with pytest.raises(SessionError, match="T1, T2"):
        await registry.rename_terminal("T7", "Frontend")


# ---------------------------------------------------------------- persistence
async def test_the_new_name_survives_a_reopen(
    registry: Registry, tmp_path: Path
) -> None:
    """A pane called "Frontend" today is still Frontend after a restart.

    Through the restore snapshot rather than an in-memory copy: the name is
    written where the workspace is remembered, or a rename lasts exactly until
    the app is closed.
    """
    await _open(registry, tmp_path, 2)
    await registry.rename_terminal("T1", "Frontend")

    stored = resume_store.load()
    assert stored is not None
    fresh = Registry(pty_manager=FakePtyManager())
    result = await fresh.restore(stored)

    assert [t.name for s in result.sessions for t in s.terminals] == ["Frontend", "T2"]
