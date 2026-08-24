"""The provider rows the chat composer can pick from.

The picker lists the providers Jarvis can THINK with — the brain-tier keys
from the API-Keys page. Picking one makes it the live brain for the turn
(:mod:`jarvis.agent_chat.runner_brain`), so what the row names is the model
behind the assistant, not a different assistant.

A subscription coding CLI (Codex, Antigravity, Grok Build) is deliberately
NOT here. Those are agent loops with their own tools and no chat API; the
brain cannot be switched to them at all (``SUBAGENT_ONLY_BRAIN_PROVIDERS``),
and a coding session belongs in the Agentic IDE. ``claude-api`` stays because
an Anthropic key IS a brain — the row is the API, not Claude Code.

Rows are shown whether or not a credential is saved: one without a key is
disabled with a "connect" hint that leads to the API-Keys page, so the person
sees what they *could* use (maintainer, 2026-08-23: all agents, not only the
active one).

Model lists come live from the provider catalog
(``GET /api/providers/{id}/models``) where the provider publishes one, else
from the curated list below; ``""`` means "the provider's default".

Presentation data only — nothing here decides behaviour (AP-21); the rows
exist so the picker can show a provider before a key is typed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

from jarvis.agent_chat.effort import default_effort, effort_levels
from jarvis.brain.model_catalog import CURATED_MODELS

#: One runner answers on this surface: Jarvis' brain. The string is what
#: ``turn_started`` reports and what the permission/effort ladders key on.
Runner = Literal["brain"]


@dataclass(frozen=True, slots=True)
class CuratedModel:
    id: str
    label: str
    #: The effort levels THIS model takes, when narrower than the provider's
    #: ladder (agy's Pro knows low/high, its Claude models none; Codex's 5.5
    #: stops at xhigh). ``None`` = the provider ladder applies unchanged.
    efforts: tuple[str, ...] | None = None
    #: A short note the picker shows as the hint ("retires 2026-08-31").
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "label": self.label}
        if self.efforts is not None:
            d["efforts"] = list(self.efforts)
        if self.note:
            d["note"] = self.note
        return d


@dataclass(frozen=True, slots=True)
class ProviderRow:
    """One row in the composer's provider column."""

    id: str
    label: str
    #: Brand family for the mark (ProviderLogo.providerFamily in the UI).
    family: str
    runner: Runner
    #: Where the model list comes from: "live" = the provider catalog route,
    #: "curated" = the list embedded below (CLI-backed providers).
    models_source: Literal["live", "curated"]
    curated_models: tuple[CuratedModel, ...] = field(default_factory=tuple)
    #: ``""`` = the runner's own default model.
    default_model: str = ""
    #: Runs on the person's own hardware — no credential to ask for.
    keyless: bool = False
    #: True when the CLI runner supports resuming a conversation natively
    #: (claude --resume, codex exec resume). Others get the transcript replayed.
    native_resume: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "family": self.family,
            "runner": self.runner,
            "models_source": self.models_source,
            "curated_models": [m.to_dict() for m in self.curated_models],
            "default_model": self.default_model,
            "keyless": self.keyless,
            "native_resume": self.native_resume,
            "effort_levels": list(effort_levels(self.id)),
            "default_effort": default_effort(self.id),
        }


def _curated(provider: str) -> tuple[CuratedModel, ...]:
    return tuple(CuratedModel(m.id, m.label) for m in CURATED_MODELS.get(provider, ()))


def _agy_fallback_models() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    from jarvis.agent_chat.runner_cli import AGY_FALLBACK_MODELS

    return AGY_FALLBACK_MODELS


