"""Heavy-Duty Worker for the Optimistic Execution prototype.

The worker is the background counterpart to the Talker. It:

1. Subscribes to ``MissionSpawn`` events on the EventBus.
2. When a mission arrives, schedules async work as an ``asyncio.Task`` and
   **returns immediately** — this is the entire point: the Talker is never
   blocked waiting for a long MCP round-trip (AD-OE1, AD-OE2).
3. Inside the task, calls ``SmartTool.execute`` and publishes either
   ``WorkerCompleted`` (success) or ``WorkerCorrectionNeeded`` (any failure).
4. Never lets an exception escape the task (AP-18 parity — one bad handler
   must not break the pipeline).

The bus is used only via duck-typing (.subscribe, async .publish) so the
worker stays fully independent of the concrete EventBus implementation and can
be tested against FakeBus without any production dependency.
"""
from __future__ import annotations

import asyncio
import logging

import optimistic.tools as _tools_mod
from optimistic.events import (
    CorrectionReason,
    MissionSpawn,
    WorkerCompleted,
    WorkerCorrectionNeeded,
    WorkerStarted,
)
from optimistic.tools import MissingInfoError

_log = logging.getLogger("optimistic.worker")


class HeavyDutyWorker:
    """Asynchronous background worker.

    Listens for ``MissionSpawn`` events, delegates each mission to a ``SmartTool``
    inside its own ``asyncio.Task``, and publishes outcome events back on the bus.

    Thread-safety note: all interactions happen inside the same asyncio event loop.
    No locks are needed — task scheduling and the ``_tasks`` set are event-loop-local.
    """

    def __init__(self, bus) -> None:
        self._bus = bus
        # Set of running asyncio.Tasks. Tasks remove themselves via a done-callback
        # so the set stays small and finished tasks don't leak memory.
        self._tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]
        bus.subscribe(MissionSpawn, self._on_mission_spawn)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def in_flight(self) -> int:
        """Number of mission tasks that have not yet finished."""
        return len(self._tasks)

    async def drain(self) -> None:
        """Await all currently in-flight mission tasks.

        Callers (typically tests) use this to wait for all background work to
        settle before inspecting the event log.
        """
        if not self._tasks:
            return
        # asyncio.gather on a snapshot; new tasks spawned during drain are not
        # included, but that is the correct semantics for a "wait until quiet" helper.
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    async def _on_mission_spawn(self, ev: MissionSpawn) -> None:
        """Handle a MissionSpawn event.

        This method MUST return immediately after scheduling the task so that the
        Talker (and the EventBus fan-out) are never blocked on heavy async work
        (AD-OE2). The actual execution happens inside ``_run`` via
        ``asyncio.create_task``.
        """
        task = asyncio.create_task(self._run(ev))
        self._tasks.add(task)
        # Remove the task from the set when it finishes so we don't accumulate
        # references. The discard is safe because the done-callback fires in the
        # same event loop after the task completes or raises.
        task.add_done_callback(self._tasks.discard)

    async def _run(self, ev: MissionSpawn) -> None:
        """Execute one mission inside its own task.

        Steps (spec-mandated order):
        1. Publish ``WorkerStarted``.
        2. Log at INFO level.
        3. Obtain the appropriate SmartTool via ``get_smart_tool``.
        4. Call ``tool.execute``; on success publish ``WorkerCompleted``.
        5. On ``MissingInfoError`` publish ``WorkerCorrectionNeeded(reason, detail)``.
        6. On any other exception: ONE silent retry; if that also fails, publish
           ``WorkerCorrectionNeeded(FATAL, ...)``.

        This coroutine MUST NOT let any exception escape — a task that raises
        unhandled would be silently swallowed by the event loop (or logged as
        "Task exception was never retrieved") rather than surfacing cleanly.
        """
        # Step 1: announce that the worker has picked up this mission.
        await self._bus.publish(
            WorkerStarted(
                mission_id=ev.mission_id,
                tool_name=ev.tool_name,
                trace_id=ev.trace_id,
            )
        )

        # Step 2: structured log so operators can follow mission progress.
        _log.info(
            "Heavy-Duty-Worker processing task %s: %s",
            ev.mission_id,
            ev.command,
        )

        # Step 3: get the tool — this is synchronous and fast.
        # Access via module reference so mock.patch.object on _tools_mod works in tests.
        tool = _tools_mod.get_smart_tool(ev.tool_name)

        # Steps 4-6: execute with retry logic.
        try:
            result = await tool.execute(ev.command, ev.context)
            # Success path.
            await self._bus.publish(
                WorkerCompleted(
                    mission_id=ev.mission_id,
                    result=result,
                    trace_id=ev.trace_id,
                )
            )

        except MissingInfoError as exc:
            # Recoverable — tell the OopsProtocol what is missing.
            await self._bus.publish(
                WorkerCorrectionNeeded(
                    mission_id=ev.mission_id,
                    reason=exc.reason,
                    detail=exc.detail,
                    command=ev.command,
                    trace_id=ev.trace_id,
                )
            )

        except Exception as exc:
            # Generic failure — one silent retry before giving up.
            _log.warning(
                "Worker task %s failed (%s), retrying once...",
                ev.mission_id,
                exc,
            )
            try:
                result = await tool.execute(ev.command, ev.context)
                # Retry succeeded.
                await self._bus.publish(
                    WorkerCompleted(
                        mission_id=ev.mission_id,
                        result=result,
                        trace_id=ev.trace_id,
                    )
                )
            except Exception as retry_exc:
                # Both attempts failed — publish a FATAL correction event.
                _log.error(
                    "Worker task %s failed after retry: %s",
                    ev.mission_id,
                    retry_exc,
                )
                await self._bus.publish(
                    WorkerCorrectionNeeded(
                        mission_id=ev.mission_id,
                        reason=CorrectionReason.FATAL,
                        detail=str(retry_exc),
                        command=ev.command,
                        trace_id=ev.trace_id,
                    )
                )
