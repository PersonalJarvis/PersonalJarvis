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


def _bound_ct2_threads(default: int = 2) -> None:
    """Bound the ctranslate2/OpenMP CPU thread pool BEFORE ctranslate2 is imported.

    Defensive hardening for the CPU stt_match wake path (AP-24/AP-25/BUG-036):
    ctranslate2's auto thread-pool can deadlock against another OpenMP consumer
    sharing the process (see ``_new_whisper_model`` / ``cpu_threads``). Setting
    ``OMP_NUM_THREADS`` caps that pool at the environment level, one layer below
    the per-instance ``cpu_threads=2`` constructor pin already in place.

    Uses ``os.environ.setdefault`` so an operator's own explicit setting is
    NEVER clobbered. This is DEFENSIVE ONLY: it does not claim to cure the
    constellation-specific ctranslate2<->OpenMP deadlock documented in AP-25
    — it only narrows the CPU thread-pool's blast radius. The real fix is the
    vosk_kws engine bypassing this ctranslate2 path entirely.

    Must be called before ctranslate2's first import on a given path (its
    thread pool reads these env vars at that point), and NEVER at module
    import time here — that would also throttle the utterance STT model,
    which intentionally keeps auto threads (see ``FasterWhisperProvider``'s
    default ``cpu_threads=0``).
    """
    import os

    os.environ.setdefault("OMP_NUM_THREADS", str(default))


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


# --- Boot-time wake-model prefetch (TTU iteration 10) -----------------------
# The instrumented cold-boot timeline (docs/diagnostics/BOOT-TTU-NOTES.md)
# shows the wake model load+prime (~3.1 s) runs strictly AFTER the import
# mountain (~2.6 s) and the WebServer ctor (~1.5 s), even though it only needs
# faster_whisper. Loading it in a daemon thread right after the UI shell is
# served overlaps those blocks. ``_ensure_model`` ADOPTS the prefetched engine
# when its exact key (normalized model, device, compute_type, cpu_threads)
# matches; on a mismatch or a prefetch failure it loads lazily as before —
# never slower than today beyond the bounded in-flight wait. The adopted model
# is popped (single use): a later ``recover()`` therefore always rebuilds a
# FRESH engine and can never re-adopt a possibly wedged prefetch (AP-24).
# Thread-safety: the prefetch runs in ONE daemon thread and hands over via the
# event+dict under a lock; the provider's own inference lock is untouched.
_PREFETCH_LOCK = threading.Lock()
_PREFETCH_EVENTS: dict[tuple, threading.Event] = {}
_PREFETCHED_MODELS: dict[tuple, Any] = {}
# Upper bound on waiting for an in-flight prefetch of the SAME key before
# falling back to a local load. The prefetch starts seconds earlier than any
# consumer, so the typical remaining wait is <1 s; the bound only matters if
# the prefetch thread died mid-load.
_PREFETCH_WAIT_S = 20.0


def _prefetch_key(
    model_name: str, device: str, compute_type: str, cpu_threads: int
) -> tuple:
    return (_normalize_model_name(model_name), device, compute_type, int(cpu_threads))


def prefetch_model(
    model_name: str, device: str, compute_type: str, cpu_threads: int
) -> bool:
    """Load a WhisperModel into the hand-over cache (call from a daemon thread).

    Idempotent per key: a second call while one is in flight (or done) is a
    no-op. Failures are swallowed — the consumer simply loads lazily. Returns
    True iff a model was cached by THIS call.
    """
    key = _prefetch_key(model_name, device, compute_type, cpu_threads)
    with _PREFETCH_LOCK:
        if key in _PREFETCH_EVENTS:
            return False
        _PREFETCH_EVENTS[key] = threading.Event()
    model: Any = None
    primed = False
    try:
        model = _new_whisper_model(key[0], device, compute_type, int(cpu_threads))
    except Exception as exc:  # noqa: BLE001 — a prefetch must never break boot
        log.debug("Wake-model prefetch failed (lazy load will cover it): %s", exc)
    if model is not None:
        # Prime in the prefetch thread too (TTU iteration 11): the FIRST real
        # transcribe pays kernel/algo warm-up costs, and paying them here —
        # still overlapped with the import mountain — lets ``warm_up`` adopt a
        # READY engine and skip its own priming inference on the critical
        # path. Best-effort: a priming failure still hands the loaded model
        # over (``warm_up`` will prime it itself). Nothing else can touch the
        # engine yet (it is published only after this), so this never races
        # the provider's inference lock (AP-24).
        try:
            rng = np.random.default_rng(0)
            warm_audio = rng.standard_normal(16_000).astype(np.float32) * 0.001
            segments_iter, _info = model.transcribe(warm_audio, beam_size=1)
            list(segments_iter)  # materialise = run the actual decode
            primed = True
        except Exception as exc:  # noqa: BLE001 — priming is best-effort
            log.debug("Wake-model prefetch priming skipped: %s", exc)
    with _PREFETCH_LOCK:
        if model is not None:
            _PREFETCHED_MODELS[key] = (model, primed)
    _PREFETCH_EVENTS[key].set()
    return model is not None


