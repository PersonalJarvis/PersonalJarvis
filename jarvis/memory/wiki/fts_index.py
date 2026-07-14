"""FTS5 index builder and maintenance for the wiki vault.

Exposes the mutation callables used by full reconciliation and incremental
writers:

    ensure_schema(conn)          -- idempotent DDL (create wiki_fts if absent)
    index_vault(vault_root, conn) -- full walk; returns count of indexed pages
    upsert_page(conn, vault_root, abs_path) -- single-page reindex (delete+insert)
    remove_page(conn, vault_root, abs_path) -- remove a single page by path

Read-only metadata and vault-scan helpers support the Wiki health endpoint.

All path values stored in the DB are vault-root-relative POSIX strings
(forward slashes, no leading slash).

Frontmatter parsing reuses the private helpers from search.py (which was
the original file-walking implementation) — specifically ``_FRONTMATTER_RE``,
``_YAML_KV_RE``, and ``_H1_RE`` — so there is exactly one copy of that logic.

DB path: ``data/jarvis.db`` — the same file that hosts ``awareness_episodes``
and the messages FTS table.  The connection helper follows the same lazy-open
pattern used by ``jarvis/memory/wiki/search.py`` (3 levels up from this file
to the ``jarvis/`` package root, then one level up to the project root,
then ``data/jarvis.db``).

FTS5 compile-time check
-----------------------
The first call to ``ensure_schema`` verifies that the running SQLite was
compiled with FTS5.  If it was not, a ``RuntimeError`` is raised immediately
with a remediation hint rather than surfacing a confusing
``no such module: fts5`` SQL error at query time.

Migration note
--------------
The virtual table DDL is also shipped as
``jarvis/memory/migrations/0004_wiki_fts.sql`` so that databases opened
through the ``RecallStore`` / ``run_migrations_sync`` path receive the table
automatically on first boot.  This module's ``ensure_schema`` is the
synchronous fast path for callers (VaultSearch, AtomicWriter) that already
hold a ``sqlite3.Connection``.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL constants
# ---------------------------------------------------------------------------

_CREATE_WIKI_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
    path      UNINDEXED,
    title,
    frontmatter,
    body,
    mtime     UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

_CREATE_WIKI_INDEX_META = """
CREATE TABLE IF NOT EXISTS wiki_index_meta (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    last_indexed_at REAL NOT NULL,
    operation       TEXT NOT NULL,
    path            TEXT
);
"""

# ---------------------------------------------------------------------------
# Regex helpers (reused from original search.py internals — single source)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_H1_RE = re.compile(r"^\s{0,3}#\s+(.+)", re.MULTILINE)
_YAML_KV_RE = re.compile(r"^(\w[\w\s\-]*):\s*(.+)$", re.MULTILINE)

# Non-content directories that are intentionally absent from search. Archived
# pages are removed by AtomicWriter and must not reappear after reconciliation.
SKIP_INDEX_DIRS: frozenset[str] = frozenset(
    {"_archive", "attachments", "99-templates"}
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the ``wiki_fts`` virtual table if it does not exist.

    Idempotent — safe to call on every connection open.  Raises
    ``RuntimeError`` when SQLite was compiled without FTS5 support.

    Parameters
    ----------
    conn:
        Open, writable ``sqlite3.Connection``.
    """
    _verify_fts5(conn)
    conn.executescript(_CREATE_WIKI_FTS + _CREATE_WIKI_INDEX_META)
    conn.commit()


def index_vault(vault_root: Path, conn: sqlite3.Connection) -> int:
    """Walk *vault_root* and (re-)index every ``*.md`` file.

    Idempotent: each page is upserted (delete-by-path then insert) so
    running twice leaves exactly one row per page.

    Parameters
    ----------
    vault_root:
        Absolute path to the Obsidian vault root.
    conn:
        Open, writable ``sqlite3.Connection`` with ``wiki_fts`` present.
        Call ``ensure_schema`` first if you are not certain.

    Returns
    -------
    int
        Number of pages indexed (each upserted page counts as 1).
    """
    ensure_schema(conn)
    count = _index_all_pages(vault_root, conn)
    _record_index_metadata(conn, operation="index_vault")
    conn.commit()
    log.info("fts_index: indexed %d pages in %s", count, vault_root)
    return count


