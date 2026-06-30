"""attach_flight_recorder — the single boot wiring point for the audit log
(audit 🟠 #14).

The FlightRecorder was defined but never attached at boot, so
``telemetry.flight_recorder = true`` promised a replayable audit trail that was
silently empty. These pin the gating: enabled -> a wildcard subscriber is
registered and a recorder returned; disabled -> nothing is touched.
"""
from __future__ import annotations

from typing import Any

from jarvis.telemetry.recorder import FlightRecorder, attach_flight_recorder


class _FakeBus:
    def __init__(self) -> None:
        self.subscribed: list[Any] = []

    def subscribe_all(self, handler: Any) -> None:
        self.subscribed.append(handler)


def test_disabled_returns_none_and_does_not_subscribe():
    bus = _FakeBus()
    rec = attach_flight_recorder(bus, enabled=False)
    assert rec is None
    assert bus.subscribed == []


def test_enabled_attaches_one_wildcard_subscriber(tmp_path):
    bus = _FakeBus()
    rec = attach_flight_recorder(bus, enabled=True, data_dir=tmp_path / "fr")
    assert isinstance(rec, FlightRecorder)
    assert len(bus.subscribed) == 1


def test_enabled_creates_the_data_dir(tmp_path):
    target = tmp_path / "fr"
    attach_flight_recorder(_FakeBus(), enabled=True, data_dir=target)
    assert target.is_dir()
    assert (target / "blobs").is_dir()


def test_attach_is_idempotent_per_bus(tmp_path):
    # FlightRecorder.attach is idempotent; re-attaching the SAME recorder to the
    # SAME bus must not double-subscribe (guards a double-init at boot).
    bus = _FakeBus()
    rec = attach_flight_recorder(bus, enabled=True, data_dir=tmp_path / "fr")
    assert rec is not None
    rec.attach(bus)  # second call, same bus
    assert len(bus.subscribed) == 1
