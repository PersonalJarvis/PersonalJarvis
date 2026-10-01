"""The chat pick survives restart and seats voice-created agents without inference."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore, ChatSelection
from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web import agent_chat_routes
from jarvis.ui.web.society_routes import router as society_router


@pytest.fixture
def app(tmp_path, monkeypatch):
    async def no_live_models():
        return {}

    monkeypatch.setattr(agent_chat_routes, "_live_cli_models", no_live_models)
    monkeypatch.setattr(agent_chat_routes, "_cli_installed", lambda runner: False)
    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: False)
    application = FastAPI()
    application.include_router(agent_chat_routes.router)
    application.include_router(society_router)
    application.state.agent_chat = AgentChatService(AgentChatStore(tmp_path / "chat.db"))
    runtime = SocietyRuntime(tmp_path / "society", seed_starter_team=False)
    application.state.society_factory = lambda: runtime
    return application


def test_selection_survives_restart_without_creating_a_chat(tmp_path: Path):
    db = tmp_path / "chat.db"
    store = AgentChatStore(db)
    selection = ChatSelection("claude-api", "claude-sonnet-5-5", "high", "account-a")
    store.save_chat_selection(selection)
    store.close()
    reopened = AgentChatStore(db)
    assert reopened.chat_selection() == selection
    assert reopened.list_sessions() == []
    reopened.close()


def test_legacy_choice_comes_from_the_last_user_chat_not_background_activity():
    store = AgentChatStore()
    earlier = store.create_session(
        provider="openai", model="earlier", effort="", cwd="", surface="jarvis"
    )
    latest = store.create_session(
        provider="ollama", model="last-used", effort="", cwd="", surface="jarvis"
    )
    ide = store.create_session(
        provider="openai-codex", model="ide", effort="", cwd="", surface="agent"
    )
    for session, timestamp, payload in [
        (earlier, 100, {"text": "first"}),
        (latest, 200, {"text": "latest user chat"}),
        (ide, 300, {"text": "unrelated IDE work"}),
        (earlier, 400, {"text": "automatic continuation", "origin": "control"}),
    ]:
        store.append_event(
            session.session_id, {"kind": "user_message", "ts_ms": timestamp, "payload": payload}
        )
    assert store.chat_selection() == ChatSelection("ollama", "last-used")
    store.save_chat_selection(ChatSelection("openai-codex", "explicit-new-choice"))
    assert store.chat_selection() == ChatSelection("openai-codex", "explicit-new-choice")
    store.close()


@pytest.mark.parametrize(
    "provider,model",
    [
        ("claude-api", "claude-sonnet-5-5"),
        ("openai-codex", "gpt-5.4"),
        ("ollama", "local-model"),
    ],
)
def test_pick_catalog_and_voice_created_agent_share_the_exact_model(app, provider, model):
    with TestClient(app) as client:
        pick = {"provider": provider, "model": model, "effort": ""}
        response = client.put("/api/agent-chat/selection", json=pick)
        assert response.status_code == 200, response.text
        catalog = client.get("/api/agent-chat/catalog?surface=jarvis").json()
        assert catalog["selection"] == {**pick, "account_id": ""}
        assert client.get("/api/agent-chat/catalog?surface=agent").json()["selection"] is None
        # A voice tool call carries no Society chat header.
        response = client.post("/api/society/agents", json={"name": "Helper"})
        assert response.status_code == 200, response.text
        agent = response.json()["agent"]
        assert (agent["provider"], agent["model"]) == (provider, model)
        runtime = app.state.society
        from jarvis.society.chat_binding import ensure_session
        from jarvis.society.roster import AgentRecord

        bound = ensure_session(app.state.agent_chat, SimpleNamespace(), AgentRecord.from_row(agent))
        assert (bound.provider, bound.model) == (provider, model)
        assert runtime is not None


def test_explicit_seat_does_not_mix_in_remembered_model_or_account(app):
    app.state.agent_chat.store.save_chat_selection(
        ChatSelection("claude-api", "claude-sonnet-5-5", "high", "claude-account")
    )
    with TestClient(app) as client:
        inherited = client.post("/api/society/agents", json={"name": "Inherited"}).json()["agent"]
        explicit = client.post(
            "/api/society/agents",
            json={
                "name": "Explicit",
                "provider": "openai-codex",
                "model": "gpt-5.4",
            },
        ).json()["agent"]
    assert inherited["account_id"] == "claude-account"
    assert explicit["model"] == "gpt-5.4"
    assert explicit["account_id"] == ""
    assert explicit["effort"] == ""


def test_unknown_provider_does_not_replace_a_saved_pick(app):
    selection = ChatSelection("openai-codex", "gpt-5.4")
    app.state.agent_chat.store.save_chat_selection(selection)
    with TestClient(app) as client:
        assert (
            client.put("/api/agent-chat/selection", json={"provider": "missing"}).status_code == 400
        )
    assert app.state.agent_chat.store.chat_selection() == selection


def test_voice_creation_loads_the_saved_selection_before_chat_is_opened(app):
    service = app.state.agent_chat
    service.store.save_chat_selection(ChatSelection("openai-codex", "chat-model"))
    app.state.agent_chat = None
    app.state.agent_chat_factory = lambda: service
    with TestClient(app) as client:
        response = client.post("/api/society/agents", json={"name": "After restart"})
    assert response.status_code == 200, response.text
    assert response.json()["agent"]["model"] == "chat-model"


def test_unavailable_selection_store_never_falls_back_to_a_different_model(app):
    def unavailable():
        raise OSError("database unavailable")

    app.state.agent_chat = None
    app.state.agent_chat_factory = unavailable
    with TestClient(app) as client:
        response = client.post("/api/society/agents", json={"name": "Must not fall back"})
    assert response.status_code == 503


def test_chat_account_is_preserved_on_new_session(app, tmp_path, monkeypatch):
    from jarvis import agent_accounts

    monkeypatch.setattr(agent_accounts, "resolve", lambda value: SimpleNamespace(platform="codex"))
    with TestClient(app) as client:
        response = client.post(
            "/api/agent-chat/sessions",
            json={
                "surface": "jarvis",
                "provider": "openai-codex",
                "model": "chat-model",
                "account_id": "chosen-account",
                "cwd": str(tmp_path),
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["account_id"] == "chosen-account"
        agent = client.post("/api/society/agents", json={"name": "Same account"}).json()["agent"]
    assert agent["account_id"] == "chosen-account"


def test_selection_contract_matches_sql_api_and_frontend(app):
    import re
    from dataclasses import fields

    from jarvis.ui.web.agent_chat_routes import ChatSelectionBody

    keys = {field.name for field in fields(ChatSelection)}
    assert keys == set(ChatSelectionBody.model_fields)
    columns = app.state.agent_chat.store._conn.execute("PRAGMA table_info(jarvis_chat_selection)")
    assert {column["name"] for column in columns} - {"id"} == keys
    source = Path("jarvis/ui/web/frontend/src/lib/agentChatApi.ts").read_text(encoding="utf-8")
    body = re.search(r"export interface ChatSelection \{([^}]+)\}", source).group(1)
    assert set(re.findall(r"^\s*(\w+)\??:", body, re.MULTILINE)) == keys
    operation = app.openapi()["paths"]["/api/agent-chat/selection"]["put"]
    assert operation["tags"] == ["agent-chat"]
    assert operation["summary"]


def test_society_creator_keeps_its_own_seat_even_when_jarvis_selected_another(app):
    app.state.agent_chat.store.save_chat_selection(ChatSelection("openai-codex", "jarvis-model"))
    with TestClient(app) as client:
        creator = client.post(
            "/api/society/agents",
            json={
                "name": "Creator",
                "provider": "ollama",
                "model": "creators-model",
            },
        ).json()["agent"]
        response = client.post(
            "/api/society/agents",
            json={"name": "Child"},
            headers={"X-Jarvis-Chat-Session": f"society:{creator['agent_id']}"},
        )
    assert response.status_code == 200, response.text
    child = response.json()["agent"]
    assert (child["provider"], child["model"]) == ("ollama", "creators-model")


def test_switching_provider_clears_the_previous_subscription_account(app, tmp_path):
    svc = app.state.agent_chat
    session = svc.create_session(
        provider="openai-codex",
        model="chat-model",
        surface="jarvis",
        cwd=str(tmp_path),
        account_id="codex-account",
    )
    with TestClient(app) as client:
        response = client.patch(
            f"/api/agent-chat/sessions/{session.session_id}",
            json={
                "provider": "ollama",
                "model": "local-model",
                "effort": "",
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["account_id"] == ""
    assert svc.store.chat_selection() == ChatSelection("ollama", "local-model")


def test_opening_history_and_other_surfaces_do_not_replace_selection(app, tmp_path):
    store = app.state.agent_chat.store
    old = store.create_session(
        provider="openai", model="old", effort="", surface="jarvis", cwd=str(tmp_path)
    )
    selection = ChatSelection("openai-codex", "gpt-5.4")
    store.save_chat_selection(selection)
    with TestClient(app) as client:
        assert client.get(f"/api/agent-chat/sessions/{old.session_id}").status_code == 200
        response = client.post(
            "/api/agent-chat/sessions",
            json={
                "surface": "agent",
                "provider": "openai",
                "model": "ide-model",
                "cwd": str(tmp_path),
            },
        )
        assert response.status_code == 201, response.text
    assert store.chat_selection() == selection


async def test_chat_send_keeps_its_pick_when_global_worker_differs(app, tmp_path, monkeypatch):
    from jarvis.agent_chat import service
    from jarvis.core import runtime_refs, task_agent

    seen = []

    async def record_turn(handle, prompt, runner, **kwargs):
        session = handle.session
        seen.append((session.provider, session.model, session.effort))

    async def selected_subscription(provider):
        return (provider, "codex-cli")

    monkeypatch.setattr(task_agent, "subscription_seat_off_loop", selected_subscription)
    monkeypatch.setattr(service, "run_cli_turn", record_turn)
    monkeypatch.setattr(
        runtime_refs,
        "get_brain_manager",
        lambda: SimpleNamespace(
            _config=SimpleNamespace(
                brain=SimpleNamespace(
                    worker=SimpleNamespace(
                        provider="openai",
                        model="unwanted-api-model",
                        reasoning_effort="low",
                    )
                )
            ),
        ),
    )
    svc = app.state.agent_chat
    session = svc.create_session(
        provider="openai-codex",
        model="chat-model",
        effort="high",
        surface="jarvis",
        cwd=str(tmp_path),
    )
    await svc.send(session.session_id, "Hello")
    await svc._running[session.session_id].task
    assert seen == [("openai-codex", "chat-model", "high")]
    assert svc.store.chat_selection() == ChatSelection("openai-codex", "chat-model", "high")
