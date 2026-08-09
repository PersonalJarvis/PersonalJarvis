"""Model discovery for the UltraWiki embedding slot.

Why this exists: the embedding card used to be a free-text box. Typing a model
id from memory is how you end up with `gemini-embedding-01`, a 404 on the first
real embed, and a paused pipeline whose cause is one character wide. The chat
providers never had that problem — they get a searchable dropdown fed by the
provider's own catalog (:mod:`jarvis.brain.model_catalog`) — and there is no
reason the embedding slot should be the exception.

So each backend gets the same treatment, in the same order of preference:

1. **Live** — ask the provider what it actually serves, so a model released
   after this build still shows up with no code change.
2. **Curated** — a short, current, hand-kept list when the provider has no
   listing endpoint (Voyage) or the call fails (no key, offline, rate limit).

Never a hard failure: an unreachable catalog degrades to the curated list, and
the UI keeps its "type a custom id" escape hatch, so a brand-new model is
always reachable. Discovery is read-only and unauthenticated where possible;
credentials ride the usual secret chain and are never logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from jarvis.core.config import get_secret, get_secret_any

log = logging.getLogger(__name__)

__all__ = [
    "EmbeddingModel",
    "EmbeddingModelList",
    "CURATED_EMBEDDING_MODELS",
    "list_embedding_models",
]

#: A catalog call must not stall the settings screen; a slow provider falls
#: back to the curated list rather than holding the page.
_TIMEOUT = httpx.Timeout(connect=3.0, read=6.0, write=3.0, pool=3.0)

#: Mirrors the Gemini credential candidates used by the embedding backend.
_GEMINI_KEY_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("gemini_api_key", "GEMINI_API_KEY"),
    ("google_aistudio_api_key", "GOOGLE_AIStudio_API_KEY"),
    ("google_api_key", "GOOGLE_API_KEY"),
)


@dataclass(frozen=True, slots=True)
class EmbeddingModel:
    """One selectable embedding model. Shape mirrors the brain picker's rows."""

    id: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label}


@dataclass(frozen=True, slots=True)
class EmbeddingModelList:
    """Discovery result. ``source`` is honest about where the list came from."""

    models: tuple[EmbeddingModel, ...]
    #: "live" (the provider answered) | "curated" (hand-kept fallback)
    source: str
    #: Empty when live; why the fallback was used otherwise.
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "models": [m.as_dict() for m in self.models],
            "source": self.source,
            "reason": self.reason,
        }


#: Hand-kept fallback per backend. Deliberately short — the current, sensible
#: choices, not an archive. Ordered best-default-first; the settings card's
#: placeholder still comes from provider_catalog.DEFAULT_MODELS.
CURATED_EMBEDDING_MODELS: dict[str, tuple[tuple[str, str], ...]] = {
    "ollama": (
        (
            "qwen3-embedding:4b",
            "qwen3-embedding:4b — current multilingual default",
        ),
    ),
    "gemini": (
        ("gemini-embedding-001", "gemini-embedding-001"),
        ("text-embedding-004", "text-embedding-004"),
    ),
    "openai": (
        ("text-embedding-3-small", "text-embedding-3-small — cheap, strong"),
        ("text-embedding-3-large", "text-embedding-3-large — highest quality"),
        ("text-embedding-ada-002", "text-embedding-ada-002 — legacy"),
    ),
    "voyage": (
        ("voyage-3.5", "voyage-3.5 — general purpose"),
        ("voyage-3.5-lite", "voyage-3.5-lite — cheaper, faster"),
        ("voyage-3-large", "voyage-3-large — highest quality"),
        ("voyage-code-3", "voyage-code-3 — tuned for code"),
    ),
    "mistral": (
        ("mistral-embed", "mistral-embed"),
        ("codestral-embed", "codestral-embed — tuned for code"),
    ),
    "cohere": (
        ("embed-v4.0", "embed-v4.0 — current, multilingual"),
        ("embed-multilingual-v3.0", "embed-multilingual-v3.0"),
        ("embed-english-v3.0", "embed-english-v3.0"),
    ),
}


def _curated(provider: str, reason: str) -> EmbeddingModelList:
    rows = CURATED_EMBEDDING_MODELS.get(provider, ())
    return EmbeddingModelList(
        models=tuple(EmbeddingModel(id=i, label=lbl) for i, lbl in rows),
        source="curated",
        reason=reason,
    )


def _live(models: list[EmbeddingModel]) -> EmbeddingModelList:
    return EmbeddingModelList(models=tuple(models), source="live")


