"""Ollama — keyless local brain via Ollama's OpenAI-compatible endpoint.

Runs whatever models the user has pulled into a local (or LAN) Ollama server —
no API key, no cloud account, nothing leaves the machine. The server's ``/v1``
endpoint speaks the OpenAI Chat-Completions format, so this brain is a thin
binding of the shared ``_openai_base`` streamer to the server root resolved
from ``[brain.providers.ollama].base_url`` / ``OLLAMA_HOST`` / the localhost
default — the same shape as the NVIDIA brain.

Keyless by design (§3 / AP-22): ``ollama`` is deliberately ABSENT from
``PROVIDER_SECRET_CANDIDATES`` — that absence is what makes the key-aware
resolver treat it as always reachable. A dead server fast-fails on the 2 s
connect timeout so the fallback chain crosses to the next family instead of
stalling. Tool calling rides the OpenAI-compat layer (``tool_choice`` is not
used on the pipeline path); reliability is model-dependent — the qwen line is
the recommended pull.

Model discovery is NATIVE (``/api/tags`` + ``/api/show``): with no configured
model the brain runs the smallest DOWNLOADED model whose declared capabilities
match the turn (``completion``, plus ``tools`` for tool turns); ``:cloud``
references never count as local, and a model-less server produces an honest
"pull one first" error instead of a hardcoded default that would 404.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import stream_complete

log = logging.getLogger(__name__)

DEFAULT_SERVER_ROOT = "http://localhost:11434"

# A tools-capable local model line that streams tool calls reliably through
# the OpenAI-compat layer (Gemma-family models have known streaming-tool-call
# defects there). Used in error/help text only — never silently pulled.
RECOMMENDED_PULL = "qwen3.5"

# Local server: an unreachable endpoint must fail fast (2 s) so the fallback
# chain moves on, while a slow CPU-bound generation may legitimately stream
# for minutes — hence the wide read timeout.
CLIENT_TIMEOUT = httpx.Timeout(connect=2.0, read=120.0, write=30.0, pool=30.0)


def normalize_server_root(url: str) -> str:
    """Normalize a user/env-supplied Ollama address to a bare server root.

    Accepts ``host:port``, a full URL, a trailing slash, or a pasted ``…/v1``
    / ``…/api`` suffix and always returns ``scheme://host[:port]`` so callers
    append ``/v1`` (chat) or ``/api/...`` (native) themselves. ``0.0.0.0`` is
    a server BIND address, not a client target (connecting to it fails on
    Windows), so it maps to localhost.
    """
    root = (url or "").strip().rstrip("/")
    if not root:
        return DEFAULT_SERVER_ROOT
    if "://" not in root:
        root = f"http://{root}"
    for suffix in ("/v1", "/api"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
    root = root.replace("://0.0.0.0", "://localhost")
    return root.rstrip("/")


def default_server_root() -> str:
    """Vendor-default server root: ``OLLAMA_HOST`` if set, else localhost.

    Ollama's own CLI honors ``OLLAMA_HOST`` for both serving and connecting,
    so a user who moved their server declares it exactly once.
    """
    return normalize_server_root(os.environ.get("OLLAMA_HOST") or DEFAULT_SERVER_ROOT)


class OllamaBrain:
    name: str = "ollama"
    # Conservative floor — the real window is model-dependent (the qwen line
    # reaches 128k); the manager treats this as a budget hint, not a hard cap.
    context_window: int = 32_768
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = (model or "").strip()
        self._client: Any = None
        self._server_root: str | None = None
        self._credential: str | None = None
        # Discovery cache per requirement profile (False = plain chat,
        # True = chat + tools) — a tool-less turn may run a smaller model
        # than a tool turn without re-asking the server every time.
        self._discovered: dict[bool, str] = {}

    def can_call_tools(self) -> bool:
        return self.supports_tools

    def _resolve_root(self) -> str:
        if self._server_root is None:
            ep = cfg.resolve_provider_endpoint(
                "ollama", vendor_default_base_url=default_server_root()
            )
            # Root convention: the stored/resolved value is the SERVER root;
            # ``/v1`` (chat) and ``/api/*`` (discovery) are appended here. A
            # team-proxy target (``…/p/ollama``) follows the same convention.
            self._server_root = normalize_server_root(ep.base_url or default_server_root())
            self._credential = ep.credential
        return self._server_root

    def _ensure_client(self) -> Any:
        if self._client is None:
            root = self._resolve_root()
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                # A local Ollama ignores the key entirely, but the SDK insists
                # on a non-empty one; a team proxy swaps in its real token.
                api_key=self._credential or "ollama",
                base_url=f"{root}/v1",
                timeout=CLIENT_TIMEOUT,
            )
        return self._client

    async def _resolve_model(self, *, need_tools: bool = False) -> str:
        """The configured model, else the smallest CAPABLE download.

        Hard rules for the silent default (three live incidents 2026-07-25):
        ``:cloud`` entries are ollama.com-proxied references, not local
        weights — a "local" brain must never route through them. Among the
        real downloads the SMALLEST wins (the former first-installed pick
        landed on a 30B model whose 256k default context needed 45 GB on a
        32 GB box and froze the whole desktop) — but only one whose DECLARED
        ``/api/show`` capabilities match the turn: ``completion`` always
        (bge-m3 would 400 on chat), plus ``tools`` when the request carries
        tools (deepseek-llm 400s on a tool turn). The user's explicit model
        pick always overrides this.
        """
        if self._model:
            return self._model
        if need_tools in self._discovered:
            return self._discovered[need_tools]
        root = self._resolve_root()
        try:
            async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
                resp = await client.get(f"{root}/api/tags")
                resp.raise_for_status()
                models = resp.json().get("models") or []
        except Exception as exc:
            raise RuntimeError(
                f"Ollama not reachable at {root} — is it running? Start it "
                "(or install from https://ollama.com/download), then retry."
            ) from exc
        local = [
            m
            for m in models
            if (name := str(m.get("name") or "").strip())
            and not name.endswith(":cloud")
            and not m.get("remote")
        ]
        if not local:
            raise RuntimeError(
                f"Ollama at {root} has no downloaded models (cloud references "
                f"do not count) — run: ollama pull {RECOMMENDED_PULL}, then retry."
            )
        local.sort(key=lambda m: int(m.get("size") or 0))
        for m in local:
            name = str(m.get("name")).strip()
            caps = await self._capabilities(name, root)
            if caps is not None:
                if "completion" not in caps:
                    continue
                if need_tools and "tools" not in caps:
                    continue
            self._discovered[need_tools] = name
            log.info(
                "ollama: no model configured — using smallest capable download (tools=%s): %s",
                need_tools,
                name,
            )
            return name
        wanted = "chat + tool calling" if need_tools else "chat"
        raise RuntimeError(
            f"None of the models downloaded at {root} supports {wanted} — run: "
            f"ollama pull {RECOMMENDED_PULL}, then retry."
        )

    @staticmethod
    async def _capabilities(name: str, root: str) -> set[str] | None:
        """DECLARED capabilities of one download via native ``/api/show``.

        Gate on capability, never the model name (AP-21). ``None`` = the probe
        could not answer — the caller FAILS OPEN and accepts the candidate (a
        probe glitch must never brick the pick; the real call then errors
        honestly)."""
        try:
            async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
                resp = await client.post(f"{root}/api/show", json={"model": name})
                resp.raise_for_status()
                caps = resp.json().get("capabilities")
        except Exception:  # noqa: BLE001 — probe glitch must not block the pick
            return None
        if not isinstance(caps, list):
            return None
        return {str(c) for c in caps}

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._ensure_client()
        model = await self._resolve_model(need_tools=bool(req.tools))
        async for delta in stream_complete(client, model, req):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        # Local inference has no per-token bill.
        return 0.0
