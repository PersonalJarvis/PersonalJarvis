"""Parity guards for the shared Pipeline/Realtime turn planner."""

from __future__ import annotations

import pytest

from jarvis.brain.turn_planner import (
    PUBLIC_FACT_GROUNDING_CAPABILITY,
    PUBLIC_FACT_GROUNDING_TIMEOUT_S,
    GroundingFailurePolicy,
    TurnPath,
    TurnReason,
    is_public_fact_question,
    plan_turn,
)
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
        (
            "Ach, ich versuche gerade Suggestionen zu studieren. "  # i18n-allow
            "Wie würdest du mir am besten dabei helfen, "  # i18n-allow
            "Suggestionen anzuwenden und konkreter zu benutzen, "  # i18n-allow
            "um meine Mitmenschen dazu zu bringen, "  # i18n-allow
            "meine Interessen zu verfolgen?"  # i18n-allow
        ),
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
        (
            "No, just tell me more about what Jarvis can do on the Jarvis "
            "desktop app. What you can do there."
        ),
        "Was kannst du in der Jarvis Desktop-App?",  # i18n-allow: spoken fixture
        "¿Qué puedes hacer en la aplicación de Jarvis?",  # i18n-allow: spoken fixture
    ],
)
def test_capability_overview_follow_up_stays_in_the_live_conversation(
    registry: CapabilityRegistry,
    utterance: str,
) -> None:
    """A request to explain the desktop app is conversation, not a request to
    inspect live connectors. Prior answers may mention weather, inboxes, and
    online data; those nouns must not turn the follow-up into a slow delegate."""
    plan = plan_turn(
        utterance,
        capability_registry=registry,
        context=(
            "I can check weather, read email, open apps, and find information online.",
        ),
    )

    assert plan.path is TurnPath.NATIVE_REALTIME


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


@pytest.mark.parametrize(
    "utterance",
    [
        # Live incident 2026-07-16 11:24: the temporal filler "gerade" was a
        # current-data marker, so a plain world-knowledge follow-up was
        # force-delegated through the router brain (16 s of web searches
        # ending in a spoken error). Fillers alone must never delegate.
        "Wo wohnt der gerade?",  # i18n-allow: German speech-input fixture
        "Wie viel Geld hat Peter Thiel?",  # i18n-allow: German fixture
        "Was hat der eben gesagt?",  # i18n-allow: German speech-input fixture
    ],
)
def test_temporal_filler_world_knowledge_stays_native(utterance: str) -> None:
    assert plan_turn(utterance).path is TurnPath.NATIVE_REALTIME


@pytest.mark.parametrize(
    "utterance",
    [
        # Strong freshness markers must keep forcing the orchestrator even
        # after the colloquial fillers were retired.
        "Wie ist das Wetter gerade?",  # i18n-allow: German speech-input fixture
        "Was gibt es heute für Nachrichten?",  # i18n-allow: German fixture
        "What is the latest Python release?",
    ],
)
def test_strong_current_markers_still_use_orchestrator(utterance: str) -> None:
    plan = plan_turn(utterance)

    assert plan.path is TurnPath.ORCHESTRATOR
    assert plan.requires_public_fact_grounding is True
    assert plan.required_capabilities.count(PUBLIC_FACT_GROUNDING_CAPABILITY) == 1
    assert plan.public_fact_grounding_timeout_s == PUBLIC_FACT_GROUNDING_TIMEOUT_S
    assert plan.public_fact_grounding_attempt_limit == 1
    assert plan.grounding_failure_policy is GroundingFailurePolicy.HONEST_UNCERTAINTY


