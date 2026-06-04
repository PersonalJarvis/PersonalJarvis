"""Regression guard: AudioPlayer degrades gracefully when the machine has no
usable audio OUTPUT device.

Background (2026-06-04): on a laptop/VM without an audio endpoint, PortAudio
raises ``PaErrorCode -9999 'There is no driver installed on your system'`` at
``sd.OutputStream`` open. The old code re-raised it, so the voice pipeline
logged a full ERROR traceback on EVERY utterance/announcement. The fix latches
``_output_unavailable`` on the first such error, logs a single WARNING, and
makes ``play_pcm`` / ``play_chunks`` silent no-ops thereafter.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import sounddevice as sd

from jarvis.audio.player import AudioPlayer
from jarvis.core.protocols import AudioChunk


def _bare_player() -> AudioPlayer:
    """An AudioPlayer with device IO fields set, bypassing device resolution."""
    p = AudioPlayer.__new__(AudioPlayer)
    p._device = None
    p._sample_rate = 24_000
    p._channels = 1
    p._device_logged = True
    p._bus = None
    p._play_lock = None
    p._active_stream = None
    p._active_source_rate = None
    p._active_device_rate = None
    p._device_rate_cache = {}
    p._output_unavailable = False
    return p


async def _one_chunk(pcm: bytes) -> AsyncIterator[AudioChunk]:
    yield AudioChunk(pcm=pcm, sample_rate=24_000, timestamp_ns=0, channels=1)


@pytest.mark.asyncio
async def test_play_pcm_no_output_device_is_silent_noop(monkeypatch) -> None:
    """A -9999 PortAudioError must latch the player and NOT propagate."""
    player = _bare_player()
    opens: list[int] = []

    def _raise_no_driver(*args, **kwargs):
        opens.append(1)
        raise sd.PortAudioError(
            "Unanticipated host error [PaErrorCode -9999]: "
            "'There is no driver installed on your system.' [MME error 6]"
        )

    monkeypatch.setattr(sd, "OutputStream", _raise_no_driver)
    monkeypatch.setattr(sd, "query_devices", lambda *a, **k: {"default_samplerate": 0})

    # First call: hits the failing open, must NOT raise.
    await player.play_pcm(b"\x00\x00" * 240)
    assert player._output_unavailable is True
    assert len(opens) == 1  # tried exactly once

    # Second call: latched — must short-circuit WITHOUT touching OutputStream.
    await player.play_pcm(b"\x00\x00" * 240)
    assert len(opens) == 1  # still 1 — no further open attempt


@pytest.mark.asyncio
async def test_play_chunks_no_output_device_is_silent_noop(monkeypatch) -> None:
    """play_chunks must drain its iterator and no-op on a driverless box."""
    player = _bare_player()

    def _raise_no_driver(*args, **kwargs):
        raise sd.PortAudioError(
            "Unanticipated host error [PaErrorCode -9999]: "
            "'There is no driver installed on your system.'"
        )

    monkeypatch.setattr(sd, "OutputStream", _raise_no_driver)
    monkeypatch.setattr(sd, "query_devices", lambda *a, **k: {"default_samplerate": 0})

    # Must not raise; latch is set.
    await player.play_chunks(_one_chunk(b"\x00\x00" * 240))
    assert player._output_unavailable is True


@pytest.mark.asyncio
async def test_invalidate_device_cache_clears_latch(monkeypatch) -> None:
    """A hot-plug / device-reset clears the latch so the next play re-probes."""
    player = _bare_player()
    player._output_unavailable = True
    player.invalidate_device_cache()
    assert player._output_unavailable is False
