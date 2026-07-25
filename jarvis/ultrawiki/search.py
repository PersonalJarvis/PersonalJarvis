"""UltraWiki hybrid search — RRF fusion over the store's legs + optional rerank.

The read path (design doc 03): the keyword leg (FTS/tsvector in the store) and
the vector leg (query embedding -> ANN in the store) run CONCURRENTLY, their
ranked lists are fused with Reciprocal Rank Fusion (smoothing constant 60 —
consensus beats a single strong vote), duplicates merge by item, results are
capped per source before the final cut, and an optional rerank model reorders
the top of the fused list.

Degradation ladder (§3 universality — never a brick):

- No embedding provider configured, a dead/keyless backend, or a store-side
  vector degradation (missing sqlite-vec, nothing embedded yet, dim mismatch)
  => the vector leg is skipped with a logged honest reason and keyword search
  answers alone; ``matched_by`` on each result shows which legs actually ran.
- No rerank provider configured, an unknown/not-ready one, or a failing rerank
  call => the fusion order stands. A rerank failure logs once and never fails
  the search.

Heavy/optional pieces (httpx-backed embedding + rerank adapters) are imported
lazily inside the functions (AP-26); this module itself is stdlib + types only.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from jarvis.ultrawiki.types import SearchResult

log = logging.getLogger(__name__)

__all__ = ["hybrid_search", "search_status"]

#: RRF smoothing constant (design doc 03: score(d) = sum 1 / (60 + rank)).
RRF_K = 60

#: How many candidates each leg contributes to the fusion pool.
LEG_POOL = 20

#: How many fused candidates the optional rerank stage rescores.
RERANK_POOL = 20

#: Maximum results per source BEFORE the final top-k cut, so one chatty
#: source cannot crowd out every other voice.
PER_SOURCE_CAP = 5

#: Recency bonus ceiling — a strict tiebreak, deliberately far below any
#: realistic difference between two distinct RRF sums.
_RECENCY_EPSILON = 1e-6


async def hybrid_search(
    store: Any,
    cfg: Any,
    query: str,
    *,
    k: int = 10,
    area_id: str | None = None,
) -> list[SearchResult]:
    """Fused keyword + vector search over an UltraWiki store.

    ``store`` is an :class:`~jarvis.ultrawiki.store.UltraStore` or
    :class:`~jarvis.ultrawiki.store.PostgresStore` (same public surface).
    Returns at most ``k`` :class:`SearchResult` rows, best first, each carrying
    the fused score and the ``matched_by`` tuple of the legs that produced it.
    An empty/blank query returns ``[]`` without touching any leg.
    """
    if not query or not query.strip():
        return []
    keyword_hits, vector_hits = await asyncio.gather(
        store.keyword_search(query, k=LEG_POOL, area_id=area_id),
        _vector_leg(store, cfg, query, area_id=area_id),
    )
    fused = _fuse(keyword_hits, vector_hits)
    if not fused:
        return []
    capped = _cap_per_source(fused)
    ordered = await _maybe_rerank(cfg, query, capped)
    return ordered[: max(0, int(k))]


async def search(
    *,
    store: Any,
    cfg: Any,
    query: str,
    k: int = 10,
    area_id: str | None = None,
) -> list[SearchResult]:
    """Keyword-only facade over :func:`hybrid_search`.

    This is the seam :meth:`UltraWikiService.search` delegates to; it exists
    so the service and the routes never depend on positional argument order.
    """
    return await hybrid_search(store, cfg, query, k=k, area_id=area_id)


def search_status(cfg: Any) -> dict[str, Any]:
    """Honest live report of the three retrieval legs, from config alone.

    ``keyword`` is always available (FTS ships with the store). ``vector``
    names the configured backend + model or the honest reason it is off.
    ``rerank`` names the configured provider or ``"off"``.
    """
    ultrawiki = getattr(cfg, "ultrawiki", None)
    status: dict[str, Any] = {"keyword": {"available": True}}

    provider = str(getattr(ultrawiki, "embedding_provider", "") or "").strip()
    if not provider:
        status["vector"] = {
            "available": False,
            "reason": (
                "no embedding provider is configured — semantic search is "
                "off and keyword search answers alone"
            ),
        }
    else:
        from jarvis.ultrawiki.embeddings import (  # noqa: PLC0415 — lazy (AP-26)
            DEFAULT_MODELS,
            EMBEDDING_BACKENDS,
        )

        model = _configured_model(ultrawiki, provider, DEFAULT_MODELS)
        factory = EMBEDDING_BACKENDS.get(provider)
        if factory is None:
            status["vector"] = {
                "available": False,
                "backend": provider,
                "reason": f"unknown embedding provider {provider!r}",
            }
        else:
            try:
                ready, reason = factory(cfg).ready()
            except Exception as exc:  # noqa: BLE001 — a broken probe reports, never raises
                ready, reason = False, f"readiness probe failed ({type(exc).__name__})"
            entry: dict[str, Any] = {
                "available": ready,
                "backend": provider,
                "model": model,
            }
            if not ready:
                entry["reason"] = reason
            status["vector"] = entry

    rerank_provider = str(getattr(ultrawiki, "rerank_provider", "") or "").strip()
    if not rerank_provider:
        status["rerank"] = {"available": False, "provider": "off"}
    else:
        from jarvis.ultrawiki.rerank import RERANK_BACKENDS  # noqa: PLC0415 — lazy (AP-26)

        factory = RERANK_BACKENDS.get(rerank_provider)
        if factory is None:
            status["rerank"] = {
                "available": False,
                "provider": rerank_provider,
                "reason": f"unknown rerank provider {rerank_provider!r}",
            }
        else:
            try:
                ready, reason = factory(cfg).ready()
            except Exception as exc:  # noqa: BLE001 — a broken probe reports, never raises
                ready, reason = False, f"readiness probe failed ({type(exc).__name__})"
            entry = {"available": ready, "provider": rerank_provider}
            if not ready:
                entry["reason"] = reason
            status["rerank"] = entry

    return status


# ---------------------------------------------------------------------------
# Legs
# ---------------------------------------------------------------------------


def _configured_model(ultrawiki: Any, provider: str, defaults: dict[str, str]) -> str:
    model = str(getattr(ultrawiki, "embedding_model", "") or "").strip()
    return model or defaults.get(provider, "")


async def _vector_leg(
    store: Any, cfg: Any, query: str, *, area_id: str | None
) -> list[SearchResult]:
    """Embed the query and run the store's ANN leg.

    Every degraded outcome (unconfigured, unknown, dead backend, store-side
    vector degradation) returns ``[]`` with a logged honest reason — the
    search itself never fails because of the vector leg.
    """
    ultrawiki = getattr(cfg, "ultrawiki", None)
    provider = str(getattr(ultrawiki, "embedding_provider", "") or "").strip()
    if not provider:
        return []
    from jarvis.ultrawiki.embeddings import (  # noqa: PLC0415 — lazy (AP-26)
        DEFAULT_MODELS,
        EMBEDDING_BACKENDS,
        EmbeddingError,
    )

    factory = EMBEDDING_BACKENDS.get(provider)
    if factory is None:
        log.warning("vector leg skipped: unknown embedding provider %r", provider)
        return []
    model = _configured_model(ultrawiki, provider, DEFAULT_MODELS)
    try:
        vectors = await factory(cfg).embed([query], model=model)
    except EmbeddingError as exc:
        log.info("vector leg skipped: %s", exc)
        return []
    except Exception:  # noqa: BLE001 — degrade to keyword-only, never fail the search
        log.warning(
            "vector leg skipped: query embedding raised unexpectedly",
            exc_info=True,
        )
        return []
    if not vectors or not vectors[0]:
        log.info("vector leg skipped: %s returned no query vector", provider)
        return []
    results, reason = await store.vector_search(vectors[0], k=LEG_POOL, area_id=area_id)
    if reason:
        log.info("vector leg degraded: %s", reason)
    return results


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def _recency_key(timestamp_utc: Any) -> float:
    """Epoch seconds for the recency tiebreak; unparsable stamps sort last."""
    try:
        parsed = datetime.fromisoformat(str(timestamp_utc).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _fuse(keyword_hits: list[SearchResult], vector_hits: list[SearchResult]) -> list[SearchResult]:
    """RRF-fuse the two ranked lists; merge duplicates by ``item_id``.

    The fused score is ``sum(1 / (RRF_K + rank))`` over the legs the item
    appeared in, plus a strictly-tiebreak-sized recency bonus derived from the
    candidates themselves (newer ``timestamp_utc`` wins ties — no extra DB
    query). ``matched_by`` is the union of the contributing legs; snippet and
    citation fields come from the keyword occurrence when both legs matched
    (its snippet is query-specific).
    """
    rrf_score: dict[int, float] = {}
    matched: dict[int, list[str]] = {}
    representative: dict[int, SearchResult] = {}
    for leg_name, hits in (("keyword", keyword_hits), ("vector", vector_hits)):
        for rank, hit in enumerate(hits, start=1):
            rrf_score[hit.item_id] = rrf_score.get(hit.item_id, 0.0) + 1.0 / (RRF_K + rank)
            labels = matched.setdefault(hit.item_id, [])
            if leg_name not in labels:
                labels.append(leg_name)
            representative.setdefault(hit.item_id, hit)
    if not representative:
        return []

    by_recency = sorted(
        representative,
        key=lambda item_id: _recency_key(representative[item_id].timestamp_utc),
        reverse=True,
    )
    total = len(by_recency)
    recency_bonus = {
        item_id: _RECENCY_EPSILON * (total - index) / total
        for index, item_id in enumerate(by_recency)
    }

    fused = [
        replace(
            base,
            score=rrf_score[item_id] + recency_bonus[item_id],
            matched_by=tuple(matched[item_id]),
        )
        for item_id, base in representative.items()
    ]
    fused.sort(key=lambda result: (-result.score, result.item_id))
    return fused


def _cap_per_source(ranked: list[SearchResult]) -> list[SearchResult]:
    """Keep at most :data:`PER_SOURCE_CAP` results per source, best first."""
    kept: list[SearchResult] = []
    per_source: dict[str, int] = {}
    for hit in ranked:
        count = per_source.get(hit.source_id, 0)
        if count >= PER_SOURCE_CAP:
            continue
        per_source[hit.source_id] = count + 1
        kept.append(hit)
    return kept


# ---------------------------------------------------------------------------
# Optional rerank
# ---------------------------------------------------------------------------


def _rerank_document(hit: SearchResult) -> str:
    return f"{hit.title}\n{hit.snippet}".strip()


async def _maybe_rerank(cfg: Any, query: str, ranked: list[SearchResult]) -> list[SearchResult]:
    """Reorder the top of the fused list with the configured reranker.

    Skipped honestly when no provider is configured / ready. Only the ORDER
    changes — every result keeps its fused score. On :class:`RerankError`
    the fusion order stands (logged once, the search never fails).
    """
    ultrawiki = getattr(cfg, "ultrawiki", None)
    if not str(getattr(ultrawiki, "rerank_provider", "") or "").strip():
        return ranked
    from jarvis.ultrawiki import rerank as rerank_mod  # noqa: PLC0415 — lazy (AP-26)

    reranker = rerank_mod.resolve_reranker(cfg)
    if reranker is None:
        # resolve_reranker already logged the honest skip reason.
        return ranked
    pool = ranked[:RERANK_POOL]
    documents = [_rerank_document(hit) for hit in pool]
    try:
        pairs = await reranker.rerank(query, documents, top_k=len(documents))
    except rerank_mod.RerankError as exc:
        log.warning("rerank failed (%s) — keeping the fusion order", exc)
        return ranked
    reordered: list[SearchResult] = []
    seen: set[int] = set()
    for index, _score in pairs:
        if 0 <= index < len(pool) and index not in seen:
            seen.add(index)
            reordered.append(pool[index])
    for index, hit in enumerate(pool):
        if index not in seen:  # providers may return fewer rows than asked
            reordered.append(hit)
    return reordered + ranked[RERANK_POOL:]
