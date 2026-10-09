"""Cold-start preparation stays local, bounded, and owned after cancellation."""

from __future__ import annotations

import asyncio
import importlib
import ssl
import threading
from types import SimpleNamespace

import pytest

from jarvis.plugins.realtime import _live_transport as transport
from tests.fakes.fake_subscription_live_wire import FakeSubscriptionLiveWire


@pytest.fixture
def empty_tls(monkeypatch):
    monkeypatch.setattr(transport, "_tls_entry", None)


async def test_tls_context_is_verified_and_reused_off_loop(empty_tls, monkeypatch):
    owner = threading.get_ident()
    threads = []
    create = ssl.create_default_context

    def build():
        threads.append(threading.get_ident())
        return create()

    monkeypatch.setattr(ssl, "create_default_context", build)
    contexts = await asyncio.gather(*(transport.websocket_options() for _ in range(8)))
    context = contexts[0]["ssl"]
    assert all(item["ssl"] is context for item in contexts)
    assert len(threads) == 1 and threads[0] != owner
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


async def test_tls_cache_refreshes_for_trust_changes_and_age(empty_tls, monkeypatch, tmp_path):
    bundle = tmp_path / "roots.pem"
    bundle.write_text("first", encoding="utf8")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    monkeypatch.setattr(ssl, "create_default_context", object)
    first = (await transport.websocket_options())["ssl"]
    bundle.write_text("replacement roots", encoding="utf8")
    second = (await transport.websocket_options())["ssl"]
    assert second is not first
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    third = (await transport.websocket_options())["ssl"]
    assert third is not second
    monkeypatch.setattr(transport, "_TLS_MAX_AGE_S", 0)
    assert (await transport.websocket_options())["ssl"] is not third


async def test_failed_trust_refresh_does_not_fall_back_to_old_context(empty_tls, monkeypatch):
    await transport.websocket_options()
    monkeypatch.setattr(transport, "_TLS_MAX_AGE_S", 0)

    def reject():
        raise ssl.SSLError("test trust failure")

    monkeypatch.setattr(ssl, "create_default_context", reject)
    with pytest.raises(ssl.SSLError):
        await transport.websocket_options()


@pytest.mark.parametrize("provider_module", ["openai_live", "openai_subscription_live"])
async def test_warmup_has_no_auth_http_or_socket_activity(provider_module, monkeypatch):
    import socket

    import httpx
    from websockets.asyncio import client

    module = importlib.import_module("jarvis.plugins.realtime." + provider_module)
    provider = (module.OpenAILiveProvider if provider_module == "openai_live"
                else module.OpenAISubscriptionLiveProvider)

    def forbidden(*args, **kwargs):
        raise AssertionError("Warmup attempted remote I/O")

    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    monkeypatch.setattr(client, "connect", forbidden)
    # Public Live has an existing DNS warmup, which has no voice/audio traffic.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [])
    await provider.warm_transport(SimpleNamespace())


@pytest.mark.parametrize("capability,module_name", [
    ("client_managed_delegation", "jarvis.live.subscription"),
    ("native_tool_orchestration", "jarvis.live.native"),
    ("continuous_conversation", "jarvis.live.session"),
])
async def test_only_selected_host_adapter_is_imported_in_worker(
    capability, module_name, monkeypatch,
):
    from jarvis.realtime import factory

    owner = threading.get_ident()
    imports = []
    selection_threads = []
    primary = SimpleNamespace(**{capability: True})
    fallback = SimpleNamespace(continuous_conversation=True, eager_warm_as_fallback=False)
    monkeypatch.setattr(factory, "_realtime_is_the_configured_voice_mode", lambda cfg: True)
    def selected(cfg):
        selection_threads.append(threading.get_ident())
        return ["primary", "fallback"]

    monkeypatch.setattr(factory, "_explicit_provider_ids", selected)
    monkeypatch.setattr(factory, "load", lambda group, name, **kw:
                        primary if name == "primary" else fallback)
    monkeypatch.setattr(importlib, "import_module", lambda name:
                        imports.append((name, threading.get_ident())))
    await factory.realtime_warm_selected_transports(SimpleNamespace())
    assert len(imports) == 1 and imports[0][0] == module_name
    assert imports[0][1] != owner
    assert selection_threads and selection_threads[0] != owner


