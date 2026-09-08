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
    "apply",
    "capability_for",
    "kind_of",
    "propose",
    "proposal_notice",
    "resolve",
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
        operation = str(payload.get("operation") or "add")
        if operation not in {"add", "replace", "remove"}:
            raise ProposalRefused(FailureReason.BLOCKED_BY_POLICY, "unknown rule operation")
        text = _text(payload.get("text"), limit=_MAX_RULE)
        if not text and operation != "remove":
            raise ProposalRefused(FailureReason.BLOCKED_BY_POLICY, "a rule needs text")
        if operation != "add":
            old = _text(payload.get("old_text"), limit=_MAX_RULE)
            if not old:
                raise ProposalRefused(
                    FailureReason.BLOCKED_BY_POLICY, "a correction needs old_text"
                )
            return {"text": text, "operation": operation, "old_text": old}
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
        operation = str(payload.get("operation") or "create")
        if operation not in {"create", "update", "pause", "resume", "delete"}:
            raise ProposalRefused(FailureReason.BLOCKED_BY_POLICY, "unknown routine operation")
        task_id = str(payload.get("task_id") or "").strip()
        if operation != "create" and not task_id:
            raise ProposalRefused(FailureReason.BLOCKED_BY_POLICY, "task_id is required")
        if operation in {"pause", "resume", "delete"}:
            return {"operation": operation, "task_id": task_id}
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
        if operation == "update":
            out.update(operation=operation, task_id=task_id)
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
        line = f"{str(payload.get('operation') or 'add').capitalize()} a standing rule: " + str(
            payload.get("text") or payload.get("old_text") or ""
        )
    elif kind == "skill":
        line = f"Save the procedure as skill '{payload.get('name', '')}'"
    elif kind == "routine":
        schedule = payload.get("schedule") or {}
        operation = str(payload.get("operation") or "create")
        line = (
            f"{operation.capitalize()} routine {payload['task_id']}"
            if operation in {"pause", "resume", "delete"}
            else f"Schedule '{payload.get('title', '')}' ({schedule.get('kind', 'every')})"
        )
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


# ------------------------------------------------------------------- apply


