"""Minimal forward-migration runner for the Recall SQLite store.

Background
==========

The base schema lives in ``jarvis/memory/schema.sql`` and is loaded
through :py:meth:`aiosqlite.Connection.executescript` on every
``RecallStore.open()``. The statements there use ``CREATE TABLE IF
NOT EXISTS`` so the bootstrap is idempotent — but the corollary is
that the bootstrap is **incapable of widening a ``CHECK``
constraint on a table that already exists**. Existing user
databases keep the constraint the table was first created with,
even after ``schema.sql`` is edited.

This module closes that gap with the smallest possible pattern:
versioned ``.sql`` files under ``jarvis/memory/migrations/`` named
``NNNN_description.sql``. The runner reads SQLite's ``user_version``
pragma, applies any file whose leading integer is greater than the
current version, and bumps the pragma to the highest applied
number. Re-running is therefore a no-op.

The first migration we ship under this scheme is
``0003_expand_role_check.sql``; numbers 0001 and 0002 are reserved
for past schema events that lived in ``schema.sql`` alone and have
no replay step. New migrations should use the next free integer
greater than the current head.

Two entry points are exposed:

- :func:`run_migrations_sync` for tests and any sync caller that
  already owns a :class:`sqlite3.Connection`.
- :func:`run_migrations` (coroutine) for ``RecallStore.open()``;
  it awaits the same statement sequence on an
  :class:`aiosqlite.Connection`.

Files that do not match ``NNNN_<slug>.sql`` are logged and skipped
— surfaces typo'd filenames as warnings rather than silent drift.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIGRATIONS_DIR: Path = Path(__file__).parent / "migrations"
"""Directory holding ``NNNN_description.sql`` migration files."""

_FILE_NAME_RE = re.compile(r"^(\d{4})_[a-zA-Z0-9_]+\.sql$")


def _discover_migrations(directory: Path) -> list[tuple[int, Path]]:
    """Return ``[(number, path), ...]`` sorted by number ascending.

    Files that don't fit ``NNNN_<slug>.sql`` are skipped with a
    warning so a typo'd filename surfaces in logs instead of
    becoming silent drift.
    """
    if not directory.exists():
        return []
    found: list[tuple[int, Path]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".sql":
            continue
        match = _FILE_NAME_RE.match(path.name)
        if match is None:
            logger.warning(
                "migration_runner: skipping %s — name does not match NNNN_<slug>.sql",
                path.name,
            )
            continue
        found.append((int(match.group(1)), path))
    found.sort(key=lambda pair: pair[0])
    return found


def run_migrations_sync(
    conn: sqlite3.Connection,
    directory: Path | None = None,
) -> int:
    """Apply pending migrations against a synchronous SQLite connection.

    Returns the new ``user_version`` (highest applied number, or
    the pre-existing value if nothing was applied). Each migration
    file is expected to wrap itself in ``BEGIN`` / ``COMMIT``; a
    failure leaves ``user_version`` at the previous head, so the
    next call retries from the same point.
    """
    target_dir = directory if directory is not None else MIGRATIONS_DIR
    cur = conn.execute("PRAGMA user_version")
    row = cur.fetchone()
    cur.close()
    current = int(row[0]) if row is not None else 0

    pending = [
        (number, path)
        for number, path in _discover_migrations(target_dir)
        if number > current
    ]
    if not pending:
        return current

    for number, path in pending:
        sql = path.read_text(encoding="utf-8")
        logger.info("migration_runner: applying %s", path.name)
        try:
            conn.executescript(sql)
        except sqlite3.Error:
            logger.exception(
                "migration_runner: %s failed; user_version stays at %d",
                path.name, current,
            )
            raise
        # PRAGMA user_version does not accept bound parameters.
        # The number is validated by _FILE_NAME_RE to be a 4-digit
        # integer from a filename we own, not user input.
        conn.execute(f"PRAGMA user_version = {number}")
        current = number

    return current


async def run_migrations(conn: Any, directory: Path | None = None) -> int:
    """Apply pending migrations against an :class:`aiosqlite.Connection`.

    Mirrors :func:`run_migrations_sync` step for step but awaits
    every database call so the event loop is not blocked.
    """
    target_dir = directory if directory is not None else MIGRATIONS_DIR

    cur = await conn.execute("PRAGMA user_version")
    row = await cur.fetchone()
    await cur.close()
    current = int(row[0]) if row is not None else 0

    pending = [
        (number, path)
        for number, path in _discover_migrations(target_dir)
        if number > current
    ]
    if not pending:
        return current

    for number, path in pending:
        sql = path.read_text(encoding="utf-8")
        logger.info("migration_runner: applying %s", path.name)
        try:
            await conn.executescript(sql)
        except Exception:
            logger.exception(
                "migration_runner: %s failed; user_version stays at %d",
                path.name, current,
            )
            raise
        await conn.execute(f"PRAGMA user_version = {number}")
        current = number

    return current


__all__ = ["MIGRATIONS_DIR", "run_migrations", "run_migrations_sync"]
