"""Runner — dispatches a job to its handler and persists the run.

One runner = many concurrent runs. Each run is started as its own
``asyncio.Task``; the store gets the terminal-state updates. Observers
are informed via the ``on_event`` callback — so Jarvis (or any other
embed situation) can render live updates in the frontend without
depending on the Conductor package.

The same callback carries the one event a *person* cares about: ``job.news``
(:data:`conductor.core.notify.NEWS_EVENT`), emitted only when a job changes
state — it just started failing, or it just recovered. See ``notify.py`` for
why every-run chatter is deliberately not emitted, and why the payload is
structured facts rather than a finished sentence.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from ..jobs import HANDLERS
from .notify import NEWS_EVENT, classify_run, failures_to_announce
from .schema import JobSpec

if TYPE_CHECKING:
    from .store import ConductorStore


log = logging.getLogger(__name__)

#: Callback signature — (event_name, payload)
EventCallback = Callable[[str, dict[str, Any]], Any]


class Runner:
    """Runs a job end-to-end and persists everything."""

    def __init__(
        self,
        store: ConductorStore,
        on_event: EventCallback | None = None,
    ) -> None:
        self._store = store
        self._on_event = on_event
        # Job ids whose current breakage THIS runner announced as "failing".
        # A recovery is only news when the breakage was (see ``notify.py``);
        # in-memory on purpose — a "working again" about an outage nobody in
        # this session was told about is stale chatter, not news.
        self._failing_announced: set[str] = set()

    def set_callback(self, on_event: EventCallback) -> None:
        self._on_event = on_event

    # ------------------------------------------------------------------

    async def trigger(
        self,
        job_id: str,
        *,
        trigger: str = "manual",
        input_data: dict[str, Any] | None = None,
    ) -> str:
        """Starts a new run (fire-and-forget). Returns the run ID."""
        job_row = await self._store.get_job(job_id)
        if job_row is None:
            raise KeyError(f"Job {job_id} not found")
        run_id = await self._store.create_run(
            job_id, trigger=trigger, input_data=input_data,
        )
        asyncio.create_task(
            self._run(job_row, run_id, trigger, input_data or {}),
            name=f"conductor-run-{run_id[:8]}",
        )
        return run_id

    # ------------------------------------------------------------------

    async def _run(
        self,
        job_row: dict[str, Any],
        run_id: str,
        trigger: str,
        input_data: dict[str, Any],
    ) -> None:
        job_id = job_row["id"]
        spec_json = job_row["spec_json"]
        # The job's terminal state BEFORE this run — the only thing that makes
        # this run's outcome newsworthy or not (see ``notify.classify_run``).
        # Read from the row fetched at trigger time; the scheduler never
        # overlaps two runs of the same job, so it is the live value.
        previous_state = job_row.get("last_run_state")

        # Reconstruct JobSpec from JSON — we know the type from 'type'
        try:
            spec_data = json.loads(spec_json)
            # Pydantic's discriminator handles the type mapping itself.
            from pydantic import TypeAdapter
            spec = TypeAdapter(JobSpec).validate_python(spec_data)
        except Exception as exc:  # noqa: BLE001
            await self._store.update_run(
                run_id, state="failed",
                error=f"spec-deserialize: {exc}",
            )
            self._emit("run.failed", {
                "run_id": run_id, "job_id": job_id, "error": str(exc),
            })
            # A job whose spec no longer loads DID run and DID fail. Recording
            # that keeps the state machine honest — and makes the second
            # broken run silent instead of announcing the same breakage twice.
            await self._finish(
                job_row, run_id, trigger, previous_state,
                "failed", f"spec-deserialize: {exc}",
            )
            return

        handler = HANDLERS.get(spec.type)
        if handler is None:
            await self._store.update_run(
                run_id, state="failed",
                error=f"no handler for type={spec.type}",
            )
            self._emit("run.failed", {
                "run_id": run_id, "job_id": job_id,
                "error": f"unknown type {spec.type}",
            })
            await self._finish(
                job_row, run_id, trigger, previous_state,
                "failed", f"no handler for type={spec.type}",
            )
            return

        await self._store.update_run(run_id, state="running")
        self._emit("run.started", {
            "run_id": run_id, "job_id": job_id, "job_name": job_row["name"],
            "trigger": trigger, "type": spec.type,
        })

        start = time.perf_counter()
        try:
            result = await handler.execute(spec, input_data)
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - start) * 1000)
            await self._store.update_run(
                run_id, state="failed",
                error=f"{type(exc).__name__}: {exc}",
                metrics={"duration_ms": duration_ms},
            )
            self._emit("run.failed", {
                "run_id": run_id, "job_id": job_id, "error": str(exc),
                "duration_ms": duration_ms,
            })
            await self._finish(
                job_row, run_id, trigger, previous_state,
                "failed", f"{type(exc).__name__}: {exc}",
            )
            log.exception("Job %s run %s crashed", job_row["name"], run_id)
            return

        final_state = "completed" if result.success else "failed"
        await self._store.update_run(
            run_id,
            state=final_state,
            exit_code=result.exit_code,
            output=result.output,
            error=result.error,
            metrics=result.metrics,
        )
        self._emit("run.finished", {
            "run_id": run_id, "job_id": job_id,
            "state": final_state,
            "success": result.success,
            "exit_code": result.exit_code,
            "duration_ms": result.metrics.get("duration_ms", 0),
            "output_preview": (result.output or "")[:240],
        })
        await self._finish(
            job_row, run_id, trigger, previous_state, final_state, result.error,
        )

    # ------------------------------------------------------------------

    async def _finish(
        self,
        job_row: dict[str, Any],
        run_id: str,
        trigger: str,
        previous_state: str | None,
        new_state: str,
        error: str | None,
    ) -> None:
        """Record the job's terminal state and emit the news, if any.

        Order matters: the store is written first, so the state a subscriber
        reads back always matches the state it was told about.
        """
        job_id = job_row["id"]
        await self._store.set_last_run(job_id, time.time_ns(), new_state)
        # The run's own terminal state is already persisted (every caller
        # writes ``update_run`` first), so the streak read back includes it.
        streak = 0
        if new_state == "failed":
            try:
                streak = await self._store.failure_streak(job_id)
            except Exception as exc:  # noqa: BLE001 — counting must never
                # silence a real breakage; without a count, fall back to the
                # plain state change.
                log.warning("failure_streak(%s) failed: %s", job_id, exc)
                streak = 1 if previous_state != "failed" else 2
        news = classify_run(
            job_id=job_id,
            job_name=job_row.get("name") or "",
            run_id=run_id,
            trigger=trigger,
            previous_state=previous_state,
            new_state=new_state,
            error=error,
            failure_streak=max(1, streak),
            failures_required=failures_to_announce(
                job_row.get("schedule_type"), job_row.get("schedule_expr")
            ),
            failing_was_announced=job_id in self._failing_announced,
        )
        if new_state == "completed":
            self._failing_announced.discard(job_id)
        if news is None:
            return
        if news.kind == "failing":
            self._failing_announced.add(job_id)
        self._emit(NEWS_EVENT, news.as_payload())

    # ------------------------------------------------------------------

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._on_event is None:
            return
        try:
            res = self._on_event(event, payload)
            # An async callback is fine — we don't await it, but we start it.
            if asyncio.iscoroutine(res):
                asyncio.create_task(res)
        except Exception as exc:  # noqa: BLE001
            log.warning("Runner event-callback crashed: %s", exc)
