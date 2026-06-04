"""Tests for the FTS5 upsert hook wired into AtomicWriter.

Requires ``jarvis.memory.wiki.fts_index`` — skipped when not available.

Test matrix
-----------
- After ``apply([PageUpdate(...)])`` succeeds, ``wiki_fts`` contains a row
  for that path.
- Re-applying the same path (upsert) keeps the row count at 1, not 2.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

fts_index = pytest.importorskip(
    "jarvis.memory.wiki.fts_index",
    reason="fts_index peer module not yet available",
)

from jarvis.memory.wiki.atomic_writer import AtomicWriter  # noqa: E402
from jarvis.memory.wiki.protocols import PageUpdate  # noqa: E402

from .conftest import FakePageRepository  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    fts_index.ensure_schema(conn)
    return conn


@pytest.fixture
def writer(tmp_path: Path, db_conn: sqlite3.Connection) -> AtomicWriter:
    vault = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions"):
        (vault / sub).mkdir(parents=True)
    w = AtomicWriter(
        vault_root=vault,
        backup_dir=tmp_path / "backups",
        max_backups=5,
        concurrent_edit_lock_seconds=0.0,   # bypass 30-second guard in tests
    )
    # Inject the in-memory test connection so we don't touch data/jarvis.db.
    w._fts_conn = db_conn
    return w


def _new_body(slug: str, body: str = "test body content") -> str:
    return f"---\ntype: entity\nslug: {slug}\n---\n# {slug}\n{body}\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_rows(conn: sqlite3.Connection, rel_path: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM wiki_fts WHERE path = ?", (rel_path,)
    ).fetchone()
    return int(row[0])


def run(coro):  # type: ignore[override]
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_apply_inserts_fts_row(writer: AtomicWriter, db_conn: sqlite3.Connection):
    """After a successful apply, wiki_fts has a row for the written page."""
    vault = writer.vault_root
    target = vault / "entities" / "myentity.md"
    update = PageUpdate(
        target_path=target,
        operation="create",
        new_body=_new_body("myentity"),
    )
    repo = FakePageRepository()

    result = run(writer.apply([update], repo=repo))

    assert target in result.applied, f"expected target in applied, got {result}"
    rel = target.relative_to(vault).as_posix()
    assert _count_rows(db_conn, rel) == 1, "expected exactly one FTS row after apply"


def test_apply_upserts_not_duplicates(writer: AtomicWriter, db_conn: sqlite3.Connection):
    """Re-applying the same page keeps the FTS row count at 1."""
    vault = writer.vault_root
    target = vault / "entities" / "dupcheck.md"
    repo = FakePageRepository()

    for i in range(2):
        update = PageUpdate(
            target_path=target,
            operation="create" if i == 0 else "update",
            new_body=_new_body("dupcheck", body=f"body version {i}"),
        )
        result = run(writer.apply([update], repo=repo))
        assert target in result.applied, f"iteration {i}: target not in applied"

    rel = target.relative_to(vault).as_posix()
    assert _count_rows(db_conn, rel) == 1, (
        "expected exactly 1 FTS row after two applies (upsert, not duplicate)"
    )


def test_archive_purges_fts_row(writer: AtomicWriter, db_conn: sqlite3.Connection):
    """Archiving a page removes its FTS row so search cannot return a ghost
    hit pointing at the now-moved (or vanished) live path.
    """
    vault = writer.vault_root
    repo = FakePageRepository()
    target = vault / "entities" / "stale.md"
    rel = target.relative_to(vault).as_posix()

    run(writer.apply(
        [PageUpdate(target_path=target, operation="create",
                    new_body=_new_body("stale"))],
        repo=repo,
    ))
    assert _count_rows(db_conn, rel) == 1

    run(writer.apply(
        [PageUpdate(target_path=target, operation="archive", new_body="")],
        repo=repo,
    ))
    assert _count_rows(db_conn, rel) == 0, (
        "archived page must be removed from the FTS index"
    )


def test_rename_purges_old_fts_row(writer: AtomicWriter, db_conn: sqlite3.Connection):
    """Renaming a page indexes the new path and purges the old one."""
    vault = writer.vault_root
    repo = FakePageRepository()
    old = vault / "entities" / "oldname.md"
    new = vault / "entities" / "newname.md"
    old_rel = old.relative_to(vault).as_posix()
    new_rel = new.relative_to(vault).as_posix()

    run(writer.apply(
        [PageUpdate(target_path=old, operation="create",
                    new_body=_new_body("oldname"))],
        repo=repo,
    ))
    assert _count_rows(db_conn, old_rel) == 1

    run(writer.apply(
        [PageUpdate(target_path=new, operation="rename",
                    new_body=_new_body("newname"), rename_from=old)],
        repo=repo,
    ))
    assert _count_rows(db_conn, new_rel) == 1, "new path must be indexed"
    assert _count_rows(db_conn, old_rel) == 0, "old path row must be purged"


def test_forget_paths_removes_rows(writer: AtomicWriter, db_conn: sqlite3.Connection):
    """``forget_paths`` purges FTS rows for paths moved outside the writer
    (e.g. the session-rollup rolling-window archiving, which renames files
    directly without going through ``apply``).
    """
    vault = writer.vault_root
    repo = FakePageRepository()
    target = vault / "sessions" / "2026-05-01-abc.md"
    rel = target.relative_to(vault).as_posix()
    run(writer.apply(
        [PageUpdate(target_path=target, operation="create",
                    new_body="---\ntype: session\nsession_id: abc\n---\n# S\nbody\n")],
        repo=repo,
    ))
    assert _count_rows(db_conn, rel) == 1

    writer.forget_paths([target])
    assert _count_rows(db_conn, rel) == 0
