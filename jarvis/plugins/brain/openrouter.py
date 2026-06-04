"""OpenRouter — Universal gateway for all top LLMs (OpenAI-compatible).

One API key → access to Claude, GPT, Gemini, Llama, Qwen, DeepSeek, and more.
Model names are namespaced ("anthropic/claude-opus-4.7", "openai/gpt-5").
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import stream_complete

DEFAULT_MODEL = "anthropic/claude-opus-4.8"
BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterBrain:
    name: str = "openrouter"
    context_window: int = 200_000
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            api_key = cfg.get_provider_secret("openrouter")
            if not api_key:
                raise RuntimeError("Kein OpenRouter-API-Key gefunden (openrouter_api_key).")
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=BASE_URL,
                default_headers={
                    "HTTP-Referer": "https://github.com/PersonalJarvis",
                    "X-Title": "Personal Jarvis",
                },
            )
        return self._client

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._ensure_client()
        async for delta in stream_complete(client, self._model, req):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        # OpenRouter costs are model-dependent. Conservative dummy estimator.
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 10 + req.max_tokens * 30) / 1_000_000