def rebuild_index(vault_root: Path, conn: sqlite3.Connection) -> int:
    """Clear ``wiki_fts`` and re-index ``vault_root`` from scratch.

    Used after a vault switch (spec A6 "connect to an existing vault") so
    search never serves stale rows from the previously active vault.

    Parameters
    ----------
    vault_root:
        Absolute path to the (new) Obsidian vault root.
    conn:
        Open, writable ``sqlite3.Connection``.

    Returns
    -------
    int
        Number of pages indexed (same contract as :func:`index_vault`).
    """
    ensure_schema(conn)
    conn.execute("DELETE FROM wiki_fts")
    count = _index_all_pages(vault_root, conn)
    _record_index_metadata(conn, operation="rebuild")
    conn.commit()
    log.info("fts_index: rebuilt %d pages in %s", count, vault_root)
    return count


def upsert_page(
    conn: sqlite3.Connection,
    vault_root: Path,
    abs_path: Path,
) -> None:
    """Reindex a single page (delete-by-path then insert).

    Called by ``AtomicWriter`` immediately after a successful write so
    the FTS index reflects the new content without a full vault walk.

    Parameters
    ----------
    conn:
        Open, writable ``sqlite3.Connection`` with ``wiki_fts`` present.
    vault_root:
        Absolute path to the vault root — used to derive the
        vault-relative path stored in the ``path`` column.
    abs_path:
        Absolute path of the page that was just written.
    """
    if _upsert_one(conn, vault_root, abs_path):
        _record_index_metadata(
            conn,
            operation="upsert",
            path=_relative_posix(vault_root, abs_path),
        )
    conn.commit()


def remove_page(
    conn: sqlite3.Connection,
    vault_root: Path,
    abs_path: Path,
) -> None:
    """Remove a single page from the FTS index by path.

    Parameters
    ----------
    conn:
        Open, writable ``sqlite3.Connection`` with ``wiki_fts`` present.
    vault_root:
        Absolute path to the vault root.
    abs_path:
        Absolute path of the page to remove.
    """
    rel = _relative_posix(vault_root, abs_path)
    conn.execute("DELETE FROM wiki_fts WHERE path = ?", (rel,))
    _record_index_metadata(conn, operation="remove", path=rel)
    conn.commit()
    log.debug("fts_index: removed %s", rel)


