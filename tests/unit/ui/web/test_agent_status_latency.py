"""Cold status reads must not serialize CLI startup or duplicate work per window."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from jarvis.ui.web import agent_status


@pytest.mark.asyncio
async def test_subscription_probes_start_together(monkeypatch):
    barrier = threading.Barrier(4, timeout=5)

    def probe(module, service, args):
        barrier.wait()
        return service

    monkeypatch.setattr(agent_status, "_status", probe)
    result = await agent_status.subscription_statuses(None)
    assert result == {
        "claude": "ClaudeAuthService",
        "codex": "CodexAuthService",
        "google": "GoogleCliAuthService",
        "grok": "GrokBuildAuthService",
    }


@pytest.mark.asyncio
async def test_one_broken_probe_preserves_other_provider_results(monkeypatch):
    def probe(module, service, args):
        if service == "ClaudeAuthService":
            raise OSError("CLI unavailable")
        return service

    monkeypatch.setattr(agent_status, "_status", probe)
    result = await agent_status.subscription_statuses(None)
    assert result["claude"] is None
    assert result["codex"] == "CodexAuthService"
    assert result["google"] == "GoogleCliAuthService"
    assert result["grok"] == "GrokBuildAuthService"


@pytest.fixture
def status_endpoint(monkeypatch, tmp_path):
    import jarvis.claude_credentials as credentials
    import jarvis.core.config as config
    from jarvis.core.bus import EventBus
    from jarvis.ui.web.server import WebServer

    monkeypatch.setattr(config, "get_secret", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(config, "get_provider_secret", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        credentials, "freshest_claude_oauth", lambda: SimpleNamespace(status="missing")
    )
    cfg = config.JarvisConfig()
    cfg.memory.data_dir = str(tmp_path)
    app = WebServer(bus=EventBus(), cfg=cfg).app
    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", "") == "/api/jarvis-agent/status"
    )


@pytest.mark.asyncio
async def test_windows_share_pending_status_and_disconnect_does_not_cancel_it(
    monkeypatch, status_endpoint
):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def probes(_binary):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {key: None for key in ("claude", "codex", "google", "grok")}

    monkeypatch.setattr(agent_status, "subscription_statuses", probes)
    first = asyncio.create_task(status_endpoint())
    await asyncio.wait_for(entered.wait(), 5)
    second = asyncio.create_task(status_endpoint())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert calls == 1
    release.set()
    result = await asyncio.wait_for(second, 5)
    assert result["mapping"]
    # A later read is fresh: saving a key or switching a login cannot be hidden
    # by caching the entire status response across completed requests.
    await asyncio.wait_for(status_endpoint(), 5)
    assert calls == 2


@pytest.mark.asyncio
async def test_failed_shared_status_is_retried(monkeypatch, status_endpoint):
    calls = 0

    async def probes(_binary):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary discovery failure")
        return {key: None for key in ("claude", "codex", "google", "grok")}

    monkeypatch.setattr(agent_status, "subscription_statuses", probes)
    with pytest.raises(RuntimeError, match="temporary discovery failure"):
        await status_endpoint()
    assert (await status_endpoint())["mapping"]
    assert calls == 2