class FasterWhisperProvider:
    """Local Whisper STT via faster-whisper (CTranslate2 backend)."""

    name = "faster-whisper"
    supports_streaming = False  # we can add stream_transcribe later

    def __init__(
        self,
        model: str = "distil-large-v3",
        # CPU-first default (ADR-0024): a bare construction with no explicit
        # device must never assume the maintainer's GPU. Real callers pass the
        # config device, resolved through ``jarvis.core.device.resolve_device``
        # at the STT factory before it reaches here; the cloud-first floor is CPU.
        device: str = "cpu",
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
        # CPU + a CUDA-only compute type is a guaranteed construction error, not a
        # silent downgrade: CTranslate2 RAISES ``ValueError("Requested int8_float16
        # compute type, but the target device or backend do not support efficient
        # int8_float16 computation")`` — it does not fall back on its own (verified
        # 2026-07-04 against ctranslate2 on a CPU device). The shipped cloud-first
        # default pairs ``device="cpu"`` with ``compute_type="int8_float16"`` (the
        # value the maintainer's CUDA box needs), so EVERY fresh CPU/VPS install
        # otherwise hits that ValueError on the first model build — twice (boot
        # prefetch + the first real ``_ensure_model``) — before ``_ensure_model``'s
        # retry recovers it to ``int8``. Pre-coercing here when the device is CPU
        # skips the doomed attempt and its scary WARNING while landing on the exact
        # same engine the fallback already produces. Only fires for ``device="cpu"``
        # (case-insensitive), so a ``cuda`` / ``auto`` device is untouched — the GPU
        # path keeps int8_float16 verbatim.
        if self._device.lower() == "cpu":
            self._compute_type = _cpu_safe_compute_type(self._compute_type)
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
        # True when _ensure_model adopted a boot-prefetched engine that was
        # ALREADY primed in the prefetch thread — warm_up then skips its own
        # priming inference (TTU iteration 11). Reset by recover().
        self._adopted_primed = False

    @property
    def is_warm(self) -> bool:
        """True when the model is constructed AND primed (safe to poll)."""
        return self._warm

    @property
    def bias_prompt(self) -> str | None:
        """The ``initial_prompt`` this instance primes the decoder with.

        The rolling wake uses this to know whether a transcript could be a
        PROMPT ECHO (the primed model emitting the prompt verbatim on
        ambiguous noise/breath windows — the 2026-07-02 ghost-activation
        forensic) and therefore needs the unbiased second-pass confirm."""
        return self._initial_prompt

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        model_name = _normalize_model_name(self._model_name)
        device, compute_type = self._device, self._compute_type
        # Adopt the boot-prefetched engine when its exact key matches (see the
        # prefetch block above). Popped = single use, so recover() always
        # rebuilds fresh. On an in-flight prefetch of OUR key, wait bounded
        # instead of double-loading the same weights.
        key = (model_name, device, compute_type, int(self._cpu_threads))
        ev = _PREFETCH_EVENTS.get(key)
        if ev is not None:
            ev.wait(timeout=_PREFETCH_WAIT_S)
            with _PREFETCH_LOCK:
                prefetched = _PREFETCHED_MODELS.pop(key, None)
            if prefetched is not None:
                model, primed = prefetched
                self._model = model
                self._adopted_primed = bool(primed)
                log.info(
                    "Adopted boot-prefetched Whisper model (%s/%s/%s, primed=%s).",
                    model_name, device, compute_type, primed,
                )
                return
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
        self._adopted_primed = False
        # (recover() drops a wedged engine; the next _ensure_model must load
        # and prime FRESH — never inherit the primed shortcut, AP-24.)

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
        if self._adopted_primed:
            # The boot prefetch already primed this exact engine off the
            # critical path (TTU iteration 11) — a second priming inference
            # here would just re-pay ~1-2 s on the usable path.
            self._warm = True
            return
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
        ignore_initial_prompt: bool = False,
    ) -> Transcript:
        """Direct path for VAD output: int16 PCM bytes → transcript.

        `language` overrides the default language per call. Useful for the
        wake detector, which should always listen for German even when the
        STT default is "auto".

        ``ignore_initial_prompt=True`` runs THIS call without the configured
        phrase bias — the rolling wake's echo confirm: when a candidate
        transcript is exactly the primed phrase, an unbiased second pass on
        the same window separates a genuine wake (the unprimed ear still
        hears speech) from a prompt echo on noise/breath (it hears nothing).
        """
        self._ensure_model()
        audio_np = pcm_bytes_to_np(pcm_bytes)
        return await self._transcribe_np(
            audio_np, sample_rate, language=language,
            ignore_initial_prompt=ignore_initial_prompt,
        )

    async def _transcribe_np(
        self, audio_np: np.ndarray, sample_rate: int,
        language: str | None = None,
        ignore_initial_prompt: bool = False,
    ) -> Transcript:
        # faster-whisper is synchronous → ship it off to a thread
        import asyncio
        return await asyncio.to_thread(
            self._transcribe_sync, audio_np, sample_rate, language,
            ignore_initial_prompt,
        )

    def _transcribe_sync(
        self, audio_np: np.ndarray, sample_rate: int,
        language: str | None = None,
        ignore_initial_prompt: bool = False,
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
                # Echo-confirm support: the caller may run one UNPRIMED pass
                # over the same audio to tell a genuine wake from the prompt
                # echoing back on noise (2026-07-02 ghost activations).
                initial_prompt=(
                    None if ignore_initial_prompt else self._initial_prompt
                ),
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
