"""Parity guards for the shared Pipeline/Realtime turn planner."""

from __future__ import annotations

import pytest

from jarvis.brain.turn_planner import TurnPath, TurnReason, plan_turn
from jarvis.core.capabilities import Capability, CapabilityRegistry


@pytest.fixture
def registry() -> CapabilityRegistry:
    value = CapabilityRegistry()
    value.register(
        Capability(
            id="mcp.sap/customer_lookup",
            source="mcp",
            verbs=("lookup", "read"),
            objects=("sap", "customer"),
            description="Read one customer from SAP.",
            risk_tier="safe",
            requires_evidence=True,
        )
    )
    value.register(
        Capability(
            id="mcp.gmail/list_messages",
            source="mcp",
            verbs=("list", "read"),
            objects=("gmail", "inbox", "email"),
            description="Read messages from Gmail.",
            risk_tier="safe",
            requires_evidence=True,
        )
    )
    return value


@pytest.mark.parametrize(
    "utterance",
    [
        "What is the capital of France?",
        "Explain how DNS works.",
        "What is SAP?",
        "How do I open a file in Python?",
        "Tell me a joke.",
    ],
)
def test_timeless_or_instructional_turns_stay_native(
    utterance: str, registry: CapabilityRegistry,
) -> None:
    assert plan_turn(utterance, capability_registry=registry).path is TurnPath.NATIVE_REALTIME


@pytest.mark.parametrize(
    "utterance",
    [
        "What is in my Gmail inbox?",
        "Read the SAP customer record.",
        "Which pull requests are open today?",
        "What is the latest Python release?",
        "Who is my best friend?",
        "Which MCPs are connected?",
        "Use the morning routine skill.",
        "Spawn a Jarvis-Agent for this research.",
        "Call Anna.",
        "Click Save in the browser.",
    ],
)
def test_private_current_connected_and_action_turns_use_orchestrator(
    utterance: str, registry: CapabilityRegistry,
) -> None:
    plan = plan_turn(utterance, capability_registry=registry)
    assert plan.path is TurnPath.ORCHESTRATOR


def test_read_only_dynamic_connector_matches_live_capability(
    registry: CapabilityRegistry,
) -> None:
    plan = plan_turn("What customer is stored in SAP?", capability_registry=registry)
    assert plan.required_capabilities == ("mcp.sap/customer_lookup",)
    assert TurnReason.CONNECTED_DATA in plan.reasons
    assert plan.requires_evidence is True


def test_evidence_domain_routes_even_without_loaded_tool() -> None:
    plan = plan_turn(
        "Are there unread messages?",
        evidence_domains={"email": ("messages", "unread")},
    )
    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.CONNECTED_DATA in plan.reasons


def test_empty_turn_stays_native() -> None:
    assert plan_turn("   ").path is TurnPath.NATIVE_REALTIME


@pytest.mark.parametrize(
    ("utterance", "context"),
    [
        (
            "Was steht im Mainim drin?",  # i18n-allow: exact German forensic STT
            ("We were talking about the user's private Wiki.",),
        ),
        (
            "What does it say?",
            ("The previous turn asked about the connected Gmail inbox.",),
        ),
        (
            "¿Y qué hay ahí?",  # i18n-allow: Spanish speech-input fixture
            ("The previous turn asked about the user's calendar.",),
        ),
    ],
)
def test_elliptical_follow_up_inherits_evidence_domain(
    utterance: str,
    context: tuple[str, ...],
) -> None:
    plan = plan_turn(utterance, context=context)

    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.UNCERTAIN in plan.reasons
    assert plan.requires_evidence is True


def test_unrelated_lookup_does_not_inherit_old_evidence_domain() -> None:
    context = ("The previous turn asked about the user's private Wiki.",)

    assert plan_turn("Who wrote Hamlet?", context=context).path is TurnPath.NATIVE_REALTIME
    assert plan_turn("What time is it?", context=context).path is TurnPath.NATIVE_REALTIME


def test_mission_findings_follow_up_inherits_the_completed_mission() -> None:
    context = (
        "[Trusted Jarvis-Agent mission result] Research finished. "
        'Result metadata: {"mission_id":"019f5ca2-e30f"}',
    )

    plan = plan_turn(
        "Und, was hast du rausgefunden?",  # i18n-allow: exact German speech-input fixture
        context=context,
    )

    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.MISSION in plan.reasons
    assert TurnReason.UNCERTAIN in plan.reasons

    topic_plan = plan_turn(
        "Um was geht's?",  # i18n-allow: exact German speech-input fixture
        context=context,
    )
    assert topic_plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.MISSION in topic_plan.reasons


