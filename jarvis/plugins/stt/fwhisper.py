"""faster-whisper STT plugin.

Structurally implements `STTProvider` — no inheritance, just duck-typing.
The model (distil-large-v3, multilingual DE+EN) is lazily loaded into
GPU memory on the first `start()` call (~1.5 GB VRAM at int8_float16).

On an RTX 5070 Ti, distil-large-v3 delivers ~250 ms latency for a
5-second utterance — good enough for Phase 1.
"""
from __future__ import annotations

import contextlib
import logging
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any

import numpy as np

from jarvis.audio.capture import pcm_bytes_to_np
from jarvis.core.protocols import AudioChunk, Transcript

log = logging.getLogger(__name__)


class TranscribeBusy(Exception):
    """Raised when a transcription is skipped because another is already in
    flight on the SAME (shared) model. ctranslate2 is not thread-safe for
    concurrent transcribe, so the wake poll loop and the VAD probe must not run
    the model at once. The caller treats this as a transient miss and re-polls;
    a run of these means the in-flight call is wedged (see ``recover``)."""


@contextlib.contextmanager
def inference_only_import_shield() -> Iterator[None]:
    """Block ``transformers`` + ``torch`` ONLY while ctranslate2 imports.

    ``import ctranslate2`` (pulled in transitively by ``faster_whisper``) eagerly
    runs ``from ctranslate2 import converters`` at the bottom of its ``__init__``,
    and ``ctranslate2.converters.transformers`` imports the full **transformers**
    (~1.5 s) → **torch** (~1.3 s) stack. Those are model-*conversion* code paths
    (HuggingFace → CTranslate2 format) that the inference engine (wake match +
    utterance STT) NEVER touches. Stubbing both modules as un-importable for the
    duration of the import makes the converter shim's guarded import skip them,
    cutting the faster_whisper/ctranslate2 import from **~2.9 s warm / ~14 s cold
    to ~0.17 s** (measured 2026-06-28) — the single biggest cost on the wake
    "ready to talk" path. Cross-platform (pure ``sys.modules`` manipulation).

    Safety: only stubs a module that is **not already really imported** (so it can
    never clobber a torch loaded first by Silero-VAD), and on exit removes ONLY
    the stub it set — leaving any real module another thread imported meanwhile.
    Inference was verified to still work and a later real ``import torch`` to
    succeed after the shield (the VAD path). This relies on the wake-model load
    being serialized before the VAD/TTS loads (pipeline ``_warmup_deferred_loaders``
    pre-warms the wake model first and alone) so there is no concurrent real
    torch import racing the stub.
    """
    saved: list[str] = []
    for name in ("transformers", "torch"):
        if name not in sys.modules:
            sys.modules[name] = None  # type: ignore[assignment]
            saved.append(name)
    try:
        yield
    finally:
        for name in saved:
            # Remove ONLY our stub; if real code imported it meanwhile, keep it.
            if sys.modules.get(name) is None:
                sys.modules.pop(name, None)


def _normalize_model_name(model: str) -> str:
    """Map known-invalid OpenAI-style aliases to faster-whisper model ids.

    faster-whisper expects a bare size id ("large-v3", "large-v3-turbo",
    "distil-large-v3", "base", "small", …) or a HuggingFace repo id
    ("org/name"). A drifted config value like "whisper-large-v3" (the OpenAI
    naming) is not a valid id and raises at load. Strip the bogus "whisper-"
    prefix off bare ids; leave HF repo ids (containing "/") untouched.
    """
    if "/" in model:
        return model
    if model.startswith("whisper-"):
        return model[len("whisper-"):]
    return model


# Known faster-whisper checkpoints that can ONLY transcribe English. The whole
# Distil-Whisper family is English-only by design, and any plain Whisper size
# carrying the ``.en`` suffix is the English-only variant. Fed German/Spanish
# audio they do not error — they phonetically mangle it into English words
# ("Kannst du mir bitte" -> "Can't you me please"), which is far worse than an
# honest failure because the garbage flows straight to the brain.
_ENGLISH_ONLY_MODELS = frozenset(
    {
        "distil-large-v3",
        "distil-large-v3.5",
        "distil-large-v2",
        "distil-medium.en",
        "distil-small.en",
    }
)


def _multilingual_equivalent(model: str) -> str | None:
    """Return a multilingual replacement when ``model`` is English-only, else None.

    Used by the constructor guard so a bilingual / auto-detect / German / Spanish
    user is never silently stuck on an English-only model. HuggingFace repo ids
    (containing ``/``) are opaque — we cannot reason about their language coverage,
    so they are left untouched.

    Mapping:
      * a plain ``<size>.en`` model -> the same size without the suffix
        (``base.en`` -> ``base``), which is the multilingual variant at the same
        speed/accuracy tier;
      * any Distil-Whisper model (all English-only, no multilingual distil exists)
        -> ``large-v3-turbo``, the fast multilingual checkpoint.
    """
    if "/" in model:
        return None
    if model.endswith(".en") and not model.startswith("distil-"):
        return model[: -len(".en")]
    if model in _ENGLISH_ONLY_MODELS or model.startswith("distil-"):
        return "large-v3-turbo"
    return None


