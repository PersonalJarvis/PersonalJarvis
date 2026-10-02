"""Text-to-speech backends behind one streaming interface.

The ladder (``docs/local-live-voice-rebuild.md`` section 4.7): Piper is the
floor that runs everywhere, Pocket TTS the natural CPU voice, Qwen3-TTS the
premium GPU/MLX voice. Each backend is loaded for one language and yields
float32 chunks at its own ``sample_rate``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Protocol

import numpy as np


class TtsEngine(Protocol):
    name: str
    sample_rate: int

    def stream(self, text: str, *, stop: threading.Event | None = None) -> Iterator[np.ndarray]:
        """Yield audio chunks for one clause; stop early when ``stop`` is set."""
        ...


LANGUAGE_NAMES = {"de": "german", "en": "english", "es": "spanish", "fr": "french",
                  "it": "italian", "pt": "portuguese", "nl": "dutch"}


def load_tts(kind: str, language: str, **options: object) -> TtsEngine:
    """Build one backend by name; heavy imports happen inside the backend."""
    if kind == "piper":
        from jarvis.voice_engine.tts.piper import PiperTts  # noqa: PLC0415

        return PiperTts.for_language(language, **options)  # type: ignore[arg-type]
    if kind == "pocket":
        from jarvis.voice_engine.tts.pocket import PocketTts  # noqa: PLC0415

        return PocketTts(language, **options)  # type: ignore[arg-type]
    if kind == "qwen3":
        from jarvis.voice_engine.tts.qwen3 import Qwen3Tts  # noqa: PLC0415

        return Qwen3Tts(language, **options)  # type: ignore[arg-type]
    raise ValueError(f"unknown TTS backend {kind!r}")
