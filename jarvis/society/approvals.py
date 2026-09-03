"""The unattended ask-queue (MASTERPLAN §2.9, agent-definition §3.4).

An action an agent may not run on its own — above its permission ceiling,
or matched by one of its ``require_approval`` rules — lands here as data.
The chat card, the world's gate, the Jarvis bar badge and voice are
projections of this table. Expiry NEVER drops work: an expired item flips
to ``blocked`` and re-surfaces on the next app focus or voice turn until a
person decides.

Rule evaluation (``decide``), in order: global blacklist (not here — the
ToolExecutor raises before anything), the agent's ``require_approval``
patterns (queue), its ``always_allow`` patterns (run when the tool's own
tier is at most ``ask`` — the person's standing yes), then the tool's risk
tier against the ceiling.
Patterns are capability ids (``plugin:gmail``), ``capability:verb``
(``plugin:gmail:send``) or ``capability:*``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from jarvis.missions.ids import uuid7_str

from .events import ApprovalState, MsgType, PermissionCeiling, SocietyEnvelope, now_ms
from .roster import AgentRecord
from .store import SocietyStore

log = logging.getLogger(__name__)

__all__ = ["Approval", "Approvals", "Verdict", "decide", "matches"]

DEFAULT_TTL_MS: Final[int] = 24 * 60 * 60 * 1000
_TIER_RANK: Final[dict[str, int]] = {"safe": 0, "monitor": 1, "ask": 2, "block": 3}


class Verdict(StrEnum):
    RUN = "run"
    QUEUE = "queue"
    BLOCK = "block"


def matches(pattern: str, capability_id: str, verb: str = "") -> bool:
    """``plugin:gmail`` matches the capability; ``plugin:gmail:send`` one verb;
    ``plugin:gmail:*`` every verb; ``plugin:*`` every plugin."""
    pattern = pattern.strip()
    if not pattern:
        return False
    if pattern == capability_id:
        return True
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        return capability_id == prefix or capability_id.startswith(prefix + ":")
    if verb and pattern == f"{capability_id}:{verb}":
        return True
    return False


def decide(agent: AgentRecord, capability_id: str, tool_tier: str, *, verb: str = "") -> Verdict:
    """What happens to one intended action of ``agent`` (see module doc)."""
    if tool_tier == "block":
        return Verdict.BLOCK
    rules = agent.approval_rules
    if any(matches(p, capability_id, verb) for p in rules.get("require_approval", [])):
        return Verdict.QUEUE
    if any(matches(p, capability_id, verb) for p in rules.get("always_allow", [])):
        # An explicit always-allow rule is the person's standing "yes" for this
        # exact action (the card's "Always allow", agent-definition §3.4): it
        # runs up to the ask tier. Block stays block — it never had a card.
        return Verdict.RUN if _TIER_RANK.get(tool_tier, 1) <= 2 else Verdict.QUEUE
    ceiling = _TIER_RANK[str(agent.permission_ceiling)]
    tier = _TIER_RANK.get(tool_tier, 1)
    if tier <= ceiling and tool_tier != str(PermissionCeiling.ASK):
        return Verdict.RUN
    return Verdict.QUEUE


@dataclass(slots=True)
class Approval:
    id: str
    agent_id: str
    trace_id: str
    capability: str
    action: dict[str, Any]
    summary: str
    state: ApprovalState
    created_ms: int
    expires_ms: int
    resolved_ms: int | None
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "trace_id": self.trace_id,
            "capability": self.capability,
            "action": self.action,
            "summary": self.summary,
            "state": str(self.state),
            "created_ms": self.created_ms,
            "expires_ms": self.expires_ms,
            "resolved_ms": self.resolved_ms,
            "note": self.note,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Approval:
        try:
            action = json.loads(row.get("action_json") or "{}")
        except ValueError:
            action = {}
        return cls(
            id=str(row["id"]),
            agent_id=str(row["agent_id"]),
            trace_id=str(row.get("trace_id") or ""),
            capability=str(row.get("capability") or ""),
            action=action if isinstance(action, dict) else {},
            summary=str(row.get("summary") or ""),
            state=ApprovalState(str(row.get("state") or "pending")),
            created_ms=int(row.get("created_ms") or 0),
            expires_ms=int(row.get("expires_ms") or 0),
            resolved_ms=row.get("resolved_ms"),
            note=str(row.get("note") or ""),
        )


class Approvals:
    def __init__(self, store: SocietyStore, *, ttl_ms: int = DEFAULT_TTL_MS) -> None:
        self._store = store
        self._ttl = ttl_ms

    async def enqueue(
        self,
        *,
        agent_id: str,
        trace_id: str,
        capability: str,
        action: dict[str, Any],
        summary: str,
    ) -> Approval:
        now = now_ms()
        approval_id = uuid7_str()
        await self._store.insert_approval(
            {
                "id": approval_id,
                "agent_id": agent_id,
                "trace_id": trace_id,
                "capability": capability,
                "action_json": json.dumps(action, ensure_ascii=False),
                "summary": summary[:500],
                "state": str(ApprovalState.PENDING),
                "created_ms": now,
                "expires_ms": now + self._ttl,
                "resolved_ms": None,
                "note": "",
            }
        )
        await self._store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.HOLD,
                from_agent=agent_id,
                to_agent="user",
                trace_id=trace_id,
                payload={
                    "approval_id": approval_id,
                    "capability": capability,
                    "text": summary[:500],
                },
            )
        )
        got = await self.get(approval_id)
        assert got is not None
        return got

    async def get(self, approval_id: str) -> Approval | None:
        row = await self._store.get_approval_row(approval_id)
        return Approval.from_row(row) if row else None

    async def items(
        self, *, state: ApprovalState | None = None, agent_id: str | None = None
    ) -> list[Approval]:
        rows = await self._store.list_approval_rows(
            state=str(state) if state else None, agent_id=agent_id
        )
        return [Approval.from_row(r) for r in rows]

    async def pending(self) -> list[Approval]:
        """Everything a person still has to decide — pending AND blocked."""
        pending = await self.items(state=ApprovalState.PENDING)
        blocked = await self.items(state=ApprovalState.BLOCKED)
        return sorted(pending + blocked, key=lambda a: a.created_ms)

    async def resolve(self, approval_id: str, *, approve: bool, note: str = "") -> Approval:
        current = await self.get(approval_id)
        if current is None:
            raise KeyError(approval_id)
        if current.state in (ApprovalState.APPROVED, ApprovalState.DENIED):
            return current
        state = ApprovalState.APPROVED if approve else ApprovalState.DENIED
        await self._store.update_approval(
            approval_id,
            {"state": str(state), "resolved_ms": now_ms(), "note": note[:500]},
        )
        await self._store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.RELEASE if approve else MsgType.VETO,
                from_agent="user",
                to_agent=current.agent_id,
                trace_id=current.trace_id,
                payload={
                    "approval_id": approval_id,
                    "capability": current.capability,
                    "approved": approve,
                    "text": note or ("approved" if approve else "denied"),
                    **({"reason": "blocked_by_policy"} if not approve else {}),
                },
            )
        )
        got = await self.get(approval_id)
        assert got is not None
        return got

    async def expire_due(self, *, now: int | None = None) -> int:
        """Pending items past their expiry become ``blocked`` — parked, never
        dropped. Returns how many flipped."""
        now = now if now is not None else now_ms()
        flipped = 0
        for item in await self.items(state=ApprovalState.PENDING):
            if item.expires_ms <= now:
                await self._store.update_approval(item.id, {"state": str(ApprovalState.BLOCKED)})
                flipped += 1
        return flipped

    async def resurface(self) -> list[Approval]:
        """On app focus / a voice turn: blocked items become pending again with
        a fresh expiry, so the person is asked once more."""
        revived: list[Approval] = []
        for item in await self.items(state=ApprovalState.BLOCKED):
            await self._store.update_approval(
                item.id, {"state": str(ApprovalState.PENDING), "expires_ms": now_ms() + self._ttl}
            )
            got = await self.get(item.id)
            if got is not None:
                revived.append(got)
        return revived
