"""SQLite FTS5 wrapper for the doc full-text index.

Why FTS5: BM25 ranking and the ``snippet()`` function are built-in, no Algolia
account required, no frontend index bundle bloat. ~50-100 Markdown files in a
single-user app fit easily into one SQLite file (~500 KB).

A single-connection pattern is fine for SQLite (WAL mode + multi-thread). The
registry holds one instance; REST routes read through its methods.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .schema import Doc

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A search result from FTS5 with snippet and BM25 score."""
    slug: str
    title: str
    diataxis: str
    phase: str
    snippet: str
    score: float


class DocSearch:
    """SQLite FTS5 index over all doc bodies and headings.

    Not thread-safe by itself; all methods are wrapped with a
    ``threading.Lock`` because watchdog reloads can write from a foreign
    thread.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # ``check_same_thread=False`` because watchdog wants to upsert from a
        # different thread. We serialise access via the lock.
        self._conn = self._connect(self.db_path)
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        return sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None,
        )

    @staticmethod
    def _configure_connection(
        connection: sqlite3.Connection,
        *,
        use_wal: bool,
    ) -> None:
        if use_wal:
            connection.execute("PRAGMA journal_mode=WAL")
        else:
            connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_index USING fts5(
                slug UNINDEXED,
                title,
                diataxis UNINDEXED,
                phase UNINDEXED,
                tags,
                headings,
                body,
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )

    def _init_schema(self) -> None:
        with self._lock:
            self._configure_connection(self._conn, use_wal=True)

    # ------------------------------------------------------------------
    # Upsert / Delete
    # ------------------------------------------------------------------

    def upsert(self, doc: Doc) -> None:
        """Writes a doc into the index. Uses ``DELETE`` + ``INSERT`` because
        FTS5 does not support native ``UPSERT`` on virtual tables."""
        slug = doc.frontmatter.slug
        title = doc.frontmatter.title
        diataxis = doc.frontmatter.diataxis.value
        phase = doc.frontmatter.phase
        tags = " ".join(doc.frontmatter.tags)
        headings = " ".join(text for _level, text, _slug in doc.headings)
        with self._lock:
            self._conn.execute(
                "DELETE FROM docs_index WHERE slug = ?", (slug,),
            )
            self._conn.execute(
                """
                INSERT INTO docs_index
                    (slug, title, diataxis, phase, tags, headings, body)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (slug, title, diataxis, phase, tags, headings, doc.body),
            )

    def delete(self, slug: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM docs_index WHERE slug = ?", (slug,))

    def replace_all(self, docs: list[Doc]) -> None:
        """Atomically replace the index with a newly built SQLite file.

        Deleting FTS rows leaves their previous pages recoverable in SQLite's
        free list and WAL. Building a fresh file prevents retired engineering
        docs or local paths from surviving a scope reduction in raw bytes.
        """
        rows = [
            (
                doc.frontmatter.slug,
                doc.frontmatter.title,
                doc.frontmatter.diataxis.value,
                doc.frontmatter.phase,
                " ".join(doc.frontmatter.tags),
                " ".join(text for _level, text, _slug in doc.headings),
                doc.body,
            )
            for doc in docs
        ]
        with self._lock:
            descriptor, raw_temp_path = tempfile.mkstemp(
                prefix=f".{self.db_path.name}.",
                suffix=".tmp",
                dir=self.db_path.parent,
            )
            os.close(descriptor)
            temp_path = Path(raw_temp_path)
            temp_connection: sqlite3.Connection | None = None
            current_connection_closed = False
            try:
                temp_connection = self._connect(temp_path)
                # DELETE mode keeps the completed replacement self-contained;
                # no temporary WAL can be orphaned during the rename.
                self._configure_connection(temp_connection, use_wal=False)
                temp_connection.execute("BEGIN IMMEDIATE")
                temp_connection.executemany(
                    """
                    INSERT INTO docs_index
                        (slug, title, diataxis, phase, tags, headings, body)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                temp_connection.execute("COMMIT")
                temp_connection.close()
                temp_connection = None

                # Flush the complete replacement before exposing it at the
                # stable path. ``os.replace`` is atomic within this directory.
                # Windows requires a writable descriptor for ``fsync``.
                with temp_path.open("rb+") as replacement_file:
                    os.fsync(replacement_file.fileno())

                try:
                    self._conn.close()
                finally:
                    current_connection_closed = True
                self._remove_sidecars(self.db_path)
                os.replace(temp_path, self.db_path)

                self._conn = self._connect(self.db_path)
                current_connection_closed = False
                self._configure_connection(self._conn, use_wal=True)
            except Exception:
                if temp_connection is not None:
                    try:
                        temp_connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                if current_connection_closed:
                    # A failed swap must leave the previous database usable.
                    self._conn = self._connect(self.db_path)
                    current_connection_closed = False
                    self._configure_connection(self._conn, use_wal=True)
                raise
            finally:
                if temp_connection is not None:
                    temp_connection.close()
                temp_path.unlink(missing_ok=True)
                self._remove_sidecars(temp_path)

    @staticmethod
    def _remove_sidecars(path: Path) -> None:
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        q: str,
        diataxis: str | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        """FTS5 MATCH with BM25 ranking and snippet.

        FTS5 MATCH syntax accepts spaces as implicit AND, ``"phrase match"``
        as a phrase, and ``-keyword`` as negation. User input is only lightly
        sanitised — trailing operators are stripped.
        """
        clean = self._sanitize_query(q)
        if not clean:
            return []
        params: list[object] = [clean]
        sql = (
            "SELECT slug, title, diataxis, phase, "
            "snippet(docs_index, 6, '<mark>', '</mark>', '...', 12) AS snip, "
            "rank "
            "FROM docs_index WHERE docs_index MATCH ?"
        )
        if diataxis:
            sql += " AND diataxis = ?"
            params.append(diataxis)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
                rows = cursor.fetchall()
            except sqlite3.OperationalError as exc:
                # User input can break FTS5 MATCH — e.g. bare operators only
                log.debug("FTS5 query failed for q=%r: %s", q, exc)
                return []

        return [
            SearchResult(
                slug=row[0], title=row[1], diataxis=row[2],
                phase=row[3], snippet=row[4],
                score=float(row[5]) if row[5] is not None else 0.0,
            )
            for row in rows
        ]

    @staticmethod
    def _sanitize_query(q: str) -> str:
        """Trims, removes bare user operators, and neutralises hyphens.

        FTS5 breaks on ``"hello`` (unclosed quote) and treats ``-foo`` as
        negation. Because our doc bodies are full of hyphenated word pairs
        (``Voice-Pipeline``, ``Jarvis-Agent-Spawn``), we split hyphenated tokens
        into individual words. The ``unicode61`` tokeniser already does this in
        the index — the query just needs to avoid the negation pattern.

        Examples:
            ``Voice-Pipeline`` -> ``Voice Pipeline`` (implicit AND)
            ``"hello world`` -> ``hello world`` (unclosed quote removed)
            ``-foo`` -> ``foo`` (leading negation removed — caution pattern)
            ``+++`` -> ``""``
        """
        s = q.strip()
        if not s:
            return ""
        # Odd number of quotes → remove all quotes
        if s.count('"') % 2 == 1:
            s = s.replace('"', "")
        # Replace hyphens in token interiors with spaces; split and filter tokens.
        # A leading ``-`` (negation) would be FTS5-valid, but we do not allow
        # user negation in v1 — everything becomes an AND match.
        out_tokens: list[str] = []
        for tok in s.replace("-", " ").split():
            tok = tok.strip("+-*&|^()")
            if not tok:
                continue
            out_tokens.append(tok)
        return " ".join(out_tokens)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()
