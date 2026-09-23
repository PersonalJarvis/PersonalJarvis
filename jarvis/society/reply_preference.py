"""Durable, agent-scoped reply preferences using the existing memory notebook."""

from __future__ import annotations

import logging
import re

from jarvis.core.turn_language import detect_language_request, resolve_output_language

from .notebook import Entry
from .working_rules import PREFIX

_DURABLE = re.compile(
    r"\b(?:always|from now on|immer|ab jetzt|siempre|a partir de ahora)\b", re.I  # i18n-allow
)  # i18n-allow
_NAMES = {"en": "English", "de": "German", "es": "Spanish"}
log = logging.getLogger(__name__)


def instruction(language: str) -> str:
    return PREFIX + f"Reply always in {_NAMES[language]}."


def durable_language_request(text: str) -> str:
    return detect_language_request(text) if _DURABLE.search(text) else ""


def stored_language(entries: list[Entry]) -> str:
    for entry in sorted(entries, key=lambda item: item.revision, reverse=True):
        if entry.origin != "user":
            continue
        for code in _NAMES:
            if entry.text == instruction(code):
                return code
    return ""


async def resolve_agent_reply_language(session_id: str, text: str, fallback: str = "") -> str:
    """Feed a durable preference into the one existing authoritative resolver."""
    from .runtime import current_runtime
    from .surface import agent_id_of

    runtime = current_runtime()
    agent_id = agent_id_of(session_id)
    if runtime is None or not agent_id:
        return fallback
    agent = await runtime.roster.get(agent_id)
    if agent is None:
        return fallback
    try:
        preference = stored_language(runtime.memory.entries(agent))
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("society: reply preference could not be read for %s", agent_id, exc_info=True)
        return fallback
    requested = detect_language_request(text)
    if not preference and not requested:
        return fallback
    configured = getattr(getattr(runtime.config(), "brain", None), "reply_language", "auto")
    # Explicit settings retain their precedence. A current language request
    # overrides an older agent preference without mutating the shared config.
    pin = configured if configured in _NAMES else requested or preference
    return resolve_output_language(pin, "unknown", text)
