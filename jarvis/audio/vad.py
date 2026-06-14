"""Voice Activity Detection via Silero-VAD.

Silero-VAD is a ~2 MB PyTorch model that operates on 16 kHz audio frames of
exactly 512 samples (32 ms) and delivers a speech probability (0.0-1.0) per
frame. Significantly more precise than WebRTC-VAD in noisy/music environments.

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
        silence_ms: int = 1000,
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
    ) -> None:
        self._threshold = speech_threshold
        self._silence_frames = max(1, silence_ms // 32)
        self._min_speech_frames = max(1, min_speech_ms // 32)
        self._max_samples = max_utterance_s * VAD_SAMPLE_RATE
        # Number of *consecutive* speech frames required to cancel an
        # in-progress silence timer. A single ambient/speaker-bleed spike
        # (music beat, fan gust, TV transient) must not reset a long
        # accumulated silence, otherwise the silence endpoint never fires in
        # a noisy room and the turn drags to the max_utterance hard cap.
        # See test_brief_speaker_bleed_spikes_do_not_reset_silence_timer.
        self._cancel_hysteresis_frames = max(1, cancel_hysteresis_ms // 32)
        self._model = None  # lazy — PyTorch import deferred until needed

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

    def request_endpoint(self) -> None:
        """External observer requests the current utterance ends now.

        Used by the STT stability probe to break out of speaker-bleed
        situations where Silero would keep streaming forever. Yields the
        accumulated audio with reason ``stt_stable`` if enough real speech
        was collected, otherwise discards as ``false_start``.
        """
        self._endpoint_requested = True

    def _ensure_model(self) -> None:
        if self._model is None:
            from silero_vad import load_silero_vad
            self._model = load_silero_vad()

    def _prob(self, frame: np.ndarray) -> float:
        """Per-frame speech probability (must be exactly 512 float32 samples)."""
        import torch
        self._ensure_model()
        assert self._model is not None
        t = torch.from_numpy(frame).unsqueeze(0)
        return float(self._model(t, VAD_SAMPLE_RATE).item())

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

        async for chunk in chunks:
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
                        speech_frames += 1
                        peak_speech_rms = max(peak_speech_rms, rms)
                        if silent_run:
                            # A silence timer is running. Require *sustained*
                            # speech to cancel it — a single ambient / bleed
                            # spike must not wipe a near-complete silence
                            # accumulation (the "Jarvis denkt, ich rede noch"
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
                    external_end = self._endpoint_requested
                    if external_end:
                        self._endpoint_requested = False
                    reached_max = total_frames * VAD_FRAME_SAMPLES >= self._max_samples
                    if (
                        silent_run >= self._silence_frames
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
