"""Scheduler — poll-based cron+interval execution.

The simplest possible functional design: an asyncio loop that, on each
tick, inspects all active jobs and checks which ``next_run_at_ns`` is
due. No dedicated heap structure — the job count per Conductor instance
realistically stays below 100, and at that size the SQLite query is
not the bottleneck.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

try:
    from croniter import croniter  # type: ignore
    _HAVE_CRONITER = True
except Exception:  # pragma: no cover
    croniter = None  # type: ignore
    _HAVE_CRONITER = False

if TYPE_CHECKING:
    from .runner import Runner
    from .store import ConductorStore


log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, store: ConductorStore, runner: Runner) -> None:
        self._store = store
        self._runner = runner
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="conductor-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=2.0)
        self._task = None

    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                wait_s = await self._tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("Scheduler tick crashed: %s", exc)
                wait_s = 30.0
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait_s)
                return
            except TimeoutError:
                continue

    async def _tick(self) -> float:
        now_ns = time.time_ns()
        rows = await self._store.list_jobs()

        due_ids: list[str] = []
        upcoming_min: int | None = None

        for row in rows:
            if not row.get("enabled"):
                continue
            sched_type = row.get("schedule_type")
            if sched_type not in ("cron", "interval"):
                continue
            expr = row.get("schedule_expr") or ""
            stored_next = row.get("next_run_at_ns")

            if stored_next is None:
                stored_next = _compute_next(sched_type, expr, now_ns)
                if stored_next is None:
                    continue
                await self._store.set_next_run(row["id"], stored_next)

            if stored_next <= now_ns:
                due_ids.append(row["id"])
                # Set a new next_run_at_ns before we trigger, so slow
                # execution doesn't cause a double trigger.
                next_after = _compute_next(
                    sched_type, expr,
                    max(now_ns + 60_000_000_000, stored_next),
                )
                await self._store.set_next_run(row["id"], next_after)
                if next_after and (upcoming_min is None or next_after < upcoming_min):
                    upcoming_min = next_after
            else:
                if upcoming_min is None or stored_next < upcoming_min:
                    upcoming_min = stored_next

        for jid in due_ids:
            trigger = "cron" if self._sched_type(rows, jid) == "cron" else "interval"
            try:
                await self._runner.trigger(jid, trigger=trigger)
            except Exception as exc:  # noqa: BLE001
                log.warning("Scheduler trigger for %s failed: %s", jid, exc)

        if upcoming_min is None:
            return 30.0
        delta = max(1.0, (upcoming_min - time.time_ns()) / 1e9)
        return min(delta, 30.0)

    @staticmethod
    def _sched_type(rows: list[dict], jid: str) -> str:
        for r in rows:
            if r["id"] == jid:
                return r.get("schedule_type") or ""
        return ""


# ----------------------------------------------------------------------
# Next-run computation
# ----------------------------------------------------------------------

def _compute_next(sched_type: str, expr: str, base_ns: int) -> int | None:
    if sched_type == "cron":
        if not _HAVE_CRONITER or not expr:
            return None
        try:
            base_dt = datetime.fromtimestamp(base_ns / 1e9).astimezone()
            it = croniter(expr, base_dt)  # type: ignore[operator]
            nxt = it.get_next(datetime)
            return int(nxt.timestamp() * 1e9)
        except Exception:  # noqa: BLE001
            return None
    if sched_type == "interval":
        try:
            seconds = int(expr)
        except ValueError:
            return None
        return base_ns + seconds * 1_000_000_000
    return None
