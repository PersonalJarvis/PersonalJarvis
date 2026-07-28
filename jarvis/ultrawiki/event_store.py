"""The SQL half of UltraWiki episodic events (design doc 01 · ``uw_events``).

:class:`EventMixin` is inherited by BOTH store backends, so the event rules
exist exactly once. Like :class:`~jarvis.ultrawiki.identity_store.IdentityMixin`
it relies only on the small surface both backends already provide — ``_txn()``,
``_fetchall()``, ``_fetchone()``, ``_ensure_open()``, ``_id_sql()`` and the
``_id_insert()`` dialect hook — plus one further hook of its own,
:attr:`EventMixin._EVENT_DIALECT`, because the keyword index is the one thing
the two engines genuinely cannot express the same way (FTS5 virtual table vs a
generated ``tsvector`` column).

The contract this module keeps:

- **Re-derivation is idempotent.** :meth:`replace_events` replaces an item's
  whole event set inside one transaction, keyed by
  :attr:`~jarvis.ultrawiki.events.DerivedEvent.dedupe_key`, so a second
  pipeline pass over unchanged content changes nothing.
- **Events die with their evidence.** ``item_id`` cascades, and a content
  CHANGE purges them alongside the stale documents and FTS rows — an event
  derived from a sentence that no longer exists must not keep answering.
- **Participants link through the identity layer, never around it.** Names are
  resolved with :meth:`IdentityMixin.resolve_identity`, which merges only on
  deterministic evidence and otherwise proposes; an unresolvable participant
  keeps its spelling and stays searchable rather than being dropped. Linking
  is cheap and always allowed; CREATING a person is gated on the event's own
  confidence, because a new entity outlives the guess that produced it.
- **One item spends a bounded identity budget.** The per-event caps multiply
  out to sixty candidate scans and hundreds of queued proposals per document,
  which would defeat the identity layer's "the queue stays short by design".
  ``MAX_IDENTITY_NAMES_PER_ITEM`` / ``MAX_ENTITIES_CREATED_PER_ITEM`` /
  ``MAX_IDENTITY_PROPOSALS_PER_ITEM`` bound the whole item; the excess links
  or keeps its spelling, deterministically, and nothing raises.
- **The keyword card is stored, not recomputed.** ``search_text`` lives on the
  row, so both dialects index the identical text and a search cannot depend
  on which engine answered.

Nothing here calls a model or touches the network; derivation runs on the
write path inside the distillation stage that produced its input (AP-9/AP-26).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from jarvis.ultrawiki.events import (
    EVENT_VERSION,
    DerivedEvent,
    EventKind,
    TimePrecision,
    format_occurred,
)
from jarvis.ultrawiki.types import SearchResult

log = logging.getLogger(__name__)

__all__ = ["EventMixin"]

#: Pool size of the event keyword leg before fusion; the same order of
#: magnitude as the item legs so no leg can dominate the RRF purely by
#: returning more rows.
EVENT_LEG_POOL = 30

_FTS_QUOTE_STRIP_RE = re.compile(r'["\']')

#: Cap on the stored card, so one pathological event cannot bloat the index.
_MAX_CARD_CHARS = 2000


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _match_expr(query: str) -> str:
    """OR-combined quoted tokens; quoting neutralizes FTS5 operators.

    Mirrors ``store._fts_match_expr``; duplicated deliberately rather than
    imported, because ``store`` imports this module and the cycle would make
    both unimportable.
    """
    tokens = [_FTS_QUOTE_STRIP_RE.sub("", tok) for tok in str(query or "").split() if tok.strip()]
    return " OR ".join(f'"{tok}"' for tok in tokens if tok)


def _normalize_bm25(raw: float) -> float:
    """FTS5 bm25 (lower = better, usually negative) -> [0, 1] higher-is-better."""
    return 1.0 / (1.0 + max(0.0, float(raw)))


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class EventMixin:
    """Episodic events: storage, bi-temporal queries and the keyword leg."""

    # -- dialect hook --------------------------------------------------------

    #: ``"sqlite"`` (FTS5 side table) or ``"postgres"`` (generated tsvector).
    #: The ONLY difference between the two backends in this module.
    _EVENT_DIALECT: str = "sqlite"

    # -- tiny query helpers (shared with the identity layer's translation) ---

    async def _ev_rows(self, conn: Any, sql: str, params: Sequence[Any] = ()) -> list[Any]:
        return await self._fetchall(conn, self._id_sql(sql), params)  # type: ignore[attr-defined]

    async def _ev_row(self, conn: Any, sql: str, params: Sequence[Any] = ()) -> Any | None:
        return await self._fetchone(conn, self._id_sql(sql), params)  # type: ignore[attr-defined]

    async def _ev_exec(self, conn: Any, sql: str, params: Sequence[Any] = ()) -> None:
        await conn.execute(self._id_sql(sql), params)  # type: ignore[attr-defined]

    # -- write path ----------------------------------------------------------

    async def replace_events(
        self,
        item_id: int,
        events: Sequence[DerivedEvent],
        *,
        create_entities: bool = True,
    ) -> list[int]:
        """Replace one item's whole event set; returns the new event ids.

        Identity resolution happens BEFORE the write transaction on purpose:
        :meth:`resolve_identity` opens its own transaction, and nesting two
        ``BEGIN IMMEDIATE`` blocks on one SQLite connection deadlocks the
        store against itself.

        ``create_entities=False`` links participants and places only to
        entities that already exist — a hard "read-only identity" switch for
        callers that must not touch the People view at all. Leaving it on does
        NOT mean every name becomes a person: an event below
        :data:`~jarvis.ultrawiki.events.ENTITY_CREATE_CONFIDENCE` links but
        never creates, so a low-confidence derivation cannot flood the People
        view with whatever it happened to name.

        Storing NOTHING for an item that already has nothing writes nothing at
        all. That is the overwhelmingly common case — most items yield no
        events — and without the check the empty case would be the most
        expensive thing on the pass: one write transaction and one DELETE round
        trip per item, forever, to delete zero rows.
        """
        item_id = int(item_id)
        if not events and not await self._ev_has_events(item_id):
            return []
        resolved = await self._ev_resolve_names(events, create_entities=create_entities)
        now = _utc_now_iso()
        new_ids: list[int] = []
        async with self._txn() as conn:  # type: ignore[attr-defined]
            await self._ev_purge(conn, [item_id])
            for event in events:
                event_id = await self._ev_insert(
                    conn, item_id, event, now=now, resolved=resolved
                )
                new_ids.append(event_id)
                for name in event.participants:
                    await self._ev_exec(
                        conn,
                        "INSERT INTO uw_event_participants"
                        " (event_id, entity_id, display_name) VALUES (?, ?, ?)",
                        (event_id, resolved.get(("person", name.casefold())), name),
                    )
        return new_ids

    async def _ev_resolve_names(
        self, events: Sequence[DerivedEvent], *, create_entities: bool
    ) -> dict[tuple[str, str], int | None]:
        """Map every participant/place name onto a live entity id (or ``None``).

        A name the identity layer refuses to decide (several live holders) maps
        to ``None`` and is stored by its spelling — an honest "I know the name,
        not who it is" beats guessing, and guessing is the wrong-merge failure
        the whole identity layer exists to prevent. A name this method never
        got to at all lands in the same place, which is why exceeding a budget
        below degrades instead of failing.

        Creating an entity is gated TWICE, because it is the only irreversible
        half of this: the caller has to allow it at all, and the name has to
        appear in at least one event confident enough to be worth a row
        (:data:`~jarvis.ultrawiki.events.ENTITY_CREATE_CONFIDENCE`). Below
        that the name still LINKS to whoever the user already curated and is
        otherwise stored as plain text — searchable, and nobody new in the
        People view.

        Everything here is bounded PER ITEM, not per event
        (:data:`~jarvis.ultrawiki.events.MAX_IDENTITY_NAMES_PER_ITEM` and
        friends): one document must not be able to spend sixty fuzzy candidate
        scans or fill the confirmation queue by itself.
        """
        from jarvis.ultrawiki.events import (  # noqa: PLC0415 — lazy (AP-26)
            MAX_IDENTITY_NAMES_PER_ITEM,
        )
        from jarvis.ultrawiki.identity import EntityKind  # noqa: PLC0415 — lazy (AP-26)

        wanted: list[tuple[str, Any]] = []
        best: dict[tuple[str, str], float] = {}
        for event in events:
            confidence = float(event.confidence)
            for name in event.participants:
                slot = ("person", name.casefold())
                if slot not in best:
                    wanted.append((name, EntityKind.PERSON))
                best[slot] = max(best.get(slot, 0.0), confidence)
            if event.place:
                slot = ("place", event.place.casefold())
                if slot not in best:
                    wanted.append((event.place, EntityKind.PLACE))
                best[slot] = max(best.get(slot, 0.0), confidence)

        if len(wanted) > MAX_IDENTITY_NAMES_PER_ITEM:
            log.info(
                "UltraWiki events: item names %d distinct entities, resolving the"
                " first %d — the rest keep their spelling and stay searchable",
                len(wanted),
                MAX_IDENTITY_NAMES_PER_ITEM,
            )
            wanted = wanted[:MAX_IDENTITY_NAMES_PER_ITEM]
        if not wanted:
            return {}

        # One transaction for the whole document instead of one per name. The
        # batch is all-or-nothing by construction, so a failure inside it
        # replays through the independent per-name path rather than costing the
        # item every participant it had already resolved.
        batch = getattr(self, "identity_batch", None)
        if callable(batch):
            try:
                async with batch() as resolve:
                    return await self._ev_resolve_with(
                        resolve, wanted, best, create_entities=create_entities
                    )
            except Exception:  # noqa: BLE001 — identity never fails an event
                log.debug(
                    "batched event identity resolution failed — replaying the"
                    " %d names one transaction at a time",
                    len(wanted),
                    exc_info=True,
                )
        return await self._ev_resolve_with(
            self.resolve_identity,  # type: ignore[attr-defined]
            wanted,
            best,
            create_entities=create_entities,
            isolate=True,
        )

    async def _ev_resolve_with(
        self,
        resolve: Any,
        wanted: Sequence[tuple[str, Any]],
        best: dict[tuple[str, str], float],
        *,
        create_entities: bool,
        isolate: bool = False,
    ) -> dict[tuple[str, str], int | None]:
        """Run *wanted* through *resolve*, spending the per-item budgets.

        ``isolate`` contains a failing name to itself; the batched caller must
        leave it off, because a raise there has already rolled its transaction
        back and nothing after it can be trusted.
        """
        from jarvis.ultrawiki.events import (  # noqa: PLC0415 — lazy (AP-26)
            ENTITY_CREATE_CONFIDENCE,
            MAX_ENTITIES_CREATED_PER_ITEM,
            MAX_IDENTITY_PROPOSALS_PER_ITEM,
        )
        from jarvis.ultrawiki.identity import (  # noqa: PLC0415 — lazy (AP-26)
            MAX_PROPOSALS,
            EntityKind,
        )

        out: dict[tuple[str, str], int | None] = {}
        created = 0
        queued = 0
        for name, kind in wanted:
            slot = ("person" if kind is EntityKind.PERSON else "place", name.casefold())
            # Both budgets gate CREATION only. A name that arrives after they
            # are spent still links to an entity the user already curated — the
            # cap withholds new rows, it does not withhold knowledge.
            #
            # The queue budget RESERVES a full proposal batch rather than
            # merely checking the running total, so the documented ceiling is
            # literally true: one item can never leave more than
            # MAX_IDENTITY_PROPOSALS_PER_ITEM pending pairs behind.
            may_create = (
                create_entities
                and best.get(slot, 0.0) >= ENTITY_CREATE_CONFIDENCE
                and created < MAX_ENTITIES_CREATED_PER_ITEM
                and queued + MAX_PROPOSALS <= MAX_IDENTITY_PROPOSALS_PER_ITEM
            )
            try:
                resolution = await resolve(
                    name=name,
                    kind=kind,
                    source_ref="uw_events",
                    create=may_create,
                )
            except Exception:
                if not isolate:
                    raise
                log.debug("event identity resolution failed for %r", name, exc_info=True)
                out[slot] = None
                continue
            out[slot] = _as_int(getattr(resolution, "entity_id", None))
            if getattr(resolution, "created", False):
                created += 1
            queued += len(getattr(resolution, "queued", ()) or ())
        return out

    async def _ev_has_events(self, item_id: int) -> bool:
        """Cheap "is there anything to clear" probe, outside any transaction."""
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        row = await self._ev_row(
            conn,
            "SELECT 1 AS present FROM uw_events WHERE item_id = ? LIMIT 1",
            (int(item_id),),
        )
        return row is not None

    async def _ev_insert(
        self,
        conn: Any,
        item_id: int,
        event: DerivedEvent,
        *,
        now: str,
        resolved: dict[tuple[str, str], int | None],
    ) -> int:
        card = event.search_text()[:_MAX_CARD_CHARS]
        place_id = (
            resolved.get(("place", event.place.casefold())) if event.place else None
        )
        event_id = await self._id_insert(  # type: ignore[attr-defined]
            conn,
            self._id_sql(  # type: ignore[attr-defined]
                "INSERT INTO uw_events"
                " (item_id, kind, title, summary, occurred_at, occurred_end,"
                "  occurred_precision, time_anchor, recorded_at, place_entity_id,"
                "  place_raw, confidence, extraction_version, dedupe_key,"
                "  evidence_json, search_text, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                item_id,
                str(event.kind),
                event.title,
                event.summary,
                event.time.occurred_at,
                event.time.occurred_end,
                str(event.time.precision),
                str(event.time.anchor),
                event.time.recorded_at,
                place_id,
                event.place,
                float(event.confidence),
                int(event.extraction_version or EVENT_VERSION),
                event.dedupe_key,
                json.dumps([item_id]),
                card,
                now,
            ),
        )
        if self._EVENT_DIALECT == "sqlite":
            await conn.execute(
                "DELETE FROM uw_event_fts WHERE event_id = ?", (event_id,)
            )
            await conn.execute(
                "INSERT INTO uw_event_fts (event_id, title, body) VALUES (?, ?, ?)",
                (event_id, event.title, card),
            )
        return event_id

    async def _ev_purge(self, conn: Any, item_ids: Sequence[int]) -> None:
        """Delete every event derived from *item_ids*, index rows included.

        Called by :meth:`replace_events` and by both backends' ``_purge_derived``
        (a content change invalidates events exactly like it invalidates
        documents and vectors).
        """
        ids = [int(value) for value in item_ids]
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        if self._EVENT_DIALECT == "sqlite":
            # FTS5 has no foreign keys: its rows must go first and explicitly.
            await conn.execute(
                self._id_sql(  # type: ignore[attr-defined]
                    "DELETE FROM uw_event_fts WHERE event_id IN"  # noqa: S608 — placeholder marks only
                    f" (SELECT id FROM uw_events WHERE item_id IN ({marks}))"
                ),
                ids,
            )
        await conn.execute(
            self._id_sql(f"DELETE FROM uw_events WHERE item_id IN ({marks})"),  # type: ignore[attr-defined]  # noqa: S608 — placeholder marks only
            ids,
        )

    # -- read path -----------------------------------------------------------

    @staticmethod
    def _ev_row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        precision = str(data.get("occurred_precision") or TimePrecision.DAY)
        try:
            evidence = json.loads(data.get("evidence_json") or "[]")
        except (TypeError, ValueError):
            evidence = []
        return {
            "id": _as_int(data.get("id")),
            "item_id": _as_int(data.get("item_id")),
            "kind": str(data.get("kind") or EventKind.OTHER),
            "title": str(data.get("title") or ""),
            "summary": str(data.get("summary") or ""),
            "occurred_at": str(data.get("occurred_at") or ""),
            "occurred_end": str(data.get("occurred_end") or ""),
            "occurred_precision": precision,
            "time_anchor": str(data.get("time_anchor") or ""),
            "recorded_at": str(data.get("recorded_at") or ""),
            "date_label": format_occurred(str(data.get("occurred_at") or ""), precision),
            "place": str(data.get("place_raw") or ""),
            "place_entity_id": _as_int(data.get("place_entity_id")),
            "confidence": float(data.get("confidence") or 0.0),
            "extraction_version": _as_int(data.get("extraction_version")) or 0,
            "evidence_item_ids": [value for value in evidence if isinstance(value, int)],
            "source_id": str(data.get("source_id") or ""),
            "permalink": str(data.get("permalink") or ""),
            "item_title": str(data.get("item_title") or ""),
            "participants": [],
        }

    _EVENT_SELECT = (
        "SELECT e.id, e.item_id, e.kind, e.title, e.summary, e.occurred_at,"
        " e.occurred_end, e.occurred_precision, e.time_anchor, e.recorded_at,"
        " e.place_entity_id, e.place_raw, e.confidence, e.extraction_version,"
        " e.evidence_json, i.source_id AS source_id, i.permalink AS permalink,"
        " i.title AS item_title"
        " FROM uw_events e JOIN uw_items i ON i.id = e.item_id"
    )

    async def get_event(self, event_id: int) -> dict[str, Any] | None:
        """One event with its participants, or ``None``."""
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        row = await self._ev_row(
            conn, f"{self._EVENT_SELECT} WHERE e.id = ?", (int(event_id),)
        )
        if row is None:
            return None
        event = self._ev_row_to_dict(row)
        await self._ev_attach_participants(conn, [event])
        return event

    async def list_events(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        kind: str | None = None,
        entity_id: int | None = None,
        place_entity_id: int | None = None,
        item_id: int | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Events in a bi-temporal window, newest first.

        ``since``/``until`` bound the VALID time and match by OVERLAP, not by
        containment: an event that spans a month is returned for a query about
        one day inside it. Containment would silently hide every coarse event,
        which is the majority of what a personal corpus actually knows.
        """
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        sql = self._EVENT_SELECT
        clauses: list[str] = []
        params: list[Any] = []
        if not include_deleted:
            clauses.append("i.deleted_at IS NULL")
        if since:
            clauses.append("e.occurred_end >= ?")
            params.append(str(since))
        if until:
            clauses.append("e.occurred_at <= ?")
            params.append(str(until))
        if kind:
            clauses.append("e.kind = ?")
            params.append(str(kind))
        if place_entity_id is not None:
            clauses.append("e.place_entity_id = ?")
            params.append(int(place_entity_id))
        if item_id is not None:
            clauses.append("e.item_id = ?")
            params.append(int(item_id))
        if entity_id is not None:
            # A merged-away entity id must still find its events: the identity
            # layer forwards it, so an old citation never goes dead.
            live = await self.resolve_entity_id(int(entity_id))  # type: ignore[attr-defined]
            clauses.append(
                "(e.place_entity_id = ? OR EXISTS (SELECT 1 FROM"
                " uw_event_participants p WHERE p.event_id = e.id"
                " AND p.entity_id = ?))"
            )
            target = int(live if live is not None else entity_id)
            params.extend([target, target])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY e.occurred_at DESC, e.id DESC LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])
        rows = await self._ev_rows(conn, sql, params)
        events = [self._ev_row_to_dict(row) for row in rows]
        await self._ev_attach_participants(conn, events)
        return events

    async def events_between(
        self,
        start: str,
        end: str,
        *,
        kind: str | None = None,
        entity_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """The design's ``events_between`` primitive (doc 03, "primitive tools")."""
        return await self.list_events(
            since=start, until=end, kind=kind, entity_id=entity_id, limit=limit
        )

    async def _ev_attach_participants(
        self, conn: Any, events: list[dict[str, Any]]
    ) -> None:
        """Fill in each event's participant list in one extra query."""
        ids = [event["id"] for event in events if event.get("id") is not None]
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        rows = await self._ev_rows(
            conn,
            "SELECT p.event_id, p.entity_id, p.display_name"  # noqa: S608 — placeholder marks only
            f" FROM uw_event_participants p WHERE p.event_id IN ({marks})"
            " ORDER BY p.id",
            ids,
        )
        by_event: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            data = dict(row)
            by_event.setdefault(int(data["event_id"]), []).append(
                {
                    "entity_id": _as_int(data.get("entity_id")),
                    "display_name": str(data.get("display_name") or ""),
                }
            )
        for event in events:
            event["participants"] = by_event.get(int(event["id"]), [])

    async def search_events(
        self,
        query: str,
        k: int = 10,
        *,
        since: str | None = None,
        until: str | None = None,
        kind: str | None = None,
        area_id: str | None = None,
    ) -> list[SearchResult]:
        """The event keyword leg, shaped exactly like the item legs.

        Returns :class:`SearchResult` rows so the fusion stage can treat events
        as one more ranked list (design doc 01, principle 5: no single scorer
        is trusted). ``timestamp_utc`` carries the event's OWN ``occurred_at``
        rather than the evidence item's — the date the user asked about is the
        date the answer should be SHOWN with — while ``recorded_utc`` keeps
        the item's own stamp, so ranking can still tell an old note from a
        fresh note about an old day.
        """
        if not query or not query.strip():
            return []
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        params: list[Any] = []
        if self._EVENT_DIALECT == "postgres":
            expression = query
            sql = (
                "SELECT e.id AS event_id, e.item_id, e.kind, e.title,"
                " e.summary, e.occurred_at, e.occurred_end, e.occurred_precision,"
                " e.place_raw, e.search_text,"
                " i.source_id, i.permalink, i.areas_json, i.timestamp_utc,"
                " ts_rank(e.search_tsv, websearch_to_tsquery('simple', ?)) AS raw_score"
                " FROM uw_events e JOIN uw_items i ON i.id = e.item_id"
                " WHERE e.search_tsv @@ websearch_to_tsquery('simple', ?)"
                "   AND i.deleted_at IS NULL"
            )
            params.extend([expression, expression])
        else:
            expression = _match_expr(query)
            if not expression:
                return []
            sql = (
                "SELECT e.id AS event_id, e.item_id, e.kind, e.title,"
                " e.summary, e.occurred_at, e.occurred_end, e.occurred_precision,"
                " e.place_raw, e.search_text,"
                " i.source_id, i.permalink, i.areas_json, i.timestamp_utc,"
                " bm25(uw_event_fts, 0.0, 3.0, 1.0) AS raw_score"
                " FROM uw_event_fts JOIN uw_events e ON e.id = uw_event_fts.event_id"
                " JOIN uw_items i ON i.id = e.item_id"
                " WHERE uw_event_fts MATCH ? AND i.deleted_at IS NULL"
            )
            params.append(expression)
        if since:
            sql += " AND e.occurred_end >= ?"
            params.append(str(since))
        if until:
            sql += " AND e.occurred_at <= ?"
            params.append(str(until))
        if kind:
            sql += " AND e.kind = ?"
            params.append(str(kind))
        if area_id is not None:
            if self._EVENT_DIALECT == "postgres":
                sql += " AND i.areas_json::jsonb @> to_jsonb(?::text)"
            else:
                sql += (
                    " AND EXISTS (SELECT 1 FROM json_each(i.areas_json)"
                    " WHERE json_each.value = ?)"
                )
            params.append(area_id)
        sql += (
            " ORDER BY raw_score DESC LIMIT ?"
            if self._EVENT_DIALECT == "postgres"
            else " ORDER BY raw_score LIMIT ?"
        )
        params.append(max(1, int(k)))
        rows = await self._ev_rows(conn, sql, params)
        return [self._ev_hit(dict(row)) for row in rows]

    def _ev_hit(self, data: dict[str, Any]) -> SearchResult:
        raw = float(data.get("raw_score") or 0.0)
        score = (
            raw / (1.0 + raw) if self._EVENT_DIALECT == "postgres" else _normalize_bm25(raw)
        )
        precision = str(data.get("occurred_precision") or TimePrecision.DAY)
        label = format_occurred(str(data.get("occurred_at") or ""), precision)
        snippet = " ".join(str(data.get("search_text") or "").split())[:400]
        return SearchResult(
            item_id=int(data["item_id"]),
            source_id=str(data.get("source_id") or ""),
            title=str(data.get("title") or ""),
            snippet=f"{label} — {snippet}" if label else snippet,
            permalink=str(data.get("permalink") or ""),
            timestamp_utc=str(data.get("occurred_at") or ""),
            score=round(score, 4),
            matched_by=("event",),
            recorded_utc=str(data.get("timestamp_utc") or ""),
        )

    # -- backfill over an ALREADY distilled corpus ---------------------------

    async def items_with_distillation(
        self, *, after_id: int = 0, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Distilled items whose stored distillation can yield events, by id.

        The reader behind the backfill lane. An install that distilled its
        corpus before the event tables existed never passes through the
        distillation stage again — that stage claims items that are NOT yet
        distilled — so without this its events would only ever appear for
        content imported afterwards.

        Nothing here costs a model call: the distillation JSON is already on
        the row, and ``events.derive_events`` is pure arithmetic over it. The
        cursor is the caller's (``after_id``), so the pass is resumable and
        each item is visited once.
        """
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        rows = await self._ev_rows(
            conn,
            "SELECT i.id AS id, i.title AS title, i.timestamp_utc AS timestamp_utc,"
            " d.distill_json AS distill_json"
            " FROM uw_items i JOIN uw_documents d ON d.item_id = i.id"
            " WHERE i.id > ? AND i.deleted_at IS NULL AND d.doc_type = 'summary'"
            "   AND d.distill_json IS NOT NULL AND d.distill_json != ''"
            " ORDER BY i.id, d.id DESC LIMIT ?",
            (int(after_id), max(1, int(limit))),
        )
        out: dict[int, dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            item_id = _as_int(data.get("id"))
            if item_id is None or item_id in out:
                continue  # one item can hold several summary documents
            out[item_id] = {
                "id": item_id,
                "title": str(data.get("title") or ""),
                "timestamp_utc": str(data.get("timestamp_utc") or ""),
                "distill_json": str(data.get("distill_json") or ""),
            }
        return [out[key] for key in sorted(out)]

    async def event_counts(self) -> dict[str, int]:
        """``{kind: n}`` over live events, plus ``total`` — the surface's badge."""
        conn = await self._ensure_open()  # type: ignore[attr-defined]
        rows = await self._ev_rows(
            conn,
            "SELECT e.kind AS kind, COUNT(*) AS n FROM uw_events e"
            " JOIN uw_items i ON i.id = e.item_id"
            " WHERE i.deleted_at IS NULL GROUP BY e.kind",
        )
        counts = {kind.value: 0 for kind in EventKind}
        total = 0
        for row in rows:
            data = dict(row)
            number = int(data.get("n") or 0)
            counts[str(data.get("kind") or EventKind.OTHER)] = number
            total += number
        counts["total"] = total
        return counts
