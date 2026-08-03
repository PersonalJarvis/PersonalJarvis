"""UltraWiki hybrid search — the ranking pipeline of design doc 03.

The read path, stage by stage (the Cerebras knowledge-base pipeline, adapted
to a single-user store):

    keyword leg  ·  vector leg  ·  event leg   (concurrent, + the IDF probe)
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
store without the episodic tables => no event leg; a failing context expansion
=> bare snippets. ``matched_by`` and ``rerank_score`` report honestly what
actually ran.

**Events are a leg, not an oracle.** The episodic leg (design doc 01,
``uw_events``) contributes precomputed answers to "when did X happen" — each
one already carrying its absolute date, its place and its participants — but
it is fused like every other list rather than allowed to veto them. Its hits
are ranked by the event's OWN ``occurred_at``, which is the date the question
was about; when an event and its evidence item both match, the event's card
becomes the representative, because a bare fragment of chat is the worse
citation for an episodic question.

Heavy/optional pieces (httpx-backed embedding + rerank adapters) are imported
lazily inside the functions (AP-26); this module itself is stdlib + types only.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from weakref import WeakKeyDictionary

from jarvis.ultrawiki.types import SearchResult

log = logging.getLogger(__name__)

__all__ = [
    "hybrid_search",
    "search",
    "search_status",
    "ranking_settings",
    "embed_query_vector",
    "fuse_legs",
    "query_terms",
]

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

#: A search slower than this logs its stage breakdown at INFO instead of
#: DEBUG, so a stalling leg is visible in a normal log without turning on
#: debug logging first.
_SLOW_SEARCH_MS = 1000.0


async def _timed(name: str, awaitable: Any, sink: dict[str, float]) -> Any:
    """Await ``awaitable`` and record its wall-clock cost in ``sink`` (ms).

    Recording happens in ``finally`` so a degraded or raising stage still
    reports the time it burned — the whole point of measuring is seeing
    where a SLOW failure spent its budget.
    """
    started = time.perf_counter()
    try:
        return await awaitable
    finally:
        sink[name] = round((time.perf_counter() - started) * 1000.0, 1)


def _finish_timings(sink: dict[str, float], started: float, *, results: int) -> None:
    """Close the timing record and log one honest per-stage breakdown."""
    sink["total_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    stages = ", ".join(
        f"{name[: -len('_ms')]} {value:.0f}"
        for name, value in sink.items()
        if name != "total_ms"
    )
    level = logging.INFO if sink["total_ms"] >= _SLOW_SEARCH_MS else logging.DEBUG
    log.log(
        level,
        "hybrid search took %.0f ms (%s) -> %d result(s)",
        sink["total_ms"],
        stages or "no stages ran",
        results,
    )


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
        "event_weight": max(0.0, _float_setting(cfg, "rrf_event_weight", 1.0)),
        "rerank_min_score": max(0.0, _float_setting(cfg, "rerank_min_score", 4.0)),
        # Hard wall-clock bound on the WHOLE rerank stage (all provider
        # attempts together). Without it, a chain of dead/hung providers can
        # burn its per-attempt timeout once per family — minutes, not the
        # design's sub-second budget. 0 disables the bound.
        "rerank_timeout_s": max(0.0, _float_setting(cfg, "rerank_timeout_s", 10.0)),
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
    vector_timeout_s: float | None = None,
    timings: dict[str, float] | None = None,
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

    ``vector_timeout_s`` (optional) bounds ONLY the vector leg: when it
    cannot deliver inside the budget (a slow or cold embedding provider,
    usually), the search proceeds on the keyword leg instead of holding the
    whole answer hostage. This is what lets a caller with a hard overall
    budget — the context injector, the voice path — still get keyword
    results when the network is having a day. ``None``/``0`` leaves the leg
    unbounded (explicit surfaces).

    ``timings`` (optional) is filled with the wall-clock cost of every stage
    that ran, in milliseconds (``keyword_ms``, ``vector_ms`` with its
    ``vector_embed_ms`` / ``vector_ann_ms`` split, ``event_ms``,
    ``signals_ms``, ``rerank_ms``, ``context_ms``, ``total_ms``) — the query
    text itself is never logged, only durations and counts.

    An empty/blank query returns ``[]`` without touching any leg.
    """
    if not query or not query.strip():
        return []
    sink: dict[str, float] = timings if timings is not None else {}
    started = time.perf_counter()
    vector_coro = _vector_leg(store, cfg, query, area_id=area_id, timings=sink)
    if vector_timeout_s and vector_timeout_s > 0:
        vector_coro = _bounded_vector_leg(vector_coro, vector_timeout_s)
    keyword_hits, vector_hits, event_hits, signals = await asyncio.gather(
        _timed("keyword_ms", store.keyword_search(query, k=LEG_POOL, area_id=area_id), sink),
        _timed("vector_ms", vector_coro, sink),
        _timed("event_ms", _event_leg(store, cfg, query, area_id=area_id), sink),
        _timed("signals_ms", _term_signals(store, query), sink),
    )
    fused = _fuse(
        keyword_hits, vector_hits, cfg=cfg, signals=signals, event_hits=event_hits
    )
    if not fused:
        _finish_timings(sink, started, results=0)
        return []
    capped = _cap_per_source(fused)
    # Timed only when a provider is actually configured: an unconfigured
    # rerank stage is a no-op, and a "rerank 0" entry in the breakdown would
    # claim a stage ran that never attempted work.
    if rerank and str(getattr(_uw(cfg), "rerank_provider", "") or "").strip():
        ordered = await _timed("rerank_ms", _maybe_rerank(cfg, query, capped), sink)
    else:
        ordered = capped
    if enforce_floor:
        ordered = _apply_relevance_floor(cfg, ordered)
    top = ordered[: max(0, int(k))]
    if expand_context and top:
        top = await _timed("context_ms", _expand_context(store, top), sink)
    _finish_timings(sink, started, results=len(top))
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
    vector_timeout_s: float | None = None,
    timings: dict[str, float] | None = None,
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
        vector_timeout_s=vector_timeout_s,
        timings=timings,
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


