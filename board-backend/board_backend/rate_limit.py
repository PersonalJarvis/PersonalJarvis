"""Sehr kleiner In-Memory-Rate-Limiter.

Einfaches Sliding-Window — pro IP halten wir eine Liste der letzten
``timestamps`` und droppen alle ausserhalb des Fensters. Das reicht fuer
die einzige rate-limited Route (``/identity/register``), die ein paar
Requests pro Minute sehen wird.

Fuer ein Multi-Worker-Deployment muesste das auf Redis o.ae. wandern;
fuer einen Single-Worker-Container ist In-Memory korrekt.
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
        """``True`` wenn der Request jetzt durch darf, sonst ``False``."""
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
