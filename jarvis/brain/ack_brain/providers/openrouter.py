"""OpenRouter provider adapter for the Pre-Thinking-Ack Flash-Brain.

OpenRouter is an OpenAI-compatible gateway, so this reuses the ``openai``
AsyncOpenAI client pointed at the OpenRouter base URL. It exists so a downloader
whose ONLY key is OpenRouter still gets a working Flash-Brain (ack preamble /
grounded spawn announcer) instead of the historical hardcoded fall-back to a
keyless Gemini, which silently failed for that user (§3 / AP-22 — no provider is
load-bearing; the flash tier must reach whatever key the user actually has).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from jarvis.brain.ack_brain.config import OpenRouterAckProviderConfig
from jarvis.core import config as cfg

log = logging.getLogger(__name__)

# OpenAI-compatible gateway endpoint. Shared with the main-brain OpenRouter
# plugin (``jarvis.plugins.brain.openrouter.BASE_URL``); an explicit
# ``[brain.providers.openrouter].base_url`` still overrides it via
# ``resolve_provider_endpoint``.
_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterFlashAck:
    """Single-shot OpenRouter adapter for the AckGenerator.

    Lazy auth: the key/endpoint are resolved on the first ``run()`` call via the
    same ``resolve_provider_endpoint("openrouter")`` path the main brain uses, so
    a per-provider ``base_url`` override and the ``openrouter_api_key`` slot are
    honoured identically.
    """

    def __init__(self, config: OpenRouterAckProviderConfig) -> None:
        self._config = config
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint(
                "openrouter", vendor_default_base_url=_BASE_URL
            )
            if not ep.credential:
                raise RuntimeError(
                    "No OpenRouter API key in keyring/env "
                    "(openrouter_api_key / OPENROUTER_API_KEY)."
                )
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=ep.credential,
                base_url=ep.base_url or _BASE_URL,
                default_headers={
                    "HTTP-Referer": "https://github.com/PersonalJarvis",
                    "X-Title": "Personal Jarvis",
                },
            )
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
        except Exception as exc:  # noqa: BLE001 — ack must never raise into the pipeline
            log.warning("OpenRouter Flash-Brain ack failed: %s", exc)
            return None

    async def run_stream(
        self,
        utterance: str,
        language: str,
        *,
        persona_prompt: str,
    ) -> AsyncIterator[str]:
        """Stream text deltas via the OpenAI-compatible streaming API (Wave 3).

        Yields nothing on any error — the AckGenerator then falls back to the
        non-streaming ``run()`` so a broken stream never silences the ack.
        """
        try:
            client = self._ensure_client()
            stream = await client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": persona_prompt},
                    {"role": "user", "content": utterance},
                ],
                temperature=self._config.temperature,
                max_tokens=self._config.max_output_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception as exc:  # noqa: BLE001
            log.warning("OpenRouter Flash-Brain ack stream failed: %s", exc)
            return
