"""REST surface of the agent society (``/api/society``).

The runtime is built on first use from ``app.state.society_factory`` (set in
``server.py``), so nothing opens on the boot path (AP-26). Every mutating
route that starts spend or stops work carries ``x-jarvis-dangerous`` so the
dynamic ``jarvis api society …`` CLI layer demands confirmation.

The roster is user content: creating an agent executes nothing and is not
dangerous; messaging one can trigger a turn (spend) and is; the kill switch
is dangerous in both directions because releasing it resumes work.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.society.events import MsgType
from jarvis.society.failure_reasons import FailureReason, retry_action
from jarvis.society.memory import MEMORY_SHARE_CAPABILITY, MemoryRefused
from jarvis.society.rooms import RoomError
from jarvis.society.roster import RosterError
from jarvis.society.runtime import SocietyRuntime

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/society", tags=["society"])


# ------------------------------------------------------------------ runtime


async def _runtime(request: Request) -> SocietyRuntime:
    state = request.app.state
    runtime = getattr(state, "society", None)
    if runtime is None:
        factory = getattr(state, "society_factory", None)
        if factory is None:
            raise HTTPException(503, "society runtime not configured")
        try:
            runtime = factory()
        except Exception as exc:  # noqa: BLE001 — surfaces as 503 with the reason in the log
            log.warning("society: runtime could not be built: %s", exc)
            raise HTTPException(503, "society runtime unavailable") from exc
        state.society = runtime
    await runtime.ensure_started()
    return runtime


def _typed_error(exc: RosterError | RoomError) -> HTTPException:
    reason = exc.reason
    status = 404 if reason is FailureReason.TARGET_UNKNOWN else 409
    return HTTPException(
        status,
        {"reason": str(reason), "retry": str(retry_action(reason)), "detail": str(exc)},
    )


# ------------------------------------------------------------------- models


class CreateAgentBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    title: str = ""
    description: str = ""
    tier: str = "specialist"
    parent_agent_id: str | None = None
    provider: str = ""
    model: str = ""
    effort: str = ""
    account_id: str = ""
    grant_mode: str | None = None
    grants: list[str] | None = None
    focus: list[str] | None = None
    denies: list[str] | None = None
    skills: list[str] | None = None
    permission_ceiling: str | None = None
    approval_rules: dict[str, list[str]] | None = None
    daily_budget_usd: float | None = None
    max_concurrent_runs: int | None = None
    avatar: dict[str, Any] | None = None
    browser_mode: str | None = None
    browser_allowed_domains: list[str] | None = None


class PatchAgentBody(BaseModel):
    title: str | None = None
    description: str | None = None
    tier: str | None = None
    parent_agent_id: str | None = None
    state: str | None = None
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    account_id: str | None = None
    grant_mode: str | None = None
    grants: list[str] | None = None
    focus: list[str] | None = None
    denies: list[str] | None = None
    skills: list[str] | None = None
    knowledge_scope: str | None = None
    permission_ceiling: str | None = None
    approval_rules: dict[str, list[str]] | None = None
    daily_budget_usd: float | None = None
    max_concurrent_runs: int | None = None
    avatar: dict[str, Any] | None = None
    checkpoint: str | None = None
    browser_mode: str | None = None
    browser_allowed_domains: list[str] | None = None


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    from_agent: str = "user"
    msg_type: str = "SAY"
    trace_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AssignBody(BaseModel):
    task: str = Field(min_length=1, max_length=20_000)
    from_agent: str = "user"
    trace_id: str | None = None
    lang: str | None = None


class CreateQuestBody(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    title: str = Field(default="", max_length=120)
    lang: str | None = None


class OpenRoomBody(BaseModel):
    members: list[str] = Field(min_length=2, max_length=6)
    topic: str = ""
    opened_by: str = "user"


class RoomSayBody(BaseModel):
    member: str
    text: str = ""


# ------------------------------------------------------------------- agents


@router.get("/agents")
async def list_agents(request: Request, include_archived: bool = False) -> dict[str, Any]:
    rt = await _runtime(request)
    agents = await rt.roster.list(include_archived=include_archived)
    rows = []
    for agent in agents:
        row = agent.to_dict()
        if agent.state == "paused":
            row["run_state"] = "paused"
        elif rt.checkpoints.is_busy(agent.agent_id):
            # A scheduler run OR a turn typed into the card: both are work.
            row["run_state"] = "working"
        else:
            row["run_state"] = "idle"
        rows.append(row)
    return {"agents": rows, "total": len(rows)}


@router.post("/agents")
async def create_agent(body: CreateAgentBody, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    fields = body.model_dump(exclude_none=True, exclude={"name", "title", "description", "tier"})
    derived_focus, derived_rules = rt.derive(body.title, body.description)
    if body.focus is None and derived_focus:
        fields["focus"] = derived_focus
    if body.approval_rules is None and derived_rules["require_approval"]:
        fields["approval_rules"] = derived_rules
    try:
        agent, created = await rt.roster.create(
            name=body.name,
            title=body.title,
            description=body.description,
            tier=body.tier,
            **fields,
        )
    except RosterError as exc:
        raise _typed_error(exc) from exc
    return {"agent": agent.to_dict(), "created": created}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    events = await rt.store.events_for_agent(agent.agent_id, limit=50)
    return {
        "agent": agent.to_dict(),
        "recent_events": [e.model_dump() for e in events],
        "active_runs": rt.scheduler.active_runs(agent.agent_id),
    }


@router.patch("/agents/{agent_id}")
async def patch_agent(agent_id: str, body: PatchAgentBody, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    fields = body.model_dump(exclude_none=True)
    if ("title" in fields or "description" in fields) and "focus" not in fields:
        # A prose edit must not wipe what the agent earned in its chat: the
        # derived focus is APPENDED to the existing order (existing first,
        # deduped) and derived approval rules are written only when the agent
        # has none — a person editing the description never silently loses the
        # rules they confirmed on a card.
        title = fields.get("title", agent.title)
        description = fields.get("description", agent.description)
        derived_focus, derived_rules = rt.derive(title, description)
        merged_focus = list(agent.focus)
        for cap_id in derived_focus:
            if cap_id not in merged_focus:
                merged_focus.append(cap_id)
        fields["focus"] = merged_focus
        has_rules = any(agent.approval_rules.get(k) for k in ("require_approval", "always_allow"))
        if "approval_rules" not in fields and not has_rules and derived_rules["require_approval"]:
            fields["approval_rules"] = derived_rules
    try:
        updated = await rt.roster.update(agent.agent_id, fields)
    except RosterError as exc:
        raise _typed_error(exc) from exc
    if "state" in fields:
        await rt.checkpoints.refresh(agent.agent_id)
        updated = await rt.roster.get(agent.agent_id) or updated
    return {"agent": updated.to_dict()}


@router.post("/agents/{agent_id}/chat")
async def bind_agent_chat(agent_id: str, request: Request) -> dict[str, Any]:
    """The agent's canonical chat (``society:<agent_id>``), created or re-seated
    to the roster row. Idempotent and free of spend: nothing runs until a
    message is sent. The model card calls it before it opens the chat column."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    svc = rt.chat_service()
    if svc is None:
        raise HTTPException(503, "agent chat service unavailable")
    from jarvis.society.chat_binding import ensure_session

    session = ensure_session(svc, rt.config(), agent)
    return {"session": session.to_dict(), "agent_id": agent.agent_id}


