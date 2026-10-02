"""Piper voices through sherpa-onnx: the TTS floor that runs on every CPU.

sherpa-onnx's Apache-2.0 runtime replaces the GPL ``piper1`` package; only the
voice files come from Piper.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from jarvis.voice_engine.models import is_present, model_path
from jarvis.voice_engine.stt import default_threads

VOICES = {"de": "piper-de-thorsten-medium", "en": "piper-en-ryan-medium"}


class PiperTts:
    name = "piper"

    def __init__(self, voice_dir: Path, *, threads: int | None = None) -> None:
        import sherpa_onnx  # noqa: PLC0415 - heavy runtime, imported on use

        onnx = next(p for p in sorted(voice_dir.glob("*.onnx")))
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(onnx),
                    tokens=str(voice_dir / "tokens.txt"),
                    data_dir=str(voice_dir / "espeak-ng-data"),
                ),
                num_threads=threads or default_threads(),
                provider="cpu",
            ),
        )
        self._tts = sherpa_onnx.OfflineTts(config)
        self.sample_rate = int(self._tts.sample_rate)

    @classmethod
    def for_language(cls, language: str, *, threads: int | None = None) -> PiperTts:
        voice = VOICES.get(language)
        if voice is None or not is_present(voice):
            raise RuntimeError(f"no Piper voice installed for language {language!r}")
        return cls(model_path(voice), threads=threads)

    def stream(self, text: str, *, stop: threading.Event | None = None) -> Iterator[np.ndarray]:
        if stop is not None and stop.is_set():
            return
        audio = self._tts.generate(text, sid=0, speed=1.0)
        yield np.asarray(audio.samples, dtype=np.float32)
