"""NVIDIA NIM — OpenAI-compatible gateway to NVIDIA-hosted LLMs.

One free NVIDIA API key (``nvapi-…`` from build.nvidia.com) reaches the models
NVIDIA hosts on its inference microservice (NIM): Nemotron, Llama, DeepSeek,
Qwen, Mistral, and more. The endpoint speaks the OpenAI Chat-Completions format,
so this brain is a thin binding of the shared ``_openai_base`` streamer to
NVIDIA's fixed base URL — the same shape as the OpenRouter/OpenAI brains.

Bring-your-own-key, capability-gated (never provider-name-gated, AP-21): a
missing key raises a clean error so the fallback chain crosses to whatever the
user actually has, and the model id comes from the user's pick (the live
``/v1/models`` catalog) — nothing here is load-bearing for a specific model.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import CLIENT_TIMEOUT, stream_complete

# NVIDIA NIM's OpenAI-compatible endpoint. Only the ``nvapi-`` key from
# build.nvidia.com works here (NOT the legacy NGC key). Passed as the vendor
# default so an explicit ``[brain.providers.nvidia].base_url`` override or the
# team proxy can still redirect it (resolve_provider_endpoint).
BASE_URL = "https://integrate.api.nvidia.com/v1"

# Last-resort default when the brain is built with NO model. A widely-hosted,
# tool-capable NIM model so a model-less construction still answers and can call
# tools; the manager passes the user's PICK in almost every path, so this only
# bites a genuinely model-less build. Kept off the reasoning flagships on
# purpose (latency) — the user selects Nemotron/DeepSeek from the live catalog.
DEFAULT_MODEL = "meta/llama-3.3-70b-instruct"


class NvidiaBrain:
    name: str = "nvidia"
    context_window: int = 128_000
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    def can_call_tools(self) -> bool:
        return self.supports_tools

    def _ensure_client(self) -> Any:
        if self._client is None:
            ep = cfg.resolve_provider_endpoint("nvidia", vendor_default_base_url=BASE_URL)
            if not ep.credential:
                raise RuntimeError(
                    "No NVIDIA API key found (nvidia_api_key / NVIDIA_API_KEY). "
                    "Get a build.nvidia.com key (starts with nvapi-)."
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
        async for delta in stream_complete(client, self._model, req):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        # NIM costs are model-dependent (many models are free on the dev tier).
        # Conservative dummy estimator, mirroring the OpenRouter brain.
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 5 + req.max_tokens * 15) / 1_000_000
