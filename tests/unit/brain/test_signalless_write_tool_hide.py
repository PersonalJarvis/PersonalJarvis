"""A signalless turn must not be able to reach a deterministic write/record tool.

Latent exposure found 2026-06-30 (deep-dive agent 2): on a no-action
conversational turn the model still sees the foreground write/record tools
(``contact-upsert``, ``update-profile``, ``wiki-ingest``, ``google_calendar``
writes, ``call-contact``). The existing ``_hide_action_tools_on_signalless_turn``
only stripped ``computer_use`` + the spawn vehicles. This widens it to the
write/record tools too — with a hard exemption for any tool the deterministic
layer ALREADY mandated this turn (a real "merk dir, dass…" keeps ``wiki-ingest``
via ``resolve_save_mandate``; a calendar READ keeps ``google_calendar`` via the
evidence gate), so the say-do write feature and calendar reads do not regress.
"""
from __future__ import annotations

import re

from jarvis.brain.manager import BrainManager

_NEVER = re.compile(r"(?!)")  # matches nothing — no explicit heavy-work vehicle


def _mgr() -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._force_spawn_pattern = _NEVER
    m._evidence_required_tool = ""
    # Signalless by default; individual tests flip these as needed.
    m._turn_has_action_intent = lambda _t: False  # type: ignore[method-assign]
    m._research_wants_artifact = lambda _t: False  # type: ignore[method-assign]
    return m


def _surface() -> dict:
    return {
        "search_web": object(),       # read — must stay
        "wiki-recall": object(),      # read — must stay
        "screenshot": object(),       # read — must stay
        "contact-upsert": object(),   # write — hide on signalless
        # Keyed by the tool's .name attribute (update_profile) — NOT the
        # hyphenated entry-point name. The 2026-07-06 pipeline audit found the
        # constant AND this test both carried "update-profile", so the gate
        # never actually stripped the real tool from a live turn.
        "update_profile": object(),   # write — hide on signalless
        "wiki-ingest": object(),      # write — hide on signalless
        "google_calendar": object(),  # write path — hide on signalless
        "call-contact": object(),     # action (places a call) — hide
        "computer_use": object(),     # existing hidden
        "spawn_worker": object(),     # existing hidden
    }


def test_signalless_turn_hides_write_and_record_tools():
    m = _mgr()
    out = m._hide_action_tools_on_signalless_turn(_surface(), "Was geht ab?")
    for hidden in (
        "contact-upsert", "update_profile", "wiki-ingest",
        "google_calendar", "call-contact", "computer_use", "spawn_worker",
    ):
        assert hidden not in out, hidden
    # Read-only tools are never stripped — the turn stays answerable inline.
    for kept in ("search_web", "wiki-recall", "screenshot"):
        assert kept in out, kept


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
    # signalless hide must NOT strip the mandated write tool, or the say-do
    # write feature breaks again (project_bug_contact_say_do_gap_no_upsert).
    m = _mgr()
    m._evidence_required_tool = "wiki-ingest"
    out = m._hide_action_tools_on_signalless_turn(
        _surface(), "Merk dir, dass ich nach Bora Bora will"  # i18n-allow
    )
    assert "wiki-ingest" in out          # the mandated tool survives
    assert "contact-upsert" not in out   # other write tools still hidden


def test_explicit_subagent_vehicle_keeps_tools():
    m = _mgr()
    m._force_spawn_pattern = re.compile(r"deep dive|subagent", re.I)
    out = m._hide_action_tools_on_signalless_turn(
        _surface(), "Mach einen deep dive und trag das ein"  # i18n-allow
    )
    assert "spawn_worker" in out
    assert "google_calendar" in out
