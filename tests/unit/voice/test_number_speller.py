"""Unit tests for the voice number speller (deterministic digit->words)."""
from __future__ import annotations

import importlib.util

import pytest

from jarvis.voice.number_speller import spell_out_numbers

_HAVE_NUM2WORDS = importlib.util.find_spec("num2words") is not None
requires_num2words = pytest.mark.skipif(
    not _HAVE_NUM2WORDS, reason="num2words not installed"
)


@requires_num2words
def test_no_bare_digit_survives_german():
    out = spell_out_numbers("Der Mond entfernt sich 3,8 Zentimeter pro Jahr.", "de")  # i18n-allow
    assert not any(c.isdigit() for c in out)
    assert "drei Komma acht" in out


@requires_num2words
def test_integer_and_large_number_german():
    out = spell_out_numbers("Vor 4 Milliarden Jahren, im Jahr 2026.", "de")
    assert not any(c.isdigit() for c in out)
    assert "vier Milliarden" in out
    assert "zweitausendsechsundzwanzig" in out


@requires_num2words
def test_thousands_separator_german_is_one_number():
    # German dot is a thousands separator, not a decimal point.
    out = spell_out_numbers("Das kostet 1.000 Euro.", "de")
    assert not any(c.isdigit() for c in out)
    assert "eintausend" in out


@requires_num2words
def test_decimal_english_uses_point():
    out = spell_out_numbers("It moves 3.8 centimeters.", "en")
    assert not any(c.isdigit() for c in out)
    assert "three point eight" in out


@requires_num2words
def test_time_german():
    out = spell_out_numbers("Wir treffen uns um 20:30.", "de")
    assert not any(c.isdigit() for c in out)
    assert "zwanzig Uhr dreißig" in out  # i18n-allow


@requires_num2words
def test_full_hour_german():
    out = spell_out_numbers("Es ist 15:00.", "de")
    assert not any(c.isdigit() for c in out)
    assert "fünfzehn Uhr" in out  # i18n-allow


@requires_num2words
def test_spanish_integer():
    out = spell_out_numbers("Hay 4 proyectos.", "es")
    assert not any(c.isdigit() for c in out)
    assert "cuatro" in out


def test_no_digits_passthrough_unchanged():
    text = "Ein ganz normaler Satz ohne Zahlen."
    assert spell_out_numbers(text, "de") == text


def test_empty_and_none_safe():
    assert spell_out_numbers("", "de") == ""


def test_unsupported_language_passthrough():
    # A French sentence must not be spelled with German words — pass through.
    text = "Il y a 4 projets."
    assert spell_out_numbers(text, "fr") == text


@requires_num2words
def test_never_raises_on_weird_input():
    # Long digit run + trailing separators must not crash; returns a string.
    weird = "id 123456789012345678901234567890, ratio 5,, code 7."
    out = spell_out_numbers(weird, "de")
    assert isinstance(out, str)


@requires_num2words
def test_trailing_punctuation_preserved():
    out = spell_out_numbers("Es sind 3.", "de")
    assert out.endswith(".")
    assert "drei" in out
