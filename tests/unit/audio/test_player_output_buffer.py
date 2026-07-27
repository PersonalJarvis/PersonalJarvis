"""Device-buffer depth: the only shock absorber for an event-loop stall.

Live forensic 2026-07-27 (realtime voice, gemini-live): replies came out with
400-800 ms holes mid-sentence. ``RealtimeSession._note_audio_flow`` attributed
half of them to "this process's event loop stalled 311-375 ms in the same
window" — while the loop is stalled nothing can move audio, so whatever sits
inside PortAudio is all that plays. Against the previous 200 ms buffer every
one of those stalls became silence.

These tests pin the two properties that follow from that: the reserved buffer
must outlast a measured stall, and the feed-dry de-click must not fire while
the device still holds plenty of audio.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np
import pytest

import jarvis.audio.player as player_module
from jarvis.audio.player import DEFAULT_OUTPUT_BUFFER_S, AudioPlayer
from jarvis.core.protocols import AudioChunk

#: Worst event-loop stall observed in the 2026-07-27 logs. The reserved buffer
#: has to outlast it, or the speaker runs dry before the loop comes back.
MEASURED_WORST_LOOP_STALL_S = 0.375


def _chunk(samples: np.ndarray, sample_rate: int = 24_000) -> AudioChunk:
    return AudioChunk(
        pcm=samples.astype(np.int16).tobytes(),
        sample_rate=sample_rate,
        timestamp_ns=0,
        channels=1,
    )


def _bare_player() -> AudioPlayer:
    """Player without ``__init__`` — mirrors the other player unit tests."""
    player = AudioPlayer.__new__(AudioPlayer)
    player._device = None
    player._sample_rate = 24_000
    player._channels = 1
    player._device_logged = True
    player._bus = None
    player._play_lock = None
    player._active_stream = None
    player._active_source_rate = None
    player._active_device_rate = None
    player._device_rate_cache = {}
    return player


def _writing_player(monkeypatch, buffer_s: float) -> tuple[AudioPlayer, list]:
    player = _bare_player()
    player._output_buffer_s = buffer_s
    written: list[np.ndarray] = []

    monkeypatch.setattr(
        player, "_open_output_stream", lambda rate: (object(), rate)
    )
    monkeypatch.setattr(player, "_close_output_stream", lambda stream: None)
    monkeypatch.setattr(
        player,
        "_write_samples",
        lambda stream, arr, src, dev, **_kw: written.append(
            np.array(arr, dtype=np.int16)
        ),
    )
    return player, written


def test_reserved_buffer_outlasts_the_measured_event_loop_stall() -> None:
    """The default buffer must survive the worst stall this process produced."""
    assert DEFAULT_OUTPUT_BUFFER_S > MEASURED_WORST_LOOP_STALL_S, (
        "an event-loop stall drains the device buffer with nothing able to "
        "refill it, so a buffer shallower than the measured stall is an "
        "audible hole in the middle of a spoken answer"
    )


def test_open_output_stream_reserves_the_configured_buffer(monkeypatch) -> None:
    player = _bare_player()
    player._output_buffer_s = 0.4
    captured: dict = {}

    class FakeStream:
        latency = 0.4

        def start(self) -> None:
            pass

    def fake_outputstream(**kw):
        captured.update(kw)
        return FakeStream()

    monkeypatch.setattr(
        player_module.sd, "query_devices", lambda d: {"default_samplerate": 48_000}
    )
    monkeypatch.setattr(player_module.sd, "OutputStream", fake_outputstream)

    player._open_output_stream(24_000)

    assert captured["latency"] == pytest.approx(0.4)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (None, DEFAULT_OUTPUT_BUFFER_S),
        (0.6, 0.6),
        (0.001, 0.1),      # clamped up: a USB headset underruns below ~100 ms
        (30.0, 2.0),       # clamped down: the UI animation would lead the voice
        ("nonsense", DEFAULT_OUTPUT_BUFFER_S),
        (float("nan"), DEFAULT_OUTPUT_BUFFER_S),
    ],
)
def test_requested_buffer_is_clamped_to_an_audible_range(requested, expected) -> None:
    assert player_module._clamp_output_buffer_s(requested) == pytest.approx(expected)


def test_stall_window_scales_with_the_device_buffer() -> None:
    """A deeper buffer waits proportionally longer before de-clicking."""
    shallow = _bare_player()
    shallow._output_buffer_s = 0.2
    deep = _bare_player()
    deep._output_buffer_s = 0.4

    assert shallow._feed_stall_window_s() == pytest.approx(
        player_module.FEED_STALL_FADE_S
    )
    assert deep._feed_stall_window_s() > shallow._feed_stall_window_s()
    assert deep._feed_stall_window_s() < 0.4, (
        "the ramp must still reach PortAudio before the buffer runs empty"
    )


@pytest.mark.asyncio
async def test_short_feed_gap_inside_the_buffer_injects_nothing(monkeypatch) -> None:
    """A provider hiccup the device buffer covers must stay inaudible.

    Ramping down here would carve a dip out of speech that was never going to
    break — with a deep buffer, ordinary network jitter is exactly this case.
    """
    player, written = _writing_player(monkeypatch, buffer_s=0.4)
    tone = np.full(4_000, 6_000, dtype=np.int16)

    async def briefly_stalling_feed() -> AsyncIterator[AudioChunk]:
        yield _chunk(tone)
        await asyncio.sleep(0.1)  # well inside the 0.4 s device buffer
        yield _chunk(tone)

    await player.play_chunks(briefly_stalling_feed())

    joined = np.concatenate(written)
    assert joined.tobytes() == np.tile(tone, 2).tobytes(), (
        "a gap the device buffer absorbs must reach the speaker byte-identical"
    )


@pytest.mark.asyncio
async def test_gap_past_the_window_still_gets_de_clicked(monkeypatch) -> None:
    """Once the buffer really is about to run dry, the edge is still ramped."""
    player, written = _writing_player(monkeypatch, buffer_s=0.4)
    tone = np.full(4_000, 6_000, dtype=np.int16)

    async def long_stalling_feed() -> AsyncIterator[AudioChunk]:
        yield _chunk(tone)
        await asyncio.sleep(player._feed_stall_window_s() + 0.1)
        yield _chunk(tone)

    await player.play_chunks(long_stalling_feed())

    assert len(written) == 3, (
        f"expected [audio, fade-out ramp, resumed audio], got {len(written)}"
    )
    assert written[1][-1] == 0, "the fade-out must end at silence"
