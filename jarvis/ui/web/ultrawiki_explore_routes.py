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

import asyncio
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
    projection = await get_projection(store)
    return (
        store,
        projection,
        {
            "sources": len(sources),
            "items": int(getattr(counts, "total", 0)),
            "distilled": int(getattr(counts, "distilled", 0)),
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


def _entity_payload(
    entity: Any, projection: Any, *, neighbor_limit: int
) -> dict[str, Any]:
    """One entity as the UI needs it — neighbours carry their display label,
    because a raw case-folded key is not something to show a human."""
    return {
        "key": entity.key,
        "label": entity.label,
        "mentions": entity.mentions,
        "first_seen": entity.first_seen,
        "last_seen": entity.last_seen,
        "neighbor_total": len(entity.neighbors),
        "neighbors": [
            {
                "key": key,
                "label": (
                    projection.entity_by_key[key].label if key in projection.entity_by_key else key
                ),
                "shared": shared,
            }
            for key, shared in entity.neighbors[:neighbor_limit]
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
        "entities": [
            _entity_payload(entity, projection, neighbor_limit=12)
            for entity in page
        ],
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
        "entity": _entity_payload(entity, projection, neighbor_limit=100),
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
    max_nodes: int = Query(
        default=250,
        ge=1,
        le=2000,
        description="Maximum graph nodes returned",
    ),
    max_edges: int = Query(
        default=1000,
        ge=0,
        le=10000,
        description="Maximum graph edges returned",
    ),
) -> dict[str, Any]:
    """Nodes + weighted edges above a mention floor.

    The floor defaults to 2 because the long tail dominates a real corpus
    (measured: 977 entities collapse to 313 at two mentions); drawing every
    one-off at once is a hairball rather than a map.
    """
    _store, projection, corpus = await _explore_context(request)
    all_nodes, all_edges = projection.graph(min_mentions=min_mentions)
    nodes = all_nodes[:max_nodes]
    kept = {str(node["key"]) for node in nodes}
    eligible_edges = [
        edge
        for edge in all_edges
        if edge["source"] in kept and edge["target"] in kept
    ]
    eligible_edges.sort(
        key=lambda edge: (-int(edge["weight"]), edge["source"], edge["target"])
    )
    edges = eligible_edges[:max_edges]
    return {
        "ok": True,
        "nodes": nodes,
        "edges": edges,
        "min_mentions": min_mentions,
        "available_nodes": len(all_nodes),
        "available_edges": len(eligible_edges),
        "truncated": len(nodes) < len(all_nodes) or len(edges) < len(eligible_edges),
        "total_entities": len(projection.entities),
        "corpus": corpus,
        "reason": _explore_reason(corpus, len(projection.entities)),
    }

# ---------------------------------------------------------------------------
# Vault — the same projection as Markdown files an Obsidian can open
# ---------------------------------------------------------------------------


def _vault_root(request: Request) -> Any:
    from jarvis.ultrawiki.vault_export import resolve_vault_root

    cfg = getattr(request.app.state, "config", None)
    memory = getattr(cfg, "memory", None)
    ultra = getattr(cfg, "ultrawiki", None)
    return resolve_vault_root(
        getattr(memory, "data_dir", None), str(getattr(ultra, "vault_path", "") or "")
    )


def _obsidian_state(vault: Any) -> dict[str, Any]:
    """What Obsidian knows about this vault, honestly on every OS.

    Obsidian is absent on a headless server and on most fresh machines. That
    is a normal state, not an error: the files are the deliverable, and the
    app is the optional reader.
    """
    from jarvis.setup import obsidian as obsidian_mod

    try:
        detection = obsidian_mod.detect_obsidian()
        state = obsidian_mod.read_obsidian_vaults()
        registered = obsidian_mod.is_vault_registered(list(state.vaults), vault)
        config_path = str(getattr(state, "config_path", "") or "")
        error = ""
    except Exception as exc:  # noqa: BLE001 — a probe must never break the page
        log.debug("obsidian probe failed: %s", exc)
        return {
            "installed": False,
            "registered": False,
            "config_path": "",
            "error": str(exc),
        }
    return {
        "installed": bool(detection.installed),
        "registered": bool(registered),
        "config_path": config_path,
        "error": error,
    }


def _vault_stats(vault: Any) -> dict[str, Any]:
    from jarvis.ultrawiki.vault_export import MANIFEST_NAME

    if not vault.is_dir():
        return {"exists": False, "notes": 0, "last_export_at": ""}
    notes = sum(1 for _ in vault.rglob("*.md"))
    manifest = vault / MANIFEST_NAME
    stamp = ""
    if manifest.exists():
        from datetime import UTC, datetime

        stamp = (
            datetime.fromtimestamp(manifest.stat().st_mtime, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return {"exists": True, "notes": notes, "last_export_at": stamp}


@router.get("/vault/status", summary="Where the Obsidian vault is and what is in it")
async def get_vault_status(request: Request) -> dict[str, Any]:
    """Path, note count, last export, and whether Obsidian knows about it."""
    _require_active(request)
    vault = _vault_root(request)
    # Both probes walk the filesystem; off the event loop so a slow disk or a
    # scanned directory cannot stall every other request.
    stats = await asyncio.to_thread(_vault_stats, vault)
    obsidian = await asyncio.to_thread(_obsidian_state, vault)
    return {"ok": True, "path": str(vault), "obsidian": obsidian, **stats}


@router.post(
    "/vault/export",
    summary="Write the knowledge base to the Obsidian vault",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def export_vault_now(request: Request) -> dict[str, Any]:
    """Rewrite the generated folders. Notes under "My notes/" are never touched.

    Runs in a worker thread: a first export writes thousands of files, and
    holding the event loop for that would freeze every other surface in the
    app — including a voice turn in progress.
    """
    from jarvis.ultrawiki.projection import get_projection
    from jarvis.ultrawiki.vault_export import export_vault

    service = _require_active(request)
    store = await _store_of(service)
    projection = await get_projection(store)
    vault = _vault_root(request)
    try:
        result = await asyncio.to_thread(export_vault, projection, vault)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"could not write the vault at {vault}: {exc}",
        ) from exc
    return {
        "ok": True,
        "path": str(result.root),
        "topics": result.topics,
        "moments": result.moments,
        "written": result.written,
        "unchanged": result.unchanged,
        "removed": result.removed,
    }


@router.post(
    "/vault/register",
    summary="Register the vault with the Obsidian app",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def register_vault_with_obsidian(request: Request) -> dict[str, Any]:
    """Add the vault to Obsidian's own index so it appears in the app."""
    from jarvis.setup import obsidian as obsidian_mod

    _require_active(request)
    vault = _vault_root(request)
    if not vault.is_dir():
        raise HTTPException(
            status_code=409,
            detail=(
                "the vault does not exist yet — run the export first, "
                "otherwise Obsidian would open an empty folder"
            ),
        )
    result = await asyncio.to_thread(obsidian_mod.register_vault, vault)
    return {
        "ok": result.status in ("added", "already_registered"),
        "status": result.status,
        "path": str(vault),
        "error": getattr(result, "error", "") or "",
    }
