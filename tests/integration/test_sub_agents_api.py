"""Integration tests for the Sub-Agent dashboard REST API.

Covers:
- GET /api/sub-agents/tree returns an empty structure when there are no events.
- Events on the bus land in the tree endpoint.
- GET /api/sub-agents/{trace_id} returns a single node.
- 404 for an unknown trace_id.
- 503 fallback when the registry is missing.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from jarvis.agents import JarvisAgentRegistry
from jarvis.core.bus import EventBus
from jarvis.core.config import load_config
from jarvis.core.events import (
    JarvisAgentTaskCompleted,
    JarvisAgentTaskStarted,
)
from jarvis.ui.web.server import WebServer


@pytest.fixture
def server_bus() -> tuple[TestClient, EventBus, JarvisAgentRegistry]:
    """Builds a real WebServer + FastAPI + registry.

    The registry hangs directly off the bus, so bus.publish events
    flow through into the /api/sub-agents/tree response.
    """
    bus = EventBus()
    ws = WebServer(bus=bus, cfg=load_config())
    client = TestClient(ws.app)
    registry = ws.app.state.sub_agent_registry
    assert registry is not None, "JarvisAgentRegistry was not attached"
    return client, bus, registry


def test_tree_empty_initially(server_bus) -> None:
    client, _, _ = server_bus
    resp = client.get("/api/sub-agents/tree")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 0
    assert payload["roots"] == []
    assert payload["all"] == {}
    assert payload["server_ts_ns"] > 0


@pytest.mark.asyncio
async def test_tree_reflects_published_events(server_bus) -> None:
    client, bus, _ = server_bus
    tid = uuid4()
    await bus.publish(
        JarvisAgentTaskStarted(
            trace_id=tid,
            utterance="bau mir X",
            provider="jarvis-agent",
            model="opus",
        )
    )
    resp = client.get("/api/sub-agents/tree")
    data = resp.json()
    assert data["count"] == 1
    assert len(data["roots"]) == 1
    root = data["roots"][0]
    assert root["kind"] == "jarvis_agent"
    assert root["utterance"] == "bau mir X"
    assert root["model"] == "opus"


@pytest.mark.asyncio
async def test_get_agent_by_trace_id(server_bus) -> None:
    client, bus, _ = server_bus
    tid = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=tid, utterance="test"))

    # Akzeptiert hex-form
    resp = client.get(f"/api/sub-agents/{tid.hex}")
    assert resp.status_code == 200
    assert resp.json()["utterance"] == "test"

    # Akzeptiert auch dashed UUID
    resp2 = client.get(f"/api/sub-agents/{tid}")
    assert resp2.status_code == 200
    assert resp2.json()["trace_id"] == tid.hex


def test_get_agent_404_on_unknown(server_bus) -> None:
    client, _, _ = server_bus
    resp = client.get("/api/sub-agents/deadbeefdeadbeefdeadbeefdeadbeef")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tree_lifecycle_running_to_completed(server_bus) -> None:
    client, bus, _ = server_bus
    tid = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=tid))
    r1 = client.get("/api/sub-agents/tree").json()
    assert r1["roots"][0]["status"] == "running"

    await bus.publish(
        JarvisAgentTaskCompleted(
            trace_id=tid,
            success=True,
            summary="done",
            duration_s=5.5,
            cost_estimate_usd=0.01,
        )
    )
    r2 = client.get("/api/sub-agents/tree").json()
    assert r2["roots"][0]["status"] == "completed"
    assert r2["roots"][0]["duration_ms"] == pytest.approx(5500.0)


def test_tree_fallback_when_registry_missing() -> None:
    """If registry is None (import error), /tree returns an empty OK payload."""
    from fastapi import FastAPI

    from jarvis.ui.web.sub_agents_routes import router as sub_agents_router

    app = FastAPI()
    app.include_router(sub_agents_router)
    app.state.sub_agent_registry = None

    client = TestClient(app)
    resp = client.get("/api/sub-agents/tree")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 0
    assert payload["server_ts_ns"] == 0


def test_detail_503_when_registry_missing() -> None:
    from fastapi import FastAPI

    from jarvis.ui.web.sub_agents_routes import router as sub_agents_router

    app = FastAPI()
    app.include_router(sub_agents_router)
    app.state.sub_agent_registry = None

    client = TestClient(app)
    resp = client.get("/api/sub-agents/anything")
    assert resp.status_code == 503
