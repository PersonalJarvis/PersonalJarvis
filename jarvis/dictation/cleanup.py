"""Deterministic filler-sound removal for dictated text.

Why deterministic and not a model pass
--------------------------------------
A dictation feature's whole promise is "these are my words". A second model
pass can rewrite, shorten and "improve" — and when it does, the user has no way
to tell. Rules can also be wrong, but they are wrong in a way you can read,
predict and correct. So the default path is regex only: **no LLM call ever
happens in this module** (same discipline as ``scrub_for_voice``, AP-11).

Three guards keep content words safe
------------------------------------
1. **Whole-word matches from a curated list, nothing else.** No stemming, no
   fuzzy matching, no "drop short words". A sound is removed only because it is
   explicitly listed as a filler for that language.
2. **A destruction ceiling.** If the rules would drop more than
   ``max_removed_fraction`` of the words, the RAW text is returned untouched.
   A cleanup that eats a quarter of a sentence is a bug, not a cleanup.
3. **An unknown language is a no-op.** Applying English rules to Spanish speech
   is how "esto" becomes a filler. No rules for the detected language means the
   text is passed through unchanged.

The lists are deliberately SHORT. Only unambiguous hesitation sounds qualify.
Words that carry meaning in ordinary speech are excluded on purpose even when
every style guide calls them filler — English "like", "actually", "basically";
German "also", "halt", "eben"; Spanish "este", "pues". Removing those changes
what was said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# i18n-allow: the tables below are speech-recognition vocabulary — the literal
# German/Spanish tokens a matcher must contain to recognise a German or Spanish
# hesitation sound. Matching data, not prose (CLAUDE.md §1, category 3).

#: Hesitation sounds per language. Keys are lowercase two-letter codes.
#: Multi-word entries are matched as a phrase.
FILLER_WORDS: dict[str, tuple[str, ...]] = {
    "en": (
        "uh", "uhh", "uhhh", "um", "umm", "ummm",
        "er", "erm", "ah", "ahh", "eh",
        "hm", "hmm", "hmmm", "mhm", "mm", "mmm",
    ),
    "de": (  # i18n-allow: speech-recognition input vocabulary (§1 list #3)
        "äh", "ähh", "ähhh", "ähm", "ähem", "hä",  # i18n-allow: input vocab
        "öh", "öhm", "em",  # i18n-allow: input vocab
        "hm", "hmm", "hmmm", "mhm", "mmh", "mm",
    ),
    "es": (
        "eh", "ehh", "ehhh", "em", "emm",
        "mm", "mmm", "ajá", "aja",
    ),
}

#: Languages we have curated rules for. Anything else is a documented no-op.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset(FILLER_WORDS)

# STT providers do NOT agree on how they name a language. faster-whisper returns
# an ISO code ("de"), while a Whisper cloud endpoint returns the English NAME
# ("German" / "english") — a live dictation on 2026-07-28 came back as
# ``language="English"``, which meant every cleanup silently resolved to
# "no rules for this language" and never ran. Accepting both spellings is what
# makes the feature work with whichever provider the user happens to have
# (AP-21: gate on capability, never on a provider's conventions).
_LANGUAGE_NAME_TO_CODE: dict[str, str] = {
    "english": "en",
    "german": "de",
    "deutsch": "de",
    "spanish": "es",
    "castilian": "es",
    "español": "es",
    "espanol": "es",
}

# The ceiling exists to catch BROKEN RULES (a content word that slipped into a
# filler list would eat half a sentence), not to punish someone who hesitates a
# lot. A percentage alone cannot do that job at both ends: one filler in a
# three-word sentence is already 33 %, while 25 % of a 100-word dictation would
# be twenty-five hesitation sounds — far past "broken". So the test is staged.
#
#: Under this word count no proportional test runs at all; the "nothing
#: survived" check is the only guard ("Ähm ja" -> "ja" must work).  # i18n-allow: quoted input
_SHORT_TEXT_WORDS = 4
#: Up to this word count an ABSOLUTE cap applies instead of a proportion.
_ABSOLUTE_CAP_WORDS = 12
#: The absolute cap itself. Three hesitation sounds in a short sentence is
#: plausible speech; a fourth means the rules are matching something else.
_ABSOLUTE_MAX_REMOVED = 3

_WORD_RE = re.compile(r"\w+", re.UNICODE)
# A filler is only ever removed as a WHOLE word. \b does not behave for tokens
# containing non-ASCII letters on some patterns, so the boundaries are spelled
# out as "not preceded/followed by a word character".
_BOUNDARY_PREFIX = r"(?<![^\W\d_])"
_BOUNDARY_SUFFIX = r"(?![^\W\d_])"


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Outcome of one cleanup pass.

    ``text`` is what should be inserted; ``raw`` is always the untouched
    transcript, so the caller can store both and show what changed.
    """

    text: str
    raw: str
    removed_words: int
    total_words: int
    applied: bool
    #: "" when applied, otherwise why it was not: ``disabled`` | ``no_rules``
    #: | ``ceiling`` | ``empty``.
    reason: str = ""

    @property
    def changed(self) -> bool:
        return self.applied and self.text != self.raw


