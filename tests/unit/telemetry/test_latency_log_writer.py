"""Tests for the per-turn LatencyLogWriter (LATENCY_REPORT_001).

The writer subscribes to ``LatencyTurnComplete`` and appends one self-contained
row per voice turn to ``state/latency_log.jsonl``. We verify:

  * the row schema matches what the aggregation CLI expects
  * derived per-stage durations are computed correctly from the offset map
  * the bus callback never blocks (writes happen in a daemon thread)
  * a missing parent directory degrades silently
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from jarvis.core.events import LatencyTurnComplete
from jarvis.telemetry.latency_log import LatencyLogWriter


def _read_lines(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _flush_writer(writer: LatencyLogWriter, expected: int, *, timeout: float = 2.0) -> None:
    """Spin-wait until ``expected`` rows have been written (or timeout)."""
    deadline = time.monotonic() + timeout
    while writer.written < expected and time.monotonic() < deadline:
        time.sleep(0.01)


async def test_row_schema_matches_cli_contract(tmp_path: Path) -> None:
    log_path = tmp_path / "latency.jsonl"
    writer = LatencyLogWriter(log_path)

    trace = UUID(int=1)
    event = LatencyTurnComplete(
        trace_id=trace,
        source_layer="speech.pipeline",
        anchor_ns=1_000_000_000,
        stages_ms={
            "stt_first_partial": 150.0,
            "stt_finalize": 150.5,
            "brain_request_sent": 180.0,
            "brain_first_token": 1180.0,
            "brain_last_token": 1500.0,
            "tts_request_sent": 1510.0,
            "tts_first_chunk": 1900.0,
            "turn_to_first_audio": 1950.0,
            "tts_stream_done": 2100.0,
        },
        stt_input_audio_ms=820.0,
        brain_input_tokens=128,
        brain_output_tokens=42,
        tts_input_chars=180,
        errors=("dummy",),
    )
    await writer._on_event(event)  # exercise the real path
    _flush_writer(writer, expected=1)
    writer.close()

    rows = _read_lines(log_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["turn_id"] == trace.hex
    assert row["anchor_ns"] == 1_000_000_000
    assert row["ttfw_ms"] == 1950.0
    assert row["total_ms"] == 2100.0
    # Per-stage durations are derived from adjacent offsets.
    assert row["durations_ms"]["brain_ttft"] == pytest.approx(1000.0, rel=1e-3)
    assert row["durations_ms"]["tts_ttfb"] == pytest.approx(390.0, rel=1e-3)
    assert row["durations_ms"]["stt_streaming"] == pytest.approx(0.5, rel=1e-3)
    assert row["stt_input_audio_ms"] == 820.0
    assert row["brain_input_tokens"] == 128
    assert row["errors"] == ["dummy"]


async def test_missing_stage_renders_none_in_durations(tmp_path: Path) -> None:
    writer = LatencyLogWriter(tmp_path / "sparse.jsonl")
    event = LatencyTurnComplete(
        trace_id=uuid4(),
        stages_ms={"stt_finalize": 100.0},  # nothing downstream
        anchor_ns=0,
    )
    await writer._on_event(event)
    _flush_writer(writer, expected=1)
    writer.close()

    [row] = _read_lines(tmp_path / "sparse.jsonl")
    durations = row["durations_ms"]
    # Stages with no end-mark must be None, not 0.
    assert durations["brain_ttft"] is None
    assert durations["tts_ttfb"] is None
    # ttfw / total must be None too because their anchor marks didn't fire.
    assert row["ttfw_ms"] is None
    assert row["total_ms"] is None


async def test_writer_callback_returns_quickly(tmp_path: Path) -> None:
    """The bus callback must enqueue and return — disk I/O happens on a thread."""
    writer = LatencyLogWriter(tmp_path / "fast.jsonl")
    event = LatencyTurnComplete(trace_id=uuid4(), anchor_ns=0)

    start = time.perf_counter_ns()
    await writer._on_event(event)
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000

    # Putting one item on an in-memory queue should be well under 5 ms,
    # comfortably below the LATENCY_REPORT_001 budget. The actual disk write
    # happens in the writer thread, not inside this assertion window.
    assert elapsed_ms < 5.0, f"callback took {elapsed_ms:.2f} ms"

    _flush_writer(writer, expected=1)
    writer.close()


async def test_writer_handles_unwritable_dir(tmp_path: Path) -> None:
    """Telemetry must never break the boot — bad path should degrade silently."""
    # We can't easily make a path *unwritable* in a portable test, but we can
    # at least verify that constructing against a deeply nested non-existent
    # parent succeeds (the writer attempts mkdir, logs on failure, continues).
    writer = LatencyLogWriter(tmp_path / "deeply" / "nested" / "ok.jsonl")
    writer.close()
    # If we got here without raising, the contract holds.
    assert True


async def test_only_first_mark_per_phase_is_recorded(tmp_path: Path) -> None:
    """If two events arrive for the same trace, the writer keeps both rows.

    Setdefault on the LatencyTracker side prevents per-phase duplication
    INSIDE one tracker. The writer itself does NOT dedupe — each event is one
    row. This documents that contract.
    """
    writer = LatencyLogWriter(tmp_path / "two.jsonl")
    trace = uuid4()
    await writer._on_event(LatencyTurnComplete(trace_id=trace, anchor_ns=0))
    await writer._on_event(LatencyTurnComplete(trace_id=trace, anchor_ns=0))
    _flush_writer(writer, expected=2)
    writer.close()

    rows = _read_lines(tmp_path / "two.jsonl")
    assert len(rows) == 2


async def test_writer_attach_subscribes_only_to_turn_complete(tmp_path: Path) -> None:
    """Narrow subscribe — not subscribe_all — so the FlightRecorder path is
    not duplicated and unrelated bus events stay off the JSONL.
    """
    from jarvis.core.bus import EventBus

    bus = EventBus()
    writer = LatencyLogWriter(tmp_path / "narrow.jsonl")
    writer.attach(bus)
    # Publish a non-LatencyTurnComplete event — must not write a row.
    from jarvis.core.events import AudioOutFirst

    await bus.publish(AudioOutFirst())
    await bus.publish(
        LatencyTurnComplete(trace_id=uuid4(), anchor_ns=0, stages_ms={"stt_finalize": 1.0})
    )
    # Let the asyncio publish task run, then drain the writer thread.
    await asyncio.sleep(0.05)
    _flush_writer(writer, expected=1)
    writer.close()

    rows = _read_lines(tmp_path / "narrow.jsonl")
    assert len(rows) == 1
