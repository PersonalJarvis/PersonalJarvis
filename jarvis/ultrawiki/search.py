"""UltraWiki hybrid search — the ranking pipeline of design doc 03.

The read path, stage by stage (the Cerebras knowledge-base pipeline, adapted
to a single-user store):

    keyword leg  ·  vector leg        (concurrent, plus the IDF probe)
              │
              ▼
    RRF FUSION  score(d) = Σ_legs weight / (60 + rank)
      × term-rarity signal   × age decay   + recency tiebreak
      dedupe by item · cap per source
              │
              ▼
    RERANK      absolute 0-10 grade over the top ~20, keep the top ~10
              │
              ▼
    RELEVANCE FLOOR (unsolicited surfaces only)
              │
              ▼
    CONTEXT EXPANSION  neighbouring messages/sections pulled back in

Two properties are load-bearing:

**RRF is ordinal, the rerank grade is absolute.** A fusion over garbage still
produces a confident-looking number one, so no fused score can ever mean
"nothing here is relevant". Only the rerank grade can, which is why
``enforce_floor`` gates on it and NOT on the fused score — the "Bugatti case"
lesson the normal wiki already paid for once.

**Nothing here may brick a search.** Every stage degrades on its own: no/dead
embedding provider => keyword answers alone; no/dead rerank provider => fusion
order stands; a store without the ranking-signal methods => neutral weights; a
failing context expansion => bare snippets. ``matched_by`` and ``rerank_score``
report honestly what actually ran.

Heavy/optional pieces (httpx-backed embedding + rerank adapters) are imported
lazily inside the functions (AP-26); this module itself is stdlib + types only.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from jarvis.ultrawiki.types import SearchResult

log = logging.getLogger(__name__)

__all__ = ["hybrid_search", "search", "search_status", "ranking_settings"]

#: RRF smoothing constant (design doc 03: score(d) = sum 1 / (60 + rank)).
RRF_K = 60

#: How many candidates each leg contributes to the fusion pool.
LEG_POOL = 20

#: How many fused candidates the rerank stage grades (the article's "more
#: diverse top twenty").
RERANK_POOL = 20

#: How many graded candidates keep their rerank order ("keep the top ten").
#: Anything below stays available in fusion order, so a caller asking for more
#: than ten results still gets them.
RERANK_KEEP = 10

#: Maximum results per source BEFORE the final top-k cut, so one chatty
#: source cannot crowd out every other voice.
PER_SOURCE_CAP = 5

#: How many neighbouring snippets one winner may pull back in.
CONTEXT_NEIGHBORS = 2

#: Recency bonus ceiling — a strict tiebreak, deliberately far below any
#: realistic difference between two distinct RRF sums. Independent of the
#: configurable half-life decay below.
_RECENCY_EPSILON = 1e-6

#: A query term counts as RARE when it occurs in no more than this fraction of
#: the live corpus. Deliberately relative: the article's absolute IDF>=4.0
#: threshold assumes a company-sized corpus and would mark nothing as rare in a
#: personal store of a few hundred items.
_RARE_DF_RATIO = 0.25

#: Lower bound of the term-rarity factor. A candidate that covers none of the
#: query's rare vocabulary is pushed DOWN, never out — a hard content-based
#: drop is the AP-27 trap (tightening on content kills recall), and the rerank
#: stage right after this is the place where real filtering belongs.
_SIGNAL_FLOOR = 0.6

#: Extra penalty for the filler case the article calls out ("sounds good,
#: thanks!"): short AND covering none of the rare query vocabulary.
_FILLER_CHARS = 200
_FILLER_PENALTY = 0.5

#: Query tokenizer: unicode-aware word characters, so German, Spanish, and any
#: other supported locale tokenize the same way (no ASCII-only class).
_TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)

_SECONDS_PER_DAY = 86400.0


def _uw(cfg: Any) -> Any:
    return getattr(cfg, "ultrawiki", None)


def _float_setting(cfg: Any, name: str, default: float) -> float:
    """One ``[ultrawiki]`` float knob, defaulting on anything unusable."""
    try:
        value = float(getattr(_uw(cfg), name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def ranking_settings(cfg: Any) -> dict[str, float]:
    """The live ranking knobs — surfaced by ``search_status`` so the CLI and
    the UI can show what the ranking is actually doing."""
    return {
        "keyword_weight": max(0.0, _float_setting(cfg, "rrf_keyword_weight", 1.0)),
        "vector_weight": max(0.0, _float_setting(cfg, "rrf_vector_weight", 1.0)),
        "recency_half_life_days": max(
            0.0, _float_setting(cfg, "recency_half_life_days", 180.0)
        ),
        "rerank_min_score": max(0.0, _float_setting(cfg, "rerank_min_score", 4.0)),
    }


async def hybrid_search(
    store: Any,
    cfg: Any,
    query: str,
    *,
    k: int = 10,
    area_id: str | None = None,
    rerank: bool = True,
    enforce_floor: bool = False,
    expand_context: bool = True,
) -> list[SearchResult]:
    """Fused keyword + vector search over an UltraWiki store.

    ``store`` is an :class:`~jarvis.ultrawiki.store.UltraStore` or
    :class:`~jarvis.ultrawiki.store.PostgresStore` (same public surface).
    Returns at most ``k`` :class:`SearchResult` rows, best first, each carrying
    the fused score, the ``matched_by`` tuple of the legs that produced it, the
    absolute ``rerank_score`` when the stage ran, and its ``context``.

    ``rerank=False`` skips the grading stage — used by the realtime voice path,
    whose latency budget (doc 03) does not admit an extra model call.

    ``enforce_floor=True`` is for UNSOLICITED surfaces (context injection into
    the brain prompt, volunteered voice answers): candidates graded below
    ``[ultrawiki].rerank_min_score`` are dropped and an empty list is an honest
    "I have nothing on that". It never silently passes everything: when the
    grade is unavailable the caller gets results with ``rerank_score=None`` and
    must apply its own deterministic gate (``jarvis.brain.wiki_relevance``).

    An empty/blank query returns ``[]`` without touching any leg.
    """
    if not query or not query.strip():
        return []
    keyword_hits, vector_hits, signals = await asyncio.gather(
        store.keyword_search(query, k=LEG_POOL, area_id=area_id),
        _vector_leg(store, cfg, query, area_id=area_id),
        _term_signals(store, query),
    )
    fused = _fuse(keyword_hits, vector_hits, cfg=cfg, signals=signals)
    if not fused:
        return []
    capped = _cap_per_source(fused)
    ordered = await _maybe_rerank(cfg, query, capped) if rerank else capped
    if enforce_floor:
        ordered = _apply_relevance_floor(cfg, ordered)
    top = ordered[: max(0, int(k))]
    if expand_context and top:
        top = await _expand_context(store, top)
    return top


async def search(
    *,
    store: Any,
    cfg: Any,
    query: str,
    k: int = 10,
    area_id: str | None = None,
    rerank: bool = True,
    enforce_floor: bool = False,
    expand_context: bool = True,
) -> list[SearchResult]:
    """Keyword-argument facade over :func:`hybrid_search`.

    This is the seam :meth:`UltraWikiService.search` delegates to; it exists
    so the service and the routes never depend on positional argument order.
    """
    return await hybrid_search(
        store,
        cfg,
        query,
        k=k,
        area_id=area_id,
        rerank=rerank,
        enforce_floor=enforce_floor,
        expand_context=expand_context,
    )


def search_status(cfg: Any) -> dict[str, Any]:
    """Honest live report of the retrieval legs + the ranking knobs.

    ``keyword`` is always available (FTS ships with the store). ``vector``
    names the configured backend + model or the honest reason it is off.
    ``rerank`` names the configured provider or ``"off"``. ``ranking`` echoes
    the live weights, half-life, and relevance floor.
    """
    ultrawiki = _uw(cfg)
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

    status["ranking"] = ranking_settings(cfg)
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
    ultrawiki = _uw(cfg)
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
# Term rarity (IDF)
# ---------------------------------------------------------------------------


def query_terms(query: str) -> list[str]:
    """Distinct lowercased query tokens, order preserved."""
    return list(dict.fromkeys(match.group(0).lower() for match in _TOKEN_RE.finditer(query)))


async def _term_signals(store: Any, query: str) -> dict[str, float]:
    """IDF weight per RARE query term — the article's "separate signal from
    filler" leg, computed against this store's own corpus.

    Returns ``{}`` (neutral, no reordering) whenever the corpus is too small
    to be meaningful, the store predates these methods (third-party stores,
    fakes), or the probe fails. Never raises, never fails a search.
    """
    terms = query_terms(query)
    if not terms:
        return {}
    count_fn = getattr(store, "live_item_count", None)
    df_fn = getattr(store, "term_document_frequency", None)
    if not callable(count_fn) or not callable(df_fn):
        return {}
    try:
        total = int(await count_fn())
        if total <= 0:
            return {}
        frequencies = await df_fn(terms)
    except Exception:  # noqa: BLE001 — a signal probe never fails the search
        log.debug("term-rarity probe failed — ranking without it", exc_info=True)
        return {}
    rare_cutoff = max(1.0, total * _RARE_DF_RATIO)
    weights: dict[str, float] = {}
    for term in terms:
        try:
            df = int(frequencies.get(term, 0))
        except (TypeError, ValueError):
            continue
        if df <= 0 or df > rare_cutoff:
            continue  # absent from the corpus, or common enough to be filler
        weights[term] = math.log(total / df)
    return {term: weight for term, weight in weights.items() if weight > 0.0}


def _signal_factor(hit: SearchResult, signals: dict[str, float]) -> float:
    """How much of the query's RARE vocabulary this candidate actually shows.

    Caveat worth knowing: the candidate text available here is its snippet, so
    a rare term living outside the snippet is invisible and the candidate is
    scaled down despite being relevant. That is why the factor is floored at
    :data:`_SIGNAL_FLOOR` and why the rerank stage — which sees the same text
    but judges meaning — runs immediately after.
    """
    if not signals:
        return 1.0
    text = f"{hit.title} {hit.snippet}"
    present = set(query_terms(text))
    total_weight = sum(signals.values())
    if total_weight <= 0:
        return 1.0
    covered = sum(weight for term, weight in signals.items() if term in present)
    coverage = covered / total_weight
    factor = _SIGNAL_FLOOR + (1.0 - _SIGNAL_FLOOR) * coverage
    if coverage <= 0.0 and len(text.strip()) < _FILLER_CHARS:
        factor *= _FILLER_PENALTY
    return factor


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


def _age_factor(timestamp_utc: Any, half_life_days: float, *, now: float) -> float:
    """``0.5 ** (age_days / half_life)`` — the article's age decay.

    ``half_life_days <= 0`` disables the decay. An unparsable or future stamp
    is never punished (factor 1.0): honesty over guessing.
    """
    if half_life_days <= 0:
        return 1.0
    stamp = _recency_key(timestamp_utc)
    if stamp == float("-inf"):
        return 1.0
    age_days = (now - stamp) / _SECONDS_PER_DAY
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def _fuse(
    keyword_hits: list[SearchResult],
    vector_hits: list[SearchResult],
    *,
    cfg: Any = None,
    signals: dict[str, float] | None = None,
) -> list[SearchResult]:
    """RRF-fuse the two ranked lists; merge duplicates by ``item_id``.

    The fused score is ``sum(weight / (RRF_K + rank))`` over the legs the item
    appeared in, scaled by the term-rarity signal and the age decay, plus a
    strictly-tiebreak-sized recency bonus derived from the candidates
    themselves (newer ``timestamp_utc`` wins ties — no extra DB query).
    ``matched_by`` is the union of the contributing legs; snippet and citation
    fields come from the keyword occurrence when both legs matched (its
    snippet is query-specific).
    """
    knobs = ranking_settings(cfg)
    weights = {"keyword": knobs["keyword_weight"], "vector": knobs["vector_weight"]}
    half_life = knobs["recency_half_life_days"]

    rrf_score: dict[int, float] = {}
    matched: dict[int, list[str]] = {}
    representative: dict[int, SearchResult] = {}
    for leg_name, hits in (("keyword", keyword_hits), ("vector", vector_hits)):
        weight = weights[leg_name]
        for rank, hit in enumerate(hits, start=1):
            rrf_score[hit.item_id] = (
                rrf_score.get(hit.item_id, 0.0) + weight / (RRF_K + rank)
            )
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

    now = datetime.now(UTC).timestamp()
    fused = [
        replace(
            base,
            score=(
                rrf_score[item_id]
                * _signal_factor(base, signals or {})
                * _age_factor(base.timestamp_utc, half_life, now=now)
                + recency_bonus[item_id]
            ),
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
# Rerank
# ---------------------------------------------------------------------------


def _rerank_document(hit: SearchResult) -> str:
    return f"{hit.title}\n{hit.snippet}".strip()


async def _maybe_rerank(cfg: Any, query: str, ranked: list[SearchResult]) -> list[SearchResult]:
    """Grade the top of the fused list with the configured reranker.

    Skipped honestly when no provider is configured / ready. Graded candidates
    are reordered by their ABSOLUTE 0-10 relevance and carry it in
    ``rerank_score``; the fused ``score`` is left untouched so both signals
    stay inspectable. Only the first :data:`RERANK_KEEP` graded candidates
    take their new order — the rest stay in fusion order behind them, which is
    the article's "keep the top ten" without truncating a larger request. On
    :class:`RerankError` the fusion order stands (logged once, never fatal).
    """
    ultrawiki = _uw(cfg)
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
    except Exception:  # noqa: BLE001 — an optional stage never fails the search
        log.warning(
            "rerank raised unexpectedly — keeping the fusion order", exc_info=True
        )
        return ranked

    graded: list[tuple[int, SearchResult]] = []
    seen: set[int] = set()
    for index, score in pairs:
        if 0 <= index < len(pool) and index not in seen:
            seen.add(index)
            graded.append((index, replace(pool[index], rerank_score=float(score))))
    # The top ten take their graded order. Everything below it — the tail of
    # the graded list and whatever the model never mentioned — falls back to
    # fusion order, so a caller asking for more than ten still gets a sane
    # ranking instead of an arbitrary one.
    head = [hit for _, hit in graded[:RERANK_KEEP]]
    tail = graded[RERANK_KEEP:] + [
        (index, hit) for index, hit in enumerate(pool) if index not in seen
    ]
    tail.sort(key=lambda pair: pair[0])
    return head + [hit for _, hit in tail] + ranked[RERANK_POOL:]


def _apply_relevance_floor(cfg: Any, ranked: list[SearchResult]) -> list[SearchResult]:
    """Drop candidates graded below ``[ultrawiki].rerank_min_score``.

    Only GRADED candidates are judged: an ungraded one (stage skipped, failed,
    or beyond the rerank pool) has no absolute evidence for or against it and
    is passed through unchanged, because a floor that silently disappears on
    provider failure is worse than a visible one. The caller's deterministic
    gate stays responsible for those (``jarvis.brain.wiki_relevance``).
    """
    floor = ranking_settings(cfg)["rerank_min_score"]
    if floor <= 0:
        return ranked
    kept = [
        hit for hit in ranked if hit.rerank_score is None or hit.rerank_score >= floor
    ]
    dropped = len(ranked) - len(kept)
    if dropped:
        log.debug("relevance floor %.1f dropped %d graded candidate(s)", floor, dropped)
    return kept


# ---------------------------------------------------------------------------
# Context expansion
# ---------------------------------------------------------------------------


async def _expand_context(store: Any, top: list[SearchResult]) -> list[SearchResult]:
    """Re-hydrate the winners with their neighbouring evidence.

    Strictly best-effort and last (design doc 03): a store without
    ``neighbors_for`` — third-party implementations, test fakes — or a failing
    lookup leaves ``context=()`` and never disturbs the ranking above it.
    """
    neighbors_fn = getattr(store, "neighbors_for", None)
    if not callable(neighbors_fn):
        return top

    async def _for(hit: SearchResult) -> tuple[str, ...]:
        try:
            rows = await neighbors_fn(hit.item_id, limit=CONTEXT_NEIGHBORS)
        except Exception:  # noqa: BLE001 — context is a bonus, never a failure
            log.debug("context expansion failed for item %s", hit.item_id, exc_info=True)
            return ()
        return tuple(str(row) for row in (rows or []) if str(row).strip())

    contexts = await asyncio.gather(*(_for(hit) for hit in top))
    return [
        replace(hit, context=context) if context else hit
        for hit, context in zip(top, contexts, strict=True)
    ]
