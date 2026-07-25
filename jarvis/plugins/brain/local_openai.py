"""Generic local OpenAI-compatible brain (HuggingFace transformers serve & co).

One card for every self-hosted server that speaks the OpenAI Chat-Completions
format: HuggingFace ``transformers serve`` (port 8000), llama.cpp
``llama-server`` (port 8080), vLLM, LM Studio (port 1234) — and TGI installs
keep working, though TGI itself is in maintenance mode and never the
recommendation. Ollama has its own dedicated provider; this is the
bring-your-own-server slot.

Unlike the cloud brains there is NO vendor default endpoint — a base URL is
REQUIRED and its absence raises an honest, example-bearing error instead of
guessing a port. The credential is OPTIONAL (``local_openai_api_key`` /
``LOCAL_OPENAI_API_KEY``): most local servers ignore it, some (LM Studio
behind a reverse proxy, vLLM with ``--api-key``) check it. Keyless by design
(§3 / AP-22): ``local-openai`` stays ABSENT from
``PROVIDER_SECRET_CANDIDATES`` so the key-aware resolver treats it as always
reachable and a dead server just fast-fails across families.

Vision is deliberately declared unsupported: whether a given local server
accepts image content is unknowable here, and the shared streamer then DROPS
images with a warning instead of letting a text-only server reject the whole
turn. Vision users take the Ollama card (which negotiates real multimodal
models) or a cloud brain.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import stream_complete
from .ollama import normalize_server_root

log = logging.getLogger(__name__)

# Same shape as the Ollama timeout: a dead local server must fail fast so the
# fallback chain crosses families; a slow CPU-bound generation may stream long.
CLIENT_TIMEOUT = httpx.Timeout(connect=2.0, read=120.0, write=30.0, pool=30.0)

_NO_BASE_URL_HELP = (
    "The local OpenAI-compatible provider needs a server URL — set it on the "
    'provider card (or [brain.providers."local-openai"].base_url). Examples: '
    "HuggingFace transformers serve: http://localhost:8000 - "
    "llama.cpp llama-server: http://localhost:8080 - "
    "LM Studio: http://localhost:1234 - vLLM: http://localhost:8000."
)


class LocalOpenAIBrain:
    name: str = "local-openai"
    # Conservative floor — the real window depends on the served model; the
    # manager treats this as a budget hint, not a hard cap.
    context_window: int = 32_768
    supports_tools: bool = True
    supports_vision: bool = False

    def __init__(self, model: str | None = None) -> None:
        self._model = (model or "").strip()
        self._client: Any = None
        self._server_root: str | None = None
        self._credential: str | None = None

    def can_call_tools(self) -> bool:
        return self.supports_tools

    def _resolve_root(self) -> str:
        if self._server_root is None:
            ep = cfg.resolve_provider_endpoint("local-openai", vendor_default_base_url=None)
            if not ep.base_url:
                raise RuntimeError(_NO_BASE_URL_HELP)
            # Root convention (same as the Ollama card): the stored value is
            # the SERVER root; ``/v1`` is appended here, and a pasted ``…/v1``
            # is normalized away instead of doubling up.
            self._server_root = normalize_server_root(ep.base_url)
            # Optional key: a team-proxy token wins, else the user's optional
            # local key, else a placeholder the server ignores.
            self._credential = ep.credential or cfg.get_secret(
                "local_openai_api_key", "LOCAL_OPENAI_API_KEY"
            )
        return self._server_root

    def _ensure_client(self) -> Any:
        if self._client is None:
            root = self._resolve_root()
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._credential or "local",
                base_url=f"{root}/v1",
                timeout=CLIENT_TIMEOUT,
            )
        return self._client

    async def _resolve_model(self) -> str:
        """The configured model, else the first one the server lists (``/v1/models``)."""
        if self._model:
            return self._model
        root = self._resolve_root()
        headers = {}
        if self._credential:
            headers["Authorization"] = f"Bearer {self._credential}"
        try:
            async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
                resp = await client.get(f"{root}/v1/models", headers=headers)
                resp.raise_for_status()
                data = resp.json().get("data") or []
        except Exception as exc:
            raise RuntimeError(
                f"Local OpenAI-compatible server not reachable at {root} — is it "
                "running? (transformers serve / llama-server / LM Studio / vLLM)"
            ) from exc
        names = [str(m.get("id") or "").strip() for m in data]
        names = [n for n in names if n]
        if not names:
            raise RuntimeError(
                f"The server at {root} lists no models (/v1/models is empty) — "
                "load a model in the server, or set one explicitly on the card."
            )
        self._model = names[0]
        log.info("local-openai: no model configured — using first served: %s", self._model)
        return self._model

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._ensure_client()
        model = await self._resolve_model()
        async for delta in stream_complete(
            client, model, req, supports_vision=self.supports_vision
        ):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        # Local inference has no per-token bill.
        return 0.0
