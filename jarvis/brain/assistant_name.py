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

# NOTE: there is no longer a "PERSONA_BASELINE_NAME". The persona files
# (SOUL.md / JARVIS_PERSONA.md) were made name-neutral on 2026-06-29, so the
# system prompt no longer bakes in "Jarvis" anywhere. ``_build_system_prompt``
# now emits the identity directive for every resolved name except the neutral
# ``DEFAULT_ASSISTANT_NAME`` fallback, and there is no "Jarvis" baseline to
# special-case or contradict.


def agent_brand_from_name(assistant_name: str) -> str:
    """Return the public agent-system display brand for ``assistant_name``.

    2026-07-17 rebrand: the user-visible name of the agent system follows the
    wake-word-derived assistant name — "Ruben" -> "Ruben-Agent", "Athena" ->
    "Athena-Agent" — for ANY configured wake word, never a hardcoded product
    name. Internal identifiers keep the "Jarvis-Agents" system name; only
    display/spoken surfaces use this brand. TS mirror:
    ``jarvis/ui/web/frontend/src/lib/agentBrand.ts``.
    """
    name = (assistant_name or "").strip() or DEFAULT_ASSISTANT_NAME
    return f"{name}-Agent"


def agent_brand(config: Any) -> str:
    """Return the agent-system display brand resolved from ``config``."""
    return agent_brand_from_name(resolve_assistant_name(config))


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
