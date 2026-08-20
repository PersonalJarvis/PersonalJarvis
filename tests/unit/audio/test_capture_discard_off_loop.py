"""Closing a microphone must never freeze the process's only event loop.

``Pa_CloseStream`` is a synchronous native call. After an audio endpoint
transition — a headset unplugged, a monitor's audio device appearing — it can
sit inside PortAudio for minutes. On 2026-08-20 the maintainer's box logged
61.7 s and 188.3 s of it, both from the mic stall watchdog, and one 15 s from a
capture teardown.

Every one of those seconds froze the SINGLE asyncio loop this process has —
which also serves the desktop window over HTTP. The socket kept accepting and
nothing was answered, so a window opening in that gap received no bytes and no
error, and stayed blank with no explanation (jarvis/ui/window_watchdog.py now
catches the symptom; this is the cause).

So the dead stream is shut down in a worker thread. The coroutine waits a short
grace — the ordinary close is milliseconds and keeps its ordering — and then
moves on. What must hold is that the loop keeps running throughout.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from jarvis.audio.capture import MicrophoneCapture


class _WedgedStream:
    """A stream whose native close blocks — the endpoint-transition case."""

    def __init__(self, block_s: float) -> None:
        self._block_s = block_s
        self.aborted = threading.Event()
        self.closed = threading.Event()

    def abort(self) -> None:
        self.aborted.set()

    def close(self) -> None:
        time.sleep(self._block_s)
        self.closed.set()


class _QuickStream:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def abort(self) -> None:
        self.calls.append("abort")

    def close(self) -> None:
        self.calls.append("close")


@pytest.mark.asyncio
async def test_a_wedged_close_does_not_stall_the_loop(monkeypatch) -> None:
    """The point of the whole change: other work keeps running during a freeze."""
    monkeypatch.setattr(MicrophoneCapture, "_DISCARD_GRACE_S", 0.05)
    stream = _WedgedStream(block_s=0.6)

    ticks = 0

    async def _heartbeat() -> None:
        # Stands in for the HTTP server sharing this loop.
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.02)
            ticks += 1

    beat = asyncio.create_task(_heartbeat())
    started = time.monotonic()
    await MicrophoneCapture._discard_stream_off_loop(stream)
    spent = time.monotonic() - started

    assert spent < 0.4, "the coroutine waited out the wedged native close"
    assert ticks > 0, "the loop was frozen while the stream was closing"
    await beat
    assert ticks == 20


@pytest.mark.asyncio
async def test_a_normal_close_keeps_its_ordering(monkeypatch) -> None:
    """The common case must still complete before the caller moves on.

    A new stream is opened immediately after; letting the old one linger on the
    same device would trade a frozen loop for a device conflict.
    """
    monkeypatch.setattr(MicrophoneCapture, "_DISCARD_GRACE_S", 2.0)
    stream = _QuickStream()
    await MicrophoneCapture._discard_stream_off_loop(stream)
    assert stream.calls == ["abort", "close"]


@pytest.mark.asyncio
async def test_the_abandoned_close_still_finishes_on_its_own(monkeypatch) -> None:
    """Giving up waiting is not giving up closing — the handle is still released."""
    monkeypatch.setattr(MicrophoneCapture, "_DISCARD_GRACE_S", 0.05)
    stream = _WedgedStream(block_s=0.3)
    await MicrophoneCapture._discard_stream_off_loop(stream)
    assert not stream.closed.is_set()
    assert stream.closed.wait(timeout=3.0), "the worker thread never finished the close"


@pytest.mark.asyncio
async def test_a_raising_close_is_swallowed_not_propagated(monkeypatch) -> None:
    """A dead handle failing to close is expected — it must not kill the caller."""
    monkeypatch.setattr(MicrophoneCapture, "_DISCARD_GRACE_S", 2.0)

    class _Hostile:
        def abort(self) -> None:
            raise RuntimeError("device gone")

        def close(self) -> None:
            raise RuntimeError("device gone")

    await MicrophoneCapture._discard_stream_off_loop(_Hostile())