def read_index_metadata(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the latest successful index mutation recorded in ``conn``.

    Older databases may contain ``wiki_fts`` without the metadata table. In
    that case this read-only helper returns ``None`` rather than mutating the
    database from a health-check path.
    """
    try:
        row = conn.execute(
            "SELECT last_indexed_at, operation, path "
            "FROM wiki_index_meta WHERE singleton = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return {
        "last_indexed_at": float(row[0]),
        "operation": str(row[1]),
        "path": str(row[2]) if row[2] is not None else None,
    }


def vault_page_mtimes(vault_root: Path) -> dict[str, float]:
    """Return indexable vault paths and their current modification times."""
    pages: dict[str, float] = {}
    for abs_path in _walk_vault(vault_root):
        try:
            pages[_relative_posix(vault_root, abs_path)] = abs_path.stat().st_mtime
        except OSError:
            # A concurrent editor may replace or delete a file during the walk.
            # The watcher will process that change; omit the transient path from
            # this snapshot instead of reporting fabricated metadata.
            continue
    return pages


def is_indexable_path(vault_root: Path, abs_path: Path) -> bool:
    """Return whether ``abs_path`` belongs in a full or incremental index."""
    if abs_path.suffix.lower() != ".md":
        return False
    try:
        rel = abs_path.resolve().relative_to(vault_root.resolve())
    except (OSError, ValueError):
        return False
    if not rel.parts:
        return False
    directories = rel.parts[:-1]
    return not any(
        part.startswith(".") or part in SKIP_INDEX_DIRS
        for part in directories
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _verify_fts5(conn: sqlite3.Connection) -> None:
    """Raise ``RuntimeError`` when FTS5 is not compiled into SQLite.

    SQLite ships FTS5 by default since 3.9.0 (2015), but some Linux
    distributions (notably older Debian/Ubuntu) ship ``libsqlite3``
    without it.  Detecting early gives the operator a clear message.
    """
    options = {row[0] for row in conn.execute("PRAGMA compile_options").fetchall()}
    if "ENABLE_FTS5" not in options:
        raise RuntimeError(
            "SQLite FTS5 is not available in this Python install.  "
            "Remediation: install a SQLite build that includes FTS5 — on Debian/Ubuntu "
            "use 'apt install libsqlite3-dev' and recompile Python, or use a "
            "'python:3.11-slim' Docker image which ships FTS5 by default.  "
            "Alternatively, install the 'pysqlite3-binary' pip package as a drop-in."
        )


def _index_all_pages(vault_root: Path, conn: sqlite3.Connection) -> int:
    """Index every visible Markdown page without committing."""
    count = 0
    for abs_path in _walk_vault(vault_root):
        if _upsert_one(conn, vault_root, abs_path):
            count += 1
    return count


def _record_index_metadata(
    conn: sqlite3.Connection,
    *,
    operation: str,
    path: str | None = None,
) -> None:
    """Record one successful derived-index mutation in the current transaction."""
    conn.execute(
        "INSERT INTO wiki_index_meta(singleton, last_indexed_at, operation, path) "
        "VALUES (1, ?, ?, ?) "
        "ON CONFLICT(singleton) DO UPDATE SET "
        "last_indexed_at = excluded.last_indexed_at, "
        "operation = excluded.operation, path = excluded.path",
        (time.time(), operation, path),
    )


def _walk_vault(vault_root: Path) -> list[Path]:
    """Recursively collect indexable Markdown files.

    Mirrors the walk logic from the original ``search.py`` so behaviour
    is consistent between a full reindex and a live search.
    """
    results: list[Path] = []
    if not vault_root.exists():
        return results
    for current_root, dirnames, filenames in os.walk(vault_root):
        # Prune large non-content trees before descent rather than walking
        # every attachment on each health poll.
        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if not dirname.startswith(".") and dirname not in SKIP_INDEX_DIRS
        )
        root = Path(current_root)
        for filename in sorted(filenames):
            item = root / filename
            if item.is_file() and is_indexable_path(vault_root, item):
                results.append(item)
    return results


def _relative_posix(vault_root: Path, abs_path: Path) -> str:
    """Return a vault-root-relative POSIX path string.

    Example: ``vault_root=/vault``, ``abs_path=/vault/entities/alex.md``
    → ``"entities/alex.md"``.

    Falls back to the absolute POSIX path if *abs_path* is not under
    *vault_root* (should not happen in practice).
    """
    try:
        return abs_path.relative_to(vault_root).as_posix()
    except ValueError:
        return abs_path.as_posix()


def _parse_page_data(
    abs_path: Path,
    vault_root: Path,
) -> tuple[str, str, str, str, str] | None:
    """Read and parse a markdown page into the five FTS5 column values.

    Returns
    -------
    ``(path, title, frontmatter_flat, body, mtime_str)`` or ``None``
    when the file cannot be read.
    """
    try:
        mtime = os.path.getmtime(abs_path)
        raw = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.debug("fts_index: cannot read %s — %s", abs_path, exc)
        return None

    frontmatter: dict[str, Any] = {}
    body = raw

    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        fm_text = fm_match.group(1)
        body = raw[fm_match.end():]
        # Try PyYAML first (same priority as the legacy search.py used).
        try:
            import yaml as _yaml  # type: ignore[import]
            parsed = _yaml.safe_load(fm_text)
            if isinstance(parsed, dict):
                frontmatter = {str(k): v for k, v in parsed.items() if v is not None}
        except Exception:  # noqa: BLE001
            # Fall back to regex KV parsing — no external dep required.
            for m in _YAML_KV_RE.finditer(fm_text):
                frontmatter[m.group(1).strip()] = m.group(2).strip()

    # Title: first H1 in body, else filename stem.
    h1 = _H1_RE.search(body)
    title = h1.group(1).strip() if h1 else abs_path.stem

    # Frontmatter flat string: join all values (lists joined with spaces).
    fm_parts: list[str] = []
    for v in frontmatter.values():
        if v is None:
            continue
        if isinstance(v, list):
            fm_parts.append(" ".join(str(item) for item in v if item is not None))
        else:
            fm_parts.append(str(v))
    frontmatter_flat = " ".join(fm_parts)

    rel_path = _relative_posix(vault_root, abs_path)
    return rel_path, title, frontmatter_flat, body, str(mtime)


def _upsert_one(
    conn: sqlite3.Connection,
    vault_root: Path,
    abs_path: Path,
) -> bool:
    """Delete existing row for *abs_path* then insert a fresh one.

    Does NOT commit — callers batch commits for efficiency.
    """
    parsed = _parse_page_data(abs_path, vault_root)
    if parsed is None:
        return False
    rel_path, title, frontmatter_flat, body, mtime_str = parsed

    # Delete-then-insert is the canonical FTS5 upsert pattern.
    conn.execute("DELETE FROM wiki_fts WHERE path = ?", (rel_path,))
    conn.execute(
        "INSERT INTO wiki_fts(path, title, frontmatter, body, mtime) "
        "VALUES (?, ?, ?, ?, ?)",
        (rel_path, title, frontmatter_flat, body, mtime_str),
    )
    log.debug("fts_index: upserted %s", rel_path)
    return True


__all__ = [
    "ensure_schema",
    "index_vault",
    "rebuild_index",
    "upsert_page",
    "remove_page",
    "read_index_metadata",
    "vault_page_mtimes",
    "is_indexable_path",
    "SKIP_INDEX_DIRS",
]
