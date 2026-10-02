"""Synthetic user speech and the lazily loaded speech components."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.voice_engine import models
from jarvis.voice_engine.audio import STT_RATE, read_wav, resample, silence, write_wav
from jarvis.voice_engine.paths import results_dir


@dataclass
class Components:
    """Loads each component once and records how long loading took."""

    load_s: dict[str, float] = field(default_factory=dict)
    _cache: dict[str, Any] = field(default_factory=dict)

    def _load(self, key: str, factory: Any) -> Any:
        if key not in self._cache:
            started = time.perf_counter()
            self._cache[key] = factory()
            self.load_s[key] = round(time.perf_counter() - started, 3)
        return self._cache[key]

    def stt(self) -> Any:
        from jarvis.voice_engine.stt import ParakeetStt  # noqa: PLC0415

        return self._load(
            "stt", lambda: ParakeetStt(models.model_path("parakeet-tdt-0.6b-v3-int8"))
        )

    def vad(self) -> Any:
        from jarvis.voice_engine.vad import SileroVad  # noqa: PLC0415

        return self._load("vad", lambda: SileroVad(models.model_path("silero-vad-v6")))

    def turn(self) -> Any:
        from jarvis.voice_engine.turn import SmartTurn  # noqa: PLC0415

        return self._load("turn", lambda: SmartTurn(models.model_path("smart-turn-v3.2")))

    def tts(self, kind: str, language: str, **options: Any) -> Any:
        from jarvis.voice_engine.tts import load_tts  # noqa: PLC0415

        key = f"tts:{kind}:{language}:{sorted(options.items())}"
        return self._load(key, lambda: load_tts(kind, language, **options))


class UserVoice:
    """Renders corpus text as 16 kHz 'user' speech, cached on disk."""

    def __init__(self, components: Components, kind: str = "piper") -> None:
        self._components = components
        self.kind = kind
        self._root = results_dir() / "speech-cache" / kind

    def render(self, text: str, language: str, *, pad_s: float = 0.2) -> np.ndarray:
        """Speech for ``text`` with ``pad_s`` of silence on both sides.

        The padding stands in for the VAD pre-roll and the trailing pause a
        real microphone segment has.
        """
        digest = hashlib.sha256(f"{language}|{text}".encode()).hexdigest()[:16]
        path = self._root / language / f"{digest}.wav"
        if path.exists():
            audio, rate = read_wav(path)
            audio16 = resample(audio, rate, STT_RATE)
        else:
            tts = self._components.tts(self.kind, language)
            audio = np.concatenate(list(tts.stream(text)))
            audio16 = resample(audio, tts.sample_rate, STT_RATE)
            write_wav(path, audio16, STT_RATE)
        if pad_s <= 0:
            return audio16
        pad = silence(pad_s, STT_RATE)
        return np.concatenate([pad, audio16, pad])

    def render_with_pauses(
        self, segments: list[str], pauses_ms: list[int], language: str, tail_s: float = 1.2
    ) -> tuple[np.ndarray, list[float]]:
        """One continuous utterance with silence inserted at the segment borders.

        Rendering each segment separately gives every piece a sentence-final
        melody, and the turn detector then (rightly) hears a finished sentence
        at every pause. Rendering the whole sentence once and splicing silence
        in at the quietest point near each border keeps the mid-sentence
        melody a hesitating speaker has. Returns the audio and the end time of
        every segment.
        """
        audio = trim_silence(self.render(" ".join(segments), language, pad_s=0.0))
        total_chars = sum(len(s) + 1 for s in segments)
        cuts: list[int] = []
        consumed = 0
        for segment in segments[:-1]:
            consumed += len(segment) + 1
            cuts.append(_quietest_near(audio, int(audio.size * consumed / total_chars)))
        lead = silence(0.3, STT_RATE)
        parts: list[np.ndarray] = [lead]
        ends: list[float] = []
        elapsed = lead.size / STT_RATE
        start = 0
        for index, cut in enumerate([*cuts, audio.size]):
            piece = audio[start:cut]
            parts.append(piece)
            elapsed += piece.size / STT_RATE
            ends.append(elapsed)
            if index < len(pauses_ms):
                pause = silence(pauses_ms[index] / 1000.0, STT_RATE)
                parts.append(pause)
                elapsed += pause.size / STT_RATE
            start = cut
        parts.append(silence(tail_s, STT_RATE))
        return np.concatenate(parts), ends


def _quietest_near(audio: np.ndarray, center: int, window_s: float = 0.15) -> int:
    """Sample index of the lowest-energy 10 ms frame within ``window_s`` of ``center``."""
    frame = int(0.01 * STT_RATE)
    low = max(0, center - int(window_s * STT_RATE))
    high = min(audio.size - frame, center + int(window_s * STT_RATE))
    if high <= low:
        return center
    starts = np.arange(low, high, frame // 2)
    energy = [float(np.mean(audio[s : s + frame] ** 2)) for s in starts]
    return int(starts[int(np.argmin(energy))] + frame // 2)


def trim_silence(audio: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    voiced = np.flatnonzero(np.abs(audio) > threshold)
    if voiced.size == 0:
        return audio
    start = max(0, voiced[0] - int(0.02 * STT_RATE))
    end = min(audio.size, voiced[-1] + int(0.05 * STT_RATE))
    return audio[start:end]


def save_sample(name: str, audio: np.ndarray, rate: int) -> Path:
    path = results_dir() / "samples" / f"{name}.wav"
    write_wav(path, audio, rate)
    return path
