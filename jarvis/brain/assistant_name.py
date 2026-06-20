"""Resolve the assistant's own name (how it refers to itself).

The name is a pure function of the wake phrase — there is no separate name
setting. Resolution order (first non-empty wins):
  1. The wake phrase with its trigger prefix stripped — "Hey Jarvis" -> "Jarvis",
     "Micron" -> "Micron", "Hey Athena" -> "Athena", "Hey Computer" -> "Computer".
  2. ``DEFAULT_ASSISTANT_NAME`` — the neutral shipped fallback when no wake phrase
     is set (pre-onboarding state). Not "Jarvis", so the product imposes no name.

A legacy ``[persona].name`` key in an old jarvis.toml is intentionally ignored:
the wake word is the single control (see the 2026-06-20 coupling design).

Capitalisation: the derived name is title-cased token-by-token so a lowercase
wake phrase ("micron") still yields a proper name ("Micron").
"""
from __future__ import annotations

from typing import Any

DEFAULT_ASSISTANT_NAME = "Assistant"

# The name baked into the static persona files (SOUL.md / JARVIS_PERSONA.md).
# When the resolved name equals this, the system prompt needs no identity-override
# directive — the persona files already say "Jarvis". Used by
# ``BrainManager._build_system_prompt`` so it never emits the self-contradictory
# "Du heisst Jarvis — nicht Jarvis" for the default wake word.
PERSONA_BASELINE_NAME = "Jarvis"


def resolve_assistant_name(config: Any) -> str:
    """Return the assistant's display name from ``config`` (see module docstring)."""
    # 1. Derive from the wake phrase (prefix stripped, title-cased).
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

    # 2. Historical default / safety fallback.
    return DEFAULT_ASSISTANT_NAME
