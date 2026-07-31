"""Transcript cleanup filter — ``jarvis.plugins.stt.transcript_filter``.

The German and Spanish strings in this file are the speech under test: a filter
that removes German hesitation sounds cannot be tested in English (CLAUDE.md §1,
allowed category 4).
"""
from __future__ import annotations

import unicodedata

import pytest

from jarvis.plugins.stt.transcript_filter import (
    PHONETIC_FIXES,
    apply_phonetic_fixes,
    clean_stt_text,
    collapse_repetitions,
    normalize_characters,
    repair_stutters,
    resolve_language,
    strip_wrapping_quotes,
)

# ----------------------------------------------------------------------
# Character normalisation
# ----------------------------------------------------------------------


def test_decomposed_umlauts_are_composed() -> None:
    """A provider answering in NFD must not blind the umlaut-bearing rules."""
    decomposed = unicodedata.normalize("NFD", "ähnlich")  # i18n-allow: input
    assert decomposed != "ähnlich"  # i18n-allow: guards the fixture itself
    assert normalize_characters(decomposed) == "ähnlich"  # i18n-allow: input


def test_invisible_characters_and_nbsp_are_removed() -> None:
    text = "Mach das​ Licht﻿ an"  # i18n-allow: input under test
    assert normalize_characters(text) == "Mach das Licht an"  # i18n-allow: input


def test_newlines_survive_but_horizontal_runs_collapse() -> None:
    assert normalize_characters("one   two\n\nthree\t four") == "one two\n\nthree four"


def test_decomposed_umlaut_filler_is_still_removed() -> None:
    """The reason NFC runs first: the filler tables are composed spellings."""
    decomposed = "Äh, mach das Licht an."  # i18n-allow: German speech under test
    assert clean_stt_text(decomposed, language="de") == "Mach das Licht an."


# ----------------------------------------------------------------------
# Wrapping quotes
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "wrapped",
    ['"turn on the light"', "'turn on the light'", "“turn on the light”"],
)
def test_one_matched_quote_pair_is_stripped(wrapped: str) -> None:
    assert strip_wrapping_quotes(wrapped) == "turn on the light"


def test_unmatched_and_inner_quotes_survive() -> None:
    assert strip_wrapping_quotes('he said "hello" to me') == 'he said "hello" to me'
    assert strip_wrapping_quotes('"unbalanced') == '"unbalanced'


# ----------------------------------------------------------------------
# Repetition loops
# ----------------------------------------------------------------------


def test_word_loop_collapses_to_one() -> None:
    assert collapse_repetitions("ja ja ja ja das passt") == "ja das passt"


def test_phrase_loop_with_punctuation_collapses() -> None:
    text = "Thank you for watching. Thank you for watching. Thank you for watching."
    assert collapse_repetitions(text) == "Thank you for watching."


def test_phrase_loop_containing_a_year_collapses() -> None:
    """The subtitle credit this repo has actually seen in the wild."""
    # i18n-allow: German STT boilerplate is the artifact under test (§1 list #4)
    one = "Untertitelung des ZDF für funk, 2017"  # i18n-allow: STT boilerplate
    assert collapse_repetitions(f"{one}. {one}. {one}. Mach das Licht an") == (
        f"{one}. Mach das Licht an"
    )


def test_a_doubled_word_is_left_alone() -> None:
    """Two, not three: ordinary speech in every language this ships in."""
    # i18n-allow: German sentence whose legitimate doubling is under test
    german = "Ich weiß, dass das das Problem ist"  # i18n-allow: input under test
    assert collapse_repetitions(german) == german
    assert collapse_repetitions("We had had enough") == "We had had enough"


def test_a_repeated_bare_number_is_left_alone() -> None:
    """A repeated number is dictated data far more often than a decoder loop."""
    spoken = "Die Nummer ist 5 5 5"  # i18n-allow: German speech under test
    assert collapse_repetitions(spoken) == spoken


def test_distinct_words_in_a_row_are_left_alone() -> None:
    text = "Wir treffen uns Montag, Dienstag und Mittwoch."  # i18n-allow: input
    assert collapse_repetitions(text) == text


# ----------------------------------------------------------------------
# Stutters
# ----------------------------------------------------------------------


def test_hyphen_stutter_is_dropped() -> None:
    assert repair_stutters("w- was ist das") == "was ist das"  # i18n-allow: input


def test_suspended_hyphen_survives() -> None:
    """German writes "Vor- und Nachteile"; that hyphen is not a stutter."""
    text = "Vor- und Nachteile"  # i18n-allow: German construction under test
    assert repair_stutters(text) == text


def test_long_fragment_before_a_hyphen_is_not_a_stutter() -> None:
    text = "Software- und Softwareentwicklung"  # i18n-allow: input under test
    assert repair_stutters(text) == text


# ----------------------------------------------------------------------
# Phonetic fixes
# ----------------------------------------------------------------------


