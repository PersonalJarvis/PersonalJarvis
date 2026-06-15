from __future__ import annotations

import pytest

from jarvis.brain.local_action_gate import (
    LocalActionMode,
    LocalActionPlan,
    LocalToolCall,
    _CapabilityRegistryLike,
    _unsupported_response,
    is_open_app_intent,
    match_local_action,
    requires_external_integration,
)


# ---------------------------------------------------------------------------
# Open-app intent (live bug 2026-06-08, data/jarvis_desktop.log 17:37): the
# conversational "Ich möchte, dass du mir Hermes Agent öffnest, also …"
# force-spawned a heavy sub-agent worker instead of routing to computer-use.
# Opening an app is ALWAYS a computer-use task — a sandboxed worker has no
# desktop. Recognition must be conjugation- and phrasing-robust.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "Ich möchte, dass du mir Hermes Agent öffnest, also",
        "öffne für mich Hermes Agent",
        "Hey Jarvis, öffne mir den Steam Client",
        "mach mir mal Spotify auf",
        "kannst du mir Discord aufmachen",
        "starte mir bitte den Taschenrechner",
        "öffnest du mir kurz Notion",
    ],
)
def test_is_open_app_intent_true(utterance: str) -> None:
    """Any conjugation/phrasing of an open/launch request is an open-app intent."""
    assert is_open_app_intent(utterance) is True, (
        f"open-app request {utterance!r} not recognised as open-app intent"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "wie öffne ich Chrome?",
        "Bau eine Landingpage",
        "öffne einen PR im jarvis-repo",
        "lies die Datei jarvis.toml",
        "implementier eine Email-Validierung",
        "wie spät ist es",
        "analysiere gründlich die Logs",
    ],
)
def test_is_open_app_intent_false(utterance: str) -> None:
    """Instructional questions, external-system work and heavy build/code/file
    tasks are NOT open-app intents (a worker, not computer-use, owns them)."""
    assert is_open_app_intent(utterance) is False, (
        f"{utterance!r} wrongly classified as an open-app intent"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        "Ich möchte, dass du mir Hermes Agent öffnest, also",
        "öffne für mich Hermes Agent",
    ],
)
def test_open_app_intent_non_alias_falls_through_to_brain(utterance: str) -> None:
    """An UNKNOWN-app open command (no alias, not in open_app's KNOWN_APPS) is
    NOT handled deterministically: it must fall through (``None``) so the brain's
    proven ``computer-use`` tool path handles it. Live 2026-06-08: the
    deterministic ``dispatch_to_harness(screenshot)`` path stalled (no [cu]
    steps, 120s TTS-ceiling abort), so open-app intents are kept off it — the
    force-spawn guard (test_open_app_intent_does_not_force_spawn) still keeps
    them off the sub-agent path.

    NOTE: KNOWN apps like Discord now DO take the fast DIRECT open_app path (see
    test_common_messaging_media_apps_take_direct_path) — that path is the clean
    instant launch (app_resolver Start Menu .lnk), NOT the stalling screenshot
    harness, so it is exempt from this fall-through rule."""
    plan = match_local_action(utterance, _registry=None)
    assert plan is None, (
        f"{utterance!r} produced plan {plan!r}; expected None (fall through to "
        f"the brain's computer-use tool)"
    )


@pytest.mark.parametrize(
    ("text", "app"),
    [
        # er-prefixed verb ("eröffne/eröffnet") + trailing politeness ("für mich")
        ("Eröffnet den Explorer für mich", "explorer"),
        ("Eröffne Chrome für mich", "chrome"),
        # trailing politeness after the app name (the leading-filler strip alone
        # left "explorer für mich" unresolved → fell to the computer-use loop)
        ("Öffne den Explorer für mich", "explorer"),
        ("Starte Notepad für mich bitte", "notepad"),
    ],
)
def test_natural_open_phrasings_take_clean_direct_path(text: str, app: str) -> None:
    """Natural open phrasings (er-prefixed verb, trailing politeness) must take
    the clean instant DIRECT open path for a known app — NOT the computer-use
    vision loop. Live 2026-06-08: "Eröffnet den Explorer für mich" fell to the
    vision loop, which wandered (clicked the taskbar, re-opened) and produced a
    confusing end-of-task readback."""
    plan = match_local_action(text, _registry=None)
    assert plan is not None, f"{text!r} produced no plan (fell through to brain)"
    assert plan.mode is LocalActionMode.DIRECT, (
        f"{text!r} produced {plan.mode}, expected DIRECT"
    )
    assert plan.tool_calls[0].args == {"app_name": app}


