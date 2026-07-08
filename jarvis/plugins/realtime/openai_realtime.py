"""OpenAI GA realtime provider (gpt-realtime) for the jarvis.realtime group.

Uses the RELEASED interface AsyncOpenAI().realtime.connect(...) (NOT the removed
client.beta.realtime). The openai SDK import is lazy inside connect() (AP-26).
This module must not import jarvis.* beyond the config secret helper and the
protocol types (both stdlib-light, no heavy side effects).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.config import get_provider_secret
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent, RealtimeSessionConfig

_MODEL = "gpt-realtime"
_OUTPUT_RATE = 24000
_INPUT_RATE = 24000  # we upsample our 16 kHz mic to 24 kHz before append


class _OpenAIRealtimeSession:
    def __init__(self, conn: Any, cfg: RealtimeSessionConfig, session_id: str) -> None:
        self._conn = conn
        self._cfg = cfg
        self.session_id = session_id
        self._last_item_id = ""

    async def send_audio(self, chunk: AudioChunk) -> None:
        from jarvis.telephony.audio import resample_pcm16

        pcm = chunk.pcm
        if chunk.sample_rate != _INPUT_RATE:
            pcm = resample_pcm16(pcm, chunk.sample_rate, _INPUT_RATE)
        await self._conn.input_audio_buffer.append(audio=base64.b64encode(pcm).decode("ascii"))

    async def receive(self) -> AsyncIterator[RealtimeEvent]:
        async for event in self._conn:
            etype = getattr(event, "type", "")
            if etype == "response.output_audio.delta":
                pcm = base64.b64decode(event.delta)
                yield RealtimeEvent(
                    type="audio_delta",
                    audio=AudioChunk(pcm=pcm, sample_rate=_OUTPUT_RATE, timestamp_ns=0),
                )
            elif etype == "response.output_audio_transcript.delta":
                yield RealtimeEvent(type="output_transcript_delta", text=event.delta)
            elif etype == "conversation.item.input_audio_transcription.completed":
                yield RealtimeEvent(type="input_transcript", text=event.transcript, is_final=True)
            elif etype == "input_audio_buffer.speech_started":
                yield RealtimeEvent(type="speech_started")
            elif etype == "response.done":
                yield RealtimeEvent(type="turn_complete")
            elif etype == "error":
                yield RealtimeEvent(type="error", error=str(getattr(event, "error", event)))

    async def update_session(
        self, *, instructions: str | None = None, language: str | None = None
    ) -> None:
        payload: dict[str, Any] = {}
        if instructions is not None:
            payload["instructions"] = instructions
        if payload:
            await self._conn.session.update(session=payload)

    async def truncate(self, audio_end_ms: int) -> None:
        if self._last_item_id:
            await self._conn.conversation.item.truncate(
                item_id=self._last_item_id, content_index=0, audio_end_ms=audio_end_ms
            )

    async def interrupt(self) -> None:
        await self._conn.response.cancel()

    async def close(self) -> None:
        await self._conn.close()


class OpenAIRealtimeProvider:
    name = "openai-realtime"
    supports_realtime = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE

    async def can_open_duplex_session(self) -> bool:
        return bool(get_provider_secret("openai"))

    async def open_session(self, cfg: RealtimeSessionConfig) -> _OpenAIRealtimeSession:
        from openai import AsyncOpenAI  # lazy (AP-26)

        client = AsyncOpenAI(api_key=get_provider_secret("openai"))
        conn = await client.realtime.connect(model=cfg.model or _MODEL).__aenter__()
        session_payload: dict[str, Any] = {
            "instructions": cfg.instructions,
            "output_modalities": list(cfg.modalities),
            "audio": {
                "input": {
                    # Declare the rate we ACTUALLY send: send_audio upsamples the
                    # mic PCM to _INPUT_RATE (24 kHz) before input_audio_buffer.append,
                    # so the wire format must be 24 kHz — not cfg.input_sample_rate
                    # (the 16 kHz mic rate), or the server mis-times the samples.
                    "format": {"type": "audio/pcm", "rate": _INPUT_RATE},
                    "turn_detection": {"type": cfg.turn_detection},
                },
                "output": {
                    "format": {"type": "audio/pcm"},
                    **({"voice": cfg.voice} if cfg.voice else {}),
                },
            },
        }
        # The openai SDK's session.update() TypedDict shape is stricter than the
        # GA over-the-wire schema we build here; a plain dict is what the API
        # actually accepts.
        await conn.session.update(session=session_payload)  # type: ignore[arg-type]
        import uuid

        return _OpenAIRealtimeSession(conn, cfg, session_id=str(uuid.uuid4()))
