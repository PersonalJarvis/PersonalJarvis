"""``/api/setup`` — starter plans and the readiness note."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from jarvis.core import config as cfg_mod
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web import provider_routes, starter_plan_routes
from jarvis.ui.web.server import WebServer


@pytest.fixture
def state_path(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(starter_plan_routes, "_STATE_PATH_OVERRIDE", tmp_path / "setup_state.json")
    yield


@pytest.fixture
def secrets(monkeypatch) -> dict[str, str]:
    data: dict[str, str] = {}
    monkeypatch.setattr(cfg_mod, "get_secret", lambda key, env_fallback=None: data.get(key))
    return data


@pytest.fixture
def server() -> WebServer:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.cfg = cfg
    srv.app.state.config = cfg
    return srv


class _Snapshot:
    def __init__(self, sections: dict[str, dict[str, str]]) -> None:
        self.sections = sections


def _all_ok(names: tuple[str, ...]) -> _Snapshot:
    return _Snapshot({n: {"status": "ok", "reason": "ok", "detail": ""} for n in names})


def test_starter_plans_report_which_keys_exist(server, state_path, secrets) -> None:
    secrets["gemini_api_key"] = "AIza-1"
    with TestClient(server.app) as client:
        body = client.get("/api/setup/starter-plans").json()
    plans = {p["id"]: p for p in body["plans"]}
    assert plans["gemini-pipeline"]["recommended"] is True
    assert plans["gemini-pipeline"]["keys_complete"] is True
    assert plans["gemini-pipeline"]["key_slots"][0]["slot"] == "gemini_api_key"
    two = plans["gemini-openai-realtime"]
    assert two["keys_complete"] is False
    assert [s["present"] for s in two["key_slots"]] == [True, False]
    assert body["selected"] is None


def test_select_starter_plan_persists_and_rejects_unknown(server, state_path) -> None:
    with TestClient(server.app) as client:
        assert client.post("/api/setup/starter-plans/nope").status_code == 404
        assert client.post("/api/setup/starter-plans/gemini-pipeline").json()["selected"] == (
            "gemini-pipeline"
        )
        assert client.post("/api/setup/starter-plans/custom").status_code == 200
        assert client.get("/api/setup/starter-plans").json()["selected"] == "custom"


def test_readiness_follows_the_active_mode_and_celebrates_once(
    server, state_path, monkeypatch
) -> None:
    server.cfg.voice.mode = "pipeline"

    async def fake_health(request, refresh=False):
        return _all_ok(("brain", "computer-use", "tts", "stt", "realtime", "subagents"))

    monkeypatch.setattr(provider_routes, "section_health", fake_health)
    with TestClient(server.app) as client:
        body = client.get("/api/setup/readiness").json()
        assert body["mode"] == "pipeline"
        assert body["required"] == ["brain", "computer-use", "tts", "stt"]
        assert body["ready"] is True and body["celebrated"] is False
        assert client.post("/api/setup/readiness/celebrated").json()["ok"] is True
        assert client.get("/api/setup/readiness").json()["celebrated"] is True


def test_readiness_is_false_when_a_required_section_is_not_ok(
    server, state_path, monkeypatch
) -> None:
    server.cfg.voice.mode = "realtime"

    async def fake_health(request, refresh=False):
        snap = _all_ok(("realtime", "computer-use"))
        snap.sections["subagents"] = {"status": "needs_setup", "reason": "x", "detail": ""}
        return snap

    monkeypatch.setattr(provider_routes, "section_health", fake_health)
    with TestClient(server.app) as client:
        body = client.get("/api/setup/readiness").json()
    assert body["required"] == ["realtime", "computer-use", "subagents"]
    assert body["ready"] is False
    assert body["sections"]["subagents"]["status"] == "needs_setup"


def test_readiness_fails_open_when_health_probe_breaks(server, state_path, monkeypatch) -> None:
    async def boom(request, refresh=False):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(provider_routes, "section_health", boom)
    with TestClient(server.app) as client:
        resp = client.get("/api/setup/readiness")
    assert resp.status_code == 200
    assert resp.json()["ready"] is False