# ---------------------------------------------------------------------------
# Fake capability registry used to test UNSUPPORTED gate without importing
# ``jarvis.core.capabilities`` (Agent A may not have shipped yet).
# ---------------------------------------------------------------------------

# Verbs that the fake registry considers "action intents".
_FAKE_ACTION_VERBS = frozenset(
    [
        "schick",
        "sende",
        "trag",
        "bestelle",
        "oeffne",
        "klick",
        "lies",
        "starte",
        "mach",
    ]
)

# Verbs for which the fake registry returns a resolved capability (i.e. the
# action IS supported — these must NOT produce UNSUPPORTED).
_FAKE_RESOLVED_VERBS = frozenset(["oeffne", "klick", "starte", "mach"])


class _FakeRegistry:
    """Structural fake that satisfies ``_CapabilityRegistryLike``."""

    def has_action_intent(self, utterance: str) -> bool:
        tokens = utterance.lower().split()
        return bool(_FAKE_ACTION_VERBS & set(tokens))

    def resolve_intent(self, utterance: str) -> object | None:
        tokens = utterance.lower().split()
        if _FAKE_RESOLVED_VERBS & set(tokens):
            return object()  # non-None → capability found
        return None

    def all(self) -> tuple[object, ...]:
        # Non-empty so the production gate's "empty-registry-skip" defence
        # does not bypass the UNSUPPORTED check during these tests.
        return (object(),)


_FAKE_REG = _FakeRegistry()


@pytest.mark.parametrize(
    ("text", "app"),
    [
        ("Oeffne Chrome", "chrome"),
        ("Starte Notepad", "notepad"),
        ("Mach Windows Terminal auf", "wt"),
        ("Hey Jarvis, kannst du Spotify aufmachen?", "spotify"),
        ("Mach Spotify auch", "spotify"),
        ("Mach Spotify App auf", "spotify"),
        ("Mach Spotify App auch", "spotify"),
    ],
)
def test_direct_open_app_commands_return_open_app_plan(text: str, app: str) -> None:
    plan = match_local_action(text)

    assert plan is not None
    assert plan.tool_calls[0].args["app_name"] == app
    assert plan == LocalActionPlan(
        mode=LocalActionMode.DIRECT,
        tool_calls=(LocalToolCall(name="open_app", args={"app_name": app}),),
    )


@pytest.mark.parametrize(
    ("text", "app"),
    [
        # Live bug 2026-06-09 17:36: "Kannst du bitte für mich einmal mein
        # Spotify öffnen?" matched NO verb-first DIRECT pattern (verb at the end,
        # fillers "mein/einmal/für mich"), fell to the router LLM, which did not
        # even call computer_use and went silent → clarifying question. Spotify
        # never opened. Verb-at-end / filler-heavy open phrasings must still take
        # the deterministic DIRECT path when the app is known.
        ("Kannst du bitte für mich einmal mein Spotify öffnen?", "spotify"),
        ("kannst du mein Spotify öffnen", "spotify"),
        ("Spotify aufmachen", "spotify"),
        ("öffnest du mir mal Discord", "discord"),
        ("mach mir mein WhatsApp auf", "whatsapp"),
        ("kannst du Chrome öffnen", "chrome"),
    ],
)
def test_verb_at_end_open_phrasings_take_direct_path(text: str, app: str) -> None:
    """Any open-app intent in any phrasing — including verb-at-end and
    filler-heavy — must reach the deterministic DIRECT open_app path for a known
    app, NOT fall through to the unreliable router LLM (which produced silence +
    a clarifying question, with the app never opening). Cross-platform: open_app
    → app_resolver branches per OS."""
    plan = match_local_action(text, _registry=None)
    assert plan is not None, f"{text!r} fell through to the brain"
    assert plan.mode is LocalActionMode.DIRECT, f"{text!r} → {plan.mode}, want DIRECT"
    assert plan.tool_calls[0].args == {"app_name": app}


