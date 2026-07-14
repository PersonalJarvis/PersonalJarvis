"""GroqCloud brain over its OpenAI-compatible Chat Completions API.

The same ``groq_api_key`` credential already used by the ``groq-api`` STT
provider can also power chat and Jarvis-Agent tool loops.  The brain keeps the
short ``groq`` slug so it cannot be confused with the STT provider identity.
No Groq-specific SDK is required: Groq documents the OpenAI client as a
supported integration when pointed at its vendor base URL.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import CLIENT_TIMEOUT, stream_complete

BASE_URL = "https://api.groq.com/openai/v1"

# Groq recommends GPT-OSS 120B as a replacement for the retiring Llama 3.3 70B
# alias.  It supports local function calling and a 131,072-token context, so the
# same safe default works for chat and Jarvis-Agent missions.
DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqBrain:
    """Fast hosted chat brain backed by the user's Groq API key."""

    name: str = "groq"
    context_window: int = 131_072
    supports_tools: bool = True
    # The default model is text-only.  A future model-capability catalog can
    # promote a selected vision model without pretending every Groq model can
    # consume screenshots today.
    supports_vision: bool = False

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def can_call_tools(self) -> bool:
        """Groq-hosted chat models support local function calling."""
        return self.supports_tools

    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint(
                "groq", vendor_default_base_url=BASE_URL
            )
            if not ep.credential:
                raise RuntimeError(
                    "No Groq API key found (groq_api_key / GROQ_API_KEY)."
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
        """Return a conservative estimate; Groq pricing varies by model."""
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 1 + req.max_tokens * 1) / 1_000_000
