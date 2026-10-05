"""REST room creation cannot forge internal lead or teammate provenance."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import router


@pytest.fixture
async def rooms(tmp_path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    app = FastAPI()
    app.include_router(router)
    app.state.society = None
    app.state.society_factory = lambda: runtime
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            for name in ("Scout", "Archivist"):
                created = await client.post("/api/society/agents", json={"name": name})
                assert created.status_code == 200, created.text
            yield client, runtime
    finally:
        await runtime.close()


@pytest.mark.parametrize("opened_by", ["jarvis", "scout", "scheduler"])
async def test_rest_room_rejects_internal_sender_before_persistence(rooms, opened_by):
    client, runtime = rooms
    before = await runtime.store.last_seq()
    response = await client.post("/api/society/rooms", json={
        "members": ["scout", "archivist"], "topic": "Discuss a change.",
        "opened_by": opened_by, "live": True,
    })
    assert response.status_code == 422, response.text
    assert await runtime.rooms.list() == []
    assert await runtime.store.last_seq() == before
    assert runtime.scheduler.running == {}


async def test_rest_user_provenance_and_internal_lead_path_remain_distinct(rooms):
    client, runtime = rooms
    response = await client.post("/api/society/rooms", json={
        "members": ["scout", "archivist"], "topic": "Discuss a change.",
    })
    assert response.status_code == 200, response.text
    public = response.json()["room"]
    assert public["opened_by"] == "user"
    events = await runtime.store.events_for_trace(public["trace_id"])
    assert events[0].from_agent == "user"

    internal = await runtime.rooms.open(
        opened_by="jarvis", members=["scout", "archivist"], topic="Trusted delegation.",
    )
    assert internal.opened_by == "jarvis"
    events = await runtime.store.events_for_trace(internal.trace_id)
    assert events[0].from_agent == "jarvis"
