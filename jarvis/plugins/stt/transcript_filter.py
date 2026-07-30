"""Transcript cleanup for cloud STT payloads — artifacts, loops, filler, format.

What this is for
----------------
A cloud transcription model returns what it heard, artifacts included: a
repetition loop that repeats one phrase forty times, a hesitation sound, a
stutter, a stray quote pair the model wrapped around its own answer, and
whatever spacing the segmenting produced. Everything downstream — the intent
router, the command gates, the readbacks — reads that string. This module is
the one place where it is turned into the sentence the user actually spoke,
before a provider wraps it in a ``Transcript``.

What it deliberately does NOT do
--------------------------------
* **No LLM call, ever.** Same discipline as ``scrub_for_voice`` (AP-11) and
  ``jarvis.dictation.cleanup``: a second model pass can rewrite and "improve",
  and the user has no way to tell. Regex only — wrong in a way you can read.
* **No content gate.** Nothing here inspects the transcript for a specific
  phrase in order to reject it. A wake word is never verified on transcript
  CONTENT (AP-27), and a cleanup that deletes a sentence because of what it
  says would be that defect wearing a different hat. Every rule below either
  repairs a mechanical artifact or removes a token from a curated list.
* **No second filler table.** The hesitation-sound lists and the punctuation
  repair live in :mod:`jarvis.dictation.cleanup` and are reused from there.
  A copy would drift from the original the first time either is touched, and
  those tables carry hard-won exclusions (see its ``FILLER_WORDS`` docstring:
  English "er" is a top-frequency GERMAN pronoun, and one wrong language tag
  turned that into silent deletion of content words).

Order matters
-------------
1. **Character normalisation.** NFC, because a provider that answers in NFD
   spells an umlaut as two codepoints and every umlaut-bearing filler rule
   then silently misses. Zero-width and control characters go; NBSP becomes a
   normal space.
2. **Wrapping quotes.** One matched outer pair, nothing else.
3. **Repetition loops.** The best-known failure mode of the Whisper family:
   the decoder latches onto a phrase and emits it until the segment ends.
4. **Hyphen stutters.** "w- was" is one word spoken twice, not two words.
5. **Phonetic fixes.** A short, language-tagged table with a strict admission
   rule — see :data:`PHONETIC_FIXES`.
6. **Filler removal**, delegated, with its destruction ceiling intact.
7. **Punctuation repair**, delegated, and idempotent.

Fail-open everywhere: any rule that raises returns the text it was given. A
cleanup that eats an utterance is worse than an utterance that reads slightly
rough.
"""
from __future__ import annotations

import logging
import re
import unicodedata

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Character normalisation
# ---------------------------------------------------------------------------

#: Zero-width and byte-order marks. They survive a ``strip()``, break word
#: boundaries in every rule below, and are invisible in a bug report — which is
#: why they are spelled as escapes here and never as the characters themselves.
_ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u2060\ufeff]")
#: Non-breaking / narrow / figure spaces — a space everywhere except to a regex.
_NBSP_RE = re.compile("[\u00a0\u2007\u202f]")
#: C0/C1 controls except the newline and tab we handle explicitly.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
#: Horizontal whitespace runs (never touches a newline — a provider that
#: returns paragraphs keeps them).
_HORIZONTAL_WS_RE = re.compile(r"[^\S\n]+")

#: Quote characters a generative transcription model wraps its answer in.
#: Only a MATCHED outer pair is removed, so a quotation the speaker actually
#: dictated survives.
_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),  # “ ”
    ("„", "“"),  # „ “  (German book quotes)
    ("«", "»"),  # « »
    ("‘", "’"),  # ‘ ’
)

# ---------------------------------------------------------------------------
# Repetition loops and stutters
# ---------------------------------------------------------------------------

