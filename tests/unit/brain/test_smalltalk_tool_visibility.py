"""A smalltalk turn hides the SPAWN vehicles and nothing else.

The 2026-05-01 incident was one tool class: the user said "es geht ab", the
allowlist missed it, and the LLM hallucinated a Jarvis-Agent spawn — Jarvis then
claimed to have started tests it never started. That protection is pinned below.

Hiding the whole surface was the bug on the other side (2026-08-17 review):
smalltalk here is a substring allowlist, so a real request that merely reads as
chit-chat reached the model with no action tool at all and could only be talked
about — the same shape as the live 2026-05-31 failure "Hallo, lies mir vor was
oben links steht", where the brain could only stall.
"""  # i18n-allow: verbatim quotes of the live German utterances
from __future__ import annotations

from jarvis.brain.manager import BrainManager


def _mgr(tools: dict, *, required: str = "", skill_match: object = None) -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._tools = tools
    m._evidence_required_tool = required
    m._skill_turn_match_fallback = skill_match
    return m


def test_smalltalk_hides_the_spawn_vehicles() -> None:
    m = _mgr({"spawn_worker": object(), "multi_spawn": object(), "run_shell": object()})
    override = m._smalltalk_tool_override()
    assert "spawn_worker" not in override
    assert "multi_spawn" not in override


def test_smalltalk_keeps_the_read_only_and_action_tools() -> None:
    # Everything that is not a spawn vehicle survives: the turn may still be a
    # real request the allowlist merely read as chit-chat.
    m = _mgr(
        {
            "screenshot": object(),
            "search_web": object(),
            "computer_use": object(),
            "run_shell": object(),
            "spawn_worker": object(),
        }
    )
    override = m._smalltalk_tool_override()
    assert set(override) == {"screenshot", "search_web", "computer_use", "run_shell"}


def test_smalltalk_turn_with_an_action_keeps_an_action_vehicle() -> None:
    # "Hallo, mach mal Musik an" — a greeting-prefixed request. Even when the
    # allowlist classifies the turn as smalltalk, the model must still see a
    # vehicle it could act with, or the request is impossible by construction.
    m = _mgr({"computer_use": object(), "spotify/play": object(), "spawn_worker": object()})
    override = m._smalltalk_tool_override()
    assert "computer_use" in override
    assert "spotify/play" in override


def test_mandated_tool_survives_even_when_it_is_a_spawn_vehicle() -> None:
    # AD-CLI8: the deterministic layer's mandate outranks the hide.
    spawn = object()
    m = _mgr({"spawn_worker": spawn, "multi_spawn": object()}, required="spawn_worker")
    assert m._smalltalk_tool_override() == {"spawn_worker": spawn}


def test_skill_matched_turn_keeps_run_skill() -> None:
    # AD-S3: a greeting-style trigger ("guten Morgen" -> morning-routine) still
    # invokes its skill. run-skill is not in the hidden set anyway; the explicit
    # keep is belt and braces and this pins it.
    m = _mgr({"run-skill": object(), "spawn_worker": object()}, skill_match=object())
    assert "run-skill" in m._smalltalk_tool_override()


def test_smalltalk_override_never_empties_a_populated_surface() -> None:
    # The core rule: a gate may narrow, never blank. A deployment whose only
    # tools are non-spawn tools keeps all of them.
    m = _mgr({"search_web": object(), "wiki-recall": object()})
    assert set(m._smalltalk_tool_override()) == {"search_web", "wiki-recall"}
