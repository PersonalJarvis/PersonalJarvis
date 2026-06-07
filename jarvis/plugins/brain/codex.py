"""OpenAI Codex Brain (OpenAI chat API via the Codex API-key slot).

Separate from the plain ``openai`` provider so Codex is an independently
selectable brain — the user can run e.g. brain=codex + subagent=gemini, or any
other combination. Authenticates with the dedicated ``codex_openai_api_key``
slot, falling back to the general OpenAI key.

Note on auth: a chat-completions *brain* needs an OpenAI **API key**. The ChatGPT
**subscription** (OAuth) cannot back a chat endpoint — it powers the Codex
*subagent* (the ``codex`` CLI) instead. So Codex-as-brain = API key,
Codex-as-subagent = subscription, and both can be active at once.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import stream_complete

# Fallback only — the active model comes from [brain.providers.codex].model in
# jarvis.toml. We mirror the proven OpenAIBrain default (a known-good OpenAI
# chat model) rather than a codex-specific id that could 404 out of the box;
# set a codex model in jarvis.toml to use one. Overridable, no code change.
DEFAULT_MODEL = "gpt-5.5"


class CodexBrain:
    name: str = "codex"
    context_window: int = 128_000
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            api_key = cfg.get_provider_secret("codex") or cfg.get_secret(
                "codex_openai_api_key", "OPENAI_API_KEY"
            )
            if not api_key:
                raise RuntimeError(
                    "No Codex/OpenAI API key found "
                    "(codex_openai_api_key / openai_api_key / OPENAI_API_KEY)."
                )
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
