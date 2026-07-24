"""UltraWiki rerank backends — thin httpx adapters for Voyage and Cohere.

Rerank is an OPTIONAL retrieval stage (design doc 03): a small scoring model
re-scores the fused top-N candidates against the actual question. When no
rerank-capable provider is configured or the configured one is not ready, the
caller skips the stage honestly and the fusion order stands — never a brick.

Adapters are plain HTTP (no SDKs); credentials resolve through
:func:`jarvis.core.config.get_secret`. ``ready()`` is a credential-presence
probe only (AP-21): never raises, never performs a paid call. A failing rerank
call raises :class:`RerankError`; the retrieval path catches it and falls back
to fusion order.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from jarvis.core.config import get_secret

log = logging.getLogger(__name__)

__all__ = [
    "RerankError",
    "VoyageReranker",
    "CohereReranker",
    "RERANK_BACKENDS",
    "DEFAULT_RERANK_MODELS",
    "available_rerankers",
    "resolve_reranker",
]


class RerankError(RuntimeError):
    """A rerank call failed (HTTP, transport, or response parse). The caller
    skips the stage and keeps the fusion order."""


_RERANK_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

#: Sensible current default model per backend.
DEFAULT_RERANK_MODELS: dict[str, str] = {
    "voyage": "rerank-2.5",
    "cohere": "rerank-v3.5",
}


class _HttpReranker:
    """Shared httpx plumbing; ``transport`` is injectable for offline tests."""

    name = "base"
    _URL = ""
    _SECRET_SLOT = ""
    _KEY_LABEL = ""

    def __init__(
        self,
        *,
        model: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model or DEFAULT_RERANK_MODELS.get(self.name, "")
        self._transport = transport

    def _key(self) -> str | None:
        return get_secret(self._SECRET_SLOT)

    def ready(self) -> tuple[bool, str]:
        if self._key():
            return True, ""
        return False, (
            f"No {self._KEY_LABEL} API key is configured - save "
            f"{self._SECRET_SLOT} in the API-Keys view or set the "
            f"{self._SECRET_SLOT.upper()} environment variable."
        )

    def _payload(self, query: str, documents: list[str], top_k: int) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, data: Any) -> list[tuple[int, float]]:
        raise NotImplementedError

    async def rerank(
        self, query: str, documents: list[str], top_k: int
    ) -> list[tuple[int, float]]:
        """Score ``documents`` against ``query``; return ``(index, score)``
        pairs, best first, at most ``top_k`` entries."""
        if not documents:
            return []
        key = self._key()
        if not key:
            raise RerankError(f"{self.name}: no API key configured")
        try:
            async with httpx.AsyncClient(
                timeout=_RERANK_TIMEOUT, transport=self._transport
            ) as client:
                response = await client.post(
                    self._URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json=self._payload(query, documents, top_k),
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise RerankError(
                f"{self.name}: rerank request failed with "
                f"HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RerankError(
                f"{self.name}: rerank request failed ({type(exc).__name__})"
            ) from exc
        except ValueError as exc:
            raise RerankError(f"{self.name}: rerank response is not valid JSON") from exc
        try:
            return self._parse(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankError(f"{self.name}: unexpected rerank response shape") from exc


class VoyageReranker(_HttpReranker):
    """Voyage AI ``POST /v1/rerank``."""

    name = "voyage"
    _URL = "https://api.voyageai.com/v1/rerank"
    _SECRET_SLOT = "voyage_api_key"  # noqa: S105 — secret slot NAME, not a value
    _KEY_LABEL = "Voyage AI"

    def _payload(self, query: str, documents: list[str], top_k: int) -> dict[str, Any]:
        return {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_k": top_k,
        }

    def _parse(self, data: Any) -> list[tuple[int, float]]:
        return [
            (int(row["index"]), float(row["relevance_score"])) for row in data["data"]
        ]


class CohereReranker(_HttpReranker):
    """Cohere ``POST /v2/rerank``."""

    name = "cohere"
    _URL = "https://api.cohere.com/v2/rerank"
    _SECRET_SLOT = "cohere_api_key"  # noqa: S105 — secret slot NAME, not a value
    _KEY_LABEL = "Cohere"

    def _payload(self, query: str, documents: list[str], top_k: int) -> dict[str, Any]:
        return {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": top_k,
        }

    def _parse(self, data: Any) -> list[tuple[int, float]]:
        return [
            (int(row["index"]), float(row["relevance_score"]))
            for row in data["results"]
        ]


#: name -> factory(cfg). cfg is unused today (keys ride the secret chain) but
#: keeps the registry shape identical to the embedding slot.
RERANK_BACKENDS: dict[str, Callable[[Any], Any]] = {
    "voyage": lambda cfg: VoyageReranker(),
    "cohere": lambda cfg: CohereReranker(),
}


def available_rerankers(cfg: Any) -> list[dict[str, Any]]:
    """Readiness report over every rerank backend, via ``ready()`` only."""
    rows: list[dict[str, Any]] = []
    for name, factory in RERANK_BACKENDS.items():
        try:
            usable, reason = factory(cfg).ready()
        except Exception as exc:  # noqa: BLE001 — a broken probe reports, never raises
            usable, reason = False, f"readiness probe failed ({type(exc).__name__})"
            log.debug("rerank backend %s readiness probe failed", name, exc_info=True)
        rows.append(
            {
                "name": name,
                "ready": usable,
                "reason": reason,
                "default_model": DEFAULT_RERANK_MODELS.get(name, ""),
            }
        )
    return rows


def resolve_reranker(cfg: Any) -> Any | None:
    """The configured, ready reranker — or ``None``, telling the caller to
    skip the stage honestly (unconfigured, unknown, or not ready)."""
    ultrawiki = getattr(cfg, "ultrawiki", None)
    name = str(getattr(ultrawiki, "rerank_provider", "") or "").strip()
    if not name:
        return None
    factory = RERANK_BACKENDS.get(name)
    if factory is None:
        log.warning("unknown rerank provider %r - skipping the rerank stage", name)
        return None
    backend = factory(cfg)
    usable, reason = backend.ready()
    if not usable:
        log.info("rerank provider %s not ready (%s) - skipping the stage", name, reason)
        return None
    return backend
