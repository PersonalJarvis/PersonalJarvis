from __future__ import annotations

import asyncio

import numpy as np
import pytest

from jarvis.audio.vad import VAD_FRAME_SAMPLES
from jarvis.realtime.desktop import (
    DesktopRealtimeBargeInDetector,
    DesktopRealtimePlayback,
)


class FakePlayer:
    def __init__(self) -> None:
        self.chunks = []
        self.stopped = 0

    async def play_chunks(self, chunks) -> None:
        async for chunk in chunks:
            self.chunks.append(chunk)

    def stop(self) -> None:
        self.stopped += 1


class FakeVadModel:
    def __init__(self, probabilities: list[float]) -> None:
        self.probabilities = list(probabilities)
        self.warmed = False

    def _ensure_model(self) -> None:
        self.warmed = True

    def _prob(self, _frame: np.ndarray) -> float:
        return self.probabilities.pop(0)


def _pcm_frames(*amplitudes: int) -> bytes:
    return np.concatenate(
        [np.full(VAD_FRAME_SAMPLES, value, dtype=np.dtype("<i2")) for value in amplitudes]
    ).tobytes()


def test_cpu_barge_detector_returns_prespeech_and_confirmed_user_audio() -> None:
    model = FakeVadModel([0.1, 0.2, 0.99, 0.99, 0.99])
    detector = DesktopRealtimeBargeInDetector(
        min_frame_rms=0.0,
        grace_s=0,
        consecutive_frames=3,
        pre_speech_frames=2,
        model=model,
    )
    detector.warmup()
    detector.start_output()

    detected = detector.feed(_pcm_frames(1, 2, 3, 4, 5))

    assert model.warmed is True
    assert detected == _pcm_frames(1, 2, 3, 4, 5)
    assert detector.active is False


def test_cpu_barge_detector_rejects_short_speaker_bleed() -> None:
    model = FakeVadModel([0.99, 0.1, 0.99, 0.99, 0.1])
    detector = DesktopRealtimeBargeInDetector(
        min_frame_rms=0.0,
        grace_s=0,
        consecutive_frames=3,
        model=model,
    )
    detector.warmup()
    detector.start_output()

    assert detector.feed(_pcm_frames(1, 2, 3, 4, 5)) is None
    assert detector.active is True


@pytest.mark.asyncio
async def test_turn_uses_one_stream_and_drains_in_order():
    player = FakePlayer()
    playback = DesktopRealtimePlayback(player)

    await playback.send_binary(b"\x01\x00" * 8)
    await playback.send_binary(b"\x02\x00" * 8)
    await playback.finish_turn()

    assert [chunk.pcm for chunk in player.chunks] == [
        b"\x01\x00" * 8,
        b"\x02\x00" * 8,
    ]
    assert all(chunk.sample_rate == 24_000 for chunk in player.chunks)


@pytest.mark.asyncio
async def test_cancel_stops_player_and_discards_queued_audio():
    gate = asyncio.Event()

    class SlowPlayer(FakePlayer):
        async def play_chunks(self, chunks) -> None:
            async for chunk in chunks:
                await gate.wait()
                self.chunks.append(chunk)

    player = SlowPlayer()
    playback = DesktopRealtimePlayback(player)
    await playback.send_binary(b"\x01\x00" * 8)

    await playback.cancel()

    assert player.stopped == 1
    assert player.chunks == []


@pytest.mark.asyncio
async def test_close_is_terminal_and_rejects_late_provider_audio() -> None:
    """A dead desktop surface cannot be reopened by a stale callback."""

    player = FakePlayer()
    playback = DesktopRealtimePlayback(player)

    await playback.send_binary(b"\x01\x00" * 8)
    await playback.finish_turn()
    await playback.close()
    await playback.send_binary(b"\x09\x00" * 8)
    await playback.finish_turn()

    assert [chunk.pcm for chunk in player.chunks] == [b"\x01\x00" * 8]
    assert player.stopped == 1


