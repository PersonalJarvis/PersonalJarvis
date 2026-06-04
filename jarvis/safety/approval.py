"""Approval-Workflow: Dual-Channel (UI-WebSocket + Toast) mit First-Wins.

Sequenz:
1. ToolExecutor publishes `ActionProposed(trace_id, tool, args, tier)`
2. `ApprovalWorkflow.wait(trace_id, timeout_s=60)` wird awaited
3. UI und/oder Toast rendern Modal
4. User drückt Approve → UI sendet `ActionApproved` via Channel →
   ChannelAdapter re-publisht aufs Bus → `wait()`-Future wird resolved
5. User drückt Deny → analog `ActionDenied`
6. Timeout → Default = Deny (`ActionDenied(reason="timeout")`)

Die Integration in eine Channel-UI (WebSocket + Desktop-App) läuft über
den gleichen Event-Bus. Die UI muss nur `ActionApproved`/`ActionDenied`
publizieren wenn der User klickt.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from jarvis.core.bus import EventBus
from jarvis.core.events import ActionApproved, ActionDenied


class ApprovalWorkflow:
    """Hält pro `trace_id` ein Future das auf Approve/Deny wartet."""

    def __init__(self, bus: EventBus, *, timeout_s: float = 60.0) -> None:
        self._bus = bus
        self._timeout_s = timeout_s
        self._pending: dict[UUID, asyncio.Future[tuple[bool, str]]] = {}
        bus.subscribe(ActionApproved, self._on_approved)
        bus.subscribe(ActionDenied, self._on_denied)

    def _resolve(self, trace_id: UUID, approved: bool, who_or_reason: str) -> None:
        fut = self._pending.pop(trace_id, None)
        if fut is not None and not fut.done():
            fut.set_result((approved, who_or_reason))

    async def _on_approved(self, event: ActionApproved) -> None:
        self._resolve(event.trace_id, True, event.approved_by or "user")

    async def _on_denied(self, event: ActionDenied) -> None:
        self._resolve(event.trace_id, False, event.reason or "denied")

    async def wait(self, trace_id: UUID, timeout_s: float | None = None) -> tuple[bool, str]:
        """Wartet bis zu `timeout_s` auf Approval/Denial für diese trace_id.

        Returns:
            (approved: bool, who_or_reason: str)
        """
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[tuple[bool, str]] = loop.create_future()
        self._pending[trace_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            self._pending.pop(trace_id, None)
            return (False, "timeout")

    # ------------------------------------------------------------------
    # Convenience: synthetische Approve/Deny für Tests & CLI-Launcher
    # ------------------------------------------------------------------

    async def approve(self, trace_id: UUID, who: str = "user") -> None:
        """Löst pending-approval auf (z.B. aus Toast-Callback heraus)."""
        await self._bus.publish(ActionApproved(trace_id=trace_id, approved_by=who))

    async def deny(self, trace_id: UUID, reason: str = "denied") -> None:
        await self._bus.publish(ActionDenied(trace_id=trace_id, reason=reason))

    # Statische Helper für Tool-Code der keinen Bus-Zugriff hat
    @staticmethod
    def extract_proposed_args(payload: dict[str, Any]) -> dict[str, Any]:
        return dict(payload.get("args", {}))
