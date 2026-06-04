"""Deictic pointing-intent gate (regex only — no LLM, hot-path-edge safe).

``is_pointing_intent(text)`` returns ``True`` only when the utterance refers
deictically to the on-screen element under the cursor — and ``False`` for a
general "what is on my screen" question, the weather veto, or a verb completed
by a concrete noun. This is the "no context-less garbage" contract.

Design (adversarially verified, 2026-06-02 — see docs/plans/ai-pointer/DESIGN.md
and the ai-pointer-deep-dive-fix workflow):

* **Locative-anchored strong phrases** ("das da", "worauf ich zeige", "wort da")
  — matched with word boundaries so "siehst du da" does NOT match inside
  "siehst du DAS problem" (the substring trap).
* **Vision/read verb + LOCATIVE anchor** ("was siehst du HIER", "was steht DA",
  "kannst du sehen was DA steht") — the locative (hier/da/dort/here/there) is the
  signal. A bare demonstrative (das/this/that) is deliberately NOT an anchor, so
  "zeig mir das wetter" / "show me that report" do NOT fire.
* **Bare deictic command** ("lies das", "show me that") — only when no concrete
  noun completes the demonstrative (so "zeig mir das menü" does not fire).
* **Bare demonstrative question** ("was ist das?") — only when not completed by
  a noun ("was ist das fuer ein Wetter" is vetoed).

The ``[pointer]`` config may extend ``strong_phrases`` at runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WS_RE = re.compile(r"\s+")

# Locative / word-deictic strong phrases (NOT bare verb+demonstrative commands —
# those go through the noun-vetoed bare-deictic tier below).
DEFAULT_STRONG_PHRASES: tuple[str, ...] = (
    # German — locative-anchored demonstratives
    "das da", "das hier", "das dort", "das da drueben", "das da drüben",
    "da drueben", "da drüben", "dies hier", "dieses hier", "dieses ding",
    "dieses teil", "das ding hier", "das teil hier", "das da unten",
    "das da oben",
    # German — cursor / pointing references
    "worauf ich zeige", "worauf zeige ich", "wo ich hinzeige",
    "wo ich hin zeige", "wo ich draufzeige", "wo ich drauf zeige",
    "wo zeige ich hin", "unter meinem cursor", "unter dem cursor",
    "unter meinem mauszeiger", "unter dem mauszeiger", "wo mein mauszeiger",
    "worauf mein mauszeiger", "was ist das da", "was ist das hier",
    # German — word/character deixis ("which word is this")
    "welches wort", "dieses wort", "das wort hier", "das wort da",
    "wort da", "wort hier", "wort dort", "was ist das wort",
    "was ist das zeichen",
    # English
    "this here", "this thing", "that thing", "right here", "over there",
    "what is this", "what's this", "whats this", "what is that",
    "what's that", "whats that", "what am i pointing", "pointing at",
    "where i'm pointing", "where im pointing", "under my cursor",
    "under the cursor", "this one here", "which word", "this word",
    "that word", "what is the word", "what word is",
)

# Vision/read verbs + a LOCATIVE anchor (hier/da/dort/here/there). The
# demonstratives das/this/that are intentionally NOT anchors — a verb + bare
# demonstrative ("zeig mir das wetter") is a normal object, not deictic.
_VISION_VERB_RE = re.compile(
    r"\b(?:siehst|sehe|seh|sehen|siehe|liest|lies|lese|lesen|vorlesen|vorliest|"
    r"steht|stehen|stehst|zeigst|zeig|zeige|"
    r"read|reads|reading|see|sees|write|writes|written|wrote)\b"
    r".*?\b(?:hier|da|dort|drueben|drüben|here|there)\b"
)

# Bare deictic command ("lies das" / "show me that") — fires only when NOT
# completed by a concrete noun (the noun veto below).
_BARE_DEICTIC_RE = re.compile(
    r"\b(?:lies(?:\s+mir)?|zeig(?:e|st)?(?:\s+du)?(?:\s+mir)?|read(?:\s+me)?|"
    r"show(?:\s+me)?)\s+(?:mir\s+|me\s+)?(?:das|dies(?:es)?|this|that|it)\b"
)
# A demonstrative completed by a concrete noun ("das menü", "that report") — but
# NOT when followed by a continuation that keeps it deictic (vor/mir/hier/wort…).
_NOUN_VETO_RE = re.compile(
    r"\b(?:das|dieses|diese|dieser|den|the|that|this)\s+"
    r"(?!vor\b|mir\b|hier\b|da\b|dort\b|drueben\b|drüben\b|here\b|there\b|nicht\b|"
    r"mal\b|bitte\b|wort\b|zeichen\b|word\b|character\b|aloud\b|laut\b|für\b|fuer\b)"
    r"[a-zäöüß]+\b"
)

# "das für ein <noun>" → not deictic ("was ist das fuer ein Wetter?").
_VETO_RE = re.compile(r"\b(?:das|dies\w*)\s+f(?:ue|ü)r\s+(?:ein|eine|einen|nen)\b")

# Bare demonstrative question: deictic only when not completed by a content word.
_BARE_Q_RE = re.compile(
    r"\b(?:was ist das|what is this|what'?s this|what is that|what'?s that)"
    r"(?P<tail>.*)$"
)
_DEICTIC_TAIL_RE = re.compile(r"^(?:da|hier|dort|drueben|drüben|here|there)\b")


@dataclass(frozen=True, slots=True)
class PointingGate:
    """A compiled deictic gate. Use :func:`compile_gate` to build one."""

    _strong_re: re.Pattern[str]

    def matches(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        t = _WS_RE.sub(" ", text.strip().lower())
        # Locative deictic signals win over everything (incl. the für-ein veto).
        if self._strong_re.search(t):
            return True
        if _VISION_VERB_RE.search(t):
            return True
        # "das für ein <noun>" → category question, not deictic.
        if _VETO_RE.search(t):
            return False
        # Bare deictic command, only when no concrete noun completes it.
        if _BARE_DEICTIC_RE.search(t) and not _NOUN_VETO_RE.search(t):
            return True
        # Bare demonstrative question ("was ist das?") not completed by a noun.
        m = _BARE_Q_RE.search(t)
        if m:
            tail = m.group("tail").strip(" ?!.,;:")
            if tail == "" or _DEICTIC_TAIL_RE.match(tail):
                return True
        return False


def compile_gate(strong_phrases: tuple[str, ...] = DEFAULT_STRONG_PHRASES) -> PointingGate:
    """Compile a :class:`PointingGate` from a strong-phrase set.

    Each phrase is anchored with word boundaries so a shorter phrase cannot match
    inside a longer word (e.g. "siehst du da" inside "siehst du das problem").
    """
    phrases = strong_phrases or DEFAULT_STRONG_PHRASES
    strong_re = re.compile(
        "|".join(r"\b" + re.escape(p.lower()) + r"\b" for p in phrases)
    )
    return PointingGate(_strong_re=strong_re)


_DEFAULT_GATE = compile_gate()


def is_pointing_intent(text: str) -> bool:
    """True when ``text`` deictically references the element under the cursor."""
    return _DEFAULT_GATE.matches(text)
