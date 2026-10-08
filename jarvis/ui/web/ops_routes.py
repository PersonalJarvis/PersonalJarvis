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

``/api/ops/morning/settings`` switches the automatic morning briefing
(``jarvis/ops/morning.py``): off by default; on keeps ONE recurring task in
the existing task scheduler, off pauses it. Delivery stays simulated.
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
from jarvis.ops.delivery import (
    LiveDisabled,
    LiveNotReady,
    disable_live,
    enable_live,
    select_transport,
    send_connection_test,
)
from jarvis.ops.ledger import WORK_SOURCES, WorkLedger
from jarvis.ops.morning import (
    DEFAULT_OVERVIEW_TIME,
    DEFAULT_PREPARE_TIME,
    BriefingSnapshotStore,
    MorningSettings,
    MorningStore,
    apply_schedule,
    validate_settings,
)
from jarvis.ops.notify import (
    NOTIFICATION_KINDS,
    Notification,
    NotificationTransport,
    NotifySettings,
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


def ledger_for_state(state: Any) -> WorkLedger:
    """The read-only ledger over whatever stores *state* (``app.state``) holds."""
    return WorkLedger(
        missions=lambda: getattr(state, "mission_manager", None),
        tasks=lambda: getattr(state, "task_store", None),
        # Only a society runtime that already runs; reading never starts one.
        quests=lambda: getattr(state, "society", None),
        workflows=lambda: getattr(state, "workflow_store", None),
    )


def _ledger(request: Request) -> WorkLedger:
    return ledger_for_state(request.app.state)


def ops_db_path_for_state(state: Any) -> Path | None:
    """The Ops core's own SQLite file under the data dir, or None without one."""
    config = getattr(state, "config", None)
    data_dir = getattr(getattr(config, "memory", None), "data_dir", None)
    return Path(data_dir) / PRIORITY_DB_NAME if data_dir else None


def _cached_store(state: Any, attribute: str, factory: Any) -> Any:
    """A store on *state*, created on first use (nothing opens on boot)."""
    store = getattr(state, attribute, None)
    if store is None:
        path = ops_db_path_for_state(state)
        if path is None:
            return None
        store = factory(path)
        setattr(state, attribute, store)
    return store


def priority_store_for_state(state: Any) -> OpsPriorityStore | None:
    return _cached_store(state, "ops_priority_store", OpsPriorityStore)


def notify_store_for_state(state: Any) -> NotifyStore | None:
    return _cached_store(state, "ops_notify_store", NotifyStore)


def morning_store_for_state(state: Any) -> MorningStore | None:
    return _cached_store(state, "ops_morning_store", MorningStore)


def snapshot_store_for_state(state: Any) -> BriefingSnapshotStore | None:
    return _cached_store(state, "ops_snapshot_store", BriefingSnapshotStore)


def _telegram_config(state: Any) -> Any:
    config = getattr(state, "config", None)
    return getattr(getattr(config, "integrations", None), "telegram", None)


def transport_for_state(state: Any, settings: NotifySettings) -> NotificationTransport:
    """Simulated unless the owner's live switch is on (``jarvis/ops/delivery.py``)."""
    return select_transport(settings, _telegram_config(state))


def _required(store: Any, what: str) -> Any:
    if store is None:
        raise HTTPException(status_code=503, detail=f"{what} not available")
    return store


def _priority_store(request: Request) -> OpsPriorityStore:
    return _required(priority_store_for_state(request.app.state), "Priority store")  # type: ignore[no-any-return]


def _notify_store(request: Request) -> NotifyStore:
    return _required(notify_store_for_state(request.app.state), "Notification store")  # type: ignore[no-any-return]


def _morning_store(request: Request) -> MorningStore:
    return _required(morning_store_for_state(request.app.state), "Morning briefing store")  # type: ignore[no-any-return]


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


def briefing_composer_for_state(state: Any) -> BriefingComposer:
    """The briefing over *state*'s stores, marks, calendar tool and profile."""
    ledger = ledger_for_state(state)

    async def _snapshot() -> Any:
        return await ledger.snapshot(include_finished=True, limit=BRIEFING_ITEM_LIMIT)

    async def _marks() -> list[PriorityMark]:
        store = priority_store_for_state(state)
        if store is None:
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
        calendar=calendar_reader_for_state(state),
        address=_address,
    )


def calendar_reader_for_state(state: Any) -> ToolCalendarReader:
    """Any day's calendar through the brain's calendar tool (AP-3)."""

    def _brain() -> Any:
        return getattr(state, "brain", None)

    return ToolCalendarReader(
        tools=lambda: getattr(_brain(), "_tools", None),
        executor=lambda: getattr(_brain(), "_tool_executor_ref", None),
    )


def _briefing_composer(request: Request) -> BriefingComposer:
    return briefing_composer_for_state(request.app.state)


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


# ------------------------------------------------------------------ morning


@router.get("/morning/settings")
async def get_morning_settings(request: Request) -> dict[str, Any]:
    """The automatic morning briefing (off by default)."""
    settings = await _morning_store(request).settings()
    return {**settings.to_dict(), "notifications": await _notify_summary(request)}


