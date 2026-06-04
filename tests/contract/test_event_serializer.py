"""Contract-Test: event_to_ws_envelope muss JEDES Event in `core.events`
verlustfrei serialisieren können.

**Hintergrund:** Das Wire-Format zwischen Backend und Frontend hängt daran,
dass jedes Event in der Codebase durch den Serializer läuft. Wenn jemand
ein neues Event mit einem nicht-JSON-fähigen Feld-Typ hinzufügt, muss dieser
Test rot werden — sonst crashed die UI zur Laufzeit.
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


@pytest.mark.parametrize("event_cls", EVENT_CLASSES, ids=[c.__name__ for c in EVENT_CLASSES])
def test_event_serializes_to_json(event_cls: type[Event]) -> None:
    """Instanziert Event mit Defaults und serialisiert zu JSON."""
    event = event_cls()
    envelope = event_to_ws_envelope(event)

    # JSON-roundtrip muss klappen
    raw = json.dumps(envelope)
    decoded = json.loads(raw)

    # Envelope-Shape
    assert decoded["type"] == "event"
    assert decoded["event_name"] == event_cls.__name__
    assert isinstance(decoded["trace_id"], str)
    # trace_id muss parseable UUID sein
    UUID(decoded["trace_id"])
    assert isinstance(decoded["timestamp_ns"], int)
    assert "payload" in decoded


def test_system_state_changed_payload_roundtrip() -> None:
    """Payload-Feldmapping für den am häufigsten gesendeten Event-Typ."""
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