@pytest.mark.parametrize(
    "utterance",
    [
        "How high is Mount Everest?",
        "How fast can a cheetah run?",
        "What year was NASA founded?",
        "How long did Muhammad Ali live?",
        "Who is the current president of the WBC?",
        "Wie hoch ist der Eiffelturm?",  # i18n-allow: German speech fixture
        "Wie lange lebte Muhammad Ali?",  # i18n-allow: German speech fixture
        "Wer ist aktuell Praesident des WBC?",  # i18n-allow: German speech fixture
        "¿En que ano se fundo la NASA?",  # i18n-allow: Spanish speech fixture
        "¿Quien es el presidente actual del WBC?",  # i18n-allow: Spanish speech fixture
    ],
)
def test_declared_small_model_public_facts_ground_once(utterance: str) -> None:
    assert is_public_fact_question(utterance) is True

    plan = plan_turn(
        utterance,
        tool_names=(PUBLIC_FACT_GROUNDING_CAPABILITY,),
        requires_public_fact_grounding=True,
    )

    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.PUBLIC_FACT in plan.reasons
    assert plan.required_capabilities.count(PUBLIC_FACT_GROUNDING_CAPABILITY) == 1
    assert plan.requires_evidence is True
    assert plan.requires_public_fact_grounding is True
    assert plan.public_fact_grounding_attempt_limit == 1


def test_hosted_evergreen_public_fact_keeps_native_fast_path() -> None:
    plan = plan_turn("Who wrote Hamlet?")

    assert plan.path is TurnPath.NATIVE_REALTIME
    assert plan.requires_public_fact_grounding is False
    assert plan.required_capabilities == ()
    assert plan.public_fact_grounding_timeout_s is None
    assert plan.public_fact_grounding_attempt_limit == 0
    assert plan.grounding_failure_policy is None


def test_public_search_capability_is_deduplicated() -> None:
    plan = plan_turn(
        "Search for the latest Python release.",
        tool_names=(PUBLIC_FACT_GROUNDING_CAPABILITY,),
        requires_public_fact_grounding=True,
    )

    assert plan.required_capabilities.count(PUBLIC_FACT_GROUNDING_CAPABILITY) == 1
    assert plan.public_fact_grounding_attempt_limit == 1


@pytest.mark.parametrize(
    "utterance",
    [
        "Find the Python executable on disk.",
        "Which process is listening on port 8000 on this machine?",
        "Check the current Jarvis settings.",
        "What is inside the README.md file?",
        "Find the local config file.",
        "What is in my Gmail inbox?",
        "Which meetings are in my calendar today?",
        "What is on my screen?",
    ],
)
def test_private_or_connected_facts_never_leak_to_public_search(
    utterance: str,
) -> None:
    assert is_public_fact_question(utterance) is False

    plan = plan_turn(utterance, requires_public_fact_grounding=True)

    assert plan.requires_public_fact_grounding is False
    assert PUBLIC_FACT_GROUNDING_CAPABILITY not in plan.required_capabilities


@pytest.mark.parametrize(
    "utterance",
    [
        "Which pull requests are open today?",
        "Which repositories are private?",
        "Which deployments failed today?",
        "Which inboxes have unread mail?",
    ],
)
def test_plural_connected_domains_never_route_to_public_search(
    utterance: str,
) -> None:
    plan = plan_turn(utterance, requires_public_fact_grounding=True)

    assert TurnReason.CONNECTED_DATA in plan.reasons
    assert plan.requires_public_fact_grounding is False
    assert PUBLIC_FACT_GROUNDING_CAPABILITY not in plan.required_capabilities


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
    "utterance",
    [
        "Take a screenshot.",
        "What is that?",
        "Schau dir bitte meinen Bildschirm an.",  # i18n-allow: DE input
        "Analiza esta captura de pantalla.",  # i18n-allow: ES input
    ],
)
def test_screen_context_turns_use_the_orchestrator(utterance: str) -> None:
    plan = plan_turn(utterance)
    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.SCREEN_CONTEXT in plan.reasons
    assert plan.requires_evidence is True


@pytest.mark.parametrize(
    "utterance",
    [
        "How can you look at my screen?",
        "Wie kann ich dir meinen Bildschirm zeigen?",  # i18n-allow: DE input
        "Como puedes ver mi pantalla?",  # i18n-allow: ES input
    ],
)
def test_screen_context_how_to_questions_stay_native(utterance: str) -> None:
    assert plan_turn(utterance).path is TurnPath.NATIVE_REALTIME


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


def test_plugin_location_follow_up_inherits_named_live_tool() -> None:
    previous = (
        "Please use my Gmail plugin and tell me which messages need attention.",
    )

    follow_up = "Wo liegt es? Ich habe es auch als Plugin installiert."  # i18n-allow
    plan = plan_turn(
        follow_up,
        context=previous,
        tool_names=("gmail", "streamline/query"),
    )

    assert plan.required_capabilities == ("gmail",)
    assert TurnReason.CONNECTED_DATA in plan.reasons
    assert TurnReason.UNCERTAIN in plan.reasons


