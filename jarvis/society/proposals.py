"""Configuration by chat: the agent PROPOSES a change to itself, the person
CONFIRMS it on a card in the chat, and only then does anything change.

A proposal is an ordinary row in the approvals queue whose capability is
``core:config:<kind>`` and whose ``action`` carries the typed payload — no new
table, no new enum, no new chat event kind. The card rides a ``notice`` event
(``payload.kind == "proposal"``), its outcome a second one
(``payload.kind == "proposal_resolved"``). Nothing here applies a change:
:func:`apply` runs only from the resolve route once the person said yes.

Kinds (agent-definition §3.5):

* ``rule`` — one standing instruction appended to the agent's description;
* ``skill`` — the procedure of the current turn saved under a name;
* ``routine`` — recurring work as a tagged Automations task;
* ``approval_rule`` — patterns for ``require_approval`` / ``always_allow``;
* ``focus`` — the full ordered list of tools the agent reaches for first;
* ``team`` — teammates proposed at onboarding (the lead only).
"""

from __future__ import annotations

import logging
from typing import Any, Final

from .approvals import Approval
from .capabilities import CapabilityRow
from .events import ApprovalState
from .failure_reasons import FailureReason
from .roster import AgentRecord

log = logging.getLogger(__name__)

__all__ = [
    "CAPABILITY_PREFIX",
    "PROPOSAL_KINDS",
    "ProposalRefused",
    "capability_for",
    "kind_of",
    "propose",
    "proposal_notice",
    "resolved_notice",
    "summarize",
    "validate",
]

PROPOSAL_KINDS: Final[frozenset[str]] = frozenset(
    {"rule", "skill", "routine", "approval_rule", "focus", "team"}
)
CAPABILITY_PREFIX: Final[str] = "core:config:"
#: Schedule kinds the Automations scheduler can keep (``jarvis/tasks/schema.py``).
#: There is deliberately no weekday form: the model must not promise what the
#: scheduler cannot run (plan decision D4).
SCHEDULE_KINDS: Final[frozenset[str]] = frozenset({"every", "at_time", "after_delay", "on_event"})
_MAX_RULE: Final[int] = 600
_MAX_TEXT: Final[int] = 2_000
_MAX_LIST: Final[int] = 24
_MAX_SUMMARY: Final[int] = 200


