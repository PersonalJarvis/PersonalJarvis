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

Structured forms are recognised BEFORE the separator logic, because a dot is
not always a separator. Times ("20:30") are spoken as "zwanzig Uhr dreißig",  # i18n-allow
dates ("17.08.2026") as a spoken date, and version numbers / IP addresses
("3.11", "192.168.1.1") group by group with a spoken "Punkt". Without that,  # i18n-allow
every dot would be stripped as a thousands separator and a date would be read
as an eight-digit integer. A dot only counts as a thousands separator when the
group behind it is an exact triple, which is what a thousands separator always
looks like.
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

# A correctly grouped integer: one to three leading digits, then nothing but
# exact triples. Used to decide whether a separator really is a thousands
# separator before any of them is stripped.
_GROUPED_INT_RE: dict[str, re.Pattern[str]] = {
    "de": re.compile(r"\d{1,3}(?:\.\d{3})+"),
    "es": re.compile(r"\d{1,3}(?:\.\d{3})+"),
    "en": re.compile(r"\d{1,3}(?:,\d{3})+"),
}

# Time connector between hour and minute words, per locale.
_TIME_JOIN: dict[str, str] = {"de": " Uhr ", "en": " ", "es": " y "}
_TIME_JOIN_OCLOCK: dict[str, str] = {"de": " Uhr", "en": " o'clock", "es": " en punto"}

# The word spoken for the dot inside a version number or an IP address.
_DOT_JOIN: dict[str, str] = {"de": " Punkt ", "en": " point ", "es": " punto "}  # i18n-allow

# Month names for the date verbaliser — spoken output vocabulary.
_MONTHS: dict[str, tuple[str, ...]] = {
    "de": (  # i18n-allow
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ),
    "en": (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ),
    "es": (  # i18n-allow
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ),
}

# German ordinals decline, and the ending follows the word governing the date:
# "am fünften März", "der fünfte März", bare "fünfter März". num2words returns
# the "-e" stem, so these two sets pick the suffix.
_DE_CUES_DATIVE = frozenset({  # i18n-allow
    "am", "vom", "zum", "beim", "im", "seit", "ab", "bis", "nach", "vor",
    "den", "dem", "ans",
})
_DE_CUES_ARTICLE = frozenset({  # i18n-allow
    "der", "die", "das", "ein", "eine", "dieser", "diese", "dieses",
})

# A bare "d.m." pair is a date only when a date word introduces it — otherwise
# "Python 3.11." would be spoken as the third of November.
_DATE_CUES: dict[str, frozenset[str]] = {
    "de": _DE_CUES_DATIVE | _DE_CUES_ARTICLE,
    "en": frozenset({"on", "by", "from", "since", "until", "till", "after", "before", "the"}),
    "es": frozenset({"el", "del", "al", "desde", "hasta", "en", "para"}),  # i18n-allow
}

# A clock time, optionally followed by a German "Uhr" that we consume so the
# spoken form is not doubled ("09:17 Uhr" -> "neun Uhr siebzehn", not
# "neun Uhr siebzehn Uhr").
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b(\s*Uhr\b)?")
# A dot-separated numeric token: two or more digit groups joined by dots, plus
# an optional trailing dot (the German ordinal dot in "5.3.", or a sentence
# period). Groups are capped at four digits — longer means it is not a date, a
# version or an address, and the token is left to the general pass. The
# ``(?!,\d)`` tail keeps a mixed "1.234,56" out of here so it stays one number.
_DOTTED_RE = re.compile(r"(?<![\w.,])\d{1,4}(?:\.\d{1,4})+\.?(?!\w)(?!,\d)")
# A number token: a digit, or a run of digits possibly carrying group/decimal
# separators, always ending on a digit so a trailing "." / "," stays as
# punctuation. The token must not touch another word character (``\w`` = letter,
# digit, or underscore) on either side, so a digit embedded in an identifier
# ("abc123def456", "cp1252", "utf8") is left fully intact — only a free-standing
# number is spoken.
_NUMBER_RE = re.compile(r"(?<!\w)(?:\d[\d.,]*\d|\d)(?!\w)")
# The last plain word before a token, used as the date cue.
_LAST_WORD_RE = re.compile(r"([^\W\d_]+)\W*$")


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


def _spell_value(value: int | float, lang: str, to: str = "cardinal") -> str | None:
    try:
        return _num2words(value, lang=lang, to=to)
    except Exception:  # noqa: BLE001 — never let a spelling failure raise
        return None


def _spell_year(value: int, lang: str) -> str | None:
    """Spell a calendar year — "twenty twenty-six", not "two thousand and ..."."""
    spoken = _spell_value(value, lang, to="year")
    return spoken if spoken is not None else _spell_value(value, lang)


def _spell_number_token(token: str, lang: str) -> str | None:
    decimal_sep, thousands_sep = _SEPARATORS[lang]
    int_part, sep, frac_part = token.partition(decimal_sep)
    if thousands_sep in int_part and not _GROUPED_INT_RE[lang].fullmatch(int_part):
        # A separator that is not followed by an exact triple is not a thousands
        # separator. Stripping it would fuse a date or a version into one giant
        # integer, so leave the token for the reader instead.
        return None
    int_part = int_part.replace(thousands_sep, "")
    if sep:
        if not frac_part.isdigit():
            return None
        try:
            value: float | int = float(f"{int_part or '0'}.{frac_part}")
        except ValueError:
            return None
        return _spell_value(value, lang)
    if not int_part.isdigit():
        return None
    # Guard pathological runs (e.g. a 60-digit id) — leave them for the reader.
    if len(int_part) > 18:
        return None
    return _spell_value(int(int_part), lang)


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


