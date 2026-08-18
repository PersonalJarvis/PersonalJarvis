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


# --- structured forms: dates, versions, IP addresses -----------------------
# A dot is not always a separator. Before these, every dot was stripped as a
# thousands separator and "17.08.2026" was spoken as a seventeen-million figure.


@requires_num2words
def test_full_date_german_is_spoken_as_a_date():
    out = spell_out_numbers("Der Termin ist am 17.08.2026.", "de")  # i18n-allow
    assert not any(c.isdigit() for c in out)
    assert "siebzehnten August zweitausendsechsundzwanzig" in out  # i18n-allow


@requires_num2words
def test_two_digit_year_german():
    out = spell_out_numbers("am 17.08.26", "de")  # i18n-allow
    assert "siebzehnten August zweitausendsechsundzwanzig" in out  # i18n-allow


@requires_num2words
def test_german_ordinal_ending_follows_the_governing_word():
    dative = spell_out_numbers("Wir sehen uns am 5.3.", "de")  # i18n-allow
    article = spell_out_numbers("Der 17.08.2026 ist ein Montag.", "de")  # i18n-allow
    assert "am fünften März" in dative  # i18n-allow
    assert "Der siebzehnte August" in article  # i18n-allow


@requires_num2words
def test_short_date_form_german():
    out = spell_out_numbers("Der Kurs startet am 5.3. um 20:30.", "de")  # i18n-allow
    assert not any(c.isdigit() for c in out)
    assert "am fünften März" in out  # i18n-allow
    assert "zwanzig Uhr dreißig" in out  # i18n-allow


@requires_num2words
def test_version_number_is_not_a_date_without_a_date_cue():
    # "Python 3.11." ends a sentence; the dot is punctuation, not an ordinal.
    out = spell_out_numbers("Ich nutze Python 3.11.", "de")  # i18n-allow
    assert not any(c.isdigit() for c in out)
    assert "drei Punkt elf" in out  # i18n-allow
    assert "November" not in out


@requires_num2words
def test_dotted_version_german():
    out = spell_out_numbers("Version 1.2.3 ist da.", "de")  # i18n-allow
    assert not any(c.isdigit() for c in out)
    assert "eins Punkt zwei Punkt drei" in out  # i18n-allow


@requires_num2words
def test_ipv4_german_is_spoken_group_by_group():
    out = spell_out_numbers("Die Adresse ist 192.168.1.1.", "de")  # i18n-allow
    assert not any(c.isdigit() for c in out)
    assert "einhundertzweiundneunzig Punkt einhundertachtundsechzig" in out  # i18n-allow
    assert out.endswith(".")


@requires_num2words
def test_thousands_still_one_number_after_the_structured_forms():
    out = spell_out_numbers("Das kostet 1.500 Euro.", "de")  # i18n-allow
    assert not any(c.isdigit() for c in out)
    assert "eintausendfünfhundert" in out  # i18n-allow


@requires_num2words
def test_mixed_thousands_and_decimal_german():
    out = spell_out_numbers("Das sind 1.234,56 Euro.", "de")  # i18n-allow
    assert not any(c.isdigit() for c in out)
    assert "eintausendzweihundertvierunddreißig Komma" in out  # i18n-allow


@requires_num2words
def test_ungrouped_dot_is_never_treated_as_a_thousands_separator():
    # Not an exact triple behind the dot -> not a thousands separator. Passing
    # the token through is honest; fusing it into "12345678" is not.
    out = spell_out_numbers("Code 12345.678 hier.", "de")  # i18n-allow
    assert "zwölf Millionen" not in out  # i18n-allow


@requires_num2words
def test_full_date_english():
    out = spell_out_numbers("The release landed on 17.08.2026.", "en")
    assert not any(c.isdigit() for c in out)
    assert "August seventeenth twenty twenty-six" in out


@requires_num2words
def test_english_decimal_still_wins_over_the_version_rule():
    # In English the dot IS the decimal point, so a two-group token stays a
    # decimal — "3.14159" must never become "fourteen thousand".
    assert "three point one four one five nine" in spell_out_numbers("pi is 3.14159", "en")
    assert "three point eight" in spell_out_numbers("It moves 3.8 cm.", "en")


@requires_num2words
def test_dotted_version_and_ip_english():
    assert "one point two point three" in spell_out_numbers("version 1.2.3", "en")
    ip = spell_out_numbers("192.168.1.1", "en")
    assert not any(c.isdigit() for c in ip)
    assert ip.count("point") == 3


@requires_num2words
def test_english_thousands_separator_survives():
    out = spell_out_numbers("It costs 1,500 dollars.", "en")
    assert not any(c.isdigit() for c in out)
    assert "one thousand" in out


@requires_num2words
def test_full_date_spanish():
    out = spell_out_numbers("La cita es el 17.08.2026.", "es")
    assert not any(c.isdigit() for c in out)
    assert "diecisiete de agosto de dos mil veintiséis" in out


@requires_num2words
def test_version_and_thousands_spanish():
    assert "uno punto dos punto tres" in spell_out_numbers("version 1.2.3", "es")
    out = spell_out_numbers("Cuesta 1.500 euros.", "es")
    assert not any(c.isdigit() for c in out)
    assert "mil quinientos" in out
