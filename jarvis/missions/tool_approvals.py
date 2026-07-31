"""Mission-scoped view and decision bridge for paused supervisor tool calls."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import asdict, dataclass
from uuid import UUID

from loguru import logger

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


class MissionToolAutoApprover:
    """Answers approval gates for tools pre-authorized to one mission (ADR-0031).

    Modeled on ``jarvis.tasks.approval_bridge.TaskAutoApprover``: on a matching
    ``ActionApprovalRequired`` it publishes ``ActionApproved`` onto the bus —
    this ANSWERS the gate, it never bypasses it, so the full
    Proposed → ApprovalRequired → Approved → Executed audit chain is preserved
    and the ``MissionToolApprovalCoordinator``'s pending entry clears through
    its normal ``_on_resolved`` subscription.

    Arming is owned by the ``WorkerToolBroker``: one arm per issued grant
    (keyed ``mission_id`` + a non-secret grant key), disarmed on every
    revocation path, so auto-approval lives exactly as long as a grant. The
    armed set is already triple-filtered upstream (registry ``worker_allowed ∧
    ¬dangerous`` → broker denylist → ``granted ∩ [phase6.safety]
    auto_approve_tool_families``); this class re-checks fail-closed anyway.

    Thread-safety: ``arm``/``disarm`` are called from broker code paths that
    may run off the event loop (reaper via the HTTP thread, ``__del__``
    finalizers), so the grant map is guarded by a ``threading.Lock``. The bus
    handler runs on the loop and only reads under the same lock.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._mutex = threading.Lock()
        # mission_id -> grant_key -> auto-approvable tool names for that grant.
        self._grants: dict[str, dict[str, frozenset[str]]] = {}
        bus.subscribe(ActionApprovalRequired, self._on_approval_required)

    def arm(
        self,
        mission_id: str,
        grant_key: str,
        tool_names: frozenset[str],
    ) -> None:
        """Pre-authorize ``tool_names`` while the grant ``grant_key`` is live.

        An empty set is a valid no-op arm (keeps arm/disarm symmetric for
        grants whose intersection with the allowlist is empty).
        """
        mission = str(mission_id or "").strip()
        if not mission:
            return
        with self._mutex:
            self._grants.setdefault(mission, {})[grant_key] = frozenset(tool_names)

    def disarm(self, mission_id: str, grant_key: str) -> None:
        mission = str(mission_id or "").strip()
        with self._mutex:
            grants = self._grants.get(mission)
            if grants is None:
                return
            grants.pop(grant_key, None)
            if not grants:
                self._grants.pop(mission, None)

    async def _on_approval_required(self, event: ActionApprovalRequired) -> None:
        mission_id = str(event.mission_id or "").strip()
        if not mission_id:
            return
        # Never a "block" answer; approve both gate reasons — the plausibility
        # guard is a voice-context heuristic (transcript confidence / wake
        # age) that carries no signal for an unattended worker whose
        # "utterance" is the mission task text.
        if event.risk_tier not in ("ask", "monitor"):
            return
        if event.reason not in ("risk_tier", "plausibility"):
            return
        with self._mutex:
            grant_sets = tuple(self._grants.get(mission_id, {}).values())
        granted: frozenset[str] = frozenset().union(*grant_sets) if grant_sets else frozenset()
        if not granted:
            return
        tool_name = event.tool_name
        # Defense in depth: even an armed name must still pass the broker
        # denylist (catalog drift between arm and call).
        from jarvis.missions.workers.worker_tool_broker import worker_tool_name_allowed

        if not worker_tool_name_allowed(tool_name):
            return
        if not self._tool_is_granted(tool_name, granted):
            return
        approved_by = f"mission-grant:{mission_id[:13]}"
        logger.info(
            "MissionToolAutoApprover: approving {} for mission {} ({})",
            tool_name,
            mission_id,
            event.reason,
        )
        await self._bus.publish(
            ActionApproved(
                trace_id=event.trace_id,
                tool_name=tool_name,
                approved_by=approved_by,
            )
        )

    @staticmethod
    def _tool_is_granted(tool_name: str, granted: frozenset[str]) -> bool:
        """Exact name, or the MCP ``server/`` prefix (mirror of
        ``TaskAutoApprover._tool_is_granted``)."""
        if tool_name in granted:
            return True
        prefix = tool_name.split("/", 1)[0]
        return prefix in granted


__all__ = [
    "MissionToolApprovalCoordinator",
    "MissionToolAutoApprover",
    "PendingMissionToolApproval",
]
