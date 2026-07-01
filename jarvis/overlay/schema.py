"""IPC schemas (main-Jarvis side) — re-export from the OS-Level package.

AD-15: single source. Every change to the wire format happens in
``OS-Level/src/overlay/schema.py``; here only re-imports.

The ``OS-Level/src`` directory must be on ``sys.path`` for
``import overlay.schema`` to work. The main-Jarvis bootstrap path
(``jarvis.overlay.server.start_ipc_server``) extends ``sys.path`` lazily
on first call, so the import doesn't already fail at module load
if someone imports ``jarvis.overlay`` in isolation.
"""

from __future__ import annotations

# sys.path extension handled lazily in jarvis/overlay/__init__.py.
from overlay.schema import (  # noqa: F401  (re-exports)
    AckEnvelope,
    AckPayload,
    ActionEndedEnvelope,
    ActionEndedPayload,
    ActionKindLiteral,
    ActionStartedEnvelope,
    ActionStartedPayload,
    ClickButton,
    ClickEnvelope,
    ClickPayload,
    ConfigEnvelope,
    ConfigPayload,
    CursorEnvelope,
    CursorPayload,
    ErrorEnvelope,
    ErrorPayload,
    HeartbeatEnvelope,
    HeartbeatPayload,
    IPCEnvelope,
    IPCMessage,
    MascotEventEnvelope,
    MascotEventKind,
    MascotEventPayload,
    NON_STATE_TYPES,
    SCHEMA_VERSION,
    STATE_TYPES,
    StateEnvelope,
    StateName,
    StatePayload,
    StateReason,
    Target,
    is_state_type,
    new_ulid,
    now_ns,
)

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
