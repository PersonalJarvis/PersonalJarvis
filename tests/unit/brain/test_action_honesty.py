"""Execution-state guards for action promises in model output."""

from __future__ import annotations

import pytest

from jarvis.brain.action_honesty import (
    action_not_started_phrase,
    has_deferred_action_claim,
    has_false_completion_claim,
    has_unbacked_action_claim,
    replace_unbacked_action_claim,
)
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from tests.fixtures.brain.fake_brain import FakeBrain


@pytest.mark.parametrize(
    "text",
    [
        (
            "Das kann ich gerne für dich nachschauen. "  # i18n-allow: German runtime fixture
            "Einen Moment, ich werfe einen Blick "  # i18n-allow: German runtime fixture
            "in dein Wiki und sage dir gleich Bescheid."  # i18n-allow: German runtime fixture
        ),
        "Ich schaue gleich in dein Wiki und melde mich.",  # i18n-allow: German runtime fixture
        "One moment, I'll check your Wiki and get back to you.",
        (
            "Un momento, voy a revisar tu wiki "  # i18n-allow: Spanish runtime fixture
            "y te digo enseguida."  # i18n-allow: Spanish runtime fixture
        ),
    ],
)
def test_deferred_action_claims_are_detected(text: str) -> None:
    assert has_deferred_action_claim(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "I checked the calculation: the result is 42.",
        "You can check the Wiki from the sidebar.",
        "Looking at the supplied data, the total is 42.",
        "In einem Wiki stehen verlinkte Seiten und "  # i18n-allow: German runtime fixture
        "Versionsverläufe.",  # i18n-allow: German runtime fixture
    ],
)
def test_grounded_or_explanatory_text_is_not_a_deferred_claim(text: str) -> None:
    assert has_deferred_action_claim(text) is False


def test_an_answer_between_two_promise_clauses_survives() -> None:
    """The reported loss: a full answer framed by promise wording.

    The opener matches the commitment vocabulary and the sign-off matches the
    defer vocabulary, so a whole-text match discarded the weather report in
    between and spoke the canned fallback instead.
    """
    original = (
        "Ich schaue kurz. Draussen sind 20 Grad und es "  # i18n-allow: German fixture
        "regnet nicht. Sage ich dir, wenn sich das ändert."  # i18n-allow: German fixture
    )

    honest = replace_unbacked_action_claim(original, executed_tools=(), language="de")

    assert honest == (
        "Draussen sind 20 Grad und es regnet nicht."  # i18n-allow: German runtime fixture
    )
    assert has_deferred_action_claim(original) is False


@pytest.mark.parametrize(
    ("original", "language", "expected"),
    [
        (
            "Let me check that for you. The build finished at 14:30 with 0 "
            "errors. I'll report back if anything changes.",
            "en",
            "The build finished at 14:30 with 0 errors.",
        ),
        (
            "Voy a revisar eso. Hace 20 grados y no llueve. "  # i18n-allow: Spanish runtime fixture
            "Te digo si cambia.",  # i18n-allow: Spanish runtime fixture
            "es",
            "Hace 20 grados y no llueve.",  # i18n-allow: Spanish runtime fixture
        ),
        (
            "Ich schaue kurz nach, draussen sind 20,5 Grad.",  # i18n-allow: German runtime fixture
            "de",
            "Draussen sind 20,5 Grad.",  # i18n-allow: German runtime fixture
        ),
        (
            "Draussen sind 20 Grad und es regnet nicht. "  # i18n-allow: German runtime fixture
            "Ich schaue nochmal nach und melde mich.",  # i18n-allow: German runtime fixture
            "de",
            "Draussen sind 20 Grad und es regnet nicht.",  # i18n-allow: German runtime fixture
        ),
    ],
)
def test_only_the_promise_clause_is_dropped(original: str, language: str, expected: str) -> None:
    assert (
        replace_unbacked_action_claim(
            original,
            executed_tools=(),
            language=language,
        )
        == expected
    )


