"""State-machine regressions over controlled HTTP, not live OAuth evidence."""

import asyncio

import httpx
import pytest
from fastapi import BackgroundTasks

from jarvis.marketplace.auth.base import AuthSession, FlowResult
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore
from jarvis.ui.web import marketplace_routes as routes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,capability",
    [
        (200, "live"),
        (401, "unauthorized"),
        (403, "limited"),
        (429, "rate_limited"),
        (503, "unavailable"),
        (0, "unavailable"),
    ],
)
async def test_google_grant_survives_probe_and_restart(monkeypatch, status, capability):
    from jarvis.plugins.tool import connected_server

    backend = InMemoryBackend()
    store = TokenStore(backend)
    store.save("google_cloud", Tokens(access="old", refresh="old-refresh"))

    class Handler:
        def __init__(self, config):
            self.config = config

        async def start(self, spec):
            return AuthSession(
                flow_id="google-contract",
                plugin_id=spec.id,
                kind="browser_redirect",
            )

        async def await_completion(self, session):
            return FlowResult(
                tokens=Tokens(
                    access="new",
                    refresh="refresh",
                    extra={"client_id": "public"},
                ),
                error=None,
            )

    original = connected_server.ConnectedRestClient

    def upstream(request):
        assert store.load("google_cloud").access == "new"
        assert request.method == "GET"
        if not status:
            raise httpx.ConnectError("test-sensitive-network-detail")
        return httpx.Response(
            status, json=({"projects": []} if status == 200 else {"error": "test-sensitive-body"})
        )

    monkeypatch.setattr(
        connected_server,
        "ConnectedRestClient",
        lambda *a, **kw: original(
            *a,
            **kw,
            transport=httpx.MockTransport(upstream),
        ),
    )
    monkeypatch.setattr(routes, "TokenStore", lambda: TokenStore(backend))
    monkeypatch.setattr(routes, "PkceLoopbackHandler", Handler)
    monkeypatch.setattr(routes, "_refresh_plugin_in_live_registry", lambda _: None)
    monkeypatch.setattr(
        "jarvis.marketplace.connect_helpers.resolve_pkce_client",
        lambda *a: ("public", None),
    )
    pending = []
    create = asyncio.create_task

    def capture(coro, **kw):
        task = create(coro, **kw)
        pending.append(task)
        return task

    monkeypatch.setattr(routes.asyncio, "create_task", capture)
    session = await routes.connect_start("google_cloud", BackgroundTasks())
    await asyncio.gather(*pending)
    result = await routes.connect_poll("google_cloud", session["flow_id"])
    assert result["capability_state"] == capability
    assert result["state"] == ("error" if status == 401 else "connected")
    restarted = TokenStore(backend)
    saved = restarted.load("google_cloud")
    assert saved.access == "new" and saved.refresh == "refresh"
    assert saved.needs_reauth == (status == 401)
    assert "test-sensitive" not in saved.to_json()
    assert routes._plugin_status_meta("google_cloud", restarted).capability_state == capability


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,capability", [(401, "unauthorized"), (403, "limited"), (200, "live")]
)
async def test_refresh_rechecks_capability_after_persisting_rotation(
    monkeypatch, status, capability
):
    from jarvis.marketplace.refresh_scheduler import refresh_plugin_token
    from jarvis.plugins.tool import connected_server

    store = TokenStore(InMemoryBackend())
    store.save(
        "google_cloud",
        Tokens(
            access="before",
            refresh="before-refresh",
            extra={"capability_state": "unauthorized"},
        ),
    )

    class Handler:
        async def refresh(self, current):
            return Tokens(access="after", refresh="rotated")

    original = connected_server.ConnectedRestClient

    def upstream(request):
        assert store.load("google_cloud").refresh == "rotated"
        return httpx.Response(status, json={"projects": []})

    monkeypatch.setattr(
        connected_server,
        "ConnectedRestClient",
        lambda *a, **kw: original(
            *a,
            **kw,
            transport=httpx.MockTransport(upstream),
        ),
    )
    result = await refresh_plugin_token("google_cloud", store, lambda _: Handler(), force=True)
    saved = store.load("google_cloud")
    assert saved.refresh == "rotated"
    assert saved.extra["capability_state"] == capability
    assert saved.needs_reauth == (status == 401)
    assert result.usable == (status != 401)


@pytest.mark.asyncio
async def test_explicit_ui_probe_preserves_grant_and_returns_no_credentials(monkeypatch):
    from jarvis.plugins.tool import connected_server

    store = TokenStore(InMemoryBackend())
    store.save("google_cloud", Tokens(access="test-credential", refresh="test-refresh"))
    original = connected_server.ConnectedRestClient
    reads = []

    def upstream(request):
        reads.append(request.method)
        return httpx.Response(403, json={"error": "test-private-body"})

    monkeypatch.setattr(routes, "TokenStore", lambda: store)
    monkeypatch.setattr(
        connected_server,
        "ConnectedRestClient",
        lambda *a, **kw: original(
            *a,
            **kw,
            transport=httpx.MockTransport(upstream),
        ),
    )
    result = await routes.verify_plugin_access("google_cloud")
    assert result == {"status": "connected", "capability_state": "limited", "resource_read": True}
    assert reads == ["GET"]
    assert store.load("google_cloud").refresh == "test-refresh"
    assert "test-credential" not in str(result) and "test-private-body" not in str(result)
