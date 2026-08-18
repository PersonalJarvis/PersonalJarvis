"""Approval workflow: dual-channel (UI WebSocket + toast) with first-wins.

Sequence:
1. ToolExecutor publishes `ActionProposed(trace_id, tool, args, tier)`
2. `ApprovalWorkflow.wait(trace_id, timeout_s=60)` is awaited
3. UI and/or toast render a modal
4. User clicks Approve → UI sends `ActionApproved` via a channel →
   ChannelAdapter re-publishes onto the bus → `wait()`'s future is resolved
5. User clicks Deny → analogous `ActionDenied`
6. Timeout → nothing runs (`ActionDenied(reason="timeout")`), and the caller
   is told it was a TIMEOUT, not a refusal

Step 2 only happens on a surface where an answer can actually arrive. Whether
this workflow is waited on at all, peeked at once, or skipped for a two-turn
spoken confirmation is decided by `jarvis.safety.approval_surface` — an
unattended runner must never block here for a decision nobody is there to make.

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

#: ``who_or_reason`` returned when the wait ran out before anyone decided.
#: A named constant because the caller MUST be able to tell "nobody answered"
#: apart from "somebody said no" — the two are different outcomes and used to
#: be reported with the same word (audit GT-12).
TIMEOUT_REASON = "timeout"


class ApprovalTicket:
    """One armed approval decision that cannot lose an early bus response."""

    def __init__(
        self,
        workflow: ApprovalWorkflow,
        trace_id: UUID,
        future: asyncio.Future[tuple[bool, str]],
    ) -> None:
        self._workflow = workflow
        self.trace_id = trace_id
        self._future = future
        self._closed = False

    async def wait(self, timeout_s: float) -> tuple[bool, str]:
        """Wait for the first decision, defaulting safely to deny on timeout."""
        try:
            return await asyncio.wait_for(self._future, timeout=timeout_s)
        except TimeoutError:
            return (False, TIMEOUT_REASON)
        finally:
            self.close()

    def peek(self) -> tuple[bool, str] | None:
        """The decision if one has ALREADY been made, else ``None``. Never waits.

        An unattended caller uses this instead of :meth:`wait`: the
        pre-authorization bridges (``TaskAutoApprover``,
        ``MissionToolAutoApprover``) answer synchronously while the executor
        awaits its ``ActionApprovalRequired`` publish, so by the time that
        publish returns their verdict is already in the future. Everything
        else would be a wait for a human who is not there, which is why the
        unattended path must never call ``wait()``.

        Leaves the ticket open — the caller decides whether the outcome is a
        decision to act on or a dead end to close.
        """
        if not self._future.done() or self._future.cancelled():
            return None
        if self._future.exception() is not None:
            return None
        return self._future.result()

    def close(self) -> None:
        """Remove an unresolved ticket so cancellation cannot leak state."""
        if self._closed:
            return
        self._closed = True
        self._workflow._discard(self.trace_id, self._future)


class ApprovalWorkflow:
    """Holds one future per `trace_id` that waits for approve/deny."""

    def __init__(self, bus: EventBus, *, timeout_s: float = 60.0) -> None:
        self._bus = bus
        self._timeout_s = timeout_s
        self._pending: dict[UUID, asyncio.Future[tuple[bool, str]]] = {}
        bus.subscribe(ActionApproved, self._on_approved)
        bus.subscribe(ActionDenied, self._on_denied)

    def _resolve(self, trace_id: UUID, approved: bool, who_or_reason: str) -> None:
        fut = self._pending.get(trace_id)
        if fut is not None and not fut.done():
            fut.set_result((approved, who_or_reason))

    def arm(self, trace_id: UUID) -> ApprovalTicket:
        """Register the decision future before an approval request is published.

        An approval received before this call is deliberately ignored. A trace
        may have only one active ticket, which keeps first-wins semantics and
        prevents unrelated events from pre-authorizing a future action.
        """
        if trace_id in self._pending:
            raise RuntimeError(f"approval trace {trace_id} is already armed")
        future: asyncio.Future[tuple[bool, str]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[trace_id] = future
        return ApprovalTicket(self, trace_id, future)

    def _discard(
        self,
        trace_id: UUID,
        future: asyncio.Future[tuple[bool, str]],
    ) -> None:
        if self._pending.get(trace_id) is future:
            self._pending.pop(trace_id, None)
        if not future.done():
            future.cancel()

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
        future = self._pending.get(trace_id)
        ticket = (
            ApprovalTicket(self, trace_id, future)
            if future is not None
            else self.arm(trace_id)
        )
        return await ticket.wait(timeout)

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
