"""Cross-platform streaming PCM helpers for realtime voice.

Realtime providers use different input rates (OpenAI 24 kHz, Gemini 16 kHz),
while browser and desktop capture devices commonly run at 48 kHz or 16 kHz.
This module owns the provider-neutral conversion without ``audioop`` so the
path remains usable on Python 3.13+ and on CPU-only/headless installations.
"""

from __future__ import annotations

import numpy as np


class StreamingPcm16Resampler:
    """Stateful linear mono int16 PCM resampler.

    One trailing sample and the fractional source position are carried across
    chunks, preventing discontinuities at WebSocket or microphone frame
    boundaries. The implementation is CPU-only and uses NumPy, which is part of
    the universal base installation.
    """

    def __init__(self, from_rate: int, to_rate: int) -> None:
        if from_rate <= 0 or to_rate <= 0:
            raise ValueError("PCM sample rates must be positive")
        self.from_rate = int(from_rate)
        self.to_rate = int(to_rate)
        self._step = self.from_rate / self.to_rate
        self._tail: np.ndarray | None = None
        self._position = 0.0

    def process(self, pcm16: bytes) -> bytes:
        if not pcm16:
            return b""
        if len(pcm16) % 2:
            raise ValueError("PCM16 input must contain complete 16-bit samples")
        if self.from_rate == self.to_rate:
            return bytes(pcm16)

        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float64)
        if self._tail is not None:
            samples = np.concatenate((self._tail, samples))
        if samples.size < 2:
            self._tail = samples[-1:].copy()
            return b""

        limit = float(samples.size - 1)
        positions = np.arange(self._position, limit, self._step, dtype=np.float64)
        if positions.size:
            left = np.floor(positions).astype(np.int64)
            fraction = positions - left
            values = samples[left] + (samples[left + 1] - samples[left]) * fraction
            output = np.clip(np.rint(values), -32768, 32767).astype("<i2").tobytes()
            self._position = float(positions[-1] + self._step - limit)
        else:
            output = b""
            self._position -= limit

        self._tail = samples[-1:].copy()
        return output

    def reset(self) -> None:
        self._tail = None
        self._position = 0.0


__all__ = ["StreamingPcm16Resampler"]
