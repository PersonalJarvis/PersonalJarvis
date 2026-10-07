"""Qwen3-TTS through faster-qwen3-tts (CUDA graphs): the premium GPU voice.

Needs torch with CUDA; Apple Silicon uses mlx-audio instead (phase P4). The
CustomVoice checkpoints carry named speakers, so no reference audio is needed.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import numpy as np

from jarvis.voice_engine.audio import as_float_array

QWEN_LANGUAGES = {"de": "German", "en": "English", "es": "Spanish", "fr": "French",
                  "it": "Italian", "pt": "Portuguese"}
CHECKPOINTS = {
    "0.6b": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "1.7b": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
}


class Qwen3Tts:
    def __init__(
        self,
        language: str,
        *,
        size: str = "0.6b",
        speaker: str = "Aiden",
        chunk_size: int = 4,
        device: str = "cuda",
    ) -> None:
        from faster_qwen3_tts import FasterQwen3TTS  # noqa: PLC0415 - heavy runtime

        if language not in QWEN_LANGUAGES:
            raise RuntimeError(f"Qwen3-TTS has no language {language!r}")
        self._language = QWEN_LANGUAGES[language]
        self._speaker = speaker
        self._chunk = int(chunk_size)
        self._model = FasterQwen3TTS.from_pretrained(CHECKPOINTS[size], device=device)
        self.sample_rate = 24_000
        self.name = f"qwen3-{size}"

    def warmup(self) -> None:
        self._model.warmup()

    def stream(self, text: str, *, stop: threading.Event | None = None) -> Iterator[np.ndarray]:
        chunks = self._model.generate_custom_voice_streaming(
            text=text, speaker=self._speaker, language=self._language, chunk_size=self._chunk
        )
        for audio, rate, _timing in chunks:
            if stop is not None and stop.is_set():
                return
            self.sample_rate = int(rate)
            yield as_float_array(audio)
