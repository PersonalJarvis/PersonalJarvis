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
        seed_starter_team=False,
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


def test_approvals_over_rest(client):
    c, _ = client
    c.post("/api/society/agents", json={"name": "Mailbox"})
    created = c.post(
        "/api/society/approvals",
        json={
            "agent_id": "mailbox",
            "trace_id": "t",
            "capability": "plugin:gmail",
            "action": {"verb": "send"},
            "summary": "Send the report",
        },
    )
    assert created.status_code == 200, created.text
    item = created.json()["approval"]
    assert item["state"] == "pending"
    listed = c.get("/api/society/approvals").json()
    assert [a["id"] for a in listed["approvals"]] == [item["id"]]
    assert c.get("/api/society/approvals", params={"agent_id": "nobody"}).json()["total"] == 0
    board = c.get("/api/society/events", params={"trace_id": "t"}).json()["events"]
    assert [e["msg_type"] for e in board] == ["HOLD"]
    res = c.post(
        f"/api/society/approvals/{item['id']}/resolve", json={"approve": True, "note": "ok"}
    )
    assert res.json()["approval"]["state"] == "approved"
    assert c.get("/api/society/approvals").json()["total"] == 0
    assert c.post("/api/society/approvals/nope/resolve", json={"approve": False}).status_code == 404
    assert c.post("/api/society/approvals/resurface").json()["total"] == 0
    assert (
        c.post("/api/society/approvals", json={"agent_id": "ghost", "capability": "x"}).status_code
        == 404
    )