@pytest.mark.parametrize(
    "text",
    [
        "ich will Spotify nicht öffnen",
        "bitte Chrome nicht starten",
        "mach mir kein Spotify auf",
        "öffne Discord lieber nicht",
    ],
)
def test_negated_open_never_launches(text: str) -> None:
    """A NEGATED open command must NEVER trigger a DIRECT launch (regression for
    the 2026-06-09 review finding: the verb-at-end fallback scans the whole
    utterance for a known app and would otherwise launch Spotify for 'ich will
    Spotify nicht öffnen'). Falling through to the brain (None) is the safe
    outcome — with clarify off by default the user is not nagged either."""
    plan = match_local_action(text, _registry=None)
    # Strong guard: a negated open must fall all the way through to the brain —
    # neither a DIRECT launch nor a COMPUTER_USE offload.
    assert plan is None, f"{text!r} wrongly produced a local plan: {plan}"


@pytest.mark.parametrize(
    "text",
    [
        # Verb-at-end multi-step: open AND do more → the computer-use loop
        # (offload path), so the follow-up action ("play Shape of You") is not
        # silently dropped. Verb-first compounds already route here via
        # _COMPOUND_OPEN_CONTROL_RE; this covers the "kannst du X öffnen und …"
        # shape that the verb-first regex misses.
        "kannst du Spotify öffnen und Shape of You spielen",
        "öffne mir mal Chrome und geh auf Amazon",
    ],
)
def test_verb_at_end_compound_open_routes_to_computer_use(text: str) -> None:
    plan = match_local_action(text, _registry=None)
    assert plan is not None, f"{text!r} fell through to the brain"
    assert plan.mode is LocalActionMode.COMPUTER_USE, f"{text!r} → {plan.mode}, want COMPUTER_USE"


@pytest.mark.parametrize(
    "text",
    [
        "navigiere zu youtube und such ein Video",
        "navigiere zu amazon",
        "mach einen Screenshot",
        "mach mal einen Screenshot vom Bildschirm",
        "schließ das Fenster",
        "minimier das Fenster",
        "maximier den Browser",
        "wechsel zum nächsten Tab",
        "zieh die Datei nach links",
    ],
)
def test_general_desktop_control_routes_to_computer_use(text: str) -> None:
    """General Computer-Use commands (navigate / screenshot / window-ops / drag)
    the narrow GUI-verb + compound-open patterns missed must route
    DETERMINISTICALLY to the CU harness offload — NOT depend on the LLM talker
    calling computer_use. Live regression 2026-06-09: brain.primary=codex (CLI
    OAuth path) drops ALL tools and can never emit a tool_call, so every CU task
    that fell to the talker went silent — ~30% of common CU commands. Making the
    gate comprehensive makes CU work regardless of the talker's tool capability."""
    plan = match_local_action(text, _registry=None)
    assert plan is not None, f"{text!r} fell through to the (tool-less) brain"
    assert plan.mode is LocalActionMode.COMPUTER_USE, f"{text!r} → {plan.mode}, want COMPUTER_USE"


# ---------------------------------------------------------------------------
# Sub-agent research mission misroute (live bug 2026-06-15, sessions.db session
# 236877e6 14:12): "Ich möchte, dass du eine Sub-Agent-Mission startest und dann
# recherchierst …" was classified COMPUTER_USE and run on the screenshot harness
# ("ich erledige das direkt am Bildschirm"), which then 403'd on its provider so
# the user heard "das hat nicht geklappt". Root cause: the open/launch verb
# regex (`start\w*`) overcaptures "Mission STARTEST" as an app-open, and
# `_NOT_OPEN_APP_RE` had no research/sub-agent/mission vocabulary to veto it.
# An explicit sub-agent / research / mission request is heavy worker work — it
# must fall through (None) to the force-spawn path, NEVER computer-use.
# ---------------------------------------------------------------------------


_SUBAGENT_RESEARCH_UTTERANCES = [
    # The exact live STT utterance that broke (line-712 open-app fallback:
    # open-verb "startest" + "und").
    "Ich möchte, dass du für mich bitte eine Sub-Agent-Mission startest "
    "und dann genau recherchierst, was die aktuellsten KI-News sind",
    # Adjacent hole (RC3): a verb-first compound "Starte … und …" reaches the
    # same misroute via _COMPOUND_OPEN_CONTROL_RE / _looks_like_desktop_control.
    "Starte eine Sub-Agent-Mission und recherchiere die neuesten KI-News",
    "Starte eine gründliche Recherche und fasse die KI-News zusammen",
    # The retry that worked live ONLY because it lacked "und"; it must stay
    # correct (None) AND no longer count as an open-app intent.
    "Kannst du mir bitte eine Sub-Agent-Mission starten, in der du "
    "recherchierst, was die aktuellen KI-News sind",
]


