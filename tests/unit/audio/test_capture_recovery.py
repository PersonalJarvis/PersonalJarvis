"""Regression coverage for microphone reopen failures between voice turns."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.audio import capture


@pytest.fixture(autouse=True)
def _clean_resolve_cache() -> None:
    capture._invalidate_resolve_cache()
    yield
    capture._invalidate_resolve_cache()


def _input_devices() -> list[dict]:
    return [
        {
            "name": "Microphone (Preferred External)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "hostapi": 0,
            "default_samplerate": 48_000,
        },
        {
            "name": "Built-in Speakers",
            "max_input_channels": 0,
            "max_output_channels": 2,
            "hostapi": 0,
            "default_samplerate": 48_000,
        },
        {
            "name": "Microphone (Backup Built-in)",
            "max_input_channels": 1,
            "max_output_channels": 0,
            "hostapi": 0,
            "default_samplerate": 48_000,
        },
    ]


def _install_device_enumeration(monkeypatch, hostapi_name: str) -> list[dict]:
    devices = _input_devices()

    def fake_query_devices(device=None, kind=None):
        if device is None:
            return devices
        assert kind == "input"
        return devices[device]

    monkeypatch.setattr(capture.sd, "query_devices", fake_query_devices)
    monkeypatch.setattr(capture.sd, "query_hostapis", lambda: [{"name": hostapi_name}])
    monkeypatch.setattr(capture.sd, "default", SimpleNamespace(device=(0, 1)))
    return devices


@pytest.mark.asyncio
@pytest.mark.parametrize("hostapi_name", ["Core Audio", "ALSA", "Windows WASAPI"])
async def test_automatic_capture_recovers_on_another_physical_microphone(
    monkeypatch, hostapi_name: str
) -> None:
    """A post-turn PortAudio open failure must not kill always-on wake."""
    _install_device_enumeration(monkeypatch, hostapi_name)
    open_calls: list[tuple[int, int]] = []
    closed_calls: list[tuple[int, int]] = []

    class FakeStream:
        def __init__(self, device: int, rate: int) -> None:
            self.device = device
            self.rate = rate

        def start(self) -> None:
            if self.device == 0:
                raise RuntimeError("Internal PortAudio error [PaErrorCode -9986]")

        def close(self) -> None:
            closed_calls.append((self.device, self.rate))

    def fake_input_stream(**kwargs):
        call = (kwargs["device"], kwargs["samplerate"])
        open_calls.append(call)
        return FakeStream(*call)

    monkeypatch.setattr(capture.sd, "InputStream", fake_input_stream)

    mic = capture.MicrophoneCapture(device="auto-headset", access_gate=lambda: True)
    await mic._try_open_stream()

    assert open_calls == [
        (0, 16_000),
        (0, 48_000),
        (0, 44_100),
        (2, 16_000),
    ]
    assert closed_calls == open_calls[:3]
    assert mic._device == 2
    assert mic._using_physical_fallback is True

    # A temporary physical fallback is deliberately not cached forever. The
    # preferred/OS-default microphone is reconsidered for the next voice turn.
    assert capture._cached_resolve("auto-headset", ()) is None
    next_mic = capture.MicrophoneCapture(device="auto-headset")
    assert next_mic._device == 0


@pytest.mark.asyncio
async def test_reopen_after_working_wake_capture_uses_physical_fallback(
    monkeypatch,
) -> None:
    """A cached long-lived wake mic must not become a permanent dead end."""
    _install_device_enumeration(monkeypatch, "Core Audio")
    preferred_is_poisoned = False
    open_calls: list[tuple[int, int]] = []

    class FakeStream:
        def __init__(self, device: int, rate: int) -> None:
            self.device = device
            self.rate = rate

        def start(self) -> None:
            if self.device == 0 and preferred_is_poisoned:
                raise RuntimeError("Internal PortAudio error [PaErrorCode -9986]")

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    def fake_input_stream(**kwargs):
        call = (kwargs["device"], kwargs["samplerate"])
        open_calls.append(call)
        return FakeStream(*call)

    monkeypatch.setattr(capture.sd, "InputStream", fake_input_stream)

    first = capture.MicrophoneCapture(device="auto-headset", access_gate=lambda: True)
    async with first:
        assert first._device == 0
    assert capture._cached_resolve("auto-headset", ()) == 0

    preferred_is_poisoned = True
    reopened = capture.MicrophoneCapture(device="auto-headset", access_gate=lambda: True)
    async with reopened:
        assert reopened._device == 2
        assert reopened._using_physical_fallback is True

    assert open_calls == [
        (0, 16_000),
        (0, 16_000),
        (0, 48_000),
        (0, 44_100),
        (2, 16_000),
    ]


@pytest.mark.asyncio
async def test_explicit_numeric_microphone_does_not_switch_hardware(
    monkeypatch,
) -> None:
    """A numeric device is an intentional strict pin, even during recovery."""
    _install_device_enumeration(monkeypatch, "Core Audio")
    open_calls: list[tuple[int, int]] = []

    class FailingStream:
        def __init__(self, device: int, rate: int) -> None:
            self.device = device
            self.rate = rate

        def start(self) -> None:
            raise RuntimeError("Internal PortAudio error [PaErrorCode -9986]")

        def close(self) -> None:
            pass

    def fake_input_stream(**kwargs):
        call = (kwargs["device"], kwargs["samplerate"])
        open_calls.append(call)
        return FailingStream(*call)

    monkeypatch.setattr(capture.sd, "InputStream", fake_input_stream)

    mic = capture.MicrophoneCapture(device=0, access_gate=lambda: True)
    with pytest.raises(RuntimeError, match="PaErrorCode -9986"):
        await mic._try_open_stream()

    assert open_calls == [(0, 16_000), (0, 48_000), (0, 44_100)]


@pytest.mark.asyncio
async def test_healthy_automatic_open_keeps_recovery_enumeration_lazy(
    monkeypatch,
) -> None:
    """Recovery must not reintroduce latency on the successful 16 kHz path."""
    _install_device_enumeration(monkeypatch, "Core Audio")

    class HealthyStream:
        def start(self) -> None:
            pass

    mic = capture.MicrophoneCapture(device="auto-headset", access_gate=lambda: True)
    monkeypatch.setattr(capture, "_fallback_input_devices", lambda _device: [])

    def unexpected_recovery_enumeration(_priority):
        raise AssertionError("physical fallback enumeration must stay lazy")

    monkeypatch.setattr(capture, "_ranked_input_device_indices", unexpected_recovery_enumeration)
    monkeypatch.setattr(capture.sd, "InputStream", lambda **_kwargs: HealthyStream())

    await mic._try_open_stream()

    assert mic._device == 0
    assert mic._using_physical_fallback is False
    assert capture._cached_resolve("auto-headset", ()) == 0
