"""OpenAI mini-model provider adapter for the Pre-Thinking-Ack Flash-Brain.

Uses the official ``openai.AsyncOpenAI`` client and a small/fast model
(gpt-5-mini by default) for a single chat-completion call.
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis.brain.ack_brain.config import OpenAIAckProviderConfig
from jarvis.core import config as cfg

log = logging.getLogger(__name__)


class OpenAIMiniAck:
    """Single-shot OpenAI mini-model adapter for the AckGenerator.

    Lazy auth: key resolved on first ``run()`` call. Uses the standard
    openai-python AsyncOpenAI client; same model is reachable via the
    existing jarvis.plugins.brain.openai plugin.
    """

    def __init__(self, config: OpenAIAckProviderConfig) -> None:
        self._config = config
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            # Configured slot first, family resolver (incl. the Realtime
            # card's scoped key) as last resort — mirrors the Gemini adapter.
            api_key = cfg.get_secret(
                self._config.api_key_secret, env_fallback="OPENAI_API_KEY"
            ) or cfg.get_provider_secret("openai")
            if not api_key:
                raise RuntimeError(
                    f"No OpenAI API key in keyring/env "
                    f"({self._config.api_key_secret} / OPENAI_API_KEY)."
                )
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=api_key)
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
            log.warning("OpenAI Flash-Brain ack failed: %s", exc)
            return None
