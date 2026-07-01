"""TaskScheduler — asyncio + heapq-based scheduler (ADR-0005).

Simple, two paths:

1. **Time-based** (``after_delay`` + ``at_time``) — a min-heap ordered by
   ``due_at_ns``. The ``run()`` loop pops every due entry and dispatches it to
   the ``TaskRunner`` before waiting for the next one.

2. **Event-based** (``on_event``) — the scheduler registers itself as a
   wildcard subscriber on the bus and dispatches every event class that an
   ``on_event`` task has recorded as its ``event_selector``.

**No** cron semantics, **no** APScheduler, **no** second thread.
Everything runs on the main async loop — that is deliberate (ADR-0005).

The scheduler uses a ``CancelToken`` as its top-level abort condition
(ADR-0004). Running tasks get their own runner task, derived from this token.
"""
from __future__ import annotations

import asyncio
import contextlib
import heapq
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from jarvis.core.bus import EventBus
from jarvis.core.events import Event, TaskScheduled
from jarvis.tasks.schema import TaskSpec

if TYPE_CHECKING:
    from jarvis.control.cancel import CancelToken
    from jarvis.tasks.runner import TaskRunner
    from jarvis.tasks.store import TaskStore


log = logging.getLogger(__name__)


class _Dispatchable(Protocol):
    async def run(
        self, task_id: str, *, trigger_event: dict[str, Any] | None = None
    ) -> None: ...


def parse_iso_timestamp_to_ns(iso: str) -> int:
    """Converts ISO-8601 (optionally with ``Z``) into UTC nanoseconds.

    Local time without a TZ is interpreted as the system zone — matching the
    TriggerAtTime contract (see schema.py).
    """
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int(dt.timestamp() * 1e9)


