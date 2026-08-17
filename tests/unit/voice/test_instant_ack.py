"""Instant acknowledgment core: work classes, pools, and the contextual validator."""

from __future__ import annotations

import pytest

from jarvis.brain.turn_planner import plan_turn
from jarvis.voice.instant_ack import (
    IMMEDIATE_DELAY_S,
    SHORT_GRACE_S,
    WorkClass,
    all_instant_ack_lines,
    contextual_ack_is_valid,
    instant_ack_pool,
    normalize_ack_line,
    pick_instant_ack_text,
    plan_instant_ack,
)

# ---------------------------------------------------------------------------
# plan_instant_ack — the trigger is the deterministic turn plan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("utterance", "work_class", "delay_s"),
    [
        (
            "Was gibt es aktuell für Bugatti Divos in Europa?",
            WorkClass.RESEARCH,
            IMMEDIATE_DELAY_S,
        ),  # i18n-allow: speech input
        ("What's the weather tomorrow?", WorkClass.RESEARCH, IMMEDIATE_DELAY_S),
        (
            "Wie viele Termine habe ich morgen?",
            WorkClass.RESEARCH,
            IMMEDIATE_DELAY_S,
        ),  # i18n-allow: speech input
        (
            "Was steht in meinen Notizen zu Albel?",
            WorkClass.PERSONAL,
            SHORT_GRACE_S,
        ),  # i18n-allow: speech input
        (
            "Weißt du noch, wann ich in Paris war?",
            WorkClass.PERSONAL,
            SHORT_GRACE_S,
        ),  # i18n-allow: speech input
        (
            "Was ist auf meinem Bildschirm?",
            WorkClass.SCREEN,
            IMMEDIATE_DELAY_S,
        ),  # i18n-allow: speech input
        ("What's on my screen?", WorkClass.SCREEN, IMMEDIATE_DELAY_S),
        ("Mach Spotify auf", WorkClass.ACTION, SHORT_GRACE_S),  # i18n-allow: speech input
        ("Open Spotify", WorkClass.ACTION, SHORT_GRACE_S),
        (
            "Öffne den Browser und such das Wetter raus",
            WorkClass.ACTION,
            SHORT_GRACE_S,
        ),  # i18n-allow: speech input
    ],
)
def test_plan_maps_orchestrator_turns_to_work_classes(utterance, work_class, delay_s):
    plan = plan_instant_ack(plan_turn(utterance), utterance)
    assert plan is not None
    assert plan.work_class is work_class
    assert plan.delay_s == pytest.approx(delay_s)
    assert plan.contextual is (work_class is WorkClass.ACTION)


@pytest.mark.parametrize(
    "utterance",
    [
        "Hallo, wie geht's?",  # i18n-allow: speech input
        "Wann wurde Einstein geboren?",  # i18n-allow: speech input
        "Sei still",  # i18n-allow: speech input
        "Thanks!",
    ],
)
def test_plain_conversation_gets_no_ack(utterance):
    assert plan_instant_ack(plan_turn(utterance), utterance) is None


def test_none_plan_gets_no_ack():
    assert plan_instant_ack(None, "anything") is None


# ---------------------------------------------------------------------------
# pools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["de", "en", "es"])
@pytest.mark.parametrize(
    "work_class", [WorkClass.RESEARCH, WorkClass.PERSONAL, WorkClass.SCREEN, WorkClass.MISSION]
)
def test_every_pooled_class_has_varied_lines_in_every_locale(work_class, language):
    pool = instant_ack_pool(work_class, language, agent_brand="Test-Agent")
    assert len(pool) >= 3
    assert len(set(pool)) == len(pool)
    for line in pool:
        assert line.strip() == line
        assert line.endswith((".", "!"))
        assert len(line.split()) <= 12


def test_action_pool_is_empty_by_design():
    for language in ("de", "en", "es"):
        assert instant_ack_pool(WorkClass.ACTION, language) == ()
        assert pick_instant_ack_text(WorkClass.ACTION, language) == ""


def test_mission_lines_carry_the_dynamic_agent_brand_never_a_product_name():
    for language in ("de", "en", "es"):
        for line in instant_ack_pool(WorkClass.MISSION, language, agent_brand="Athena-Agent"):
            assert "Athena-Agent" in line
            assert "{agent}" not in line
            assert "Jarvis" not in line


def test_unknown_language_falls_back_to_english():
    assert instant_ack_pool(WorkClass.RESEARCH, "fr") == instant_ack_pool(WorkClass.RESEARCH, "en")


def test_pick_avoids_back_to_back_repeats():
    seen = [pick_instant_ack_text(WorkClass.RESEARCH, "de") for _ in range(12)]
    for first, second in zip(seen, seen[1:], strict=False):
        assert first != second


