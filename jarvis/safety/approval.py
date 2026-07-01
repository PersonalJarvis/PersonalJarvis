"""Approval workflow: dual-channel (UI WebSocket + toast) with first-wins.

Sequence:
1. ToolExecutor publishes `ActionProposed(trace_id, tool, args, tier)`
2. `ApprovalWorkflow.wait(trace_id, timeout_s=60)` is awaited
3. UI and/or toast render a modal
4. User clicks Approve → UI sends `ActionApproved` via a channel →
   ChannelAdapter re-publishes onto the bus → `wait()`'s future is resolved
5. User clicks Deny → analogous `ActionDenied`
6. Timeout → default = deny (`ActionDenied(reason="timeout")`)

The integration into a channel UI (WebSocket + desktop app) runs over
the same event bus. The UI only needs to publish `ActionApproved`/`ActionDenied`
when the user clicks.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from jarvis.core.bus import EventBus
from jarvis.core.events import ActionApproved, ActionDenied


class ApprovalWorkflow:
    """Holds one future per `trace_id` that waits for approve/deny."""

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
        """Waits up to `timeout_s` for an approval/denial for this trace_id.

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
    # Convenience: synthetic approve/deny for tests & the CLI launcher
    # ------------------------------------------------------------------

    async def approve(self, trace_id: UUID, who: str = "user") -> None:
        """Resolves a pending approval (e.g. from a toast callback)."""
        await self._bus.publish(ActionApproved(trace_id=trace_id, approved_by=who))

    async def deny(self, trace_id: UUID, reason: str = "denied") -> None:
        await self._bus.publish(ActionDenied(trace_id=trace_id, reason=reason))

    # Static helper for tool code that has no bus access
    @staticmethod
    def extract_proposed_args(payload: dict[str, Any]) -> dict[str, Any]:
        return dict(payload.get("args", {}))
