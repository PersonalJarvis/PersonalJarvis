"""Schema coverage for the mascot → main jarvis interaction envelope.

The mascot subprocess sends ``MascotEventEnvelope`` upstream when the
user interacts with the sprite in a way that should drive Jarvis state
(currently: doubleClick → mute toggle). The envelope is part of the
discriminated IPC union, so we round-trip it through ``IPCMessage`` to
catch any drift in the validator.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from overlay.schema import (
    IPCMessage,
    MascotEventEnvelope,
    MascotEventPayload,
    NON_STATE_TYPES,
    STATE_TYPES,
    is_state_type,
)


def test_mascot_event_round_trip() -> None:
    e = MascotEventEnvelope(payload=MascotEventPayload(kind="mute_toggle"))
    raw = e.model_dump_json()
    back = IPCMessage.validate_json(raw)
    assert isinstance(back, MascotEventEnvelope)
    assert back.payload.kind == "mute_toggle"
    assert back.type == "mascot_event"


def test_mascot_event_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        MascotEventPayload(kind="hover")  # type: ignore[arg-type]


def test_mascot_event_is_non_state_type() -> None:
    assert "mascot_event" in NON_STATE_TYPES
    assert "mascot_event" not in STATE_TYPES
    assert is_state_type("mascot_event") is False


def test_mascot_event_payload_forbids_extras() -> None:
    """User-driven payload must reject unknown keys to surface protocol drift."""
    with pytest.raises(ValidationError):
        MascotEventPayload.model_validate({"kind": "mute_toggle", "stray": 1})