async def test_disabled_voice_does_not_prepare_host_or_transport(monkeypatch):
    from jarvis.realtime import factory

    def forbidden(*args):
        raise AssertionError("Disabled voice was warmed")

    monkeypatch.setattr(factory, "_realtime_is_the_configured_voice_mode", lambda cfg: False)
    monkeypatch.setattr(factory, "_warm_session_imports", forbidden)
    monkeypatch.setattr(factory, "_explicit_provider_ids", forbidden)
    await factory.realtime_warm_selected_transports(SimpleNamespace())


async def test_cancelled_client_construction_closes_late_result():
    building, release = threading.Event(), threading.Event()
    closed = asyncio.Event()
    closes = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closes.append(True)
            closed.set()

    def build():
        building.set()
        assert release.wait(5), "Test did not release constructor"
        return Client()

    async def open_client():
        async with transport.preparing_http_client(build) as prepared:
            await asyncio.shield(prepared)
            pytest.fail("A cancelled start must never send a request")

    task = asyncio.create_task(open_client())
    try:
        assert await asyncio.to_thread(building.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert not closed.is_set()  # Hangup does not wait for the constructor.
        release.set()
        await asyncio.wait_for(closed.wait(), 3)
        assert closes == [True]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_subscription_prepares_client_during_credentials_and_reports_phases():
    wire = FakeSubscriptionLiveWire()
    building, release = threading.Event(), threading.Event()
    provider = wire.provider()
    phases = []

    def build(**kwargs):
        building.set()
        assert release.wait(5), "Credentials were serialized after client construction"
        return wire.client(**kwargs)

    async def credentials(**kwargs):
        assert await asyncio.to_thread(building.wait, 3)
        release.set()
        return await wire.credentials(**kwargs)

    provider._http_client_factory = build
    provider._credentials = credentials
    config = wire.config()
    config.on_startup_phase = phases.append
    try:
        connection = await provider.open_session(config)
        assert phases == ["credentials_ready", "http_client_ready", "session_response",
                          "control_tls_ready", "control_connect_started"]
        context = wire.connects[0][1]["ssl"]
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert wire.auth_refreshes == [False] and len(wire.requests) == 1
        await connection.close()
    finally:
        release.set()


async def test_original_auth_error_survives_parallel_preparation():
    from jarvis.live.subscription_auth import SubscriptionAuthError

    wire = FakeSubscriptionLiveWire()
    provider = wire.provider()

    async def unavailable(**kwargs):
        raise SubscriptionAuthError("Test account signed out")

    provider._credentials = unavailable
    with pytest.raises(SubscriptionAuthError, match="signed out"):
        await provider.open_session(wire.config())
    if transport._cleanup_tasks:
        await asyncio.gather(*tuple(transport._cleanup_tasks))
    assert wire.requests == [] and wire.connects == []


async def test_cancel_during_attach_retry_retires_the_existing_allocation():
    wire = FakeSubscriptionLiveWire()
    provider = wire.provider()
    waiting = asyncio.Event()
    error = RuntimeError("call is still indexing")
    error.response = SimpleNamespace(status_code=404)
    wire.connect_errors.append(error)
    attempts = 0

    async def permit():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            waiting.set()
            await asyncio.Future()

    provider._connection_permit = permit
    task = asyncio.create_task(provider.open_session(wire.config()))
    await asyncio.wait_for(waiting.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(wire.requests) == 1
    assert wire.socket.sent == [{"type": "session.close"}]
    assert wire.socket.closed == 1
    assert all(options["ssl"].verify_mode == ssl.CERT_REQUIRED for _, options in wire.connects)
