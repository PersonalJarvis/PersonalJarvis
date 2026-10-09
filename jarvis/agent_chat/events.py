"""The event vocabulary of an agent-chat session.

One shape for the persisted log and the live WebSocket stream: every event is
a plain dict ``{"seq", "ts_ms", "kind", "payload"}``. The UI rebuilds a
session's timeline by folding the persisted events, then keeps folding live
ones — the same reducer for both, so a reopened session looks exactly like
it did while it ran (``src/components/agentchat/reduce.ts``).

Kinds (``payload`` keys in brackets):

``agent_message``      [IncomingMessage fields] — trusted internal sender, initially queued
``agent_message_status`` [message_id, status, turn_id, error] — durable delivery receipt
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
``approval_resolved``  [turn_id, approval_id, decision]   decision: allow |
                        allow_always | deny | cancel | expired (nothing waits
                        on the card any more: a restart, or the call asked again)
``question_required``  [turn_id, question_id, asker, questions, deferred?] — a question
                        card; ``deferred`` = an end-of-turn card (turn_prompts)
                        whose answers go to the agent as the next message
``question_progress``  [turn_id, question_id, answers]
``question_resolved``  [turn_id, question_id, answers]
``credential_required`` [turn_id, request_id, asker, env, label, description, placeholder,
                        replace, expires_ms] — a secure field for a secret the agent
                        needs (credential_requests.py); the value never appears in any event
``credential_resolved`` [turn_id, request_id, env, status]   status: saved | declined |
                        timeout | cancelled
``plan_ready``         [turn_id, build_mode]           — a coding agent's plan card
``plan_resolved``      [turn_id, decision]             decision: build | keep
``subagent_started``   [turn_id, agent_id, description, agent_type, prompt, background,
                        task_id?, thread_id?] — a coding agent spawned a sub-agent;
                        ``agent_id`` is the spawning tool call's id
``subagent_progress``  [turn_id, agent_id, activity, last_tool, tokens?, tool_uses?,
                        duration_ms?] — live only: what the sub-agent is doing now
``subagent_finished``  [turn_id, agent_id, status, summary?, tokens?, tool_uses?,
                        duration_ms?]   status: done | failed | stopped
``turn_finished``      [turn_id, status, duration_ms, usage, error, cost_usd]
                        status: done | cancelled | error
``session_updated``    [title?, provider?, model?, effort?, cwd?, permission_mode?]
``error``              [turn_id?, message]
``notice``              [kind, ...]                      — a system line outside a turn

A sub-agent's own text, thoughts and calls are the ordinary kinds with an
``agent_id`` in the payload: they belong to that sub-agent's conversation,
never to the main agent's answer.

``text_delta`` / ``reasoning_delta`` / ``reasoning_started`` / ``usage_delta`` /
``subagent_progress`` are the transient kinds: the finished block carries the whole text and the
``turn_finished`` event the whole usage, so the log never stores token dust.
"""

from __future__ import annotations

import time
from typing import Any, Final

TRANSIENT_KINDS: Final[frozenset[str]] = frozenset(
    {"text_delta", "reasoning_delta", "reasoning_started", "usage_delta", "subagent_progress"}
)


def now_ms() -> int:
    return int(time.time() * 1000)


def make_event(kind: str, payload: dict[str, Any] | None = None, *, seq: int = 0) -> dict[str, Any]:
    """Build one event dict. ``seq`` is assigned by the store on persist."""
    return {"seq": seq, "ts_ms": now_ms(), "kind": kind, "payload": dict(payload or {})}


def is_transient(event: dict[str, Any]) -> bool:
    return event.get("kind") in TRANSIENT_KINDS
