"""Shared barge-in intent detection — "stop what you are doing", not "hang up".

Sibling of :mod:`jarvis.speech.hangup`, and deliberately its opposite number:
``HANGUP_RE`` ends the whole call, this module ends the *current action* while
the call stays open. Both surfaces that run a live conversation need it — the
realtime orchestrator (``jarvis/realtime/session.py``) and, in future, the
classic pipeline — so it lives here rather than inside either one.

Why it exists
-------------
A realtime turn that routes to the tool model is silent for as long as the
action runs. Speaking into that silence used to be absorbed: the orchestrator
classified a short fragment as a continuation of the order already executing
and answered with a canned progress line instead of taking the floor (see
``_continues_executing_order``). That is correct for a sentence split by a
thinking pause ("…the skill system doesn't work" / "you know, recognize the
skills") and wrong for a user saying "stop". Only the words can tell the two
apart, so the words get their own deterministic probe.

Design rules
------------
* **Regex only.** This sits on the live-turn hot path, next to the VAD edge; a
  model call here would add exactly the latency barge-in exists to remove
  (AP-9/AP-11), and prompt compliance is not a correctness boundary
  (BUG-047 class rule).
* **Every locale equal** (CLAUDE.md §1). German, English and Spanish carry the
  same vocabulary depth; adding a locale means adding its tokens, never
  special-casing a "default" language.
* **Whole-utterance anchored.** A stop token only counts at the START of the
  utterance and only when everything before it is a filler. "Don't stop the
  music" must never cancel an action.
* **Two strengths.** ``_HARD_STOP`` tokens are unambiguous enough to also
  cancel when the user keeps talking ("wait, I meant Rome"). ``_SOFT_STOP``
  tokens (a bare "no") only count when they are the ENTIRE utterance —
  otherwise "no problem" would abort a running action.
* **Hang-up wins.** "stopp jarvis" is already a closing command in
  ``HANGUP_RE``; anything that phrase matches is refused here so the two
  intents can never race.

Standard library only (``re``), like ``hangup.py``: ``jarvis/speech/__init__.py``
is intentionally empty, so importing this module pulls in nothing else and any
surface can use it without dragging in audio dependencies.
"""
from __future__ import annotations

import re
from typing import Final

from jarvis.speech.hangup import HANGUP_RE

#: Nothing in the utterance asks to stop.
INTERRUPT_NONE: Final[str] = ""
#: The utterance is nothing BUT a stop request ("stop", "warte mal", "no").
#: The running action is obsolete and no new order replaces it.
INTERRUPT_STOP: Final[str] = "stop"
#: A stop request that carries a replacement ("wait, I meant Rome"). The
#: running action is obsolete AND the remainder is a new order of its own.
INTERRUPT_REDIRECT: Final[str] = "redirect"

# Discourse particles that may precede the stop token without changing it.
# Kept generous: a hesitation sound ("ähm") reaches us  # i18n-allow: STT output
# spelled a dozen different ways, and a filler is never evidence either way.
_FILLER: Final[str] = (
    r"(?:okay|okey|ok|oke|hey|hallo|jarvis|bitte|please|por\s+favor|vale|oye|"
    r"äh+m?|ah+|eh+|uh+|um+|hm+|mhm|also|na|pues|bueno|so)"  # i18n-allow: STT fillers
)

