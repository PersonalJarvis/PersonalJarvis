"""OpenAI GPT-Brain (direct API)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import stream_complete

DEFAULT_MODEL = "gpt-5.5"


class OpenAIBrain:
    name: str = "openai"
    context_window: int = 128_000
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            api_key = cfg.get_provider_secret("openai")
            if not api_key:
                raise RuntimeError("Kein OpenAI-API-Key gefunden (openai_api_key / OPENAI_API_KEY).")
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=api_key)
        return self._client

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._ensure_client()
        async for delta in stream_complete(client, self._model, req):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 5 + req.max_tokens * 15) / 1_000_000
