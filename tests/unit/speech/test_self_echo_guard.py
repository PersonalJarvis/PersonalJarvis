"""BUG-084: self-echo TEXT guard — last defense against the self-talk loop.

On open speakers next to a built-in mic (the Intel-Mac test machine,
2026-07-18) the assistant's own TTS can slip every acoustic gate, get
transcribed (garbled) and come back as a "user" turn — which the brain then
answers, spiralling into a multi-turn conversation with itself. These tests
pin the text-level guard: an utterance that is (fuzzily) nothing but words
Jarvis itself just voiced is dropped; anything that adds novel content is
kept (fail-open). German fixture strings quote the runtime voice product
surface (the actual Mac transcript).
"""

from __future__ import annotations

import time

from jarvis.speech.pipeline import SpeechPipeline


def _pipeline() -> SpeechPipeline:
    # Same fixture pattern as test_thinking_interrupt_monitor: the guard
    # methods are getattr-defensive, so a bare instance is enough.
    return SpeechPipeline.__new__(SpeechPipeline)


def test_garbled_echo_fragment_is_flagged() -> None:
    # The live loop's smoking gun: the reply tail came back STT-garbled
    # ("mich" heard as "misch") and was answered as a user turn.
    p = _pipeline()
    p._register_assistant_speech("Das freut mich zu hören.")  # i18n-allow: voice fixture
    assert p._looks_like_self_echo("Misch zu hören") is True  # i18n-allow: garbled echo


def test_verbatim_echo_is_flagged() -> None:
    p = _pipeline()
    p._register_assistant_speech(
        "Guten Morgen, bei mir läuft alles bestens."  # i18n-allow: voice fixture
    )
    assert p._looks_like_self_echo("bei mir läuft alles bestens") is True  # i18n-allow


def test_user_answer_with_novel_word_is_kept() -> None:
    # A genuine answer largely built from the assistant's own words must
    # survive: ONE novel token ("gut") keeps it a real turn — and the fuzzy
    # cutoff must not swallow it into "guten" (ratio exactly 0.75).
    p = _pipeline()
    p._register_assistant_speech(
        "Guten Morgen, bei mir läuft alles bestens. Was geht bei dir?"  # i18n-allow
    )
    assert p._looks_like_self_echo("bei mir läuft alles gut") is False  # i18n-allow


def test_short_commands_are_never_judged() -> None:
    # Sub-3-token turns must always reach their handlers (barge "stopp",
    # hangup phrases), even when the assistant just spoke those very words.
    p = _pipeline()
    p._register_assistant_speech(
        "Soll ich wirklich stoppen und auflegen?"  # i18n-allow: voice fixture
    )
    assert p._looks_like_self_echo("stoppen") is False  # i18n-allow: voice fixture
    assert p._looks_like_self_echo("nicht auflegen") is False  # i18n-allow: voice fixture


def test_guard_lapses_outside_the_activity_window() -> None:
    # Long after playback the user may echo Jarvis verbatim all they want.
    p = _pipeline()
    p._register_assistant_speech("Das freut mich zu hören.")  # i18n-allow: voice fixture
    p._assistant_speech_activity_ns = time.time_ns() - int(60e9)
    assert p._looks_like_self_echo("freut mich zu hören") is False  # i18n-allow


def test_echo_spanning_two_sentences_is_flagged() -> None:
    # Streaming TTS registers per sentence; an echo window can straddle the
    # boundary. The concatenation of the two newest references covers it.
    p = _pipeline()
    p._register_assistant_speech("Ich bin bereit für den Tag.")  # i18n-allow
    p._register_assistant_speech("Was geht bei dir?")  # i18n-allow: voice fixture
    assert p._looks_like_self_echo("für den Tag was geht") is True  # i18n-allow


def test_without_any_assistant_speech_nothing_is_flagged() -> None:
    p = _pipeline()
    assert p._looks_like_self_echo("bei mir läuft alles") is False  # i18n-allow
