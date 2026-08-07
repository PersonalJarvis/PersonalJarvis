"""``VaultSearch`` — FTS5-backed keyword search over the Obsidian wiki vault.

B5 Agent B (recall-tool). Backed by SQLite FTS5 index in ``data/jarvis.db``.

The search engine is consumed by two callers:
- ``WikiRecallTool`` (router-tier plugin tool, this package)
- ``WikiContextInjector`` (Agent C, ``jarvis/brain/wiki_context.py``)

The §3.1 interface contract in ``docs/plans/b5/00-OVERVIEW.md`` is
**non-negotiable** — ``SearchHit`` and ``VaultSearch.search`` signatures are
unchanged.

Design notes
~~~~~~~~~~~~
* Delegates search to the ``wiki_fts`` virtual table (FTS5, unicode61,
  remove_diacritics 2) built by ``jarvis/memory/wiki/fts_index.py``.
* Query tokens are OR-combined; each token is wrapped in double-quotes so that
  FTS5 special characters (``"``, ``*``, ``:``, ``(``, ``)``, ``^``, ``-``)
  are treated as literals.
* BM25 score is normalised to [0.0, 1.0] higher-is-better via
  ``1.0 / (1.0 + max(0.0, raw_bm25))``.  FTS5 BM25 returns lower (more
  negative) values for better matches; clamping ``max(0.0, …)`` ensures
  perfect matches (BM25 ≤ 0) get a score close to 1.0.
* ``SearchHit.path`` is the absolute path (vault_root / stored_relative_path)
  so that callers that do ``path.relative_to(vault_root)`` work without change.
* Connection is opened lazily and reused; ``check_same_thread=False`` because
  the voice path and tool path may call from different threads.
* Empty query → returns ``[]`` without touching the DB (mirror previous
  behaviour).
* When ``fts_index`` is not yet importable (peer module not yet delivered),
  all searches return ``[]`` gracefully — no hard crash.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# FTS5 characters that must be escaped inside a quoted token.
# Wrapping in double-quotes handles most; we strip internal double-quotes
# before quoting so the token itself is safe.
_FTS5_SPECIAL_STRIP_RE = re.compile(r'"')

# Path to the shared SQLite database (relative to project root resolved at
# import time would be fragile — callers may set vault_root from any CWD).
# The fts_index module resolves this from config; here we just use whatever
# the schema says: "data/jarvis.db" relative to the project root.
# VaultSearch accepts an optional `conn` parameter for test injection.


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single ranked search result from the vault.

    Attributes
    ----------
    title:
        H1 heading of the matched page, falling back to the stem of the
        filename when no ``# Title`` line is present.
    path:
        Absolute path of the matched markdown file inside the vault.
    snippet:
        Up to ~200 characters around the first match in the body, as
        produced by SQLite's ``snippet()`` function.  Empty string when
        the match was only in the frontmatter column.
    score:
        Float in [0.0, 1.0].  Higher is better.  Monotonic within a
        single call; not comparable across calls.
    frontmatter:
        The page's flattened frontmatter values — the same text the FTS
        ``frontmatter`` column indexes, including any ``search_aliases``.
        A hit matched via an alias has NO body snippet, so a consumer
        judging relevance on title+snippet alone would discard exactly the
        hits aliases exist to produce; it needs this to see WHY the page
        matched.  Empty string when the page has no frontmatter.
    preview:
        Leading body text of the page, always populated regardless of where
        the match occurred.  Gives frontmatter-only hits something to show;
        ``snippet`` keeps its legacy "empty unless the body matched"
        contract.
    """

    title: str
    path: Path
    snippet: str
    score: float
    frontmatter: str = ""
    preview: str = ""


