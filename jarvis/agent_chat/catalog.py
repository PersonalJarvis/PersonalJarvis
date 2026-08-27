"""The provider rows the agent-chat composer can pick from.

Two kinds of row. The **coding CLIs** are the Agentic IDE's own registry
(``jarvis.workspace.agents`` — Claude Code, Codex, OpenCode, Kimi Code, GLM
Coding Plan, Grok Build, Antigravity, DeepSeek Harness), one row per entry
that :mod:`jarvis.agent_chat.runner_cli` drives without a terminal. Each row
names its registry key in ``agent``, and :func:`rows_for` drops a row whose
entry the IDE no longer has, so the chat's picker and the IDE's pane picker
offer the same CLIs (maintainer, 2026-08-27: the CLIs connected in the
Agentic IDE, not the sub-agent missions' provider map — and every one of
them drawn as a chat). The **API rows** are the provider families the
API-Keys "Agents" section shows. Every row is listed whether or not a
credential is saved: a provider without one is shown disabled with a
"connect" hint that leads to the API-Keys page, so the person sees what they
*could* use (maintainer, 2026-08-23: all agents, not only the active one).

Two kinds of runner sit behind the rows:

``api``
    The provider's own chat API, driven in-process in a tool loop
    (:mod:`jarvis.agent_chat.runner_api`). Model lists come live from the
    provider catalog (``GET /api/providers/{id}/models``).

``claude-cli`` … ``dsh-cli`` — one runner per coding CLI (``runner_cli._PLANNERS``)
    A vendor CLI driven non-interactively (:mod:`jarvis.agent_chat.runner_cli`).
    The CLI brings its own tools and permissions; the model list is the
    CLI's own — read live where the CLI publishes one (``agy models``,
    ``opencode models``, Codex's ``models_cache.json``), else the curated
    fallback here — and ``""`` means "the CLI's default".

``brain``
    Jarvis' own harness — ``BrainManager.generate`` with a per-turn pick of
    provider, model and effort (:mod:`jarvis.agent_chat.runner_brain`). Never
    a row's static runner: the service chooses it for every ``api`` row on
    the Jarvis surface (the front page's chat), where the typed turn is
    Jarvis rather than a coding agent.

``claude-api`` is the one dual row: on a surface with CLI seats, a Claude
subscription login makes the ``claude`` CLI run the session (Claude Code
proper — tools, skills, the Max plan's included usage); otherwise the
Anthropic API answers, through the API runner or the brain one. The route
decides per request by probing; this module only carries the static shape.

Not every row reaches every surface: a surface whose kit has no CLI seats
(``SurfaceKit.cli_seats``, the front page's chat) is offered only the rows
with a brain plugin behind them — :func:`rows_for` is the one place that
narrowing happens, and it gates on the capability, never on a provider name
(AP-21).

Presentation data only — nothing here decides behaviour (AP-21); the rows
exist so the picker can show a provider before a key is typed.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

from jarvis.agent_chat.effort import default_effort, effort_levels
from jarvis.brain.model_catalog import CURATED_MODELS

Runner = Literal[
    "api",
    "brain",
    "claude-cli",
    "codex-cli",
    "agy-cli",
    "grok-cli",
    "opencode-cli",
    "kimi-cli",
    "glm-cli",
    "dsh-cli",
]


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
    #: The Agentic IDE's registry key for the CLI this row runs
    #: (``jarvis.workspace.agents``) — ``""`` on an API row. The one link
    #: between the chat's picker and the IDE's: :func:`rows_for` offers a CLI
    #: row only while the IDE still has that entry, and the UI draws the row
    #: with the same mark the IDE's pane picker uses.
    agent: str = ""

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
            "agent": self.agent,
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


# Order = the order the picker shows. The coding CLIs first, in the Agentic
# IDE registry's own order (``jarvis.workspace.agents._AGENTS``) so the two
# pickers read alike, then the API families, then the local servers.
PROVIDER_ROWS: Final[tuple[ProviderRow, ...]] = (
    ProviderRow(
        id="claude-api",
        label="Anthropic Claude",
        family="claude",
        runner="claude-cli",
        models_source="curated",
        curated_models=_curated("claude-api"),
        default_model="",
        native_resume=True,
        agent="claude",
    ),
    ProviderRow(
        id="openai-codex",
        label="OpenAI Codex",
        family="openai",
        runner="codex-cli",
        models_source="curated",
        # Codex's OWN catalog, not the OpenAI API's: the route replaces this
        # with the account's live list from ``models_cache.json``.
        curated_models=CODEX_FALLBACK_MODELS,
        default_model="",
        native_resume=True,
        agent="codex",
    ),
    ProviderRow(
        id="opencode",
        label="OpenCode",
        family="opencode",
        runner="opencode-cli",
        models_source="curated",
        # OpenCode's ids are ``provider/model`` and depend on which providers
        # the person configured, so nothing is shipped: the route replaces
        # this with the live ``opencode models`` list.
        default_model="",
        native_resume=True,
        agent="opencode",
    ),
    ProviderRow(
        id="kimi",
        label="Kimi Code",
        family="kimi",
        runner="kimi-cli",
        models_source="curated",
        # Model aliases live in the person's own config.toml and the CLI
        # publishes no list — the pick is whatever they named there.
        default_model="",
        native_resume=True,
        agent="kimi",
    ),
    ProviderRow(
        id="glm",
        label="GLM Coding Plan",
        family="glm",
        runner="glm-cli",
        models_source="curated",
        # Claude Code against Z.ai's endpoint, which maps Claude Code's model
        # aliases onto GLM models itself (``glm_spawn_env`` in
        # ``jarvis.workspace.agents``); this app ships no list of Z.ai's ids
        # on purpose — see ``_GLM_PICKS`` there.
        default_model="",
        native_resume=True,
        agent="glm",
    ),
    ProviderRow(
        id="grok-build",
        label="Grok Build",
        family="xai",
        runner="grok-cli",
        models_source="curated",
        curated_models=_curated("grok"),
        default_model="",
        agent="grok-build",
    ),
    ProviderRow(
        id="antigravity",
        label="Antigravity",
        family="antigravity",
        runner="agy-cli",
        models_source="curated",
        # agy's OWN ids (not the Gemini API's): the route replaces this with
        # the live ``agy models`` list when the binary answers.
        curated_models=tuple(
            CuratedModel(mid, label, efforts) for mid, label, efforts in _agy_fallback_models()
        ),
        default_model="",
        native_resume=True,
        agent="antigravity",
    ),
    ProviderRow(
        id="deepseek-harness",
        label="DeepSeek Harness",
        family="deepseek",
        runner="dsh-cli",
        models_source="curated",
        # The headless profile answers with its final message only — no
        # model flag, no session to resume, no tool stream.
        default_model="",
        agent="deepseek-harness",
    ),
    ProviderRow(id="openai", label="OpenAI", family="openai", runner="api", models_source="live"),
    ProviderRow(
        id="gemini", label="Google Gemini", family="gemini", runner="api", models_source="live"
    ),
    ProviderRow(id="grok", label="xAI Grok", family="xai", runner="api", models_source="live"),
    ProviderRow(
        id="openrouter",
        label="OpenRouter",
        family="openrouter",
        runner="api",
        models_source="live",
    ),
    ProviderRow(
        id="nvidia", label="NVIDIA NIM", family="nvidia", runner="api", models_source="live"
    ),
    ProviderRow(
        id="vertex",
        label="Google Vertex AI",
        family="google-cloud",
        runner="api",
        models_source="curated",
        curated_models=_curated("gemini"),
    ),
    ProviderRow(
        id="ollama",
        label="Ollama",
        family="ollama",
        runner="api",
        models_source="live",
        keyless=True,
    ),
    ProviderRow(
        id="local-openai",
        label="Local OpenAI-compatible",
        family="local",
        runner="api",
        models_source="live",
        keyless=True,
    ),
)

_BY_ID: Final[dict[str, ProviderRow]] = {row.id: row for row in PROVIDER_ROWS}


def provider_row(provider_id: str) -> ProviderRow | None:
    return _BY_ID.get((provider_id or "").strip().lower())


def known_provider_ids() -> tuple[str, ...]:
    return tuple(row.id for row in PROVIDER_ROWS)


def rows_for(surface: str) -> tuple[ProviderRow, ...]:
    """The rows ``surface`` may pick from.

    A surface with CLI seats sees every API row and every CLI row whose entry
    the Agentic IDE still registers — the registry is open, so the chat asks
    it rather than assuming the table above is the whole truth. One without
    CLI seats (the front page's chat) sees only the rows a brain plugin can
    drive — the providers whose own API answers behind a key, so the turn
    runs through Jarvis' harness and its model pick is one ``TurnOverride``
    rather than a vendor process. Decided by asking the runner whether it can
    drive the provider at all, never from a list of names (AP-21).
    """
    from jarvis.agent_chat.surface_kits import kit_for

    if kit_for(surface).cli_seats:
        return tuple(row for row in PROVIDER_ROWS if not row.agent or _ide_has(row.agent))
    from jarvis.agent_chat.runner_api import supports_api_runner

    return tuple(row for row in PROVIDER_ROWS if supports_api_runner(row.id))


def cli_rows() -> tuple[ProviderRow, ...]:
    """The rows that run a coding CLI — the chat's half of the IDE's registry."""
    return tuple(row for row in PROVIDER_ROWS if row.agent)


def _ide_has(agent: str) -> bool:
    """Whether the Agentic IDE registers ``agent`` right now.

    A registry that cannot be imported or asked (a stripped install, a broken
    custom-CLI store) answers yes: the row then says what is wrong the moment
    a turn tries to run, which beats a picker that silently lost a CLI.
    """
    try:
        from jarvis.workspace.agents import get_agent

        return get_agent(agent) is not None
    except Exception:  # noqa: BLE001 — see docstring
        return True


def offers(surface: str, provider_id: str) -> bool:
    """Whether ``provider_id`` is one of ``surface``'s rows."""
    pid = (provider_id or "").strip().lower()
    return any(row.id == pid for row in rows_for(surface))


#: A CLI-only row and the API row of the same brand. A chat that was seated
#: on a vendor CLI before the front page dropped its CLI seats moves across
#: this map instead of being stranded on a provider its picker no longer
#: lists — same brand, same key page, now the provider's own endpoint.
API_TWIN: Final[dict[str, str]] = {
    "openai-codex": "openai",
    "antigravity": "gemini",
    "grok-build": "grok",
}


def api_seat(provider: str, model: str) -> tuple[str, str]:
    """Where a CLI-seated ``(provider, model)`` lands on an API-only surface.

    The provider moves to its API twin; the model is kept only when the
    provider's own catalog knows it. A CLI's model ids are the CLI's own —
    ``opusplan``, ``best``, ``claude-opus-5[1m]``, ``gpt-5.6-sol`` as Codex
    spells it — and sending one to the provider's endpoint is an error, not
    a near miss. An unknown id therefore becomes ``""``: the provider's
    default, which the picker shows as "Default" and the person can change
    in one click.
    """
    pid = (provider or "").strip().lower()
    seat = API_TWIN.get(pid, pid)
    known = {m.id for m in CURATED_MODELS.get(seat, ())}
    return seat, (model if model in known else "")
