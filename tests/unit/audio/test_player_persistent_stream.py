"""Unit tests for AudioPlayer's persistent OutputStream across play_chunks calls.

Background: 2026-05-16 TTS time-stretch diagnosis (5-agent deep-dive,
see docs/diagnostics/tts-stretch-2026-05-16.html). The streaming-TTS
pipeline calls ``play_chunks`` once per sentence. The original
implementation closed the ``sd.OutputStream`` in a ``finally`` at the
end of every ``play_chunks`` call, so every sentence boundary triggered
a fresh stream-open + WASAPI prebuffer + scipy ``resample_poly`` FIR
ringing — perceived as phonemes being elongated and duplicated
("Hallo" → "haaaaa lalala oooo").

The fix hoists the stream lifecycle to instance fields
(``_active_stream``, ``_active_source_rate``, ``_active_device_rate``)
so it survives between ``play_chunks`` invocations. The stream is only
torn down when:

  * the next call comes in with a different source sample rate, or
  * ``stop()`` is called (barge-in).

These tests pin that contract.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest

import jarvis.audio.player as player_module
from jarvis.audio.player import AudioPlayer
from jarvis.core.protocols import AudioChunk


async def _one_chunk(pcm: bytes, sample_rate: int = 24_000) -> AsyncIterator[AudioChunk]:
    yield AudioChunk(pcm=pcm, sample_rate=sample_rate, timestamp_ns=0, channels=1)


def _make_player(monkeypatch) -> tuple[AudioPlayer, list[str]]:
    """Build an AudioPlayer with stream IO monkeypatched to record events.

    Returns (player, events). Each call to ``_open_output_stream`` appends
    ``"open"`` and each ``_close_output_stream`` appends ``"close"`` —
    the test asserts on the count.
    """
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

    events: list[str] = []

    fake_stream_counter = {"n": 0}

    def fake_open(needed_rate: int):
        fake_stream_counter["n"] += 1
        events.append(f"open@{needed_rate}#{fake_stream_counter['n']}")
        # Return a sentinel + the same device rate (no resample needed).
        return (object(), needed_rate)

    def fake_close(stream):
        events.append("close")

    def fake_write(stream, arr, src_rate, dev_rate):
        events.append(f"write@{src_rate}")

    monkeypatch.setattr(player, "_open_output_stream", fake_open)
    monkeypatch.setattr(player, "_close_output_stream", fake_close)
    monkeypatch.setattr(player, "_write_samples", fake_write)

    return player, events


@pytest.mark.asyncio
async def test_two_sequential_play_chunks_share_one_stream(monkeypatch) -> None:
    """Two ``play_chunks`` calls at the same sample rate must reuse the
    same OutputStream. Pre-fix behaviour was: open, write, close, open,
    write, close (2× open). Post-fix: open, write, write (1× open, 0× close
    until ``stop()`` or rate-change).
    """
    player, events = _make_player(monkeypatch)

    pcm = b"\x01\x00" * 4000  # ~333 ms at 24 kHz
    await player.play_chunks(_one_chunk(pcm))
    await player.play_chunks(_one_chunk(pcm))

    opens = [e for e in events if e.startswith("open@")]
    closes = [e for e in events if e == "close"]
    writes = [e for e in events if e.startswith("write@")]

    assert len(opens) == 1, f"expected 1 stream open across two calls, got {opens}"
    assert len(closes) == 0, (
        f"stream must stay open between calls — got {len(closes)} close events"
    )
    assert len(writes) == 2, f"both calls should have written, got {writes}"
    assert player._active_stream is not None, "active stream must survive call return"
    assert player._active_source_rate == 24_000


@pytest.mark.asyncio
async def test_sample_rate_change_triggers_reopen(monkeypatch) -> None:
    """When the source sample rate changes between two calls (e.g. Gemini
    24 kHz → SAPI5 22 kHz fallback), the old stream must be closed and a
    new one opened at the new rate.
    """
    player, events = _make_player(monkeypatch)

    pcm_24k = b"\x01\x00" * 4000
    pcm_22k = b"\x02\x00" * 4000

    await player.play_chunks(_one_chunk(pcm_24k, sample_rate=24_000))
    await player.play_chunks(_one_chunk(pcm_22k, sample_rate=22_050))

    opens = [e for e in events if e.startswith("open@")]
    closes = [e for e in events if e == "close"]

    assert len(opens) == 2, f"rate change should reopen — got opens={opens}"
    assert len(closes) == 1, (
        f"old stream should be closed on rate change — got closes={len(closes)}"
    )
    assert player._active_source_rate == 22_050


@pytest.mark.asyncio
async def test_stop_aborts_active_stream(monkeypatch) -> None:
    """``stop()`` must abort the persistent stream — the old implementation
    only called ``sd.stop()`` which is a no-op for ``sd.OutputStream``
    instances opened via ``play_chunks``. Barge-in was therefore silent.
    """
    player, events = _make_player(monkeypatch)

    abort_calls = {"n": 0}
    close_calls = {"n": 0}

    class FakeStream:
        def abort(self) -> None:
            abort_calls["n"] += 1

        def close(self) -> None:
            close_calls["n"] += 1

    # Override fake_open to yield a stream whose .abort() and .close() we count.
    fake_stream_counter = {"n": 0}

    def fake_open_real(needed_rate: int):
        fake_stream_counter["n"] += 1
        events.append(f"open@{needed_rate}#{fake_stream_counter['n']}")
        return (FakeStream(), needed_rate)

    monkeypatch.setattr(player, "_open_output_stream", fake_open_real)

    pcm = b"\x01\x00" * 4000
    await player.play_chunks(_one_chunk(pcm))
    assert player._active_stream is not None, "stream should be open before stop()"

    player.stop()

    assert abort_calls["n"] == 1, "stop() must call stream.abort() exactly once"
    assert close_calls["n"] == 1, "stop() must also call stream.close()"
    assert player._active_stream is None, "active fields must reset after stop()"
    assert player._active_source_rate is None
    assert player._active_device_rate is None


@pytest.mark.asyncio
async def test_stop_is_idempotent_when_no_stream(monkeypatch) -> None:
    """Calling ``stop()`` with no active stream must not raise — it's
    legitimate to call stop() defensively even when nothing is playing.
    """
    player, _ = _make_player(monkeypatch)
    assert player._active_stream is None
    player.stop()  # must not raise
    player.stop()  # idempotent


@pytest.mark.asyncio
async def test_stop_rejects_stream_that_finishes_opening_after_cancel(
    monkeypatch,
) -> None:
    """A worker-thread stream open cannot resurrect stopped playback."""

    player, events = _make_player(monkeypatch)
    open_entered = threading.Event()
    release_open = threading.Event()

    def delayed_open(needed_rate: int):
        events.append(f"open@{needed_rate}")
        open_entered.set()
        assert release_open.wait(timeout=1.0)
        return object(), needed_rate

    monkeypatch.setattr(player, "_open_output_stream", delayed_open)
    if player_module.sd is not None:
        monkeypatch.setattr(player_module.sd, "stop", lambda: None)

    pcm = b"\x01\x00" * 4000
    play_task = asyncio.create_task(player.play_chunks(_one_chunk(pcm)))
    assert await asyncio.to_thread(open_entered.wait, 1.0)

    player.stop()
    release_open.set()

    assert await asyncio.wait_for(play_task, timeout=1.0) is False
    assert [event for event in events if event == "close"] == ["close"]
    assert [event for event in events if event.startswith("write@")] == []
    assert player._active_stream is None


@pytest.mark.asyncio
async def test_multi_sentence_brain_response_keeps_one_stream(monkeypatch) -> None:
    """Simulates the streaming-TTS pipeline: 5 sentences in one brain
    response = 5 sequential ``play_chunks`` calls at the same rate.
    Must result in exactly 1 stream open, 5 writes, 0 closes — the entire
    point of the persistent-stream fix.
    """
    player, events = _make_player(monkeypatch)

    pcm = b"\x01\x00" * 4000
    for _ in range(5):
        await player.play_chunks(_one_chunk(pcm))

    opens = [e for e in events if e.startswith("open@")]
    closes = [e for e in events if e == "close"]
    writes = [e for e in events if e.startswith("write@")]

    assert len(opens) == 1, (
        f"5-sentence response should open ONE stream, got {len(opens)}: {opens}"
    )
    assert len(closes) == 0, (
        f"no closes until stop() or rate-change — got {len(closes)} closes"
    )
    assert len(writes) == 5, f"all 5 sentences must reach the writer, got {writes}"
