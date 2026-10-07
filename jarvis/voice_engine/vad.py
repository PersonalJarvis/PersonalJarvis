"""Silero VAD v6 on ONNX Runtime, without torch.

Mirrors the official ``OnnxWrapper`` (silero-vad ``utils_vad.py``): 512-sample
frames at 16 kHz, each prefixed with the last 64 samples of the previous frame,
and a recurrent state of shape (2, 1, 128).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

FRAME_SAMPLES = 512
CONTEXT_SAMPLES = 64
RATE = 16_000
FRAME_MS = FRAME_SAMPLES * 1000 // RATE  # 32 ms


class SileroVad:
    def __init__(self, model_path: Path, *, threads: int = 1) -> None:
        import onnxruntime as ort  # noqa: PLC0415 - heavy runtime, imported on use

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = max(1, threads)
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._rate = np.array(RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        """Speech probability of one 512-sample frame (float32, 16 kHz)."""
        if frame.shape[-1] != FRAME_SAMPLES:
            raise ValueError(f"Silero expects {FRAME_SAMPLES} samples, got {frame.shape[-1]}")
        chunk = np.concatenate([self._context, frame.reshape(1, -1).astype(np.float32)], axis=1)
        out, state = self._session.run(
            None, {"input": chunk, "state": self._state, "sr": self._rate}
        )
        self._state = state
        self._context = chunk[:, -CONTEXT_SAMPLES:]
        return float(out.reshape(-1)[0])


@dataclass(slots=True)
class VadEvent:
    kind: str  # "speech_start" | "silence_start" | "silence_end"
    at_ms: int


class Endpointer:
    """Turns frame probabilities into speech/silence edges with hysteresis.

    ``speech_start`` fires after ``min_speech_ms`` of voiced frames;
    ``silence_start`` after ``silence_ms`` of unvoiced frames inside speech —
    the moment the turn detector and the speculative transcription start;
    ``silence_end`` when the user resumes before the turn was taken.
    """

    def __init__(
        self,
        *,
        on_threshold: float = 0.5,
        off_threshold: float = 0.35,
        min_speech_ms: int = 160,
        silence_ms: int = 200,
        early_ms: int = 0,
    ) -> None:
        self.on_threshold = on_threshold
        self.off_threshold = off_threshold
        self.min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self.silence_frames = max(1, silence_ms // FRAME_MS)
        # ``silence_early`` fires once per pause after ``early_ms`` of quiet, so
        # the transcription can start before the turn decision is due.
        self.early_frames = max(1, early_ms // FRAME_MS) if early_ms else 0
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self.in_silence = False
        self._voiced = 0
        self._unvoiced = 0
        self._frame = 0

    def start_speech(self) -> None:
        """Enter speech now (e.g. after a barge-in detected elsewhere), with clean counters."""
        self.in_speech = True
        self.in_silence = False
        self._voiced = self.min_speech_frames
        self._unvoiced = 0

    def feed(self, probability: float) -> VadEvent | None:
        self._frame += 1
        now = self._frame * FRAME_MS
        if probability >= self.on_threshold:
            self._voiced += 1
            self._unvoiced = 0
            if not self.in_speech and self._voiced >= self.min_speech_frames:
                self.in_speech = True
                return VadEvent("speech_start", now)
            if self.in_silence:
                self.in_silence = False
                return VadEvent("silence_end", now)
            return None
        if probability < self.off_threshold:
            self._unvoiced += 1
            if not self.in_speech:
                self._voiced = 0
            elif not self.in_silence:
                if self._unvoiced >= self.silence_frames:
                    self.in_silence = True
                    return VadEvent("silence_start", now)
                if self.early_frames and self._unvoiced == self.early_frames:
                    return VadEvent("silence_early", now)
        return None