#: What Claude Code's ``--model`` takes (claude 2.1.241): the current
#: families by full id, the two legacy families that still answer, and the
#: aliases people type. Per-model effort caps follow the docs (no xhigh on
#: 4.6; no effort at all on Haiku 4.5 and the 4.5 generation).
CLAUDE_CODE_MODELS: Final[tuple[CuratedModel, ...]] = (
    CuratedModel("claude-fable-5", "Claude Fable 5"),
    CuratedModel("claude-opus-5", "Claude Opus 5"),
    CuratedModel("claude-opus-5[1m]", "Claude Opus 5", note="1M context"),
    CuratedModel("claude-sonnet-5", "Claude Sonnet 5"),
    CuratedModel("claude-haiku-4-5-20251001", "Claude Haiku 4.5", efforts=()),
    CuratedModel("opusplan", "Opus plans, Sonnet builds", note="alias"),
    CuratedModel("best", "Best available", note="alias"),
    CuratedModel("claude-opus-4-8", "Claude Opus 4.8"),
    CuratedModel("claude-opus-4-7", "Claude Opus 4.7"),
    CuratedModel("claude-opus-4-6", "Claude Opus 4.6", efforts=("low", "medium", "high", "max")),
    CuratedModel(
        "claude-sonnet-4-6", "Claude Sonnet 4.6", efforts=("low", "medium", "high", "max")
    ),
    CuratedModel("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5", efforts=()),
    CuratedModel("claude-opus-4-5-20251101", "Claude Opus 4.5", efforts=()),
)

#: Codex's catalog as of codex 0.149 (the bundled list, ``visibility: list``),
#: for a box where ``models_cache.json`` cannot be read. The route prefers the
#: account's live list.
CODEX_FALLBACK_MODELS: Final[tuple[CuratedModel, ...]] = (
    CuratedModel("gpt-5.6-sol", "GPT-5.6 Sol", ("low", "medium", "high", "xhigh", "max", "ultra")),
    CuratedModel(
        "gpt-5.6-terra", "GPT-5.6 Terra", ("low", "medium", "high", "xhigh", "max", "ultra")
    ),
    CuratedModel("gpt-5.6-luna", "GPT-5.6 Luna", ("low", "medium", "high", "xhigh", "max")),
    CuratedModel("gpt-5.5", "GPT-5.5", ("low", "medium", "high", "xhigh")),
    CuratedModel("gpt-5.2", "GPT-5.2", ("low", "medium", "high", "xhigh")),
)


# Order = the order the picker shows. Subscription CLIs first (they are what
# "our own Claude Code" means for most people), then the API families, then
# the local servers.
PROVIDER_ROWS: Final[tuple[ProviderRow, ...]] = (
    ProviderRow(
        id="claude-api",
        label="Anthropic Claude",
        family="claude",
        runner="brain",
        models_source="curated",
        curated_models=_curated("claude-api"),
        default_model="",
        native_resume=True,
    ),
    ProviderRow(id="openai", label="OpenAI", family="openai", runner="brain", models_source="live"),
    ProviderRow(
        id="gemini", label="Google Gemini", family="gemini", runner="brain", models_source="live"
    ),
    ProviderRow(id="grok", label="xAI Grok", family="xai", runner="brain", models_source="live"),
    ProviderRow(
        id="openrouter",
        label="OpenRouter",
        family="openrouter",
        runner="brain",
        models_source="live",
    ),
    ProviderRow(
        id="nvidia", label="NVIDIA NIM", family="nvidia", runner="brain", models_source="live"
    ),
    ProviderRow(
        id="vertex",
        label="Google Vertex AI",
        family="google-cloud",
        runner="brain",
        models_source="curated",
        curated_models=_curated("gemini"),
    ),
    ProviderRow(
        id="ollama",
        label="Ollama",
        family="ollama",
        runner="brain",
        models_source="live",
        keyless=True,
    ),
    ProviderRow(
        id="local-openai",
        label="Local OpenAI-compatible",
        family="local",
        runner="brain",
        models_source="live",
        keyless=True,
    ),
)

_BY_ID: Final[dict[str, ProviderRow]] = {row.id: row for row in PROVIDER_ROWS}


def provider_row(provider_id: str) -> ProviderRow | None:
    return _BY_ID.get((provider_id or "").strip().lower())


def known_provider_ids() -> tuple[str, ...]:
    return tuple(row.id for row in PROVIDER_ROWS)
