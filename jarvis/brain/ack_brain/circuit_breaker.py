"""Circuit breaker for the Pre-Thinking-Ack Flash-Brain.

Three-state machine ``closed → open → half-open`` that protects the
voice pipeline from a degraded provider. After ``threshold`` consecutive
failures the breaker opens for ``cooldown_s`` seconds; during cooldown
all ``run()`` calls short-circuit to ``None``. After cooldown the next
call enters half-open: a success closes the breaker, a failure re-opens
it for another cooldown.

The breaker mutates state through an ``asyncio.Lock`` so concurrent
voice utterances cannot race on the failure counter.
"""
from __future__ import annotations

import asyncio
import time
from typing import Literal

CircuitState = Literal["closed", "open", "half-open"]


class CircuitBreaker:
    """Async-safe three-state circuit breaker."""

    def __init__(
        self,
        *,
        threshold: int,
        cooldown_s: int,
        now: callable = time.monotonic,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        if cooldown_s < 1:
            raise ValueError("cooldown_s must be >= 1")
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._now = now
        self._lock = asyncio.Lock()
        self._state: CircuitState = "closed"
        self._consecutive_failures = 0
        self._opened_at: float = 0.0

    async def is_open(self) -> bool:
        """Return True if the breaker is currently blocking calls.

        Side effect: may transition ``open → half-open`` if the cooldown
        window has elapsed since the breaker opened. Half-open is NOT
        considered "open" for the purposes of this check — one trial
        call is allowed through.
        """
        async with self._lock:
            if self._state == "open":
                elapsed = self._now() - self._opened_at
                if elapsed >= self._cooldown_s:
                    self._state = "half-open"
                    return False
                return True
            return False

    async def record_failure(self) -> None:
        """Register a failed provider call.

        - closed: increment counter; flip to open at threshold.
        - half-open: re-open immediately (one strike out).
        - open: stay open, reset the cooldown clock.
        """
        async with self._lock:
            if self._state == "half-open":
                self._state = "open"
                self._opened_at = self._now()
                self._consecutive_failures = self._threshold
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._threshold:
                self._state = "open"
                self._opened_at = self._now()

    async def record_success(self) -> None:
        """Register a successful provider call.

        Any success resets the failure counter and closes the breaker
        (whether it was closed, half-open, or open — defence in depth).
        """
        async with self._lock:
            self._state = "closed"
            self._consecutive_failures = 0
            self._opened_at = 0.0

    @property
    def state(self) -> CircuitState:
        """Current state — read-only snapshot, not synchronised."""
        return self._state
