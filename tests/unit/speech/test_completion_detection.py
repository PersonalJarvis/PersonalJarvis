"""Unit tests for the incomplete-prompt completion classifier.

Target module: ``jarvis/speech/completion.py`` (not yet implemented — these are
the RED tests). The classifier is the structural twin of ``jarvis/speech/hangup.py``:
a deterministic, stdlib-only detector that fires ONLY on syntactically
unambiguous open-ended utterances.

Top directive under test: **precision over recall / "answer when in doubt".**
A complete prompt must NEVER be held back. The price we knowingly pay is missing
some genuine continuations whose final token is ambiguous (e.g. a German
separable verb prefix that looks like a stranded preposition).

The two case families the user called out:

* **Approach A (MUST fire):** clear dangling endings — trailing subordinating /
  coordinating conjunction, noun-requiring determiner, or an *unambiguous* German
  preposition.
* **Approach C (MUST NOT fire):** grammatically complete sentences that merely
  had a hard acoustic cut-off, including the separable-prefix traps and the
  article/pronoun ambiguities.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from jarvis.speech.completion import (
    REASON_CONJUNCTION,
    REASON_DETERMINER,
    REASON_PREPOSITION,
    REASON_TRAILING_COMMA,
    IncompleteVerdict,
    is_cancel,
    is_incomplete,
)

# --------------------------------------------------------------------------- #
# Approach A — MUST fire (verdict returned)                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        # German subordinating conjunctions — never end a complete sentence
        "Erinnere mich morgen daran, dass",
        "Ich komme heute nicht, weil",
        "Stell den Wecker, damit",
        "Sag mir Bescheid, sobald",
        "Ich weiß nicht, ob",
        "Ruf mich an, falls",
        "Ich mache es, obwohl",
        "Schreib es auf, bevor",
        # German coordinating conjunctions
        "Schick die Mail an Tom und",
        "Nimm den Bus oder",
        "Ich wollte kommen, aber",
        "Nicht heute, sondern",
        # English subordinating / coordinating conjunctions
        "Remind me tomorrow that",
        "I won't come because",
        "I don't know whether",
        "Send it to Tom and",
        "Take the bus or",
        "I wanted to come but",
    ],
)
def test_fires_on_trailing_conjunction(text: str) -> None:
    verdict = is_incomplete(text)
    assert verdict is not None
    assert verdict.reason == REASON_CONJUNCTION


@pytest.mark.parametrize(
    "text",
    [
        # German noun-requiring determiners (der/die/das deliberately excluded —
        # they are article/pronoun ambiguous and tested as MUST-NOT-fire below)
        "Ich hätte gerne ein",
        "Reservier bitte einen",
        "Gib mir eine",
        "Das gehört zu einem",
        "Erzähl mir von meiner",
        "Ich brauche keinen",
        "Buch mir den",
        # English determiners
        "Open the",
        "Give me a",
        "I need an",
    ],
)
def test_fires_on_trailing_determiner(text: str) -> None:
    verdict = is_incomplete(text)
    assert verdict is not None
    assert verdict.reason == REASON_DETERMINER


@pytest.mark.parametrize(
    "text",
    [
        # German UNAMBIGUOUS prepositions (not separable verb prefixes, and German
        # does not strand prepositions the way English does)
        "Reservier einen Tisch für",
        "Das spricht klar gegen",
        "Mach das bitte ohne",
        "Triff mich nachher bei",
        "Ich kann nicht kommen wegen",
    ],
)
def test_fires_on_trailing_unambiguous_preposition_de(text: str) -> None:
    verdict = is_incomplete(text)
    assert verdict is not None
    assert verdict.reason == REASON_PREPOSITION


# --------------------------------------------------------------------------- #
# Trailing comma — continuation marker (live regression 2026-05-26)            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        # Live regression — 2026-05-26 12:13 voice session: user paused mid-task
        # at a comma. VAD cut the turn; without trailing-comma detection BOTH
        # halves trigger a separate spawn_worker call → one task becomes
        # multiple sub-agents (see SUBAGENT_OUTPUTS_DEEP_DIVE + Screenshot
        # mission_019e63c6-4133 + 019e63c6-b3cf).
        "Kannst du bitte mir einen Subagent spawnen, welcher meine HTML-Datei baut, in der ganz klar beschrieben wird,",
        # Canonical list-mid-pause
        "Bring mir Brot, Käse, Milch,",
        # Subordinate-clause comma (German)
        "Ich brauche jemanden, der mir hilft,",
        # Trailing whitespace after the comma must not defeat detection
        "Schreib eine Mail an Tom,   ",
        # English trailing comma
        "I need someone who can help me,",
    ],
)
def test_fires_on_trailing_comma(text: str) -> None:
    verdict = is_incomplete(text)
    assert verdict is not None, f"trailing comma must mark {text!r} as INCOMPLETE"
    assert verdict.reason == REASON_TRAILING_COMMA


@pytest.mark.parametrize(
    "text",
    [
        # Comma in the middle, sentence ends complete
        "Hallo Tom, wie geht's?",
        "Bring mir Brot, Käse und Milch.",
        "Open the browser, please",
    ],
)
def test_precision_mid_comma_does_not_fire(text: str) -> None:
    verdict = is_incomplete(text)
    assert verdict is None, (
        f"non-trailing comma must NOT mark {text!r} as INCOMPLETE: got {verdict!r}"
    )


# --------------------------------------------------------------------------- #
# Approach C / precision — MUST NOT fire (None returned)                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        # Complete imperatives / questions (STT often drops terminal punctuation)
        "Öffne den Browser",
        "Wie spät ist es",
        "Schick eine Mail an Tom",
        "Spiel mir etwas Synthwave",
        "Was steht heute im Kalender",
        "Erinnere mich an den Termin",
        "Ruf die Müllers an",  # ends on the noun's verb frame — complete
        # English complete sentences
        "Open the browser",
        "What time is it",
        "Play some music",
    ],
)
def test_precision_complete_sentences_do_not_fire(text: str) -> None:
    assert is_incomplete(text) is None


@pytest.mark.parametrize(
    "text",
    [
        # German separable-prefix traps: final token equals a preposition but the
        # sentence is COMPLETE (anrufen / mitkommen / abspielen / aufmachen /
        # zuhören / ausschalten). Must not fire — this is the core precision guard.
        "Ruf Tom an",
        "Komm bitte mit",
        "Spiel das Lied ab",
        "Mach das Fenster auf",
        "Hör mir zu",
        "Schalt das Licht aus",
        "Räum das bitte weg",
    ],
)
def test_precision_separable_prefix_traps_do_not_fire(text: str) -> None:
    assert is_incomplete(text) is None


@pytest.mark.parametrize(
    "text",
    [
        # Article vs. demonstrative-pronoun ambiguity — these are COMPLETE.
        "Mach das",
        "Ich will das",
        "Nimm die",
        "Gib mir der",
        # Particle-ambiguous words (denn/dann/so) that legitimately end an
        # utterance — must not be treated as dangling conjunctions.
        "Was ist denn",
        "Bis dann",
        "Ich glaube so",
        "I think so",
    ],
)
def test_precision_ambiguous_tail_does_not_fire(text: str) -> None:
    assert is_incomplete(text) is None


@pytest.mark.parametrize(
    "text",
    [
        # English preposition stranding is grammatical, so a trailing EN
        # preposition must NOT fire (precision over recall on the EN path).
        "What are you looking at",
        "Who is this for",
        "I'll come with",
        "What is it about",
    ],
)
def test_precision_english_stranded_preposition_does_not_fire(text: str) -> None:
    assert is_incomplete(text) is None


@pytest.mark.parametrize(
    "text",
    [
        # Too short to be a dangling fragment: wake-ish / single tokens / fillers.
        "Jarvis",
        "Ja",
        "Hallo",
        "Danke",
        "Okay",
        "und",  # a bare conjunction alone is not a held fragment
        "the",
    ],
)
def test_precision_too_short_does_not_fire(text: str) -> None:
    assert is_incomplete(text) is None


@pytest.mark.parametrize(
    "text",
    [
        # German tag/closing questions: a trailing conjunction CLOSED by "?" is a
        # COMPLETE question, not an open continuation. Live wedge 2026-06-19
        # (session da25113a): "…morgen ist ja Montag, oder?" was classified as a
        # trailing conjunction, held by the ContinuationBuffer, never dispatched,
        # and discarded 30 s later at idle-timeout → "Jarvis hört für immer zu".
        # The trailing "?" is the disambiguator the tokenizer would otherwise
        # strip (so a BARE trailing "oder" stays a genuine open conjunction).
        "Morgen ist ja Montag, oder?",
        "Das machen wir so, oder?",
        "Du kommst doch mit, und?",
        # English closing tag with a conjunction tail
        "We could take the bus, or?",
    ],
)
def test_precision_tag_question_with_trailing_conjunction_does_not_fire(
    text: str,
) -> None:
    assert is_incomplete(text) is None, (
        f"a trailing conjunction closed by '?' is a complete tag question: {text!r}"
    )


def test_bare_trailing_conjunction_without_question_mark_still_fires() -> None:
    # Scoping guard: the tag-question exemption is keyed ONLY on the trailing
    # "?" — a bare trailing conjunction with no "?" remains a genuine open
    # continuation and must still be held (the spawn-fragmentation fix it exists
    # for, live regression 2026-05-26).
    verdict = is_incomplete("Nimm den Bus oder", language="de")
    assert verdict is not None and verdict.reason == REASON_CONJUNCTION


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_input_does_not_fire(text: str | None) -> None:
    assert is_incomplete(text) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Verdict contract                                                             #
# --------------------------------------------------------------------------- #


def test_verdict_is_frozen_and_carries_reason() -> None:
    verdict = is_incomplete("Erinnere mich daran, dass")
    assert isinstance(verdict, IncompleteVerdict)
    assert isinstance(verdict.reason, str) and verdict.reason
    with pytest.raises(FrozenInstanceError):
        verdict.reason = "mutated"  # type: ignore[misc]


def test_language_hint_is_optional() -> None:
    # The hint may steer tie-breaks but the default must still classify.
    assert is_incomplete("Open the") is not None
    assert is_incomplete("Open the", language="en") is not None
    assert is_incomplete("Öffne den Browser", language="de") is None


# --------------------------------------------------------------------------- #
# Cancel phrases                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "vergiss das",
        "vergiss es",
        "ach nein",
        "lass stecken",
        "schon gut",
        "never mind",
        "forget it",
    ],
)
def test_is_cancel_matches_abort_phrases(text: str) -> None:
    assert is_cancel(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "erinnere mich daran",
        "spiel etwas musik",
        "wie spät ist es",
        "",
        None,
    ],
)
def test_is_cancel_ignores_normal_speech(text: str | None) -> None:
    assert is_cancel(text) is False  # type: ignore[arg-type]
