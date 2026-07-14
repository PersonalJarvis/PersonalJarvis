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