class TaskScheduler:
    """Lightweight asyncio scheduler.

    Lifecycle:
    1. ``__init__`` — constructor, binds to bus + store + runner.
    2. ``bind_bus()`` — subscribe_all for event dispatch (optional; can be
       called later if needed).
    3. ``run(cancel_token)`` — main loop. Hydrates from the DB, sleeps,
       dispatches, waits. Terminates on token cancel.
    4. ``schedule(spec)`` — called externally when the UI creates a new
       task. Writes to the store + the heap + a wakeup event.
    """

    def __init__(
        self,
        store: TaskStore,
        bus: EventBus,
        runner: _Dispatchable | TaskRunner | None = None,
    ) -> None:
        self._store = store
        self._bus = bus
        self._runner = runner
        # Heap entries: (due_at_ns, task_id). task_id as str is
        # comparable — heapq only uses it as a tiebreaker for identical
        # due_at_ns values.
        self._heap: list[tuple[int, str]] = []
        self._wakeup = asyncio.Event()
        # On-event tasks — map event class name → set of task_ids.
        # The wildcard subscriber checks the event class against this index.
        self._on_event_index: dict[str, set[str]] = {}
        self._bound = False
        self._hydrated = False
        # Track already-registered task IDs — prevents duplicate heap entries
        # on a race between ``hydrate()`` and ``schedule()``.
        self._known: set[str] = set()
        # H10 fix: max_firings is tracked per task in memory.
        # None = unlimited. Decremented on each on_event match; at 0 the task
        # is removed from the index and marked as "completed".
        self._firings_left: dict[str, int | None] = {}
        # Fire-once dedup for event-triggered rules: an (task_id, subject) pair
        # that has already fired is never fired again. ``subject`` is the event's
        # identifying field (e.g. a MissionCompleted's mission_id), so a standing
        # rule ("whenever a mission finishes") cannot re-fire for the SAME mission
        # if a terminal event is somehow re-published. Insertion-ordered dict used
        # as a bounded FIFO set (see _DEDUP_CAP) to keep memory bounded.
        self._fired_dedup: dict[tuple[str, str], None] = {}
        # Pending runner tasks, so we can clean up on cancel.
        self._runner_tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------
    # Runner wiring (DI — the runner can be set after construction)
    # ------------------------------------------------------------------

    def attach_runner(self, runner: _Dispatchable | TaskRunner) -> None:
        self._runner = runner

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def schedule(self, spec: TaskSpec, *, trace_id: str | None = None) -> str:
        """Persists the spec, adds it to the heap/event index, wakes the loop."""
        task_id = await self._store.insert(spec, trace_id=trace_id)
        self._register_in_memory(spec, task_id)
        # TaskScheduled event for transparency (the UI lists it)
        due_at_ns = self._due_at_ns_for(spec)
        await self._bus.publish(
            TaskScheduled(
                task_id=task_id,
                trigger_type=spec.trigger.type,
                due_at_ns=due_at_ns or 0,
                title=spec.title,
                source_layer="tasks.scheduler",
            )
        )
        self._wakeup.set()
        return task_id

    async def cancel_task(self, task_id: str, reason: str = "user_cancel") -> bool:
        """Marks a task as ``cancelled`` if it isn't already terminal.

        Also removes it from the in-memory structures. Running runners are
        NOT interrupted directly — that's what the kill-switch path is for.
        The use case here is: "task was scheduled, user hits X."
        """
        task = await self._store.get(task_id)
        if task is None:
            return False
        if task["state"] in ("completed", "failed", "cancelled", "interrupted"):
            return False
        # Remove from heap (linear scan, small enough — a typical queue is < 100).
        self._heap = [(due, tid) for (due, tid) in self._heap if tid != task_id]
        heapq.heapify(self._heap)
        # Remove from the event index
        for ids in self._on_event_index.values():
            ids.discard(task_id)
        self._known.discard(task_id)

        await self._store.update_state(task_id, "cancelled", error=reason)
        await self._store.append_step(task_id, "log", {"event": "cancelled", "reason": reason})
        # Event on the bus
        from jarvis.core.events import TaskCancelled
        await self._bus.publish(
            TaskCancelled(task_id=task_id, reason=reason, source_layer="tasks.scheduler")
        )
        self._wakeup.set()
        return True

    # ------------------------------------------------------------------
    # Event-bus wiring
    # ------------------------------------------------------------------

    def bind_bus(self) -> None:
        """Registers the wildcard handler for ``on_event`` dispatch."""
        if self._bound:
            return
        self._bus.subscribe_all(self._on_any_event)
        self._bound = True

    async def _on_any_event(self, event: Event) -> None:
        """Wildcard handler: if an event class matches an ``on_event`` task,
        dispatch the runner fire-and-forget.

        The filter expression (``filter_expr``) is checked via ``_match_filter``
        during the runner dispatch — we do have access to the event here, but
        the spec lives in the DB, and we don't want to block in this handler.
        Hence: the handler enqueues, and the runner checks.
        """
        cls_name = type(event).__name__
        task_ids = self._on_event_index.get(cls_name)
        if not task_ids:
            return
        # Snapshot the event's flat fields once — handed to the runner as the
        # ``trigger_event`` template context ({result_uri}, {status}, ...).
        event_ctx = _event_to_dict(event)
        # Copy, so the runner loop is allowed to modify the set.
        for tid in list(task_ids):
            # Filter-match here first — this avoids a runner launch for
            # events that obviously don't match.
            spec = await self._store.get_spec(tid)
            if spec is None:
                task_ids.discard(tid)
                continue
            if spec.trigger.type != "on_event":
                # Defensive: should never happen, but if it does, drop it.
                task_ids.discard(tid)
                continue
            if not _match_filter(event, spec.trigger.filter_expr):
                continue
            # Fire-once dedup: never run the same rule twice for the same event
            # subject (e.g. one mission's terminal event). Skips WITHOUT touching
            # the max_firings counter, so a re-published event cannot drain it.
            dedup_key = _dedup_key(tid, event)
            if dedup_key is not None:
                if dedup_key in self._fired_dedup:
                    continue
                self._fired_dedup[dedup_key] = None
                self._trim_dedup()
            # H10 fix: track max_firings in memory. When 0 → remove the task
            # from the index, mark it "completed", and do NOT dispatch it.
            left = self._firings_left.get(tid)
            if left is not None:
                if left <= 0:
                    task_ids.discard(tid)
                    self._firings_left.pop(tid, None)
                    continue
                self._firings_left[tid] = left - 1
            await self._dispatch_runner(tid, trigger_event=event_ctx)
            # If that was the last fire: clean up.
            if left is not None and left - 1 <= 0:
                task_ids.discard(tid)
                self._firings_left.pop(tid, None)
                self._known.discard(tid)
                try:
                    await self._store.update_state(tid, "completed")
                except Exception:  # noqa: BLE001
                    log.exception("max_firings cleanup: update_state failed "
                                  "for task_id=%s", tid)

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------

    async def hydrate(self) -> None:
        """Reads all ``scheduled`` tasks and rebuilds the heap + event index.

        Idempotent: a second call is a no-op. Protects against ``run()``'s
        internal call and an explicit ``await hydrate()`` registering a task
        twice.

        H9 fix: the ``due_at_ns`` stored in the DB is used **directly**
        instead of being recomputed. Otherwise an "in 30s" task would wait
        another 30s after a 20s crash (50s total instead of 30s).
        """
        if self._hydrated:
            return
        rows = await self._store.all_pending_scheduled()
        for row in rows:
            spec = await self._store.get_spec(row["id"])
            if spec is None:
                continue
            stored_due = row.get("due_at_ns")
            self._register_in_memory(spec, row["id"],
                                      stored_due_at_ns=stored_due)
        self._hydrated = True

    def _register_in_memory(
        self,
        spec: TaskSpec,
        task_id: str,
        *,
        stored_due_at_ns: int | None = None,
    ) -> None:
        """Adds a task to the heap or the event index — idempotent.

        If ``stored_due_at_ns`` is given (the hydration path), the persisted
        value is used; otherwise the trigger recomputes it (the schedule path
        for new inserts).
        """
        if task_id in self._known:
            return
        trig = spec.trigger
        if trig.type == "after_delay":
            due = (stored_due_at_ns
                   if stored_due_at_ns is not None
                   else time.time_ns() + int(trig.delay_seconds * 1e9))
            heapq.heappush(self._heap, (due, task_id))
        elif trig.type == "at_time":
            due = (stored_due_at_ns
                   if stored_due_at_ns is not None
                   else parse_iso_timestamp_to_ns(trig.iso_timestamp))
            heapq.heappush(self._heap, (due, task_id))
        elif trig.type == "every":
            if stored_due_at_ns is not None:
                due = stored_due_at_ns
            elif trig.start_at:
                due = parse_iso_timestamp_to_ns(trig.start_at)
            else:
                due = time.time_ns() + int(trig.interval_seconds * 1e9)
            heapq.heappush(self._heap, (due, task_id))
        elif trig.type == "on_event":
            self._on_event_index.setdefault(trig.event_name, set()).add(task_id)
            self._firings_left[task_id] = trig.max_firings   # None = unlimited
        self._known.add(task_id)

    def _due_at_ns_for(self, spec: TaskSpec) -> int | None:
        trig = spec.trigger
        if trig.type == "after_delay":
            return time.time_ns() + int(trig.delay_seconds * 1e9)
        if trig.type == "at_time":
            try:
                return parse_iso_timestamp_to_ns(trig.iso_timestamp)
            except ValueError:
                return None
        if trig.type == "every":
            if trig.start_at:
                try:
                    return parse_iso_timestamp_to_ns(trig.start_at)
                except ValueError:
                    return None
            return time.time_ns() + int(trig.interval_seconds * 1e9)
        return None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, cancel_token: CancelToken | None = None) -> None:
        """Main loop — runs until ``cancel_token.is_cancelled()``.

        Typical usage from the DesktopApp/orchestrator:

            token = CancelToken()
            scheduler = TaskScheduler(store, bus, runner)
            scheduler.bind_bus()
            asyncio.create_task(scheduler.run(token))
        """
        await self.hydrate()
        if not self._bound:
            self.bind_bus()

        while True:
            if cancel_token is not None and cancel_token.is_cancelled():
                return
            now_ns = time.time_ns()
            await self._drain_due_tasks(now_ns)

            # Next wakeup: either the next-due time or indefinitely
            if self._heap:
                timeout = max(0.05, (self._heap[0][0] - now_ns) / 1e9)
            else:
                timeout = None
            try:
                if timeout is None:
                    await self._wakeup.wait()
                else:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=timeout)
            except TimeoutError:
                pass
            self._wakeup.clear()

    async def _drain_due_tasks(self, now_ns: int) -> None:
        """Pop + dispatch every task whose due time has passed.

        Recurring (``every``) tasks are re-armed for their next interval
        right after dispatch; one-shot triggers simply leave the heap.
        Dispatch is fire-and-forget (``create_task``) so a slow task never
        blocks the scheduler loop.
        """
        while self._heap and self._heap[0][0] <= now_ns:
            _due, tid = heapq.heappop(self._heap)
            self._known.discard(tid)
            spec = await self._store.get_spec(tid)
            await self._dispatch_runner(tid)
            if spec is not None and spec.trigger.type == "every":
                await self._rearm_every(tid, spec, now_ns)

    async def _rearm_every(self, task_id: str, spec: TaskSpec, now_ns: int) -> None:
        """Re-insert a recurring task at ``now + interval`` and persist the
        new due time so a restart picks the schedule back up.
        """
        next_due = now_ns + int(spec.trigger.interval_seconds * 1e9)
        heapq.heappush(self._heap, (next_due, task_id))
        self._known.add(task_id)
        await self._store.set_next_due(task_id, next_due)

    def _trim_dedup(self) -> None:
        """Bound the fire-once dedup map — drop the oldest half on overflow."""
        if len(self._fired_dedup) <= _DEDUP_CAP:
            return
        for key in list(self._fired_dedup)[: _DEDUP_CAP // 2]:
            del self._fired_dedup[key]

    async def _dispatch_runner(
        self,
        task_id: str,
        *,
        trigger_event: dict[str, Any] | None = None,
    ) -> None:
        if self._runner is None:
            log.warning("TaskScheduler.run: no runner attached, task %s falls through", task_id)
            return
        task = asyncio.create_task(
            self._safe_run(task_id, trigger_event), name=f"task-runner-{task_id}"
        )
        self._runner_tasks.add(task)
        task.add_done_callback(self._runner_tasks.discard)

    async def _safe_run(
        self, task_id: str, trigger_event: dict[str, Any] | None = None
    ) -> None:
        try:
            await self._runner.run(task_id, trigger_event=trigger_event)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            log.exception("TaskRunner crashed task=%s: %s", task_id, exc)

    async def shutdown(self) -> None:
        """Waits for runner tasks to finish (with a timeout). The caller must stop the loop first."""
        tasks = list(self._runner_tasks)
        if not tasks:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait(tasks, timeout=2.0)


# ----------------------------------------------------------------------
# Event passthrough + fire-once dedup helpers
# ----------------------------------------------------------------------

# Upper bound on the fire-once dedup map; oldest half is dropped on overflow.
_DEDUP_CAP = 4096


def _event_to_dict(event: Event) -> dict[str, Any]:
    """Flatten a frozen Event dataclass into a ``{field: value}`` dict.

    Shallow (no recursion) — matches the flat-fields contract that both the
    ``filter_expr`` evaluator and the runner's ``{field}`` templating rely on.
    """
    fields = getattr(event, "__dataclass_fields__", {})
    return {name: getattr(event, name, None) for name in fields}


def _dedup_key(task_id: str, event: Event) -> tuple[str, str] | None:
    """Identify the event's subject for fire-once dedup, or ``None`` to skip it.

    Today the only subject is a mission (``mission_id``); events without an
    identifying field are not deduped (every occurrence is a distinct trigger).
    """
    subject = getattr(event, "mission_id", None)
    if subject:
        return (task_id, str(subject))
    return None


# ----------------------------------------------------------------------
# Filter expression — safe evaluation
# ----------------------------------------------------------------------

def _match_filter(event: Event, filter_expr: str | None) -> bool:
    """Evaluates a filter expression against an event.

    Supported operators: ``==``, ``!=``, ``and``, ``or``, ``not``, plus
    access to plain field names (e.g. ``role``, ``text``).

    The implementation is AST-based (``ast.parse`` + ``ast.walk``) and
    accepts only a whitelist of node types — **no** ``eval()``, no
    attribute-access chains, no function calls.
    """
    if filter_expr is None or filter_expr.strip() == "":
        return True

    import ast

    allowed_nodes: tuple[type, ...] = (
        ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
        ast.Compare, ast.Eq, ast.NotEq, ast.Name, ast.Constant, ast.Load,
    )

    try:
        tree = ast.parse(filter_expr, mode="eval")
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            return False

    env: dict[str, Any] = {}
    # Build a flat namespace from the event's fields. Nested dicts are
    # deliberately NOT supported — the scope is top-level field equality.
    for field in getattr(event, "__dataclass_fields__", {}).keys():
        env[field] = getattr(event, field, None)

    return _eval_ast_node(tree.body, env)


def _eval_ast_node(node: Any, env: dict[str, Any]) -> Any:
    import ast

    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_ast_node(node.operand, env)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval_ast_node(v, env) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(_eval_ast_node(v, env) for v in node.values)
    if isinstance(node, ast.Compare):
        left = _eval_ast_node(node.left, env)
        for op, right in zip(node.ops, node.comparators, strict=False):
            right_val = _eval_ast_node(right, env)
            if isinstance(op, ast.Eq) and not (left == right_val):
                return False
            if isinstance(op, ast.NotEq) and not (left != right_val):
                return False
            left = right_val
        return True
    return False

