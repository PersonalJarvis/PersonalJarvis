"""Guarantees of the in-process WebRTC audio endpoint.

Three classes of defect are pinned here, all of them previously invisible:

* **Pacing.** The outgoing microphone track must emit 50 frames per second of
  wall clock. Pacing it with a plain ``sleep(0.02)`` per frame measured 30.8 ms
  per frame on a stock Windows desktop — 0.65x realtime — which buries the far
  end under a growing backlog and then deletes a third of the user's speech.
* **Termination.** Every consumer of ``next_output_pcm`` must eventually see
  ``None``. A terminator lost to a full queue, or never queued by ``close()``,
  is an unbreakable hang rather than a lost frame.
* **Reporting.** The assistant's voice stopping, or losing a piece, must leave
  a trace above DEBUG (AP-30). On a live media track that audio is gone for
  good — the player never fills the hole — so the log is the only place the
  loss can still be seen.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

import pytest

import jarvis.realtime.webrtc_transport as webrtc_transport

pytest.importorskip("aiortc", reason="the in-process WebRTC endpoint needs aiortc")
pytest.importorskip("av", reason="the in-process WebRTC endpoint needs PyAV")

# Long enough that a single frame's quantisation cannot dominate the ratio,
# short enough to stay a unit test.
_PACING_WINDOW_S = 1.5
_FRAME_S = 0.02


class MediaStreamError(Exception):
    """Same NAME aiortc uses for the ordinary end of a track."""


class _Track:
    kind = "audio"

    def __init__(self, frames=(), *, failure: BaseException | None = None) -> None:
        self._frames = list(frames)
        self._failure = failure or MediaStreamError("track ended")

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        raise self._failure


class _SilentTrack:
    """A track that attaches and then never delivers a frame."""

    kind = "audio"

    async def recv(self):
        await asyncio.Event().wait()  # pragma: no cover - never returns
        raise AssertionError("unreachable")


class _StubPeerConnection:
    def __init__(self, state: str = "new") -> None:
        self.connectionState = state
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1
        self.connectionState = "closed"


class _Endpoint(webrtc_transport.RealtimeWebRtcAudioEndpoint):
    """A real endpoint minus the peer connection.

    Every method under test here runs unchanged; only the aiortc peer is
    replaced, so the tests stay fast and need no network at all.
    """

    def __init__(  # noqa: D107 - deliberately bypasses the aiortc constructor
        self,
        *,
        recv_max: int = 200,
        first_audio_timeout_s: float = 10.0,
        state: str = "new",
    ) -> None:
        self._pc = _StubPeerConnection(state)
        self._recv_queue: asyncio.Queue = asyncio.Queue(maxsize=recv_max)
        self._reader_task = None
        self._first_audio_timeout_s = first_audio_timeout_s
        self._closed = False
        self._ended = False
        self._outgoing_drops = 0
        self._recv_dropped = 0
        self._sender = None
        self._remote_track_seen = asyncio.Event()
        self._connection_changed = asyncio.Event()


def _frame(pts: int = 0):
    import av  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    frame = av.AudioFrame.from_ndarray(
        np.zeros((1, 960), dtype=np.int16), format="s16", layout="mono"
    )
    frame.sample_rate = 48_000
    frame.pts = pts
    return frame


def _warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]


# --------------------------------------------------------------------------
# WR-1 — wall-clock pacing of the outgoing microphone track
# --------------------------------------------------------------------------


async def _count_frames(track, window_s: float) -> tuple[int, float]:
    loop = asyncio.get_running_loop()
    start = loop.time()
    frames = 0
    while loop.time() - start < window_s:
        await track.recv()
        frames += 1
    return frames, loop.time() - start


@pytest.mark.asyncio
async def test_the_sender_track_holds_wall_clock_pacing() -> None:
    """50 frames per second of WALL CLOCK, not one frame per 20 ms sleep.

    A fixed per-frame sleep costs 20 ms plus scheduling latency every
    iteration; on the default Windows timer granularity that is ~31 ms, so the
    microphone would transmit at two thirds of realtime.
    """
    sender = webrtc_transport._PcmSenderTrack()

    frames, elapsed = await _count_frames(sender.track, _PACING_WINDOW_S)

    assert frames == pytest.approx(elapsed / _FRAME_S, rel=0.06)


@pytest.mark.asyncio
async def test_the_sender_track_keeps_pacing_under_event_loop_load() -> None:
    """Deadline correction has to survive a busy loop, which is the point.

    Competing work is exactly what a fixed sleep cannot absorb: every stall is
    added to the next frame instead of being caught up.
    """
    stop = asyncio.Event()

    async def _load() -> None:
        while not stop.is_set():
            spin_until = time.perf_counter() + 0.004
            while time.perf_counter() < spin_until:
                pass
            await asyncio.sleep(0.002)

    load_task = asyncio.create_task(_load())
    sender = webrtc_transport._PcmSenderTrack()
    try:
        frames, elapsed = await _count_frames(sender.track, _PACING_WINDOW_S)
    finally:
        stop.set()
        await asyncio.gather(load_task, return_exceptions=True)

    assert frames == pytest.approx(elapsed / _FRAME_S, rel=0.10)


@pytest.mark.asyncio
async def test_a_long_stall_resyncs_the_clock_instead_of_bursting(caplog) -> None:
    """A blocked loop must not become a wall of stale speech at the far end."""
    caplog.set_level(logging.INFO)
    sender = webrtc_transport._PcmSenderTrack()
    track = sender.track
    await track.recv()
    # Pretend the loop was blocked for two seconds: 100 frames of pacing debt.
    track._start -= 2.0

    await track.recv()

    assert any("behind wall clock" in message for message in _warnings(caplog))
    # The clock was re-based rather than left 2 s in debt, so the next frame
    # waits normally again instead of firing immediately.
    loop = asyncio.get_running_loop()
    before = loop.time()
    await track.recv()
    assert loop.time() - before >= _FRAME_S * 0.5


@pytest.mark.asyncio
async def test_a_stopped_sender_track_raises_instead_of_producing_frames() -> None:
    """aiortc's contract: a stopped track ends, it does not emit forever."""
    sender = webrtc_transport._PcmSenderTrack()
    track = sender.track
    await track.recv()

    track.stop()

    with pytest.raises(Exception) as error:  # noqa: PT011 - matched by NAME below
        await track.recv()
    assert type(error.value).__name__ == "MediaStreamError"


