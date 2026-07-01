"""Deterministic fakes for the telephony STT -> Brain -> TTS seams.

Convention (CLAUDE.md): fakes over mocks. These let the telephony session loop
and the media-stream integration test run with no model download, no API key
and no real socket.

Each fake matches only the narrow surface ``TelephonyCallSession`` calls:

* ``FakeSTT.transcribe_pcm(pcm, sample_rate=...)`` -> a ``_FakeTranscript``.
* ``FakeBrain.generate_stream(text)`` -> an async iterator of text chunks.
* ``FakeTTS.synthesize(text, language_code=...)`` -> async iterator of
  ``AudioChunk`` with 24 kHz int16 PCM (Gemini Charon output shape).
"""

from __future__ import annotations

import math
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from jarvis.core.protocols import AudioChunk


@dataclass(frozen=True, slots=True)
class _FakeTranscript:
    text: str
    language: str = "de"
    confidence: float = 0.95


class FakeSTT:
    """Returns a scripted transcript regardless of the audio bytes.

    ``scripted`` is consumed in order across calls; once exhausted the last
    value repeats. Records every ``(len, sample_rate)`` it saw for assertions.
    """

    name = "fake-stt"
    supports_streaming = False

    def __init__(self, scripted: list[str] | None = None) -> None:
        self._scripted = list(scripted or ["Wie spät ist es?"])  # i18n-allow: simulated German telephony STT transcript (product voice input)
        self._idx = 0
        self.calls: list[tuple[int, int]] = []

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000, language: str | None = None
    ) -> _FakeTranscript:
        self.calls.append((len(pcm_bytes), sample_rate))
        if self._idx < len(self._scripted):
            text = self._scripted[self._idx]
            self._idx += 1
        else:
            text = self._scripted[-1] if self._scripted else ""
        return _FakeTranscript(text=text)


class FakeBrain:
    """Streams a canned response (optionally per prompt) in small chunks.

    ``_history`` is a list so a test can assert the brain is per-call (a fresh
    FakeBrain per session has an empty history).
    """

    def __init__(
        self,
        response: str = "Es ist genau vierzehn Uhr dreißig.",  # i18n-allow: simulated German telephony TTS response (product voice output)
        responses: dict[str, str] | None = None,
        chunk_size: int = 8,
    ) -> None:
        self._response = response
        self._responses = responses or {}
        self._chunk_size = chunk_size
        self._history: list[str] = []
        self.prompts: list[str] = []

    async def generate_stream(self, user_text: str, **_kw) -> AsyncIterator[str]:
        self.prompts.append(user_text)
        self._history.append(user_text)
        text = self._responses.get(user_text, self._response)
        for i in range(0, len(text), self._chunk_size):
            yield text[i : i + self._chunk_size]


@dataclass
class FakeTTS:
    """Synthesizes a fixed-duration 24 kHz int16 tone per call.

    The audio content is irrelevant; what matters for tests is that real PCM
    bytes flow through the transcode + framing path and produce outbound mu-law
    frames. ``ms_per_char`` keeps long answers longer (proves no truncation).
    """

    sample_rate: int = 24_000
    freq: int = 220
    ms_per_char: int = 5
    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def synthesize(
        self, text: str, language_code: str = "de-DE", voice: str | None = None
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        total_ms = max(40, len(text) * self.ms_per_char)
        n_samples = self.sample_rate * total_ms // 1000
        # Emit in ~100 ms chunks to exercise the streaming resampler state.
        chunk_samples = self.sample_rate // 10
        produced = 0
        while produced < n_samples:
            count = min(chunk_samples, n_samples - produced)
            pcm = b"".join(
                struct.pack(
                    "<h",
                    int(
                        8000 * math.sin(2 * math.pi * self.freq * (produced + i) / self.sample_rate)
                    ),
                )
                for i in range(count)
            )
            produced += count
            yield AudioChunk(pcm=pcm, sample_rate=self.sample_rate, timestamp_ns=0, channels=1)


__all__ = ["FakeBrain", "FakeSTT", "FakeTTS"]
