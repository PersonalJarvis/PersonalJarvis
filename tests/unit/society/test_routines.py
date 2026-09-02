"""Routines are tagged Automations tasks that carry the agent's identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.society.roster import Roster
from jarvis.society.routines import (
    agent_tag,
    build_task_spec,
    create_routine,
    is_agent_routine,
    list_routines,
)
from jarvis.society.store import SocietyStore


class FakeTaskStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, spec, *, trace_id=None) -> str:
        task_id = f"task-{len(self.rows) + 1}"
        self.rows.append(
            {
                "id": task_id,
                "title": spec.title,
                "state": "scheduled",
                "spec_json": spec.model_dump_json(),
                "due_at_ns": 1,
            }
        )
        return task_id

    async def list(self, state_filter=None, *, limit: int = 100) -> list[dict]:
        return list(self.rows)


class FakeScheduler:
    def __init__(self, store: FakeTaskStore) -> None:
        self.store = store
        self.scheduled: list[str] = []

    async def schedule(self, spec) -> str:
        task_id = await self.store.insert(spec)
        self.scheduled.append(task_id)
        return task_id


@pytest.fixture
async def agent(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    rec, _ = await Roster(store).create(
        name="Mailbox",
        title="Gmail agent",
        description="Handle my mail.",
        focus=["plugin:gmail"],
    )
    try:
        yield rec
    finally:
        await store.close()


async def test_spec_carries_identity_tags_and_grants(agent):
    spec = build_task_spec(
        agent,
        title="  Morning   inbox  brief ",
        prompt="Summarize new mail.",
        schedule={"kind": "every", "interval_seconds": 3600, "start_at": "2026-09-02T07:00:00"},
        plugin_grants=[{"plugin_id": "gmail", "scope": "read"}, {"plugin_id": ""}],
    )
    assert spec.title == "[agent:Mailbox] Morning inbox brief"
    assert spec.created_by == "society"
    assert spec.tags == ("society", agent_tag("mailbox"))
    assert spec.trigger.type == "every" and spec.trigger.interval_seconds == 3600
    assert spec.action.kind == "agent"
    assert spec.action.prompt.startswith("You are Mailbox, Gmail agent,")
    assert "Handle my mail." in spec.action.prompt and "Summarize new mail." in spec.action.prompt
    assert "plugin:gmail" in spec.action.prompt
    assert [(g.plugin_id, g.scope) for g in spec.action.plugin_grants] == [("gmail", "read")]


async def test_every_schedule_kind(agent):
    at = build_task_spec(
        agent,
        title="t",
        prompt="p",
        schedule={"kind": "at_time", "iso_timestamp": "2026-09-02T07:00:00"},
    )
    assert at.trigger.type == "at_time"
    ev = build_task_spec(
        agent, title="t", prompt="p", schedule={"kind": "on_event", "event_name": "MessageSent"}
    )
    assert ev.trigger.type == "on_event"
    delay = build_task_spec(
        agent, title="t", prompt="p", schedule={"kind": "after_delay", "delay_seconds": 5}
    )
    assert delay.trigger.type == "after_delay"
    with pytest.raises(ValueError):
        build_task_spec(agent, title="t", prompt="p", schedule={"kind": "cron"})


async def test_list_filters_by_agent_tag(agent):
    store = FakeTaskStore()
    spec = build_task_spec(agent, title="brief", prompt="p", schedule={"kind": "every"})
    await create_routine(store, None, spec)
    store.rows.append(
        {
            "id": "other",
            "title": "x",
            "state": "scheduled",
            "spec_json": json.dumps({"tags": ["agent:quill"]}),
        }
    )
    store.rows.append(
        {"id": "broken", "title": "y", "state": "scheduled", "spec_json": "{not json"}
    )
    assert is_agent_routine(store.rows[0], "mailbox")
    assert not is_agent_routine(store.rows[1], "mailbox")
    rows = await list_routines(store, "mailbox")
    assert [r["id"] for r in rows] == ["task-1"]
    assert rows[0]["title"] == "[agent:Mailbox] brief"
    assert rows[0]["trigger"]["type"] == "every"


async def test_scheduler_is_preferred(agent):
    store = FakeTaskStore()
    scheduler = FakeScheduler(store)
    spec = build_task_spec(agent, title="brief", prompt="p", schedule={"kind": "every"})
    task_id = await create_routine(store, scheduler, spec)
    assert scheduler.scheduled == [task_id]
