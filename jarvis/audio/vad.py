"""Voice Activity Detection via Silero-VAD with a three-tier fallback chain.

Silero-VAD is a ~2 MB PyTorch model that operates on 16 kHz audio frames of
exactly 512 samples (32 ms) and delivers a speech probability (0.0-1.0) per
frame. Significantly more precise than WebRTC-VAD in noisy/music environments.

The per-frame speech score degrades through three tiers so voice capture never
bricks on a platform without a working native runtime:

  1. Silero ONNX (bundled model on base CPU onnxruntime) — the default.
  2. WebRTC VAD (``webrtcvad``, ships in ``[local-voice]``/``[full]``) when
     the ONNX runtime cannot load or execute.
  3. Portable RMS energy endpointing when neither engine is available.

Used in "endpointing" mode: once a silence phase of `silence_ms` duration is
detected after active speech, the utterance is considered complete and the
pipeline sends the audio to Whisper.
"""
from __future__ import annotations

import logging
from collections import deque
from collections.abc import AsyncIterator, Callable

import numpy as np

from jarvis.audio import mic_level
from jarvis.audio.capture import pcm_bytes_to_np
from jarvis.audio.vad_reasons import (
    VAD_REASON_FALSE_START,
    VAD_REASON_MAX_UTTERANCE,
    VAD_REASON_SILENCE,
    VAD_REASON_STT_STABLE,
)
from jarvis.core.protocols import AudioChunk

VAD_SAMPLE_RATE = 16_000
VAD_FRAME_SAMPLES = 512       # fixed requirement from Silero

# WebRTC VAD middle tier: aggressiveness 0 (permissive) .. 3 (strict). 2 keeps
# recall high enough for endpointing while filtering steady ambient noise.
_WEBRTC_AGGRESSIVENESS = 2
# webrtcvad accepts only 10/20/30 ms frames at 16 kHz; 480 samples (30 ms) is
# the largest slice that fits inside every 512-sample Silero frame.
_WEBRTC_FRAME_SAMPLES = 480

# Upper bound on the adaptive patience grant, expressed as a multiple of the
# user-configured base silence window. A long dictation / delegation widens the
# silence window so a thinking pause is not cut, but the grant must never stretch
# the window to a fixed absolute value that ignores the slider: a deliberately
# short "thinking pause" (e.g. 1.0 s) was being silently forced to ~3 s, so the
# turn never submitted near the configured threshold. At the 1.5 s default this
# is a no-op (2 x 1.5 s == the historical fixed 3 s grant). See
# tests/unit/audio/test_vad_turn_taking.py (stuck-in-LISTENING regression).
_PATIENCE_FACTOR = 2

log = logging.getLogger("jarvis.audio.vad")


