"""The polish pass's prompt contract — versioned, and structurally injection-safe.

Why the contract lives in its own module
----------------------------------------
The system prompt is the ONLY thing standing between "a formatter" and "a
chatbot that answers the transcript". It is therefore a piece of behaviour, not
a string literal: it is versioned (:data:`POLISH_PROMPT_VERSION`), it is pinned
by tests, and it is written once so no caller can quietly ship a softer variant.

Why the raw transcript is NEVER interpolated into the system prompt
-------------------------------------------------------------------
A dictation is untrusted text. People dictate sentences that are shaped exactly
like instructions — "ignore the previous paragraph", "answer the question
below", "translate this into English". Splicing that into the prompt that
defines the model's job is prompt injection with the user as the accidental
attacker: the model can no longer tell the rules from the material.

So the raw text travels as a SEPARATE user message, wrapped in the explicit
delimiters below, and any delimiter that happens to occur inside the transcript
is neutralised before wrapping (:func:`build_polish_user_message`) so the block
cannot be closed early. A model answer that repeats a delimiter is rejected
downstream by the ``meta_output`` guard — that is the tell that the model
treated the material as conversation.

Everything in this module is English source. The OUTPUT language is data: the
model is told to mirror whatever language it was given, never to translate, and
never to answer in the prompt's language.

Pure string building — no I/O, no model call, no heavy import. Safe to import
anywhere (AP-26).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

#: Bumped whenever the wording below changes in a way that could change model
#: behaviour. Stored alongside a polished transcript so a later quality
#: regression can be attributed to a prompt revision rather than a provider.
POLISH_PROMPT_VERSION: Final[int] = 2

#: The delimiters that fence the untrusted transcript inside the user message.
#: Deliberately ugly and unlikely to be dictated by accident; deliberately
#: symmetric so the ``meta_output`` guard can spot either half in an answer.
RAW_OPEN_DELIMITER: Final[str] = "<<<BEGIN TRANSCRIPT>>>"
RAW_CLOSE_DELIMITER: Final[str] = "<<<END TRANSCRIPT>>>"

#: The register hints a caller may ask for. This is the cheap analogue of the
#: reference tool's three-value application-type enum: one extra line in the
#: prompt, no per-application model, no new failure mode.
POLISH_STYLES: Final[tuple[str, ...]] = ("neutral", "messaging", "email")

#: Exactly one appended line per style. ``neutral`` adds nothing on purpose —
#: the default must be the least opinionated prompt we can ship.
_STYLE_LINES: Final[dict[str, str]] = {
    "neutral": "",
    "messaging": "Keep it casual, no formal salutations.",
    "email": "Keep a neutral written-correspondence register.",
}

# The clause order is load-bearing: the four HARD RULES come first because a
# model that reads "what you may do" first starts editing and only then learns
# the limits. "WHEN IN DOUBT, CHANGE NOTHING" is last so it is the freshest
# instruction — the reference tool's own shipped regression ("fewer changes to
# words you said") proves the failure direction is over-rewriting, not
# under-rewriting.
_SYSTEM_PROMPT: Final[str] = """\
You are a transcript formatter. You are given the raw output of a
speech-recognition system. You return the SAME utterance, written the way the
speaker would have written it.

HARD RULES — a violation makes your answer unusable:
1. MEANING NEVER CHANGES. You may restructure; you may not add, remove or
   alter information. You do not answer, summarize, translate, continue,
   explain, or comment. You are not in a conversation.
2. OUTPUT LANGUAGE = INPUT LANGUAGE, always. If the input mixes languages,
   keep the mix exactly as spoken.
3. PRESERVE VERBATIM: proper names, place names, product names, brand names,
   technical terms, code identifiers, file paths, URLs, e-mail addresses,
   acronyms, and every number, quantity, date, time and currency amount.
   An unfamiliar word that IS a word stays exactly as written — you do not
   know every name, and a word you have not met is not a mistake.
4. RETURN ONLY THE FORMATTED TEXT. No preamble, no quotes, no explanation,
   no markdown fences, no "Here is".