#: One spoken word: a letter-initial token that may carry an apostrophe or an
#: internal hyphen ("don't", "e-mail"). Digits are excluded on purpose — a
#: repeated number ("zwei zwei zwei") is usually dictated data, not a loop.
#:
#: The trailing group admits an INTERNAL dot that a letter immediately follows
#: ("Amara.org", "z.B."). It is load-bearing rather than cosmetic: the single
#: most common decoder loop in this repo's history is a subtitle credit ending
#: in a domain name, and without this the phrase never matched itself, so the
#: rule quietly did nothing on the exact case it exists for. A dot that
#: whitespace follows is still a sentence end and stays outside the token.
_WORD_PATTERN = r"[^\W\d_][\w'’\-]*(?:\.[^\W\d_][\w'’\-]*)*"
#: What separates two repetitions: whitespace, optionally with the punctuation
#: the model emitted between them ("das, das, das", "Danke. Danke. Danke.").
_REPEAT_SEP = r"[ \t]*[,;:.!?]{0,2}[ \t]+"
#: Word boundaries spelled out — ``\b`` misbehaves next to non-ASCII letters.
_NOT_WORD_BEFORE = r"(?<![^\W\d_])"
_NOT_WORD_AFTER = r"(?![^\W\d_])"

#: Inside a MULTI-word phrase a pure number counts as one of the words. On its
#: own it does not (see :data:`_WORD_LOOP_RE`): a repeated bare number is often
#: dictated data — a phone number, a house number said twice for clarity —
#: while a whole phrase that happens to contain a year is never anything but a
#: loop. The one this repo has actually seen, verbatim:
#:   "Untertitelung des ZDF für funk, 2017"  # i18n-allow: STT boilerplate
_PHRASE_PATTERN = rf"(?:{_WORD_PATTERN}|\d+(?:[.,:]\d+)*)"

#: A phrase of 2–8 words repeated at least three times in a row. Three, not
#: two: a doubled word is ordinary speech in every language this ships in
#: (English "had had"; German "dass das das")  # i18n-allow: quoted input
#: while the same span three times over is always a decoder loop.
#:
#: The upper span is set by the artifact, not by taste: the subtitle credits
#: Whisper falls into run six to eight words, so a shorter window would leave
#: the most common loop in the wild unmatched. Cost measured at ~2 ms on 4 KB
#: of text that contains no repetition at all, which is noise next to the
#: network call that produced the transcript.
_PHRASE_LOOP_RE = re.compile(
    _NOT_WORD_BEFORE
    + rf"((?:{_PHRASE_PATTERN}{_REPEAT_SEP}){{1,7}}{_PHRASE_PATTERN})"
    + rf"(?:{_REPEAT_SEP}\1){{2,}}"
    + _NOT_WORD_AFTER,
    re.IGNORECASE | re.UNICODE,
)
#: The same rule for a single word ("ja ja ja ja"), letters only.
_WORD_LOOP_RE = re.compile(
    _NOT_WORD_BEFORE + rf"({_WORD_PATTERN})(?:{_REPEAT_SEP}\1){{2,}}" + _NOT_WORD_AFTER,
    re.IGNORECASE | re.UNICODE,
)

#: An aborted syllable in front of the word it was aborted for: "w- was",
#: "ge- gehen". The fragment is capped at four characters, which is what keeps
#: the rule off a German suspended hyphen ("Vor- und Nachteile" is safe because
#: "und" does not start with "Vor"; "Software- und ..." is safe because the
#: fragment is too long to qualify at all).  # i18n-allow: quoted German example
_HYPHEN_STUTTER_RE = re.compile(
    _NOT_WORD_BEFORE + r"([^\W\d_]{1,4})[-–][ \t]+(?=\1)",
    re.IGNORECASE | re.UNICODE,
)

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# ---------------------------------------------------------------------------
# Phonetic fixes
# ---------------------------------------------------------------------------

# i18n-allow: the table below is speech-recognition vocabulary — the literal
# tokens a matcher must contain to recognise a mis-transcription. Matching
# data, not prose (CLAUDE.md §1, category 3).

#: Known mis-transcriptions, per language. Replacement is whole-word and
#: case-insensitive; the replacement inherits nothing from the source casing.
#:
#: ADMISSION RULE, and it is strict: an entry qualifies ONLY if the source is
#: **not a word in any language this project serves**. The language tag comes
#: from the provider and is regularly wrong — a cloud Whisper endpoint answering
#: "English" for German speech is a documented defect in this repo — so a source
#: token that means something somewhere will eventually be replaced inside a
#: sentence that was perfectly correct, and nothing will report it. That rules
#: out the obvious candidates: "auffliegen" is real German (to be found out),
#: "Gitter" is real German (a grid), so neither belongs here even though both
#: are documented mis-hearings of something else. "aufflegen" (doubled "f")
#: is a word in no language and is only ever the command "auflegen"
#: mis-transcribed on a low-confidence utterance (live 2026-06-09, see
#: ``jarvis/speech/hangup.py``).
#:
#: This table stays SHORT by design. The scalable path for "the recognizer keeps
#: getting MY word wrong" is the user's own STT dictionary
#: (``jarvis.speech.stt_dictionary``), which is editable in the app, applies to
#: every provider, and cannot poison anyone else's transcripts.
PHONETIC_FIXES: dict[str, tuple[tuple[str, str], ...]] = {
    "de": (
        ("aufflegen", "auflegen"),
        ("auffleg", "aufleg"),
    ),
    "en": (),
    "es": (),
}


