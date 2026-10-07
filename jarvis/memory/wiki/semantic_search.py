from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import numpy as np

from jarvis.memory.wiki.db_path import resolve_wiki_db_path
from jarvis.memory.wiki.search import SearchHit
from jarvis.memory.wiki.semantic_index import ensure_schema

log = logging.getLogger(__name__)


class SemanticVaultSearch:
    def __init__(
        self,
        vault_root: Path,
        *,
        embedder=None,
        conn: sqlite3.Connection | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._root = Path(vault_root)
        self._embedder = embedder
        self._conn = conn
        self._owns_conn = conn is None
        self._db_path = db_path

    def _model(self):
        if self._embedder is None:
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
        return self._embedder

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        db_path = self._db_path or resolve_wiki_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        ensure_schema(self._conn)
        return self._conn

    @staticmethod
    def _cosine(a, b) -> float:
        a = np.asarray(a)
        b = np.asarray(b)
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)

    def search(self, query: str, *, k: int = 5) -> list[SearchHit]:
        if not query or not query.strip():
            return []

        try:
            conn = self._get_conn()
            qvec = list(self._model().embed([query]))[0]
            rows = conn.execute(
                "SELECT path, chunk_index, content, embedding FROM wiki_semantic"
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            log.debug("SemanticVaultSearch unavailable: %s", exc)
            return []

        hits: list[SearchHit] = []

        for rel_path, _chunk_index, content, embedding_json in rows:
            try:
                vector = json.loads(embedding_json)
                score = self._cosine(qvec, vector)
            except Exception as exc:  # noqa: BLE001
                log.debug("SemanticVaultSearch: invalid stored embedding: %s", exc)
                continue

            path = self._root / rel_path
            hits.append(
                SearchHit(
                    title=path.stem,
                    path=path,
                    snippet=content[:200],
                    score=score,
                    preview=content[:500],
                )
            )

        return sorted(hits, key=lambda h: h.score, reverse=True)[:k]

    def warm_up(self) -> None:
        try:
            self._model()
            self._get_conn()
        except Exception as exc:  # noqa: BLE001
            log.debug("SemanticVaultSearch warm_up skipped: %s", exc)

    def close(self) -> None:
        if self._owns_conn and self._conn is not None:
            try:
                self._conn.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("SemanticVaultSearch close failed: %s", exc)
            self._conn = None
