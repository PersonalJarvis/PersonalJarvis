"""Deterministic filler-sound removal and punctuation repair for dictated text.

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

A filler must also be meaningless in EVERY language this module has rules for,
not only in its own. The language tag arrives from the STT provider and is
regularly wrong, so an English list containing "er" is one bad tag away from
deleting every pronoun in a German sentence — and reporting ``applied=True``
while doing it. See the comment on :data:`FILLER_WORDS`.

The second job of this module is punctuation repair (:func:`tidy_transcript`),
and unlike filler removal it runs on EVERY dictation. The damage it repairs is
manufactured by the SEGMENTING transcriber — each ~8 s segment is punctuated
and capitalised on its own and the pieces are then joined with a bare space —
so it is present in transcripts this module never otherwise touches. It is
regex only for the same reason as everything else here: no LLM call ever
happens in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# i18n-allow: the tables below are speech-recognition vocabulary — the literal
# German/Spanish tokens a matcher must contain to recognise a German or Spanish
# hesitation sound. Matching data, not prose (CLAUDE.md §1, category 3).

#: Hesitation sounds per language. Keys are lowercase two-letter codes.
#: Multi-word entries are matched as a phrase.
#:
#: DE-MINED on 2026-07-29: "er", "ah" and "eh" were removed from the English
#: table and "eh" from the Spanish one. Every style guide calls them hesitation
#: sounds, and in English they are — but "er" is a top-frequency GERMAN pronoun
#: ("he") and "eh" is ordinary German for "anyway". The tag that selects the
#: table is whatever the provider reported, and cloud Whisper endpoints answer
#: "English" for German speech often enough that it is a documented defect, so
#: those three tokens turned a wrong tag into silent deletion of content words:
#: a 45-word German dictation lost all four occurrences of "er" at
#: ``applied=True removed_words=4``, which is 8.9 % — comfortably under the
#: destruction ceiling, so no guard fired and nothing was reported. A token that
#: is a content word in ANY language this module serves does not belong in a
#: list applied on a provider's word. The longer spellings ("erm", "ahh",
#: "ehh") stay: no language spells a word that way.
#:
#: DE-MINED again on 2026-07-30: "um" left the English table for exactly the
#: same reason, and it was the most damaging entry of the family. It is the
#: canonical English hesitation sound AND one of the highest-frequency German
#: prepositions ("um fünf Uhr", "es geht um", "kümmere dich um"), so a German  # i18n-allow
#: utterance tagged English lost the preposition out of the MIDDLE of the
#: sentence:
#:     "Kümmere dich um das Update" -> "Kümmere dich das Update"  # i18n-allow
#: One dropped function word there is a fraction of a percent of the text, so
#: the destruction ceiling never fires and nothing is reported — the sentence
#: simply arrives ungrammatical and the router acts on a mangled instruction.
#: English dictation keeps "umm"/"ummm" (no language spells a word that way);
#: a bare spoken "um" now survives into English text, which is the cheap half
#: of this trade and visible to the person who said it.
FILLER_WORDS: dict[str, tuple[str, ...]] = {
    "en": (
        "uh", "uhh", "uhhh", "umm", "ummm",
        "erm", "ahh",
        "hm", "hmm", "hmmm", "mhm", "mm", "mmm",
    ),
    "de": (  # i18n-allow: speech-recognition input vocabulary (§1 list #3)
        "äh", "ähh", "ähhh", "ähm", "ähem", "hä",  # i18n-allow: input vocab
        "öh", "öhm", "em",  # i18n-allow: input vocab
        "hm", "hmm", "hmmm", "mhm", "mmh", "mm",
    ),
    "es": (
        "ehh", "ehhh", "em", "emm",
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


def count_words(text: str) -> int:
    """How many words a transcript contains.

    Public because the history and the statistics sidecar need the SAME answer
    the destruction ceiling uses. A second word regex living somewhere else
    would drift from this one the first time either is touched, and then
    "words dictated" and "words removed" would stop being comparable numbers.
    """
    return len(_WORD_RE.findall(text or ""))


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
    # A removal can leave " , " or " ." — pull the mark back onto the previous
    # word. The ``\.(?!\.)`` is not decoration: the naive ``[,.;:!?]`` version of
    # this rule pulled the space out of "gesprochen. ... ist" and produced
    # "gesprochen.... ist" — a defect this module MANUFACTURED on text it had no
    # business touching, seen verbatim in a live dictation on 2026-07-28. A dot
    # that another dot follows is part of an ellipsis, never a mark to reattach;
    # ``tidy_transcript`` decides what to do with it.
    out = re.sub(r"\s+([,;:!?]|\.(?!\.))", r"\1", out)
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
            text=raw, raw=raw, removed_words=0, total_words=count_words(raw),
            applied=False, reason="disabled",
        )

    lang = normalize_language(language)
    if lang is None:
        # Honest no-op: we would rather leave a filler in than damage a
        # sentence in a language whose rules we do not have.
        return CleanupResult(
            text=raw, raw=raw, removed_words=0, total_words=count_words(raw),
            applied=False, reason="no_rules",
        )

    total = count_words(raw)
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

    remaining = count_words(cleaned)
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


# ---------------------------------------------------------------------------
# Punctuation repair at the segment joins
# ---------------------------------------------------------------------------

#: What a recognizer emits at a pause: a run of two or more dots, or the single
#: "…" glyph. Both spellings occur, sometimes in the same transcript.
_ELLIPSIS = r"(?:\.{2,}|…)"

#: A sentence terminator with a recognizer ellipsis glued to it, either directly
#: ("gesprochen...") or across the space a segment join left ("gesprochen. ...").
#: The ``(?<=\w)`` lookbehind is load-bearing: it demands a WORD before the
#: terminator, so the first dot of a free-standing " ... " can never be mistaken
#: for the terminator and the speaker's own ellipsis survives the rule.
_JOINED_ELLIPSIS_RE = re.compile(r"(?<=\w)([.!?])[ \t]*" + _ELLIPSIS)

#: The same artifact spelled with the "…" glyph, which carries no terminator to
#: keep: "gesprochen… ist". Only repaired when text follows — a trailing "…"
#: ends nothing and may well be what the speaker meant.
_ATTACHED_GLYPH_RE = re.compile(r"(?<=\w)…(?=[ \t]+\S)")

#: A free-standing ellipsis between two segments. Whether it is an artifact or
#: the speaker's own pause is undecidable from the text alone, so the decision
#: is made in :func:`_repair_standalone_ellipsis` on the ONE signal that exists:
#: the recognizer capitalises the start of every segment.
_STANDALONE_ELLIPSIS_RE = re.compile(
    r"(?<=\w)[ \t]+" + _ELLIPSIS + r"[ \t]+(?=(?P<next>\w))"
)

#: EXACTLY two dots, not part of a longer run. ".." is always a join artifact;
#: "..." is an ellipsis and must not be reduced to it one dot at a time.
_DOUBLE_DOT_RE = re.compile(r"(?<!\.)\.[ \t]*\.(?!\.)")

#: ".," / "., " / ".;" — a sentence terminator that a weak mark follows. The
#: terminator wins: it is the stronger claim about where the sentence ended.
_TERMINATOR_THEN_WEAK_RE = re.compile(r"([.!?])[ \t]*[,;:]+")

#: ",." / ",," — a weak mark that any mark follows; the second one wins. The
#: ``(?!\.)`` keeps the rule off an ellipsis that merely stands after a comma.
_WEAK_THEN_MARK_RE = re.compile(r"[,;:][ \t]*([,;:.!?])(?!\.)")

#: A space in front of a full stop. Deliberately period-only: French sets a
#: space before "?", "!", ":" and ";" on purpose, and this function runs on
#: every language.
_SPACE_BEFORE_PERIOD_RE = re.compile(r"[ \t]+(\.(?!\.))")

_DOUBLE_SPACE_RE = re.compile(r"[ \t]{2,}")

#: A sentence start a segment join left lower-case. ``(?<=\w\w)`` requires two
#: word characters in front of the terminator, which keeps the rule off single
#: letter abbreviations ("z. B. das") and off the tail dot of an ellipsis.
_SENTENCE_START_RE = re.compile(r"(?<=\w\w)([.!?])([ \t]+)([^\W\d_])")


def _repair_standalone_ellipsis(match: re.Match[str]) -> str:
    """A free-standing ellipsis is only a join artifact when a segment follows.

    The recognizer capitalises the first word of every segment it produces, so
    an upper-case continuation after " ... " means a new segment began there and
    the ellipsis is ours to remove. A lower-case continuation is the speaker
    trailing off mid-sentence, and that stays exactly as dictated.
    """
    if not match.group("next").isupper():
        return match.group(0)
    return ". "


def _upper_sentence_start(match: re.Match[str]) -> str:
    return match.group(1) + match.group(2) + match.group(3).upper()


def tidy_transcript(text: str) -> str:
    """Repair the punctuation a SEGMENTED transcription leaves behind. Never raises.

    This runs on every dictation, not only on one a filler was removed from.
    The damage is not made here: the lane transcribes in ~8 s segments, the
    recognizer punctuates and capitalises each one as if it were the whole
    utterance, emits its own trailing "..." wherever a segment cut a sentence in
    half, and the pieces are then joined with a bare space. So a transcript no
    rule in this module ever touched still arrives with a stray "..." parked
    between two sentences and the next one starting lower-case — which is why
    gating the repair on "a filler was removed" left the common case broken.

    Four repairs, in this order:

    1. an ellipsis glued to a sentence terminator collapses into that terminator;
    2. a free-standing ellipsis before a capitalised word becomes a full stop;
    3. adjacent terminal marks ("..", ".,", "., ") de-duplicate, stronger wins;
    4. a lower-case word after a sentence terminator gets its capital back.

    THE TRADE-OFF, stated plainly
    -----------------------------
    Nothing in the text says whether an ellipsis came from the recognizer or
    from the speaker, so the split is drawn where it is cheapest to be wrong:

    * an **attached** ellipsis ("gesprochen... ist") is treated as an artifact
      and collapsed — a speaker who dictated a trailing pause there loses it;
    * a **free-standing** ellipsis ("das war ... schwierig") is kept, unless the
      next word is capitalised, which only a segment boundary produces.

    Rule 4 has a matching cost: it capitalises after an abbreviation that ends
    in a period and is at least two letters long ("etc. and" -> "etc. And").
    Both errors are cosmetic and visible; the errors they prevent — a doubled
    "...." in the middle of a sentence, a paragraph of lower-case sentence
    starts — are the ones users actually report.

    Returns the text unchanged on anything unexpected: a repair that eats a
    dictation is worse than a transcript that reads slightly wrong.
    """
    original = text or ""
    if not original.strip():
        return original
    try:
        out = _JOINED_ELLIPSIS_RE.sub(r"\1", original)
        out = _ATTACHED_GLYPH_RE.sub(".", out)
        out = _STANDALONE_ELLIPSIS_RE.sub(_repair_standalone_ellipsis, out)
        out = _DOUBLE_DOT_RE.sub(".", out)
        out = _TERMINATOR_THEN_WEAK_RE.sub(r"\1", out)
        out = _WEAK_THEN_MARK_RE.sub(r"\1", out)
        out = _SPACE_BEFORE_PERIOD_RE.sub(r"\1", out)
        out = _DOUBLE_SPACE_RE.sub(" ", out)
        out = _SENTENCE_START_RE.sub(_upper_sentence_start, out)
        return out.strip()
    except Exception:  # noqa: BLE001 — a broken rule must never eat the dictation
        return original


__all__ = [
    "FILLER_WORDS",
    "SUPPORTED_LANGUAGES",
    "CleanupResult",
    "clean_transcript",
    "count_words",
    "normalize_language",
    "tidy_transcript",
]
