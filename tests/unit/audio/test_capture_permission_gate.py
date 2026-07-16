"""macOS microphone capture never discovers TCC access by opening a device."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.audio import capture
from jarvis.core.protocols import AudioChunk


class _Stream:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def test_macos_default_gate_uses_process_wide_permission_port(monkeypatch) -> None:
    import jarvis.platform.permissions as permissions

    calls: list[permissions.PermissionId] = []
    port = SimpleNamespace(
        runtime_access_granted=lambda permission_id: calls.append(permission_id) or True
    )
    monkeypatch.setattr(capture.sys, "platform", "darwin")
    monkeypatch.setattr(permissions, "get_system_permission_port", lambda: port)

    gate = capture._macos_microphone_access_gate()

    assert gate is not None and gate() is True
    assert calls == [permissions.PermissionId.MICROPHONE]


@pytest.mark.asyncio
async def test_denied_gate_fails_before_portaudio_open(monkeypatch) -> None:
    opens: list[object] = []

    def _input_stream(**_kwargs):
        opens.append(object())
        return _Stream()

    monkeypatch.setattr(capture, "sd", SimpleNamespace(InputStream=_input_stream))
    mic = capture.MicrophoneCapture(device=0, access_gate=lambda: False)

    with pytest.raises(capture.MicrophoneAccessError):
        await mic.__aenter__()

    assert opens == []


@pytest.mark.asyncio
async def test_live_revocation_stops_stream_and_closes_device(monkeypatch) -> None:
    allowed = True
    stream = _Stream()
    monkeypatch.setattr(
        capture,
        "sd",
        SimpleNamespace(InputStream=lambda **_kwargs: stream),
    )
    mic = capture.MicrophoneCapture(device=0, access_gate=lambda: allowed)

    with pytest.raises(capture.MicrophoneAccessError):
        async with mic:
            iterator = mic.stream()
            mic._safe_put(
                AudioChunk(
                    pcm=b"\x00\x00",
                    sample_rate=16_000,
                    timestamp_ns=1,
                    channels=1,
                )
            )
            assert (await anext(iterator)).timestamp_ns == 1
            allowed = False
            await anext(iterator)

    assert stream.started is True
    assert stream.stopped is True
    assert stream.closed is True
