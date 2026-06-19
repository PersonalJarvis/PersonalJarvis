"""WorkflowScheduler — Cron-basierter Auto-Trigger.

Analog zu ``jarvis.tasks.scheduler``, aber mit echtem Cron via ``croniter``.
Ein einziger ``asyncio``-Loop pollt die Workflow-Liste, berechnet das naechste
``next_run_at_ns`` pro aktivem Cron-Workflow und schlaeft bis zum frueheste
Termin. Beim Feuern: ``runner.trigger(workflow_id, trigger_reason="cron")``.

Das ist bewusst nicht dieselbe Code-Pfad-Architektur wie Skills-Cron
(``skills/trigger_matcher.run_cron_scheduler``) — Skills liefern einen
``AsyncIterator`` den der Supervisor konsumiert; hier schiesst der Scheduler
direkt auf den Runner.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

try:
    from croniter import croniter  # type: ignore
    _HAVE_CRONITER = True
except Exception:  # pragma: no cover
    croniter = None  # type: ignore
    _HAVE_CRONITER = False

from jarvis.core.bus import EventBus
from jarvis.core.events import WorkflowScheduled

if TYPE_CHECKING:
    from .runner import WorkflowRunner
    from .store import WorkflowStore


log = logging.getLogger(__name__)


class WorkflowScheduler:
    """Poll-Schleife — berechnet und feuert Cron-basierte Workflow-Runs."""

    def __init__(
        self,
        store: WorkflowStore,
        runner: WorkflowRunner,
        bus: EventBus,
    ) -> None:
        self._store = store
        self._runner = runner
        self._bus = bus
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------

    def start(self) -> None:
        """Startet den Hintergrund-Loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="workflow-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=2.0)
        self._task = None

    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Main-Loop — pollt, berechnet, schlaeft, triggert.

        Poll-Intervall: bei leerer Cron-Liste 60s, sonst bis zum naechsten
        faelligen Termin (mind. 1s, max 60s — damit neue Workflows via API
        binnen einer Minute aufgepickt werden).
        """
        while not self._stop.is_set():
            try:
                wait_s = await self._tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("WorkflowScheduler-Tick crashed: %s", exc)
                wait_s = 30.0

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait_s)
                return  # stop_set
            except TimeoutError:
                continue

    async def _tick(self) -> float:
        """Ein Tick: naechstes Cron-Event berechnen, triggern wenn faellig,
        bis zum naechsten Termin schlafen (Rueckgabe in Sekunden).
        """
        if not _HAVE_CRONITER:
            return 60.0

        now_ns = time.time_ns()
        rows = await self._store.list_workflows()

        due: list[tuple[int, str]] = []     # (next_run_at_ns, wid)
        upcoming_min_ns: int | None = None

        for row in rows:
            if not row.get("enabled"):
                continue
            if row.get("trigger_type") != "cron":
                continue
            cron_expr = row.get("cron_expression")
            if not cron_expr:
                continue
            wid = row["id"]

            stored_next = row.get("next_run_at_ns")
            if stored_next is None:
                stored_next = _compute_next_cron_ns(cron_expr, now_ns)
                if stored_next is None:
                    continue
                await self._store.set_next_run(wid, stored_next)
                await self._bus.publish(
                    WorkflowScheduled(
                        workflow_id=wid,
                        next_run_ns=stored_next,
                        reason="cron_next",
                        source_layer="workflows.scheduler",
                    )
                )

            if stored_next <= now_ns:
                due.append((stored_next, wid))
            else:
                if upcoming_min_ns is None or stored_next < upcoming_min_ns:
                    upcoming_min_ns = stored_next

        # Faellige Workflows triggern
        for _due_ns, wid in due:
            next_after = _compute_next_cron_ns(
                _cron_expr_for(rows, wid),
                now_ns + 60_000_000_000,  # mindestens 60s in Zukunft, um Drift
            )
            await self._store.set_next_run(wid, next_after)
            if next_after is not None:
                await self._bus.publish(
                    WorkflowScheduled(
                        workflow_id=wid,
                        next_run_ns=next_after,
                        reason="cron_next",
                        source_layer="workflows.scheduler",
                    )
                )
                if upcoming_min_ns is None or next_after < upcoming_min_ns:
                    upcoming_min_ns = next_after
            try:
                await self._runner.trigger(wid, trigger_reason="cron")
            except Exception as exc:  # noqa: BLE001
                log.warning("Cron-Trigger fuer %s fehlgeschlagen: %s", wid, exc)

        if upcoming_min_ns is None:
            return 60.0
        delta_s = max(1.0, (upcoming_min_ns - time.time_ns()) / 1e9)
        return min(delta_s, 60.0)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _compute_next_cron_ns(cron_expr: str, base_ns: int) -> int | None:
    """Naechste Fire-Time in ns oder None wenn Cron-Syntax kaputt."""
    if not _HAVE_CRONITER:
        return None
    try:
        base_dt = datetime.fromtimestamp(base_ns / 1e9).astimezone()
        it = croniter(cron_expr, base_dt)  # type: ignore[operator]
        nxt = it.get_next(datetime)
        return int(nxt.timestamp() * 1e9)
    except Exception:  # noqa: BLE001
        return None


def _cron_expr_for(rows: list[dict[str, Any]], wid: str) -> str:
    for r in rows:
        if r["id"] == wid:
            return r.get("cron_expression") or ""
    return ""
