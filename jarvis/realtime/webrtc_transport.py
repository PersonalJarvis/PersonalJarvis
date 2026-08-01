"""In-process WebRTC audio endpoint for subscription realtime transports.

ChatGPT-Live (Codex app-server realtime ``v3``) carries audio ONLY on the
WebRTC media track: its client-event vocabulary has no audio append at all
(``session.update`` / ``session.context.append`` /
``delegation.context.append`` / ``delegation.function_call_output.create`` /
``session.close``), and the sideband ``thread/realtime/outputAudio/delta``
that the retired ``v1`` protocol used is never emitted. Verified live on
2026-08-01: a real peer received 956 RTP frames and zero sideband deltas.

Owning the peer HERE rather than in the UI keeps every audio-dependent
Jarvis feature intact — wake word, echo guard, barge-in, the Orb's speaking
state, transcript-gated playback — because PCM still flows through the same
pipeline. The UI only ever brokered a signalling-shaped offer, which cannot
carry a microphone.

The module imports ``aiortc`` lazily so plugin discovery and boot stay free
of it (AP-26), and degrades with an honest message when it is absent.
"""

from __future__ import annotations

import asyncio
import fractions
import logging
from typing import Any

log = logging.getLogger(__name__)

# Opus/WebRTC negotiate 48 kHz; Jarvis's realtime pipeline speaks 24 kHz mono.
_WIRE_RATE = 48_000
_WIRE_FRAME_SAMPLES = 960  # 20 ms
_JARVIS_RATE = 24_000
# Bounded so a stalled consumer propagates backpressure instead of buffering
# an unbounded amount of audio (~4 s at 20 ms frames).
_SEND_QUEUE_MAX = 200
_RECV_QUEUE_MAX = 200
_ICE_TIMEOUT_S = 15.0


class WebRtcTransportUnavailable(RuntimeError):
    """The host cannot provide an in-process WebRTC audio endpoint."""


class WebRtcMediaPathUnavailable(WebRtcTransportUnavailable):
    """The negotiated media path never became usable.

    Distinct from a missing stack so a caller can retry with a different ICE
    configuration instead of giving up on the provider entirely.
    """


def stun_ice_servers() -> list[Any]:
    """Public STUN fallback for networks where host candidates cannot connect."""
    from aiortc import RTCIceServer  # noqa: PLC0415

    return [RTCIceServer(urls="stun:stun.l.google.com:19302")]


def webrtc_available() -> bool:
    """Whether an in-process WebRTC audio endpoint can be built here."""
    try:
        import aiortc  # noqa: F401, PLC0415 - capability probe only
        import av  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001 - a missing optional stack is a capability answer
        return False
    return True


class _PcmSenderTrack:
    """Outgoing audio track fed by Jarvis PCM chunks.

    Real-time paced: the peer expects roughly wall-clock delivery, so a gap in
    the incoming PCM becomes silence rather than a burst that would desync the
    far end's voice-activity detection.
    """

    kind = "audio"

    def __init__(self) -> None:
        from aiortc.mediastreams import MediaStreamTrack  # noqa: PLC0415

        # Composed rather than subclassed at import time so the aiortc import
        # stays inside the function that needs it.
        self._impl = _build_sender_track(MediaStreamTrack)
        self.queue: asyncio.Queue[bytes] = self._impl.queue

    @property
    def track(self) -> Any:
        return self._impl


