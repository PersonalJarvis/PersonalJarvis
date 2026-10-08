"""Ops REST: one view over missions, tasks, quests and workflows.

``GET /api/ops/work`` folds every system's work into the shared WorkLedger
statuses (see ``jarvis/ops/ledger.py``). Reading only: it changes no store,
starts no run and builds no service — a source that is not up in this process
is listed as ``unavailable``.

``/api/ops/priorities`` holds the person's own priority / focus-today marks
(``jarvis/ops/priority.py``) — the only thing these routes write, and only on
an explicit request. ``GET /api/ops/agenda`` ranks the ledger with those marks
(``jarvis/ops/ranking.py``): deterministic, no model call.

``POST /api/ops/briefing/preview`` composes the daily briefing
(``jarvis/ops/briefing.py``) and returns it — it sends nothing and schedules
nothing. Phrasing by a model happens only when asked for, and only on a
subscription or a local model; the deterministic text is always returned.

``/api/ops/notify/*`` is the OwnerNotifier (``jarvis/ops/notify.py``): off
until the person opts in, and for now a SIMULATED Telegram transport only —
``POST /api/ops/notify/simulate`` records what would be sent and opens no
connection, reads no token and needs no chat id.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from jarvis.ops.briefing import (
    BriefingComposer,
    Phrasing,
    ToolCalendarReader,
    normalize_language,
    phrase_briefing,
)
from jarvis.ops.ledger import WORK_SOURCES, WorkLedger
from jarvis.ops.notify import (
    NOTIFICATION_KINDS,
    NotifyStore,
    OwnerNotifier,
    SimulatedTelegramTransport,
    notifications_from_briefing,
    outbox_dicts,
)
from jarvis.ops.priority import NOTE_MAX, OpsPriorityStore, PriorityError, PriorityMark
from jarvis.ops.ranking import build_agenda

router = APIRouter(prefix="/api/ops", tags=["ops"])

PRIORITY_DB_NAME = "ops.sqlite"


def _ledger(request: Request) -> WorkLedger:
    state = request.app.state
    return WorkLedger(
        missions=lambda: getattr(state, "mission_manager", None),
        tasks=lambda: getattr(state, "task_store", None),
        # Only a society runtime that already runs; reading never starts one.
        quests=lambda: getattr(state, "society", None),
        workflows=lambda: getattr(state, "workflow_store", None),
    )


def _ops_db_path(request: Request, what: str) -> Path:
    config = getattr(request.app.state, "config", None)
    data_dir = getattr(getattr(config, "memory", None), "data_dir", None)
    if not data_dir:
        raise HTTPException(status_code=503, detail=f"{what} not available")
    return Path(data_dir) / PRIORITY_DB_NAME


def _priority_store(request: Request) -> OpsPriorityStore:
    """The marks store, created on first use (nothing opens on boot)."""
    state = request.app.state
    store = getattr(state, "ops_priority_store", None)
    if store is None:
        store = OpsPriorityStore(_ops_db_path(request, "Priority store"))
        state.ops_priority_store = store
    return store


def _notify_store(request: Request) -> NotifyStore:
    """Opt-in settings and delivery log, created on first use."""
    state = request.app.state
    store = getattr(state, "ops_notify_store", None)
    if store is None:
        store = NotifyStore(_ops_db_path(request, "Notification store"))
        state.ops_notify_store = store
    return store


def _today() -> date:
    """The person's local calendar day (focus-today is per local day)."""
    return datetime.now().astimezone().date()


