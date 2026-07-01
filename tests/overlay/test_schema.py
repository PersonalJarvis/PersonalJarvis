"""Pydantic-v2-Schemas: Round-Trip + Discriminated Union + Defaults."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from overlay.schema import (
    AckEnvelope,
    AckPayload,
    ActionEndedEnvelope,
    ActionEndedPayload,
    ActionStartedEnvelope,
    ActionStartedPayload,
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
    IPCMessage,
    SCHEMA_VERSION,
    StateEnvelope,
    StatePayload,
    is_state_type,
    new_ulid,
    now_ns,
)


def test_ulid_format() -> None:
    u = new_ulid()
    assert isinstance(u, str)
    assert len(u) == 26  # Crockford Base32 ULID


def test_now_ns_is_unix_epoch() -> None:
    import time

    a = now_ns()
    b = time.time_ns()
    assert abs(b - a) < 1_000_000_000  # innerhalb 1s


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1


def test_state_envelope_round_trip() -> None:
    e = StateEnvelope(payload=StatePayload(state="typing", intensity=0.8, reason="tool"))
    raw = e.model_dump_json()
    back = IPCMessage.validate_json(raw)
    assert isinstance(back, StateEnvelope)
    assert back.payload.state == "typing"
    assert back.payload.intensity == 0.8
    assert back.payload.reason == "tool"
    assert back.v == 1
    assert back.target == "*"


@pytest.mark.parametrize(
    "envelope_cls,payload",
    [
        (ClickEnvelope, ClickPayload(x=10, y=20, button="left")),
        (ActionStartedEnvelope, ActionStartedPayload(kind="click")),
        (ActionEndedEnvelope, ActionEndedPayload(action_id="X", succeeded=False)),
        (CursorEnvelope, CursorPayload(x=1, y=2)),
        (HeartbeatEnvelope, HeartbeatPayload(uptime_s=42.0, fps_actual=58.5)),
        (ConfigEnvelope, ConfigPayload(mascot_enabled=False)),
        (AckEnvelope, AckPayload(ack_id="X")),
        (ErrorEnvelope, ErrorPayload(code="X", message="m")),
    ],
)
def test_all_envelope_round_trips(envelope_cls, payload) -> None:
    e = envelope_cls(payload=payload)
    back = IPCMessage.validate_json(e.model_dump_json())
    assert isinstance(back, envelope_cls)
    assert back.payload == payload


def test_discriminator_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        IPCMessage.validate_python(
            {"type": "definitely_unknown", "payload": {}, "id": "x", "ts_ns": 1}
        )


def test_state_payload_intensity_clamped() -> None:
    with pytest.raises(ValidationError):
        StatePayload(state="idle", intensity=1.5)
    with pytest.raises(ValidationError):
        StatePayload(state="idle", intensity=-0.1)


def test_envelope_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        IPCMessage.validate_python(
            {
                "type": "state",
                "v": 1,
                "id": "x",
                "ts_ns": 1,
                "target": "*",
                "payload": {"state": "idle"},
                "rogue_field": True,
            }
        )


def test_target_enum() -> None:
    e = StateEnvelope(target="edgeglow", payload=StatePayload(state="idle"))
    back = IPCMessage.validate_json(e.model_dump_json())
    assert back.target == "edgeglow"

    with pytest.raises(ValidationError):
        StateEnvelope(target="not-a-target", payload=StatePayload(state="idle"))  # type: ignore[arg-type]


def test_state_name_enum() -> None:
    with pytest.raises(ValidationError):
        StatePayload(state="acting")  # type: ignore[arg-type]


def test_is_state_type_classification() -> None:
    # Plan §10.4 — drop oldest non-state first.
    assert is_state_type("state") is True
    assert is_state_type("config") is True
    assert is_state_type("cursor") is False
    assert is_state_type("heartbeat") is False
    assert is_state_type("click") is False
    assert is_state_type("ack") is False
    assert is_state_type("unknown") is False


def test_envelope_v_must_be_at_least_1() -> None:
    with pytest.raises(ValidationError):
        IPCMessage.validate_python(
            {
                "type": "state",
                "v": 0,
                "id": "x",
                "ts_ns": 1,
                "target": "*",
                "payload": {"state": "idle"},
            }
        )


def test_higher_version_accepted_for_forward_compat() -> None:
    # Forward compat: receiver lets v=2 through (only logs a warning at
    # the IPC layer); the Pydantic model itself must not raise.
    msg = IPCMessage.validate_python(
        {
            "type": "state",
            "v": 2,
            "id": "x",
            "ts_ns": 1,
            "target": "*",
            "payload": {"state": "idle"},
        }
    )
    assert msg.v == 2


def test_jarvis_re_export_identical() -> None:
    """``jarvis.overlay.schema`` must re-export exactly the same classes."""
    from jarvis.overlay.schema import IPCMessage as JIPC
    from jarvis.overlay.schema import StateEnvelope as JSE

    assert JIPC is IPCMessage
    assert JSE is StateEnvelope