# Unambiguous "abandon what you are doing" tokens. Strong enough that the user
# may keep talking after them — the remainder is then a replacement order.
_HARD_STOP_PATTERNS: Final[tuple[str, ...]] = (
    # --- German ---------------------------------------------------------- #
    # One family for both locales: German "stopp" and English "stop" are the
    # same spoken token, and two separate alternatives would let the shorter
    # one shadow the longer ("Stop it" matching only "Stop").
    r"stopp?(?:\s+(?:it|that|mal))?",  # i18n-allow: spoken command
    r"halt",  # i18n-allow
    r"warte(?:t|n)?(?:\s+(?:mal|kurz|bitte))*",  # i18n-allow
    r"wart",  # i18n-allow
    r"moment(?:\s+mal)?",  # i18n-allow
    r"(?:einen|ein)\s+moment",  # i18n-allow
    r"abbrechen",  # i18n-allow
    r"abbruch",  # i18n-allow
    r"brich(?:\s+(?:das|es))?\s+ab",  # i18n-allow
    r"brech(?:\s+(?:das|es))?\s+ab",  # i18n-allow
    r"h(?:ö|oe)r(?:\s+mal)?\s+auf",  # i18n-allow
    r"lass(?:\s+(?:es|das|mal|stecken|gut\s+sein))+",  # i18n-allow
    r"vergiss(?:\s+(?:es|das))+",  # i18n-allow
    r"(?:doch|lieber)\s+nicht",  # i18n-allow
    r"nicht\s+mehr",  # i18n-allow
    r"egal",  # i18n-allow
    r"quatsch",  # i18n-allow
    # --- English --------------------------------------------------------- #
    r"wait(?:\s+(?:a\s+(?:second|sec|moment|minute)|up))?",
    r"hold\s+(?:on|up)",
    r"hang\s+on",
    r"never\s*mind",
    r"forget\s+(?:it|that)",
    r"cancel(?:\s+(?:it|that))?",
    r"abort",
    r"scratch\s+that",
    r"drop\s+it",
    r"my\s+bad",
    r"one\s+(?:second|sec|moment)",
    # --- Spanish --------------------------------------------------------- #
    r"p(?:a|á)ra(?:te)?",  # i18n-allow
    r"alto",  # i18n-allow
    r"espera(?:te)?",  # i18n-allow
    r"esp(?:é|e)rate",  # i18n-allow
    r"(?:un\s+)?momento",  # i18n-allow
    r"cancela(?:lo)?",  # i18n-allow
    r"canc(?:é|e)lalo",  # i18n-allow
    r"anula(?:lo)?",  # i18n-allow
    r"olv(?:í|i)dalo",  # i18n-allow
    r"olvida\s+(?:eso|lo)",  # i18n-allow
    r"d(?:é|e)jalo",  # i18n-allow
    r"deja\s+eso",  # i18n-allow
    r"no\s+importa",  # i18n-allow
    r"mejor\s+no",  # i18n-allow
)

# Bare negations. A genuine stop when they are the WHOLE utterance, ordinary
# speech the moment anything follows them ("no problem", "nein danke").
_SOFT_STOP_PATTERNS: Final[tuple[str, ...]] = (
    r"nein",  # i18n-allow
    r"nee",  # i18n-allow
    r"n(?:ö|oe)",  # i18n-allow
    r"no",
    r"nope",
    r"nah",
)

_HARD_STOP: Final[str] = "|".join(_HARD_STOP_PATTERNS)
_ANY_STOP: Final[str] = "|".join((*_HARD_STOP_PATTERNS, *_SOFT_STOP_PATTERNS))

# A run of stop tokens (optionally repeated: "no no", "stop stop") preceded by
# any number of fillers. ``rest`` is whatever the user kept saying.
#
# The trailing ``\b`` after every token group is load-bearing: Python's
# alternation is leftmost-first, so without it the shorter alternative wins and
# swallows the longer one ("no" matching the head of "nope", "moment" matching
# the head of "momento") — the remainder then looks like ordinary speech and
# the interrupt is lost. The boundary forces the backtrack to the full token.
_HARD_LEAD_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\s*(?:{_FILLER}\b[\s,.!?]+)*(?:{_HARD_STOP})\b"
    rf"(?:[\s,]+(?:{_ANY_STOP})\b)*"
    rf"(?P<rest>[\s,.!?…]*.*)$",
    re.IGNORECASE | re.DOTALL,
)
_ANY_LEAD_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\s*(?:{_FILLER}\b[\s,.!?]+)*(?:{_ANY_STOP})\b"
    rf"(?:[\s,]+(?:{_ANY_STOP})\b)*"
    rf"(?P<rest>[\s,.!?…]*.*)$",
    re.IGNORECASE | re.DOTALL,
)
# Trailing politeness that must not turn a pure stop into a redirect.
_TAIL_RE: Final[re.Pattern[str]] = re.compile(
    rf"^(?:[\s,.!?…]|{_FILLER}|danke|thanks|thank\s+you|gracias)*$",  # i18n-allow
    re.IGNORECASE,
)

