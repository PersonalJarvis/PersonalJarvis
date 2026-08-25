"""Local-models routes through the fake server and monkeypatched modules.

Inventory, unload, delete; roles; per-model options and the suggestion;
catalogue; Hugging Face (off by default); server status and actions.

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

from jarvis.brain import ollama_inventory, ollama_library, ollama_pull, ollama_runtime
from jarvis.core import config_writer
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig, OllamaModelOptions
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


# ═════════════════════════════════════════════════════════════════════════
# Roles
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def shortlist(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    rows = [
        {
            "id": "qwen3.8:27b",
            "role": "chat",
            "vision": True,
            "size_gb": 18.0,
            "installed": False,
            "recommended": True,
            "recommended_for": ["chat", "vision"],
        },
        {
            "id": "ornith:9b",
            "role": "coder",
            "vision": False,
            "size_gb": 5.6,
            "installed": False,
            "recommended": True,
            "recommended_for": ["coder"],
        },
    ]

    async def _recommendations() -> dict:
        return {"models": rows, "curated_reviewed_on": "2026-08-24", "roles": ["chat"]}

    monkeypatch.setattr(ollama_pull, "recommendations", _recommendations)
    return rows


def test_roles_list_every_slot_with_pick_qualifying_and_recommendation(
    server: WebServer, fake, shortlist
) -> None:
    with TestClient(server.app) as client:
        resp = client.get(f"{BASE}/roles")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error"] is None
    roles = {r["id"]: r for r in body["roles"]}
    assert list(roles) == ["chat", "voice", "tools_screen", "deep", "embedding", "ack", "polish"]
    assert roles["chat"]["current"] == "qwen3.5:4b"
    assert roles["chat"]["installed"] is True
    assert roles["chat"]["recommended"] == "qwen3.8:27b"
    assert roles["chat"]["qualifying"] == ["qwen3.5:4b", "gemma4:12b-it-qat"]
    assert roles["tools_screen"]["required"] == ["tools", "vision"]
    assert roles["tools_screen"]["qualifying"] == ["qwen3.5:4b"]
    assert roles["deep"]["recommended"] == "ornith:9b"
    assert roles["embedding"]["qualifying"] == ["qwen3-embedding:4b"]
    assert roles["voice"]["writable"] is True and roles["voice"]["advanced"] is False
    assert roles["ack"]["writable"] is False and roles["ack"]["advanced"] is True
    assert roles["chat"]["label_key"] == "local_models.role_chat"


def test_roles_keep_rendering_when_the_server_is_offline(server, fake, shortlist) -> None:
    fake.offline = True
    with TestClient(server.app) as client:
        body = client.get(f"{BASE}/roles").json()
    assert "did not answer" in body["error"]
    assert len(body["roles"]) == 7
    assert body["roles"][0]["qualifying"] == []


def test_put_role_writes_through_the_config_writer(server, fake, writes) -> None:
    with TestClient(server.app) as client:
        resp = client.put(f"{BASE}/roles/tools_screen", json={"model": "qwen3.5:4b"})
        back = client.put(f"{BASE}/roles/deep", json={"model": ""})
    assert resp.status_code == 200, resp.text
    assert resp.json()["config_key"] == "brain.providers.ollama.tool_model"
    assert "qwen3.5:4b" in resp.json()["message"]
    assert "discovery" in back.json()["message"]
    assert writes == [
        ("brain", "ollama", {"tool_model": "qwen3.5:4b", "cu_model": "qwen3.5:4b"}),
        ("brain", "ollama", {"deep_model": ""}),
    ]
    provider = server.app.state.config.brain.providers["ollama"]
    assert provider.tool_model == "qwen3.5:4b"
    assert provider.deep_model == ""


def test_put_role_refuses_read_only_and_unknown_roles(server, fake, writes) -> None:
    with TestClient(server.app) as client:
        ro = client.put(f"{BASE}/roles/ack", json={"model": "x"})
        unknown = client.put(f"{BASE}/roles/nope", json={"model": "x"})
        empty_embedding = client.put(f"{BASE}/roles/embedding", json={"model": ""})
    assert ro.status_code == 422
    assert unknown.status_code == 404
    assert empty_embedding.status_code == 422
    assert writes == []


# ═════════════════════════════════════════════════════════════════════════
# Options
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def option_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Stand-in for the TOML writer: clamps like the real one, records the call."""
    calls: list[tuple] = []

    def _set(model: str, options: dict, **_kw) -> dict:
        calls.append(("set", model, options))
        return OllamaModelOptions(**options).model_dump(exclude_none=True)

    def _clear(model: str, **_kw) -> bool:
        calls.append(("clear", model))
        return True

    monkeypatch.setattr(config_writer, "set_ollama_model_options", _set)
    monkeypatch.setattr(config_writer, "clear_ollama_model_options", _clear)
    return calls


