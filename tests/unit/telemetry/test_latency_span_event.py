"""Tests for the LatencySpan event and its LatencyPhase wire vocabulary.

LatencySpan is the measurement primitive for the voice hot path (Wave 0 of the
omni-latency suite). LatencyPhase is the single source of truth for the phase
names; a runtime guard rejects unknown phases to prevent the BUG-008 enum-drift
class from recurring on a new wire-format string.
"""
from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest

from jarvis.core.events import Event, LatencyPhase, LatencySpan


def test_latency_span_inherits_event_base_and_carries_timing() -> None:
    trace = uuid4()
    span = LatencySpan(
        trace_id=trace,
        source_layer="speech.pipeline",
        phase=LatencyPhase.STT_FINALIZE,
        duration_ms=42.5,
        t_start_ns=1_000,
        t_end_ns=43_500,
    )
    assert isinstance(span, Event)
    assert span.trace_id == trace
    assert isinstance(span.timestamp_ns, int)
    assert span.phase == LatencyPhase.STT_FINALIZE
    assert span.duration_ms == 42.5
    assert span.t_start_ns == 1_000
    assert span.t_end_ns == 43_500


def test_latency_span_is_frozen() -> None:
    span = LatencySpan(phase=LatencyPhase.INTENT_DECISION, duration_ms=1.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        span.duration_ms = 99.0  # type: ignore[misc]


def test_latency_span_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="unknown latency phase"):
        LatencySpan(phase="totally-not-a-phase", duration_ms=1.0)


def test_latency_phase_is_a_string_enum_source_of_truth() -> None:
    # StrEnum members ARE strings (clean JSONL serialization in the recorder).
    assert LatencyPhase.BRAIN_FIRST_TOKEN == "brain_first_token"  # noqa: S105
    expected = {
        "stt_finalize",
        "intent_decision",
        "ack_first_token",
        "ack_first_audio",
        "brain_first_token",
        "brain_first_audio",
        "turn_to_first_audio",
        # LATENCY_REPORT_001 t0..t9 diagnostic milestones (marked on the
        # streaming hot path; consumed by latency_log._DURATION_PAIRS).
        "stt_first_partial",
        "brain_request_sent",
        "brain_last_token",
        "tts_request_sent",
        "tts_first_chunk",
        "tts_stream_done",
        # Realtime voice mode (OpenAI Realtime / Gemini Live) milestones.
        "realtime_input_committed",
        "realtime_routing_decision",
        "realtime_first_transcript",
        "realtime_first_audio",
        "realtime_delegate_started",
        "realtime_delegate_completed",
        "realtime_tool_completed",
        "realtime_scrub_cancel",
        "realtime_cancel",
        "realtime_turn_complete",
    }
    assert {p.value for p in LatencyPhase} == expected


def test_latency_span_accepts_plain_string_phase_from_source_of_truth() -> None:
    # Callers may pass the StrEnum or its bare string value; both validate.
    span = LatencySpan(phase="turn_to_first_audio", duration_ms=1234.0)
    assert span.phase == LatencyPhase.TURN_TO_FIRST_AUDIO
