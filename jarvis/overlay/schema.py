"""IPC schemas (main-Jarvis side) — re-export from the OS-Level package.

AD-15: single source. Every change to the wire format happens in
``OS-Level/src/overlay/schema.py``; here only re-imports.

The ``overlay`` package resolves either from a real install (``pip install
-e OS-Level``) or — fallback — from the in-repo ``OS-Level/src`` tree, which
this module puts on ``sys.path`` itself when the first import misses.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Self-sufficient resolution of the OS-Level package (2026-07-06). The old
# comment claimed a lazy sys.path extension elsewhere — it never existed, so
# this import only worked on hosts with a manual ``pip install -e OS-Level``
# (the maintainer's interpreter). In a full repo checkout, fall back to
# putting ``OS-Level/src`` on sys.path; on a proper install the probe import
# succeeds and the fallback never runs.
try:
    import overlay.schema  # noqa: F401  — probe only
except ModuleNotFoundError:
    _os_level_src = Path(__file__).resolve().parents[2] / "OS-Level" / "src"
    if _os_level_src.is_dir() and str(_os_level_src) not in sys.path:
        sys.path.append(str(_os_level_src))

from overlay.schema import (  # noqa: E402,F401  (re-exports)
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