@pytest.mark.parametrize("utterance", _SUBAGENT_RESEARCH_UTTERANCES)
def test_subagent_research_mission_is_not_open_app_intent(utterance: str) -> None:
    """An explicit sub-agent / research / mission request is heavy worker work,
    NOT an app-open — even though it contains the launch verb "starte/startest".
    `is_open_app_intent` is the single predicate that BOTH the COMPUTER_USE gate
    and the force-spawn veto (manager.py:2530) consult, so it must return False
    or the turn is hijacked to the screen harness (live bug 2026-06-15)."""
    assert is_open_app_intent(utterance) is False, (
        f"{utterance!r} wrongly classified as an open-app intent"
    )


@pytest.mark.parametrize("utterance", _SUBAGENT_RESEARCH_UTTERANCES)
def test_subagent_research_mission_falls_through_to_force_spawn(utterance: str) -> None:
    """The deterministic local-action gate must NOT produce a plan for an
    explicit sub-agent research mission: it falls through (None) so the brain's
    force-spawn path dispatches a real research worker. Before the fix this
    returned COMPUTER_USE (screenshot harness) → "ich erledige das am Bildschirm"
    → provider 403 → spoken failure (sessions.db 236877e6, 2026-06-15)."""
    plan = match_local_action(utterance, _registry=None)
    assert plan is None, (
        f"{utterance!r} wrongly produced a local plan {plan} — must fall "
        "through to force-spawn, never the screen harness"
    )


@pytest.mark.parametrize(
    "text",
    [
        "schreib mir ein Gedicht über den Herbst",
        "was ist die Hauptstadt von Frankreich",
        "erzähl mir einen Witz",
        "wie navigiere ich zu den Einstellungen",
        "wie mache ich einen Screenshot",
        "erklär mir was ein Tab ist",
        # Informational / non-take mentions of "screenshot" must NOT launch a CU
        # mission (review finding 2026-06-09: the bare-noun pattern over-routed).
        "ich habe einen Screenshot gemacht",
        "zeig mir den letzten Screenshot",
        "schick mir einen Screenshot per Mail",
        # Non-desktop "verschieb"/"zieh" objects stay brain answers.
        "verschieb den Termin auf Montag",
        "zieh das bitte in Betracht",
    ],
)
def test_non_control_utterances_do_not_route_to_computer_use(text: str) -> None:
    """Precision guard: questions, how-to, and content-generation must NOT be
    mistaken for desktop control — they stay normal brain answers (None here)."""
    plan = match_local_action(text, _registry=None)
    if plan is not None:
        assert plan.mode is not LocalActionMode.COMPUTER_USE, (
            f"{text!r} wrongly routed to COMPUTER_USE: {plan}"
        )


@pytest.mark.parametrize(
    ("text", "app"),
    [
        ("Öffne Discord", "discord"),
        ("öffne Discord für mich", "discord"),
        ("kannst du mir Discord aufmachen", "discord"),
        ("Mach Slack auf", "slack"),
        ("Starte Telegram", "telegram"),
        ("Öffne WhatsApp", "whatsapp"),
        ("Mach mir Signal auf", "signal"),
        ("Öffne Teams", "teams"),
        ("Starte VLC", "vlc"),
        ("Öffne Steam", "steam"),
    ],
)
def test_common_messaging_media_apps_take_direct_path(text: str, app: str) -> None:
    """Common chat/media apps a voice user opens by name must take the clean
    instant DIRECT open path (open_app → app_resolver, which resolves Squirrel
    installs like Discord/Slack via the Start Menu .lnk fallback added
    2026-06-09), NOT the slower LLM-driven computer-use loop. Live bug
    2026-06-09: these were absent from the gate's _APP_ALIASES, so "öffne
    Discord" fell through to the brain — which opened it via computer_use but,
    producing no narration, was answered with "Wie meinst du das genau?".
    open_app's KNOWN_APPS already accepts every app here."""
    plan = match_local_action(text, _registry=None)
    assert plan is not None, f"{text!r} fell through to the brain"
    assert plan.mode is LocalActionMode.DIRECT, f"{text!r} → {plan.mode}, want DIRECT"
    assert plan.tool_calls[0].args == {"app_name": app}


