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
#:
#: v4: the paragraph and enumeration clauses grew a concrete target format
#: (blank line between paragraphs; one list item per line, "1." when the
#: speaker counted, "-" when they did not). The v3 wording licensed both
#: transformations without describing them, and the model answered with prose.
#:
#: v5: "the number replaces the spoken ordinal" became an explicit ALWAYS,
#: with restructuring of the remaining item licensed by example. Live v4 runs
#: split the lines but kept "Erstens ..." written out: replacing a German
#: enumeration adverb changes the word order of what follows, and a model not
#: expressly allowed to repair that keeps the adverb rather than break the
#: sentence.
#:
#: v6: bullet lists got their own recognition contract. An explicit spoken
#: list-marker command ("bullet point" and its input-language equivalents)
#: always opens a "- " line; uncommanded bullets need an announced or clearly
#: parallel list; and "turning speech into a list never deletes content" is a
#: stated rule. Matched by the guards' ``_FORMAT_COMMAND_WORDS`` table, which
#: licenses the command words to vanish into the marks that replace them.
#:
#: v7: the filler and false-start clauses grew an explicit floor — a finite
#: verb, an auxiliary, a modal and a negation are never what gets removed. v6
#: licensed removing "repeated words that carry no meaning" and left the model
#: to decide which those are, and on live transcripts it decided a copula was
#: one: "what I've meant IS this expanded term" came back as "I meant this
#: expanded term". A deleted verb is the one loss the speaker cannot see, so
#: the rule is stated where the licence to remove is granted rather than as a
#: general plea for restraint. Backed deterministically by the ``lost_verb``
#: drift guard, which rejects the answer when the model does it anyway.
POLISH_PROMPT_VERSION: Final[int] = 7

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
- NEVER remove a verb, an auxiliary, a modal or a negation. A filler is a
  SOUND; a word that carries tense, mood or truth is not filler, however small
  and however often it occurs. "Tomorrow is Sunday" never becomes "Tomorrow
  Sunday"; "I can see it" never becomes "I see it"; "not ready" never becomes
  "ready". Where such a word is wrong, CORRECT it in place and leave one
  standing ("which actions is called" -> "which actions are called") — the
  sentence must never come back missing the word that made it a sentence.
- Apply sentence-final punctuation, commas and capitalization. Repair
  capitalization broken by transcription segment boundaries.
- Break the text into paragraphs where the speaker clearly moved to a new
  thought or topic, and wherever they dictated a break ("new paragraph" and
  its equivalents). Separate paragraphs with ONE blank line.
- Turn spoken punctuation commands into the mark ("period", "comma",
  "question mark", "new line", and their equivalents in the input language).