async def _notify_summary(request: Request) -> dict[str, Any]:
    settings = await _notify_store(request).settings()
    return {"enabled": settings.enabled, "daily_briefing": "daily_briefing" in settings.kinds}


class MorningSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    prepare_time: str = Field(
        default=DEFAULT_PREPARE_TIME,
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description="When the briefing is prepared (never spoken or sent)",
    )
    overview_time: str = Field(
        default=DEFAULT_OVERVIEW_TIME,
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description="When the day's overview goes to Telegram (calendar read again)",
    )
    timezone: str | None = Field(
        default=None, description="IANA timezone, e.g. Europe/Madrid; required to switch on"
    )
    language: str | None = Field(default=None, description="en, de, es or zh; default UI language")
    include_calendar: bool = True


@router.put("/morning/settings")
async def set_morning_settings(request: Request, body: MorningSettingsBody) -> dict[str, Any]:
    """Switch the daily morning schedule on or off: prepare (no output) and
    the Telegram overview (simulated unless the live switch is on)."""
    state = request.app.state
    store = _morning_store(request)
    current = await store.settings()
    timezone = body.timezone or current.timezone
    if body.enabled and not timezone:
        raise HTTPException(status_code=400, detail="timezone is required to switch on")
    scheduler = getattr(state, "task_scheduler", None)
    task_store = getattr(state, "task_store", None)
    if scheduler is None or task_store is None:
        raise HTTPException(status_code=503, detail="The task scheduler is unavailable")
    config = getattr(state, "config", None)
    language = normalize_language(
        body.language or current.language or getattr(getattr(config, "ui", None), "language", None)
    )
    settings = MorningSettings(
        enabled=body.enabled,
        prepare_time=body.prepare_time,
        overview_time=body.overview_time,
        timezone=timezone,
        language=language,
        include_calendar=body.include_calendar,
    )
    try:
        validate_settings(settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The scheduler first: settings never say "on" without the task behind them.
    task_ids = await apply_schedule(settings, scheduler=scheduler, store=task_store)
    saved = await store.save(settings)
    return {
        **saved.to_dict(),
        "task_ids": task_ids,
        "notifications": await _notify_summary(request),
    }


# ------------------------------------------------------------------ telegram


@router.get("/notify/telegram")
async def telegram_readiness(request: Request) -> dict[str, Any]:
    """Whether live Telegram delivery COULD be switched on — read-only.

    Reports only yes/no facts and reason codes: never the token, never the
    chat id. Live delivery itself stays off (``live_enabled`` is always false
    until the owner approves the activation step).
    """
    import asyncio

    from jarvis.ops.telegram_transport import default_token, owner_chat

    config = getattr(request.app.state, "config", None)
    telegram = getattr(getattr(config, "integrations", None), "telegram", None)
    token_stored = bool(await asyncio.to_thread(default_token))
    owner = owner_chat(telegram)
    steps: list[str] = []
    if not token_stored:
        steps.append("store_bot_token")
    if owner.chat_id is None:
        steps.append("pair_owner_chat")
    return {
        "live_enabled": False,
        "token_stored": token_stored,
        "channel_enabled": bool(getattr(telegram, "enabled", False)),
        "owner_paired": owner.chat_id is not None,
        "owner_reason": owner.reason,
        "pairing_open": bool(getattr(telegram, "pair_on_first_private_message", False))
        and owner.chat_id is None,
        "ready_for_activation": token_stored and owner.chat_id is not None,
        "next_steps": steps,
    }



class LiveSwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


@router.put("/notify/live")
async def set_live_delivery(request: Request, body: LiveSwitchBody) -> dict[str, Any]:
    """The owner's live switch for real Telegram delivery (off by default).

    Switching on needs the stored bot token and exactly one paired private
    owner chat; otherwise 409 with a reason code. Switching off always works.
    """
    store = _notify_store(request)
    if not body.enabled:
        return (await disable_live(store)).to_dict()
    try:
        settings = await enable_live(store, _telegram_config(request.app.state))
    except LiveNotReady as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from None
    return settings.to_dict()


class TelegramTestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=300)


@router.post("/notify/telegram/test", openapi_extra={"x-jarvis-dangerous": True})
async def send_telegram_test(request: Request, body: TelegramTestBody) -> dict[str, Any]:
    """Send ONE real test message to the paired owner chat — only while the
    live switch is on. Repeating the same text on the same day sends nothing."""
    from datetime import datetime

    store = _notify_store(request)
    settings = await store.settings()
    transport = transport_for_state(request.app.state, settings)
    try:
        outcome = await send_connection_test(
            store, transport, text=body.text, day=datetime.now().astimezone().date()
        )
    except LiveDisabled:
        raise HTTPException(status_code=409, detail="live_disabled") from None
    return outcome.to_dict()


# ------------------------------------------------------------------ briefing on request


