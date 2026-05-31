"""RateLimitTracker: circuit breaker for rate-limited providers.

When a brain provider has returned a 429, we mark it as "unavailable" for
`cooldown_s` seconds — the fallback chain skips it. After the cooldown
expires, it is tried again.

This prevents the manager from wasting ~3-5 s per voice turn on overloaded
providers, only to fall back to the same fallback every time.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RateLimitTracker:
    cooldown_s: float = 30.0
    _marks: dict[tuple[str, str | None], float] = field(default_factory=dict)

    def is_available(self, provider: str, model: str | None = None) -> bool:
        key = (provider, model)
        deadline = self._marks.get(key)
        if deadline is None:
            return True
        if time.time() >= deadline:
            # Cooldown expired — new attempt allowed
            self._marks.pop(key, None)
            return True
        return False

    def mark_rate_limited(self, provider: str, model: str | None = None,
                          cooldown_s: float | None = None) -> None:
        key = (provider, model)
        self._marks[key] = time.time() + (cooldown_s or self.cooldown_s)

    def clear(self, provider: str | None = None) -> None:
        if provider is None:
            self._marks.clear()
            return
        self._marks = {k: v for k, v in self._marks.items() if k[0] != provider}

    def snapshot(self) -> dict[str, float]:
        now = time.time()
        return {
            f"{p}:{m}" if m else p: max(0.0, deadline - now)
            for (p, m), deadline in self._marks.items()
        }
