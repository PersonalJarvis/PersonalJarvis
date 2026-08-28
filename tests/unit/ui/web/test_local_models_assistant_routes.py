"""The assistant routes: run / session / test / benchmarks / health behind the pull gate.

The agent-chat service is real (in-memory store); the brain turn is not run
— ``send`` is observed through the service's own store events. The Ollama
server is the fake; the Agents-tier check is injected by patching the routes'
``_usable`` seam (a plain function, not a mock library).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.brain import ollama_inventory, ollama_runtime
from jarvis.core import config as cfg_mod
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, BrainTierConfig, JarvisConfig
from jarvis.local_models.assistant_prompt import DIAGNOSE_OPENER, SETUP_OPENER
from jarvis.local_models.assistant_session import NOT_READY
from jarvis.ui.web import local_models_assistant_routes as routes
from jarvis.ui.web.server import WebServer
from tests.fakes.fake_ollama_server import FakeOllamaServer

BASE = "/api/providers/ollama/local-models/assistant"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeOllamaServer:
    server = FakeOllamaServer()
    server.add("embeddinggemma", capabilities=("embedding",), embed_dim=768)

    def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=server.transport(), timeout=5.0)

    monkeypatch.setattr(ollama_inventory, "_make_client", _client)

    real_probe = ollama_runtime.probe_host

    async def _probe(base_url: str, *, transport=None) -> dict:
        return await real_probe(base_url, transport=server.transport())

    monkeypatch.setattr(ollama_runtime, "probe_host", _probe)
    return server


@pytest.fixture(autouse=True)
def live_probe_is_a_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """The routes ask every candidate pair for one real generation; a unit
    test answers "alive" for all of them instead of calling a vendor."""
    from jarvis.local_models import assistant_session

    async def _alive(cfg, pairs, *, timeout_s=20.0, tester=None):
        return {pair: (True, "") for pair in pairs}

    assistant_session._reset_for_tests()
    monkeypatch.setattr(assistant_session, "probe_live", _alive)


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebServer:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    cfg.brain.providers["ollama"] = BrainProviderConfig(
        model="qwen3.5:4b", base_url="http://fake:11434"
    )
    cfg.brain.worker = BrainTierConfig(provider="openai", model="gpt-x")
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    srv.app.state.agent_chat = AgentChatService(AgentChatStore(":memory:"))
    return srv


@pytest.fixture
def ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_usable", lambda _p: True)


def test_routes_sit_behind_the_pull_gate(server: WebServer) -> None:
    with TestClient(server.app) as client:
        assert client.get("/api/providers/openai/local-models/assistant/health").status_code == 400
        assert client.get("/api/providers/nope/local-models/assistant/session").status_code == 404


def test_session_is_null_until_a_run_and_names_the_tier(server: WebServer, ready: None) -> None:
    with TestClient(server.app) as client:
        body = client.get(f"{BASE}/session").json()
    assert body == {
        "session_id": None,
        "surface": "local-models",
        "provider": "openai",
        "model": "gpt-x",
        "ready": True,
        "reason": "",
    }


def test_run_refuses_honestly_without_the_agents_tier(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "_usable", lambda _p: False)
    with TestClient(server.app) as client:
        resp = client.post(f"{BASE}/run", json={"mode": "setup"})
        assert resp.status_code == 409
        assert resp.json()["detail"] == NOT_READY
        assert client.get(f"{BASE}/session").json()["ready"] is False
    assert server.app.state.agent_chat.store.list_sessions(surface="local-models") == []


def test_run_creates_one_session_and_sends_the_mode_opener(
    server: WebServer, ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc: AgentChatService = server.app.state.agent_chat
    sent: list[tuple[str, str]] = []

    async def fake_send(session_id: str, text: str) -> str:
        sent.append((session_id, text))
        return "turn-1"

    monkeypatch.setattr(svc, "send", fake_send)
    with TestClient(server.app) as client:
        first = client.post(f"{BASE}/run", json={"mode": "setup"})
        assert first.status_code == 200, first.text
        assert first.json() == {
            "session_id": first.json()["session_id"],
            "turn_id": "turn-1",
            "surface": "local-models",
        }
        second = client.post(f"{BASE}/run", json={"mode": "diagnose"})
        assert second.json()["session_id"] == first.json()["session_id"]
        state = client.get(f"{BASE}/session").json()
        assert state["session_id"] == first.json()["session_id"]
        assert client.post(f"{BASE}/run", json={"mode": "bogus"}).status_code == 422
    assert [text for _sid, text in sent] == [SETUP_OPENER, DIAGNOSE_OPENER]
    sessions = svc.store.list_sessions(surface="local-models")
    assert len(sessions) == 1 and sessions[0].provider == "openai"


def test_run_answers_409_while_a_turn_is_running(
    server: WebServer, ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agent_chat.service import SessionBusy

    svc: AgentChatService = server.app.state.agent_chat

    async def busy(session_id: str, text: str) -> str:
        raise SessionBusy(session_id)

    monkeypatch.setattr(svc, "send", busy)
    with TestClient(server.app) as client:
        resp = client.post(f"{BASE}/run", json={"mode": "test"})
    assert resp.status_code == 409 and "still answering" in resp.json()["detail"]


def test_test_route_runs_the_setup_test_and_health_reads_the_file(
    server: WebServer,
    fake: FakeOllamaServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The generation seam: every remaining role asks a brain for one real
    # answer, so the route needs a stub where the embedding role used to
    # answer straight from the Ollama inventory.
    from jarvis.local_models import assistant_test

    async def _ok(_cfg, model: str, **_kw) -> assistant_test.RoleCheck:
        return assistant_test.RoleCheck(
            model=model, status="ok", latency_ms=1.0, detail="Answered."
        )

    monkeypatch.setattr(assistant_test, "_generation_check", _ok)
    with TestClient(server.app) as client:
        before = client.get(f"{BASE}/health").json()
        assert before == {
            "status": "unknown",
            "reason": "",
            "since": None,
            "last_ok": None,
            "checked_at": None,
        }
        resp = client.post(f"{BASE}/test", json={"roles": ["chat"]})
        assert resp.status_code == 200, resp.text
        report = resp.json()
        assert report["roles"]["chat"]["status"] == "ok"
        assert report["overall"] == "ok"
        assert client.post(f"{BASE}/test", json={"roles": ["ack"]}).status_code == 400
        after = client.get(f"{BASE}/health").json()
    assert after["status"] == "ok" and after["checked_at"] == report["checked_at"]
    assert after["since"] == after["last_ok"] == report["checked_at"]
    written = json.loads((tmp_path / "state" / "local_models_health.json").read_text("utf-8"))
    assert written["overall"] == "ok"


def test_benchmarks_without_a_cache_is_curated_only(server: WebServer) -> None:
    with TestClient(server.app) as client:
        body = client.get(f"{BASE}/benchmarks").json()
    assert body["source"] == "curated" and body["rows"] == [] and body["note"]


def test_section_health_carries_the_local_models_badge_from_the_file(
    server: WebServer, fake: FakeOllamaServer
) -> None:
    from jarvis.local_models.health_monitor import write_health_record

    write_health_record("error", "chat: model not found")
    with TestClient(server.app) as client:
        resp = client.get("/api/providers/section-health?refresh=true")
    assert resp.status_code == 200, resp.text
    section = resp.json()["sections"]["local_models"]
    assert section["status"] == "error"
    assert section["detail"] == "chat: model not found"
