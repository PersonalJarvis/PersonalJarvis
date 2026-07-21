"""Mid-reply audio-stall attribution: silent provider vs. starved event loop.

The stall diagnostic in ``RealtimeVoiceSession._note_audio_flow`` measures
the gap between provider audio ARRIVALS — but arrival is when our event
loop reads the socket. Heavy concurrent work in this process produces the
identical "provider sent no audio" signature while the audio sits unread
in the socket buffer (live run 2026-07-21 08:40: a 54 s wiki-consolidator
Codex turn finished right as a 1850 ms stall began). The ``_LoopLagProbe``
separates the two so the log names the actual producer.
"""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from jarvis.realtime.session import (
    RealtimeVoiceSession,
    _LoopLagProbe,
)


class _IdleGate:
    last_hold_ms = 0.0
    pending_audio_ms = 0.0


def _bare_session(loop_lag_ms: float) -> RealtimeVoiceSession:
    """Session skeleton carrying only the fields _note_audio_flow reads."""
    session = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    session.session_id = "test-session"
    session._turn_id = "turn-1"
    session._last_audio_emit_turn = "turn-1"
    session._output_samples_sent = 1
    session._last_audio_emit_monotonic = time.monotonic() - 1.85
    session._embedded_silence_ms = 0.0
    session._gate = _IdleGate()

    class _FixedProbe:
        def max_lag_ms(self, window_s: float) -> float:
            return loop_lag_ms

    session._loop_lag = _FixedProbe()
    return session


def _stall_line(caplog) -> str:
    lines = [
        record.getMessage()
        for record in caplog.records
        if "mid-reply audio stalled" in record.getMessage()
    ]
    assert lines, "a 1850 ms arrival gap must emit the stall diagnostic"
    return lines[0]


def test_responsive_loop_blames_the_provider(caplog) -> None:
    session = _bare_session(loop_lag_ms=12.0)
    with caplog.at_level(logging.INFO, logger="jarvis.realtime.session"):
        session._note_audio_flow(b"\x00\x10" * 480, _FakeChunk())
    assert "the provider sent no audio" in _stall_line(caplog)


def test_starved_loop_is_named_instead_of_the_provider(caplog) -> None:
    session = _bare_session(loop_lag_ms=1_700.0)
    with caplog.at_level(logging.INFO, logger="jarvis.realtime.session"):
        session._note_audio_flow(b"\x00\x10" * 480, _FakeChunk())
    line = _stall_line(caplog)
    assert "event loop stalled" in line
    assert "provider sent no audio" not in line


class _FakeChunk:
    sample_rate = 24_000


@pytest.mark.asyncio
async def test_loop_lag_probe_measures_a_blocked_loop() -> None:
    probe = _LoopLagProbe()
    probe.start()
    try:
        # Let the probe take at least one clean sample, then block the loop.
        # The worst-phase measurable lag is block − interval (a sample can
        # become due right at the block's start), so a 0.7 s block yields at
        # least ~450 ms of recorded lag against the 0.25 s interval.
        await asyncio.sleep(0.3)
        # A synchronous sleep is the point of this test: it blocks the loop
        # so the probe's own sleep cannot be serviced.
        time.sleep(0.7)  # noqa: ASYNC251
        await asyncio.sleep(0.3)  # one post-block sample records the lag
        assert probe.max_lag_ms(window_s=5.0) >= 300.0
    finally:
        probe.stop()


@pytest.mark.asyncio
async def test_loop_lag_probe_stays_quiet_on_a_healthy_loop() -> None:
    probe = _LoopLagProbe()
    probe.start()
    try:
        await asyncio.sleep(0.6)
        assert probe.max_lag_ms(window_s=5.0) < 200.0
    finally:
        probe.stop()
