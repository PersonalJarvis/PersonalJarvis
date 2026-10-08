"""A remote ownership receipt must prevent a second local execution."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.society.cloud_admission import assert_local_owner
from jarvis.ui.web.society_cloud_proxy import SocietyCloudProxy, cloud_agent_path


@pytest.mark.parametrize("state", ["preparing", "ready", "activating", "active", "uncertain"])
def test_every_cloud_receipt_fences_local_execution(monkeypatch, tmp_path, state):
    monkeypatch.setattr("jarvis.society.cloud_host.placement_for", lambda *_: {"state": state})
    runtime = SimpleNamespace(store=SimpleNamespace(path=tmp_path / "society.db"))
    with pytest.raises(PermissionError, match="Local execution|local execution"):
        assert_local_owner(runtime, "agent-a")


def test_unplaced_agent_keeps_local_execution(monkeypatch, tmp_path):
    monkeypatch.setattr("jarvis.society.cloud_host.placement_for", lambda *_: None)
    assert_local_owner(SimpleNamespace(store=SimpleNamespace(path=tmp_path / "society.db")), "a")


@pytest.mark.parametrize(
    "path, owner",
    [
        ("/api/agent-chat/sessions/society:agent-a/ws", "agent-a"),
        ("/api/agent-chat/sessions/society:agent-a:routine:task:run/messages", "agent-a"),
        ("/api/society/agents/agent-a/routines", "agent-a"),
        ("/api/society-cloud/agents/agent-a/handoff", None),
        ("/api/society/agents", None),
        ("/api/agent-chat/sessions/unrelated/messages", None),
    ],
)
def test_only_owner_scoped_routes_are_proxied(path, owner):
    assert cloud_agent_path(path) == owner


@pytest.mark.asyncio
async def test_uncertain_handoff_never_falls_through_to_local_route(monkeypatch, tmp_path):
    import httpx
    from fastapi import FastAPI

    local_calls = []
    app = FastAPI()

    @app.post("/api/agent-chat/sessions/society:agent-a/messages")
    async def local_send():
        local_calls.append(True)
        return {"started": True}

    app.add_middleware(SocietyCloudProxy, data_dir=str(tmp_path))
    monkeypatch.setattr(
        "jarvis.society.cloud_host.placement_for", lambda *_: {"state": "uncertain"}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/api/agent-chat/sessions/society:agent-a/messages", json={"text": "go"}
        )
    assert result.status_code == 409
    assert local_calls == []


@pytest.mark.asyncio
async def test_failed_cloud_send_is_not_retried_or_executed_locally(monkeypatch, tmp_path):
    import httpx
    from fastapi import FastAPI

    calls = []
    local_calls = []
    app = FastAPI()

    @app.post("/api/agent-chat/sessions/society:agent-a/messages")
    async def local_send():
        local_calls.append(True)
        return {}

    class Host:
        def __init__(self, runtime):
            pass

        async def request(self, *args):
            calls.append(args)
            raise OSError("connection ended after sending")

    async def runtime(_):
        return SimpleNamespace(store=SimpleNamespace(path=Path(tmp_path) / "society.db"))

    monkeypatch.setattr("jarvis.society.cloud_host.placement_for", lambda *_: {"state": "active"})
    monkeypatch.setattr("jarvis.society.cloud_host.CloudHost", Host)
    monkeypatch.setattr("jarvis.ui.web.society_routes._runtime", runtime)
    app.add_middleware(SocietyCloudProxy, data_dir=str(tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/api/agent-chat/sessions/society:agent-a/messages", json={"text": "go"}
        )
    assert result.status_code == 502
    assert len(calls) == 1
    assert local_calls == []


@pytest.mark.asyncio
async def test_cloud_history_keeps_remote_status_and_query(monkeypatch, tmp_path):
    import httpx
    from fastapi import FastAPI

    calls = []

    class Host:
        def __init__(self, runtime):
            pass

        async def request(self, *args):
            calls.append(args)
            return 200, {"session": {"running": True}, "events": [{"seq": 71}]}

    async def runtime(_):
        return object()

    app = FastAPI()
    app.add_middleware(SocietyCloudProxy, data_dir=str(tmp_path))
    monkeypatch.setattr("jarvis.society.cloud_host.placement_for", lambda *_: {"state": "active"})
    monkeypatch.setattr("jarvis.society.cloud_host.CloudHost", Host)
    monkeypatch.setattr("jarvis.ui.web.society_routes._runtime", runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get("/api/agent-chat/sessions/society:agent-a?tail=10")
    assert result.json()["events"] == [{"seq": 71}]
    assert calls[0][2].endswith("?tail=10")


@pytest.mark.asyncio
async def test_global_stop_reports_each_unreachable_owner_without_retry(monkeypatch, tmp_path):
    from jarvis.society.cloud_controls import relay_kill_switch

    calls = []

    class Host:
        def __init__(self, runtime):
            pass

        def placement(self, agent_id):
            return {"agent_id": agent_id, "state": "active"}

        async def request(self, agent_id, method, path, body):
            calls.append((agent_id, method, path))
            if agent_id == "unreachable":
                raise OSError("offline")
            return 200, {"engaged": True}

    monkeypatch.setattr("jarvis.society.cloud_host.CloudHost", Host)
    monkeypatch.setattr(
        "jarvis.society.cloud_host.placements_for",
        lambda _: [
            {"agent_id": "available"},
            {"agent_id": "unreachable"},
        ],
    )
    runtime = SimpleNamespace(store=SimpleNamespace(path=tmp_path / "society.db"))
    assert await relay_kill_switch(runtime) == ["unreachable"]
    assert sorted(calls) == [
        ("available", "POST", "/api/society/kill-switch"),
        ("unreachable", "POST", "/api/society/kill-switch"),
    ]


@pytest.mark.asyncio
async def test_pending_cloud_review_does_not_consume_retry_budget(monkeypatch, tmp_path):
    from jarvis.society.review_queue import _drain_once, _schedule_wake

    class Archive:
        def pending_reviews(self):
            return [{"session": "society:agent-a", "turn_id": "turn", "retry_after_ms": 0}]

        def fail_review(self, *args, **kwargs):
            pytest.fail("A remote review must not consume local retry budget")

    monkeypatch.setattr("jarvis.society.cloud_host.placement_for", lambda *_: {"state": "active"})
    runtime = SimpleNamespace(
        store=SimpleNamespace(path=tmp_path / "society.db"),
        conversations=Archive(),
    )
    await _drain_once(runtime)
    _schedule_wake(runtime)
    assert not hasattr(runtime, "_memory_review_retry")


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_error", [False, True])
async def test_cloud_websocket_streams_history_and_releases_on_disconnect(
    monkeypatch,
    tmp_path,
    upstream_error,
):
    import asyncio
    import json
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from websockets.asyncio.server import serve

    closed = asyncio.Event()
    seen = []
    closes = []

    async def remote(socket):
        seen.append(socket.request.headers.get("Authorization"))
        await socket.send(json.dumps({"type": "snapshot", "events": [{"seq": 17}]}))
        if upstream_error:
            await socket.close(code=1011, reason="upstream failed")
        await socket.wait_closed()
        closed.set()

    async with serve(remote, "127.0.0.1", 0) as upstream:
        port = upstream.sockets[0].getsockname()[1]

        class Host:
            def __init__(self, runtime):
                pass

            @asynccontextmanager
            async def tunnel(self, agent_id):
                yield f"http://127.0.0.1:{port}", "test-connection-token"

        async def runtime(_):
            return object()

        app = FastAPI()
        app.add_middleware(SocietyCloudProxy, data_dir=str(tmp_path))
        monkeypatch.setattr("jarvis.society.cloud_host.CloudHost", Host)
        monkeypatch.setattr(
            "jarvis.society.cloud_host.placement_for", lambda *_: {"state": "active"}
        )
        monkeypatch.setattr("jarvis.ui.web.society_routes._runtime", runtime)

        async def recorded(scope, receive, send):
            async def record(message):
                if message["type"] == "websocket.close":
                    closes.append(message["code"])
                await send(message)

            await app(scope, receive, record)

        def browser():
            with TestClient(recorded) as client:
                with client.websocket_connect(
                    "/api/agent-chat/sessions/society:agent-a/ws?after=12"
                ) as socket:
                    assert socket.receive_json()["events"] == [{"seq": 17}]
                    if upstream_error:
                        with pytest.raises(WebSocketDisconnect) as disconnected:
                            socket.receive_json()
                        assert disconnected.value.code == 1011

        await asyncio.to_thread(browser)
        await asyncio.wait_for(closed.wait(), timeout=3)
    assert seen == ["Bearer test-connection-token"]
    if upstream_error:
        assert closes == [1011]
