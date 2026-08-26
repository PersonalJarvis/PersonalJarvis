"""Reasoning-effort levels per agent-chat provider.

Every provider exposes its own ladder: Claude Code takes ``--effort low |
medium | high | xhigh | max``, the Codex CLI ``low .. ultra`` (per model), the
OpenAI API ``none .. max`` (per model), Gemini 3 has three ``thinking_level``
steps, Antigravity's ``agy`` takes ``low | medium | high`` (per model, none
on its Claude models). The picker in the
composer shows exactly the ladder of the provider in use, and
:func:`normalize_effort` folds a picked level onto the nearest one a provider
accepts when the two disagree (a session that moves from Codex to Gemini
keeps "high"; "xhigh" becomes Gemini's "high").

Single source of truth: the frontend receives these lists through
``GET /api/agent-chat/catalog`` and never mirrors them (AP-4).
"""

from __future__ import annotations

from typing import Final

# The universal ordering — every provider ladder is a sub-sequence of this.
ORDER: Final[tuple[str, ...]] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)

# Provider id -> (offered levels in ascending order, default level).
# ``""`` as default means "leave it to the provider" and is shown as
# "Default" in the picker; it is only used for providers whose models vary
# too much to pick one honest default (OpenRouter, NVIDIA NIM, local servers).
_LADDERS: Final[dict[str, tuple[tuple[str, ...], str]]] = {
    # Anthropic low/medium/high/xhigh/max — one ladder for both runners.
    # Claude Code takes it as ``--effort``; the API takes it as
    # ``output_config.effort`` plus adaptive thinking on the models that
    # have it (``_anthropic_base.reasoning_kwargs``, which snaps a level a
    # model does not offer down to its nearest one). So the pick means the
    # same thing on the front page's chat, where the API always answers, as
    # it does on a Claude Code seat in the IDE's.
    "claude-api": (("low", "medium", "high", "xhigh", "max"), "high"),
    # OpenAI reasoning.effort for the GPT-5 family (``max`` since GPT-5.6;
    # ``minimal`` only on the older 5.x — the plugin retries without a level
    # the model rejects).
    "openai": (("none", "minimal", "low", "medium", "high", "xhigh", "max"), "medium"),
    # Codex CLI ``model_reasoning_effort`` (codex 0.149: low..xhigh on every
    # current model, ``max`` on the 5.6 family, ``ultra`` = max + automatic
    # task delegation on terra/sol). The catalog narrows it per model.
    "openai-codex": (("low", "medium", "high", "xhigh", "max", "ultra"), "medium"),
    # Gemini 3 thinking_level. MINIMAL exists on some 3.x models but 400s on
    # others (3.7-flash), and the plugin maps nothing onto it — so it is not
    # offered here; 2.5 maps onto budgets, the plugin handles both.
    "gemini": (("low", "medium", "high"), "medium"),
    "vertex": (("low", "medium", "high"), "medium"),
    # agy --effort.
    "antigravity": (("low", "medium", "high"), "medium"),
    # OpenRouter forwards reasoning.effort to whatever sits behind the slug.
    "openrouter": (("", "none", "low", "medium", "high", "xhigh"), ""),
    # xAI reasoning_effort: grok-4.6 low..xhigh, grok-4.5 low..high; "" leaves
    # a model that reasons on its own alone.
    "grok": (("", "low", "medium", "high", "xhigh"), ""),
    "grok-build": (("", "low", "medium", "high"), ""),
    "nvidia": (("", "none", "low", "medium", "high"), ""),
    "ollama": (("", "none", "low", "medium", "high", "max"), ""),
    "local-openai": (("", "none", "low", "medium", "high"), ""),
}

_FALLBACK_LADDER: Final[tuple[tuple[str, ...], str]] = (("", "low", "medium", "high"), "")


def effort_levels(provider: str) -> tuple[str, ...]:
    """The ladder the composer offers for ``provider`` (ascending)."""
    return _LADDERS.get((provider or "").strip().lower(), _FALLBACK_LADDER)[0]


def default_effort(provider: str) -> str:
    """The level a fresh session on ``provider`` starts with."""
    return _LADDERS.get((provider or "").strip().lower(), _FALLBACK_LADDER)[1]


def snap_to_ladder(level: str | None, ladder: tuple[str, ...] | list[str]) -> str:
    """Fold ``level`` onto ``ladder`` (a model's own levels), lower neighbour first.

    ``""``/``None`` passes through (provider default); an empty ladder means
    the model has no knob and returns ``""``.
    """
    picked = (level or "").strip().lower()
    offered = [lvl for lvl in ladder if lvl]
    if not picked or not offered:
        return "" if not offered else picked if picked in offered else ""
    if picked in offered:
        return picked
    if picked not in ORDER:
        return offered[0]
    idx = ORDER.index(picked)
    lower = [lvl for lvl in offered if lvl in ORDER and ORDER.index(lvl) <= idx]
    return lower[-1] if lower else offered[0]


def normalize_effort(provider: str, level: str | None) -> str:
    """Fold ``level`` onto the nearest level ``provider`` accepts.

    ``""``/``None`` means "provider default" and passes through. A level the
    provider offers passes through unchanged. Anything else snaps to the
    closest offered level on the universal ladder, preferring the lower
    neighbour (a stronger model that cannot go higher should not silently
    cost more): ``xhigh`` on Gemini -> ``high``; ``none`` on Claude Code ->
    ``low``; ``max`` on Codex -> ``xhigh``.
    """
    picked = (level or "").strip().lower()
    if not picked:
        return ""
    offered = [lvl for lvl in effort_levels(provider) if lvl]
    if picked in offered:
        return picked
    if picked not in ORDER or not offered:
        return default_effort(provider)
    idx = ORDER.index(picked)
    lower = [lvl for lvl in offered if ORDER.index(lvl) <= idx]
    if lower:
        return lower[-1]
    return offered[0]