def _merge_rules(current: dict[str, list[str]], add: dict[str, Any]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for key in ("require_approval", "always_allow"):
        seen = [x for x in current.get(key, []) if x]
        for pattern in _strings(add.get(key)):
            if pattern not in seen:
                seen.append(pattern)
        merged[key] = sorted(seen)
    return merged


async def _notify(rt: Any, agent: AgentRecord, item: Approval, *, status: str, text: str) -> None:
    try:
        await rt.post_chat_notice(agent, resolved_notice(agent, item, status=status, text=text))
    except Exception:  # noqa: BLE001 — the change is applied; the card is a projection
        log.warning("society: proposal outcome not posted for %s", agent.agent_id, exc_info=True)


async def _apply_skill_later(rt: Any, agent: AgentRecord, item: Approval, payload: dict) -> None:
    """Author the skill in the background (a model call) and report on the card."""
    from .learning import TurnDigest

    digest = TurnDigest(
        task=str(payload.get("goal") or ""),
        final_text=str(payload.get("outcome") or "") or "Done as described.",
        tool_steps=list(payload.get("steps") or []),
    )
    name = str(payload.get("name") or "")
    try:
        slug = await rt.learning.run(agent, digest, name_hint=name, force=True)
    except Exception:  # noqa: BLE001 — reported on the card, never lost in a log line alone
        log.warning(
            "society: skill proposal %s failed for %s", item.id, agent.agent_id, exc_info=True
        )
        slug = None
    if slug:
        await _notify(rt, agent, item, status="applied", text=f"Skill '{name}' saved as {slug}.")
    else:
        await _notify(
            rt,
            agent,
            item,
            status="failed",
            text=f"Skill '{name}' could not be authored (no brain, or the daily cap is reached).",
        )


async def apply(
    rt: Any, item: Approval, *, task_store: Any = None, scheduler: Any = None
) -> dict[str, Any]:
    """Apply one APPROVED proposal; returns ``{"applied", "detail", "kind"}``.

    Never routed through ``PATCH /agents/{id}``: that route re-derives focus
    and may overwrite approval rules, and a confirmed change must touch
    exactly the field the person confirmed.
    """
    action = item.action
    kind = str(action.get("kind") or kind_of(item.capability) or "")
    payload = action.get("payload") or {}
    agent = await rt.roster.get(item.agent_id)
    if agent is None:
        return {"applied": False, "detail": "the agent no longer exists", "kind": kind}
    if not isinstance(payload, dict) or kind not in PROPOSAL_KINDS:
        return {"applied": False, "detail": "malformed proposal", "kind": kind}
    if kind == "rule":
        addition = str(payload.get("text") or "")
        operation = payload.get("operation", "add")
        if operation == "add":
            text = (
                agent.description
                if addition in agent.description
                else (agent.description.rstrip() + "\n\n" + addition).strip()
            )
        else:
            old = str(payload.get("old_text") or "")
            if not old or agent.description.count(old) != 1:
                return {
                    "applied": False,
                    "detail": "old_text must match exactly one standing rule",
                    "kind": kind,
                }
            text = agent.description.replace(
                old, addition if operation == "replace" else ""
            ).strip()
        await rt.roster.update(agent.agent_id, {"description": text})
        return {"applied": True, "detail": f"standing rule {operation} applied", "kind": kind}
    if kind == "approval_rule":
        merged = _merge_rules(agent.approval_rules, payload)
        await rt.roster.update(agent.agent_id, {"approval_rules": merged})
        return {"applied": True, "detail": "approval rules updated", "kind": kind}
    if kind == "focus":
        focus = _strings(payload.get("focus"))
        await rt.roster.update(agent.agent_id, {"focus": focus})
        return {"applied": True, "detail": "focus order updated", "kind": kind}
    if kind == "routine":
        from .routines import (
            MAX_ROUTINES_PER_AGENT,
            build_task_spec,
            count_routines,
            create_routine,
        )

        if task_store is None:
            return {"applied": False, "detail": "the task store is not available", "kind": kind}
        operation = str(payload.get("operation") or "create")
        if operation != "create":
            from .routines import manage_routine

            try:
                detail = await manage_routine(agent, payload, task_store, scheduler)
            except (ValueError, KeyError, RuntimeError) as exc:
                return {"applied": False, "detail": str(exc), "kind": kind}
            return {"applied": True, "detail": detail, "kind": kind}
        if await count_routines(task_store, agent.agent_id) >= MAX_ROUTINES_PER_AGENT:
            return {"applied": False, "detail": "routine cap reached", "kind": kind}
        try:
            spec = build_task_spec(
                agent,
                title=str(payload.get("title") or ""),
                prompt=str(payload.get("prompt") or ""),
                schedule=dict(payload.get("schedule") or {}),
                announce_on_success=payload.get("announce_on_success"),
            )
        except (ValueError, KeyError) as exc:
            return {"applied": False, "detail": f"invalid routine: {exc}", "kind": kind}
        task_id = await create_routine(task_store, scheduler, spec)
        return {"applied": True, "detail": f"routine scheduled ({task_id})", "kind": kind}
    if kind == "skill":
        rt.background(_apply_skill_later(rt, agent, item, payload))
        return {"applied": True, "detail": "authoring the skill", "kind": kind}
    # team — onboarding
    from .seeds import create_from_proposals

    created = await create_from_proposals(rt.roster, rt.catalog(), _strings(payload.get("names")))
    detail = "created " + ", ".join(created) if created else "no teammate created"
    return {"applied": bool(created), "detail": detail, "kind": kind}


async def resolve(
    rt: Any,
    approval_id: str,
    *,
    approve: bool,
    note: str = "",
    task_store: Any = None,
    scheduler: Any = None,
) -> dict[str, Any]:
    """The person decided: record it, apply on yes, and report on the card.

    Raises ``KeyError`` for an unknown id and ``ValueError`` when the item is
    not a proposal or is already resolved.
    """
    current = await rt.approvals.get(approval_id)
    if current is None:
        raise KeyError(approval_id)
    if kind_of(current.capability) is None:
        raise ValueError("not a configuration proposal")
    if current.state in (ApprovalState.APPROVED, ApprovalState.DENIED):
        raise ValueError("already resolved")
    if note.strip() and approve and current.action.get("kind") == "team":
        # The card sends the picked names in the note (a comma-separated list).
        picked = _strings([n.strip() for n in note.split(",")])
        if picked:
            current.action["payload"] = {**current.action.get("payload", {}), "names": picked}
    item = await rt.approvals.resolve(approval_id, approve=approve, note=note)
    item.action = current.action  # the picked names ride along for apply
    agent = await rt.roster.get(item.agent_id)
    if not approve:
        if agent is not None:
            await _notify(rt, agent, item, status="rejected", text="Rejected; nothing changed.")
        return {"proposal": item.to_dict(), "applied": False, "detail": "rejected"}
    outcome = await apply(rt, item, task_store=task_store, scheduler=scheduler)
    if agent is not None and outcome["kind"] != "skill":
        await _notify(
            rt,
            agent,
            item,
            status="applied" if outcome["applied"] else "failed",
            text=str(outcome["detail"]),
        )
    return {"proposal": item.to_dict(), **outcome}
