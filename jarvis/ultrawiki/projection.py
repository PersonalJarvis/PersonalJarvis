"""UltraWiki projection — turn the store's log into a readable wiki model.

The store is deliberately a log: one row per raw unit, thousands of small
fragments whose titles repeat by the hundred (a real corpus measured 4712
items across 261 distinct titles, the most common repeating 213 times). That
shape is right for retrieval and wrong for reading. Anything that shows the
corpus to a human — the Explore view, the Obsidian vault export — needs a
condensation layer first, or it renders hundreds of identical stubs linked to
nothing.

The readable units are one level up, and the distiller already extracted them:

- **Entities** — the people, places, organizations and projects named in
  ``uw_documents.distill_json``. They are the natural hubs of a graph.
- **Moments** — one distilled document each, titled by the ``question`` it
  answers, which is a real sentence instead of "Conversation on 2026-04-25".

Raw items never become pages. They stay evidence, reachable through
``permalink`` — mandatory from item one, so every claim can be traced back.

**Derived, never stored.** The full fold — query, JSON parse, entities,
mentions and co-occurrence — measures ~28 ms over a 1500-document corpus, so
the projection stays a computed view behind a fingerprint cache rather than a
second copy of the same truth in extra tables. A stored copy is precisely the
multi-layer drift class this repo has been bitten by repeatedly (BUG-008), and
it would also collide with the P5 identity tables (``uw_entities`` /
``uw_identifiers``), whose names are reserved for real, mergeable entities
with history. When P5 lands, this module reads from those tables instead and
both consuming surfaces stay unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol
from weakref import WeakKeyDictionary

log = logging.getLogger(__name__)

__all__ = [
    "MAX_LABEL_CHARS",
    "MIN_LABEL_CHARS",
    "ProjectedEntity",
    "ProjectedMoment",
    "WikiProjection",
    "build_projection",
    "get_projection",
    "invalidate_projection",
    "normalize_entity_label",
]

#: Length bounds for an entity label. Below the floor a "name" is an initial
#: or stray punctuation; above the ceiling the distiller handed back a
#: sentence, not an entity — and it would also blow past filename limits.
MIN_LABEL_CHARS = 2
MAX_LABEL_CHARS = 80

#: Trimmed from both ends of a label. Deliberately punctuation only: there is
#: no stop-word blacklist anywhere in this module, because guessing which
#: entity is "not real" is how a wiki quietly loses content. Rarity is handled
#: by ranking and the graph threshold, never by dropping data.
_EDGE_PUNCTUATION = " \t\r\n.,;:!?-–—_'\"()[]{}<>/\\|*"


class _ProjectionSource(Protocol):
    """The slice of the store this module reads (either backend satisfies it)."""

    async def distilled_rows(self) -> list[dict[str, Any]]: ...

    async def distilled_fingerprint(self) -> tuple[int, int, str]: ...


def normalize_entity_label(raw: object) -> str | None:
    """Canonical display spelling of one entity label, or ``None`` to drop it.

    NFC normalization is load-bearing, not cosmetic: macOS hands text back
    decomposed, so without it the same name becomes two entities *and* two
    vault files that collide with each other on the next export.
    """
    if not isinstance(raw, str):
        return None
    collapsed = " ".join(raw.split())
    label = unicodedata.normalize("NFC", collapsed).strip(_EDGE_PUNCTUATION)
    if not MIN_LABEL_CHARS <= len(label) <= MAX_LABEL_CHARS:
        return None
    return label


@dataclass(frozen=True, slots=True)
class ProjectedEntity:
    """One browsable entity page: who/what, how often, when, next to whom."""

    key: str  # case-folded identity
    label: str  # most frequent original spelling
    mentions: int
    item_ids: tuple[int, ...]
    first_seen: str  # ISO-8601 UTC
    last_seen: str
    neighbors: tuple[tuple[str, int], ...]  # (entity key, shared moments)


@dataclass(frozen=True, slots=True)
class ProjectedMoment:
    """One distilled moment: a real question, its answer, and its evidence."""

    document_id: int
    item_id: int
    title: str
    summary: str
    resolution: str
    entity_keys: tuple[str, ...]
    timestamp_utc: str
    source_id: str
    source_label: str
    permalink: str

    @property
    def month(self) -> str:
        """``YYYY-MM`` bucket, empty when the timestamp is unusable."""
        return self.timestamp_utc[:7] if len(self.timestamp_utc) >= 7 else ""


@dataclass(frozen=True, slots=True)
class WikiProjection:
    """The whole readable model: entities, moments, and the graph over them."""

    entities: tuple[ProjectedEntity, ...]
    moments: tuple[ProjectedMoment, ...]
    entity_by_key: dict[str, ProjectedEntity]
    moments_by_entity: dict[str, tuple[ProjectedMoment, ...]]

    def graph(self, *, min_mentions: int = 2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Nodes + edges above a mention floor.

        The floor exists because the long tail dominates: on the reference
        corpus 947 entities collapse to 303 at two mentions and 72 at five.
        Drawing all of them at once is a hairball, not a map — so the view
        defaults to a threshold and offers the full set explicitly.

        Edges whose other endpoint fell below the floor are dropped, never
        left dangling at an invisible node.
        """
        kept = {e.key for e in self.entities if e.mentions >= min_mentions}
        nodes = [
            {
                "key": entity.key,
                "label": entity.label,
                "mentions": entity.mentions,
                "first_seen": entity.first_seen,
                "last_seen": entity.last_seen,
            }
            for entity in self.entities
            if entity.key in kept
        ]
        edges: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for entity in self.entities:
            if entity.key not in kept:
                continue
            for other_key, weight in entity.neighbors:
                if other_key not in kept:
                    continue
                pair = (
                    (entity.key, other_key) if entity.key < other_key else (other_key, entity.key)
                )
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append({"source": pair[0], "target": pair[1], "weight": weight})
        return nodes, edges


