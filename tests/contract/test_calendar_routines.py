"""Calendar routine contract: UTC storage, user-local time, portable zone data."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime

import pytest
from pydantic import ValidationError

from jarvis.core.bus import EventBus
from jarvis.core.events import MessageSent
from jarvis.tasks.calendar import next_calendar_due_ns
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.schema import AgentAction, TaskSpec, TriggerCalendar, TriggerOnEvent
from jarvis.tasks.store import SCHEMA_FILE, TaskStore


def ns(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp()) * 1_000_000_000


@pytest.mark.parametrize(
    "zone,now,expected",
    [
        ("Europe/Berlin", "2026-03-28T07:00:00+00:00", "2026-03-29T06:00:00+00:00"),
        ("America/Los_Angeles", "2026-03-07T16:00:00+00:00", "2026-03-08T15:00:00+00:00"),
        ("America/Los_Angeles", "2026-10-31T15:00:00+00:00", "2026-11-01T16:00:00+00:00"),
        ("Asia/Kathmandu", "2026-09-08T02:15:00+00:00", "2026-09-09T02:15:00+00:00"),
    ],
)
def test_local_eight_survives_dst_and_fractional_offsets(zone, now, expected):
    trigger = TriggerCalendar(timezone=zone, local_time="08:00")
    assert next_calendar_due_ns(trigger, ns(now)) == ns(expected)


def test_gap_skips_and_fold_never_runs_twice():
    gap = TriggerCalendar(timezone="Europe/Berlin", local_time="02:30")
    assert next_calendar_due_ns(gap, ns("2026-03-28T01:30:00+00:00")) == ns(
        "2026-03-30T00:30:00+00:00"
    )
    assert next_calendar_due_ns(gap, ns("2026-10-25T00:30:00+00:00")) == ns(
        "2026-10-26T01:30:00+00:00"
    )


def test_weekdays_month_end_and_leap_year():
    weekdays = TriggerCalendar(timezone="UTC", local_time="08:00", weekdays=(0, 1, 2, 3, 4))
    assert next_calendar_due_ns(weekdays, ns("2026-09-11T08:00:00+00:00")) == ns(
        "2026-09-14T08:00:00+00:00"
    )
    monthly = TriggerCalendar(timezone="UTC", local_time="08:00", month_days=(31,))
    assert next_calendar_due_ns(monthly, ns("2026-04-01T00:00:00+00:00")) == ns(
        "2026-05-31T08:00:00+00:00"
    )
    leap = TriggerCalendar(timezone="UTC", local_time="08:00", months=(2,), month_days=(29,))
    assert next_calendar_due_ns(leap, ns("2026-09-08T00:00:00+00:00")) == ns(
        "2028-02-29T08:00:00+00:00"
    )


@pytest.mark.parametrize(
    "override",
    [
        {"timezone": "Mars/Olympus"},
        {"local_time": "24:00"},
        {"weekdays": [7]},
        {"months": [2], "month_days": [30]},
        {"start_date": "tomorrow"},
    ],
)
def test_invalid_calendar_is_refused(override):
    with pytest.raises(ValidationError):
        TriggerCalendar.model_validate({"timezone": "UTC", "local_time": "08:00", **override})


class Brain:
    def __init__(self):
        self.calls = []

    async def run_task(self, *, prompt, **kwargs):
        self.calls.append(prompt)
        return "Routine result"


async def test_persist_pause_resume_hydrate_and_execute(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    brain = Brain()
    bus = EventBus()
    runner = TaskRunner(store=store, bus=bus, agent_brain=brain)
    scheduler = TaskScheduler(store, bus, runner)
    spec = TaskSpec(
        title="Morning",
        action=AgentAction(prompt="Check inbox"),
        trigger=TriggerCalendar(timezone="America/Los_Angeles", local_time="08:00"),
    )
    try:
        tid = await scheduler.schedule(spec)
        row = await store.get(tid)
        assert row["trigger_type"] == "calendar"
        assert json.loads(row["spec_json"])["trigger"]["timezone"] == "America/Los_Angeles"
        await scheduler.pause(tid)
        due = await scheduler.resume(tid, now_ns=ns("2026-03-07T16:00:00+00:00"))
        assert due == ns("2026-03-08T15:00:00+00:00")
        await runner.run(tid)
        assert brain.calls == ["Check inbox"]
        assert (await store.get(tid))["state"] == "scheduled"
        fresh = TaskScheduler(store, bus, runner)
        await fresh.hydrate()
        assert tid in fresh._known
    finally:
        await scheduler.shutdown()
        await store.close()


async def test_migrate_old_database_accepts_calendar(tmp_path):
    path = tmp_path / "old.db"
    legacy = SCHEMA_FILE.read_text(encoding="utf-8").replace(",'calendar'", "")
    with sqlite3.connect(path) as db:
        db.executescript(legacy)
    store = TaskStore(path)
    await store.init()
    try:
        spec = TaskSpec(
            title="Calendar",
            trigger=TriggerCalendar(timezone="UTC", local_time="08:00"),
            action=AgentAction(prompt="Read"),
        )
        tid = await store.insert(spec)
        assert (await store.get(tid))["trigger_type"] == "calendar"
    finally:
        await store.close()


async def test_event_repeats_and_finite_limit_survives_hydration(tmp_path):
    store = TaskStore(tmp_path / "events.db")
    await store.init()
    brain, bus = Brain(), EventBus()
    runner = TaskRunner(store=store, bus=bus, agent_brain=brain)
    scheduler = TaskScheduler(store, bus, runner)
    spec = TaskSpec(
        title="Event",
        action=AgentAction(prompt="Read"),
        trigger=TriggerOnEvent(event_name="MessageSent", max_firings=2),
    )
    try:
        tid = await scheduler.schedule(spec)
        await scheduler._on_any_event(MessageSent(role="user", text="first"))
        await scheduler.shutdown()
        assert (await store.get(tid))["state"] == "scheduled"
        fresh = TaskScheduler(store, bus, runner)
        await fresh.hydrate()
        await fresh._on_any_event(MessageSent(role="user", text="second"))
        await fresh.shutdown()
        await fresh._on_any_event(MessageSent(role="user", text="third"))
        await fresh.shutdown()
        assert brain.calls == ["Read", "Read"]
        assert (await store.get(tid))["state"] == "completed"
    finally:
        await scheduler.shutdown()
        await store.close()


async def test_client_timezone_context_is_isolated_between_turns():
    from jarvis.tasks.context import client_timezone

    async def turn(zone):
        token = client_timezone.set(zone)
        try:
            await asyncio.sleep(0)
            return client_timezone.get()
        finally:
            client_timezone.reset(token)

    assert await asyncio.gather(turn("Europe/Berlin"), turn("America/Los_Angeles")) == [
        "Europe/Berlin",
        "America/Los_Angeles",
    ]
    assert client_timezone.get() is None


def test_calendar_contract_reaches_sql_api_and_typescript():
    from pathlib import Path

    from jarvis.tasks.schema import PAUSABLE_TRIGGER_TYPES, TRIGGER_TYPES
    from jarvis.ui.web.tasks_routes import _row_to_summary

    trigger = TriggerCalendar(timezone="America/Los_Angeles", local_time="08:00")
    spec = TaskSpec(title="Inbox", trigger=trigger, action=AgentAction(prompt="Read"))
    row = {
        "id": str(spec.id),
        "title": spec.title,
        "state": "scheduled",
        "trigger_type": "calendar",
        "spec_json": spec.model_dump_json(),
    }
    assert _row_to_summary(row)["trigger"] == trigger.model_dump(mode="json")
    assert "calendar" in TRIGGER_TYPES and "calendar" in PAUSABLE_TRIGGER_TYPES
    assert "'calendar'" in SCHEMA_FILE.read_text(encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    typescript = (root / "jarvis/ui/web/frontend/src/views/tasks/taskSpec.ts").read_text(
        encoding="utf-8"
    )
    for field in (
        "calendar",
        "timezone",
        "local_time",
        "weekdays",
        "month_days",
        "months",
        "start_date",
    ):
        assert field in typescript


def test_calendar_proposal_keeps_timezone_after_approval_context_changes():
    from jarvis.society.proposals import validate
    from jarvis.tasks.context import client_timezone

    token = client_timezone.set("America/Los_Angeles")
    try:
        payload = validate(
            "routine",
            {
                "title": "Inbox",
                "prompt": "Read",
                "schedule": {"kind": "calendar", "local_time": "08:00"},
            },
            catalog=[],
        )
    finally:
        client_timezone.reset(token)
    assert payload["schedule"]["timezone"] == "America/Los_Angeles"


@pytest.mark.parametrize(
    "schedule",
    [
        {"event_name": "InventedInboxEvent"},
        {"event_name": "MessageSent", "filter_expr": "unknown_field == 'value'"},
        {"event_name": "MessageSent", "filter_expr": "text.startswith('x')"},
    ],
)
def test_unknown_events_and_unusable_filters_fail_before_scheduling(schedule):
    from jarvis.tasks.event_catalog import validate_event_schedule

    with pytest.raises(ValueError):
        validate_event_schedule(schedule)


async def test_http_timezone_propagates_to_spawned_turn_without_leaking():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from jarvis.tasks.context import client_timezone
    from jarvis.ui.web.agent_chat_routes import MessageBody, post_message

    captured = []

    class Service:
        async def send(self, *args, **kwargs):
            async def turn():
                await asyncio.sleep(0)
                captured.append(client_timezone.get())

            self.task = asyncio.create_task(turn())
            return "turn"

    service = Service()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_chat=service)))
    await post_message(
        "society:qa", MessageBody(text="Schedule", timezone="America/Los_Angeles"), request
    )
    await service.task
    assert captured == ["America/Los_Angeles"]
    assert client_timezone.get() is None
    with pytest.raises(HTTPException) as error:
        await post_message(
            "society:qa", MessageBody(text="Schedule", timezone="Mars/Nope"), request
        )
    assert error.value.status_code == 422
