"""xAI Grok — OpenAI-compatible, dedicated endpoint."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import CLIENT_TIMEOUT, stream_complete

DEFAULT_MODEL = "grok-4.3"
BASE_URL = "https://api.x.ai/v1"


class GrokBrain:
    name: str = "grok"
    context_window: int = 1_000_000
    supports_tools: bool = True
    # Grok supports image input. The OpenAI-compat image-routing path
    # (_openai_base._to_openai_messages → image_url base64 data-URI) is now
    # verified end-to-end against the live xAI API (2026-06-21): grok-4.3 and
    # grok-4.20-0309-(non-)reasoning all read an attached screenshot and answer
    # about it — no runtime error. This matters for Computer-Use: the screenshot
    # loop SKIPS every supports_vision=False provider (screenshot_only_loop.py
    # _call_brain), so leaving this False made grok — often the only provider
    # with a live key — get skipped, and CU failed with "provider chain failed:
    # N provider(s) skipped — no vision" (live forensic 2026-06-21 18:41).
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("grok", vendor_default_base_url=BASE_URL)
            if not ep.credential:
                raise RuntimeError(
                    "Kein Grok-API-Key gefunden "
                    "(grok_api_key / xai_api_key / GROK_API_KEY / XAI_API_KEY)."
                )
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=ep.credential, base_url=ep.base_url, timeout=CLIENT_TIMEOUT
            )
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
