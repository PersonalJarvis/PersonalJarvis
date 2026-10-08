"""Mounted sandbox routes, placement changes and binary export contracts."""
from __future__ import annotations

import base64
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from jarvis.society import sandbox
from jarvis.ui.web.society_routes import router

pytest_plugins = ["tests.unit.society.test_surface"]


async def test_persisted_environment_route_and_binary_export(rt, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.state.society = rt

    class Files:
        async def file(self, action, path):
            assert path == "result.txt"
            assert action == "download"
            return 0, base64.b64encode(b"verified artifact")

    monkeypatch.setattr(sandbox, "for_agent", lambda runtime, agent_id: Files())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test",
    ) as client:
        response = await client.post("/api/society/agents", json={
            "name": "Export Box", "provider": "openai", "execution_environment": "sandbox",
        })
        assert response.status_code == 200, response.text
        agent_id = response.json()["agent"]["agent_id"]
        assert response.json()["agent"]["execution_environment"] == "sandbox"
        response = await client.get(
            f"/api/society/agents/{agent_id}/sandbox/file", params={"path": "result.txt"},
        )
        assert response.content == b"verified artifact"
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in response.headers["content-disposition"]
        response = await client.patch(
            f"/api/society/agents/{agent_id}", json={"execution_environment": "local"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["agent"]["execution_environment"] == "local"
        assert (await client.get(
            f"/api/society/agents/{agent_id}/sandbox/file", params={"path": "result.txt"},
        )).status_code == 409


async def test_active_agent_cannot_change_execution_environment(rt):
    agent, _ = await rt.roster.create(name="Busy", provider="openai")
    app = FastAPI()
    app.include_router(router)
    app.state.society = rt
    rt._get_chat = lambda: SimpleNamespace(running_session_ids=lambda: [agent.session_id])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test",
    ) as client:
        response = await client.patch(
            f"/api/society/agents/{agent.agent_id}", json={"execution_environment": "sandbox"},
        )
        assert response.status_code == 409, response.text
        assert "Stop" in response.json()["detail"]
    assert (await rt.roster.get(agent.agent_id)).execution_environment == "local"


async def test_existing_database_migrates_without_changing_agents(tmp_path):
    import asyncio
    import sqlite3
    from pathlib import Path

    from jarvis.society.roster import Roster
    from jarvis.society.store import SocietyStore

    database = tmp_path / "old.db"
    source = await asyncio.to_thread(
        Path("jarvis/society/society_schema.sql").read_text, encoding="utf-8",
    )
    source = source.replace(
        "    execution_environment TEXT NOT NULL DEFAULT 'local'\n"
        "                        CHECK (execution_environment IN ('local', 'sandbox')),\n", "",
    )
    with sqlite3.connect(database) as conn:
        conn.executescript(source)
        conn.execute(
            "INSERT INTO society_agents (agent_id,name,tier,created_ms,updated_ms) "
            "VALUES ('existing','Existing','specialist',1,1)"
        )
    store = SocietyStore(database)
    await store.open()
    try:
        agent = await Roster(store).get("existing")
        assert agent.name == "Existing"
        assert agent.execution_environment == "local"
    finally:
        await store.close()
