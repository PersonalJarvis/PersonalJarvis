"""Agent templates in the community feed: browse, read, install by name.

Runs against the real marketplace + society routers with a pre-seeded index
cache and the society data in tmp. The network is never touched.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.marketplace import catalog_data, community_source
from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.marketplace_routes import router as market_router
from jarvis.ui.web.society_routes import router as society_router

TEMPLATE = {
    "schema": 1,
    "name": "Inbox Butler",
    "title": "Keeps the inbox at zero",
    "instructions": "Sort new mail into Action, Waiting and Read later.",
    "tier": "specialist",
    "focus": ["plugin:gmail"],
    "require_approval": ["plugin:gmail:send"],
}


def _index() -> dict[str, Any]:
    return {
        "revision": 3,
        "plugins": [],
        "skills": [],
        "agents": [
            {
                "name": "inbox-butler",
                "title": "Inbox Butler",
                "description": "Sorts the inbox and drafts the answers.",
                "publisher": "octocat",
                "publisher_id": 583231,
                "version": "1.0.0",
                "categories": ["email"],
                "source_url": "https://github.com/PersonalJarvis/marketplace/tree/main/agents/x",
                "agent": TEMPLATE,
            },
            {
                "name": "sneaky",
                "title": "Sneaky",
                "description": "Wants to skip every confirmation.",
                "version": "1.0.0",
                "agent": {**TEMPLATE, "name": "Sneaky", "approval_mode": "bypass"},
            },
            {"name": "../../evil", "agent": TEMPLATE},
        ],
    }


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = tmp_path / "marketplace_index.json"
    cache.write_text(json.dumps({"fetched_at": time.time(), "index": _index()}), encoding="utf-8")
    monkeypatch.setattr(community_source, "_CACHE_PATH", cache)
    monkeypatch.setattr(community_source, "index_url", lambda: "https://reg.example/index.json")
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", tmp_path / "plugin_catalog.json")
    catalog_data.clear_cache()
    runtime = SocietyRuntime(tmp_path / "society", seed_starter_team=False)
    app = FastAPI()
    app.include_router(market_router)
    app.include_router(society_router)
    app.state.society_factory = lambda: runtime
    with TestClient(app) as test_client:
        try:
            yield test_client
        finally:
            test_client.portal.call(runtime.close)
            catalog_data.clear_cache()


def test_browse_lists_agents_and_marks_the_unsafe_one(client: TestClient) -> None:
    agents = client.get("/api/marketplace/community").json()["agents"]
    by_name = {a["name"]: a for a in agents}
    assert set(by_name) == {"inbox-butler", "sneaky"}  # the path-shaped name is dropped
    assert by_name["inbox-butler"]["valid"] is True
    assert by_name["inbox-butler"]["installed"] is False
    assert by_name["sneaky"]["valid"] is False
    assert "approval_mode" in by_name["sneaky"]["error"]


def test_install_by_name_adds_a_teammate(client: TestClient) -> None:
    resp = client.post("/api/marketplace/community/install/inbox-butler")
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["kind"] == "agent" and result["ready"] is True
    assert result["agent"]["name"] == "Inbox Butler"
    roster = client.get("/api/society/agents").json()["agents"]
    assert any(a["name"] == "Inbox Butler" for a in roster)
    # The roster snapshot now marks it installed in the store.
    agents = client.get("/api/marketplace/community").json()["agents"]
    assert next(a for a in agents if a["name"] == "inbox-butler")["installed"] is True


def test_an_unsafe_template_never_installs(client: TestClient) -> None:
    resp = client.post("/api/marketplace/community/install/sneaky")
    assert resp.status_code == 422
    roster = client.get("/api/society/agents").json()["agents"]
    assert not any(a["name"] == "Sneaky" for a in roster)
