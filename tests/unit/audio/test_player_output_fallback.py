"""BUG-108: a dead local speaker is recovered, not treated as transport death."""

from __future__ import annotations

import jarvis.audio.player as player_module
from jarvis.audio.player import AudioPlayer, is_local_output_error


def test_is_local_output_error_detects_portaudio_device_death() -> None:
    err = player_module._PortAudioError(
        "Error opening OutputStream: Internal PortAudio error [PaErrorCode -9986]"
    )
    assert is_local_output_error(err) is True
    assert is_local_output_error(RuntimeError(str(err))) is True


def test_is_local_output_error_ignores_invalid_sample_rate() -> None:
    err = player_module._PortAudioError("Invalid sample rate [PaErrorCode -9997]")
    assert is_local_output_error(err) is False


def test_is_local_output_error_ignores_transport_failures() -> None:
    assert is_local_output_error(ConnectionError("websocket 1006")) is False
    assert is_local_output_error(TimeoutError("handshake")) is False


def test_open_retries_on_the_next_output_device(monkeypatch) -> None:
    player = AudioPlayer.__new__(AudioPlayer)
    player._device = 5
    player._device_priority = ()
    player._device_logged = True
    player._output_failed_devices = set()
    player._device_rate_cache = {}
    player._device_rate_failed = set()
    player._playback_generation = 0
    player._active_stream = None
    player._stream_channels = 2
    player._output_buffer_s = 0.2

    opens: list[int | str | None] = []

    class _OkStream:
        latency = 0.05

        def start(self) -> None:
            return None

    def fake_query(device=None, kind=None):  # noqa: ANN001
        return {"default_samplerate": 48000, "max_output_channels": 2}

    monkeypatch.setattr(player_module.sd, "query_devices", fake_query)

    def fake_stream(**kwargs):  # noqa: ANN003
        opens.append(kwargs.get("device"))
        if kwargs.get("device") == 5:
            raise player_module._PortAudioError(
                "Internal PortAudio error",
                -9986,
            )
        return _OkStream()

    monkeypatch.setattr(player_module.sd, "OutputStream", fake_stream)

    recovered = {"called": False}

    def recover() -> bool:
        recovered["called"] = True
        player._device = 2
        return True

    monkeypatch.setattr(player, "recover_output_device", recover)
    monkeypatch.setattr(
        player_module.topology, "stream_open_guard", lambda: _null_guard()
    )

    stream, rate = player._open_output_stream(24_000)  # noqa: SLF001
    assert recovered["called"] is True
    assert player._device == 2
    assert isinstance(stream, _OkStream)
    assert rate in (24_000, 48_000)
    assert 5 in opens
    assert 2 in opens


class _null_guard:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_recover_output_device_skips_the_dead_index(monkeypatch) -> None:
    player = AudioPlayer.__new__(AudioPlayer)
    player._device = 7
    player._device_priority = ()
    player._output_failed_devices = set()
    player._playback_generation = 0
    player._active_stream = None
    player._active_source_rate = None
    player._active_device_rate = None
    player._device_rate_cache = {}
    player._device_rate_failed = set()
    player._device_logged = True

    monkeypatch.setattr(
        player_module,
        "_ranked_output_device_indices",
        lambda _priority=None: [7, 3, 1],
    )
    monkeypatch.setattr(player, "invalidate_device_cache", lambda: None)

    assert player.recover_output_device() is True
    assert player._device == 3
    assert 7 in player._output_failed_devices