def _cpu_safe_compute_type(compute_type: str) -> str:
    """Downgrade CUDA-only compute types to a CPU-compatible one.

    ``float16`` / ``int8_float16`` require a GPU; on a CPU / headless VPS they
    raise. ``int8`` is the universal CPU-safe equivalent (cloud-first floor).
    """
    if compute_type in ("float16", "int8_float16"):
        return "int8"
    return compute_type


def _new_whisper_model(
    model_name: str, device: str, compute_type: str, cpu_threads: int = 0
) -> Any:
    """Construct a ``WhisperModel`` (overridable seam for tests).

    The heavy ``faster_whisper`` import stays lazy here so importing this module
    on a host without it is cheap; tests monkeypatch this function to avoid the
    import + a real model build. The import shield skips ctranslate2's
    transformers+torch converter stack (inference doesn't need it) — see
    :func:`inference_only_import_shield`.

    ``cpu_threads`` bounds ctranslate2's intra-op thread pool. 0 = auto (all
    cores). A FIXED small value is the standard mitigation for the intermittent
    ``model.transcribe`` HANG that appears when ctranslate2 and PyTorch share a
    process (both bring their own OpenMP runtime; ctranslate2 auto-grabbing every
    core deadlocks against torch's pool — live-log evidence 2026-06-30: the wake
    transcribe hung for 8 s at a time on BOTH cpu and cuda while torch was
    loaded). ``num_workers=1`` keeps a single inference stream so there is no
    internal cross-thread contention. Only the wake model sets this (it coexists
    with torch on the always-on loop); the utterance STT keeps auto threads.
    """
    with inference_only_import_shield():
        from faster_whisper import WhisperModel

    kwargs: dict[str, Any] = {"device": device, "compute_type": compute_type}
    if cpu_threads and cpu_threads > 0:
        kwargs["cpu_threads"] = int(cpu_threads)
        kwargs["num_workers"] = 1
    return WhisperModel(model_name, **kwargs)


