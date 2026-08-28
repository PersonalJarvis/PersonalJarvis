"""Parity for the overview wire contract (five-layer pattern, AP-4).

``OverviewResponse`` in ``local_models_routes.py`` (the Python half) and the
TS interface of the same name in ``src/hooks/useLocalModels.ts`` must carry
exactly the same six fields, and the route must actually answer them from
the disk snapshot when the server is silent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.brain import ollama_inventory, ollama_overview, ollama_pull, ollama_runtime
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.ui.web import local_models_routes
from jarvis.ui.web.local_models_routes import OverviewResponse
from jarvis.ui.web.server import WebServer
from tests.fakes.fake_ollama_server import FakeOllamaServer

REPO_ROOT = Path(__file__).resolve().parents[4]
TS_FILE = REPO_ROOT / "jarvis/ui/web/frontend/src/hooks/useLocalModels.ts"
ROOT = "http://localhost:11434"
BASE = "/api/providers/ollama/local-models"

EXPECTED = ("server", "roles", "inventory", "recommended", "source", "fetched_at")


def _ts_interface_keys(name: str) -> list[str] | None:
    src = TS_FILE.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name}\s*\{{([^}}]*)\}}", src)
    if not match:
        return None
    return re.findall(r"^\s*([a-z_]+)\??:", match.group(1), flags=re.MULTILINE)


def test_pydantic_fields_are_the_six_the_frontend_builds_against() -> None:
    assert tuple(OverviewResponse.model_fields) == EXPECTED


def test_typescript_mirror_matches_the_pydantic_model() -> None:
    keys = _ts_interface_keys("OverviewResponse")
    if keys is None:
        pytest.skip(
            "interface OverviewResponse is not in useLocalModels.ts yet — the frontend "
            "chunk (A4/A5) adds it; this test then compares the six fields."
        )
    assert keys == list(OverviewResponse.model_fields)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeOllamaServer:
    ollama_inventory._reset_for_tests()
    ollama_overview._reset_for_tests()
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    server = FakeOllamaServer()
    server.add("qwen3.5:4b", size=3_400_000_000, capabilities=("completion", "tools", "vision"))

    def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=server.transport())

    monkeypatch.setattr(ollama_inventory, "_make_client", _client)
    monkeypatch.setattr(local_models_routes, "_server_root", lambda: ROOT)
    monkeypatch.setattr(
        ollama_runtime,
        "runtime_status",
        lambda: {
            "installed": True,
            "binary": "ollama",
            "running": not server.offline,
            "version": "0.32.15",
            "detail": "Ollama is running.",
            "base_url": ROOT,
            "host_kind": "local",
            "models_dir": "",
        },
    )

    async def _recommendations() -> dict:
        return {"models": [], "curated_reviewed_on": "2026-08-24"}

    monkeypatch.setattr(ollama_pull, "recommendations", _recommendations)
    yield server
    ollama_inventory._reset_for_tests()
    ollama_overview._reset_for_tests()


@pytest.fixture
def server(tmp_path, monkeypatch: pytest.MonkeyPatch) -> WebServer:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    cfg.brain.providers["ollama"] = BrainProviderConfig(model="qwen3.5:4b")
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    return srv


def test_the_route_answers_live_then_the_cache(server: WebServer, fake, tmp_path: Path) -> None:
    with TestClient(server.app) as client:
        live = client.get(f"{BASE}/overview")
        assert live.status_code == 200, live.text
        body = live.json()
        assert set(body) == set(EXPECTED)
        assert body["source"] == "live"
        assert body["server"]["running"] is True
        assert body["inventory"]["disk_bytes"] == 3_400_000_000
        assert body["roles"]["roles"][0]["id"] == "chat"
        assert body["recommended"]["curated_reviewed_on"] == "2026-08-24"
        assert body["fetched_at"] > 0
        # Each part is exactly what the single endpoint answers.
        assert body["inventory"] == client.get(f"{BASE}/inventory").json()
        assert body["roles"] == client.get(f"{BASE}/roles").json()
        assert body["server"] == client.get(f"{BASE}/server").json()
    assert (tmp_path / ollama_overview.SNAPSHOT_FILE_NAME).exists()

    # A new process with a silent server: the snapshot paints first.
    ollama_overview._reset_for_tests()
    ollama_inventory._reset_for_tests()
    fake.offline = True
    with TestClient(server.app) as client:
        cached = client.get(f"{BASE}/overview").json()
        assert cached["source"] == "cache"
        assert cached["inventory"]["disk_bytes"] == 3_400_000_000
        forced = client.get(f"{BASE}/overview", params={"fresh": 1}).json()
        assert forced["source"] == "live"
        assert forced["server"]["running"] is False
        assert "did not answer" in forced["inventory"]["error"]
    # The offline sweep is a truthful answer NOW but a ruinous head start
    # later, so `is_paintable` keeps it off disk: the last honest snapshot
    # survives the outage that would otherwise wipe it (BUG-188).
    saved = json.loads((tmp_path / ollama_overview.SNAPSHOT_FILE_NAME).read_text("utf-8"))
    assert saved["payload"]["server"]["running"] is True
    assert saved["payload"]["inventory"]["disk_bytes"] == 3_400_000_000


def test_the_overview_refuses_cards_without_a_pull_api(server: WebServer, fake) -> None:
    with TestClient(server.app) as client:
        assert client.get("/api/providers/openai/local-models/overview").status_code == 400