@pytest.mark.asyncio
async def test_cancel_can_interrupt_a_turn_already_draining() -> None:
    stop_event = asyncio.Event()

    class SlowPlayer(FakePlayer):
        def stop(self) -> None:
            super().stop()
            stop_event.set()

        async def play_chunks(self, chunks) -> None:
            async for _chunk in chunks:
                await stop_event.wait()
                return

    player = SlowPlayer()
    playback = DesktopRealtimePlayback(player)
    await playback.send_binary(b"\x01\x00" * 8)
    drain = asyncio.create_task(playback.finish_turn())
    await asyncio.sleep(0)

    await playback.cancel()
    await drain

    assert player.stopped == 1
    assert player.chunks == []


@pytest.mark.asyncio
async def test_cancel_during_drain_treats_stopped_stream_as_expected() -> None:
    stop_event = asyncio.Event()

    class AbortedStreamPlayer(FakePlayer):
        def stop(self) -> None:
            super().stop()
            stop_event.set()

        async def play_chunks(self, chunks) -> None:
            async for _chunk in chunks:
                await stop_event.wait()
                raise RuntimeError("Stream is stopped [PaErrorCode -9983]")

    player = AbortedStreamPlayer()
    playback = DesktopRealtimePlayback(player)
    await playback.send_binary(b"\x01\x00" * 8)
    drain = asyncio.create_task(playback.finish_turn())
    await asyncio.sleep(0)

    await playback.cancel()
    await drain

    assert player.stopped == 1


@pytest.mark.asyncio
async def test_finish_turn_still_surfaces_an_unrelated_playback_failure() -> None:
    class FailingPlayer(FakePlayer):
        async def play_chunks(self, chunks) -> None:
            async for _chunk in chunks:
                raise RuntimeError("output device disappeared")

    playback = DesktopRealtimePlayback(FailingPlayer())
    await playback.send_binary(b"\x01\x00" * 8)

    with pytest.raises(RuntimeError, match="output device disappeared"):
        await playback.finish_turn()


@pytest.mark.asyncio
async def test_handshake_sample_rate_is_used_for_the_next_turn():
    player = FakePlayer()
    playback = DesktopRealtimePlayback(player)

    playback.set_sample_rate(16_000)
    await playback.send_binary(b"\x01\x00" * 8)
    await playback.finish_turn()

    assert [chunk.sample_rate for chunk in player.chunks] == [16_000]


def test_energy_pre_gate_skips_onnx_for_quiet_frames() -> None:
    # BUG-062: quiet frames (silence / hiss / moderate speaker echo) must
    # never reach the Silero model — that is both the loop-load fix (stutter
    # on slow CPUs) and the self-barge-in damper on speakers+mic laptops.
    calls: list[int] = []

    class _CountingModel:
        def _ensure_model(self) -> None: ...

        def _prob(self, _frame) -> float:
            calls.append(1)
            return 1.0

    detector = DesktopRealtimeBargeInDetector(
        grace_s=0.0, consecutive_frames=1, model=_CountingModel()
    )
    detector.warmup()
    detector.start_output()
    # Amplitude 100/32768 ≈ 0.003 RMS — below the 0.010 floor.
    assert detector.feed(_pcm_frames(100, 100, 100)) is None
    assert calls == []  # ONNX never consulted


def test_energy_pre_gate_passes_loud_speech_through() -> None:
    calls: list[int] = []

    class _CountingModel:
        def _ensure_model(self) -> None: ...

        def _prob(self, _frame) -> float:
            calls.append(1)
            return 1.0

    detector = DesktopRealtimeBargeInDetector(
        grace_s=0.0, consecutive_frames=1, model=_CountingModel()
    )
    detector.warmup()
    detector.start_output()
    # Amplitude 2000/32768 ≈ 0.061 RMS — normal speaking volume.
    assert detector.feed(_pcm_frames(2000)) is not None
    assert calls  # the model ran and confirmed


# --- BUG-084: adaptive echo floor ----------------------------------------- #
# A FIXED RMS floor cannot cover every speaker/mic coupling: on built-in
# laptop speakers next to the built-in mic the assistant's own voice lands far
# above the static 0.010 gate, Silero (which cannot tell whose voice it hears)
# confirms, and the false barge truncates the answer and seeds the self-talk
# loop. The detector therefore calibrates a per-answer echo floor from the
# grace-window frames (pure speaker echo by construction) and keeps updating
# it from a LAGGED rolling window, so echo at the calibrated loudness is
# gated while a user speaking clearly above it still confirms.


