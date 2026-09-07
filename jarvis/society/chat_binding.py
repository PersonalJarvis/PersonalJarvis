"""Binding an agent to its one canonical chat, and delivering board messages
into it.

Identity chain (agent-definition §2, build plan decision b): roster row →
session id ``society:<agent_id>`` (a pure function; no pointer column) →
``ensure_session`` mirrors the row's provider / model / effort onto the
session at bind time and re-seats it when the card changes (transcript
kept). Without a provider on the row the session runs on the Agents tier
the rest of the app uses (``local_models.assistant_session.agents_tier``).

``make_deliver_hook`` is what the scheduler calls for SAY / QUERY / ANSWER /
PROPOSE / RESULT envelopes: ensure the target's session, then start a turn
with the message framed as coming from the sender. A busy session raises
``target busy`` so the scheduler writes a typed veto and the envelope stays
in the inbox for the next turn.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from .delivery import DeliveryBusy, IncomingMessage, incoming_context
from .events import MsgType, SocietyEnvelope
from .roster import LEAD_AGENT_ID, AgentRecord, PermissionCeiling
from .scheduler import DeliverHook

log = logging.getLogger(__name__)

__all__ = [
    "SURFACE",
    "ensure_session",
    "frame_assignment",
    "frame_incoming",
    "make_deliver_hook",
    "pair_for",
]

SURFACE: Final[str] = "society"

#: Permission ceiling → the Jarvis-ladder stance the session runs in
#: (``jarvis/agent_chat/permissions.py``): safe reads only, monitor may edit
#: and runs commands after asking, ask asks for everything mutating.
_CEILING_TO_MODE: Final[dict[str, str]] = {
    str(PermissionCeiling.SAFE): "plan",
    str(PermissionCeiling.MONITOR): "accept-edits",
    str(PermissionCeiling.ASK): "ask",
}


def pair_for(cfg: Any, agent: AgentRecord) -> tuple[str, str, str]:
    """``(provider, model, effort)`` the agent's chat runs on."""
    if agent.provider:
        return agent.provider, agent.model, agent.effort
    from jarvis.local_models.assistant_session import agents_tier

    tier = agents_tier(cfg)
    if not tier.ready:
        raise PermissionError(tier.reason or "no provider can run the agent chat")
    return tier.provider, tier.model, agent.effort


def _workspace(cfg: Any, agent: AgentRecord) -> str:
    data_dir = Path(getattr(getattr(cfg, "memory", None), "data_dir", None) or "data")
    folder = data_dir / (agent.workspace_dir or f"society/{agent.agent_id}/workspace")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("society: workspace %s could not be created", folder, exc_info=True)
    try:
        folder = folder.resolve()
    except OSError:
        pass
    return str(folder)


def ensure_session(svc: Any, cfg: Any, agent: AgentRecord) -> Any:
    """The agent's canonical session, created or re-seated to the roster row."""
    from jarvis.agent_chat.effort import default_effort
    from jarvis.agent_chat.permissions import ladder_key, normalize_permission
    from jarvis.agent_chat.service import resolve_runner

    provider, model, effort = pair_for(cfg, agent)
    session_id = agent.session_id
    existing = svc.store.get_session(session_id)
    if existing is None:
        ladder = ladder_key(SURFACE, resolve_runner(provider, surface=SURFACE))
        mode = normalize_permission(ladder, _CEILING_TO_MODE[str(agent.permission_ceiling)])
        return svc.store.create_session(
            provider=provider,
            model=model,
            effort=effort or default_effort(provider),
            cwd=_workspace(cfg, agent),
            permission_mode=mode,
            title=agent.name,
            session_id=session_id,
            surface=SURFACE,
            account_id=agent.account_id,
        )
    if existing.provider != provider or (model and existing.model != model):
        svc.store.reseat_session(session_id, provider=provider, model=model or existing.model)
        existing = svc.store.get_session(session_id)
    updates: dict[str, str] = {}
    if getattr(existing, "account_id", "") != agent.account_id:
        updates["account_id"] = agent.account_id
    if effort and existing.effort != effort:
        updates["effort"] = effort
    if updates:
        svc.store.update_session(session_id, **updates)
        existing = svc.store.get_session(session_id)
    return existing


