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
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

_MODEL = "gpt-realtime"
_INPUT_RATE = 24_000
_OUTPUT_RATE = 24_000
_HANDSHAKE_TIMEOUT_S = 12.0
_RESPONSE_REQUEST_METADATA_KEY = "jarvis_request_id"


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
    item_id: str | None = None
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
    transcription: dict[str, Any] = {"model": "gpt-4o-mini-transcribe"}

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
                    # Jarvis requests the response only after the final input
                    # transcript has passed the single turn-language resolver.
                    "create_response": False,
                    # Jarvis also owns barge-in explicitly. Keeping both flags
                    # false is OpenAI's documented manual-response VAD mode.
                    "interrupt_response": False,
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
        # Every client response.create carries a unique marker. Only the first
        # response.created lifecycle that consumes one pending marker may emit
        # audio. This is a transport boundary, not transcript-text deduplication:
        # a provider-side duplicate or unsolicited response is cancelled before
        # any PCM reaches the speaker.
        self._pending_response_markers: set[str] = set()
        self._accepted_response_ids: set[str] = set()
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
            if event_type == "response.created":
                await self._handle_response_created(event)
                continue
            if event_type.startswith("response.") and not self._response_is_accepted(
                event
            ):
                continue
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
                    item_id=str(getattr(event, "item_id", "") or "") or None,
                )
            elif event_type == "conversation.item.input_audio_transcription.failed":
                # The model still has the committed audio in conversation
                # context. Let the orchestrator fail open to a spoken response,
                # while withholding tools because no auditable text exists.
                yield _ProviderEvent(
                    type="input_transcript",
                    text="",
                    is_final=True,
                    error=_error_message(event),
                    item_id=str(getattr(event, "item_id", "") or "") or None,
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
                response_id = self._event_response_id(event)
                if response_id:
                    self._accepted_response_ids.discard(response_id)
                elif len(self._accepted_response_ids) == 1:
                    self._accepted_response_ids.pop()
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
        del language  # Input transcription stays provider-inferred and multilingual.
        if instructions is not None:
            await self._conn.session.update(
                session={"type": "realtime", "instructions": instructions}
            )

    async def request_response(self) -> None:
        await self._create_response()

    async def send_text(self, text: str) -> None:
        """Add one trusted text turn and ask the live model for audio output."""
        await self._conn.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": str(text)}],
            }
        )
        await self._create_response(tool_choice="none")

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
        await self._create_response()

    async def _create_response(self, *, tool_choice: str | None = None) -> None:
        marker = uuid4().hex
        self._pending_response_markers.add(marker)
        response: dict[str, Any] = {
            "metadata": {_RESPONSE_REQUEST_METADATA_KEY: marker},
        }
        if tool_choice is not None:
            response["tool_choice"] = tool_choice
        try:
            await self._conn.response.create(response=response)
        except BaseException:
            self._pending_response_markers.discard(marker)
            raise

    @staticmethod
    def _event_response_id(event: Any) -> str:
        direct = str(getattr(event, "response_id", "") or "")
        if direct:
            return direct
        response = getattr(event, "response", None)
        return str(getattr(response, "id", "") or "")

    async def _handle_response_created(self, event: Any) -> None:
        response = getattr(event, "response", None)
        response_id = self._event_response_id(event)
        if response_id and response_id in self._accepted_response_ids:
            return

        metadata = getattr(response, "metadata", None) or {}
        if hasattr(metadata, "model_dump"):
            metadata = metadata.model_dump()
        marker = (
            str(metadata.get(_RESPONSE_REQUEST_METADATA_KEY, "") or "")
            if isinstance(metadata, dict)
            else ""
        )

        if marker and marker in self._pending_response_markers:
            self._pending_response_markers.discard(marker)
        elif not marker and self._pending_response_markers:
            # Compatibility for a server/SDK that omits echoed metadata. The
            # pending allowance is still consumed, preserving the exactly-one
            # response invariant even if an automatic response races our own.
            self._pending_response_markers.pop()
            log.warning(
                "OpenAI Realtime response.created omitted Jarvis request metadata; "
                "accepted one pending response by lifecycle order"
            )
        else:
            log.warning(
                "OpenAI Realtime suppressed unsolicited response %s",
                response_id or "<unknown>",
            )
            if response_id:
                try:
                    await self._conn.response.cancel(response_id=response_id)
                except Exception:  # noqa: BLE001 -- suppression remains fail-closed
                    log.debug(
                        "OpenAI Realtime unsolicited response cancel failed",
                        exc_info=True,
                    )
            return

        if not response_id:
            log.warning(
                "OpenAI Realtime response.created had no response id; "
                "response events remain suppressed"
            )
            return
        self._accepted_response_ids.add(response_id)

    def _response_is_accepted(self, event: Any) -> bool:
        response_id = self._event_response_id(event)
        if response_id:
            return response_id in self._accepted_response_ids
        # Current GA response events carry response_id (or response.id for
        # response.done). This fallback keeps older SDK event shapes usable
        # only when their lifecycle is otherwise unambiguous.
        return len(self._accepted_response_ids) == 1

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending_response_markers.clear()
        self._accepted_response_ids.clear()
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
