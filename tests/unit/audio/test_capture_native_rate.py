"""Cross-platform microphone native-rate fallback tests."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from jarvis.audio import capture


@pytest.mark.asyncio
@pytest.mark.parametrize("native_rate", [44_100, 48_000])
async def test_native_rate_fallback_resamples_to_16khz_contract(
    monkeypatch, native_rate: int
) -> None:
    """CoreAudio/ALSA devices may reject 16 kHz but expose a native rate."""
    open_calls: list[dict] = []

    class FakeStream:
        def __init__(self, kwargs: dict) -> None:
            self.kwargs = kwargs
            self.started = False

        def start(self) -> None:
            self.started = True

    def fake_input_stream(**kwargs):
        open_calls.append(kwargs)
        if kwargs["samplerate"] != native_rate:
            raise RuntimeError("Invalid sample rate")
        return FakeStream(kwargs)

    def fake_query_devices(device=None, kind=None):
        assert device == 3
        return {
            "max_input_channels": 1,
            "default_samplerate": native_rate,
        }

    monkeypatch.setattr(capture, "_fallback_input_devices", lambda _device: [])
    monkeypatch.setattr(capture.sd, "query_devices", fake_query_devices)
    monkeypatch.setattr(capture.sd, "InputStream", fake_input_stream)

    mic = capture.MicrophoneCapture(device=3, access_gate=lambda: True)
    mic._loop = asyncio.get_running_loop()
    await mic._try_open_stream()

    assert [call["samplerate"] for call in open_calls] == [16_000, native_rate]
    assert open_calls[-1]["blocksize"] == native_rate // 10
    assert mic._capture_sample_rate == native_rate
    assert mic._capture_resampler is not None

    # One 100 ms native-rate callback must remain one approximately 100 ms
    # downstream chunk while advertising the unchanged 16 kHz contract.
    native_frames = np.arange(native_rate // 10, dtype="<i2").reshape(-1, 1)
    mic._callback(native_frames, native_frames.shape[0], None, None)
    await asyncio.sleep(0)
    chunk = mic._queue.get_nowait()

    assert chunk.sample_rate == 16_000
    assert chunk.channels == 1
    assert len(chunk.pcm) // 2 == 1_600


@pytest.mark.asyncio
async def test_16khz_host_api_twin_precedes_native_rate_fallback(monkeypatch) -> None:
    """The existing Windows MME/DirectSound recovery order stays intact."""
    open_calls: list[tuple[int, int]] = []

    class FakeStream:
        def start(self) -> None:
            pass

    def fake_input_stream(**kwargs):
        device_rate = (kwargs["device"], kwargs["samplerate"])
        open_calls.append(device_rate)
        if device_rate == (10, 16_000):
            raise RuntimeError("Invalid sample rate")
        if device_rate == (11, 16_000):
            return FakeStream()
        raise AssertionError(f"Unexpected native-rate attempt: {device_rate}")

    monkeypatch.setattr(capture, "_fallback_input_devices", lambda _device: [11])

    def unexpected_native_rate_query(device=None, kind=None):
        raise AssertionError("Native rates must stay off the 16 kHz fast path")

    monkeypatch.setattr(capture.sd, "query_devices", unexpected_native_rate_query)
    monkeypatch.setattr(capture.sd, "InputStream", fake_input_stream)

    mic = capture.MicrophoneCapture(device=10, access_gate=lambda: True)
    await mic._try_open_stream()

    assert open_calls == [(10, 16_000), (11, 16_000)]
    assert mic._device == 11
    assert mic._capture_sample_rate == 16_000
    assert mic._capture_resampler is None
