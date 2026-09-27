"""TaskScheduler — asyncio + heapq-based scheduler (ADR-0005).

Simple, two paths:

1. **Time-based** (``after_delay`` + ``at_time``) — a min-heap ordered by
   ``due_at_ns``. The ``run()`` loop pops every due entry and dispatches it to
   the ``TaskRunner`` before waiting for the next one.

2. **Event-based** (``on_event``) — the scheduler registers itself as a
   wildcard subscriber on the bus and dispatches every event class that an
   ``on_event`` task has recorded as its ``event_selector``.

Cron and calendar rules share the time heap. Listener lifecycles use independent asyncio tasks.
Everything runs on the main async loop — that is deliberate (ADR-0005).

The scheduler uses a ``CancelToken`` as its top-level abort condition
(ADR-0004). Running tasks get their own runner task, derived from this token.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import json
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    Event,
    TaskCompleted,
    TaskFailed,
    TaskScheduled,
    WorkflowActivationChanged,
    WorkflowCompleted,
)
from jarvis.core.misfire import is_missed, late_by_s
from jarvis.core.protocols import RoutineDeferred, current_trigger_path
from jarvis.tasks.hook_events import RoutineEventReceived
from jarvis.tasks.schema import PAUSABLE_TRIGGER_TYPES, TERMINAL_STATES, TaskSpec

if TYPE_CHECKING:
    from jarvis.control.cancel import CancelToken
    from jarvis.tasks.runner import TaskRunner
    from jarvis.tasks.store import TaskStore


log = logging.getLogger(__name__)


class TaskNotFound(LookupError):
    """No task row with that id."""


class TaskStateConflict(RuntimeError):
    """The requested transition is not allowed from the task's current
    state (or for its trigger type). Maps onto HTTP 409."""


class _Dispatchable(Protocol):
    async def run(
        self,
        task_id: str,
        cancel_token: CancelToken | None = None,
        *,
        trigger_event: dict[str, Any] | None = None,
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
        *,
        workflow_services: Any = None,
    ) -> None:
        from .source_runtime import SourceSupervisor

        self.sources = SourceSupervisor(store, self.receive_hook)
        self._store = store
        self._bus = bus
        self._runner = runner
        self._workflow_services = workflow_services
        # Heap entries: (due_at_ns, task_id). task_id as str is
        # comparable — heapq only uses it as a tiebreaker for identical
        # due_at_ns values.
        self._heap: list[tuple[int, str]] = []
        self._wakeup = asyncio.Event()
        # On-event tasks — map event class name → set of task_ids.
        # The wildcard subscriber checks the event class against this index.
        self._hook_running: set[str] = set()
        self._hook_retry_at: dict[str, float] = {}
        self._event_dispatch_lock = asyncio.Lock()
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
        # Per-run cancel tokens (H-03): cancel_task() fires the token of a
        # RUNNING task so its harness action stops; cleared in _safe_run.
        self._running_tokens: dict[str, Any] = {}

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
        await self._validate_source(spec)
        task_id = await self._store.insert(spec, trace_id=trace_id)
        row = await self._store.get(task_id)
        due_at_ns = row.get("due_at_ns") if row is not None else self._due_at_ns_for(spec)
        self._register_in_memory(spec, task_id, stored_due_at_ns=due_at_ns)
        # The event, heap and database must agree even across a clock boundary.
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

    async def _validate_source(self, spec: TaskSpec) -> None:
        if spec.action.kind == "workflow":
            services = self._workflow_services() if self._workflow_services else (None, None)
            if (
                services[0] is None
                or services[1] is None
                or await services[0].get_workflow(str(spec.action.workflow_id)) is None
            ):
                raise ValueError("Destination workflow is unavailable")
        if spec.trigger.type != "source" or spec.trigger.source.kind != "workflow":
            return
        source = spec.trigger.source
        if source.upstream_kind == "workflow":
            services = self._workflow_services() if self._workflow_services else (None, None)
            if services[0] is None or await services[0].get_workflow(source.upstream_id) is None:
                raise ValueError("Upstream workflow is unavailable")
            return
        cursor = source.upstream_id
        visited = {str(spec.id)}
        while cursor:
            if cursor in visited:
                raise ValueError("Workflow dependencies must not contain a cycle")
            visited.add(cursor)
            upstream = await self._store.get_spec(cursor)
            if upstream is None:
                raise ValueError("Upstream routine does not exist")
            trigger = upstream.trigger
            cursor = (
                trigger.source.upstream_id
                if trigger.type == "source"
                and trigger.source.kind == "workflow"
                and trigger.source.upstream_kind == "task"
                else ""
            )

    async def invoke_source(
        self, task_id: str, payload: dict[str, Any], delivery_id: str, *, mode: str
    ) -> str:
        from .source_schema import validate_form

        spec = await self._store.get_spec(task_id)
        if spec is None or spec.trigger.type != "source" or spec.trigger.source.kind != mode:
            raise ValueError("This routine does not accept that invocation mode")
        if mode == "form":
            validate_form(spec.trigger.source, payload)
        return await self.receive_hook(task_id, payload, delivery_id, source="source")

    async def _chain_event(self, event: Event) -> None:
        if isinstance(event, (TaskCompleted, TaskFailed, TaskScheduled)):
            kind, upstream = "task", event.task_id
            phase = (
                "activated"
                if isinstance(event, TaskScheduled)
                else "failed"
                if isinstance(event, TaskFailed)
                else "succeeded"
            )
        elif isinstance(event, (WorkflowCompleted, WorkflowActivationChanged)):
            kind, upstream = "workflow", event.workflow_id
            if isinstance(event, WorkflowActivationChanged) and not event.enabled:
                return
            phase = (
                "activated"
                if isinstance(event, WorkflowActivationChanged)
                else "succeeded"
                if event.success
                else "failed"
            )
        else:
            return
        targets = tuple(self._on_event_index.get(f"chain:{kind}:{upstream}:{phase}", ()))
        if not targets:
            return
        path = current_trigger_path.get()
        marker = kind + ":" + upstream
        if marker not in path:
            path = path + (marker,)
        output = ""
        output_ref = f"/api/tasks/{upstream}" if kind == "task" else f"/api/workflows/{upstream}"
        if kind == "task" and phase != "activated":
            results = await self._store.latest_agent_results([upstream], max_chars=16001)
            output = str(results.get(upstream, ""))
        elif isinstance(event, WorkflowCompleted) and self._workflow_services:
            services = self._workflow_services()
            if services[0] is not None:
                run = await services[0].get_run(event.run_id)
                steps = (run or {}).get("steps") or []
                output = str(steps[-1].get("output", "")) if steps else ""
                output_ref = f"/api/workflows/runs/{event.run_id}"
        payload = {
            "upstream_id": upstream,
            "status": phase,
            "output": output[:16000],
            "output_truncated": len(output) > 16000,
            "output_ref": output_ref,
            "error": getattr(event, "error", None),
        }
        for task_id in targets:
            try:
                status = await self.receive_hook(
                    task_id, payload, str(event.trace_id), source="source", lineage=path
                )
            except ValueError:
                status = "payload_invalid"
            if status not in {"queued", "duplicate", "filtered"}:
                await self._store.sources.status(task_id, "blocked", status)

    async def receive_hook(
        self,
        task_id: str,
        payload: dict[str, Any],
        delivery_id: str,
        *,
        source: str = "webhook",
        lineage: tuple[str, ...] = (),
    ) -> str:
        """Persist an authenticated delivery; the scheduler owns its execution."""
        if self._runner is None:
            return "unavailable"
        spec = await self._store.get_spec(task_id)
        if (
            spec is None
            or spec.trigger.type not in ("webhook", "event_hook", "source")
            or spec.trigger.type != source
        ):
            return "not_found"
        status = await self._store.hooks.accept(
            task_id, delivery_id, payload, spec.trigger, lineage
        )
        if status == "queued":
            self._wakeup.set()
        return status

    async def _enqueue_hook_event(self, event: RoutineEventReceived) -> dict[str, str]:
        statuses = {}
        payload = json.loads(event.payload_json)
        for tid in tuple(self._on_event_index.get("hook:" + event.event_name, ())):
            statuses[tid] = await self.receive_hook(
                tid, payload, event.delivery_id, source="event_hook"
            )
        return statuses

    async def emit_hook_event(
        self, event_name: str, payload: dict[str, Any], delivery_id: str
    ) -> dict[str, str]:
        from .hook_inbox import encode_payload

        event = RoutineEventReceived(
            event_name=event_name,
            payload_json=encode_payload(payload),
            delivery_id=delivery_id,
            source_layer="tasks.hooks.accepted",
        )
        # The API acknowledges durable admission, independently of the bus's
        # bounded best-effort observers. Publish only after saving the receipts.
        statuses = await self._enqueue_hook_event(event)
        await self._bus.publish(event)
        return statuses

    async def _drain_hooks(self) -> None:
        if self._runner is None:
            return
        for row in await self._store.hooks.pending():
            tid = str(row["task_id"])
            if self._hook_retry_at.get(tid, 0) > time.monotonic():
                continue
            if tid in self._hook_running or tid in self._running_tokens:
                continue
            self._hook_retry_at.pop(tid, None)
            self._hook_running.add(tid)
            task = asyncio.create_task(self._run_hook(row), name=f"routine-hook-{tid}")
            self._runner_tasks.add(task)
            task.add_done_callback(self._runner_tasks.discard)

    async def _run_hook(self, row: dict[str, Any]) -> None:
        tid, delivery = str(row["task_id"]), str(row["delivery_id"])
        try:
            current = await self._store.get(tid)
            if current is None or current["state"] != "scheduled":
                return
            await self._store.hooks.mark(tid, delivery, "running")
            await self._store.append_step(
                tid, "log", {"event": "hook_started", "delivery_id": delivery}
            )
            await self._safe_run(
                tid,
                {
                    "hook_payload": row["payload_json"],
                    "hook_delivery_id": delivery,
                    "__trigger_path": json.loads(row.get("lineage_json") or "[]"),
                },
            )
            current = await self._store.get(tid)
            failed = (
                current is None
                or bool(current.get("last_error"))
                or current["state"] in ("failed", "cancelled")
            )
            await self._store.hooks.mark(tid, delivery, "failed" if failed else "done")
            spec = await self._store.get_spec(tid)
            if spec is not None and spec.trigger.type in ("webhook", "event_hook", "source"):
                total, pending = await self._store.hooks.counts(tid)
                if (
                    spec.trigger.max_firings is not None
                    and total >= spec.trigger.max_firings
                    and pending == 0
                    and current is not None
                    and current["state"] == "scheduled"
                ):
                    await self._store.update_state(tid, "completed")
        except RoutineDeferred:
            await self._store.hooks.mark(tid, delivery, "pending")
            self._hook_retry_at[tid] = time.monotonic() + 2
        except asyncio.CancelledError:
            await self._store.hooks.mark(tid, delivery, "interrupted")
            raise
        except Exception:
            log.exception("Hook delivery failed for task %s", tid)
            await self._store.hooks.mark(tid, delivery, "failed")
        finally:
            self._hook_running.discard(tid)
            self._wakeup.set()

    async def run_now(self, task_id: str) -> None:
        """Run the task's action NOW, out of band — the schedule is untouched.

        A recurring (``every``) task keeps its heap entry and its
        ``due_at_ns``; the runner hands it back as ``scheduled`` afterwards,
        exactly like a regular firing minus the re-arm. A paused recurring
        task is restored to ``paused`` once the run settles. A one-shot task
        (``after_delay``/``at_time``) is consumed by the run: it leaves the
        heap so the scheduler cannot fire it a second time.

        Raises :class:`TaskNotFound` for an unknown id and
        :class:`TaskStateConflict` while the task is already ``running`` or
        already terminal.
        """
        task = await self._store.get(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        state = task["state"]
        if state == "running":
            raise TaskStateConflict(f"task is already running (state={state})")
        if state in TERMINAL_STATES:
            raise TaskStateConflict(f"task is already final (state={state})")
        if self._runner is None:
            raise TaskStateConflict("no runner attached — nothing can execute the task")
        spec = await self._store.get_spec(task_id)
        if spec is None:
            raise TaskNotFound(task_id)
        if spec.trigger.type == "source":
            raise TaskStateConflict("Use the configured trigger input for this routine")
        if spec.trigger.type in ("after_delay", "at_time"):
            self._remove_from_memory(task_id)
        restore_state = "paused" if state == "paused" else None
        await self._store.append_step(task_id, "log", {"event": "run_now"})
        task_obj = asyncio.create_task(
            self._run_now_and_settle(task_id, restore_state),
            name=f"task-run-now-{task_id}",
        )
        self._runner_tasks.add(task_obj)
        task_obj.add_done_callback(self._runner_tasks.discard)

    async def _run_now_and_settle(
        self,
        task_id: str,
        restore_state: str | None,
    ) -> None:
        await self._safe_run(task_id, None)
        if restore_state is None:
            return
        # The runner leaves a recurring task as `scheduled` (or `failed`);
        # a manual run must not silently switch a paused automation back on.
        try:
            task = await self._store.get(task_id)
            if task is not None and task["state"] not in ("running", "cancelled"):
                await self._store.update_state(task_id, restore_state)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            log.exception("run_now: could not restore state=%s for task=%s", restore_state, task_id)

    async def pause(self, task_id: str) -> None:
        """Switch a recurring/``on_event`` task off: it leaves the heap and
        the event index and its state becomes ``paused``. Idempotent.

        Raises :class:`TaskNotFound` / :class:`TaskStateConflict` (one-shot
        trigger, running, or terminal).
        """
        task = await self._store.get(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        if task["trigger_type"] not in PAUSABLE_TRIGGER_TYPES:
            raise TaskStateConflict(
                f"only recurring tasks can be paused (trigger={task['trigger_type']})"
            )
        state = task["state"]
        if state == "paused":
            return
        if state == "running":
            raise TaskStateConflict("task is running — wait for it to finish")
        if state in TERMINAL_STATES:
            raise TaskStateConflict(f"task is already final (state={state})")
        self._remove_from_memory(task_id)
        await self._store.update_state(task_id, "paused")
        await self._store.append_step(task_id, "log", {"event": "paused"})
        self._wakeup.set()

    async def update_task(self, task_id: str, spec: TaskSpec) -> None:
        """Replace a recurring schedule while keeping its identity and pause state."""
        current = await self._store.get(task_id)
        if current is None:
            raise TaskNotFound(task_id)
        if spec.trigger.type not in PAUSABLE_TRIGGER_TYPES:
            raise TaskStateConflict("Routine updates require a recurring trigger")
        await self._validate_source(spec)
        was_paused = current["state"] == "paused"
        await self.pause(task_id)
        try:
            await self._store.replace_spec(task_id, spec)
        finally:
            if not was_paused:
                await self.resume(task_id, notify_activation=False)
        await self._store.append_step(task_id, "log", {"event": "updated"})

    async def resume(
        self, task_id: str, *, now_ns: int | None = None, notify_activation: bool = True
    ) -> int | None:
        """Switch a paused task back on and re-register it for its NEXT
        occurrence. Returns the new ``due_at_ns`` (``None`` for ``on_event``).

        An ``every`` task anchored with ``start_at`` keeps its wall-clock
        anchor: the next due time is ``start_at + k * interval``, the first
        such moment strictly in the future. Without an anchor it is
        ``now + interval``.
        """
        task = await self._store.get(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        if task["state"] != "paused":
            raise TaskStateConflict(f"task is not paused (state={task['state']})")
        spec = await self._store.get_spec(task_id)
        if spec is None:
            raise TaskNotFound(task_id)
        now = time.time_ns() if now_ns is None else now_ns
        due: int | None = None
        if spec.trigger.type in ("every", "calendar", "cron"):
            due = next_every_due_ns(spec, now)
            await self._store.set_next_due(task_id, due)
        await self._store.update_state(task_id, "scheduled")
        await self._store.append_step(task_id, "log", {"event": "resumed"})
        self._register_in_memory(spec, task_id, stored_due_at_ns=due)
        if notify_activation:
            await self._bus.publish(
                TaskScheduled(task_id=task_id, trigger_type=spec.trigger.type, title=spec.title)
            )
        self._wakeup.set()
        return due

    def _remove_from_memory(self, task_id: str) -> None:
        """Drop a task from the heap, the event index and the known set."""
        self.sources.cancel(task_id)
        self._hook_retry_at.pop(task_id, None)
        self._heap = [(due, tid) for (due, tid) in self._heap if tid != task_id]
        heapq.heapify(self._heap)
        for ids in self._on_event_index.values():
            ids.discard(task_id)
        self._firings_left.pop(task_id, None)
        self._known.discard(task_id)

    async def cancel_task(self, task_id: str, reason: str = "user_cancel") -> bool:
        """Marks a task as ``cancelled`` if it isn't already terminal.

        Also removes it from the in-memory structures. A RUNNING task's
        per-run cancel token is fired too (deep-dive 2026-07-15, H-03): the
        runner races its harness stream against that token and stops the
        harness itself, so cancelling a running Computer-Use task no longer
        leaves desktop automation clicking until its own timeout. The global
        kill switch remains the emergency path for everything at once.
        """
        task = await self._store.get(task_id)
        if task is None:
            return False
        if task["state"] in TERMINAL_STATES:
            return False
        running_token = self._running_tokens.get(task_id)
        if running_token is not None:
            running_token.cancel(reason)
        # Linear scan over the heap — small enough, a typical queue is < 100.
        self._remove_from_memory(task_id)

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
        async with self._event_dispatch_lock:
            await self._dispatch_event(event)

    async def _dispatch_event(self, event: Event) -> None:
        """Wildcard handler: if an event class matches an ``on_event`` task,
        dispatch the runner fire-and-forget.

        The filter expression (``filter_expr``) is checked via ``_match_filter``
        during the runner dispatch — we do have access to the event here, but
        the spec lives in the DB, and we don't want to block in this handler.
        Hence: the handler enqueues, and the runner checks.
        """
        await self._chain_event(event)
        if isinstance(event, RoutineEventReceived):
            if event.source_layer != "tasks.hooks.accepted":
                await self._enqueue_hook_event(event)
            return
        cls_name = type(event).__name__
        task_ids = self._on_event_index.get(cls_name)
        if not task_ids:
            return
        # Snapshot the event's flat fields once — handed to the runner as the
        # ``trigger_event`` template context ({result_uri}, {status}, ...).
        event_ctx = _event_to_dict(event)
        # Copy, so the runner loop is allowed to modify the set.
        for tid in list(task_ids):
            if getattr(event, "task_id", None) == tid:
                continue  # A routine must not recursively trigger on its own lifecycle.
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
            maximum = spec.trigger.max_firings
            left = None if maximum is None else maximum - await self._store.event_firings(tid)
            if left is not None:
                if left <= 0:
                    task_ids.discard(tid)
                    self._firings_left.pop(tid, None)
                    continue
                self._firings_left[tid] = left - 1
            await self._store.append_step(tid, "log", {"event": "event_fired"})
            await self._dispatch_runner(tid, trigger_event=event_ctx)
            # If that was the last fire: clean up.
            if left is not None and left - 1 <= 0:
                task_ids.discard(tid)
                self._firings_left.pop(tid, None)
                self._known.discard(tid)
                try:
                    await self._store.update_state(tid, "completed")
                except Exception:  # noqa: BLE001
                    log.exception("max_firings cleanup: update_state failed for task_id=%s", tid)

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
        await self._store.hooks.recover()
        rows = await self._store.all_pending_scheduled()
        now_ns = time.time_ns()
        for row in rows:
            spec = await self._store.get_spec(row["id"])
            if spec is None:
                continue
            stored_due = row.get("due_at_ns")
            if (
                spec.trigger.type in ("every", "calendar", "cron")
                and stored_due is not None
                and is_missed(int(stored_due), now_ns)
            ):
                # BUG-212: a recurring slot that passed while the app was
                # down is missed, not caught up at boot. Skip to the next
                # occurrence on the task's own grid.
                stored_due = await self._skip_missed_every(
                    row["id"],
                    spec,
                    int(stored_due),
                    now_ns,
                )
            self._register_in_memory(spec, row["id"], stored_due_at_ns=stored_due)
        self._hydrated = True

    async def _skip_missed_every(
        self,
        task_id: str,
        spec: TaskSpec,
        due_ns: int,
        now_ns: int,
    ) -> int:
        """Record a missed recurring slot and return the next due time."""
        next_due = next_every_due_ns(spec, now_ns)
        late_s = late_by_s(due_ns, now_ns)
        log.warning(
            "Task %r missed its slot (%.0f min late) — skipped to the next "
            "occurrence, not caught up",
            spec.title,
            late_s / 60,
        )
        try:
            await self._store.append_step(
                task_id,
                "log",
                {
                    "event": "missed",
                    "due_at_ns": due_ns,
                    "late_by_s": int(late_s),
                    "next_due_at_ns": next_due,
                },
            )
        except Exception:  # noqa: BLE001
            log.exception("Could not record the missed slot for task %s", task_id)
        await self._store.set_next_due(task_id, next_due)
        return next_due

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
            due = (
                stored_due_at_ns
                if stored_due_at_ns is not None
                else time.time_ns() + int(trig.delay_seconds * 1e9)
            )
            heapq.heappush(self._heap, (due, task_id))
        elif trig.type == "at_time":
            due = (
                stored_due_at_ns
                if stored_due_at_ns is not None
                else parse_iso_timestamp_to_ns(trig.iso_timestamp)
            )
            heapq.heappush(self._heap, (due, task_id))
        elif trig.type == "cron":
            due = (
                stored_due_at_ns
                if stored_due_at_ns is not None
                else next_every_due_ns(spec, time.time_ns())
            )
            heapq.heappush(self._heap, (due, task_id))
        elif trig.type == "source":
            if trig.source.kind == "workflow":
                key = (
                    f"chain:{trig.source.upstream_kind}:{trig.source.upstream_id}:"
                    f"{trig.source.when}"
                )
                self._on_event_index.setdefault(key, set()).add(task_id)
            self.sources.start(spec)
        elif trig.type == "calendar":
            due = (
                stored_due_at_ns
                if stored_due_at_ns is not None
                else next_every_due_ns(spec, time.time_ns())
            )
            heapq.heappush(self._heap, (due, task_id))
        elif trig.type == "every":
            if stored_due_at_ns is not None:
                due = stored_due_at_ns
            elif trig.start_at:
                due = parse_iso_timestamp_to_ns(trig.start_at)
            else:
                due = time.time_ns() + int(trig.interval_seconds * 1e9)
            heapq.heappush(self._heap, (due, task_id))
        elif trig.type == "event_hook":
            self._on_event_index.setdefault("hook:" + trig.event_name, set()).add(task_id)
        elif trig.type == "on_event":
            self._on_event_index.setdefault(trig.event_name, set()).add(task_id)
            self._firings_left[task_id] = trig.max_firings  # None = unlimited
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
        if trig.type == "cron":
            return next_every_due_ns(spec, time.time_ns())
        if trig.type == "calendar":
            return next_every_due_ns(spec, time.time_ns())
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
            await self._drain_hooks()

            # Next wakeup: either the next-due time or indefinitely
            if self._heap:
                timeout = max(0.05, (self._heap[0][0] - now_ns) / 1e9)
            else:
                timeout = None
            if self._hook_retry_at:
                retry_in = max(0.1, min(self._hook_retry_at.values()) - time.monotonic())
                timeout = retry_in if timeout is None else min(timeout, retry_in)
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
            due, tid = heapq.heappop(self._heap)
            self._known.discard(tid)
            spec = await self._store.get_spec(tid)
            if spec is not None and spec.trigger.type in ("every", "calendar", "cron"):
                if is_missed(due, now_ns):
                    # The process lived but did not tick (machine asleep,
                    # loop blocked): same rule as at boot — skip, never
                    # catch up (BUG-212).
                    next_due = await self._skip_missed_every(tid, spec, due, now_ns)
                    heapq.heappush(self._heap, (next_due, tid))
                    self._known.add(tid)
                    continue
                await self._dispatch_runner(tid)
                await self._rearm_every(tid, spec, now_ns)
                continue
            await self._dispatch_runner(tid)

    async def _rearm_every(self, task_id: str, spec: TaskSpec, now_ns: int) -> None:
        """Re-insert a recurring task at its next occurrence and persist the
        new due time so a restart picks the schedule back up.

        Anchored tasks stay on their wall-clock grid (``start_at + k *
        interval``); ``now + interval`` used to be the rule for every task,
        so one late firing shifted a daily 07:30 automation permanently
        (BUG-212). Unanchored tasks still re-arm one interval from now.
        """
        next_due = next_every_due_ns(spec, now_ns)
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

    async def _safe_run(self, task_id: str, trigger_event: dict[str, Any] | None = None) -> None:
        # Per-run cancel token (deep-dive 2026-07-15, H-03): production runs
        # used to pass NO token, so the runner's cancel probes were no-ops and
        # a running harness action was unstoppable except via the global kill
        # switch. cancel_task() fires this token for a single running task.
        from jarvis.control.cancel import CancelToken  # noqa: PLC0415

        token = CancelToken()
        self._running_tokens[task_id] = token
        path = tuple((trigger_event or {}).get("__trigger_path") or current_trigger_path.get())
        context_token = current_trigger_path.set(path + ("task:" + task_id,))
        try:
            await self._runner.run(  # type: ignore[union-attr]
                task_id,
                token,
                trigger_event=trigger_event,
            )
        except RoutineDeferred:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("TaskRunner crashed task=%s: %s", task_id, exc)
        finally:
            self._running_tokens.pop(task_id, None)
            current_trigger_path.reset(context_token)

    async def shutdown(self) -> None:
        """Waits for runner tasks to finish (with a timeout).

        The caller must stop the loop first.
        """
        await self.sources.close()
        tasks = list(self._runner_tasks)
        if not tasks:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait(tasks, timeout=2.0)


# ----------------------------------------------------------------------
# Recurring schedule arithmetic
# ----------------------------------------------------------------------


def next_every_due_ns(spec: TaskSpec, now_ns: int) -> int:
    """Next due time of an ``every`` trigger strictly after ``now_ns``.

    ``start_at``-anchored tasks stay on their wall-clock grid
    (``start_at + k * interval``); unanchored ones fire one interval from now.
    A malformed ``start_at`` degrades to the unanchored rule.
    """
    trig = spec.trigger
    if trig.type == "cron":
        from .cron_schedule import next_cron_ns

        return next_cron_ns(trig.expression, trig.timezone, now_ns)
    if trig.type == "calendar":
        from .calendar import next_calendar_due_ns

        return next_calendar_due_ns(trig, now_ns)
    if trig.type != "every":
        raise ValueError(f"not a recurring trigger: {trig.type}")
    interval_ns = int(trig.interval_seconds * 1e9)
    start_at = trig.start_at
    if start_at:
        try:
            anchor = parse_iso_timestamp_to_ns(start_at)
        except ValueError:
            anchor = None
        if anchor is not None:
            if anchor > now_ns:
                return anchor
            k = (now_ns - anchor) // interval_ns + 1
            return anchor + k * interval_ns
    return now_ns + interval_ns


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
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Name,
        ast.Constant,
        ast.Load,
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
