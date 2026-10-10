from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from jarvis.memory.wiki import fts_index

_EXCLUDED_FILES = {"schema.md", "memory.md", "log.md"}

_CREATE = """
CREATE TABLE IF NOT EXISTS wiki_semantic (
    path TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT NOT NULL,
    PRIMARY KEY(path, chunk_index)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_CREATE)
    conn.commit()


def _chunks(body: str) -> list[str]:
    return [
        part.strip()
        for part in body.split("\n\n")
        if len(part.strip()) >= 30
    ]


def upsert_page(
    conn: sqlite3.Connection,
    vault_root: Path,
    abs_path: Path,
    *,
    embedder,
) -> None:
    if abs_path.name.lower() in _EXCLUDED_FILES:
        return

    parsed = fts_index._parse_page_data(abs_path, vault_root)
    if parsed is None:
        return

    rel_path, _title, _frontmatter, body, _mtime = parsed
    chunks = _chunks(body)

    conn.execute("DELETE FROM wiki_semantic WHERE path = ?", (rel_path,))

    for i, (chunk, vector) in enumerate(zip(chunks, embedder.embed(chunks), strict=True)):
        conn.execute(
            "INSERT INTO wiki_semantic(path, chunk_index, content, embedding) "
            "VALUES (?, ?, ?, ?)",
            (rel_path, i, chunk, json.dumps(np.asarray(vector, dtype=float).tolist())),
        )

    conn.commit()


def remove_page(
    conn: sqlite3.Connection,
    vault_root: Path,
    abs_path: Path,
) -> None:
    rel_path = fts_index._relative_posix(vault_root, abs_path)
    conn.execute("DELETE FROM wiki_semantic WHERE path = ?", (rel_path,))
    conn.commit()


def rebuild_index(
    vault_root: Path,
    conn: sqlite3.Connection,
    *,
    embedder,
) -> int:
    ensure_schema(conn)
    conn.execute("DELETE FROM wiki_semantic")

    count = 0
    for abs_path in fts_index._walk_vault(vault_root):
        if abs_path.name.lower() in _EXCLUDED_FILES:
            continue
        upsert_page(conn, vault_root, abs_path, embedder=embedder)
        count += 1

    conn.commit()
    return count
