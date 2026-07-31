"""Punctuation repair at the segment joins — ``cleanup.tidy_transcript``.

The dictation lane transcribes in ~8 s segments and joins them with a bare
space, while the recognizer punctuates and capitalises every segment as if it
were the whole utterance. Everything tested here is damage that arrangement
manufactures: a doubled "....", a lower-case sentence start, a stray ellipsis
where a segment cut a sentence in half.

The negative tests carry the same weight as the positive ones. A repair that
deletes an ellipsis the speaker actually dictated is worse than the artifact it
was written to remove, so "an intentional pause survives" is pinned as hard as
"the artifact is gone".
"""
from __future__ import annotations

import re

import pytest

from jarvis.dictation.cleanup import clean_transcript, tidy_transcript

# The live transcript this whole repair exists for. Recorded on 2026-07-28 at
# 18:50:05: two segments joined, the second one preceded by the ellipsis the
# recognizer emitted when the first segment cut the sentence.
LIVE_JOIN = "Ich habe äh gesprochen. ... ist, ist das gut."  # i18n-allow: fixture (§1 #4)
LIVE_JOIN_CLEANED = "Ich habe gesprochen. ... ist, ist das gut."  # i18n-allow: fixture
LIVE_JOIN_TIDIED = "Ich habe gesprochen. Ist, ist das gut."  # i18n-allow: fixture


# --------------------------------------------------------------------------
# The doubled "...." — our own bug, pinned in both directions
# --------------------------------------------------------------------------


def test_the_historical_rule_really_did_manufacture_four_dots() -> None:
    """Reproduce the defect byte-for-byte so the fix cannot be undone silently.

    ``_tidy`` used to pull a space back onto ANY following mark with
    ``\\s+([,.;:!?])``. Against a following ellipsis that ate the space in front
    of the first dot and glued it to the sentence that had already ended.
    """
    historical_rule = re.compile(r"\s+([,.;:!?])")
    assert historical_rule.sub(r"\1", "Ich habe gesprochen. ... ist") == (
        "Ich habe gesprochen.... ist"
    )


def test_filler_removal_no_longer_glues_the_following_ellipsis() -> None:
    """The same input through the shipped path keeps the dots apart."""
    result = clean_transcript(LIVE_JOIN, language="de")
    assert result.applied is True
    assert "...." not in result.text
    assert result.text == LIVE_JOIN_CLEANED


def test_a_space_before_a_lone_period_is_still_pulled_back() -> None:
    """The narrowed rule must not stop doing the job it was written for."""
    result = clean_transcript("The meeting is at four umm.", language="en")
    assert result.text == "The meeting is at four."


def test_the_live_join_ends_up_clean_after_the_full_pass() -> None:
    cleaned = clean_transcript(LIVE_JOIN, language="de")
    assert tidy_transcript(cleaned.text) == LIVE_JOIN_TIDIED


# --------------------------------------------------------------------------
# It runs unconditionally — the untouched transcript is the common case
# --------------------------------------------------------------------------


def test_a_transcript_no_cleanup_touched_is_still_tidied() -> None:
    """Gating the repair on "a filler was removed" is why this stayed broken.

    Nothing in this text is a filler, in any language, so ``clean_transcript``
    returns it verbatim — and the join damage would have survived all the way to
    the clipboard under the old, cleanup-gated behaviour.
    """
    raw = "Das ist der erste Teil. ... und es geht weiter."  # i18n-allow: fixture
    tidied = "Das ist der erste Teil. Und es geht weiter."  # i18n-allow: fixture
    assert clean_transcript(raw, language="de").text == raw
    assert tidy_transcript(raw) == tidied


def test_a_clean_transcript_is_returned_unchanged() -> None:
    text = "Wir treffen uns morgen um zehn. Bring alles mit."  # i18n-allow: fixture
    assert tidy_transcript(text) == text


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_blank_input_is_returned_as_is(text: str) -> None:
    assert tidy_transcript(text) == text