@pytest.mark.parametrize(
    ("text", "app"),
    [
        # Live repro 2026-05-25 ("Oeffne mir Chrome"): the filler word "mir"
        # broke canonicalisation ("mir chrome" had no alias) so the command
        # fell through to the brain instead of launching locally.
        ("Oeffne mir Chrome", "chrome"),
        ("\u00d6ffne mir Chrome", "chrome"),
        ("Mach mir Chrome auf", "chrome"),
        ("Starte mir den Chrome Browser", "chrome"),
        ("Oeffne mir bitte Firefox", "firefox"),
        ("Oeffne den Editor", "notepad"),
        ("Starte den Rechner", "calc"),
        ("Oeffne mir den Explorer", "explorer"),
    ],
)
def test_filler_words_are_stripped_before_alias_lookup(text: str, app: str) -> None:
    # _registry=None isolates the canonicalisation path (skips the capability
    # gate) so this asserts the alias/filler logic independent of seeding.
    plan = match_local_action(text, _registry=None)

    assert plan == LocalActionPlan(
        mode=LocalActionMode.DIRECT,
        tool_calls=(LocalToolCall(name="open_app", args={"app_name": app}),),
    )


@pytest.mark.parametrize(
    "text",
    [
        "Oeffne drei Terminals",
        "\u00d6ffne drei Terminals",
        "Mach drei Terminals auf",
        "Starte 3 Terminals",
    ],
)
def test_terminal_count_commands_return_repeated_terminal_open_plan(text: str) -> None:
    plan = match_local_action(text)

    assert plan == LocalActionPlan(
        mode=LocalActionMode.DIRECT,
        tool_calls=(
            LocalToolCall(name="open_app", args={"app_name": "wt"}),
            LocalToolCall(name="open_app", args={"app_name": "wt"}),
            LocalToolCall(name="open_app", args={"app_name": "wt"}),
        ),
    )


def test_terminal_count_is_capped_at_five_for_direct_gate() -> None:
    plan = match_local_action("Oeffne 9 Terminals")

    assert plan is not None
    assert len(plan.tool_calls) == 5
    assert {call.name for call in plan.tool_calls} == {"open_app"}
    assert {call.args["app_name"] for call in plan.tool_calls} == {"wt"}


@pytest.mark.parametrize(
    "text",
    [
        "Klick auf den Senden Button",
        "Schreib hallo in das ChatGPT Eingabefeld",
        "Oeffne das rote Fenster links und suche nach Bugs",
    ],
)
def test_visual_target_commands_return_computer_use_plan(text: str) -> None:
    plan = match_local_action(text)

    assert plan == LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE,
        harness="screenshot",
        prompt=text,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Wie kann ich Chrome oeffnen?",
        "Bau mir eine Landingpage",
        "Analysiere diese PR tief",
        "Was ist ein Browser?",
    ],
)
def test_non_local_how_to_and_heavy_commands_return_none(text: str) -> None:
    assert match_local_action(text) is None


# ----------------------------------------------------------------------
# ADR-0016 L2 — orb-recovery voice commands
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Orb zurück",
        "Orb zurueck",
        "orb zurück.",
        "Orb zurück!",
        "Hey Jarvis, Orb zurück",
        "Hey Jarvis, wo bist du?",
        "wo bist du",
        "wo bist du.",
        "reset orb",
        "Reset den Orb",
        "hey jarvis, reset orb!",
    ],
)
def test_orb_reset_commands_return_direct_reset_plan(text: str) -> None:
    """Tight phrase list (BUG-027 / ADR-0016 L2): each variant must
    produce a DIRECT plan that calls ``reset_orb_position``."""
    plan = match_local_action(text)
    assert plan is not None, f"no match for: {text!r}"
    assert plan == LocalActionPlan(
        mode=LocalActionMode.DIRECT,
        tool_calls=(LocalToolCall(name="reset_orb_position", args={}),),
    )


