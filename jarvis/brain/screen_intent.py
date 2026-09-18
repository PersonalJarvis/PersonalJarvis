"""Explicit "look at my screen" requests.

A small local model answers "what is on my screen?" from the window titles in
its context instead of calling the screenshot tool (live 2026-09-18: it named
the wrong window twice). When the active brain can see images, such a request
mandates the ``screenshot`` tool for the turn, through the same evidence
machinery as the other say-do guards.

Only an explicit reference to the screen counts: "screen", "screenshot",
"what do you see". Japanese is matched without word boundaries.
"""

from __future__ import annotations

import re

SCREEN_TOOL = "screenshot"

_JA_SCREEN_RE = re.compile(
    # gamen / sukuriin / sukusho
    "(\u753b\u9762|\u30b9\u30af\u30ea\u30fc\u30f3|\u30b9\u30af\u30b7\u30e7)"
    ".{0,12}"
    # mite / yonde / kakunin / totte / oshiete
    "(\u898b\u3066|\u8aad\u3093\u3067|\u78ba\u8a8d|\u64ae\u3063\u3066|\u6559\u3048\u3066"
    # nani ga / setsumei / utsutte / hyouji
    "|\u4f55\u304c|\u306a\u306b\u304c|\u8aac\u660e|\u6620\u3063\u3066|\u8868\u793a)"
)
_LATIN_SCREEN_RE = re.compile(
    r"\b(screenshot|screen\s*shot|bildschirmfoto)\b"
    r"|\b(look\s+at|check|read|describe|what('s|\s+is)\s+on)\b.{0,15}\b(my\s+|the\s+)?screen\b"
    r"|\bwhat\s+do\s+you\s+see\b"
    r"|\b(schau|guck|lies)\w*\b.{0,20}\bbildschirm\b"  # i18n-allow
    r"|\bwas\s+(ist|steht|siehst\s+du)\b.{0,20}\bbildschirm\b"  # i18n-allow
    r"|\b(mira|lee|describe)\b.{0,15}\b(mi\s+|la\s+)?pantalla\b",  # i18n-allow
    re.IGNORECASE,
)


def wants_screen_look(text: str) -> bool:
    """True when ``text`` explicitly asks Jarvis to look at the screen."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_JA_SCREEN_RE.search(t) or _LATIN_SCREEN_RE.search(t))


SCREEN_DIRECTIVE = (
    "The user asks you to look at their screen. Call the screenshot tool FIRST "
    "and answer only from the image it returns; window titles in your context "
    "are not what the screen shows."
)


__all__ = ["SCREEN_DIRECTIVE", "SCREEN_TOOL", "wants_screen_look"]
