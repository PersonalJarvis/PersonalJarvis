"""WorkflowScheduler — cron-based auto-trigger.

Analogous to ``jarvis.tasks.scheduler``, but with real cron via ``croniter``.
A single ``asyncio`` loop polls the workflow list, computes the next
``next_run_at_ns`` for each active cron workflow, and sleeps until the
earliest one. On firing: ``runner.trigger(workflow_id, trigger_reason="cron")``.

A slot that passed while the app was not running is NOT caught up: past the
shared misfire grace (``jarvis.core.misfire``) it is recorded as a ``missed``
run and the schedule continues from the next occurrence (BUG-212).

This is deliberately not the same code-path architecture as skills-cron
(``skills/trigger_matcher.run_cron_scheduler``) — skills yield an
``AsyncIterator`` that the supervisor consumes; here the scheduler fires
directly at the runner.
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
from jarvis.core.events import AnnouncementRequested, WorkflowScheduled
from jarvis.core.misfire import is_missed, late_by_s
from jarvis.voice.action_phrases import action_phrase, resolve_ambient_language

from .runner import FailureAnnouncer

if TYPE_CHECKING:
    from .runner import WorkflowRunner
    from .store import WorkflowStore


log = logging.getLogger(__name__)

#: :class:`FailureAnnouncer` key for the poll loop itself, as opposed to one
#: workflow. Not a workflow id, and no id can collide with it.
_LOOP_KEY = "*scheduler-loop*"


class WorkflowScheduler:
    """Poll loop — computes and fires cron-based workflow runs."""

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
        #: Keyed by workflow id, plus ``_LOOP_KEY`` for the loop itself.
        self._failures = FailureAnnouncer()

    # ------------------------------------------------------------------

    def start(self) -> None:
        """Starts the background loop. Idempotent."""
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
        """Main loop — polls, computes, sleeps, triggers.

        Poll interval: 60s when the cron list is empty, otherwise until the
        next due time (min 1s, max 60s — so new workflows added via the API
        are picked up within a minute).
        """
        while not self._stop.is_set():
            try:
                wait_s = await self._tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("WorkflowScheduler tick crashed: %s", exc)
                wait_s = 30.0
                # A crashed tick means NO scheduled routine fires — every one
                # of them silently stops happening. The loop retries by itself,
                # but the user has to learn that their routines are down from
                # something other than a log file (AU-12).
                await self._announce(_LOOP_KEY, "workflow_scheduler_stalled")
            else:
                self._failures.clear(_LOOP_KEY)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait_s)
                return  # stop_set
            except TimeoutError:
                continue

    async def _tick(self) -> float:
        """One tick: computes the next cron event, triggers when due,
        sleeps until the next due time (returns in seconds).
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
                if is_missed(stored_next, now_ns):
                    # BUG-212: the slot passed while the app was not running.
                    # Catching it up hours later is what put the 07:30
                    # Morning Briefing at 15:04 and 20:49. Record the miss,
                    # skip to the next occurrence from NOW, never run it.
                    next_after = await self._skip_missed(
                        wid, cron_expr, stored_next, now_ns,
                        name=_name_for(rows, wid),
                    )
                    if next_after is not None and (
                        upcoming_min_ns is None or next_after < upcoming_min_ns
                    ):
                        upcoming_min_ns = next_after
                    continue
                due.append((stored_next, wid))
            else:
                if upcoming_min_ns is None or stored_next < upcoming_min_ns:
                    upcoming_min_ns = stored_next

        # Trigger due workflows
        for _due_ns, wid in due:
            next_after = _compute_next_cron_ns(
                _cron_expr_for(rows, wid),
                now_ns + 60_000_000_000,  # at least 60s in the future, to avoid drift
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
                log.warning("Cron trigger for %s failed: %s", wid, exc)
                # The routine was due and did not even start. Only the log knew
                # (AU-12); now the user does. The next fire time is already
                # stored above, so "I'll try again" is honest.
                await self._announce(
                    wid, "workflow_trigger_failed",
                    name=_name_for(rows, wid),
                )
            else:
                self._failures.clear(wid)

        if upcoming_min_ns is None:
            return 60.0
        delta_s = max(1.0, (upcoming_min_ns - time.time_ns()) / 1e9)
        return min(delta_s, 60.0)

    async def _skip_missed(
        self,
        wid: str,
        cron_expr: str,
        stored_next: int,
        now_ns: int,
        *,
        name: str,
    ) -> int | None:
        """Record a missed slot and move the workflow to its next occurrence.

        Returns the new ``next_run_at_ns`` (``None`` for a broken cron
        expression). The missed run row is best effort: a store without
        ``record_missed_run`` (older fakes) still gets rescheduled, so a
        bookkeeping failure can never turn back into a catch-up firing.
        """
        late_s = late_by_s(stored_next, now_ns)
        log.warning(
            "Workflow %r missed its slot at %s (%.0f min late) — skipped, "
            "not caught up",
            name, _iso_local(stored_next), late_s / 60,
        )
        record = getattr(self._store, "record_missed_run", None)
        if callable(record):
            try:
                await record(wid, due_at_ns=stored_next, late_by_s=late_s)
            except Exception:  # noqa: BLE001
                log.exception("Could not record the missed run for workflow %s", wid)
        next_after = _compute_next_cron_ns(cron_expr, now_ns)
        await self._store.set_next_run(wid, next_after)
        if next_after is not None:
            await self._bus.publish(
                WorkflowScheduled(
                    workflow_id=wid,
                    next_run_ns=next_after,
                    reason="missed",
                    source_layer="workflows.scheduler",
                )
            )
        return next_after

    # ------------------------------------------------------------------

    async def _announce(self, key: str, phrase_key: str, **fmt: object) -> None:
        """Tell the user a scheduled routine did not happen.

        Same path as every other background result
        (``AnnouncementRequested(kind="subagent")``, as in
        ``jarvis/tasks/runner.py:225``) — it survives the voice hangup gate and
        reaches browser tabs, so a headless runtime still reports. Rate-limited
        per ``key`` by :class:`~jarvis.workflows.runner.FailureAnnouncer`: a
        routine that fails every minute is said once, not sixty times an hour.
        """
        if not self._failures.should_speak(key):
            log.info(
                "Scheduler failure %s repeated within the announce cooldown — "
                "reported once already, staying quiet", key,
            )
            return
        lang = resolve_ambient_language()
        try:
            await self._bus.publish(
                AnnouncementRequested(
                    text=action_phrase(phrase_key, lang, **fmt),
                    language=lang,
                    kind="subagent",
                    source_layer="workflows.scheduler",
                )
            )
        except Exception:  # noqa: BLE001
            # A dead bus must not turn one failed routine into a claim that the
            # whole scheduler is down (this runs inside _tick, whose caller
            # announces exactly that). Leave a trace and carry on.
            log.exception("Scheduler failure announcement could not be published")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _iso_local(ns: int) -> str:
    """Local wall-clock rendering of a ns timestamp for log lines."""
    try:
        return datetime.fromtimestamp(ns / 1e9).astimezone().isoformat(timespec="minutes")
    except (OverflowError, OSError, ValueError):
        return str(ns)


def _compute_next_cron_ns(cron_expr: str, base_ns: int) -> int | None:
    """Next fire time in ns, or None if the cron syntax is broken."""
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


def _name_for(rows: list[dict[str, Any]], wid: str) -> str:
    """The routine's own user-given name for a SPOKEN report.

    Never the id — an id read aloud tells the user nothing about which of their
    routines broke. A nameless row degrades to the localized generic noun rather
    than leaking the id as a substitute.
    """
    for r in rows:
        if r["id"] == wid:
            name = str(r.get("name") or "").strip()
            if name:
                return name
            break
    return action_phrase("workflow_unnamed", resolve_ambient_language())
