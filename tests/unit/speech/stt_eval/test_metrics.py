"""Multilingual STT evaluation metrics."""

from jarvis.speech.stt_eval.metrics import (
    repeatability_error_rate,
    switch_error_rate,
)


def test_switch_anchors_cover_multiple_language_families_and_names() -> None:
    hypothesis = (
        "Please ask Nova mañana por la mañana ثم send the report "
        "and finish with 東京チームお願いします"
    )  # i18n-allow: multilingual STT fixture under test
    anchors = (
        "Nova mañana por la mañana",  # i18n-allow: multilingual fixture
        "mañana ثم send",  # i18n-allow: multilingual fixture
        "with 東京チームお願いします",  # i18n-allow: multilingual fixture
    )

    assert switch_error_rate(anchors, hypothesis) == 0.0


def test_missing_switch_anchor_is_counted() -> None:
    assert switch_error_rate(("hello mundo", "mundo again"), "hello world again") == 1.0


def test_no_switch_annotation_is_not_a_zero_measurement() -> None:
    assert switch_error_rate((), "anything") is None


def test_repeatability_is_word_error_against_the_first_run() -> None:
    assert repeatability_error_rate(("one two three", "one two three")) == 0.0
    assert repeatability_error_rate(("one two three", "one four three")) == 1 / 3
    assert repeatability_error_rate(("one",)) is None
