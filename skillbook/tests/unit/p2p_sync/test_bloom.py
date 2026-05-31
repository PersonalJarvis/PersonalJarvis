"""BloomFilter primitive used for set reconciliation between peers."""

from __future__ import annotations

import math

from skillbook.p2p_sync.bloom import BloomFilter


def test_add_then_contains_is_true() -> None:
    bf = BloomFilter(m=2048, k=4)
    bf.add("rule_001")
    assert "rule_001" in bf


def test_absent_item_is_usually_not_contained() -> None:
    bf = BloomFilter(m=2048, k=4)
    for i in range(50):
        bf.add(f"rule_{i:03d}")
    assert "definitely_not_present_xyz" not in bf


def test_serialize_deserialize_roundtrip() -> None:
    bf = BloomFilter(m=1024, k=3)
    items = [f"id_{i}" for i in range(20)]
    for it in items:
        bf.add(it)
    blob = bf.serialize()
    restored = BloomFilter.deserialize(blob)
    for it in items:
        assert it in restored


def test_from_items_classmethod_short_path() -> None:
    bf = BloomFilter.from_items([f"r_{i}" for i in range(10)], m=1024, k=3)
    for i in range(10):
        assert f"r_{i}" in bf


def test_false_positive_rate_within_bound() -> None:
    """For m=8192, k=4, n=200 the expected FPR is below 5%."""
    bf = BloomFilter(m=8192, k=4)
    for i in range(200):
        bf.add(f"present_{i}")
    false_positives = sum(1 for j in range(2000) if f"missing_{j}" in bf)
    assert false_positives / 2000 < 0.05
