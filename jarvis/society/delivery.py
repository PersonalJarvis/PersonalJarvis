"""Trusted provenance for internal chat messages and their delivery receipts."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Literal

from pydantic import BaseModel, ConfigDict


class IncomingMessage(BaseModel):
    """Persisted chat payload; callers cannot choose this through the public API."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str
    sender_id: str
    sender_name: str
    sender_kind: Literal["jarvis", "agent", "user"]
    text: str
    prompt: str
    trace_id: str
    status: Literal["queued", "delivered", "failed"] = "queued"
    turn_id: str = ""
    error: str = ""


# asyncio tasks inherit this context when a receiving turn is started. No
# model argument or REST body can select a sender or a conversation trace.
incoming_context: ContextVar[IncomingMessage | None] = ContextVar(
    "society_incoming_message", default=None
)


class DeliveryBusy(RuntimeError):
    """The canonical chat is occupied; keep the envelope in the durable queue."""