def test_the_send_queue_is_a_jitter_budget_not_a_hiding_place() -> None:
    """200 chunks was multiple seconds of lag that made a stalled sender look fine."""
    assert webrtc_transport._SEND_QUEUE_MAX <= 20


async def _no_pacing() -> None:
    return None


@pytest.mark.asyncio
async def test_a_preroll_burst_is_delivered_losslessly_in_order() -> None:
    """The pre-handshake microphone flush must survive the jitter queue.

    ~1.2 s of the user's FIRST sentence is buffered during the handshake and
    flushed in one unpaced burst. The old drop-oldest queue deleted ~2/3 of
    it, so ChatGPT-Live heard a fragment and invented a user turn out of it
    (live 2026-08-04). Overflow now drains ORDERED into the elastic residue.
    """
    sender = webrtc_transport._PcmSenderTrack()
    track = sender.track
    track._pace = _no_pacing
    frame_bytes = webrtc_transport._WIRE_FRAME_BYTES
    chunks = [
        (1000 + i).to_bytes(2, "little", signed=True) * (frame_bytes // 2)
        for i in range(40)
    ]
    for chunk in chunks:
        assert sender.enqueue(chunk) == 0

    received = bytearray()
    for _ in range(40):
        frame = await track.recv()
        received += bytes(frame.planes[0])[: frame.samples * 2]

    assert bytes(received) == b"".join(chunks)


@pytest.mark.asyncio
async def test_backlog_drains_by_skipping_silent_frames_only() -> None:
    """A media track is clocked at realtime, so a delivered backlog can only
    catch up by SKIPPING frames — and only silent ones are skippable without
    losing speech. Without this, the preroll's lag would persist for the whole
    call and every reply would arrive that much late."""
    sender = webrtc_transport._PcmSenderTrack()
    track = sender.track
    track._pace = _no_pacing
    frame_bytes = webrtc_transport._WIRE_FRAME_BYTES
    loud = (1200).to_bytes(2, "little", signed=True) * (frame_bytes // 2)
    quiet = b"\x00" * frame_bytes
    for chunk in [loud] * 10 + [quiet] * 15 + [loud] * 5:
        sender.enqueue(chunk)

    delivered: list[bytes] = []
    for _ in range(30):
        frame = await track.recv()
        payload = bytes(frame.planes[0])[: frame.samples * 2]
        delivered.append(payload)
        if sum(1 for item in delivered if item == loud) == 15:
            break

    # Every loud frame arrived, in fewer wall-clock slots than were enqueued:
    # the silent middle shrank instead of the speech being dropped.
    assert sum(1 for item in delivered if item == loud) == 15
    assert len(delivered) < 30


def test_a_stalled_sender_still_sheds_stale_audio() -> None:
    """The elastic residue is not a hiding place: past the cap, the OLDEST
    audio is shed and reported, exactly like the old jitter queue promised."""
    sender = webrtc_transport._PcmSenderTrack()
    frame_bytes = webrtc_transport._WIRE_FRAME_BYTES
    chunk = (1000).to_bytes(2, "little", signed=True) * (frame_bytes // 2)
    total = 0
    shed = 0
    while shed == 0 and total < 1000:
        shed = sender.enqueue(chunk)
        total += 1
    assert 0 < total < 1000
    assert shed > 0


# --------------------------------------------------------------------------
# WR-2 / WR-3 — the output stream always terminates
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_full_receive_queue_still_delivers_the_end_of_stream(caplog) -> None:
    """The terminator used to be dropped in exactly the situation it matters.

    A lagging consumer is why the queue is full, and a lagging consumer is why
    the track is ending — so the one discarded ``None`` was an unbreakable
    hang, not a lost frame.
    """
    caplog.set_level(logging.INFO)
    endpoint = _Endpoint(recv_max=2)
    endpoint._recv_queue.put_nowait(b"\x00\x00")
    endpoint._recv_queue.put_nowait(b"\x01\x01")

    endpoint._end_stream()

    assert await asyncio.wait_for(endpoint.next_output_pcm(), timeout=1.0) == b"\x01\x01"
    assert await asyncio.wait_for(endpoint.next_output_pcm(), timeout=1.0) is None
    assert any("end-of-stream" in message for message in _warnings(caplog))


@pytest.mark.asyncio
async def test_close_releases_a_consumer_waiting_for_output() -> None:
    """``close()`` used to leave ``next_output_pcm`` parked forever."""
    endpoint = _Endpoint()
    waiter = asyncio.create_task(endpoint.next_output_pcm())
    await asyncio.sleep(0)  # let the consumer park on the empty queue

    await endpoint.close()

    assert await asyncio.wait_for(waiter, timeout=1.0) is None


@pytest.mark.asyncio
async def test_output_stays_terminated_after_the_marker_is_consumed() -> None:
    """A second consumer must not wait for a second terminator."""
    endpoint = _Endpoint()
    endpoint._end_stream()

    assert await asyncio.wait_for(endpoint.next_output_pcm(), timeout=1.0) is None
    assert await asyncio.wait_for(endpoint.next_output_pcm(), timeout=1.0) is None


@pytest.mark.asyncio
async def test_close_is_idempotent_and_closes_the_peer_once() -> None:
    endpoint = _Endpoint()

    await endpoint.close()
    await endpoint.close()

    assert endpoint._pc.closed == 1


def test_diagnostics_reports_all_transport_counters() -> None:
    """``diagnostics()`` is the one public seam for the postmortem counters.

    All zero on a healthy endpoint; the sender-less test double stands for an
    endpoint whose sender never came up — still all zero, never an error.
    """
    endpoint = _Endpoint()
    assert endpoint.diagnostics() == {
        "sender_pacing_resyncs": 0,
        "sender_catchup_dropped_frames": 0,
        "sender_shed_frames": 0,
        "recv_dropped_frames": 0,
    }

    endpoint._outgoing_drops = 7
    endpoint._recv_dropped = 3
    diag = endpoint.diagnostics()
    assert diag["sender_shed_frames"] == 7
    assert diag["recv_dropped_frames"] == 3


# --------------------------------------------------------------------------
# WR-4 — a connected peer is not the same as a usable media path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_connected_peer_without_a_track_fails_the_handshake() -> None:
    """Connected and mute is a handshake failure, not a healthy session.

    ``WebRtcMediaPathUnavailable`` is the caller's signal to retry with a
    different ICE configuration.
    """
    endpoint = _Endpoint(state="connected")

    with pytest.raises(webrtc_transport.WebRtcMediaPathUnavailable, match="no audio"):
        await endpoint.wait_connected(0.5, track_timeout_s=0.05)


@pytest.mark.asyncio
async def test_wait_connected_returns_once_the_track_arrived() -> None:
    endpoint = _Endpoint(state="connected")
    endpoint._remote_track_seen.set()

    await asyncio.wait_for(endpoint.wait_connected(0.5), timeout=1.0)


@pytest.mark.asyncio
async def test_a_failed_peer_fails_the_wait_immediately() -> None:
    endpoint = _Endpoint(state="failed")

    with pytest.raises(webrtc_transport.WebRtcMediaPathUnavailable, match="failed"):
        await endpoint.wait_connected(5.0)


@pytest.mark.asyncio
async def test_a_track_that_never_delivers_audio_ends_the_stream_honestly(
    caplog,
) -> None:
    """A mid-call stop must NOT masquerade as a handshake failure.

    So the first-frame watchdog ends the output stream with a logged reason
    instead of raising into the handshake retry.
    """
    caplog.set_level(logging.INFO)
    endpoint = _Endpoint(first_audio_timeout_s=0.05)

    await endpoint._drain_remote(_SilentTrack())

    assert await asyncio.wait_for(endpoint.next_output_pcm(), timeout=1.0) is None
    assert any("no audio within" in message for message in _warnings(caplog))


@pytest.mark.asyncio
async def test_an_answer_without_audio_media_is_refused() -> None:
    endpoint = _Endpoint()

    with pytest.raises(webrtc_transport.WebRtcTransportUnavailable, match="no audio"):
        await endpoint.apply_answer("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\n")


# --------------------------------------------------------------------------
# WR-5 / WR-7 — reader lifecycle
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_reader_closes_the_endpoint_when_the_track_ends() -> None:
    """A dead media track leaves no reason to keep the peer and its sender alive."""
    endpoint = _Endpoint()

    await endpoint._drain_remote(_Track())

    assert endpoint._closed is True
    assert endpoint._pc.closed == 1


@pytest.mark.asyncio
async def test_only_the_first_audio_track_is_read(caplog) -> None:
    """A second reader would interleave two producers into one PCM stream."""
    caplog.set_level(logging.INFO)
    endpoint = webrtc_transport.RealtimeWebRtcAudioEndpoint(first_audio_timeout_s=5.0)
    on_track = endpoint._pc.listeners("track")[0]
    try:
        on_track(_SilentTrack())
        first = endpoint._reader_task
        assert first is not None

        on_track(_SilentTrack())

        assert endpoint._reader_task is first
        assert any("another audio track" in message for message in _warnings(caplog))
    finally:
        await endpoint.close()


@pytest.mark.asyncio
async def test_a_track_offered_after_close_is_ignored() -> None:
    endpoint = webrtc_transport.RealtimeWebRtcAudioEndpoint()
    on_track = endpoint._pc.listeners("track")[0]
    await endpoint.close()

    on_track(_SilentTrack())

    assert endpoint._reader_task is None


# --------------------------------------------------------------------------
# End-to-end: a real aiortc peer on both sides
# --------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_a_real_peer_handshake_delivers_decoded_audio() -> None:
    """The only test that exercises the real aiortc wiring end to end.

    Offer, answer, ICE, DTLS, the ``track`` event, the reader task and the
    48 kHz to 24 kHz resampler all run for real. Marked slow because the DTLS
    handshake alone costs about five seconds; ``pytest -m 'not slow'`` skips
    it, and it needs a usable loopback path, so a locked-down runner will fail
    it for reasons that have nothing to do with this module.
    """
    from aiortc import RTCPeerConnection, RTCSessionDescription  # noqa: PLC0415
    from aiortc.mediastreams import AudioStreamTrack  # noqa: PLC0415

    endpoint = webrtc_transport.RealtimeWebRtcAudioEndpoint(first_audio_timeout_s=5.0)
    remote = RTCPeerConnection()
    remote.addTrack(AudioStreamTrack())
    try:
        offer = await endpoint.create_offer()
        await remote.setRemoteDescription(
            RTCSessionDescription(sdp=offer, type="offer")
        )
        await remote.setLocalDescription(await remote.createAnswer())
        await endpoint.apply_answer(remote.localDescription.sdp)

        await endpoint.wait_connected(15.0)
        pcm = await asyncio.wait_for(endpoint.next_output_pcm(), timeout=5.0)

        assert pcm
        assert len(pcm) % 2 == 0
    finally:
        await endpoint.close()
        await remote.close()

    # Closing the endpoint terminates the stream for whoever is still reading.
    # Audio already decoded before the close is still delivered first — the
    # terminator is appended, not substituted — so drain to it.
    drained = 0
    while True:
        chunk = await asyncio.wait_for(endpoint.next_output_pcm(), timeout=1.0)
        if chunk is None:
            break
        drained += 1
        assert drained < webrtc_transport._RECV_QUEUE_MAX + 2


# --------------------------------------------------------------------------
# Reporting guarantees (AP-30)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ordinary_track_end_stays_quiet(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    endpoint = _Endpoint()

    await endpoint._drain_remote(_Track())

    assert await asyncio.wait_for(endpoint.next_output_pcm(), timeout=1.0) is None
    assert not _warnings(caplog)


@pytest.mark.asyncio
async def test_a_failing_track_reports_that_the_voice_stopped(caplog) -> None:
    """This used to be a DEBUG line: the voice went mute mid-call and the log
    said nothing a user or maintainer would ever see."""
    caplog.set_level(logging.DEBUG)
    endpoint = _Endpoint()

    await endpoint._drain_remote(_Track(failure=RuntimeError("decoder exploded")))

    assert any("provider voice" in message for message in _warnings(caplog))


@pytest.mark.asyncio
async def test_dropped_audio_frames_are_reported(caplog) -> None:
    """A full receive queue drops the oldest frame to stay current. That is a
    hole in the reply, and it used to leave no trace at all."""
    caplog.set_level(logging.INFO)
    endpoint = _Endpoint(recv_max=1)
    endpoint._recv_queue.put_nowait(b"\x00\x00")  # already full

    await endpoint._drain_remote(_Track(frames=[_frame(0), _frame(960)]))

    dropped = [
        record.getMessage()
        for record in caplog.records
        if "dropped" in record.getMessage()
    ]
    assert dropped
    assert any("in total" in message for message in dropped)


def test_a_missing_media_stack_explains_itself(monkeypatch, caplog) -> None:
    """A bare boolean hid the common case: an installed stack that fails to load."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(webrtc_transport, "_import_failure_reported", False)
    # A ``None`` entry in sys.modules makes the import raise, which is exactly
    # what a stack that cannot load its bundled libraries does.
    monkeypatch.setitem(sys.modules, "av", None)

    assert webrtc_transport.webrtc_available() is False
    assert "aiortc" in webrtc_transport.webrtc_unavailable_reason()
    assert any("could not be imported" in message for message in _warnings(caplog))