def test_options_round_trip_through_the_writer_and_the_live_config(
    server, fake, option_writes
) -> None:
    with TestClient(server.app) as client:
        before = client.get(f"{BASE}/models/qwen3.5:4b/options").json()
        put = client.put(
            f"{BASE}/models/qwen3.5:4b/options",
            json={"num_ctx": 16384, "keep_alive": "30m", "think": False, "stop": "###"},
        )
        after = client.get(f"{BASE}/models/qwen3.5:4b/options").json()
        reset = client.delete(f"{BASE}/models/qwen3.5:4b/options").json()
        gone = client.get(f"{BASE}/models/qwen3.5:4b/options").json()
    assert before == {
        "model": "qwen3.5:4b",
        "options": {},
        "configured": False,
        "profile_alias": None,
    }
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["configured"] is True
    assert body["options"] == {
        "num_ctx": 16384,
        "stop": ["###"],
        "keep_alive": "30m",
        "think": False,
    }
    assert body["profile_alias"].startswith("qwen3.5-4b-jarvis-")
    assert after == body
    assert reset["configured"] is False
    assert gone["options"] == {}
    assert option_writes == [
        (
            "set",
            "qwen3.5:4b",
            {"num_ctx": 16384, "stop": "###", "keep_alive": "30m", "think": False},
        ),
        ("clear", "qwen3.5:4b"),
    ]
    assert "qwen3.5:4b" not in server.app.state.config.brain.providers["ollama"].models


def test_options_without_a_bakeable_knob_have_no_alias(server, fake, option_writes) -> None:
    with TestClient(server.app) as client:
        body = client.put(f"{BASE}/models/qwen3.5:4b/options", json={"temperature": 0.2}).json()
    assert body["profile_alias"] is None
    assert body["options"] == {"temperature": 0.2}


def test_suggested_options_are_judged_against_this_machine(server, fake, monkeypatch) -> None:
    monkeypatch.setattr(ollama_pull, "accelerator_gb", lambda: (16.0, "nvidia-smi"))
    monkeypatch.setattr(ollama_pull, "total_memory_gb", lambda: 32.0)
    with TestClient(server.app) as client:
        resp = client.get(f"{BASE}/models/qwen3.5:4b/suggested-options")
        missing = client.get(f"{BASE}/models/ghost:7b/suggested-options")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accelerator_gb"] == 16.0
    assert body["accelerator_source"] == "nvidia-smi"
    assert body["ram_gb"] == 32.0
    assert body["native_context"] == 262_144
    assert body["options"]["num_gpu"] == -1
    assert body["options"]["num_ctx"] >= 4096
    assert any("num_ctx" in r for r in body["reasons"])
    assert missing.status_code == 404


# ═════════════════════════════════════════════════════════════════════════
# Catalogue
# ═════════════════════════════════════════════════════════════════════════


