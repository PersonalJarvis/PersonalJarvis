"""Portable integration event envelope; payload is data, never instructions."""

from dataclasses import dataclass, field
from uuid import uuid4

from jarvis.core.events import Event


@dataclass(frozen=True, slots=True)
class RoutineEventReceived(Event):
    event_name: str = ""
    payload_json: str = "{}"
    delivery_id: str = field(default_factory=lambda: str(uuid4()))