WHAT YOU MAY DO:
- Repair a word the recognizer clearly misheard, and ONLY under all three
  conditions: what it wrote is not a word in the input language, the word the
  speaker said is unambiguous from the surrounding sentence, and your repair
  sounds like what was written. This covers a word cut short ("deskto" ->
  "desktop") and two words run together ("haboogle Drive" -> "hab Google
  Drive"). If more than one word would fit, or the result would only be more
  plausible rather than clearly intended, leave it alone — a strange word the
  reader can see beats a confident wrong one they cannot.
- Remove filler sounds and repeated words that carry no meaning.
- Remove false starts and self-corrections: keep the corrected version, drop
  the abandoned one. ("at 2, actually 3" -> "at 3")
- Apply sentence-final punctuation, commas, capitalization and paragraph
  breaks. Repair capitalization broken by transcription segment boundaries.
- Turn spoken punctuation commands into the mark ("period", "comma",
  "question mark", "new line", and their equivalents in the input language).
- Turn a spoken enumeration into a list when the speaker clearly enumerated.
- Normalize spoken numbers to numerals where a writer would ("seven" -> "7"),
  but never invent a number that was not spoken.
- Remove stray ellipses left by the recognizer at a pause.

WHEN IN DOUBT, CHANGE NOTHING. Returning the input unchanged is always a
correct answer. Rewriting is the exception, not the default."""

# The language hint is phrased as a WEAK signal on purpose. The tag comes from
# a recognizer that is documented to echo a configured pin back at us (see
# jarvis/core/turn_language.py), so a hard "the input is German" line would tell
# the model to translate an English dictation that was merely mislabelled — the
# exact defect this whole feature is downstream of.
_LANGUAGE_HINT: Final[str] = (
    'The recognizer reported the input language as "{language}". That label is\n'
    "unreliable: trust the text you are given, and never translate to match it."
)

_PROTECTED_BLOCK: Final[str] = (
    "<protected terms — spell these exactly as listed>\n"
    "{terms}\n"
    "</protected>"
)

#: What the protected block says when the caller has nothing to protect. An
#: empty block reads to a model like an empty instruction; a named "(none)" is
#: unambiguous.
_NO_PROTECTED_TERMS: Final[str] = "(none)"


def normalize_style(style: object) -> str:
    """Collapse *style* to one of :data:`POLISH_STYLES`; unknown -> ``neutral``.

    Tolerant on purpose: the value arrives from ``jarvis.toml`` and from a REST
    payload, and an unrecognised register must degrade to the least opinionated
    prompt rather than raise inside a dictation.
    """
    value = str(style or "").strip().lower()
    return value if value in POLISH_STYLES else "neutral"


def style_line(style: object) -> str:
    """The one appended register line for *style*; ``""`` for ``neutral``.

    Public because the translate prompt (:mod:`jarvis.dictation.translate_prompt`)
    offers the same three registers and must append the same sentence. Two
    copies of a register hint is how the email style ends up meaning something
    subtly different depending on whether a translation was involved.
    """
    return _STYLE_LINES[normalize_style(style)]


def build_protected_block(protected_terms: Sequence[str]) -> str:
    """The ``<protected terms>`` block, ready to append to a system prompt.

    Shared with the translate prompt for the reason above: what "do not touch
    this spelling" looks like to a model is one decision, not one per pass.
    """
    return _PROTECTED_BLOCK.format(terms=_format_protected_terms(protected_terms))


def build_polish_prompt(
    *,
    language: str,
    style: str,
    protected_terms: Sequence[str],
) -> str:
    """Assemble the system prompt for one polish call.

    ``language`` is the already-resolved transcript language; pass ``""`` or
    ``"auto"`` when it could not be resolved and the hint line is omitted
    entirely — an absent hint is safer than a wrong one.

    ``protected_terms`` are spellings the model must not "correct": the user's
    STT dictionary, the wake word, their own name. They are listed, never
    described, so the model has nothing to interpret.
    """
    parts: list[str] = [_SYSTEM_PROMPT]

    hint_language = str(language or "").strip()
    if hint_language and hint_language.lower() not in ("auto", "unknown"):
        parts.append(_LANGUAGE_HINT.format(language=hint_language))

    register = style_line(style)
    if register:
        parts.append(register)

    parts.append(build_protected_block(protected_terms))

    return "\n\n".join(parts)


def build_polish_user_message(raw: str) -> str:
    """Wrap the untrusted transcript in the delimiters, injection-safely.

    Two properties matter here and nothing else does:

    * the transcript is the WHOLE user message, so nothing the model reads as
      an instruction shares a message with the actual instructions;
    * a delimiter occurring inside the transcript is neutralised first, so a
      dictation cannot close the block early and continue as if it were the
      operator. The neutralised form keeps the words the user said (this is a
      dictation feature — losing text is the one unacceptable outcome) and only
      breaks the literal marker.
    """
    body = str(raw or "")
    for delimiter in (RAW_OPEN_DELIMITER, RAW_CLOSE_DELIMITER):
        body = body.replace(delimiter, _defuse(delimiter))
    return f"{RAW_OPEN_DELIMITER}\n{body}\n{RAW_CLOSE_DELIMITER}"


def _defuse(delimiter: str) -> str:
    """Break a literal delimiter without deleting any of its characters."""
    return delimiter.replace("<", "< ").replace(">", " >")


def _format_protected_terms(protected_terms: Sequence[str]) -> str:
    """One term per line, de-duplicated, order preserved, blanks dropped."""
    seen: set[str] = set()
    lines: list[str] = []
    for term in protected_terms or ():
        text = str(term or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(text)
    return "\n".join(lines) if lines else _NO_PROTECTED_TERMS


__all__ = [
    "POLISH_PROMPT_VERSION",
    "POLISH_STYLES",
    "RAW_CLOSE_DELIMITER",
    "RAW_OPEN_DELIMITER",
    "build_polish_prompt",
    "build_polish_user_message",
    "build_protected_block",
    "normalize_style",
    "style_line",
]
