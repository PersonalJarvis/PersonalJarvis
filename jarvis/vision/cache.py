"""VisionCache — screenshot-hash-based dedup for observations.

Background: the CU loop calls `VisionEngine.observe()` several times per
step. If the screen hasn't changed between two calls, the UIA pruning (the
main cost factor) is wasted work. So we cache observations under their
screenshot hash.

Strategy: FIFO, capacity 10 entries. If a screenshot hash is cached and the
UIA tree structure (node_count, window_title) matches, return the old
observation. Otherwise it's a cache miss.

The cache lives in-process — no persistence. That's fine, because the first
observe of a new process needs a fresh screenshot anyway.
"""
from __future__ import annotations

from collections import OrderedDict

from jarvis.core.protocols import Observation


class VisionCache:
    """FIFO cache for observations, keyed on the screenshot hash.

    Usage:

        cache = VisionCache(capacity=10)
        cached = cache.get(hash_of_current_screenshot)
        if cached is not None and cached.window_title == current_title:
            return cached
        obs = do_expensive_observe(...)
        cache.put(obs)
        return obs
    """

    def __init__(self, *, capacity: int = 10) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        # OrderedDict gives us FIFO via `popitem(last=False)`.
        self._store: OrderedDict[str, Observation] = OrderedDict()

    def get(self, screenshot_hash: str) -> Observation | None:
        """Returns a cached observation, or None."""
        if not screenshot_hash:
            return None
        return self._store.get(screenshot_hash)

    def put(self, obs: Observation) -> None:
        """Stores an observation under its screenshot hash.

        If the hash is empty, the put is ignored — a cache without a key
        makes no sense. This can happen with `source='ui_tree_only'`,
        where there is no screenshot.
        """
        if not obs.screenshot_hash:
            return
        # On a hash collision we overwrite, and the ordering stays FIFO.
        if obs.screenshot_hash in self._store:
            # Move-to-end would be LRU; we want FIFO: remove + re-insert,
            # so an overwritten entry loses its original position.
            # FIFO is the clearer semantics for cache-hit tests.
            del self._store[obs.screenshot_hash]
        self._store[obs.screenshot_hash] = obs
        # Evict on overflow — drop the oldest entry.
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, screenshot_hash: object) -> bool:
        return isinstance(screenshot_hash, str) and screenshot_hash in self._store
