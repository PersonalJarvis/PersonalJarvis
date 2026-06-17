"""TaskAutoApprover — unattended pre-authorization for scheduled-task tools.

A scheduled "agent" task pre-authorizes specific plugins at creation time by
toggling them with a ``write``/``full`` permission scope. While that task's
turn runs, an ask-tier tool call (e.g. "post a tweet", "send an email")
would normally block at ``ApprovalWorkflow.wait()`` because no human is
present to confirm. This bridge answers that gate programmatically — but
only for tools the task was explicitly granted, and only for that task's
own ``trace_id``.

Design (Option B from the integration analysis):
  - The runner ``arm()``s the bridge with the turn's ``trace_id`` and the
    set of pre-authorized plugin ids before running the turn, then
    ``disarm()``s in a finally.
  - The bridge listens for ``ActionProposed``. On a match (same trace_id +
    the proposed tool belongs to a granted plugin) it publishes
    ``ActionApproved`` straight onto the bus, which resolves the executor's
    ``wait()``. The full audit trail (Proposed -> Approved -> Executed) is
    preserved — this answers the gate, it does not bypass it.
  - Anything NOT armed (read-only grants arm with an empty set; ungranted
    tools) is left to block and deny on timeout. Nothing is auto-approved
    by default.

Concurrency-safe: a persistent ``ActionProposed`` subscriber plus a
``trace_id -> grant`` map, so several tasks can run at once without a
per-turn subscribe/unsubscribe race.
"""
from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from jarvis.core.bus import EventBus
from jarvis.core.events import ActionApproved, ActionProposed


class TaskAutoApprover:
    """Programmatically approves pre-authorized ask-tier tools per task turn."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        # trace_id -> (granted plugin ids, audit label)
        self._active: dict[UUID, tuple[frozenset[str], str]] = {}
        bus.subscribe(ActionProposed, self._on_proposed)

    def arm(self, trace_id: UUID, plugin_ids: Iterable[str], *, approved_by: str) -> None:
        """Pre-authorize ``plugin_ids`` for the turn identified by ``trace_id``.

        An empty ``plugin_ids`` is a valid no-op arm (a read-only task): it
        registers the trace but approves nothing, so the disarm() in the
        runner stays symmetric.
        """
        self._active[trace_id] = (frozenset(plugin_ids), approved_by)

    def disarm(self, trace_id: UUID) -> None:
        self._active.pop(trace_id, None)

    async def _on_proposed(self, event: ActionProposed) -> None:
        ctx = self._active.get(event.trace_id)
        if ctx is None:
            return
        granted, approved_by = ctx
        if not self._tool_is_granted(event.tool_name, granted):
            return
        await self._bus.publish(
            ActionApproved(
                trace_id=event.trace_id,
                tool_name=event.tool_name,
                approved_by=approved_by,
            )
        )

    @staticmethod
    def _tool_is_granted(tool_name: str, granted: frozenset[str]) -> bool:
        """A tool is covered if its name (native tool) or its plugin prefix
        (MCP tools are namespaced ``plugin/tool``) is in the grant set.
        """
        if tool_name in granted:
            return True
        prefix = tool_name.split("/", 1)[0]
        return prefix in granted
