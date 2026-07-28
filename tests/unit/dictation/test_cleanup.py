"""Filler-word cleanup: remove hesitation sounds, never content words.

The value of a dictation feature is "these are my words", so the tests that
matter most here are the NEGATIVE ones: words that look like filler to a style
guide but carry meaning must survive, an unknown language must be a no-op, and
a cleanup that would eat too much must be refused outright.
"""
from __future__ import annotations

import pytest

from jarvis.dictation.cleanup import (
    FILLER_WORDS,
    SUPPORTED_LANGUAGES,
    clean_transcript,
    normalize_language,
)

# --------------------------------------------------------------------------
# Removal actually happens, in every supported language
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        (
            "Uh, I think we should um ship it tomorrow.",
            "en",
            "I think we should ship it tomorrow.",
        ),
        (
            "Ähm, das ist äh wirklich gut geworden.",  # i18n-allow: German fixture under test (§1 list #4)
            "de",
            "Das ist wirklich gut geworden.",  # i18n-allow: German fixture under test (§1 list #4)
        ),
        (
            "Eh, creo que em deberíamos enviarlo.",
            "es",
            "Creo que deberíamos enviarlo.",
        ),
    ],
)
def test_removes_hesitation_sounds(text: str, language: str, expected: str) -> None:
    result = clean_transcript(text, language=language)
    assert result.applied is True
    assert result.text == expected
    assert result.raw == text  # the raw transcript is always preserved
    assert result.removed_words == 2


def test_every_supported_language_has_rules() -> None:
    """A language in the set must actually have a non-empty table."""
    for language in SUPPORTED_LANGUAGES:
        assert FILLER_WORDS[language], language


# --------------------------------------------------------------------------
# Content words must survive — the failure mode that matters
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "language"),
    [
        # English words every style guide calls filler, but which change meaning.
        ("I like this, actually it is basically fine.", "en"),
        ("So right, well then, you know the answer.", "en"),
        # German particles that are ordinary vocabulary.
        ("Also halt eben das Thema.", "de"),
        ("Ja nun gut, das machen wir so.", "de"),
        # Spanish words that are demonstratives / connectives, not fillers.
        ("Este informe es bueno, pues claro.", "es"),
        ("O sea que vale, entonces seguimos.", "es"),
    ],
)
def test_content_words_are_never_removed(text: str, language: str) -> None:
    result = clean_transcript(text, language=language)
    assert result.removed_words == 0
    assert result.text == text


def test_filler_inside_a_word_is_not_touched() -> None:
    """Whole-word matching only — "umbrella" must not lose its "um"."""
    text = "The umbrella and the ehrenamt are fine."
    assert clean_transcript(text, language="en").text == text


# --------------------------------------------------------------------------
# The destruction ceiling
# --------------------------------------------------------------------------


def test_all_filler_input_is_refused_rather_than_emptied() -> None:
    result = clean_transcript("um uh er", language="en")
    assert result.applied is False
    assert result.reason == "ceiling"
    assert result.text == "um uh er"


def test_absolute_cap_refuses_a_short_sentence_losing_too_many_words() -> None:
    # 8 words, 4 of them filler -> past the absolute cap for short texts.
    text = "um uh er ah the plan is ready"
    result = clean_transcript(text, language="en")
    assert result.applied is False
    assert result.reason == "ceiling"
    assert result.text == text


def test_short_sentence_with_two_fillers_is_still_cleaned() -> None:
    """The ceiling catches broken rules, not someone who hesitates twice."""
    result = clean_transcript("Ähm, das ist äh gut.", language="de")  # i18n-allow: German fixture under test (§1 list #4)
    assert result.applied is True
    assert result.text == "Das ist gut."  # i18n-allow: German fixture under test (§1 list #4)


def test_long_text_uses_the_proportional_ceiling() -> None:
    body = " ".join(["word"] * 40)
    result = clean_transcript(f"um {body}", language="en")
    assert result.applied is True
    assert result.removed_words == 1


def test_ceiling_is_configurable() -> None:
    text = " ".join(["um"] * 5 + ["word"] * 15)  # 25 % filler in 20 words
    lenient = clean_transcript(text, language="en", max_removed_fraction=0.9)
    strict = clean_transcript(text, language="en", max_removed_fraction=0.01)
    assert lenient.applied is True
    assert strict.applied is False
    assert strict.reason == "ceiling"


# --------------------------------------------------------------------------
# Language handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("de", "de"),
        ("de-DE", "de"),
        ("DE_de", "de"),
        ("en-US", "en"),
        ("auto", None),
        ("unknown", None),
        ("", None),
        (None, None),
        ("fr", None),
        ("ja", None),
    ],
)
def test_normalize_language(value: str | None, expected: str | None) -> None:
    assert normalize_language(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("English", "en"),
        ("english", "en"),
        ("German", "de"),
        ("Deutsch", "de"),
        ("Spanish", "es"),
        ("español", "es"),
        ("French", None),  # a name we have no rules for is still a no-op
    ],
)
def test_language_NAMES_are_accepted_not_just_codes(
    value: str, expected: str | None
) -> None:
    """Providers disagree: faster-whisper says "de", a cloud Whisper says "German".

    Found live on 2026-07-28: a real dictation came back with
    ``language="English"``, so every cleanup resolved to "no rules for this
    language" and silently never ran. Accepting both spellings is what makes
    the feature provider-agnostic (AP-21).
    """
    assert normalize_language(value) == expected


def test_cleanup_runs_when_the_provider_reports_a_language_name() -> None:
    result = clean_transcript("Uh, I think we should um ship it.", language="English")
    assert result.applied is True
    assert result.text == "I think we should ship it."


def test_unknown_language_is_a_no_op_not_an_english_guess() -> None:
    """Applying English rules to French speech is how content gets eaten."""
    text = "Euh, je pense que um c'est bien."
    result = clean_transcript(text, language="fr")
    assert result.applied is False
    assert result.reason == "no_rules"
    assert result.text == text


def test_disabled_returns_the_raw_text() -> None:
    result = clean_transcript("um hello", language="en", remove_fillers=False)
    assert result.applied is False
    assert result.reason == "disabled"
    assert result.text == "um hello"


def test_empty_input() -> None:
    result = clean_transcript("   ", language="en")
    assert result.applied is False
    assert result.reason == "empty"


# --------------------------------------------------------------------------
# Tidying after a removal
# --------------------------------------------------------------------------


def test_leading_capital_is_restored_after_removing_the_first_word() -> None:
    result = clean_transcript("Um, the meeting is at four.", language="en")
    assert result.text == "The meeting is at four."


def test_lowercase_stays_lowercase() -> None:
    result = clean_transcript("um the meeting is at four", language="en")
    assert result.text == "the meeting is at four"


def test_punctuation_is_pulled_back_onto_the_previous_word() -> None:
    result = clean_transcript("We should go um, tomorrow please.", language="en")
    assert "  " not in result.text
    assert " ," not in result.text
