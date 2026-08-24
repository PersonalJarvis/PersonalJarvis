"""The normalised line item every cost source is reduced to.

Two vocabularies carry the whole section:

**Surface** — which part of the app spent it (voice, agent chat, a mission).
**Role** — what the model was doing in that surface. A realtime voice turn
that delegates one tool call to a text model spends on TWO roles at once, and
they bill at wildly different rates (Live-API audio tokens run 4-40x the text
rate of the same family), so a report that only knows "provider" hides the
single most useful answer: *what kind of work* the money went to.

Pricing honesty is the other half. A recorded ``cost_usd`` of 0.0 means one of
four very different things, and the UI must be able to tell them apart:

``recorded``  the source priced the call itself (realtime audio rates included)
``derived``   the source stored 0.0, we re-priced it from the rate tables
``free``      a local engine or an explicitly free model — 0.0 is the truth
``unknown``   tokens were spent, no rate exists anywhere — 0.0 is a GAP
``subscription`` a seat paid monthly — the amount is what the same work
              would have cost through the API, not money that moved

Only ``unknown`` is a hole in the accounting, and it is reported as tokens,
never quietly folded into the total as if it were free. ``subscription`` is
the opposite kind of honesty: the number is real arithmetic on real tokens,
but no invoice carries it, so it is named rather than mixed in silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

SURFACE_VOICE = "voice"
SURFACE_AGENT_CHAT = "agent-chat"
SURFACE_MISSION = "mission"
#: The speech layer. Its own surface because it does not bill by token:
#: hearing costs audio seconds, speaking costs characters.
SURFACE_JARVIS_VOICE = "jarvis-voice"
#: Coding agents driven by a vendor CLI, indexed from their session logs.
SURFACE_AGENTIC_IDE = "agentic-ide"

ROLE_REALTIME = "realtime"
"""Speech-to-speech model — audio tokens, billed per Live/Realtime API rates."""

ROLE_TOOL = "tool"
"""Text model a realtime turn delegated a tool call to (``prefer_tool_model``)."""

ROLE_PIPELINE = "pipeline"
"""Brain of a classic STT → brain → TTS turn (the ``fast`` / ``deep`` tiers)."""

ROLE_AGENT = "agent"
"""A coding-agent turn (Claude Code, Codex, …) run from the chat surface."""

ROLE_WORKER = "worker"
ROLE_STT = "stt"
ROLE_TTS = "tts"
"""An autonomous mission worker."""

ROLES: tuple[str, ...] = (ROLE_REALTIME, ROLE_TOOL, ROLE_PIPELINE, ROLE_AGENT, ROLE_WORKER)
SURFACES: tuple[str, ...] = (SURFACE_VOICE, SURFACE_AGENT_CHAT, SURFACE_MISSION)

PriceSource = Literal["recorded", "derived", "free", "unknown", "subscription"]

# Providers that run on the user's own hardware: 0.00 is the correct price,
# not a missing one. Matched on the provider id as the stores record it.
LOCAL_PROVIDERS: frozenset[str] = frozenset(
    {
        "local",
        "local-realtime",
        "ollama",
        "llamacpp",
        "llama-cpp",
        "lmstudio",
        "vllm",
        "whisper-local",
        # Any OpenAI-compatible server the user points at themselves.
        "local-openai",
    }
)

# Subscription-billed runners: the seat is paid monthly, per-call metering
# does not apply. Their tokens still count, their marginal cost does not.
SUBSCRIPTION_PROVIDERS: frozenset[str] = frozenset(
    {
        "codex-subscription-realtime",
        "codex-subscription",
        "claude-subscription",
    }
)

# The other half of the same question, and the reliable one. A provider id
# cannot answer it: ``claude-api`` is one row for two billing worlds — the
# Claude Code CLI on a Max seat when it is installed, the Anthropic API
# otherwise (:mod:`jarvis.agent_chat.catalog`). Only the runner that actually
# answered the turn knows, and it is recorded on every ``turn_started``.
SUBSCRIPTION_RUNNERS: frozenset[str] = frozenset(
    {
        "claude-cli",
        "codex-cli",
        "agy-cli",
        "grok-cli",
    }
)


@dataclass(frozen=True, slots=True)
class CostEntry:
    """One priced unit of spend, whatever produced it.

    ``tokens_cached`` is counted separately because cache reads are billed at
    a fraction of the input rate; a source that does not report them leaves
    it at 0 rather than folding them into ``tokens_in``.
    """

    ts_ms: int
    surface: str
    role: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    cost_usd: float
    price_source: PriceSource
    ref_id: str
    """Session / mission id — the thing a user can go look at."""
    label: str
    """Short human-readable handle for ``ref_id`` (session title, prompt head)."""

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out + self.tokens_cached

    @property
    def is_gap(self) -> bool:
        """True when tokens were spent at a price nobody knows."""
        return self.price_source == "unknown" and self.tokens_total > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_ms": self.ts_ms,
            "surface": self.surface,
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_cached": self.tokens_cached,
            "tokens_total": self.tokens_total,
            "cost_usd": round(self.cost_usd, 6),
            "price_source": self.price_source,
            "ref_id": self.ref_id,
            "label": self.label,
        }


def price_entry(
    *,
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    recorded_usd: float,
    subscription: bool = False,
) -> tuple[float, PriceSource]:
    """Settle what a call actually cost and how confident that number is.

    A recorded price always wins — the source that made the call knew the
    modality (audio vs text) and we do not. Everything else is re-derived from
    the shared rate tables so a model the price table gained later stops
    showing up as free.

    ``subscription`` says a monthly seat answered the turn. The amount is still
    the API-equivalent — a seat that reports one (Claude Code does) is quoting
    exactly that, and a seat that reports nothing gets the rate tables applied
    like any other call. What changes is the label, so a report can show the
    worth of the work without claiming the money moved. A seat whose model has
    no rate anywhere is still a gap: the tokens are real and unpriced.
    """
    tokens = max(0, tokens_in) + max(0, tokens_out)
    if subscription and tokens > 0:
        if recorded_usd > 0:
            return float(recorded_usd), "subscription"
        # Lazy, like the branch below: a report of nothing but priced seats
        # never has to load the catalogue.
        from jarvis.brain.cost import calculate_cost_usd, resolve_rates

        if model and resolve_rates(model) is not None:
            return calculate_cost_usd(model, tokens_in, tokens_out), "subscription"
        return 0.0, "unknown"

    if recorded_usd > 0:
        return float(recorded_usd), "recorded"

    if tokens <= 0:
        # Nothing was consumed — a 0.0 with no tokens is not a pricing gap.
        return 0.0, "recorded"

    key = (provider or "").strip().casefold()
    if key in LOCAL_PROVIDERS or key in SUBSCRIPTION_PROVIDERS:
        return 0.0, "free"
    if model.strip().casefold().endswith(":free"):
        return 0.0, "free"

    # Lazy import: jarvis.brain.cost pulls the catalog cache and is not worth
    # loading for a request that never reaches an unpriced row.
    from jarvis.brain.cost import calculate_cost_usd, resolve_rates

    if not model or resolve_rates(model) is None:
        return 0.0, "unknown"
    return calculate_cost_usd(model, tokens_in, tokens_out), "derived"