@pytest.mark.parametrize(
    "text",
    [
        # Conversational / unrelated queries that look superficially similar
        # but must NOT trigger the orb reset. This corpus is the regression
        # guard for the regex anchoring at ``^`` and ``$[?.!]*``.
        "wo bist du gerade",
        "wo bist du gerade?",
        "weißt du wo der Bus ist",
        "wo ist der Orb?",
        "kannst du den Orb verschieben",
        "warum bist du da?",
        "wo bin ich",
        "reset chrome",  # similar verb, different target
        "der orb ist weg",
        "wo war der orb",
    ],
)
def test_orb_reset_false_positive_corpus(text: str) -> None:
    """The regex MUST NOT match generic questions that contain similar
    fragments but are not an orb-reset command. False positives here
    would steal the user's prompt from the brain pipeline."""
    plan = match_local_action(text)
    if plan is None:
        return
    # If the gate matched at all, it must not be the reset path.
    assert plan.tool_calls and plan.tool_calls[0].name != "reset_orb_position", (
        f"false positive: {text!r} matched orb-reset"
    )


# ----------------------------------------------------------------------
# Mascot respawn voice commands (mirror of ADR-0016 L2 for the overlay
# supervisor — BUG-012 class, cap-fired / hidden / hung subprocess).
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Hey Jarvis, Maskottchen wieder auftauchen",
        "Hey Jarvis, Maskottchen wieder auftauchen?",
        "Hey Jarvis, Maskottchen zurück",
        "Maskottchen zurück",
        "Maskottchen weg",
        "Maskottchen kommt zurück",
        "Hey Jarvis, Maskottchen reset",
        "Hey Jarvis, Maskottchen respawn",
        "Hey Jarvis, Maskottchen spawnen",
        "Hey Jarvis, spawne das Maskottchen",
        "Hey Jarvis, respawn the mascot",
        "Hey Jarvis, mascot back",
        "Hey Jarvis, Mascot come back",
        "Wo ist das Maskottchen?",
        "Hey Jarvis, Spawner",
        "Spawner",
        "Hey Jarvis, der Spawner",
    ],
)
def test_mascot_respawn_phrases_dispatch_respawn_mascot(text: str) -> None:
    """Every phrasing in the user's vocabulary for "bring the mascot back"
    must produce a DIRECT plan that calls ``respawn_mascot``."""
    plan = match_local_action(text)
    assert plan is not None, f"no match for: {text!r}"
    assert plan == LocalActionPlan(
        mode=LocalActionMode.DIRECT,
        tool_calls=(LocalToolCall(name="respawn_mascot", args={}),),
    )


@pytest.mark.parametrize(
    "text",
    [
        # Conversational queries that mention mascot/spawn but are NOT a
        # respawn command. Regression guard for the regex anchoring.
        "Spawne mir mal ein Terminal",
        "Hey Jarvis, das Maskottchen-Konzept ist toll",
        "Wo ist mein Schlüssel?",
        "Was macht der Spawner intern?",
        "respawn the server",
    ],
)
def test_mascot_respawn_false_positive_corpus(text: str) -> None:
    plan = match_local_action(text)
    if plan is None:
        return
    assert plan.tool_calls and plan.tool_calls[0].name != "respawn_mascot", (
        f"false positive: {text!r} matched mascot-respawn"
    )


# ---------------------------------------------------------------------------
# UNSUPPORTED gate — capability-coupling spec §Layer 2, insertion point (a)
#
# All tests below inject ``_FakeRegistry`` via the ``_registry`` kwarg so
# they run without ``jarvis.core.capabilities`` being present (Agent A may
# not have merged yet).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "schick eine email an sam@gmx.de",
        "Schick eine Email an sam@gmx.de",
        "trag einen termin morgen 10 uhr ein",
        "Trag einen Termin morgen 10 Uhr ein",
        "sende eine whatsapp an mama",
        "Sende eine WhatsApp an Mama",
        "bestelle eine pizza",
        "Bestelle eine Pizza",
    ],
)
def test_hard_negatives_return_unsupported(text: str) -> None:
    """Action intents with no registered capability must return UNSUPPORTED.

    These are the acceptance criteria from the capability-coupling spec:
    email, calendar, WhatsApp, pizza-ordering.  The fake registry treats
    their leading verbs as action-intents but resolves them to None.
    """
    plan = match_local_action(text, _registry=_FAKE_REG)

    assert plan is not None, f"gate returned None for: {text!r}"
    assert plan.mode == LocalActionMode.UNSUPPORTED, (
        f"expected UNSUPPORTED, got {plan.mode} for: {text!r}"
    )
    assert plan.response_text is not None, "response_text must be populated"
    assert plan.response_text.strip() != "", "response_text must be non-empty"


def test_unsupported_response_de_contains_required_phrase() -> None:
    """German response must contain the spec-mandated phrase."""
    msg = _unsupported_response("schick eine email", "de")
    assert "Das kann ich noch nicht" in msg
    assert "Werkzeug" in msg


