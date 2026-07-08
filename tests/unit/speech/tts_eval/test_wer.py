"""The pure word-error-rate helper (no ASR model needed)."""
from __future__ import annotations

from jarvis.speech.tts_eval.metrics import normalize_words, word_error_rate


def test_identical_text_is_zero():
    assert word_error_rate("Hello world", "hello, world!") == 0.0


def test_one_word_substitution():
    # 1 edit over 3 reference words.
    assert word_error_rate("the quick fox", "the slow fox") == 1 / 3


def test_deletion_counts():
    assert word_error_rate("a b c d", "a b d") == 1 / 4


def test_empty_reference():
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "unexpected") == 1.0


def test_punctuation_and_case_normalized():
    assert normalize_words("NASA's PDF, via HTTP!") == ["nasa's", "pdf", "via", "http"]


def test_garbled_hypothesis_scores_high():
    wer = word_error_rate(
        "the meeting is at nine forty five",
        "the meeting is at nine forty five in the morning today now",
    )
    assert wer > 0.0
