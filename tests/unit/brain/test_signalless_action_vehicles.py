"""An ordinary action request keeps its vehicle; inheritance is still blocked.

Two halves of ``_hide_action_tools_on_signalless_turn`` (2026-08-17 rework):

* The 2026-06-27 forensic stays fixed. "Was geht ab?" -> STT "Lask it up!" [en]
  conf 0.509, and the router-LLM re-ran the prior "open Discord, bridge-mine
  channel" computer_use plan on a turn that asked for nothing. That inheritance
  needs a desktop episode in the context to inherit FROM, so the hide is gated
  on ``cu_run_registry.has_recent_run`` — the same signal ``cu_gate`` uses.
* Cold, the vehicles stay. The old gate hid computer_use and every write tool
  from any turn missing ``is_open_app_intent`` / ``_looks_like_pc_control`` /
  the capability registry. That regex knows "klick" and "Bildschirm"; it does
  not know "spiel Musik", "ruf Anna an" or "trag das in meinen Kalender ein",
  so those requests reached the model with no vehicle at all.
"""  # i18n-allow: verbatim quotes of the live German utterances
from __future__ import annotations

import re

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.harness import cu_run_registry

_NEVER = re.compile(r"(?!)")  # matches nothing — no explicit heavy-work vehicle


@pytest.fixture(autouse=True)
def _clean_registry():
    cu_run_registry.clear_runs()
    yield
    cu_run_registry.clear_runs()


def _mgr(*, episode_live: bool | None = None) -> BrainManager:
    """A manager with every deterministic action detector answering "no signal"."""
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._force_spawn_pattern = _NEVER
    m._evidence_required_tool = ""
    m._turn_has_action_intent = lambda _t: False  # type: ignore[method-assign]
    m._research_wants_artifact = lambda _t: False  # type: ignore[method-assign]
    m._is_smalltalk = lambda _t: False  # type: ignore[method-assign]
    if episode_live is not None:
        m._desktop_episode_is_live = lambda: episode_live  # type: ignore[method-assign]
    return m


def _surface() -> dict:
    return {
        "search_web": object(),
        "screenshot": object(),
        "computer_use": object(),
        "spawn_worker": object(),
        "multi_spawn": object(),
        "call-contact": object(),
        "google_calendar": object(),
        "spotify/play": object(),
    }


# --------------------------------------------------------------------------
# The vehicles survive an ordinary action request
# --------------------------------------------------------------------------

# Real requests whose verb is in no PC-control regex and in no seeded registry.
# Each names an action; none may lose the tool that could perform it.
VEHICLE_TURNS = [
    "Spiel Musik",                        # i18n-allow: play music
    "Ruf Anna an",                        # i18n-allow: call Anna
    "Trag das in meinen Kalender ein",    # i18n-allow: put that in my calendar
    "Mach das Licht aus",                 # i18n-allow: turn off the light
]


@pytest.mark.parametrize("utterance", VEHICLE_TURNS)
def test_ordinary_action_request_keeps_every_vehicle(utterance: str) -> None:
    gated = _mgr(episode_live=False)._hide_action_tools_on_signalless_turn(
        _surface(), utterance
    )
    for kept in ("computer_use", "call-contact", "google_calendar", "spotify/play"):
        assert kept in gated, (
            f"{kept} must stay visible on an action request the regex misses: "
            f"{utterance!r}"
        )


def test_no_gate_fires_when_nothing_is_conversational_and_no_episode_runs() -> None:
    surface = _surface()
    gated = _mgr(episode_live=False)._hide_action_tools_on_signalless_turn(
        surface, "Spiel Musik"  # i18n-allow
    )
    assert set(gated) == set(surface), "a cold, non-conversational turn loses nothing"


# --------------------------------------------------------------------------
# The 2026-06-27 inheritance forensic stays fixed
# --------------------------------------------------------------------------

NO_ACTION_SIGNAL_TURNS = [
    "Lask it up!",                              # the live STT junk
    "Mask it up.",                              # sibling STT-junk variant
    "Was geht ab?",                             # i18n-allow: the original question
    "Wie viele Menschen leben in Australien?",  # i18n-allow: long factual question
    "Which company owns the most data centers in the world right now?",
]


@pytest.mark.parametrize("utterance", NO_ACTION_SIGNAL_TURNS)
def test_signalless_turn_inside_a_desktop_episode_loses_the_heavy_tools(
    utterance: str,
) -> None:
    gated = _mgr(episode_live=True)._hide_action_tools_on_signalless_turn(
        _surface(), utterance
    )
    assert "computer_use" not in gated, (
        f"computer_use must not be inheritable on: {utterance!r}"
    )
    assert "spawn_worker" not in gated
    assert "multi_spawn" not in gated
    assert "search_web" in gated, "read-only search_web must stay visible"


def test_live_registry_run_drives_the_episode_probe() -> None:
    # Not a stub: the real registry decides, so the gate and cu_gate can never
    # read "are we in a desktop episode" differently.
    assert BrainManager._desktop_episode_is_live() is False
    cu_run_registry.register_run("m-1", "open Discord, bridge-mine channel", None)
    assert BrainManager._desktop_episode_is_live() is True

    gated = _mgr()._hide_action_tools_on_signalless_turn(_surface(), "Lask it up!")
    assert "computer_use" not in gated

    cu_run_registry.clear_runs()
    gated = _mgr()._hide_action_tools_on_signalless_turn(_surface(), "Lask it up!")
    assert "computer_use" in gated, (
        "with no desktop episode there is no prior plan to inherit"
    )


def test_action_intent_keeps_the_vehicles_even_inside_an_episode() -> None:
    m = _mgr(episode_live=True)
    m._turn_has_action_intent = lambda _t: True  # type: ignore[method-assign]
    gated = m._hide_action_tools_on_signalless_turn(
        _surface(), "Klick auf den Play-Button"  # i18n-allow
    )
    assert "computer_use" in gated


# --------------------------------------------------------------------------
# Request framing is not a question
# --------------------------------------------------------------------------

POLITE_REQUESTS = [
    "Kannst du Anna anrufen?",             # i18n-allow: can you call Anna
    "Kannst du das nochmal machen?",       # i18n-allow: the BUG-105 follow-up shape
    "Can you put that in my calendar?",
    "Ich möchte Musik hören",              # i18n-allow: I want to hear music
]


@pytest.mark.parametrize("utterance", POLITE_REQUESTS)
def test_request_framing_keeps_every_vehicle(utterance: str) -> None:
    """A politely framed request is question-SHAPED but it is a command.

    ``_is_smalltalk`` already trusts ``_ACTION_REQUEST_RE`` for exactly this
    call; without it here, "Kannst du Anna anrufen?" ends in "?" and loses
    call-contact to the write-reflex half, and a corrective follow-up inside a
    live desktop episode loses the computer_use that cu_gate's follow-up window
    exists to keep alive.
    """
    m = _mgr(episode_live=True)
    m._is_smalltalk = lambda _t: True  # type: ignore[method-assign]
    gated = m._hide_action_tools_on_signalless_turn(_surface(), utterance)
    assert set(gated) == set(_surface()), f"nothing may be hidden on: {utterance!r}"


def test_the_gate_never_empties_a_surface() -> None:
    # Worst case: a conversational turn inside a live desktop episode. Even then
    # the read-only tools survive — a gate narrows, it never blanks.
    m = _mgr(episode_live=True)
    m._is_smalltalk = lambda _t: True  # type: ignore[method-assign]
    gated = m._hide_action_tools_on_signalless_turn(_surface(), "Was geht ab?")
    assert set(gated) == {"search_web", "screenshot", "spotify/play"}
