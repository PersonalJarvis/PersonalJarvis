"""HashEmbedder contract: 384-dim float32 unit vector, deterministic, distinct inputs distinguish."""

from __future__ import annotations

import numpy as np

from skillbook.memory_layer.embedder import HashEmbedder, EMBEDDING_DIM


def test_hash_embedder_returns_float32_vector_of_expected_dim() -> None:
    emb = HashEmbedder()
    vec = emb.embed("hello world")
    assert isinstance(vec, np.ndarray)
    assert vec.shape == (EMBEDDING_DIM,)
    assert vec.dtype == np.float32


def test_hash_embedder_returns_unit_vector() -> None:
    emb = HashEmbedder()
    vec = emb.embed("anything")
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5


def test_hash_embedder_is_deterministic_across_instances() -> None:
    v1 = HashEmbedder().embed("retry actor magic_home_controller after delay 3s")
    v2 = HashEmbedder().embed("retry actor magic_home_controller after delay 3s")
    np.testing.assert_array_equal(v1, v2)


def test_hash_embedder_distinguishes_different_strings() -> None:
    emb = HashEmbedder()
    v_apple = emb.embed("apple")
    v_banana = emb.embed("banana")
    cosine = float(v_apple @ v_banana)
    assert cosine < 0.5  # unrelated short strings should be far apart