def test_catalog_forwards_sort_and_capability(server, fake, monkeypatch) -> None:
    seen: dict = {}

    async def _search(query: str, **kw) -> dict:
        seen.update({"query": query, **kw})
        return {
            "query": query,
            "sort": kw["sort"],
            "capability": kw["capability"],
            "models": [],
            "error": None,
        }

    async def _tags(model: str) -> dict:
        return {"model": model, "tags": [{"tag": "4b", "quantization": ""}], "error": None}

    monkeypatch.setattr(ollama_library, "search_library", _search)
    monkeypatch.setattr(ollama_library, "library_tags", _tags)
    with TestClient(server.app) as client:
        resp = client.get(
            f"{BASE}/catalog", params={"q": "qwen", "sort": "newest", "capability": "tools"}
        )
        tags = client.get(f"{BASE}/catalog/huihui_ai/qwen3-abliterated/tags")
    assert resp.status_code == 200
    assert seen == {"query": "qwen", "sort": "newest", "capability": "tools", "limit": 50}
    assert tags.json()["model"] == "huihui_ai/qwen3-abliterated"


def test_catalog_recommended_carries_the_review_date(server, fake, shortlist) -> None:
    with TestClient(server.app) as client:
        body = client.get(f"{BASE}/catalog/recommended").json()
    assert body["curated_reviewed_on"] == "2026-08-24"
    assert body["models"][0]["recommended_for"] == ["chat", "vision"]


# ═════════════════════════════════════════════════════════════════════════
# Hugging Face
# ═════════════════════════════════════════════════════════════════════════


def test_hf_routes_answer_404_with_a_sentence_while_switched_off(server, fake) -> None:
    with TestClient(server.app) as client:
        enabled = client.get(f"{BASE}/hf/enabled").json()
        search = client.get(f"{BASE}/hf/search", params={"q": "qwen"})
        files = client.get(f"{BASE}/hf/unsloth/Qwen3.8-27B-GGUF/files")
        pull = client.post(f"{BASE}/hf/pull", json={"user": "unsloth", "repo": "x"})
    assert enabled == {"enabled": False}
    for resp in (search, files, pull):
        assert resp.status_code == 404
        assert "switched off" in resp.json()["detail"]


def test_hf_switch_persists_and_opens_the_routes(server, fake, monkeypatch) -> None:
    from jarvis.brain import hf_gguf

    flags: list[bool] = []
    pulls: list[str] = []
    monkeypatch.setattr(config_writer, "set_ollama_hf_enabled", lambda v, **_kw: flags.append(v))

    async def _search(q: str, *, sort: str = "downloads", limit: int = 30) -> dict:
        return {"repos": [{"id": f"{q}/{sort}/{limit}"}], "error": None}

    async def _files(user: str, repo: str) -> dict:
        return {"files": [{"filename": f"{user}-{repo}.gguf", "quant": "Q4_K_M"}], "error": None}

    async def _start_pull(model: str) -> dict:
        pulls.append(model)
        return {"state": "running", "model": model, "message": "Starting"}

    monkeypatch.setattr(hf_gguf, "search", _search)
    monkeypatch.setattr(hf_gguf, "files", _files)
    monkeypatch.setattr(ollama_pull, "start_pull", _start_pull)
    with TestClient(server.app) as client:
        on = client.put(f"{BASE}/hf/enabled", json={"enabled": True})
        search = client.get(
            f"{BASE}/hf/search", params={"q": "qwen", "sort": "lastModified", "limit": 5}
        )
        files = client.get(f"{BASE}/hf/unsloth/Qwen3.8-27B-GGUF/files")
        pull = client.post(
            f"{BASE}/hf/pull",
            json={"user": "unsloth", "repo": "Qwen3.8-27B-GGUF", "quant": "Q4_K_M"},
        )
        bad = client.post(f"{BASE}/hf/pull", json={"user": "../etc", "repo": "x"})
    assert on.json() == {"enabled": True}
    assert flags == [True]
    assert server.app.state.config.brain.providers["ollama"].hf_enabled is True
    assert search.json()["repos"] == [{"id": "qwen/lastModified/5"}]
    assert files.json()["files"][0]["filename"] == "unsloth-Qwen3.8-27B-GGUF.gguf"
    assert pull.json()["state"] == "running"
    assert pulls == ["hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M"]
    assert bad.status_code == 400


