"""Opening several panes at once — the batch behind "open five more terminals".

Deliberately layout-agnostic: placement is ``add_terminal``'s job and is pinned
by its own tests. What matters here is the part a spoken request depends on —
how many panes appear, which agent they run, and whether the caller is told the
truth when the pane cap within a workspace cuts the request short. A batch that
silently opened three of five would report success for work that did not happen.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import (
    MAX_TERMINALS,
    Registry,
    SessionError,
    terminals_added_event,
)
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _isolated_recents(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the recents file out of the developer's real data directory.

    Opening a workspace records it as "most recently used", and that file lives
    under the per-user data dir — the SAME file the running app reads. Without
    this, a test run rewrites the maintainer's recent-workspace list with
    throwaway pytest folders (measured 2026-07-25: all eight entries were
    ``pytest-of-…`` paths), and the voice path that opens "the most recent
    workspace" then starts coding agents in a deleted temp directory.
    """
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return Registry(pty_manager=FakePtyManager())


async def _open(registry: Registry, folder: Path, count: int, agent: str = "claude"):
    return await registry.start(
        str(folder), [{"agent": agent} for _ in range(count)]
    )


def _names(registry: Registry) -> list[str]:
    assert registry.session is not None
    return [t.name for t in registry.session.terminals]


async def test_a_batch_opens_every_requested_pane(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    created, capped = await registry.add_terminals(3)
    assert len(created) == 3
    assert capped is False
    assert len(_names(registry)) == 5
    # Call-signs stay unique and come from the speakable pool, so the reply can
    # name what appeared.
    assert len(set(_names(registry))) == 5


async def test_the_cap_truncates_and_says_so(
    registry: Registry, tmp_path: Path
) -> None:
    """Nine panes open, five requested: three appear and ``capped`` is True.

    This is the maintainer's live case (nine panes on screen). Silent truncation
    is the failure mode — the flag is what lets the spoken reply be honest.
    """
    await _open(registry, tmp_path, MAX_TERMINALS - 3)
    created, capped = await registry.add_terminals(5)
    assert len(created) == 3
    assert capped is True
    assert len(_names(registry)) == MAX_TERMINALS


async def test_a_full_workspace_refuses_instead_of_reporting_nothing(
    registry: Registry, tmp_path: Path
) -> None:
    """With no room at all the caller gets an error it can read out loud."""
    await _open(registry, tmp_path, MAX_TERMINALS)
    with pytest.raises(SessionError, match="maximum"):
        await registry.add_terminals(2)


async def test_the_agent_is_inherited_unless_named(
    registry: Registry, tmp_path: Path
) -> None:
    """"Two more" means two more OF THESE; naming an agent overrides that."""
    await _open(registry, tmp_path, 1, agent="codex")
    inherited, _ = await registry.add_terminals(2)
    assert [t.agent for t in inherited] == ["codex", "codex"]

    named, _ = await registry.add_terminals(2, agent="claude")
    assert [t.agent for t in named] == ["claude", "claude"]


async def test_an_unknown_agent_fails_loudly_with_nothing_opened(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 1)
    with pytest.raises(SessionError):
        await registry.add_terminals(3, agent="nope")
    assert len(_names(registry)) == 1


async def test_a_batch_without_a_session_is_an_error(registry: Registry) -> None:
    with pytest.raises(SessionError, match="No Agentic-IDE session"):
        await registry.add_terminals(2)


async def test_the_bus_event_carries_what_the_clients_need(
    registry: Registry, tmp_path: Path
) -> None:
    """One event per batch, naming every pane — not one event per pane.

    The open workspace view refreshes on it, so a five-pane batch must not make
    it refetch five times.
    """
    session = await _open(registry, tmp_path, 1)
    created, _ = await registry.add_terminals(3)
    event = terminals_added_event(session, created, source_layer="test")
    assert event.session_id == session.id
    assert event.names == tuple(t.name for t in created)
    assert len(event.names) == 3
    assert event.agent == "claude"
    assert event.folder == session.folder
