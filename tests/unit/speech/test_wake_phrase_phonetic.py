"""Sound-folding makes the wake matcher robust to ASR spelling variance.

A small local Whisper mis-spells a proper-noun wake word in sound-equivalent
ways ("Nico" -> "Niko" / "Nicko" / "Nikko" / "Neko"). The exact-token match then
fails and the wake needs repeating. Folding sound-equivalent spellings (c/k/ck,
ph/f, y/i, doubled letters) BEFORE the fuzzy compare collapses those variants
onto one form so they match — WITHOUT loosening the ratio threshold, so clearly
different words still do not match (no extra false wakes). Pure Python, so it
behaves identically on Windows, Linux and macOS.
"""
from __future__ import annotations

import pytest

from jarvis.speech.wake_phrase import compile_wake_matcher


@pytest.mark.parametrize(
    "heard",
    ["Nico", "Niko", "Nicko", "Nikko", "Neko", "hey niko", "ok, niko"],
)
def test_sound_equivalent_spellings_of_the_wake_word_match(heard: str) -> None:
    m = compile_wake_matcher("Nico")
    assert m.search(heard) is not None, f"{heard!r} should match the 'Nico' wake"


@pytest.mark.parametrize(
    "heard",
    ["Marco", "Hallo", "Computer", "das war die Welt", "Schule fertig"],  # i18n-allow
)
def test_clearly_different_words_still_do_not_match(heard: str) -> None:
    m = compile_wake_matcher("Nico")
    assert m.search(heard) is None, f"{heard!r} must NOT match the 'Nico' wake"


def test_sound_folding_helps_a_longer_name_too() -> None:
    m = compile_wake_matcher("Marc")
    assert m.search("mark") is not None   # c -> k
    assert m.search("marc") is not None
    assert m.search("computer") is None


def test_double_letter_and_ph_folding() -> None:
    assert compile_wake_matcher("Emma").search("Ema") is not None   # doubled m
    assert compile_wake_matcher("Sophie").search("Sofie") is not None  # ph -> f


def test_prefixed_jarvis_phrase_keeps_bare_word_guard_under_folding() -> None:
    # "Hey Jarvis" runs the same fuzzy/folded path as every phrase (design
    # 2026-07-07). The prefix requirement keeps the bare core word silent.
    m = compile_wake_matcher("Hey Jarvis")
    assert m.search("hey jarvis") is not None
    assert m.search("jarvis") is None       # bare jarvis still rejected
    assert m.search("marco") is None
