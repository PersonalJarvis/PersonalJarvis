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
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None


class _GeminiLiveSession:
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
        async for message in self._session.receive():
            # ``LiveServerMessage.data`` concatenates every inline audio part,
            # including Gemini 3.1 events that carry multiple parts at once.
            data = getattr(message, "data", None)
            if data:
                yield _ProviderEvent(
                    type="audio_delta",
                    audio=_PcmChunk(pcm=bytes(data), sample_rate=_OUTPUT_RATE),
                )

            content = getattr(message, "server_content", None)
            if content is not None:
                output_transcription = getattr(content, "output_transcription", None)
                output_text = str(getattr(output_transcription, "text", "") or "")
                if output_text:
                    yield _ProviderEvent(
                        type="output_transcript_delta", text=output_text
                    )

                input_transcription = getattr(content, "input_transcription", None)
                input_text = str(getattr(input_transcription, "text", "") or "")
                if input_text:
                    yield _ProviderEvent(
                        type="input_transcript", text=input_text, is_final=True
                    )

                if bool(getattr(content, "interrupted", False)):
                    yield _ProviderEvent(type="interrupted")
                if bool(getattr(content, "turn_complete", False)):
                    yield _ProviderEvent(type="turn_complete")

            tool_call = getattr(message, "tool_call", None)
            for function_call in getattr(tool_call, "function_calls", None) or ():
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

            go_away = getattr(message, "go_away", None)
            if go_away is not None:
                retry_ms = getattr(go_away, "time_left", None)
                suffix = f" (time_left={retry_ms})" if retry_ms is not None else ""
                yield _ProviderEvent(
                    type="error", error=f"Gemini Live requested reconnect{suffix}"
                )

    async def update_session(
        self, *, instructions: str | None = None, language: str | None = None
    ) -> None:
        # Gemini fixes system instructions at connect time. The orchestrator
        # reconnects on a substantive language change in a later session.
        del instructions, language

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
        live_config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=str(getattr(cfg, "instructions", "") or "") or None,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            **(
                {
                    "tools": [
                        {
                            "function_declarations": list(
                                tuple(getattr(cfg, "tools", ()) or ())
                            )
                        }
                    ]
                }
                if tuple(getattr(cfg, "tools", ()) or ())
                else {}
            ),
            **(
                {
                    "speech_config": types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice
                            )
                        )
                    )
                }
                if voice
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