@pytest.mark.parametrize(
    "text",
    [
        "Ich schaue kurz nach und sage dir Bescheid.",  # i18n-allow: German runtime fixture
        "Ich schaue kurz nach dem Wetter.",  # i18n-allow: German runtime fixture
        "Let me check and get back to you.",
    ],
)
def test_a_bare_promise_has_nothing_worth_keeping(text: str) -> None:
    assert has_deferred_action_claim(text) is True
    assert replace_unbacked_action_claim(
        text,
        executed_tools=(),
        language="de",
    ) == action_not_started_phrase("de")


def test_substance_needs_no_fixed_result_phrasing() -> None:
    """Substance is structural, not a whitelist of five result openings."""
    original = (
        "Ich schaue kurz. Der Serverraum liegt im zweiten "  # i18n-allow: German runtime fixture
        "Stock. Melde ich dir, falls sich das ändert."  # i18n-allow: German runtime fixture
    )

    assert replace_unbacked_action_claim(
        original,
        executed_tools=(),
        language="de",
    ) == (
        "Der Serverraum liegt im zweiten Stock."  # i18n-allow: German runtime fixture
    )


def test_a_conditional_tail_is_not_a_delivered_result() -> None:
    """A clause like "as soon as the export is ready" is a condition, not an outcome."""
    original = "One moment, I'll check that as soon as the export is ready."

    assert replace_unbacked_action_claim(
        original,
        executed_tools=(),
        language="en",
    ) == action_not_started_phrase("en")


def test_unbacked_claim_is_replaced_with_honest_localized_result() -> None:
    original = "Give me a moment; I'll open that and report back."

    assert replace_unbacked_action_claim(
        original,
        executed_tools=(),
        language="en",
    ) == action_not_started_phrase("en")


def test_executed_tool_makes_the_same_wording_grounded() -> None:
    original = "Give me a moment; I'll open that and report back."

    assert (
        replace_unbacked_action_claim(
            original,
            executed_tools={"open_app"},
            language="en",
        )
        == original
    )


def test_action_not_started_phrase_supports_every_runtime_language() -> None:
    assert action_not_started_phrase("de")
    assert action_not_started_phrase("en")
    assert action_not_started_phrase("es")


@pytest.mark.asyncio
async def test_brain_manager_replaces_a_provider_promise_without_tool_evidence() -> None:
    cfg = JarvisConfig()
    cfg.brain.primary = "openrouter"
    cfg.brain.providers["openrouter"] = BrainProviderConfig(
        model="test-model",
        deep_model="test-model",
    )
    manager = BrainManager(config=cfg, bus=EventBus(), tools={})
    manager._registry._loaded = True
    manager._active_can_call_tools = lambda: True  # type: ignore[method-assign]
    manager._brain_cache[("openrouter", "test-model")] = FakeBrain(
        text_response="One moment, I'll check that and get back to you."
    )

    reply = await manager.generate("Tell me something interesting.", use_history=False)

    assert reply == action_not_started_phrase("en")


@pytest.mark.parametrize(
    "text",
    [
        (
            "Alles klar, ich spiele dir eine Playlist mit Songs "  # i18n-allow
            "aus den 2010ern."  # i18n-allow: live 2026-08-19 17:13
        ),
        "I'm playing you a playlist of 2010s hits.",
        "I'll play you a playlist of songs from the 2010s.",
        "I've just sent the email.",
        "Te pongo una playlist de los 2010.",  # i18n-allow: Spanish output matcher
    ],
)
def test_false_completion_claims_are_detected(text: str) -> None:
    """Narrating the outcome is not the same as a deferred 'I'll look later'."""
    assert has_false_completion_claim(text) is True
    assert has_unbacked_action_claim(text) is True
    assert replace_unbacked_action_claim(
        text, executed_tools=(), language="en"
    ) == action_not_started_phrase("en")


@pytest.mark.parametrize(
    "text",
    [
        "Was genau für Musik gefällt dir denn?",  # i18n-allow: live clarifying question
        "Ich spiele gerne Tennis am Wochenende.",  # i18n-allow: hobby, not an action
        "Ich habe gestern Tennis gespielt.",  # i18n-allow: hobby perfect, no beneficiary
        "You can play the playlist from the sidebar.",
        "Looking at the 2010s, the hits were huge.",
        "I'll send you a list: A, B and C are ready.",
        "Te pongo un ejemplo.",  # i18n-allow: idiom, not playback
    ],
)
def test_questions_and_hobby_talk_are_not_false_completions(text: str) -> None:
    assert has_false_completion_claim(text) is False


