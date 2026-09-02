"""The dictation lane's on-device final-pass recognizer, hosted out of process.

Why this exists (2026-09-02 forensics, 220 dictations over four days): every
one of them ran on a cloud Whisper although the settings named the local
engine, because a user-scope environment override had moved the VOICE lane to
the cloud — deliberately, to keep the CUDA DLL load and a second large model
out of the desktop process (the 15-30 s loader-lock freezes of BUG-189 and
the VRAM oversubscription of BUG-204). The cloud round-trip was the lag the
user felt, and the cloud model's near-empty answers on 25 s windows were the
"truncated window" repairs that doubled it.

The dictation lane does not need the voice lane's provider at all. It needs a
strong local model that never touches this process's loader lock or VRAM
budget on its own — which is exactly the shape the live-preview worker already
has (:mod:`jarvis.dictation.preview_worker`). This module reuses that worker
with a stronger checkpoint and beam search, and presents it as an ordinary
``transcribe_pcm`` provider so the existing cross-family chain
(:class:`jarvis.speech.stt_fallback.FallbackSTT`) can put it IN FRONT of the
configured cloud provider:

* the worker builds off the press path (``warm_up`` after boot) and, failing
  that, on the first press;
* a card without enough free memory, a host whose worker only comes up on
  the CPU, a missing local runtime or a wedged worker all raise a CROSSABLE
  failure, so the chain moves to the cloud for that call and comes back
  later — no press is ever lost to the local engine being unavailable;
* a call that outlives its ceiling leaves a response in the pipe; the next
  call kills the worker rather than reading someone else's answer (AP-24:
  a wedged native engine is recovered by replacing it, never by waiting).

Nothing here is Windows-specific: the worker is our own interpreter running
our own module, the free-memory probe reports "unknown" where there is no
NVIDIA tooling (and unknown ALLOWS the spawn, AP-22 shape), and a worker that
comes up on the CPU is declined because a beam-search turbo decode of a 25 s
window on a CPU is slower than the cloud it would replace.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from jarvis.core.protocols import Transcript

log = logging.getLogger(__name__)

#: Checkpoint the final pass decodes with when the user has not chosen one.
#: ``large-v3-turbo`` is the accuracy of ``large-v3`` at a fraction of the
#: decode time and ~1 GB of VRAM at ``int8_float16``.
DEFAULT_FINAL_MODEL = "large-v3-turbo"

#: Compute type asked of the worker on CUDA. ``int8_float16`` halves the
#: weights against ``float16`` at no measurable accuracy cost, which matters
#: on a card that already holds the wake model, a local voice brain and the
#: local TTS server.
FINAL_COMPUTE = "int8_float16"

#: Beam width for the final pass. The preview decodes greedily (latency); the
#: final pass is read once after release and can afford the search.
FINAL_BEAM_SIZE = 5

#: Free accelerator memory the spawn asks for by default — the turbo weights
#: plus a CUDA context, with a little headroom.
DEFAULT_MIN_FREE_GB = 1.5

#: How long a refused or failed spawn is remembered before the next call may
#: try again. Free memory moves with every model load, so the door is not
#: closed for the process — but probing the card on every press would turn
#: a full card into a slow cloud.
_RETRY_AFTER_S = 60.0


class LocalEngineUnavailable(RuntimeError):
    """The on-device final pass cannot take this call; another provider may.

    Carries ``status = 503`` so :func:`jarvis.speech.stt_failure.classify_stt_failure`
    reads it as ``unavailable`` — the crossable class — without this module
    importing the classifier or the classifier learning a new marker. A
    dictation must never end because the LOCAL engine is missing; the cloud
    behind it in the chain takes the call.
    """

    status = 503


class LocalFinalSTT:
    """``transcribe_pcm`` provider whose decodes run in the dictation worker.

    Built once per pipeline and kept; the worker process it owns is spawned
    lazily (``warm_up`` off-path, else the first call) and replaced whenever
    it fails. Not safe for concurrent calls by design — the chain in front of
    it serialises callers because ``supports_concurrent_requests`` is False.
    """

    supports_concurrent_requests = False
    runs_on_device = True

    def __init__(
        self,
        model_name: str = DEFAULT_FINAL_MODEL,
        *,
        min_free_gb: float = DEFAULT_MIN_FREE_GB,
        compute: str = FINAL_COMPUTE,
        beam_size: int = FINAL_BEAM_SIZE,
    ) -> None:
        self._model_name = str(model_name or DEFAULT_FINAL_MODEL).strip()
        self._min_free_gb = max(0.0, float(min_free_gb))
        self._compute = compute
        self._beam_size = max(1, int(beam_size))
        self._worker: Any = None
        self._device = ""
        self._engine_compute = ""
        self._spawn_lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._next_attempt_at = 0.0
        self._last_refusal = ""

    # ------------------------------------------------------------------
    # Identity — read by the chain, the history row and the health panel
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "faster-whisper"

    @property
    def provider_label(self) -> str:
        return "faster-whisper"

    @property
    def last_used_model(self) -> str:
        return self._model_name

    @property
    def is_warm(self) -> bool:
        return self._worker is not None

    @property
    def device(self) -> str:
        """``"cuda"`` / ``"cpu"`` of the live worker, ``""`` without one."""
        return self._device

    @property
    def unavailable_reason(self) -> str:
        """Why the last spawn was declined, for the log and the status card."""
        return self._last_refusal

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def warm_up(self) -> None:
        """Spawn the worker off the press path. BLOCKING — call from a thread.

        Quiet when the host declines: the reason is logged once and the first
        press asks again after :data:`_RETRY_AFTER_S`. Raising here would only
        turn a full card into a warning nobody can act on.
        """
        self._ensure_worker()

    def recover(self) -> None:
        """Drop the worker so the next call starts a fresh one (AP-24)."""
        with self._spawn_lock:
            self._drop_worker_locked()
            self._next_attempt_at = 0.0

    def close(self) -> None:
        self.recover()

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16_000,
        language: str | None = None,
    ) -> Transcript:
        """int16 PCM bytes → transcript, decoded in the worker.

        A second caller while one is in flight means the previous call was
        abandoned at its ceiling and its answer is still in the pipe; that
        worker is replaced rather than read from, and THIS call crosses over.
        """
        if sample_rate != 16_000:
            raise LocalEngineUnavailable(
                f"the dictation worker takes 16 kHz audio, not {sample_rate} Hz"
            )
        if not self._call_lock.acquire(blocking=False):
            self.recover()
            raise LocalEngineUnavailable(
                "a previous call is still waiting on the dictation worker; "
                "it was replaced — this call goes to the next provider"
            )
        try:
            return await asyncio.to_thread(self._transcribe_blocking, pcm_bytes, language)
        finally:
            self._call_lock.release()

    def _transcribe_blocking(self, pcm_bytes: bytes, language: str | None) -> Transcript:
        worker = self._ensure_worker()
        if worker is None:
            raise LocalEngineUnavailable(self._last_refusal or "no local dictation worker")
        import numpy as np

        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        started = time.perf_counter()
        try:
            segments, info = worker.transcribe(
                samples, language=language or None, beam_size=self._beam_size
            )
        except Exception as exc:  # noqa: BLE001 — classified as crossable below
            with self._spawn_lock:
                self._drop_worker_locked()
                self._next_attempt_at = time.monotonic() + _RETRY_AFTER_S
                self._last_refusal = f"{type(exc).__name__}: {exc}"
            log.warning(
                "Local dictation worker failed (%s) — replaced; this call crosses "
                "to the next provider.",
                self._last_refusal,
            )
            raise LocalEngineUnavailable(self._last_refusal) from exc
        # Whisper segment texts carry their own leading space, so a bare join
        # reproduces the decoder's spacing (a " " join would double it).
        text = "".join(str(getattr(seg, "text", "") or "") for seg in segments).strip()
        seg_dicts = tuple(
            {
                "start": getattr(seg, "start", None),
                "end": getattr(seg, "end", None),
                "text": str(getattr(seg, "text", "") or ""),
            }
            for seg in segments
        )
        detected = str(getattr(info, "language", "") or "") or (language or "")
        try:
            confidence = float(getattr(info, "language_probability", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        log.debug(
            "Local dictation final pass: %.1fs of audio in %.2fs on %s (%s).",
            len(pcm_bytes) / 32_000.0,
            time.perf_counter() - started,
            self._device,
            self._engine_compute,
        )
        from jarvis.plugins.stt.transcript_filter import clean_stt_text

        return Transcript(
            text=clean_stt_text(text, language=detected),
            language=detected,
            confidence=confidence,
            is_partial=False,
            segments=seg_dicts,
            raw_text=text,
        )

    # ------------------------------------------------------------------
    # Worker ownership
    # ------------------------------------------------------------------

    def _ensure_worker(self) -> Any:
        """The live worker, spawning one when allowed; ``None`` when declined."""
        with self._spawn_lock:
            if self._worker is not None:
                return self._worker
            now = time.monotonic()
            if now < self._next_attempt_at:
                return None
            self._next_attempt_at = now + _RETRY_AFTER_S
            refusal = self._spawn_refusal()
            if refusal:
                self._last_refusal = refusal
                log.info("Local dictation engine not started: %s", refusal)
                return None
            from jarvis.dictation.local_preview import _spawn_worker_model

            worker = _spawn_worker_model(self._model_name, compute=self._compute)
            if worker is None:
                self._last_refusal = "the dictation worker did not come up"
                return None
            device = str(getattr(worker, "device", "") or "")
            if device == "cpu":
                # A beam-search turbo decode of a 25 s window on a CPU takes
                # longer than the cloud round-trip it would replace; the
                # preview keeps its own CPU floor, the final pass does not.
                worker.close()
                self._last_refusal = (
                    "the local engine only runs on the CPU here, which is slower "
                    "than the configured provider for a final pass"
                )
                log.info("Local dictation engine declined: %s", self._last_refusal)
                return None
            self._worker = worker
            self._device = device
            self._engine_compute = str(getattr(worker, "compute", "") or "")
            self._last_refusal = ""
            log.info(
                "Local dictation engine ready: %s on %s (%s), out of process.",
                self._model_name,
                self._device,
                self._engine_compute,
            )
            return worker

    def _spawn_refusal(self) -> str:
        """Why the worker must not be started right now, or ``""``."""
        try:
            from jarvis.dictation.local_preview import faster_whisper_available

            if not faster_whisper_available():
                return "the local speech engine (faster-whisper) is not installed here"
        except Exception as exc:  # noqa: BLE001 — a broken probe reads as "absent"
            return f"the local engine probe failed ({type(exc).__name__}: {exc})"
        if self._min_free_gb <= 0.0:
            return ""
        try:
            from jarvis.hardware.detection import free_accelerator_gb

            free_gb, source = free_accelerator_gb()
        except Exception:  # noqa: BLE001 — an unreadable card must not veto the spawn
            log.debug("local dictation: free-memory probe failed", exc_info=True)
            return ""
        if source == "none" or free_gb <= 0.0:
            # Unknown is not "full": Apple unified memory and ROCm have no cheap
            # reading here, and the worker's own device ladder still decides.
            return ""
        if free_gb >= self._min_free_gb:
            return ""
        return (
            f"only {free_gb:.1f} GB of accelerator memory is free ({source}) and "
            f"{self._model_name} needs ~{self._min_free_gb:.1f} GB; the configured "
            "provider takes this dictation"
        )

    def _drop_worker_locked(self) -> None:
        worker, self._worker = self._worker, None
        self._device = ""
        self._engine_compute = ""
        if worker is None:
            return
        try:
            worker.close()
        except Exception as exc:  # noqa: BLE001 — a dead worker is the goal state
            log.debug("local dictation: worker close skipped (%s)", exc)


__all__ = [
    "DEFAULT_FINAL_MODEL",
    "DEFAULT_MIN_FREE_GB",
    "FINAL_BEAM_SIZE",
    "FINAL_COMPUTE",
    "LocalEngineUnavailable",
    "LocalFinalSTT",
]