def test_all_lines_are_normalized_for_transcript_matching():
    lines = all_instant_ack_lines("de", agent_brand="Athena-Agent")
    assert normalize_ack_line("Ich suche das gerade online.") in lines  # i18n-allow: voice output
    assert normalize_ack_line("Ich Suche Das Gerade ONLINE") in lines  # i18n-allow: voice output
    assert (
        normalize_ack_line("Das gebe ich einem Athena-Agent weiter.") in lines
    )  # i18n-allow: voice output


# ---------------------------------------------------------------------------
# contextual validator — intent grammar + the user's own words only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "utterance", "language"),
    [
        ("Ich öffne Spotify.", "Mach Spotify auf", "de"),  # i18n-allow: voice output / speech input
        ("Ich mache Spotify für dich auf.", "Mach Spotify auf", "de"),  # i18n-allow
        (
            "Ich stelle die Stimme auf Cartesia um.",
            "Stell die Stimme auf Cartesia um",
            "de",
        ),  # i18n-allow
        (
            "Ich sage T1, dass er die Tests laufen lassen soll.",
            "Sag T1, er soll die Tests laufen lassen",
            "de",
        ),  # i18n-allow
        (
            "Ich merke mir, dass Alex Signal bevorzugt.",
            "Merk dir: Alex bevorzugt Signal",
            "de",
        ),  # i18n-allow
        ("Ich rufe die Praxis an.", "Ruf die Praxis an und mach einen Termin", "de"),  # i18n-allow
        ("I'm opening Spotify.", "Open Spotify", "en"),
        ("Opening Spotify for you now.", "Open Spotify", "en"),
        ("I'm telling T1 to run the tests.", "Tell T1 to run the tests", "en"),
        ("Abro Spotify.", "Abre Spotify", "es"),
    ],
)
def test_contextual_intent_lines_pass(text, utterance, language):
    assert contextual_ack_is_valid(text, utterance=utterance, language=language)


@pytest.mark.parametrize(
    ("text", "utterance", "language", "why"),
    [
        ("Spotify ist offen.", "Mach Spotify auf", "de", "copula result claim"),  # i18n-allow
        (
            "Ich habe Spotify geöffnet.",
            "Mach Spotify auf",
            "de",
            "perfect-tense result",
        ),  # i18n-allow
        (
            "Ich öffne Spotify und spiele dein Lieblingslied.",
            "Mach Spotify auf",
            "de",
            "invented content",
        ),  # i18n-allow
        (
            "Ich rufe die Praxis an, Termin um 15 Uhr.",
            "Ruf die Praxis an",
            "de",
            "invented number",
        ),  # i18n-allow
        ("Mache ich.", "Mach Spotify auf", "de", "stock filler, no subject"),  # i18n-allow
        (
            "Ich kümmere mich drum.",
            "Mach Spotify auf",
            "de",
            "stock filler, no subject",
        ),  # i18n-allow
        ("Soll ich Spotify öffnen?", "Mach Spotify auf", "de", "question"),  # i18n-allow
        ("Spotify is open.", "Open Spotify", "en", "copula result claim"),
        ("I've opened Spotify.", "Open Spotify", "en", "perfect-tense result"),
        ("On it.", "Open Spotify", "en", "stock filler"),
        ("I'm sending that to terminal one.", "Tell T1 to run the tests", "en", "invented words"),
        ("Done, Spotify is running.", "Open Spotify", "en", "completion"),
        ("Spotify ya está abierto.", "Abre Spotify", "es", "copula result claim"),
        ("I'm opening Spotify, Sir.", "Open Spotify", "en", "forbidden honorific"),
        ("The worker is opening Spotify.", "Open Spotify", "en", "forbidden internal name"),
        ("", "Open Spotify", "en", "empty"),
        (
            "I'm opening Spotify and then I'm also going to check the weather "
            "for you right now okay.",
            "Open Spotify",
            "en",
            "too long",
        ),
    ],
)
def test_contextual_lines_that_claim_invent_or_fill_are_rejected(text, utterance, language, why):
    assert not contextual_ack_is_valid(text, utterance=utterance, language=language), why


def test_extra_allowed_words_admit_the_agent_brand():
    text = "I'm handing Spotify to an Athena-Agent."
    assert not contextual_ack_is_valid(text, utterance="Open Spotify", language="en")
    assert contextual_ack_is_valid(
        text, utterance="Open Spotify", language="en", extra_allowed_words=("Athena-Agent",)
    )


def test_a_digit_from_the_request_is_allowed_a_new_one_is_not():
    assert contextual_ack_is_valid(
        "I'm opening terminal 3.", utterance="Open terminal 3", language="en"
    )
    assert not contextual_ack_is_valid(
        "I'm opening terminal 4.", utterance="Open terminal 3", language="en"
    )
