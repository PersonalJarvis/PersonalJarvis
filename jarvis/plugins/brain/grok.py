"""xAI Grok brain over its OpenAI-compatible API."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import CLIENT_TIMEOUT, stream_complete

BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.3"


class GrokBrain:
    """Tool-capable Grok brain backed by the user's xAI API key."""

    name: str = "grok"
    context_window: int = 1_000_000
    supports_tools: bool = True
    # The OpenAI-compatible image path has not been verified end to end yet.
    supports_vision: bool = False

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def can_call_tools(self) -> bool:
        return self.supports_tools

    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint(
                "grok", vendor_default_base_url=BASE_URL
            )
            if not ep.credential:
                raise RuntimeError(
                    "No xAI API key found "
                    "(grok_api_key / xai_api_key / GROK_API_KEY / XAI_API_KEY)."
                )
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=ep.credential,
                base_url=ep.base_url,
                timeout=CLIENT_TIMEOUT,
            )
        return self._client

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._ensure_client()
        async for delta in stream_complete(
            client,
            self._model,
            req,
            supports_vision=self.supports_vision,
        ):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        """Estimate Grok 4.3 cost from xAI's published token pricing."""
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 1.25 + req.max_tokens * 2.50) / 1_000_000
