"""Spot an agent that asks for a secret in plain text instead of the secure field.

A society agent gets credentials only through ``society_request_credential``
(jarvis/society/credential_tool.py): the chat shows a password field and the
value never enters the conversation. Models still fall back to old habits:
"please paste your bot token", "enter it in a secure field", a question card
whose free-text answer would carry the token, or "the token is already
stored" when nothing is. These checks catch those replies so the turn can be
steered back to the tool (turn_completion.py) or a question card refused
(society/ask_tool.py).

Deliberately lexical and narrow: a sentence must name a secret AND ask the
user to hand it over (or claim one is stored). Mentioning a stored variable by
name, or explaining what a token is, does not match.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["asks_for_secret", "claims_stored_secret", "SECRET_NOUN"]

#: Something only the user can supply and that must never travel through the chat.
SECRET_NOUN: Final[re.Pattern[str]] = re.compile(
    r"(?:\b(?:api[\s_-]?keys?|access[\s_-]?keys?|secret[\s_-]?keys?|private[\s_-]?keys?|"
    r"client[\s_-]?secrets?|secrets?|tokens?|passwords?|passphrases?|credentials?|"
    r"webhook[\s_-]?urls?|connection[\s_-]?strings?|ssh[\s_-]?keys?|"
    r"\w*token\w*|\w*passwor[dt]\w*|"
    r"kennw[oö]rt\w*|zugangsdaten|anmeldedaten|\w*schl[uü]ssel\w*|"  # i18n-allow
    r"contrase[nñ]as?|claves?|credenciales)\b"  # i18n-allow
    r"|令牌|密钥|密码|凭据|凭证)",  # i18n-allow
    re.I,
)

#: The user is asked to hand something over, or told where to put it.
_HAND_OVER: Final[re.Pattern[str]] = re.compile(
    r"(?:\b(?:please|send|paste|enter|provide|share|give|copy|"
    r"i need|we need|i'?ll need|need your|need a|need the|do you have|reply with|"
    r"secure (?:input )?field|"
    r"bitte|gib|gebe|schick\w*|sende|teil\w*|f[uü]g\w*|kopier\w*|trag\w*|"  # i18n-allow
    r"hinterleg\w*|eingeben|einf[uü]gen|brauche|ben[oö]tige|hast du|"  # i18n-allow
    r"sicheres?n? (?:eingabe)?feld|"  # i18n-allow
    r"env[ií]a\w*|pega\w*|introduc\w*|necesito|proporciona\w*|compart\w*|ingresa\w*|"  # i18n-allow
    r"por favor)\b"  # i18n-allow
    r"|\?|请|提供|输入|粘贴|需要)",  # i18n-allow
    re.I,
)

#: Only the value itself is asked for (the answer would BE the secret).
_VALUE: Final[re.Pattern[str]] = re.compile(
    r"(?:\b(?:send|paste|enter|provide|share|give me|type|reply with|"
    r"(?:what|which) (?:is|are) (?:your|the)|"
    r"gib|schick\w*|sende|teil\w* mir|nenn\w*|eingeben|einf[uü]gen|wie lautet|"  # i18n-allow
    r"was ist dein\w*|"  # i18n-allow
    r"env[ií]a\w*|pega\w*|introduc\w*|proporciona\w*|cu[aá]l es (?:tu|la|el))\b"  # i18n-allow
    r"|请提供|输入|粘贴)",  # i18n-allow
    re.I,
)

#: A sentence that reports a credential as already saved.
_STORED: Final[re.Pattern[str]] = re.compile(
    r"\b(?:(?:is|was|has been|are|have been) (?:already |now |securely |safely )*"
    r"(?:stored|saved|set|configured)|"
    r"(?:ist|wurde|sind|wurden) (?:\w+ ){0,3}(?:gespeichert|hinterlegt|gesetzt|"  # i18n-allow
    r"eingerichtet)|"  # i18n-allow
    r"(?:est[aá]|fue|ya est[aá]) (?:\w+ )?(?:guardad[oa]|almacenad[oa]))\b",  # i18n-allow
    re.I,
)

_SENTENCE: Final[re.Pattern[str]] = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.findall(text or "") if s.strip()]


def asks_for_secret(text: str, *, value_only: bool = False) -> bool:
    """``text`` asks the user to hand over a secret, or says where to put one.

    ``value_only``: only a request for the value itself counts (a question card
    whose typed answer would be the secret), not "token or browser login?".
    A sentence that only reports a credential as stored does not count; see
    ``claims_stored_secret`` for that case.
    """
    cue = _VALUE if value_only else _HAND_OVER
    for sentence in _sentences(text):
        if not SECRET_NOUN.search(sentence) or _STORED.search(sentence):
            continue
        if cue.search(sentence):
            return True
    return False


def claims_stored_secret(text: str) -> bool:
    """``text`` says a credential is already stored or set."""
    return any(
        SECRET_NOUN.search(sentence) and _STORED.search(sentence) for sentence in _sentences(text)
    )
