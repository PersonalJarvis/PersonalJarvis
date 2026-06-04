"""Tests for LatencyTracker — the fire-and-forget hot-path span emitter.

The tracker records perf_counter milestones for one voice turn and publishes
LatencySpan events. It must never block or break the hot path: emission is
fire-and-forget, a disabled tracker is a no-op, and a missing bus is safe.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from jarvis.core.events import LatencyPhase, LatencySpan
from jarvis.telemetry.latency import LatencyTracker


class _CaptureBus:
    """Minimal stand-in for EventBus that records published events."""

    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


async def test_mark_emits_cumulative_span_from_anchor() -> None:
    bus = _CaptureBus()
    tracker = LatencyTracker(bus=bus, trace_id=uuid4(), enabled=True)

    tracker.mark(LatencyPhase.STT_FINALIZE)
    await asyncio.sleep(0)  # let the fire-and-forget publish task run

    assert len(bus.events) == 1
    span = bus.events[0]
    assert isinstance(span, LatencySpan)
    assert span.phase == LatencyPhase.STT_FINALIZE
    assert span.duration_ms >= 0.0
    assert span.t_end_ns >= span.t_start_ns


async def test_disabled_tracker_emits_nothing() -> None:
    bus = _CaptureBus()
    tracker = LatencyTracker(bus=bus, trace_id=uuid4(), enabled=False)

    tracker.mark(LatencyPhase.INTENT_DECISION)
    with tracker.span(LatencyPhase.BRAIN_FIRST_TOKEN):
        pass
    await asyncio.sleep(0)

    assert bus.events == []


async def test_span_context_manager_measures_block() -> None:
    bus = _CaptureBus()
    tracker = LatencyTracker(bus=bus, trace_id=uuid4(), enabled=True)

    with tracker.span(LatencyPhase.INTENT_DECISION):
        sum(range(10_000))  # a little measurable work
    await asyncio.sleep(0)

    assert len(bus.events) == 1
    span = bus.events[0]
    assert span.phase == LatencyPhase.INTENT_DECISION
    assert span.duration_ms >= 0.0


async def test_tracker_without_bus_is_safe_noop() -> None:
    tracker = LatencyTracker(bus=None, trace_id=uuid4(), enabled=True)

    # Must not raise even though there is no bus to publish to.
    tracker.mark(LatencyPhase.ACK_FIRST_TOKEN)
    with tracker.span(LatencyPhase.ACK_FIRST_AUDIO):
        pass
    await asyncio.sleep(0)