@router.delete("/agents/{agent_id}")
async def archive_agent(agent_id: str, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    try:
        archived = await rt.roster.archive(agent.agent_id)
    except RosterError as exc:
        raise _typed_error(exc) from exc
    return {"agent": archived.to_dict()}


@router.post("/agents/{agent_id}/message", openapi_extra={"x-jarvis-dangerous": True})
async def message_agent(agent_id: str, body: MessageBody, request: Request) -> dict[str, Any]:
    """Append a message to the agent (user → agent by default). May start a turn."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    try:
        msg_type = MsgType(body.msg_type)
    except ValueError as exc:
        raise HTTPException(422, f"msg_type must be one of {[str(m) for m in MsgType]}") from exc
    if msg_type in (MsgType.ASSIGN, MsgType.ROOM_OPEN, MsgType.ROOM_SETTLE, MsgType.VETO):
        raise HTTPException(422, "use /assign or /rooms for that message type")
    env = await rt.say(
        from_agent=body.from_agent,
        to_agent=agent.agent_id,
        text=body.text,
        trace_id=body.trace_id,
        msg_type=msg_type,
        payload=body.payload,
    )
    return {"event": env.model_dump()}


@router.post("/agents/{agent_id}/assign", openapi_extra={"x-jarvis-dangerous": True})
async def assign_agent(agent_id: str, body: AssignBody, request: Request) -> dict[str, Any]:
    """Give the agent a task: an ASSIGN the scheduler turns into real work."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    payload: dict[str, Any] = {"text": body.task}
    if body.lang:
        payload["lang"] = body.lang
    env = await rt.say(
        from_agent=body.from_agent,
        to_agent=agent.agent_id,
        text=body.task,
        trace_id=body.trace_id or f"task:{env_trace_seed()}",
        msg_type=MsgType.ASSIGN,
        payload=payload,
    )
    outcome = await rt.store.events_for_trace(env.trace_id)
    verdict = next((e for e in outcome if e.seq and env.seq and e.seq > env.seq), None)
    return {
        "event": env.model_dump(),
        "outcome": verdict.model_dump() if verdict else None,
    }


def env_trace_seed() -> str:
    from jarvis.missions.ids import uuid7_str

    return uuid7_str()


@router.post("/agents/{agent_id}/kill", openapi_extra={"x-jarvis-dangerous": True})
async def kill_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Pause the agent and drop its run slots (its missions are cancelled when possible)."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    dropped = 0
    manager = rt._get_manager()  # noqa: SLF001 — the route is the runtime's operator
    for run_id, owner in list(rt.scheduler.running.items()):
        if owner != agent.agent_id:
            continue
        rt.scheduler.note_run_ended(run_id)
        dropped += 1
        if manager is not None and hasattr(manager, "cancel"):
            try:
                await manager.cancel(run_id)
            except Exception:  # noqa: BLE001 — already gone is fine
                log.debug("society kill: mission %s not cancellable", run_id)
    paused = await rt.roster.update(agent.agent_id, {"state": "paused"})
    return {"agent": paused.to_dict(), "runs_dropped": dropped}


# ------------------------------------------------------------------ quests


@router.get("/quests")
async def list_quests(
    request: Request, state: str | None = None, limit: int = 200
) -> dict[str, Any]:
    """The Quest Board: newest first, optionally one state only."""
    rt = await _runtime(request)
    quests = await rt.quests.list(state=state or None, limit=max(1, min(int(limit), 1000)))
    return {"quests": [q.to_dict() for q in quests]}


@router.post("/quests", openapi_extra={"x-jarvis-dangerous": True})
async def create_quest(body: CreateQuestBody, request: Request) -> dict[str, Any]:
    """Post a quest: trusted Python routes it to one agent (forging one when
    nobody fits) and the scheduler starts the work — this spends."""
    rt = await _runtime(request)
    try:
        quest = await rt.quests.create(body.text, title=body.title, lang=body.lang)
    except RosterError as exc:
        raise _typed_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"quest": quest.to_dict()}


