"""The event vocabulary of an agent-chat session.

One shape for the persisted log and the live WebSocket stream: every event is
a plain dict ``{"seq", "ts_ms", "kind", "payload"}``. The UI rebuilds a
session's timeline by folding the persisted events, then keeps folding live
ones — the same reducer for both, so a reopened session looks exactly like
it did while it ran (``src/components/agentchat/reduce.ts``).

Kinds (``payload`` keys in brackets):

``user_message``       [text]                          — the person's turn
``turn_started``       [turn_id, provider, model, effort, runner]
``text_delta``         [turn_id, message_id, text]     — live only, never stored
``assistant_text``     [turn_id, message_id, text]     — the finished block
``reasoning_started``  [turn_id, message_id]           — live only: the model began
                        to think (its thinking may be redacted, so this is the
                        only sign of it until the finished block arrives)
``reasoning_delta``    [turn_id, text]                 — live only
``reasoning``          [turn_id, text, duration_ms]    — the finished block; text
                        may be "" when the vendor redacts thinking — the
                        duration still says how long it thought
``usage_delta``        [turn_id, usage]                — live only: tokens so far
                        (cumulative {input_tokens, output_tokens, …})
``tool_call``          [turn_id, call_id, name, input]
``tool_result``        [turn_id, call_id, output, is_error, duration_ms]
``approval_required``  [turn_id, approval_id, call_id, name, input, summary]
``approval_resolved``  [turn_id, approval_id, decision]   decision: allow | deny
``turn_finished``      [turn_id, status, duration_ms, usage, error, cost_usd]
                        status: done | cancelled | error
``session_updated``    [title?, provider?, model?, effort?, cwd?, permission_mode?]
``error``              [turn_id?, message]

``text_delta`` / ``reasoning_delta`` / ``reasoning_started`` / ``usage_delta``
are the transient kinds: the finished block carries the whole text and the
``turn_finished`` event the whole usage, so the log never stores token dust.
"""

from __future__ import annotations

import time
from typing import Any, Final

TRANSIENT_KINDS: Final[frozenset[str]] = frozenset(
    {"text_delta", "reasoning_delta", "reasoning_started", "usage_delta"}
)


def now_ms() -> int:
    return int(time.time() * 1000)


def make_event(kind: str, payload: dict[str, Any] | None = None, *, seq: int = 0) -> dict[str, Any]:
    """Build one event dict. ``seq`` is assigned by the store on persist."""
    return {"seq": seq, "ts_ms": now_ms(), "kind": kind, "payload": dict(payload or {})}


def is_transient(event: dict[str, Any]) -> bool:
    return event.get("kind") in TRANSIENT_KINDS
