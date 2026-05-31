"""Pipeline must support a faster-whisper-free lightweight local wake path.

When ``enable_local_whisper=False`` (the new cloud-first default), the pipeline
instantiates NO FasterWhisperProvider at all: openWakeWord is the only local
detector, the RollingWhisperWake backstop is off, and the VAD stability probe
is a no-op. Post-wake utterance STT still resolves from config (Groq cloud).

The legacy heavy path (default ``enable_local_whisper=True``) is unchanged so
existing callers/tests keep their local Whisper instance.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from jarvis.core.config import STTConfig
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator:
        if False:  # pragma: no cover
            yield


def _cfg_groq() -> SimpleNamespace:
    return SimpleNamespace(stt=STTConfig(provider="groq-api"))


def test_lightweight_mode_instantiates_no_faster_whisper() -> None:
    pipe = SpeechPipeline(
        tts=FakeTTS(),
        bus=None,
        enable_openwakeword=False,
        enable_whisper_wake=False,
        enable_local_whisper=False,
        config=_cfg_groq(),
    )
    assert pipe._stt is None
    assert pipe._whisper_wake is None
    assert type(pipe._utterance_stt).__name__ == "GroqWhisperAPI"


def test_heavy_mode_keeps_faster_whisper_by_default() -> None:
    # Default enable_local_whisper=True → existing behaviour unchanged.
    pipe = SpeechPipeline(tts=FakeTTS(), bus=None, enable_whisper_wake=False)
    assert pipe._stt is not None
    assert type(pipe._stt).__name__ == "FasterWhisperProvider"


@pytest.mark.asyncio
async def test_vad_probe_uses_utterance_stt_without_local_whisper() -> None:
    pipe = SpeechPipeline(
        tts=FakeTTS(),
        bus=None,
        enable_openwakeword=False,
        enable_whisper_wake=False,
        enable_local_whisper=False,
        config=_cfg_groq(),
    )
    seen: list[bytes] = []

    async def _probe(
        pcm: bytes, generation: int | None = None, tail_loud: bool = True
    ) -> None:
        seen.append(pcm)

    pipe._stt_probe_async = _probe  # type: ignore[method-assign]

    pipe._on_vad_probe(b"\x00" * 320)
    await asyncio.sleep(0)

    assert pipe._probe_in_flight is True
    assert seen == [b"\x00" * 320]


@pytest.mark.asyncio
async def test_vad_probe_is_noop_without_any_probe_stt() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._stt = None
    pipe._utterance_stt = None
    pipe._probe_stt = None
    pipe._probe_in_flight = False

    pipe._on_vad_probe(b"\x00" * 320)

    assert pipe._probe_in_flight is False
