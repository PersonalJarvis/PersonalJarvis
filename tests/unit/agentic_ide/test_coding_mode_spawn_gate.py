"""A pane's own sub-agent fan-out is the pane's work; delegation stays available.

"Sub-agent" is what every agentic coding CLI calls its own parallel helpers, and
it is also the word that makes Jarvis dispatch a background mission worker.
Inside a coding workspace those two readings point in opposite directions.

Live failure these pin (voice session 2026-07-27 20:00): "let Alex and Ellis do
a deep dive … and they should spawn swarms of sub-agents". Both call-signs named
running panes, the addressing was detected correctly — and the turn went to an
invisible background worker anyway because the sentence contained "sub-agents"
and "spawn". Both terminals sat idle while the assistant reported them briefed.

The TURN decides, not the mode (maintainer, 2026-07-28). An earlier version of
this fix blocked every spawn while coding mode was on, which bought the fix by
deleting a feature — brainstorming inside the IDE and asking for a background
agent is legitimate and common. So the guards below come in pairs: each one that
pins "this reaches the pane" has a sibling pinning "and this still delegates".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.intent import owns_turn, spawn_vehicle_outranks_workspace
from jarvis.agentic_ide.session import Registry, reset_registry
from jarvis.brain.spawn_gate import (
    OFFER_WINDOW,
    SPAWN_BLOCKED_ADDRESSED_PANE_FEEDBACK,
    SPAWN_BLOCKED_MODEL_FEEDBACK,
    addressed_pane_blocks_spawn,
    llm_spawn_allowed,
    spawn_blocked_feedback,
)
from tests.fakes.fake_pty_manager import FakePtyManager

# The live utterance, in the spelling the transcript actually produced —
# including "Elis" for the pane called Ellis, which is what made the phonetic
# folding part of this path rather than an incidental detail.
LIVE_TURN = (
    "Kannst du bitte einen Alex und Elis mal einen Deep Dive machen "  # i18n-allow: transcript
    "lassen und ich möchte, dass die nach konkreten Fehlern bei "  # i18n-allow: transcript
    "unserem Wiki System suchen. Die sollen nur lesen und sie sollen "  # i18n-allow: transcript
    "Schwärme von Sub Agents spawnen, um kompletten Kontext für die "  # i18n-allow: transcript
    "Codebasis zu erlangen."  # i18n-allow: transcript
)

PANES = ["Alex", "Ellis", "Casey"]

# The strongest delegation wording the gate knows, spoken.
EXPLICIT_DELEGATION = "Spawne bitte einen Agenten im Hintergrund"  # i18n-allow: input vocab

# The mirror cases: the vehicle word comes FIRST, so these are orders to Jarvis
# even though two of them also name a running pane.
GENUINE_DELEGATIONS = [
    "Spawne einen Agenten, der Alex hilft die Tests zu fixen",  # i18n-allow: input vocab
    "spawn an agent that helps Alex fix the failing tests",
    "Delegiere das im Hintergrund an einen Worker",  # i18n-allow: input vocab
]

# "Open five more panes" — the pane-noun path, decided before any vehicle logic.
MORE_TERMINALS = "Öffne bitte fünf neue Claude Code Terminals"  # i18n-allow: input vocab


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    OFFER_WINDOW.disarm()
    yield
    reset_registry()
    OFFER_WINDOW.disarm()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Registry:
    registry = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    monkeypatch.setattr(session_mod, "get_registry", lambda: registry)
    return registry


async def _coding_mode(registry: Registry, folder: Path) -> str:
    """Open a workspace in coding mode; returns the one pane's call-sign."""
    await registry.start(str(folder), [{"agent": "claude"}])
    registry.set_focus_mode(True)
    return registry.session.terminals[0].name


# --------------------------------------------------------------------------- #
# The feature that must NOT be deleted                                        #
# --------------------------------------------------------------------------- #


async def test_coding_mode_still_allows_an_explicit_spawn(
    wired: Registry, tmp_path: Path
) -> None:
    """The scope correction, pinned: coding mode is not a reason to refuse.

    Brainstorming inside the IDE and asking for a background agent is a normal
    thing to do. The first version of this fix blocked it outright; this test
    exists so that never comes back silently.
    """
    await _coding_mode(wired, tmp_path)
    assert llm_spawn_allowed(EXPLICIT_DELEGATION) is True


async def test_coding_mode_allows_a_research_delegation(
    wired: Registry, tmp_path: Path
) -> None:
    """Naming no pane means the workspace has no claim, mode or not."""
    await _coding_mode(wired, tmp_path)
    assert llm_spawn_allowed("Spawn a background agent that audits the wiki") is True


