"""Local Ollama provider adapter for the Pre-Thinking-Ack Flash-Brain.

No API key; talks HTTP to a local Ollama instance (default
http://localhost:11434). Used as an offline-friendly fallback or when
the user prefers all-local inference.

The HTTP client uses a short connect timeout (2s) so an unreachable
endpoint falls through to ``None`` quickly instead of stalling the
voice pipeline.
"""
from __future__ import annotations

import logging

from jarvis.brain.ack_brain.config import OllamaAckProviderConfig

log = logging.getLogger(__name__)


class OllamaFlashAck:
    """Single-shot Ollama adapter for the AckGenerator.

    Talks to /api/chat with ``stream=False``. Connect timeout is
    bounded to 2s so an offline Ollama doesn't block the voice path.
    """

    def __init__(self, config: OllamaAckProviderConfig) -> None:
        self._config = config

    async def run(
        self,
        utterance: str,
        language: str,
        *,
        persona_prompt: str,
    ) -> str | None:
        try:
            import httpx

            timeout = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self._config.endpoint.rstrip('/')}/api/chat",
                    json={
                        "model": self._config.model,
                        "messages": [
                            {"role": "system", "content": persona_prompt},
                            {"role": "user", "content": utterance},
                        ],
                        "stream": False,
                        "options": {
                            "temperature": self._config.temperature,
                            "num_predict": self._config.max_output_tokens,
                        },
                    },
                )
                response.raise_for_status()
                data = response.json()
                # /api/chat returns {"message": {"role": "assistant", "content": "..."}}
                text = (data.get("message", {}).get("content") or "").strip()
                return text if text else None
        except Exception as exc:
            log.warning("Ollama Flash-Brain ack failed: %s", exc)
            return None