# A bare negation is too weak to cancel an action on its own once the user
# keeps talking — "no problem" and "nein danke" are ordinary speech. It becomes
# a redirect only in the one shape that is unmistakably a correction, which is
# also the shape the classic pipeline already knows as the "nein, ich meinte X"
# command (``jarvis/speech/turn_buffer.py``).
_CORRECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:"
    r"ich\s+(?:meinte|meine|wollte|sagte)|"  # i18n-allow
    r"eigentlich|sondern|stattdessen|lieber|besser|"  # i18n-allow
    r"mach(?:\s+(?:lieber|stattdessen|es))?|nimm|"  # i18n-allow
    r"i\s+(?:meant|mean|said|wanted)|"
    r"actually|instead|rather|make\s+it|"
    r"quer(?:í|i)a\s+decir|quiero\s+decir|"  # i18n-allow
    r"en\s+realidad|m(?:á|a)s\s+bien|mejor"  # i18n-allow
    r")\b",
    re.IGNORECASE,
)

#: A replacement order longer than this is treated as ordinary speech that
#: merely happens to open with a stop word. Keeps a monologue beginning with
#: "wait" from cancelling an action the user never mentioned again.
_REDIRECT_MAX_TOKENS: Final[int] = 24


def classify_interrupt(text: str | None) -> str:
    """Classify ``text`` as a stop request, a redirect, or neither.

    Returns :data:`INTERRUPT_STOP` when the utterance is nothing but a request
    to abandon the current action, :data:`INTERRUPT_REDIRECT` when it opens
    with an unambiguous stop token and carries a replacement order, and
    :data:`INTERRUPT_NONE` otherwise.

    Never raises: a malformed transcript answers "no interrupt", because the
    fail-safe direction here is letting the action finish.
    """
    utterance = " ".join(str(text or "").split())
    if not utterance:
        return INTERRUPT_NONE
    # Hang-up owns its own phrases; a closing command must end the CALL, not
    # merely cancel an action and leave the session listening.
    if HANGUP_RE.search(utterance):
        return INTERRUPT_NONE

    match = _ANY_LEAD_RE.match(utterance)
    if match is not None and _TAIL_RE.match(match.group("rest") or ""):
        # Everything after the stop run is punctuation or politeness.
        return INTERRUPT_STOP

    hard = _HARD_LEAD_RE.match(utterance)
    if hard is not None:
        rest = str(hard.group("rest") or "").strip(" ,.!?…")
        if not rest:
            return INTERRUPT_STOP
        if len(rest.split()) > _REDIRECT_MAX_TOKENS:
            return INTERRUPT_NONE
        return INTERRUPT_REDIRECT

    # Soft opener with a remainder: only the explicit correction shape counts.
    if match is None:
        return INTERRUPT_NONE
    rest = str(match.group("rest") or "").strip(" ,.!?…")
    if (
        rest
        and len(rest.split()) <= _REDIRECT_MAX_TOKENS
        and _CORRECTION_RE.match(rest)
    ):
        return INTERRUPT_REDIRECT
    return INTERRUPT_NONE


def is_interrupt_intent(text: str | None) -> bool:
    """True when ``text`` asks to abandon the running action in any form."""
    return classify_interrupt(text) != INTERRUPT_NONE


__all__ = [
    "INTERRUPT_NONE",
    "INTERRUPT_REDIRECT",
    "INTERRUPT_STOP",
    "classify_interrupt",
    "is_interrupt_intent",
]
