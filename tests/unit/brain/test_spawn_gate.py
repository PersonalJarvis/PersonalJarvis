"""Explicit-delegation gate for LLM-chosen agent spawns (spawn_gate.py).

Maintainer mandate 2026-07-18: a background agent may be spawned by the model
ONLY when the user explicitly asks for one (or confirms a delegation offer one
turn later). The two live regression utterances pinned below come verbatim
from the 2026-07-18 voice sessions (08:25 Gulfstream remark, 08:29 relocation
remark) — both spawned an unrequested agent before the gate existed.
"""
from __future__ import annotations

import pytest

from jarvis.brain.spawn_gate import (
    OFFER_WINDOW,
    SPAWN_VEHICLE_TOOL_NAMES,
    DelegationOfferWindow,
    llm_spawn_allowed,
)


@pytest.fixture(autouse=True)
def _fresh_offer_window():
    OFFER_WINDOW.disarm()
    yield
    OFFER_WINDOW.disarm()


# ── live regressions: conversational turns must NEVER unlock a spawn ──────


@pytest.mark.parametrize(
    "utterance",
    [
        # voice-session 2026-07-18 08:25 — a remark, spawned an agent anyway
        "Kann er jetzt überhaupt, der kann sich ja "  # i18n-allow: live utterance
        "jeden Tag 'ne Golf Stream kaufen.",  # i18n-allow: live utterance
        # voice-session 2026-07-18 08:29 — an intention, spawned an agent anyway
        "Ah, ich will gucken, wo ich als nächstes hinziehe.",  # i18n-allow: live utterance
        "What is the richest place in Europe after Monaco?",
        "Wie viele Milliardäre gibt es in Starnberg?",  # i18n-allow: DE turn fixture
        "Research the best cities to move to.",
        "",
    ],
)
def test_conversational_turn_blocks_spawn(utterance: str) -> None:
    assert llm_spawn_allowed(utterance) is False


# ── explicit requests: naming the vehicle unlocks the spawn ───────────────


@pytest.mark.parametrize(
    "utterance",
    [
        "Spawn an agent to research the best cities.",
        "Spawne einen Subagenten und recherchier das.",  # i18n-allow: DE trigger
        "Lass das einen Gustav Agent machen.",  # i18n-allow: DE trigger
        "Ein Nova-Agent soll das übernehmen.",  # i18n-allow: DE trigger
        "Delegate this to a worker, please.",
        "Mach das im Hintergrund.",  # i18n-allow: DE trigger
        "Do that in the background and tell me later.",
        "Starte eine Mission dafür.",  # i18n-allow: DE trigger
        "Delega esto a un agente.",
    ],
)
def test_explicit_delegation_request_allows_spawn(utterance: str) -> None:
    assert llm_spawn_allowed(utterance) is True


def test_wake_word_brand_is_not_hardcoded() -> None:
    """ANY '<wake-name> Agent' phrasing must match — the brand is dynamic (§4)."""
    for brand in ("Gustav", "Harald", "Nova"):
        OFFER_WINDOW.disarm()
        utterance = f"Frag mal einen {brand} Agent dazu."  # i18n-allow: DE trigger
        assert llm_spawn_allowed(utterance) is True


# ── declines and feature talk must not read as requests ───────────────────


def test_spawn_decline_blocks_even_though_it_names_the_vehicle() -> None:
    assert (
        llm_spawn_allowed("Nee, spawne bitte keinen Subagenten dafür.")  # i18n-allow: DE decline
        is False
    )


def test_auto_spawn_feature_complaint_blocks() -> None:
    assert (
        llm_spawn_allowed(
            "Das Auto-Spawn-Verhalten nervt, das müssen wir fixen."  # i18n-allow: DE feature talk
        )
        is False
    )


# ── the offer window: blocked turn → model offers → short yes unlocks ─────


def test_short_yes_after_blocked_turn_unlocks_exactly_once() -> None:
    remark = "Ich will gucken, wo ich als nächstes hinziehe."  # i18n-allow: live utterance
    assert llm_spawn_allowed(remark) is False
    # model offered delegation; the user's short yes unlocks ONE spawn ...
    assert llm_spawn_allowed("Ja, mach das.") is True  # i18n-allow: DE confirm
    # ... and only one — the window is consumed
    OFFER_WINDOW.disarm()
    assert llm_spawn_allowed("Ja, mach das.") is False  # i18n-allow: DE confirm


def test_yes_in_english_and_spanish_unlocks_too() -> None:
    assert llm_spawn_allowed("figure out where I should move next") is False
    assert llm_spawn_allowed("Yes, go ahead.") is True
    OFFER_WINDOW.disarm()
    assert llm_spawn_allowed("figure out where I should move next") is False
    assert llm_spawn_allowed("Sí, hazlo.") is True


def test_long_sentence_containing_yes_does_not_unlock() -> None:
    question = "Wo soll ich als nächstes hinziehen?"  # i18n-allow: DE turn fixture
    assert llm_spawn_allowed(question) is False
    assert (
        llm_spawn_allowed(
            "Ja, und erzähl mir bitte noch mehr über Monaco."  # i18n-allow: DE counter-example
        )
        is False
    )


def test_veto_closes_the_offer_window_for_good() -> None:
    assert llm_spawn_allowed("Find out where I should move next.") is False
    assert llm_spawn_allowed("No, don't.") is False
    # a later bare yes must not resurrect the declined offer
    assert llm_spawn_allowed("Yes.") is False


def test_blocked_turn_cannot_confirm_itself() -> None:
    # a bare affirmative with no pending offer arms the window with ITSELF —
    # a second model attempt in the same turn must still be blocked
    assert llm_spawn_allowed("Ja bitte.") is False  # i18n-allow: DE confirm
    assert llm_spawn_allowed("Ja bitte.") is False  # i18n-allow: DE confirm


def test_expired_offer_window_does_not_unlock() -> None:
    window = DelegationOfferWindow(ttl_s=-1.0)
    window.arm("find out where I should move next")
    assert window.consume_confirm("yes, do it") is False


def test_explicit_request_disarms_a_stale_offer() -> None:
    assert llm_spawn_allowed("Find out where I should move next.") is False
    assert llm_spawn_allowed("Spawn an agent for something else.") is True
    # the explicit spawn consumed the conversation state — a stray later yes
    # must not unlock another spawn from the stale offer
    assert llm_spawn_allowed("Yes.") is False


# ── parity with the manager's spawn-tool inventory ────────────────────────


def test_vehicle_tool_names_match_manager_inventory() -> None:
    from jarvis.brain.manager import _SPAWN_TOOL_NAMES

    assert SPAWN_VEHICLE_TOOL_NAMES == _SPAWN_TOOL_NAMES
