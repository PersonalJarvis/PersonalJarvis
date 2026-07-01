"""Contract test: event_to_ws_envelope must serialize EVERY event in
`core.events` losslessly.

**Background:** the wire format between backend and frontend depends on
every event in the codebase passing through the serializer. If someone
adds a new event with a non-JSON-able field type, this test must go
red — otherwise the UI crashes at runtime.
"""
from __future__ import annotations

import inspect
import json
from uuid import UUID

import pytest

from jarvis.core import events as events_mod
from jarvis.core.events import Event
from jarvis.ui.web.schema import event_to_ws_envelope


def _all_event_classes() -> list[type[Event]]:
    """Discovery: alle Event-Subklassen in `jarvis.core.events`."""
    classes: list[type[Event]] = []
    for _, obj in inspect.getmembers(events_mod, inspect.isclass):
        if obj is Event:
            continue
        if issubclass(obj, Event):
            classes.append(obj)
    return classes


EVENT_CLASSES = _all_event_classes()


# Some Event subclasses validate their fields in __post_init__ and cannot be
# instantiated with bare defaults.  Map class name → minimal valid kwargs so
# the parametrized test can still cover them without weakening the guard.
_REQUIRED_KWARGS: dict[str, dict] = {
    # LatencySpan.__post_init__ rejects phase="" — supply a real phase value.
    "LatencySpan": {"phase": "stt_finalize"},
}


@pytest.mark.parametrize("event_cls", EVENT_CLASSES, ids=[c.__name__ for c in EVENT_CLASSES])
def test_event_serializes_to_json(event_cls: type[Event]) -> None:
    """Instantiates the event with defaults and serializes it to JSON."""
    kwargs = _REQUIRED_KWARGS.get(event_cls.__name__, {})
    event = event_cls(**kwargs)
    envelope = event_to_ws_envelope(event)

    # JSON round-trip must work
    raw = json.dumps(envelope)
    decoded = json.loads(raw)

    # Envelope-Shape
    assert decoded["type"] == "event"
    assert decoded["event_name"] == event_cls.__name__
    assert isinstance(decoded["trace_id"], str)
    # trace_id must be a parseable UUID
    UUID(decoded["trace_id"])
    assert isinstance(decoded["timestamp_ns"], int)
    assert "payload" in decoded


def test_system_state_changed_payload_roundtrip() -> None:
    """Payload field mapping for the most frequently sent event type."""
    from jarvis.core.events import SystemStateChanged

    evt = SystemStateChanged(
        new_state="LISTENING", previous="IDLE", source_layer="test"
    )
    env = event_to_ws_envelope(evt)
    assert env["payload"]["new_state"] == "LISTENING"
    assert env["payload"]["previous"] == "IDLE"
    assert env["source_layer"] == "test"


def test_event_classes_discovered() -> None:
    """Meta-Test: die Discovery findet die erwarteten Kern-Events."""
    names = {c.__name__ for c in EVENT_CLASSES}
    for expected in (
        "SystemStarted",
        "SystemStateChanged",
        "ThreadCreated",
        "MessageSent",
        "ResponseGenerated",
    ):
        assert expected in names, f"Fehlt: {expected}"
