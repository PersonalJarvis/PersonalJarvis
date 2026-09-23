"""Recognize explicit requests to create an agent routine in a chat turn."""

from __future__ import annotations

import re

from jarvis.skills.authoring_request import is_skill_authoring_request

_ROUTINE_NOUN = re.compile(r"\b(?:\w*routinen?|rutinas?)\b", re.IGNORECASE)  # i18n-allow: input vocabulary
_CREATE_VERB = re.compile(
    r"\b(?:erstell\w*|anleg\w*|einricht\w*|creat\w*|set\s+up|"  # i18n-allow: input vocabulary
    r"program\w*|configur\w*)\b",
    re.IGNORECASE,
)
_HOW_TO = re.compile(
    r"\b(?:wie\s+(?:kann|könnte|soll|würde|erstelle)|how\s+(?:do|can|to)|"  # i18n-allow: input vocabulary
    r"cómo\s+(?:puedo|crear))\b",
    re.IGNORECASE,
)
_CONFIRMED_CREATE = re.compile(
    r"^\s*(?:bitte\s+|please\s+)?(?:erstell\w*|create|set\s+up|"  # i18n-allow: input vocabulary
    r"configur\w*)\s+(?:mir\s+)?(?:die|diese|the|this)\s+routine\b",
    re.IGNORECASE,
)
_PAST_REPORT = re.compile(
    r"\b(?:wurde|war|was|were|had|habe|hatte)\s+\w*",  # i18n-allow: input vocabulary
    re.IGNORECASE,
)


def requests_routine_creation(text: str) -> bool:
    """Require a routine noun and creation intent; ignore how-to questions."""
    if not _ROUTINE_NOUN.search(text) or _HOW_TO.search(text):
        return False
    if _CONFIRMED_CREATE.search(text):
        return True
    return bool(
        _CREATE_VERB.search(text)
        and not _PAST_REPORT.search(text)
        and is_skill_authoring_request(text)
    )
