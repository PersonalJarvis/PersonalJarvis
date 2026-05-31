"""Unit-Tests fuer die Replay-CLI."""
from __future__ import annotations

import io
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionExecuted,
    HarnessDispatched,
    ObservationCaptured,
)
from jarvis.core.protocols import HarnessTask
from jarvis.telemetry import FlightRecorder
from jarvis.telemetry.replay import main as replay_main
from jarvis.telemetry.replay import render_timeline


@pytest.mark.asyncio
async def test_timeline_renders_relative_times(tmp_path, capsys):
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.attach(bus)

    tid = uuid4()
    await bus.publish(HarnessDispatched(trace_id=tid, harness="computer-use",
                                         task=HarnessTask(prompt="x")))
    await bus.publish(ObservationCaptured(trace_id=tid, window_title="Notepad",
                                           node_count=42, screenshot_hash="abc"))
    await bus.publish(ActionExecuted(trace_id=tid, tool_name="type_text",
                                      success=True, duration_ms=123))
    await rec.flush()
    await rec.close()

    records = rec.iter_events_for_trace(tid)
    buf = io.StringIO()
    render_timeline(records, out=buf)
    text = buf.getvalue()

    assert "HarnessDispatched" in text
    assert "ObservationCaptured" in text
    assert "ActionExecuted" in text
    assert "harness=computer-use" in text
    assert "window_title=Notepad" in text
    assert "tool_name=type_text" in text


@pytest.mark.asyncio
async def test_cli_exits_zero_when_records_found(tmp_path):
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.attach(bus)

    tid = uuid4()
    await bus.publish(HarnessDispatched(trace_id=tid, harness="fake",
                                         task=HarnessTask(prompt="p")))
    await rec.flush()
    await rec.close()

    code = replay_main([tid.hex, "--data-dir", str(tmp_path)])
    assert code == 0


def test_cli_exits_one_when_no_records(tmp_path):
    code = replay_main([uuid4().hex, "--data-dir", str(tmp_path)])
    assert code == 1


def test_cli_rejects_invalid_uuid(tmp_path):
    code = replay_main(["not-a-uuid", "--data-dir", str(tmp_path)])
    assert code == 2


@pytest.mark.asyncio
async def test_cli_json_mode_emits_raw_lines(tmp_path, capsys):
    import json as _json
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.attach(bus)
    tid = uuid4()
    await bus.publish(HarnessDispatched(trace_id=tid, harness="x",
                                         task=HarnessTask(prompt="p")))
    await rec.flush()
    await rec.close()

    replay_main([tid.hex, "--data-dir", str(tmp_path), "--json"])
    captured = capsys.readouterr()
    records = [
        _json.loads(line) for line in captured.out.strip().splitlines() if line
    ]
    assert len(records) == 1
    assert records[0]["event"] == "HarnessDispatched"
