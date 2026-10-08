"""Bounded automatic retries for durable post-turn memory reviews.

A review runs on the reviewed agent's own seat and nothing else
(``seat_brain``). When that seat keeps failing, a review gets at most
``MAX_ATTEMPTS`` seat calls in total, spaced by a jittered backoff, and is
then dropped with one log line. The budget is durable: the attempt count and
the next due time live on the review row, so neither a burst of turns nor an
app restart hands a failing seat extra calls.

Drains are coalesced: every finished turn asks for one, but while a drain
runs, further requests only mark that another pass is wanted. A drain only
calls the seat for reviews that are due; one wake-up is kept for the next
review that becomes due.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Final

from .events import now_ms
from .review import review_turn

log = logging.getLogger(__name__)

#: Seat calls one review may cost before it is dropped: a first attempt plus
#: three spaced retries.
MAX_ATTEMPTS: Final[int] = 4
#: Pause after the n-th failed attempt: 45-60 s, doubled per attempt.
_BACKOFF_MIN_S: Final[float] = 45.0
_BACKOFF_MAX_S: Final[float] = 60.0


def _backoff_ms(attempts: int) -> int:
    delay = random.uniform(_BACKOFF_MIN_S, _BACKOFF_MAX_S)  # noqa: S311 — retry jitter, not a secret
    return int(delay * 2 ** max(0, attempts - 1) * 1000)


async def drain_reviews(runtime: Any) -> None:
    """Review every due pending turn; coalesced with a drain already running."""
    if getattr(runtime, "_review_draining", False):
        # One drain at a time: the running one makes one more pass for this
        # request (a turn queued meanwhile is picked up there).
        runtime._review_rerun = True
        return
    runtime._review_draining = True
    try:
        while True:
            runtime._review_rerun = False
            async with runtime._review_lock:
                await _drain_once(runtime)
            if not runtime._review_rerun or getattr(runtime, "_closing", False):
                break
    finally:
        runtime._review_draining = False
    _schedule_wake(runtime)


async def _drain_once(runtime: Any) -> None:
    archive = runtime.conversations
    for pending in archive.pending_reviews():
        if _cloud_owned(runtime, pending):
            continue  # The remote owner alone may review; preserve retry state.
        if pending["retry_after_ms"] > now_ms():
            continue  # Still waiting out its backoff: no seat call now.
        key = (pending["session"], pending["turn_id"])
        try:
            done = await review_turn(runtime, pending)
        except Exception:
            log.exception("society review remains pending for %s", pending["turn_id"])
            done = False
        if done:
            archive.finish_review(*key)
            continue
        attempts = archive.fail_review(
            *key, retry_after_ms=now_ms() + _backoff_ms(pending["attempts"] + 1)
        )
        if attempts >= MAX_ATTEMPTS:
            archive.drop_review(*key)
            log.warning(
                "society review of turn %s (%s) dropped after %d failed attempts; "
                "only the agent's own seat is ever asked",
                pending["turn_id"],
                pending["session"],
                attempts,
            )


def _schedule_wake(runtime: Any) -> None:
    """Keep exactly one wake-up, for the earliest pending review's due time."""
    if getattr(runtime, "_closing", False):
        return
    pending = [
        row for row in runtime.conversations.pending_reviews() if not _cloud_owned(runtime, row)
    ]
    if not pending:
        return
    due = min(item["retry_after_ms"] for item in pending)
    current = getattr(runtime, "_memory_review_retry", None)
    if current is not None and current is not asyncio.current_task() and not current.done():
        if getattr(runtime, "_memory_review_retry_at", 0) <= due:
            return  # An earlier (or equal) wake-up is already armed.
        # Only a sleeping wake-up can be here: a draining one would have
        # coalesced this drain instead of letting it reach this point.
        current.cancel()
    runtime._memory_review_retry_at = due
    runtime._memory_review_retry = runtime.background(_wake(runtime, due))


async def _wake(runtime: Any, due: int) -> None:
    await asyncio.sleep(max(0.0, (due - now_ms()) / 1000))
    await drain_reviews(runtime)


def _cloud_owned(runtime: Any, pending: dict[str, Any]) -> bool:
    from .cloud_host import placement_for
    from .surface import agent_id_of

    owner = agent_id_of(pending["session"])
    return bool(owner and placement_for(runtime.store.path.parent, owner))