def frame_incoming(env: SocietyEnvelope, sender_name: str) -> str:
    """How a board envelope reads inside the receiving agent's chat."""
    kind = str(env.msg_type).lower()
    lines = [f"[{kind} from {sender_name}]"]
    if env.msg_type is MsgType.RESULT:
        p = env.payload
        lines.append(f"Status: {p.get('status', 'done')}")
        lines.append(f"Done: {p.get('done', '')}")
        if p.get("output"):
            lines.append("Output: " + ", ".join(str(x) for x in p.get("output", [])))
        if p.get("open"):
            lines.append("Open: " + "; ".join(str(x) for x in p.get("open", [])))
    text = env.text
    if text:
        lines.append(text)
    refs = env.payload.get("refs")
    if isinstance(refs, list) and refs:
        lines.append("Refs: " + ", ".join(str(r) for r in refs))
    return "\n".join(lines)


def frame_assignment(env: SocietyEnvelope) -> str:
    """An ASSIGN as the receiving agent's chat turn — with the handoff ask."""
    sender = env.from_agent if env.from_agent != "user" else "the user"
    task = env.text or str(env.payload.get("task") or "")
    lines = [f"[assignment from {sender}]", task.strip()]
    refs = env.payload.get("refs")
    if isinstance(refs, list) and refs:
        lines.append("Refs: " + ", ".join(str(r) for r in refs))
    lines.append(
        "When you are done, end with a handoff: what is done, where the output is, "
        "what evidence you used, what remains open, who should own the next step."
    )
    return "\n".join(lines)


def make_deliver_hook(
    get_service: Callable[[], Any | None],
    get_cfg: Callable[[], Any],
    resolve_name: Callable[[str], str] | None = None,
) -> DeliverHook:
    """The scheduler's deliver hook over the agent-chat service."""

    async def _receive(
        target: AgentRecord, env: SocietyEnvelope
    ) -> tuple[Any, Any, IncomingMessage]:
        svc = get_service()
        if svc is None:
            raise DeliveryBusy("agent chat service unavailable: message stays in the inbox")
        if resolve_name is not None:
            sender = resolve_name(env.from_agent)
        else:
            from .runtime import current_runtime

            runtime = current_runtime()
            record = await runtime.roster.get(env.from_agent) if runtime is not None else None
            sender = record.name if record is not None else env.from_agent
        incoming = IncomingMessage(
            message_id=env.event_id,
            sender_id=env.from_agent,
            sender_name=sender,
            sender_kind=("jarvis" if env.from_agent == LEAD_AGENT_ID else "user" if env.from_agent == "user" else "agent"),
            text=env.text,
            prompt=frame_incoming(env, sender),
            trace_id=env.trace_id,
        )
        if target.agent_id == LEAD_AGENT_ID:
            sessions = svc.store.list_sessions(limit=1, surface="jarvis")
            if not sessions:
                raise DeliveryBusy("Jarvis chat is not open yet")
            session = sessions[0]
        else:
            session = ensure_session(svc, get_cfg(), target)
        await svc.receive_message(session.session_id, incoming)
        return svc, session, incoming

    async def _deliver(target: AgentRecord, env: SocietyEnvelope) -> None:
        svc, session, incoming = await _receive(target, env)
        if target.agent_id == LEAD_AGENT_ID:
            receipt = svc.store.incoming_message(session.session_id, env.event_id)
            if receipt is not None and receipt["status"] != "delivered":
                await svc.message_status(session.session_id, env.event_id, "delivered")
            return
        receipt = svc.store.incoming_message(session.session_id, env.event_id)
        if receipt is not None and receipt["status"] == "delivered":
            return
        if svc.is_running(session.session_id):
            raise DeliveryBusy(f"target busy: {target.name} is running a turn")
        token = incoming_context.set(incoming)
        try:
            from jarvis.agent_chat.service import SessionBusy

            await svc.send(session.session_id, incoming.prompt, incoming=incoming)
        except SessionBusy as exc:
            raise DeliveryBusy(str(exc)) from exc
        finally:
            incoming_context.reset(token)

    # The scheduler can project later FIFO entries without starting their turns.
    setattr(_deliver, "receive", _receive)  # noqa: B010 — optional callable capability
    return _deliver