- Turn a spoken enumeration into a list when the speaker clearly enumerated:
  one item per line. When the speaker counted ("first", "second", "third" and
  their equivalents in the input language), ALWAYS number the lines "1.",
  "2.", "3." — the digit REPLACES the spoken ordinal, so an ordinal word
  never remains at the start of a numbered line. Where dropping the ordinal
  breaks the word order of the rest of the item, restructure the item so it
  reads naturally ("first we prepare the offer" -> "1. We prepare the
  offer").
- A spoken list-marker command ALWAYS starts a new "- " line: "bullet
  point", "next point", and their equivalents in the input language. The
  command words become the marker itself and never appear in the text.
- Without a command, use "- " bullets only when the speaker plainly
  delivered an uncounted list: they announced one ("we need the following")
  or spoke three or more short items of the same shape in a row. Turning
  speech into a list never deletes content — every word that is not the
  count, the command or the enumeration word itself stays. When you cannot
  tell a list from a flowing sentence, keep the sentence.
- Normalize spoken numbers to numerals where a writer would ("seven" -> "7"),
  but never invent a number that was not spoken.
- Remove stray ellipses left by the recognizer at a pause.

WHEN IN DOUBT, CHANGE NOTHING. Returning the input unchanged is always a
correct answer. Rewriting is the exception, not the default."""

# ---------------------------------------------------------------------------
# Precision mode — the OPTIONAL word-choice pass on top of the formatter
# ---------------------------------------------------------------------------
# The formatter above repairs how a sentence is WRITTEN and is forbidden from
# touching which words it is made of. This block licenses exactly one further
# step: replacing a word with a more precise one that means the same thing.
#
# It is appended, never merged, and it ships OFF. Two reasons, and both are the
# whole design:
#
# 1. It weakens a guard. The ``lost_term`` rare-token check throws away any
#    answer in which an uncommon word from the transcript vanished — which is
#    precisely what a word replacement looks like from the outside. Precision
#    mode therefore runs against ``precision_drift_reason`` instead, which drops
#    that one check and keeps every other. A user who has not asked for this
#    must not silently lose the stronger guard.
# 2. The failure direction is known and it is not subtle. Every model asked to
#    "improve wording" reaches for the ornate register — utilize, facilitate,
#    leverage, commence — because that is what its training data marks as
#    "better writing". That is the OPPOSITE of the goal here. So the block
#    spends more words forbidding ornamentation than requesting precision, and
#    the forbidden swaps are named literally rather than described: a model
#    given the rule "prefer plain words" still writes "utilize", and a model
#    given "'use' does not become 'utilize'" does not.
#
# The examples are English because the prompt is; the RULES are language-neutral
# and the model applies them in whatever language it was handed (hard rule 2
# above still governs, and precision mode never licenses a language change).
_PRECISION_BLOCK: Final[str] = """\
PRECISION MODE IS ON. On top of the rules above you may now also sharpen the
WORD CHOICE:
- Replace a vague placeholder with the specific word the speaker plainly meant
  ("the thing that holds the pipe" -> "the bracket").
- Replace a description of a thing that has an established name with that name
  ("the list of stuff it needs to run" -> "its dependencies").
- Cut hedges and empty intensifiers that carry no information ("kind of",
  "sort of", "basically", "really", "very", "I mean", "you know").
- Collapse padding into the plain verb ("make a decision" -> "decide",
  "give an explanation" -> "explain", "is able to" -> "can"). The collapse
  always leaves a verb behind — it swaps one for another, it never deletes.

PLAIN, NOT ORNATE — this is the half that goes wrong, so it is a rule and not a
preference:
- NEVER swap a common word for a rarer one that means the same thing. "use"
  does not become "utilize". "help" does not become "facilitate". "start" does
  not become "commence". "about" does not become "regarding". "so" does not
  become "consequently". A longer synonym is not a more precise word.
- NEVER add adjectives, adverbs, metaphors, imagery or flourish. No sentence
  becomes longer because the result sounds more impressive.
- Keep the speaker's voice and register. Casual stays casual. Short sentences
  stay short. You are making them CLEAR, not making them sound clever.

MEANING STILL NEVER CHANGES, and that outranks every line above. A word is only
more precise if it means the same thing. Where you are not certain what the
speaker meant, keep their word exactly as they said it — a plain word the
reader understands beats a sharp one that is wrong."""


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


def precision_block() -> str:
    """The precision-mode clause, ready to append to a system prompt.

    Public and shared with the translate prompt for the same reason
    :func:`build_protected_block` and :func:`style_line` are: what "sharpen the
    wording" means to a model is ONE decision. A second copy is how the setting
    ends up meaning something subtly different depending on whether a
    translation was involved — and the user would have no way to tell, because
    both paths report the same status on the same history row.
    """
    return _PRECISION_BLOCK


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
    precision: bool = False,
) -> str:
    """Assemble the system prompt for one polish call.

    ``language`` is the already-resolved transcript language; pass ``""`` or
    ``"auto"`` when it could not be resolved and the hint line is omitted
    entirely — an absent hint is safer than a wrong one.

    ``protected_terms`` are spellings the model must not "correct": the user's
    STT dictionary, the wake word, their own name. They are listed, never
    described, so the model has nothing to interpret.

    ``precision`` appends the word-choice clause. It defaults to ``False`` so
    that every existing caller keeps the strict formatter contract it was
    written against — and so that a caller who forgets the argument gets the
    SAFER prompt rather than the looser one. The caller that passes ``True``
    must also switch to
    :func:`jarvis.dictation.polish_guards.precision_drift_reason`; the two are
    one decision, and the ordinary guard rejects most precision answers by
    construction.
    """
    parts: list[str] = [_SYSTEM_PROMPT]

    if precision:
        parts.append(_PRECISION_BLOCK)

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
    "precision_block",
    "style_line",
]
