"""Unit tests for AudioPlayer device hot-swap safety (Bug H3, 2026-05-18).

Background — audit-4 finding:
    AudioPlayer kept a cache mapping ``source_rate -> working device_rate``.
    On USB headset hot-swap (disconnect + reconnect of a different device,
    or runtime ``self._device`` switch), the next ``_open_output_stream``
    call would return the stale cached rate for the previous device.
    Concretely: USB headset at 48 kHz cached → user pulls headset, system
    falls back to a Realtek codec whose default is 44.1 kHz → cached
    48000 returned → PortAudio either crashes with ``-9997 Invalid sample
    rate`` or worse, opens the stream and writes zero audible frames.

Fix:
    1. Cache key is now ``(device, source_rate)`` so a device change
       misses the cache and walks the cascade fresh.
    2. New ``AudioPlayer.invalidate_device_cache()`` drops the active
       stream and clears the rate cache — intended for explicit hot-swap
       signaling from device-monitor code.
    3. New ``AudioPlayer.set_device(new_device)`` re-resolves the device,
       calls invalidate_device_cache, and re-arms the once-per-instance
       device log.
"""
from __future__ import annotations

import pytest

from jarvis.audio.player import AudioPlayer


def _make_player(device: int | str | None = None) -> AudioPlayer:
    """Bare AudioPlayer that doesn't touch sounddevice."""
    p = AudioPlayer.__new__(AudioPlayer)
    p._device = device
    p._sample_rate = 24_000
    p._channels = 1
    p._device_logged = True
    p._bus = None
    p._play_lock = None
    p._active_stream = None
    p._active_source_rate = None
    p._active_device_rate = None
    p._device_rate_cache = {}
    return p


def test_cache_key_includes_device(monkeypatch) -> None:
    """A successful open on device=3 must NOT shortcut a later open on
    device=7 — the cascade must run fresh for the new device.
    """
    import jarvis.audio.player as player_mod

    open_log: list[tuple] = []

    class FakeStream:
        latency = 0.2
        def start(self): pass

    def fake_outputstream(*, samplerate, device, **kw):
        open_log.append((device, samplerate))
        # device=3 (the USB headset) only does 48000.
        # device=7 (the Realtek board) only does 44100.
        if device == 3 and samplerate == 48000:
            return FakeStream()
        if device == 7 and samplerate == 44100:
            return FakeStream()
        raise player_mod.sd.PortAudioError(
            f"Error opening OutputStream: Invalid sample rate "
            f"[PaErrorCode -9997] (device={device}, rate={samplerate})"
        )

    monkeypatch.setattr(player_mod.sd, "OutputStream", fake_outputstream)
    monkeypatch.setattr(
        player_mod.sd, "query_devices", lambda d: {"default_samplerate": 48000}
    )

    # Cache 48000 against device=3.
    p = _make_player(device=3)
    _, rate = p._open_output_stream(24000)
    assert rate == 48000
    assert p._device_rate_cache == {(3, 24000): 48000}

    # Switch device WITHOUT clearing cache (simulating naive code path).
    # The new device=7 must walk the cascade — not blindly retry 48000.
    p._device = 7
    open_log.clear()
    _, rate2 = p._open_output_stream(24000)
    assert rate2 == 44100
    # Cascade should NOT short-circuit to a stale 48000 hit.
    assert (7, 24000) in p._device_rate_cache
    assert p._device_rate_cache[(7, 24000)] == 44100
    # Old device's cache entry must remain untouched (other consumers may
    # depend on it; we trust the caller to invalidate explicitly).
    assert (3, 24000) in p._device_rate_cache


def test_invalidate_device_cache_drops_active_stream_and_cache() -> None:
    """invalidate_device_cache() must:
    - close the active stream (if any),
    - reset _active_source_rate / _active_device_rate,
    - empty _device_rate_cache.
    """
    p = _make_player(device=3)
    p._device_rate_cache[(3, 24000)] = 48000
    p._device_rate_cache[(7, 22050)] = 44100

    close_calls: list = []

    class FakeStream:
        def abort(self): pass
        def stop(self): close_calls.append("stop")
        def close(self): close_calls.append("close")

    p._active_stream = FakeStream()
    p._active_source_rate = 24000
    p._active_device_rate = 48000

    p.invalidate_device_cache()

    assert p._active_stream is None
    assert p._active_source_rate is None
    assert p._active_device_rate is None
    assert p._device_rate_cache == {}
    assert close_calls == ["stop", "close"], (
        f"expected _close_output_stream to call stop+close; got {close_calls}"
    )


def test_set_device_to_same_device_is_noop() -> None:
    """If the new device resolves to the same value, no cache flush,
    no _device_logged reset.
    """
    p = _make_player(device=5)
    p._device_logged = True
    p._device_rate_cache[(5, 24000)] = 48000

    p.set_device(5)

    assert p._device == 5
    assert p._device_logged is True
    assert p._device_rate_cache == {(5, 24000): 48000}


def test_set_device_to_different_device_clears_cache() -> None:
    """set_device(other) must invalidate the rate cache, drop the active
    stream, and re-arm the once-per-instance device log.
    """
    p = _make_player(device=3)
    p._device_logged = True
    p._device_rate_cache[(3, 24000)] = 48000

    class FakeStream:
        def abort(self): pass
        def stop(self): pass
        def close(self): pass

    p._active_stream = FakeStream()
    p._active_source_rate = 24000
    p._active_device_rate = 48000

    p.set_device(7)

    assert p._device == 7
    assert p._device_logged is False, "must re-log the new device on next play"
    assert p._device_rate_cache == {}
    assert p._active_stream is None


def test_set_device_with_auto_headset_string_resolves(monkeypatch) -> None:
    """Strings like ``"auto-headset"`` must be re-resolved by
    ``_resolve_output_device`` — set_device shouldn't store the literal
    string.
    """
    import jarvis.audio.player as player_mod

    # _resolve_output_device now takes an optional priority arg (user
    # device-name preference); the stub accepts and ignores it.
    monkeypatch.setattr(
        player_mod,
        "_resolve_output_device",
        lambda d, priority=None: 42 if d == "auto-headset" else d,
    )

    p = _make_player(device=3)
    p.set_device("auto-headset")
    assert p._device == 42
