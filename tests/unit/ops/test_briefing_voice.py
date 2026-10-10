"""Voice and Telegram share one briefing service; the morning briefing is never doubled.

Covers: new appointments (only with a stated creation time), the spoken and
written answers for today / tomorrow / changes, channel detection from the
calling turn's origin, the "already heard by voice" note that stops the
07:00 Telegram briefing, and Telegram on request (live switch only). Every
calendar, channel and Telegram call is a fake; nothing leaves the machine.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.http_pool import HttpClientPool
from jarvis.core.protocols import ToolResult
from jarvis.core.turn_origin import HEADER_DELIVERY, HEADER_TRACE, turn_origin_headers
from jarvis.ops import briefing_service
from jarvis.ops.briefing import BriefingComposer
from jarvis.ops.calendar_day import CalendarDay, classify_day
from jarvis.ops.ledger import WorkItem, WorkSnapshot
from jarvis.ops.morning import MorningBriefingTool
from jarvis.ops.notify import NotifyStore, SimulatedTelegramTransport
from jarvis.ops.telegram_transport import TelegramBotTransport

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 10, 7, 6, 30, tzinfo=MADRID)
TODAY = NOW.date()
TOMORROW = TODAY + timedelta(days=1)

EVENTS = {
    TODAY: [
        {
            "id": "standup",
            "summary": "Standup",
            "start": "2026-10-07T10:00:00+02:00",
            "end": "2026-10-07T10:15:00+02:00",
            "original_start": "2026-10-07T09:00:00+02:00",
            "updated": "2026-10-07T03:00:00Z",
        },
        {
            "id": "dentist",
            "summary": "Dentist",
            "start": "2026-10-07T11:00:00+02:00",
            "created": "2026-10-06T20:00:00Z",
        },
        {
            "id": "call",
            "summary": "Client call",
            "start": "2026-10-07T12:00:00+02:00",
            "status": "cancelled",
            "updated": "2026-10-07T04:00:00Z",
        },
        {
            "id": "lunch",
            "summary": "Lunch",
            "start": "2026-10-07T13:00:00+02:00",
            "created": "2026-09-01T10:00:00Z",
        },
    ],
    TOMORROW: [
        {"id": "review", "summary": "Review", "start": "2026-10-08T16:00:00+02:00"},
    ],
}


# --- New appointments --------------------------------------------------------------


def test_new_needs_a_stated_creation_time_within_a_day() -> None:
    upcoming, cancelled = classify_day(TODAY, EVENTS[TODAY], tz=MADRID, now=NOW)
    flags = {e["id"]: e["new"] for e in upcoming}
    assert flags == {"standup": False, "dentist": True, "lunch": False}
    assert [e["new"] for e in cancelled] == [False]  # a cancelled event is never "new"


# --- The shared service ------------------------------------------------------------


class FakeCalendar:
    def __init__(self, status: str = "ok") -> None:
        self.status = status
        self.days: list[Any] = []

    async def read_day(self, day: Any, now: datetime) -> CalendarDay:
        self.days.append(day)
        if self.status != "ok":
            return CalendarDay(self.status)
        upcoming, cancelled = classify_day(day, EVENTS.get(day, []), tz=MADRID, now=now)
        return CalendarDay("ok" if upcoming or cancelled else "empty", upcoming, cancelled)


def _composer(calendar: Any) -> BriefingComposer:
    async def _snapshot() -> WorkSnapshot:
        return WorkSnapshot(
            (
                WorkItem(
                    "mission",
                    "m1",
                    "Quarterly report",
                    "waiting_capacity",
                    "WAITING_CAPACITY",
                    needs_attention=True,
                    attention_reason="capacity_decision",
                ),
            )
        )

    async def _marks() -> list[Any]:
        return []

    return BriefingComposer(snapshot=_snapshot, marks=_marks, calendar=calendar)


async def _answer(focus: str, day: str = "today", language: str = "en", calendar: Any = None):
    cal = calendar or FakeCalendar()
    return await briefing_service.answer(
        composer=_composer(cal), now=NOW, day=day, focus=focus, language=language
    )


async def test_the_morning_briefing_spoken_and_written() -> None:
    result = await _answer("briefing")
    assert result.text.startswith("Briefing for 2026-10-07")
    spoken = result.spoken
    assert spoken.startswith("One thing needs you: Quarterly report.")
    assert "Today you have one appointment: at 13:00 Lunch." in spoken
    assert "New in your calendar: Dentist 11:00." in spoken  # its own group
    assert "Moved: Standup, now 10:00, before 09:00. That was a short-notice change." in spoken
    assert "Cancelled: Client call 12:00." in spoken
    assert "- " not in spoken and "(!)" not in spoken  # sentences, not bullet marks


async def test_tomorrows_appointments_read_tomorrow() -> None:
    calendar = FakeCalendar()
    result = await _answer("appointments", day="tomorrow", calendar=calendar)
    assert calendar.days == [TOMORROW]
    assert result.spoken == "Tomorrow you have one appointment: at 16:00 Review."
    assert result.day == TOMORROW and "Review" in result.text


async def test_changes_list_moved_new_and_cancelled_only() -> None:
    result = await _answer("changes", language="de")
    spoken = result.spoken
    assert "Verschoben: Standup, jetzt 10:00, vorher 09:00." in spoken  # i18n-allow
    assert "Neu im Kalender: Dentist 11:00." in spoken  # i18n-allow
    assert "Abgesagt: Client call 12:00." in spoken  # i18n-allow
    assert "Lunch" not in spoken and "Review" not in spoken  # unchanged ones stay out


async def test_no_changes_and_a_dead_calendar_are_said_plainly() -> None:
    quiet = await briefing_service.answer(
        composer=_composer(FakeCalendar("empty")),
        now=NOW,
        focus="changes",
    )
    assert quiet.spoken == "Nothing has changed in your calendar for today or tomorrow."
    dead = await _answer("appointments", calendar=FakeCalendar("unavailable"))
    assert dead.spoken == "I could not read your calendar right now."


def test_every_language_has_every_spoken_phrase() -> None:
    keys = set(briefing_service._SPOKEN["en"])
    for language in ("en", "de", "es", "zh"):
        assert set(briefing_service._SPOKEN[language]) == keys


# --- Channel detection ---------------------------------------------------------------


def test_turn_origin_headers() -> None:
    trace = uuid4()
    spoken = turn_origin_headers(SimpleNamespace(config={}, trace_id=trace))
    written = turn_origin_headers(SimpleNamespace(config={"delivery": "written"}, trace_id=trace))
    assert spoken == {HEADER_DELIVERY: "spoken", HEADER_TRACE: str(trace)}
    assert written[HEADER_DELIVERY] == "written"
    assert turn_origin_headers(None) == {}


class FakeTelegramChannel:
    def __init__(self, traces: set[Any]) -> None:
        self.traces = traces

    def owns_trace(self, trace_id: Any) -> bool:
        return trace_id in self.traces


class FakeChannelManager:
    def __init__(self, telegram: FakeTelegramChannel) -> None:
        self.telegram = telegram

    def started(self) -> list[str]:
        return ["telegram"]

    def get(self, name: str) -> Any:
        return self.telegram


class CalendarTool:
    name = "google_calendar"


class CalendarExecutor:
    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        day = datetime.fromisoformat(args["time_min"]).date()
        return ToolResult(True, {"events": EVENTS.get(day, [])})


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from jarvis.ui.web import ops_routes

    application = FastAPI()
    application.include_router(ops_routes.router)
    state = application.state
    state.config = SimpleNamespace(
        memory=SimpleNamespace(data_dir=str(tmp_path)),
        ui=SimpleNamespace(language="en"),
        integrations=SimpleNamespace(telegram=SimpleNamespace(allowed_user_ids=[4242], chat_id="")),
    )
    state.brain = SimpleNamespace(
        _tools={"google_calendar": CalendarTool()}, _tool_executor_ref=CalendarExecutor()
    )
    state.telegram_trace = uuid4()
    state.channel_manager = FakeChannelManager(FakeTelegramChannel({state.telegram_trace}))
    return application


async def _ask(app: FastAPI, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        res = await client.post("/api/ops/briefing/answer", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr("jarvis.ui.web.ops_routes.datetime", _Frozen)


async def test_the_channel_is_detected_from_the_calling_turn(app: FastAPI) -> None:
    trace = str(app.state.telegram_trace)
    voice = await _ask(
        app, {HEADER_DELIVERY: "spoken", HEADER_TRACE: str(uuid4())}, focus="appointments"
    )
    chat = await _ask(
        app, {HEADER_DELIVERY: "written", HEADER_TRACE: str(uuid4())}, focus="appointments"
    )
    telegram = await _ask(
        app, {HEADER_DELIVERY: "spoken", HEADER_TRACE: trace}, focus="appointments"
    )
    direct = await _ask(app, {}, focus="appointments")
    assert [r["channel"] for r in (voice, chat, telegram, direct)] == [
        "voice",
        "text",
        "telegram",
        "text",
    ]
    assert voice["say"] == voice["spoken"] and chat["say"] == chat["text"]
    assert telegram["say"] == telegram["text"]


async def test_a_voice_briefing_never_stops_the_0700_telegram_overview(
    app: FastAPI, tmp_path: Path
) -> None:
    store = NotifyStore(tmp_path / "ops.sqlite")
    await store.save_settings(enabled=True, kinds=["daily_briefing"])
    heard = await _ask(app, {HEADER_DELIVERY: "spoken", HEADER_TRACE: str(uuid4())})
    assert heard["noted_as_delivered"] is True and heard["channel"] == "voice"
    again = await _ask(app, {HEADER_DELIVERY: "spoken", HEADER_TRACE: str(uuid4())})
    assert again["noted_as_delivered"] is False  # noted once

    # 07:00 Europe/Madrid: the overview goes out regardless of the voice briefing.
    sent = SimulatedTelegramTransport()
    tool = MorningBriefingTool(
        composer=lambda: _composer(FakeCalendar()),
        notify_store=lambda: store,
        transport=lambda _settings: sent,
        clock=lambda: datetime(2026, 10, 7, 5, 0, tzinfo=ZoneInfo("UTC")),
    )
    args = {"mode": "overview", "slot": "07:00", "timezone": "Europe/Madrid", "language": "en"}
    result = await tool.execute(args)
    again_0700 = await tool.execute(args)
    assert result.success and result.output["delivered"] == 1
    assert again_0700.output["delivered"] == 0  # one Telegram message per scheduled send
    assert len(sent.sent) == 1 and sent.sent[0].startswith("Briefing for 2026-10-07")


async def test_other_questions_never_mark_the_briefing(app: FastAPI, tmp_path: Path) -> None:
    for focus in ("appointments", "changes"):
        body = await _ask(app, {HEADER_DELIVERY: "spoken"}, focus=focus)
        assert body["noted_as_delivered"] is False
    typed = await _ask(app, {HEADER_DELIVERY: "written"})
    assert typed["noted_as_delivered"] is False  # read in the app chat, not counted
    assert await NotifyStore(tmp_path / "ops.sqlite").get("briefing:2026-10-07") is None


async def test_a_briefing_asked_in_telegram_counts_too(app: FastAPI, tmp_path: Path) -> None:
    trace = str(app.state.telegram_trace)
    body = await _ask(app, {HEADER_DELIVERY: "spoken", HEADER_TRACE: trace})
    assert (body["channel"], body["noted_as_delivered"]) == ("telegram", True)
    row = await NotifyStore(tmp_path / "ops.sqlite").get("briefing:2026-10-07")
    assert row is not None and row.transport == "telegram-chat"


# --- Telegram on request (fallback) ----------------------------------------------------


class BotApi:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.texts.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {}})


@pytest.fixture
def live(app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BotApi:
    from jarvis.ops import delivery

    api = BotApi()
    monkeypatch.setattr(
        delivery,
        "TelegramBotTransport",
        lambda owner: TelegramBotTransport(
            owner=owner,
            token=lambda: "123456789:FAKE-token-for-tests-only-abcdefghij",
            pool=HttpClientPool(transport=httpx.MockTransport(api.handler)),
        ),
    )
    return api


async def _send(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/api/ops/briefing/telegram", json={})


async def test_telegram_on_request_needs_the_live_switch(app: FastAPI, live: BotApi) -> None:
    res = await _send(app)
    assert res.status_code == 409 and res.json()["detail"] == "live_disabled"
    assert live.texts == []


async def test_telegram_on_request_sends_once_even_after_voice(
    app: FastAPI, live: BotApi, tmp_path: Path
) -> None:
    store = NotifyStore(tmp_path / "ops.sqlite")
    await store.set_live(True)
    await _ask(app, {HEADER_DELIVERY: "spoken"})  # heard by voice first
    first = await _send(app)
    second = await _send(app)
    assert (first.json()["status"], second.json()["status"]) == ("delivered", "duplicate")
    assert len(live.texts) == 1 and live.texts[0].startswith("Briefing for 2026-10-07")


async def test_telegram_on_request_after_the_automatic_one_is_not_doubled(
    app: FastAPI, live: BotApi, tmp_path: Path
) -> None:
    store = NotifyStore(tmp_path / "ops.sqlite")
    await store.set_live(True)
    await store.record(  # the 07:00 Telegram briefing already went out
        __import__("jarvis.ops.notify", fromlist=["LogRow"]).LogRow(
            "briefing:2026-10-07",
            "daily_briefing",
            "normal",
            "delivered",
            "telegram",
            1,
            1,
            1,
            "",
            "Briefing",
            1,
            1,
        )
    )
    res = await _send(app)
    assert res.json()["status"] == "duplicate" and live.texts == []


# --- Reachable by voice, safely ---------------------------------------------------------


def test_voice_reaches_the_answer_and_must_confirm_a_telegram_send() -> None:
    from jarvis.app_actions.catalog import build_catalog, default_tier
    from jarvis.commands.registry import get_command
    from jarvis.ui.web import ops_routes

    application = FastAPI()
    application.include_router(ops_routes.router)
    by_route = {(e.method, e.path): e for e in build_catalog(application.openapi()).values()}
    assert default_tier(by_route[("POST", "/api/ops/briefing/answer")]) == "monitor"
    assert default_tier(by_route[("POST", "/api/ops/briefing/telegram")]) == "ask"
    command = get_command("ops-briefing")
    assert command is not None and command.path == "/api/ops/briefing/answer"
    assert not command.dangerous
    assert "was steht heute an" in command.voice_aliases["de"]  # i18n-allow: input vocab


# --- The voice tool really carries the turn's origin ------------------------------------


@pytest.mark.parametrize("delivery,expected", [({}, "voice"), ({"delivery": "written"}, "text")])
async def test_app_command_carries_the_turn_origin_to_the_briefing(
    app: FastAPI, delivery: dict[str, str], expected: str
) -> None:
    from jarvis.plugins.tool.app_command import AppCommandTool

    loader = AppCommandTool(
        transport=httpx.ASGITransport(app=app),
        control_key_resolver=lambda: None,
        config_resolver=lambda: SimpleNamespace(),
    )
    tool = {t.name: t for t in loader.expand()}["ops-briefing"]
    ctx = SimpleNamespace(config=dict(delivery), trace_id=uuid4())
    result = await tool.execute({"focus": "appointments", "day": "tomorrow"}, ctx)
    assert result.success, result.error
    response = result.output["response"]
    assert response["channel"] == expected
    assert "Review" in response["say"]


async def test_app_command_from_a_telegram_turn_is_telegram(app: FastAPI) -> None:
    from jarvis.plugins.tool.app_command import AppCommandTool

    loader = AppCommandTool(
        transport=httpx.ASGITransport(app=app),
        control_key_resolver=lambda: None,
        config_resolver=lambda: SimpleNamespace(),
    )
    tool = {t.name: t for t in loader.expand()}["ops-briefing"]
    ctx = SimpleNamespace(config={}, trace_id=app.state.telegram_trace)
    result = await tool.execute({"focus": "appointments"}, ctx)
    assert result.output["response"]["channel"] == "telegram"


# --- 06:30 prepared, on call ------------------------------------------------------------


class DeadCalendarExecutor:
    async def execute(self, *a: Any, **k: Any) -> ToolResult:
        return ToolResult(False, None, "Calendar API 503: backend error")


async def test_without_a_readable_calendar_voice_gets_the_0630_briefing(
    app: FastAPI, tmp_path: Path
) -> None:
    from jarvis.ops.morning import BriefingSnapshot, BriefingSnapshotStore

    await BriefingSnapshotStore(tmp_path / "ops.sqlite").save(
        BriefingSnapshot(
            "2026-10-07",
            "en",
            "Briefing for 2026-10-07\n",
            "Today you have one appointment: at 13:00 Lunch.",
            "2026-10-07T06:30+02:00",
        )
    )
    app.state.brain._tool_executor_ref = DeadCalendarExecutor()
    body = await _ask(app, {HEADER_DELIVERY: "spoken"})
    assert body["prepared_at"] == "2026-10-07T06:30+02:00"
    assert body["say"] == (
        "I cannot read your calendar right now; this is the briefing from 06:30. "
        "Today you have one appointment: at 13:00 Lunch."
    )
    assert "503" not in json.dumps(body)


async def test_a_live_calendar_always_wins_over_the_prepared_briefing(
    app: FastAPI, tmp_path: Path
) -> None:
    from jarvis.ops.morning import BriefingSnapshot, BriefingSnapshotStore

    await BriefingSnapshotStore(tmp_path / "ops.sqlite").save(
        BriefingSnapshot("2026-10-07", "en", "old", "old spoken", "2026-10-07T06:30+02:00")
    )
    body = await _ask(app, {HEADER_DELIVERY: "spoken"})
    assert body["prepared_at"] is None and "Lunch" in body["say"] and "old" not in body["say"]


def test_the_morning_module_cannot_speak() -> None:
    """06:30 never starts speech: the scheduled module has no path to the voice."""
    import ast

    from jarvis.ops import morning

    tree = ast.parse(Path(morning.__file__).read_text(encoding="utf-8"))
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not names & {"AnnouncementRequested", "EventBus", "TTSRequested"}
    assert "publish(" not in Path(morning.__file__).read_text(encoding="utf-8")


# --- Four separate groups -----------------------------------------------------------------


async def test_new_moved_and_cancelled_are_separate_groups_in_order() -> None:
    result = await _answer("briefing")
    keys = [s.key for s in result.sections]
    assert keys[-5:-1] == ["calendar", "calendar_new", "calendar_moved", "calendar_cancelled"]
    by = {s.key: [e["id"] for e in s.items] for s in result.sections}
    assert (by["calendar"], by["calendar_new"], by["calendar_moved"], by["calendar_cancelled"]) == (
        ["lunch"],
        ["dentist"],
        ["standup"],
        ["call"],
    )
    text = result.text
    order = [
        text.index(h)
        for h in (
            "Upcoming appointments:",
            "New appointments (1):",
            "Moved appointments (1):",
            "Cancelled appointments (1):",
        )
    ]
    assert order == sorted(order)