# --------------------------------------------------------------------------
# Ellipsis collapsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The German fixtures below are the vocabulary under test (§1 #4).
        # Terminator plus a free-standing ellipsis: the join artifact proper.
        ("gesprochen. ... ist das gut", "gesprochen. Ist das gut"),  # i18n-allow
        # The already-glued form, in case an older transcript carries it.
        ("gesprochen.... ist das gut", "gesprochen. Ist das gut"),  # i18n-allow
        # A word-attached ellipsis: the recognizer's mid-sentence cut.
        ("gesprochen... ist das gut", "gesprochen. Ist das gut"),  # i18n-allow
        # The single-glyph spelling of the same thing.
        ("gesprochen… ist das gut", "gesprochen. Ist das gut"),  # i18n-allow
        # Free-standing, but the next segment starts on a capital.
        ("gesprochen ... Ist das gut", "gesprochen. Ist das gut"),  # i18n-allow
    ],
)
def test_a_segment_join_ellipsis_becomes_one_terminator(
    text: str, expected: str
) -> None:
    assert tidy_transcript(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # The German fixtures below are the vocabulary under test (§1 #4).
        # Free-standing, lower-case continuation: the speaker trailing off.
        "das war ... schwierig und dann ging es weiter",  # i18n-allow
        "das war … schwierig und dann ging es weiter",  # i18n-allow
        # Same, after a comma — the comma must not swallow the dots either.
        "wir gehen, ... aber nicht heute",  # i18n-allow
        "I waited ... and nothing happened",
    ],
)
def test_an_intentional_mid_sentence_ellipsis_survives(text: str) -> None:
    """The failure direction that matters: never delete what was dictated."""
    assert tidy_transcript(text) == text


# --------------------------------------------------------------------------
# Adjacent terminal punctuation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello.. world", "Hello. World"),
        ("Hello., world", "Hello. World"),
        ("Hello,. world", "Hello. World"),
        ("Hello. . world", "Hello. World"),
        ("Hello,, world", "Hello, world"),
    ],
)
def test_adjacent_terminal_marks_de_duplicate(text: str, expected: str) -> None:
    assert tidy_transcript(text) == expected


def test_a_deliberate_double_mark_is_left_alone() -> None:
    """A "?!" is a thing a person writes; a ".." is not."""
    text = "Was denn?! also gut"  # i18n-allow: German fixture under test (§1 #4)
    assert tidy_transcript(text) == text


# --------------------------------------------------------------------------
# Capitalization at the join
# --------------------------------------------------------------------------


def test_a_lower_case_sentence_start_gets_its_capital_back() -> None:
    joined = "Erste Sache. zweite Sache."  # i18n-allow: fixture under test (§1 #4)
    repaired = "Erste Sache. Zweite Sache."  # i18n-allow: fixture under test (§1 #4)
    assert tidy_transcript(joined) == repaired


def test_the_first_word_of_the_dictation_is_not_touched() -> None:
    """Only a JOIN is repaired. What the speaker opened with is their business."""
    text = "ich fange klein an."  # i18n-allow: German fixture under test (§1 #4)
    assert tidy_transcript(text) == text


@pytest.mark.parametrize(
    "text",
    [
        # The German fixtures below are the vocabulary under test (§1 #4).
        # Single-letter abbreviations: two word characters are required in front
        # of the terminator, which is exactly what these do not have.
        "z. B. das ist so",  # i18n-allow
        # Internal dots: the character before the second dot is not a word char.
        "e.g. this is fine",
        # A decimal point never starts a sentence.
        "der Wert 3.5 kommt danach",  # i18n-allow
    ],
)
def test_a_dot_that_does_not_end_a_sentence_capitalizes_nothing(text: str) -> None:
    assert tidy_transcript(text) == text


def test_a_number_can_still_end_a_sentence() -> None:
    joined = "3.14 ist Pi. das war es"  # i18n-allow: fixture under test (§1 #4)
    repaired = "3.14 ist Pi. Das war es"  # i18n-allow: fixture under test (§1 #4)
    assert tidy_transcript(joined) == repaired


# --------------------------------------------------------------------------
# Whitespace
# --------------------------------------------------------------------------


def test_the_double_space_a_join_leaves_collapses() -> None:
    joined = "erster Teil.  zweiter Teil"  # i18n-allow: fixture under test (§1 #4)
    repaired = "erster Teil. Zweiter Teil"  # i18n-allow: fixture under test (§1 #4)
    assert tidy_transcript(joined) == repaired


def test_a_french_style_space_before_a_question_mark_survives() -> None:
    """The repair runs on all ~100 recognition languages, not only on de/en/es."""
    text = "Est-ce que tu viens ? je ne sais pas"
    assert tidy_transcript(text) == text