def normalize_language(language: str | None) -> str | None:
    """``"de-DE"`` / ``"DE"`` / ``"German"`` -> ``"de"``; unknown -> ``None``.

    Accepts BOTH spellings providers use — an ISO code and the English language
    name — because they genuinely differ per provider (see
    ``_LANGUAGE_NAME_TO_CODE``). ``auto`` and ``unknown`` (what the providers
    return when they could not tell) map to ``None`` on purpose: guessing here
    would apply one language's filler list to another's speech.
    """
    if not language:
        return None
    value = str(language).strip().lower().replace("_", "-")
    if not value or value in ("auto", "unknown", "und"):
        return None
    named = _LANGUAGE_NAME_TO_CODE.get(value)
    if named is not None:
        return named
    code = value.split("-", 1)[0]
    return code if code in SUPPORTED_LANGUAGES else None


def _count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _compile_patterns(language: str) -> list[re.Pattern[str]]:
    """Whole-word patterns for one language, longest phrase first.

    Longest-first matters for multi-word entries: a short rule must never
    shadow a longer phrase that contains it.
    """
    words = sorted(FILLER_WORDS[language], key=len, reverse=True)
    patterns: list[re.Pattern[str]] = []
    for word in words:
        escaped = r"\s+".join(re.escape(part) for part in word.split())
        patterns.append(
            re.compile(
                _BOUNDARY_PREFIX + escaped + _BOUNDARY_SUFFIX,
                re.IGNORECASE | re.UNICODE,
            )
        )
    return patterns


# Compiled once per language at import; the tables are constant.
_PATTERN_CACHE: dict[str, list[re.Pattern[str]]] = {
    lang: _compile_patterns(lang) for lang in FILLER_WORDS
}


def _tidy(text: str, *, raw: str) -> str:
    """Repair the punctuation and spacing a removal leaves behind.

    Strictly repair, never restyle: collapse the whitespace a removed word left,
    drop punctuation that now has nothing in front of it, and restore a leading
    capital ONLY when the raw text had one (so a German example that starts on a
    filler keeps its capital instead of turning into a lowercase sentence).
    """
    out = text
    # A removal can leave " , " or " ." — pull the mark back onto the previous word.
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    # Two marks that ended up adjacent ("..,") collapse to the first.
    out = re.sub(r"([,;:])\s*([,.;:!?])", r"\2", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    # Leading punctuation left by a filler at the start of the text.
    out = re.sub(r"^[\s,;:.!?\-–—]+", "", out)
    out = out.strip()
    if out and raw[:1].isupper() and out[:1].islower():
        out = out[:1].upper() + out[1:]
    return out


def clean_transcript(
    text: str,
    *,
    language: str | None = None,
    remove_fillers: bool = True,
    max_removed_fraction: float = 0.25,
) -> CleanupResult:
    """Remove hesitation sounds from ``text``. Never raises.

    Returns a :class:`CleanupResult` whose ``text`` is safe to insert. When the
    cleanup is refused (disabled, unknown language, or the destruction ceiling),
    ``text`` is the raw transcript and ``reason`` says why.
    """
    raw = text or ""
    stripped = raw.strip()
    if not stripped:
        return CleanupResult(
            text=raw, raw=raw, removed_words=0, total_words=0,
            applied=False, reason="empty",
        )
    if not remove_fillers:
        return CleanupResult(
            text=raw, raw=raw, removed_words=0, total_words=_count_words(raw),
            applied=False, reason="disabled",
        )

    lang = normalize_language(language)
    if lang is None:
        # Honest no-op: we would rather leave a filler in than damage a
        # sentence in a language whose rules we do not have.
        return CleanupResult(
            text=raw, raw=raw, removed_words=0, total_words=_count_words(raw),
            applied=False, reason="no_rules",
        )

    total = _count_words(raw)
    cleaned = raw
    try:
        for pattern in _PATTERN_CACHE[lang]:
            cleaned = pattern.sub(" ", cleaned)
        cleaned = _tidy(cleaned, raw=raw)
    except Exception:  # noqa: BLE001 — a broken rule must never eat the dictation
        return CleanupResult(
            text=raw, raw=raw, removed_words=0, total_words=total,
            applied=False, reason="error",
        )

    remaining = _count_words(cleaned)
    removed = max(0, total - remaining)

    # Nothing survived — always a defect in the rules, never a valid result.
    if not cleaned.strip():
        return CleanupResult(
            text=raw, raw=raw, removed_words=0, total_words=total,
            applied=False, reason="ceiling",
        )

    if _SHORT_TEXT_WORDS <= total <= _ABSOLUTE_CAP_WORDS:
        if removed > _ABSOLUTE_MAX_REMOVED:
            return CleanupResult(
                text=raw, raw=raw, removed_words=removed, total_words=total,
                applied=False, reason="ceiling",
            )
    elif total > _ABSOLUTE_CAP_WORDS:
        fraction = removed / total if total else 0.0
        if fraction > max_removed_fraction:
            return CleanupResult(
                text=raw, raw=raw, removed_words=removed, total_words=total,
                applied=False, reason="ceiling",
            )

    return CleanupResult(
        text=cleaned, raw=raw, removed_words=removed, total_words=total,
        applied=True, reason="",
    )


__all__ = [
    "FILLER_WORDS",
    "SUPPORTED_LANGUAGES",
    "CleanupResult",
    "clean_transcript",
    "normalize_language",
]
