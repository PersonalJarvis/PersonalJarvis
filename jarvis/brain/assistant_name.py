"""Resolve the assistant's own name (how it refers to itself).

Resolution order (first non-empty wins):
  1. ``[persona].name`` — an explicit override, for when the spoken identity
     should differ from the wake word (wake "Hey Computer", identity "Friday").
  2. The wake phrase with its prefix stripped — "Hey Jarvis" -> "Jarvis",
     "Micron" -> "Micron", "Hey Athena" -> "Athena".
  3. ``DEFAULT_ASSISTANT_NAME`` — the neutral shipped fallback when no wake
     phrase and no persona name is set (pre-onboarding state). A user who types
     "Hey Jarvis" gets "Jarvis" via path 2; this constant is not "Jarvis" so
     the product does not silently impose a name.

Capitalisation: the derived name is title-cased token-by-token so a lowercase
wake phrase ("micron") still yields a proper name ("Micron").
"""
from __future__ import annotations

from typing import Any

DEFAULT_ASSISTANT_NAME = "Assistant"


def resolve_assistant_name(config: Any) -> str:
    """Return the assistant's display name from ``config`` (see module docstring)."""
    # 1. Explicit [persona].name override.
    persona = getattr(config, "persona", None)
    explicit = (getattr(persona, "name", "") or "").strip() if persona is not None else ""
    if explicit:
        return explicit

    # 2. Derive from the wake phrase (prefix stripped).
    trigger = getattr(config, "trigger", None)
    wake_word = getattr(trigger, "wake_word", None) if trigger is not None else None
    phrase = (getattr(wake_word, "phrase", "") or "") if wake_word is not None else ""
    if phrase:
        try:
            from jarvis.speech.wake_constants import phrase_core

            core = phrase_core(phrase)
        except Exception:  # noqa: BLE001 — never break name resolution on import/parse
            core = []
        if core:
            return " ".join(tok.capitalize() for tok in core)

    # 3. Historical default / safety fallback.
    return DEFAULT_ASSISTANT_NAME
