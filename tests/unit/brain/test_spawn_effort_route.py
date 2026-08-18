"""The EFFORT route: Jarvis may delegate heavy work without a magic word.

Maintainer mandate 2026-08-18: "the goal is that he just DOES things without
being told. Right now he doesn't even do the things you DO tell him." The
explicit-only rule (mandate 2026-07-21) meant a plain "build me a website with
Flask and a start page" produced an offer and waited — never work.

The second route added for that is deterministic and conservative: a request
for a multi-step artefact may delegate on its own; a question, a lookup, a chat
turn and anything cheap may not. Direction of error is asymmetric on purpose —
these tests pin BOTH sides, because a spurious background agent is expensive
and surprising while a missed delegation only costs an inline answer.

Every earlier protection is re-pinned here: the 2026-05-01 hallucinated-spawn
forensic, the decline and feature-talk guards, the offer/confirmation window,
fail-closed on an empty turn, and ``strict`` mode keeping its old behaviour
verbatim.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.brain.spawn_gate import (
    OFFER_WINDOW,
    active_force_spawn_mode,
    effort_route_enabled,
    effort_warrants_delegation,
    llm_spawn_allowed,
)
from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.config import (
    DEFAULT_FORCE_SPAWN_MODE,
    FORCE_SPAWN_MODE_BALANCED,
    FORCE_SPAWN_MODE_PERMISSIVE,
    FORCE_SPAWN_MODE_STRICT,
    BrainRoutingConfig,
    JarvisConfig,
    normalize_force_spawn_mode,
)


class _ModeOnlyManager:
    """The one attribute ``active_force_spawn_mode`` reads off the live manager."""

    def __init__(self, mode: str) -> None:
        self.force_spawn_mode = mode


@pytest.fixture
def in_mode():
    """Run a block with a given force-spawn mode registered as the live one."""

    def _apply(mode: str) -> None:
        runtime_refs.set_brain_manager(_ModeOnlyManager(mode))

    yield _apply
    runtime_refs._reset_for_tests()


@pytest.fixture(autouse=True)
def _fresh_offer_window():
    OFFER_WINDOW.disarm()
    yield
    OFFER_WINDOW.disarm()


class _FakeSpawnTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _Inert:
    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("no execution in a classification test")


def _manager(mode: str = FORCE_SPAWN_MODE_BALANCED) -> BrainManager:
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = mode
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeSpawnTool()},
        tool_executor=_Inert(),  # type: ignore[arg-type]
    )


# ── (a) a multi-step artefact brief delegates without a magic word ─────────

#: The maintainer's own example, plus the same shape in the other supported
#: languages and in code-work form. None of these names an agent, a worker, a
#: mission, "spawn", "delegate" or "in the background".
_HEAVY_BRIEFS = [
    "Bau mir eine Website mit Flask und einer Startseite",  # i18n-allow: DE brief
    "Baue mir eine Flask-App mit Login, Datenbank und einer Startseite",  # i18n-allow: DE brief
    "Erstelle mir eine vollständige Dokumentation für das Projekt "  # i18n-allow: DE brief
    "mit Architektur und Deployment",  # i18n-allow: DE brief
    "Recherchier die besten E-Bikes unter 3000 Euro und schreib mir "  # i18n-allow: DE brief
    "einen Bericht als Markdown-Datei",  # i18n-allow: DE brief
    "Kannst du mir eine Landingpage mit Kontaktformular und "  # i18n-allow: DE brief
    "Impressum bauen?",  # i18n-allow: DE brief
    "Refactor the auth module and split the tests into their own package",
    "Build me a dashboard with a login page and a database behind it",
]


@pytest.mark.parametrize("utterance", _HEAVY_BRIEFS)
def test_heavy_brief_delegates_without_delegation_vocabulary(
    utterance: str, in_mode
) -> None:
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    assert effort_warrants_delegation(utterance) is True
    assert llm_spawn_allowed(utterance) is True


def test_the_maintainers_example_also_force_spawns_deterministically() -> None:
    """A tool-incapable talker must reach the same answer as the LLM gate."""
    utterance = "Bau mir eine Website mit Flask und einer Startseite"  # i18n-allow: DE brief
    assert _manager()._should_force_spawn(utterance) is True


# ── (b) a plain question never delegates ──────────────────────────────────

_QUESTIONS = [
    "Was ist ein Verbrenner-Motor?",  # i18n-allow: DE question
    "Wie viele Milliardäre gibt es in Starnberg?",  # i18n-allow: DE question
    "Wie baue ich eine Website mit Flask und einer Startseite?",  # i18n-allow: DE question
    "What is the richest place in Europe after Monaco?",
    "How do I write a report with charts and a summary?",
    "Which database should I use for a website with a login?",
]


@pytest.mark.parametrize("utterance", _QUESTIONS)
def test_question_never_delegates(utterance: str, in_mode) -> None:
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    assert effort_warrants_delegation(utterance) is False
    assert llm_spawn_allowed(utterance) is False


# ── (c) a cheap one-step request never delegates ──────────────────────────

_CHEAP_REQUESTS = [
    "Erstell ein Skript",  # i18n-allow: DE request
    "Bau mir eine Website",  # i18n-allow: DE request
    "Schreib das in eine Datei",  # i18n-allow: DE request
    "Schreib mir eine kurze Zusammenfassung von dem Artikel",  # i18n-allow: DE request
    "Erstell mir bitte ein kleines Skript das die Uhrzeit ausgibt",  # i18n-allow: DE request
    "Zeig mir das Wetter",  # i18n-allow: DE request
    "Write a quick script that prints the time",
    "Just create a simple HTML page with a heading",
]


@pytest.mark.parametrize("utterance", _CHEAP_REQUESTS)
def test_cheap_request_never_delegates(utterance: str, in_mode) -> None:
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    assert effort_warrants_delegation(utterance) is False
    assert llm_spawn_allowed(utterance) is False


def test_one_scope_signal_is_not_enough() -> None:
    """Where the unsure line sits: one named part, no spawn; two, spawn."""
    assert effort_warrants_delegation("Baue mir ein Login") is False  # i18n-allow: DE
    assert (
        effort_warrants_delegation(
            "Baue mir ein Login und eine Datenbank dafür"  # i18n-allow: DE
        )
        is True
    )


def test_finished_work_is_reported_not_requested(in_mode) -> None:
    """A build verb in the past tense is a report, never a brief."""
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    said = (
        "Ich habe die Website schon gebaut und die Startseite geschrieben"  # i18n-allow: DE
    )
    assert effort_warrants_delegation(said) is False
    assert llm_spawn_allowed(said) is False


# ── (d) the explicit vocabulary route still works ─────────────────────────


@pytest.mark.parametrize(
    "utterance",
    [
        "Spawn an agent to research the best cities.",
        "Spawne einen Subagenten und recherchier das.",  # i18n-allow: DE trigger
        "Delegate this to a worker, please.",
        "Mach das im Hintergrund.",  # i18n-allow: DE trigger
        "Delega esto a un agente.",
    ],
)
def test_explicit_vocabulary_still_unlocks_the_spawn(
    utterance: str, in_mode
) -> None:
    in_mode(FORCE_SPAWN_MODE_STRICT)  # even with the effort route switched off
    assert llm_spawn_allowed(utterance) is True


# ── (e) the offer / confirmation path still works ─────────────────────────


def test_offer_and_confirmation_still_unlock_exactly_one_spawn(in_mode) -> None:
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    # A substantive turn the effort test does NOT take: blocked, offer armed.
    assert llm_spawn_allowed("Find me the best cities to move to.") is False
    # The user's short yes on the next turn unlocks the spawn once.
    assert llm_spawn_allowed("Ja, mach das.") is True  # i18n-allow: DE confirmation
    assert llm_spawn_allowed("Ja, mach das.") is False  # i18n-allow: DE confirmation


def test_declining_the_offer_closes_the_window(in_mode) -> None:
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    assert llm_spawn_allowed("Find me the best cities to move to.") is False
    assert llm_spawn_allowed("Nein, lass mal.") is False  # i18n-allow: DE veto
    assert llm_spawn_allowed("Ja, mach das.") is False  # i18n-allow: DE confirmation


def test_a_delegated_heavy_brief_clears_a_pending_offer(in_mode) -> None:
    """An allowed spawn disarms the window — the documented gate contract."""
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    assert llm_spawn_allowed("Find me the best cities to move to.") is False
    assert (
        llm_spawn_allowed(
            "Bau mir eine Website mit Flask und einer Startseite"  # i18n-allow: DE brief
        )
        is True
    )
    assert llm_spawn_allowed("Ja, mach das.") is False  # i18n-allow: DE confirmation


# ── (f) the 2026-05-01 forensic must still never spawn ────────────────────


@pytest.mark.parametrize(
    "utterance",
    [
        # voice session 2026-04-30 22:38 — the smalltalk turn on which the
        # model hallucinated a spawn and Jarvis then claimed to have started
        # tests it never started.
        "es geht ab",  # i18n-allow: forensic utterance
        # voice session 2026-07-18 08:25 / 08:29 — conversational remarks that
        # spawned an unrequested agent before the gate existed.
        "Kann er jetzt überhaupt, der kann sich ja "  # i18n-allow: forensic utterance
        "jeden Tag 'ne Golf Stream kaufen.",  # i18n-allow: forensic utterance
        "Ah, ich will gucken, wo ich als nächstes hinziehe.",  # i18n-allow: forensic utterance
        "Research the best cities to move to.",
    ],
)
def test_live_forensic_turns_still_block(utterance: str, in_mode) -> None:
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    assert effort_warrants_delegation(utterance) is False
    assert llm_spawn_allowed(utterance) is False


def test_empty_turn_still_fails_closed(in_mode) -> None:
    """No words, no judgement, no background agent — see the gate docstring."""
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    for empty in ("", "   ", "\n"):
        assert effort_warrants_delegation(empty) is False
        assert llm_spawn_allowed(empty) is False


def test_decline_beats_the_effort_route(in_mode) -> None:
    """A heavy brief with an explicit refusal must not delegate."""
    in_mode(FORCE_SPAWN_MODE_BALANCED)
    said = (
        "Bau mir eine Website mit Flask und einer Startseite, "  # i18n-allow: DE brief
        "aber spawn dafür keinen Subagenten"  # i18n-allow: DE decline
    )
    assert llm_spawn_allowed(said) is False


# ── mode wiring: the switch is read, and strict keeps its old behaviour ────


def test_balanced_is_the_shipped_default() -> None:
    assert DEFAULT_FORCE_SPAWN_MODE == FORCE_SPAWN_MODE_BALANCED
    assert BrainRoutingConfig().force_spawn_mode == FORCE_SPAWN_MODE_BALANCED
    assert JarvisConfig().brain.routing.force_spawn_mode == FORCE_SPAWN_MODE_BALANCED


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("strict", FORCE_SPAWN_MODE_STRICT),
        ("  STRICT ", FORCE_SPAWN_MODE_STRICT),
        ("permissive", FORCE_SPAWN_MODE_PERMISSIVE),
        ("balanced", FORCE_SPAWN_MODE_BALANCED),
        ("", DEFAULT_FORCE_SPAWN_MODE),
        (None, DEFAULT_FORCE_SPAWN_MODE),
        ("wharrgarbl", DEFAULT_FORCE_SPAWN_MODE),
    ],
)
def test_mode_normalization(raw: object, expected: str) -> None:
    assert normalize_force_spawn_mode(raw) == expected


def test_a_configured_strict_mode_survives_normalization() -> None:
    """The escape hatch: a user who wants explicit-only keeps it."""
    config = JarvisConfig()
    config.brain.routing = BrainRoutingConfig(force_spawn_mode="strict")
    assert _manager_from(config).force_spawn_mode == FORCE_SPAWN_MODE_STRICT


def _manager_from(config: JarvisConfig) -> BrainManager:
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeSpawnTool()},
        tool_executor=_Inert(),  # type: ignore[arg-type]
    )


def test_strict_mode_switches_the_effort_route_off(in_mode) -> None:
    in_mode(FORCE_SPAWN_MODE_STRICT)
    assert effort_route_enabled() is False
    assert active_force_spawn_mode() == FORCE_SPAWN_MODE_STRICT
    utterance = "Bau mir eine Website mit Flask und einer Startseite"  # i18n-allow: DE brief
    # The classifier still answers the same — only the ROUTE is off.
    assert effort_warrants_delegation(utterance) is True
    assert llm_spawn_allowed(utterance) is False
    assert _manager(FORCE_SPAWN_MODE_STRICT)._should_force_spawn(utterance) is False


def test_balanced_and_permissive_both_grant_the_effort_route(in_mode) -> None:
    for mode in (FORCE_SPAWN_MODE_BALANCED, FORCE_SPAWN_MODE_PERMISSIVE):
        in_mode(mode)
        assert effort_route_enabled() is True
        assert active_force_spawn_mode() == mode


def test_gate_falls_back_to_the_shipped_default_without_a_live_manager() -> None:
    runtime_refs._reset_for_tests()
    assert active_force_spawn_mode() == DEFAULT_FORCE_SPAWN_MODE


def test_manager_property_is_the_one_live_accessor() -> None:
    assert _manager(FORCE_SPAWN_MODE_BALANCED).force_spawn_mode == (
        FORCE_SPAWN_MODE_BALANCED
    )
    assert _manager("nonsense").force_spawn_mode == DEFAULT_FORCE_SPAWN_MODE
