"""Bounded automatic retries for durable post-turn memory reviews."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from .review import review_turn

log = logging.getLogger(__name__)


async def drain_reviews(runtime: Any, *, retry: int = 0) -> None:
    async with runtime._review_lock:
        for pending in runtime.conversations.pending_reviews():
            try:
                if await review_turn(runtime, pending):
                    runtime.conversations.finish_review(pending["session"], pending["turn_id"])
            except Exception:
                log.exception("society review remains pending for %s", pending["turn_id"])
    if (
        not runtime.conversations.pending_reviews()
        or retry >= 3
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
