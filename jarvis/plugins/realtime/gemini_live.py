"""Gemini Live realtime provider (google-genai Live API) for jarvis.realtime.

Structural mirror of ``openai_realtime.py``: same class shape, same lazy-import
discipline (AP-26 — ``from google import genai`` / ``from google.genai import
types`` only inside methods, never at module top), same ``RealtimeEvent``
mapping style. This module must not import ``jarvis.*`` beyond the config
secret helper, ``jarvis.core.protocols.AudioChunk`` and
``jarvis.realtime.protocol`` (both stdlib-light, no heavy side effects).

Live audio is 16 kHz PCM in / 24 kHz PCM out. The mic is already captured at
16 kHz, so — unlike the OpenAI adapter, which upsamples 16 kHz -> 24 kHz
before ``input_audio_buffer.append`` — no resample happens here.

Verified against the INSTALLED SDK (google-genai==2.9.0, checked 2026-07-08):
- ``genai.Client(api_key=...).aio.live.connect(model=..., config=...)`` is an
  async context manager (``AsyncIterator[AsyncSession]``); call it to get the
  cm, then ``await cm.__aenter__()`` / ``await cm.__aexit__(None, None,
  None)`` (mirrors the plan's usage; ``genai.live.AsyncLive.connect``).
- ``AsyncSession`` exposes: ``close``, ``receive``, ``send``,
  ``send_client_content``, ``send_realtime_input``, ``send_tool_response``,
  ``start_stream``. Audio in goes through
  ``send_realtime_input(audio=types.Blob(data=..., mime_type=...))``
  (``Blob`` fields: ``data``, ``display_name``, ``mime_type``).
- ``receive()`` yields ``types.LiveServerMessage``, which has a ``.data``
  *property* (not a model field — concatenates inline audio parts from
  ``server_content.model_turn``, ``None`` when there is none) plus a
  ``server_content`` field of type ``LiveServerContent`` with fields
  ``input_transcription``, ``output_transcription``, ``interrupted``,
  ``turn_complete`` (both transcription fields are ``Transcription`` objects
  with a ``.text`` field) — exactly the shape this module maps below.
- ``types.LiveConnectConfig`` has ``response_modalities``,
  ``system_instruction``, ``input_audio_transcription`` and
  ``output_audio_transcription`` (both ``AudioTranscriptionConfig``) among
  its fields — transcripts are OFF by default and MUST be requested via the
  latter two, or ``server_content`` never carries a transcription.

Model id: ``gemini-3.1-flash-live-preview``, confirmed live on
https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk (fetched
2026-07-08) as the model used in that page's ``client.aio.live.connect()``
Python/JS examples — i.e. reachable with a plain Google AI Studio API key
(no Vertex AI service account needed). The older ``gemini-2.0-flash-live-001``
id from the initial task brief no longer appears in current docs; use the
verified id above. Re-verify if AI Studio deprecates this preview model.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.config import get_provider_secret
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent, RealtimeSessionConfig

log = logging.getLogger(__name__)

_MODEL = "gemini-3.1-flash-live-preview"
_INPUT_RATE = 16000  # mic is already 16 kHz -- no resample before send
_OUTPUT_RATE = 24000


class _GeminiLiveSession:
    def __init__(self, session: Any, cm: Any, cfg: RealtimeSessionConfig, session_id: str) -> None:
        self._session = session  # the live AsyncSession
        self._cm = cm  # the async context manager `connect()` returned, for close()
        self._cfg = cfg
        self.session_id = session_id

    async def send_audio(self, chunk: AudioChunk) -> None:
        from google.genai import types  # lazy (AP-26)

        pcm = chunk.pcm  # mic is 16 kHz == _INPUT_RATE; no resample needed
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={_INPUT_RATE}")
        )

    async def receive(self) -> AsyncIterator[RealtimeEvent]:
        async for msg in self._session.receive():
            data = getattr(msg, "data", None)
            if data:
                yield RealtimeEvent(
                    type="audio_delta",
                    audio=AudioChunk(pcm=data, sample_rate=_OUTPUT_RATE, timestamp_ns=0),
                )
            sc = getattr(msg, "server_content", None)
            if sc is not None:
                ot = getattr(sc, "output_transcription", None)
                if ot and getattr(ot, "text", None):
                    yield RealtimeEvent(type="output_transcript_delta", text=ot.text)
                it = getattr(sc, "input_transcription", None)
                if it and getattr(it, "text", None):
                    yield RealtimeEvent(type="input_transcript", text=it.text, is_final=True)
                if getattr(sc, "interrupted", False):
                    yield RealtimeEvent(type="speech_started")
                if getattr(sc, "turn_complete", False):
                    yield RealtimeEvent(type="turn_complete")

    async def update_session(
        self, *, instructions: str | None = None, language: str | None = None
    ) -> None:
        # Gemini Live sets system_instruction only at connect time; there is no
        # mid-session update call on AsyncSession. An honest no-op.
        return None

    async def truncate(self, audio_end_ms: int) -> None:
        return None  # server-side context trim not exposed; barge-in handled by send flow

    async def interrupt(self) -> None:
        return None  # interruption is driven by new input audio (server VAD)

    async def close(self) -> None:
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            log.debug("gemini-live: session close raised, ignoring", exc_info=True)


class GeminiLiveProvider:
    name = "gemini-live"
    supports_realtime = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE

    async def can_open_duplex_session(self) -> bool:
        return bool(get_provider_secret("gemini"))

    async def open_session(self, cfg: RealtimeSessionConfig) -> _GeminiLiveSession:
        from google import genai  # lazy (AP-26)
        from google.genai import types

        client = genai.Client(api_key=get_provider_secret("gemini"))
        live_config = types.LiveConnectConfig(
            # types.Modality is a str-Enum ("AUDIO" == Modality.AUDIO at runtime);
            # the enum member (not a bare string) satisfies the SDK's static type.
            response_modalities=[types.Modality.AUDIO],
            system_instruction=cfg.instructions or None,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        cm = client.aio.live.connect(model=_MODEL, config=live_config)
        session = await cm.__aenter__()
        import uuid

        return _GeminiLiveSession(session, cm, cfg, session_id=str(uuid.uuid4()))
