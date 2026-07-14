"""Mission-scoped view and decision bridge for paused supervisor tool calls."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from uuid import UUID

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionApprovalRequired,
    ActionApproved,
    ActionDenied,
    ActionExecuted,
)


@dataclass(frozen=True, slots=True)
class PendingMissionToolApproval:
    """Secret-free data needed to render and decide one paused call."""

    trace_id: UUID
    mission_id: str
    worker_id: str | None
    tool_name: str
    risk_tier: str
    reason: str
    args_preview: str
    requested_at_ns: int
    expires_at_ns: int

    def to_dict(self) -> dict[str, str | int | None]:
        payload = asdict(self)
        payload["trace_id"] = str(self.trace_id)
        return payload


class MissionToolApprovalCoordinator:
    """Tracks mission approvals while ``ApprovalWorkflow`` owns the waiter.

    The coordinator never holds tool objects, arguments, or credentials. It is
    an in-memory projection of bus events and publishes the normal
    ``ActionApproved``/``ActionDenied`` decisions, preserving the single safety
    path through ``ToolExecutor``.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._pending: dict[UUID, PendingMissionToolApproval] = {}
        self._lock = asyncio.Lock()
        bus.subscribe(ActionApprovalRequired, self._on_required)
        bus.subscribe(ActionApproved, self._on_resolved)
        bus.subscribe(ActionDenied, self._on_resolved)
        bus.subscribe(ActionExecuted, self._on_resolved)

    async def _on_required(self, event: ActionApprovalRequired) -> None:
        mission_id = str(event.mission_id or "").strip()
        if not mission_id or event.expires_at_ns <= time.time_ns():
            return
        pending = PendingMissionToolApproval(
            trace_id=event.trace_id,
            mission_id=mission_id,
            worker_id=event.worker_id,
            tool_name=event.tool_name,
            risk_tier=event.risk_tier,
            reason=event.reason,
            args_preview=event.args_preview,
            requested_at_ns=event.timestamp_ns,
            expires_at_ns=event.expires_at_ns,
        )
        async with self._lock:
            self._pending[event.trace_id] = pending

    async def _on_resolved(
        self,
        event: ActionApproved | ActionDenied | ActionExecuted,
    ) -> None:
        async with self._lock:
            self._pending.pop(event.trace_id, None)

    async def list_pending(
        self,
        mission_id: str,
    ) -> tuple[PendingMissionToolApproval, ...]:
        now_ns = time.time_ns()
        async with self._lock:
            expired = [
                trace_id
                for trace_id, item in self._pending.items()
                if item.expires_at_ns <= now_ns
            ]
            for trace_id in expired:
                self._pending.pop(trace_id, None)
            return tuple(
                sorted(
                    (
                        item
                        for item in self._pending.values()
                        if item.mission_id == mission_id
                    ),
                    key=lambda item: item.requested_at_ns,
                )
            )

    async def approve(
        self,
        mission_id: str,
        trace_id: UUID,
        *,
        approved_by: str,
    ) -> PendingMissionToolApproval | None:
        pending = await self._take_live(mission_id, trace_id)
        if pending is None:
            return None
        await self._bus.publish(
            ActionApproved(
                trace_id=trace_id,
                tool_name=pending.tool_name,
                approved_by=approved_by,
            )
        )
        return pending

    async def deny(
        self,
        mission_id: str,
        trace_id: UUID,
        *,
        reason: str,
    ) -> PendingMissionToolApproval | None:
        pending = await self._take_live(mission_id, trace_id)
        if pending is None:
            return None
        await self._bus.publish(
            ActionDenied(
                trace_id=trace_id,
                tool_name=pending.tool_name,
                reason=reason,
            )
        )
        return pending

    async def _take_live(
        self,
        mission_id: str,
        trace_id: UUID,
    ) -> PendingMissionToolApproval | None:
        async with self._lock:
            pending = self._pending.get(trace_id)
            if (
                pending is None
                or pending.mission_id != mission_id
                or pending.expires_at_ns <= time.time_ns()
            ):
                if pending is not None and pending.expires_at_ns <= time.time_ns():
                    self._pending.pop(trace_id, None)
                return None
            self._pending.pop(trace_id, None)
            return pending

    async def deny_all(self, *, reason: str) -> None:
        """Release every waiter during orderly application shutdown."""
        async with self._lock:
            pending = tuple(self._pending.values())
            self._pending.clear()
        for item in pending:
            await self._bus.publish(
                ActionDenied(
                    trace_id=item.trace_id,
                    tool_name=item.tool_name,
                    reason=reason,
                )
            )


__all__ = ["MissionToolApprovalCoordinator", "PendingMissionToolApproval"]
