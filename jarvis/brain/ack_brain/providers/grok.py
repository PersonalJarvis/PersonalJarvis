"""xAI Grok Flash provider adapter for the Pre-Thinking-Ack Flash-Brain.

Grok uses an OpenAI-compatible chat-completions surface on its own
endpoint. Same credential as the existing brain-side Grok plugin
(``grok_api_key`` in Windows Credential Manager).
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis.brain.ack_brain.config import GrokAckProviderConfig
from jarvis.core import config as cfg

log = logging.getLogger(__name__)

BASE_URL = "https://api.x.ai/v1"


class GrokFlashAck:
    """Single-shot Grok Flash adapter for the AckGenerator.

    Lazy auth: ``_ensure_client()`` resolves the key on first use.
    Uses the same ``openai.AsyncOpenAI`` pattern as
    jarvis.plugins.brain.grok with the xAI base URL.
    """

    def __init__(self, config: GrokAckProviderConfig) -> None:
        self._config = config
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            api_key = cfg.get_secret(
                self._config.api_key_secret, env_fallback="GROK_API_KEY"
            )
            if not api_key:
                raise RuntimeError(
                    f"Kein Grok-API-Key in keyring/env "
                    f"({self._config.api_key_secret} / GROK_API_KEY)."
                )
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=api_key, base_url=BASE_URL)
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
            response = await client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": persona_prompt},
                    {"role": "user", "content": utterance},
                ],
                temperature=self._config.temperature,
                max_tokens=self._config.max_output_tokens,
            )
            text = (response.choices[0].message.content or "").strip()
            return text if text else None
        except Exception as exc:
            log.warning("Grok Flash-Brain ack failed: %s", exc)
            return None
