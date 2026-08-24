"""Local-models routes: inventory, unload, delete — through the fake server.

Pins: the routes exist only on pull-capable cards, the inventory pairs the
downloads with what is loaded and marks which role uses which model, an
offline server answers a sentence instead of an empty "you have nothing",
and delete refuses while a role points at the model unless a replacement is
named — in which case the roles are rewritten through the config writers
before the delete runs.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.brain import ollama_inventory
from jarvis.core import config_writer
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.ui.web import local_models_routes
from jarvis.ui.web.server import WebServer
from tests.fakes.fake_ollama_server import FakeOllamaServer

ROOT = "http://localhost:11434"
BASE = "/api/providers/ollama/local-models"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeOllamaServer:
    server = FakeOllamaServer()
    server.add("qwen3.5:4b", size=3_400_000_000, capabilities=("completion", "tools", "vision"))
    server.add("gemma4:12b-it-qat", size=7_200_000_000, capabilities=("completion", "tools"))
    server.add("qwen3-embedding:4b", size=2_500_000_000, capabilities=("embedding",))
    server.add("qwen3.5:4b-voice-8k", size=3_400_000_000)

    def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=server.transport())

    monkeypatch.setattr(ollama_inventory, "_make_client", _client)
    monkeypatch.setattr(local_models_routes, "_server_root", lambda: ROOT)
    return server


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Record the config writes instead of touching jarvis.toml."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        config_writer,
        "set_brain_provider_model",
        lambda provider, **kw: calls.append(("brain", provider, kw)),
    )
    monkeypatch.setattr(
        config_writer,
        "set_ultrawiki_slot",
        lambda key, value: calls.append(("ultrawiki", key, value)),
    )
    return calls


@pytest.fixture
def server(tmp_path, monkeypatch: pytest.MonkeyPatch) -> WebServer:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    cfg.brain.providers["ollama"] = BrainProviderConfig(
        model="qwen3.5:4b", deep_model="gemma4:12b-it-qat"
    )
    cfg.ultrawiki.embedding_provider = "ollama"
    cfg.ultrawiki.embedding_model = "qwen3-embedding:4b"
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    return srv


def test_routes_refuse_cards_without_a_pull_api(server: WebServer, fake) -> None:
    with TestClient(server.app) as client:
        assert client.get("/api/providers/openai/local-models/inventory").status_code == 400
        assert client.get("/api/providers/nope/local-models/inventory").status_code == 404


def test_inventory_lists_facts_roles_and_loaded_state(server: WebServer, fake) -> None:
    fake.load("qwen3.5:4b", size_vram=3_000_000_000)
    with TestClient(server.app) as client:
        resp = client.get(f"{BASE}/inventory")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["server"] == ROOT
    rows = {m["name"]: m for m in body["models"]}
    # The voice alias is Jarvis's own — hidden.
    assert set(rows) == {"qwen3.5:4b", "gemma4:12b-it-qat", "qwen3-embedding:4b"}
    chat = rows["qwen3.5:4b"]
    assert chat["used_by"] == ["chat"]
    assert chat["loaded"] is True
    assert chat["size_vram_bytes"] == 3_000_000_000
    assert chat["capabilities"] == ["completion", "tools", "vision"]
    assert chat["context_length"] == 262_144
    assert rows["gemma4:12b-it-qat"]["used_by"] == ["deep"]
    assert rows["qwen3-embedding:4b"]["used_by"] == ["embedding"]
    assert body["disk_bytes"] == 3_400_000_000 + 7_200_000_000 + 2_500_000_000
    assert body["loaded_vram_bytes"] == 3_000_000_000
    assert [r["name"] for r in body["running"]] == ["qwen3.5:4b"]


def test_inventory_answers_a_sentence_when_the_server_is_offline(server: WebServer, fake) -> None:
    fake.offline = True
    with TestClient(server.app) as client:
        resp = client.get(f"{BASE}/inventory")
    assert resp.status_code == 200
    body = resp.json()
    assert body["models"] == []
    assert "did not answer" in body["error"]


def test_detail_carries_the_long_facts(server: WebServer, fake) -> None:
    with TestClient(server.app) as client:
        resp = client.get(f"{BASE}/inventory/qwen3.5:4b")
        missing = client.get(f"{BASE}/inventory/ghost:7b")
    assert resp.status_code == 200
    body = resp.json()
    assert body["license"] == "Apache-2.0"
    assert body["parameters"] == "temperature 0.7"
    assert body["template"] == "{{ .Prompt }}"
    assert missing.status_code == 404


def test_unload_frees_the_model(server: WebServer, fake) -> None:
    fake.load("qwen3.5:4b")
    with TestClient(server.app) as client:
        resp = client.post(f"{BASE}/inventory/qwen3.5:4b/unload")
        after = client.get(f"{BASE}/inventory")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert ("POST", "/api/generate", {"model": "qwen3.5:4b", "keep_alive": 0}) in fake.calls
    assert after.json()["running"] == []


def test_delete_refuses_while_a_role_points_at_the_model(server: WebServer, fake, writes) -> None:
    with TestClient(server.app) as client:
        resp = client.delete(f"{BASE}/inventory/qwen3.5:4b")
    assert resp.status_code == 409
    assert "chat" in resp.json()["detail"]
    assert "qwen3.5:4b" in fake.models  # nothing was deleted
    assert writes == []


def test_delete_with_reassign_rewrites_the_roles_first(server: WebServer, fake, writes) -> None:
    with TestClient(server.app) as client:
        resp = client.delete(
            f"{BASE}/inventory/qwen3.5:4b", params={"reassign": "gemma4:12b-it-qat"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reassigned"] == ["chat"]
    assert body["reassigned_to"] == "gemma4:12b-it-qat"
    assert writes == [
        (
            "brain",
            "ollama",
            {"model": "gemma4:12b-it-qat", "deep_model": None, "tool_model": None},
        )
    ]
    assert "qwen3.5:4b" not in fake.models
    # The live config agrees with the TOML from now on.
    assert server.app.state.config.brain.providers["ollama"].model == "gemma4:12b-it-qat"


def test_delete_reassigns_the_embedding_slot_through_its_own_writer(
    server: WebServer, fake, writes
) -> None:
    fake.add("embeddinggemma:latest", size=300_000_000, capabilities=("embedding",))
    with TestClient(server.app) as client:
        resp = client.delete(
            f"{BASE}/inventory/qwen3-embedding:4b", params={"reassign": "embeddinggemma"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reassigned"] == ["embedding"]
    assert writes == [("ultrawiki", "embedding_model", "embeddinggemma")]
    assert server.app.state.config.ultrawiki.embedding_model == "embeddinggemma"


def test_delete_rejects_a_replacement_that_is_not_installed(
    server: WebServer, fake, writes
) -> None:
    with TestClient(server.app) as client:
        resp = client.delete(f"{BASE}/inventory/qwen3.5:4b", params={"reassign": "never:pulled"})
        same = client.delete(f"{BASE}/inventory/qwen3.5:4b", params={"reassign": "qwen3.5:4b"})
    assert resp.status_code == 422
    assert same.status_code == 422
    assert writes == []
    assert "qwen3.5:4b" in fake.models


def test_delete_of_an_unused_model_just_deletes(server: WebServer, fake, writes) -> None:
    fake.add("old-thing:7b", size=1)
    with TestClient(server.app) as client:
        resp = client.delete(f"{BASE}/inventory/old-thing:7b")
        gone = client.delete(f"{BASE}/inventory/old-thing:7b")
    assert resp.status_code == 200
    assert resp.json()["reassigned"] == []
    assert writes == []
    assert gone.status_code == 404


def test_destructive_routes_carry_the_danger_flag() -> None:
    flagged = {
        route.path
        for route in local_models_routes.router.routes
        if (getattr(route, "openapi_extra", None) or {}).get("x-jarvis-dangerous")
    }
    assert f"{BASE.replace('ollama', '{provider_id}')}/inventory/{{name:path}}/unload" in flagged
    assert f"{BASE.replace('ollama', '{provider_id}')}/inventory/{{name:path}}" in flagged
