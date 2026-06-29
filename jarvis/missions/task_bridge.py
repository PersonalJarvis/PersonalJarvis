"""MissionEventBridge — re-publish terminal MissionBus events onto the global bus.

The Phase-6 ``MissionBus`` is isolated from the global ``jarvis.core.bus.EventBus``
(per-subscriber bounded queues, so a slow consumer never blocks the voice path).
When-Then automation — the Tasks ``on_event`` trigger — listens on the *global* bus
and therefore never sees a mission finishing. This bridge closes that gap: it
subscribes to the ``MissionBus`` and emits exactly one flat ``MissionCompleted``
event on the global bus for every terminal mission outcome (approved / failed /
cancelled / timed-out). The Tasks scheduler can then match it like any other event.

Deliberately SEPARATE from ``MissionAnnouncer`` (``voice/announcer.py``): the
announcer translates the same mission events into a spoken ``AnnouncementRequested``
readback, this bridge emits the machine-readable ``MissionCompleted`` trigger signal.
Different event classes ⇒ no double announcement (init.py's readback-mode guard is
unaffected — the bridge does not speak).

Errors are swallowed and logged (AP-18 / the EventBus dependency rule): a broken
bridge must never freeze the mission bus.
"""
from __future__ import annotations

import logging

from jarvis.core.bus import EventBus
from jarvis.core.events import MissionCompleted

from .event_bus import MissionBus
from .events import (
    EventEnvelope,
    MissionApproved,
    MissionCancelled,
    MissionFailed,
    MissionTimedOut,
)

logger = logging.getLogger(__name__)


class MissionEventBridge:
    """MissionBus → global-bus bridge emitting ``MissionCompleted``.

    Args:
        bus: the per-mission ``MissionBus`` to subscribe to.
        global_bus: the global ``EventBus`` (``jarvis.core.bus.EventBus``) the
            Tasks scheduler also binds to — the bridge publishes
            ``MissionCompleted`` here.
    """

    def __init__(self, *, bus: MissionBus, global_bus: EventBus) -> None:
        self._bus = bus
        self._global_bus = global_bus
        self._unsubscribe = None  # set by start()

    async def start(self) -> None:
        """Register the wildcard subscriber on the MissionBus."""
        self._unsubscribe = self._bus.subscribe_all(self._on_event)
        logger.info("MissionEventBridge: bus-subscribe registered")

    def stop(self) -> None:
        """Cancel the subscription. Idempotent."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def _on_event(self, env: EventEnvelope) -> None:
        """Wildcard handler. No-op on error — the mission bus must never freeze
        because of a broken bridge."""
        try:
            signal = self._to_signal(env)
            if signal is not None:
                await self._global_bus.publish(signal)
        except Exception:  # noqa: BLE001
            logger.warning("MissionEventBridge crashed", exc_info=True)

    @staticmethod
    def _to_signal(env: EventEnvelope) -> MissionCompleted | None:
        """Map a terminal mission envelope to a flat ``MissionCompleted`` signal.

        Returns ``None`` for every non-terminal event (worker progress, state
        changes, budget warnings, …) so only real completions drive a rule.
        """
        payload = env.payload
        if isinstance(payload, MissionApproved):
            return MissionCompleted(
                mission_id=env.mission_id,
                status="approved",
                summary_de=payload.summary_de,
                summary_en=payload.summary_en,
                result_uri=payload.result_uri,
                source_layer="missions.bridge",
            )
        if isinstance(payload, MissionFailed):
            return MissionCompleted(
                mission_id=env.mission_id,
                status="failed",
                reason=payload.reason,
                source_layer="missions.bridge",
            )
        if isinstance(payload, MissionCancelled):
            return MissionCompleted(
                mission_id=env.mission_id,
                status="cancelled",
                reason=payload.reason,
                source_layer="missions.bridge",
            )
        if isinstance(payload, MissionTimedOut):
            return MissionCompleted(
                mission_id=env.mission_id,
                status="timed_out",
                source_layer="missions.bridge",
            )
        return None


__all__ = ["MissionEventBridge"]