def _compile_phonetic(language: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Whole-word patterns for one language, longest source first."""
    pairs = sorted(PHONETIC_FIXES[language], key=lambda p: len(p[0]), reverse=True)
    return tuple(
        (
            re.compile(
                _NOT_WORD_BEFORE + re.escape(source) + _NOT_WORD_AFTER,
                re.IGNORECASE | re.UNICODE,
            ),
            target,
        )
        for source, target in pairs
    )


_PHONETIC_CACHE: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    lang: _compile_phonetic(lang) for lang in PHONETIC_FIXES
}


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def normalize_characters(text: str) -> str:
    """NFC, no invisibles, plain spaces. The precondition for every other rule.

    Composed form matters more than it looks: a provider answering in NFD
    spells an umlaut as a plain letter plus a combining mark, so a German
    filler rule matching the composed spelling finds nothing and reports a
    clean no-op.
    """
    out = unicodedata.normalize("NFC", text)
    out = _ZERO_WIDTH_RE.sub("", out)
    out = _NBSP_RE.sub(" ", out)
    out = _CONTROL_RE.sub("", out)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = _HORIZONTAL_WS_RE.sub(" ", out)
    return out.strip()


def strip_wrapping_quotes(text: str) -> str:
    """Remove ONE matched pair of quotes the model wrapped the answer in.

    Word-agnostic (AP-27): it looks at the first and last character and never
    at what is between them. Only one pair, so a transcript that legitimately
    begins and ends on a quoted phrase keeps its inner quotes.
    """
    out = text.strip()
    if len(out) < 2:
        return out
    for opening, closing in _QUOTE_PAIRS:
        if out[0] == opening and out[-1] == closing:
            return out[1:-1].strip()
    return out


def collapse_repetitions(text: str) -> str:
    """Collapse a decoder loop: 3+ identical repetitions in a row become one.

    Phrases first, then single words, so a looping phrase is not shredded into
    per-word decisions.

    No destruction ceiling here, and that is deliberate: a real loop IS the
    majority of the string it appears in ("Untertitel von Amara.org" forty
    times over), so a ceiling would refuse exactly the case the rule exists
    for. The safety comes from the threshold instead — three identical
    repetitions with nothing between them do not occur in dictated speech.
    """
    out = _PHRASE_LOOP_RE.sub(r"\1", text)
    return _WORD_LOOP_RE.sub(r"\1", out)


def repair_stutters(text: str) -> str:
    """Drop an aborted syllable that the completed word follows ("w- was")."""
    return _HYPHEN_STUTTER_RE.sub("", text)


def apply_phonetic_fixes(text: str, language: str | None) -> str:
    """Apply the curated mis-transcription table for ``language``.

    An unresolved or unlisted language is a no-op — the same discipline the
    filler tables use, and for the same reason: applying one language's rules
    to another's speech is how a correct sentence gets damaged.
    """
    if not language:
        return text
    rules = _PHONETIC_CACHE.get(language)
    if not rules:
        return text
    out = text
    for pattern, target in rules:
        out = pattern.sub(target, out)
    return out


def resolve_language(text: str, language: str | None) -> str | None:
    """Which language's word lists may run on ``text``. ``None`` = none of them.

    The provider's word is a HINT here, never the verdict. Cloud Whisper
    endpoints answer with a language on every request and answer confidently
    when they are wrong, and a ``[stt].language`` pin makes them echo the pin
    back for speech in any language at all. Every rule that keys off this
    string DELETES tokens, so a wrong tag is not a cosmetic mislabel — it runs
    one language's filler list over another language's sentence and removes
    words that carry meaning there (the German preposition "um" is the English
    hesitation sound; see ``FILLER_WORDS`` in :mod:`jarvis.dictation.cleanup`).

    So the decision goes through the canonical resolver
    (:func:`jarvis.core.turn_language.resolve_transcript_language`) — the same
    one the dictation lane uses — rather than being re-derived here (CLAUDE.md
    §1: no layer re-derives the language). Fails soft: if that module cannot be
    imported, the provider's tag is normalised on its own, which is the
    behaviour this function replaced.
    """
    try:
        from jarvis.core.turn_language import resolve_transcript_language

        code = resolve_transcript_language(language, text)
        return None if code == "unknown" else code
    except Exception as exc:  # noqa: BLE001 — degrade to the tag alone
        log.debug("Transcript language resolution unavailable (%s).", exc)
    try:
        from jarvis.dictation.cleanup import normalize_language

        return normalize_language(language)
    except Exception as exc:  # noqa: BLE001 — degrade to the neutral rules
        log.debug("Language normalisation unavailable (%s).", exc)
        return None


def _delegated_cleanup(
    text: str, *, language: str | None, remove_fillers: bool
) -> tuple[str, str]:
    """Filler removal + punctuation repair from :mod:`jarvis.dictation.cleanup`.

    Returns ``(text, reason)`` where ``reason`` is the cleanup module's own
    verdict (``""`` when it applied, ``no_rules`` / ``ceiling`` / ``disabled``
    otherwise), so a caller can log WHY nothing was removed.

    The import is local, not module-level: this file is reached from an
    entry-point plugin, which must stay ``jarvis.*``-free at import time, and
    nothing here may land on the boot critical path (AP-26). ``cleanup`` itself
    imports only ``re`` and ``dataclasses``, so the call is cheap.
    """
    try:
        from jarvis.dictation.cleanup import clean_transcript, tidy_transcript
    except Exception as exc:  # noqa: BLE001 — a missing table is not a lost turn
        log.debug("Transcript cleanup tables unavailable (%s); skipping.", exc)
        return text, "unavailable"

    outcome = clean_transcript(
        text, language=language, remove_fillers=remove_fillers
    )
    return tidy_transcript(outcome.text), outcome.reason


def clean_stt_text(
    text: str,
    *,
    language: str | None = None,
    remove_fillers: bool = True,
) -> str:
    """Turn a raw STT payload string into the sentence that was spoken.

    ``language`` is whatever the provider reported; both an ISO code and the
    English language name are understood, and an unknown one simply narrows the
    work to the language-independent repairs. Never raises: on any failure the
    input is returned unchanged.
    """
    raw = text or ""
    if not raw.strip():
        return raw

    try:
        out = normalize_characters(raw)
        out = strip_wrapping_quotes(out)
        out = collapse_repetitions(out)
        out = repair_stutters(out)

        # One resolved language for the two rule sets that need one. Resolved
        # from the tag AND the text (see :func:`resolve_language`), so a
        # confidently wrong provider tag can no longer point one language's
        # word list at another language's sentence. The repaired text is what
        # gets read, not the raw payload: the character normalisation above is
        # what makes a German umlaut spell as one codepoint, and the detector
        # scores umlauts.
        lang = resolve_language(out, language)

        out = apply_phonetic_fixes(out, lang)
        out, reason = _delegated_cleanup(
            out, language=lang, remove_fillers=remove_fillers
        )

        # A cleanup that leaves no words behind is a bug in the rules, never a
        # valid result — hand back what the provider said.
        if not _WORD_RE.search(out) and _WORD_RE.search(raw):
            log.warning(
                "Transcript cleanup emptied a non-empty transcript; using the raw text."
            )
            return raw.strip()
        if reason not in ("", "unavailable"):
            log.debug("Filler removal skipped (%s).", reason)
        return out
    except Exception:  # noqa: BLE001 — never lose an utterance to a cleanup bug
        log.warning("Transcript cleanup failed; using the raw text.", exc_info=True)
        return raw.strip()


__all__ = [
    "PHONETIC_FIXES",
    "apply_phonetic_fixes",
    "clean_stt_text",
    "collapse_repetitions",
    "normalize_characters",
    "repair_stutters",
    "resolve_language",
    "strip_wrapping_quotes",
]