async def _get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def _ollama(cfg: Any, transport: Any) -> EmbeddingModelList:
    """Everything the local Ollama has pulled.

    Ollama exposes no "is this an embedding model" flag, so the list is what is
    installed and the user picks. That is honest: guessing by name would hide a
    freshly pulled model behind a filter the user cannot see.
    """
    ultrawiki = getattr(cfg, "ultrawiki", None)
    endpoint = str(
        getattr(ultrawiki, "ollama_endpoint", "") or "http://localhost:11434"
    ).rstrip("/")
    data = await _get_json(f"{endpoint}/api/tags", transport=transport)
    rows = data.get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected /api/tags shape")
    models = [
        EmbeddingModel(id=str(row["name"]), label=str(row["name"]))
        for row in rows
        if isinstance(row, dict) and row.get("name")
    ]
    if not models:
        return _curated(
            "ollama",
            "Ollama is running but has no models pulled yet — "
            "'ollama pull qwen3-embedding:4b' installs the current default.",
        )
    return _live(models)


async def _gemini(cfg: Any, transport: Any) -> EmbeddingModelList:
    """Gemini models that actually serve embedContent (capability, not name)."""
    key = get_secret_any(_GEMINI_KEY_CANDIDATES)
    if not key:
        return _curated("gemini", "no Gemini API key saved yet")
    data = await _get_json(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
        headers={"x-goog-api-key": key},
        transport=transport,
    )
    rows = data.get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected models shape")
    models: list[EmbeddingModel] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        methods = row.get("supportedGenerationMethods") or []
        if not any(str(m).lower() == "embedcontent" for m in methods):
            continue
        name = str(row.get("name") or "").removeprefix("models/")
        if name:
            models.append(
                EmbeddingModel(id=name, label=str(row.get("displayName") or name))
            )
    return _live(models) if models else _curated("gemini", "no embedding models listed")


async def _openai_shaped(
    provider: str, url: str, slot: str, transport: Any, *, needle: str
) -> EmbeddingModelList:
    """OpenAI-compatible ``/v1/models``, narrowed to embedding models."""
    key = get_secret(slot)
    if not key:
        return _curated(provider, "no API key saved yet")
    data = await _get_json(
        url, headers={"Authorization": f"Bearer {key}"}, transport=transport
    )
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected /v1/models shape")
    models = [
        EmbeddingModel(id=str(row["id"]), label=str(row["id"]))
        for row in rows
        if isinstance(row, dict) and needle in str(row.get("id", "")).lower()
    ]
    return _live(models) if models else _curated(provider, "no embedding models listed")


async def _cohere(cfg: Any, transport: Any) -> EmbeddingModelList:
    """Cohere lets us ask for the embed endpoint's models directly."""
    key = get_secret("cohere_api_key")
    if not key:
        return _curated("cohere", "no API key saved yet")
    data = await _get_json(
        "https://api.cohere.com/v1/models?endpoint=embed&page_size=100",
        headers={"Authorization": f"Bearer {key}"},
        transport=transport,
    )
    rows = data.get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected models shape")
    models = [
        EmbeddingModel(id=str(row["name"]), label=str(row["name"]))
        for row in rows
        if isinstance(row, dict) and row.get("name")
    ]
    return _live(models) if models else _curated("cohere", "no embedding models listed")


async def list_embedding_models(
    provider: str,
    cfg: Any,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EmbeddingModelList:
    """The selectable models for one embedding backend. Never raises.

    Any discovery failure — no key, offline, throttled, a changed response
    shape — degrades to the curated list with the reason attached, because a
    settings dropdown that shows nothing is worse than one showing five known
    good options.
    """
    provider = (provider or "").strip()
    if provider not in CURATED_EMBEDDING_MODELS:
        return EmbeddingModelList(
            models=(), source="curated", reason=f"unknown embedding provider {provider!r}"
        )
    try:
        if provider == "ollama":
            return await _ollama(cfg, transport)
        if provider == "gemini":
            return await _gemini(cfg, transport)
        if provider == "openai":
            return await _openai_shaped(
                "openai",
                "https://api.openai.com/v1/models",
                "openai_api_key",
                transport,
                needle="embedding",
            )
        if provider == "mistral":
            return await _openai_shaped(
                "mistral",
                "https://api.mistral.ai/v1/models",
                "mistral_api_key",
                transport,
                needle="embed",
            )
        if provider == "cohere":
            return await _cohere(cfg, transport)
        # Voyage publishes no model-listing endpoint; the curated list is the
        # only honest answer, and the custom-id row still reaches anything new.
        return _curated("voyage", "Voyage publishes no model list — these are the current ones")
    except httpx.HTTPStatusError as exc:
        return _curated(provider, f"the provider answered HTTP {exc.response.status_code}")
    except httpx.HTTPError as exc:
        return _curated(provider, f"the provider was unreachable ({type(exc).__name__})")
    except Exception as exc:  # noqa: BLE001 — discovery never breaks the screen
        log.debug("embedding model discovery failed for %s", provider, exc_info=True)
        return _curated(provider, f"model discovery failed ({type(exc).__name__})")