#: In-process LRU of query-text embeddings, keyed by (provider, model,
#: whitespace-normalized lowercased query). Embedding the query is usually the
#: only network round trip on the read path, and the SAME query text is
#: embedded repeatedly — the context injector and the search tool both run it
#: within one turn, and users re-ask questions. In-memory only, deliberately:
#: persisting query texts (or vectors keyed by them) to disk would write a
#: trace of what the user asked their memory, which the log-privacy rule above
#: exists to prevent.
_QUERY_VECTOR_CACHE: dict[tuple[str, str, str], list[float]] = {}
_QUERY_VECTOR_CACHE_MAX = 256
_QUERY_VECTOR_INFLIGHT: dict[
    tuple[str, str, str], asyncio.Task[list[list[float]]]
] = {}

#: The exact same semantic query often arrives twice in one turn (context
#: injection + explicit tool/UI). sqlite-vec is an exact scan on the universal
#: SQLite floor, so two concurrent 3,072-dimensional scans made each other
#: several times slower on the live corpus. Coalesce in-flight work and retain
#: a tiny, short-lived result cache; it is memory-only and bounded.
_VECTOR_RESULT_TTL_S = 10.0
_VECTOR_RESULT_CACHE_MAX = 64
_VECTOR_RESULT_CACHE: WeakKeyDictionary[
    Any,
    dict[tuple[str, str, str, str], tuple[float, tuple[list[SearchResult], str]]],
] = WeakKeyDictionary()
_VECTOR_RESULT_INFLIGHT: WeakKeyDictionary[
    Any,
    dict[
        tuple[str, str, str, str],
        asyncio.Task[tuple[list[SearchResult], str]],
    ],
] = WeakKeyDictionary()


def _query_cache_key(provider: str, model: str, query: str) -> tuple[str, str, str]:
    return (provider, model, " ".join(query.split()).lower())


def _cached_query_vector(key: tuple[str, str, str]) -> list[float] | None:
    vector = _QUERY_VECTOR_CACHE.get(key)
    if vector is not None:
        # Refresh LRU position (dicts iterate in insertion order).
        _QUERY_VECTOR_CACHE[key] = _QUERY_VECTOR_CACHE.pop(key)
    return vector


