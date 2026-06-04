"""xAI Grok — OpenAI-compatible, dedicated endpoint."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import stream_complete

DEFAULT_MODEL = "grok-4.3"
BASE_URL = "https://api.x.ai/v1"


class GrokBrain:
    name: str = "grok"
    context_window: int = 1_000_000
    supports_tools: bool = True
    # Grok-4.3 supports image and video input. supports_vision is intentionally
    # left False until the image-routing path in the OpenAI-compat layer
    # (_openai_base.stream_complete) has been tested end-to-end —
    # otherwise image-bearing requests cause a runtime error. Follow-up.
    supports_vision: bool = False

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            api_key = cfg.get_provider_secret("grok")
            if not api_key:
                raise RuntimeError(
                    "Kein Grok-API-Key gefunden "
                    "(grok_api_key / xai_api_key / GROK_API_KEY / XAI_API_KEY)."
                )
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=api_key, base_url=BASE_URL)
        return self._client

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._ensure_client()
        async for delta in stream_complete(
            client, self._model, req, supports_vision=self.supports_vision,
        ):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        # grok-4.3 pricing: $1.25 / M input, $2.50 / M output (doubles
        # above 200k input tokens — approximated linearly here, sufficient
        # accuracy for heuristic cost estimates).
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 1.25 + req.max_tokens * 2.50) / 1_000_000
