"""The chat's approval card answers ``ToolExecutor``'s ticket.

On the Jarvis surface a typed turn runs on Jarvis' own harness, and its tools
run through ``ToolExecutor`` (jarvis/safety/tool_executor.py). When the
executor decides a call needs a person — an ``ask``-tier tool, or a
plausibility doubt — it arms an approval ticket, publishes
``ActionApprovalRequired`` and waits for an ``ActionApproved`` /
``ActionDenied`` on the bus. On the voice path that decision is a spoken
"yes" on the next turn; here it is a CLICK on the chat's approval card. This
bridge is what turns the click into the bus event the ticket is waiting for,
and what makes the chat's permission stance mean something:

============  ==================  ============  ==================
stance        folder Write/Edit   RunCommand    Jarvis ask-tier
============  ==================  ============  ==================
``ask``       card                card          card
``accept-edits``  approved         card          card
``bypass``    approved            approved      approved
``plan``      (not offered)       (not offered) (not offered)
============  ==================  ============  ==================

A tool the person once answered with "always allow" is approved without a
card for the rest of the session, in every stance below ``bypass``. The
blacklist is never reached from here: ``RiskTierEvaluator.evaluate`` raises
for a blacklisted call before the executor ever asks anyone, in every stance
— "bypass" bypasses the ASKING, not the blocking.

Shape, and the one rule it obeys: like ``TaskAutoApprover`` this is ONE
persistent subscriber with a map of grants keyed by ``approval_ref`` (the
chat stamps ``"agent-chat:<session_id>"`` through its tool context, the
executor echoes it on the event), so several chats can run at once without
a subscribe/unsubscribe race. And it NEVER awaits the person inside the
handler: ``EventBus.publish`` awaits every subscriber, so a handler that
blocked on a click would stall the executor's own publish. A synchronous
decision (a stance or an "always") is published inline; a card is asked in
a task of its own and the decision published when it lands.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from jarvis.core.events import ActionApprovalRequired, ActionApproved, ActionDenied, ActionProposed

log = logging.getLogger(__name__)

#: ``ExecutionContext.config["approval_ref"]`` for one chat session.
REF_PREFIX: Final[str] = "agent-chat:"

#: The folder tools an ``accept-edits`` stance waves through.
EDIT_TOOLS: Final[frozenset[str]] = frozenset({"Write", "Edit"})

#: How many ``ActionProposed`` argument sets are remembered for the cards'
#: "what exactly" — one per in-flight call would do; a few more cost nothing.
_ARGS_CACHE_SIZE: Final[int] = 64

#: What the card's ``ask`` callback answers with.
AskFn = Callable[[str, str, dict[str, Any], str], Awaitable[str]]


def approval_ref(session_id: str) -> str:
    return f"{REF_PREFIX}{session_id}"


@dataclass(slots=True)
class ChatGrant:
    """What one chat turn allows without asking, and how it asks otherwise."""

    session_id: str
    turn_id: str
    #: ``ask`` | ``accept-edits`` | ``bypass`` | ``plan`` (permissions.py).
    stance: str
    #: The tools the person waved through with "always allow" this session —
    #: shared with the service, which owns the set.
    always_allowed: set[str]
    #: ``ask(call_id, tool_name, args, summary) -> "allow" | "allow_always" |
    #: "deny" | "cancel"`` — the service's card.
    ask: AskFn
    #: The timeline row the card belongs to (the step mirror's open call for
    #: this tool), else a fresh id.
    call_id_for: Callable[[str], str] = field(default=lambda name: "")
    edit_tools: frozenset[str] = EDIT_TOOLS
    #: Tools the vendor CLI already asked about on its own control channel
    #: this turn (Claude Code's ``can_use_tool``): the executor's gate for the
    #: same tool is answered without a second card.
    pre_approved: set[str] = field(default_factory=set)


class ChatApprovalBridge:
    """One subscriber, many chats: turns approval tickets into cards and back."""

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._grants: dict[str, ChatGrant] = {}
        self._args: OrderedDict[UUID, dict[str, Any]] = OrderedDict()
        self._tasks: set[asyncio.Task[None]] = set()
        bus.subscribe(ActionProposed, self._on_proposed)
        bus.subscribe(ActionApprovalRequired, self._on_required)

    # ------------------------------------------------------------ grants

    def arm(self, ref: str, grant: ChatGrant) -> None:
        self._grants[ref] = grant

    def disarm(self, ref: str) -> None:
        self._grants.pop(ref, None)

    def is_armed(self, ref: str) -> bool:
        return ref in self._grants

    def note_cli_approval(self, ref: str, tool_name: str) -> None:
        """A vendor CLI's own prompt for ``tool_name`` was answered "allow"."""
        grant = self._grants.get(ref)
        if grant is not None:
            grant.pre_approved.add(_bare(tool_name))

    # ------------------------------------------------------------ events

    async def _on_proposed(self, event: ActionProposed) -> None:
        # The card wants the real arguments; the approval event only carries
        # a redacted preview. Keep the last few proposals by trace.
        try:
            self._args[event.trace_id] = dict(event.args or {})
            while len(self._args) > _ARGS_CACHE_SIZE:
                self._args.popitem(last=False)
        except Exception:  # noqa: BLE001 — AP-18: a subscriber never raises into the bus
            log.debug("chat approval bridge: could not cache proposed args", exc_info=True)

    async def _on_required(self, event: ActionApprovalRequired) -> None:
        try:
            ref = getattr(event, "approval_ref", None)
            grant = self._grants.get(ref) if ref else None
            if grant is None:
                return
            decision = self._decide_inline(grant, event.tool_name)
            if decision is not None:
                await self._bus.publish(
                    ActionApproved(
                        trace_id=event.trace_id,
                        tool_name=event.tool_name,
                        approved_by=decision,
                    )
                )
                return
            task = asyncio.create_task(
                self._ask_and_answer(grant, event),
                name=f"agent-chat-approval-{grant.session_id[:8]}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception:  # noqa: BLE001 — AP-18: a subscriber never raises into the bus
            log.warning("chat approval bridge: could not handle an approval request", exc_info=True)

    # ------------------------------------------------------------ policy

    @staticmethod
    def _decide_inline(grant: ChatGrant, tool_name: str) -> str | None:
        """The ``approved_by`` label when the stance decides without a card, else None."""
        name = _bare(tool_name)
        if grant.stance == "bypass":
            return "chat-bypass"
        if name in grant.always_allowed:
            return "user"
        if name in grant.pre_approved:
            return "user"
        if grant.stance == "accept-edits" and name in grant.edit_tools:
            return "chat-accept-edits"
        return None

    async def _ask_and_answer(self, grant: ChatGrant, event: ActionApprovalRequired) -> None:
        args = self._args.pop(event.trace_id, None) or {}
        summary = _summary(args, event.args_preview)
        call_id = ""
        try:
            call_id = grant.call_id_for(event.tool_name) or ""
        except Exception:  # noqa: BLE001 — a missing row is cosmetic, the card still asks
            log.debug("chat approval bridge: no timeline row for %s", event.tool_name)
        try:
            decision = await grant.ask(call_id, event.tool_name, args, summary)
        except Exception:  # noqa: BLE001 — the ticket must be answered either way
            log.warning("chat approval bridge: the card failed; denying", exc_info=True)
            decision = "deny"
        if decision in ("allow", "allow_always"):
            if decision == "allow_always":
                grant.always_allowed.add(_bare(event.tool_name))
            await self._bus.publish(
                ActionApproved(
                    trace_id=event.trace_id, tool_name=event.tool_name, approved_by="user"
                )
            )
            return
        reason = "cancelled" if decision == "cancel" else "declined by the person"
        await self._bus.publish(
            ActionDenied(trace_id=event.trace_id, tool_name=event.tool_name, reason=reason)
        )


def _bare(tool_name: str) -> str:
    """A tool's own name without an MCP prefix (``mcp__jarvis__open-app`` -> ``open-app``)."""
    name = tool_name or ""
    if name.startswith("mcp__"):
        return name.split("__", 2)[-1]
    return name


def _summary(args: dict[str, Any], preview: str) -> str:
    for key in ("command", "file_path", "pattern", "path", "query", "title", "to"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:200]
    return (preview or "")[:200]


__all__ = [
    "EDIT_TOOLS",
    "REF_PREFIX",
    "AskFn",
    "ChatApprovalBridge",
    "ChatGrant",
    "approval_ref",
]
