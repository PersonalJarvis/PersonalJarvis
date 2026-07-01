"""Tests for the UUIDv7 helper."""
from __future__ import annotations

import time
from uuid import UUID

from jarvis.missions.ids import uuid7, uuid7_str


def test_uuid7_returns_uuid_instance() -> None:
    assert isinstance(uuid7(), UUID)


def test_uuid7_version_is_7() -> None:
    """RFC 9562: version bits must be 0b0111 (=7)."""
    u = uuid7()
    assert u.version == 7


def test_uuid7_variant_is_rfc4122() -> None:
    """Variant bits must be 0b10 (RFC 4122)."""
    u = uuid7()
    # variant property returns 'specified in RFC 4122' string or int
    assert u.variant == "specified in RFC 4122"


def test_uuid7_distinct_calls_produce_distinct_ids() -> None:
    ids = {uuid7() for _ in range(1000)}
    assert len(ids) == 1000  # no collisions


def test_uuid7_str_is_canonical_format() -> None:
    s = uuid7_str()
    # 8-4-4-4-12 hex-format
    assert len(s) == 36
    parts = s.split("-")
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


def test_uuid7_timestamp_prefix_lexicographically_sortable() -> None:
    """Two IDs >=2ms apart must be lexicographically ordered in time."""
    a = uuid7_str()
    time.sleep(0.005)  # 5 ms
    b = uuid7_str()
    assert a < b


def test_uuid7_throughput_acceptable() -> None:
    """10000 IDs in <1s — enough for a mission-event spike."""
    start = time.perf_counter()
    for _ in range(10000):
        uuid7()
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"uuid7 too slow: {elapsed:.3f}s for 10k calls"
