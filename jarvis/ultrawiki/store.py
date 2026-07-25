"""UltraWiki unified store — SQLite reference backend + Postgres variant.

One SQL surface, two backends (design doc 01):

- :class:`UltraStore` — the universal floor. Own DB file
  ``<data_dir>/ultrawiki.db`` following the ``missions.db`` precedent
  (ADR-0009): the staged ingest pipeline is a heavy writer and must not
  contend with the shared ``jarvis.db`` tenants. Keyword leg is FTS5; the
  vector leg is the sqlite-vec ``vec0`` extension, queried through plain SQL
  (never a vector-SDK import), loaded lazily and degraded honestly when the
  host cannot load it.
- :class:`PostgresStore` — the cloud/self-hosted option behind the same
  public interface. Keyword leg is a generated ``tsvector`` column + GIN
  index; the vector leg is pgvector. The driver (``psycopg``) is an optional
  extra and imported lazily; a missing driver raises an honest
  ``ImportError`` naming the install command.

Store discipline:

- Lazy open on first use; ``schema.sql`` is applied idempotently on every
  open and forward-only migrations under ``jarvis/ultrawiki/migrations/``
  ride the ``PRAGMA user_version`` runner from
  ``jarvis/memory/migration_runner.py``.
- The staged state machine (design doc 02) lives in ``uw_items.state``;
  workers claim a batch, perform exactly one transition, and commit. The
  contentless-FTS delete+insert for the keyword stage happens in the SAME
  transaction as the state transition.
- Vectors are stored provider-neutral as little-endian float32 BLOBs in
  ``uw_embeddings``; the ``uw_vec`` ANN index is DERIVED from that table, so
  a host that gains the extension later backfills without re-embedding.
- The store is policy-free: consent gating, scheduling, and provider calls
  belong to the pipeline runtime. Nothing in this module touches the
  network or reads credentials.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from jarvis.ultrawiki.types import (
    STATE_ORDER,
    ConsentState,
    DocType,
    ItemState,
    PipelineCounts,
    RawItem,
    SearchResult,
    content_hash_for,
)

log = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: Retry policy (design doc 02): backoff 60s * 4^n, capped at 6h,
#: dead-letter (state ``failed``) after 5 attempts.
MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 60
BACKOFF_CAP_S = 6 * 3600

#: ``uw_meta`` keys pinning the embedding space (semi-permanent, D-3).
META_EMBED_MODEL = "embed_model"
META_EMBED_DIM = "embed_dim"

_SNIPPET_CHARS = 240
_IN_CHUNK = 400

_FTS_QUOTE_STRIP_RE = re.compile(r'"')

#: Seconds a Postgres connect attempt may take before it gives up. The store
#: opens on the app's startup path, so an unreachable host must fail fast and
#: degrade to SQLite instead of stalling the boot.
PG_CONNECT_TIMEOUT_S = 5

#: ``scheme://user:password@host`` userinfo inside any connection string a
#: driver may echo back in an error message.
_DSN_USERINFO_RE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<userinfo>[^/@\s]+)@"
)


class UltraStoreError(RuntimeError):
    """Raised for store-contract violations (e.g. embedding-dim mismatch)."""


def sanitize_conn_error(exc: BaseException, conn_str: str = "") -> str:
    """``"TypeName: message"`` with every credential scrubbed out.

    A psycopg failure routinely echoes the whole connection string — including
    the password — and that text lands in ``/api/ultrawiki/status``
    degradations and the ``/test/storage`` result. Both the stored connection
    string itself and any ``scheme://user:password@`` userinfo are replaced by
    ``***`` before the message is allowed to leave the store.
    """
    text = f"{type(exc).__name__}: {exc}"
    if conn_str:
        text = text.replace(conn_str, "***")
    return _DSN_USERINFO_RE.sub(lambda m: f"{m.group('scheme')}***@", text)


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    """Result of one :meth:`UltraStore.upsert_items` batch."""

    new: int = 0
    changed: int = 0
    unchanged: int = 0
    tombstoned: int = 0


# ---------------------------------------------------------------------------
# Shared helpers (both backends)
# ---------------------------------------------------------------------------


def resolve_ultrawiki_db_path(data_dir: str | Path | None = None) -> Path:
    """Return one absolute ``ultrawiki.db`` path independent of process CWD.

    Mirrors ``jarvis/memory/wiki/db_path.py``: relative ``data_dir`` values
    (the ``cfg.memory.data_dir`` default is ``./data``) are anchored at the
    repo root, never the process CWD.
    """
    from jarvis.core.paths import repo_root  # lazy: keep module import cheap

    raw = Path(data_dir) if data_dir is not None else Path("data")
    directory = raw if raw.is_absolute() else repo_root() / raw
    return (directory / "ultrawiki.db").resolve(strict=False)


def pack_vector(vector: Sequence[float]) -> bytes:
    """Serialize a vector as little-endian float32 (the sqlite-vec format)."""
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes) -> list[float]:
    """Inverse of :func:`pack_vector`."""
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def _import_sqlite_vec() -> Any:
    """Import hook for the optional sqlite-vec extension (test seam)."""
    import sqlite_vec  # noqa: PLC0415 — deliberately lazy (AP-26)

    return sqlite_vec


def _import_psycopg() -> Any:
    """Import hook for the optional Postgres driver (test seam)."""
    try:
        import psycopg  # noqa: PLC0415 — deliberately lazy optional extra
    except ImportError as exc:
        raise ImportError(
            "PostgresStore requires the 'psycopg' driver, which is not "
            "installed. Install it with: pip install "
            "personal-jarvis[ultrawiki-postgres]. The SQLite backend keeps "
            "working without it."
        ) from exc
    return psycopg


def _iso_utc(moment: datetime | None = None) -> str:
    """Canonical ISO-8601 UTC second-precision string (lexicographically
    ordered, so TEXT comparisons in SQL behave like time comparisons)."""
    dt = moment if moment is not None else datetime.now(UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_now(now: str | datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if isinstance(now, datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _coerce_state(state: ItemState | str) -> ItemState:
    try:
        return ItemState(state)
    except ValueError as exc:
        valid = ", ".join(s.value for s in ItemState)
        raise ValueError(f"unknown item state {state!r} (valid: {valid})") from exc


def _predecessor_of(target: ItemState | str) -> ItemState:
    coerced = _coerce_state(target)
    try:
        index = STATE_ORDER.index(coerced)
    except ValueError:
        raise ValueError(
            f"cannot claim toward {coerced.value!r} — it is outside the "
            "forward state ladder"
        ) from None
    if index == 0:
        raise ValueError(
            "cannot claim toward 'captured' — items enter that state via "
            "upsert_items, not a worker transition"
        )
    return STATE_ORDER[index - 1]


def _retry_delay_s(attempts_before: int) -> int:
    return min(BACKOFF_BASE_S * 4 ** max(0, attempts_before), BACKOFF_CAP_S)


def _content_identity(item: RawItem) -> str:
    """Content hash driving change detection (title + body)."""
    return content_hash_for(item.title, item.body)


def _fts_match_expr(query: str) -> str:
    """OR-combined quoted tokens; quoting neutralizes FTS5 operators."""
    tokens = [_FTS_QUOTE_STRIP_RE.sub("", tok) for tok in query.split() if tok.strip()]
    return " OR ".join(f'"{tok}"' for tok in tokens if tok)


def _normalize_bm25(raw: float) -> float:
    """FTS5 bm25 (lower = better, usually negative) -> [0, 1] higher-is-better."""
    return 1.0 / (1.0 + max(0.0, float(raw)))


def _distance_score(distance: float) -> float:
    """Monotonic distance -> [0, 1] score (works for cosine and L2)."""
    return 1.0 / (1.0 + max(0.0, float(distance)))


def _snippet_of(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:_SNIPPET_CHARS]


def _neighbors_per_side(limit: int) -> int:
    """How many neighbours to pull on EACH side for a total budget of
    ``limit`` (the article's "two neighboring sections" = one per side)."""
    return max(1, (int(limit) + 1) // 2)


def _neighbor_snippets(before: Iterable[Any], after: Iterable[Any]) -> list[str]:
    """Interleave the preceding and following rows, nearest neighbour first."""
    out: list[str] = []
    for row in before:
        out.append(_snippet_of(f"{row['title']} {row['body_raw']}"))
    for row in after:
        out.append(_snippet_of(f"{row['title']} {row['body_raw']}"))
    return out


def _chunks(values: Sequence[Any], size: int = _IN_CHUNK) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _placeholders(count: int) -> str:
    return ",".join("?" * count)


def _counts_from_pairs(pairs: Iterable[tuple[str, int]]) -> PipelineCounts:
    by_state = {state.value: 0 for state in ItemState}
    for state_value, count in pairs:
        if state_value in by_state:
            by_state[state_value] = int(count)
    return PipelineCounts(**by_state)


_UNSET: Any = object()


# ---------------------------------------------------------------------------
# SQLite reference backend
# ---------------------------------------------------------------------------


class UltraStore:
    """Async SQLite store for UltraWiki (the universal reference backend).

    One instance = one ``aiosqlite`` connection, opened lazily on first use
    (so the store can be constructed synchronously and opened on the right
    event loop). Autocommit mode; multi-statement writes run inside explicit
    ``BEGIN IMMEDIATE`` transactions serialized by an ``asyncio.Lock``.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        # sqlite-vec bookkeeping (per instance / per connection).
        self._vec_ext_loaded = False
        self._vec_state: tuple[bool, str] | None = None
        self._vec_dim: int | None = None

    # -- lifecycle ---------------------------------------------------------

    async def open(self) -> None:
        """Open + apply the idempotent base schema and pending migrations."""
        async with self._lock:
            if self._conn is not None:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(
                self._db_path,
                isolation_level=None,  # autocommit — WAL is the lock manager
            )
            conn.row_factory = aiosqlite.Row
            schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
            await conn.executescript(schema_sql)
            from jarvis.memory.migration_runner import (  # noqa: PLC0415 — lazy
                run_migrations,
            )

            await run_migrations(conn, directory=_MIGRATIONS_DIR)
            self._conn = conn

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
            self._vec_ext_loaded = False
            self._vec_state = None
            self._vec_dim = None

    async def _ensure_open(self) -> aiosqlite.Connection:
        if self._conn is None:
            await self.open()
        assert self._conn is not None
        return self._conn

    @asynccontextmanager
    async def _txn(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = await self._ensure_open()
        async with self._lock:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                await conn.execute("ROLLBACK")
                raise
            else:
                await conn.execute("COMMIT")

    @staticmethod
    async def _fetchall(
        conn: aiosqlite.Connection, sql: str, params: Sequence[Any] = ()
    ) -> list[aiosqlite.Row]:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return list(rows)

    @staticmethod
    async def _fetchone(
        conn: aiosqlite.Connection, sql: str, params: Sequence[Any] = ()
    ) -> aiosqlite.Row | None:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
        await cur.close()
        return row

    # -- sources & consent -------------------------------------------------

    async def upsert_source(
        self,
        source_id: str,
        *,
        connector: str,
        label: str,
        config: dict[str, Any] | None = None,
        areas: list[str] | None = None,
    ) -> None:
        """Create or update a configured source.

        Consent and enablement are user-granted state and are NEVER reset by
        an upsert; ``config``/``areas`` of ``None`` keep the existing values
        on update.
        """
        now = _iso_utc()
        async with self._txn() as conn:
            row = await self._fetchone(
                conn, "SELECT id FROM uw_sources WHERE id = ?", (source_id,)
            )
            if row is None:
                await conn.execute(
                    "INSERT INTO uw_sources"
                    " (id, connector, label, config_json, areas_json, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        source_id,
                        connector,
                        label,
                        json.dumps(config or {}),
                        json.dumps(areas or []),
                        now,
                    ),
                )
            else:
                sets = ["connector = ?", "label = ?"]
                params: list[Any] = [connector, label]
                if config is not None:
                    sets.append("config_json = ?")
                    params.append(json.dumps(config))
                if areas is not None:
                    sets.append("areas_json = ?")
                    params.append(json.dumps(areas))
                params.append(source_id)
                await conn.execute(
                    f"UPDATE uw_sources SET {', '.join(sets)} WHERE id = ?",  # noqa: S608 — column list is a code-owned literal
                    params,
                )

    @staticmethod
    def _source_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "connector": row["connector"],
            "label": row["label"],
            "config": json.loads(row["config_json"] or "{}"),
            "areas": json.loads(row["areas_json"] or "[]"),
            "consent": row["consent"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "last_sync_at": row["last_sync_at"],
            "last_error": row["last_error"],
        }

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT * FROM uw_sources WHERE id = ?", (source_id,)
        )
        return None if row is None else self._source_row_to_dict(row)

    async def list_sources(self) -> list[dict[str, Any]]:
        """All sources with per-source pipeline counts and sync state."""
        conn = await self._ensure_open()
        rows = await self._fetchall(conn, "SELECT * FROM uw_sources ORDER BY id")
        count_rows = await self._fetchall(
            conn,
            "SELECT source_id, state, COUNT(*) AS n FROM uw_items"
            " WHERE deleted_at IS NULL GROUP BY source_id, state",
        )
        per_source: dict[str, list[tuple[str, int]]] = {}
        for crow in count_rows:
            per_source.setdefault(crow["source_id"], []).append(
                (crow["state"], crow["n"])
            )
        sync_rows = await self._fetchall(conn, "SELECT * FROM uw_sync_state")
        sync_by_id = {srow["source_id"]: dict(srow) for srow in sync_rows}
        result = []
        for row in rows:
            entry = self._source_row_to_dict(row)
            entry["counts"] = _counts_from_pairs(per_source.get(row["id"], []))
            sync = sync_by_id.get(row["id"])
            if sync is not None:
                sync.pop("source_id", None)
            entry["sync_state"] = sync
            result.append(entry)
        return result

    async def set_consent(self, source_id: str, consent: ConsentState | str) -> None:
        value = ConsentState(consent).value
        conn = await self._ensure_open()
        await conn.execute(
            "UPDATE uw_sources SET consent = ? WHERE id = ?", (value, source_id)
        )

    async def set_enabled(self, source_id: str, enabled: bool) -> None:
        conn = await self._ensure_open()
        await conn.execute(
            "UPDATE uw_sources SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, source_id),
        )

    async def set_source_status(
        self,
        source_id: str,
        *,
        last_sync_at: str | None = _UNSET,
        last_error: str | None = _UNSET,
    ) -> None:
        """Partial update of the per-source sync status columns."""
        sets: list[str] = []
        params: list[Any] = []
        if last_sync_at is not _UNSET:
            sets.append("last_sync_at = ?")
            params.append(last_sync_at)
        if last_error is not _UNSET:
            sets.append("last_error = ?")
            params.append(last_error)
        if not sets:
            return
        params.append(source_id)
        conn = await self._ensure_open()
        await conn.execute(
            f"UPDATE uw_sources SET {', '.join(sets)} WHERE id = ?",  # noqa: S608 — column list is a code-owned literal
            params,
        )

    async def delete_source(self, source_id: str, *, purge: bool) -> None:
        """Remove a source.

        ``purge=True`` deletes the source and EVERY derived row (items,
        documents, embeddings, FTS and vector entries, sync state).
        ``purge=False`` disconnects only: consent is revoked and the source
        disabled, but captured data stays in the store (the schema's
        ``ON DELETE CASCADE`` makes a row delete inherently purging, so a
        keep-the-data delete must keep the source row).
        """
        if not purge:
            conn = await self._ensure_open()
            await conn.execute(
                "UPDATE uw_sources SET consent = ?, enabled = 0 WHERE id = ?",
                (ConsentState.REVOKED.value, source_id),
            )
            return
        async with self._txn() as conn:
            rows = await self._fetchall(
                conn, "SELECT id FROM uw_items WHERE source_id = ?", (source_id,)
            )
            await self._purge_derived(conn, [row["id"] for row in rows])
            await conn.execute("DELETE FROM uw_sources WHERE id = ?", (source_id,))

    # -- items: the staged state machine -----------------------------------

    async def upsert_items(
        self, source_id: str, items: Sequence[RawItem]
    ) -> UpsertCounts:
        """Idempotent batch upsert on ``UNIQUE (source_id, external_id)``.

        - Unchanged content hash: the stored row is left completely
          untouched (zero new work — roadmap gate P1c).
        - Changed content: the row resets to ``captured`` and every stale
          derived row (FTS, documents, embeddings, vector entries) is
          removed in the same transaction.
        - ``RawItem.deleted=True``: tombstone (``deleted_at`` set, derived
          rows removed). Unknown deleted external ids are ignored.

        The whole batch is ONE transaction: a crash mid-batch rolls back to
        the previous consistent state and the re-run converges (gate P1a).
        """
        source = await self.get_source(source_id)
        if source is None:
            raise UltraStoreError(
                f"unknown source {source_id!r} — call upsert_source() first"
            )
        areas_json = json.dumps(source["areas"])
        now = _iso_utc()
        new = changed = unchanged = tombstoned = 0
        async with self._txn() as conn:
            for item in items:
                row = await self._fetchone(
                    conn,
                    "SELECT id, content_hash, deleted_at FROM uw_items"
                    " WHERE source_id = ? AND external_id = ?",
                    (source_id, item.external_id),
                )
                if item.deleted:
                    if row is not None and row["deleted_at"] is None:
                        await self._purge_derived(conn, [row["id"]])
                        await conn.execute(
                            "UPDATE uw_items SET deleted_at = ?, updated_at = ?"
                            " WHERE id = ?",
                            (now, now, row["id"]),
                        )
                        tombstoned += 1
                    continue
                identity = _content_identity(item)
                if row is None:
                    await conn.execute(
                        "INSERT INTO uw_items"
                        " (source_id, external_id, thread_key, author_raw, title,"
                        "  body_raw, permalink, timestamp_utc, areas_json,"
                        "  content_hash, state, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            source_id,
                            item.external_id,
                            item.thread_key,
                            item.author_raw,
                            item.title,
                            item.body,
                            item.permalink,
                            item.timestamp_utc,
                            areas_json,
                            identity,
                            ItemState.CAPTURED.value,
                            now,
                            now,
                        ),
                    )
                    new += 1
                elif row["content_hash"] == identity and row["deleted_at"] is None:
                    unchanged += 1
                else:
                    # Changed content (or a resurrected tombstone): reset to
                    # captured and drop every stale derived row.
                    await self._purge_derived(conn, [row["id"]])
                    await conn.execute(
                        "UPDATE uw_items SET thread_key = ?, author_raw = ?,"
                        " title = ?, body_raw = ?, permalink = ?,"
                        " timestamp_utc = ?, areas_json = ?, content_hash = ?,"
                        " state = ?, attempt_count = 0, next_retry_at = NULL,"
                        " last_error = NULL, deleted_at = NULL, updated_at = ?"
                        " WHERE id = ?",
                        (
                            item.thread_key,
                            item.author_raw,
                            item.title,
                            item.body,
                            item.permalink,
                            item.timestamp_utc,
                            areas_json,
                            identity,
                            ItemState.CAPTURED.value,
                            now,
                            row["id"],
                        ),
                    )
                    changed += 1
        return UpsertCounts(
            new=new, changed=changed, unchanged=unchanged, tombstoned=tombstoned
        )

    async def _purge_derived(
        self, conn: aiosqlite.Connection, item_ids: Sequence[int]
    ) -> None:
        """Remove FTS rows, documents (cascading embeddings) and — when the
        vec index is live on this connection — vector rows for *item_ids*.

        Vector rows a session without the extension cannot delete are
        reconciled on the next :meth:`_ensure_vec` (stale-row sweep).
        """
        if not item_ids:
            return
        for chunk in _chunks(list(item_ids)):
            marks = _placeholders(len(chunk))
            await conn.execute(
                f"DELETE FROM uw_fts WHERE item_id IN ({marks})",  # noqa: S608 — placeholders only
                chunk,
            )
            doc_rows = await self._fetchall(
                conn,
                f"SELECT id FROM uw_documents WHERE item_id IN ({marks})",  # noqa: S608 — placeholders only
                chunk,
            )
            doc_ids = [doc["id"] for doc in doc_rows]
            if doc_ids and self._vec_state is not None and self._vec_state[0]:
                for doc_chunk in _chunks(doc_ids):
                    await conn.execute(
                        f"DELETE FROM uw_vec WHERE rowid IN ({_placeholders(len(doc_chunk))})",  # noqa: S608 — placeholders only
                        doc_chunk,
                    )
            await conn.execute(
                f"DELETE FROM uw_documents WHERE item_id IN ({marks})",  # noqa: S608 — placeholders only
                chunk,
            )

    @staticmethod
    def _item_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "source_id": row["source_id"],
            "external_id": row["external_id"],
            "thread_key": row["thread_key"],
            "author_raw": row["author_raw"],
            "title": row["title"],
            "body_raw": row["body_raw"],
            "permalink": row["permalink"],
            "timestamp_utc": row["timestamp_utc"],
            "areas": json.loads(row["areas_json"] or "[]"),
            "content_hash": row["content_hash"],
            "state": row["state"],
            "attempt_count": row["attempt_count"],
            "next_retry_at": row["next_retry_at"],
            "last_error": row["last_error"],
            "deleted_at": row["deleted_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def get_item(self, item_id: int) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT * FROM uw_items WHERE id = ?", (item_id,)
        )
        return None if row is None else self._item_row_to_dict(row)

    async def get_item_by_external_id(
        self, source_id: str, external_id: str
    ) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn,
            "SELECT * FROM uw_items WHERE source_id = ? AND external_id = ?",
            (source_id, external_id),
        )
        return None if row is None else self._item_row_to_dict(row)

    async def claim_batch(
        self,
        target_state: ItemState | str,
        *,
        limit: int = 50,
        now: str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Items ready to advance TO *target_state* (their state is the
        predecessor in ``STATE_ORDER``), retry-eligible, newest first.

        Claiming is read-only — the single-process pipeline claims, works,
        then commits exactly one transition via :meth:`mark_stage_done` /
        :meth:`mark_retry`, so a crash between claim and commit loses
        nothing (the item is simply claimed again).
        """
        predecessor = _predecessor_of(target_state)
        now_s = _iso_utc(_coerce_now(now))
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT * FROM uw_items"
            " WHERE state = ? AND deleted_at IS NULL"
            "   AND (next_retry_at IS NULL OR next_retry_at <= ?)"
            " ORDER BY timestamp_utc DESC, id DESC LIMIT ?",
            (predecessor.value, now_s, int(limit)),
        )
        return [self._item_row_to_dict(row) for row in rows]

    async def mark_stage_done(
        self,
        item_id: int,
        new_state: ItemState | str,
        *,
        fts_title: str | None = None,
        fts_body: str | None = None,
        expected_state: ItemState | str | None = None,
        expected_content_hash: str | None = None,
    ) -> bool:
        """Commit one state transition; retry bookkeeping resets.

        For the keyword stage the FTS delete+insert happens in the SAME
        transaction as the state transition (pass ``fts_title``/``fts_body``)
        so the index can never drift from the state column.

        **Compare-and-set (the lost-claim guard).** A sync running concurrently
        with the pipeline can reset an item to ``captured`` and purge its
        derived rows the moment its content changes (:meth:`upsert_items`) —
        between a worker's claim and its commit. An unconditional UPDATE would
        then stamp e.g. ``embedded`` onto content that is no longer keyword
        indexed and whose vector belongs to the OLD text. Passing
        ``expected_state`` (the predecessor state seen at claim time) and
        ``expected_content_hash`` narrows the UPDATE to exactly that row
        version: when it no longer matches, nothing is written (no FTS row
        either) and ``False`` says "claim lost, skip this item this pass".
        The pipeline always passes both; callers that omit them get the
        unconditional legacy behaviour.

        Returns ``True`` when the transition was committed.
        """
        state = _coerce_state(new_state)
        if state not in STATE_ORDER or state is ItemState.CAPTURED:
            raise ValueError(
                f"mark_stage_done cannot set {state.value!r} — only forward "
                "stages after 'captured' are worker transitions"
            )
        now = _iso_utc()
        sql = (
            "UPDATE uw_items SET state = ?, attempt_count = 0,"
            " next_retry_at = NULL, last_error = NULL, updated_at = ?"
            " WHERE id = ?"
        )
        params: list[Any] = [state.value, now, item_id]
        if expected_state is not None:
            sql += " AND state = ?"
            params.append(_coerce_state(expected_state).value)
        if expected_content_hash is not None:
            sql += " AND content_hash = ?"
            params.append(expected_content_hash)
        async with self._txn() as conn:
            cur = await conn.execute(sql, params)
            claimed = cur.rowcount != 0
            await cur.close()
            if not claimed:
                return False
            if fts_body is not None or fts_title is not None:
                await conn.execute(
                    "DELETE FROM uw_fts WHERE item_id = ?", (item_id,)
                )
                await conn.execute(
                    "INSERT INTO uw_fts (item_id, title, body) VALUES (?, ?, ?)",
                    (item_id, fts_title or "", fts_body or ""),
                )
        return True

    async def mark_retry(
        self,
        item_id: int,
        error: str,
        *,
        now: str | datetime | None = None,
    ) -> None:
        """Record a retryable stage failure.

        Attempts 1..4 keep the last good state and schedule
        ``next_retry_at = now + 60s * 4^(attempt-1)`` (capped at 6h); the
        5th failure dead-letters the item (state ``failed``).
        """
        moment = _coerce_now(now)
        async with self._txn() as conn:
            row = await self._fetchone(
                conn,
                "SELECT attempt_count FROM uw_items WHERE id = ?",
                (item_id,),
            )
            if row is None:
                return
            attempts_before = int(row["attempt_count"])
            new_count = attempts_before + 1
            if new_count >= MAX_ATTEMPTS:
                await conn.execute(
                    "UPDATE uw_items SET state = ?, attempt_count = ?,"
                    " next_retry_at = NULL, last_error = ?, updated_at = ?"
                    " WHERE id = ?",
                    (
                        ItemState.FAILED.value,
                        new_count,
                        error,
                        _iso_utc(moment),
                        item_id,
                    ),
                )
            else:
                retry_at = moment + timedelta(seconds=_retry_delay_s(attempts_before))
                await conn.execute(
                    "UPDATE uw_items SET attempt_count = ?, next_retry_at = ?,"
                    " last_error = ?, updated_at = ? WHERE id = ?",
                    (new_count, _iso_utc(retry_at), error, _iso_utc(moment), item_id),
                )

    async def mark_failed(self, item_id: int, error: str) -> None:
        """Dead-letter an item immediately (non-retryable poison)."""
        now = _iso_utc()
        conn = await self._ensure_open()
        await conn.execute(
            "UPDATE uw_items SET state = ?, next_retry_at = NULL,"
            " last_error = ?, updated_at = ? WHERE id = ?",
            (ItemState.FAILED.value, error, now, item_id),
        )

    async def requeue_failed(self, source_id: str | None = None) -> int:
        """Give every dead-lettered item another run; returns the count moved.

        Dead-lettering is permanent by design (5 attempts, then ``failed``), so
        a transient outage — a chat provider without credit while the distill
        stage ran, a dead embedding endpoint — silently strands items forever
        once the user fixes the cause. This is the recovery path.

        The state each item returns to is DERIVED from the rows it actually
        owns, never guessed: a stored embedding means the embed stage really
        finished (``embedded``), an FTS row means the keyword stage finished
        (``keyword_indexed``), and anything else restarts at ``captured``.
        Retry bookkeeping (attempt count, backoff, last error) is cleared so
        the pipeline picks the items up on its next pass.
        """
        now = _iso_utc()
        moved = 0
        async with self._txn() as conn:
            sql = (
                "SELECT i.id AS id,"
                " EXISTS (SELECT 1 FROM uw_documents d"
                "  JOIN uw_embeddings e ON e.document_id = d.id"
                "  WHERE d.item_id = i.id) AS has_vector,"
                " EXISTS (SELECT 1 FROM uw_fts f WHERE f.item_id = i.id) AS has_fts"
                " FROM uw_items i"
                " WHERE i.state = ? AND i.deleted_at IS NULL"
            )
            params: list[Any] = [ItemState.FAILED.value]
            if source_id is not None:
                sql += " AND i.source_id = ?"
                params.append(source_id)
            rows = await self._fetchall(conn, sql, params)
            for row in rows:
                if int(row["has_vector"]):
                    target = ItemState.EMBEDDED
                elif int(row["has_fts"]):
                    target = ItemState.KEYWORD_INDEXED
                else:
                    target = ItemState.CAPTURED
                await conn.execute(
                    "UPDATE uw_items SET state = ?, attempt_count = 0,"
                    " next_retry_at = NULL, last_error = NULL, updated_at = ?"
                    " WHERE id = ?",
                    (target.value, now, row["id"]),
                )
                moved += 1
        return moved

    async def counts(self) -> PipelineCounts:
        """Per-stage backlog counts over live (non-tombstoned) items."""
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT state, COUNT(*) AS n FROM uw_items"
            " WHERE deleted_at IS NULL GROUP BY state",
        )
        return _counts_from_pairs((row["state"], row["n"]) for row in rows)

    async def counts_for_source(self, source_id: str) -> PipelineCounts:
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT state, COUNT(*) AS n FROM uw_items"
            " WHERE deleted_at IS NULL AND source_id = ? GROUP BY state",
            (source_id,),
        )
        return _counts_from_pairs((row["state"], row["n"]) for row in rows)

    async def reconcile_deletes(
        self, source_id: str, yielded_external_ids: set[str]
    ) -> int:
        """Tombstone every stored live item of *source_id* that a FULL
        backfill did not yield (connectors emit no tombstones — delete
        detection is runtime-side, connector convention). Returns the number
        of items tombstoned.
        """
        now = _iso_utc()
        async with self._txn() as conn:
            rows = await self._fetchall(
                conn,
                "SELECT id, external_id FROM uw_items"
                " WHERE source_id = ? AND deleted_at IS NULL",
                (source_id,),
            )
            doomed = [
                row["id"]
                for row in rows
                if row["external_id"] not in yielded_external_ids
            ]
            if doomed:
                await self._purge_derived(conn, doomed)
                for chunk in _chunks(doomed):
                    await conn.execute(
                        f"UPDATE uw_items SET deleted_at = ?, updated_at = ?"  # noqa: S608 — placeholders only
                        f" WHERE id IN ({_placeholders(len(chunk))})",
                        [now, now, *chunk],
                    )
        return len(doomed)

    # -- documents & vectors ------------------------------------------------

    async def add_document(
        self,
        item_id: int,
        doc_type: DocType | str,
        text_norm: str,
        *,
        distill_json: str | None = None,
        distill_version: int = 0,
        content_hash: str = "",
    ) -> int:
        """Insert a derived document, replacing any existing document of the
        same ``(item_id, doc_type)``. Returns the new document id."""
        kind = DocType(doc_type).value
        now = _iso_utc()
        async with self._txn() as conn:
            old_rows = await self._fetchall(
                conn,
                "SELECT id FROM uw_documents WHERE item_id = ? AND doc_type = ?",
                (item_id, kind),
            )
            old_ids = [row["id"] for row in old_rows]
            if old_ids and self._vec_state is not None and self._vec_state[0]:
                for chunk in _chunks(old_ids):
                    await conn.execute(
                        f"DELETE FROM uw_vec WHERE rowid IN ({_placeholders(len(chunk))})",  # noqa: S608 — placeholders only
                        chunk,
                    )
            if old_ids:
                await conn.execute(
                    "DELETE FROM uw_documents WHERE item_id = ? AND doc_type = ?",
                    (item_id, kind),
                )
            cur = await conn.execute(
                "INSERT INTO uw_documents (item_id, doc_type, text_norm,"
                " distill_json, distill_version, content_hash, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (
                    item_id,
                    kind,
                    text_norm,
                    distill_json,
                    int(distill_version),
                    content_hash,
                    now,
                ),
            )
            row = await cur.fetchone()
            await cur.close()
            assert row is not None
            return int(row[0])

    async def _pinned_space(self) -> tuple[str | None, int | None]:
        model = await self.get_meta(META_EMBED_MODEL)
        dim_raw = await self.get_meta(META_EMBED_DIM)
        return model, int(dim_raw) if dim_raw else None

    async def store_embedding(
        self,
        document_id: int,
        *,
        model: str,
        dim: int,
        vector: Sequence[float],
    ) -> None:
        """Store one vector (little-endian float32 BLOB) for a document.

        The first embedding pins ``embed_model``/``embed_dim`` in
        ``uw_meta`` (semi-permanent, D-3); later writes with a different
        model or dimension are rejected with a clear error — changing the
        embedding space goes through :meth:`reset_vectors`.
        """
        if len(vector) != dim:
            raise UltraStoreError(
                f"vector has {len(vector)} components but dim={dim} was declared"
            )
        pinned_model, pinned_dim = await self._pinned_space()
        if pinned_model is None or pinned_dim is None:
            await self.set_meta(META_EMBED_MODEL, model)
            await self.set_meta(META_EMBED_DIM, str(dim))
        elif pinned_model != model or pinned_dim != dim:
            raise UltraStoreError(
                "embedding space mismatch: the store is pinned to "
                f"model={pinned_model!r} dim={pinned_dim} but got "
                f"model={model!r} dim={dim}. Changing the embedding model "
                "requires reset_vectors() (re-embeds the corpus)."
            )
        vec_ok, _ = await self._ensure_vec(dim)
        blob = pack_vector(vector)
        now = _iso_utc()
        async with self._txn() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO uw_embeddings"
                " (document_id, model, dim, vector, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (document_id, model, dim, blob, now),
            )
            if vec_ok:
                await conn.execute(
                    "INSERT OR REPLACE INTO uw_vec (rowid, embedding) VALUES (?, ?)",
                    (document_id, blob),
                )

    async def _ensure_vec(self, dim: int | None) -> tuple[bool, str]:
        """Lazily derive the sqlite-vec ``vec0`` index from ``uw_embeddings``.

        Returns ``(usable, honest_reason_if_not)``. The probe result is
        cached per instance; a host that gains the extension later gets the
        index (and a backfill of every stored vector) on the next store
        instance without re-embedding anything.
        """
        if dim is None:
            _, dim = await self._pinned_space()
        if dim is None:
            return (
                False,
                "no embedding has been stored yet — the vector index is "
                "created with the first embedding",
            )
        if self._vec_state is not None and self._vec_dim == dim:
            return self._vec_state
        conn = await self._ensure_open()
        if not self._vec_ext_loaded:
            try:
                vec_mod = _import_sqlite_vec()
            except ImportError as exc:
                self._vec_state = (
                    False,
                    "semantic vector search is disabled: the sqlite-vec "
                    f"extension is not installed ({exc}). Keyword search "
                    "keeps working; install the 'sqlite-vec' package to "
                    "enable vectors — stored embeddings are kept and will "
                    "be indexed automatically.",
                )
                self._vec_dim = dim
                return self._vec_state
            try:
                await conn.enable_load_extension(True)
                await conn.load_extension(vec_mod.loadable_path())
                await conn.enable_load_extension(False)
            except Exception as exc:  # pragma: no cover — build-specific
                self._vec_state = (
                    False,
                    "semantic vector search is disabled: this Python's "
                    f"SQLite cannot load the sqlite-vec extension ({exc}). "
                    "Keyword search keeps working.",
                )
                self._vec_dim = dim
                return self._vec_state
            self._vec_ext_loaded = True
        async with self._lock:
            # Recreate the index when the pinned dimension changed.
            existing = await self._fetchone(
                conn,
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'uw_vec'",
            )
            if existing is not None:
                match = re.search(r"float\[(\d+)\]", existing["sql"] or "")
                if match and int(match.group(1)) != dim:
                    await conn.execute("DROP TABLE uw_vec")
                    existing = None
            if existing is None:
                await conn.execute(
                    "CREATE VIRTUAL TABLE uw_vec USING vec0"
                    f"(embedding float[{int(dim)}] distance_metric=cosine)"
                )
            # Sweep rows a no-extension session could not delete, then
            # backfill vectors the index does not hold yet.
            await conn.execute(
                "DELETE FROM uw_vec WHERE rowid NOT IN"
                " (SELECT document_id FROM uw_embeddings)"
            )
            missing = await self._fetchall(
                conn,
                "SELECT document_id, vector FROM uw_embeddings"
                " WHERE document_id NOT IN (SELECT rowid FROM uw_vec)",
            )
            for row in missing:
                await conn.execute(
                    "INSERT OR REPLACE INTO uw_vec (rowid, embedding) VALUES (?, ?)",
                    (row["document_id"], row["vector"]),
                )
        self._vec_dim = dim
        self._vec_state = (True, "")
        return self._vec_state

    async def vector_status(self) -> tuple[bool, str]:
        """Honest capability report for the vector leg."""
        return await self._ensure_vec(None)

    async def reset_vectors(self) -> None:
        """The embedding-model-change path: wipe the vector index and every
        stored embedding, clear the ``embed_model``/``embed_dim`` pin, and
        reset ``embedded``/``distilled`` items to ``keyword_indexed`` so the
        pipeline re-embeds (re-distillation rides the distill cache).
        Derived documents keep their text; only vectors are discarded.
        """
        vec_droppable = self._vec_ext_loaded  # vec0 DDL needs the extension
        async with self._txn() as conn:
            if vec_droppable:
                await conn.execute("DROP TABLE IF EXISTS uw_vec")
            await conn.execute("DELETE FROM uw_embeddings")
            await conn.execute(
                "DELETE FROM uw_meta WHERE key IN (?, ?)",
                (META_EMBED_MODEL, META_EMBED_DIM),
            )
            await conn.execute(
                "UPDATE uw_items SET state = ?, updated_at = ? WHERE state IN (?, ?)",
                (
                    ItemState.KEYWORD_INDEXED.value,
                    _iso_utc(),
                    ItemState.EMBEDDED.value,
                    ItemState.DISTILLED.value,
                ),
            )
        self._vec_state = None
        self._vec_dim = None

    # -- search legs (SQL only, fusion lives in the read path) --------------

    async def keyword_search(
        self, query: str, k: int = 10, *, area_id: str | None = None
    ) -> list[SearchResult]:
        """FTS5 keyword leg over live items; bm25 normalized to [0, 1]."""
        match_expr = _fts_match_expr(query)
        if not match_expr:
            return []
        conn = await self._ensure_open()
        sql = (
            "SELECT uw_fts.item_id AS item_id, i.source_id AS source_id,"
            " i.title AS title,"
            " snippet(uw_fts, 2, '', '', '…', 32) AS snip,"
            " i.permalink AS permalink, i.timestamp_utc AS timestamp_utc,"
            " bm25(uw_fts, 0.0, 3.0, 1.0) AS raw_score"
            " FROM uw_fts JOIN uw_items i ON i.id = uw_fts.item_id"
            " WHERE uw_fts MATCH ? AND i.deleted_at IS NULL"
        )
        params: list[Any] = [match_expr]
        if area_id is not None:
            sql += (
                " AND EXISTS (SELECT 1 FROM json_each(i.areas_json)"
                " WHERE json_each.value = ?)"
            )
            params.append(area_id)
        sql += " ORDER BY raw_score LIMIT ?"
        params.append(int(k))
        rows = await self._fetchall(conn, sql, params)
        return [
            SearchResult(
                item_id=int(row["item_id"]),
                source_id=row["source_id"],
                title=row["title"],
                snippet=row["snip"] or "",
                permalink=row["permalink"],
                timestamp_utc=row["timestamp_utc"],
                score=round(_normalize_bm25(row["raw_score"]), 4),
                matched_by=("keyword",),
            )
            for row in rows
        ]

    async def vector_search(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        *,
        area_id: str | None = None,
    ) -> tuple[list[SearchResult], str]:
        """ANN leg via the derived ``uw_vec`` index.

        Returns ``(results, reason)`` — ``reason`` is an empty string on the
        healthy path and an honest English explanation whenever the vector
        leg is degraded (extension unavailable, nothing embedded yet, or a
        query-vector/pin mismatch).
        """
        ok, reason = await self._ensure_vec(None)
        if not ok:
            return [], reason
        assert self._vec_dim is not None
        if len(query_vector) != self._vec_dim:
            return [], (
                f"query vector has {len(query_vector)} components but the "
                f"store's embedding space is pinned to dim={self._vec_dim}"
            )
        conn = await self._ensure_open()
        fetch_k = min(max(int(k) * 4, int(k) + 8), 200)
        knn_rows = await self._fetchall(
            conn,
            "SELECT rowid, distance FROM uw_vec WHERE embedding MATCH ? AND k = ?",
            (pack_vector(query_vector), fetch_k),
        )
        if not knn_rows:
            return [], ""
        distance_by_doc = {int(row["rowid"]): float(row["distance"]) for row in knn_rows}
        doc_ids = list(distance_by_doc)
        joined: list[tuple[float, aiosqlite.Row]] = []
        for chunk in _chunks(doc_ids):
            sql = (
                "SELECT d.id AS doc_id, d.text_norm AS text_norm,"  # noqa: S608 — the interpolation below is placeholder marks only
                " i.id AS item_id, i.source_id AS source_id, i.title AS title,"
                " i.permalink AS permalink, i.timestamp_utc AS timestamp_utc"
                " FROM uw_documents d JOIN uw_items i ON i.id = d.item_id"
                f" WHERE d.id IN ({_placeholders(len(chunk))})"
                " AND i.deleted_at IS NULL"
            )
            params = list(chunk)
            if area_id is not None:
                sql += (
                    " AND EXISTS (SELECT 1 FROM json_each(i.areas_json)"
                    " WHERE json_each.value = ?)"
                )
                params.append(area_id)
            for row in await self._fetchall(conn, sql, params):
                joined.append((distance_by_doc[int(row["doc_id"])], row))
        joined.sort(key=lambda pair: pair[0])
        results: list[SearchResult] = []
        seen_items: set[int] = set()
        for distance, row in joined:
            item_id = int(row["item_id"])
            if item_id in seen_items:
                continue  # dedupe multiple documents of one item to the best
            seen_items.add(item_id)
            results.append(
                SearchResult(
                    item_id=item_id,
                    source_id=row["source_id"],
                    title=row["title"],
                    snippet=_snippet_of(row["text_norm"]),
                    permalink=row["permalink"],
                    timestamp_utc=row["timestamp_utc"],
                    score=round(_distance_score(distance), 4),
                    matched_by=("vector",),
                )
            )
            if len(results) >= int(k):
                break
        return results, ""

    # -- ranking signals -----------------------------------------------------

    async def live_item_count(self) -> int:
        """Live (non-deleted) item count — the ``N`` of the IDF formula."""
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT count(*) AS n FROM uw_items WHERE deleted_at IS NULL", ()
        )
        return int(row["n"]) if row else 0

    async def term_document_frequency(self, terms: Sequence[str]) -> dict[str, int]:
        """In how many live items does each term occur? (the ``df`` of IDF)

        One indexed FTS count per distinct term — queries carry a handful of
        terms, so this stays cheap. Unindexable tokens report 0, which the
        caller reads as "maximally rare" only after the count is compared
        against the corpus size.
        """
        conn = await self._ensure_open()
        frequencies: dict[str, int] = {}
        for term in dict.fromkeys(terms):
            match_expr = _fts_match_expr(term)
            if not match_expr:
                frequencies[term] = 0
                continue
            row = await self._fetchone(
                conn,
                "SELECT count(*) AS n FROM uw_fts"
                " JOIN uw_items i ON i.id = uw_fts.item_id"
                " WHERE uw_fts MATCH ? AND i.deleted_at IS NULL",
                (match_expr,),
            )
            frequencies[term] = int(row["n"]) if row else 0
        return frequencies

    async def neighbors_for(self, item_id: int, *, limit: int = 2) -> list[str]:
        """Surrounding evidence for one winning item (context expansion).

        Conversation-shaped sources (a ``thread_key``) return the items
        immediately before and after inside that thread; file-shaped sources
        (no thread) fall back to the item's other stored document rendition,
        so a hit that matched a short fragment still shows its fuller text.
        Returns snippets, best-effort and possibly empty.
        """
        if limit <= 0:
            return []
        conn = await self._ensure_open()
        anchor = await self._fetchone(
            conn,
            "SELECT thread_key, timestamp_utc FROM uw_items WHERE id = ?",
            (int(item_id),),
        )
        if anchor is None:
            return []
        thread_key = str(anchor["thread_key"] or "")
        stamp = str(anchor["timestamp_utc"] or "")
        out: list[str] = []
        if thread_key:
            # ISO-8601 UTC stamps sort lexicographically — no date function,
            # identical behaviour on SQLite and Postgres.
            before = await self._fetchall(
                conn,
                "SELECT title, body_raw FROM uw_items"
                " WHERE thread_key = ? AND id <> ? AND deleted_at IS NULL"
                "   AND timestamp_utc <= ?"
                " ORDER BY timestamp_utc DESC LIMIT ?",
                (thread_key, int(item_id), stamp, _neighbors_per_side(limit)),
            )
            after = await self._fetchall(
                conn,
                "SELECT title, body_raw FROM uw_items"
                " WHERE thread_key = ? AND id <> ? AND deleted_at IS NULL"
                "   AND timestamp_utc > ?"
                " ORDER BY timestamp_utc ASC LIMIT ?",
                (thread_key, int(item_id), stamp, _neighbors_per_side(limit)),
            )
            out = _neighbor_snippets(reversed(list(before)), after)
        if not out:
            docs = await self._fetchall(
                conn,
                "SELECT text_norm FROM uw_documents WHERE item_id = ?"
                " ORDER BY length(text_norm) DESC LIMIT ?",
                (int(item_id), int(limit)),
            )
            out = [_snippet_of(row["text_norm"] or "") for row in docs]
        return [snippet for snippet in out if snippet][:limit]

    # -- sync state ----------------------------------------------------------

    async def get_sync_state(self, source_id: str) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT * FROM uw_sync_state WHERE source_id = ?", (source_id,)
        )
        if row is None:
            return None
        result = dict(row)
        result.pop("source_id", None)
        return result

    async def set_sync_state(
        self,
        source_id: str,
        *,
        cursor: str | None = _UNSET,
        backfill_checkpoint: str | None = _UNSET,
        backfill_complete_at: str | None = _UNSET,
        last_success_at: str | None = _UNSET,
    ) -> None:
        """Partial upsert of the per-source cursor/checkpoint bookkeeping."""
        fields = {
            "cursor": cursor,
            "backfill_checkpoint": backfill_checkpoint,
            "backfill_complete_at": backfill_complete_at,
            "last_success_at": last_success_at,
        }
        updates = {name: value for name, value in fields.items() if value is not _UNSET}
        async with self._txn() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO uw_sync_state (source_id) VALUES (?)",
                (source_id,),
            )
            if updates:
                sets = ", ".join(f'"{name}" = ?' for name in updates)
                await conn.execute(
                    f"UPDATE uw_sync_state SET {sets} WHERE source_id = ?",  # noqa: S608 — column names are code-owned literals
                    [*updates.values(), source_id],
                )

    # -- areas ---------------------------------------------------------------

    async def upsert_area(
        self, area_id: str, name: str, *, is_default: bool = False
    ) -> None:
        """Create or rename an area; ``is_default=True`` claims the single
        default slot (clearing it from every other area)."""
        now = _iso_utc()
        async with self._txn() as conn:
            if is_default:
                await conn.execute("UPDATE uw_areas SET is_default = 0")
            await conn.execute(
                "INSERT INTO uw_areas (id, name, is_default, created_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET name = excluded.name,"
                " is_default = excluded.is_default",
                (area_id, name, 1 if is_default else 0, now),
            )

    async def list_areas(self) -> list[dict[str, Any]]:
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn, "SELECT * FROM uw_areas ORDER BY is_default DESC, id"
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "is_default": bool(row["is_default"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def delete_area(self, area_id: str) -> None:
        conn = await self._ensure_open()
        await conn.execute("DELETE FROM uw_areas WHERE id = ?", (area_id,))

    async def ensure_default_area(
        self, area_id: str = "default", name: str = "Default"
    ) -> str:
        """Seed the default area when none exists; returns the default id."""
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT id FROM uw_areas WHERE is_default = 1 LIMIT 1"
        )
        if row is not None:
            return str(row["id"])
        await self.upsert_area(area_id, name, is_default=True)
        return area_id

    # -- distillation cache --------------------------------------------------

    async def distill_cache_get(
        self, content_hash: str, prompt_version: int, model: str
    ) -> str | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn,
            "SELECT result_json FROM uw_distill_cache"
            " WHERE content_hash = ? AND prompt_version = ? AND model = ?",
            (content_hash, int(prompt_version), model),
        )
        return None if row is None else str(row["result_json"])

    async def distill_cache_put(
        self, content_hash: str, prompt_version: int, model: str, result_json: str
    ) -> None:
        conn = await self._ensure_open()
        await conn.execute(
            "INSERT OR REPLACE INTO uw_distill_cache"
            " (content_hash, prompt_version, model, result_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (content_hash, int(prompt_version), model, result_json, _iso_utc()),
        )

    # -- meta ----------------------------------------------------------------

    async def get_meta(self, key: str) -> str | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT value FROM uw_meta WHERE key = ?", (key,)
        )
        return None if row is None else str(row["value"])

    async def set_meta(self, key: str, value: str) -> None:
        conn = await self._ensure_open()
        await conn.execute(
            "INSERT OR REPLACE INTO uw_meta (key, value) VALUES (?, ?)",
            (key, value),
        )


