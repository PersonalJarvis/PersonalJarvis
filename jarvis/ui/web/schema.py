"""Pydantic-Models als Single-Source-of-Truth für WebSocket-Messages.

Ausgehende Events werden via `event_to_ws_envelope()` auf die Wire geformt.
Eingehende Frames werden gegen `WSMessageIn` oder `WSCommand` validiert
(Discriminator: `type`-Feld).

Diese Models werden in Phase 1a zu JSON-Schema exportiert (via
`scripts/export_ws_schema.py`) und im Frontend zu Zod-Validators generiert,
damit Front- und Backend strukturell synchron bleiben.
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
    """Hüllt ein beliebiges Bus-Event für den Transport zur UI ein.

    Der Payload bleibt absichtlich `dict[str, Any]` — die UI rendert
    generisch über den `event_name`, typspezifische Views casten
    anhand bekannter Payload-Shapes.
    """

    type: Literal["event"] = "event"
    event_name: str
    trace_id: str
    timestamp_ns: int
    source_layer: str
    payload: dict[str, Any] = Field(default_factory=dict)


class WSWelcome(BaseModel):
    """Erstes Frame das nach `accept()` gesendet wird."""

    type: Literal["welcome"] = "welcome"
    session_id: str
    version: str
    token: str | None = None


# ----------------------------------------------------------------------
# Incoming (Client → Server)
# ----------------------------------------------------------------------

class WSMessageIn(BaseModel):
    """Nutzer-Message aus der UI — Text-Chat, Voice-Transcript oder Action."""

    type: Literal["message"] = "message"
    kind: Literal["text", "voice", "system", "action"] = "text"
    content: str
    metadata: dict[str, Any] | None = None


class WSCommand(BaseModel):
    """Steuer-Befehl aus der UI — Ping + Terminal-Control.

    Provider-Switch und Secret-Updates laufen jetzt über REST
    (POST /api/brain/switch, POST /api/secrets/{key}) — der WS-Channel
    ist reine Event-Read-Lane für den UI-State.
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
# Event-Sanitizer
# ----------------------------------------------------------------------

_ENVELOPE_FIELDS = {"trace_id", "timestamp_ns", "source_layer"}


def _jsonable(value: Any) -> Any:
    """Rekursiv non-JSON-Serializables auf primitive Typen projizieren."""
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
    # Pydantic-BaseModel hat model_dump; Fallback: str()
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _jsonable(dump())
    return str(value)


def event_to_ws_envelope(event: Event) -> dict[str, Any]:
    """Serialisiert einen Bus-Event in ein JSON-fähiges `WSEventEnvelope`-Dict.

    - `trace_id`, `timestamp_ns`, `source_layer` werden auf die Envelope-Ebene
      gehoben.
    - Alle anderen Felder landen im `payload`.
    - UUIDs → str, nested dataclasses → dict (asdict), bytes → utf8/hex.
    """
    if not is_dataclass(event):
        # Darf nicht passieren — Events sind immer dataclasses.
        raise TypeError(f"Event {type(event).__name__} ist kein dataclass")

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
