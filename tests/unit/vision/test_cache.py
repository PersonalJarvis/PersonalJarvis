"""Unit tests for VisionCache (FIFO, hash dedup)."""
from __future__ import annotations

import time
from uuid import uuid4

import pytest

from jarvis.core.protocols import Observation
from jarvis.vision.cache import VisionCache


def _obs(hash_: str, title: str = "Notepad") -> Observation:
    return Observation(
        trace_id=uuid4(),
        timestamp_ns=time.time_ns(),
        screenshot_path=None,
        screenshot_hash=hash_,
        nodes=(),
        window_title=title,
        active_pid=42,
        source="full",
        pruning_stats={},
    )


def test_cache_hit_on_same_hash():
    cache = VisionCache(capacity=5)
    obs = _obs("abc")
    cache.put(obs)
    assert cache.get("abc") is obs


def test_cache_miss_on_new_hash():
    cache = VisionCache(capacity=5)
    cache.put(_obs("abc"))
    assert cache.get("xyz") is None


def test_cache_miss_on_empty_hash():
    cache = VisionCache(capacity=5)
    # An empty hash should never be cached in the first place.
    cache.put(_obs(""))
    assert len(cache) == 0
    assert cache.get("") is None


def test_cache_fifo_evicts_oldest():
    cache = VisionCache(capacity=3)
    cache.put(_obs("a"))
    cache.put(_obs("b"))
    cache.put(_obs("c"))
    cache.put(_obs("d"))  # evict 'a'
    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None
    assert cache.get("d") is not None
    assert len(cache) == 3


def test_cache_overwrite_refreshes_position():
    """When a hash is put again, it should count as 'new' and
    not be the first one evicted next time.
    """
    cache = VisionCache(capacity=3)
    cache.put(_obs("a"))
    cache.put(_obs("b"))
    cache.put(_obs("c"))
    cache.put(_obs("a"))  # 'a' gets a fresh position, 'b' will be the first evicted
    cache.put(_obs("d"))  # evict: 'b' war aeltestes
    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None
    assert cache.get("d") is not None


def test_cache_contains():
    cache = VisionCache(capacity=3)
    cache.put(_obs("hash1"))
    assert "hash1" in cache
    assert "missing" not in cache


def test_cache_clear_resets():
    cache = VisionCache(capacity=3)
    cache.put(_obs("a"))
    cache.put(_obs("b"))
    cache.clear()
    assert len(cache) == 0


def test_cache_invalid_capacity_rejected():
    with pytest.raises(ValueError):
        VisionCache(capacity=0)
