"""Runner — dispatcht einen Job an seinen Handler und persistiert den Run.

Ein Runner = viele gleichzeitige Runs. Jeder Run wird als eigener
``asyncio.Task`` gestartet; der Store bekommt die Terminal-State-
Updates. Observer werden per ``on_event``-Callback informiert — so kann
Jarvis (oder jede andere Embed-Situation) Live-Updates im Frontend
rendern ohne vom Conductor-Package abhaengig zu sein.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from ..jobs import HANDLERS
from .schema import JobSpec

if TYPE_CHECKING:
    from .store import ConductorStore


log = logging.getLogger(__name__)

#: Callback-Signatur — (event_name, payload)
EventCallback = Callable[[str, dict[str, Any]], Any]


class Runner:
    """Fuehrt einen Job-Run end-to-end aus und persistiert alles."""

    def __init__(
        self,
        store: ConductorStore,
        on_event: EventCallback | None = None,
    ) -> None:
        self._store = store
        self._on_event = on_event

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
        """Startet einen neuen Run (fire-and-forget). Returnt die Run-ID."""
        job_row = await self._store.get_job(job_id)
        if job_row is None:
            raise KeyError(f"Job {job_id} nicht gefunden")
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

        # JobSpec aus JSON zurueckbauen — wir wissen den Typ aus 'type'
        try:
            spec_data = json.loads(spec_json)
            # Pydantic-Discriminator macht die Typ-Zuordnung selbst.
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
            return

        handler = HANDLERS.get(spec.type)
        if handler is None:
            await self._store.update_run(
                run_id, state="failed",
                error=f"kein Handler fuer type={spec.type}",
            )
            self._emit("run.failed", {
                "run_id": run_id, "job_id": job_id,
                "error": f"unknown type {spec.type}",
            })
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
            await self._store.set_last_run(job_id, time.time_ns(), "failed")
            self._emit("run.failed", {
                "run_id": run_id, "job_id": job_id, "error": str(exc),
                "duration_ms": duration_ms,
            })
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
        await self._store.set_last_run(job_id, time.time_ns(), final_state)
        self._emit("run.finished", {
            "run_id": run_id, "job_id": job_id,
            "state": final_state,
            "success": result.success,
            "exit_code": result.exit_code,
            "duration_ms": result.metrics.get("duration_ms", 0),
            "output_preview": (result.output or "")[:240],
        })

    # ------------------------------------------------------------------

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._on_event is None:
            return
        try:
            res = self._on_event(event, payload)
            # Async callback ist ok — wir warten nicht, aber starten ihn.
            if asyncio.iscoroutine(res):
                asyncio.create_task(res)
        except Exception as exc:  # noqa: BLE001
            log.warning("Runner event-callback crashed: %s", exc)
