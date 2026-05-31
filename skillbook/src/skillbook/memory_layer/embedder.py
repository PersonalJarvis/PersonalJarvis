"""Embedder protocol + deterministic HashEmbedder fallback (ADR-0003)."""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

import numpy as np

EMBEDDING_DIM = 384


@runtime_checkable
class Embedder(Protocol):
    """Maps a text string to a unit-normalized float32 vector of length EMBEDDING_DIM."""

    def embed(self, text: str) -> np.ndarray: ...


class HashEmbedder:
    """Deterministic offline embedder: SHA-256 seeds a 384-dim unit vector.

    Semantically meaningless beyond exact-string equality, but stable across runs,
    platforms, and Python versions. Sufficient for the Curator's exact-match
    deduplication path. ADR-0003 documents the production sentence-transformers
    upgrade path.
    """

    def embed(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big", signed=False)
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(EMBEDDING_DIM).astype(np.float32, copy=False)
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            vec[0] = 1.0
            return vec
        return vec / norm


def default_embedder() -> Embedder:
    """Return the production embedder if sentence-transformers is installed and the
    model cache is reachable; otherwise fall back to HashEmbedder.

    Lazy: only imports sentence_transformers on first call.
    """
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except ImportError:
        return HashEmbedder()

    try:
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    except Exception:
        return HashEmbedder()

    class _SentenceTransformersEmbedder:
        def embed(self, text: str) -> np.ndarray:
            vec = model.encode(text, normalize_embeddings=True)
            return np.asarray(vec, dtype=np.float32)

    return _SentenceTransformersEmbedder()
