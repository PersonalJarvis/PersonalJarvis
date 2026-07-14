"""Event-loop diagnostics — snapshot every asyncio task with its await stack.

Built to hunt the AP-20-class busy-loop where anyio's
``CancelScope._deliver_cancellation`` reschedules itself on every event-loop
iteration because some task inside the cancelled scope never finishes
(measured 2026-07-10/11: ~95 % of one core burned while the app idles).
py-spy cannot name the owner — the sampled stack only shows the event-loop
callback, and an elevated process denies attachment anyway — so the app
reports on itself from inside the loop.

Read-only, stdlib-only, and imported lazily by ``server.py`` like every other
route module (AP-26: nothing here runs at boot; the handler only does work
when called).
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

# Stack frames reported per task. Enough to see the await chain that pins a
# task inside a cancelled scope without shipping megabytes of JSON.
_STACK_LIMIT = 16


def _task_snapshot(task: asyncio.Task[Any]) -> dict[str, Any]:
    """One task as a JSON-safe dict; private-attr reads are best-effort.

    ``cancelling()`` (Py 3.11+) is the number of pending cancel requests —
    a task that stays alive with ``cancelling > 0`` across two snapshots is
    exactly the busy-loop culprit this endpoint exists to name.
    """
    try:
        stack_frames = task.get_stack(limit=_STACK_LIMIT)
        stack = [
            f"{f.f_code.co_name} ({f.f_code.co_filename}:{f.f_lineno})"
            for f in stack_frames
        ]
    except Exception as exc:  # noqa: BLE001 — diagnostics must never raise
        stack = [f"<stack unavailable: {exc}>"]

    cancelling = 0
    with contextlib.suppress(Exception):  # pre-3.11 or exotic task impl
        cancelling = task.cancelling()

    must_cancel = None
    fut_waiter = None
    with contextlib.suppress(Exception):  # CPython-private, absent elsewhere
        must_cancel = bool(task._must_cancel)  # type: ignore[attr-defined]
        waiter = task._fut_waiter  # type: ignore[attr-defined]
        fut_waiter = repr(waiter)[:300] if waiter is not None else None

    exception = None
    if task.done() and not task.cancelled():
        with contextlib.suppress(Exception):
            exc_obj = task.exception()
            exception = repr(exc_obj)[:300] if exc_obj is not None else None

    return {
        "name": task.get_name(),
        "coro": repr(task.get_coro())[:300],
        "done": task.done(),
        "cancelled": task.cancelled(),
        "cancelling": cancelling,
        "must_cancel": must_cancel,
        "fut_waiter": fut_waiter,
        "exception": exception,
        "stack": stack,
    }


@router.get("/event-loop-tasks")
async def event_loop_tasks() -> dict[str, Any]:
    """Snapshot all tasks on the serving loop, suspects first.

    Suspects (``cancelling > 0`` or ``must_cancel``) sort to the top: those
    are the tasks anyio is trying — and failing — to cancel, i.e. the owners
    of a ``_deliver_cancellation`` busy-loop. Two snapshots a few seconds
    apart separate a task that is legitimately shutting down from one that is
    stuck forever.
    """
    tasks = [_task_snapshot(t) for t in asyncio.all_tasks()]
    tasks.sort(
        key=lambda t: (t["cancelling"], 1 if t["must_cancel"] else 0),
        reverse=True,
    )
    return {
        "total": len(tasks),
        "suspects": sum(
            1 for t in tasks if t["cancelling"] or t["must_cancel"]
        ),
        "tasks": tasks,
    }


@router.get("/cancel-scopes")
async def cancel_scopes() -> dict[str, Any]:
    """Report every anyio CancelScope that is actively delivering cancellation.

    A scope whose ``_cancel_handle`` is set has a ``_deliver_cancellation``
    callback scheduled on the loop. A HEALTHY delivery lasts a few loop
    iterations; one that persists across snapshots is the busy-loop engine.
    For each such scope this names the host task, the tasks still held in the
    scope's bookkeeping, and its child scopes — the ownership answer the
    sampled py-spy stack cannot give (live hunt 2026-07-11: the callback
    burned ~95 % of a core with ZERO tasks carrying a pending cancel, so the
    owner had to be read off the scope objects themselves).

    Walks ``gc.get_objects()`` — costs a moment and briefly blocks the loop;
    strictly a diagnostics tool, never called by product code.
    """
    import gc

    try:
        from anyio._backends._asyncio import CancelScope  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 — anyio absent or reshaped
        return {"error": f"anyio backend CancelScope unavailable: {exc}"}

    def _task_brief(task: Any) -> str:
        try:
            return f"{task.get_name()} | {task.get_coro()!r}"[:300]
        except Exception:  # noqa: BLE001
            return repr(task)[:300]

    delivering: list[dict[str, Any]] = []
    total = 0
    for obj in gc.get_objects():
        if not isinstance(obj, CancelScope):
            continue
        total += 1
        if getattr(obj, "_cancel_handle", None) is None:
            continue
        host = getattr(obj, "_host_task", None)
        parent = getattr(obj, "_parent_scope", None)
        delivering.append(
            {
                "id": hex(id(obj)),
                "cancel_called": bool(getattr(obj, "_cancel_called", False)),
                "shield": bool(getattr(obj, "_shield", False)),
                "deadline": getattr(obj, "_deadline", None),
                "host_task": _task_brief(host) if host is not None else None,
                "host_task_done": bool(host.done()) if host is not None else None,
                "parent_scope": hex(id(parent)) if parent is not None else None,
                "tasks": [
                    _task_brief(t) for t in list(getattr(obj, "_tasks", ()) or ())
                ][:20],
                "child_scopes": [
                    {
                        "id": hex(id(c)),
                        "cancel_called": bool(getattr(c, "_cancel_called", False)),
                        "shield": bool(getattr(c, "_shield", False)),
                        "host_task": (
                            _task_brief(getattr(c, "_host_task", None))
                            if getattr(c, "_host_task", None) is not None
                            else None
                        ),
                        "tasks": [
                            _task_brief(t)
                            for t in list(getattr(c, "_tasks", ()) or ())
                        ][:20],
                    }
                    for c in list(getattr(obj, "_child_scopes", ()) or ())[:20]
                ],
            }
        )
    return {
        "total_scopes": total,
        "delivering": delivering,
        "delivering_count": len(delivering),
    }


@router.get("/event-loop-lag")
async def event_loop_lag() -> dict[str, Any]:
    """Measure scheduling lag of the serving loop.

    Sleeps 0 four times and reports how long each round-trip through the
    ready queue took. A healthy idle loop yields microseconds; a loop being
    spammed by a rescheduling callback (the busy-loop signature) shows
    consistently elevated lag.
    """
    loop = asyncio.get_running_loop()
    lags_ms: list[float] = []
    for _ in range(4):
        t0 = loop.time()
        await asyncio.sleep(0)
        lags_ms.append((loop.time() - t0) * 1000.0)
    return {"lags_ms": lags_ms, "max_ms": max(lags_ms)}


__all__ = ["router"]