def _remember_query_vector(key: tuple[str, str, str], vector: list[float]) -> None:
    _QUERY_VECTOR_CACHE[key] = vector
    while len(_QUERY_VECTOR_CACHE) > _QUERY_VECTOR_CACHE_MAX:
        del _QUERY_VECTOR_CACHE[next(iter(_QUERY_VECTOR_CACHE))]


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    """Retrieve a detached shared task's exception after all waiters cancel."""
    if task.cancelled():
        return
    task.exception()


async def _embed_query_once(
    key: tuple[str, str, str], backend: Any, query: str, model: str
) -> list[list[float]]:
    """Coalesce concurrent misses for one query embedding."""
    task = _QUERY_VECTOR_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(
            backend.embed([query], model=model),
            name="ultrawiki-query-embedding",
        )
        _QUERY_VECTOR_INFLIGHT[key] = task
        task.add_done_callback(_consume_task_exception)
    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and _QUERY_VECTOR_INFLIGHT.get(key) is task:
            _QUERY_VECTOR_INFLIGHT.pop(key, None)


async def _run_vector_search_once(
    store: Any,
    key: tuple[str, str, str, str],
    query_vector: list[float],
    area_id: str | None,
) -> tuple[list[SearchResult], str]:
    """Own one physical ANN scan and publish its short-lived result."""
    try:
        result = await store.vector_search(
            query_vector, k=LEG_POOL, area_id=area_id
        )
        cache = _VECTOR_RESULT_CACHE.setdefault(store, {})
        cache[key] = (time.monotonic() + _VECTOR_RESULT_TTL_S, result)
        while len(cache) > _VECTOR_RESULT_CACHE_MAX:
            del cache[next(iter(cache))]
        return result
    finally:
        inflight = _VECTOR_RESULT_INFLIGHT.get(store)
        current = asyncio.current_task()
        if inflight is not None and inflight.get(key) is current:
            inflight.pop(key, None)


async def _vector_search_once(
    store: Any,
    key: tuple[str, str, str, str],
    query_vector: list[float],
    area_id: str | None,
) -> tuple[list[SearchResult], str]:
    """Serve a recent identical scan or share the one already running."""
    cache = _VECTOR_RESULT_CACHE.setdefault(store, {})
    cached = cache.get(key)
    if cached is not None:
        if cached[0] > time.monotonic():
            cache[key] = cache.pop(key)
            return cached[1]
        cache.pop(key, None)
    inflight = _VECTOR_RESULT_INFLIGHT.setdefault(store, {})
    task = inflight.get(key)
    if task is None:
        task = asyncio.create_task(
            _run_vector_search_once(store, key, query_vector, area_id),
            name="ultrawiki-vector-search",
        )
        inflight[key] = task
        task.add_done_callback(_consume_task_exception)
    return await asyncio.shield(task)


async def _bounded_vector_leg(
    leg: Any, timeout_s: float
) -> list[SearchResult]:
    """Race the vector leg against its budget; a bust degrades to no vector
    hits (keyword answers alone) instead of stalling the whole search."""
    try:
        return await asyncio.wait_for(leg, timeout=timeout_s)
    except TimeoutError:
        log.info(
            "vector leg exceeded its %.0f ms budget — continuing keyword-only",
            timeout_s * 1000.0,
        )
        return []