# ═════════════════════════════════════════════════════════════════════════
# Server
# ═════════════════════════════════════════════════════════════════════════


def _status(running: bool) -> dict:
    return {
        "installed": True,
        "binary": "/usr/local/bin/ollama",
        "running": running,
        "version": "0.32.15" if running else "",
        "detail": "Ollama is running (version 0.32.15)."
        if running
        else "Ollama is installed but not running.",
        "base_url": ROOT,
        "host_kind": "local",
        "models_dir": "/home/me/.ollama/models",
    }


def test_server_status_pairs_the_runtime_with_what_is_loaded(server, fake, monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "runtime_status", lambda: _status(True))
    fake.load("qwen3.5:4b", size_vram=3_000_000_000)
    with TestClient(server.app) as client:
        body = client.get(f"{BASE}/server").json()
    assert body["running"] is True
    assert body["version"] == "0.32.15"
    assert body["host_kind"] == "local"
    assert body["models_dir"].endswith("models")
    assert [r["name"] for r in body["running_models"]] == ["qwen3.5:4b"]
    assert body["loaded_vram_bytes"] == 3_000_000_000
    assert body["disk_bytes"] == 3_400_000_000 + 7_200_000_000 + 2_500_000_000
    assert body["error"] is None


def test_server_status_skips_the_inventory_when_stopped(server, fake, monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "runtime_status", lambda: _status(False))
    with TestClient(server.app) as client:
        body = client.get(f"{BASE}/server").json()
    assert body["running"] is False
    assert body["running_models"] == []
    assert body["disk_bytes"] == 0
    assert fake.calls == []


def test_server_stop_test_log_and_env_guide(server, fake, monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_runtime, "stop_server", lambda: (False, "This Ollama was not started by Jarvis.")
    )
    monkeypatch.setattr(
        ollama_runtime, "tail_log", lambda lines=40: [f"line {i}" for i in range(lines)]
    )

    async def _probe(base_url: str, **_kw) -> dict:
        return {
            "ok": True,
            "version": "0.32.15",
            "latency_ms": 12,
            "detail": f"Ollama answered at {base_url}.",
        }

    monkeypatch.setattr(ollama_runtime, "probe_host", _probe)
    with TestClient(server.app) as client:
        stop = client.post(f"{BASE}/server/stop").json()
        probe = client.post(f"{BASE}/server/test", json={"base_url": "gpu-box:11434"}).json()
        log_ = client.get(f"{BASE}/server/log", params={"lines": 3}).json()
        guide = client.get(f"{BASE}/server/env-guide", params={"os": "macos"}).json()
        own = client.get(f"{BASE}/server/env-guide").json()
    assert stop == {"ok": False, "message": "This Ollama was not started by Jarvis."}
    assert probe["ok"] is True and probe["latency_ms"] == 12 and "gpu-box" in probe["detail"]
    assert log_["lines"] == ["line 0", "line 1", "line 2"]
    assert guide["os"] == "macos"
    assert [r["key"] for r in guide["rows"]][:2] == ["OLLAMA_HOST", "OLLAMA_MODELS"]
    assert all(r["command"].startswith("launchctl setenv") for r in guide["rows"])
    assert own["os"] in {"windows", "macos", "linux"}


def test_server_stop_carries_the_danger_flag() -> None:
    flagged = {
        route.path
        for route in local_models_routes.router.routes
        if (getattr(route, "openapi_extra", None) or {}).get("x-jarvis-dangerous")
    }
    assert f"{BASE.replace('ollama', '{provider_id}')}/server/stop" in flagged
