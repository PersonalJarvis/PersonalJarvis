"""Desktop playback adapter for the transport-neutral realtime session."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

import numpy as np

from jarvis.audio.vad import VAD_FRAME_SAMPLES, SileroEndpointer
from jarvis.core.protocols import AudioChunk

log = logging.getLogger("jarvis.realtime.desktop")


class DesktopRealtimeBargeInDetector:
    """Detect deliberate desktop barge-in on the existing microphone stream.

    Desktop realtime stays half-duplex at the provider boundary because raw
    PortAudio capture has no portable acoustic echo cancellation. Dropping the
    microphone entirely while the assistant speaks also makes interruption
    impossible, though. This detector therefore inspects those otherwise-dropped
    16 kHz frames locally with the bundled torch-free Silero ONNX model.

    Detection deliberately matches the conservative classic-pipeline policy:
    a startup grace period, a high speech probability, and sustained speech.
    The returned PCM contains a short pre-speech window plus the confirmed speech
    frames, allowing the caller to cancel output and forward the user's opening
    syllables to any realtime provider without making the provider/model itself
    responsible for desktop echo suppression.
    """

    # Frames quieter than this (float RMS, 1.0 = full scale) skip the Silero
    # inference entirely and count as non-speech. Two field problems on a
    # speakers+mic laptop (old Intel MacBook, BUG-062) share this one gate:
    # (a) per-frame ONNX on the audio-critical loop starved the 120 ms write
    # batches -> constant playback stutter; (b) the assistant's own speaker
    # echo fed the detector and false barge-ins truncated the answer. AP-27
    # empirics anchor the value: silence ghosts sit <= 0.0043, quiet real
    # speech reaches ~0.009 — 0.010 keeps deliberate interruptions (normal
    # speaking volume) while dropping silence, hiss, and moderate echo.
    # Trade-off (documented): whisper-quiet barge-in no longer triggers.
    _DEFAULT_MIN_FRAME_RMS = 0.010

    # Adaptive echo floor (BUG-084): a FIXED floor cannot cover every
    # speaker/mic coupling — on built-in laptop speakers next to the built-in
    # mic the assistant's own voice lands far above 0.010, so the static gate
    # passes it, Silero (which cannot tell whose voice it hears) confirms, and
    # the false barge truncates the answer AND opens the self-talk loop. The
    # only word-agnostic discriminator we own is the echo's measured loudness:
    # while output is active the detector keeps a rolling window of recent
    # frame RMS values and derives the gate floor from its 90th percentile
    # times a safety margin — i.e. "louder than the loudest sustained thing
    # the room currently produces", which during playback IS our own echo.
    # The newest ``_ADAPTIVE_FLOOR_LAG_FRAMES`` frames are EXCLUDED from the
    # baseline so a user starting to speak is judged against the pure-echo
    # past, never against their own rising voice (the lag must stay above
    # ``consecutive_frames`` or sustained genuine speech would raise its own
    # bar before it can confirm). The floor is clamped to the static minimum
    # below and ``_DEFAULT_ADAPTIVE_FLOOR_CAP`` above so a shouting user can
    # always break through. The cap must sit ABOVE any realistic echo RMS
    # (loud open speakers reach ~0.06-0.15) — a cap below the echo level
    # would re-open the false-barge hole it exists to close; 0.25 still
    # leaves loud close-range speech (>0.25) able to interrupt worst-case
    # coupling. ``min_frame_rms=0.0`` disables both gates (test hook /
    # explicit opt-out).
    _DEFAULT_ADAPTIVE_FLOOR_MARGIN = 1.4
    _DEFAULT_ADAPTIVE_FLOOR_CAP = 0.25
    _ADAPTIVE_FLOOR_LAG_FRAMES = 16
    _ADAPTIVE_FLOOR_WINDOW_FRAMES = 96
    _ADAPTIVE_FLOOR_MIN_BASELINE_FRAMES = 8

    def __init__(
        self,
        *,
        grace_s: float = 1.5,
        speech_threshold: float = 0.97,
        consecutive_frames: int = 12,
        pre_speech_frames: int = 10,
        min_frame_rms: float | None = None,
        adaptive_floor_margin: float | None = None,
        adaptive_floor_cap: float | None = None,
        output_active: Callable[[], bool] | None = None,
        model: Any = None,
    ) -> None:
        self._grace_s = max(0.0, float(grace_s))
        self._speech_threshold = min(1.0, max(0.0, float(speech_threshold)))
        self._consecutive_frames = max(1, int(consecutive_frames))
        self._pre_speech_frames = max(1, int(pre_speech_frames))
        self._min_frame_rms = (
            self._DEFAULT_MIN_FRAME_RMS if min_frame_rms is None else max(0.0, float(min_frame_rms))
        )
        self._adaptive_floor_margin = (
            self._DEFAULT_ADAPTIVE_FLOOR_MARGIN
            if adaptive_floor_margin is None
            else max(1.0, float(adaptive_floor_margin))
        )
        self._adaptive_floor_cap = (
            self._DEFAULT_ADAPTIVE_FLOOR_CAP
            if adaptive_floor_cap is None
            else max(self._min_frame_rms, float(adaptive_floor_cap))
        )
        # Surface TTS can spend more than a second synthesizing and opening the
        # output stream after the response is logically marked SPEAKING. The
        # grace window must begin at physical playback, not at that early state
        # edge, or synthesis latency consumes the echo-calibration period and
        # the assistant's first sentence is misclassified as a barge-in. The
        # desktop pipeline supplies the AudioPlayer's process-local playback
        # probe; omitted keeps the detector usable in isolation and tests.
        self._output_active = output_active
        self._playback_started = False
        # The lag must exceed the confirm run so genuine sustained speech is
        # always judged against a baseline formed BEFORE it started.
        self._adaptive_floor_lag = max(
            self._consecutive_frames + 4, self._ADAPTIVE_FLOOR_LAG_FRAMES
        )
        self._rms_history: deque[float] = deque(
            maxlen=self._ADAPTIVE_FLOOR_WINDOW_FRAMES + self._adaptive_floor_lag
        )
        self._model = model or SileroEndpointer(
            speech_threshold=self._speech_threshold
        )
        self._ready = False
        self._active = False
        self._started_at = 0.0
        self._residual = np.empty(0, dtype=np.dtype("<i2"))
        self._pre_buffer: deque[np.ndarray] = deque(maxlen=self._pre_speech_frames)
        self._candidate_frames: list[np.ndarray] = []
        self._speech_run = 0

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def active(self) -> bool:
        return self._active

    def warmup(self) -> None:
        """Load the CPU ONNX session outside the realtime audio callback."""

        self._model._ensure_model()
        self._ready = True

    def start_output(self) -> None:
        """Arm a fresh detector window for one assistant audio response."""

        self._active = True
        self._started_at = time.monotonic()
        self._playback_started = self._output_active is None
        self._reset_buffers()
        # Fresh echo calibration per response: the grace window (pure speaker
        # echo by construction) re-trains the adaptive floor for the volume /
        # coupling of THIS answer instead of trusting a stale estimate.
        self._rms_history.clear()

    def stop_output(self) -> None:
        self._active = False
        self._playback_started = False
        self._reset_buffers()

    def feed(self, pcm16: bytes) -> bytes | None:
        """Return buffered user PCM once sustained speech is confirmed."""

        if not self._active or not self._ready or len(pcm16) < 2:
            return None

        if not self._playback_started:
            # Keep half-duplex input local while synthesis/stream setup is
            # pending, but do not let that silent lead-in age the grace clock.
            # The first frame observed during real playback starts a full,
            # fresh calibration window built only from speaker echo.
            if self._output_active is not None and not self._output_active():
                self._reset_buffers()
                return None
            self._playback_started = True
            self._started_at = time.monotonic()

        usable = len(pcm16) - (len(pcm16) % 2)
        samples = np.frombuffer(pcm16[:usable], dtype=np.dtype("<i2"))
        if self._residual.size:
            samples = np.concatenate([self._residual, samples])
        frame_count = samples.size // VAD_FRAME_SAMPLES

        if time.monotonic() - self._started_at < self._grace_s:
            # Never let speaker echo collected during the grace period become
            # user preroll once detection arms — but DO measure it: grace-time
            # frames are our own playback echo, the calibration data the
            # adaptive floor is built from (BUG-084).
            if frame_count:
                framed_samples = frame_count * VAD_FRAME_SAMPLES
                grace_frames = samples[:framed_samples].reshape(
                    frame_count, VAD_FRAME_SAMPLES
                )
                for frame in grace_frames:
                    self._rms_history.append(self._frame_rms(frame))
            self._reset_buffers()
            return None

        if frame_count == 0:
            self._residual = samples.copy()
            return None

        framed_samples = frame_count * VAD_FRAME_SAMPLES
        frames = samples[:framed_samples].reshape(frame_count, VAD_FRAME_SAMPLES)
        trailing = samples[framed_samples:].copy()

        for index, frame in enumerate(frames):
            normalized = frame.astype(np.float32) / 32768.0
            frame_rms = float(np.sqrt(np.mean(np.square(normalized))))
            # Energy pre-gate (BUG-062) + adaptive echo floor (BUG-084):
            # frames below the gate never reach the ONNX model — this is the
            # loop-load fix and the echo damper in one. The adaptive floor is
            # computed BEFORE this frame enters the history, so every frame is
            # judged against the lagged pure-echo past, never against itself.
            gate = self._effective_floor()
            self._rms_history.append(frame_rms)
            if gate > 0.0 and frame_rms < gate:
                probability = 0.0
            else:
                probability = float(self._model._prob(normalized))
            if probability >= self._speech_threshold:
                if self._speech_run == 0:
                    self._candidate_frames = [part.copy() for part in self._pre_buffer]
                self._candidate_frames.append(frame.copy())
                self._speech_run += 1
                if self._speech_run >= self._consecutive_frames:
                    tail_frames = frames[index + 1 :].reshape(-1)
                    parts = [*self._candidate_frames]
                    if tail_frames.size:
                        parts.append(tail_frames.copy())
                    if trailing.size:
                        parts.append(trailing)
                    detected = np.concatenate(parts).astype(
                        np.dtype("<i2"), copy=False
                    )
                    self._active = False
                    self._reset_buffers()
                    return detected.tobytes()
                continue

            # A short high-probability burst is speaker bleed, not a barge-in.
            # Retain only the rolling pre-speech window for the next candidate.
            for candidate in self._candidate_frames:
                self._pre_buffer.append(candidate)
            self._candidate_frames = []
            self._speech_run = 0
            self._pre_buffer.append(frame.copy())

        self._residual = trailing
        return None

    @staticmethod
    def _frame_rms(frame: np.ndarray) -> float:
        normalized = frame.astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(np.square(normalized))))

    def _effective_floor(self) -> float:
        """Current energy gate: static minimum or the learned echo floor.

        ``min_frame_rms == 0.0`` disables gating entirely (adaptive included) —
        the explicit logic-test / opt-out hook. Otherwise the floor is the 90th
        percentile of the lagged RMS history times the safety margin, clamped
        to [static minimum, cap]. With too little history (fresh detector, no
        playback echo measured yet) it falls back to the static minimum.
        """

        if self._min_frame_rms <= 0.0:
            return 0.0
        baseline = list(self._rms_history)[: -self._adaptive_floor_lag]
        if len(baseline) < self._ADAPTIVE_FLOOR_MIN_BASELINE_FRAMES:
            return self._min_frame_rms
        learned = self._adaptive_floor_margin * float(np.percentile(baseline, 90))
        return min(self._adaptive_floor_cap, max(self._min_frame_rms, learned))

    def _reset_buffers(self) -> None:
        self._residual = np.empty(0, dtype=np.dtype("<i2"))
        self._pre_buffer.clear()
        self._candidate_frames = []
        self._speech_run = 0


class DesktopRealtimePlayback:
    """Feed provider PCM deltas through one persistent ``AudioPlayer`` stream.

    The bounded queue applies backpressure when an output device is slower than
    the provider. A turn-complete marker drains naturally; barge-in or teardown
    stops the device immediately and discards queued audio.
    """

    def __init__(
        self,
        player: Any,
        *,
        sample_rate: int = 24_000,
        max_queue_chunks: int = 200,
        finish_timeout_s: float = 120.0,
    ) -> None:
        self._player = player
        self._sample_rate = int(sample_rate)
        self._max_queue_chunks = max(1, int(max_queue_chunks))
        self._finish_timeout_s = max(1.0, float(finish_timeout_s))
        self._queue: asyncio.Queue[AudioChunk | None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def send_binary(self, pcm: bytes) -> None:
        if not pcm or self._closed:
            return
        if self._task is None or self._task.done():
            self._queue = asyncio.Queue(maxsize=self._max_queue_chunks)
            self._task = asyncio.create_task(
                self._player.play_chunks(self._chunks(self._queue)),
                name="realtime-desktop-playback",
            )
            # A terminal surface cancellation can race a provider callback
            # that is already unwinding. Always retrieve the native playback
            # result even if that callback loses its final await; explicit
            # ``finish_turn`` callers still receive the same exception.
            self._task.add_done_callback(self._observe_playback_result)
        assert self._queue is not None
        await self._queue.put(
            AudioChunk(pcm=bytes(pcm), sample_rate=self._sample_rate, timestamp_ns=0)
        )

    def set_sample_rate(self, sample_rate: int) -> None:
        """Set the rate announced by the accepted provider handshake.

        Providers must announce this before their first audio delta. Refuse a
        mid-stream rate change because one ``AudioPlayer`` stream cannot safely
        reinterpret already queued PCM at a different rate.
        """
        rate = int(sample_rate)
        if rate <= 0:
            raise ValueError("Realtime output sample rate must be positive")
        if self._task is not None and not self._task.done():
            raise RuntimeError("Cannot change realtime sample rate during playback")
        self._sample_rate = rate

    async def finish_turn(self) -> None:
        queue, task = self._queue, self._task
        if queue is None or task is None:
            return
        await queue.put(None)
        try:
            await asyncio.wait_for(task, timeout=self._finish_timeout_s)
        except TimeoutError:
            self._player.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            # The playback worker was canceled by a concurrent barge-in.
        except Exception:
            if self._task is task:
                raise
            # A concurrent cancel detached this task and aborted its PortAudio
            # stream. The blocked write can then unwind with "Stream is
            # stopped"; that is the expected result of barge-in, not a failed
            # realtime session.
        finally:
            # Keep the active task discoverable while it drains. A user can
            # barge in after the provider has sent turn_complete but before the
            # local speaker queue is empty; cancel() must still be able to stop
            # and drain this exact task during that window.
            if self._task is task:
                self._queue = None
                self._task = None

    async def cancel(self) -> None:
        queue, task = self._detach()
        task_was_done = task is not None and task.done()
        self._player.stop()
        if queue is None or task is None:
            return
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - race-safe guard
                break
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:  # pragma: no cover - queue was drained above
            pass
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except Exception:
            if task_was_done:
                raise
            # stop() deliberately aborts the live PortAudio stream. A write
            # already running in its worker thread can report that abort as a
            # playback exception while the task unwinds.

    async def close(self) -> None:
        # Terminal close differs from an ordinary barge-in ``cancel``: later
        # provider callbacks belong to a dead voice surface and must never
        # create a fresh OutputStream after teardown has started.
        self._closed = True
        await self.cancel()

    @staticmethod
    def _observe_playback_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            log.debug("Realtime desktop playback task ended with %r", exc)

    def _detach(
        self,
    ) -> tuple[asyncio.Queue[AudioChunk | None] | None, asyncio.Task[None] | None]:
        queue, task = self._queue, self._task
        self._queue = None
        self._task = None
        return queue, task

    @staticmethod
    async def _chunks(
        queue: asyncio.Queue[AudioChunk | None],
    ) -> AsyncIterator[AudioChunk]:
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            yield chunk


__all__ = ["DesktopRealtimeBargeInDetector", "DesktopRealtimePlayback"]