@router.get("/quests/{quest_id}")
async def get_quest(quest_id: str, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    quest = await rt.quests.get(quest_id)
    if quest is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    events = await rt.store.events_for_trace(quest.trace_id)
    return {"quest": quest.to_dict(), "events": [e.model_dump() for e in events]}


@router.post("/quests/{quest_id}/cancel", openapi_extra={"x-jarvis-dangerous": True})
async def cancel_quest(quest_id: str, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    try:
        quest = await rt.quests.cancel(quest_id)
    except KeyError as exc:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)}) from exc
    return {"quest": quest.to_dict()}


@router.post("/quests/{quest_id}/retry", openapi_extra={"x-jarvis-dangerous": True})
async def retry_quest(quest_id: str, request: Request) -> dict[str, Any]:
    """A failed or open quest is routed again (the taker may differ)."""
    rt = await _runtime(request)
    try:
        quest = await rt.quests.retry(quest_id)
    except KeyError as exc:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)}) from exc
    except RosterError as exc:
        raise _typed_error(exc) from exc
    return {"quest": quest.to_dict()}


# ------------------------------------------------------------------- board


@router.get("/events")
async def list_events(
    request: Request,
    after_seq: int = 0,
    trace_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    rt = await _runtime(request)
    limit = max(1, min(int(limit), 1000))
    if trace_id:
        events = await rt.store.events_for_trace(trace_id)
    elif agent_id:
        events = await rt.store.events_for_agent(agent_id, after_seq=after_seq, limit=limit)
    else:
        events = await rt.store.events_since(after_seq, limit=limit)
    return {"events": [e.model_dump() for e in events], "last_seq": await rt.store.last_seq()}


@router.get("/agents/{agent_id}/inbox")
async def agent_inbox(agent_id: str, request: Request, after_seq: int = 0) -> dict[str, Any]:
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    events = await rt.store.inbox_for(agent.agent_id, after_seq=after_seq)
    return {"events": [e.model_dump() for e in events]}


# ------------------------------------------------------------------- seeds


@router.get("/seeds")
async def list_seed_proposals(request: Request) -> dict[str, Any]:
    """Teammates worth creating on this box — one per connected capability."""
    from jarvis.society.seeds import propose_seeds

    rt = await _runtime(request)
    taken = {a.name for a in await rt.roster.list(include_archived=True)}
    proposals = propose_seeds(rt.catalog(), taken)
    return {"proposals": proposals, "total": len(proposals)}


class ApplySeedsBody(BaseModel):
    names: list[str] = Field(default_factory=list)


@router.post("/seeds/apply")
async def apply_seed_proposals(body: ApplySeedsBody, request: Request) -> dict[str, Any]:
    """Create the picked proposals (all of them when ``names`` is empty)."""
    from jarvis.society.seeds import create_from_proposals

    rt = await _runtime(request)
    ids = await create_from_proposals(rt.roster, rt.catalog(), body.names)
    created = []
    for agent_id in ids:
        agent = await rt.roster.get(agent_id)
        if agent is not None:
            created.append(agent.to_dict())
    return {"agents": created, "total": len(created)}


# --------------------------------------------------------------- providers


class ModelBody(BaseModel):
    provider: str = Field(min_length=1)
    model: str = ""
    effort: str = ""
    account_id: str = ""


@router.get("/providers")
async def list_society_providers(request: Request) -> dict[str, Any]:
    """Every provider an agent may run on, with the runner that answers on
    this box (a vendor CLI = a subscription seat; brain = an API key) and the
    subscription accounts stored for that CLI. Models, efforts and ladders
    come from GET /api/agent-chat/catalog?surface=society."""
    from jarvis.agent_chat.catalog import rows_for
    from jarvis.agent_chat.service import resolve_runner

    await _runtime(request)
    rows = []
    for row in rows_for("society"):
        runner = resolve_runner(row.id, surface="society")
        accounts: list[dict[str, Any]] = []
        if row.agent:
            accounts = _account_snapshots(row.agent)
        rows.append(
            {
                "id": row.id,
                "label": row.label,
                "family": row.family,
                "runner": runner,
                "subscription": runner not in ("brain", "api", "unknown"),
                "keyless": bool(getattr(row, "keyless", False)),
                "platform": row.agent or None,
                "accounts": accounts,
            }
        )
    return {"providers": rows, "catalog": "/api/agent-chat/catalog?surface=society"}


def _account_snapshots(platform: str) -> list[dict[str, Any]]:
    try:
        from jarvis import agent_accounts

        if platform not in agent_accounts.platforms():
            return []
        return [s.to_dict() for s in agent_accounts.snapshots(platform)]  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 — no account layer: the picker shows none
        log.debug("society: account snapshots unavailable for %s", platform, exc_info=True)
        return []


@router.post("/agents/{agent_id}/model")
async def switch_agent_model(agent_id: str, body: ModelBody, request: Request) -> dict[str, Any]:
    """Move the agent onto another provider / model / effort / subscription seat.
    Its canonical chat is re-seated at once (transcript kept)."""
    from jarvis.agent_chat.catalog import offers
    from jarvis.agent_chat.service import resolve_runner

    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    if not offers("society", body.provider):
        raise HTTPException(
            422,
            {"reason": str(FailureReason.BLOCKED_BY_POLICY), "detail": "provider not offered"},
        )
    fields = {
        "provider": body.provider.strip().lower(),
        "model": body.model.strip(),
        "effort": body.effort.strip(),
        "account_id": body.account_id.strip(),
    }
    try:
        updated = await rt.roster.update(agent.agent_id, fields)
    except RosterError as exc:
        raise _typed_error(exc) from exc
    reseated = None
    svc = rt._get_chat()  # noqa: SLF001 — the route is the runtime's operator
    if svc is not None:
        from jarvis.society.chat_binding import ensure_session

        try:
            session = ensure_session(svc, rt._get_cfg(), updated)  # noqa: SLF001
            reseated = session.session_id
        except PermissionError as exc:
            log.info("society: %s re-seat deferred: %s", agent.agent_id, exc)
    return {
        "agent": updated.to_dict(),
        "runner": resolve_runner(updated.provider, surface="society"),
        "reseated": reseated,
    }


# ------------------------------------------------------------------ skills


@router.get("/agents/{agent_id}/skills")
async def list_agent_skills(agent_id: str, request: Request) -> dict[str, Any]:
    """The agent's own learned skills (active for the agent, drafts for Jarvis)."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    skills = rt.skills_for(agent.agent_id)
    return {"skills": skills.summaries(), "root": str(skills.root)}


@router.post("/agents/{agent_id}/skills/{slug}/promote")
async def promote_agent_skill(agent_id: str, slug: str, request: Request) -> dict[str, Any]:
    """Copy a learned skill into the user's global skills as a DRAFT (AP-15)."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    try:
        target = rt.skills_for(agent.agent_id).promote_to_global(slug)
    except KeyError as exc:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)}) from exc
    except FileExistsError as exc:
        raise HTTPException(409, {"reason": str(FailureReason.BLOCKED_BY_POLICY)}) from exc
    return {"promoted": str(target), "state": "draft"}


