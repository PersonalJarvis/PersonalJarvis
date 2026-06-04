"""IPC Pydantic-v2-Schemas — Single-Source. Plan §10.

Wire-Envelope (§10.1):
    {v, type, id, ts_ns, target, payload}

``type`` ist der Discriminator fuer ``payload``. Die saubere Pydantic-v2-
Variante: pro Message-Type ein eigenes Envelope-Modell mit ``type`` als
``Literal``-Field; die Union darueber wird mit ``Field(discriminator="type")``
typisiert. ``IPCMessage`` ist die runtime-validierende ``TypeAdapter``-
Instanz.

AD-15: Pydantic v2 (Python) und Zod (TS) symmetrisch — Phase 9.4 leitet
das Zod-Schema aus dem JSON-Schema-Export hier ab.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
)
from ulid import ULID

# Schema-Major-Version. Forward-Compat: Empfaenger warnen wenn != 1.
SCHEMA_VERSION = 1

# Discriminator-Werte (auch von der State-Machine in 9.3 referenziert).
StateName = Literal[
    "idle",
    "listening",
    "thinking",
    "typing",
    "clicking",
    "speaking",
    "error",
    "hidden",
]
Target = Literal["edgeglow", "mascot", "*"]
ActionKindLiteral = Literal["click", "type", "move", "navigate", "hotkey", "scroll"]
ClickButton = Literal["left", "right", "middle"]
StateReason = Literal["wakeword", "user", "tool", "timeout", "error"]


def now_ns() -> int:
    """Unix-epoch ns. Kompatibel mit SHM-Cursor + WS-Envelopes (§11.4)."""
    return time.time_ns()


def new_ulid() -> str:
    """Frische ULID als Wire-String (26 Crockford-Base32 chars)."""
    return str(ULID())


# -----------------------------------------------------------------------------
# Payloads — §10.2
# -----------------------------------------------------------------------------


class StatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: StateName
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    since_ts_ns: int = Field(default_factory=now_ns)
    reason: StateReason | None = None


class ClickPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    monitor: str = ""
    button: ClickButton = "left"
    modifiers: list[str] = Field(default_factory=list)
    wallclock_ns: int = Field(default_factory=now_ns)


class ActionStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ActionKindLiteral
    action_id: str = Field(default_factory=new_ulid)
    duration_hint_ms: int | None = None


class ActionEndedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    succeeded: bool = True
    duration_actual_ms: int | None = None


class CursorPayload(BaseModel):
    """WS-Fallback wenn SHM nicht verfuegbar (§11.5)."""

    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    monitor: str = ""


class HeartbeatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uptime_s: float = 0.0
    rss_mb: float = 0.0
    fps_actual: float = 0.0
    fps_target: float = 0.0
    drops: int = 0
    ws_connected: bool = True
    shm_attached: bool = False


class ConfigPayload(BaseModel):
    """Hauptjarvis -> Overlay nach Config-Reload (§10.2)."""

    model_config = ConfigDict(extra="allow")  # forward-compat, neue Theme-Keys

    theme: dict[str, Any] = Field(default_factory=dict)
    mascot_enabled: bool = True
    mascot_pos: dict[str, Any] = Field(default_factory=dict)
    fps_active: int = 30
    fps_burst: int = 60
    all_monitors: bool = False
    hide_on_fullscreen: bool = True
    hide_from_capture: bool = True
    respect_reduced_motion: bool = True
    shm_cursor_name: str = ""
    shm_cursor_hz: int = 60


class AckPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ack_id: str
    received_ts_ns: int = Field(default_factory=now_ns)
    rendered_ts_ns: int | None = None


class ErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    recoverable: bool = True
    context: dict[str, Any] = Field(default_factory=dict)


# Mascot-originated user interactions. The overlay subprocess emits this
# upstream when the user interacts with the mascot in a way that should
# trigger Jarvis-side behaviour (currently: toggle global voice mute).
MascotEventKind = Literal["mute_toggle"]


class MascotEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MascotEventKind


# -----------------------------------------------------------------------------
# Envelope — §10.1, eine Klasse pro type, Discriminated Union
# -----------------------------------------------------------------------------


class _BaseEnvelope(BaseModel):
    """Gemeinsame Envelope-Felder. ``type`` setzt jede Subklasse selbst."""

    model_config = ConfigDict(extra="forbid")

    v: int = SCHEMA_VERSION
    id: str = Field(default_factory=new_ulid)
    ts_ns: int = Field(default_factory=now_ns)
    target: Target = "*"

    @field_validator("v")
    @classmethod
    def _check_version(cls, value: int) -> int:
        # Forward-Compat: hoechere Versionen werden in IPC-Layer geloggt,
        # hier akzeptieren wir alles >=1, damit ein neueres Overlay vom
        # alten Hauptjarvis empfangen kann.
        if value < 1:
            raise ValueError(f"schema-version v={value} < 1")
        return value


class StateEnvelope(_BaseEnvelope):
    type: Literal["state"] = "state"
    payload: StatePayload


class ClickEnvelope(_BaseEnvelope):
    type: Literal["click"] = "click"
    payload: ClickPayload


class ActionStartedEnvelope(_BaseEnvelope):
    type: Literal["action_started"] = "action_started"
    payload: ActionStartedPayload


class ActionEndedEnvelope(_BaseEnvelope):
    type: Literal["action_ended"] = "action_ended"
    payload: ActionEndedPayload


class CursorEnvelope(_BaseEnvelope):
    type: Literal["cursor"] = "cursor"
    payload: CursorPayload


class HeartbeatEnvelope(_BaseEnvelope):
    type: Literal["heartbeat"] = "heartbeat"
    payload: HeartbeatPayload


class ConfigEnvelope(_BaseEnvelope):
    type: Literal["config"] = "config"
    payload: ConfigPayload


class AckEnvelope(_BaseEnvelope):
    type: Literal["ack"] = "ack"
    payload: AckPayload


class ErrorEnvelope(_BaseEnvelope):
    type: Literal["error"] = "error"
    payload: ErrorPayload


class MascotEventEnvelope(_BaseEnvelope):
    type: Literal["mascot_event"] = "mascot_event"
    payload: MascotEventPayload


IPCEnvelope = Annotated[
    Union[
        StateEnvelope,
        ClickEnvelope,
        ActionStartedEnvelope,
        ActionEndedEnvelope,
        CursorEnvelope,
        HeartbeatEnvelope,
        ConfigEnvelope,
        AckEnvelope,
        ErrorEnvelope,
        MascotEventEnvelope,
    ],
    Field(discriminator="type"),
]

# Runtime-Validator. ``IPCMessage.validate_python(d)`` /
# ``IPCMessage.validate_json(b)`` waehlen anhand des ``type``-Felds das
# richtige Envelope-Modell.
IPCMessage: TypeAdapter[Any] = TypeAdapter(IPCEnvelope)


# State-Sets, die Plan §6.1 in Code abbildet — auch von der 9.3-State-
# Machine konsumiert.
NON_STATE_TYPES: frozenset[str] = frozenset(
    {
        "cursor",
        "ack",
        "heartbeat",
        "action_started",
        "action_ended",
        "click",
        "error",
        "mascot_event",
    }
)
STATE_TYPES: frozenset[str] = frozenset({"state", "config"})


def is_state_type(envelope_type: str) -> bool:
    """Backpressure-Hint: ``False`` -> kann gedroppt werden (§10.4)."""
    return envelope_type in STATE_TYPES


__all__ = [
    "AckEnvelope",
    "AckPayload",
    "ActionEndedEnvelope",
    "ActionEndedPayload",
    "ActionKindLiteral",
    "ActionStartedEnvelope",
    "ActionStartedPayload",
    "ClickButton",
    "ClickEnvelope",
    "ClickPayload",
    "ConfigEnvelope",
    "ConfigPayload",
    "CursorEnvelope",
    "CursorPayload",
    "ErrorEnvelope",
    "ErrorPayload",
    "HeartbeatEnvelope",
    "HeartbeatPayload",
    "IPCEnvelope",
    "IPCMessage",
    "MascotEventEnvelope",
    "MascotEventKind",
    "MascotEventPayload",
    "NON_STATE_TYPES",
    "SCHEMA_VERSION",
    "STATE_TYPES",
    "StateEnvelope",
    "StateName",
    "StatePayload",
    "StateReason",
    "Target",
    "is_state_type",
    "new_ulid",
    "now_ns",
]
