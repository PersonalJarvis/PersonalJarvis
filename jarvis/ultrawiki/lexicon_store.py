"""The SQL half of the UltraWiki word lexicon (``uw_terms``).

:class:`LexiconMixin` is inherited by BOTH store backends, so the vocabulary
rules exist exactly once. Like :class:`~jarvis.ultrawiki.event_store.EventMixin`
it relies only on the small surface both backends already provide — ``_txn()``,
``_fetchall()``, ``_fetchone()``, ``_ensure_open()``, ``_id_sql()``,
``get_meta()``/``set_meta()`` — plus one dialect hook of its own,
:attr:`LexiconMixin._LEXICON_DIALECT`, because nearest-vector search is the one
thing the two engines cannot express the same way.

What this layer is for
======================

Semantic search answers a QUESTION. It cannot answer a WORD: a passage vector
says what a passage is about, never what a single term means on its own. So the
lexicon embeds the vocabulary itself — every term the corpus actually uses gets
one vector in the SAME space as the documents — and "the twenty words nearest
to X" becomes an ordinary nearest-neighbour query.

The contract this module keeps:

- **Harvesting is incremental and bounded.** A cursor in ``uw_meta`` records
  the highest passage id already walked; each pass reads the next slice. A
  200 000-passage corpus is therefore never scanned in one go, and a restart
  resumes where it stopped instead of starting over.
- **The lexicon never grows without limit.** Only terms above a document
  frequency floor are ever embedded, capped by
  ``[ultrawiki].lexicon_max_terms``, most-seen first. A corpus of ids, hashes
  and typos costs nothing: those terms are stored (they still answer an exact
  lookup) but never embedded.
- **One vector space, never mixed.** Term vectors are keyed by
  ``(model, dim)`` exactly like ``uw_embeddings`` (design rule D-3). The
  neighbour query reads the ACTIVE space only; a model switch simply leaves
  the old rows behind and re-embeds into the new space on the next pass.
- **A missing vector index is a degradation, not a failure.** Without
  sqlite-vec / pgvector there are no vector neighbours, and
  :meth:`term_neighbors` says so honestly instead of raising — the caller
  falls back to the co-occurrence path, which needs no provider at all.
- **The store stays policy-free.** Nothing here tokenizes, scores or decides
  what a "word" is; that is :mod:`jarvis.ultrawiki.lexicon`. This module moves
  rows.

Nothing here calls a model or touches the network.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "LexiconMixin",
    "META_LEXICON_CURSOR",
]

#: ``uw_meta`` key holding the highest ``uw_items.id`` the vocabulary
#: harvester has already walked. Monotonic; reset by :meth:`reset_lexicon`.
#:
#: ITEMS, not passages, and that is load bearing: passages (``uw_documents``)
#: only come into existence when the embed stage runs, so a store with no
#: embedding provider would have an empty vocabulary — and the word search
#: fallback that exists precisely FOR that store would have nothing to fall
#: back to. Items exist the moment a sync lands, and their text is the same
#: text the passages are cut from, so the vocabulary is identical either way.
META_LEXICON_CURSOR = "lexicon_scan_item_id"

#: Rows per SQL ``IN (...)`` batch, mirroring ``store._IN_CHUNK``.
_IN_CHUNK = 400


def _pack_vector(vector: Sequence[float]) -> bytes:
    """Little-endian float32 BLOB.

    Duplicated from ``store.pack_vector`` deliberately rather than imported:
    ``store`` imports this module, and the cycle would make both unimportable
    (the same reason ``event_store`` carries its own ``_match_expr``).
    """
    return struct.pack(f"<{len(vector)}f", *[float(value) for value in vector])


def _unpack_vector(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cosine_similarity_from_distance(distance: float) -> float:
    """sqlite-vec / pgvector report cosine DISTANCE (0 = identical, 2 = opposite).

    The surface shows a similarity, because "0.91 alike" is readable and
    "0.09 apart" is not. Clamped, so a provider that returns a hair outside
    the theoretical range cannot produce a similarity above 1.
    """
    return max(0.0, min(1.0, 1.0 - float(distance)))


class LexiconMixin:
    """Vocabulary + term-vector SQL shared by both UltraWiki store backends."""

    #: ``"sqlite"`` (default) or ``"postgres"`` — set on the store class.
    _LEXICON_DIALECT: str = "sqlite"

    #: Cached ``(usable, reason)`` of the term-vector search path, per store
    #: instance. Mirrors ``_vec_state``: the verdict only changes when the
    #: process gains the extension, which it cannot do mid-run.
    _term_vec_state: tuple[bool, str] | None = None
    _term_vec_dim: int | None = None

    # -- small dialect-aware helpers ----------------------------------------

    async def _lex_all(
        self, conn: Any, sql: str, params: Sequence[Any] = ()
    ) -> list[Any]:
        return await self._fetchall(conn, self._id_sql(sql), params)  # type: ignore[attr-defined]

    async def _lex_one(
        self, conn: Any, sql: str, params: Sequence[Any] = ()
    ) -> Any:
        return await self._fetchone(conn, self._id_sql(sql), params)  # type: ignore[attr-defined]

    async def _lex_exec(
        self, conn: Any, sql: str, params: Sequence[Any] = ()
    ) -> None:
        await conn.execute(self._id_sql(sql), params)

    def _lex_marks(self, count: int) -> str:
        mark = "?" if self._LEXICON_DIALECT == "sqlite" else "%s"
        return ",".join([mark] * count)

    async def _lex_conn(self) -> Any:
        """A connection for READS.

        The SQLite backend keeps a pool of read-only connections precisely so
        a search leg does not queue behind the ingest writer (one aiosqlite
        connection = one worker thread). Two of the queries below run on the
        search path, so they take that pool when it exists and the writer
        otherwise — which is also what a store fake without a pool gets.
        """
        reader = getattr(self, "_read_conn", None)
        if callable(reader):
            return await reader()
        return await self._ensure_open()  # type: ignore[attr-defined]

    async def _lex_vec_conn(self) -> Any:
        """A connection that can evaluate vector distances.

        sqlite-vec loads per CONNECTION while the readiness verdict is cached
        per STORE, so a pooled reader has to be prepared before it can run the
        scan — the same trap ``_vec_ready_conn`` exists for on the document
        leg. Reuses that helper rather than repeating its fallback.
        """
        conn = await self._lex_conn()
        prepare = getattr(self, "_vec_ready_conn", None)
        if callable(prepare):
            return await prepare(conn)
        return conn

    # -- vocabulary harvesting ----------------------------------------------

    async def lexicon_cursor(self) -> int:
        """Highest item id the vocabulary harvester has already walked."""
        raw = await self.get_meta(META_LEXICON_CURSOR)  # type: ignore[attr-defined]
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            # A cursor nobody can parse means "start over", and starting over
            # is the correct, complete repair: the harvest is idempotent per
            # slice, so re-walking the corpus costs one background pass and
            # loses nothing. There is no failure here worth reporting.
            return 0

    async def set_lexicon_cursor(self, item_id: int) -> None:
        await self.set_meta(META_LEXICON_CURSOR, str(int(item_id)))  # type: ignore[attr-defined]

    async def lexicon_scan_batch(
        self, *, after_item_id: int, limit: int
    ) -> list[dict[str, Any]]:
        """The next slice of items to harvest vocabulary from.

        Ordered by id so the cursor above is a complete record of what has
        been seen. Tombstoned items are skipped — their text is gone from the
        corpus and its words have no business entering the vocabulary. Title
        and body are returned together because a title is where a document
        names its own subject, which is exactly the vocabulary worth having.
        """
        if limit <= 0:
            return []
        conn = await self._lex_conn()
        rows = await self._lex_all(
            conn,
            "SELECT id AS item_id, title, body_raw FROM uw_items"
            " WHERE id > ? AND deleted_at IS NULL"
            " ORDER BY id LIMIT ?",
            (int(after_item_id), int(limit)),
        )
        return [
            {
                "item_id": int(row["item_id"]),
                "text": f"{row['title'] or ''} {row['body_raw'] or ''}",
            }
            for row in rows
        ]

    async def bump_terms(self, counts: Mapping[str, int]) -> int:
        """Add ``counts`` to the vocabulary, inserting terms it has not seen.

        Returns the number of distinct terms touched. Idempotent per BATCH,
        not per corpus: the caller must only ever pass a slice of passages it
        has not passed before, which is what the harvest cursor guarantees.
        """
        rows = [
            (term, int(count))
            for term, count in counts.items()
            if term and int(count) > 0
        ]
        if not rows:
            return 0
        now = _utc_now_iso()
        # ON CONFLICT is spelled identically in both engines for this shape.
        sql = (
            "INSERT INTO uw_terms (term, doc_freq, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (term) DO UPDATE SET"
            "  doc_freq = uw_terms.doc_freq + EXCLUDED.doc_freq,"
            "  updated_at = EXCLUDED.updated_at"
        )
        async with self._txn() as conn:  # type: ignore[attr-defined]
            for term, count in rows:
                await self._lex_exec(conn, sql, (term, count, now, now))
        return len(rows)

    async def lexicon_counts(self, *, model: str = "", dim: int = 0) -> dict[str, int]:
        """Honest size report: vocabulary, embedded share, harvest progress."""
        conn = await self._lex_conn()
        terms_row = await self._lex_one(conn, "SELECT COUNT(*) AS n FROM uw_terms")
        items_row = await self._lex_one(
            conn, "SELECT COUNT(*) AS n FROM uw_items WHERE deleted_at IS NULL"
        )
        passages_row = await self._lex_one(
            conn,
            "SELECT COUNT(*) AS n FROM uw_documents d"
            " JOIN uw_items i ON i.id = d.item_id WHERE i.deleted_at IS NULL",
        )
        embedded = 0
        if model and int(dim) > 0:
            embedded_row = await self._lex_one(
                conn,
                "SELECT COUNT(*) AS n FROM uw_term_embeddings"
                " WHERE model = ? AND dim = ?",
                (model, int(dim)),
            )
            embedded = int(embedded_row["n"]) if embedded_row else 0
        return {
            "terms": int(terms_row["n"]) if terms_row else 0,
            "embedded_terms": embedded,
            "items": int(items_row["n"]) if items_row else 0,
            # Passages, so a surface can say whether hits will carry a located
            # span at all: zero means nothing has been embedded yet and every
            # hit will point at its item rather than at a paragraph of it.
            "passages": int(passages_row["n"]) if passages_row else 0,
            "scanned_items": await self.lexicon_cursor(),
        }

    async def reset_lexicon(self) -> None:
        """Drop the whole vocabulary and rewind the harvest cursor.

        The one repair for a ``doc_freq`` that has drifted after many
        tombstones, and the way a user asks for a fresh count. Vectors go with
        it (``ON DELETE CASCADE``) and are re-earned by the background pass —
        which costs embedding calls, so this is never automatic.
        """
        async with self._txn() as conn:  # type: ignore[attr-defined]
            if self._LEXICON_DIALECT == "postgres":
                # DERIVED, and possibly not created yet — dropping it is both
                # the correct reset and the only spelling that cannot abort
                # this transaction on a store that never ran a word search.
                await self._lex_exec(conn, "DROP TABLE IF EXISTS uw_term_vec")
            await self._lex_exec(conn, "DELETE FROM uw_term_embeddings")
            await self._lex_exec(conn, "DELETE FROM uw_terms")
        await self.set_lexicon_cursor(0)
        self._term_vec_state = None
        self._term_vec_dim = None

    # -- term vectors --------------------------------------------------------

    async def terms_needing_vectors(
        self,
        *,
        model: str,
        dim: int,
        limit: int,
        min_doc_freq: int = 2,
        max_terms: int = 20000,
    ) -> list[dict[str, Any]]:
        """The next terms to embed: most-seen first, above the rarity floor.

        ``max_terms`` is the ceiling on the whole embedded vocabulary, not on
        this batch: once that many terms carry a vector in this space, the
        pass is done and returns nothing. Without it a corpus of build logs
        would queue every hex id it ever printed.
        """
        if limit <= 0 or not model or int(dim) <= 0:
            return []
        conn = await self._lex_conn()
        embedded_row = await self._lex_one(
            conn,
            "SELECT COUNT(*) AS n FROM uw_term_embeddings WHERE model = ? AND dim = ?",
            (model, int(dim)),
        )
        embedded = int(embedded_row["n"]) if embedded_row else 0
        remaining = max(0, int(max_terms) - embedded)
        if remaining <= 0:
            return []
        rows = await self._lex_all(
            conn,
            "SELECT t.id AS term_id, t.term AS term FROM uw_terms t"
            " WHERE t.doc_freq >= ?"
            "   AND NOT EXISTS (SELECT 1 FROM uw_term_embeddings e"
            "                   WHERE e.term_id = t.id AND e.model = ? AND e.dim = ?)"
            " ORDER BY t.doc_freq DESC, t.id LIMIT ?",
            (int(min_doc_freq), model, int(dim), min(int(limit), remaining)),
        )
        return [
            {"term_id": int(row["term_id"]), "term": str(row["term"])} for row in rows
        ]

    async def store_term_vectors(
        self,
        rows: Sequence[tuple[int, Sequence[float]]],
        *,
        model: str,
        dim: int,
    ) -> int:
        """Persist one vector per term id. Returns how many landed.

        A vector whose width disagrees with ``dim`` is DROPPED with a warning
        rather than stored: a mixed-width lexicon would make every neighbour
        query fail on a row nobody can point at.
        """
        usable = [
            (int(term_id), list(vector))
            for term_id, vector in rows
            if vector is not None and len(vector) == int(dim)
        ]
        dropped = len(rows) - len(usable)
        if dropped:
            log.warning(
                "lexicon: dropped %d term vector(s) whose width was not %d",
                dropped,
                int(dim),
            )
        if not usable:
            return 0
        now = _utc_now_iso()
        async with self._txn() as conn:  # type: ignore[attr-defined]
            for term_id, vector in usable:
                blob = _pack_vector(vector)
                if self._LEXICON_DIALECT == "postgres":
                    await self._lex_exec(
                        conn,
                        "INSERT INTO uw_term_embeddings"
                        " (term_id, model, dim, vector, created_at)"
                        " VALUES (?, ?, ?, ?, ?)"
                        " ON CONFLICT (term_id, model, dim) DO UPDATE SET"
                        "  vector = EXCLUDED.vector, created_at = EXCLUDED.created_at",
                        (term_id, model, int(dim), blob, now),
                    )
                else:
                    await self._lex_exec(
                        conn,
                        "INSERT OR REPLACE INTO uw_term_embeddings"
                        " (term_id, model, dim, vector, created_at)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (term_id, model, int(dim), blob, now),
                    )
        # The Postgres neighbour table is DERIVED from the rows just written;
        # invalidate the cached readiness so the next query refreshes it.
        if self._LEXICON_DIALECT == "postgres":
            self._term_vec_state = None
        return len(usable)

    async def term_vectors_for(
        self, terms: Sequence[str], *, model: str, dim: int
    ) -> dict[str, list[float]]:
        """Stored vectors for specific terms, keyed by term. Missing = absent.

        This is what lets the expansion build a centroid out of neighbours
        that were embedded once, at harvest time, instead of paying a provider
        call per neighbour on every search.
        """
        wanted = [term for term in dict.fromkeys(terms) if term]
        if not wanted or not model or int(dim) <= 0:
            return {}
        conn = await self._lex_conn()
        out: dict[str, list[float]] = {}
        for start in range(0, len(wanted), _IN_CHUNK):
            block = wanted[start : start + _IN_CHUNK]
            rows = await self._lex_all(
                conn,
                "SELECT t.term AS term, e.vector AS vector"  # noqa: S608 — placeholder marks only
                " FROM uw_term_embeddings e JOIN uw_terms t ON t.id = e.term_id"
                f" WHERE e.model = ? AND e.dim = ? AND t.term IN ({self._lex_marks(len(block))})",
                (model, int(dim), *block),
            )
            for row in rows:
                out[str(row["term"])] = _unpack_vector(bytes(row["vector"]))
        return out

    async def _ensure_term_vec(self, dim: int) -> tuple[bool, str]:
        """Make the term-vector nearest-neighbour path usable, or say why not.

        SQLite needs nothing but the sqlite-vec extension: the scan runs as a
        plain ``vec_distance_cosine`` over the stored BLOBs, so there is no
        second index to keep in step with anything. Postgres needs a real
        ``vector`` column, so a small ``uw_term_vec`` table is DERIVED from
        ``uw_term_embeddings`` the same way ``uw_vec`` is derived from
        ``uw_embeddings``.
        """
        if self._term_vec_state is not None and self._term_vec_dim == int(dim):
            return self._term_vec_state
        # Both engines gate on the SAME probe the document vector leg uses, so
        # a host without vectors gives one reason, not two different ones.
        ok, reason = await self._ensure_vec(None)  # type: ignore[attr-defined]
        if not ok:
            self._term_vec_state = (False, reason)
            self._term_vec_dim = int(dim)
            return self._term_vec_state
        if self._LEXICON_DIALECT == "postgres":
            conn = await self._ensure_open()  # type: ignore[attr-defined]
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS uw_term_vec ("
                " term_id BIGINT PRIMARY KEY"
                "  REFERENCES uw_terms(id) ON DELETE CASCADE,"
                f" embedding vector({int(dim)}) NOT NULL)"
            )
            # Drop what no longer belongs to the active space, then backfill.
            await conn.execute(
                "DELETE FROM uw_term_vec WHERE term_id NOT IN"
                " (SELECT term_id FROM uw_term_embeddings"
                "  WHERE model = %s AND dim = %s)",
                (await self._active_embed_model(), int(dim)),
            )
            missing = await self._fetchall(  # type: ignore[attr-defined]
                conn,
                "SELECT term_id, vector FROM uw_term_embeddings"
                " WHERE model = %s AND dim = %s"
                " AND term_id NOT IN (SELECT term_id FROM uw_term_vec)",
                (await self._active_embed_model(), int(dim)),
            )
            for row in missing:
                literal = (
                    "["
                    + ",".join(
                        f"{value:.8g}" for value in _unpack_vector(bytes(row["vector"]))
                    )
                    + "]"
                )
                await conn.execute(
                    "INSERT INTO uw_term_vec (term_id, embedding)"
                    " VALUES (%s, %s::vector) ON CONFLICT (term_id) DO NOTHING",
                    (row["term_id"], literal),
                )
        self._term_vec_dim = int(dim)
        self._term_vec_state = (True, "")
        return self._term_vec_state

    async def _active_embed_model(self) -> str:
        model, _ = await self._pinned_space()  # type: ignore[attr-defined]
        return model or ""

    async def embedding_space(self) -> tuple[str, int]:
        """The ACTIVE ``(model, dim)`` pin, or ``("", 0)`` when nothing is
        embedded yet.

        Public because the lexicon has to embed its terms into exactly the
        space the documents already live in — asking the CONFIG instead would
        silently build a second, incompatible geometry the moment someone
        switches models mid-rebuild (design rule D-3).
        """
        model, dim = await self._pinned_space()  # type: ignore[attr-defined]
        return (model or ""), int(dim or 0)

    async def term_neighbors(
        self,
        query_vector: Sequence[float],
        *,
        model: str,
        dim: int,
        limit: int = 20,
        exclude: Sequence[str] = (),
    ) -> tuple[list[dict[str, Any]], str]:
        """The vocabulary nearest ``query_vector``, best first.

        Returns ``(neighbours, reason)``. ``reason`` is empty on the healthy
        path and an honest English sentence whenever the list is empty for a
        structural cause (no vector extension, nothing embedded yet, a query
        vector from a different space). Never raises: the caller has a
        provider-free fallback and must be able to reach it.
        """
        if limit <= 0:
            return [], ""
        if not model or int(dim) <= 0:
            return [], (
                "the embedding space is not pinned yet — nothing has been "
                "embedded, so there are no word vectors to compare against"
            )
        if len(query_vector) != int(dim):
            return [], (
                f"the query vector has {len(query_vector)} components but this "
                f"store's embedding space is pinned to dim={int(dim)}"
            )
        ok, reason = await self._ensure_term_vec(int(dim))
        if not ok:
            return [], reason
        skip = {str(term).strip().lower() for term in exclude if str(term).strip()}
        # Ask for a few extra rows so excluding the query's own words cannot
        # shorten the answer below what the caller asked for.
        fetch = int(limit) + len(skip) + 4
        conn = await self._lex_vec_conn()
        if self._LEXICON_DIALECT == "postgres":
            rows = await self._fetchall(  # type: ignore[attr-defined]
                conn,
                "SELECT t.term AS term, t.doc_freq AS doc_freq,"
                " (v.embedding <=> %s::vector) AS distance"
                " FROM uw_term_vec v JOIN uw_terms t ON t.id = v.term_id"
                " ORDER BY distance LIMIT %s",
                (
                    "["
                    + ",".join(f"{value:.8g}" for value in query_vector)
                    + "]",
                    fetch,
                ),
            )
        else:
            try:
                rows = await self._fetchall(  # type: ignore[attr-defined]
                    conn,
                    "SELECT t.term AS term, t.doc_freq AS doc_freq,"
                    " vec_distance_cosine(e.vector, ?) AS distance"
                    " FROM uw_term_embeddings e JOIN uw_terms t ON t.id = e.term_id"
                    " WHERE e.model = ? AND e.dim = ?"
                    " ORDER BY distance LIMIT ?",
                    (_pack_vector(query_vector), model, int(dim), fetch),
                )
            except Exception as exc:  # noqa: BLE001 — degrade, never fail a search
                log.info("lexicon: term nearest-neighbour scan unavailable (%s)", exc)
                self._term_vec_state = (
                    False,
                    "this SQLite build cannot compare word vectors "
                    f"({type(exc).__name__}); related words fall back to "
                    "co-occurrence in the text",
                )
                return [], self._term_vec_state[1]
        neighbours: list[dict[str, Any]] = []
        for row in rows:
            term = str(row["term"])
            if term.lower() in skip:
                continue
            neighbours.append(
                {
                    "term": term,
                    "similarity": round(
                        _cosine_similarity_from_distance(row["distance"]), 4
                    ),
                    "doc_freq": int(row["doc_freq"] or 0),
                }
            )
            if len(neighbours) >= int(limit):
                break
        if not neighbours:
            return [], (
                "no word vectors are stored yet — the background lexicon pass "
                "builds them once an embedding provider is configured"
            )
        return neighbours, ""

    # -- provider-free fallback + passage lookup ----------------------------

    async def text_samples_for_term(
        self, term: str, *, limit: int = 60, area_id: str | None = None
    ) -> list[dict[str, Any]]:
        """A bounded sample of the text that contains ``term`` (keyword, not vector).

        The input of the co-occurrence fallback: with no embedding provider,
        the words that keep company with the query word in real text are still
        a genuine, if blunter, notion of "related". Bounded on purpose — this
        SAMPLES the corpus, it does not walk it.

        Passages are preferred when the item has been embedded, because a
        900-character window is a much sharper measure of "keeps company with"
        than a whole file. A store with no passages yet — precisely the
        keyword-only install this fallback exists for — is sampled at item
        level instead, so the path is never simply dead.
        """
        cleaned = " ".join(str(term or "").split())
        if not cleaned or limit <= 0:
            return []
        conn = await self._lex_conn()
        postgres = self._LEXICON_DIALECT == "postgres"
        if postgres:
            match_clause = "i.search_tsv @@ websearch_to_tsquery('simple', %s)"
            match_param: Any = cleaned
            area_clause = " AND i.areas_json::jsonb @> to_jsonb(%s::text)"
            mark = "%s"
            source = " FROM uw_documents d JOIN uw_items i ON i.id = d.item_id WHERE "
        else:
            match_clause = "uw_fts MATCH ?"
            match_param = '"' + cleaned.replace('"', "") + '"'
            area_clause = (
                " AND EXISTS (SELECT 1 FROM json_each(i.areas_json)"
                " WHERE json_each.value = ?)"
            )
            mark = "?"
            source = (
                " FROM uw_fts JOIN uw_items i ON i.id = uw_fts.item_id"
                " JOIN uw_documents d ON d.item_id = i.id WHERE "
            )

        passage_sql = (
            "SELECT d.id AS document_id, d.item_id AS item_id, d.text_norm AS text"
            + source
            + match_clause
            + " AND i.deleted_at IS NULL"
        )
        params: list[Any] = [match_param]
        if area_id is not None:
            passage_sql += area_clause
            params.append(area_id)
        passage_sql += f" ORDER BY d.id DESC LIMIT {mark}"
        params.append(int(limit))
        rows = await self._fetchall(conn, passage_sql, params)  # type: ignore[attr-defined]
        if rows:
            return [
                {
                    "document_id": int(row["document_id"]),
                    "item_id": int(row["item_id"]),
                    "text": str(row["text"] or ""),
                }
                for row in rows
            ]

        item_source = (
            " FROM uw_items i WHERE "
            if postgres
            else " FROM uw_fts JOIN uw_items i ON i.id = uw_fts.item_id WHERE "
        )
        item_sql = (
            "SELECT i.id AS item_id, i.title AS title, i.body_raw AS body_raw"
            + item_source
            + match_clause
            + " AND i.deleted_at IS NULL"
        )
        params = [match_param]
        if area_id is not None:
            item_sql += area_clause
            params.append(area_id)
        item_sql += f" ORDER BY i.id DESC LIMIT {mark}"
        params.append(int(limit))
        rows = await self._fetchall(conn, item_sql, params)  # type: ignore[attr-defined]
        return [
            {
                "document_id": None,
                "item_id": int(row["item_id"]),
                "text": f"{row['title'] or ''} {row['body_raw'] or ''}",
            }
            for row in rows
        ]

    async def passages_for_items(
        self, item_ids: Sequence[int], *, limit: int = 400
    ) -> list[dict[str, Any]]:
        """Every stored passage of the given items, with its char span.

        The raw material of passage localization: a keyword hit knows its item
        but not WHERE in it the answer sits, and this is the only table that
        can say. Bounded by ``limit`` across the whole call — a single 200 KB
        file is hundreds of passages and the ranking above needs a sample, not
        the file.
        """
        ids = [int(value) for value in dict.fromkeys(item_ids)]
        if not ids or limit <= 0:
            return []
        conn = await self._lex_conn()
        out: list[dict[str, Any]] = []
        for start in range(0, len(ids), _IN_CHUNK):
            block = ids[start : start + _IN_CHUNK]
            rows = await self._fetchall(  # type: ignore[attr-defined]
                conn,
                "SELECT id AS document_id, item_id, chunk_index, char_start,"  # noqa: S608 — placeholder marks only
                " char_end, text_norm"
                f" FROM uw_documents WHERE item_id IN ({self._lex_marks(len(block))})"
                " ORDER BY item_id, chunk_index"
                f" LIMIT {int(limit)}",
                block,
            )
            for row in rows:
                out.append(
                    {
                        "document_id": int(row["document_id"]),
                        "item_id": int(row["item_id"]),
                        "chunk_index": int(row["chunk_index"] or 0),
                        "char_start": int(row["char_start"] or 0),
                        "char_end": int(row["char_end"] or 0),
                        "text_norm": str(row["text_norm"] or ""),
                    }
                )
            if len(out) >= int(limit):
                break
        return out[: int(limit)]
