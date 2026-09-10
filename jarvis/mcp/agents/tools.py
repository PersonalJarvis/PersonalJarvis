"""The Agent MCP tool set — the ecosystem's outward vocabulary.

Design rules this surface holds itself to, because it is meant to be a standard
other clients can rely on rather than a projection of today's REST paths:

1. **One tool per intent, not per endpoint.** ``agent_chat`` is a conversation;
   the fact that it seats a session, subscribes, sends and waits is ours to
   carry, not the caller's.
2. **Every answer is JSON with the field names the REST layer already uses**
   (snake_case, ``agent_id``, ``run_state``, ``seq``). A client that has read
   ``/api/society`` needs no second vocabulary.
3. **A refusal is typed and says what to do next.** Never a bare traceback: an
   unknown agent lists the known ones, a busy agent says so, a stopped
   ecosystem says the kill switch is on.
4. **Anything that starts spend or work is marked** ``dangerous`` and says so in
   its own description, so a client can gate it and the model reading the
   catalog knows before it calls.

The safety seam is the one the rest of the house uses. Work is started through
the scheduler (kill switch, tier wall, depth ≤ 2, budgets — AP-5/AP-14), the
board is append-only and persist-before-publish, and no tool here can reach a
spawn vehicle. An MCP client is a person with a keyboard, not a second router.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

from .context import EcosystemUnavailable, resolve_agent, society

log = logging.getLogger(__name__)

#: What ``SocietyEnvelope.from_agent`` carries for a message an MCP client sent.
#: Distinguishable from ``"user"`` (the person at the app) in the board history,
#: so a later reader can tell a remote client's word from a spoken one.
MCP_ORIGIN: Final[str] = "user"

#: Ceiling for ``agent_chat``'s wait. A conversation that has not answered in
#: ten minutes is a background task; the caller gets the turn id to follow.
MAX_WAIT_S: Final[float] = 600.0
DEFAULT_WAIT_S: Final[float] = 180.0

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class AgentTool:
    """One tool on the surface: what it is called, what it takes, what it does."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    dangerous: bool = False
    tags: tuple[str, ...] = field(default=())


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


_AGENT_REF = {
    "type": "string",
    "description": "The agent's id or its name (both work).",
}


# --------------------------------------------------------------- discovery


