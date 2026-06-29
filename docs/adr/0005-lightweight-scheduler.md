---
title: "ADR-0005: Lightweight-Scheduler"
slug: adr-0005-lightweight-scheduler
diataxis: adr
status: active
owner: sam
last_reviewed: 2026-04-29
phase: 5
audience: developer
---

# ADR-0005 — Scheduler: Lightweight Instead of APScheduler

**Status:** Accepted  (2026-04-22)
**Phase:** 5 — Async capability

## Context

The task queue needs time-based scheduling (`after_delay`, `at_time`) and event-based scheduling (`on_event`). `croniter` is already a dependency for skills, but tasks should **not** support cron (see user confirmation §8.3 of the research). The question: build it ourselves or use a library.

## Decision

**Build our own, `jarvis/tasks/scheduler.py`, asyncio task + `heapq`.**

Core:
```python
class TaskScheduler:
    def __init__(self, store: TaskStore, bus: EventBus, runner: TaskRunner):
        self._store = store; self._bus = bus; self._runner = runner
        self._heap: list[tuple[int, str]] = []    # (due_at_ns, task_id)
        self._wakeup = asyncio.Event()
        self._bus.subscribe_all(self._on_event)   # for on_event triggers

    async def run(self, cancel_token: CancelToken):
        await self._hydrate_from_store()
        while not cancel_token.is_cancelled():
            now_ns = time.time_ns()
            while self._heap and self._heap[0][0] <= now_ns:
                due_at, task_id = heapq.heappop(self._heap)
                asyncio.create_task(self._runner.run(task_id, cancel_token))
            timeout = max(0.05, (self._heap[0][0] - now_ns) / 1e9) if self._heap else None
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=timeout)
            except TimeoutError:
                pass
            self._wakeup.clear()
```

New tasks: `heapq.heappush(self._heap, (due_at_ns, task_id)); self._wakeup.set()`.
The event subscriber routes events matching the `event_selector` of an `on_event` task directly to the runner.

## Consequences

+ No new dependency (APScheduler = ~200 KB + pytz + tzlocal — overkill).
+ Fully embedded in our async/bus pattern. No separate worker pool, no thread fights.
+ Deterministic behavior: the heap structure is easy to test.
+ Cancel-token integration is trivial (the same primitive throughout, ADR-0004).
- No features like "fire-and-forget jobs", "every-N-seconds recurring", or "misfire handling" out of the box. Can be retrofitted later if needed; not required by the mandate.
- No cross-process scheduler semantics. The task scheduler runs only in the Jarvis main app (single instance via `single_instance.py`).

## Alternatives Considered

- **APScheduler (SQLAlchemy jobstore):** Robust, feature-rich, but +100 LOC of boilerplate to integrate into our bus, +3 dependencies (APScheduler, SQLAlchemy, tzlocal). YAGNI.
- **`arq` / `rq`:** Require Redis. Rejected.
- **Celery:** Too heavy, not Windows-first. Rejected.
- **`croniter` + our own loop:** Cron syntax is confusing for users ("how do I write '3 days from now'?"). We explicitly do not want cron. Rejected.

## Open

- Time zone: all `due_at_ns` are UTC in the DB. User input "in two hours" is computed relative to `time.time_ns()` → no TZ questions. "Tomorrow 9am" comes via `zoneinfo.ZoneInfo` from the system zone — the trigger parser translates it.
- Drift on long delays (days): not relevant, as long as `await asyncio.wait_for` with TimeoutError tolerance covers it.