class _AlwaysSpeechModel:
    """Silero stand-in that calls every frame 'speech' — mirrors the real
    model's behavior on the assistant's own voice, so ONLY the energy gate
    can tell echo and user apart."""

    def __init__(self) -> None:
        self.calls = 0

    def _ensure_model(self) -> None: ...

    def _prob(self, _frame) -> float:
        self.calls += 1
        return 1.0


def _echo_calibrated_detector(
    *, echo_amplitude: int, grace_frames: int = 30, **kwargs
) -> tuple[DesktopRealtimeBargeInDetector, _AlwaysSpeechModel]:
    model = _AlwaysSpeechModel()
    detector = DesktopRealtimeBargeInDetector(grace_s=60.0, model=model, **kwargs)
    detector.warmup()
    detector.start_output()
    # Grace window: playback echo only — calibration data, never preroll.
    assert detector.feed(_pcm_frames(*[echo_amplitude] * grace_frames)) is None
    # End the grace period deterministically (no sleeps in unit tests).
    detector._started_at = -1e9
    return detector, model


def test_adaptive_floor_gates_echo_louder_than_the_static_floor() -> None:
    # Echo at amplitude 2000 (~0.061 RMS) sails over the static 0.010 gate —
    # exactly the Mac speakers+mic case. After grace calibration the learned
    # floor (~1.4 × 0.061) must keep gating it: no ONNX call, no confirm.
    detector, model = _echo_calibrated_detector(
        echo_amplitude=2000, consecutive_frames=3
    )
    assert detector.feed(_pcm_frames(*[2000] * 20)) is None
    assert model.calls == 0  # pure echo never reached the model
    # A user speaking clearly above the echo still confirms.
    assert detector.feed(_pcm_frames(*[6000] * 5)) is not None
    assert model.calls >= 3


def test_adaptive_floor_lag_lets_sustained_user_speech_confirm() -> None:
    # Quiet room during grace → floor stays at the static minimum. Sustained
    # genuine speech must then confirm even though its own frames enter the
    # rolling history: the lag excludes them from their own baseline, so
    # speech can never raise its own bar before the confirm run completes.
    detector, model = _echo_calibrated_detector(
        echo_amplitude=100, consecutive_frames=12
    )
    assert detector.feed(_pcm_frames(*[2000] * 12)) is not None
    assert model.calls >= 12


def test_adaptive_floor_disabled_with_zero_min_rms() -> None:
    # min_frame_rms=0.0 is the explicit logic-test / opt-out hook: it must
    # disable the adaptive floor as well, not only the static gate.
    detector, model = _echo_calibrated_detector(
        echo_amplitude=2000, consecutive_frames=3, min_frame_rms=0.0
    )
    assert detector.feed(_pcm_frames(*[2000] * 3)) is not None
    assert model.calls >= 3


def test_start_output_resets_echo_calibration() -> None:
    detector, _model = _echo_calibrated_detector(
        echo_amplitude=2000, consecutive_frames=3
    )
    assert len(detector._rms_history) > 0
    detector.start_output()
    assert len(detector._rms_history) == 0


