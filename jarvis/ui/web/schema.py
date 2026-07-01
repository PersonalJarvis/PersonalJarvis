"""Pydantic models as the single source of truth for WebSocket messages.

Outgoing events are shaped onto the wire via `event_to_ws_envelope()`.
Incoming frames are validated against `WSMessageIn` or `WSCommand`
(discriminator: `type` field).

These models are exported to JSON schema in Phase 1a (via
`scripts/export_ws_schema.py`) and generated into Zod validators on the
frontend, so front- and backend stay structurally in sync.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from jarvis.core.events import Event

# ----------------------------------------------------------------------
# Outgoing (Server → Client)
# ----------------------------------------------------------------------

class WSEventEnvelope(BaseModel):
    """Wraps an arbitrary bus event for transport to the UI.

    The payload deliberately stays `dict[str, Any]` — the UI renders
    generically based on `event_name`, and type-specific views cast
    against known payload shapes.
    """

    type: Literal["event"] = "event"
    event_name: str
    trace_id: str
    timestamp_ns: int
    source_layer: str
    payload: dict[str, Any] = Field(default_factory=dict)


class WSWelcome(BaseModel):
    """First frame sent after `accept()`."""

    type: Literal["welcome"] = "welcome"
    session_id: str
    version: str
    token: str | None = None


# ----------------------------------------------------------------------
# Incoming (Client → Server)
# ----------------------------------------------------------------------

class WSMessageIn(BaseModel):
    """User message from the UI — text chat, voice transcript, or action."""

    type: Literal["message"] = "message"
    kind: Literal["text", "voice", "system", "action"] = "text"
    content: str
    metadata: dict[str, Any] | None = None


class WSCommand(BaseModel):
    """Control command from the UI — ping + terminal control.

    Provider switching and secret updates now run over REST
    (POST /api/brain/switch, POST /api/secrets/{key}) — the WS channel
    is a pure event-read lane for UI state.
    """

    type: Literal["command"] = "command"
    action: Literal[
        "ping",
        "test_event",
        "terminal.spawn",
        "terminal.input",
        "terminal.resize",
        "terminal.close",
        # Chat mic-dictation: payload {"mode": "start" | "stop"}. Transcribe-only
        # into the chat input — never reaches the brain.
        "stt_dictate",
        # Drag-drop a mission/output card onto the Jarvis dock: payload
        # {slug, utterance, status, summary?, error?, mission_id?, thread_id?}.
        # Pulls the sub-agent task into the live conversation context.
        "mission.inject",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


# ----------------------------------------------------------------------
# Event sanitizer
# ----------------------------------------------------------------------

_ENVELOPE_FIELDS = {"trace_id", "timestamp_ns", "source_layer"}


def _jsonable(value: Any) -> Any:
    """Recursively project non-JSON-serializable values onto primitive types."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    # A Pydantic BaseModel has model_dump; fallback: str()
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _jsonable(dump())
    return str(value)


def event_to_ws_envelope(event: Event) -> dict[str, Any]:
    """Serializes a bus event into a JSON-able `WSEventEnvelope` dict.

    - `trace_id`, `timestamp_ns`, `source_layer` are lifted onto the envelope
      level.
    - All other fields end up in `payload`.
    - UUIDs → str, nested dataclasses → dict (asdict), bytes → utf8/hex.
    """
    if not is_dataclass(event):
        # Should never happen — events are always dataclasses.
        raise TypeError(f"Event {type(event).__name__} is not a dataclass")

    raw = asdict(event)
    payload: dict[str, Any] = {}
    for k, v in raw.items():
        if k in _ENVELOPE_FIELDS:
            continue
        payload[k] = _jsonable(v)

    envelope = WSEventEnvelope(
        event_name=type(event).__name__,
        trace_id=str(event.trace_id),
        timestamp_ns=int(event.timestamp_ns),
        source_layer=str(event.source_layer),
        payload=payload,
    )
    return envelope.model_dump()
