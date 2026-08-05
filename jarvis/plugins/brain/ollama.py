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
match the turn (``completion``, plus ``tools`` for tool turns, plus ``vision``
for a turn carrying images); ``:cloud`` references never count as local, and a
model-less server produces an honest "pull one first" error instead of a
hardcoded default that would 404.

**Vision is a MODEL property, never a server property.** A blanket
``supports_vision = True`` on the class made every Ollama install advertise
sight: ``resolve_vision_brain`` picked it for Screen Context and the CU planner
let it plan from screenshots, so a host whose only pull is a text-only model
answered *about* the picture it had never seen. The flag is therefore resolved
per SELECTED model from the cached catalog (the same route OpenRouter takes),
the image path negotiates a ``vision``-capable download, and a server without
one raises an honest error instead of describing a screenshot blind.
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

# The multimodal counterpart, named only in help text for a turn that carries
# images and finds no ``vision``-capable download. Never silently pulled.
RECOMMENDED_VISION_PULL = "qwen3-vl"

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


def _request_has_images(req: BrainRequest) -> bool:
    """True when any user message of ``req`` carries an image block.

    ``getattr`` for the same backwards-compatibility reason the shared streamer
    uses it: a Protocol predating multimodal messages has no ``images`` field.
    """
    for message in getattr(req, "messages", ()) or ():
        if getattr(message, "images", ()):
            return True
    return False


