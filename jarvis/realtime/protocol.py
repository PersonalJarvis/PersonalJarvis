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
    # A recoverable provider event reports a rejected operation while the
    # duplex transport remains usable. It must not end voice ownership or
    # trigger the classic pipeline.
    recoverable: bool = False
    item_id: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RealtimeSessionConfig:
    """Everything a provider needs to open one duplex session."""

    instructions: str = ""
    language: str = "en"                     # output: bare de/en/es, resolved upstream
    input_language: str = "auto"              # recognition: auto or bare de/en/es
    language_is_pinned: bool = False          # explicit reply-language preference
    model: str = ""                          # provider model id ("" -> the adapter's
                                              # hardcoded default; no regression)
    voice: str = ""
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    # Native audio responses already carry a transcript side-channel. OpenAI's
    # GA Realtime schema rejects requesting text and audio simultaneously.
    modalities: tuple[str, ...] = ("audio",)
    turn_detection: str = "server_vad"       # "server_vad" | "semantic_vad"
    # None (the default) = the provider's NATIVE turn detection decides when
    # the user's turn ends. The Settings "Thinking pause"
    # (SpeechConfig.vad_silence_ms) endpoints the classic pipeline only —
    # forcing it into realtime sessions made the realtime model wait the full
    # window after every utterance, which reads as "done speaking but still
    # listening" (maintainer directive 2026-07-21). An explicit int still
    # overrides the provider default for callers that need a fixed window.
    silence_duration_ms: int | None = None
    tools: tuple[dict[str, Any], ...] = ()
    # Bounded transcript of the call so far, oldest first, as
    # ``{"role": "user" | "assistant", "text": ...}`` mappings. A fresh
    # transport opened MID-CALL (in-place rebuild after a provider disconnect,
    # or a cross-family fallback) starts with an empty server-side
    # conversation; seeding this history restores the context the model
    # needs to understand follow-up turns (BUG-088). Empty at the first open
    # of a call. Providers that cannot inject history ignore it.
    history: tuple[dict[str, str], ...] = ()


@runtime_checkable
class RealtimeSession(Protocol):
    """A live duplex handle (one connection).

    Optional capability (probed with ``getattr``, never required): a session
    that can seed conversation history into a rebuilt transport may expose
    ``set_history_snapshot(history: tuple[dict[str, str], ...]) -> None``.
    The orchestrator calls it with the current bounded call transcript after
    every completed turn so a provider-internal transport rebuild (e.g. the
    openai_realtime BUG-064 stack) can restore context without a wire call.

    A former optional ``renders_pinned_voice`` voice-identity capability
    (BUG-086 escalation) was removed 2026-07-21: routing delegate replies
    to the surface TTS produced an audibly different voice on every
    tool-model turn. Delegate replies render natively; the surface TTS is
    only the provider-mute fallback.
    """

    session_id: str
    creates_responses_automatically: bool
    isolates_response_generations: bool

    async def send_audio(self, chunk: AudioChunk) -> None: ...
    def receive(self) -> AsyncIterator[RealtimeEvent]: ...

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> None: ...

    async def request_response(self, *, required_tool: str | None = None) -> None: ...
    async def send_text(self, text: str) -> None: ...
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
