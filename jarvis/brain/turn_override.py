"""A per-turn pick for ``BrainManager.generate``: which brain, with what hands.

The front page's typed chat is Jarvis with a keyboard — the same harness the
microphone drives — but the person picks the provider, the model and the
reasoning effort for THAT chat in its composer, and that pick must not move
the live brain the voice runs on. The first attempt at this (2026-08-24)
called ``BrainManager.switch`` and ``apply_provider_model`` per chat turn,
which changed the voice brain as a side effect and was reverted. This is the
replacement: a value object the caller hands to ``generate(turn_override=…)``,
carried in a ContextVar for the length of that one turn, read where the
manager builds its provider chain, its tool surface and its dispatcher —
and nowhere else. The manager's own state (``_active_name``, the provider
config, the dead-lists, the shared brain cache) is never written by it.

Import-light on purpose: ``jarvis.agent_chat`` builds these without pulling
the manager in, and the manager imports this module at the top level.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.protocols import ReasoningEffort, Tool


@dataclass(slots=True)
class TurnReceipt:
    """What the turn actually cost and who answered — filled by the manager.

    Mutable by design: the override is frozen, the receipt is its one output
    channel, so a caller reads the answer's provider, model and usage without
    guessing them from bus events another turn may have published.
    """

    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    finish_reason: str = ""

    def record(
        self,
        *,
        provider: str,
        model: str | None,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        finish_reason: str,
    ) -> None:
        self.provider = provider
        self.model = model or ""
        self.tokens_in = int(tokens_in)
        self.tokens_out = int(tokens_out)
        self.cost_usd = float(cost_usd)
        self.finish_reason = finish_reason

    def usage(self) -> dict[str, Any]:
        """The ``usage`` dict an agent-chat ``turn_finished`` event carries."""
        out: dict[str, Any] = {}
        if self.tokens_in:
            out["input_tokens"] = self.tokens_in
        if self.tokens_out:
            out["output_tokens"] = self.tokens_out
        if self.cost_usd:
            out["cost_usd"] = self.cost_usd
        return out


@dataclass(frozen=True, slots=True)
class TurnOverride:
    """The pick for one ``generate`` call.

    ``provider`` / ``model``
        The chain is exactly this pair — no cross-provider fallback: a person
        who picked a model must get that model or an honest error, never a
        silent stand-in. When the pick cannot call tools at all (a text-only
        brain), the manager's intelligent-router lead still runs in front of
        it, as it does for a tool-incapable voice brain.
    ``reasoning_effort``
        Forwarded onto every request of the turn; ``None`` keeps the
        provider's default.
    ``tools_extra``
        Merged INTO the turn's tool surface after every gate the manager
        applies (the folder tools of a chat, scoped to its working directory).
    ``tool_filter``
        Applied last; a plan-mode caller keeps the read-only tools only.
    ``tool_context``
        Extra keys for every tool's ``ExecutionContext.config`` this turn —
        the approval surface, the working directory, written delivery.
    ``credential_scope``
        Namespaces the brain instance in the manager's cache so the chat's
        client never shares a resolved credential with the voice brain.
    ``max_turns``
        A loop ceiling for the dispatcher; ``None`` keeps its default.
    ``allow_force_spawn``
        Whether the deterministic force-spawn heuristic may hand the turn to
        a background worker. A chat that IS the worker in its folder says no;
        an explicit "spawn an agent for this" still spawns either way.
    ``system_extra``
        A per-turn addendum to the system prompt, appended after the
        manager's own ``_system_prompt_extra``. Empty leaves the prompt
        byte-identical; a surface with its own briefing (the local-models
        setup assistant) sets it for its turns only.
    """

    provider: str
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    tools_extra: Mapping[str, Tool] = field(default_factory=dict)
    tool_filter: Callable[[dict[str, Tool]], dict[str, Tool]] | None = None
    tool_context: Mapping[str, Any] = field(default_factory=dict)
    credential_scope: str = "agent"
    max_turns: int | None = None
    allow_force_spawn: bool = False
    system_extra: str = ""
    receipt: TurnReceipt = field(default_factory=TurnReceipt)


__all__ = ["TurnOverride", "TurnReceipt"]
