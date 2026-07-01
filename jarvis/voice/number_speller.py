"""Deterministic number-to-words normalization for the voice path.

Text-to-speech reads a bare digit inconsistently across engines and locales, so
the voice persona mandates spelling every number out as words ("drei Komma acht
Zentimeter", never "3,8 cm"). This module is the deterministic backstop that
guarantees it regardless of which brain/provider produced the text — the
open-source doctrine forbids relying on one model obeying a prose rule, and a
flash-tier model in particular still emits digits despite the instruction.

Rule-based via ``num2words`` — no LLM call, microsecond-fast, safe on the AP-11
hot path. It never raises, and when ``num2words`` is unavailable (a minimal
install that did not pull the dependency) it is a transparent no-op so text
passes through unchanged instead of crashing the voice path.

Locale-aware separators: German/Spanish use a comma decimal and dot thousands
("3,8" = three-point-eight, "1.000" = one thousand); English is the reverse.
Times ("20:30") are spoken as "zwanzig Uhr dreißig" before the general pass so
the colon parts are not spelled as two bare integers.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

try:  # optional dependency — a minimal install may lack it (open-source doctrine)
    from num2words import num2words as _num2words

    _HAVE_NUM2WORDS = True
except Exception:  # noqa: BLE001 — any import failure degrades to a no-op
    _HAVE_NUM2WORDS = False

# Supported locales. Anything else falls through unchanged (honesty over a wrong
# guess — never spell a French number with German words).
_SUPPORTED = ("de", "en", "es")

# (decimal separator, thousands separator) per locale.
_SEPARATORS: dict[str, tuple[str, str]] = {
    "de": (",", "."),
    "es": (",", "."),
    "en": (".", ","),
}

# Time connector between hour and minute words, per locale.
_TIME_JOIN: dict[str, str] = {"de": " Uhr ", "en": " ", "es": " y "}
_TIME_JOIN_OCLOCK: dict[str, str] = {"de": " Uhr", "en": " o'clock", "es": " en punto"}

# A clock time, optionally followed by a German "Uhr" that we consume so the
# spoken form is not doubled ("09:17 Uhr" -> "neun Uhr siebzehn", not
# "neun Uhr siebzehn Uhr").
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b(\s*Uhr\b)?")
# A number token: a digit, or a run of digits possibly carrying group/decimal
# separators, always ending on a digit so a trailing "." / "," stays as
# punctuation. Letter-adjacent digits are skipped (a lookbehind/lookahead for a
# Unicode letter) so an identifier like "abc123def456", "cp1252", or "utf8" is
# left intact — only a free-standing number is spoken.
_LETTER = r"[^\W\d_]"
_NUMBER_RE = re.compile(
    rf"(?<!{_LETTER})(?:\d[\d.,]*\d|\d)(?!{_LETTER})"
)


def _norm_lang(language: str | None) -> str:
    if not language:
        return "de"
    low = language.lower()
    if low.startswith("en"):
        return "en"
    if low.startswith("es"):
        return "es"
    if low.startswith("de"):
        return "de"
    return low  # unknown → not in _SUPPORTED → passthrough


def _spell_value(value: int | float, lang: str) -> str | None:
    try:
        return _num2words(value, lang=lang)
    except Exception:  # noqa: BLE001 — never let a spelling failure raise
        return None


def _spell_number_token(token: str, lang: str) -> str | None:
    decimal_sep, thousands_sep = _SEPARATORS[lang]
    cleaned = token.replace(thousands_sep, "")
    if decimal_sep in cleaned:
        int_part, _, frac_part = cleaned.partition(decimal_sep)
        if not frac_part.isdigit():
            return None
        try:
            value: float | int = float(f"{int_part or '0'}.{frac_part}")
        except ValueError:
            return None
        return _spell_value(value, lang)
    if not cleaned.isdigit():
        return None
    # Guard pathological runs (e.g. a 60-digit id) — leave them for the reader.
    if len(cleaned) > 18:
        return None
    return _spell_value(int(cleaned), lang)


def _spell_time(hour: str, minute: str, lang: str) -> str | None:
    h_word = _spell_value(int(hour), lang)
    if h_word is None:
        return None
    if int(minute) == 0:
        return f"{h_word}{_TIME_JOIN_OCLOCK[lang]}"
    m_word = _spell_value(int(minute), lang)
    if m_word is None:
        return None
    return f"{h_word}{_TIME_JOIN[lang]}{m_word}"


def spell_out_numbers(text: str, language: str = "de") -> str:
    """Spell every digit run in ``text`` out as words for the given locale.

    Never raises. A no-op when ``num2words`` is missing, the language is
    unsupported, or the text has no digits. Individual tokens that cannot be
    parsed are left untouched rather than dropped.
    """
    if not _HAVE_NUM2WORDS or not text:
        return text
    lang = _norm_lang(language)
    if lang not in _SUPPORTED:
        return text
    if not any(ch.isdigit() for ch in text):
        return text

    def _time_sub(match: re.Match[str]) -> str:
        spelled = _spell_time(match.group(1), match.group(2), lang)
        return spelled if spelled is not None else match.group(0)

    out = _TIME_RE.sub(_time_sub, text)

    def _num_sub(match: re.Match[str]) -> str:
        spelled = _spell_number_token(match.group(0), lang)
        return spelled if spelled is not None else match.group(0)

    return _NUMBER_RE.sub(_num_sub, out)


__all__ = ["spell_out_numbers"]
