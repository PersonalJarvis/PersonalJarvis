"""A very small in-memory rate limiter.

Simple sliding window — per IP we keep a list of the last
``timestamps`` and drop everything outside the window. That's enough
for the only rate-limited route (``/identity/register``), which will
see a handful of requests per minute.

For a multi-worker deployment this would need to move to Redis or
similar; for a single-worker container, in-memory is correct.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, *, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """``True`` if the request may pass now, ``False`` otherwise."""
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True