# ---------------------------------------------------------------------------
# Postgres variant (same public interface)
# ---------------------------------------------------------------------------


class PostgresStore:
    """Postgres backend behind the same public surface as :class:`UltraStore`.

    - The keyword leg is a generated ``tsvector`` column with a GIN index,
      so :meth:`mark_stage_done` accepts and ignores the ``fts_*`` arguments
      (the index follows ``title``/``body_raw`` automatically).
    - The vector leg is pgvector: ``CREATE EXTENSION IF NOT EXISTS vector``
      is attempted lazily on first vector use and degrades honestly when the
      server lacks the extension or the role lacks the privilege.
    - The caller passes the RESOLVED connection string (it is a credential —
      stored under the ``ultrawiki_db_url`` secret slot, never in config).

    SQLite is the reference backend; this class mirrors its semantics.
    """

    def __init__(self, conn_str: str) -> None:
        self._conn_str = conn_str
        self._conn: Any = None
        self._lock = asyncio.Lock()
        self._vec_state: tuple[bool, str] | None = None
        self._vec_dim: int | None = None

    # -- DDL -----------------------------------------------------------------

    @classmethod
    def ddl_statements(cls) -> list[str]:
        """Idempotent DDL derived from the logical schema (schema.sql).

        CHECK value lists are DERIVED from the canonical enums in
        ``jarvis/ultrawiki/types.py`` — never retyped (five-layer rule).
        """
        states = ", ".join(f"'{state.value}'" for state in ItemState)
        consents = ", ".join(f"'{consent.value}'" for consent in ConsentState)
        doc_types = ", ".join(f"'{doc.value}'" for doc in DocType)
        return [
            "CREATE TABLE IF NOT EXISTS uw_meta ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS uw_areas ("
            " id TEXT PRIMARY KEY, name TEXT NOT NULL,"
            " is_default BOOLEAN NOT NULL DEFAULT FALSE,"
            " created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS uw_sources ("
            " id TEXT PRIMARY KEY, connector TEXT NOT NULL, label TEXT NOT NULL,"
            " config_json TEXT NOT NULL DEFAULT '{}',"
            " areas_json TEXT NOT NULL DEFAULT '[]',"
            " consent TEXT NOT NULL DEFAULT 'pending'"
            f" CHECK (consent IN ({consents})),"
            " enabled BOOLEAN NOT NULL DEFAULT TRUE,"
            " created_at TEXT NOT NULL, last_sync_at TEXT, last_error TEXT)",
            "CREATE TABLE IF NOT EXISTS uw_sync_state ("
            " source_id TEXT PRIMARY KEY"
            "  REFERENCES uw_sources(id) ON DELETE CASCADE,"
            " cursor TEXT, backfill_checkpoint TEXT,"
            " backfill_complete_at TEXT, last_success_at TEXT)",
            "CREATE TABLE IF NOT EXISTS uw_items ("
            " id BIGSERIAL PRIMARY KEY,"
            " source_id TEXT NOT NULL REFERENCES uw_sources(id) ON DELETE CASCADE,"
            " external_id TEXT NOT NULL,"
            " thread_key TEXT NOT NULL DEFAULT '',"
            " author_raw TEXT NOT NULL DEFAULT '',"
            " title TEXT NOT NULL DEFAULT '',"
            " body_raw TEXT NOT NULL, permalink TEXT NOT NULL,"
            " timestamp_utc TEXT NOT NULL,"
            " areas_json TEXT NOT NULL DEFAULT '[]',"
            " content_hash TEXT NOT NULL,"
            " state TEXT NOT NULL DEFAULT 'captured'"
            f" CHECK (state IN ({states})),"
            " attempt_count INTEGER NOT NULL DEFAULT 0,"
            " next_retry_at TEXT, last_error TEXT, deleted_at TEXT,"
            " created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
            " search_tsv tsvector GENERATED ALWAYS AS"
            "  (to_tsvector('simple',"
            "   coalesce(title, '') || ' ' || coalesce(body_raw, ''))) STORED,"
            " UNIQUE (source_id, external_id))",
            "CREATE INDEX IF NOT EXISTS idx_uw_items_state"
            " ON uw_items(state, next_retry_at)",
            "CREATE INDEX IF NOT EXISTS idx_uw_items_source"
            " ON uw_items(source_id, state)",
            "CREATE INDEX IF NOT EXISTS idx_uw_items_time"
            " ON uw_items(timestamp_utc)",
            "CREATE INDEX IF NOT EXISTS idx_uw_items_tsv"
            " ON uw_items USING GIN (search_tsv)",
            "CREATE TABLE IF NOT EXISTS uw_documents ("
            " id BIGSERIAL PRIMARY KEY,"
            " item_id BIGINT NOT NULL REFERENCES uw_items(id) ON DELETE CASCADE,"
            f" doc_type TEXT NOT NULL CHECK (doc_type IN ({doc_types})),"
            " text_norm TEXT NOT NULL, distill_json TEXT,"
            " distill_version INTEGER NOT NULL DEFAULT 0,"
            " content_hash TEXT NOT NULL, created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_uw_documents_item"
            " ON uw_documents(item_id)",
            "CREATE TABLE IF NOT EXISTS uw_embeddings ("
            " document_id BIGINT PRIMARY KEY"
            "  REFERENCES uw_documents(id) ON DELETE CASCADE,"
            " model TEXT NOT NULL, dim INTEGER NOT NULL,"
            " vector BYTEA NOT NULL, created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS uw_distill_cache ("
            " content_hash TEXT NOT NULL, prompt_version INTEGER NOT NULL,"
            " model TEXT NOT NULL, result_json TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " PRIMARY KEY (content_hash, prompt_version, model))",
        ]

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    async def connect_test(cls, conn_str: str) -> tuple[bool, str]:
        """Probe a connection string: ``(ok, honest_message)``, never raises."""
        try:
            psycopg = _import_psycopg()
        except ImportError as exc:
            return False, str(exc)
        try:
            conn = await psycopg.AsyncConnection.connect(
                conn_str, connect_timeout=PG_CONNECT_TIMEOUT_S
            )
        except Exception as exc:
            # psycopg echoes the DSN — password included — in several failure
            # modes, and this message is shown in the UI.
            return False, f"Connection failed: {sanitize_conn_error(exc, conn_str)}"
        try:
            cur = await conn.execute("SELECT version()")
            version_row = await cur.fetchone()
            cur = await conn.execute(
                "SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'"
            )
            vec_row = await cur.fetchone()
            vec_note = (
                "pgvector is available"
                if vec_row and int(vec_row[0]) > 0
                else "pgvector is NOT available — vector search will be "
                "disabled (keyword search still works)"
            )
            server = version_row[0] if version_row else "PostgreSQL"
            return True, f"Connected: {server}; {vec_note}"
        except Exception as exc:
            return False, (
                "Connected, but the server probe failed: "
                f"{sanitize_conn_error(exc, conn_str)}"
            )
        finally:
            await conn.close()

    async def open(self) -> None:
        async with self._lock:
            if self._conn is not None:
                return
            psycopg = _import_psycopg()
            # A bounded connect: this runs on the app's startup path, so an
            # unreachable host must fail fast into the SQLite fallback rather
            # than hang the boot (psycopg would otherwise wait on the OS).
            conn = await psycopg.AsyncConnection.connect(
                self._conn_str,
                autocommit=True,
                row_factory=psycopg.rows.dict_row,
                connect_timeout=PG_CONNECT_TIMEOUT_S,
            )
            async with conn.transaction():
                for statement in self.ddl_statements():
                    await conn.execute(statement)
            self._conn = conn

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
            self._vec_state = None
            self._vec_dim = None

    async def _ensure_open(self) -> Any:
        if self._conn is None:
            await self.open()
        return self._conn

    @asynccontextmanager
    async def _txn(self) -> AsyncIterator[Any]:
        conn = await self._ensure_open()
        async with self._lock, conn.transaction():
            yield conn

    @staticmethod
    async def _fetchall(conn: Any, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        cur = await conn.execute(sql, params)
        return list(await cur.fetchall())

    @staticmethod
    async def _fetchone(
        conn: Any, sql: str, params: Sequence[Any] = ()
    ) -> dict | None:
        cur = await conn.execute(sql, params)
        return await cur.fetchone()

    # -- sources & consent ---------------------------------------------------

    async def upsert_source(
        self,
        source_id: str,
        *,
        connector: str,
        label: str,
        config: dict[str, Any] | None = None,
        areas: list[str] | None = None,
    ) -> None:
        now = _iso_utc()
        async with self._txn() as conn:
            row = await self._fetchone(
                conn, "SELECT id FROM uw_sources WHERE id = %s", (source_id,)
            )
            if row is None:
                await conn.execute(
                    "INSERT INTO uw_sources"
                    " (id, connector, label, config_json, areas_json, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        source_id,
                        connector,
                        label,
                        json.dumps(config or {}),
                        json.dumps(areas or []),
                        now,
                    ),
                )
            else:
                await conn.execute(
                    "UPDATE uw_sources SET connector = %s, label = %s,"
                    " config_json = COALESCE(%s, config_json),"
                    " areas_json = COALESCE(%s, areas_json) WHERE id = %s",
                    (
                        connector,
                        label,
                        None if config is None else json.dumps(config),
                        None if areas is None else json.dumps(areas),
                        source_id,
                    ),
                )

    @staticmethod
    def _source_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "connector": row["connector"],
            "label": row["label"],
            "config": json.loads(row["config_json"] or "{}"),
            "areas": json.loads(row["areas_json"] or "[]"),
            "consent": row["consent"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "last_sync_at": row["last_sync_at"],
            "last_error": row["last_error"],
        }

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT * FROM uw_sources WHERE id = %s", (source_id,)
        )
        return None if row is None else self._source_row_to_dict(row)

    async def list_sources(self) -> list[dict[str, Any]]:
        conn = await self._ensure_open()
        rows = await self._fetchall(conn, "SELECT * FROM uw_sources ORDER BY id")
        count_rows = await self._fetchall(
            conn,
            "SELECT source_id, state, COUNT(*) AS n FROM uw_items"
            " WHERE deleted_at IS NULL GROUP BY source_id, state",
        )
        per_source: dict[str, list[tuple[str, int]]] = {}
        for crow in count_rows:
            per_source.setdefault(crow["source_id"], []).append(
                (crow["state"], crow["n"])
            )
        sync_rows = await self._fetchall(conn, "SELECT * FROM uw_sync_state")
        sync_by_id = {srow["source_id"]: dict(srow) for srow in sync_rows}
        result = []
        for row in rows:
            entry = self._source_row_to_dict(row)
            entry["counts"] = _counts_from_pairs(per_source.get(row["id"], []))
            sync = sync_by_id.get(row["id"])
            if sync is not None:
                sync.pop("source_id", None)
            entry["sync_state"] = sync
            result.append(entry)
        return result

    async def set_consent(self, source_id: str, consent: ConsentState | str) -> None:
        value = ConsentState(consent).value
        conn = await self._ensure_open()
        await conn.execute(
            "UPDATE uw_sources SET consent = %s WHERE id = %s", (value, source_id)
        )

    async def set_enabled(self, source_id: str, enabled: bool) -> None:
        conn = await self._ensure_open()
        await conn.execute(
            "UPDATE uw_sources SET enabled = %s WHERE id = %s",
            (bool(enabled), source_id),
        )

    async def set_source_status(
        self,
        source_id: str,
        *,
        last_sync_at: str | None = _UNSET,
        last_error: str | None = _UNSET,
    ) -> None:
        conn = await self._ensure_open()
        if last_sync_at is not _UNSET:
            await conn.execute(
                "UPDATE uw_sources SET last_sync_at = %s WHERE id = %s",
                (last_sync_at, source_id),
            )
        if last_error is not _UNSET:
            await conn.execute(
                "UPDATE uw_sources SET last_error = %s WHERE id = %s",
                (last_error, source_id),
            )

    async def delete_source(self, source_id: str, *, purge: bool) -> None:
        if not purge:
            conn = await self._ensure_open()
            await conn.execute(
                "UPDATE uw_sources SET consent = %s, enabled = FALSE WHERE id = %s",
                (ConsentState.REVOKED.value, source_id),
            )
            return
        async with self._txn() as conn:
            if self._vec_state is not None and self._vec_state[0]:
                await conn.execute(
                    "DELETE FROM uw_vec WHERE document_id IN"
                    " (SELECT d.id FROM uw_documents d"
                    "  JOIN uw_items i ON i.id = d.item_id"
                    "  WHERE i.source_id = %s)",
                    (source_id,),
                )
            await conn.execute(
                "DELETE FROM uw_sources WHERE id = %s", (source_id,)
            )  # items/documents/embeddings cascade

    # -- items ---------------------------------------------------------------

    async def upsert_items(
        self, source_id: str, items: Sequence[RawItem]
    ) -> UpsertCounts:
        source = await self.get_source(source_id)
        if source is None:
            raise UltraStoreError(
                f"unknown source {source_id!r} — call upsert_source() first"
            )
        areas_json = json.dumps(source["areas"])
        now = _iso_utc()
        new = changed = unchanged = tombstoned = 0
        async with self._txn() as conn:
            for item in items:
                row = await self._fetchone(
                    conn,
                    "SELECT id, content_hash, deleted_at FROM uw_items"
                    " WHERE source_id = %s AND external_id = %s",
                    (source_id, item.external_id),
                )
                if item.deleted:
                    if row is not None and row["deleted_at"] is None:
                        await self._purge_derived(conn, [row["id"]])
                        await conn.execute(
                            "UPDATE uw_items SET deleted_at = %s, updated_at = %s"
                            " WHERE id = %s",
                            (now, now, row["id"]),
                        )
                        tombstoned += 1
                    continue
                identity = _content_identity(item)
                if row is None:
                    await conn.execute(
                        "INSERT INTO uw_items"
                        " (source_id, external_id, thread_key, author_raw, title,"
                        "  body_raw, permalink, timestamp_utc, areas_json,"
                        "  content_hash, state, created_at, updated_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                        " %s, %s)",
                        (
                            source_id,
                            item.external_id,
                            item.thread_key,
                            item.author_raw,
                            item.title,
                            item.body,
                            item.permalink,
                            item.timestamp_utc,
                            areas_json,
                            identity,
                            ItemState.CAPTURED.value,
                            now,
                            now,
                        ),
                    )
                    new += 1
                elif row["content_hash"] == identity and row["deleted_at"] is None:
                    unchanged += 1
                else:
                    await self._purge_derived(conn, [row["id"]])
                    await conn.execute(
                        "UPDATE uw_items SET thread_key = %s, author_raw = %s,"
                        " title = %s, body_raw = %s, permalink = %s,"
                        " timestamp_utc = %s, areas_json = %s, content_hash = %s,"
                        " state = %s, attempt_count = 0, next_retry_at = NULL,"
                        " last_error = NULL, deleted_at = NULL, updated_at = %s"
                        " WHERE id = %s",
                        (
                            item.thread_key,
                            item.author_raw,
                            item.title,
                            item.body,
                            item.permalink,
                            item.timestamp_utc,
                            areas_json,
                            identity,
                            ItemState.CAPTURED.value,
                            now,
                            row["id"],
                        ),
                    )
                    changed += 1
        return UpsertCounts(
            new=new, changed=changed, unchanged=unchanged, tombstoned=tombstoned
        )

    async def _purge_derived(self, conn: Any, item_ids: Sequence[int]) -> None:
        """Postgres twin of the SQLite purge: the tsvector column follows the
        row automatically, documents/embeddings cascade; only pgvector rows
        need an explicit delete when the index is live."""
        if not item_ids:
            return
        ids = list(item_ids)
        if self._vec_state is not None and self._vec_state[0]:
            await conn.execute(
                "DELETE FROM uw_vec WHERE document_id IN"
                " (SELECT id FROM uw_documents WHERE item_id = ANY(%s))",
                (ids,),
            )
        await conn.execute(
            "DELETE FROM uw_documents WHERE item_id = ANY(%s)", (ids,)
        )

    @staticmethod
    def _item_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "source_id": row["source_id"],
            "external_id": row["external_id"],
            "thread_key": row["thread_key"],
            "author_raw": row["author_raw"],
            "title": row["title"],
            "body_raw": row["body_raw"],
            "permalink": row["permalink"],
            "timestamp_utc": row["timestamp_utc"],
            "areas": json.loads(row["areas_json"] or "[]"),
            "content_hash": row["content_hash"],
            "state": row["state"],
            "attempt_count": row["attempt_count"],
            "next_retry_at": row["next_retry_at"],
            "last_error": row["last_error"],
            "deleted_at": row["deleted_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def get_item(self, item_id: int) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT * FROM uw_items WHERE id = %s", (item_id,)
        )
        return None if row is None else self._item_row_to_dict(row)

    async def get_item_by_external_id(
        self, source_id: str, external_id: str
    ) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn,
            "SELECT * FROM uw_items WHERE source_id = %s AND external_id = %s",
            (source_id, external_id),
        )
        return None if row is None else self._item_row_to_dict(row)

    async def claim_batch(
        self,
        target_state: ItemState | str,
        *,
        limit: int = 50,
        now: str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        predecessor = _predecessor_of(target_state)
        now_s = _iso_utc(_coerce_now(now))
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT * FROM uw_items"
            " WHERE state = %s AND deleted_at IS NULL"
            "   AND (next_retry_at IS NULL OR next_retry_at <= %s)"
            " ORDER BY timestamp_utc DESC, id DESC LIMIT %s",
            (predecessor.value, now_s, int(limit)),
        )
        return [self._item_row_to_dict(row) for row in rows]

    async def mark_stage_done(
        self,
        item_id: int,
        new_state: ItemState | str,
        *,
        fts_title: str | None = None,  # noqa: ARG002 — tsvector is generated
        fts_body: str | None = None,  # noqa: ARG002 — tsvector is generated
        expected_state: ItemState | str | None = None,
        expected_content_hash: str | None = None,
    ) -> bool:
        """Same contract as the SQLite twin — including the compare-and-set
        lost-claim guard; the ``fts_*`` arguments are accepted and ignored
        because the keyword leg is a generated column."""
        state = _coerce_state(new_state)
        if state not in STATE_ORDER or state is ItemState.CAPTURED:
            raise ValueError(
                f"mark_stage_done cannot set {state.value!r} — only forward "
                "stages after 'captured' are worker transitions"
            )
        sql = (
            "UPDATE uw_items SET state = %s, attempt_count = 0,"
            " next_retry_at = NULL, last_error = NULL, updated_at = %s"
            " WHERE id = %s"
        )
        params: list[Any] = [state.value, _iso_utc(), item_id]
        if expected_state is not None:
            sql += " AND state = %s"
            params.append(_coerce_state(expected_state).value)
        if expected_content_hash is not None:
            sql += " AND content_hash = %s"
            params.append(expected_content_hash)
        conn = await self._ensure_open()
        cur = await conn.execute(sql, params)
        # A driver that cannot report a row count (-1) is treated as a hit —
        # the guard may never invent a lost claim out of missing information.
        return int(getattr(cur, "rowcount", -1)) != 0

    async def mark_retry(
        self,
        item_id: int,
        error: str,
        *,
        now: str | datetime | None = None,
    ) -> None:
        moment = _coerce_now(now)
        async with self._txn() as conn:
            row = await self._fetchone(
                conn,
                "SELECT attempt_count FROM uw_items WHERE id = %s",
                (item_id,),
            )
            if row is None:
                return
            attempts_before = int(row["attempt_count"])
            new_count = attempts_before + 1
            if new_count >= MAX_ATTEMPTS:
                await conn.execute(
                    "UPDATE uw_items SET state = %s, attempt_count = %s,"
                    " next_retry_at = NULL, last_error = %s, updated_at = %s"
                    " WHERE id = %s",
                    (
                        ItemState.FAILED.value,
                        new_count,
                        error,
                        _iso_utc(moment),
                        item_id,
                    ),
                )
            else:
                retry_at = moment + timedelta(seconds=_retry_delay_s(attempts_before))
                await conn.execute(
                    "UPDATE uw_items SET attempt_count = %s, next_retry_at = %s,"
                    " last_error = %s, updated_at = %s WHERE id = %s",
                    (
                        new_count,
                        _iso_utc(retry_at),
                        error,
                        _iso_utc(moment),
                        item_id,
                    ),
                )

    async def mark_failed(self, item_id: int, error: str) -> None:
        conn = await self._ensure_open()
        await conn.execute(
            "UPDATE uw_items SET state = %s, next_retry_at = NULL,"
            " last_error = %s, updated_at = %s WHERE id = %s",
            (ItemState.FAILED.value, error, _iso_utc(), item_id),
        )

    async def requeue_failed(self, source_id: str | None = None) -> int:
        """Postgres twin of the SQLite requeue.

        One deliberate difference: the keyword leg here is a GENERATED
        ``tsvector`` column, so there is no separate index row to look for and
        no ``keyword_indexed`` evidence to find. An item without a stored
        embedding therefore restarts at ``captured`` — re-running the keyword
        transition costs a single cheap UPDATE and can never claim an item is
        searchable when it is not.
        """
        now = _iso_utc()
        moved = 0
        async with self._txn() as conn:
            sql = (
                "SELECT i.id AS id,"
                " EXISTS (SELECT 1 FROM uw_documents d"
                "  JOIN uw_embeddings e ON e.document_id = d.id"
                "  WHERE d.item_id = i.id) AS has_vector"
                " FROM uw_items i"
                " WHERE i.state = %s AND i.deleted_at IS NULL"
            )
            params: list[Any] = [ItemState.FAILED.value]
            if source_id is not None:
                sql += " AND i.source_id = %s"
                params.append(source_id)
            rows = await self._fetchall(conn, sql, params)
            for row in rows:
                target = (
                    ItemState.EMBEDDED
                    if bool(row["has_vector"])
                    else ItemState.CAPTURED
                )
                await conn.execute(
                    "UPDATE uw_items SET state = %s, attempt_count = 0,"
                    " next_retry_at = NULL, last_error = NULL, updated_at = %s"
                    " WHERE id = %s",
                    (target.value, now, row["id"]),
                )
                moved += 1
        return moved

    async def counts(self) -> PipelineCounts:
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT state, COUNT(*) AS n FROM uw_items"
            " WHERE deleted_at IS NULL GROUP BY state",
        )
        return _counts_from_pairs((row["state"], row["n"]) for row in rows)

    async def counts_for_source(self, source_id: str) -> PipelineCounts:
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT state, COUNT(*) AS n FROM uw_items"
            " WHERE deleted_at IS NULL AND source_id = %s GROUP BY state",
            (source_id,),
        )
        return _counts_from_pairs((row["state"], row["n"]) for row in rows)

    async def reconcile_deletes(
        self, source_id: str, yielded_external_ids: set[str]
    ) -> int:
        now = _iso_utc()
        async with self._txn() as conn:
            rows = await self._fetchall(
                conn,
                "SELECT id, external_id FROM uw_items"
                " WHERE source_id = %s AND deleted_at IS NULL",
                (source_id,),
            )
            doomed = [
                row["id"]
                for row in rows
                if row["external_id"] not in yielded_external_ids
            ]
            if doomed:
                await self._purge_derived(conn, doomed)
                await conn.execute(
                    "UPDATE uw_items SET deleted_at = %s, updated_at = %s"
                    " WHERE id = ANY(%s)",
                    (now, now, doomed),
                )
        return len(doomed)

    # -- documents & vectors -------------------------------------------------

    async def add_document(
        self,
        item_id: int,
        doc_type: DocType | str,
        text_norm: str,
        *,
        distill_json: str | None = None,
        distill_version: int = 0,
        content_hash: str = "",
    ) -> int:
        kind = DocType(doc_type).value
        now = _iso_utc()
        async with self._txn() as conn:
            if self._vec_state is not None and self._vec_state[0]:
                await conn.execute(
                    "DELETE FROM uw_vec WHERE document_id IN"
                    " (SELECT id FROM uw_documents"
                    "  WHERE item_id = %s AND doc_type = %s)",
                    (item_id, kind),
                )
            await conn.execute(
                "DELETE FROM uw_documents WHERE item_id = %s AND doc_type = %s",
                (item_id, kind),
            )
            row = await self._fetchone(
                conn,
                "INSERT INTO uw_documents (item_id, doc_type, text_norm,"
                " distill_json, distill_version, content_hash, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    item_id,
                    kind,
                    text_norm,
                    distill_json,
                    int(distill_version),
                    content_hash,
                    now,
                ),
            )
            assert row is not None
            return int(row["id"])

    async def _pinned_space(self) -> tuple[str | None, int | None]:
        model = await self.get_meta(META_EMBED_MODEL)
        dim_raw = await self.get_meta(META_EMBED_DIM)
        return model, int(dim_raw) if dim_raw else None

    async def store_embedding(
        self,
        document_id: int,
        *,
        model: str,
        dim: int,
        vector: Sequence[float],
    ) -> None:
        if len(vector) != dim:
            raise UltraStoreError(
                f"vector has {len(vector)} components but dim={dim} was declared"
            )
        pinned_model, pinned_dim = await self._pinned_space()
        if pinned_model is None or pinned_dim is None:
            await self.set_meta(META_EMBED_MODEL, model)
            await self.set_meta(META_EMBED_DIM, str(dim))
        elif pinned_model != model or pinned_dim != dim:
            raise UltraStoreError(
                "embedding space mismatch: the store is pinned to "
                f"model={pinned_model!r} dim={pinned_dim} but got "
                f"model={model!r} dim={dim}. Changing the embedding model "
                "requires reset_vectors() (re-embeds the corpus)."
            )
        vec_ok, _ = await self._ensure_vec(dim)
        now = _iso_utc()
        async with self._txn() as conn:
            await conn.execute(
                "INSERT INTO uw_embeddings"
                " (document_id, model, dim, vector, created_at)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (document_id) DO UPDATE SET model = %s,"
                " dim = %s, vector = %s, created_at = %s",
                (
                    document_id,
                    model,
                    dim,
                    pack_vector(vector),
                    now,
                    model,
                    dim,
                    pack_vector(vector),
                    now,
                ),
            )
            if vec_ok:
                literal = self._pgvector_literal(vector)
                await conn.execute(
                    "INSERT INTO uw_vec (document_id, embedding)"
                    " VALUES (%s, %s::vector)"
                    " ON CONFLICT (document_id) DO UPDATE"
                    " SET embedding = %s::vector",
                    (document_id, literal, literal),
                )

    @staticmethod
    def _pgvector_literal(vector: Sequence[float]) -> str:
        return "[" + ",".join(f"{component:.8g}" for component in vector) + "]"

    async def _ensure_vec(self, dim: int | None) -> tuple[bool, str]:
        """Lazily enable pgvector + derive the ``uw_vec`` table; degrades
        honestly when the server lacks the extension or the privilege."""
        if dim is None:
            _, dim = await self._pinned_space()
        if dim is None:
            return (
                False,
                "no embedding has been stored yet — the vector index is "
                "created with the first embedding",
            )
        if self._vec_state is not None and self._vec_dim == dim:
            return self._vec_state
        conn = await self._ensure_open()
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception as exc:
            self._vec_state = (
                False,
                "semantic vector search is disabled: the pgvector extension "
                f"is not available on this PostgreSQL server ({exc}). "
                "Keyword search keeps working; stored embeddings are kept "
                "and will be indexed once the extension is installed.",
            )
            self._vec_dim = dim
            return self._vec_state
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS uw_vec ("
            " document_id BIGINT PRIMARY KEY"
            "  REFERENCES uw_documents(id) ON DELETE CASCADE,"
            f" embedding vector({int(dim)}) NOT NULL)"
        )
        # Backfill vectors stored while the index was unavailable.
        missing = await self._fetchall(
            conn,
            "SELECT document_id, vector FROM uw_embeddings"
            " WHERE document_id NOT IN (SELECT document_id FROM uw_vec)",
        )
        for row in missing:
            literal = self._pgvector_literal(unpack_vector(bytes(row["vector"])))
            await conn.execute(
                "INSERT INTO uw_vec (document_id, embedding)"
                " VALUES (%s, %s::vector) ON CONFLICT (document_id) DO NOTHING",
                (row["document_id"], literal),
            )
        self._vec_dim = dim
        self._vec_state = (True, "")
        return self._vec_state

    async def vector_status(self) -> tuple[bool, str]:
        return await self._ensure_vec(None)

    async def reset_vectors(self) -> None:
        async with self._txn() as conn:
            await conn.execute("DROP TABLE IF EXISTS uw_vec")
            await conn.execute("DELETE FROM uw_embeddings")
            await conn.execute(
                "DELETE FROM uw_meta WHERE key IN (%s, %s)",
                (META_EMBED_MODEL, META_EMBED_DIM),
            )
            await conn.execute(
                "UPDATE uw_items SET state = %s, updated_at = %s"
                " WHERE state IN (%s, %s)",
                (
                    ItemState.KEYWORD_INDEXED.value,
                    _iso_utc(),
                    ItemState.EMBEDDED.value,
                    ItemState.DISTILLED.value,
                ),
            )
        self._vec_state = None
        self._vec_dim = None

    # -- search legs ---------------------------------------------------------

    async def keyword_search(
        self, query: str, k: int = 10, *, area_id: str | None = None
    ) -> list[SearchResult]:
        if not query or not query.strip():
            return []
        conn = await self._ensure_open()
        sql = (
            "SELECT i.id AS item_id, i.source_id, i.title,"
            " LEFT(i.body_raw, %s) AS snip, i.permalink, i.timestamp_utc,"
            " ts_rank(i.search_tsv, websearch_to_tsquery('simple', %s)) AS rank"
            " FROM uw_items i"
            " WHERE i.search_tsv @@ websearch_to_tsquery('simple', %s)"
            "   AND i.deleted_at IS NULL"
        )
        params: list[Any] = [_SNIPPET_CHARS, query, query]
        if area_id is not None:
            sql += " AND i.areas_json::jsonb @> to_jsonb(%s::text)"
            params.append(area_id)
        sql += " ORDER BY rank DESC LIMIT %s"
        params.append(int(k))
        rows = await self._fetchall(conn, sql, params)
        return [
            SearchResult(
                item_id=int(row["item_id"]),
                source_id=row["source_id"],
                title=row["title"],
                snippet=_snippet_of(row["snip"] or ""),
                permalink=row["permalink"],
                timestamp_utc=row["timestamp_utc"],
                score=round(
                    float(row["rank"]) / (1.0 + float(row["rank"])), 4
                ),
                matched_by=("keyword",),
            )
            for row in rows
        ]

    async def vector_search(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        *,
        area_id: str | None = None,
    ) -> tuple[list[SearchResult], str]:
        ok, reason = await self._ensure_vec(None)
        if not ok:
            return [], reason
        assert self._vec_dim is not None
        if len(query_vector) != self._vec_dim:
            return [], (
                f"query vector has {len(query_vector)} components but the "
                f"store's embedding space is pinned to dim={self._vec_dim}"
            )
        conn = await self._ensure_open()
        literal = self._pgvector_literal(query_vector)
        sql = (
            "SELECT d.item_id, i.source_id, i.title, d.text_norm,"
            " i.permalink, i.timestamp_utc,"
            " (v.embedding <=> %s::vector) AS distance"
            " FROM uw_vec v"
            " JOIN uw_documents d ON d.id = v.document_id"
            " JOIN uw_items i ON i.id = d.item_id"
            " WHERE i.deleted_at IS NULL"
        )
        params: list[Any] = [literal]
        if area_id is not None:
            sql += " AND i.areas_json::jsonb @> to_jsonb(%s::text)"
            params.append(area_id)
        sql += " ORDER BY distance LIMIT %s"
        params.append(min(max(int(k) * 4, int(k) + 8), 200))
        rows = await self._fetchall(conn, sql, params)
        results: list[SearchResult] = []
        seen_items: set[int] = set()
        for row in rows:
            item_id = int(row["item_id"])
            if item_id in seen_items:
                continue
            seen_items.add(item_id)
            results.append(
                SearchResult(
                    item_id=item_id,
                    source_id=row["source_id"],
                    title=row["title"],
                    snippet=_snippet_of(row["text_norm"]),
                    permalink=row["permalink"],
                    timestamp_utc=row["timestamp_utc"],
                    score=round(_distance_score(float(row["distance"])), 4),
                    matched_by=("vector",),
                )
            )
            if len(results) >= int(k):
                break
        return results, ""

    # -- ranking signals -----------------------------------------------------

    async def live_item_count(self) -> int:
        """Live (non-deleted) item count — the ``N`` of the IDF formula."""
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT count(*) AS n FROM uw_items WHERE deleted_at IS NULL", ()
        )
        return int(row["n"]) if row else 0

    async def term_document_frequency(self, terms: Sequence[str]) -> dict[str, int]:
        """In how many live items does each term occur? (the ``df`` of IDF)"""
        conn = await self._ensure_open()
        frequencies: dict[str, int] = {}
        for term in dict.fromkeys(terms):
            cleaned = " ".join(str(term).split())
            if not cleaned:
                frequencies[term] = 0
                continue
            row = await self._fetchone(
                conn,
                "SELECT count(*) AS n FROM uw_items"
                " WHERE search_tsv @@ websearch_to_tsquery('simple', %s)"
                "   AND deleted_at IS NULL",
                (cleaned,),
            )
            frequencies[term] = int(row["n"]) if row else 0
        return frequencies

    async def neighbors_for(self, item_id: int, *, limit: int = 2) -> list[str]:
        """Surrounding evidence for one winning item (context expansion)."""
        if limit <= 0:
            return []
        conn = await self._ensure_open()
        anchor = await self._fetchone(
            conn,
            "SELECT thread_key, timestamp_utc FROM uw_items WHERE id = %s",
            (int(item_id),),
        )
        if anchor is None:
            return []
        thread_key = str(anchor["thread_key"] or "")
        stamp = str(anchor["timestamp_utc"] or "")
        out: list[str] = []
        if thread_key:
            before = await self._fetchall(
                conn,
                "SELECT title, body_raw FROM uw_items"
                " WHERE thread_key = %s AND id <> %s AND deleted_at IS NULL"
                "   AND timestamp_utc <= %s"
                " ORDER BY timestamp_utc DESC LIMIT %s",
                (thread_key, int(item_id), stamp, _neighbors_per_side(limit)),
            )
            after = await self._fetchall(
                conn,
                "SELECT title, body_raw FROM uw_items"
                " WHERE thread_key = %s AND id <> %s AND deleted_at IS NULL"
                "   AND timestamp_utc > %s"
                " ORDER BY timestamp_utc ASC LIMIT %s",
                (thread_key, int(item_id), stamp, _neighbors_per_side(limit)),
            )
            out = _neighbor_snippets(reversed(list(before)), after)
        if not out:
            docs = await self._fetchall(
                conn,
                "SELECT text_norm FROM uw_documents WHERE item_id = %s"
                " ORDER BY length(text_norm) DESC LIMIT %s",
                (int(item_id), int(limit)),
            )
            out = [_snippet_of(row["text_norm"] or "") for row in docs]
        return [snippet for snippet in out if snippet][:limit]

    # -- sync state / areas / cache / meta ----------------------------------

    async def get_sync_state(self, source_id: str) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT * FROM uw_sync_state WHERE source_id = %s", (source_id,)
        )
        if row is None:
            return None
        result = dict(row)
        result.pop("source_id", None)
        return result

    async def set_sync_state(
        self,
        source_id: str,
        *,
        cursor: str | None = _UNSET,
        backfill_checkpoint: str | None = _UNSET,
        backfill_complete_at: str | None = _UNSET,
        last_success_at: str | None = _UNSET,
    ) -> None:
        fields = {
            "cursor": cursor,
            "backfill_checkpoint": backfill_checkpoint,
            "backfill_complete_at": backfill_complete_at,
            "last_success_at": last_success_at,
        }
        updates = {name: value for name, value in fields.items() if value is not _UNSET}
        async with self._txn() as conn:
            await conn.execute(
                "INSERT INTO uw_sync_state (source_id) VALUES (%s)"
                " ON CONFLICT (source_id) DO NOTHING",
                (source_id,),
            )
            for name, value in updates.items():
                await conn.execute(
                    f'UPDATE uw_sync_state SET "{name}" = %s WHERE source_id = %s',  # noqa: S608 — column name from a code-owned literal dict
                    (value, source_id),
                )

    async def upsert_area(
        self, area_id: str, name: str, *, is_default: bool = False
    ) -> None:
        now = _iso_utc()
        async with self._txn() as conn:
            if is_default:
                await conn.execute("UPDATE uw_areas SET is_default = FALSE")
            await conn.execute(
                "INSERT INTO uw_areas (id, name, is_default, created_at)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name,"
                " is_default = EXCLUDED.is_default",
                (area_id, name, bool(is_default), now),
            )

    async def list_areas(self) -> list[dict[str, Any]]:
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn, "SELECT * FROM uw_areas ORDER BY is_default DESC, id"
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "is_default": bool(row["is_default"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def delete_area(self, area_id: str) -> None:
        conn = await self._ensure_open()
        await conn.execute("DELETE FROM uw_areas WHERE id = %s", (area_id,))

    async def ensure_default_area(
        self, area_id: str = "default", name: str = "Default"
    ) -> str:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT id FROM uw_areas WHERE is_default = TRUE LIMIT 1"
        )
        if row is not None:
            return str(row["id"])
        await self.upsert_area(area_id, name, is_default=True)
        return area_id

    async def distill_cache_get(
        self, content_hash: str, prompt_version: int, model: str
    ) -> str | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn,
            "SELECT result_json FROM uw_distill_cache"
            " WHERE content_hash = %s AND prompt_version = %s AND model = %s",
            (content_hash, int(prompt_version), model),
        )
        return None if row is None else str(row["result_json"])

    async def distill_cache_put(
        self, content_hash: str, prompt_version: int, model: str, result_json: str
    ) -> None:
        conn = await self._ensure_open()
        await conn.execute(
            "INSERT INTO uw_distill_cache"
            " (content_hash, prompt_version, model, result_json, created_at)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (content_hash, prompt_version, model)"
            " DO UPDATE SET result_json = EXCLUDED.result_json,"
            " created_at = EXCLUDED.created_at",
            (content_hash, int(prompt_version), model, result_json, _iso_utc()),
        )

    async def get_meta(self, key: str) -> str | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT value FROM uw_meta WHERE key = %s", (key,)
        )
        return None if row is None else str(row["value"])

    async def set_meta(self, key: str, value: str) -> None:
        conn = await self._ensure_open()
        await conn.execute(
            "INSERT INTO uw_meta (key, value) VALUES (%s, %s)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )


__all__ = [
    "BACKOFF_BASE_S",
    "BACKOFF_CAP_S",
    "MAX_ATTEMPTS",
    "META_EMBED_DIM",
    "META_EMBED_MODEL",
    "PG_CONNECT_TIMEOUT_S",
    "PostgresStore",
    "UltraStore",
    "UltraStoreError",
    "UpsertCounts",
    "pack_vector",
    "resolve_ultrawiki_db_path",
    "sanitize_conn_error",
    "unpack_vector",
]
