"""Gemini Flash provider adapter for the Pre-Thinking-Ack Flash-Brain.

Wraps the google-genai async SDK for a single short generate-content
call. Mirrors the style of jarvis.plugins.brain.gemini but is one-shot
(non-streaming) and minimal — no tools, no caching, no vision.
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator
from typing import Any

from jarvis.brain.ack_brain.config import GeminiAckProviderConfig
from jarvis.core import config as cfg

log = logging.getLogger(__name__)


class GeminiFlashAck:
    """Single-shot Gemini Flash adapter for the AckGenerator.

    Lazy auth: the API key is resolved only inside ``run()``. The
    constructor accepts only the typed config sub-model so the
    REGISTRY can wire up adapters without touching credentials.
    """

    def __init__(self, config: GeminiAckProviderConfig) -> None:
        self._config = config
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            api_key = cfg.get_secret(
                self._config.api_key_secret, env_fallback="GEMINI_API_KEY"
            )
            if not api_key:
                raise RuntimeError(
                    f"Kein Gemini-API-Key in keyring/env "
                    f"({self._config.api_key_secret} / GEMINI_API_KEY)."
                )
            from google import genai
            self._client = genai.Client(api_key=api_key)
        return self._client

    async def run(
        self,
        utterance: str,
        language: str,
        *,
        persona_prompt: str,
    ) -> str | None:
        try:
            client = self._ensure_client()
            from google.genai import types as genai_types

            response = await client.aio.models.generate_content(
                model=self._config.model,
                contents=[utterance],
                config=genai_types.GenerateContentConfig(
                    system_instruction=persona_prompt,
                    temperature=self._config.temperature,
                    max_output_tokens=self._config.max_output_tokens,
                ),
            )
            text = (response.text or "").strip()
            return text if text else None
        except Exception as exc:
            log.warning("Gemini Flash-Brain ack failed: %s", exc)
            return None

    async def run_stream(
        self,
        utterance: str,
        language: str,
        *,
        persona_prompt: str,
    ) -> AsyncIterator[str]:
        """Stream text deltas via generate_content_stream (Wave 3).

        Yields nothing on any error — the AckGenerator then falls back to the
        non-streaming ``run()`` so a broken stream never silences the ack.
        """
        try:
            client = self._ensure_client()
            from google.genai import types as genai_types

            # The google-genai async API has shipped both an awaitable-returning
            # and a direct-async-iterator form of generate_content_stream;
            # handle both so an SDK bump does not silently break streaming.
            maybe = client.aio.models.generate_content_stream(
                model=self._config.model,
                contents=[utterance],
                config=genai_types.GenerateContentConfig(
                    system_instruction=persona_prompt,
                    temperature=self._config.temperature,
                    max_output_tokens=self._config.max_output_tokens,
                ),
            )
            stream = await maybe if inspect.isawaitable(maybe) else maybe
            async for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    yield text
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini Flash-Brain ack stream failed: %s", exc)
            return
