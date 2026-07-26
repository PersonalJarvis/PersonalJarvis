"""UltraWiki Explore REST surface — the readable view over the store.

UltraWiki could answer questions long before it could SHOW anything. The
store is a log (one row per raw unit, titles repeating by the hundred), so
these routes serve the condensed model from
:mod:`jarvis.ultrawiki.projection` instead: entity pages, moment pages, and
the graph over them. Pure reads, no writes, off the voice hot path.

Own module rather than more lines in ``ultrawiki_routes.py``: that file had
already grown past 1800 lines and is edited concurrently by other sessions in
this shared tree. Same prefix and same ``ultrawiki`` tag, so the CLI-first
contract still resolves every handler to ``jarvis api ultrawiki <op>``.

The mode gate and the store accessor are reused from the main module — one
definition of "is UltraWiki answering right now", never a second opinion.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from jarvis.ui.web.ultrawiki_routes import _require_active, _store_of

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ultrawiki", tags=["ultrawiki"])


async def _explore_context(request: Request) -> tuple[Any, Any, dict[str, int]]:
    """(store, projection, corpus counts) for any Explore read.

    The corpus counts travel with EVERY Explore answer, not just the empty
    ones: they are what turns "nothing here" from a dead end into a diagnosis.
    """
    from jarvis.ultrawiki.projection import get_projection

    service = _require_active(request)
    store = await _store_of(service)
    sources = await store.list_sources()
    counts = await store.counts()
    distilled, _max_id, _newest = await store.distilled_fingerprint()
    projection = await get_projection(store)
    return (
        store,
        projection,
        {
            "sources": len(sources),
            "items": int(getattr(counts, "total", 0)),
            "distilled": int(distilled),
        },
    )


def _explore_reason(corpus: dict[str, int], entity_count: int) -> str:
    """Name the cause of an empty Explore view, in the order it fails."""
    from jarvis.ultrawiki.types import ExploreReason

    if corpus["sources"] == 0:
        return ExploreReason.NO_SOURCES.value
    if corpus["items"] == 0:
        return ExploreReason.NOTHING_IMPORTED.value
    if corpus["distilled"] == 0:
        return ExploreReason.NOTHING_DISTILLED.value
    if entity_count == 0:
        return ExploreReason.NO_ENTITIES.value
    return ExploreReason.OK.value


def _entity_payload(entity: Any, projection: Any) -> dict[str, Any]:
    """One entity as the UI needs it — neighbours carry their display label,
    because a raw case-folded key is not something to show a human."""
    return {
        "key": entity.key,
        "label": entity.label,
        "mentions": entity.mentions,
        "first_seen": entity.first_seen,
        "last_seen": entity.last_seen,
        "neighbors": [
            {
                "key": key,
                "label": (
                    projection.entity_by_key[key].label if key in projection.entity_by_key else key
                ),
                "shared": shared,
            }
            for key, shared in entity.neighbors
        ],
    }


def _moment_payload(moment: Any) -> dict[str, Any]:
    return {
        "document_id": moment.document_id,
        "item_id": moment.item_id,
        "title": moment.title,
        "summary": moment.summary,
        "resolution": moment.resolution,
        "entity_keys": list(moment.entity_keys),
        "timestamp_utc": moment.timestamp_utc,
        "month": moment.month,
        "source_id": moment.source_id,
        "source_label": moment.source_label,
        "permalink": moment.permalink,
    }


@router.get(
    "/explore/entities",
    summary="Browse the people, places and things the knowledge base knows",
)
async def list_explore_entities(
    request: Request,
    q: str = Query(default="", description="Substring filter over entity labels"),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Entity pages, most-mentioned first, with the reason when there are none."""
    _store, projection, corpus = await _explore_context(request)
    needle = q.strip().casefold()
    matched = [
        entity
        for entity in projection.entities
        if not needle or needle in entity.key or needle in entity.label.casefold()
    ]
    page = matched[offset : offset + limit]
    return {
        "ok": True,
        "entities": [_entity_payload(entity, projection) for entity in page],
        "total": len(matched),
        "corpus": corpus,
        "reason": _explore_reason(corpus, len(projection.entities)),
    }


@router.get(
    "/explore/entities/{key:path}",
    summary="One entity page with the moments it appears in",
)
async def get_explore_entity(
    key: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Everything the knowledge base has on one entity, newest evidence first."""
    _store, projection, corpus = await _explore_context(request)
    entity = projection.entity_by_key.get(key.casefold())
    if entity is None:
        raise HTTPException(status_code=404, detail=f"no entity named {key!r}")
    moments = projection.moments_by_entity.get(entity.key, ())
    return {
        "ok": True,
        "entity": _entity_payload(entity, projection),
        "moments": [_moment_payload(m) for m in moments[:limit]],
        "total": len(moments),
        "corpus": corpus,
    }


@router.get(
    "/explore/moments",
    summary="Browse the distilled moments, newest first",
)
async def list_explore_moments(
    request: Request,
    entity: str = Query(default="", description="Restrict to one entity key"),
    month: str = Query(default="", description="Restrict to one YYYY-MM bucket"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Each moment is titled by the question it answers and links to its evidence."""
    _store, projection, corpus = await _explore_context(request)
    if entity:
        moments = list(projection.moments_by_entity.get(entity.casefold(), ()))
    else:
        moments = list(projection.moments)
    if month:
        moments = [m for m in moments if m.month == month]
    page = moments[offset : offset + limit]
    return {
        "ok": True,
        "moments": [_moment_payload(m) for m in page],
        "total": len(moments),
        "corpus": corpus,
        "reason": _explore_reason(corpus, len(projection.entities)),
    }


@router.get(
    "/explore/graph",
    summary="The entity graph — who and what appears together",
)
async def get_explore_graph(
    request: Request,
    min_mentions: int = Query(
        default=2,
        ge=1,
        le=100,
        description="Hide entities mentioned fewer times than this",
    ),
) -> dict[str, Any]:
    """Nodes + weighted edges above a mention floor.

    The floor defaults to 2 because the long tail dominates a real corpus
    (measured: 977 entities collapse to 313 at two mentions); drawing every
    one-off at once is a hairball rather than a map.
    """
    _store, projection, corpus = await _explore_context(request)
    nodes, edges = projection.graph(min_mentions=min_mentions)
    return {
        "ok": True,
        "nodes": nodes,
        "edges": edges,
        "min_mentions": min_mentions,
        "total_entities": len(projection.entities),
        "corpus": corpus,
        "reason": _explore_reason(corpus, len(projection.entities)),
    }