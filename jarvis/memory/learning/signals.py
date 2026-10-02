"""Decide without a model whether a user turn can hold something worth keeping.

Most turns are requests ("play some music", "what's the weather"): they say
nothing lasting about the user and must cost nothing. Only a turn in which the
user talks about themselves, states a preference, corrects the assistant, or
names a plan or goal is ever shown to the reviewer. Missing an odd phrasing
is acceptable; paying for every turn is not.

The patterns cover the shipped input languages (English, German, Spanish).
"""

from __future__ import annotations

import re
from typing import Final

#: Shortest turn that can carry a durable fact ("I'm vegan" has 9 characters).
_MIN_CHARS: Final[int] = 8

_PEOPLE_AND_THINGS_EN = (
    "name|wife|husband|partner|girlfriend|boyfriend|son|daughter|kids?|children|mother|mom"
    "|father|dad|brother|sister|friend|boss|team|company|job|work|office|home|birthday"
    "|address|car|dog|cat|project|goal|plan|deadline|launch|trip|wedding|exam|appointment"
)
_PEOPLE_AND_THINGS_DE = (  # i18n-allow: input vocabulary
    "name|frau|mann|freundin|freund|sohn|tochter|kinder?|mutter|mama|vater|papa"  # i18n-allow
    "|bruder|schwester|chef|chefin|team|firma|job|arbeit|büro|zuhause|geburtstag"  # i18n-allow
    "|adresse|auto|hund|katze|projekt|ziel|plan|deadline|launch|reise|hochzeit"  # i18n-allow
    "|prüfung|termin"  # i18n-allow
)
_PEOPLE_AND_THINGS_ES = (  # i18n-allow: input vocabulary
    "nombre|esposa|esposo|pareja|hijo|hija|madre|padre|hermano|hermana|jefe|equipo"  # i18n-allow
    "|empresa|trabajo|proyecto|plan|viaje|boda|examen|cita"  # i18n-allow
)

_SIGNALS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # About themselves: identity, life, work, possessions, people, plans.
        r"\b(?:i am|i'm|i work|i live|i have|i've got|i own|i use|i usually|i always"
        r"|i never)\b",
        rf"\bmy (?:{_PEOPLE_AND_THINGS_EN})\b",
        r"\bich (?:bin|arbeite|wohne|lebe|habe|besitze|nutze|benutze|stehe)\b",  # i18n-allow
        rf"\bmein(?:e|en|em|er)? (?:{_PEOPLE_AND_THINGS_DE})\b",  # i18n-allow
        r"\b(?:soy|estoy|trabajo|vivo en|tengo)\b",  # i18n-allow
        rf"\bmi (?:{_PEOPLE_AND_THINGS_ES})\b",  # i18n-allow
        # Preferences and standing wishes.
        r"\bi (?:prefer|like|love|hate|don't like|do not like|can't stand|want you to)\b",
        r"\b(?:call me|please always|please never|from now on|remember)\b",
        r"\bich (?:mag|liebe|hasse|bevorzuge|will dass du|möchte dass du)\b",  # i18n-allow
        r"\b(?:nenn mich|bitte immer|bitte nie|ab jetzt|in zukunft|merk dir)\b",  # i18n-allow
        r"\b(?:prefiero|me gusta|no me gusta|odio|llámame|a partir de ahora)\b",  # i18n-allow
        # Corrections of the assistant.
        r"\b(?:that's wrong|that is wrong|not what i (?:said|meant|asked)|i told you"
        r"|you forgot|no,? i meant|actually,? i)\b",
        r"\b(?:das (?:ist|war) falsch|nein,? ich meinte|hab ich (?:dir )?doch gesagt"  # i18n-allow
        r"|du hast vergessen|stimmt nicht)\b",  # i18n-allow
        r"\b(?:eso está mal|no es lo que dije|te dije|te olvidaste)\b",  # i18n-allow
        # Plans and goals.
        r"\b(?:i|we)(?: am|'m| are|'re)? (?:plan|planning|going to|working on|preparing"
        r"|moving|launching)\b",
        r"\b(?:deadline|launch|release date|due date)\b",
        r"\b(?:ich|wir) (?:plane|planen|werde|werden|arbeite gerade an|arbeiten an"  # i18n-allow
        r"|bereite|bereiten|ziehe um|ziehen um)\b|\b(?:umzug|abgabe)\b",  # i18n-allow
        r"\b(?:voy a|vamos a|planeo|planeamos|estoy preparando|me mudo)\b",  # i18n-allow
    )
)


def has_signal(text: str) -> bool:
    """True when ``text`` may hold a durable fact, preference, correction or plan."""
    clean = " ".join((text or "").split())
    if len(clean) < _MIN_CHARS:
        return False
    return any(pattern.search(clean) for pattern in _SIGNALS)