def test_unsupported_response_en_contains_required_phrase() -> None:
    """English response must contain the spec-mandated phrase."""
    msg = _unsupported_response("send an email", "en")
    assert "I can't do that yet" in msg
    assert "registered tool" in msg


def test_unsupported_response_lang_en_in_plan() -> None:
    """lang='en' propagates correctly into response_text."""
    plan = match_local_action("schick eine email", lang="en", _registry=_FAKE_REG)

    assert plan is not None
    assert plan.mode == LocalActionMode.UNSUPPORTED
    assert plan.response_text is not None
    assert "I can't do that yet" in plan.response_text


# ---------------------------------------------------------------------------
# Positive cases — these MUST NOT return UNSUPPORTED even with a registry
# that knows the verbs (because resolve_intent returns non-None for them).
# ---------------------------------------------------------------------------


def test_open_chrome_returns_direct_not_unsupported() -> None:
    """'oeffne chrome' resolves to the open_app capability → DIRECT, not UNSUPPORTED."""
    plan = match_local_action("oeffne chrome", _registry=_FAKE_REG)

    assert plan is not None
    assert plan.mode == LocalActionMode.DIRECT
    assert plan.tool_calls[0].name == "open_app"
    assert plan.tool_calls[0].args["app_name"] == "chrome"


def test_smalltalk_returns_none_not_unsupported() -> None:
    """'wie spät ist es' has no action verb → registry gate is skipped → None."""
    plan = match_local_action("wie spät ist es", _registry=_FAKE_REG)

    assert plan is None, (
        f"smalltalk should fall through to brain (None), got {plan}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Öffne bitte WhatsApp für mich und schreibe mal.",
        "oeffne WhatsApp und schreib Mama hallo",
        "oeffne den Rechner und rechne sieben plus acht",
        "starte das Mailprogramm und sende eine Nachricht",
        "scroll runter",
        "tippe hallo und druecke enter",
    ],
)
def test_desktop_control_routes_to_computer_use_not_unsupported(text: str) -> None:
    """Live bug 2026-05-25: "oeffne WhatsApp und schreib" got the canned
    "das kann ich noch nicht" refusal. Compound open-and-operate / GUI verbs
    must route to the computer-use loop even when the registry resolves nothing
    (resolve_intent is None for these in _FAKE_REG) — computer-use is the
    universal GUI integration, never an UNSUPPORTED refusal.
    """
    plan = match_local_action(text, _registry=_FAKE_REG)

    assert plan is not None
    assert plan.mode == LocalActionMode.COMPUTER_USE, (
        f"{text!r} should route to computer-use, got {plan.mode}"
    )
    assert plan.harness == "screenshot"


def test_write_me_a_poem_is_not_desktop_control() -> None:
    """'schreib mir ein Gedicht' is content generation, not GUI control —
    it must fall through to the brain (None), not the computer-use loop."""
    plan = match_local_action("schreib mir ein Gedicht", _registry=_FAKE_REG)
    assert plan is None


def test_computer_use_click_returns_computer_use_not_unsupported() -> None:
    """'klick auf den roten button' is a visual-target → COMPUTER_USE, not UNSUPPORTED."""
    plan = match_local_action("klick auf den roten button", _registry=_FAKE_REG)

    assert plan is not None
    assert plan.mode == LocalActionMode.COMPUTER_USE


# ---------------------------------------------------------------------------
# No-registry fallback — when registry is explicitly None the gate is skipped
# and the existing behaviour (DIRECT / COMPUTER_USE / None) is preserved.
# ---------------------------------------------------------------------------


def test_no_registry_preserves_existing_direct_match() -> None:
    """Without a registry, 'oeffne chrome' still returns DIRECT."""
    plan = match_local_action("oeffne chrome", _registry=None)

    assert plan is not None
    assert plan.mode == LocalActionMode.DIRECT


def test_no_registry_unsupported_intent_returns_none() -> None:
    """Without a registry, an unsupported intent like 'schick eine email'
    falls through all existing gates and returns None (brain handles it)."""
    plan = match_local_action("schick eine email an test@example.com", _registry=None)

    # No existing pattern matches this → gate returns None, brain is invoked.
    assert plan is None


