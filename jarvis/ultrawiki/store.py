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
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiosqlite

from jarvis.ultrawiki.event_store import EventMixin
from jarvis.ultrawiki.events import (
    EVENT_KIND_VALUES,
    TIME_ANCHOR_VALUES,
    TIME_PRECISION_VALUES,
)
from jarvis.ultrawiki.identity import (
    MERGEABLE_TIERS,
    EntityKind,
    IdentifierKind,
    QueueStatus,
)
from jarvis.ultrawiki.identity_store import IdentityMixin
from jarvis.ultrawiki.lexicon_store import LexiconMixin
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

#: ``uw_meta`` keys pinning the ACTIVE embedding space — the one the derived
#: ``uw_vec`` index holds and every search answers from (D-3).
META_EMBED_MODEL = "embed_model"
META_EMBED_DIM = "embed_dim"

#: ``uw_meta`` keys naming the space currently being built in the background
#: after a model switch. Vectors accumulate under it while searches keep using
#: the active space; :meth:`UltraStore.promote_pending_space` swaps them once
#: every item the switch invalidated has been re-embedded. The dimension is
#: unknown until the provider answers, so ``PENDING_DIM`` appears with the
#: first vector.
META_PENDING_EMBED_MODEL = "pending_embed_model"
META_PENDING_EMBED_DIM = "pending_embed_dim"

#: How many items :meth:`UltraStore.begin_reembed` flagged when the rebuild
#: started — the denominator of an honest progress report. Counting the WORK
#: rather than the surviving old vectors is what makes the number monotonic:
#: re-embedding an item whose passage set changed DELETES its old documents
#: (and their vectors with them), so any measure defined over those rows reads
#: 0 % until the very end and then jumps.
META_REEMBED_TOTAL = "reembed_total"

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


class EmbeddingSpaceMismatch(UltraStoreError):
    """A vector was offered for a space this store neither serves nor builds.

    Its own type because the CALLER's correct response differs from every
    other store error: this is a CONFIGURATION fault, not a poisoned item.
    Charging it as a per-item retry (the generic handler) burns five attempts
    on innocent content and then dead-letters it, so a single mis-registered
    model switch quietly destroys the corpus item by item while the surface
    still reads "still filling up" (forensic 2026-07-28). Callers pause the
    stage instead and leave the backlog exactly where it is.
    """


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


#: Projection of :meth:`UltraStore.list_items` — the inventory view's row.
#: An item whose connector supplied no title is listed under its external id
#: rather than as a blank line the user cannot identify.
_ITEM_LIST_COLUMNS = (
    "id, source_id, state, permalink, timestamp_utc,"
    " COALESCE(NULLIF(title, ''), external_id) AS title,"
    " created_at AS ingested_at, updated_at"
)