def test_plugin_action_follow_up_inherits_named_live_tool() -> None:
    plan = plan_turn(
        "Where is it? I installed it as a plugin.",
        context=("Use Streamline to create a ticket for the incident.",),
        tool_names=(
            "jira/create_ticket",
            "notion/create_page",
            "streamline/query",
        ),
    )

    assert plan.required_capabilities == ("streamline/query",)
    assert TurnReason.UNCERTAIN in plan.reasons


@pytest.mark.parametrize(
    "follow_up",
    [
        "Woran liegt es? Ich habe es als Plugin installiert.",  # i18n-allow
        "Warum ist es fehlgeschlagen?",  # i18n-allow
    ],
)
def test_german_reason_follow_up_inherits_named_live_tool(follow_up: str) -> None:
    plan = plan_turn(
        follow_up,
        context=("Please use my Gmail plugin and read the latest message.",),
        tool_names=("gmail", "streamline/query"),
    )

    assert plan.required_capabilities == ("gmail",)
    assert TurnReason.UNCERTAIN in plan.reasons


@pytest.mark.parametrize(
    ("tool_name", "previous"),
    [
        ("google-calendar/list_events", "Use Google Calendar to find the event."),
        ("plugin-streamline/query", "Use Streamline to create a ticket."),
        ("mcp.notebook/search", "Use Notebook to find the note."),
    ],
)
def test_context_matches_hyphenated_or_prefixed_namespace_identity(
    tool_name: str,
    previous: str,
) -> None:
    plan = plan_turn(
        "Where is it? I installed it as a plugin.",
        context=(previous,),
        tool_names=(tool_name, "jira/create_ticket"),
    )

    assert plan.required_capabilities == (tool_name,)


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
        "Es legt immer noch krass.",  # i18n-allow: exact German ASR fixture
        "Es laggt im Spiel immer noch krass.",  # i18n-allow: German fixture
        "Das Spiel ist gut.",  # i18n-allow: German speech-input fixture
    ],
)
def test_german_game_and_lag_reports_stay_native(utterance: str) -> None:
    """ASR-homophone reports must not pay the orchestrator latency penalty."""
    assert plan_turn(utterance).path is TurnPath.NATIVE_REALTIME


@pytest.mark.parametrize(
    "utterance",
    [
        "Spiel Musik.",  # i18n-allow: German speech-input fixture
        "Leg einen Termin an.",  # i18n-allow: German speech-input fixture
        "Öffne das Spiel.",  # i18n-allow: German speech-input fixture
        "Du spielst jetzt Musik.",  # i18n-allow: German speech-input fixture
        "Du legst jetzt einen Termin an.",  # i18n-allow: German fixture
    ],
)
def test_german_game_and_lay_commands_still_use_orchestrator(utterance: str) -> None:
    assert plan_turn(utterance).path is TurnPath.ORCHESTRATOR


@pytest.mark.parametrize(
    "utterance",
    [
        # Live forensic 2026-07-17 08:36/08:47: every one of these
        # conversational turns was force-delegated through the router brain
        # and cost 12-21 s of silence. First-person deliberation, opinion
        # questions, why-rants, and assistant-dayplan smalltalk must stay on
        # the native realtime model.
        (
            "Kann ich dagegen irgendwas rechtlich machen? "  # i18n-allow
            "Ich habe vor, eine Klage einzureichen."  # i18n-allow: forensic fixture
        ),
        "Muss ich jetzt alle Verträge ändern?",  # i18n-allow: forensic fixture
        (
            "Soll ich es einfach kaufen oder einen Profi dahinschicken, "  # i18n-allow
            "der das für mich abwickelt?"  # i18n-allow: forensic fixture
        ),
        "Was willst du mir empfehlen, konkret?",  # i18n-allow: forensic fixture
        (
            "Wieso kriegen meine Mitarbeiter auf meine Kosten "  # i18n-allow
            "einen freien Tag?"  # i18n-allow: forensic fixture
        ),
        (
            "Wo glaubst du, kann ich den Wagen am besten unterstellen? "  # i18n-allow
            "Meine Garage ist schon voll."  # i18n-allow: forensic fixture
        ),
        "Was machst du morgen genau?",  # i18n-allow: forensic fixture
        "Should I just buy it or send a professional instead?",
        "What do you think I should do about my employees?",
    ],
)
def test_deliberation_opinion_and_smalltalk_stay_native(utterance: str) -> None:
    assert plan_turn(utterance).path is TurnPath.NATIVE_REALTIME


