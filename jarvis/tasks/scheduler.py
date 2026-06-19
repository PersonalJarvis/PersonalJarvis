"""TaskScheduler — asyncio + heapq-basierter Scheduler (ADR-0005).

Einfach, zwei Paths:

1. **Zeit-basiert** (``after_delay`` + ``at_time``) — ein min-heap nach
   ``due_at_ns``. Die ``run()``-Schleife poppt alle faelligen Eintraege und
   dispatcht sie an den ``TaskRunner``, bevor sie auf den naechsten wartet.

2. **Event-basiert** (``on_event``) — der Scheduler registriert sich als
   wildcard-subscriber am Bus und dispatcht jede Event-Klasse, die ein
   ``on_event``-Task als ``event_selector`` hinterlegt hat.

**Keine** Cron-Semantik, **kein** APScheduler, **kein** zweiter Thread.
Alles laeuft im Main-Async-Loop — das ist mit Absicht (ADR-0005).

Der Scheduler nutzt einen ``CancelToken`` als oberste Abbruch-Bedingung
(ADR-0004). Laufende Tasks bekommen einen eigenen Runner-Task, die von
diesem Token abgeleitet sind.
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
    async def run(self, task_id: str) -> None: ...


def parse_iso_timestamp_to_ns(iso: str) -> int:
    """Konvertiert ISO-8601 (optional mit ``Z``) in UTC-Nanosekunden.

    Local-Time ohne TZ wird als System-Zone interpretiert — das entspricht
    dem TriggerAtTime-Kontrakt (siehe schema.py).
    """
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int(dt.timestamp() * 1e9)


class TaskScheduler:
    """Lightweight asyncio-Scheduler.

    Lifecycle:
    1. ``__init__`` — Konstruktor, bindet an Bus + Store + Runner.
    2. ``bind_bus()`` — subscribe_all fuer Event-Dispatch (optional; kann
       bei Bedarf spaeter aufgerufen werden).
    3. ``run(cancel_token)`` — Main-Loop. Hydratisiert aus DB, schlaeft,
       dispatcht, wartet. Beendet beim Token-Cancel.
    4. ``schedule(spec)`` — extern gerufen, wenn das UI einen neuen Task
       anlegt. Schreibt in den Store + in den Heap + wakeup-Event.
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
        # Heap-Eintraege: (due_at_ns, task_id). task_id als str ist
        # vergleichbar — heapq nutzt das nur als Tiebreaker bei identischen
        # due_at_ns.
        self._heap: list[tuple[int, str]] = []
        self._wakeup = asyncio.Event()
        # On-Event-Tasks — Map Event-Klassenname → set von task_ids.
        # Wildcard-Subscriber checked die Event-Klasse gegen diesen Index.
        self._on_event_index: dict[str, set[str]] = {}
        self._bound = False
        self._hydrated = False
        # Track bereits registrierte Task-IDs — verhindert doppelte Heap-Eintraege
        # bei Race zwischen ``hydrate()`` und ``schedule()``.
        self._known: set[str] = set()
        # H10-Fix: max_firings wird pro Task in-memory mitgezaehlt.
        # None = unbegrenzt. Beim on_event-Match wird dekrementiert;
        # bei 0 fliegt der Task aus dem Index und wird als "completed"
        # markiert.
        self._firings_left: dict[str, int | None] = {}
        # Pending Runner-Tasks, damit wir beim Cancel aufraeumen koennen.
        self._runner_tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------
    # Runner-Wiring (DI — der Runner kann nach Konstruktor gesetzt werden)
    # ------------------------------------------------------------------

    def attach_runner(self, runner: _Dispatchable | TaskRunner) -> None:
        self._runner = runner

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def schedule(self, spec: TaskSpec, *, trace_id: str | None = None) -> str:
        """Persistiert Spec, fuegt dem Heap/Event-Index zu, weckt den Loop."""
        task_id = await self._store.insert(spec, trace_id=trace_id)
        self._register_in_memory(spec, task_id)
        # TaskScheduled-Event zur Transparenz (UI listet)
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
        """Markiert einen Task als ``cancelled``, wenn er noch nicht final ist.

        Entfernt ihn auch aus den In-Memory-Strukturen. Laufende Runner
        werden NICHT direkt unterbrochen — dafuer haben wir den
        KillSwitch-Pfad. Hier ist der Use-Case: "Task war scheduled, User
        drueckt X."
        """
        task = await self._store.get(task_id)
        if task is None:
            return False
        if task["state"] in ("completed", "failed", "cancelled", "interrupted"):
            return False
        # Aus Heap entfernen (linear scan, klein genug — typische Queue < 100).
        self._heap = [(due, tid) for (due, tid) in self._heap if tid != task_id]
        heapq.heapify(self._heap)
        # Aus Event-Index loeschen
        for ids in self._on_event_index.values():
            ids.discard(task_id)
        self._known.discard(task_id)

        await self._store.update_state(task_id, "cancelled", error=reason)
        await self._store.append_step(task_id, "log", {"event": "cancelled", "reason": reason})
        # Event auf dem Bus
        from jarvis.core.events import TaskCancelled
        await self._bus.publish(
            TaskCancelled(task_id=task_id, reason=reason, source_layer="tasks.scheduler")
        )
        self._wakeup.set()
        return True

    # ------------------------------------------------------------------
    # Event-Bus Wiring
    # ------------------------------------------------------------------

    def bind_bus(self) -> None:
        """Registriert den Wildcard-Handler fuer ``on_event``-Dispatch."""
        if self._bound:
            return
        self._bus.subscribe_all(self._on_any_event)
        self._bound = True

    async def _on_any_event(self, event: Event) -> None:
        """Wildcard-Handler: wenn eine Event-Klasse einem ``on_event``-Task
        entspricht, den Runner fire-and-forget dispatchen.

        Filter-Expression (``filter_expr``) wird via ``_match_filter`` im
        Runner-Dispatch gecheckt — wir haetten hier zwar Zugriff auf das
        Event, aber die Spec ist in der DB, und wir wollen in diesem Handler
        nicht blockieren. Deshalb: Handler enqueues + Runner prueft.
        """
        cls_name = type(event).__name__
        task_ids = self._on_event_index.get(cls_name)
        if not task_ids:
            return
        # Copy, damit der Runner-Loop die Menge modifizieren darf.
        for tid in list(task_ids):
            # Filter-Matching hier vorziehen — das spart einen Runner-
            # Launch bei Events, die offensichtlich nicht passen.
            spec = await self._store.get_spec(tid)
            if spec is None:
                task_ids.discard(tid)
                continue
            if spec.trigger.type != "on_event":
                # Defensive: sollte nie passieren, aber wenn ja, raus damit.
                task_ids.discard(tid)
                continue
            if not _match_filter(event, spec.trigger.filter_expr):
                continue
            # H10-Fix: max_firings in-memory tracken. Wenn 0 → Task aus
            # Index entfernen, auf "completed" setzen, NICHT dispatchen.
            left = self._firings_left.get(tid)
            if left is not None:
                if left <= 0:
                    task_ids.discard(tid)
                    self._firings_left.pop(tid, None)
                    continue
                self._firings_left[tid] = left - 1
            await self._dispatch_runner(tid)
            # Wenn das der letzte Fire war: cleanup.
            if left is not None and left - 1 <= 0:
                task_ids.discard(tid)
                self._firings_left.pop(tid, None)
                self._known.discard(tid)
                try:
                    await self._store.update_state(tid, "completed")
                except Exception:  # noqa: BLE001
                    log.exception("max_firings cleanup: update_state fehlgeschlagen "
                                  "fuer task_id=%s", tid)

    # ------------------------------------------------------------------
    # Hydration
    # ------------------------------------------------------------------

    async def hydrate(self) -> None:
        """Liest alle ``scheduled`` Tasks und baut Heap + Event-Index wieder auf.

        Idempotent: zweiter Aufruf ist no-op. Schuetzt davor, dass
        ``run()``-intern und ein expliziter ``await hydrate()`` einen Task
        doppelt registrieren.

        H9-Fix: Der in der DB gespeicherte ``due_at_ns`` wird **direkt**
        genutzt statt neu zu rechnen. Sonst wuerde ein "in 30s" nach einem
        20s-Crash erneut 30s warten (insgesamt 50s statt 30s).
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
        """Fuegt einen Task dem Heap oder dem Event-Index zu — idempotent.

        Wenn ``stored_due_at_ns`` gegeben ist (Hydration-Pfad), wird der
        persistierte Wert genutzt; sonst berechnet der Trigger neu
        (Schedule-Pfad bei Neu-Inserts).
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
            self._firings_left[task_id] = trig.max_firings   # None = unbegrenzt
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
    # Main-Loop
    # ------------------------------------------------------------------

    async def run(self, cancel_token: CancelToken | None = None) -> None:
        """Main-Loop — laeuft bis ``cancel_token.is_cancelled()``.

        Typische Verwendung aus dem DesktopApp/Orchestrator:

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

            # Naechstes Wake-up: entweder next-due oder unendlich
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

    async def _dispatch_runner(self, task_id: str) -> None:
        if self._runner is None:
            log.warning("TaskScheduler.run: kein Runner attached, Task %s faellt durch", task_id)
            return
        task = asyncio.create_task(self._safe_run(task_id), name=f"task-runner-{task_id}")
        self._runner_tasks.add(task)
        task.add_done_callback(self._runner_tasks.discard)

    async def _safe_run(self, task_id: str) -> None:
        try:
            await self._runner.run(task_id)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            log.exception("TaskRunner crashed task=%s: %s", task_id, exc)

    async def shutdown(self) -> None:
        """Wartet auf Runner-Tasks ab (mit Timeout). Aufrufer stoppt vorher den Loop."""
        tasks = list(self._runner_tasks)
        if not tasks:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait(tasks, timeout=2.0)


# ----------------------------------------------------------------------
# Filter-Expression — sichere Auswertung
# ----------------------------------------------------------------------

def _match_filter(event: Event, filter_expr: str | None) -> bool:
    """Wertet eine Filter-Expression gegen ein Event aus.

    Unterstuetzte Operatoren: ``==``, ``!=``, ``and``, ``or``, ``not``,
    sowie Zugriff auf einfache Feld-Namen (z.B. ``role``, ``text``).

    Die Implementierung ist AST-basiert (``ast.parse`` + ``ast.walk``),
    akzeptiert nur eine Whitelist von Node-Typen — **kein** ``eval()``,
    keine Attribute-Access-Chain, keine Function-Calls.
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
    # Bau ein flaches Namespace aus den Event-Feldern. Nested Dicts werden
    # bewusst NICHT unterstuetzt — der Scope ist Feld-Gleichheit auf top-level.
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