def _build_sender_track(base: Any) -> Any:
    """Return a live sender-track instance bound to ``aiortc``'s base class."""
    import numpy as np  # noqa: PLC0415
    from av import AudioFrame  # noqa: PLC0415

    class _SenderTrack(base):  # type: ignore[misc, valid-type]
        kind = "audio"

        def __init__(self) -> None:
            super().__init__()
            self.queue: asyncio.Queue[bytes] = asyncio.Queue(
                maxsize=_SEND_QUEUE_MAX
            )
            self._timestamp = 0
            self._residue = b""

        async def recv(self) -> Any:
            # One 20 ms frame per tick, at wall clock.
            await asyncio.sleep(_WIRE_FRAME_SAMPLES / _WIRE_RATE)
            needed = _WIRE_FRAME_SAMPLES * 2  # int16 mono at the wire rate
            while len(self._residue) < needed:
                try:
                    chunk = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._residue += chunk
            if len(self._residue) >= needed:
                payload = self._residue[:needed]
                self._residue = self._residue[needed:]
            else:
                # Underrun: send silence so the far end keeps a continuous
                # stream (its VAD reads gaps as end-of-speech).
                payload = self._residue + b"\x00" * (needed - len(self._residue))
                self._residue = b""
            samples = np.frombuffer(payload, dtype=np.int16).reshape(1, -1)
            frame = AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate = _WIRE_RATE
            frame.pts = self._timestamp
            frame.time_base = fractions.Fraction(1, _WIRE_RATE)
            self._timestamp += _WIRE_FRAME_SAMPLES
            return frame

    return _SenderTrack()


class _MicResampler:
    """Stateful mic resampler to the 48 kHz wire rate.

    Statefulness is the point: resampling each 20 ms chunk INDEPENDENTLY
    restarts the interpolation at every boundary, which stamps a periodic
    discontinuity into the stream 50 times a second — audible as a robotic
    buzz and, worse, degrading what the model's transcriber hears. One
    resampler carried across chunks produces a continuous signal.
    """

    def __init__(self) -> None:
        self._resampler: Any = None
        self._source_rate = 0
        self._pts = 0

    def convert(self, pcm: bytes, source_rate: int) -> bytes:
        if not pcm:
            return b""
        if source_rate == _WIRE_RATE:
            return pcm
        import numpy as np  # noqa: PLC0415
        from av import AudioFrame  # noqa: PLC0415
        from av.audio.resampler import AudioResampler  # noqa: PLC0415

        if self._resampler is None or source_rate != self._source_rate:
            self._resampler = AudioResampler(
                format="s16", layout="mono", rate=_WIRE_RATE
            )
            self._source_rate = source_rate
            self._pts = 0
        samples = np.frombuffer(pcm, dtype=np.int16).reshape(1, -1)
        frame = AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = source_rate
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, source_rate)
        self._pts += samples.shape[1]
        out = bytearray()
        for resampled in self._resampler.resample(frame):
            out += bytes(resampled.planes[0])[: resampled.samples * 2]
        return bytes(out)


