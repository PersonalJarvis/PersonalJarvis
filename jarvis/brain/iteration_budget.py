"""Iteration budget: limits per conversation turn.

Two parallel limits:
- `max_turns`: number of allowed tool-use loops (prevents infinite loops). This
  is the hard iteration bound.
- `max_tokens_total`: ceiling on the tokens the loop *generates* (output),
  guarding against runaway generation.

`tokens_used` counts OUTPUT tokens only — NOT the re-sent prompt. The prompt
(system + tool schemas + the full conversation history) is re-sent on every
turn and grows with the conversation; counting it here let a single
large-context turn exhaust the whole loop budget and abort a pending tool call
before execution (live bug 2026-06-01: "Einen Moment." then silence in a long
voice session — an AD-OE6 silent drop). The re-sent prompt is bounded by
`max_turns` × the context window, so `max_turns` is the cost/loop guard; this
ceiling catches genuine runaway *generation*.

Both are *soft hints* — the API may exceed them by up to ~10% mid-callback. We
therefore combine a hard count with a soft check.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IterationBudget:
    """Tracks turns and generated tokens for a conversation loop."""
    max_turns: int = 15
    max_tokens_total: int = 50_000
    turns_used: int = 0
    tokens_used: int = 0           # OUTPUT tokens accumulated (runaway-generation signal)
    input_tokens_seen: int = 0     # telemetry only — re-sent prompt, not a budget input

    def record_turn(self, tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.turns_used += 1
        # Only generated (output) tokens count toward the ceiling — see module
        # docstring. The re-sent prompt is tracked separately for telemetry.
        self.tokens_used += int(tokens_out)
        self.input_tokens_seen += int(tokens_in)

    def exceeded(self) -> bool:
        return self.turns_used >= self.max_turns or self.tokens_used >= self.max_tokens_total

    def remaining_turns(self) -> int:
        return max(0, self.max_turns - self.turns_used)

    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens_total - self.tokens_used)

    def snapshot(self) -> dict[str, int]:
        return {
            "turns_used": self.turns_used,
            "tokens_used": self.tokens_used,            # output tokens
            "turns_remaining": self.remaining_turns(),
            "tokens_remaining": self.remaining_tokens(),
            "input_tokens_seen": self.input_tokens_seen,  # re-sent prompt (telemetry)
        }
