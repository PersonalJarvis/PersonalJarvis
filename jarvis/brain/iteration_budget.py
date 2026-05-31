"""Iteration budget: limits per conversation turn.

Two parallel limits:
- `max_turns`: number of allowed tool-use loops (prevents infinite loops).
- `max_tokens`: cumulative token ceiling (prevents cost explosion).

Both are *soft hints* — according to the API docs Claude may exceed them by up
to 10% when in the middle of a callback. We therefore combine a hard count with
a soft check.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IterationBudget:
    """Tracks turns and tokens for a conversation."""
    max_turns: int = 15
    max_tokens_total: int = 50_000
    turns_used: int = 0
    tokens_used: int = 0

    def record_turn(self, tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.turns_used += 1
        self.tokens_used += int(tokens_in) + int(tokens_out)

    def exceeded(self) -> bool:
        return self.turns_used >= self.max_turns or self.tokens_used >= self.max_tokens_total

    def remaining_turns(self) -> int:
        return max(0, self.max_turns - self.turns_used)

    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens_total - self.tokens_used)

    def snapshot(self) -> dict[str, int]:
        return {
            "turns_used": self.turns_used,
            "tokens_used": self.tokens_used,
            "turns_remaining": self.remaining_turns(),
            "tokens_remaining": self.remaining_tokens(),
        }
