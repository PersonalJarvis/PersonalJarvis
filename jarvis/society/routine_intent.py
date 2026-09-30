"""Recognize requests to create an agent routine or other recurring work."""

from __future__ import annotations

import re

from jarvis.skills.authoring_request import is_skill_authoring_request

_ROUTINE_NOUN = re.compile(
    r"\b(?:\w*routinen?|rutinas?)\b",  # i18n-allow: input vocabulary
    re.IGNORECASE,
)
_CREATE_VERB = re.compile(
    r"\b(?:erstell\w*|anleg\w*|einricht\w*|creat\w*|set\s+up|"  # i18n-allow: input vocabulary
    r"program\w*|configur\w*)\b",
    re.IGNORECASE,
)
_HOW_TO = re.compile(
    r"\b(?:wie\s+(?:kann|könnte|soll|würde|erstelle)|"  # i18n-allow: input vocabulary
    r"how\s+(?:do|can|to)|"
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


# Recurrence phrases in English, German and Spanish ("every day at 8").
_RECURRENCE = re.compile(
    r"\b(?:every\s+(?:day|morning|evening|night|week|weekday|month|hour|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d+\s+\w+)|"
    r"each\s+(?:day|morning|evening|week|month)|daily|weekly|monthly|hourly|"
    r"jede[nrs]?\s+(?:tag|morgen|abend|nacht|woche|werktag|"  # i18n-allow: input vocabulary
    r"monat|stunde|montag|dienstag|mittwoch|donnerstag|"  # i18n-allow: input vocabulary
    r"freitag|samstag|sonntag)|"  # i18n-allow: input vocabulary
    r"täglich\w*|taeglich\w*|wöchentlich\w*|"  # i18n-allow: input vocabulary
    r"monatlich\w*|stündlich\w*|"  # i18n-allow: input vocabulary
    r"alle\s+\d+\s+\w+|morgens\s+um|abends\s+um|werktags|"  # i18n-allow: input vocabulary
    r"cada\s+(?:día|dia|mañana|manana|noche|semana|mes|hora|"  # i18n-allow: input vocabulary
    r"lunes|martes|miércoles|jueves|viernes|sábado|domingo|"  # i18n-allow: input vocabulary
    r"\d+\s+\w+)|"
    r"todos\s+los\s+(?:días|dias|lunes)|"  # i18n-allow: input vocabulary
    r"diariamente|semanalmente)\b",
    re.IGNORECASE,
)
# The person asks for something to happen, rather than describing a habit.
_REQUEST_CUE = re.compile(
    r"\b(?:soll\w*|möcht\w*|will|bitte|mach\w*|gib|"  # i18n-allow: input vocabulary
    r"schick\w*|send\w*|erstell\w*|erinner\w*|"  # i18n-allow: input vocabulary
    r"kannst|könntest|fass\w*|informier\w*|"  # i18n-allow: input vocabulary
    r"should|want|please|give|make|create|remind|set\s+up|can\s+you|could\s+you|"
    r"summari[sz]e|brief|tell\s+me|"
    r"quiero|por\s+favor|dame|envía|envia|crea|"  # i18n-allow: input vocabulary
    r"recuérdame|puedes)\b",  # i18n-allow: input vocabulary
    re.IGNORECASE,
)


def requests_recurring_work(text: str) -> bool:
    """A request for work that repeats on a schedule, with or without the word
    "routine" ("give me a briefing every day at 8"); habits and how-to
    questions do not count."""
    if not _RECURRENCE.search(text) or _HOW_TO.search(text):
        return False
    return bool(_REQUEST_CUE.search(text)) and not _PAST_REPORT.search(text)


_SKILL_WORD = re.compile(r"\bskills?\b", re.IGNORECASE)


def wants_agent_routine(text: str) -> bool:
    """Scheduled work for an agent, not a request to author a skill.

    "Routine" is also a spoken synonym for a skill; the user saying "skill"
    keeps that meaning. Everything else that asks for recurring work or
    creates a routine belongs to ``society-create-routine``.
    """
    if _SKILL_WORD.search(text):
        return False
    if requests_recurring_work(text):
        return True
    return bool(
        _ROUTINE_NOUN.search(text)
        and _CREATE_VERB.search(text)
        and not _HOW_TO.search(text)
        and not _PAST_REPORT.search(text)
    )