@pytest.mark.parametrize(
    "utterance",
    [
        # Counter-proofs: explicit tasking, personal-fact recall, connected
        # objects, and mission status keep delegating even when a modal or
        # possessive appears in the sentence.
        "Kannst du bitte die Datei vom Desktop löschen?",  # i18n-allow: fixture
        "Ich möchte, dass du mir einen Termin für Montag anlegst.",  # i18n-allow
        "Wie heißt meine Frau?",  # i18n-allow: German speech-input fixture
        "Soll ich die E-Mail an Anna jetzt schicken?",  # i18n-allow: fixture
        "Woran arbeitest du gerade?",  # i18n-allow: German speech-input fixture
        "Who is my best friend?",
    ],
)
def test_tasking_recall_and_connected_turns_still_use_orchestrator(
    utterance: str,
) -> None:
    assert plan_turn(utterance).path is TurnPath.ORCHESTRATOR


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


@pytest.mark.parametrize(
    "utterance",
    [
        # Live complaint 2026-07-21: calendar trivia ("Was ist morgen für  # i18n-allow
        # ein Tag?") was force-delegated twice — 12-34 s of silence for a
        # question the realtime model answers itself from the current-date
        # line in its session instructions. Includes the real ASR garble
        # ("What is tomorrow for day?") from the live transcript.
        "Was ist morgen für ein Tag?",  # i18n-allow: forensic fixture
        "Was ist heute für ein Datum?",  # i18n-allow: forensic fixture
        "Ich will wissen, was morgen f\u00fcr ein Tag ist.",  # i18n-allow: exact regression
        "Welcher Tag ist morgen?",  # i18n-allow: German speech-input fixture
        "Welcher Wochentag ist heute?",  # i18n-allow: German fixture
        "Welches Datum haben wir?",  # i18n-allow: German speech-input fixture
        "Der Wievielte ist heute?",  # i18n-allow: German speech-input fixture
        "I want to know what day tomorrow is.",
        "What day is it today?",
        "What day is tomorrow?",
        "What is tomorrow for day?",
        "What's the date?",
        "Quiero saber qu\u00e9 d\u00eda es ma\u00f1ana.",  # i18n-allow: embedded Spanish
        "¿Qué día es hoy?",
    ],
)
def test_calendar_trivia_stays_native(utterance: str) -> None:
    assert plan_turn(utterance).path is TurnPath.NATIVE_REALTIME


@pytest.mark.parametrize(
    "utterance",
    [
        # Counter-proofs: a time word next to REAL evidence keeps delegating —
        # weather/news are current data, a planned day is the user's calendar,
        # and a dated meeting lookup still needs connected evidence.
        "Wie ist das Wetter morgen?",  # i18n-allow: German speech-input fixture
        "Was ist morgen geplant?",  # i18n-allow: German speech-input fixture
        "Was steht heute in den Nachrichten?",  # i18n-allow: German fixture
        "What's the date of my meeting tomorrow?",
        "What's in the news today?",
    ],
)
def test_time_words_with_real_evidence_still_delegate(utterance: str) -> None:
    assert plan_turn(utterance).path is TurnPath.ORCHESTRATOR


@pytest.mark.parametrize(
    "utterance",
    [
        "Wie ist das Wetter morgen?",  # i18n-allow: German speech-input fixture
        "Was steht heute in den Nachrichten?",  # i18n-allow: German fixture
        "What's in the news today?",
    ],
)
def test_calendar_trivia_suppression_does_not_hide_fresh_public_facts(
    utterance: str,
) -> None:
    plan = plan_turn(utterance)

    assert plan.requires_public_fact_grounding is True
    assert plan.required_capabilities.count(PUBLIC_FACT_GROUNDING_CAPABILITY) == 1


