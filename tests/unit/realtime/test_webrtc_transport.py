"""Reporting guarantees of the in-process WebRTC audio endpoint.

Both defects pinned here are silent-failure defects (AP-30): the assistant's
voice stops, or loses a piece, and nothing above DEBUG says so. On a live
media track that audio is gone for good — the player never fills the hole —
so the log is the only place the loss can still be seen.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

import jarvis.realtime.webrtc_transport as webrtc_transport


class MediaStreamError(Exception):
    """Same NAME aiortc uses for the ordinary end of a track."""


class _Track:
    def __init__(self, frames=(), *, failure: BaseException | None = None) -> None:
        self._frames = list(frames)
        self._failure = failure or MediaStreamError("track ended")

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        raise self._failure


class _Stub:
    """Only what ``_drain_remote`` touches — no peer connection required."""

    def __init__(self, maxsize: int = 200) -> None:
        self._recv_queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.finished = 0

    async def _finish_stream(self) -> None:
        self.finished += 1


def _frame():
    import av  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    frame = av.AudioFrame.from_ndarray(
        np.zeros((1, 960), dtype=np.int16), format="s16", layout="mono"
    )
    frame.sample_rate = 48_000
    frame.pts = 0
    return frame


async def _drain(stub: _Stub, track: _Track) -> None:
    await webrtc_transport.RealtimeWebRtcAudioEndpoint._drain_remote(stub, track)


@pytest.mark.asyncio
async def test_an_ordinary_track_end_stays_quiet(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    stub = _Stub()

    await _drain(stub, _Track())

    assert stub.finished == 1
    assert not [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]


@pytest.mark.asyncio
async def test_a_failing_track_reports_that_the_voice_stopped(caplog) -> None:
    """This used to be a DEBUG line: the voice went mute mid-call and the log
    said nothing a user or maintainer would ever see."""
    caplog.set_level(logging.DEBUG)
    stub = _Stub()

    await _drain(stub, _Track(failure=RuntimeError("decoder exploded")))

    assert stub.finished == 1
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert any("provider voice" in message for message in warnings)


@pytest.mark.asyncio
async def test_dropped_audio_frames_are_reported(caplog) -> None:
    """A full receive queue drops the oldest frame to stay current. That is a
    hole in the reply, and it used to leave no trace at all."""
    caplog.set_level(logging.INFO)
    stub = _Stub(maxsize=1)
    stub._recv_queue.put_nowait(b"\x00\x00")  # already full

    await _drain(stub, _Track(frames=[_frame(), _frame()]))

    dropped = [
        record.getMessage()
        for record in caplog.records
        if "dropped" in record.getMessage()
    ]
    assert dropped
    assert any("in total" in message for message in dropped)