async def embed_query_vector(
    cfg: Any, query: str, *, timings: dict[str, float] | None = None
) -> tuple[list[float] | None, str]:
    """Embed one piece of QUERY text through the configured backend.

    Returns ``(vector, reason)``. ``vector`` is ``None`` whenever the
    embedding could not be produced, and ``reason`` then carries an honest,
    credential-free English sentence — no caller of this function may fail
    because embeddings are unavailable, they must degrade.

    Shared by the vector leg and the word lexicon on purpose: both embed short
    query text, both benefit from the same in-process cache, and both must see
    the identical verdict about whether the slot is usable. ``timings``, when
    given, records the call under ``vector_embed_ms``.
    """
    sink: dict[str, float] = timings if timings is not None else {}
    ultrawiki = _uw(cfg)
    provider = str(getattr(ultrawiki, "embedding_provider", "") or "").strip()
    if not provider:
        return None, (
            "no embedding provider is configured — pick one in the UltraWiki "
            "settings; keyword search keeps working"
        )
    from jarvis.ultrawiki.embeddings import (  # noqa: PLC0415 — lazy (AP-26)
        DEFAULT_MODELS,
        EMBEDDING_BACKENDS,
        EmbeddingError,
    )

    factory = EMBEDDING_BACKENDS.get(provider)
    if factory is None:
        return None, f"unknown embedding provider {provider!r}"
    model = _configured_model(ultrawiki, provider, DEFAULT_MODELS)
    cache_key = _query_cache_key(provider, model, query)
    cached = _cached_query_vector(cache_key)
    if cached is not None:
        log.debug("query vector served from the in-process cache")
        return cached, ""
    try:
        vectors = await _timed(
            "vector_embed_ms",
            _embed_query_once(cache_key, factory(cfg), query, model),
            sink,
        )
    except EmbeddingError as exc:
        # Not swallowed: the provider's own honest sentence becomes this
        # function's ``reason`` and every caller either logs it or shows it to
        # the user. Logging here as well would print the same outage twice per
        # search — the noise that hid a real one once.
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 — degrade, never fail the caller
        log.warning(
            "query embedding raised unexpectedly", exc_info=True
        )
        return None, f"the embedding provider failed ({type(exc).__name__})"
    if not vectors or not vectors[0]:
        return None, f"{provider} returned no query vector"
    query_vector = list(vectors[0])
    _remember_query_vector(cache_key, query_vector)
    return query_vector, ""


def query_vector_cache_key(cfg: Any, query: str) -> tuple[str, str, str]:
    """The cache identity of one query text — also the ANN result cache key."""
    ultrawiki = _uw(cfg)
    provider = str(getattr(ultrawiki, "embedding_provider", "") or "").strip()
    from jarvis.ultrawiki.embeddings import DEFAULT_MODELS  # noqa: PLC0415 — lazy (AP-26)

    return _query_cache_key(provider, _configured_model(ultrawiki, provider, DEFAULT_MODELS), query)


async def _vector_leg(
    store: Any,
    cfg: Any,
    query: str,
    *,
    area_id: str | None,
    timings: dict[str, float] | None = None,
) -> list[SearchResult]:
    """Embed the query and run the store's ANN leg.

    Every degraded outcome (unconfigured, unknown, dead backend, store-side
    vector degradation) returns ``[]`` with a logged honest reason — the
    search itself never fails because of the vector leg.

    ``timings`` splits the leg's cost into ``vector_embed_ms`` (the query
    embedding, usually a network round trip) and ``vector_ann_ms`` (the
    store's vector query) — the split that tells a slow provider apart from
    a slow database.
    """
    sink: dict[str, float] = timings if timings is not None else {}
    if not str(getattr(_uw(cfg), "embedding_provider", "") or "").strip():
        return []
    query_vector, reason = await embed_query_vector(cfg, query, timings=sink)
    if query_vector is None:
        log.info("vector leg skipped: %s", reason)
        return []
    cache_key = query_vector_cache_key(cfg, query)
    result_key = (*cache_key, str(area_id or ""))
    results, reason = await _timed(
        "vector_ann_ms",
        _vector_search_once(store, result_key, query_vector, area_id),
        sink,
    )
    if reason:
        log.info("vector leg degraded: %s", reason)
    return results


# ---------------------------------------------------------------------------
# Term rarity (IDF)
# ---------------------------------------------------------------------------