def resolve_channel(request: Request) -> str:
    """Where a briefing question came from: ``voice``, ``telegram`` or ``text``.

    The app tools send the calling turn's origin (``jarvis/core/turn_origin.py``):
    a trace id the Telegram channel is waiting to answer means Telegram; a
    written turn means a typed chat; any other turn is spoken. A direct UI or
    CLI call carries no origin and is ``text``.
    """
    from uuid import UUID

    from jarvis.core.turn_origin import HEADER_DELIVERY, HEADER_TRACE

    delivery = request.headers.get(HEADER_DELIVERY, "")
    trace = request.headers.get(HEADER_TRACE, "")
    manager = getattr(request.app.state, "channel_manager", None)
    if trace and manager is not None:
        try:
            channel = manager.get("telegram") if "telegram" in manager.started() else None
            owns = getattr(channel, "owns_trace", None)
            if callable(owns) and owns(UUID(trace)):
                return "telegram"
        except (ValueError, KeyError, AttributeError):  # not a Telegram turn: fall through
            pass
    if delivery == "spoken":
        return "voice"
    return "text"


class BriefingQuestionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: str = Field(default="today", pattern="^(today|tomorrow)$")
    focus: str = Field(default="briefing", pattern="^(briefing|appointments|changes)$")
    language: str | None = Field(default=None, description="en, de, es or zh; default UI language")


@router.post("/briefing/answer")
async def answer_briefing_question(
    request: Request, body: BriefingQuestionBody
) -> dict[str, Any]:
    """Answer "what is on today / tomorrow", "my morning briefing" or "what
    changed in my calendar" — spoken for voice, written for chat and Telegram.

    A full morning briefing given here (by voice or in the Telegram chat) is
    noted as delivered for the day, so the automatic one is not sent again.
    Sends nothing itself; no model call.
    """
    from jarvis.ops.briefing_service import answer

    state = request.app.state
    config = getattr(state, "config", None)
    language = normalize_language(
        body.language or getattr(getattr(config, "ui", None), "language", None)
    )
    channel = resolve_channel(request)
    result = await answer(
        composer=briefing_composer_for_state(state),
        calendar=calendar_reader_for_state(state),
        now=datetime.now().astimezone(),
        day=body.day,
        focus=body.focus,
        language=language,
    )
    prepared_at = None
    if body.focus == "briefing" and body.day == "today":
        calendar = next((s for s in result.sections if s.key == "calendar"), None)
        if calendar is not None and calendar.status in ("unavailable", "not_connected"):
            result, prepared_at = await _prepared_instead(state, result)
    noted = False
    if body.focus == "briefing" and body.day == "today" and channel in ("voice", "telegram"):
        store = notify_store_for_state(state)
        if store is not None:
            noted = await store.note_delivered(
                dedup_key=f"briefing:{result.day.isoformat()}",
                kind="daily_briefing",
                transport="voice" if channel == "voice" else "telegram-chat",
                text=result.text,
            )
    return {
        **result.to_dict(),
        "channel": channel,
        "say": result.spoken if channel == "voice" else result.text,
        "noted_as_delivered": noted,
        "prepared_at": prepared_at,
    }


async def _prepared_instead(state: Any, result: Any) -> tuple[Any, str | None]:
    """The calendar cannot be read now: answer with the briefing prepared this
    morning, if there is one, and say from when it is."""
    from dataclasses import replace

    from jarvis.ops.briefing_service import spoken_table

    store = snapshot_store_for_state(state)
    snapshot = await store.get(result.day) if store is not None else None
    if snapshot is None or snapshot.language != result.language:
        return result, None
    clock = snapshot.composed_at[11:16]
    note = spoken_table(result.language)["as_of"].format(time=clock)
    return (
        replace(result, spoken=f"{note} {snapshot.spoken}", text=snapshot.text),
        snapshot.composed_at,
    )


class BriefingTelegramBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str | None = None


@router.post("/briefing/telegram", openapi_extra={"x-jarvis-dangerous": True})
async def send_briefing_to_telegram(
    request: Request, body: BriefingTelegramBody
) -> dict[str, Any]:
    """On request ("send it to Telegram"): today's briefing to the paired owner
    chat — only while the live switch is on, at most once per day (an automatic
    Telegram briefing already sent counts)."""
    state = request.app.state
    store = _notify_store(request)
    settings = await store.settings()
    if not settings.live:
        raise HTTPException(status_code=409, detail="live_disabled")
    config = getattr(state, "config", None)
    language = normalize_language(
        body.language or getattr(getattr(config, "ui", None), "language", None)
    )
    briefing = await briefing_composer_for_state(state).compose(
        now=datetime.now().astimezone(), language=language
    )
    key = f"briefing:{briefing.day.isoformat()}"
    previous = await store.get(key)
    if previous is not None and previous.transport in ("voice", "telegram-chat"):
        # Heard by voice or read in a chat: an explicit Telegram copy is still wanted.
        key = f"{key}:telegram-request"
    note = Notification("daily_briefing", key, briefing.text, "normal")
    report = await OwnerNotifier(store, transport_for_state(state, settings)).deliver(
        [note], explicit=True
    )
    return report.outcomes[0].to_dict()
