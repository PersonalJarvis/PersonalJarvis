"""Unit tests for FlightRecorder (ADR-0007)."""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionExecuted,
    HarnessDispatched,
    KillRequested,
    ObservationCaptured,
)
from jarvis.core.protocols import HarnessTask
from jarvis.telemetry import FlightRecorder


@pytest.mark.asyncio
async def test_recorder_writes_jsonl_per_event(tmp_path):
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.attach(bus)

    tid = uuid4()
    await bus.publish(HarnessDispatched(
        trace_id=tid, harness="computer-use",
        task=HarnessTask(prompt="test prompt"),
    ))
    await rec.flush()
    await rec.close()

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1

    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "HarnessDispatched"
    assert record["trace_id"] == tid.hex
    assert record["payload"]["harness"] == "computer-use"
    assert "ts_ns" in record


@pytest.mark.asyncio
async def test_recorder_captures_multiple_event_types(tmp_path):
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.attach(bus)

    tid = uuid4()
    await bus.publish(HarnessDispatched(trace_id=tid, harness="x",
                                         task=HarnessTask(prompt="p")))
    await bus.publish(ObservationCaptured(trace_id=tid, window_title="Notepad",
                                           node_count=42,
                                           screenshot_hash="abc123"))
    await bus.publish(ActionExecuted(trace_id=tid, tool_name="type_text",
                                      success=True, duration_ms=120))
    await rec.flush()
    await rec.close()

    lines = list(tmp_path.glob("*.jsonl"))[0].read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["event"] for line in lines]
    assert kinds == ["HarnessDispatched", "ObservationCaptured", "ActionExecuted"]


@pytest.mark.asyncio
async def test_recorder_can_iterate_by_trace_id(tmp_path):
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.attach(bus)

    t1, t2 = uuid4(), uuid4()
    await bus.publish(KillRequested(trace_id=t1, source="hotkey"))
    await bus.publish(KillRequested(trace_id=t2, source="voice"))
    await bus.publish(KillRequested(trace_id=t1, source="tray"))
    await rec.flush()
    await rec.close()

    records = rec.iter_events_for_trace(t1)
    assert len(records) == 2
    sources = {r["payload"]["source"] for r in records}
    assert sources == {"hotkey", "tray"}


@pytest.mark.asyncio
async def test_recorder_is_idempotent_on_attach(tmp_path):
    """Calling attach() twice on the same bus must not subscribe twice."""
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.attach(bus)
    rec.attach(bus)

    tid = uuid4()
    await bus.publish(KillRequested(trace_id=tid, source="hotkey"))
    await rec.flush()

    lines = list(tmp_path.glob("*.jsonl"))[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_recorder_externalizes_large_blobs(tmp_path):
    """Bytes > blob_inline_limit_bytes end up in blobs/, not inline."""
    from dataclasses import dataclass

    from jarvis.core.events import Event

    @dataclass(frozen=True, slots=True)
    class BigEvent(Event):
        screenshot_png: bytes = b""
        label: str = ""

    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.blob_inline_limit_bytes = 100         # kuenstliches Mini-Limit
    rec.attach(bus)

    huge = b"X" * 200
    await bus.publish(BigEvent(screenshot_png=huge, label="test"))
    await rec.flush()
    await rec.close()

    record = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
    payload = record["payload"]
    assert "__file__" in payload["screenshot_png"]
    # Die referenzierte Datei existiert wirklich
    blob_rel = payload["screenshot_png"]["__file__"]
    assert (tmp_path.parent / blob_rel).exists()


@pytest.mark.asyncio
async def test_recorder_handles_day_rotation(tmp_path):
    """Tageswechsel → neue Datei."""
    fake_day = ["2026-01-01"]
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0,
                         today_date=lambda: fake_day[0])
    rec.attach(bus)

    tid = uuid4()
    await bus.publish(KillRequested(trace_id=tid, source="a"))
    await rec.flush()
    assert (tmp_path / "2026-01-01.jsonl").exists()

    fake_day[0] = "2026-01-02"
    await bus.publish(KillRequested(trace_id=tid, source="b"))
    await rec.flush()
    assert (tmp_path / "2026-01-02.jsonl").exists()

    await rec.close()


@pytest.mark.asyncio
async def test_flush_interval_respected(tmp_path):
    """With flush_interval_s > 0, it does not write after every event."""
    fake_now = [0]
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=1.0,
                         now_ns=lambda: fake_now[0])
    rec.attach(bus)

    tid = uuid4()
    # First event → buffer, no write yet
    await bus.publish(KillRequested(trace_id=tid, source="a"))
    assert not list(tmp_path.glob("*.jsonl"))

    # Advance time by 0.5s → still no flush
    fake_now[0] += 500_000_000
    await bus.publish(KillRequested(trace_id=tid, source="b"))
    # Event is delivered, but flush only once the interval is exceeded:
    await asyncio.sleep(0)
    # File may not exist yet — test depends on dispatch timing.

    # Advance time by 2s and publish one more event
    fake_now[0] += 2_000_000_000
    await bus.publish(KillRequested(trace_id=tid, source="c"))
    await asyncio.sleep(0)
    await rec.flush()               # explizit, damit Test deterministisch
    await rec.close()

    files = list(tmp_path.glob("*.jsonl"))
    assert files
    lines = files[0].read_text().splitlines()
    assert len(lines) == 3
