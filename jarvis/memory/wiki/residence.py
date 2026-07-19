"""Deterministic residence detection shared by both Wiki review stages.

The detector recognizes only an explicitly asserted current residence in the
supported runtime languages. It supplies a schema-safe place slug so provider
responses can be checked semantically instead of relying on prompt compliance.
"""
from __future__ import annotations

import re

from jarvis.memory.wiki.session_links import slugify

_RESIDENCE_PLACE = r"(?P<place>[\w'.-]+(?:\s+[\w'.-]+){0,5}?)"
_RESIDENCE_END = (
    r"(?=\s*(?:[,;.!?]|$|\b(?:and|but|that|"
    r"und|aber|dass|"  # i18n-allow: German residence input vocabulary
    r"y|pero|que)\b))"
)
_RESIDENCE_PATTERNS = (
    re.compile(
        r"\b(?:i|the\s+user|user|speaker)\s+"
        r"(?:(?:now|currently)\s+)?"
        r"(?:live|lives|reside|resides|am\s+based)\s+"
        r"(?:now\s+)?(?:in|at)\s+"
        + _RESIDENCE_PLACE
        + _RESIDENCE_END,
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ich|der\s+benutzer|"  # i18n-allow: German input vocabulary
        r"die\s+nutzerin)\s+"  # i18n-allow: German residence input vocabulary
        r"(?:(?:jetzt|aktuell)\s+)?"  # i18n-allow: German residence input vocabulary
        r"(?:wohne|wohnt|lebe|lebt)\s+"  # i18n-allow: German residence input vocabulary
        r"(?:(?:jetzt|aktuell)\s+)?in\s+"  # i18n-allow: German residence input vocabulary
        + _RESIDENCE_PLACE
        + _RESIDENCE_END,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bich\s+(?:(?:jetzt|aktuell)\s+)?in\s+"  # i18n-allow: German residence input vocabulary
        + _RESIDENCE_PLACE
        + r"\s+(?:wohne|lebe)\b",  # i18n-allow: German residence input vocabulary
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:yo\s+)?(?:vivo|resido)\s+(?:ahora\s+)?en\s+"
        + _RESIDENCE_PLACE
        + _RESIDENCE_END,
        re.IGNORECASE,
    ),
)
_RESIDENCE_TRAILING_WORDS = frozenset(
    {
        "now",
        "currently",
        "jetzt",  # i18n-allow: German residence input vocabulary
        "aktuell",  # i18n-allow: German residence input vocabulary
        "nun",  # i18n-allow: German residence input vocabulary
        "ahora",
    }
)


def detect_residence_slug(source_content: str) -> str | None:
    """Return the named place slug from an asserted current residence."""
    source = " ".join(str(source_content or "").split())
    for pattern in _RESIDENCE_PATTERNS:
        match = pattern.search(source)
        if match is None:
            continue
        words = match.group("place").strip(" '.-").split()
        while words and words[-1].casefold() in _RESIDENCE_TRAILING_WORDS:
            words.pop()
        place_slug = slugify(" ".join(words))
        if place_slug:
            return place_slug
    return None


__all__ = ["detect_residence_slug"]
