"""A conversational turn must not be able to reach a deterministic write tool.

Latent exposure found 2026-06-30 (deep-dive agent 2): on a no-action
conversational turn the model still saw the foreground write/record tools
(``contact-upsert``, ``update-profile``, ``wiki-ingest``, ``google_calendar``
writes, ``call-contact``) — "does my budget fit?" is the forensic shape.

2026-08-17: the trigger is the turn's own SHAPE (smalltalk or a plain question),
not "the action regex did not fire". The old signalless reading stripped these
tools from "ruf Anna an" and "trag das in meinen Kalender ein" too — see
``test_signalless_action_vehicles.py`` for the other half of the rework.
"""
from __future__ import annotations

import re

from jarvis.brain.manager import BrainManager

_NEVER = re.compile(r"(?!)")  # matches nothing — no explicit heavy-work vehicle


def _mgr(*, smalltalk: bool = False) -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._force_spawn_pattern = _NEVER
    m._evidence_required_tool = ""
    # Signalless by default; individual tests flip these as needed.
    m._turn_has_action_intent = lambda _t: False  # type: ignore[method-assign]
    m._research_wants_artifact = lambda _t: False  # type: ignore[method-assign]
    m._is_smalltalk = lambda _t: smalltalk  # type: ignore[method-assign]
    m._desktop_episode_is_live = lambda: False  # type: ignore[method-assign]
    return m


def _surface() -> dict:
    return {
        "search_web": object(),       # read — must stay
        "wiki-recall": object(),      # read — must stay
        "screenshot": object(),       # read — must stay
        "contact-upsert": object(),   # write — hide on a conversational turn
        # Keyed by the tool's .name attribute (update_profile) — NOT the
        # hyphenated entry-point name. The 2026-07-06 pipeline audit found the
        # constant AND this test both carried "update-profile", so the gate
        # never actually stripped the real tool from a live turn.
        "update_profile": object(),   # write — hide on a conversational turn
        "wiki-ingest": object(),      # write — hide on a conversational turn
        "google_calendar": object(),  # write path — hide on a conversational turn
        "call-contact": object(),     # action (places a call) — hide
        "computer_use": object(),     # inheritance half — see the sibling module
        "spawn_worker": object(),     # inheritance half — see the sibling module
    }


def test_plain_question_hides_write_and_record_tools():
    # The 2026-06-30 forensic shape: a question that writes nothing.
    m = _mgr()
    out = m._hide_action_tools_on_signalless_turn(
        _surface(), "Passt das noch in mein Budget?"  # i18n-allow: the forensic turn
    )
    for hidden in (
        "contact-upsert", "update_profile", "wiki-ingest",
        "google_calendar", "call-contact",
    ):
        assert hidden not in out, hidden
    # Read-only tools are never stripped — the turn stays answerable inline.
    for kept in ("search_web", "wiki-recall", "screenshot"):
        assert kept in out, kept


def test_smalltalk_turn_hides_write_and_record_tools():
    m = _mgr(smalltalk=True)
    out = m._hide_action_tools_on_signalless_turn(_surface(), "Alles klar bei dir")
    assert "contact-upsert" not in out
    assert "google_calendar" not in out


def test_action_intent_keeps_the_write_tools():
    m = _mgr()
    m._turn_has_action_intent = lambda _t: True  # type: ignore[method-assign]
    out = m._hide_action_tools_on_signalless_turn(
        _surface(), "Trag meinen Urlaub in den Kalender ein"  # i18n-allow
    )
    assert "google_calendar" in out
    assert "contact-upsert" in out


def test_mandated_write_tool_is_exempt_say_do_stays_green():
    # resolve_save_mandate fired for "merk dir, dass…" and set the mandate; the
    # hide must NOT strip the mandated write tool, or the say-do write feature
    # breaks again (project_bug_contact_say_do_gap_no_upsert).
    m = _mgr(smalltalk=True)
    m._evidence_required_tool = "wiki-ingest"
    out = m._hide_action_tools_on_signalless_turn(
        _surface(), "Merk dir, dass ich nach Bora Bora will"  # i18n-allow
    )
    assert "wiki-ingest" in out          # the mandated tool survives
    assert "contact-upsert" not in out   # other write tools still hidden


def test_explicit_subagent_vehicle_keeps_tools():
    m = _mgr(smalltalk=True)
    m._force_spawn_pattern = re.compile(r"deep dive|subagent", re.I)
    m._desktop_episode_is_live = lambda: True  # type: ignore[method-assign]
    out = m._hide_action_tools_on_signalless_turn(
        _surface(), "Mach einen deep dive und trag das ein"  # i18n-allow
    )
    assert "spawn_worker" in out
    assert "google_calendar" in out


def test_a_broken_smalltalk_probe_does_not_take_the_gate_down():
    # _is_smalltalk needs the compiled routing patterns; a manager without them
    # must degrade to the question-shape check, not lose the whole gate.
    m = _mgr()

    def _boom(_t: str) -> bool:
        raise RuntimeError("no routing patterns")

    m._is_smalltalk = _boom  # type: ignore[method-assign]
    out = m._hide_action_tools_on_signalless_turn(_surface(), "Was kostet das?")
    assert "google_calendar" not in out
    assert "search_web" in out
