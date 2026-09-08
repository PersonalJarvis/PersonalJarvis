"""Trusted turn provenance and the immutable chat completion contract."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatTurn:
    session_id: str
    turn_id: str
    user_text: str
    direct_user: bool
    trace_id: str


current_chat_turn: ContextVar[ChatTurn | None] = ContextVar("current_chat_turn", default=None)


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    turn: ChatTurn
    events_json: str
