"""Bounded automatic retries for durable post-turn memory reviews.

A review runs on the reviewed agent's own seat and nothing else
(``seat_brain``). When that seat keeps failing, the review gets its first
attempt plus ``MAX_RETRIES`` spaced retries and is then dropped with one log
line, instead of being retried on every later turn or answered by another
provider.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Final

from .review import review_turn

log = logging.getLogger(__name__)

#: Spaced retries after the first attempt (45-60 s, doubled per round).
MAX_RETRIES: Final[int] = 3
#: Failed attempts after which a review is dropped: the first plus the retries.
MAX_ATTEMPTS: Final[int] = MAX_RETRIES + 1


def _failures(runtime: Any) -> dict[tuple[str, str], int]:
    """Failed attempts per pending review in this process."""
    failures = getattr(runtime, "_review_failures", None)
    if failures is None:
        failures = {}
        runtime._review_failures = failures
    return failures


async def drain_reviews(runtime: Any, *, retry: int = 0) -> None:
    failures = _failures(runtime)
    async with runtime._review_lock:
        for pending in runtime.conversations.pending_reviews():
            key = (pending["session"], pending["turn_id"])
            try:
                done = await review_turn(runtime, pending)
            except Exception:
                log.exception("society review remains pending for %s", pending["turn_id"])
                done = False
            if done:
                runtime.conversations.finish_review(*key)
                failures.pop(key, None)
                continue
            failures[key] = failures.get(key, 0) + 1
            # Only the last round of a retry chain drops, so a burst of turns
            # never uses up a review's attempts before the spaced retries ran.
            if retry >= MAX_RETRIES and failures[key] >= MAX_ATTEMPTS:
                runtime.conversations.drop_review(*key)
                attempts = failures.pop(key)
                log.warning(
                    "society review of turn %s (%s) dropped after %d failed attempts; "
                    "only the agent's own seat is ever asked",
                    pending["turn_id"],
                    pending["session"],
                    attempts,
                )
    if (
        not runtime.conversations.pending_reviews()
        or retry >= MAX_RETRIES
        or getattr(runtime, "_closing", False)
    ):
        return
    active = getattr(runtime, "_memory_review_retry", None)
    if active is not None and not active.done():
        return

    async def later() -> None:
        try:
            delay = random.uniform(45, 60) * 2**retry  # noqa: S311 — retry jitter, not a secret
            await asyncio.sleep(delay)
            runtime._memory_review_retry = None
            await drain_reviews(runtime, retry=retry + 1)
        finally:
            if getattr(runtime, "_memory_review_retry", None) is asyncio.current_task():
                runtime._memory_review_retry = None

    runtime._memory_review_retry = runtime.background(later())
