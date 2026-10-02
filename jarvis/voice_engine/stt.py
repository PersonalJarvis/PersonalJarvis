"""Parakeet TDT 0.6B v3 transcription through sherpa-onnx (offline transducer).

Parakeet v3 decodes a whole utterance at once. The engine runs it
speculatively when the user falls silent, so by the time the turn detector
decides, the transcript is usually ready (``docs/local-live-voice-rebuild.md``
section 4.3).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from jarvis.voice_engine.audio import STT_RATE


def default_threads() -> int:
    """All cores on small machines (measured: half of 4 vCPUs left Piper slower
    than Pocket in the Linux container), half of them, at most 4, elsewhere."""
    cores = os.cpu_count() or 2
    return cores if cores <= 4 else min(4, cores // 2)


class ParakeetStt:
    def __init__(
        self, model_dir: Path, *, threads: int | None = None, provider: str = "cpu"
    ) -> None:
        import sherpa_onnx  # noqa: PLC0415 - heavy runtime, imported on use

        def pick(stem: str) -> str:
            matches = sorted(model_dir.glob(f"{stem}*.onnx"))
            if not matches:
                raise FileNotFoundError(f"{model_dir.name}: no {stem}*.onnx")
            return str(matches[0])

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=pick("encoder"),
            decoder=pick("decoder"),
            joiner=pick("joiner"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=threads or default_threads(),
            model_type="nemo_transducer",
            provider=provider,
        )

    def transcribe(self, audio: np.ndarray, rate: int = STT_RATE) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(rate, np.asarray(audio, dtype=np.float32))
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()