async def _ecosystem_status(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    status = await rt.status()
    agents = await rt.roster.list()
    running = rt.scheduler.running
    await rt.approvals.expire_due()
    pending = await rt.approvals.pending()
    by_state: dict[str, int] = {}
    for agent in agents:
        if agent.state == "paused":
            key = "paused"
        elif agent.agent_id in running.values():
            key = "working"
        else:
            key = "idle"
        by_state[key] = by_state.get(key, 0) + 1
    return {
        "kill_switch": status["kill_switch"],
        "agents_total": len(agents),
        "agents_by_run_state": by_state,
        "active_runs": status["active_runs"],
        "rooms_running": status["rooms_running"],
        "approvals_pending": len(pending),
        "board_last_seq": status["last_seq"],
        "lead": rt.lead_id,
        "capabilities_connected": len(rt.catalog()),
    }


async def _agents_list(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    include_archived = bool(args.get("include_archived"))
    agents = await rt.roster.list(include_archived=include_archived)
    running = rt.scheduler.running
    rows = []
    for agent in agents:
        row = agent.to_dict()
        if agent.state == "paused":
            row["run_state"] = "paused"
        elif agent.agent_id in running.values():
            row["run_state"] = "working"
        else:
            row["run_state"] = "idle"
        rows.append(row)
    return {"agents": rows, "total": len(rows)}


async def _agent_get(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    agent = await resolve_agent(rt, str(args["agent"]))
    limit = max(1, min(int(args.get("recent_events") or 20), 200))
    events = await rt.store.events_for_agent(agent.agent_id, limit=limit)
    return {
        "agent": agent.to_dict(),
        "recent_events": [e.model_dump(mode="json") for e in events],
        "active_runs": rt.scheduler.active_runs(agent.agent_id),
        "skills": [s for s in _agent_skill_names(rt, agent.agent_id)],
    }


def _agent_skill_names(rt: Any, agent_id: str) -> list[str]:
    try:
        skills = rt.skills_for(agent_id)
    except Exception:  # noqa: BLE001 — a broken skill store costs the list, not the call
        log.debug("agent MCP: skills unavailable for %s", agent_id, exc_info=True)
        return []
    listing = getattr(skills, "list", None)
    if not callable(listing):
        return []
    try:
        return [str(getattr(s, "slug", s)) for s in listing()]
    except Exception:  # noqa: BLE001 — same
        log.debug("agent MCP: skill listing failed for %s", agent_id, exc_info=True)
        return []


async def _capabilities_list(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    kind = str(args.get("kind") or "").strip().lower()
    rows = [row.to_dict() for row in rt.catalog()]
    if kind:
        rows = [r for r in rows if str(r.get("kind", "")).lower() == kind]
    return {"capabilities": rows, "total": len(rows)}


# ----------------------------------------------------------------- roster


async def _agent_create(args: dict[str, Any]) -> dict[str, Any]:
    from jarvis.society.roster import RosterError

    rt = await society()
    title = str(args["title"])
    description = str(args["description"])
    focus, rules = rt.derive(title, description)
    fields: dict[str, Any] = {}
    if focus:
        fields["focus"] = focus
    if rules.get("require_approval"):
        fields["approval_rules"] = rules
    try:
        agent, created = await rt.roster.create(
            name=str(args["name"]),
            title=title,
            description=description,
            tier=str(args.get("tier") or "specialist"),
            **fields,
        )
    except RosterError as exc:
        raise _refuse_typed(exc) from exc
    return {"agent": agent.to_dict(), "created": created}


# ---------------------------------------------------------- conversation


async def _agent_chat(args: dict[str, Any]) -> dict[str, Any]:
    """Write to an agent and wait for its answer — the surface's centre.

    This is the same path a person's keyboard takes in the app: the agent's
    canonical chat session, seated from its roster row (provider, model,
    effort, permission ceiling, workspace), one turn, one answer. It is NOT the
    scheduler — a conversation is not a job — so the tier wall and budgets do
    not apply, but the kill switch does: a stopped ecosystem answers nobody.
    """
    from jarvis.agent_chat.service import SessionBusy
    from jarvis.society.chat_binding import ensure_session

    rt = await society()
    if await rt.store.kill_switch():
        raise EcosystemUnavailable(
            "the ecosystem",
            "the kill switch is engaged — nothing runs. Release it with kill_switch first.",
        )
    agent = await resolve_agent(rt, str(args["agent"]))
    text = str(args["text"]).strip()
    if not text:
        raise ValueError("text must not be empty")
    wait_s = min(float(args.get("timeout_s") or DEFAULT_WAIT_S), MAX_WAIT_S)

    svc = rt._get_chat()  # noqa: SLF001 — this module is the runtime's operator, as the routes are
    if svc is None:
        raise EcosystemUnavailable(
            "agent chat", "the chat service is not running, so agents cannot be talked to."
        )
    try:
        session = ensure_session(svc, rt._get_cfg(), agent)  # noqa: SLF001 — same
    except PermissionError as exc:
        # No provider can run this agent — an unconfigured box, not a bug. The
        # message already says what to set up, so pass it through rather than
        # burying it in a traceback the caller cannot act on.
        raise EcosystemUnavailable(f"agent {agent.name}", str(exc)) from exc
    if svc.is_running(session.session_id):
        raise EcosystemUnavailable(
            f"agent {agent.name}",
            "it is in the middle of a turn. Wait for it to finish, then ask again.",
        )

    queue = svc.subscribe(session.session_id)
    try:
        try:
            turn_id = await svc.send(session.session_id, text)
        except SessionBusy as exc:
            raise EcosystemUnavailable(
                f"agent {agent.name}", "it started another turn just now. Try again shortly."
            ) from exc
        reply = await _await_turn(queue, turn_id, wait_s)
    finally:
        svc.unsubscribe(session.session_id, queue)

    return {
        "agent_id": agent.agent_id,
        "agent_name": agent.name,
        "session_id": session.session_id,
        "turn_id": turn_id,
        **reply,
    }


async def _await_turn(queue: Any, turn_id: str, wait_s: float) -> dict[str, Any]:
    """Drain one turn's events into a reply, or say honestly that it is still running."""
    text = ""
    tools: list[str] = []
    deadline = asyncio.get_running_loop().time() + wait_s
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return {
                "status": "still_running",
                "reply": text,
                "tool_calls": tools,
                "note": (
                    f"The agent is still working after {wait_s:.0f}s. "
                    "Its answer lands on the board — read it with agent_inbox or board_events."
                ),
            }
        try:
            event = await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            continue
        payload = event.get("payload") or {}
        if payload.get("turn_id") not in (None, turn_id):
            continue
        kind = event.get("kind")
        if kind == "assistant_text":
            text = str(payload.get("text") or text)
        elif kind == "tool_call":
            name = str(payload.get("name") or payload.get("tool") or "tool")
            tools.append(name)
        elif kind == "error":
            return {
                "status": "blocked",
                "reply": text,
                "tool_calls": tools,
                "error": str(payload.get("message") or "the turn failed"),
            }
        elif kind == "turn_finished":
            status = payload.get("status")
            ok = status in (None, "ok", "done", "completed")
            return {
                "status": "answered" if ok else "blocked",
                "reply": text,
                "tool_calls": tools,
                **({} if ok else {"error": str(payload.get("error") or status or "")}),
            }


async def _agent_message(args: dict[str, Any]) -> dict[str, Any]:
    """A note on the board. Delivered, not answered — use agent_chat for a reply."""
    from jarvis.society.events import MsgType

    rt = await society()
    agent = await resolve_agent(rt, str(args["agent"]))
    raw = str(args.get("msg_type") or "SAY").upper()
    try:
        msg_type = MsgType(raw)
    except ValueError as exc:
        allowed = ["SAY", "QUERY", "PROPOSE", "HOLD", "RELEASE"]
        raise ValueError(f"msg_type must be one of {allowed}") from exc
    if msg_type in (MsgType.ASSIGN, MsgType.ROOM_OPEN, MsgType.ROOM_SETTLE, MsgType.VETO):
        raise ValueError(f"{raw} is not a message — use agent_assign or the room_* tools")
    env = await rt.say(
        from_agent=str(args.get("from_agent") or MCP_ORIGIN),
        to_agent=agent.agent_id,
        text=str(args["text"]),
        msg_type=msg_type,
        payload=args.get("payload") or None,
    )
    return {"event": env.model_dump(mode="json"), "delivered_to": agent.name}


async def _agent_assign(args: dict[str, Any]) -> dict[str, Any]:
    """A task the scheduler turns into real work — the spending path."""
    from jarvis.missions.ids import uuid7_str
    from jarvis.society.events import MsgType

    rt = await society()
    agent = await resolve_agent(rt, str(args["agent"]))
    task = str(args["task"]).strip()
    if not task:
        raise ValueError("task must not be empty")
    payload: dict[str, Any] = {"text": task}
    runner = str(args.get("runner") or "").strip()
    if runner:
        if runner not in ("chat", "mission"):
            raise ValueError("runner must be 'chat' (a turn) or 'mission' (an isolated worktree)")
        payload["runner"] = runner
    env = await rt.say(
        from_agent=str(args.get("from_agent") or MCP_ORIGIN),
        to_agent=agent.agent_id,
        text=task,
        trace_id=f"task:{uuid7_str()}",
        msg_type=MsgType.ASSIGN,
        payload=payload,
    )
    # The scheduler answers on the same trace: a CLAIM when work started, a
    # VETO with a typed reason when it refused. Both are already stored by the
    # time say() returns, because the board persists before it publishes.
    trail = await rt.store.events_for_trace(env.trace_id)
    verdict = next((e for e in trail if e.seq and env.seq and e.seq > env.seq), None)
    return {
        "assigned_to": agent.name,
        "trace_id": env.trace_id,
        "event": env.model_dump(mode="json"),
        "outcome": verdict.model_dump(mode="json") if verdict else None,
        "accepted": bool(verdict and str(verdict.msg_type) != "VETO"),
    }


# ---------------------------------------------------------------- quests


async def _quest_post(args: dict[str, Any]) -> dict[str, Any]:
    """Post a job without naming who does it — the ecosystem picks, or forges.

    The highest-value verb on this surface for a caller who knows what they
    want but not who on the team should do it. Routing is deterministic Python
    (focus overlap, names mentioned, load), never a model, and the answer says
    WHY the taker was chosen — including when a new teammate was forged for it.
    """
    from jarvis.society.roster import RosterError

    rt = await society()
    text = str(args["text"]).strip()
    if not text:
        raise ValueError("text must not be empty — a quest is a job in one sentence")
    try:
        quest = await rt.quests.create(
            text,
            title=str(args.get("title") or "") or None,
            lang=str(args.get("lang") or "") or None,
        )
    except RosterError as exc:
        raise _refuse_typed(exc) from exc
    row = quest.to_dict()
    return {
        "quest": row,
        "taker": row.get("agent_id"),
        "why": (row.get("routing") or {}).get("reason"),
        "forged_a_new_agent": bool((row.get("routing") or {}).get("forged")),
    }


async def _quests_list(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    limit = max(1, min(int(args.get("limit") or 100), 1000))
    state = str(args.get("state") or "").strip() or None
    quests = await rt.quests.list(state=state, limit=limit)
    return {"quests": [q.to_dict() for q in quests], "total": len(quests)}


async def _quest_get(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    quest_id = str(args["quest_id"])
    quest = await rt.quests.get(quest_id)
    if quest is None:
        raise EcosystemUnavailable(
            f"quest {quest_id!r}", "no quest by that id. List them with quests_list."
        )
    events = await rt.store.events_for_trace(quest.trace_id)
    return {
        "quest": quest.to_dict(),
        "events": [e.model_dump(mode="json") for e in events],
    }


async def _quest_cancel(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    quest_id = str(args["quest_id"])
    try:
        quest = await rt.quests.cancel(quest_id)
    except KeyError as exc:
        raise EcosystemUnavailable(f"quest {quest_id!r}", "no quest by that id.") from exc
    return {"quest": quest.to_dict()}


async def _quest_retry(args: dict[str, Any]) -> dict[str, Any]:
    from jarvis.society.roster import RosterError

    rt = await society()
    quest_id = str(args["quest_id"])
    try:
        quest = await rt.quests.retry(quest_id)
    except KeyError as exc:
        raise EcosystemUnavailable(f"quest {quest_id!r}", "no quest by that id.") from exc
    except RosterError as exc:
        raise _refuse_typed(exc) from exc
    row = quest.to_dict()
    return {"quest": row, "taker": row.get("agent_id")}


# ------------------------------------------------------------- portability


async def _ecosystem_export(args: dict[str, Any]) -> dict[str, Any]:
    """This ecosystem's design as a portable bundle — no secrets, no history."""
    from .portable import export_bundle

    rt = await society()
    return await export_bundle(rt, include_archived=bool(args.get("include_archived")))


async def _ecosystem_import(args: dict[str, Any]) -> dict[str, Any]:
    """Apply a bundle from another machine. Adopts by name, mints what is new."""
    from .portable import BundleError, import_bundle

    rt = await society()
    bundle = args.get("bundle")
    if isinstance(bundle, str):
        try:
            bundle = json.loads(bundle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"bundle is not valid JSON: {exc}") from exc
    try:
        return await import_bundle(
            rt,
            bundle,
            overwrite=bool(args.get("overwrite", True)),
            dry_run=bool(args.get("dry_run", False)),
        )
    except BundleError as exc:
        raise EcosystemUnavailable("that bundle", str(exc)) from exc


# -------------------------------------------------------------- the board


async def _agent_inbox(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    agent = await resolve_agent(rt, str(args["agent"]))
    after_seq = int(args.get("after_seq") or 0)
    events = await rt.store.inbox_for(agent.agent_id, after_seq=after_seq)
    return {
        "agent_id": agent.agent_id,
        "events": [e.model_dump(mode="json") for e in events],
        "last_seq": await rt.store.last_seq(),
    }


async def _board_events(args: dict[str, Any]) -> dict[str, Any]:
    """The ecosystem's stream. Poll with the returned ``last_seq`` to follow it."""
    rt = await society()
    limit = max(1, min(int(args.get("limit") or 100), 1000))
    trace_id = str(args.get("trace_id") or "").strip()
    agent_ref = str(args.get("agent") or "").strip()
    after_seq = int(args.get("after_seq") or 0)
    if trace_id:
        events = await rt.store.events_for_trace(trace_id)
    elif agent_ref:
        agent = await resolve_agent(rt, agent_ref)
        events = await rt.store.events_for_agent(agent.agent_id, after_seq=after_seq, limit=limit)
    else:
        events = await rt.store.events_since(after_seq, limit=limit)
    return {
        "events": [e.model_dump(mode="json") for e in events],
        "last_seq": await rt.store.last_seq(),
    }


# ----------------------------------------------------------------- rooms


async def _room_open(args: dict[str, Any]) -> dict[str, Any]:
    from jarvis.society.rooms import RoomError

    rt = await society()
    members = [str(m) for m in (args.get("members") or [])]
    if len(members) < 2:
        raise ValueError("a room needs at least 2 members")
    resolved = [(await resolve_agent(rt, m)).agent_id for m in members]
    try:
        room = await rt.rooms.open(
            opened_by=str(args.get("opened_by") or MCP_ORIGIN),
            members=resolved,
            topic=str(args["topic"]),
        )
    except RoomError as exc:
        raise _refuse_typed(exc) from exc
    return {"room": room.to_dict()}


async def _room_say(args: dict[str, Any]) -> dict[str, Any]:
    from jarvis.society.rooms import RoomError

    rt = await society()
    member = str(args["member"])
    resolved = (await resolve_agent(rt, member)).agent_id
    try:
        room = await rt.rooms.say(str(args["room_id"]), resolved, str(args["text"]))
    except RoomError as exc:
        raise _refuse_typed(exc) from exc
    return {"room": room.to_dict()}


async def _room_settle(args: dict[str, Any]) -> dict[str, Any]:
    from jarvis.society.rooms import RoomError

    rt = await society()
    try:
        room = await rt.rooms.settle(str(args["room_id"]), reason="mcp-client", by="user")
    except RoomError as exc:
        raise _refuse_typed(exc) from exc
    return {"room": room.to_dict()}


async def _rooms_list(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    return {"rooms": [r.to_dict() for r in await rt.rooms.list()]}


# ------------------------------------------------------------- governance


async def _approvals_list(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    await rt.approvals.expire_due()
    items = await rt.approvals.pending()
    ref = str(args.get("agent") or "").strip()
    if ref:
        agent = await resolve_agent(rt, ref)
        items = [a for a in items if a.agent_id == agent.agent_id]
    return {"approvals": [a.to_dict() for a in items], "total": len(items)}


async def _approval_resolve(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    approve = bool(args["approve"])
    try:
        item = await rt.approvals.resolve(
            str(args["approval_id"]), approve=approve, note=str(args.get("note") or "")
        )
    except KeyError as exc:
        raise EcosystemUnavailable(
            f"approval {args['approval_id']!r}",
            "no pending approval by that id. List them with approvals_list.",
        ) from exc
    return {"approval": item.to_dict()}


async def _kill_switch(args: dict[str, Any]) -> dict[str, Any]:
    rt = await society()
    if bool(args["engage"]):
        return await rt.engage_kill_switch()
    return await rt.release_kill_switch()


# ------------------------------------------------------------------ error


def _refuse_typed(exc: Any) -> EcosystemUnavailable:
    """A ``RosterError``/``RoomError`` as a refusal that names the retry action."""
    from jarvis.society.failure_reasons import retry_action

    reason = getattr(exc, "reason", None)
    hint = f"{exc}"
    if reason is not None:
        hint = f"{exc} (reason: {reason}; retry: {retry_action(reason)})"
    return EcosystemUnavailable("that action", hint)


# ------------------------------------------------------------- the catalog


async def _routine_invoke(args: dict[str, Any]) -> dict[str, Any]:
    from uuid import UUID, uuid4

    from jarvis.tasks.hook_inbox import encode_payload

    rt = await society()
    if await rt.store.kill_switch():
        raise EcosystemUnavailable("routine invocation", "the society is halted")
    _, scheduler = rt.task_services()
    if scheduler is None:
        raise EcosystemUnavailable("routine scheduler", "wait for startup")
    task_id = str(UUID(str(args["task_id"])))
    payload = args.get("payload") or {}
    encode_payload(payload)
    status = await scheduler.invoke_source(
        task_id, payload, str(args.get("delivery_id") or uuid4()), mode="mcp"
    )
    return {"task_id": task_id, "status": status}


async def _routine_status(args: dict[str, Any]) -> dict[str, Any]:
    from uuid import UUID

    rt = await society()
    store, _ = rt.task_services()
    if store is None:
        raise EcosystemUnavailable("routine store", "wait for startup")
    task_id = str(UUID(str(args["task_id"])))
    row = await store.get(task_id)
    if row is None:
        raise ValueError("Routine not found")
    return {
        "task_id": task_id,
        "state": row["state"],
        "error": row.get("last_error"),
        "result": (await store.latest_agent_results([task_id])).get(task_id, ""),
    }


TOOLS: Final[tuple[AgentTool, ...]] = (
    AgentTool(
        name="routine_status",
        description="Read a routine's state and most recent result after invocation.",
        input_schema=_obj({"task_id": {"type": "string"}}, ["task_id"]),
        handler=_routine_status,
        tags=("routines",),
    ),
    AgentTool(
        name="routine_invoke",
        description=(
            "Submit data to an MCP-triggered routine. Queued means durably accepted, "
            "not completed. Reuse delivery_id when retrying."
        ),
        input_schema=_obj(
            {
                "task_id": {"type": "string"},
                "payload": {"type": "object"},
                "delivery_id": {"type": "string", "maxLength": 128},
            },
            ["task_id"],
        ),
        handler=_routine_invoke,
        dangerous=True,
        tags=("routines",),
    ),
    AgentTool(
        name="ecosystem_status",
        description=(
            "One glance at the whole agent ecosystem: how many agents exist and what "
            "they are doing, active runs, running rooms, pending approvals, the "
            "connected capability count, and whether the kill switch is engaged. "
            "Call this first when you do not know the state of the house."
        ),
        input_schema=_obj({}),
        handler=_ecosystem_status,
        tags=("discovery",),
    ),
    AgentTool(
        name="agents_list",
        description=(
            "Every agent on this Jarvis: id, name, title, tier, model, focus, "
            "permission ceiling and run_state (idle / working / paused). "
            "The roster is the team — start here to know who you can talk to."
        ),
        input_schema=_obj(
            {
                "include_archived": {
                    "type": "boolean",
                    "description": "Include retired agents. Default false.",
                }
            }
        ),
        handler=_agents_list,
        tags=("discovery",),
    ),
    AgentTool(
        name="agent_get",
        description=(
            "One agent in full: its roster row, its most recent board events, the "
            "runs it currently owns, and the skills it has learned."
        ),
        input_schema=_obj(
            {
                "agent": _AGENT_REF,
                "recent_events": {
                    "type": "integer",
                    "description": "How many recent events to include (1-200, default 20).",
                },
            },
            ["agent"],
        ),
        handler=_agent_get,
        tags=("discovery",),
    ),
    AgentTool(
        name="agent_chat",
        description=(
            "Write to an agent and get its answer back — the normal way to talk to "
            "the team. Runs one turn on that agent's own session (its provider, "
            "model, tools and briefing) and returns the reply text plus the tools it "
            "used. Waits up to timeout_s; if the agent is still working when that "
            "expires you get status 'still_running' and can follow it with "
            "agent_inbox. Costs money — it runs a model turn."
        ),
        input_schema=_obj(
            {
                "agent": _AGENT_REF,
                "text": {"type": "string", "description": "What to say to the agent."},
                "timeout_s": {
                    "type": "number",
                    "description": "How long to wait for the answer (default 180, max 600).",
                },
            },
            ["agent", "text"],
        ),
        handler=_agent_chat,
        dangerous=True,
        tags=("conversation",),
    ),
    AgentTool(
        name="agent_message",
        description=(
            "Leave a note on an agent's board — delivered to it, but it does NOT "
            "start a turn and there is no reply. Use agent_chat when you want an "
            "answer, agent_assign when you want work done."
        ),
        input_schema=_obj(
            {
                "agent": _AGENT_REF,
                "text": {"type": "string"},
                "msg_type": {
                    "type": "string",
                    "enum": ["SAY", "QUERY", "PROPOSE", "HOLD", "RELEASE"],
                    "description": "The envelope type. Default SAY.",
                },
                "from_agent": {
                    "type": "string",
                    "description": "Who it is from. Default 'user' (the person).",
                },
                "payload": {"type": "object", "description": "Extra structured fields."},
            },
            ["agent", "text"],
        ),
        handler=_agent_message,
        dangerous=True,
        tags=("conversation",),
    ),
    AgentTool(
        name="agent_assign",
        description=(
            "Give an agent a task. The scheduler — not a model — decides whether it "
            "runs: kill switch, tier wall, depth limit and budgets all apply, and a "
            "refusal comes back as a typed VETO with a reason. runner 'chat' does it "
            "in a turn; runner 'mission' does it in an isolated git worktree for "
            "heavy coding work. Costs money."
        ),
        input_schema=_obj(
            {
                "agent": _AGENT_REF,
                "task": {"type": "string", "description": "What the agent should do."},
                "runner": {
                    "type": "string",
                    "enum": ["chat", "mission"],
                    "description": "How to run it. Default: chat when available.",
                },
                "from_agent": {"type": "string", "description": "Who assigns. Default 'user'."},
            },
            ["agent", "task"],
        ),
        handler=_agent_assign,
        dangerous=True,
        tags=("work",),
    ),
    AgentTool(
        name="quest_post",
        description=(
            "Post a job on the Quest Board WITHOUT choosing who does it. The "
            "ecosystem routes it: deterministic Python scores every agent on focus "
            "overlap, whether the quest names them, and current load — and if "
            "nobody fits, it forges a new teammate for the job. The answer names "
            "the taker and why. This is the tool to reach for when you know what "
            "you want done but not who should do it. Costs money."
        ),
        input_schema=_obj(
            {
                "text": {
                    "type": "string",
                    "description": "The job, in one or two sentences.",
                },
                "title": {"type": "string", "description": "Short label. Derived if omitted."},
                "lang": {
                    "type": "string",
                    "description": "Language for the agent's answer, e.g. 'de'.",
                },
            },
            ["text"],
        ),
        handler=_quest_post,
        dangerous=True,
        tags=("quests", "work"),
    ),
    AgentTool(
        name="quests_list",
        description=(
            "The Quest Board: every posted job, newest first, with who took it, "
            "why, and where it stands. Filter by state to see only what is open."
        ),
        input_schema=_obj(
            {
                "state": {
                    "type": "string",
                    "description": "Only this state (e.g. open, running, done, failed).",
                },
                "limit": {"type": "integer", "description": "1-1000, default 100."},
            }
        ),
        handler=_quests_list,
        tags=("quests",),
    ),
    AgentTool(
        name="quest_get",
        description=(
            "One quest in full: its state, its taker and the routing reason, plus "
            "every board event on its trace — the whole story of one job."
        ),
        input_schema=_obj({"quest_id": {"type": "string"}}, ["quest_id"]),
        handler=_quest_get,
        tags=("quests",),
    ),
    AgentTool(
        name="quest_cancel",
        description="Stop a quest. Work already running for it is dropped.",
        input_schema=_obj({"quest_id": {"type": "string"}}, ["quest_id"]),
        handler=_quest_cancel,
        dangerous=True,
        tags=("quests",),
    ),
    AgentTool(
        name="quest_retry",
        description=(
            "Route a failed or open quest again. The taker may come out different "
            "the second time — the roster or the load has changed. Costs money."
        ),
        input_schema=_obj({"quest_id": {"type": "string"}}, ["quest_id"]),
        handler=_quest_retry,
        dangerous=True,
        tags=("quests",),
    ),
    AgentTool(
        name="ecosystem_export",
        description=(
            "This ecosystem's design as a portable bundle: every agent with its "
            "role, focus, model, permissions and budget. Carries NO secrets, no "
            "chat history and no board — only the team you built. Save the JSON "
            "and hand it to ecosystem_import on another machine."
        ),
        input_schema=_obj(
            {
                "include_archived": {
                    "type": "boolean",
                    "description": "Include retired agents. Default false.",
                }
            }
        ),
        handler=_ecosystem_export,
        tags=("portability",),
    ),
    AgentTool(
        name="ecosystem_import",
        description=(
            "Bring a team over from another machine. Agents already here are "
            "matched BY NAME and updated; the rest are created. Run it with "
            "dry_run first to see exactly what it would do — importing twice "
            "changes nothing the second time."
        ),
        input_schema=_obj(
            {
                "bundle": {
                    "type": "object",
                    "description": "The bundle from ecosystem_export.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Report the plan and change nothing. Default false.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "Update agents that already exist here. Default true; "
                        "false leaves them untouched and reports them as skipped."
                    ),
                },
            },
            ["bundle"],
        ),
        handler=_ecosystem_import,
        dangerous=True,
        tags=("portability",),
    ),
    AgentTool(
        name="agent_inbox",
        description=(
            "Everything addressed to one agent, oldest first. Pass after_seq with the "
            "last_seq you got back to read only what is new — this is how you follow "
            "a conversation or an assignment without re-reading history."
        ),
        input_schema=_obj(
            {
                "agent": _AGENT_REF,
                "after_seq": {"type": "integer", "description": "Only events after this seq."},
            },
            ["agent"],
        ),
        handler=_agent_inbox,
        tags=("board",),
    ),
    AgentTool(
        name="board_events",
        description=(
            "The ecosystem's event stream — every message, assignment, claim, result "
            "and veto, in order. Filter by agent or by trace_id to follow one piece "
            "of work end to end, or poll with after_seq to watch the house live."
        ),
        input_schema=_obj(
            {
                "after_seq": {"type": "integer"},
                "agent": {"type": "string", "description": "Only this agent's events."},
                "trace_id": {"type": "string", "description": "One task's full trail."},
                "limit": {"type": "integer", "description": "1-1000, default 100."},
            }
        ),
        handler=_board_events,
        tags=("board",),
    ),
    AgentTool(
        name="agent_create",
        description=(
            "Add a teammate. Focus and approval rules are derived from the title and "
            "description — no model call, deterministic. Creating an agent executes "
            "nothing; it only puts a row on the roster."
        ),
        input_schema=_obj(
            {
                "name": {"type": "string", "description": "Short unique handle, e.g. 'Scout'."},
                "title": {"type": "string", "description": "Its role, e.g. 'Research analyst'."},
                "description": {
                    "type": "string",
                    "description": "What it is for. Drives its focus and approval rules.",
                },
                "tier": {
                    "type": "string",
                    "enum": ["orchestrator", "specialist"],
                    "description": "Default specialist. The lead seat is Jarvis' and is taken.",
                },
            },
            ["name", "title", "description"],
        ),
        handler=_agent_create,
        tags=("roster",),
    ),
    AgentTool(
        name="capabilities_list",
        description=(
            "Everything the agents can actually use on this machine: connected "
            "plugins, coding CLIs, MCP servers, active skills and core tools. This "
            "is what makes an agent able to do a thing rather than only talk about "
            "it — check here before assigning work that needs a capability."
        ),
        input_schema=_obj(
            {
                "kind": {
                    "type": "string",
                    "enum": ["plugin", "cli", "mcp", "skill", "core"],
                    "description": "Only this kind. Omit for everything.",
                }
            }
        ),
        handler=_capabilities_list,
        tags=("discovery",),
    ),
    AgentTool(
        name="rooms_list",
        description="Every room: who is in it, the topic, whose turn it is, and its state.",
        input_schema=_obj({}),
        handler=_rooms_list,
        tags=("rooms",),
    ),
    AgentTool(
        name="room_open",
        description=(
            "Open a bounded discussion between 2-6 agents on one topic. Rooms are "
            "capped (≤3 rounds, ≤10 messages) so a debate cannot run away."
        ),
        input_schema=_obj(
            {
                "members": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-6 agent ids or names.",
                },
                "topic": {"type": "string"},
                "opened_by": {"type": "string", "description": "Default 'user'."},
            },
            ["members", "topic"],
        ),
        handler=_room_open,
        tags=("rooms",),
    ),
    AgentTool(
        name="room_say",
        description=(
            "Speak in a room as one of its members. Turn order is enforced — saying "
            "out of turn is refused with the reason."
        ),
        input_schema=_obj(
            {
                "room_id": {"type": "string"},
                "member": _AGENT_REF,
                "text": {"type": "string"},
            },
            ["room_id", "member", "text"],
        ),
        handler=_room_say,
        dangerous=True,
        tags=("rooms",),
    ),
    AgentTool(
        name="room_settle",
        description="Close a room and record its outcome. Nothing more is said in it after.",
        input_schema=_obj({"room_id": {"type": "string"}}, ["room_id"]),
        handler=_room_settle,
        dangerous=True,
        tags=("rooms",),
    ),
    AgentTool(
        name="approvals_list",
        description=(
            "What the person still has to decide: actions agents parked because they "
            "sit above their permission ceiling. Oldest first."
        ),
        input_schema=_obj({"agent": {"type": "string", "description": "Only this agent's."}}),
        handler=_approvals_list,
        tags=("governance",),
    ),
    AgentTool(
        name="approval_resolve",
        description=(
            "Approve or deny one parked action. Approving does NOT run it — whoever "
            "asked runs it once the answer is there."
        ),
        input_schema=_obj(
            {
                "approval_id": {"type": "string"},
                "approve": {"type": "boolean"},
                "note": {"type": "string", "description": "Why. Recorded with the decision."},
            },
            ["approval_id", "approve"],
        ),
        handler=_approval_resolve,
        dangerous=True,
        tags=("governance",),
    ),
    AgentTool(
        name="kill_switch",
        description=(
            "The ecosystem's emergency stop. Engaging halts every run, settles every "
            "room, cancels every mission and closes the agents' browser. Releasing it "
            "lets work resume — dangerous in both directions."
        ),
        input_schema=_obj(
            {"engage": {"type": "boolean", "description": "true stops everything, false resumes."}},
            ["engage"],
        ),
        handler=_kill_switch,
        dangerous=True,
        tags=("governance",),
    ),
)

_BY_NAME: Final[dict[str, AgentTool]] = {t.name: t for t in TOOLS}


def tool_specs() -> list[dict[str, Any]]:
    """The catalog as plain dicts — what the MCP list_tools handler serves."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
            "dangerous": t.dangerous,
            "tags": list(t.tags),
        }
        for t in TOOLS
    ]


def get(name: str) -> AgentTool | None:
    return _BY_NAME.get(name)


async def call(name: str, arguments: dict[str, Any] | None) -> str:
    """Run one tool and render its result as the text the model reads back.

    Every failure mode leaves here as readable text rather than an exception:
    an MCP tool error is a dead end for the model, while a sentence explaining
    what went wrong and what to try instead keeps the conversation going.
    """
    tool = _BY_NAME.get(name)
    if tool is None:
        known = ", ".join(sorted(_BY_NAME))
        return f"No such tool: {name}. This server offers: {known}"
    try:
        result = await tool.handler(dict(arguments or {}))
    except EcosystemUnavailable as exc:
        return f"Not possible right now — {exc.what} is not available: {exc.hint}"
    except (KeyError, ValueError, TypeError) as exc:
        return f"Bad request for {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 — a tool failure is data for the model, not a crash
        log.warning("agent MCP: %s raised", name, exc_info=True)
        return f"{name} failed: {type(exc).__name__}: {exc}"
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    except (TypeError, ValueError):
        return str(result)


__all__ = [
    "DEFAULT_WAIT_S",
    "MAX_WAIT_S",
    "MCP_ORIGIN",
    "TOOLS",
    "AgentTool",
    "call",
    "get",
    "tool_specs",
]
