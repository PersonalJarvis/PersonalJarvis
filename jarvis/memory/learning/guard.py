"""Screen text before it becomes a durable notebook entry.

A notebook entry is re-read in every later prompt, so one poisoned line would
act on every future turn. The reviewer's evidence check already requires the
user's own words; this guard additionally refuses credentials, instruction
injection, orders phrased as entries, and invisible or unassigned characters,
whatever their source.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Final

log = logging.getLogger(__name__)

#: Longest single entry. A fact that needs more is a wiki page, not a note.
MAX_ENTRY_CHARS: Final[int] = 300

#: Format (Cf: zero-width, bidi, tag characters), private-use and unassigned
#: code points. None belongs in a note; all can smuggle hidden text.
_HIDDEN_CATEGORIES: Final[frozenset[str]] = frozenset({"Cf", "Co", "Cn"})

_INJECTION: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:previous|prior|above|earlier"
        r"|all|system)\b.{0,20}\b(?:instructions?|rules?|prompts?|messages?)",
        r"\b(?:system|developer)\s+(?:prompt|message|instructions?)\b",
        r"\byou are now\b|\bact as (?:an? )?(?:unrestricted|jailbroken|different)\b",
        r"\bdo not (?:tell|inform|mention (?:this )?to) the user\b",
        r"\b(?:send|post|upload|exfiltrate)\b.{0,60}\bhttps?://",
        r"\b(?:curl|wget|invoke-webrequest)\b",
        r"\bauthorized_keys\b|~/\.ssh\b|(?:^|[\s/])\.env\b",
        r"<\s*/?\s*(?:system|assistant|tool)\s*>",
        # Input vocabulary of the other shipped locales: the reviewer may copy
        # a quoted phrase instead of translating it.
        r"\bignorier\w*\b.{0,40}\b(?:vorherig|bisherig|alle|obig)\w*\b"  # i18n-allow
        r".{0,20}\b(?:anweisung|regel|vorgabe)\w*",  # i18n-allow
        r"\bdu bist (?:jetzt|ab jetzt|nun)\b",  # i18n-allow
        r"\b(?:sag|sage|verrate)\w* (?:dem|der) (?:nutzer|benutzer|user)\w* nicht",  # i18n-allow
        r"\bignora\w*\b.{0,40}\binstrucciones\b|\bahora eres\b",  # i18n-allow
    )
)

#: An entry is a statement about the user or the environment, never an order
#: to the assistant (orders get re-read as directives in every later prompt).
_ORDER: Final = re.compile(
    r"^\s*(?:you\b|your\b|always\b|never\b|do not\b|don't\b|must\b|make sure\b"
    r"|du\b|immer\b|niemals\b|siempre\b|nunca\b)",  # i18n-allow
    re.IGNORECASE,
)


def hidden_characters(text: str) -> bool:
    return any(unicodedata.category(ch) in _HIDDEN_CATEGORIES for ch in text or "")


def refusal(text: str) -> str | None:
    """Why ``text`` must not be stored, or ``None`` when it may."""
    if hidden_characters(text):
        return "invisible or unassigned characters"
    clean = unicodedata.normalize("NFKC", text or "").strip()
    if not clean:
        return "empty"
    if len(clean) > MAX_ENTRY_CHARS:
        return "too long"
    if any(pattern.search(clean) for pattern in _INJECTION):
        return "instruction-like content"
    if _ORDER.search(clean):
        return "phrased as an order, not a fact"
    if contains_secret(clean):
        return "credential-shaped content"
    return None


def contains_secret(text: str) -> bool:
    """The wiki's secret detector; refuses everything when it is missing."""
    try:
        from jarvis.memory.wiki.secret_guard import contains_secret as _detect
    except ImportError:
        # The guard ships with the package; without it we store nothing.
        log.warning("learning guard: secret guard unavailable, refusing the write")
        return True
    return bool(_detect(text))