def _declared_vision_support(model: str) -> bool:
    """Whether this install can be handed an image, read SYNCHRONOUSLY.

    ``resolve_vision_brain`` and the CU planner ask for ``supports_vision``
    before any call has been made, so the answer may not hit the network here
    (that would block the caller's event loop). It comes from the cached model
    catalog instead, which stores each download's declared ``/api/show``
    capabilities under the generic modality fields.

    Fail-OPEN on unknown: with a configured model whose capabilities are not
    cached, or a catalog that has never been fetched on this host, the answer
    stays ``True`` — the image path then negotiates a real vision model and
    raises an honest error if there is none, which is strictly better than
    declaring an install blind and silently routing every screenshot to the
    cloud. Fail-CLOSED once the catalog actually says no: a server whose
    downloads are all text-only answers ``False`` and drops out of the vision
    chain, so the resolver crosses to a provider that can see (AP-21/AP-22).
    """
    try:
        from jarvis.brain.model_catalog import (  # noqa: PLC0415 — lazy (AP-26)
            model_capabilities,
            pick_vision_model,
            provider_has_modality_data,
        )

        if model:
            vision = model_capabilities("ollama", model)["vision"]
            return True if vision is None else bool(vision)
        if not provider_has_modality_data("ollama"):
            return True
        return pick_vision_model("ollama") is not None
    except Exception:  # noqa: BLE001 — a probe must never break construction
        log.debug("ollama: vision capability probe failed — assuming capable")
        return True


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
    # Class-attr DEFAULTS (capable). The INSTANCE narrows them per selected
    # model in ``__init__`` and again once the server has answered — an Ollama
    # server serves whatever the user pulled, and a text-only pull must not be
    # sent screenshots (the vision resolver and the CU planner both gate on
    # ``supports_vision``).
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = (model or "").strip()
        self._client: Any = None
        self._server_root: str | None = None
        self._credential: str | None = None
        # Discovery cache per requirement profile (tools, vision) — a plain
        # chat turn may run a smaller model than a tool turn or an image turn
        # without re-asking the server every time.
        self._discovered: dict[tuple[bool, bool], str] = {}
        # Per-model ``/api/show`` capability cache, shared by discovery and the
        # image path so one turn never probes the same download twice.
        self._caps_cache: dict[str, set[str] | None] = {}
        self.supports_vision = _declared_vision_support(self._model)

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

    async def _resolve_model(
        self, *, need_tools: bool = False, need_vision: bool = False
    ) -> str:
        """The configured model, else the smallest CAPABLE download.

        Hard rules for the silent default (three live incidents 2026-07-25):
        ``:cloud`` entries are ollama.com-proxied references, not local
        weights — a "local" brain must never route through them. Among the
        real downloads the SMALLEST wins (the former first-installed pick
        landed on a 30B model whose 256k default context needed 45 GB on a
        32 GB box and froze the whole desktop) — but only one whose DECLARED
        ``/api/show`` capabilities match the turn: ``completion`` always
        (bge-m3 would 400 on chat), plus ``tools`` when the request carries
        tools (deepseek-llm 400s on a tool turn), plus ``vision`` when it
        carries images (a text-only model does not refuse a screenshot — it
        answers about the words around it). The user's explicit model pick
        always overrides this.
        """
        if self._model:
            return self._model
        profile = (need_tools, need_vision)
        if profile in self._discovered:
            return self._discovered[profile]
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
                # Vision is the one requirement that must NOT fail open: an
                # unprobeable model is skipped rather than handed a screenshot
                # it cannot see, because a blind answer looks exactly like a
                # sighted one.
                if need_vision and "vision" not in caps:
                    continue
            elif need_vision:
                continue
            self._discovered[(need_tools, need_vision)] = name
            log.info(
                "ollama: no model configured — using smallest capable download "
                "(tools=%s, vision=%s): %s",
                need_tools,
                need_vision,
                name,
            )
            return name
        if need_vision:
            self.supports_vision = False
            raise RuntimeError(
                f"None of the models downloaded at {root} can see images — run: "
                f"ollama pull {RECOMMENDED_VISION_PULL} (a multimodal model), "
                "then retry."
            )
        wanted = "chat + tool calling" if need_tools else "chat"
        raise RuntimeError(
            f"None of the models downloaded at {root} supports {wanted} — run: "
            f"ollama pull {RECOMMENDED_PULL}, then retry."
        )

    async def _capabilities(self, name: str, root: str) -> set[str] | None:
        """DECLARED capabilities of one download via native ``/api/show``.

        Gate on capability, never the model name (AP-21). ``None`` = the probe
        could not answer — the caller FAILS OPEN for chat/tools (a probe glitch
        must never brick the pick; the real call then errors honestly) and
        fails CLOSED for vision, where a wrong yes is invisible.

        Cached per model for the lifetime of the brain instance: discovery and
        the image path both ask, and the answer only changes when the user
        re-pulls a model — at which point the catalog TTL and a new instance
        pick it up.
        """
        if name in self._caps_cache:
            return self._caps_cache[name]
        caps_set: set[str] | None = None
        try:
            async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
                resp = await client.post(f"{root}/api/show", json={"model": name})
                resp.raise_for_status()
                caps = resp.json().get("capabilities")
            if isinstance(caps, list):
                caps_set = {str(c) for c in caps}
        except Exception:  # noqa: BLE001 — probe glitch must not block the pick
            caps_set = None
        self._caps_cache[name] = caps_set
        return caps_set

    async def _pinned_model_can_see(self, model: str) -> bool:
        """Whether the user's PINNED model declares ``vision`` (``/api/show``).

        Fails CLOSED on an unanswerable probe: an image turn must not run on a
        model we cannot confirm as multimodal.
        """
        caps = await self._capabilities(model, self._resolve_root())
        return caps is not None and "vision" in caps

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        client = self._ensure_client()
        need_vision = _request_has_images(req)
        model = await self._resolve_model(
            need_tools=bool(req.tools), need_vision=need_vision
        )
        if need_vision and self._model and not await self._pinned_model_can_see(model):
            # A pinned text-only model is the user's own choice, so we do not
            # silently swap it — but we also refuse to answer about a picture
            # it never saw. The caller degrades honestly (Screen Context says
            # it cannot look; the CU planner skips this brain).
            self.supports_vision = False
            raise RuntimeError(
                f"The configured Ollama model '{model}' cannot see images. Pick a "
                f"multimodal model on the Ollama card (ollama pull "
                f"{RECOMMENDED_VISION_PULL}), or leave the model empty so Jarvis "
                "picks a vision-capable download for image turns."
            )
        if need_vision:
            # The negotiated model DOES see — keep the instance flag honest for
            # the next synchronous resolver question.
            self.supports_vision = True
        # Invariant at this point: either the request carries no images, or the
        # model just negotiated declares ``vision`` — so the streamer may encode
        # them.
        async for delta in stream_complete(client, model, req, supports_vision=True):
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:
        # Local inference has no per-token bill.
        return 0.0
