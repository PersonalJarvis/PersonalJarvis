"""Navigation-gate-vs-Agentic-IDE routing (a section word inside a pane order).

Live bug (voice session 2026-07-29 17:04, coding mode ON, BUG-121): the user
asked terminal T7 to investigate why the workspace resume feature only works for
Claude Code sessions. Jarvis opened the *Sessions* sidebar section and answered
the turn. T7 was never briefed — and the live model, which is never told any of
this, narrated a briefing that had not happened. The user had to repeat himself
twice; the third attempt only worked because the live model happened to call the
action tool by itself.

Root cause (measured off the flight recorder, not guessed): ``BrainManager
.generate`` runs ``_run_navigation_fast_path`` at line ~9243 and
``_run_agentic_ide_fast_path`` at ~9279, and ``match_navigation_intent``
searched the whole utterance for a cue and a section word independently. In this
dictated paragraph the cue was ``open`` — from the CLI product name "Open Code"
— and the section word was ``sessions`` from a bug description 30 characters
later, in a different clause behind an "oder". The navigation gate returned
"Opening Sessions." and the delivery path 36 lines below never ran.

This is the same shape as BUG-120 (``test_config_gate_vs_agentic_ide_routing``),
with a different thief. Two independent fixes, one test file:

1. ``match_navigation_intent`` binds the section word to the cue — same clause,
   after the cue, at most a command's worth of filler between them.
2. A turn that NAMES A RUNNING PANE outranks the navigation gate, the same
   precedence the desktop gate and the config gates already honour.

Deterministic throughout: real gate, real detector, faked prompt composer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from jarvis.agentic_ide import prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from jarvis.brain import navigation_intent
from jarvis.brain.manager import BrainManager
from jarvis.brain.navigation_intent import match_navigation_intent
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from tests.fakes.fake_pty_manager import FakePtyManager

# The live transcript, verbatim off the flight recorder. ``{a}`` stands where
# the user named the pane. Every word that made the navigation gate fire is kept
# at its original distance from the others.
LIVE_FAILURE = (
    "Kannst du mal bitte Terminal {a} prompten, dass es einen "  # i18n-allow: transcript
    "Deep Dive machen, sondern analysieren soll, wieso das "  # i18n-allow: transcript
    "Resuming Feature von ähm unseren Agentic ID, was wir "  # i18n-allow: transcript
    "eingebaut haben, nur bei ähm claude Code Sessions "  # i18n-allow: transcript
    "funktioniert und nicht z.B. bei Codec Sessions oder bei "  # i18n-allow: transcript
    "Open Codes oder bei anderen Sessions. Also, ich möchte, "  # i18n-allow: transcript
    "dass das Resuming Feature bei allen ähm Coding Terminals, "  # i18n-allow: transcript
    "welche wir verbunden haben, funktioniert."  # i18n-allow: transcript
)


class _SpyExecutor:
    """Records navigate executions so a stand-down can be proven, not inferred."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        tool: object,
        args: dict[str, Any],
        *,
        user_utterance: str = "",
        trace_id: UUID | None = None,
    ) -> dict[str, Any]:
        self.calls.append(dict(args))
        return {"success": True}


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never rewrite the developer's real recent-workspace list from a test."""
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture(autouse=True)
def _fake_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic stand-in for the quality-tier prompt writer."""

    async def fake_compose(utterance: str, **kwargs: object) -> ComposedPrompt:
        name = kwargs["terminal_name"]
        instruction = kwargs.get("instruction") or utterance
        return ComposedPrompt(
            text=f"## Task for {name}\n{instruction}",
            files=["jarvis/core/bus.py"],
            composed_by="llm",
        )

    monkeypatch.setattr(prompt_composer, "compose", fake_compose)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    return reg


@pytest.fixture
def spy() -> _SpyExecutor:
    return _SpyExecutor()


@pytest.fixture
def manager(spy: _SpyExecutor) -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    # A real navigate tool is not needed — the fast path only forwards it to the
    # executor — but SOMETHING must be registered, or the gate returns None for
    # a reason that has nothing to do with the precedence under test.
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={"navigate": object()})
    mgr._tool_executor = spy  # type: ignore[assignment]
    # Pinned so wording assertions do not depend on the host's locale
    # (AP-23: never test against the maintainer's own configuration).
    mgr._reply_language = "en"
    return mgr


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _open(registry: Registry, folder: Path, count: int) -> None:
    """Open ``count`` panes AND bring their agents live."""
    await registry.start(str(folder), [{"agent": "claude"} for _ in range(count)])
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)


def _names(registry: Registry) -> list[str]:
    assert registry.session is not None
    return [t.name for t in registry.session.terminals]


def _prompted(registry: Registry) -> list[str]:
    """Every pane that actually received a prompt."""
    assert registry.session is not None
    return [t.name for t in registry.session.terminals if t.prompts_sent > 0]


def _spoken(registry: Registry) -> str:
    """The live utterance with the addressed pane filled in."""
    return LIVE_FAILURE.format(a=_names(registry)[6])


# --------------------------------------------------------------------------- #
# Fix 1 — the gate may not assemble a command out of unrelated clauses         #
# --------------------------------------------------------------------------- #


def test_the_navigation_gate_no_longer_claims_this_turn() -> None:
    """The ingredients are all present; only their binding disqualifies them."""
    spoken = LIVE_FAILURE.format(a="T7")
    assert "Open" in spoken
    assert "Sessions" in spoken

    assert match_navigation_intent(spoken) is None


def test_a_real_navigation_command_is_untouched() -> None:
    """The recall side: the gate still exists to catch these."""
    spoken = "zeig mir die Sessions"  # i18n-allow: speech input under test
    assert match_navigation_intent(spoken) == "sessions"


# --------------------------------------------------------------------------- #
# Fix 2 — a named pane outranks the navigation gate                            #
# --------------------------------------------------------------------------- #


async def test_the_workspace_owns_this_turn(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """The guard the navigation fast path consults, against a REAL workspace."""
    await _open(registry, tmp_path, 7)

    assert manager._agentic_ide_owns_turn(_spoken(registry)) is True


async def test_a_forced_section_match_still_cannot_take_a_pane_turn(
    manager: BrainManager,
    registry: Registry,
    spy: _SpyExecutor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The precedence itself, proven independently of the matcher's own fix.

    The matcher is forced to claim a section, so this fails unless the fast path
    really does consult the workspace before it navigates. Belt and braces on
    purpose: fix 1 stops this particular sentence, fix 2 stops the whole class —
    some future section word will land next to a genuine cue in a pane order.
    """
    await _open(registry, tmp_path, 7)
    monkeypatch.setattr(
        navigation_intent, "match_navigation_intent", lambda _text: "sessions"
    )

    reply = await manager._run_navigation_fast_path(_spoken(registry))

    assert reply is None
    assert spy.calls == []


async def test_navigation_still_runs_while_a_workspace_is_open(
    manager: BrainManager, registry: Registry, spy: _SpyExecutor, tmp_path: Path
) -> None:
    """The stand-down is about ADDRESSED panes, not about coding mode.

    An open workspace must not deafen the sidebar: "show me the sessions" names
    no pane and asks for no work, so it navigates exactly as before.
    """
    await _open(registry, tmp_path, 7)

    reply = await manager._run_navigation_fast_path("show me the sessions")

    assert reply is not None
    assert spy.calls == [{"section": "sessions"}]


async def test_the_addressed_pane_is_briefed_end_to_end(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """What the user actually asked for: the named agent gets the work."""
    await _open(registry, tmp_path, 7)
    names = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(_spoken(registry))

    assert reply is not None
    assert _prompted(registry) == [names[6]]