# ---------------------------------------------------------------------------
# Browser+URL fast-path (2026-06-10 latency collapse, plan Task 2): "open
# <browser> and go to <site>" is ONE deterministic argv launch — browsers
# accept a URL argument on every OS, and open_app already whitelists
# http(s):// targets. The vision-LLM loop took ~3 minutes for exactly this
# goal shape (live log 20:46).
# ---------------------------------------------------------------------------


class TestBrowserUrlFastPath:
    def test_open_browser_and_goto_site_is_direct(self) -> None:
        plan = match_local_action("öffne chrome und gehe auf x.com")
        assert plan is not None
        assert plan.mode is LocalActionMode.DIRECT
        call = plan.tool_calls[0]
        assert call.name == "open_app"
        assert call.args["app_name"] == "chrome"
        assert call.args["arguments"] == "https://x.com"

    def test_open_browser_and_goto_site_en(self) -> None:
        plan = match_local_action("open firefox and go to github.com", lang="en")
        assert plan is not None
        assert plan.mode is LocalActionMode.DIRECT
        assert plan.tool_calls[0].args == {
            "app_name": "firefox", "arguments": "https://github.com",
        }

    def test_bare_goto_site_opens_url_directly(self) -> None:
        plan = match_local_action("geh auf x.com")
        assert plan is not None
        assert plan.mode is LocalActionMode.DIRECT
        assert plan.tool_calls[0].args["app_name"] == "https://x.com"

    def test_existing_url_scheme_is_preserved(self) -> None:
        plan = match_local_action("öffne chrome und gehe auf https://x.com")
        assert plan is not None
        assert plan.tool_calls[0].args["arguments"] == "https://x.com"

    def test_negated_open_stays_off_the_fast_path(self) -> None:
        plan = match_local_action("öffne chrome bitte nicht und geh auf x.com")
        assert plan is None or plan.mode is not LocalActionMode.DIRECT

    def test_howto_question_stays_brain(self) -> None:
        plan = match_local_action("wie gehe ich auf x.com")
        assert plan is None or plan.mode is not LocalActionMode.DIRECT

    def test_browser_with_followup_work_still_goes_to_cu(self) -> None:
        # Site + further UI work must keep the CU loop (it has to act there).
        plan = match_local_action(
            "öffne chrome und gehe auf x.com und poste einen tweet"
        )
        assert plan is not None
        assert plan.mode is LocalActionMode.COMPUTER_USE

    def test_plain_sentence_with_domain_noun_stays_brain(self) -> None:
        # A domain mention without a goto/open verb shape must not launch.
        plan = match_local_action("was haeltst du von x.com")
        assert plan is None or plan.mode is not LocalActionMode.DIRECT


# ---------------------------------------------------------------------------
# requires_external_integration — real-world booking/transaction requests no
# generic sub-agent worker can fulfil (no travel/lodging/ticketing tool exists).
# Live gap 2026-06-14: "book me a trip from Melbourne to Tokyo" was NOT caught
# (the noun list had mail/calendar/Spotify/food-delivery but no travel nouns),
# so it force-spawned a worker that produced no file -> 3-loop empty-diff
# critic_loop_exhausted FAIL, and the spawn ACK falsely promised the booking.
# These nouns route it to the honest inline refusal instead (still gated on
# resolve_intent being None downstream, so a real travel MCP would win).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "book me a trip from Melbourne to Tokyo",
        "Buche mir eine Reise von Melbourne nach Tokio",
        "buche mir einen Flug nach Tokio",
        "book a flight to London for me",
        "reserviere mir ein Hotel in Berlin",
        "book a hotel in Paris",
        "reserviere einen Tisch im Restaurant heute Abend",
        "book a table at a restaurant tonight",
        "bestell mir ein Ticket für den Zug nach München",
    ],
)
def test_booking_transactions_require_external_integration(text: str) -> None:
    assert requires_external_integration(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # Coding/research that merely NAMES travel — generic sub-agent work,
        # must NOT be refused (build/research verb, or no dispatch verb at all).
        "schreib ein Skript das günstige Flüge sucht",
        "baue mir einen Reise-Budget-Rechner",
        "recherchier welche Stadt sich für eine Reise nach Australien lohnt",
        "erzähl mir etwas über meine letzte Reise",
        "was kostet ungefähr ein Flug nach Tokio",
    ],
)
def test_travel_mentions_without_dispatch_verb_stay_generic(text: str) -> None:
    assert requires_external_integration(text) is False
