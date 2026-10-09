"""Work, private, own projects, trading, video — weekdays show all, weekends
everything except work.

The employer here is a made-up "Acme Publishing"; the real names are the
person's local settings, never code. Dates around the 2026-10-25 DST change
in Europe/Madrid: Fri 23, Sat 24, Sun 25 (clocks go back), Mon 26.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from jarvis.ops import briefing_service
from jarvis.ops.briefing import BriefingComposer, BriefingSection
from jarvis.ops.calendar_day import CalendarDay, classify_day
from jarvis.ops.categories import (
    UNASSIGNED,
    CategoryError,
    CategoryStore,
    DayProfile,
    classify_event,
    classify_item,
    keep,
    make_profile,
    make_rules,
)
from jarvis.ops.ledger import WorkItem, WorkSnapshot
from jarvis.ops.morning import MorningBriefingTool
from jarvis.ops.notify import NotifyStore, SimulatedTelegramTransport, notifications_from_briefing

MADRID = ZoneInfo("Europe/Madrid")
FRI, SAT, SUN, MON = date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 26)

RULES = make_rules(
    calendars={"acme-work@group.calendar.google.com": "work", "Family": "private"},
    keywords=[("acme", "work"), ("channel", "business")],
    items={"task:t-follow": "work"},
)
WORK_FREE_WEEKEND = make_profile(
    {5: ["private", "business", "trading", "video"], 6: ["private", "business", "trading", "video"]}
)


def _events(day: date) -> list[dict[str, Any]]:
    stamp = day.isoformat()
    return [
        {
            "id": "standup",
            "summary": "Team standup",
            "start": f"{stamp}T09:30:00",
            "calendar_id": "acme-work@group.calendar.google.com",
            "calendar": "Acme",
        },
        {
            "id": "dinner",
            "summary": "Dinner with parents",
            "start": f"{stamp}T20:00:00",
            "calendar_id": "family@group",
            "calendar": "Family",
        },
        {
            "id": "edit",
            "summary": "Edit channel video",
            "start": f"{stamp}T11:00:00",
            "calendar_id": "primary",
            "calendar": "Me",
        },
        {
            "id": "mystery",
            "summary": "Call Paul",
            "start": f"{stamp}T12:00:00",
            "calendar_id": "primary",
            "calendar": "Me",
        },
        {
            "id": "review",
            "summary": "Acme cover review",
            "start": f"{stamp}T15:00:00",
            "calendar_id": "primary",
            "calendar": "Me",
            "status": "cancelled",
        },
    ]


# Running, so each one would appear in the briefing unless filtered out.
ITEMS = (
    WorkItem("task", "t-follow", "Follow up on proofs", "running", "running"),
    WorkItem("task", "t-acme", "Send Acme invoice", "running", "running"),
    WorkItem("task", "t-video", "Upload channel video", "running", "running"),
    WorkItem("task", "t-plain", "Water the plants", "running", "running"),
)


# --- Classification ------------------------------------------------------------------


def test_explicit_beats_calendar_beats_keyword_beats_unassigned() -> None:
    assert classify_event({"id": "x", "title": "Acme sync", "calendar": "Family"}, RULES) == (
        "private",
        "calendar",
    )
    assert classify_event({"id": "x", "title": "acme sync", "calendar": "Me"}, RULES) == (
        "work",
        "keyword",
    )
    assert classify_event({"id": "x", "title": "Lunch", "calendar": "Me"}, RULES) == (
        UNASSIGNED,
        "none",
    )
    assert classify_item("task", "t-follow", "Follow up on proofs", RULES) == ("work", "item")
    assert classify_item("task", "t-plain", "Water the plants", RULES) == (UNASSIGNED, "none")


def test_unassigned_is_never_private() -> None:
    assert keep(UNASSIGNED, MON, WORK_FREE_WEEKEND) is True  # weekday shows everything
    assert keep(UNASSIGNED, SAT, WORK_FREE_WEEKEND) is False  # might be work: left out
    assert keep("private", SAT, WORK_FREE_WEEKEND) is True
    assert keep("trading", SAT, WORK_FREE_WEEKEND) is True
    assert keep("work", SUN, WORK_FREE_WEEKEND) is False
    assert keep("business", SUN, WORK_FREE_WEEKEND) is True  # own projects also on weekends


def test_rules_are_validated() -> None:
    with pytest.raises(CategoryError):
        make_rules(keywords=[("acme", "job")])
    with pytest.raises(CategoryError):
        make_profile({7: ["private"]})
    with pytest.raises(CategoryError):
        make_rules(items={"no-colon": "work"})


# --- The briefing per day ------------------------------------------------------------


class FakeCalendar:
    async def read_day(self, day: date, now: datetime) -> CalendarDay:
        upcoming, cancelled = classify_day(day, _events(day), tz=MADRID, now=now)
        return CalendarDay("ok", upcoming, cancelled)


def _composer(rules: Any = RULES, profile: DayProfile = WORK_FREE_WEEKEND) -> BriefingComposer:
    async def _snapshot() -> WorkSnapshot:
        return WorkSnapshot(ITEMS)

    async def _marks() -> list[Any]:
        return []

    async def _categories() -> tuple[Any, DayProfile]:
        return rules, profile

    return BriefingComposer(
        snapshot=_snapshot, marks=_marks, calendar=FakeCalendar(), categories=_categories
    )


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MADRID)


def _titles(briefing: Any) -> set[str]:
    return {
        str(e.get("title"))
        for s in briefing.sections
        if s.key != "hidden_unassigned"
        for e in s.items
    }


async def test_a_weekday_shows_work_private_business_and_marks_unassigned() -> None:
    briefing = await _composer().compose(now=_at(MON, 8, 30), language="en")
    titles = _titles(briefing)
    assert {"Team standup", "Dinner with parents", "Edit channel video", "Call Paul"} <= titles
    assert {"Follow up on proofs", "Send Acme invoice", "Upload channel video"} <= titles
    assert "Acme cover review" in titles  # a work cancellation counts on weekdays
    assert briefing.section("hidden_unassigned").count == 0
    assert "- 12:00 Call Paul (unassigned)" in briefing.text
    assert "Water the plants (task, running; unassigned)" in briefing.text


@pytest.mark.parametrize("day", [SAT, SUN])
async def test_a_weekend_shows_everything_but_work(day: date) -> None:
    briefing = await _composer().compose(now=_at(day, 7), language="en")
    titles = _titles(briefing)
    assert titles == {"Dinner with parents", "Edit channel video", "Upload channel video"}
    assert briefing.section("hidden_unassigned").count == 2  # Call Paul, Water the plants
    assert "Acme" not in briefing.text and "Team standup" not in briefing.text
    assert "Follow up on proofs" not in briefing.text  # a work follow-up is left out
    assert "2 unassigned entries are left out today" in briefing.text


async def test_sunday_of_the_dst_change_and_the_monday_after() -> None:
    sunday = await _composer().compose(now=_at(SUN, 9), language="de")
    monday = await _composer().compose(now=_at(MON, 9), language="de")
    assert "Team standup" not in sunday.text and "Team standup" in monday.text
    assert _at(SUN, 9).utcoffset() != _at(SAT, 9).utcoffset()  # the clocks went back


async def test_without_rules_nothing_is_hidden_or_marked() -> None:
    briefing = await _composer(rules=make_rules(), profile=DayProfile()).compose(
        now=_at(SAT, 9), language="en"
    )
    assert "Team standup" in briefing.text and "unassigned" not in briefing.text


# --- The same filter for voice, any time -------------------------------------------------


async def test_a_spontaneous_weekend_voice_briefing_leaves_work_out() -> None:
    result = await briefing_service.answer(
        composer=_composer(), now=_at(SAT, 11, 15), focus="briefing", language="en"
    )
    assert "Standup" not in result.spoken and "Acme" not in result.spoken
    assert "Dinner with parents" in result.spoken
    assert "I left out 2 unassigned entries" in result.spoken


async def test_asking_on_friday_about_tomorrow_uses_saturdays_profile() -> None:
    friday = await briefing_service.answer(
        composer=_composer(), now=_at(FRI, 18), day="today", focus="appointments", language="en"
    )
    saturday = await briefing_service.answer(
        composer=_composer(), now=_at(FRI, 18), day="tomorrow", focus="appointments", language="en"
    )
    assert "Dinner with parents" in friday.spoken
    assert "Team standup" not in saturday.spoken and "Dinner with parents" in saturday.spoken
    assert "I left out 1 unassigned entries" in saturday.spoken  # Call Paul


async def test_weekend_changes_leave_work_cancellations_out() -> None:
    result = await briefing_service.answer(
        composer=_composer(), now=_at(SAT, 9), focus="changes", language="en"
    )
    assert "Acme" not in result.spoken


# --- Telegram at 07:00 follows the same profile -------------------------------------------


async def test_the_sunday_overview_carries_no_work_notifications(tmp_path: Path) -> None:
    store = NotifyStore(tmp_path / "ops.sqlite")
    await store.save_settings(
        enabled=True, kinds=["daily_briefing", "appointment_cancelled", "important_task"]
    )
    sent = SimulatedTelegramTransport()
    tool = MorningBriefingTool(
        composer=_composer,
        notify_store=lambda: store,
        transport=lambda _s: sent,
        clock=lambda: _at(SUN, 7).astimezone(ZoneInfo("UTC")),
    )
    result = await tool.execute(
        {"mode": "overview", "slot": "07:00", "timezone": "Europe/Madrid", "language": "en"}
    )
    assert result.success and result.output["day"] == "2026-10-25"
    assert all("Acme" not in m and "proofs" not in m for m in sent.sent)
    weekday = notifications_from_briefing(await _composer().compose(now=_at(MON, 7)))
    assert any("Acme cover review" in n.text for n in weekday)  # on Monday it is sent


# --- Future modules plug in by category -----------------------------------------------------


class FakeExtension:
    def __init__(self, name: str, category: str, *, fail: bool = False) -> None:
        self.name = name
        self.category = category
        self.fail = fail

    async def section(self, day: date, now: datetime, language: str) -> BriefingSection | None:
        if self.fail:
            raise RuntimeError("module down")
        return BriefingSection("x", 1, ({"text": f"{self.name} update"},), title=self.name.title())


async def test_extensions_follow_the_day_profile_and_never_break_the_briefing() -> None:
    async def _snapshot() -> WorkSnapshot:
        return WorkSnapshot(())

    async def _marks() -> list[Any]:
        return []

    async def _categories() -> tuple[Any, DayProfile]:
        return RULES, WORK_FREE_WEEKEND

    composer = BriefingComposer(
        snapshot=_snapshot,
        marks=_marks,
        categories=_categories,
        extensions=[
            FakeExtension("trading", "trading"),
            FakeExtension("youtube", "video"),
            FakeExtension("office", "work"),
            FakeExtension("projects", "business"),
            FakeExtension("broken", "trading", fail=True),
        ],
    )
    saturday = await composer.compose(now=_at(SAT, 9), language="en")
    monday = await composer.compose(now=_at(MON, 9), language="en")
    assert [s.key for s in saturday.sections if s.key.startswith("ext:")] == [
        "ext:trading",
        "ext:youtube",
        "ext:projects",
    ]
    assert [s.key for s in monday.sections if s.key.startswith("ext:")] == [
        "ext:trading",
        "ext:youtube",
        "ext:office",
        "ext:projects",
    ]
    assert "Trading (1):\n- trading update" in saturday.text


# --- Local settings ---------------------------------------------------------------------------


async def test_the_store_and_routes_keep_rules_locally(tmp_path: Path) -> None:
    from jarvis.ui.web import ops_routes

    store = CategoryStore(tmp_path / "ops.sqlite")
    assert await store.load() == (make_rules(), DayProfile())

    app = FastAPI()
    app.include_router(ops_routes.router)
    app.state.config = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    body = {
        "calendars": {"Acme": "work"},
        "keywords": [{"keyword": "Acme", "category": "work"}],
        "weekdays": {"5": ["private", "trading", "video"], "6": ["private", "trading", "video"]},
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        put = await client.put("/api/ops/categories", json=body)
        bad = await client.put(
            "/api/ops/categories", json={"keywords": [{"keyword": "x", "category": "job"}]}
        )
        got = await client.get("/api/ops/categories")
    assert put.status_code == 200 and bad.status_code == 400
    data = got.json()
    assert data["rules"]["calendars"] == {"acme": "work"}
    assert data["weekdays"]["5"] == ["private", "trading", "video"]
    assert data["weekdays"]["0"] == ["business", "private", "trading", "video", "work"]
    rules, profile = await store.load()
    assert classify_event({"id": "1", "title": "x", "calendar": "ACME"}, rules)[0] == "work"
    assert keep("work", SAT, profile) is False
    assert json.dumps(data).count("Acme") == 0  # stored casefolded, as typed is not needed
