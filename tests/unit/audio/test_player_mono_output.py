"""Output-channel portability tests for mono and stereo devices."""

from __future__ import annotations

import numpy as np

from jarvis.audio.player import AudioPlayer


def _bare_player() -> AudioPlayer:
    player = AudioPlayer.__new__(AudioPlayer)
    player._device = 4
    player._sample_rate = 24_000
    player._device_rate_cache = {}
    player._stream_channels = 2
    player._volume = 1.0
    return player


def _open_and_write(monkeypatch, max_output_channels: int) -> tuple[int, list]:
    import jarvis.audio.player as player_mod

    opened_channels: list[int] = []
    writes: list[np.ndarray] = []

    class FakeStream:
        latency = 0.2

        def start(self) -> None:
            pass

        def write(self, frames: np.ndarray) -> bool:
            writes.append(frames.copy())
            return False

    def fake_output_stream(**kwargs):
        opened_channels.append(kwargs["channels"])
        return FakeStream()

    monkeypatch.setattr(
        player_mod.sd,
        "query_devices",
        lambda device=None, kind=None: {
            "default_samplerate": 48_000,
            "max_output_channels": max_output_channels,
        },
    )
    monkeypatch.setattr(player_mod.sd, "OutputStream", fake_output_stream)

    player = _bare_player()
    stream, device_rate = player._open_output_stream(24_000)
    player._write_samples(
        stream,
        np.arange(2_400, dtype=np.int16),
        source_rate=24_000,
        device_rate=device_rate,
    )
    return opened_channels[0], writes


def test_mono_only_device_opens_and_receives_one_channel(monkeypatch) -> None:
    channels, writes = _open_and_write(monkeypatch, max_output_channels=1)

    assert channels == 1
    assert writes
    assert all(block.ndim == 2 and block.shape[1] == 1 for block in writes)
    assert sum(block.shape[0] for block in writes) == 2_400


def test_stereo_capable_device_preserves_front_channel_duplication(monkeypatch) -> None:
    channels, writes = _open_and_write(monkeypatch, max_output_channels=2)

    assert channels == 2
    assert writes
    assert all(block.ndim == 2 and block.shape[1] == 2 for block in writes)
    for block in writes:
        np.testing.assert_array_equal(block[:, 0], block[:, 1])
