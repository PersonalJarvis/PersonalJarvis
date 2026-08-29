"""Cancellation that sticks — tearing down long-lived asyncio tasks.

A plain ``task.cancel(); await task`` is not a teardown on this codebase's
Python floor (3.11). Two things can leave the task running with the cancel
counted but never delivered again:

* ``asyncio.wait_for`` swallows a cancellation when the awaited future
  completes in the same loop step the cancel lands in — it returns the result
  instead of raising (CPython gh-86296 / bpo-42130; only the 3.12 rewrite fixed
  it). A pump that awaits ``wait_for`` once per frame — a microphone feed at
  thirty frames a second, under a loop that stalls for hundreds of
  milliseconds — sits in that window most of the time.
* On the Windows proactor loop a cancel landing while the loop has no timer
  armed can be lost in the IOCP poll (BUG-081's general form).

Both look identical from outside: ``Task.cancelling() > 0`` and the task keeps
going. Both are repaired the same way: re-deliver the cancel until it sticks,
inside a budget, so a task that never yields to it cannot wedge its owner.

BUG-185 is the live incident: the voice session's teardown awaited its
microphone pump unbounded after two swallowed cancels, so the pipeline never
returned to IDLE, the dictation key refused every press with
``handover_failed``, and only an app restart freed the microphone.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

#: How long ``cancel_and_reap`` keeps re-delivering a cancel before it gives
#: the task up as unkillable and returns to its caller.
DEFAULT_REAP_BUDGET_S = 3.0

#: The re-delivery interval. Also the timer that guarantees the loop wakes up
#: on Windows, where a cancel can otherwise be lost (BUG-081).
DEFAULT_REAP_HEARTBEAT_S = 0.5


async def cancel_and_reap(
    task: asyncio.Task[Any] | asyncio.Future[Any],
    *,
    budget_s: float = DEFAULT_REAP_BUDGET_S,
    heartbeat_s: float = DEFAULT_REAP_HEARTBEAT_S,
    label: str | None = None,
) -> bool:
    """Cancel ``task`` and wait for it to actually finish — bounded.

    Re-issues the cancel every ``heartbeat_s`` until the task is done or the
    budget is spent, then consumes the outcome so a cancelled or failed task
    never warns about an unretrieved exception. Returns ``True`` when the task
    finished within the budget and ``False`` when it was abandoned; the caller
    decides what an abandoned task means for it (usually: log, and rely on the
    resource the task reads from being closed a moment later).

    Never raises for the task's sake. The CALLER's own cancellation is honoured:
    if this coroutine is cancelled while waiting, that propagates.
    """
    name = label or _task_name(task)
    if task.done():
        _consume(task)
        return True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(budget_s))
    beat = max(0.01, float(heartbeat_s))
    attempts = 0
    while True:
        task.cancel()
        attempts += 1
        remaining = deadline - loop.time()
        done, _pending = await asyncio.wait({task}, timeout=min(beat, max(0.0, remaining)))
        if done:
            _consume(task)
            if attempts > 1:
                log.info(
                    "%s needed %d cancel deliveries before it stopped "
                    "(a swallowed cancellation, BUG-185).",
                    name,
                    attempts,
                )
            return True
        if loop.time() >= deadline:
            log.warning(
                "%s ignored %d cancel deliveries over %.1fs — abandoning it "
                "so the teardown can finish (BUG-185).",
                name,
                attempts,
                float(budget_s),
            )
            # Whatever it eventually raises must not surface as "exception was
            # never retrieved" after everyone has moved on.
            task.add_done_callback(_consume)
            return False


def raise_if_cancelling() -> None:
    """Re-honour a cancel the current task swallowed.

    For long-running pumps: call once per iteration. ``Task.cancelling()`` is
    the number of cancel requests nobody has ``uncancel()``-ed — a value above
    zero after an await that returned normally means the cancellation was
    eaten on the way (the ``wait_for`` race above). Raising here delivers it
    where the runtime failed to.
    """
    current = asyncio.current_task()
    if current is not None and current.cancelling() > 0:
        raise asyncio.CancelledError()


def _task_name(task: asyncio.Task[Any] | asyncio.Future[Any]) -> str:
    getter = getattr(task, "get_name", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001, S110 — a name is cosmetic
            pass
    return repr(task)[:120]


def _consume(task: asyncio.Task[Any] | asyncio.Future[Any]) -> None:
    """Retrieve a finished task's outcome without letting it raise here."""
    if not task.done() or task.cancelled():
        return
    try:
        task.exception()
    except Exception:  # noqa: BLE001, S110 — consuming, not handling
        pass