@pytest.mark.parametrize(
    "utterance",
    [
        # Umlaut verbs: real STT emits umlaut characters while the planner
        # vocabulary is written in transliterated digraphs (oe/ae/ue) —
        # these matched NOTHING before the transliterating _normalize fix.
        "Lösche die Datei vom Desktop.",  # i18n-allow: German speech-input fixture
        "Ändere die Lautstärke.",  # i18n-allow: German speech-input fixture
        "Führe den Befehl aus.",  # i18n-allow: German speech-input fixture
        "Öffne Spotify.",  # i18n-allow: German speech-input fixture
        "Prüfe meine Mails.",  # i18n-allow: German speech-input fixture
    ],
)
def test_umlaut_action_verbs_route_to_orchestrator(utterance: str) -> None:
    plan = plan_turn(utterance)
    assert plan.path is TurnPath.ORCHESTRATOR


@pytest.mark.parametrize(
    "utterance",
    [
        "Switch to the Gemini provider.",
        "Play some music.",
        "Remind me to buy milk tomorrow.",
        "Turn off the lights.",
        "Wechsle den Provider auf Gemini.",  # i18n-allow: German speech-input fixture
        "Merk dir, dass ich morgen Zahnarzt habe.",  # i18n-allow: German fixture
        "Notier dir das bitte.",  # i18n-allow: German speech-input fixture
        "Leg einen Termin für Montag an.",  # i18n-allow: German speech-input fixture
        "Stell den Wecker auf sieben Uhr.",  # i18n-allow: German speech-input fixture
        "Pon música relajante.",  # i18n-allow: Spanish speech-input fixture
        "Recuérdame comprar leche.",  # i18n-allow: Spanish speech-input fixture
        "Apaga la luz.",  # i18n-allow: Spanish speech-input fixture
    ],
)
def test_common_assistant_action_verbs_route_to_orchestrator(utterance: str) -> None:
    plan = plan_turn(utterance)
    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.ACTION in plan.reasons


@pytest.mark.parametrize(
    "utterance",
    [
        "Das ist wirklich merkwürdig.",  # i18n-allow: German speech-input fixture
        "Das war eine Tragödie.",  # i18n-allow: German speech-input fixture
        "Erzähl mir einen Witz.",  # i18n-allow: German speech-input fixture
        "Ich denke, das stimmt so.",  # i18n-allow: German speech-input fixture
        "That was a hard task for everyone.",
        "How is it going?",
        "Guten Morgen.",  # i18n-allow: German speech-input fixture
    ],
)
def test_guarded_non_action_words_stay_native(utterance: str) -> None:
    assert plan_turn(utterance).path is TurnPath.NATIVE_REALTIME


@pytest.mark.parametrize(
    "utterance",
    [
        # One canonical spoken form per capability class that previously
        # stayed native (per-action reachability matrix, 2026-07-13).
        "Wie ist Christophs Telefonnummer?",  # i18n-allow: German fixture
        "What is Christoph's phone number?",
        "Welche Provider gibt es?",  # i18n-allow: German speech-input fixture
        "Teste den Gemini-Provider.",  # i18n-allow: German speech-input fixture
        "Nutz eine andere Stimme.",  # i18n-allow: German speech-input fixture
        "Use a different voice.",
        "Mach lauter.",  # i18n-allow: German speech-input fixture
        "Welche Mikrofone gibt es?",  # i18n-allow: German speech-input fixture
        "Sprich Englisch mit mir.",  # i18n-allow: German speech-input fixture
        "Speak German from now on.",
        "Brich alles ab.",  # i18n-allow: German speech-input fixture
        "Brich die Aufgabe ab.",  # i18n-allow: German speech-input fixture
        "Welche Aufgaben stehen an?",  # i18n-allow: German speech-input fixture
        "Woran arbeitest du gerade?",  # i18n-allow: German speech-input fixture
        "Was haben wir vorhin besprochen?",  # i18n-allow: German fixture
        "What do we know about project Atlas?",
        "What is this element I am pointing at?",
    ],
)
def test_capability_canonical_utterances_route_to_orchestrator(
    utterance: str,
) -> None:
    assert plan_turn(utterance).path is TurnPath.ORCHESTRATOR
