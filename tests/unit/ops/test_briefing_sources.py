"""Every question gets an answer: a source that cannot be read is named, the
rest is answered anyway — and unreadable category rules fail closed."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.ops import briefing_service
from jarvis.ops.briefing import BriefingComposer, BriefingSection
from jarvis.ops.calendar_day import CalendarDay, classify_day
from jarvis.ops.categories import CategoryStore, DayProfile, make_profile, make_rules
from jarvis.ops.ledger import SourceReport, WorkItem, WorkSnapshot

MADRID = ZoneInfo("Europe/Madrid")
SAT, MON = date(2026, 10, 24), date(2026, 10, 26)

RULES = make_rules(keywords=[("acme", "work"), ("dinner", "private")])
WEEKEND = make_profile({5: ["private", "trading", "video"], 6: ["private", "trading", "video"]})


def _at(day: date, hour: int = 7) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=MADRID)


class FakeCalendar:
    async def read_day(self, day: date, now: datetime) -> CalendarDay:
        stamp = day.isoformat()
        events = [
            {"id": "a", "summary": "Acme planning", "start": f"{stamp}T10:00:00"},
            {"id": "d", "summary": "Dinner with Ana", "start": f"{stamp}T20:00:00"},
        ]
        upcoming, cancelled = classify_day(day, events, tz=MADRID, now=now)
        return CalendarDay("ok", upcoming, cancelled)


class FailingExtension:
    name = "trading"
    category = "trading"

    async def section(self, day: date, now: datetime, language: str) -> BriefingSection | None:
        raise ConnectionError("broker api down")


def _composer(
    *,
    snapshot: Any = None,
    categories: Any = None,
    extensions: tuple[Any, ...] = (),
) -> BriefingComposer:
    async def _ok_snapshot() -> WorkSnapshot:
        return WorkSnapshot((WorkItem("task", "t1", "Book dinner table", "running", "running"),))

    async def _marks() -> list[Any]:
        return []

    async def _rules() -> tuple[Any, DayProfile]:
        return RULES, WEEKEND

    return BriefingComposer(
        snapshot=snapshot or _ok_snapshot,
        marks=_marks,
        calendar=FakeCalendar(),
        categories=categories or _rules,
        extensions=extensions,
    )


async def _boom() -> Any:
    raise RuntimeError("source down")


async def test_a_dead_work_source_is_named_and_the_calendar_still_answers() -> None:
    briefing = await _composer(snapshot=_boom).compose(now=_at(MON), language="en")
    assert briefing.unavailable == ("work",)
    assert "Not reachable right now: work items." in briefing.text
    assert "Acme planning" in briefing.text  # the calendar was read

    spoken = await briefing_service.answer(
        composer=_composer(snapshot=_boom), now=_at(MON), focus="briefing", language="de"
    )
    assert "Gerade nicht erreichbar: Aufgaben." in spoken.spoken  # i18n-allow
    assert "Dinner with Ana" in spoken.spoken


async def test_a_source_reporting_an_error_counts_as_unreachable() -> None:
    async def _partial() -> WorkSnapshot:
        return WorkSnapshot((), (SourceReport("missions", "error", message="OperationalError"),))

    briefing = await _composer(snapshot=_partial).compose(now=_at(MON), language="en")
    assert briefing.unavailable == ("work",)


async def test_a_failing_module_is_named_instead_of_silently_missing() -> None:
    result = await briefing_service.answer(
        composer=_composer(extensions=(FailingExtension(),)),
        now=_at(SAT),
        focus="briefing",
        language="en",
    )
    assert "I could not reach Trading right now." in result.spoken
    assert "Not reachable right now: Trading." in result.text
    assert "Dinner with Ana" in result.spoken  # the rest of the weekend briefing stands


async def test_unreadable_rules_fail_closed_and_say_so() -> None:
    composer = _composer(categories=_boom)
    for day in (SAT, MON):
        briefing = await composer.compose(now=_at(day), language="en")
        assert "Acme" not in briefing.text  # never let work through on a guess
        assert briefing.section("hidden_unassigned").count == 3  # 2 appointments + 1 task
        assert "category rules (entries left out to be safe)" in briefing.text
    tomorrow = await briefing_service.answer(
        composer=composer,
        now=_at(date(2026, 10, 23), 18),
        day="tomorrow",
        focus="appointments",
        language="en",
    )
    assert "Acme" not in tomorrow.spoken
    assert "I could not reach category rules" in tomorrow.spoken


async def test_the_store_keeps_the_last_rules_when_the_database_breaks(tmp_path: Path) -> None:
    db = tmp_path / "ops.sqlite"
    store = CategoryStore(db)
    await store.save(RULES, WEEKEND)
    assert await store.load() == (RULES, WEEKEND)
    with sqlite3.connect(db) as conn:  # the stored rules become unreadable
        conn.execute("UPDATE ops_categories SET rules = 'not json'")
    assert await store.load() == (RULES, WEEKEND)  # last known rules, not "show everything"
    fresh = CategoryStore(db)
    try:
        await fresh.load()
    except ValueError:
        pass  # nothing known yet: the composer then fails closed
    else:
        raise AssertionError("a fresh store must not invent rules")
