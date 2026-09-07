"""Disambiguate an internal addressee from an external messaging connector."""

from __future__ import annotations

import re
from collections.abc import Iterable


def is_internal_message_request(text: str, agent_names: Iterable[str]) -> bool:
    """Require both a message request and a named teammate, not just 'Gmail'."""
    norm = text.casefold()
    if not re.search(r"\b(message|nachricht|testnachricht|mensaje)\w*\b", norm):  # i18n-allow: speech-input vocabulary
        return False
    if not re.search(
        r"\b(send|write|tell|schreib\w*|sende?\w*|schick\w*|env[ií]\w*|escrib\w*)\b", norm
    ):
        return False
    for name in agent_names:
        words = re.findall(r"\w+", name.casefold())
        if not words:
            continue
        pattern = r"\s+".join(
            r"agent(?:en|s|e)?" if word == "agent" else re.escape(word) for word in words
        )
        if re.search(r"\b" + pattern + r"\b", norm):
            return True
    return False
