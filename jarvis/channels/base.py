"""Channel base types (dataclasses) + re-export of the protocol.

All dataclasses are ``frozen=True, slots=True`` — consistent with the rest
of the codebase (see :mod:`jarvis.core.protocols` and
:mod:`jarvis.core.events`). Immutability is a prerequisite for
flight-recorder replay.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

from jarvis.core.protocols import ChannelAdapter

__all__ = ["ChannelAdapter", "ChannelMessage", "ChannelSession"]


def _now_ns() -> int:
    return time.time_ns()


ChannelMessageKind = Literal["text", "voice", "system", "action", "event_mirror"]


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    """A message flowing through a channel in either direction.

    - ``kind="text"`` / ``"voice"``: user input from the UI.
    - ``kind="system"`` / ``"action"``: server -> client (status lines, tool runs).
    - ``kind="event_mirror"``: serialised bus event for UI rendering.
    """

    session_id: UUID
    kind: ChannelMessageKind
    content: str
    trace_id: UUID = field(default_factory=uuid4)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp_ns: int = field(default_factory=_now_ns)


@dataclass(frozen=True, slots=True)
class ChannelSession:
    """A connected client (a WebSocket session, a Telegram chat, ...)."""

    session_id: UUID
    channel_name: str
    user_handle: str = ""
    locale: str = "de"
    connected_at_ns: int = field(default_factory=_now_ns)
