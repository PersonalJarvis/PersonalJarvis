"""OpenAI Realtime provider plugin.

The module is structurally compatible with the realtime protocol but imports
no ``jarvis.*`` modules. Credentials and configuration are injected by the
orchestrator. The OpenAI SDK stays lazy and is imported only when a session is
opened, keeping the provider off the startup path (AP-26).
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

_MODEL = "gpt-realtime"
_INPUT_RATE = 24_000
_OUTPUT_RATE = 24_000
_HANDSHAKE_TIMEOUT_S = 12.0


@dataclass(frozen=True, slots=True)
class _PcmChunk:
    pcm: bytes
    sample_rate: int
    timestamp_ns: int = 0


@dataclass(frozen=True, slots=True)
class _ProviderEvent:
    type: str
    audio: _PcmChunk | None = None
    text: str | None = None
    is_final: bool = False
    ms_played: int | None = None
    error: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None


def _error_message(event: Any) -> str:
    error = getattr(event, "error", None)
    code = str(getattr(error, "code", "") or "").strip()
    message = str(getattr(error, "message", "") or "").strip()
    if code and message:
        return f"{code}: {message}"[:800]
    return (message or code or "OpenAI Realtime session error")[:800]


def _session_payload(cfg: Any) -> dict[str, Any]:
    """Build the current GA ``session.update`` payload.

    Audio output already includes a transcript side-channel, so the Realtime
    API accepts ``[\"audio\"]`` only; requesting text and audio together is
    invalid. PCM input and output are both explicitly declared as 24 kHz.
    """
    language = str(getattr(cfg, "language", "") or "").strip().lower()
    transcription: dict[str, Any] = {"model": "gpt-4o-mini-transcribe"}
    if language and language != "auto":
        transcription["language"] = language.split("-", 1)[0]

    turn_detection = str(getattr(cfg, "turn_detection", "server_vad") or "server_vad")
    if turn_detection not in {"server_vad", "semantic_vad"}:
        turn_detection = "server_vad"

    output: dict[str, Any] = {
        "format": {"type": "audio/pcm", "rate": _OUTPUT_RATE},
    }
    voice = str(getattr(cfg, "voice", "") or "").strip()
    if voice:
        output["voice"] = voice

    payload: dict[str, Any] = {
        "type": "realtime",
        "instructions": str(getattr(cfg, "instructions", "") or ""),
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": _INPUT_RATE},
                "transcription": transcription,
                "turn_detection": {
                    "type": turn_detection,
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": output,
        },
    }
    tools = tuple(getattr(cfg, "tools", ()) or ())
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": str(tool.get("name", "")),
                "description": str(tool.get("description", "")),
                "parameters": tool.get("parameters") or {"type": "object"},
            }
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        ]
        payload["tool_choice"] = "auto"
    return payload


class _OpenAIRealtimeSession:
    def __init__(
        self,
        *,
        connection: Any,
        connection_cm: Any,
        client: Any,
        session_id: str,
    ) -> None:
        self._conn = connection
        self._connection_cm = connection_cm
        self._client = client
        self._events = connection.__aiter__()
        self.session_id = session_id
        self._last_item_id = ""
        self._response_had_tool_calls = False
        self._tool_response_done_seen = False
        self._pending_tool_call_ids: set[str] = set()
        self._closed = False

    async def wait_until_ready(self) -> None:
        """Reject a connection unless the server confirms our effective schema."""

        async def _wait() -> None:
            while True:
                event = await anext(self._events)
                event_type = str(getattr(event, "type", "") or "")
                if event_type == "session.updated":
                    return
                if event_type == "error":
                    raise RuntimeError(_error_message(event))

        await asyncio.wait_for(_wait(), timeout=_HANDSHAKE_TIMEOUT_S)

    async def send_audio(self, chunk: Any) -> None:
        sample_rate = int(getattr(chunk, "sample_rate", 0) or 0)
        if sample_rate != _INPUT_RATE:
            raise ValueError(
                f"OpenAI Realtime requires {_INPUT_RATE} Hz PCM; received {sample_rate} Hz"
            )
        pcm = bytes(getattr(chunk, "pcm", b"") or b"")
        if not pcm:
            return
        await self._conn.input_audio_buffer.append(
            audio=base64.b64encode(pcm).decode("ascii")
        )

    async def receive(self) -> AsyncIterator[_ProviderEvent]:
        async for event in self._events:
            event_type = str(getattr(event, "type", "") or "")
            if event_type == "response.output_audio.delta":
                self._last_item_id = str(getattr(event, "item_id", "") or "")
                yield _ProviderEvent(
                    type="audio_delta",
                    audio=_PcmChunk(
                        pcm=base64.b64decode(getattr(event, "delta", "")),
                        sample_rate=_OUTPUT_RATE,
                    ),
                )
            elif event_type == "response.output_audio_transcript.delta":
                yield _ProviderEvent(
                    type="output_transcript_delta",
                    text=str(getattr(event, "delta", "") or ""),
                )
            elif event_type == "conversation.item.input_audio_transcription.completed":
                yield _ProviderEvent(
                    type="input_transcript",
                    text=str(getattr(event, "transcript", "") or ""),
                    is_final=True,
                )
            elif event_type == "input_audio_buffer.speech_started":
                yield _ProviderEvent(type="speech_started")
            elif event_type == "response.function_call_arguments.done":
                call_id = str(getattr(event, "call_id", "") or "")
                self._response_had_tool_calls = True
                if call_id:
                    self._pending_tool_call_ids.add(call_id)
                raw_arguments = str(getattr(event, "arguments", "") or "{}")
                try:
                    arguments = json.loads(raw_arguments)
                except (TypeError, ValueError):
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                yield _ProviderEvent(
                    type="tool_call",
                    call_id=call_id,
                    tool_name=str(getattr(event, "name", "") or ""),
                    tool_args=arguments,
                )
            elif event_type == "response.done":
                if self._response_had_tool_calls:
                    self._tool_response_done_seen = True
                    await self._continue_after_tools_if_ready()
                else:
                    yield _ProviderEvent(type="turn_complete")
            elif event_type == "error":
                yield _ProviderEvent(type="error", error=_error_message(event))

    async def update_session(
        self, *, instructions: str | None = None, language: str | None = None
    ) -> None:
        del language  # Input-transcription language is fixed for the live session.
        if instructions is not None:
            await self._conn.session.update(
                session={"type": "realtime", "instructions": instructions}
            )

    async def truncate(self, audio_end_ms: int) -> None:
        if self._last_item_id:
            await self._conn.conversation.item.truncate(
                item_id=self._last_item_id,
                content_index=0,
                audio_end_ms=max(0, int(audio_end_ms)),
            )

    async def interrupt(self) -> None:
        await self._conn.response.cancel()

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        del name
        await self._conn.conversation.item.create(
            item={
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result, ensure_ascii=False, default=str),
            }
        )
        self._pending_tool_call_ids.discard(call_id)
        await self._continue_after_tools_if_ready()

    async def _continue_after_tools_if_ready(self) -> None:
        if (
            not self._response_had_tool_calls
            or not self._tool_response_done_seen
            or self._pending_tool_call_ids
        ):
            return
        self._response_had_tool_calls = False
        self._tool_response_done_seen = False
        await self._conn.response.create()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._connection_cm.__aexit__(None, None, None)
        finally:
            close = getattr(self._client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result


class OpenAIRealtimeProvider:
    """Structural provider entry point for the OpenAI Realtime family."""

    name = "openai-realtime"
    supports_realtime = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    credential_candidates = (("openai_api_key", "OPENAI_API_KEY"),)

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = (api_key or "").strip()

    async def can_open_duplex_session(self) -> bool:
        return bool(self._api_key)

    async def open_session(self, cfg: Any) -> _OpenAIRealtimeSession:
        if not self._api_key:
            raise RuntimeError("OpenAI Realtime API key is not configured")

        from openai import AsyncOpenAI  # lazy (AP-26)

        client = AsyncOpenAI(api_key=self._api_key)
        connection_cm = client.realtime.connect(
            model=str(getattr(cfg, "model", "") or _MODEL)
        )
        connection = await connection_cm.__aenter__()
        session = _OpenAIRealtimeSession(
            connection=connection,
            connection_cm=connection_cm,
            client=client,
            session_id=str(uuid4()),
        )
        try:
            await connection.session.update(session=_session_payload(cfg))
            await session.wait_until_ready()
        except BaseException:
            await session.close()
            raise
        return session
