"""/api/society: roster CRUD with derived focus, messaging, assign, board, controls."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import router


class FakeManager:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.cancelled: list[str] = []
        self.bus = SimpleNamespace(subscribe_all=lambda h: lambda: None)

    async def dispatch(self, *, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        return f"m-{len(self.prompts)}"

    async def cancel(self, mission_id: str) -> None:
        self.cancelled.append(mission_id)


def _tool(name: str, desc: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor", schema={})


TOOLS = {
    "gmail": _tool("gmail", "Read and send mail."),
    "search-web": _tool("search-web", "Search the web."),
    "spawn-worker": _tool("spawn-worker", "Spawn."),
}


@pytest.fixture
def client(tmp_path: Path):
    manager = FakeManager()
    runtime = SocietyRuntime(
        tmp_path,
        mission_manager=lambda: manager,
        mission_bus=lambda: manager.bus,
        brain_tools=lambda: TOOLS,
    )
    app = FastAPI()
    app.include_router(router)
    app.state.society = None
    app.state.society_factory = lambda: runtime
    with TestClient(app) as c:
        yield c, manager


def test_lead_is_seeded_and_listed(client):
    c, _ = client
    body = c.get("/api/society/agents").json()
    assert [a["agent_id"] for a in body["agents"]] == ["jarvis"]
    assert body["agents"][0]["tier"] == "lead"
    assert body["agents"][0]["run_state"] == "idle"


def test_create_derives_focus_and_rules(client):
    c, _ = client
    res = c.post(
        "/api/society/agents",
        json={
            "name": "Mailbox",
            "title": "Gmail agent",
            "description": "You read and answer my mail; external mail only after approval.",
        },
    )
    assert res.status_code == 200, res.text
    agent = res.json()["agent"]
    assert res.json()["created"] is True
    assert agent["focus"] == ["plugin:gmail"]
    assert agent["approval_rules"]["require_approval"] == ["plugin:gmail:send"]
    assert agent["session_id"] == "society:mailbox"
    # Adopt-before-mint through REST.
    again = c.post("/api/society/agents", json={"name": "mailbox"}).json()
    assert again["created"] is False


def test_patch_rederives_focus_on_description_change(client):
    c, _ = client
    c.post("/api/society/agents", json={"name": "Scout", "description": "Research on the web."})
    first = c.get("/api/society/agents/scout").json()["agent"]
    assert first["focus"] == ["core:search-web"]
    patched = c.patch(
        "/api/society/agents/scout", json={"description": "Handle my mail inbox."}
    ).json()["agent"]
    assert patched["focus"] == ["plugin:gmail"]
    explicit = c.patch("/api/society/agents/scout", json={"focus": []}).json()["agent"]
    assert explicit["focus"] == []


def test_typed_errors(client):
    c, _ = client
    assert c.get("/api/society/agents/ghost").status_code == 404
    bad = c.post("/api/society/agents", json={"name": "Boss", "tier": "lead"})
    assert bad.status_code == 409
    assert bad.json()["detail"]["reason"] == "tier_not_allowed"
    assert c.delete("/api/society/agents/jarvis").status_code == 409
    c.post("/api/society/agents", json={"name": "Scout"})
    worse = c.patch("/api/society/agents/scout", json={"permission_ceiling": "block"})
    assert worse.status_code == 409


def test_message_lands_in_inbox_and_board(client):
    c, _ = client
    c.post("/api/society/agents", json={"name": "Scout"})
    res = c.post("/api/society/agents/scout/message", json={"text": "hello"})
    assert res.status_code == 200
    event = res.json()["event"]
    assert event["msg_type"] == "SAY" and event["from_agent"] == "user"
    inbox = c.get("/api/society/agents/scout/inbox").json()["events"]
    assert [e["payload"]["text"] for e in inbox] == ["hello"]
    board = c.get("/api/society/events").json()
    assert board["last_seq"] == 1
    assert c.get("/api/society/events", params={"trace_id": event["trace_id"]}).json()["events"]


def test_assign_dispatches_a_framed_mission(client):
    c, manager = client
    c.post(
        "/api/society/agents",
        json={"name": "Scout", "title": "Research scout", "description": "Find things on the web."},
    )
    res = c.post("/api/society/agents/scout/assign", json={"task": "Find the best VPS."})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"]["msg_type"] == "CLAIM"
    assert body["outcome"]["payload"]["run_id"] == "m-1"
    prompt = manager.prompts[0]
    assert prompt.startswith("You are Scout, Research scout,")
    assert "Find things on the web." in prompt and "Find the best VPS." in prompt
    assert "core:search-web" in prompt
    status = c.get("/api/society/status").json()
    assert status["active_runs"] == 1 and status["running"] == {"m-1": "scout"}
    listed = c.get("/api/society/agents").json()["agents"]
    assert next(a for a in listed if a["agent_id"] == "scout")["run_state"] == "working"


def test_assign_from_specialist_is_vetoed(client):
    c, manager = client
    c.post("/api/society/agents", json={"name": "Scout"})
    c.post("/api/society/agents", json={"name": "Quill"})
    res = c.post("/api/society/agents/quill/assign", json={"task": "x", "from_agent": "scout"})
    assert res.json()["outcome"]["msg_type"] == "VETO"
    assert res.json()["outcome"]["payload"]["reason"] == "tier_not_allowed"
    assert manager.prompts == []


def test_kill_agent_drops_runs_and_pauses(client):
    c, manager = client
    c.post("/api/society/agents", json={"name": "Scout"})
    c.post("/api/society/agents/scout/assign", json={"task": "go"})
    res = c.post("/api/society/agents/scout/kill")
    assert res.json()["runs_dropped"] == 1
    assert res.json()["agent"]["state"] == "paused"
    assert manager.cancelled == ["m-1"]


def test_kill_switch_round_trip(client):
    c, _ = client
    c.post("/api/society/agents", json={"name": "Scout"})
    on = c.post("/api/society/kill-switch").json()
    assert on["engaged"] is True
    assert c.get("/api/society/status").json()["kill_switch"] is True
    vetoed = c.post("/api/society/agents/scout/assign", json={"task": "x"}).json()
    assert vetoed["outcome"]["payload"]["reason"] == "kill_switch"
    assert c.post("/api/society/kill-switch/release").json()["engaged"] is False


def test_capabilities_catalog_hides_dispatch(client):
    c, _ = client
    ids = [r["id"] for r in c.get("/api/society/capabilities").json()["capabilities"]]
    assert ids == ["plugin:gmail", "core:search-web"]


def test_rooms_over_rest(client):
    c, _ = client
    c.post("/api/society/agents", json={"name": "Scout"})
    c.post("/api/society/agents", json={"name": "Quill"})
    missing = c.post("/api/society/rooms", json={"members": ["scout", "ghost"]})
    assert missing.status_code == 404
    room = c.post("/api/society/rooms", json={"members": ["scout", "quill"], "topic": "t"}).json()[
        "room"
    ]
    assert room["next_speaker"] == "scout"
    wrong = c.post(
        f"/api/society/rooms/{room['room_id']}/say", json={"member": "quill", "text": "me"}
    )
    assert wrong.status_code == 409
    ok = c.post(f"/api/society/rooms/{room['room_id']}/say", json={"member": "scout", "text": "hi"})
    assert ok.json()["room"]["message_count"] == 1
    settled = c.post(f"/api/society/rooms/{room['room_id']}/settle").json()["room"]
    assert settled["state"] == "settled"
    assert c.get("/api/society/rooms").json()["rooms"][0]["room_id"] == room["room_id"]


def test_missing_factory_is_503():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        assert c.get("/api/society/status").status_code == 503
