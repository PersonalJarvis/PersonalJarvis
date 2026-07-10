"""Contracts for the realtime (full-duplex speech-to-speech) plugin group.

A realtime provider fuses STT + reasoning + TTS + VAD into one stateful
WebSocket session. None of the Brain/STT/TTS protocols can express this, so this
is its own ``jarvis.realtime`` group. Provider modules live under
``jarvis/plugins/realtime/`` and MUST NOT import ``jarvis.*`` at module import.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from jarvis.core.protocols import AudioChunk

RealtimeEventType = Literal[
    "audio_delta",
    "output_transcript_delta",
    "input_transcript",
    "tool_call",
    "speech_started",
    "interrupted",
    "turn_complete",
    "error",
]


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    """One normalized, provider-neutral event from a duplex session."""

    type: RealtimeEventType
    audio: AudioChunk | None = None          # audio_delta
    text: str | None = None                  # output_transcript_delta / input_transcript
    is_final: bool = False
    ms_played: int | None = None             # speech_started: ms of our audio already heard
    error: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RealtimeSessionConfig:
    """Everything a provider needs to open one duplex session."""

    instructions: str = ""
    language: str = "en"                     # bare de/en/es (resolved once, upstream)
    model: str = ""                          # provider model id ("" -> the adapter's
                                              # hardcoded default; no regression)
    voice: str = ""
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    # Native audio responses already carry a transcript side-channel. OpenAI's
    # GA Realtime schema rejects requesting text and audio simultaneously.
    modalities: tuple[str, ...] = ("audio",)
    turn_detection: str = "server_vad"       # "server_vad" | "semantic_vad"
    tools: tuple[dict[str, Any], ...] = ()


@runtime_checkable
class RealtimeSession(Protocol):
    """A live duplex handle (one connection)."""

    session_id: str

    async def send_audio(self, chunk: AudioChunk) -> None: ...
    def receive(self) -> AsyncIterator[RealtimeEvent]: ...

    async def update_session(
        self, *, instructions: str | None = None, language: str | None = None
    ) -> None: ...

    async def truncate(self, audio_end_ms: int) -> None: ...
    async def interrupt(self) -> None: ...
    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class RealtimeProvider(Protocol):
    """The plugin entry-point class."""

    name: str
    supports_realtime: bool
    input_sample_rate: int
    output_sample_rate: int
    credential_candidates: tuple[tuple[str, str | None], ...]

    async def can_open_duplex_session(self) -> bool: ...
    async def open_session(self, cfg: RealtimeSessionConfig) -> RealtimeSession: ...
