"""xAI Grok Voice Agent realtime provider plugin.

xAI documents its Voice Agent API as OpenAI-Realtime compatible.  This adapter
therefore reuses the hardened transport lifecycle from the OpenAI plugin while
injecting xAI's endpoint, credential family, model, and transcription schema.
The OpenAI SDK remains lazy so importing the plugin never slows application
startup (AP-26).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from .openai_realtime import _OpenAIRealtimeSession, _session_payload

log = logging.getLogger(__name__)

_MODEL = "grok-voice-latest"
_BASE_URL = "https://api.x.ai/v1"
_INPUT_RATE = 24_000
_OUTPUT_RATE = 24_000


def _grok_session_payload(cfg: Any) -> dict[str, Any]:
    """Return xAI's OpenAI-compatible realtime session configuration."""
    payload = _session_payload(cfg)
    payload["audio"]["input"]["transcription"]["model"] = "grok-transcribe"
    return payload


class GrokRealtimeProvider:
    """Structural provider entry point for xAI's Grok Voice Agent API."""

    name = "grok-realtime"
    supports_realtime = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    credential_candidates = (
        ("realtime_grok_api_key", "JARVIS_REALTIME_GROK_API_KEY"),
        ("grok_api_key", "GROK_API_KEY"),
        ("xai_api_key", "XAI_API_KEY"),
    )

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = (api_key or "").strip()

    async def can_open_duplex_session(self) -> bool:
        return bool(self._api_key)

    async def open_session(self, cfg: Any) -> _OpenAIRealtimeSession:
        if not self._api_key:
            raise RuntimeError("xAI Grok Realtime API key is not configured")

        from openai import AsyncOpenAI  # lazy (AP-26)

        client = AsyncOpenAI(api_key=self._api_key, base_url=_BASE_URL)
        connect_model = str(getattr(cfg, "model", "") or _MODEL)
        connection_cm = client.realtime.connect(model=connect_model)
        try:
            connection = await connection_cm.__aenter__()
        except BaseException as exc:
            try:
                await connection_cm.__aexit__(type(exc), exc, exc.__traceback__)
            except BaseException:  # noqa: BLE001 - preserve the handshake error
                log.debug(
                    "xAI Grok Realtime connection cleanup after failed enter failed",
                    exc_info=True,
                )
            try:
                close = getattr(client, "close", None)
                if close is not None:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
            except BaseException:  # noqa: BLE001 - preserve the handshake error
                log.debug(
                    "xAI Grok Realtime client cleanup after failed enter failed",
                    exc_info=True,
                )
            raise

        payload = _grok_session_payload(cfg)
        session = _OpenAIRealtimeSession(
            connection=connection,
            connection_cm=connection_cm,
            client=client,
            session_id=str(uuid4()),
            session_payload=payload,
            connect_model=connect_model,
        )
        try:
            await connection.session.update(session=payload)
            await session.wait_until_ready()
        except BaseException:
            await session.close()
            raise
        return session