def test_known_misrecognition_is_repaired_for_its_language() -> None:
    # i18n-allow: the mis-transcription and its target are the data under test
    assert apply_phonetic_fixes("Bitte aufflegen", "de") == "Bitte auflegen"


def test_phonetic_fixes_do_not_run_without_a_language() -> None:
    text = "Bitte aufflegen"  # i18n-allow: input vocabulary under test
    assert apply_phonetic_fixes(text, None) == text
    assert apply_phonetic_fixes(text, "fr") == text


def test_no_phonetic_source_is_a_real_word_elsewhere() -> None:
    """The admission rule, pinned: a source token that means something in
    another supported language would be replaced inside correct sentences the
    first time a provider reports the wrong language tag — and providers do."""
    from jarvis.dictation.cleanup import FILLER_WORDS

    every_filler = {word for words in FILLER_WORDS.values() for word in words}
    for language, pairs in PHONETIC_FIXES.items():
        for source, target in pairs:
            assert source.casefold() != target.casefold()
            assert source.casefold() not in every_filler, (language, source)


# ----------------------------------------------------------------------
# End to end
# ----------------------------------------------------------------------


def test_english_filler_and_quotes_in_one_pass() -> None:
    raw = '"Uh, I think we should umm ship it."'
    assert clean_stt_text(raw, language="en") == "I think we should ship it."


def test_german_filler_needs_the_german_tag() -> None:
    raw = "Ähm, kannst du äh das Licht anmachen?"  # i18n-allow: speech under test
    assert clean_stt_text(raw, language="de") == "Kannst du das Licht anmachen?"


def test_the_english_language_name_is_understood() -> None:
    """Cloud Whisper endpoints answer with the NAME, not the ISO code."""
    assert clean_stt_text("Uh, ship it.", language="English") == "Ship it."


def test_an_unknown_language_still_gets_the_neutral_repairs() -> None:
    """No filler table for French, but a loop is a loop in every language."""
    out = clean_stt_text("bonjour bonjour bonjour Marie", language="fr")
    assert out == "bonjour Marie"


def test_filler_removal_can_be_switched_off() -> None:
    raw = "Uh, ship it."
    assert clean_stt_text(raw, language="en", remove_fillers=False) == raw


def test_empty_and_blank_input_is_returned_unchanged() -> None:
    assert clean_stt_text("") == ""
    assert clean_stt_text("   ") == "   "


def test_a_transcript_of_only_filler_survives_the_destruction_ceiling() -> None:
    """The delegated ceiling: rules that leave nothing behind are a defect."""
    assert clean_stt_text("umm uh uhh", language="en") == "umm uh uhh"


def test_cleanup_never_raises_on_a_broken_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("rule exploded")

    monkeypatch.setattr(
        "jarvis.plugins.stt.transcript_filter.collapse_repetitions", _boom
    )
    assert clean_stt_text("turn on the light", language="en") == "turn on the light"


# ----------------------------------------------------------------------
# The provider's language tag is a hint, not the verdict (2026-07-30)
# ----------------------------------------------------------------------
#
# Every rule keyed off that string DELETES tokens, and the cloud endpoints
# report a language confidently when it is wrong ("English" for German speech;
# a `[stt].language` pin echoed back for speech in any language). So the filter
# resolves the language from the tag AND the repaired text, through the same
# canonical resolver the dictation lane uses.


@pytest.mark.parametrize(
    "text",
    [
        # German speech under test (§1 list #4). The escape is per LINE, not
        # per block — both language gates read it that way.
        "Kümmere dich um das Update",  # i18n-allow
        "Erinnere mich um fünf Uhr an den Termin",  # i18n-allow
        "Es geht um die Rechnung von gestern",  # i18n-allow
    ],
)
def test_german_speech_mistagged_english_keeps_its_prepositions(text: str) -> None:
    """The reported defect: a preposition vanished from the sentence middle.

    Two independent guards now stop it — "um" left the English filler table,
    and a tag the text contradicts no longer selects that table at all — so the
    sentence survives whichever spelling of "English" the provider sends.
    """
    for tag in ("en", "English", "english", "en-US"):
        assert clean_stt_text(text, language=tag) == text


def test_a_mistagged_transcript_resolves_to_the_language_it_is_in() -> None:
    assert resolve_language("Mach bitte das Licht an", "English") == "de"  # i18n-allow: fixture


def test_an_agreeing_tag_is_kept() -> None:
    assert resolve_language("Turn on the lights please", "English") == "en"


def test_a_language_we_have_no_rules_for_stays_a_no_op() -> None:
    # Neither the French tag nor the French text may resolve to one of the
    # three tables — that is how "je" would be judged by German rules.
    assert resolve_language("Je pense que c'est bien", "French") is None


def test_english_fillers_still_go_when_the_text_agrees() -> None:
    assert (
        clean_stt_text("Uh, I think we should umm ship it.", language="en")
        == "I think we should ship it."
    )
