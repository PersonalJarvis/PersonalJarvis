"""Heavy-Duty Worker — v2 rewrite with real, provider-agnostic LLM calls.

The worker is the background counterpart to the Talker. It:

1. Subscribes to ``MissionSpawn`` events on the EventBus.
2. When a mission arrives, schedules async work as an ``asyncio.Task`` and
   **returns immediately** — the Talker is never blocked (AD-OE1, AD-OE2).
3. Inside the task:
   a. Publishes ``WorkerStarted``.
   b. For ``gmail`` missions: runs the ``check_missing_info`` pre-check
      (contact lookup). If info is missing, publishes ``WorkerCorrectionNeeded``
      immediately (no LLM call wasted).
   c. Otherwise: calls ``llm.complete`` with the mission command. On success,
      publishes ``WorkerCompleted``. On ``LLMError``: one silent retry; if the
      retry also fails, publishes ``WorkerCorrectionNeeded(NETWORK_ERROR)``.
4. Never lets an exception escape the task (AP-18 parity).

Transport injection
-------------------
Pass ``_transport`` (an ``httpx.AsyncBaseTransport``) to ``HeavyDutyWorker``
to redirect all httpx calls in tests without touching a real network.
"""
from __future__ import annotations

import asyncio
import logging

import optimistic.llm as llm
from optimistic.config import LLMSettings
from optimistic.events import (
    CorrectionReason,
    MissionSpawn,
    WorkerCompleted,
    WorkerCorrectionNeeded,
    WorkerStarted,
)
from optimistic.tools import check_missing_info

_log = logging.getLogger("optimistic.worker")


class HeavyDutyWorker:
    """Asynchronous background worker backed by a real (provider-agnostic) LLM.

    Args:
        bus:        Duck-typed event bus (.subscribe, async .publish).
        settings:   Frozen LLMSettings — model, URL, key, timeout, system prompt.
        _transport: Optional httpx transport injected for unit tests so no real
                    network is hit.

    Thread-safety: all interactions stay inside one asyncio event loop.
    """

    def __init__(
        self,
        bus,
        settings: LLMSettings | None = None,
        _transport=None,
    ) -> None:
        self._bus = bus
        # If no settings provided, fall back to mock backend so v1 tests and
        # the orchestrator's integration helpers still work without needing to
        # construct an LLMSettings explicitly.
        if settings is None:
            from optimistic.config import load_settings
            settings = load_settings(env={"LLM_BACKEND": "mock", "LLM_MODEL": "fallback"})
        self._settings = settings
        self._transport = _transport
        # Tracks running tasks; done-callback removes each on completion.
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
        """Wait for all in-flight mission tasks to settle.

        Callers (typically tests) use this before inspecting the event log.
        Exceptions from tasks are suppressed here — they are already handled
        inside ``_run``.
        """
        if not self._tasks:
            return
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    async def _on_mission_spawn(self, ev: MissionSpawn) -> None:
        """Schedule a task for the mission and return immediately (AD-OE2)."""
        task = asyncio.create_task(self._run(ev))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, ev: MissionSpawn) -> None:
        """Execute one mission inside its own task.

        Spec-mandated steps:
        1. Publish ``WorkerStarted``.
        2. Log at INFO.
        3. Gmail pre-check via ``check_missing_info``; if info missing, publish
           ``WorkerCorrectionNeeded(MISSING_INFO)`` and return.
        4. Call ``llm.complete``; on success publish ``WorkerCompleted``.
        5. On ``LLMError``: one retry; if both fail, publish
           ``WorkerCorrectionNeeded(NETWORK_ERROR)``.

        This coroutine MUST NOT let any exception escape.
        """
        # Step 1: announce that the worker picked up this mission.
        await self._bus.publish(
            WorkerStarted(
                mission_id=ev.mission_id,
                tool_name=ev.tool_name,
                session_id=ev.session_id,
                trace_id=ev.trace_id,
            )
        )

        # Step 2: structured log so operators can follow mission progress.
        _log.info(
            "Heavy-Duty-Worker processing task %s: %s",
            ev.mission_id,
            ev.command,
        )

        # Step 3: gmail pre-check — contact must be in context before we
        # waste an LLM call.
        if ev.tool_name == "gmail":
            miss = check_missing_info(ev.command, ev.context)
            if miss:
                await self._bus.publish(
                    WorkerCorrectionNeeded(
                        reason=miss[0],
                        detail=miss[1],
                        mission_id=ev.mission_id,
                        command=ev.command,
                        session_id=ev.session_id,
                        trace_id=ev.trace_id,
                    )
                )
                return

        # Step 4 + 5: LLM call with one retry on LLMError.
        try:
            result = await llm.complete(
                ev.command,
                settings=self._settings,
                system=self._settings.system_prompt,
                _transport=self._transport,
            )
            await self._bus.publish(
                WorkerCompleted(
                    result=result,
                    mission_id=ev.mission_id,
                    session_id=ev.session_id,
                    trace_id=ev.trace_id,
                )
            )
        except llm.LLMError as exc:
            # First attempt failed — one silent retry.
            _log.warning(
                "Worker task %s: LLM call failed (%s), retrying once...",
                ev.mission_id,
                exc,
            )
            try:
                result = await llm.complete(
                    ev.command,
                    settings=self._settings,
                    system=self._settings.system_prompt,
                    _transport=self._transport,
                )
                await self._bus.publish(
                    WorkerCompleted(
                        result=result,
                        mission_id=ev.mission_id,
                        session_id=ev.session_id,
                        trace_id=ev.trace_id,
                    )
                )
            except llm.LLMError as retry_exc:
                # Both attempts failed — signal the Oops loop.
                _log.error(
                    "Worker task %s: LLM call failed after retry: %s",
                    ev.mission_id,
                    retry_exc,
                )
                await self._bus.publish(
                    WorkerCorrectionNeeded(
                        reason=CorrectionReason.NETWORK_ERROR,
                        detail=str(retry_exc),
                        mission_id=ev.mission_id,
                        command=ev.command,
                        session_id=ev.session_id,
                        trace_id=ev.trace_id,
                    )
                )
        except Exception as exc:
            # Unexpected exception — guard against any future regression.
            _log.error(
                "Worker task %s: unexpected error: %s",
                ev.mission_id,
                exc,
            )
            await self._bus.publish(
                WorkerCorrectionNeeded(
                    reason=CorrectionReason.FATAL,
                    detail=str(exc),
                    mission_id=ev.mission_id,
                    command=ev.command,
                    session_id=ev.session_id,
                    trace_id=ev.trace_id,
                )
            )
