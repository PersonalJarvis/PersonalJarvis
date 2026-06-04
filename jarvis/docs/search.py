"""SQLite FTS5 wrapper for the doc full-text index.

Why FTS5: BM25 ranking and the ``snippet()`` function are built-in, no Algolia
account required, no frontend index bundle bloat. ~50-100 Markdown files in a
single-user app fit easily into one SQLite file (~500 KB).

A single-connection pattern is fine for SQLite (WAL mode + multi-thread). The
registry holds one instance; REST routes read through its methods.
"""
from __future__ import annotations

import logging
import sqlite3
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
        # ``check_same_thread=False`` because watchdog wants to upsert from a
        # different thread. We serialise access via the lock.
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None,
        )
        self._lock = threading.Lock()
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE VIRTUAL TABLE IF NOT EXISTS docs_index USING fts5(
                    slug UNINDEXED,
                    title,
                    diataxis UNINDEXED,
                    phase UNINDEXED,
                    tags,
                    headings,
                    body,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )

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
        """Full re-indexing — called on bootstrap and after larger reloads.
        Atomic via a transaction."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM docs_index")
                self._conn.executemany(
                    """
                    INSERT INTO docs_index
                        (slug, title, diataxis, phase, tags, headings, body)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            d.frontmatter.slug,
                            d.frontmatter.title,
                            d.frontmatter.diataxis.value,
                            d.frontmatter.phase,
                            " ".join(d.frontmatter.tags),
                            " ".join(t for _l, t, _s in d.headings),
                            d.body,
                        )
                        for d in docs
                    ],
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

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
        (``Voice-Pipeline``, ``OpenClaw-Spawn``), we split hyphenated tokens
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
