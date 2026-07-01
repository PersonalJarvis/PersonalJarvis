"""Claude via the direct Anthropic API (standard API key).

Keyring/ENV: `anthropic_api_key` / `ANTHROPIC_API_KEY`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._anthropic_base import stream_complete

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class ClaudeAPIBrain:
    """Anthropic messages API via API-Key (classical developer path)."""

    name: str = "claude-api"
    context_window: int = 200_000
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("claude-api")
            if not ep.credential:
                raise RuntimeError(
                    "No Anthropic API key found. Please set one via the wizard or "
                    "ANTHROPIC_API_KEY in the environment."
                )
            from anthropic import AsyncAnthropic
            # max_retries=0 → BrainManager-Fallback greift schneller bei 429
            kwargs: dict[str, Any] = {"api_key": ep.credential, "max_retries": 0, "timeout": 15.0}
            if ep.base_url:
                kwargs["base_url"] = ep.base_url
            self._client = AsyncAnthropic(**kwargs)
        return self._client

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._ensure_client()
        async for delta in stream_complete(client, self._model, req):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        # Rough Claude-Opus estimate: $15/M-in + $75/M-out
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 15 + req.max_tokens * 75) / 1_000_000