class VaultSearch:
    """FTS5-backed search over an Obsidian-style markdown vault.

    All public methods are synchronous — the class is designed to run
    without blocking the asyncio event loop for more than a few
    milliseconds (FTS5 on 100 pages is sub-millisecond).

    Parameters
    ----------
    vault_root:
        Absolute path to the vault root.
    conn:
        Optional pre-opened ``sqlite3.Connection``.  When supplied it is
        used directly and never closed by this instance (useful in tests).
        When omitted, the connection is opened lazily from
        ``data/jarvis.db`` and owned by this instance.
    db_path:
        Override the default DB path.  Ignored when ``conn`` is provided.
    """

    def __init__(
        self,
        vault_root: Path,
        *,
        conn: sqlite3.Connection | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._root = vault_root
        self._conn: sqlite3.Connection | None = conn
        self._owns_conn: bool = conn is None
        self._db_path: Path | None = db_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, query: str, *, k: int = 5) -> list[SearchHit]:
        """Return up to *k* hits, highest score first.

        Parameters
        ----------
        query:
            One or more whitespace-separated keywords.  Treated as OR —
            any keyword match counts.  Case-insensitive.
        k:
            Maximum number of results to return.  Must be >= 1.

        Returns
        -------
        list[SearchHit]
            Sorted descending by score.  Empty list when the vault is
            missing, empty, nothing matches, or the FTS index is not yet
            available.
        """
        if not query or not query.strip():
            return []

        tokens = [t for t in query.split() if t.strip()]
        if not tokens:
            return []

        match_expr = _build_match_expr(tokens)

        try:
            conn = self._get_conn()
        except Exception as exc:  # noqa: BLE001
            log.warning("VaultSearch: cannot open DB: %s", exc)
            return []

        try:
            return self._run_query(conn, match_expr, k)
        except sqlite3.OperationalError as exc:
            # Table may not exist yet (index not built) — degrade gracefully.
            log.debug("VaultSearch: FTS query failed (%s) — returning []", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            log.warning("VaultSearch: unexpected error during search: %s", exc)
            return []

    def warm_up(self) -> None:
        """Open the connection and run one throwaway FTS query.

        The lazy ``_get_conn`` (open + ``ensure_schema``) otherwise runs
        INSIDE the injector's per-turn latency budget, and losing that race
        on the first qualifying turn of a process cost the turn its context
        (logged ``reason=timeout``). Meant to be called once from a
        background thread at boot — never on the startup critical path
        (AP-26). Failures are non-fatal: the next ``search`` simply pays the
        cold open itself.
        """
        try:
            conn = self._get_conn()
            conn.execute(
                "SELECT rowid FROM wiki_fts WHERE wiki_fts MATCH ? LIMIT 1",
                ('"warmup"',),
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            # Missing table = index not built yet; anything else is the same
            # degraded-but-working state search() already tolerates.
            log.debug("VaultSearch warm_up skipped: %s", exc)

    def close(self) -> None:
        """Close the owned DB connection, if any."""
        if self._owns_conn and self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Return the sqlite3 connection, opening lazily if needed."""
        if self._conn is not None:
            return self._conn
        db_path = self._db_path or _default_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        # Ensure schema exists (idempotent).
        try:
            import jarvis.memory.wiki.fts_index as _fts  # type: ignore[import]
            _fts.ensure_schema(self._conn)
        except ImportError:
            log.debug("VaultSearch: fts_index not available — schema not ensured")
        return self._conn

    def _run_query(
        self, conn: sqlite3.Connection, match_expr: str, k: int
    ) -> list[SearchHit]:
        # BM25 weights are positional over EVERY column, including the
        # UNINDEXED ones. ``wiki_fts`` is
        # ``(path UNINDEXED, title, frontmatter, body, mtime UNINDEXED)`` —
        # five columns — so the weight list must have five entries. The
        # leading 0.0 belongs to ``path`` (UNINDEXED, never matches); the
        # trailing 0.0 to ``mtime``. Passing only four weights silently
        # shifted everything left, giving ``path`` the top weight and
        # leaving ``body`` at 0.0 (body invisible to ranking).
        sql = """
            SELECT
                path,
                title,
                snippet(wiki_fts, 3, '', '', '…', 32) AS snippet,
                body,
                bm25(wiki_fts, 0.0, 3.0, 2.0, 1.0, 0.0) AS bm25_score,
                frontmatter
            FROM wiki_fts
            WHERE wiki_fts MATCH ?
            ORDER BY bm25_score
            LIMIT ?
        """
        cursor = conn.execute(sql, (match_expr, k))
        # Lowercased bare tokens for the "did body actually contain a hit?" check.
        # Strip the quotes AND the prefix marker: '"drogen"*' and '"drogen"'
        # both reduce to 'drogen', so the body check below stays a plain
        # substring test and a prefix-only hit is judged on its stem.
        bare_tokens = [
            _FTS5_SPECIAL_STRIP_RE.sub("", tok).rstrip("*").lower()
            for tok in match_expr.split(" OR ")
        ]
        hits: list[SearchHit] = []
        for row in cursor:
            rel_path_str, title, snippet, body, bm25_raw, frontmatter = row
            abs_path = self._root / rel_path_str
            score = _normalise_bm25(bm25_raw)
            # SQLite's snippet() returns the body column text even when the
            # match was only in the frontmatter column. Mirror the legacy
            # contract: snippet is empty for frontmatter-only matches.
            body_lc = (body or "").lower()
            if not any(t.strip('"') and t.strip('"') in body_lc for t in bare_tokens):
                snippet = ""
            hits.append(
                SearchHit(
                    title=title or abs_path.stem,
                    path=abs_path,
                    snippet=snippet or "",
                    score=round(score, 4),
                    frontmatter=frontmatter or "",
                    preview=_leading_text(body),
                )
            )
        log.debug(
            "VaultSearch: %d hits for match=%r, vault=%s",
            len(hits),
            match_expr,
            self._root,
        )
        return hits


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


#: Shortest token that may be searched as a prefix. Below this a prefix match
#: is indiscriminate ("ab*" hits half the vault) and costs precision for no
#: recall worth having.
_PREFIX_MIN_LEN = 4


def _build_match_expr(tokens: list[str]) -> str:
    """Build an FTS5 MATCH expression from a list of tokens.

    Each token is wrapped in double-quotes (after stripping any embedded
    double-quotes) so that FTS5 special characters are treated as literals.
    Tokens are combined with OR.

    Tokens of at least :data:`_PREFIX_MIN_LEN` characters additionally get a
    PREFIX variant (``"drogen"*``). FTS5 matches whole tokens, which is a poor
    fit for a compounding language: measured on the real vault, "Drogen" did
    not find a page whose term was the corresponding German compound, and a
    singular did not find its own plural. Every compound and every inflection
    was a
    separate, unreachable word. The prefix variant is OR-ed IN ADDITION to the
    exact token, so exact matches keep their higher BM25 rank and the change
    can only add recall, never reorder a hit that already worked.

    Examples
    --------
    >>> _build_match_expr(["foo", "bar"])
    '"foo" OR "bar"'
    >>> _build_match_expr(["drogen"])
    '"drogen" OR "drogen"*'
    >>> _build_match_expr(['a:b'])
    '"a:b"'
    """
    parts: list[str] = []
    for tok in tokens:
        cleaned = _FTS5_SPECIAL_STRIP_RE.sub("", tok)
        if not cleaned:
            continue
        parts.append(f'"{cleaned}"')
        if len(cleaned) >= _PREFIX_MIN_LEN:
            parts.append(f'"{cleaned}"*')
    return " OR ".join(parts)


#: How much leading body text ``SearchHit.preview`` carries.
_PREVIEW_CHARS = 200


def _leading_text(body: str | None, limit: int = _PREVIEW_CHARS) -> str:
    """First meaningful prose of a page, for hits with no body snippet.

    Skips the H1 and any blank/heading lines so the preview starts at real
    content rather than at "# Title" — a frontmatter-only hit otherwise shows
    the reader nothing it did not already know from the title.
    """
    if not body:
        return ""
    parts: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("---"):
            continue
        parts.append(stripped)
        if sum(len(p) for p in parts) >= limit:
            break
    text = " ".join(parts)
    return text[:limit].rstrip() + "…" if len(text) > limit else text


def _normalise_bm25(raw: float) -> float:
    """Normalise FTS5 BM25 to [0.0, 1.0) higher-is-better.

    FTS5 BM25 returns NEGATIVE values for real matches, more negative =
    better. Formula: ``m / (1.0 + m)`` with ``m = max(0.0, -raw)``, a
    monotonic map of match strength onto [0.0, 1.0).

    The previous formula (``1.0 / (1.0 + max(0.0, raw))``) clamped every
    negative raw value to the SAME score 1.0 — believable at a glance
    because "good match → close to 1.0" held, but with zero spread the
    relative-score floor in ``wiki_relevance.relevant_hits`` compared
    every hit against ``1.0 * factor`` and could never drop anything.
    """
    strength = max(0.0, -float(raw))
    return strength / (1.0 + strength)


def _default_db_path() -> Path:
    """Resolve ``data/jarvis.db`` relative to the project root.

    Walks upward from this file's location to find the directory
    containing ``jarvis/`` as a package, then appends ``data/jarvis.db``.
    Falls back to a sibling ``data/`` if the walk fails.
    """
    from jarvis.memory.wiki.db_path import resolve_wiki_db_path

    return resolve_wiki_db_path()


__all__ = ["SearchHit", "VaultSearch"]