def _sources(source: str | None) -> list[str] | None:
    if not source:
        return None
    sources = [s.strip() for s in source.split(",") if s.strip()]
    unknown = [s for s in sources if s not in WORK_SOURCES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown work sources: {unknown}")
    return sources


@router.get("/work")
async def list_work(
    request: Request,
    source: str | None = Query(
        default=None, description="Comma-separated: mission,task,quest,workflow"
    ),
    include_finished: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Everything Jarvis is working on, in one status vocabulary (read-only)."""
    snapshot = await _ledger(request).snapshot(
        sources=_sources(source), include_finished=include_finished, limit=limit
    )
    return snapshot.to_dict()


@router.get("/agenda")
async def get_agenda(
    request: Request,
    source: str | None = Query(
        default=None, description="Comma-separated: mission,task,quest,workflow"
    ),
    include_finished: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """The work ranked by lane, focus and the person's own priorities."""
    snapshot = await _ledger(request).snapshot(
        sources=_sources(source), include_finished=include_finished, limit=limit
    )
    marks = await _priority_store(request).all()
    agenda = build_agenda(snapshot.items, marks, today=_today(), now_ms=int(time.time() * 1000))
    return {**agenda.to_dict(), "sources": [s.to_dict() for s in snapshot.sources]}


@router.get("/priorities")
async def list_priorities(request: Request) -> dict[str, Any]:
    """Every priority / focus mark the person has set."""
    today = _today()
    marks = await _priority_store(request).all()
    return {"today": today.isoformat(), "priorities": [m.to_dict(today) for m in marks]}


class PriorityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: str | None = Field(default=None, description="urgent, high, normal, low or null")
    focus_today: bool = False
    note: str = Field(default="", max_length=NOTE_MAX)


@router.put("/priorities/{source}/{item_id}")
async def set_priority(
    source: str, item_id: str, body: PriorityBody, request: Request
) -> dict[str, Any]:
    """Set the person's mark on one work item (replaces any earlier mark)."""
    today = _today()
    mark = PriorityMark(
        source=source,
        item_id=item_id,
        priority=body.priority,
        focus_date=today.isoformat() if body.focus_today else None,
        note=body.note.strip(),
    )
    try:
        stored = await _priority_store(request).put(mark)
    except PriorityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return stored.to_dict(today)


@router.delete("/priorities/{source}/{item_id}")
async def clear_priority(source: str, item_id: str, request: Request) -> dict[str, Any]:
    """Remove the person's mark from one work item. The item itself is untouched."""
    removed = await _priority_store(request).delete(source, item_id)
    return {"removed": removed, "source": source, "item_id": item_id}


class BriefingPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str | None = Field(default=None, description="en, de, es or zh; default: UI language")
    include_calendar: bool = True
    phrase: bool = Field(
        default=False,
        description="Also ask a subscription or local model for prose (never an API key)",
    )


#: Work items the briefing reads per source; enough for a day's overview.
BRIEFING_ITEM_LIMIT = 200


def _briefing_composer(request: Request) -> BriefingComposer:
    state = request.app.state
    ledger = _ledger(request)

    async def _snapshot() -> Any:
        return await ledger.snapshot(include_finished=True, limit=BRIEFING_ITEM_LIMIT)

    async def _marks() -> list[PriorityMark]:
        try:
            store = _priority_store(request)
        except HTTPException:
            return []  # no data dir: the briefing simply carries no marks
        return await store.all()

    def _brain() -> Any:
        return getattr(state, "brain", None)

    def _address() -> str | None:
        profile = getattr(_brain(), "_user_profile", None)
        value = getattr(profile, "preferred_address", None)
        return str(value) if value else None

    return BriefingComposer(
        snapshot=_snapshot,
        marks=_marks,
        calendar=ToolCalendarReader(
            tools=lambda: getattr(_brain(), "_tools", None),
            executor=lambda: getattr(_brain(), "_tool_executor_ref", None),
        ),
        address=_address,
    )


@router.post("/briefing/preview")
async def preview_briefing(request: Request, body: BriefingPreviewBody) -> dict[str, Any]:
    """Compose today's briefing and return it. Sends and schedules nothing."""
    config = getattr(request.app.state, "config", None)
    language = normalize_language(
        body.language or getattr(getattr(config, "ui", None), "language", None)
    )
    briefing = await _briefing_composer(request).compose(
        now=datetime.now().astimezone(),
        language=language,
        include_calendar=body.include_calendar,
    )
    phrasing = Phrasing("not_requested")
    if body.phrase:
        phrasing = await phrase_briefing(
            briefing, brain=getattr(request.app.state, "brain", None), config=config
        )
    return {**briefing.to_dict(), "phrasing": phrasing.to_dict()}


# ------------------------------------------------------------------ notify


@router.get("/notify/settings")
async def get_notify_settings(request: Request) -> dict[str, Any]:
    """Whether the person opted in to notifications (off by default)."""
    return (await _notify_store(request).settings()).to_dict()


class NotifySettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    kinds: list[str] | None = Field(
        default=None, description=f"Subset of {', '.join(NOTIFICATION_KINDS)}; default all"
    )


@router.put("/notify/settings")
async def set_notify_settings(request: Request, body: NotifySettingsBody) -> dict[str, Any]:
    """The person's explicit opt-in (or opt-out) for notifications."""
    kinds = body.kinds if body.kinds is not None else list(NOTIFICATION_KINDS)
    try:
        saved = await _notify_store(request).save_settings(enabled=body.enabled, kinds=kinds)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return saved.to_dict()


class NotifySimulateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str | None = None
    include_calendar: bool = True


@router.post("/notify/simulate")
async def simulate_notifications(request: Request, body: NotifySimulateBody) -> dict[str, Any]:
    """Build today's notifications and run them through the SIMULATED Telegram
    transport: what would be sent, deduplicated and prioritised. Nothing leaves
    the machine; no model is called."""
    config = getattr(request.app.state, "config", None)
    language = normalize_language(
        body.language or getattr(getattr(config, "ui", None), "language", None)
    )
    briefing = await _briefing_composer(request).compose(
        now=datetime.now().astimezone(),
        language=language,
        include_calendar=body.include_calendar,
    )
    transport = SimulatedTelegramTransport()
    report = await OwnerNotifier(_notify_store(request), transport).deliver(
        notifications_from_briefing(briefing)
    )
    return {**report.to_dict(), "simulated_messages": list(transport.sent)}


@router.get("/notify/outbox")
async def notify_outbox(
    request: Request, limit: int = Query(default=50, ge=1, le=500)
) -> dict[str, Any]:
    """What was (simulated as) sent, failed or given up — newest first."""
    rows = await _notify_store(request).outbox(limit)
    return {"items": outbox_dicts(rows)}
