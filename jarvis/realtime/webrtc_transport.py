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

Two properties are load-bearing and easy to break again:

* **The outgoing track is paced against a monotonic DEADLINE, never with a
  fixed per-frame sleep.** A media track that emits one 20 ms frame per
  ``sleep(0.02)`` costs 20 ms PLUS scheduling latency every iteration, so it
  transmits SLOWER than the microphone records. Measured on a stock Windows
  desktop the naive loop ran one frame per 30.8 ms — 0.65x realtime, because
  the default system timer granularity (~15.6 ms) rounds a 20 ms sleep up to
  two ticks. The microphone queue then grows without bound, the far end hears
  the user seconds late, and once the queue saturates a third of every
  utterance is deleted outright — which no voice-activity detector can
  endpoint. Deadline correction fixes that on every OS and needs no timer
  tricks; raising the Windows timer resolution would only paper over it.
* **The output stream always terminates.** Every consumer of
  ``next_output_pcm`` must eventually see ``None``, whether the track ended,
  the peer failed, or the endpoint was closed. A lost terminator is an
  unbreakable hang, not a lost frame.
"""

from __future__ import annotations

import asyncio
import fractions
import logging
import platform
import sys
from typing import Any

log = logging.getLogger(__name__)

# Opus/WebRTC negotiate 48 kHz; Jarvis's realtime pipeline speaks 24 kHz mono.
_WIRE_RATE = 48_000
_WIRE_FRAME_SAMPLES = 960  # 20 ms
_WIRE_FRAME_BYTES = _WIRE_FRAME_SAMPLES * 2  # int16 mono at the wire rate
_JARVIS_RATE = 24_000
# Outgoing microphone jitter budget, counted in CHUNKS as the capture layer
# delivers them (~32 ms on the desktop path, 20 ms from a browser) — so 12
# chunks is roughly 240-384 ms. That is enough to ride out ordinary scheduling
# jitter and small enough that a genuinely stalled sender discards STALE audio
# instead of handing the far end a microphone seconds out of date. The former
# 200 was not a jitter budget at all: it was ~6.4 s of hidden lag that made a
# broken sender look survivable.
_SEND_QUEUE_MAX = 12
# Incoming provider audio, counted in decoded 20 ms frames (~4 s). This side is
# paced by the network rather than by a local clock, so the bound only has to
# absorb a slow consumer.
_RECV_QUEUE_MAX = 200
# Host candidates connect in about a second or not at all; a longer ceiling is
# dead time in front of the retry that actually helps.
_ICE_TIMEOUT_S = 8.0
# A connected peer that never offers a media track has no usable media path.
_REMOTE_TRACK_TIMEOUT_S = 5.0
# Liveness check, NOT a turn timeout. ChatGPT-Live keeps its media track open
# across the whole call and fills the gaps with silence, so the first frame
# normally follows the handshake immediately — but a user who says nothing must
# never be hung up on (there is no idle auto-hangup in this product), so the
# bound is deliberately far longer than any handshake and no realistic opening
# can reach it. The DETERMINISTIC protection against a dead media path is the
# missing-track check in ``wait_connected``; this only catches a track that
# attached and then stayed permanently mute. If a provider is ever observed to
# withhold RTP entirely during silence, raise or remove this rather than
# shortening it. Firing it ends the output stream honestly, which the adapter
# reports as a RECOVERABLE error (the session rebuilds); it is never a hangup.
_FIRST_AUDIO_TIMEOUT_S = 30.0
# Pacing debt past this point is not jitter: the loop was blocked, and the
# audio for those frames is simply gone. Re-baseline the clock instead of
# bursting a second's worth of frames at the far end.
_MAX_PACING_CATCHUP_S = 1.0
# One line per 50 dropped frames == per second of lost audio at 20 ms frames.
_DROP_LOG_EVERY = 50
# Overflow past the jitter queue drains ORDERED into the sender's elastic
# residue instead of deleting the oldest 20 ms slices. The one legitimate
# burst is the pre-handshake microphone preroll (~1-2 s of the user's FIRST
# sentence flushed at once); dropping ~2/3 of it made ChatGPT-Live hear a
# fragment and invent a user turn (live 2026-08-04). The cap keeps the old
# guarantee that a genuinely stalled sender sheds STALE audio instead of
# hiding many seconds of lag.
_ELASTIC_BACKLOG_MAX_BYTES = int(_WIRE_RATE * 2 * 8.0)
# A media track can never be sent FASTER than realtime, so a delivered backlog
# would otherwise lag the whole call. It drains by skipping SILENT frames
# (energy only, AP-27): pauses shrink, speech is never dropped, and the lag
# disappears at the first gap in the user's speech.
_CATCHUP_MIN_BACKLOG_BYTES = int(_WIRE_RATE * 2 * 0.2)
_CATCHUP_SILENCE_PEAK = 300
_CATCHUP_MAX_DROPS_PER_FRAME = 25


def _is_clean_track_end(exc: BaseException) -> bool:
    """Whether a remote-track exception is the ordinary end of a call.

    aiortc signals a finished track with ``MediaStreamError``; everything else
    means the assistant's voice stopped for a reason worth reporting. Matched
    by NAME so the module keeps importing aiortc lazily (AP-26) and so an
    aiortc refactor cannot turn this into an import error at call time.
    """
    return type(exc).__name__ == "MediaStreamError"


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


def _platform_has_no_webrtc_build() -> bool:
    """Whether this architecture has no installable aiortc dependency chain."""
    return sys.platform == "win32" and platform.machine().upper() in {
        "ARM64",
        "AARCH64",
    }


_import_failure_reported = False


def webrtc_available() -> bool:
    """Whether an in-process WebRTC audio endpoint can be built here."""
    global _import_failure_reported
    try:
        import aiortc  # noqa: F401, PLC0415 - capability probe only
        import av  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001 - a missing optional stack is a capability answer
        # A boolean alone hid the far more common failure: a stack that IS
        # installed and then fails to load its bundled libraries (a broken
        # PyAV, a glibc mismatch on a slim image, a half-finished upgrade).
        # Both cases used to produce the same "install the requirements"
        # advice with nothing in the log to contradict it (AP-30). Reported
        # once so a repeated probe cannot turn this into log spam.
        if not _import_failure_reported:
            _import_failure_reported = True
            log.warning(
                "In-process WebRTC audio is unavailable: the aiortc/PyAV media "
                "stack could not be imported",
                exc_info=True,
            )
        return False
    return True


def webrtc_unavailable_reason() -> str:
    """Return an honest, user-facing reason, or an empty string when usable.

    Exposed so a provider can degrade BEFORE advertising subscription voice,
    rather than accepting a call and failing inside the handshake.
    """
    if webrtc_available():
        return ""
    if _platform_has_no_webrtc_build():
        return (
            "This computer's architecture has no supported WebRTC media build, "
            "so ChatGPT subscription voice cannot run here. Every other voice "
            "provider remains available."
        )
    return (
        "In-process WebRTC audio needs the 'aiortc' package. Install Jarvis's "
        "requirements to use ChatGPT subscription voice."
    )


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

    @property
    def track(self) -> Any:
        return self._impl

    def enqueue(self, payload: bytes) -> int:
        """Queue one wire-rate payload; returns stale bytes shed, if any."""
        return self._impl.enqueue(payload)


def _build_sender_track(base: Any) -> Any:
    """Return a live sender-track instance bound to ``aiortc``'s base class."""
    import numpy as np  # noqa: PLC0415
    from aiortc.mediastreams import MediaStreamError  # noqa: PLC0415
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
            # Wall-clock origin of frame 0. ``None`` until the first frame is
            # produced, so the clock starts when the sender actually starts.
            self._start: float | None = None
            self._pacing_resyncs = 0
            self._catchup_dropped = 0

        def enqueue(self, payload: bytes) -> int:
            """Queue a payload; overflow drains ORDERED into the residue.

            The residue is consumed before the queue and everything queued is
            older than this payload, so draining the queue into the residue
            and appending keeps the stream byte-exact. Returns how many STALE
            bytes the elastic cap shed (0 in any healthy call).
            """
            try:
                self.queue.put_nowait(payload)
                return 0
            except asyncio.QueueFull:
                # Not swallowed: a full queue is the trigger for the elastic
                # cap below, and the bytes it sheds ARE reported, as this
                # method's return value. The caller logs them once per call.
                while True:
                    try:
                        self._residue += self.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        # Drained — the loop's exit condition, not a failure.
                        break
                self._residue += payload
                shed = len(self._residue) - _ELASTIC_BACKLOG_MAX_BYTES
                if shed > 0:
                    self._residue = self._residue[shed:]
                    return shed
                return 0

        async def _pace(self) -> None:
            """Sleep until this frame's wall-clock deadline, never longer.

            Correcting against an absolute deadline is what keeps the track at
            exactly 50 frames per second. A fixed ``sleep(20 ms)`` per frame
            instead accumulates every scheduling delay — on a stock Windows
            desktop that measured 30.8 ms per frame, i.e. a microphone
            transmitted at 0.65x realtime.
            """
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._start is None:
                self._start = now
                return
            delay = self._start + self._timestamp / _WIRE_RATE - now
            if delay > 0:
                await asyncio.sleep(delay)
                return
            behind = -delay
            if behind > _MAX_PACING_CATCHUP_S:
                # The loop was blocked for longer than any jitter budget. The
                # microphone audio for that window is already gone; bursting
                # its frames now would only shove a wall of stale speech at the
                # far end. Re-baseline and say so — silence here is how a
                # stalled event loop reads as "the assistant ignored me".
                self._start = now - self._timestamp / _WIRE_RATE
                self._pacing_resyncs += 1
                log.warning(
                    "Realtime WebRTC sender fell %.2fs behind wall clock "
                    "(resync #%d); that much microphone audio is lost",
                    behind,
                    self._pacing_resyncs,
                )
            # Emit immediately so an ordinary backlog drains, but yield first:
            # a catch-up burst must not starve the rest of the loop.
            await asyncio.sleep(0)

        async def recv(self) -> Any:
            # aiortc's contract: a stopped track raises instead of producing
            # frames forever (see aiortc.mediastreams.AudioStreamTrack).
            if self.readyState != "live":
                raise MediaStreamError
            await self._pace()
            needed = _WIRE_FRAME_BYTES
            catchup_drops = 0
            while True:
                while len(self._residue) < needed:
                    try:
                        chunk = self.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        # The current audio frame is intentionally padded below.
                        break
                    self._residue += chunk
                if len(self._residue) < needed:
                    # Underrun: send silence so the far end keeps a continuous
                    # stream (its VAD reads gaps as end-of-speech).
                    payload = self._residue + b"\x00" * (needed - len(self._residue))
                    self._residue = b""
                    break
                payload = self._residue[:needed]
                self._residue = self._residue[needed:]
                # Backlog catch-up (see _CATCHUP_* above): a media track is
                # clocked at realtime, so a delivered backlog can only drain
                # by skipping frames — and only SILENT ones are skippable
                # without losing speech. Energy only, never content (AP-27).
                if (
                    catchup_drops < _CATCHUP_MAX_DROPS_PER_FRAME
                    and len(self._residue) >= _CATCHUP_MIN_BACKLOG_BYTES
                ):
                    quiet = np.frombuffer(payload, dtype=np.int16)
                    peak = int(np.max(np.abs(quiet.astype(np.int32))))
                    if peak < _CATCHUP_SILENCE_PEAK:
                        catchup_drops += 1
                        continue
                break
            if catchup_drops:
                previous = self._catchup_dropped
                self._catchup_dropped = previous + catchup_drops
                if previous == 0 or previous // 250 != self._catchup_dropped // 250:
                    log.info(
                        "Realtime WebRTC sender is draining its backlog by "
                        "skipping silent frames (%d skipped so far)",
                        self._catchup_dropped,
                    )
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

    def __init__(
        self,
        ice_servers: list[Any] | None = None,
        *,
        first_audio_timeout_s: float = _FIRST_AUDIO_TIMEOUT_S,
    ) -> None:
        unavailable = webrtc_unavailable_reason()
        if unavailable:
            raise WebRtcTransportUnavailable(unavailable)
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
        self._outgoing_drops = 0
        self._recv_dropped = 0
        self._first_audio_timeout_s = max(0.1, float(first_audio_timeout_s))
        self._closed = False
        # The output stream is terminated exactly once, and that fact outlives
        # the queue: a consumer must never be able to wait for a terminator
        # that a full queue swallowed.
        self._ended = False
        self._remote_track_seen = asyncio.Event()
        self._connection_changed = asyncio.Event()

        @self._pc.on("track")
        def _on_track(track: Any) -> None:  # pragma: no cover - callback wiring
            if track.kind != "audio":
                return
            if self._closed:
                log.info(
                    "Ignoring a realtime WebRTC audio track offered after the "
                    "endpoint was closed"
                )
                return
            if self._reader_task is not None and not self._reader_task.done():
                # A second reader would interleave a SECOND producer into one
                # PCM stream, and whichever track ended first would terminate
                # the output while the other was still live. Only the first
                # audio track is the call.
                log.warning(
                    "Realtime WebRTC peer offered another audio track while one "
                    "is already being read; ignoring the extra track"
                )
                return
            self._remote_track_seen.set()
            self._reader_task = asyncio.create_task(
                self._drain_remote(track), name="codex-realtime-rtp-reader"
            )

        @self._pc.on("connectionstatechange")
        async def _on_state() -> None:  # pragma: no cover - callback wiring
            state = self._pc.connectionState
            self._connection_changed.set()
            if state == "failed":
                log.warning("Realtime WebRTC peer connection failed")
                # A failed peer never recovers, and leaving it open keeps the
                # ICE agent, the DTLS transport and the outgoing Opus sender
                # running for the life of the process.
                await self.close()
            elif state == "closed":
                if not self._closed:
                    log.warning("The realtime WebRTC peer was closed by the far end")
                else:
                    log.debug("Realtime WebRTC peer closed")
                self._end_stream()

    async def _drain_remote(self, track: Any) -> None:
        """Decode the remote track into 24 kHz mono PCM for the pipeline."""
        from av.audio.resampler import AudioResampler  # noqa: PLC0415

        resampler = AudioResampler(
            format="s16", layout="mono", rate=_JARVIS_RATE
        )
        dropped = 0
        first_frame_pending = True
        try:
            while True:
                if first_frame_pending:
                    # A track that attaches and then stays permanently mute is
                    # indistinguishable from a healthy call until somebody
                    # bounds the wait. Only the FIRST frame is bounded: the
                    # provider keeps this track open across the whole call and
                    # fills the gaps with silence, but a mid-call pause must
                    # never be mistaken for a dead transport — an idle call is
                    # not a broken one.
                    try:
                        frame = await asyncio.wait_for(
                            track.recv(), timeout=self._first_audio_timeout_s
                        )
                    except TimeoutError:
                        log.warning(
                            "Realtime WebRTC remote track delivered no audio "
                            "within %.1fs; the assistant voice cannot arrive on "
                            "this media path",
                            self._first_audio_timeout_s,
                        )
                        return
                    first_frame_pending = False
                else:
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
                        # Say so — this is a hole punched in the assistant's
                        # own voice, and it used to leave no trace anywhere
                        # (AP-30). Report the first one and then every full
                        # second of loss, so a wedged consumer is visible
                        # without the log becoming the next bottleneck.
                        with_suppress_get(self._recv_queue)
                        self._recv_queue.put_nowait(pcm)
                        dropped += 1
                        self._recv_dropped += 1
                        if dropped == 1 or dropped % _DROP_LOG_EVERY == 0:
                            log.warning(
                                "Realtime WebRTC receive queue is full; dropped "
                                "%d provider audio frame(s) — the reply loses "
                                "that audio",
                                dropped,
                            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the track ends with the call
            if _is_clean_track_end(exc):
                log.debug(
                    "Realtime WebRTC remote track ended (%s)", type(exc).__name__
                )
            else:
                # Anything else means the voice just went mute mid-call. At
                # DEBUG that was invisible to everyone including the user.
                log.warning(
                    "Realtime WebRTC remote track failed; the provider voice "
                    "stops here",
                    exc_info=True,
                )
        finally:
            if dropped:
                log.warning(
                    "Realtime WebRTC remote track dropped %d audio frame(s) in "
                    "total on a full receive queue",
                    dropped,
                )
            self._end_stream()
            # The remote track ending IS the end of this call's media. Holding
            # the peer open past it leaves the outgoing sender transmitting
            # silence for the life of the process.
            await self.close()

    def diagnostics(self) -> dict[str, int]:
        """Transport counters for the end-of-session postmortem event.

        All zero in a healthy call. This method is the one public seam for
        them — reading the sender's private fields here keeps every other
        consumer (session postmortem, probe, forensics) off the internals.
        """
        sender = getattr(self._sender, "_impl", None)
        return {
            "sender_pacing_resyncs": int(getattr(sender, "_pacing_resyncs", 0)),
            "sender_catchup_dropped_frames": int(
                getattr(sender, "_catchup_dropped", 0)
            ),
            "sender_shed_frames": int(self._outgoing_drops),
            "recv_dropped_frames": int(self._recv_dropped),
        }

    def _end_stream(self) -> None:
        """Terminate the output stream exactly once, without fail.

        Synchronous and idempotent on purpose: every caller — the reader's
        exit, a failed peer, ``close()`` — must be able to end the stream
        without awaiting anything, and a consumer parked in
        ``next_output_pcm`` must always be released.
        """
        if self._ended:
            return
        self._ended = True
        if self._recv_queue.full():
            # A full queue is exactly the situation a terminator matters most
            # in: the consumer is behind, which is why the track is ending.
            # Dropping the marker here (as this used to, with a comment
            # claiming the consumer already had one) is an unbreakable hang.
            # Make room instead, and say what the frame cost.
            log.warning(
                "Realtime WebRTC receive queue was full at end of stream; "
                "dropping one audio frame so the end-of-stream marker cannot "
                "be lost"
            )
            with_suppress_get(self._recv_queue)
        try:
            self._recv_queue.put_nowait(None)
        except asyncio.QueueFull:  # pragma: no cover - a bounded queue always has room after a get
            # ``_ended`` still releases every FUTURE call; only a consumer
            # already parked in ``get()`` could miss this, so it must be loud.
            log.error(
                "Realtime WebRTC end-of-stream marker could not be queued; a "
                "waiting audio consumer may not be released"
            )

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

        if "m=audio" not in answer_sdp:
            # Without an audio media section there is no track to receive and
            # no destination for the microphone — a fact worth failing on now
            # rather than discovering as permanent silence. Deliberately NOT a
            # media-path failure: a different ICE configuration cannot add a
            # media section, so this must fail fast instead of buying a second
            # full handshake.
            raise WebRtcTransportUnavailable(
                "The realtime WebRTC answer carries no audio media section."
            )
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type="answer")
        )

    async def _wait_for_connection(self, timeout_s: float) -> None:
        """Await the peer's connected state without polling."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            # Clear BEFORE reading the state: a transition that lands between
            # the read and the clear would otherwise be erased, and the wait
            # would then sit out the full timeout on an already-connected peer.
            self._connection_changed.clear()
            state = self._pc.connectionState
            if state == "connected":
                return
            if state in {"failed", "closed"}:
                raise WebRtcMediaPathUnavailable(
                    f"The realtime WebRTC media path {state}."
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise WebRtcMediaPathUnavailable(
                    "The realtime WebRTC media path did not connect in time."
                )
            try:
                await asyncio.wait_for(
                    self._connection_changed.wait(), timeout=remaining
                )
            except TimeoutError:
                # Re-checked at the top of the loop so a state that changed
                # while we were arming the wait is never missed.
                continue

    async def wait_connected(
        self,
        timeout_s: float = _ICE_TIMEOUT_S,
        *,
        track_timeout_s: float = _REMOTE_TRACK_TIMEOUT_S,
    ) -> None:
        """Block until the media path is usable, or fail honestly.

        "Usable" means BOTH that the peer connected and that the far end
        actually offered an audio track. ICE and DTLS coming up proves only
        that packets can flow; a connected peer with no media track is a
        session that looks healthy and is mute in both directions forever.
        Both failures raise :class:`WebRtcMediaPathUnavailable`, which is a
        handshake verdict — the caller may retry with a different ICE
        configuration.
        """
        await self._wait_for_connection(timeout_s)
        if self._remote_track_seen.is_set():
            return
        try:
            await asyncio.wait_for(
                self._remote_track_seen.wait(), timeout=max(0.0, track_timeout_s)
            )
        except TimeoutError:
            raise WebRtcMediaPathUnavailable(
                "The realtime WebRTC peer connected but offered no audio "
                f"track within {track_timeout_s:.1f}s."
            ) from None

    def send_pcm(self, pcm: bytes, sample_rate: int) -> None:
        """Queue one mono int16 PCM chunk for the outgoing media track."""
        if self._closed or not pcm:
            return
        payload = self._mic_resampler.convert(pcm, int(sample_rate or _JARVIS_RATE))
        if not payload:
            return
        # A burst past the jitter queue (the pre-handshake preroll, a blocked
        # loop) drains ORDERED into the sender's elastic residue and is later
        # caught up by skipping silent frames — user speech is only ever shed
        # once the elastic cap proves the peer is genuinely not draining.
        # SAY so when that happens: this path used to silently delete the
        # user's first sentence, which reads to the user as "it ignored me"
        # and to a log reader as a healthy call.
        shed_bytes = self._sender.enqueue(payload)
        if shed_bytes:
            previous_drops = self._outgoing_drops
            self._outgoing_drops = previous_drops + max(
                1, shed_bytes // _WIRE_FRAME_BYTES
            )
            if (
                previous_drops == 0
                or previous_drops // _DROP_LOG_EVERY
                != self._outgoing_drops // _DROP_LOG_EVERY
            ):
                log.warning(
                    "Realtime WebRTC sender shed ~%d stale outgoing microphone "
                    "frame(s); the far end is not draining audio at wall clock",
                    self._outgoing_drops,
                )

    async def next_output_pcm(self) -> bytes | None:
        """Return the next decoded 24 kHz chunk, or ``None`` at end of stream."""
        if self._ended and self._recv_queue.empty():
            # The terminator has already been consumed; every later call
            # answers from the flag instead of waiting for a second one.
            return None
        return await self._recv_queue.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Release every waiting consumer BEFORE the peer teardown: closing the
        # endpoint and ending its output stream are one fact, and a consumer
        # parked in ``next_output_pcm`` used to wait here forever.
        self._end_stream()
        task = self._reader_task
        self._reader_task = None
        # The reader itself calls ``close()`` when its track ends; a task may
        # never await its own completion.
        if (
            task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
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
    "webrtc_unavailable_reason",
]
