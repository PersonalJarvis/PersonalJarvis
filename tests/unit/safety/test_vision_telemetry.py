"""VisionTelemetryCollector Unit-Tests (Wave-2 B8)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import VisionInjected
from jarvis.safety.vision_telemetry import VisionTelemetryCollector


@pytest.mark.asyncio
async def test_vision_telemetry_accumulates_across_events():
    bus = EventBus()
    coll = VisionTelemetryCollector()
    coll.attach(bus)

    for i in range(3):
        await bus.publish(VisionInjected(
            trace_id=uuid4(),
            screenshot_hash=f"h-{i}",
            bytes_size=1024 * (i + 1),
            capture_age_ms=100 + i * 50,
        ))

    assert coll.injects_total == 3
    assert coll.bytes_total == 1024 + 2048 + 3072
    assert coll.avg_capture_age_ms == pytest.approx(150.0)


@pytest.mark.asyncio
async def test_vision_telemetry_dedupes_by_trace_id():
    bus = EventBus()
    coll = VisionTelemetryCollector()
    coll.attach(bus)

    tid = uuid4()
    await bus.publish(VisionInjected(trace_id=tid, bytes_size=1024, capture_age_ms=100))
    await bus.publish(VisionInjected(trace_id=tid, bytes_size=1024, capture_age_ms=100))

    assert coll.injects_total == 1
    assert coll.bytes_total == 1024


def test_vision_telemetry_snapshot_fields():
    coll = VisionTelemetryCollector()
    snap = coll.snapshot()
    assert set(snap.keys()) == {
        "vision.bytes_total",
        "vision.injects_total",
        "vision.avg_capture_age_ms",
    }
    assert snap["vision.injects_total"] == 0
    assert snap["vision.avg_capture_age_ms"] == 0.0