class RealtimeWebRtcAudioEndpoint:
    """A Python-owned WebRTC peer that carries realtime audio both ways."""

    def __init__(self, ice_servers: list[Any] | None = None) -> None:
        if not webrtc_available():
            raise WebRtcTransportUnavailable(
                "In-process WebRTC audio needs the 'aiortc' package. Install "
                "Jarvis's requirements to use ChatGPT subscription voice."
            )
        from aiortc import RTCConfiguration, RTCPeerConnection  # noqa: PLC0415

        # Host candidates only by DEFAULT. We are the offerer and the provider's
        # media server is publicly reachable, so our outgoing checks establish
        # the path without a reflexive candidate — measured live: gathering
        # 0.00 s vs 5.01 s with STUN, identical media either way. Those five
        # seconds sat in front of every call, swallowing the user's first
        # sentence. ``stun_ice_servers()`` is the retry for networks that
        # genuinely need it.
        self._pc = RTCPeerConnection(
            configuration=RTCConfiguration(iceServers=list(ice_servers or []))
        )
        self._sender = _PcmSenderTrack()
        self._pc.addTrack(self._sender.track)
        self._recv_queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=_RECV_QUEUE_MAX
        )
        self._reader_task: asyncio.Task[None] | None = None
        self._mic_resampler = _MicResampler()
        self._closed = False

        @self._pc.on("track")
        def _on_track(track: Any) -> None:  # pragma: no cover - callback wiring
            if track.kind != "audio":
                return
            self._reader_task = asyncio.create_task(
                self._drain_remote(track), name="codex-realtime-rtp-reader"
            )

        @self._pc.on("connectionstatechange")
        async def _on_state() -> None:  # pragma: no cover - callback wiring
            state = self._pc.connectionState
            if state in {"failed", "closed"}:
                log.warning("Realtime WebRTC peer entered state %s", state)
                await self._finish_stream()

    async def _drain_remote(self, track: Any) -> None:
        """Decode the remote track into 24 kHz mono PCM for the pipeline."""
        from av.audio.resampler import AudioResampler  # noqa: PLC0415

        resampler = AudioResampler(
            format="s16", layout="mono", rate=_JARVIS_RATE
        )
        try:
            while True:
                frame = await track.recv()
                for resampled in resampler.resample(frame):
                    pcm = bytes(resampled.planes[0])[: resampled.samples * 2]
                    if not pcm:
                        continue
                    try:
                        self._recv_queue.put_nowait(pcm)
                    except asyncio.QueueFull:
                        # Backpressure: drop the OLDEST frame so live speech
                        # stays current instead of drifting further behind.
                        with_suppress_get(self._recv_queue)
                        self._recv_queue.put_nowait(pcm)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the track ends with the call
            log.debug("Realtime WebRTC remote track ended (%s)", type(exc).__name__)
        finally:
            await self._finish_stream()

    async def _finish_stream(self) -> None:
        if self._closed:
            return
        try:
            self._recv_queue.put_nowait(None)
        except asyncio.QueueFull:  # noqa: S110 - the consumer already has a terminator queued
            pass

    async def create_offer(self) -> str:
        """Return a fully gathered offer SDP for the provider handshake."""
        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        sdp = str(self._pc.localDescription.sdp or "")
        if not sdp.strip():
            raise WebRtcTransportUnavailable(
                "The local WebRTC endpoint produced no offer."
            )
        return sdp

    async def apply_answer(self, answer_sdp: str) -> None:
        from aiortc import RTCSessionDescription  # noqa: PLC0415

        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type="answer")
        )

    async def wait_connected(self, timeout_s: float = _ICE_TIMEOUT_S) -> None:
        """Block until the media path is usable, or fail honestly."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            state = self._pc.connectionState
            if state == "connected":
                return
            if state in {"failed", "closed"}:
                raise WebRtcMediaPathUnavailable(
                    f"The realtime WebRTC media path {state}."
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise WebRtcMediaPathUnavailable(
                    "The realtime WebRTC media path did not connect in time."
                )
            await asyncio.sleep(0.05)

    def send_pcm(self, pcm: bytes, sample_rate: int) -> None:
        """Queue one mono int16 PCM chunk for the outgoing media track."""
        if self._closed or not pcm:
            return
        payload = self._mic_resampler.convert(pcm, int(sample_rate or _JARVIS_RATE))
        if not payload:
            return
        try:
            self._sender.queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop the oldest: stale microphone audio is worse than a gap.
            with_suppress_get(self._sender.queue)
            try:
                self._sender.queue.put_nowait(payload)
            except asyncio.QueueFull:  # noqa: S110 - a full queue after a drop means the peer is gone
                pass

    async def next_output_pcm(self) -> bytes | None:
        """Return the next decoded 24 kHz chunk, or ``None`` at end of stream."""
        return await self._recv_queue.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._reader_task
        self._reader_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        try:
            await self._pc.close()
        except Exception:  # noqa: BLE001 - teardown is best effort
            log.debug("Realtime WebRTC peer close failed", exc_info=True)


def with_suppress_get(queue: asyncio.Queue[Any]) -> None:
    """Drop one queued item; used to make room under backpressure."""
    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:  # noqa: S110 - an empty queue already has room
        pass


__all__ = [
    "RealtimeWebRtcAudioEndpoint",
    "WebRtcMediaPathUnavailable",
    "WebRtcTransportUnavailable",
    "stun_ice_servers",
    "webrtc_available",
]