# ----------------------------------------------------------------- browser


class LoginBody(BaseModel):
    start_url: str = ""


@router.get("/browser/status")
async def browser_status(request: Request) -> dict[str, Any]:
    """Is the managed browser environment installed, is an install running."""
    from jarvis.society.browser import install as install_mod

    rt = await _runtime(request)
    return install_mod.snapshot(rt.data_dir)


@router.post("/browser/install")
async def browser_install(request: Request) -> dict[str, Any]:
    """One-click install of browser-use into its own environment (background)."""
    from jarvis.society.browser import install as install_mod

    rt = await _runtime(request)
    ok, message = install_mod.start_install(rt.data_dir)
    return {"started": ok, "message": message, **install_mod.snapshot(rt.data_dir)}


@router.get("/agents/{agent_id}/browser")
async def agent_browser_status(agent_id: str, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    return rt.browser.status_for(agent)


@router.post("/agents/{agent_id}/browser/login", openapi_extra={"x-jarvis-dangerous": True})
async def agent_browser_login(agent_id: str, body: LoginBody, request: Request) -> dict[str, Any]:
    """Open the agent's browser profile headed so the person can sign in once.
    Returns when the window is closed, /login/done is called, or 15 minutes pass."""
    from jarvis.society.browser.session import BrowserUnavailable

    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    try:
        return await rt.browser.login(agent, start_url=body.start_url)
    except BrowserUnavailable as exc:
        raise HTTPException(409, {"reason": str(exc.reason), "detail": str(exc)}) from exc


@router.post("/agents/{agent_id}/browser/login/done")
async def agent_browser_login_done(agent_id: str, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    return {"closed": await rt.browser.end_login(agent.agent_id)}


# ----------------------------------------------------------------- catalog


@router.get("/capabilities")
async def list_capabilities(request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    rows = rt.catalog()
    return {"capabilities": [r.to_dict() for r in rows], "total": len(rows)}


# ------------------------------------------------------------------- rooms


@router.get("/rooms")
async def list_rooms(request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    return {"rooms": [r.to_dict() for r in await rt.rooms.list()]}


@router.post("/rooms")
async def open_room(body: OpenRoomBody, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    for member in body.members:
        if await rt.roster.resolve(member) is None:
            raise HTTPException(
                404, {"reason": str(FailureReason.TARGET_UNKNOWN), "member": member}
            )
    try:
        room = await rt.rooms.open(opened_by=body.opened_by, members=body.members, topic=body.topic)
    except RoomError as exc:
        raise _typed_error(exc) from exc
    return {"room": room.to_dict()}


@router.post("/rooms/{room_id}/say", openapi_extra={"x-jarvis-dangerous": True})
async def room_say(room_id: str, body: RoomSayBody, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    try:
        room = await rt.rooms.say(room_id, body.member, body.text)
    except RoomError as exc:
        raise _typed_error(exc) from exc
    return {"room": room.to_dict()}


@router.post("/rooms/{room_id}/settle", openapi_extra={"x-jarvis-dangerous": True})
async def room_settle(room_id: str, request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    try:
        room = await rt.rooms.settle(room_id, reason="user", by="user")
    except RoomError as exc:
        raise _typed_error(exc) from exc
    return {"room": room.to_dict()}


# ----------------------------------------------------------------- routines


class RoutineBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=16_000)
    schedule: dict[str, Any] = Field(default_factory=lambda: {"kind": "every"})
    plugin_grants: list[dict[str, str]] = Field(default_factory=list)
    announce_on_success: str | None = None


def _task_store(request: Request) -> Any:
    store = getattr(request.app.state, "task_store", None)
    if store is None:
        raise HTTPException(503, "task store not available")
    return store


@router.get("/agents/{agent_id}/routines")
async def list_agent_routines(agent_id: str, request: Request) -> dict[str, Any]:
    from jarvis.society.routines import list_routines

    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    rows = await list_routines(_task_store(request), agent.agent_id)
    return {"routines": rows, "total": len(rows)}


@router.post("/agents/{agent_id}/routines", openapi_extra={"x-jarvis-dangerous": True})
async def create_agent_routine(
    agent_id: str, body: RoutineBody, request: Request
) -> dict[str, Any]:
    """A routine is a task in the Automations scheduler tagged with the agent."""
    from jarvis.society.routines import (
        MAX_ROUTINES_PER_AGENT,
        build_task_spec,
        count_routines,
        create_routine,
    )

    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    store = _task_store(request)
    if await count_routines(store, agent.agent_id) >= MAX_ROUTINES_PER_AGENT:
        raise HTTPException(409, {"reason": str(FailureReason.BLOCKED_BY_POLICY)})
    try:
        spec = build_task_spec(
            agent,
            title=body.title,
            prompt=body.prompt,
            schedule=body.schedule,
            plugin_grants=body.plugin_grants,
            announce_on_success=body.announce_on_success,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, f"invalid routine: {exc}") from exc
    task_id = await create_routine(store, getattr(request.app.state, "task_scheduler", None), spec)
    return {"id": task_id, "title": spec.title, "tags": list(spec.tags)}


# ---------------------------------------------------------------- approvals


class ResolveApprovalBody(BaseModel):
    approve: bool
    note: str = ""


class EnqueueApprovalBody(BaseModel):
    agent_id: str
    capability: str
    trace_id: str = ""
    action: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


@router.post("/approvals")
async def enqueue_approval(body: EnqueueApprovalBody, request: Request) -> dict[str, Any]:
    """Park an action for the person to decide. Executes nothing by itself —
    the approved action is run by whoever asked (executor, routine, CLI)."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(body.agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    item = await rt.approvals.enqueue(
        agent_id=agent.agent_id,
        trace_id=body.trace_id or f"approval:{agent.agent_id}",
        capability=body.capability,
        action=body.action,
        summary=body.summary or body.capability,
    )
    return {"approval": item.to_dict()}


@router.get("/approvals")
async def list_approvals(request: Request, agent_id: str | None = None) -> dict[str, Any]:
    """Everything a person still has to decide (pending and parked), oldest first."""
    rt = await _runtime(request)
    await rt.approvals.expire_due()
    items = await rt.approvals.pending()
    if agent_id:
        items = [a for a in items if a.agent_id == agent_id]
    return {"approvals": [a.to_dict() for a in items], "total": len(items)}


@router.post("/approvals/{approval_id}/resolve", openapi_extra={"x-jarvis-dangerous": True})
async def resolve_approval(
    approval_id: str, body: ResolveApprovalBody, request: Request
) -> dict[str, Any]:
    from jarvis.society.proposals import kind_of

    rt = await _runtime(request)
    current = await rt.approvals.get(approval_id)
    if current is not None and kind_of(current.capability) is not None:
        # A configuration proposal is applied by ITS route, never by this one:
        # confirming here would flip the row without changing the agent.
        raise HTTPException(
            409,
            {
                "reason": str(FailureReason.BLOCKED_BY_POLICY),
                "detail": f"use POST /api/society/proposals/{approval_id}/resolve",
            },
        )
    try:
        item = await rt.approvals.resolve(approval_id, approve=body.approve, note=body.note)
    except KeyError as exc:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)}) from exc
    promoted: str | None = None
    if body.approve and item.capability == MEMORY_SHARE_CAPABILITY:
        knowledge_id = int(item.action.get("knowledge_id") or 0)
        try:
            promoted = await rt.memory.promote(knowledge_id)
        except MemoryRefused as exc:
            raise HTTPException(409, {"reason": "blocked_by_policy", "detail": str(exc)}) from exc
    return {"approval": item.to_dict(), "promoted": promoted}


@router.post("/approvals/resurface")
async def resurface_approvals(request: Request) -> dict[str, Any]:
    """App focus / voice turn: parked items are asked again."""
    rt = await _runtime(request)
    revived = await rt.approvals.resurface()
    return {"approvals": [a.to_dict() for a in revived], "total": len(revived)}


# ---------------------------------------------------------------- proposals


class ResolveProposalBody(BaseModel):
    approve: bool
    note: str = ""


@router.get("/proposals")
async def list_proposals(request: Request, agent_id: str | None = None) -> dict[str, Any]:
    """Configuration proposals still waiting for the person (agent-definition §3.5)."""
    from jarvis.society.proposals import kind_of

    rt = await _runtime(request)
    await rt.approvals.expire_due()
    items = [a for a in await rt.approvals.pending() if kind_of(a.capability) is not None]
    if agent_id:
        items = [a for a in items if a.agent_id == agent_id]
    return {"proposals": [a.to_dict() for a in items], "total": len(items)}


@router.post("/proposals/{proposal_id}/resolve", openapi_extra={"x-jarvis-dangerous": True})
async def resolve_proposal(
    proposal_id: str, body: ResolveProposalBody, request: Request
) -> dict[str, Any]:
    """The person decided on the card: a yes APPLIES the change (rule, focus,
    approval rules, routine, skill, team), a no changes nothing."""
    from jarvis.society import proposals

    rt = await _runtime(request)
    try:
        return await proposals.resolve(
            rt,
            proposal_id,
            approve=body.approve,
            note=body.note,
            task_store=getattr(request.app.state, "task_store", None),
            scheduler=getattr(request.app.state, "task_scheduler", None),
        )
    except KeyError as exc:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)}) from exc
    except ValueError as exc:
        raise HTTPException(
            409, {"reason": str(FailureReason.BLOCKED_BY_POLICY), "detail": str(exc)}
        ) from exc


# ------------------------------------------------------------------- memory


class RecallBody(BaseModel):
    query: str = Field(min_length=1, max_length=400)
    agent_id: str = "jarvis"
    k: int = Field(default=8, ge=1, le=20)


@router.get("/memory")
async def memory_overview(request: Request) -> dict[str, Any]:
    """What the Memory House shows: shared topics, every agent's memory head, the review queue."""
    rt = await _runtime(request)
    return await rt.memory.overview()


@router.post("/memory/recall")
async def memory_recall(body: RecallBody, request: Request) -> dict[str, Any]:
    """A lookup as the given agent would see it (scope labels included). Read-only."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(body.agent_id)
    if agent is None:
        raise HTTPException(404, {"reason": str(FailureReason.TARGET_UNKNOWN)})
    hits = await rt.memory.recall(agent, body.query, k=body.k)
    return {"hits": [h.to_dict() for h in hits], "total": len(hits)}


@router.post("/memory/{knowledge_id}/promote", openapi_extra={"x-jarvis-dangerous": True})
async def memory_promote(knowledge_id: int, request: Request) -> dict[str, Any]:
    """Copy a staged agent page into society/shared/ as reviewed team knowledge."""
    rt = await _runtime(request)
    try:
        path = await rt.memory.promote(knowledge_id)
    except MemoryRefused as exc:
        raise HTTPException(
            404, {"reason": str(FailureReason.TARGET_UNKNOWN), "detail": str(exc)}
        ) from exc
    return {"path": path, "reviewed": True}


@router.post("/memory/{knowledge_id}/dismiss")
async def memory_dismiss(knowledge_id: int, request: Request) -> dict[str, Any]:
    """Mark a staged page reviewed without promotion; it stays in the agent's own folder."""
    rt = await _runtime(request)
    try:
        await rt.memory.dismiss(knowledge_id)
    except MemoryRefused as exc:
        raise HTTPException(
            404, {"reason": str(FailureReason.TARGET_UNKNOWN), "detail": str(exc)}
        ) from exc
    return {"id": knowledge_id, "reviewed": True}


# ----------------------------------------------------------------- controls


@router.get("/status")
async def society_status(request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    return await rt.status()


@router.post("/kill-switch", openapi_extra={"x-jarvis-dangerous": True})
async def engage_kill_switch(request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    return await rt.engage_kill_switch()


@router.post("/kill-switch/release", openapi_extra={"x-jarvis-dangerous": True})
async def release_kill_switch(request: Request) -> dict[str, Any]:
    rt = await _runtime(request)
    return await rt.release_kill_switch()