def test_routines_over_rest(client):
    from tests.unit.society.test_routines import FakeScheduler, FakeTaskStore

    c, _ = client
    c.post("/api/society/agents", json={"name": "Mailbox", "title": "Gmail agent"})
    assert c.get("/api/society/agents/mailbox/routines").status_code == 503
    store = FakeTaskStore()
    c.app.state.task_store = store
    c.app.state.task_scheduler = FakeScheduler(store)
    res = c.post(
        "/api/society/agents/mailbox/routines",
        json={"title": "Morning brief", "prompt": "Summarize mail.", "schedule": {"kind": "every"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["title"] == "[agent:Mailbox] Morning brief"
    assert res.json()["tags"] == ["society", "agent:mailbox"]
    listed = c.get("/api/society/agents/mailbox/routines").json()
    assert listed["total"] == 1 and listed["routines"][0]["state"] == "scheduled"
    bad = c.post(
        "/api/society/agents/mailbox/routines",
        json={"title": "x", "prompt": "p", "schedule": {"kind": "cron"}},
    )
    assert bad.status_code == 422
    assert c.get("/api/society/agents/ghost/routines").status_code == 404


def test_browser_routes(client, tmp_path, monkeypatch):
    c, _ = client
    status = c.get("/api/society/browser/status").json()
    assert status["installed"] is False and status["phase"] == "idle"
    c.post("/api/society/agents", json={"name": "Scout"})
    per_agent = c.get("/api/society/agents/scout/browser").json()
    assert per_agent["installed"] is False and per_agent["mode"] == "own"
    assert per_agent["logged_in_profile"] is False
    # Not installed: a login session is refused with a typed reason.
    refused = c.post("/api/society/agents/scout/browser/login", json={})
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "blocked_by_policy"
    assert c.post("/api/society/agents/scout/browser/login/done").json()["closed"] is False
    # Attach mode is a roster field with its own check.
    patched = c.patch("/api/society/agents/scout", json={"browser_mode": "attach"}).json()
    assert patched["agent"]["browser_mode"] == "attach"
    assert c.patch("/api/society/agents/scout", json={"browser_mode": "cloud"}).status_code == 409
    domains = c.patch(
        "/api/society/agents/scout", json={"browser_allowed_domains": ["Mail.Google.com", "x.com"]}
    ).json()
    assert domains["agent"]["browser_allowed_domains"] == ["mail.google.com", "x.com"]
    # Install kicks off a background thread; a stub keeps the test offline.
    from jarvis.society.browser import install as install_mod

    monkeypatch.setattr(install_mod, "start_install", lambda data_dir=None: (True, "stubbed"))
    started = c.post("/api/society/browser/install").json()
    assert started["started"] is True and started["message"] == "stubbed"


# ------------------------------------------------------------------- memory


@pytest.fixture
def memory_client(tmp_path: Path):
    cfg = SimpleNamespace(
        wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")),
        memory=SimpleNamespace(data_dir=str(tmp_path / "data")),
    )
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False, cfg=lambda: cfg)
    app = FastAPI()
    app.include_router(router)
    app.state.society = None
    app.state.society_factory = lambda: runtime
    with TestClient(app) as c:
        yield c, tmp_path / "vault"


def test_memory_overview_recall_and_promotion_through_approvals(memory_client):
    c, vault = memory_client
    c.post("/api/society/agents", json={"name": "Scout"})
    # A legacy shared proposal remains manageable by the person after agent sharing stops.
    import asyncio

    runtime: SocietyRuntime = c.app.state.society
    scout = asyncio.run(runtime.roster.get("scout"))
    assert scout is not None
    got = asyncio.run(
        runtime.memory.propose_shared(scout, "Hosting", "We host on Hetzner.", root=vault)
    )
    view = c.get("/api/society/memory").json()
    assert view["shared"] == []
    assert [u["id"] for u in view["unreviewed"]] == [got["knowledge_id"]]
    assert next(a for a in view["agents"] if a["agent_id"] == "scout")["notes"] == 1
    # The proposal waits for the person: the figure stands at the gate (rule 2 beats rule 4).
    assert c.get("/api/society/agents/scout").json()["agent"]["checkpoint"] == "gate"
    # Ordinary recall cannot expose another agent's notes, even to the society lead.
    hits = c.post("/api/society/memory/recall", json={"query": "Hetzner hosting"}).json()["hits"]
    assert hits == []
    own = c.post(
        "/api/society/memory/recall",
        json={"query": "Hetzner hosting", "agent_id": "scout"},
    ).json()["hits"]
    assert own and all(hit["scope"] == "own" for hit in own)
    # Approving the share item promotes the page.
    approvals = c.get("/api/society/approvals").json()["approvals"]
    assert approvals[0]["capability"] == "core:memory:share"
    res = c.post(f"/api/society/approvals/{approvals[0]['id']}/resolve", json={"approve": True})
    assert res.status_code == 200, res.text
    assert res.json()["promoted"] == "society/shared/hosting.md"
    assert (vault / "society" / "shared" / "hosting.md").is_file()
    view = c.get("/api/society/memory").json()
    assert view["shared"][0]["title"] == "Hosting" and view["unreviewed"] == []
    hits = c.post("/api/society/memory/recall", json={"query": "Hetzner"}).json()["hits"]
    assert hits == []
    # Direct promote/dismiss on unknown rows answer 404.
    assert c.post("/api/society/memory/999/promote").status_code == 404
    assert c.post("/api/society/memory/999/dismiss").status_code == 404


def test_bind_agent_chat_creates_the_canonical_session(tmp_path: Path):
    from jarvis.agent_chat.store import AgentChatStore

    class FakeService:
        def __init__(self, store: AgentChatStore) -> None:
            self.store = store

        def is_running(self, session_id: str) -> bool:
            return False

    svc = FakeService(AgentChatStore(tmp_path / "chat.db"))
    cfg = SimpleNamespace(
        memory=SimpleNamespace(data_dir=str(tmp_path / "data")),
        wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")),
    )
    runtime = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    app = FastAPI()
    app.include_router(router)
    app.state.society = None
    app.state.society_factory = lambda: runtime
    with TestClient(app) as c:
        c.post(
            "/api/society/agents", json={"name": "Scout", "provider": "openai", "model": "gpt-5"}
        )
        res = c.post("/api/society/agents/scout/chat")
        assert res.status_code == 200, res.text
        session = res.json()["session"]
        assert session["session_id"] == "society:scout" and session["surface"] == "society"
        assert session["provider"] == "openai" and session["model"] == "gpt-5"
        # Idempotent: the same session comes back, nothing is duplicated.
        again = c.post("/api/society/agents/scout/chat").json()["session"]
        assert again["session_id"] == session["session_id"]
        assert c.post("/api/society/agents/nobody/chat").status_code == 404
    svc.store.close()


def test_bind_agent_chat_without_a_chat_service_is_503(memory_client):
    c, _ = memory_client
    c.post("/api/society/agents", json={"name": "Scout"})
    assert c.post("/api/society/agents/scout/chat").status_code == 503


def test_pausing_moves_the_figure_home(memory_client):
    c, _ = memory_client
    c.post("/api/society/agents", json={"name": "Scout"})
    runtime: SocietyRuntime = c.app.state.society
    import asyncio

    asyncio.run(
        runtime.approvals.enqueue(
            agent_id="scout", trace_id="t", capability="x", action={}, summary="x"
        )
    )
    assert c.get("/api/society/agents/scout").json()["agent"]["checkpoint"] == "gate"
    res = c.patch("/api/society/agents/scout", json={"state": "paused"})
    assert res.json()["agent"]["checkpoint"] == "idle"
