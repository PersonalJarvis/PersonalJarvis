"""faster-whisper STT-Plugin.

Implementiert strukturell `STTProvider` — kein Vererbung, nur Duck-Type.
Das Modell (distil-large-v3, multilingual DE+EN) wird lazy beim ersten
`start()`-Call in GPU-Memory geladen (~1.5 GB VRAM bei int8_float16).

Auf RTX 5070 Ti liefert distil-large-v3 für eine 5-Sekunden-Utterance
~250 ms Latenz — gut genug für Phase 1.
"""
from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import AsyncIterator, Iterator
from typing import Any

import numpy as np

from jarvis.audio.capture import pcm_bytes_to_np
from jarvis.core.protocols import AudioChunk, Transcript

log = logging.getLogger(__name__)


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


def _cpu_safe_compute_type(compute_type: str) -> str:
    """Downgrade CUDA-only compute types to a CPU-compatible one.

    ``float16`` / ``int8_float16`` require a GPU; on a CPU / headless VPS they
    raise. ``int8`` is the universal CPU-safe equivalent (cloud-first floor).
    """
    if compute_type in ("float16", "int8_float16"):
        return "int8"
    return compute_type


def _new_whisper_model(model_name: str, device: str, compute_type: str) -> Any:
    """Construct a ``WhisperModel`` (overridable seam for tests).

    The heavy ``faster_whisper`` import stays lazy here so importing this module
    on a host without it is cheap; tests monkeypatch this function to avoid the
    import + a real model build. The import shield skips ctranslate2's
    transformers+torch converter stack (inference doesn't need it) — see
    :func:`inference_only_import_shield`.
    """
    with inference_only_import_shield():
        from faster_whisper import WhisperModel

    return WhisperModel(model_name, device=device, compute_type=compute_type)


class FasterWhisperProvider:
    """Lokaler Whisper-STT über faster-whisper (CTranslate2-Backend)."""

    name = "faster-whisper"
    supports_streaming = False  # wir können später stream_transcribe nachrüsten

    def __init__(
        self,
        model: str = "distil-large-v3",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        language: str | None = None,  # None = auto-detect (bilingual DE+EN)
        beam_size: int = 5,
        vad_filter: bool = False,  # wir haben externes Silero-VAD davor
        # Kein Initial-Prompt im Hot-Path: feste Beispielsaetze wurden bei
        # leisem Audio als Transkript halluziniert und gingen ans Brain.
        initial_prompt: str | None = None,
        no_speech_threshold: float = 0.6,
    ) -> None:
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._language = language if language and language != "auto" else None
        self._beam_size = beam_size
        self._vad_filter = vad_filter
        self._initial_prompt = initial_prompt
        self._no_speech_threshold = no_speech_threshold
        self._model: Any = None  # lazy

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        model_name = _normalize_model_name(self._model_name)
        device, compute_type = self._device, self._compute_type
        try:
            self._model = _new_whisper_model(model_name, device, compute_type)
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
        self._model = _new_whisper_model(model_name, fb_device, fb_ct)

    async def transcribe(self, audio: AsyncIterator[AudioChunk]) -> Transcript:
        """Sammelt alle Chunks, transkribiert am Stück.

        Für Phase 1 reicht das — die VAD-Schicht davor liefert uns bereits
        saubere Utterances, also ist "am Stück" das natürliche Granularity.
        """
        self._ensure_model()

        # Alle Chunks in einem float32-Array zusammenziehen
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
        """Direkter Weg für VAD-Output: int16-PCM-Bytes → Transcript.

        `language` überschreibt per Call die Default-Sprache. Nützlich für den
        Wake-Detector der auch bei STT-Default "auto" immer auf Deutsch hören soll.
        """
        self._ensure_model()
        audio_np = pcm_bytes_to_np(pcm_bytes)
        return await self._transcribe_np(audio_np, sample_rate, language=language)

    async def _transcribe_np(
        self, audio_np: np.ndarray, sample_rate: int,
        language: str | None = None,
    ) -> Transcript:
        # faster-whisper ist synchron → in Thread shippen
        import asyncio
        return await asyncio.to_thread(self._transcribe_sync, audio_np, sample_rate, language)

    def _transcribe_sync(
        self, audio_np: np.ndarray, sample_rate: int,
        language: str | None = None,
    ) -> Transcript:
        assert self._model is not None
        # faster-whisper akzeptiert np.ndarray float32 direkt wenn 16 kHz
        if sample_rate != 16_000:
            # Resample wäre hier nötig — wir erwarten aber 16 kHz von Capture
            raise ValueError(f"Erwartet 16 kHz, bekommen {sample_rate} Hz")

        # Per-Call-Override hat Vorrang vor self._language
        effective_lang = language if language is not None else self._language

        segments_iter, info = self._model.transcribe(
            audio_np,
            language=effective_lang,
            beam_size=self._beam_size,
            vad_filter=self._vad_filter,
            condition_on_previous_text=False,
            initial_prompt=self._initial_prompt,
            no_speech_threshold=self._no_speech_threshold,
        )
        # segments_iter ist generator — durchiterieren materialisiert
        segments = list(segments_iter)
        text = "".join(s.text for s in segments).strip()

        # Segment-Tuples als Meta für Debugging/Flight-Recorder
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

        # Confidence-Approximation: exp(avg_logprob) gemittelt — nicht perfekt, reicht.
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
        """Placeholder für inkrementelle Transkription — Phase 2+."""
        final = await self.transcribe(audio)
        yield final