def test_synthesis_delay_cannot_consume_echo_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grace clock starts only once output is physically active."""
    now = 100.0
    output_is_active = False
    monkeypatch.setattr(
        "jarvis.realtime.desktop.time.monotonic", lambda: now
    )
    model = _AlwaysSpeechModel()
    detector = DesktopRealtimeBargeInDetector(
        grace_s=1.5,
        consecutive_frames=3,
        output_active=lambda: output_is_active,
        model=model,
    )
    detector.warmup()
    detector.start_output()

    # Surface synthesis and stream setup can exceed the nominal grace period.
    # Loud microphone frames during that lead-in must remain local without
    # arming Silero or aging the calibration clock.
    now = 102.0
    assert detector.feed(_pcm_frames(*[2000] * 20)) is None
    assert model.calls == 0
    assert len(detector._rms_history) == 0

    # Physical playback begins now. Its first 1.5 seconds are a fresh echo-only
    # calibration window even though start_output() happened two seconds ago.
    output_is_active = True
    assert detector.feed(_pcm_frames(*[2000] * 30)) is None
    assert model.calls == 0
    assert len(detector._rms_history) == 30

    now = 104.0
    assert detector.feed(_pcm_frames(*[2000] * 20)) is None
    assert model.calls == 0


def test_grace_frames_calibrate_but_never_become_preroll() -> None:
    detector, model = _echo_calibrated_detector(
        echo_amplitude=2000, grace_frames=10, consecutive_frames=3
    )
    # Calibration data was collected …
    assert len(detector._rms_history) == 10
    # … but nothing from the grace window is buffered as user preroll and
    # the model was never consulted during grace.
    assert len(detector._pre_buffer) == 0
    assert detector._candidate_frames == []
    assert model.calls == 0


# --- BUG-101: output-envelope correlation gate -------------------------------


def _ramp_reference(now: float, *, span_s: float = 2.5) -> list[tuple[float, float, float]]:
    """A played-output envelope rising linearly until ``now`` (60 ms blocks)."""
    block = 0.06
    count = int(span_s / block)
    return [
        (now - span_s + index * block, block, 0.001 + 0.002 * index)
        for index in range(count)
    ]


def _corr_detector(
    snapshot, probabilities: int = 12
) -> DesktopRealtimeBargeInDetector:
    model = FakeVadModel([0.99] * probabilities)
    detector = DesktopRealtimeBargeInDetector(
        min_frame_rms=0.0,
        grace_s=0,
        consecutive_frames=12,
        pre_speech_frames=2,
        echo_reference_snapshot=snapshot,
        model=model,
    )
    detector.warmup()
    detector.start_output()
    return detector


def test_echo_gate_suppresses_candidate_tracking_the_output_envelope() -> None:
    import time as _time

    now = _time.monotonic()
    reference = _ramp_reference(now + 0.5)
    # Candidate loudness rises linearly exactly like the played output — a
    # shifted linear ramp correlates ~1.0 at some lag in the search window.
    ramp = _pcm_frames(*[1000 * (index + 1) for index in range(12)])

    detector = _corr_detector(lambda _window: reference)
    assert detector.feed(ramp) is None
    # Suppressed as echo, but the detector stays armed for a real user.
    assert detector.active is True


def test_echo_gate_passes_candidate_uncorrelated_with_output() -> None:
    import time as _time

    now = _time.monotonic()
    reference = _ramp_reference(now + 0.5)
    # Alternating loud/quiet speech does not track the rising output ramp.
    alternating = _pcm_frames(
        *[9000 if index % 2 == 0 else 1500 for index in range(12)]
    )

    detector = _corr_detector(lambda _window: reference)
    detected = detector.feed(alternating)
    assert detected == alternating
    assert detector.active is False


def test_echo_gate_fails_open_without_reference_data() -> None:
    ramp = _pcm_frames(*[1000 * (index + 1) for index in range(12)])
    detector = _corr_detector(lambda _window: [])
    assert detector.feed(ramp) == ramp


def test_echo_gate_fails_open_when_snapshot_raises() -> None:
    def _broken(_window: float) -> list[tuple[float, float, float]]:
        raise RuntimeError("tap unavailable")

    ramp = _pcm_frames(*[1000 * (index + 1) for index in range(12)])
    detector = _corr_detector(_broken)
    assert detector.feed(ramp) == ramp


def test_echo_gate_suppression_keeps_scanning_for_real_speech() -> None:
    import time as _time

    now = _time.monotonic()
    reference = _ramp_reference(now + 1.0)
    ramp = _pcm_frames(*[1000 * (index + 1) for index in range(12)])
    alternating = _pcm_frames(
        *[9000 if index % 2 == 0 else 1500 for index in range(12)]
    )

    detector = _corr_detector(lambda _window: reference, probabilities=24)
    assert detector.feed(ramp) is None
    detected = detector.feed(alternating)
    assert detected is not None
    assert detector.active is False
