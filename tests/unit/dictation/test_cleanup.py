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
            "Uh, I think we should umm ship it tomorrow.",
            "en",
            "I think we should ship it tomorrow.",
        ),
        (
            "Ähm, das ist äh wirklich gut geworden.",  # i18n-allow: German fixture under test (§1 list #4)
            "de",
            "Das ist wirklich gut geworden.",  # i18n-allow: German fixture under test (§1 list #4)
        ),
        (
            # "ehh", not "eh" — see test_a_filler_must_be_meaningless_in_every
            # _supported_language for why the short spelling is gone.
            "Ehh, creo que em deberíamos enviarlo.",
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
    """Whole-word matching only — "kommen" must not lose its "mm"."""
    # German fixture under test (§1 list #4); escape is per line.
    text = "Wir kommen gleich, das Hemd ist da."  # i18n-allow
    assert clean_transcript(text, language="de").text == text


# --------------------------------------------------------------------------
# A wrong language tag must not be able to delete content words
# --------------------------------------------------------------------------
#
# The tag that picks the filler table comes from the STT provider, and cloud
# Whisper endpoints report "English" for German speech often enough that the
# repo treats it as a documented defect. So the tables have to survive being
# applied to the wrong language: a token that means something in one supported
# language may not sit in another one's list.


def test_german_text_tagged_english_keeps_its_pronouns() -> None:
    """The exact damage that got "er" removed from the English table.

    Measured on the live module before the fix: this shape of sentence came
    back with every "er" deleted at ``applied=True removed_words=4``, which was
    8.9 % of the words — under the destruction ceiling, so nothing flagged it
    and the user was handed a mutilated sentence reported as a clean success.
    """
    text = "Er hat gesagt, er kommt und er bringt es mit."  # i18n-allow: fixture (§1 #4)
    result = clean_transcript(text, language="English")
    assert result.removed_words == 0
    assert result.text == text


@pytest.mark.parametrize(
    "text",
    [
        # German fixtures under test (§1 list #4). The escape is per LINE, not
        # per block — both language gates read it that way.
        "Kümmere dich um das Update.",  # i18n-allow
        "Erinnere mich um fünf Uhr an den Termin.",  # i18n-allow
        "Es geht um die Rechnung von gestern.",  # i18n-allow
        "Ich gehe um das Haus herum.",  # i18n-allow
    ],
)
def test_german_preposition_um_survives_an_english_tag(text: str) -> None:
    """The 2026-07-30 report: a preposition vanished out of the sentence middle.

    "um" is the canonical English hesitation sound and one of the most common
    German prepositions, so a German utterance tagged "English" came back
    without it:

        "Kümmere dich um das Update" -> "Kümmere dich das Update"  # i18n-allow

    One function word is a small fraction of the text, so the destruction
    ceiling never fired: the sentence arrived ungrammatical, reported clean,
    and the router acted on the mangled instruction.
    """
    result = clean_transcript(text, language="English")
    assert result.removed_words == 0
    assert result.text == text


def test_german_eh_survives_a_spanish_tag() -> None:
    """The mirror hole: Spanish "eh" against German "eh" ("anyway")."""
    text = "Das mache ich eh morgen, das ist eh klar."  # i18n-allow: fixture (§1 #4)
    result = clean_transcript(text, language="es")
    assert result.removed_words == 0
    assert result.text == text


@pytest.mark.parametrize(
    ("token", "language", "why"),
    [
        # i18n-allow: the tokens below are the vocabulary under test (§1 list #4)
        ("er", "en", "a top-frequency German pronoun"),
        ("eh", "en", "ordinary German for 'anyway'"),
        ("eh", "es", "ordinary German for 'anyway'"),
        ("ah", "en", "a spoken interjection that carries meaning of its own"),
        ("um", "en", "one of the most common German prepositions"),
    ],
)
def test_a_filler_must_be_meaningless_in_every_supported_language(
    token: str, language: str, why: str
) -> None:
    """Structural guard: re-adding one of these re-opens the hole above."""
    assert token not in FILLER_WORDS[language], (
        f"{token!r} is {why} and must not sit in the {language!r} filler list — "
        f"one wrong provider language tag deletes it silently"
    )


# --------------------------------------------------------------------------
# The destruction ceiling
# --------------------------------------------------------------------------


def test_all_filler_input_is_refused_rather_than_emptied() -> None:
    result = clean_transcript("umm uh uhh", language="en")
    assert result.applied is False
    assert result.reason == "ceiling"
    assert result.text == "umm uh uhh"


def test_absolute_cap_refuses_a_short_sentence_losing_too_many_words() -> None:
    # 8 words, 4 of them filler -> past the absolute cap for short texts.
    text = "umm uh uhh mhm the plan is ready"
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
    result = clean_transcript(f"umm {body}", language="en")
    assert result.applied is True
    assert result.removed_words == 1


def test_ceiling_is_configurable() -> None:
    text = " ".join(["umm"] * 5 + ["word"] * 15)  # 25 % filler in 20 words
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
    result = clean_transcript("Uh, I think we should umm ship it.", language="English")
    assert result.applied is True
    assert result.text == "I think we should ship it."


def test_unknown_language_is_a_no_op_not_an_english_guess() -> None:
    """Applying English rules to French speech is how content gets eaten."""
    text = "Euh, je pense que umm c'est bien."
    result = clean_transcript(text, language="fr")
    assert result.applied is False
    assert result.reason == "no_rules"
    assert result.text == text


def test_disabled_returns_the_raw_text() -> None:
    result = clean_transcript("umm hello", language="en", remove_fillers=False)
    assert result.applied is False
    assert result.reason == "disabled"
    assert result.text == "umm hello"


def test_empty_input() -> None:
    result = clean_transcript("   ", language="en")
    assert result.applied is False
    assert result.reason == "empty"


# --------------------------------------------------------------------------
# Tidying after a removal
# --------------------------------------------------------------------------


def test_leading_capital_is_restored_after_removing_the_first_word() -> None:
    result = clean_transcript("Umm, the meeting is at four.", language="en")
    assert result.text == "The meeting is at four."


def test_lowercase_stays_lowercase() -> None:
    result = clean_transcript("umm the meeting is at four", language="en")
    assert result.text == "the meeting is at four"


def test_punctuation_is_pulled_back_onto_the_previous_word() -> None:
    result = clean_transcript("We should go umm, tomorrow please.", language="en")
    assert "  " not in result.text
    assert " ," not in result.text