@pytest.mark.parametrize("text", GENUINE_DELEGATIONS)
def test_genuine_delegation_still_outranks_an_open_workspace(text: str) -> None:
    """The vehicle word FIRST is an order to Jarvis, even when a pane is named.

    "Spawn an agent that helps Alex" names a pane too — as what the new agent is
    FOR. Reading it as an instruction to Alex would swallow a background request
    the user genuinely made.
    """
    assert spawn_vehicle_outranks_workspace(text, names=PANES) is True
    assert owns_turn(text, names=PANES) is False


def test_no_workspace_leaves_the_gate_untouched() -> None:
    """With no workspace at all, the gate behaves exactly as it always did."""
    assert llm_spawn_allowed("Spawn an agent to research this") is True
    assert llm_spawn_allowed("What is the capital of Portugal?") is False


# --------------------------------------------------------------------------- #
# The turn that belongs to a pane                                             #
# --------------------------------------------------------------------------- #


def test_live_turn_reaches_the_addressed_panes() -> None:
    """The 2026-07-27 20:00 utterance belongs to Alex and Ellis, not to a worker."""
    assert owns_turn(LIVE_TURN, names=PANES) is True


def test_pane_told_to_fan_out_keeps_the_turn_in_english() -> None:
    """Same shape, plainly worded — the rule is word order, not locale."""
    text = "Alex should spawn a swarm of sub-agents to map the codebase"
    assert owns_turn(text, names=PANES) is True
    assert spawn_vehicle_outranks_workspace(text, names=PANES) is False


def test_a_report_about_a_spawned_window_does_not_outrank_the_pane() -> None:
    """The live 2026-08-06 18:51 turn: 'spawned' reports, it does not request.

    "It spawned on my other screen" comments on the window that had just
    opened; the instruction addresses t1. The word-order tie-breaker cannot
    save this shape — the past-tense report stands BEFORE the call-sign — so
    the reported mention must not count as naming the vehicle at all.
    Reading it as one made the deterministic fast path refuse to type into
    the very pane the sentence addressed, and the turn took a 5 s router
    detour it never came back from.
    """
    text = (
        "It spawned on my other screen, but no problem. Could you please "
        "prompt terminal t1 to do a deep dive and look for bugs related "
        "on macOS?"
    )
    panes = ["T1", "T2", "T3"]
    assert spawn_vehicle_outranks_workspace(text, names=panes) is False
    assert owns_turn(text, names=panes) is True


async def test_addressed_pane_blocks_the_llm_spawn(
    wired: Registry, tmp_path: Path
) -> None:
    """End to end through the gate the model actually hits."""
    pane = await _coding_mode(wired, tmp_path)
    text = f"{pane} should spawn sub-agents and analyze the wiki system"
    assert addressed_pane_blocks_spawn(text) is True
    assert llm_spawn_allowed(text) is False


async def test_plain_terminal_prompt_never_spawns(
    wired: Registry, tmp_path: Path
) -> None:
    """The ordinary case the user cares about most: "prompt <pane> …"."""
    pane = await _coding_mode(wired, tmp_path)
    text = f"Prompt {pane} to review the wake pipeline"
    assert addressed_pane_blocks_spawn(text) is True
    assert llm_spawn_allowed(text) is False


# --------------------------------------------------------------------------- #
# What the model is told when a spawn is refused                              #
# --------------------------------------------------------------------------- #


async def test_blocked_feedback_points_at_the_terminal(
    wired: Registry, tmp_path: Path
) -> None:
    """A block caused by an addressed pane must not invite a background offer.

    The generic text asks the model to OFFER delegation on the next turn, which
    is the wrong next move here — the work belongs in the pane just named.
    """
    pane = await _coding_mode(wired, tmp_path)
    message = spawn_blocked_feedback(f"{pane} should spawn sub-agents")
    assert message == SPAWN_BLOCKED_ADDRESSED_PANE_FEEDBACK
    assert "agentic-ide-prompt" in message
    # Gathering context for the brief stays explicitly allowed.
    assert "other functions" in message


async def test_blocked_feedback_stays_generic_without_a_pane(
    wired: Registry, tmp_path: Path
) -> None:
    """A conversational block keeps the offer-delegation guidance."""
    await _coding_mode(wired, tmp_path)
    assert spawn_blocked_feedback("What is the capital of Portugal?") == (
        SPAWN_BLOCKED_MODEL_FEEDBACK
    )
    assert spawn_blocked_feedback() == SPAWN_BLOCKED_MODEL_FEEDBACK


def test_asking_for_more_terminals_is_still_the_workspace() -> None:
    """The pane-noun path is untouched — it is checked before any of this."""
    assert owns_turn(MORE_TERMINALS, names=PANES) is True
