"""Coding mode forbids an internal worker; a pane's own fan-out stays its work.

Two rules, one collision. "Sub-agent" is the vocabulary every agentic coding CLI
uses for its own parallel helpers, and it is also the word that makes Jarvis
dispatch a background mission worker. While the user is inside the Agentic IDE
those two readings point in opposite directions, and the user cannot phrase
their way out of it — so the MODE decides, not the wording.

Live failure these pin (voice session 2026-07-27 20:00, maintainer mandate the
same day): "let Alex and Ellis do a deep dive … and they should spawn swarms of
sub-agents". Both call-signs named running panes, the addressing was detected
correctly — and the turn went to an invisible background worker anyway because
the sentence contained "sub-agents" and "spawn". Both terminals sat idle while
the assistant reported that the agents had been briefed.

The other direction is guarded just as hard: with coding mode off, asking for a
background agent must still get one. That feature is the reason the vehicle
stand-down exists at all, and breaking it while fixing the above would only swap
which half of the user's intent gets lost.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.intent import owns_turn, spawn_vehicle_outranks_workspace
from jarvis.agentic_ide.session import Registry, reset_registry
from jarvis.brain.spawn_gate import (
    OFFER_WINDOW,
    SPAWN_BLOCKED_CODING_MODE_FEEDBACK,
    SPAWN_BLOCKED_MODEL_FEEDBACK,
    coding_mode_blocks_spawn,
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

# The strongest delegation wording the gate knows, spoken. If THIS reaches a
# mission worker while coding mode is on, nothing said inside the IDE is safe
# from the collision.
EXPLICIT_DELEGATION = "Spawne bitte einen Agenten im Hintergrund"  # i18n-allow: input vocab

# The mirror cases: the vehicle word comes FIRST, so these are orders to Jarvis
# even though two of them also name a running pane.
GENUINE_DELEGATIONS = [
    "Spawne einen Agenten, der Alex hilft die Tests zu fixen",  # i18n-allow: input vocab
    "spawn an agent that helps Alex fix the failing tests",
    "Delegiere das im Hintergrund an einen Worker",  # i18n-allow: input vocab
]

# "Open five more panes" — the pane-noun path, which is decided before any of
# the vehicle logic and must stay exactly as it was.
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


async def _coding_mode(registry: Registry, folder: Path) -> None:
    await registry.start(str(folder), [{"agent": "claude"}])
    registry.set_focus_mode(True)


# --------------------------------------------------------------------------- #
# Part A — the mode is the switch                                             #
# --------------------------------------------------------------------------- #


async def test_coding_mode_blocks_even_an_explicit_spawn_request(
    wired: Registry, tmp_path: Path
) -> None:
    """The clearest possible delegation request still starts no worker.

    Deliberately the strongest wording the gate knows: if THIS reaches a mission
    worker, nothing the user says inside the IDE is safe from the collision.
    """
    await _coding_mode(wired, tmp_path)
    assert coding_mode_blocks_spawn() is True
    assert llm_spawn_allowed(EXPLICIT_DELEGATION) is False


async def test_workspace_without_the_toggle_still_delegates(
    wired: Registry, tmp_path: Path
) -> None:
    """Terminals on a screen are not the mode — the background agent stays.

    The toggle is what the user can see (the app-wide coding-mode badge), so it
    is what may take a feature away from them.
    """
    await wired.start(str(tmp_path), [{"agent": "claude"}])
    assert coding_mode_blocks_spawn() is False
    assert llm_spawn_allowed(EXPLICIT_DELEGATION) is True


async def test_leaving_coding_mode_restores_the_background_agent(
    wired: Registry, tmp_path: Path
) -> None:
    """The block is a mode, not a latch — turning it off gives the feature back."""
    await _coding_mode(wired, tmp_path)
    assert llm_spawn_allowed("Spawn a background worker for this") is False
    wired.set_focus_mode(False)
    assert llm_spawn_allowed("Spawn a background worker for this") is True


def test_no_workspace_leaves_the_gate_untouched() -> None:
    """With no workspace at all, the gate behaves exactly as it always did."""
    assert coding_mode_blocks_spawn() is False
    assert llm_spawn_allowed("Spawn an agent to research this") is True
    assert llm_spawn_allowed("What is the capital of Portugal?") is False


async def test_blocked_feedback_tells_the_model_to_use_a_terminal(
    wired: Registry, tmp_path: Path
) -> None:
    """A block inside the IDE must not invite an offer of a background agent.

    The generic text asks the model to OFFER delegation on the next turn, which
    is the wrong next move here — the work belongs in a pane on screen.
    """
    assert spawn_blocked_feedback() == SPAWN_BLOCKED_MODEL_FEEDBACK
    await _coding_mode(wired, tmp_path)
    message = spawn_blocked_feedback()
    assert message == SPAWN_BLOCKED_CODING_MODE_FEEDBACK
    assert "agentic-ide-prompt" in message


# --------------------------------------------------------------------------- #
# Part B — a pane's own fan-out is the pane's work                            #
# --------------------------------------------------------------------------- #


def test_live_turn_reaches_the_addressed_panes() -> None:
    """The 2026-07-27 20:00 utterance belongs to Alex and Ellis, not to a worker.

    Asserted with the toggle OFF on purpose: whether coding mode was on during
    the live session cannot be recovered, so the fix may not depend on it.
    """
    assert coding_mode_blocks_spawn() is False
    assert owns_turn(LIVE_TURN, names=PANES) is True


def test_pane_told_to_fan_out_keeps_the_turn_in_english() -> None:
    """Same shape, plainly worded — the rule is about word order, not locale."""
    text = "Alex should spawn a swarm of sub-agents to map the codebase"
    assert owns_turn(text, names=PANES) is True


@pytest.mark.parametrize("text", GENUINE_DELEGATIONS)
def test_genuine_delegation_still_outranks_an_open_workspace(text: str) -> None:
    """The mirror bug stays shut: the vehicle word FIRST is an order to Jarvis.

    "Spawn an agent that helps Alex" names a pane too — as what the new agent is
    FOR. Reading it as an instruction to Alex would swallow a background request
    the user genuinely made, which is the failure the vehicle stand-down exists
    to prevent.
    """
    assert spawn_vehicle_outranks_workspace(text, names=PANES) is True
    assert owns_turn(text, names=PANES) is False


def test_spawn_vocabulary_without_any_named_pane_is_never_the_workspace() -> None:
    """No call-sign, no claim — the ordinary delegation request is untouched."""
    text = "Spawn a background agent that audits the wiki system"
    assert spawn_vehicle_outranks_workspace(text, names=PANES) is True
    assert owns_turn(text, names=PANES) is False


async def test_coding_mode_hands_the_turn_to_the_pane(
    wired: Registry, tmp_path: Path
) -> None:
    """In coding mode the stand-down lifts entirely.

    Not cosmetic: with Part A blocking the spawn, a turn the workspace also
    refused would reach nothing at all — the spawn gate would decline the
    mission, the router's fast path would decline to type, and the user would
    get silence. The two gates have to agree in the same direction.
    """
    await _coding_mode(wired, tmp_path)
    pane = wired.session.terminals[0].name
    text = f"{pane} should spawn sub-agents and analyze the wiki system"
    assert spawn_vehicle_outranks_workspace(text, names=[pane]) is False
    assert owns_turn(text, names=[pane]) is True


def test_asking_for_more_terminals_is_still_the_workspace() -> None:
    """The pane-noun path is untouched — it is checked before any of this."""
    assert owns_turn(MORE_TERMINALS, names=PANES) is True
