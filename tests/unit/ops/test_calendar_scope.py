"""Strict calendar scope: a shared work account holds several people's
calendars; only the person's own one may reach their briefing.

Made-up names: the shared account has calendars for Alex (the person),
Robin and Sam; the private calendar lives on a separate address.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from jarvis.ops import briefing_service
from jarvis.ops.briefing import BriefingComposer
from jarvis.ops.calendar_day import CalendarDay, ToolCalendarReader, classify_day
from jarvis.ops.categories import (
    EXCLUDED,
    CategoryError,
    DayProfile,
    classify_event,
    keep,
    listed_calendar_ids,
    make_profile,
    make_rules,
)
from jarvis.ops.ledger import WorkSnapshot

MADRID = ZoneInfo("Europe/Madrid")
SAT, MON = date(2026, 10, 24), date(2026, 10, 26)

OWN = "alex@acme-publishing.example"
ROBIN = "robin@acme-publishing.example"
SAM = "sam@acme-publishing.example"
PRIVATE = "alex.private@mail.example"

STRICT = make_rules(calendars={OWN: "work", PRIVATE: "private"}, only_listed=True)
WEEKEND = make_profile({d: ["private", "business", "trading", "video"] for d in (5, 6)})


def _events(day: date) -> list[dict[str, Any]]:
    stamp = day.isoformat()

    def ev(i: str, title: str, hour: int, cal: str, name: str) -> dict[str, Any]:
        return {
            "id": i,
            "summary": title,
            "start": f"{stamp}T{hour:02d}:00:00",
            "calendar_id": cal,
            "calendar": name,
        }

    return [
        ev("own", "Print run sign-off", 9, OWN, "Alex"),
        ev("robin", "Robin: author call", 10, ROBIN, "Robin"),
        ev("sam", "Sam: cover shoot with Alex", 11, SAM, "Sam"),
        ev("private", "Dinner with Ana", 20, PRIVATE, "Alex private"),
    ]


class FakeCalendar:
    async def read_day(self, day: date, now: datetime) -> CalendarDay:
        upcoming, cancelled = classify_day(day, _events(day), tz=MADRID, now=now)
        return CalendarDay("ok", upcoming, cancelled)


def _composer(rules: Any = STRICT, profile: DayProfile = WEEKEND) -> BriefingComposer:
    async def _snapshot() -> WorkSnapshot:
        return WorkSnapshot(())

    async def _marks() -> list[Any]:
        return []

    async def _categories() -> tuple[Any, DayProfile]:
        return rules, profile

    return BriefingComposer(
        snapshot=_snapshot, marks=_marks, calendar=FakeCalendar(), categories=_categories
    )


def _at(day: date, hour: int = 7) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=MADRID)


# --- Classification -----------------------------------------------------------------


def test_colleagues_calendars_are_excluded_even_when_they_mention_the_person() -> None:
    assert classify_event({"id": "x", "title": "x", "calendar_id": OWN}, STRICT)[0] == "work"
    for cal in (ROBIN, SAM):
        event = {"id": "x", "title": "Meeting with Alex", "calendar_id": cal, "calendar": "Alex?"}
        assert classify_event(event, STRICT) == (EXCLUDED, "not_listed")
    # Not even an explicit per-item rule pulls a colleague's appointment in.
    rules = make_rules(calendars={OWN: "work"}, items={"calendar:robin": "work"}, only_listed=True)
    assert classify_event({"id": "robin", "title": "x", "calendar_id": ROBIN}, rules)[0] == (
        EXCLUDED
    )
    assert keep(EXCLUDED, MON, DayProfile()) is False


def test_only_listed_ids_are_read_and_strict_mode_needs_a_calendar() -> None:
    assert listed_calendar_ids(STRICT) == (PRIVATE, OWN)
    assert listed_calendar_ids(make_rules(calendars={OWN: "work"})) == ()  # not strict
    assert listed_calendar_ids(make_rules(calendars={"Alex": "work"}, only_listed=True)) == ()
    with pytest.raises(CategoryError):
        make_rules(only_listed=True)


# --- The briefing -----------------------------------------------------------------------


async def test_monday_shows_own_work_and_private_never_colleagues() -> None:
    briefing = await _composer().compose(now=_at(MON), language="en")
    assert "Print run sign-off" in briefing.text and "Dinner with Ana" in briefing.text
    assert "Robin" not in briefing.text and "Sam" not in briefing.text
    assert briefing.section("hidden_unassigned").count == 0  # excluded is not "hidden"


async def test_saturday_shows_private_only_no_work_at_all() -> None:
    briefing = await _composer().compose(now=_at(SAT), language="en")
    assert "Dinner with Ana" in briefing.text
    assert "Print run" not in briefing.text and "Robin" not in briefing.text


async def test_a_spontaneous_question_follows_the_same_scope() -> None:
    result = await briefing_service.answer(
        composer=_composer(), now=_at(MON, 7), day="today", focus="appointments", language="en"
    )
    assert "Print run sign-off" in result.spoken
    assert "author call" not in result.spoken and "cover shoot" not in result.spoken


# --- The reader asks the calendar tool for the listed calendars only ---------------------


class RecordingExecutor:
    def __init__(self) -> None:
        self.args: list[dict[str, Any]] = []

    async def execute(self, tool: Any, args: dict[str, Any], **_kw: Any) -> Any:
        self.args.append(dict(args))
        return SimpleNamespace(success=True, output={"events": []})


async def test_the_reader_requests_only_listed_calendars() -> None:
    executor = RecordingExecutor()

    async def _ids() -> tuple[str, ...]:
        return listed_calendar_ids(STRICT)

    reader = ToolCalendarReader(
        tools=lambda: {"google_calendar": object()},
        executor=lambda: executor,
        calendar_ids=_ids,
    )
    await reader.read_day(MON, _at(MON))
    assert executor.args[0]["calendar_ids"] == [PRIVATE, OWN]

    async def _broken() -> tuple[str, ...]:
        raise RuntimeError("store down")

    failing = ToolCalendarReader(
        tools=lambda: {"google_calendar": object()}, executor=lambda: executor, calendar_ids=_broken
    )
    assert (await failing.read_day(MON, _at(MON))).status == "unavailable"
    assert len(executor.args) == 1  # an unknown scope reads nothing, never everything


async def test_strict_mode_round_trips_through_the_route(tmp_path: Path) -> None:
    from jarvis.ui.web import ops_routes

    app = FastAPI()
    app.include_router(ops_routes.router)
    app.state.config = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        ok = await client.put(
            "/api/ops/categories", json={"calendars": {OWN: "work"}, "only_listed": True}
        )
        empty = await client.put("/api/ops/categories", json={"only_listed": True})
        got = await client.get("/api/ops/categories")
    assert ok.status_code == 200 and empty.status_code == 400
    assert got.json()["rules"]["only_listed"] is True