class SileroEndpointer:
    """Buffers audio chunks, runs VAD, yields complete utterances.

    State machine:
      IDLE       → only frames with prob < threshold → nothing yielded
      SPEAKING   → >= `min_speech_frames` active frames seen → collecting audio
      ENDING     → >= `silence_frames` silence after speaking → yield utterance, back to IDLE
    """

    def __init__(
        self,
        speech_threshold: float = 0.5,
        # Product default = the configured "Thinking pause" (1.5 s "1.5s rule",
        # jarvis.core.config.SpeechConfig.vad_silence_ms). Every real caller passes
        # the configured value explicitly (pipeline from config; wake overrides to
        # 500 ms); this default only governs a bare construction, and it must match
        # the product default so a stale pre-1.5s-rule 1.0 s can never creep back.
        silence_ms: int = 1500,
        min_speech_ms: int = 250,
        max_utterance_s: int = 30,
        min_speech_rms: float = 0.002,
        relative_silence_rms_ratio: float = 0.22,
        cancel_hysteresis_ms: int = 160,
        on_speech_start: Callable[[], None] | None = None,
        on_silence_start: Callable[[], None] | None = None,
        on_silence_cancel: Callable[[], None] | None = None,
        on_endpoint: Callable[[str], None] | None = None,
        probe_callback: Callable[[bytes, bool], None] | None = None,
        probe_interval_ms: int = 1000,
        probe_min_active_ms: int = 1500,
        probe_tail_ms: int = 2000,
        tail_loud_window_ms: int = 320,
        long_utterance_speech_ms: int = 2000,
        long_utterance_silence_ms: int = 3000,
    ) -> None:
        self._threshold = speech_threshold
        self._silence_frames = max(1, silence_ms // 32)
        # Adaptive endpoint patience: extra silence frames granted to the CURRENT
        # utterance on top of ``_silence_frames``. Raised via
        # ``extend_silence_window`` when the STT probe sees a delegation being
        # composed (longer thinking pauses than a short command), and reset to 0
        # at every speech start so short commands keep the snappy default and the
        # patience never leaks across utterances. See
        # tests/unit/audio/test_vad_turn_taking.py (adaptive-patience block).
        self._extra_silence_frames = 0
        self._min_speech_frames = max(1, min_speech_ms // 32)
        self._max_samples = max_utterance_s * VAD_SAMPLE_RATE
        # Number of *consecutive* speech frames required to cancel an
        # in-progress silence timer. A single ambient/speaker-bleed spike
        # (music beat, fan gust, TV transient) must not reset a long
        # accumulated silence, otherwise the silence endpoint never fires in
        # a noisy room and the turn drags to the max_utterance hard cap.
        # See test_brief_speaker_bleed_spikes_do_not_reset_silence_timer.
        self._cancel_hysteresis_frames = max(1, cancel_hysteresis_ms // 32)
        # Torch-free Silero VAD: when ONNX Runtime is available, the bundled
        # model runs on its CPU provider with numpy-managed recurrent state.
        # Platforms without a compatible ONNX Runtime wheel degrade through a
        # three-tier chain instead of losing the whole voice path: WebRTC VAD
        # (when the [local-voice]/[full] extra ships it), then portable
        # RMS-based energy endpointing as the floor. Lazy: the preferred
        # engine is created on first use.
        self._session = None
        self._vad_state = None  # np.ndarray [2,1,128] float32, carried per frame
        self._vad_context = None  # np.ndarray [1,64] float32, carried per frame
        self._webrtc = None  # webrtcvad.Vad instance for the middle tier
        self._energy_only = False

        self._min_speech_rms = min_speech_rms
        self._relative_silence_rms_ratio = relative_silence_rms_ratio
        self._on_speech_start = on_speech_start
        self._on_silence_start = on_silence_start
        self._on_silence_cancel = on_silence_cancel
        self._on_endpoint = on_endpoint
        # External stability probe (typically STT-based). Guards against
        # speaker bleed where Silero keeps detecting "speech" while Whisper
        # already shows the user has stopped talking. The probe receives only
        # the tail of the active buffer, not the full utterance, so probe
        # transcription stays cheap and the comparison is anchored on recent
        # audio (whether new speech arrived in the last `probe_tail_ms`).
        self._probe_callback = probe_callback
        self._probe_interval_frames = max(1, probe_interval_ms // 32)
        self._probe_min_active_frames = max(1, probe_min_active_ms // 32)
        self._probe_tail_frames = max(1, probe_tail_ms // 32)
        # Window (much shorter than the transcription tail) over which the
        # probe's loud/quiet discriminator is measured — see the probe block.
        self._tail_loud_window_frames = max(1, tail_loud_window_ms // 32)
        self._endpoint_requested = False
        # Autonomous long-utterance patience (probe-independent). Once this much
        # ACTIVE speech has accumulated in the current utterance, the user is
        # clearly dictating a long request, not a short command — grant the wider
        # silence window so a thinking pause is not cut. Fixes session 71f2d2de:
        # the STT probe never surfaced a partial, so the probe-driven
        # extend_silence_window never armed. Resets per utterance via the
        # existing _extra_silence_frames=0 at speech start.
        self._long_utterance_speech_frames = max(1, long_utterance_speech_ms // 32)
        self._long_utterance_silence_ms = int(long_utterance_silence_ms)

    def request_endpoint(self) -> None:
        """External observer requests the current utterance ends now.

        Used by the STT stability probe to break out of speaker-bleed
        situations where Silero would keep streaming forever. Yields the
        accumulated audio with reason ``stt_stable`` if enough real speech
        was collected, otherwise discards as ``false_start``.
        """
        self._endpoint_requested = True

    def extend_silence_window(self, total_ms: int) -> None:
        """Raise the silence-endpoint threshold for the CURRENT utterance.

        The STT probe calls this when the live partial transcript shows a
        delegation is being composed ("spawn a sub-agent that ..."), which
        involves longer thinking pauses than a short command. The window only
        ever GROWS within an utterance and resets to the base ``silence_ms`` at
        the next speech start — so short commands keep the snappy default and the
        patience never leaks into a later turn. The opposite of
        ``request_endpoint`` (which shortens), so the two never fight: a genuine
        speaker-bleed force still ends the turn, only the *natural* silence
        endpoint is made more patient.
        """
        want_frames = max(1, int(total_ms) // 32)
        # Cap the grant relative to the configured base so the slider keeps
        # governing the wait: the window may at most grow to _PATIENCE_FACTOR x
        # the base, never to a fixed absolute value that overrides a short
        # setting (the 1.0 s "Thinking pause" stuck-in-LISTENING bug, 2026-06-29).
        # No-op at the 1.5 s default (2 x 1.5 s >= the 3 s callers request).
        want_frames = min(want_frames, self._silence_frames * _PATIENCE_FACTOR)
        extra = max(0, want_frames - self._silence_frames)
        if extra > self._extra_silence_frames:
            self._extra_silence_frames = extra

    def set_silence_window_ms(self, ms: int) -> None:
        """Live-update the BASE silence window and the matching hard cap.

        The running ``utterances()`` loop reads ``_effective_silence_frames`` and
        ``_max_samples`` on every frame, so a change here takes effect on the next
        processed frame — no pipeline rebuild (the user-tunable "think buffer",
        desktop Settings → Voice). ``_extra_silence_frames`` (delegation patience)
        stays additive on top of the new base. The max-utterance cap grows with
        the window so a long thinking pause is never beheaded by the safety net
        (maintainer choice 2026-06-16): cap = max(8 s, ceil(window_s) + 5 s).
        Clamps defensively to 500–5000 ms — the route validates, but the VAD must
        not trust callers or a stray value could wedge endpointing.
        """
        ms = max(500, min(5000, int(ms)))
        self._silence_frames = max(1, ms // 32)
        cap_s = max(8, (ms + 999) // 1000 + 5)  # (ms+999)//1000 == ceil(ms/1000)
        self._max_samples = cap_s * VAD_SAMPLE_RATE

    @property
    def _effective_silence_frames(self) -> int:
        """Silence frames required to end the turn, including any patience grant."""
        return self._silence_frames + self._extra_silence_frames

    def _ensure_model(self) -> None:
        if (
            self._session is not None
            or getattr(self, "_webrtc", None) is not None
            or getattr(self, "_energy_only", False)
        ):
            return
        try:
            import os

            import onnxruntime  # already warm when the wake model uses it

            # Prefer the model bundled in-repo (jarvis/assets/vad/silero_vad.onnx):
            # it ships in every supported install without the torch-pulling
            # ``silero-vad`` package. We never import that package; a partial
            # source layout may only use its model path as a final fallback.
            from jarvis.assets import bundled_silero_vad_model

            bundled = bundled_silero_vad_model()
            if bundled is not None:
                model_path = str(bundled)
            else:
                import importlib.util

                spec = importlib.util.find_spec("silero_vad")
                if spec is None or spec.origin is None:
                    raise RuntimeError(
                        "neither the bundled Silero model nor an installed model "
                        "asset is available"
                    )
                model_path = os.path.join(
                    os.path.dirname(spec.origin), "data", "silero_vad.onnx"
                )
            opts = onnxruntime.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self._session = onnxruntime.InferenceSession(
                model_path, providers=["CPUExecutionProvider"], sess_options=opts
            )
            # Recurrent state + 64-sample context, mirroring
            # silero_vad.OnnxWrapper.
            self._vad_state = np.zeros((2, 1, 128), dtype=np.float32)
            self._vad_context = np.zeros((1, 64), dtype=np.float32)
        except Exception as exc:
            self._enable_degraded_fallback(exc)

    def _enable_degraded_fallback(self, exc: Exception) -> None:
        """Drop from Silero to the best remaining tier (WebRTC, then energy)."""
        self._session = None
        self._vad_state = None
        self._vad_context = None
        try:
            import webrtcvad

            self._webrtc = webrtcvad.Vad(_WEBRTC_AGGRESSIVENESS)
            log.warning(
                "Silero ONNX VAD is unavailable; using WebRTC VAD endpointing "
                "for this session (%s: %s)",
                type(exc).__name__,
                exc,
            )
        except Exception:
            self._enable_energy_fallback(exc)

    def _enable_energy_fallback(self, exc: Exception) -> None:
        """Keep endpointing available when every optional native engine fails."""
        self._session = None
        self._vad_state = None
        self._vad_context = None
        self._webrtc = None
        self._energy_only = True
        log.warning(
            "Silero ONNX VAD and WebRTC VAD are unavailable; using portable "
            "CPU energy endpointing for this session (%s: %s)",
            type(exc).__name__,
            exc,
        )

    def _energy_prob(self, frame: np.ndarray) -> float:
        """Return a deterministic speech score for the portable VAD floor."""
        rms = float(np.sqrt(np.mean(np.square(frame))))
        minimum = float(getattr(self, "_min_speech_rms", 0.002))
        return 1.0 if rms >= minimum else 0.0

    def _webrtc_prob(self, frame: np.ndarray) -> float:
        """Binary speech score from the WebRTC VAD middle tier."""
        try:
            samples = np.asarray(frame, dtype=np.float32).reshape(-1)
            buf = _float32_to_int16_bytes(samples[:_WEBRTC_FRAME_SAMPLES])
            return 1.0 if self._webrtc.is_speech(buf, VAD_SAMPLE_RATE) else 0.0
        except Exception as exc:
            self._enable_energy_fallback(exc)
            return self._energy_prob(frame)

    def _prob(self, frame: np.ndarray) -> float:
        """Per-frame speech probability (must be exactly 512 float32 samples).

        Prefer the bundled Silero ONNX model on the CPU. If its optional native
        runtime cannot load or execute on this platform, fall back to WebRTC
        VAD when available, and finally to a portable RMS score. The
        surrounding state machine still supplies relative-noise calibration,
        hysteresis, silence timing, and the maximum-duration cap.
        """
        self._ensure_model()
        x = np.ascontiguousarray(frame, dtype=np.float32).reshape(1, -1)  # [1,512]
        if getattr(self, "_webrtc", None) is not None:
            return self._webrtc_prob(x)
        if getattr(self, "_energy_only", False):
            return self._energy_prob(x)
        assert self._session is not None
        try:
            x_full = np.concatenate([self._vad_context, x], axis=1)  # [1,576]
            out, new_state = self._session.run(
                None,
                {
                    "input": x_full,
                    "state": self._vad_state,
                    "sr": np.array(VAD_SAMPLE_RATE, dtype=np.int64),
                },
            )
            self._vad_state = new_state
            self._vad_context = x_full[:, -64:]
            return float(np.asarray(out).reshape(-1)[0])
        except Exception as exc:
            self._enable_degraded_fallback(exc)
            if getattr(self, "_webrtc", None) is not None:
                return self._webrtc_prob(x)
            return self._energy_prob(x)

    async def utterances(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[bytes]:
        """Consumes mic chunks and yields complete utterance PCM bytes.

        Each yielded `bytes` value is int16 PCM at 16 kHz — ready to feed
        directly into Whisper.
        """
        self._ensure_model()

        # Re-frame: mic delivers 100 ms blocks (1600 samples), VAD wants 512 samples.
        # Buffer samples until >= 512 are available, split them, keep the remainder.
        residual = np.empty(0, dtype=np.float32)
        # Rolling buffer of VAD frames for pre-speech context (preserves leading syllables).
        pre_buffer: deque[np.ndarray] = deque(maxlen=10)  # ~320 ms
        active_frames: list[np.ndarray] = []
        silent_run = 0
        # Consecutive speech frames seen *while a silence timer is running*.
        # Only sustained speech (>= self._cancel_hysteresis_frames) cancels
        # the timer; isolated bleed spikes are absorbed.
        resume_run = 0
        speaking = False
        total_frames = 0
        speech_frames = 0
        peak_speech_rms = 0.0
        last_probe_frame = 0
        self._endpoint_requested = False
        self._extra_silence_frames = 0
        # True after a ``max_utterance`` forced cut until the NEXT yield. The
        # consumer buffered that fragment and waits for another endpoint to
        # finalize it — but a regular silence endpoint only exists inside an
        # active speech phase. If the user finished their sentence right
        # inside the capped window (or only a false-start blip follows), no
        # endpoint would ever fire again and the buffered turn would hang
        # forever ("Jarvis listens forever", 2026-06-09). While pending,
        # ``silence_ms`` of idle silence yields an empty tail with reason
        # ``silence`` so the consumer flushes its carry.
        tail_pending = False
        tail_silent_run = 0
        # Wall-clock anchor for the silence timer (real-time gap credit). The
        # endpoint decision counts silent FRAMES and treats each as 32 ms of
        # elapsed time — which silently assumes frames arrive in lockstep with
        # real time. On hardware that cannot consume the mic stream in real time
        # the capture queue overflows and whole chunks are DROPPED (and delivery
        # lags), so the frame count runs slow or stalls and the turn ends far too
        # late or never (the "stuck listening / never advances" bug on weaker
        # laptops). The surviving chunks still carry the true capture wall-clock
        # in ``timestamp_ns``; ``prev_chunk_ts`` lets us detect a jump larger than
        # a chunk's own audio duration (= dropped/lagged time) and credit it to
        # the silence timer below, so end-of-speech stays anchored to real time,
        # not to how many frames happened to survive.
        prev_chunk_ts: int = 0

        async for chunk in chunks:
            # Real-time gap in frames: how much silent time to credit if this
            # chunk arrives after a capture drop/lag. Inert (<= 0) when timestamps
            # are absent / unrealistic (tests use tiny counters) or contiguous (a
            # machine that keeps up), and capped so one huge gap cannot overshoot
            # the window. Applied only inside an active end-of-speech silence run
            # (see the silence branch), never to speech or a fresh utterance.
            gap_frames = 0
            ts = int(getattr(chunk, "timestamp_ns", 0) or 0)
            n_samples = len(chunk.pcm) // 2  # int16 mono
            nominal_ms = (n_samples / VAD_SAMPLE_RATE * 1000.0) if n_samples else 0.0
            if prev_chunk_ts and ts > prev_chunk_ts and nominal_ms > 0.0:
                missing_ms = (ts - prev_chunk_ts) / 1e6 - nominal_ms
                if missing_ms >= 16.0:  # at least ~half a VAD frame went missing
                    gap_frames = min(
                        int(missing_ms // 32), self._effective_silence_frames
                    )
            prev_chunk_ts = ts
            pending_gap_frames = gap_frames

            samples = pcm_bytes_to_np(chunk.pcm)
            buf = np.concatenate([residual, samples])

            # Extract all complete 512-sample frames
            n_full = len(buf) // VAD_FRAME_SAMPLES
            frames = buf[: n_full * VAD_FRAME_SAMPLES].reshape(n_full, VAD_FRAME_SAMPLES)
            residual = buf[n_full * VAD_FRAME_SAMPLES:]

            for frame in frames:
                prob = self._prob(frame)
                rms = float(np.sqrt(np.mean(np.square(frame))))
                # Live mic loudness → overlay equalizer bars (zero-cost when no
                # overlay is subscribed). Taps the audio already flowing to STT,
                # so no second mic stream is opened (BUG: bars never reacted).
                if mic_level.has_subscribers():
                    mic_level.feed(rms)
                vad_speech = prob >= self._threshold and rms >= self._min_speech_rms
                relative_silence_rms = max(
                    self._min_speech_rms * 1.5,
                    peak_speech_rms * self._relative_silence_rms_ratio,
                )
                is_relative_silence = (
                    speaking
                    and speech_frames >= self._min_speech_frames
                    and peak_speech_rms > self._min_speech_rms
                    and rms <= relative_silence_rms
                )
                is_speech = vad_speech and not is_relative_silence

                if not speaking:
                    pre_buffer.append(frame)
                    if is_speech:
                        # Utterance begins — pull in the pre-buffer as context
                        active_frames = list(pre_buffer)
                        active_frames.append(frame)
                        speaking = True
                        silent_run = 0
                        tail_silent_run = 0
                        # Fresh utterance → snappy default; the probe re-grants
                        # patience if a delegation is still being composed.
                        self._extra_silence_frames = 0
                        total_frames = len(active_frames)
                        speech_frames = 1
                        peak_speech_rms = rms
                        self._notify(self._on_speech_start)
                        log.info(
                            "VAD speech start: prob=%.3f rms=%.4f threshold=%.2f",
                            prob,
                            rms,
                            self._threshold,
                        )
                    elif tail_pending:
                        tail_silent_run += 1
                        if tail_silent_run >= self._silence_frames:
                            tail_pending = False
                            tail_silent_run = 0
                            self._notify(self._on_endpoint, VAD_REASON_SILENCE)
                            log.info(
                                "VAD tail flush: %d ms of silence after a "
                                "forced cut — yielding empty tail so the "
                                "buffered utterance finalizes.",
                                self._silence_frames * 32,
                            )
                            yield b""
                else:
                    active_frames.append(frame)
                    total_frames += 1
                    if is_speech:
                        # Speech resumed inside this chunk — a capture gap here
                        # was (at least partly) real speech, so do NOT credit it
                        # as silence. Drop the pending credit.
                        pending_gap_frames = 0
                        speech_frames += 1
                        # Probe-independent patience: a long active-speech run is
                        # a long dictation — widen the natural silence window so a
                        # mid-sentence thinking pause is not cut. extend only grows
                        # and is reset at the next speech start, so short commands
                        # stay snappy.
                        if speech_frames >= self._long_utterance_speech_frames:
                            self.extend_silence_window(self._long_utterance_silence_ms)
                        peak_speech_rms = max(peak_speech_rms, rms)
                        if silent_run:
                            # A silence timer is running. Require *sustained*
                            # speech to cancel it — a single ambient / bleed
                            # spike must not wipe a near-complete silence
                            # accumulation (the "Jarvis thinks I am still talking"
                            # bug, 2026-05-25). Brief blips below the
                            # hysteresis hold ``silent_run`` (neither reset
                            # nor grow); only a real resume cancels.
                            resume_run += 1
                            if resume_run >= self._cancel_hysteresis_frames:
                                self._notify(self._on_silence_cancel)
                                log.info(
                                    "VAD silence timer cancel: paused_ms=%d prob=%.3f rms=%.4f",
                                    silent_run * 32,
                                    prob,
                                    rms,
                                )
                                silent_run = 0
                                resume_run = 0
                        else:
                            resume_run = 0
                    else:
                        resume_run = 0
                        if silent_run == 0:
                            self._notify(self._on_silence_start)
                            log.info(
                                "VAD silence timer start: threshold_ms=%d "
                                "prob=%.3f rms=%.4f peak_rms=%.4f",
                                self._silence_frames * 32,
                                prob,
                                rms,
                                peak_speech_rms,
                            )
                        silent_run += 1
                        # Real-time gap credit: this chunk arrived after a capture
                        # drop/lag and its frames are silence, so the missing time
                        # was almost certainly more end-of-speech silence. Credit
                        # it ONCE (on the first silent frame of the chunk) so the
                        # silence timer tracks real wall-clock instead of stalling
                        # on the frames a slow machine failed to deliver — the fix
                        # for "the turn never ends on a weaker laptop". Capped at
                        # construction; harmless (0) on a machine that keeps up.
                        if pending_gap_frames:
                            silent_run += pending_gap_frames
                            pending_gap_frames = 0

                    # Probe hook: hand only the *tail* of the active buffer
                    # (last `probe_tail_frames`) to the external stability
                    # observer. Sending the whole growing buffer would make
                    # Whisper transcribe more and more music with each call,
                    # producing fresh hallucinated lyrics every time and
                    # never stabilising. The tail anchors the question on
                    # "did anything new happen in the last few seconds".
                    if (
                        self._probe_callback is not None
                        and total_frames >= self._probe_min_active_frames
                        and total_frames - last_probe_frame >= self._probe_interval_frames
                    ):
                        tail = active_frames[-self._probe_tail_frames:]
                        tail_arr = np.concatenate(tail)
                        # Tell the probe whether the tail is loud (speaker bleed)
                        # or a quiet thinking pause, reusing the per-frame
                        # relative-silence calibration. Measure only the most
                        # RECENT ``tail_loud_window_frames`` of audio, NOT the
                        # whole ``probe_tail_ms`` tail: the tail is dominated by
                        # the speech preceding a pause, so a full-tail RMS stays
                        # "loud" right through the pause and the probe cuts the
                        # user off mid-thought ("no time to think"; recurred
                        # 2026-06-14 as stt_stable endpoints at silence_ms=32..864).
                        # A short recent window goes quiet within ~one window of
                        # the user stopping → defer to the natural ``silence_ms``
                        # endpoint; yet it still averages over the loud beats of
                        # dynamic speaker bleed (music/TV with quiet dips) → force
                        # the endpoint, since the silence endpoint never fires
                        # there. Loud tail + empty/stable transcript = bleed;
                        # quiet tail = pause.
                        recent = active_frames[-self._tail_loud_window_frames:]
                        recent_arr = np.concatenate(recent)
                        recent_rms = float(np.sqrt(np.mean(np.square(recent_arr))))
                        relative_silence_rms = max(
                            self._min_speech_rms * 1.5,
                            peak_speech_rms * self._relative_silence_rms_ratio,
                        )
                        tail_loud = recent_rms > relative_silence_rms
                        probe_pcm = _float32_to_int16_bytes(tail_arr)
                        self._notify(self._probe_callback, probe_pcm, tail_loud)
                        last_probe_frame = total_frames

                    # Endpoint: too much silence OR max length OR external
                    # request (e.g. STT probe declared the tail empty/stable).
                    #
                    # An external (STT-probe) request is HONOURED only once the
                    # natural silence floor for THIS utterance has been reached
                    # (``_effective_silence_frames`` — the 1.5 s base, or the wider
                    # window a delegation composition was granted). Below the floor
                    # the request is DISCARDED, not latched: it would behead a
                    # thinking pause, and the maintainer requires a guaranteed
                    # silence buffer on EVERY utterance (2026-06-16: "always give me
                    # ~1.5 s to pause and finish the thought" — delegation /
                    # Computer-Use prompts were being auto-submitted mid-sentence).
                    # Binding the force to the floor also stops it from bypassing
                    # ``extend_silence_window`` (the earlier delegation-patience fix
                    # only widened the natural endpoint; ``request_endpoint`` still
                    # short-circuited it). A real speaker-bleed turn still ends:
                    # moderate bleed lets ``silent_run`` climb via the
                    # relative-silence calibration so the floor is reached and the
                    # request lands there; very loud *continuous* bleed (where
                    # ``silent_run`` never climbs) falls back to the
                    # ``max_utterance`` cap. The probe re-requests every cycle while
                    # the tail still looks done, so discarding one early request
                    # loses nothing. DO NOT honour an external request below the
                    # floor — that IS the premature auto-submit this guard removes.
                    external_end = self._endpoint_requested
                    if external_end:
                        self._endpoint_requested = False
                        if silent_run < self._effective_silence_frames:
                            external_end = False
                    reached_max = total_frames * VAD_FRAME_SAMPLES >= self._max_samples
                    if (
                        silent_run >= self._effective_silence_frames
                        or reached_max
                        or external_end
                    ):
                        # Only yield if sufficient speech was collected
                        enough_speech = speech_frames >= self._min_speech_frames
                        if external_end:
                            reason = VAD_REASON_STT_STABLE
                        elif reached_max:
                            reason = VAD_REASON_MAX_UTTERANCE
                        else:
                            reason = VAD_REASON_SILENCE
                        if enough_speech:
                            utterance = np.concatenate(active_frames)
                            self._notify(self._on_endpoint, reason)
                            log.info(
                                "VAD endpoint: reason=%s duration_ms=%d speech_ms=%d silence_ms=%d",
                                reason,
                                total_frames * 32,
                                speech_frames * 32,
                                silent_run * 32,
                            )
                            # A forced cut arms the tail flush; any natural
                            # yield clears it (the carry was finalized). A
                            # false start leaves it untouched — the carry is
                            # still waiting.
                            tail_pending = reason == VAD_REASON_MAX_UTTERANCE
                            yield _float32_to_int16_bytes(utterance)
                        else:
                            self._notify(self._on_endpoint, VAD_REASON_FALSE_START)
                            log.info(
                                "VAD false start discarded: speech_ms=%d duration_ms=%d",
                                speech_frames * 32,
                                total_frames * 32,
                            )
                        # Reset
                        active_frames = []
                        pre_buffer.clear()
                        speaking = False
                        silent_run = 0
                        resume_run = 0
                        tail_silent_run = 0
                        total_frames = 0
                        speech_frames = 0
                        peak_speech_rms = 0.0
                        last_probe_frame = 0

    @staticmethod
    def _notify(callback: Callable[..., None] | None, *args: object) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # noqa: BLE001
            log.debug("VAD callback failed: %s", exc)


def _float32_to_int16_bytes(arr: np.ndarray) -> bytes:
    """Convert float32 [-1, 1] to int16 PCM bytes (Whisper input format)."""
    clipped = np.clip(arr, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()