@pytest.mark.parametrize("language", ["de", "en", "es"])
def test_honesty_fallback_is_not_itself_a_false_completion(language: str) -> None:
    phrase = action_not_started_phrase(language)
    assert has_false_completion_claim(phrase) is False
    assert has_unbacked_action_claim(phrase) is False


def test_german_perfect_needs_the_beneficiary() -> None:
    opened = "Ich habe dir die Datei geöffnet."  # i18n-allow: beneficiary + open
    assert has_false_completion_claim(opened) is True


def test_executed_tool_grounds_a_false_completion() -> None:
    text = "I'm playing you a playlist of 2010s hits."
    assert (
        replace_unbacked_action_claim(
            text, executed_tools={"youtube_music"}, language="en"
        )
        == text
    )


def test_packaged_persona_forbids_narrating_a_future_action() -> None:
    from jarvis.brain.persona_loader import default_persona_prompt

    prompt = default_persona_prompt()

    assert "emit the tool call without narrating a future action first" in prompt
    assert "a promise is not execution evidence" in prompt


@pytest.mark.parametrize(
    "text",
    [
        # Live 2026-08-22 18:22, gemini-live hybrid, zero function calls:
        # the whole turn was spoken as a finished result.
        (
            "Ich habe dir ein paar Lieder rausgesucht, bei denen du "  # i18n-allow: German runtime fixture
            "dich gut konzentrieren kannst. Sie laufen jetzt auf "  # i18n-allow: German runtime fixture
            "YouTube Music. Viel Spaß dabei!"  # i18n-allow: German runtime fixture
        ),
        # Live 2026-08-22 18:16, same session shape, Spotify skill.
        (
            "Alles klar! Ich habe dir entspannte Musik angemacht, "  # i18n-allow: German runtime fixture
            "damit du dich ein bisschen zurücklehnen kannst. "  # i18n-allow: German runtime fixture
            "Ich hoffe, es gefällt dir!"  # i18n-allow: German runtime fixture
        ),
        "Die Musik läuft jetzt.",  # i18n-allow: German runtime fixture
        "Your song is playing now on the speaker.",
        "La canción ya está sonando.",  # i18n-allow: Spanish runtime fixture
    ],
)
def test_spoken_completions_without_a_tool_call_are_false_completions(
    text: str,
) -> None:
    """A world-state claim names no actor, so the first-person matchers miss it.

    The forensic that produced this test: two voice turns reported music
    playing while ``tool_calls`` held only the instruction loader, and this
    predicate returned False for both, so nothing was withheld.
    """
    assert has_false_completion_claim(text) is True
    assert has_unbacked_action_claim(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # A delivered result after the claim keeps the whole text.
        (
            "Ich habe dir die Zusammenfassung geschickt. Der Umsatz "  # i18n-allow: German runtime fixture
            "lag bei 1,2 Millionen Euro im dritten Quartal."  # i18n-allow: German runtime fixture
        ),
        "Ich habe dir die Datei gespeichert. Sie liegt jetzt im Ordner Downloads.",  # i18n-allow: German runtime fixture
        # A present-tense "running now" about something other than the
        # promised action.
        (
            "Die Sitzung läuft jetzt schon zwanzig Minuten, "  # i18n-allow: German runtime fixture
            "also fassen wir zusammen."  # i18n-allow: German runtime fixture
        ),
        "Es sind 20 Grad und sonnig.",  # i18n-allow: German runtime fixture
        "I checked the calculation: the result is 42.",
    ],
)
def test_a_delivered_result_is_never_read_as_a_false_completion(text: str) -> None:
    assert has_false_completion_claim(text) is False


def test_a_send_off_alone_does_not_rescue_a_false_completion() -> None:
    """Goodwill is not substance.

    A two-word send-off clears the content bar on structure alone, which is
    what let the YouTube Music turn read as an answer.
    """
    assert (
        has_false_completion_claim(
            "Ich habe dir die Playlist gestartet. Viel Spaß dabei!"  # i18n-allow: German runtime fixture
        )
        is True
    )
