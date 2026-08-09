"""In the Command Deck, an order with nobody's name on it still reaches an agent.

Everywhere else that sentence keeps meaning what it always did. That boundary is
the whole safety story of this feature: a workspace open in the grid must not
start swallowing ordinary conversation, and the deck must not need a call-sign
per order from a user who is running the room by voice.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jarvis.agentic_ide import prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    return reg


@pytest.fixture
def manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    # Pinned so the wording assertions do not depend on the host's locale
    # (AP-23: never test against the maintainer's own configuration).
    mgr._reply_language = "en"
    return mgr


@pytest.fixture(autouse=True)
def _fake_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_compose(utterance: str, **kwargs: object) -> ComposedPrompt:
        name = kwargs["terminal_name"]
        instruction = kwargs.get("instruction") or utterance
        return ComposedPrompt(
            text=f"## Task for {name}\n{instruction}", files=[], composed_by="llm"
        )

    monkeypatch.setattr(prompt_composer, "compose", fake_compose)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _open(registry: Registry, folder: Path, count: int, *, view: str) -> None:
    """Open ``count`` live panes and report the workspace as read in ``view``."""
    await registry.start(str(folder), [{"agent": "claude"} for _ in range(count)])
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)
    registry.set_surface_context(
        workspace_id=registry.session.id,
        view=view,
        on_screen=True,
        terminal=None,
    )


def _typed(registry: Registry) -> dict[str, str]:
    """What actually reached each pane, by call-sign."""
    assert registry.session is not None
    return {
        term.name: str(getattr(term, "last_prompt", "") or "")
        for term in registry.session.terminals
    }


async def test_the_deck_gives_an_unaddressed_order_to_a_free_agent(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2, view="deck")

    reply = await manager._run_agentic_ide_fast_path("get the wake-path tests green")

    assert reply is not None
    delivered = [name for name, prompt in _typed(registry).items() if prompt]
    assert len(delivered) == 1


async def test_the_grid_does_not(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    # The boundary. An unaddressed instruction in the grid is a sentence for
    # Jarvis, and a workspace that quietly typed it into an agent would make
    # every open workspace a hazard to ordinary conversation.
    await _open(registry, tmp_path, 2, view="grid")

    await manager._run_agentic_ide_fast_path("get the wake-path tests green")

    assert not any(_typed(registry).values())


async def test_a_question_is_never_delegated(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    # It contains an instruction verb and is not an instruction. Typing it into
    # a coding agent as a task is the worst thing this path could do.
    await _open(registry, tmp_path, 2, view="deck")

    await manager._run_agentic_ide_fast_path("how do I fix the wake path?")

    assert not any(_typed(registry).values())


async def test_a_busy_fleet_is_told_rather_than_queued(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    # A silently held order is how somebody ends up waiting an hour on work
    # that was never started.
    await _open(registry, tmp_path, 1, view="deck")
    assert registry.session is not None
    for term in registry.session.terminals:
        # Stamped as the sweep would, NOW. A timestamp in the future is not
        # "very fresh" to `activity.observed` — it is outside the window in the
        # other direction, and the reading falls back to a single look.
        term.activity = "working"
        term.activity_at = time.time()
        term.activity_since = time.time()

    reply = await manager._run_agentic_ide_fast_path("get the wake-path tests green")

    assert reply is not None
    assert "free" in reply.lower() or "working" in reply.lower()
    assert not any(_typed(registry).values())


async def test_a_workspace_with_no_agent_says_that_instead(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    # Telling somebody their agents are busy when they have none is the kind of
    # small lie that makes a surface untrustworthy.
    await registry.start(str(tmp_path), [{"agent": "shell"}])
    assert registry.session is not None
    registry.set_surface_context(
        workspace_id=registry.session.id, view="deck", on_screen=True, terminal=None
    )

    reply = await manager._run_agentic_ide_fast_path("get the wake-path tests green")

    assert reply is not None
    assert "no agent" in reply.lower()


async def test_a_pane_the_user_took_over_is_skipped(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2, view="deck")
    assert registry.session is not None
    first, second = registry.session.terminals
    registry.set_deck_hold(first.name, True)

    await manager._run_agentic_ide_fast_path("get the wake-path tests green")

    typed = _typed(registry)
    assert not typed[first.name]
    assert typed[second.name]


async def test_naming_a_pane_still_wins(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    # Delegation is the fallback for an order with no name on it, never a
    # replacement for the addressing path.
    await _open(registry, tmp_path, 3, view="deck")
    assert registry.session is not None
    wanted = registry.session.terminals[2].name

    await manager._run_agentic_ide_fast_path(f"tell {wanted} to fix the wake path")

    typed = _typed(registry)
    assert typed[wanted]
    assert [name for name, prompt in typed.items() if prompt] == [wanted]


async def test_asking_for_a_background_agent_still_spawns_one(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    # Precedence rule 1 of `intent`: naming the spawn vehicle outranks the
    # workspace. The deck must not swallow it just because a pane is free.
    await _open(registry, tmp_path, 2, view="deck")

    reply = await manager._run_agentic_ide_fast_path(
        "spawn a background agent to review the wake path"
    )

    assert reply is None
    assert not any(_typed(registry).values())
