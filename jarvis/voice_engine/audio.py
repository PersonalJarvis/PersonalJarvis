"""PCM helpers shared by every engine component.

Audio inside the engine is mono float32 in [-1, 1]. The wire format to the app
is PCM16 little-endian; conversion happens only at the edges.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np

STT_RATE = 16_000
OUTPUT_RATE = 24_000


def to_float32(pcm16: bytes | np.ndarray) -> np.ndarray:
    data = np.frombuffer(pcm16, dtype="<i2") if isinstance(pcm16, bytes) else pcm16
    return data.astype(np.float32) / 32768.0


def to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def resample(samples: np.ndarray, rate_in: int, rate_out: int) -> np.ndarray:
    """High-quality resampling through soxr, with a band-limited numpy fallback.

    The fallback exists so pure-numpy environments (tests, a broken wheel) still
    work; it low-passes by averaging before linear interpolation, which is
    adequate for VAD and turn detection but not for listening.
    """
    samples = np.asarray(samples, dtype=np.float32)
    if rate_in == rate_out or samples.size == 0:
        return samples
    try:
        import soxr  # noqa: PLC0415 - optional, lazily imported

        return soxr.resample(samples, rate_in, rate_out).astype(np.float32)
    except ImportError:
        # soxr is an optional speed/quality upgrade; the fallback below is exact
        # enough for detection models and keeps the engine importable without it.
        pass
    if rate_out < rate_in:
        width = max(1, int(round(rate_in / rate_out)))
        if width > 1:
            kernel = np.ones(width, dtype=np.float32) / width
            samples = np.convolve(samples, kernel, mode="same").astype(np.float32)
    duration = samples.size / rate_in
    count = max(1, int(round(duration * rate_out)))
    positions = np.linspace(0, samples.size - 1, count)
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"{path.name}: only 16-bit PCM WAV is supported, got {8 * width}-bit")
    samples = to_float32(raw)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(rate))
        handle.writeframes(to_pcm16(np.asarray(samples, dtype=np.float32)))


def silence(seconds: float, rate: int) -> np.ndarray:
    return np.zeros(max(0, int(round(seconds * rate))), dtype=np.float32)


def as_float_array(chunk: Any) -> np.ndarray:
    """Normalise a TTS chunk (numpy, list or torch tensor) to float32 numpy."""
    if hasattr(chunk, "detach"):
        chunk = chunk.detach().cpu().numpy()
    return np.asarray(chunk, dtype=np.float32).reshape(-1)
