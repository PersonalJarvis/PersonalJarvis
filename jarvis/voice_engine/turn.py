"""Smart Turn v3.2 end-of-turn detection on ONNX Runtime, without transformers.

The model scores whether the user finished speaking from the last 8 s of
audio. Its input is Whisper's 80-bin log-mel spectrogram; this module
reimplements ``WhisperFeatureExtractor(chunk_length=8)`` with
``do_normalize=True`` in numpy, matching the reference ``inference.py`` of
github.com/pipecat-ai/smart-turn: keep the last 8 s, zero-pad at the BEGINNING,
normalise to zero mean and unit variance, then log-mel.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

RATE = 16_000
WINDOW_SECONDS = 8
N_FFT = 400
HOP = 160
N_MELS = 80


def _hertz_to_mel(freq: np.ndarray) -> np.ndarray:
    """Slaney mel scale, as transformers' ``hertz_to_mel(mel_scale="slaney")``."""
    freq = np.asarray(freq, dtype=np.float64)
    mels = 3.0 * freq / 200.0
    log_region = freq >= 1000.0
    mels[log_region] = 15.0 + np.log(freq[log_region] / 1000.0) * (27.0 / np.log(6.4))
    return mels


def _mel_to_hertz(mels: np.ndarray) -> np.ndarray:
    mels = np.asarray(mels, dtype=np.float64)
    freq = 200.0 * mels / 3.0
    log_region = mels >= 15.0
    freq[log_region] = 1000.0 * np.exp((np.log(6.4) / 27.0) * (mels[log_region] - 15.0))
    return freq


@lru_cache(maxsize=1)
def mel_filters() -> np.ndarray:
    """(201, 80) Slaney-normalised triangular filter bank for 0-8 kHz."""
    fft_freqs = np.linspace(0, RATE // 2, 1 + N_FFT // 2)
    low, high = _hertz_to_mel(np.array([0.0, 8000.0]))
    mel_points = np.linspace(low, high, N_MELS + 2)
    filter_freqs = _mel_to_hertz(mel_points)
    filter_diff = np.diff(filter_freqs)
    slopes = filter_freqs[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / filter_diff[:-1]
    up = slopes[:, 2:] / filter_diff[1:]
    filters = np.maximum(0.0, np.minimum(down, up))
    enorm = 2.0 / (filter_freqs[2 : N_MELS + 2] - filter_freqs[:N_MELS])
    return filters * enorm[None, :]


@lru_cache(maxsize=1)
def _hann() -> np.ndarray:
    return np.hanning(N_FFT + 1)[:-1]  # periodic Hann, as window_function(400, "hann")


def prepare_window(audio: np.ndarray) -> np.ndarray:
    """Last 8 s of 16 kHz audio, zero-padded at the beginning."""
    target = WINDOW_SECONDS * RATE
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size >= target:
        return audio[-target:]
    return np.pad(audio, (target - audio.size, 0))


def log_mel(audio: np.ndarray) -> np.ndarray:
    """Whisper-compatible (80, 800) log-mel features of an 8 s window."""
    x = prepare_window(audio).astype(np.float64)
    x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)
    padded = np.pad(x, (N_FFT // 2, N_FFT // 2), mode="reflect")
    frames = 1 + (padded.size - N_FFT) // HOP
    strides = (padded.strides[0] * HOP, padded.strides[0])
    windows = np.lib.stride_tricks.as_strided(padded, shape=(frames, N_FFT), strides=strides)
    spectrum = np.fft.rfft(windows * _hann(), n=N_FFT, axis=1)
    power = (spectrum.real**2 + spectrum.imag**2).T  # (201, frames)
    mel = mel_filters().T @ power  # (80, frames)
    log_spec = np.log10(np.maximum(mel, 1e-10))[:, :-1]
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return ((log_spec + 4.0) / 4.0).astype(np.float32)


class SmartTurn:
    def __init__(self, model_path: Path, *, threads: int = 1) -> None:
        import onnxruntime as ort  # noqa: PLC0415 - heavy runtime, imported on use

        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = max(1, threads)
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def probability(self, audio16k: np.ndarray) -> float:
        """Probability that the turn is complete (sigmoid output of the model)."""
        features = log_mel(audio16k)[None, :, :]
        (out,) = self._session.run(None, {self._input: features})
        return float(np.asarray(out).reshape(-1)[0])