class FasterWhisperProvider:
    """Local Whisper STT via faster-whisper (CTranslate2 backend)."""

    name = "faster-whisper"
    supports_streaming = False  # we can add stream_transcribe later

    def __init__(
        self,
        model: str = "distil-large-v3",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        language: str | None = None,  # None = auto-detect (bilingual DE+EN)
        beam_size: int = 5,
        vad_filter: bool = False,  # we have an external Silero VAD in front of this
        # No initial prompt in the hot path: fixed example sentences were
        # hallucinated as the transcript on quiet audio and sent to the brain.
        initial_prompt: str | None = None,
        no_speech_threshold: float = 0.6,
        # 0 = auto (all cores). A fixed small value avoids the ctranslate2<->torch
        # OpenMP deadlock that hung the always-on wake transcribe (see
        # ``_new_whisper_model``); set only on the wake model.
        cpu_threads: int = 0,
    ) -> None:
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._language = language if language and language != "auto" else None
        # Defense-in-depth (forensic 2026-06-28): an English-only model fed
        # non-English audio mangles it into English words. Unless the user has
        # DELIBERATELY pinned English (``self._language == "en"``), swap an
        # English-only checkpoint for a multilingual one so auto-detect / a de/es
        # pin actually transcribes the spoken language. A "en" pin keeps the fast
        # English-only model. The swap covers every construction path (config
        # factory, bare default, wake builder, CLI) in one place.
        if self._language != "en":
            multilingual = _multilingual_equivalent(self._model_name)
            if multilingual is not None and multilingual != self._model_name:
                log.warning(
                    "STT model %r is English-only but the recognition language is "
                    "%r — using multilingual %r instead so non-English speech is "
                    "not mangled into English.",
                    self._model_name,
                    self._language or "auto",
                    multilingual,
                )
                self._model_name = multilingual
        self._beam_size = beam_size
        self._vad_filter = vad_filter
        self._initial_prompt = initial_prompt
        self._no_speech_threshold = no_speech_threshold
        self._cpu_threads = int(cpu_threads)
        self._model: Any = None  # lazy
        # Serialize the actual ctranslate2 inference. ``transcribe_pcm`` runs
        # ``model.transcribe`` in a worker thread (asyncio.to_thread), and the
        # SAME provider instance is shared by the rolling-whisper wake poll loop
        # AND the VAD "listening bubble" probe (``_probe_stt = _stt`` for a custom
        # wake phrase). ctranslate2's ``WhisperModel`` is NOT thread-safe for
        # concurrent ``transcribe`` on one model object — two overlapping calls
        # hang (the 12-minute custom-wake "Hey Nico" silent wedge, forensic
        # 2026-06-29) or return garbage. This lock makes the model call mutually
        # exclusive per instance so the two callers serialize instead of racing.
        self._infer_lock = threading.Lock()
        # True once ``warm_up`` completed (model constructed + one priming
        # inference). Boot-time consumers (the rolling-whisper wake poll loop,
        # the heavy-backend gate) wait on this instead of poking the model
        # while it is still loading: a transcribe DURING the load used to time
        # out at 8 s, the next poll hit TranscribeBusy, two fails triggered
        # recover() which threw the half-loaded model away and reloaded from
        # scratch — a load cascade that turned a ~4 s model load into 114.7 s
        # on the boot path (TTU forensic 2026-07-02).
        self._warm = False

    @property
    def is_warm(self) -> bool:
        """True when the model is constructed AND primed (safe to poll)."""
        return self._warm

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        model_name = _normalize_model_name(self._model_name)
        device, compute_type = self._device, self._compute_type
        try:
            self._model = _new_whisper_model(
                model_name, device, compute_type, self._cpu_threads
            )
            return
        except Exception as exc:  # noqa: BLE001 — fall back rather than crash boot
            fb_device, fb_ct = "cpu", _cpu_safe_compute_type(compute_type)
            if (device, compute_type) == (fb_device, fb_ct):
                # Already on the CPU-safe combo — the failure is not a
                # device/compute mismatch (e.g. a genuinely bad model id).
                # Re-raise instead of pointlessly retrying the same load.
                raise
            log.warning(
                "WhisperModel(%s, device=%s, compute_type=%s) failed (%s); "
                "retrying on cpu/%s.",
                model_name, device, compute_type, exc, fb_ct,
            )
        self._model = _new_whisper_model(
            model_name, fb_device, fb_ct, self._cpu_threads
        )

    def recover(self) -> None:
        """Self-heal a WEDGED inference engine without an app restart.

        ctranslate2's ``transcribe`` can hang unrecoverably (the concurrent-call
        corruption this provider's lock now prevents, but a model already wedged
        before the fix — or any future driver/resource hiccup — stays hung). The
        worker thread running it cannot be cancelled, so the ONLY way back is a
        fresh model object. Drop the (possibly hung) model AND its lock: the next
        ``transcribe_pcm`` rebuilds a clean model under a clean lock, while any
        thread still stuck in the old ``model.transcribe`` keeps the OLD object +
        OLD lock alive (orphaned — it never blocks the fresh path, and is freed
        if/when it ever returns). Called by the wake loop after a run of
        consecutive transcribe time-outs (the wedge signature). Cheap + instant:
        it only clears references; the ~load happens lazily on the next call."""
        log.warning(
            "FasterWhisperProvider.recover(): dropping a wedged model + lock so "
            "the next transcription rebuilds a fresh engine (self-heal)."
        )
        self._model = None
        self._infer_lock = threading.Lock()
        self._warm = False

    def warm_up(self) -> None:
        """Prime the engine with one throwaway inference so the FIRST live
        transcription isn't cold.

        ``_ensure_model`` only constructs the ``WhisperModel`` (weights into
        VRAM). On a faster-whisper / CTranslate2 CUDA backend the FIRST actual
        ``model.transcribe`` pays a one-off cost — CUDA kernel selection/JIT,
        cuDNN algo search, memory-pool setup — of several seconds; steady state
        is ~100 ms. For the rolling-window custom-phrase wake (this provider IS
        the wake model) that cold inference lands on the user's first
        "Hey Jarvis": the wake loop blocks on it long enough that the wake audio
        rolls out of the rolling buffer before the transcript returns, so the
        first wake is missed and the user has to repeat it (forensic 2026-06-28).
        Running one inference here, before the model goes live, moves that cost
        off the wake path.

        Synchronous (call it via ``asyncio.to_thread`` off the event loop, like
        ``_ensure_model``). Idempotent — a second call reuses the loaded model.
        Best-effort: a warm-up failure is swallowed (the model still lazy-works
        on the first real transcribe), so priming never breaks boot.
        """
        self._ensure_model()
        try:
            # ~1 s of very low-amplitude noise: enough signal to exercise the
            # full encoder + decoder/beam kernels (pure zeros can early-exit on
            # no-speech and leave the decode path cold). Harmless content — a
            # random mis-hear can never match the strict wake matcher, and this
            # transcript is discarded.
            rng = np.random.default_rng(0)
            warm_audio = rng.standard_normal(16_000).astype(np.float32) * 0.001
            self._transcribe_sync(warm_audio, 16_000)
        except Exception as exc:  # noqa: BLE001 — priming is best-effort
            log.debug("Whisper warm-up inference skipped: %s", exc)
        # Signal readiness even when the priming inference failed: the model
        # object exists, so pollers can safely transcribe (lazy paths cover the
        # rest). Leaving _warm False here would park the wake poll loop forever.
        self._warm = True

    async def transcribe(self, audio: AsyncIterator[AudioChunk]) -> Transcript:
        """Collects all chunks, transcribes them in one go.

        This is enough for Phase 1 — the VAD layer in front already delivers
        clean utterances, so "in one go" is the natural granularity.
        """
        self._ensure_model()

        # Concatenate all chunks into one float32 array
        pieces: list[np.ndarray] = []
        sample_rate = 16_000
        async for chunk in audio:
            pieces.append(pcm_bytes_to_np(chunk.pcm))
            sample_rate = chunk.sample_rate
        if not pieces:
            return Transcript(text="", language="unknown", confidence=0.0)
        audio_np = np.concatenate(pieces)

        return await self._transcribe_np(audio_np, sample_rate)

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000,
        language: str | None = None,
    ) -> Transcript:
        """Direct path for VAD output: int16 PCM bytes → transcript.

        `language` overrides the default language per call. Useful for the
        wake detector, which should always listen for German even when the
        STT default is "auto".
        """
        self._ensure_model()
        audio_np = pcm_bytes_to_np(pcm_bytes)
        return await self._transcribe_np(audio_np, sample_rate, language=language)

    async def _transcribe_np(
        self, audio_np: np.ndarray, sample_rate: int,
        language: str | None = None,
    ) -> Transcript:
        # faster-whisper is synchronous → ship it off to a thread
        import asyncio
        return await asyncio.to_thread(self._transcribe_sync, audio_np, sample_rate, language)

    def _transcribe_sync(
        self, audio_np: np.ndarray, sample_rate: int,
        language: str | None = None,
    ) -> Transcript:
        # faster-whisper accepts np.ndarray float32 directly when 16 kHz
        if sample_rate != 16_000:
            # A resample would be needed here — but we expect 16 kHz from capture
            raise ValueError(f"Expected 16 kHz, got {sample_rate} Hz")

        # Per-call override takes precedence over self._language
        effective_lang = language if language is not None else self._language

        # NON-BLOCKING acquire across BOTH the transcribe() call and the lazy
        # generator materialization (``list(segments_iter)`` runs the actual
        # ctranslate2 decode). The SAME instance is shared by the rolling-whisper
        # wake poll loop AND the VAD "listening bubble" probe; ctranslate2 is not
        # thread-safe for concurrent transcribe on one model. A *blocking* lock
        # would let a HUNG call (the 2-hour "Hey Nico" wedge, forensic 2026-06-29:
        # every transcribe timed out at 8 s forever) pile every later call up
        # behind it. Non-blocking instead: if a call is already in flight, skip
        # (raise ``TranscribeBusy``) so the caller re-polls; a run of these is the
        # wedge signal that drives ``recover()``. Capture the lock locally so a
        # concurrent ``recover()`` swapping in a fresh lock still releases THIS one.
        lock = self._infer_lock
        if not lock.acquire(blocking=False):
            raise TranscribeBusy("a transcription is already in flight on this model")
        try:
            self._ensure_model()  # rebuild a fresh model if recover() cleared it
            model = self._model
            assert model is not None
            segments_iter, info = model.transcribe(
                audio_np,
                language=effective_lang,
                beam_size=self._beam_size,
                vad_filter=self._vad_filter,
                condition_on_previous_text=False,
                initial_prompt=self._initial_prompt,
                no_speech_threshold=self._no_speech_threshold,
            )
            # segments_iter is a generator — iterating over it materializes it
            segments = list(segments_iter)
        finally:
            lock.release()
        text = "".join(s.text for s in segments).strip()

        # Segment tuples as metadata for debugging/flight-recorder
        seg_dicts = tuple(
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "avg_logprob": s.avg_logprob,
                "no_speech_prob": getattr(s, "no_speech_prob", None),
            }
            for s in segments
        )

        # Confidence approximation: averaged exp(avg_logprob) — not perfect, good enough.
        if segments:
            avg = sum(s.avg_logprob for s in segments) / len(segments)
            confidence = float(np.exp(avg))
        else:
            confidence = 0.0

        return Transcript(
            text=text,
            language=info.language,
            confidence=confidence,
            is_partial=False,
            segments=seg_dicts,
        )

    async def stream_transcribe(
        self, audio: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[Transcript]:
        """Placeholder for incremental transcription — Phase 2+."""
        final = await self.transcribe(audio)
        yield final
