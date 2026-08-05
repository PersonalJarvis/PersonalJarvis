"""Local engine for the dictation LIVE PREVIEW — the words while you speak.

The preview and the transcript want opposite things. The transcript wants the
best model available and can afford a round-trip; the preview wants to be on
screen NOW and is thrown away on the next tick. Sending both to the same cloud
provider meant the throwaway half was spending the quota the keeping half
needed — measured at ~40 requests per minute of speech, ~85 % of it preview,
against a 20 RPM ceiling that Groq applies to its **paid plan too**. That is
what made a 137 s dictation come back with 367 characters.

Measured on the maintainer's box (RTX 5070 Ti, faster-whisper ``base``,
int8_float16):

    1 s tail ->  34 ms      4 s tail ->  63 ms      8 s tail -> 137 ms

against 400-1500 ms plus one rationed request for a cloud round-trip. So the
preview is not merely cheaper locally, it is an order of magnitude faster —
and it costs no quota at all, which is what lets the transcript have the whole
budget. That is the difference between "runs out after 40 seconds" and "as long
as you care to talk".

Why ``base`` and not the model the transcript uses: a preview is read at a
glance and replaced a second later, so a rare wrong ending costs nothing, while
a model that takes 800 ms turns the preview into a lagging distraction. The
FINAL text is never produced here — it always comes from the configured
provider, so the words that land in the user's document are the good ones.

Degrades honestly (CLAUDE.md §3): on a host without ``faster_whisper`` — a base
or headless install — this reports unavailable and the caller falls back to the
budgeted cloud preview. No GPU is required either; the engine picks CPU and the
caller simply sees a slower preview.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

#: Model for the preview. Small enough to answer in tens of milliseconds, good
#: enough that the line on screen reads as what was said. Never used for the
#: text that is actually delivered.
PREVIEW_MODEL = "base"

#: How long one preview transcription may take before we stop waiting for it.
#: The preview is worthless once it is stale, and a wedged native engine must
#: never hold the dictation loop (AP-24: a timeout BOUNDS the wait, it does not
#: recover the engine — that is what ``_failures``/``reset`` below are for).
PREVIEW_TIMEOUT_S = 2.0

#: Consecutive failures after which the engine is dropped and rebuilt on next
#: use. A native engine that has wedged never recovers by being asked again.
_MAX_FAILURES = 3


def faster_whisper_available() -> bool:
    """Is a local engine importable here? Cheap spec probe, no heavy import."""
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


class LocalPreviewTranscriber:
    """Lazily-built local engine for preview text. Never raises to the caller.

    One instance owns one engine and serialises access to it with a
    NON-BLOCKING lock: a second caller arriving while a transcription is in
    flight is turned away rather than queued (AP-24 — ctranslate2 is not
    thread-safe, and a queued caller would wedge it permanently). Turning a
    preview away is free; the next tick asks again.
    """

    def __init__(self, model_name: str = PREVIEW_MODEL) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._failures = 0
        self._unavailable = False
        self._loading = False
        self._load_failed = False
        #: Language of the MOST RECENT preview, as ``(code, probability)``.
        #: The engine computes this on every call and it used to be discarded.
        #: It is the only reading of the spoken language taken from the AUDIO
        #: rather than from a transcript, which is what makes it worth keeping:
        #: a cloud provider handed a few seconds of speech may silently
        #: TRANSLATE it, and a translated sentence looks like the wrong
        #: language to any text-based detector (BUG: German dictation
        #: delivered in English, 2026-07-29). Empty until a preview has run.
        self.last_language = ""
        self.last_language_probability = 0.0

    @property
    def available(self) -> bool:
        """False once this host has proven it cannot serve a local preview."""
        return not self._unavailable

    @property
    def ready(self) -> bool:
        """Whether the engine can answer right now (model built)."""
        return self._model is not None

    def _load_model(self) -> None:
        """Build the engine. Runs OFF the transcribe path — see ``transcribe``."""
        try:
            from jarvis.plugins.stt.fwhisper import _new_whisper_model

            preferred = self._pick_device()
            attempts = [preferred]
            if preferred[0] != "cpu":
                attempts.append(("cpu", "int8"))
            last_error: Exception | None = None
            for device, compute in attempts:
                try:
                    model = _new_whisper_model(self._model_name, device, compute)
                except Exception as exc:  # noqa: BLE001 — try the portable floor
                    last_error = exc
                    log.info(
                        "Dictation preview engine could not use %s (%s: %s).",
                        device,
                        type(exc).__name__,
                        exc,
                    )
                    continue
                try:
                    # Constructing the model does not pay CUDA's first-decode
                    # setup cost. Prime one throwaway greedy decode here, while
                    # the preview is still advertised as unavailable, so the
                    # first visible preview keeps the steady-state latency.
                    import numpy as np

                    rng = np.random.default_rng(0)
                    warm_audio = (
                        rng.standard_normal(16_000).astype(np.float32) * 0.001
                    )
                    segments, _info = model.transcribe(
                        warm_audio,
                        beam_size=1,
                        temperature=0.0,
                        condition_on_previous_text=False,
                    )
                    list(segments)
                except Exception as exc:  # noqa: BLE001 — try the portable floor
                    last_error = exc
                    log.info(
                        "Dictation preview rejected %s after its first "
                        "decode failed (%s: %s).",
                        device,
                        type(exc).__name__,
                        exc,
                    )
                    continue
                with self._lock:
                    self._model = model
                log.info(
                    "Dictation preview engine ready: %s on %s (%s).",
                    self._model_name,
                    device,
                    compute,
                )
                break
            else:
                self._load_failed = True
                self._unavailable = True
                log.info(
                    "Local dictation preview unavailable (%s: %s) — the transcript "
                    "is unaffected; the live line falls back to the provider.",
                    type(last_error).__name__ if last_error is not None else "Error",
                    last_error or "no usable local inference device",
                )
        finally:
            self._loading = False

    @staticmethod
    def _pick_device() -> tuple[str, str]:
        """``(device, compute_type)`` — CUDA when it is genuinely usable.

        Ask the inference runtime that will actually execute the model. Importing
        torch here was both indirect and racy: the faster-whisper import shield
        can temporarily hide torch from another loader thread, which made a
        CUDA-capable desktop silently choose the CPU for the rest of the process.
        Anything uncertain picks CPU, and ``_load_model`` still proves the choice
        with a real model build before accepting it.
        """
        try:
            from jarvis.plugins.stt.fwhisper import (
                ensure_cuda_libraries_findable,
                inference_only_import_shield,
            )

            ensure_cuda_libraries_findable()
            with inference_only_import_shield():
                import ctranslate2

            supported = ctranslate2.get_supported_compute_types("cuda")
            if "int8_float16" in supported:
                return "cuda", "int8_float16"
        except Exception as exc:  # noqa: BLE001 — a probe must never decide by raising
            log.debug("Preview CUDA probe failed (%s); using CPU.", exc)
        return "cpu", "int8"

    def _transcribe_sync(self, pcm: bytes, language: str | None) -> tuple[str, str, float]:
        """``(text, language_code, language_probability)``.

        The language is reported back rather than dropped: it costs nothing
        (the decoder already produced it) and it is an AUDIO-derived reading,
        which no downstream text inspection can reconstruct once a provider
        has translated the words.
        """
        import numpy as np

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return "", "", 0.0
        model = self._model
        if model is None:  # pragma: no cover — transcribe() gates on ready
            return "", "", 0.0
        segments, info = model.transcribe(
            samples,
            language=language,
            beam_size=1,  # greedy: the preview trades a little accuracy for latency
            # A tuple/default enables Whisper's temperature fallback ladder and
            # may decode the same stale preview repeatedly. One fixed greedy
            # pass keeps the measured path in the tens-of-milliseconds range.
            temperature=0.0,
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text for seg in segments).strip()
        # A language the CALLER pinned is not a detection — reporting it back as
        # one would let a pin confirm itself forever.
        detected = "" if language else str(getattr(info, "language", "") or "")
        try:
            probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        except (TypeError, ValueError):
            # Deliberately quiet: zero is the honest reading of "this engine
            # build reports no confidence", and it makes the caller's gate
            # reject the detection — the same outcome a log line would only
            # narrate, once per preview tick.
            probability = 0.0
        return text, detected, probability

    async def transcribe(self, pcm: bytes, language: str | None = None) -> str | None:
        """Preview text, or ``None`` when this tick has none.

        ``None`` is not an error — it is "no preview right now" (engine busy,
        too slow, or unavailable). The caller shows the previous line and asks
        again on the next tick.
        """
        if self._unavailable or not pcm:
            return None
        if self._model is None:
            # Building the engine takes seconds — far longer than the timeout a
            # PREVIEW may hold. Loading it inside the timed call meant the first
            # ticks of the first dictation all "timed out", and each one counted
            # as an engine failure, so the local preview reliably disabled
            # itself before it had ever worked once. Load OFF this path and
            # answer "nothing yet" meanwhile: a missing preview line for the
            # first couple of seconds is not a failure of anything.
            self._start_loading()
            return None
        guard = self._busy
        if not guard.acquire(blocking=False):
            # A previous preview is still running. Skipping is correct: a queued
            # call on a native engine is how it wedges (AP-24). This can only
            # persist after a timed-out/cancelled waiter, so count it toward
            # recovery; otherwise one truly wedged worker owns the guard forever.
            self._note_failure("still busy after its waiter ended")
            return None
        work: asyncio.Task[tuple[str, str, float]] | None = None
        release_here = True
        try:
            work = asyncio.create_task(
                asyncio.to_thread(self._transcribe_sync, pcm, language),
                name="dictation-local-preview",
            )
            text, detected, probability = await asyncio.wait_for(
                asyncio.shield(work),
                timeout=PREVIEW_TIMEOUT_S,
            )
            if detected:
                self.last_language = detected
                self.last_language_probability = probability
            self._failures = 0
            return text
        except TimeoutError:
            # Not silent: _note_failure logs the attempt and drops the engine once
            # the failures persist. Returning None is the whole handling — a
            # preview that missed its slot has nothing left to say, and raising
            # here would break the dictation the preview only decorates.
            self._note_failure("timed out")
            return None
        except Exception as exc:  # noqa: BLE001 — a preview must never break dictation
            self._note_failure(f"{type(exc).__name__}: {exc}")
            return None
        finally:
            if work is not None and not work.done():
                # Cancelling or timing out the asyncio waiter does not stop the
                # native thread. Keep the non-blocking guard held until that
                # thread really exits, or the next preview would enter the same
                # ctranslate2 session concurrently (AP-24).
                release_here = False

                def _release_finished_worker(done: asyncio.Task) -> None:
                    guard.release()
                    try:
                        done.result()
                    except asyncio.CancelledError:  # Cancellation is the contained worker teardown.
                        pass
                    except Exception as exc:  # noqa: BLE001 — detached worker is contained
                        log.debug("Detached dictation preview failed: %s", exc)

                work.add_done_callback(_release_finished_worker)
            if release_here:
                guard.release()

    def _start_loading(self) -> None:
        """Kick off a one-shot background load. Cheap and idempotent."""
        with self._lock:
            if self._loading or self._model is not None or self._load_failed:
                return
            self._loading = True
        threading.Thread(
            target=self._load_model, name="dictation-preview-load", daemon=True
        ).start()

    def _note_failure(self, why: str) -> None:
        """Count a failure and drop the engine once they persist.

        Rebuilding a FRESH engine is the only way back from a wedged native
        session — re-asking the same one never recovers it (AP-24). Dropping the
        model is therefore the whole repair here; the NEXT tick sees no model
        and starts a background rebuild.

        Turning the local path off for good is deliberately NOT decided here. A
        transcription failure says "this engine is unwell", not "this host
        cannot run one" — only a failed BUILD proves that, and ``_load_model``
        is where it is recorded. Deciding it in both places is how a machine
        that had one bad segment loses its fast preview permanently.
        """
        self._failures += 1
        log.debug("Dictation preview failed (%s), attempt %d.", why, self._failures)
        if self._failures < _MAX_FAILURES:
            return
        with self._lock:
            # The old native worker keeps its captured model and guard. Rotate
            # both references atomically so the next tick builds a genuinely
            # fresh session rather than re-polling a wedged engine (AP-24).
            self._model = None
            self._busy = threading.Lock()
        self._failures = 0
        log.info("Dictation preview engine dropped (%s); rebuilding on the next tick.", why)


_INSTANCE: LocalPreviewTranscriber | None = None
_INSTANCE_LOCK = threading.Lock()


def local_preview() -> LocalPreviewTranscriber | None:
    """The shared preview engine, or ``None`` where none can run.

    Shared for the same reason the preview budget is: consecutive dictations
    should not each pay the model load. Returns ``None`` (rather than an inert
    object) so the caller's fallback is an explicit branch, not a silent no-op.
    """
    global _INSTANCE
    if not faster_whisper_available():
        return None
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = LocalPreviewTranscriber()
        return _INSTANCE if _INSTANCE.available else None


def reset_local_preview_for_tests() -> None:
    """Drop the shared engine — test-isolation hook."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None


__all__ = [
    "PREVIEW_MODEL",
    "PREVIEW_TIMEOUT_S",
    "LocalPreviewTranscriber",
    "faster_whisper_available",
    "local_preview",
    "reset_local_preview_for_tests",
]