async def _event_leg(
    store: Any, cfg: Any, query: str, *, area_id: str | None = None
) -> list[SearchResult]:
    """The episodic leg: precomputed events matched by their keyword card.

    Silent and empty on every store that does not have one — a third-party
    backend, a test fake, an install whose corpus predates the event tables.
    A leg that raises would take the whole search down with it for a feature
    that is, by design, an accelerator.
    """
    if ranking_settings(cfg)["event_weight"] <= 0:
        return []
    search_events = getattr(store, "search_events", None)
    if not callable(search_events):
        return []
    try:
        hits = await search_events(query, k=LEG_POOL, area_id=area_id)
    except Exception:  # noqa: BLE001 — an optional leg never fails a search
        log.debug("event leg unavailable", exc_info=True)
        return []
    return list(hits or [])


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
    event_hits: list[SearchResult] | None = None,
) -> list[SearchResult]:
    """RRF-fuse the ranked lists; merge duplicates by ``item_id``.

    The fused score is ``sum(weight / (RRF_K + rank))`` over the legs the item
    appeared in, scaled by the term-rarity signal and the age decay, plus a
    strictly-tiebreak-sized recency bonus derived from the candidates
    themselves (newer ``timestamp_utc`` wins ties — no extra DB query).
    ``matched_by`` is the union of the contributing legs.

    The representative (the row whose title, snippet and timestamp survive the
    merge) is taken from the FIRST leg that produced the item, and the legs
    are walked event → keyword → vector on purpose: an event card states the
    date, the place and who was there, a keyword snippet is query-specific
    prose, and a vector hit is neither. For an episodic question that ordering
    is the difference between "on 14 March 2026 in Porto Verde with …" and a
    fragment of the chat that happened to mention it.

    Two properties the event leg makes non-obvious, both enforced here:

    - **One item, one vote per leg.** An itinerary can produce five events, and
      five rows for one ``item_id`` would otherwise stack five RRF
      contributions where every other item gets one. Only an item's BEST rank
      in a leg counts, and ranks are dense over distinct items, so a
      many-event item cannot push the items behind it down either.
    - **Ranking decays the RECORD, not the memory.** The age factor reads
      ``recorded_utc`` (when the item entered the corpus), never the event's
      ``occurred_at``. A note written yesterday about a dinner in 2023 is a
      fresh record; decaying it by the dinner's date demotes it ~65× for
      having remembered something old.
    """
    knobs = ranking_settings(cfg)
    return fuse_legs(
        [
            ("event", knobs["event_weight"], list(event_hits or [])),
            ("keyword", knobs["keyword_weight"], keyword_hits),
            ("vector", knobs["vector_weight"], vector_hits),
        ],
        cfg=cfg,
        signals=signals,
    )


def fuse_legs(
    legs: Sequence[tuple[str, float, list[SearchResult]]],
    *,
    cfg: Any = None,
    signals: dict[str, float] | None = None,
) -> list[SearchResult]:
    """RRF-fuse an ORDERED list of ``(leg_name, weight, hits)`` triples.

    The generalization of :func:`_fuse`, which is the three-leg hybrid case.
    A caller with a different leg set — the word search fuses four: the exact
    word, its meaning-neighbours, and two vector queries — gets the identical
    ranking rules (one vote per item per leg, dense ranks, term-rarity signal,
    age decay, recency tiebreak) instead of a second, subtly different fusion.

    Leg ORDER decides the representative row when an item appears in several
    legs, so the leg with the most informative snippet belongs first.
    """
    half_life = ranking_settings(cfg)["recency_half_life_days"]

    rrf_score: dict[int, float] = {}
    matched: dict[int, list[str]] = {}
    representative: dict[int, SearchResult] = {}
    for leg_name, weight, hits in legs:
        rank = 0
        for hit in hits:
            labels = matched.setdefault(hit.item_id, [])
            if leg_name in labels:
                continue  # this item already voted in this leg
            labels.append(leg_name)
            rank += 1
            rrf_score[hit.item_id] = (
                rrf_score.get(hit.item_id, 0.0) + weight / (RRF_K + rank)
            )
            representative.setdefault(hit.item_id, hit)
    if not representative:
        return []

    recorded = {
        item_id: (base.recorded_utc or base.timestamp_utc)
        for item_id, base in representative.items()
    }
    by_recency = sorted(
        representative,
        key=lambda item_id: _recency_key(recorded[item_id]),
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
                * _age_factor(recorded[item_id], half_life, now=now)
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
    timeout_s = ranking_settings(cfg)["rerank_timeout_s"]
    try:
        grading = reranker.rerank(query, documents, top_k=len(documents))
        if timeout_s > 0:
            pairs = await asyncio.wait_for(grading, timeout=timeout_s)
        else:
            pairs = await grading
    except TimeoutError:
        log.warning(
            "rerank stage exceeded its %.1f s budget — keeping the fusion order",
            timeout_s,
        )
        return ranked
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
