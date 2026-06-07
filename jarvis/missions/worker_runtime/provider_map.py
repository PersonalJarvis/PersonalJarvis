"""Personal-Jarvis provider-slug to OpenClaw provider-slug mapping.

Wave-2 helper for the OpenClaw bridge. Pure data module without IO —
subprocess spawn mechanics live in Wave 3 (`jarvis/plugins/harness/openclaw.py`).

Source: docs/openclaw-bridge.md AD-6 Amendment (Wave-1 spike B-2, 2026-05-09).
Empirically verified via `openclaw models list --all` (1122 models, 46 providers).

Bridge mechanics (summary for future readers):
    1. cfg.brain.primary  -> jarvis provider slug (e.g. "gemini")
    2. to_provider_slug() -> OpenClaw slug (e.g. "google")
    3. cfg.brain.providers.<jarvis>.deep_model -> model ID (e.g. "gemini-3.1-pro-preview")
    4. CLI argument: --model "google/gemini-3.1-pro-preview"
    5. env_vars_for() -> ENV vars that OpenClaw reads at spawn time (primary +
       fallback; both are set so that OpenClaw provider drift does not break auth).

Why no dedicated OPENCLAW_<PROVIDER>_API_KEY namespace (as originally assumed in
AD-6)? OpenClaw reads the standard provider ENV vars like any other
Anthropic/OpenAI/Gemini client. Dual-maintenance in the wizard would be friction
without benefit.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

__all__ = [
    "ProviderMapping",
    "MAPPINGS",
    "JARVIS_TO_OPENCLAW",
    "OPENCLAW_TO_JARVIS",
    "UnknownJarvisProviderError",
    "UnknownOpenclawProviderError",
    "to_provider_slug",
    "to_jarvis_slug",
    "env_vars_for",
    "validate_configured_providers",
    "canonical_subagent_provider",
    "CODEX_SUBAGENT_SLUGS",
    "CODEX_SUBAGENT_CANONICAL",
]


# Subagent slugs that route to the DIRECT Codex CLI worker (CodexDirectWorker),
# NOT through MAPPINGS — Codex has no OpenClaw provider slug. SINGLE SOURCE OF
# TRUTH shared by /api/subagent/switch (provider_routes), the brain-tool path
# (app_control._switch_subagent), the worker selector
# (init._select_subagent_worker_kind) and the worker-env builder, so the
# acceptance set can never drift across sites (BUG-008 class). The canonical
# persisted value is "openai-codex"; "chatgpt" is an accepted alias.
CODEX_SUBAGENT_SLUGS: Final[frozenset[str]] = frozenset({"openai-codex", "chatgpt"})
CODEX_SUBAGENT_CANONICAL: Final[str] = "openai-codex"


@dataclass(frozen=True, slots=True)
class ProviderMapping:
    """One row of the AD-6 mapping table.

    Attributes:
        jarvis: Provider slug as used in `jarvis.toml [brain.providers.<slug>]`.
        openclaw: Provider slug as OpenClaw expects it in the
            `--model <slug>/<model>` CLI argument (see `openclaw models list --all`).
        env_var: Primary ENV var that OpenClaw reads for auth.
        env_fallback: Optional fallback ENV var. OpenClaw falls back to this
            when `env_var` is not set (e.g. `GEMINI_API_KEY` -> `GOOGLE_API_KEY`).
            The bridge should set both when available — guards against OpenClaw drift.
    """

    jarvis: str
    openclaw: str
    env_var: str
    env_fallback: str | None = None


# Single source of truth for the mapping. All derived dicts are generated
# from this — drift between forward/reverse/env-var is impossible.
#
# Quelle: docs/openclaw-bridge.md Section 2 "Amendment AD-6" (2026-05-09).
# 2026-05-16 — claude-api migrated from OpenClaw's "anthropic" provider
# (Messages-API path, requires paid Anthropic API key + extra-usage credits)
# to "claude-cli" provider (OAuth path that reads ~/.claude/.credentials.json
# directly, works under a Claude Max subscription with no extra-usage charge).
# Live verified: "anthropic/claude-sonnet-4-6" returns 400 "out of extra
# usage" even with the OAuth bearer token in ANTHROPIC_API_KEY; "claude-cli"
# routes through the Claude Max plan's included usage. ENV-var primary is
# ANTHROPIC_OAUTH_TOKEN (OpenClaw's preferred OAuth name) with
# ANTHROPIC_API_KEY as fallback for back-compat. Reapplied 2026-05-17 after
# a Drift-Guard / parallel-pull rolled back the original commit.
MAPPINGS: Final[tuple[ProviderMapping, ...]] = (
    ProviderMapping("gemini", "google", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ProviderMapping(
        "claude-api", "claude-cli",
        "ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
    ),
    ProviderMapping("openai", "openai", "OPENAI_API_KEY"),
    ProviderMapping("openrouter", "openrouter", "OPENROUTER_API_KEY"),
    ProviderMapping("grok", "xai", "XAI_API_KEY", "GROK_API_KEY"),
)


JARVIS_TO_OPENCLAW: Final[dict[str, str]] = {m.jarvis: m.openclaw for m in MAPPINGS}
OPENCLAW_TO_JARVIS: Final[dict[str, str]] = {m.openclaw: m.jarvis for m in MAPPINGS}
_BY_JARVIS: Final[dict[str, ProviderMapping]] = {m.jarvis: m for m in MAPPINGS}


class UnknownJarvisProviderError(ValueError):
    """Personal-Jarvis provider has no OpenClaw mapping.

    The bridge must not guess — when switching providers in `jarvis.toml` this
    table must be deliberately extended so that the spike finding (B-2) is preserved.
    """


class UnknownOpenclawProviderError(ValueError):
    """OpenClaw-Provider hat kein Personal-Jarvis-Mapping (Reverse-Richtung).

    Genutzt fuer Telemetrie/Voice-Readback wenn die Bridge nur den OpenClaw-Slug
    aus dem JSON-Output (`meta.agentMeta.provider`) zurueckbekommt.
    """


def to_provider_slug(jarvis_provider: str) -> str:
    """Personal-Jarvis-Slug zu OpenClaw-Slug.

    Beispiel:
        >>> to_provider_slug("gemini")
        'google'

    Raises:
        UnknownJarvisProviderError: Wenn `jarvis_provider` nicht in MAPPINGS ist.
    """
    try:
        return JARVIS_TO_OPENCLAW[jarvis_provider]
    except KeyError:
        known = ", ".join(sorted(JARVIS_TO_OPENCLAW))
        raise UnknownJarvisProviderError(
            f"No OpenClaw mapping for Personal-Jarvis provider {jarvis_provider!r}. "
            f"Known: {known}. Extend jarvis.missions.worker_runtime.provider_map.MAPPINGS "
            f"and update docs/openclaw-bridge.md AD-6 Amendment table."
        ) from None


def to_jarvis_slug(openclaw_provider: str) -> str:
    """OpenClaw-Slug zu Personal-Jarvis-Slug (Reverse-Mapping fuer Telemetrie).

    Beispiel:
        >>> to_jarvis_slug("xai")
        'grok'

    Raises:
        UnknownOpenclawProviderError: Wenn `openclaw_provider` nicht in MAPPINGS ist.
    """
    try:
        return OPENCLAW_TO_JARVIS[openclaw_provider]
    except KeyError:
        known = ", ".join(sorted(OPENCLAW_TO_JARVIS))
        raise UnknownOpenclawProviderError(
            f"No Personal-Jarvis mapping for OpenClaw provider {openclaw_provider!r}. "
            f"Known: {known}."
        ) from None


def env_vars_for(jarvis_provider: str) -> tuple[str, ...]:
    """ENV-Var-Namen die OpenClaw fuer diesen Provider beim Subprocess-Spawn liest.

    Gibt das Tuple (primary, fallback) bzw. (primary,) zurueck — die Bridge soll
    **beide** setzen wenn ein Fallback existiert, damit OpenClaw-interner Drift
    (Provider liest plotzlich nur noch eine Var) nicht zum Auth-Fail fuehrt.

    Beispiel:
        >>> env_vars_for("gemini")
        ('GEMINI_API_KEY', 'GOOGLE_API_KEY')
        >>> env_vars_for("openai")
        ('OPENAI_API_KEY',)

    Raises:
        UnknownJarvisProviderError: Wenn `jarvis_provider` nicht in MAPPINGS ist.
    """
    try:
        mapping = _BY_JARVIS[jarvis_provider]
    except KeyError:
        known = ", ".join(sorted(_BY_JARVIS))
        raise UnknownJarvisProviderError(
            f"No ENV-Var mapping for {jarvis_provider!r}. Known: {known}."
        ) from None
    if mapping.env_fallback is None:
        return (mapping.env_var,)
    return (mapping.env_var, mapping.env_fallback)


def canonical_subagent_provider(raw_provider: str | None) -> str | None:
    """Resolve the active *subagent* brain provider, for DISPLAY purposes.

    The Heavy-Task subagent runs on ``[brain.sub_jarvis].provider`` — **NOT**
    on ``brain.primary`` (which is only the lightweight router brain). This
    normalizes the configured value to the canonical brain-provider slug used
    in :data:`MAPPINGS`, so the API-Keys UI marks the brain that *actually*
    executes heavy tasks as active.

    Mirrors the worker routing in ``jarvis/missions/init.py::_worker_factory``
    so the displayed brain never drifts from the worker that runs:

      * ``"openclaw-claude"`` runs the OpenClaw subprocess but is still the
        Claude brain -> normalize to ``"claude-api"`` for display.
      * all other slugs pass through (lower-cased, stripped).

    Note: ``"chatgpt"`` / ``"openai-codex"`` (the Codex/ChatGPT-OAuth route)
    pass through unchanged and intentionally do **not** match a MAPPINGS row —
    they have no OpenClaw provider slug.

    Args:
        raw_provider: the raw ``[brain.sub_jarvis].provider`` value, or None.

    Returns:
        The canonical brain-provider slug, or ``None`` when no subagent
        provider is configured (the worker then falls back to its default
        OpenClaw chain — the caller decides what to show in that case).
    """
    if not raw_provider:
        return None
    provider = str(raw_provider).strip().lower()
    if not provider:
        return None
    if provider == "openclaw-claude":
        return "claude-api"
    return provider


def validate_configured_providers(configured: Iterable[str]) -> list[str]:
    """Gibt die Liste der konfigurierten Provider zurueck die KEIN Mapping haben.

    Verwendet von der Bridge beim Boot um fruehzeitig zu warnen wenn ein in
    `jarvis.toml [brain.providers.*]` registrierter Provider in OpenClaw nicht
    nutzbar waere. Leere Liste = alles okay.

    Beispiel:
        >>> validate_configured_providers(["gemini", "openai", "ollama-local"])
        ['ollama-local']
        >>> validate_configured_providers(["gemini"])
        []

    Idempotent + Order-stabil (sortiert alphabetisch).
    """
    return sorted(p for p in configured if p not in JARVIS_TO_OPENCLAW)
