"""Gemini Live provider plugin using the Google Gen AI SDK.

The module imports no ``jarvis.*`` modules. Credentials and configuration are
injected by the realtime orchestrator, and the Google SDK remains a lazy import
inside the live methods (AP-26). Gemini Live consumes raw 16-bit little-endian
mono PCM at 16 kHz and emits the same format at 24 kHz.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

_MODEL = "gemini-3.1-flash-live-preview"
_INPUT_RATE = 16_000
_OUTPUT_RATE = 24_000


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


class _GeminiLiveSession:
    supports_tool_updates = False
    creates_responses_automatically = True
    # The ordered Live stream emits old output before ``interrupted`` and the
    # next input transcript. Output observed after that boundary belongs to
    # the new automatic response generation.
    isolates_response_generations = True

    def __init__(
        self,
        *,
        session: Any,
        connection_cm: Any,
        client: Any,
        session_id: str,
    ) -> None:
        self._session = session
        self._connection_cm = connection_cm
        self._client = client
        self.session_id = session_id
        self._closed = False

    async def send_audio(self, chunk: Any) -> None:
        from google.genai import types  # lazy (AP-26)

        sample_rate = int(getattr(chunk, "sample_rate", 0) or 0)
        if sample_rate != _INPUT_RATE:
            raise ValueError(
                f"Gemini Live requires {_INPUT_RATE} Hz PCM; received {sample_rate} Hz"
            )
        pcm = bytes(getattr(chunk, "pcm", b"") or b"")
        if not pcm:
            return
        await self._session.send_realtime_input(
            audio=types.Blob(
                data=pcm,
                mime_type=f"audio/pcm;rate={_INPUT_RATE}",
            )
        )

    async def receive(self) -> AsyncIterator[_ProviderEvent]:
        # ``google.genai.live.AsyncSession.receive()`` intentionally ends after
        # one model turn. The Jarvis provider contract spans the whole call, so
        # re-enter the SDK iterator after every clean turn boundary instead of
        # making the desktop supervisor mistake a completed answer for a dead
        # provider session.
        while not self._closed:
            turn_boundary_seen = False
            async for message in self._session.receive():
                # ``LiveServerMessage.data`` concatenates every inline audio part,
                # including Gemini 3.1 events that carry multiple parts at once.
                data = getattr(message, "data", None)
                if data:
                    yield _ProviderEvent(
                        type="audio_delta",
                        audio=_PcmChunk(pcm=bytes(data), sample_rate=_OUTPUT_RATE),
                    )

                tool_call = getattr(message, "tool_call", None)
                function_calls = tuple(
                    getattr(tool_call, "function_calls", None) or ()
                )
                for function_call in function_calls:
                    raw_args = getattr(function_call, "args", None) or {}
                    if hasattr(raw_args, "model_dump"):
                        raw_args = raw_args.model_dump()
                    try:
                        args = dict(raw_args)
                    except (TypeError, ValueError):
                        args = {}
                    yield _ProviderEvent(
                        type="tool_call",
                        call_id=str(getattr(function_call, "id", "") or ""),
                        tool_name=str(getattr(function_call, "name", "") or ""),
                        tool_args=args,
                    )

                content = getattr(message, "server_content", None)
                if content is not None:
                    output_transcription = getattr(
                        content, "output_transcription", None
                    )
                    output_text = str(
                        getattr(output_transcription, "text", "") or ""
                    )
                    if output_text:
                        yield _ProviderEvent(
                            type="output_transcript_delta", text=output_text
                        )

                    # The interrupted flag is the boundary between the partial
                    # assistant reply above and any new user transcript carried by
                    # the same server-content message. Emit it first so the shared
                    # session closes the old turn before adopting the new words.
                    if bool(getattr(content, "interrupted", False)):
                        yield _ProviderEvent(type="interrupted")

                    input_transcription = getattr(
                        content, "input_transcription", None
                    )
                    input_text = str(
                        getattr(input_transcription, "text", "") or ""
                    )
                    if input_text:
                        yield _ProviderEvent(
                            type="input_transcript", text=input_text, is_final=True
                        )

                    if bool(getattr(content, "turn_complete", False)):
                        turn_boundary_seen = True
                        if not function_calls:
                            yield _ProviderEvent(type="turn_complete")

                go_away = getattr(message, "go_away", None)
                if go_away is not None:
                    retry_ms = getattr(go_away, "time_left", None)
                    suffix = (
                        f" (time_left={retry_ms})" if retry_ms is not None else ""
                    )
                    yield _ProviderEvent(
                        type="error", error=f"Gemini Live requested reconnect{suffix}"
                    )

            # An iterator that vanishes without a model-turn boundary signals a
            # closed/broken transport. Let the shared session observe that end;
            # retrying it here would spin on an empty iterator forever.
            if not turn_boundary_seen:
                return

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> None:
        # Gemini fixes system instructions at connect time. The orchestrator
        # reconnects on a substantive language change in a later session.
        del instructions, language, tools

    async def request_response(self, *, required_tool: str | None = None) -> None:
        # Gemini Live creates a response automatically at the VAD turn boundary.
        del required_tool
        return None

    async def send_text(self, text: str) -> None:
        """Send an incremental text turn through the current Gemini 3.1 API."""
        # Gemini 3.1 permits send_client_content only for initial history.
        # Runtime text updates must use the realtime-input text stream.
        await self._session.send_realtime_input(text=str(text))

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms  # Gemini interrupts generation when new audio arrives.

    async def interrupt(self) -> None:
        # The Live API has no separate response-cancel call for this flow.
        return None

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        from google.genai import types  # lazy (AP-26)

        await self._session.send_tool_response(
            function_responses=[
                types.FunctionResponse(
                    id=call_id,
                    name=name,
                    response=result,
                )
            ]
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._connection_cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            log.debug("gemini-live: session close raised", exc_info=True)
        finally:
            close = getattr(self._client, "close", None)
            if close is not None:
                try:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
                except Exception:  # noqa: BLE001
                    log.debug("gemini-live: client close raised", exc_info=True)


# Gemini function_declarations accept only an OpenAPI-style schema subset.
# Standard JSON-schema keys like additionalProperties, $ref/$defs, or
# oneOf/anyOf/allOf make the handshake fail — which silently drops the whole
# provider to the fallback family. Sanitizing is this adapter's wire-format
# translation; the bridge declarations (and the OpenAI path) keep the full
# schema.
_GEMINI_SCHEMA_KEYS = frozenset(
    {
        "type",
        "description",
        "enum",
        "properties",
        "required",
        "items",
        "nullable",
        "minimum",
        "maximum",
        "default",
    }
)


def _sanitize_schema_for_gemini(schema: Any, *, tool_name: str = "") -> Any:
    if not isinstance(schema, dict):
        return schema
    sanitized: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            # Drop unsupported keys but keep their siblings: the tool stays
            # usable with a permissive schema instead of bricking the session.
            log.debug(
                "gemini-live: dropping unsupported schema key %r (tool=%s)",
                key,
                tool_name or "unknown",
            )
            continue
        if key == "properties" and isinstance(value, dict):
            sanitized[key] = {
                name: _sanitize_schema_for_gemini(sub, tool_name=tool_name)
                for name, sub in value.items()
            }
        elif key == "items":
            sanitized[key] = _sanitize_schema_for_gemini(value, tool_name=tool_name)
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_declarations(tools: tuple[Any, ...]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for declaration in tools:
        if not isinstance(declaration, dict):
            continue
        entry = dict(declaration)
        name = str(entry.get("name", "") or "")
        if isinstance(entry.get("parameters"), dict):
            entry["parameters"] = _sanitize_schema_for_gemini(
                entry["parameters"], tool_name=name
            )
        sanitized.append(entry)
    return sanitized


class GeminiLiveProvider:
    """Structural provider entry point for the Gemini Live family."""

    name = "gemini-live"
    supports_realtime = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    credential_candidates = (
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("google_aistudio_api_key", "GOOGLE_AIStudio_API_KEY"),
        ("google_api_key", "GOOGLE_API_KEY"),
    )

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = (api_key or "").strip()

    async def can_open_duplex_session(self) -> bool:
        return bool(self._api_key)

    async def open_session(self, cfg: Any) -> _GeminiLiveSession:
        if not self._api_key:
            raise RuntimeError("Gemini Live API key is not configured")

        from google import genai  # lazy (AP-26)
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        voice = str(getattr(cfg, "voice", "") or "").strip()
        speech_config: dict[str, Any] = {}
        if voice:
            speech_config["voice_config"] = types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice
                )
            )
        live_config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=str(getattr(cfg, "instructions", "") or "") or None,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    silence_duration_ms=int(
                        getattr(cfg, "silence_duration_ms", 1_500) or 1_500
                    ),
                )
            ),
            **(
                {
                    "tools": [
                        {
                            "function_declarations": _sanitize_declarations(
                                tuple(getattr(cfg, "tools", ()) or ())
                            )
                        }
                    ]
                }
                if tuple(getattr(cfg, "tools", ()) or ())
                else {}
            ),
            **(
                {"speech_config": types.SpeechConfig(**speech_config)}
                if speech_config
                else {}
            ),
        )
        connection_cm = client.aio.live.connect(
            model=str(getattr(cfg, "model", "") or _MODEL),
            config=live_config,
        )
        try:
            session = await connection_cm.__aenter__()
        except BaseException:
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            raise
        return _GeminiLiveSession(
            session=session,
            connection_cm=connection_cm,
            client=client,
            session_id=str(uuid4()),
        )