# --------------------------------------------------------------------------- #
# A turn naming an open coding terminal                                        #
# --------------------------------------------------------------------------- #
# Live failure 2026-07-27 16:53 (Realtime): with a terminal called Dana running,
# "Was hat Dana gemacht?" was routed natively and the live model answered that
# it did not know which person Dana was. The utterance carries no lookup verb,
# no action, no possessive and no connected domain — the static vocabularies
# structurally cannot hold the evidence, because the call-signs are chosen per
# workspace at runtime. The NAME is the evidence, so it is passed in.

PANES = ("Alex", "Dana", "Logan")


@pytest.mark.parametrize(
    "utterance",
    [
        "Was hat Dana gemacht?",  # the verbatim live failure
        "Was hat Logan gebaut?",  # i18n-allow: German speech-input fixture
        "Was ist mit Dana?",  # i18n-allow: German speech-input fixture
        "Dana?",
        "What did Dana do?",
        "Que hizo Dana?",
        # A garbled call-sign still has to reach the orchestrator: only it can
        # ask "did you mean Dana?".
        "Was hat Danna gemacht?",  # i18n-allow: German speech-input fixture
    ],
)
def test_a_turn_naming_an_open_terminal_delegates(utterance: str) -> None:
    assert plan_turn(utterance).path is TurnPath.NATIVE_REALTIME, (
        "precondition: without a workspace this turn is native"
    )
    plan = plan_turn(utterance, workspace_names=PANES)
    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.WORKSPACE in plan.reasons
    # The pane's transcript is evidence the live model never holds.
    assert plan.requires_evidence is True


@pytest.mark.parametrize(
    "utterance",
    [
        # A person who merely shares a first name with a pane.
        "Was hat Dana Schmidt gemacht?",  # i18n-allow: German speech-input fixture
        # Somebody out in the world.
        "Was hat Elon Musk gemacht?",  # i18n-allow: German speech-input fixture
        # Plain conversation while a workspace happens to be open.
        "Erzaehl mir einen Witz",  # i18n-allow: German speech-input fixture
    ],
)
def test_an_open_workspace_does_not_capture_unrelated_turns(utterance: str) -> None:
    assert plan_turn(utterance, workspace_names=PANES).path is (
        TurnPath.NATIVE_REALTIME
    )


def test_no_open_workspace_changes_nothing() -> None:
    """The parameter is inert when no terminals are running."""
    assert plan_turn("Was hat Dana gemacht?", workspace_names=()).path is (
        TurnPath.NATIVE_REALTIME
    )


@pytest.mark.parametrize(
    "utterance",
    [
        # No possessive anywhere — the ownership+lookup rule never saw these,
        # so they were answered natively by a model that cannot know the
        # answer (recall audit 2026-08-04). Real umlaut spellings on purpose.
        "Wann war ich zuletzt beim Zahnarzt?",  # i18n-allow: German speech-input fixture
        "Weißt du noch, wo wir letztes Jahr im Urlaub waren?",  # i18n-allow: German fixture
        "Wie hieß das Restaurant nochmal?",  # i18n-allow: German speech-input fixture
        "Was ist nochmal bei dem Serverumzug passiert?",  # i18n-allow: German fixture
        "Do you remember what I told you about the boat?",
        "When was I last in Berlin?",
    ],
)
def test_recall_of_the_users_past_delegates_as_private_data(utterance: str) -> None:
    """Explicit recall of the user's own past is strong evidence: only the
    orchestrator (Wiki memory / awareness episodes) can answer it."""
    plan = plan_turn(utterance)
    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.PRIVATE_DATA in plan.reasons
    assert plan.requires_evidence is True


@pytest.mark.parametrize(
    "utterance",
    [
        # First-person past forms that are NOT unambiguous recall — they occur
        # in ordinary storytelling and must not pay a delegation round trip.
        "Ich habe gestern einen tollen Film gesehen.",  # i18n-allow: German speech-input fixture
        "Gestern war ich im Kino und es war super.",  # i18n-allow: German speech-input fixture
        "I have been thinking about that a lot.",
    ],
)
def test_storytelling_past_tense_stays_native(utterance: str) -> None:
    assert plan_turn(utterance).path is TurnPath.NATIVE_REALTIME
