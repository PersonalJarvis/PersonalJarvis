"""OpenRouter — Universal gateway for all top LLMs (OpenAI-compatible).

One API key → access to Claude, GPT, Gemini, Llama, Qwen, DeepSeek, and more.
Model names are namespaced ("anthropic/claude-opus-4.7", "openai/gpt-5").
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import CLIENT_TIMEOUT, stream_complete

# Last-resort default when the brain is built with NO model. OpenRouter is a
# gateway, so this must NEVER be a paid Anthropic id — that silently billed the
# single most expensive model in the catalog on a spend-limited key (live forensic
# 2026-06-29). A free general-purpose model degrades with a clean 404 if retired,
# rather than charging the user (§3/AP-22). The manager passes the user's PICK in
# almost every path; this only bites a genuinely model-less construction.
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterBrain:
    name: str = "openrouter"
    context_window: int = 200_000
    # Class-attr DEFAULTS (capable). The instance overrides them per SELECTED model
    # in __init__ — OpenRouter gateways ~325 models and a text-only or non-tool
    # model must not be sent screenshots / a tools payload (the provider 400s).
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None
        # H4/H5: resolve per-model vision/tool capability from the cached catalog.
        # Default to capable when unknown (no cache yet / field absent) → no
        # regression; the CU planner gates on supports_vision and the brain manager
        # on can_call_tools(), so an honest False makes them delegate/skip instead
        # of erroring on an incapable model the user picked.
        try:
            from jarvis.brain.model_catalog import model_capabilities

            caps = model_capabilities("openrouter", self._model)
            self.supports_vision = True if caps["vision"] is None else bool(caps["vision"])
            self.supports_tools = True if caps["tools"] is None else bool(caps["tools"])
        except Exception:  # noqa: BLE001 — capability probe must never break construction
            self.supports_vision = True
            self.supports_tools = True

    def can_call_tools(self) -> bool:
        """Runtime tool-capability for the SELECTED model (H5)."""
        return self.supports_tools

    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("openrouter", vendor_default_base_url=BASE_URL)
            if not ep.credential:
                raise RuntimeError("Kein OpenRouter-API-Key gefunden (openrouter_api_key).")
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=ep.credential,
                base_url=ep.base_url,
                timeout=CLIENT_TIMEOUT,
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
