"""A publisher's string trace id costs telemetry one line, not the event.

The recorder observes EVERY event on the bus. ``Event.trace_id`` is a UUID by
contract, but one publisher handed over a plain string and ``.hex`` raised
inside the wildcard subscriber on every dictation (2026-08-27) — logged as a
failure, and the event never reached the flight-recorder file.
"""

from __future__ import annotations

import json

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import DictationTranscribing
from jarvis.telemetry import FlightRecorder


@pytest.mark.asyncio
async def test_a_string_trace_id_is_recorded_not_dropped(tmp_path) -> None:
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.attach(bus)

    await bus.publish(
        DictationTranscribing(source_layer="speech.dictation", trace_id="not-a-uuid")  # type: ignore[arg-type]
    )
    await rec.flush()
    await rec.close()

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, "the event was dropped"
    record = json.loads(lines[0])
    assert record["event"] == "DictationTranscribing"
    assert record["trace_id"] == "not-a-uuid"