def _title_for(distilled: dict[str, Any], row: dict[str, Any]) -> str:
    """The most speaking title available, in descending order of usefulness.

    The item title is the LAST resort on purpose — it is the one that repeats
    by the hundred and made the corpus unreadable in the first place.
    """
    for candidate in (distilled.get("question"), distilled.get("summary")):
        if isinstance(candidate, str) and candidate.strip():
            return " ".join(candidate.split())
    fallback = row.get("title") or ""
    return " ".join(str(fallback).split())


def _text_field(distilled: dict[str, Any], name: str) -> str:
    value = distilled.get(name)
    return value.strip() if isinstance(value, str) else ""


async def build_projection(store: _ProjectionSource) -> WikiProjection:
    """Fold the store's distilled documents into the readable wiki model.

    Malformed rows are skipped with a debug line rather than raised: one bad
    distillation must never blank the whole wiki view.
    """
    rows = await store.distilled_rows()

    moments: list[ProjectedMoment] = []
    spellings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    item_ids: dict[str, list[int]] = defaultdict(list)
    seen_times: dict[str, list[str]] = defaultdict(list)
    cooccurrence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_entity: dict[str, list[ProjectedMoment]] = defaultdict(list)

    for row in rows:
        try:
            distilled = json.loads(row.get("distill_json") or "")
        except (json.JSONDecodeError, TypeError):
            log.debug(
                "projection: unparseable distillation for document %s",
                row.get("document_id"),
            )
            continue
        if not isinstance(distilled, dict):
            continue

        keys: list[str] = []
        for raw_label in distilled.get("entities") or []:
            label = normalize_entity_label(raw_label)
            if label is None:
                continue
            key = label.casefold()
            if key not in keys:
                keys.append(key)
            spellings[key][label] += 1

        timestamp = str(row.get("timestamp_utc") or "")
        moment = ProjectedMoment(
            document_id=int(row.get("document_id") or 0),
            item_id=int(row.get("item_id") or 0),
            title=_title_for(distilled, row),
            summary=_text_field(distilled, "summary"),
            resolution=_text_field(distilled, "resolution"),
            entity_keys=tuple(keys),
            timestamp_utc=timestamp,
            source_id=str(row.get("source_id") or ""),
            source_label=str(row.get("source_label") or ""),
            permalink=str(row.get("permalink") or ""),
        )
        moments.append(moment)

        for key in keys:
            item_ids[key].append(moment.item_id)
            seen_times[key].append(timestamp)
            by_entity[key].append(moment)
        for index, key in enumerate(keys):
            for other in keys[index + 1 :]:
                cooccurrence[key][other] += 1
                cooccurrence[other][key] += 1

    entities: list[ProjectedEntity] = []
    for key, variants in spellings.items():
        # Ties resolve alphabetically so a re-export is byte-identical.
        label = max(sorted(variants), key=lambda spelling: variants[spelling])
        times = sorted(t for t in seen_times[key] if t)
        neighbors = tuple(
            sorted(
                cooccurrence[key].items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        )
        entities.append(
            ProjectedEntity(
                key=key,
                label=label,
                mentions=len(item_ids[key]),
                item_ids=tuple(item_ids[key]),
                first_seen=times[0] if times else "",
                last_seen=times[-1] if times else "",
                neighbors=neighbors,
            )
        )

    entities.sort(key=lambda e: (-e.mentions, e.key))
    moments.sort(key=lambda m: (m.timestamp_utc, m.document_id), reverse=True)

    return WikiProjection(
        entities=tuple(entities),
        moments=tuple(moments),
        entity_by_key={e.key: e for e in entities},
        moments_by_entity={
            key: tuple(sorted(found, key=lambda m: (m.timestamp_utc, m.document_id), reverse=True))
            for key, found in by_entity.items()
        },
    )


# ---------------------------------------------------------------------------
# Fingerprint cache
# ---------------------------------------------------------------------------

#: Per-store cache. Weak keys so a closed store is collected with its entry;
#: the value carries the fingerprint the projection was built from.
PROJECTION_CACHE_TTL_S = 15.0


@dataclass(frozen=True, slots=True)
class _ProjectionCacheEntry:
    fingerprint: tuple[int, int, str]
    projection: WikiProjection
    checked_at: float


_CACHE: WeakKeyDictionary[Any, _ProjectionCacheEntry] = WeakKeyDictionary()
_CACHE_LOCK = asyncio.Lock()
_monotonic = time.monotonic


async def get_projection(store: _ProjectionSource) -> WikiProjection:
    """The cached projection, refreshed shortly after its input changes.

    A busy pipeline changes the fingerprint every few seconds. Checking it on
    every UI request made the cache useless and repeatedly folded the entire
    corpus while the user clicked around. The projection is a browse aid, not
    the retrieval index, so a bounded 15-second stale window is preferable to
    multi-second page loads. Explicit invalidation remains immediate.
    """
    checked_at = _monotonic()
    cached = _CACHE.get(store)
    if (
        cached is not None
        and checked_at - cached.checked_at < PROJECTION_CACHE_TTL_S
    ):
        return cached.projection
    async with _CACHE_LOCK:
        cached = _CACHE.get(store)
        checked_at = _monotonic()
        if (
            cached is not None
            and checked_at - cached.checked_at < PROJECTION_CACHE_TTL_S
        ):
            return cached.projection
        fingerprint = await store.distilled_fingerprint()
        if cached is not None and cached.fingerprint == fingerprint:
            _CACHE[store] = _ProjectionCacheEntry(
                fingerprint=fingerprint,
                projection=cached.projection,
                checked_at=checked_at,
            )
            return cached.projection
        projection = await build_projection(store)
        _CACHE[store] = _ProjectionCacheEntry(
            fingerprint=fingerprint,
            projection=projection,
            checked_at=checked_at,
        )
        return projection


def invalidate_projection(store: _ProjectionSource | None = None) -> None:
    """Drop the cached projection — for tests and for an explicit refresh."""
    if store is None:
        _CACHE.clear()
    else:
        _CACHE.pop(store, None)
