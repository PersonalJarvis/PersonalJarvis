"""faster-whisper STT-Plugin.

Implementiert strukturell `STTProvider` — kein Vererbung, nur Duck-Type.
Das Modell (distil-large-v3, multilingual DE+EN) wird lazy beim ersten
`start()`-Call in GPU-Memory geladen (~1.5 GB VRAM bei int8_float16).

Auf RTX 5070 Ti liefert distil-large-v3 für eine 5-Sekunden-Utterance
~250 ms Latenz — gut genug für Phase 1.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import numpy as np

from jarvis.audio.capture import pcm_bytes_to_np
from jarvis.core.protocols import AudioChunk, Transcript


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
        from faster_whisper import WhisperModel

        device = self._device
        compute_type = self._compute_type
        # ``int8_float16`` is a CUDA-only compute type. On a CPU-only machine
        # (no NVIDIA GPU — the common case) WhisperModel raises
        # "target device or backend do not support efficient int8_float16",
        # which killed the whole speech pipeline → the stt_match wake word
        # ("Hey Jarvis") and dictation silently stopped working. Coerce to a
        # CPU-supported type up front, then fall back defensively.
        if device != "cuda" and "float16" in compute_type:
            compute_type = "int8"
        try:
            self._model = WhisperModel(
                self._model_name, device=device, compute_type=compute_type
            )
            self._device, self._compute_type = device, compute_type
        except (ValueError, RuntimeError):
            # Last-resort CPU fallbacks so local Whisper never hard-fails boot.
            for fallback in ("int8", "float32"):
                if fallback == compute_type:
                    continue
                try:
                    self._model = WhisperModel(
                        self._model_name, device="cpu", compute_type=fallback
                    )
                    self._device, self._compute_type = "cpu", fallback
                    return
                except (ValueError, RuntimeError):
                    continue
            raise

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
