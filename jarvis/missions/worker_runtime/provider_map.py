"""Personal-Jarvis provider-slug to worker-harness provider-slug mapping.

Pure data module without IO — subprocess spawn mechanics live in the harness
plugin (`jarvis/plugins/harness/openclaw.py`).

Bridge mechanics (summary for future readers):
    1. cfg.brain.primary  -> jarvis provider slug (e.g. "gemini")
    2. to_worker_slug() -> worker harness slug (e.g. "google")
    3. cfg.brain.providers.<jarvis>.deep_model -> model ID (e.g. "gemini-3.1-pro-preview")
    4. CLI argument: --model "google/gemini-3.1-pro-preview"
    5. env_vars_for() -> ENV vars that the worker harness reads at spawn time (primary +
       fallback; both are set so that harness provider drift does not break auth).

Jarvis-Agent credentials use dedicated keyring slots. The supervisor resolves
the scoped slot and translates it to the standard provider ENV name only at the
worker boundary. Generic Brain credentials remain a compatibility fallback for
upgraded installations; Realtime-only credentials are never candidates.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

__all__ = [
    "ProviderMapping",
    "MAPPINGS",
    "JARVIS_TO_WORKER_SLUG",
    "WORKER_SLUG_TO_JARVIS",
    "NoWorkerSlugMappingError",
    "NoJarvisFromWorkerSlugError",
    "to_worker_slug",
    "to_jarvis_from_worker_slug",
    "env_vars_for",
    "validate_configured_providers",
    "canonical_worker_provider",
    "CODEX_SUBAGENT_SLUGS",
    "CODEX_SUBAGENT_CANONICAL",
    "ANTIGRAVITY_SUBAGENT_SLUGS",
    "ANTIGRAVITY_SUBAGENT_CANONICAL",
]


# Subagent slugs that route to the DIRECT Codex CLI worker (CodexDirectWorker),
# NOT through MAPPINGS — Codex has no worker-harness provider slug. SINGLE SOURCE
# OF TRUTH shared by /api/jarvis-agent/switch (provider_routes), the brain-tool
# path (app_control._switch_subagent), the worker selector
# (init._select_subagent_worker_kind) and the worker-env builder, so the
# acceptance set can never drift across sites (BUG-008 class). The canonical
# persisted value is "openai-codex"; "chatgpt" and the bare spoken word "codex"
# are accepted aliases (the voice gate emits "codex"; forensic 2026-06-27 a
# "set the subagent to codex" answered "codex is not a valid provider" because
# the bare alias was missing here).
CODEX_SUBAGENT_SLUGS: Final[frozenset[str]] = frozenset(
    {"openai-codex", "chatgpt", "codex"}
)
CODEX_SUBAGENT_CANONICAL: Final[str] = "openai-codex"


# Subagent slugs that route to the OAuth Google-CLI worker (the GeminiWorker
# driven over the "Sign in with Google" login, billed against the Google
# subscription — no API key), NOT through MAPPINGS (no worker-harness provider
# slug). Mirror of CODEX_SUBAGENT_SLUGS so the acceptance set is a SINGLE
# SOURCE OF TRUTH shared by /api/jarvis-agent/switch, the brain-tool path, and
# the worker selector (init._select_subagent_worker_kind), and can never drift
# (BUG-008).
ANTIGRAVITY_SUBAGENT_SLUGS: Final[frozenset[str]] = frozenset({"antigravity"})
ANTIGRAVITY_SUBAGENT_CANONICAL: Final[str] = "antigravity"


@dataclass(frozen=True, slots=True)
class ProviderMapping:
    """One row of the AD-6 mapping table.

    Attributes:
        jarvis: Provider slug as used in `jarvis.toml [brain.providers.<slug>]`.
        worker_slug: Provider slug as the worker harness expects it in the
            `--model <slug>/<model>` CLI argument.
        env_var: Primary ENV var that the worker harness reads for auth.
        env_fallback: Optional fallback ENV var. The harness falls back to this
            when `env_var` is not set (e.g. `GEMINI_API_KEY` -> `GOOGLE_API_KEY`).
            The bridge should set both when available — guards against harness drift.
    """

    jarvis: str
    worker_slug: str
    env_var: str
    env_fallback: str | None = None


# Single source of truth for the mapping. All derived dicts are generated
# from this — drift between forward/reverse/env-var is impossible.
#
# Source: docs/jarvis-agents-bridge.md Section 2 "Amendment AD-6" (2026-05-09).
# 2026-05-16 — claude-api migrated from the "anthropic" provider
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
    # Grok runs through the in-process ApiAgentWorker. The xAI worker slug is a
    # stable display/reverse-mapping identity; no external CLI is spawned.
    ProviderMapping("grok", "xai", "XAI_API_KEY", "GROK_API_KEY"),
    # NVIDIA NIM: an OpenAI-compatible API provider. Like openai/openrouter it
    # runs through the in-process ApiAgentWorker (not the OpenClaw CLI harness),
    # so ``worker_slug`` is only a placeholder — this row exists so nvidia is a
    # selectable subagent in the API-Keys "Subagents" tab and env/slug lookups
    # stay consistent.
    ProviderMapping("nvidia", "nvidia", "NVIDIA_API_KEY"),
)


JARVIS_TO_WORKER_SLUG: Final[dict[str, str]] = {m.jarvis: m.worker_slug for m in MAPPINGS}
WORKER_SLUG_TO_JARVIS: Final[dict[str, str]] = {m.worker_slug: m.jarvis for m in MAPPINGS}
_BY_JARVIS: Final[dict[str, ProviderMapping]] = {m.jarvis: m for m in MAPPINGS}


class NoWorkerSlugMappingError(ValueError):
    """Personal-Jarvis provider has no worker-harness slug mapping.

    The bridge must not guess — when switching providers in `jarvis.toml` this
    table must be deliberately extended so that the spike finding (B-2) is preserved.
    """


class NoJarvisFromWorkerSlugError(ValueError):
    """Worker-harness provider slug has no Personal-Jarvis mapping (reverse direction).

    Used for telemetry/voice readback when the bridge receives only the worker
    slug from the JSON output (`meta.agentMeta.provider`).
    """


def to_worker_slug(jarvis_provider: str) -> str:
    """Personal-Jarvis-Slug to worker-harness slug.

    Example:
        >>> to_worker_slug("gemini")
        'google'

    Raises:
        NoWorkerSlugMappingError: When `jarvis_provider` is not in MAPPINGS.
    """
    try:
        return JARVIS_TO_WORKER_SLUG[jarvis_provider]
    except KeyError:
        known = ", ".join(sorted(JARVIS_TO_WORKER_SLUG))
        raise NoWorkerSlugMappingError(
            f"No worker-harness mapping for Personal-Jarvis provider {jarvis_provider!r}. "
            f"Known: {known}. Extend jarvis.missions.worker_runtime.provider_map.MAPPINGS."
        ) from None


def to_jarvis_from_worker_slug(worker_provider: str) -> str:
    """Worker-harness slug to Personal-Jarvis slug (reverse mapping for telemetry).

    Example:
        >>> to_jarvis_from_worker_slug("google")
        'gemini'

    Raises:
        NoJarvisFromWorkerSlugError: When `worker_provider` is not in MAPPINGS.
    """
    try:
        return WORKER_SLUG_TO_JARVIS[worker_provider]
    except KeyError:
        known = ", ".join(sorted(WORKER_SLUG_TO_JARVIS))
        raise NoJarvisFromWorkerSlugError(
            f"No Personal-Jarvis mapping for worker provider {worker_provider!r}. "
            f"Known: {known}."
        ) from None


def env_vars_for(jarvis_provider: str) -> tuple[str, ...]:
    """ENV var names the worker harness reads for this provider at subprocess spawn time.

    Returns the tuple (primary, fallback) or (primary,) — the bridge should set
    **both** when a fallback exists, so that harness-internal drift (provider
    starts reading only one var) does not break auth.

    Example:
        >>> env_vars_for("gemini")
        ('GEMINI_API_KEY', 'GOOGLE_API_KEY')
        >>> env_vars_for("openai")
        ('OPENAI_API_KEY',)

    Raises:
        NoWorkerSlugMappingError: When `jarvis_provider` is not in MAPPINGS.
    """
    try:
        mapping = _BY_JARVIS[jarvis_provider]
    except KeyError:
        known = ", ".join(sorted(_BY_JARVIS))
        raise NoWorkerSlugMappingError(
            f"No ENV-Var mapping for {jarvis_provider!r}. Known: {known}."
        ) from None
    if mapping.env_fallback is None:
        return (mapping.env_var,)
    return (mapping.env_var, mapping.env_fallback)


def canonical_worker_provider(raw_provider: str | None) -> str | None:
    """Resolve the active Jarvis-Agent worker brain provider, for DISPLAY purposes.

    The Jarvis-Agent worker runs on ``[brain.sub_jarvis].provider`` — **NOT**
    on ``brain.primary`` (which is only the lightweight router brain). This
    normalizes the configured value to the canonical brain-provider slug used
    in :data:`MAPPINGS`, so the API-Keys UI marks the brain that *actually*
    executes heavy tasks as active.

    Mirrors the worker routing in ``jarvis/missions/init.py::_worker_factory``
    so the displayed brain never drifts from the worker that runs:

      * ``"openclaw-claude"`` runs the worker harness subprocess but is still
        the Claude brain -> normalize to ``"claude-api"`` for display.
      * all other slugs pass through (lower-cased, stripped).

    Note: ``"chatgpt"`` / ``"openai-codex"`` (the Codex/ChatGPT-OAuth route)
    pass through unchanged and intentionally do **not** match a MAPPINGS row —
    they have no worker-harness provider slug.

    Args:
        raw_provider: the raw ``[brain.sub_jarvis].provider`` value, or None.

    Returns:
        The canonical brain-provider slug, or ``None`` when no worker provider
        is configured (the worker then falls back to its default chain — the
        caller decides what to show in that case).
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
    """Return configured providers that have no worker-harness mapping.

    Used by the bridge at boot to warn early when a provider registered in
    `jarvis.toml [brain.providers.*]` would not be usable in the worker
    harness. Empty list = all good.

    Example:
        >>> validate_configured_providers(["gemini", "openai", "ollama-local"])
        ['ollama-local']
        >>> validate_configured_providers(["gemini"])
        []

    Idempotent + order-stable (sorted alphabetically).
    """
    return sorted(p for p in configured if p not in JARVIS_TO_WORKER_SLUG)
