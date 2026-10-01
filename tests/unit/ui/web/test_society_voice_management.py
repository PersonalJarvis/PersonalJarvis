"""Managing agents by voice: a structured brief becomes real instructions, a
schedule becomes a routine with a spoken next run, and routines can be paused,
run and deleted through the same routes the app commands call."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society.runtime import SocietyRuntime
from jarvis.tasks import context as task_context
from jarvis.tasks.context import CLIENT_TIMEZONE_HEADER
from jarvis.ui.web.society_routes import router
from tests.unit.society.test_routines import FakeScheduler, FakeTaskStore


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=f"{name} tool.", risk_tier="monitor")


TOOLS = {name: _tool(name) for name in ("gmail", "google_calendar", "search-web")}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(task_context, "_reported_ui_timezone", None)
    runtime = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        brain_tools=lambda: TOOLS,
        plugin_state=lambda: (["gmail", "google_calendar"], ["gmail"]),
    )
    app = FastAPI()
    app.include_router(router)
    app.state.society = None
    app.state.society_factory = lambda: runtime
    store = FakeTaskStore()
    app.state.task_store = store
    app.state.task_scheduler = FakeScheduler(store)
    with TestClient(app) as c:
        try:
            yield c
        finally:
            assert c.portal is not None
            c.portal.call(runtime.close)


def test_a_brief_becomes_sectioned_instructions_and_a_readback(client) -> None:
    res = client.post(
        "/api/society/agents",
        json={
            "name": "Briefer",
            "title": "Morning briefing",
            "mission": "Give the user a short, useful start to the day.",
            "responsibilities": ["Summarize important mail", "List today's calendar events"],
            "working_rules": ["Read Gmail first, then the calendar"],
            "output_format": "An artifact with three sections.",
            "boundaries": ["Never send or delete mail"],
            "success_criteria": "Readable in two minutes.",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    description = body["agent"]["description"]
    assert description.startswith("Give the user a short, useful start to the day.")
    assert "## Responsibilities\n- Summarize important mail" in description
    assert "## Boundaries\n- Never send or delete mail" in description
    assert "## Output\nAn artifact with three sections." in description
    assert "plugin:google_calendar" in body["agent"]["focus"]
    assert body["readback"]["not_connected"] == ["google-calendar"]


def test_a_brief_patch_rewrites_the_instructions(client) -> None:
    client.post("/api/society/agents", json={"name": "Briefer", "description": "Old text."})
    patched = client.patch(
        "/api/society/agents/briefer", json={"mission": "New mission.", "boundaries": ["No mail"]}
    ).json()
    assert patched["agent"]["description"] == "New mission.\n\n## Boundaries\n- No mail"
    assert "readback" in patched


def test_a_daily_routine_needs_a_timezone_and_reports_its_next_run(client) -> None:
    client.post("/api/society/agents", json={"name": "Briefer"})
    body = {
        "title": "Morning briefing",
        "prompt": "Summarize mail and calendar.",
        "schedule": {"kind": "calendar", "local_time": "08:00"},
    }
    missing = client.post("/api/society/agents/briefer/routines", json=body)
    assert missing.status_code == 422
    assert missing.json()["detail"]["reason"] == "timezone_required"

    created = client.post(
        "/api/society/agents/Briefer/routines",
        json=body,
        headers={CLIENT_TIMEZONE_HEADER: "Europe/Berlin"},
    )
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["tags"] == ["society", "agent:briefer"]
    assert data["state"] == "scheduled"
    assert data["next_run"].endswith("(Europe/Berlin)")
    spec = client.app.state.task_store.rows[0]["spec_json"]
    assert '"timezone":"Europe/Berlin"' in spec and '"local_time":"08:00"' in spec


def test_the_ui_reported_zone_serves_a_voice_turn(client) -> None:
    task_context.remember_ui_timezone("America/New_York")
    client.post("/api/society/agents", json={"name": "Briefer"})
    created = client.post(
        "/api/society/agents/briefer/routines",
        json={
            "title": "Brief",
            "prompt": "Brief me.",
            "schedule": {"kind": "calendar", "local_time": "07:30"},
        },
    )
    assert created.status_code == 200, created.text
    assert '"timezone":"America/New_York"' in client.app.state.task_store.rows[0]["spec_json"]


def test_routine_operations_pause_resume_run_and_delete(client) -> None:
    client.post("/api/society/agents", json={"name": "Briefer"})
    task_id = client.post(
        "/api/society/agents/briefer/routines",
        json={"title": "Hourly", "prompt": "Check.", "schedule": {"kind": "every"}},
    ).json()["id"]
    url = f"/api/society/agents/briefer/routines/{task_id}/operation"

    assert client.post(url, json={"operation": "pause"}).json()["state"] == "paused"
    assert client.post(url, json={"operation": "resume"}).json()["state"] == "scheduled"
    assert client.post(url, json={"operation": "run"}).json()["ok"] is True
    assert client.app.state.task_scheduler.ran == [task_id]
    assert client.post(url, json={"operation": "delete"}).json()["state"] == "deleted"
    assert client.app.state.task_store.rows == []
    assert client.post(url, json={"operation": "pause"}).status_code == 404
    assert client.post(url, json={"operation": "explode"}).status_code == 422


def test_ui_timezone_route_validates_the_zone(tmp_path: Path, monkeypatch) -> None:
    from jarvis.ui.web.tasks_routes import router as tasks_router

    monkeypatch.setattr(task_context, "_reported_ui_timezone", None)
    app = FastAPI()
    app.include_router(tasks_router)
    with TestClient(app) as c:
        assert (
            c.post("/api/tasks/client-timezone", json={"timezone": "Mars/Base"}).status_code == 422
        )
        assert c.post("/api/tasks/client-timezone", json={"timezone": "Europe/Berlin"}).json()["ok"]
    assert task_context.turn_timezone() == "Europe/Berlin"


def test_a_spoken_schedule_with_day_names_and_blank_fields_is_accepted(client) -> None:
    client.post("/api/society/agents", json={"name": "Briefer"})
    created = client.post(
        "/api/society/agents/briefer/routines",
        json={
            "title": "Weekday brief",
            "prompt": "Brief me.",
            "schedule": {
                "kind": "calendar",
                "local_time": "8:00",
                "days": ["mon", "Friday"],
                "timezone": "",
                "expression": None,
                # A model fills sibling fields of other kinds (seen live).
                "interval_seconds": 86400,
            },
        },
        headers={CLIENT_TIMEZONE_HEADER: "Europe/Berlin"},
    )
    assert created.status_code == 200, created.text
    spec = client.app.state.task_store.rows[0]["spec_json"]
    assert '"weekdays":[0,4]' in spec and '"local_time":"08:00"' in spec
