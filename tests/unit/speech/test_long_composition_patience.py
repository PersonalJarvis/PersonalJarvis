"""Vocabulary-independent silence-window patience for long/complex dictation.

Root cause (deep dive 2026-06-16): a long spoken prompt was chopped into ~12
turns because the 1.5 s silence window is too short for composing, and the
existing patience only widened on narrow delegation keywords (sub-agent / spawn
/ delegate / openclaw legacy alias). A prompt full of "Agents" / "Agent Team" never matched,
so the window stayed 1.5 s and cut at every thinking pause. These tests pin a
vocabulary-independent trigger: a long partial extends the window, while a short
command stays snappy.
"""
from __future__ import annotations

from jarvis.speech.pipeline import (
    _looks_like_long_composition,
    _should_extend_silence_for_composition,
)


def test_short_command_is_not_long_composition():
    assert _looks_like_long_composition("öffne Chrome") is False
    assert _looks_like_long_composition("auflegen") is False


def test_long_partial_triggers_patience_regardless_of_vocabulary():
    # The exact prompt shape that was chopped — no delegation keyword present.
    long = (
        "Du machst zwei verschiedene Agents der eine Agent kümmert sich um das "
        "eine Feature der andere um das andere"
    )
    assert _looks_like_long_composition(long) is True


def test_mid_length_question_triggers_patience():
    # Live bug 2026-06-18 (session b34a4bba): the user said
    # "Hey Jarvis, was geht ab? Kannst du mir bitte mal …" and paused to think
    # after "mal". The 10-word partial fell just under the old 12-word threshold,
    # so it got only the base 1.5 s silence window and was cut mid-sentence. A
    # mid-length, clearly-unfinished question must reach the patience extension.
    mid = "Hey Jarvis, was geht ab? Kannst du mir bitte mal"
    assert len(mid.split()) == 10
    assert _looks_like_long_composition(mid) is True


def test_empty_is_not_long_composition():
    assert _looks_like_long_composition("") is False
    assert _looks_like_long_composition(None) is False


def test_combined_trigger_covers_delegation_long_and_keeps_short_snappy():
    # delegation keyword (existing path) still triggers
    assert _should_extend_silence_for_composition(
        "spawn a sub-agent that researches X"
    ) is True
    # long non-delegation dictation (new path) triggers
    assert _should_extend_silence_for_composition(
        "okay also ich möchte dass du mir jetzt bitte ein ganz langes Programm "
        "baust das ganz viele verschiedene Dinge können soll"
    ) is True
    # short complete command stays snappy (no extension)
    assert _should_extend_silence_for_composition("mach das Licht an") is False