class ProposalRefused(Exception):
    """A proposal the tool refuses with a typed reason (never a silent drop)."""

    def __init__(self, reason: FailureReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def capability_for(kind: str) -> str:
    return f"{CAPABILITY_PREFIX}{kind}"


def kind_of(capability: str) -> str | None:
    """The proposal kind behind a capability id; ``None`` for a non-proposal."""
    if not capability.startswith(CAPABILITY_PREFIX):
        return None
    kind = capability[len(CAPABILITY_PREFIX) :]
    return kind if kind in PROPOSAL_KINDS else None


def _text(value: Any, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _strings(value: Any, *, limit: int = _MAX_LIST) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out[:limit]


def _capability_root(pattern: str) -> str:
    """``plugin:gmail:send`` and ``plugin:gmail:*`` → ``plugin:gmail``; ``plugin:*`` stays."""
    pattern = pattern.strip()
    if pattern.endswith(":*"):
        head = pattern[:-2]
        return head if ":" in head else pattern
    parts = pattern.split(":")
    if len(parts) >= 3:
        return ":".join(parts[:2])
    return pattern


def _check_capabilities(patterns: list[str], catalog: list[CapabilityRow]) -> None:
    known = {row.id for row in catalog}
    for pattern in patterns:
        root = _capability_root(pattern)
        if root.endswith(":*"):
            kind = root[:-2]
            if not any(row.id.startswith(kind + ":") for row in catalog):
                raise ProposalRefused(FailureReason.TARGET_UNKNOWN, f"no capability kind {kind!r}")
            continue
        if root not in known:
            raise ProposalRefused(FailureReason.TARGET_UNKNOWN, f"unknown capability {root!r}")


def validate(kind: str, payload: Any, *, catalog: list[CapabilityRow]) -> dict[str, Any]:
    """Normalise one payload for ``kind``; extra keys are dropped, bad ones refused."""
    if kind not in PROPOSAL_KINDS:
        raise ProposalRefused(FailureReason.BLOCKED_BY_POLICY, f"unknown proposal kind {kind!r}")
    if not isinstance(payload, dict):
        raise ProposalRefused(FailureReason.BLOCKED_BY_POLICY, "payload must be an object")
    if kind == "rule":
        text = _text(payload.get("text"), limit=_MAX_RULE)
        if not text:
            raise ProposalRefused(FailureReason.BLOCKED_BY_POLICY, "a rule needs text")
        return {"text": text}
    if kind == "skill":
        name = _text(payload.get("name"), limit=80)
        goal = _text(payload.get("goal"), limit=_MAX_TEXT)
        steps = _strings(payload.get("steps"))
        outcome = _text(payload.get("outcome"), limit=_MAX_TEXT)
        if not name or not goal:
            raise ProposalRefused(
                FailureReason.BLOCKED_BY_POLICY, "a skill needs a name and a goal"
            )
        return {"name": name, "goal": goal, "steps": steps, "outcome": outcome}
    if kind == "routine":
        title = _text(payload.get("title"), limit=120)
        prompt = _text(payload.get("prompt"), limit=_MAX_TEXT)
        schedule = payload.get("schedule")
        if not title or not prompt:
            raise ProposalRefused(
                FailureReason.BLOCKED_BY_POLICY, "a routine needs a title and a prompt"
            )
        if not isinstance(schedule, dict):
            raise ProposalRefused(
                FailureReason.BLOCKED_BY_POLICY, "a routine needs a schedule object"
            )
        raw_kind = schedule.get("kind") or schedule.get("type") or "every"
        schedule_kind = str(raw_kind).strip().lower()
        if schedule_kind not in SCHEDULE_KINDS:
            raise ProposalRefused(
                FailureReason.BLOCKED_BY_POLICY,
                f"schedule kind {schedule_kind!r} is not one the scheduler keeps "
                f"({', '.join(sorted(SCHEDULE_KINDS))})",
            )
        clean = {
            "kind": schedule_kind,
            **{k: v for k, v in schedule.items() if k not in ("kind", "type")},
        }
        out: dict[str, Any] = {"title": title, "prompt": prompt, "schedule": clean}
        announce = _text(payload.get("announce_on_success"), limit=300)
        if announce:
            out["announce_on_success"] = announce
        return out
    if kind == "approval_rule":
        require = _strings(payload.get("require_approval"))
        allow = _strings(payload.get("always_allow"))
        if not require and not allow:
            raise ProposalRefused(
                FailureReason.BLOCKED_BY_POLICY, "an approval rule needs a pattern"
            )
        _check_capabilities(require + allow, catalog)
        return {"require_approval": require, "always_allow": allow}
    if kind == "focus":
        focus = _strings(payload.get("focus"))
        if not focus:
            raise ProposalRefused(FailureReason.BLOCKED_BY_POLICY, "a focus change needs the list")
        _check_capabilities(focus, catalog)
        return {"focus": focus}
    names = _strings(payload.get("names"))
    if not names:
        raise ProposalRefused(FailureReason.BLOCKED_BY_POLICY, "a team proposal needs names")
    out_team: dict[str, Any] = {"names": names}
    proposals = payload.get("proposals")
    if isinstance(proposals, list):
        out_team["proposals"] = [p for p in proposals if isinstance(p, dict)][:_MAX_LIST]
    return out_team


def summarize(kind: str, payload: dict[str, Any]) -> str:
    """One card line for the person (≤200 chars)."""
    if kind == "rule":
        line = f"Add a standing rule: {payload.get('text', '')}"
    elif kind == "skill":
        line = f"Save the procedure as skill '{payload.get('name', '')}'"
    elif kind == "routine":
        schedule = payload.get("schedule") or {}
        line = f"Schedule '{payload.get('title', '')}' ({schedule.get('kind', 'every')})"
    elif kind == "approval_rule":
        parts: list[str] = []
        if payload.get("require_approval"):
            parts.append("ask first: " + ", ".join(payload["require_approval"]))
        if payload.get("always_allow"):
            parts.append("always allow: " + ", ".join(payload["always_allow"]))
        line = "Approval rules — " + "; ".join(parts)
    elif kind == "focus":
        line = "Reach first for: " + ", ".join(payload.get("focus", []))
    else:
        line = "Create teammates: " + ", ".join(payload.get("names", []))
    return line[:_MAX_SUMMARY]


def proposal_notice(agent: AgentRecord, item: Approval) -> dict[str, Any]:
    action = item.action
    kind = str(action.get("kind") or kind_of(item.capability) or "")
    return {
        "kind": "proposal",
        "proposal_id": item.id,
        "proposal_kind": kind,
        "summary": item.summary,
        "payload": action.get("payload") or {},
        "reason": str(action.get("reason") or ""),
        "status": "pending",
        "agent_id": agent.agent_id,
        "agent_name": agent.name,
        "text": item.summary,
    }


def resolved_notice(
    agent: AgentRecord, item: Approval, *, status: str, text: str
) -> dict[str, Any]:
    return {
        "kind": "proposal_resolved",
        "proposal_id": item.id,
        "proposal_kind": str(item.action.get("kind") or kind_of(item.capability) or ""),
        "status": status,
        "text": text,
        "agent_id": agent.agent_id,
        "agent_name": agent.name,
    }


async def propose(
    rt: Any,
    agent: AgentRecord,
    *,
    kind: str,
    payload: Any,
    reason: str,
    session_id: str,
) -> Approval:
    """Validate, queue, and show the card. Raises :class:`ProposalRefused`."""
    clean = validate(kind, payload, catalog=rt.catalog())
    for item in await rt.approvals.items(state=ApprovalState.PENDING, agent_id=agent.agent_id):
        action = item.action
        if action.get("kind") == kind and action.get("payload") == clean:
            raise ProposalRefused(
                FailureReason.BLOCKED_BY_POLICY,
                f"the same {kind} proposal is already waiting for the user ({item.id})",
            )
    item = await rt.approvals.enqueue(
        agent_id=agent.agent_id,
        trace_id=f"config:{agent.agent_id}:{session_id}"[:120],
        capability=capability_for(kind),
        action={
            "kind": kind,
            "payload": clean,
            "reason": _text(reason, limit=600),
            "session_id": session_id,
        },
        summary=summarize(kind, clean),
    )
    try:
        await rt.post_chat_notice(agent, proposal_notice(agent, item))
    except Exception:  # noqa: BLE001 — the queue holds the proposal; the card is a projection
        log.warning("society: proposal card not posted for %s", agent.agent_id, exc_info=True)
    return item