def _spell_groups(groups: list[str], lang: str) -> str | None:
    """Spell a version number or an IP address one digit group at a time."""
    words: list[str] = []
    for group in groups:
        word = _spell_value(int(group), lang)
        if word is None:
            return None
        words.append(word)
    return _DOT_JOIN[lang].join(words)


def _resolve_day_month(first: int, second: int) -> tuple[int, int] | None:
    """A dotted date is day-first; accept month-first only when day-first cannot
    be read ("08.17.2026")."""
    if 1 <= first <= 31 and 1 <= second <= 12:
        return first, second
    if 1 <= second <= 31 and 1 <= first <= 12:
        return second, first
    return None


def _expand_year(raw: str) -> int:
    """Two-digit years pivot at 70, the convention every calendar app uses."""
    if len(raw) == 4:
        return int(raw)
    value = int(raw)
    return 2000 + value if value < 70 else 1900 + value


def _spell_date(day: int, month: int, year: int | None, lang: str, cue: str) -> str | None:
    month_name = _MONTHS[lang][month - 1]
    if lang == "de":
        stem = _spell_value(day, "de", to="ordinal")
        if stem is None:
            return None
        if cue in _DE_CUES_DATIVE:
            day_word = f"{stem}n"
        elif cue in _DE_CUES_ARTICLE:
            day_word = stem
        else:
            day_word = f"{stem}r"
        spoken = f"{day_word} {month_name}"
    elif lang == "en":
        ordinal = _spell_value(day, "en", to="ordinal")
        if ordinal is None:
            return None
        spoken = f"{month_name} {ordinal}"
    else:  # Spanish speaks the day of the month as a cardinal
        cardinal = _spell_value(day, "es")
        if cardinal is None:
            return None
        spoken = f"{cardinal} de {month_name}"  # i18n-allow
    if year is None:
        return spoken
    year_word = _spell_year(year, lang)
    if year_word is None:
        return None
    if lang == "es":
        return f"{spoken} de {year_word}"  # i18n-allow
    return f"{spoken} {year_word}"


def _spell_dotted_token(token: str, lang: str, cue: str, at_end: bool) -> str | None:
    """Classify a dot-separated token and spell it in its own shape.

    Returns ``None`` when the token is none of the structured forms, so the
    general number pass still gets its turn.
    """
    trailing_dot = token.endswith(".")
    core = token[:-1] if trailing_dot else token
    groups = core.split(".")
    if not all(group.isdigit() for group in groups):
        return None
    tail = "." if trailing_dot else ""

    # A full date. Any trailing dot here is sentence punctuation, so it stays.
    if (
        len(groups) == 3
        and len(groups[0]) <= 2
        and len(groups[1]) <= 2
        and len(groups[2]) in (2, 4)
    ):
        day_month = _resolve_day_month(int(groups[0]), int(groups[1]))
        if day_month is not None:
            day, month = day_month
            spoken = _spell_date(day, month, _expand_year(groups[2]), lang, cue)
            if spoken is not None:
                return spoken + tail

    # A dotted quad — an IPv4 address, spoken group by group.
    if len(groups) == 4 and all(len(g) <= 3 and int(g) <= 255 for g in groups):
        spoken = _spell_groups(groups, lang)
        if spoken is not None:
            return spoken + tail

    # The short date form "5.3." — the trailing dot is the ordinal marker, not
    # punctuation, so it is consumed unless the date ends the text.
    if (
        trailing_dot
        and len(groups) == 2
        and len(groups[0]) <= 2
        and len(groups[1]) <= 2
        and cue in _DATE_CUES[lang]
    ):
        day_month = _resolve_day_month(int(groups[0]), int(groups[1]))
        if day_month is not None:
            day, month = day_month
            spoken = _spell_date(day, month, None, lang, cue)
            if spoken is not None:
                return spoken + (tail if at_end else "")

    # An ordinary grouped number ("1.500" in German) or an English decimal
    # ("3.8") — one value, handed to the general speller.
    if _SEPARATORS[lang][1] == "." and _GROUPED_INT_RE[lang].fullmatch(core):
        spoken = _spell_number_token(core, lang)
        if spoken is not None:
            return spoken + tail
    if lang == "en" and len(groups) == 2:
        spoken = _spell_number_token(core, lang)
        if spoken is not None:
            return spoken + tail

    # Whatever is left is a version number: "3.11", "1.2.3".
    spoken = _spell_groups(groups, lang)
    return None if spoken is None else spoken + tail


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

    def _dotted_sub(match: re.Match[str]) -> str:
        cue_match = _LAST_WORD_RE.search(match.string[: match.start()])
        cue = cue_match.group(1).lower() if cue_match else ""
        at_end = not match.string[match.end() :].strip()
        spelled = _spell_dotted_token(match.group(0), lang, cue, at_end)
        return spelled if spelled is not None else match.group(0)

    out = _DOTTED_RE.sub(_dotted_sub, out)

    def _num_sub(match: re.Match[str]) -> str:
        spelled = _spell_number_token(match.group(0), lang)
        return spelled if spelled is not None else match.group(0)

    return _NUMBER_RE.sub(_num_sub, out)


__all__ = ["spell_out_numbers"]