def _metadata_json(item: RawItem) -> str:
    """A ``RawItem``'s metadata as storable JSON; ``"{}"`` for anything odd.

    Never raises: a connector may attach whatever it likes, and one
    unserialisable value must cost that item its metadata rather than the
    whole batch its transaction.
    """
    metadata = getattr(item, "metadata", None)
    if not isinstance(metadata, dict) or not metadata:
        return "{}"
    try:
        return json.dumps(metadata, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        log.debug("UltraStore: metadata of %s is not serialisable", item.external_id)
        return "{}"


def _parse_metadata(row: Any) -> dict[str, Any]:
    """One row's stored metadata. Empty for a row written before the column."""
    try:
        raw = row["metadata_json"]
    except (KeyError, IndexError, TypeError):
        return {}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _media_pending_only(
    rows: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Keep only rows whose metadata really flags a pending enrichment.

    The SQL narrows with a substring match, which is the same on every
    backend but also matches an item that merely records ``enrich_pending``
    as ``false``. The exact decision therefore happens here, on the parsed
    metadata, where a serializer's spacing cannot change the answer.
    """
    kept: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        if metadata.get("enrich_pending") is True:
            kept.append(row)
            if len(kept) >= max(1, int(limit)):
                break
    return kept


def _item_filter_sql(
    *,
    source_id: str | None,
    state: str | None,
    include_deleted: bool,
    mark: str = "?",
) -> tuple[str, list[Any]]:
    """``(WHERE clause, params)`` for the item inventory, shared by both
    backends (``mark`` is the driver's placeholder style)."""
    clauses: list[str] = []
    params: list[Any] = []
    if not include_deleted:
        clauses.append("deleted_at IS NULL")
    if source_id:
        clauses.append(f"source_id = {mark}")
        params.append(source_id)
    if state:
        clauses.append(f"state = {mark}")
        params.append(state)
    return (f" WHERE {' AND '.join(clauses)}" if clauses else ""), params


def _documents_unchanged(
    rows: Sequence[Any], chunks: Sequence[Any], content_hash: str
) -> bool:
    """True when the stored rows ALREADY are the passage set *chunks* describes.

    Why this matters far beyond saving two writes: ``uw_embeddings`` cascades on
    ``uw_documents``. A delete-and-reinsert therefore destroys every vector the
    item owns — including the ones of the ACTIVE embedding space, which the
    shadow rebuild after a model switch depends on keeping. Chunking is
    deterministic, so a re-embed of unchanged content produces byte-identical
    passages; recognizing that keeps the document ids (and their live vectors)
    stable across re-runs.
    """
    chunk_list = list(chunks)
    if len(rows) != len(chunk_list):
        return False
    by_index = {int(row["chunk_index"]): row for row in rows}
    for chunk in chunk_list:
        row = by_index.get(int(getattr(chunk, "index", 0)))
        if row is None:
            return False
        if str(row["text_norm"]) != str(getattr(chunk, "text", "")):
            return False
        if str(row["content_hash"] or "") != str(content_hash or ""):
            return False
    return True


def _counts_from_pairs(pairs: Iterable[tuple[str, int]]) -> PipelineCounts:
    by_state = {state.value: 0 for state in ItemState}
    for state_value, count in pairs:
        if state_value in by_state:
            by_state[state_value] = int(count)
    return PipelineCounts(**by_state)


_UNSET: Any = object()

#: Columns added AFTER the first shipped schema. They are declared in
#: ``schema.sql`` (and in :meth:`PostgresStore.ddl_statements`) so a fresh
#: database gets them from the CREATE, and appended here for databases that
#: already exist. SQLite has no ``ADD COLUMN IF NOT EXISTS``, so the existing
#: columns are read from ``PRAGMA table_info`` first — the pattern of
#: ``jarvis/missions/event_store.py``.
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # Connectors have always attached metadata to a RawItem — the source
    # format, a file's modification time, a chat's participants — and it was
    # dropped on the way into the store, silently. Nothing depended on it
    # until media items had to carry the reference back to their own bytes.
    ("uw_items", "metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("uw_sources", "last_notice", "TEXT"),
    ("uw_sync_state", "last_outcome_at", "TEXT"),
    ("uw_sync_state", "last_outcome_status", "TEXT"),
    ("uw_sync_state", "last_outcome_mode", "TEXT"),
    ("uw_sync_state", "last_new", "INTEGER NOT NULL DEFAULT 0"),
    ("uw_sync_state", "last_changed", "INTEGER NOT NULL DEFAULT 0"),
    ("uw_sync_state", "last_unchanged", "INTEGER NOT NULL DEFAULT 0"),
    ("uw_sync_state", "last_tombstoned", "INTEGER NOT NULL DEFAULT 0"),
    # Set by begin_reembed on every item whose vectors the model switch
    # invalidated, cleared the moment that item is embedded again. Without it
    # the re-embed backlog is indistinguishable from the ordinary ingest
    # backlog, and the claim order (newest first, right for ingest) schedules
    # exactly the items that need re-embedding LAST — on a 236 k-item store
    # that is the difference between minutes and days.
    ("uw_items", "reembed_pending", "INTEGER NOT NULL DEFAULT 0"),
    # Multi-chunk documents (migrations/0001_document_chunk_index.sql). SQLite
    # gains them from that migration; every OTHER database — a Postgres store
    # created before the feature, and a fresh one, since ``ddl_statements``
    # writes the base table — has to gain them here, or ``replace_documents``
    # inserts into columns that do not exist and the embed stage fails on
    # every single item.
    ("uw_documents", "chunk_index", "INTEGER NOT NULL DEFAULT 0"),
    ("uw_documents", "char_start", "INTEGER NOT NULL DEFAULT 0"),
    ("uw_documents", "char_end", "INTEGER NOT NULL DEFAULT 0"),
)

#: Indexes that belong to :data:`_ADDITIVE_COLUMNS` — created after the columns
#: exist, in both backends (``CREATE INDEX IF NOT EXISTS`` is portable).
_ADDITIVE_INDEXES: tuple[str, ...] = (
    # The claim query's exact shape: filter by state, take the re-embed
    # backlog first, newest first within each group. Without it every claim
    # sorts the whole ~115 k-row keyword_indexed bucket.
    "CREATE INDEX IF NOT EXISTS idx_uw_items_claim"
    " ON uw_items(state, reembed_pending DESC, timestamp_utc DESC, id DESC)",
    # :meth:`_reembed_remaining`'s exact shape, and the one index whose absence
    # is measurable as CPU load rather than as latency. That count runs on
    # every pipeline pass while a model switch is rebuilding, and no index
    # above can serve it: the claim index leads with ``state``, which the
    # query constrains only by ``!=``, so SQLite fell back to SCAN uw_items —
    # 665 ms over a 236 k-row store, ten times a second, which is one
    # saturated core doing nothing but recounting the same number (measured
    # 2026-07-27).
    #
    # PARTIAL on purpose: the flagged backlog is a rounding error next to the
    # corpus (1.3 k of 236 k here), so this indexes a thousandth of the table
    # and disappears entirely once a rebuild finishes. Carrying ``state`` and
    # ``deleted_at`` makes it cover every term of the count, so the answer
    # comes from the index alone and the table is never touched. Partial
    # indexes with a WHERE clause are portable across SQLite and Postgres,
    # like the rest of this tuple.
    "CREATE INDEX IF NOT EXISTS idx_uw_items_reembed_pending"
    " ON uw_items(state, deleted_at) WHERE reembed_pending = 1",
    # What makes "replace this item's passages" atomic and idempotent on the
    # backend that did not get migration 0001.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_uw_documents_chunk"
    " ON uw_documents(item_id, doc_type, chunk_index)",
)

#: The persisted per-source outcome of the last finished sync.
_OUTCOME_COLUMNS: tuple[str, ...] = (
    "last_outcome_at",
    "last_outcome_status",
    "last_outcome_mode",
    "last_new",
    "last_changed",
    "last_unchanged",
    "last_tombstoned",
)

#: Every live distilled document joined to its item and source — the whole
#: input of the readable projection (``jarvis.ultrawiki.projection``). Shared
#: verbatim by both backends because it binds no parameters, so the two
#: dialects cannot drift apart on the one query the wiki view depends on.
_DISTILLED_ROWS_SQL = (
    "SELECT d.id AS document_id, d.item_id, d.distill_json,"
    " i.title, i.timestamp_utc, i.permalink, i.source_id,"
    " s.label AS source_label"
    " FROM uw_documents d"
    " JOIN uw_items i ON i.id = d.item_id"
    " JOIN uw_sources s ON s.id = i.source_id"
    " WHERE d.distill_json IS NOT NULL AND d.distill_json != ''"
    "  AND i.deleted_at IS NULL"
    " ORDER BY i.timestamp_utc DESC, d.id DESC"
)

#: Cheap change stamp over the same set. ``MAX(id)`` is what makes a RE-
#: distillation visible: ``add_document`` replaces the row, so the count is
#: unchanged and ``created_at`` may land in the same second, but the new row
#: always carries a higher id.
_DISTILLED_FINGERPRINT_SQL = (
    "SELECT COUNT(*) AS n, COALESCE(MAX(d.id), 0) AS max_id,"
    " COALESCE(MAX(d.created_at), '') AS newest"
    " FROM uw_documents d"
    " JOIN uw_items i ON i.id = d.item_id"
    " WHERE d.distill_json IS NOT NULL AND d.distill_json != ''"
    "  AND i.deleted_at IS NULL"
)


# ---------------------------------------------------------------------------
# SQLite reference backend
# ---------------------------------------------------------------------------


class UltraStore(IdentityMixin, EventMixin, LexiconMixin):
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
        # Live-item count, cached until the next committed write. The count
        # is the IDF denominator queried on every search; a full count(*)
        # costs ~20 ms on a real store while the answer only moves when a
        # write commits.
        self._live_count_cache: int | None = None
        # Read-only connection pool for the search legs. One aiosqlite
        # connection = one worker thread, so concurrent legs sharing the
        # writer connection serialize completely (measured 0.99 overlap on a
        # live store); WAL gives each pooled reader a consistent snapshot
        # without ever blocking the writer.
        self._read_conns: list[aiosqlite.Connection] = []
        self._read_rr = 0
        self._readers_unavailable = False
        self._reader_vec_loaded: set[int] = set()
        self._pool_lock = asyncio.Lock()
        # Older builds could set deleted_at without removing searchable
        # derivatives or the original payload. The background pipeline drains
        # those legacy rows in bounded batches; current writes keep the
        # invariant after one clean pass has been observed.
        self._legacy_tombstone_repair_done = False

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
            await self._apply_column_migrations(conn)
            await self._adopt_running_reembed(conn)
            await self._repair_unclaimable_reembed(conn)
            self._conn = conn

    @staticmethod
    async def _apply_column_migrations(conn: aiosqlite.Connection) -> None:
        """Append the :data:`_ADDITIVE_COLUMNS` an older database still lacks."""
        known: dict[str, set[str]] = {}
        for table, column, declaration in _ADDITIVE_COLUMNS:
            if table not in known:
                cur = await conn.execute(f"PRAGMA table_info({table})")  # noqa: S608 — table names are code-owned literals
                rows = await cur.fetchall()
                await cur.close()
                known[table] = {str(row[1]) for row in rows}
            if column in known[table]:
                continue
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"  # noqa: S608 — code-owned literals
            )
            known[table].add(column)
            log.info("UltraStore migration applied — added %s.%s", table, column)
        for statement in _ADDITIVE_INDEXES:
            await conn.execute(statement)

    @staticmethod
    async def _adopt_running_reembed(conn: aiosqlite.Connection) -> None:
        """Flag the backlog of a rebuild that started before this column existed.

        A switch performed by an older build left a pending pin and a demoted
        corpus but no way to tell the two backlogs apart, so the rebuild sat
        behind every never-embedded item in the store. Reconstructing the set
        is exact rather than a guess: an item that still holds a vector in the
        ACTIVE space is, by definition, one the switch invalidated.

        Runs at most once per store — the presence of ``reembed_total`` is the
        marker — and does nothing at all when no rebuild is pending, which is
        the steady state.
        """
        cur = await conn.execute(
            "SELECT key, value FROM uw_meta WHERE key IN (?, ?, ?)",
            (META_PENDING_EMBED_MODEL, META_EMBED_MODEL, META_REEMBED_TOTAL),
        )
        meta = {str(row[0]): str(row[1]) for row in await cur.fetchall()}
        await cur.close()
        if META_PENDING_EMBED_MODEL not in meta or META_REEMBED_TOTAL in meta:
            return
        active_model = meta.get(META_EMBED_MODEL)
        if not active_model:
            return
        cur = await conn.execute(
            "UPDATE uw_items SET reembed_pending = 1"
            " WHERE deleted_at IS NULL AND state != ?"
            "   AND EXISTS (SELECT 1 FROM uw_documents d"
            "               JOIN uw_embeddings e ON e.document_id = d.id"
            "               WHERE d.item_id = uw_items.id AND e.model = ?)",
            (ItemState.FAILED.value, active_model),
        )
        flagged = int(cur.rowcount or 0)
        await cur.close()
        await conn.execute(
            "INSERT OR REPLACE INTO uw_meta (key, value) VALUES (?, ?)",
            (META_REEMBED_TOTAL, str(flagged)),
        )
        log.info(
            "UltraWiki: adopted a rebuild that was already running — %d item(s) "
            "moved to the front of the embed queue",
            flagged,
        )

    @staticmethod
    async def _repair_unclaimable_reembed(conn: aiosqlite.Connection) -> None:
        """Enforce the invariant every rebuild depends on: a FLAGGED item must
        be CLAIMABLE.

        ``_reembed_remaining`` counts ``reembed_pending = 1``; ``claim_batch``
        only ever selects the PREDECESSOR state of the stage it feeds. An item
        that is flagged while sitting in ``embedded``/``distilled`` therefore
        satisfies neither: the counter waits for it forever and no worker can
        ever reach it. The promotion never fires, and because the distill stage
        stands aside for a running rebuild (``reembed-priority``), summaries
        stop too — permanently, from a handful of rows.

        ``begin_reembed`` demotes correctly; ``_adopt_running_reembed`` did not,
        which is how nine such rows stranded a 4 712-item rebuild at 4 703 (the
        2026-07-28 forensic). Rather than trust every future writer of the flag
        to remember, the invariant is restored on open: cheap (the partial
        index ``idx_uw_items_reembed_pending`` answers it), idempotent, and a
        no-op in the overwhelmingly common case of no rebuild at all.
        """
        await conn.execute(
            "UPDATE uw_items SET state = ?, updated_at = ?"
            " WHERE reembed_pending = 1 AND deleted_at IS NULL"
            "   AND state IN (?, ?)",
            (
                ItemState.KEYWORD_INDEXED.value,
                _iso_utc(),
                ItemState.EMBEDDED.value,
                ItemState.DISTILLED.value,
            ),
        )

    async def close(self) -> None:
        async with self._lock:
            for reader in self._read_conns:
                with suppress(Exception):
                    await reader.close()
            self._read_conns = []
            self._reader_vec_loaded.clear()
            self._readers_unavailable = False
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
            self._vec_ext_loaded = False
            self._vec_state = None
            self._vec_dim = None
            self._live_count_cache = None

    async def _ensure_open(self) -> aiosqlite.Connection:
        if self._conn is None:
            await self.open()
        assert self._conn is not None
        return self._conn

    #: Four pooled readers cover the concurrent search legs (keyword, vector,
    #: events and term signals) without spawning a thread per query. With two,
    #: the signal query regularly queued behind a multi-second sqlite-vec scan
    #: on the live corpus, making a cheap keyword fallback wait for semantics.
    _READ_POOL_SIZE = 4

    async def _read_conn(self) -> aiosqlite.Connection:
        """A pooled read-only connection for the search legs.

        The writer connection stays the only one that ever runs DDL,
        migrations, or writes (its ``executescript`` in :meth:`open` happens
        first, so the file and schema exist before any reader opens). Every
        failure to build the pool degrades honestly to the writer connection
        — correctness never depends on the pool existing.
        """
        writer = await self._ensure_open()
        if self._readers_unavailable:
            return writer
        if not self._read_conns:
            async with self._pool_lock:
                if not self._read_conns and not self._readers_unavailable:
                    try:
                        self._read_conns = await self._open_readers()
                    except Exception:
                        # In-memory paths, exotic filesystems, sandboxed
                        # read-only mounts: reads stay on the writer.
                        log.info(
                            "read-only connection pool unavailable — search "
                            "legs share the writer connection",
                            exc_info=True,
                        )
                        self._readers_unavailable = True
                        return writer
        if not self._read_conns:
            return writer
        self._read_rr = (self._read_rr + 1) % len(self._read_conns)
        return self._read_conns[self._read_rr]

    async def _open_readers(self) -> list[aiosqlite.Connection]:
        posix = self._db_path.resolve().as_posix()
        if not posix.startswith("/"):
            posix = "/" + posix  # Windows drive paths become /C:/…
        # RFC 3986: the path must be percent-encoded (a literal space in an
        # install path would otherwise corrupt the URI).
        uri = f"file:{quote(posix, safe='/:')}?mode=ro"
        readers: list[aiosqlite.Connection] = []
        try:
            for _ in range(self._READ_POOL_SIZE):
                conn = await aiosqlite.connect(uri, uri=True, isolation_level=None)
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA busy_timeout = 5000")
                readers.append(conn)
        except BaseException:
            for conn in readers:
                with suppress(Exception):
                    await conn.close()
            raise
        return readers

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
                # Every write path runs through here; deliberately coarse —
                # an unnecessary refresh costs one count(*), a stale count
                # would silently skew the IDF signal.
                self._live_count_cache = None

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

    async def _id_insert(
        self, conn: aiosqlite.Connection, sql: str, params: Sequence[Any]
    ) -> int:
        """Identity-layer INSERT hook: SQLite reports the id via ``lastrowid``.

        Deliberately not ``RETURNING``: that needs SQLite ≥ 3.35, and the
        headless-Linux floor may run whatever the distro ships.
        """
        cur = await conn.execute(sql, params)
        row_id = int(cur.lastrowid or 0)
        await cur.close()
        return row_id

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
            "last_notice": row["last_notice"],
        }

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        conn = await self._ensure_open()
        row = await self._fetchone(
            conn, "SELECT * FROM uw_sources WHERE id = ?", (source_id,)
        )
        return None if row is None else self._source_row_to_dict(row)

    async def list_sources(self) -> list[dict[str, Any]]:
        """All sources with per-source pipeline counts and sync state."""
        # Status polls must not queue behind a large import transaction on the
        # writer connection. WAL readers see the latest committed snapshot and
        # keep the Overview responsive while a source is being reconciled.
        conn = await self._read_conn()
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
        last_notice: str | None = _UNSET,
    ) -> None:
        """Partial update of the per-source sync status columns.

        ``last_error`` and ``last_notice`` are deliberately separate: an error
        means the sync failed, a notice means it ran fine and had nothing to
        import. Writing a notice into the error column would make a healthy
        source look broken (and the next successful sync would silently erase
        it along with the real errors).
        """
        sets: list[str] = []
        params: list[Any] = []
        if last_sync_at is not _UNSET:
            sets.append("last_sync_at = ?")
            params.append(last_sync_at)
        if last_error is not _UNSET:
            sets.append("last_error = ?")
            params.append(last_error)
        if last_notice is not _UNSET:
            sets.append("last_notice = ?")
            params.append(last_notice)
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
                        await self._clear_deleted_payload(conn, [row["id"]])
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
                        "  content_hash, state, metadata_json, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                            _metadata_json(item),
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
                        " state = ?, metadata_json = ?, attempt_count = 0,"
                        " next_retry_at = NULL, last_error = NULL,"
                        " deleted_at = NULL, updated_at = ?"
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
                            _metadata_json(item),
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
        """Remove FTS rows, documents (cascading embeddings), derived events
        and — when the vec index is live on this connection — vector rows for
        *item_ids*.

        Vector rows a session without the extension cannot delete are
        reconciled on the next :meth:`_ensure_vec` (stale-row sweep).

        Events belong here for the same reason documents do: they were derived
        from text that has just changed or been tombstoned, so leaving them
        would let a sentence that no longer exists keep answering questions.
        """
        if not item_ids:
            return
        for chunk in _chunks(list(item_ids)):
            marks = _placeholders(len(chunk))
            await conn.execute(
                f"DELETE FROM uw_fts WHERE item_id IN ({marks})",  # noqa: S608 — placeholders only
                chunk,
            )
            await self._ev_purge(conn, chunk)
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

    async def _clear_deleted_payload(
        self, conn: aiosqlite.Connection, item_ids: Sequence[int]
    ) -> None:
        """Keep only a tombstone's stable identity, never its source content."""
        for chunk in _chunks(list(item_ids)):
            sql = (
                "UPDATE uw_items SET thread_key = '', author_raw = '', title = '',"  # noqa: S608
                " body_raw = '', permalink = '', timestamp_utc = '',"
                " areas_json = '[]', metadata_json = '{}', attempt_count = 0,"
                " next_retry_at = NULL, last_error = NULL, reembed_pending = 0"
                f" WHERE id IN ({_placeholders(len(chunk))})"
            )
            await conn.execute(sql, chunk)

    async def repair_legacy_tombstones(self, *, limit: int = 5000) -> int:
        """Scrub legacy tombstones and purge derivatives in bounded batches."""
        if self._legacy_tombstone_repair_done:
            return 0
        batch_limit = min(10_000, max(1, int(limit)))
        async with self._txn() as conn:
            rows = await self._fetchall(
                conn,
                "SELECT id FROM uw_items WHERE deleted_at IS NOT NULL AND ("
                " thread_key != '' OR author_raw != '' OR title != '' OR"
                " body_raw != '' OR permalink != '' OR timestamp_utc != '' OR"
                " areas_json != '[]' OR metadata_json != '{}' OR"
                " attempt_count != 0 OR next_retry_at IS NOT NULL OR"
                " last_error IS NOT NULL OR reembed_pending != 0) LIMIT ?",
                (batch_limit,),
            )
            item_ids = [int(row["id"]) for row in rows]
            remaining = batch_limit - len(item_ids)
            if remaining:
                # A partially repaired row may already have a blank payload
                # while an old derivative remains. These code-owned tables are
                # the three independent derivative roots on SQLite.
                for table in ("uw_documents", "uw_events", "uw_fts"):
                    extra = await self._fetchall(
                        conn,
                        f"SELECT DISTINCT d.item_id AS id FROM {table} d"  # noqa: S608
                        " JOIN uw_items i ON i.id = d.item_id"
                        " WHERE i.deleted_at IS NOT NULL LIMIT ?",
                        (remaining,),
                    )
                    known = set(item_ids)
                    item_ids.extend(
                        int(row["id"])
                        for row in extra
                        if int(row["id"]) not in known
                    )
                    remaining = batch_limit - len(item_ids)
                    if not remaining:
                        break
            if not item_ids:
                self._legacy_tombstone_repair_done = True
                return 0
            await self._purge_derived(conn, item_ids)
            await self._clear_deleted_payload(conn, item_ids)
        return len(item_ids)

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
            "metadata": _parse_metadata(row),
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

    async def item_documents(self, item_id: int) -> list[dict[str, Any]]:
        """The derived documents of one item, with their embedding state.

        What a user means by "show me what is actually stored": not the raw
        text they already have at the source, but what UltraWiki MADE of it —
        the normalised document, the distillation, and whether it carries a
        vector. Without this, "distilled" is a badge with nothing behind it.
        """
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT d.id, d.doc_type, d.text_norm, d.distill_json,"
            " d.distill_version, d.created_at, d.chunk_index,"
            " d.char_start, d.char_end,"
            " EXISTS (SELECT 1 FROM uw_embeddings e WHERE e.document_id = d.id)"
            "   AS has_vector"
            " FROM uw_documents d WHERE d.item_id = ?"
            " ORDER BY d.doc_type, d.chunk_index, d.id",
            (item_id,),
        )
        # `_fetchall` hands back aiosqlite.Row, which has no .get() — mapping
        # each row through dict() first is what keeps this from raising an
        # AttributeError the caller would only ever see as "no documents".
        return [
            {**dict(row), "has_vector": bool(dict(row).get("has_vector"))}
            for row in rows
        ]

    async def list_items(
        self,
        *,
        source_id: str | None = None,
        state: ItemState | str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of the stored inventory + the unpaged total.

        Newest-INGESTED first (``created_at``), not newest-authored: the
        question this answers is "what did the last import actually put in
        here", and a decade-old note imported a minute ago belongs at the top.
        Tombstoned rows stay out unless ``include_deleted`` asks for them —
        they are no longer part of what the store answers from.
        """
        coerced = _coerce_state(state).value if state else None
        where, params = _item_filter_sql(
            source_id=source_id, state=coerced, include_deleted=include_deleted
        )
        conn = await self._ensure_open()
        total_row = await self._fetchone(
            conn,
            f"SELECT count(*) AS n FROM uw_items{where}",  # noqa: S608 — placeholders only
            params,
        )
        total = int(total_row["n"]) if total_row is not None else 0
        rows = await self._fetchall(
            conn,
            f"SELECT {_ITEM_LIST_COLUMNS} FROM uw_items{where}"  # noqa: S608 — code-owned projection + placeholders
            " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, max(0, int(limit)), max(0, int(offset))],
        )
        return [dict(row) for row in rows], total

    async def set_item_metadata(self, item_id: int, metadata: dict[str, Any]) -> None:
        """Replace one item's metadata WITHOUT touching its content or state.

        The narrow path a side lane needs to record an outcome. It cannot go
        through :meth:`upsert_items`: unchanged content leaves the row
        completely untouched by design (the zero-new-work guarantee), so a
        failure note written that way would be silently discarded and the same
        file would be retried forever with nothing to show for it.

        Content, hash, state and retry bookkeeping are deliberately untouched
        — this records what was learned ABOUT an item, never what it says.
        """
        async with self._txn() as conn:
            await conn.execute(
                "UPDATE uw_items SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata or {}, ensure_ascii=False, default=str), int(item_id)),
            )

    async def pending_media_items(self, *, limit: int = 8) -> list[dict[str, Any]]:
        """Media items still waiting to be described or transcribed.

        Deliberately NOT a new pipeline state (AP-4): a fifth value crossing
        Python, SQL, Pydantic, TypeScript and the UI is the drift class this
        repo has paid for four times, and enrichment is a SIDE lane rather
        than a step every item takes. The flag lives in the item's metadata
        and the SQL narrows on it with a plain ``LIKE`` — no JSON operator, so
        this one query is identical on both backends — with the exact decision
        made in Python, where a serializer's spacing cannot change the answer.

        Oldest first: a backlog should drain in the order it built up.
        """
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT * FROM uw_items"
            " WHERE deleted_at IS NULL AND metadata_json LIKE '%enrich_pending%'"
            " ORDER BY id ASC LIMIT ?",
            (max(1, int(limit)) * 4,),
        )
        return _media_pending_only([self._item_row_to_dict(row) for row in rows], limit)

    async def claim_batch(
        self,
        target_state: ItemState | str,
        *,
        limit: int = 50,
        now: str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Items ready to advance TO *target_state* (their state is the
        predecessor in ``STATE_ORDER``), retry-eligible, re-embed backlog
        first and newest first within each group.

        Claiming is read-only — the single-process pipeline claims, works,
        then commits exactly one transition via :meth:`mark_stage_done` /
        :meth:`mark_retry`, so a crash between claim and commit loses
        nothing (the item is simply claimed again).

        **Why two sort keys.** Newest-first is right for ingest: fresh content
        becomes searchable while an old backfill drains behind it. It is the
        worst possible order for a re-embed, because the items a model switch
        invalidated are by definition the OLDEST in the store — they would be
        rebuilt only after every never-embedded item, and until then the
        vector space cannot be promoted and semantic search stays on the old
        model. ``reembed_pending`` (set by :meth:`begin_reembed`) puts that
        backlog in front without disturbing the ingest order behind it.
        """
        predecessor = _predecessor_of(target_state)
        now_s = _iso_utc(_coerce_now(now))
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT * FROM uw_items"
            " WHERE state = ? AND deleted_at IS NULL"
            "   AND (next_retry_at IS NULL OR next_retry_at <= ?)"
            " ORDER BY reembed_pending DESC, timestamp_utc DESC, id DESC LIMIT ?",
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
        # Reaching `embedded` is exactly what the re-embed backlog was waiting
        # for: this item now has a vector in the space being built, so it
        # leaves the priority lane and the progress counter moves.
        clear_reembed = ", reembed_pending = 0" if state is ItemState.EMBEDDED else ""
        sql = (
            "UPDATE uw_items SET state = ?, attempt_count = 0,"  # noqa: S608 — clear_reembed is one of two code-owned literals
            " next_retry_at = NULL, last_error = NULL, updated_at = ?"
            f"{clear_reembed}"
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
        conn = await self._read_conn()
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

    async def distilled_rows(self) -> list[dict[str, Any]]:
        """Input rows of the readable projection (see ``_DISTILLED_ROWS_SQL``)."""
        conn = await self._ensure_open()
        rows = await self._fetchall(conn, _DISTILLED_ROWS_SQL)
        return [dict(row) for row in rows]

    async def distilled_fingerprint(self) -> tuple[int, int, str]:
        """Change stamp of the projection input, for caching the projection."""
        conn = await self._ensure_open()
        row = await self._fetchone(conn, _DISTILLED_FINGERPRINT_SQL)
        if row is None:
            return (0, 0, "")
        data = dict(row)
        return (int(data["n"]), int(data["max_id"]), str(data["newest"]))

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
                await self._clear_deleted_payload(conn, doomed)
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

    async def replace_documents(
        self,
        item_id: int,
        doc_type: DocType | str,
        chunks: Sequence[Any],
        *,
        content_hash: str = "",
    ) -> list[int]:
        """Replace ALL documents of one ``(item_id, doc_type)`` with N passages.

        :meth:`add_document` keeps exactly one row per ``(item, type)``, which
        made a multi-vector item structurally impossible: passage 2 deleted
        passage 1. That single rule is why an item reached the vector space as
        its opening 8 000 characters and nothing else.

        ``chunks`` are :class:`jarvis.ultrawiki.chunking.Chunk` objects (or any
        object carrying ``index``/``text``/``char_start``/``char_end``). The
        whole set is swapped inside ONE transaction, so a reader never observes
        an item with half its passages, and a re-run is idempotent.

        Returns the new document ids in passage order, so the caller can pair
        each with its vector. When the stored passages are already identical
        (a re-embed of unchanged content), the existing rows are KEPT and their
        ids returned — see :func:`_documents_unchanged` for why that is load
        bearing rather than an optimization.
        """
        kind = DocType(doc_type).value
        now = _iso_utc()
        async with self._txn() as conn:
            old_rows = await self._fetchall(
                conn,
                "SELECT id, chunk_index, text_norm, content_hash FROM uw_documents"
                " WHERE item_id = ? AND doc_type = ?",
                (item_id, kind),
            )
            if _documents_unchanged(old_rows, chunks, content_hash):
                by_index = {int(row["chunk_index"]): int(row["id"]) for row in old_rows}
                return [by_index[int(getattr(c, "index", 0))] for c in chunks]
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
            new_ids: list[int] = []
            for chunk in chunks:
                cur = await conn.execute(
                    "INSERT INTO uw_documents (item_id, doc_type, text_norm,"
                    " distill_json, distill_version, content_hash, created_at,"
                    " chunk_index, char_start, char_end)"
                    " VALUES (?, ?, ?, NULL, 0, ?, ?, ?, ?, ?) RETURNING id",
                    (
                        item_id,
                        kind,
                        str(getattr(chunk, "text", "")),
                        content_hash,
                        now,
                        int(getattr(chunk, "index", 0)),
                        int(getattr(chunk, "char_start", 0)),
                        int(getattr(chunk, "char_end", 0)),
                    ),
                )
                row = await cur.fetchone()
                await cur.close()
                assert row is not None
                new_ids.append(int(row[0]))
            return new_ids

    async def _pinned_space(self) -> tuple[str | None, int | None]:
        model = await self.get_meta(META_EMBED_MODEL)
        dim_raw = await self.get_meta(META_EMBED_DIM)
        return model, int(dim_raw) if dim_raw else None

    async def _pending_space(self) -> tuple[str | None, int | None]:
        model = await self.get_meta(META_PENDING_EMBED_MODEL)
        dim_raw = await self.get_meta(META_PENDING_EMBED_DIM)
        return model, int(dim_raw) if dim_raw else None

    async def _writes_to_active_space(self, model: str, dim: int) -> bool:
        """Decide which space a vector of ``(model, dim)`` belongs to.

        ``True`` means the ACTIVE space — the one ``uw_vec`` mirrors and every
        search answers from. ``False`` means the shadow space of a running
        rebuild. A pair matching neither raises: that is the D-3 guard, so
        nothing mixes incompatible vector spaces by accident. A deliberate
        change goes through :meth:`begin_reembed`.
        """
        pinned_model, pinned_dim = await self._pinned_space()
        if pinned_model is None or pinned_dim is None:
            await self.set_meta(META_EMBED_MODEL, model)
            await self.set_meta(META_EMBED_DIM, str(dim))
            return True
        if pinned_model == model and pinned_dim == dim:
            return True
        pending_model, pending_dim = await self._pending_space()
        if pending_model is not None and pending_model == model:
            if pending_dim is None:
                # First vector of the rebuild: the provider just told us the
                # width of the new space.
                await self.set_meta(META_PENDING_EMBED_DIM, str(dim))
            elif pending_dim != dim:
                raise UltraStoreError(
                    f"embedding dimension mismatch: the rebuild of {model!r} "
                    f"started at dim={pending_dim} but got dim={dim}"
                )
            return False
        if pinned_model == model:
            # Same model NAME, different width — a genuinely different model
            # behind a familiar name (switching provider onto a same-named
            # model). It IS a new vector space, so rebuild into it rather than
            # reject every vector until the corpus dead-letters.
            log.warning(
                "UltraWiki: model %r now answers with dim=%d instead of %d — "
                "rebuilding the vector space in the background",
                model,
                dim,
                pinned_dim,
            )
            await self.begin_reembed(model, dim=dim)
            return False
        raise EmbeddingSpaceMismatch(
            "embedding space mismatch: the store is pinned to "
            f"model={pinned_model!r} dim={pinned_dim} but got "
            f"model={model!r} dim={dim}. Changing the embedding model goes "
            "through begin_reembed() (rebuilds the corpus in the background "
            "while search keeps using the current vectors)."
        )

    async def store_embedding(
        self,
        document_id: int,
        *,
        model: str,
        dim: int,
        vector: Sequence[float],
    ) -> None:
        """Store one vector (little-endian float32 BLOB) for a document.

        The first embedding pins ``embed_model``/``embed_dim`` in ``uw_meta``
        (D-3). While a model switch is rebuilding, vectors of the new model
        land in the shadow space instead and are deliberately kept OUT of
        ``uw_vec``: the ANN index mirrors the active space alone, so searches
        keep answering correctly until :meth:`promote_pending_space` swaps
        them.
        """
        if len(vector) != dim:
            raise UltraStoreError(
                f"vector has {len(vector)} components but dim={dim} was declared"
            )
        active = await self._writes_to_active_space(model, dim)
        vec_ok = False
        if active:
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
            # Cheap path first: every vector_search lands here. The pinned
            # space can only change through reset_vectors(), which clears
            # this cache — so a cached verdict needs no meta lookups.
            if self._vec_state is not None and self._vec_dim is not None:
                return self._vec_state
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
        # The index mirrors the ACTIVE space alone. A shadow rebuild's vectors
        # answer a different geometry and cover only part of the corpus, so
        # letting them in would corrupt every search until promotion.
        active_model, _ = await self._pinned_space()
        space = (active_model or "", int(dim))
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
                " (SELECT document_id FROM uw_embeddings"
                "  WHERE model = ? AND dim = ?)",
                space,
            )
            missing = await self._fetchall(
                conn,
                "SELECT document_id, vector FROM uw_embeddings"
                " WHERE model = ? AND dim = ?"
                " AND document_id NOT IN (SELECT rowid FROM uw_vec)",
                space,
            )
            if missing:
                # One batch, not one round trip per row: the first search
                # after boot can find thousands of vectors to backfill, and
                # this runs under the store lock.
                await conn.executemany(
                    "INSERT OR REPLACE INTO uw_vec (rowid, embedding) VALUES (?, ?)",
                    [(row["document_id"], row["vector"]) for row in missing],
                )
        self._vec_dim = dim
        self._vec_state = (True, "")
        return self._vec_state

    async def _vec_ready_conn(self, conn: aiosqlite.Connection) -> aiosqlite.Connection:
        """Make *conn* KNN-capable, or fall back to the writer connection.

        The sqlite-vec extension loads per CONNECTION while the readiness
        verdict is cached per STORE — a pooled reader that skipped this
        step would run the KNN query against a connection without ``vec0``
        and fail (the audit's flagged trap).
        """
        if conn is self._conn:
            return conn  # the writer carries the extension via _ensure_vec
        if id(conn) in self._reader_vec_loaded:
            return conn
        try:
            vec_mod = _import_sqlite_vec()
            await conn.enable_load_extension(True)
            await conn.load_extension(vec_mod.loadable_path())
            await conn.enable_load_extension(False)
        except Exception:  # noqa: BLE001 — degrade to the writer, never fail the leg
            log.debug(
                "sqlite-vec unavailable on a pooled reader — KNN uses the "
                "writer connection",
                exc_info=True,
            )
            return await self._ensure_open()
        self._reader_vec_loaded.add(id(conn))
        return conn

    async def vector_status(self) -> tuple[bool, str]:
        """Honest capability report for the vector leg."""
        return await self._ensure_vec(None)

    async def reset_vectors(self) -> None:
        """Hard reset: wipe the vector index and EVERY stored embedding of
        every space, clear both pins, and reset ``embedded``/``distilled``
        items to ``keyword_indexed`` so the pipeline re-embeds
        (re-distillation rides the distill cache). Derived documents keep
        their text; only vectors are discarded.

        This blinds semantic search until the corpus is rebuilt, so it is the
        recovery path, not the model-switch path — a switch uses
        :meth:`begin_reembed`, which keeps the current vectors answering.
        """
        vec_droppable = self._vec_ext_loaded  # vec0 DDL needs the extension
        async with self._txn() as conn:
            if vec_droppable:
                await conn.execute("DROP TABLE IF EXISTS uw_vec")
            await conn.execute("DELETE FROM uw_embeddings")
            await conn.execute(
                "DELETE FROM uw_meta WHERE key IN (?, ?, ?, ?, ?)",
                (
                    META_EMBED_MODEL,
                    META_EMBED_DIM,
                    META_PENDING_EMBED_MODEL,
                    META_PENDING_EMBED_DIM,
                    META_REEMBED_TOTAL,
                ),
            )
            await conn.execute(
                "UPDATE uw_items SET state = ?, reembed_pending = 0, updated_at = ?"
                " WHERE state IN (?, ?)",
                (
                    ItemState.KEYWORD_INDEXED.value,
                    _iso_utc(),
                    ItemState.EMBEDDED.value,
                    ItemState.DISTILLED.value,
                ),
            )
        self._vec_state = None
        self._vec_dim = None

    async def begin_reembed(self, model: str, *, dim: int | None = None) -> bool:
        """Start rebuilding the corpus in *model*'s vector space, in the
        background, WITHOUT taking semantic search down.

        *dim* is normally unknown until the provider answers and stays ``None``;
        pass it when the width is already known and distinguishes the target
        space from the live one (same model name, different geometry).

        The live vectors, the ``uw_vec`` index and every search stay untouched;
        new vectors accumulate in the shadow space until
        :meth:`promote_pending_space` finds it complete and swaps it in. An
        abandoned rebuild costs nothing but the shadow rows, which the next
        call drops.

        Returns ``True`` when a rebuild is running for *model* afterwards.
        ``False`` means there is nothing to rebuild: either the corpus already
        sits in that space (a provider change that keeps the model — same
        geometry, no work) or nothing has been embedded yet, in which case the
        pipeline's normal backlog already produces the new space.

        **Idempotent.** Called again for the SAME target — a second settings
        save, a provider change and a model change arriving as two requests —
        it reports the running rebuild and returns without touching anything.
        Re-running the body would delete every shadow vector built so far and
        re-demote the items that had already been rebuilt, throwing away hours
        of provider time for a request that changed nothing.
        """
        pinned_model, pinned_dim = await self._pinned_space()
        same_space = pinned_model == model and (dim is None or pinned_dim == dim)
        if pinned_model is None or pinned_dim is None or same_space:
            # Switching back to the live model mid-rebuild lands here too: the
            # shadow becomes garbage and the live space was never touched.
            await self.abort_reembed()
            return False
        pending_model, pending_dim = await self._pending_space()
        if pending_model == model and (
            dim is None or pending_dim is None or pending_dim == dim
        ):
            log.info(
                "UltraWiki: a rebuild into %r is already running — keeping its "
                "progress instead of restarting it",
                model,
            )
            return True
        now = _iso_utc()
        async with self._txn() as conn:
            # A superseded rebuild leaves half-built rows behind. Everything
            # outside the ACTIVE space is exactly that.
            await conn.execute(
                "DELETE FROM uw_embeddings WHERE model != ? OR dim != ?",
                (pinned_model, pinned_dim),
            )
            await conn.execute(
                "INSERT OR REPLACE INTO uw_meta (key, value) VALUES (?, ?)",
                (META_PENDING_EMBED_MODEL, model),
            )
            # The width is normally the provider's answer, not ours — see
            # _writes_to_active_space.
            if dim is None:
                await conn.execute(
                    "DELETE FROM uw_meta WHERE key = ?", (META_PENDING_EMBED_DIM,)
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO uw_meta (key, value) VALUES (?, ?)",
                    (META_PENDING_EMBED_DIM, str(int(dim))),
                )
            # A superseded rebuild's flags describe work for a space that no
            # longer exists; the fresh set below is the whole truth.
            await conn.execute(
                "UPDATE uw_items SET reembed_pending = 0 WHERE reembed_pending != 0"
            )
            # What the promotion must wait for is "holds a vector in the space
            # being replaced" — derived from the vectors themselves, not from
            # the state column alone, so an item parked outside embedded/
            # distilled by a concurrent content change is not silently left
            # behind in the old space.
            cur = await conn.execute(
                "UPDATE uw_items SET reembed_pending = 1"
                " WHERE deleted_at IS NULL AND state != ?"
                "   AND (state IN (?, ?)"
                "        OR EXISTS (SELECT 1 FROM uw_documents d"
                "                   JOIN uw_embeddings e ON e.document_id = d.id"
                "                   WHERE d.item_id = uw_items.id"
                "                     AND e.model = ? AND e.dim = ?))",
                (
                    ItemState.FAILED.value,
                    ItemState.EMBEDDED.value,
                    ItemState.DISTILLED.value,
                    pinned_model,
                    pinned_dim,
                ),
            )
            flagged = int(cur.rowcount or 0)
            await cur.close()
            await conn.execute(
                "UPDATE uw_items SET state = ?, updated_at = ?"
                " WHERE state IN (?, ?) AND deleted_at IS NULL",
                (
                    ItemState.KEYWORD_INDEXED.value,
                    now,
                    ItemState.EMBEDDED.value,
                    ItemState.DISTILLED.value,
                ),
            )
            await conn.execute(
                "INSERT OR REPLACE INTO uw_meta (key, value) VALUES (?, ?)",
                (META_REEMBED_TOTAL, str(flagged)),
            )
        log.info(
            "UltraWiki: rebuilding the vector space in %r — %d item(s) moved to "
            "the front of the embed queue",
            model,
            flagged,
        )
        return True

    async def abort_reembed(self) -> bool:
        """Discard a running rebuild and keep the live space. ``True`` when
        one was actually running. Items already re-embedded stay where the
        pipeline left them — their live vectors were never removed."""
        pending_model, _ = await self._pending_space()
        pinned_model, pinned_dim = await self._pinned_space()
        if pending_model is None:
            return False
        async with self._txn() as conn:
            if pinned_model is not None and pinned_dim is not None:
                await conn.execute(
                    "DELETE FROM uw_embeddings WHERE model != ? OR dim != ?",
                    (pinned_model, pinned_dim),
                )
            await conn.execute(
                "DELETE FROM uw_meta WHERE key IN (?, ?, ?)",
                (
                    META_PENDING_EMBED_MODEL,
                    META_PENDING_EMBED_DIM,
                    META_REEMBED_TOTAL,
                ),
            )
            await conn.execute(
                "UPDATE uw_items SET reembed_pending = 0 WHERE reembed_pending != 0"
            )
        return True

    async def _reembed_remaining(self, conn: aiosqlite.Connection) -> int:
        """How many items the running rebuild has still not re-embedded.

        Counting the flagged WORK rather than the surviving old vectors is
        what makes this both correct and monotonic. The natural-looking
        alternative — "old-space documents without a twin in the new space" —
        is unmeasurable whenever the passage set changes underneath a rebuild:
        re-embedding such an item REPLACES its documents (new ids, the old
        vectors cascade away with the old rows), so no document ever holds
        both spaces, the count reads 0 % throughout, and the promotion can
        only ever fire once the live space has been destroyed row by row.

        Dead-lettered and tombstoned items are excluded: they will never be
        re-embedded, and waiting for them would stall the promotion forever
        behind a handful of permanently failing items.
        """
        row = await self._fetchone(
            conn,
            "SELECT count(*) AS n FROM uw_items"
            " WHERE reembed_pending = 1 AND deleted_at IS NULL AND state != ?",
            (ItemState.FAILED.value,),
        )
        return 0 if row is None else int(row["n"])

    async def promote_pending_space(self) -> bool:
        """Swap a completed shadow space in; a no-op otherwise.

        The pipeline calls this after every pass, so the common case (nothing
        pending) must stay cheap — it costs one ``uw_meta`` read.
        """
        pending_model, pending_dim = await self._pending_space()
        if pending_model is None or pending_dim is None:
            return False  # no rebuild, or not one vector produced yet
        active_model, active_dim = await self._pinned_space()
        if active_model is None or active_dim is None:
            return False
        conn = await self._ensure_open()
        if await self._reembed_remaining(conn):
            return False
        vec_droppable = self._vec_ext_loaded
        async with self._txn() as txn:
            await txn.execute(
                "DELETE FROM uw_embeddings WHERE model = ? AND dim = ?",
                (active_model, active_dim),
            )
            await txn.execute(
                "INSERT OR REPLACE INTO uw_meta (key, value) VALUES (?, ?)",
                (META_EMBED_MODEL, pending_model),
            )
            await txn.execute(
                "INSERT OR REPLACE INTO uw_meta (key, value) VALUES (?, ?)",
                (META_EMBED_DIM, str(pending_dim)),
            )
            await txn.execute(
                "DELETE FROM uw_meta WHERE key IN (?, ?, ?)",
                (
                    META_PENDING_EMBED_MODEL,
                    META_PENDING_EMBED_DIM,
                    META_REEMBED_TOTAL,
                ),
            )
            if vec_droppable:
                # The index is derived, so rebuilding it is a local copy, not
                # a re-embed: _ensure_vec backfills it from the new space.
                await txn.execute("DROP TABLE IF EXISTS uw_vec")
        self._vec_state = None
        self._vec_dim = None
        await self._ensure_vec(pending_dim)
        log.info(
            "UltraWiki embedding space promoted: %s (dim=%d) replaces %s",
            pending_model,
            pending_dim,
            active_model,
        )
        return True

    async def reconcile_space(self, model: str) -> str:
        """Make the store agree with the model the pipeline is about to use.

        **Why this exists (forensic 2026-07-28).** Registering a model switch
        used to live in exactly ONE caller — the settings route. Every other
        way the same value can legitimately change wrote the config and left
        the store pinned to the previous model: the activation route behind
        the Normal/Ultra switch, a voice-driven config change, a hand-edited
        ``jarvis.toml``, a config carried over from another machine. The store
        then rejected every vector the configured provider produced, the embed
        lane failed 100 % of its work for days, and the surface still read
        "still filling up" — because nothing on the failing side ever compared
        the two values. A rule enforced in one caller is not enforced.

        So the reconciliation happens HERE, next to the pins it protects, and
        the pipeline calls it with the model it actually resolved. Cheap by
        design (two primary-key reads in the steady state) because it runs on
        every pass.

        Returns ``"active"`` (the model serves live search), ``"rebuilding"``
        (a rebuild into it is already under way), ``"started"`` (this call
        registered the switch) or ``"unknown"`` (no model to check).
        """
        model = str(model or "").strip()
        if not model:
            return "unknown"
        pinned_model, _pinned_dim = await self._pinned_space()
        # No pin yet: the first vector of an empty store defines the space.
        if pinned_model is None or pinned_model == model:
            return "active"
        pending_model, _pending_dim = await self._pending_space()
        if pending_model == model:
            return "rebuilding"
        # The configured model belongs to NEITHER space. This is the exact
        # state that used to brick the lane. `begin_reembed` is the sanctioned
        # switch: live vectors and the ANN index keep answering search
        # untouched while the new space is built alongside them.
        started = await self.begin_reembed(model)
        return "started" if started else "active"

    async def reembed_is_running(self) -> bool:
        """Is a model switch rebuilding the vector space right now?

        One primary-key read, because the pipeline asks once per pass to
        decide whether a stage should stand aside for the rebuild.
        """
        return await self.get_meta(META_PENDING_EMBED_MODEL) is not None

    async def reembed_status(self) -> dict[str, Any]:
        """Honest progress of a running rebuild, for the settings surface.

        ``{}`` when none is running. ``done``/``total`` count ITEMS — the ones
        :meth:`begin_reembed` flagged and the ones that have since been
        embedded again. See :meth:`_reembed_remaining` for why the obvious
        document-level measure cannot work.
        """
        pending_model, _pending_dim = await self._pending_space()
        if pending_model is None:
            return {}
        active_model, _active_dim = await self._pinned_space()
        conn = await self._ensure_open()
        remaining = await self._reembed_remaining(conn)
        total_raw = await self.get_meta(META_REEMBED_TOTAL)
        # A rebuild started before the counter existed reports its remaining
        # work rather than inventing a denominator it cannot know.
        total = int(total_raw) if total_raw else remaining
        return {
            "model": pending_model,
            "done": max(0, total - remaining),
            "total": total,
            "remaining": remaining,
            "active_model": active_model or "",
        }

    # -- search legs (SQL only, fusion lives in the read path) --------------

    async def keyword_search(
        self, query: str, k: int = 10, *, area_id: str | None = None
    ) -> list[SearchResult]:
        """FTS5 keyword leg over live items; bm25 normalized to [0, 1].

        Two phases on purpose: ``snippet()`` re-tokenizes the document body
        and dominated this leg's cost when computed for EVERY matching row
        (an OR query with a stopword matches half the corpus). Phase one
        ranks with bm25 alone — index statistics, no body access — and phase
        two renders snippets for the ``k`` winners only.
        """
        match_expr = _fts_match_expr(query)
        if not match_expr:
            return []
        conn = await self._read_conn()
        sql = (
            "SELECT uw_fts.rowid AS fts_rowid, uw_fts.item_id AS item_id,"
            " i.source_id AS source_id, i.title AS title,"
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
        if not rows:
            return []
        winner_rowids = [int(row["fts_rowid"]) for row in rows]
        snippet_rows = await self._fetchall(
            conn,
            "SELECT rowid AS fts_rowid,"  # noqa: S608 — placeholder marks only
            " snippet(uw_fts, 2, '', '', '…', 32) AS snip"
            " FROM uw_fts WHERE uw_fts MATCH ?"
            f" AND rowid IN ({_placeholders(len(winner_rowids))})",
            [match_expr, *winner_rowids],
        )
        snippets = {int(row["fts_rowid"]): row["snip"] for row in snippet_rows}
        return [
            SearchResult(
                item_id=int(row["item_id"]),
                source_id=row["source_id"],
                title=row["title"],
                snippet=snippets.get(int(row["fts_rowid"])) or "",
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
        """ANN leg via the derived ``uw_vec`` index — one hit per item.

        Returns ``(results, reason)`` — ``reason`` is an empty string on the
        healthy path and an honest English explanation whenever the vector
        leg is degraded (extension unavailable, nothing embedded yet, or a
        query-vector/pin mismatch).

        Each hit carries the passage that actually matched (``document_id`` /
        ``chunk_index`` / ``char_start`` / ``char_end``), so a caller can
        locate it inside a 200 KB file instead of being told only which file
        it was.
        """
        return await self.vector_search_passages(
            query_vector, k, area_id=area_id, per_item=1
        )

    async def vector_search_passages(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        *,
        area_id: str | None = None,
        per_item: int = 1,
    ) -> tuple[list[SearchResult], str]:
        """The ANN leg at PASSAGE granularity.

        ``per_item`` is how many passages of one item may appear. ``1`` is the
        classic behaviour (:meth:`vector_search`): one vote per item, which is
        what fusion wants, because five passages of one file would otherwise
        stack five RRF contributions against everyone else's one. A word
        search asks for more, because "where in this document" is precisely
        the question it is answering.
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
        conn = await self._vec_ready_conn(await self._read_conn())
        cap = max(1, int(per_item))
        # Widen the KNN pool with the per-item allowance: asking for k rows
        # when up to `cap` of them may belong to one item would return fewer
        # distinct items the moment a single document dominates.
        fetch_k = min(max(int(k) * 4 * cap, int(k) + 8), 400)
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
                " d.chunk_index AS chunk_index, d.char_start AS char_start,"
                " d.char_end AS char_end,"
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
        per_item_seen: dict[int, int] = {}
        for distance, row in joined:
            item_id = int(row["item_id"])
            taken = per_item_seen.get(item_id, 0)
            if taken >= cap:
                continue
            per_item_seen[item_id] = taken + 1
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
                    document_id=int(row["doc_id"]),
                    chunk_index=int(row["chunk_index"] or 0),
                    char_start=int(row["char_start"] or 0),
                    char_end=int(row["char_end"] or 0),
                )
            )
            if len(results) >= int(k):
                break
        return results, ""

    # -- ranking signals -----------------------------------------------------

    async def live_item_count(self) -> int:
        """Live (non-deleted) item count — the ``N`` of the IDF formula.

        Cached until the next committed write (see :meth:`_txn`): the count
        is asked on every search and only moves when a write commits.
        """
        if self._live_count_cache is not None:
            return self._live_count_cache
        conn = await self._read_conn()
        row = await self._fetchone(
            conn, "SELECT count(*) AS n FROM uw_items WHERE deleted_at IS NULL", ()
        )
        count = int(row["n"]) if row else 0
        self._live_count_cache = count
        return count

    async def term_document_frequency(self, terms: Sequence[str]) -> dict[str, int]:
        """In how many live items does each term occur? (the ``df`` of IDF)

        One FTS-index-only count per distinct term — deliberately WITHOUT a
        join onto ``uw_items``. The join is redundant: tombstoning purges an
        item's FTS row in the same transaction (:meth:`_purge_derived`), so
        ``uw_fts`` only ever holds live items — while the join forced a row
        lookup per posting and made this probe ~450x more expensive (328 ms
        -> 0.7 ms measured on a 15k-item store). Do not re-add it; if the
        purge-on-tombstone invariant ever changes, ``df`` merely drifts high
        for a while, and the term-rarity factor is floored so it can never
        drop a candidate.

        Unindexable tokens report 0, which the caller reads as "maximally
        rare" only after the count is compared against the corpus size.
        """
        conn = await self._read_conn()
        frequencies: dict[str, int] = {}
        for term in dict.fromkeys(terms):
            match_expr = _fts_match_expr(term)
            if not match_expr:
                frequencies[term] = 0
                continue
            row = await self._fetchone(
                conn,
                "SELECT count(*) AS n FROM uw_fts WHERE uw_fts MATCH ?",
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
        conn = await self._read_conn()
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

    async def record_sync_outcome(
        self,
        source_id: str,
        *,
        status: str,
        mode: str,
        finished_at: str,
        new: int = 0,
        changed: int = 0,
        unchanged: int = 0,
        tombstoned: int = 0,
    ) -> None:
        """Persist what the last finished sync of *source_id* actually did.

        The live job registry is in-memory and empty after a restart, so
        without this the card falls back to "Approved / Never synced" for a
        source that holds thousands of imported items. Written as one block —
        a half-written outcome would be worse than none.
        """
        async with self._txn() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO uw_sync_state (source_id) VALUES (?)",
                (source_id,),
            )
            await conn.execute(
                "UPDATE uw_sync_state SET last_outcome_at = ?,"
                " last_outcome_status = ?, last_outcome_mode = ?, last_new = ?,"
                " last_changed = ?, last_unchanged = ?, last_tombstoned = ?"
                " WHERE source_id = ?",
                (
                    finished_at,
                    status,
                    mode,
                    int(new),
                    int(changed),
                    int(unchanged),
                    int(tombstoned),
                    source_id,
                ),
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


class PostgresStore(IdentityMixin, EventMixin, LexiconMixin):
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

    #: The identity layer writes its SQL once, in the SQLite dialect;
    #: :meth:`IdentityMixin._id_sql` rewrites the placeholders for psycopg.
    _IDENTITY_PARAM = "%s"

    #: The event keyword leg is the one thing the engines cannot share: a
    #: generated ``tsvector`` column here, an FTS5 side table on SQLite.
    _EVENT_DIALECT = "postgres"

    #: Nearest-vector search over the word lexicon: a derived pgvector table
    #: here, a ``vec_distance_cosine`` scan over the stored BLOBs on SQLite.
    _LEXICON_DIALECT = "postgres"

    def __init__(self, conn_str: str) -> None:
        self._conn_str = conn_str
        self._conn: Any = None
        self._lock = asyncio.Lock()
        self._vec_state: tuple[bool, str] | None = None
        self._vec_dim: int | None = None
        self._legacy_tombstone_repair_done = False

    async def _id_insert(self, conn: Any, sql: str, params: Sequence[Any]) -> int:
        """Identity-layer INSERT hook: Postgres reports the id via RETURNING."""
        cur = await conn.execute(f"{sql} RETURNING id", params)
        row = await cur.fetchone()
        return int(row["id"])

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
        entity_kinds = ", ".join(f"'{kind.value}'" for kind in EntityKind)
        identifier_kinds = ", ".join(f"'{kind.value}'" for kind in IdentifierKind)
        queue_states = ", ".join(f"'{status.value}'" for status in QueueStatus)
        merge_tiers = ", ".join(
            f"'{tier.value}'" for tier in sorted(MERGEABLE_TIERS, key=str)
        )
        event_kinds = ", ".join(f"'{value}'" for value in EVENT_KIND_VALUES)
        precisions = ", ".join(f"'{value}'" for value in TIME_PRECISION_VALUES)
        anchors = ", ".join(f"'{value}'" for value in TIME_ANCHOR_VALUES)
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
            " created_at TEXT NOT NULL, last_sync_at TEXT, last_error TEXT,"
            " last_notice TEXT)",
            "CREATE TABLE IF NOT EXISTS uw_sync_state ("
            " source_id TEXT PRIMARY KEY"
            "  REFERENCES uw_sources(id) ON DELETE CASCADE,"
            " cursor TEXT, backfill_checkpoint TEXT,"
            " backfill_complete_at TEXT, last_success_at TEXT,"
            " last_outcome_at TEXT, last_outcome_status TEXT,"
            " last_outcome_mode TEXT,"
            " last_new INTEGER NOT NULL DEFAULT 0,"
            " last_changed INTEGER NOT NULL DEFAULT 0,"
            " last_unchanged INTEGER NOT NULL DEFAULT 0,"
            " last_tombstoned INTEGER NOT NULL DEFAULT 0)",
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
            " metadata_json TEXT NOT NULL DEFAULT '{}',"
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
            " document_id BIGINT NOT NULL"
            "  REFERENCES uw_documents(id) ON DELETE CASCADE,"
            " model TEXT NOT NULL, dim INTEGER NOT NULL,"
            " vector BYTEA NOT NULL, created_at TEXT NOT NULL,"
            " PRIMARY KEY (document_id, model, dim))",
            # Databases created before the shadow-space key: widen the primary
            # key in place so a model switch can build the new space alongside
            # the live one (SQLite does the same via migration 0002).
            "DO $$ BEGIN"
            "  IF EXISTS (SELECT 1 FROM pg_constraint"
            "             WHERE conrelid = to_regclass('uw_embeddings')"
            "               AND contype = 'p'"
            "               AND array_length(conkey, 1) = 1) THEN"
            "    ALTER TABLE uw_embeddings DROP CONSTRAINT uw_embeddings_pkey;"
            "    ALTER TABLE uw_embeddings"
            "      ADD PRIMARY KEY (document_id, model, dim);"
            "  END IF;"
            "END $$",
            "CREATE INDEX IF NOT EXISTS idx_uw_embeddings_space"
            " ON uw_embeddings(model, dim)",
            # Word lexicon (jarvis/ultrawiki/lexicon_store.py) — the Postgres
            # twin of migrations/0005_term_lexicon.sql. The pgvector twin of
            # the SQLite scan (uw_term_vec) is DERIVED lazily on first use,
            # exactly like uw_vec, so a server without the extension still
            # accumulates term vectors and gains word neighbours later.
            "CREATE TABLE IF NOT EXISTS uw_terms ("
            " id BIGSERIAL PRIMARY KEY, term TEXT NOT NULL UNIQUE,"
            " doc_freq INTEGER NOT NULL DEFAULT 0,"
            " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_uw_terms_freq"
            " ON uw_terms(doc_freq DESC, id)",
            "CREATE TABLE IF NOT EXISTS uw_term_embeddings ("
            " term_id BIGINT NOT NULL REFERENCES uw_terms(id) ON DELETE CASCADE,"
            " model TEXT NOT NULL, dim INTEGER NOT NULL,"
            " vector BYTEA NOT NULL, created_at TEXT NOT NULL,"
            " PRIMARY KEY (term_id, model, dim))",
            "CREATE INDEX IF NOT EXISTS idx_uw_term_embeddings_space"
            " ON uw_term_embeddings(model, dim)",
            "CREATE TABLE IF NOT EXISTS uw_distill_cache ("
            " content_hash TEXT NOT NULL, prompt_version INTEGER NOT NULL,"
            " model TEXT NOT NULL, result_json TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " PRIMARY KEY (content_hash, prompt_version, model))",
            # Identity layer (design doc 05 · D-10) — the Postgres twin of
            # migrations/0003_identity.sql. Same tables, same constraints, same
            # partial indexes; only the key types differ.
            "CREATE TABLE IF NOT EXISTS uw_entities ("
            " id BIGSERIAL PRIMARY KEY,"
            " kind TEXT NOT NULL DEFAULT 'person'"
            f" CHECK (kind IN ({entity_kinds})),"
            " display_name TEXT NOT NULL,"
            " canonical_key TEXT NOT NULL DEFAULT '',"
            " merged_into BIGINT REFERENCES uw_entities(id) ON DELETE SET NULL,"
            " source_ref TEXT NOT NULL DEFAULT '',"
            " profile_json TEXT NOT NULL DEFAULT '{}',"
            " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_uw_entities_live"
            " ON uw_entities(merged_into, kind)",
            "CREATE INDEX IF NOT EXISTS idx_uw_entities_key"
            " ON uw_entities(canonical_key)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_uw_entities_source_ref"
            " ON uw_entities(source_ref) WHERE source_ref != ''",
            "CREATE TABLE IF NOT EXISTS uw_identifiers ("
            " id BIGSERIAL PRIMARY KEY,"
            " entity_id BIGINT NOT NULL"
            "  REFERENCES uw_entities(id) ON DELETE CASCADE,"
            f" kind TEXT NOT NULL CHECK (kind IN ({identifier_kinds})),"
            " value TEXT NOT NULL, display_value TEXT NOT NULL DEFAULT '',"
            " value_len INTEGER NOT NULL DEFAULT 0,"
            " source_ref TEXT NOT NULL DEFAULT '',"
            " created_at TEXT NOT NULL)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_uw_identifiers_unique"
            " ON uw_identifiers(entity_id, kind, value)",
            "CREATE INDEX IF NOT EXISTS idx_uw_identifiers_value"
            " ON uw_identifiers(kind, value)",
            "CREATE INDEX IF NOT EXISTS idx_uw_identifiers_len"
            " ON uw_identifiers(kind, value_len)",
            "CREATE INDEX IF NOT EXISTS idx_uw_identifiers_entity"
            " ON uw_identifiers(entity_id)",
            "CREATE TABLE IF NOT EXISTS uw_confirm_queue ("
            " id BIGSERIAL PRIMARY KEY, pair_key TEXT NOT NULL UNIQUE,"
            " left_entity_id BIGINT NOT NULL"
            "  REFERENCES uw_entities(id) ON DELETE CASCADE,"
            " right_entity_id BIGINT NOT NULL"
            "  REFERENCES uw_entities(id) ON DELETE CASCADE,"
            " status TEXT NOT NULL DEFAULT 'pending'"
            f" CHECK (status IN ({queue_states})),"
            " score DOUBLE PRECISION NOT NULL DEFAULT 0,"
            " evidence_json TEXT NOT NULL DEFAULT '[]',"
            " created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
            " decided_at TEXT, decided_by TEXT)",
            "CREATE INDEX IF NOT EXISTS idx_uw_confirm_queue_status"
            " ON uw_confirm_queue(status, score DESC, id)",
            "CREATE TABLE IF NOT EXISTS uw_merge_log ("
            " id BIGSERIAL PRIMARY KEY,"
            " winner_id BIGINT NOT NULL, loser_id BIGINT NOT NULL,"
            f" tier TEXT NOT NULL CHECK (tier IN ({merge_tiers})),"
            " reason TEXT NOT NULL DEFAULT '',"
            " evidence_json TEXT NOT NULL DEFAULT '[]',"
            " undo_json TEXT NOT NULL DEFAULT '{}',"
            " queue_id BIGINT, merged_at TEXT NOT NULL, undone_at TEXT)",
            "CREATE INDEX IF NOT EXISTS idx_uw_merge_log_winner"
            " ON uw_merge_log(winner_id, undone_at)",
            "CREATE INDEX IF NOT EXISTS idx_uw_merge_log_loser"
            " ON uw_merge_log(loser_id, undone_at)",
            # Episodic events (design doc 01 · uw_events) — the Postgres twin
            # of migrations/0004_events.sql. Same columns, same CHECK lists
            # (derived from jarvis/ultrawiki/events.py), same overlap-friendly
            # occurred_at/occurred_end pair; the FTS5 side table is replaced by
            # a generated tsvector on the stored card, so both engines index
            # the identical text.
            "CREATE TABLE IF NOT EXISTS uw_events ("
            " id BIGSERIAL PRIMARY KEY,"
            " item_id BIGINT NOT NULL REFERENCES uw_items(id) ON DELETE CASCADE,"
            " kind TEXT NOT NULL DEFAULT 'other'"
            f" CHECK (kind IN ({event_kinds})),"
            " title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',"
            " occurred_at TEXT NOT NULL, occurred_end TEXT NOT NULL,"
            " occurred_precision TEXT NOT NULL DEFAULT 'day'"
            f" CHECK (occurred_precision IN ({precisions})),"
            " time_anchor TEXT NOT NULL DEFAULT 'recorded'"
            f" CHECK (time_anchor IN ({anchors})),"
            " recorded_at TEXT NOT NULL,"
            " place_entity_id BIGINT REFERENCES uw_entities(id) ON DELETE SET NULL,"
            " place_raw TEXT NOT NULL DEFAULT '',"
            " confidence DOUBLE PRECISION NOT NULL DEFAULT 0,"
            " extraction_version INTEGER NOT NULL DEFAULT 0,"
            " dedupe_key TEXT NOT NULL DEFAULT '',"
            " evidence_json TEXT NOT NULL DEFAULT '[]',"
            " search_text TEXT NOT NULL DEFAULT '',"
            " created_at TEXT NOT NULL,"
            " search_tsv tsvector GENERATED ALWAYS AS"
            "  (to_tsvector('simple',"
            "   coalesce(title, '') || ' ' || coalesce(search_text, ''))) STORED)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_uw_events_dedupe"
            " ON uw_events(item_id, dedupe_key)",
            "CREATE INDEX IF NOT EXISTS idx_uw_events_occurred"
            " ON uw_events(occurred_at, id)",
            "CREATE INDEX IF NOT EXISTS idx_uw_events_kind_time"
            " ON uw_events(kind, occurred_at)",
            "CREATE INDEX IF NOT EXISTS idx_uw_events_item ON uw_events(item_id)",
            "CREATE INDEX IF NOT EXISTS idx_uw_events_recorded"
            " ON uw_events(recorded_at)",
            "CREATE INDEX IF NOT EXISTS idx_uw_events_place"
            " ON uw_events(place_entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_uw_events_tsv"
            " ON uw_events USING GIN (search_tsv)",
            "CREATE TABLE IF NOT EXISTS uw_event_participants ("
            " id BIGSERIAL PRIMARY KEY,"
            " event_id BIGINT NOT NULL REFERENCES uw_events(id) ON DELETE CASCADE,"
            " entity_id BIGINT REFERENCES uw_entities(id) ON DELETE SET NULL,"
            " display_name TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX IF NOT EXISTS idx_uw_event_participants_event"
            " ON uw_event_participants(event_id)",
            "CREATE INDEX IF NOT EXISTS idx_uw_event_participants_entity"
            " ON uw_event_participants(entity_id)",
            # The same additive columns for databases created before the
            # feature that introduced them. Postgres HAS `ADD COLUMN IF NOT
            # EXISTS`, so the SQLite pragma dance is unnecessary here — but the
            # statements must run AFTER every CREATE TABLE, or a fresh database
            # alters a table that does not exist yet.
            *(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {declaration}"
                for table, column, declaration in _ADDITIVE_COLUMNS
            ),
            *_ADDITIVE_INDEXES,
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
                await self._adopt_running_reembed(conn)
                await self._repair_unclaimable_reembed(conn)
            self._conn = conn

    @staticmethod
    async def _adopt_running_reembed(conn: Any) -> None:
        """Postgres twin of :meth:`UltraStore._adopt_running_reembed`."""
        cur = await conn.execute(
            "SELECT key, value FROM uw_meta WHERE key IN (%s, %s, %s)",
            (META_PENDING_EMBED_MODEL, META_EMBED_MODEL, META_REEMBED_TOTAL),
        )
        meta = {str(row["key"]): str(row["value"]) for row in await cur.fetchall()}
        if META_PENDING_EMBED_MODEL not in meta or META_REEMBED_TOTAL in meta:
            return
        active_model = meta.get(META_EMBED_MODEL)
        if not active_model:
            return
        cur = await conn.execute(
            "UPDATE uw_items SET reembed_pending = 1"
            " WHERE deleted_at IS NULL AND state != %s"
            "   AND EXISTS (SELECT 1 FROM uw_documents d"
            "               JOIN uw_embeddings e ON e.document_id = d.id"
            "               WHERE d.item_id = uw_items.id AND e.model = %s)",
            (ItemState.FAILED.value, active_model),
        )
        flagged = max(0, int(getattr(cur, "rowcount", 0) or 0))
        await conn.execute(
            "INSERT INTO uw_meta (key, value) VALUES (%s, %s)"
            " ON CONFLICT (key) DO UPDATE SET value = %s",
            (META_REEMBED_TOTAL, str(flagged), str(flagged)),
        )
        log.info(
            "UltraWiki: adopted a rebuild that was already running — %d item(s) "
            "moved to the front of the embed queue",
            flagged,
        )

    @staticmethod
    async def _repair_unclaimable_reembed(conn: Any) -> None:
        """Postgres twin of :meth:`UltraStore._repair_unclaimable_reembed`."""
        await conn.execute(
            "UPDATE uw_items SET state = %s, updated_at = %s"
            " WHERE reembed_pending = 1 AND deleted_at IS NULL"
            "   AND state IN (%s, %s)",
            (
                ItemState.KEYWORD_INDEXED.value,
                _iso_utc(),
                ItemState.EMBEDDED.value,
                ItemState.DISTILLED.value,
            ),
        )

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
            "last_notice": row.get("last_notice"),
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
        last_notice: str | None = _UNSET,
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
        if last_notice is not _UNSET:
            await conn.execute(
                "UPDATE uw_sources SET last_notice = %s WHERE id = %s",
                (last_notice, source_id),
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
                        await self._clear_deleted_payload(conn, [row["id"]])
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
                        "  content_hash, state, metadata_json, created_at, updated_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                        " %s, %s, %s)",
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
                            _metadata_json(item),
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
                        " state = %s, metadata_json = %s, attempt_count = 0,"
                        " next_retry_at = NULL, last_error = NULL,"
                        " deleted_at = NULL, updated_at = %s"
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
                            _metadata_json(item),
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
        row automatically, documents/embeddings/event participants cascade;
        only pgvector rows need an explicit delete when the index is live."""
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
        await conn.execute("DELETE FROM uw_events WHERE item_id = ANY(%s)", (ids,))

    async def _clear_deleted_payload(
        self, conn: Any, item_ids: Sequence[int]
    ) -> None:
        ids = list(item_ids)
        if not ids:
            return
        await conn.execute(
            "UPDATE uw_items SET thread_key = '', author_raw = '', title = '',"
            " body_raw = '', permalink = '', timestamp_utc = '',"
            " areas_json = '[]', metadata_json = '{}', attempt_count = 0,"
            " next_retry_at = NULL, last_error = NULL, reembed_pending = 0"
            " WHERE id = ANY(%s)",
            (ids,),
        )

    async def repair_legacy_tombstones(self, *, limit: int = 5000) -> int:
        """Postgres twin of the bounded legacy-tombstone repair."""
        if self._legacy_tombstone_repair_done:
            return 0
        batch_limit = min(10_000, max(1, int(limit)))
        async with self._txn() as conn:
            rows = await self._fetchall(
                conn,
                "SELECT id FROM uw_items WHERE deleted_at IS NOT NULL AND ("
                " thread_key != '' OR author_raw != '' OR title != '' OR"
                " body_raw != '' OR permalink != '' OR timestamp_utc != '' OR"
                " areas_json != '[]' OR metadata_json != '{}' OR"
                " attempt_count != 0 OR next_retry_at IS NOT NULL OR"
                " last_error IS NOT NULL OR reembed_pending != 0) LIMIT %s",
                (batch_limit,),
            )
            item_ids = [int(row["id"]) for row in rows]
            remaining = batch_limit - len(item_ids)
            if remaining:
                for table in ("uw_documents", "uw_events"):
                    extra = await self._fetchall(
                        conn,
                        f"SELECT DISTINCT d.item_id AS id FROM {table} d"  # noqa: S608
                        " JOIN uw_items i ON i.id = d.item_id"
                        " WHERE i.deleted_at IS NOT NULL LIMIT %s",
                        (remaining,),
                    )
                    known = set(item_ids)
                    item_ids.extend(
                        int(row["id"])
                        for row in extra
                        if int(row["id"]) not in known
                    )
                    remaining = batch_limit - len(item_ids)
                    if not remaining:
                        break
            if not item_ids:
                self._legacy_tombstone_repair_done = True
                return 0
            await self._purge_derived(conn, item_ids)
            await self._clear_deleted_payload(conn, item_ids)
        return len(item_ids)

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
            "metadata": _parse_metadata(row),
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

    async def item_documents(self, item_id: int) -> list[dict[str, Any]]:
        """Postgres twin of :meth:`UltraStore.item_documents`."""
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT d.id, d.doc_type, d.text_norm, d.distill_json,"
            " d.distill_version, d.created_at, d.chunk_index,"
            " d.char_start, d.char_end,"
            " EXISTS (SELECT 1 FROM uw_embeddings e WHERE e.document_id = d.id)"
            "   AS has_vector"
            " FROM uw_documents d WHERE d.item_id = %s"
            " ORDER BY d.doc_type, d.chunk_index, d.id",
            (item_id,),
        )
        return [
            {**dict(row), "has_vector": bool(dict(row).get("has_vector"))}
            for row in rows
        ]

    async def list_items(
        self,
        *,
        source_id: str | None = None,
        state: ItemState | str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Postgres twin of the SQLite inventory page (same ordering + total)."""
        coerced = _coerce_state(state).value if state else None
        where, params = _item_filter_sql(
            source_id=source_id,
            state=coerced,
            include_deleted=include_deleted,
            mark="%s",
        )
        conn = await self._ensure_open()
        total_row = await self._fetchone(
            conn,
            f"SELECT count(*) AS n FROM uw_items{where}",  # noqa: S608 — placeholders only
            params,
        )
        total = int(total_row["n"]) if total_row is not None else 0
        rows = await self._fetchall(
            conn,
            f"SELECT {_ITEM_LIST_COLUMNS} FROM uw_items{where}"  # noqa: S608 — code-owned projection + placeholders
            " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
            [*params, max(0, int(limit)), max(0, int(offset))],
        )
        return [dict(row) for row in rows], total

    async def set_item_metadata(self, item_id: int, metadata: dict[str, Any]) -> None:
        """Replace one item's metadata without touching its content or state.

        Mirrors the SQLite implementation (see its docstring for why a side
        lane cannot record an outcome through ``upsert_items``).
        """
        async with self._txn() as conn:
            await conn.execute(
                "UPDATE uw_items SET metadata_json = %s WHERE id = %s",
                (json.dumps(metadata or {}, ensure_ascii=False, default=str), int(item_id)),
            )

    async def pending_media_items(self, *, limit: int = 8) -> list[dict[str, Any]]:
        """Media items still waiting to be described or transcribed.

        Mirrors the SQLite implementation exactly (see its docstring for why
        this is a metadata flag rather than a fifth pipeline state).
        """
        conn = await self._ensure_open()
        rows = await self._fetchall(
            conn,
            "SELECT * FROM uw_items"
            " WHERE deleted_at IS NULL AND metadata_json LIKE '%%enrich_pending%%'"
            " ORDER BY id ASC LIMIT %s",
            (max(1, int(limit)) * 4,),
        )
        return _media_pending_only([self._item_row_to_dict(row) for row in rows], limit)

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
            " ORDER BY reembed_pending DESC, timestamp_utc DESC, id DESC LIMIT %s",
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
        # See the SQLite twin: reaching `embedded` is what releases the item
        # from the re-embed priority lane.
        clear_reembed = ", reembed_pending = 0" if state is ItemState.EMBEDDED else ""
        sql = (
            "UPDATE uw_items SET state = %s, attempt_count = 0,"  # noqa: S608 — clear_reembed is one of two code-owned literals
            " next_retry_at = NULL, last_error = NULL, updated_at = %s"
            f"{clear_reembed}"
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

    async def distilled_rows(self) -> list[dict[str, Any]]:
        """Input rows of the readable projection (see ``_DISTILLED_ROWS_SQL``)."""
        conn = await self._ensure_open()
        rows = await self._fetchall(conn, _DISTILLED_ROWS_SQL)
        return [dict(row) for row in rows]

    async def distilled_fingerprint(self) -> tuple[int, int, str]:
        """Change stamp of the projection input, for caching the projection."""
        conn = await self._ensure_open()
        row = await self._fetchone(conn, _DISTILLED_FINGERPRINT_SQL)
        if row is None:
            return (0, 0, "")
        data = dict(row)
        return (int(data["n"]), int(data["max_id"]), str(data["newest"]))

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
                await self._clear_deleted_payload(conn, doomed)
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

    async def replace_documents(
        self,
        item_id: int,
        doc_type: DocType | str,
        chunks: Sequence[Any],
        *,
        content_hash: str = "",
    ) -> list[int]:
        """Postgres twin of :meth:`UltraStore.replace_documents` — including
        the keep-identical-passages rule that protects the live vectors of a
        running rebuild (see :func:`_documents_unchanged`)."""
        kind = DocType(doc_type).value
        now = _iso_utc()
        async with self._txn() as conn:
            old_rows = await self._fetchall(
                conn,
                "SELECT id, chunk_index, text_norm, content_hash FROM uw_documents"
                " WHERE item_id = %s AND doc_type = %s",
                (item_id, kind),
            )
            if _documents_unchanged(old_rows, chunks, content_hash):
                by_index = {int(row["chunk_index"]): int(row["id"]) for row in old_rows}
                return [by_index[int(getattr(c, "index", 0))] for c in chunks]
            await conn.execute(
                "DELETE FROM uw_documents WHERE item_id = %s AND doc_type = %s",
                (item_id, kind),
            )
            new_ids: list[int] = []
            for chunk in chunks:
                cur = await conn.execute(
                    "INSERT INTO uw_documents (item_id, doc_type, text_norm,"
                    " distill_json, distill_version, content_hash, created_at,"
                    " chunk_index, char_start, char_end)"
                    " VALUES (%s, %s, %s, NULL, 0, %s, %s, %s, %s, %s)"
                    " RETURNING id",
                    (
                        item_id,
                        kind,
                        str(getattr(chunk, "text", "")),
                        content_hash,
                        now,
                        int(getattr(chunk, "index", 0)),
                        int(getattr(chunk, "char_start", 0)),
                        int(getattr(chunk, "char_end", 0)),
                    ),
                )
                row = await cur.fetchone()
                assert row is not None
                new_ids.append(int(row[0]))
            return new_ids

    async def _pinned_space(self) -> tuple[str | None, int | None]:
        model = await self.get_meta(META_EMBED_MODEL)
        dim_raw = await self.get_meta(META_EMBED_DIM)
        return model, int(dim_raw) if dim_raw else None

    async def _pending_space(self) -> tuple[str | None, int | None]:
        model = await self.get_meta(META_PENDING_EMBED_MODEL)
        dim_raw = await self.get_meta(META_PENDING_EMBED_DIM)
        return model, int(dim_raw) if dim_raw else None

    async def _writes_to_active_space(self, model: str, dim: int) -> bool:
        """Postgres twin of :meth:`UltraStore._writes_to_active_space`."""
        pinned_model, pinned_dim = await self._pinned_space()
        if pinned_model is None or pinned_dim is None:
            await self.set_meta(META_EMBED_MODEL, model)
            await self.set_meta(META_EMBED_DIM, str(dim))
            return True
        if pinned_model == model and pinned_dim == dim:
            return True
        pending_model, pending_dim = await self._pending_space()
        if pending_model is not None and pending_model == model:
            if pending_dim is None:
                await self.set_meta(META_PENDING_EMBED_DIM, str(dim))
            elif pending_dim != dim:
                raise UltraStoreError(
                    f"embedding dimension mismatch: the rebuild of {model!r} "
                    f"started at dim={pending_dim} but got dim={dim}"
                )
            return False
        if pinned_model == model:
            log.warning(
                "UltraWiki: model %r now answers with dim=%d instead of %d — "
                "rebuilding the vector space in the background",
                model,
                dim,
                pinned_dim,
            )
            await self.begin_reembed(model, dim=dim)
            return False
        raise EmbeddingSpaceMismatch(
            "embedding space mismatch: the store is pinned to "
            f"model={pinned_model!r} dim={pinned_dim} but got "
            f"model={model!r} dim={dim}. Changing the embedding model goes "
            "through begin_reembed() (rebuilds the corpus in the background "
            "while search keeps using the current vectors)."
        )

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
        active = await self._writes_to_active_space(model, dim)
        vec_ok = False
        if active:
            vec_ok, _ = await self._ensure_vec(dim)
        now = _iso_utc()
        async with self._txn() as conn:
            await conn.execute(
                "INSERT INTO uw_embeddings"
                " (document_id, model, dim, vector, created_at)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (document_id, model, dim) DO UPDATE"
                " SET vector = %s, created_at = %s",
                (
                    document_id,
                    model,
                    dim,
                    pack_vector(vector),
                    now,
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
        # Backfill vectors stored while the index was unavailable — from the
        # ACTIVE space only, never a shadow rebuild's partial one.
        active_model, _ = await self._pinned_space()
        await conn.execute(
            "DELETE FROM uw_vec WHERE document_id NOT IN"
            " (SELECT document_id FROM uw_embeddings"
            "  WHERE model = %s AND dim = %s)",
            (active_model or "", int(dim)),
        )
        missing = await self._fetchall(
            conn,
            "SELECT document_id, vector FROM uw_embeddings"
            " WHERE model = %s AND dim = %s"
            " AND document_id NOT IN (SELECT document_id FROM uw_vec)",
            (active_model or "", int(dim)),
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
                "DELETE FROM uw_meta WHERE key IN (%s, %s, %s, %s, %s)",
                (
                    META_EMBED_MODEL,
                    META_EMBED_DIM,
                    META_PENDING_EMBED_MODEL,
                    META_PENDING_EMBED_DIM,
                    META_REEMBED_TOTAL,
                ),
            )
            await conn.execute(
                "UPDATE uw_items SET state = %s, reembed_pending = 0, updated_at = %s"
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

    async def begin_reembed(self, model: str, *, dim: int | None = None) -> bool:
        """Postgres twin of :meth:`UltraStore.begin_reembed` — including its
        idempotency: a second call for the same target keeps the running
        rebuild instead of restarting it."""
        pinned_model, pinned_dim = await self._pinned_space()
        same_space = pinned_model == model and (dim is None or pinned_dim == dim)
        if pinned_model is None or pinned_dim is None or same_space:
            await self.abort_reembed()
            return False
        pending_model, pending_dim = await self._pending_space()
        if pending_model == model and (
            dim is None or pending_dim is None or pending_dim == dim
        ):
            log.info(
                "UltraWiki: a rebuild into %r is already running — keeping its "
                "progress instead of restarting it",
                model,
            )
            return True
        async with self._txn() as conn:
            await conn.execute(
                "DELETE FROM uw_embeddings WHERE model != %s OR dim != %s",
                (pinned_model, pinned_dim),
            )
            await conn.execute(
                "INSERT INTO uw_meta (key, value) VALUES (%s, %s)"
                " ON CONFLICT (key) DO UPDATE SET value = %s",
                (META_PENDING_EMBED_MODEL, model, model),
            )
            if dim is None:
                await conn.execute(
                    "DELETE FROM uw_meta WHERE key = %s", (META_PENDING_EMBED_DIM,)
                )
            else:
                await conn.execute(
                    "INSERT INTO uw_meta (key, value) VALUES (%s, %s)"
                    " ON CONFLICT (key) DO UPDATE SET value = %s",
                    (META_PENDING_EMBED_DIM, str(int(dim)), str(int(dim))),
                )
            await conn.execute(
                "UPDATE uw_items SET reembed_pending = 0 WHERE reembed_pending != 0"
            )
            cur = await conn.execute(
                "UPDATE uw_items SET reembed_pending = 1"
                " WHERE deleted_at IS NULL AND state != %s"
                "   AND (state IN (%s, %s)"
                "        OR EXISTS (SELECT 1 FROM uw_documents d"
                "                   JOIN uw_embeddings e ON e.document_id = d.id"
                "                   WHERE d.item_id = uw_items.id"
                "                     AND e.model = %s AND e.dim = %s))",
                (
                    ItemState.FAILED.value,
                    ItemState.EMBEDDED.value,
                    ItemState.DISTILLED.value,
                    pinned_model,
                    pinned_dim,
                ),
            )
            flagged = max(0, int(getattr(cur, "rowcount", 0) or 0))
            await conn.execute(
                "UPDATE uw_items SET state = %s, updated_at = %s"
                " WHERE state IN (%s, %s) AND deleted_at IS NULL",
                (
                    ItemState.KEYWORD_INDEXED.value,
                    _iso_utc(),
                    ItemState.EMBEDDED.value,
                    ItemState.DISTILLED.value,
                ),
            )
            await conn.execute(
                "INSERT INTO uw_meta (key, value) VALUES (%s, %s)"
                " ON CONFLICT (key) DO UPDATE SET value = %s",
                (META_REEMBED_TOTAL, str(flagged), str(flagged)),
            )
        log.info(
            "UltraWiki: rebuilding the vector space in %r — %d item(s) moved to "
            "the front of the embed queue",
            model,
            flagged,
        )
        return True

    async def abort_reembed(self) -> bool:
        """Postgres twin of :meth:`UltraStore.abort_reembed`."""
        pending_model, _ = await self._pending_space()
        if pending_model is None:
            return False
        pinned_model, pinned_dim = await self._pinned_space()
        async with self._txn() as conn:
            if pinned_model is not None and pinned_dim is not None:
                await conn.execute(
                    "DELETE FROM uw_embeddings WHERE model != %s OR dim != %s",
                    (pinned_model, pinned_dim),
                )
            await conn.execute(
                "DELETE FROM uw_meta WHERE key IN (%s, %s, %s)",
                (
                    META_PENDING_EMBED_MODEL,
                    META_PENDING_EMBED_DIM,
                    META_REEMBED_TOTAL,
                ),
            )
            await conn.execute(
                "UPDATE uw_items SET reembed_pending = 0 WHERE reembed_pending != 0"
            )
        return True

    async def _reembed_remaining(self, conn: Any) -> int:
        """Postgres twin of :meth:`UltraStore._reembed_remaining`."""
        row = await self._fetchone(
            conn,
            "SELECT count(*) AS n FROM uw_items"
            " WHERE reembed_pending = 1 AND deleted_at IS NULL AND state != %s",
            (ItemState.FAILED.value,),
        )
        return 0 if row is None else int(row["n"])

    async def promote_pending_space(self) -> bool:
        """Postgres twin of :meth:`UltraStore.promote_pending_space`."""
        pending_model, pending_dim = await self._pending_space()
        if pending_model is None or pending_dim is None:
            return False
        active_model, active_dim = await self._pinned_space()
        if active_model is None or active_dim is None:
            return False
        conn = await self._ensure_open()
        if await self._reembed_remaining(conn):
            return False
        async with self._txn() as txn:
            await txn.execute(
                "DELETE FROM uw_embeddings WHERE model = %s AND dim = %s",
                (active_model, active_dim),
            )
            await txn.execute(
                "INSERT INTO uw_meta (key, value) VALUES (%s, %s)"
                " ON CONFLICT (key) DO UPDATE SET value = %s",
                (META_EMBED_MODEL, pending_model, pending_model),
            )
            await txn.execute(
                "INSERT INTO uw_meta (key, value) VALUES (%s, %s)"
                " ON CONFLICT (key) DO UPDATE SET value = %s",
                (META_EMBED_DIM, str(pending_dim), str(pending_dim)),
            )
            await txn.execute(
                "DELETE FROM uw_meta WHERE key IN (%s, %s, %s)",
                (
                    META_PENDING_EMBED_MODEL,
                    META_PENDING_EMBED_DIM,
                    META_REEMBED_TOTAL,
                ),
            )
            # Derived index: dropping it costs a local rebuild, not a re-embed.
            await txn.execute("DROP TABLE IF EXISTS uw_vec")
        self._vec_state = None
        self._vec_dim = None
        await self._ensure_vec(pending_dim)
        log.info(
            "UltraWiki embedding space promoted: %s (dim=%d) replaces %s",
            pending_model,
            pending_dim,
            active_model,
        )
        return True

    async def reconcile_space(self, model: str) -> str:
        """Postgres twin of :meth:`UltraStore.reconcile_space`."""
        model = str(model or "").strip()
        if not model:
            return "unknown"
        pinned_model, _pinned_dim = await self._pinned_space()
        if pinned_model is None or pinned_model == model:
            return "active"
        pending_model, _pending_dim = await self._pending_space()
        if pending_model == model:
            return "rebuilding"
        started = await self.begin_reembed(model)
        return "started" if started else "active"

    async def reembed_is_running(self) -> bool:
        """Postgres twin of :meth:`UltraStore.reembed_is_running`."""
        return await self.get_meta(META_PENDING_EMBED_MODEL) is not None

    async def reembed_status(self) -> dict[str, Any]:
        """Postgres twin of :meth:`UltraStore.reembed_status`."""
        pending_model, _pending_dim = await self._pending_space()
        if pending_model is None:
            return {}
        active_model, _active_dim = await self._pinned_space()
        conn = await self._ensure_open()
        remaining = await self._reembed_remaining(conn)
        total_raw = await self.get_meta(META_REEMBED_TOTAL)
        total = int(total_raw) if total_raw else remaining
        return {
            "model": pending_model,
            "done": max(0, total - remaining),
            "total": total,
            "remaining": remaining,
            "active_model": active_model or "",
        }

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
        return await self.vector_search_passages(
            query_vector, k, area_id=area_id, per_item=1
        )

    async def vector_search_passages(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        *,
        area_id: str | None = None,
        per_item: int = 1,
    ) -> tuple[list[SearchResult], str]:
        """Postgres twin of :meth:`UltraStore.vector_search_passages`."""
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
        cap = max(1, int(per_item))
        sql = (
            "SELECT d.id AS doc_id, d.item_id, i.source_id, i.title, d.text_norm,"
            " d.chunk_index, d.char_start, d.char_end,"
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
        params.append(min(max(int(k) * 4 * cap, int(k) + 8), 400))
        rows = await self._fetchall(conn, sql, params)
        results: list[SearchResult] = []
        per_item_seen: dict[int, int] = {}
        for row in rows:
            item_id = int(row["item_id"])
            taken = per_item_seen.get(item_id, 0)
            if taken >= cap:
                continue
            per_item_seen[item_id] = taken + 1
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
                    document_id=int(row["doc_id"]),
                    chunk_index=int(row["chunk_index"] or 0),
                    char_start=int(row["char_start"] or 0),
                    char_end=int(row["char_end"] or 0),
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

    async def record_sync_outcome(
        self,
        source_id: str,
        *,
        status: str,
        mode: str,
        finished_at: str,
        new: int = 0,
        changed: int = 0,
        unchanged: int = 0,
        tombstoned: int = 0,
    ) -> None:
        """Postgres twin of the SQLite outcome write (same one-block contract)."""
        async with self._txn() as conn:
            await conn.execute(
                "INSERT INTO uw_sync_state (source_id) VALUES (%s)"
                " ON CONFLICT (source_id) DO NOTHING",
                (source_id,),
            )
            await conn.execute(
                "UPDATE uw_sync_state SET last_outcome_at = %s,"
                " last_outcome_status = %s, last_outcome_mode = %s, last_new = %s,"
                " last_changed = %s, last_unchanged = %s, last_tombstoned = %s"
                " WHERE source_id = %s",
                (
                    finished_at,
                    status,
                    mode,
                    int(new),
                    int(changed),
                    int(unchanged),
                    int(tombstoned),
                    source_id,
                ),
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
    "META_PENDING_EMBED_DIM",
    "META_PENDING_EMBED_MODEL",
    "PG_CONNECT_TIMEOUT_S",
    "EmbeddingSpaceMismatch",
    "PostgresStore",
    "UltraStore",
    "UltraStoreError",
    "UpsertCounts",
    "pack_vector",
    "resolve_ultrawiki_db_path",
    "sanitize_conn_error",
    "unpack_vector",
]
