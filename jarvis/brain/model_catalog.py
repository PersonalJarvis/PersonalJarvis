"""Per-provider model catalog — the live model list behind the API-Keys model
picker.

Where :mod:`jarvis.brain.frontier_resolver` queries a provider's ``/v1/models``
endpoint and distils it down to the single frontier pick per tier, this module
returns the *whole* catalog so the desktop UI can offer a searchable dropdown.
The two share the same upstream endpoints but answer different questions
(``frontier_resolver`` = "what is the newest model?"; ``model_catalog`` = "what
are all of them, so the user can pick one?").

Design goals (maintainer mandate 2026-06-20):
- **Always current.** The list comes from the provider's own catalog, so a model
  the provider published an hour ago appears without any code change here. There
  is no hand-maintained frontier list on the hot path — only a small ``static``
  fallback for the offline/no-key case, honestly labelled as such.
- **OpenRouter included.** Its catalog has hundreds of models, which is exactly
  why the UI needs search; this module just hands over the full list.
- **Honest source.** Every result carries ``source`` ∈ {``live``, ``cache``,
  ``static``} so the UI never pretends a stale fallback is the live catalog.

Cache: ``data/model_catalog_cache.json``, default TTL 6 h (shorter than the
frontier resolver's 24 h — fresher is better for a list the user browses), with
``force_refresh`` to bypass it on an explicit "refresh" click.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from jarvis.core import config as cfg

log = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 6

# The API-key brain providers whose catalogs we can enumerate. Codex is excluded
# on purpose: it authenticates via the ChatGPT login / a generic OpenAI key and
# its model id is largely ignored by the ``codex exec`` CLI path — it has no own
# model picker in the UI (it renders the Codex login widget instead).
CATALOG_PROVIDERS: tuple[str, ...] = (
    "claude-api",
    "openai",
    "gemini",
    "grok",
    "openrouter",
)

# Endpoint + auth shape per provider. ``auth`` selects how the key is attached:
#   "x-api-key"  → Anthropic header pair
#   "bearer"     → Authorization: Bearer (OpenAI-compatible: OpenAI, Grok)
#   "query"      → ?key= (Gemini)
#   "bearer_opt" → Authorization: Bearer if a key exists, else anonymous
#                  (OpenRouter's catalog is public)
_ENDPOINTS: dict[str, tuple[str, str]] = {
    "claude-api": ("https://api.anthropic.com/v1/models", "x-api-key"),
    "openai": ("https://api.openai.com/v1/models", "bearer"),
    "grok": ("https://api.x.ai/v1/models", "bearer"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/models",
        "query",
    ),
    "openrouter": ("https://openrouter.ai/api/v1/models", "bearer_opt"),
}


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """One selectable model: the wire ``id`` plus a human ``label``."""

    id: str
    label: str


def _curated(pairs: list[tuple[str, str]]) -> list[ModelInfo]:
    return [ModelInfo(id=i, label=lbl) for i, lbl in pairs]


# Curated current model families per provider — the picker's fallback when the
# live ``/v1/models`` catalog is unreachable (no/invalid key, network down). This
# is what makes the dropdown useful for providers the user drives WITHOUT an API
# key: Claude in particular runs via the Max subscription (OAuth), so its live
# fetch always 401s — the user still expects to pick Fable / Opus / Sonnet /
# Haiku. Keep these to the *current* frontier families (maintainer mandate: never
# offer a years-old model); when a valid key exists the live catalog supersedes
# this entirely, so a new release still shows up automatically there.
CURATED_MODELS: dict[str, list[ModelInfo]] = {
    "claude-api": _curated([
        ("claude-fable-5", "Claude Fable 5"),
        ("claude-opus-4-8", "Claude Opus 4.8"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
    ]),
    "openai": _curated([
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-5.5-pro", "GPT-5.5 Pro"),
        ("gpt-5.5-mini", "GPT-5.5 Mini"),
    ]),
    "gemini": _curated([
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
        ("gemini-3-pro", "Gemini 3 Pro"),
        ("gemini-3-flash-preview", "Gemini 3 Flash"),
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-flash-lite-latest", "Gemini Flash Lite"),
    ]),
    "grok": _curated([
        ("grok-4.3", "Grok 4.3"),
        ("grok-4.3-fast", "Grok 4.3 Fast"),
    ]),
    "openrouter": _curated([
        ("anthropic/claude-opus-4.8", "Claude Opus 4.8"),
        ("anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6"),
        ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5"),
        ("openai/gpt-5.5", "GPT-5.5"),
        ("google/gemini-3-pro-preview", "Gemini 3 Pro"),
        ("x-ai/grok-4.3", "Grok 4.3"),
    ]),
}


@dataclass(frozen=True, slots=True)
class CatalogResult:
    """The model list for one provider, with an honest provenance flag."""

    provider: str
    models: tuple[ModelInfo, ...]
    source: str  # "live" | "cache" | "static" | "curated"
    fetched_at: float
    selects: str = "model"  # what the picker writes: "model" | "voice"


def _ids(ids: list[str]) -> list[ModelInfo]:
    return [ModelInfo(id=i, label=i) for i in ids]


# TTS catalogs — for most TTS providers the user-facing pick is the VOICE
# (Gemini Charon/Kore, Grok leo/rex, OpenAI alloy/nova, Google Neural2 names);
# Cartesia's meaningful pick is its MODEL (sonic-3.5). The ``[tts]`` config is a
# single block (voice_de/voice_en/model), so the picker only renders on the
# ACTIVE TTS card and sets the global value.
TTS_CATALOG: dict[str, tuple[str, list[ModelInfo]]] = {
    "gemini-flash-tts": ("voice", _ids([
        "Charon", "Kore", "Aoede", "Orus", "Iapetus",
        "Rasalgethi", "Algenib", "Algieba", "Fenrir",
    ])),
    "grok-voice": ("voice", _ids(["leo", "rex", "sal", "ara", "eve"])),
    "openai-tts": ("voice", _ids([
        "alloy", "ash", "ballad", "coral", "echo",
        "fable", "onyx", "nova", "sage", "shimmer",
    ])),
    "google-neural2": ("voice", _ids([
        "en-US-Neural2-A", "en-US-Neural2-C", "en-US-Neural2-D", "en-US-Neural2-F",
        "de-DE-Neural2-B", "de-DE-Neural2-C", "de-DE-Neural2-D", "de-DE-Neural2-F",
    ])),
    "cartesia": ("model", _ids(["sonic-3.5", "sonic-2", "sonic-turbo"])),
}

# STT model catalogs (the ``[stt] model`` is a single global value).
STT_CATALOG: dict[str, list[ModelInfo]] = {
    "groq-api": _ids(["whisper-large-v3", "whisper-large-v3-turbo", "distil-whisper-large-v3-en"]),
    "faster-whisper": _ids([
        "distil-large-v3", "large-v3", "large-v3-turbo", "medium", "small", "base", "tiny",
    ]),
    "openai-api": _ids(["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"]),
    "deepgram": _ids(["nova-3", "nova-2", "nova-2-general", "enhanced", "base"]),
}


@dataclass(frozen=True, slots=True)
class CatalogSpec:
    """Per-provider picker spec: which tier, what it selects, the curated list,
    and whether a live ``/v1/models`` fetch is available (brain providers only)."""

    tier: str       # "brain" | "tts" | "stt"
    selects: str    # "model" | "voice"
    curated: tuple[ModelInfo, ...]
    live: bool


def _build_provider_catalog() -> dict[str, CatalogSpec]:
    cat: dict[str, CatalogSpec] = {}
    # The 5 live-fetchable API brains.
    for p in CATALOG_PROVIDERS:
        cat[p] = CatalogSpec("brain", "model", tuple(CURATED_MODELS.get(p, ())), live=True)
    # Codex — a subscription brain (ChatGPT login); no /v1/models over OAuth, so
    # curated only. Its model is the OpenAI/codex gpt-5.5 family.
    cat["codex"] = CatalogSpec("brain", "model", tuple(_curated([
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-5.5-pro", "GPT-5.5 Pro"),
        ("gpt-5.5-codex", "GPT-5.5 Codex"),
        ("gpt-5.5-mini", "GPT-5.5 Mini"),
    ])), live=False)
    # Antigravity — a Google-subscription brain driven via the official agy/gemini
    # CLI (OAuth login); no /v1/models over OAuth, so curated only. The available
    # set is plan-gated by the user's Google subscription.
    cat["antigravity"] = CatalogSpec("brain", "model", tuple(_curated([
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
        ("gemini-3-pro", "Gemini 3 Pro"),
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-3-flash-preview", "Gemini 3 Flash"),
    ])), live=False)
    for p, (selects, opts) in TTS_CATALOG.items():
        cat[p] = CatalogSpec("tts", selects, tuple(opts), live=False)
    for p, opts in STT_CATALOG.items():
        cat[p] = CatalogSpec("stt", "model", tuple(opts), live=False)
    return cat


PROVIDER_CATALOG: dict[str, CatalogSpec] = _build_provider_catalog()


def catalog_spec(provider: str) -> CatalogSpec | None:
    """The picker spec for ``provider`` (None if it has no catalog)."""
    return PROVIDER_CATALOG.get(provider)


# ----------------------------------------------------------------------
# Pure parsing + sorting (module-level for easy testing)
# ----------------------------------------------------------------------

def parse_models_response(provider: str, payload: dict) -> list[ModelInfo]:
    """Map a provider's ``/v1/models`` JSON to a flat ``list[ModelInfo]``.

    Anthropic / OpenAI / Grok share the OpenAI-compatible ``data[].id`` shape.
    Gemini lists ``models[].name`` (``models/<id>``) with an optional
    ``displayName``. OpenRouter adds a human ``name`` we surface as the label.
    Entries without a usable id are dropped.
    """
    out: list[ModelInfo] = []
    if provider == "gemini":
        for m in payload.get("models", []) or []:
            raw = (m.get("name") or "").removeprefix("models/").strip()
            if not raw:
                continue
            label = (m.get("displayName") or "").strip() or raw
            out.append(ModelInfo(id=raw, label=label))
        return out

    # OpenAI-compatible shape (OpenAI / Anthropic / Grok / OpenRouter).
    for m in payload.get("data", []) or []:
        raw = (m.get("id") or "").strip()
        if not raw:
            continue
        label = (m.get("name") or "").strip() or raw  # OpenRouter has a name
        out.append(ModelInfo(id=raw, label=label))
    return out


# Substrings (case-insensitive on the id) that mark a model as NOT a usable
# chat/reasoning brain: generative-media (video/image/music), audio I/O, speech,
# embeddings, moderation/safety classifiers. These can never back the brain (the
# probe would 404/400), and showing them in a brain picker is pure noise — the
# Gemini catalog in particular front-loads Veo/Imagen/Lyria/Nano-Banana. A truly
# exotic model is still reachable via the free-text custom entry.
_NON_BRAIN_MARKERS: tuple[str, ...] = (
    "veo", "imagen", "lyria", "nano-banana", "dall-e", "dalle", "sora",
    "whisper", "transcrib", "speech", "tts", "-audio", "audio-",
    "embed", "image", "moderation", "rerank", "-live", "guard",
)


def filter_brain_models(models: list[ModelInfo]) -> list[ModelInfo]:
    """Keep only models that can plausibly serve as a chat/reasoning brain.

    Drops generative-media, audio, speech, embedding and safety-classifier models
    by an id-substring blocklist (:data:`_NON_BRAIN_MARKERS`). Conservative on
    purpose — anything not clearly non-text stays, and the UI's free-text entry
    covers the rest.
    """
    out: list[ModelInfo] = []
    for m in models:
        low = m.id.lower()
        if any(mark in low for mark in _NON_BRAIN_MARKERS):
            continue
        out.append(m)
    return out


def _is_stale(provider: str, model_id: str) -> bool:
    """True if ``model_id`` is an end-of-life model we demote in the list.

    Reuses ``frontier_resolver.STALE_MODELS`` (the maintained EOL set). For
    OpenRouter the id is namespaced (``openai/gpt-4o``); we test the part after
    the slash against the same set.
    """
    from jarvis.brain.frontier_resolver import STALE_MODELS

    if model_id in STALE_MODELS:
        return True
    if "/" in model_id and model_id.rsplit("/", 1)[-1] in STALE_MODELS:
        return True
    return False


def sort_models(provider: str, models: list[ModelInfo]) -> list[ModelInfo]:
    """Order newest/frontier first, EOL models last.

    Sort key: non-stale before stale, then id descending — version strings sort
    so the newer one wins (``gpt-5.5`` > ``gpt-4o``, ``gemini-3`` > ``gemini-2``).
    Search is the real discovery tool (esp. for OpenRouter), so this is only a
    sensible default ordering, not a curation.
    """
    return sorted(
        models,
        key=lambda m: (not _is_stale(provider, m.id), m.id),
        reverse=True,
    )


# ----------------------------------------------------------------------
# Catalog with cache + live fetch + static fallback
# ----------------------------------------------------------------------

class ModelCatalog:
    """Live model lists per provider with a TTL cache and honest fallbacks."""

    def __init__(
        self,
        cache_path: Path | None = None,
        ttl_hours: int = DEFAULT_TTL_HOURS,
        http_client_factory: object | None = None,
    ) -> None:
        self._cache_path = cache_path or Path("data/model_catalog_cache.json")
        self._ttl_seconds = ttl_hours * 3600
        # provider -> (fetched_at, models)
        self._cache: dict[str, tuple[float, list[ModelInfo]]] = {}
        self._lock = asyncio.Lock()
        self._client_factory = http_client_factory
        self._load_cache()

    # -- cache I/O -----------------------------------------------------

    def _load_cache(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            for prov, entry in data.items():
                models = [
                    ModelInfo(id=m["id"], label=m.get("label") or m["id"])
                    for m in entry.get("models", [])
                ]
                self._cache[prov] = (float(entry.get("fetched_at", 0.0)), models)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("model_catalog_cache.json corrupt — discarded: %s", exc)
            self._cache.clear()

    def _save_cache(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            prov: {
                "fetched_at": ts,
                "models": [{"id": m.id, "label": m.label} for m in models],
            }
            for prov, (ts, models) in self._cache.items()
        }
        self._cache_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )

    def _is_fresh(self, fetched_at: float) -> bool:
        return (time.time() - fetched_at) < self._ttl_seconds

    @staticmethod
    def _present(provider: str, models: list[ModelInfo]) -> tuple[ModelInfo, ...]:
        """Filter to brain-usable models, then order newest/frontier first."""
        return tuple(sort_models(provider, filter_brain_models(models)))

    # -- public API ----------------------------------------------------

    async def list_models(
        self, provider: str, *, force_refresh: bool = False
    ) -> CatalogResult:
        """Return the catalog for ``provider`` with an honest ``source`` flag.

        Brain providers with a live endpoint: fresh cache → ``cache`` (no
        network); else fetch → ``live`` (cache updated); fetch failure → stale
        cache or the curated ``static`` fallback. TTS/STT providers (and Codex)
        have no ``/v1/models`` endpoint → the curated catalog (``curated``). The
        ``selects`` field tells the UI whether it picks a model or a voice.
        """
        spec = catalog_spec(provider)
        if spec is None:
            return CatalogResult(provider, (), "static", 0.0, "model")

        # TTS / STT / Codex: curated list only (no live endpoint). Returned as-is
        # (no brain-model filtering/sorting — voices and STT models are not brain
        # models and must keep their curated order).
        if not spec.live:
            return CatalogResult(
                provider=provider,
                models=tuple(spec.curated),
                source="curated",
                fetched_at=0.0,
                selects=spec.selects,
            )

        async with self._lock:
            cached = self._cache.get(provider)
            if cached and not force_refresh and self._is_fresh(cached[0]):
                return CatalogResult(
                    provider, self._present(provider, cached[1]), "cache", cached[0], "model",
                )

            try:
                models = await self._fetch_raw(provider)
            except Exception as exc:  # noqa: BLE001 — a UI list must never crash the page.
                log.info("Model catalog fetch for %s failed: %s", provider, exc)
                if cached:
                    return CatalogResult(
                        provider, self._present(provider, cached[1]), "cache", cached[0], "model",
                    )
                static = self._static_fallback(provider)
                return CatalogResult(
                    provider, self._present(provider, static), "static", 0.0, "model",
                )

            now = time.time()
            self._cache[provider] = (now, models)
            self._save_cache()
            return CatalogResult(
                provider, self._present(provider, models), "live", now, "model",
            )

    # -- network -------------------------------------------------------

    async def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()  # type: ignore[operator]
        return httpx.AsyncClient(timeout=12.0)

    async def _fetch_raw(self, provider: str) -> list[ModelInfo]:
        """Call ``provider``'s catalog endpoint and parse it. Raises on no key
        (except OpenRouter, whose catalog is public) or a transport/HTTP error."""
        if provider not in _ENDPOINTS:
            raise ValueError(f"Unsupported provider: {provider}")
        url, auth = _ENDPOINTS[provider]
        key = cfg.get_provider_secret(provider)
        if not key and auth != "bearer_opt":
            raise RuntimeError(f"No API key configured for {provider}.")

        headers: dict[str, str] = {}
        params: dict[str, str] = {}
        if auth == "x-api-key":
            headers = {"x-api-key": key or "", "anthropic-version": "2023-06-01"}
        elif auth == "bearer":
            headers = {"Authorization": f"Bearer {key}"}
        elif auth == "bearer_opt":
            if key:
                headers = {"Authorization": f"Bearer {key}"}
        elif auth == "query":
            params = {"key": key or ""}

        client = await self._client()
        async with client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return parse_models_response(provider, resp.json())

    # -- static fallback ----------------------------------------------

    def _static_fallback(self, provider: str) -> list[ModelInfo]:
        """The curated current model family for ``provider``.

        Used when the live catalog is unreachable AND there is no cache — so the
        picker still offers a full, useful selection (esp. Claude via Max, whose
        live fetch always 401s). Falls back to the maintained tier defaults for
        any provider not in :data:`CURATED_MODELS`.
        """
        curated = CURATED_MODELS.get(provider)
        if curated:
            return list(curated)
        try:
            from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER
        except Exception:  # noqa: BLE001
            return []
        seen: dict[str, ModelInfo] = {}
        for tier in ("router", "deep"):
            mid = TIER_DEFAULTS_BY_PROVIDER.get(tier, {}).get(provider)
            if mid:
                seen.setdefault(mid, ModelInfo(id=mid, label=mid))
        return list(seen.values())
