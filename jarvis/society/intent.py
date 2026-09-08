"""Pure intent boundaries for the persistent team, shared by voice and brain."""

from __future__ import annotations

import re
from collections.abc import Sequence

# Full utterances only: a combined question and assignment must reach the
# orchestrator rather than lose the requested assignment in a read-only path.
# i18n-allow: multilingual spoken-input vocabulary
_INVENTORY = re.compile(
    r"(?:"
    r"(?:was|welche|wie viele|wieviele) (?:hast du|habe ich|haben wir) "
    r"(?:(?:fuer|für) )?agent(?:en|s)?"  # i18n-allow: spoken-input vocabulary
    r"|(?:welche|wie viele|wieviele) agent(?:en|s)? "
    r"(?:hast du|habe ich|haben wir|gibt es|"  # i18n-allow: spoken-input vocabulary
    r"stehen (?:dir|mir|uns) zur verf(?:ue|ü)gung)"  # i18n-allow: spoken-input vocabulary
    r"|(?:which|what|how many) agents (?:do you have(?: access to)?|do i have|"
    r"do we have|are there|are on (?:the|my|our) team|are available)"
    r"|who is (?:on|in) (?:the|my|our|your) team"
    r"|(?:list|show|tell me) (?:all |me )?(?:(?:my|your|our|the) )?agents"
    r"|(?:zeig(?:e)?|nenn(?:e)?) (?:mir )?(?:(?:alle|meine|deine|unsere|die) )?agenten"
    r"|(?:qué|que|cuántos|cuantos|cuáles|cuales) agentes (?:tienes|tengo|tenemos|hay)"
    r")",
    re.IGNORECASE,
)


def is_inventory_question(text: str) -> bool:
    """An unambiguous roster request; never an assignment or management action."""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip(" .?!¿¡")
    normalized = re.sub(
        r"\b(?:eigentlich|denn|bitte|actually|currently|please"  # i18n-allow
        r"|right now|por favor)\b",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return _INVENTORY.fullmatch(normalized) is not None


def names_team_member(text: str, names: Sequence[str]) -> bool:
    """Match even short real names at word boundaries, never substrings."""
    return any(
        name.strip() and re.search(rf"(?<!\w){re.escape(name.strip())}(?!\w)", text, re.IGNORECASE)
        for name in names
    )


def blocks_background_spawn(text: str, names: Sequence[str] = ()) -> bool:
    """Team reads and addressed members must not become anonymous workers.

    Explicit creation of a background worker remains a separate request; a
    specialist can be mentioned as context in such a request.
    """
    if is_inventory_question(text) or is_team_management_request(text):
        return True
    if not names_team_member(text, names):
        return False
    return not explicitly_requests_worker(text)


def is_team_management_request(text: str) -> bool:
    """Profile creation/reconfiguration belongs to the existing Agents surface.

    This only selects the orchestrator; it does not execute a management
    action. Explicit worker creation retains its separate routing semantics.
    """
    if explicitly_requests_worker(text):
        return False
    return (
        re.search(
            r"\b(?:creat\w*|add|updat\w*|reconfigur\w*|renam\w*|"
            r"erstell\w*|konfigurier\w*|aender\w*|änder\w*|crea\w*|actualiza\w*)\b"  # i18n-allow
            r"[^.?!]{0,70}\bagent(?:en|s|e|es)?\b",
            text,
            re.IGNORECASE,
        )
        is not None
    )


def explicitly_requests_worker(text: str) -> bool:
    """Whether a new background worker is the explicitly requested vehicle."""
    # i18n-allow: multilingual spoken-input vocabulary
    return (
        re.search(
            r"\b(?:spawn\w*|start\w*|creat\w*|erstell\w*|crea\w*)\b"
            r"[^.?!]{0,45}\b(?:sub-?agent\w*|worker\w*|background agent|mission\w*)\b",
            text,
            re.IGNORECASE,
        )
        is not None
    )
